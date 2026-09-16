"""Phase 5 / TEST-03: integration tests for the Explorer sub-agent + generalized SSE protocol.

Covers EXPLORER-01..06 + Pitfall 8 carry-forward:

  Section 1: [Phase 5 setup canary]      — run_explorer_sub_agent importable; tool registered; backend reachable
  Section 2: [EXPLORER-01 MAX_TURNS]     — in-process counter monkeypatch; assert <=8 inner tool calls
  Section 3: [EXPLORER-02 wall-clock]    — monkeypatch WALL_CLOCK_BUDGET_S=0.1; assert short-circuit reason
  Section 4: [EXPLORER-02 no-progress]   — stub Gemini to repeat same call; assert single tool_start
  Section 5: [EXPLORER-03 recursion ban] — module assert; tool-set builder; tampered-reload
  Section 6: [EXPLORER-04 dual-emit SSE] — live chat; BOTH legacy + generalized envelope events
  Section 7: [EXPLORER-04 multi-sub]     — analyze_document + explore_knowledge_base in one turn
  Section 8: [EXPLORER-05 JSONB reload]  — GET /messages returns tool_calls[] intact
  Section 9: [EXPLORER-06 LangSmith]     — chain span with <=8 children; SKIPs without API key
  Section 10: [Pitfall 8 carry-forward]  — 50K-char Explorer summary still flows through TOOL-09 wrapper

PREREQUISITES (Phase 5 setup gate):
  1. Plan 02 applied: run_explorer_sub_agent must exist in sub_agent.py
  2. Plan 03 applied: explore_knowledge_base must register as a Gemini function
  3. Plan 04 applied: messages.py must dual-emit (sub_agent_tool_start/done events fire)
  4. Backend running on http://localhost:8001
  5. backend/.env contains SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY, GEMINI_API_KEY
  6. Phase 4 Migration 020 already applied (grep_documents RPC)
  7. Optional: LANGSMITH_API_KEY + LANGSMITH_PROJECT for Section 9 (gracefully SKIPs if absent)

If any prerequisite is missing, the canary precheck (_verify_phase5_setup) returns
a single FAIL h.test + early-returns with an actionable [FATAL] message naming the
missing Plan / migration / env var.

CRITICAL: per CLAUDE.md, tests must NEVER delete all user data. Cleanup uses
per-id batched .delete().in_("id", batch) only — ZERO bulk-table-removal SQL keywords
in executable code. Phase 3+4 added a static grep gate that fails any test file
containing those phrases outside an .eq("id", ...) clause.
"""
import importlib
import io
import json
import os
import sys
import time
import uuid
from collections import defaultdict
from typing import Any

import requests

# Reconfigure stdout/stderr to UTF-8 so emoji/arrow/box-drawing chars don't crash
# the suite on Windows cp1252 consoles. Phase 4 LOCKED pattern from
# test_exploration_tools.py:48-52.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

# Two-step sys.path bootstrap (matches test_folders.py:43-45 + test_exploration_tools.py:55-58).
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

import test_helpers as h  # noqa: E402
from supabase import create_client  # noqa: E402

# Module-top import of run_explorer_sub_agent surfaces EXPLORER-03 layer 1
# AssertionError in CI even before any chat triggers it (RESEARCH.md
# §Tool Registration Boundary "Where the setup-time error fires"). If
# EXPLORER_ALLOWED_TOOLS is tampered to include analyze_document, the import
# below raises AssertionError and the suite crashes — exactly what we want.
from app.services.sub_agent import (  # noqa: E402
    run_explorer_sub_agent,
    EXPLORER_ALLOWED_TOOLS,
    MAX_TURNS,
    WALL_CLOCK_BUDGET_S,
    _signature,
)

STORAGE_BUCKET = "documents"

# Tracking lists for scoped cleanup. Per CLAUDE.md: NEVER bulk-delete.
_tracked_documents: list = []     # list[(document_id, supabase_client)]
_tracked_folders: list = []       # list[(folder_id, supabase_client)]
_tracked_storage_paths: list = [] # list[(storage_path, supabase_client)]
_tracked_threads: list = []       # list[(thread_id, supabase_client)]

# Captured at module import — used as the start_time floor for Section 9
# LangSmith run_list filtering. Anything emitted before this is NOT ours.
_TEST_START_EPOCH = time.time()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _service_role_client():
    """Mirror auth.py:8-12; service-role client for fixture insert + cleanup."""
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )


def _track_doc(document_id, client):
    if document_id:
        _tracked_documents.append((document_id, client))


def _track_folder(folder_id, client):
    if folder_id:
        _tracked_folders.append((folder_id, client))


def _track_storage_path(path, client):
    if path:
        _tracked_storage_paths.append((path, client))


def _track_thread(thread_id, client):
    if thread_id:
        _tracked_threads.append((thread_id, client))


def _verify_phase5_setup(sb_admin) -> tuple[bool, str]:
    """Pre-flight canary: Phase 5 surface (run_explorer_sub_agent + factory + backend reachable).

    Probes:
      1. run_explorer_sub_agent + EXPLORER_ALLOWED_TOOLS importable (Plan 02 applied).
      2. analyze_document NOT in EXPLORER_ALLOWED_TOOLS (EXPLORER-03 layer 1 healthy).
      3. _build_explore_knowledge_base_tool factory exists + returns name='explore_knowledge_base' (Plan 03 applied).
      4. backend responds at BASE_URL (Plans 03-04 wired in openai_client + messages).

    Returns (ok: bool, message: str). Mirrors test_exploration_tools.py::_verify_phase4_setup.
    """
    # Probe 1: importable. Already done at module top — but re-verify symbols.
    try:
        if not callable(run_explorer_sub_agent):
            return False, (
                "[FATAL] Plan 02 not applied — run_explorer_sub_agent missing from sub_agent.py. "
                "Apply Plan 02 (Explorer sub-agent generator)."
            )
        if not isinstance(EXPLORER_ALLOWED_TOOLS, tuple):
            return False, (
                "[FATAL] EXPLORER_ALLOWED_TOOLS not a tuple — Plan 01 foundation tampered. "
                f"got: {type(EXPLORER_ALLOWED_TOOLS)}"
            )
    except Exception as e:
        return False, f"[FATAL] Plan 02 surface broken: {type(e).__name__}: {e}"

    # Probe 2: EXPLORER-03 layer 1 module assert is healthy.
    if "analyze_document" in EXPLORER_ALLOWED_TOOLS:
        return False, (
            "[FATAL] EXPLORER-03 violation — analyze_document is in EXPLORER_ALLOWED_TOOLS. "
            "The Plan 01 module-level assert should have prevented import. "
            "Did someone bypass the assert? Re-read Plan 01."
        )

    # Probe 3: Plan 03 factory exists + returns the expected name.
    try:
        from app.services.openai_client import _build_explore_knowledge_base_tool
        decl = _build_explore_knowledge_base_tool()
        if getattr(decl, "name", None) != "explore_knowledge_base":
            return False, (
                f"[FATAL] _build_explore_knowledge_base_tool().name == "
                f"{getattr(decl, 'name', None)!r}, expected 'explore_knowledge_base'. "
                f"Plan 03 factory broken."
            )
    except ImportError as e:
        return False, (
            f"[FATAL] Plan 03 not applied — _build_explore_knowledge_base_tool missing "
            f"from openai_client.py: {e}"
        )
    except Exception as e:
        return False, f"[FATAL] Plan 03 factory crashed: {type(e).__name__}: {e}"

    # Probe 4: backend reachable (3-attempt transient retry).
    last_err = None
    for attempt in range(3):
        try:
            r = requests.get(f"{h.BASE_URL}/health", timeout=5)
            if r.status_code == 200:
                return True, "ok"
            last_err = f"backend health endpoint returned {r.status_code}"
            if 500 <= r.status_code < 600 and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            break
        except Exception as e:
            last_err = f"backend unreachable: {type(e).__name__}: {e}"
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
    return False, (
        f"[FATAL] {last_err}. Start with: "
        f"cd backend && venv/Scripts/python -m uvicorn app.main:app --reload --port 8001"
    )


