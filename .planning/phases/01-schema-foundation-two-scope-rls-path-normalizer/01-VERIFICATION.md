---
phase: 01-schema-foundation-two-scope-rls-path-normalizer
verified: 2026-05-03T00:00:00Z
status: human_needed
score: 5/5 success criteria verified; 11/11 requirement IDs accounted for
overrides_applied: 0
human_verification:
  - test: "Re-run verify_phase1_schema.py once DATABASE_URL is exported in PowerShell"
    expected: "Exit 0; all 18 structural checks (pg_trgm, columns, table, functions, triggers, policy counts, indexes) print [OK]"
    why_human: "Backend's run-time direct connection string contains the Supabase DB password and is not in this verifier's shell. Plan 07 captured the live MCP-side execute_sql equivalent (18/18 OK on 2026-05-04) but a re-run by the user across a session boundary is the right re-confirmation surface. The script itself is verified existing/wired; only its in-process execution needs DATABASE_URL."
  - test: "Decide whether migration 017 (profiles RLS recursion fix) should be committed as backend/migrations/017_profiles_admin_policy_use_is_admin_helper.sql before Phase 2 begins on a different DB / fresh checkout"
    expected: "Either (a) commit the .sql file so run_migrations.py reproduces the fix on any environment, or (b) explicitly accept that the live qgojopazceldfxfbbnhy DB is the only environment that needs the fix and document that decision."
    why_human: "Recorded carry-forward in 08-SUMMARY.md §Deviations §2. Live DB has the fix applied via MCP; no .sql file exists in backend/migrations/. A fresh DB will hit SQLSTATE 42P17 on profiles RLS and block plan 08 setup until 017.sql is replayed manually. This is a persistence/portability decision, not a Phase 1 functional gap — Phase 1's matrix passes 49/0 on the live DB."
  - test: "Decide whether backend/scripts/run_migrations.py (untracked file, pre-existed Phase 1) should be committed to the repo"
    expected: "Either commit the file or explicitly mark it intentionally-untracked."
    why_human: "Plan 07's canonical apply path requires this script. It is functionally fine on the developer's machine (the MCP path was used as substitute) but not in source control. Carry-forward from 07-SUMMARY.md."
  - test: "Decide whether the 5 pre-existing Episode-1 test failures (Hybrid 8/2, Tools 12/2, Sub-Agents 0/1) should be triaged as part of Phase 2 prep or deferred."
    expected: "Either flip TEST_USER_A.is_admin=true on this Supabase project to match Episode-1 dev convention, OR rewrite test_settings/test_hybrid/test_tools to use TEST_USER_ADMIN for admin-gated calls."
    why_human: "Carry-forward from 08-SUMMARY.md §Issues §3. Not Phase 1 regressions (test_two_scope_rls didn't exist before; Hybrid/Tools/Sub-Agents failures are admin-flag drift). Phase 1 introduced no regressions in those suites — verified by full-sweep run."
---

# Phase 1: Schema Foundation + Two-Scope RLS + Path Normalizer Verification Report

**Phase Goal:** Every downstream phase has the columns, indexes, RLS policies, and path-canonicalization chokepoint it needs — and the highest-rank pitfalls (RLS scope-leak, grep perf collapse, path drift, concurrent-upload race) are designed out of the data model up front.

