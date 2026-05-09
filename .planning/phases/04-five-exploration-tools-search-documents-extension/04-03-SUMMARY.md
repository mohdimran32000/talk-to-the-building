---
phase: 04-five-exploration-tools-search-documents-extension
plan: 03
subsystem: api
tags: [gemini-tools, langsmith-traceable, dispatch-routing, scope-tag, truncation, pydantic-v2, folder-service]

# Dependency graph
requires:
  - phase: 04-five-exploration-tools-search-documents-extension
    plan: 02
    provides: ListFilesArgs (Pydantic v2 args schema), apply_12k_cap (TOOL-08 truncation), ensure_scope_tag (TOOL-07 invariant)
  - phase: 03-folder-service-routers-and-dedup-extension
    provides: folder_service.list_folder (UNION pattern documents+folders+inferred), folder_service.normalize_path (Pitfall 4 chokepoint), folder_service._assert_uuid (HI-01 defense)
  - phase: 01-two-scope-foundation
    provides: Migration 012 canonical-form CHECK on documents.folder_path (third leg of triple chokepoint)
provides:
  - app.services.exploration_tools.list_files.list_files — TOOL-04 single-folder one-level listing tool with folders-then-files-alpha ordering, scope-tagged rows, 12K cap, structured error envelope
  - app.services.openai_client._build_list_files_tool — Gemini FunctionDeclaration factory (path + scope props; required=[])
  - app.services.openai_client dispatch arm `elif tool_name == "list_files"` — TOOL-09 routing into the existing layered-fallback wrapper (UNCHANGED at L565-610)
  - LOCKED template Plans 04-07 mirror: artifact split (tool fn + factory + dispatch arm), cross-cutting concerns (normalize_path-first, @traceable, ensure_scope_tag, apply_12k_cap), dispatch-arm shape (lazy imports + Pydantic try/except + result_text = json.dumps)
affects: [04-04 (tree), 04-05 (glob), 04-06 (read_document), 04-07 (grep), 04-08 (search_documents extension), 04-09 (test_exploration_tools)]

# Tech tracking
tech-stack:
  added: []  # No new deps — reuses langsmith.traceable, pydantic v2, supabase-py from Phase 1+
  patterns:
    - "TOOL-09 dispatch arm shape: lazy import inside elif + ListFilesArgs(**args) try/except + result_text = json.dumps(tool_result) → flows into wrapper unchanged"
    - "Tool function shape: @traceable decorated, normalize_path() first, delegate to existing service (no hand-rolled UNION), ensure_scope_tag per row, apply_12k_cap on happy-path return"
    - "Subfolder scope re-derivation per RESEARCH.md A5(b): walk documents list looking for folder_path startswith(sub) — fallback to args.scope, then 'user'"
    - "Structured error envelope on tool function: {tool, error: CODE, message} dict — INVALID_PATH (normalize_path ValueError) and QUERY_FAILED (list_folder Exception); error path skips apply_12k_cap (small dicts)"
    - "Dispatch-arm error envelope: INVALID_ARGS on Pydantic ValidationError → json.dumps({tool, error: INVALID_ARGS, message}) → still routes through wrapper"
    - "Additive extension to openai_client.py: new factory + new registration line + new dispatch arm; wrapper at L565-610 and existing 4 factories/4 arms UNCHANGED"

key-files:
  created:
    - backend/app/services/exploration_tools/list_files.py
  modified:
    - backend/app/services/openai_client.py

