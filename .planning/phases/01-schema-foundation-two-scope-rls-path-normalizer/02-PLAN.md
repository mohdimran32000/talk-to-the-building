---
phase: 01
plan: 02
type: execute
wave: 1
depends_on: []
files_modified:
  - backend/migrations/012_folder_path_and_scope.sql
autonomous: true
requirements:
  - SCHEMA-01
  - SCHEMA-02
must_haves:
  truths:
    - "documents table has columns folder_path TEXT NOT NULL DEFAULT '/' and scope TEXT NOT NULL DEFAULT 'user'"
    - "documents.user_id is NULLABLE (was NOT NULL before migration 012)"
    - "documents has CHECK constraint documents_scope_user_id_consistency"
    - "documents has CHECK constraint documents_folder_path_canonical (regex ^/$|^/[^/]+(/[^/]+)*$)"
    - "documents has CHECK constraint enforcing scope IN ('user','global')"
    - "Old constraint documents_user_filename_unique is dropped"
    - "New unique expression index documents_scope_user_path_filename_unique covers (scope, COALESCE(user_id,'00000000-0000-0000-0000-000000000000'::uuid), folder_path, file_name)"
    - "document_chunks table has scope TEXT NOT NULL DEFAULT 'user' column with same CHECK constraints (matching documents)"
    - "document_chunks.user_id is NULLABLE"
    - "pg_trgm extension is enabled (CREATE EXTENSION IF NOT EXISTS pg_trgm executed)"
    - "Existing Episode 1 documents are queryable at folder_path='/', scope='user' immediately after migration (no manual data movement, no row rewrite)"
  artifacts:
    - path: "backend/migrations/012_folder_path_and_scope.sql"
      provides: "documents/document_chunks scope+folder_path columns, CHECK coupling, scope-aware unique index, pg_trgm extension"
      contains: "ALTER TABLE documents ADD COLUMN IF NOT EXISTS folder_path"
      contains_2: "ALTER TABLE documents ADD COLUMN IF NOT EXISTS scope"
      contains_3: "CREATE EXTENSION IF NOT EXISTS pg_trgm"
      contains_4: "documents_scope_user_id_consistency"
      contains_5: "documents_folder_path_canonical"
      min_lines: 50
  key_links:
    - from: "documents.scope + documents.user_id"
      to: "Migration 015 RLS policies (require both columns to exist)"
      via: "schema dependency — 015 references both"
      pattern: "scope = 'user' AND user_id"
    - from: "pg_trgm extension"
      to: "Migration 016 (CREATE INDEX ... USING gin (col gin_trgm_ops))"
      via: "extension must be enabled before index referencing gin_trgm_ops"
      pattern: "gin_trgm_ops"
    - from: "documents.folder_path canonical CHECK"
      to: "folder_service.normalize_path (plan 01)"
      via: "DB defense in depth for the Python chokepoint"
      pattern: "^/\\$|^/\\[\\^/\\]\\+"
---

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| App service layer -> Postgres documents/document_chunks | Untrusted scope/user_id/folder_path values cross here from API requests |
| Migration runner -> documents table | Schema changes must preserve existing Episode 1 data integrity (no row rewrite, no orphaned constraints) |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-1-01 (foundation) | Tampering / Information Disclosure | documents.scope + documents.user_id | mitigate | CHECK constraint `documents_scope_user_id_consistency` couples scope to user_id presence: scope='user' REQUIRES user_id NOT NULL; scope='global' REQUIRES user_id IS NULL. This is the schema-layer foundation for RLS-02 (admin-only global writes) and prevents the "orphan-leak" failure where a NULL user_id on a user-scope row would bypass user-isolation policies. (Pitfall 1 mitigation §3.) Same CHECK on document_chunks. |
| T-1-02 (DB layer) | Tampering | documents.folder_path | mitigate | CHECK constraint `documents_folder_path_canonical` rejects any folder_path not matching `^/$|^/[^/]+(/[^/]+)*$` at INSERT time. Rejects trailing slash, double slashes, empty strings, backslashes. Defense in depth for the Python `normalize_path` chokepoint (plan 01). (Pitfall 4 mitigation §2.) |
| T-1-04 (foundation) | Performance / Information Disclosure | documents (search base) | mitigate | `CREATE EXTENSION IF NOT EXISTS pg_trgm` is enabled here (early) so migration 016 can build the GIN trigram index without dependency-ordering surprises. Without this index, Phase 4 grep degrades to Seq Scan (Pitfall 3 — grep perf collapse RANK 4 priority for this phase). |
| T-1-Aux | Data Integrity | documents (uniqueness) | mitigate | Old constraint `documents_user_filename_unique` from migration 006 is dropped and replaced with a scope-aware expression index `(scope, COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid), folder_path, file_name)`. COALESCE sentinel handles NULLable user_id for global rows (Pitfall 10 — concurrent upload race precondition). |
</threat_model>

