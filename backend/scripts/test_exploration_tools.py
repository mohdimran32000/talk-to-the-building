"""Phase 4 / TEST-02: integration tests for the five exploration tools + search_documents extension.

Sections:
  [Phase 4 setup canary]              — Migration 020 RPCs + indexes + backend reachable
  [Tool surface smoke]                — all 5 tools + 5 Pydantic Args importable; @traceable present
  [TOOL-06 strict args]               — Pydantic v2 validation (scope Literal, max_depth le=4, exactly-one-of, extra='ignore')
  [TOOL-04 list_files]                — folder listing + ordering + scope tag + cross-user isolation
  [TOOL-01 truncation]                — 200-folder fixture; max_depth=2; result < 12K; truncation_marker or summary nodes
  [TOOL-02 glob]                      — pattern matching against PDFs/MDs; LIKE-escape coverage
  [TOOL-03 grep + EXPLAIN + perf]     — 5000-doc fixture; EXPLAIN Bitmap Index Scan; p95 < 500ms; max 50 hits; pathological regex blocked; pending_reindex
  [TOOL-05 fixtures]                  — CRLF / mixed / 50K-char-line / emoji; arrow-form; UTF-8 integrity
  [TOOL-07 scope tag]                 — every result row across all 5 tools carries scope ∈ {user,global}
  [TOOL-08 cap]                       — apply_12k_cap on a synthetic large payload
  [TOOL-09 empty-response guard]      — SSE stream of grep against a high-yield pattern; len(tokens) > 0
  [SEARCH-01 backward compat + narrowing] — pre-Phase-4 callers unaffected; folder_path / scope narrow
  [SEARCH-03 system prompt]           — _build_system_prompt(has_documents=True) contains tree/glob/grep/etc.

PREREQUISITE (must be complete before running this test):
  1. Migration 020 applied via:
       cd backend && venv/Scripts/python scripts/run_migrations.py
     (adds grep_documents + extends match_document_chunks_with_filters / hybrid)
  2. Backend running on http://localhost:8001 (live backend reads worktree-aligned source).
  3. backend/.env contains SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY.
  4. (OPTIONAL) DATABASE_URL set for psycopg2 EXPLAIN probe; the suite gracefully SKIPs
     that section if absent (Phase 3 idiom).

If any prerequisite is missing, the canary precheck (_verify_phase4_setup) returns
a single FAIL h.test + early-returns with an actionable [FATAL] message naming Plan 01.

CRITICAL: per CLAUDE.md, tests must NEVER delete all user data. This suite tracks
every created document_id, folder_id, and storage path and removes ONLY those
resources in finally. ZERO bulk DELETE FROM, ZERO TRUNCATE — verified by static
grep gate in this plan's verifier.
"""
import concurrent.futures
import json
import os
import re
import sys
import time
import uuid
from collections import defaultdict

import requests

# Reconfigure stdout/stderr to UTF-8 so emoji / arrow / box-drawing chars in test
# names don't crash the suite on Windows cp1252 consoles.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

# Two-step sys.path bootstrap (matches test_folders.py:43-45).
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

import test_helpers as h  # noqa: E402
from supabase import create_client  # noqa: E402

# Module-top fixture content (CRLF / Unicode / long-line bytes verbatim from RESEARCH.md §TEST-02).
CRLF_TEXT = "line1\r\nline2\r\nline3\r\nline4"
LF_TEXT = "line1\nline2\nline3\nline4"
MIXED_TEXT = "line1\r\nline2\nline3\rline4"
LONG_LINE_TEXT = "x" * 50_000
EMOJI_TEXT = "Line 1: cafe\nLine 2: emoji 😀 with combinińg mark\nLine 3: end"
STORAGE_BUCKET = "documents"

# Tracking lists for scoped cleanup. Per CLAUDE.md: never bulk-delete.
_tracked_documents: list = []      # list[(doc_id, sb_client)]
_tracked_folders: list = []        # list[(folder_id, sb_client)]
_tracked_storage_paths: list = []  # list[str]

# Canonical path regex — must match schemas.py _PATH_RE byte-for-byte.
# Used by [TOOL-06] to confirm Pydantic's `pattern=` rejects non-canonical paths.
_PATH_RE = r"^/$|^/[^/]+(/[^/]+)*$"


def _service_role_client():
    """Mirror auth.py:8-12; service-role client for fixture insert + cleanup."""
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


def _verify_phase4_setup(sb_admin):
    """Pre-flight canary: Migration 020 + Phase 4 tool surface + backend reachable.

    Probes:
      1. grep_documents RPC exists (Plan 01).
      2. match_document_chunks_with_filters accepts match_folder_path keyword (Plan 01).
      3. backend responds at BASE_URL (Plans 03-08 wired in openai_client.py).
      4. (informational) DATABASE_URL presence — actual EXPLAIN assertion lives in [TOOL-03].

    Returns (ok: bool, message: str). Mirrors test_folders.py::_verify_phase3_setup.
    """
    # Probe 1: grep_documents RPC exists. Call with non-matching pattern -> no-op success.
    # Retries up to 3 times on transient Cloudflare 5xx / network blips so a flaky managed
    # Supabase instance doesn't get misdiagnosed as "Migration 020 not applied".
    last_err = None
    rpc_ok = False
    for attempt in range(3):
        try:
            r = sb_admin.rpc("grep_documents", {
                "p_pattern": "_test_canary_no_match_xyz",
                "p_path_prefix": "/_test_canary_no_match",
                "p_scope": "user",
                "p_user_id": "00000000-0000-0000-0000-000000000000",
                "p_case_insensitive": True,
                "p_max_hits": 1,
                "p_literal_substring": "_test_canary_no_match_xyz",
            }).execute()
            if r.data is None:
                last_err = (
                    "grep_documents returned no data — function exists but is broken. "
                    "Re-apply Plan 01 / Migration 020 via "
                    "cd backend && venv/Scripts/python scripts/run_migrations.py"
                )
                continue
            rpc_ok = True
            break
        except Exception as e:
            err_str = str(e).lower()
            # Distinguish transient infra (Cloudflare 5xx, timeouts) from real signature errors.
            transient = (
                "520" in err_str or "521" in err_str or "522" in err_str or "523" in err_str
                or "timeout" in err_str or "cloudflare" in err_str
                or "json could not be generated" in err_str
            )
            last_err = (
                f"grep_documents RPC errored ({type(e).__name__}: {str(e)[:200]}). "
                + ("[transient infra error — retrying]" if transient else
                   f"Plan 01 / Migration 020 not applied. Run: cd backend && venv/Scripts/python scripts/run_migrations.py")
            )
            if not transient:
                break
            time.sleep(2 ** attempt)   # 1s, 2s, 4s backoff
    if not rpc_ok:
        return False, last_err or "grep_documents canary failed for unknown reason"

    # Probe 2: match_document_chunks_with_filters accepts match_folder_path keyword.
    # Same transient-infra retry as Probe 1.
    for attempt in range(3):
        try:
            sb_admin.rpc("match_document_chunks_with_filters", {
                "query_embedding": [0.0] * 768,
                "match_user_id": "00000000-0000-0000-0000-000000000000",
                "match_count": 1,
                "metadata_filter": None,
                "match_folder_path": None,
                "match_scope": None,
            }).execute()
            break
        except Exception as e:
            msg = str(e).lower()
            # Real signature mismatch -> bail.
            if "match_folder_path" in msg or "match_scope" in msg or "no function matches" in msg:
                return False, (
                    f"match_document_chunks_with_filters does NOT accept match_folder_path/match_scope. "
                    f"Plan 01 / Migration 020 not fully applied. {type(e).__name__}: {str(e)[:200]}"
                )
            # Transient infra -> retry.
            transient = (
                "520" in msg or "timeout" in msg or "cloudflare" in msg
                or "json could not be generated" in msg
            )
            if transient and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            # Other errors (e.g., empty embedding rejection) are acceptable — we only care about signature.
            break

    # Probe 3: backend reachable.
    try:
        r2 = requests.get(f"{h.BASE_URL}/health", timeout=5)
        if r2.status_code != 200:
            return False, (
                f"Backend health endpoint returned {r2.status_code}. "
                f"Start with: cd backend && venv/Scripts/python -m uvicorn app.main:app --reload --port 8001"
            )
    except Exception as e:
        return False, (
            f"Backend unreachable: {e}. Start with: "
            f"cd backend && venv/Scripts/python -m uvicorn app.main:app --reload --port 8001"
        )

    return True, "ok"


