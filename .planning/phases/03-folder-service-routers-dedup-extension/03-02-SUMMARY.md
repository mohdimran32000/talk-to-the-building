---
phase: 03-folder-service-routers-dedup-extension
plan: 02
subsystem: backend-services

tags:
  - service-layer
  - folder-crud
  - rpc-wrapper
  - normalize-path-chokepoint
  - pitfall-4
  - pitfall-5
  - pitfall-10
  - pure-service-no-fastapi
  - supabase-py
  - postgrest-or-syntax
  - defense-in-depth

# Dependency graph
requires:
  - phase: 03-folder-service-routers-dedup-extension / Plan 01
    provides: "Migration 019 RPCs (rename_folder_prefix, delete_folder_if_empty, create_folder_if_not_exists) — applied live to Postgres + verified via pg_proc; the three thin-wrapper functions added in this plan call those RPCs by exact name with the exact parameter shapes locked in Plan 01's interfaces block"
  - phase: 01-schema-foundation-two-scope-rls-path-normalizer / Plan 01
    provides: "normalize_path() pure helper at folder_service.py:28-67 — the chokepoint that every new function in this plan invokes as its FIRST STATEMENT (Pitfall 4 enforcement). Plan 01's docstring at L11-13 explicitly anticipated this extension"
  - phase: 01-schema-foundation-two-scope-rls-path-normalizer / Plan 02
    provides: "Migration 012 — documents.folder_path/scope columns + scope/user_id coupling CHECK + canonical-path regex; the predicates in list_folder (.eq('folder_path',norm), .eq('scope','user'), .is_('user_id','null')) target this schema"
  - phase: 01-schema-foundation-two-scope-rls-path-normalizer / Plan 03
    provides: "Migration 013 — public.folders side table + unique expression index; list_folder reads this for explicit empty-folder rows; create_folder/rename_folder/delete_folder mutate this via Plan 01 RPCs"
  - phase: 01-schema-foundation-two-scope-rls-path-normalizer / Plan 05
    provides: "Migration 015 — RLS policies + forbid_scope_mutation BEFORE UPDATE trigger; move_document deliberately does NOT touch scope (immutable per the trigger), and the .eq('user_id', user_id) filter is the app-layer mirror of the user-scope RLS predicate"

provides:
  - "backend/app/services/folder_service.py extended with 5 new public functions (list_folder, create_folder, move_document, rename_folder, delete_folder) — total 354 lines (was 96; +258 LOC)"
  - "Phase 3 Wave 3 unblocked: Plan 04 (folders router) can now `from app.services.folder_service import list_folder, create_folder, rename_folder, delete_folder, normalize_path` and Plan 05 (files router PATCH) can `from app.services.folder_service import move_document, normalize_path`"
  - "Phase 3 Wave 4 unblocked (when Plans 03/04/05 land): test_folders.py (Plan 06) can import the five functions for direct service-surface assertions"

affects:
  - "Phase 3 Plan 04 (folders router) — DIRECT consumer of list_folder, create_folder, rename_folder, delete_folder via public function imports"
  - "Phase 3 Plan 05 (files router PATCH) — DIRECT consumer of move_document via public function import"
  - "Phase 3 Plan 06 (test_folders.py integration suite) — asserts the five functions are importable + callable"

# Tech tracking
tech-stack:
  added:
    - "PostgREST or() filter via supabase-py — `.or_(\"and(scope.eq.user,user_id.eq.{user_id}),and(scope.eq.global,user_id.is.null)\")` for scope='both' union queries; new idiom in this codebase (Episode 1 used single-scope tables)"
    - "supabase-py `.not_.like()` chained operator — for 'NOT LIKE \"prefix/%/%\"' immediate-child folder predicate"
    - "supabase-py `.is_('user_id', 'null')` — explicit IS NULL filter for global-scope rows (literal 'null' string for PostgREST is.null operator)"
  patterns:
    - "Service-layer chokepoint pattern: every public service function whose signature includes a path argument runs `normalize_path()` as the FIRST STATEMENT — belt+suspenders alongside the router-layer normalization in Plans 04/05; defense-in-depth third-layer alongside the DB CHECK constraint and the RPC's own canonical-form regex"
    - "RPC-wrapper service pattern: thin Python wrappers (rename_folder, delete_folder, create_folder) translate Plan 01 RPCs into stable plain-dict return shapes; the wrapper hides PostgREST plumbing from the router and gives the router a stable contract that survives RPC body changes (e.g., if Migration 019 ever adds a new return column)"
    - "Defensive empty-data branch: each RPC wrapper checks `if not result.data` and returns a structured zero-count dict instead of raising; the only error that propagates is the no_data_found SQLSTATE from delete_folder_if_empty (folder missing — router decides 404 vs other handling). This keeps the service layer total-function and lets the router layer own HTTP-status mapping"
    - "List-folder dual-source subfolder discovery: explicit folders rows (Strategy B sparse side table — only present for explicitly-empty folders) UNIONed with inferred folders (DISTINCT one-level subfolder names extracted from documents.folder_path LIKE norm||'/%' results); covers both the empty-explicit case AND the implicit-from-files case in a single response without requiring upload-time folders writes (Pitfall 10 mitigation)"