**Verified:** 2026-05-03 (verifier session) against codebase + live test execution
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria, lines 33-38)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Every Episode-2 table (`documents`, `document_chunks`, `folders`) enforces two-scope union read predicate; separate INSERT/UPDATE per scope; scope mutation forbidden | VERIFIED | `migrations/015_two_scope_rls.sql` lines 73-204 — 19 policies (7 documents + 5 chunks + 7 folders); policy `documents_select` lines 73-79 has the union predicate `scope='global' OR (scope='user' AND user_id=(SELECT auth.uid()))`. Scope mutation forbidden via `forbid_scope_mutation()` BEFORE UPDATE trigger lines 44-55, attached to all 3 tables lines 209-222. `test_two_scope_rls.py` Group 1 (matrix on documents/folders/chunks) + Group 2 (3 trigger tests) all pass live. |
| 2 | Non-admin INSERT/UPDATE scope='global' rejected by RLS; cross-user × cross-scope matrix in `test_two_scope_rls.py` passes 100% | VERIFIED | Test executed live via `venv/Scripts/python scripts/test_two_scope_rls.py`: **Results: 49 passed, 0 failed**. Includes `A INSERT scope='global' rejected by RLS (no policy grants)` (PASS), `[folders] A INSERT scope='global' rejected by RLS` (PASS), `[chunks] A INSERT scope='global' rejected by RLS` (PASS). `documents_insert_global` policy lines 88-95 of mig 015 requires `public.is_admin()`. |
| 3 | `pg_trgm` enabled; representative grep-shape EXPLAIN shows Bitmap Index Scan; `text_pattern_ops` btree on `folder_path` accelerates LIKE prefix | VERIFIED (structural) | `migrations/012_folder_path_and_scope.sql` line 9 `CREATE EXTENSION IF NOT EXISTS pg_trgm`. `migrations/016_search_indexes.sql` lines 35-50 add `documents_content_markdown_trgm_idx` (gin_trgm_ops) and `documents_folder_path_prefix_idx` (text_pattern_ops). 07-SUMMARY.md records live MCP execute_sql verification: pg_trgm OK; both indexes exist. ROADMAP success criterion 3 explicitly defers scaled-perf to Phase 4 TEST-02; "Phase 1 verifies structural existence" — satisfied. |
| 4 | Existing Episode-1 docs queryable at `folder_path='/'`, `scope='user'` via DEFAULTs; canonical-form CHECK rejects `'projects/'` and `'projects'` | VERIFIED (vacuously for existing rows; CHECK enforcement live) | `migrations/012` line 13 `folder_path TEXT NOT NULL DEFAULT '/'` and line 14 `scope TEXT NOT NULL DEFAULT 'user'` — PG11+ stored DEFAULT applies as metadata-only. 07-SUMMARY.md records 0 pre-existing rows on this fresh project, so the truth is vacuously satisfied for existing rows and the DEFAULTs would apply if any existed. CHECK enforcement is verified live by `test_two_scope_rls.py` Group 4: `INSERT folder_path='projects' rejected by canonical CHECK` (PASS), `INSERT folder_path='/projects/' rejected by canonical CHECK` (PASS), `INSERT folder_path='//' rejected by canonical CHECK` (PASS). |
| 5 | Single Python `normalize_path()` helper at `backend/app/services/folder_service.py`; round-trip tests for `/`, `/a/b`, `/a/b/c` and rejections for `..`/`.` | VERIFIED | `backend/app/services/folder_service.py` lines 28-67 (96-line file). `python -m app.services.folder_service` prints "folder_service.normalize_path: 15 self-tests passed" (executed live in this verifier session). `test_two_scope_rls.py` Group 3 (FOLDER-01) imports `from app.services.folder_service import normalize_path` (line 37) and runs 12 assertions all PASS — including `normalize_path('/') == '/'`, `normalize_path('/a/b') == '/a/b'`, `normalize_path('/a/b/c')` round-trip, and `..`/`.` ValueError raises. |

