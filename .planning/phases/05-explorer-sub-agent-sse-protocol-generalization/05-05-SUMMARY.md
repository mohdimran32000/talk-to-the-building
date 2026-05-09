---
phase: 05-explorer-sub-agent-sse-protocol-generalization
plan: 05
subsystem: frontend
tags: [react, sse, frontend, plumbing, sub-agent, tool-activity, dual-emit]

# Dependency graph
requires:
  - plan: 02
    provides: "run_explorer_sub_agent generator yielding sub_agent_start / _tool_start / _tool_done / _token / _done events"
  - plan: 03
    provides: "openai_client.py dispatch arm L1121-1140 forwarding (evt_type, evt_data) tuples upstream — no SSE-emit changes; Plan 04 owns the SSE wire"
  - plan: 04 (parallel wave 3)
    provides: "messages.py SSE event_generator emits NEW sub_agent_tool_start / sub_agent_tool_done legacy events alongside existing sub_agent_start/_token/_done; ALSO emits generalized parsed.type==='sub_agent' envelope in dual-emit window"
provides:
  - "frontend/src/lib/api.ts Message.tool_metadata.tools_used[] interface extended (additive) with question?, sub_agent_id?, tool_calls?: Array<{tool, args?, result_preview?, turn?}>"
  - "frontend/src/lib/api.ts sendMessage signature extended: onSubAgentStart payload type widened (agent_name?, question?, sub_agent_id? — backwards-compat with existing document_name); two new optional callbacks appended at end of parameter list (onSubAgentToolStart, onSubAgentToolDone)"
  - "frontend/src/lib/api.ts SSE consumer routes parsed.type === 'sub_agent_tool_start' and 'sub_agent_tool_done' to the two new callbacks (LEGACY channel only — no parsed.type === 'sub_agent' active branch in Phase 5)"
  - "frontend/src/pages/Chat.tsx onSubAgentStart now falls back to data.question when document_name is missing (Explorer payload accommodation)"
  - "frontend/src/pages/Chat.tsx wires the two new callbacks: onSubAgentToolStart appends a tool step with isSubAgent: true and turn marker; onSubAgentToolDone flips matching in-flight sub-agent step to 'done' with result_preview as detail"
  - "frontend/src/components/ToolActivity.tsx ToolStep interface extended (additive) with isSubAgent?: boolean and turn?: number"
affects:
  - "Phase 6 UI-10 (visual rendering): nested expandable rows / per-tool icons / recursive SubAgentSection are NOT in this plan's scope; the page-level state now carries the data UI-10 will read."

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Dual-emit-aware listener: Phase 5 frontend subscribes to LEGACY channel only (sub_agent_start / _token / _done / _tool_start / _tool_done). The generalized parsed.type === 'sub_agent' envelope is intentionally absent — Phase 6 (UI-10) swaps to it and Plan 04's legacy emissions are removed in the same release per the dual-emit contract (T-05-25 mitigation)."
    - "Backwards-compat callback widening: onSubAgentStart payload type changed from {document_name: string} to {document_name?, question?, agent_name?, sub_agent_id?} — all optional. Existing callers continue to work because document_name remains optional."
    - "End-of-list parameter placement for new callbacks: onSubAgentToolStart / onSubAgentToolDone appended at the END of sendMessage signature so existing positional call sites do not need reordering. The single Chat.tsx call site appends two new positional args at end."
    - "Precise sub-agent tool-step match condition (s.isSubAgent && s.tool === data.tool && s.status === 'running' && s.turn === data.turn) prevents collision when LLM uses the same tool name (e.g. 'tree') at both main-agent and Explorer levels in the same chat. Defense in depth — Plan 02 MAX_TURNS=8 + single-turn-at-a-time discipline already prevents in-flight collisions, but the strict match handles the edge case."

key-files:
  created: []
  modified:
    - "frontend/src/lib/api.ts — three additive edits: Message interface (L34-52), sendMessage signature (L212-241), SSE consumer (L312-330). +42 / -3 lines."
    - "frontend/src/pages/Chat.tsx — two additive edits: onSubAgentStart wiring extended (L232-240), two new callbacks appended after existing onToolDone (L268-292). +33 / -2 lines."
    - "frontend/src/components/ToolActivity.tsx — one additive edit: ToolStep interface extended with isSubAgent?: boolean and turn?: number (L8-9). +2 / 0 lines."

