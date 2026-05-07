---
phase: 03-folder-service-routers-dedup-extension
verified: 2026-05-07T11:30:00Z
status: human_needed
score: 56/57 must-haves verified (1 gated on operator backend restart)
overrides_applied: 0
human_verification:
  - test: "Restart uvicorn on localhost:8001 (kill stale process, start fresh from current HEAD), then run `cd backend && venv/Scripts/python scripts/test_folders.py`"
    expected: "Output ends with `Results: N passed, 0 failed` where N >= 35 (36 if DATABASE_URL is exported; 35 if the FOLDER-03 transactional-rollback test SKIPs)"
    why_human: "The running localhost:8001 backend is stale (started before Plans 04+05; OpenAPI spec at probe time shows 10 routes, no folders router, no PATCH /api/files/{file_id}). Source code at HEAD is correct (23 routes once mounted). The verifier confirmed the live OpenAPI is stale via `curl http://localhost:8001/openapi.json`. The canary in test_folders.py (`_verify_phase3_setup`) correctly fired [FATAL] in this state — the suite's contract is to bail when prerequisites are missing. Operator action is required because the verifier cannot autonomously kill a long-running uvicorn process."
  - test: "(Optional, recommended) Set $env:DATABASE_URL to the Supabase Direct connection string before running the suite, so the FOLDER-03 transactional-rollback test runs (it gracefully SKIPs without it)."
    expected: "FOLDER-03 transactional rollback section runs an in-test PL/pgSQL function (test_rename_folder_prefix_fails_midway) and asserts post-failure documents.folder_path is UNCHANGED."
    why_human: "DATABASE_URL is not exported in the agent environment. Without it, the rollback test SKIPs but the rest of the suite still validates the atomic rename happy path."
  - test: "(Optional) Cross-suite regression sweep: `cd backend && venv/Scripts/python scripts/test_all.py` after the focused suite is green."
    expected: "All 15 suites either pass or fail with previously-known Phase-1 carry-forward FAILs only (admin-assumption regression in Threads/Messages/Hybrid/Tools/Sub-Agents per STATE.md L118)."
    why_human: "CLAUDE.md rule: 'Do NOT run the full test suite automatically.' Operator decides when to run the full sweep."
---

# Phase 3: Folder Service + Routers + Dedup Extension — Verification Report

**Phase Goal:** Users can create, rename, delete, and move folders/documents through HTTP endpoints with admin-gated writes for global scope, transactional folder rename, and concurrent-upload safety.

**Verified:** 2026-05-07T11:30:00Z
**Status:** human_needed (code complete; one final runtime validation gated on operator backend restart)
**Re-verification:** No — initial verification

## Summary

All 6 plans landed cleanly across 16 commits (ca017e7..64e3e55). Every must_have truth from every plan was verified against the actual source on HEAD. Every contract artifact (Migration 019, schemas.py, folder_service.py, record_manager.py, folders.py, main.py registration, files.py extensions, test_folders.py, test_all.py) exists at or above the plan-specified line floors and contains every required substring marker.

The single outstanding item is a **runtime validation, not a code gap**: the focused integration suite `test_folders.py` (591 lines, 36 h.test() assertions) was authored and committed, but cannot be run green against the currently-running localhost:8001 because that uvicorn process predates Plans 04 + 05 and has stale module imports. The verifier confirmed this empirically via `curl http://localhost:8001/openapi.json` — the live process exposes 10 routes (no `/api/folders/*`, no `PATCH /api/files/{file_id}`); HEAD source mounts 23. The suite's own canary precheck `_verify_phase3_setup` correctly bails [FATAL] in this state — that is its contract. Operator must restart uvicorn from a fresh shell, then re-run the focused suite.

## Goal Achievement

### ROADMAP Success Criteria

