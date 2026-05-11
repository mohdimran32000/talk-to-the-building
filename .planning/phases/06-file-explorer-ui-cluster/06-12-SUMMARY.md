---
phase: 06-file-explorer-ui-cluster
plan: 12
subsystem: backend/folder-api
tags: [phase6, backend, folder-api, wave0, d-06]
dependency-graph:
  requires:
    - "Migration 019 (rename_folder_prefix, delete_folder_if_empty, create_folder_if_not_exists) — Phase 3"
    - "backend/app/routers/folders.py registered in main.py — Phase 3 / Plan 03-04"
    - "backend/app/services/folder_service.list_folder (Phase 3 / Plan 03-02)"
  provides:
    - "GET /api/folders subfolders[].id (UUID | null) wire contract for Plans 06-05/06-06/06-09"
    - "FolderRef + FolderListResponse Pydantic models"
    - "folder_service.list_folder() returns subfolders as List[{id, path}]"
  affects:
    - "backend/app/services/folder_service.py — list_folder return shape"
    - "backend/app/models/schemas.py — +2 models, +List import"
    - "backend/app/routers/folders.py — GET response_model annotation"
    - "Plan 06-05 (api.ts types) can now declare subfolders: Array<{id: string|null, path: string}>"
    - "Plan 06-06 (FolderNode) can wire folderId: string|null through props"
    - "Plan 06-09 (DELETE /api/folders/{id} wiring) — blocker closed end-to-end"
tech-stack:
  added: []
  patterns:
    - "Wire-shape upgrade: bare-string list -> typed-object list with id round-trip"
    - "Inferred-vs-explicit distinction surfaced to frontend via id=null sentinel"
    - "FastAPI response_model retro-annotation for type discipline (no behavior change)"
key-files:
  created:
    - backend/scripts/test_folders_subfolder_id.py
  modified:
    - backend/app/services/folder_service.py
    - backend/app/models/schemas.py
    - backend/app/routers/folders.py
decisions:
  - "id: Optional[str] (not required) — inferred-only subfolders (no folders row) carry id=None so the frontend can disable rename/delete affordances on them"
  - "FastAPI auto-coerces the dict returned by list_folder() to FolderListResponse via the response_model decorator — no manual mapping in the router endpoint"
  - "test_all.py registration deferred per plan spec — operator confirms test runs in isolation first"
  - "Test uses TEST_USER_B (test@test.com) per plan spec; resource paths suffixed with uuid4().hex[:8] to avoid collisions across parallel/repeat runs"
metrics:
  duration_minutes: 8
  completed_date: 2026-05-11
  tasks: 3
  files_touched: 4
  commits: 3
---

# Phase 06 Plan 12: Folder-id resolution gap (D-06) Summary

**One-liner:** Extends `GET /api/folders` so `subfolders[]` returns typed `{id: UUID | null, path: string}` objects instead of bare path strings, closing the plan-checker blocker on Plan 06-09 (DELETE /api/folders/{id} wiring) by giving the frontend the UUID it needs without a separate path→id lookup endpoint.

## What changed

### 1. `folder_service.list_folder` return shape

**Before:**
```py
explicit_subfolders: list[str] = []
...
f_q = supabase_client.table("folders").select("path")
...
explicit_subfolders = [row["path"] for row in (f_resp.data or [])]
...
all_subfolders = sorted(set(explicit_subfolders) | inferred_subfolders)
return {
    "path": norm,
    "documents": documents,
    "subfolders": all_subfolders,   # list[str]
}
```

**After:**
```py
explicit_subfolders: list[dict] = []   # each item: {"id": <uuid str>, "path": <str>}
...
f_q = supabase_client.table("folders").select("id, path")
...
explicit_subfolders = [
    {"id": row["id"], "path": row["path"]}
    for row in (f_resp.data or [])
]
...
explicit_by_path: dict[str, str] = {f["path"]: f["id"] for f in explicit_subfolders}
all_paths = sorted(set(explicit_by_path.keys()) | inferred_subfolders)
all_subfolders = [
    {"id": explicit_by_path.get(p), "path": p}
    for p in all_paths
]
return {
    "path": norm,
    "documents": documents,
    "subfolders": all_subfolders,   # list[{"id": str | None, "path": str}]
}
```

Inferred-only subfolders (paths discovered from `documents.folder_path` with no matching `folders` row) carry `id: None`. The frontend uses this sentinel to disable rename/delete affordances on them — the contract for ghost folders that materialize from documents alone.

