-- Phase 1 / Migration 016: search-acceleration indexes
-- Adds the index set that Phase 4's tree/glob/grep/list_files/read_document tools
-- depend on for sub-second latency at scale. pg_trgm extension was enabled in
-- migration 012; this migration consumes it.
--
-- TWO NET-NEW PATTERNS in this codebase (no prior precedent — comment for reviewers):
-- 1. `gin_trgm_ops` operator class on TEXT columns (existing migrations use default
--    jsonb_ops on JSONB and default for tsvector). Required for ILIKE/regex acceleration.
-- 2. `text_pattern_ops` operator class on btree (existing migrations use default
--    collation btree). Required for `LIKE 'prefix/%'` in non-C locales (Supabase
--    runs en_US.UTF-8) — default-collation btree is silently NOT used for LIKE
--    in non-C locales, the classic foot-gun this migration eliminates.
--
-- INDEX BUILD STRATEGY:
-- All indexes use plain `CREATE INDEX` (the non-concurrent form) because
-- run_migrations.py runs each migration inside a transaction (autocommit=False;
-- verified at backend/scripts/run_migrations.py:39). The concurrent variant
-- (`create index concurrently …`) raises "ERROR: ... cannot run inside a
-- transaction block" — Postgres forbids it inside a transaction. Plain CREATE INDEX
-- acquires SHARE lock (blocks writes for the build duration); at Episode 2 boot
-- (low-thousands docs per user) the build is sub-second — acceptable.
-- For production at 10k+ docs per user, run the concurrent (`create index
-- concurrently …`) variants manually outside the migration runner during a
-- maintenance window: drop the in-transaction index, then recreate it with
-- the non-blocking variant.
--
-- DEFERRED to Phase 4 (per RESEARCH.md §4 / Open Question §7):
-- - Composite (scope, COALESCE(user_id,'00..0'::uuid), folder_path) index. Add only
--   after EXPLAIN ANALYZE on actual Phase 4 query shapes shows it's needed.
--   Adding it speculatively now risks index bloat and slows writes.

-- ── 1. GIN trigram on documents.content_markdown (powers Phase 4 grep) ──
-- Accelerates ILIKE / ~ / ~* queries with literal substrings ≥3 chars.
-- Pitfall 3 (RANK 4) mitigation. ROADMAP success criterion 3 verifier.
CREATE INDEX IF NOT EXISTS documents_content_markdown_trgm_idx
  ON documents USING gin (content_markdown gin_trgm_ops);

-- ── 2. GIN trigram on documents.folder_path (powers Phase 4 glob substring) ──
-- For non-pure-prefix patterns like `**/*foo*` where the prefix btree below
-- doesn't help. Cheap because folder_path is a small TEXT column.
CREATE INDEX IF NOT EXISTS documents_folder_path_trgm_idx
  ON documents USING gin (folder_path gin_trgm_ops);

-- ── 3. text_pattern_ops btree on documents.folder_path (powers LIKE 'prefix/%') ──
-- CRITICAL: default-collation btree is NOT used for prefix LIKE in non-C locales
-- (Supabase is en_US.UTF-8). text_pattern_ops forces byte-wise comparison and
-- enables the index. Without this, Phase 4 tree/list_files/glob fall back to
-- Seq Scan even though a btree exists.
CREATE INDEX IF NOT EXISTS documents_folder_path_prefix_idx
  ON documents (folder_path text_pattern_ops);

-- ── 4. GIN trigram on folders.path ──
-- Same rationale as #2 but for the empty-folder side table. Phase 3's folder
-- listing endpoints and Phase 4's tree may query folders directly.
CREATE INDEX IF NOT EXISTS folders_path_trgm_idx
  ON public.folders USING gin (path gin_trgm_ops);

-- ── 5. text_pattern_ops btree on folders.path ──
-- Same rationale as #3 but for the folders side table.
CREATE INDEX IF NOT EXISTS folders_path_prefix_idx
  ON public.folders (path text_pattern_ops);
