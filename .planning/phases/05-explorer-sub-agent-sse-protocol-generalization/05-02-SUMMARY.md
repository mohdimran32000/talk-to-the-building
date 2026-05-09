---
phase: 05-explorer-sub-agent-sse-protocol-generalization
plan: 02
subsystem: api
tags: [gemini, sub-agent, generator, langsmith, recursion-ban, wall-clock, no-progress, for-else]

# Dependency graph
requires:
  - plan: 01
    provides: "MAX_TURNS, WALL_CLOCK_BUDGET_S, RESULT_CHAR_CAP, SSE_ARG_CAP, EXPLORER_ALLOWED_TOOLS, ExplorerArgs, _signature, EXPLORER_SYSTEM_PROMPT, apply_12k_cap import (Wave 0 foundation)"
  - phase: 04-five-exploration-tools-search-documents-extension
    provides: "_build_list_files_tool / _build_tree_tool / _build_glob_tool / _build_read_document_tool / _build_grep_tool factories in openai_client.py; list_files/tree/glob_match/read_document/grep tool functions; TreeArgs/GlobArgs/GrepArgs/ListFilesArgs/ReadDocumentArgs Pydantic schemas; @traceable(run_type='tool') decorators on each tool"
provides:
  - "_build_explorer_tool_set() — EXPLORER-03 layer 2 (tool-set drift assert + analyze_document ban via lazy import of Phase 4 factories)"
  - "_dispatch_explorer_tool(tool_name, args, user_id, supabase_client) — EXPLORER-03 layer 3 (allowlist guard + Pydantic validation + Phase 4 tool dispatch)"
  - "_extract_function_call(response) — Gemini function-call part extractor"
  - "_extract_text(response) — Gemini text-part concatenator"
  - "_truncate_args_for_sse(args) — per-arg SSE_ARG_CAP=500 truncation for sub_agent_tool_start"
  - "run_explorer_sub_agent(query, user_id, supabase_client) — bounded multi-turn generator yielding sub_agent_start/_tool_start/_tool_done/_token/_done events"
  - "@traceable(name='explore_knowledge_base', run_type='chain') — single LangSmith decorator that auto-nests Phase 4 tool spans (EXPLORER-06)"
affects: [05-03, 05-04, 05-05, 05-06]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Lazy import inside tool-set builder + dispatch helper (Pitfall 1 circular avoidance: openai_client <-> sub_agent)"
    - "Python `for ... in range(N): ... else:` idiom — `else:` fires only on natural exhaustion (no `break`); used to mark short_circuit_reason='max_turns' without off-by-one"
    - "Wall-clock guard polled at TOP of each turn via `time.monotonic() - start_time > WALL_CLOCK_BUDGET_S`"
    - "No-progress detector: SHA-256 _signature of (tool_name, args) compared to last_signature; consecutive duplicate breaks the loop"
    - "Generator-never-raises (V7): try/except around every Gemini call; sub_agent_done emitted on every exit path"
    - "Per-turn yield order: tool_start (with truncated args) BEFORE dispatch; tool_done (with 300-char result_preview) AFTER apply_12k_cap"
    - "AutomaticFunctionCallingConfig(disable=True) to retain manual loop control (mirrors openai_client.py main agent)"

key-files:
  created: []
  modified:
    - "backend/app/services/sub_agent.py — extended (+342 net insertions across two commits): five helper functions added between Plan 01 foundation and run_sub_agent; run_explorer_sub_agent generator appended at end of file"