<objective>
Write `backend/migrations/012_folder_path_and_scope.sql` — the foundation migration that adds the two new axes (`folder_path`, `scope`) to `documents` and `document_chunks`, makes `user_id` NULLABLE on both, adds the scope/user_id coupling CHECK constraint (T-1-01 schema foundation), adds the canonical-form CHECK on `folder_path` (T-1-02 defense in depth), drops the old single-axis unique constraint and replaces it with a scope-aware expression index, and enables `pg_trgm` extension so migration 016 can reference `gin_trgm_ops`. This migration runs first in lexical order (`run_migrations.py` uses `sorted()`) and is the foundation that 013/014/015/016 all build on. No RLS changes (those are 015). No new tables (013). No new indexes besides the unique replacement (016).
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

@backend/migrations/003_byo_retrieval.sql
@backend/migrations/006_record_manager.sql
@backend/migrations/007_document_metadata.sql
@backend/migrations/008_hybrid_search.sql
@backend/scripts/run_migrations.py

<interfaces>
<!-- Existing schema this migration modifies. Executor must see current state before mutating. -->

documents table (post-migration 003 + 006 + 007):
- id UUID PRIMARY KEY DEFAULT gen_random_uuid()
- user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE  -- migration 012 will DROP NOT NULL
- file_name TEXT NOT NULL
- file_size INTEGER, mime_type TEXT, status TEXT, error_message TEXT
- created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
- updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
- content_hash TEXT  (from 006)
- metadata JSONB    (from 007)
- CONSTRAINT documents_user_filename_unique UNIQUE (user_id, file_name)  -- migration 012 will DROP this

document_chunks table (post-migration 003 + 006 + 008):
- id UUID PRIMARY KEY
- document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE
- user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE  -- migration 012 will DROP NOT NULL
- chunk_index INTEGER, content TEXT, content_hash TEXT (from 006)
- embedding vector(...), tsv tsvector (from 008)

Migration runner contract (backend/scripts/run_migrations.py:38-58):
- Each .sql file runs in its own transaction (autocommit=False)
- conn.commit() per file on success; conn.rollback() on failure
- Files run in `sorted(MIGRATIONS_DIR.glob("*.sql"))` order — lexical
- CREATE INDEX CONCURRENTLY is FORBIDDEN inside transactions — use plain CREATE INDEX
- All DDL must be idempotent (IF NOT EXISTS / IF EXISTS / CREATE OR REPLACE) for re-run safety
</interfaces>
</context>

<tasks>

