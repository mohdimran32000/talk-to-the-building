---
phase: 02
plan: 04
type: execute
wave: 2
depends_on: [01, 02, 03]
files_modified:
  - backend/scripts/test_backfill.py
  - backend/scripts/test_all.py
autonomous: false
requirements:
  - BACKFILL-03
must_haves:
  truths:
    - "backend/scripts/test_backfill.py exists with run() entry point returning (h.passed, h.failed) per the SUITES contract"
    - "test_backfill.py covers Plan 02's synchronous-on-upload write: after upload + poll-to-ready, documents.content_markdown is non-empty AND content_markdown_status='ready' (asserts BACKFILL-01 over the wire)"
    - "test_backfill.py covers Plan 01's Storage upload: after upload, the blob is downloadable via supabase.storage.from_('documents').download(f'{user_id}/{doc_id}{ext}') using a service-role client (asserts the Storage Gap closure)"
    - "test_backfill.py covers Plan 03 backfill happy path: insert a fixture doc with content_markdown_status='pending' AND a Storage blob, run backfill via subprocess with --document-id, assert content_markdown populated AND content_markdown_status='ready' (BACKFILL-02 happy path)"
    - "test_backfill.py covers Plan 03 missing-blob path: insert a fixture doc with content_markdown_status='pending' AND NO Storage blob, run backfill with --document-id, assert content_markdown_status='requires_user_reupload' (BACKFILL-04)"
    - "test_backfill.py covers byte-equivalence (Phase 2 SC4): for the same blob, the synchronous-on-upload content_markdown equals (within ±20 chars) the result of a fresh extract_text() call on the same bytes"
    - "test_backfill.py covers idempotency: running backfill a second time on a row that is already 'ready' is a no-op (the row is NOT included in the .neq scan and the per-row defense-in-depth check returns 'skipped' if pointed at directly)"
    - "test_backfill.py covers BACKFILL-03 verification: SELECT COUNT(*) FROM documents WHERE folder_path != '/' OR scope != 'user' returns 0 for all pre-Phase-2 rows (the migration-DEFAULT path; Phase 1 / Migration 012 already did the work — this test asserts the no-op verifier)"
    - "test_backfill.py uses anon-key + JWT for the upload-path tests (real user JWT — RLS applies to UI-style uploads) AND service-role for the fixture-insert / Storage-download / direct-DB-readback paths (matches the test_two_scope_rls.py Phase 1 / Plan 08 convention)"
    - "test_backfill.py tracks every created document_id and storage_path; cleanup in finally removes ONLY tracked resources (CLAUDE.md mandatory rule: no blanket DELETE FROM, no TRUNCATE)"
    - "test_backfill.py is registered in test_all.py SUITES list as ('Backfill', test_backfill) immediately after the existing ('Files', test_files) entry — adjacent to its closest analog suite"
    - "test_backfill.py has at least 6 distinct h.test() assertions covering the 6 truths above (sync write OK, blob downloadable, backfill happy path, backfill missing-blob, byte-equivalence, idempotency, BACKFILL-03 verifier)"
  artifacts:
    - path: "backend/scripts/test_backfill.py"
      provides: "Integration tests for Plan 01 (Storage upload), Plan 02 (synchronous content_markdown write + failure surfacing), Plan 03 (backfill happy path + missing-blob + idempotency + --dry-run), and BACKFILL-03 verification (folder_path/scope DEFAULT migration is a no-op for existing rows)"
      exports: ["run"]
      contains: "def run()"
      contains_2: "import subprocess"
      contains_3: "test_backfill"
      contains_4: "content_markdown_status"
      contains_5: "requires_user_reupload"
      contains_6: "supabase.storage.from_(\"documents\").download"
      min_lines: 250
    - path: "backend/scripts/test_all.py"
      provides: "Backfill suite registration in the full sweep"
      contains: "import test_backfill"
      contains_2: "(\"Backfill\", test_backfill)"
  key_links:
    - from: "backend/scripts/test_backfill.py"
      to: "Plan 01 _upload_to_storage + Plan 02 sync write + Plan 03 backfill script"
      via: "Real upload → poll-ready → Storage download → fixture-insert → subprocess backfill → DB readback"
      pattern: "subprocess.run.*backfill_content_markdown"
    - from: "backend/scripts/test_backfill.py BACKFILL-03 verifier"
      to: "Phase 1 / Migration 012 (folder_path NOT NULL DEFAULT '/', scope NOT NULL DEFAULT 'user')"
      via: "SELECT COUNT(*) FROM documents WHERE folder_path != '/' OR scope != 'user' — assertion-only verifier"
      pattern: "folder_path.*scope"
    - from: "backend/scripts/test_all.py SUITES registration"
      to: "Full-suite regression test (cd backend && venv/Scripts/python scripts/test_all.py)"
      via: "Backfill suite runs alongside the existing 13 suites"
      pattern: "(\"Backfill\", test_backfill)"