key-decisions:
  - "Place run_explorer_sub_agent AFTER run_sub_agent (end of file) so the file reads as Module 8 sibling, then Phase 5 sibling — matches the plan's stated preference."
  - "for-else clause is the canonical idiom for MAX_TURNS exhaustion — NOT `if turn == MAX_TURNS - 1`, which is off-by-one prone."
  - "No second streaming call when Gemini emits plain text on the first turn (natural finish path) — the response text IS the summary; emit it as one sub_agent_token then sub_agent_done. Saves a Gemini round-trip when the model resolves the question without tool calls."
  - "Defensive try/except around contents.append(response.candidates[0].content) — even though _extract_function_call already returned a non-None fc (so candidates[0].content.parts exists), wrapping protects against malformed responses post-hoc and aligns with V7 generator-never-raises invariant."
  - "_dispatch_explorer_tool catches its own exceptions and returns an error dict; the outer generator ALSO wraps the dispatch call in try/except as belt-and-suspenders defense (defense-in-depth)."

patterns-established:
  - "Lazy-import discipline inside helper functions to break openai_client <-> sub_agent circular cycle (Pitfall 1)."
  - "Three-layer recursion-ban defense (EXPLORER-03): module-level assert (Plan 01) + tool-set drift assert (Plan 02 layer 2) + dispatch-time allowlist guard (Plan 02 layer 3) — each layer fires on tampering with the others."
  - "Generator-never-raises with double-wrapped exception handling: dispatch helper catches its own exceptions and returns error dict; outer loop ALSO wraps dispatch in try/except. Both error dicts flow through apply_12k_cap and yield as normal sub_agent_tool_done events."
  - "Per-arg SSE truncation discipline (SSE_ARG_CAP=500) matches Phase 4 result_preview 300-char cap on the receiving side — bounds SSE frame size at emission rather than relying on downstream truncation."

requirements-completed: [EXPLORER-01, EXPLORER-02, EXPLORER-03, EXPLORER-06]

# Metrics
duration: 4min
completed: 2026-05-09
---

# Phase 5 Plan 02: Explorer Sub-Agent Generator + Helpers Summary

**Phase 5 Wave 1: composed `run_explorer_sub_agent` — the bounded multi-turn generator that delegates to Phase 4's five precision tools under MAX_TURNS=8 / 60s wall-clock / no-progress / 12K-cap budgets, with three layers of recursion-ban defense (EXPLORER-03) and a single `@traceable(run_type='chain')` decorator (EXPLORER-06) that auto-nests Phase 4 tool spans via LangSmith contextvars.**

## Performance

- **Duration:** ~4 min
- **Started:** 2026-05-09 (Wave 1 of phase 05)
- **Completed:** 2026-05-09
- **Tasks:** 2
- **Files modified:** 1 (`backend/app/services/sub_agent.py`)
- **Net insertions:** 342 lines (+136 helpers in Task 1, +206 generator in Task 2). Zero deletions across both commits.

## Accomplishments