| # | Truth (ROADMAP SC) | Status | Evidence |
|---|--------------------|--------|----------|
| SC1 | POST/PATCH/DELETE/GET /api/folders work end-to-end with admin gate enforced for `scope='global'`; non-admin global-write returns 403 | VERIFIED | folders.py:36-159 — 4 endpoints registered under `/api/folders` prefix; `_require_admin()` helper at L21-33 mirrors auth.py admin check; gate fires when `body.scope=='global'` (POST L62) or `existing.scope=='global'` (PATCH L89, DELETE L134); test_folders.py FOLDER-06 admin-gate section asserts both 403/200 outcomes. main.py:23 includes `folders.router` between files and settings as required. |
| SC2 | Folder rename atomically updates documents.folder_path AND folders.path via Supabase RPC; mid-rename crash leaves no partial state | VERIFIED | Migration 019 §1 (rename_folder_prefix at lines 37-83) wraps both UPDATEs in single PL/pgSQL block (implicitly transactional). Defense-in-depth canonical-path validation; raises check_violation on root rename. folder_service.rename_folder L253-288 calls the RPC by name. test_folders.py FOLDER-03 transactional-rollback section creates a deliberate-fail PL/pgSQL fixture and asserts pre-call state preserved. |
| SC3 | DELETE /api/folders/{id} on non-empty returns structured `{error:'FOLDER_NOT_EMPTY', document_count, subfolder_count}`; no documents deleted on rejected calls | VERIFIED | Migration 019 §2 (delete_folder_if_empty at lines 93-141) uses SELECT ... FOR UPDATE row lock + count-check + DELETE in single PL/pgSQL block (TOCTOU eliminated). folders.py:149-154 maps `deleted=False` to `JSONResponse(status_code=409, content={error:'FOLDER_NOT_EMPTY', document_count, subfolder_count})`. test_folders.py FOLDER-04 section asserts 409 body shape AND no-orphan invariant. |
| SC4 | record_manager dedup key is `(scope, user_id, folder_path, file_name, hash)`; same file in two different folders → two rows; same file in same folder → deduped | VERIFIED | record_manager.py:27-93 determine_action() gains `scope: str = "user"` (L32), `folder_path: str = "/"` (L33); SELECT extended at L62-71 with `.eq("scope", scope).eq("folder_path", folder_path).eq("file_name", file_name)` and scope-branched user_id filter (`.eq("user_id", user_id)` for user, `.is_("user_id", "null")` for global — Pitfall A mitigation). The (scope, user_id, folder_path, file_name) column order matches the Migration 012 unique index. test_folders.py FOLDER-05 section asserts upload-same-file-to-different-folders creates two docs. |
| SC5 | POST /api/files/upload accepts folder_path + scope query args; PATCH /api/files/{id} supports rename/folder-move; concurrent-upload-no-orphan test (10 parallel uploads to brand-new path) produces 0 or 1 folders rows | VERIFIED | files.py:65-69 adds `folder_path: str = Query("/")` and `scope: str = Query("user", regex="^(user|global)$")` query args; normalize_path at L77 (Pitfall 4 belt); inline admin gate L82-85; effective_user_id+storage_user_segment computed L86-94 (Pitfall F). PATCH /api/files/{file_id} at L198-238 with FilePatch body, lookup-then-gate-then-act, normalize_path on folder_path, empty-update 400, three-layer scope-immutability. test_folders.py Pitfall 10 section uses concurrent.futures.ThreadPoolExecutor(max_workers=10) at L559 and asserts 0 folders rows at the brand-new race path. |

**Score:** 5/5 ROADMAP success criteria verified at the source-code level (SC2/SC3/SC4/SC5 also have empirical assertions in test_folders.py awaiting the green run).

### Plan-by-Plan Must-Have Verification

#### Plan 01 (Migration 019 + Pydantic schemas)

