---
phase: 01-schema-foundation-two-scope-rls-path-normalizer
plan: 01
subsystem: backend-services
tags: [python, path-normalization, security, unicode, regex, pure-function]

# Dependency graph
requires:
  - phase: none
    provides: Wave 1 plan with no prior dependencies
provides:
  - normalize_path() pure-function helper at app.services.folder_service
  - _CANONICAL_PATH_RE constant mirroring DB CHECK regex
  - _FORBIDDEN_SEGMENTS set rejecting '.' and '..' path segments
affects:
  - phase 01 plan 02 (DB CHECK regex must match Python output)
  - phase 01 plan 03 (folders.path uses same canonical form)
  - phase 01 plan 08 (test_two_scope_rls.py imports normalize_path)
  - phase 03 (folder CRUD will extend this same file)
  - phase 04 (tool arg parsing for tree/glob/grep/list_files/read_document)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pure-function service module (no DB, no I/O) — analog to record_manager.py hash helpers"
    - "Single canonical chokepoint for input canonicalization (Pitfall 4 mitigation)"
    - "Defense-in-depth: Python normalize + DB CHECK regex"
    - "Inline self-tests via if __name__ == '__main__' for fast sanity checks"

key-files:
  created:
    - backend/app/services/folder_service.py
  modified: []

key-decisions:
  - "Used stdlib only (re, unicodedata) — no third-party deps for leaf module"
  - "ValueError (not custom exception) for invalid input — Pythonic + matches CONVENTIONS.md"
  - "NFC Unicode normalization (not NFD/NFKC) to prevent visually-identical-bytes-different attack while preserving common composed forms"
  - "Case preserved (no lowercasing) — Postgres comparison is case-sensitive; '/Projects' and '/projects' are intentionally distinct (per Pitfall 4 warning)"
  - "Inline self-tests in __main__ block (15 cases) — fast sanity check; full matrix in plan 08 test"
  - "No __all__, no barrel re-exports — direct import surface per CONVENTIONS.md §Module Design"

patterns-established:
  - "Path canonicalization: leading slash, no trailing slash (except root), no double slashes, no backslashes, NFC Unicode, case preserved"
  - "Module-private constants use underscore prefix (_CANONICAL_PATH_RE, _FORBIDDEN_SEGMENTS)"
  - "Service modules expose top-level functions, no class wrapper for stateless helpers"

requirements-completed: [FOLDER-01]

# Metrics
duration: 1min
completed: 2026-05-03
---

# Phase 01 Plan 01: normalize_path() Pure-Function Helper Summary

**Path canonicalization chokepoint at `backend/app/services/folder_service.py` — leading-slash + NFC Unicode normalization with `..`/`.` traversal rejection, mirroring the migration 012/013 DB CHECK regex.**

## Performance

- **Duration:** ~1 min
- **Started:** 2026-05-03T16:06:06Z
- **Completed:** 2026-05-03T16:07:05Z
- **Tasks:** 1
- **Files modified:** 1 (created)

## Accomplishments

- Created `backend/app/services/folder_service.py` (96 lines) — single canonical chokepoint for folder-path normalization across Episode 2.
- `normalize_path(p: str | None) -> str` ships exactly the spec from RESEARCH.md §3 — leading slash, no trailing slash (except root), collapsed double slashes, backslash→slash, NFC Unicode, case preserved.
- Path-traversal segments (`..`, `.`) raise `ValueError` — Python is the enforcement layer for these (the DB CHECK regex passes them because they have no `/` inside).
- `_CANONICAL_PATH_RE = re.compile(r"^/$|^/[^/]+(/[^/]+)*$")` constant exposed for downstream use; mirrors the DB CHECK constraint that lands in plan 02.
- Inline `__main__` self-test block (15 assertions: 11 round-trip + 4 rejection) verified passing in ~0.1s via `python -m app.services.folder_service`.

## Task Commits

Each task was committed atomically:

1. **Task 1-01-01: Create folder_service.py with normalize_path() and inline pytest-style assertions** — `b608452` (feat)

**Plan metadata commit:** pending (created after STATE.md/ROADMAP.md updates).

## Files Created/Modified

- `backend/app/services/folder_service.py` (created, 96 lines) — pure-function path canonicalization helper. Exposes `normalize_path()`, `_CANONICAL_PATH_RE`, `_FORBIDDEN_SEGMENTS`. Stdlib-only imports (`re`, `unicodedata`). No DB access, no I/O.

## Normalization Rules Implemented