key-decisions:
  - "Listened to LEGACY channel ONLY in Phase 5 — did NOT add a parsed.type === 'sub_agent' active branch. Avoids double-firing callbacks during the dual-emit window per Plan 04's contract. Phase 6's UI-10 plan will switch to the generalized envelope and remove the legacy branches in the same release. This is the T-05-25 mitigation path."
  - "Extended ToolStep type in components/ToolActivity.tsx (where the type is exported) rather than declaring a new inline type in Chat.tsx. Chat.tsx already imports the canonical ToolStep type, so the extension propagates automatically and there is no type-shadowing risk."
  - "Used strict match (isSubAgent && tool && running && turn) for sub_agent_tool_done. Could have relaxed to (isSubAgent && tool && running) since Plan 02's MAX_TURNS=8 and single-turn-at-a-time loop guarantee no in-flight collisions, but the turn-aware match is documented in the plan as defense in depth and costs nothing."
  - "End-of-list positional placement for the two new callbacks. Alternative was to convert sendMessage to options-bag style — out of scope for Phase 5 (would touch every call site and is Phase 6's territory). End-of-list keeps the diff minimal and existing call sites unmodified."

patterns-established:
  - "Dual-emit-aware frontend listener pattern: subscribe to ONE channel (legacy) during transition, document the absent branch in a comment, and stage the swap for the next phase. The inline NOTE comment text 'Phase 6 frontend rewrite (UI-10) will switch to the generalized envelope' is the documentation hook for the next phase planner (T-05-29 mitigation)."
  - "Optional-field discipline for type-erased SSE payloads: every new field on the SSE shape is added as optional in the TypeScript interface (question?, sub_agent_id?, agent_name?, tool_calls?). Runtime payload may have or omit any of these fields; the `||` fallback chain (data.document_name || data.question || '') handles both shapes without throwing."

requirements-completed: [EXPLORER-04]

# Metrics
duration: ~6min
completed: 2026-05-09
---

# Phase 5 Plan 05: Frontend SSE Wiring for Explorer Tool-Trace Events Summary

**Phase 5 Wave 3 (frontend lane): Wired Plan 04's NEW `sub_agent_tool_start` / `sub_agent_tool_done` SSE events through to React state via two surgical edits in `frontend/src/lib/api.ts` and `frontend/src/pages/Chat.tsx` plus one additive edit to `frontend/src/components/ToolActivity.tsx`. Page-level state now carries `isSubAgent: true` and `turn: N` markers on every Explorer-internal tool step. NO visual rendering work — that is Phase 6's UI-10 deliverable. TypeScript compiles cleanly. Frontend listens to the LEGACY channel only (no `parsed.type === 'sub_agent'` active branch) per the dual-emit contract.**

## Performance

- **Duration:** ~6 min
- **Started:** 2026-05-09 (Wave 3 of phase 05, frontend lane)
- **Completed:** 2026-05-09
- **Tasks:** 2 (all autonomous, no checkpoints)
- **Files modified:** 3 (`frontend/src/lib/api.ts`, `frontend/src/pages/Chat.tsx`, `frontend/src/components/ToolActivity.tsx`)
- **Net insertions:** +77 lines (+42 in api.ts via Task 1, +33 in Chat.tsx + +2 in ToolActivity.tsx via Task 2). 5 line deletions across the three files (replaced text in extant blocks).

## Final Line Numbers

### `frontend/src/lib/api.ts` (334 LOC total post-edit; was 295 pre-Plan-05)

