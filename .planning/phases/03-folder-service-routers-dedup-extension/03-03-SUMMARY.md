---
phase: 03-folder-service-routers-dedup-extension
plan: 03
subsystem: backend-services

tags:
  - dedup
  - record-manager
  - folder-aware
  - scope-aware
  - select-filter-branching
  - pitfall-A
  - back-compat-default-kwargs
  - supabase-py
  - is-null-vs-eq
  - service-layer

# Dependency graph
requires:
  - phase: 01-schema-foundation-two-scope-rls-path-normalizer / Plan 02
    provides: "Migration 012 — documents_scope_user_path_filename_unique unique expression index on (scope, COALESCE(user_id, '00..0'::uuid), folder_path, file_name); the SELECT filter columns in this plan match the index columns in the same order so Postgres uses the index for the dedup lookup"
  - phase: 01-schema-foundation-two-scope-rls-path-normalizer / Plan 02
    provides: "Migration 012 — documents.scope/user_id coupling CHECK (scope='global' requires user_id IS NULL); the .is_('user_id', 'null') branch in this plan targets that schema invariant"
  - phase: pre-Phase-3 baseline
    provides: "backend/app/services/record_manager.py with RecordAction dataclass + compute_file_hash + compute_chunk_hash + the 4-arg determine_action() — all preserved unchanged here; only determine_action's body grows (signature gains 2 trailing kwargs with defaults)"

provides:
  - "backend/app/services/record_manager.py::determine_action() extended with scope: str = 'user' and folder_path: str = '/' kwargs (defaults preserve Phase 1/2 callers)"
  - "Dedup SELECT now filters by (scope, folder_path, file_name) AND (user_id = u OR user_id IS NULL) per scope branch — same dedup key as Migration 012's unique expression index"
  - "FOLDER-05 satisfied at the service layer (Plan 05 owns the router upgrade that passes the new kwargs)"

affects:
  - "Phase 3 Plan 05 (files router PATCH + upload-handler kwargs) — DIRECT consumer; Plan 05 will upgrade backend/app/routers/files.py:73 from `determine_action(file_hash, file_name, user_id, supabase)` to pass `scope=scope, folder_path=normalize_path(folder_path or '/')`"
  - "Phase 3 Plan 06 (test_folders.py integration suite) — asserts FOLDER-05 dedup behavior end-to-end (same file at /a vs /b creates two rows; same file at same dedup key returns action='skip')"
  - "Phase 4 tools (read_document / grep / glob) — INDIRECT; rely on the (scope, folder_path) axes being part of the dedup contract so two same-named docs in different folders both exist for the tools to discover"

# Tech tracking
tech-stack:
  added: []  # No new libraries; pure existing-API extension
  patterns:
    - "Default-kwarg back-compat extension pattern: when a service function gains new arguments mid-project, append them at the end with defaults that exactly match the pre-extension behavior. Existing positional callers keep working untouched; new callers opt in via kwargs. Verified empirically here (backend/app/routers/files.py:73's 4-positional-arg call still type-checks and behaves identically to pre-Phase-3)"
    - "Explicit IS-NULL branch on supabase-py SELECT filters: when the underlying column may be NULL and the unique index uses COALESCE-equivalence for write-time dedup, the SELECT side must explicitly branch — `.eq('col', val)` for non-NULL values; `.is_('col', 'null')` for NULL. supabase-py / PostgREST does NOT auto-translate the COALESCE trick into SELECT filter semantics. This is Pitfall A from 03-RESEARCH.md and is now codebase-documented for any future query that touches a nullable column with a COALESCE-based unique index"
    - "SELECT-filter column-order-matches-unique-index pattern: list .eq() filters in the same column order as the matching unique expression index (here: scope, user_id, folder_path, file_name — matching documents_scope_user_path_filename_unique from Migration 012:51-57). Postgres can then use the index for the lookup without any planner hint. Convention extends to any future read path that filters by a multi-column unique index's exact column list"

key-files:
  created: []
  modified:
    - "backend/app/services/record_manager.py — +37 lines / -14 lines (net +23 LOC; 70 → 93 total). Function determine_action signature gains 2 trailing kwargs (scope='user', folder_path='/'); function body's SELECT clause gains 2 .eq() filters (scope, folder_path) plus a scope-branch on the user_id filter (.eq for 'user', .is_ for 'global'); docstring rewritten to document the (scope, user_id, folder_path, file_name) dedup key + Pitfall A. RecordAction dataclass (L10-14), compute_file_hash (L17-19), compute_chunk_hash (L22-24) UNCHANGED. Try/except shape preserved (try: SELECT; except: return RecordAction(action='create', ...)). Skip/update return paths unchanged."

