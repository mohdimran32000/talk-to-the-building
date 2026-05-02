---
phase: 01
plan: 06
type: execute
wave: 3
depends_on: [02, 04]
files_modified:
  - backend/migrations/016_search_indexes.sql
autonomous: true
requirements:
  - SCHEMA-05
must_haves:
  truths:
    - "GIN trigram index documents_content_markdown_trgm_idx exists on documents (content_markdown gin_trgm_ops)"
    - "GIN trigram index documents_folder_path_trgm_idx exists on documents (folder_path gin_trgm_ops)"
    - "Btree index documents_folder_path_prefix_idx exists on documents (folder_path text_pattern_ops)"
    - "GIN trigram index folders_path_trgm_idx exists on public.folders (path gin_trgm_ops)"
    - "Btree index folders_path_prefix_idx exists on public.folders (path text_pattern_ops)"
    - "EXPLAIN ANALYZE on `SELECT id FROM documents WHERE content_markdown ILIKE '%foo%'` shows Bitmap Index Scan on documents_content_markdown_trgm_idx (NOT Seq Scan) once a fixture row exists"
    - "EXPLAIN ANALYZE on `SELECT id FROM documents WHERE folder_path LIKE '/projects/%'` shows Index Scan on documents_folder_path_prefix_idx (NOT Seq Scan)"
    - "pg_trgm extension is enabled (verified via SELECT 1 FROM pg_extension WHERE extname='pg_trgm')"
    - "All indexes created with plain CREATE INDEX (NOT CONCURRENTLY) per migration runner transaction constraint"
  artifacts:
    - path: "backend/migrations/016_search_indexes.sql"
      provides: "5 search-acceleration indexes — trigram on content_markdown + folder_path on documents and folders, plus text_pattern_ops btree for prefix LIKE on folder_path/path"
      contains: "CREATE INDEX IF NOT EXISTS documents_content_markdown_trgm_idx"
      contains_2: "gin_trgm_ops"
      contains_3: "text_pattern_ops"
      contains_4: "documents_folder_path_prefix_idx"
      min_lines: 30
  key_links:
    - from: "documents_content_markdown_trgm_idx (GIN trigram)"
      to: "Phase 4 grep tool (TOOL-03)"
      via: "Bitmap Index Scan accelerates ILIKE/regex queries on content_markdown"
      pattern: "content_markdown gin_trgm_ops"
    - from: "documents_folder_path_prefix_idx (text_pattern_ops btree)"
      to: "Phase 4 tree/glob/list_files tools (TOOL-01, TOOL-02, TOOL-04)"
      via: "Index Scan accelerates LIKE 'prefix/%' queries (default-collation btree fails in en_US.UTF-8 locale)"
      pattern: "folder_path text_pattern_ops"
    - from: "pg_trgm extension (enabled in migration 012)"
      to: "All gin_trgm_ops indexes in this migration"
      via: "extension dependency — 016 references gin_trgm_ops which requires pg_trgm"
      pattern: "gin_trgm_ops"
---

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Phase 4 grep/tree/glob queries -> documents/folders indexes | Performance is a security concern at scale (Pitfall 3 — grep degrades to Seq Scan, connection pool starves) |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-1-04 | Denial of Service / Performance | documents.content_markdown | mitigate | GIN trigram index `documents_content_markdown_trgm_idx` accelerates `ILIKE`/`~`/`~*` queries with literal substrings ≥3 chars (Phase 4 grep tool). Without this index, grep degrades from ~80ms (50 docs) to 8s+ (5000 docs) — Seq Scan over the entire documents table per query, Supabase connection pool starvation. Pitfall 3 RANK 4 mitigation. ROADMAP success criterion 3 is the gate (`EXPLAIN ANALYZE` must show `Bitmap Index Scan`). |
| T-1-04-prefix | Performance | documents.folder_path / folders.path | mitigate | `text_pattern_ops` btree index forces byte-wise comparison, enabling `LIKE 'prefix/%'` queries to use the index. Default-collation btree (created without an opclass) does NOT use the index for LIKE in non-C locales — Supabase runs `en_US.UTF-8`. Pitfall 4 (perf table) and Pitfall 3 mitigation. |
| T-1-04-glob | Performance | documents.folder_path / folders.path | mitigate | GIN trigram on folder_path/path accelerates Phase 4 glob's `**/*pattern*` substring matches that are not pure-prefix. Cheap to build because folder_path is a small column. |
| T-1-Aux | Operational | Migration safety | accept | Plain `CREATE INDEX` (NOT `CONCURRENTLY`) acquires SHARE lock that blocks writes for the build duration. At Episode 2 boot (low-thousands docs per user) the build is sub-second — acceptable. For production at 10k+ docs per user, the operator runs `CREATE INDEX CONCURRENTLY` versions manually outside the migration runner (documented in the migration header as the production-scale upgrade path). RESEARCH.md §8 verified this trade-off. |
</threat_model>

