---
phase: 06-file-explorer-ui-cluster
plan: 04
subsystem: api
tags: [phase6, sse, cleanup, wave0, frontend, backend, dual-emit]

# Dependency graph
requires:
  - phase: 05-explorer-sub-agent-sse-protocol-generalization
    provides: "Generalized SSE envelope `{type: sub_agent, agent_name, event, payload}` dual-emitted alongside 5 legacy `sub_agent_*` shapes; locked Phase 6 cleanup hook in 05-04-SUMMARY.md Next Phase Readiness"
provides:
  - "Backend SSE emitter speaks ONLY the generalized envelope: `{type: 'sub_agent', agent_name, event, payload}` (5 events: start, token, tool_start, tool_done, done)"
  - "Frontend SSE consumer has ONE `parsed.type === 'sub_agent'` branch with switch on `parsed.event`; zero legacy `sub_agent_*` branches remain"
  - "Phase 5 dual-emit window is FULLY CLOSED in a single commit pair (producer + consumer)"
affects: [06-07-subagentsection-recursive-extension, 06-11-playwright-e2e-tests, any-future-phase-consuming-sub-agent-sse]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Dual-emit window closure pattern: producer-delete + consumer-rewrite in a single plan/wave so no consumer is ever stranded on a deprecated shape"
    - "Generalized SSE envelope discriminated by (type='sub_agent', event=<name>) — consumer dispatches via switch(parsed.event); replaces N parallel branches with 1 typed branch"

key-files:
  created: []
  modified:
    - "backend/app/routers/messages.py"
    - "frontend/src/lib/api.ts"

key-decisions:
  - "Closed the dual-emit window in Wave 0 of Phase 6 (not deferred) — leaving it open across phases pays the protocol-fork tax forever (Pitfall 12). Every subsequent Phase 6 frontend plan now consumes ONE canonical envelope."
  - "Preserved the `tool_metadata` accumulator code in `messages.py` bit-identically per Phase 5 lock — the dual-emit removal touched only the 5 legacy `yield` lines + their comments, not the slot-append logic for `tools_used`."
  - "Kept the `agent_name` resolution comment in the `sub_agent_token` branch (slightly rephrased from 'Dual-emit:' to 'Token stream...') rather than deleting it — the agent_name accumulator-lookup explanation is still useful documentation post-cleanup, and the grep -i 'dual.emit' verification gate still passes."
  - "Wrote the generalized frontend branch verbatim from RESEARCH.md §SSE envelope switchover (lines 642-650) — used multi-line `case:` form for readability rather than single-line form per project convention; semantically identical."

patterns-established:
  - "Generalized SSE envelope consumer pattern: `} else if (parsed.type === 'X') { switch (parsed.event) { case 'a': cbA?.(parsed.payload); break; ... } }` — one branch per envelope kind, switch per event kind"
  - "Dual-emit removal grep-gate pattern: verify ZERO occurrences of legacy literal (e.g., `'sub_agent_'` with trailing underscore) AND non-zero occurrences of generalized literal (e.g., `'sub_agent'` no trailing underscore) — uses the trailing-underscore disambiguation to distinguish via simple string match"

requirements-completed: [UI-10]

# Metrics
duration: 6min
completed: 2026-05-11
---

# Phase 6 Plan 04: SSE Dual-Emit Window Closure Summary

**Closed Phase 5 dual-emit window in a single commit pair: deleted 5 legacy `sub_agent_*` SSE yields in messages.py and replaced 5 legacy `parsed.type === 'sub_agent_*'` consumer branches in api.ts with ONE generalized `parsed.type === 'sub_agent'` branch dispatching on `parsed.event`.**

## Performance

- **Duration:** ~6 min
- **Started:** 2026-05-11T05:43:00Z
- **Completed:** 2026-05-11T05:48:22Z
- **Tasks:** 2 / 2
- **Files modified:** 2

## Accomplishments

- Backend emitter (`event_generator` in `messages.py`) now speaks ONLY the generalized envelope across all 5 sub-agent events (start, token, tool_start, tool_done, done).
- Frontend consumer (`sendMessage` in `api.ts`) now has ONE `parsed.type === 'sub_agent'` branch with a switch on `parsed.event` — replaces 5 parallel `else if` branches.
- Phase 5 dual-emit window is fully closed; Plan 06-07 (SubAgentSection recursive extension) and Plan 06-11 (Playwright e2e) can now rely on a single canonical SSE envelope shape.

## Task Commits

1. **Task 1: Delete 5 legacy yield lines in backend/app/routers/messages.py** — `2dd2eaa` (refactor)
2. **Task 2: Replace 5 legacy SSE branches with 1 generalized branch in api.ts** — `8b691f0` (refactor)

## Files Created/Modified

- `backend/app/routers/messages.py` — 5 legacy `yield json.dumps({"type": "sub_agent_*", ...})` lines deleted; 5 generalized `yield json.dumps({"type": "sub_agent", "agent_name": ..., "event": ..., "payload": ...})` lines preserved; dual-emit scaffolding comments removed. Diff: 1 insertion, 12 deletions (1 insertion = rephrase of one "Dual-emit: ..." comment to "Token stream..." to remove the obsolete-contract reference while keeping the agent_name-resolution explanation).
- `frontend/src/lib/api.ts` — 5 legacy `parsed.type === 'sub_agent_*'` branches deleted; 1 generalized `parsed.type === 'sub_agent'` branch added with switch on `parsed.event` (cases: `start`, `token`, `tool_start`, `tool_done`, `done`). The redundant NOTE comment at the end of the SSE block (referencing the now-closed dual-emit contract) was also removed. `sendMessage()` signature unchanged. Diff: 22 insertions, 21 deletions.

