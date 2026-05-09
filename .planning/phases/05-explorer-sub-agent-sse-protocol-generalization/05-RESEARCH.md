# Phase 5: Explorer Sub-Agent + SSE Protocol Generalization — Research

**Researched:** 2026-05-09
**Domain:** Bounded multi-turn sub-agent loops with the native `google-genai` SDK; SSE event-protocol generalization across two heterogenous sub-agents; LangSmith span hierarchy (`run_type="chain"` parent with nested `tool` children); JSONB persistence of sub-agent traces.
**Confidence:** HIGH — every claim grounded in shipped Phase 1–4 code, in-tree `sub_agent.py`/`messages.py`/`openai_client.py` patterns, or already-validated PITFALLS.md guidance. Two `[ASSUMED]` items captured in the Assumptions Log; rest is `[VERIFIED]` against HEAD.

---

## Project Constraints (from CLAUDE.md)

These directives carry the same authority as locked decisions. They are **non-negotiable**:

- **Python venv** mandatory (`cd backend && venv/Scripts/python ...`)
- **No LangChain / no LangGraph** — raw `google-genai` SDK only
- **Pydantic** for structured outputs (Explorer's Pydantic args model continues TOOL-06 convention)
- **Row-Level Security on every table** — Explorer queries inherit RLS via the existing tool functions; no new bypass paths
- **SSE for chat streaming** — Phase 5 generalizes (does NOT bypass) the existing `messages.py:event_generator` pipeline
- **Polling, not Realtime** for ingestion — irrelevant here; sub-agent events flow through SSE only
- **Stateless completions** — Explorer gets the user question; it does NOT receive parent thread history (token-cost containment is the entire point of sub-agents per `research/STACK.md` §5)
- **Tests must NEVER delete all user data** — `_tracked_*` + per-id `.delete().eq()` in `finally`; no blanket DELETE / TRUNCATE; documented "no whole-table wipes" docstring rephrase from Phase 3 / Plan 06 carries forward
- **Plans saved to `.planning/phases/05-.../05-NN-PLAN.md`** (Phase 3 / 4 convention; not `.agent/plans/`)

---

<phase_requirements>
## Phase Requirements

| ID | Description (verbatim from REQUIREMENTS.md L61–66, L86) | Research Support |
|----|-------------|------------------|
| EXPLORER-01 | `run_explorer_sub_agent()` extends existing `run_sub_agent` shape with `for turn in range(MAX_TURNS=8)` hard bound | §Sub-Agent Loop Architecture; §Bounded for-loop pattern |
| EXPLORER-02 | 60s wall-clock timeout + no-progress detector (tool-name+args-hash repeat → short-circuit) | §Timeout enforcement; §No-progress detector (hash design) |
| EXPLORER-03 | Hard exclusion of `analyze_document` from Explorer's toolset (no recursive sub-agents) | §Tool registration boundary; §Setup-time assertion |
| EXPLORER-04 | Generalized SSE event protocol (`agent_name`, `event`, `payload`); new event types `sub_agent_tool_start` / `sub_agent_tool_done` | §SSE Protocol Generalization; §Dual-emit backwards-compat strategy |
| EXPLORER-05 | `messages.tool_metadata` JSONB persists Explorer trace so old chats render correctly on reload | §tool_metadata Persistence Schema; §Reload rendering contract |
| EXPLORER-06 | LangSmith `@traceable(run_type="chain")` on Explorer entry; tool calls become nested children spans | §LangSmith Span Structure; §Nested children via in-process call site |
| TEST-03 | `test_explorer_sub_agent.py` — MAX_TURNS bound, timeout, no-progress detector, recursive-sub-agent rejection | §Test Strategy for TEST-03 |
</phase_requirements>

---

## Summary

Phase 5 lands a single new public function — `run_explorer_sub_agent(query, user_id, supabase_client) -> Generator[tuple[str, str], None, None]` — alongside the existing `run_sub_agent` in `backend/app/services/sub_agent.py` (or a sibling module if the planner prefers `sub_agents/explorer.py`; see §Files To Be Touched). The function runs a `for turn in range(MAX_TURNS=8)` bounded loop with a 60-second `time.monotonic()` wall-clock check and a no-progress detector that hashes `(tool_name, json.dumps(args, sort_keys=True))` per call and short-circuits on duplicate consecutive signatures. Inside the loop, only the **five Phase 4 precision tools** (`tree`, `glob`, `grep`, `list_files`, `read_document`) are registered — `analyze_document` is **hard-excluded at registration time** via a setup-time `assert` so a future maintainer cannot accidentally enable recursion. Each turn dispatches the LLM-chosen tool by reusing Phase 4's existing tool functions verbatim (`from app.services.exploration_tools.{list_files, tree, glob_match, read_document, grep} import ...`), truncates each tool result to **12K chars** (using the same `apply_12k_cap` helper at `_truncate.py`), and appends a `types.FunctionResponse` to the Explorer's local `contents` list. After loop exit (natural finish, MAX_TURNS hit, no-progress short-circuit, or wall-clock timeout) the Explorer asks Gemini for a final compact summary with **no tools available**, streaming tokens as `sub_agent_token` events.

The SSE protocol is **generalized at this single integration point** rather than forked. The current event shape `{type: 'sub_agent_start', document_name: '...'}` becomes the new shape `{type: 'sub_agent', agent_name: 'analyze_document'|'explore_knowledge_base', event: 'start'|'tool_start'|'tool_done'|'token'|'done', payload: {...}}`. To preserve frontend backwards-compat for the **single in-flight release**, the backend **dual-emits** old-form and new-form events for one release window, then drops old-form in Phase 6 alongside the `MessageList` recursive `SubAgentSection` extension. `messages.py:event_generator` adds two new `elif` arms (`sub_agent_tool_start`, `sub_agent_tool_done`) and accumulates Explorer's nested trace into a JSONB structure (`{tools_used: [{tool: 'explore_knowledge_base', sub_agent_id, tool_calls: [...]}]}`) persisted to `messages.tool_metadata` on the SAME column already used by `analyze_document` (Migration `010_sub_agents.sql` already added this JSONB column — no new migration needed for EXPLORER-05).

LangSmith hierarchy is automatic: `@traceable(run_type="chain")` on `run_explorer_sub_agent` makes it a chain span; the existing `@traceable(run_type="tool")` decorators on the five Phase 4 tools (verified at HEAD: lines `list_files.py:32`, `tree.py:34`, `glob_match.py:48`, `read_document.py:39`, `grep.py:46`) are picked up as children **automatically** because LangSmith's contextvars-based context propagation nests any `@traceable`-decorated callable invoked from within an active chain span. No manual `with trace(...)` block is needed (this is exactly how `_execute_search_documents` nests under `gemini_chat` today — see `openai_client.py:553` + `:586`).

**Primary recommendation:** Stage Phase 5 in **6 plans across 4 waves**, locked in this build order:

1. **Wave 0 — Plan 01:** Pydantic args model (`ExplorerArgs`) + `_no_progress_detector.py` helper + `_compact_summary_prompt.py` constant + Wave-0 test fixtures (deliberately broad query)
2. **Wave 1 — Plan 02:** `run_explorer_sub_agent()` in `backend/app/services/sub_agent.py` (or `sub_agents/explorer.py`) — the bounded loop, the no-progress detector wiring, the wall-clock guard, the setup-time `analyze_document`-exclusion assert, and the trace-yielding generator events. **Depends on Plan 01.**
3. **Wave 2 — Plan 03:** `_build_explore_knowledge_base_tool()` factory + dispatch arm in `openai_client.py:stream_response()` (additive `elif tool_name == "explore_knowledge_base":` mirroring `analyze_document`'s arm at `openai_client.py:892`); system prompt update describing when LLM should delegate to Explorer
4. **Wave 3 — Plans 04+05 (parallel):**
   - Plan 04 — `messages.py:event_generator` extension: forward new event types; persist trace into `messages.tool_metadata` JSONB; **dual-emit** old + new event shape for backwards-compat
   - Plan 05 — frontend pass-through in `frontend/src/lib/api.ts`: parse new `sub_agent` event shape, route to existing callbacks AND new `onSubAgentToolStart`/`onSubAgentToolDone` callbacks; minimal UI wiring in `Chat.tsx` (full UI rendering is Phase 6's job per ROADMAP.md)
5. **Wave 4 — Plan 06:** `backend/scripts/test_explorer_sub_agent.py` integration suite (TEST-03); registers in `test_all.py` SUITES as `("Explorer", test_explorer_sub_agent)` after `("Exploration", test_exploration_tools)`. Tests cover MAX_TURNS bound, timeout, no-progress detector, recursive-sub-agent registration error, generalized SSE shape, dual-emit backwards-compat, JSONB persistence/reload, and LangSmith span structure (in-process span capture or LangSmith SDK query — see §Test Strategy).

---

## Cross-Phase Dependencies & Locked Inheritances

| Inherited from | What's locked | How Phase 5 consumes it |
|----------------|---------------|------------------------|
| **Phase 1 / Migration 012–016** | `documents.folder_path` + `scope` + `content_markdown` + RLS + indexes | Explorer queries inherit ALL of this for free via the five Phase 4 tools — Phase 5 adds zero new SQL |
| **Phase 4 / 5 tool functions** | `list_files`, `tree`, `glob_match`, `read_document`, `grep` — `@traceable(run_type="tool")` decorated, `normalize_path` first-statement, `ensure_scope_tag` per row, `apply_12k_cap` at tail (except `read_document` which uses inline UTF-8 truncation) | Explorer dispatches to these IDENTICAL functions; **no fork, no parallel implementation**. Same call signature: `tool_fn(parsed_args, user_id, supabase_client) -> dict` |
| **Phase 4 / `_truncate.py`** | `apply_12k_cap(payload, char_cap=12_000)` — UTF-8 codepoint-safe; `[...truncated, N more entries]` marker; priority order `("entries","hits","matches")` | Reused **inside the Explorer loop** to enforce the in-sub-agent 12K result cap (Pitfall 7 mitigation 3 — "more aggressive than main agent's view") |
| **Phase 4 / Pydantic v2 schemas** | `Literal["user","global","both"]` + `Field(..., ge=, le=)` + `model_config = {"extra": "ignore"}` | `ExplorerArgs(query: str = Field(..., min_length=1, max_length=2000))` follows the same convention |
| **Phase 4 / openai_client.py layered-fallback wrapper** | `truncated_result = result_text[:16000]` + `system_with_context` injection at lines 1070 + 1146 (TOOL-09 contract) | Explorer's compact summary IS a tool result — flows through the SAME wrapper unchanged. Pitfall 8 mitigation 1: "the unified dispatch loop is the only context-injection path" |
| **Phase 4 / dispatch arm idiom** | `elif tool_name == "X": ... result_text = json.dumps(tool_result)` | Phase 5 adds **one** elif arm at `openai_client.py:~916` (alphabetical / functional placement near `analyze_document`'s arm at L892) |
| **Module 8 / Migration `010_sub_agents.sql`** | `messages.tool_metadata JSONB` column (verified at HEAD: `010_sub_agents.sql:4`, `schemas.py:28`, `messages.py:118-119`) | EXPLORER-05 reuses this same column — **no new migration**. Schema extends existing `{tools_used: [{tool, document_name, sub_agent_result}]}` to support `{tool: 'explore_knowledge_base', tool_calls: [...]}` |
| **Module 8 / `run_sub_agent` shape** | `Generator[tuple[str, str], None, None]` yielding `("sub_agent_start", json_payload)`, `("sub_agent_token", text)`, `("sub_agent_done", text)` | `run_explorer_sub_agent` matches this shape EXACTLY for the existing event types; adds `("sub_agent_tool_start", json_payload)` and `("sub_agent_tool_done", json_payload)` as net-new yield types |
| **Module 8 / `messages.py:event_generator`** | `tool_metadata = None` accumulator; `["tools_used"][0]["sub_agent_result"] = data[:300]` capture pattern | Phase 5 extends this accumulator to handle Explorer's nested tool calls; `tool_metadata` becomes `{tools_used: [{tool, ..., tool_calls: [{tool, args, result_preview}, ...]}]}` |
| **Phase 4 / Plan 09 test discipline** | `_tracked_documents`/`_tracked_folders`/`_tracked_storage_paths` + per-id finally cleanup + `_verify_phaseN_setup` canary + `_service_role_client()` mirror of `auth.py:8-12` | `test_explorer_sub_agent.py` reuses these patterns verbatim — same module-top fixtures, same finally-block cleanup, same canary discipline |

**Key cross-phase invariant:** Phase 5 **adds zero new SQL** and **zero new database migrations**. The `messages.tool_metadata` JSONB column already exists; the five Phase 4 tools already query Postgres correctly through RLS. Phase 5 is **pure Python orchestration plus event-protocol plumbing**.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Explorer loop control flow (`for turn in range(8)`) | **Backend / `sub_agent.py:run_explorer_sub_agent`** | — | Pure Python state machine; no DB or frontend involvement |
| Wall-clock timeout (60s) | **Backend / `time.monotonic()` checked inside loop** | — | Avoid `signal.alarm` (not portable on Windows; backend dev is Windows). `asyncio.wait_for` not applicable — `stream_response` is a synchronous generator |
| No-progress detector | **Backend / in-loop hash-set on `(tool_name, args_hash)`** | — | Stateless across requests; lives entirely in the loop's local closure |
| Tool result truncation (12K) | **Backend / `apply_12k_cap` reused from Phase 4** | — | Identical contract to Phase 4 tools — no fork |
| Setup-time `analyze_document` exclusion | **Backend / module-level `assert "analyze_document" not in EXPLORER_ALLOWED_TOOLS`** | — | Fires at import time; raises `AssertionError` before any chat traffic |
| LangSmith chain span | **Backend / `@traceable(run_type="chain")` on `run_explorer_sub_agent`** | — | LangSmith contextvars propagate to nested `@traceable` tools automatically |
| SSE event-protocol generalization | **Backend / `messages.py:event_generator`** | Frontend `lib/api.ts` (consumer side) | New JSON shape originates server-side; frontend parses but does NOT define the shape |
| Backwards-compat dual-emit | **Backend / `event_generator` emits BOTH old + new shapes for one release** | — | Server is the canonical authority on protocol versioning |
| `messages.tool_metadata` JSONB persistence | **Backend / `event_generator` accumulator written at insert-time** | DB / JSONB column (Migration 010, already shipped) | Schema extension, not column extension — column already exists |
| Reload rendering of old Explorer chats | **Frontend / `MessageList.SubAgentSection`** | — | Phase 6 owns full recursive rendering; Phase 5 only persists the data so Phase 6 has something to render |
| Explorer system prompt | **Backend / module-level constant `EXPLORER_SYSTEM_PROMPT`** | — | Reused across every loop turn; budget statement embedded |

---

## Standard Stack

### Core (already in place — no new deps required)

| Library | Version (verified at HEAD) | Purpose | Why Standard |
|---------|---------------------------|---------|--------------|
| `google-genai` | unpinned in `requirements.txt` (Episode 1 lock) | LLM tool-calling SDK — multi-turn loop via `client.models.generate_content()` then `generate_content_stream()` for the final summary | Project rule: no LangChain/LangGraph; same SDK used in `sub_agent.py:81` and `openai_client.py:731,1081` |
| `langsmith` | unpinned | `@traceable(run_type="chain")` on Explorer entry; nested children automatic via contextvars | Already 4 sites: `openai_client.py:553,586`, `sub_agent.py:18`, `sql_tool.py:56`, `web_search.py:14`, plus 5 Phase 4 tools |
| `pydantic` | unpinned (FastAPI ≥0.100 ships v2) | `ExplorerArgs(query: str)` validation | TOOL-06 convention; v2 silently-drops-unknown is a Phase 3-locked defense layer |
| `time` (stdlib) | — | `monotonic()` for wall-clock timeout | stdlib only, Windows-portable, no signal handler |
| `hashlib` (stdlib) | — | `hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()` for no-progress detector | Deterministic; stable across CPython versions; no third-party dep |
| `sse-starlette` | unpinned (Episode 1 lock) | `EventSourceResponse(event_generator())` in `messages.py:125` | Already plumbed; Phase 5 only adds new event types — no SDK constraint |

**Installation:** No new packages. Phase 5 ships with the existing dependency tree.

**Version verification:**
- `[VERIFIED]` at HEAD: `google-genai`, `langsmith`, `pydantic`, `sse-starlette` all imported successfully and used in Phase 4 — see `backend/app/services/openai_client.py:5-7`, `backend/app/services/sub_agent.py:7-8`, `backend/app/routers/messages.py:1-7`.
- `[VERIFIED]` at HEAD: `Migration 010_sub_agents.sql` exists and `messages.tool_metadata JSONB` column is queried by `messages.py:118-119` — no new migration needed.

### Alternatives Considered (and rejected)

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `for turn in range(MAX_TURNS=8)` | `while not done: ...` | Pitfall 7 mitigation 1 explicitly mandates `for ... in range(N)` over `while` — easier to reason about, impossible to accidentally remove the bound |
| `time.monotonic()` wall-clock check | `signal.SIGALRM` / `asyncio.wait_for` | `signal` is not portable to Windows (Windows backend dev environment is verified — `STATE.md` references Windows paths). `asyncio.wait_for` doesn't apply — `stream_response` is a synchronous generator yielding events into FastAPI's SSE pipeline (the entire chain is synchronous-with-generators per `messages.py:66-103`). Generators-with-deadline are best handled via inline `monotonic()` checks at known yield points. |
| `signal.alarm` for timeout | `time.monotonic()` polled inside loop | Windows non-portable; cannot be raised from inside C extension code; can leak across requests in worker threads. `monotonic()` is precise enough for 60s budget. |
| New SSE event type per nested tool (`explorer_grep_start`, `explorer_tree_start`, ...) | Generalized `sub_agent` event with `agent_name` + `event` + `payload` keys | Pitfall 12 explicitly mandates generalization at the second sub-agent — pay the small cost now, not the larger debt later. Per-tool event types would explode combinatorially and require frontend updates per new tool. |
| `asyncio.Lock` / threading for no-progress detector state | Local closure variable (`last_signature: str = ""`) | Single-request scope; no shared state across requests; locking adds latency and complexity for zero benefit |
| Spawn separate Gemini client for sub-agent | Reuse `_get_client()` (cached) | Episode 1 cache pattern at `openai_client.py:19-26` is correct; spawning a new client per Explorer call wastes API-key/SSL-handshake cost |
| One mega-migration `021_sub_agent_extensions.sql` | No migration | `messages.tool_metadata` is already JSONB — extending the JSONB schema requires zero DDL. New migration only if a hard column or index is needed (none is) |

---

## Architecture Patterns

### System Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  USER (browser)                                                              │
│    │                                                                         │
│    │  POST /api/threads/{id}/messages  (JWT auth)                           │
│    ▼                                                                         │
│  messages.py:send_message → event_generator()                               │
│    │                                                                         │
│    │  for event_type, data in stream_response(...):                         │
│    │    yields SSE JSON events to client                                    │
│    ▼                                                                         │
│  openai_client.py:stream_response()                                          │
│    │                                                                         │
│    │  Call#1 (non-streaming): Gemini picks tool                             │
│    │  ────────────┬────────────────────────────────────┐                    │
│    │              ▼                                    ▼                    │
│    │  Tool name = "explore_knowledge_base"   (other 9 tools — unchanged)   │
│    │              │                                                         │
│    │              ▼                                                         │
│    │   for evt_type, evt_data in run_explorer_sub_agent(query, user_id):  │
│    │     yield (evt_type, evt_data)  # forward through to messages.py       │
│    │                                                                         │
│    │              │                                                         │
│    │              ▼                                                         │
│    │   ┌─────────────────────────────────────────────────────────────┐     │
│    │   │  sub_agent.py:run_explorer_sub_agent  (NEW — Plan 02)       │     │
│    │   │  @traceable(run_type="chain")                                │     │
│    │   │                                                              │     │
│    │   │  yield ("sub_agent_start", json.dumps({                      │     │
│    │   │    agent_name: "explore_knowledge_base", question: ...}))   │     │
│    │   │                                                              │     │
│    │   │  start_time = time.monotonic()                               │     │
│    │   │  last_signature = ""                                         │     │
│    │   │  contents = [user_message(query)]                            │     │
│    │   │                                                              │     │
│    │   │  for turn in range(MAX_TURNS=8):                             │     │
│    │   │    if time.monotonic() - start_time > 60: break              │     │
│    │   │                                                              │     │
│    │   │    resp = client.generate_content(contents, tools=5_TOOLS)  │     │
│    │   │    fc = extract_function_call(resp)                          │     │
│    │   │    if not fc: break  # natural finish                        │     │
│    │   │                                                              │     │
│    │   │    sig = hash(fc.name, sorted_json(fc.args))                 │     │
│    │   │    if sig == last_signature: break  # no-progress            │     │
│    │   │    last_signature = sig                                      │     │
│    │   │                                                              │     │
│    │   │    yield ("sub_agent_tool_start", json.dumps({               │     │
│    │   │      tool: fc.name, args: fc.args}))                         │     │
│    │   │                                                              │     │
│    │   │    result = dispatch_to_phase_4_tool(fc.name, fc.args, ...)  │     │
│    │   │    truncated = apply_12k_cap(result)  # in-sub-agent cap     │     │
│    │   │                                                              │     │
│    │   │    yield ("sub_agent_tool_done", json.dumps({                │     │
│    │   │      tool: fc.name, result_preview: truncated[:300]}))       │     │
│    │   │                                                              │     │
│    │   │    contents.append(model_message(resp))                      │     │
│    │   │    contents.append(tool_response(fc.name, truncated))        │     │
│    │   │                                                              │     │
│    │   │  # Final compact summary call (no tools)                     │     │
│    │   │  for chunk in client.generate_content_stream(                │     │
│    │   │      contents, system=COMPACT_SUMMARY_PROMPT):               │     │
│    │   │    yield ("sub_agent_token", chunk.text)                     │     │
│    │   │                                                              │     │
│    │   │  yield ("sub_agent_done", full_summary_text)                 │     │
│    │   │  return                                                      │     │
│    │   └─────────────────────────────────────────────────────────────┘     │
│    │                                                                         │
│    │              │                                                         │
│    │              ▼                                                         │
│    │   result_text = full_summary_text  (treated as tool result)            │
│    │   truncated_result = result_text[:16000]                                │
│    │   system_with_context = f"... Tool (explore_knowledge_base) results:   │
│    │     {truncated_result}"  ← TOOL-09 layered-fallback wrapper             │
│    │                                                                         │
│    │   for chunk in client.generate_content_stream(                          │
│    │       contents, system=system_with_context):                            │
│    │     yield ("token", chunk.text)  # main agent's final answer            │
│    ▼                                                                         │
│  messages.py:event_generator (CONSUMER)                                      │
│    │                                                                         │
│    │  Routes events:                                                        │
│    │    sub_agent_start      → (1) emit OLD shape: {type: "sub_agent_start",│
│    │                              document_name?: ..., agent_name: ...}      │
│    │                          → (2) emit NEW shape: {type: "sub_agent",     │
│    │                              agent_name, event: "start", payload: ...} │
│    │    sub_agent_tool_start → emit {type: "sub_agent_tool_start", ...}     │
│    │                          AND {type: "sub_agent",                       │
│    │                              event: "tool_start", payload: {tool, args}}│
│    │    sub_agent_tool_done  → similar dual-emit                            │
│    │    sub_agent_token      → unchanged                                    │
│    │    sub_agent_done       → unchanged + accumulate to tool_metadata      │
│    │                                                                         │
│    │  Accumulator: tool_metadata = {                                        │
│    │    tools_used: [{                                                      │
│    │      tool: "explore_knowledge_base",                                   │
│    │      sub_agent_id: <uuid>,                                             │
│    │      tool_calls: [                                                     │
│    │        {tool: "tree", args: {...}, result_preview: "..."},             │
│    │        {tool: "grep", args: {...}, result_preview: "..."},             │
│    │      ],                                                                 │
│    │      sub_agent_result: <compact_summary[:300]>                         │
│    │    }]                                                                  │
│    │  }                                                                     │
│    │                                                                         │
│    │  After "done": INSERT messages (... tool_metadata=json.dumps(...))     │
│    ▼                                                                         │
│  Frontend lib/api.ts:sendMessage  (CONSUMER)                                │
│    │  Parses both old-shape AND new-shape events                            │
│    │  Routes new-shape to: onSubAgentToolStart, onSubAgentToolDone          │
│    ▼                                                                         │
│  Chat.tsx → MessageList.tsx → SubAgentSection                                │
│    │  (Phase 5 minimum: events flow; full nested rendering = Phase 6)       │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Recommended Project Structure

**Recommendation A (preferred — minimal disruption):** Keep `sub_agent.py` as-is (singular file) and add `run_explorer_sub_agent` next to `run_sub_agent`. This matches `research/ARCHITECTURE.md:175` ("`sub_agent.py` extended, not split: the Explorer sub-agent reuses the streaming-Gemini-with-isolated-context pattern that already exists. Adding `run_explorer_sub_agent` next to `run_sub_agent` keeps the two siblings discoverable and lets them share helpers like `MAX_CONTEXT_CHARS`").

**Recommendation B (alternative — defer to planner discussion):** Promote to a package `backend/app/services/sub_agents/{__init__.py, analyze_document.py, explorer.py, _shared.py}`. This is what the orchestrator's prompt mentions ("backend/app/services/sub_agents/analyze_document.py if it exists"). At HEAD, this package does NOT exist (verified by Glob); the singular `sub_agent.py` does (verified by Glob and Read). The planner should choose between A and B explicitly in `05-CONTEXT.md` discuss-phase.

```
backend/app/services/
├── sub_agent.py                  [EDIT — Recommendation A] add run_explorer_sub_agent
│   OR
├── sub_agents/                   [NEW — Recommendation B]
│   ├── __init__.py               re-export run_sub_agent + run_explorer_sub_agent
│   ├── analyze_document.py       moved from sub_agent.py:run_sub_agent
│   ├── explorer.py               NEW: run_explorer_sub_agent + helpers
│   └── _shared.py                MAX_CONTEXT_CHARS, _hash_signature, EXPLORER_SYSTEM_PROMPT
├── exploration_tools/            (Phase 4 — UNCHANGED; Explorer dispatches to these)
│   ├── list_files.py
│   ├── tree.py
│   ├── glob_match.py
│   ├── read_document.py
│   └── grep.py
└── openai_client.py              [EDIT] _build_explore_knowledge_base_tool + dispatch arm

backend/app/routers/
└── messages.py                   [EDIT] event_generator extension + dual-emit + tool_metadata

backend/scripts/
├── test_explorer_sub_agent.py    [NEW — TEST-03]
└── test_all.py                   [EDIT] register ("Explorer", test_explorer_sub_agent)

frontend/src/
├── lib/api.ts                    [EDIT] parse new sub_agent event shape
└── pages/Chat.tsx                [EDIT] wire new callbacks (full UI = Phase 6)
```

The planner-discussion decision (A vs B) only affects file paths; every other piece of this research is invariant.

### Pattern 1: Bounded `for turn in range(MAX_TURNS)` Loop with Wall-Clock Guard

**What:** A multi-turn LLM-tool dispatch loop with three independent termination conditions: hard turn count, wall-clock budget, and no-progress detection.

**When to use:** Any sub-agent that calls tools iteratively. Banned alternative: `while not done:` (Pitfall 7 mitigation 1).

**Example shape** (verified pattern from `research/STACK.md:355-415`, adapted to Phase 4 reality):

```python
# Source: research/STACK.md §5 + sub_agent.py:18-97 (existing run_sub_agent shape)
import time
import json
import hashlib
import logging
from typing import Generator
from google.genai import types
from langsmith import traceable

from app.services.openai_client import _get_client
from app.services.settings import get_llm_model
from app.services.exploration_tools._truncate import apply_12k_cap

logger = logging.getLogger(__name__)

MAX_TURNS = 8
WALL_CLOCK_BUDGET_S = 60.0
RESULT_CHAR_CAP = 12_000

EXPLORER_ALLOWED_TOOLS = ("tree", "glob", "grep", "list_files", "read_document")
# EXPLORER-03: setup-time hard-exclusion. Raises AssertionError at MODULE IMPORT
# if a future maintainer accidentally adds analyze_document to the allowed set.
assert "analyze_document" not in EXPLORER_ALLOWED_TOOLS, (
    "EXPLORER-03 violation: analyze_document MUST NOT be registered in "
    "Explorer's toolset (no recursive sub-agents). See PITFALLS.md Pitfall 7."
)


def _signature(tool_name: str, args: dict) -> str:
    """Stable hash of (tool_name, args) for the no-progress detector.

    Whitespace-insensitive on dict keys via sort_keys=True; stable across
    CPython versions via hashlib.sha256.
    """
    canonical = json.dumps({"tool": tool_name, "args": args}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@traceable(name="explore_knowledge_base", run_type="chain")
def run_explorer_sub_agent(
    query: str,
    user_id: str,
    supabase_client,
) -> Generator[tuple[str, str], None, None]:
    """EXPLORER-01..06: bounded multi-turn loop with the 5 Phase 4 tools.

    Yields:
        ("sub_agent_start", json) — emitted ONCE at entry
        ("sub_agent_tool_start", json) — per tool dispatch
        ("sub_agent_tool_done", json) — per tool result
        ("sub_agent_token", text) — per token of final compact summary
        ("sub_agent_done", final_summary_text) — emitted ONCE at exit
    """
    yield ("sub_agent_start", json.dumps({
        "agent_name": "explore_knowledge_base",
        "question": query,
    }))

    start_time = time.monotonic()
    last_signature = ""
    short_circuit_reason = None

    client = _get_client()
    model = get_llm_model()
    contents = [types.Content(role="user", parts=[types.Part(text=query)])]
    tools = _build_explorer_tool_set()  # 5 Phase 4 tools, NOT analyze_document

    for turn in range(MAX_TURNS):
        # EXPLORER-02: wall-clock timeout
        if time.monotonic() - start_time > WALL_CLOCK_BUDGET_S:
            short_circuit_reason = "wall_clock_timeout"
            logger.warning(f"Explorer wall-clock timeout at turn {turn}")
            break

        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=EXPLORER_SYSTEM_PROMPT,
                    tools=tools,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
        except Exception as e:
            logger.error(f"Explorer Gemini call failed at turn {turn}: {e}")
            short_circuit_reason = "gemini_error"
            break

        fc = _extract_function_call(response)
        if not fc:
            # Natural finish — Gemini emitted text, not a tool call.
            # Use the response text as the summary directly (no second call needed).
            summary = _extract_text(response) or "Exploration complete (no findings)."
            for chunk in summary.split():
                yield ("sub_agent_token", chunk + " ")
            yield ("sub_agent_done", summary)
            return

        # EXPLORER-02: no-progress detector
        sig = _signature(fc.name, dict(fc.args) if fc.args else {})
        if sig == last_signature:
            short_circuit_reason = "no_progress"
            logger.info(f"Explorer no-progress at turn {turn}: repeated {fc.name}")
            break
        last_signature = sig

        yield ("sub_agent_tool_start", json.dumps({
            "tool": fc.name,
            "args": dict(fc.args) if fc.args else {},
            "turn": turn,
        }))

        result_dict = _dispatch_explorer_tool(fc.name, fc.args, user_id, supabase_client)
        # EXPLORER aggressive truncation: 12K char cap (Pitfall 7 mitigation 3)
        truncated_dict = apply_12k_cap(result_dict, char_cap=RESULT_CHAR_CAP)
        truncated_text = json.dumps(truncated_dict, default=str)

        yield ("sub_agent_tool_done", json.dumps({
            "tool": fc.name,
            "result_preview": truncated_text[:300],
            "turn": turn,
        }))

        contents.append(response.candidates[0].content)  # model's tool-call message
        contents.append(types.Content(
            role="user",
            parts=[types.Part(function_response=types.FunctionResponse(
                name=fc.name,
                response={"result": truncated_text},
            ))],
        ))
    else:
        # MAX_TURNS exhausted (Python for-else fires on natural loop exhaustion)
        short_circuit_reason = "max_turns"
        logger.info(f"Explorer hit MAX_TURNS={MAX_TURNS}")

    # Final compact-summary call (NO tools available)
    summary_system = (
        f"{EXPLORER_SYSTEM_PROMPT}\n\n"
        f"Status: {short_circuit_reason or 'complete'}.\n"
        f"Synthesize a COMPACT summary (≤ 8 sentences) of what you found. "
        f"Cite folder paths and document names. Do not echo raw tool output. "
        f"If you stopped early ({short_circuit_reason}), state that explicitly."
    )
    full_summary = ""
    try:
        for chunk in client.models.generate_content_stream(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=summary_system),
        ):
            if chunk.text:
                full_summary += chunk.text
                yield ("sub_agent_token", chunk.text)
    except Exception as e:
        logger.error(f"Explorer summary streaming failed: {e}")
        if not full_summary:
            full_summary = (
                f"Exploration ended ({short_circuit_reason or 'complete'}); "
                f"summary unavailable due to streaming error."
            )

    yield ("sub_agent_done", full_summary)
```

### Pattern 2: Generalized SSE Sub-Agent Event Protocol (Pitfall 12 Mitigation)

**What:** Replace the bespoke `sub_agent_start`/`sub_agent_token`/`sub_agent_done` event types with a parameterized envelope `{type: 'sub_agent', agent_name, event, payload}`. Dual-emit BOTH old-form and new-form events for one release window so the frontend can migrate without breaking.

**When to use:** At the second sub-agent integration point (which is exactly where Phase 5 sits). Pitfall 12 mitigation 1 explicitly mandates "generalize NOW, not later."

**Old shape (current, verified at `messages.py:91-101`):**
```javascript
// Phase 4 / Module 8 reality
{type: "sub_agent_start", document_name: "report.pdf"}
{type: "sub_agent_token", content: "Based on the document..."}
{type: "sub_agent_done"}
```

**New shape (Phase 5 generalized — Pitfall 12 mitigation 1+2):**
```javascript
{type: "sub_agent", agent_name: "analyze_document", event: "start",
 payload: {document_name: "report.pdf", sub_agent_id: "<uuid>"}}
{type: "sub_agent", agent_name: "explore_knowledge_base", event: "start",
 payload: {question: "where are the 2026 floor plans?", sub_agent_id: "<uuid>"}}
{type: "sub_agent", agent_name: "explore_knowledge_base", event: "tool_start",
 payload: {tool: "tree", args: {path: "/projects", max_depth: 2}, turn: 0}}
{type: "sub_agent", agent_name: "explore_knowledge_base", event: "tool_done",
 payload: {tool: "tree", result_preview: "...", turn: 0}}
{type: "sub_agent", agent_name: "explore_knowledge_base", event: "token",
 payload: {content: "Found three candidate folders..."}}
{type: "sub_agent", agent_name: "explore_knowledge_base", event: "done",
 payload: {summary: "..."}}
```

**Dual-emit window:** Phase 5 backend emits BOTH shapes for one release. Phase 6's frontend update consumes the new shape and the old-shape emission is removed in a follow-up commit (LOCKED for Phase 6 plan-checker enforcement).

**Exact list of new event types** (Plan 04 of Phase 5 must implement all six):

| Event | Old type (kept this release) | New type (Phase 5 NEW) | Payload schema |
|-------|------------------------------|------------------------|----------------|
| Sub-agent entry | `sub_agent_start` | `sub_agent` event=`start` | `{agent_name, sub_agent_id, ...agent-specific keys}` |
| Sub-agent inner tool dispatch | (none — NEW) | `sub_agent_tool_start` (legacy fallback) + `sub_agent` event=`tool_start` | `{tool, args, turn}` |
| Sub-agent inner tool result | (none — NEW) | `sub_agent_tool_done` (legacy fallback) + `sub_agent` event=`tool_done` | `{tool, result_preview, turn}` |
| Sub-agent streaming token | `sub_agent_token` | `sub_agent` event=`token` | `{content}` |
| Sub-agent exit | `sub_agent_done` | `sub_agent` event=`done` | `{summary?}` |

**Why both `sub_agent_tool_start` legacy AND `sub_agent` event=`tool_start`:** The legacy event types (with `_tool_*` suffix) are NEW in Phase 5 — frontend has never seen them. So technically `sub_agent_tool_*` could be Phase-5-only and skip the dual-emit. **However**, dual-emitting them in the generalized shape too means Phase 6's frontend overhaul only listens to ONE channel (the generalized envelope) and the dual-emit cleanup in Phase 6 removes both legacy emissions in one shot. Cleaner cleanup boundary. Recommendation: **dual-emit for one release**.

### Pattern 3: tool_metadata JSONB Persistence — Recursive Schema

**What:** Persist Explorer's full trace (per-turn tool calls + final summary) into the existing `messages.tool_metadata JSONB` column so reloading an old chat shows the nested trace correctly.

**When to use:** EXPLORER-05 contract — old chats must render correctly on reload.

**Existing schema (verified at HEAD: `messages.py:93,98-100`, `frontend/src/lib/api.ts:39-46`):**
```json
{
  "tools_used": [
    {
      "tool": "analyze_document",
      "document_name": "report.pdf",
      "sub_agent_result": "Based on the document, key findings are..."
    }
  ]
}
```

**Phase 5 extension (additive — Module 8 schema preserved bit-for-bit):**
```json
{
  "tools_used": [
    {
      "tool": "explore_knowledge_base",
      "sub_agent_id": "<uuid>",
      "question": "where are the 2026 floor plans?",
      "tool_calls": [
        {"tool": "tree", "args": {"path": "/projects", "max_depth": 2},
         "result_preview": "found 3 folders..."},
        {"tool": "grep", "args": {"pattern": "floor plan"},
         "result_preview": "5 hits in /projects/2026/..."}
      ],
      "short_circuit_reason": null,
      "sub_agent_result": "Three candidate folders identified..."
    }
  ]
}
```

A message with BOTH `analyze_document` and `explore_knowledge_base` calls (Pitfall 12 mitigation 6 test case) populates `tools_used` with two entries — one per call.

### Anti-Patterns to Avoid

- **Recursive sub-agents** (Explorer calling `analyze_document` calling Explorer): "Easy to write, hard to debug, blows the trace tree." (PITFALLS.md Pitfall 7 mitigation; STACK.md §5 "What NOT to do"). EXPLORER-03 enforces this with a setup-time `assert`.
- **`while not done:` Explorer loop**: Pitfall 7 mitigation 1 explicitly requires `for turn in range(N)`. A `while` loop can be silently mutated to remove the bound.
- **`signal.alarm` for timeout**: Windows non-portable; cannot be raised from inside C extension code. Use `time.monotonic()` polling.
- **Sharing parent's `contents` (chat history) with the Explorer**: PITFALLS.md / STACK.md §5: "The Explorer is **isolated context** — it gets only `question`, not the parent's chat history. This is the whole point of sub-agents (token cost containment)."
- **Inventing a parallel context-injection path for Explorer's summary**: Phase 4 TOOL-09 invariant. The compact summary IS a tool result. It flows through `openai_client.py:1070,1146` `truncated_result = result_text[:16000]` UNCHANGED.
- **`automatic_function_calling=enable`**: STACK.md §3 — "Loses per-tool tracing and SSE forwarding hooks." Keep `disable=True` and run the manual loop (matches Phase 1–4).
- **Frontend `if (agentType === 'explorer')` branch in `SubAgentSection`**: Pitfall 12 warning sign #1. Phase 5 lays the protocol foundation; Phase 6 must extend `SubAgentSection` recursively without forking.
- **New SSE event types with `explorer_*` prefix**: Pitfall 12 warning sign #2. Use the generalized envelope.
- **Per-turn LangSmith `with trace(...)` context manager**: not needed. `@traceable` decorators on the Phase 4 tools are picked up via contextvars automatically.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Multi-turn LLM loop | LangGraph state machine | `for turn in range(MAX_TURNS)` + `contents.append(...)` (this codebase's existing pattern) | Project rule (CLAUDE.md): no LangChain/LangGraph. STACK.md §5: "Explorer is a **linear loop** — model emits a tool call, we run it, append the response, repeat. No branching, no parallel fan-out." |
| LangSmith span hierarchy | Manual `with trace(...)` blocks | `@traceable(run_type="chain")` on entry + existing `@traceable` decorators on tools (contextvars propagation) | Already 9 sites in codebase. STACK.md §5: "`@traceable` on the outer function nests the tool calls under the chain in the LangSmith UI exactly like `_execute_search_documents` is nested under `gemini_chat` today." |
| 12K char cap on tool results | Inline `text[:12_000]` truncation | `apply_12k_cap()` from `exploration_tools/_truncate.py` (Phase 4) | Already UTF-8 codepoint-safe + truncation_marker discipline. Phase 4 Plan 02 verified at HEAD. |
| Tool dispatch (5 tools) | New dispatcher module | Reuse Phase 4's existing tool functions: `from app.services.exploration_tools.{X} import {X}` | Phase 4 functions already accept `(parsed_args, user_id, supabase_client) -> dict`; identical contract. Zero new business logic. |
| Tool argument validation | Manual dict-key checks | Reuse Phase 4's Pydantic schemas: `from app.services.exploration_tools.schemas import {TreeArgs, GlobArgs, GrepArgs, ListFilesArgs, ReadDocumentArgs}` | TOOL-06 invariant; same `Literal["user","global","both"]` + `Field(ge=,le=)` discipline |
| LangSmith trace assertions in tests | Custom span-capture middleware | LangSmith Python SDK's `Client().list_runs(project_name, run_type="chain", ...)` query OR in-process span capture via `langsmith.run_helpers` callback | TEST-03 needs CI assertion that Explorer span never has > 8 tool-call children. SDK query is the canonical pattern; in-process capture is faster but newer (see §Test Strategy for tradeoffs). |
| New SQL migration for tool_metadata extension | Migration 021 | Use existing `messages.tool_metadata JSONB` column (Module 8 / Migration 010) | Verified at HEAD: column exists, JSONB allows arbitrary nesting; no DDL needed |
| Wall-clock timeout | `signal.alarm` | `time.monotonic()` polling at known yield points | Windows-portable; the loop is generator-based, so polling at the top of each iteration is sufficient |
| No-progress detection state | Async/threading lock | Local closure variable + `hashlib.sha256` | Single-request scope; no shared state |

**Key insight:** Phase 5 is **plumbing, not new logic**. Every "hard" piece (multi-turn loop shape, tracing, validation, truncation, dispatch, persistence column) has a precedent in Phases 1–4 or in shipped Module 8 code. The only **net-new** code is: (1) the loop's `for/else/break` discipline, (2) the no-progress detector, (3) the wall-clock check, (4) the setup-time assert, (5) the SSE event-protocol generalization, (6) the dual-emit window, (7) the `tool_calls: [...]` JSONB schema extension. Each is small, focused, and testable independently.

---

## Sub-Agent Loop Architecture (Detailed Answer to Question #1)

**Concrete shape of `run_explorer_sub_agent(query, user_id, supabase_client) -> Generator[tuple[str, str], None, None]`:**

| Concern | Phase 5 design | Why |
|---------|---------------|-----|
| **Function signature** | `(query: str, user_id: str, supabase_client) -> Generator[tuple[str, str], None, None]` — matches `run_sub_agent` (`sub_agent.py:19-25`) bit-for-bit | Same caller in `openai_client.py` dispatch arm; same SSE forwarding in `messages.py`; zero new plumbing |
| **Bounded loop** | `for turn in range(MAX_TURNS)` where `MAX_TURNS = 8` (module-level constant) | EXPLORER-01 + Pitfall 7 mitigation 1; for-else clause handles MAX_TURNS exhaustion |
| **Wall-clock timeout** | `start = time.monotonic()` once outside the loop; `if time.monotonic() - start > 60.0: break` checked at top of each turn | EXPLORER-02; Windows-portable; precise enough for 60s budget; no signal handler needed |
| **No-progress detector** | `last_signature: str = ""` closure variable; per turn: `sig = hashlib.sha256(json.dumps({"tool": fc.name, "args": dict(fc.args)}, sort_keys=True).encode()).hexdigest()`; `if sig == last_signature: break`; `last_signature = sig` | Pitfall 7 mitigation 2; deterministic; whitespace-insensitive via `sort_keys=True`; stable across CPython versions |
| **Tool dispatch** | Inline `if/elif tool_name == "tree": ...` block (5 arms) calling Phase 4 tool functions with the existing Pydantic-args validation pattern | Identical to `openai_client.py:917-1062` dispatch arms; reused verbatim. Plan 02 may extract to `_dispatch_explorer_tool()` helper for testability |
| **Tool result truncation** | `truncated = apply_12k_cap(result_dict, char_cap=12_000)` — even more aggressive than the main agent's 16K cap | Pitfall 7 mitigation 3 ("more aggressive than main agent's view"); Pitfall 8 mitigation 2 (sanitize before injection) |
| **Compact summary** | Final `client.models.generate_content_stream(...)` call AFTER loop exit, with NO tools registered, system prompt instructing: "Synthesize ≤ 8 sentences. Cite paths/names. State if early-stopped." | Streamed via `sub_agent_token` events — frontend renders as the Explorer's "answer" line; STACK.md §5 verified pattern |
| **Compact summary returns to main agent** | `result_text = full_summary` is set in `openai_client.py` dispatch arm (just like `analyze_document`'s arm at L915); `result_text` then flows into the unchanged layered-fallback wrapper at L1070 | TOOL-09 invariant; same pattern Module 8 uses |
| **Existing analog** | `run_sub_agent` at `sub_agent.py:18-97` is the singular precedent — same generator shape, same `("sub_agent_start", ...)` / `("sub_agent_token", ...)` / `("sub_agent_done", ...)` event vocabulary | "Add `run_explorer_sub_agent` next to `run_sub_agent`" — `research/ARCHITECTURE.md:175` |
| **Error handling** | `try/except Exception` around each Gemini call inside the loop; on failure, log + break + still emit `sub_agent_done`; **never** raise out of the generator | Generators that raise corrupt the SSE stream and break the FastAPI response; mirrors `sub_agent.py:92-95` failure path |

---

## Tool Registration Boundary (Detailed Answer to Question #2)

**Where Explorer's allowed-tool list lives** — three layers of defense:

1. **Module-level constant** (Plan 02 of Phase 5):
   ```python
   # backend/app/services/sub_agent.py (or sub_agents/explorer.py)
   EXPLORER_ALLOWED_TOOLS = ("tree", "glob", "grep", "list_files", "read_document")

   # EXPLORER-03 layer 1: setup-time assertion (fires at IMPORT time)
   assert "analyze_document" not in EXPLORER_ALLOWED_TOOLS, (
       "EXPLORER-03 violation: analyze_document MUST NOT be registered. "
       "Recursive sub-agents are forbidden — see PITFALLS.md Pitfall 7."
   )
   ```

2. **Tool-set builder** (Plan 02 of Phase 5):
   ```python
   def _build_explorer_tool_set() -> list[types.Tool]:
       """Constructs the 5-tool registration for the Explorer's Gemini call.

       EXPLORER-03 layer 2: explicitly enumerated, no dynamic registration.
       """
       from app.services.openai_client import (
           _build_list_files_tool, _build_tree_tool, _build_glob_tool,
           _build_read_document_tool, _build_grep_tool,
       )
       declarations = [
           _build_list_files_tool(),
           _build_tree_tool(),
           _build_glob_tool(),
           _build_read_document_tool(),
           _build_grep_tool(),
       ]
       # EXPLORER-03 layer 3: runtime guard (defense-in-depth in case future
       # tools are added to EXPLORER_ALLOWED_TOOLS but the function-call dispatch
       # branch is incomplete).
       names = {fd.name for fd in declarations}
       assert names == set(EXPLORER_ALLOWED_TOOLS), (
           f"Explorer tool-set drift: declared={names}, allowed={EXPLORER_ALLOWED_TOOLS}"
       )
       assert "analyze_document" not in names, "EXPLORER-03 violation"
       return [types.Tool(function_declarations=declarations)]
   ```

3. **Dispatch-time tool-name check**:
   ```python
   def _dispatch_explorer_tool(tool_name, args, user_id, supabase_client):
       if tool_name not in EXPLORER_ALLOWED_TOOLS:
           # If Gemini hallucinates a tool name not in our registration, fail loudly
           return {"error": "TOOL_NOT_ALLOWED_IN_EXPLORER", "tool": tool_name}
       # ... dispatch to Phase 4 tool function
   ```

**Where the main agent's tool registry lives:** `backend/app/services/openai_client.py` lines 644–689 (`function_declarations = []` block in `stream_response`). Each `_build_*_tool()` factory function returns a `types.FunctionDeclaration`. The list is conditionally extended with `analyze_document`, `search_documents`, the five precision tools, `query_structured_data`, `web_search`, and (Phase 5 NEW) `explore_knowledge_base`. Phase 5 adds **one** factory function (`_build_explore_knowledge_base_tool`) and **one** conditional appender alongside the others.

**Where the setup-time error fires:** at module import — i.e., the FIRST time `from app.services.sub_agent import run_explorer_sub_agent` is executed (which happens lazily in the dispatch arm: `from app.services.sub_agent import run_explorer_sub_agent` inside the `elif tool_name == "explore_knowledge_base":` arm). On the very first chat that triggers the Explorer, if the assertion is broken, the import raises `AssertionError` and the dispatch arm fails. Because the import is lazy (inside the `elif`), the rest of the application continues to work. **The TEST-03 suite must explicitly import `run_explorer_sub_agent` at module top to surface this error in CI even before any chat triggers it.**

---

## SSE Protocol Generalization Strategy (Detailed Answer to Question #3)

**Current event shape (verified at HEAD):**

| Source | Lines | Events |
|--------|-------|--------|
| `sub_agent.py:19-97` | yields `("sub_agent_start", json_payload)`, `("sub_agent_token", text)`, `("sub_agent_done", text)` | 3 event types |
| `openai_client.py:911-915,1138-1143` | passes through these 3 event types via `for evt_type, evt_data in run_sub_agent(...): yield (evt_type, evt_data)` | none added |
| `messages.py:91-101` | converts to SSE JSON: `{type: 'sub_agent_start', document_name}`, `{type: 'sub_agent_token', content}`, `{type: 'sub_agent_done'}`; accumulates `tool_metadata = {"tools_used": [{"document_name": ..., "tool": "analyze_document", "sub_agent_result": data[:300]}]}` | tool_metadata persistence |
| `frontend/src/lib/api.ts:282-287` | parses `sub_agent_start` → `onSubAgentStart`; `sub_agent_token` → `onSubAgentToken`; `sub_agent_done` → `onSubAgentDone` | 3 callbacks |
| `frontend/src/components/MessageList.tsx:18-50` | `SubAgentSection` renders `documentName`, `content`, `isActive`, `defaultExpanded` from these callbacks + `msg.tool_metadata.tools_used` array | renders ONE document-name + content stream per sub-agent |

**Phase 5 generalized shape (Plan 04):**

The event payload sent over the wire is generalized to:
```json
{"type": "sub_agent",
 "agent_name": "explore_knowledge_base" | "analyze_document",
 "event": "start" | "tool_start" | "tool_done" | "token" | "done",
 "payload": {<event-specific keys>}}
```

**Dual-emit approach (one release window):**

`messages.py:event_generator` emits BOTH the old and new shapes for one release:
```python
elif event_type == "sub_agent_start":
    parsed = json.loads(data)
    sub_agent_id = str(uuid.uuid4())
    # Init accumulator slot
    if not tool_metadata:
        tool_metadata = {"tools_used": []}
    agent_name = parsed.get("agent_name", "analyze_document")  # legacy events have no agent_name
    slot = {"tool": agent_name, "sub_agent_id": sub_agent_id, "tool_calls": []}
    if agent_name == "analyze_document":
        slot["document_name"] = parsed.get("document_name", "")
    elif agent_name == "explore_knowledge_base":
        slot["question"] = parsed.get("question", "")
    tool_metadata["tools_used"].append(slot)

    # OLD shape (kept for one release — BACKWARDS-COMPAT)
    yield json.dumps({"type": "sub_agent_start", **parsed})
    # NEW shape (Phase 5 NEW)
    yield json.dumps({
        "type": "sub_agent",
        "agent_name": agent_name,
        "event": "start",
        "payload": {"sub_agent_id": sub_agent_id, **parsed},
    })

elif event_type == "sub_agent_tool_start":
    parsed = json.loads(data)
    if tool_metadata and tool_metadata["tools_used"]:
        # Append to current sub-agent's tool_calls
        slot = tool_metadata["tools_used"][-1]
        slot.setdefault("tool_calls", []).append({
            "tool": parsed["tool"],
            "args": parsed.get("args", {}),
        })
    # Phase 5 NEW emits both legacy form AND generalized form
    yield json.dumps({"type": "sub_agent_tool_start", **parsed})
    yield json.dumps({
        "type": "sub_agent",
        "agent_name": "explore_knowledge_base",  # only Explorer emits these
        "event": "tool_start",
        "payload": parsed,
    })

elif event_type == "sub_agent_tool_done":
    parsed = json.loads(data)
    if tool_metadata and tool_metadata["tools_used"]:
        slot = tool_metadata["tools_used"][-1]
        if slot.get("tool_calls"):
            # Update the last in-flight tool_call
            slot["tool_calls"][-1]["result_preview"] = parsed.get("result_preview", "")[:300]
    yield json.dumps({"type": "sub_agent_tool_done", **parsed})
    yield json.dumps({
        "type": "sub_agent",
        "agent_name": "explore_knowledge_base",
        "event": "tool_done",
        "payload": parsed,
    })

elif event_type == "sub_agent_token":
    yield json.dumps({"type": "sub_agent_token", "content": data})
    yield json.dumps({
        "type": "sub_agent",
        "event": "token",
        "payload": {"content": data},
    })

elif event_type == "sub_agent_done":
    if tool_metadata and tool_metadata["tools_used"]:
        slot = tool_metadata["tools_used"][-1]
        slot["sub_agent_result"] = data[:300]
    yield json.dumps({"type": "sub_agent_done"})
    yield json.dumps({
        "type": "sub_agent",
        "event": "done",
        "payload": {"summary": data[:300]},
    })
```

**Frontend file(s) that consume these events:** `frontend/src/lib/api.ts` lines 282–287 (the `parsed.type === 'sub_agent_*'` branches in the SSE consumer loop) — Plan 05 of Phase 5 adds new `parsed.type === 'sub_agent_tool_start'` and `'sub_agent_tool_done'` branches plus a generalized `parsed.type === 'sub_agent'` branch that routes by `parsed.event`. The Phase 5 frontend update is **minimal callback wiring**; full nested-tree rendering inside `SubAgentSection` is **Phase 6's UI-10 deliverable**.

**Dual-emit duration:** ONE release. Phase 5 ships dual-emit; Phase 6's frontend update consumes the generalized shape; the legacy emissions are removed in Phase 6's plan-checker as a CLOCK in the same commit window. Without Phase 6 catching the dual-emit cleanup, the protocol fork debt persists indefinitely (Pitfall 12 antipattern).

---

## LangSmith Span Structure (Detailed Answer to Question #4)

**How to emit a `chain` span with nested `tool` children:**

```python
@traceable(name="explore_knowledge_base", run_type="chain")
def run_explorer_sub_agent(query: str, user_id: str, supabase_client) -> Generator:
    # The 5 Phase 4 tools are ALREADY @traceable(run_type="tool") decorated:
    #   list_files.py:32, tree.py:34, glob_match.py:48, read_document.py:39, grep.py:46
    # When invoked from inside this chain span, LangSmith's contextvars-based
    # context propagation picks them up as nested children automatically.
    # NO manual `with trace(...)` block needed.
    ...
    # Inside the loop:
    result = list_files(parsed_args, user_id, supabase_client)  # auto-nests as child span
    ...
```

**Verified pattern in this codebase:** `_execute_search_documents` at `openai_client.py:553` is `@traceable(name="search_documents", run_type="tool")`; it's called from inside `stream_response` which is `@traceable(name="gemini_chat", run_type="llm")`; LangSmith renders search_documents nested under gemini_chat with no extra plumbing. STACK.md §5 confirms: "This nests the tool calls under the chain in the LangSmith UI exactly like `_execute_search_documents` is nested under `gemini_chat` today."

**Current `agent.py` pattern for spans:** This codebase has NO `agent.py`. The orchestrator's prompt mentions `backend/app/services/agent.py` but it does NOT exist (verified by Glob). The orchestrator's references to `agent.py` should be read as `backend/app/services/openai_client.py` — that file is where `stream_response()` (the agent loop) lives. Phase 4 SUMMARY documents confirm this.

**Phase 4 tool tracing (verified):**
- `backend/app/services/exploration_tools/list_files.py:32` — `@traceable(name="list_files", run_type="tool")`
- `backend/app/services/exploration_tools/tree.py:34` — `@traceable(name="tree", run_type="tool")`
- `backend/app/services/exploration_tools/glob_match.py:48` — `@traceable(name="glob", run_type="tool")`
- `backend/app/services/exploration_tools/read_document.py:39` — `@traceable(name="read_document", run_type="tool")`
- `backend/app/services/exploration_tools/grep.py:46` — `@traceable(name="grep", run_type="tool")`

All five are picked up automatically as children of the Explorer's chain span. EXPLORER-06 is satisfied by **a single `@traceable` decorator** on `run_explorer_sub_agent` — no other code changes for tracing.

**CI assertion for "Explorer span never has > 8 tool-call children":** See §Test Strategy.

---

## Explorer System Prompt Design (Detailed Answer to Question #5)

The Explorer's system prompt MUST state the budget and stopping criteria explicitly (Pitfall 7 mitigation 4: "Sub-agent system prompt explicitly instructs ... 'You have at most 6 tool calls'..." — Phase 5 uses 8 turns per the locked phase brief, generalized).

**Draft (Plan 02 of Phase 5 should land verbatim or near-verbatim):**

```python
# backend/app/services/sub_agent.py (or sub_agents/explorer.py)
EXPLORER_SYSTEM_PROMPT = """You are an isolated knowledge-base exploration sub-agent.

Your job: given a user question that requires open-ended exploration of a document
knowledge base, use the precision tools below to locate the relevant information,
then return a COMPACT summary.

Available tools (5 only):
- tree(path, max_depth, scope) — see folder structure
- list_files(path, scope) — list one folder one level deep
- glob(pattern, path, type, scope) — find files by name pattern (e.g. '**/*.pdf')
- grep(pattern, path, scope, output_mode, A, B, C) — search inside document text
- read_document(document_id|path, offset, limit) — read line-numbered slice

HARD LIMITS (do not exceed):
- 8 tool calls maximum across this whole exploration
- 60 seconds wall-clock time
- Each tool result you receive is capped at 12,000 characters; if you see a
  truncation marker, your next call should NARROW (e.g. add `path` filter,
  reduce `max_depth`, narrow regex)

STRATEGY:
1. Start broad (tree at depth 2 or list_files at the most likely root).
2. Narrow to 1-2 candidate folders quickly — prefer adding `path` filter over
   re-searching with broader scope.
3. If you find the target, call read_document or grep with tight bounds to
   confirm and gather quotes.
4. STOP as soon as you have enough to answer. Do NOT call additional tools
   "just to be thorough" — token budget is precious.

DO NOT:
- Repeat the same tool call with the same arguments (no progress).
- Use analyze_document — it is NOT in your toolset (recursive sub-agents
  are forbidden).
- Echo raw tool output verbatim. Synthesize.

When you are ready, RESPOND WITH PLAIN TEXT (no further tool calls). Your text
will be the compact summary returned to the main agent.

Compact-summary format:
- ≤ 8 sentences
- Cite folder paths and document names with the scope tag (user|global)
- If you stopped early, say why ('hit turn budget', 'found enough', etc.)
"""
```

**Key design choices:**
- States 8-turn budget AND 60s budget AND 12K result cap explicitly (Pitfall 7 mitigation 4).
- Names the 5 tools and explicitly excludes `analyze_document` (Pitfall 7 mitigation; EXPLORER-03 belt-and-suspenders).
- Instructs the model to return PLAIN TEXT when done (the loop's natural-finish branch).
- Instructs against repeating same call (the no-progress detector is the enforcer; the prompt is the request).
- Tells model to cite scope tag — preserves Phase 4 Pitfall 11 mitigation through the sub-agent.

---

## Test Strategy for TEST-03 (Detailed Answer to Question #6)

**TEST-03 (verbatim from REQUIREMENTS.md L86):** `test_explorer_sub_agent.py` — MAX_TURNS bound, timeout, no-progress detector, recursive-sub-agent rejection.

### Fixture Query Engineering — "Deliberately Broad" Without Flaking

A "deliberately broad" query needs to reliably trigger ≥ 4 tool calls but ≤ 8. Engineering approach:

1. **Seeded fixture corpus:** Test setup creates 12 documents across 4 folders with known content (uses the `_service_role_client()` + `_track_doc()` pattern from `test_folders.py`):
   - `/projects/2025/floor-plans/main.pdf` — content includes "floor plan"
   - `/projects/2026/floor-plans/north-wing.pdf` — content includes "floor plan, electrical"
   - `/projects/2026/specs/electrical-spec-v3.md` — content includes "panel MDB-C-G3"
   - `/shared/standards/iec-61439.md` — content includes "panel rating IEC 61439"
   - ... 8 more docs in `/projects/2024/`, `/shared/templates/`, etc.
2. **Fixture query:** "Find all documents about electrical panel ratings across both my projects and shared standards." — broad enough to trigger `tree` → `grep` → maybe `read_document`, narrow enough that 8 turns is plenty.
3. **Anti-flake measures:** assert `total_tool_calls IN range(2, 9)` (not exactly 4) — Gemini's exact tool sequencing is non-deterministic; the testable invariants are the BOUNDS, not the exact sequence.

### Asserting the 8-Turn Bound (TEST-03 main case)

Two independent paths — use both for defense in depth:

**Path A — In-process counter (preferred for CI speed):** Wrap the dispatch helper with a counter:
```python
# In test
import app.services.sub_agent as sa
original_dispatch = sa._dispatch_explorer_tool
call_count = 0
def counting_dispatch(*args, **kwargs):
    nonlocal call_count
    call_count += 1
    return original_dispatch(*args, **kwargs)
sa._dispatch_explorer_tool = counting_dispatch
try:
    list(sa.run_explorer_sub_agent(query, user_id, sb))
    assert call_count <= 8
finally:
    sa._dispatch_explorer_tool = original_dispatch
```

**Path B — LangSmith SDK query (canonical / source of truth):**
```python
# In test, after running the chat E2E
from langsmith import Client
ls = Client()
runs = list(ls.list_runs(
    project_name=os.environ.get("LANGSMITH_PROJECT", "default"),
    run_type="chain",
    filter='eq(name, "explore_knowledge_base")',
    start_time=test_start_time,
))
assert len(runs) >= 1
explorer_run = runs[0]
tool_children = [r for r in ls.list_runs(parent_run_id=explorer_run.id, run_type="tool")]
assert len(tool_children) <= 8, f"Explorer span has {len(tool_children)} tool children"
# Each tool result < 12K
for tr in tool_children:
    output_size = len(json.dumps(tr.outputs)) if tr.outputs else 0
    assert output_size <= 12_500, f"Tool result {output_size} > 12K"  # 500 char buffer for JSON wrapping
```

The LangSmith SDK path requires `LANGSMITH_API_KEY` and `LANGSMITH_TRACING=true` in the test env — gracefully `SKIP` if missing (matches Phase 3 / FOLDER-03 transactional rollback test SKIP pattern when DATABASE_URL is missing).

### Testing the Wall-Clock Timeout (60s)

**Hard test (slow — 60s minimum):** Set `MAX_TURNS=999` via monkeypatch + use a fixture corpus that takes ~1s per turn → assert loop exits in ~60s. This adds 60s to the test run; only run in nightly CI, not per-commit.

**Fast test (preferred for per-commit):** Monkeypatch `WALL_CLOCK_BUDGET_S = 0.1`, run a fixture query, assert that the loop exits with `short_circuit_reason == "wall_clock_timeout"` after 1 or 2 turns. Test takes < 1s.

### Testing the No-Progress Detector Deterministically

Monkeypatch the Gemini client to return the SAME function call twice in a row:

```python
# In test
class StubResponse:
    def __init__(self, name, args):
        self.candidates = [type('C', (), {
            'content': type('Ct', (), {
                'parts': [type('Pt', (), {'function_call': type('FC', (), {
                    'name': name, 'args': args
                })()})()]
            })()
        })()]

def stub_generate_content(model, contents, config):
    return StubResponse("tree", {"path": "/", "max_depth": 2})

# Monkeypatch _get_client().models.generate_content
sa._get_client = lambda: type('C', (), {'models': type('M', (), {
    'generate_content': stub_generate_content,
    'generate_content_stream': lambda **kw: iter([type('Ck', (), {'text': 'summary'})()])
})()})()

events = list(sa.run_explorer_sub_agent("test query", user_id, sb))
tool_starts = [e for e in events if e[0] == "sub_agent_tool_start"]
# First call goes through, second call is detected as repeat
assert len(tool_starts) == 1, f"Expected 1 tool_start before short-circuit, got {len(tool_starts)}"
```

### Testing Recursive Sub-Agent Rejection (EXPLORER-03)

Three distinct test cases:

```python
# 1. EXPLORER_ALLOWED_TOOLS does not contain analyze_document
from app.services.sub_agent import EXPLORER_ALLOWED_TOOLS
assert "analyze_document" not in EXPLORER_ALLOWED_TOOLS

# 2. Tool-set builder excludes it
tools = sa._build_explorer_tool_set()
fd_names = {fd.name for tool in tools for fd in tool.function_declarations}
assert "analyze_document" not in fd_names
assert fd_names == {"tree", "glob", "grep", "list_files", "read_document"}

# 3. Setup-time assertion fires if tampered
import importlib
import sys
# Save and tamper
original = sys.modules['app.services.sub_agent']
try:
    # Reload with tampered constant — should raise AssertionError at import
    sa.EXPLORER_ALLOWED_TOOLS = ("tree", "glob", "grep", "list_files", "read_document", "analyze_document")
    importlib.reload(sa)
    assert False, "Expected AssertionError on tampered EXPLORER_ALLOWED_TOOLS"
except AssertionError:
    pass  # Expected
finally:
    importlib.reload(sa)  # Restore clean state
```

### Testing the Generalized SSE Shape

```python
# Send a chat that triggers Explorer
events = stream_sse_full(token, thread_id, "Find docs about electrical panels", timeout=120)
event_types = [e.get("type") for e in events]

# OLD shape still emitted (backwards-compat)
assert "sub_agent_start" in event_types
assert "sub_agent_token" in event_types
assert "sub_agent_done" in event_types
assert "sub_agent_tool_start" in event_types
assert "sub_agent_tool_done" in event_types

# NEW shape emitted
sub_agent_events = [e for e in events if e.get("type") == "sub_agent"]
assert len(sub_agent_events) >= 5  # start + ≥1 tool_start/done pair + token(s) + done
# Schema check
for e in sub_agent_events:
    assert "agent_name" in e
    assert e["event"] in ("start", "tool_start", "tool_done", "token", "done")
    assert "payload" in e
```

### Testing tool_metadata JSONB Persistence and Reload

```python
# After Explorer chat completes:
msgs = requests.get(f"{BASE_URL}/api/threads/{thread_id}/messages", headers=headers).json()
last_assistant = [m for m in msgs if m["role"] == "assistant"][-1]
tm = last_assistant.get("tool_metadata")
assert tm is not None
tools_used = tm.get("tools_used", [])
assert len(tools_used) == 1
assert tools_used[0]["tool"] == "explore_knowledge_base"
assert "tool_calls" in tools_used[0]
assert len(tools_used[0]["tool_calls"]) >= 1
for tc in tools_used[0]["tool_calls"]:
    assert tc["tool"] in ("tree", "glob", "grep", "list_files", "read_document")
    assert "result_preview" in tc

# Both-sub-agents-in-one-conversation test (Pitfall 12 mitigation 6)
# msg #1: triggers analyze_document
# msg #2: triggers explore_knowledge_base
# Both messages must have correct tool_metadata; reload (GET /messages) must show both
```

### Test Suite Structure (Plan 06)

`backend/scripts/test_explorer_sub_agent.py` — mirrors `test_exploration_tools.py` and `test_folders.py` shape:
1. Module imports + `_tracked_*` lists + helpers (`_track_doc`, `_track_folder`, `_service_role_client`, `_verify_phase5_setup` canary)
2. **Section 1**: Setup — seed 12-doc fixture corpus (broad-query precondition)
3. **Section 2**: EXPLORER-01 — MAX_TURNS bound (Path A counter)
4. **Section 3**: EXPLORER-02 — wall-clock timeout (fast test via monkeypatch)
5. **Section 4**: EXPLORER-02 — no-progress detector (stub-response test)
6. **Section 5**: EXPLORER-03 — analyze_document exclusion (3 sub-tests)
7. **Section 6**: EXPLORER-04 — generalized SSE shape (legacy + new)
8. **Section 7**: EXPLORER-04 — both-sub-agents-in-one-conversation
9. **Section 8**: EXPLORER-05 — tool_metadata JSONB persistence + reload
10. **Section 9**: EXPLORER-06 — LangSmith span structure (LangSmith SDK; SKIPs without API key)
11. **Section 10**: Pitfall 8 / TOOL-09 carry-forward — adversarial 50K-char Explorer summary still flows through layered-fallback wrapper without empty-response failure
12. Cleanup section (per-id `.delete().eq()` in finally; CLAUDE.md discipline)

Register in `test_all.py` SUITES: `("Explorer", test_explorer_sub_agent)` after `("Exploration", test_exploration_tools)` (suite count 16 → 17).

---

## Files To Be Created/Modified (Detailed Answer to Question #7)

| File | Phase 5 Action | Plan | Rationale |
|------|----------------|------|-----------|
| `backend/app/services/sub_agent.py` | **EDIT** (Recommendation A) — add `run_explorer_sub_agent`, `_build_explorer_tool_set`, `_dispatch_explorer_tool`, `_extract_function_call`, `_extract_text`, `_signature` helpers, `EXPLORER_ALLOWED_TOOLS`, `EXPLORER_SYSTEM_PROMPT`, `MAX_TURNS`, `WALL_CLOCK_BUDGET_S`, `RESULT_CHAR_CAP` constants, setup-time `assert` | 02 | Sibling to existing `run_sub_agent`; `research/ARCHITECTURE.md:175` recommends extending vs splitting |
| `backend/app/services/sub_agents/{__init__,analyze_document,explorer,_shared}.py` | **NEW package** (Recommendation B — alternative) — split `sub_agent.py` into a package | 02 alt | If planner prefers this layout (orchestrator's prompt mentions `sub_agents/` dir which doesn't exist at HEAD); decision belongs in `05-CONTEXT.md` discuss-phase |
| `backend/app/services/openai_client.py` | **EDIT** — add `_build_explore_knowledge_base_tool` factory (~70 LOC after `_build_grep_tool`); add `function_declarations.append(_build_explore_knowledge_base_tool())` in the `if has_documents:` block; add `elif tool_name == "explore_knowledge_base":` dispatch arm (~25 LOC, mirrors `analyze_document` arm at L892); update `_build_system_prompt()` adding "When the user's question is open-ended exploration ... use `explore_knowledge_base`" guidance | 03 | Same additive `elif` arm pattern as Phase 4; existing precedent for forwarding sub-agent generator events at L911-915 |
| `backend/app/routers/messages.py` | **EDIT** — extend `event_generator` with new `elif event_type == "sub_agent_tool_start"` and `"sub_agent_tool_done"` arms; refactor `tool_metadata` accumulator to support `tool_calls: [...]` array; emit BOTH old and new SSE shapes (dual-emit for one release window) | 04 | EXPLORER-04 + EXPLORER-05; backwards-compat per Pitfall 12 mitigation |
| `frontend/src/lib/api.ts` | **EDIT** — extend `Message` interface to include `tool_calls?: Array<...>`; add `parsed.type === 'sub_agent_tool_start'`/`'sub_agent_tool_done'`/`'sub_agent'` branches in SSE consumer; add new callbacks `onSubAgentToolStart`, `onSubAgentToolDone` to `sendMessage` signature | 05 | Frontend callback wiring; full UI rendering is Phase 6's UI-10 deliverable |
| `frontend/src/pages/Chat.tsx` | **EDIT (minimal)** — wire new callbacks to `setToolSteps` (tool steps array under the active sub-agent) — minimum viable; rich rendering deferred to Phase 6 | 05 | Phase 5 unblocks Phase 6 by ensuring the events flow through; Phase 6 owns the visual presentation |
| `backend/scripts/test_explorer_sub_agent.py` | **NEW** — TEST-03 integration suite, ~600 LOC, 10 sections | 06 | Mirrors `test_exploration_tools.py` and `test_folders.py` shape verbatim |
| `backend/scripts/test_all.py` | **EDIT** — `import test_explorer_sub_agent` + append `("Explorer", test_explorer_sub_agent)` to SUITES (16 → 17) | 06 | Same one-liner pattern Plan 04-09 used |
| `backend/migrations/*.sql` | **NONE** — no new migration | — | `messages.tool_metadata` JSONB already exists; Explorer adds zero new SQL |
| `requirements.txt` | **NONE** — no new package | — | All deps already present |

**Pattern-mapper inputs (for `05-PATTERNS.md`):**

| New/Modified File | Closest Analog | Match Quality |
|---|---|---|
| `sub_agent.py` (extended) | itself — `run_sub_agent` (`sub_agent.py:18-97`) | exact |
| `sub_agents/explorer.py` (alt) | `sub_agent.py:18-97` | exact |
| `openai_client.py` (modified) | itself — `analyze_document` dispatch arm (`openai_client.py:892-915`) | exact |
| `messages.py` (modified) | itself — sub_agent event arms (`messages.py:91-101`) | role-match (extended schema) |
| `frontend/src/lib/api.ts` (modified) | itself — sub_agent SSE branches (`api.ts:282-287`) | exact |
| `frontend/src/pages/Chat.tsx` (modified) | itself — sub-agent callback wiring (`Chat.tsx:232-242`) | exact |
| `backend/scripts/test_explorer_sub_agent.py` (NEW) | `backend/scripts/test_exploration_tools.py` (Phase 4) + `backend/scripts/test_folders.py` (Phase 3) + `backend/scripts/test_sub_agents.py` (Module 8) | exact (3-way blended) |
| `backend/scripts/test_all.py` (modified) | itself — `("Folders", test_folders)` and `("Exploration", test_exploration_tools)` registrations | exact |

---

## Open Questions / Risks (Detailed Answer to Question #8)

These would benefit from a design decision in `05-CONTEXT.md` before planning starts. They are **NOT blockers** — research provides recommended defaults — but the planner should confirm with the operator:

1. **Should `sub_agent.py` be extended or split into a `sub_agents/` package?**
   - **What we know:** At HEAD, `sub_agent.py` (singular file) exists; `sub_agents/` package does NOT. `research/ARCHITECTURE.md:175` recommends extending. Orchestrator's prompt mentions the package path which suggests a future-state assumption.
   - **What's unclear:** Whether the operator wants the package layout pre-emptively or prefers the minimal-disruption single-file approach.
   - **Recommendation:** Default to Recommendation A (extend `sub_agent.py`) for Phase 5; revisit in a future phase if a third sub-agent appears. Two siblings in one file is fine; three is the threshold for splitting.

2. **No-progress detector — hash args verbatim or normalize whitespace?**
   - **What we know:** Whitespace is irrelevant for tool semantics. `json.dumps(args, sort_keys=True)` already normalizes key order.
   - **What's unclear:** Should we additionally normalize string values (lowercase, strip)? E.g., `pattern: "Floor Plan"` vs `pattern: "floor plan"` are semantically distinct (case matters in regex), but `path: " /projects "` vs `path: "/projects"` are the same.
   - **Recommendation:** Hash verbatim with `sort_keys=True` only. Don't normalize string values — too easy to introduce false-equivalence bugs (case sensitivity in regex/glob is real). Phase 4 already runs paths through `normalize_path()` at the tool entry, so trailing/leading whitespace on paths is a non-issue.

3. **Should sub-agents share the parent's LangSmith run_id or get a child run_id?**
   - **What we know:** `@traceable(run_type="chain")` on `run_explorer_sub_agent` automatically creates a child run nested under the calling chain (gemini_chat). LangSmith's contextvars handle this transparently.
   - **What's unclear:** Whether we want the Explorer's sub_agent_id (UUID) to MATCH the LangSmith run_id (so a chat conversation log entry can deep-link to the LangSmith trace). Currently, the SSE `sub_agent_id` is generated server-side as a fresh UUID.
   - **Recommendation:** Don't couple them. The LangSmith run_id is for debugging; the SSE sub_agent_id is for client-side rendering correlation. Keeping them separate avoids LangSmith availability becoming a hard dependency for chat to work.

4. **Should the Explorer's tool args be shown in the SSE event verbatim, or sanitized?**
   - **What we know:** Phase 4 tool args contain user-controllable strings (regex patterns, paths). Echoing them back in `sub_agent_tool_start` events is harmless for our threat model (it's already the user's data going back to the user's browser).
   - **What's unclear:** Whether truncating long args (e.g., `pattern` > 200 chars) at SSE-emit time would help frontend rendering.
   - **Recommendation:** Truncate at emit time to 500 chars per arg to bound SSE message size. This matches Phase 4's `result_preview: text[:300]` discipline at `messages.py:100`.

5. **Should the `analyze_document` tool's existing SSE events also migrate to the generalized envelope, or stay legacy until Phase 6?**
   - **What we know:** Both old-shape and new-shape events for `analyze_document` should be emitted — the old shape preserves frontend continuity, the new shape lets Phase 6's recursive `SubAgentSection` parse both sub-agents uniformly.
   - **What's unclear:** Whether the old `sub_agent_*` event types are removed in Phase 5 or Phase 6.
   - **Recommendation:** Phase 5 keeps both. Phase 6 plan-checker enforces removal of legacy emissions in Phase 6's plan.

6. **`ExplorerArgs` Pydantic model — what fields beyond `query`?**
   - **What we know:** The spec's `explore_knowledge_base(question)` is single-arg. Phase 4 tools have multiple args; Explorer is intentionally single-purpose.
   - **What's unclear:** Whether to expose an optional `scope` arg so the user/main agent can pre-narrow ("explore only my private docs").
   - **Recommendation (`[ASSUMED]`):** Keep `ExplorerArgs(query: str = Field(..., min_length=1, max_length=2000))` minimal in v1. Defer optional `scope` arg to a v2 hardening pass; the LLM can always pass `scope` to individual tool calls inside the loop. This is **`[ASSUMED]`** — confirm with operator in discuss-phase.

7. **What happens if Explorer is invoked from a chat where `has_documents=False`?**
   - **What we know:** `_build_tools()` only adds tool factories under `if has_documents:` (`openai_client.py:651`). If no documents, Explorer is not registered → cannot be called.
   - **What's unclear:** Whether Explorer should also be gated on a Phase-2 readiness check (does the user have any `content_markdown_status='ready'` rows?). Without ready rows, grep/read_document return `pending_reindex` and Explorer can't make progress.
   - **Recommendation:** Add Explorer to the same `if has_documents:` block as Phase 4 tools. The `pending_reindex` contract is already locked from Phase 2; Explorer's compact summary will surface "documents not yet indexed" naturally.

---

## Runtime State Inventory

> Phase 5 is greenfield service code + frontend wiring + test code. No rename/refactor/migration; this section is included for completeness only — every category is "None" except the JSONB schema extension which is a code change, not a runtime-state change.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | None | — `messages.tool_metadata` JSONB already exists; the new schema is additive (new keys nested under existing ones). No data migration. |
| Live service config | None | — No external service config changes (LangSmith project unchanged; Supabase unchanged; Gemini API unchanged) |
| OS-registered state | None | — No background processes, no scheduled tasks |
| Secrets/env vars | None | — Existing `LANGSMITH_API_KEY`, `GEMINI_API_KEY`, `SUPABASE_*` reused; no new secrets |
| Build artifacts | None | — No new packages; no new compiled artifacts; existing `venv` is sufficient |

**Verified:** Greenfield phase — confirmed via Glob (no `sub_agents/` dir, no `explorer.py`, no `test_explorer_sub_agent.py`); confirmed via Grep (no `run_explorer_sub_agent` references in HEAD).

---

## Common Pitfalls

### Pitfall 7 (RANK 5): Explorer Sub-Agent Infinite-Loops on a Too-Broad Initial Query

**What goes wrong:** A user asks "tell me everything about our projects" and the Explorer iterates `tree → glob → grep → tree → glob → ...` forever, burning Gemini quota and frontend "Thinking..." patience.

**Why it happens:** Gemini's native SDK has no concept of tool-call budget. The Episode 1 sub-agent (`analyze_document`) is single-shot, so the codebase has zero precedent for bounding tool-call iteration in a sub-agent.

**How to avoid (per PITFALLS.md and Phase 5 success criteria):**
1. **Hard `for turn in range(MAX_TURNS=8)` bound.** Code the loop as `for ... in range(N)`, not `while not done:`.
2. **No-progress detector** on `(tool_name, args_hash)`. Short-circuit on consecutive duplicate signatures.
3. **Aggressive in-sub-agent result truncation** (`apply_12k_cap` at 12_000 chars — even tighter than the main agent's 16K).
4. **System prompt explicitly states the budget** (8 turns, 60s, 12K cap) — see EXPLORER_SYSTEM_PROMPT draft above.
5. **Wall-clock timeout** (60s `time.monotonic()` polling).
6. **TEST-03 LangSmith assertion** — Explorer span never has > 8 tool-call children.

**Warning signs:**
- LangSmith trace for `explore_knowledge_base` shows > 5 tool-call children
- Explorer runs longer than ~30s
- User reports: "the assistant is taking forever and giving vague answers"
- Explorer's final summary contains "I tried multiple searches but couldn't narrow down…"

### Pitfall 12 (high impact): SSE Forwarding for Nested Sub-Agent Tool Calls Breaks the Existing Event Protocol

**What goes wrong:** Developer adds a new event type per sub-agent (`explorer_start`, `explorer_token`, …) and bolts it onto `messages.py` as another `elif`. Two months later, a third sub-agent ships and the protocol forks again.

**Why it happens:** Module 8's sub-agent SSE work was bespoke (single use case). Generalizing now requires either versioning (effort) or bolt-on (debt). Most teams choose bolt-on under deadline pressure.

**How to avoid (per PITFALLS.md):**
1. **Generalize the protocol now**: `{type: 'sub_agent', agent_name, event, payload}` — see §SSE Protocol Generalization Strategy.
2. **Tool calls are nested events** of the same envelope: `{type: 'sub_agent', event: 'tool_call', payload: {tool, args}}`.
3. **Persist the trace** to `messages.tool_metadata` JSONB (Module 8's column — already there).
4. **LangSmith hierarchy** — `chain` span with nested `tool` children (automatic via `@traceable` + contextvars).
5. **Frontend collapsible-section pattern stays recursive** — same `SubAgentSection` component, deeper tree (Phase 6 deliverable).
6. **Test with both sub-agents in one conversation** — message #1 → `analyze_document`; message #2 → `explore_knowledge_base`; both render correctly + persist to `tool_metadata`.

**Warning signs (Pitfall 12 verbatim):**
- Frontend `SubAgentSection` component has an `if (agentType === 'explorer')` branch
- New SSE event types added with `explorer_*` prefix
- LangSmith trace doesn't show Explorer's tool calls as children
- Reloading old chat doesn't show Explorer's trace

### Pitfall 8 (RANK 3) — Carry-Forward: Gemini Empty-Response Recurs on Large Explorer Summaries

**What goes wrong:** Explorer's compact summary, plus tool_calls history, plus context-injection prompt approaches the Gemini context limit → Gemini emits zero tokens → user sees "Thinking..." forever.

**How to avoid:**
1. **Apply 12K char cap to the Explorer's compact summary** before it returns from the dispatch arm — same `apply_12k_cap` Phase 4 uses.
2. **The compact summary IS a tool result** — it flows through the unchanged TOOL-09 layered-fallback wrapper at `openai_client.py:1070` (truncate to 16K → streaming → non-streaming retry → raw yield). Pitfall 8 mitigation 1: never invent a parallel context-injection path.
3. **Pre-truncate the loop's `contents` accumulator** — each `FunctionResponse` is already 12K-capped; if `contents` grows past 100K total chars, log warning and break. Defense in depth.

**TEST-03 cross-cutting test:** A 50K-char adversarial doc + Explorer fixture query that would naturally produce a verbose summary → assert SSE stream emits `done` with non-empty assistant content + no `error` events.

---

## Code Examples

Verified patterns from in-tree HEAD:

### Example 1: Generator-of-Events Sub-Agent Shape

```python
# Source: backend/app/services/sub_agent.py:18-97 (HEAD verified)
@traceable(name="sub_agent_analyze", run_type="chain")
def run_sub_agent(
    document_id: str,
    document_name: str,
    question: str,
    user_id: str,
    supabase_client,
) -> Generator[tuple[str, str], None, None]:
    yield ("sub_agent_start", json.dumps({"document_name": document_name}))
    # ... (load chunks, build prompt, stream tokens)
    for chunk in response:
        if chunk.text:
            yield ("sub_agent_token", chunk.text)
    yield ("sub_agent_done", full_result)
```

`run_explorer_sub_agent` matches this shape exactly with the addition of `("sub_agent_tool_start", json)` and `("sub_agent_tool_done", json)` yields per inner tool call.

### Example 2: Lazy Import Inside Dispatch Arm (Avoids Circular Imports)

```python
# Source: backend/app/services/openai_client.py:892-915 (HEAD verified)
elif tool_name == "analyze_document":
    from app.services.sub_agent import run_sub_agent  # lazy import
    doc_name = args.get("document_name", "")
    question = args.get("question", "")
    # ... resolve doc_id ...
    sub_agent_result = ""
    for evt_type, evt_data in run_sub_agent(doc_id, actual_name, question, user_id, supabase_client):
        yield (evt_type, evt_data)
        if evt_type == "sub_agent_done":
            sub_agent_result = evt_data
    result_text = sub_agent_result
```

Phase 5 adds an `elif tool_name == "explore_knowledge_base":` arm that follows the SAME pattern, using `from app.services.sub_agent import run_explorer_sub_agent`.

### Example 3: SSE Event Forwarding in `messages.py:event_generator`

```python
# Source: backend/app/routers/messages.py:91-101 (HEAD verified)
elif event_type == "sub_agent_start":
    parsed = json.loads(data)
    tool_metadata = {"tools_used": [{"document_name": parsed.get("document_name", "")}]}
    yield json.dumps({"type": "sub_agent_start", **parsed})
elif event_type == "sub_agent_token":
    yield json.dumps({"type": "sub_agent_token", "content": data})
elif event_type == "sub_agent_done":
    if tool_metadata and tool_metadata["tools_used"]:
        tool_metadata["tools_used"][0]["tool"] = "analyze_document"
        tool_metadata["tools_used"][0]["sub_agent_result"] = data[:300]
    yield json.dumps({"type": "sub_agent_done"})
```

Phase 5 extends this with: (a) two new event arms (`sub_agent_tool_start`, `sub_agent_tool_done`), (b) dual-emit of generalized envelope, (c) refactored accumulator that supports `tool_calls: [...]` array per sub-agent slot.

### Example 4: TOOL-09 Layered-Fallback Wrapper (UNCHANGED)

```python
# Source: backend/app/services/openai_client.py:1070-1113 (HEAD verified)
truncated_result = result_text[:16000] if len(result_text) > 16000 else result_text
system_with_context = f"""You are a helpful assistant. Use the provided tool results...
Tool ({tool_name}) results:
{truncated_result}"""
# ... streaming + non-streaming retry + raw-yield fallback (lines 1081-1113)
```

Phase 5's Explorer compact summary is `result_text` — flows through this UNCHANGED. **Plan 03's plan-checker MUST verify these lines remain bit-identical.**

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Module 8 bespoke sub-agent SSE events | Generalized `{type: sub_agent, agent_name, event, payload}` envelope with dual-emit | Phase 5 (this) | Pitfall 12 mitigation; Phase 6 frontend recursion enabled |
| `sub_agent.py` single-shot single-doc analysis | `sub_agent.py` extended with `run_explorer_sub_agent` multi-turn loop | Phase 5 (this) | Two sub-agents share helpers; isolated context preserved |
| Explorer-specific event types (rejected) | Generalized envelope (chosen) | Phase 5 (this) | One handler, multiple sub-agent types — Pitfall 12 mitigation 1 |
| `while not done:` loop (rejected — STACK.md §5 + Pitfall 7) | `for turn in range(MAX_TURNS=8)` bounded loop (chosen) | Phase 5 (this) | Hard ceiling; for-else clause for natural exhaustion |
| `automatic_function_calling=enable` (rejected) | `automatic_function_calling=disable` + manual loop | Phase 1–4 lock | Per-tool tracing + SSE forwarding hooks preserved |

**Deprecated/outdated:**
- **None for Phase 5.** This is greenfield extension of in-tree patterns. No deprecations.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | Custom Python test suite (matches `test_helpers.py` + `test_all.py` shape used in Phases 1–4) |
| Config file | `backend/scripts/test_all.py` SUITES list |
| Quick run command | `cd backend && venv/Scripts/python scripts/test_explorer_sub_agent.py` |
| Full suite command | `cd backend && venv/Scripts/python scripts/test_all.py` |
| Estimated runtime | ~90s single-suite warm (Gemini API + LangSmith roundtrips); ~5 min full suite (17 suites) |

**Pre-reqs:** Backend on `localhost:8001`; `.env` with `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `GEMINI_API_KEY`; Phase 4's Migration 020 already applied (`grep_documents` RPC available); `documents` Storage bucket exists; admin promoted via `UPDATE public.profiles SET is_admin=true WHERE email='admin@test.com'`; **optional** `LANGSMITH_API_KEY` + `LANGSMITH_PROJECT` for span-structure assertion (Section 9 SKIPs gracefully without these — same pattern as Phase 4's psycopg2 EXPLAIN test).

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| EXPLORER-01 | `for turn in range(MAX_TURNS=8)` hard bound — no Explorer chat ever exceeds 8 tool calls | unit + integration (in-process counter monkeypatch + LangSmith SDK query) | `cd backend && venv/Scripts/python scripts/test_explorer_sub_agent.py` (Section 2) | ❌ Wave 0 |
| EXPLORER-02 | 60s wall-clock + no-progress detector short-circuits on duplicate `(tool, args)` | unit (monkeypatch `WALL_CLOCK_BUDGET_S=0.1` + stub Gemini repeated response) | same (Sections 3+4) | ❌ Wave 0 |
| EXPLORER-03 | `analyze_document` excluded — setup-time `assert` fires on tampered constant; `_build_explorer_tool_set()` has 5 tools, none = `analyze_document` | unit | same (Section 5) | ❌ Wave 0 |
| EXPLORER-04 | Generalized SSE event protocol; `sub_agent_tool_start`/`sub_agent_tool_done` flow; both sub-agents render in same conversation | integration (live SSE stream) | same (Sections 6+7) | ❌ Wave 0 |
| EXPLORER-05 | `messages.tool_metadata` JSONB persists Explorer trace; reload (GET messages) returns it intact | integration (chat → reload) | same (Section 8) | ❌ Wave 0 |
| EXPLORER-06 | LangSmith chain span with `tool` children nested; ≤ 8 tool children; tool result size ≤ 12K | integration (LangSmith SDK; SKIPs without API key) | same (Section 9) | ❌ Wave 0 |
| TEST-03 | Test suite mirrors `test_exploration_tools.py` discipline (10 sections, `_tracked_*` cleanup, canary, registered in test_all.py) | smoke (full suite green) | `cd backend && venv/Scripts/python scripts/test_all.py` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `cd backend && venv/Scripts/python scripts/test_explorer_sub_agent.py` (single suite, ~90s warm)
- **Per wave merge:** `cd backend && venv/Scripts/python scripts/test_explorer_sub_agent.py` (still single suite — full sweep is the phase gate)
- **Phase gate:** Full suite green via `cd backend && venv/Scripts/python scripts/test_all.py` (17 suites: Phase 4 sweep + new Explorer suite). Pre-existing carry-forward FAILs from Phase 1 (admin-assumption regression in Threads/Messages/Hybrid/Tools/Sub-Agents per STATE.md L118) remain out of scope; Explorer suite must report 0 failed.

### Wave 0 Gaps

- [ ] `backend/app/services/sub_agent.py` — extend with Explorer (or NEW `sub_agents/explorer.py` per planner choice)
- [ ] `backend/scripts/test_explorer_sub_agent.py` — covers EXPLORER-01..06 + TEST-03
- [ ] `backend/scripts/test_all.py` — register `("Explorer", test_explorer_sub_agent)` after `("Exploration", test_exploration_tools)`; `import test_explorer_sub_agent` between `import test_exploration_tools` and `import test_backfill`
- [ ] `backend/app/services/openai_client.py` — `_build_explore_knowledge_base_tool()` factory + dispatch arm + system-prompt update
- [ ] `backend/app/routers/messages.py` — extend `event_generator` with new event arms + dual-emit + tool_metadata accumulator refactor
- [ ] `frontend/src/lib/api.ts` — parse new SSE shape + new callbacks
- [ ] `frontend/src/pages/Chat.tsx` — wire new callbacks (minimum viable)

*(No framework install needed — existing test infrastructure covers all phase requirements.)*

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | Inherited — `get_current_user` + JWT (Episode 1 lock); Explorer is invoked through `/api/threads/{id}/messages` which already authenticates |
| V3 Session Management | yes (read-only) | Sessions managed by Supabase Auth; Explorer adds no session state |
| V4 Access Control | yes | RLS policies on `documents` + `document_chunks` + `folders` (Migration 015); Explorer's tool functions inherit these — no new bypass paths |
| V5 Input Validation | yes | Pydantic v2 `ExplorerArgs(query: str = Field(..., min_length=1, max_length=2000))`; each tool call's args validated by Phase 4's Pydantic schemas; tool_name dispatch validates against allowlist |
| V6 Cryptography | no | No new secrets; existing `LANGSMITH_API_KEY`/`GEMINI_API_KEY`/`SUPABASE_*` reused |
| V7 Error Handling | yes | Generator NEVER raises (mirrors `sub_agent.py:92-95`); errors are logged + surface as `sub_agent_done` with error summary |
| V8 Data Protection | yes | Tool result preview truncated to 300 chars in SSE/JSONB to bound exposure of doc content in trace logs |
| V13 API & Web Service | yes | SSE endpoint `/api/threads/{id}/messages` already has JWT auth; new event types require no new endpoint |

### Known Threat Patterns for Phase 5 Stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| LLM-injected pathological tool args (regex DoS, deep paths) | DoS | Reused from Phase 4: pathological-regex blocklist in `grep.py`; `normalize_path()` rejects `..`/`.`; `_assert_uuid()` on PostgREST `or_()` interpolation; statement_timeout 5s on grep RPC |
| Recursive sub-agent (Explorer → analyze_document → Explorer) | DoS / cost runaway | EXPLORER-03 setup-time `assert` (3 layers); see §Tool Registration Boundary |
| Infinite tool-call loop | DoS / cost runaway | EXPLORER-01 + EXPLORER-02 (MAX_TURNS=8 + 60s wall-clock + no-progress detector); see §Sub-Agent Loop Architecture |
| SSE protocol fork → frontend rendering breakage | Tampering (data integrity) | EXPLORER-04 generalized envelope + dual-emit + Phase 6 plan-checker enforces legacy removal; see §SSE Protocol Generalization |
| `tool_metadata` JSONB injection / oversize | Tampering / DoS | `result_preview: data[:300]` bounding; full result not stored in JSONB (only preview); RLS on `messages` table prevents cross-user reads |
| Explorer's compact summary leaks cross-scope data | Information disclosure | Tool functions already enforce RLS + `ensure_scope_tag` per row (Phase 4 TOOL-07); summary citations include scope tag (system prompt mandate) |
| Empty-response Pitfall 8 carry-forward | DoS (UX dead-end) | TOOL-09 layered-fallback wrapper UNCHANGED; Explorer's summary flows through it; cross-cutting TEST-03 Section 10 verifies 50K-char adversarial doc |

---

## Sources

### Primary (HIGH confidence)
- `[VERIFIED]` `backend/app/services/sub_agent.py:18-97` — existing `run_sub_agent` shape (analog for `run_explorer_sub_agent`)
- `[VERIFIED]` `backend/app/services/openai_client.py:553,586,892-915,1064-1113` — `@traceable` patterns + `analyze_document` dispatch arm + TOOL-09 layered-fallback wrapper
- `[VERIFIED]` `backend/app/routers/messages.py:66-125` — `event_generator` shape + `tool_metadata` accumulator + SSE conversion idiom
- `[VERIFIED]` `backend/app/services/exploration_tools/{list_files,tree,glob_match,read_document,grep}.py` — five Phase 4 tool functions (Explorer dispatches to these unchanged)
- `[VERIFIED]` `backend/app/services/exploration_tools/_truncate.py` — `apply_12k_cap` (12K cap reused inside Explorer loop)
- `[VERIFIED]` `backend/migrations/010_sub_agents.sql:4` — `messages.tool_metadata JSONB` column already exists; no new migration
- `[VERIFIED]` `backend/app/models/schemas.py:23-29` — `MessageResponse.tool_metadata: Optional[dict]` already shipped
- `[VERIFIED]` `frontend/src/lib/api.ts:34-47,282-287` — frontend `Message` interface + SSE consumer branches
- `[VERIFIED]` `frontend/src/components/MessageList.tsx:18-50,136-145` — `SubAgentSection` rendering pattern
- `[CITED]` `.planning/research/PITFALLS.md:199-225` — Pitfall 7 mitigation strategy
- `[CITED]` `.planning/research/PITFALLS.md:361-387` — Pitfall 12 generalization mandate
- `[CITED]` `.planning/research/STACK.md:349-435` — sub-agent orchestration without LangGraph; STACK.md §5 (full pattern + LangSmith hierarchy)
- `[CITED]` `.planning/research/ARCHITECTURE.md:175,251-299,365-395,461-466` — Pattern 3 Explorer Sub-Agent; Stage 5 build order
- `[VERIFIED]` `.planning/REQUIREMENTS.md:61-66,86` — EXPLORER-01..06 + TEST-03 verbatim text
- `[VERIFIED]` `.planning/ROADMAP.md:150-160` — Phase 5 success criteria
- `[VERIFIED]` `.planning/PROJECT.md:44` — `explore_knowledge_base` open feature
- `[VERIFIED]` `.planning/phases/04-five-exploration-tools-search-documents-extension/04-VERIFICATION.md` — Phase 4 verification report (all 5 tools shipped, 78/0)

### Secondary (MEDIUM confidence)
- `[ASSUMED]` LangSmith Python SDK's `Client.list_runs()` behaves consistently across versions — verified at `langsmith>=0.1` per existing `langsmith` usage in this codebase, but exact API stability across releases is not pinned. Recommendation: the TEST-03 Section 9 LangSmith assertion SKIPs gracefully if the SDK call fails (matches Phase 3 pattern).
- `[ASSUMED]` Gemini's `automatic_function_calling=disable` + manual loop continues to work for `gemini-3-flash-preview` (the Episode 1 default model). Phase 1–4 use this pattern in production. SDK release notes are not exhaustively reviewed for this research; rely on Phase 4's verified shipped behavior.

### Tertiary (LOW confidence)
- None. All claims are either grounded in HEAD code or in `.planning/research/*.md` documents.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `ExplorerArgs(query: str)` is single-arg in v1; optional `scope` is deferred | Open Questions §6 | Low — v2 hardening can add `scope` without breaking change; LLM can pass `scope` to inner tool calls already |
| A2 | LangSmith SDK `list_runs(project_name, run_type, ...)` API is stable across `langsmith>=0.1` releases | Test Strategy / EXPLORER-06 | Low — TEST-03 Section 9 SKIPs gracefully on SDK error; CI doesn't break |

If any `[ASSUMED]` items move to `[VERIFIED]` during discuss-phase or Wave 0, this log is the canonical record.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python venv | All backend code | ✓ | already established (`backend/venv`) | — |
| `google-genai` | LLM calls | ✓ | already in `requirements.txt` | — |
| `langsmith` | tracing + TEST-03 SDK | ✓ | already in `requirements.txt` | tests SKIP if `LANGSMITH_API_KEY` missing |
| `pydantic` | args validation | ✓ | v2 ships with FastAPI ≥ 0.100 | — |
| `supabase-py` | DB access | ✓ | already in `requirements.txt` | — |
| Phase 4 tools | Explorer dispatch | ✓ | shipped in Phase 4 (Glob verified) | NONE — hard dependency |
| Migration 010 (`tool_metadata` JSONB) | EXPLORER-05 persistence | ✓ | already applied (Module 8) | NONE — hard dependency |
| Migration 020 (Phase 4 RPCs) | grep tool inside Explorer | ✓ | applied 2026-05-09 (per Phase 4 VERIFICATION.md) | NONE — hard dependency |
| Backend on `localhost:8001` | TEST-03 integration | varies | dev environment | tests bail out with `[FATAL]` canary message — same Phase 3 pattern |
| `documents` Storage bucket | doc fixture seeding | varies | dev environment | tests SKIP fixture-creation if missing — same Phase 2 pattern |

**Missing dependencies with no fallback:** None.

**Missing dependencies with fallback:** `LANGSMITH_API_KEY` for span-structure assertion (Section 9 SKIPs gracefully).

---

## Metadata

**Confidence breakdown:**
- Sub-agent loop architecture: HIGH — verified against `sub_agent.py` HEAD shape + STACK.md §5 detailed pattern
- Tool registration boundary: HIGH — three independent enforcement layers; setup-time assert is Python convention
- SSE protocol generalization: HIGH — Pitfall 12 mitigation explicitly designed for this; dual-emit pattern is standard backwards-compat
- LangSmith span structure: HIGH — verified pattern at `_execute_search_documents` nested under `gemini_chat`
- Test strategy: HIGH on patterns (mirror Phases 3+4 test discipline) + MEDIUM on LangSmith SDK pinning (assumption A2)
- Files to be touched: HIGH — exhaustive enumeration via Glob + Grep against HEAD
- Open questions: HIGH — 6 explicit decisions with recommendations + 1 `[ASSUMED]` flag

**Research date:** 2026-05-09
**Valid until:** 2026-05-23 (14 days — fast-moving Gemini SDK landscape; LangSmith SDK can drift; revalidate before phase execution starts if > 14 days have passed)

---

## RESEARCH COMPLETE

**Phase:** 5 — Explorer Sub-Agent + SSE Protocol Generalization
**Confidence:** HIGH

### Key Findings

- **Phase 5 is plumbing, not new logic.** `run_explorer_sub_agent` mirrors the existing `run_sub_agent` generator shape (`sub_agent.py:18-97`); dispatches to the SAME five Phase 4 tools (`exploration_tools/{list_files,tree,glob_match,read_document,grep}.py`); reuses the SAME `apply_12k_cap` truncation helper; flows the compact summary through the UNCHANGED TOOL-09 layered-fallback wrapper at `openai_client.py:1070`. **Zero new SQL, zero new migrations, zero new third-party deps.**

- **EXPLORER-03 (recursive-sub-agent ban) is enforced with three independent layers:** module-level `assert "analyze_document" not in EXPLORER_ALLOWED_TOOLS` (fires at import), tool-set builder `assert names == set(EXPLORER_ALLOWED_TOOLS)` (fires at first dispatch), and dispatch-time `if tool_name not in EXPLORER_ALLOWED_TOOLS` (runtime). TEST-03 must verify all three.

- **EXPLORER-04 (SSE protocol generalization) requires dual-emit for one release.** Backend emits BOTH `{type: 'sub_agent_start'}` (legacy) AND `{type: 'sub_agent', agent_name, event: 'start', payload}` (generalized) for one release window; Phase 6's frontend rewrite consumes the generalized shape and Phase 6's plan-checker removes legacy emissions. Without that Phase 6 cleanup hook, the protocol fork debt persists indefinitely (Pitfall 12 antipattern).

- **EXPLORER-06 (LangSmith chain span with nested tool children) is automatic.** A single `@traceable(run_type="chain")` decorator on `run_explorer_sub_agent` — combined with the EXISTING `@traceable(run_type="tool")` decorators on the five Phase 4 tools (verified at `list_files.py:32`, `tree.py:34`, `glob_match.py:48`, `read_document.py:39`, `grep.py:46`) — produces the nested-children hierarchy via LangSmith's contextvars propagation. No manual `with trace(...)` blocks required.

- **6 plans across 4 waves** is the recommended structure: Plan 01 (helpers + Pydantic args), Plan 02 (`run_explorer_sub_agent` core loop), Plan 03 (`openai_client.py` factory + dispatch arm + system prompt), Plans 04+05 in parallel (`messages.py` SSE generalization + dual-emit + tool_metadata persistence; `frontend/src/lib/api.ts` callback wiring), Plan 06 (`test_explorer_sub_agent.py` 10-section integration suite + register in `test_all.py`). Phase 6 (UI cluster) consumes Phase 5's generalized SSE protocol + persisted JSONB trace as inputs to UI-10's recursive `SubAgentSection` extension.

### File Created

`C:\RAG Automators\claude-code-agentic-rag-masterclass-ep2\.planning\phases\05-explorer-sub-agent-sse-protocol-generalization\05-RESEARCH.md`

### Confidence Assessment

| Area | Level | Reason |
|------|-------|--------|
| Sub-agent loop architecture | HIGH | Verified against shipped `run_sub_agent` shape + STACK.md §5 detailed pattern |
| Tool registration boundary | HIGH | Three-layer setup-time defense; standard Python convention |
| SSE protocol generalization | HIGH | Pitfall 12 designed for this exact moment; dual-emit is standard backwards-compat |
| LangSmith span structure | HIGH | Verified analog (`_execute_search_documents` under `gemini_chat`) |
| Tool-result truncation | HIGH | Reuses Phase 4's verified `apply_12k_cap` |
| tool_metadata JSONB persistence | HIGH | Module 8 column already shipped; schema is additive |
| Test strategy | HIGH (patterns) / MEDIUM (LangSmith SDK pinning) | Mirrors Phase 3+4 test discipline; SDK assumption A2 has graceful SKIP fallback |
| Files to be touched | HIGH | Exhaustive enumeration via Glob + Grep against HEAD |
| Open questions / risks | HIGH | 6 explicit decisions with recommendations + 1 `[ASSUMED]` flag |

### Open Questions

1. Single-file `sub_agent.py` extension vs. `sub_agents/` package split — recommendation: extend; defer split to next sub-agent (planner picks in `05-CONTEXT.md`).
2. No-progress detector hash policy — recommendation: verbatim `json.dumps(args, sort_keys=True)` (no value normalization).
3. Explorer's `sub_agent_id` UUID vs. LangSmith run_id coupling — recommendation: keep separate.
4. Sanitization of tool args in SSE events — recommendation: 500-char per-arg cap.
5. `analyze_document` SSE event migration to generalized envelope (this phase or Phase 6) — recommendation: dual-emit in Phase 5; remove legacy in Phase 6.
6. `ExplorerArgs` field surface — recommendation: `query: str` only in v1; defer optional `scope` to v2 (`[ASSUMED]`).

### Ready for Planning

Research complete. Planner can now create 6 PLAN.md files in 4 waves per the recommendation in §Summary. The Validation Architecture section above feeds directly into `05-VALIDATION.md`.