---

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Test fixture inserts (service-role) -> documents table | Service-role bypasses RLS; tests must scope INSERTs to a known fixture user_id (TEST_USER_A) and tracked document_ids only (CLAUDE.md cleanup rule) |
| subprocess.run(backfill script) -> live Supabase | The test invokes the production backfill script as a subprocess; the script's own --document-id flag scopes the invocation to the fixture row, preventing collateral writes |
| Storage uploads/downloads -> bucket 'documents' | Tests upload real bytes to a real bucket; cleanup MUST remove tracked storage paths in finally to avoid bucket litter |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-2-13 | Tampering / Data Loss | Test cleanup logic | mitigate | Per CLAUDE.md "CRITICAL: Tests must NEVER delete all user data": every created document_id and storage_path is tracked in module-level lists. The `finally` block iterates ONLY those IDs and DELETEs each individually via the test's authenticated client (or service-role for fixture-inserted rows). NEVER `DELETE FROM documents` without WHERE. NEVER `TRUNCATE`. Mirrors the Phase 1 / Plan 08 (`test_two_scope_rls.py:39-77`) cleanup pattern exactly. |
| T-2-14 | Information Disclosure / Test integrity | RLS bypass via service-role | mitigate | Tests use TWO clients per the Phase 1 convention: anon-key + JWT (`h.get_user_supabase_client(token)`) for "as-a-user" assertions, and service-role (constructed inline matching `auth.py:8-12`) for fixture-insert / Storage-download / cross-user setup-and-teardown. The choice is documented per assertion so reviewers can audit which boundary is being tested. |
| T-2-15 | Denial of Service | Long Docling runs blocking the test suite | mitigate | Test fixtures use small text-only blobs (the existing CAPYBARA_TEXT pattern from `test_files.py:11-17`). No PDFs, no OCR, no PowerPoint. Docling on plain text is sub-second. Per-test timeout is the existing 30-second `h.poll_document_status` budget. The backfill subprocess invocation uses `--document-id <fixture_id>` to scope work to a single row (worst-case 1 sub-second Docling pass). |
| T-2-16 | Operational | Test depends on Plan 01's bucket existing in Studio + Migration 018 RLS applied | accept | Test will fail with a clear error if the bucket is missing or service-role can't access it (catches Plan 01 deployment regression). This is an intentional integration check — the test serves as a deployment smoke test for the Storage piece. The test docstring documents the prerequisite. |
</threat_model>

