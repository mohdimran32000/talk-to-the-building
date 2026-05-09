---
phase: 05-explorer-sub-agent-sse-protocol-generalization
plan: 03
subsystem: api
tags: [gemini, function-calling, tool-dispatch, sub-agent, lazy-import, system-prompt, tool-09-invariant]

# Dependency graph
requires:
  - plan: 02
    provides: "run_explorer_sub_agent(query, user_id, supabase_client) generator yielding sub_agent_start/_tool_start/_tool_done/_token/_done events; ExplorerArgs Pydantic model; EXPLORER_ALLOWED_TOOLS; @traceable(run_type='chain') decorator on the generator"
  - phase: 04-five-exploration-tools-search-documents-extension
    provides: "openai_client._build_grep_tool factory + grep dispatch arm at L1033-1062 (last Phase 4 exploration arm); TOOL-09 layered-fallback wrapper at L1070+L1146 (LOCKED); has_documents conditional registration block pattern; _build_system_prompt structure with precision-tools-overview + scope-disambiguation bullets (LOCKED)"
provides:
  - "_build_explore_knowledge_base_tool() FunctionDeclaration factory in openai_client.py — Gemini-visible tool name 'explore_knowledge_base' with one required STRING param 'query'"
  - "function_declarations.append(_build_explore_knowledge_base_tool()) registered inside the if has_documents: block, after _build_grep_tool() and before the if text_to_sql_enabled: branch"
  - "elif tool_name == 'explore_knowledge_base': dispatch arm in stream_response — lazy-imports run_explorer_sub_agent, forwards generator events, captures sub_agent_done as result_text, falls through to the UNCHANGED TOOL-09 wrapper"
  - "_build_system_prompt() taught the LLM about explore_knowledge_base — tool-list bullet + TOOL SELECTION RULES disambiguation bullet (analyze_document vs search_documents vs explore_knowledge_base)"
affects: [05-04, 05-05, 05-06]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Lazy import inside elif branch to break openai_client <-> sub_agent circular cycle (Pitfall 1, mirrors analyze_document arm at L893)"
    - "Empty-query guard at top of dispatch arm — short-circuits run_explorer_sub_agent invocation when args.get('query', '') is missing or whitespace-only (T-05-16 DoS mitigation)"
    - "Empty-summary fallback string ('Exploration completed without a summary.') keeps result_text non-empty so the UNCHANGED TOOL-09 layered-fallback wrapper at L1148 + L1224 always receives content"
    - "Generator event forwarding loop (for evt_type, evt_data in run_explorer_sub_agent(...): yield (evt_type, evt_data)) — identical idiom to the analyze_document arm at L911-915"
    - "Phase 4 LOCKED non-fatal try/except wrapper around new factory registration (logger.warning on failure, never crashes the registration loop)"

key-files:
  created: []
  modified:
    - "backend/app/services/openai_client.py — extended (+78 net insertions, 0 deletions across three commits): new factory function (L484-520), registration line (L734), dispatch arm (L1121-1140), and two system-prompt bullets in _build_system_prompt"

key-decisions:
  - "Place factory `_build_explore_knowledge_base_tool` AFTER `_build_grep_tool` (sibling order matches registration order in the if has_documents: block)."
  - "Dispatch arm placement: BEFORE the `else: result_text = f'Unknown tool: {tool_name}'` fallthrough at L1148 — sibling of the existing 8 arms (search/web_search/analyze_document/list_files/tree/glob/read_document/grep)."
  - "Empty-query guard returns a short user-facing string instead of raising — preserves the V7 generator-never-raises invariant at the boundary between main agent and sub-agent."
  - "Empty-summary fallback uses 'Exploration completed without a summary.' (verbatim per plan) — defensive against rare paths in run_explorer_sub_agent where sub_agent_done yields empty data."
  - "System prompt EDIT B placed BETWEEN the Phase 4 precision-tools-overview bullet and the Phase 4 scope-disambiguation bullet — preserves both LOCKED bullets bit-identical and keeps the closing 'Only call ONE tool per turn.' as the last parts.append."

