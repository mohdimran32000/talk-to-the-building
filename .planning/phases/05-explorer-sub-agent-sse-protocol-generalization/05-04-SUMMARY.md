---
phase: 05-explorer-sub-agent-sse-protocol-generalization
plan: 04
subsystem: api
tags: [sse, sub-agent, dual-emit, generalized-envelope, tool-metadata, jsonb, recursive-accumulator, pitfall-12]

# Dependency graph
requires:
  - plan: 02
    provides: "run_explorer_sub_agent generator yielding 5 event types: sub_agent_start / sub_agent_tool_start / sub_agent_tool_done / sub_agent_token / sub_agent_done; SSE_ARG_CAP=500 for arg truncation"
  - plan: 03
    provides: "openai_client.stream_response dispatch arm at L1121-1140 forwards every (evt_type, evt_data) tuple from run_explorer_sub_agent unchanged — messages.py event_generator now sees the 5-event vocabulary"
provides:
  - "messages.py event_generator dual-emits BOTH legacy and generalized envelopes for all 5 sub-agent events. Generalized envelope contract: {type: 'sub_agent', agent_name, event, payload} — uniform across start/token/tool_start/tool_done/done. agent_name resolved from tool_metadata['tools_used'][-1]['tool'] for token/done arms (server-side resolution)"
  - "messages.py tool_metadata accumulator: tools_used is an ARRAY of slots (was [0]-fixed). Each slot includes {tool, sub_agent_id, tool_calls: [...], document_name|question, sub_agent_result}. Multi-sub-agent-per-message supported via append + [-1] last-slot updates"
  - "Phase 5 dual-emit window: legacy yields kept for ONE release for frontend back-compat. Phase 6 plan-checker enforces removal of legacy emissions when frontend switches to generalized envelope"
affects: [05-05, 05-06]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Recursive tool_metadata accumulator: tools_used as list, slot append on sub_agent_start, [-1] last-slot updates on tool_start/tool_done/token/done — supports multiple sub-agents per assistant turn"
    - "Dual-emit SSE protocol generalization: legacy event shape kept ONE release alongside generalized envelope to avoid mid-flight frontend break (Pitfall 12: generalize NOW, not later)"
    - "Generalized envelope uniformity: every {type:'sub_agent', ...} yield carries agent_name as the immediately-following key — Phase 6 frontend routes by agent_name to the correct sub-agent slot"
    - "Server-side agent_name resolution for token/done arms: payload doesn't carry agent_name (token is raw text, done is summary text), so resolve from tool_metadata['tools_used'][-1]['tool'] — the most recently active sub-agent slot"
    - "Defensive accessors at multi-sub-agent boundary: tool_metadata.get('tools_used') + truthiness check before tools_used[-1] — guards V7 generator-never-raises invariant"
    - "300-char cap discipline (V8): result_preview[:300] on inner tool calls + data[:300] on sub_agent_result — bounded JSONB exposure of doc content"

key-files:
  created:
    - ".planning/phases/05-explorer-sub-agent-sse-protocol-generalization/05-04-SUMMARY.md"
  modified:
    - "backend/app/routers/messages.py — extended from 125 LOC to 228 LOC (+107 / -4 net): import uuid added, sub_agent_start refactored to append slot + dual-emit, sub_agent_tool_start NEW + dual-emit, sub_agent_tool_done NEW + dual-emit, sub_agent_token refactored to dual-emit with server-side agent_name, sub_agent_done refactored to write to tools_used[-1] + dual-emit. Persistence INSERT at L222 bit-identical."

key-decisions:
  - "Append slot per sub_agent_start (NOT overwrite [0]) — supports multi-sub-agent-per-message (e.g. analyze_document + explore_knowledge_base in one assistant turn). Backwards-compatible with single-sub-agent existing chats: tools_used[-1] resolves to tools_used[0] when there's only one slot."
  - "Legacy fallback agent_name='analyze_document' when no agent_name in payload — preserves existing analyze_document path's persisted tool_metadata.tools_used[0].tool == 'analyze_document' contract that the existing frontend expects."
  - "Generalized envelope key order: type, agent_name, event, payload — chosen for grep-friendly verification (Pitfall 12 acceptance criterion regex r'\"type\":\\s*\"sub_agent\",\\s*\"agent_name\":...' matches all 5 arms)."
  - "tool_calls: [] initialized on EVERY slot (including analyze_document slots) — additive change, doesn't break existing frontend reads. analyze_document slots persist with empty tool_calls array (the analyze_document sub-agent never emits sub_agent_tool_*)."
  - "Server-side agent_name resolution for token/done arms (not embedding agent_name in the wire payload from sub_agent.py) — keeps run_sub_agent's token/done events bit-identical (zero edits to Module 8 sub_agent.py legacy paths)."