key-decisions:
  - "Defaults `scope='user'` and `folder_path='/'` chosen to exactly match Phase 1/2 (Episode-1-style) root-folder upload semantics — existing positional caller at backend/app/routers/files.py:73 continues to work without modification"
  - "Used `.is_('user_id', 'null')` (literal string 'null') for the global-scope branch — the supabase-py PostgREST builder maps this to SQL `IS NULL`. Pitfall A: `.eq('user_id', None)` and `.eq('user_id', user_id)` would BOTH never match a NULL column"
  - "DID NOT add a new index — Migration 012's `documents_scope_user_path_filename_unique` is sufficient (the .eq filter column list matches the index column list in the same order; Postgres uses the index for the lookup)"
  - "DID NOT call normalize_path() inside determine_action — normalization is the CALLER's responsibility (the upload-handler chokepoint in Plan 05). Adding it here would duplicate work and violate the single-canonical-place principle (Pitfall 4 chokepoint)"
  - "DID NOT add `from app.services.folder_service import normalize_path` to record_manager — avoids future circular-import risk and keeps record_manager dependency-free as a pure data-layer helper"
  - "DID NOT add `@traceable` LangSmith decorator — out of scope per CONVENTIONS.md (record_manager is a pure data-layer helper; tracing belongs at the router/orchestration layer)"

patterns-established:
  - "Default-kwarg back-compat extension: append new args at the end of the signature with defaults that match pre-extension behavior; verify positional-call back-compat via inspect.signature() in the verification gate"
  - "Explicit IS-NULL filter branch: branch the supabase-py query at SELECT time when a nullable column is in the filter; do NOT rely on the unique index's COALESCE-equivalence to leak into SELECT semantics"
  - "Filter-column-order-matches-index pattern: write .eq() filters in the column order of the matching unique index for free index usage"

requirements-completed: [FOLDER-05]

# Metrics
duration: ~3 min
completed: 2026-05-07
---

# Phase 3 Plan 03: Record Manager Dedup Extension Summary

**Extended `determine_action()` with scope + folder_path kwargs (defaults preserve back-compat) so the same file in two different folders creates two rows; same file at same (scope, user_id, folder_path) deduplicates — satisfying FOLDER-05 at the service layer.**

## Performance

- **Duration:** ~3 min
- **Started:** 2026-05-07T10:14:57Z (approx; commit timestamp HEAD~1)
- **Completed:** 2026-05-07T10:17:34Z (commit timestamp HEAD)
- **Tasks:** 1 (single-task atomic plan)
- **Files modified:** 1 (backend/app/services/record_manager.py)

## Accomplishments

- `determine_action()` signature extended from 4 to 6 parameters; the 2 new kwargs (`scope: str = 'user'`, `folder_path: str = '/'`) are appended at the end with defaults that preserve Phase 1/2 behavior exactly
- Dedup SELECT extended from 2 filters to 4 filters: `.eq("scope", scope).eq("folder_path", folder_path).eq("file_name", file_name)` plus a scope-branched user_id filter (`.eq("user_id", user_id)` for `scope='user'`; `.is_("user_id", "null")` for `scope='global'`) — matching Migration 012's unique index column list in the same order
- Pitfall A (RESEARCH.md L880) explicitly mitigated via the scope-branched user_id filter — the unique index uses `COALESCE(user_id, '00..0')` for write-time NULL-equivalence, but supabase-py SELECT filters do NOT auto-apply that trick; the explicit `.is_('user_id', 'null')` branch is now the canonical pattern for any future nullable-column-in-COALESCE-unique-index query
- Back-compat verified empirically: backend/app/routers/files.py:73's existing 4-positional-arg call (`determine_action(file_hash, file_name, user_id, supabase)`) continues to work; behavior is identical to Phase 1/2 (defaults to scope='user', folder_path='/'). Plan 05 will upgrade the call site to pass the new kwargs explicitly
- All 5 smoke-test paths PASS in isolation: (1) back-compat 4-arg call → action='create' on no-data; (2) user-scope explicit kwargs → action='create'; (3) global-scope branch (.is_('user_id','null')) → action='create' on no-data; (4) existing-row same-hash → action='skip' with document_id; (5) existing-row different-hash → action='update' with document_id
- RecordAction dataclass, compute_file_hash, compute_chunk_hash UNCHANGED (Phase 1 contract preserved); try/except shape around .execute() preserved so .maybe_single() 204 still routes to action='create'

## Task Commits

Each task was committed atomically:

1. **Task 1: Extend determine_action() with scope and folder_path kwargs** — `c86711a` (feat)

**Plan metadata:** to be appended after this SUMMARY (docs commit)

## Files Created/Modified

- `backend/app/services/record_manager.py` — +37 lines / -14 lines (net +23 LOC; 70 → 93 total). determine_action signature gains 2 kwargs (scope, folder_path); body extended with 2 new .eq() filters and a scope-branch on the user_id filter; docstring rewritten. RecordAction dataclass + 2 hash helpers UNCHANGED.

## Decisions Made

