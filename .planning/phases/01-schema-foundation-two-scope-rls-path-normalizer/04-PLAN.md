---
phase: 01
plan: 04
type: execute
wave: 2
depends_on: [02]
files_modified:
  - backend/migrations/014_content_markdown_column.sql
autonomous: true
requirements:
  - SCHEMA-03
must_haves:
  truths:
    - "documents has column content_markdown TEXT (nullable)"
    - "documents has column content_markdown_status TEXT NOT NULL DEFAULT 'pending'"
    - "documents has CHECK constraint enforcing content_markdown_status IN ('pending','ready','failed','requires_user_reupload') (canonical 4-element set per REQUIREMENTS.md SCHEMA-03)"
    - "Existing Episode 1 documents get content_markdown_status='pending' automatically (column added with default — no row rewrite, metadata-only change for PG11+)"
    - "Existing Episode 1 documents have content_markdown=NULL (nullable column — backfill is Phase 2 scope, not Phase 1)"
    - "Partial index documents_content_markdown_status_idx exists on (content_markdown_status) WHERE content_markdown_status <> 'ready' (for Phase 2 backfill scan efficiency)"
  artifacts:
    - path: "backend/migrations/014_content_markdown_column.sql"
      provides: "content_markdown TEXT (nullable) and content_markdown_status TEXT (NOT NULL DEFAULT 'pending') columns on documents, plus partial index for backfill scan"
      contains: "ADD COLUMN IF NOT EXISTS content_markdown"
      contains_2: "ADD COLUMN IF NOT EXISTS content_markdown_status"
      contains_3: "CHECK (content_markdown_status IN ('pending', 'ready', 'failed', 'requires_user_reupload'))"
      min_lines: 20
  key_links:
    - from: "documents.content_markdown column"
      to: "Migration 016 GIN trigram index (CREATE INDEX … USING gin (content_markdown gin_trgm_ops))"
      via: "schema dependency — 016 references this column"
      pattern: "content_markdown gin_trgm_ops"
    - from: "documents.content_markdown_status column"
      to: "Phase 2 backfill_content_markdown.py + Phase 4 grep/read_document tools"
      via: "Phase 2 flips 'pending' to 'ready'/'failed'/'requires_user_reupload' after Docling re-run; Phase 4 tools surface pending status instead of empty matches"
      pattern: "content_markdown_status"
---

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Phase 2 backfill script -> documents.content_markdown_status | Backfill script writes 'ready'/'failed'/'requires_user_reupload' transitions after Docling re-run; CHECK enforces enum vocabulary |
| Phase 4 grep/read_document tools -> documents.content_markdown | Tools must surface NULL/'pending' status explicitly rather than silently skip rows (Pitfall 6) |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-1-Aux | Tampering / Data Integrity | documents.content_markdown_status | mitigate | TEXT + CHECK constraint locks the status vocabulary to the canonical 4-element set per REQUIREMENTS.md SCHEMA-03: `'pending'`, `'ready'`, `'failed'`, `'requires_user_reupload'`. NOT an ENUM type (RESEARCH.md §2: `ALTER TYPE` is painful in migrations; TEXT+CHECK is identically safe and easier to evolve). Default 'pending' on existing rows is a metadata-only change for PG11+ (no full table rewrite; verified per RESEARCH.md §6 backfill safety). |
| T-1-Aux-2 | Information Disclosure / Silent Failure | content_markdown column rollout | accept | Existing Episode 1 documents get `content_markdown=NULL` after this migration. This is INTENTIONAL — Phase 2's backfill_content_markdown.py re-runs Docling against the original Storage blob to populate it. Phase 4's `grep` and `read_document` are GATED on Phase 2 backfill completion (per ROADMAP critical-path: Phase 2 blocks Phase 4 tools). The partial index `WHERE content_markdown_status <> 'ready'` is added here to make Phase 2's backfill scan O(rows-needing-backfill) instead of O(all-documents). Pitfall 6 mitigation is Phase 2's responsibility, not Phase 1's; Phase 1 only ensures the column + status enum exist with the correct vocabulary. |
</threat_model>