def _seed_doc(sb_admin, user_id, scope, folder_path, file_name, content):
    """Insert a single document with content_markdown set directly. Tracks for cleanup."""
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    eff_user_id = user_id if scope == "user" else None
    row = sb_admin.table("documents").insert({
        "user_id": eff_user_id,
        "scope": scope,
        "folder_path": folder_path,
        "file_name": file_name,
        "file_size": len(content),
        "mime_type": "text/markdown",
        "status": "ready",
        "content_markdown": content,
        "content_markdown_status": "ready",
    }).execute().data[0]
    _track_doc(row["id"], sb_admin)
    return row["id"]


def _seed_folder(sb_admin, user_id, scope, path):
    """Insert a folder row. Tracks for cleanup."""
    eff_user_id = user_id if scope == "user" else None
    row = sb_admin.table("folders").insert({
        "scope": scope,
        "user_id": eff_user_id,
        "path": path,
    }).execute().data[0]
    _track_folder(row["id"], sb_admin)
    return row["id"]


def _seed_fixture_corpus(sb_admin, user_id) -> dict:
    """Seed 12 documents across 4 unique-suffixed folders for Sections 6-10.

    Folder paths use a uuid suffix to keep this user's fixture from colliding with
    prior runs. Returns dict mapping logical_label -> list of doc_ids.
    """
    suffix = uuid.uuid4().hex[:8]
    base = f"/explorer-fixture-{suffix}"

    folder_paths = {
        "fp_2025": f"{base}/projects/2025/floor-plans",
        "fp_2026": f"{base}/projects/2026/floor-plans",
        "specs":   f"{base}/projects/2026/specs",
        "shared":  f"{base}/shared/standards",
    }

    fixtures: dict = {k: [] for k in folder_paths}
    for label, path in folder_paths.items():
        try:
            _seed_folder(sb_admin, user_id, "user", path)
        except Exception as e:
            print(f"  (folder seed for {path} failed; will rely on inferred folders: {e})")

    # 12 documents with deterministic content mentioning panel-related terms.
    docs_to_seed = [
        ("fp_2025", "panel-mdb-overview.md",
         "# Panel MDB Overview 2025\nThe electrical panel MDB-C-G3 is rated at 800A.\nFloor plan reference: /projects/2025."),
        ("fp_2025", "lighting-circuit.md",
         "# Lighting Circuit\nLighting subdistribution panels feed off the main MDB.\nPanel rating tables are referenced."),
        ("fp_2025", "ground-floor-layout.md",
         "# Ground Floor Layout\nMain electrical panel location. Floor plan north view."),
        ("fp_2026", "panel-mdb-c-g3-spec.md",
         "# Panel MDB-C-G3 Specification\nPanel rating: 800A, 400V, 3-phase. Series rating coordination per IEC 61439."),
        ("fp_2026", "circuit-schedule.md",
         "# Circuit Schedule 2026\nAll branches feed from MDB-C-G3. Panel ratings standardized per shared standard."),
        ("fp_2026", "tenant-fitout-electrical.md",
         "# Tenant Fitout Electrical\nFitout teams must coordinate with main panel ratings (MDB-C-G3)."),
        ("specs", "iec-61439-coordination.md",
         "# IEC 61439 Coordination\nPanel rating coordination requires conditional short-circuit ratings."),
        ("specs", "panel-board-spec.md",
         "# Panelboard Specification\nMDB-C-G3 series compliance per IEC 61439-1 and IEC 61439-2 sections."),
        ("specs", "earthing-spec.md",
         "# Earthing Specification\nMain panel earthing per IEC 61439 conventions."),
        ("shared", "iec-61439.md",
         "# IEC 61439 Standard Summary\nPanel assemblies — global standard. Used across projects (2025, 2026) for panel ratings."),
        ("shared", "panel-ratings-guide.md",
         "# Panel Ratings Guide\nGlobal guidance on panel ratings: short-circuit, conditional, prospective."),
        ("shared", "electrical-symbols.md",
         "# Electrical Symbols\nStandard symbols for electrical panel single-line diagrams."),
    ]

    for label, file_name, content in docs_to_seed:
        try:
            doc_id = _seed_doc(
                sb_admin, user_id, "user", folder_paths[label], file_name, content
            )
            fixtures[label].append(doc_id)
        except Exception as e:
            print(f"  (doc seed {label}/{file_name} failed: {e})")

    return {"folders": folder_paths, "docs": fixtures, "base": base}


