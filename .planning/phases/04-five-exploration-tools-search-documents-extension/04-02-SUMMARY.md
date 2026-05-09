---
phase: 04-five-exploration-tools-search-documents-extension
plan: 02
subsystem: api
tags: [pydantic-v2, gemini-tools, validation, truncation, scope-tag, defense-in-depth]

# Dependency graph
requires:
  - phase: 03-folder-service-routers-and-dedup-extension
    provides: folder_service.normalize_path + _CANONICAL_PATH_RE chokepoint (mirrored byte-identical here as schemas._PATH_RE)
  - phase: 01-two-scope-foundation
    provides: Migration 012 canonical-form CHECK regex on documents.folder_path (third leg of triple chokepoint)
provides:
  - app.services.exploration_tools.schemas — TreeArgs, GlobArgs, GrepArgs, ListFilesArgs, ReadDocumentArgs (TOOL-06)
  - app.services.exploration_tools._truncate.apply_12k_cap — TOOL-08 12K-char total truncation helper
  - app.services.exploration_tools._scope_tag.ensure_scope_tag — TOOL-07 scope-invariant defense helper
  - exploration_tools/ package skeleton (Plans 03–07 hang per-tool modules off of this)
affects: [04-03 (list_files), 04-04 (tree), 04-05 (glob), 04-06 (read_document), 04-07 (grep), 04-08 (search_documents extension), 04-09 (test_exploration_tools)]

# Tech tracking
tech-stack:
  added: []  # stdlib + pydantic v2 only — no new deps
  patterns:
    - "Pydantic v2 args-validation BaseModels with Literal scope + Field ge/le bounds + extra='ignore' (LLM-facing)"
    - "Triple-chokepoint regex for canonical paths (Python normalize_path + Python _PATH_RE + DB CHECK)"
    - "Total (no-raise) truncation helper with sibling truncation_marker (never embedded in trimmed list)"
    - "Defense-in-depth scope-tag invariant assertion in lieu of trusting SQL projection alone"

key-files:
  created:
    - backend/app/services/exploration_tools/__init__.py
    - backend/app/services/exploration_tools/schemas.py
    - backend/app/services/exploration_tools/_truncate.py
    - backend/app/services/exploration_tools/_scope_tag.py
  modified: []

key-decisions:
  - "schemas.py uses dict-literal model_config = {'extra': 'ignore'} (not ConfigDict import) to match Phase 3 / Plan 01 LOCKED style"
  - "_PATH_RE defined as a module-top string constant, byte-identical to folder_service._CANONICAL_PATH_RE pattern AND Migration 012 CHECK — Pitfall 4 triple chokepoint"
  - "ReadDocumentArgs uses a SEPARATE path regex requiring a file_name segment after the folder, plus @model_validator(mode='after') enforcing exactly-one-of(document_id, path)"
  - "apply_12k_cap is total — never raises; degrades gracefully via marker text variants (3 marker strings cover: normal trim, no-list-found, list-empty-but-still-over-cap)"
  - "Marker is a sibling field on the payload, never embedded in the trimmed list — LLM sees clean list + neighbor marker rather than poisoned final element"
  - "12K cap (Phase 4 LLM-readability heuristic) is intentionally distinct from openai_client.py:567's 16K cap (Gemini-context-window heuristic); both layers fire — 12K inside the tool, 16K in the wrapper"
  - "ensure_scope_tag uses assert (not raise) so invalid scope surfaces loudly as a 5xx (programmer-error invariant) rather than silent miscitation"
  - "__init__.py kept empty (no re-exports) — barrel-file decision deferred to Plans 03–07; Plans use full-path imports per existing flat-services convention"

patterns-established:
  - "Pattern: Pydantic v2 args module for LLM tool calls — Literal enums + Field bounds + extra='ignore' + module-top regex constants"
  - "Pattern: Total-helper-with-marker-string-variants (truncation as a graceful-degradation contract, not a failure)"
  - "Pattern: Defense-in-depth scope assertion as a backstop for SQL-projection regressions"
  - "Pattern: Underscore-prefixed private helper modules (_truncate.py, _scope_tag.py) inside service sub-packages"

requirements-completed: [TOOL-06, TOOL-07, TOOL-08]

# Metrics
duration: 12min
completed: 2026-05-09
---

# Phase 4 Plan 02: Exploration Tools Foundation Summary