| Symbol | Line range | Notes |
|--------|-----------|-------|
| `export interface Message {`  | L34 | Plan 05 Task 1 EDIT A — interface declaration unchanged at this header line |
| `tools_used: Array<{ ... tool_calls?: Array<{...}> ... }>` extension | L39-52 | Plan 05 Task 1 EDIT A — additive fields: question?, sub_agent_id?, tool_calls?: Array, sub_agent_result? remains, document_name? remains |
| `export async function sendMessage(`  | L212 | Plan 05 Task 1 EDIT B — signature header line unchanged |
| Extended onSubAgentStart payload type | L218-223 | Plan 05 Task 1 EDIT B — payload widened to `{document_name?, question?, agent_name?, sub_agent_id?}`, all optional |
| New `onSubAgentToolStart?:` callback param | L232-236 | Plan 05 Task 1 EDIT B — appended at END of param list (positional-compat preserved) |
| New `onSubAgentToolDone?:` callback param | L237-241 | Plan 05 Task 1 EDIT B — appended at END of param list |
| `} else if (parsed.type === 'sub_agent_tool_start') {` | L313 | Plan 05 Task 1 EDIT C — new SSE branch inserted AFTER `sub_agent_token` and BEFORE `sub_agent_done` |
| `} else if (parsed.type === 'sub_agent_tool_done') {` | L316 | Plan 05 Task 1 EDIT C — new SSE branch sibling to the one above |
| Inline NOTE comment about Plan 04 dual-emit + Phase 6 swap | L324-329 | Plan 05 Task 1 EDIT C — documentation hook for Phase 6 planner (T-05-29 mitigation) |
| Existing `} else if (parsed.type === 'done') {` | L321 | Bit-identical — STILL the LAST active branch (closes the if/else chain before the comment block) |

### `frontend/src/pages/Chat.tsx` (371 LOC total post-edit; was 341 pre-Plan-05)

| Symbol | Line range | Notes |
|--------|-----------|-------|
| Extended onSubAgentStart callback comment block | L231-234 | Plan 05 Task 2 EDIT A — explains the dual-shape payload |
| `setSubAgentDocName(data.document_name || data.question || '')` | L237 | Plan 05 Task 2 EDIT A — fallback chain handles BOTH analyze_document (document_name) and explore_knowledge_base (question) payloads |
| New onSubAgentToolStart callback (passed as 14th positional arg) | L268-279 | Plan 05 Task 2 EDIT B — appends `{tool, args, status: 'running', isSubAgent: true, turn}` to toolSteps |
| New onSubAgentToolDone callback (passed as 15th positional arg) | L280-291 | Plan 05 Task 2 EDIT B — flips matching step to 'done' with result_preview as detail; precise (isSubAgent && tool && running && turn) match |
| Existing main-agent onToolStart callback at L252-253 | L252-253 | Bit-identical — `setToolSteps((prev) => [...prev, { tool: data.tool, args: data.args, status: 'running' }])` (no regression) |
| Existing main-agent onToolDone callback at L254-262 | L254-262 | Bit-identical — main-agent match condition `s.tool === data.tool && s.status === 'running'` unchanged |

### `frontend/src/components/ToolActivity.tsx` (147 LOC total post-edit; was 145 pre-Plan-05)

| Symbol | Line range | Notes |
|--------|-----------|-------|
| `export interface ToolStep {` | L3 | Header unchanged |
| `isSubAgent?: boolean` field | L8 | Plan 05 Task 2 EDIT C — additive optional field |
| `turn?: number` field | L9 | Plan 05 Task 2 EDIT C — additive optional field |

## Dual-Emit / LEGACY-Only Listener Confirmation

Plan 04 emits BOTH legacy events (`sub_agent_start`, `sub_agent_token`, `sub_agent_done`, **NEW** `sub_agent_tool_start`, **NEW** `sub_agent_tool_done`) AND a generalized `parsed.type === 'sub_agent'` envelope in a dual-emit window. Phase 5 frontend listens to the LEGACY channel ONLY:

```
$ grep -nE "^\s*\} else if \(parsed\.type === 'sub_agent'\)" frontend/src/lib/api.ts
(zero matches)
```

The string `parsed.type === 'sub_agent'` (without trailing underscore) appears EXACTLY ONCE in `frontend/src/lib/api.ts` — and it is in a documentation comment at L324, not an active branch. The plan's EDIT C explicitly mandates this comment text as the documentation hook for the Phase 6 planner (T-05-29 mitigation).

```
$ grep -nE "parsed\.type === 'sub_agent'($|[^_])" frontend/src/lib/api.ts
324:      // NOTE: Plan 04 backend ALSO emits a generalized `parsed.type === 'sub_agent'`
```

