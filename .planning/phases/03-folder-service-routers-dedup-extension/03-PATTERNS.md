# Phase 3: Folder Service + Routers + Dedup Extension — Pattern Map

**Mapped:** 2026-05-07
**Phase:** 03-folder-service-routers-dedup-extension
**Files analyzed:** 9 (3 created, 6 modified)
**Analogs found:** 9 / 9 (every Phase 3 file has a strong codebase analog — no NEW PATTERNS this phase, unlike Phase 2's Storage upload + argparse)

This map identifies, per planned file, the closest existing analog in the codebase and extracts paste-ready code excerpts for the planner. Phase 3 is exceptionally well-anchored: every new file mirrors an existing sibling's shape, every modification has a precise insertion point with surrounding context already in the file. Two PL/pgSQL function patterns are notable — `match_document_chunks_hybrid` (Migration 008/011) is the cross-table-RPC analog; `is_admin()` + `forbid_scope_mutation()` (Migration 015) are the in-PL/pgSQL-validation analogs.

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `backend/migrations/019_folder_rename_and_delete_rpcs.sql` | migration (DDL + PL/pgSQL functions) | DB transaction (cross-table) | `backend/migrations/008_hybrid_search.sql` (PL/pgSQL function shape) + `backend/migrations/015_two_scope_rls.sql` (validation/raise pattern) + `backend/migrations/013_folders_table.sql` (unique constraint shape) | role-match (no prior cross-table-write RPC; existing RPCs are read-only) |
| `backend/app/routers/folders.py` | controller (CRUD router) | request-response | `backend/app/routers/threads.py` (4-endpoint CRUD shape) + `backend/app/routers/files.py` (admin gate + RPC invocation) | exact (threads.py is structurally identical) |
| `backend/scripts/test_folders.py` | test (integration, multi-suite) | request-response + DB read-back + concurrent | `backend/scripts/test_files.py` (upload + poll + assertion) + `backend/scripts/test_two_scope_rls.py` (RLS matrix + scoped cleanup) + `backend/scripts/test_backfill.py` (canary + subprocess + service-role direct DB) | exact (composite of three established suites) |
| `backend/app/services/folder_service.py` (modify) | service | DB CRUD + RPC | self (extend after `normalize_path()` at L67) + `backend/app/services/record_manager.py` (function shape with injected supabase_client) | exact (in-place extension of existing module) |
| `backend/app/services/record_manager.py` (modify) | service | DB SELECT (dedup query) | self (extend `determine_action()` at L27-69) | exact (in-place edit of existing function) |
| `backend/app/routers/files.py` (modify) | controller | request-response (multipart + Query args) | self (L60-145 — add Query args + new PATCH endpoint after existing endpoints) | exact (in-place extension) |
| `backend/app/main.py` (modify) | config (router registration) | n/a | self (L8 import + L23 include_router) | exact (mechanical addition) |
| `backend/app/models/schemas.py` (modify) | model (Pydantic) | n/a | self (L6-44 — `ThreadCreate`, `DocumentResponse` for naming + style) | exact (in-place extension) |
| `backend/scripts/test_all.py` (modify) | config (test registration) | n/a | self (L17 import + L33 SUITES entry — mirror Phase 2's `test_backfill` registration) | exact (mechanical addition) |

---

## Pattern Assignments

### `backend/migrations/019_folder_rename_and_delete_rpcs.sql` (migration, cross-table RPC)

**Primary analog:** `backend/migrations/008_hybrid_search.sql` (PL/pgSQL function with multi-statement body returning a TABLE)
**Secondary analog:** `backend/migrations/015_two_scope_rls.sql` (header comment style + `RAISE EXCEPTION ... USING ERRCODE = 'check_violation'` + idempotent DROP-then-CREATE)
**Tertiary analog:** `backend/migrations/013_folders_table.sql` (unique expression index shape — referenced by `create_folder_if_not_exists` if shipped)

**Header comment block pattern** (copy from `015_two_scope_rls.sql:1-22`):

```sql
-- Phase 3 / Migration 019: Folder rename + delete-if-empty PL/pgSQL functions.
-- Bundles the two cross-table-transactional RPCs Phase 3's folders router needs.
-- Both are colocated here (vs. separate migration files) because they share
-- PL/pgSQL idiom and review surface; mirrors Phase 1's bundling of the full
-- RLS catalog into Migration 015.
--
-- DESIGN NOTES:
-- 1. rename_folder_prefix wraps two UPDATEs (documents + folders) in a single
--    PL/pgSQL block — implicitly transactional. PostgREST executes each
--    .execute() in its own transaction; an RPC is the only cross-table-atomic
--    unit available from supabase-py.
-- 2. delete_folder_if_empty uses FOR UPDATE on the folders row to eliminate
--    the TOCTOU race between count-check and delete. Standard MVCC: row-level
--    write lock blocks concurrent UPDATE/DELETE but not SELECT (see 03-
--    RESEARCH.md §Folder Delete Implementation, A4).
-- 3. SECURITY INVOKER (the default) — RLS policies on documents / folders
--    apply to function execution. Router-level Depends(get_admin_user) is the
--    first line of defense for global-scope writes; RLS is the second.
-- 4. RAISE EXCEPTION ... USING ERRCODE = 'check_violation' mirrors the
--    forbid_scope_mutation pattern from migration 015:48-51.
-- 5. GRANT EXECUTE ... TO authenticated mirrors the is_admin() grant pattern
--    from migration 015:35.
```

**Convention to copy:**
- Triple-line `-- ──` section dividers (matches 015 throughout).
- Section numbering (`-- ── 1.`, `-- ── 2.`) for reviewable navigation.
- Explicit cross-references to the Phase / Migration source (e.g., "mirrors migration 015:48-51").

**Function definition pattern — the rename RPC** (assemble from `008_hybrid_search.sql:28-77` + `015_two_scope_rls.sql:44-55`):

The RESEARCH.md §Folder Rename RPC Design provides the exact body. Copy that body verbatim. The skeleton follows the codebase convention shape:

```sql
CREATE OR REPLACE FUNCTION public.rename_folder_prefix(
  p_old_prefix TEXT,
  p_new_prefix TEXT,
  p_scope      TEXT,
  p_user_id    UUID DEFAULT NULL
)
RETURNS TABLE (documents_updated INT, folders_updated INT)
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
DECLARE
  v_doc_count    INT;
  v_folder_count INT;
BEGIN
  -- Validate canonical form (defense in depth alongside CHECK constraints from migration 012/013)
  IF p_old_prefix !~ '^/$|^/[^/]+(/[^/]+)*$' THEN
    RAISE EXCEPTION 'old_prefix not canonical: %', p_old_prefix
      USING ERRCODE = 'check_violation';
  END IF;
  IF p_new_prefix !~ '^/$|^/[^/]+(/[^/]+)*$' THEN
    RAISE EXCEPTION 'new_prefix not canonical: %', p_new_prefix
      USING ERRCODE = 'check_violation';
  END IF;
  IF p_old_prefix = '/' THEN
    RAISE EXCEPTION 'cannot rename root path /'
      USING ERRCODE = 'check_violation';
  END IF;

  UPDATE public.documents
     SET folder_path = p_new_prefix || substring(folder_path FROM length(p_old_prefix) + 1)
   WHERE scope = p_scope
     AND (p_user_id IS NULL OR user_id = p_user_id)
     AND (folder_path = p_old_prefix
          OR folder_path LIKE p_old_prefix || '/%');
  GET DIAGNOSTICS v_doc_count = ROW_COUNT;

  UPDATE public.folders
     SET path = p_new_prefix || substring(path FROM length(p_old_prefix) + 1)
   WHERE scope = p_scope
     AND (p_user_id IS NULL OR user_id = p_user_id)
     AND (path = p_old_prefix
          OR path LIKE p_old_prefix || '/%');
  GET DIAGNOSTICS v_folder_count = ROW_COUNT;

  RETURN QUERY SELECT v_doc_count, v_folder_count;
END;
$$;

GRANT EXECUTE ON FUNCTION public.rename_folder_prefix(TEXT, TEXT, TEXT, UUID) TO authenticated;
```

**Convention notes from analogs:**
- `LANGUAGE plpgsql` after `RETURNS TABLE (...)` mirrors `008_hybrid_search.sql:37` and `match_document_chunks_with_filters` in `007_document_metadata.sql:36`.
- `RETURN QUERY SELECT ...` for TABLE-returning functions mirrors `008_hybrid_search.sql:39, 72-75`.
- `GET DIAGNOSTICS v_count = ROW_COUNT;` is standard PL/pgSQL idiom (no codebase precedent — but it IS the canonical Postgres pattern for capturing UPDATE/DELETE row counts).
- `GRANT EXECUTE ... TO authenticated;` after each function definition mirrors `015_two_scope_rls.sql:35`.

**Function definition pattern — the delete RPC** (RESEARCH.md §Folder Delete Implementation provides the body verbatim; same skeleton as rename):

```sql
CREATE OR REPLACE FUNCTION public.delete_folder_if_empty(
  p_folder_id UUID
)
RETURNS TABLE (deleted BOOLEAN, document_count INT, subfolder_count INT)
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
DECLARE
  v_path           TEXT;
  v_scope          TEXT;
  v_user_id        UUID;
  v_doc_count      INT;
  v_subfolder_count INT;
BEGIN
  -- FOR UPDATE row lock — eliminates TOCTOU between count-check and delete (see 03-RESEARCH.md §Folder Delete Implementation, A4).
  SELECT path, scope, user_id INTO v_path, v_scope, v_user_id
    FROM public.folders
   WHERE id = p_folder_id
   FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'folder not found: %', p_folder_id
      USING ERRCODE = 'no_data_found';
  END IF;

  SELECT COUNT(*) INTO v_doc_count
    FROM public.documents
   WHERE scope = v_scope
     AND (v_user_id IS NULL OR user_id = v_user_id)
     AND (folder_path = v_path OR folder_path LIKE v_path || '/%');

  SELECT COUNT(*) INTO v_subfolder_count
    FROM public.folders
   WHERE scope = v_scope
     AND (v_user_id IS NULL OR user_id = v_user_id)
     AND path LIKE v_path || '/%';

  IF v_doc_count > 0 OR v_subfolder_count > 0 THEN
    RETURN QUERY SELECT FALSE, v_doc_count, v_subfolder_count;
    RETURN;
  END IF;

  DELETE FROM public.folders WHERE id = p_folder_id;
  RETURN QUERY SELECT TRUE, 0, 0;
END;
$$;

GRANT EXECUTE ON FUNCTION public.delete_folder_if_empty(UUID) TO authenticated;
```

**Convention notes:**
- `SELECT col1, col2 INTO var1, var2 FROM ... WHERE ... FOR UPDATE` is the canonical PL/pgSQL "lock and read" idiom (no prior codebase use, but mirrors the Postgres docs verbatim).
- `IF NOT FOUND THEN RAISE EXCEPTION ... USING ERRCODE = 'no_data_found';` matches the `forbid_scope_mutation` exception shape at `015:48-51`.
- Use `'no_data_found'` SQLSTATE for "row missing" — supabase-py reliably surfaces this as a Python Exception the router can catch.

**Optional third RPC — `create_folder_if_not_exists`** (RESEARCH.md §Concurrent-upload-no-orphan recommends shipping it; Open Question §4 endorses it):

```sql
CREATE OR REPLACE FUNCTION public.create_folder_if_not_exists(
  p_scope   TEXT,
  p_user_id UUID,
  p_path    TEXT
)
RETURNS TABLE (id UUID, created BOOLEAN)
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
DECLARE
  v_id UUID;
BEGIN
  -- Validate canonical form (defense in depth)
  IF p_path !~ '^/$|^/[^/]+(/[^/]+)*$' THEN
    RAISE EXCEPTION 'path not canonical: %', p_path
      USING ERRCODE = 'check_violation';
  END IF;

  -- Coupling assertion (mirrors the table CHECK from migration 013:17-20)
  IF (p_scope = 'user' AND p_user_id IS NULL)
     OR (p_scope = 'global' AND p_user_id IS NOT NULL) THEN
    RAISE EXCEPTION 'scope/user_id coupling violation: scope=%, user_id=%',
      p_scope, p_user_id
      USING ERRCODE = 'check_violation';
  END IF;

  -- ON CONFLICT against the unique expression index from migration 013:38-43.
  -- (scope, COALESCE(user_id, '00..0'), path) — expression-targeted ON CONFLICT
  -- requires the same expression in the conflict target.
  INSERT INTO public.folders (scope, user_id, path)
       VALUES (p_scope, p_user_id, p_path)
  ON CONFLICT (scope, COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid), path) DO NOTHING
  RETURNING id INTO v_id;

  IF v_id IS NULL THEN
    -- Existed already; look it up by the same expression index columns.
    SELECT f.id INTO v_id
      FROM public.folders f
     WHERE f.scope = p_scope
       AND COALESCE(f.user_id, '00000000-0000-0000-0000-000000000000'::uuid)
           = COALESCE(p_user_id, '00000000-0000-0000-0000-000000000000'::uuid)
       AND f.path = p_path;
    RETURN QUERY SELECT v_id, FALSE;
  ELSE
    RETURN QUERY SELECT v_id, TRUE;
  END IF;
END;
$$;

GRANT EXECUTE ON FUNCTION public.create_folder_if_not_exists(TEXT, UUID, TEXT) TO authenticated;
```

**Convention note:** `ON CONFLICT (expression_list) DO NOTHING` is canonical Postgres for upsert-or-noop. The expression list MUST match the unique index expression (Migration 013:38-43) exactly — `COALESCE(user_id, '00..0'::uuid)` — or PostgreSQL raises `ERROR: there is no unique or exclusion constraint matching the ON CONFLICT specification`.

**Migration filename:** `backend/migrations/019_folder_rename_and_delete_rpcs.sql`. Slot 017 is reserved (per STATE.md "017.sql carry-forward is documentation/migration-naming follow-up"); 018 ships Storage RLS (Phase 2). 019 is the next free slot. [VERIFIED: glob shows 012-016 + 018; 017 + 019 absent.]

---

### `backend/app/routers/folders.py` (controller, CRUD with admin gate)

**Primary analog:** `backend/app/routers/threads.py` (4-endpoint CRUD shape, top-to-bottom)
**Secondary analog:** `backend/app/routers/files.py` (admin gate inline pattern, supabase RPC invocation, JSONResponse for non-200 statuses)
**Tertiary analog:** `backend/app/routers/settings.py` (mixed `Depends(get_current_user)` / `Depends(get_admin_user)` per endpoint)

**Imports + router-instantiation pattern** (copy from `threads.py:1-5`):

```python
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from app.auth import get_current_user, get_admin_user, get_user_profile, get_supabase_client
from app.models.schemas import FolderResponse, FolderCreate, FolderPatch
from app.services.folder_service import (
    normalize_path, list_folder, create_folder,
    rename_folder, delete_folder,
)

router = APIRouter(prefix="/api/folders", tags=["folders"])
```

**Convention to copy:**
- Imports stdlib-then-FastAPI-then-app (matches `threads.py`, `files.py`, `settings.py`).
- `JSONResponse` only imported when needed (FOLDER-04's 409 response — `files.py` doesn't use it; `threads.py` doesn't use it; this is the first router with structured non-200 returns, but `JSONResponse` is the canonical FastAPI escape hatch).
- `get_user_profile` and `get_admin_user` BOTH imported because Phase 3 needs both shapes (see "Admin gate two-shape" below).
- `router = APIRouter(prefix="/api/folders", tags=["folders"])` — prefix path mirrors the routes, tags is the lowercase plural noun. Matches all four existing routers exactly.

**GET endpoint pattern** (copy shape from `threads.py:22-32`):

```python
@router.get("")
async def list_folders(
    path: str = Query("/", description="Folder path to list"),
    scope: str = Query("both", regex="^(user|global|both)$"),
    user_id: str = Depends(get_current_user),
):
    sb = get_supabase_client()
    norm = normalize_path(path)
    return list_folder(norm, scope, user_id, sb)
```

**Convention notes:**
- `Query(default, ...)` for non-path arguments mirrors files.py:60-65 (where `BackgroundTasks`, `UploadFile = File(...)` use FastAPI dependency-injection markers).
- `regex="^(user|global|both)$"` enforces enum at the FastAPI layer — produces a clean 422 with details, vs. an opaque 400 from a manual check (matches RESEARCH.md §Files Router Extensions which uses the same regex on `scope`).
- `description=` on Query args powers the OpenAPI docs at `/docs`.
- Response model intentionally omitted for GET (returns a dict with `documents` + `subfolders` lists; not a flat `FolderResponse`). Per RESEARCH.md Open Question §5, recommended shape is `{path, documents, subfolders}`.

**POST endpoint pattern with conditional admin gate** (assemble from `threads.py:8-19` + `auth.py:43-52` inline mirror):

```python
@router.post("", response_model=FolderResponse)
async def create_folder_endpoint(
    body: FolderCreate,
    user_id: str = Depends(get_current_user),
):
    sb = get_supabase_client()
    norm = normalize_path(body.path)
    if body.scope == "global":
        # Inline admin check — Depends(get_admin_user) doesn't work because the
        # admin requirement depends on body.scope, which Depends evaluates BEFORE
        # the body is parsed. Mirror auth.py:46-51 inline.
        profile = get_user_profile(user_id)
        if not profile or not profile.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin required for global scope")
        return create_folder(norm, "global", None, sb)
    return create_folder(norm, "user", user_id, sb)
```

**Convention notes:**
- `body: PydanticModel` followed by `user_id: str = Depends(...)` matches `threads.py:9-12` exactly.
- The inline admin check is necessary because `Depends(get_admin_user)` is request-time-static; Phase 3 needs admin-gate-conditional-on-body.
- The mirror is exactly 3 lines: `profile = get_user_profile(user_id) → if not profile or not profile.get("is_admin"): raise HTTPException(403, ...)`. This is what `auth.py:46-51` does internally.

**PATCH endpoint pattern** (RESEARCH.md §Folders Router Design provides body; copy 404-then-admin-gate shape from `threads.py:36-48`):

```python
@router.patch("/{folder_id}", response_model=FolderResponse)
async def rename_folder_endpoint(
    folder_id: str,
    body: FolderPatch,
    user_id: str = Depends(get_current_user),
):
    sb = get_supabase_client()
    existing = sb.table("folders").select("*").eq("id", folder_id).maybe_single().execute()
    if not existing or not existing.data:
        raise HTTPException(status_code=404, detail="Folder not found")
    folder = existing.data
    if folder["scope"] == "global":
        profile = get_user_profile(user_id)
        if not profile or not profile.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin required")
    new_path_norm = normalize_path(body.new_path)
    result = rename_folder(folder["path"], new_path_norm, folder["scope"],
                           folder.get("user_id"), sb)
    return {**folder, "path": new_path_norm, **result}
```

**Convention to copy:**
- Look up the row first with `.maybe_single()` (matches `threads.py:43`); 404 if missing — clean error contract.
- Apply admin gate AFTER the lookup (because admin-gate decision depends on the existing row's scope). This is the analog to a "subject-based" auth check.
- Return the merged dict: existing row fields + the patch + the RPC result.

**DELETE endpoint with structured 409 pattern** (assemble from `threads.py:51-61` + `files.py:153-165` shape + RESEARCH.md §Folder Delete Implementation):

```python
@router.delete("/{folder_id}")
async def delete_folder_endpoint(
    folder_id: str,
    user_id: str = Depends(get_current_user),
):
    sb = get_supabase_client()
    existing = sb.table("folders").select("*").eq("id", folder_id).maybe_single().execute()
    if not existing or not existing.data:
        raise HTTPException(status_code=404, detail="Folder not found")
    folder = existing.data
    if folder["scope"] == "global":
        profile = get_user_profile(user_id)
        if not profile or not profile.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin required")
    result = delete_folder(folder_id, sb)
    if not result.get("deleted"):
        return JSONResponse(status_code=409, content={
            "error": "FOLDER_NOT_EMPTY",
            "document_count": result.get("document_count", 0),
            "subfolder_count": result.get("subfolder_count", 0),
        })
    return {"status": "deleted"}
```

**Convention notes:**
- `JSONResponse(status_code=409, content={...})` is the FastAPI escape hatch for non-200 with structured body. No prior codebase use — but it's the canonical FastAPI idiom (and Phase 6 UI's "show actual count" expectation depends on the structured shape).
- 409 (Conflict) chosen over 400 — semantically correct (request well-formed; state forbids it). RESEARCH.md §Folder Delete Implementation A7 endorses.
- `{"status": "deleted"}` on success matches `files.py:165` (`return {"status": "deleted"}`).

---

### `backend/scripts/test_folders.py` (test, integration with concurrent + RPC fixtures)

**Primary analog:** `backend/scripts/test_files.py` (upload + poll + assertion shape, scoped cleanup via local var)
**Secondary analog:** `backend/scripts/test_two_scope_rls.py` (RLS matrix + tracked-resource cleanup with `_track_*` lists + admin token + `_raises` helper)
**Tertiary analog:** `backend/scripts/test_backfill.py` (canary precheck for migration prerequisites + subprocess invocation + service-role direct DB readback)

**Module docstring + imports + sys.path bootstrap** (copy from `test_backfill.py:1-44`):

```python
"""Integration tests for Phase 3: folder service + routers + dedup extension.

Covers:
  - FOLDER-02: list_folder / create_folder / move_document / rename_folder / delete_folder
              service surface (importability + signature smoke)
  - FOLDER-03: rename_folder_prefix RPC atomically updates documents + folders
              (transactional rollback verified via deliberate-fail test fixture)
  - FOLDER-04: delete_folder_if_empty RPC rejects non-empty with structured 409
              ({error: 'FOLDER_NOT_EMPTY', document_count, subfolder_count})
  - FOLDER-05: dedup key extension — same file in two folders → 2 docs;
              same file in same (scope, user, path) → action='skip'
  - FOLDER-06: GET/POST/PATCH/DELETE /api/folders end-to-end + admin gate for global
  - FOLDER-07: POST /api/files/upload?folder_path=&scope= + PATCH /api/files/{id};
              concurrent-upload-no-orphan (Pitfall 10 / Strategy B)
  - TEST-01: registered as 15th suite in test_all.py SUITES list (Files → Folders → Backfill order)

PREREQUISITE (must be complete before running this test):
  1. Migration 019 applied via:
       cd backend && venv/Scripts/python scripts/run_migrations.py
     (adds rename_folder_prefix, delete_folder_if_empty, optionally create_folder_if_not_exists)
  2. backend/app/routers/folders.py registered in main.py (else GET /api/folders returns 404).
  3. Backend running on http://localhost:8001.
  4. backend/.env contains SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY.
  5. Admin user promoted: UPDATE public.profiles SET is_admin=true WHERE email='admin@test.com'.

If any prerequisite is missing, the canary precheck (_verify_phase3_setup) returns
a single FAIL h.test + early-returns with an actionable [FATAL] message.

CRITICAL: per CLAUDE.md, tests must NEVER delete all user data. This test tracks every
created document_id and folder_id and removes ONLY those resources in finally.
No blanket-delete SQL, no table truncation, no cross-user cleanup.
"""
import concurrent.futures
import os
import subprocess
import sys
import uuid

import requests

# Two-step sys.path bootstrap: scripts/ first (for sibling imports), then backend/
# so that `from app.services.folder_service import normalize_path` resolves.
# Mirrors backend/scripts/test_two_scope_rls.py:32-37 + test_backfill.py:39-40.
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

import test_helpers as h
from app.services.folder_service import normalize_path
from supabase import create_client
```

**Convention to copy:**
- Module docstring includes Coverage list + Prerequisite list + CLAUDE.md cleanup pledge (matches `test_backfill.py:1-28`).
- Two-step `sys.path.insert(0, ...)` (matches both `test_two_scope_rls.py:32-37` and `test_backfill.py:39-40`) — required to import from `app.services.*`.
- Imports stdlib (`concurrent.futures`, `os`, `subprocess`, `sys`, `uuid`) → third-party (`requests`) → service-role helper from supabase.

**Tracked-resource cleanup pattern** (copy from `test_two_scope_rls.py:39-77`):

```python
# Tracking for cleanup. Each list holds tuples of (id, client_for_cleanup).
# CLAUDE.md: tests must NEVER delete all user data — only tracked resources.
_tracked_documents: list = []  # list[(doc_id, sb_client)]
_tracked_folders: list = []    # list[(folder_id, sb_client)]


def _track_doc(doc_id, sb_client):
    _tracked_documents.append((doc_id, sb_client))


def _track_folder(folder_id, sb_client):
    _tracked_folders.append((folder_id, sb_client))


def _cleanup():
    """Delete ONLY tracked resources. Per CLAUDE.md: never bulk-delete."""
    for did, client in _tracked_documents:
        try:
            client.table("document_chunks").delete().eq("document_id", did).execute()
            client.table("documents").delete().eq("id", did).execute()
        except Exception:
            pass
    for fid, client in _tracked_folders:
        try:
            client.table("folders").delete().eq("id", fid).execute()
        except Exception:
            pass
    _tracked_documents.clear()
    _tracked_folders.clear()
```

**Convention to copy verbatim:**
- Module-level lists with `_tracked_` prefix (matches `test_two_scope_rls.py:41-43`).
- Each list holds `(id, client)` tuples so cleanup uses the same client that created the resource (relevant for direct-supabase test rows that bypass FastAPI).
- Wrap each delete in `try/except: pass` — cleanup must not crash the test summary.
- `_cleanup()` called from a single `finally` at the bottom of `run()`.

**Canary precheck pattern** (assemble from `test_two_scope_rls.py:80-102` + `test_backfill.py:85-105`):

```python
def _verify_phase3_setup() -> tuple[bool, str]:
    """Pre-flight: assert Migration 019's RPCs exist and folders router is registered.

    Mirrors test_two_scope_rls.py::_verify_admin_setup and
    test_backfill.py::_verify_storage_setup. Returns (ok, message); test driver
    bails with a clear error message and a SINGLE failing h.test if not ok.
    """
    sb_admin = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )
    # Probe 1: rename_folder_prefix exists. Call with a non-matching prefix so it
    # is a no-op (returns 0 documents_updated / 0 folders_updated).
    try:
        r = sb_admin.rpc("rename_folder_prefix", {
            "p_old_prefix": f"/probe-{uuid.uuid4().hex[:8]}",
            "p_new_prefix": f"/probe-renamed-{uuid.uuid4().hex[:8]}",
            "p_scope": "user",
            "p_user_id": "00000000-0000-0000-0000-000000000000",
        }).execute()
        if r.data is None:
            return False, "rename_folder_prefix returned no data — function exists but is broken"
    except Exception as e:
        return False, (
            f"rename_folder_prefix RPC missing or errored: {type(e).__name__}: {e}. "
            f"Did you apply Migration 019 via run_migrations.py?"
        )
    # Probe 2: GET /api/folders responds (router registered in main.py).
    try:
        r = requests.get(f"{h.BASE_URL}/api/folders", timeout=5)
        # 401 expected (no auth header) — but 404 means router is missing.
        if r.status_code == 404:
            return False, (
                "GET /api/folders returns 404 — folders router not registered in main.py. "
                "Add `from app.routers import folders` and `app.include_router(folders.router)`."
            )
    except Exception as e:
        return False, f"Backend unreachable: {e}. Start with: cd backend && venv/Scripts/python -m uvicorn app.main:app --reload --port 8001"
    return True, "ok"
```

**Convention to copy:**
- Returns `(bool, str)` tuple — driver does `if not ok: h.test('Phase 3 setup', False, msg); return h.passed, h.failed`.
- Uses service-role for RPC probe (bypasses RLS).
- HTTP probe uses `timeout=5` (matches `test_backfill.py:114`).
- Probe message includes the exact remediation command (matches `test_two_scope_rls.py:97-99` and `test_backfill.py:101-104`).

**`_raises()` helper** (copy verbatim from `test_two_scope_rls.py:105-114`):

```python
def _raises(fn, *exc_substrings):
    """Run fn(); return (raised: bool, message: str). Optionally check substrings appear in message."""
    try:
        fn()
        return False, ""
    except Exception as e:
        msg = str(e)
        if exc_substrings and not all(s in msg for s in exc_substrings):
            return False, msg
        return True, msg
```

**Test runner shape** (copy from `test_files.py:20-146` + `test_backfill.py:147-...`):

```python
def run():
    h.reset_counters()

    ok, msg = _verify_phase3_setup()
    if not ok:
        h.test("Phase 3 setup (Migration 019 + folders router)", False, msg)
        return h.passed, h.failed

    token = h.get_auth_token()
    headers = h.auth_headers(token)
    admin_token = h.get_admin_token()

    sb_admin = h.get_user_supabase_client(admin_token)
    sb_a = h.get_user_supabase_client(token)

    try:
        # ── FOLDER-02: service surface (smoke import + signature) ──
        h.section("FOLDER-02 service surface")
        from app.services.folder_service import (
            list_folder, create_folder, move_document, rename_folder, delete_folder,
        )
        h.test("list_folder importable", callable(list_folder))
        h.test("create_folder importable", callable(create_folder))
        h.test("move_document importable", callable(move_document))
        h.test("rename_folder importable", callable(rename_folder))
        h.test("delete_folder importable", callable(delete_folder))

        # ── FOLDER-06: router CRUD (full happy path + admin gate) ──
        h.section("FOLDER-06 router CRUD")
        # ... POST /api/folders, GET /api/folders, PATCH, DELETE; track every folder_id ...

        # ── FOLDER-03: transactional rollback (deliberate-fail RPC variant) ──
        h.section("FOLDER-03 transactional rollback")
        # ... see "Mid-rename rollback test fixture" section below ...

        # ── FOLDER-04: non-empty rejection ──
        h.section("FOLDER-04 non-empty rejected")
        # ... POST file into a folder, attempt DELETE folder, assert 409 + counts ...

        # ── FOLDER-05: dedup key extension ──
        h.section("FOLDER-05 dedup key")
        # ... upload file at /a, upload same file at /b → 2 docs;
        #     re-upload same file at /a → action='skip' ...

        # ── FOLDER-07: files router extensions + concurrent-upload-no-orphan ──
        h.section("FOLDER-07 files router")
        # ... POST /api/files/upload?folder_path=...&scope=...; PATCH /api/files/{id} ...
        h.section("Pitfall 10 concurrent upload")
        # ... see "Concurrent-upload-no-orphan test fixture" below ...

    finally:
        _cleanup()

    return h.passed, h.failed


if __name__ == "__main__":
    run()
    sys.exit(h.summary())
```

**Convention to copy:**
- `h.reset_counters()` first (matches every `run()` in the suite).
- Canary precheck FIRST — bail with single failing h.test if missing (matches `test_two_scope_rls.py:121` + `test_backfill.py:153-159`).
- `h.section(name)` for visual grouping; `h.test(name, condition, detail)` for assertions.
- `try / finally: _cleanup()` (matches `test_two_scope_rls.py:133, 436-437`).
- Bottom: `if __name__ == "__main__": run(); sys.exit(h.summary())` (matches every test_*.py).

**Concurrent-upload-no-orphan test fixture** (verbatim from RESEARCH.md §Concurrent-upload-no-orphan test fixture):

```python
def _test_concurrent_upload_no_orphan(token, headers, sb_admin):
    h.section("Pitfall 10 — concurrent upload no-orphan")
    test_path = f"/test-race-{uuid.uuid4().hex[:8]}"
    file_bytes = b"race test content"

    def _upload(idx):
        return requests.post(
            f"{h.BASE_URL}/api/files/upload",
            headers={"Authorization": f"Bearer {token}"},
            params={"folder_path": test_path, "scope": "user"},
            files={"file": (f"race-{idx}.txt", file_bytes, "text/plain")},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(_upload, range(10)))

    success_count = sum(1 for r in results if r.status_code == 200)
    h.test("All 10 parallel uploads return 200", success_count == 10,
           f"got {success_count} successes")

    # Track every doc for cleanup
    for r in results:
        if r.status_code == 200:
            doc_id = r.json().get("id")
            if doc_id:
                _track_doc(doc_id, sb_admin)

    # Strategy B assertion: folders table did NOT acquire a row at test_path.
    folders_check = sb_admin.table("folders").select("id").eq("path", test_path).execute()
    h.test("Strategy B: folders table has 0 rows at brand-new upload path",
           len(folders_check.data) == 0,
           f"got {len(folders_check.data)} folder rows")
```

**Mid-rename rollback test fixture** (per RESEARCH.md §Mid-rename rollback test):

The test creates a SQL-side deliberate-fail variant of `rename_folder_prefix` (NOT shipped in Migration 019), invokes it, and asserts the `documents.folder_path` is unchanged. The deliberate-fail function is created and dropped within the test:

```python
def _test_rename_rollback(sb_admin, u_a):
    h.section("FOLDER-03 transactional rollback")
    # 1. Insert a document at /test-rename/doc.txt (tracked for cleanup)
    r = sb_admin.table("documents").insert({
        "user_id": u_a, "scope": "user", "folder_path": "/test-rename",
        "file_name": f"doc-{uuid.uuid4()}.txt",
        "file_size": 1, "mime_type": "text/plain", "status": "ready",
    }).execute()
    doc_id = r.data[0]["id"] if r.data else None
    if doc_id:
        _track_doc(doc_id, sb_admin)

    # 2. Create a deliberately-failing RPC variant (test-only, dropped in finally).
    # This exercises the "implicitly transactional" guarantee of PL/pgSQL.
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        h.test("FOLDER-03 rollback test SKIPPED (no DATABASE_URL)", True,
               "set DATABASE_URL to run; structural plpgsql-language assertion still ran")
        return
    import psycopg2
    pg = psycopg2.connect(db_url)
    pg.autocommit = True
    try:
        with pg.cursor() as cur:
            cur.execute("""
                CREATE OR REPLACE FUNCTION public.test_rename_folder_prefix_fails_midway(
                  p_old_prefix TEXT, p_new_prefix TEXT, p_scope TEXT, p_user_id UUID
                ) RETURNS VOID LANGUAGE plpgsql AS $$
                BEGIN
                  UPDATE public.documents SET folder_path = p_new_prefix
                   WHERE scope = p_scope AND user_id = p_user_id
                     AND (folder_path = p_old_prefix
                          OR folder_path LIKE p_old_prefix || '/%');
                  RAISE EXCEPTION 'deliberate test failure mid-rename';
                END;
                $$;
            """)
        # 3. Call it — must raise.
        raised, _ = _raises(lambda: sb_admin.rpc(
            "test_rename_folder_prefix_fails_midway",
            {"p_old_prefix": "/test-rename", "p_new_prefix": "/test-rename-new",
             "p_scope": "user", "p_user_id": u_a},
        ).execute())
        h.test("Deliberate-fail RPC raises", raised)
        # 4. Read doc back and assert folder_path UNCHANGED (rollback worked).
        row = sb_admin.table("documents").select("folder_path").eq("id", doc_id).single().execute().data
        h.test("After rollback, folder_path UNCHANGED",
               row["folder_path"] == "/test-rename",
               f"got {row['folder_path']!r}")
    finally:
        with pg.cursor() as cur:
            cur.execute("DROP FUNCTION IF EXISTS public.test_rename_folder_prefix_fails_midway(TEXT, TEXT, TEXT, UUID);")
        pg.close()
```

**Convention notes:**
- `os.environ.get("DATABASE_URL")` skip-if-absent matches `test_two_scope_rls.py:396-397` Group 5 EXPLAIN-plan checks.
- `psycopg2.connect(db_url); pg.autocommit = True` matches `test_two_scope_rls.py:399-400`.
- DROP function in finally so the test is repeatable.

---

### `backend/app/services/folder_service.py` (MODIFY — extend after `normalize_path`)

**Analog:** self (in-place extension of L67) + `backend/app/services/record_manager.py:27-69` (function shape with injected supabase_client)

**Existing pattern at L67** (the exact insertion point):

```python
# folder_service.py:65-67 — current end of normalize_path()
    if not _CANONICAL_PATH_RE.match(s):
        raise ValueError(f"Path failed canonical form check: {s!r} (input was {p!r})")
    return s

# Phase 3 inserts five new functions after this line (BEFORE the
# `if __name__ == "__main__":` block at L72-96 — keep the inline self-tests at the bottom).
```

**Function shape pattern** (copy from `record_manager.py:27-69`):

```python
def determine_action(
    file_hash: str,
    file_name: str,
    user_id: str,
    supabase_client,
) -> RecordAction:
    """
    Check if this file has been ingested before.

    Logic:
    1. Look for existing doc with same (user_id, file_name)
    2. ...
    """
    try:
        result = supabase_client.table("documents") \
            .select("id, content_hash, status") \
            .eq("user_id", user_id) \
            ...
```

**Convention to copy:**
- Type hints on every parameter (`file_hash: str, file_name: str, user_id: str`).
- `supabase_client` parameter is positional-untyped (matches `record_manager.py:31`); type-hinting it would import the supabase client class which adds an unnecessary dependency on the service module.
- Triple-quoted docstring with numbered logic block (matches `record_manager.py:33-40`).
- Default values mirrored from RESEARCH.md §Folder Service API Surface.

**Pseudocode for the five functions** (per RESEARCH.md §Folder Service API Surface — these are the exact signatures the planner should expand):

```python
def list_folder(path, scope, user_id, supabase_client):
    """Return {documents: [...], subfolders: [...]} at one level deep."""
    norm = normalize_path(path)  # belt-and-suspenders chokepoint
    # ... query documents WHERE folder_path = norm
    #     UNION DISTINCT subfolder names from folder_path LIKE norm||'/%'
    #     UNION folders rows where path matches immediate-children predicate ...

def create_folder(path, scope, user_id, supabase_client):
    """INSERT into folders with ON CONFLICT DO NOTHING semantics.
    Calls Migration 019's create_folder_if_not_exists RPC if shipped,
    else uses try/except on .insert() for the unique violation."""
    norm = normalize_path(path)
    # ... result = supabase_client.rpc("create_folder_if_not_exists", {
    #         "p_scope": scope, "p_user_id": user_id, "p_path": norm,
    #     }).execute()
    # ... return {**row, "action": "created" if row["created"] else "exists"}

def move_document(document_id, new_folder_path, user_id, supabase_client):
    """UPDATE documents SET folder_path WHERE id AND user_id."""
    norm = normalize_path(new_folder_path)
    # ... result = supabase_client.table("documents") \
    #         .update({"folder_path": norm}) \
    #         .eq("id", document_id) \
    #         .eq("user_id", user_id) \
    #         .execute()
    # ... return result.data[0] if result.data else None

def rename_folder(old_path, new_path, scope, user_id, supabase_client):
    """Calls Migration 019's rename_folder_prefix RPC."""
    old_norm = normalize_path(old_path)
    new_norm = normalize_path(new_path)
    if old_norm == "/" or new_norm == "/":
        raise ValueError("cannot rename root path")
    # ... result = supabase_client.rpc("rename_folder_prefix", {
    #         "p_old_prefix": old_norm,
    #         "p_new_prefix": new_norm,
    #         "p_scope": scope,
    #         "p_user_id": user_id,
    #     }).execute()
    # ... return {"documents_updated": ..., "folders_updated": ...}

def delete_folder(folder_id, supabase_client):
    """Calls Migration 019's delete_folder_if_empty RPC."""
    # ... result = supabase_client.rpc("delete_folder_if_empty", {
    #         "p_folder_id": folder_id,
    #     }).execute()
    # ... return {"deleted": ..., "document_count": ..., "subfolder_count": ...}
```

**Convention notes:**
- EVERY function's first statement is `normalize_path()` on its path argument(s) — Pitfall 4 chokepoint enforcement (matches the existing `normalize_path()` design intent at L11-13 docstring "Phase 3 extends this file with folder CRUD").
- Functions are PURE service-layer — no FastAPI imports (matches `record_manager.py` which has zero FastAPI imports).
- `supabase_client` injected, never imported (matches `record_manager.py:31`, `ingestion.py` parameter pattern).

---

### `backend/app/services/record_manager.py` (MODIFY — extend `determine_action()` signature)

**Analog:** self (in-place edit of L27-69)

**Existing pattern at L27-48** (the exact insertion point):

```python
# record_manager.py:27-48 — current state
def determine_action(
    file_hash: str,
    file_name: str,
    user_id: str,
    supabase_client,
) -> RecordAction:
    """
    Check if this file has been ingested before.

    Logic:
    1. Look for existing doc with same (user_id, file_name)
    2. If found and same hash → skip (identical content)
    3. If found and different hash → update (content changed)
    4. If not found → create (new file)
    """
    try:
        result = supabase_client.table("documents") \
            .select("id, content_hash, status") \
            .eq("user_id", user_id) \
            .eq("file_name", file_name) \
            .maybe_single() \
            .execute()
    except Exception:
        return RecordAction(action="create", message="New document")
```

**Edit pattern** (per RESEARCH.md §Dedup Key Extension — paste-ready):

```python
def determine_action(
    file_hash: str,
    file_name: str,
    user_id: str,
    supabase_client,
    scope: str = "user",          # NEW — defaults preserve Phase 1/2 behavior
    folder_path: str = "/",       # NEW — defaults preserve Phase 1/2 behavior
) -> RecordAction:
    """
    Check if this file has been ingested before.

    Logic:
    1. Look for existing doc with same (scope, user_id, folder_path, file_name)
    2. If found and same hash → skip (identical content)
    3. If found and different hash → update (content changed)
    4. If not found → create (new file)

    NOTE: For scope='global', user_id is None and we use the .is_('user_id', 'null')
    branch — same NULL-semantics as the unique index documents_scope_user_path_filename_unique
    from Migration 012 (which uses COALESCE(user_id, '00..0') to make NULLs compare equal).
    """
    try:
        query = supabase_client.table("documents") \
            .select("id, content_hash, status") \
            .eq("scope", scope) \
            .eq("folder_path", folder_path) \
            .eq("file_name", file_name)
        if scope == "user":
            query = query.eq("user_id", user_id)
        else:
            query = query.is_("user_id", "null")  # global rows have user_id IS NULL
        result = query.maybe_single().execute()
    except Exception:
        return RecordAction(action="create", message="New document")

    # ... rest unchanged (L53-68)
```

**Convention notes:**
- Defaults `scope='user'` and `folder_path='/'` preserve Phase 1/2 behavior — callers that don't pass them get identical semantics. This is back-compat as a hard contract (per Pitfall A in RESEARCH.md).
- `.is_('user_id', 'null')` for global scope — the supabase-py PostgREST builder maps this to SQL `IS NULL`. The existing `.eq('user_id', user_id)` would never match a NULL row.
- The unique index uses `COALESCE` for write-time dedup; the SELECT must explicitly match the column's actual NULL state — RESEARCH.md §Pitfall A is the smoking gun here.

---

### `backend/app/routers/files.py` (MODIFY — Query args + new PATCH endpoint)

**Analog:** self (in-place extension at L60-145 + new endpoint after L165)

**Existing upload signature at L60-65** (the exact edit point):

```python
@router.post("/upload", response_model=DocumentResponse)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user),
):
```

**Edit pattern — add Query args** (per RESEARCH.md §Files Router Extensions):

```python
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks, Query
from app.auth import get_current_user, get_supabase_client, get_user_profile
from app.models.schemas import DocumentResponse, FilePatch
from app.services.folder_service import normalize_path
from app.services.ingestion import ingest_document, ingest_document_update
from app.services.record_manager import compute_file_hash, determine_action

# ... existing helpers unchanged: _ingestion_semaphore, _throttled_ingest, _upload_to_storage ...

@router.post("/upload", response_model=DocumentResponse)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    folder_path: str = Query("/", description="Canonical folder path"),
    scope: str = Query("user", regex="^(user|global)$"),
    user_id: str = Depends(get_current_user),
):
    supabase = get_supabase_client()
    contents = await file.read()
    file_name = file.filename or "unnamed"
    mime_type = file.content_type or "application/octet-stream"

    # NEW: normalize and admin-gate scope
    folder_path = normalize_path(folder_path)
    if scope == "global":
        profile = get_user_profile(user_id)
        if not profile or not profile.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin required for global scope")
        effective_user_id = None  # global rows have user_id IS NULL per coupling CHECK
        storage_user_segment = "global"  # Pitfall F: avoid 'None' in storage path
    else:
        effective_user_id = user_id
        storage_user_segment = user_id

    # NEW: pass scope + folder_path to determine_action (record_manager extension)
    file_hash = compute_file_hash(contents)
    record_action = determine_action(
        file_hash, file_name, user_id, supabase,
        scope=scope, folder_path=folder_path,
    )

    # ... rest of handler unchanged EXCEPT:
    # 1. The documents.insert() in the action == "create" branch must include
    #    scope and folder_path:
    #       {"user_id": effective_user_id, "scope": scope, "folder_path": folder_path, ...}
    # 2. The _upload_to_storage call uses storage_user_segment (NOT user_id):
    #       _upload_to_storage(supabase, user_id=storage_user_segment, ...)
    # 3. The action == "update" branch is unchanged (existing row already has scope + folder_path).
```

**Convention notes:**
- `Query(default, regex=...)` in the function signature — FastAPI generates a clean 422 with field-level error if the regex fails (matches `routers/folders.py` GET shape).
- `effective_user_id` shadowed at top of handler — used in `documents.insert()` to keep the existing-user-flow row payload pure.
- `storage_user_segment` is NEW (Pitfall F mitigation) — RESEARCH.md §Files Router Extensions explicitly flags this: `user_id=None` would produce `documents/None/...` in the Storage path, which Storage RLS rejects.
- Comment block above the `effective_user_id = None` line explicitly references Pitfall F and the coupling CHECK.

**New PATCH endpoint** (insert after L165 — after `delete_file`; per RESEARCH.md §Files Router Extensions):

```python
@router.patch("/{file_id}", response_model=DocumentResponse)
async def patch_file(
    file_id: str,
    body: FilePatch,
    user_id: str = Depends(get_current_user),
):
    sb = get_supabase_client()
    doc = sb.table("documents").select("*").eq("id", file_id).maybe_single().execute()
    if not doc or not doc.data:
        raise HTTPException(status_code=404, detail="Document not found")
    existing = doc.data

    # Admin gate for global-scope writes
    if existing["scope"] == "global":
        profile = get_user_profile(user_id)
        if not profile or not profile.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin required")

    # CRITICAL: scope is IMMUTABLE — Migration 015's forbid_scope_mutation trigger
    # is the bedrock. FilePatch model deliberately omits scope (Pydantic ignores
    # unknown fields on body parsing); explicit safety net here.
    update_data = {}
    if body.file_name is not None:
        update_data["file_name"] = body.file_name
    if body.folder_path is not None:
        update_data["folder_path"] = normalize_path(body.folder_path)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    sb.table("documents").update(update_data).eq("id", file_id).execute()
    return sb.table("documents").select("*").eq("id", file_id).single().execute().data
```

**Convention notes:**
- Same 404-then-admin-gate-then-update shape as `routers/folders.py` PATCH endpoint (consistency across phase).
- Empty `update_data` → 400 (matches conventional REST semantics — empty PATCH is malformed).
- `normalize_path(body.folder_path)` at the router layer (belt) AND at the service layer (suspenders) — Pitfall 4 mitigation.

---

### `backend/app/main.py` (MODIFY — register folders router)

**Analog:** self (L8 import + L23 include_router)

**Existing pattern at L8 + L20-23**:

```python
# main.py:6-23 — current state
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import threads, messages, files, settings

app = FastAPI(title="RAG Masterclass API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(threads.router)
app.include_router(messages.router)
app.include_router(files.router)
app.include_router(settings.router)
```

**Edit pattern** (per RESEARCH.md §Files to Create / Modify):

```python
# Line 8 — append `folders` to the import line:
from app.routers import threads, messages, files, settings, folders

# Line 23 — add include_router AFTER files (folders is logically a Files extension):
app.include_router(threads.router)
app.include_router(messages.router)
app.include_router(files.router)
app.include_router(folders.router)   # NEW (Phase 3)
app.include_router(settings.router)
```

**Convention notes:**
- Comma-separated imports on a single line (matches existing L8).
- `include_router()` ordering follows logical grouping: auth-style (threads → messages) → file/folder family (files → folders) → admin (settings).
- Pitfall E in RESEARCH.md explicitly calls out forgetting this step; the canary precheck in `test_folders.py` catches it via the GET probe (404 vs 401).

---

### `backend/app/models/schemas.py` (MODIFY — add folder + file-patch models)

**Analog:** self (L1-93 — `ThreadCreate` / `DocumentResponse` for naming + style)

**Existing pattern at L6-44**:

```python
# schemas.py:6-15 — ThreadCreate / ThreadResponse model shape
class ThreadCreate(BaseModel):
    title: Optional[str] = None


class ThreadResponse(BaseModel):
    id: str
    user_id: str
    title: str
    created_at: datetime
    updated_at: datetime
```

**Edit pattern** — add four new models per RESEARCH.md §Files to Create / Modify (insert after `DocumentResponse` at L44 to keep file/folder models together):

```python
class DocumentResponse(BaseModel):
    id: str
    user_id: Optional[str] = None      # NEW — nullable for scope='global'
    file_name: str
    file_size: int
    mime_type: str
    status: str
    error_message: Optional[str] = None
    content_hash: Optional[str] = None
    metadata: Optional[dict] = None
    folder_path: str = "/"             # NEW (Phase 3 / FOLDER-07)
    scope: str = "user"                # NEW (Phase 3 / FOLDER-07)
    action: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class FolderResponse(BaseModel):
    id: str
    scope: str                         # 'user' | 'global'
    user_id: Optional[str] = None      # nullable for scope='global'
    path: str
    created_at: datetime


class FolderCreate(BaseModel):
    path: str
    scope: str = "user"                # 'user' | 'global'


class FolderPatch(BaseModel):
    new_path: str


class FilePatch(BaseModel):
    # Mutable fields ONLY. scope is IMMUTABLE (Migration 015 forbid_scope_mutation
    # trigger); file_size / mime_type / status / content_hash / content_markdown
    # are managed by ingestion. Pydantic ignores unknown fields on body parsing,
    # so a smuggled "scope" in the request body is silently dropped here.
    file_name: Optional[str] = None
    folder_path: Optional[str] = None
```

**Convention notes:**
- `BaseModel` + `Optional[T] = None` for nullable fields (matches every existing model in this file).
- `Optional[str] = None` on `user_id` for `DocumentResponse` is REQUIRED — global rows have `user_id IS NULL` per Migration 012 coupling CHECK. Without this change, FastAPI's response serialization raises ValidationError for global docs.
- `scope: str = "user"` defaults `FolderCreate.scope` to `'user'` — caller doesn't have to specify for normal flow.
- `FilePatch` deliberately has only two fields (Pitfall B mitigation; comment explains why).
- Inline comment on `FilePatch` explains the immutability contract — important for readers.

---

### `backend/scripts/test_all.py` (MODIFY — register `test_folders` as 15th suite)

**Analog:** self (L17 import + L33 SUITES entry — mirror the Phase 2 `test_backfill` registration)

**Existing pattern at L11-42**:

```python
# test_all.py:11-42 — current state with Phase 2's test_backfill registered
import test_helpers as h
import test_health
import test_auth
import test_threads
import test_messages
import test_files
import test_backfill
import test_rag
import test_rls
import test_two_scope_rls
import test_settings
import test_metadata
import test_hybrid
import test_tools
import test_sub_agents

SUITES = [
    ("Health", test_health),
    ("Auth", test_auth),
    ("Threads", test_threads),
    ("Messages", test_messages),
    ("Files", test_files),
    ("Backfill", test_backfill),
    ("RAG", test_rag),
    ("RLS", test_rls),
    ("Two-Scope RLS", test_two_scope_rls),
    ("Settings", test_settings),
    ("Metadata", test_metadata),
    ("Hybrid", test_hybrid),
    ("Tools", test_tools),
    ("Sub-Agents", test_sub_agents),
]
```

**Edit pattern** (per RESEARCH.md §Wave 0 Gaps — "append `("Folders", test_folders)` after `("Files", test_files)` and before `("Backfill", test_backfill)`"):

```python
# Add after L16 (after `import test_files`):
import test_folders     # NEW (Phase 3)

# Add to SUITES list AFTER ("Files", test_files) and BEFORE ("Backfill", test_backfill):
SUITES = [
    ("Health", test_health),
    ("Auth", test_auth),
    ("Threads", test_threads),
    ("Messages", test_messages),
    ("Files", test_files),
    ("Folders", test_folders),     # NEW (Phase 3 — folders is logically a Files extension)
    ("Backfill", test_backfill),
    ("RAG", test_rag),
    # ... rest unchanged ...
]
```

**Convention notes:**
- Import line with single trailing-comment annotation (matches Phase 2's `test_backfill` registration style — see git log).
- Suite name PascalCase ("Folders") — matches every existing entry.
- Order: Files → Folders → Backfill mirrors the data-flow dependency (folder routing depends on file infrastructure; backfill is independent).

---

## Shared Patterns

These cross-cutting patterns apply to multiple Phase 3 files.

### Logging
**Source:** `backend/app/services/ingestion.py:19` (Phase 2 PATTERNS.md confirmed)
**Apply to:** `folders.py`, extended `files.py`, `folder_service.py` (all extensions).

```python
import logging
logger = logging.getLogger(__name__)

# usage in routers/folders.py — match the existing files.py:11 + threads.py logger style:
logger.info(f"Folder created: id={folder_id} scope={scope} path={path}")
logger.warning(f"Concurrent folder INSERT race detected (non-fatal): {e}")
logger.error(f"Folder rename RPC failed: {e}", exc_info=True)
```

**Convention:** module-level logger, f-strings with structured fields (id, scope, path), severity matched to user-impact: `info` = normal flow, `warning` = recoverable race, `error` = unrecoverable / data-affecting. NEW routers add `logger = logging.getLogger(__name__)` at top of module — `routers/files.py:11` does this; `routers/threads.py` does NOT (only routers with logging needs add it; folders.py needs it for the rename/delete RPC paths).

### Admin gate two-shape
**Source:** `backend/app/auth.py:43-52` (formal `Depends(get_admin_user)`) and `backend/app/auth.py:46-51` body (inline mirror)
**Apply to:** `routers/folders.py` POST/PATCH/DELETE, `routers/files.py` POST/PATCH (when scope='global').

```python
# Shape 1 — for unconditionally-admin endpoints (none in Phase 3 itself):
async def admin_only_endpoint(user_id: str = Depends(get_admin_user)):
    ...

# Shape 2 — for conditional admin (admin required only when body.scope == 'global'):
async def conditional_admin_endpoint(body: SomeBody, user_id: str = Depends(get_current_user)):
    if body.scope == "global":
        profile = get_user_profile(user_id)
        if not profile or not profile.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin required for global scope")
    # ... rest of endpoint ...
```

**Convention:** `Depends(get_admin_user)` is request-time-static (FastAPI evaluates Depends BEFORE body parsing). Conditional admin gates require the inline mirror of `auth.py:46-51`. Always use `status_code=403` (not 401) and `detail="Admin required for X"` (not "Forbidden") — matches `auth.py:48-51` exactly.

### Service-role client pattern (test fixtures)
**Source:** `backend/app/auth.py:8-12` + `backend/scripts/test_backfill.py:63-72`
**Apply to:** `test_folders.py` for direct-supabase fixtures (insert documents bypassing the API; cleanup that bypasses RLS).

```python
def _service_role_client():
    """Return a service-role Supabase client (mirrors backend/app/auth.py:8-12)."""
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )
```

**Convention:** Direct `os.environ[...]` access (KeyError raises at startup if missing — fail-fast). Service-role bypasses RLS — used ONLY for test fixtures and cleanup; production routers use `get_supabase_client()` from `auth.py` (which is also service-role per the existing CONCERNS.md anti-pattern, but Phase 3 routers add `.eq('scope',...).eq('user_id',...)` defense in depth per RESEARCH.md Constraints table).

### Path canonicalization at every boundary
**Source:** `backend/app/services/folder_service.py:14-67` (`normalize_path()` chokepoint)
**Apply to:** EVERY router endpoint that accepts a path arg + EVERY service-layer function that processes one (Pitfall 4 — three-layer enforcement).

```python
# Layer 1 (Belt) — router:
folder_path = normalize_path(folder_path)        # at top of handler

# Layer 2 (Suspenders) — service:
def list_folder(path, ...):
    norm = normalize_path(path)                  # first statement of every service fn

# Layer 3 (Bedrock) — DB CHECK constraint (Migration 012:40-42 + 013:24-26):
-- folder_path = '/' OR folder_path ~ '^/[^/]+(/[^/]+)*$'

# Layer 4 (RPC defense) — Migration 019's rename_folder_prefix has its own canonical-form regex check.
```

**Convention:** Pitfall 4 enforcement is FOUR layers. The Python helper is the primary; everything else is defense in depth. Belt+suspenders means a malformed value gets caught at the Python layer (clean 400) before reaching the DB layer (opaque 500).

### CLAUDE.md scoped-cleanup rule
**Source:** `backend/scripts/test_two_scope_rls.py:39-77` + `backend/scripts/test_backfill.py:108-130`
**Apply to:** `test_folders.py`'s entire fixture surface.

```python
_tracked_documents: list = []  # list[(doc_id, sb_client)]
_tracked_folders: list = []    # list[(folder_id, sb_client)]


def _track_doc(doc_id, sb_client):
    _tracked_documents.append((doc_id, sb_client))


def _track_folder(folder_id, sb_client):
    _tracked_folders.append((folder_id, sb_client))


def _cleanup():
    """Delete ONLY tracked resources. Per CLAUDE.md: never bulk-delete."""
    for did, client in _tracked_documents:
        try:
            client.table("document_chunks").delete().eq("document_id", did).execute()
            client.table("documents").delete().eq("id", did).execute()
        except Exception:
            pass
    for fid, client in _tracked_folders:
        try:
            client.table("folders").delete().eq("id", fid).execute()
        except Exception:
            pass
    _tracked_documents.clear()
    _tracked_folders.clear()
```

**Convention:** EVERY new test in Phase 3 calls `_track_doc()` / `_track_folder()` immediately after a successful insert/upload. `_cleanup()` runs in a single `finally` at the bottom of `run()`. Per CLAUDE.md: NEVER `DELETE FROM` or `TRUNCATE` on production tables. Cleanup deletes ONLY tracked IDs.

### sys.path bootstrap for scripts
**Source:** `backend/scripts/test_two_scope_rls.py:32-37` + `backend/scripts/test_backfill.py:39-40`
**Apply to:** `test_folders.py`.

```python
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))                             # for test_helpers
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))    # for app.*

import test_helpers as h
from app.services.folder_service import normalize_path
```

**Convention:** Two `sys.path.insert(0, ...)` calls — first puts `scripts/` on path (sibling test imports), second puts `backend/` on path (so `app.services.X` resolves). Do this BEFORE any `from app...` imports.

### Idempotent migration (DROP-then-CREATE)
**Source:** `backend/migrations/015_two_scope_rls.sql:60-68, 209-222` + `backend/migrations/018_storage_rls.sql:32-33`
**Apply to:** Migration 019.

```sql
-- For functions: CREATE OR REPLACE handles idempotency natively.
CREATE OR REPLACE FUNCTION public.rename_folder_prefix(...)
  ...

-- For policies / triggers (not used in 019, but pattern to remember):
DROP POLICY IF EXISTS "name" ON public.table;
CREATE POLICY "name" ...
```

**Convention:** `CREATE OR REPLACE FUNCTION` is the canonical idempotent form for functions (matches every existing function definition in `008_hybrid_search.sql:11, 28`, `015_two_scope_rls.sql:27, 44`, `007_document_metadata.sql:36`). For policies/triggers, the DROP-then-CREATE pattern from `015:60-68, 209-222` is canonical. Migration 019 uses functions only; CREATE OR REPLACE is sufficient.

---

## No Analog Found

Every Phase 3 file has a strong codebase analog. **No NEW PATTERNS this phase** (unlike Phase 2 which introduced Storage upload + argparse from scratch).

Two minor patterns are first-of-kind in the codebase but trivially derivable:

| Pattern | First codebase use | Source | Why not a "real" gap |
|---------|-------------------|--------|---------------------|
| `JSONResponse(status_code=409, content={...})` for structured non-200 | Phase 3 `routers/folders.py` DELETE | FastAPI canonical idiom — `from fastapi.responses import JSONResponse` | Documented at https://fastapi.tiangolo.com/advanced/custom-response/ ; ~3 lines of straightforward code |
| `concurrent.futures.ThreadPoolExecutor(max_workers=10)` for race-test fixture | Phase 3 `test_folders.py` Pitfall-10 test | Python stdlib — `import concurrent.futures` | Pattern excerpt provided verbatim from RESEARCH.md §Concurrent-upload-no-orphan test fixture |

Both are stdlib / first-party FastAPI; neither requires external research or design discussion.

---

## Phase 3 → Phase 4 / Phase 6 Forward Contracts (locked here, consumed there)

### Phase 4 (`grep`, `read_document`) — already locked by Phase 2

Phase 2 PATTERNS.md §Phase 2 → Phase 4 Forward Contract documents that any tool reading a row with `content_markdown_status != 'ready'` returns a structured `pending_reindex` object. Phase 3 does NOT change this contract — it only ADDS `folder_path` and `scope` fields to the row shape. Phase 4 tools must include these fields in tool output (e.g., `tree`, `list_files`, `read_document`).

### Phase 6 (UI) consumes the structured 409

When the Phase 6 UI calls `DELETE /api/folders/{id}` and receives a 409 with `{error: 'FOLDER_NOT_EMPTY', document_count: 12, subfolder_count: 3}`, it must surface "Folder not empty: 12 documents and 3 subfolders" to the user. Phase 3 ships this body shape; Phase 6 consumes it. Any change to the field names in this object is a breaking change for Phase 6.

### Phase 6 consumes the rename RPC result

`PATCH /api/folders/{id}` returns the merged dict `{...folder, path: new_path, documents_updated: N, folders_updated: M}`. The `documents_updated` / `folders_updated` counters are useful for the UI to show "Renamed folder: 47 documents updated." Locked here.

---

## Metadata

**Analog search scope:**
- `backend/scripts/` — all 21 .py files (test runners, helpers, migrations runner, schema verifier)
- `backend/app/services/` — all 11 .py files (ingestion, record_manager, folder_service, etc.)
- `backend/app/routers/` — all 5 .py files (threads, messages, files, settings — for full CRUD shape)
- `backend/app/auth.py` — admin gate + service-role client analog
- `backend/app/main.py` — router registration site
- `backend/app/models/schemas.py` — Pydantic naming + style
- `backend/migrations/` — all 18 .sql files (PL/pgSQL function shapes from 008/011/015; idempotent RLS pattern from 015/018; unique expression index from 013)
- `.planning/phases/02-content-markdown-backfill-gated/02-PATTERNS.md` — structural template + conventions reference
- `.planning/phases/03-folder-service-routers-dedup-extension/03-RESEARCH.md` — paste-ready code excerpts for every Phase 3 file

**Files scanned:** ~52 source files across 7 directories.
**Pattern extraction date:** 2026-05-07
**Total analogs read:** 14 files (threads.py, files.py, settings.py, messages.py, folder_service.py, record_manager.py, auth.py, main.py, schemas.py, ingestion.py, test_files.py, test_two_scope_rls.py, test_backfill.py, test_helpers.py) + 6 SQL migrations (008, 012, 013, 015, 016, 018) + Phase 2's PATTERNS.md.

---

## PATTERN MAPPING COMPLETE
