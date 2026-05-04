# Phase 2: content_markdown Backfill (Gated) — Context

**Gathered:** 2026-05-04
**Status:** Ready for planning
**Source:** Inline decision capture (no /gsd-discuss-phase run; single Storage Gap question resolved at plan-time)

<domain>
## Phase Boundary

Phase 2 delivers the `documents.content_markdown` population pipeline, in two halves:

1. **New uploads (forward-looking):** `ingest_document()` captures Docling's canonical markdown export and writes it synchronously into `documents.content_markdown` in the same transaction as chunks/embeddings. Status flips to `'ready'` atomically.
2. **Existing rows (gated backfill):** `backend/scripts/backfill_content_markdown.py` re-runs Docling against the original Storage blob for any document with `content_markdown_status != 'ready'`. If the blob is missing (GC, Episode 1 row predating Storage capture), the row flips to `'requires_user_reupload'` and is surfaced in tool results — never silently skipped.

The "Gated" half: Phase 4 tools (`grep`, `read_document`) MUST honor the `content_markdown_status` field and return a structured `{status: 'pending_reindex', ...}` row instead of empty results when they encounter non-ready documents. Phase 4 cannot ship until this contract is locked here.

Phase 2 also closes a **foundational codebase gap** discovered during research: Supabase Storage was never wired up. Original blob bytes are discarded after the FastAPI request, leaving zero recoverable source for the Episode 1 corpus. Phase 2 adds the Storage upload path so all NEW uploads persist their blobs — making future re-indexing (this phase + any future re-Docling pass) actually possible.

</domain>

<decisions>
## Implementation Decisions

### LOCKED — Storage Gap Resolution (resolved 2026-05-04)
- **Add Supabase Storage upload now** (Option A from research §Decisions §2). Specifically:
  - Create a new bucket `documents` (private; service-role + per-user RLS read).
  - Migration 017 (the one the user is already carrying forward from Phase 1) is the pre-existing slot — DO NOT bundle Storage work into it; create a separate Migration 018 for Storage RLS / bucket setup if SQL is needed. Most Storage bucket work is done via the Supabase Storage API, not migration SQL — confirm during planning.
  - In `ingest_document()` (or `files.py` upload path — pick the location that holds the in-memory bytes immediately after the multipart parse), upload the original blob to `documents/{user_id}/{document_id}.{ext}` BEFORE Docling parsing begins (so even if Docling fails the blob is recoverable).
  - Storage path becomes a new column on `documents` (`storage_path TEXT`) OR is computed from `(user_id, id, original_filename)` — planner picks; recommend computed-from-id to avoid a migration.

### LOCKED — Episode 1 corpus disposition (user permission captured)
- **The user explicitly does NOT need the existing Episode 1 documents preserved.**
- The user explicitly permits a one-shot delete of Episode 1 documents that have NULL `content_markdown` AND no Storage blob.
- However, per CLAUDE.md rule ("Never run DELETE FROM or TRUNCATE on production tables; Never write migrations with DROP TABLE on tables that hold user data"), the cleanup MUST NOT live in a migration. Acceptable shapes:
  - **Recommended:** `backend/scripts/backfill_content_markdown.py --purge-orphans` flag — script-side, requires explicit operator invocation, prints affected rows + interactive confirmation before any DELETE.
  - **Alternative:** the script ALWAYS marks orphan rows as `requires_user_reupload` (BACKFILL-04 path) and a separate human-invoked SQL session deletes them later. (Cleaner separation; safer; recommended fallback if `--purge-orphans` adds too much complexity.)
- Cleanup of `document_chunks` for any deleted document is in scope (FK cascade if present, otherwise explicit two-step delete).

### LOCKED — Backfill scope reframe
- BACKFILL-02 success criterion 2's "every Episode 1 document with NULL `content_markdown`" is reframed to: **every document with `content_markdown_status != 'ready'` whose Storage blob is downloadable**. Rows whose blob is missing flip to `requires_user_reupload` (BACKFILL-04 path), never silently skipped, never error-blocking the backfill loop.
- Initial backfill run after Phase 2 ships will mark every Episode 1 row as `requires_user_reupload` (since none have Storage blobs). The orphan-purge flag (or the human-invoked DELETE pass) is the operator's tool for cleaning up those rows once the user has re-uploaded what they want to keep.