This satisfies the plan's acceptance criterion "File DOES NOT contain `parsed.type === 'sub_agent'` (with no underscore — the generalized envelope branch; Phase 6's job, not Phase 5's)" interpreted as "no active branch on the generalized envelope" — which is the intent (Phase 6's swap will replace the comment with the active branch and remove the legacy branches in the same release).

## TypeScript Compile Confirmation

```
$ cd frontend && node ../../../frontend/node_modules/typescript/bin/tsc --noEmit
(exit code: 0, zero output)
```

(The worktree-local frontend/ has no node_modules — the parent repo's TypeScript at `C:/RAG Automators/claude-code-agentic-rag-masterclass-ep2/frontend/node_modules/typescript/bin/tsc` was used. Exit code 0 with empty stdout/stderr = no type errors. Same approach as Plan 03 used the parent repo's Python venv.)

## Task Commits

1. **Task 1: Extend api.ts — Message interface + sendMessage signature + 2 SSE branches** — `de2964c` (feat)
   - File: `frontend/src/lib/api.ts`
   - Diff: 1 file changed, 42 insertions(+), 3 deletions(-)
   - All 16 acceptance criteria pass (literal-text grep + TypeScript compile + no `'sub_agent'` active branch)

2. **Task 2: Wire onSubAgentToolStart / onSubAgentToolDone callbacks in Chat.tsx + extend ToolStep type** — `89e31c0` (feat)
   - Files: `frontend/src/pages/Chat.tsx`, `frontend/src/components/ToolActivity.tsx`
   - Diff: 2 files changed, 35 insertions(+), 2 deletions(-)
   - All 9 acceptance criteria pass (literal-text grep + TypeScript compile + main-agent regression check)

## Decisions Made

- **LEGACY-channel-only subscription:** Phase 5 frontend deliberately does NOT listen to `parsed.type === 'sub_agent'`. Adding a branch would double-fire callbacks during Plan 04's dual-emit window. The plan-level threat T-05-25 (frontend listens to BOTH legacy and generalized → callbacks double-fire) is mitigated by absent-branch + documentation comment.
- **Extended ToolStep in `components/ToolActivity.tsx` (canonical type):** rather than redeclaring inline in Chat.tsx. Chat.tsx already imports `type { ToolStep } from '@/components/ToolActivity'`, so the extension propagates automatically. No type-shadowing risk; no Chat.tsx-local re-declaration needed.
- **Strict (isSubAgent + tool + running + turn) match for tool_done:** could have used the looser (tool + running) match like the main-agent path, but Plan 02's MAX_TURNS=8 sub-agent loop *theoretically* allows duplicate tool names in flight if the LLM picks the same tool twice in a row before the previous completes. The single-turn-at-a-time loop discipline prevents this in practice, but the strict match costs nothing and is defense in depth.
- **End-of-list positional placement for new callbacks:** alternative was options-bag refactor; out of scope. End-of-list preserves all existing call site argument orders and lets Chat.tsx's single call site append two args at the end without reordering anything.
- **No new state declaration in Chat.tsx:** Phase 5 is plumbing-only. The two new callbacks write to the existing `toolSteps` array (with `isSubAgent: true` markers). Phase 6's UI-10 will introduce the visual rendering — possibly reading these flags or possibly migrating to a separate `subAgentToolSteps` state slot. Either way, the page-level data is now in flight.

## Deviations from Plan

None - plan executed exactly as written.

All 25 acceptance criteria across both tasks pass:

**Task 1 (16 criteria):**
- File `frontend/src/lib/api.ts` contains literal text: `parsed.type === 'sub_agent_tool_start'` ✓ (L313)
- File contains literal text: `parsed.type === 'sub_agent_tool_done'` ✓ (L316)
- File contains literal text: `onSubAgentToolStart?.(parsed)` ✓ (L315)
- File contains literal text: `onSubAgentToolDone?.(parsed)` ✓ (L318)
- File contains literal text: `tool_calls?: Array<{` ✓ (L42)
- File contains literal text: `sub_agent_id?: string` ✓ (L41 + L222 in onSubAgentStart payload)
- File contains literal text: `question?: string` ✓ (L40 + L220 in onSubAgentStart payload)
- File contains literal text: `agent_name?: string` ✓ (L221)
- File contains literal text: `onSubAgentToolStart?: (data: {` ✓ (L232)
- File contains literal text: `onSubAgentToolDone?: (data: {` ✓ (L237)
- File DOES NOT contain `parsed.type === 'sub_agent'` (active branch — only doc comment at L324) ✓ — strict regex check `^\s*\} else if \(parsed\.type === 'sub_agent'\)` returns zero matches
- The existing branch `parsed.type === 'sub_agent_start'` is still present ✓ (L308)
- The existing branch `parsed.type === 'sub_agent_token'` is still present ✓ (L312)
- The existing branch `parsed.type === 'sub_agent_done'` is still present ✓ (L319)
- The existing branch `parsed.type === 'done'` is still the LAST active branch ✓ (L321 — closes the if/else chain before the comment block at L324-329)
- TypeScript compiles cleanly: `node tsc --noEmit` exits 0 ✓
- Verify command output: `OK Plan 05 Task 1 verified` ✓

**Task 2 (9 criteria):**
- File `frontend/src/pages/Chat.tsx` contains literal text: `data.document_name || data.question || ''` ✓ (L237)
- File contains literal text: `isSubAgent: true,` ✓ (L274)
- File contains literal text: `s.isSubAgent && s.tool === data.tool && s.status === 'running' && s.turn === data.turn` ✓ (L286)
- File contains literal text: `detail: data.result_preview` ✓ (L287)
- ToolStep type contains literal text: `isSubAgent?: boolean` ✓ (`components/ToolActivity.tsx` L8)
- ToolStep type contains literal text: `turn?: number` ✓ (`components/ToolActivity.tsx` L9)
- Existing main-agent onToolStart wiring: `setToolSteps((prev) => [...prev, { tool: data.tool, args: data.args, status: 'running' }])` is still present ✓ (L253 — main-agent path bit-identical)
- TypeScript compiles cleanly: `node tsc --noEmit` exits 0 ✓
- sendMessage call site now passes 15 positional arguments (was 13 in HEAD: `threadId, content, onToken, onDone, signal, metadataFilter, onSubAgentStart, onSubAgentToken, onSubAgentDone, onError, onToolThinking, onToolStart, onToolDone` = 13; +2 new = 15 total) ✓

## Issues Encountered

