---
phase: 03
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - backend/migrations/019_folder_rename_and_delete_rpcs.sql
  - backend/app/models/schemas.py
autonomous: false
requirements:
  - FOLDER-03
  - FOLDER-04
must_haves:
  truths:
    - "Migration 019 file backend/migrations/019_folder_rename_and_delete_rpcs.sql exists with three CREATE OR REPLACE FUNCTION statements: rename_folder_prefix, delete_folder_if_empty, create_folder_if_not_exists"
    - "Migration 019 is applied to the live Supabase Postgres database via run_migrations.py (exit code 0; stdout contains 'RUN  019_folder_rename_and_delete_rpcs.sql ... OK')"
    - "All three Phase 3 RPCs exist in pg_proc after the apply (rename_folder_prefix, delete_folder_if_empty, create_folder_if_not_exists)"
    - "rename_folder_prefix RPC raises ERRCODE='check_violation' on non-canonical prefixes AND on attempts to rename root '/' (defense in depth alongside Phase 1 CHECK constraints from migrations 012/013)"
    - "delete_folder_if_empty RPC uses SELECT ... FOR UPDATE row lock to eliminate TOCTOU race between count-check and DELETE (Pitfall 5 mitigation)"
    - "create_folder_if_not_exists RPC uses INSERT ... ON CONFLICT (scope, COALESCE(user_id,'00..0'::uuid), path) DO NOTHING — exactly matching Migration 013's unique expression index columns (Pitfall 10 mitigation for explicit POST /api/folders calls)"
    - "All three RPCs use SECURITY INVOKER (RLS still applies per Phase 1 Migration 015) AND GRANT EXECUTE TO authenticated"
    - "backend/app/models/schemas.py contains four new Pydantic v2 models: FolderResponse, FolderCreate, FolderPatch, FilePatch — paste-ready for Plans 04 and 05 to import"
    - "schemas.py DocumentResponse.user_id is changed from `str` to `Optional[str] = None` (required because scope='global' rows have user_id IS NULL per Migration 012 coupling CHECK; without this, FastAPI response serialization will raise ValidationError for global docs)"
    - "schemas.py DocumentResponse gains `folder_path: str = '/'` and `scope: str = 'user'` fields with safe defaults (preserves existing-row response shape)"
    - "FilePatch model deliberately OMITS a scope field (Pitfall B mitigation; comment in code explains the immutability contract — Migration 015 forbid_scope_mutation trigger is bedrock)"
  artifacts:
    - path: "backend/migrations/019_folder_rename_and_delete_rpcs.sql"
      provides: "rename_folder_prefix + delete_folder_if_empty + create_folder_if_not_exists PL/pgSQL functions, transactional cross-table updates, race-free empty-check, atomic upsert"
      contains: "CREATE OR REPLACE FUNCTION public.rename_folder_prefix"
      contains_2: "CREATE OR REPLACE FUNCTION public.delete_folder_if_empty"
      contains_3: "CREATE OR REPLACE FUNCTION public.create_folder_if_not_exists"
      contains_4: "GET DIAGNOSTICS"
      contains_5: "FOR UPDATE"
      contains_6: "ON CONFLICT"
      contains_7: "SECURITY INVOKER"
      contains_8: "GRANT EXECUTE"
      min_lines: 120
    - path: "backend/app/models/schemas.py"
      provides: "Pydantic v2 request/response models for Phase 3 routers"
      contains: "class FolderResponse"
      contains_2: "class FolderCreate"
      contains_3: "class FolderPatch"
      contains_4: "class FilePatch"
      contains_5: "folder_path: str ="
  key_links:
    - from: "backend/migrations/019_folder_rename_and_delete_rpcs.sql"
      to: "backend/app/services/folder_service.py (Plan 02)"
      via: "supabase_client.rpc('rename_folder_prefix', {...}) and .rpc('delete_folder_if_empty', {...}) and .rpc('create_folder_if_not_exists', {...})"
      pattern: "rpc\\(\"rename_folder_prefix\""
    - from: "schemas.py FolderResponse / FolderCreate / FolderPatch"
      to: "backend/app/routers/folders.py (Plan 04)"
      via: "from app.models.schemas import FolderResponse, FolderCreate, FolderPatch"
      pattern: "FolderResponse"
    - from: "schemas.py FilePatch"
      to: "backend/app/routers/files.py (Plan 05) — new PATCH endpoint"
      via: "from app.models.schemas import FilePatch"
      pattern: "FilePatch"