key-files:
  created: []
  modified:
    - "backend/app/services/folder_service.py — +258 lines / 0 deletions; 5 new public functions inserted after L67 (return s of normalize_path) and before the inline __main__ self-tests (which remain at the bottom unchanged); zero new imports needed (re + unicodedata already at top); zero FastAPI imports added (pure service-layer module preserved)"

key-decisions:
  - "Function ordering follows call-graph dependency: list_folder (read-only) → create_folder (RPC wrapper) → move_document (direct UPDATE) → rename_folder (RPC wrapper, requires create_folder semantic prior) → delete_folder (RPC wrapper, requires create_folder/rename_folder prior). Mirrors the natural Phase 3 lifecycle: create → list → move → rename → delete. Easier code review than alphabetic ordering"
  - "supabase_client parameter is positional-untyped on every new function (matches `record_manager.determine_action()` style at L31 — `supabase_client,` with no type hint). Type-hinting it would import the supabase Client class from supabase-py and add a dependency on the service module that nothing else in the file needs. The router-layer Depends(get_supabase) injects a real client; tests can pass any duck-typed mock"
  - "list_folder's scope='both' branch uses PostgREST `or_()` syntax `and(scope.eq.user,user_id.eq.{u}),and(scope.eq.global,user_id.is.null)` — the only way to express 'union of two AND-clauses' in a single supabase-py query without two round-trips. The user_id is interpolated into the f-string deliberately; supabase-py does NOT parameterize or_() arguments (PostgREST design), and user_id at this point is already a UUID-shaped str from the JWT (Plan 04's router will validate before calling)"
  - "Subfolder discovery is a UNION of two sources (explicit folders rows + inferred from documents.folder_path) because Strategy B (locked in STATE.md line 74) keeps folders SPARSE — folders rows only exist for explicitly-empty folders. Without the inferred-from-documents source, a folder containing files but no folders row would not show up in the listing at all. Sorted-deduplicated output for deterministic test assertions"
  - "rename_folder raises ValueError BEFORE invoking the RPC if old/new normalize to '/' — defense in depth alongside the RPC's own root-rename check at Migration 019:60-63. The Python guard fails fast (saves an RPC round-trip) AND gives a more informative error message ('cannot rename root path') than the Postgres check_violation surfacing as a generic exception"
  - "create_folder hydrates the full folders row via a SECOND query (`.table('folders').select('*').eq('id', row['id']).maybe_single()`) after the RPC returns just `(id, created_bool)`. The RPC could be extended to RETURN the full row, but the current contract (Plan 01) returns only the minimum needed for action-differentiation; the hydrate-step keeps the wrapper compatible with that minimal contract while still giving the router a complete FolderResponse-shaped dict (id, scope, user_id, path, created_at, action)"
  - "move_document filters .eq('id', document_id).eq('user_id', user_id) — defense in depth alongside RLS. Even though Migration 015's UPDATE policy on documents already enforces user_id = (SELECT auth.uid()) for scope='user', the explicit .eq('user_id', user_id) at the app layer means a service-role client (which bypasses RLS) still cannot accidentally move another user's document. Pattern matches CONCERNS.md anti-pattern documentation"
  - "Try/except wraps each query block in list_folder (NOT each individual function-wide) — defensive against transient PostgREST errors like maybe_single's 204 quirk, while still letting the function return a partial response (e.g., documents OK + subfolders empty) instead of failing the whole listing. The router (Plan 04) cannot tell the difference between 'no subfolders' and 'subfolder query failed', which is acceptable for a list endpoint"

