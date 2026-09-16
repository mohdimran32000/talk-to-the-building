-- Phase 1 / Migration 012: folder_path + scope columns + pg_trgm extension
-- Adds the two new axes to documents/document_chunks and prepares for two-scope RLS.
-- Replaces UNIQUE (user_id, file_name) from migration 006 with a scope+folder-aware
-- unique expression index. Enables pg_trgm here (early) so migration 016 can use
-- gin_trgm_ops without dependency surprises.

-- ── 0. Enable trigram extension up front ──
-- pg_trgm is bundled with Postgres; CREATE EXTENSION is sub-second on Supabase.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ── 1. documents: add folder_path + scope columns ──
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS folder_path TEXT NOT NULL DEFAULT '/',
  ADD COLUMN IF NOT EXISTS scope       TEXT NOT NULL DEFAULT 'user'
    CHECK (scope IN ('user', 'global'));

-- Make user_id nullable for scope='global' rows (which have no owning user).
-- Existing rows keep their non-null user_id; this is a metadata-only change.
ALTER TABLE documents ALTER COLUMN user_id DROP NOT NULL;

-- ── 2. documents: scope/user_id coupling CHECK ──
-- Pitfall 1 mitigation: prevents orphan-leak where (scope='user', user_id=NULL)
-- would silently bypass RLS user-isolation. Phase 1 success criterion 1.
ALTER TABLE documents
  DROP CONSTRAINT IF EXISTS documents_scope_user_id_consistency;
ALTER TABLE documents
  ADD CONSTRAINT documents_scope_user_id_consistency CHECK (
    (scope = 'user'   AND user_id IS NOT NULL)
    OR (scope = 'global' AND user_id IS NULL)
  );

-- ── 3. documents: folder_path canonical-form CHECK ──
-- Pitfall 4 mitigation: defense in depth for the Python normalize_path() chokepoint
-- (backend/app/services/folder_service.py). Rejects 'projects' (no leading slash),
-- 'projects/' (trailing slash), '//' (double slash), and any backslash-bearing input.
-- Phase 1 success criterion 4.
ALTER TABLE documents
  DROP CONSTRAINT IF EXISTS documents_folder_path_canonical;
ALTER TABLE documents
  ADD CONSTRAINT documents_folder_path_canonical CHECK (
    folder_path = '/' OR folder_path ~ '^/[^/]+(/[^/]+)*$'
  );

-- ── 4. documents: replace old (user_id, file_name) unique with scope-aware unique ──
-- The Phase 3 record_manager dedup key (FOLDER-05) is
-- (scope, user_id, folder_path, file_name, hash). Phase 1 enforces the path/scope
-- portion at the DB level; hash-level dedup remains in app code.
-- COALESCE sentinel forces NULL user_id (global rows) to compare equal across rows.
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_user_filename_unique;

CREATE UNIQUE INDEX IF NOT EXISTS documents_scope_user_path_filename_unique
  ON documents (
    scope,
    COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid),
    folder_path,
    file_name
  );

-- ── 5. document_chunks: add scope column + coupling CHECK ──
-- Chunks denormalize scope (for RLS performance — RLS predicate doesn't have to
-- join back to documents on every chunk read). folder_path is NOT denormalized
-- onto chunks (per RESEARCH.md Open Question §8 — defer until Phase 4 query
-- plans show join cost is unacceptable).
ALTER TABLE document_chunks
  ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'user'
    CHECK (scope IN ('user', 'global'));

ALTER TABLE document_chunks ALTER COLUMN user_id DROP NOT NULL;

ALTER TABLE document_chunks
  DROP CONSTRAINT IF EXISTS document_chunks_scope_user_id_consistency;
ALTER TABLE document_chunks
  ADD CONSTRAINT document_chunks_scope_user_id_consistency CHECK (
    (scope = 'user'   AND user_id IS NOT NULL)
    OR (scope = 'global' AND user_id IS NULL)
  );