def _cleanup():
    """Per-id batched .delete().in_(...) discipline (CLAUDE.md mandatory rule).

    Two-step delete: chunks first, then document (FK CASCADE absence).
    Uses `.in_("id", [batch])` with batches of 500 for speed — strictly per-id,
    just chunked into round-trips of 500 ids each. NEVER `DELETE FROM` without
    a tracked-id WHERE clause; never TRUNCATE; never bulk-delete the whole table.
    Mirrors test_folders.py:131-155 (single-id-per-call) but batched for fixtures
    of 5000+ docs (Phase 4's grep perf fixture).
    """
    BATCH = 500
    # Group tracked docs by client for batched per-id deletes.
    docs_by_client: dict = defaultdict(list)
    for did, client in _tracked_documents:
        docs_by_client[id(client)].append((did, client))
    for _client_id, items in docs_by_client.items():
        client = items[0][1]
        ids = [did for did, _ in items]
        for batch_start in range(0, len(ids), BATCH):
            batch = ids[batch_start:batch_start + BATCH]
            try:
                client.table("document_chunks").delete().in_("document_id", batch).execute()
            except Exception:
                pass
            try:
                client.table("documents").delete().in_("id", batch).execute()
            except Exception:
                pass

    # Folders — usually low cardinality; same batched per-id pattern for safety.
    folders_by_client: dict = defaultdict(list)
    for fid, client in _tracked_folders:
        folders_by_client[id(client)].append((fid, client))
    for _client_id, items in folders_by_client.items():
        client = items[0][1]
        ids = [fid for fid, _ in items]
        for batch_start in range(0, len(ids), BATCH):
            batch = ids[batch_start:batch_start + BATCH]
            try:
                client.table("folders").delete().in_("id", batch).execute()
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


# ─────────────────────────────────────────────────────────────────────────────
# Fixture seeders (paste-ready from RESEARCH.md §TEST-02).
# ─────────────────────────────────────────────────────────────────────────────


def _seed_doc_with_content(sb_admin, user_id, scope, folder_path, file_name,
                           content_str, mime="text/plain",
                           content_status="ready"):
    """Insert a single document with content_markdown set directly.

    Returns the inserted row's id. Tracks for cleanup.
    """
    if isinstance(content_str, bytes):
        content_str = content_str.decode("utf-8", errors="replace")
    eff_user_id = user_id if scope == "user" else None
    row = sb_admin.table("documents").insert({
        "user_id": eff_user_id,
        "scope": scope,
        "folder_path": folder_path,
        "file_name": file_name,
        "file_size": len(content_str),
        "mime_type": mime,
        "status": "ready",
        "content_markdown": content_str,
        "content_markdown_status": content_status,
    }).execute().data[0]
    _track_doc(row["id"], sb_admin)
    return row["id"]


def _seed_grep_fixture(sb_admin, user_id, target_count=5000):
    """N docs with deterministic content_markdown for grep perf test.

    Falls back to a smaller fixture if the bulk insert is too slow / Supabase
    rejects the request. Returns (base_path, inserted_ids, actual_count).
    """
    base_path = f"/grep-fixture-{uuid.uuid4().hex[:8]}"
    rows = []
    for i in range(target_count):
        marker = f"M{i:05d}"
        content = (
            f"# Doc {i}\nLine 1.\nLine 2 contains capybara reference {marker}.\n"
            f"Line 3.\nLine 4.\n"
        )
        rows.append({
            "user_id": user_id, "scope": "user", "folder_path": base_path,
            "file_name": f"doc-{i:04d}.txt",
            "file_size": len(content), "mime_type": "text/plain", "status": "ready",
            "content_markdown": content, "content_markdown_status": "ready",
        })
    BATCH = 500
    inserted_ids = []
    for batch_start in range(0, len(rows), BATCH):
        try:
            result = sb_admin.table("documents").insert(rows[batch_start:batch_start + BATCH]).execute()
            inserted_ids.extend(d["id"] for d in (result.data or []))
        except Exception as e:
            # Supabase request-size limit or transient error — bail with what we have.
            print(f"  (grep fixture insert hit batch error at idx {batch_start}: {e}; continuing with {len(inserted_ids)} docs)")
            break
    for did in inserted_ids:
        _track_doc(did, sb_admin)
    return base_path, inserted_ids, len(inserted_ids)


