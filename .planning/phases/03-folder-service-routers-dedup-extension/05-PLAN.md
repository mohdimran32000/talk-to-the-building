---
phase: 03
plan: 05
type: execute
wave: 3
depends_on: [01, 03]
files_modified:
  - backend/app/routers/files.py
autonomous: true
requirements:
  - FOLDER-07
must_haves:
  truths:
    - "POST /api/files/upload accepts two new query args: folder_path: str = '/' (default preserves Phase 1/2 behavior — root-folder upload) and scope: str = 'user' (regex '^(user|global)$'; only 'user' or 'global' — NOT 'both')"
    - "POST /api/files/upload normalizes folder_path via normalize_path() at the top of the handler (Pitfall 4 belt) and rejects ValueError with HTTPException(400)"
    - "When scope='global', POST /api/files/upload applies the inline admin gate (mirror of auth.py:46-51) — non-admin returns 403"
    - "When scope='global', the documents row insert sets user_id=None (the coupling CHECK from Migration 012:23-37 requires user_id IS NULL for global rows)"
    - "When scope='global', the Storage upload path uses 'global' as the folder segment instead of None — `_upload_to_storage(supabase, user_id='global', ...)` — to avoid Pitfall F (None in path produces 'documents/None/{id}{ext}' which Storage RLS rejects)"
    - "POST /api/files/upload calls determine_action() with scope=scope and folder_path=folder_path kwargs (Plan 03's extension) — same file at two different paths now creates two rows (FOLDER-05 acceptance via the upload path)"
    - "documents.insert() in the create branch includes scope and folder_path columns (with the normalized values); the existing fields (file_name, file_size, mime_type, status='pending') are unchanged"
    - "documents.update() in the action='update' branch is unchanged for scope/folder_path — the existing row's scope is immutable (Migration 015 trigger); the existing row's folder_path is preserved unless explicitly changed via PATCH /api/files/{id}"
    - "PATCH /api/files/{file_id} is added — accepts body FilePatch (file_name?, folder_path?), 404 if missing, applies admin gate AFTER lookup if existing.scope=='global', normalizes folder_path, rejects empty update_data with 400, then UPDATEs the documents row and returns the updated row"
    - "PATCH /api/files/{id} body MUST NOT accept a scope field — FilePatch deliberately omits it (Plan 01 Pydantic model contract); Pydantic v2 ignores unknown fields so a smuggled scope is silently dropped (defense in depth alongside Migration 015 forbid_scope_mutation trigger)"
    - "Existing endpoints GET /api/files (list_files) and DELETE /api/files/{file_id} (delete_file) are UNCHANGED in this plan — only the upload handler and the new PATCH endpoint are added"
    - "The existing _upload_to_storage helper (L32-57), _ingestion_semaphore (L15), _throttled_ingest (L18-29) are UNCHANGED — only callers (the upload handler) change"
  artifacts:
    - path: "backend/app/routers/files.py"
      provides: "Extended upload handler with folder_path/scope query args + admin gate + new PATCH endpoint for rename/move"
      contains: "folder_path: str = Query("
      contains_2: "scope: str = Query("
      contains_3: "@router.patch(\"/{file_id}\""
      contains_4: "FilePatch"
      contains_5: "normalize_path("
      contains_6: "scope=scope"
      contains_7: "folder_path=folder_path"
      contains_8: "storage_user_segment"
  key_links:
    - from: "POST /api/files/upload upload_file()"
      to: "record_manager.determine_action() (Plan 03)"
      via: "determine_action(file_hash, file_name, user_id, supabase, scope=scope, folder_path=folder_path)"
      pattern: "scope=scope.*folder_path=folder_path"
    - from: "POST /api/files/upload (scope='global')"
      to: "Storage RLS Migration 018 path predicate"
      via: "storage_user_segment='global' instead of None — Storage path becomes 'documents/global/{id}{ext}' which Storage RLS rejects for the authenticated role; service-role bypasses (admin-only consumption pattern)"
      pattern: "storage_user_segment"
    - from: "PATCH /api/files/{id} body"
      to: "FilePatch Pydantic model (Plan 01)"
      via: "Pydantic v2 unknown-field-ignore + missing scope field == three-layer scope-immutability defense"
      pattern: "FilePatch"