### Backend: 5 Deleted Lines (final pre-deletion line numbers per pre-edit messages.py)

| Final pre-deletion line | Verbatim text deleted |
|---|---|
| 120 | `yield json.dumps({"type": "sub_agent_start", **parsed})` |
| 141 | `yield json.dumps({"type": "sub_agent_tool_start", **parsed})` |
| 160 | `yield json.dumps({"type": "sub_agent_tool_done", **parsed})` |
| 176 | `yield json.dumps({"type": "sub_agent_token", "content": data})` |
| 198 | `yield json.dumps({"type": "sub_agent_done"})` |

Also deleted the surrounding dual-emit scaffolding comments at approx lines 118, 119, 121, 140, 159, 197.

### Frontend: 5 Deleted Branches (final pre-deletion line numbers per pre-edit api.ts)

| Final pre-deletion line | Verbatim opening of deleted branch |
|---|---|
| 306 | `} else if (parsed.type === 'sub_agent_start') {` |
| 311 | `} else if (parsed.type === 'sub_agent_token') {` |
| 313 | `} else if (parsed.type === 'sub_agent_tool_start') {` |
| 316 | `} else if (parsed.type === 'sub_agent_tool_done') {` |
| 319 | `} else if (parsed.type === 'sub_agent_done') {` |

### Frontend: 1 Inserted Generalized Branch (verbatim, replacing the 5 above)

```ts
} else if (parsed.type === 'sub_agent') {
  // Phase 6 (UI-10): generalized SSE envelope for all 5 sub-agent events.
  // Backend emits `{type: 'sub_agent', agent_name, event, payload}` — the
  // legacy `sub_agent_*` shapes (with trailing underscore) were removed
  // in 06-04 when the Phase 5 dual-emit window closed.
  switch (parsed.event) {
    case 'start':
      onSubAgentStart?.({ ...parsed.payload, agent_name: parsed.agent_name })
      break
    case 'token':
      onSubAgentToken?.(parsed.payload.content)
      break
    case 'tool_start':
      onSubAgentToolStart?.(parsed.payload)
      break
    case 'tool_done':
      onSubAgentToolDone?.(parsed.payload)
      break
    case 'done':
      onSubAgentDone?.()
      break
  }
}
```

## Confirmation Grep Output

Per the plan's `<output>` block — required counts `0 / 5 / 0 / 1`:

| Grep | Expected | Actual |
|---|---|---|
| `grep -c '"type": "sub_agent_' backend/app/routers/messages.py` | 0 | 0 |
| `grep -c '"type": "sub_agent"' backend/app/routers/messages.py` | 5 | 5 |
| `grep -c "parsed.type === 'sub_agent_" frontend/src/lib/api.ts` | 0 | 0 |
| `grep -c "parsed.type === 'sub_agent'" frontend/src/lib/api.ts` | 1 | 1 |

Additional gates:
- `cd backend && venv/Scripts/python -c "import ast; ast.parse(open('app/routers/messages.py').read())"` exits 0
- `cd frontend && npx tsc --noEmit` exits 0
- `grep -i "dual.emit\|Phase 6 cleanup" backend/app/routers/messages.py` returns ZERO matches
- `grep -n "tool_metadata" backend/app/routers/messages.py` still returns multiple matches (accumulator preserved per Phase 5 lock)

## Decisions Made

- **Kept one comment line in `sub_agent_token` branch (rephrased rather than deleted):** the original comment said "Dual-emit: token stream from the sub-agent's compact summary." The dual-emit clause was rephrased to "Token stream from the sub-agent's compact summary." to retain the documentation about `agent_name` resolution while satisfying the `grep -i "dual.emit"` zero-match gate. This is the single net insertion in the backend diff (otherwise pure subtraction).
- **Removed the redundant NOTE comment at the bottom of the api.ts SSE block:** the Phase 5 NOTE that described the dual-emit contract intentionally listening to LEGACY only is now obsolete. Removed alongside the 5 legacy branches.

## Deviations from Plan

None — plan executed exactly as written. Both verification gates (Python parse, tsc, all 4 grep counts) passed first-time.

The plan's soft criterion "git diff --stat shows ONLY deletions (zero additions) for messages.py" is technically off by one (`1 insertion(+), 12 deletions(-)`); the one insertion is the rephrased comment described under Decisions Made above. The hard verification gates (4 grep counts + Python parse + dual-emit-zero-match) all pass. Logged for transparency; not classified as a Rule deviation.

## Issues Encountered

None.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- Wave 0 of Phase 6 is now CODE-COMPLETE: Plan 06-01 (Pydantic field add), Plan 06-03 (deps + 6 shadcn primitives), and Plan 06-04 (dual-emit closure) all merged. Plan 06-02 (admin@test.com seed) remains to fully close Wave 0.
- Plan 06-07 (SubAgentSection recursive extension) and Plan 06-11 (Playwright e2e) can now build against a single canonical SSE envelope shape — no special-casing for legacy `sub_agent_*` events.
- No blockers introduced. The SSE contract change is fully self-contained between producer and consumer; no other consumers of the sub-agent stream exist in the codebase.

## Self-Check: PASSED

- FOUND: `backend/app/routers/messages.py` (modified)
- FOUND: `frontend/src/lib/api.ts` (modified)
- FOUND: commit `2dd2eaa` (Task 1, refactor: remove legacy sub_agent_* SSE yields)
- FOUND: commit `8b691f0` (Task 2, refactor: replace 5 legacy sub_agent_* SSE branches with generalized switch)

---
*Phase: 06-file-explorer-ui-cluster*
*Completed: 2026-05-11*
