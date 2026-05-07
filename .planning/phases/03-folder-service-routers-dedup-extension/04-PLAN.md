---
phase: 03
plan: 04
type: execute
wave: 3
depends_on: [01, 02]
files_modified:
  - backend/app/routers/folders.py
  - backend/app/main.py
autonomous: true
requirements:
  - FOLDER-06
must_haves:
  truths:
    - "backend/app/routers/folders.py exists with four endpoints under prefix /api/folders: GET '' (list), POST '' (create), PATCH '/{folder_id}' (rename), DELETE '/{folder_id}' (delete-if-empty)"
    - "GET /api/folders supports query args path: str (default '/') and scope: str (default 'both', regex '^(user|global|both)$') and returns the {path, documents, subfolders} shape from folder_service.list_folder()"
    - "POST /api/folders accepts body FolderCreate (path, scope='user') and applies the inline admin gate ONLY when body.scope=='global' — non-admin returns 403 with detail='Admin required for global scope'; admin or scope='user' proceeds (FOLDER-06 admin gate)"
    - "PATCH /api/folders/{folder_id} accepts body FolderPatch (new_path) — looks up the existing folder via .maybe_single(), 404 if missing, applies admin gate AFTER lookup if existing.scope=='global', then calls folder_service.rename_folder() and returns {**folder, path: new_path, documents_updated, folders_updated}"
    - "DELETE /api/folders/{folder_id} looks up the folder, 404 if missing, applies admin gate if existing.scope=='global', then calls folder_service.delete_folder() — on deleted=False returns JSONResponse(status_code=409, content={error: 'FOLDER_NOT_EMPTY', document_count, subfolder_count}); on deleted=True returns {status: 'deleted'} (FOLDER-04 structured 409)"
    - "POST/PATCH normalize their path/new_path arguments via normalize_path() at the top of the handler (belt) — the service layer also normalizes (suspenders)"
    - "backend/app/main.py imports `folders` in the routers import line and calls `app.include_router(folders.router)` AFTER files.router and BEFORE settings.router"
    - "GET /api/folders without an Authorization header returns 401 (Phase 1 auth gate from get_current_user); with valid auth returns 200 and the structured listing"
    - "POST /api/folders {path: '/test'} as a regular user with scope='user' returns 200 + the FolderResponse shape (id, scope, user_id, path, created_at)"
    - "POST /api/folders {path: '/test', scope: 'global'} as a non-admin returns 403; as an admin returns 200 + the FolderResponse with scope='global' and user_id=None"
    - "Concurrent POST /api/folders for the same path produces exactly ONE folders row — the second call returns the existing row with action='exists' (Migration 019's create_folder_if_not_exists handles this atomically via ON CONFLICT DO NOTHING)"
  artifacts:
    - path: "backend/app/routers/folders.py"
      provides: "FastAPI router for folder CRUD with admin gate for global writes; structured 409 for non-empty deletes"
      contains: "@router.get(\"\")"
      contains_2: "@router.post(\"\""
      contains_3: "@router.patch(\"/{folder_id}\""
      contains_4: "@router.delete(\"/{folder_id}\""
      contains_5: "JSONResponse(status_code=409"
      contains_6: "FOLDER_NOT_EMPTY"
      contains_7: "Depends(get_current_user)"
      contains_8: "from app.services.folder_service import"
      contains_9: "from app.models.schemas import FolderResponse, FolderCreate, FolderPatch"
      min_lines: 100
    - path: "backend/app/main.py"
      provides: "Folders router registered in the FastAPI app"
      contains: "from app.routers import"
      contains_2: "include_router(folders.router)"
  key_links:
    - from: "POST/PATCH/DELETE /api/folders"
      to: "folder_service.create_folder / rename_folder / delete_folder (Plan 02)"
      via: "Direct function calls"
      pattern: "create_folder\\(|rename_folder\\(|delete_folder\\("
    - from: "Admin gate (inline)"
      to: "auth.py:46-51 (get_user_profile + is_admin check)"
      via: "Mirror of get_admin_user inline so the gate can be conditional on body.scope or existing.scope"
      pattern: "profile.get\\(\"is_admin\""
    - from: "main.py registration"
      to: "FastAPI app routing table"
      via: "include_router(folders.router) — AFTER files.router, BEFORE settings.router"
      pattern: "include_router\\(folders\\.router\\)"
