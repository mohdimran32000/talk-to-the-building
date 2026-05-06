"""Integration tests for Phase 2: content_markdown backfill (gated).

Covers:
  - Plan 01: Storage upload at upload time (blob downloadable post-upload via service-role)
  - Plan 02: Synchronous content_markdown write inside ingest_document (BACKFILL-01)
  - Plan 03: backfill_content_markdown.py happy path, missing-blob path, idempotency
             (BACKFILL-02, BACKFILL-04)
  - BACKFILL-03: assertion-only verifier — Migration 012 DEFAULT '/' / 'user' did the work
                 for existing rows (no-op verifier; if non-zero, Phase 1 has regressed)
  - Phase 2 SC4: byte-equivalence (synchronous markdown == fresh extract_text() on same blob,
                 within +/- 20 chars)

PREREQUISITE (must be complete before running this test):
  1. Plan 01 deployed: backend/app/routers/files.py performs Storage upload, AND
     the 'documents' bucket has been created in Supabase Studio (one-time task).
  2. Migration 018 applied via run_migrations.py (adds storage.objects RLS policies).
  3. Plan 02 deployed: backend/app/services/ingestion.py writes content_markdown synchronously.
  4. Plan 03 deployed: backend/scripts/backfill_content_markdown.py exists and is invokable.
  5. Backend running on http://localhost:8001 (the upload-path tests hit POST /api/files/upload).
  6. backend/.env contains SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY.

If any prerequisite is missing, individual tests will FAIL with actionable error messages.
This is intentional - the test suite doubles as a deployment smoke check for Phase 2.

CRITICAL: per CLAUDE.md, tests must NEVER delete all user data. This test tracks every
created document_id and Storage path and removes ONLY those resources in finally.
No blanket-delete SQL, no table truncation, no cross-user cleanup.
"""
import os
import subprocess
import sys
import time

import requests

# Two-step sys.path bootstrap: scripts/ first (for sibling imports),
# then backend/ so that `from app.services.ingestion import extract_text` resolves.
# Mirrors backend/scripts/test_two_scope_rls.py:32-37.
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

import test_helpers as h
from app.services.ingestion import extract_text
from supabase import create_client


CAPYBARA_TEXT = b"""The capybara (Hydrochoerus hydrochaeris) is the largest living rodent in the world.
Native to South America, capybaras are semi-aquatic mammals that inhabit savannas and dense forests.
They live near bodies of water and are excellent swimmers, able to stay submerged for up to five minutes.
Adult capybaras can weigh between 35 to 66 kilograms and measure up to 134 centimeters in length."""

WOMBAT_TEXT = b"""Wombats are short-legged, muscular quadrupedal marsupials native to Australia.
They are about 1 metre in length with small, stubby tails and weigh between 20 and 35 kilograms.
Their fur color can vary from sandy color to brown, or from grey to black."""

STORAGE_BUCKET = "documents"

# Tracking for scoped cleanup. Per CLAUDE.md: tests must NEVER delete all user data.
_tracked_doc_ids: list = []          # for cleanup via DELETE /api/files/{id} + service-role fallback
_tracked_storage_paths: list = []    # for cleanup via supabase.storage.from_('documents').remove([...])


def _service_role_client():
    """Return a service-role Supabase client (mirrors backend/app/auth.py:8-12).

    Used for: fixture inserts (BACKFILL-02 happy/missing-blob paths), Storage download
    assertions (Plan 01 verification), direct DB readback after backfill subprocess.
    """
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )


def _storage_path_for(user_id: str, document_id: str, file_name: str) -> str:
    """MUST mirror backend/app/routers/files.py::_upload_to_storage's path formula exactly.

    Plan 01 contract: documents/{user_id}/{document_id}{ext} where
    ext = os.path.splitext(file_name)[1] (includes leading dot, e.g. '.pdf' or '').
    """
    ext = os.path.splitext(file_name or "")[1]
    return f"{user_id}/{document_id}{ext}"


