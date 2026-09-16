-- Phase 1 / Migration 015: Two-scope RLS policies + scope-mutation trigger
-- Replaces Episode 1's single-axis user-isolation RLS (migration 003 lines 28-53)
-- with the two-scope (user × scope) policy catalog. This is the security-critical
-- migration — Pitfall 1 / RANK 1 threat mitigation.
--
-- DESIGN NOTES:
-- 1. Policy names shift from sentence-case ("Users can view own documents") to
--    snake_case ("documents_select", "documents_insert_user"). This is deliberate —
--    the new naming makes the (table, op, scope) decomposition obvious in the
--    pg_policy catalog.
-- 2. (SELECT auth.uid()) subquery form — Postgres caches the result per query
--    (10× faster than bare auth.uid() per row on hot tables). Supabase RLS perf
--    best practice — first use of this pattern in the codebase.
-- 3. RLS-03 (forbid scope mutation): Postgres RLS WITH CHECK cannot reference
--    OLD.col (raises "missing FROM-clause entry for table 'old'"). The canonical
--    workaround is a BEFORE UPDATE trigger that RAISEs on scope change.
-- 4. is_admin() SQL function factors out the EXISTS-from-profiles admin check
--    used in 6+ policies. SECURITY DEFINER bypasses profiles RLS for the lookup.
-- 5. Splitting INSERT into "_user" and "_global" policies (and same for UPDATE/DELETE)
--    works because Postgres OR's multiple permissive policies per (table, command).
--    Trivially reviewable: who can do what reads top-to-bottom.

-- ── 1. is_admin() helper ──
-- DRY admin gate used in 6+ policies. SECURITY DEFINER + SET search_path=public
-- mirrors handle_new_user pattern from migration 005:38-50. STABLE lets Postgres
-- cache the result within a single statement.
CREATE OR REPLACE FUNCTION public.is_admin() RETURNS BOOLEAN
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(
    (SELECT is_admin FROM public.profiles WHERE id = auth.uid()),
    false
  );
$$;

GRANT EXECUTE ON FUNCTION public.is_admin() TO authenticated;

-- ── 2. forbid_scope_mutation() trigger function ──
-- WORKAROUND for RLS-03: Postgres RLS WITH CHECK cannot reference OLD.col, so
-- the canonical pattern is a BEFORE UPDATE trigger. Trigger fires after RLS
-- policies pass but before the row is persisted. Raises check_violation (a
-- standard SQLSTATE) if scope is being changed.
-- IMPORTANT: the trigger must return the new row AFTER the IF block (NOT inside
-- it), otherwise non-mutation updates are silently discarded.
CREATE OR REPLACE FUNCTION public.forbid_scope_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.scope IS DISTINCT FROM OLD.scope THEN
    RAISE EXCEPTION
      'Scope mutation forbidden: cannot change scope from % to % (use delete + admin re-insert)',
      OLD.scope, NEW.scope
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$;

-- ── 3. Drop Episode-1 single-axis policies ──
-- Names matched verbatim (including capitalization and "own") from
-- backend/migrations/003_byo_retrieval.sql:29-32 and :51-53.
-- No prior migration drops policies — IF EXISTS is the canonical safe form.
DROP POLICY IF EXISTS "Users can view own documents"   ON public.documents;
DROP POLICY IF EXISTS "Users can insert own documents" ON public.documents;
DROP POLICY IF EXISTS "Users can update own documents" ON public.documents;
DROP POLICY IF EXISTS "Users can delete own documents" ON public.documents;

DROP POLICY IF EXISTS "Users can view own chunks"   ON public.document_chunks;
DROP POLICY IF EXISTS "Users can insert own chunks" ON public.document_chunks;
DROP POLICY IF EXISTS "Users can delete own chunks" ON public.document_chunks;

-- ── 4a. documents: 7 new policies (SELECT, INSERT user/global, UPDATE user/global, DELETE user/global) ──
-- The trigger from §2 attached in §5 below is the "8th protection" on documents.

CREATE POLICY "documents_select"
  ON public.documents FOR SELECT
  TO authenticated
  USING (
    scope = 'global'
    OR (scope = 'user' AND user_id = (SELECT auth.uid()))
  );

CREATE POLICY "documents_insert_user"
  ON public.documents FOR INSERT
  TO authenticated
  WITH CHECK (
    scope = 'user' AND user_id = (SELECT auth.uid())
  );

CREATE POLICY "documents_insert_global"
  ON public.documents FOR INSERT
  TO authenticated
  WITH CHECK (
    scope = 'global'
    AND user_id IS NULL
    AND public.is_admin()
  );

