---
phase: 02-content-markdown-backfill-gated
plan: 01
subsystem: storage

tags:
  - supabase-storage
  - rls
  - fastapi
  - upload-pipeline
  - storage3

# Dependency graph
requires:
  - phase: 01-schema-foundation
    provides: "Two-scope RLS catalog (migration 015) + perf-cached (SELECT auth.uid()) convention reused here"
provides:
  - "Original file blobs are persisted to Supabase Storage at upload time so future re-Docling can recover them"
  - "Per-user RLS on storage.objects (SELECT + INSERT) scoped to bucket_id='documents' AND auth.uid()'s own folder"
  - "Deterministic, computed-from-id Storage path formula consumed by Plan 03 backfill"
affects:
  - "02-02 (synchronous content_markdown write — unaffected, separate code path)"
  - "02-03 (backfill_content_markdown.py — uses identical path formula to download blobs)"
  - "02-04 (test_backfill.py — exercises end-to-end Storage round-trip)"
  - "06-file-explorer-ui (future: Phase 6 SELECT policy enables direct frontend blob fetch under user's own folder)"

# Tech tracking
tech-stack:
  added:
    - "supabase-py Storage SDK (storage3) — first use of supabase.storage.from_().upload() in this codebase"
  patterns:
    - "Computed-from-id Storage path: f\"{user_id}/{document_id}{ext}\" with ext = os.path.splitext(file_name)[1] — identical formula on upload (files.py) and download (Plan 03 backfill); avoids a documents.storage_path column"
    - "Non-fatal Storage upload (try/except + logger.warning) — extends the ingestion.py:407-408,444-450 'non-fatal' convention to a third site (Storage) so ingest reaches status='ready' even if Storage is unavailable"
    - "Per-user RLS on storage.objects via (storage.foldername(name))[1] — first storage.objects policy in the codebase; reuses the (SELECT auth.uid()) perf-cached idiom from migration 015"
    - "Bucket creation as a one-time Supabase Studio task documented in the migration header (bucket-level config — MIME allowlist, size limit — doesn't belong in DDL)"

key-files:
  created:
    - "backend/migrations/018_storage_rls.sql"
  modified:
    - "backend/app/routers/files.py"

key-decisions:
  - "Storage path is computed-from-id (no documents.storage_path column added) — Plan 03 reconstructs via the same os.path.splitext formula at lookup time (CONTEXT.md §LOCKED—Storage Gap directive 'recommend computed-from-id to avoid a migration')"
  - "Storage upload happens BEFORE background_tasks.add_task in BOTH the action='create' and action='update' branches of upload_file() — the blob lands first, so even if Docling later fails the original is recoverable"
  - "Storage failure is non-fatal (logger.warning + continue) — extends the existing ingestion.py 'non-fatal' convention; ingest still reaches status='ready'; Plan 03's backfill detects missing blobs and flips them to 'requires_user_reupload'"
  - "Migration 018 does NOT create the bucket — that is documented in the header as a one-time Supabase Studio task (Studio is canonical for bucket-level config; SQL is the wrong layer)"
  - "Migration 018 only adds SELECT + INSERT policies for the authenticated role — no UPDATE/DELETE for authenticated this phase (re-upload uses upsert=true which is INSERT semantics; service-role handles backend deletes)"

patterns-established:
  - "Storage upload helper pattern: factor try/except into a private _upload_to_storage(supabase, user_id, document_id, file_name, contents, mime_type) so both upload-paths share one non-fatal wrapper"
  - "Module-level imports + module-level logger in routers — the inline 'import logging' inside _throttled_ingest was promoted to module level; this convention applies to any router that needs structured logging"
  - "storage.objects RLS policy naming: <bucket>_storage_<operation>_<scope> (e.g., documents_storage_select_own) — extends the snake_case (table)_(operation)_(scope) convention from migration 015 to the storage schema"

requirements-completed: []

# Metrics
duration: ~5 min
completed: 2026-05-06
---

# Phase 2 Plan 01: Storage Gap Closure Summary

**Supabase Storage upload at upload-time + Migration 018 storage.objects RLS — preserves original blobs at documents/{user_id}/{doc_id}{ext} so Plan 03 backfill (and any future re-Docling pass) can recover them.**

## Performance

- **Duration:** ~5 min (active execution)
- **Started:** 2026-05-04T11:59:50Z
- **Completed:** 2026-05-06T06:51:12Z (session was paused between commits)
- **Tasks:** 2/2
- **Files modified:** 1
- **Files created:** 1

## Accomplishments

- Storage upload wired into both `action='create'` and `action='update'` branches of `upload_file()` — blob persists BEFORE the Docling background task is scheduled
- Migration 018 ships idempotent SELECT + INSERT RLS policies on `storage.objects` scoped to `bucket_id='documents'` AND the authenticated user's own folder
- Storage path formula `f"{user_id}/{doc['id']}{ext}"` (with `ext = os.path.splitext(file_name)[1]`) locked as the deterministic contract Plan 03's backfill will mirror
- Non-fatal failure mode preserved: Storage upload exceptions log a warning and the ingest path still reaches `status='ready'`

## Task Commits

Each task was committed atomically:

1. **Task 1: Add Supabase Storage upload to files.py upload_file() — both create and update branches** — `41e3eeb` (feat)
2. **Task 2: Write Migration 018 — RLS policies on storage.objects for the 'documents' bucket** — `e256c91` (feat)

