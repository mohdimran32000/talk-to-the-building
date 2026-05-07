---
phase: 03-folder-service-routers-dedup-extension
fixed_at: 2026-05-07T11:26:34Z
review_path: .planning/phases/03-folder-service-routers-dedup-extension/03-REVIEW.md
iteration: 1
findings_in_scope: 7
fixed: 10
skipped: 0
status: all_fixed
---

# Phase 3: Code Review Fix Report

**Fixed at:** 2026-05-07T11:26:34Z
**Source review:** `.planning/phases/03-folder-service-routers-dedup-extension/03-REVIEW.md`
**Iteration:** 1

## Summary

- **Findings in scope (CRITICAL + HIGH from `fix_scope=critical_warning`):** 7 (CR-01, CR-02, CR-03, HI-01, HI-02, HI-03, HI-04)
- **Fixed:** 10 (the 7 in-scope CRITICAL/HIGH findings + 3 cheap MEDIUM findings the orchestrator context flagged as "include if cheap": MD-01, MD-02, MD-03)
- **Skipped:** 0
- **Status:** `all_fixed`

The three CRITICAL findings shared a single root cause (service-role Supabase client + missing application-level ownership guards on user-scope rows). They were applied as three separate atomic commits (one per finding) for clean traceability. After the CRITICAL trio, all four HIGH findings were addressed. Three MEDIUM findings that were source-only and cheap (MD-01 file_name hygiene; MD-02 maybe_single try/except; MD-03 logger plumbing) were also applied. MEDIUM test-architecture findings (MD-04, MD-05, MD-06) were deferred per orchestrator guidance ("defer if they require new test coverage / test-only changes"). LOW findings deferred to a polish pass — except LO-03 (docstring wording for FOR-UPDATE lock-scope), which was folded into the HI-02 fix because they cover the same docstring.

**Smoke test after fixes:** `cd backend && venv/Scripts/python.exe -c "from app.main import app; print(len(app.routes))"` returns `23` (matches pre-fix count). No import or syntax regressions.

