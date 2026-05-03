---
phase: 01-schema-foundation-two-scope-rls-path-normalizer
plan: 03
subsystem: backend-migrations
tags: [postgres, ddl, schema-evolution, rls-foundation, check-constraints, unique-expression-index, concurrency]

# Dependency graph
requires:
  - phase: 01
    plan: 02
    provides: scope/user_id coupling CHECK shape + canonical-path regex (^/$|^/[^/]+(/[^/]+)*$) + COALESCE sentinel idiom — folders mirrors all three
provides:
  - public.folders table (id, scope, user_id, path, created_at)
  - CHECK folders_scope_user_id_consistency (couples scope ↔ user_id; same shape as documents/document_chunks)
  - CHECK folders_path_canonical (regex defense in depth; mirrors documents.folder_path canonical CHECK)
  - UNIQUE INDEX folders_scope_user_path_unique on (scope, COALESCE(user_id, sentinel), path) — Pitfall 10 mitigation
  - INDEX folders_scope_user_idx on (scope, user_id) — general listing
  - RLS enabled on public.folders (policies deferred to migration 015)
  - GRANT SELECT/INSERT/UPDATE/DELETE on public.folders to authenticated
affects:
  - phase 01 plan 05 (migration 015 RLS policies — adds policies to public.folders alongside documents/document_chunks)
  - phase 01 plan 07 (BLOCKING — pushes this migration to live Supabase DB)
  - phase 01 plan 08 (test_two_scope_rls.py — falsifiable assertions: folders unique-index race rejection; folders CHECK constraints)
  - phase 03 (folder service — INSERT ... ON CONFLICT DO NOTHING against folders_scope_user_path_unique for concurrent-upload safety)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "CREATE TABLE IF NOT EXISTS public.<name> with inline CHECK constraints"
    - "Scope-aware unique expression index using COALESCE sentinel (re-used from migration 012)"
    - "RLS-enabled-no-policies as fail-closed default (policies deferred to a later migration for catalog reviewability)"
    - "GRANT to authenticated role pattern for tables with RLS"
    - "Box-drawing section dividers (── 1. ──, ── 2. ──, etc.) for multi-section migrations — established in migration 012"

key-files:
  created:
    - backend/migrations/013_folders_table.sql
  modified: []

key-decisions:
  - "Single-statement CREATE TABLE with inline CONSTRAINT clauses (not separate ADD CONSTRAINT) — folders is a NEW table, so the simpler inline form applies; the drop-then-add pattern from migration 012 is only needed when adding constraints to existing tables"
  - "RLS policies intentionally deferred to migration 015 — keeps the full Phase 1 RLS catalog (documents, document_chunks, folders) in a single reviewable file; until 015 lands, RLS-enabled-no-policies = fail-closed (all reads/writes from authenticated role denied) which is the safest default"
  - "No foreign key from documents.folder_path to folders.path — per ARCHITECTURE.md Pattern 2, folders is a sparse, explicit-empty-only table. Most folders exist by inference from documents.folder_path. A FK would force every document upload to also touch folders, defeating the sparse design"
  - "COALESCE sentinel idiom re-used from migration 012's documents_scope_user_path_filename_unique — Postgres treats NULL as distinct in unique indexes by default; without the sentinel, multiple global rows with the same path would coexist (Pitfall 10)"
  - "GRANT to authenticated (not service_role) — service_role bypasses RLS regardless; authenticated is the role that needs the GRANT for RLS policies in 015 to take effect"
  - "Header comment matches plan 02's two-line `-- Phase 1 / Migration 0NN: <purpose>` + 3-line context shape (established as Phase 1 migration convention)"

patterns-established:
  - "Side-table-with-RLS-deferred shape: CREATE TABLE + CHECKs + indexes + ENABLE RLS + GRANT (no CREATE POLICY) — useful when policy review benefits from being in a single later migration"
  - "Inline CONSTRAINT in CREATE TABLE for new tables (vs. ADD CONSTRAINT for existing tables) — adopt the simpler form when applicable"

requirements-completed: [SCHEMA-04]