**Pydantic v2 args schemas (TOOL-06) + 12K total-truncation helper (TOOL-08) + scope-tag invariant helper (TOOL-07) — the three building blocks Plans 03–07 import as the first/last lines of every per-tool function body.**

## Performance

- **Duration:** ~12 min
- **Started:** 2026-05-09 (worktree wave 1)
- **Completed:** 2026-05-09
- **Tasks:** 2
- **Files created:** 4
- **Files modified:** 0
- **LOC delta:** +257 (148 schemas + 68 truncate + 40 scope_tag + 1 package marker)

## Accomplishments

- Five Pydantic v2 BaseModel args schemas locked: TreeArgs, GlobArgs, GrepArgs, ListFilesArgs, ReadDocumentArgs
- Path regex chokepoint extended from one place (folder_service) to two (folder_service + schemas) with byte-identical regex strings — Pitfall 4 mitigation now triple (Python normalize_path + Python _PATH_RE + DB CHECK on Migration 012)
- TOOL-08 12K-char total truncation helper extracted from inline precedent at openai_client.py:567 — first reusable truncation primitive in the codebase
- TOOL-07 scope-tag invariant helper landed as defense-in-depth backstop for the SQL projection
- Five Args models all explicitly carry `model_config = {"extra": "ignore"}` — Phase 3 / Plan 01 LOCKED defense layer made explicit because the LLM is the caller and exotic args are likely
- exploration_tools/ package skeleton ready — Plans 03–07 unblocked for parallel implementation

## Task Commits

Each task committed atomically (parallel-wave with --no-verify):

1. **Task 1: schemas + __init__.py** — `14b92fa` (feat)
2. **Task 2: _truncate.py + _scope_tag.py** — `c31d7cb` (feat)

## Files Created/Modified

- `backend/app/services/exploration_tools/__init__.py` (1 line) — empty package marker; barrel-file decision deferred
- `backend/app/services/exploration_tools/schemas.py` (148 lines) — TOOL-06 Pydantic v2 args schemas (5 BaseModels) + module-top _PATH_RE regex constant
- `backend/app/services/exploration_tools/_truncate.py` (68 lines) — TOOL-08 apply_12k_cap helper; total function with sibling truncation_marker contract
- `backend/app/services/exploration_tools/_scope_tag.py` (40 lines) — TOOL-07 ensure_scope_tag helper; logger.warning + assert in ('user','global')

## Public APIs Established (consumed by Plans 03–09)

**`app.services.exploration_tools.schemas`:**
- `TreeArgs(path='/', max_depth=2, scope='both')` — max_depth bounded ge=1 le=4 (Pitfall 2 RANK 4)
- `GlobArgs(pattern, path='/', type='both', scope='both')` — pattern length 1..200
- `GrepArgs(pattern, path='/', case_insensitive=True, multiline=False, output_mode='content', A=2, B=2, C=None, scope='both')` — pattern length 1..500; A/B/C bounded ge=0 le=10
- `ListFilesArgs(path='/', scope='both')`
- `ReadDocumentArgs(document_id?, path?, offset=1, limit=2000)` — limit bounded ge=1 le=5000 (TOOL-05 hard cap); model_validator enforces xor(document_id, path)

**`app.services.exploration_tools._truncate`:**
- `apply_12k_cap(payload: dict, *, char_cap: int = 12_000) -> dict` — total; sets `payload['truncation_marker']` to `None` (no trim), `'[...truncated, N more entries]'` (trimmed), `'[...truncated; payload too large to summarize]'` (no list found), or `'[...truncated; non-list fields exceed cap]'` (list emptied but still over cap)

**`app.services.exploration_tools._scope_tag`:**
- `ensure_scope_tag(row: dict, default: Literal['user','global'] = 'user') -> dict` — injects scope=default with logger.warning if missing; AssertionError on scope not in ('user','global'); returns same row for chaining

## Decisions Made

See key-decisions in frontmatter. Highlights:
- Dict-literal model_config form (not ConfigDict import) — matches Phase 3 / Plan 01 LOCKED style
- _PATH_RE byte-identical across three locations (Python normalize_path regex source, schemas.py, Migration 012 CHECK)
- apply_12k_cap is intentionally total (no raise) — graceful degradation, not failure; tools must produce a result
- ensure_scope_tag uses `assert` (not raise) — invariant violation surfaces loudly via 5xx in dispatch loop
- __init__.py kept empty — barrel-file decision deferred to Plans 03–07