patterns-established:
  - "Tool factory + registration + dispatch + system-prompt pattern is now applied four times in openai_client.py (analyze_document, search_documents, the five Phase 4 precision tools as a group, and now explore_knowledge_base) — each addition is purely additive and the TOOL-09 layered-fallback wrapper has remained bit-identical across all of them."
  - "Sub-agent dispatch arm pattern: lazy import + arg extraction + defensive guard + generator-forwarding loop + result_text capture — established by analyze_document at L900-925, replicated faithfully by explore_knowledge_base at L1121-1140."

requirements-completed: [EXPLORER-01, EXPLORER-03]

# Metrics
duration: 7min
completed: 2026-05-09
---

# Phase 5 Plan 03: Explorer Tool Wiring in openai_client.py Summary

**Phase 5 Wave 2: Wired `explore_knowledge_base` as a callable Gemini tool from the main agent's perspective. Three additive edits to `backend/app/services/openai_client.py` — new factory function, registration in the if has_documents: block, and a new dispatch arm in stream_response that bridges into Plan 02's `run_explorer_sub_agent` generator. TOOL-09 layered-fallback wrapper at L1148 + L1224 remains bit-identical (grep -c == 2 preserved across the diff).**

## Performance

- **Duration:** ~7 min
- **Started:** 2026-05-09 (Wave 2 of phase 05)
- **Completed:** 2026-05-09
- **Tasks:** 3 (all autonomous, no checkpoints)
- **Files modified:** 1 (`backend/app/services/openai_client.py`)
- **Net insertions:** 78 lines (+43 in Task 1, +21 in Task 2, +14 in Task 3). Zero deletions across all three commits.

## Final Line Numbers in `backend/app/services/openai_client.py` (1251 LOC total)

| Symbol | Line range | Notes |
|--------|-----------|-------|
| `_build_explore_knowledge_base_tool` factory | L484-520 | Plan 03 Task 1 — placed between `_build_grep_tool` and `_sanitize_keyword_query` |
| Registration `function_declarations.append(_build_explore_knowledge_base_tool())` | L734 | Plan 03 Task 1 — inside `if has_documents:` block, AFTER `_build_grep_tool()` registration at L730, BEFORE `if text_to_sql_enabled:` at L737 |
| `elif tool_name == "explore_knowledge_base":` dispatch arm | L1121-1140 | Plan 03 Task 2 — sibling elif inside `stream_response`, AFTER grep arm (which now ends at L1119), BEFORE `else: result_text = f"Unknown tool: ..."` at L1142 |
| `_build_system_prompt` tool-list bullet (EDIT A) | L49 | Plan 03 Task 3 — `parts.append("- explore_knowledge_base: Open-ended exploration ...")` after the search_documents bullet |
| `_build_system_prompt` TOOL SELECTION RULES bullet (EDIT B) | L79-89 | Plan 03 Task 3 — disambiguation bullet between Phase 4 precision-tools-overview and Phase 4 scope-disambiguation |
| TOOL-09 layered-fallback wrapper #1 | **L1148** (was L1070 pre-edit; +78 line shift) | UNCHANGED — bit-identical to pre-Plan-03 state |
| TOOL-09 layered-fallback wrapper #2 | **L1224** (was L1146 pre-edit; +78 line shift) | UNCHANGED — bit-identical to pre-Plan-03 state |

## TOOL-09 Wrapper Bit-Identity Confirmation

```
$ grep -c "truncated_result = result_text\[:16000\] if len(result_text) > 16000 else result_text" backend/app/services/openai_client.py
2
```

The two occurrences (now at L1148 and L1224) are bit-identical to their pre-edit state. The full layered-fallback ladder body (`system_with_context = f"""..."""`, `response2 = client.models.generate_content_stream(...)`, the `has_response2_text` flag and non-streaming fallback at L1175-1188, and the raw-yield last-resort at L1189-1191) is also bit-identical — verified by:

```
$ git diff b991785..HEAD -- backend/app/services/openai_client.py | grep -E '^[+-][^+-]' | grep -E 'truncated_result|system_with_context|generate_content_stream'
(zero matches)
```

## Phase 4 Tests Carry-Forward Status

The Phase 4 exploration-tools test suite (`backend/scripts/test_exploration_tools.py`) was NOT executed live during Plan 03 — it requires a running backend and a live LangSmith endpoint. The Plan 03 changes are purely additive at three insertion points well outside Phase 4's tested code paths:

- **Phase 4 factories (`_build_list_files_tool`/`_build_tree_tool`/`_build_glob_tool`/`_build_read_document_tool`/`_build_grep_tool`):** zero edits. Each factory body is bit-identical to its Phase 4 final form.
- **Phase 4 dispatch arms (list_files/tree/glob/read_document/grep elif branches at L1023-1119):** zero edits. The new `elif tool_name == "explore_knowledge_base":` is appended AFTER the grep arm closes; the grep arm body remains unchanged.
- **Phase 4 SEARCH-03 self-scope hint bullet + scope-disambiguation bullet in `_build_system_prompt`:** zero edits. Both LOCKED Phase 4 bullets are preserved bit-identical (verified by literal-string grep of the bullet text).

Phase 4's 78/0 test count (per Phase 4 close-out) is therefore expected to carry forward intact when the suite next runs against a live backend. No regression vector.

## Task Commits

1. **Task 1: Add `_build_explore_knowledge_base_tool` factory + register in if has_documents:** — `e008ccf` (feat)
2. **Task 2: Wire `elif tool_name == "explore_knowledge_base":` dispatch arm in stream_response** — `939b8ae` (feat)
3. **Task 3: Teach `_build_system_prompt` the explore_knowledge_base tool (two surgical edits)** — `702f19e` (feat)

## Decisions Made

- **Factory placement after `_build_grep_tool`:** keeps the registration-order convention (factory definitions appear in roughly the same order they are appended to `function_declarations` in the `if has_documents:` block).
- **Dispatch arm placement BEFORE the `else: result_text = f"Unknown tool: ..."` fallthrough:** preserves the `else:` branch as the catch-all for genuinely unknown tool names; placing the new arm there keeps the dispatch logic linear and matches the analog pattern from analyze_document (which is also a sibling elif before the catch-all).
- **Empty-query guard returns a short user-facing string instead of yielding a sub_agent error event:** the main agent already has the TOOL-09 wrapper that turns `result_text` into a final answer, so a clean string flows through the wrapper as if it were a normal tool result. Yielding sub_agent events from this guard would require synthesizing a full `("sub_agent_start", ...)` + `("sub_agent_done", ...)` pair just to communicate "your tool args were empty" — overkill.
- **Empty-summary fallback string ('Exploration completed without a summary.'):** defensive against the rare path where `run_explorer_sub_agent` yields `("sub_agent_done", "")`. Without this, `result_text` would be an empty string, which the TOOL-09 wrapper's `if len(result_text) > 16000` predicate handles fine but the downstream `if not has_response2_text and result_text:` last-resort condition would silently skip — leaving the user with an empty response. The fallback string keeps the wrapper's three-stage ladder fully exercised.
- **System prompt EDIT B placement:** between the Phase 4 precision-tools-overview bullet and the Phase 4 scope-disambiguation bullet. This is the natural spot for tool-vs-tool disambiguation because the precision-tools bullet has just introduced the 5 sub-agent-internal tools, and the new bullet then explains when to elevate to the explore_knowledge_base sub-agent vs staying in single-shot mode.

## Deviations from Plan

None - plan executed exactly as written.

All literal-text acceptance criteria from all three tasks pass:

**Task 1 (10 criteria):**
- `def _build_explore_knowledge_base_tool() -> "types.FunctionDeclaration":` ✓
- `name="explore_knowledge_base"` ✓
- `required=["query"]` ✓
- `"query"` inside `properties={` block ✓
- `function_declarations.append(_build_explore_knowledge_base_tool())` ✓
- `Failed to build explore_knowledge_base tool (non-fatal)` ✓
- explore_knowledge_base registration line (L734) > _build_grep_tool registration line (L730) ✓
- registration is inside the `if has_documents:` block (8-space indent matches surrounding try/except blocks) ✓
- Module imports cleanly: `python -c "import app.services.openai_client"` exits 0 ✓
- Verify command output: `OK Plan 03 Task 1 verified` ✓

