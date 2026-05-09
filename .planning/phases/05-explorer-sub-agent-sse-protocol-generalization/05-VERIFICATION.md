---
phase: 05-explorer-sub-agent-sse-protocol-generalization
verified: 2026-05-09T23:30:00Z
status: human_needed
score: 4/4 success criteria verified (static); runtime gate awaits operator-run TEST-03 suite
overrides_applied: 0
re_verification: false
human_verification:
  - test: "Run TEST-03 integration suite end-to-end"
    expected: "cd backend && venv/Scripts/python scripts/test_explorer_sub_agent.py reports `Results: N passed, 0 failed` for N >= ~25"
    why_human: "CLAUDE.md mandates do-NOT-auto-run-tests; suite requires backend on :8001 + Supabase + GEMINI_API_KEY; LLM-controlled tool selection means SC1's '<=8 tool calls' upper bound is observable only on a live run"
  - test: "Manually trigger an Explorer-eligible chat in the UI and confirm SSE rendering"
    expected: "User asks 'find everything in my KB about MDB-C-G3 panel ratings'; UI shows sub-agent banner + nested tool steps populating in real time; reload renders the persisted Explorer trace from messages.tool_metadata"
    why_human: "End-to-end visual rendering through MessageList.SubAgentSection cannot be checked statically; Phase 6 owns the polished UI but Phase 5 must produce a non-broken experience today"
  - test: "Run Phase 4 regression suite"
    expected: "cd backend && venv/Scripts/python scripts/test_exploration_tools.py reports the established Phase 4 baseline (78/0 per Phase 4 close); no regression from Plan 03's openai_client.py edits"
    why_human: "Regression check requires live backend; Plan 03 added a registration line and dispatch arm in openai_client.py — cannot statically prove zero behavioral drift on Phase 4 tool factories"
  - test: "Run Module-8 sub-agent regression suite"
    expected: "cd backend && venv/Scripts/python scripts/test_sub_agents.py remains green (analyze_document path's `tool_metadata.tools_used[0].tool == 'analyze_document'` still works through Plan 04's refactored event_generator)"
    why_human: "Plan 04 refactored the SSE event_generator's accumulator from `[0]`-fixed to `[-1]`-array. Static evidence shows the legacy fallback `parsed.get('agent_name', 'analyze_document')` is preserved, but only a live run of test_sub_agents proves analyze_document's persisted JSONB shape is bit-compatible."
  - test: "LangSmith chain-span hierarchy check (EXPLORER-06 + Success Criterion 4)"
    expected: "After a live Explorer chat, LangSmith UI shows `explore_knowledge_base` as a `chain` run with 5-tool children nested as `tool` runs (NOT flat siblings); each tool result <= 12K chars"
    why_human: "Requires LANGSMITH_API_KEY + LangSmith UI inspection; the `@traceable(run_type='chain')` decorator + Phase 4 tools' `@traceable(run_type='tool')` create the auto-nesting via contextvars but only LangSmith renders the actual hierarchy"
---

# Phase 5: Explorer Sub-Agent + SSE Protocol Generalization — Verification Report

**Phase Goal:** The main agent can delegate open-ended exploration to `explore_knowledge_base`, an isolated-context sub-agent that composes the five precision tools under hard turn/timeout/no-progress bounds; the SSE sub-agent event protocol is generalized once (now) instead of bolted on (later).

**Verified:** 2026-05-09T23:30:00Z
**Status:** human_needed (static verification PASSED on all 4 Success Criteria; live runtime gate routes to operator)
**Re-verification:** No — initial verification

## Goal Achievement