key-decisions:
  - "list_files delegates to folder_service.list_folder rather than re-rolling the UNION query (Don't Hand-Roll — Plan 02 LOCKED list_folder as the single source of truth for the documents+folders+inferred shape)"
  - "Subfolder scope re-derivation uses RESEARCH.md A5 option (b): walk documents list for folder_path.startswith(sub_path); fallback to args.scope, then 'user' (safer default)"
  - "Error path returns dict directly without apply_12k_cap wrap — error dicts are small (~4 fields); apply_12k_cap is a no-op there but the asymmetry signals 'this is an error envelope' to the LLM"
  - "ListFilesArgs.scope='both' is the default; required=[] in the Gemini Schema lets the LLM call list_files with no args (lists root with both scopes — common case)"
  - "Lazy imports inside the elif arm (matches sub_agent import convention at analyze_document branch L537); avoids top-level import bloat and preserves the established pattern"
  - "isinstance(tool_result, dict) guard before .get('total', 0) in the SSE detail string — defends against a future bug surfacing a non-dict result without 5xx-ing the dispatch loop"
  - "@traceable decorator from langsmith adds a 'config' kwarg to the wrapped function signature (verified across _execute_search_documents and execute_sql_query); the plan's strict signature check (params == ['args','user_id','supabase_client']) was relaxed to params[:3] == [...] AND 'config' in params — same contract, more accurate assertion"

patterns-established:
  - "Pattern: Phase 4 tool function shape — @traceable + normalize_path-first + delegate-to-service + ensure_scope_tag-per-row + apply_12k_cap. Plans 04-07 mirror."
  - "Pattern: Phase 4 dispatch arm shape — lazy import + Pydantic args parse with try/except → result_text = json.dumps(tool_result). Both branches (parse-success + parse-error) yield tool_done. Plans 04-07 mirror."
  - "Pattern: subfolder scope re-derivation by walking documents list (A5 option b) — applicable to any tool that returns folder + doc rows in one entries list (list_files, tree, glob with type='both')."
  - "Pattern: structured error envelope variants — INVALID_PATH (normalize_path), QUERY_FAILED (service Exception), INVALID_ARGS (Pydantic). Plans 04-07 reuse the same error code naming."

requirements-completed: [TOOL-04, TOOL-06, TOOL-07, TOOL-08, TOOL-09, TOOL-10]

# Metrics
duration: 9min
completed: 2026-05-09
---

# Phase 4 Plan 03: list_files (TOOL-04) Summary

**TOOL-04 single-folder one-level listing tool — @traceable wrapper around folder_service.list_folder with folders-then-files-alpha ordering, scope-tagged rows, 12K cap, and additive openai_client dispatch wiring; locks the template Plans 04-07 mirror.**

## Performance

- **Duration:** ~9 min
- **Started:** 2026-05-09 (worktree wave 2)
- **Completed:** 2026-05-09
- **Tasks:** 2
- **Files created:** 1 (list_files.py — 131 lines)
- **Files modified:** 1 (openai_client.py — +63 lines additive)
- **LOC delta:** +194

## Accomplishments

- TOOL-04 list_files() function landed: delegates to folder_service.list_folder, applies folders-then-files-alpha ordering, tags every entry with scope, caps result at 12K chars, and returns structured error envelopes on caught exceptions.
- Six cross-cutting Phase 4 concerns honored in one function body: normalize_path chokepoint (TOOL-04 implicit), @traceable tracing (TOOL-10), ensure_scope_tag invariant (TOOL-07), apply_12k_cap (TOOL-08), no-Gemini-SDK + no-HTTPException (TOOL-09 routing contract), Pydantic v2 args (TOOL-06).
- openai_client.py extended additively: _build_list_files_tool factory (FunctionDeclaration with path/scope schema, required=[]), registration in `if has_documents:` block alongside existing _build_search_tool/_build_analyze_tool, and `elif tool_name == "list_files":` dispatch arm with INVALID_ARGS/happy-path branches both assigning result_text and yielding tool_done.
- Wrapper at openai_client.py L565-610 (truncated_result = result_text[:16000] + 4-layer fallback) UNCHANGED; existing 4 factories and 4 dispatch arms UNCHANGED. Verified via post-edit `body.count(...)` checks.
- Locked template Plans 04-07 follow: artifact split (tool fn + factory + dispatch arm), cross-cutting concern checklist, dispatch-arm shape with try/except Pydantic parse, subfolder scope re-derivation pattern (RESEARCH A5 option b).

## Task Commits

Each task committed atomically (parallel-wave with --no-verify):