**Task 2 (10 criteria):**
- `elif tool_name == "explore_knowledge_base":` ✓
- `from app.services.sub_agent import run_explorer_sub_agent` (lazy inside elif) ✓
- `for evt_type, evt_data in run_explorer_sub_agent(` ✓
- `if evt_type == "sub_agent_done":` ✓
- `result_text = sub_agent_result` ✓
- `query_arg = args.get("query", "")` ✓
- `Exploration completed without a summary.` ✓
- New arm is sibling elif to existing arms (verified by elif placement before `else:` fallthrough) ✓
- TOOL-09 wrapper UNCHANGED: `grep -c` returns exactly `2` ✓
- Module imports cleanly post-edit ✓
- Verify command output: `OK Plan 03 Task 2 verified` ✓

**Task 3 (8 criteria):**
- `_build_system_prompt(has_documents=True, ..., ...)` contains `explore_knowledge_base` ≥2 times (count = 3 in practice — tool-list bullet + 2x in disambiguation bullet) ✓
- `_build_system_prompt(has_documents=False, ..., ...)` does NOT contain `explore_knowledge_base` ✓
- Contains literal phrase `OPEN-ENDED exploration` ✓
- Trio (analyze_document + search_documents + explore_knowledge_base) distinguished in the same paragraph ✓
- Phase 4 precision-tools bullet (`use \`tree\` to see the folder structure`) preserved ✓
- Phase 4 scope-disambiguation bullet (`Tool results carry a 'scope' field on every row`) preserved ✓
- Closing bullet `- Only call ONE tool per turn.` is still the LAST `parts.append` (verified at L104, comes after the new bullets at L49 and L79-89) ✓
- Verify command output: `OK Plan 03 Task 3 verified` ✓

**Plan-level verification (all pass):**
- Module + cross-module imports clean: `from app.services.openai_client import _build_explore_knowledge_base_tool, _build_system_prompt, stream_response` and `from app.services.sub_agent import run_explorer_sub_agent, run_sub_agent` both succeed.
- `_build_explore_knowledge_base_tool().name == 'explore_knowledge_base'` ✓
- `stream_response` AST-level dispatch arm presence (function found at L640 post-edit) ✓
- TOOL-09 wrapper bit-identity confirmed via `git diff b991785..HEAD` — zero +/- lines containing `truncated_result`, `system_with_context`, or `generate_content_stream` ✓
- `git diff --stat b991785..HEAD -- backend/app/services/openai_client.py` reports `1 file changed, 78 insertions(+)` with zero deletions ✓

## Issues Encountered

- **No worktree-local Python venv:** Verification used the parent repo's venv at `../../../../backend/venv/Scripts/python` (same approach as Plan 01 and Plan 02). All static + import + AST-level checks pass; the live Phase 4 exploration-tools test suite (`backend/scripts/test_exploration_tools.py`) was NOT run because it requires a backend server, but the diff is purely additive at three points outside Phase 4's tested code paths and `_build_grep_tool` + the grep dispatch arm are bit-identical (no possible Phase 4 regression).
- **Worktree branch base correction:** the worktree was initially at the same commit as the expected base (`b991785`) per `git rev-parse HEAD`, but `git merge-base HEAD b991785` reported `376b21d` (the Episode 1 freeze). This was a transient inconsistency — `git reset --hard b991785` brought HEAD bit-identical to the expected base before Plan 03 edits started. Confirmed via `git rev-parse HEAD` post-reset.

## Next Phase Readiness