Only the `subfolders` value type changed. Documents query, inferred-subfolders set construction, scope handling (`user` / `global` / `both`), UUID-injection defense, and LIKE-escape logic are all untouched.

### 2. Pydantic models (backend/app/models/schemas.py)

Added two models at the folder-shape grouping point (after `RenameFolderResponse`):

```py
class FolderRef(BaseModel):
    """Lightweight folder reference returned by GET /api/folders subfolders[]."""
    id: Optional[str] = None
    path: str


class FolderListResponse(BaseModel):
    """Response model for GET /api/folders (D-06: subfolders is List[FolderRef])."""
    path: str
    documents: List[DocumentResponse]
    subfolders: List[FolderRef]
```

`from typing import Optional` was upgraded to `from typing import List, Optional` (typing.List was not previously imported in this file). No existing models touched. The earlier Plan 06-01 addition (`content_markdown_status: Optional[str] = None` on `DocumentResponse`, schemas.py line 42) was preserved verbatim.

### 3. Router annotation (backend/app/routers/folders.py)

```py
# Import (multi-line block now includes FolderListResponse)
from app.models.schemas import (
    FolderResponse,
    FolderCreate,
    FolderPatch,
    RenameFolderResponse,
    FolderListResponse,
)

# Decorator
@router.get("", response_model=FolderListResponse)
async def list_folders(...):
    ...
```

The endpoint body is unchanged. FastAPI coerces the `dict` returned by `list_folder()` into `FolderListResponse` automatically; each subfolder dict (`{"id": ..., "path": ...}`) becomes a `FolderRef` via Pydantic validation.

### 4. Pytest (backend/scripts/test_folders_subfolder_id.py)

New 210-line test, runnable standalone:
```
cd backend && venv/Scripts/python scripts/test_folders_subfolder_id.py
```

Test flow:
1. Canary precheck (`_verify_setup`) — GET /api/folders must respond non-404; backend must be reachable on `http://localhost:8001`. Single FAIL + early return on canary failure (mirrors the Plan 03-06 canary pattern).
2. Authenticate as `test@test.com` via `h.get_auth_token(h.TEST_USER_B["email"], h.TEST_USER_B["password"])`.
3. Create parent folder `/d06-test-parent-<uuid8>` (path suffixed to avoid collisions on repeat / parallel runs).
4. Create nested subfolder `/d06-test-parent-<uuid8>/child`.
5. GET `/api/folders?path=<parent>&scope=user` and assert:
   - `subfolders is a list`
   - `len(subfolders) >= 1`
   - The entry with `path == child_path` is locatable (deterministic alpha-sorted output)
   - `isinstance(ours, dict)` (not a bare string — wire-shape regression guard)
   - `"id" in ours and "path" in ours`
   - `isinstance(ours["id"], str)` and `UUID_RE.match(ours["id"])` (8-4-4-4-12 hex)
   - `ours["id"] == child_id` (round-trip consistency with POST response)
   - `ours["path"] == child_path`
6. Cleanup (in `finally`): DELETE child by id, then DELETE parent by id. Per CLAUDE.md no blanket deletes — only the two ids returned from steps 3+4.

Verbatim assertion list (the strings that `h.test()` records as test names):
- `"Create parent folder returns 2xx"`
- `"Parent folder has id"`
- `"Create child folder returns 2xx"`
- `"Child folder has id"`
- `"GET /api/folders returns 200"`
- `"subfolders is a list"`
- `"subfolders has at least 1 entry"`
- `"subfolder with path=... present"`
- `"subfolder is a dict (not bare string)"`
- `"subfolder has id key"`
- `"subfolder has path key"`
- `"subfolder id is a string"`
- `"subfolder id matches UUID format (36-char hex with dashes)"`
- `"subfolder id matches the id returned by POST"`
- `"subfolder path matches expected"`

## Operator smoke (manual — DEFERRED)

Per the plan's verification block, the operator should run:
```bash
JWT=$(curl ... | jq -r .access_token)  # test@test.com token
curl -H "Authorization: Bearer $JWT" \
  "http://localhost:8001/api/folders?path=/d06-test-parent-<uuid>&scope=user" \
  | jq '.subfolders[0]'
```
Expected output (post-test-run, while the test is between POST and the cleanup): an object with `id` (UUID string) and `path` (string), not a bare string. This SUMMARY does not capture a live `curl | jq` snapshot — the backend was NOT restarted as part of this plan and the same operator-pre-req gap recorded in 03-06-SUMMARY may still gate the live smoke. Test file is committed (parses, statically validated); operator runs `scripts/test_folders_subfolder_id.py` standalone once the backend is on a freshly-restarted uvicorn with the new schema models loaded.

