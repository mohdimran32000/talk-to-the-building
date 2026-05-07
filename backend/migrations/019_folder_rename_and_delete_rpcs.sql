-- Phase 3 / Migration 019: Folder rename + delete-if-empty + create-if-not-exists RPCs.
-- Bundles the three cross-table-transactional RPCs Phase 3's folders router needs.
-- Colocated here (vs. separate migration files) because they share PL/pgSQL idiom
-- and review surface; mirrors Phase 1's bundling of the full RLS catalog into 015.
--
-- DESIGN NOTES:
-- 1. rename_folder_prefix wraps two UPDATEs (documents + folders) in a single
--    PL/pgSQL block — implicitly transactional. PostgREST executes each
--    .execute() in its own transaction; an RPC is the only cross-table-atomic
--    unit available from supabase-py (FOLDER-03 + Pitfall 5 mid-rename rollback).
-- 2. delete_folder_if_empty uses FOR UPDATE on the folders row to serialize
--    concurrent rename / delete attempts on the SAME folders row (FOLDER-04 +
--    Pitfall 5). Standard MVCC: row-level write lock blocks concurrent
--    UPDATE/DELETE on this row but not SELECT.
--    HI-02 (Phase 3 review): the FOR UPDATE lock does NOT block concurrent
--    INSERTs into `documents` at folder_path = v_path. The classic interleaving
--    is: (T1) lock folders row, count documents (sees 0); (T2) upload INSERTs a
--    doc at v_path, commits; (T1) DELETE folders row. Result: documents row
--    persists at v_path with no folders row — under Strategy B this is a
--    valid INFERRED folder, but the user perceives "delete didn't work" because
--    /v_path reappears in the next list. We ACCEPT this behavior for now (a
--    heavier `LOCK TABLE documents IN SHARE ROW EXCLUSIVE MODE` would serialize
--    ALL uploads during a delete, which is too coarse for the production
--    workload). Strategy-B-aware UIs MUST refresh after delete.
-- 3. create_folder_if_not_exists wraps INSERT ... ON CONFLICT DO NOTHING with
--    proper coupling validation (Pitfall 10 + STATE.md Strategy B). The
--    expression list in ON CONFLICT MUST exactly match Migration 013's
--    unique expression index (scope, COALESCE(user_id, '00..0'), path).
-- 4. SECURITY INVOKER (the default) — RLS policies on documents / folders
--    apply to function execution. Router-level Depends(get_admin_user) is the
--    first line of defense for global-scope writes; RLS is the second.
-- 5. RAISE EXCEPTION ... USING ERRCODE = 'check_violation' mirrors the
--    forbid_scope_mutation pattern from migration 015:48-51.
-- 6. GRANT EXECUTE ... TO authenticated mirrors the is_admin() grant pattern
--    from migration 015:35.
-- 7. CREATE OR REPLACE FUNCTION is idempotent — re-running this migration
--    is a no-op (no errors).

-- ── 1. rename_folder_prefix (FOLDER-03 — atomic cross-table prefix update) ──
-- Updates documents.folder_path AND folders.path in a single transaction.
-- The descend predicate uses '/' separator: LIKE p_old_prefix || '/%' avoids
-- /projects matching /projectsX (sibling-prefix bug). The empty-suffix case
-- (folder_path = p_old_prefix exactly) is handled by Postgres substring
-- semantics: substring(s FROM len+1) on a len-char string returns '', so
-- p_new_prefix || '' = p_new_prefix. Correct in both branches without IF.

CREATE OR REPLACE FUNCTION public.rename_folder_prefix(
  p_old_prefix TEXT,
  p_new_prefix TEXT,
  p_scope      TEXT,
  p_user_id    UUID DEFAULT NULL
)
RETURNS TABLE (documents_updated INT, folders_updated INT)
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
DECLARE
  v_doc_count        INT;
  v_folder_count     INT;
  v_old_prefix_like  TEXT;  -- HI-03: LIKE-pattern-escaped form of p_old_prefix
