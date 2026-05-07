---
phase: 03-folder-service-routers-dedup-extension
plan: 05
subsystem: api
tags: [fastapi, supabase-py, pydantic, rls, storage, folders, dedup, admin-gate]

# Dependency graph
requires:
  - phase: 03 / Plan 01
    provides: FilePatch + DocumentResponse Pydantic v2 schema extensions; Migration 019 RPCs (consumed indirectly via Plan 02 service wrappers but only document.update path here)
  - phase: 03 / Plan 03
    provides: record_manager.determine_action(scope=, folder_path=) extended kwargs (FOLDER-05 dedup key)
  - phase: 03 / Plan 02
    provides: folder_service.normalize_path() — already shipped Phase 1, re-imported here at the new chokepoint
  - phase: 02 / Plan 01
    provides: _upload_to_storage helper + Migration 018 storage RLS policies — unchanged but now called with the storage_user_segment ('global' for admin global uploads) per Pitfall F
  - phase: 01 / Plan 02
    provides: Migration 012 documents.scope/folder_path columns + coupling CHECK (scope='global' requires user_id IS NULL) + scope-aware unique index
  - phase: 01 / Plan 05
    provides: Migration 015 forbid_scope_mutation trigger (RLS bedrock for scope immutability that this plan defends in three layers)
provides:
  - "FOLDER-07 (extended files router): POST /api/files/upload accepts folder_path + scope query args + admin gate + storage_user_segment; PATCH /api/files/{file_id} for rename + folder move with admin gate for global-scope rows"
  - "Three-layer scope-immutability defense: FilePatch model omits scope (Pydantic v2 silent-drop) + handler builds update_data only from explicit fields + Migration 015 trigger bedrock"
  - "Pitfall F mitigation pattern: storage_user_segment string variable carries 'global' literal when scope='global' (avoids 'documents/None/{id}{ext}' which Storage RLS rejects) — service-role bypasses RLS for admin reads"
  - "scope=scope, folder_path=folder_path kwargs upgrade at the upload-handler call site: same file at two different paths now creates two rows (FOLDER-05 acceptance via the upload path)"
