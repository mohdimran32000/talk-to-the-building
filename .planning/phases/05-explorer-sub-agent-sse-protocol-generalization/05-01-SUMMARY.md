---
phase: 05-explorer-sub-agent-sse-protocol-generalization
plan: 01
subsystem: api
tags: [pydantic, gemini, sub-agent, hashlib, sha256, generator, langsmith]

# Dependency graph
requires:
  - phase: 04-five-exploration-tools-search-documents-extension
    provides: "exploration_tools._truncate.apply_12k_cap (12K result-cap helper); exploration_tools.schemas (Pydantic v2 BaseModel pattern with model_config={'extra':'ignore'})"
  - phase: 03-folder-system-and-routing
    provides: "Pydantic 'extra=ignore' defense layer (LOCKED) reused for ExplorerArgs"
provides:
  - "MAX_TURNS=8, WALL_CLOCK_BUDGET_S=60.0, RESULT_CHAR_CAP=12_000, SSE_ARG_CAP=500 module-level budget constants"
  - "EXPLORER_ALLOWED_TOOLS tuple ('tree','glob','grep','list_files','read_document') + module-import setup-time assert (EXPLORER-03 layer 1, recursion ban)"
  - "ExplorerArgs Pydantic v2 model — query bounded (1..2000 chars), extras silently dropped"
  - "_signature(tool_name, args) — SHA-256 of json.dumps(sort_keys=True, default=str), stable across CPython versions, no value normalization"
  - "EXPLORER_SYSTEM_PROMPT — ASCII-only system instruction stating 8-turn / 60s / 12K caps and analyze_document ban"
  - "apply_12k_cap import wired (Plan 02 will call it; Wave 0 foundation is now import-complete)"
affects: [05-02, 05-03, 05-04, 05-05, 05-06]

# Tech tracking
tech-stack:
  added: [hashlib, time, pydantic.BaseModel/Field]
  patterns:
    - "Module-level allowlist tuple + setup-time `assert` as recursion-ban defense layer 1"
    - "Pydantic v2 args model with extra='ignore' — Phase 3 LOCKED defense layer reused for sub-agent tool args"
    - "Stable signature via json.dumps(sort_keys=True, default=str) + hashlib.sha256, no value normalization (case-sensitive regex/glob preserved)"

key-files:
  created: []
  modified:
    - "backend/app/services/sub_agent.py — extended with Phase 5 foundation block (constants, ExplorerArgs, _signature, EXPLORER_SYSTEM_PROMPT, EXPLORER_ALLOWED_TOOLS, setup-time assert, new imports) above unchanged run_sub_agent"

key-decisions:
  - "Hash args VERBATIM (no value normalization) per RESEARCH.md §Open Questions #2 — case-sensitive regex/glob patterns must remain distinct signatures."
  - "ExplorerArgs v1 surface is single-arg `query: str`; optional `scope` narrowing arg deferred to v2 (LLM can still pass scope to individual tool calls in the loop)."
  - "EXPLORER_SYSTEM_PROMPT is ASCII-only (uses `<=` not U+2264) — shipped to Gemini verbatim."
  - "apply_12k_cap import wired now (used by Plan 02) so foundation is import-complete and Plan 02 can focus on loop control flow."
  - "Recursion ban implemented at layer 1 (module-level assert at import). Plan 02 adds layers 2 (tool-set builder) and 3 (dispatch-time check)."

patterns-established:
  - "Setup-time assert as static recursion-ban defense: `assert 'analyze_document' not in EXPLORER_ALLOWED_TOOLS` fires AT IMPORT, prevents module load if a future maintainer tampers with the allowlist."
  - "Sibling extension of an existing service module (sub_agent.py) — net-new helpers ABOVE the existing `@traceable run_sub_agent` function with zero modification to the existing function (bit-identical preserved)."

requirements-completed: [EXPLORER-01, EXPLORER-02, EXPLORER-03]

# Metrics
duration: 6min
completed: 2026-05-09
---

# Phase 5 Plan 01: Explorer Sub-Agent Foundation Summary

