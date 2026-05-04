# Phase 2: content_markdown Backfill (Gated) — Pattern Map

**Mapped:** 2026-05-04
**Phase:** 02-content-markdown-backfill-gated
**Files analyzed:** 7 (3 created, 4 modified)
**Analogs found:** 5 / 7 (one new pattern: Storage upload; one verify-only: requirements.txt)

This map identifies, per planned file, the closest existing analog in the codebase and extracts paste-ready code excerpts for the planner. Two patterns are flagged as **NEW PATTERNS** (no codebase precedent): the Supabase Storage upload (Storage Gap finding from RESEARCH.md §2) and `argparse`-based CLI scripts (existing scripts use env-vars only).

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `backend/scripts/backfill_content_markdown.py` | script (CLI / batch) | batch transform + DB CRUD | `backend/scripts/run_migrations.py` (CLI shape) + `backend/app/services/ingestion.py` (Docling reuse) | role-match (no batch script with argparse exists) |
| `backend/scripts/test_backfill.py` | test (integration) | request-response + DB read-back | `backend/scripts/test_files.py` (upload + poll + assertion) + `backend/scripts/test_two_scope_rls.py` (direct supabase + scoped cleanup) | exact (test_files.py) |
| `backend/migrations/018_storage_rls.sql` | migration | DDL — DROP/CREATE policy | `backend/migrations/015_two_scope_rls.sql` (policy block syntax) | role-match (no Storage RLS analog; conditional file — see "No Analog" §) |
| `backend/app/services/ingestion.py` (modify) | service | file I/O + DB UPDATE | self (lines 437-439 — extend existing UPDATE) | exact (in-place edit) |
| `backend/app/routers/files.py` (possibly modify) | controller | request-response + multipart parse | self (line 37 — `await file.read()` is the bytes capture point) | exact (Storage upload insertion candidate) |
| `backend/requirements.txt` (modify) | config | n/a | self (line 10 — `docling` → `docling==2.91.0`) | exact (version pin only) |
| `backend/app/services/record_manager.py` (verify) | service | n/a | self (must NOT change in Phase 2) | exact (read-only verification) |

---

## Pattern Assignments

### `backend/scripts/backfill_content_markdown.py` (script, batch transform)

**Primary analog:** `backend/scripts/run_migrations.py`
**Secondary analog:** `backend/app/services/ingestion.py` (for Docling reuse)
**Tertiary analog:** `backend/scripts/verify_phase1_schema.py` (for env-var + service-role + exit-code shape)

**Module docstring + imports pattern** (copy from `run_migrations.py:1-20`):

```python
"""Run all SQL migrations in backend/migrations/ against a Supabase Postgres database.

Usage:
    DATABASE_URL='postgresql://...' venv/Scripts/python scripts/run_migrations.py

Get DATABASE_URL from Supabase: Project Settings -> Database -> Connection string -> URI.
Use the "Direct connection" string (port 5432), not the pooler — DDL works reliably on direct.

Each migration runs in its own transaction. On failure, the failing migration rolls back
and the script stops; later migrations are not attempted.
"""
import os
import sys
from pathlib import Path

import psycopg2

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"
```

**Convention to copy:** triple-quoted module docstring with explicit `Usage:` block including the exact `venv/Scripts/python scripts/...` Windows invocation, env-var requirements documented in the docstring, `Path(__file__).parent.parent` for finding the script's relative dirs, all imports stdlib-then-third-party.

**Service-role client instantiation pattern** (copy from `backend/app/auth.py:8-12`):

```python
def get_supabase_client():
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )
```

**Apply to backfill:** Use `create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)` directly at top of `main()`. RLS is bypassed (per CONCERNS.md anti-pattern); backfill is admin-operational so this is acceptable. Do NOT import from `app.auth` — keeps the script as a standalone CLI without FastAPI dependency.

**`if __name__ == '__main__'` + exit-code pattern** (copy from `run_migrations.py:62-66` AND `verify_phase1_schema.py:97-98`):