# Metrics
duration: ~1 min
completed: 2026-05-03
---

# Phase 01 Plan 03: Migration 013 — folders table + unique expression index + RLS enable Summary

**Thin `public.folders` side table for first-class empty-folder tracking, with COALESCE-based unique expression index that mitigates concurrent-upload races (Pitfall 10), the same scope/user_id coupling and canonical-path CHECK constraints as documents (migration 012), and RLS enabled with policies deferred to migration 015.**

## Performance

- **Duration:** ~1 min
- **Started:** 2026-05-03T16:16:04Z
- **Completed:** 2026-05-03T16:17:21Z
- **Tasks:** 1
- **Files modified:** 1 (created)

## Accomplishments

- Created `backend/migrations/013_folders_table.sql` (50 lines) — wave 2 plan 03 of Phase 1.
- Mirrored the paste-ready DDL from `01-RESEARCH.md § "Migration 013"` verbatim — every primitive justified in the threat register (T-1-01 folders, T-1-02 folders, T-1-03 concurrency).
- Re-used the COALESCE sentinel (`'00000000-0000-0000-0000-000000000000'::uuid`) idiom from migration 012's `documents_scope_user_path_filename_unique` — same Pitfall 10 mitigation pattern, this time on the folders side table.
- The DB CHECK regex `'^/[^/]+(/[^/]+)*$'` exactly mirrors migration 012's `documents_folder_path_canonical` and `folder_service._CANONICAL_PATH_RE` (plan 01) — three-layer defense in depth.
- The scope/user_id coupling CHECK is bit-for-bit identical to migration 012's `documents_scope_user_id_consistency` and `document_chunks_scope_user_id_consistency` — uniform shape across all three tables that will receive RLS policies in migration 015.
- Migration is fully idempotent (re-runnable without error): `CREATE TABLE IF NOT EXISTS`, `CREATE UNIQUE INDEX IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`. The CHECK constraints are inline in `CREATE TABLE`, so the drop-then-add pattern from migration 012 is unnecessary here (table itself is gated by `IF NOT EXISTS`).
- No `BEGIN`/`COMMIT`/`ROLLBACK` — `run_migrations.py` wraps each file in a transaction.
- No `CREATE INDEX CONCURRENTLY` — forbidden inside transactions; runner enforces this.
- Migration is **NOT yet applied** to the live Supabase database — plan 07 ([BLOCKING] schema push) handles that.

## DDL Primitives Included

| # | Primitive | Target | Purpose |
|---|-----------|--------|---------|
| 1 | `CREATE TABLE IF NOT EXISTS public.folders (id, scope, user_id, path, created_at)` | (new) | Side table for first-class empty-folder tracking |
| 1 | inline `CHECK (scope IN ('user','global'))` | folders.scope | Restricts scope to the two-scope vocabulary |
| 1 | inline `REFERENCES auth.users(id) ON DELETE CASCADE` | folders.user_id | User-scope rows cascade-delete on user removal; global rows have NULL user_id |
| 1 | inline `CONSTRAINT folders_scope_user_id_consistency` | folders | Couples scope ↔ user_id (T-1-01 folders — prevents orphan-leak shape) |
| 1 | inline `CONSTRAINT folders_path_canonical` regex `^/$ \| ^/[^/]+(/[^/]+)*$` | folders.path | Rejects 'projects', 'projects/', '//', backslashes (T-1-02 folders defense in depth) |
| 2 | `CREATE UNIQUE INDEX IF NOT EXISTS folders_scope_user_path_unique` on `(scope, COALESCE(user_id, sentinel), path)` | folders | Concurrent-upload race mitigation (T-1-03 / Pitfall 10) |
| 3 | `CREATE INDEX IF NOT EXISTS folders_scope_user_idx` on `(scope, user_id)` | folders | General listing index for "all folders for this user/scope" |
| 4 | `ALTER TABLE public.folders ENABLE ROW LEVEL SECURITY` | folders | Fail-closed default until migration 015 adds policies |
| 4 | `GRANT SELECT, INSERT, UPDATE, DELETE ON public.folders TO authenticated` | folders | Required for migration 015 policies to grant access (service_role bypasses RLS) |