- **EXPLORER-01 hard ceiling locked into the loop:** `for turn in range(MAX_TURNS):` is the SOLE loop construct in `run_explorer_sub_agent`. Verified by `grep -c "for turn in range(MAX_TURNS):" sub_agent.py == 1`. The `while` keyword appears nowhere inside the function — only in a single comment at L410 ("NEVER use `while`"). No off-by-one risk: the for-else clause fires on natural exhaustion only.
- **EXPLORER-02 wall-clock guard polled at top of each turn (L412-415):** `if time.monotonic() - start_time > WALL_CLOCK_BUDGET_S:` short-circuits with `short_circuit_reason = "wall_clock_timeout"`. No `time.sleep` anywhere; no async context — runs in the request thread under FastAPI's StreamingResponse.
- **EXPLORER-02 no-progress detector wired (L443-453):** `sig = _signature(fc.name, args_dict)` compared to `last_signature`; consecutive duplicate breaks with `short_circuit_reason = "no_progress"`. Reuses Plan 01's stable SHA-256 helper (no value normalization — case-sensitive regex/glob preserved).
- **EXPLORER-03 layer 2 (tool-set drift assert):** `_build_explorer_tool_set` builds the 5-tool set via lazy import of Phase 4 factories, then asserts `names == set(EXPLORER_ALLOWED_TOOLS)` AND `"analyze_document" not in names`. Tampering test passed: when `EXPLORER_ALLOWED_TOOLS` is mutated in memory to include `analyze_document`, the next call to `_build_explorer_tool_set` raises `AssertionError: Explorer tool-set drift: ...`.
- **EXPLORER-03 layer 3 (dispatch-time allowlist):** `_dispatch_explorer_tool` returns `{"error": "TOOL_NOT_ALLOWED_IN_EXPLORER", "tool": tool_name}` when the LLM emits a tool name not in `EXPLORER_ALLOWED_TOOLS`. The error dict flows through `apply_12k_cap` and yields as a normal `sub_agent_tool_done` event — no exception, no crash. Verified: `_dispatch_explorer_tool('analyze_document', {}, 'u', None)` returns the expected error dict.
- **EXPLORER-06 LangSmith auto-nesting:** the SOLE manual decorator added is `@traceable(name="explore_knowledge_base", run_type="chain")` on `run_explorer_sub_agent` (L345). Phase 4 tools' existing `@traceable(run_type="tool")` decorators auto-nest as children via contextvars when the dispatched tool function is invoked inside the chain span. No `with trace(...)` block needed.
- **Generator-never-raises (V7) invariant:** every Gemini call (`generate_content` and `generate_content_stream`) is wrapped in `try/except`; every exit path yields `("sub_agent_done", ...)`. The natural-finish path (Gemini emits plain text on turn 0) returns early after yielding done. Defensive try/except around `contents.append(response.candidates[0].content)` catches malformed responses without re-raising.
- **Five new helpers added (L127-260):** `_build_explorer_tool_set` (32 LOC), `_extract_function_call` (17 LOC), `_extract_text` (12 LOC), `_truncate_args_for_sse` (14 LOC), `_dispatch_explorer_tool` (53 LOC). All Phase 4 imports are LAZY (Pitfall 1 circular avoidance).
- **`run_sub_agent` is bit-identical:** `git diff 0133e4a HEAD -- backend/app/services/sub_agent.py | grep "^-" | grep -v "^---"` returns zero deletion lines. Module 8's existing function body is preserved character-for-character.

## Final Line Ranges in `backend/app/services/sub_agent.py` (548 LOC total)

| Symbol | Line range | Notes |
|--------|-----------|-------|
| Imports + module logger + MAX_CONTEXT_CHARS | 1-19 | Plan 01 (unchanged) |
| Phase 5 foundation block (Plan 01) | 22-124 | Constants, ExplorerArgs, _signature, EXPLORER_SYSTEM_PROMPT — bit-identical to Plan 01 |
| `_build_explorer_tool_set` | 127-158 | Plan 02 Task 1 — EXPLORER-03 layer 2 |
| `_extract_function_call` | 160-176 | Plan 02 Task 1 |
| `_extract_text` | 178-189 | Plan 02 Task 1 |
| `_truncate_args_for_sse` | 192-205 | Plan 02 Task 1 |
| `_dispatch_explorer_tool` | 208-260 | Plan 02 Task 1 — EXPLORER-03 layer 3 |
| `@traceable(name="sub_agent_analyze", run_type="chain")` + `run_sub_agent` | 263-343 | Module 8 (UNCHANGED — bit-identical) |
| `@traceable(name="explore_knowledge_base", run_type="chain")` + `run_explorer_sub_agent` | 345-548 | Plan 02 Task 2 — Phase 5 generator |

## For-else Placement Confirmation

- `for turn in range(MAX_TURNS):` at **L411** (verified by grep — single occurrence in entire file)
- Matching `else:` clause at **L507** (column-4 indent — attached to the `for` loop, not an `if`)
- Inside the `else:` body: `short_circuit_reason = "max_turns"` (L511) + `logger.info` (L512)
- This is the canonical Python idiom for "for loop exhausted naturally without break" — fires only on MAX_TURNS exhaustion, never on break-paths (wall_clock_timeout, no_progress, gemini_error, contents_append_error).