---

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Developer environment (DATABASE_URL) -> Supabase Postgres | Privileged direct-connection string runs DDL; one-time push of Migration 019 |
| run_migrations.py -> live database | Migration 019 runs in its own transaction; rollback on failure stops the run, leaves DB at last-committed state |
| FastAPI authenticated user -> RPC invocation | Phase 1 Migration 015 RLS policies apply via SECURITY INVOKER; admin-gate enforced at the Plan 04 router layer |
| Unique-index expression target -> ON CONFLICT specification | The COALESCE expression must EXACTLY match Migration 013's unique index expression — mismatch raises `there is no unique or exclusion constraint matching the ON CONFLICT specification` |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-3-01-Path-Traversal | Tampering | rename_folder_prefix RPC `p_old_prefix`/`p_new_prefix` args | mitigate | RPC body validates canonical form via `IF p_old_prefix !~ '^/$|^/[^/]+(/[^/]+)*$' THEN RAISE EXCEPTION ... USING ERRCODE = 'check_violation';` — matches Phase 1 Migration 015's forbid_scope_mutation pattern. Plan 02's `rename_folder()` ALSO calls `normalize_path()` first (belt). DB CHECK constraints on `documents.folder_path` (Migration 012) and `folders.path` (Migration 013) are the third layer. |
| T-3-01-RootRename | Tampering / Data Integrity | rename_folder_prefix called with `p_old_prefix='/'` | mitigate | RPC body raises EXCEPTION on `p_old_prefix = '/'` — root rename is structurally forbidden (renaming root would mass-update every row in scope, semantically nonsense). Plan 02's service layer ALSO raises ValueError before invoking the RPC. |
| T-3-01-TOCTOU | Tampering / Race condition | delete_folder_if_empty (concurrent INSERT during empty-check) | mitigate | RPC uses `SELECT ... FOR UPDATE` row-level write lock on the folders row. Standard MVCC: `FOR UPDATE` blocks concurrent UPDATE/DELETE of the same row but not SELECT (assumption A4 in RESEARCH.md). The count-check + DELETE happen in the same PL/pgSQL block (implicitly transactional). A concurrent INSERT of `documents` at the path would have to acquire its own row lock to commit — irrelevant here because the empty-check counts already-committed rows. |
| T-3-01-RaceFolders | Tampering / Race condition | create_folder_if_not_exists called concurrently with same path | mitigate | RPC uses `INSERT ... ON CONFLICT (scope, COALESCE(user_id,'00..0'::uuid), path) DO NOTHING` — Postgres-native upsert. Migration 013's unique index makes the second INSERT a no-op; the function returns `(existing_id, FALSE)` for the loser. Pitfall 10 (concurrent upload race) — locked by Strategy B (uploads NEVER write folders rows; only explicit POST /api/folders does). Test in Plan 06 verifies. |
| T-3-01-RLSBypass | Privilege Escalation | RPC with elevated privileges | mitigate | All three RPCs declared `SECURITY INVOKER` (the default; explicit for documentation). Phase 1 Migration 015 RLS policies apply normally — a non-admin trying to rename a global folder hits the RLS gate (UPDATE policy gate fails). Defense in depth via Plan 04's router-level `Depends(get_admin_user)` (clean 403) before the RPC is ever invoked. |
| T-3-01-CouplingViolation | Tampering | create_folder_if_not_exists with mismatched scope/user_id | mitigate | RPC body raises EXCEPTION on `(scope='user' AND user_id IS NULL) OR (scope='global' AND user_id IS NOT NULL)` — mirrors the table CHECK from Migration 013:17-20. Defense in depth at the function level. |
| T-3-01-ScopeFieldSmuggling | Privilege Escalation | FilePatch body with smuggled `scope` field | mitigate | Pydantic v2 ignores unknown fields by default (model_config not set otherwise). FilePatch deliberately omits `scope`. Migration 015's `forbid_scope_mutation` trigger is the bedrock — even if a smuggled field somehow reached the UPDATE, it raises check_violation. Three-layer defense (Pydantic → Plan 05 router → DB trigger). |
| T-3-01-ApplyOps | Operational | Migration application failure | mitigate | Migration 019 uses `CREATE OR REPLACE FUNCTION` (idempotent); `GRANT EXECUTE ... TO authenticated` is idempotent (`GRANT` re-runs are no-op). Re-running run_migrations.py is safe. Failure mode: run_migrations.py rolls back the failing migration's transaction, exit code 2, prints `FAIL\n  <ExceptionType>: <message>`. Operator reads the error, fixes the SQL file, re-runs (idempotent). Existing migrations 001-018 re-apply as no-ops. |
</threat_model>

<objective>
Land the Phase 3 SQL bedrock and the Pydantic-model interface contracts that Plans 02-06 consume. This is THE blocking foundation for the whole phase: without Migration 019's three RPCs applied, Plans 02 (folder_service.rename_folder/delete_folder/create_folder) have nothing to call; without the new schemas, Plans 04 and 05 cannot import the request/response models the routers need.

Two artifacts ship together because the schemas additions are mechanical (~30 LOC) and gate Plan 04 the same way Migration 019 gates Plan 02 — bundling them keeps Wave 1 small and avoids a one-task plan. The migration is applied via the existing `run_migrations.py` runner; the schemas additions are an in-place edit of `backend/app/models/schemas.py`.

Output: (1) `backend/migrations/019_folder_rename_and_delete_rpcs.sql` with three RPCs, applied to the live database, structurally verified via psql/psycopg2; (2) `backend/app/models/schemas.py` extended with FolderResponse, FolderCreate, FolderPatch, FilePatch + DocumentResponse fields for folder_path/scope/nullable user_id.

This plan is `autonomous: false` because Task 2 includes a `checkpoint:human-action` gate that requires the operator's `DATABASE_URL` env var to apply Migration 019 (mirrors Phase 1 / Plan 07 + Phase 2 / Plan 04 pattern).
</objective>

<execution_context>
@.claude/get-shit-done/workflows/execute-plan.md
@.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/REQUIREMENTS.md
@CLAUDE.md