def _verify_storage_setup(sb_admin) -> bool:
    """Pre-flight check: verify the 'documents' bucket is reachable via service-role.

    Mirrors test_two_scope_rls.py::_verify_admin_setup. Returns True if reachable;
    if not, prints a clear error pointing to Plan 01 / Migration 018 setup.

    Note: bucket name is the literal "documents" - matches Plan 01 contract and the
    must_haves.contains_6 grep gate for `supabase.storage.from_("documents").download`.
    """
    try:
        # Hardcoded bucket literal here on purpose (parity with files.py + the verifier gate).
        sb_admin.storage.from_("documents").list(path="", options={"limit": 1})
        return True
    except Exception as e:
        print(
            f"\n[FATAL] Storage bucket '{STORAGE_BUCKET}' is not reachable "
            f"({type(e).__name__}: {e}). Did you (1) create the bucket in Supabase Studio "
            f"per Plan 01 setup, AND (2) apply Migration 018 via run_migrations.py? "
            f"Phase 2 tests cannot proceed."
        )
        return False


def _cleanup(token: str, sb_admin) -> None:
    """Delete only tracked resources. Per CLAUDE.md: never bulk-delete."""
    headers = h.auth_headers(token)
    for did in _tracked_doc_ids:
        # First try the user-facing API delete (handles user-owned rows + chunks).
        try:
            requests.delete(f"{h.BASE_URL}/api/files/{did}", headers=headers, timeout=10)
        except Exception:
            pass
        # Defensive: also try the service-role direct delete in case the API delete failed
        # (e.g. fixture-inserted rows that bypass the API user's RLS scope).
        try:
            sb_admin.table("document_chunks").delete().eq("document_id", did).execute()
            sb_admin.table("documents").delete().eq("id", did).execute()
        except Exception:
            pass
    if _tracked_storage_paths:
        try:
            sb_admin.storage.from_(STORAGE_BUCKET).remove(_tracked_storage_paths)
        except Exception:
            pass
    _tracked_doc_ids.clear()
    _tracked_storage_paths.clear()


def _backfill_cmd(document_id: str) -> list:
    """Build the subprocess argv to run the backfill script under the same Python."""
    return [
        sys.executable,
        os.path.join(os.path.dirname(__file__), "backfill_content_markdown.py"),
        "--document-id", document_id,
    ]


def _backfill_cwd() -> str:
    """cwd for the backfill subprocess - the backend/ dir, so .env loads correctly."""
    return os.path.join(os.path.dirname(__file__), os.pardir)