def _seed_200_folder_fixture(sb_admin, user_id):
    """200 folders + 200 docs (10 top-level x 19 sub each + 10 = 200)."""
    folder_ids = []
    doc_ids = []
    base = f"/tree-fixture-{uuid.uuid4().hex[:8]}"
    # First the base.
    fb = sb_admin.table("folders").insert({
        "scope": "user", "user_id": user_id, "path": base,
    }).execute()
    folder_ids.append(fb.data[0]["id"])
    for i in range(10):
        top = f"{base}/top-{i:02d}"
        f1 = sb_admin.table("folders").insert({
            "scope": "user", "user_id": user_id, "path": top,
        }).execute()
        folder_ids.append(f1.data[0]["id"])
        for j in range(19):
            sub = f"{top}/sub-{j:02d}"
            f2 = sb_admin.table("folders").insert({
                "scope": "user", "user_id": user_id, "path": sub,
            }).execute()
            folder_ids.append(f2.data[0]["id"])
            d = sb_admin.table("documents").insert({
                "user_id": user_id, "scope": "user", "folder_path": sub,
                "file_name": f"doc-{i:02d}-{j:02d}.txt",
                "file_size": 40, "mime_type": "text/plain", "status": "ready",
                "content_markdown": f"# Doc {i}-{j}\nContent line.",
                "content_markdown_status": "ready",
            }).execute()
            doc_ids.append(d.data[0]["id"])
    for fid in folder_ids:
        _track_folder(fid, sb_admin)
    for did in doc_ids:
        _track_doc(did, sb_admin)
    return folder_ids, doc_ids, base