```python
def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL env var not set", file=sys.stderr)
        print("Get it from Supabase: Settings -> Database -> Connection string -> URI", file=sys.stderr)
        return 1
    # ...
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**Convention to copy:** `main()` returns `int` exit code (0=success, 1=usage/config error, 2=runtime error). `print(..., file=sys.stderr)` for errors. `sys.exit(main())` at module bottom. **Codebase has no `2`-vs-`1` distinction beyond that — match `run_migrations.py:57` which uses `return 2` for migration-runtime failures**; backfill should mirror: 0=clean, 1=missing env / no rows to process / dry-run "what-if", 2=Docling/Storage exception during run.

**Per-row processing log line pattern** (copy from `run_migrations.py:48-56` AND `ingestion.py:440`):

```python
# run_migrations.py:48-56 — single-line print with status suffix
print(f"RUN  {f.name} ... ", end="", flush=True)
try:
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    print("OK")
except Exception as e:
    conn.rollback()
    print(f"FAIL\n  {type(e).__name__}: {e}")
    return 2

# ingestion.py:440 — module-logger line with structured fields
logger.info(f"Ingested document {document_id}: {len(chunks)} chunks")
```

**Apply to backfill:** Per CONTEXT.md §LOCKED—Logging, emit a structured log line per document: `document_id, file_name, status_before, status_after, duration_ms, error_class`. Use module-level `logger = logging.getLogger(__name__)` (matches `ingestion.py:19`). Format suggestion (matching `ingestion.py` style):
```
logger.info(f"[OK] doc={document_id} file={file_name} blob_size={n} docling_ms={t} markdown_chars={len(md)}")
```
End-of-run summary uses bare `print()` to stdout (matches `run_migrations.py:61` and `verify_phase1_schema.py:91-93`).

**Docling reuse pattern** (call existing helper, NEVER reimplement) — from `ingestion.py:62-142`:

```python
# ingestion.py:62 — extract_text() already handles ALL formats Episode 1 supports
def extract_text(file_content: bytes, mime_type: str, file_name: str) -> str:
    ext = os.path.splitext(file_name)[1].lower()
    # ... PPTX→PDF conversion, OCR-enabled PDF parsing, plain-text fallbacks ...
    # Returns the markdown export string from result.document.export_to_markdown()
```

**Apply to backfill:** Per RESEARCH.md §Standard Stack §Alternatives, the recommendation is **direct call to `extract_text()` from the backfill script** — same args, same return type, same Docling options. Do NOT write a new `extract_markdown_only()` helper; reuse maximizes byte-equivalence with synchronous-on-upload path (Phase 2 success criterion 4). Import via `from app.services.ingestion import extract_text` (the script must add `backend/` to sys.path — matches `test_two_scope_rls.py:34`).

**Semaphore-throttle pattern** (copy from `backend/app/routers/files.py:11-27`):

```python
import threading

_ingestion_semaphore = threading.Semaphore(2)


def _throttled_ingest(func, *args, **kwargs):
    acquired = _ingestion_semaphore.acquire(timeout=300)
    try:
        if not acquired:
            import logging
            logging.getLogger(__name__).error("Ingestion queue full — skipping")
            return
        func(*args, **kwargs)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Ingestion crashed: {e}", exc_info=True)
    finally:
        if acquired:
            _ingestion_semaphore.release()
```

**Apply to backfill:** Per CONTEXT.md §LOCKED—Concurrency throttle, the script reuses the **same Semaphore(2) capacity** (NOT the same instance — different process). Define `_backfill_semaphore = threading.Semaphore(2)` at module top; wrap the per-row Docling call in the same acquire/try/finally/release pattern. The 300-second timeout is appropriate for OCR-heavy PDFs (matches `files.py:15`).

**Argparse pattern — NEW (no codebase precedent):**

CONTEXT.md §Claude's Discretion explicitly accepts argparse. RESEARCH.md §Standard Stack notes "no project precedent for argparse in scripts (run_migrations.py uses env vars only)." This makes argparse a NEW pattern. Use the stdlib defaults (no third-party `click`/`typer`). Required flags per CONTEXT.md:

```python
import argparse

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill documents.content_markdown for existing Episode 1 documents.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would change without writing.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N rows (operator safety on large corpora).")
    parser.add_argument("--document-id", type=str, default=None,
                        help="Spot-fix a single row by UUID.")
    parser.add_argument("--purge-orphans", action="store_true",
                        help="Interactive: delete rows whose blob is missing AND content_markdown IS NULL. "
                             "Prints affected rows + asks for explicit y/N before any DELETE.")
    args = parser.parse_args()
    # ... rest of script ...