<objective>
Build the Phase 2 verification suite: `backend/scripts/test_backfill.py` covering Plan 01 (Storage upload at upload time), Plan 02 (synchronous content_markdown write inside ingest_document — BACKFILL-01), Plan 03 (backfill script happy path + missing-blob path + idempotency — BACKFILL-02 + BACKFILL-04), AND BACKFILL-03 (assertion-only verifier that Migration 012's `NOT NULL DEFAULT '/'/'user'` did its job for existing rows). Register the suite in `test_all.py`. Tests use the existing `test_helpers` infrastructure, follow the Phase 1 / Plan 08 cleanup pattern (track-then-delete, never blanket delete), and produce 6+ distinct `h.test()` assertions covering the six truths in `must_haves.truths`.

This plan is `autonomous: false` because Task 2 includes a `checkpoint:human-verify` gate that requires the operator to confirm Plan 01's Supabase Studio bucket creation AND Migration 018 application BEFORE running the test (otherwise every test that touches Storage will fail in a non-actionable way).
</objective>

<execution_context>
@.claude/get-shit-done/workflows/execute-plan.md
@.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md

@.planning/phases/02-content-markdown-backfill-gated/02-CONTEXT.md
@.planning/phases/02-content-markdown-backfill-gated/02-RESEARCH.md
@.planning/phases/02-content-markdown-backfill-gated/02-PATTERNS.md
@.planning/REQUIREMENTS.md
@.planning/codebase/TESTING.md
@CLAUDE.md

@backend/scripts/test_helpers.py
@backend/scripts/test_files.py
@backend/scripts/test_two_scope_rls.py
@backend/scripts/test_all.py
@backend/app/services/ingestion.py

@.planning/phases/02-content-markdown-backfill-gated/02-01-PLAN.md
@.planning/phases/02-content-markdown-backfill-gated/02-02-PLAN.md
@.planning/phases/02-content-markdown-backfill-gated/02-03-PLAN.md

<interfaces>
<!-- The contracts this test asserts against. -->

Plan 01 contract (Storage upload formula):
  documents/{user_id}/{document_id}{ext}, ext = os.path.splitext(file_name)[1]

Plan 02 contract (synchronous content_markdown):
  After successful upload + status='ready':
    documents.content_markdown IS NOT NULL AND len(content_markdown) > 0
    documents.content_markdown_status = 'ready'

Plan 03 contract (backfill script):
  CLI: cd backend && venv/Scripts/python scripts/backfill_content_markdown.py --document-id <UUID>
  Pre: row has content_markdown_status='pending', Storage blob present
  Post: content_markdown populated, content_markdown_status='ready'
  Pre: row has content_markdown_status='pending', Storage blob ABSENT
  Post: content_markdown_status='requires_user_reupload' (no exception escaped subprocess)

BACKFILL-03 (assertion-only verifier):
  SELECT COUNT(*) FROM documents WHERE folder_path != '/' OR scope != 'user'
    -- For pre-Phase-2 rows this should be 0 (Migration 012 DEFAULT did the work).
    -- Test asserts the count is 0; if it is non-zero, Phase 1 / Migration 012 has regressed.

Phase 2 success criterion 4 (byte-equivalence):
  For the same blob bytes:
    content_markdown_from_upload = (read from documents row after sync upload)
    content_markdown_fresh       = extract_text(blob, mime_type, file_name)
    abs(len(content_markdown_from_upload) - len(content_markdown_fresh)) <= 20
    -- Mathematically equal if Docling version is pinned (Plan 02 docling==2.91.0)
    -- and call options are identical (extract_text uses no kwargs on export_to_markdown).

Test_helpers fixtures (already in place from Phase 1 / Plan 08):
  TEST_USER_A         -> regular user (no admin)
  TEST_USER_B         -> regular user (cross-user testing)
  TEST_USER_ADMIN     -> admin user (not used by Phase 2 unless cross-user backfill is asserted)
  get_auth_token()     -> JWT for TEST_USER_A (or specified user)
  get_user_supabase_client(jwt) -> anon-key + JWT (RLS applies)
  poll_document_status(token, doc_id, target='ready', max_wait=30)
  track_file(file_id) / cleanup_files(token) -> existing scoped-cleanup helpers
</interfaces>
</context>

<tasks>

<task id="2-04-01" type="auto">
  <name>Task 1: Write test_backfill.py — integration tests for Plans 01/02/03 + BACKFILL-03 verifier</name>
  <files>backend/scripts/test_backfill.py</files>
  <read_first>
    - backend/scripts/test_files.py (PRIMARY analog — full structure: docstring, imports, sys.path bootstrap for test_helpers, run() returning (h.passed, h.failed), `try / finally` with conditional cleanup, the `h.section()` / `h.test()` / `h.poll_document_status()` patterns)
    - backend/scripts/test_two_scope_rls.py L1-90 (SECONDARY analog for direct-Supabase + scoped-cleanup pattern: module-level `_tracked_documents` list, `_track_doc()`, `_cleanup()` helpers; sys.path two-step bootstrap to import from `app.*`; the `_verify_admin_setup` pattern is the analog for this test's `_verify_storage_setup` pre-flight check)
    - backend/scripts/test_helpers.py (FULL FILE — `BASE_URL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `TEST_USER_A`, `get_auth_token`, `auth_headers`, `get_user_supabase_client`, `poll_document_status`, `test`, `section`, `reset_counters`, `summary` are all reused; this test does NOT need to add new helpers)
    - backend/scripts/test_all.py (SUITES list — Task 2 adds the new suite here; Task 1 only needs to make sure run() returns the (passed, failed) tuple shape that the runner expects)
    - backend/app/services/ingestion.py L62-142 (`extract_text` signature — this test imports it for the byte-equivalence assertion, just like `backfill_content_markdown.py` does)
    - backend/app/auth.py L8-12 (service-role client construction — this test inlines the same shape for the fixture-insert + Storage-download + DB-readback paths)
    - .planning/phases/02-content-markdown-backfill-gated/02-CONTEXT.md ALL §LOCKED sections (defines what must be true after the plan executes — every assertion in this test maps to a §LOCKED contract)
    - .planning/phases/02-content-markdown-backfill-gated/02-PATTERNS.md §"backend/scripts/test_backfill.py" (lines ~184-292 — paste-ready test runner shape, scoped-cleanup pattern, required test cases table — this is the AUTHORITATIVE list of tests to write)
    - .planning/phases/02-content-markdown-backfill-gated/02-RESEARCH.md §Validation Architecture (the falsifiable assertions this test makes) AND §Pitfall 1 (chunk-stitching evidence — the byte-equivalence test catches this empirically)
    - .planning/phases/02-content-markdown-backfill-gated/02-01-PLAN.md interfaces (the storage_path formula this test must compute identically)
    - .planning/phases/02-content-markdown-backfill-gated/02-02-PLAN.md interfaces (the UPDATE shapes this test asserts against)
    - .planning/phases/02-content-markdown-backfill-gated/02-03-PLAN.md interfaces (the backfill CLI surface + log line format this test invokes)
    - .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/08-PLAN.md (Phase 1 / Plan 08 — the structural quality bar; this test should match its docstring + cleanup discipline)
    - CLAUDE.md ("Tests must NEVER delete all user data"; "Python backend uses venv"; "Backend validation suite ... 112 tests; covers ... file upload/ingestion ...")
  </read_first>
  <action>
    Create `backend/scripts/test_backfill.py` with the structure below. The test produces 6 named sections matching the 6 must_haves.truths, with a total of 8+ `h.test()` assertions (some sections have multiple checks). All cleanup is per-tracked-id; no blanket deletes.

    Module shape:

```python
"""Integration tests for Phase 2: content_markdown backfill (gated).

Covers:
  - Plan 01: Storage upload at upload time (blob downloadable post-upload via service-role)
  - Plan 02: Synchronous content_markdown write inside ingest_document (BACKFILL-01)
  - Plan 03: backfill_content_markdown.py happy path, missing-blob path, idempotency (BACKFILL-02, BACKFILL-04)
  - BACKFILL-03: assertion-only verifier — Migration 012 DEFAULT '/' / 'user' did the work for existing rows
  - Phase 2 SC4: byte-equivalence (synchronous markdown == fresh extract_text() on same blob, ±20 chars)

PREREQUISITE (must be complete before running this test):
  1. Plan 01 deployed: backend/app/routers/files.py performs Storage upload, AND
     the 'documents' bucket has been created in Supabase Studio (one-time task).
  2. Migration 018 applied via run_migrations.py (adds storage.objects RLS policies).
  3. Plan 02 deployed: backend/app/services/ingestion.py writes content_markdown synchronously.
  4. Plan 03 deployed: backend/scripts/backfill_content_markdown.py exists and is invokable.
  5. Backend running on http://localhost:8001 (the upload-path tests hit POST /api/files/upload).
  6. backend/.env contains SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY.

If any prerequisite is missing, individual tests will FAIL with actionable error messages.
This is intentional — the test suite doubles as a deployment smoke check for Phase 2.

CRITICAL: per CLAUDE.md, tests must NEVER delete all user data. This test tracks every
created document_id and Storage path and removes ONLY those resources in finally.
No blanket DELETE FROM, no TRUNCATE, no cross-user cleanup.
"""
import os
import subprocess
import sys
import time

import requests

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
_tracked_doc_ids: list[str] = []   # for cleanup via DELETE /api/files/{id}
_tracked_storage_paths: list[str] = []   # for cleanup via supabase.storage.from_('documents').remove([...])


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
    """MUST mirror backend/app/routers/files.py::_upload_to_storage's path formula exactly."""
    ext = os.path.splitext(file_name or "")[1]
    return f"{user_id}/{document_id}{ext}"


def _verify_storage_setup(sb_admin) -> bool:
    """Pre-flight check: verify the 'documents' bucket is reachable via service-role.

    Mirrors test_two_scope_rls.py::_verify_admin_setup. Returns True if reachable;
    if not, prints a clear error pointing to Plan 01 / Migration 018 setup.
    """
    try:
        sb_admin.storage.from_(STORAGE_BUCKET).list(path="", options={"limit": 1})
        return True
    except Exception as e:
        print(
            f"\n[FATAL] Storage bucket '{STORAGE_BUCKET}' is not reachable ({type(e).__name__}: {e}). "
            f"Did you (1) create the bucket in Supabase Studio per Plan 01 setup, AND "
            f"(2) apply Migration 018 via run_migrations.py? Phase 2 tests cannot proceed."
        )
        return False


def _cleanup(token: str, sb_admin) -> None:
    """Delete only tracked resources. Per CLAUDE.md: never bulk-delete."""
    headers = h.auth_headers(token)
    for did in _tracked_doc_ids:
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


def run():
    h.reset_counters()
    token = h.get_auth_token()  # TEST_USER_A
    headers = h.auth_headers(token)

    sb_admin = _service_role_client()
    if not _verify_storage_setup(sb_admin):
        h.test("Storage bucket reachable (Plan 01 + Migration 018 prerequisite)", False,
               "bucket 'documents' not reachable; see [FATAL] message above")
        return h.passed, h.failed

    user_id = None
    upload_doc_id = None
    upload_markdown = None
    upload_file_name = "capybara_facts.txt"
    fixture_happy_id = None
    fixture_happy_path = None
    fixture_missing_id = None

    try:
        # ── Section 1: Plan 02 — synchronous content_markdown write on real upload ──
        h.section("Plan 02: Synchronous content_markdown on upload (BACKFILL-01)")
        r = requests.post(
            f"{h.BASE_URL}/api/files/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (upload_file_name, CAPYBARA_TEXT, "text/plain")},
        )
        h.test("POST /api/files/upload returns 200", r.status_code == 200, f"status={r.status_code}")
        if r.status_code == 200:
            doc = r.json()
            upload_doc_id = doc.get("id")
            user_id = doc.get("user_id")
            if upload_doc_id:
                _tracked_doc_ids.append(upload_doc_id)
                _tracked_storage_paths.append(_storage_path_for(user_id, upload_doc_id, upload_file_name))
            final_status, _err = h.poll_document_status(token, upload_doc_id, "ready", max_wait=30)
            h.test("Document reaches status='ready' after upload", final_status == "ready", f"status={final_status}")

            # Read back via service-role to inspect content_markdown directly (RLS doesn't matter for this assertion)
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

        # ── Section 2: Plan 01 — Storage upload contract (blob downloadable post-upload) ──
        h.section("Plan 01: Storage upload (Storage Gap closure)")
        if upload_doc_id and user_id:
            sp = _storage_path_for(user_id, upload_doc_id, upload_file_name)
            try:
                downloaded = sb_admin.storage.from_(STORAGE_BUCKET).download(sp)
                h.test(
                    f"Plan 01: blob downloadable at {sp}",
                    bool(downloaded) and len(downloaded) == len(CAPYBARA_TEXT),
                    f"downloaded len={len(downloaded) if downloaded else 0}, expected={len(CAPYBARA_TEXT)}",
                )
            except Exception as e:
                h.test(f"Plan 01: blob downloadable at {sp}", False,
                       f"{type(e).__name__}: {e}")
        else:
            h.test("Plan 01: blob downloadable", False, "no upload_doc_id / user_id")

        # ── Section 3: Phase 2 SC4 — byte-equivalence (sync markdown == fresh extract_text) ──
        h.section("Phase 2 SC4: byte-equivalence")
        if upload_markdown:
            fresh = extract_text(CAPYBARA_TEXT, "text/plain", upload_file_name)
            diff = abs(len(upload_markdown) - len(fresh))
            h.test(
                "Sync content_markdown ≈ fresh extract_text (±20 chars)",
                diff <= 20,
                f"len(sync)={len(upload_markdown)} len(fresh)={len(fresh)} diff={diff}",
            )

        # ── Section 4: BACKFILL-03 verifier (Migration 012 NOT NULL DEFAULT did the work) ──
        h.section("BACKFILL-03: existing rows at folder_path='/' AND scope='user'")
        try:
            row_offending = sb_admin.table("documents").select("id").or_(
                "folder_path.neq./,scope.neq.user"
            ).limit(5).execute().data or []
            h.test(
                "BACKFILL-03: zero documents with non-default folder_path or scope",
                len(row_offending) == 0,
                f"found {len(row_offending)} offending row(s) — Migration 012 DEFAULT may have regressed",
            )
        except Exception as e:
            # Postgrest filter syntax can vary; fall back to a server-side function or a coarser check.
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

        # ── Section 5: Plan 03 — backfill happy path (BACKFILL-02) ──
        h.section("Plan 03: backfill happy path (BACKFILL-02)")
        if user_id:
            # Insert a fixture row directly via service-role: status=ready (chunks pipeline already done in some other run),
            # content_markdown_status='pending', and put a Storage blob in place.
            ins = sb_admin.table("documents").insert({
                "user_id": user_id,
                "file_name": "wombat_facts.txt",
                "file_size": len(WOMBAT_TEXT),
                "mime_type": "text/plain",
                "status": "ready",                       # pretend chunks pipeline already finished
                "content_markdown_status": "pending",    # but markdown was not captured (Episode 1 row simulation)
            }).execute().data
            if ins:
                fixture_happy_id = ins[0]["id"]
                _tracked_doc_ids.append(fixture_happy_id)
                fixture_happy_path = _storage_path_for(user_id, fixture_happy_id, "wombat_facts.txt")
                _tracked_storage_paths.append(fixture_happy_path)
                # Upload the blob directly via service-role.
                sb_admin.storage.from_(STORAGE_BUCKET).upload(
                    fixture_happy_path, WOMBAT_TEXT,
                    file_options={"content-type": "text/plain", "upsert": "true"},
                )
                # Run backfill scoped to this single document.
                proc = subprocess.run(
                    [sys.executable, os.path.join(os.path.dirname(__file__), "backfill_content_markdown.py"),
                     "--document-id", fixture_happy_id],
                    capture_output=True, text=True, timeout=120,
                    cwd=os.path.join(os.path.dirname(__file__), os.pardir),
                )
                h.test("Plan 03: backfill subprocess exits 0 (happy path)",
                       proc.returncode == 0,
                       f"rc={proc.returncode} stderr={proc.stderr[-300:]}")
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

        # ── Section 6: Plan 03 — backfill missing-blob path (BACKFILL-04) ──
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
                # Intentionally do NOT upload a Storage blob.
                proc2 = subprocess.run(
                    [sys.executable, os.path.join(os.path.dirname(__file__), "backfill_content_markdown.py"),
                     "--document-id", fixture_missing_id],
                    capture_output=True, text=True, timeout=120,
                    cwd=os.path.join(os.path.dirname(__file__), os.pardir),
                )
                h.test("Plan 03: backfill subprocess does not crash on missing blob",
                       proc2.returncode in (0, 2),
                       f"rc={proc2.returncode} stderr={proc2.stderr[-300:]}")
                row_after2 = sb_admin.table("documents").select(
                    "content_markdown, content_markdown_status"
                ).eq("id", fixture_missing_id).single().execute().data or {}
                h.test(
                    "BACKFILL-04: content_markdown_status='requires_user_reupload' when blob missing",
                    row_after2.get("content_markdown_status") == "requires_user_reupload",
                    f"got={row_after2.get('content_markdown_status')!r}",
                )

        # ── Section 7: Plan 03 — idempotency (second run on a 'ready' row is a no-op) ──
        h.section("Plan 03: idempotency (Pitfall 4 mitigation)")
        if fixture_happy_id:
            # Re-run backfill scoped to the now-ready fixture row. The script's defense-in-depth
            # check should report 'skipped' for this row.
            proc3 = subprocess.run(
                [sys.executable, os.path.join(os.path.dirname(__file__), "backfill_content_markdown.py"),
                 "--document-id", fixture_happy_id],
                capture_output=True, text=True, timeout=120,
                cwd=os.path.join(os.path.dirname(__file__), os.pardir),
            )
            h.test("Plan 03: backfill re-run on ready row exits 0",
                   proc3.returncode == 0,
                   f"rc={proc3.returncode}")
            # The default SELECT (.neq content_markdown_status='ready') excludes this row, so the
            # script reports 'Found 0 documents' OR processes it as 'skipped' via --document-id override.
            combined = (proc3.stdout or "") + (proc3.stderr or "")
            h.test(
                "Plan 03: re-run is observably a no-op (Found 0 documents OR [SKIP] log line)",
                ("Found 0 document" in combined) or ("[SKIP]" in combined) or ("ready=0" in combined and "failed=0" in combined),
                f"output tail: {combined[-300:]!r}",
            )

    finally:
        _cleanup(token, sb_admin)

    return h.passed, h.failed


if __name__ == "__main__":
    run()
    sys.exit(h.summary())
```

    Conventions to honor (per PATTERNS.md and the Phase 1 / Plan 08 quality bar):
    - Module docstring on lines 1-N: triple-quoted, lists prerequisites, documents the CLAUDE.md cleanup discipline.
    - Imports: stdlib (os, subprocess, sys, time), third-party (requests), then `import test_helpers as h` after sys.path bootstrap, then `from app.services.ingestion import extract_text` and `from supabase import create_client`.
    - Two `sys.path.insert(0, ...)` calls: scripts/ first, backend/ second (so `app.services.X` resolves) — matches `test_two_scope_rls.py:32-37`.
    - Use the existing `h.section()` / `h.test()` / `h.poll_document_status()` / `h.auth_headers()` / `h.get_auth_token()` helpers — do NOT reimplement.
    - Tracking lists are MODULE-LEVEL (`_tracked_doc_ids`, `_tracked_storage_paths`) and emptied at the end of `_cleanup()`.
    - `run()` returns `(h.passed, h.failed)` so `test_all.py:54` can sum it (matches the Files / Tools / Sub-Agents pattern).
    - Service-role client is constructed inline via `_service_role_client()` (matches `auth.py:8-12`); does NOT import from `app.auth`.
    - Subprocess invocations use `[sys.executable, "scripts/backfill_content_markdown.py", "--document-id", <id>]` so the test runs the script under the same venv Python that the test itself runs under.
    - The `--document-id` flag scopes the backfill to a single fixture row, preventing collateral writes to unrelated documents that may exist in the dev DB.
    - The byte-equivalence assertion uses `extract_text(CAPYBARA_TEXT, "text/plain", upload_file_name)` (the CAPYBARA blob is plain text, so Docling is a passthrough → identical output to the synchronous-on-upload path → diff is 0 in practice).
    - Pre-flight `_verify_storage_setup()` runs BEFORE any test — if it fails, the suite emits one FAIL `h.test` with an actionable message and returns early.

    Do NOT:
    - Use `DELETE FROM` without a WHERE clause (CLAUDE.md mandatory rule).
    - `TRUNCATE` any table.
    - Delete documents that this test did not create (no cross-user / cross-test cleanup).
    - Add LangChain / LangGraph (project rule).
    - Add LangSmith tracing (out of scope per CONTEXT.md — applies to backfill script; this test inherits the discipline).
    - Spin up the FastAPI backend yourself — assume it is running on http://localhost:8001 per the existing test convention (`test_files.py` etc.).
    - Use PDF/PPTX/OCR fixtures (would make the test multi-second; plain text is sufficient and Docling-passthrough makes byte-equivalence trivially true).
    - Test the `--purge-orphans` flag end-to-end (its DELETE path is destructive; the unit-style verification of the interactive ritual is left to manual testing per the test's docstring — Task 2's checkpoint can confirm operator visibility).
    - Test cross-user backfill (this is operator-driven and not a Phase 2 success criterion; defer to Phase 4 testing).
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -c "import ast, pathlib; src = pathlib.Path('scripts/test_backfill.py').read_text(encoding='utf-8'); ast.parse(src); body = '\n'.join(line for line in src.splitlines() if not line.lstrip().startswith('#')); assert 'def run()' in body, 'run() missing'; assert 'return h.passed, h.failed' in body, 'run() must return (passed, failed)'; assert 'sys.exit(h.summary())' in body, 'main block missing'; assert 'import subprocess' in body, 'subprocess import missing'; assert 'from app.services.ingestion import extract_text' in body, 'extract_text import missing (byte-equivalence test)'; assert body.count('h.test(') &gt;= 8, f'expected at least 8 h.test() assertions, got {body.count(chr(34)+\"h.test(\"+chr(34)) if False else body.count(\"h.test(\")}'; assert 'requires_user_reupload' in body, 'BACKFILL-04 path not asserted'; assert 'BACKFILL-01' in body, 'BACKFILL-01 not labeled'; assert 'BACKFILL-02' in body, 'BACKFILL-02 not labeled'; assert 'BACKFILL-03' in body, 'BACKFILL-03 not labeled'; assert 'BACKFILL-04' in body, 'BACKFILL-04 not labeled'; assert 'storage.from_(\"documents\").download' in body or 'storage.from_(\"documents\").list' in body, 'Storage SDK call missing'; assert '_tracked_doc_ids' in body and '_tracked_storage_paths' in body, 'cleanup tracking lists missing'; assert 'def _cleanup' in body, '_cleanup helper missing'; assert 'DELETE FROM' not in body.upper(), 'blanket DELETE FROM forbidden (CLAUDE.md)'; assert 'TRUNCATE' not in body.upper(), 'TRUNCATE forbidden (CLAUDE.md)'; assert 'os.path.splitext' in body, 'storage path formula missing splitext'; print('test_backfill structure OK')"</automated>
  </verify>
  <acceptance_criteria>
    - File `backend/scripts/test_backfill.py` exists.
    - File parses as valid Python (`ast.parse` succeeds).
    - `grep -c "def run()" backend/scripts/test_backfill.py` returns 1.
    - `grep -c "return h.passed, h.failed" backend/scripts/test_backfill.py` returns 1.
    - `grep -c "sys.exit(h.summary())" backend/scripts/test_backfill.py` returns 1.
    - `grep -c "import subprocess" backend/scripts/test_backfill.py` returns 1.
    - `grep -c "from app.services.ingestion import extract_text" backend/scripts/test_backfill.py` returns 1.
    - `grep -c "h.test(" backend/scripts/test_backfill.py` returns at least 8.
    - `grep -c "h.section(" backend/scripts/test_backfill.py` returns at least 6 (one per logical section per must_haves).
    - `grep -c "BACKFILL-01" backend/scripts/test_backfill.py` returns at least 1.
    - `grep -c "BACKFILL-02" backend/scripts/test_backfill.py` returns at least 1.
    - `grep -c "BACKFILL-03" backend/scripts/test_backfill.py` returns at least 1.
    - `grep -c "BACKFILL-04" backend/scripts/test_backfill.py` returns at least 1.
    - `grep -c "requires_user_reupload" backend/scripts/test_backfill.py` returns at least 2 (status assertion + sentence reference).
    - `grep -c "storage.from_(\"documents\")" backend/scripts/test_backfill.py` returns at least 2 (list/canary + download/upload).
    - `grep -c "_tracked_doc_ids" backend/scripts/test_backfill.py` returns at least 3 (def + .append + .clear).
    - `grep -c "_tracked_storage_paths" backend/scripts/test_backfill.py` returns at least 3.
    - `grep -c "def _cleanup" backend/scripts/test_backfill.py` returns 1.
    - `grep -iE "DELETE FROM|TRUNCATE" backend/scripts/test_backfill.py` returns no matches (CLAUDE.md mandatory rule).
    - `grep -E "from\s+(langchain\|langgraph)" backend/scripts/test_backfill.py` returns no matches (project rule).
    - `grep -E "@traceable|langsmith" backend/scripts/test_backfill.py` returns no matches (out of scope).
    - `grep -c "os.path.splitext" backend/scripts/test_backfill.py` returns at least 1 (storage path formula MUST match Plan 01).
    - `cd backend && venv/Scripts/python -c "import sys, os; sys.path.insert(0, 'scripts'); sys.path.insert(0, '.'); import test_backfill; assert hasattr(test_backfill, 'run'), 'run() not exported'; assert callable(test_backfill.run); print('test_backfill imports OK')"` prints "test_backfill imports OK".
  </acceptance_criteria>
  <done>
    `backend/scripts/test_backfill.py` exists with 6 sections covering the 6 must_haves truths, 8+ `h.test()` assertions, scoped cleanup of tracked document_ids + storage_paths, no blanket DELETE / TRUNCATE, byte-equivalence assertion using `extract_text`, BACKFILL-03 verifier asserting Migration 012's NOT NULL DEFAULT did its job. Module imports cleanly via venv Python. Pre-flight `_verify_storage_setup()` produces an actionable error if Plan 01 / Migration 018 setup is missing.
  </done>
</task>

<task id="2-04-02" type="auto">
  <name>Task 2: Register test_backfill in test_all.py SUITES list (after the Files entry)</name>
  <files>backend/scripts/test_all.py</files>
  <read_first>
    - backend/scripts/test_all.py FULL FILE (the SUITES list at L26-40 is the only thing this task modifies; the runner's `for name, module in SUITES` loop at L49 consumes the list directly; the existing import block at L11-24 is the place to add `import test_backfill`)
    - .planning/phases/02-content-markdown-backfill-gated/02-PATTERNS.md §"Register in test_all.py" (lines ~285-292 — paste-ready snippet showing `("Backfill", test_backfill)` placement convention)
    - .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/08-PLAN.md (Phase 1 / Plan 08 — the `("Two-Scope RLS", test_two_scope_rls)` registration is the most-recent precedent for this convention; the Backfill suite belongs after the Files suite because that's its closest semantic neighbor)
    - CLAUDE.md ("Backend validation suite: cd backend && venv/Scripts/python scripts/test_all.py (112 tests) ... When building new features: ... Update backend/scripts/test_all.py if adding a new module")
  </read_first>
  <action>
    Modify `backend/scripts/test_all.py` to register the new Backfill suite.

    Step 1: In the import block (currently L11-24), add `import test_backfill` AFTER the existing `import test_files` line (currently L16). Place it adjacent to its closest analog (Files) so the import order matches the SUITES order. The new import block becomes:

    ```python
    import test_helpers as h
    import test_health
    import test_auth
    import test_threads
    import test_messages
    import test_files
    import test_backfill          # NEW (Phase 2)
    import test_rag
    import test_rls
    import test_two_scope_rls
    import test_settings
    import test_metadata
    import test_hybrid
    import test_tools
    import test_sub_agents
    ```

    Step 2: In the SUITES list (currently L26-40), add `("Backfill", test_backfill)` immediately after the `("Files", test_files)` entry. The new SUITES list becomes:

    ```python
    SUITES = [
        ("Health", test_health),
        ("Auth", test_auth),
        ("Threads", test_threads),
        ("Messages", test_messages),
        ("Files", test_files),
        ("Backfill", test_backfill),         # NEW (Phase 2 — content_markdown synchronous + backfill)
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
    - Suite name `"Backfill"` — title case, single word — matches the existing `"Health"` / `"Auth"` / `"Files"` style; matches PATTERNS.md §"Register in test_all.py" recommendation.
    - Inline comment `# NEW (Phase 2 — content_markdown synchronous + backfill)` documents the addition for reviewers.
    - No other lines modified — do NOT touch the `main()` function, the `clear_token_cache()` call, or the summary print logic.
    - Do NOT renumber or reorder any other suite entries.
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -c "src = open('scripts/test_all.py', encoding='utf-8').read(); assert 'import test_backfill' in src, 'import test_backfill missing'; assert '(\"Backfill\", test_backfill)' in src, 'Backfill suite tuple missing'; idx_files = src.find('(\"Files\", test_files)'); idx_backfill = src.find('(\"Backfill\", test_backfill)'); assert 0 &lt; idx_files &lt; idx_backfill, f'Backfill must come AFTER Files in SUITES list (Files idx={idx_files}, Backfill idx={idx_backfill})'; idx_imp_files = src.find('import test_files'); idx_imp_backfill = src.find('import test_backfill'); assert 0 &lt; idx_imp_files &lt; idx_imp_backfill, 'import test_backfill must come AFTER import test_files'; print('test_all.py SUITES registration OK')"</automated>
  </verify>
  <acceptance_criteria>
    - `grep -c "^import test_backfill$" backend/scripts/test_all.py` returns 1.
    - `grep -c "(\"Backfill\", test_backfill)" backend/scripts/test_all.py` returns 1.
    - The line `import test_backfill` appears AFTER `import test_files` in the file.
    - The line `("Backfill", test_backfill)` appears AFTER `("Files", test_files)` in the file.
    - `cd backend && venv/Scripts/python -c "import sys, os; sys.path.insert(0, 'scripts'); from test_all import SUITES; names = [n for n, _ in SUITES]; assert 'Backfill' in names, f'Backfill not in SUITES: {names}'; assert names.index('Backfill') == names.index('Files') + 1, f'Backfill must be immediately after Files. Got order: {names}'; print('SUITES order OK')"` prints "SUITES order OK".
    - `cd backend && venv/Scripts/python -c "import sys, os; sys.path.insert(0, 'scripts'); import test_all; assert len(test_all.SUITES) == 14, f'expected 14 SUITES (was 13 + Backfill), got {len(test_all.SUITES)}'; print('SUITES count OK')"` prints "SUITES count OK".
  </acceptance_criteria>
  <done>
    `backend/scripts/test_all.py` imports `test_backfill` and registers `("Backfill", test_backfill)` in the SUITES list immediately after the existing `("Files", test_files)` entry. SUITES count is 14 (was 13). No other lines in `test_all.py` are modified.
  </done>
</task>

<task id="2-04-03" type="checkpoint:human-verify" gate="blocking">
  <name>Checkpoint: confirm Plan 01 Studio bucket + Migration 018 are deployed BEFORE running test_backfill</name>
  <what-built>
    Plans 01-04 are complete in code:
      - Plan 01: backend/app/routers/files.py performs Storage upload at upload time.
      - Plan 01: backend/migrations/018_storage_rls.sql creates storage.objects RLS policies (still needs to be APPLIED).
      - Plan 02: backend/app/services/ingestion.py writes content_markdown synchronously.
      - Plan 02: backend/requirements.txt pins docling==2.91.0.
      - Plan 03: backend/scripts/backfill_content_markdown.py exists and is invokable.
      - Plan 04: backend/scripts/test_backfill.py exists and is registered in test_all.py.

    Two operational steps require human action because they cannot be automated from inside the agent loop:
      1. Create the 'documents' bucket in Supabase Studio (one-time; Studio path: Storage -> Create bucket -> Name: documents, Public: OFF, File size limit: 50MB).
      2. Apply Migration 018 via run_migrations.py (`cd backend && DATABASE_URL='postgresql://...' venv/Scripts/python scripts/run_migrations.py`).

    Without these steps, `test_backfill.py`'s pre-flight `_verify_storage_setup()` will fail with `[FATAL] Storage bucket 'documents' is not reachable` and the test suite will report 1 fail and exit early.
  </what-built>
  <how-to-verify>
    Operator performs these steps in order:

    1. Open Supabase Studio for the project, navigate to Storage in the left nav. Confirm a bucket named `documents` exists. If not, create it: click "Create bucket", name = `documents`, set Public toggle to OFF, set File size limit to 50MB (or higher per project policy). Click Create.

    2. From the project root, apply Migration 018:
       ```
       cd backend
       DATABASE_URL='<paste from Supabase Studio -> Settings -> Database -> Direct connection URI>' venv/Scripts/python scripts/run_migrations.py
       ```
       Expected output: `RUN  018_storage_rls.sql ... OK`. If the migration was already applied (idempotent DROP/CREATE), it will still print OK.

    3. (Optional smoke check) Run a one-off canary check from a Python REPL:
       ```
       cd backend
       venv/Scripts/python -c "import os; from dotenv import load_dotenv; load_dotenv('.env'); from supabase import create_client; sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY']); print(sb.storage.from_('documents').list(path='', options={'limit': 1}))"
       ```
       Expected output: `[]` (empty list — bucket exists but has no objects yet) or a list of existing objects. ANY exception means the bucket is misconfigured or Migration 018 was not applied.

    4. Confirm the backend is running on `http://localhost:8001` (test_backfill.py hits the upload endpoint there). If not running:
       ```
       cd backend
       venv/Scripts/python -m uvicorn app.main:app --reload --port 8001
       ```

    Once all 3 (or 4) steps confirm OK, run the test:
       ```
       cd backend
       venv/Scripts/python scripts/test_backfill.py
       ```
       Expected: `Results: N passed, 0 failed` where N >= 8.
  </how-to-verify>
  <resume-signal>Type "approved" to mark the deployment-prerequisites checkpoint complete, OR describe any issues encountered (bucket creation failed, migration failed, canary failed) so the agent can investigate.</resume-signal>
</task>

</tasks>

<verification>
This plan delivers BACKFILL-03 (assertion-only verifier — Migration 012 DEFAULT did the work for existing rows) and the integration verification of Plans 01, 02, 03. Phase 2 success criterion 4 (byte-equivalence) is asserted empirically.

Verification steps:
- Task 1: AST parse + grep gates confirm test_backfill.py has the run() entry point, the 6 sections, 8+ assertions, the cleanup discipline (tracking lists, no blanket deletes), the labels for all four BACKFILL-* requirements.
- Task 2: SUITES order + count verified via Python import.
- Task 3 (checkpoint): operator confirms Plan 01 Studio bucket + Migration 018 are in place. This is a `checkpoint:human-verify` because Studio bucket creation and `DATABASE_URL=` migration application both require operator-supplied secrets that the agent does not have access to.
- Operational verification: after the checkpoint resumes, the operator runs `cd backend && venv/Scripts/python scripts/test_all.py` to confirm the new Backfill suite passes alongside the existing 13 suites (target: 14 suites green).
</verification>

<success_criteria>
- BACKFILL-03 satisfied: test_backfill.py asserts `SELECT COUNT(*) FROM documents WHERE folder_path != '/' OR scope != 'user'` returns 0 for pre-Phase-2 rows (the no-op verifier).
- Phase 2 success criterion 4 (byte-equivalence) is asserted: synchronous content_markdown ≈ fresh extract_text() result on the same blob (within ±20 chars; for plain text fixtures, identical).
- Plan 01 (Storage upload) is verified end-to-end: real upload → blob is downloadable from `documents/{user_id}/{doc_id}{ext}`.
- Plan 02 (synchronous content_markdown write) is verified end-to-end: real upload → poll-to-ready → DB readback shows non-empty content_markdown + content_markdown_status='ready'.
- Plan 03 (backfill) is verified end-to-end via subprocess: happy path populates content_markdown; missing-blob path marks `requires_user_reupload`; idempotent re-run is a no-op.
- test_backfill.py is registered in test_all.py SUITES (immediately after Files); SUITES count = 14.
- All cleanup is per-tracked-id (CLAUDE.md mandatory rule); no blanket DELETE / TRUNCATE.
- Checkpoint Task 3 confirms operator-side prerequisites are deployed before the test runs.
</success_criteria>

<output>
After completion, create `.planning/phases/02-content-markdown-backfill-gated/02-04-SUMMARY.md` recording: file created (test_backfill.py), test count by section, the SUITES registration line in test_all.py, the BACKFILL-03 verifier query, the byte-equivalence formula and tolerance, the operator-prerequisite checkpoint outcome (bucket + Migration 018 status), and the final test_all.py run result (passed/failed counts) so the Phase 2 close-out has empirical evidence that all four BACKFILL-* requirements are green.
</output>
</content>