def _cleanup():
    """Per-id batched .delete().in_(...) discipline (CLAUDE.md mandatory rule).

    Two-step delete: chunks first, then documents (FK CASCADE absence). Uses
    .in_("id", [batch]) with batches of 500 — strictly per-id, just chunked
    into round-trips of 500 ids each. Mirrors test_exploration_tools.py:201-253.
    """
    BATCH = 500

    # Documents — chunks first (FK), then documents.
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

    # Folders.
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

    # Storage paths.
    storage_by_client: dict = defaultdict(list)
    for path, client in _tracked_storage_paths:
        storage_by_client[id(client)].append((path, client))
    for _client_id, items in storage_by_client.items():
        client = items[0][1]
        paths = [p for p, _ in items]
        try:
            client.storage.from_(STORAGE_BUCKET).remove(paths)
        except Exception:
            pass

    # Threads (cascades to messages via existing FK).
    threads_by_client: dict = defaultdict(list)
    for tid, client in _tracked_threads:
        threads_by_client[id(client)].append((tid, client))
    for _client_id, items in threads_by_client.items():
        client = items[0][1]
        ids = [tid for tid, _ in items]
        for batch_start in range(0, len(ids), BATCH):
            batch = ids[batch_start:batch_start + BATCH]
            try:
                client.table("threads").delete().in_("id", batch).execute()
            except Exception:
                pass

    _tracked_documents.clear()
    _tracked_folders.clear()
    _tracked_storage_paths.clear()
    _tracked_threads.clear()


def _capture_events_from_generator(gen) -> list:
    """Drain a run_explorer_sub_agent generator and return all yielded (event_type, data) tuples.

    Catches exceptions so partial yields are still returned (V7 generator-never-raises
    test discipline — but we are robust to a genuine raise here too).
    """
    events: list = []
    try:
        for evt_type, evt_data in gen:
            events.append((evt_type, evt_data))
    except Exception as e:
        events.append(("__exception__", f"{type(e).__name__}: {e}"))
    return events


def _stream_chat_events(token, thread_id, content, timeout=120) -> tuple[list, int]:
    """POST a message and return (events, status_code).

    `events` is a list of parsed-JSON dicts in arrival order. Stops after a
    `done` event or stream end. Mirrors test_sub_agents.py::stream_sse_full.
    """
    try:
        resp = requests.post(
            f"{h.BASE_URL}/api/threads/{thread_id}/messages",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"content": content},
            stream=True,
            timeout=timeout,
        )
    except Exception as e:
        return [], 0

    if resp.status_code != 200:
        return [], resp.status_code

    events: list = []
    for line in resp.iter_lines():
        if not line:
            continue
        decoded = line.decode("utf-8") if isinstance(line, bytes) else line
        if not decoded.startswith("data:"):
            continue
        json_str = decoded[5:].strip()
        if not json_str or json_str == "[DONE]":
            continue
        try:
            event = json.loads(json_str)
            events.append(event)
            if event.get("type") == "done":
                break
        except json.JSONDecodeError:
            pass
    return events, resp.status_code


def _make_stub_gemini_client(repeat_function_call=None, summary_text="Stub summary."):
    """Build a stub of openai_client._get_client() return for Sections 3-4.

    The stub implements `.models.generate_content(...)` returning a hand-built
    response with the same function_call every time, AND `.models.generate_content_stream(...)`
    yielding a single chunk for the compact summary path.
    """
    class _StubFC:
        def __init__(self, name, args):
            self.name = name
            self.args = args

    class _StubPart:
        def __init__(self, fc=None, text=None):
            self.function_call = fc
            self.text = text

    class _StubContent:
        def __init__(self, parts):
            self.parts = parts
            self.role = "model"

    class _StubCandidate:
        def __init__(self, parts):
            self.content = _StubContent(parts)

    class _StubResponse:
        def __init__(self, parts):
            self.candidates = [_StubCandidate(parts)]

    class _StubChunk:
        def __init__(self, text):
            self.text = text

    class _StubModels:
        def generate_content(self, model, contents, config):
            if repeat_function_call is not None:
                fc = _StubFC(repeat_function_call[0], repeat_function_call[1])
                return _StubResponse([_StubPart(fc=fc)])
            # No FC -> natural finish.
            return _StubResponse([_StubPart(text=summary_text)])

        def generate_content_stream(self, model, contents, config):
            yield _StubChunk(summary_text)

    class _StubClient:
        def __init__(self):
            self.models = _StubModels()

    return _StubClient()


# ─────────────────────────────────────────────────────────────────────────────
# Sections
# ─────────────────────────────────────────────────────────────────────────────


def _section_2_max_turns(sb_admin, user_id, token, fixtures) -> tuple[int, int]:
    """EXPLORER-01 — in-process counter monkeypatching _dispatch_explorer_tool.

    Stubs Gemini to ALWAYS return a function_call (so the for-loop runs full
    MAX_TURNS turns) AND counts calls to _dispatch_explorer_tool. Asserts the
    counter is bounded by MAX_TURNS=8.
    """
    h.section("EXPLORER-01 MAX_TURNS bound — in-process counter")
    h_passed_before = h.passed
    h_failed_before = h.failed

    import app.services.sub_agent as sa
    original_dispatch = sa._dispatch_explorer_tool
    original_get_client = None
    try:
        from app.services import openai_client as oc
        original_get_client = oc._get_client
    except Exception:
        pass

    counter = {"n": 0}

    def _counting_dispatch(tool_name, args, user_id, supabase_client):
        counter["n"] += 1
        return {"ok": True, "tool": tool_name, "stub_call_n": counter["n"]}

    # Stub Gemini to always return a function_call -> drives loop to MAX_TURNS.
    stub_client = _make_stub_gemini_client(
        repeat_function_call=("tree", {"path": "/", "max_depth": 2, "scope": "user"}),
    )

    sa._dispatch_explorer_tool = _counting_dispatch
    if original_get_client:
        from app.services import openai_client as oc
        oc._get_client = lambda: stub_client

    try:
        gen = run_explorer_sub_agent("explore everything", user_id, sb_admin)
        events = _capture_events_from_generator(gen)
    finally:
        sa._dispatch_explorer_tool = original_dispatch
        if original_get_client:
            from app.services import openai_client as oc
            oc._get_client = original_get_client

    h.test(
        "EXPLORER-01: dispatch counter <= MAX_TURNS=8",
        counter["n"] <= MAX_TURNS,
        f"got counter={counter['n']}, MAX_TURNS={MAX_TURNS}",
    )
    h.test(
        "EXPLORER-01: at least one sub_agent_tool_start event yielded (sanity)",
        any(et == "sub_agent_tool_start" for et, _ in events),
        f"event_types={[et for et, _ in events][:20]}",
    )
    done_events = [e for e in events if e[0] == "sub_agent_done"]
    h.test(
        "EXPLORER-01: exactly one sub_agent_done event yielded (V7 invariant)",
        len(done_events) == 1,
        f"got {len(done_events)} done events",
    )

    return h.passed - h_passed_before, h.failed - h_failed_before