| # | Must-Have | Status | Evidence |
|---|-----------|--------|----------|
| 1.01 | Migration 019 file exists with 3 CREATE OR REPLACE FUNCTION statements (rename_folder_prefix, delete_folder_if_empty, create_folder_if_not_exists) | VERIFIED | 019_folder_rename_and_delete_rpcs.sql L37, L93, L153 — exactly 3 CREATE OR REPLACE FUNCTION blocks |
| 1.02 | Migration 019 applied to live Supabase Postgres | VERIFIED (via documented MCP path) | 03-01-SUMMARY.md L67 + L128 attests apply via `mcp__supabase__apply_migration` (DATABASE_URL fallback used in same situation as Phase 1 / Plan 07); verified live via `mcp__supabase__execute_sql` against pg_proc per phase_context. The verifier cannot independently re-probe pg_proc without MCP tools but the documented apply path is the canonical fallback. |
| 1.03 | All three RPCs exist in pg_proc with SECURITY INVOKER | VERIFIED (via documented MCP probe) | Per phase_context + 03-01-SUMMARY.md L128: `mcp__supabase__execute_sql` returned 3 rows with prosecdef=false (SECURITY INVOKER) and proacl containing `authenticated=X/postgres` |
| 1.04 | rename_folder_prefix raises check_violation on non-canonical prefixes AND on rename of '/' | VERIFIED | Migration 019 L52-63 — three IF blocks raising EXCEPTION USING ERRCODE='check_violation' |
| 1.05 | delete_folder_if_empty uses SELECT ... FOR UPDATE row lock | VERIFIED | Migration 019 L108-111 — `SELECT ... FROM folders WHERE id=p_folder_id FOR UPDATE` |
| 1.06 | create_folder_if_not_exists uses INSERT ... ON CONFLICT (scope, COALESCE(user_id,'00..0'::uuid), path) DO NOTHING | VERIFIED | Migration 019 L182 — exact mirror of Migration 013 unique expression index |
| 1.07 | All three RPCs SECURITY INVOKER + GRANT EXECUTE TO authenticated | VERIFIED | Migration 019 L45/L98/L160 (SECURITY INVOKER) + L85/L143/L200 (GRANT EXECUTE TO authenticated) |
| 1.08 | schemas.py contains FolderResponse, FolderCreate, FolderPatch, FilePatch | VERIFIED | schemas.py L49 (FolderResponse), L57 (FolderCreate), L62 (FolderPatch), L66 (FilePatch) |
| 1.09 | DocumentResponse.user_id changed to Optional[str] = None | VERIFIED | schemas.py L34 — `user_id: Optional[str] = None` |
| 1.10 | DocumentResponse gains folder_path/scope with safe defaults | VERIFIED | schemas.py L42 (`folder_path: str = "/"`), L43 (`scope: str = "user"`) |
| 1.11 | FilePatch deliberately OMITS scope field | VERIFIED | schemas.py L66-69 — only file_name and folder_path; comment at L67 documents Migration 015 trigger as bedrock |

#### Plan 02 (folder_service.py extensions)

| # | Must-Have | Status | Evidence |
|---|-----------|--------|----------|
| 2.01 | folder_service.py exports list_folder, create_folder, move_document, rename_folder, delete_folder | VERIFIED | folder_service.py L80, L184, L227, L253, L291 |
| 2.02 | Every new function takes positional-untyped supabase_client param | VERIFIED | Each function signature ends with `supabase_client,` (L84, L188, L231, L258, L293) |
| 2.03 | Every path-accepting function runs normalize_path() AS FIRST STATEMENT | VERIFIED | list_folder L105, create_folder L199, move_document L239, rename_folder L270-271 (both old and new paths normalized first) |
| 2.04 | rename_folder/delete_folder/create_folder call Migration 019 RPCs by exact name | VERIFIED | folder_service.py L201 (`rpc("create_folder_if_not_exists", ...)`), L275 (`rpc("rename_folder_prefix", ...)`), L309 (`rpc("delete_folder_if_empty", ...)`) |
| 2.05 | rename_folder raises ValueError BEFORE invoking RPC if old or new normalize to '/' | VERIFIED | folder_service.py L272-273 — `if old_norm == "/" or new_norm == "/": raise ValueError("cannot rename root path")` |
| 2.06 | list_folder returns {documents, subfolders} structure | VERIFIED | folder_service.py L177-181 — explicit folders rows + inferred subfolders union; scope handling at L109-116 |
| 2.07 | move_document does NOT accept target scope arg | VERIFIED | folder_service.py L227-232 — signature is `(document_id, new_folder_path, user_id, supabase_client)` only; no scope param |
| 2.08 | Existing normalize_path() function (L28-67) UNCHANGED | VERIFIED | folder_service.py L28-67 preserved; Phase 1 contract intact |
| 2.09 | New functions live AFTER normalize_path and BEFORE __main__ block | VERIFIED | folder_service.py: normalize_path ends L67; new section divider L70-77; new funcs L80-325; `if __name__=="__main__":` at L330 |
| 2.10 | Module imports cleanly | VERIFIED | The integration suite test_folders.py imports `list_folder, create_folder, move_document, rename_folder, delete_folder` at module top — Python parses the file (visual confirmation) |

#### Plan 03 (record_manager.determine_action extension)

