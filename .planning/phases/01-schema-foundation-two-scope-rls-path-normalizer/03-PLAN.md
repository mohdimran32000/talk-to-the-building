---
phase: 01
plan: 03
type: execute
wave: 2
depends_on: [02]
files_modified:
  - backend/migrations/013_folders_table.sql
autonomous: true
requirements:
  - SCHEMA-04
must_haves:
  truths:
    - "public.folders table exists with columns id, scope, user_id, path, created_at"
    - "folders.user_id REFERENCES auth.users(id) ON DELETE CASCADE"
    - "folders has CHECK constraint folders_scope_user_id_consistency (same coupling as documents)"
    - "folders has CHECK constraint folders_path_canonical (same regex as documents)"
    - "folders has CHECK constraint enforcing scope IN ('user','global')"
    - "Unique expression index folders_scope_user_path_unique exists on (scope, COALESCE(user_id,'00000000-0000-0000-0000-000000000000'::uuid), path)"
    - "Index folders_scope_user_idx exists on (scope, user_id) for general listing"
    - "Row-level security is ENABLED on public.folders (policies created in migration 015)"
    - "GRANT SELECT, INSERT, UPDATE, DELETE on public.folders TO authenticated is set"
    - "Concurrent INSERT of (scope='user', user_id=X, path='/y') from two clients results in exactly one folders row (the second fails on the unique index) — Pitfall 10 mitigation"
  artifacts:
    - path: "backend/migrations/013_folders_table.sql"
      provides: "Thin folders side table for empty-folder tracking, with concurrency-safe unique expression index and RLS-enabled (policies in 015)"
      contains: "CREATE TABLE IF NOT EXISTS public.folders"
      contains_2: "folders_scope_user_path_unique"
      contains_3: "ALTER TABLE public.folders ENABLE ROW LEVEL SECURITY"
      min_lines: 30
  key_links:
    - from: "folders unique expression index"
      to: "Phase 3 INSERT ... ON CONFLICT DO NOTHING (concurrent upload safety)"
      via: "DB unique constraint is the bedrock; app-layer ON CONFLICT is the consumer"
      pattern: "folders_scope_user_path_unique"
    - from: "folders.scope + folders.user_id"
      to: "Migration 015 RLS policies on folders"
      via: "schema dependency"
      pattern: "scope = 'user' AND user_id"
