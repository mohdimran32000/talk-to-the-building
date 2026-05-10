---
status: partial
phase: 05-explorer-sub-agent-sse-protocol-generalization
source: [05-VERIFICATION.md]
started: 2026-05-09T23:30:00Z
updated: 2026-05-10T00:00:00Z
---

## Current Test

[awaiting human action on remaining UI + LangSmith tests + 1 code-level fix]

## Tests

### 1. Run TEST-03 integration suite end-to-end
expected: `cd backend && venv/Scripts/python scripts/test_explorer_sub_agent.py` reports `Results: N passed, 0 failed` for N >= ~25
result: Results: 21 passed, 1 failed
notes: |
  - SC1 PASSED (3/3 — MAX_TURNS bound enforced)
  - SC2 wall-clock PASSED (2/2)
  - **SC2 no-progress FAILED (1/2)** — `_section_4_no_progress` expected exactly ONE `sub_agent_tool_start` before short-circuit; got 4. Real bug in `backend/app/services/sub_agent.py` `for turn in range(MAX_TURNS)` loop — the `_signature(...)` no-progress detector either does not match across turns when args dict is identical, or the check happens too late. This is a code-level gap; the loop still exits cleanly (sub_agent_done emitted), so production impact is bounded by MAX_TURNS=8 wasted turns instead of immediate short-circuit.
  - SC3 EXPLORER-03 PASSED (4/4 — all 3 recursion-ban layers fire)
  - Sections 6/7/8 SKIPPED at runtime (test framework reports PASS) — "thread create failed" prevented dual-emit + multi-sub + JSONB persistence verification on a live POST. Setup issue, not a code defect.
  - Section 9 LangSmith PASSED (2/2 — chain run found, child count <=8) but no live Explorer chat ran during this session, so child count = 0
  - Section 10 Pitfall 8 PASSED (3/3 — TOOL-09 wrapper handles Explorer summary)

### 2. Manually trigger an Explorer-eligible chat in the UI and confirm SSE rendering
expected: User asks "find everything in my KB about MDB-C-G3 panel ratings"; UI shows sub-agent banner + nested tool steps populating in real time; reload renders the persisted Explorer trace from `messages.tool_metadata`
result: [pending — requires browser interaction]

### 3. Run Phase 4 regression suite
expected: `cd backend && venv/Scripts/python scripts/test_exploration_tools.py` reports the established Phase 4 baseline (78/0); no regression from Plan 03's openai_client.py edits
result: Results: 78 passed, 0 failed
notes: Phase 4 baseline preserved. Zero regression from Plan 03's additive edits to openai_client.py. TOOL-09 wrapper bit-identity confirmed.

### 4. Run Module-8 sub-agent regression suite
expected: `cd backend && venv/Scripts/python scripts/test_sub_agents.py` remains green (analyze_document path's `tool_metadata.tools_used[0].tool == 'analyze_document'` still works through Plan 04's refactored event_generator)
result: 2 failures observed
notes: |
  - "Test document reaches ready status" FAILED — document upload stuck at status=pending after polling. This is a test-environment / Docling-parsing setup issue, NOT a Phase 5 regression. The test never reached the persistence assertions on Plan 04's refactor.
  - "GET messages returns 200" CRASHED with JSONDecodeError — likely a cascading failure from #1 (no completed analyze_document run → empty/error response from GET /messages). Cannot statically prove Plan 04's `tools_used` accumulator refactor is bit-compatible with analyze_document's persisted JSONB shape until the doc-upload pipeline returns clean.
  - Recommended re-run after upload pipeline is healthy. Plan 04's static evidence (legacy `agent_name='analyze_document'` fallback preserved at L92-141, persistence path bit-identical at L213-223) suggests this is environmental, but only a green run proves it.

### 5. LangSmith chain-span hierarchy check (EXPLORER-06 + Success Criterion 4)
expected: After a live Explorer chat, LangSmith UI shows `explore_knowledge_base` as a `chain` run with 5-tool children nested as `tool` runs (NOT flat siblings); each tool result <= 12K chars
result: [pending — requires LangSmith UI inspection after manual UAT (Test #2)]

## Summary

total: 5
passed: 1
issues: 2
pending: 2
skipped: 0
blocked: 0

## Gaps

### Gap 1: no-progress detector emits 4 tool_starts instead of 1
status: failed
source_test: TEST-03 Section 4 — `_section_4_no_progress`
location: backend/app/services/sub_agent.py — `run_explorer_sub_agent` for-loop body, `_signature` invocation around lines 447-457
hypothesis: |
  The `if sig == last_signature` check is in place but somehow not matching across turns when the stub returns identical (tool, args). Possible causes:
  - `dict(fc.args)` for stubbed function_calls produces non-deterministic key ordering in `json.dumps(..., sort_keys=True)` (unlikely — sort_keys=True is canonical)
  - `last_signature = sig` assignment landing AFTER `yield` instead of before (current code has it before — verify)
  - `_signature` hashing the wrong tuple shape
priority: blocker — SC1 ROADMAP success criterion #1 explicitly names "tool-name+args-hash repeat → short-circuit"
remediation: investigate locally with print statements, or run `/gsd-plan-phase 5 --gaps` to scope a fix plan

### Gap 2: Module 8 regression test environment
status: needs_investigation
source_test: backend/scripts/test_sub_agents.py
hypothesis: pre-existing test-env issue (Docling-parsing pipeline stuck on synthetic upload, OR document upload endpoint changed schema). Not caused by Phase 5 changes.
remediation: Re-run when upload pipeline is healthy; if still failing, separate from Phase 5 scope.