---

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Unauthenticated client -> Backend | get_current_user dependency rejects requests without a valid JWT (returns 401); applies to ALL four endpoints in folders.py |
| Authenticated user -> /api/folders POST/PATCH/DELETE on global-scope rows | Admin gate (inline mirror of auth.py:46-51) returns 403 if profile.is_admin is not true; defense in depth alongside Migration 015 RLS policies |
| Frontend -> structured 409 response | The {error: 'FOLDER_NOT_EMPTY', document_count, subfolder_count} body is locked here for Phase 6 UI consumption — any field-rename is a breaking change |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-3-04-PathTraversal | Tampering | path / new_path query/body args | mitigate | Every handler calls normalize_path() at the TOP of the handler body (belt). The service layer (Plan 02) also normalizes (suspenders). The DB CHECK from Migration 013:24-26 is the bedrock. The Migration 019 RPCs have their own canonical-form regex check (fourth layer). normalize_path() raises ValueError on `'.'` and `'..'` segments — the router catches ValueError and returns HTTPException(status_code=400, detail=str(e)) for clean DX. |
| T-3-04-AdminGateBypass | Privilege Escalation | POST /api/folders with body.scope='global' as a non-admin | mitigate | The handler does NOT use Depends(get_admin_user) directly because the gate is BODY-CONDITIONAL (admin required only when body.scope='global'). It uses the inline mirror via _require_admin() helper: `profile = get_user_profile(user_id); if not profile or not profile.get('is_admin'): raise HTTPException(403, ...)`. Same pattern for PATCH/DELETE on rows where existing.scope='global'. Migration 015 RLS is the bedrock; this is the clean-403 layer. |
| T-3-04-CrossUserPATCHDelete | Information Disclosure / Tampering | User A PATCHes/DELETEs User B's folder via the {folder_id} path param | mitigate | The handler looks up the folder via .maybe_single(); RLS enforces user_id=auth.uid() OR scope='global' on the SELECT (Migration 015 documents_select policy applies to folders too via folders_select). For service-role clients (CONCERNS.md: codebase uses service-role everywhere), the lookup returns the row regardless, but the rename_folder/delete_folder RPCs call SECURITY INVOKER which re-applies RLS. Defense in depth: future maintainers can add `.eq('user_id', user_id)` to the lookup if the service-role anti-pattern is removed. |
| T-3-04-FolderNotEmpty | Tampering / Race condition | DELETE /api/folders/{id} on non-empty folder | mitigate | The Migration 019 RPC `delete_folder_if_empty` does the empty-check + DELETE inside a single PL/pgSQL transaction with FOR UPDATE row lock — TOCTOU race eliminated. The router maps the deleted=FALSE branch to JSONResponse(status_code=409, content={...}) — a 200 false-positive would let the frontend believe the delete succeeded. |
| T-3-04-RenameRollback | Data Integrity | Mid-rename failure leaves partial state on documents but not folders (or vice versa) | mitigate | The Migration 019 RPC `rename_folder_prefix` runs both UPDATEs in one PL/pgSQL block (implicitly transactional) — partial state is impossible. The router has no role here; the bedrock is at the DB. Plan 06's test_folders.py exercises this with a deliberate-fail RPC variant. |
| T-3-04-MissingFolder | Operational | Lookup with bogus folder_id | mitigate | The handler's .maybe_single() lookup returns no data; handler raises HTTPException(404, "Folder not found"). For DELETE, the RPC also raises 'no_data_found' SQLSTATE if the folder is missing — service layer's `delete_folder` does not catch it; the router catches Exception and returns 404. |
| T-3-04-RouterMissing | Operational | folders.py exists but main.py never includes the router (Pitfall E in RESEARCH) | mitigate | Plan 04 explicitly modifies main.py to include the router. Plan 06's test_folders.py canary precheck probes `GET /api/folders` and bails with [FATAL] if the response is 404 (router not registered). |
</threat_model>