patterns-established:
  - "RPC-wrapper service pattern: when a service function exists primarily to call an RPC, its job is to (a) normalize input via Pitfall-4 chokepoint, (b) translate Python kwargs to RPC parameter names (p_<name>), (c) extract `result.data[0]` defensively, (d) return a plain dict with stable field names. The router layer never sees PostgREST response shapes"
  - "Inferred-vs-explicit folder UNION pattern: any read endpoint that lists folders MUST union the sparse folders side table with implicit folders inferred from documents.folder_path (Strategy B requires this). One-level-down-only filtering uses `.like(prefix||'%').not_.like(prefix||'%/%')` — the second filter excludes nested descendants. Plan 04's GET /api/folders endpoint and Phase 4's tree tool will reuse this UNION shape"
  - "Service-layer total-function rule: service functions should be total (always return a value) unless the missing data is genuinely a programming error. Plan 02 catches PostgREST 204/500 noise via try/except → empty-list fallback for read paths; only the no_data_found case (folder missing in delete_folder) is allowed to propagate, because the router needs to decide 404 vs 410 vs other"

requirements-completed:
  - FOLDER-02

# Metrics
duration: ~5 min
completed: 2026-05-07
---

# Phase 3 Plan 02: folder_service.py CRUD Extensions Summary

**Five new public service-layer functions (list_folder, create_folder, move_document, rename_folder, delete_folder) added to backend/app/services/folder_service.py — three are thin Python wrappers around Plan 01's Migration 019 RPCs (rename_folder_prefix, delete_folder_if_empty, create_folder_if_not_exists), two are direct supabase-py table queries; every path-accepting function runs normalize_path() as its FIRST STATEMENT (Pitfall 4 chokepoint enforcement); zero FastAPI imports added (pure service-layer module preserved); inline __main__ self-tests still pass 15/15.**

## Performance

- **Duration:** ~5 min (single-task plan; paste-from-PATTERNS body succeeded on first attempt; verify gates green on first run)
- **Started:** 2026-05-07T10:01Z (Wave 2 entry — immediately after Plan 01 commit chain)
- **Completed:** 2026-05-07T10:06Z
- **Tasks:** 1 of 1 (green)
- **Files modified:** 1 (backend/app/services/folder_service.py: +258 lines, 0 deletions; line count 96 → 354)

## Accomplishments

- **folder_service.py extended with 5 new public functions** (FOLDER-02) — list_folder, create_folder, move_document, rename_folder, delete_folder; total +258 LOC
- **Phase 3 Wave 3 unblocked** — Plan 04 (folders router) can `from app.services.folder_service import list_folder, create_folder, rename_folder, delete_folder, normalize_path`; Plan 05 (files router PATCH) can `from app.services.folder_service import move_document, normalize_path`
- **Pitfall 4 chokepoint enforced at service layer** — every new function whose signature includes a path argument runs normalize_path() as its FIRST STATEMENT (suspenders alongside the router-layer belt in Plans 04/05); count of normalize_path() calls in the file: 12 (was 1)
- **Migration 019 RPCs invoked by exact name** — rpc("rename_folder_prefix", ...), rpc("delete_folder_if_empty", ...), rpc("create_folder_if_not_exists", ...) all present verbatim in the file; pg_proc-level integration is now end-to-end exercisable from Python

## Task Commits

Each task committed atomically:

1. **Task 1: Add list_folder, create_folder, move_document, rename_folder, delete_folder to folder_service.py** — `4802edd` (feat)

**Plan metadata commit:** *(this commit — see git log after this SUMMARY lands)*

## Files Created/Modified

- `backend/app/services/folder_service.py` (MODIFIED, +258 / 0) — 5 new public functions inserted between L67 (`return s` of normalize_path) and the inline `__main__` self-tests (preserved unchanged at the bottom). Zero new imports (re + unicodedata already at the top of the file from Plan 01). Zero FastAPI imports added (pure service module convention preserved). New section header comment block at L70-77 documents the four conventions all five functions follow

## Decisions Made

