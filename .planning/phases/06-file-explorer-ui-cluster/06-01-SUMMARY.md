---
phase: 06-file-explorer-ui-cluster
plan: 01
subsystem: api
tags: [phase6, backend, schema, pydantic, fastapi, wave0]

# Dependency graph
requires:
  - phase: 01-schema-and-rls
    provides: "Migration 014 added documents.content_markdown_status TEXT column with CHECK ('pending','ready','failed','requires_user_reupload')"
  - phase: 02-content-markdown-backfill
    provides: "Backfill writes content_markdown_status='ready'/'failed'/'requires_user_reupload' at row level"
provides:
  - "DocumentResponse.content_markdown_status exposed on the wire for every documents-returning endpoint (GET /api/folders, GET /api/files, POST /api/files/upload, PATCH /api/files/{id})"
affects: [06-05-folders-list-endpoint-extension, 06-08-status-badge-component, ui-08-status-badge]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pydantic response-model field add as the canonical fix for FastAPI silent-strip of DB columns"

key-files:
  created: []
  modified:
    - "backend/app/models/schemas.py"

key-decisions:
  - "Field placed immediately after metadata (between metadata and folder_path) to keep optional-string fields contiguous and match the existing DocumentResponse shape ordering"
  - "Typed Optional[str] = None (no validation/enum at the Pydantic layer) — the DB CHECK constraint in Migration 014 is the bedrock; Pydantic deliberately stays permissive so legacy rows with NULL pass through, and any new enum values added later via DROP/ADD CONSTRAINT do not require a coordinated Pydantic release"
  - "Zero router-code changes — folders/files routers already construct DocumentResponse via `**row` spread (or supabase-py .select('*') -> response_model auto-serialization), so adding the Pydantic field alone flips the column from 'silently stripped' to 'passed through' (D-03 condition verified by PATTERNS.md)"

patterns-established:
  - "Single-field add to a Pydantic response model as the minimal-diff fix when a DB column is present but absent from the wire response — diff stat: 1 file, 1 insertion, 0 deletions"
  - "Inline traceability comment `(Migration <NNN>; <D-NN>)` on Pydantic fields that originate from a specific migration + design decision — supports future migration audits"

requirements-completed: [UI-08]

# Metrics
duration: 1min
completed: 2026-05-11
---

# Phase 6 Plan 01: Expose content_markdown_status on DocumentResponse Summary

**Single-field Pydantic add (`content_markdown_status: Optional[str] = None`) flips the Migration-014 column from silently stripped to wire-serialized on every documents-returning endpoint, unblocking UI-08's StatusBadge.**

## Performance

- **Duration:** ~1 min
- **Started:** 2026-05-11T05:05:15Z
- **Completed:** 2026-05-11T05:06:00Z
- **Tasks:** 1
- **Files modified:** 1

## Accomplishments
- `DocumentResponse` Pydantic model now declares `content_markdown_status: Optional[str] = None` with traceability comment `(Migration 014; D-03)`
- All four documents-returning endpoints (`GET /api/folders`, `GET /api/files`, `POST /api/files/upload`, `PATCH /api/files/{id}`) now serialize the field — zero router-code changes required (response_model auto-serialization picks up the new field)
- Frontend consumers (Plans 06-05 / 06-08) can now rely on `document.content_markdown_status` being present in the JSON body (value: `"ready"` | `"pending"` | `"failed"` | `"requires_user_reupload"` | `null` for legacy rows)

## Task Commits

Each task was committed atomically:

1. **Task 1: Add content_markdown_status field to DocumentResponse** - `8009e97` (feat)

## Files Created/Modified
- `backend/app/models/schemas.py` - Added one line: `content_markdown_status: Optional[str] = None  # 'ready' | 'pending' | 'failed' | 'requires_user_reupload' (Migration 014; D-03)` inside the `DocumentResponse` class block, positioned between `metadata` and `folder_path`

## Decisions Made
- **Field placement between `metadata` and `folder_path`** — matches the plan's verbatim required-shape block; keeps optional-string fields contiguous; avoids reordering any existing field
- **No Pydantic-layer enum/Literal type** — kept as `Optional[str]` to match the existing `status: str` / `error_message: Optional[str]` style in the same model and to avoid coupling the Pydantic layer to the 4-element CHECK vocabulary (Migration 014 owns that vocabulary; a future DROP/ADD CONSTRAINT to add e.g. `'reindexing'` would otherwise require a coordinated backend release)
- **Inline traceability comment** — `(Migration 014; D-03)` tags the field back to the migration that added the column AND to the D-03 design decision in `06-CONTEXT.md` / PATTERNS.md, so future maintainers tracing why this field exists hit both source-of-truth documents in one grep

## Verification

Schema-level assertion (from plan's automated verify):
```
$ cd backend && venv/Scripts/python -c "from app.models.schemas import DocumentResponse; assert 'content_markdown_status' in DocumentResponse.model_fields, 'content_markdown_status missing from DocumentResponse'; print('OK: content_markdown_status field present'); print('annotation:', DocumentResponse.model_fields['content_markdown_status'].annotation)"
OK: content_markdown_status field present
annotation: typing.Optional[str]
```

Cross-schema regression check (no other Pydantic class broke):
```
$ cd backend && venv/Scripts/python -c "from app.models.schemas import DocumentResponse, FolderResponse, FilePatch, FolderPatch, FolderCreate, RenameFolderResponse; print('all imports OK')"
all imports OK
```

Diff stat (≤3 insertions / 0 deletions per acceptance criteria):
```
$ git diff --stat HEAD~1 backend/app/models/schemas.py
 backend/app/models/schemas.py | 1 +
 1 file changed, 1 insertion(+)
```

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- **Plan 06-05 (folders-list-endpoint extension)** can now consume `content_markdown_status` from the GET /api/folders response without any further backend change — the field is present on every `DocumentResponse` instance the endpoint emits
- **Plan 06-08 (StatusBadge component)** can drive its render branches off `document.content_markdown_status === 'pending' | 'failed' | 'requires_user_reupload'` with `'ready'` (or `null` for legacy) as the no-badge case
- No blockers introduced

## Self-Check: PASSED

Verification of claims:
- `backend/app/models/schemas.py` exists and contains `content_markdown_status` inside `DocumentResponse` (FOUND)
- Commit `8009e97` exists in `git log` (FOUND)
- No file deletions in the commit (verified — `git diff --diff-filter=D --name-only HEAD~1 HEAD` empty)

---
*Phase: 06-file-explorer-ui-cluster*
*Completed: 2026-05-11*