## Task Commits

Each task was committed atomically:

1. **Task 1-03-01: Write migration 013 — folders table + unique expression index + RLS-enable** — `37853b7` (feat)

**Plan metadata commit:** pending (created after STATE.md/ROADMAP.md updates).

## Files Created/Modified

- `backend/migrations/013_folders_table.sql` (created, 50 lines) — folders side table with both CHECK constraints, COALESCE-based unique expression index, listing index, RLS enabled, GRANT to authenticated. No application of the migration occurs in this plan.

## Deliberate Non-FK Design Choice

`documents.folder_path` is referenced by `folders.path` as **plain TEXT — no foreign key** per `ARCHITECTURE.md` Pattern 2. The folders table is intentionally sparse: most folders exist by inference from `documents.folder_path`, and rows in `public.folders` exist only to track folders that are *explicitly* empty (created without uploading a document). A FK would force every document upload to also touch `public.folders`, defeating the sparse design and adding write-amplification on a hot path.

## RLS Policies Deferred to Plan 05

Migration 013 only sets `ENABLE ROW LEVEL SECURITY` on `public.folders`; no `CREATE POLICY` statements are present. Policies will be added in **plan 05 / migration 015**, which will land the full two-scope policy catalog for `documents`, `document_chunks`, AND `folders` in a single reviewable migration. Until 015 runs, `public.folders` is in the safest possible state for the `authenticated` role: RLS enabled + no policies = all reads/writes denied (service_role bypasses RLS regardless, so the migration runner and admin paths still function).

## Decisions Made

- **Inline `CONSTRAINT` clauses in `CREATE TABLE`** (not separate `ALTER TABLE … ADD CONSTRAINT`) — folders is a *new* table, so the simpler inline form is correct. Migration 012's drop-then-add pattern is only required for adding constraints to *existing* tables (Postgres has no `ADD CONSTRAINT IF NOT EXISTS`).
- **RLS policies deferred to migration 015** — keeps the full Phase 1 RLS catalog (documents, document_chunks, folders) in one file for reviewability. RLS-enabled-no-policies is fail-closed for the authenticated role, which is the safest default during the gap.
- **No FK from documents.folder_path to folders.path** — per ARCHITECTURE.md Pattern 2 (sparse folders table). Documented in the migration header comment for future readers.
- **COALESCE sentinel idiom re-used from migration 012** — same Pitfall 10 mitigation, same `'00000000-0000-0000-0000-000000000000'::uuid` sentinel, same rationale (Postgres treats NULL as distinct in unique indexes; sentinel forces global rows to compete in the same uniqueness namespace).
- **GRANT to authenticated** (not service_role) — service_role bypasses RLS, so the GRANT is unnecessary for the runner. The authenticated role is the one that needs the GRANT for migration 015's policies to take effect.

## Deviations from Plan

None — plan executed exactly as written. The reference DDL skeleton in `<action>` (sourced from `01-RESEARCH.md § "Migration 013"`) was paste-applied verbatim and passed every acceptance-criterion grep on first run.

## Issues Encountered

None.

## Threat Mitigation Coverage

- **T-1-01 (folders) Tampering / Information Disclosure — RLS scope-leak shape on folders:** Mitigated. `folders_scope_user_id_consistency` CHECK guarantees scope='user' rows have user_id, scope='global' rows have user_id IS NULL. Same shape as documents/document_chunks. Combined with `ENABLE ROW LEVEL SECURITY`, the table is fail-closed until migration 015 adds policies.
- **T-1-02 (folders) Tampering — folder path traversal:** Mitigated. `folders_path_canonical` CHECK rejects every input not matching `'^/$|^/[^/]+(/[^/]+)*$'`. Defense in depth for `folder_service.normalize_path` (plan 01) — same regex used in migration 012's `documents_folder_path_canonical`. Test plan 08 will enforce with falsifiable assertions.
- **T-1-03 Tampering / Data Integrity (Concurrency) — concurrent INSERT race:** Mitigated. `folders_scope_user_path_unique` unique expression index on `(scope, COALESCE(user_id, sentinel), path)` ensures concurrent INSERTs of the same `(scope, user_id, path)` produce exactly one row (the second INSERT fails on the unique constraint). COALESCE sentinel is required because Postgres treats NULLs as distinct by default. Phase 3's folder service will pair with `INSERT ... ON CONFLICT DO NOTHING`. ROADMAP success criterion: "exactly one folders row from 10 parallel uploads" — schema bedrock laid here.