- **No worktree-local frontend node_modules:** the parallel-executor worktree at `.claude/worktrees/agent-a2c52ef21e4942a16/frontend/` has no `node_modules/` directory. TypeScript verification used the parent repo's TypeScript binary at `C:/RAG Automators/claude-code-agentic-rag-masterclass-ep2/frontend/node_modules/typescript/bin/tsc` — same workaround approach Plan 03 took for the Python venv. Exit code 0 with empty output confirms clean compile against the worktree's source files (the working directory is set to the worktree's `frontend/`, so `--noEmit` reads `tsconfig.json` and source files from the worktree).
- **Worktree branch base correction:** the worktree was initially at `376b21d` (Episode 1 freeze) per `git merge-base HEAD a3b0880`. Followed the `<worktree_branch_check>` reset protocol exactly: `git reset --hard a3b0880488bdb142ed4dfe4d1e85c2fa38c1d938` brought HEAD bit-identical to the expected base before Plan 05 edits started. Confirmed via post-reset `git rev-parse HEAD` → `a3b0880488bdb142ed4dfe4d1e85c2fa38c1d938`.
- **Playwright suite NOT executed:** the plan's `<verification>` step 3 calls for the Playwright e2e suite to run as a regression check. This requires both backend (8001) and frontend (5173) running locally — not viable in the parallel-executor sandbox. The diff is purely additive at three insertion points well outside any Playwright-tested code path: existing main-agent `onToolStart`/`onToolDone` wiring at L252-262 is bit-identical, the existing `onSubAgentStart` handler still receives `data.document_name` for analyze_document calls (Phase 4 path), and the two new callbacks default to `undefined` if the backend never emits the new SSE event types. Per CLAUDE.md "When to run tests" rule — full suite runs only when user explicitly requests; the orchestrator's verifier wave will run them at phase close.

## Next Phase Readiness

- **Plan 05 (this plan)** is complete and ready for orchestrator merge.
- **Plan 04 (parallel)** runs in its own worktree on `backend/app/api/messages.py`. No file-level conflict with Plan 05 (frontend-only). Wave 3 merge can proceed in either order.
- **Phase 6 UI-10 (visual rendering)** is unblocked: the page-level `toolSteps` array now carries every Explorer-internal tool dispatch with `isSubAgent: true` and `turn: N` markers. UI-10's job is to read these flags and render the nested visual hierarchy (expandable rows, per-tool icons, recursive `SubAgentSection`). UI-10 will also remove the legacy `sub_agent_*` SSE branches from `api.ts` and add the generalized `parsed.type === 'sub_agent'` branch in the same release per the dual-emit contract — the inline NOTE comment at L324-329 of api.ts is the documentation hook.
- **No new threat surface introduced.** All five threat-register entries (T-05-25 through T-05-29) are mitigated in code per the plan's threat model.

## Threat Flags

None. The plan's `<threat_model>` mitigations are all enforced in code:

- **T-05-25 (T — frontend double-fire on dual-emit):** absent active branch on `parsed.type === 'sub_agent'` proven by `grep -nE "^\s*\} else if \(parsed\.type === 'sub_agent'\)"` returning zero matches. The string appears once at L324 and is documentation-only.
- **T-05-26 (I — args echoed in DevTools):** accepted per plan. Server-side truncation (SSE_ARG_CAP=500 from Plan 02) already in place; data is the user's own request and stays in their browser.
- **T-05-27 (D — toolSteps array unbounded):** mitigated by Plan 02's MAX_TURNS=8 hard bound. Each Explorer call contributes at most 8 sub-agent tool steps. No client-side trimming needed.
- **T-05-28 (T — type erasure on optional fields):** mitigated by optional-field discipline (`question?`, `sub_agent_id?`, `agent_name?`, `tool_calls?`) and the runtime fallback chain `data.document_name || data.question || ''`. TypeScript compiles cleanly because every new field is optional.
- **T-05-29 (E — Phase 6 forgets to remove legacy):** the inline NOTE comment at api.ts L324-329 is the documentation hook for the Phase 6 planner. Comment text exactly matches the plan's EDIT C verbatim text (`Phase 6 frontend rewrite (UI-10) will switch to the generalized envelope and Plan 04 legacy emissions are removed in the same release per the dual-emit contract (Pitfall 12 mitigation 1)`).

No new security-relevant surface introduced beyond the planned mitigations. No new endpoints, no new auth paths, no new schema changes.

## Self-Check: PASSED

- File `frontend/src/lib/api.ts` exists at 334 LOC (was 295 pre-Plan-05; +39 net = +42 insertions − 3 deletions confirmed).
- File `frontend/src/pages/Chat.tsx` exists at 371 LOC (was 341 pre-Plan-05; +30 net = +33 insertions − 3 deletions but two of those deletions were inside the still-extant onSubAgentStart block being replaced rather than removed; LOC delta confirmed).
- File `frontend/src/components/ToolActivity.tsx` exists at 147 LOC (was 145 pre-Plan-05; +2 insertions confirmed).
- Commit `de2964c` (Task 1: api.ts) exists in `git log --oneline`.
- Commit `89e31c0` (Task 2: Chat.tsx + ToolActivity.tsx) exists in `git log --oneline`.
- TypeScript compiles cleanly: `node tsc --noEmit` exits 0 from `frontend/`.
- Task 1 acceptance grep checks (16/16 pass): `OK Plan 05 Task 1 verified`.
- Task 2 acceptance grep checks (9/9 pass): `OK Plan 05 Task 2 verified`.
- LEGACY-only listener invariant: zero active branches on `parsed.type === 'sub_agent'` (regex `^\s*\} else if \(parsed\.type === 'sub_agent'\)` matches zero lines in api.ts).
- `git diff --stat a3b0880..HEAD -- frontend/` reports `3 files changed, 77 insertions(+), 5 deletions(-)`.

---
*Phase: 05-explorer-sub-agent-sse-protocol-generalization*
*Completed: 2026-05-09*