patterns-established:
  - "Dual-emit SSE window: when a wire format changes mid-flight, emit BOTH old and new shapes for ONE release window, then plan-checker the legacy removal in the next phase. Established here for sub-agent events; applicable to any future SSE event-shape evolution."
  - "Recursive tool_metadata accumulator: top-level dict + tools_used array of slots + [-1] last-slot updates is the canonical pattern for accumulating multi-event-type sub-agent activity into a single JSONB row."
  - "Generalized envelope uniformity invariant: every member of an event-class shares the same outer envelope shape ({type, agent_name, event, payload}); inner payload is event-type-specific. Verifiable via regex grep on the source."

requirements-completed: [EXPLORER-04, EXPLORER-05]

# Metrics
duration: 6min
completed: 2026-05-09
---

# Phase 5 Plan 04: SSE Protocol Generalization Summary

**Generalized the SSE sub-agent event protocol in `backend/app/routers/messages.py` to dual-emit BOTH the legacy `{type:'sub_agent_*'}` shape AND the generalized `{type:'sub_agent', agent_name, event, payload}` envelope across all 5 sub-agent events. Refactored `tool_metadata['tools_used']` from a [0]-fixed slot into a recursive list of slots supporting multi-sub-agent-per-message. Added two NEW arms (`sub_agent_tool_start`, `sub_agent_tool_done`) accumulating Explorer's nested tool calls with 300-char result_preview caps. Persistence INSERT path at L222 bit-identical — only the accumulator's internal shape changed.**

## Performance

- **Duration:** ~6 min
- **Started:** 2026-05-09 (Wave 3 of phase 05)
- **Completed:** 2026-05-09
- **Tasks:** 1 (autonomous, no checkpoints)
- **Files modified:** 1 (`backend/app/routers/messages.py`)
- **Net diff:** +107 insertions, -4 deletions (file grew from 125 → 228 LOC; plan estimated ~210 LOC, actual 228 LOC due to comment density)

## Final Line Numbers in `backend/app/routers/messages.py` (228 LOC total)

| Symbol / Arm | Line range | Notes |
|---|---|---|
| `import uuid` | L3 | Added between `import logging` (L2) and `from fastapi ...` (L4) |
| `elif event_type == "token":` | L80-82 | UNCHANGED (bit-identical to pre-edit) |
| `elif event_type == "tool_thinking":` | L83-85 | UNCHANGED (bit-identical to pre-edit) |
| `elif event_type == "tool_start":` | L86-88 | UNCHANGED (bit-identical to pre-edit) |
| `elif event_type == "tool_done":` | L89-91 | UNCHANGED (bit-identical to pre-edit) |
| `elif event_type == "sub_agent_start":` | **L92-127** | REFACTORED: slot append + dual-emit (legacy + generalized envelope with sub_agent_id) |
| `elif event_type == "sub_agent_tool_start":` | **L128-147** | **NEW** (Explorer-only): append to tools_used[-1].tool_calls + dual-emit |
| `elif event_type == "sub_agent_tool_done":` | **L148-166** | **NEW** (Explorer-only): result_preview[:300] write to tools_used[-1].tool_calls[-1] + dual-emit |
| `elif event_type == "sub_agent_token":` | **L167-182** | REFACTORED: server-side agent_name resolution + dual-emit |
| `elif event_type == "sub_agent_done":` | **L183-204** | REFACTORED: tools_used[-1].sub_agent_result write (was [0]) + dual-emit |
| `elif event_type == "done":` | L205-206 | UNCHANGED (bit-identical to pre-edit) |
| `except Exception as e:` (event_generator try/except) | L207-211 | UNCHANGED (bit-identical to pre-edit) |
| Persistence path (`if full_response.strip()` … `insert_data["tool_metadata"] = json.dumps(tool_metadata)` … `supabase.table("messages").insert(insert_data).execute()`) | **L213-226** | **UNCHANGED — bit-identical to pre-edit** |