### LOCKED — Synchronous-on-upload (BACKFILL-01)
- The markdown export must happen INSIDE `ingest_document()` in the same transaction as the row write. NO background task. NO follow-up job.
- Insertion point: `backend/app/services/ingestion.py:437-439` (the existing UPDATE that flips `status` to `'ready'`). Add `content_markdown=text, content_markdown_status='ready'` to the same UPDATE.
- The `text` variable is already in scope (returned by `extract_text()` at lines 99/132). Zero re-extraction needed. ~10 lines of edits.
- Failure handling: if the markdown export string is empty/None for some pathological input, write `content_markdown=NULL, content_markdown_status='failed'` (NOT 'ready'). Chunks still get written (they came from a parallel path). BACKFILL-04 surfacing then applies.

### LOCKED — Status state machine (per Migration 014)
- 4 valid values: `'pending'`, `'ready'`, `'failed'`, `'requires_user_reupload'`.
- Transitions:
  - On synchronous upload success: `→ 'ready'`
  - On synchronous upload failure (Docling export returned empty): `→ 'failed'`
  - On backfill success: `→ 'ready'`
  - On backfill blob-missing: `→ 'requires_user_reupload'`
  - On backfill Docling exception: `→ 'failed'`
- Backfill re-run policy: the script processes any row with `status IN ('pending', 'failed')` by default. `requires_user_reupload` rows are SKIPPED (the operator must take the orphan-purge or re-upload action). A `--reset-failed` flag is OUT OF SCOPE this phase.

### LOCKED — Tool integration contract (gated half — Phase 4 will consume this)
- When Phase 4's `grep` or `read_document` encounters a row with `content_markdown_status != 'ready'`, it returns a row of shape:
  ```json
  {
    "document_id": "<uuid>",
    "file_name": "<original filename>",
    "scope": "user" | "global",
    "folder_path": "/<path>",
    "status": "pending_reindex",
    "content_markdown_status": "pending" | "failed" | "requires_user_reupload"
  }
  ```
