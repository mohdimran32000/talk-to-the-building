---
phase: 4
slug: five-exploration-tools-search-documents-extension
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-08
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from `04-RESEARCH.md` § Validation Architecture (Nyquist Dimensions).

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | Custom Python test suite (matches `test_helpers.py` + `test_all.py`) |
| **Config file** | `backend/scripts/test_all.py` SUITES list |
| **Quick run command** | `cd backend && venv/Scripts/python scripts/test_exploration_tools.py` |
| **Full suite command** | `cd backend && venv/Scripts/python scripts/test_all.py` |
| **Estimated runtime** | ~60 sec single-suite (5000-doc grep fixture is the long pole); ~4 min full suite (16 suites) |

**Pre-reqs:** Backend on `localhost:8001` (Phase 3 canary discipline applies — verify route presence before running); `.env` with `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `GEMINI_API_KEY`; **Migration 020 applied** via `cd backend && venv/Scripts/python scripts/run_migrations.py`; `admin@test.com` promoted via `UPDATE public.profiles SET is_admin=true WHERE email='admin@test.com'`; `documents` Storage bucket exists (Phase 2 carry-forward); optional `DATABASE_URL` env var for psycopg2 EXPLAIN test (test SKIPs gracefully without it, per Phase 3 idiom).

---

## Sampling Rate

- **After every task commit:** Run `cd backend && venv/Scripts/python scripts/test_exploration_tools.py` (single-suite, ~60s warm)
- **After every plan wave:** Run `cd backend && venv/Scripts/python scripts/test_exploration_tools.py` (still single-suite — full suite is the phase gate)
- **Before `/gsd-verify-work`:** Full suite must be green via `cd backend && venv/Scripts/python scripts/test_all.py` (16 suites: existing 15 + Exploration)
- **Max feedback latency:** ~60 seconds per task

---

## Per-Task Verification Map

> Task IDs use the form `{phase}-{plan}-{task}`. Plan IDs are placeholders (`01..NN`) until the planner finalizes the wave structure; this file MUST be re-checked once `04-NN-PLAN.md` files exist (planner is responsible for filling actual `Task ID` values).

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 04-01-01 | 01 (Migration 020 + grep_documents RPC + RPC extensions) | 0 | SEARCH-02, TOOL-03 | Pitfall 3 (grep DoS), Pitfall 1 (scope-leak) | `SET LOCAL statement_timeout = '5s'` inside RPC body; `match_folder_path`/`match_scope` NULL-defaulted; existing call sites unaffected | integration (psycopg2 EXPLAIN + supabase-py rpc) | `cd backend && venv/Scripts/python scripts/test_exploration_tools.py` ([SEARCH-02 backward compat] + [TOOL-03 perf]) | ❌ W0 | ⬜ pending |
| 04-02-01 | 02 (Pydantic v2 schemas module) | 0 | TOOL-06 | V5 Input Validation | `Literal["user","global","both"]` for scope; `Field(..., ge=, le=)` for numeric bounds; `Field(pattern=)` for path; `extra='ignore'` strict mode | unit | same ([TOOL-06 strict args]) | ❌ W0 | ⬜ pending |
| 04-02-02 | 02 (12K char-cap helper) | 0 | TOOL-08 | Pitfall 8 (empty-response upstream protection) | Hard 12K char cap with `[...truncated, N more]` marker; UTF-8 codepoint-safe | unit | same ([TOOL-08 cap helper]) | ❌ W0 | ⬜ pending |
| 04-03-01 | 03 (`list_files` tool — TOOL-04, simplest) | 1 | TOOL-04, TOOL-06, TOOL-07, TOOL-08, TOOL-09, TOOL-10 | Pitfall 11 (scope confusion), Pitfall 1 (RLS) | folders-then-files-alpha; every row has `scope`; `@traceable`; routed through layered-fallback | unit + integration via dispatch | same ([TOOL-04]) | ❌ W0 | ⬜ pending |
| 04-04-01 | 04 (`tree` tool — TOOL-01) | 2 | TOOL-01, TOOL-06, TOOL-07, TOOL-08, TOOL-09, TOOL-10 | Pitfall 2 (RANK 4 — context blow-up), Pitfall 11 | server-side `max_depth` cap (4); 500-entry hard cap; `[N more folders, M more docs]` summaries; 12K char cap | unit + 200-folder adversarial fixture | same ([TOOL-01 truncation] + [TOOL-01 char budget]) | ❌ W0 | ⬜ pending |
| 04-05-01 | 05 (`glob` tool — TOOL-02) | 2 | TOOL-02, TOOL-06, TOOL-07, TOOL-08, TOOL-09, TOOL-10 | Pitfall 11 | `**`/`*` semantics over `folder_path` + `file_name`; 500-match cap; scope-tagged rows | unit | same ([TOOL-02]) | ❌ W0 | ⬜ pending |
| 04-06-01 | 06 (`read_document` tool — TOOL-05) | 3 | TOOL-05, TOOL-06, TOOL-07, TOOL-08, TOOL-09, TOOL-10 | Pitfall 9 (line drift) | 1-based offset; default `limit=2000`; hard cap 5000 lines; arrow-form `{n}→{content}`; CRLF normalized; UTF-8 codepoint-safe last-line truncation; `splitlines(keepends=False)` | unit + adversarial fixtures (CRLF / Unicode / single-long-line / mixed-ending) | same ([TOOL-05 fixtures]) | ❌ W0 | ⬜ pending |
| 04-07-01 | 07 (`grep` tool — TOOL-03, most complex) | 4 | TOOL-03, TOOL-06, TOOL-07, TOOL-08, TOOL-09, TOOL-10 | Pitfall 3 (RANK 1 perf), Pitfall 8 | calls `grep_documents` RPC (Plan 01); ≤50 hits; ±2 line context; rejects pathological regexes (`(.*)+`/`(.+)+` blocklist); EXPLAIN shows `Bitmap Index Scan on documents_content_trgm_idx`; p95 < 500ms over 5000-doc fixture | integration + EXPLAIN ANALYZE assertion (psycopg2) + 10-iter latency timing | same ([TOOL-03 perf] + [TOOL-03 50-hit cap]) | ❌ W0 | ⬜ pending |
| 04-08-01 | 08 (`search_documents` extension — SEARCH-01..03) | 5 | SEARCH-01, SEARCH-02, SEARCH-03 | Pitfall 11 | tool schema accepts optional `folder_path` + `scope`; defaults preserve existing behavior (regression snapshot); system prompt updated to teach LLM self-scoping | integration via real dispatch + system-prompt string-contains | same ([SEARCH-01] + [SEARCH-03]) | ❌ W0 | ⬜ pending |
| 04-09-01 | 09 (Test module + `test_all.py` registration) | 6 | TEST-02 | — | `test_exploration_tools.py` covers TOOL-01..10 + SEARCH-01..03; appended to SUITES after `("Folders", test_folders)` | smoke (full suite runs) | `cd backend && venv/Scripts/python scripts/test_all.py` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

> **Cross-cutting Pitfall 8 / TOOL-09 guard runs in EVERY tool's section** (50K-char adversarial doc → tool emits ≤12K result with truncation marker → dispatch routes through layered-fallback → SSE assistant message non-empty + `has_done_event`). The guard is enforced PER TOOL because TOOL-09 is "every tool routes through the wrapper" — a single shared assertion would mask per-tool drift.

---

## Wave 0 Requirements

- [ ] `backend/migrations/020_phase4_rpcs.sql` — new `grep_documents` RPC (with `SET LOCAL statement_timeout = '5s'`, `LATERAL regexp_split_to_table(..., E'\n') WITH ORDINALITY`); `CREATE OR REPLACE FUNCTION` extending `match_document_chunks_with_filters` (add `match_folder_path TEXT DEFAULT NULL`, `match_scope TEXT DEFAULT NULL`); same extension for `match_document_chunks_hybrid`
- [ ] `backend/app/services/exploration_tools/__init__.py` — public surface: `tree`, `glob_match`, `grep`, `list_files`, `read_document`
- [ ] `backend/app/services/exploration_tools/schemas.py` — five Pydantic v2 `BaseModel` classes (`TreeArgs`, `GlobArgs`, `GrepArgs`, `ListFilesArgs`, `ReadDocumentArgs`) with `Literal["user","global","both"]` for `scope`, `Field(..., ge=, le=)` for numeric bounds, `Field(pattern=...)` for `path`, `extra='ignore'` strict mode (TOOL-06)
- [ ] `backend/app/services/exploration_tools/_truncate.py` — 12K char-cap helper with `[...truncated, N more]` marker (TOOL-08); UTF-8 codepoint-safe (must not slice mid-codepoint)
- [ ] `backend/scripts/test_exploration_tools.py` — 10-section test module mirroring Phase 3 / `test_folders.py` shape; covers TOOL-01..10, SEARCH-01..03, TEST-02; per-id `_tracked_*` cleanup discipline; canary `_verify_phase4_setup` (probes Migration 020 + tool registration in `_build_tools()`)
- [ ] `backend/scripts/test_all.py` SUITES list — append `("Exploration", test_exploration_tools)` after `("Folders", test_folders)` (16th suite); add `import test_exploration_tools` between `import test_folders` and `import test_backfill`
- [ ] **Migration 020 applied via `cd backend && venv/Scripts/python scripts/run_migrations.py` BEFORE running `test_exploration_tools.py`** (operator pre-req — Phase 3 canary discipline mandates this is documented in test failures)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| LangSmith trace contains `@traceable(run_type="tool")` spans for all 5 tools after a real chat turn invokes them | TOOL-10 | Requires LangSmith dashboard access + a real chat session (not unit-testable without mocking the LangSmith ingest endpoint) | After Wave 5 lands: send 5 chat messages each invoking a different tool; open LangSmith project; verify 5 distinct `tool` spans with correct names |
| LLM correctly self-scopes via `folder_path` / `scope` after SEARCH-03 prompt update | SEARCH-03 | Subjective — depends on LLM behavior with the updated system prompt; we can string-contains the prompt insertion in unit tests, but "does the model use it?" is qualitative | After Wave 5 lands: send a chat asking about "the architecture docs in /shared/specs"; observe LangSmith trace shows `search_documents` called with `folder_path="/shared/specs"` |
| 50K-char adversarial result actually traverses Gemini → assistant message non-empty (vs. our mock fixture) | TOOL-09 (real-world) | Unit test uses an adversarial fixture; the real-world failure mode at `openai_client.py:565-610` requires a live Gemini call to fully reproduce | Stage a 50K-char doc; send a chat that triggers grep against it; verify SSE stream emits `done` with non-empty assistant content |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references (Migration 020, schemas module, helpers, test file, suite registration)
- [ ] No watch-mode flags
- [ ] Feedback latency < 60s per task
- [ ] `nyquist_compliant: true` set in frontmatter (after planner finalizes plan/task IDs and re-syncs the table above)

**Approval:** pending