## Persistence Path Bit-Identity Confirmation

```
$ git diff a3b0880..HEAD -- backend/app/routers/messages.py | grep -E "^[+-][^+-]" | grep "insert_data\[.tool_metadata.\]"
(zero matches)
```

The single `insert_data["tool_metadata"] = json.dumps(tool_metadata)` line at L222 has zero +/- entries in the diff. The full persistence block (`if full_response.strip(): insert_data = {...}; if tool_metadata: ...; supabase.table("messages").insert(insert_data).execute()` at L213-226) is bit-identical to its pre-edit form. Confirmed by:

```
$ python -c "src=open('backend/app/routers/messages.py').read(); print(src.count('insert_data[\"tool_metadata\"] = json.dumps(tool_metadata)'))"
1
```

## Sample Dual-Emit Wire Output

For a representative Explorer sub-agent flow that emits one `sub_agent_token` event with content `"Found two papers about phages."`, the SSE channel produces TWO consecutive frames:

**1) LEGACY frame** (consumed by Phase 5 frontend in current release window):
```json
{"type": "sub_agent_token", "content": "Found two papers about phages."}
```

**2) GENERALIZED envelope** (consumed by Phase 6 frontend after plan-checker removes legacy):
```json
{"type": "sub_agent", "agent_name": "explore_knowledge_base", "event": "token", "payload": {"content": "Found two papers about phages."}}
```

Note `agent_name` uniformity: the generalized envelope carries `agent_name` as the immediately-following key after `type` for ALL FIVE sub-agent events (start/token/tool_start/tool_done/done). Token and done arms resolve `agent_name` server-side from `tool_metadata['tools_used'][-1]['tool']` since the wire payload itself does not carry it (run_sub_agent and run_explorer_sub_agent token events emit raw text, not JSON-with-agent_name).

For the legacy `analyze_document` path (Module 8, where `sub_agent_start` payload has only `{document_name: ...}` and no `agent_name` field), the legacy fallback `parsed.get("agent_name", "analyze_document")` preserves the existing persistence contract: `tool_metadata.tools_used[0].tool == "analyze_document"`. Same chat behaviour, same JSONB shape (additively gained `tool_calls: []` + `sub_agent_id`).

## tool_metadata Accumulator Shape (Post-Plan-04)

For an assistant turn that calls BOTH analyze_document AND explore_knowledge_base in sequence, the persisted `messages.tool_metadata` JSONB row reads:

```json
{
  "tools_used": [
    {
      "tool": "analyze_document",
      "sub_agent_id": "<uuid-1>",
      "tool_calls": [],
      "document_name": "phages-overview.pdf",
      "sub_agent_result": "[300-char-bounded analyze_document summary]"
    },
    {
      "tool": "explore_knowledge_base",
      "sub_agent_id": "<uuid-2>",
      "tool_calls": [
        {"tool": "list_files",     "args": {"scope":"both"},                   "turn": 1, "result_preview": "[300-char preview]"},
        {"tool": "search_documents","args": {"query":"phage CRISPR"},          "turn": 2, "result_preview": "[300-char preview]"},
        {"tool": "read_document",  "args": {"document_id":"...", "max_chars":3000}, "turn": 3, "result_preview": "[300-char preview]"}
      ],
      "question": "What does my knowledge base say about phage-CRISPR interactions?",
      "sub_agent_result": "[300-char-bounded compact summary]"
    }
  ]
}
```

Phase 6's UI-10 reads this on chat reload to render the Explorer trace UI (per EXPLORER-05 — old chats render correctly).

## Verification Results

