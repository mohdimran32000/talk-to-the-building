---
phase: 01-schema-foundation-two-scope-rls-path-normalizer
plan: 02
subsystem: backend-migrations
tags: [postgres, ddl, schema-evolution, rls-foundation, check-constraints, pg_trgm]

# Dependency graph
requires:
  - phase: 01
    plan: 01
    provides: folder_service.normalize_path canonical regex (^/$|^/[^/]+(/[^/]+)*$) — DB CHECK regex must match
provides:
  - documents.folder_path TEXT NOT NULL DEFAULT '/' column
  - documents.scope TEXT NOT NULL DEFAULT 'user' column with CHECK scope IN ('user','global')
  - document_chunks.scope TEXT NOT NULL DEFAULT 'user' column with same CHECK
  - documents.user_id NULLABLE (was NOT NULL)
  - document_chunks.user_id NULLABLE (was NOT NULL)
  - CHECK documents_scope_user_id_consistency (couples scope ↔ user_id presence)
  - CHECK document_chunks_scope_user_id_consistency (mirrors documents)
  - CHECK documents_folder_path_canonical (regex defense in depth for normalize_path chokepoint)
  - UNIQUE INDEX documents_scope_user_path_filename_unique (scope-aware replacement for documents_user_filename_unique)
  - pg_trgm extension enabled (prereq for migration 016 gin_trgm_ops)
affects:
  - phase 01 plan 03 (migration 013 folders table — same canonical CHECK pattern + scope/user_id coupling)
  - phase 01 plan 04 (migration 014 content_markdown — depends on documents table being mutated cleanly here)
  - phase 01 plan 05 (migration 015 RLS policies — references scope + user_id columns added here)
  - phase 01 plan 06 (migration 016 search indexes — references pg_trgm enabled here)
  - phase 01 plan 07 (BLOCKING — pushes this migration to live Supabase DB)
  - phase 01 plan 08 (test_two_scope_rls.py — falsifiable assertions 29–33 validate these CHECK constraints)
  - phase 03 (record_manager dedup key uses (scope, user_id, folder_path, file_name, hash) — schema portion enforced here)

# Tech tracking
tech-stack:
  added:
    - pg_trgm Postgres extension (CREATE EXTENSION IF NOT EXISTS)
  patterns:
    - "Multi-line ALTER TABLE with comma-separated ADD COLUMN IF NOT EXISTS clauses"
    - "Drop-then-add idempotent shape for CHECK constraints (Postgres has no ADD CONSTRAINT IF NOT EXISTS)"
    - "Scope-aware unique expression index using COALESCE sentinel for NULLable user_id"
    - "Bare DDL — no BEGIN/COMMIT (run_migrations.py wraps each file in a transaction)"
    - "DB CHECK regex mirrors Python regex for defense-in-depth path canonicalization"

key-files:
  created:
    - backend/migrations/012_folder_path_and_scope.sql
  modified: []

key-decisions:
  - "Used IF NOT EXISTS / IF EXISTS / DROP-then-ADD throughout for full re-runnability (more recent 007/008/011 pattern, not the bare 003/006 style)"
  - "COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid) sentinel in unique index — Postgres treats NULL as distinct in unique indexes by default; sentinel forces global rows (user_id=NULL) to compete in the same uniqueness namespace (Pitfall 10 — concurrent upload race)"
  - "Enabled pg_trgm in 012 (early) rather than 016 — eliminates dependency-ordering surprises; CREATE EXTENSION is sub-second on Supabase"
  - "Did NOT denormalize folder_path onto document_chunks (per RESEARCH.md Open Question §8) — defer until Phase 4 query plans show join cost is unacceptable; chunks get scope only"
  - "Used regex form `folder_path = '/' OR folder_path ~ '^/[^/]+(/[^/]+)*$'` instead of single regex `^/$|^/[^/]+(/[^/]+)*$` — semantically identical, but the OR form is fractionally clearer to reviewers and avoids the alternation-anchor edge case"
  - "Header-comment style follows 008's two-line `-- Phase X / Module Y: <purpose>` + context-line convention"

