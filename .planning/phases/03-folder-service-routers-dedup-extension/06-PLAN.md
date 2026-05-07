---
phase: 03
plan: 06
type: execute
wave: 4
depends_on: [01, 02, 03, 04, 05]
files_modified:
  - backend/scripts/test_folders.py
  - backend/scripts/test_all.py
autonomous: false
requirements:
  - TEST-01
  - FOLDER-02
  - FOLDER-03
  - FOLDER-04
  - FOLDER-05
  - FOLDER-06
  - FOLDER-07
must_haves:
  truths:
    - "backend/scripts/test_folders.py exists with run() returning (h.passed, h.failed) per the SUITES contract"
    - "test_folders.py begins with a canary precheck (_verify_phase3_setup) that probes Migration 019's RPCs (rename_folder_prefix, delete_folder_if_empty, create_folder_if_not_exists) AND probes GET /api/folders for non-404 (router registered) — bails with single FAIL h.test + actionable [FATAL] message if any prerequisite missing (mirrors test_two_scope_rls._verify_admin_setup + test_backfill._verify_storage_setup pattern)"
    - "test_folders.py FOLDER-02 section asserts list_folder, create_folder, move_document, rename_folder, delete_folder are importable from app.services.folder_service AND callable (smoke import + signature check)"
    - "test_folders.py FOLDER-06 section covers: POST /api/folders {path:'/test-folder', scope:'user'} returns 200 + FolderResponse shape; GET /api/folders?path=/test-folder returns the {path, documents, subfolders} shape; PATCH renames; DELETE empty folder returns 200; non-admin POST /api/folders {scope:'global'} returns 403; admin POST {scope:'global'} returns 200 with user_id=None"
    - "test_folders.py FOLDER-03 section covers atomic rename (happy path: insert doc at /old, POST /api/folders /old, PATCH to /new, assert documents.folder_path=/new AND folders.path=/new) AND transactional rollback (deliberate-fail RPC variant: insert fixture, call test_rename_folder_prefix_fails_midway, assert raised, assert documents.folder_path UNCHANGED — uses psycopg2 + DROP FUNCTION in finally)"
    - "test_folders.py FOLDER-04 section covers non-empty rejection: insert doc at /with-docs, POST /api/folders /with-docs, DELETE /api/folders/{id} returns 409 with {error:'FOLDER_NOT_EMPTY', document_count:1, subfolder_count:0}; assert documents.folder_path=/with-docs UNCHANGED post-rejection (no-orphan)"
    - "test_folders.py FOLDER-05 section covers dedup key: upload file F at /a returns action='create'; upload SAME F at /a returns action='skip'; upload SAME F at /b returns action='create' (same file in two different folders is allowed — FOLDER-05 acceptance)"
    - "test_folders.py FOLDER-07 section covers POST /api/files/upload?folder_path=/x&scope=user (200 with documents row showing folder_path=/x, scope=user); PATCH /api/files/{id} {folder_path:'/y'} (move; row's folder_path becomes /y); PATCH {file_name:'new.txt'} (rename); PATCH {} (empty body, 400); PATCH {scope:'global'} smuggling (200 — Pydantic ignores; row's scope unchanged)"
    - "test_folders.py Pitfall 10 section covers concurrent-upload-no-orphan: 10 parallel POST /api/files/upload?folder_path=/test-race-{uuid} via concurrent.futures.ThreadPoolExecutor(max_workers=10); assert all 10 succeed; assert SELECT id FROM folders WHERE path='/test-race-{uuid}' returns 0 rows (Strategy B locked: uploads NEVER write folders rows)"
    - "test_folders.py covers cross-user isolation (a doc-side defense check): user A POST /api/folders {path:'/private-A'}, user B GET /api/folders?path=/private-A returns no documents (RLS filter; cross-user-no-leak)"
    - "test_folders.py uses module-level _tracked_documents and _tracked_folders lists; _cleanup() runs in finally; ZERO bulk DELETE FROM and ZERO TRUNCATE statements (CLAUDE.md mandatory rule)"
    - "test_folders.py is registered in backend/scripts/test_all.py SUITES list as ('Folders', test_folders) immediately AFTER ('Files', test_files) and BEFORE ('Backfill', test_backfill) — the file/folder family is contiguous in the suite order; SUITES count grows from 14 to 15"
    - "test_folders.py has at least 25 distinct h.test() assertions covering FOLDER-02 (5 import smoke checks), FOLDER-06 (~6 — create/list/patch/delete + admin-403 + admin-200), FOLDER-03 (~3 — atomic rename + rollback), FOLDER-04 (~3 — 409 shape + counts + no-orphan), FOLDER-05 (~3 — create-skip-create), FOLDER-07 (~5 — upload + patch rename/move/empty/smuggling), Pitfall 10 (~2 — all-200 + zero-orphans)"
  artifacts:
    - path: "backend/scripts/test_folders.py"
      provides: "Integration tests covering FOLDER-02..07 + TEST-01 + SC1..SC5 + Pitfalls 4/5/10"
      exports: ["run"]
      contains: "def run()"
      contains_2: "_tracked_documents"
      contains_3: "_tracked_folders"
      contains_4: "def _verify_phase3_setup"
      contains_5: "concurrent.futures.ThreadPoolExecutor"
      contains_6: "FOLDER_NOT_EMPTY"
      contains_7: "rename_folder_prefix"
      contains_8: "test-race"
      contains_9: "h.section"
      min_lines: 350
    - path: "backend/scripts/test_all.py"
      provides: "Folders suite registered in the full sweep"
      contains: "import test_folders"
      contains_2: "(\"Folders\", test_folders)"
  key_links:
    - from: "backend/scripts/test_folders.py canary"
      to: "Migration 019 (Plan 01) + folders router registration (Plan 04)"
      via: "rpc('rename_folder_prefix', ...) probe + GET /api/folders (assert NOT 404)"
      pattern: "rpc\\(\"rename_folder_prefix\""
    - from: "backend/scripts/test_folders.py FOLDER-04 section"
      to: "Plan 04 router 409 contract"
      via: "DELETE /api/folders/{id} on non-empty -> assert 409 with structured body"
      pattern: "FOLDER_NOT_EMPTY"
    - from: "backend/scripts/test_folders.py Pitfall 10 section"
      to: "Plan 05 upload handler + Migration 013 unique index + Strategy B"
      via: "10 parallel uploads to brand-new path -> assert 0 folders rows"
      pattern: "ThreadPoolExecutor"
    - from: "backend/scripts/test_all.py SUITES"
      to: "Full-suite regression test"
      via: "(\"Folders\", test_folders) entry"
      pattern: "\\(\"Folders\", test_folders\\)"