### Success Criteria (Roadmap Contract)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `run_explorer_sub_agent()` runs `for turn in range(MAX_TURNS=8):` bounded loop with 60s wall-clock timeout and no-progress detector (tool-name+args-hash repeat → short-circuit); a deliberately broad fixture query never exceeds 8 tool calls in LangSmith trace | VERIFIED (static); RUNTIME bound deferred to TEST-03 | `backend/app/services/sub_agent.py:411` `for turn in range(MAX_TURNS):`; `:413` wall-clock guard `if time.monotonic() - start_time > WALL_CLOCK_BUDGET_S:`; `:447-457` no-progress detector `sig = _signature(fc.name, args_dict); if sig == last_signature: break`. `MAX_TURNS = 8` at `:29`; `WALL_CLOCK_BUDGET_S = 60.0` at `:30`. Single occurrence of `for turn in range(...)` — no `while` loop. Section 2 of TEST-03 monkeypatches `_dispatch_explorer_tool` to count calls and asserts `counter <= MAX_TURNS=8`. Section 3 monkeypatches `WALL_CLOCK_BUDGET_S=0.1` to confirm short-circuit. Section 4 stubs Gemini to repeat same call to confirm no-progress detector. |
| 2 | `analyze_document` is hard-excluded from Explorer's toolset (no recursive sub-agents); attempting to register it raises a setup-time error | VERIFIED | Layer 1 — module-level assert at `sub_agent.py:39-43`: `EXPLORER_ALLOWED_TOOLS = ("tree", "glob", "grep", "list_files", "read_document")` followed by `assert "analyze_document" not in EXPLORER_ALLOWED_TOOLS` (fires at import). Layer 2 — `_build_explorer_tool_set()` at `:127-157` asserts `names == set(EXPLORER_ALLOWED_TOOLS)` AND `"analyze_document" not in names`. Layer 3 — `_dispatch_explorer_tool()` at `:208-226` returns `{"error": "TOOL_NOT_ALLOWED_IN_EXPLORER", "tool": tool_name}` if not allowlisted. TEST-03 Section 5 has 3 sub-tests covering all three layers including a tampered-tuple test that triggers the layer-2 assert via in-memory mutation. |
| 3 | SSE event protocol generalized to `{type: 'sub_agent', agent_name, event, payload}` with new `sub_agent_tool_start`/`sub_agent_tool_done` events forwarded by `messages.py:event_generator`; both `analyze_document` and `explore_knowledge_base` flows render correctly in the same conversation, and `messages.tool_metadata` JSONB persists Explorer traces so old chats render correctly on reload | VERIFIED (static); cross-flow co-existence + reload-rendering deferred to TEST-03 + manual UAT | `backend/app/routers/messages.py:92-204` — five sub-agent arms (start/tool_start/tool_done/token/done) all dual-emit BOTH legacy `{type:'sub_agent_*', ...}` AND generalized `{type:'sub_agent', agent_name, event, payload}`. `agent_name` resolved server-side from `tool_metadata['tools_used'][-1]['tool']` for token/done arms (uniform contract across all 5 events). `tool_metadata['tools_used']` accumulator refactored from `[0]`-fixed (analyze-only) to `[-1]`-array supporting multi-sub-agent-per-message. JSONB persistence path at `:213-223` is bit-identical (single `insert_data["tool_metadata"] = json.dumps(tool_metadata)` line). Frontend wired in `frontend/src/lib/api.ts:43-58` (Message interface extended with `question?`, `sub_agent_id?`, `tool_calls?: Array<...>`), `:232-246` (sendMessage signature gains `onSubAgentToolStart`/`onSubAgentToolDone`), `:313-320` (two new SSE branches). Chat.tsx `:237` falls back to `data.question` when `document_name` missing; `:264-294` wires both new callbacks with `isSubAgent: true` markers. ToolActivity.tsx exports the extended `ToolStep` with `isSubAgent?: boolean` + `turn?: number`. TEST-03 Section 6 asserts both legacy AND generalized envelopes co-exist on a live SSE stream; Section 8 asserts JSONB reload via `GET /messages` returns `tool_calls[]` intact with `<=300`-char `result_preview`. |
| 4 | LangSmith shows Explorer as a `chain` span with its tool calls as nested children (not flat siblings); a CI assertion confirms Explorer spans never exceed 8 tool-call children and tool-result size stays under 12K chars | VERIFIED (static); rendered hierarchy + child-count deferred to TEST-03 + LangSmith UI | `sub_agent.py:345` `@traceable(name="explore_knowledge_base", run_type="chain")` decorator on `run_explorer_sub_agent`. Phase 4 precision tools already carry `@traceable(run_type="tool")` so LangSmith auto-nests via contextvars (no manual `with trace(...)` block). TEST-03 Section 9 (`_section_9_langsmith_spans`) issues `Client.list_runs(filter='eq(name, "explore_knowledge_base")', run_type="chain")`, then `list_runs(parent_run_id=run.id, run_type="tool")` for children, asserting `len(children) <= MAX_TURNS` (= 8) at `test_explorer_sub_agent.py:1176-1180` AND `max_output_len <= 12_500` chars (RESULT_CHAR_CAP=12_000 + 500 buffer for JSON wrapping) at `:1191-1195`. Section 9 gracefully SKIPs without `LANGSMITH_API_KEY`. RESULT_CHAR_CAP = 12_000 enforced inside `run_explorer_sub_agent` via `apply_12k_cap(result_dict, char_cap=RESULT_CHAR_CAP)` at `:481`. |