```

**Convention notes:**
- Match `run_migrations.py`'s env-var pattern for SECRETS (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY) — never CLI-flag a secret.
- Match `--dry-run` behavior to "print what WOULD change, exit 0" (Unix idiom).
- For `--purge-orphans`, use `input()` for confirmation; require literal `y` or `yes` to proceed; CLAUDE.md "tests must NEVER delete all user data" applies to production scripts too — interactive gate is mandatory.

---

### `backend/scripts/test_backfill.py` (test, integration)

**Primary analog:** `backend/scripts/test_files.py` (upload + poll + assertion shape)
**Secondary analog:** `backend/scripts/test_two_scope_rls.py` (direct supabase client + scoped cleanup tracking)

**Module docstring + sys.path + helper-import pattern** (copy from `test_files.py:1-7`):

```python
"""File upload, ingestion polling, delete, and record manager dedup tests."""
import sys
import os
import requests

sys.path.insert(0, os.path.dirname(__file__))
import test_helpers as h

FAKE_ID = "00000000-0000-0000-0000-000000000000"
```

**Apply to test_backfill:** Match the exact shape — short docstring, `sys.path.insert(0, os.path.dirname(__file__))` for `test_helpers` import, alias as `h`. **Also add** `sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))` (matches `test_two_scope_rls.py:34`) so the test can import `extract_text` from `app.services.ingestion` and the backfill module's helpers.

**Test runner shape** (copy from `test_files.py:20-146`):

```python
def run():
    h.reset_counters()
    token = h.get_auth_token()
    headers = h.auth_headers(token)

    doc_id = None
    # ... track all created IDs in local vars; cleanup in finally ...

    try:
        h.section("File Upload")
        r = requests.post(
            f"{h.BASE_URL}/api/files/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("capybara_facts.txt", CAPYBARA_TEXT, "text/plain")},
        )
        h.test("Upload txt returns 200", r.status_code == 200, f"status={r.status_code}")
        # ... assertions via h.test(name, condition, detail) ...

    finally:
        if doc_id:
            requests.delete(f"{h.BASE_URL}/api/files/{doc_id}", headers=headers)

    return h.passed, h.failed


if __name__ == "__main__":
    run()
    sys.exit(h.summary())
```

**Convention to copy:**
- `run()` returns `(passed, failed)` tuple (consumed by `test_all.py:54`).
- `h.section(title)` for visual grouping; `h.test(name, condition, detail)` for assertions.
- Local-variable ID tracking (NEVER bulk-delete); `try/finally` with conditional `if doc_id: requests.delete(...)`.
- Bottom `if __name__ == "__main__":` runs the suite + `sys.exit(h.summary())`.

**Scoped-cleanup pattern for direct-Supabase tests** (copy from `test_two_scope_rls.py:39-77`):

```python
# Tracking lists for cleanup. Each list holds tuples of (id, client_for_cleanup).
# CLAUDE.md: tests must NEVER delete all user data — only tracked resources.
_tracked_documents = []   # list[(doc_id, sb_client)]


def _track_doc(doc_id, sb_client):
    _tracked_documents.append((doc_id, sb_client))


def _cleanup():
    """Delete ONLY tracked resources. Per CLAUDE.md: never bulk-delete."""
    for did, client in _tracked_documents:
        try:
            client.table("documents").delete().eq("id", did).execute()
        except Exception:
            pass
    _tracked_documents.clear()