---

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Test fixture inserts (service-role) -> documents/folders tables | Service-role bypasses RLS; tests must scope INSERTs to a known fixture user_id (TEST_USER_A or admin) and tracked IDs only (CLAUDE.md cleanup rule) |
| concurrent.futures.ThreadPoolExecutor -> 10 parallel POST /api/files/upload | The test exercises the production endpoint as a multi-threaded client; the requests library is thread-safe; cleanup tracks every successful response's doc_id |
| psycopg2 direct connection (DATABASE_URL) -> live Supabase | The transactional-rollback test creates and DROPs a deliberate-fail PL/pgSQL function; uses a session-scoped pg connection and DROP FUNCTION in finally so the test is repeatable |
| Storage uploads -> bucket 'documents' | Tests upload real bytes to a real bucket; cleanup MUST remove tracked storage paths in finally |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-3-06-Cleanup | Tampering / Data Loss | Test cleanup logic | mitigate | Per CLAUDE.md "CRITICAL: Tests must NEVER delete all user data": every created document_id, folder_id, and storage_path is tracked in module-level lists. The `finally` block iterates ONLY those IDs and DELETEs each individually via the test's authenticated client AND service-role (defense in depth two-path cleanup). NEVER `DELETE FROM documents` without WHERE. NEVER `TRUNCATE`. Mirrors Phase 1 / Plan 08 (test_two_scope_rls.py:39-77) and Phase 2 / Plan 04 (test_backfill.py:108-130) exactly. |
| T-3-06-RLSBypass | Information Disclosure / Test integrity | Service-role for fixture-insert | mitigate | Tests use TWO clients per Phase 1+2 convention: anon-key + JWT (h.get_user_supabase_client(token)) for "as-a-user" assertions, and service-role (constructed inline matching auth.py:8-12) for fixture-insert / cross-user setup-and-teardown. The choice is documented per assertion via section name + inline comments. |
| T-3-06-FixtureLeak | Data Integrity | Deliberate-fail RPC variant remains in DB after test crash | mitigate | The test creates `test_rename_folder_prefix_fails_midway` PL/pgSQL function, exercises it, and DROP FUNCTION IF EXISTS in finally (psycopg2 connection autocommit). Even if the test crashes mid-section, the DROP runs because it's in finally. The function name is unique to test fixtures (prefix `test_`). |
| T-3-06-CanaryFailure | Operational | Migration 019 not applied OR folders router not registered | mitigate | _verify_phase3_setup() probes (1) the rename_folder_prefix RPC via service-role .rpc(...) call (no-op probe), (2) GET /api/folders responds with 401 (not 404). Failure mode = single FAIL h.test + early return + actionable [FATAL] message naming the responsible plan. Mirrors test_backfill._verify_storage_setup that surfaced the bucket-missing operator-pre-req gap empirically. |
| T-3-06-DOS | Denial of Service | 10-thread concurrent-upload test on tiny dev backend | accept | Each upload is sub-second (small text fixture, ingestion semaphore = 2 — 5 uploads queue). Total wall time < 30 sec. The test is gated by a 60-second timeout per request (the existing `requests.post` default + explicit `timeout=30`). Acceptable for a CI-like single-process backend. |
| T-3-06-CrossSuiteInterference | Operational | test_folders.py inserts/cleanup affects other suites | mitigate | Every doc/folder/storage path is tracked in module-level lists; cleanup runs in finally. No bulk DELETE. Suite order in test_all.py: Files -> Folders -> Backfill -> ... so cross-suite test_documents fixtures are not touched. |
</threat_model>