patterns-established:
  - "Phase 1 migration header: `-- Phase 1 / Migration 0NN: <one-line purpose>` followed by 1-3 context lines"
  - "Box-drawing section dividers (── 0. ──, ── 1. ──) for multi-section migrations — improves scannability for reviewers"
  - "Scope/user_id coupling CHECK pattern: `(scope='user' AND user_id IS NOT NULL) OR (scope='global' AND user_id IS NULL)` — to be reused on folders table (013) and as the model for trigger-based scope-mutation guard (015)"

requirements-completed: [SCHEMA-01, SCHEMA-02]

# Metrics
duration: ~2 min
completed: 2026-05-03
---

# Phase 01 Plan 02: Migration 012 — folder_path + scope columns + pg_trgm extension Summary

**Foundation migration that adds the two new axes (`folder_path`, `scope`) to `documents` and `document_chunks`, makes `user_id` NULLABLE on both, installs the scope/user_id coupling CHECK + canonical-form CHECK, drops the old single-axis unique constraint and replaces it with a scope-aware expression index, and enables `pg_trgm` so migration 016 can reference `gin_trgm_ops` cleanly.**

## Performance

- **Duration:** ~2 min
- **Started:** 2026-05-03T16:10:32Z
- **Completed:** 2026-05-03T16:11:48Z
- **Tasks:** 1
- **Files modified:** 1 (created)

## Accomplishments

- Created `backend/migrations/012_folder_path_and_scope.sql` (76 lines) — the foundation migration for Phase 1 wave 2.
- Mirrored the paste-ready DDL from RESEARCH.md §"Migration 012" verbatim — every primitive justified in the threat register (T-1-01, T-1-02, T-1-04, T-1-Aux).
- DB CHECK regex `'^/[^/]+(/[^/]+)*$'` exactly mirrors `folder_service._CANONICAL_PATH_RE` (plan 01) — Python normalize is the chokepoint, DB CHECK is defense in depth.
- Migration is fully idempotent (re-runnable without error): `IF NOT EXISTS` on ALTER ADD COLUMN + CREATE EXTENSION + CREATE INDEX; `IF EXISTS` on DROP CONSTRAINT; `DROP CONSTRAINT IF EXISTS … ; ADD CONSTRAINT …` for CHECK constraints (Postgres has no `ADD CONSTRAINT IF NOT EXISTS`, so this drop-then-add pattern is the canonical idempotent shape).
- No `BEGIN`/`COMMIT`/`ROLLBACK` — `run_migrations.py:39-52` wraps each file in a transaction.
- No `CREATE INDEX CONCURRENTLY` — forbidden inside transactions; runner enforces this.
- Migration is **NOT yet applied** to the live Supabase database — plan 07 ([BLOCKING] schema push) handles that.

## DDL Primitives Included

| # | Primitive | Target | Purpose |
|---|-----------|--------|---------|
| 0 | `CREATE EXTENSION IF NOT EXISTS pg_trgm` | (database) | Enables trigram GIN op class for migration 016 (T-1-04) |
| 1 | `ADD COLUMN IF NOT EXISTS folder_path TEXT NOT NULL DEFAULT '/'` | documents | New axis 1 — folder path |
| 1 | `ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'user' CHECK (scope IN ('user','global'))` | documents | New axis 2 — visibility scope |
| 1 | `ALTER COLUMN user_id DROP NOT NULL` | documents | Required for scope='global' rows (no owner) |
| 2 | `CHECK documents_scope_user_id_consistency` | documents | Couples scope ↔ user_id presence (T-1-01 — prevents orphan-leak RLS bypass) |
| 3 | `CHECK documents_folder_path_canonical` regex `^/$ \| ^/[^/]+(/[^/]+)*$` | documents | Rejects 'projects', 'projects/', '//', backslashes (T-1-02 defense in depth) |
| 4 | `DROP CONSTRAINT IF EXISTS documents_user_filename_unique` | documents | Removes old single-axis uniqueness (from migration 006) |
| 4 | `CREATE UNIQUE INDEX IF NOT EXISTS documents_scope_user_path_filename_unique` on `(scope, COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid), folder_path, file_name)` | documents | Scope-aware uniqueness; COALESCE sentinel handles NULL user_id for global rows (T-1-Aux — Pitfall 10) |
| 5 | `ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'user' CHECK (...)` | document_chunks | RLS-perf denormalization (chunks check scope without join to documents) |
| 5 | `ALTER COLUMN user_id DROP NOT NULL` | document_chunks | Mirrors documents change |
| 5 | `CHECK document_chunks_scope_user_id_consistency` | document_chunks | Mirrors documents coupling CHECK |

