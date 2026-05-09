---
phase: 5
slug: explorer-sub-agent-sse-protocol-generalization
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-09
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | Custom Python test suite (matches `test_helpers.py` + `test_all.py` shape used in Phases 1–4) |
| **Config file** | `backend/scripts/test_all.py` SUITES list |
| **Quick run command** | `cd backend && venv/Scripts/python scripts/test_explorer_sub_agent.py` |
| **Full suite command** | `cd backend && venv/Scripts/python scripts/test_all.py` |
| **Estimated runtime** | ~90s single suite warm; ~5 min full suite (17 suites) |

**Pre-reqs:** Backend on `localhost:8001`; `.env` with `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `GEMINI_API_KEY`; Phase 4 Migration 020 already applied (`grep_documents` RPC available); `documents` Storage bucket exists; admin promoted via SQL. Optional `LANGSMITH_API_KEY` + `LANGSMITH_PROJECT` for span-structure assertion (Section 9 SKIPs gracefully without these — Phase 4 psycopg2 EXPLAIN test pattern).

---

## Sampling Rate

- **After every task commit:** Run `cd backend && venv/Scripts/python scripts/test_explorer_sub_agent.py` (~90s)
- **After every plan wave:** Run `cd backend && venv/Scripts/python scripts/test_explorer_sub_agent.py` (still single suite — full sweep is the phase gate)
- **Before `/gsd-verify-work`:** Full suite must be green via `cd backend && venv/Scripts/python scripts/test_all.py`
- **Max feedback latency:** 90 seconds

---

## Per-Task Verification Map

> Filled by gsd-planner from PLAN.md task list during planning. Each task gets a row mapping its requirement → command → file dependency. Wave 0 column marks Wave 0 fixture creation tasks.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 05-01-* | 01 | 0 | EXPLORER-01..06, TEST-03 | T-Pitfall-7 | Helpers + Pydantic args + fixtures | unit | `cd backend && venv/Scripts/python scripts/test_explorer_sub_agent.py` (Section 1 fixture canary) | ❌ W0 | ⬜ pending |
| 05-02-* | 02 | 1 | EXPLORER-01, EXPLORER-02, EXPLORER-03 | T-Pitfall-7 / T-Pitfall-12 | MAX_TURNS bound, wall-clock timeout, no-progress detector, 3-layer recursive ban | unit + integration | `cd backend && venv/Scripts/python scripts/test_explorer_sub_agent.py` (Sections 2+3+4+5) | ❌ W0 | ⬜ pending |
| 05-03-* | 03 | 2 | EXPLORER-01, EXPLORER-03 | T-Pitfall-7 | Tool factory + dispatch arm + system prompt budget statement | unit | same suite (Section 5) | ❌ W0 | ⬜ pending |
| 05-04-* | 04 | 3 | EXPLORER-04, EXPLORER-05 | T-Pitfall-12 | Generalized SSE envelope + dual-emit + tool_metadata accumulator | integration (live SSE) | same suite (Sections 6+7+8) | ❌ W0 | ⬜ pending |
| 05-05-* | 05 | 3 | EXPLORER-04 | T-Pitfall-12 | Frontend SSE callback wiring (parse generalized shape) | integration (live SSE) | same suite (Section 7 cross-validation) | ❌ W0 | ⬜ pending |
| 05-06-* | 06 | 4 | EXPLORER-01..06, TEST-03 | T-Pitfall-7 / T-Pitfall-8 / T-Pitfall-12 | 10-section integration suite + register in `test_all.py` | smoke (full suite) | `cd backend && venv/Scripts/python scripts/test_all.py` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

*Note: rows are placeholders keyed by Plan ID (05-NN-*). gsd-planner replaces these with concrete `05-NN-MM` task IDs once PLAN.md files are written, and gsd-plan-checker verifies sampling continuity (no 3 consecutive tasks without an `<automated>` verify).*

---

## Wave 0 Requirements

- [ ] `backend/app/services/sub_agent.py` — extend with Explorer (or new `sub_agents/explorer.py` per Plan 02 choice)
- [ ] `backend/app/services/openai_client.py` — `_build_explore_knowledge_base_tool()` factory + dispatch arm + system-prompt budget statement
- [ ] `backend/app/routers/messages.py` — extend `event_generator` with new event arms + dual-emit + tool_metadata accumulator refactor
- [ ] `backend/scripts/test_explorer_sub_agent.py` — covers EXPLORER-01..06 + TEST-03 (10 sections; mirrors `test_exploration_tools.py` discipline)
- [ ] `backend/scripts/test_all.py` — register `("Explorer", test_explorer_sub_agent)` after `("Exploration", test_exploration_tools)`; add `import test_explorer_sub_agent` between `import test_exploration_tools` and `import test_backfill`
- [ ] `frontend/src/lib/api.ts` — parse new SSE shape + new callbacks
- [ ] `frontend/src/pages/Chat.tsx` — wire new callbacks (minimum viable)

*No framework install needed — existing test infrastructure covers all phase requirements.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Both `analyze_document` and `explore_knowledge_base` flows render correctly in the same conversation | EXPLORER-04 | UI rendering acceptance — automated SSE assertion verifies events flow; visual rendering needs browser eyes | 1) Start backend (8001) + frontend (5173). 2) Sign in. 3) Send message asking the agent to "summarize doc X" (triggers `analyze_document`). 4) In same thread, send "explore everything you have on topic Y" (triggers Explorer). 5) Confirm both sub-agent sections render with expandable tool calls and don't visually fork. |
| LangSmith trace shows Explorer as a `chain` span with the five tools as nested children (not flat siblings) | EXPLORER-06 | Visual hierarchy in LangSmith UI — automated SDK assertion catches structure but not human-readable hierarchy | 1) Set `LANGSMITH_API_KEY` + `LANGSMITH_PROJECT`. 2) Run a broad fixture query through Explorer. 3) Open the run in LangSmith UI. 4) Confirm Explorer appears as a `chain` span with `tool` children indented beneath it. |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags (suite runs to completion)
- [ ] Feedback latency < 90s
- [ ] `nyquist_compliant: true` set in frontmatter (gsd-plan-checker flips this once Per-Task Verification Map is concrete and continuous)

**Approval:** pending