```

**Apply to test_backfill:** Use this pattern when the test inserts test fixture documents directly via `supabase` (bypassing the upload API) to isolate the backfill code under test. Tests in this phase will need to:
1. Insert a fixture document with `content_markdown_status='pending'` (direct supabase write).
2. Optionally upload a real blob to Storage (for the success path).
3. Call backfill (in-process: import and run; OR subprocess: `subprocess.run(['venv/Scripts/python', 'scripts/backfill_content_markdown.py', '--document-id', test_id])`).
4. Assert post-state via `supabase.table('documents').select('content_markdown, content_markdown_status').eq('id', test_id).execute()`.
5. Cleanup via `_cleanup()`.

**Required test cases (per CONTEXT.md decision matrix):**

| Test | Pattern source | What to verify |
|------|----------------|----------------|
| Synchronous-on-upload writes content_markdown=ready | `test_files.py:30-54` (upload + poll) | After `poll_document_status(target='ready')`, `documents.content_markdown` is non-empty AND `content_markdown_status='ready'` |
| Synchronous-on-upload empty extraction → status='failed' | `test_files.py:30-54` (upload non-text fixture that Docling can't parse) | `content_markdown_status='failed'`, `content_markdown IS NULL`, `status='failed'` |
| Backfill success path (existing row + blob present) | `test_two_scope_rls.py:39-77` (direct insert) + subprocess invocation | Pre-fixture: insert doc with `content_markdown_status='pending'` + Storage blob. Run backfill. Assert: `status='ready'`, `content_markdown` populated. |
| Backfill missing-blob path → `requires_user_reupload` | Same as above, but DON'T upload blob | Assert: `content_markdown_status='requires_user_reupload'`, no exception escapes the script. |
| Backfill idempotency | Same fixture as success path | Run backfill twice. Second run is a no-op (skips rows where status='ready'); assert second-run summary shows `processed=0`. |
| Byte-equivalence spot-check (BACKFILL-02 success criterion 4) | Read both: synchronous-on-upload markdown for doc X, then backfill-rerun markdown for same blob | Two strings byte-equal (mathematically true if both call `extract_text()` with the same args + same Docling version). |

**Register in test_all.py:** Match the `SUITES` list shape from `test_all.py:26-40`:
```python
import test_backfill
# ...
SUITES = [
    # ... existing entries ...
    ("Backfill", test_backfill),  # Phase 2 — content_markdown synchronous + backfill
]
```

---

### `backend/migrations/018_storage_rls.sql` (CONDITIONAL — only if Storage RLS requires SQL)

**Analog:** `backend/migrations/015_two_scope_rls.sql`

**CONTEXT.md §LOCKED—Storage Gap explicitly says:** *"Most Storage bucket work is done via the Supabase Storage API, not migration SQL — confirm during planning."* Pattern-mapper finding: **Supabase Storage RLS uses the `storage.objects` table** which IS a regular Postgres table with RLS — but bucket creation (`INSERT INTO storage.buckets`) and bucket-level metadata is typically handled via Supabase Studio UI or the Storage API, NOT SQL migrations. **However**, the per-user RLS policies on `storage.objects` (the "users can read their own folder" policy) ARE expressible as SQL and SHOULD be in a migration for reproducibility.

**If creating Migration 018, use this header + DROP/CREATE policy shape** (copy from `015_two_scope_rls.sql:1-22, 57-86`):

```sql
-- Phase 2 / Migration 018: Supabase Storage RLS for the 'documents' bucket.
-- Policies on storage.objects gate per-user read of original blobs uploaded by
-- ingest_document() (Phase 2 Storage Gap mitigation).
--
-- Bucket creation (`INSERT INTO storage.buckets ('documents', 'documents', false)`)
-- is performed via Supabase Studio UI as a one-time admin task; this migration
-- only handles the RLS policies which MUST be in version control.
--
-- Path convention: documents/{user_id}/{document_id}.{ext}
-- The user_id sits at storage.foldername(name)[1], enabling the RLS pattern below.

-- Idempotent: drop-then-create matches the convention from migration 015:60-68.
DROP POLICY IF EXISTS "documents_storage_select_own" ON storage.objects;
CREATE POLICY "documents_storage_select_own"
  ON storage.objects FOR SELECT
  TO authenticated
  USING (
    bucket_id = 'documents'
    AND auth.uid()::text = (storage.foldername(name))[1]
  );

DROP POLICY IF EXISTS "documents_storage_insert_own" ON storage.objects;
CREATE POLICY "documents_storage_insert_own"
  ON storage.objects FOR INSERT
  TO authenticated
  WITH CHECK (
    bucket_id = 'documents'
    AND auth.uid()::text = (storage.foldername(name))[1]
  );