<objective>
Write `backend/migrations/014_content_markdown_column.sql` — adds `documents.content_markdown TEXT` (nullable) and `documents.content_markdown_status TEXT NOT NULL DEFAULT 'pending'` with a CHECK enforcing the canonical 4-element status vocabulary per REQUIREMENTS.md SCHEMA-03 (`'pending' | 'ready' | 'failed' | 'requires_user_reupload'`). Adds a partial index on the status column scoped to non-'ready' rows, which makes Phase 2's backfill scan efficient. No backfill data movement (Phase 2's job). No GIN trigram index on content_markdown (migration 016's job). Existing rows get 'pending' automatically via DEFAULT (metadata-only change, no row rewrite per Postgres 11+ semantics).
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
@.planning/REQUIREMENTS.md
@CLAUDE.md

@backend/migrations/003_byo_retrieval.sql
@backend/migrations/007_document_metadata.sql
@backend/migrations/012_folder_path_and_scope.sql

<interfaces>
<!-- Schema this migration adds. Phase 2 (backfill) and Phase 4 (grep/read_document) consume these. -->

documents (additions only — preserves all columns from 003/006/007/012):
+ content_markdown        TEXT NULL              -- Backfill in Phase 2
+ content_markdown_status TEXT NOT NULL DEFAULT 'pending'
                          CHECK (content_markdown_status IN
                                 ('pending', 'ready', 'failed', 'requires_user_reupload'))

Status transitions (Phase 2's responsibility):
  'pending'                 -- new column default; existing Episode 1 docs start here
    └─> 'ready'             -- Phase 2 backfill: Docling re-run succeeded, content_markdown populated
    └─> 'failed'            -- Phase 2 backfill: Docling raised
    └─> 'requires_user_reupload' -- Phase 2 backfill: source Storage blob is GC'd

Vocabulary lock per REQUIREMENTS.md SCHEMA-03: exactly these 4 values.
NOT 'ok' (ROADMAP additional context says 'ok'; per RESEARCH.md §2 this is wrong — REQUIREMENTS is canonical).
NOT 'processing' (that exists for documents.status, not content_markdown_status).
</interfaces>
</context>

<tasks>

<task id="1-04-01" type="auto">
  <name>Task 1: Write migration 014 — content_markdown column + status enum + partial index</name>
  <files>backend/migrations/014_content_markdown_column.sql</files>
  <read_first>
    - backend/migrations/007_document_metadata.sql (canonical "ALTER TABLE ADD COLUMN IF NOT EXISTS" + GIN-index pattern; lines 4-6 are the closest-shape analog for "add nullable column to documents")
    - backend/migrations/003_byo_retrieval.sql (lines 18-19 — canonical TEXT + CHECK enum pattern: `status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'ready', 'failed'))` — 014's content_markdown_status mirrors this exact shape, just with a different value list)
    - backend/migrations/012_folder_path_and_scope.sql (the just-written file from plan 02 — confirms the multi-column ALTER TABLE comma-separated style and IF NOT EXISTS conventions for this phase)
    - .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-RESEARCH.md § "Migration 014 — content_markdown column + status enum" (lines ~606-636 — DEFINITIVE DDL skeleton; paste-ready) AND § Decisions §2 (lines ~186-205 — explains why TEXT+CHECK over ENUM, why 'ready' not 'ok', why default 'pending')
    - .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-PATTERNS.md § "backend/migrations/014_content_markdown_column.sql" (lines ~115-149 — confirms 007 + 003 are the analogs; documents the partial-index pattern as a new convention call-out)
    - .planning/REQUIREMENTS.md § SCHEMA-03 (line 12 — canonical 4-element value list — DO NOT deviate)
    - .planning/research/PITFALLS.md § Pitfall 6 (lines ~165-196 — explains WHY 'requires_user_reupload' is mandatory in v1 and why backfill is a dedicated Phase 2)
  </read_first>
  <action>
    Create `backend/migrations/014_content_markdown_column.sql` with the EXACT SQL below (paste-ready from RESEARCH.md § Migration 014).

```sql
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
```

Conventions to honor (per .planning/phases/01-.../01-PATTERNS.md):
- Filename `014_content_markdown_column.sql`.
- Header comment: "-- Phase 1 / Migration 014: <purpose>" + 4-6 lines of context (this migration warrants more context because the partial-index + enum-via-CHECK choices are new conventions for this codebase — call them out so reviewers know the intent).
- Multi-column ALTER TABLE: one statement with comma-separated `ADD COLUMN IF NOT EXISTS` clauses (cleaner; matches the multi-column style in plan 02's migration 012).
- CHECK constraint formatted with one value per line (readability — matches the style implied by RESEARCH.md skeleton; the existing `status TEXT … CHECK (status IN ('pending', 'processing', 'ready', 'failed'))` in 003 is single-line because there are only 4 values, but 014's call-out comment justifies the multi-line form).
- Vocabulary EXACTLY: `'pending'`, `'ready'`, `'failed'`, `'requires_user_reupload'`. **Do not include `'ok'` (ROADMAP additional context is wrong; REQUIREMENTS.md is canonical) or `'processing'` (that's for `documents.status`, a different column).**
- Default `'pending'` on the status column.
- Partial index uses `WHERE content_markdown_status <> 'ready'` filter (per RESEARCH.md skeleton).
- All DDL idempotent (`IF NOT EXISTS`).
- No `BEGIN`/`COMMIT`.

Do NOT:
- Add a GIN trigram index on content_markdown (that's migration 016's job — keeps that file focused on search-acceleration indexes).
- Backfill data (set content_markdown values for existing rows). Phase 2's backfill_content_markdown.py is the dedicated mechanism.
- Use an ENUM TYPE (`CREATE TYPE markdown_status AS ENUM (…)`). RESEARCH.md §2 explicitly chose TEXT + CHECK over ENUM.
- Touch document_chunks (content_markdown is a documents-only column).
- Use `CONCURRENTLY`.
- Make content_markdown NOT NULL (would block the migration on existing rows; backfill is Phase 2's deferred work).
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -c "sql = open('migrations/014_content_markdown_column.sql', encoding='utf-8').read(); assert 'ADD COLUMN IF NOT EXISTS content_markdown' in sql; assert 'ADD COLUMN IF NOT EXISTS content_markdown_status' in sql; assert \"DEFAULT 'pending'\" in sql; assert \"'pending'\" in sql and \"'ready'\" in sql and \"'failed'\" in sql and \"'requires_user_reupload'\" in sql; assert \"'ok'\" not in sql; assert \"'processing'\" not in sql; assert 'CREATE TYPE' not in sql.upper(); assert 'documents_content_markdown_status_idx' in sql; assert 'CONCURRENTLY' not in sql; assert 'document_chunks' not in sql.replace('-- ', ''); print('migration 014 structure OK')"</automated>
  </verify>
  <acceptance_criteria>
    - File `backend/migrations/014_content_markdown_column.sql` exists.
    - File starts with comment line `-- Phase 1 / Migration 014: content_markdown column + status enum + backfill-scan index`.
    - `grep -c "ADD COLUMN IF NOT EXISTS content_markdown" backend/migrations/014_content_markdown_column.sql` returns at least 2 (content_markdown + content_markdown_status).
    - `grep -c "TEXT NOT NULL DEFAULT 'pending'" backend/migrations/014_content_markdown_column.sql` returns 1.
    - `grep -c "'pending'" backend/migrations/014_content_markdown_column.sql` returns at least 2 (default + CHECK list).
    - `grep -c "'ready'" backend/migrations/014_content_markdown_column.sql` returns at least 1.
    - `grep -c "'failed'" backend/migrations/014_content_markdown_column.sql` returns at least 1.
    - `grep -c "'requires_user_reupload'" backend/migrations/014_content_markdown_column.sql` returns at least 1.
    - `grep -c "'ok'" backend/migrations/014_content_markdown_column.sql` returns 0 (canonical value is 'ready', not 'ok'; ROADMAP additional context is wrong, REQUIREMENTS.md is canonical).
    - `grep -c "'processing'" backend/migrations/014_content_markdown_column.sql` returns 0 (that's documents.status vocabulary, not content_markdown_status).
    - `grep -c "CREATE INDEX IF NOT EXISTS documents_content_markdown_status_idx" backend/migrations/014_content_markdown_column.sql` returns 1.
    - `grep -c "WHERE content_markdown_status <> 'ready'" backend/migrations/014_content_markdown_column.sql` returns 1.
    - `grep -iE "CREATE TYPE.*ENUM" backend/migrations/014_content_markdown_column.sql` returns no matches (TEXT+CHECK, not ENUM).
    - `grep -c "CONCURRENTLY" backend/migrations/014_content_markdown_column.sql` returns 0.
    - File does NOT contain `document_chunks` outside of comments (chunks are not modified by this migration).
    - File does NOT contain `BEGIN;` or `COMMIT;` at top level.
    - Python sanity check in `<verify>` exits 0 and prints "migration 014 structure OK".
  </acceptance_criteria>
  <done>
    Migration 014 SQL file written, idempotent, adds content_markdown TEXT (nullable) and content_markdown_status TEXT NOT NULL DEFAULT 'pending' with the canonical 4-element CHECK vocabulary per REQUIREMENTS.md SCHEMA-03, plus the partial index for Phase 2 backfill scan efficiency. No data backfill (Phase 2's job). No GIN index on content_markdown (Phase 1 plan 06 / migration 016's job). Migration not yet applied.
  </done>
</task>

</tasks>

<verification>
Maps to .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-VALIDATION.md row "SCHEMA-03" (line ~45). Falsifiable assertion 34 from RESEARCH.md § Validation Architecture (Group 4: `INSERT … content_markdown_status='processing'` rejected because not in enum) — runs in plan 08 against the live DB after plan 07.

Static structural verification (this plan): Python one-liner in `<automated>` validates required DDL primitives are present and confirms the wrong-vocabulary values ('ok', 'processing') are absent.
</verification>

<success_criteria>
- `backend/migrations/014_content_markdown_column.sql` exists with content_markdown + content_markdown_status columns, the CHECK enforcing the canonical 4-value enum, and the partial backfill-scan index.
- File is idempotent.
- Status vocabulary is exactly `'pending'`, `'ready'`, `'failed'`, `'requires_user_reupload'` — no 'ok', no 'processing'.
- Default is `'pending'` so existing Episode 1 docs migrate without manual data movement (ROADMAP success criterion 4 alignment for the markdown column rollout).
</success_criteria>

<output>
After completion, create `.planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-04-SUMMARY.md` recording: file created, line count, the canonical 4-element status vocabulary (call it out so Phase 2 has a clear reference), the deliberate "no backfill data movement in Phase 1" boundary (Phase 2 owns that), and confirmation that the GIN trigram index on content_markdown is plan 06 / migration 016's responsibility.
</output>
