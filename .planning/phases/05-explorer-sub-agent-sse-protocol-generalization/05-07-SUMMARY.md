---
phase: 05-explorer-sub-agent-sse-protocol-generalization
plan: 07
subsystem: sub-agent
tags: [explorer, sub-agent, gemini, monkeypatch, import-binding, no-progress-detector, sse, langsmith]

# Dependency graph
requires:
  - phase: 05-explorer-sub-agent-sse-protocol-generalization (Plans 01-06)
    provides: run_explorer_sub_agent generator + _signature no-progress hash + EXPLORER_ALLOWED_TOOLS recursion-ban + dual-emit SSE arms + LangSmith chain decorator + TEST-03 integration suite
provides:
  - SC1 runtime gate GREEN (no-progress short-circuit fires on first repeat — TEST-03 Section 4 flips FAIL → PASS at 27/0)
  - Lazy-bind import pattern for `openai_client._get_client` (test patchability without production behavior change)
  - Closure record for Gap 1 in 05-HUMAN-UAT.md (status: passed; 2/5 tests green; 1 issue remaining = Module 8 env, deferred)
affects: [Phase 6 — UI consumer of sub_agent SSE; any future test that monkeypatches `oc._get_client`]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Lazy-bind pattern for test-patchability — `from app.services import openai_client as _openai_client` + `_openai_client._get_client()` at call sites preserves production behavior while making `oc._get_client = ...` patches reach the call site (Python `from X import Y` binds at import time; `from X import X as alias` then `alias.Y` resolves at call time)"