<task id="1-02-01" type="auto">
  <name>Task 1: Write migration 012 — folder_path + scope columns + pg_trgm extension</name>
  <files>backend/migrations/012_folder_path_and_scope.sql</files>
  <read_first>
    - backend/migrations/006_record_manager.sql (closest analog — adds columns to documents AND creates the UNIQUE constraint that 012 must DROP; shows ALTER TABLE ADD COLUMN pattern)
    - backend/migrations/007_document_metadata.sql (canonical "ALTER TABLE ADD COLUMN IF NOT EXISTS" idempotent shape)
    - backend/migrations/008_hybrid_search.sql (header comment style + CREATE EXTENSION pattern)
    - backend/migrations/003_byo_retrieval.sql (existing documents/document_chunks schema and the `status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (...))` enum-via-CHECK pattern that 012 mirrors for `scope`)
    - backend/scripts/run_migrations.py (lines 38-58 — confirms each migration runs in a transaction; constrains us to plain `CREATE INDEX`, no `CONCURRENTLY`)
    - .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-RESEARCH.md § "Migration 012 — Folder path + scope columns + pg_trgm enable" (lines ~484-548 — DEFINITIVE DDL skeleton; paste-ready)
    - .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-PATTERNS.md § "backend/migrations/012_folder_path_and_scope.sql" (lines ~27-66 — confirms 006 + 008 are the analogs; documents the IF NOT EXISTS / IF EXISTS conventions and the "no BEGIN/COMMIT" rule)
    - .planning/research/PITFALLS.md § Pitfall 1 (RLS scope-leak — explains WHY the scope/user_id coupling CHECK is non-negotiable) and § Pitfall 4 (canonical regex rationale) and § Pitfall 10 (why scope-aware unique replaces user_id+file_name unique)
  </read_first>
  <action>
    Create `backend/migrations/012_folder_path_and_scope.sql` with the EXACT SQL below (paste-ready from RESEARCH.md § Migration 012). The runner will execute it in a single transaction; rollback on any failure is automatic.

```sql
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
```

Conventions to honor (per .planning/phases/01-.../01-PATTERNS.md):
- Filename `012_folder_path_and_scope.sql` (zero-padded 3 digits, snake_case, matches existing 001-011 sequence).
- Header comment: single-line "-- Phase 1 / Migration 012: <purpose>" followed by 1-3 lines of context. Matches 006/008 style.
- All DDL is idempotent: `IF NOT EXISTS` on ALTER TABLE ADD COLUMN, `IF NOT EXISTS` on CREATE EXTENSION, `IF EXISTS` on DROP CONSTRAINT, `DROP CONSTRAINT IF EXISTS … ; ADD CONSTRAINT …` for CHECK constraints (Postgres has no `ADD CONSTRAINT IF NOT EXISTS`, so the drop-then-add pattern is the canonical idempotent shape).
- No `BEGIN`/`COMMIT` — the runner wraps each file in a transaction (run_migrations.py:39-52).
- Two-space indent inside multi-line `ALTER TABLE` and `CREATE INDEX` statements.
- Index name matches `<table>_<purpose>_unique` convention from 006: `documents_scope_user_path_filename_unique`.