| # | Must-Have | Status | Evidence |
|---|-----------|--------|----------|
| 3.01 | determine_action() gains scope: str = 'user' and folder_path: str = '/' kwargs | VERIFIED | record_manager.py L32-33 |
| 3.02 | SELECT extended with .eq('scope', scope) AND .eq('folder_path', folder_path) | VERIFIED | record_manager.py L62-63 |
| 3.03 | scope='user' uses .eq('user_id', user_id); scope='global' uses .is_('user_id', 'null') (Pitfall A) | VERIFIED | record_manager.py L66-71 — explicit branching |
| 3.04 | Dedup query benefits from documents_scope_user_path_filename_unique index | VERIFIED | record_manager.py L62-67 — column filter order matches Migration 012:51-57 index column list |
| 3.05 | Existing call sites in files.py NOT changed by this plan (Plan 05 owns those) | VERIFIED | git history confirms record_manager edits in c86711a (Plan 03), files.py upload-handler edits in 6fdbdef (Plan 05) — separate commits |
| 3.06 | compute_file_hash and compute_chunk_hash UNCHANGED | VERIFIED | record_manager.py L17-24 preserved |
| 3.07 | RecordAction dataclass UNCHANGED | VERIFIED | record_manager.py L10-14 preserved |
| 3.08 | Same file at /a returns create; same file again at /a returns skip; same file at /b returns create | VERIFIED (logic) | record_manager.py L78-93 logic correctly returns create when no row matches the (scope, user_id, folder_path, file_name) tuple. test_folders.py FOLDER-05 section asserts this end-to-end (suite run gated on operator restart) |

#### Plan 04 (folders.py router + main.py registration)

| # | Must-Have | Status | Evidence |
|---|-----------|--------|----------|
| 4.01 | folders.py exists with 4 endpoints under /api/folders | VERIFIED | folders.py L36 (GET), L51 (POST), L69 (PATCH), L116 (DELETE) — all under prefix `/api/folders` (L18) |
| 4.02 | GET /api/folders supports path/scope query args + returns list_folder shape | VERIFIED | folders.py L36-48; scope regex `^(user\|global\|both)$`; calls list_folder() at L48 |
| 4.03 | POST inline admin gate fires only when body.scope=='global' | VERIFIED | folders.py L62-66 — `if body.scope == "global": _require_admin(...)` |
| 4.04 | PATCH lookup-then-admin-gate-then-rename pattern | VERIFIED | folders.py L78-99 — maybe_single lookup, 404 if missing, admin gate at L89, rename_folder() at L98 |
| 4.05 | DELETE returns JSONResponse(status_code=409, content={error:'FOLDER_NOT_EMPTY', ...}) on non-empty | VERIFIED | folders.py L149-154 |
| 4.06 | POST/PATCH normalize_path at top of handler | VERIFIED | folders.py L45 (GET), L58 (POST), L93 (PATCH new_path) |
| 4.07 | main.py imports folders and calls include_router(folders.router) AFTER files BEFORE settings | VERIFIED | main.py L8 (`from app.routers import threads, messages, files, folders, settings`); L20-24 (include_router calls in order: threads, messages, files, folders, settings) |
| 4.08 | All endpoints require Depends(get_current_user) | VERIFIED | folders.py L41, L54, L73, L119 — all four endpoints depend on get_current_user |
| 4.09 | Concurrent POST same path produces exactly ONE row | VERIFIED (via RPC) | Migration 019 create_folder_if_not_exists ON CONFLICT DO NOTHING; folder_service.create_folder returns existing row with action='exists' for the loser. Empirical assertion deferred to Pitfall 10 section of test_folders.py |
| 4.10 | min_lines >= 100 for folders.py | VERIFIED | folders.py = 159 lines |

#### Plan 05 (files.py upload + PATCH extensions)