**Phase 5 Wave 0 foundation: ExplorerArgs Pydantic model, four budget constants, _signature SHA-256 helper, EXPLORER_SYSTEM_PROMPT, allowlist tuple, and EXPLORER-03 layer-1 setup-time assert all landed above unchanged run_sub_agent in backend/app/services/sub_agent.py.**

## Performance

- **Duration:** ~6 min
- **Started:** 2026-05-09T18:27:00Z
- **Completed:** 2026-05-09T18:33:15Z
- **Tasks:** 1
- **Files modified:** 1

## Accomplishments

- **EXPLORER-01 / EXPLORER-02 budgets locked at module level:** MAX_TURNS=8, WALL_CLOCK_BUDGET_S=60.0, RESULT_CHAR_CAP=12_000, SSE_ARG_CAP=500. Plan 02's loop body imports these directly — no inline magic numbers.
- **EXPLORER-03 layer 1 (recursion ban) is now compile-time-enforced:** the `assert "analyze_document" not in EXPLORER_ALLOWED_TOOLS` fires at module import. A future maintainer who edits the tuple to include `analyze_document` cannot get the module to load. Plan 02 will add layers 2 and 3 for defense in depth.
- **ExplorerArgs Pydantic v2 model is the single LLM-facing surface for the explore_knowledge_base tool:** `query: str` bounded (1..2000 chars), extras silently dropped via `model_config = {"extra": "ignore"}` (Phase 3 LOCKED defense layer reused).
- **`_signature(tool_name, args)` no-progress detector helper:** stable SHA-256 hex digest of `json.dumps({"tool":..., "args":...}, sort_keys=True, default=str)`. Whitespace-insensitive on dict keys; preserves case sensitivity in args (no value normalization, per RESEARCH.md §Open Questions #2).
- **EXPLORER_SYSTEM_PROMPT (ASCII-only) defines the 5-tool surface, hard limits (8 calls / 60s / 12K cap), exploration strategy, and explicit ban on analyze_document.** Plan 02 will compose this into the Gemini call.
- **`apply_12k_cap` import wired now** — Wave 0 foundation is import-complete; Plan 02 can focus solely on the loop body.
- **`run_sub_agent` is bit-identical** — git diff stat: `1 file changed, 109 insertions(+)` (zero deletions, zero edits inside the existing function).

## Task Commits

1. **Task 1: Add Phase 5 imports + constants + ExplorerArgs + EXPLORER_ALLOWED_TOOLS + setup-time assert + _signature + EXPLORER_SYSTEM_PROMPT** — `dcd2ffa` (feat)

## Files Created/Modified

- `backend/app/services/sub_agent.py` — extended (109 insertions, 0 deletions). New content lives at lines 3-15 (added imports) and lines 22-125 (Phase 5 foundation block) above the unchanged `@traceable` decorator (now at L127) and `run_sub_agent` function (now starting at L128). Pre-Plan-01 line ranges preserved bit-for-bit.

## Decisions Made

None new — all decisions were already locked by 05-RESEARCH.md and 05-PATTERNS.md before plan execution. Implementation followed the plan verbatim:

- Hash args verbatim (no value normalization) per RESEARCH.md §Open Questions #2 — case sensitivity in regex/glob patterns is preserved.
- v1 ExplorerArgs surface is single-arg `query`; optional `scope` arg deferred to v2.
- EXPLORER_SYSTEM_PROMPT is ASCII-only (`<=` not U+2264) — shipped to Gemini verbatim.

## Deviations from Plan

None - plan executed exactly as written.

All 23 literal-text acceptance criteria pass:
- `MAX_TURNS = 8`, `WALL_CLOCK_BUDGET_S = 60.0`, `RESULT_CHAR_CAP = 12_000`, `SSE_ARG_CAP = 500` ✓
- `EXPLORER_ALLOWED_TOOLS = ("tree", "glob", "grep", "list_files", "read_document")` ✓
- `assert "analyze_document" not in EXPLORER_ALLOWED_TOOLS` ✓
- `class ExplorerArgs(BaseModel):` with `min_length=1`, `max_length=2000`, `model_config = {"extra": "ignore"}` ✓
- `def _signature(tool_name: str, args: dict) -> str:` with `sort_keys=True` and `hashlib.sha256(canonical.encode("utf-8")).hexdigest()` ✓
- `EXPLORER_SYSTEM_PROMPT = ` containing `8 tool calls maximum`, `60 seconds wall-clock time`, `12,000 characters` ✓
- New imports: `import hashlib`, `import time`, `from pydantic import BaseModel, Field`, `from app.services.exploration_tools._truncate import apply_12k_cap` ✓
- Existing `def run_sub_agent(` and `@traceable(name="sub_agent_analyze", run_type="chain")` unchanged ✓

Verification command output: `OK Plan 01 verified` (covers all signature determinism, ExplorerArgs validation, smuggled-field drop, prompt-content checks).

## Issues Encountered

- **Worktree branch base correction:** the worktree was initially based on the old Episode 1 freeze commit (`376b21d`) instead of the feature branch HEAD (`0c9c1095`). Resolved per `<worktree_branch_check>` instructions: `git reset --hard 0c9c1095...` brought the working tree to the correct base, restoring the `exploration_tools/` package referenced by the new import.
- **No worktree-local Python venv:** verification was run against the parent repo's venv (`../../../../backend/venv/Scripts/python`). Standard import + acceptance-text checks all pass; the existing Module 8 sub_agent test suite (`backend/scripts/test_sub_agents.py`) was NOT run because it requires a live backend, but the diff is purely additive and the existing function is bit-identical (no possible regression in `run_sub_agent` behavior).

## EXPLORER_SYSTEM_PROMPT (verbatim, for downstream plan reference)

```text
You are an isolated knowledge-base exploration sub-agent.

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
- <= 8 sentences
- Cite folder paths and document names with the scope tag (user|global)
- If you stopped early, say why ('hit turn budget', 'found enough', etc.)
```

(Note: the `—` em-dashes are intentional Unicode (U+2014). The prompt-line `<= 8 sentences` uses ASCII `<=` (two chars), NOT U+2264, per the plan's ASCII-only constraint for the inequality.)

## Confirmation: run_sub_agent is bit-identical

```
$ git diff --stat HEAD~1 HEAD -- backend/app/services/sub_agent.py
 backend/app/services/sub_agent.py | 109 ++++++++++++++++++++++++++++++++++++++
 1 file changed, 109 insertions(+)
```

Zero deletions. The body of `run_sub_agent` (now at lines 128-205) is unchanged from its pre-Phase-5 state.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- **Plan 02 can now compose `run_explorer_sub_agent()`** by importing the foundation primitives:
  ```python
  from app.services.sub_agent import (
      MAX_TURNS, WALL_CLOCK_BUDGET_S, RESULT_CHAR_CAP, SSE_ARG_CAP,
      EXPLORER_ALLOWED_TOOLS, EXPLORER_SYSTEM_PROMPT,
      ExplorerArgs, _signature,
  )
  ```
- `apply_12k_cap` is already wired into the imports — Plan 02 can call it directly without adding a new import line.
- Layer-1 of EXPLORER-03 (module-level allowlist + assert) is in place. Plan 02 must add layer 2 (tool-set builder runtime guard rejecting any FunctionDeclaration whose name is not in EXPLORER_ALLOWED_TOOLS) and layer 3 (dispatch-time tool-name allowlist check before invoking the tool function).
- `time` is imported but not yet used (reserved for Plan 02's wall-clock budget tracking via `time.monotonic()`).

## Self-Check: PASSED

- File `backend/app/services/sub_agent.py` exists and contains all 23 required literal-text fragments.
- Commit `dcd2ffa` exists in `git log --oneline`.
- `python -c "import app.services.sub_agent"` exits 0 from `backend/` with the parent venv (no SyntaxError, no AssertionError, no ImportError).
- Verify command from Task 1 prints `OK Plan 01 verified`.
- `from app.services.sub_agent import run_sub_agent` succeeds and prints `run_sub_agent` (zero regression on existing function).

---
*Phase: 05-explorer-sub-agent-sse-protocol-generalization*
*Completed: 2026-05-09*