<objective>
Build the new `backend/app/routers/folders.py` router with four endpoints (GET / POST / PATCH / DELETE) under the `/api/folders` prefix, and register it in `backend/app/main.py`. The router consumes the Pydantic models added in Plan 01 (FolderResponse, FolderCreate, FolderPatch) and the service-layer functions added in Plan 02 (list_folder, create_folder, rename_folder, delete_folder).

Two patterns are first-of-kind in this codebase:
1. `JSONResponse(status_code=409, content={...})` for the structured FOLDER_NOT_EMPTY response (DELETE non-empty folder). Phase 6 UI consumes this body shape.
2. The inline admin-gate mirror of `auth.py:46-51` is required because the gate is BODY-CONDITIONAL (admin required only when body.scope='global' for POST, or existing.scope='global' for PATCH/DELETE). FastAPI's `Depends(get_admin_user)` evaluates BEFORE body parsing, so it cannot be conditional on body content.

The router structure mirrors `backend/app/routers/threads.py` (4-endpoint CRUD) with the addition of the inline admin gate. Every path-accepting handler calls `normalize_path()` at the top of the handler body (Pitfall 4 belt; the service layer adds the suspenders).

This plan also updates `backend/app/main.py` to add `folders` to the routers import line and `app.include_router(folders.router)` AFTER `files.router` and BEFORE `settings.router` (logical grouping: file/folder family adjacent).
</objective>

<execution_context>
@.claude/get-shit-done/workflows/execute-plan.md
@.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/REQUIREMENTS.md
@CLAUDE.md

@.planning/phases/03-folder-service-routers-dedup-extension/03-RESEARCH.md
@.planning/phases/03-folder-service-routers-dedup-extension/03-PATTERNS.md
@.planning/phases/03-folder-service-routers-dedup-extension/03-VALIDATION.md

@.planning/phases/03-folder-service-routers-dedup-extension/01-PLAN.md
@.planning/phases/03-folder-service-routers-dedup-extension/02-PLAN.md

@backend/app/routers/threads.py
@backend/app/routers/files.py
@backend/app/auth.py
@backend/app/main.py
@backend/app/models/schemas.py
@backend/app/services/folder_service.py

<interfaces>
<!-- The HTTP API contracts this plan ESTABLISHES — Plan 06's test_folders.py asserts these. -->

GET /api/folders
  Query args:
    path: str = '/'           — canonical (server normalizes anyway)
    scope: str = 'both'       — regex '^(user|global|both)$'
  Auth: Depends(get_current_user)  -> 401 without JWT
  Response: 200 with body {path: str, documents: list[dict], subfolders: list[str]}
  Errors:
    - 400 on ValueError from normalize_path (path-traversal segments)
    - 401 on missing/invalid JWT (handled by get_current_user)

POST /api/folders
  Body: FolderCreate { path: str, scope: str = 'user' }
  Auth: Depends(get_current_user)
  Admin gate: inline check WHEN body.scope == 'global' (403 if not admin)
  Response: 200 with FolderResponse { id, scope, user_id, path, created_at }
  Errors:
    - 400 on ValueError from normalize_path
    - 401 on missing/invalid JWT
    - 403 on non-admin POST with scope='global'

PATCH /api/folders/{folder_id}
  Body: FolderPatch { new_path: str }
  Auth: Depends(get_current_user)
  Lookup: .maybe_single() on folders.id == folder_id
  Admin gate: inline check WHEN existing.scope == 'global' (403 if not admin)
  Service call: rename_folder(existing.path, new_path_norm, existing.scope, existing.user_id, sb)
  Response: 200 with merged dict {**existing_folder, path: new_path_norm, documents_updated, folders_updated}
  Errors:
    - 400 on ValueError (root rename or non-canonical input)
    - 401 on missing/invalid JWT
    - 403 on non-admin PATCH on global-scope folder
    - 404 if folder not found