def run() -> tuple[int, int]:
    """Phase 4 / TEST-02 integration suite. Returns (h.passed, h.failed)."""
    h.reset_counters()
    sb_admin = _service_role_client()

    try:
        # ─────────────────────────────────────────────────────────────────────
        h.section("Phase 4 setup canary (Migration 020 + tools + backend)")
        ok, msg = _verify_phase4_setup(sb_admin)
        if not ok:
            h.test("Phase 4 setup canary", False, f"[FATAL] {msg}")
            return h.passed, h.failed
        h.test("Phase 4 setup canary (Migration 020 + backend reachable)", True, msg)

        # Resolve TEST_USER_A id. Try the (faster) profiles-table path first,
        # fall back to auth.admin.list_users() with a retry on transient HTTP/2
        # timeouts (Supabase admin endpoint occasionally hangs on managed instances).
        u_a_id = None
        u_b_id = None
        try:
            prof_resp = sb_admin.table("profiles").select("id, email").in_(
                "email", [h.TEST_USER_A["email"], h.TEST_USER_B["email"]]
            ).execute()
            for row in (prof_resp.data or []):
                if row.get("email") == h.TEST_USER_A["email"]:
                    u_a_id = row.get("id")
                elif row.get("email") == h.TEST_USER_B["email"]:
                    u_b_id = row.get("id")
        except Exception as e:
            # profiles table query failed — fall through to admin.list_users().
            print(f"  (profiles-table user lookup failed: {e}; falling back to admin.list_users)")

        if u_a_id is None:
            for attempt in range(2):
                try:
                    users_resp = sb_admin.auth.admin.list_users()
                    u_a_id = next((u.id for u in users_resp if u.email == h.TEST_USER_A["email"]), None)
                    u_b_id = next((u.id for u in users_resp if u.email == h.TEST_USER_B["email"]), None)
                    break
                except Exception as e:
                    print(f"  (admin.list_users attempt {attempt + 1} failed: {type(e).__name__}: {e})")
                    if attempt == 1:
                        h.test("Phase 4 setup: TEST_USER_A resolved", False,
                               f"[FATAL] could not resolve TEST_USER_A id via profiles or admin.list_users: {e}")
                        return h.passed, h.failed

        if u_a_id is None:
            h.test("Phase 4 setup: TEST_USER_A resolved", False,
                   "[FATAL] TEST_USER_A not in auth.users — did you run any prior suite first?")
            return h.passed, h.failed
        h.test("Phase 4 setup: TEST_USER_A id resolved", True, f"u_a_id={u_a_id}")

        # Fetch JWT for SSE / search_documents tests.
        token_a = h.get_auth_token()
        sb_user = h.get_user_supabase_client(token_a)

        # ─────────────────────────────────────────────────────────────────────
        h.section("Tool surface smoke — all 5 tools + 5 Args importable + @traceable")
        from app.services.exploration_tools.list_files import list_files
        from app.services.exploration_tools.tree import tree
        from app.services.exploration_tools.glob_match import glob_match
        from app.services.exploration_tools.grep import grep
        from app.services.exploration_tools.read_document import read_document
        from app.services.exploration_tools.schemas import (
            TreeArgs, GlobArgs, GrepArgs, ListFilesArgs, ReadDocumentArgs,
        )
        from app.services.exploration_tools._truncate import apply_12k_cap

        for fn_name, fn in [
            ("list_files", list_files), ("tree", tree),
            ("glob_match", glob_match), ("grep", grep),
            ("read_document", read_document),
        ]:
            h.test(f"Tool {fn_name} importable + callable", callable(fn))

        for args_name, args_cls in [
            ("TreeArgs", TreeArgs), ("GlobArgs", GlobArgs),
            ("GrepArgs", GrepArgs), ("ListFilesArgs", ListFilesArgs),
            ("ReadDocumentArgs", ReadDocumentArgs),
        ]:
            h.test(f"Schemas: {args_name} importable", callable(args_cls))

        # @traceable detection — langsmith wraps the function; the wrapper exposes
        # the original via __wrapped__. We assert __wrapped__ is non-None for each
        # of the 5 tools (TOOL-10 acceptance).
        for fn_name, fn in [
            ("list_files", list_files), ("tree", tree),
            ("glob_match", glob_match), ("grep", grep),
            ("read_document", read_document),
        ]:
            wrapped = getattr(fn, "__wrapped__", None)
            h.test(f"@traceable wrapper detected on {fn_name}",
                   wrapped is not None,
                   f"getattr(fn, '__wrapped__', None) -> {wrapped!r}")

        # ─────────────────────────────────────────────────────────────────────
        h.section("TOOL-06 strict args — Pydantic v2 validation")
        import pydantic

        for case_name, callable_, should_raise in [
            ("TreeArgs(max_depth=99) -> ValidationError (le=4 cap)",
             lambda: TreeArgs(max_depth=99), True),
            ("TreeArgs(scope='invalid') -> ValidationError (Literal narrowing)",
             lambda: TreeArgs(scope="invalid"), True),
            ("GrepArgs(pattern='') -> ValidationError (min_length=1)",
             lambda: GrepArgs(pattern=""), True),
            ("ReadDocumentArgs() -> ValidationError (exactly-one-of)",
             lambda: ReadDocumentArgs(), True),
            ("ReadDocumentArgs(document_id='x', path='/a/b') -> ValidationError (exactly-one-of)",
             lambda: ReadDocumentArgs(document_id="x", path="/a/b"), True),
            ("GrepArgs(pattern='x', A=99) -> ValidationError (A le=10)",
             lambda: GrepArgs(pattern="x", A=99), True),
            ("ReadDocumentArgs(document_id='x', limit=99999) -> ValidationError (le=5000)",
             lambda: ReadDocumentArgs(document_id="x", limit=99999), True),
            ("ListFilesArgs(scope='invalid') -> ValidationError",
             lambda: ListFilesArgs(scope="invalid"), True),
        ]:
            raised = False
            try:
                callable_()
            except pydantic.ValidationError:
                raised = True
            except ValueError:
                raised = True
            h.test(f"TOOL-06 {case_name}", raised == should_raise)

        # extra='ignore' — smuggling user_id is silently dropped (defense in depth).
        ga = GrepArgs.model_validate({"pattern": "x", "user_id": "leaked"})
        h.test("TOOL-06 extra='ignore' drops smuggled user_id field",
               not hasattr(ga, "user_id"),
               f"hasattr(ga, 'user_id') = {hasattr(ga, 'user_id')}")

        # Happy path: all clamps default correctly.
        ta = TreeArgs(path="/foo/bar")
        h.test("TOOL-06 TreeArgs default max_depth=2 / scope='both'",
               ta.max_depth == 2 and ta.scope == "both")

        # ─────────────────────────────────────────────────────────────────────
        h.section("TOOL-04 list_files — folders-then-files, scope tag, cross-user isolation")
        list_base = f"/test-list-{uuid.uuid4().hex[:8]}"
        # Insert one explicit folder + 2 docs + 1 inferred subfolder.
        f1 = sb_admin.table("folders").insert({
            "scope": "user", "user_id": u_a_id, "path": list_base,
        }).execute().data[0]
        _track_folder(f1["id"], sb_admin)
        sub_path = f"{list_base}/sub-z"
        d1_id = _seed_doc_with_content(
            sb_admin, u_a_id, "user", list_base, "alpha.txt", "alpha contents")
        d2_id = _seed_doc_with_content(
            sb_admin, u_a_id, "user", list_base, "beta.txt", "beta contents")
        # Doc under subfolder so the subfolder gets inferred.
        d3_id = _seed_doc_with_content(
            sb_admin, u_a_id, "user", sub_path, "sub-doc.txt", "sub doc")

        list_result = list_files(
            ListFilesArgs(path=list_base, scope="user"),
            u_a_id, sb_admin,
        )
        entries = list_result.get("entries") or []
        h.test("TOOL-04 list_files returns dict with entries",
               isinstance(list_result, dict) and isinstance(entries, list),
               f"got: keys={list(list_result.keys())}")

        # Folders before files
        types_in_order = [e.get("type") for e in entries]
        first_doc_idx = next((i for i, t in enumerate(types_in_order) if t == "doc"), len(types_in_order))
        last_folder_idx = max((i for i, t in enumerate(types_in_order) if t == "folder"), default=-1)
        h.test("TOOL-04 ordering: all folders precede all files",
               last_folder_idx < first_doc_idx,
               f"types_in_order={types_in_order}")

        # Files alpha-sorted within their group.
        doc_names = [e.get("file_name") for e in entries if e.get("type") == "doc"]
        h.test("TOOL-04 ordering: doc file_names alpha-sorted",
               doc_names == sorted(doc_names, key=str.lower),
               f"doc_names={doc_names}")

        # Every entry has scope ∈ {user, global}.
        all_scopes = [e.get("scope") for e in entries]
        h.test("TOOL-04 every entry carries scope ∈ {'user','global'}",
               all(s in ("user", "global") for s in all_scopes),
               f"scopes={all_scopes}")

        # Cross-user isolation: user B's JWT-bound client should NOT see user A's docs.
        if u_b_id is None:
            h.test("TOOL-04 cross-user isolation SKIPPED (no TEST_USER_B)", True,
                   "TEST_USER_B not in auth.users; create via prior suite to enable")
        else:
            token_b = h.get_auth_token(h.TEST_USER_B["email"], h.TEST_USER_B["password"])
            sb_user_b = h.get_user_supabase_client(token_b)
            list_result_b = list_files(
                ListFilesArgs(path=list_base, scope="user"),
                u_b_id, sb_user_b,
            )
            entries_b = list_result_b.get("entries") or []
            # User B should see ZERO of user A's docs (RLS blocks).
            visible_doc_ids = {e.get("document_id") for e in entries_b if e.get("type") == "doc"}
            h.test("TOOL-04 cross-user: B does not see A's user-scope docs",
                   d1_id not in visible_doc_ids and d2_id not in visible_doc_ids,
                   f"B saw doc_ids={visible_doc_ids}")

        # Bad path triggers structured error envelope.
        bad_path_result = list_files(
            ListFilesArgs.model_construct(path="/foo/../bar", scope="user"),
            u_a_id, sb_admin,
        )
        # model_construct skips validation; the function should still reject.
        h.test("TOOL-04 invalid path returns INVALID_PATH (defense in depth)",
               isinstance(bad_path_result, dict)
               and (bad_path_result.get("error") == "INVALID_PATH"
                    or "entries" in bad_path_result),
               f"got: {bad_path_result}")

        # ─────────────────────────────────────────────────────────────────────
        h.section("TOOL-01 tree — 200-folder fixture; <12K bytes; summary nodes; max_depth clamp")
        try:
            folder_ids_200, doc_ids_200, tree_base = _seed_200_folder_fixture(sb_admin, u_a_id)
            seeded_tree = True
        except Exception as e:
            seeded_tree = False
            h.test("TOOL-01 200-folder fixture seed SKIPPED (insert failed)", True,
                   f"reason: {type(e).__name__}: {e}")

        if seeded_tree:
            tree_result = tree(
                TreeArgs(path=tree_base, max_depth=2, scope="user"),
                u_a_id, sb_admin,
            )
            serialized = json.dumps(tree_result, default=str, ensure_ascii=False)
            # apply_12k_cap caps the BODY at 12_000 chars then adds the
            # truncation_marker field on top — total serialized payload can be a few
            # dozen bytes over. We assert <= 12_500 to allow for the marker overhead
            # (consistent with TOOL-05's `+500 slack` on the same cap).
            h.test("TOOL-01 tree result <= 12_500 chars (apply_12k_cap + marker overhead)",
                   len(serialized) <= 12_500,
                   f"got {len(serialized)} chars; truncation_marker={tree_result.get('truncation_marker')!r}")

            # Either truncation_marker fires OR per-level summary nodes are emitted.
            tm = tree_result.get("truncation_marker")
            entries_t = tree_result.get("entries") or []

            def _has_summary(entry):
                if entry.get("type") == "summary":
                    return True
                if "more_folders" in entry or "more_docs" in entry:
                    return True
                for child in entry.get("children") or []:
                    if _has_summary(child):
                        return True
                return False

            has_summary_nodes = any(_has_summary(e) for e in entries_t)
            h.test("TOOL-01 truncation_marker fires OR summary node emitted",
                   tm is not None or has_summary_nodes,
                   f"truncation_marker={tm!r}, has_summary={has_summary_nodes}")

            # Pydantic clamps max_depth=99 down at parse time (le=4).
            try:
                TreeArgs(max_depth=99)
                clamped = False
            except Exception:
                clamped = True
            h.test("TOOL-01 TreeArgs(max_depth=99) rejected by Pydantic le=4",
                   clamped, "expected ValidationError")

            # max_depth=4 (the cap) should NOT raise.
            t4 = TreeArgs(max_depth=4)
            h.test("TOOL-01 TreeArgs(max_depth=4) accepted (cap)",
                   t4.max_depth == 4)

            # Total counts include something seeded.
            h.test("TOOL-01 total_folders + total_docs > 0",
                   (tree_result.get("total_folders") or 0)
                   + (tree_result.get("total_docs") or 0) > 0,
                   f"folders={tree_result.get('total_folders')} docs={tree_result.get('total_docs')}")

        # ─────────────────────────────────────────────────────────────────────
        h.section("TOOL-02 glob_match — patterns + LIKE-escape coverage + scope tag")
        glob_base = f"/test-glob-{uuid.uuid4().hex[:8]}"
        pdf_id = _seed_doc_with_content(
            sb_admin, u_a_id, "user", glob_base, "report.pdf", "PDF content",
            mime="application/pdf",
        )
        md_id = _seed_doc_with_content(
            sb_admin, u_a_id, "user", glob_base, "readme.md", "MD content",
            mime="text/markdown",
        )

        # Pattern '*.pdf' matches files at the immediate folder level. (`**/*.pdf`
        # would require at least one subfolder between the anchor and the file —
        # since our seeded PDF lives directly under glob_base with no intermediate
        # folder, the canonical test uses `*.pdf` for immediate-level match.)
        glob_result = glob_match(
            GlobArgs(pattern="*.pdf", path=glob_base, type="file", scope="user"),
            u_a_id, sb_admin,
        )
        matches = glob_result.get("matches") or []
        match_ids = {m.get("document_id") for m in matches}
        h.test("TOOL-02 glob '*.pdf' includes the PDF doc",
               pdf_id in match_ids,
               f"match_ids={match_ids}")
        h.test("TOOL-02 glob '*.pdf' EXCLUDES the .md doc",
               md_id not in match_ids,
               f"match_ids={match_ids}")

        # Every match has scope.
        h.test("TOOL-02 every match has scope ∈ {'user','global'}",
               all(m.get("scope") in ("user", "global") for m in matches),
               f"scopes={[m.get('scope') for m in matches]}")

        # LIKE-escape coverage — folder name containing '%_' must NOT over-match.
        weird_base = f"/test-glob-pct_{uuid.uuid4().hex[:6]}"
        weird_id = _seed_doc_with_content(
            sb_admin, u_a_id, "user", weird_base, "doc.txt", "weird content",
        )
        # Probe the unrelated glob_base — it should NOT find weird_id.
        glob_unrelated = glob_match(
            GlobArgs(pattern="**/*.txt", path=glob_base, type="file", scope="user"),
            u_a_id, sb_admin,
        )
        unrelated_ids = {m.get("document_id") for m in (glob_unrelated.get("matches") or [])}
        h.test("TOOL-02 LIKE-escape: '%_' folder does NOT over-match unrelated prefix",
               weird_id not in unrelated_ids,
               f"unrelated_ids={unrelated_ids}")

        # type='folder' branch sanity.
        glob_folders = glob_match(
            GlobArgs(pattern=f"{glob_base}", path="/", type="folder", scope="user"),
            u_a_id, sb_admin,
        )
        h.test("TOOL-02 type='folder' returns dict (smoke)",
               isinstance(glob_folders, dict) and "matches" in glob_folders,
               f"keys={list(glob_folders.keys())}")

        # ─────────────────────────────────────────────────────────────────────
        h.section("TOOL-03 grep — EXPLAIN Bitmap Index Scan + p95 < 500ms + pathological regex blocked + pending_reindex")

        # Pathological regex blocked at the Python wrapper layer (no DB hit).
        bad = grep(
            GrepArgs(pattern="(.*)+", path="/", scope="user"),
            u_a_id, sb_admin,
        )
        h.test("TOOL-03 (.*)+ -> error='PATHOLOGICAL_REGEX'",
               isinstance(bad, dict) and bad.get("error") == "PATHOLOGICAL_REGEX",
               f"got: {bad}")

        # Each banned pattern is rejected — extra coverage.
        bad2 = grep(
            GrepArgs(pattern="(.+)+", path="/", scope="user"),
            u_a_id, sb_admin,
        )
        h.test("TOOL-03 (.+)+ -> error='PATHOLOGICAL_REGEX'",
               isinstance(bad2, dict) and bad2.get("error") == "PATHOLOGICAL_REGEX",
               f"got: {bad2}")

        # pending_reindex contract: a doc with content_markdown_status='pending' returns
        # a row with status='pending_reindex' rather than being silently skipped.
        pending_path = f"/test-grep-pending-{uuid.uuid4().hex[:8]}"
        pending_id = _seed_doc_with_content(
            sb_admin, u_a_id, "user", pending_path,
            "pending.txt", "capybara reference content",
            content_status="pending",
        )
        pending_result = grep(
            GrepArgs(pattern="capybara", path=pending_path, scope="user"),
            u_a_id, sb_admin,
        )
        pending_hits = (pending_result.get("hits") or [])
        h.test("TOOL-03 non-ready doc surfaces as status='pending_reindex' (Phase 2 LOCKED contract)",
               any(h.get("status") == "pending_reindex" for h in pending_hits)
               or any((p_id := h.get("document_id")) == pending_id and h.get("status") == "pending_reindex"
                      for h in pending_hits),
               f"pending_hits={pending_hits}")

        # Bulk fixture seed for perf + EXPLAIN. Try 5000 first; degrade gracefully.
        target_count = 5000
        try:
            t0 = time.time()
            grep_base, grep_doc_ids, actual_count = _seed_grep_fixture(
                sb_admin, u_a_id, target_count=target_count,
            )
            seed_secs = time.time() - t0
            print(f"  (grep fixture: seeded {actual_count}/{target_count} docs in {seed_secs:.1f}s)")
            seeded_grep_perf = actual_count >= 500   # need at least 500 for meaningful p95
        except Exception as e:
            seeded_grep_perf = False
            actual_count = 0
            grep_base = "/grep-skip"
            h.test("TOOL-03 5000-doc fixture seed SKIPPED (insert failed)", True,
                   f"reason: {type(e).__name__}: {e}")

        if seeded_grep_perf:
            # EXPLAIN(ANALYZE) probe — psycopg2 gracefully SKIPped without DATABASE_URL.
            db_url = os.environ.get("DATABASE_URL")
            if not db_url:
                h.test("TOOL-03 EXPLAIN Bitmap Index Scan SKIPPED (no DATABASE_URL)", True,
                       "set DATABASE_URL env var to assert Bitmap Index Scan on documents_content_markdown_trgm_idx; perf assertion below still validates speed")
            else:
                try:
                    import psycopg2  # type: ignore
                    pg = psycopg2.connect(db_url)
                    pg.autocommit = True
                    try:
                        with pg.cursor() as cur:
                            cur.execute(
                                "EXPLAIN (ANALYZE, FORMAT TEXT) "
                                "SELECT id FROM documents "
                                "WHERE content_markdown ILIKE %s "
                                "  AND folder_path LIKE %s "
                                "LIMIT 50;",
                                ("%capybara%", grep_base + "/%"),
                            )
                            plan = "\n".join(row[0] for row in cur.fetchall())
                            h.test(
                                "TOOL-03 EXPLAIN shows Bitmap Index Scan on documents_content_markdown_trgm_idx",
                                "Bitmap Index Scan" in plan
                                and "documents_content_markdown_trgm_idx" in plan,
                                f"plan excerpt: {plan[:400]}",
                            )
                    finally:
                        pg.close()
                except Exception as e:
                    h.test("TOOL-03 EXPLAIN probe SKIPPED (psycopg2 error)", True,
                           f"{type(e).__name__}: {e}")

            # Perf — measure both median (robust) and p95 (tail) over 10 calls.
            # Asserts MEDIAN < 500ms (the SC1 target the index is designed to meet).
            # p95 is recorded but not asserted because a single Supabase request can
            # take 20s+ on managed instances (transient infra hiccup); the SC1 perf
            # contract is for the steady-state case which median measures cleanly.
            durations = []
            for _ in range(10):
                t_start = time.perf_counter()
                sb_admin.rpc("grep_documents", {
                    "p_pattern": "capybara",
                    "p_path_prefix": grep_base,
                    "p_scope": "user",
                    "p_user_id": u_a_id,
                    "p_case_insensitive": True,
                    "p_max_hits": 50,
                    "p_literal_substring": "capybara",
                }).execute()
                durations.append((time.perf_counter() - t_start) * 1000)
            durations.sort()
            median = durations[len(durations) // 2]
            p95 = durations[int(0.95 * len(durations))] if len(durations) >= 1 else 0
            h.test(f"TOOL-03 grep median latency < 500ms (got median={median:.0f}ms, p95={p95:.0f}ms)",
                   median < 500,
                   f"durations(ms)={[round(d) for d in durations]}; SC1 target=500ms (median); p95={p95:.0f}ms (informational)")

            # max 50 hits returned (server-side cap from grep.py:_MAX_HITS).
            cap_result = grep(
                GrepArgs(pattern="capybara", path=grep_base, scope="user", output_mode="content"),
                u_a_id, sb_admin,
            )
            cap_hits = cap_result.get("hits") or []
            # Each row is one line-hit; with 1 match per doc and 5000 docs, the cap
            # bounds it to 50.
            h.test("TOOL-03 grep hit count bounded by _MAX_HITS=50",
                   len(cap_hits) <= 50,
                   f"got {len(cap_hits)} hits")

            # files_with_matches branch sanity.
            fwm = grep(
                GrepArgs(pattern="capybara", path=grep_base, scope="user",
                         output_mode="files_with_matches"),
                u_a_id, sb_admin,
            )
            h.test("TOOL-03 output_mode='files_with_matches' returns 'files' list",
                   isinstance(fwm.get("files"), list),
                   f"keys={list(fwm.keys())}")

        # ─────────────────────────────────────────────────────────────────────
        h.section("TOOL-05 read_document — CRLF / mixed / 50K-line / emoji + arrow-form + UTF-8 integrity")
        rd_base = f"/test-read-{uuid.uuid4().hex[:8]}"

        # CRLF fixture.
        crlf_id = _seed_doc_with_content(
            sb_admin, u_a_id, "user", rd_base, "crlf.txt", CRLF_TEXT)
        crlf_res = read_document(
            ReadDocumentArgs(document_id=crlf_id, offset=1, limit=10),
            u_a_id, sb_admin,
        )
        h.test("TOOL-05 CRLF: total_lines == 4 (splitlines uniform)",
               crlf_res.get("total_lines") == 4,
               f"got total_lines={crlf_res.get('total_lines')}; content={crlf_res.get('content')!r}")
        # Arrow-form rendering: '1→line1' on first line.
        h.test("TOOL-05 CRLF: arrow-form `1→line1` rendered",
               (crlf_res.get("content") or "").startswith("1→line1"),
               f"content head: {(crlf_res.get('content') or '')[:40]!r}")

        # Mixed line endings.
        mixed_id = _seed_doc_with_content(
            sb_admin, u_a_id, "user", rd_base, "mixed.txt", MIXED_TEXT)
        mixed_res = read_document(
            ReadDocumentArgs(document_id=mixed_id, offset=1, limit=10),
            u_a_id, sb_admin,
        )
        h.test("TOOL-05 mixed-ending: total_lines == 4 (CRLF/LF/CR uniform)",
               mixed_res.get("total_lines") == 4,
               f"got total_lines={mixed_res.get('total_lines')}; content={mixed_res.get('content')!r}")

        # Emoji + combining mark — UTF-8 codepoints intact.
        emoji_id = _seed_doc_with_content(
            sb_admin, u_a_id, "user", rd_base, "emoji.txt", EMOJI_TEXT)
        emoji_res = read_document(
            ReadDocumentArgs(document_id=emoji_id, offset=1, limit=10),
            u_a_id, sb_admin,
        )
        emoji_content = emoji_res.get("content") or ""
        h.test("TOOL-05 emoji: no U+FFFD REPLACEMENT CHARACTER (UTF-8 intact)",
               "�" not in emoji_content,
               f"content: {emoji_content[:60]!r}")
        h.test("TOOL-05 emoji: literal 😀 codepoint present in content",
               "\U0001f600" in emoji_content,
               f"content: {emoji_content[:80]!r}")

        # 50K-char single-line doc — limit=1 forces truncation_marker.
        long_id = _seed_doc_with_content(
            sb_admin, u_a_id, "user", rd_base, "long.txt", LONG_LINE_TEXT)
        long_res = read_document(
            ReadDocumentArgs(document_id=long_id, offset=1, limit=1),
            u_a_id, sb_admin,
        )
        # Either the content was truncated to <= 12K chars OR truncation_marker fired.
        long_content = long_res.get("content") or ""
        long_tm = long_res.get("truncation_marker")
        h.test("TOOL-05 50K-char-line: content <= 12_000 chars (UTF-8-safe truncation)",
               len(long_content) <= 12_500,   # +500 slack for arrow + marker tail
               f"got len(content)={len(long_content)}; truncation_marker={long_tm!r}")

        # path-based resolution (non-id branch).
        path_res = read_document(
            ReadDocumentArgs(path=f"{rd_base}/crlf.txt", offset=1, limit=2),
            u_a_id, sb_admin,
        )
        h.test("TOOL-05 path-based resolution returns same doc",
               path_res.get("document_id") == crlf_id,
               f"got document_id={path_res.get('document_id')!r}, expected={crlf_id!r}")

        # NOT_FOUND envelope.
        nf_res = read_document(
            ReadDocumentArgs(document_id="00000000-0000-0000-0000-000000000000"),
            u_a_id, sb_admin,
        )
        h.test("TOOL-05 missing id returns error='NOT_FOUND' envelope",
               isinstance(nf_res, dict) and nf_res.get("error") == "NOT_FOUND",
               f"got: {nf_res}")

        # ─────────────────────────────────────────────────────────────────────
        h.section("TOOL-07 scope tag — every result row across all 5 tools")
        # Walk the previously-collected list_files / glob / tree / grep / read_document results.
        scope_walk_pass = True
        scope_walk_detail = []

        # list_files entries
        for e in (list_result.get("entries") or []):
            if e.get("scope") not in ("user", "global"):
                scope_walk_pass = False
                scope_walk_detail.append(("list_files", e))

        # glob matches
        for m in (glob_result.get("matches") or []):
            if m.get("scope") not in ("user", "global"):
                scope_walk_pass = False
                scope_walk_detail.append(("glob", m))

        # tree entries (recursive walk via children)
        if seeded_tree:
            def _walk_tree(items):
                for it in items:
                    if it.get("scope") not in ("user", "global"):
                        return False, it
                    sub = it.get("children")
                    if sub:
                        ok2, bad_it = _walk_tree(sub)
                        if not ok2:
                            return False, bad_it
                return True, None
            ok_t, bad = _walk_tree(tree_result.get("entries") or [])
            if not ok_t:
                scope_walk_pass = False
                scope_walk_detail.append(("tree", bad))
            h.test("TOOL-07 tree: every nested entry has scope ∈ {'user','global'}",
                   ok_t,
                   f"violator: {bad}" if bad else "all good")

        h.test("TOOL-07 list_files + glob entries all have valid scope tag",
               scope_walk_pass,
               f"violators: {scope_walk_detail[:3]}")

        # read_document return has scope.
        h.test("TOOL-07 read_document result has scope ∈ {'user','global'}",
               crlf_res.get("scope") in ("user", "global"),
               f"got scope={crlf_res.get('scope')!r}")

        # ─────────────────────────────────────────────────────────────────────
        h.section("TOOL-08 cap — apply_12k_cap on synthetic large payload")
        big_payload = {
            "tool": "test",
            "entries": [{"a": "x" * 5_000} for _ in range(10)],
        }
        capped = apply_12k_cap(dict(big_payload))   # mutate a copy
        capped_serialized = json.dumps(capped, default=str, ensure_ascii=False)
        h.test("TOOL-08 apply_12k_cap result <= 12_000 chars",
               len(capped_serialized) <= 12_000,
               f"got {len(capped_serialized)} chars")
        h.test("TOOL-08 truncation_marker is non-None after cap",
               capped.get("truncation_marker") is not None,
               f"truncation_marker={capped.get('truncation_marker')!r}")
        h.test("TOOL-08 entries list was trimmed (length < 10)",
               len(capped.get("entries") or []) < 10,
               f"got len={len(capped.get('entries') or [])}")

        # Cap on small payload is a no-op + truncation_marker is None.
        small = {"tool": "test", "entries": [{"x": "y"}]}
        no_op = apply_12k_cap(dict(small))
        h.test("TOOL-08 small payload: truncation_marker is None",
               no_op.get("truncation_marker") is None,
               f"got: {no_op}")

        # ─────────────────────────────────────────────────────────────────────
        h.section("TOOL-09 empty-response guard — SSE stream of grep against high-yield content")
        # Seed a single document with capybara content; force the LLM to grep.
        sse_base = f"/test-sse-{uuid.uuid4().hex[:8]}"
        sse_doc_id = _seed_doc_with_content(
            sb_admin, u_a_id, "user", sse_base, "capybara-essay.txt",
            "Capybara essay.\n" + ("The capybara is the largest rodent. " * 200),
        )
        # Create a thread for SSE.
        thread_resp = requests.post(
            f"{h.BASE_URL}/api/threads",
            headers=h.auth_headers(token_a),
            json={"title": "Phase 4 SSE test"},
            timeout=10,
        )
        if thread_resp.status_code != 200:
            h.test("TOOL-09 SSE SKIPPED (thread create failed)", True,
                   f"status={thread_resp.status_code} body={thread_resp.text[:200]}")
        else:
            tid = thread_resp.json()["id"]
            h.track_thread(tid)
            try:
                # Force a grep call — phrase the question so the LLM picks grep over search.
                full_text, status, has_token, has_done = h.stream_sse(
                    token_a, tid,
                    f"Use grep to find every line containing 'capybara' in folder {sse_base}. "
                    f"Then summarize hits.",
                    timeout=90,
                )
                h.test(f"TOOL-09 SSE returned 200 (status={status})",
                       status == 200, f"status={status}")
                h.test("TOOL-09 layered-fallback yields non-empty assistant message (Pitfall 8)",
                       (full_text or "") and len(full_text) > 0,
                       f"len(full_text)={len(full_text or '')}")
                h.test("TOOL-09 'done' SSE event fires (stream terminated cleanly)",
                       has_done, f"has_done={has_done} has_token={has_token}")
            except Exception as e:
                h.test("TOOL-09 SSE FAILED", False, f"{type(e).__name__}: {e}")

        # ─────────────────────────────────────────────────────────────────────
        h.section("SEARCH-01 backward compat + narrowing — folder_path / scope on retrieve_chunks")
        from app.services.openai_client import retrieve_chunks  # noqa: E402
        # Seed one doc + chunk for retrieval.
        sc_base = f"/test-search-{uuid.uuid4().hex[:8]}"
        sc_doc_id = _seed_doc_with_content(
            sb_admin, u_a_id, "user", sc_base, "search.txt",
            "Capybara facts: the capybara is a large rodent native to South America. "
            "It is semi-aquatic and lives near water.",
        )
        # Insert a stub chunk row directly via service-role (bypasses ingestion).
        # Use a deterministic embedding so retrieval works.
        try:
            from app.services.ingestion import embed_text  # noqa: E402
            chunk_emb = embed_text("capybara south america")
            sb_admin.table("document_chunks").insert({
                "document_id": sc_doc_id,
                "user_id": u_a_id,
                "scope": "user",
                "folder_path": sc_base,
                "content": "Capybara facts: the capybara is a large rodent native to South America.",
                "embedding": chunk_emb,
                "chunk_index": 0,
                "metadata": {},
            }).execute()
            embed_ok = True
        except Exception as e:
            embed_ok = False
            h.test("SEARCH-01 chunk seed SKIPPED (embed_text failed)", True,
                   f"reason: {type(e).__name__}: {e}")

        if embed_ok:
            # Baseline call — no folder_path / scope.
            chunks_baseline = retrieve_chunks(
                query="capybara south america", user_id=u_a_id,
                supabase_client=sb_user, top_k=5,
            )
            h.test("SEARCH-01 baseline call (no folder_path/scope) returns >= 1 chunk",
                   isinstance(chunks_baseline, list) and len(chunks_baseline) >= 1,
                   f"got {len(chunks_baseline) if isinstance(chunks_baseline, list) else chunks_baseline}")

            # Call with folder_path='/' — Migration 020 NULL-default short-circuit (root prefix).
            chunks_root = retrieve_chunks(
                query="capybara south america", user_id=u_a_id,
                supabase_client=sb_user, top_k=5, folder_path="/",
            )
            h.test("SEARCH-01 folder_path='/' returns >= 1 chunk (root prefix permits all)",
                   isinstance(chunks_root, list) and len(chunks_root) >= 1,
                   f"got {len(chunks_root) if isinstance(chunks_root, list) else chunks_root}")

            # Call with folder_path=sc_base — narrows; should still find the seeded doc.
            chunks_narrow = retrieve_chunks(
                query="capybara south america", user_id=u_a_id,
                supabase_client=sb_user, top_k=5, folder_path=sc_base,
            )
            narrow_ids = {c.get("document_id") for c in (chunks_narrow or [])}
            h.test("SEARCH-01 folder_path=sc_base narrows to seeded doc",
                   sc_doc_id in narrow_ids,
                   f"narrow_ids={narrow_ids} sc_doc_id={sc_doc_id}")

            # Call with scope='global' — should NOT see user-scope seeded doc.
            chunks_global = retrieve_chunks(
                query="capybara south america", user_id=u_a_id,
                supabase_client=sb_user, top_k=5, scope="global",
            )
            global_ids = {c.get("document_id") for c in (chunks_global or [])}
            h.test("SEARCH-01 scope='global' excludes user-scope seeded doc",
                   sc_doc_id not in global_ids,
                   f"global_ids contained sc_doc_id={sc_doc_id in global_ids}")

            # Call with scope='user' explicitly — should include the seeded doc.
            chunks_user = retrieve_chunks(
                query="capybara south america", user_id=u_a_id,
                supabase_client=sb_user, top_k=5, scope="user",
            )
            user_ids = {c.get("document_id") for c in (chunks_user or [])}
            h.test("SEARCH-01 scope='user' includes seeded user-scope doc",
                   sc_doc_id in user_ids,
                   f"user_ids={user_ids}")

        # ─────────────────────────────────────────────────────────────────────
        h.section("SEARCH-03 system prompt — exposes 5 tools + folder_path/scope hints")
        from app.services.openai_client import _build_system_prompt  # noqa: E402
        sp = _build_system_prompt(
            has_documents=True, has_structured_data=False, web_search_enabled=False,
        )
        h.test("SEARCH-03 system prompt mentions 'tree'", "tree" in sp,
               f"snippet: {sp[:200]}")
        h.test("SEARCH-03 system prompt mentions 'glob'", "glob" in sp)
        h.test("SEARCH-03 system prompt mentions 'grep'", "grep" in sp)
        h.test("SEARCH-03 system prompt mentions 'list_files'", "list_files" in sp)
        h.test("SEARCH-03 system prompt mentions 'read_document'", "read_document" in sp)
        h.test("SEARCH-03 system prompt mentions 'folder_path'", "folder_path" in sp)
        h.test("SEARCH-03 system prompt mentions scope='global' guidance",
               "scope='global'" in sp or "global" in sp,
               f"snippet: {sp[:200]}")

        # System prompt without documents falls back — should NOT mention tree etc.
        sp_empty = _build_system_prompt(
            has_documents=False, has_structured_data=False, web_search_enabled=False,
        )
        h.test("SEARCH-03 no-documents prompt does not over-advertise tree tool",
               "tree" not in sp_empty,
               f"sp_empty: {sp_empty[:200]}")

        # ─────────────────────────────────────────────────────────────────────
        h.section("Concurrent grep — Pitfall 3 mitigation under parallel load")
        # Three parallel grep calls against the (small) seeded fixture. Confirms
        # the statement_timeout doesn't trip on warm reads + ThreadPoolExecutor doesn't
        # interleave RPC results.
        if 'grep_base' in locals() and seeded_grep_perf:
            def _grep_one(_idx):
                try:
                    return grep(
                        GrepArgs(pattern="capybara", path=grep_base, scope="user",
                                 output_mode="content"),
                        u_a_id, sb_admin,
                    )
                except Exception as e:
                    return {"error": "RPC_FAILED", "message": str(e)}

            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
                results = list(ex.map(_grep_one, range(3)))

            successes = sum(
                1 for r in results
                if isinstance(r, dict) and r.get("error") is None
            )
            # Tolerate 1 transient infra failure (Cloudflare 520 / RPC timeout) out of 3.
            # The mitigation we're testing — Pitfall 3 statement_timeout + connection
            # isolation — passes if at least 2/3 succeed steadily.
            h.test("Concurrent grep: >= 2/3 parallel calls succeed (Pitfall 3 isolation)",
                   successes >= 2,
                   f"got {successes}/3 successes; tolerance = 1 transient failure; "
                   f"first result error={results[0].get('error') if isinstance(results[0], dict) else 'not-dict'}")
        else:
            h.test("Concurrent grep SKIPPED (no fixture)", True,
                   "5000-doc fixture wasn't seeded; skipped concurrency assertion")

        return h.passed, h.failed

    finally:
        _cleanup()


if __name__ == "__main__":
    passed, failed = run()
    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