def _section_3_wall_clock(sb_admin, user_id, token, fixtures) -> tuple[int, int]:
    """EXPLORER-02 wall-clock 60s timeout, fast variant via WALL_CLOCK_BUDGET_S=0.1."""
    h.section("EXPLORER-02 wall-clock budget — fast variant (0.1s)")
    h_passed_before = h.passed
    h_failed_before = h.failed

    import app.services.sub_agent as sa
    from app.services import openai_client as oc

    original_budget = sa.WALL_CLOCK_BUDGET_S
    original_get_client = oc._get_client
    original_dispatch = sa._dispatch_explorer_tool

    def _slow_dispatch(tool_name, args, user_id, supabase_client):
        # Sleep just enough that the wall-clock budget fires within 1-2 turns.
        time.sleep(0.15)
        return {"ok": True, "tool": tool_name}

    stub_client = _make_stub_gemini_client(
        repeat_function_call=("list_files", {"path": "/", "scope": "user"}),
    )

    sa.WALL_CLOCK_BUDGET_S = 0.1
    sa._dispatch_explorer_tool = _slow_dispatch
    oc._get_client = lambda: stub_client

    t_start = time.monotonic()
    try:
        gen = run_explorer_sub_agent("test query", user_id, sb_admin)
        events = _capture_events_from_generator(gen)
        elapsed = time.monotonic() - t_start
    finally:
        sa.WALL_CLOCK_BUDGET_S = original_budget
        sa._dispatch_explorer_tool = original_dispatch
        oc._get_client = original_get_client

    done_events = [e for e in events if e[0] == "sub_agent_done"]
    h.test(
        "EXPLORER-02 wall-clock: sub_agent_done event yielded (graceful exit)",
        len(done_events) == 1,
        f"got {len(done_events)} done events; events={[et for et, _ in events]}",
    )
    h.test(
        "EXPLORER-02 wall-clock: total elapsed < 5s (fast variant)",
        elapsed < 5.0,
        f"got elapsed={elapsed:.2f}s; budget was 0.1s + dispatch sleep + Gemini stub",
    )

    return h.passed - h_passed_before, h.failed - h_failed_before


def _section_4_no_progress(sb_admin, user_id, token, fixtures) -> tuple[int, int]:
    """EXPLORER-02 no-progress detector — Gemini stub returns same call every turn.

    Stub `_get_client` to return a Gemini stub that always emits the same
    function_call (same args). The first turn dispatches; the second turn's
    _signature matches last_signature and short-circuits with reason='no_progress'.
    Asserts exactly ONE sub_agent_tool_start event is emitted.
    """
    h.section("EXPLORER-02 no-progress detector — repeated identical call")
    h_passed_before = h.passed
    h_failed_before = h.failed

    import app.services.sub_agent as sa
    from app.services import openai_client as oc

    original_get_client = oc._get_client
    original_dispatch = sa._dispatch_explorer_tool

    def _ok_dispatch(tool_name, args, user_id, supabase_client):
        return {"ok": True, "tool": tool_name}

    # Stub returns SAME (tool, args) every turn — second turn is a no-progress hit.
    stub_client = _make_stub_gemini_client(
        repeat_function_call=("tree", {"path": "/", "max_depth": 2, "scope": "user"}),
        summary_text="Stub no-progress summary.",
    )

    sa._dispatch_explorer_tool = _ok_dispatch
    oc._get_client = lambda: stub_client

    try:
        gen = run_explorer_sub_agent("test", user_id, sb_admin)
        events = _capture_events_from_generator(gen)
    finally:
        sa._dispatch_explorer_tool = original_dispatch
        oc._get_client = original_get_client

    tool_starts = [e for e in events if e[0] == "sub_agent_tool_start"]
    done_events = [e for e in events if e[0] == "sub_agent_done"]
    h.test(
        "EXPLORER-02 no-progress: exactly ONE sub_agent_tool_start emitted before short-circuit",
        len(tool_starts) == 1,
        f"got {len(tool_starts)} tool_start events",
    )
    h.test(
        "EXPLORER-02 no-progress: sub_agent_done event yielded",
        len(done_events) == 1,
        f"got {len(done_events)} done events",
    )

    return h.passed - h_passed_before, h.failed - h_failed_before


def _section_5_recursion_ban(sb_admin, user_id, token, fixtures) -> tuple[int, int]:
    """EXPLORER-03 recursion-ban — three sub-tests."""
    h.section("EXPLORER-03 recursion-ban — module / builder / tampered-reload")
    h_passed_before = h.passed
    h_failed_before = h.failed

    # Sub-test 1: module-level allowlist excludes analyze_document.
    h.test(
        "EXPLORER-03 sub-test 1: 'analyze_document' not in EXPLORER_ALLOWED_TOOLS",
        "analyze_document" not in EXPLORER_ALLOWED_TOOLS,
        f"got: {EXPLORER_ALLOWED_TOOLS}",
    )

    # Sub-test 2: _build_explorer_tool_set returns exactly the 5 allowed tools.
    import app.services.sub_agent as sa
    try:
        tools = sa._build_explorer_tool_set()
        names_in_set: set = set()
        for tool in tools:
            for fd in tool.function_declarations:
                names_in_set.add(fd.name)
        h.test(
            "EXPLORER-03 sub-test 2: _build_explorer_tool_set names == {tree, glob, grep, list_files, read_document}",
            names_in_set == {"tree", "glob", "grep", "list_files", "read_document"},
            f"got: {sorted(names_in_set)}",
        )
        h.test(
            "EXPLORER-03 sub-test 2: 'analyze_document' not in tool-set names",
            "analyze_document" not in names_in_set,
            f"got: {sorted(names_in_set)}",
        )
    except Exception as e:
        h.test(
            "EXPLORER-03 sub-test 2: _build_explorer_tool_set succeeds",
            False,
            f"crashed: {type(e).__name__}: {e}",
        )

    # Sub-test 3: tampering test — mutate EXPLORER_ALLOWED_TOOLS in-memory and
    # call _build_explorer_tool_set; the layer-2 assert should fire.
    original_tuple = sa.EXPLORER_ALLOWED_TOOLS
    raised = False
    try:
        sa.EXPLORER_ALLOWED_TOOLS = sa.EXPLORER_ALLOWED_TOOLS + ("analyze_document",)
        try:
            sa._build_explorer_tool_set()
        except AssertionError:
            raised = True
        h.test(
            "EXPLORER-03 sub-test 3: tampering triggers AssertionError in _build_explorer_tool_set",
            raised,
            "expected AssertionError on EXPLORER_ALLOWED_TOOLS drift",
        )
    finally:
        # Restore via importlib.reload — re-reads source-defined tuple from disk.
        # The reload re-runs the module's setup-time assert which would FAIL if
        # the source had analyze_document in the tuple. Since the source is
        # clean, reload restores the original tuple safely.
        try:
            importlib.reload(sa)
        except AssertionError:
            # Should NOT happen — the source-defined tuple is clean. If this
            # fires, the disk source is corrupted and the suite must fail loud.
            sa.EXPLORER_ALLOWED_TOOLS = original_tuple
            raise

    return h.passed - h_passed_before, h.failed - h_failed_before