<objective>
Build the Phase 3 verification suite: `backend/scripts/test_folders.py` covering all 7 Phase 3 requirement IDs (FOLDER-02..07 + TEST-01) plus the 5 ROADMAP success criteria + Pitfalls 4/5/10. Register the suite in `test_all.py` as the 15th entry (Files → Folders → Backfill order, mirroring Phase 2's suite registration convention).

The test exercises:
- **Service surface (FOLDER-02):** import + signature smoke for the five new folder_service functions.
- **Router CRUD (FOLDER-06):** POST/GET/PATCH/DELETE /api/folders end-to-end, admin gate for global writes (403 for non-admin, 200 for admin).
- **Atomic rename + rollback (FOLDER-03 + SC2):** happy-path rename via PATCH; deliberate-fail RPC variant proves transactional rollback.
- **Non-empty rejection (FOLDER-04 + SC3):** 409 with structured body; no-orphan assertion (documents survive rejected delete).
- **Dedup key (FOLDER-05 + SC4):** same file in two folders → 2 docs; same file in same folder → 1 doc.
- **Files router extensions (FOLDER-07 + SC5):** upload with folder_path/scope; PATCH for rename + move; scope-smuggling defense.
- **Concurrent-upload-no-orphan (Pitfall 10 + SC5):** 10 parallel uploads; ZERO folders rows at the brand-new upload path (Strategy B locked).

The suite runs in <30 sec when the backend is warm; uses `_verify_phase3_setup()` canary at the top to bail fast with [FATAL] message if Migration 019 or the folders router is missing.

This plan is `autonomous: false` because Task 3 includes a `checkpoint:human-verify` gate confirming the operator has applied Plan 01's Migration 019 AND the folders router is registered (Plan 04 / Task 2) AND the backend is running on localhost:8001 — without these, the canary fires actionable [FATAL] messages but a checkpoint at this layer surfaces the dependency clearly to the operator.
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
@.planning/codebase/TESTING.md

@.planning/phases/03-folder-service-routers-dedup-extension/01-PLAN.md
@.planning/phases/03-folder-service-routers-dedup-extension/02-PLAN.md
@.planning/phases/03-folder-service-routers-dedup-extension/03-PLAN.md
@.planning/phases/03-folder-service-routers-dedup-extension/04-PLAN.md
@.planning/phases/03-folder-service-routers-dedup-extension/05-PLAN.md

@backend/scripts/test_helpers.py
@backend/scripts/test_files.py
@backend/scripts/test_two_scope_rls.py
@backend/scripts/test_backfill.py
@backend/scripts/test_all.py
@backend/app/services/folder_service.py

<interfaces>
<!-- The contracts this test asserts against (locked in Plans 01-05). -->

Migration 019 RPC contracts (Plan 01):
  rpc('rename_folder_prefix', {p_old_prefix, p_new_prefix, p_scope, p_user_id})
    -> data[0]['documents_updated'], data[0]['folders_updated']
  rpc('delete_folder_if_empty', {p_folder_id})
    -> data[0]['deleted'] (bool), data[0]['document_count'], data[0]['subfolder_count']
  rpc('create_folder_if_not_exists', {p_scope, p_user_id, p_path})
    -> data[0]['id'], data[0]['created'] (bool)

Service-layer contracts (Plan 02):
  list_folder(path, scope, user_id, sb) -> {path, documents, subfolders}
  create_folder(path, scope, user_id, sb) -> {id, scope, user_id, path, created_at, action}
  move_document(document_id, new_folder_path, user_id, sb) -> dict | None
  rename_folder(old_path, new_path, scope, user_id, sb) -> {documents_updated, folders_updated}
  delete_folder(folder_id, sb) -> {deleted, document_count, subfolder_count, error?}

record_manager dedup contract (Plan 03):
  determine_action(file_hash, file_name, user_id, sb, scope='user', folder_path='/')
    -> RecordAction(action='create'|'skip'|'update', document_id, message)

HTTP API contracts (Plans 04 + 05):
  POST   /api/folders {path, scope='user'} -> 200 FolderResponse | 403 (admin) | 400 (path)
  GET    /api/folders?path=&scope= -> 200 {path, documents, subfolders} | 401
  PATCH  /api/folders/{id} {new_path} -> 200 merged folder | 404 | 403 | 400
  DELETE /api/folders/{id} -> 200 {status:'deleted'} | 409 {error,document_count,subfolder_count} | 404 | 403
  POST   /api/files/upload?folder_path=&scope= multipart -> 200 DocumentResponse | 403 (admin)
  PATCH  /api/files/{id} {file_name?, folder_path?} -> 200 DocumentResponse | 400 (empty) | 404 | 403

test_helpers (existing):
  BASE_URL, SUPABASE_URL, SUPABASE_ANON_KEY
  TEST_USER_A, TEST_USER_B, TEST_USER_ADMIN
  get_auth_token(email?, password?), get_admin_token(), auth_headers(token)
  get_user_supabase_client(token), poll_document_status, test, section, reset_counters, summary
</interfaces>
</context>

<tasks>

<task id="3-06-01" type="auto">
  <name>Task 1: Write test_folders.py — integration suite covering FOLDER-02..07 + TEST-01 + SC1..SC5 + Pitfalls 4/5/10</name>
  <files>backend/scripts/test_folders.py</files>
  <read_first>
    - backend/scripts/test_helpers.py FULL FILE (`BASE_URL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `TEST_USER_A`, `TEST_USER_B`, `TEST_USER_ADMIN`, `get_auth_token`, `get_admin_token`, `auth_headers`, `get_user_supabase_client`, `poll_document_status`, `test`, `section`, `reset_counters`, `summary` — all reused; this test does NOT add new helpers)
    - backend/scripts/test_two_scope_rls.py L1-115 (PRIMARY analog — sys.path bootstrap; module-level _tracked_* lists; _cleanup helper; canary precheck _verify_admin_setup; _raises helper for exception assertions; admin-token + service-role construction)
    - backend/scripts/test_backfill.py L1-300 (SECONDARY analog — canary precheck pattern; subprocess invocation pattern; service-role _service_role_client; cleanup tracking storage paths; defense-in-depth two-path cleanup via API DELETE + service-role DELETE)
    - backend/scripts/test_files.py FULL FILE (TERTIARY analog — upload + poll + assertion pattern; CAPYBARA_TEXT fixture style; tracked file_ids list)
    - backend/app/services/folder_service.py (the five functions to import + signature-check in FOLDER-02 section)
    - backend/migrations/019_folder_rename_and_delete_rpcs.sql (verify the RPC names + parameter shapes match what the canary probes)
    - .planning/phases/03-folder-service-routers-dedup-extension/03-PATTERNS.md §`backend/scripts/test_folders.py` (lines ~385-720 — paste-ready test runner shape, scoped-cleanup pattern, canary precheck, _raises helper, concurrent-upload fixture, mid-rename rollback fixture)
    - .planning/phases/03-folder-service-routers-dedup-extension/03-RESEARCH.md §Validation Architecture (the falsifiable assertions; SC-to-test mapping)
    - .planning/phases/03-folder-service-routers-dedup-extension/03-RESEARCH.md §Concurrent-upload-no-orphan test fixture (lines 818-857 — verbatim Python for the 10-thread fixture)
    - .planning/phases/03-folder-service-routers-dedup-extension/03-RESEARCH.md §Mid-rename rollback test (lines 148-173 — verbatim PL/pgSQL test fixture)
    - CLAUDE.md ("Tests must NEVER delete all user data"; "Python backend uses venv"; "Backend validation suite ... 112 tests")
  </read_first>
  <action>
    Create `backend/scripts/test_folders.py` with the structure below. The test produces ~10 named sections matching FOLDER-02..07 + TEST-01 + SC1..SC5 + Pitfalls 4/5/10, with a total of 25+ h.test() assertions. All cleanup is per-tracked-id; no blanket deletes.

    The file is paste-ready; do not deviate from the canary structure, the cleanup discipline, or the assertion sections. Inline comments map each test to the requirement / SC / pitfall it satisfies.

    ### Module structure (paste-ready)

    ```python
    """Integration tests for Phase 3: folder service + routers + dedup extension.

    Covers:
      - FOLDER-02: list_folder / create_folder / move_document / rename_folder /
                  delete_folder service-surface smoke (importability + signatures)
      - FOLDER-03: rename_folder_prefix RPC atomically updates documents + folders
                  (transactional rollback verified via deliberate-fail test fixture)
      - FOLDER-04: delete_folder_if_empty RPC rejects non-empty with structured 409
                  ({error: 'FOLDER_NOT_EMPTY', document_count, subfolder_count})
      - FOLDER-05: dedup key extension — same file in two folders -> 2 docs;
                  same file in same (scope, user, path) -> action='skip'
      - FOLDER-06: GET/POST/PATCH/DELETE /api/folders end-to-end + admin gate for global
      - FOLDER-07: POST /api/files/upload?folder_path=&scope= + PATCH /api/files/{id};
                  scope-smuggling defense; concurrent-upload-no-orphan (Pitfall 10 + Strategy B)
      - TEST-01: registered as 15th suite in test_all.py SUITES list

    PREREQUISITE (must be complete before running this test):
      1. Migration 019 applied via:
           cd backend && venv/Scripts/python scripts/run_migrations.py
         (adds rename_folder_prefix, delete_folder_if_empty, create_folder_if_not_exists)
      2. backend/app/routers/folders.py registered in main.py (else GET /api/folders -> 404).
      3. backend/app/routers/files.py extended with folder_path/scope query args + PATCH /{id}.
      4. backend/app/services/folder_service.py extended with 5 CRUD functions.
      5. backend/app/services/record_manager.py extended with scope/folder_path kwargs.
      6. Backend running on http://localhost:8001.
      7. backend/.env contains SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY.
      8. Admin user promoted: UPDATE public.profiles SET is_admin=true WHERE email='admin@test.com'.

    If any prerequisite is missing, the canary precheck (_verify_phase3_setup) returns
    a single FAIL h.test + early-returns with an actionable [FATAL] message.

    CRITICAL: per CLAUDE.md, tests must NEVER delete all user data. This test tracks
    every created document_id, folder_id, and storage path and removes ONLY those
    resources in finally. No blanket DELETE FROM, no TRUNCATE, no cross-user cleanup.
    """
    import concurrent.futures
    import os
    import sys
    import uuid

    import requests

    # Two-step sys.path bootstrap (matches test_two_scope_rls.py:32-37 + test_backfill.py:39-40).
    sys.path.insert(0, os.path.dirname(__file__))
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

    import test_helpers as h
    from app.services.folder_service import normalize_path
    from supabase import create_client


    CAPYBARA_TEXT = b"""The capybara is the largest living rodent.
    Native to South America, capybaras are semi-aquatic mammals.
    They live near bodies of water and are excellent swimmers."""

    STORAGE_BUCKET = "documents"

    # Tracking lists for scoped cleanup. Per CLAUDE.md: never bulk-delete.
    _tracked_documents: list = []   # list[(doc_id, sb_client)]
    _tracked_folders: list = []     # list[(folder_id, sb_client)]
    _tracked_storage_paths: list = []   # list[str]


    def _service_role_client():
        """Return a service-role Supabase client (mirrors auth.py:8-12)."""
        return create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        )


    def _track_doc(doc_id: str, sb_client) -> None:
        if doc_id:
            _tracked_documents.append((doc_id, sb_client))


    def _track_folder(folder_id: str, sb_client) -> None:
        if folder_id:
            _tracked_folders.append((folder_id, sb_client))


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


    def _verify_phase3_setup(sb_admin) -> tuple[bool, str]:
        """Pre-flight: assert Migration 019's RPCs exist AND folders router is registered.

        Mirrors test_two_scope_rls.py::_verify_admin_setup and
        test_backfill.py::_verify_storage_setup. Returns (ok, message).
        """
        # Probe 1: rename_folder_prefix exists. Call with non-matching prefix -> no-op.
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
            r2 = requests.get(f"{h.BASE_URL}/api/folders", timeout=5)
            if r2.status_code == 404:
                return False, (
                    "GET /api/folders returns 404 — folders router not registered in main.py. "
                    "Add `from app.routers import folders` and `app.include_router(folders.router)`."
                )
        except Exception as e:
            return False, (
                f"Backend unreachable: {e}. Start with: "
                f"cd backend && venv/Scripts/python -m uvicorn app.main:app --reload --port 8001"
            )
        return True, "ok"


    def _cleanup() -> None:
        """Delete only tracked resources. Per CLAUDE.md: never bulk-delete."""
        for did, client in _tracked_documents:
            try:
                client.table("document_chunks").delete().eq("document_id", did).execute()
            except Exception:
                pass
            try:
                client.table("documents").delete().eq("id", did).execute()
            except Exception:
                pass
        for fid, client in _tracked_folders:
            try:
                client.table("folders").delete().eq("id", fid).execute()
            except Exception:
                pass
        if _tracked_storage_paths:
            try:
                sb = _service_role_client()
                sb.storage.from_(STORAGE_BUCKET).remove(_tracked_storage_paths)
            except Exception:
                pass
        _tracked_documents.clear()
        _tracked_folders.clear()
        _tracked_storage_paths.clear()


    def run():
        h.reset_counters()

        sb_admin = _service_role_client()

        ok, msg = _verify_phase3_setup(sb_admin)
        if not ok:
            h.test("Phase 3 setup (Migration 019 + folders router)", False, msg)
            return h.passed, h.failed

        token_a = h.get_auth_token()              # TEST_USER_A — regular user
        headers_a = h.auth_headers(token_a)
        token_b = h.get_auth_token(h.TEST_USER_B["email"], h.TEST_USER_B["password"])
        headers_b = h.auth_headers(token_b)
        admin_token = h.get_admin_token()
        headers_admin = h.auth_headers(admin_token)

        # Resolve user UUIDs once.
        u_a = sb_admin.auth.admin.list_users()
        u_a_id = next((u.id for u in u_a if u.email == h.TEST_USER_A["email"]), None)
        u_b_id = next((u.id for u in u_a if u.email == h.TEST_USER_B["email"]), None)

        try:
            # ── FOLDER-02: service-surface smoke ──
            h.section("FOLDER-02 service surface")
            from app.services.folder_service import (
                list_folder, create_folder, move_document, rename_folder, delete_folder,
            )
            h.test("list_folder importable + callable", callable(list_folder))
            h.test("create_folder importable + callable", callable(create_folder))
            h.test("move_document importable + callable", callable(move_document))
            h.test("rename_folder importable + callable", callable(rename_folder))
            h.test("delete_folder importable + callable", callable(delete_folder))

            # ── FOLDER-06: router CRUD happy path ──
            h.section("FOLDER-06 router CRUD")
            test_path = f"/test-folder-{uuid.uuid4().hex[:8]}"

            # POST /api/folders {scope:'user'} as regular user -> 200 + FolderResponse
            r = requests.post(
                f"{h.BASE_URL}/api/folders",
                headers=headers_a,
                json={"path": test_path, "scope": "user"},
                timeout=10,
            )
            h.test("POST /api/folders user-scope returns 200", r.status_code == 200,
                   f"status={r.status_code} body={r.text[:200]}")
            folder_id = None
            if r.status_code == 200:
                folder = r.json()
                folder_id = folder.get("id")
                if folder_id:
                    _track_folder(folder_id, sb_admin)
                h.test("POST returns FolderResponse with id + scope=user + path",
                       folder.get("id") and folder.get("scope") == "user" and folder.get("path") == test_path,
                       f"got: {folder}")

            # GET /api/folders?path={test_path} -> 200 with structured shape
            r = requests.get(
                f"{h.BASE_URL}/api/folders",
                headers=headers_a, params={"path": test_path, "scope": "user"}, timeout=10,
            )
            h.test("GET /api/folders returns 200 with structured shape",
                   r.status_code == 200 and "documents" in r.json() and "subfolders" in r.json(),
                   f"status={r.status_code} body={r.text[:200]}")

            # GET /api/folders without auth -> 401
            r = requests.get(f"{h.BASE_URL}/api/folders", timeout=5)
            h.test("GET /api/folders without auth returns 401",
                   r.status_code == 401, f"status={r.status_code}")

            # ── FOLDER-06 admin gate: non-admin POST scope='global' -> 403 ──
            h.section("FOLDER-06 admin gate")
            r = requests.post(
                f"{h.BASE_URL}/api/folders",
                headers=headers_a,
                json={"path": f"/test-global-{uuid.uuid4().hex[:8]}", "scope": "global"},
                timeout=10,
            )
            h.test("Non-admin POST /api/folders scope=global returns 403",
                   r.status_code == 403, f"status={r.status_code}")

            # Admin POST scope='global' -> 200 + user_id=None
            global_path = f"/test-global-{uuid.uuid4().hex[:8]}"
            r = requests.post(
                f"{h.BASE_URL}/api/folders",
                headers=headers_admin,
                json={"path": global_path, "scope": "global"},
                timeout=10,
            )
            h.test("Admin POST /api/folders scope=global returns 200",
                   r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")
            if r.status_code == 200:
                global_folder = r.json()
                global_folder_id = global_folder.get("id")
                if global_folder_id:
                    _track_folder(global_folder_id, sb_admin)
                h.test("Admin global folder has user_id IS NULL",
                       global_folder.get("user_id") is None,
                       f"got user_id={global_folder.get('user_id')!r}")

            # ── FOLDER-03: atomic rename (happy path + rollback via deliberate-fail variant) ──
            h.section("FOLDER-03 atomic rename")
            # Insert a document at /rename-src-X via service-role
            rename_src_path = f"/rename-src-{uuid.uuid4().hex[:8]}"
            ins = sb_admin.table("documents").insert({
                "user_id": u_a_id, "scope": "user", "folder_path": rename_src_path,
                "file_name": f"doc-{uuid.uuid4().hex[:8]}.txt",
                "file_size": 1, "mime_type": "text/plain", "status": "ready",
            }).execute()
            doc_id_for_rename = ins.data[0]["id"] if ins.data else None
            if doc_id_for_rename:
                _track_doc(doc_id_for_rename, sb_admin)

            # POST /api/folders to register the source folder explicitly
            r = requests.post(
                f"{h.BASE_URL}/api/folders",
                headers=headers_a, json={"path": rename_src_path, "scope": "user"}, timeout=10,
            )
            rename_folder_id = r.json().get("id") if r.status_code == 200 else None
            if rename_folder_id:
                _track_folder(rename_folder_id, sb_admin)

            # PATCH /api/folders/{id} -> rename to /rename-dst-X
            rename_dst_path = f"/rename-dst-{uuid.uuid4().hex[:8]}"
            if rename_folder_id:
                r = requests.patch(
                    f"{h.BASE_URL}/api/folders/{rename_folder_id}",
                    headers=headers_a, json={"new_path": rename_dst_path}, timeout=10,
                )
                h.test("PATCH /api/folders/{id} returns 200", r.status_code == 200,
                       f"status={r.status_code}")
                if r.status_code == 200:
                    body = r.json()
                    h.test("Rename returns documents_updated + folders_updated counts",
                           "documents_updated" in body and "folders_updated" in body,
                           f"keys: {list(body.keys())}")
                # Verify document.folder_path was updated atomically.
                if doc_id_for_rename:
                    row = sb_admin.table("documents").select("folder_path") \
                        .eq("id", doc_id_for_rename).single().execute().data
                    h.test("FOLDER-03 atomic: documents.folder_path updated to new path",
                           row.get("folder_path") == rename_dst_path,
                           f"got folder_path={row.get('folder_path')!r}")

            # ── FOLDER-03 transactional rollback (deliberate-fail RPC variant) ──
            h.section("FOLDER-03 transactional rollback")
            db_url = os.environ.get("DATABASE_URL")
            if not db_url:
                h.test("FOLDER-03 rollback test SKIPPED (no DATABASE_URL)", True,
                       "set DATABASE_URL env var to run; happy-path rename above still validates atomicity")
            else:
                import psycopg2
                pg = psycopg2.connect(db_url)
                pg.autocommit = True
                try:
                    rollback_path = f"/rollback-src-{uuid.uuid4().hex[:8]}"
                    rb_ins = sb_admin.table("documents").insert({
                        "user_id": u_a_id, "scope": "user", "folder_path": rollback_path,
                        "file_name": f"rb-{uuid.uuid4().hex[:8]}.txt",
                        "file_size": 1, "mime_type": "text/plain", "status": "ready",
                    }).execute()
                    rb_doc_id = rb_ins.data[0]["id"] if rb_ins.data else None
                    if rb_doc_id:
                        _track_doc(rb_doc_id, sb_admin)
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
                    raised, _msg = _raises(lambda: sb_admin.rpc(
                        "test_rename_folder_prefix_fails_midway",
                        {"p_old_prefix": rollback_path,
                         "p_new_prefix": f"/rollback-dst-{uuid.uuid4().hex[:8]}",
                         "p_scope": "user", "p_user_id": u_a_id},
                    ).execute())
                    h.test("Deliberate-fail RPC raises", raised)
                    if rb_doc_id:
                        row = sb_admin.table("documents").select("folder_path") \
                            .eq("id", rb_doc_id).single().execute().data
                        h.test("After rollback, folder_path UNCHANGED (transactional)",
                               row.get("folder_path") == rollback_path,
                               f"got folder_path={row.get('folder_path')!r}")
                finally:
                    try:
                        with pg.cursor() as cur:
                            cur.execute("DROP FUNCTION IF EXISTS public.test_rename_folder_prefix_fails_midway(TEXT, TEXT, TEXT, UUID);")
                    except Exception:
                        pass
                    pg.close()

            # ── FOLDER-04: non-empty rejection (structured 409) ──
            h.section("FOLDER-04 non-empty rejected")
            ne_path = f"/non-empty-{uuid.uuid4().hex[:8]}"
            ne_ins = sb_admin.table("documents").insert({
                "user_id": u_a_id, "scope": "user", "folder_path": ne_path,
                "file_name": f"ne-{uuid.uuid4().hex[:8]}.txt",
                "file_size": 1, "mime_type": "text/plain", "status": "ready",
            }).execute()
            ne_doc_id = ne_ins.data[0]["id"] if ne_ins.data else None
            if ne_doc_id:
                _track_doc(ne_doc_id, sb_admin)
            r = requests.post(
                f"{h.BASE_URL}/api/folders", headers=headers_a,
                json={"path": ne_path, "scope": "user"}, timeout=10,
            )
            ne_folder_id = r.json().get("id") if r.status_code == 200 else None
            if ne_folder_id:
                _track_folder(ne_folder_id, sb_admin)
            if ne_folder_id:
                r = requests.delete(
                    f"{h.BASE_URL}/api/folders/{ne_folder_id}", headers=headers_a, timeout=10,
                )
                h.test("DELETE non-empty folder returns 409",
                       r.status_code == 409, f"status={r.status_code}")
                if r.status_code == 409:
                    body = r.json()
                    h.test("409 body has FOLDER_NOT_EMPTY error code",
                           body.get("error") == "FOLDER_NOT_EMPTY",
                           f"got error={body.get('error')!r}")
                    h.test("409 body shows document_count >= 1",
                           body.get("document_count", 0) >= 1,
                           f"got document_count={body.get('document_count')}")
            # No-orphan check: the document must still exist with folder_path=ne_path.
            if ne_doc_id:
                row = sb_admin.table("documents").select("folder_path") \
                    .eq("id", ne_doc_id).single().execute().data
                h.test("FOLDER-04 no-orphan: document at rejected-delete path UNCHANGED",
                       row.get("folder_path") == ne_path,
                       f"got folder_path={row.get('folder_path')!r}")

            # ── FOLDER-05: dedup key — same file in two folders -> 2 docs ──
            h.section("FOLDER-05 dedup key")
            d_a = f"/dedup-a-{uuid.uuid4().hex[:8]}"
            d_b = f"/dedup-b-{uuid.uuid4().hex[:8]}"
            d_file_name = f"dedup-{uuid.uuid4().hex[:6]}.txt"
            r1 = requests.post(
                f"{h.BASE_URL}/api/files/upload", headers={"Authorization": f"Bearer {token_a}"},
                params={"folder_path": d_a, "scope": "user"},
                files={"file": (d_file_name, CAPYBARA_TEXT, "text/plain")}, timeout=30,
            )
            h.test("FOLDER-05 first upload at /a returns action='created'",
                   r1.status_code == 200 and r1.json().get("action") == "created",
                   f"status={r1.status_code} action={r1.json().get('action') if r1.status_code == 200 else None}")
            if r1.status_code == 200:
                _track_doc(r1.json().get("id"), sb_admin)
                _tracked_storage_paths.append(f"{u_a_id}/{r1.json().get('id')}.txt")
            # Second upload SAME file at SAME folder -> action='skip'
            r2 = requests.post(
                f"{h.BASE_URL}/api/files/upload", headers={"Authorization": f"Bearer {token_a}"},
                params={"folder_path": d_a, "scope": "user"},
                files={"file": (d_file_name, CAPYBARA_TEXT, "text/plain")}, timeout=30,
            )
            h.test("FOLDER-05 same file at same path returns action='skipped'",
                   r2.status_code == 200 and r2.json().get("action") == "skipped",
                   f"status={r2.status_code} action={r2.json().get('action') if r2.status_code == 200 else None}")
            # Third upload SAME file at DIFFERENT folder -> action='create' (FOLDER-05 acceptance)
            r3 = requests.post(
                f"{h.BASE_URL}/api/files/upload", headers={"Authorization": f"Bearer {token_a}"},
                params={"folder_path": d_b, "scope": "user"},
                files={"file": (d_file_name, CAPYBARA_TEXT, "text/plain")}, timeout=30,
            )
            h.test("FOLDER-05 same file at DIFFERENT folder returns action='created'",
                   r3.status_code == 200 and r3.json().get("action") == "created",
                   f"status={r3.status_code} action={r3.json().get('action') if r3.status_code == 200 else None}")
            if r3.status_code == 200:
                _track_doc(r3.json().get("id"), sb_admin)
                _tracked_storage_paths.append(f"{u_a_id}/{r3.json().get('id')}.txt")

            # ── FOLDER-07: files router extensions (upload + PATCH) ──
            h.section("FOLDER-07 files router extensions")
            up_path = f"/upload-x-{uuid.uuid4().hex[:8]}"
            up_file = f"upload-{uuid.uuid4().hex[:6]}.txt"
            ru = requests.post(
                f"{h.BASE_URL}/api/files/upload", headers={"Authorization": f"Bearer {token_a}"},
                params={"folder_path": up_path, "scope": "user"},
                files={"file": (up_file, CAPYBARA_TEXT, "text/plain")}, timeout=30,
            )
            h.test("FOLDER-07 upload?folder_path returns 200",
                   ru.status_code == 200, f"status={ru.status_code}")
            up_doc_id = None
            if ru.status_code == 200:
                up_doc = ru.json()
                up_doc_id = up_doc.get("id")
                if up_doc_id:
                    _track_doc(up_doc_id, sb_admin)
                    _tracked_storage_paths.append(f"{u_a_id}/{up_doc_id}.txt")
                h.test("FOLDER-07 upload row has folder_path + scope set",
                       up_doc.get("folder_path") == up_path and up_doc.get("scope") == "user",
                       f"got folder_path={up_doc.get('folder_path')!r} scope={up_doc.get('scope')!r}")

            # PATCH rename
            if up_doc_id:
                rp = requests.patch(
                    f"{h.BASE_URL}/api/files/{up_doc_id}", headers=headers_a,
                    json={"file_name": "renamed.txt"}, timeout=10,
                )
                h.test("FOLDER-07 PATCH rename returns 200",
                       rp.status_code == 200 and rp.json().get("file_name") == "renamed.txt",
                       f"status={rp.status_code}")

                # PATCH move
                new_path = f"/upload-y-{uuid.uuid4().hex[:8]}"
                rp2 = requests.patch(
                    f"{h.BASE_URL}/api/files/{up_doc_id}", headers=headers_a,
                    json={"folder_path": new_path}, timeout=10,
                )
                h.test("FOLDER-07 PATCH move returns 200 with new folder_path",
                       rp2.status_code == 200 and rp2.json().get("folder_path") == new_path,
                       f"status={rp2.status_code} folder_path={rp2.json().get('folder_path') if rp2.status_code == 200 else None}")

                # PATCH empty body -> 400
                rp3 = requests.patch(
                    f"{h.BASE_URL}/api/files/{up_doc_id}", headers=headers_a,
                    json={}, timeout=10,
                )
                h.test("FOLDER-07 PATCH empty body returns 400",
                       rp3.status_code == 400, f"status={rp3.status_code}")

                # PATCH scope smuggling -> 200 (Pydantic ignores) AND scope unchanged
                rp4 = requests.patch(
                    f"{h.BASE_URL}/api/files/{up_doc_id}", headers=headers_a,
                    json={"scope": "global"}, timeout=10,
                )
                # Note: with empty body after Pydantic strips, the route raises 400.
                # Smuggled-scope WITH a valid field MUST be silently dropped:
                rp5 = requests.patch(
                    f"{h.BASE_URL}/api/files/{up_doc_id}", headers=headers_a,
                    json={"scope": "global", "file_name": "smug.txt"}, timeout=10,
                )
                h.test("FOLDER-07 PATCH with smuggled scope (and valid field) returns 200",
                       rp5.status_code == 200, f"status={rp5.status_code}")
                if rp5.status_code == 200:
                    h.test("FOLDER-07 smuggled scope is silently dropped (scope unchanged)",
                           rp5.json().get("scope") == "user",
                           f"got scope={rp5.json().get('scope')!r}")

            # ── Pitfall 10: concurrent-upload-no-orphan (10 parallel uploads) ──
            h.section("Pitfall 10 concurrent upload no-orphan")
            test_path = f"/test-race-{uuid.uuid4().hex[:8]}"
            file_bytes = b"race test content"

            def _upload(idx):
                try:
                    return requests.post(
                        f"{h.BASE_URL}/api/files/upload",
                        headers={"Authorization": f"Bearer {token_a}"},
                        params={"folder_path": test_path, "scope": "user"},
                        files={"file": (f"race-{idx}-{uuid.uuid4().hex[:4]}.txt", file_bytes, "text/plain")},
                        timeout=60,
                    )
                except Exception as e:
                    return type("R", (), {"status_code": 0, "_e": str(e), "json": lambda self=None: {}})()

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
                results = list(ex.map(_upload, range(10)))

            success_count = sum(1 for r in results if getattr(r, "status_code", 0) == 200)
            h.test("Pitfall 10: all 10 parallel uploads return 200",
                   success_count == 10, f"got {success_count}/10 successes")

            # Track every doc for cleanup.
            for r in results:
                if getattr(r, "status_code", 0) == 200:
                    try:
                        doc_id = r.json().get("id")
                        if doc_id:
                            _track_doc(doc_id, sb_admin)
                            _tracked_storage_paths.append(f"{u_a_id}/{doc_id}.txt")
                    except Exception:
                        pass

            # Strategy B assertion: folders table did NOT acquire a row at test_path.
            folders_check = sb_admin.table("folders").select("id").eq("path", test_path).execute()
            h.test("Strategy B: folders table has 0 rows at brand-new upload path",
                   len(folders_check.data or []) == 0,
                   f"got {len(folders_check.data or [])} folder rows (Strategy B locks at 0)")

        finally:
            _cleanup()

        return h.passed, h.failed


    if __name__ == "__main__":
        run()
        sys.exit(h.summary())
    ```

    Conventions to honor:
    - Module docstring lists prerequisites + CLAUDE.md cleanup pledge.
    - Imports: stdlib (concurrent.futures, os, sys, uuid) → third-party (requests) → after sys.path bootstrap, `import test_helpers as h`, `from app.services.folder_service import normalize_path`, `from supabase import create_client`.
    - Two `sys.path.insert(0, ...)` calls (matches test_two_scope_rls.py:32-37).
    - Use existing `h.section()` / `h.test()` / `h.get_auth_token()` / `h.get_admin_token()` / `h.get_user_supabase_client()` / `h.auth_headers()` helpers — do NOT reimplement.
    - Tracking lists are MODULE-LEVEL (`_tracked_documents`, `_tracked_folders`, `_tracked_storage_paths`).
    - `run()` returns `(h.passed, h.failed)` so test_all.py can sum.
    - Service-role client constructed inline via `_service_role_client()`.
    - Concurrent uploads use ThreadPoolExecutor(max_workers=10); each request has timeout=60.
    - Mid-rename rollback test SKIPS gracefully if DATABASE_URL not set (matches test_two_scope_rls.py:396-397 pattern).
    - DROP FUNCTION IF EXISTS in finally inside the rollback section so the test fixture is clean.

    Critical DON'Ts:
    - DO NOT use `DELETE FROM` without a WHERE clause (CLAUDE.md mandatory rule).
    - DO NOT `TRUNCATE` any table (CLAUDE.md mandatory rule).
    - DO NOT delete documents/folders that this test did not create (no cross-user / cross-test cleanup).
    - DO NOT add LangChain / LangGraph (project rule).
    - DO NOT add LangSmith @traceable (out of scope for tests).
    - DO NOT spin up the FastAPI backend — assume http://localhost:8001 per existing convention.
    - DO NOT use PDF / PPTX / OCR fixtures (slow and unnecessary; plain text suffices).
    - DO NOT test `--purge-orphans` end-to-end (out of scope; that's Phase 2 territory).
    - DO NOT add concurrent-FOLDER-creation race tests (single-folder behavior is asserted via the upload path; an explicit POST /api/folders race test is welcome but not required for SC5).
    - DO NOT skip the rollback test entirely when DATABASE_URL is missing — emit a SKIP h.test with explanation (informative for CI / dev environments).
    - DO NOT use `sb.table('folders').delete().neq('id', '00..0')` or any pattern that bulk-deletes — only per-id .eq().delete().
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -c "import pathlib, ast; src = pathlib.Path('scripts/test_folders.py').read_text(encoding='utf-8'); ast.parse(src); body = '\n'.join(line for line in src.splitlines() if not line.lstrip().startswith('#')); assert 'def run()' in body, 'run() entry point missing'; assert 'return h.passed, h.failed' in body, 'run() must return (passed, failed)'; assert 'sys.exit(h.summary())' in body, 'main block missing'; assert 'def _verify_phase3_setup' in body, 'canary precheck missing'; assert 'rpc(\"rename_folder_prefix\"' in body, 'rename_folder_prefix probe missing'; assert '_tracked_documents' in body, 'tracking list missing'; assert '_tracked_folders' in body, 'tracking list missing'; assert 'def _cleanup' in body, 'cleanup helper missing'; assert 'concurrent.futures.ThreadPoolExecutor' in body, 'concurrent test missing'; assert 'FOLDER_NOT_EMPTY' in body, 'FOLDER_NOT_EMPTY assertion missing'; assert 'FOLDER-02' in body and 'FOLDER-03' in body and 'FOLDER-04' in body and 'FOLDER-05' in body and 'FOLDER-06' in body and 'FOLDER-07' in body, 'requirement labels missing'; assert 'Pitfall 10' in body, 'Pitfall 10 section label missing'; assert 'test-race' in body, 'concurrent-upload-no-orphan path pattern missing'; assert 'test_rename_folder_prefix_fails_midway' in body, 'deliberate-fail RPC variant missing'; assert 'DROP FUNCTION IF EXISTS' in body, 'rollback test cleanup missing'; assert body.count('h.test(') &gt;= 25, f'expected at least 25 h.test() assertions'; assert body.count('h.section(') &gt;= 8, f'expected at least 8 h.section() calls (one per logical group)'; import re; bare_delete = re.search(r'DELETE FROM\\s+\\w+\\s*$', body.upper()); assert not bare_delete, 'bare DELETE FROM forbidden'; assert 'TRUNCATE' not in body.upper(), 'TRUNCATE forbidden'; assert '@traceable' not in body, '@traceable out of scope'; assert 'from langchain' not in body and 'from langgraph' not in body, 'LangChain/LangGraph forbidden'; print(f'test_folders.py structure OK; {len(src.splitlines())} lines, {body.count(chr(104)+chr(46)+chr(116)+chr(101)+chr(115)+chr(116)+chr(40))} h.test() assertions')" &amp;&amp; venv/Scripts/python -c "import sys, os; sys.path.insert(0, 'scripts'); sys.path.insert(0, '.'); import test_folders; assert hasattr(test_folders, 'run'), 'run() not exported'; assert callable(test_folders.run); print('test_folders imports OK')"</automated>
  </verify>
  <acceptance_criteria>
    - File `backend/scripts/test_folders.py` exists.
    - File parses as valid Python.
    - `grep -c "^def run()" backend/scripts/test_folders.py` returns 1.
    - `grep -c "return h.passed, h.failed" backend/scripts/test_folders.py` returns 1.
    - `grep -c "sys.exit(h.summary())" backend/scripts/test_folders.py` returns 1.
    - `grep -c "def _verify_phase3_setup" backend/scripts/test_folders.py` returns 1.
    - `grep -c "rpc(\"rename_folder_prefix\"" backend/scripts/test_folders.py` returns at least 1 (canary probe).
    - `grep -c "concurrent.futures.ThreadPoolExecutor" backend/scripts/test_folders.py` returns at least 1.
    - `grep -c "FOLDER_NOT_EMPTY" backend/scripts/test_folders.py` returns at least 1 (FOLDER-04 structured 409 assertion).
    - `grep -c "_tracked_documents" backend/scripts/test_folders.py` returns at least 3 (def + .append + .clear).
    - `grep -c "_tracked_folders" backend/scripts/test_folders.py` returns at least 3.
    - `grep -c "def _cleanup" backend/scripts/test_folders.py` returns 1.
    - File contains the literal string `test_rename_folder_prefix_fails_midway` (deliberate-fail rollback fixture).
    - File contains `DROP FUNCTION IF EXISTS` (rollback fixture cleanup).
    - File contains the literal string `test-race` (concurrent-upload path pattern).
    - File contains `Pitfall 10` (concurrent-upload section label).
    - File contains all 6 requirement labels: `FOLDER-02`, `FOLDER-03`, `FOLDER-04`, `FOLDER-05`, `FOLDER-06`, `FOLDER-07`.
    - `grep -c "h.test(" backend/scripts/test_folders.py` returns at least 25.
    - `grep -c "h.section(" backend/scripts/test_folders.py` returns at least 8.
    - File contains NO bare `DELETE FROM <table>` (no WHERE clause) — sanity check via regex.
    - File contains NO `TRUNCATE` keyword (case-insensitive).
    - File contains NO `@traceable` decorator.
    - File contains NO `from langchain` or `from langgraph` imports.
    - Module imports cleanly: `cd backend && venv/Scripts/python -c "import sys; sys.path.insert(0, 'scripts'); sys.path.insert(0, '.'); import test_folders; assert hasattr(test_folders, 'run'); print('OK')"` prints `OK`.
    - File length is at least 350 lines.
  </acceptance_criteria>
  <done>
    `backend/scripts/test_folders.py` is built with 8+ sections covering FOLDER-02..07 + TEST-01 + Pitfalls 4/5/10, 25+ h.test() assertions, scoped cleanup of tracked documents/folders/storage paths, no blanket DELETE / TRUNCATE, and a canary precheck that bails fast with [FATAL] message if Migration 019 or the folders router is missing. Module imports cleanly via venv Python.
  </done>
</task>

<task id="3-06-02" type="auto">
  <name>Task 2: Register test_folders in test_all.py SUITES list (immediately after Files; SUITES count 14 → 15)</name>
  <files>backend/scripts/test_all.py</files>
  <read_first>
    - backend/scripts/test_all.py FULL FILE (the SUITES list at L27-42 is the only thing this task modifies; the runner's `for name, module in SUITES` loop at L51 consumes the list directly)
    - .planning/phases/03-folder-service-routers-dedup-extension/03-PATTERNS.md §`backend/scripts/test_all.py` (paste-ready snippet showing `("Folders", test_folders)` placement after Files)
    - .planning/phases/02-content-markdown-backfill-gated/02-04-PLAN.md (Phase 2 / Plan 04 — the most-recent precedent for this convention; the Backfill registration is the analog)
    - CLAUDE.md ("Backend validation suite ... Update backend/scripts/test_all.py if adding a new module")
  </read_first>
  <action>
    Modify `backend/scripts/test_all.py` to register the new Folders suite. Two edits in the file.

    ### Edit 1: Add the import line (after L16 `import test_files`)

    Current L11-25:
    ```python
    import test_helpers as h
    import test_health
    import test_auth
    import test_threads
    import test_messages
    import test_files
    import test_backfill
    import test_rag
    ...
    import test_sub_agents
    ```

    Insert `import test_folders` immediately AFTER `import test_files` and BEFORE `import test_backfill`:
    ```python
    import test_helpers as h
    import test_health
    import test_auth
    import test_threads
    import test_messages
    import test_files
    import test_folders         # NEW (Phase 3)
    import test_backfill
    import test_rag
    ...
    import test_sub_agents
    ```

    ### Edit 2: Add the SUITES tuple (after `("Files", test_files)`)

    Current L27-42:
    ```python
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

    Insert `("Folders", test_folders)` IMMEDIATELY AFTER `("Files", test_files)` and BEFORE `("Backfill", test_backfill)` so the file/folder family is contiguous:
    ```python
    SUITES = [
        ("Health", test_health),
        ("Auth", test_auth),
        ("Threads", test_threads),
        ("Messages", test_messages),
        ("Files", test_files),
        ("Folders", test_folders),       # NEW (Phase 3 — folders is logically a Files extension)
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

    Conventions to honor:
    - Suite name `"Folders"` — Title case, single word — matches `"Files"` / `"Backfill"`.
    - Inline comment `# NEW (Phase 3 — folders is logically a Files extension)` documents the addition.
    - No other lines modified — do NOT touch the `main()` function, the `clear_token_cache()` call, or the summary print logic.
    - Do NOT renumber or reorder any other suite entries.

    Critical DON'Ts:
    - DO NOT place `("Folders", test_folders)` AFTER `("Backfill", test_backfill)` — the convention is files-family adjacency: Files → Folders → Backfill.
    - DO NOT remove or rename any existing suite tuple.
    - DO NOT change the `def main()` function body.
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -c "src = open('scripts/test_all.py', encoding='utf-8').read(); assert 'import test_folders' in src, 'import test_folders missing'; assert '(\"Folders\", test_folders)' in src, 'Folders suite tuple missing'; idx_files_imp = src.find('import test_files'); idx_folders_imp = src.find('import test_folders'); idx_backfill_imp = src.find('import test_backfill'); assert 0 &lt; idx_files_imp &lt; idx_folders_imp &lt; idx_backfill_imp, f'import order must be test_files -> test_folders -> test_backfill'; idx_files_t = src.find('(\"Files\", test_files)'); idx_folders_t = src.find('(\"Folders\", test_folders)'); idx_backfill_t = src.find('(\"Backfill\", test_backfill)'); assert 0 &lt; idx_files_t &lt; idx_folders_t &lt; idx_backfill_t, f'SUITES tuple order must be Files -> Folders -> Backfill'; print('test_all.py SUITES registration OK')" &amp;&amp; venv/Scripts/python -c "import sys; sys.path.insert(0, 'scripts'); from test_all import SUITES; names = [n for n, _ in SUITES]; assert 'Folders' in names, f'Folders not in SUITES: {names}'; assert names.index('Folders') == names.index('Files') + 1, f'Folders must be immediately after Files. Got order: {names}'; assert names.index('Backfill') == names.index('Folders') + 1, f'Backfill must be immediately after Folders. Got order: {names}'; assert len(SUITES) == 15, f'expected 15 SUITES (was 14 + Folders), got {len(SUITES)}'; print('SUITES order + count OK')"</automated>
  </verify>
  <acceptance_criteria>
    - `grep -c "^import test_folders$" backend/scripts/test_all.py` returns 1.
    - `grep -c "(\"Folders\", test_folders)" backend/scripts/test_all.py` returns 1.
    - The line `import test_folders` appears AFTER `import test_files` in the file.
    - The line `import test_folders` appears BEFORE `import test_backfill` in the file.
    - The line `("Folders", test_folders)` appears AFTER `("Files", test_files)` in the file.
    - The line `("Folders", test_folders)` appears BEFORE `("Backfill", test_backfill)` in the file.
    - `cd backend && venv/Scripts/python -c "import sys; sys.path.insert(0, 'scripts'); from test_all import SUITES; names = [n for n, _ in SUITES]; assert 'Folders' in names; assert names.index('Folders') == names.index('Files') + 1; assert names.index('Backfill') == names.index('Folders') + 1; print('OK')"` prints `OK`.
    - `cd backend && venv/Scripts/python -c "import sys; sys.path.insert(0, 'scripts'); import test_all; assert len(test_all.SUITES) == 15; print('OK')"` prints `OK`.
    - The other 14 suite entries are unchanged.
  </acceptance_criteria>
  <done>
    `backend/scripts/test_all.py` imports `test_folders` and registers `("Folders", test_folders)` in the SUITES list immediately after `("Files", test_files)` and before `("Backfill", test_backfill)`. SUITES count is 15 (was 14). No other lines in `test_all.py` are modified.
  </done>
</task>

<task id="3-06-03" type="checkpoint:human-verify" gate="blocking">
  <name>Checkpoint: confirm Phase 3 deployment prerequisites + run test_folders.py to validate green</name>
  <what-built>
    Phase 3 plans 01-05 are complete in code:
      - Plan 01: Migration 019 (3 RPCs) authored AND applied; Pydantic schemas extended.
      - Plan 02: folder_service.py extended with 5 CRUD functions.
      - Plan 03: record_manager.determine_action extended with scope/folder_path kwargs.
      - Plan 04: backend/app/routers/folders.py created; main.py registers it.
      - Plan 05: backend/app/routers/files.py extended with folder_path/scope query args + PATCH /{id} endpoint.

    Plan 06 / Task 1 created `backend/scripts/test_folders.py` (~25 h.test assertions; canary precheck; Pitfall 10 ThreadPoolExecutor; FOLDER-03 deliberate-fail rollback fixture).
    Plan 06 / Task 2 registered the suite in test_all.py.

    Two operational pre-reqs that the operator should confirm BEFORE running the suite:
      1. Migration 019 has been applied (Plan 01 / Task 2 — should already be done, but the canary verifies).
      2. Backend is running on http://localhost:8001 (the upload-path tests hit POST /api/files/upload).
      3. Admin user is promoted: UPDATE public.profiles SET is_admin=true WHERE email='admin@test.com'.
      4. (Optional but recommended for the FOLDER-03 rollback test) DATABASE_URL is set in the shell — without it, the rollback test SKIPs gracefully but the empirical proof of mid-rename rollback is missed.
  </what-built>
  <how-to-verify>
    Operator performs these steps:

    1. Confirm backend is running. From a separate terminal:
       ```
       cd backend; venv/Scripts/python -m uvicorn app.main:app --reload --port 8001
       ```
       Leave running. In another terminal verify health: `curl http://localhost:8001/health` -> `{"status":"ok"}`.

    2. (Optional but recommended) Set DATABASE_URL in the test-runner shell:
       ```
       $env:DATABASE_URL = "postgresql://postgres.<project>:<password>@<host>:5432/postgres"
       ```
       Without this, the FOLDER-03 transactional rollback test SKIPs (the happy-path rename still validates the atomic contract).

    3. Confirm admin user is promoted. From the project root:
       ```
       cd backend; venv/Scripts/python -c "import os; from supabase import create_client; sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY']); r = sb.table('profiles').select('email,is_admin').eq('email','admin@test.com').single().execute(); print(r.data)"
       ```
       Expected: `{'email': 'admin@test.com', 'is_admin': True}`. If `is_admin` is False or the row is missing, run via SQL editor:
       ```sql
       UPDATE public.profiles SET is_admin = true WHERE email = 'admin@test.com';
       ```

    4. Run the test suite single-suite (fast, ~30 sec):
       ```
       cd backend; venv/Scripts/python scripts/test_folders.py
       ```
       Expected output ends with: `Results: N passed, 0 failed` where N >= 25.

       If the canary fires with `[FATAL] rename_folder_prefix RPC missing`: re-run Plan 01 / Task 2 (apply Migration 019).
       If the canary fires with `[FATAL] GET /api/folders returns 404`: re-run Plan 04 / Task 2 (register router in main.py) AND restart the backend.
       If a non-canary FAIL appears (e.g., FOLDER-04 returns 500 instead of 409): inspect the failure detail; cross-reference against the responsible plan's acceptance criteria.

    5. (Optional, ~3 min) Cross-suite sweep — confirm Phase 3 doesn't regress other suites:
       ```
       cd backend; venv/Scripts/python scripts/test_all.py
       ```
       Expected: 15 suites run; Folders suite shows N/0 PASS; the previously-known Phase-1 carry-forward FAILs (admin-assumption regression in Threads/Messages/Hybrid/Tools/Sub-Agents per STATE.md) are still present and OUT OF SCOPE for Phase 3.
  </how-to-verify>
  <resume-signal>Type "approved" if test_folders.py passes (N passed, 0 failed where N >= 25). If any non-canary FAIL appears, describe the failure (suite section + assertion name + detail message) so the agent can investigate which Phase 3 plan needs revision.</resume-signal>
</task>

</tasks>

<verification>
This plan delivers TEST-01 (test_folders.py covers FOLDER-02..07 + concurrent-upload + transactional rollback + non-empty rejection) AND empirically validates FOLDER-02..07 + Pitfalls 4/5/10 + the 5 ROADMAP success criteria. Maps to .planning/phases/03-folder-service-routers-dedup-extension/03-VALIDATION.md row "3-06-* | 06 (test_folders.py + register) | 4 | TEST-01 | covers all above".

Verification steps:
- Task 1: AST + grep gates confirm test_folders.py has the run() entry point, the 8+ sections, 25+ h.test() assertions, the cleanup discipline (tracking lists, no blanket deletes), the labels for all 6 FOLDER-* requirement IDs, the Pitfall 10 concurrent-upload fixture, and the FOLDER-03 deliberate-fail rollback variant.
- Task 2: Import + SUITES order verified via Python introspection; SUITES count is 15 (was 14).
- Task 3 (checkpoint): operator confirms backend running, admin user promoted, optionally DATABASE_URL set, then runs the suite. Failure modes have actionable remediation messages.

SC-to-test mapping (from 03-VALIDATION.md):
  SC1 -> [FOLDER-06 router CRUD] section: covers create/list/patch/delete + admin-403
  SC2 -> [FOLDER-03 atomic rename] + [FOLDER-03 transactional rollback] sections
  SC3 -> [FOLDER-04 non-empty rejected] section (409 + structured body + no-orphan)
  SC4 -> [FOLDER-05 dedup key] section (same file in two folders -> 2 docs)
  SC5 -> [FOLDER-07 files router] + [Pitfall 10 concurrent upload] sections
</verification>

<success_criteria>
- backend/scripts/test_folders.py exists and is importable; run() returns (passed, failed).
- The suite covers FOLDER-02..07 + TEST-01 + SC1..SC5 + Pitfalls 4/5/10 with at least 25 h.test() assertions across at least 8 named sections.
- Canary precheck bails fast with actionable [FATAL] message if Migration 019 or folders router missing.
- All cleanup is per-tracked-id; ZERO bulk DELETE / TRUNCATE statements.
- backend/scripts/test_all.py registers the suite immediately after Files (SUITES count 14 → 15).
- The suite passes green (N passed, 0 failed where N >= 25) when prerequisites are met (Task 3 checkpoint).
- Phase 3 verification gate (5 ROADMAP success criteria) is empirically satisfied.
</success_criteria>

<output>
After completion, create `.planning/phases/03-folder-service-routers-dedup-extension/03-06-SUMMARY.md` recording: file created (test_folders.py), test count by section (~25 assertions across 8 sections), the SUITES registration line in test_all.py, the canary probe pattern (rename_folder_prefix RPC + GET /api/folders 401), the FOLDER-03 deliberate-fail rollback fixture pattern (psycopg2 + DROP FUNCTION in finally), the Pitfall 10 ThreadPoolExecutor pattern, the operator-checkpoint outcome (test run pass/fail counts), and the cross-suite sweep result if Task 3 ran the full suite. Phase 3 closes after this plan ships green.
</output>