---

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Multipart upload (UploadFile) -> /api/files/upload | The bytes are user-controlled; ingestion + Storage upload happen post-validation; folder_path/scope query args are user-controlled and normalized + admin-gated |
| Authenticated user -> /api/files/upload?scope=global OR PATCH /api/files/{id} on global-scope row | Inline admin gate (mirror of auth.py:46-51); non-admin gets 403 — defense in depth alongside Migration 015 RLS |
| Multipart filename -> Storage path | The Storage path is `f"{storage_user_segment}/{doc_id}{ext}"` where `ext` is `os.path.splitext(file_name)[1]`. The doc_id is server-generated UUID; only `ext` is user-controlled and limited to characters after the last `.` in the filename — no path components |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-3-05-PathTraversal | Tampering | folder_path query arg | mitigate | Handler runs `folder_path = normalize_path(folder_path)` at the TOP (belt). normalize_path raises ValueError on `'.'` and `'..'` segments — handler catches and raises HTTPException(400). Service layer (record_manager.determine_action via Plan 03) accepts canonical form as-is; DB CHECK from Migration 012:40-42 is the bedrock. |
| T-3-05-AdminGateBypass-Upload | Privilege Escalation | POST /api/files/upload?scope=global as non-admin | mitigate | Handler applies the inline admin-gate mirror (`profile = get_user_profile(user_id); if not profile or not profile.get('is_admin'): raise HTTPException(403, ...)`) when `scope == 'global'`. Migration 015 RLS policies are bedrock; this is the clean-403 layer. |
| T-3-05-AdminGateBypass-Patch | Privilege Escalation | PATCH /api/files/{id} on a global-scope document as non-admin | mitigate | After the maybe_single lookup, if `existing.scope == 'global'` the handler invokes _require_admin (or inline mirror). Defense in depth: even with service-role, the underlying RLS-aware queries respect scope; the gate exists for clean DX. |
| T-3-05-ScopeMutation | Privilege Escalation | PATCH /api/files/{id} with smuggled scope field in body | mitigate | Three-layer defense: (a) FilePatch Pydantic model deliberately omits `scope` field (Plan 01); Pydantic v2 ignores unknown fields by default — `scope` in body is silently dropped. (b) The handler builds update_data dict only from .file_name and .folder_path explicitly. (c) Migration 015's forbid_scope_mutation trigger raises check_violation if the UPDATE somehow includes scope=different. |
| T-3-05-StoragePathPitfallF | Tampering / Data Integrity | scope='global' upload with effective_user_id=None produces 'documents/None/{id}{ext}' | mitigate | Handler computes `storage_user_segment = user_id if scope == 'user' else 'global'` and passes the segment (NOT None) to `_upload_to_storage(...)`. Migration 018's RLS predicate `(SELECT auth.uid())::text = (storage.foldername(name))[1]` then evaluates as `'<uuid>' == 'global'` for the authenticated role — denied (which is the desired behavior for the regular auth role; admins/service-role bypass RLS to access global blobs). Pitfall F (RESEARCH.md L911) explicitly documents this. |
| T-3-05-FilenameInjection | Tampering | file_name with path components (e.g., '../etc/passwd') | mitigate | Storage path uses ONLY `os.path.splitext(file_name)[1]` (the extension after the LAST dot — no path separators in the result). doc_id is server-generated. file_name is stored in the documents row as TEXT (no path construction outside Storage). RESEARCH.md §Security Domain documents this assumption. |
| T-3-05-EmptyPatch | Operational | PATCH /api/files/{id} with empty body | mitigate | Handler builds update_data dict by checking `if body.file_name is not None: ...; if body.folder_path is not None: ...`. If both are None, raises HTTPException(400, "No fields to update") — clean DX vs. a no-op UPDATE. |
| T-3-05-CrossUserPatch | Information Disclosure | User A PATCHes User B's document via {file_id} | mitigate | The maybe_single lookup uses .eq('id', file_id) only — relies on RLS for user isolation in the SELECT. For service-role (CONCERNS.md anti-pattern), the lookup returns the row regardless. Migration 015 RLS on the subsequent UPDATE applies via the trigger system; if RLS blocks the UPDATE, supabase-py raises and the handler returns 500 (acceptable for this codebase's current state). Future work: tighten the SELECT to .eq('user_id', user_id) for non-admin users (out of scope this plan). |
</threat_model>

<objective>
Extend `backend/app/routers/files.py` with two changes that complete FOLDER-07:

1. **Upload handler (POST /api/files/upload)** — add `folder_path: str = Query("/")` and `scope: str = Query("user", regex="^(user|global)$")` query parameters, normalize folder_path, apply the inline admin gate when scope='global', compute `effective_user_id` (None for global), compute `storage_user_segment` (avoids Pitfall F's None-in-path), pass `scope=scope, folder_path=folder_path` kwargs to determine_action() (Plan 03's extension), and include scope+folder_path in the documents.insert() in the create branch.

2. **New PATCH endpoint (PATCH /api/files/{file_id})** — body FilePatch (file_name?, folder_path?), 404 if missing, admin gate after lookup if existing.scope='global', normalize folder_path, reject empty update_data with 400, UPDATE documents, return the updated row. The endpoint deliberately does NOT accept scope (FilePatch model omits it; Pydantic v2 ignores unknown fields; Migration 015 trigger is the bedrock).