## Idempotency Verification (Static)

Every DDL primitive in this migration uses one of:

- `CREATE TABLE IF NOT EXISTS public.folders` (with inline CONSTRAINT clauses — table-level idempotency gate)
- `CREATE UNIQUE INDEX IF NOT EXISTS folders_scope_user_path_unique`
- `CREATE INDEX IF NOT EXISTS folders_scope_user_idx`
- `ALTER TABLE … ENABLE ROW LEVEL SECURITY` (re-running is a no-op when already enabled)
- `GRANT … TO authenticated` (idempotent — duplicate grants are no-ops)

Re-running the migration is safe; no statement raises on second execution.

## User Setup Required

None — this plan only writes the migration file. Plan 07 (BLOCKING) is the human-action checkpoint where the user runs the migration runner to apply 012-016 to the live Supabase database.

## Next Phase Readiness

- **Plan 04 (migration 014 content_markdown)** is unblocked — adds `content_markdown TEXT` column to documents (independent of folders).
- **Plan 05 (migration 015 RLS policies)** can now write USING/WITH CHECK predicates against `public.folders` knowing the table, scope/user_id columns, and ENABLE RLS are all in place.
- **Plan 06 (migration 016 search indexes)** is independent of folders.
- **Plan 07 (BLOCKING — schema push)** can apply 013 against the live DB; folders starts empty (Phase 3 populates on demand).
- **Plan 08 (test_two_scope_rls.py)** can write falsifiable assertions against folders' CHECK constraints (rejection of non-canonical paths, scope/user_id mismatch) and against the unique index race (concurrent INSERT of same `(scope, user_id, path)` → exactly one row).
- **Phase 3 folder service** can rely on `INSERT INTO public.folders … ON CONFLICT DO NOTHING` against `folders_scope_user_path_unique` for race-safe folder creation.

Migration is queued for plan 07's push.

## Self-Check: PASSED

**Files exist:**
- FOUND: `backend/migrations/013_folders_table.sql` (50 lines)

**Commits exist:**
- FOUND: `37853b7` (feat(01-03): add migration 013 folders table + unique expression index + RLS enable)

**Verification commands run:**
- `cd backend && venv/Scripts/python -c "<plan-automated-check>"` → exit 0, prints `migration 013 structure OK`
- All 15 acceptance-criterion grep checks match expected values:
  - `CREATE TABLE IF NOT EXISTS public.folders` = 1
  - `REFERENCES auth.users(id) ON DELETE CASCADE` = 1
  - `folders_scope_user_id_consistency` = 1
  - `folders_path_canonical` = 1
  - `CREATE UNIQUE INDEX IF NOT EXISTS folders_scope_user_path_unique` = 1
  - `COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid)` = 1
  - `CREATE INDEX IF NOT EXISTS folders_scope_user_idx` = 1
  - `ALTER TABLE public.folders ENABLE ROW LEVEL SECURITY` = 1
  - `GRANT SELECT, INSERT, UPDATE, DELETE ON public.folders TO authenticated` = 1
  - canonical regex literal `'^/[^/]+(/[^/]+)*$'` = 1
  - `CREATE POLICY` = 0
  - `FOREIGN KEY` = 0
  - `CONCURRENTLY` = 0
  - `NULLS NOT DISTINCT` = 0
  - top-level `BEGIN;` / `COMMIT;` = 0
- Migration is structurally valid; live DB validation deferred to plan 08 (post plan 07 push).

---
*Phase: 01-schema-foundation-two-scope-rls-path-normalizer*
*Completed: 2026-05-03*