- **Function ordering follows call-graph dependency:** list_folder → create_folder → move_document → rename_folder → delete_folder. Mirrors Phase 3 lifecycle (create → list → move → rename → delete); easier code review than alphabetic
- **supabase_client positional-untyped on every function:** matches record_manager.determine_action style at L31; type-hinting would import supabase Client class as a service-module dependency for no benefit
- **list_folder scope='both' uses PostgREST or_() syntax:** `and(scope.eq.user,user_id.eq.{u}),and(scope.eq.global,user_id.is.null)` — only way to express union of AND-clauses in a single supabase-py query; user_id is interpolated into the f-string (PostgREST does not parameterize or_() arguments)
- **Subfolder discovery is dual-source UNION:** explicit folders rows + inferred-from-documents.folder_path; required because Strategy B (STATE.md line 74) keeps folders sparse — without inferred source, a folder containing files but no folders row would not appear in listings
- **rename_folder raises ValueError BEFORE invoking the RPC** if old/new normalize to '/' — fail-fast (saves a network round-trip) + more informative error than Postgres check_violation
- **create_folder hydrates the full folders row via a second query** — RPC returns minimum (id, created_bool); hydrate-step gives the router a complete FolderResponse-shaped dict (id, scope, user_id, path, created_at, action)
- **move_document filters .eq('id', document_id).eq('user_id', user_id):** defense in depth alongside RLS; service-role client bypassing RLS still cannot accidentally move another user's document
- **Per-block try/except in list_folder** (not per-function): defensive against transient PostgREST 204/500 noise; allows partial response (documents OK + subfolders empty) instead of failing the whole listing

## Deviations from Plan

None - plan executed exactly as written.

(Single-task plan; paste-from-PATTERNS body matched the plan's `<action>` block verbatim; the plan's `<verify>` AST/grep gate AND the smoke-import probe AND the inline self-tests all passed on first run; rename_folder root-guard validated 4/4 boundary cases via inline assertion.)

## Issues Encountered

None. The plan was paste-ready and self-contained.

## User Setup Required

None — folder_service.py module imports cleanly via `cd backend && venv/Scripts/python -c "from app.services.folder_service import normalize_path, list_folder, create_folder, move_document, rename_folder, delete_folder"`. Plans 04 and 05 can begin importing immediately.

## Next Phase Readiness

- **Wave 3 unblocked:** Plan 04 (folders router — backend/app/routers/folders.py NEW + main.py registration) can now build endpoints around list_folder/create_folder/rename_folder/delete_folder; Plan 05 (files router PATCH — backend/app/routers/files.py extension) can now build the PATCH/upload-into-folder paths around move_document/normalize_path
- **Wave 4 prerequisites partially met:** Plan 06 (test_folders.py integration suite) can already assert the five functions are importable + callable; full integration coverage requires Plans 03/04/05 to land first
- **No blockers:** Phase 3 critical path remains clear

## Self-Check: PASSED

- FOUND: backend/app/services/folder_service.py (354 lines, was 96)
- FOUND: .planning/phases/03-folder-service-routers-dedup-extension/03-02-SUMMARY.md (this file)
- FOUND: commit 4802edd (Task 1 — five new folder CRUD functions in folder_service.py)
- VERIFIED: AST parse of folder_service.py succeeds (`venv/Scripts/python -c "import ast,pathlib; ast.parse(pathlib.Path('app/services/folder_service.py').read_text(encoding='utf-8'))"`)
- VERIFIED: 6 top-level functions present (`grep -n "^def " backend/app/services/folder_service.py` → normalize_path L28, list_folder L80, create_folder L184, move_document L227, rename_folder L253, delete_folder L291)
- VERIFIED: 12 occurrences of `normalize_path(` in the file (well above the 6-call minimum: 5 new functions × 1 first-statement call + 7 in inline self-tests)
- VERIFIED: All 3 Migration 019 RPC names appear verbatim (`rpc("rename_folder_prefix"`, `rpc("delete_folder_if_empty"`, `rpc("create_folder_if_not_exists"`)
- VERIFIED: Zero `from fastapi` imports in the file (pure service-layer convention preserved)
- VERIFIED: rename_folder root-rename guard raises ValueError on 4/4 boundary cases (`('/', '/foo')`, `('/foo', '/')`, `('/', '/')`, `('', '/foo')`)
- VERIFIED: Inline self-tests run green via `cd backend && venv/Scripts/python -m app.services.folder_service` → "folder_service.normalize_path: 15 self-tests passed"
- VERIFIED: Smoke-import succeeds via `from app.services.folder_service import normalize_path, list_folder, create_folder, move_document, rename_folder, delete_folder` → "OK"

---
*Phase: 03-folder-service-routers-dedup-extension*
*Completed: 2026-05-07*