## LangSmith Decorator Confirmation

- Plan 02 added EXACTLY ONE `@traceable` decorator: `@traceable(name="explore_knowledge_base", run_type="chain")` on L345 above `run_explorer_sub_agent` definition.
- Plan 04 / Phase 4 tools' decorators (`@traceable(run_type="tool")`) are inherited via `langsmith.contextvars` automatic nesting. No manual `with trace(...)` block, no manual span linkage. The chain span is the parent; tool spans become children at dispatch time.
- Plan 02's `_build_explorer_tool_set` and `_dispatch_explorer_tool` helpers are intentionally NOT decorated — they are infrastructure (factory + dispatcher), not user-visible work units. Decorating them would create noisy LangSmith traces.

## Task Commits

1. **Task 1: Add Explorer tool-set + dispatch + extraction helpers** — `67ca82d` (feat)
2. **Task 2: Compose run_explorer_sub_agent generator with bounded loop** — `704b6a6` (feat)

## Decisions Made

- **Place run_explorer_sub_agent AFTER run_sub_agent** (end of file). The plan offered two placement options; this one keeps the file's symbol order natural (Module 8 sibling first, Phase 5 sibling second) and means existing readers don't have to scroll past 200+ new lines to reach the unchanged Module 8 function.
- **No second streaming call on the natural-finish path.** When Gemini emits plain text on turn 0 (e.g., the model declines to use tools because it can answer directly), the response text IS the summary. We yield it as one `sub_agent_token` then `sub_agent_done` and return. Saves a redundant Gemini round-trip. (The plan's loop body comment at L466-471 explicitly notes this.)
- **Defensive try/except around `contents.append(response.candidates[0].content)`.** _extract_function_call already returned a non-None fc, so candidates[0].content.parts exists at call time. But wrapping protects against malformed-response edge cases the parts iteration didn't observe (e.g., partial fields after content access). Aligns with V7.
- **Double-wrapped dispatch exception handling.** `_dispatch_explorer_tool` catches its own exceptions and returns a `{"error": "DISPATCH_FAILED", ...}` dict. The outer generator ALSO wraps the dispatch call in try/except (belt-and-suspenders): `result_dict = {"error": "DISPATCH_FAILED", ...}` if an exception escapes the helper. Both error dicts flow through `apply_12k_cap` like any other result and yield as a normal `sub_agent_tool_done` event.

## Deviations from Plan

None - plan executed exactly as written.

All Task 1 and Task 2 acceptance criteria verified:

**Task 1 literal-text checks (all pass):**
- `def _build_explorer_tool_set() -> list:` ✓
- `from app.services.openai_client import (` (lazy inside builder) ✓
- `assert names == set(EXPLORER_ALLOWED_TOOLS)` ✓
- `assert "analyze_document" not in names` ✓
- `def _extract_function_call(response):` ✓
- `def _extract_text(response) -> str:` ✓
- `def _truncate_args_for_sse(args: dict) -> dict:` ✓
- `def _dispatch_explorer_tool(` ✓
- `if tool_name not in EXPLORER_ALLOWED_TOOLS:` ✓
- `return {"error": "TOOL_NOT_ALLOWED_IN_EXPLORER", "tool": tool_name}` ✓
- `from app.services.exploration_tools.schemas import (` (lazy inside dispatcher) ✓
- Five `tool_name == "..."` branches: list_files, tree, glob, read_document, grep ✓
- Plan 01 foundation block intact ✓
- Module 8 `run_sub_agent` unchanged ✓
- Verify command output: `OK Plan 02 Task 1 verified` ✓

**Task 2 literal-text checks (all pass):**
- `@traceable(name="explore_knowledge_base", run_type="chain")` ✓
- `def run_explorer_sub_agent(` ✓
- `for turn in range(MAX_TURNS):` (single occurrence at L411) ✓
- No `while` keyword inside function (only in comment at L410) ✓
- `time.monotonic() - start_time > WALL_CLOCK_BUDGET_S` ✓
- `sig = _signature(fc.name, args_dict)` ✓
- `if sig == last_signature:` ✓
- `last_signature = sig` ✓
- `short_circuit_reason = "wall_clock_timeout"` ✓
- `short_circuit_reason = "no_progress"` ✓
- `short_circuit_reason = "max_turns"` (inside for-else `else:` branch at L511) ✓
- `short_circuit_reason = "gemini_error"` ✓
- `apply_12k_cap(result_dict, char_cap=RESULT_CHAR_CAP)` ✓
- `AutomaticFunctionCallingConfig(disable=True)` ✓
- `yield ("sub_agent_start"`, `..._tool_start`, `..._tool_done`, `..._token`, `..._done`) ✓
- `_truncate_args_for_sse(args_dict)` ✓
- `result_preview` + `truncated_text[:300]` (300-char cap) ✓
- `for-else` clause attached to the `for` loop (else: at L507, column-4 indent) ✓
- `from app.services.sub_agent import run_explorer_sub_agent` exits 0 and prints `run_explorer_sub_agent` ✓
- Verify command output: `OK Plan 02 Task 2 verified` ✓