-- Service-role bypasses RLS automatically (used by backfill_content_markdown.py
-- and by ingest_document() during synchronous upload). No explicit grant needed.
```

**Convention to copy from `015_two_scope_rls.sql`:**
- Header comment block explains migration purpose, references CONTEXT.md and the specific Pitfall mitigated.
- Naming pattern: `<table>_<operation>_<scope>` (e.g., `documents_storage_select_own`).
- `DROP POLICY IF EXISTS ... ON ...` immediately before `CREATE POLICY` (idempotent re-run).
- `TO authenticated` role qualifier (matches every policy in 015).
- `USING (...)` for SELECT/UPDATE/DELETE; `WITH CHECK (...)` for INSERT.
- `(SELECT auth.uid())` is the perf-cached pattern from 015:13 — prefer over bare `auth.uid()` for hot tables; for storage.objects which is hit per-blob-fetch this matters.

**Planner decision:** If Supabase Studio UI is used for the bucket creation AND the project's deployment story for Storage policies is "click-through in Studio," then this migration may be SKIPPED entirely. Document the decision in Phase 2's PLAN — either ship 018 with the RLS policies above, OR document that Storage policies live in Studio (and add a verification step to the test suite that confirms the policies exist).

---

### `backend/app/services/ingestion.py` (MODIFY — synchronous-on-upload write)

**Analog:** self (this is an in-place edit — extend the existing UPDATE)

**Existing pattern at lines 437-439** (the exact insertion point per CONTEXT.md §LOCKED—Synchronous-on-upload):

```python
# ingestion.py:436-440 — current state
file_hash = compute_file_hash(file_content)
supabase_client.table("documents").update(
    {"status": "ready", "content_hash": file_hash, "updated_at": "now()"}
).eq("id", document_id).execute()
logger.info(f"Ingested document {document_id}: {len(chunks)} chunks")
```

**Edit pattern to apply** (per RESEARCH.md §Pattern 1 paste-ready):

```python
file_hash = compute_file_hash(file_content)
supabase_client.table("documents").update({
    "status": "ready",
    "content_hash": file_hash,
    "content_markdown": text,                  # NEW (BACKFILL-01)
    "content_markdown_status": "ready",        # NEW (BACKFILL-01)
    "updated_at": "now()",
}).eq("id", document_id).execute()
logger.info(
    f"Ingested document {document_id}: {len(chunks)} chunks, "
    f"{len(text)} markdown chars"
)
```

**Convention notes:**
- Atomic single-UPDATE per CONTEXT.md §specifics: status/markdown/markdown_status flip together. **Do NOT split into two UPDATEs** — that risks half-updated rows where `status='ready' AND content_markdown_status='pending'`.
- `text` is already in scope from `ingestion.py:395` (`text = extract_text(file_content, mime_type, file_name)`). Zero re-extraction.
- The `if not text.strip(): raise ValueError(...)` guard at L396-397 already handles the "no extractable text" case → falls into `except` block → status='failed' (so we just need to ALSO write `content_markdown_status='failed'` in the except block).

**Existing except-block pattern at lines 442-450** (extend with `content_markdown_status='failed'`):

```python
# ingestion.py:442-450 — current state
except Exception as e:
    error_msg = str(e)
    logger.error(f"Ingestion failed for document {document_id}: {error_msg}")
    try:
        supabase_client.table("documents").update(
            {"status": "failed", "error_message": error_msg, "updated_at": "now()"}
        ).eq("id", document_id).execute()
    except Exception as inner_e:
        logger.error(f"Could not update failed status: {inner_e}")
```

**Edit:** Add `"content_markdown_status": "failed",` to the dict so Phase 4 tools surface it via the `pending_reindex` row contract:

```python
supabase_client.table("documents").update({
    "status": "failed",
    "content_markdown_status": "failed",       # NEW (BACKFILL-04 surfacing)
    "error_message": error_msg,
    "updated_at": "now()",
}).eq("id", document_id).execute()
```

**Same edit applies to `ingest_document_update()` at L512-515 and L520-524** — identical shape, identical edits. RESEARCH.md §Pattern 1 explicitly directs: *"Apply the same edit to ingest_document_update() at ingestion.py:453-526."*

---

### `backend/app/routers/files.py` (POSSIBLY MODIFY — Storage upload insertion point)

**Analog:** self (the bytes are captured at line 37; storage upload inserts before `background_tasks.add_task`)

**Existing pattern at lines 30-37** (the multipart-parse + bytes-capture point):

```python
@router.post("/upload", response_model=DocumentResponse)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user),
):
    supabase = get_supabase_client()
    contents = await file.read()
    file_name = file.filename or "unnamed"
    mime_type = file.content_type or "application/octet-stream"