1. **Task 1: list_files.py — TOOL-04 + cross-cutting concerns** — `32eede3` (feat)
2. **Task 2: openai_client.py extension — factory + registration + dispatch arm** — `9d99cf1` (feat)

## Files Created/Modified

- `backend/app/services/exploration_tools/list_files.py` (NEW, 131 lines) — public list_files() function decorated with @traceable(name="list_files", run_type="tool"); delegates to folder_service.list_folder; applies TOOL-04 ordering (folders sorted alpha then docs sorted alpha by file_name.lower()); tags each entry with scope (subfolders re-derived from documents per A5 option b, docs use d['scope'] from folder_service projection); wraps result in apply_12k_cap; returns INVALID_PATH (normalize_path ValueError) or QUERY_FAILED (list_folder Exception) error envelopes; includes _infer_subfolder_scope helper.
- `backend/app/services/openai_client.py` (MODIFIED, +63 lines additive — file now 733 lines) — three localized edit points: (1) _build_list_files_tool() factory inserted after _build_analyze_tool() at L188-220 with types.FunctionDeclaration name="list_files", path/scope properties, scope.enum=["user","global","both"], required=[]; (2) registration `function_declarations.append(_build_list_files_tool())` inside `if has_documents:` block after _build_search_tool; (3) `elif tool_name == "list_files":` dispatch arm inserted after analyze_document branch with lazy imports of list_files + ListFilesArgs, try/except Pydantic parse → INVALID_ARGS envelope, else branch calls _list_files() and assigns result_text = json.dumps(tool_result), both branches yield tool_done. Wrapper at L565-610 (now ~L628-673) unchanged.

## Public APIs Established (consumed by Plans 04-07 + Plan 09)

**`app.services.exploration_tools.list_files`:**
- `list_files(args: ListFilesArgs, user_id: Optional[str], supabase_client) -> dict` — happy-path returns `{tool: 'list_files', scope_arg, path, entries: [...], total, truncation_marker}`; error-path returns `{tool: 'list_files', error: 'INVALID_PATH'|'QUERY_FAILED', message, [path, scope_arg]}`. The `entries` list is folders-then-files; each entry carries `scope ∈ {'user','global'}`.

**`app.services.openai_client`:**
- `_build_list_files_tool() -> types.FunctionDeclaration` — returned object exposes `name='list_files'`, `parameters.type=Type.OBJECT`, `parameters.properties={'path','scope'}`, `parameters.properties['scope'].enum=['user','global','both']`, `parameters.required=[]`.
- New dispatch arm: when fc.name == 'list_files', the arm parses args via ListFilesArgs(**args), calls list_files(), assigns result_text = json.dumps(tool_result), yields ('tool_done', detail).

## Decisions Made