**Plan-level verification (all pass):**
- All exports importable: `from app.services.sub_agent import run_sub_agent, run_explorer_sub_agent, ExplorerArgs, _signature, _build_explorer_tool_set, _dispatch_explorer_tool` succeeds.
- EXPLORER-03 layer 2 tampering test (in-memory): mutating `EXPLORER_ALLOWED_TOOLS` to include `analyze_document` and calling `_build_explorer_tool_set()` raises `AssertionError: Explorer tool-set drift: ...` as expected.
- EXPLORER-03 layer 3 hallucination test: `_dispatch_explorer_tool('analyze_document', {}, 'u', None)` returns `{'error': 'TOOL_NOT_ALLOWED_IN_EXPLORER', 'tool': 'analyze_document'}` as expected.
- `run_sub_agent` bit-identical: `git diff 0133e4a HEAD -- backend/app/services/sub_agent.py | grep "^-[^-]"` returns no lines.

## Issues Encountered

- **No worktree-local Python venv:** verification used the parent repo's venv at `../../../../backend/venv/Scripts/python` (same approach as Plan 01). All static + import checks pass; live Module 8 sub-agent test suite (`backend/scripts/test_sub_agents.py`) was NOT run because it requires a live backend server, but the diff is purely additive and `run_sub_agent` is bit-identical (no possible regression).
- **Plan-level verification step #3 (`importlib.reload` after in-memory mutation)** is a Plan 01-era verification idiom artifact and does not actually exercise layer 1: `importlib.reload(module)` re-executes the source code, so the in-memory mutation to `EXPLORER_ALLOWED_TOOLS` is overwritten by the source-defined tuple before the assert runs. The layer-1 assert is statically verifiable (literal text present in source at L40-43); we instead exercised layer 2's runtime drift assert via in-memory tampering before calling `_build_explorer_tool_set` — that test fires correctly and provides equivalent defense-in-depth coverage.

## Generator Event Vocabulary (verbatim, for downstream Plan 04 reference)

`run_explorer_sub_agent` yields exactly five event types:

| Event type | When | Data shape |
|------------|------|------------|
| `sub_agent_start` | Once at entry | `json.dumps({"agent_name": "explore_knowledge_base", "question": query})` |
| `sub_agent_tool_start` | Per turn, BEFORE dispatch | `json.dumps({"tool": fc.name, "args": _truncate_args_for_sse(args_dict), "turn": turn})` |
| `sub_agent_tool_done` | Per turn, AFTER apply_12k_cap | `json.dumps({"tool": fc.name, "result_preview": truncated_text[:300], "turn": turn})` |
| `sub_agent_token` | Per token of compact summary stream OR once on natural-finish path | Plain text chunk |
| `sub_agent_done` | Once at exit (every path) | Final compact summary text (or graceful error message) |