The existing helpers (`_ingestion_semaphore`, `_throttled_ingest`, `_upload_to_storage`) are UNCHANGED. The list_files (GET) and delete_file (DELETE) endpoints are UNCHANGED. The action='skip' and action='update' branches of upload_file are UNCHANGED for their core ingestion logic — only the documents row payload (in the create branch's insert) and the determine_action call signature change.

Plan 03 already extended `determine_action` with scope/folder_path kwargs (defaults preserve back-compat); this plan upgrades the upload handler's call site to pass them explicitly. The two plans land in either order safely (Wave 2 ↔ Wave 3 — Plan 05 in Wave 3 depends on Plan 03 in Wave 2 plus Plan 01 in Wave 1 for the FilePatch model).
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
@.planning/phases/03-folder-service-routers-dedup-extension/03-PLAN.md

@backend/app/routers/files.py
@backend/app/auth.py
@backend/app/services/folder_service.py
@backend/app/services/record_manager.py
@backend/app/models/schemas.py
@backend/migrations/012_folder_path_and_scope.sql
@backend/migrations/015_two_scope_rls.sql
@backend/migrations/018_storage_rls.sql

<interfaces>
<!-- The HTTP API contracts this plan ESTABLISHES — Plan 06's test_folders.py asserts these. -->

POST /api/files/upload (extended)
  Multipart body: file: UploadFile (existing)
  Query args (NEW):
    folder_path: str = '/'                 — canonical (server normalizes)
    scope: str = 'user'                    — regex '^(user|global)$'
  Auth: Depends(get_current_user)          — 401 without JWT
  Admin gate: inline mirror WHEN scope == 'global' (403 if not admin)
  Behavior:
    - Normalize folder_path via normalize_path()
    - effective_user_id = None if scope='global' else user_id (coupling CHECK)
    - storage_user_segment = 'global' if scope='global' else user_id (Pitfall F)
    - determine_action(..., scope=scope, folder_path=folder_path) -> action='create'|'skip'|'update'
    - On create: documents.insert({user_id: effective_user_id, scope, folder_path, file_name, ...})
    - On update: existing row's scope/folder_path are PRESERVED (separate PATCH endpoint for moves)
    - On skip: existing row returned with action='skipped'
    - Storage upload uses storage_user_segment in the path
  Response: DocumentResponse (with the new folder_path + scope fields populated)

PATCH /api/files/{file_id} (NEW)
  Body: FilePatch { file_name?: Optional[str], folder_path?: Optional[str] }
  Auth: Depends(get_current_user)
  Lookup: .maybe_single() on documents.id == file_id
  Admin gate: inline check WHEN existing.scope == 'global' (403 if not admin)
  Behavior:
    - 404 if document not found
    - Build update_data: include file_name if not None; include folder_path (normalized) if not None
    - 400 if both fields are None (empty update)
    - UPDATE documents SET <update_data> WHERE id = file_id
    - Return the updated document row
  Response: DocumentResponse (with the new folder_path + scope fields populated)
  Errors:
    - 400 on empty update_data, ValueError from normalize_path
    - 401 on missing/invalid JWT
    - 403 on non-admin PATCH on global-scope document
    - 404 if document not found

Unchanged endpoints (preserved verbatim):
  - GET /api/files (list_files)
  - DELETE /api/files/{file_id} (delete_file)
</interfaces>
</context>

<tasks>

<task id="3-05-01" type="auto">
  <name>Task 1: Extend upload_file with folder_path/scope Query args + admin gate + storage_user_segment + determine_action kwargs</name>
  <files>backend/app/routers/files.py</files>
  <read_first>
    - backend/app/routers/files.py FULL FILE (the in-place edit point — L60-145 is the upload_file handler; the create branch is L116-122; the update branch is L82-101; the skip branch is L75-79; this task modifies the signature, normalizes folder_path, applies admin gate, computes effective_user_id + storage_user_segment, passes kwargs to determine_action, includes scope+folder_path in documents.insert)
    - backend/app/auth.py L37-51 (get_user_profile + get_admin_user — the inline admin gate mirror is the auth.py:46-51 body, called WHEN scope='global')
    - backend/app/services/folder_service.py L28-67 (normalize_path — imported at the top of files.py for the new normalization step)
    - backend/app/services/record_manager.py (Plan 03 has extended determine_action with scope+folder_path kwargs; the existing 4-arg positional call MUST be upgraded to pass these explicitly)
    - backend/migrations/012_folder_path_and_scope.sql L23-37 (the coupling CHECK constraint — explains why effective_user_id MUST be None for scope='global')
    - backend/migrations/018_storage_rls.sql L38, L52 (Storage RLS predicate `(SELECT auth.uid())::text = (storage.foldername(name))[1]` — explains why storage_user_segment must NOT be None; Pitfall F)
    - .planning/phases/03-folder-service-routers-dedup-extension/03-RESEARCH.md §Files Router Extensions (lines 615-657 — paste-ready edit pattern with explicit Pitfall F treatment)
    - .planning/phases/03-folder-service-routers-dedup-extension/03-PATTERNS.md §`backend/app/routers/files.py` (paste-ready edit pattern; convention notes on Query() with regex)
    - .planning/phases/03-folder-service-routers-dedup-extension/03-RESEARCH.md §Pitfall F (line 911 — definitive explanation of the storage_user_segment workaround)
  </read_first>
  <action>
    Modify `backend/app/routers/files.py` `upload_file` handler (currently L60-144). Make these changes in order:

    ### Edit 1: Update imports (L1-9)

    Current L1-9:
    ```python
    import logging
    import os
    import threading

    from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
    from app.auth import get_current_user, get_supabase_client
    from app.models.schemas import DocumentResponse
    from app.services.ingestion import ingest_document, ingest_document_update
    from app.services.record_manager import compute_file_hash, determine_action
    ```

    Change to (add `Query` to fastapi import; add `get_user_profile` to auth import; add `FilePatch` to schemas import; add `normalize_path` import from folder_service):
    ```python
    import logging
    import os
    import threading

    from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks, Query
    from app.auth import get_current_user, get_supabase_client, get_user_profile
    from app.models.schemas import DocumentResponse, FilePatch
    from app.services.folder_service import normalize_path
    from app.services.ingestion import ingest_document, ingest_document_update
    from app.services.record_manager import compute_file_hash, determine_action
    ```

    ### Edit 2: Extend the upload_file signature (L60-65) and body (L66-144)

    Replace the entire `upload_file` function (L60-144) with the version below. Preserve `_ingestion_semaphore`, `_throttled_ingest`, `_upload_to_storage` (L11-58) UNCHANGED.

    ```python
    @router.post("/upload", response_model=DocumentResponse)
    async def upload_file(
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        folder_path: str = Query("/", description="Canonical folder path"),
        scope: str = Query("user", regex="^(user|global)$",
                           description="'user' (default) or 'global'; admin required for 'global'"),
        user_id: str = Depends(get_current_user),
    ):
        supabase = get_supabase_client()
        contents = await file.read()
        file_name = file.filename or "unnamed"
        mime_type = file.content_type or "application/octet-stream"

        # ── Pitfall 4 belt: normalize the path at the router boundary ──
        try:
            folder_path = normalize_path(folder_path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        # ── Admin gate for global scope (inline mirror of auth.py:46-51) ──
        if scope == "global":
            profile = get_user_profile(user_id)
            if not profile or not profile.get("is_admin"):
                raise HTTPException(status_code=403, detail="Admin required for global scope")
            # Migration 012 coupling CHECK: scope='global' requires user_id IS NULL.
            effective_user_id = None
            # Pitfall F: avoid 'None' in storage path; use 'global' segment so the
            # path is well-formed. Migration 018 RLS rejects this for the authenticated
            # role; service-role bypasses (admin / backend reads work).
            storage_user_segment = "global"
        else:
            effective_user_id = user_id
            storage_user_segment = user_id

        # ── Record Manager: check for duplicates (Plan 03 extended dedup key) ──
        file_hash = compute_file_hash(contents)
        record_action = determine_action(
            file_hash, file_name, user_id, supabase,
            scope=scope, folder_path=folder_path,
        )

        if record_action.action == "skip":
            doc = supabase.table("documents").select("*") \
                .eq("id", record_action.document_id).single().execute().data
            doc["action"] = "skipped"
            return doc

        if record_action.action == "update":
            supabase.table("documents").update({
                "file_size": len(contents),
                "mime_type": mime_type,
                "status": "pending",
                "error_message": None,
                "updated_at": "now()",
            }).eq("id", record_action.document_id).execute()

            doc = supabase.table("documents").select("*") \
                .eq("id", record_action.document_id).single().execute().data

            _upload_to_storage(
                supabase,
                user_id=storage_user_segment,
                document_id=doc["id"],
                file_name=file_name,
                contents=contents,
                mime_type=mime_type,
            )

            background_tasks.add_task(
                _throttled_ingest,
                ingest_document_update,
                document_id=doc["id"],
                file_content=contents,
                mime_type=mime_type,
                file_name=file_name,
                user_id=user_id,
                supabase_client=supabase,
            )
            doc["action"] = "updated"
            return doc

        # ── action == "create": new document ──
        doc = supabase.table("documents").insert({
            "user_id": effective_user_id,
            "scope": scope,                  # NEW (Phase 3 / FOLDER-07)
            "folder_path": folder_path,      # NEW (Phase 3 / FOLDER-07)
            "file_name": file_name,
            "file_size": len(contents),
            "mime_type": mime_type,
            "status": "pending",
        }).execute().data[0]

        _upload_to_storage(
            supabase,
            user_id=storage_user_segment,
            document_id=doc["id"],
            file_name=file_name,
            contents=contents,
            mime_type=mime_type,
        )

        background_tasks.add_task(
            _throttled_ingest,
            ingest_document,
            document_id=doc["id"],
            file_content=contents,
            mime_type=mime_type,
            file_name=file_name,
            user_id=user_id,
            supabase_client=supabase,
        )
        doc["action"] = "created"
        return doc
    ```

    Critical DON'Ts:
    - DO NOT change `_upload_to_storage`'s signature; it still takes `user_id: str`. The variable being passed is now `storage_user_segment` (a regular Python string — either the JWT-derived UUID or the literal `'global'`). The helper's existing string-formatting `f"{user_id}/{document_id}{ext}"` works correctly for both cases.
    - DO NOT change `determine_action`'s call ORDER. Pass the new args as kwargs (`scope=scope, folder_path=folder_path`); positional ordering breaks Plan 03's signature contract.
    - DO NOT pass `effective_user_id` to `determine_action` — pass `user_id` (the JWT-derived caller). The function uses `user_id` only when scope='user'; for scope='global', it switches to `.is_('user_id', 'null')` (Plan 03's branching).
    - DO NOT change the `action == "update"` branch to overwrite the existing row's scope or folder_path. Migration 015's forbid_scope_mutation trigger blocks scope changes entirely; folder_path moves are exclusively the responsibility of PATCH /api/files/{id} (Task 2 of this plan).
    - DO NOT include `scope` or `folder_path` in the action='update' branch's `update_data` dict — the existing row's values are preserved (the upload here is a CONTENT update, not a metadata move).
    - DO NOT remove the existing logger.info calls inside `_upload_to_storage` (we're not editing that helper).
    - DO NOT add a `Depends(get_admin_user)` — the gate is BODY-CONDITIONAL on scope; inline mirror is required.
    - DO NOT touch the GET /api/files (list_files) or DELETE /api/files/{file_id} (delete_file) endpoints in this task — Task 2 owns the new PATCH endpoint.
    - DO NOT add a `scope` field to the action='update' branch's update_data dict (Migration 015 trigger raises check_violation; FilePatch model omits it for the same reason).
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -c "import pathlib, ast; src = pathlib.Path('app/routers/files.py').read_text(encoding='utf-8'); ast.parse(src); body = '\n'.join(line for line in src.splitlines() if not line.lstrip().startswith('#')); assert 'folder_path: str = Query(' in body, 'folder_path Query arg missing'; assert 'scope: str = Query(' in body, 'scope Query arg missing'; assert 'regex=\"^(user|global)$\"' in body or \"regex='^(user|global)\\\\$'\" in body, 'scope regex constraint missing'; assert 'normalize_path(folder_path)' in body, 'normalize_path call missing in upload handler'; assert 'get_user_profile(user_id)' in body, 'inline admin gate missing'; assert 'profile.get(\"is_admin\")' in body, 'is_admin check missing'; assert 'effective_user_id = None' in body, 'effective_user_id=None branch missing for global scope'; assert 'storage_user_segment' in body, 'storage_user_segment variable missing (Pitfall F)'; assert 'storage_user_segment = \"global\"' in body, 'storage_user_segment global value missing'; assert 'scope=scope, folder_path=folder_path' in body, 'determine_action kwargs missing'; assert '\"scope\": scope' in body, 'documents.insert must include scope column'; assert '\"folder_path\": folder_path' in body, 'documents.insert must include folder_path column'; assert 'from app.services.folder_service import normalize_path' in body, 'normalize_path import missing'; assert 'from app.auth import' in body and 'get_user_profile' in body, 'get_user_profile import missing'; assert 'FilePatch' in body, 'FilePatch import missing (used by Task 2)'; assert ', Query' in body or 'Query,' in body or 'Query)' in body or 'BackgroundTasks, Query' in body, 'Query import missing from fastapi'; print('files.py upload_file extensions OK')"</automated>
  </verify>
  <acceptance_criteria>
    - File `backend/app/routers/files.py` parses as valid Python (`ast.parse` succeeds).
    - File contains `folder_path: str = Query("/"` (new Query arg with default '/').
    - File contains `scope: str = Query("user"` (new Query arg with default 'user').
    - File contains the regex `^(user|global)$` constraining scope (NOT 'both' — uploads are single-scope).
    - File contains `normalize_path(folder_path)` (Pitfall 4 belt).
    - File contains `get_user_profile(user_id)` (inline admin-gate mirror).
    - File contains `profile.get("is_admin")` (admin check).
    - File contains the literal string `effective_user_id = None` (used in the global-scope branch — coupling CHECK).
    - File contains the literal string `storage_user_segment` (Pitfall F variable name).
    - File contains the literal string `storage_user_segment = "global"` (assigning the segment for global scope).
    - File contains `scope=scope, folder_path=folder_path` (the kwargs passed to determine_action — Plan 03 contract).
    - File contains `"scope": scope` in the documents.insert dict (the create branch's payload extension).
    - File contains `"folder_path": folder_path` in the documents.insert dict.
    - File contains `from app.services.folder_service import normalize_path` (new import).
    - File imports `get_user_profile` from app.auth (modified import).
    - File imports `FilePatch` from app.models.schemas (used by Task 2; importing here keeps imports together).
    - File contains `Query` somewhere in the fastapi import line.
    - The `_upload_to_storage`, `_ingestion_semaphore`, `_throttled_ingest` helpers are UNCHANGED — `grep -c "def _upload_to_storage(" backend/app/routers/files.py` returns 1 and matches L32 of the original.
    - The list_files endpoint (`async def list_files`) and delete_file endpoint (`async def delete_file`) are UNCHANGED in this task (Task 2 only adds patch_file; does not modify these).
    - Module imports cleanly: `cd backend && venv/Scripts/python -c "from app.routers import files; assert files.router.prefix == '/api/files'; print('OK')"` prints `OK`.
  </acceptance_criteria>
  <done>
    `backend/app/routers/files.py::upload_file()` accepts folder_path + scope Query args, applies the inline admin gate for scope='global', computes effective_user_id (None for global, per Migration 012 coupling CHECK) and storage_user_segment (Pitfall F mitigation), passes scope+folder_path kwargs to determine_action (Plan 03 contract), and includes scope+folder_path in the documents.insert payload. The existing _upload_to_storage helper, list_files endpoint, and delete_file endpoint are unchanged. Module still imports cleanly.
  </done>
</task>

<task id="3-05-02" type="auto">
  <name>Task 2: Add PATCH /api/files/{file_id} endpoint for rename + folder move</name>
  <files>backend/app/routers/files.py</files>
  <read_first>
    - backend/app/routers/files.py (post-Task-1 state — imports already include Query, FilePatch, normalize_path, get_user_profile)
    - backend/app/routers/folders.py (Plan 04 — the inline _require_admin pattern; the maybe_single + 404 + admin-gate-after-lookup pattern is identical here)
    - backend/app/models/schemas.py (FilePatch model — file_name?: Optional[str], folder_path?: Optional[str]; deliberately omits scope)
    - backend/migrations/015_two_scope_rls.sql L37-55 (forbid_scope_mutation trigger — bedrock for scope immutability; this endpoint is the third layer of defense)
    - .planning/phases/03-folder-service-routers-dedup-extension/03-RESEARCH.md §Files Router Extensions §"2. New PATCH endpoint" (lines 663-695 — paste-ready endpoint body)
    - .planning/phases/03-folder-service-routers-dedup-extension/03-RESEARCH.md §Pitfall B (line 887 — definitive explanation of why FilePatch omits scope)
  </read_first>
  <action>
    Append a NEW `patch_file` endpoint to `backend/app/routers/files.py` AFTER the existing `delete_file` function (currently L153-165). Insert one blank line after `delete_file`, then add the new endpoint.

    ### New endpoint (paste-ready)

    ```python


    @router.patch("/{file_id}", response_model=DocumentResponse)
    async def patch_file(
        file_id: str,
        body: FilePatch,
        user_id: str = Depends(get_current_user),
    ):
        sb = get_supabase_client()

        # Lookup for admin-gate decision; 404 cleanly if missing.
        try:
            doc_resp = sb.table("documents").select("*").eq("id", file_id).maybe_single().execute()
        except Exception:
            raise HTTPException(status_code=404, detail="Document not found")
        if not doc_resp or not doc_resp.data:
            raise HTTPException(status_code=404, detail="Document not found")
        existing = doc_resp.data

        # Admin gate AFTER lookup — gate decision depends on existing.scope.
        if existing["scope"] == "global":
            profile = get_user_profile(user_id)
            if not profile or not profile.get("is_admin"):
                raise HTTPException(status_code=403, detail="Admin required for global document")

        # CRITICAL: scope is IMMUTABLE — Migration 015's forbid_scope_mutation trigger
        # is the bedrock. FilePatch model deliberately omits scope (Pydantic v2 ignores
        # unknown fields on body parsing); explicit safety net via update_data dict
        # building (only file_name and folder_path get passed through).
        update_data: dict = {}
        if body.file_name is not None:
            update_data["file_name"] = body.file_name
        if body.folder_path is not None:
            try:
                update_data["folder_path"] = normalize_path(body.folder_path)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        sb.table("documents").update(update_data).eq("id", file_id).execute()
        return sb.table("documents").select("*").eq("id", file_id).single().execute().data
    ```

    Critical DON'Ts:
    - DO NOT add `scope` to update_data even defensively — explicitly building update_data from FilePatch fields means there's no path for `scope` to leak in. Migration 015 trigger is the bedrock; this is the second layer.
    - DO NOT call `_upload_to_storage` here — PATCH is a metadata-only operation (rename or move; no new bytes; the Storage object stays at the same `{user_id}/{doc_id}{ext}` path because doc_id and the original extension don't change).
    - DO NOT trigger `background_tasks.add_task(_throttled_ingest, ...)` here — no re-ingestion is needed for a metadata change. The chunks/embeddings/content_markdown remain valid.
    - DO NOT add `.eq('user_id', user_id)` to the maybe_single lookup unconditionally — admins need to PATCH global-scope documents (where user_id IS NULL). RLS handles per-user filtering; the inline admin gate handles the global-scope case.
    - DO NOT raise HTTPException(404) on the .update() call — the lookup already confirmed the row exists. If RLS subsequently blocks the UPDATE, supabase-py raises and FastAPI surfaces a 500 (acceptable for the existing service-role anti-pattern).
    - DO NOT change the response_model from DocumentResponse — Plan 01 extended DocumentResponse with folder_path and scope fields, so the response correctly reflects the post-PATCH state.
    - DO NOT remove the second-pass SELECT (`return sb.table("documents").select("*").eq("id", file_id).single().execute().data`); the .update() returns no data by default in supabase-py — re-selecting is required.
    - DO NOT add an idempotency key or ETag — out of scope for this phase.
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -c "import pathlib, ast; src = pathlib.Path('app/routers/files.py').read_text(encoding='utf-8'); ast.parse(src); body = '\n'.join(line for line in src.splitlines() if not line.lstrip().startswith('#')); assert '@router.patch(\"/{file_id}\"' in body, 'PATCH /{file_id} endpoint missing'; assert 'async def patch_file(' in body, 'patch_file function missing'; assert 'body: FilePatch' in body, 'FilePatch body parameter missing'; assert 'No fields to update' in body, 'empty update_data 400 missing'; assert 'normalize_path(body.folder_path)' in body, 'folder_path normalization missing'; assert 'profile.get(\"is_admin\")' in body, 'admin gate missing for global docs'; assert 'Admin required for global document' in body, 'admin error message missing'; assert 'scope=' not in body or body.count('scope=') == 1, 'no scope field allowed in update_data dict'; assert body.count('@router.patch(') == 1, 'expected exactly one @router.patch decorator'; assert body.count('@router.delete(') == 1, 'delete_file endpoint preserved'; assert body.count('@router.get(') == 1, 'list_files endpoint preserved'; assert body.count('@router.post(') == 1, 'upload endpoint preserved (still single POST)'; print('files.py patch_file endpoint OK')" &amp;&amp; venv/Scripts/python -c "import sys; sys.path.insert(0, '.'); from app.routers import files; routes = [(sorted(r.methods), r.path) for r in files.router.routes if hasattr(r, 'methods')]; print(f'files.router routes: {routes}'); paths_methods = set((tuple(m), p) for m, p in routes); assert (('POST',), '/api/files/upload') in paths_methods, 'POST /upload missing'; assert (('GET',), '/api/files') in paths_methods, 'GET / missing'; assert (('DELETE',), '/api/files/{file_id}') in paths_methods, 'DELETE /{id} missing'; assert (('PATCH',), '/api/files/{file_id}') in paths_methods, 'PATCH /{id} missing'; print('all 4 file-router routes mounted')"</automated>
  </verify>
  <acceptance_criteria>
    - File `backend/app/routers/files.py` parses as valid Python.
    - `grep -c '@router.patch("/{file_id}"' backend/app/routers/files.py` returns 1.
    - `grep -c "^async def patch_file(" backend/app/routers/files.py` returns 1.
    - `grep -c "body: FilePatch" backend/app/routers/files.py` returns 1.
    - File contains the literal string `No fields to update` (empty update_data 400).
    - File contains `normalize_path(body.folder_path)` (Pitfall 4 belt for the new endpoint).
    - File contains `Admin required for global document` (admin error message).
    - File still has the existing endpoints: `@router.post("/upload"`, `@router.get(""`, `@router.delete("/{file_id}"` — exactly one each.
    - File now has 4 endpoints total under /api/files (POST, GET, PATCH, DELETE).
    - Runtime route inspection confirms PATCH /api/files/{file_id} is mounted on the router.
    - Module imports cleanly via venv Python.
    - The patch_file body does NOT contain a `scope` field assignment (`update_data["scope"] = ...` is absent).
    - The `_upload_to_storage`, `_ingestion_semaphore`, `_throttled_ingest`, list_files, delete_file functions are UNCHANGED from Task 1's state.
    - File length is approximately 200 lines (was 165 + ~35 for the new endpoint).
  </acceptance_criteria>
  <done>
    `backend/app/routers/files.py` now has a PATCH /{file_id} endpoint that supports rename (file_name) and move (folder_path) with admin gate for global-scope documents. The endpoint enforces the FilePatch contract (no scope field), normalizes folder_path (Pitfall 4 belt), rejects empty PATCHes with 400, and returns the updated DocumentResponse. Plan 06 can now exercise the FOLDER-07 PATCH path.
  </done>
</task>

</tasks>

<verification>
This plan delivers FOLDER-07 (extended files router: upload accepts folder_path + scope query args; new PATCH endpoint for rename + folder move). Maps to .planning/phases/03-folder-service-routers-dedup-extension/03-VALIDATION.md row "3-04-* | 04 (files router extensions) | 3 | FOLDER-07 | T-scope-mutation, T-Pitfall-10".

Verification steps:
- Task 1: AST + grep gates confirm the upload_file signature has folder_path + scope Query args, the body normalizes folder_path, applies the admin gate for global, computes effective_user_id + storage_user_segment, passes kwargs to determine_action, and includes scope+folder_path in the documents.insert payload.
- Task 2: AST + grep gates confirm the PATCH /{file_id} endpoint exists with the FilePatch body, admin-gate-after-lookup pattern, normalize_path for folder_path, empty-update 400, and the response is a re-selected document row.
- Runtime route inspection confirms 4 endpoints under /api/files (POST upload, GET list, PATCH rename/move, DELETE delete).
- Plan 06's test_folders.py exercises:
  - FOLDER-07 happy path: POST /api/files/upload?folder_path=/a&scope=user with a small fixture file → 200; the documents row has folder_path='/a' and scope='user'.
  - FOLDER-07 admin gate: POST /api/files/upload?scope=global as non-admin → 403; as admin → 200 with effective_user_id=None.
  - FOLDER-07 PATCH rename: PATCH /api/files/{id} {file_name: 'new.txt'} → updated row has new file_name.
  - FOLDER-07 PATCH move: PATCH /api/files/{id} {folder_path: '/b'} → updated row has folder_path='/b'.
  - FOLDER-07 PATCH empty: PATCH /api/files/{id} {} → 400.
  - FOLDER-07 scope smuggling: PATCH /api/files/{id} {scope: 'global'} → 200 (Pydantic ignores) AND row's scope is unchanged.
  - Pitfall 10 concurrent-upload: 10 parallel POST /api/files/upload?folder_path=/test-race-{uuid} → exactly 0 folders rows at that path (Strategy B locked).
  - Pitfall F: scope=global upload produces Storage path 'documents/global/{id}{ext}' (not 'documents/None/...') — verified via service-role download attempt.
</verification>

<success_criteria>
- POST /api/files/upload accepts folder_path + scope Query args (FOLDER-07).
- Admin gate enforces scope='global' uploads (403 for non-admin).
- effective_user_id is None for scope='global' (Migration 012 coupling CHECK).
- storage_user_segment is 'global' for scope='global' (Pitfall F mitigation).
- determine_action receives scope + folder_path kwargs (FOLDER-05 acceptance via the upload path).
- documents.insert includes scope and folder_path columns.
- PATCH /api/files/{file_id} supports rename (file_name) and move (folder_path) (FOLDER-07).
- PATCH applies admin gate for global-scope documents.
- PATCH rejects scope smuggling (FilePatch model omits scope; update_data dict explicitly built).
- PATCH rejects empty bodies with 400.
- The 4 file-router endpoints (POST /upload, GET, PATCH /{id}, DELETE /{id}) are mounted on the FastAPI app.
- Plan 06 unblocked to test FOLDER-07 + concurrent-upload-no-orphan + scope smuggling defense end-to-end.
</success_criteria>

<output>
After completion, create `.planning/phases/03-folder-service-routers-dedup-extension/03-05-SUMMARY.md` recording: file modified (files.py), the upload_file signature change (folder_path + scope Query args), the inline admin gate, the storage_user_segment Pitfall F mitigation, the determine_action kwargs upgrade, the PATCH /{file_id} endpoint, and a one-line confirmation that the four /api/files routes are mounted (POST /upload, GET, PATCH /{id}, DELETE /{id}).
</output>