```

**Insertion point for Storage upload** — between `contents = await file.read()` (L37) and `_throttled_ingest` background task scheduling (L85-94). Per CONTEXT.md §LOCKED—Storage Gap: *"upload the original blob to documents/{user_id}/{document_id}.{ext} BEFORE Docling parsing begins (so even if Docling fails the blob is recoverable)."*

**NEW PATTERN — Supabase Storage upload (no codebase precedent):**

The Storage upload is an entirely NEW pattern — there are zero `storage.from_()` or `storage_path` calls anywhere in `backend/`. Pattern-mapper verified via two greps (both returned "No matches found"). Reference the `supabase-py` Storage SDK directly. Verified signature from `backend/venv/Lib/site-packages/storage3/_sync/file_api.py:574`:

```python
def upload(
    self,
    path: str,
    file: Union[BufferedReader, bytes, FileIO, str, Path],
    file_options: Optional[FileOptions] = None,
) -> UploadResponse:
```

**Recommended insertion shape** (after the `action == "create"` doc-insert at L77-83 so `doc["id"]` is known; AND mirror at the `action == "update"` branch at L51-58):

```python
# action == "create": new document — insert row first, THEN upload blob, THEN schedule ingest.
doc = supabase.table("documents").insert({
    "user_id": user_id,
    "file_name": file_name,
    "file_size": len(contents),
    "mime_type": mime_type,
    "status": "pending",
}).execute().data[0]

# Storage upload — original blob retained at documents/{user_id}/{document_id}.{ext}
# enables Phase 2 backfill (re-run Docling) and any future re-index pass.
ext = os.path.splitext(file_name)[1]  # includes leading dot, e.g. '.pdf'
storage_path = f"{user_id}/{doc['id']}{ext}"
try:
    supabase.storage.from_("documents").upload(
        storage_path,
        contents,
        file_options={"content-type": mime_type, "upsert": "true"},
    )
except Exception as e:
    import logging
    logging.getLogger(__name__).warning(
        f"Storage upload failed for {doc['id']} (non-fatal — ingestion still proceeds): {e}"
    )

background_tasks.add_task(
    _throttled_ingest,
    ingest_document,
    document_id=doc["id"],
    file_content=contents,
    mime_type=mime_type,
    file_name=file_name,
    user_id=user_id,
    supabase_client=supabase,
)
doc["action"] = "created"
return doc
```

**Convention notes:**
- Storage upload failure is **non-fatal** — log and continue. The ingest path doesn't require Storage; only future backfill does. This matches the existing "non-fatal" pattern from `ingestion.py:407-408` (`logger.warning(f"Metadata extraction failed (non-fatal)...")`).
- Path convention is `{user_id}/{document_id}{ext}` per CONTEXT.md §specifics. NOT `{user_id}/{document_id}.{ext}` — the extension already includes the dot via `os.path.splitext`.
- `upsert=true` makes the upload idempotent (re-uploads to the same path overwrite). Important for the `action == "update"` branch where the same path may be uploaded again with new content.
- The backfill script downloads via the inverse: `supabase.storage.from_("documents").download(storage_path)` — see `storage3/_sync/file_api.py:459` for signature.
- **Storage path is NOT persisted as a column** per CONTEXT.md §LOCKED—Storage Gap: *"recommend computed-from-id to avoid a migration."* The backfill script computes the path from `(user_id, id, file_name)` at lookup time — same formula as the upload above.

**Planner decision:** Per CONTEXT.md the upload may live EITHER in `files.py` (recommended — bytes are immediately in-scope here) OR be passed to `ingest_document()` and uploaded there. Pattern-mapper recommends **`files.py`** because:
1. Bytes are already in `contents` here — no parameter passing needed.
2. The doc row is created here (so `doc['id']` is available for the path).
3. CONTEXT.md explicit guidance: *"pick the location that holds the in-memory bytes immediately after the multipart parse"* — this is exactly that location.
4. Keeps `ingestion.py` focused on parse/chunk/embed without a new external dependency on Storage.

---

### `backend/requirements.txt` (MODIFY — version pin)

**Analog:** self (line 10)

**Existing pattern** — every dep is unpinned:

```
fastapi
uvicorn[standard]
python-dotenv
pydantic
google-genai
supabase
langsmith
sse-starlette
python-multipart
docling
cohere
duckdb
tavily-python
```

**Edit** (per CONTEXT.md §Claude's Discretion + RESEARCH.md §Standard Stack):

```
docling==2.91.0
```

**Convention note:** This is the **first version pin in the file**. The other 12 deps remain unpinned. RESEARCH.md verified `docling==2.91.0` is the currently-installed version (`pip show docling`); pinning preserves byte-equivalence between synchronous-on-upload markdown and backfilled markdown (Phase 2 success criterion 4 depends on this — different Docling versions produce different markdown for the same input). Do NOT pin other dependencies in this PR — that's a separate concern outside Phase 2 scope.

---

### `backend/app/services/record_manager.py` (VERIFY — must NOT change)

**Analog:** self

CONTEXT.md §canonical_refs explicitly states: *"backfill must NOT trigger re-chunking"* and CONTEXT.md §specifics: *"The backfill script should NEVER touch document_chunks or embeddings."* This file MUST remain unmodified in Phase 2. Phase 3's `record_manager` extension (folder-aware dedup key) is a separate phase.

**Verification step for the planner:** Add a checklist item to Phase 2's plan: "Confirm `git diff backend/app/services/record_manager.py` is empty before merging Phase 2."

---

## Shared Patterns

These cross-cutting patterns apply to multiple Phase 2 files.

### Logging
**Source:** `backend/app/services/ingestion.py:19`
**Apply to:** Both new files (`backfill_content_markdown.py`, `test_backfill.py`) AND the modified `ingestion.py` edits.

```python
import logging
logger = logging.getLogger(__name__)