**Worktree note:** The agent attempted to create an isolated worktree per spec but `git worktree add ... master` failed because master is already checked out in the main working tree (the foreground session's branch). Fell back to the main working tree with per-finding atomic commits and pre-fix syntax verification, mitigating the no-isolation risk. No staged changes from the foreground session were touched (git status was checked before each fix to confirm only the fix-modified files were staged).

## Fixed Issues

### CR-01: Cross-user folder DELETE — any user can delete any other user's user-scope folder

**Files modified:** `backend/app/routers/folders.py`
**Commit:** `c72a854`
**Applied fix:** Added explicit ownership guard in `delete_folder_endpoint` after the lookup and before the admin gate: `if folder["scope"] == "user" and folder.get("user_id") != user_id: raise HTTPException(404, "Folder not found")`. Returns 404 (not 403) per the reviewer's note about not leaking existence. Mirrors the safe pattern already used in `routers/files.py:delete_file`.

### CR-02: Cross-user folder PATCH (rename) — any user can rename any other user's user-scope folder

**Files modified:** `backend/app/routers/folders.py`
**Commit:** `ca84e70`
**Applied fix:** Added the same ownership guard in `rename_folder_endpoint` BEFORE the admin gate (so the missing-row 404 is identical for "not yours" and "doesn't exist"). Without this guard, the router would have passed the VICTIM's stored `user_id` from the row into the `rename_folder_prefix` RPC, which would happily rename the victim's documents and folders.

### CR-03: Cross-user document PATCH — any user can rename/move any other user's user-scope document

**Files modified:** `backend/app/routers/files.py`
**Commit:** `971799a`
**Applied fix:** Two-layer fix in `patch_file`. (1) Added the ownership guard after the lookup. (2) Tightened the UPDATE itself to also `.eq("user_id", user_id)` when `existing.scope == "user"` — defense in depth so a TOCTOU between lookup and mutation still cannot cross users. For `scope == "global"`, the admin gate is sufficient (`user_id IS NULL`).

### HI-01: PostgREST `or_()` interpolates user_id into the query DSL — defense-in-depth violation

**Files modified:** `backend/app/services/folder_service.py`, `backend/app/routers/folders.py`
**Commit:** `79c5c71`
**Applied fix:** Added `_assert_uuid()` helper in `folder_service.py` that validates a string is a syntactically valid UUID (or `None`) and raises `ValueError` otherwise. Called from `list_folder()` at the start when `scope in ("user", "both")`. The `list_folders` router catches `ValueError` and surfaces as HTTP 400. Verified with standalone test that valid UUIDs pass and injection-shaped strings (`abc),and(x.eq.1`) are rejected.

### HI-02: `delete_folder_if_empty` FOR-UPDATE lock does not prevent concurrent uploads from re-creating the folder

**Files modified:** `backend/app/services/folder_service.py`, `backend/migrations/019_folder_rename_and_delete_rpcs.sql`
**Commit:** `ec90c47`
**Applied fix:** Chose option (b) from the review (document the actual semantics; do NOT add a heavy `LOCK TABLE documents IN SHARE ROW EXCLUSIVE MODE` that would serialize all uploads during a delete). Updated DESIGN NOTE 2 in the migration header to spell out the exact race interleaving and the Strategy-B-aware acceptance. Updated the `delete_folder` Python docstring to remove the misleading "race-free" / "eliminates the TOCTOU race" claims and replace with accurate semantics. This single fix also addresses LO-03 (which flagged the same docstring).

**Status note:** This is a documentation + semantic-acceptance fix, not a code-behavior fix. A future operator who needs strict serialization with uploads will need to obtain an external lock — documented in the docstring.

### HI-03: `LIKE prefix||'/%'` does not escape `%` and `_` in folder names

**Files modified:** `backend/app/services/folder_service.py`, `backend/migrations/019_folder_rename_and_delete_rpcs.sql`
**Commit:** `71a7054`
**Applied fix:** Added `_escape_like()` helper in `folder_service.py` (escapes `\`, `%`, `_` with `\` — escaping `\` first to avoid double-escaping). Applied to all three `.like()` call sites in `list_folder`. In the SQL migration, added a `v_old_prefix_like` / `v_path_like` local var in both `rename_folder_prefix` and `delete_folder_if_empty` that escapes the dynamic prefix with the same three replacements, and added explicit `ESCAPE '\'` clauses to the LIKE predicates (clearer intent than relying on Postgres's default-escape behavior). Verified with standalone tests: `/foo_bar` -> `/foo\_bar`, `/100%off` -> `/100\%off`, `/a\b` -> `/a\\b`.

**Status note: requires human verification.** The migration changes need to be re-applied to the dev database for the SQL fix to take effect. The Python-side `_escape_like` is already live. The behavior change is correctness-only (no over-matching for folders with literal `_`/`%`), but a rename of an existing `/foo_bar`-style folder before the migration is re-applied could still over-match.

### HI-04: `"updated_at": "now()"` is sent as a string literal — likely silently broken

**Files modified:** `backend/app/routers/files.py`
**Commit:** `34f82ef`
**Applied fix:** Replaced `"updated_at": "now()"` with `"updated_at": datetime.now(timezone.utc).isoformat()` in the `record_action == "update"` branch of `upload_file`. Added the `from datetime import datetime, timezone` import. The fix is unambiguous regardless of whether the table has a column-level trigger.

### MD-01: PATCH /api/files updates `file_name` without normalization or validation

**Files modified:** `backend/app/routers/files.py`
**Commit:** `234994d`
**Applied fix:** Added a `file_name` hygiene check in `patch_file`: rejects `/`, `\`, `\x00`, length > 255, and length == 0 with HTTP 400. Defends downstream consumers (storage path computation via `os.path.splitext`, UI rendering). Does NOT attempt structured-409 mapping for the dedup unique-index collision (deferred to Phase 6 per RESEARCH §Open Questions Q2).

### MD-02: `create_folder` hydration query may hit 204-on-empty raise

**Files modified:** `backend/app/services/folder_service.py`
**Commit:** `e051ce9`
**Applied fix:** Wrapped the `.maybe_single().execute()` hydration call in `create_folder` with try/except, mirroring the idiom from `record_manager.determine_action`. Now returns the canonical id from the RPC even if a concurrent delete races between RPC return and hydration.

### MD-03: `list_folder` swallows ALL exceptions — silent failure on real errors

**Files modified:** `backend/app/services/folder_service.py`
**Commit:** `d3f1cb7`
**Applied fix:** Added `logger = logging.getLogger(__name__)` and replaced the three silent `except Exception:` blocks in `list_folder` with `except Exception as e: logger.error(...)`. Kept the empty-list fallback so partial query failures (one of three independent queries) do not blank the others — but operators now see real failures in logs. Future hardening (narrow to `APIError` and map to 5xx) is documented inline as a TODO.

## Skipped Issues

None. All findings in scope (CR-01, CR-02, CR-03, HI-01, HI-02, HI-03, HI-04) plus three MEDIUM findings that fit the "include if cheap" criterion were applied successfully.

The following are NOT in scope for `fix_scope=critical_warning` and were not applied:

- **MD-04** (`_verify_phase3_setup` probes only one of three RPCs) — test-file change; per orchestrator context "defer if they require new test coverage."
- **MD-05** (test installs PL/pgSQL function side effect) — test-architecture change; deferred.
- **MD-06** (test inserts via service-role bypassing RLS) — test-architecture change; deferred.
- **LO-01** (`_FORBIDDEN_SEGMENTS` does not block Windows reserved names) — polish; deferred to Phase 6.
- **LO-02** (`_upload_to_storage` parameter name `user_id` misleading) — polish; deferred to Phase 6.
- **LO-03** (FOR-UPDATE docstring inaccuracy) — folded into HI-02 fix (same docstring).
- **LO-04** (inline `__main__` self-tests in `folder_service.py`) — polish; deferred to Phase 6.

## Verification Performed

- **Tier 1 (always):** Re-read each modified region after edit; confirmed fix text present and surrounding code intact.
- **Tier 2 (preferred):** Ran `python -c "import ast; ast.parse(open(...).read()); print('OK')"` after each Python edit. All passed.
- **Tier 2 bonus:** Ran `venv/Scripts/python.exe -m app.services.folder_service` after the HI-01 / HI-03 / MD-02 / MD-03 changes — all 15 normalize_path self-tests still pass.
- **Tier 2 bonus:** Wrote a throwaway `_test_escape_like.py` to verify `_escape_like` correctness across `/foo_bar`, `/100%off`, `/a\b`, and clean paths. All pass. File deleted before commit.
- **End-of-run smoke:** `cd backend && venv/Scripts/python.exe -c "from app.main import app; print(len(app.routes))"` returns `23` — same as pre-fix.

## Recommended Follow-up

1. **Re-apply migration 019** to the dev / staging databases. The HI-02 docstring change is metadata-only, but the HI-03 LIKE-escape change is a behavior fix that requires `CREATE OR REPLACE FUNCTION` to take effect.
2. **Add regression tests** in `backend/scripts/test_folders.py`:
   - User A's PATCH/DELETE on user B's folder must return 404 (CR-01, CR-02).
   - User A's PATCH on user B's document must return 404 (CR-03).
   - PATCH `/api/files/{id}` with `file_name` containing `/` returns 400 (MD-01).
   - PATCH `/api/files/{id}` with valid file_name updates `updated_at` to a recent timestamp (HI-04).
   - Rename / delete of `/foo_bar` does not affect `/fooXbar/baz` (HI-03).
3. **HI-01 router-side validator:** Consider promoting `_assert_uuid` into a router-level `Annotated[str, AfterValidator(...)]` dependency so all router endpoints get UUID validation for free. Current fix validates at the service-layer entry point only.
4. **MD-04..MD-06**: Address in a follow-up phase or as part of Phase 6 polish — they are test-architecture improvements, not correctness fixes.

---

_Fixed: 2026-05-07T11:26:34Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