@.planning/phases/03-folder-service-routers-dedup-extension/03-RESEARCH.md
@.planning/phases/03-folder-service-routers-dedup-extension/03-PATTERNS.md
@.planning/phases/03-folder-service-routers-dedup-extension/03-VALIDATION.md
@.planning/research/PITFALLS.md
@.planning/codebase/ARCHITECTURE.md
@.planning/codebase/CONVENTIONS.md

@backend/scripts/run_migrations.py
@backend/migrations/008_hybrid_search.sql
@backend/migrations/012_folder_path_and_scope.sql
@backend/migrations/013_folders_table.sql
@backend/migrations/015_two_scope_rls.sql
@backend/migrations/018_storage_rls.sql
@backend/app/models/schemas.py

<interfaces>
<!-- Contracts this plan ESTABLISHES — Plans 02-06 consume these. -->

Migration 019 RPC contracts (locked here):

  rpc('rename_folder_prefix', {
    p_old_prefix: TEXT,    -- canonical, not '/'
    p_new_prefix: TEXT,    -- canonical
    p_scope:      TEXT,    -- 'user' | 'global'
    p_user_id:    UUID | None,  -- None for 'global'
  }) -> TABLE(documents_updated INT, folders_updated INT)

  rpc('delete_folder_if_empty', {
    p_folder_id:  UUID,
  }) -> TABLE(deleted BOOLEAN, document_count INT, subfolder_count INT)
       -- deleted=TRUE on success; deleted=FALSE means non-empty (router maps to 409).
       -- Raises 'no_data_found' SQLSTATE if folder doesn't exist (router maps to 404).

  rpc('create_folder_if_not_exists', {
    p_scope:    TEXT,    -- 'user' | 'global'
    p_user_id:  UUID | None,  -- None for 'global'; coupling-checked
    p_path:     TEXT,    -- canonical
  }) -> TABLE(id UUID, created BOOLEAN)
       -- created=TRUE if INSERT happened; created=FALSE if path already existed.
       -- Raises 'check_violation' on coupling violation or non-canonical path.

Pydantic v2 model contracts (locked here):

  FolderResponse:
    id: str
    scope: str                 # 'user' | 'global'
    user_id: Optional[str]     # nullable for scope='global'
    path: str
    created_at: datetime

  FolderCreate (request body for POST /api/folders):
    path: str
    scope: str = 'user'        # 'user' | 'global'

  FolderPatch (request body for PATCH /api/folders/{id}):
    new_path: str

  FilePatch (request body for PATCH /api/files/{id}):
    file_name: Optional[str] = None
    folder_path: Optional[str] = None
    # NO scope field — IMMUTABLE per Migration 015 forbid_scope_mutation trigger.

  DocumentResponse (UPDATED — backwards-compatible additions):
    id: str
    user_id: Optional[str] = None      # CHANGED from `str` — global rows have user_id IS NULL
    file_name: str
    file_size: int
    mime_type: str
    status: str
    error_message: Optional[str] = None
    content_hash: Optional[str] = None
    metadata: Optional[dict] = None
    folder_path: str = '/'             # NEW (FOLDER-07)
    scope: str = 'user'                # NEW (FOLDER-07)
    action: Optional[str] = None
    created_at: datetime
    updated_at: datetime

Required environment variable for Task 2:
  DATABASE_URL — Supabase project's Direct connection string (port 5432, NOT pooler)
  Source: Supabase Dashboard -> Project Settings -> Database -> Connection string -> URI -> Direct connection
</interfaces>
</context>

<tasks>