| # | Must-Have | Status | Evidence |
|---|-----------|--------|----------|
| 5.01 | POST /upload accepts folder_path + scope query args | VERIFIED | files.py L65-67 |
| 5.02 | folder_path normalized at top of handler | VERIFIED | files.py L77 (`folder_path = normalize_path(folder_path)`) |
| 5.03 | scope='global' triggers inline admin gate (403 for non-admin) | VERIFIED | files.py L82-85 |
| 5.04 | scope='global' sets effective_user_id=None for documents row | VERIFIED | files.py L87 (`effective_user_id = None`); L145 (`"user_id": effective_user_id`) |
| 5.05 | scope='global' uses 'global' as Storage path segment (Pitfall F) | VERIFIED | files.py L91 (`storage_user_segment = "global"`); used at L123, L156 |
| 5.06 | determine_action called with scope=scope, folder_path=folder_path | VERIFIED | files.py L98-101 |
| 5.07 | documents.insert in create branch includes scope and folder_path | VERIFIED | files.py L144-152 — both `"scope": scope` (L146) and `"folder_path": folder_path` (L147) present |
| 5.08 | PATCH /api/files/{file_id} added with FilePatch body | VERIFIED | files.py L198-238 |
| 5.09 | PATCH lookup-then-gate-then-act pattern; 404 fast-fail; admin gate after lookup if global; reject empty update_data with 400 | VERIFIED | files.py L207-235 — maybe_single lookup, 404 if missing, admin gate at L216-219, normalize at L230, empty-update 400 at L234-235 |
| 5.10 | FilePatch model omits scope; Pydantic v2 silently drops smuggled scope | VERIFIED | schemas.py L66-69 (FilePatch has only file_name + folder_path); files.py PATCH only passes through file_name + folder_path explicitly via update_data dict (L226-232) |
| 5.11 | Existing endpoints (GET list_files, DELETE delete_file) UNCHANGED | VERIFIED | files.py L177-195 — list_files and delete_file preserved verbatim from pre-phase-3 state (verified via git diff) |
| 5.12 | _ingestion_semaphore, _throttled_ingest, _upload_to_storage UNCHANGED | VERIFIED | files.py L16-58 helpers preserved |

#### Plan 06 (test_folders.py + test_all.py registration)