<objective>
Write `backend/migrations/016_search_indexes.sql` — adds the search-acceleration index set: (1) GIN trigram on `documents.content_markdown` (Phase 4 grep), (2) GIN trigram on `documents.folder_path` (Phase 4 glob substring), (3) `text_pattern_ops` btree on `documents.folder_path` (Phase 4 tree/list_files prefix LIKE), (4) GIN trigram on `folders.path` (Phase 3/4 listing), (5) `text_pattern_ops` btree on `folders.path` (same). Uses plain `CREATE INDEX` (not CONCURRENTLY — runner constraint per RESEARCH.md §8). pg_trgm extension was enabled in migration 012; this migration consumes it. Defers the speculative composite `(scope, user_id, folder_path)` index to Phase 4 (RESEARCH.md §4 / Open Question §7).
</objective>

<execution_context>
@.claude/get-shit-done/workflows/execute-plan.md
@.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md

@.planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-RESEARCH.md
@.planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-PATTERNS.md
@.planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-VALIDATION.md
@.planning/research/PITFALLS.md
@CLAUDE.md

@backend/migrations/007_document_metadata.sql
@backend/migrations/008_hybrid_search.sql
@backend/migrations/012_folder_path_and_scope.sql
@backend/migrations/013_folders_table.sql
@backend/migrations/014_content_markdown_column.sql

<interfaces>
<!-- Schema dependencies (must exist before this migration runs). -->

documents.content_markdown TEXT  -- created in migration 014 (plan 04)
documents.folder_path     TEXT NOT NULL DEFAULT '/'  -- created in migration 012 (plan 02)
public.folders.path       TEXT NOT NULL  -- created in migration 013 (plan 03)
pg_trgm extension         -- enabled in migration 012 (plan 02)

Index naming convention (codebase): <table>_<column>_<purpose>_idx
- documents_content_markdown_trgm_idx
- documents_folder_path_trgm_idx
- documents_folder_path_prefix_idx
- folders_path_trgm_idx
- folders_path_prefix_idx

Operator class semantics:
- gin_trgm_ops: GIN index using trigrams; accelerates ILIKE/~/~* with literal substrings ≥3 chars (Pitfall 3, RESEARCH.md §4)
- text_pattern_ops: btree using byte-wise comparison; required for LIKE 'prefix%' in non-C locales (Pitfall 4 perf table; RESEARCH.md §4)
- (default jsonb_ops/vector_ops/etc applied elsewhere — not used here)

Runner constraint:
- run_migrations.py runs each file in a transaction (autocommit=False)
- CREATE INDEX CONCURRENTLY is FORBIDDEN inside transactions
- Plain CREATE INDEX acquires SHARE lock (blocks writes) — acceptable at low-thousands rows per user
- For production scale: operator runs CONCURRENTLY versions manually (documented in header)
</interfaces>
</context>

<tasks>

<task id="1-06-01" type="auto">
  <name>Task 1: Write migration 016 — search-acceleration indexes (gin_trgm_ops + text_pattern_ops)</name>
  <files>backend/migrations/016_search_indexes.sql</files>
  <read_first>
    - backend/migrations/008_hybrid_search.sql (lines 5-8 — closest analog: `CREATE INDEX IF NOT EXISTS … USING gin(...)` shape)
    - backend/migrations/007_document_metadata.sql (line 6 — `CREATE INDEX IF NOT EXISTS … USING gin (col)` shape, with default jsonb_ops; 016 differs by passing gin_trgm_ops explicitly)
    - backend/migrations/012_folder_path_and_scope.sql (the file from plan 02 — confirms pg_trgm extension is enabled before 016 references gin_trgm_ops; confirms folder_path column exists on documents)
    - backend/migrations/013_folders_table.sql (the file from plan 03 — confirms public.folders.path column exists)
    - backend/migrations/014_content_markdown_column.sql (the file from plan 04 — confirms documents.content_markdown column exists)
    - .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-RESEARCH.md § "Migration 016 — pg_trgm + text_pattern_ops indexes" (lines ~723-764 — DEFINITIVE DDL skeleton; paste-ready) AND § Decisions §4 (lines ~289-325 — explains why each index, why composite is deferred to Phase 4) AND § Decisions §8 (lines ~459-472 — explains why plain CREATE INDEX not CONCURRENTLY)
    - .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-PATTERNS.md § "backend/migrations/016_search_indexes.sql" (lines ~224-255 — confirms 008 + 007 are analogs; calls out gin_trgm_ops and text_pattern_ops as net-new patterns with no prior precedent — must be commented for reviewers)
    - .planning/research/PITFALLS.md § Pitfall 3 (lines ~73-99 — grep perf collapse threat motivating these indexes)
  </read_first>
  <action>
    Create `backend/migrations/016_search_indexes.sql` with the EXACT SQL below (paste-ready from RESEARCH.md § Migration 016).