**Score:** 5/5 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `backend/app/services/folder_service.py` | normalize_path() pure-function helper, _CANONICAL_PATH_RE, _FORBIDDEN_SEGMENTS | VERIFIED | 96 lines; stdlib-only (`re`, `unicodedata`); exports `normalize_path`. Inline self-test runs in <1s and prints "15 self-tests passed". Currently consumed by `scripts/test_two_scope_rls.py` line 37 (one consumer; Phase 3 will be the primary user — FOLDER-01 contract is "single canonical helper exists and is importable", which is the FOLDER-01 spec for Phase 1). |
| `backend/migrations/012_folder_path_and_scope.sql` | folder_path + scope columns + scope-aware unique index + pg_trgm enabled | VERIFIED | 76 lines; 6 sections (── 0..5 ──); `CREATE EXTENSION IF NOT EXISTS pg_trgm` line 9; `documents_scope_user_id_consistency` CHECK lines 24-30; `documents_folder_path_canonical` CHECK lines 37-42; scope-aware unique expression index with COALESCE sentinel lines 51-57; `document_chunks` mirrored lines 64-76. |
| `backend/migrations/013_folders_table.sql` | folders side-table with COALESCE-based unique expression index + RLS-enable | VERIFIED | 57 lines; `CREATE TABLE IF NOT EXISTS public.folders` lines 8-27 with both CHECK constraints; unique expression index `folders_scope_user_path_unique` lines 38-43 (COALESCE sentinel for global rows); `ENABLE ROW LEVEL SECURITY` line 55; `GRANT ... TO authenticated` line 57. |
| `backend/migrations/014_content_markdown_column.sql` | content_markdown column + status enum + backfill-scan partial index | VERIFIED | 29 lines; `content_markdown TEXT` (nullable) lines 13-14; `content_markdown_status TEXT NOT NULL DEFAULT 'pending'` with 4-element CHECK enum lines 14-21; partial index `documents_content_markdown_status_idx WHERE content_markdown_status <> 'ready'` lines 27-29. |
| `backend/migrations/015_two_scope_rls.sql` | 19 two-scope RLS policies + is_admin() helper + forbid_scope_mutation() trigger | VERIFIED | 222 lines; `is_admin()` SECURITY DEFINER STABLE function lines 27-33; `forbid_scope_mutation()` trigger function lines 44-55; old Episode-1 single-axis policies dropped lines 61-68; documents 7 policies lines 73-117; document_chunks 5 policies (no UPDATE) lines 124-156; folders 7 policies lines 160-204; 3 BEFORE UPDATE triggers attached lines 209-222. |
| `backend/migrations/016_search_indexes.sql` | gin_trgm_ops + text_pattern_ops indexes (3 GIN + 2 btree = 5 total) | VERIFIED | 61 lines; `documents_content_markdown_trgm_idx` USING gin gin_trgm_ops (line 35-36); `documents_folder_path_trgm_idx` USING gin gin_trgm_ops (line 41-42); `documents_folder_path_prefix_idx` btree text_pattern_ops (line 49-50); `folders_path_trgm_idx` USING gin (line 55-56); `folders_path_prefix_idx` text_pattern_ops btree (line 60-61). |
| `backend/scripts/verify_phase1_schema.py` | 18-check structural verifier (pg_extension / information_schema / pg_proc / pg_trigger / pg_policies / pg_indexes) | VERIFIED (existence + structure) — runtime BLOCKED on DATABASE_URL | 98 lines; reads `DATABASE_URL` from env; psycopg2; 18 CHECKS array lines 23-60; `main() -> int`; exit 0 on pass / 1 on fail. **Live invocation in this verifier session printed `[FATAL] DATABASE_URL not set.`** This is expected — see human_verification item 1. The 18 SQL checks were executed live via Supabase MCP execute_sql on 2026-05-04 (recorded in 07-SUMMARY.md "Live Verification Output": 18/18 OK). |
| `backend/scripts/test_two_scope_rls.py` | 49 falsifiable assertions across 5 groups | VERIFIED — live run **49 passed, 0 failed** | 444 lines; 5 groups (FOLDER-01 normalize_path 12; SCHEMA-01..03 CHECK constraints 7; RLS matrix documents 14; RLS matrix folders 6; RLS matrix chunks 3; RLS-03 trigger 4; SCHEMA-05 EXPLAIN 3); imports `normalize_path` line 37; uses anon-key + JWT (NOT service-role) lines 124-126; tracking-list cleanup in finally lines 58-77 (CLAUDE.md compliant). Live run: see Behavioral Spot-Check below. |
| `backend/scripts/test_helpers.py` extensions | TEST_USER_ADMIN, get_admin_token, get_user_supabase_client | VERIFIED | Lines 26-28 `TEST_USER_ADMIN = {...}`; line 99 `def get_admin_token() -> str`; line 110 `def get_user_supabase_client(jwt_token: str)`. |
| `backend/scripts/test_all.py` registration | Two-Scope RLS suite registered | VERIFIED | Line 19 `import test_two_scope_rls`; line 34 `("Two-Scope RLS", test_two_scope_rls),`. |
| `backend/migrations/017_profiles_admin_policy_use_is_admin_helper.sql` | Migration to fix Episode-1 profiles RLS recursion using is_admin() | MISSING (file) — APPLIED LIVE (DB) | No file in `backend/migrations/`. 08-SUMMARY.md §Deviations §2 explicitly flags this: applied via MCP `apply_migration`, not committed. Live DB has the fix; fresh DB would re-trigger SQLSTATE 42P17. **Not blocking for Phase 1 contract** — `RLS-04` / `TEST-04` matrix passes; the carry-forward is a portability concern for Phase 2 prep. Surfaced as human_verification item 2. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `test_two_scope_rls.py` | `folder_service.normalize_path` | `from app.services.folder_service import normalize_path` (line 37) | WIRED | Import succeeds; 12 normalize_path assertions all PASS in live run. |
| `migration 015 RLS policies` | `is_admin()` SECURITY DEFINER helper (lines 27-33) | `public.is_admin()` references in 6+ policies (lines 94, 107, 117, 145, 156, 181, 194, 204) | WIRED | Live MCP verifier (07-SUMMARY) confirms function exists; live test_two_scope_rls confirms admin-only global writes succeed and non-admin global writes are rejected. |
| `migration 015 triggers` | `forbid_scope_mutation()` trigger function | `BEFORE UPDATE … FOR EACH ROW EXECUTE FUNCTION public.forbid_scope_mutation()` lines 209-222 | WIRED | 3 triggers attached (documents, document_chunks, folders); live test_two_scope_rls Group 2 confirms `check_violation` raised on scope mutation across all 3 tables. |
| `migration 012 documents_scope_user_path_filename_unique` | scope-aware uniqueness | COALESCE sentinel `'00000000-0000-0000-0000-000000000000'::uuid` (line 54) | WIRED | Live test verifies Pitfall 10 mitigation: `[folders] Duplicate (scope,user,path) INSERT rejected by unique expression index (Pitfall 10)` PASS. |
| `migration 012 / 013` CHECK regex | `folder_service._CANONICAL_PATH_RE` | DB `folder_path = '/' OR folder_path ~ '^/[^/]+(/[^/]+)*$'` mirrors Python `re.compile(r"^/$|^/[^/]+(/[^/]+)*$")` | WIRED | Both regexes are identically anchored; live test verifies CHECK rejects `'projects'`, `'/projects/'`, `'//'`. |
| `test_all.py` | `test_two_scope_rls` | Line 19 import + line 34 registration tuple | WIRED | Suite registered; full-sweep run records `[PASS] Two-Scope RLS (Phase 1, direct-Supabase): 49/0`. |