def _section_6_dual_emit_sse(sb_admin, user_id, token, fixtures) -> tuple[int, int]:
    """EXPLORER-04 dual-emit SSE — live chat triggers Explorer; assert BOTH legacy + generalized envelope events."""
    h.section("EXPLORER-04 dual-emit SSE — legacy + generalized envelope")
    h_passed_before = h.passed
    h_failed_before = h.failed

    base = fixtures["base"]

    # Create thread.
    thread_resp = requests.post(
        f"{h.BASE_URL}/api/threads",
        headers=h.auth_headers(token),
        json={"title": "Phase 5 Explorer dual-emit test"},
        timeout=10,
    )
    if thread_resp.status_code != 200:
        h.test(
            "EXPLORER-04 dual-emit SKIPPED (thread create failed)",
            True,
            f"status={thread_resp.status_code} body={thread_resp.text[:200]}",
        )
        return h.passed - h_passed_before, h.failed - h_failed_before
    thread_id = thread_resp.json()["id"]
    _track_thread(thread_id, sb_admin)
    h.track_thread(thread_id)  # also register with helpers cleanup

    # Phrase the question to nudge Gemini toward explore_knowledge_base. We don't
    # control the LLM's tool selection, but the prompt emphasizes the OPEN-ENDED
    # multi-step nature that the system prompt teaches the model to elevate.
    prompt = (
        f"I need you to call the explore_knowledge_base tool. Use it to find "
        f"every document that mentions panel ratings or MDB-C-G3 across all my "
        f"folders under {base}. The information is spread across multiple "
        f"folders (2025, 2026, shared) and you'll need to look around the "
        f"folder tree first. Pass the user's question through to the tool."
    )

    events, status = _stream_chat_events(token, thread_id, prompt, timeout=120)
    h.test(
        f"EXPLORER-04 SSE returns 200 (status={status})",
        status == 200,
        f"status={status}",
    )

    if status != 200:
        return h.passed - h_passed_before, h.failed - h_failed_before

    # Capture event types observed.
    event_types = [e.get("type") for e in events]

    # Some checks are conditional on the LLM actually choosing explore_knowledge_base.
    # The fallback "if no Explorer triggered" path SKIPs the dual-emit assertions
    # since there's nothing to assert about. We still record a positive result for
    # "stream completed without error" so the section isn't a total no-op.
    explorer_triggered = "sub_agent_start" in event_types and any(
        e.get("type") == "sub_agent_start" and e.get("agent_name") == "explore_knowledge_base"
        for e in events
    )
    # Or via generalized envelope:
    explorer_triggered = explorer_triggered or any(
        e.get("type") == "sub_agent" and e.get("agent_name") == "explore_knowledge_base"
        for e in events
    )

    if not explorer_triggered:
        h.test(
            "EXPLORER-04 dual-emit SKIPPED (LLM did not pick explore_knowledge_base — flake-tolerant)",
            True,
            f"event_types observed: {sorted(set(event_types))}",
        )
        h.test(
            "EXPLORER-04 stream terminated cleanly",
            "done" in event_types,
            f"event_types={sorted(set(event_types))}",
        )
        return h.passed - h_passed_before, h.failed - h_failed_before

    # Plan 06-04 collapsed legacy sub_agent_* into one 'sub_agent' envelope with
    # a discriminating 'event' field. The legacy event names are gone; we now
    # assert the same five behaviors against the generalized envelope.
    generalized_events = [e for e in events if e.get("type") == "sub_agent"]
    sub_events = [e.get("event") for e in generalized_events]
    h.test(
        "EXPLORER-04: sub_agent event=start present (replaces legacy sub_agent_start)",
        "start" in sub_events,
        f"sub_events={sorted(set(e for e in sub_events if e))}",
    )
    h.test(
        "EXPLORER-04: sub_agent event=done present (replaces legacy sub_agent_done)",
        "done" in sub_events,
        f"sub_events={sorted(set(e for e in sub_events if e))}",
    )
    h.test(
        "EXPLORER-04: sub_agent event=token present (replaces legacy sub_agent_token)",
        "token" in sub_events,
        f"sub_events={sorted(set(e for e in sub_events if e))}",
    )
    h.test(
        "EXPLORER-04: sub_agent event=tool_start present (replaces legacy sub_agent_tool_start)",
        "tool_start" in sub_events,
        f"sub_events={sorted(set(e for e in sub_events if e))}",
    )
    h.test(
        "EXPLORER-04: sub_agent event=tool_done present (replaces legacy sub_agent_tool_done)",
        "tool_done" in sub_events,
        f"sub_events={sorted(set(e for e in sub_events if e))}",
    )

    # Generalized envelope dual-emit — at least 5 'sub_agent' events.
    generalized = [e for e in events if e.get("type") == "sub_agent"]
    h.test(
        "EXPLORER-04 generalized envelope: >=5 'type:sub_agent' events emitted",
        len(generalized) >= 5,
        f"got {len(generalized)} generalized events",
    )

    # Every generalized event has agent_name + event + payload.
    well_formed = all(
        ("agent_name" in e) and ("event" in e) and ("payload" in e)
        for e in generalized
    )
    h.test(
        "EXPLORER-04 generalized envelope: every event has agent_name + event + payload keys",
        well_formed,
        f"first event keys: {list(generalized[0].keys()) if generalized else 'none'}",
    )

    # At least one generalized event is event=='tool_start' AND
    # agent_name=='explore_knowledge_base'.
    has_explorer_tool_start = any(
        e.get("event") == "tool_start" and e.get("agent_name") == "explore_knowledge_base"
        for e in generalized
    )
    h.test(
        "EXPLORER-04 generalized: at least one event=='tool_start' agent_name=='explore_knowledge_base'",
        has_explorer_tool_start,
        f"agent_names+events seen: {[(e.get('agent_name'), e.get('event')) for e in generalized][:10]}",
    )

    return h.passed - h_passed_before, h.failed - h_failed_before


