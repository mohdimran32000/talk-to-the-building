---
status: approved-with-carry-forward
phase: 01-schema-foundation-two-scope-rls-path-normalizer
source: [01-VERIFICATION.md]
started: 2026-05-04T00:00:00Z
updated: 2026-05-04T00:00:00Z
approved_at: 2026-05-04T00:00:00Z
approval_note: "User-approved with all 4 items as carry-forward to Phase 2 prep / follow-up. Phase 1 marked complete; no items block Phase 2."
---

## Current Test

[awaiting human testing]

## Tests

### 1. Re-run verify_phase1_schema.py once DATABASE_URL is exported
expected: Exit 0; all 18 structural checks (pg_trgm, columns, table, functions, triggers, policy counts, indexes) print [OK]
result: [pending]
why_human: Backend's run-time direct connection string contains the Supabase DB password and is not in this verifier's shell. Plan 07 captured the live MCP-side execute_sql equivalent (18/18 OK on 2026-05-04) but a re-run by the user across a session boundary is the right re-confirmation surface. The script itself is verified existing/wired; only its in-process execution needs DATABASE_URL.

### 2. Decide commit-vs-leave for migration 017 (profiles RLS recursion fix)
expected: Either (a) commit a `backend/migrations/017_profiles_admin_policy_use_is_admin_helper.sql` file so run_migrations.py reproduces the fix on any environment, or (b) explicitly accept that the live qgojopazceldfxfbbnhy DB is the only environment that needs the fix and document that decision.
result: [pending]
why_human: Recorded carry-forward in 08-SUMMARY.md §Deviations §2. Live DB has the fix applied via MCP; no .sql file exists in backend/migrations/. A fresh DB will hit SQLSTATE 42P17 on profiles RLS and block plan 08 setup until 017.sql is replayed manually. This is a persistence/portability decision, not a Phase 1 functional gap — Phase 1's matrix passes 49/0 on the live DB.

### 3. Decide commit-vs-leave for backend/scripts/run_migrations.py
expected: Either commit the file or explicitly mark it intentionally-untracked.
result: [pending]
why_human: Plan 07's canonical apply path requires this script. It is functionally fine on the developer's machine (the MCP path was used as substitute) but not in source control. Carry-forward from 07-SUMMARY.md.

### 4. Triage 5 pre-existing Episode-1 test failures (Hybrid 8/2, Tools 12/2, Sub-Agents 0/1)
expected: Either flip TEST_USER_A.is_admin=true on this Supabase project to match Episode-1 dev convention, OR rewrite test_settings/test_hybrid/test_tools to use TEST_USER_ADMIN for admin-gated calls.
result: [pending]
why_human: Carry-forward from 08-SUMMARY.md §Issues §3. Not Phase 1 regressions (test_two_scope_rls didn't exist before; Hybrid/Tools/Sub-Agents failures are admin-flag drift). Phase 1 introduced no regressions in those suites — verified by full-sweep run.

## Summary

total: 4
passed: 0
issues: 0
pending: 4
skipped: 0
blocked: 0

## Gaps