### Data-Flow Trace (Level 4)

Not applicable to this phase — schema foundation, not a UI/API rendering surface. Data flows are validated by the test_two_scope_rls.py matrix end-to-end (anon-key + JWT → Supabase REST → RLS policies → CHECK constraints → triggers → DB rows → SELECT response). The 49/0 live run is the data-flow verification.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| normalize_path inline self-tests | `cd backend && venv/Scripts/python -m app.services.folder_service` | `folder_service.normalize_path: 15 self-tests passed` | PASS |
| Two-Scope RLS matrix runs live against Supabase | `cd backend && venv/Scripts/python scripts/test_two_scope_rls.py` | `Results: 49 passed, 0 failed / All tests passed!` | PASS |
| Module-surface import contract | `python -c "from app.services.folder_service import normalize_path, _CANONICAL_PATH_RE, _FORBIDDEN_SEGMENTS; ..."` | `Importable + signature OK` | PASS |
| Schema verifier (live DB) | `cd backend && venv/Scripts/python scripts/verify_phase1_schema.py` | `[FATAL] DATABASE_URL not set.` (expected; see human_verification item 1) | SKIP |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| FOLDER-01 | 01-PLAN | Single canonical normalize_path() helper | SATISFIED | folder_service.py exists; 12/12 normalize_path assertions PASS in live RLS test |
| SCHEMA-01 | 02-PLAN, 07-PLAN | documents.folder_path NOT NULL DEFAULT '/' + canonical CHECK | SATISFIED | mig 012 lines 13, 37-42; live test rejects non-canonical inputs |
| SCHEMA-02 | 02-PLAN, 07-PLAN | documents.scope NOT NULL DEFAULT 'user' + scope/user_id coupling CHECK | SATISFIED | mig 012 lines 14-15, 24-30; coupling CHECK live test PASS for both directions |
| SCHEMA-03 | 04-PLAN, 07-PLAN | content_markdown TEXT + content_markdown_status enum | SATISFIED | mig 014 lines 13-21; live test PASS: `'processing' rejected by enum CHECK` |
| SCHEMA-04 | 03-PLAN, 07-PLAN | folders table with (id, scope, user_id, path, created_at) + unique constraint | SATISFIED | mig 013 lines 8-27, 38-43; live test PASS: duplicate (scope,user,path) rejected (Pitfall 10) |
| SCHEMA-05 | 06-PLAN, 07-PLAN | pg_trgm + GIN trgm + text_pattern_ops btree indexes | SATISFIED | mig 012 line 9 (extension); mig 016 lines 35-61 (5 indexes); structural verification by plan 07's MCP execute_sql; scaled-perf deferred to Phase 4 per ROADMAP SC-3 |
| RLS-01 | 05-PLAN, 07-PLAN | SELECT predicate `(scope='user' AND user_id=auth.uid()) OR scope='global'` on documents/document_chunks/folders | SATISFIED | mig 015 lines 73-79, 124-130, 160-166 — identical predicate on all 3 tables |
| RLS-02 | 05-PLAN, 07-PLAN | Separate INSERT/UPDATE per scope; admin-only writes for global via is_admin() | SATISFIED | mig 015 lines 81-117, 132-156, 168-204 — splits hold; live test confirms non-admin global writes rejected |
| RLS-03 | 05-PLAN, 07-PLAN | UPDATE forbids scope mutation — implemented as BEFORE UPDATE trigger (canonical Postgres workaround) | SATISFIED | mig 015 lines 44-55 + 209-222; live test Group 2 PASS on all 3 tables |
| RLS-04 | 08-PLAN | test_rls.py extended with full cross-user × cross-scope matrix | SATISFIED | test_two_scope_rls.py 444 lines; live 49/0 |
| TEST-04 | 08-PLAN | test_two_scope_rls.py — full cross-user × cross-scope matrix | SATISFIED | Same as RLS-04; suite registered in test_all.py line 34 |

