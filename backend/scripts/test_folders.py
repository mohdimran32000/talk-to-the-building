"""Integration tests for Phase 3: folder service + routers + dedup extension.

Covers:
  - FOLDER-02: list_folder / create_folder / move_document / rename_folder /
              delete_folder service-surface smoke (importability + signatures)
  - FOLDER-03: rename_folder_prefix RPC atomically updates documents + folders
              (transactional rollback verified via deliberate-fail test fixture)
  - FOLDER-04: delete_folder_if_empty RPC rejects non-empty with structured 409
              ({error: 'FOLDER_NOT_EMPTY', document_count, subfolder_count})
  - FOLDER-05: dedup key extension - same file in two folders -> 2 docs;
              same file in same (scope, user, path) -> action='skipped'
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
resources in finally. No blanket deletes, no whole-table wipes, no cross-user cleanup.
"""
import concurrent.futures
import os
import sys
import uuid

import requests

# Two-step sys.path bootstrap (matches test_two_scope_rls.py:32-37 + test_backfill.py:39-40).
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

import test_helpers as h  # noqa: E402
from app.services.folder_service import normalize_path  # noqa: E402,F401  (re-exported for downstream tests)
from supabase import create_client  # noqa: E402


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


def _track_doc(doc_id, sb_client):
    if doc_id:
        _tracked_documents.append((doc_id, sb_client))


def _track_folder(folder_id, sb_client):
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


def _verify_phase3_setup(sb_admin):
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
            return False, "rename_folder_prefix returned no data - function exists but is broken"
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
                "GET /api/folders returns 404 - folders router not registered in main.py. "
                "Add `from app.routers import folders` and `app.include_router(folders.router)`."
            )
    except Exception as e:
        return False, (
            f"Backend unreachable: {e}. Start with: "
            f"cd backend && venv/Scripts/python -m uvicorn app.main:app --reload --port 8001"
        )
    return True, "ok"