| Check | Result |
|---|---|
| `python -m py_compile backend/app/routers/messages.py` | EXIT 0 |
| Plan 04 Task 1 verify command | `OK Plan 04 Task 1 verified` |
| `python -c "import app.routers.messages"` (from backend/) | OK — module imports cleanly |
| Backend boot smoke test (`TestClient(app).get('/health')`) | `backend boots ok` (status 200) |
| `import uuid` at top | Present at L3 |
| `elif event_type == "sub_agent_start":` count | 1 |
| `elif event_type == "sub_agent_tool_start":` count | 1 |
| `elif event_type == "sub_agent_tool_done":` count | 1 |
| `elif event_type == "sub_agent_token":` count | 1 |
| `elif event_type == "sub_agent_done":` count | 1 |
| Generalized envelope yields (`"type": "sub_agent"`) | 5 (one per: start, token, tool_start, tool_done, done) |
| `agent_name` follows `"type": "sub_agent"` in all 5 arms | Verified by regex `r'"type":\\s*"sub_agent",\\s*"agent_name":\\s*[a-zA-Z_"]'` returning ≥5 matches |
| Legacy yields (`"type": "sub_agent_*"`) preserved | 5 (start/tool_start/tool_done/token/done) |
| `sub_agent_id = str(uuid.uuid4())` | Present (L99) |
| `tool_metadata["tools_used"].append(slot)` | Present (L119) |
| `tool_metadata["tools_used"][-1]` (recursive accumulator) | Multiple uses (L134, L154, L173, L191) |
| `parsed.get("result_preview", "")[:300]` (V8 cap) | Present (L156-158) |
| `data[:300]` (V8 cap on sub_agent_result + done payload) | Present (L191, L203) |
| Persistence path edit count | 1 occurrence of `insert_data["tool_metadata"] = json.dumps(tool_metadata)` (L222) — UNCHANGED |
| Backend boots with parent `.env` (Gemini/Supabase keys) | OK |

## Task Commits

1. **Task 1: Refactor event_generator to dual-emit + recursive tool_metadata accumulator + new sub_agent_tool_* arms** — `dd688ac` (feat)

## Decisions Made