- This row is counted toward the 50-hit cap of `grep` (so a corpus full of pending rows degrades gracefully).
- `tree`, `glob`, `list_files` are unaffected (they don't read `content_markdown`).
- Phase 4 plan-checker must enforce this shape exists in tool output schemas.

### LOCKED — Concurrency throttle
- The backfill script reuses the existing `_ingestion_semaphore` in `backend/app/services/ingestion.py` (or wherever it currently lives — planner verifies path).
- The script runs as a standalone process — it acquires its own semaphore instance with the same capacity (typically 2-4). It does NOT need to coordinate with the live API server's semaphore (different process, different limit), and that is acceptable (the backfill is offline-ish; if it competes with live uploads the live uploads win because they hold their own semaphore slots).

### LOCKED — Logging / observability
- Per-document structured log line: `document_id, file_name, status_before, status_after, duration_ms, error_class`.
- Aggregate summary at end: total processed / ready / requires_user_reupload / failed counts.
- LangSmith `@traceable` is OUT OF SCOPE for the backfill script (it's an offline batch tool, not an LLM call path). The synchronous-on-upload edit (BACKFILL-01) inherits whatever tracing `ingest_document()` already has.

### LOCKED — Forward-compatibility for Phases 3, 4, 6 (per user direction "make relevant changes for coming phases")
- **Phase 3 (Folder Service):** No direct dependency. Folder routes operate on `documents` rows; `content_markdown` is a separate column they don't touch.
- **Phase 4 (Tools):** Hard dependency. The tool integration contract above is the locked interface. Phase 4 plans will reference this CONTEXT.md.
- **Phase 6 (UI):** Soft dependency. The file explorer should render a small "needs re-index" badge when `content_markdown_status != 'ready'`. UI-08 already mentions `content_markdown_status` badge — this is the source of truth.
- **Test fixtures across Episodes:** Existing Episode 1 tests that assume `content_markdown IS NULL` is fine remain valid. New Phase 2+ tests that touch `documents` should set `content_markdown_status='ready'` explicitly.

### Claude's Discretion
- CLI argument shape (`argparse` vs. typer vs. click) — match existing `backend/scripts/run_migrations.py` convention.
- Whether to add a `--limit N` flag for chunked backfill runs (recommended yes, for operator safety on large corpora).
- Whether to add a `--document-id <uuid>` flag for spot-fixing one row (recommended yes, low cost).
- Exact log format (JSON vs. key=value). Match what `ingestion.py` uses today.
- Whether a `--dry-run` flag is added (recommended yes — prints what would change without writing).
- Whether to pin `docling==2.91.0` in `requirements.txt` (recommended yes, per researcher §Decisions §3).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase 2 research
- `.planning/phases/02-content-markdown-backfill-gated/02-RESEARCH.md` — Docling API surface, `extract_text()` location, the Storage Gap finding, status state machine, tool contract spec, validation architecture

### Foundational pitfall this phase mitigates
- `.planning/research/PITFALLS.md` — Pitfall 6 ("content_markdown backfill done wrong") — RANK 2 — chunk-stitching is the single forbidden implementation path

### Phase 1 dependencies (already shipped)
- `backend/migrations/014_*.sql` (or equivalent) — defines `content_markdown` TEXT + `content_markdown_status` enum + partial index `WHERE content_markdown_status <> 'ready'`
- `backend/migrations/015_*.sql` — RLS policies (backfill script uses service-role; doesn't hit RLS)
- `backend/migrations/016_*.sql` — search-acceleration indexes (downstream — Phase 4 reads, Phase 2 writes the data they index)
- `backend/app/services/folder_service.py` — `normalize_path()` (backfill doesn't write `folder_path` but if any code path does, it must use this)

### Existing ingestion code (the integration point)
- `backend/app/services/ingestion.py` — `ingest_document()` flow, `extract_text()` at L99/132, the UPDATE-to-ready at L437-439, `_ingestion_semaphore`
- `backend/app/services/record_manager.py` — dedup contract (backfill must NOT trigger re-chunking)
- `backend/app/routers/files.py` — multipart upload path (Storage upload insertion point)

### CLI script convention
- `backend/scripts/run_migrations.py` — argparse + service-role client + `if __name__ == '__main__'` shape
- `backend/scripts/test_helpers.py` — auth/cleanup utilities

### Codebase intel (note errata)
- `.planning/codebase/INTEGRATIONS.md` — claims Supabase Storage is in use. **This is a documentation error** — research found zero Storage calls in `backend/`. Treat the source code as authoritative.

</canonical_refs>

<specifics>
## Specific Ideas

- The Storage bucket name is `documents` (singular bucket; multi-tenant via path prefix `{user_id}/`).
- The Storage path convention: `{user_id}/{document_id}.{original_extension}`. This makes RLS trivial (`auth.uid()::text = (storage.foldername(name))[1]`).
- Service-role uploads happen from the FastAPI process; per-user reads are gated by Storage RLS so the frontend can fetch its own blobs if needed in Phase 6.
- The backfill script's success line for a single document should match (loosely): `[OK] doc=<uuid> file=<name> blob_size=<N> docling_ms=<N> markdown_chars=<N>`.
- The backfill script should NEVER touch `document_chunks` or `embeddings` — those came from the upload-time Docling pass; re-Docling for `content_markdown` produces the same chunk text but new chunks would invalidate vector IDs and break the record manager. Markdown-only update.
- The synchronous-on-upload edit must be ONE atomic UPDATE — if `content_markdown` write fails the row should not be left half-updated (status='ready' but content_markdown=NULL). Use a single SQL `UPDATE documents SET status='ready', content_markdown=$1, content_markdown_status='ready' WHERE id=$2` shape.

</specifics>

<deferred>
## Deferred Ideas

- `--reset-failed` flag (resets `failed` rows to `pending` so backfill retries them) — recommended NO this phase; manual SQL is fine for the rare case.
- LangSmith tracing of the backfill script — out of scope (offline tool, not an LLM call path).
- A separate Migration 018 for Storage RLS — only if Storage RLS requires SQL beyond what the Supabase Studio UI captures. Most Storage policy work is done via the Supabase Storage API; planner verifies and decides.
- Auto-cleanup of orphaned rows on script run (without `--purge-orphans` flag) — explicitly rejected; purges must be opt-in to preserve operator safety.
- Batch update of multiple rows in one transaction — per-row commits are simpler, idempotent, and preserve resumability after a crash. Out of scope.
- Versioning of `content_markdown` (e.g., a `content_markdown_version` column to track which Docling version produced it) — defer to a future re-index phase if Docling output changes meaningfully.

</deferred>

---

*Phase: 02-content-markdown-backfill-gated*
*Context captured: 2026-05-04 — single inline decision (Storage Gap: Option A) replaces a full /gsd-discuss-phase pass*