<task id="3-01-01" type="auto">
  <name>Task 1: Author Migration 019 — three Phase 3 RPCs (rename, delete-if-empty, create-if-not-exists)</name>
  <files>backend/migrations/019_folder_rename_and_delete_rpcs.sql</files>
  <read_first>
    - backend/migrations/008_hybrid_search.sql (PRIMARY analog — PL/pgSQL function with multi-statement body returning TABLE; LANGUAGE plpgsql; RETURN QUERY SELECT idiom)
    - backend/migrations/015_two_scope_rls.sql (SECONDARY analog — header comment block style with `-- ── 1.` numbered sections; RAISE EXCEPTION ... USING ERRCODE='check_violation' shape at L48-51; CREATE OR REPLACE FUNCTION + GRANT EXECUTE pattern)
    - backend/migrations/013_folders_table.sql (TERTIARY analog — the unique expression index `(scope, COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid), path)` is the EXACT target of `create_folder_if_not_exists`'s ON CONFLICT specification; the table CHECK at L17-20 is the coupling rule the RPC mirrors)
    - backend/migrations/012_folder_path_and_scope.sql L40-57 (the canonical-path regex `^/$|^/[^/]+(/[^/]+)*$` and the scope-aware unique index — the regex is what the RPC's defense-in-depth IF check uses)
    - .planning/phases/03-folder-service-routers-dedup-extension/03-RESEARCH.md §Folder Rename RPC Design + §Folder Delete Implementation + §Concurrent-upload-no-orphan Strategy (paste-ready RPC bodies; signatures locked here)
    - .planning/phases/03-folder-service-routers-dedup-extension/03-PATTERNS.md §`backend/migrations/019_folder_rename_and_delete_rpcs.sql` (paste-ready snippets including the `-- ── N.` divider style)
    - CLAUDE.md (Python backend uses venv; Tests must NEVER delete all user data — note: this is DDL, not test code, but the restraint informs why the RPC ONLY deletes when explicitly safe)
  </read_first>
  <action>
    Create `backend/migrations/019_folder_rename_and_delete_rpcs.sql` with the EXACT content below. This is a paste-ready file — do not deviate from the function bodies, signatures, RETURNS shapes, or naming. The migration is applied by Task 2 of this plan; Plans 02 and 04 invoke these RPCs by their exact names and parameter shapes.

    Header comment block (mirrors `015_two_scope_rls.sql:1-22` style — Phase 1's bundling pattern):

    ```sql
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
    -- 2. delete_folder_if_empty uses FOR UPDATE on the folders row to eliminate
    --    the TOCTOU race between count-check and delete (FOLDER-04 + Pitfall 5).
    --    Standard MVCC: row-level write lock blocks concurrent UPDATE/DELETE but
    --    not SELECT (see 03-RESEARCH.md §Folder Delete Implementation, A4).
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
    ```

    Then three numbered sections, one RPC per section.

    ### Section 1: rename_folder_prefix (FOLDER-03)

    ```sql
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
      v_doc_count    INT;
      v_folder_count INT;
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

      UPDATE public.documents
         SET folder_path = p_new_prefix || substring(folder_path FROM length(p_old_prefix) + 1)
       WHERE scope = p_scope
         AND (p_user_id IS NULL OR user_id = p_user_id)
         AND (folder_path = p_old_prefix
              OR folder_path LIKE p_old_prefix || '/%');
      GET DIAGNOSTICS v_doc_count = ROW_COUNT;

      UPDATE public.folders
         SET path = p_new_prefix || substring(path FROM length(p_old_prefix) + 1)
       WHERE scope = p_scope
         AND (p_user_id IS NULL OR user_id = p_user_id)
         AND (path = p_old_prefix
              OR path LIKE p_old_prefix || '/%');
      GET DIAGNOSTICS v_folder_count = ROW_COUNT;

      RETURN QUERY SELECT v_doc_count, v_folder_count;
    END;
    $$;

    GRANT EXECUTE ON FUNCTION public.rename_folder_prefix(TEXT, TEXT, TEXT, UUID) TO authenticated;
    ```

    ### Section 2: delete_folder_if_empty (FOLDER-04 + Pitfall 5)

    ```sql
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

      -- Count documents at-or-under this path (RLS applies via SECURITY INVOKER).
      SELECT COUNT(*) INTO v_doc_count
        FROM public.documents
       WHERE scope = v_scope
         AND (v_user_id IS NULL OR user_id = v_user_id)
         AND (folder_path = v_path OR folder_path LIKE v_path || '/%');

      -- Count strict-descendant folders rows.
      SELECT COUNT(*) INTO v_subfolder_count
        FROM public.folders
       WHERE scope = v_scope
         AND (v_user_id IS NULL OR user_id = v_user_id)
         AND path LIKE v_path || '/%';

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
    ```

    ### Section 3: create_folder_if_not_exists (Pitfall 10 + Strategy B)

    ```sql
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
    ```

    Critical DON'Ts:
    - DO NOT use `SECURITY DEFINER` — RLS must apply (defense in depth alongside Plan 04 admin gate).
    - DO NOT change the parameter names (`p_old_prefix`, `p_new_prefix`, `p_scope`, `p_user_id`, `p_folder_id`, `p_path`) — Plan 02 calls these by name via the supabase-py `.rpc(name, {...})` shape.
    - DO NOT change the RETURNS TABLE column names (`documents_updated`, `folders_updated`, `deleted`, `document_count`, `subfolder_count`, `id`, `created`) — Plan 02 reads these via `result.data[0]['<column>']`.
    - DO NOT use `ON CONFLICT` with a column list (`(scope, user_id, path)`) — the unique constraint is an EXPRESSION index, not a column list, and Postgres raises `there is no unique or exclusion constraint matching the ON CONFLICT specification` if you try.
    - DO NOT add a `DELETE FROM` statement that bulk-deletes (CLAUDE.md mandatory rule). The DELETE in `delete_folder_if_empty` is per-id and gated by the empty-check.
    - DO NOT use `CONCURRENTLY` on any operation (the migration runner uses transactions; CONCURRENTLY is forbidden inside transactions — same convention as Phase 1 / Plan 06).
    - DO NOT split into three separate migration files — colocated by design (mirrors Migration 015's catalog bundling).
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -c "import pathlib; src = pathlib.Path('migrations/019_folder_rename_and_delete_rpcs.sql').read_text(encoding='utf-8'); body = '\n'.join(line for line in src.splitlines() if not line.lstrip().startswith('--')); assert 'CREATE OR REPLACE FUNCTION public.rename_folder_prefix' in src, 'rename_folder_prefix missing'; assert 'CREATE OR REPLACE FUNCTION public.delete_folder_if_empty' in src, 'delete_folder_if_empty missing'; assert 'CREATE OR REPLACE FUNCTION public.create_folder_if_not_exists' in src, 'create_folder_if_not_exists missing'; assert body.count('GRANT EXECUTE ON FUNCTION') == 3, f'expected 3 GRANT EXECUTE, got {body.count(chr(71)+chr(82)+chr(65)+chr(78)+chr(84)+chr(32)+chr(69)+chr(88)+chr(69)+chr(67)+chr(85)+chr(84)+chr(69)+chr(32)+chr(79)+chr(78)+chr(32)+chr(70)+chr(85)+chr(78)+chr(67)+chr(84)+chr(73)+chr(79)+chr(78))}'; assert 'SECURITY INVOKER' in body, 'SECURITY INVOKER missing (RLS must apply)'; assert 'SECURITY DEFINER' not in body, 'SECURITY DEFINER forbidden (RLS bypass risk)'; assert 'GET DIAGNOSTICS' in body, 'GET DIAGNOSTICS ROW_COUNT idiom missing'; assert 'FOR UPDATE' in body, 'FOR UPDATE row lock missing (TOCTOU mitigation)'; assert 'ON CONFLICT' in body, 'ON CONFLICT clause missing'; assert 'COALESCE(user_id' in body, 'COALESCE expression missing in ON CONFLICT'; assert 'check_violation' in body, 'check_violation ERRCODE missing'; assert 'no_data_found' in body, 'no_data_found ERRCODE missing'; assert 'CONCURRENTLY' not in body.upper(), 'CONCURRENTLY forbidden in transactional migrations'; assert 'LANGUAGE plpgsql' in body, 'LANGUAGE plpgsql missing'; assert body.count('CREATE OR REPLACE FUNCTION') == 3, f'expected 3 functions'; print(f'OK: 019_folder_rename_and_delete_rpcs.sql structurally valid; {len(src.splitlines())} lines')"</automated>
  </verify>
  <acceptance_criteria>
    - File `backend/migrations/019_folder_rename_and_delete_rpcs.sql` exists.
    - File starts with the header comment block beginning `-- Phase 3 / Migration 019:`.
    - File contains exactly 3 `CREATE OR REPLACE FUNCTION` statements.
    - File contains `CREATE OR REPLACE FUNCTION public.rename_folder_prefix(` (exact text).
    - File contains `CREATE OR REPLACE FUNCTION public.delete_folder_if_empty(` (exact text).
    - File contains `CREATE OR REPLACE FUNCTION public.create_folder_if_not_exists(` (exact text).
    - File contains exactly 3 `GRANT EXECUTE ON FUNCTION ... TO authenticated;` statements (one per function).
    - File contains `SECURITY INVOKER` (3 times — once per function).
    - File contains NO `SECURITY DEFINER` (would bypass RLS).
    - File contains `GET DIAGNOSTICS` (used in rename_folder_prefix for ROW_COUNT capture).
    - File contains `FOR UPDATE` (TOCTOU mitigation in delete_folder_if_empty).
    - File contains `ON CONFLICT (scope, COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid), path) DO NOTHING` (exact match — must mirror Migration 013:38-43 unique index).
    - File contains `RAISE EXCEPTION` followed by `USING ERRCODE = 'check_violation'` (defense in depth — at least 4 occurrences across the three functions).
    - File contains `RAISE EXCEPTION` followed by `USING ERRCODE = 'no_data_found'` (delete_folder_if_empty when folder missing).
    - File contains NO `CONCURRENTLY` keyword (case-insensitive — forbidden in transactional migrations; matches Phase 1 / Plan 06's comment-keyword-case discipline).
    - File contains NO `DROP TABLE` or bulk `DELETE FROM` without WHERE clause (CLAUDE.md rule — defensive sanity check).
    - File parses with `psycopg2` without errors when the apply runner runs Task 2.
    - File length is at least 120 lines.
    - Filename matches `019_folder_rename_and_delete_rpcs.sql` exactly (lexical sort puts it after 018_storage_rls.sql).
  </acceptance_criteria>
  <done>
    Migration 019 file exists with three functioning PL/pgSQL RPCs, structurally valid, idempotent re-runnable. Ready for Task 2 to apply via run_migrations.py. Plans 02 and 04 can now reference these RPCs by name.
  </done>
</task>

<task id="3-01-02" type="checkpoint:human-action" gate="blocking">
  <name>Task 2: Apply Migration 019 to live database (operator-supplied DATABASE_URL required)</name>
  <what-built>
    Migration 019 (`backend/migrations/019_folder_rename_and_delete_rpcs.sql`) is written and ready to apply (Task 1). The next step requires the developer's `DATABASE_URL` environment variable to point at the target Supabase project's Postgres direct-connection string — the same connection used in Phase 1 / Plan 07 + Phase 2 setup.

    Three PL/pgSQL functions will be created on the live database:
      - `public.rename_folder_prefix(TEXT, TEXT, TEXT, UUID)` — Plan 02's `rename_folder()` calls this.
      - `public.delete_folder_if_empty(UUID)` — Plan 02's `delete_folder()` calls this.
      - `public.create_folder_if_not_exists(TEXT, UUID, TEXT)` — Plan 02's `create_folder()` calls this.

    All three are `CREATE OR REPLACE` (idempotent — safe to re-run). All three are `SECURITY INVOKER` (RLS applies; admin gate at the router layer is the first line of defense).
  </what-built>
  <how-to-verify>
    Operator performs these steps in order:

    1. Confirm `DATABASE_URL` is set in the current shell session and points at the SAME project Phase 1 + Phase 2 used. From the project root (PowerShell):
       ```
       cd backend; venv/Scripts/python -c "import os; url = os.environ.get('DATABASE_URL', ''); print('DATABASE_URL is set' if url.startswith('postgres') else 'DATABASE_URL is NOT set or invalid'); print(f'  starts with: {url[:30]}...' if url else '')"
       ```
       Expected: `DATABASE_URL is set` followed by `starts with: postgresql://...` or `postgres://...`.

       If NOT set:
       - Get the connection string from Supabase Dashboard -> Project Settings -> Database -> Connection string -> URI -> **Direct connection** (port 5432, NOT pooler).
       - Set it (PowerShell): `$env:DATABASE_URL = "postgresql://postgres.<project>:<password>@<host>:5432/postgres"`
       - Re-run the verification.

    2. Apply the migration. From the project root:
       ```
       cd backend; venv/Scripts/python scripts/run_migrations.py
       ```

       Expected stdout (relevant lines):
       ```
       Found N migration(s) in <path>
         ...
         - 018_storage_rls.sql
         - 019_folder_rename_and_delete_rpcs.sql

       RUN  001_threads_and_messages.sql ... OK
       ...
       RUN  018_storage_rls.sql ... OK
       RUN  019_folder_rename_and_delete_rpcs.sql ... OK

       All N migration(s) applied successfully.
       ```
       Exit code: 0.

       On FAIL: read the error message carefully. Re-running is safe (every migration is idempotent — `CREATE OR REPLACE FUNCTION` re-applies as no-op). Do NOT manually run individual migrations via psql or Supabase SQL editor.

    3. Verify the three functions exist via psycopg2 + pg_proc query. From the project root:
       ```
       cd backend; venv/Scripts/python -c "import os, psycopg2; conn = psycopg2.connect(os.environ['DATABASE_URL']); cur = conn.cursor(); cur.execute(\"SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON p.pronamespace=n.oid WHERE n.nspname='public' AND p.proname IN ('rename_folder_prefix','delete_folder_if_empty','create_folder_if_not_exists') ORDER BY p.proname;\"); rows = cur.fetchall(); print(f'Found {len(rows)} Phase 3 RPCs:'); [print(f'  - {r[0]}') for r in rows]; conn.close(); assert len(rows) == 3, f'expected 3 RPCs, got {len(rows)}'"
       ```
       Expected stdout:
       ```
       Found 3 Phase 3 RPCs:
         - create_folder_if_not_exists
         - delete_folder_if_empty
         - rename_folder_prefix
       ```

    4. (Optional smoke check) Probe `rename_folder_prefix` via supabase-py with a non-matching prefix (no-op). The pre-flight canary in Plan 06's `test_folders.py` does this same probe.
  </how-to-verify>
  <resume-signal>Type "approved" once Migration 019 has been applied successfully (exit code 0) AND the three RPCs exist in pg_proc (verification step 3 prints "Found 3 Phase 3 RPCs"). Plans 02-06 are unblocked.</resume-signal>
  <done>
    Migration 019 is applied to the live Supabase Postgres database. `pg_proc` confirms all three RPCs exist in the `public` schema. The phase's blocking dependency (RPCs must exist before Plan 02 can call them) is satisfied. Re-running run_migrations.py is a no-op (`CREATE OR REPLACE FUNCTION` is idempotent).
  </done>
</task>

<task id="3-01-03" type="auto">
  <name>Task 3: Add Phase 3 Pydantic models to schemas.py (FolderResponse, FolderCreate, FolderPatch, FilePatch + DocumentResponse extensions)</name>
  <files>backend/app/models/schemas.py</files>
  <read_first>
    - backend/app/models/schemas.py FULL FILE (the in-place edit point — DocumentResponse at L32-44 needs nullable `user_id` + new `folder_path` / `scope` fields; new models added after L44 to keep file/folder models adjacent)
    - .planning/phases/03-folder-service-routers-dedup-extension/03-RESEARCH.md §Files Router Extensions + §Folders Router Design (paste-ready Pydantic shapes)
    - .planning/phases/03-folder-service-routers-dedup-extension/03-PATTERNS.md §`backend/app/models/schemas.py` (paste-ready model definitions with inline comments)
    - backend/migrations/012_folder_path_and_scope.sql L23-37 (the coupling CHECK that makes user_id nullable for scope='global' — without `Optional[str] = None` on DocumentResponse.user_id, FastAPI response serialization raises ValidationError on global docs)
    - backend/migrations/015_two_scope_rls.sql L37-55 (forbid_scope_mutation trigger — this is WHY FilePatch must omit scope; comment in FilePatch references this)
  </read_first>
  <action>
    Modify `backend/app/models/schemas.py` to add four new Pydantic v2 models AND extend the existing `DocumentResponse` with two new fields plus a nullability change. Match the existing file's style: BaseModel + Optional[T] = None for nullable, datetime imported from `datetime` (already done at L2), Optional from `typing` (already done at L3).

    ### Step 1: Modify `DocumentResponse` (currently L32-44)

    Change `user_id: str` (currently L34) to `user_id: Optional[str] = None`, and add two new fields after `metadata: Optional[dict] = None` (currently L41) and BEFORE `action: Optional[str] = None` (currently L42).

    The full updated DocumentResponse becomes:

    ```python
    class DocumentResponse(BaseModel):
        id: str
        user_id: Optional[str] = None       # CHANGED: nullable for scope='global' rows (Migration 012 coupling CHECK)
        file_name: str
        file_size: int
        mime_type: str
        status: str
        error_message: Optional[str] = None
        content_hash: Optional[str] = None
        metadata: Optional[dict] = None
        folder_path: str = "/"              # NEW (Phase 3 / FOLDER-07) — default preserves existing-row response shape
        scope: str = "user"                 # NEW (Phase 3 / FOLDER-07) — default preserves existing-row response shape
        action: Optional[str] = None        # "created" | "skipped" | "updated" (only on upload response)
        created_at: datetime
        updated_at: datetime
    ```

    ### Step 2: Add four new models AFTER `DocumentResponse` (insert immediately before `class MetadataFieldDefinition` at currently L47)

    ```python


    class FolderResponse(BaseModel):
        id: str
        scope: str                          # 'user' | 'global'
        user_id: Optional[str] = None       # nullable for scope='global' rows
        path: str
        created_at: datetime


    class FolderCreate(BaseModel):
        path: str
        scope: str = "user"                 # 'user' | 'global'


    class FolderPatch(BaseModel):
        new_path: str


    class FilePatch(BaseModel):
        # Mutable fields ONLY. scope is IMMUTABLE (Migration 015 forbid_scope_mutation
        # trigger raises check_violation if NEW.scope IS DISTINCT FROM OLD.scope);
        # file_size / mime_type / status / content_hash / content_markdown are managed
        # by ingestion. Pydantic v2 ignores unknown fields on body parsing by default,
        # so a smuggled "scope" in the request body is silently dropped here — three-
        # layer defense (Pydantic -> Plan 05 router rejects empty update_data ->
        # DB trigger).
        file_name: Optional[str] = None
        folder_path: Optional[str] = None
    ```

    ### Step 3: Make NO other modifications

    - Do NOT touch `ThreadCreate`, `ThreadResponse`, `MessageCreate`, `MessageResponse`, `MetadataFieldDefinition`, `ProfileResponse`, `GlobalSettingsResponse`, `GlobalSettingsUpdate`.
    - Do NOT add new imports — all needed imports (`BaseModel`, `datetime`, `Optional`) are already at L1-3.
    - Do NOT add `model_config = ConfigDict(...)` — Pydantic v2's defaults (extra='ignore') are correct for FilePatch's smuggled-field defense.
    - Do NOT export from `__init__.py` — schemas are imported by full path from `app.models.schemas`.

    Critical DON'Ts:
    - DO NOT add `scope: Optional[str] = None` to FilePatch — even silent acceptance is a security risk; Migration 015's trigger blocks it but the cleaner pattern is to omit the field entirely.
    - DO NOT remove the `action` field from DocumentResponse — Plan 05 (files router) still uses it for upload response shape ("created" | "skipped" | "updated").
    - DO NOT change DocumentResponse's `id`, `file_name`, `file_size`, `mime_type`, `status` types — those are existing contracts the test suite asserts against.
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -c "import pathlib, ast; src = pathlib.Path('app/models/schemas.py').read_text(encoding='utf-8'); ast.parse(src); body = '\n'.join(line for line in src.splitlines() if not line.lstrip().startswith('#')); assert 'class FolderResponse' in body, 'FolderResponse missing'; assert 'class FolderCreate' in body, 'FolderCreate missing'; assert 'class FolderPatch' in body, 'FolderPatch missing'; assert 'class FilePatch' in body, 'FilePatch missing'; assert 'user_id: Optional[str] = None' in body, 'DocumentResponse.user_id must be nullable'; assert 'folder_path: str = ' in body, 'DocumentResponse.folder_path = / default missing'; assert 'scope: str = \"user\"' in body or \"scope: str = 'user'\" in body, 'DocumentResponse.scope=user default missing'; assert body.count('class ') &gt;= 12, f'expected at least 12 classes, got {body.count(chr(99)+chr(108)+chr(97)+chr(115)+chr(115)+chr(32))}'; assert 'new_path: str' in body, 'FolderPatch.new_path missing'; import importlib.util; spec = importlib.util.spec_from_file_location('schemas', 'app/models/schemas.py'); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); assert hasattr(mod, 'FolderResponse'), 'FolderResponse not importable'; assert hasattr(mod, 'FolderCreate'), 'FolderCreate not importable'; assert hasattr(mod, 'FolderPatch'), 'FolderPatch not importable'; assert hasattr(mod, 'FilePatch'), 'FilePatch not importable'; fp = mod.FilePatch(); assert fp.file_name is None; assert fp.folder_path is None; fp_smug = mod.FilePatch.model_validate({'scope': 'global'}); assert not hasattr(fp_smug, 'scope') or fp_smug.model_dump(exclude_unset=True).get('scope') is None, 'FilePatch must NOT accept scope field'; dr = mod.DocumentResponse(id='x', file_name='f', file_size=0, mime_type='m', status='s', created_at='2026-01-01T00:00:00', updated_at='2026-01-01T00:00:00'); assert dr.user_id is None; assert dr.folder_path == '/'; assert dr.scope == 'user'; print('schemas.py extensions OK')"</automated>
  </verify>
  <acceptance_criteria>
    - File `backend/app/models/schemas.py` parses as valid Python (`ast.parse` succeeds).
    - `grep -c "^class FolderResponse" backend/app/models/schemas.py` returns 1.
    - `grep -c "^class FolderCreate" backend/app/models/schemas.py` returns 1.
    - `grep -c "^class FolderPatch" backend/app/models/schemas.py` returns 1.
    - `grep -c "^class FilePatch" backend/app/models/schemas.py` returns 1.
    - DocumentResponse contains the line `user_id: Optional[str] = None` (nullable for global rows).
    - DocumentResponse contains `folder_path: str = "/"` (with leading-slash default).
    - DocumentResponse contains `scope: str = "user"` (with safe default).
    - FilePatch class body does NOT contain a `scope:` line (Pitfall B mitigation).
    - FilePatch contains exactly two field definitions: `file_name: Optional[str] = None` and `folder_path: Optional[str] = None`.
    - FolderResponse has fields: `id: str`, `scope: str`, `user_id: Optional[str] = None`, `path: str`, `created_at: datetime` (5 fields).
    - FolderCreate has fields: `path: str`, `scope: str = "user"` (2 fields).
    - FolderPatch has exactly one field: `new_path: str`.
    - Module imports cleanly via `cd backend && venv/Scripts/python -c "from app.models.schemas import FolderResponse, FolderCreate, FolderPatch, FilePatch, DocumentResponse; print('OK')"` printing `OK`.
    - Pydantic v2 model construction smoke check: `DocumentResponse(id='x', file_name='f', file_size=0, mime_type='m', status='s', created_at='2026-01-01T00:00:00', updated_at='2026-01-01T00:00:00')` produces an instance with `user_id is None`, `folder_path == '/'`, `scope == 'user'`.
    - Pydantic v2 smuggled-field test: `FilePatch.model_validate({'scope': 'global'})` succeeds (Pydantic ignores unknown fields by default) AND the resulting instance has no `scope` attribute set in `model_dump(exclude_unset=True)`.
    - File still imports `from pydantic import BaseModel`, `from datetime import datetime`, `from typing import Optional` (no new imports added).
    - All Episode 1 / Phase 2 models (`ThreadCreate`, `ThreadResponse`, `MessageCreate`, `MessageResponse`, `MetadataFieldDefinition`, `ProfileResponse`, `GlobalSettingsResponse`, `GlobalSettingsUpdate`) remain unchanged (`grep -c "^class ThreadCreate" backend/app/models/schemas.py` returns 1; same for the other 7 — total class count is at least 12 = 8 existing + 4 new).
  </acceptance_criteria>
  <done>
    `backend/app/models/schemas.py` extended with FolderResponse, FolderCreate, FolderPatch, FilePatch + DocumentResponse user_id/folder_path/scope changes. Module imports cleanly. Plans 04 (folders router) and 05 (files router PATCH endpoint) can now `from app.models.schemas import ...` the new models. The Migration 015 immutability contract is documented in FilePatch's inline comment for future maintainers.
  </done>
</task>

</tasks>

<verification>
This plan delivers (1) the Phase 3 SQL bedrock — three RPCs that Plans 02 and 04 invoke by name; (2) the Pydantic v2 model interface contracts that Plans 04 and 05 import. Maps to .planning/phases/03-folder-service-routers-dedup-extension/03-VALIDATION.md row "3-01-* | 01 (Migration 019) | 1 | FOLDER-03, FOLDER-04 | T-Pitfall-5 (TOCTOU), T-Pitfall-5 (rollback)".

Verification steps:
- Task 1: AST/grep gates confirm Migration 019 has the three RPCs with correct names, signatures, ON CONFLICT expression matching Migration 013's unique index, FOR UPDATE row lock, ERRCODE='check_violation' / 'no_data_found' raises, no SECURITY DEFINER, no CONCURRENTLY.
- Task 2: Operator-checkpoint applies Migration 019; pg_proc query confirms all three functions exist.
- Task 3: AST/grep gates confirm schemas.py has FolderResponse/FolderCreate/FolderPatch/FilePatch + DocumentResponse extensions; runtime import + Pydantic construction smoke checks confirm the models are functional and FilePatch correctly silences smuggled scope fields.

After this plan completes, Plans 02-06 can run. Specifically:
- Plan 02 invokes `supabase_client.rpc('rename_folder_prefix', {...}).execute()` etc.
- Plan 04 imports `from app.models.schemas import FolderResponse, FolderCreate, FolderPatch`.
- Plan 05 imports `from app.models.schemas import FilePatch`.
- Plan 06's test_folders.py canary probes the RPCs by name and bails with [FATAL] if absent.
</verification>

<success_criteria>
- backend/migrations/019_folder_rename_and_delete_rpcs.sql exists with three RPCs (rename_folder_prefix, delete_folder_if_empty, create_folder_if_not_exists), all SECURITY INVOKER, all GRANT EXECUTE TO authenticated.
- Migration 019 applied to live database (run_migrations.py exit 0; pg_proc confirms 3 functions).
- backend/app/models/schemas.py extended with FolderResponse, FolderCreate, FolderPatch, FilePatch and DocumentResponse changes (nullable user_id, folder_path/scope defaults).
- All three Phase 3 RPCs are invocable by Plan 02's service layer.
- Pydantic models are importable by Plans 04 and 05.
- Plans 02, 03, 04, 05, 06 are unblocked.
</success_criteria>

<output>
After completion, create `.planning/phases/03-folder-service-routers-dedup-extension/03-01-SUMMARY.md` recording: the migration filename, the run_migrations.py output line `RUN  019_folder_rename_and_delete_rpcs.sql ... OK`, the pg_proc verification output (3 functions found), the schemas.py classes added (FolderResponse, FolderCreate, FolderPatch, FilePatch), the DocumentResponse field changes, and a one-line confirmation that Plans 02-06 are unblocked.
</output>