See key-decisions in frontmatter. Highlights:
- Delegation to folder_service.list_folder over re-rolling the UNION query (Don't Hand-Roll — RESEARCH §Tool Block D)
- Subfolder scope re-derivation via documents-list walk (A5 option b) with fallback chain: doc-derived → args.scope → 'user'
- Error envelopes returned directly without apply_12k_cap wrap (error dicts small; asymmetry signals error semantics to LLM)
- required=[] in the Gemini Schema lets the LLM call list_files() with zero args (lists root with both scopes — the most common case)
- Lazy imports inside the elif arm — matches existing sub_agent convention at analyze_document branch
- isinstance(tool_result, dict) guard on .get('total', 0) — defense against future non-dict surfacing without 5xx-ing the dispatch loop

## Threat Mitigations

| Threat ID | STRIDE | Mitigation Verified |
|-----------|--------|---------------------|
| T-04-03-01 | Information Disclosure (cross-scope leak) | ListFilesArgs has no user_id field (verified Plan 02 schemas.py) — dispatch arm derives user_id from JWT (Episode 1 invariant); RLS on documents/folders/document_chunks gates visibility at DB; scope arg is narrowing on top of RLS, never the access decision. |
| T-04-03-02 | Tampering (path traversal) | Triple chokepoint verified inline: (1) Pydantic _PATH_RE rejects most malformed paths at parse time; (2) normalize_path runs as FIRST STATEMENT and rejects '..'/'.' segments via _FORBIDDEN_SEGMENTS — confirmed via smoke test where ListFilesArgs(path='/..') passed parse but list_files() returned INVALID_PATH envelope; (3) Migration 012 CHECK on documents.folder_path enforces canonical form at DB. |
| T-04-03-03 | Elevation of Privilege (PostgREST injection) | Inherited via delegation to folder_service.list_folder which already calls _assert_uuid(user_id, field_name='user_id') before the or_() interpolation (HI-01 from Phase 3 / Plan 02). No separate _assert_uuid call needed at the tool layer. |
| T-04-03-04 | Denial of Service (result blow-up) | apply_12k_cap wraps result dict at the tail of the happy path; sets `truncation_marker` and trims entries from the END until under 12K chars (Plan 02 implementation). The 16K wrapper cap at openai_client.py:567 (UNCHANGED) is the second layer. |
| T-04-03-05 | Repudiation (empty Gemini response) | Dispatch arm assigns result_text = json.dumps(tool_result) to the SAME variable the wrapper at L565-610 consumes (verified via assertion `truncated_result = result_text[:16000]` still present in body). Wrapper layered fallback (streaming → non-streaming → raw yield) untouched. Tool function never calls client.models.generate_content_stream (verified by grep gate `assert 'generate_content' not in body`). |

## Deviations from Plan

**Total: 1 minor deviation (test assertion correction, no code drift)**

### Auto-fixed Issues

**1. [Rule 1 - Bug] Plan's smoke-test signature assertion was incorrect**
- **Found during:** Task 1 verification (plan's automated smoke test)
- **Issue:** Plan's smoke test asserted `inspect.signature(list_files).parameters == ['args','user_id','supabase_client']` (3 params), but the @traceable decorator from langsmith always appends a `config` kwarg to the wrapped signature (4 params total). Verified against existing traceable sites in the codebase — `_execute_search_documents` shows `['search_query','metadata_filter','user_id','supabase_client','config']` and `execute_sql_query` shows `['question','user_id','supabase_client','config']`. The assertion was specified in the plan based on the contract before decoration, but `inspect.signature` reads the post-decoration signature.
- **Fix:** Relaxed the smoke-test assertion to `params[:3] == ['args','user_id','supabase_client'] AND 'config' in params` — same contract (the function still accepts the documented 3-arg call), more accurate assertion. The actual function code is unchanged; this is a test-script correction only.
- **Files modified:** None (smoke test only — assertion lives in inline `python -c` in this session, not a checked-in file).
- **Verification:** Re-ran the corrected smoke test; all checks pass; `sig=['args','user_id','supabase_client','config']` printed.
- **Committed in:** N/A (test-script correction only — list_files.py code is correct)

---

**Total deviations:** 1 auto-fixed (1 bug — incorrect smoke-test assertion in plan)
**Impact on plan:** Zero scope creep. The actual deliverables (list_files.py + openai_client.py edits) match the plan's spec exactly. The deviation is a smoke-test assertion correction grounded in the codebase's verified langsmith convention.

## Issues Encountered

None of substance. The single test-assertion mismatch above was caught immediately and corrected; root cause (langsmith @traceable signature appending) is documented in the deviation entry.

## Smoke Tests Run

All inline smoke tests from the plan ran via `cd backend && venv/Scripts/python -c ...` and passed:

- **Task 1 structural** — ast.parse OK; `def list_files` present; `@traceable(name="list_files", run_type="tool")` present; normalize_path call present; list_folder delegation present; apply_12k_cap present; ensure_scope_tag present; sorted() present; INVALID_PATH error code present; no `generate_content` substring; no `HTTPException`; importable; signature core = ('args','user_id','supabase_client') with langsmith config kwarg appended; 131 lines (>= 60 required).
- **Task 1 functional** — `ListFilesArgs(path='/..', scope='user')` passes Pydantic regex (because `..` matches `[^/]+`), but `list_files(args, ...)` returns the documented INVALID_PATH envelope `{'tool': 'list_files', 'error': 'INVALID_PATH', 'message': "Invalid path segment: '..'..."}`. Triple chokepoint confirmed.
- **Task 2 structural** — ast.parse OK; `def _build_list_files_tool` present; `name="list_files"` in factory; scope `enum=["user","global","both"]` present; `function_declarations.append(_build_list_files_tool())` present; `elif tool_name == "list_files":` present; lazy imports `from app.services.exploration_tools.list_files import list_files` and `from app.services.exploration_tools.schemas import ListFilesArgs` present; `ListFilesArgs(**args)` present; `result_text = json.dumps(tool_result)` present (TOOL-09 routing); `INVALID_ARGS` present; `truncated_result = result_text[:16000]` STILL present (wrapper at L565-610 UNCHANGED check passes); existing `_build_search_tool`/`_build_analyze_tool`/`_build_sql_tool`/`_build_web_search_tool` each present exactly once; existing `if tool_name == "search_documents":`/`elif tool_name == "query_structured_data":`/`elif tool_name == "web_search":`/`elif tool_name == "analyze_document":` arms each present exactly once.
- **Task 2 runtime** — `from app.services.openai_client import _build_list_files_tool` succeeds; returned `FunctionDeclaration(name='list_files')` with `parameters.type=Type.OBJECT`, `properties keys=['path','scope']`, `scope.enum=['user','global','both']`, `required=[]`.

## User Setup Required

None — no external service configuration required. Plan extends in-process Python and Gemini tool registration only; uses the venv at the main repo path (worktree has no separate venv) which already has langsmith + pydantic + supabase-py + google-genai installed from Phase 1.

## Next Phase Readiness

**Plans 04-07 (tree, glob, read_document, grep) unblocked for parallel implementation under Wave 2.** Each plan can now follow the locked template:

1. Create `app/services/exploration_tools/<tool>.py` with @traceable-decorated public function: normalize_path-first → service-layer query (delegate to folder_service or new RPC) → ensure_scope_tag per row → apply_12k_cap on happy-path return → structured error envelope on exception.
2. Add `_build_<tool>_tool()` factory to openai_client.py alongside _build_list_files_tool (same lazy `from google.genai import types` body shape).
3. Register inside `if has_documents:` block: `try: function_declarations.append(_build_<tool>_tool())` etc.
4. Add `elif tool_name == "<tool>":` dispatch arm AFTER list_files arm and BEFORE the unknown-tool fall-through; same try/except Pydantic parse → result_text = json.dumps(tool_result) shape.
5. Wrapper at openai_client.py L565-610 (now ~L628-673) STAYS UNCHANGED.

Plan 09 (test_exploration_tools.py) will exercise list_files end-to-end via SSE alongside the other four tools.

**ROADMAP Phase 4 Success Criteria mapping:**
- SC1 (registered + dispatched + Pydantic-validated + layered-fallback routed): list_files ✓ — first of five
- SC3 (every result row carries scope): list_files ✓ — folders re-derive from documents (A5 b), docs use folder_service projection

## Self-Check: PASSED

Verification results:
- `backend/app/services/exploration_tools/list_files.py` — FOUND (131 lines)
- `backend/app/services/openai_client.py` — FOUND (modified, 733 lines)
- Commit `32eede3` (feat(04-03): list_files tool — TOOL-04 single-folder listing) — FOUND in git log
- Commit `9d99cf1` (feat(04-03): wire list_files into openai_client dispatch) — FOUND in git log
- All Task 1 smoke checks PASSED (structural + import + functional INVALID_PATH envelope)
- All Task 2 smoke checks PASSED (structural + runtime + wrapper-UNCHANGED + existing-arms-UNCHANGED)
- No STATE.md / ROADMAP.md / REQUIREMENTS.md modifications (worktree mode — orchestrator owns shared state)

---
*Phase: 04-five-exploration-tools-search-documents-extension*
*Plan: 03*
*Completed: 2026-05-09*