# usage:
logger.info(f"Ingested document {document_id}: {len(chunks)} chunks")
logger.warning(f"Metadata extraction failed (non-fatal) for {document_id}: {e}")
logger.error(f"Ingestion failed for document {document_id}: {error_msg}")
```

**Convention:** module-level logger, f-strings with structured fields (id, file_name, counts, durations), severity matched to user-impact: `info` = normal flow, `warning` = recoverable / non-fatal, `error` = unrecoverable / data-affecting.

### Error handling — non-fatal pattern
**Source:** `backend/app/services/ingestion.py:407-408, 413-414, 444-450`
**Apply to:** Storage upload in `files.py`, metadata edits in backfill script.

```python
try:
    # ... operation that may fail but shouldn't block the main flow ...
except Exception as e:
    logger.warning(f"<operation> failed (non-fatal) for {document_id}: {e}")
```

**Convention:** wrap **every** auxiliary operation (Storage upload, structured-data extraction, metadata extraction) in this shape. Only the core happy path raises; everything else degrades gracefully.

### Service-role client pattern
**Source:** `backend/app/auth.py:8-12`
**Apply to:** `backfill_content_markdown.py` (script context — no FastAPI dependency).

```python
from supabase import create_client

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)
```

**Convention:** Direct `os.environ[...]` access (KeyError raises at startup if missing — fail-fast). Service-role bypasses RLS, which is required for cross-user backfill but is also the existing anti-pattern from CONCERNS.md. **Defense in depth:** even though service-role bypasses RLS, the backfill script MUST `.neq('content_markdown_status', 'ready')` to scope its writes (per RESEARCH.md §Project Constraints).

### CLAUDE.md scoped-cleanup rule
**Source:** `backend/scripts/test_two_scope_rls.py:39-77`
**Apply to:** `test_backfill.py` AND the backfill script's `--purge-orphans` flag.

The CLAUDE.md rule *"Tests must NEVER delete all user data"* extends in spirit to production scripts. The `--purge-orphans` flag MUST:
1. SELECT the candidate rows first.
2. Print them in a human-readable table.
3. Require interactive `input()` confirmation (literal `y`/`yes`).
4. DELETE only the previously-printed IDs (no `DELETE WHERE ...` blanket queries).

```python
# Pseudocode for --purge-orphans (NEW pattern but bound by CLAUDE.md rule):
candidates = supabase.table("documents") \
    .select("id, file_name, user_id, created_at") \
    .eq("content_markdown_status", "requires_user_reupload") \
    .is_("content_markdown", "null") \
    .execute().data

print(f"\nFound {len(candidates)} orphan document(s) (no Storage blob, NULL content_markdown):")
for d in candidates:
    print(f"  {d['id']}  user={d['user_id']}  file={d['file_name']}  created={d['created_at']}")