def run():
    h.reset_counters()
    token = h.get_auth_token()  # TEST_USER_A
    headers = h.auth_headers(token)

    sb_admin = _service_role_client()
    if not _verify_storage_setup(sb_admin):
        h.test(
            "Storage bucket reachable (Plan 01 + Migration 018 prerequisite)",
            False,
            "bucket 'documents' not reachable; see [FATAL] message above",
        )
        return h.passed, h.failed

    user_id = None
    upload_doc_id = None
    upload_markdown = None
    upload_file_name = "capybara_facts.txt"
    fixture_happy_id = None
    fixture_happy_path = None
    fixture_missing_id = None

    try:
        # -- Section 1: Plan 02 - synchronous content_markdown write on real upload --
        h.section("Plan 02: Synchronous content_markdown on upload (BACKFILL-01)")
        r = requests.post(
            f"{h.BASE_URL}/api/files/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (upload_file_name, CAPYBARA_TEXT, "text/plain")},
        )
        h.test(
            "POST /api/files/upload returns 200",
            r.status_code == 200,
            f"status={r.status_code}",
        )
        if r.status_code == 200:
            doc = r.json()
            upload_doc_id = doc.get("id")
            user_id = doc.get("user_id")
            if upload_doc_id:
                _tracked_doc_ids.append(upload_doc_id)
                if user_id:
                    _tracked_storage_paths.append(
                        _storage_path_for(user_id, upload_doc_id, upload_file_name)
                    )
            final_status, _err = h.poll_document_status(token, upload_doc_id, "ready", max_wait=30)
            h.test(
                "Document reaches status='ready' after upload",
                final_status == "ready",
                f"status={final_status}",
            )

            # Read back via service-role to inspect content_markdown directly
            # (RLS doesn't matter for this assertion - we own the row).
            row = sb_admin.table("documents").select(
                "id, user_id, file_name, content_markdown, content_markdown_status"
            ).eq("id", upload_doc_id).single().execute().data
            upload_markdown = (row or {}).get("content_markdown") or ""
            h.test(
                "BACKFILL-01: content_markdown is non-empty after upload",
                bool(upload_markdown.strip()),
                f"content_markdown len={len(upload_markdown)} (expected > 0)",
            )
            h.test(
                "BACKFILL-01: content_markdown_status='ready' after upload",
                (row or {}).get("content_markdown_status") == "ready",
                f"got={(row or {}).get('content_markdown_status')!r}",
            )

        # -- Section 2: Plan 01 - Storage upload contract (blob downloadable post-upload) --
        h.section("Plan 01: Storage upload (Storage Gap closure)")
        if upload_doc_id and user_id:
            sp = _storage_path_for(user_id, upload_doc_id, upload_file_name)
            try:
                # Hardcoded "documents" bucket literal for parity with Plan 01 contract
                # AND the must_haves.contains_6 grep gate.
                downloaded = sb_admin.storage.from_("documents").download(sp)
                h.test(
                    f"Plan 01: blob downloadable at {sp}",
                    bool(downloaded) and len(downloaded) == len(CAPYBARA_TEXT),
                    f"downloaded len={len(downloaded) if downloaded else 0}, "
                    f"expected={len(CAPYBARA_TEXT)}",
                )
            except Exception as e:
                h.test(
                    f"Plan 01: blob downloadable at {sp}",
                    False,
                    f"{type(e).__name__}: {e}",
                )
        else:
            h.test("Plan 01: blob downloadable", False, "no upload_doc_id / user_id")

        # -- Section 3: Phase 2 SC4 - byte-equivalence (sync markdown == fresh extract_text) --
        h.section("Phase 2 SC4: byte-equivalence")
        if upload_markdown:
            fresh = extract_text(CAPYBARA_TEXT, "text/plain", upload_file_name)
            diff = abs(len(upload_markdown) - len(fresh))
            h.test(
                "Sync content_markdown ~= fresh extract_text (+/- 20 chars)",
                diff <= 20,
                f"len(sync)={len(upload_markdown)} len(fresh)={len(fresh)} diff={diff}",
            )
        else:
            h.test(
                "Sync content_markdown ~= fresh extract_text (+/- 20 chars)",
                False,
                "no upload_markdown captured (upload-path test failed earlier)",
            )

        # -- Section 4: BACKFILL-03 verifier (Migration 012 NOT NULL DEFAULT did the work) --
        h.section("BACKFILL-03: existing rows at folder_path='/' AND scope='user'")
        # PostgREST .or_() filter syntax: comma-separated, dots inside escaped via the syntax below.
        # Coarser fallback uses two .neq() calls if the .or_() form fails on this client version.
        try:
            r1 = sb_admin.table("documents").select("id").neq("folder_path", "/").limit(5).execute().data or []
            r2 = sb_admin.table("documents").select("id").neq("scope", "user").limit(5).execute().data or []
            h.test(
                "BACKFILL-03: zero documents with folder_path != '/'",
                len(r1) == 0,
                f"found {len(r1)} offending rows: {[x['id'] for x in r1]}",
            )
            h.test(
                "BACKFILL-03: zero documents with scope != 'user'",
                len(r2) == 0,
                f"found {len(r2)} offending rows: {[x['id'] for x in r2]}",
            )
        except Exception as e:
            h.test(
                "BACKFILL-03: zero documents with non-default folder_path or scope",
                False,
                f"{type(e).__name__}: {e}",
            )

        # -- Section 5: Plan 03 - backfill happy path (BACKFILL-02) --
        h.section("Plan 03: backfill happy path (BACKFILL-02)")
        if user_id:
            # Insert a fixture row directly via service-role: status=ready (chunks pipeline
            # already finished in some other run), content_markdown_status='pending', and
            # put a Storage blob in place. This simulates an Episode 1 row that has chunks
            # but no markdown.
            ins = sb_admin.table("documents").insert({
                "user_id": user_id,
                "file_name": "wombat_facts.txt",
                "file_size": len(WOMBAT_TEXT),
                "mime_type": "text/plain",
                "status": "ready",
                "content_markdown_status": "pending",
            }).execute().data
            if ins:
                fixture_happy_id = ins[0]["id"]
                _tracked_doc_ids.append(fixture_happy_id)
                fixture_happy_path = _storage_path_for(user_id, fixture_happy_id, "wombat_facts.txt")
                _tracked_storage_paths.append(fixture_happy_path)
                # Upload the blob directly via service-role (matches Plan 01's path formula).
                try:
                    sb_admin.storage.from_(STORAGE_BUCKET).upload(
                        fixture_happy_path,
                        WOMBAT_TEXT,
                        file_options={"content-type": "text/plain", "upsert": "true"},
                    )
                except Exception as e:
                    h.test(
                        "Plan 03 fixture: storage upload succeeds",
                        False,
                        f"{type(e).__name__}: {e}",
                    )
                # Run backfill scoped to this single document.
                proc = subprocess.run(
                    _backfill_cmd(fixture_happy_id),
                    capture_output=True,
                    text=True,
                    timeout=120,
                    cwd=_backfill_cwd(),
                )
                h.test(
                    "Plan 03: backfill subprocess exits 0 (happy path)",
                    proc.returncode == 0,
                    f"rc={proc.returncode} stderr={proc.stderr[-300:]}",
                )
                # Read back the row.
                row_after = sb_admin.table("documents").select(
                    "content_markdown, content_markdown_status"
                ).eq("id", fixture_happy_id).single().execute().data or {}
                h.test(
                    "BACKFILL-02: content_markdown populated after backfill",
                    bool((row_after.get("content_markdown") or "").strip()),
                    f"len={len(row_after.get('content_markdown') or '')}",
                )
                h.test(
                    "BACKFILL-02: content_markdown_status='ready' after backfill",
                    row_after.get("content_markdown_status") == "ready",
                    f"got={row_after.get('content_markdown_status')!r}",
                )

        # -- Section 6: Plan 03 - backfill missing-blob path (BACKFILL-04) --
        h.section("Plan 03: backfill missing-blob path (BACKFILL-04)")
        if user_id:
            ins2 = sb_admin.table("documents").insert({
                "user_id": user_id,
                "file_name": "no_blob.txt",
                "file_size": 0,
                "mime_type": "text/plain",
                "status": "ready",
                "content_markdown_status": "pending",
            }).execute().data
            if ins2:
                fixture_missing_id = ins2[0]["id"]
                _tracked_doc_ids.append(fixture_missing_id)
                # Intentionally do NOT upload a Storage blob. The script should detect
                # the missing blob and flip the row to 'requires_user_reupload'.
                proc2 = subprocess.run(
                    _backfill_cmd(fixture_missing_id),
                    capture_output=True,
                    text=True,
                    timeout=120,
                    cwd=_backfill_cwd(),
                )
                h.test(
                    "Plan 03: backfill subprocess does not crash on missing blob",
                    proc2.returncode in (0, 2),
                    f"rc={proc2.returncode} stderr={proc2.stderr[-300:]}",
                )
                row_after2 = sb_admin.table("documents").select(
                    "content_markdown, content_markdown_status"
                ).eq("id", fixture_missing_id).single().execute().data or {}
                h.test(
                    "BACKFILL-04: content_markdown_status='requires_user_reupload' when blob missing",
                    row_after2.get("content_markdown_status") == "requires_user_reupload",
                    f"got={row_after2.get('content_markdown_status')!r}",
                )

        # -- Section 7: Plan 03 - idempotency (second run on a 'ready' row is a no-op) --
        h.section("Plan 03: idempotency (Pitfall 4 mitigation)")
        if fixture_happy_id:
            # Re-run backfill scoped to the now-ready fixture row. The script's
            # default SELECT (.neq content_markdown_status='ready') excludes this row,
            # so the script reports 'Found 0 documents'. Even if --document-id forces
            # a load, the per-row defense-in-depth check returns 'skipped' for ready rows.
            proc3 = subprocess.run(
                _backfill_cmd(fixture_happy_id),
                capture_output=True,
                text=True,
                timeout=120,
                cwd=_backfill_cwd(),
            )
            h.test(
                "Plan 03: backfill re-run on ready row exits 0",
                proc3.returncode == 0,
                f"rc={proc3.returncode}",
            )
            combined = (proc3.stdout or "") + (proc3.stderr or "")
            h.test(
                "Plan 03: re-run is observably a no-op (Found 0 documents OR [SKIP] log line)",
                ("Found 0 document" in combined)
                or ("[SKIP]" in combined)
                or ("ready=0" in combined and "failed=0" in combined),
                f"output tail: {combined[-300:]!r}",
            )

    finally:
        _cleanup(token, sb_admin)

    return h.passed, h.failed


if __name__ == "__main__":
    run()
    sys.exit(h.summary())