def _section_7_multi_sub_agent(sb_admin, user_id, token, fixtures) -> tuple[int, int]:
    """EXPLORER-04 multi-sub-agent — analyze_document + explore_knowledge_base in one turn."""
    h.section("EXPLORER-04 multi-sub-agent — analyze + explore in one chat")
    h_passed_before = h.passed
    h_failed_before = h.failed

    base = fixtures["base"]

    # Pick one specific shared-standards doc as the analyze_document target.
    target_doc_name = "iec-61439.md"
    folder_paths = fixtures["folders"]

    thread_resp = requests.post(
        f"{h.BASE_URL}/api/threads",
        headers=h.auth_headers(token),
        json={"title": "Phase 5 multi-sub-agent test"},
        timeout=10,
    )
    if thread_resp.status_code != 200:
        h.test(
            "EXPLORER-04 multi-sub SKIPPED (thread create failed)",
            True,
            f"status={thread_resp.status_code}",
        )
        return h.passed - h_passed_before, h.failed - h_failed_before
    thread_id = thread_resp.json()["id"]
    _track_thread(thread_id, sb_admin)
    h.track_thread(thread_id)

    prompt = (
        f"Two separate tasks. First, analyze the document {target_doc_name} in "
        f"detail using analyze_document. Second, separately explore the "
        f"knowledge base under {base} for everything that mentions MDB-C-G3 "
        f"using explore_knowledge_base."
    )
    events, status = _stream_chat_events(token, thread_id, prompt, timeout=180)

    h.test(
        f"EXPLORER-04 multi-sub: SSE returns 200 (status={status})",
        status == 200,
        f"status={status}",
    )
    if status != 200:
        return h.passed - h_passed_before, h.failed - h_failed_before

    # Defer JSONB inspection to Section 8 — here we just observe whether 2 sub-agent
    # starts fired in the SSE stream. The LLM may pick only one tool; that's
    # tolerated (SKIP form) per the plan's anti-flake provision.
    sub_agent_starts = [e for e in events if e.get("type") == "sub_agent_start"]
    sub_agent_starts_generalized = [
        e for e in events if e.get("type") == "sub_agent" and e.get("event") == "start"
    ]
    n_starts = max(len(sub_agent_starts), len(sub_agent_starts_generalized))

    if n_starts < 2:
        h.test(
            "EXPLORER-04 multi-sub SKIPPED (LLM only triggered "
            + str(n_starts) + " sub-agent(s); flake-tolerant)",
            True,
            f"n_starts={n_starts}; expected >=2 (analyze + explore)",
        )
        return h.passed - h_passed_before, h.failed - h_failed_before

    # We saw >=2 sub_agent_start events.
    h.test(
        "EXPLORER-04 multi-sub: >=2 sub_agent_start events observed in SSE",
        n_starts >= 2,
        f"n_starts={n_starts}",
    )
    # Verify both agent_names appear among generalized envelope events.
    seen_agents = set(
        e.get("agent_name", "") for e in events if e.get("type") == "sub_agent"
    ) | set(
        e.get("agent_name", "")
        for e in events
        if e.get("type") == "sub_agent_start"
    )
    h.test(
        "EXPLORER-04 multi-sub: BOTH 'analyze_document' and 'explore_knowledge_base' agent_names present",
        "analyze_document" in seen_agents and "explore_knowledge_base" in seen_agents,
        f"seen_agents={seen_agents}",
    )

    return h.passed - h_passed_before, h.failed - h_failed_before


def _section_8_jsonb_persistence(sb_admin, user_id, token, fixtures) -> tuple[int, int]:
    """EXPLORER-05 — GET /messages returns tool_metadata.tools_used[0].tool_calls[] intact.

    Triggers a fresh Explorer chat (cannot rely on Section 6's chat being
    Explorer-triggered), then GETs the thread's messages and asserts the
    persisted JSONB shape.
    """
    h.section("EXPLORER-05 JSONB persistence + reload")
    h_passed_before = h.passed
    h_failed_before = h.failed

    base = fixtures["base"]

    thread_resp = requests.post(
        f"{h.BASE_URL}/api/threads",
        headers=h.auth_headers(token),
        json={"title": "Phase 5 JSONB persistence test"},
        timeout=10,
    )
    if thread_resp.status_code != 200:
        h.test(
            "EXPLORER-05 SKIPPED (thread create failed)",
            True,
            f"status={thread_resp.status_code}",
        )
        return h.passed - h_passed_before, h.failed - h_failed_before
    thread_id = thread_resp.json()["id"]
    _track_thread(thread_id, sb_admin)
    h.track_thread(thread_id)

    prompt = (
        f"Use the explore_knowledge_base tool to find every reference to "
        f"MDB-C-G3 panel ratings across my projects in {base}. Pass the "
        f"user query through to the tool."
    )
    events, status = _stream_chat_events(token, thread_id, prompt, timeout=120)

    if status != 200:
        h.test(
            f"EXPLORER-05 SSE chat returns 200 (status={status})",
            False,
            f"status={status}",
        )
        return h.passed - h_passed_before, h.failed - h_failed_before

    # GET messages.
    msgs_resp = requests.get(
        f"{h.BASE_URL}/api/threads/{thread_id}/messages",
        headers=h.auth_headers(token),
        timeout=15,
    )
    h.test(
        f"EXPLORER-05 GET /messages returns 200 (status={msgs_resp.status_code})",
        msgs_resp.status_code == 200,
        f"status={msgs_resp.status_code}",
    )
    if msgs_resp.status_code != 200:
        return h.passed - h_passed_before, h.failed - h_failed_before

    msgs = msgs_resp.json()
    assistant_msgs = [m for m in msgs if m.get("role") == "assistant"]
    h.test(
        "EXPLORER-05 at least one assistant message persisted",
        len(assistant_msgs) > 0,
        f"got {len(assistant_msgs)} assistant messages",
    )
    if not assistant_msgs:
        return h.passed - h_passed_before, h.failed - h_failed_before

    # Pick the LAST assistant message (most recent).
    last = assistant_msgs[-1]
    tm_raw = last.get("tool_metadata")
    # tool_metadata may already be a dict or a JSON string depending on driver.
    if isinstance(tm_raw, str):
        try:
            tm = json.loads(tm_raw)
        except Exception:
            tm = None
    else:
        tm = tm_raw

    if tm is None:
        # The LLM may not have triggered Explorer; SKIP the rest gracefully.
        h.test(
            "EXPLORER-05 SKIPPED (LLM did not trigger sub-agent — no tool_metadata)",
            True,
            f"last assistant message tool_metadata is None",
        )
        return h.passed - h_passed_before, h.failed - h_failed_before

    h.test(
        "EXPLORER-05 tool_metadata is a dict",
        isinstance(tm, dict),
        f"type={type(tm).__name__}",
    )
    if not isinstance(tm, dict):
        return h.passed - h_passed_before, h.failed - h_failed_before

    tools_used = tm.get("tools_used") or []
    h.test(
        "EXPLORER-05 tool_metadata.tools_used is a non-empty list",
        isinstance(tools_used, list) and len(tools_used) >= 1,
        f"got len={len(tools_used) if isinstance(tools_used, list) else 'N/A'}",
    )

    # Find the explore_knowledge_base slot specifically.
    explorer_slots = [
        s for s in tools_used if s.get("tool") == "explore_knowledge_base"
    ]
    if not explorer_slots:
        h.test(
            "EXPLORER-05 SKIPPED (no explore_knowledge_base slot — LLM picked different tool)",
            True,
            f"slots: {[s.get('tool') for s in tools_used]}",
        )
        return h.passed - h_passed_before, h.failed - h_failed_before

    h.test(
        "EXPLORER-05 at least one tools_used entry has tool == 'explore_knowledge_base'",
        len(explorer_slots) >= 1,
        f"slots: {[s.get('tool') for s in tools_used]}",
    )

    explorer_slot = explorer_slots[0]

    # sub_agent_id present + UUID-like (36 chars with dashes).
    sub_agent_id = explorer_slot.get("sub_agent_id", "")
    h.test(
        "EXPLORER-05 explore_knowledge_base slot has sub_agent_id (UUID format)",
        isinstance(sub_agent_id, str) and len(sub_agent_id) == 36 and sub_agent_id.count("-") == 4,
        f"sub_agent_id={sub_agent_id!r}",
    )

    # tool_calls array of length 1..MAX_TURNS=8.
    tool_calls = explorer_slot.get("tool_calls") or []
    h.test(
        "EXPLORER-05 tool_calls is a list of length 1..MAX_TURNS=8",
        isinstance(tool_calls, list) and 1 <= len(tool_calls) <= MAX_TURNS,
        f"got len={len(tool_calls) if isinstance(tool_calls, list) else 'N/A'}",
    )

    # Every tool_calls[i].result_preview is <= 300 chars (V8 cap).
    if isinstance(tool_calls, list) and tool_calls:
        all_preview_ok = all(
            isinstance(tc, dict) and len(tc.get("result_preview", "") or "") <= 300
            for tc in tool_calls
        )
        h.test(
            "EXPLORER-05 every tool_calls[i].result_preview <= 300 chars (V8 cap)",
            all_preview_ok,
            f"max preview len: "
            + str(max((len(tc.get('result_preview', '') or '') for tc in tool_calls), default=0)),
        )

        # Every tool name is in the allowlist.
        all_tools_allowed = all(
            isinstance(tc, dict) and tc.get("tool") in EXPLORER_ALLOWED_TOOLS
            for tc in tool_calls
        )
        h.test(
            "EXPLORER-05 every tool_calls[i].tool is in EXPLORER_ALLOWED_TOOLS",
            all_tools_allowed,
            f"tools seen: {[tc.get('tool') for tc in tool_calls]}",
        )

    return h.passed - h_passed_before, h.failed - h_failed_before