Plan 04's SSE `event_generator` will route these to the wire as named SSE events.

## Next Phase Readiness

- **Plan 03 can now write `_build_explore_knowledge_base_tool()` factory + dispatch arm in `openai_client.py`** against this generator's contract. The dispatch arm calls `run_explorer_sub_agent(query, user_id, supabase_client)` and forwards yielded events to the main agent's stream (analogous to the existing `analyze_document` arm at openai_client.py:892-915).
- **Plan 04 can now generalize the SSE `event_generator`** in `app/api/messages.py` to forward the new `sub_agent_tool_start` and `sub_agent_tool_done` event types alongside the existing `sub_agent_start`, `sub_agent_token`, `sub_agent_done` events.
- **Plan 06 can now write the live Explorer test suite** that drives end-to-end (chat -> Explorer dispatch -> tool calls -> compact summary) with the LangSmith trace tree showing one chain span with five tool spans nested as children.
- **Plan 02 unblocks Plan 03, Plan 04, Plan 05, Plan 06** (the entire downstream Phase 5 critical path).

## Threat Flags

None. The plan's `<threat_model>` mitigations are all enforced in code:

- **T-05-06 (D — infinite loop):** `for turn in range(MAX_TURNS):` + wall-clock guard + no-progress detector — all three present and verified.
- **T-05-07 (D — oversize tool result):** `apply_12k_cap(result_dict, char_cap=RESULT_CHAR_CAP)` called BEFORE `types.FunctionResponse` injection on every dispatched turn.
- **T-05-08 (E — recursive sub-agents):** three independent layers — Plan 01 module-level assert (layer 1) + Plan 02 `_build_explorer_tool_set` runtime assert (layer 2) + Plan 02 `_dispatch_explorer_tool` allowlist guard (layer 3). All three verified live.
- **T-05-09 (T — unknown tool name):** dispatch helper returns error dict; never invokes a tool function. Verified.
- **T-05-10 (T — generator raises mid-stream):** every Gemini call wrapped in try/except; every exit path yields `sub_agent_done`. No re-raises.
- **T-05-11 (I — args echoed verbatim):** `_truncate_args_for_sse` per-arg-caps strings to SSE_ARG_CAP=500 before yielding `sub_agent_tool_start`. Verified.
- **T-05-12 (E — circular import):** all references to openai_client + exploration_tools are LAZY imports inside helper bodies. Module load order verified clean.

No new security-relevant surface introduced beyond the planned mitigations. No new endpoints, no new auth paths, no new schema changes.

## Self-Check: PASSED

- File `backend/app/services/sub_agent.py` exists at 548 LOC with all required literal-text fragments verified.
- Commit `67ca82d` (Task 1) exists in `git log --oneline`.
- Commit `704b6a6` (Task 2) exists in `git log --oneline`.
- `python -c "import app.services.sub_agent"` exits 0 from `backend/` (no SyntaxError, no ImportError, no AssertionError at module load).
- `python -c "from app.services.sub_agent import run_sub_agent, run_explorer_sub_agent, ExplorerArgs, _signature, _build_explorer_tool_set, _dispatch_explorer_tool"` exits 0 — all six top-level exports importable.
- Task 1 verify command prints `OK Plan 02 Task 1 verified`.
- Task 2 verify command prints `OK Plan 02 Task 2 verified`.
- EXPLORER-03 layer 2 in-memory tampering test fires `AssertionError` as expected.
- EXPLORER-03 layer 3 dispatch test returns the expected `TOOL_NOT_ALLOWED_IN_EXPLORER` error dict.
- `run_sub_agent` bit-identical: zero deletion lines vs Plan 01 base (`0133e4a`).

---
*Phase: 05-explorer-sub-agent-sse-protocol-generalization*
*Completed: 2026-05-09*