BEGIN
  -- Defense in depth: validate canonical form (matches CHECK from migrations 012/013).
  IF p_old_prefix !~ '^/$|^/[^/]+(/[^/]+)*$' THEN
    RAISE EXCEPTION 'old_prefix not canonical: %', p_old_prefix
      USING ERRCODE = 'check_violation';
  END IF;
  IF p_new_prefix !~ '^/$|^/[^/]+(/[^/]+)*$' THEN
    RAISE EXCEPTION 'new_prefix not canonical: %', p_new_prefix
      USING ERRCODE = 'check_violation';
  END IF;
  IF p_old_prefix = '/' THEN
    RAISE EXCEPTION 'cannot rename root path /'
      USING ERRCODE = 'check_violation';
  END IF;

  -- HI-03: Migration 012/013's canonical-form regex `^/[^/]+(/[^/]+)*$` allows
  -- `_` and `%` in folder segments. Without escaping, `LIKE '/foo_bar/%'`
  -- treats `_` as a single-char wildcard and would over-match `/fooXbar/...`.
  -- Escape backslash FIRST (so we do not double-escape the backslashes we
  -- insert next), then `%` and `_`. We pass `ESCAPE '\'` explicitly even
  -- though `\` is the Postgres default — clearer intent and guards against
  -- future standard_conforming_strings changes.
  v_old_prefix_like := replace(replace(replace(p_old_prefix,
                          '\', '\\'),
                          '%', '\%'),
                          '_', '\_');

  UPDATE public.documents
     SET folder_path = p_new_prefix || substring(folder_path FROM length(p_old_prefix) + 1)
   WHERE scope = p_scope
     AND (p_user_id IS NULL OR user_id = p_user_id)
     AND (folder_path = p_old_prefix
          OR folder_path LIKE v_old_prefix_like || '/%' ESCAPE '\');
  GET DIAGNOSTICS v_doc_count = ROW_COUNT;

  UPDATE public.folders
     SET path = p_new_prefix || substring(path FROM length(p_old_prefix) + 1)
   WHERE scope = p_scope
     AND (p_user_id IS NULL OR user_id = p_user_id)
     AND (path = p_old_prefix
          OR path LIKE v_old_prefix_like || '/%' ESCAPE '\');
  GET DIAGNOSTICS v_folder_count = ROW_COUNT;

  RETURN QUERY SELECT v_doc_count, v_folder_count;
END;
$$;

GRANT EXECUTE ON FUNCTION public.rename_folder_prefix(TEXT, TEXT, TEXT, UUID) TO authenticated;

-- ── 2. delete_folder_if_empty (FOLDER-04 — race-free empty-check + delete) ──
-- Single PL/pgSQL block: FOR UPDATE row lock + count-check + DELETE. Eliminates
-- the TOCTOU race that an app-side SELECT-then-DELETE would have. Returns
-- (deleted, document_count, subfolder_count); router (Plan 04) maps deleted=FALSE
-- to a 409 with a structured body (FOLDER_NOT_EMPTY).

CREATE OR REPLACE FUNCTION public.delete_folder_if_empty(
  p_folder_id UUID
)
RETURNS TABLE (deleted BOOLEAN, document_count INT, subfolder_count INT)
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
DECLARE
  v_path            TEXT;
  v_scope           TEXT;
  v_user_id         UUID;
  v_doc_count       INT;
  v_subfolder_count INT;
  v_path_like       TEXT;  -- HI-03: LIKE-pattern-escaped form of v_path
BEGIN
  -- Lock the folders row to block concurrent renames during this transaction.
  SELECT path, scope, user_id INTO v_path, v_scope, v_user_id
    FROM public.folders
   WHERE id = p_folder_id
   FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'folder not found: %', p_folder_id
      USING ERRCODE = 'no_data_found';
  END IF;

  -- HI-03: escape `\`, `%`, `_` in v_path so a folder name containing those
  -- literals (allowed by Migration 012/013's canonical-form CHECK) does not
  -- become wildcards in the LIKE predicate. Without this, /foo_bar would be
  -- incorrectly classified as non-empty if /fooXbar/baz exists. Escape `\`
  -- first so we do not double-escape the backslashes we insert next.
  v_path_like := replace(replace(replace(v_path,
                    '\', '\\'),
                    '%', '\%'),
                    '_', '\_');

  -- Count documents at-or-under this path (RLS applies via SECURITY INVOKER).
  SELECT COUNT(*) INTO v_doc_count
    FROM public.documents
   WHERE scope = v_scope
     AND (v_user_id IS NULL OR user_id = v_user_id)
     AND (folder_path = v_path OR folder_path LIKE v_path_like || '/%' ESCAPE '\');

  -- Count strict-descendant folders rows.
  SELECT COUNT(*) INTO v_subfolder_count
    FROM public.folders
   WHERE scope = v_scope
     AND (v_user_id IS NULL OR user_id = v_user_id)
     AND path LIKE v_path_like || '/%' ESCAPE '\';

  IF v_doc_count > 0 OR v_subfolder_count > 0 THEN
    -- Return without deleting; router maps to {error: 'FOLDER_NOT_EMPTY', ...}
    RETURN QUERY SELECT FALSE, v_doc_count, v_subfolder_count;
    RETURN;
  END IF;

  DELETE FROM public.folders WHERE id = p_folder_id;
  RETURN QUERY SELECT TRUE, 0, 0;
END;
$$;

GRANT EXECUTE ON FUNCTION public.delete_folder_if_empty(UUID) TO authenticated;

-- ── 3. create_folder_if_not_exists (Pitfall 10 — atomic upsert for explicit POST /api/folders) ──
-- Strategy B (locked by STATE.md line 74): folders is a sparse, explicit-empty-only
-- side table. Upload path NEVER writes to folders; only POST /api/folders does.
-- The unique expression index from Migration 013:38-43 makes concurrent calls safe.
-- The expression list in ON CONFLICT MUST exactly match the unique index expression
-- (scope, COALESCE(user_id, '00..0'::uuid), path) or Postgres raises:
--   ERROR: there is no unique or exclusion constraint matching the ON CONFLICT specification

CREATE OR REPLACE FUNCTION public.create_folder_if_not_exists(
  p_scope   TEXT,
  p_user_id UUID,
  p_path    TEXT
)
RETURNS TABLE (id UUID, created BOOLEAN)
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
DECLARE
  v_id UUID;
BEGIN
  -- Defense in depth: validate canonical form (matches CHECK from migration 013:24-26).
  IF p_path !~ '^/$|^/[^/]+(/[^/]+)*$' THEN
    RAISE EXCEPTION 'path not canonical: %', p_path
      USING ERRCODE = 'check_violation';
  END IF;

  -- Coupling assertion (mirrors the table CHECK from migration 013:17-20).
  IF (p_scope = 'user' AND p_user_id IS NULL)
     OR (p_scope = 'global' AND p_user_id IS NOT NULL) THEN
    RAISE EXCEPTION 'scope/user_id coupling violation: scope=%, user_id=%',
      p_scope, p_user_id
      USING ERRCODE = 'check_violation';
  END IF;

  -- Atomic upsert. ON CONFLICT expression MUST match Migration 013:38-43 verbatim.
  INSERT INTO public.folders (scope, user_id, path)
       VALUES (p_scope, p_user_id, p_path)
  ON CONFLICT (scope, COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid), path) DO NOTHING
  RETURNING folders.id INTO v_id;

  IF v_id IS NULL THEN
    -- Existed already; look it up to return the canonical id.
    SELECT f.id INTO v_id
      FROM public.folders f
     WHERE f.scope = p_scope
       AND COALESCE(f.user_id, '00000000-0000-0000-0000-000000000000'::uuid)
           = COALESCE(p_user_id, '00000000-0000-0000-0000-000000000000'::uuid)
       AND f.path = p_path;
    RETURN QUERY SELECT v_id, FALSE;
  ELSE
    RETURN QUERY SELECT v_id, TRUE;
  END IF;
END;
$$;

GRANT EXECUTE ON FUNCTION public.create_folder_if_not_exists(TEXT, UUID, TEXT) TO authenticated;