1. None or empty string → `/` (root sentinel).
2. Unicode NFC normalization applied first.
3. Backslash → slash conversion (Windows-origin LLM hallucination defense).
4. Double slashes collapsed via repeated `replace("//", "/")`.
5. Leading slash prepended if absent.
6. Trailing slash stripped (except root `/`).
7. After all transforms, segments are split on `/` and validated against `_FORBIDDEN_SEGMENTS = {"..", "."}` and empty-string check.
8. Final regex match against `^/$|^/[^/]+(/[^/]+)*$` as a defense-in-depth assertion.
9. Case preserved (no lowercasing) — `/Projects` ≠ `/projects` is intentional.

## Round-Trip Cases Verified (11)

| Input | Output |
|-------|--------|
| `/` | `/` |
| `/a` | `/a` |
| `/a/b` | `/a/b` |
| `/a/b/c` | `/a/b/c` |
| `/A/B` | `/A/B` (case preserved) |
| `/a//b` | `/a/b` (double-slash collapsed) |
| `a/b` | `/a/b` (leading slash prepended) |
| `/a/b/` | `/a/b` (trailing slash stripped) |
| `\a\b` | `/a/b` (backslash→slash) |
| `""` | `/` |
| `None` | `/` |

## Rejection Cases Verified (4)

| Input | Behavior |
|-------|----------|
| `/a/../b` | raises `ValueError` (path traversal rejected) |
| `/a/./b` | raises `ValueError` (current-dir segment rejected) |
| `/foo/../../etc/passwd` | raises `ValueError` (multi-segment traversal rejected) |
| `/.` | raises `ValueError` |

## Import Path for Downstream Phases

```python
from app.services.folder_service import normalize_path
```

Phase 3 will extend this same file with `list_folder`, `create_folder`, `move_document`, `rename_folder`, `delete_folder`. Phase 4 tool arg parsers (tree/glob/grep/list_files/read_document) will route every `path`/`folder_path` arg through `normalize_path()` before any DB access.

## Decisions Made

- Stdlib-only imports (`re`, `unicodedata`) — module is a leaf with no app-level dependencies.
- `ValueError` for invalid input rather than a custom exception class — standard Python convention for invalid string args.
- NFC normalization (not NFD/NFKC) — prevents visually-identical-bytes-different attack while preserving the most common composed forms users actually type.
- Case preserved — Postgres TEXT comparison is case-sensitive by default; tests in plan 08 enforce that `/Projects` and `/projects` remain distinct.
- Inline `__main__` self-tests — fast sanity check (<1s); full test matrix lives in plan 08's `test_two_scope_rls.py`.

## Deviations from Plan

None — plan executed exactly as written. The reference implementation in PLAN §action / RESEARCH.md §3 was paste-applied verbatim and passed all 15 inline self-tests on first run.

## Issues Encountered

None.

## Threat Mitigation Coverage

- **T-1-02 (Tampering / Information Disclosure):** Mitigated. `..` and `.` segments raise `ValueError` (Python enforcement layer for path traversal). Backslash→slash conversion handles Windows-origin hallucinations. NFC normalization defends against homoglyph-byte-difference attacks. DB CHECK regex (plan 02) provides defense in depth.
- **T-1-04 (Tampering — module surface):** Mitigated. This file is the single canonical chokepoint; no other module re-implements path canonicalization. Phase 3+ write paths will import `normalize_path` from this module exclusively.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- `normalize_path()` is importable as `from app.services.folder_service import normalize_path` — ready for plans 02–08 to consume.
- Plan 02 (migration 012) can now author its DB CHECK regex with confidence that the Python and SQL layers will agree on canonical form.
- Plan 08's `test_two_scope_rls.py` can import `normalize_path` directly for FOLDER-01 falsifiable assertions 17–28.
- No blockers for Wave 2 (plans 02–06 migrations).

## Self-Check: PASSED

**Files exist:**
- FOUND: `backend/app/services/folder_service.py`

**Commits exist:**
- FOUND: `b608452` (feat(01-01): add normalize_path helper in folder_service.py)

**Verification commands run:**
- `cd backend && venv/Scripts/python -m app.services.folder_service` → exit 0, prints "folder_service.normalize_path: 15 self-tests passed"
- `from app.services.folder_service import normalize_path; assert normalize_path('/a//b') == '/a/b'; assert normalize_path(None) == '/'` → OK
- Rejection check for `/a/../b` and `/a/./b` → both raise `ValueError` → OK

---
*Phase: 01-schema-foundation-two-scope-rls-path-normalizer*
*Completed: 2026-05-03*
