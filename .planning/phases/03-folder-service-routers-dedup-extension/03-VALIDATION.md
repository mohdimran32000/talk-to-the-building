---
phase: 3
slug: folder-service-routers-dedup-extension
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-07
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from `03-RESEARCH.md` §Validation Architecture.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | Custom Python test suite (matches `test_helpers.py` + `test_all.py`) |
| **Config file** | `backend/scripts/test_all.py` SUITES list |
| **Quick run command** | `cd backend && venv/Scripts/python scripts/test_folders.py` |
| **Full suite command** | `cd backend && venv/Scripts/python scripts/test_all.py` |
| **Estimated runtime** | ~30 sec single-suite (warm backend); ~3 min full suite |

**Pre-reqs:** Backend on `localhost:8001`; `.env` with `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `GEMINI_API_KEY`; admin@test.com promoted via `UPDATE public.profiles SET is_admin=true WHERE email='admin@test.com'`; Migration 019 applied.

---

## Sampling Rate

- **After every task commit:** Run `cd backend && venv/Scripts/python scripts/test_folders.py` (single-suite, <30s warm)
- **After every plan wave:** Run `cd backend && venv/Scripts/python scripts/test_folders.py` (still single-suite — full suite is the phase gate)
- **Before `/gsd-verify-work`:** Full suite must be green via `cd backend && venv/Scripts/python scripts/test_all.py`
- **Max feedback latency:** ~30 seconds per task

---

## Per-Task Verification Map

> Task IDs are placeholders (`{N}-PP-TT`) until the planner finalizes wave/plan numbering. The Sec. column maps to threats from RESEARCH §Security Domain. Status fields update during execution.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 3-01-* | 01 (Migration 019) | 1 | FOLDER-03, FOLDER-04 | T-Pitfall-5 (TOCTOU), T-Pitfall-5 (rollback) | RPC raises check_violation on non-empty; PL/pgSQL transactional rollback | smoke (function existence) + integration (canary) | `cd backend && venv/Scripts/python scripts/test_folders.py` (canary section) | ❌ W0 | ⬜ pending |
| 3-02-* | 02 (folder_service extensions) | 2 | FOLDER-02 | T-path-traversal (Pitfall 4) | normalize_path() chokepoint on every write | unit (import + signature) | `cd backend && venv/Scripts/python scripts/test_folders.py` ([FOLDER-02 service surface]) | ❌ W0 | ⬜ pending |
| 3-03-* | 03 (folders router) | 3 | FOLDER-06 | T-admin-gate, T-cross-user | `Depends(get_admin_user)` for global writes; user_id filter on lookups | integration | `cd backend && venv/Scripts/python scripts/test_folders.py` ([FOLDER-06 router CRUD]) | ❌ W0 | ⬜ pending |
| 3-04-* | 04 (files router extensions) | 3 | FOLDER-07 | T-scope-mutation, T-Pitfall-10 | FilePatch omits scope; trigger fail-safe; Strategy B (no folders write on upload) | integration | `cd backend && venv/Scripts/python scripts/test_folders.py` ([FOLDER-07 files router] + [Pitfall 10 concurrent upload]) | ❌ W0 | ⬜ pending |
| 3-05-* | 05 (record_manager dedup) | 3 | FOLDER-05 | — | Dedup key uses scope-aware unique index | integration | `cd backend && venv/Scripts/python scripts/test_folders.py` ([FOLDER-05 dedup key]) | ❌ W0 | ⬜ pending |
| 3-06-* | 06 (test_folders.py + register) | 4 | TEST-01 | covers all above | All assertions cover SC1..SC5 | smoke (test_all runs) + integration | `cd backend && venv/Scripts/python scripts/test_all.py` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists |
|--------|----------|-----------|-------------------|-------------|
| FOLDER-02 | `list_folder` / `create_folder` / `move_document` / `rename_folder` / `delete_folder` exposed in `folder_service.py` | unit (import + signature check) + integration (router-driven) | `test_folders.py` (section: "FOLDER-02 service surface") | ❌ W0 |
| FOLDER-03 | Rename atomically updates `documents.folder_path` (every descendant) AND `folders.path` via single RPC | integration | `test_folders.py` (section: "FOLDER-03 atomic rename") | ❌ W0 |
| FOLDER-03 (rollback) | Mid-rename failure leaves no partial state | integration with deliberate-fail RPC variant | `test_folders.py` (section: "FOLDER-03 transactional rollback") | ❌ W0 |
| FOLDER-04 | Non-empty delete returns structured `{error, document_count, subfolder_count}` | integration | `test_folders.py` (section: "FOLDER-04 non-empty rejected") | ❌ W0 |
| FOLDER-04 (no-orphan) | Rejected delete leaves all documents in place | integration | `test_folders.py` (section: "FOLDER-04 no-orphan") | ❌ W0 |
| FOLDER-05 | Same file in two folders creates two rows; same file in same folder deduped | integration (upload twice with different `folder_path`, then twice with same) | `test_folders.py` (section: "FOLDER-05 dedup key") | ❌ W0 |
| FOLDER-06 | GET/POST/PATCH/DELETE /api/folders work; admin gate enforced for `scope='global'` writes | integration | `test_folders.py` (section: "FOLDER-06 router CRUD") | ❌ W0 |
| FOLDER-06 (admin-403) | Non-admin POST `scope='global'` returns 403 | integration | `test_folders.py` (section: "FOLDER-06 admin-403") | ❌ W0 |
| FOLDER-07 | POST /api/files/upload accepts `folder_path` + `scope`; PATCH /api/files/{id} for rename + folder move | integration | `test_folders.py` (section: "FOLDER-07 files router extensions") | ❌ W0 |
| FOLDER-07 (concurrent-no-orphan) | 10 parallel uploads to brand-new path produce 0 folders rows (Strategy B) | integration with ThreadPoolExecutor | `test_folders.py` (section: "Pitfall 10 concurrent upload") | ❌ W0 |
| TEST-01 | `test_folders.py` registered as 15th suite in `test_all.py` SUITES list | smoke (test_all.py runs successfully) | `cd backend && venv/Scripts/python scripts/test_all.py` | ❌ W0 |

---

## Success Criteria → Test Map

| SC | Behavior | Test name (in `test_folders.py`) |
|----|----------|----------------------------------|
| SC1 | Routers work end-to-end with admin gate enforced; non-admin → 403 | `[FOLDER-06 router CRUD]` (multiple h.test calls covering create/list/patch/delete + admin-403) |
| SC2 | Folder rename atomically updates documents+folders; mid-rename rollback verified | `[FOLDER-03 atomic rename]` + `[FOLDER-03 transactional rollback]` (uses deliberate-fail RPC variant) |
| SC3 | Non-empty delete returns structured 409 with counts; no docs deleted | `[FOLDER-04 non-empty rejected]` + `[FOLDER-04 no-orphan]` |
| SC4 | Same file in two folders → 2 docs; same path → deduped | `[FOLDER-05 dedup key]` |
| SC5 | POST /api/files/upload accepts query args; PATCH supports rename+move; concurrent-upload no orphan | `[FOLDER-07 files router]` + `[Pitfall 10 concurrent upload]` |

---

## Wave 0 Requirements

- [ ] `backend/scripts/test_folders.py` — covers FOLDER-02..07 + TEST-01 + SC1..SC5
- [ ] `backend/scripts/test_helpers.py` — extend if shared concurrent-upload helper is useful (recommend keeping the 10-thread executor inline in `test_folders.py` for now)
- [ ] Migration 019 applied via `cd backend && venv/Scripts/python scripts/run_migrations.py` BEFORE running `test_folders.py`. The test fixture begins with a canary check that asserts the RPCs (`rename_folder_prefix`, `delete_folder_if_empty`) exist (mirrors `test_two_scope_rls.py::_verify_admin_setup` pattern). Failure mode: single FAIL h.test + early return + actionable [FATAL] message naming Migration 019.
- [ ] `test_all.py` SUITES list — append `("Folders", test_folders)` after `("Files", test_files)` and before `("Backfill", test_backfill)` (folders is logically a Files extension and runs in <30s, so Files → Folders → Backfill is the natural order)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| (none) | — | All Phase 3 behaviors have automated verification | — |

---

## Validation Sign-Off

- [ ] All tasks have automated verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references (Migration 019 applied + test_folders.py created)
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending (set after planner finalizes wave/task IDs and `/gsd-execute-phase` runs Wave 0)
