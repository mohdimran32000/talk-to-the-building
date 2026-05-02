---
phase: 1
slug: schema-foundation-two-scope-rls-path-normalizer
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-02
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution. Sourced from `01-RESEARCH.md` § Validation Architecture.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | Custom Python harness — `backend/scripts/test_helpers.py` (matches existing `test_rls.py` pattern) |
| **Config file** | None — convention-based (`test_*.py` files in `backend/scripts/`, registered in `test_all.py` SUITES) |
| **Quick run command** | `cd backend && venv/Scripts/python scripts/test_two_scope_rls.py` |
| **Full suite command** | `cd backend && venv/Scripts/python scripts/test_all.py` |
| **Estimated runtime** | ~25–35 seconds (RLS suite alone, no backend HTTP needed — direct Supabase calls) |

---

## Sampling Rate

- **After every task commit:** Run `cd backend && venv/Scripts/python scripts/test_two_scope_rls.py`
- **After every plan wave:** Run `cd backend && venv/Scripts/python scripts/test_two_scope_rls.py`
- **Before `/gsd-verify-work`:** `cd backend && venv/Scripts/python scripts/test_all.py` must be green (no Episode 1 regressions)
- **Max feedback latency:** ~30 seconds per task

---

## Per-Task Verification Map

> Filled in by planner once tasks are numbered. Each row maps task → requirement → automated command. Tasks without an `<automated>` verify block must depend on a Wave 0 test stub.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 1-XX-XX | XX | X | SCHEMA-01 | Pitfall 4 (path drift) | folder_path CHECK rejects non-canonical | unit (SQL) | `cd backend && venv/Scripts/python scripts/test_two_scope_rls.py` | ❌ W0 | ⬜ pending |
| 1-XX-XX | XX | X | SCHEMA-02 | Pitfall 1 (RLS scope-leak) | scope/user_id coupling CHECK rejects mismatch | unit (SQL) | same | ❌ W0 | ⬜ pending |
| 1-XX-XX | XX | X | SCHEMA-03 | — | content_markdown_status CHECK rejects bad enum | unit (SQL) | same | ❌ W0 | ⬜ pending |
| 1-XX-XX | XX | X | SCHEMA-04 | Pitfall 10 (concurrent upload race) | folders unique expr-index rejects duplicate | unit (SQL) | same | ❌ W0 | ⬜ pending |
| 1-XX-XX | XX | X | SCHEMA-05 | Pitfall 3 (grep perf collapse) | EXPLAIN shows Bitmap Index Scan, not Seq Scan | integration (EXPLAIN) | same | ❌ W0 | ⬜ pending |
| 1-XX-XX | XX | X | RLS-01 | Pitfall 1 | cross-user user-scope read isolation | integration | same | ❌ W0 | ⬜ pending |
| 1-XX-XX | XX | X | RLS-02 | Pitfall 1 | non-admin global INSERT rejected | integration | same | ❌ W0 | ⬜ pending |
| 1-XX-XX | XX | X | RLS-03 | Pitfall 1 | UPDATE flipping scope raises check_violation (trigger) | integration | same | ❌ W0 | ⬜ pending |
| 1-XX-XX | XX | X | RLS-04 | Pitfall 1 | cross-user × cross-scope matrix passes 100% | integration | same | ❌ W0 | ⬜ pending |
| 1-XX-XX | XX | X | FOLDER-01 | Pitfall 4 | normalize_path round-trips per spec | unit (Python) | `cd backend && venv/Scripts/python scripts/test_two_scope_rls.py` (or split test_normalize_path.py) | ❌ W0 | ⬜ pending |
| 1-XX-XX | XX | X | TEST-04 | — | test_two_scope_rls.py registered in test_all.py SUITES | integration | `cd backend && venv/Scripts/python scripts/test_all.py` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `backend/scripts/test_two_scope_rls.py` — covers RLS-01..04, SCHEMA-01..05, FOLDER-01 (40 falsifiable assertions per RESEARCH.md)
- [ ] `backend/app/services/folder_service.py` — exports `normalize_path()` (importable by tests)
- [ ] `backend/scripts/test_helpers.py` extension — add `TEST_USER_ADMIN` fixture and `get_admin_token()` helper that uses anon-key + JWT (NOT service-role — service-role bypasses RLS and silently passes broken tests)
- [ ] `backend/scripts/test_all.py` — add `import test_two_scope_rls` and append to `SUITES`
- [ ] One-time setup documented in `test_two_scope_rls.py` docstring: `UPDATE profiles SET is_admin=true WHERE email='admin@test.com';`

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| `CREATE INDEX CONCURRENTLY` upgrade at production scale | SCHEMA-05 | `run_migrations.py` runs each migration in a transaction; CONCURRENTLY cannot run inside a transaction | After Phase 1 lands, document the manual upgrade step in `backend/migrations/README.md`: drop the in-tx index, recreate `CONCURRENTLY` outside a tx during a maintenance window. Out of Phase 1 scope but flagged here for production hand-off. |
| Promoting `admin@test.com` profile.is_admin = true | RLS-02, RLS-04 | `profiles.is_admin` is set by Episode 1's admin gate; test fixture creation is a one-time DB poke, not part of the test suite | Documented in test docstring; run `UPDATE profiles SET is_admin=true WHERE email='admin@test.com'` once after creating the fixture user via Supabase Auth. |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references (test_two_scope_rls.py, normalize_path export, TEST_USER_ADMIN fixture, test_all.py registration)
- [ ] No watch-mode flags (test scripts are one-shot)
- [ ] Feedback latency < 35s
- [ ] `nyquist_compliant: true` set in frontmatter (after planner fills task IDs)

**Approval:** pending