affects: [Phase 3 / Plan 06 (test_folders.py asserts FOLDER-07 happy path + admin gate + scope smuggling defense end-to-end), Phase 4 (tools), Phase 6 (UI upload + drag-move call sites)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Body-conditional inline admin gate at the router boundary (mirror of auth.py:46-51) used WHEN scope=='global' on POST and WHEN existing.scope=='global' on PATCH — Depends(get_admin_user) cannot express the conditional because it evaluates BEFORE body parsing / DB lookup"
    - "Pitfall F mitigation: storage_user_segment string variable ('global' literal for global scope; user_id UUID otherwise) — passed to _upload_to_storage to avoid the documented Storage path foot-gun where None segment produces 'documents/None/{id}{ext}'"
    - "Three-layer scope-immutability defense (Pydantic silent-drop -> explicit update_data dict building -> Migration 015 trigger) — first concrete instance of the pattern documented in 03-RESEARCH.md §Pitfall B"
    - "FOLDER-05 acceptance via the upload path: same file uploaded to two different folder_paths now creates two distinct documents rows because determine_action filters on (scope, user_id, folder_path, file_name) — verified at the router/service boundary via kwargs upgrade"

key-files:
  created: []
  modified:
    - "backend/app/routers/files.py — extended upload_file handler (folder_path/scope query args, normalize, admin gate, effective_user_id + storage_user_segment, determine_action kwargs, scope+folder_path in documents.insert) + appended new patch_file handler (FilePatch body, lookup-then-gate-then-act, normalize, empty-update 400, UPDATE + re-SELECT + return)"

key-decisions:
  - "Used regex= form (NOT pattern=) on Query() to match the verifier gate's literal-substring check for ^(user|global)$ AND to stay consistent with folders.py:39's prior choice; FastAPI emits a deprecation warning but the form is still valid in current FastAPI versions — pinning the exact substring takes precedence over stylistic up-to-dateness"
  - "Passed storage_user_segment as the user_id kwarg of _upload_to_storage rather than refactoring the helper signature — the helper's f-string '{user_id}/{document_id}{ext}' works identically whether the segment is a UUID or the literal 'global'; refactoring would have rippled to ingestion.py callers and Plan 02 setup"
  - "Did NOT touch the action='update' branch's update_data dict for scope/folder_path — content updates preserve the existing row's metadata; folder moves are a separate operation owned by PATCH /api/files/{file_id}"
  - "Did NOT add .eq('user_id', user_id) to the patch_file lookup — admins must be able to PATCH global-scope rows (where user_id IS NULL); RLS handles user-scope isolation; admin gate handles the global-scope case (mirror of folders.py rename/delete pattern)"

patterns-established:
  - "Body-conditional inline admin gate (WHEN scope=='global'): mirrors folders.py from Plan 04 verbatim; new instance for upload + PATCH"
  - "Three-layer scope-immutability defense (Pydantic + dict building + DB trigger): first concrete instance — pattern reusable for any future Patch model that touches a Migration-015-immutable column"
  - "Pitfall F mitigation via storage_user_segment string variable: keep the Storage path well-formed (no None) by computing the segment at the router boundary"
  - "Lookup-then-gate-then-act for row-targeted endpoints (mirrors folders.py PATCH/DELETE): .maybe_single() lookup -> 404 fast-fail -> admin gate based on row.scope -> service call"
  - "Metadata-only PATCH discipline: rename/move endpoint does NOT call _upload_to_storage and does NOT trigger background_tasks.add_task(_throttled_ingest, ...) — chunks/embeddings/content_markdown remain valid; the Storage object stays at the same {user_id}/{doc_id}{ext} path"

requirements-completed: [FOLDER-07]

# Metrics
duration: ~3min
completed: 2026-05-07
---

# Phase 03 Plan 05: Files Router Extensions Summary

**POST /api/files/upload now accepts folder_path + scope query args with body-conditional admin gate + Pitfall F storage_user_segment mitigation + Plan 03 dedup-key kwargs upgrade; new PATCH /api/files/{file_id} endpoint supports rename + folder move with row-conditional admin gate and three-layer scope-immutability defense.**

## Performance

- **Duration:** ~3 min
- **Started:** 2026-05-07T10:34:14Z
- **Completed:** 2026-05-07T10:36:45Z
- **Tasks:** 2
- **Files modified:** 1

## Accomplishments
- Extended `POST /api/files/upload` with `folder_path: str = Query("/")` and `scope: str = Query("user", regex="^(user|global)$")` query parameters; normalize_path() at the router boundary (Pitfall 4 belt); inline admin gate (mirror of `auth.py:46-51`) when `scope=='global'` returning 403 for non-admins.
- Computed `effective_user_id` (None for global per Migration 012 coupling CHECK) and `storage_user_segment` ('global' literal for global, user_id UUID for user) — Pitfall F mitigation that keeps the Storage path well-formed.
- Upgraded the `determine_action()` call site to pass `scope=scope, folder_path=folder_path` kwargs (Plan 03's extension) — FOLDER-05 dedup-key acceptance via the upload path.
- Documents.insert in the create branch now includes `scope` and `folder_path` columns alongside the existing fields.
- Added new endpoint `PATCH /api/files/{file_id}` with `FilePatch` body — supports rename (`file_name`) and folder move (`folder_path`) with row-conditional admin gate when `existing.scope=='global'`; rejects empty update_data with 400; normalizes `folder_path` (Pitfall 4 belt); returns the updated `DocumentResponse` via re-SELECT after UPDATE.
- `FilePatch` deliberately omits `scope` field — Pydantic v2 silently drops unknown fields, so a smuggled `{"scope": "global"}` in the request body never reaches the handler. Combined with the explicit `update_data` dict building (only `file_name` and `folder_path` fields), this is the second layer of defense; Migration 015's `forbid_scope_mutation` trigger is the bedrock.

## Task Commits

Each task was committed atomically:

1. **Task 1: Extend upload_file with folder_path/scope query args + admin gate + storage_user_segment + determine_action kwargs** — `6fdbdef` (feat)
2. **Task 2: Add PATCH /api/files/{file_id} endpoint for rename + folder move** — `60da21c` (feat)

**Plan metadata:** _to be created in final commit (this SUMMARY + STATE.md + ROADMAP.md)_

## Files Created/Modified
- `backend/app/routers/files.py` — extended `upload_file` handler (signature + normalize + admin gate + effective_user_id + storage_user_segment + determine_action kwargs + scope/folder_path in documents.insert) and appended new `patch_file` handler (FilePatch body + lookup-then-gate-then-act + normalize + empty-update 400 + UPDATE + re-SELECT + return). Imports extended: `Query`, `get_user_profile`, `FilePatch`, `normalize_path`. Existing `_ingestion_semaphore`, `_throttled_ingest`, `_upload_to_storage` helpers unchanged. Existing `list_files` (GET) and `delete_file` (DELETE) endpoints unchanged. Total /api/files routes 3 → 4; total app routes 22 → 23.

## Decisions Made
- Kept `regex=` form (not `pattern=`) on Query() to match the verifier-gate literal-substring check AND to stay consistent with `folders.py:39`'s prior choice; FastAPI's deprecation warning is acknowledged but the form is still valid in the project's pinned FastAPI version.
- Passed `storage_user_segment` as the `user_id` kwarg of `_upload_to_storage` (no helper signature refactor) — the helper's f-string `{user_id}/{document_id}{ext}` works identically whether the segment is a UUID or the literal 'global'; refactoring would have rippled to ingestion.py callers and Plan 02 setup pre-req.
- Did NOT modify the `action == "update"` branch's `update_data` dict for `scope`/`folder_path` — content updates preserve the existing row's metadata; folder moves are owned exclusively by `PATCH /api/files/{file_id}` (consistent with Migration 015's scope immutability and the FilePatch contract).
- Did NOT add `.eq('user_id', user_id)` to the `patch_file` lookup — admins must be able to PATCH global-scope rows (where `user_id IS NULL`); RLS handles user-scope isolation; admin gate handles the global-scope case. This mirrors the lookup pattern in `folders.py` PATCH/DELETE.

## Deviations from Plan

None — plan executed exactly as written. Both task verifier gates PASSED on first attempt; runtime route inspection confirmed all 4 /api/files routes mounted (POST upload, GET list, PATCH rename/move, DELETE delete). The deprecation warning emitted by `Query(..., regex=...)` is environmental (FastAPI prefers `pattern=` in newer minor releases) and does NOT change the runtime semantics; the project's existing `folders.py` already uses the same `regex=` form, so consistency takes precedence over chasing the deprecation.

## Issues Encountered

- **Module-load env-var ordering:** `python -c "from app.routers import files"` initially failed with `ValueError: No API key was provided` because `app.services.ingestion:22` instantiates `genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))` at module-import time. Resolved by prepending `from dotenv import load_dotenv; load_dotenv();` to the verification command — same Phase 2 / Plan 03 convention. NOT a defect of this plan; documented as `Phase 2 / Plan 03 (executed): Module-load env-var ordering convention` in STATE.md decisions. Did not require any code change.

## User Setup Required

None — no external service configuration required. Migrations 012, 015, 018, 019 already live; profiles.is_admin already populated for the admin user from Phase 1; documents bucket already provisioned during Phase 2 / Plan 04.

## Next Phase Readiness

- **Plan 06 unblocked:** test_folders.py integration suite can now exercise FOLDER-07 end-to-end:
  - Happy path: `POST /api/files/upload?folder_path=/a&scope=user` → documents row has folder_path='/a' and scope='user'
  - Admin gate: `POST /api/files/upload?scope=global` as non-admin → 403; as admin → 200 with effective_user_id=None, Storage path 'documents/global/{id}{ext}'
  - PATCH rename: `PATCH /api/files/{id}` with `{file_name: 'new.txt'}` → 200, file_name updated
  - PATCH move: `PATCH /api/files/{id}` with `{folder_path: '/b'}` → 200, folder_path updated
  - PATCH empty: `PATCH /api/files/{id}` with `{}` → 400 'No fields to update'
  - PATCH scope smuggling: `PATCH /api/files/{id}` with `{scope: 'global'}` → 200 (Pydantic ignores) AND row's scope unchanged (Migration 015 bedrock not even reached because Pydantic drops first)
  - FOLDER-05 acceptance via upload: same file uploaded to /a then /b → 2 distinct documents rows
  - Pitfall 10 concurrent-upload: 10 parallel POST with same folder_path → exactly 0 folders rows at that path (Strategy B locked from Plan 01 RPC)
- **Phase 3 progress:** 5/6 plans complete (Wave 1 complete, Wave 2 complete, Wave 3 complete; Wave 4 = Plan 06 next).

---
*Phase: 03-folder-service-routers-dedup-extension*
*Completed: 2026-05-07*

## Self-Check: PASSED

- FOUND: backend/app/routers/files.py (modified, 238 lines after both tasks vs ~165 before)
- FOUND commit 6fdbdef (Task 1: feat(03-05): extend upload_file with folder_path/scope query args + admin gate)
- FOUND commit 60da21c (Task 2: feat(03-05): add PATCH /api/files/{file_id} for rename + folder move)
- AST gate Task 1 PASSED (folder_path Query, scope Query with regex, normalize_path call, admin gate, effective_user_id/storage_user_segment, determine_action kwargs, scope+folder_path in insert, all imports present)
- AST gate Task 2 PASSED (PATCH decorator, patch_file function, FilePatch body, no-fields-400, normalize_path on body.folder_path, admin gate text, exactly 1 patch + 1 delete + 1 get + 1 post decorator)
- Runtime route inspection PASSED (4 routes: POST /api/files/upload, GET /api/files, DELETE /api/files/{file_id}, PATCH /api/files/{file_id})
- Total app route count grew 22 → 23 (+1 = the new PATCH endpoint, matching success criterion)
- No file deletions in either commit
- Module imports cleanly via venv Python (with .env loaded per project convention)