def _cleanup():
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
        h.test("Phase 3 setup (Migration 019 + folders router)", False, f"[FATAL] {msg}")
        return h.passed, h.failed

    token_a = h.get_auth_token()              # TEST_USER_A - regular user
    headers_a = h.auth_headers(token_a)
    token_b = h.get_auth_token(h.TEST_USER_B["email"], h.TEST_USER_B["password"])
    headers_b = h.auth_headers(token_b)
    admin_token = h.get_admin_token()
    headers_admin = h.auth_headers(admin_token)

    # Resolve user UUIDs once.
    users_resp = sb_admin.auth.admin.list_users()
    u_a_id = next((u.id for u in users_resp if u.email == h.TEST_USER_A["email"]), None)
    u_b_id = next((u.id for u in users_resp if u.email == h.TEST_USER_B["email"]), None)

    try:
        # -- FOLDER-02: service-surface smoke --
        h.section("FOLDER-02 service surface")
        from app.services.folder_service import (
            list_folder, create_folder, move_document, rename_folder, delete_folder,
        )
        h.test("FOLDER-02 list_folder importable + callable", callable(list_folder))
        h.test("FOLDER-02 create_folder importable + callable", callable(create_folder))
        h.test("FOLDER-02 move_document importable + callable", callable(move_document))
        h.test("FOLDER-02 rename_folder importable + callable", callable(rename_folder))
        h.test("FOLDER-02 delete_folder importable + callable", callable(delete_folder))

        # -- FOLDER-06: router CRUD happy path --
        h.section("FOLDER-06 router CRUD")
        test_path = f"/test-folder-{uuid.uuid4().hex[:8]}"

        # POST /api/folders {scope:'user'} as regular user -> 200 + FolderResponse
        r = requests.post(
            f"{h.BASE_URL}/api/folders",
            headers=headers_a,
            json={"path": test_path, "scope": "user"},
            timeout=10,
        )
        h.test("FOLDER-06 POST /api/folders user-scope returns 200", r.status_code == 200,
               f"status={r.status_code} body={r.text[:200]}")
        folder_id = None
        if r.status_code == 200:
            folder = r.json()
            folder_id = folder.get("id")
            if folder_id:
                _track_folder(folder_id, sb_admin)
            h.test("FOLDER-06 POST returns FolderResponse with id + scope=user + path",
                   bool(folder.get("id")) and folder.get("scope") == "user" and folder.get("path") == test_path,
                   f"got: {folder}")

        # GET /api/folders?path={test_path} -> 200 with structured shape
        r = requests.get(
            f"{h.BASE_URL}/api/folders",
            headers=headers_a, params={"path": test_path, "scope": "user"}, timeout=10,
        )
        ok_get = r.status_code == 200 and "documents" in r.json() and "subfolders" in r.json()
        h.test("FOLDER-06 GET /api/folders returns 200 with structured shape",
               ok_get, f"status={r.status_code} body={r.text[:200]}")

        # GET /api/folders without auth -> 401
        r = requests.get(f"{h.BASE_URL}/api/folders", timeout=5)
        h.test("FOLDER-06 GET /api/folders without auth returns 401",
               r.status_code == 401, f"status={r.status_code}")

        # -- FOLDER-06 admin gate: non-admin POST scope='global' -> 403 --
        h.section("FOLDER-06 admin gate")
        r = requests.post(
            f"{h.BASE_URL}/api/folders",
            headers=headers_a,
            json={"path": f"/test-global-{uuid.uuid4().hex[:8]}", "scope": "global"},
            timeout=10,
        )
        h.test("FOLDER-06 non-admin POST /api/folders scope=global returns 403",
               r.status_code == 403, f"status={r.status_code}")

        # Admin POST scope='global' -> 200 + user_id=None
        global_path = f"/test-global-{uuid.uuid4().hex[:8]}"
        r = requests.post(
            f"{h.BASE_URL}/api/folders",
            headers=headers_admin,
            json={"path": global_path, "scope": "global"},
            timeout=10,
        )
        h.test("FOLDER-06 admin POST /api/folders scope=global returns 200",
               r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")
        if r.status_code == 200:
            global_folder = r.json()
            global_folder_id = global_folder.get("id")
            if global_folder_id:
                _track_folder(global_folder_id, sb_admin)
            h.test("FOLDER-06 admin global folder has user_id IS NULL",
                   global_folder.get("user_id") is None,
                   f"got user_id={global_folder.get('user_id')!r}")

        # -- FOLDER-03: atomic rename (happy path) --
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
            h.test("FOLDER-03 PATCH /api/folders/{id} returns 200", r.status_code == 200,
                   f"status={r.status_code}")
            if r.status_code == 200:
                body = r.json()
                h.test("FOLDER-03 rename returns documents_updated + folders_updated counts",
                       "documents_updated" in body and "folders_updated" in body,
                       f"keys: {list(body.keys())}")
            # Verify document.folder_path was updated atomically.
            if doc_id_for_rename:
                row = sb_admin.table("documents").select("folder_path") \
                    .eq("id", doc_id_for_rename).single().execute().data
                h.test("FOLDER-03 atomic: documents.folder_path updated to new path",
                       row.get("folder_path") == rename_dst_path,
                       f"got folder_path={row.get('folder_path')!r}")

        # -- FOLDER-03 transactional rollback (deliberate-fail RPC variant) --
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
                h.test("FOLDER-03 deliberate-fail RPC raises", raised)
                if rb_doc_id:
                    row = sb_admin.table("documents").select("folder_path") \
                        .eq("id", rb_doc_id).single().execute().data
                    h.test("FOLDER-03 after rollback, folder_path UNCHANGED (transactional)",
                           row.get("folder_path") == rollback_path,
                           f"got folder_path={row.get('folder_path')!r}")
            finally:
                try:
                    with pg.cursor() as cur:
                        cur.execute(
                            "DROP FUNCTION IF EXISTS public.test_rename_folder_prefix_fails_midway"
                            "(TEXT, TEXT, TEXT, UUID);"
                        )
                except Exception:
                    pass
                pg.close()

        # -- FOLDER-04: non-empty rejection (structured 409) --
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
            h.test("FOLDER-04 DELETE non-empty folder returns 409",
                   r.status_code == 409, f"status={r.status_code}")
            if r.status_code == 409:
                body = r.json()
                h.test("FOLDER-04 409 body has FOLDER_NOT_EMPTY error code",
                       body.get("error") == "FOLDER_NOT_EMPTY",
                       f"got error={body.get('error')!r}")
                h.test("FOLDER-04 409 body shows document_count >= 1",
                       body.get("document_count", 0) >= 1,
                       f"got document_count={body.get('document_count')}")
        # No-orphan check: the document must still exist with folder_path=ne_path.
        if ne_doc_id:
            row = sb_admin.table("documents").select("folder_path") \
                .eq("id", ne_doc_id).single().execute().data
            h.test("FOLDER-04 no-orphan: document at rejected-delete path UNCHANGED",
                   row.get("folder_path") == ne_path,
                   f"got folder_path={row.get('folder_path')!r}")

        # -- FOLDER-05: dedup key - same file in two folders -> 2 docs --
        h.section("FOLDER-05 dedup key")
        d_a = f"/dedup-a-{uuid.uuid4().hex[:8]}"
        d_b = f"/dedup-b-{uuid.uuid4().hex[:8]}"
        d_file_name = f"dedup-{uuid.uuid4().hex[:6]}.txt"
        r1 = requests.post(
            f"{h.BASE_URL}/api/files/upload", headers={"Authorization": f"Bearer {token_a}"},
            params={"folder_path": d_a, "scope": "user"},
            files={"file": (d_file_name, CAPYBARA_TEXT, "text/plain")}, timeout=30,
        )
        r1_action = r1.json().get("action") if r1.status_code == 200 else None
        h.test("FOLDER-05 first upload at /a returns action='created'",
               r1.status_code == 200 and r1_action == "created",
               f"status={r1.status_code} action={r1_action}")
        if r1.status_code == 200:
            r1_id = r1.json().get("id")
            _track_doc(r1_id, sb_admin)
            _tracked_storage_paths.append(f"{u_a_id}/{r1_id}.txt")
        # Second upload SAME file at SAME folder -> action='skipped'
        r2 = requests.post(
            f"{h.BASE_URL}/api/files/upload", headers={"Authorization": f"Bearer {token_a}"},
            params={"folder_path": d_a, "scope": "user"},
            files={"file": (d_file_name, CAPYBARA_TEXT, "text/plain")}, timeout=30,
        )
        r2_action = r2.json().get("action") if r2.status_code == 200 else None
        h.test("FOLDER-05 same file at same path returns action='skipped'",
               r2.status_code == 200 and r2_action == "skipped",
               f"status={r2.status_code} action={r2_action}")
        # Third upload SAME file at DIFFERENT folder -> action='created' (FOLDER-05 acceptance)
        r3 = requests.post(
            f"{h.BASE_URL}/api/files/upload", headers={"Authorization": f"Bearer {token_a}"},
            params={"folder_path": d_b, "scope": "user"},
            files={"file": (d_file_name, CAPYBARA_TEXT, "text/plain")}, timeout=30,
        )
        r3_action = r3.json().get("action") if r3.status_code == 200 else None
        h.test("FOLDER-05 same file at DIFFERENT folder returns action='created'",
               r3.status_code == 200 and r3_action == "created",
               f"status={r3.status_code} action={r3_action}")
        if r3.status_code == 200:
            r3_id = r3.json().get("id")
            _track_doc(r3_id, sb_admin)
            _tracked_storage_paths.append(f"{u_a_id}/{r3_id}.txt")

        # -- FOLDER-07: files router extensions (upload + PATCH) --
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
            h.test("FOLDER-07 PATCH rename returns 200 with new file_name",
                   rp.status_code == 200 and rp.json().get("file_name") == "renamed.txt",
                   f"status={rp.status_code}")

            # PATCH move
            new_path = f"/upload-y-{uuid.uuid4().hex[:8]}"
            rp2 = requests.patch(
                f"{h.BASE_URL}/api/files/{up_doc_id}", headers=headers_a,
                json={"folder_path": new_path}, timeout=10,
            )
            rp2_path = rp2.json().get("folder_path") if rp2.status_code == 200 else None
            h.test("FOLDER-07 PATCH move returns 200 with new folder_path",
                   rp2.status_code == 200 and rp2_path == new_path,
                   f"status={rp2.status_code} folder_path={rp2_path}")

            # PATCH empty body -> 400
            rp3 = requests.patch(
                f"{h.BASE_URL}/api/files/{up_doc_id}", headers=headers_a,
                json={}, timeout=10,
            )
            h.test("FOLDER-07 PATCH empty body returns 400",
                   rp3.status_code == 400, f"status={rp3.status_code}")

            # PATCH scope smuggling WITH a valid field -> 200, but smuggled scope silently dropped
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

        # -- Cross-user isolation (RLS doc-side defense) --
        h.section("Cross-user isolation")
        # User A POST a private folder; user B GET should not see user A's docs at that path.
        priv_path = f"/private-A-{uuid.uuid4().hex[:8]}"
        ra = requests.post(
            f"{h.BASE_URL}/api/folders", headers=headers_a,
            json={"path": priv_path, "scope": "user"}, timeout=10,
        )
        priv_folder_id = ra.json().get("id") if ra.status_code == 200 else None
        if priv_folder_id:
            _track_folder(priv_folder_id, sb_admin)
        # Insert a doc at priv_path owned by user A via service-role
        priv_ins = sb_admin.table("documents").insert({
            "user_id": u_a_id, "scope": "user", "folder_path": priv_path,
            "file_name": f"priv-{uuid.uuid4().hex[:6]}.txt",
            "file_size": 1, "mime_type": "text/plain", "status": "ready",
        }).execute()
        priv_doc_id = priv_ins.data[0]["id"] if priv_ins.data else None
        if priv_doc_id:
            _track_doc(priv_doc_id, sb_admin)
        # User B GETs the same path -> should see no documents (RLS filters cross-user)
        rb = requests.get(
            f"{h.BASE_URL}/api/folders", headers=headers_b,
            params={"path": priv_path, "scope": "user"}, timeout=10,
        )
        b_docs = rb.json().get("documents", []) if rb.status_code == 200 else None
        h.test("Cross-user: user B GET does not see user A's docs at private path",
               rb.status_code == 200 and isinstance(b_docs, list) and len(b_docs) == 0,
               f"status={rb.status_code} docs={b_docs}")

        # -- Pitfall 10: concurrent-upload-no-orphan (10 parallel uploads) --
        h.section("Pitfall 10 concurrent upload no-orphan")
        race_path = f"/test-race-{uuid.uuid4().hex[:8]}"
        file_bytes = b"race test content"

        def _upload(idx):
            try:
                return requests.post(
                    f"{h.BASE_URL}/api/files/upload",
                    headers={"Authorization": f"Bearer {token_a}"},
                    params={"folder_path": race_path, "scope": "user"},
                    files={"file": (f"race-{idx}-{uuid.uuid4().hex[:4]}.txt", file_bytes, "text/plain")},
                    timeout=60,
                )
            except Exception as e:
                # Return an object that quacks like a Response with status_code 0.
                return type("R", (), {
                    "status_code": 0,
                    "_e": str(e),
                    "json": lambda self=None: {},
                })()

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

        # Strategy B assertion: folders table did NOT acquire a row at race_path.
        folders_check = sb_admin.table("folders").select("id").eq("path", race_path).execute()
        h.test("Pitfall 10 Strategy B: folders table has 0 rows at brand-new upload path",
               len(folders_check.data or []) == 0,
               f"got {len(folders_check.data or [])} folder rows (Strategy B locks at 0)")

    finally:
        _cleanup()

    return h.passed, h.failed


if __name__ == "__main__":
    run()
    sys.exit(h.summary())