## Threat Mitigations

| Threat ID | STRIDE | Mitigation Verified |
|-----------|--------|---------------------|
| T-04-02-01 | Tampering (path traversal) | _PATH_RE rejects `..`/`.` segments at parse time; smoke check confirms TreeArgs(scope='invalid') raises ValidationError; `..` segments fail [^/]+ character class because they would have to start with `/` |
| T-04-02-02 | Tampering / EoP (field smuggling) | model_config={"extra":"ignore"} confirmed via `GrepArgs.model_validate({'pattern':'x','unknown_field':'leak'})` — resulting instance has NO unknown_field attribute; scope='admin' raises ValidationError (Literal) |
| T-04-02-03 | DoS (limit overflow) | TreeArgs(max_depth=99) raises ValidationError (le=4); ReadDocumentArgs.limit le=5000; GrepArgs A/B/C le=10; apply_12k_cap second-line defense |
| T-04-02-04 | Info Disclosure (missing scope tag) | ensure_scope_tag injects default + warns; assert 'admin' raises AssertionError verified in smoke check |

## Deviations from Plan

None — plan executed exactly as written. Two minor non-deviations worth noting:

1. **Worktree path resolution:** Initial Write tool calls used the absolute `C:\RAG Automators\...\backend\...` path which Windows resolves to the *main* repo, not the worktree. Files were moved to the worktree with `mv` and re-committed. No content change; pure path correction. The smoke checks were re-run from the worktree path and all passed.

2. **venv location:** Worktree has no `venv/`; smoke checks were executed via the main-repo venv at `C:/RAG Automators/.../backend/venv/Scripts/python.exe` against the worktree's source tree (sys.path.insert from worktree backend dir). This is functionally equivalent — the venv only supplies pydantic v2 (2.13.3) and the schemas + helpers were importable cleanly.

## Issues Encountered

None. All seven smoke checks passed on first re-run after path correction:
- TreeArgs() defaults = ('/', 2, 'both')
- TreeArgs(max_depth=99) raises ValidationError (le=4)
- TreeArgs(scope='invalid') raises ValidationError (Literal)
- ReadDocumentArgs() raises ValidationError (model_validator xor)
- GrepArgs.model_validate({'pattern':'x','unknown_field':'leak'}) drops unknown_field
- apply_12k_cap with [{'a':'x'*20000}] returns marker '[...truncated, 1 more entries]'
- apply_12k_cap with [] returns truncation_marker=None
- ensure_scope_tag with valid/missing/invalid scope behaves as specified

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

**Plans 03–07 are unblocked for parallel implementation.** Each per-tool plan can now:
- `from app.services.exploration_tools.schemas import {TreeArgs|GlobArgs|GrepArgs|ListFilesArgs|ReadDocumentArgs}`
- `from app.services.exploration_tools._truncate import apply_12k_cap`
- `from app.services.exploration_tools._scope_tag import ensure_scope_tag`

The chokepoint contract is locked: every tool function will start with `normalize_path()` (folder_service), middle with the per-tool query builder + `ensure_scope_tag` per row, and end with `apply_12k_cap(payload)`.

Plan 09 (test_exploration_tools.py) will additionally import all five Args models for unit-style validation tests.

## Self-Check: PASSED

Verification results:
- `backend/app/services/exploration_tools/__init__.py` — FOUND
- `backend/app/services/exploration_tools/schemas.py` — FOUND (148 lines, all 5 classes + 5 model_config + _PATH_RE + le=4/le=5000/le=10 + model_validator)
- `backend/app/services/exploration_tools/_truncate.py` — FOUND (68 lines, apply_12k_cap with char_cap=12_000, no tiktoken, no raise)
- `backend/app/services/exploration_tools/_scope_tag.py` — FOUND (40 lines, ensure_scope_tag with logger.warning + scope assertion)
- Commit `14b92fa` — FOUND in git log (feat(04-02): exploration_tools schemas)
- Commit `c31d7cb` — FOUND in git log (feat(04-02): exploration_tools helpers)
- All 8 smoke checks passed (5 Pydantic + 3 truncate + 3 scope_tag — see Issues Encountered)

---
*Phase: 04-five-exploration-tools-search-documents-extension*
*Plan: 02*
*Completed: 2026-05-09*