```sql
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
-- All indexes use plain `CREATE INDEX` (NOT CONCURRENTLY) because run_migrations.py
-- runs each migration inside a transaction (autocommit=False; verified at
-- backend/scripts/run_migrations.py:39). CREATE INDEX CONCURRENTLY raises
-- "ERROR: CREATE INDEX CONCURRENTLY cannot run inside a transaction block".
-- Plain CREATE INDEX acquires SHARE lock (blocks writes for the build duration);
-- at Episode 2 boot (low-thousands docs per user) the build is sub-second — acceptable.
-- For production at 10k+ docs per user, run CREATE INDEX CONCURRENTLY versions
-- manually outside the migration runner during a maintenance window (drop in-tx
-- index, recreate CONCURRENTLY).
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
```

Conventions to honor (per .planning/phases/01-.../01-PATTERNS.md):
- Filename `016_search_indexes.sql`.
- Header: long context comment block (~25 lines) — this migration warrants extensive context because gin_trgm_ops and text_pattern_ops are both net-new in the codebase, AND the "plain CREATE INDEX not CONCURRENTLY" choice has a maintenance-window upgrade path that operators must know about.
- All `CREATE INDEX IF NOT EXISTS` (idempotent — re-runnable; matches 007/008/011 pattern).
- Numbered comment per index (`-- 1.`, `-- 2.`, …) explaining its query-shape purpose (matches 008's numbered-comment style).
- Index naming `<table>_<column>_<purpose>_idx` (`documents_content_markdown_trgm_idx`, `documents_folder_path_prefix_idx`, etc.).
- `public.folders` qualified for the folders indexes (consistency with the table create in 013).
- No `BEGIN`/`COMMIT`.

Do NOT:
- Use `CREATE INDEX CONCURRENTLY` (forbidden inside the migration runner's transaction; will fail with `cannot run inside a transaction block`).
- Re-enable pg_trgm here (it was enabled in migration 012 — duplication is harmless because of `IF NOT EXISTS` but the comment in plan 02's migration explicitly placed extension creation early to avoid dependency confusion; keep that boundary).
- Add the speculative composite `(scope, user_id, folder_path)` index (defer to Phase 4 per RESEARCH.md §4).
- Add indexes on document_chunks (chunks have their tsvector index from migration 008; Phase 4 doesn't grep chunks directly — it greps content_markdown which lives on documents).
- Use any operator class besides `gin_trgm_ops` (for GIN on TEXT) and `text_pattern_ops` (for btree prefix LIKE).
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -c "sql = open('migrations/016_search_indexes.sql', encoding='utf-8').read(); assert 'CREATE INDEX IF NOT EXISTS documents_content_markdown_trgm_idx' in sql; assert 'USING gin (content_markdown gin_trgm_ops)' in sql; assert 'CREATE INDEX IF NOT EXISTS documents_folder_path_trgm_idx' in sql; assert 'USING gin (folder_path gin_trgm_ops)' in sql; assert 'CREATE INDEX IF NOT EXISTS documents_folder_path_prefix_idx' in sql; assert '(folder_path text_pattern_ops)' in sql; assert 'CREATE INDEX IF NOT EXISTS folders_path_trgm_idx' in sql; assert 'USING gin (path gin_trgm_ops)' in sql; assert 'CREATE INDEX IF NOT EXISTS folders_path_prefix_idx' in sql; assert '(path text_pattern_ops)' in sql; assert sql.count('CREATE INDEX IF NOT EXISTS') == 5, f'expected 5 indexes, got {sql.count(\"CREATE INDEX IF NOT EXISTS\")}'; assert 'CONCURRENTLY' not in sql; assert 'CREATE EXTENSION' not in sql, 'pg_trgm is enabled in migration 012, not 016'; print(f'migration 016 structure OK: 5 indexes (3 trigram, 2 text_pattern_ops btree)')"</automated>
  </verify>
  <acceptance_criteria>
    - File `backend/migrations/016_search_indexes.sql` exists.
    - File starts with comment line `-- Phase 1 / Migration 016: search-acceleration indexes`.
    - `grep -c "CREATE INDEX IF NOT EXISTS documents_content_markdown_trgm_idx" backend/migrations/016_search_indexes.sql` returns 1.
    - `grep -c "USING gin (content_markdown gin_trgm_ops)" backend/migrations/016_search_indexes.sql` returns 1.
    - `grep -c "CREATE INDEX IF NOT EXISTS documents_folder_path_trgm_idx" backend/migrations/016_search_indexes.sql` returns 1.
    - `grep -c "USING gin (folder_path gin_trgm_ops)" backend/migrations/016_search_indexes.sql` returns 1.
    - `grep -c "CREATE INDEX IF NOT EXISTS documents_folder_path_prefix_idx" backend/migrations/016_search_indexes.sql` returns 1.
    - `grep -c "(folder_path text_pattern_ops)" backend/migrations/016_search_indexes.sql` returns 1.
    - `grep -c "CREATE INDEX IF NOT EXISTS folders_path_trgm_idx" backend/migrations/016_search_indexes.sql` returns 1.
    - `grep -c "USING gin (path gin_trgm_ops)" backend/migrations/016_search_indexes.sql` returns 1.
    - `grep -c "CREATE INDEX IF NOT EXISTS folders_path_prefix_idx" backend/migrations/016_search_indexes.sql` returns 1.
    - `grep -c "(path text_pattern_ops)" backend/migrations/016_search_indexes.sql` returns 1.
    - Total CREATE INDEX count is exactly 5: `grep -c "CREATE INDEX IF NOT EXISTS" backend/migrations/016_search_indexes.sql` returns 5.
    - `grep -c "CONCURRENTLY" backend/migrations/016_search_indexes.sql` returns 0 (forbidden inside transactions; documented in header).
    - `grep -c "CREATE EXTENSION" backend/migrations/016_search_indexes.sql` returns 0 (pg_trgm enabled in migration 012, not here).
    - File does NOT contain `document_chunks` outside of comments (chunks have their tsvector index from migration 008; this migration is documents/folders only).
    - File does NOT contain `composite` or `(scope, user_id, folder_path)` index — speculative composite is deferred to Phase 4 per RESEARCH.md §4.
    - File does NOT contain `BEGIN;` or `COMMIT;` at top level.
    - Python sanity check in `<verify>` exits 0 and prints "migration 016 structure OK: 5 indexes (3 trigram, 2 text_pattern_ops btree)".
  </acceptance_criteria>
  <done>
    Migration 016 SQL file written, idempotent, contains 5 search-acceleration indexes (3 GIN trigram on content_markdown / documents.folder_path / folders.path, 2 text_pattern_ops btree on documents.folder_path / folders.path). All plain CREATE INDEX (no CONCURRENTLY). Header documents the production-scale CONCURRENTLY upgrade path. No pg_trgm CREATE EXTENSION (already enabled in plan 02 / migration 012). Composite (scope, user_id, folder_path) deferred to Phase 4. Migration not yet applied.
  </done>
</task>

</tasks>

<verification>
Maps to .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-VALIDATION.md row "SCHEMA-05" (line ~47). Falsifiable assertions 36-38 from RESEARCH.md § Validation Architecture (Group 5: Indexes & perf — `EXPLAIN ANALYZE … content_markdown ILIKE` shows Bitmap Index Scan; `EXPLAIN ANALYZE … folder_path LIKE 'prefix/%'` shows Index Scan; `pg_extension` lists pg_trgm) — those run in plan 08 against the live DB after plan 07 applies the migration.

Static structural verification (this plan): the Python one-liner in `<automated>` validates all 5 indexes are present with the correct operator classes and confirms no CONCURRENTLY / no duplicate CREATE EXTENSION.
</verification>

<success_criteria>
- `backend/migrations/016_search_indexes.sql` exists with all 5 indexes (3 GIN trigram + 2 text_pattern_ops btree) targeting documents.content_markdown, documents.folder_path, folders.path.
- File is idempotent (CREATE INDEX IF NOT EXISTS).
- No CONCURRENTLY (runner constraint).
- No re-enable of pg_trgm extension (already in migration 012).
- No speculative composite index (deferred to Phase 4 per RESEARCH.md §4).
- Header documents the production-scale CONCURRENTLY upgrade path.
- Static structural check passes.
</success_criteria>

<output>
After completion, create `.planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-06-SUMMARY.md` recording: file created, line count, list of 5 indexes with their operator classes and target tools (e.g., "documents_content_markdown_trgm_idx (gin_trgm_ops) → Phase 4 grep"), the deliberate "plain CREATE INDEX not CONCURRENTLY" choice with one-line note about the production-scale upgrade path, and confirmation that the speculative composite index is deferred to Phase 4.
</output>