## test_all.py registration

**Deferred.** Per the plan's task-3 action block: "Do NOT register this in test_all.py automatically — leave registration as a follow-up so the operator can confirm the test runs in isolation first." Operator can append `('subfolder_id', 'test_folders_subfolder_id', 'D-06 wire contract')` to `backend/scripts/test_all.py` SUITES once the standalone run is green.

## Tasks completed

| # | Task                                                                  | Commit  | Files                                                                                       |
| - | --------------------------------------------------------------------- | ------- | ------------------------------------------------------------------------------------------- |
| 1 | Extend folder_service.list_folder to include subfolder IDs            | afa70e8 | backend/app/services/folder_service.py                                                      |
| 2 | Add FolderRef + FolderListResponse models; wire response_model        | 35697c8 | backend/app/models/schemas.py, backend/app/routers/folders.py                               |
| 3 | Pytest verifying GET /api/folders returns id for nested subfolders    | ee3d20c | backend/scripts/test_folders_subfolder_id.py                                                |

## Acceptance criteria (per task)

**Task 1:**
- ✅ `grep -q '"id, path"' backend/app/services/folder_service.py` → line 197
- ✅ `grep -q 'explicit_by_path' backend/app/services/folder_service.py` → line 261
- ✅ `grep -nE '"subfolders"\s*:\s*all_subfolders' backend/app/services/folder_service.py` → line 271
- ✅ `python -c "import ast; ast.parse(open('app/services/folder_service.py').read())"` → 0
- ✅ Single `def list_folder` (not duplicated)

**Task 2:**
- ✅ `grep -q "class FolderRef" backend/app/models/schemas.py`
- ✅ `grep -q "class FolderListResponse" backend/app/models/schemas.py`
- ✅ `grep -q "FolderListResponse" backend/app/routers/folders.py`
- ✅ `grep -q "response_model=FolderListResponse" backend/app/routers/folders.py`
- ✅ Import test: `from app.models.schemas import FolderRef, FolderListResponse` → 0
- ✅ Existing imports unchanged: `DocumentResponse, FolderResponse, FolderCreate, FolderPatch, FilePatch, RenameFolderResponse` all still importable

**Task 3:**
- ✅ `test -f backend/scripts/test_folders_subfolder_id.py`
- ✅ References `subfolders` (29 grep hits across subfolders/UUID/DELETE/delete tokens)
- ✅ UUID regex validation present (`UUID_RE`, `0-9a-f`)
- ✅ DELETE cleanup branch present (`_delete_folder(child_id, headers)` + parent)
- ✅ `python -c "import ast; ast.parse(...)"` → 0
- ⏳ Operator smoke (manual, deferred — backend not restarted in this plan)

## Deviations from Plan

None — plan executed exactly as written.

The plan's `<important>` callout flagged a potential collision with Plan 06-01's recently-added `content_markdown_status` field on `DocumentResponse`. I read schemas.py before editing; the field is at line 42 and was not in the edit region (Edit A only added `FolderRef` + `FolderListResponse` AFTER `RenameFolderResponse`, untouched lines preserved). No regression.

## Threat Flags

None — no new network endpoints, no auth-path changes, no schema changes at trust boundaries. The shape change is a wire-format upgrade on an existing authenticated endpoint, and the underlying SQL filter set (scope + user_id discrimination, LIKE-escape, UUID validation) is unchanged.

## Known Stubs

None. The endpoint returns real data from the live folders table (extended SELECT) joined with the existing inferred-subfolders computation; no placeholder values, no hardcoded empties, no "coming soon" text. The `id: None` sentinel for inferred-only folders is a deliberate semantic value (D-06 design decision documented in 06-CONTEXT.md), not a stub.

## Self-Check: PASSED

Files exist:
- ✅ `backend/app/services/folder_service.py` (modified)
- ✅ `backend/app/models/schemas.py` (modified)
- ✅ `backend/app/routers/folders.py` (modified)
- ✅ `backend/scripts/test_folders_subfolder_id.py` (created)

Commits exist (verified via `git log --oneline -5`):
- ✅ `afa70e8 feat(06-12): extend folder_service.list_folder to return subfolder ids`
- ✅ `35697c8 feat(06-12): add FolderRef + FolderListResponse models; wire into GET /api/folders`
- ✅ `ee3d20c test(06-12): add pytest verifying GET /api/folders subfolders[].id (D-06)`