---

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| App service layer (Phase 3) -> public.folders | Concurrent INSERTs from parallel uploads / drag-move operations cross here |
| Migration runner -> new public.folders table | Net-new table with no existing data; CASCADE chain to auth.users must be safe |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-1-03 | Tampering / Data Integrity (Concurrency) | public.folders | mitigate | Unique expression index `folders_scope_user_path_unique` on `(scope, COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid), path)` ensures concurrent INSERTs of the same (scope, user_id, path) produce exactly one row (the second fails). COALESCE sentinel is REQUIRED because Postgres treats NULLs as distinct in unique indexes by default — without it, infinite duplicate global rows would be allowed. (Pitfall 10 mitigation §1; ROADMAP success criterion implied by Phase 3's "exactly one folders row from 10 parallel uploads" test.) Phase 3 will pair with `INSERT ... ON CONFLICT DO NOTHING`. |
| T-1-01 (folders) | Tampering / Information Disclosure | public.folders | mitigate | (a) CHECK `folders_scope_user_id_consistency` couples scope/user_id (same shape as documents); (b) `ENABLE ROW LEVEL SECURITY` set here so the table is locked from authenticated users until migration 015's policies grant access. With RLS enabled and no policies, all reads/writes to `folders` from authenticated role are denied — fail-closed default. |
| T-1-02 (folders) | Tampering | public.folders.path | mitigate | CHECK `folders_path_canonical` enforces the same `^/$|^/[^/]+(/[^/]+)*$` regex as documents.folder_path. Same defense-in-depth shape. (Pitfall 4.) |
</threat_model>

<objective>
Write `backend/migrations/013_folders_table.sql` — creates the thin `public.folders` side table for first-class empty-folder tracking. Includes the COALESCE-based unique expression index that prevents concurrent-upload races (Pitfall 10), the same scope/user_id coupling and canonical-path CHECK constraints as documents, and `ENABLE ROW LEVEL SECURITY` (policies deferred to migration 015 so the full RLS catalog is reviewable in one file). No FK from documents.folder_path to folders.path (per ARCHITECTURE.md Pattern 2 — folders is sparse; documents reference folders by string path, not by FK). Phase 3's folder service will consume this table; Phase 1 just lays the schema.
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
@backend/migrations/005_profiles_and_settings.sql
@backend/migrations/012_folder_path_and_scope.sql

<interfaces>
<!-- New table contract that Phase 3 folder service will consume. -->

public.folders (NEW table created by this migration):
- id         UUID PRIMARY KEY DEFAULT gen_random_uuid()
- scope      TEXT NOT NULL CHECK (scope IN ('user','global'))
- user_id    UUID REFERENCES auth.users(id) ON DELETE CASCADE  -- NULLable for global rows
- path       TEXT NOT NULL  -- canonical form, validated by CHECK
- created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
- CONSTRAINT folders_scope_user_id_consistency
- CONSTRAINT folders_path_canonical
- UNIQUE INDEX (scope, COALESCE(user_id, '00..0'::uuid), path)  -- expression index, not table constraint

NO foreign key from documents.folder_path -> folders.path (intentional; per ARCHITECTURE.md Pattern 2).
RLS enabled but no policies (policies in migration 015).
</interfaces>
</context>

<tasks>

<task id="1-03-01" type="auto">
  <name>Task 1: Write migration 013 — folders table + unique expression index + RLS-enable</name>
  <files>backend/migrations/013_folders_table.sql</files>
  <read_first>
    - backend/migrations/003_byo_retrieval.sql (canonical CREATE TABLE pattern; lines 12-32 for table + indexes + ENABLE RLS shape)
    - backend/migrations/005_profiles_and_settings.sql (CREATE TABLE IF NOT EXISTS public.<name> form; lines 6-15 for `public.` schema qualification + GRANT pattern)
    - backend/migrations/012_folder_path_and_scope.sql (the file just written by plan 02 — confirms scope/user_id coupling CHECK shape; 013 mirrors it for folders)
    - .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-RESEARCH.md § "Migration 013 — Folders table + unique-expression index + RLS-enable" (lines ~552-602 — DEFINITIVE DDL skeleton; paste-ready) AND § Decisions §5 (lines ~328-368 — explains why expression index, why no FK from documents.folder_path)
    - .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-PATTERNS.md § "backend/migrations/013_folders_table.sql" (lines ~69-112 — confirms 003 + 005 are the analogs)
    - .planning/research/PITFALLS.md § Pitfall 10 (lines ~304-328 — concurrent upload race; explains the unique constraint requirement)
  </read_first>
  <action>
    Create `backend/migrations/013_folders_table.sql` with the EXACT SQL below (paste-ready from RESEARCH.md § Migration 013).

```sql
-- Phase 1 / Migration 013: folders table + unique expression index + RLS enable
-- Side table for first-class empty-folder tracking. Documents reference folders
-- ONLY by string path (no FK) per ARCHITECTURE.md Pattern 2 — folders is a sparse,
-- explicit-empty-only table. Most folders exist by inference from documents.folder_path.
-- RLS is enabled here; policies are added in migration 015 (kept together for review).

CREATE TABLE IF NOT EXISTS public.folders (
  id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  scope      TEXT        NOT NULL CHECK (scope IN ('user', 'global')),
  user_id    UUID        REFERENCES auth.users(id) ON DELETE CASCADE,
  path       TEXT        NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  -- Coupling: user_id required iff scope='user' (mirrors documents/document_chunks)
  CONSTRAINT folders_scope_user_id_consistency CHECK (
    (scope = 'user'   AND user_id IS NOT NULL)
    OR (scope = 'global' AND user_id IS NULL)
  ),

  -- Canonical-form path (mirrors documents.folder_path canonical regex)
  CONSTRAINT folders_path_canonical CHECK (
    path = '/' OR path ~ '^/[^/]+(/[^/]+)*$'
  )
);

-- ── Unique expression index with COALESCE sentinel ──
-- Postgres treats NULLs as distinct in unique indexes by default — without the
-- COALESCE, two global rows with the same path would both be allowed (NULL != NULL).
-- The all-zeros UUID sentinel forces NULL user_id to compare equal across rows.
-- Pitfall 10 mitigation — concurrent uploads to the same new path produce exactly one row.
-- (Table-level UNIQUE accepts only column lists, not expressions; CREATE UNIQUE INDEX is required.)
CREATE UNIQUE INDEX IF NOT EXISTS folders_scope_user_path_unique
  ON public.folders (
    scope,
    COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid),
    path
  );

-- General listing index for "all folders for this user/scope"
CREATE INDEX IF NOT EXISTS folders_scope_user_idx
  ON public.folders (scope, user_id);

-- ── Enable RLS ──
-- Policies land in migration 015 alongside the two-scope policies for documents
-- and document_chunks (kept together so the policy catalog is reviewable in one file).
-- Until 015 runs, RLS-enabled-no-policies = fail-closed default for the authenticated role.
ALTER TABLE public.folders ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.folders TO authenticated;
```

Conventions to honor (per .planning/phases/01-.../01-PATTERNS.md):
- Filename `013_folders_table.sql`.
- Header comment: "-- Phase 1 / Migration 013: folders table + unique expression index + RLS enable" + 2-3 line context.
- `public.folders` qualified (matches 005's `public.profiles` form, the most recent CREATE TABLE).
- `id UUID PRIMARY KEY DEFAULT gen_random_uuid()` (matches 003 line 13).
- `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()` (uppercase NOW() to match documents, even though 005 uses lowercase — 003's NOW() is the closer analog since folders sits next to documents).
- `ON DELETE CASCADE` on user_id (matches 003 line 14).
- `IF NOT EXISTS` on table, indexes (idempotent — re-runnable).
- `GRANT … TO authenticated` (matches 005's GRANT pattern, scoped to the authenticated role; service-role bypasses RLS regardless).
- No `BEGIN`/`COMMIT`.

Do NOT:
- Add CREATE POLICY statements (those are migration 015 — RESEARCH.md design choice for reviewability).
- Add a foreign key from `folders.path` to anything (documents.folder_path stays as plain TEXT per ARCHITECTURE.md Pattern 2).
- Pre-populate any rows (Phase 3 creates folders on demand).
- Add `text_pattern_ops` btree or trigram indexes on path (those are migration 016).
- Use `NULLS NOT DISTINCT` instead of COALESCE (RESEARCH.md §5 chose COALESCE for portability; do not deviate).
- Use `CREATE INDEX CONCURRENTLY`.
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -c "sql = open('migrations/013_folders_table.sql', encoding='utf-8').read(); assert 'CREATE TABLE IF NOT EXISTS public.folders' in sql; assert 'folders_scope_user_id_consistency' in sql; assert 'folders_path_canonical' in sql; assert 'folders_scope_user_path_unique' in sql; assert 'COALESCE(user_id' in sql; assert '00000000-0000-0000-0000-000000000000' in sql; assert 'ENABLE ROW LEVEL SECURITY' in sql; assert 'GRANT SELECT, INSERT, UPDATE, DELETE ON public.folders TO authenticated' in sql; assert 'CREATE POLICY' not in sql; assert 'CONCURRENTLY' not in sql; assert 'NULLS NOT DISTINCT' not in sql; print('migration 013 structure OK')"</automated>
  </verify>
  <acceptance_criteria>
    - File `backend/migrations/013_folders_table.sql` exists.
    - File starts with comment line `-- Phase 1 / Migration 013: folders table + unique expression index + RLS enable`.
    - `grep -c "CREATE TABLE IF NOT EXISTS public.folders" backend/migrations/013_folders_table.sql` returns 1.
    - `grep -c "REFERENCES auth.users(id) ON DELETE CASCADE" backend/migrations/013_folders_table.sql` returns 1.
    - `grep -c "folders_scope_user_id_consistency" backend/migrations/013_folders_table.sql` returns 1.
    - `grep -c "folders_path_canonical" backend/migrations/013_folders_table.sql` returns 1.
    - `grep -c "CREATE UNIQUE INDEX IF NOT EXISTS folders_scope_user_path_unique" backend/migrations/013_folders_table.sql` returns 1.
    - `grep -c "COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid)" backend/migrations/013_folders_table.sql` returns 1.
    - `grep -c "CREATE INDEX IF NOT EXISTS folders_scope_user_idx" backend/migrations/013_folders_table.sql` returns 1.
    - `grep -c "ALTER TABLE public.folders ENABLE ROW LEVEL SECURITY" backend/migrations/013_folders_table.sql` returns 1.
    - `grep -c "GRANT SELECT, INSERT, UPDATE, DELETE ON public.folders TO authenticated" backend/migrations/013_folders_table.sql` returns 1.
    - File contains the exact regex literal `'^/[^/]+(/[^/]+)*$'` (path canonical CHECK).
    - File does NOT contain `CREATE POLICY` (those are migration 015).
    - File does NOT contain `FOREIGN KEY` referencing documents (no FK from folders to documents per ARCHITECTURE.md Pattern 2).
    - File does NOT contain `CONCURRENTLY`.
    - File does NOT contain `NULLS NOT DISTINCT` (per RESEARCH.md §5 portability choice).
    - File does NOT contain `BEGIN;` or `COMMIT;` at top level.
    - Python sanity check in `<verify>` exits 0 and prints "migration 013 structure OK".
  </acceptance_criteria>
  <done>
    Migration 013 SQL file written, idempotent, creates the public.folders table with both CHECK constraints (scope/user_id coupling, canonical path), the COALESCE-based unique expression index (Pitfall 10 concurrent-upload race mitigation), the listing index, ENABLE ROW LEVEL SECURITY (policies deferred to 015), and GRANT to authenticated. No FK to documents. No policies. Migration not yet applied.
  </done>
</task>

</tasks>

<verification>
Maps to .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-VALIDATION.md row "SCHEMA-04" (line ~46). Falsifiable assertion 35 from RESEARCH.md § Validation Architecture (Group 4: same constraints exist on folders) and Group 1 line 18-19 (folders unique-index race rejection) — those run in plan 08 against the live DB after plan 07.

Static structural verification (this plan): Python one-liner in `<automated>` validates required DDL primitives are present.
</verification>

<success_criteria>
- `backend/migrations/013_folders_table.sql` exists with the public.folders table, both CHECK constraints, the COALESCE-based unique expression index, the listing index, ENABLE RLS, and GRANT to authenticated.
- File is idempotent (re-runnable).
- No CREATE POLICY (deferred to 015).
- No FK from folders to documents (per ARCHITECTURE.md Pattern 2).
- Static structural check passes.
</success_criteria>

<output>
After completion, create `.planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-03-SUMMARY.md` recording: file created, line count, list of constraints/indexes added, the deliberate non-FK design choice (one-line: "folders.path is referenced by documents.folder_path as plain TEXT — no FK per ARCHITECTURE.md Pattern 2"), and confirmation that policies are deferred to plan 05 (migration 015).
</output>