| # | Must-Have | Status | Evidence |
|---|-----------|--------|----------|
| 6.01 | test_folders.py exists with run() returning (passed, failed) | VERIFIED | test_folders.py L158 has `def run():` |
| 6.02 | _verify_phase3_setup canary probes Migration 019 RPCs and GET /api/folders | VERIFIED | test_folders.py L94-128 — probes rename_folder_prefix RPC AND GET /api/folders for non-404 with [FATAL] message |
| 6.03 | FOLDER-02 section: 5 import smoke checks | VERIFIED | test_folders.py L182 section header; service surface assertions |
| 6.04 | FOLDER-06 section: POST/GET/PATCH/DELETE + admin 403 + admin 200 | VERIFIED | test_folders.py L193 (router CRUD), L230 (admin gate) |
| 6.05 | FOLDER-03 section: atomic rename + transactional rollback (deliberate-fail RPC variant) | VERIFIED | test_folders.py L260 (atomic rename), L304 (transactional rollback fixture); deliberate-fail PL/pgSQL fixture at L325; DROP FUNCTION IF EXISTS in finally at L354 |
| 6.06 | FOLDER-04 section: 409 with structured body + no-orphan | VERIFIED | test_folders.py L362 section; FOLDER_NOT_EMPTY assertions L387-388 |
| 6.07 | FOLDER-05 section: dedup key (create/skip/create) | VERIFIED | test_folders.py L402 section |
| 6.08 | FOLDER-07 section: upload + PATCH rename/move/empty/smuggling | VERIFIED | test_folders.py L445 section |
| 6.09 | Pitfall 10 section: 10 parallel uploads via ThreadPoolExecutor; assert 0 folders rows | VERIFIED | test_folders.py L538 section; race_path L539; ThreadPoolExecutor(max_workers=10) at L559 |
| 6.10 | Cross-user isolation section | VERIFIED | test_folders.py L508 section |
| 6.11 | _tracked_documents/_tracked_folders/_cleanup with ZERO bulk DELETE FROM/TRUNCATE | VERIFIED | test_folders.py L59-60 (tracking lists), L133-154 (per-id .delete().eq() in finally) |
| 6.12 | test_all.py registers ('Folders', test_folders) AFTER Files BEFORE Backfill (15 of 15) | VERIFIED | test_all.py L17 (import), L34 (tuple); SUITES from L28-43 has 15 entries; Folders is 6th (Files at L33, Folders at L34, Backfill at L35) |
| 6.13 | At least 25 distinct h.test() assertions | VERIFIED | grep counted 36 h.test() invocations |
| 6.14 | min_lines >= 350 for test_folders.py | VERIFIED | test_folders.py = 591 lines |
| 6.15 | Focused suite green run | NOT YET RUN — gated on operator backend restart | The running uvicorn predates Plans 04+05 (10 routes vs HEAD's 23). Source is correct. Suite canary correctly bails [FATAL] in this state. **This is the single human_verification item.** |

### Required Artifacts

| Artifact | Status | Details |
|----------|--------|---------|
| `backend/migrations/019_folder_rename_and_delete_rpcs.sql` | VERIFIED | 200 lines; 3 CREATE OR REPLACE FUNCTION; 3 GRANT EXECUTE; FOR UPDATE; ON CONFLICT (scope, COALESCE(user_id, '00..0'::uuid), path); GET DIAGNOSTICS; check_violation; no_data_found; SECURITY INVOKER (3x); no SECURITY DEFINER; no CONCURRENTLY |
| `backend/app/models/schemas.py` | VERIFIED | 13 classes total (8 pre-existing preserved + FolderResponse + FolderCreate + FolderPatch + FilePatch + DocumentResponse extensions); FilePatch has 2 fields (no scope); DocumentResponse user_id: Optional[str]=None, folder_path: str="/", scope: str="user" |
| `backend/app/services/folder_service.py` | VERIFIED | 354 lines; 5 new public funcs (list_folder, create_folder, move_document, rename_folder, delete_folder) AFTER normalize_path; all RPC calls use exact names; normalize_path is first stmt on all path-accepting funcs |
| `backend/app/services/record_manager.py` | VERIFIED | scope/folder_path kwargs added; .eq('scope',...).eq('folder_path',...).eq('file_name',...) chain; .is_('user_id','null') for global; defaults preserve back-compat |
| `backend/app/routers/folders.py` | VERIFIED | 159 lines; 4 endpoints; _require_admin helper at L21; structured 409 at L150; admin gate body/row-conditional |
| `backend/app/main.py` | VERIFIED | folders imported (L8); include_router(folders.router) between files and settings (L23) |
| `backend/app/routers/files.py` | VERIFIED | 238 lines (was 96 pre-phase); folder_path/scope query args; normalize_path; admin gate; storage_user_segment; determine_action(scope=, folder_path=); PATCH /{file_id} endpoint with FilePatch body and three-layer scope-immutability defense |
| `backend/scripts/test_folders.py` | VERIFIED (source) | 591 lines; 36 h.test() assertions; 10 sections; canary precheck; ThreadPoolExecutor; deliberate-fail RPC fixture; tracked-IDs cleanup |
| `backend/scripts/test_all.py` | VERIFIED | 15 SUITES; ('Folders', test_folders) between Files and Backfill |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| folder_service.rename_folder/delete_folder/create_folder | Migration 019 RPCs | `supabase_client.rpc("<name>", {...}).execute()` | WIRED | folder_service.py L201, L275, L309 — exact RPC names match Migration 019 |
| folders.py / files.py | folder_service.normalize_path | `from app.services.folder_service import normalize_path` | WIRED | folders.py L9; files.py L8 |
| folders.py | schemas (FolderResponse/FolderCreate/FolderPatch) | `from app.models.schemas import ...` | WIRED | folders.py L7 |
| files.py PATCH | schemas.FilePatch | `from app.models.schemas import DocumentResponse, FilePatch` | WIRED | files.py L7 |
| files.py upload_file | record_manager.determine_action | `determine_action(file_hash, file_name, user_id, supabase, scope=scope, folder_path=folder_path)` | WIRED | files.py L98-101 |
| main.py | folders.router | `app.include_router(folders.router)` | WIRED | main.py L8 (import), L23 (include_router) |
| test_folders.py canary | rename_folder_prefix RPC + GET /api/folders | RPC probe + endpoint probe | WIRED | test_folders.py L102 (RPC probe), GET /api/folders probe in same _verify_phase3_setup |
| test_all.py SUITES | test_folders module | `("Folders", test_folders)` | WIRED | test_all.py L17 + L34 |
| Inline admin gate in folders.py + files.py | auth.py:46-51 (get_user_profile + is_admin) | `profile = get_user_profile(user_id); if not profile or not profile.get("is_admin"): raise HTTPException(403, ...)` | WIRED | folders.py L21-33 (`_require_admin` helper); files.py L82-85 (inline) and L216-219 (inline) |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|---------------------|--------|
| folder_service.list_folder | documents, subfolders | supabase_client.table("documents").select("*").eq("folder_path", norm) chain (L108-116); folders table queries L129-141; inferred subfolders L150-170 | YES — three real DB queries with correct filtering | FLOWING |
| folder_service.create_folder | row (id, created) | RPC `create_folder_if_not_exists` (L201); fallback hydration via folders SELECT (L214) | YES — RPC + DB SELECT | FLOWING |
| folder_service.rename_folder | row (documents_updated, folders_updated) | RPC `rename_folder_prefix` (L275-280) | YES — atomic cross-table UPDATE returns row counts | FLOWING |
| folder_service.delete_folder | row (deleted, document_count, subfolder_count) | RPC `delete_folder_if_empty` (L309-311) | YES — RPC returns structured response | FLOWING |
| folders.py POST/PATCH/DELETE | folder | sb.table("folders").select("*")...maybe_single() → folder_service functions → supabase RPCs | YES — real DB lookups feed real RPCs | FLOWING |
| files.py upload_file | doc | supabase.table("documents").insert({...scope, folder_path, user_id...}).execute().data[0] (L144-152) | YES — real DB INSERT | FLOWING |
| files.py patch_file | updated row | sb.table("documents").update(update_data).eq("id", file_id).execute() then re-SELECT (L237-238) | YES — real DB UPDATE + SELECT | FLOWING |
| record_manager.determine_action | existing | supabase_client.table("documents").select(...).eq("scope", scope).eq("folder_path", folder_path).eq("file_name", file_name).maybe_single() (L59-72) | YES — real DB SELECT with extended dedup key | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Backend serves OpenAPI | `curl -s http://localhost:8001/openapi.json` | 10 routes (stale process — predates Plans 04+05) | FAIL (operator-pre-req gap; HEAD source is correct) |
| Folders router registered on disk | grep `include_router\(folders\.router\)` main.py | 1 match at L23 | PASS |
| Folders import on disk | grep `from app.routers import` main.py | matches `threads, messages, files, folders, settings` at L8 | PASS |
| 5 service funcs in folder_service.py | grep `^def\s+(list_folder\|create_folder\|move_document\|rename_folder\|delete_folder)\(` | 5 matches at L80, L184, L227, L253, L291 | PASS |
| 36 h.test() assertions in test_folders.py | grep -c `h\.test\(` | 36 | PASS |
| 15 SUITES in test_all.py | grep `"Folders"` test_all.py | 1 match at L34 | PASS |
| Migration 019 has 3 CREATE OR REPLACE FUNCTION | grep -c | 3 | PASS |
| Migration 019 has SECURITY INVOKER (3x) | grep | found at L45, L98, L160 | PASS |
| Migration 019 has NO SECURITY DEFINER | grep -v | absent | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| FOLDER-02 | Plan 02, 06 | folder_service.py provides list_folder, create_folder, move_document, rename_folder, delete_folder | SATISFIED | folder_service.py L80, L184, L227, L253, L291 — 5 functions exist; called from folders.py and exercised by test_folders.py FOLDER-02 section. REQUIREMENTS.md table marked Complete (line 159). |
| FOLDER-03 | Plan 01, 06 | Folder rename is transactional prefix update on documents.folder_path AND folders.path via Supabase RPC | SATISFIED | Migration 019 §1 rename_folder_prefix wraps both UPDATEs in single PL/pgSQL block (lines 65-79). REQUIREMENTS.md marked Complete (line 160). |
| FOLDER-04 | Plan 01, 06 | Folder delete rejects non-empty (returns structured `{error:'FOLDER_NOT_EMPTY', ...}`) | SATISFIED | Migration 019 §2 delete_folder_if_empty + folders.py L150-154 JSONResponse(409, ...). REQUIREMENTS.md marked Complete (line 161). |
| FOLDER-05 | Plan 03, 06 | record_manager dedup key extended to (scope, user_id, folder_path, file_name, hash) | SATISFIED | record_manager.py L62-71 — extended SELECT with .eq("scope", ...).eq("folder_path", ...).is_("user_id", "null") branch. REQUIREMENTS.md marked Complete (line 162). |
| FOLDER-06 | Plan 04, 06 | folders router with GET/POST/PATCH/DELETE + admin gate for global writes | SATISFIED | folders.py 4 endpoints + _require_admin helper. main.py:23 registers router. REQUIREMENTS.md marked Complete (line 163). |
| FOLDER-07 | Plan 05, 06 | Extended files router: POST /upload?folder_path=...&scope=...; PATCH /{id} for rename/move | SATISFIED | files.py upload_file with new query args + PATCH /{file_id} endpoint. REQUIREMENTS.md marked Complete (line 164). |
| TEST-01 | Plan 06 | test_folders.py — folder CRUD, transactional rename, non-empty-delete rejection, concurrent-upload-no-orphan | NEEDS HUMAN | Source authored (591 lines, 36 h.test() assertions); 10 sections covering all required scenarios. REQUIREMENTS.md table still says Pending (line 195) — appropriate because the green-run gate is not yet passed. Operator must restart backend then run the suite. |

**Coverage:** 6 of 7 Phase 3 requirements SATISFIED at the source level. TEST-01 is code-complete but its acceptance is the suite-green-run, which is gated on operator backend restart.

**No orphaned requirements.** REQUIREMENTS.md line 209 lists Phase 3 IDs as `FOLDER-02..07, TEST-01` — exactly matching the union of all 6 plans' frontmatter.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| (none) | - | - | - | No TODO/FIXME/PLACEHOLDER markers in any Phase 3 file. No empty handlers. No `return null`/`return []` stubs in production code (only in test mock data). No `console.log` placeholders (Python codebase). |

Anti-pattern scan over Phase 3 modified files (folder_service.py, record_manager.py, folders.py, files.py, main.py, schemas.py, 019_folder_rename_and_delete_rpcs.sql, test_folders.py, test_all.py) found:
- 0 TODO/FIXME/XXX/HACK/PLACEHOLDER comments
- 0 "coming soon" / "not yet implemented" strings
- 0 empty `return null`/`return {}`/`return []` non-test paths
- 0 hardcoded empty data flowing to the API surface
- 0 prop placeholders (no React UI in this phase)

### Human Verification Required

**1. Restart backend and run focused suite**

- **Test:** Stop the running uvicorn on localhost:8001 (it is stale — predates Plans 04+05), restart from a fresh shell at HEAD, then run the focused suite.
  ```
  # Stop the running backend (Ctrl+C in its terminal, or close it)
  cd backend
  venv\Scripts\python -m uvicorn app.main:app --reload --port 8001
  ```
  Then in another shell:
  ```
  cd backend
  venv\Scripts\python scripts\test_folders.py
  ```
- **Expected:** Output ends with `Results: N passed, 0 failed` where N >= 35 (36 if `$env:DATABASE_URL` is exported; 35 if the FOLDER-03 transactional-rollback test SKIPs).
- **Why human:** The verifier confirmed the live OpenAPI is stale (10 routes vs HEAD's 23). The verifier cannot autonomously kill a long-running uvicorn process. The canary in test_folders.py correctly bailed [FATAL] in this state — that is its contract. Source is verified correct.

**2. (Optional) Set DATABASE_URL before running the suite**

- **Test:** Set `$env:DATABASE_URL` to the Supabase Direct connection string before running the suite.
- **Expected:** FOLDER-03 transactional-rollback test runs (creates `test_rename_folder_prefix_fails_midway` PL/pgSQL function via psycopg2, asserts post-failure documents.folder_path is UNCHANGED, DROPs the test function in finally).
- **Why human:** DATABASE_URL is not in the verifier environment. Without it, the rollback test SKIPs gracefully but the rest of the suite still validates atomic-rename happy path.

**3. (Optional) Cross-suite regression sweep**

- **Test:** `cd backend && venv/Scripts/python scripts/test_all.py` after the focused suite is green.
- **Expected:** All 15 suites either PASS or fail with previously-known Phase-1 carry-forward FAILs only (admin-assumption regression in Threads/Messages/Hybrid/Tools/Sub-Agents per STATE.md L118).
- **Why human:** CLAUDE.md rule explicitly forbids the verifier from running the full test suite automatically.

### Gaps Summary

**No code gaps.** Every must_have truth from every plan was verified against the source on HEAD. Migration 019 was applied via the canonical fallback path (Supabase MCP `apply_migration`, same as Phase 1 / Plan 07 used) and verified live via MCP `execute_sql`. All artifacts exist at or above the plan-specified line floors. All key links are wired. All data flows through real DB queries.

The single outstanding item is a runtime validation gated on operator action — the running uvicorn process is stale and must be restarted before the focused suite can run green. This is documented in 03-06-SUMMARY.md as the expected outcome of the canary precheck pattern (mirrors Phase 2 / Plan 04's documents-bucket-missing setup gap, which was resolved the same way).

---

*Verified: 2026-05-07T11:30:00Z*
*Verifier: Claude (gsd-verifier)*