Do NOT:
- Use `CREATE INDEX CONCURRENTLY` (forbidden inside transactions; runner enforces this — RESEARCH.md §8).
- Add RLS policy changes (those are migration 015 — keep policies in one reviewable file).
- Create the `folders` table (that's migration 013).
- Add the `content_markdown` column (that's migration 014).
- Add the trigram or text_pattern_ops indexes (those are migration 016).
- Add `folder_path` to `document_chunks` (per RESEARCH.md Open Question §8: chunks get scope only; join back to documents for path).
- Lowercase or modify any string values; respect Postgres case-sensitivity.

After writing the file, do NOT execute the migration — that is plan 07 ([BLOCKING] schema push). This task only writes the .sql file.
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -c "import re; sql = open('migrations/012_folder_path_and_scope.sql', encoding='utf-8').read(); assert 'CREATE EXTENSION IF NOT EXISTS pg_trgm' in sql; assert 'ADD COLUMN IF NOT EXISTS folder_path' in sql; assert 'ADD COLUMN IF NOT EXISTS scope' in sql; assert 'documents_scope_user_id_consistency' in sql; assert 'documents_folder_path_canonical' in sql; assert 'DROP CONSTRAINT IF EXISTS documents_user_filename_unique' in sql; assert 'documents_scope_user_path_filename_unique' in sql; assert 'CONCURRENTLY' not in sql; assert 'BEGIN;' not in sql.upper().replace('-- ', ''); print('migration 012 structure OK')"</automated>
  </verify>
  <acceptance_criteria>
    - File `backend/migrations/012_folder_path_and_scope.sql` exists.
    - File starts with the comment line `-- Phase 1 / Migration 012: folder_path + scope columns + pg_trgm extension` (matches 006/008 header convention).
    - `grep -c "CREATE EXTENSION IF NOT EXISTS pg_trgm" backend/migrations/012_folder_path_and_scope.sql` returns 1.
    - `grep -c "ADD COLUMN IF NOT EXISTS folder_path TEXT NOT NULL DEFAULT '/'" backend/migrations/012_folder_path_and_scope.sql` returns 1.
    - `grep -c "ADD COLUMN IF NOT EXISTS scope" backend/migrations/012_folder_path_and_scope.sql` returns at least 2 (documents + document_chunks).
    - `grep -c "ALTER COLUMN user_id DROP NOT NULL" backend/migrations/012_folder_path_and_scope.sql` returns 2 (documents + document_chunks).
    - `grep -c "documents_scope_user_id_consistency" backend/migrations/012_folder_path_and_scope.sql` returns at least 2 (DROP + ADD).
    - `grep -c "documents_folder_path_canonical" backend/migrations/012_folder_path_and_scope.sql` returns at least 2.
    - `grep -c "DROP CONSTRAINT IF EXISTS documents_user_filename_unique" backend/migrations/012_folder_path_and_scope.sql` returns 1.
    - `grep -c "documents_scope_user_path_filename_unique" backend/migrations/012_folder_path_and_scope.sql` returns 1.
    - `grep -c "COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid)" backend/migrations/012_folder_path_and_scope.sql` returns 1.
    - `grep -c "document_chunks_scope_user_id_consistency" backend/migrations/012_folder_path_and_scope.sql` returns at least 2.
    - `grep -i "concurrently" backend/migrations/012_folder_path_and_scope.sql` returns no matches (CONCURRENTLY forbidden inside transactions).
    - `grep -E "^(BEGIN|COMMIT|ROLLBACK);" backend/migrations/012_folder_path_and_scope.sql` returns no matches (runner manages transactions).
    - File contains the exact regex literal `'^/[^/]+(/[^/]+)*$'` (folder_path canonical CHECK).
    - File does NOT contain the substring `content_markdown` (that is migration 014's scope).
    - File does NOT contain `CREATE TABLE` (folders table is migration 013).
    - File does NOT contain `CREATE POLICY` (RLS is migration 015).
    - The Python sanity check in `<verify>` exits 0 and prints "migration 012 structure OK".
  </acceptance_criteria>
  <done>
    Migration 012 SQL file written, idempotent (re-runnable), structurally valid (no BEGIN/COMMIT, no CONCURRENTLY), covers SCHEMA-01 (folder_path + canonical CHECK) and SCHEMA-02 (scope + coupling CHECK) for both documents and document_chunks, drops the old single-axis unique constraint and replaces with scope-aware expression index, enables pg_trgm. Migration is NOT yet applied to the database (plan 07 handles the push).
  </done>
</task>

</tasks>

<verification>
Maps to .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-VALIDATION.md rows "SCHEMA-01" and "SCHEMA-02" (lines ~43-44). Falsifiable assertions 29–33 from RESEARCH.md § Validation Architecture (Group 4: CHECK constraints — `folder_path = 'projects'` rejected, `folder_path = '/projects/'` rejected, `(scope='user', user_id=NULL)` rejected, `(scope='global', user_id=<uuid>)` rejected) — these run against the LIVE database after plan 07 applies the migration; they live in plan 08's test_two_scope_rls.py.

Static structural verification (this plan): The Python one-liner in `<automated>` validates the file contains all required DDL primitives. Live DB validation occurs in plan 08.
</verification>

<success_criteria>
- `backend/migrations/012_folder_path_and_scope.sql` exists with all required DDL (folder_path + scope columns on documents + document_chunks, scope/user_id coupling CHECK, canonical-form CHECK, dropped old unique, new scope-aware unique expression index, pg_trgm extension enable).
- File is idempotent (re-runnable without error — every DDL uses IF NOT EXISTS / IF EXISTS / DROP-before-ADD).
- File contains no transactional control statements (no BEGIN/COMMIT/ROLLBACK at top level).
- File contains no CREATE INDEX CONCURRENTLY.
- Static structural check passes (`grep` assertions in acceptance criteria all hold).
</success_criteria>

<output>
After completion, create `.planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-02-SUMMARY.md` recording: file created, line count, list of DDL primitives included (columns added, CHECK constraints added, constraint dropped, expression index created, extension enabled), and a one-line note that the migration is queued for plan 07's push.
</output>
