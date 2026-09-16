-- Phase 1 / Migration 014: content_markdown column + status enum + backfill-scan index
-- Adds the nullable content_markdown TEXT column and the content_markdown_status enum.
-- Existing Episode 1 documents get content_markdown_status='pending' via DEFAULT
-- (metadata-only change in PG11+; no full table rewrite). Phase 2's backfill_content_markdown.py
-- re-runs Docling against original Storage blobs and flips status to 'ready' / 'failed' /
-- 'requires_user_reupload'. Phase 4's grep and read_document tools surface non-'ready'
-- status explicitly (per Pitfall 6 — never silently skip rows that have NULL content).
--
-- TEXT + CHECK is used instead of an ENUM type per RESEARCH.md §2 — ALTER TYPE in
-- Postgres ENUMs is painful in migrations; TEXT + CHECK is identically safe and easier
-- to evolve (just DROP CONSTRAINT … ADD CONSTRAINT … with a new value list).

ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS content_markdown        TEXT,
  ADD COLUMN IF NOT EXISTS content_markdown_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (content_markdown_status IN (
      'pending',
      'ready',
      'failed',
      'requires_user_reupload'
    ));

-- Partial index for Phase 2's backfill scan: "find every document still needing
-- content_markdown work." Index covers only non-'ready' rows, which keeps it small
-- in steady state (most docs become 'ready' after backfill). New convention for
-- this codebase (no prior migration uses a partial index) — call out for reviewers.
CREATE INDEX IF NOT EXISTS documents_content_markdown_status_idx
  ON documents (content_markdown_status)
  WHERE content_markdown_status <> 'ready';