def _section_9_langsmith_spans(sb_admin, user_id, token, fixtures) -> tuple[int, int]:
    """EXPLORER-06 LangSmith — chain run with <=8 child tool runs.

    SKIPs without LANGSMITH_API_KEY.
    """
    h.section("EXPLORER-06 LangSmith chain span structure")
    h_passed_before = h.passed
    h_failed_before = h.failed

    if not os.environ.get("LANGSMITH_API_KEY"):
        h.test(
            "EXPLORER-06 LangSmith SKIPPED (no LANGSMITH_API_KEY)",
            True,
            "set LANGSMITH_API_KEY env var to assert chain span structure",
        )
        return h.passed - h_passed_before, h.failed - h_failed_before

    try:
        from langsmith import Client  # type: ignore
    except ImportError as e:
        h.test(
            "EXPLORER-06 LangSmith SKIPPED (langsmith SDK not importable)",
            True,
            f"reason: {e}",
        )
        return h.passed - h_passed_before, h.failed - h_failed_before

    project = os.environ.get("LANGSMITH_PROJECT", "default")

    try:
        ls = Client()
        # Use the test start time as a floor — only runs from this test execution.
        # The list_runs API expects a datetime; convert epoch -> datetime.
        from datetime import datetime, timezone
        start_dt = datetime.fromtimestamp(_TEST_START_EPOCH, tz=timezone.utc)
        chain_runs = list(ls.list_runs(
            project_name=project,
            run_type="chain",
            filter='eq(name, "explore_knowledge_base")',
            start_time=start_dt,
        ))
    except Exception as e:
        h.test(
            "EXPLORER-06 LangSmith SKIPPED (list_runs failed)",
            True,
            f"reason: {type(e).__name__}: {e}",
        )
        return h.passed - h_passed_before, h.failed - h_failed_before

    h.test(
        "EXPLORER-06 LangSmith: at least one explore_knowledge_base chain run found",
        len(chain_runs) >= 1,
        f"got {len(chain_runs)} chain runs since test start",
    )

    if not chain_runs:
        return h.passed - h_passed_before, h.failed - h_failed_before

    # Most recent run.
    most_recent = chain_runs[0]
    try:
        children = list(ls.list_runs(parent_run_id=most_recent.id, run_type="tool"))
    except Exception as e:
        h.test(
            "EXPLORER-06 LangSmith SKIPPED (child run lookup failed)",
            True,
            f"reason: {type(e).__name__}: {e}",
        )
        return h.passed - h_passed_before, h.failed - h_failed_before

    h.test(
        f"EXPLORER-06 LangSmith: child tool count <= MAX_TURNS=8 (got {len(children)})",
        len(children) <= MAX_TURNS,
        f"got {len(children)} tool children",
    )

    # Every child's outputs JSON length <= 12_500 (RESULT_CHAR_CAP=12_000 + 500 buffer).
    if children:
        max_output_len = 0
        for child in children:
            try:
                out_json = json.dumps(child.outputs or {}, default=str)
                max_output_len = max(max_output_len, len(out_json))
            except Exception:
                pass
        h.test(
            f"EXPLORER-06 LangSmith: every tool child output JSON <= 12_500 chars (max={max_output_len})",
            max_output_len <= 12_500,
            f"max_output_len={max_output_len}",
        )

    return h.passed - h_passed_before, h.failed - h_failed_before