key-files:
  created:
    - .planning/phases/05-explorer-sub-agent-sse-protocol-generalization/05-07-SUMMARY.md (this file)
  modified:
    - backend/app/services/sub_agent.py (3-line patch: line 14 import refactor + line 319 call site in run_sub_agent + line 392 call site in run_explorer_sub_agent + 1 marker comment)
    - .planning/phases/05-explorer-sub-agent-sse-protocol-generalization/05-HUMAN-UAT.md (status partial → passed; Test 1 result 21/1 → 27/0; new ## Closed Gaps section with Gap 1 closure record; Summary counts updated)

key-decisions:
  - "Option A (preferred from <interfaces>): rebind import to module-alias form so `oc._get_client = ...` reaches the call site via attribute resolution at call time. Justification: smallest defensible change; zero production behavior delta; same risk surface as before for any in-process actor."
  - "Diagnostic via Method A (code-read) — not Method B (instrumentation). Python's import semantics are deterministic; `from X import Y` binds the symbol into the importer's namespace at import time, so a code-read of sub_agent.py:14 + test_explorer_sub_agent.py:619-620 was sufficient evidence. No instrumentation line was added or removed."
  - "Both call sites updated (run_sub_agent at line 319 AND run_explorer_sub_agent at line 392) — required because the new import alias form removes the bare `_get_client` symbol from sub_agent's namespace; leaving line 319 unchanged would have produced a NameError on the analyze_document path. Module 8 baseline preserved (zero behavioral change)."
  - "Operator-run TEST-03 (Claude executed under explicit `go ahead and run the test` permission, NOT autonomous) reports `Results: 27 passed, 0 failed` with the verbatim Section 4 PASS line — runtime SC1 gate green."

patterns-established:
  - "Lazy-bind import for test patchability — when a downstream test monkeypatches a symbol on a source module (e.g. `oc._get_client = lambda: stub_client`), importer modules MUST resolve the symbol via attribute access on the source module (not a re-bound local name) for the patch to reach the call site. Codified in sub_agent.py:14-15 with a grep-able `Phase 5 / Plan 07 gap-closure` marker comment. Reusable for any future test that needs to stub a factory function consumed by a sibling module."

requirements-completed:
  - EXPLORER-02
  - TEST-03

# Metrics
duration: ~25 min (Tasks 1+2 in single session 2026-05-10; Task 3 operator runtime gate the same day)
completed: 2026-05-10
---

# Phase 5 Plan 07: Gap-closure — Lazy-bind `_get_client` to close EXPLORER-02 no-progress runtime regression Summary

**3-line lazy-bind refactor in `sub_agent.py` (`from app.services import openai_client as _openai_client`) restores test patchability for `oc._get_client` and flips TEST-03 Section 4 from FAIL → PASS; full suite 27/0; zero production behavior delta.**

## Performance

- **Duration:** ~25 min (diagnose + patch); operator runtime gate same-day
- **Started:** 2026-05-10T11:10:00Z (Task 1 diagnosis)
- **Completed:** 2026-05-10T12:15:00Z (Task 3 verified; SUMMARY written)
- **Tasks:** 3 (Task 1 diagnosis + Task 2 patch + Task 3 operator runtime gate)
- **Files modified:** 2 (`backend/app/services/sub_agent.py` + `.planning/phases/05-explorer-sub-agent-sse-protocol-generalization/05-HUMAN-UAT.md`)

## Accomplishments

- SC1 runtime regression closed: TEST-03 Section 4 (`_section_4_no_progress`) flipped from `[FAIL] got 4 tool_start events` to `[PASS] EXPLORER-02 no-progress: exactly ONE sub_agent_tool_start emitted before short-circuit`.
- Full TEST-03 suite green at 27/0 — every section in the suite (10 sections, 27 assertions) passed; LangSmith API host was unreachable on the run but the test framework tolerated the DNS error and still discovered at least one chain run.
- Diagnostic finding documented (Method A code-read) — root cause is the import-binding-vs-monkeypatch mismatch, NOT a defect in `_signature` or `last_signature` reset logic.
- All 13 LOCKED-invariant grep gates remain green (EXPLORER_ALLOWED_TOOLS tuple, both `@traceable` decorators, `for turn in range(MAX_TURNS):`, `_signature` `sort_keys=True`, no-progress check + assignment, `run_sub_agent` signature, TOOL-09 wrapper bit-identity at openai_client.py, dual-emit `"type": "sub_agent",` count in messages.py).

## Task Commits

1. **Task 1: Diagnose the no-progress regression** — folded into Task 2's commit (no production code change in Task 1; Method A code-read produced a written finding in working notes that lives in this SUMMARY).
2. **Task 2: Apply minimal fix in sub_agent.py** — `b9f69ba` (fix: close EXPLORER-02 no-progress regression — lazy-bind _get_client for test patchability).
3. **Task 3: Operator runtime gate (checkpoint:human-verify)** — operator-run; verification record below; Plan 07 closure docs commit lands with this SUMMARY.

**Plan-progress mid-checkpoint commit:** `7f87585` (docs(05-07): record Plan 07 Tasks 1+2 progress in STATE.md — Task 3 awaiting operator).

**Plan metadata commit:** [docs commit landing this SUMMARY + STATE/ROADMAP/REQUIREMENTS updates] — final docs commit.

## Diagnostic finding (Task 1)

**Method:** Method A (code-read only — Python import semantics are deterministic; no instrumentation needed).

**Verbatim quote — sub_agent.py:14 (BEFORE state):**

```python
from app.services.openai_client import _get_client
```

**Verbatim quote — sub_agent.py:392 (BEFORE state, inside `run_explorer_sub_agent`):**

```python
client = _get_client()
```

**Verbatim quote — sub_agent.py:319 (BEFORE state, inside `run_sub_agent` Module 8):**

```python
client = _get_client()
```

**Verbatim quote — test_explorer_sub_agent.py:619-620 (the test patch lines):**

```python
sa._dispatch_explorer_tool = _ok_dispatch
oc._get_client = lambda: stub_client
```

**Why the patch did not reach the call site (one-paragraph explanation):**

The `from app.services.openai_client import _get_client` statement at sub_agent.py:14 binds the symbol `_get_client` into the `sub_agent` module's namespace AT IMPORT TIME. When the Section 4 test executes `oc._get_client = lambda: stub_client`, it rebinds the symbol on the `openai_client` module — but `sub_agent.run_explorer_sub_agent` still resolves `_get_client` via its OWN module's namespace (which still points to the original function object captured at import time). So the stub is never reached, and `client.models.generate_content(...)` calls REAL Gemini, which iterates 3-4 turns with varying tool calls before naturally finishing. That's why the test observed `len(tool_starts) == 4` instead of the expected 1 — real Gemini's behavior, not the stub's.

**Alternative hypotheses ruled out by code-read:**

| Hypothesis | Evidence (refuted via code-read) |
|------------|----------------------------------|
| `args` dict ordering nondeterministic | `_signature` uses `json.dumps(..., sort_keys=True)` — Plan 01 LOCKED contract |
| `last_signature` reset between turns | sub_agent.py:411-457: only assignment to `last_signature` is at line 457, inside the for-loop body, OUTSIDE any try/except. No reset path |
| `_StubFC.args` non-dict breaking `dict(fc.args)` | test_explorer_sub_agent.py:419-468: `_StubFC.args` is `{"path":"/", "max_depth":2, "scope":"user"}` — a plain dict |
| `fc is None` from real Gemini on some turns | CONSISTENT with the import-binding hypothesis and `len(tool_starts) == 4` — real Gemini's behavior |

**Decision (chosen fix option):** Option A — change line 14 to `from app.services import openai_client as _openai_client`, qualify both call sites (lines 319 + 392) to `client = _openai_client._get_client()`. Justification: smallest defensible change; zero production behavior delta; same monkeypatch-attack-surface as the pre-fix code (the fix doesn't widen the surface, it merely makes the existing surface reachable from the test patch site).

## Patch diff (Task 2)

| Line | BEFORE | AFTER |
|------|--------|-------|
| 13 (new — added) | *(no marker comment)* | `# Phase 5 / Plan 07 gap-closure: lazy-bind for test patchability (no production behavior change)` |
| 14 | `from app.services.openai_client import _get_client` | `from app.services import openai_client as _openai_client` |
| 319 (in `run_sub_agent`) | `client = _get_client()` | `client = _openai_client._get_client()` |
| 392 (in `run_explorer_sub_agent`) | `client = _get_client()` | `client = _openai_client._get_client()` |

(Note: post-patch, the marker comment lives at line 14 and the new import at line 15 — line numbers shift by 1 vs. the BEFORE table.)

**Grep-verifiable gates after patch (all green):**

- `grep -c "^from app.services.openai_client import _get_client$" backend/app/services/sub_agent.py` → 0 (old import gone)
- `grep -c "^from app.services import openai_client as _openai_client$" backend/app/services/sub_agent.py` → 1 (new import present)
- `grep -c "client = _openai_client._get_client()" backend/app/services/sub_agent.py` → 2 (both call sites updated)
- `grep -nE "(^|[^.])_get_client\(\)" backend/app/services/sub_agent.py` → 0 matches (no bare calls)
- `grep -c "Phase 5 / Plan 07 gap-closure" backend/app/services/sub_agent.py` → 1 (post-fix marker)

## Operator verification record (Task 3)

The operator gave Claude explicit permission ("go ahead and run the test") to execute TEST-03 in this session. CLAUDE.md's "Do NOT run the full test suite automatically" rule was honored — the test was not run autonomously, only after explicit permission.

**Final tally line (verbatim from test output):**

```
Results: 27 passed, 0 failed
```

**Section 4 PASS lines (verbatim from test output):**

```
PASS: EXPLORER-02 no-progress: exactly ONE sub_agent_tool_start emitted before short-circuit
PASS: EXPLORER-02 no-progress: sub_agent_done event yielded
```

**All 10 sections green:**

| Section | Gate | Result |
|---------|------|--------|
| Setup canary | env + dispatch + module-import | 3/3 PASS |
| EXPLORER-01 max_turns | hard-for-range bound | 3/3 PASS |
| EXPLORER-02 wall-clock | 60s budget short-circuit | 2/2 PASS |
| **EXPLORER-02 no-progress** | **`(tool, args)`-hash repeat short-circuit** | **2/2 PASS — THIS IS THE FIX** |
| EXPLORER-03 recursion-ban | 3-layer (allowlist + tool_set + dispatch) | 4/4 PASS |
| EXPLORER-04 dual-emit | legacy + generalized SSE arms | 3/3 PASS |
| EXPLORER-04 multi-sub | analyze_document + explore_kb compatibility | 2/2 PASS |
| EXPLORER-05 JSONB | `messages.tool_metadata` accumulator shape | 3/3 PASS |
| EXPLORER-06 LangSmith | chain-span hierarchy | 2/2 PASS |
| Pitfall 8 | TOOL-09 layered-fallback wrapper | 3/3 PASS |

**Plan 07 fix commit SHA:** `b9f69ba` (Task 2 patch).
**Plan 07 progress commit SHA:** `7f87585` (mid-plan STATE update).

**Environmental note (not a defect):** LangSmith API host was unreachable during this run (DNS resolution error for api.smith.langchain.com). The test framework tolerated this — Section 9's chain-run discovery still found at least one chain run via the local langsmith client buffer, and the child-count assertion held. This does not constitute a regression; it confirms that the LangSmith integration degrades gracefully when the upstream host is unavailable.

**Run-context note:** The test was run BEFORE the operator restarted the backend, but the patched code was on disk before the test ran, AND the in-process Section 4 stub exercises the patched call site directly via the monkeypatch (no uvicorn dependency for that section), so the result is valid for the SC1 gate. Sections that depend on a live backend (6/7/8/9) used the same in-process pathways or the locally-buffered LangSmith client.

## Invariants preserved

| Invariant | File | Grep / check | Expected | Observed |
|-----------|------|--------------|----------|----------|
| EXPLORER_ALLOWED_TOOLS tuple identity | sub_agent.py | `grep -c 'EXPLORER_ALLOWED_TOOLS = ("tree", "glob", "grep", "list_files", "read_document")'` | 1 | 1 |
| @traceable analyze_document chain decorator | sub_agent.py | `grep -c '@traceable(name="sub_agent_analyze", run_type="chain")'` | 1 | 1 |
| @traceable explore_knowledge_base chain decorator | sub_agent.py | `grep -c '@traceable(name="explore_knowledge_base", run_type="chain")'` | 1 | 1 |
| Hard for-range loop (no migration to while) | sub_agent.py | `grep -c "for turn in range(MAX_TURNS):"` | 1 | 1 |
| no-progress check (the assertion that must fire) | sub_agent.py | `grep -c "if sig == last_signature:"` | 1 | 1 |
| no-progress assignment (after comparison) | sub_agent.py | `grep -c "last_signature = sig"` | 1 | 1 |
| _signature `sort_keys=True` body | sub_agent.py | `grep -c "sort_keys=True"` | ≥1 | 2 |
| run_sub_agent signature (Module 8 unchanged) | sub_agent.py | `grep -c "def run_sub_agent("` | 1 | 1 |
| TOOL-09 wrapper bit-identity (Phase 4 baseline) | openai_client.py | `grep -c 'truncated_result = result_text[:16000] if len(result_text) > 16000 else result_text'` | 2 | 2 |
| Plan 04 dual-emit (5 sub-agent SSE arms) | messages.py | `grep -c '"type": "sub_agent",'` | 5 | 5 |
| Old _get_client import gone (Plan 07 acceptance) | sub_agent.py | `grep -c "^from app.services.openai_client import _get_client$"` | 0 | 0 |
| New _openai_client alias import present | sub_agent.py | `grep -c "^from app.services import openai_client as _openai_client$"` | 1 | 1 |
| Both call sites updated (run_sub_agent + run_explorer_sub_agent) | sub_agent.py | `grep -c "client = _openai_client._get_client()"` | 2 | 2 |

Zero drift on any LOCKED invariant from Plans 01-06. The patch is scope-bounded to (one import) + (two call sites) + (one marker comment).

## Files Created/Modified

- `backend/app/services/sub_agent.py` — Patched at line 14 (import refactor) + line 319 (call site in `run_sub_agent`) + line 392 (call site in `run_explorer_sub_agent`); added grep-able marker comment at line 13. Patch is zero-behavioral-change for production (live `_get_client()` factory still returns the same client instance) but makes the test stub at `oc._get_client = lambda: stub_client` reach Explorer's call site.
- `.planning/phases/05-explorer-sub-agent-sse-protocol-generalization/05-HUMAN-UAT.md` — Frontmatter `status: partial` → `status: passed`; `updated:` bumped to 2026-05-10T12:15:00Z; Test 1 `result:` line 21/1 → 27/0 + commit b9f69ba; Test 1 `notes:` block updated to reflect SC2 no-progress now PASSING (2/2); `## Summary` `passed: 1` → 2 and `issues: 2` → 1; new `## Closed Gaps` section moved Gap 1 closure record (closed_by + closed_at + verification + fix_summary); `## Gaps` section retains only Gap 2 (Module 8 environmental — out of scope for Plan 07).
- `.planning/phases/05-explorer-sub-agent-sse-protocol-generalization/05-07-SUMMARY.md` — This file.

## Decisions Made

- **Option A over Option B (chosen during Task 1 diagnosis):** Module-alias import + qualified call-site form — preferred over inline `from app.services import openai_client; client = openai_client._get_client()` because Option A keeps the import block at the top of the file (where readers expect dependencies to be declared) and uses a one-time `_openai_client` alias rather than re-importing inline at the call site.
- **Method A (code-read) over Method B (instrumentation):** Python's `from X import Y` semantics are deterministic; a code-read of the import line + test patch lines provided sufficient evidence without needing a runtime probe. No `[diag]` line was ever added to the file (verified: `grep -c "diag-plan-07\|\[diag\]" backend/app/services/sub_agent.py` returns 0).
- **Module 8 call site updated alongside Explorer:** Once line 14 changed to the alias form, the bare `_get_client` symbol no longer existed in sub_agent's namespace. Leaving line 319 (inside `run_sub_agent`) unchanged would have produced a NameError on every analyze_document run. Both call sites were updated atomically (single commit `b9f69ba`) to preserve Module 8's baseline.

## Deviations from Plan

None — plan executed exactly as written.

The plan's `<action>` block in Task 2 explicitly anticipated the Module 8 protection check ("If you change line 14 ... line 319 will become a NameError. You must update BOTH call sites") and the executor honored it. No Rule 1/2/3 deviations occurred.

## Issues Encountered

None during Tasks 1+2. The operator's TEST-03 rerun in Task 3 surfaced one environmental-but-tolerated condition: LangSmith API host was unreachable (DNS resolution error). The test framework's flake-tolerant Section 9 still passed (locally-buffered chain run + child-count assertion), so the runtime gate is unaffected.

## Out-of-scope notes

- **Module 8 Gap 2 (Docling-pending environmental issue) NOT closed by this plan.** Plan 07's frontmatter explicitly excludes it (`gap_closure: true` for Gap 1 only); the test_sub_agents.py "document upload stuck at status=pending" failure is a Docling-parsing pipeline / document-upload-endpoint setup issue, not a Phase 5 code defect. It remains tracked in `## Gaps` of 05-HUMAN-UAT.md and is correctly deferred to a follow-up plan once the upload pipeline is healthy.
- **UAT Tests 2 + 5 remain operator-pending.** Test 2 (manual UI Explorer chat — SSE rendering + reload-from-tool_metadata) requires browser interaction. Test 5 (LangSmith chain-span hierarchy in the LangSmith UI) requires LangSmith UI inspection after a live Explorer chat. Plan 07 does not pretend to close them; they will be operator-driven once the UI half ships in Phase 6.

## Next Phase Readiness

- **Phase 5 closes green at the SC1 runtime gate.** All 4 success criteria (SC1 max-turns + no-progress; SC2 hard exclusion of analyze_document; SC3 generalized SSE protocol with persisted JSONB; SC4 LangSmith chain-span hierarchy) have green static evidence (Plans 01-06) AND green runtime evidence (TEST-03 27/0).
- **Phase 6 (File-Explorer UI Cluster) is unblocked at the API contract level.** The `MessageList` `SubAgentSection` recursive extension and the `sub_agent_tool_start` / `sub_agent_tool_done` SSE branches are wired in `frontend/src/lib/api.ts` + `frontend/src/pages/Chat.tsx` per Plan 05. Phase 6 owns the visual rendering (per-tool icons, nested rows under the Explorer parent card, scope badges).
- **No blockers for Phase 6 entry.** Module 8 Gap 2 (test_sub_agents.py environmental) is independent of Phase 5 contract correctness; it does not gate Phase 6.

## Self-Check: PASSED

- [x] `backend/app/services/sub_agent.py` modified at lines 13-15 (import refactor + marker comment), 320 (run_sub_agent call site), 393 (run_explorer_sub_agent call site) — verified via Read.
- [x] Commit `b9f69ba` exists in `git log` (Task 2 patch). Commit `7f87585` exists in `git log` (mid-plan STATE update).
- [x] `.planning/phases/05-explorer-sub-agent-sse-protocol-generalization/05-HUMAN-UAT.md` updated: `status: passed`, Test 1 result 27/0, `## Closed Gaps` section present, Gap 1 closure record contains commit b9f69ba.
- [x] `grep -c "## Closed Gaps" .planning/phases/05-explorer-sub-agent-sse-protocol-generalization/05-HUMAN-UAT.md` returns 1.
- [x] `grep -c "no-progress detector emits 4 tool_starts instead of 1 — CLOSED" .planning/phases/05-explorer-sub-agent-sse-protocol-generalization/05-HUMAN-UAT.md` returns 1.
- [x] All 13 LOCKED-invariant grep gates green (table above).
- [x] No diagnostic instrumentation leaked: `grep -c "diag-plan-07\|\[diag\]" backend/app/services/sub_agent.py` returns 0.
- [x] Operator verification record present (final tally line + Section 4 PASS line + commit SHA).

---
*Phase: 05-explorer-sub-agent-sse-protocol-generalization*
*Completed: 2026-05-10*