**Score (Static):** 4/4 success criteria have all required wiring + assertions in code; 0 BLOCKERS.
**Runtime Score:** Pending — operator must run TEST-03 to convert "static evidence + assertion in code" into "live test reports N passed, 0 failed".

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `backend/app/services/sub_agent.py` (Plan 01+02) | `ExplorerArgs`, `_signature`, `EXPLORER_SYSTEM_PROMPT`, `EXPLORER_ALLOWED_TOOLS`, `MAX_TURNS=8`, `WALL_CLOCK_BUDGET_S=60.0`, `RESULT_CHAR_CAP=12_000`, `SSE_ARG_CAP=500`, setup-time assert, `_build_explorer_tool_set`, `_extract_function_call`, `_extract_text`, `_truncate_args_for_sse`, `_dispatch_explorer_tool`, `run_explorer_sub_agent` with `@traceable(name="explore_knowledge_base", run_type="chain")` | VERIFIED | All 14 net-new symbols present; module-level assert at `:40-43`; Pydantic v2 `ExplorerArgs` with `min_length=1, max_length=2000, model_config={"extra":"ignore"}` at `:46-60`; SHA-256 `_signature` with `sort_keys=True` at `:63-79`; system prompt is plain ASCII (verified `<=` is two chars, no U+2264) at `:82-124`; `run_explorer_sub_agent` 200+ lines with `for turn in range(MAX_TURNS):` at `:411`, wall-clock guard at `:413`, no-progress detector at `:447-457`, `apply_12k_cap(... char_cap=RESULT_CHAR_CAP)` at `:481`, `AutomaticFunctionCallingConfig(disable=True)` at `:426`, `for-else` clause sets `short_circuit_reason = "max_turns"` at `:507-512`. Generator never raises — every Gemini call wrapped in try/except; every exit path yields `("sub_agent_done", ...)`. Pre-existing `run_sub_agent` at `:263-342` unchanged (Module 8 baseline preserved). |
| `backend/app/services/openai_client.py` (Plan 03) | `_build_explore_knowledge_base_tool` factory; registration in `if has_documents:` block AFTER `_build_grep_tool`; `elif tool_name == "explore_knowledge_base":` dispatch arm; system-prompt updates | VERIFIED | Factory at `:484-520` returns `FunctionDeclaration(name="explore_knowledge_base", ...)` with single `query` STRING parameter (`required=["query"]`). Registration at `:733-736` is the LAST factory in the `if has_documents:` block (after grep at `:729-732`, before `if text_to_sql_enabled:` at `:737`). Dispatch arm at `:1121-1140` mirrors `analyze_document` arm pattern: lazy import `from app.services.sub_agent import run_explorer_sub_agent`, empty-query guard, `for evt_type, evt_data in run_explorer_sub_agent(query_arg, user_id, supabase_client): yield (evt_type, evt_data); if evt_type == "sub_agent_done": sub_agent_result = evt_data`, then `result_text = sub_agent_result or "Exploration completed without a summary."`. TOOL-09 wrapper bit-identity proven: `grep -c "truncated_result = result_text\[:16000\] if len(result_text) > 16000 else result_text"` returns **2** (matches Phase 4 baseline; zero edits inside the layered-fallback block). System prompt: `_build_system_prompt` at `:49` adds the explore_knowledge_base bullet; `:79-90` adds the disambiguation rule (analyze vs search vs explore); `_build_system_prompt` mentions `explore_knowledge_base` >= 2 times when `has_documents=True`. |
| `backend/app/routers/messages.py` (Plan 04) | `import uuid`; refactored `sub_agent_start` arm with `tool_metadata["tools_used"]` array + UUID + agent_name fallback; new `sub_agent_tool_start` / `sub_agent_tool_done` arms; refactored `sub_agent_token` / `sub_agent_done` arms; uniform generalized envelope `{type:"sub_agent", agent_name, event, payload}` across all 5; UNCHANGED persistence path | VERIFIED | `import uuid` at `:3`. Five sub-agent arms at `:92-204` — every arm dual-emits one legacy + one generalized event. UUID generated per sub_agent_start at `:99` `sub_agent_id = str(uuid.uuid4())`. `tools_used.append(slot)` at `:117` — multi-sub-agent ready. Generalized envelope `{"type": "sub_agent", "agent_name": ..., "event": ..., "payload": ...}` for all 5 events: start (`:122-127`), tool_start (`:142-147`), tool_done (`:161-166`), token (`:177-182`), done (`:199-204`). Token/done arms resolve `agent_name` server-side from `tool_metadata["tools_used"][-1].get("tool", "analyze_document")` (`:175`, `:196`) — uniform contract. Result_preview cap to 300 chars: `parsed.get("result_preview", "")[:300]` at `:157`; `slot["sub_agent_result"] = data[:300]` at `:195`. Persistence path bit-identical at `:214-223` — single `insert_data["tool_metadata"] = json.dumps(tool_metadata)` line. Generator-never-raises preserved (outer try/except at `:70`/`:207`). |
| `frontend/src/lib/api.ts` (Plan 05) | Message interface extended with `question?`, `sub_agent_id?`, `tool_calls?`; sendMessage signature gains `onSubAgentToolStart`, `onSubAgentToolDone`; SSE consumer routes new event types to new callbacks | VERIFIED | Message interface at `:34-58` extended with `question?: string`, `sub_agent_id?: string`, `tool_calls?: Array<{tool, args?, result_preview?, turn?}>`. sendMessage signature at `:213-246` adds onSubAgentToolStart and onSubAgentToolDone at END of parameter list (positional-compat preserved). SSE consumer at `:313-320` adds two new branches: `} else if (parsed.type === 'sub_agent_tool_start') { onSubAgentToolStart?.(parsed) } else if (parsed.type === 'sub_agent_tool_done') { onSubAgentToolDone?.(parsed) }`. No `parsed.type === 'sub_agent'` branch — Phase 5 listens to LEGACY channel only (matches Plan 04's dual-emit; Phase 6 will switch to generalized). |
| `frontend/src/pages/Chat.tsx` (Plan 05) | onSubAgentStart extended for question fallback; onSubAgentToolStart / onSubAgentToolDone callbacks wired to setToolSteps with isSubAgent flag | VERIFIED | `:237` `setSubAgentDocName(data.document_name \|\| data.question \|\| '')` — backwards-compat preserved. `:264-294` two new callbacks appended to sendMessage call: onSubAgentToolStart pushes `{tool, args, status:'running' as const, isSubAgent: true, turn: data.turn}`; onSubAgentToolDone updates with precise match `s.isSubAgent && s.tool === data.tool && s.status === 'running' && s.turn === data.turn` setting `detail: data.result_preview`. Existing main-agent onToolStart/onToolDone handlers unchanged (no regression). |
| `frontend/src/components/ToolActivity.tsx` (Plan 05) | ToolStep type extended with `isSubAgent?: boolean` + `turn?: number` | VERIFIED | At `:3-10`, ToolStep interface includes `isSubAgent?: boolean` and `turn?: number` as optional additive fields. |
| `backend/scripts/test_explorer_sub_agent.py` (Plan 06) | TEST-03 integration suite — module-top imports surface EXPLORER-03 layer 1 in CI; canary precheck; 10 sections covering EXPLORER-01..06 + Pitfall 8 carry-forward; per-id batched cleanup | VERIFIED | File exists at expected path; **1399 lines** (well above 600 minimum). Module-top import at `:67-72`: `from app.services.sub_agent import run_explorer_sub_agent, EXPLORER_ALLOWED_TOOLS, MAX_TURNS, WALL_CLOCK_BUDGET_S, _signature` — surfaces Plan 01 module assert in CI even before any test runs. UTF-8 stdout reconfigure at `:50` (Windows safety). `_verify_phase5_setup` canary at `:121`. `_seed_fixture_corpus` at `:228`. `_cleanup` at `:291`. All 9 numbered section functions present (`_section_2_max_turns` at `:476` through `_section_10_pitfall_8_carry_forward` at `:1200`). Section 5 has 3 sub-tests including layer-2 tampering check (mutates `EXPLORER_ALLOWED_TOOLS` in-memory, asserts `_build_explorer_tool_set` raises AssertionError, restores via `importlib.reload`). Section 9 LangSmith assertions at `:1176-1195` verify `len(children) <= MAX_TURNS` AND `max_output_len <= 12_500` chars. SKIP gates for missing `LANGSMITH_API_KEY` at `:1115-1121`. Static gate confirmed: zero `DELETE FROM` / `TRUNCATE` matches in file (per Grep). All deletes use per-id batched `.in_("id", batch)` discipline. `run() -> tuple[int, int]` entry point at `:1276`; `if __name__ == "__main__":` at `:1395`. |
| `backend/scripts/test_all.py` (Plan 06 Task 2) | `import test_explorer_sub_agent` between exploration_tools and backfill; `("Explorer", test_explorer_sub_agent)` tuple in same position | VERIFIED | `:19` `import test_explorer_sub_agent  # NEW (Phase 5)` between `import test_exploration_tools` (`:18`) and `import test_backfill` (`:20`). `:38` `("Explorer", test_explorer_sub_agent), # NEW (Phase 5 — explore_knowledge_base sub-agent)` between Exploration and Backfill in SUITES list. Suite count is now 17 (was 16 in Phase 4 close). |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `sub_agent.py` module top | Python interpreter at first import | module-level `assert` | WIRED | `:40-43` — fires AssertionError on tampered tuple |
| `ExplorerArgs` | Pydantic v2 validator | `Field(..., min_length=1, max_length=2000)` | WIRED | `:54-58` |
| `_signature` | hashlib.sha256 | `json.dumps(..., sort_keys=True, default=str).encode("utf-8")` | WIRED | `:74-79` |
| `run_explorer_sub_agent` | Phase 4 tool factories | lazy import inside `_build_explorer_tool_set` | WIRED | `:135-141` lazy `from app.services.openai_client import _build_list_files_tool, ...` |
| `run_explorer_sub_agent` | Phase 4 tool functions | lazy imports inside `_dispatch_explorer_tool` | WIRED | `:229-236` lazy `from app.services.exploration_tools.* import *` |
| `_dispatch_explorer_tool` | Phase 4 Pydantic schemas | `TreeArgs/GlobArgs/.../ReadDocumentArgs(**args_dict)` | WIRED | `:240-254` 5 branches |
| Tool result dict | Gemini next-turn contents | `apply_12k_cap(result_dict, char_cap=RESULT_CHAR_CAP)` then `types.FunctionResponse` | WIRED | `:481-505` |
| `@traceable(name="explore_knowledge_base", run_type="chain")` | Phase 4 tools' `@traceable(run_type="tool")` | LangSmith contextvars auto-nesting | WIRED | `:345` decorator + Phase 4 tools already decorated (verified by reference, not re-checked) |
| `openai_client.py` dispatch arm | `sub_agent.run_explorer_sub_agent` | lazy import inside elif | WIRED | `:1121-1135` |
| Dispatch arm `result_text` | TOOL-09 layered-fallback wrapper | `result_text = sub_agent_result or "..."` | WIRED | `:1138-1140` flows to wrapper at `:1148` (bit-identical to Phase 4) |
| `event_generator` `sub_agent_start` arm | `uuid.uuid4()` | `import uuid` + `str(uuid.uuid4())` | WIRED | `messages.py:3` import; `:99` call |
| `tool_metadata` accumulator (recursive) | `messages.tool_metadata` JSONB column | UNCHANGED `json.dumps(tool_metadata)` INSERT | WIRED | `messages.py:222` |
| api.ts SSE branch `sub_agent_tool_start` | Chat.tsx `onSubAgentToolStart` callback | sendMessage signature parameter | WIRED | `api.ts:232-236`, `:315`; Chat.tsx callback at L266-276 |
| api.ts SSE branch `sub_agent_tool_done` | Chat.tsx `setToolSteps` update | callback with `isSubAgent: true` flag | WIRED | `api.ts:237-241`, `:318`; Chat.tsx callback at L283-293 |
| Phase 5 frontend | Phase 6 UI-10 nested rendering | persisted `tool_metadata.tools_used[].tool_calls[]` JSONB | WIRED (data layer) | Persisted by Plan 04 with 300-char cap; `tool_calls?: Array<...>` in api.ts Message interface; rendering deferred to Phase 6 |
| `test_all.py` | `test_explorer_sub_agent` module | `import test_explorer_sub_agent` + SUITES tuple | WIRED | `test_all.py:19, :38` |
| Section 5 importlib.reload | Plan 01 module-level setup-time assert | tampered `EXPLORER_ALLOWED_TOOLS` triggers AssertionError | WIRED (with adapted approach) | TEST-03 `:683-709` mutates tuple in-memory + calls `_build_explorer_tool_set()` to trigger LAYER-2 assert (rather than reloading the module — equally effective for verifying the recursion ban; restores via `importlib.reload(sa)` to re-read source from disk) |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|---------------------|--------|
| `run_explorer_sub_agent` generator | `result_dict` (per turn) | `_dispatch_explorer_tool` invokes Phase 4 tools (tree/glob/grep/list_files/read_document) which run RLS-respecting Supabase queries via the user's `supabase_client` | YES — Phase 4 tools query real `documents` / `document_chunks` tables; results truncated via `apply_12k_cap` before injection | FLOWING |
| `event_generator` | `tool_metadata["tools_used"]` | Server-side accumulator populated from generator yields (start → append slot; tool_start → append tool_call; tool_done → set result_preview; done → set sub_agent_result) | YES — populated turn-by-turn during live SSE; persisted to JSONB only when `full_response.strip()` is non-empty | FLOWING |
| Chat.tsx `toolSteps` state | `data.tool, data.args, data.turn, data.result_preview` | onSubAgentToolStart / onSubAgentToolDone callbacks fire from api.ts SSE branches | YES — wire data flows from Plan 04's dual-emit (legacy channel) → Plan 05 callbacks → setToolSteps state | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| `run_explorer_sub_agent` is exported as a generator | `Read sub_agent.py:345-548` | function decorated with `@traceable(...)` returning `Generator[tuple[str, str], None, None]` | PASS |
| `EXPLORER_ALLOWED_TOOLS` is an immutable tuple containing exactly the 5 expected tools | `Grep EXPLORER_ALLOWED_TOOLS = ` | `("tree", "glob", "grep", "list_files", "read_document")` | PASS |
| Single `for turn in range(MAX_TURNS):` loop (no `while`) inside Explorer | `Grep for.*range\(MAX_TURNS\)` in sub_agent.py | exactly one match at `:411` | PASS |
| TOOL-09 wrapper bit-identity preserved (Phase 4 invariant) | `Grep -c "truncated_result = result_text\[:16000\] if len(result_text) > 16000 else result_text"` | **2** (matches Phase 4 baseline) | PASS |
| Five sub-agent arms in event_generator (start/tool_start/tool_done/token/done) | `Grep elif event_type == "sub_agent_*"` in messages.py | 5 distinct elif arms present | PASS |
| Five generalized envelope yields (one per arm) | `Grep "type": "sub_agent",` | 5 occurrences | PASS |
| Test_all.py SUITES count went 16→17 with Explorer between Exploration and Backfill | `Read test_all.py:30-48` | Index 7 = "Explorer", at correct position | PASS |
| Static grep gate: no `DELETE FROM` / `TRUNCATE` in test_explorer_sub_agent.py | `Grep -i "DELETE FROM\|TRUNCATE"` | zero matches | PASS |
| TEST-03 file size sanity (>=600 LOC per acceptance criterion) | `wc -l test_explorer_sub_agent.py` | 1399 lines | PASS |
| Module imports cleanly (live runtime gate — operator-deferred) | `cd backend && venv/Scripts/python -c "import app.services.sub_agent"` | NOT RUN — CLAUDE.md prohibits auto test execution | SKIP |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| EXPLORER-01 | Plans 01, 02, 06 | `run_explorer_sub_agent()` extends existing `run_sub_agent` shape with `for turn in range(MAX_TURNS=8)` hard bound | SATISFIED | sub_agent.py:411 hard `for turn in range(MAX_TURNS):`; `MAX_TURNS = 8` at `:29`; TEST-03 Section 2 monkeypatches `_dispatch_explorer_tool` to count calls and asserts `<= MAX_TURNS=8` |
| EXPLORER-02 | Plans 01, 02, 06 | 60s wall-clock timeout + no-progress detector | SATISFIED | sub_agent.py:413 wall-clock guard `if time.monotonic() - start_time > WALL_CLOCK_BUDGET_S:`; `:447-457` `sig = _signature(fc.name, args_dict); if sig == last_signature: short_circuit_reason = "no_progress"; break`; `WALL_CLOCK_BUDGET_S = 60.0` at `:30`; TEST-03 Section 3 (`WALL_CLOCK_BUDGET_S=0.1` monkeypatch) and Section 4 (Gemini stub repeating same call) |
| EXPLORER-03 | Plans 01, 02, 03, 06 | Hard exclusion of `analyze_document` from Explorer's toolset (no recursive sub-agents) | SATISFIED | Three-layer defense: module assert at sub_agent.py:40-43; `_build_explorer_tool_set` runtime assert at `:152-156`; `_dispatch_explorer_tool` allowlist guard at `:221-226`. Plan 03 introduces `_build_explore_knowledge_base_tool` factory at openai_client.py:484 — explicitly without `analyze_document` in the parameters. TEST-03 Section 5 has 3 sub-tests covering all three layers. |
| EXPLORER-04 | Plans 02, 04, 05, 06 | Generalized SSE event protocol (`agent_name`, `event`, `payload`) supporting both `analyze_document` and `explore_knowledge_base`; new event types `sub_agent_tool_start` / `sub_agent_tool_done` | SATISFIED | Backend: messages.py:92-204 dual-emits all 5 sub-agent events with uniform `{type:"sub_agent", agent_name, event, payload}` envelope; agent_name resolved server-side for token/done arms. Frontend: api.ts:313-320 routes the two new event types; Chat.tsx wires both new callbacks with `isSubAgent: true` markers. TEST-03 Section 6 asserts both legacy AND generalized envelope co-exist on a live SSE stream. |
| EXPLORER-05 | Plans 04, 06 | `messages.tool_metadata` JSONB persists Explorer trace so old chats render correctly on reload | SATISFIED | messages.py:117 appends slot to `tools_used` array; `:135-139` populates `tool_calls[]` per turn; `:156-158` writes `result_preview[:300]`; `:195` writes `sub_agent_result[:300]`. Persistence path UNCHANGED at `:222`. api.ts Message interface declares the persisted JSONB shape (`tool_calls?: Array<{tool, args?, result_preview?, turn?}>`). TEST-03 Section 8 issues `GET /messages` post-chat and asserts `tool_metadata.tools_used[i].tool_calls[]` is intact with `<=300`-char `result_preview`. |
| EXPLORER-06 | Plans 02, 06 | LangSmith `@traceable(run_type="chain")` on Explorer entry; tool calls become nested children spans | SATISFIED | sub_agent.py:345 `@traceable(name="explore_knowledge_base", run_type="chain")` on `run_explorer_sub_agent`. Phase 4 precision tools already carry `@traceable(run_type="tool")` (inherited from Phase 4 close), so LangSmith auto-nests via contextvars. TEST-03 Section 9 (`_section_9_langsmith_spans`) calls `Client.list_runs(filter='eq(name, "explore_knowledge_base")', run_type="chain")` then `list_runs(parent_run_id=run.id, run_type="tool")` and asserts `len(children) <= MAX_TURNS` AND `max_output_len <= 12_500` chars. |
| TEST-03 | Plan 06 | `test_explorer_sub_agent.py` — MAX_TURNS bound, timeout, no-progress detector, recursive-sub-agent rejection | SATISFIED | File exists at backend/scripts/test_explorer_sub_agent.py (1399 lines, well above 600 minimum). Suite registered as 17th in test_all.py SUITES list (between Exploration and Backfill). All 9 numbered sections present + canary covering EXPLORER-01..06 plus Pitfall 8 carry-forward (Section 10). Cleanup discipline confirmed: zero `DELETE FROM` / `TRUNCATE` matches; only per-id batched `.in_("id", batch)` deletes. **Operator must run the suite to confirm `Results: N passed, 0 failed`** — this is the runtime gate routed to human verification. |

**Coverage:** 7/7 declared requirements have implementation + test evidence. Zero ORPHANED requirements (every ID listed in a plan's `requirements:` field is accounted for).

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| (none) | — | — | — | No blocker or warning anti-patterns detected. The TEST-03 suite uses `time.sleep(0.15)` in Section 3's slow-dispatch stub (legitimate — tests the wall-clock guard). The `# TODO/FIXME` grep returns zero matches in the modified files. The `console.log` only-implementation check returns zero matches in Chat.tsx new callbacks. The `return null` / `=> {}` empty-handler pattern is absent. The `tool_metadata = None` initial state is overwritten by the SSE accumulator before persistence, not a stub. |

### Human Verification Required

1. **Run TEST-03 integration suite end-to-end**
   - Test: `cd backend && venv/Scripts/python scripts/test_explorer_sub_agent.py`
   - Expected: `Results: N passed, 0 failed` for N >= ~25 (canary + ~24 assertions across sections 2-10)
   - Why human: CLAUDE.md mandates do-NOT-auto-run-tests; suite requires backend on :8001 + Supabase + GEMINI_API_KEY; LLM-controlled tool selection means SC1's `<=8 tool calls` upper bound is observable only on a live run

2. **Manually trigger an Explorer-eligible chat in the UI**
   - Test: With both backend (:8001) and frontend (:5173) running, sign in as a regular user with at least 2-3 documents across multiple folders. Send a chat: "Find everything in my knowledge base about [topic]" or "Where are all my docs that mention [pattern]?"
   - Expected: UI shows sub-agent banner + tool steps populating in real time as Explorer iterates. Reload the chat — the persisted Explorer trace renders without rerunning the sub-agent.
   - Why human: End-to-end visual rendering through MessageList.SubAgentSection cannot be checked statically; Phase 6 owns the polished UI but Phase 5 must produce a non-broken experience today.

3. **Run Phase 4 regression suite**
   - Test: `cd backend && venv/Scripts/python scripts/test_exploration_tools.py`
   - Expected: Reports the established Phase 4 baseline (78/0 per Phase 4 close); no regression from Plan 03's openai_client.py edits
   - Why human: Regression check requires live backend; Plan 03 added a registration line and dispatch arm in openai_client.py — cannot statically prove zero behavioral drift on Phase 4 tool factories.

4. **Run Module-8 sub-agent regression suite**
   - Test: `cd backend && venv/Scripts/python scripts/test_sub_agents.py`
   - Expected: Remains green; analyze_document path's `tool_metadata.tools_used[0].tool == "analyze_document"` still works through Plan 04's refactored event_generator.
   - Why human: Plan 04 refactored the SSE event_generator's accumulator from `[0]`-fixed to `[-1]`-array. Static evidence shows the legacy fallback `parsed.get("agent_name", "analyze_document")` is preserved, but only a live run of test_sub_agents proves analyze_document's persisted JSONB shape is bit-compatible.

5. **LangSmith chain-span hierarchy check (EXPLORER-06 + Success Criterion 4)**
   - Test: After running TEST-03 with `LANGSMITH_API_KEY` set, open LangSmith UI and find the most recent `explore_knowledge_base` chain run.
   - Expected: Run is type `chain`; child runs are 1-8 `tool` runs (NOT flat siblings); each child's outputs JSON <= 12,500 chars.
   - Why human: Requires LangSmith UI inspection; auto-nesting via contextvars produces the hierarchy but only LangSmith renders it.

### Gaps Summary

No blocking gaps in static verification. All 4 Success Criteria have full code evidence + assertions in TEST-03 sections. All 7 requirement IDs (EXPLORER-01..06, TEST-03) are wired to specific plans with implementation evidence. The Phase 5 plumbing is COMPLETE in the codebase.

The status is `human_needed` (not `passed`) because:
- CLAUDE.md mandates "Do NOT run the full test suite automatically. Only run tests when the user explicitly asks." Phase 5's runtime gates (TEST-03 + Phase 4 regression + Module 8 regression + LangSmith UI) require live execution that the verifier cannot perform.
- The phase-close contract in 05-06-PLAN.md `<phase_close>` enumerates 7 gates, of which 4 are operator-run test commands and 1 is UAT manual verification — these are the "human_verification" items routed above.
- No code-level gap blocks the operator from running the gates today.

When the operator confirms all 5 human-verification items pass, this VERIFICATION.md may be re-stamped with `status: passed` (the static evidence underlying it does not change).

---

_Verified: 2026-05-09T23:30:00Z_
_Verifier: Claude (gsd-verifier, static analysis only per CLAUDE.md)_