## Task Commits

Each task was committed atomically:

1. **Task 1-02-01: Write migration 012 — folder_path + scope columns + pg_trgm extension** — `29d387f` (feat)

**Plan metadata commit:** pending (created after STATE.md/ROADMAP.md updates).

## Files Created/Modified

- `backend/migrations/012_folder_path_and_scope.sql` (created, 76 lines) — foundation migration for two-scope schema. Adds folder_path + scope columns (with CHECKs), drops old unique constraint, creates scope-aware unique index, enables pg_trgm. No application of the migration occurs in this plan.

## Existing-Row Migration Behavior

PG11+ stored DEFAULT applies as metadata-only — **no row rewrite, no data movement** for existing Episode 1 rows:

- `documents.folder_path` defaults to `/` for all existing rows.
- `documents.scope` defaults to `'user'` for all existing rows.
- Existing `documents.user_id` values stay non-null (the DROP NOT NULL only relaxes the constraint; it does not change values).
- The new `documents_scope_user_id_consistency` CHECK is satisfied: scope='user' AND user_id IS NOT NULL.
- The new `documents_folder_path_canonical` CHECK is satisfied: folder_path = '/' matches the regex.
- The new unique index on `(scope, COALESCE(user_id, sentinel), '/', file_name)` is functionally equivalent to the dropped `(user_id, file_name)` unique for existing rows (since all existing rows have scope='user' and folder_path='/'), so no duplicate-key violations on application.
- Same applies to document_chunks for the scope column + ALTER user_id NOT NULL.