DELETE /api/folders/{folder_id}
  Auth: Depends(get_current_user)
  Lookup: .maybe_single() on folders.id == folder_id
  Admin gate: inline check WHEN existing.scope == 'global' (403 if not admin)
  Service call: delete_folder(folder_id, sb)
  Response:
    - 200 {status: 'deleted'} on success (deleted=True)
    - 409 JSONResponse {error: 'FOLDER_NOT_EMPTY', document_count: int, subfolder_count: int} on non-empty
  Errors:
    - 401 on missing/invalid JWT
    - 403 on non-admin DELETE on global-scope folder
    - 404 if folder not found (catches the no_data_found Exception from the RPC)
</interfaces>
</context>

<tasks>

<task id="3-04-01" type="auto">
  <name>Task 1: Create backend/app/routers/folders.py with the four CRUD endpoints + inline admin gates</name>
  <files>backend/app/routers/folders.py</files>
  <read_first>
    - backend/app/routers/threads.py FULL FILE (PRIMARY analog — 4-endpoint CRUD shape; APIRouter prefix/tags pattern; Depends(get_current_user) signature; .maybe_single() + 404 pattern; HTTPException raise pattern)
    - backend/app/routers/files.py FULL FILE (SECONDARY analog — supabase RPC invocation shape; logger module-level; the existing /api/files endpoints to mirror tags/prefix style)
    - backend/app/auth.py FULL FILE (PRIMARY pattern source — inline admin gate `profile = get_user_profile(user_id); if not profile or not profile.get('is_admin'): raise HTTPException(403, ...)` is the verbatim mirror of L46-51 in get_admin_user)
    - backend/app/services/folder_service.py (the five functions Plan 02 added — list_folder, create_folder, rename_folder, delete_folder; signature shapes locked in Plan 02's interfaces)
    - backend/app/models/schemas.py (the four new Pydantic models from Plan 01 — FolderResponse, FolderCreate, FolderPatch; FilePatch is for Plan 05, not used here)
    - .planning/phases/03-folder-service-routers-dedup-extension/03-RESEARCH.md §Folders Router Design (paste-ready endpoint bodies; admin-gate two-shape discussion)
    - .planning/phases/03-folder-service-routers-dedup-extension/03-PATTERNS.md §`backend/app/routers/folders.py` (paste-ready imports + endpoint shapes + convention notes)
    - .planning/phases/03-folder-service-routers-dedup-extension/03-RESEARCH.md §Pitfall D (RESEARCH.md L898 — "Returning 500 instead of structured 409 on FOLDER_NOT_EMPTY"; explains why JSONResponse vs HTTPException)
    - CLAUDE.md (Python backend uses venv; Stack: FastAPI; Use Pydantic; All tables need RLS — relevant: routers reuse Phase 1 RLS via supabase_client)
  </read_first>
  <action>
    Create `backend/app/routers/folders.py` with the EXACT structure below. The file is paste-ready; do not deviate from the imports, prefix, dependency declarations, or response shapes.

    ### Full file content

    ```python
    import logging

    from fastapi import APIRouter, Depends, HTTPException, Query
    from fastapi.responses import JSONResponse

    from app.auth import get_current_user, get_user_profile, get_supabase_client
    from app.models.schemas import FolderResponse, FolderCreate, FolderPatch
    from app.services.folder_service import (
        normalize_path,
        list_folder,
        create_folder,
        rename_folder,
        delete_folder,
    )

    logger = logging.getLogger(__name__)

    router = APIRouter(prefix="/api/folders", tags=["folders"])


    def _require_admin(user_id: str, action: str) -> None:
        """Inline admin gate (mirror of auth.py:46-51).

        Used when the admin requirement is body-conditional or row-conditional and
        therefore cannot be expressed via Depends(get_admin_user) (which evaluates
        BEFORE body parsing). Raises HTTPException(403) if the user is not admin.
        """
        profile = get_user_profile(user_id)
        if not profile or not profile.get("is_admin"):
            raise HTTPException(
                status_code=403,
                detail=f"Admin required for {action}",
            )


    @router.get("")
    async def list_folders(
        path: str = Query("/", description="Canonical folder path to list"),
        scope: str = Query("both", regex="^(user|global|both)$",
                           description="Filter scope: user | global | both (union)"),
        user_id: str = Depends(get_current_user),
    ):
        sb = get_supabase_client()
        try:
            norm = normalize_path(path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return list_folder(norm, scope, user_id, sb)


    @router.post("", response_model=FolderResponse)
    async def create_folder_endpoint(
        body: FolderCreate,
        user_id: str = Depends(get_current_user),
    ):
        sb = get_supabase_client()
        try:
            norm = normalize_path(body.path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        if body.scope == "global":
            _require_admin(user_id, "global scope")
            return create_folder(norm, "global", None, sb)

        return create_folder(norm, "user", user_id, sb)


    @router.patch("/{folder_id}", response_model=FolderResponse)
    async def rename_folder_endpoint(
        folder_id: str,
        body: FolderPatch,
        user_id: str = Depends(get_current_user),
    ):
        sb = get_supabase_client()

        # Look up the existing folder to determine its scope (admin-gate decision input).
        try:
            existing_resp = (
                sb.table("folders").select("*").eq("id", folder_id).maybe_single().execute()
            )
        except Exception:
            raise HTTPException(status_code=404, detail="Folder not found")
        if not existing_resp or not existing_resp.data:
            raise HTTPException(status_code=404, detail="Folder not found")
        folder = existing_resp.data

        # Admin gate AFTER lookup — gate decision depends on existing.scope.
        if folder["scope"] == "global":
            _require_admin(user_id, "global folder rename")

        try:
            new_path_norm = normalize_path(body.new_path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        try:
            result = rename_folder(
                folder["path"], new_path_norm, folder["scope"], folder.get("user_id"), sb
            )
        except ValueError as e:
            # rename_folder raises on root-rename attempts (defense in depth).
            raise HTTPException(status_code=400, detail=str(e))

        logger.info(
            f"Folder renamed: id={folder_id} scope={folder['scope']} "
            f"old={folder['path']!r} new={new_path_norm!r} "
            f"docs_updated={result.get('documents_updated', 0)} "
            f"folders_updated={result.get('folders_updated', 0)}"
        )

        # Return merged dict — existing fields + new path + RPC counters.
        return {**folder, "path": new_path_norm, **result}


    @router.delete("/{folder_id}")
    async def delete_folder_endpoint(
        folder_id: str,
        user_id: str = Depends(get_current_user),
    ):
        sb = get_supabase_client()

        # Lookup for admin-gate decision; 404 cleanly if missing.
        try:
            existing_resp = (
                sb.table("folders").select("*").eq("id", folder_id).maybe_single().execute()
            )
        except Exception:
            raise HTTPException(status_code=404, detail="Folder not found")
        if not existing_resp or not existing_resp.data:
            raise HTTPException(status_code=404, detail="Folder not found")
        folder = existing_resp.data

        if folder["scope"] == "global":
            _require_admin(user_id, "global folder delete")

        # Race-free empty-check + delete via the Migration 019 RPC.
        try:
            result = delete_folder(folder_id, sb)
        except Exception as e:
            # The RPC raises 'no_data_found' SQLSTATE if the folder vanished between
            # lookup and delete (concurrent delete from another session). Map to 404.
            msg = str(e).lower()
            if "no_data_found" in msg or "not found" in msg:
                raise HTTPException(status_code=404, detail="Folder not found")
            logger.error(f"delete_folder RPC failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Delete failed: {e}")

        if not result.get("deleted"):
            return JSONResponse(
                status_code=409,
                content={
                    "error": "FOLDER_NOT_EMPTY",
                    "document_count": result.get("document_count", 0),
                    "subfolder_count": result.get("subfolder_count", 0),
                },
            )

        logger.info(
            f"Folder deleted: id={folder_id} scope={folder['scope']} path={folder['path']!r}"
        )
        return {"status": "deleted"}
    ```

    Critical DON'Ts:
    - DO NOT use `Depends(get_admin_user)` for any of these endpoints — the admin gate is BODY/ROW conditional. FastAPI's `Depends(...)` is request-time-static and evaluates BEFORE body parsing.
    - DO NOT add `.eq('user_id', user_id)` to the maybe_single lookup unconditionally — it would block admin DELETEs on global folders (where folders.user_id IS NULL but the operator is an admin user). RLS handles this; the inline admin gate handles the specific global-scope case.
    - DO NOT wrap normalize_path calls without ValueError handling — the user-facing 400 vs 500 difference is what makes the API friendly.
    - DO NOT raise HTTPException for the FOLDER_NOT_EMPTY case — the body shape is structured (`{error, document_count, subfolder_count}`); HTTPException returns `{detail: ...}` which loses the structure. Use `JSONResponse(status_code=409, content={...})` (Pitfall D in 03-RESEARCH.md).
    - DO NOT add a separate "soft delete" endpoint or trash flag (out of scope; v2 TRASH-01).
    - DO NOT add bulk operations (out of scope; v2 BULK-01/BULK-02).
    - DO NOT touch any file other than `backend/app/routers/folders.py` in this task — Task 2 owns main.py.
    - DO NOT add LangSmith @traceable on these endpoints (out of scope per CONVENTIONS.md — routers are not @traceable; only tool functions are).
    - DO NOT log JWT tokens or user PII in the logger.info calls — the structured fields are id/scope/path/counts only.
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -c "import pathlib, ast; src = pathlib.Path('app/routers/folders.py').read_text(encoding='utf-8'); ast.parse(src); body = '\n'.join(line for line in src.splitlines() if not line.lstrip().startswith('#')); assert '@router.get(\"\")' in body, 'GET endpoint missing'; assert '@router.post(\"\"' in body, 'POST endpoint missing'; assert '@router.patch(\"/{folder_id}\"' in body, 'PATCH endpoint missing'; assert '@router.delete(\"/{folder_id}\"' in body, 'DELETE endpoint missing'; assert 'JSONResponse(status_code=409' in body, 'structured 409 response missing'; assert 'FOLDER_NOT_EMPTY' in body, 'FOLDER_NOT_EMPTY error code missing'; assert 'Depends(get_current_user)' in body, 'auth dependency missing'; assert 'Depends(get_admin_user)' not in body, 'must use inline admin gate, NOT Depends(get_admin_user) (body/row-conditional)'; assert 'profile.get(\"is_admin\")' in body, 'inline admin check missing'; assert 'normalize_path(' in body, 'normalize_path called somewhere'; assert body.count('normalize_path(') &gt;= 3, 'normalize_path should be called in GET, POST, PATCH (at least 3 times)'; assert 'create_folder(' in body, 'service create_folder call missing'; assert 'rename_folder(' in body, 'service rename_folder call missing'; assert 'delete_folder(' in body, 'service delete_folder call missing'; assert 'list_folder(' in body, 'service list_folder call missing'; assert 'APIRouter(prefix=\"/api/folders\"' in body, 'router prefix wrong'; assert 'tags=[\"folders\"]' in body, 'tags missing/wrong'; assert 'logger = logging.getLogger(__name__)' in body, 'module-level logger missing'; assert '@traceable' not in body, '@traceable out of scope for routers'; print('folders.py OK; line count =', len(src.splitlines()))"</automated>
  </verify>
  <acceptance_criteria>
    - File `backend/app/routers/folders.py` exists.
    - File parses as valid Python (`ast.parse` succeeds).
    - File contains `@router.get("")` (1 occurrence).
    - File contains `@router.post(""` (POST endpoint, 1 occurrence).
    - File contains `@router.patch("/{folder_id}"` (PATCH endpoint, 1 occurrence).
    - File contains `@router.delete("/{folder_id}"` (DELETE endpoint, 1 occurrence).
    - File contains `JSONResponse(status_code=409` (structured 409 — Pitfall D mitigation).
    - File contains the literal string `FOLDER_NOT_EMPTY`.
    - File contains `Depends(get_current_user)` (4 times — once per endpoint).
    - File does NOT contain `Depends(get_admin_user)` (admin gate is body/row-conditional; inline mirror is required).
    - File contains `profile.get("is_admin")` (inline admin check; matches auth.py:47).
    - File contains `normalize_path(` at least 3 times (GET, POST, PATCH each call it for their path arg).
    - File contains `create_folder(`, `rename_folder(`, `delete_folder(`, `list_folder(` (service calls).
    - File contains `APIRouter(prefix="/api/folders", tags=["folders"])` exactly.
    - File contains `logger = logging.getLogger(__name__)` (module-level logger for rename/delete logs).
    - File does NOT contain `@traceable` (out of scope for routers).
    - Module imports cleanly: `cd backend && venv/Scripts/python -c "from app.routers import folders; assert folders.router.prefix == '/api/folders'; routes = [(r.methods, r.path) for r in folders.router.routes]; assert len(routes) == 4; print('OK')"` prints `OK`.
    - The four routes appear at runtime: GET /api/folders, POST /api/folders, PATCH /api/folders/{folder_id}, DELETE /api/folders/{folder_id}.
    - File length is at least 100 lines.
  </acceptance_criteria>
  <done>
    `backend/app/routers/folders.py` is created with the four CRUD endpoints, inline admin gates for global-scope writes, structured 409 for FOLDER_NOT_EMPTY, and Pitfall 4 path normalization at every entry point. Module imports cleanly and exposes the router with the four expected routes. Task 2 will register it in main.py.
  </done>
</task>

<task id="3-04-02" type="auto">
  <name>Task 2: Register folders router in backend/app/main.py</name>
  <files>backend/app/main.py</files>
  <read_first>
    - backend/app/main.py FULL FILE (the in-place edit point — L8 import line + L20-23 include_router calls; the file is 28 lines; the changes are 2 lines: add `folders` to the import on L8, add `app.include_router(folders.router)` between L22 and L23)
    - .planning/phases/03-folder-service-routers-dedup-extension/03-RESEARCH.md §Pitfall E (RESEARCH.md L905 — explains why this registration is mandatory; without it folders.py works in isolation but POST /api/folders returns 404 in production)
    - .planning/phases/03-folder-service-routers-dedup-extension/03-PATTERNS.md §`backend/app/main.py` (paste-ready edit pattern showing comma-separated import update + include_router placement)
  </read_first>
  <action>
    Modify `backend/app/main.py` with two edits:

    ### Edit 1: Update the routers import line (L8)

    Current line 8:
    ```python
    from app.routers import threads, messages, files, settings
    ```

    Change to:
    ```python
    from app.routers import threads, messages, files, folders, settings
    ```

    `folders` is inserted AFTER `files` (logical grouping — file/folder family is adjacent) and BEFORE `settings`.

    ### Edit 2: Add the include_router call

    Current L20-23 (the include_router block):
    ```python
    app.include_router(threads.router)
    app.include_router(messages.router)
    app.include_router(files.router)
    app.include_router(settings.router)
    ```

    Change to:
    ```python
    app.include_router(threads.router)
    app.include_router(messages.router)
    app.include_router(files.router)
    app.include_router(folders.router)
    app.include_router(settings.router)
    ```

    Insert `app.include_router(folders.router)` between `files.router` and `settings.router` (matches the import order; logical grouping by domain).

    Make NO other modifications. Do NOT touch the FastAPI app instantiation (L10), the CORSMiddleware block (L12-18), the load_dotenv call (L4), or the /health endpoint (L26-28).
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -c "src = open('app/main.py', encoding='utf-8').read(); assert 'folders' in src and 'from app.routers import' in src, 'folders not in import line'; assert 'app.include_router(folders.router)' in src, 'include_router(folders.router) missing'; idx_files = src.find('app.include_router(files.router)'); idx_folders = src.find('app.include_router(folders.router)'); idx_settings = src.find('app.include_router(settings.router)'); assert 0 &lt; idx_files &lt; idx_folders &lt; idx_settings, 'order must be files -> folders -> settings'; print('main.py registration OK')"</automated>
  </verify>
  <acceptance_criteria>
    - `grep -c "app.include_router(folders.router)" backend/app/main.py` returns 1.
    - The line `from app.routers import` in main.py contains the token `folders` (verifiable via `grep "from app.routers import" backend/app/main.py | grep folders`).
    - In main.py, the position of `app.include_router(folders.router)` is AFTER `app.include_router(files.router)` and BEFORE `app.include_router(settings.router)`.
    - The other 4 include_router calls (threads, messages, files, settings) remain present (5 include_router calls total — 4 existing + 1 new).
    - The CORSMiddleware block, load_dotenv call, FastAPI() instantiation, and /health endpoint are UNCHANGED.
    - Module imports cleanly: `cd backend && venv/Scripts/python -c "import sys; sys.path.insert(0, '.'); from app.main import app; routes = [getattr(r, 'path', None) for r in app.routes]; folder_routes = [r for r in routes if r and r.startswith('/api/folders')]; assert len(folder_routes) >= 4, f'expected at least 4 /api/folders routes, got {folder_routes}'; print(f'folders routes mounted: {sorted(folder_routes)}')"` prints a sorted list including `/api/folders`, `/api/folders/{folder_id}` (each may appear with multiple HTTP method bindings).
    - Total main.py line count remains close to 30 (was 28; +1-2 for the added include_router and whitespace).
  </acceptance_criteria>
  <done>
    `backend/app/main.py` registers the folders router via `app.include_router(folders.router)` placed between files.router and settings.router. The FastAPI app exposes the four /api/folders routes at runtime. Pitfall E is mitigated. Plan 06's test_folders.py canary precheck will succeed when GET /api/folders returns 401 (auth required) instead of 404 (router missing).
  </done>
</task>

</tasks>

<verification>
This plan delivers FOLDER-06 (folders router with GET/POST/PATCH/DELETE; admin gate for global writes) and partially delivers FOLDER-04 (the structured 409 error wiring; the DB-side empty-check is in Plan 01's Migration 019). Maps to .planning/phases/03-folder-service-routers-dedup-extension/03-VALIDATION.md row "3-03-* | 03 (folders router) | 3 | FOLDER-06 | T-admin-gate, T-cross-user".

Verification steps:
- AST parse + grep gates confirm folders.py has the four endpoints with the correct decorators, the structured 409 response, the inline admin gate, and the normalize_path() chokepoint enforcement.
- Runtime route inspection confirms the four /api/folders routes are mounted on the FastAPI app after Task 2's main.py edit.
- Plan 06's test_folders.py exercises:
  - FOLDER-06 happy path: POST /api/folders {path: '/test', scope: 'user'} returns 200 + FolderResponse shape.
  - FOLDER-06 admin gate: POST /api/folders {path: '/test', scope: 'global'} as non-admin returns 403; as admin returns 200.
  - FOLDER-04 non-empty: insert a doc into '/with-docs', POST /api/folders {path: '/with-docs'} (idempotent), then DELETE the folder -> 409 with {error: 'FOLDER_NOT_EMPTY', document_count: 1, subfolder_count: 0}.
  - FOLDER-03 happy path: POST /api/folders, then PATCH to rename -> 200 with {documents_updated, folders_updated} counts.
  - 404 paths: PATCH/DELETE on bogus folder_id returns 404.
  - 401 path: GET /api/folders without Authorization returns 401.
</verification>

<success_criteria>
- backend/app/routers/folders.py exists with four CRUD endpoints under /api/folders prefix.
- Each endpoint applies normalize_path() to path inputs at the top of the handler.
- POST/PATCH/DELETE apply the inline admin gate when the scope is global (body.scope or existing.scope).
- DELETE returns structured 409 JSONResponse on FOLDER_NOT_EMPTY (Phase 6 UI consumer contract locked).
- backend/app/main.py registers the router via include_router(folders.router) between files and settings.
- All four /api/folders routes are mounted at runtime; FastAPI startup succeeds.
- Plans 05 and 06 are unblocked (Plan 05 modifies files.py independently; Plan 06's canary probe will succeed against this router).
</success_criteria>

<output>
After completion, create `.planning/phases/03-folder-service-routers-dedup-extension/03-04-SUMMARY.md` recording: files created (folders.py) and modified (main.py), the four endpoints with their paths/methods, the inline admin gate pattern (vs. Depends), the structured 409 body shape locked for Phase 6 UI consumption, and a one-line confirmation that GET /api/folders, POST /api/folders, PATCH /api/folders/{id}, DELETE /api/folders/{id} are mounted on the running app.
</output>