CREATE POLICY "documents_update_user"
  ON public.documents FOR UPDATE
  TO authenticated
  USING (scope = 'user' AND user_id = (SELECT auth.uid()))
  WITH CHECK (scope = 'user' AND user_id = (SELECT auth.uid()));

CREATE POLICY "documents_update_global"
  ON public.documents FOR UPDATE
  TO authenticated
  USING (scope = 'global' AND public.is_admin())
  WITH CHECK (scope = 'global' AND public.is_admin());

CREATE POLICY "documents_delete_user"
  ON public.documents FOR DELETE
  TO authenticated
  USING (scope = 'user' AND user_id = (SELECT auth.uid()));

CREATE POLICY "documents_delete_global"
  ON public.documents FOR DELETE
  TO authenticated
  USING (scope = 'global' AND public.is_admin());

-- ── 4b. document_chunks: 5 new policies (SELECT, INSERT user/global, DELETE user/global) ──
-- NO UPDATE policy — chunks are insert-and-delete only. Re-ingestion is
-- delete-then-insert per record_manager pattern in migration 006. The trigger
-- attached in §5 is defensive (fires only if a future migration adds an UPDATE policy).

CREATE POLICY "document_chunks_select"
  ON public.document_chunks FOR SELECT
  TO authenticated
  USING (
    scope = 'global'
    OR (scope = 'user' AND user_id = (SELECT auth.uid()))
  );

CREATE POLICY "document_chunks_insert_user"
  ON public.document_chunks FOR INSERT
  TO authenticated
  WITH CHECK (
    scope = 'user' AND user_id = (SELECT auth.uid())
  );

CREATE POLICY "document_chunks_insert_global"
  ON public.document_chunks FOR INSERT
  TO authenticated
  WITH CHECK (
    scope = 'global'
    AND user_id IS NULL
    AND public.is_admin()
  );

CREATE POLICY "document_chunks_delete_user"
  ON public.document_chunks FOR DELETE
  TO authenticated
  USING (scope = 'user' AND user_id = (SELECT auth.uid()));

CREATE POLICY "document_chunks_delete_global"
  ON public.document_chunks FOR DELETE
  TO authenticated
  USING (scope = 'global' AND public.is_admin());

-- ── 4c. folders: 7 new policies (same shape as documents) ──

CREATE POLICY "folders_select"
  ON public.folders FOR SELECT
  TO authenticated
  USING (
    scope = 'global'
    OR (scope = 'user' AND user_id = (SELECT auth.uid()))
  );

CREATE POLICY "folders_insert_user"
  ON public.folders FOR INSERT
  TO authenticated
  WITH CHECK (
    scope = 'user' AND user_id = (SELECT auth.uid())
  );

CREATE POLICY "folders_insert_global"
  ON public.folders FOR INSERT
  TO authenticated
  WITH CHECK (
    scope = 'global'
    AND user_id IS NULL
    AND public.is_admin()
  );

CREATE POLICY "folders_update_user"
  ON public.folders FOR UPDATE
  TO authenticated
  USING (scope = 'user' AND user_id = (SELECT auth.uid()))
  WITH CHECK (scope = 'user' AND user_id = (SELECT auth.uid()));

CREATE POLICY "folders_update_global"
  ON public.folders FOR UPDATE
  TO authenticated
  USING (scope = 'global' AND public.is_admin())
  WITH CHECK (scope = 'global' AND public.is_admin());

CREATE POLICY "folders_delete_user"
  ON public.folders FOR DELETE
  TO authenticated
  USING (scope = 'user' AND user_id = (SELECT auth.uid()));

CREATE POLICY "folders_delete_global"
  ON public.folders FOR DELETE
  TO authenticated
  USING (scope = 'global' AND public.is_admin());

-- ── 5. Attach BEFORE UPDATE triggers (RLS-03) ──
-- Idempotent shape: drop-if-exists then create (matches the trigger
-- (re)installation pattern from migration 005:53-56 and 008:18-22).
DROP TRIGGER IF EXISTS documents_forbid_scope_mutation ON public.documents;
CREATE TRIGGER documents_forbid_scope_mutation
  BEFORE UPDATE ON public.documents
  FOR EACH ROW EXECUTE FUNCTION public.forbid_scope_mutation();

DROP TRIGGER IF EXISTS document_chunks_forbid_scope_mutation ON public.document_chunks;
CREATE TRIGGER document_chunks_forbid_scope_mutation
  BEFORE UPDATE ON public.document_chunks
  FOR EACH ROW EXECUTE FUNCTION public.forbid_scope_mutation();

DROP TRIGGER IF EXISTS folders_forbid_scope_mutation ON public.folders;
CREATE TRIGGER folders_forbid_scope_mutation
  BEFORE UPDATE ON public.folders
  FOR EACH ROW EXECUTE FUNCTION public.forbid_scope_mutation();