Existing Episode 1 documents will be queryable at `folder_path='/'`, `scope='user'` immediately after migration 012 lands (plan 07's push).

## Decisions Made

- **IF NOT EXISTS / IF EXISTS pervasive:** Adopted the more recent 007/008/011 idempotent style rather than the bare 003/006 style. Re-running the migration after a partial failure is safe.
- **COALESCE sentinel `'00000000-0000-0000-0000-000000000000'::uuid`** for NULL user_id in unique index — Postgres treats NULL as distinct by default, which would let multiple global rows with the same folder_path + file_name coexist. The sentinel forces them into the same uniqueness namespace.
- **pg_trgm in 012 (not 016):** Eliminates dependency-ordering surprises. `CREATE EXTENSION IF NOT EXISTS` is sub-second on Supabase and idempotent.
- **No folder_path on document_chunks** (per RESEARCH.md Open Question §8): defer denormalization until Phase 4 query plans show join cost is unacceptable. RLS-perf demands scope on chunks; reads of folder_path on chunks always join back to documents.
- **CHECK regex form:** Used `folder_path = '/' OR folder_path ~ '^/[^/]+(/[^/]+)*$'` (the OR-of-equality-and-regex form) rather than the single regex `'^/$|^/[^/]+(/[^/]+)*$'` — semantically identical, but the OR form is fractionally clearer to reviewers and matches the structure used in Python (`if s == '/': pass; else: regex.match(s)`).
- **Box-drawing dividers (── N. ──):** Improves scannability for reviewers across a multi-section migration. Established as a Phase 1 migration convention.

## Deviations from Plan

None — plan executed exactly as written. The reference DDL skeleton in `<action>` (sourced from RESEARCH.md §"Migration 012") was paste-applied verbatim and passed every acceptance-criterion grep on first run.

## Issues Encountered

None.

## Threat Mitigation Coverage

- **T-1-01 (Tampering / Information Disclosure — RLS scope-leak):** Mitigated. `documents_scope_user_id_consistency` CHECK guarantees scope='user' rows have user_id, scope='global' rows have user_id IS NULL. Same on document_chunks. The schema-layer foundation for migration 015's RLS policies — without this CHECK, a NULL user_id on a user-scope row would silently bypass user-isolation.
- **T-1-02 (Tampering — folder path traversal):** Mitigated. `documents_folder_path_canonical` CHECK rejects every input not matching the canonical regex (no leading slash, trailing slash, double slash, backslash). Defense in depth for `folder_service.normalize_path` (plan 01). Test plan 08 enforces with falsifiable assertions 29–33.
- **T-1-04 (Performance — grep collapse without trigram index):** Mitigated. `pg_trgm` extension enabled here (early), so migration 016 can `CREATE INDEX … USING gin (col gin_trgm_ops)` without dependency-ordering surprises. Without this index, Phase 4 grep would degrade to Seq Scan (Pitfall 3 RANK 4 priority).
- **T-1-Aux (Data Integrity — concurrent upload race precondition):** Mitigated. Old `documents_user_filename_unique` from migration 006 dropped, replaced with scope-aware expression index `(scope, COALESCE(user_id, sentinel), folder_path, file_name)`. COALESCE sentinel ensures NULL user_id (global) rows compete in the same uniqueness namespace.

## Idempotency Verification (Static)

Every DDL primitive in this migration uses one of:

- `CREATE EXTENSION IF NOT EXISTS …`
- `ALTER TABLE … ADD COLUMN IF NOT EXISTS …`
- `ALTER TABLE … ALTER COLUMN … DROP NOT NULL` (already-nullable column → no-op; idempotent)
- `ALTER TABLE … DROP CONSTRAINT IF EXISTS …` followed by `ALTER TABLE … ADD CONSTRAINT …` (drop-then-add)
- `CREATE UNIQUE INDEX IF NOT EXISTS …`

Re-running the migration is safe; no statement raises on second execution.

## User Setup Required

None — this plan only writes the migration file. Plan 07 (BLOCKING) is the human-action checkpoint where the user runs `DATABASE_URL=… venv/Scripts/python scripts/run_migrations.py` to apply 012-016 to the live Supabase database.

## Next Phase Readiness

- **Plan 03 (migration 013 folders table)** can now reference the scope + user_id coupling CHECK pattern established here when adding `public.folders` with the same coupling shape and canonical-form CHECK on `folders.path`.
- **Plan 04 (migration 014 content_markdown)** can extend `documents` knowing the table's column-add pattern is already idempotent.
- **Plan 05 (migration 015 RLS policies)** can write USING/WITH CHECK predicates against `scope` and `user_id` knowing both columns and the coupling CHECK exist.
- **Plan 06 (migration 016 search indexes)** can `CREATE INDEX … USING gin (col gin_trgm_ops)` without enabling pg_trgm itself (already enabled here).
- **Plan 07 (BLOCKING — schema push)** can apply 012 against the live DB; existing Episode 1 documents will land at `folder_path='/', scope='user'` automatically.
- **Plan 08 (test_two_scope_rls.py)** can write falsifiable assertions 29–33 against the CHECK constraints (rejection of `'projects'`, `'/projects/'`, `(scope='user', user_id=NULL)`, `(scope='global', user_id=<uuid>)`).

Migration is queued for plan 07's push.

## Self-Check: PASSED

**Files exist:**
- FOUND: `backend/migrations/012_folder_path_and_scope.sql` (76 lines)

**Commits exist:**
- FOUND: `29d387f` (feat(01-02): add migration 012 folder_path + scope columns)

**Verification commands run:**
- `cd backend && venv/Scripts/python -c "<plan-automated-check>"` → exit 0, prints `migration 012 structure OK`
- All 18 acceptance-criterion grep counts match expected values (CREATE EXTENSION pg_trgm = 1, ADD COLUMN folder_path = 1, ADD COLUMN scope = 2, ALTER COLUMN user_id DROP NOT NULL = 2, documents_scope_user_id_consistency = 2, documents_folder_path_canonical = 2, DROP user_filename_unique = 1, documents_scope_user_path_filename_unique = 1, COALESCE sentinel = 1, document_chunks_scope_user_id_consistency = 2, concurrently = 0, BEGIN/COMMIT at top level = 0, canonical regex literal present, no content_markdown, no CREATE TABLE, no CREATE POLICY)
- Migration is structurally valid; live DB validation deferred to plan 08 (post plan 07 push).

---
*Phase: 01-schema-foundation-two-scope-rls-path-normalizer*
*Completed: 2026-05-03*