**All 11 Phase-1 requirements accounted for.** No orphaned requirements (REQUIREMENTS.md lines 207-209 lists 11; all 11 are claimed by plans 01/02/03/04/05/06/07/08).

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| (none in created files) | — | — | — | — |

Anti-pattern grep on the 7 created files returned no TODO/FIXME/PLACEHOLDER, no `return null`/`return {}` stubs, no `console.log`-only handlers, no hardcoded empty data destined for rendering. Migration files are complete DDL; verifier and test files are fully populated; folder_service.py is the spec-correct implementation.

### Human Verification Required

**This phase has 4 items requiring human action / decision** (none are Phase 1 functional gaps; all are persistence / portability / triage decisions):

1. **Re-run verify_phase1_schema.py with DATABASE_URL exported.** The script is implemented and the 18 SQL checks were executed live via Supabase MCP execute_sql on 2026-05-04 (18/18 OK per 07-SUMMARY.md). A direct in-process re-run by the user is the canonical reproduction of the live structural-verification gate.

2. **Decide whether to commit migration 017 as a .sql file.** Migration 017 (Episode-1 profiles RLS recursion fix using is_admin()) was applied via MCP but is NOT committed in `backend/migrations/`. Live DB has it; fresh checkouts won't reproduce it. Either commit `017_profiles_admin_policy_use_is_admin_helper.sql` or document the intentional MCP-only application. Recorded carry-forward from 08-SUMMARY.md §Deviations §2.

3. **Decide whether to commit `backend/scripts/run_migrations.py`.** Untracked file pre-existed Phase 1; canonical apply path requires it. Either commit or mark intentionally-untracked.

4. **Triage the 5 pre-existing Episode-1 test failures.** Hybrid 8/2, Tools 12/2, Sub-Agents 0/1 fail because `TEST_USER_A.is_admin` is false on this fresh Supabase project (Episode-1 dev convention assumed it true). Phase 1 introduced no regressions; the failures are environment drift. Decide: flip `TEST_USER_A.is_admin=true` OR rewire those tests to use `TEST_USER_ADMIN`.

### Gaps Summary

**No functional gaps.** Phase 1's contract — the schema/RLS/index/normalizer foundation that downstream phases consume, plus the 49-assertion cross-user × cross-scope matrix passing 100% — is fully met. The Phase-2 gate (RANK-1 Pitfall 1 mitigation: cross-user × cross-scope RLS matrix passes 100%) is satisfied: 49 passed / 0 failed against the live Supabase DB.

The 4 human-verification items are explicitly NOT Phase-1 functional failures:
- Item 1 is a re-confirmation of an already-verified live state (MCP execute_sql 18/18 OK on 2026-05-04).
- Items 2 and 3 are persistence/portability decisions about untracked artifacts (017.sql, run_migrations.py); Phase 1's deliverables on the live DB are correct.
- Item 4 is environment drift in Episode-1 tests (carry-forward triage), not Phase-1 work.

The status is `human_needed` (not `passed`) because the verification process MUST surface these decisions to the user — even when the codebase contract is met — so the developer can resolve them before Phase 2 plans depend on them.

---

*Verified: 2026-05-03*
*Verifier: Claude (gsd-verifier)*