- **Default values chosen for back-compat:** `scope='user'` and `folder_path='/'` — these are exactly the dedup semantics that Phase 1/2 (Episode-1-style root-folder uploads) had. Existing positional caller at files.py:73 keeps working without modification.
- **`.is_('user_id', 'null')` for global-scope branch (Pitfall A mitigation):** literal string `'null'` is the supabase-py / PostgREST idiom for `IS NULL`. `.eq('user_id', None)` and `.eq('user_id', user_id)` would BOTH never match a NULL column — Migration 012's coupling CHECK guarantees scope='global' rows always have user_id IS NULL.
- **No new index added:** Migration 012's `documents_scope_user_path_filename_unique` already covers the dedup column list in the same order — Postgres uses it for the lookup.
- **No normalize_path() call inside determine_action:** caller-owned chokepoint principle. The router (Plan 05) normalizes once before calling; record_manager accepts canonical form as-is.
- **No `from app.services.folder_service import normalize_path`:** avoids circular-import risk and keeps record_manager dependency-free as a pure data-layer helper.
- **No `@traceable` decorator added:** out of scope per CONVENTIONS.md; tracing belongs at the orchestration layer, not the data-layer helper.

## Deviations from Plan

None — plan executed exactly as written. The paste-ready function body from PATTERNS.md L870-907 was applied verbatim with the inline comment style from the plan's `<action>` block (line 184-186). All AST/grep/runtime-import/signature-inspection gates passed on first run; all 5 smoke-test paths passed on first run.

---

**Total deviations:** 0
**Impact on plan:** N/A — paste-from-PATTERNS succeeded on first attempt; no Rule 1/2/3 fixes were needed; no Rule 4 architectural decisions surfaced.

## Issues Encountered

None.

## User Setup Required

None — no external service configuration required. This plan is a pure code edit; no migrations, no Supabase Studio steps, no env vars.

## Threat Flags

None — no new security-relevant surface introduced. The plan's `<threat_model>` (T-3-03-PitfallA, T-3-03-DedupBypass, T-3-03-CrossUser, T-3-03-CompatRegression) is fully mitigated by the implementation:
- **T-3-03-PitfallA (mitigate):** explicit `if scope == 'user': .eq(user_id) else: .is_(user_id, 'null')` branch — verified by grep gate `.is_("user_id", "null")` in body
- **T-3-03-DedupBypass (mitigate):** caller's responsibility (Plan 05's router normalizes); determine_action accepts canonical form as-is — documented in docstring
- **T-3-03-CrossUser (accept):** `.eq('user_id', user_id)` for scope='user' / `.is_('user_id', 'null')` for scope='global' prevents cross-scope/user matches at the app layer; Phase 1 RLS is the bedrock
- **T-3-03-CompatRegression (mitigate):** back-compat verified via inspect.signature() — first 4 positional params unchanged in name + order; defaults `scope='user'`, `folder_path='/'` match Phase 1/2 semantics

## Self-Check: PASSED

- File `backend/app/services/record_manager.py` exists and parses as valid Python (ast.parse succeeded in verification gate)
- Commit `c86711a` exists in `git log` (verified by `git log -1 --format=%H` after commit)
- All success criteria from plan satisfied:
  - ✅ determine_action signature gains scope: str = "user" and folder_path: str = "/" kwargs
  - ✅ Dedup SELECT extended with .eq("scope", scope) AND .eq("folder_path", folder_path)
  - ✅ Scope-branched user_id filter (.eq for 'user', .is_("user_id", "null") for 'global')
  - ✅ Existing call site backend/app/routers/files.py:73 NOT changed by this plan
  - ✅ compute_file_hash and compute_chunk_hash UNCHANGED
  - ✅ RecordAction dataclass UNCHANGED
  - ✅ Try/except shape preserved
  - ✅ Module imports cleanly: `venv/Scripts/python -c "from app.services.record_manager import compute_file_hash, determine_action, RecordAction"` exits 0
  - ✅ Plan committed atomically (1 feat commit; docs commit pending after STATE.md update)

## Next Phase Readiness

- **Plan 05 unblocked:** can now upgrade `backend/app/routers/files.py:73` from `determine_action(file_hash, file_name, user_id, supabase)` to pass `scope=scope, folder_path=normalize_path(folder_path or '/')`
- **Plan 06 (test_folders.py) unblocked:** can assert FOLDER-05 dedup behavior end-to-end:
  - Upload file F to /a → action='create'
  - Upload SAME file F to /a (same dedup key) → action='skip'
  - Upload SAME file F to /b (different folder_path; same scope, user_id, file_name) → action='create' (FOLDER-05 acceptance)
  - Global scope: .is_('user_id','null') matches a fixture-inserted global doc → action='skip' on second upload
- **Wave 2 complete:** Phase 3 Wave 2 (Plan 03 — record_manager dedup extension) finished; Wave 3 (Plans 04 + 05 — folders router + files router PATCH) remains the parallel-safe next step

---
*Phase: 03-folder-service-routers-dedup-extension*
*Completed: 2026-05-07*