- **Append-slot vs overwrite-[0]:** Multi-sub-agent-per-message support is the requirement (Pitfall 12: a single assistant turn can sequence analyze_document into explore_knowledge_base, or vice versa). The append + [-1] pattern is the simplest accumulator that handles N≥1 sub-agents. For N=1 (the Module 8 legacy path), `tools_used[-1]` resolves to `tools_used[0]` — backwards-compatible.
- **Legacy fallback `agent_name="analyze_document"`:** Module 8's `run_sub_agent` is unchanged in Phase 5 (zero edits to sub_agent.py for analyze_document path). Its `sub_agent_start` payload has no `agent_name` field. The fallback preserves the existing frontend contract (`tool_metadata.tools_used[0].tool === "analyze_document"`) without requiring a sub_agent.py edit.
- **Server-side agent_name resolution for token/done arms:** The wire payload for sub_agent_token is raw text (`("sub_agent_token", chunk_text)`), and for sub_agent_done is summary text (`("sub_agent_done", full_summary)`). Embedding agent_name in those payloads would require a sub_agent.py edit. Resolving server-side from `tool_metadata['tools_used'][-1]['tool']` (set by the most recent `sub_agent_start`) is zero-coupling and uniform across both Module 8 (analyze_document) and Plan 02 (explore_knowledge_base) paths.
- **Dual-emit window vs hard switchover:** Pitfall 12 mandates "generalize NOW, not later" — but a hard switchover would break the in-flight Phase 5 frontend (Plan 05) which still listens to legacy event types. Dual-emit gives the frontend ONE release to migrate; Phase 6's plan-checker enforces the legacy removal so the protocol-fork debt doesn't persist past Phase 6.
- **Defensive `.get("tools_used")` accessors:** The `sub_agent_tool_start` arm could in theory arrive without a preceding `sub_agent_start` (impossible per Plan 02 contract, but the defensive guard preserves V7 generator-never-raises). The arm silently no-ops the accumulator update in that pathological case but still emits the SSE frames, keeping the wire protocol consistent.
- **`tool_calls: []` initialization on every slot (including analyze_document):** Additive shape change. analyze_document never appends to it (Module 8 doesn't emit sub_agent_tool_*), so its persisted JSONB has `tool_calls: []` empty. This keeps the accumulator shape uniform and lets Phase 6's frontend render the same component for both sub-agent types (collapsed `tool_calls` panel for analyze_document, expanded for explore_knowledge_base).

## Deviations from Plan

None — plan executed exactly as written. All 21 acceptance-criteria items in Task 1 pass:

1. `import uuid` at import block top — verified at L3
2. `elif event_type == "sub_agent_start":` (refactored arm) — L92
3. `elif event_type == "sub_agent_tool_start":` (NEW) — L128
4. `elif event_type == "sub_agent_tool_done":` (NEW) — L148
5. `elif event_type == "sub_agent_token":` (refactored) — L167
6. `elif event_type == "sub_agent_done":` (refactored) — L183
7. `sub_agent_id = str(uuid.uuid4())` — L99
8. `tool_metadata["tools_used"].append(slot)` — L119
9. `tool_metadata["tools_used"][-1]` (last slot) — L134, L154, L173, L191
10. `agent_name = parsed.get("agent_name", "analyze_document")` (legacy fallback) — L107
11. `"event": "start"` — L124
12. `"event": "tool_start"` — L144
13. `"event": "tool_done"` — L164
14. `"event": "token"` — L179
15. `"event": "done"` — L201
16. `parsed.get("result_preview", "")[:300]` — L156-158
17. `data[:300]` — L191, L203
18. 5 generalized envelope yields, 5 legacy yields — confirmed
19. Generalized envelope uniformity (`agent_name` follows `type` in all 5 arms) — confirmed by regex
20. Persistence path UNCHANGED (`insert_data["tool_metadata"] = json.dumps(tool_metadata)` count == 1) — confirmed
21. Closing `done` arm + try/except + persistence block bit-identical — confirmed

## Issues Encountered

- **Worktree base correction at startup:** `git merge-base HEAD a3b0880488...` returned `376b21d` (the Episode 1 freeze) rather than `a3b0880`. Per the `<worktree_branch_check>` block, ran `git reset --hard a3b0880488...` to bring HEAD bit-identical to the expected base. Confirmed via `git rev-parse HEAD == a3b0880488...` post-reset.
- **No worktree-local Python venv:** Verification used the parent repo's venv at `C:/RAG Automators/.../backend/venv/Scripts/python.exe` (same approach as Plan 01-03). All checks pass.
- **Backend boot test required parent `.env` loaded explicitly:** Default `cd backend && venv/Scripts/python -c "..."` from the parent repo's directory failed with "No API key" because the worktree's backend doesn't bundle a `.env`. Loaded the parent repo's `.env` via `dotenv.load_dotenv()` and added the worktree's backend to `sys.path` before importing — TestClient confirmed `/health` returns 200.

## Next Phase Readiness

- **Plan 05 (frontend Explorer trace renderer)** can now consume EITHER the legacy `{type:'sub_agent_*'}` events OR the generalized envelope. Plan 05's minimum-viable scope listens to legacy (per Pitfall 12 mitigation 3 — frontend listens to ONE channel only to prevent callback double-fire). Phase 6 switches frontend to the generalized envelope.
- **Plan 06 (live Explorer test suite)** can now drive a full Explorer chat end-to-end and pattern-match BOTH dual-emit channels in the SSE log to verify Plan 04's invariants (legacy + generalized frame pair per sub-agent event).
- **Phase 6 plan-checker hook for legacy removal:** Phase 6's plan-checker enforces removal of the LEGACY `yield json.dumps({"type": "sub_agent_*", ...})` lines in this file once Phase 6's frontend rewrite consumes the generalized envelope. Concrete grep-anchored deletion targets:
  - `yield json.dumps({"type": "sub_agent_start", **parsed})` (L121)
  - `yield json.dumps({"type": "sub_agent_tool_start", **parsed})` (L141)
  - `yield json.dumps({"type": "sub_agent_tool_done", **parsed})` (L161)
  - `yield json.dumps({"type": "sub_agent_token", "content": data})` (L177)
  - `yield json.dumps({"type": "sub_agent_done"})` (L199)
- **No new threat surface introduced.** All 6 threat-register entries for Plan 04 (T-05-19 through T-05-24) are mitigated in code per the plan's threat model.

## Threat Flags

None. The plan's `<threat_model>` mitigations are all enforced in code:

- **T-05-19 (T — accumulator state corruption):** defensive `tool_metadata.get("tools_used")` accessors at every multi-sub-agent boundary (L131, L151, L172, L189). Truthiness check before `tools_used[-1]` access. If `sub_agent_tool_start` arrives without a preceding `sub_agent_start` (impossible per Plan 02 contract), the accumulator silently no-ops rather than crashing. Generator-never-raises invariant preserved.
- **T-05-20 (I — LLM-emitted result_preview leaks user data):** 300-char cap at JSONB layer (`parsed.get("result_preview", "")[:300]` at L156-158); 300-char cap on `sub_agent_result` (`data[:300]` at L191); RLS on `messages` table prevents cross-user reads (Migration 010 + existing RLS). ASVS V8.
- **T-05-21 (T — frontend listens to BOTH channels → double-fire):** Plan 05 listens to ONE channel only (legacy preferred for Phase 5 minimum-viable; Phase 6 switches to generalized). The dual-emit window itself is a backend-only contract — any frontend that listens to both would double-fire, which is Plan 05's responsibility to avoid.
- **T-05-22 (D — unbounded tool_metadata growth):** Plan 02 hard-bounds Explorer at MAX_TURNS=8 → tool_calls[] has at most 8 entries per slot; result_preview capped at 300 chars per call → max ~2.4KB tool_calls per slot. RLS-protected JSONB column; no DoS surface beyond a single user's own message row.
- **T-05-23 (E — dual-emit lingers past Phase 6):** Phase 6 plan-checker enforces removal of LEGACY yield statements in this file (5 grep-anchored targets enumerated above in "Next Phase Readiness").
- **T-05-24 (T — persistence path tampering defeats EXPLORER-05):** `insert_data["tool_metadata"] = json.dumps(tool_metadata)` count == 1 (bit-identical to pre-edit). Plan 04 explicitly forbids editing L213-226. Confirmed via `git diff a3b0880..HEAD` showing zero +/- lines containing the persistence-path literal.

ASVS L1 inheritance verified:
- **V2 Auth:** inherited via `/api/threads/{id}/messages` JWT auth (Depends(get_current_user) at messages.py:27).
- **V4 RLS:** inherited (existing thread/message RLS policies; Migration 015 forbid_scope_mutation; Plan 04 introduces no new tables).
- **V5 Input validation:** server-built tool_metadata structure; LLM-emitted args truncated by Plan 02's `_truncate_args_for_sse` (SSE_ARG_CAP=500); JSONB column accepts any structure (no DB-side schema enforcement, but cap-at-write-time discipline bounds payload size).
- **V7 Generator-never-raises:** existing `try: ... except Exception as e: ... yield error + done` at L207-211 surrounds the entire event_generator body — UNCHANGED. Defensive `tool_metadata.get(...)` accessors avoid KeyError inside the try block.
- **V8 Result-preview truncation:** 300-char cap on sub_agent_result + 300-char cap on tool_calls[].result_preview = bounded JSONB exposure.
- **V13 SSE auth:** inherited (sse-starlette EventSourceResponse + JWT-authenticated endpoint).

No new security-relevant surface introduced. No new endpoints, no new auth paths, no new schema changes (Migration 010's `tool_metadata JSONB` column already shipped).

## Self-Check: PASSED

- File `backend/app/routers/messages.py` exists at 228 LOC (was 125 pre-Plan-04; +103 net).
- Commit `dd688ac` (Task 1) exists in `git log --oneline`.
- Commit `dd688ac` post-commit deletion check: zero deleted files.
- `python -m py_compile backend/app/routers/messages.py` exits 0.
- `python -c "import app.routers.messages"` exits 0 (cross-module import OK; no SyntaxError, no ImportError).
- Plan 04 Task 1 verify command prints `OK Plan 04 Task 1 verified`.
- Backend boot smoke test: `TestClient(app).get('/health')` returns 200 with parent `.env` loaded.
- Persistence path bit-identity: `git diff a3b0880..HEAD` shows zero +/- lines touching `insert_data["tool_metadata"]`.
- Dual-emit invariant: 5 legacy `"type": "sub_agent_*"` yields + 5 generalized `"type": "sub_agent"` yields.
- Generalized envelope uniformity: regex `r'"type":\\s*"sub_agent",\\s*"agent_name":\\s*[a-zA-Z_"]'` matches in all 5 generalized arms.
- File `.planning/phases/05-explorer-sub-agent-sse-protocol-generalization/05-04-SUMMARY.md` exists (this file).

---
*Phase: 05-explorer-sub-agent-sse-protocol-generalization*
*Completed: 2026-05-09*