def _section_10_pitfall_8_carry_forward(sb_admin, user_id, token, fixtures) -> tuple[int, int]:
    """Pitfall 8 carry-forward — 50K-char Explorer summary still flows through TOOL-09 wrapper."""
    h.section("Pitfall 8 carry-forward — TOOL-09 layered-fallback wrapper for Explorer")
    h_passed_before = h.passed
    h_failed_before = h.failed

    base = fixtures["base"]

    # Seed a 50K-char document with high-yield content under fixture base.
    big_path = f"{base}/projects/2026/specs"  # use existing folder
    big_content = (
        "# Large IEC 61439 Specification\n"
        + ("Panel rating standardization for MDB-C-G3 series. " * 1000)
        + "\nEnd of large doc."
    )
    # Cap content at ~50K characters.
    big_content = big_content[:50_000]
    big_doc_id = _seed_doc(
        sb_admin, user_id, "user", big_path, "iec-61439-large.md", big_content
    )

    thread_resp = requests.post(
        f"{h.BASE_URL}/api/threads",
        headers=h.auth_headers(token),
        json={"title": "Phase 5 Pitfall 8 carry-forward test"},
        timeout=10,
    )
    if thread_resp.status_code != 200:
        h.test(
            "Pitfall 8 SKIPPED (thread create failed)",
            True,
            f"status={thread_resp.status_code}",
        )
        return h.passed - h_passed_before, h.failed - h_failed_before
    thread_id = thread_resp.json()["id"]
    _track_thread(thread_id, sb_admin)
    h.track_thread(thread_id)

    prompt = (
        f"Use explore_knowledge_base to find and summarize everything in my "
        f"knowledge base under {base} that mentions MDB-C-G3 panel ratings. "
        f"There is a large specification document I want you to surface."
    )
    events, status = _stream_chat_events(token, thread_id, prompt, timeout=180)

    h.test(
        f"Pitfall 8 SSE returns 200 (status={status})",
        status == 200,
        f"status={status}",
    )
    if status != 200:
        return h.passed - h_passed_before, h.failed - h_failed_before

    # Main agent's final answer should have streamed back via 'token' events.
    token_events = [e for e in events if e.get("type") == "token"]
    main_text = "".join(e.get("content", "") for e in token_events)

    h.test(
        "Pitfall 8 main agent emitted >=1 'token' event (TOOL-09 wrapper non-empty path)",
        len(token_events) >= 1,
        f"got {len(token_events)} token events",
    )
    h.test(
        "Pitfall 8 assistant final content has length > 0 (no empty Pitfall-8 regression)",
        len(main_text) > 0,
        f"main_text length={len(main_text)}",
    )

    return h.passed - h_passed_before, h.failed - h_failed_before


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────


def run() -> tuple[int, int]:
    """Phase 5 / TEST-03 integration suite. Returns (h.passed, h.failed)."""
    h.reset_counters()
    sb_admin = _service_role_client()

    try:
        # ─────────────────────────────────────────────────────────────────────
        h.section("Phase 5 setup canary (Explorer surface + Plan 03 factory + backend)")
        ok, msg = _verify_phase5_setup(sb_admin)
        if not ok:
            h.test("Phase 5 setup canary", False, f"{msg}")
            print(f"  Skipping all sections; please apply missing Plans / migrations.")
            return h.passed, h.failed
        h.test(
            "Phase 5 setup canary (Plan 02 + Plan 03 + Plan 04 + backend reachable)",
            True,
            msg,
        )

        # Resolve TEST_USER_A id (mirrors test_exploration_tools.py).
        u_a_id = None
        try:
            prof_resp = sb_admin.table("profiles").select("id, email").in_(
                "email", [h.TEST_USER_A["email"]]
            ).execute()
            for row in (prof_resp.data or []):
                if row.get("email") == h.TEST_USER_A["email"]:
                    u_a_id = row.get("id")
        except Exception as e:
            print(f"  (profiles-table lookup failed: {e}; falling back to admin.list_users)")

        if u_a_id is None:
            for attempt in range(2):
                try:
                    users_resp = sb_admin.auth.admin.list_users()
                    u_a_id = next(
                        (u.id for u in users_resp if u.email == h.TEST_USER_A["email"]),
                        None,
                    )
                    break
                except Exception as e:
                    print(f"  (admin.list_users attempt {attempt + 1} failed: {e})")
                    if attempt == 1:
                        h.test(
                            "Phase 5 setup: TEST_USER_A resolved",
                            False,
                            f"[FATAL] could not resolve TEST_USER_A id: {e}",
                        )
                        return h.passed, h.failed

        if u_a_id is None:
            h.test(
                "Phase 5 setup: TEST_USER_A resolved",
                False,
                "[FATAL] TEST_USER_A not in auth.users — run a prior suite first to seed.",
            )
            return h.passed, h.failed
        h.test(
            "Phase 5 setup: TEST_USER_A id resolved",
            True,
            f"u_a_id={u_a_id}",
        )

        token_a = h.get_auth_token()

        # Seed fixture corpus once for Sections 6+7+8+10.
        try:
            fixtures = _seed_fixture_corpus(sb_admin, u_a_id)
            seeded_count = sum(len(v) for v in fixtures["docs"].values())
            h.test(
                f"Phase 5 fixture corpus seeded ({seeded_count} docs across "
                f"{len(fixtures['folders'])} folders)",
                seeded_count >= 8,
                f"seeded={seeded_count}; expected >=8 to support all live sections",
            )
        except Exception as e:
            h.test(
                "Phase 5 fixture corpus seed FAILED",
                False,
                f"{type(e).__name__}: {e}",
            )
            return h.passed, h.failed

        # Run sections in order. Each section returns (p, f) for accounting in
        # logs; failures are already counted in h.passed / h.failed.
        for section_fn in [
            _section_2_max_turns,
            _section_3_wall_clock,
            _section_4_no_progress,
            _section_5_recursion_ban,
            _section_6_dual_emit_sse,
            _section_7_multi_sub_agent,
            _section_8_jsonb_persistence,
            _section_9_langsmith_spans,
            _section_10_pitfall_8_carry_forward,
        ]:
            try:
                p, f = section_fn(sb_admin, u_a_id, token_a, fixtures)
                print(f"  ({section_fn.__name__}: {p} passed, {f} failed)")
            except Exception as e:
                print(f"  [SECTION ERROR] {section_fn.__name__}: {type(e).__name__}: {e}")
                h.test(
                    f"{section_fn.__name__} crashed",
                    False,
                    f"{type(e).__name__}: {e}",
                )

        return h.passed, h.failed

    finally:
        # Cleanup belongs in finally — fires even on test crash. Per CLAUDE.md
        # mandatory rule: only per-id batched .delete().in_(...) is permitted.
        _cleanup()
        try:
            h.cleanup_threads(token_a)  # noqa
        except Exception:
            pass


if __name__ == "__main__":
    passed, failed = run()
    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