**Plan metadata:** to be committed alongside this SUMMARY + STATE.md + ROADMAP.md updates.

## Files Created/Modified

- `backend/app/routers/files.py` — added module-level `os` + `logging` imports + module logger; added private helper `_upload_to_storage()` that uploads to `documents/{user_id}/{document_id}{ext}` with `upsert='true'` and logs failures as non-fatal warnings; wired the helper into both branches of `upload_file()` immediately BEFORE `background_tasks.add_task` (line 93–100 for the update branch, line 124–131 for the create branch)
- `backend/migrations/018_storage_rls.sql` — new migration with two RLS policies on `storage.objects` (`documents_storage_select_own`, `documents_storage_insert_own`), both `TO authenticated` and predicated on `bucket_id = 'documents' AND (SELECT auth.uid())::text = (storage.foldername(name))[1]`; header documents the one-time Supabase Studio bucket-creation task

## Decisions Made

None beyond what was already locked in 02-CONTEXT.md and 02-PATTERNS.md. The plan was executed exactly as written:
- Helper signature matches the plan: `(supabase, user_id, document_id, file_name, contents, mime_type)`
- Both call sites place the upload BEFORE `background_tasks.add_task`
- Storage path formula is `f"{user_id}/{document_id}{ext}"` with `ext = os.path.splitext(file_name)[1]` (no trailing dot for files without extension)
- The pre-existing inline `import logging` calls inside `_throttled_ingest` were cleaned up to use the new module-level `logger` (zero behavior change — the inline imports were resolving to the same logger; this is a tidy-up, not a deviation)
- Migration 018 is idempotent (`DROP POLICY IF EXISTS … then CREATE POLICY …`), uses the `(SELECT auth.uid())` perf-cached subquery from migration 015, and documents bucket creation as a one-time Studio task in the header

## Deviations from Plan

None — plan executed exactly as written. The single inline `import logging` cleanup inside `_throttled_ingest` is consistent with Step 2 of Task 1 ("Logger is module-level. No inline `import logging` inside `_upload_to_storage` (clean up the existing inline import in `_throttled_ingest` if it would otherwise become unused — leave it alone if removing it adds risk; the duplicate-import is benign)") — I removed the inline imports because the module-level `logger` was already imported and the cleanup is risk-free (same logger object, just looked up once at module import instead of twice per call).

## Issues Encountered

- During verification, `python -c "from app.routers.files import …"` raised `ValueError: No API key was provided` because `app/services/ingestion.py` instantiates the Gemini client at import time using `os.environ.get("GEMINI_API_KEY")`. This is a pre-existing import-side-effect outside this plan's scope — it does not block the application (FastAPI loads `.env` via dotenv at startup) and is not caused by this plan's changes. Workaround: load `.env` via `dotenv.load_dotenv()` before importing. The import succeeds with that workaround and acceptance criteria pass. Logging this as an observation, not a deviation, since (a) it's pre-existing and (b) the plan didn't require fixing it.

## User Setup Required

**One-time operator action required before Plan 03's backfill will work end-to-end:**

1. **Create the Supabase Storage bucket via Studio** — Storage → Create bucket → Name: `documents`, Public: OFF, File size limit: 50MB. (Equivalent SDK call from a Python REPL using the service-role key: `supabase.storage.create_bucket("documents", options={"public": False})`.) This is documented in the migration 018 header.
2. **Apply Migration 018 via `run_migrations.py`** — `cd backend && DATABASE_URL='postgresql://...' venv/Scripts/python scripts/run_migrations.py`. This installs the two RLS policies on `storage.objects` so authenticated users can SELECT + INSERT only inside their own folder.

Both steps are pre-requisites for Plan 04's integration test checkpoint.

## Next Phase Readiness

- Plan 02 (synchronous content_markdown write inside `ingest_document()`) is unblocked — independent code path, no dependency on this plan's Storage code
- Plan 03 (backfill CLI) is unblocked at the contract level — the deterministic Storage path formula is now committed; backfill will compute the identical path on download
- Plan 04 (integration test suite) has a human-verify checkpoint that requires the operator to have completed the User Setup Required steps above before the test suite can pass

## Self-Check: PASSED

- File `backend/app/routers/files.py` exists and parses cleanly: FOUND
- File `backend/migrations/018_storage_rls.sql` exists: FOUND
- Commit `41e3eeb` exists in git log: FOUND
- Commit `e256c91` exists in git log: FOUND
- Helper `_upload_to_storage` defined and called in both create + update branches: FOUND
- Storage upload uses `upsert='true'` and `os.path.splitext`: FOUND
- Migration 018 contains both policy names and is idempotent: FOUND
- No `documents.storage_path` column written (computed-from-id contract): VERIFIED (zero `supabase.table('documents').*storage_path` matches)
- No `INSERT INTO storage.buckets` in migration: VERIFIED
- No destructive SQL (`DROP TABLE`, `TRUNCATE`, `DELETE FROM`) in migration: VERIFIED
- Min-line gates: files.py 165 lines (≥150), migration 018 58 lines (≥25): PASS

---

*Phase: 02-content-markdown-backfill-gated*
*Plan: 01 — Storage Gap closure*
*Completed: 2026-05-06*
