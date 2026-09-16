-- Phase 1 / Migration 013: folders table + unique expression index + RLS enable
-- Side table for first-class empty-folder tracking. Documents reference folders
-- ONLY by string path (no FK) per ARCHITECTURE.md Pattern 2 — folders is a sparse,
-- explicit-empty-only table. Most folders exist by inference from documents.folder_path.
-- RLS is enabled here; policies are added in migration 015 (kept together for review).

-- ── 1. folders: create table with both CHECK constraints ──
CREATE TABLE IF NOT EXISTS public.folders (
  id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  scope      TEXT        NOT NULL CHECK (scope IN ('user', 'global')),
  user_id    UUID        REFERENCES auth.users(id) ON DELETE CASCADE,
  path       TEXT        NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  -- Coupling: user_id required iff scope='user' (mirrors documents/document_chunks
  -- coupling CHECK from migration 012; same shape across all three tables).
  CONSTRAINT folders_scope_user_id_consistency CHECK (
    (scope = 'user'   AND user_id IS NOT NULL)
    OR (scope = 'global' AND user_id IS NULL)
  ),

  -- Canonical-form path (mirrors documents.folder_path canonical regex from
  -- migration 012; defense in depth for the Python normalize_path() chokepoint).
  CONSTRAINT folders_path_canonical CHECK (
    path = '/' OR path ~ '^/[^/]+(/[^/]+)*$'
  )
);

-- ── 2. Unique expression index with COALESCE sentinel ──
-- Postgres treats NULLs as distinct in unique indexes by default — without the
-- COALESCE, two global rows with the same path would both be allowed (NULL != NULL).
-- The all-zeros UUID sentinel forces NULL user_id to compare equal across rows.
-- Pitfall 10 mitigation — concurrent uploads to the same new path produce exactly
-- one folders row (the second INSERT fails on the unique index). Phase 3 will pair
-- with INSERT ... ON CONFLICT DO NOTHING.
-- (Table-level UNIQUE accepts only column lists, not expressions; CREATE UNIQUE
-- INDEX is required.)
CREATE UNIQUE INDEX IF NOT EXISTS folders_scope_user_path_unique
  ON public.folders (
    scope,
    COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid),
    path
  );

-- ── 3. General listing index ──
-- "All folders for this user/scope" — supports the folder-tree listing query.
CREATE INDEX IF NOT EXISTS folders_scope_user_idx
  ON public.folders (scope, user_id);

-- ── 4. Enable RLS ──
-- Policies land in migration 015 alongside the two-scope policies for documents
-- and document_chunks (kept together so the policy catalog is reviewable in one file).
-- Until 015 runs, RLS-enabled-no-policies = fail-closed default for the authenticated
-- role: all reads/writes from authenticated are denied. Service-role bypasses RLS.
ALTER TABLE public.folders ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.folders TO authenticated;