- **Plan 04 can now generalize the SSE `event_generator`** in `app/api/messages.py` to forward the new `sub_agent_tool_start` and `sub_agent_tool_done` event types alongside the existing `sub_agent_start`, `sub_agent_token`, `sub_agent_done` events. The dispatch arm at L1121-1140 already forwards every (evt_type, evt_data) tuple from `run_explorer_sub_agent` upstream — Plan 04's job is purely to add SSE event-routing on the receiving side.
- **Plan 05 / Plan 06 (live Explorer test suite)** can drive end-to-end with a real Gemini call. The chain is now wired: chat -> Gemini sees explore_knowledge_base in tools -> Gemini emits `function_call(name="explore_knowledge_base", args={"query": "..."})` -> dispatch arm at L1121 -> `run_explorer_sub_agent(query, user_id, supabase_client)` (Plan 02) -> 5-tool sub-agent loop -> compact summary -> result_text -> TOOL-09 wrapper -> final streamed answer.
- **No new threat surface introduced.** All three threat-register entries (T-05-14 circular import, T-05-15 wrapper tampering, T-05-16 empty-query DoS) are mitigated in code per the plan's threat model. T-05-17 (system-prompt info disclosure) is accepted per plan.

## Threat Flags

None. The plan's `<threat_model>` mitigations are all enforced in code:

- **T-05-14 (E — circular import):** lazy `from app.services.sub_agent import run_explorer_sub_agent` INSIDE the elif arm at L1124 — resolves at first dispatch, AFTER both modules are loaded. Mirrors analyze_document at L893.
- **T-05-15 (T — wrapper tampering):** TOOL-09 wrapper bit-identity proven by `grep -c == 2` post-edit + zero +/- lines in the diff that touch the wrapper's literal text. Empty-query guard and empty-summary fallback ensure `result_text` is non-empty on every dispatch path so the wrapper never receives an empty input.
- **T-05-16 (D — empty-query DoS):** guard at L1128-1129 short-circuits before invoking `run_explorer_sub_agent`. The fallback string `"explore_knowledge_base called with empty query."` flows through the wrapper as a normal tool result.
- **T-05-17 (I — system-prompt info disclosure):** accepted per plan. The system prompt is shipped to Gemini, which is already privileged for the user's data.
- **T-05-18 (D — factory crash on registration):** the registration at L731-734 is wrapped in `try/except Exception as e: logger.warning(...)` matching the Phase 4 LOCKED non-fatal pattern (verified at L731-734 indentation matches the surrounding 8 try/except blocks).

No new security-relevant surface introduced beyond the planned mitigations. No new endpoints, no new auth paths, no new schema changes.

## Self-Check: PASSED

- File `backend/app/services/openai_client.py` exists at 1251 LOC (was 1173 pre-Plan-03; +78 insertions confirmed).
- Commit `e008ccf` (Task 1) exists in `git log --oneline`.
- Commit `939b8ae` (Task 2) exists in `git log --oneline`.
- Commit `702f19e` (Task 3) exists in `git log --oneline`.
- `python -c "import app.services.openai_client"` exits 0 from `backend/` (no SyntaxError, no ImportError, no AssertionError at module load).
- `python -c "from app.services.openai_client import _build_explore_knowledge_base_tool, _build_system_prompt, stream_response; from app.services.sub_agent import run_explorer_sub_agent, run_sub_agent"` exits 0 — all five top-level exports importable.
- Task 1 verify command prints `OK Plan 03 Task 1 verified`.
- Task 2 verify command prints `OK Plan 03 Task 2 verified`.
- Task 3 verify command prints `OK Plan 03 Task 3 verified`.
- Plan-level all-invariants check prints `OK Plan 03 PLAN-LEVEL verified`.
- TOOL-09 wrapper bit-identical: `grep -c "truncated_result = result_text\[:16000\]"` returns 2; `git diff b991785..HEAD` shows zero +/- lines touching `truncated_result`, `system_with_context`, or `generate_content_stream`.
- `git diff --stat b991785..HEAD -- backend/app/services/openai_client.py` reports 1 file changed, 78 insertions(+), 0 deletions.

---
*Phase: 05-explorer-sub-agent-sse-protocol-generalization*
*Completed: 2026-05-09*