if not args.dry_run:
    answer = input(f"\nDELETE these {len(candidates)} rows + their chunks? [y/N]: ").strip().lower()
    if answer not in ("y", "yes"):
        print("Aborted.")
        return 0
    for d in candidates:
        supabase.table("document_chunks").delete().eq("document_id", d["id"]).execute()
        supabase.table("documents").delete().eq("id", d["id"]).execute()
        print(f"  deleted: {d['id']}")
```

### sys.path bootstrap for scripts
**Source:** `backend/scripts/test_two_scope_rls.py:32-37`
**Apply to:** `backfill_content_markdown.py` AND `test_backfill.py` (both need to import from `app.services`).

```python
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))                             # for test_helpers
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))    # for app.*

import test_helpers as h
from app.services.ingestion import extract_text
from app.services.folder_service import normalize_path  # if needed by tool contract
```

**Convention:** Two `sys.path.insert(0, ...)` calls — first puts `scripts/` on path (sibling test imports), second puts `backend/` on path (so `app.services.X` resolves). Do this BEFORE any `from app...` imports.

---

## No Analog Found

| File / Pattern | Reason | Replacement |
|----------------|--------|-------------|
| Supabase Storage upload (`storage.from_().upload()`) | Verified zero existing Storage calls in `backend/` (RESEARCH.md §Storage Gap finding). INTEGRATIONS.md erroneously claims Storage is in use — confirmed wrong. | External reference: `supabase-py` Storage SDK. Verified signature in `backend/venv/Lib/site-packages/storage3/_sync/file_api.py:574`. Pattern excerpt provided in `files.py` section above. |
| `argparse`-based CLI | Existing scripts (`run_migrations.py`, `verify_phase1_schema.py`) use env-vars only. CONTEXT.md §Claude's Discretion accepts argparse. | Stdlib `argparse` — pattern excerpt in `backfill_content_markdown.py` section above. Match Unix idioms (`--dry-run`, `--limit N`, `--document-id UUID`, interactive `--purge-orphans`). |
| Storage RLS migration | No existing migration touches `storage.objects`. Migration 015 is the closest analog for policy syntax (NOT for Storage specifically). | Use the policy-block shape from `015_two_scope_rls.sql:73-86` adapted for `storage.objects` + `bucket_id='documents'` + `(storage.foldername(name))[1]`. Pattern excerpt in `018_storage_rls.sql` section above. **Conditional file** — may not ship if Studio handles policies. |

---

## Phase 2 → Phase 4 Forward Contract (locked here, consumed there)

Per CONTEXT.md §LOCKED—Tool integration contract: when Phase 4 (`grep`, `read_document`) reads a row with `content_markdown_status != 'ready'`, the tool returns:

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

Phase 2 does NOT implement this contract — it only ensures the data is in the right state for Phase 4 to honor it (status field is correctly populated by both synchronous-on-upload and backfill paths). Pattern-mapper notes this here so the Phase 2 planner adds a docstring/comment in `ingestion.py` and `backfill_content_markdown.py` referencing the Phase 4 dependency: any change to the status vocabulary must be coordinated with Phase 4 tool implementation.

---

## Metadata

**Analog search scope:**
- `backend/scripts/` — all 19 .py files (test runners, helpers, migrations runner, schema verifier)
- `backend/app/services/` — all 11 .py files (ingestion, record_manager, folder_service, etc.)
- `backend/app/routers/` — files.py (multipart upload analog)
- `backend/app/auth.py` — service-role client analog
- `backend/migrations/` — all 17 .sql files (RLS policy syntax analog from 015)
- `backend/venv/Lib/site-packages/storage3/_sync/file_api.py` — external SDK signature verification
- Two `Grep` passes for existing Storage usage: `pattern="storage"` and `pattern="storage_path|storage\.from_"` — both returned zero matches in `backend/` (excluding venv).

**Files scanned:** ~50 source files across 6 directories.
**Pattern extraction date:** 2026-05-04
**Total analogs read:** 11 files (run_migrations.py, ingestion.py, files.py, auth.py, record_manager.py, folder_service.py, test_files.py, test_two_scope_rls.py, test_all.py, test_helpers.py, verify_phase1_schema.py) + 2 SQL migrations (014, 015) + 1 external SDK reference.
