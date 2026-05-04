# Phase 2: content_markdown Backfill (Gated) — Research

**Researched:** 2026-05-04
**Domain:** Synchronous Docling markdown capture in `ingest_document()` + idempotent CLI backfill script + tool-surfaced status state machine, layered onto an existing Episode 1 Docling pipeline that produces `result.document.export_to_markdown()` and immediately discards it.
**Confidence:** HIGH on the synchronous-on-upload path (BACKFILL-01) — the markdown export is already computed in `extract_text()` and just needs to be threaded through. **HIGH on the tool-integration contract (BACKFILL-04)** — the status enum and partial index landed cleanly in Migration 014. **MEDIUM-LOW on the historical-blob backfill (BACKFILL-02)** — the central design assumption ("re-run Docling against original Storage blobs") collides with the actual codebase reality (no Supabase Storage integration exists; original blobs are discarded after the FastAPI request). This is the load-bearing finding of this research and the planner must address it head-on. **HIGH on BACKFILL-03** — already a no-op via Migration 012's `folder_path TEXT NOT NULL DEFAULT '/'` and `scope TEXT NOT NULL DEFAULT 'user'`; verifier just needs to confirm.

## Summary

Phase 2 has four requirements (BACKFILL-01..04) with three logical groups:

1. **Synchronous-on-upload write (BACKFILL-01).** Trivial. `extract_text()` at `backend/app/services/ingestion.py:62-142` already calls `result.document.export_to_markdown()` on lines 99 and 132 and returns the resulting text — which then gets fed to `chunk_text()` and discarded as a side effect. The plan just needs to capture that string before discarding it and persist it to `documents.content_markdown` in the same UPDATE that flips `documents.status` to `'ready'` at `ingestion.py:437-439`. **Zero new external dependencies, zero new code paths, ~10 lines of edits.**

2. **Status surface (BACKFILL-03 + BACKFILL-04).** BACKFILL-03 is already a no-op — Phase 1 / Migration 012 made `folder_path` and `scope` `NOT NULL DEFAULT '/'/'user'` so existing rows migrated automatically when the migration ran. The planner just verifies and documents. BACKFILL-04 requires defining the contract for what `grep`/`read_document` return when they encounter a row with `content_markdown_status != 'ready'`, then ensuring `backfill_content_markdown.py` correctly populates the failure-mode terminal states (`'failed'`, `'requires_user_reupload'`).

3. **Backfill of historical Episode 1 documents (BACKFILL-02).** **This is the hard part — and the design assumption embedded in the requirement is unverified by the codebase.** ROADMAP, REQUIREMENTS, and Pitfall 6 all say "re-run Docling against the original Storage blob." But the Episode 1 ingest path (`backend/app/routers/files.py:30-96`) takes the file via `UploadFile`, reads `await file.read()` into bytes, passes those bytes to `ingest_document()` as `file_content`, and never persists them anywhere. There is no `documents.storage_path` column. There is no call to `supabase.storage.from_(bucket).upload(...)` anywhere in `backend/`. There is no Storage bucket configured (or at least, no code touches one). The codebase intel docs (`.planning/codebase/INTEGRATIONS.md:48-50`) claim "Document uploads stored in Supabase buckets" but this is **incorrect** — verified by exhaustive grep of `backend/` for any Storage SDK call (zero matches outside `venv/`).

**Primary recommendation:** Resolve the Storage gap as the **first** task of the phase, BEFORE the backfill script is written. The planner has three viable options (detailed in Decision §2): (A) add Storage upload to the ingest path now and accept that historical Episode 1 docs without blobs go straight to `requires_user_reupload`; (B) skip BACKFILL-02 entirely and only backfill via re-upload, marking all existing docs as `requires_user_reupload` immediately; (C) defer Phase 2 and route Phase 4 around it. Option A is recommended: it costs ~30 lines of new code, restores the Storage assumption that the rest of the architecture rests on, and Phase 2's success criterion 4 (byte-equivalence spot-check) is then satisfiable for any document uploaded after Phase 2 ships even though most pre-existing docs become `requires_user_reupload`. Document the operational decision in the SUMMARY so Phase 6 UI can surface "X documents need re-upload to enable grep/read."

The synchronous-on-upload path (BACKFILL-01) and the tool-integration contract (BACKFILL-04) ship cleanly regardless of which Storage option is chosen. The byte-equivalence success criterion 4 is satisfiable on any document uploaded after the synchronous path lands — the criterion does NOT require historical backfill to work, only that backfilled-via-Docling content equals fresh-Docling content for the same blob, which is mathematically true if both use the same Docling version and options.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Synchronous Docling markdown capture on new upload | API / Backend (`ingestion.py`) | — | Markdown is produced by Docling already in the existing pipeline; capture point is co-located with chunk write |
| Backfill of historical Episode 1 documents | Standalone CLI script (`backend/scripts/`) | API / Backend (reuses ingestion helpers + supabase client) | Long-running (potentially hours), interruptible, runs out-of-process; should not compete with API request lifecycle |
| `documents.content_markdown` persistence | Database / Storage (Supabase Postgres) | — | Already landed via Migration 014 (Phase 1) |
| `content_markdown_status` state transitions | API / Backend (writes by both ingest and backfill) | Database / Storage (CHECK enforces vocabulary) | The 4-element enum is locked at the DB layer; transition logic lives in app code |
| Original blob retention | **GAP** — currently nowhere | Should be: Database / Storage (Supabase Storage bucket) | This is the central hidden gap — see Decision §2 |
| Tool-surfaced re-index status | API / Backend (Phase 4 — forward-looking contract) | UI (Phase 6 — badge rendering) | Phase 2 locks the JSON contract that Phase 4 honors |

## User Constraints (from CONTEXT.md)

**No CONTEXT.md exists.** Per the orchestrator's directive: ROADMAP success criteria + PITFALLS Pitfall 6 + REQUIREMENTS BACKFILL-01..04 are the authoritative specification. This research treats those documents as locked decisions.

The following are de facto locked decisions extracted from those sources:

- **Re-run Docling, NEVER stitch from chunks.** Pitfall 6 RANK 2 — the 50-word chunk overlap silently breaks `grep` line numbers and `read_document` slicing. Even as a temporary fallback this is forbidden.
- **`content_markdown_status` vocabulary is locked at exactly 4 values:** `'pending'`, `'ready'`, `'failed'`, `'requires_user_reupload'` (Phase 1 / Migration 014, REQUIREMENTS.md SCHEMA-03 line 12, project state Decisions log line 78).
- **Idempotency is required.** Backfill must be safely re-runnable; second run is a no-op for any row already at `'ready'`.
- **Throttle via existing `_ingestion_semaphore`.** Per BACKFILL-02 explicit wording.
- **Synchronous-on-upload, NOT a follow-up job.** Per BACKFILL-01 explicit wording — the markdown export must happen inside `ingest_document()` and persist before the row's status flips to `'ready'`.
- **Tools surface `pending_reindex` status, never silently skip.** Per BACKFILL-04 + Pitfall 6 — Phase 4 contract.

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| BACKFILL-01 | `ingestion.py` captures and persists Docling's markdown export to `documents.content_markdown` on every new upload (synchronous in-pipeline, NOT a follow-up job) | §1 (Docling API), §7 (synchronous insertion point) — capture point is `ingestion.py:395` (after `extract_text` returns) and persist point is the same UPDATE at `ingestion.py:437-439` that flips `status='ready'` |
| BACKFILL-02 | `backend/scripts/backfill_content_markdown.py` re-runs Docling against original Storage blobs for existing Episode 1 docs (idempotent, throttled, status-tracked, logs counts) | §2 (Storage gap — load-bearing decision required), §3 (idempotency contract), §4 (semaphore reuse), §9 (CLI conventions) |
| BACKFILL-03 | Episode 1 documents migrate to `folder_path='/'`, `scope='user'` (automatic via column DEFAULT) | §5 — already a no-op via Migration 012's `NOT NULL DEFAULT`; verifier confirms `SELECT COUNT(*) FROM documents WHERE folder_path != '/' OR scope != 'user'` is 0 for pre-Phase-2 rows |
| BACKFILL-04 | Documents whose source blob is missing get marked `requires_user_reupload`; tools surface this status (returning `{status: 'pending_reindex'}`) rather than silently skipping | §5 (status state machine) + §6 (tool integration contract) |

## Project Constraints (from CLAUDE.md)

These directives apply to every file/script created in Phase 2:

- **Python backend uses `venv`** — backfill script must be invoked via `cd backend && venv/Scripts/python scripts/backfill_content_markdown.py` (Windows path).
- **No LangChain / no LangGraph — raw SDK calls only.** Backfill uses the supabase-py client directly + the existing Docling `DocumentConverter` already in `ingestion.py`.
- **Pydantic for structured outputs.** Not directly applicable to this phase (no LLM-structured responses), but if the backfill script returns a structured per-row result it should be a Pydantic dataclass for consistency.
- **All tables need RLS — users only see their own data.** The backfill runs as service-role (the only client capable of operating on cross-user rows); RLS is bypassed by service-role per Episode 1 anti-pattern (`.planning/codebase/CONCERNS.md`). Backfill is admin-operational, not user-facing — this is acceptable, but defense-in-depth: backfill script should explicitly filter by `content_markdown_status <> 'ready'` to scope its writes.
- **Stream chat responses via SSE.** Not applicable to Phase 2.
- **Polling (not Realtime) for ingestion status updates.** Not applicable to Phase 2.
- **Module 2+ uses stateless completions.** Not applicable to Phase 2.
- **Ingestion is manual file upload only — no connectors or automated pipelines.** The backfill script is NOT a connector — it is an idempotent re-run against existing rows. Acceptable per project rules.
- **Tests must NEVER delete all user data.** Backfill modifies existing rows but never deletes them. Test fixtures must use scoped cleanup (track IDs created).

## Standard Stack

### Core (already installed — zero new dependencies)

| Library | Version (verified) | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `docling` | 2.91.0 [VERIFIED: `pip show docling`] | Markdown export via `result.document.export_to_markdown()` | Already used by Episode 1 `extract_text()` at `ingestion.py:115-132` |
| `supabase` (supabase-py) | unpinned in requirements.txt [VERIFIED: `backend/requirements.txt:6`] | Client to read documents, update content_markdown, AND (if Decision §2 Option A taken) download Storage blobs | Already used throughout codebase |
| `python-dotenv` | unpinned [VERIFIED: `backend/requirements.txt:3`] | Load `.env` for service-role key + DB URL | Already used by `test_helpers.py:10` |
| `psycopg2` (transitively via supabase or directly) | n/a [VERIFIED: imported in `backend/scripts/run_migrations.py:16`] | NOT used by backfill script (supabase-py is sufficient); listed only as the existing alternative pattern for direct-DB scripts | The Episode 1 convention is supabase-py for app-layer, psycopg2 for migrations only |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| Standard `logging` | stdlib | Structured per-document logs (success/failure/missing-blob counts) | Matches existing convention — `ingestion.py:19` uses `logger = logging.getLogger(__name__)` |
| `langsmith.@traceable` | (already imported elsewhere) | Optional tracing of `ingest_document()` if it's not already traced | The current `ingest_document()` does NOT have `@traceable` — this is a small enhancement opportunity but not required by Phase 2 |
| `argparse` | stdlib | CLI flags for `--dry-run`, `--limit`, `--document-id`, `--scope` | No project precedent for argparse in scripts (`run_migrations.py` uses env vars only) — argparse is the safer-default Python idiom; planner can choose env-var if matching `run_migrations.py` shape is preferred |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| supabase-py for the backfill DB writes | psycopg2 directly (matches `run_migrations.py`) | psycopg2 bypasses RLS naturally (already a service-role thing) and is closer to the metal; supabase-py uses PostgREST and adds an HTTP hop. **Recommendation: supabase-py** — the backfill is talking about row-level reads/updates, exactly supabase-py's strength; psycopg2 is overkill |
| Reuse `ingest_document()` from `ingestion.py` | Write a new lighter-weight function that ONLY does Docling export (skip chunking, embedding, metadata extraction) | Reusing `ingest_document()` re-chunks and re-embeds every backfilled doc — that's wasteful (chunks already exist) and risks RLS/dedup contract changes. **Recommendation: write a slim `extract_markdown_only(file_content, mime_type, file_name)` helper that wraps Docling and returns the markdown string only.** Reuses the same `extract_text()` body but returns instead of feeding to chunking |
| Direct call to Docling in the backfill script | Reuse `extract_text()` from `ingestion.py` as-is | `extract_text()` already does PPTX→PDF conversion, OCR-enabled PDF parsing, fallback handling, and UTF-8 decoding for plain text — exactly what we need. **Recommendation: call `extract_text()` directly from the backfill script; no new helper needed.** This is the line of least resistance and least risk |

**Installation:** None — no new dependencies.

**Version verification (verified during this research):**
- `docling==2.91.0` (checked `pip show docling` — confirms Episode 1's pinned-via-shipped version is current)
- `export_to_markdown(self, ...) -> str` signature has 19 keyword args [VERIFIED: `backend/venv/Lib/site-packages/docling_core/types/doc/document.py:5908-5934`]; the existing `ingestion.py:99,132` calls it with no args (all defaults); for byte-equivalence determinism, the backfill MUST also call it with no args (so success criterion 4 holds — same input, same options, same output)

## Architecture Patterns

### System Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          PHASE 2 SCOPE                                    │
└──────────────────────────────────────────────────────────────────────────┘

NEW-UPLOAD PATH (BACKFILL-01) — synchronous, in-pipeline:

   FastAPI POST /api/files/upload
        │
        ▼ (await file.read())
   files.py:upload_file → background_tasks.add_task(_throttled_ingest, ingest_document, ...)
        │
        ▼ (acquires _ingestion_semaphore[2])
   ingestion.py:ingest_document(document_id, file_content, mime_type, file_name, user_id, supabase_client)
        │
        ├──→ documents.UPDATE status='processing'              (line 391-393)
        │
        ├──→ text = extract_text(file_content, mime_type, file_name)
        │         │
        │         └──→ DocumentConverter.convert(tmp_path)
        │                  │
        │                  └──→ result.document.export_to_markdown()  ← THIS STRING IS THE ASSET
        │                            (lines 99 / 132 — currently flows back as `text` and then discarded after chunking)
        │
        ├──→ extract_metadata(text, ...)                       (line 401-408)
        ├──→ _extract_structured_data(...)                     (line 411-414)
        ├──→ chunks = chunk_text(text, 500, 50)                (line 416)
        ├──→ embeddings = embed_batch(chunks)                  (line 420)
        ├──→ document_chunks INSERT                            (line 433-434)
        │
        └──→ documents.UPDATE status='ready', content_hash=...  (line 437-439)
                                  ▲
                                  │
                                  └──── ★ NEW: also write content_markdown=text,
                                              content_markdown_status='ready'  ★

BACKFILL PATH (BACKFILL-02) — out-of-process CLI, idempotent:

   $ cd backend && venv/Scripts/python scripts/backfill_content_markdown.py [--dry-run] [--limit N] [--document-id UUID]
        │
        ├──→ load .env (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        │
        ├──→ supabase = create_client(URL, SERVICE_ROLE_KEY)   (bypasses RLS — required for cross-user backfill)
        │
        ├──→ rows = supabase.table('documents')
        │              .select('id, user_id, file_name, mime_type, scope, folder_path, storage_path?, ...')
        │              .neq('content_markdown_status', 'ready')
        │              .execute()
        │           (uses partial index documents_content_markdown_status_idx — Migration 014)
        │
        └──→ for each row:
              acquire _ingestion_semaphore[2]
              try:
                  blob_bytes = download_blob(row)              ← THIS IS THE GAP — see Decision §2
                  if blob_bytes is None:
                      mark requires_user_reupload, log, continue
                  markdown = extract_text(blob_bytes, mime_type, file_name)
                  if not markdown.strip():
                      raise ValueError("empty extraction")
                  supabase.table('documents').update({
                      'content_markdown': markdown,
                      'content_markdown_status': 'ready',
                  }).eq('id', row['id']).execute()
                  log success
              except Exception as e:
                  supabase.table('documents').update({
                      'content_markdown_status': 'failed',
                      'error_message': str(e),  # if column exists
                  }).eq('id', row['id']).execute()
                  log failure
              finally:
                  release _ingestion_semaphore

TOOL-INTEGRATION CONTRACT (BACKFILL-04, forward-looking for Phase 4):

   Phase 4 grep / read_document (NOT IN PHASE 2 SCOPE — contract only)
        │
        ├──→ SELECT id, file_name, content_markdown_status, content_markdown FROM documents WHERE ...
        │
        └──→ for each row:
              if content_markdown_status == 'ready' and content_markdown IS NOT NULL:
                  do the grep / read normally
              elif content_markdown_status in ('pending', 'failed', 'requires_user_reupload'):
                  emit a result row of shape:
                  {
                    "document_id": "<uuid>",
                    "file_name": "<name>",
                    "scope": "<user|global>",
                    "status": "pending_reindex",
                    "content_markdown_status": "<pending|failed|requires_user_reupload>"
                  }
                  (NEVER silently exclude — Pitfall 6)
```

### Recommended Project Structure

```
backend/
├── app/
│   └── services/
│       └── ingestion.py                      # MODIFIED — capture markdown export, persist alongside chunks
└── scripts/
    ├── backfill_content_markdown.py          # NEW — idempotent CLI backfill script
    └── test_backfill_content_markdown.py     # NEW (or extend test_files.py) — unit + integration tests
```

### Pattern 1: Capture-and-persist Docling export inside `ingest_document()`

**What:** Hold onto the `text` returned by `extract_text()`, then include it in the final UPDATE that flips `status='ready'`.

**When to use:** Every new upload via `POST /api/files/upload`.

**Example (paste-ready edit):**

```python
# Source: backend/app/services/ingestion.py — modify the existing ingest_document()
def ingest_document(
    document_id: str,
    file_content: bytes,
    mime_type: str,
    file_name: str,
    user_id: str,
    supabase_client,
) -> None:
    try:
        supabase_client.table("documents").update(
            {"status": "processing", "updated_at": "now()"}
        ).eq("id", document_id).execute()

        text = extract_text(file_content, mime_type, file_name)
        if not text.strip():
            raise ValueError("No extractable text found in document")

        # ... metadata, structured-data, chunks, embeddings (unchanged) ...

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

    except Exception as e:
        # ... existing error handling — flip status='failed' ...
        # ALSO: flip content_markdown_status='failed' so Phase 4 tools surface it
        try:
            supabase_client.table("documents").update({
                "status": "failed",
                "content_markdown_status": "failed",
                "error_message": str(e),
                "updated_at": "now()",
            }).eq("id", document_id).execute()
        except Exception:
            pass
```

**Apply the same edit to `ingest_document_update()`** at `ingestion.py:453-526` — it has the same shape and the same UPDATE-to-'ready' pattern at lines 512-515.

### Pattern 2: Idempotent CLI backfill script

**What:** Standalone Python script that scans `documents WHERE content_markdown_status <> 'ready'`, downloads each row's original blob, re-runs Docling, and writes the markdown.

**When to use:** Once at Phase 2 deploy time. May be re-run safely as new failure modes are discovered.

**Example (skeleton; details depend on Decision §2):**

```python
# Source: backend/scripts/backfill_content_markdown.py — NEW FILE
"""Backfill documents.content_markdown for existing Episode 1 documents.

Re-runs Docling against the original blob (downloaded from Supabase Storage if available;
otherwise marks the row as 'requires_user_reupload') and persists the canonical markdown
export to documents.content_markdown.

Usage:
    cd backend && venv/Scripts/python scripts/backfill_content_markdown.py
    cd backend && venv/Scripts/python scripts/backfill_content_markdown.py --dry-run
    cd backend && venv/Scripts/python scripts/backfill_content_markdown.py --limit 10
    cd backend && venv/Scripts/python scripts/backfill_content_markdown.py --document-id <uuid>

Idempotent — safe to re-run. Only processes rows where content_markdown_status != 'ready'.
Throttled via the same Semaphore(2) used by the live ingestion path.
"""
import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

# Make `app` importable when running from backend/ as cwd
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.ingestion import extract_text  # reuses Docling pipeline

load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
)
logger = logging.getLogger("backfill")

# Same throttle as the live ingestion path (files.py:11). We instantiate our OWN
# Semaphore here because the API server's _ingestion_semaphore is a process-local
# threading.Semaphore — a separate process cannot acquire it. The intent of
# BACKFILL-02's "throttled via existing _ingestion_semaphore" is concurrency-control
# parity, not literal object reuse. See Decision §4.
_backfill_semaphore = threading.Semaphore(2)

STORAGE_BUCKET = "documents"  # see Decision §2 for whether this exists at all


def _download_blob(supabase, row: dict) -> bytes | None:
    """Return the original file bytes for `row`, or None if unavailable.

    Implementation depends on Decision §2 (Storage Gap):
      - Option A: Phase 2 adds Storage upload at ingest time. Pre-Phase-2 docs
                  return None; post-Phase-2 docs are downloadable via storage.from_('documents').download(path).
      - Option B: Skip — always return None. Every existing doc → 'requires_user_reupload'.
      - Option C: This function does not exist; backfill is descoped.
    """
    storage_path = row.get("storage_path")
    if not storage_path:
        return None
    try:
        return supabase.storage.from_(STORAGE_BUCKET).download(storage_path)
    except Exception as e:
        logger.warning(f"Storage download failed for {row['id']}: {e}")
        return None


def _process_one(supabase, row: dict, dry_run: bool) -> str:
    """Process one row; return one of {'ready', 'failed', 'requires_user_reupload', 'skipped'}."""
    doc_id = row["id"]
    file_name = row.get("file_name", "<unknown>")
    mime_type = row.get("mime_type", "application/octet-stream")
    started = time.monotonic()

    blob = _download_blob(supabase, row)
    if blob is None:
        if dry_run:
            logger.info(f"[DRY] {doc_id} {file_name}: would mark requires_user_reupload (no blob)")
            return "requires_user_reupload"
        supabase.table("documents").update({
            "content_markdown_status": "requires_user_reupload",
            "updated_at": "now()",
        }).eq("id", doc_id).execute()
        logger.info(f"{doc_id} {file_name}: requires_user_reupload (no blob)")
        return "requires_user_reupload"

    try:
        markdown = extract_text(blob, mime_type, file_name)
        if not markdown or not markdown.strip():
            raise ValueError("Docling returned empty markdown")
    except Exception as e:
        if dry_run:
            logger.error(f"[DRY] {doc_id} {file_name}: would mark failed ({type(e).__name__}: {e})")
            return "failed"
        supabase.table("documents").update({
            "content_markdown_status": "failed",
            "updated_at": "now()",
        }).eq("id", doc_id).execute()
        logger.error(f"{doc_id} {file_name}: failed ({type(e).__name__}: {e})")
        return "failed"

    duration_ms = int((time.monotonic() - started) * 1000)
    if dry_run:
        logger.info(f"[DRY] {doc_id} {file_name}: would mark ready ({len(markdown)} chars, {duration_ms}ms)")
        return "ready"
    supabase.table("documents").update({
        "content_markdown": markdown,
        "content_markdown_status": "ready",
        "updated_at": "now()",
    }).eq("id", doc_id).execute()
    logger.info(f"{doc_id} {file_name}: ready ({len(markdown)} chars, {duration_ms}ms)")
    return "ready"


def _process_throttled(supabase, row, dry_run, counts):
    acquired = _backfill_semaphore.acquire(timeout=600)
    try:
        if not acquired:
            logger.error(f"semaphore timeout for {row.get('id')}")
            counts["timeout"] += 1
            return
        outcome = _process_one(supabase, row, dry_run)
        counts[outcome] = counts.get(outcome, 0) + 1
    finally:
        if acquired:
            _backfill_semaphore.release()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report what would change; do not write")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N documents")
    parser.add_argument("--document-id", type=str, default=None, help="Process only this document ID")
    args = parser.parse_args()

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        logger.error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY required")
        return 1

    supabase = create_client(url, key)

    query = supabase.table("documents").select(
        "id, user_id, file_name, mime_type, scope, folder_path, "
        "content_markdown_status, storage_path"
    ).neq("content_markdown_status", "ready").order("created_at", desc=False)

    if args.document_id:
        query = query.eq("id", args.document_id)
    if args.limit:
        query = query.limit(args.limit)

    result = query.execute()
    rows = result.data or []
    logger.info(
        f"Found {len(rows)} documents needing backfill "
        f"(dry_run={args.dry_run}, limit={args.limit}, document_id={args.document_id})"
    )

    counts: dict[str, int] = {}
    for row in rows:
        # Sequential is fine — the semaphore is the throttle, but in a single-threaded
        # script we don't actually parallelize. For parallelism, wrap each call in a
        # ThreadPoolExecutor; deferred to a future iteration.
        _process_throttled(supabase, row, args.dry_run, counts)

    logger.info(f"Backfill complete. Counts: {counts}")
    return 0 if counts.get("failed", 0) == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
```

### Pattern 3: Status state machine

**What:** Define the legal transitions of `content_markdown_status` and document them in the script.

**When to use:** Backfill script logic — every UPDATE must respect these transitions.

**Example:**

```
Initial state for ALL existing Episode 1 documents (after Migration 014):
    content_markdown_status = 'pending'      (DEFAULT)
    content_markdown        = NULL

State machine (transitions):
                         ┌───────────────────────┐
                         │       'pending'       │ ← DEFAULT for existing rows + new uploads
                         └─────────┬─────────────┘
                                   │
        ┌──────────────────────────┼──────────────────────────────┐
        │                          │                              │
        ▼ Docling success          ▼ Docling raised               ▼ Storage 404 / blob GC'd
   ┌──────────┐              ┌──────────┐                    ┌──────────────────────────┐
   │ 'ready'  │              │ 'failed' │                    │ 'requires_user_reupload' │
   └──────────┘              └────┬─────┘                    └──────────┬───────────────┘
   (terminal-success)             │                                     │
                                  │ re-run backfill                     │ re-run backfill
                                  ▼ (transient errors)                  ▼ (still no blob)
                          ┌──────────────┐                       ┌──────────────────────────┐
                          │ 'ready' OR   │                       │ 'requires_user_reupload' │
                          │ stay 'failed'│                       │   (no-op)                │
                          └──────────────┘                       └──────────────────────────┘

Backfill RE-RUN policy:
  - 'ready' rows are SKIPPED (never re-processed; idempotent)
  - 'pending' rows are PROCESSED
  - 'failed' rows are RE-PROCESSED (operator may have fixed root cause)
  - 'requires_user_reupload' rows are RE-PROCESSED (blob may now exist)

Phase 4 tool behavior (forward-looking — see §6):
  - 'ready' → normal grep/read
  - 'pending' / 'failed' / 'requires_user_reupload' → return {status: 'pending_reindex', ...}
```

### Anti-Patterns to Avoid

- **Stitching from chunks via `string_agg`.** Pitfall 6 RANK 2. The 50-word overlap silently breaks `grep` line numbers and `read_document` slicing. Even as a temporary fallback this is forbidden. Verify by code review of the backfill script: zero `string_agg`, zero `array_agg(content)`, zero `'\n\n'.join([row['content'] for row ...])` patterns.
- **Background-tasking the synchronous-on-upload write (BACKFILL-01).** The requirement is explicit: "synchronous in-pipeline write — NOT a follow-up job." Do not split it into `BackgroundTasks.add_task(write_markdown_later, ...)` — that loses the atomicity guarantee that "if `status='ready'`, then `content_markdown` is populated."
- **Try/except wrapping the markdown capture so chunks still write on Docling failure.** This is tempting but BAD: it lets the document reach `status='ready'` (chunks present) while `content_markdown_status='failed'`. The tool-integration contract treats `failed` rows as `pending_reindex` even though `search_documents` (chunks-based) works fine for them. **Recommended: if Docling fails, propagate the exception — both chunks and content_markdown fail together.** This matches the existing semantic where `extract_text()` failure = entire ingest failure (line 396 raises if no text).
- **Catching exceptions in the backfill that are NOT Docling-specific.** A network error talking to Supabase, an OOM, a SIGINT — these should propagate and crash the script, not silently mark a row 'failed'. Only Docling errors should mark a row 'failed'.
- **Updating `documents.status` from the backfill script.** That column belongs to the chunks/embeddings ingestion lifecycle. The backfill ONLY touches `content_markdown` and `content_markdown_status`. If a document's `status='failed'` (from a previous failed ingest), the backfill should still try — it's an independent dimension.
- **Persisting Docling's full result object to disk for replay.** Tempting (debugging aid) but adds an unbudgeted code path; defer.
- **Using LangSmith `@traceable` on the backfill script.** Backfill is operational tooling, not LLM-tool dispatch. Traces should not pollute the LangSmith project. Use stdlib `logging` only.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Markdown extraction from PDF/DOCX/PPTX/etc | A custom extractor | `extract_text()` from `ingestion.py` (which wraps Docling) | Docling already handles 17+ formats with OCR, PPTX→PDF conversion, fallback decoding — re-implementing is months of edge cases |
| Idempotency tracking | A separate `backfill_state` table | Just check `content_markdown_status != 'ready'` (uses Migration 014's partial index) | The status column IS the idempotency record |
| Throttle / concurrency control | A complex async queue | `threading.Semaphore(2)` matching the existing `files.py:11` | Already the project convention for "don't melt the embedding API" |
| Resumability | Checkpoint files / cursors | Idempotent re-run via the status filter | A re-run after a SIGINT picks up where it left off automatically |
| Storage download retry | Custom retry with exponential backoff | Just let it fail → mark `requires_user_reupload` → operator can re-run later | Backfill is operator-driven and re-runnable; transient retry is unnecessary complexity |
| CLI argument parsing | bash positional args / env vars | stdlib `argparse` | Three+ flags merit argparse; no project precedent constrains the choice |
| Per-row status logging | Custom JSON-line logger | `logging.getLogger("backfill")` with the existing format | Matches Episode 1 convention (`ingestion.py:19`) |

**Key insight:** Phase 2's hardest problems are NOT software-engineering problems — they are operational problems (does the original blob exist? what version of Docling did Episode 1 ship with?). Resist the urge to add code complexity to make those operational problems "go away." The right shape is: a thin script that delegates to existing helpers, a clear status state machine that surfaces operational gaps to humans, and aggressive logging.

## Runtime State Inventory

This is a refactor/migration phase (it modifies existing data via the backfill script). The full 5-category inventory:

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | (a) `documents.content_markdown`: NULL for all existing Episode 1 rows after Migration 014; backfill populates. (b) `documents.content_markdown_status`: `'pending'` for all existing rows after Migration 014 [VERIFIED: Phase 1 / Plan 04 Decision]. (c) `documents.folder_path` / `documents.scope`: already migrated to `'/'` / `'user'` for existing rows by Migration 012's `NOT NULL DEFAULT` [VERIFIED: backend/migrations/012_folder_path_and_scope.sql:13-15] — BACKFILL-03 is a no-op verifier task | Code edit + data migration: backfill script writes `content_markdown` + `content_markdown_status='ready'` per row |
| Live service config | None — Phase 2 doesn't touch external services. (No n8n, no Datadog, no Tailscale ACLs in scope.) | None |
| OS-registered state | None. (No Windows Task Scheduler entries, no pm2/launchd/systemd registrations for the backfill — it's a one-shot operator script.) | None |
| Secrets/env vars | Backfill reuses existing `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` from `backend/.env` [VERIFIED: same env vars used by `auth.py:8-12`]. No new secrets. | None — verify `.env` has both keys before running backfill |
| Build artifacts / installed packages | None — Phase 2 adds no new packages. The `backend/scripts/__pycache__/` will gain a new `.pyc` for the script after first run; no special handling needed | None |

**Critical follow-up — Storage bucket existence:** [ASSUMED] If Decision §2 Option A is taken and a Supabase Storage bucket named `documents` is introduced, the backfill script's `STORAGE_BUCKET = "documents"` must match the bucket actually created. The bucket must be created via the Supabase dashboard (or `supabase.storage.create_bucket("documents", public=False)` in a one-time setup script) BEFORE the synchronous-on-upload write runs in production. This is an operational prerequisite, not a code task.

## Common Pitfalls

### Pitfall 1: Backfill stitches markdown from `document_chunks` (Pitfall 6 RANK 2)

**What goes wrong:** Developer "optimizes" by writing `SELECT string_agg(content, '\n\n' ORDER BY chunk_index) FROM document_chunks WHERE document_id = ...` instead of re-running Docling. The 50-word chunk overlap (`ingestion.py:145-158`) duplicates ~10% of every document's text. `grep` finds phantom matches; `read_document` line numbers are wrong by a varying-per-doc amount.

**Why it happens:** Stitching seems trivially cheaper than running Docling (no OCR, no GPU). Tests on simple short documents pass. The bug surfaces only on complex documents with deep chunking.

**How to avoid:**
- The backfill script MUST call `extract_text()` (which wraps `DocumentConverter.convert()` → `result.document.export_to_markdown()`).
- The script MUST NOT import or query `document_chunks`.
- Code review checklist: grep the script for `string_agg`, `array_agg`, `chunk`, `'.join(`. Zero matches required.
- Success criterion 4 (byte-equivalence ±20 chars across 10 random samples) catches this empirically.

**Warning signs:**
- `len(content_markdown) ≈ sum(len(c) for c in chunks)` — that's overlap duplication.
- `grep` for a known unique string returns 2× the expected matches.
- `read_document` line N references content that's actually on line N+overlap_offset of the source.

### Pitfall 2: Background-tasking the synchronous write breaks the atomicity guarantee

**What goes wrong:** Developer reads BACKFILL-01's "synchronous in-pipeline write" but cargo-cults the `BackgroundTasks.add_task(...)` pattern from `files.py:63-72` and ends up writing `content_markdown` in a separate background task. Now there's a window where `documents.status='ready'` but `content_markdown` is NULL.

**Why it happens:** The whole upload path is already background-tasked, so it's natural to add another background task on top. But the existing background task IS the synchronous boundary — `ingest_document()` runs entirely inside that task and the markdown write must happen inside the SAME function, in the SAME UPDATE that flips `status='ready'`.

**How to avoid:** The patch lives entirely inside `ingest_document()` and `ingest_document_update()`. No new `BackgroundTasks.add_task` calls. Code review: any new `add_task` in this PR is a red flag.

**Warning signs:** A test that uploads a doc and immediately reads it back finds `status='ready'` but `content_markdown=NULL`.

### Pitfall 3: Backfill spawns a parallel `extract_text()` per row and OOM-crashes the host

**What goes wrong:** Developer writes the backfill script using `concurrent.futures.ThreadPoolExecutor(max_workers=10)` to parallelize. Each Docling invocation loads OCR models into memory. Ten concurrent OCR runs blow past the host's RAM.

**Why it happens:** The naïve assumption that "more threads = faster." Docling is heavyweight; the existing `Semaphore(2)` exists precisely to throttle this.

**How to avoid:**
- The script's `_backfill_semaphore = threading.Semaphore(2)` is the only allowed concurrency primitive.
- A simpler pattern (recommended for MVP): process rows sequentially (one at a time). The semaphore is then mostly defensive — for an operator-script use case, one Docling at a time is plenty.
- If parallelism is added later, cap workers at `_backfill_semaphore._value` (= 2).

**Warning signs:** Backfill OOMs on a host with 8GB RAM; backfill saturates GPU; ingest API requests start timing out while backfill runs.

### Pitfall 4: The backfill silently mutates documents that should not be touched

**What goes wrong:** A coding error makes the backfill update rows where `content_markdown_status='ready'` (i.e., already done). Now the byte-equivalence guarantee depends on Docling being deterministic across versions — if Docling 2.91.0 produces slightly different markdown than the version Episode 1 used, every re-run drifts.

**Why it happens:** Filtering only at the WHERE clause is fragile; a missing `.neq('content_markdown_status', 'ready')` quietly processes every row.

**How to avoid:**
- The supabase-py select MUST include `.neq('content_markdown_status', 'ready')`.
- Defense in depth: the per-row `_process_one` checks the row's current status and returns `'skipped'` if already `'ready'` (the script-level filter could have been bypassed by a manual `--document-id` flag pointing at a ready row).
- Test: pass `--document-id` to a known-ready row and assert nothing changed.

**Warning signs:** Backfill counts show `ready` count >> the count of pre-existing pending rows.

### Pitfall 5: Storage bucket policy blocks service-role download

**What goes wrong:** A Supabase Storage bucket with restrictive RLS policies blocks even the service-role key from downloading. Backfill marks every doc `requires_user_reupload`.

**Why it happens:** Supabase Storage has its own RLS (separate from Postgres RLS) per `storage.objects` — and not every team's setup correctly grants service-role unrestricted download.

**How to avoid:**
- One-time verification BEFORE running the backfill: `supabase.storage.from_('documents').download('<known-existing-path>')` from a Python REPL using the service-role key. If this fails, fix the bucket policy first.
- Backfill script's first action should be to download a known canary blob and abort if it fails, with a clear error message pointing to the bucket policy.

**Warning signs:** Backfill marks 100% of docs `requires_user_reupload` — that's a config issue, not a missing-blob issue.

### Pitfall 6: Docling version drift makes byte-equivalence test flaky

**What goes wrong:** The backfill runs on a host with `docling==2.91.0`; the original Episode 1 ingest used a slightly different version. Markdown export differs by whitespace / ordering / image placeholder format. Success criterion 4 (±20 chars) intermittently fails.

**Why it happens:** `docling` is unpinned in `requirements.txt:10`. Different developers' venvs may have different versions.

**How to avoid:**
- Pin `docling==2.91.0` in `requirements.txt` as part of Phase 2.
- Document the pinned version in the backfill script header.
- Treat the byte-equivalence criterion as "same Docling version + same options + same bytes → identical output." The criterion is meaningful only as a regression guard, not as a cross-version compatibility claim.

**Warning signs:** Spot-check shows ~50 chars diff with no obvious content difference; differences are in image placeholders or whitespace only.

### Pitfall 7: `extract_text()` writes to `tempfile.NamedTemporaryFile(delete=False)` and the script leaks tmp files

**What goes wrong:** The current `extract_text()` at `ingestion.py:119-142` uses `NamedTemporaryFile(delete=False)` and unlinks in a `finally`. If the backfill is killed mid-run (Ctrl+C, OOM), tmp files accumulate.

**Why it happens:** The pattern works fine for a single-shot upload (process exits, OS cleans up); a long-running backfill creates many.

**How to avoid:**
- Either accept the leak (run from a tempfs / dedicated VM that's discarded).
- Or wrap the backfill in a try/finally that scrubs `tempfile.gettempdir()` for matching files on exit.
- Recommended: accept the leak for v1; document in the script header that ops should monitor `/tmp` if running thousands of docs.

**Warning signs:** `df` shows tmpfs filling up during backfill.

## Code Examples

Verified patterns from existing source code:

### Existing Docling markdown export (the asset Phase 2 captures)

```python
# Source: backend/app/services/ingestion.py:115-132
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions

with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
    tmp.write(file_content)
    tmp_path = tmp.name
try:
    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=PdfPipelineOptions(do_ocr=True),
            ),
        }
    )
    result = converter.convert(tmp_path)
    text = result.document.export_to_markdown()  # ★ THIS STRING IS THE BACKFILL ASSET ★
    # ... currently `text` is returned and then chunked-and-discarded
```

### Existing semaphore throttle pattern (to mirror in backfill)

```python
# Source: backend/app/routers/files.py:11-27
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

### Existing CLI script convention (to follow)

```python
# Source: backend/scripts/run_migrations.py
"""Run all SQL migrations in backend/migrations/ against a Supabase Postgres database.

Usage:
    DATABASE_URL='postgresql://...' venv/Scripts/python scripts/run_migrations.py

Get DATABASE_URL from Supabase: Project Settings -> Database -> Connection string -> URI.
"""
import os
import sys
from pathlib import Path

import psycopg2

def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL env var not set", file=sys.stderr)
        return 1
    # ... process ...
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

**Variations the backfill script should adopt:**
- Use `argparse` (the backfill has `--dry-run`, `--limit`, `--document-id` — argparse is justified).
- Use `from dotenv import load_dotenv` to auto-load `.env` (matches `test_helpers.py:12` convention; `run_migrations.py` doesn't because DATABASE_URL is operator-supplied).
- Use `logging` (`run_migrations.py` uses `print()` because it's stdout-as-progress; backfill emits structured logs — `logging` is preferred per `ingestion.py:19` convention).
- Exit codes: `0` = all-good, `1` = config error (missing env), `2` = some rows failed.

### Existing supabase-py update pattern (to mirror)

```python
# Source: backend/app/services/ingestion.py:437-439
supabase_client.table("documents").update(
    {"status": "ready", "content_hash": file_hash, "updated_at": "now()"}
).eq("id", document_id).execute()
```

### Existing per-document `logger.info` pattern (to mirror)

```python
# Source: backend/app/services/ingestion.py:440
logger.info(f"Ingested document {document_id}: {len(chunks)} chunks")
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Episode 1: discard `result.document.export_to_markdown()` after chunking | Episode 2: persist alongside chunks in `documents.content_markdown` | Phase 2 (this phase) | Enables `grep`, `read_document` (Phase 4) — entire family of precision tools |
| `documents.status` enum (`pending`/`processing`/`ready`/`failed`) is the only ingestion-state column | `content_markdown_status` is a parallel orthogonal axis (`pending`/`ready`/`failed`/`requires_user_reupload`) | Phase 1 / Migration 014 | Backfill can succeed/fail independently of chunk ingest; tools can surface re-index status without confusing it with overall ingest status |
| Episode 1 doesn't store original blobs | Phase 2 needs them — see Decision §2 for resolution | Phase 2 | The hidden critical-path issue this research surfaces |

**Deprecated/outdated:**
- The `result.document.export_to_markdown(strict_text=True)` and `result.document.export_to_markdown(delim="\n\n")` parameters are deprecated as of `docling_core==2.x` [VERIFIED: `docling_core/types/doc/document.py:5940-5951`]. Phase 2 should use the no-args call (matches existing `ingestion.py:99,132`).

## Decisions

### §1. Docling markdown export API — confirmed identical to existing usage

**Decision:** Call `result.document.export_to_markdown()` with NO keyword arguments.

**Rationale:** This is what the existing `extract_text()` at `ingestion.py:99,132` does. For success criterion 4 (byte-equivalence) to hold, the backfill MUST use the identical call. Adding any kwarg risks divergence. The default behavior is documented [VERIFIED: `docling_core/types/doc/document.py:5908-5934`]: `delim="\n\n"`, `from_element=0`, `to_element=sys.maxsize`, `escape_html=True`, `image_placeholder="<!-- image -->"`, etc.

**Insertion point:** `extract_text(file_content, mime_type, file_name)` already returns the markdown string. The backfill calls it directly; the live ingest path captures the existing return value.

**Risk:** None. This is the existing call.

### §2. Storage Gap — the load-bearing decision

**The gap:** ROADMAP success criterion 2 + REQUIREMENTS BACKFILL-02 + Pitfall 6 all assume "re-run Docling against the original Storage blob." But:
- The codebase has NO `documents.storage_path` column [VERIFIED: `grep -n "file_path|storage_path|blob_path|original_path|file_url" backend/migrations/` returns no matches]
- The codebase has NO Supabase Storage SDK calls anywhere in `backend/` [VERIFIED: exhaustive grep for `supabase.storage`, `storage.from_`, `StorageClient` returns 0 matches outside `venv/`]
- The codebase intel (`.planning/codebase/INTEGRATIONS.md:48-50`) claims "Document uploads stored in Supabase buckets" — this is INCORRECT [VERIFIED: by searching the entire codebase for any storage call]
- Episode 1's upload path takes `UploadFile.read()` → `bytes` → `ingest_document()` → discards bytes after chunking [VERIFIED: `backend/app/routers/files.py:30-96`]

**Therefore:** Pre-Phase-2 documents have NO recoverable original blob. The "re-run Docling" plan as stated cannot work for those documents. The planner MUST pick one of:

#### Option A (RECOMMENDED): Add Storage upload at ingest time + accept that pre-Phase-2 docs go to `requires_user_reupload`

**Cost:** ~30 lines of new code:
- Migration 017: add `documents.storage_path TEXT` column (nullable; only post-Phase-2 docs have it).
- One-time setup: create Supabase Storage bucket named `documents` (private, service-role-only) — operator action via dashboard or one-time `supabase.storage.create_bucket()` call.
- Edit `files.py:upload_file` to upload `contents` to `storage.from_('documents').upload(f"{user_id}/{doc_id}.{ext}", contents, ...)` after the document row is inserted; persist the path back to `documents.storage_path`.
- Backfill script: rows where `storage_path IS NULL` → mark `requires_user_reupload`; rows where `storage_path IS NOT NULL` → download via `storage.from_('documents').download(path)` and re-extract.

**Tradeoffs:**
- ✓ Restores the foundational assumption that the rest of Episode 2 architecture rests on.
- ✓ Makes BACKFILL-02 actually achievable for any post-Phase-2 document.
- ✓ Makes BACKFILL-04 (`requires_user_reupload`) the dominant path for pre-existing docs — which is what Pitfall 6 anticipates anyway.
- ✓ Success criterion 4 (byte-equivalence) is satisfiable on test fixtures uploaded after Phase 2 ships.
- ✗ Pre-existing Episode 1 production docs become `requires_user_reupload` en masse — UI must surface this in Phase 6 (`UI-08` already mentions a `content_markdown_status` badge — wire it).
- ✗ Adds storage cost (per-doc blob retention) — minor at Episode 2 scale.

**This is the recommended option** because it makes the architecture honest. Every document going forward is recoverable; existing docs surface the operational debt without silent failure.

#### Option B: Skip BACKFILL-02 entirely; mark every existing doc `requires_user_reupload`

**Cost:** ~5 lines:
- A one-time SQL migration: `UPDATE documents SET content_markdown_status='requires_user_reupload' WHERE content_markdown_status='pending'`.
- The backfill script becomes a no-op shell that just verifies the migration ran.

**Tradeoffs:**
- ✓ Trivially simple.
- ✓ No new code paths.
- ✗ User experience: every existing doc is marked needing re-upload immediately, with no path to recovery (Phase 2 leaves them stuck).
- ✗ Does not address the underlying "no blob retention" issue — the next time someone needs the original (e.g., re-chunking with new strategy) they'll hit the same wall.
- ✗ Violates the spirit of BACKFILL-02 (the requirement explicitly says "re-runs Docling against original Storage blobs," which is impossible if no blobs exist).

**Recommended only if Storage cost is a hard blocker** — but at Episode 2 scale that's implausible.

#### Option C: Defer Phase 2; route Phase 4 around it

**Cost:** Major roadmap change. Phase 4 tools (`grep`, `read_document`) would need a fundamentally different design that doesn't depend on `content_markdown` (e.g., regex against chunks with overlap-dedup logic). This contradicts every prior design decision.

**Recommendation: REJECTED.** Pitfall 6 is RANK 2 specifically because chunk-based grep is wrong. Phase 4 cannot ship a half-broken `grep` and `read_document`.

**Recommended pick:** **Option A.** Add Storage upload now, accept the operational debt for pre-Phase-2 docs, surface it via the UI badge in Phase 6.

**If Option B is chosen instead** (smaller scope, simpler delivery), the backfill script collapses to: a single supabase-py UPDATE setting `content_markdown_status='requires_user_reupload'` for all `pending` rows. The synchronous-on-upload write (BACKFILL-01) still ships, so all NEW uploads work correctly — only historical recovery is sacrificed.

[ASSUMED] The user has not been asked which option to take. The planner MUST surface this decision to the user before Wave 1 task execution begins. Discussion phase was skipped, so this surfaces as a Phase 2 plan-time question.

### §3. Idempotency contract

**Decision:** Backfill scans via `WHERE content_markdown_status <> 'ready'` (uses Migration 014's partial index `documents_content_markdown_status_idx`).

**Defensive secondary check:** Do NOT add `AND content_markdown IS NULL` to the WHERE clause. The two columns can drift if a manual UPDATE goes sideways; the canonical source of truth is `content_markdown_status`. A doc with `status='ready'` but `content_markdown=NULL` is a data-integrity bug the backfill should NOT repair (it should be surfaced via a separate audit query).

**SELECT shape:**
```python
supabase.table("documents") \
    .select("id, user_id, file_name, mime_type, scope, folder_path, "
            "content_markdown_status, storage_path") \
    .neq("content_markdown_status", "ready") \
    .order("created_at", desc=False) \
    .execute()
```

**UPDATE shape (success):**
```python
supabase.table("documents").update({
    "content_markdown": markdown,
    "content_markdown_status": "ready",
    "updated_at": "now()",
}).eq("id", doc_id).execute()
```

**UPDATE shape (failure):**
```python
supabase.table("documents").update({
    "content_markdown_status": "failed",  # or "requires_user_reupload"
    "updated_at": "now()",
}).eq("id", doc_id).execute()
```

**Transaction boundary:** One row per UPDATE; supabase-py `.update()` is auto-commit per PostgREST call. There is NO need (and NO mechanism via supabase-py) for a multi-row transaction. Each row succeeds or fails independently.

**Confidence:** HIGH. Migration 014's partial index is purpose-built for this scan.

### §4. Concurrency throttle — semaphore reuse semantics

**Decision:** Backfill instantiates its OWN `threading.Semaphore(2)` with the same capacity as `files.py:11`'s `_ingestion_semaphore`.

**Rationale:** The semaphore at `files.py:11` is a process-local `threading.Semaphore` — instantiated when the FastAPI app starts. A separate Python process running `backfill_content_markdown.py` cannot acquire that semaphore object (different process, different memory).

The intent of BACKFILL-02's "throttled via existing `_ingestion_semaphore`" is concurrency-control PARITY (= 2 max concurrent Docling runs), not literal object reuse.

**Tradeoff acknowledged:** The backfill and the live API can each have 2 concurrent Docling runs simultaneously — total 4. If running backfill against production while users upload, this MAY cause OOM / GPU saturation. Mitigation: backfill should be run during a maintenance window, or `_backfill_semaphore = threading.Semaphore(1)` for safety.

**Recommended:** Keep `Semaphore(2)` for parity, document in script header that "for production, consider running during low-traffic window."

**Confidence:** HIGH. The semaphore choice is mechanical; the operational guidance is the only judgment call.

### §5. Status state machine + retry semantics

**Decision:** All four statuses are valid; transitions follow the diagram in Pattern 3.

**Failure-retry semantics (key open detail):** A row at `'failed'` IS re-processed on backfill re-run. Operationally, `'failed'` means "Docling raised on this blob — it's worth trying again, maybe a transient issue or a Docling upgrade fixes it." A row at `'requires_user_reupload'` is ALSO re-processed — maybe the user re-uploaded since the last run.

**Manual reset:** Not needed. The two terminal-error states are inherently retryable. If an operator wants to FORCE re-process a `'ready'` row (e.g., post-Docling-upgrade), they manually `UPDATE documents SET content_markdown_status = 'pending' WHERE id = ...` first.

**`failed` vs `requires_user_reupload` operational meaning:**
- `'failed'`: The blob existed but Docling errored on it (e.g., corrupted PDF, unsupported format edge case, OCR crash). Likely to be a CONTENT issue; manual investigation may be warranted.
- `'requires_user_reupload'`: The blob doesn't exist (404 from Storage, or `storage_path IS NULL` — never uploaded). The user is the only one who can fix this by re-uploading. Surfaced in UI badge.

**Confidence:** HIGH. The semantics are clear and the script enforces them.

### §6. Tool integration contract (Phase 4 forward-looking)

**Decision:** Phase 4's `grep` and `read_document` MUST surface non-`'ready'` rows as a structured stub, not silently skip them.

**Exact JSON shape (LOCKED CONTRACT — Phase 4 MUST honor):**

```json
{
  "document_id": "<uuid>",
  "file_name": "<original file name>",
  "scope": "user" | "global",
  "status": "pending_reindex",
  "content_markdown_status": "pending" | "failed" | "requires_user_reupload"
}
```

**Notes for Phase 4 planners:**
- The TOP-LEVEL `status` field is `"pending_reindex"` (always this literal string for any non-`'ready'` row) — distinct from the lower-level `content_markdown_status` field which carries the precise sub-status. This abstraction layer lets the LLM see one consistent flag ("you can't grep this doc yet") while debugging tools can still drill into the specific failure mode.
- `scope` MUST be present (Pitfall 11: every tool result row carries scope, no exceptions).
- Phase 4's response wrapper should add `[N documents pending re-index]` to its summary message so the LLM can mention this to the user.
- This contract MUST be locked in this phase (Phase 2) so Phase 4 can build against a stable interface, even though Phase 4 implements the tools that consume it.

**Confidence:** HIGH for the contract shape; MEDIUM for adoption discipline (depends on Phase 4 actually following it — flag in Phase 4 plan-checker).

### §7. Synchronous-on-upload insertion point

**Decision:** Modify `ingest_document()` (and identically `ingest_document_update()`) to:

1. The existing `text = extract_text(...)` call at `ingestion.py:395` already produces the markdown.
2. The existing UPDATE at `ingestion.py:437-439` that flips `status='ready'` is the right insertion point. Add `content_markdown=text, content_markdown_status='ready'` to the UPDATE dict.
3. The existing error path at `ingestion.py:445-450` that sets `status='failed'` should also set `content_markdown_status='failed'` for consistency with the state machine.

**Re-extraction wasteful?** No — `text` is already in scope as a local variable from line 395. Just thread it through. Zero re-extraction.

**Why not a separate UPDATE before chunks write?** Two UPDATEs = two round-trips = larger window for partial state. Combining into the existing terminal UPDATE is atomic-by-construction.

**Code edit (paste-ready):**

```python
# In ingest_document() at ingestion.py:437-439, REPLACE:
file_hash = compute_file_hash(file_content)
supabase_client.table("documents").update(
    {"status": "ready", "content_hash": file_hash, "updated_at": "now()"}
).eq("id", document_id).execute()
logger.info(f"Ingested document {document_id}: {len(chunks)} chunks")

# WITH:
file_hash = compute_file_hash(file_content)
supabase_client.table("documents").update({
    "status": "ready",
    "content_hash": file_hash,
    "content_markdown": text,
    "content_markdown_status": "ready",
    "updated_at": "now()",
}).eq("id", document_id).execute()
logger.info(
    f"Ingested document {document_id}: {len(chunks)} chunks, "
    f"{len(text)} markdown chars"
)
```

**And in the except block at ingestion.py:445-450, REPLACE:**

```python
supabase_client.table("documents").update(
    {"status": "failed", "error_message": error_msg, "updated_at": "now()"}
).eq("id", document_id).execute()

# WITH:
supabase_client.table("documents").update({
    "status": "failed",
    "content_markdown_status": "failed",
    "error_message": error_msg,
    "updated_at": "now()",
}).eq("id", document_id).execute()
```

**Apply the identical pattern to `ingest_document_update()` at `ingestion.py:512-515` and `:521-524`.**

**Confidence:** HIGH. The insertion points are obvious; the edits are mechanical.

### §8. Logging + observability

**Decision:** Use stdlib `logging.getLogger("backfill")`; no LangSmith.

**Per-document log line shape:**

```
2026-05-04 14:23:11,432 INFO    {document_id} {file_name}: ready ({n_chars} chars, {duration_ms}ms)
2026-05-04 14:23:14,901 INFO    {document_id} {file_name}: requires_user_reupload (no blob)
2026-05-04 14:23:18,221 ERROR   {document_id} {file_name}: failed (RuntimeError: <message>)
```

**Final summary line:**

```
2026-05-04 14:25:00,000 INFO    Backfill complete. Counts: {'ready': 47, 'failed': 2, 'requires_user_reupload': 12, 'skipped': 0}
```

**LangSmith @traceable:** NOT applied. Backfill is operational tooling, not LLM dispatch. Adding `@traceable` would pollute the LangSmith project with hundreds of operational spans.

**Existing `ingest_document()` is NOT @traceable** [VERIFIED: grep `@traceable` in `ingestion.py` returns 0 matches]. Adding it now is out of scope for Phase 2.

**Confidence:** HIGH. Logging-only is the right shape.

### §9. CLI entrypoint convention

**Decision:** Adopt these conventions for `backfill_content_markdown.py`:

| Aspect | Decision | Rationale |
|--------|----------|-----------|
| Shebang / module structure | `if __name__ == "__main__": sys.exit(main())` | Matches `run_migrations.py:65-66` and every script in `backend/scripts/` |
| Argument parsing | `argparse` (NOT click/typer) | Stdlib; no project precedent for click/typer; `argparse` is the safer-default |
| Env loading | `from dotenv import load_dotenv; load_dotenv(...)` | Matches `test_helpers.py:12`; `run_migrations.py` doesn't load .env because DATABASE_URL is operator-supplied |
| Supabase client instantiation | `from supabase import create_client; supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)` | Matches `auth.py:9-12` |
| Working directory assumption | `cd backend && venv/Scripts/python scripts/backfill_content_markdown.py` | Matches CLAUDE.md test invocation convention |
| Python path setup | `sys.path.insert(0, str(Path(__file__).parent.parent))` so `from app.services.ingestion import extract_text` works | Matches `test_helpers.py:6` import pattern |
| Exit codes | 0 = success, 1 = config/setup error, 2 = some rows failed | Standard Unix convention; fits CI gating |

**Flags (recommended set):**

| Flag | Default | Purpose |
|------|---------|---------|
| `--dry-run` | False | Report what would change; do not write |
| `--limit N` | None (unlimited) | Process at most N documents — useful for incremental rollout |
| `--document-id UUID` | None (all eligible) | Process only this document — useful for spot-fixing one row |
| `--scope user\|global\|both` | both | Restrict to one scope — useful for staged rollout |

**Confidence:** HIGH. Patterns are established by existing code.

### §10. Testing strategy

**Decision:** New test file `backend/scripts/test_backfill_content_markdown.py`, registered in `test_all.py`'s `SUITES` list.

**Test surface:**

| Test ID | Type | Behavior tested | File / function |
|---------|------|------------------|-----------------|
| BF-01 | Unit | After `ingest_document()` runs on a small text doc, `content_markdown` is non-NULL and equals Docling's export of that doc | New: `test_backfill_content_markdown.py::test_synchronous_capture_on_upload` — uploads a fixture via the existing `/api/files/upload` endpoint, polls for `status='ready'`, queries the DB, asserts both `content_markdown != NULL` and `content_markdown_status='ready'` |
| BF-02 | Integration | Fixture document with NULL `content_markdown` + Storage blob present → backfill populates correctly, `status='ready'`, no chunk reflow | New: `test_backfill_content_markdown.py::test_backfill_populates_existing_doc` — DB-direct fixture insert with `content_markdown=NULL, content_markdown_status='pending'`, then call the backfill's `_process_one()` directly (or invoke the script via subprocess), assert state |
| BF-04 | Integration | Fixture document with NULL `content_markdown` + Storage blob ABSENT → backfill marks `requires_user_reupload`, no exception escapes | New: same file, `test_backfill_marks_missing_blob_as_user_reupload` — fixture row has `storage_path=NULL` (or invalid path); assert status flips to `'requires_user_reupload'` and counts dict has `requires_user_reupload >= 1` |
| BF-IDEMPOTENCY | Integration | Run backfill twice; second run is a no-op | New: same file, `test_backfill_is_idempotent` — first run flips a fixture to `'ready'`; second run's counts dict has 0 in every key (or `'skipped': 1` if --document-id used) |
| BF-BYTE-EQUIVALENCE | Integration / spot-check | Take 3 freshly-uploaded Phase 2 documents, capture their `content_markdown`, then DELETE `content_markdown` and re-run backfill, assert equivalence ±20 chars | New: same file, `test_backfill_byte_equivalence` — uploads fixtures, captures markdown, nulls the column, re-runs, diff length |
| BF-PITFALL-6-GUARD | Static | Code review assertion: backfill script does NOT contain `string_agg`, `array_agg(content)`, or `chunk` references | New: `test_backfill_no_chunk_stitching` — opens the script as text, asserts forbidden patterns absent |

**Sampling rate:**
- BF-BYTE-EQUIVALENCE: ROADMAP success criterion 4 says "10 random documents." Test fixture creates 3; criterion 4 spot-check is a separate manual operation against production. The 3-doc test catches regressions; the 10-doc spot-check is an operator task documented in the SUMMARY.

**`backend/scripts/test_helpers.py` reuse:**
- `get_auth_token()` for upload via `/api/files/upload`.
- `poll_document_status()` for waiting on the synchronous-on-upload path.
- `get_user_supabase_client(jwt)` for direct DB queries.
- `track_file()` + `cleanup_files()` for scoped cleanup (per CLAUDE.md "Tests must NEVER delete all user data").

**Existing test pattern to mirror:** `test_files.py` (especially the upload + poll-for-ready + DB-verify flow at lines 28-56).

**Confidence:** HIGH for BF-01, BF-02, BF-04, BF-IDEMPOTENCY, BF-PITFALL-6-GUARD; MEDIUM for BF-BYTE-EQUIVALENCE (depends on Docling determinism — see Pitfall 6 above).

### §11. Open questions / planner judgment calls

These are non-blocking — the planner can resolve them in plan files or by surfacing to the user:

1. **Storage Gap resolution (Decision §2).** Surface to user: A vs B vs C. **Highest priority** — no other Phase 2 work is well-defined until this is answered.
2. **`--dry-run` flag.** Recommended INCLUDE — operationally useful for first-run sanity check. Cost: ~5 lines.
3. **`--limit` flag.** Recommended INCLUDE — enables staged rollout. Cost: ~3 lines.
4. **`--document-id` flag.** Recommended INCLUDE — enables spot-fixing. Cost: ~3 lines.
5. **`--scope` flag.** Recommended INCLUDE for parity with the rest of Episode 2's scope-aware patterns; LOW priority. Cost: ~5 lines.
6. **Should the synchronous-on-upload write be wrapped in try/except so a Docling export failure doesn't block ingestion?** **Recommended NO.** If `extract_text()` raises, the entire ingest fails — that's the existing semantic at `ingestion.py:396` (`if not text.strip(): raise ValueError`). Decoupling them creates an inconsistent state where chunks exist but markdown doesn't, which the tools then surface as `pending_reindex` for a doc that's actually fine. Better: chunks and markdown succeed or fail together.
7. **Should the backfill script also re-extract `metadata` and `structured_data`?** **Recommended NO.** Phase 2 is scoped to `content_markdown` only. Re-extracting metadata risks overwriting admin-curated metadata changes. If an operator wants to refresh metadata, that's a separate script.
8. **Pin `docling==2.91.0` in `requirements.txt`?** **Recommended YES** as a Phase 2 task. Eliminates a class of byte-equivalence flakes (Pitfall 6).
9. **Should the backfill script use `concurrent.futures.ThreadPoolExecutor` for parallelism?** **Recommended NO** for v1 — sequential is plenty for an operator-script with low row count and the semaphore is the actual safety net. Defer to v2 if backfill duration becomes a problem.
10. **Should `documents.error_message` be populated on backfill failure?** Currently `error_message` is set only by `ingest_document()`'s outer except [VERIFIED: `ingestion.py:447`]. Recommended: NO — `error_message` is for the chunks/embedding ingestion lifecycle (`status='failed'`); backfill-specific errors should go to logs only. If operators need them in the DB later, add a `content_markdown_error` column in a future migration.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3 | Backfill script + edit to `ingestion.py` | ✓ | (per `backend/venv`) | — |
| Backend `venv` (with `docling`, `supabase`, `python-dotenv`) | Backfill script | ✓ | docling 2.91.0 verified; supabase unpinned | — |
| `backend/.env` with `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` | Backfill script | ✓ (per project standard) | — | Operator must set; backfill exits 1 if missing |
| Supabase Storage bucket `documents` | Decision §2 Option A only | ✗ | — | Decision §2 Option B descopes Storage; Option A requires one-time bucket creation |
| `documents.storage_path` column | Decision §2 Option A only | ✗ | — | Migration 017 adds it (part of Phase 2 if Option A) |
| Live backend running on `localhost:8001` | Tests for BACKFILL-01 (uses `/api/files/upload`) | ✓ (per CLAUDE.md test convention) | — | Same as existing `test_files.py` requirement |

**Missing dependencies with no fallback:**
- (depends on Decision §2 Option A) Supabase Storage bucket `documents` and corresponding `documents.storage_path` column. **Planner must decide §2 BEFORE writing plan files.**

**Missing dependencies with fallback:**
- None — every dependency either exists or has a clean Decision §2-driven path.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | Python stdlib (no pytest); custom test harness in `backend/scripts/test_helpers.py` (`h.test()`, `h.section()`, `h.summary()`) |
| Config file | None — convention-based; tests are scripts that import `test_helpers` |
| Quick run command | `cd backend && venv/Scripts/python scripts/test_backfill_content_markdown.py` |
| Full suite command | `cd backend && venv/Scripts/python scripts/test_all.py` (after registering in SUITES) |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| BACKFILL-01 | New upload populates `content_markdown` synchronously | integration | `cd backend && venv/Scripts/python scripts/test_backfill_content_markdown.py` (test BF-01) | ❌ Wave 0 |
| BACKFILL-02 | Backfill script populates existing pending docs | integration | same script (test BF-02) | ❌ Wave 0 |
| BACKFILL-02 (idempotency) | Second backfill run is a no-op | integration | same script (test BF-IDEMPOTENCY) | ❌ Wave 0 |
| BACKFILL-02 (no chunk stitching) | Static check on script source | unit (file-content assertion) | same script (test BF-PITFALL-6-GUARD) | ❌ Wave 0 |
| BACKFILL-03 | Existing rows have `folder_path='/'`, `scope='user'` | integration (DB query) | `cd backend && venv/Scripts/python scripts/verify_phase2_state.py` (or extend `verify_phase1_schema.py`) | ❌ Wave 0 (or extend existing) |
| BACKFILL-04 | Missing-blob row marked `requires_user_reupload` | integration | same script (test BF-04) | ❌ Wave 0 |
| Success criterion 4 | 10-doc byte-equivalence spot-check | manual + integration sample | manual operator task on production; 3-doc fixture test in same script (BF-BYTE-EQUIVALENCE) | ❌ Wave 0 (3-doc fixture); manual for 10-doc |

### Sampling Rate

- **Per task commit:** `cd backend && venv/Scripts/python scripts/test_backfill_content_markdown.py` (the new test module — runs in <30s if the backend is up).
- **Per wave merge:** `cd backend && venv/Scripts/python scripts/test_all.py` (full suite — registers the new module).
- **Phase gate:** Full suite green AND the 10-doc byte-equivalence spot-check on production / staging is documented as completed in the Phase 2 SUMMARY.

### Wave 0 Gaps

- [ ] `backend/scripts/test_backfill_content_markdown.py` — covers BF-01 / BF-02 / BF-04 / BF-IDEMPOTENCY / BF-PITFALL-6-GUARD / BF-BYTE-EQUIVALENCE
- [ ] Register in `backend/scripts/test_all.py` `SUITES` list
- [ ] (If Option A taken) `backend/scripts/verify_phase2_state.py` or extend `verify_phase1_schema.py` to confirm BACKFILL-03 column defaults landed correctly
- [ ] (If Option A taken) `backend/migrations/017_storage_path.sql` to add `documents.storage_path TEXT`
- [ ] Operator runbook: 10-doc byte-equivalence spot-check procedure (a markdown doc in `.planning/phases/02-content-markdown-backfill-gated/`)

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | Backfill uses service-role key (admin operation); not user-facing |
| V3 Session Management | no | No session in scope |
| V4 Access Control | yes | Backfill bypasses RLS via service-role — same anti-pattern as rest of codebase (`.planning/codebase/CONCERNS.md`); defense-in-depth: backfill explicitly filters `WHERE content_markdown_status <> 'ready'` so it cannot accidentally touch unrelated rows |
| V5 Input Validation | partial | Backfill input is the DB row itself (trusted); the only external input is CLI args parsed by argparse (low risk) |
| V6 Cryptography | no | No new cryptographic operations; existing `compute_file_hash` (SHA-256) untouched |

### Known Threat Patterns for Python + Supabase + Docling

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Service-role key leaked via logs | Information Disclosure | Backfill script does NOT log `SUPABASE_SERVICE_ROLE_KEY`; only loads from `.env` and uses to instantiate client |
| Path traversal via `storage_path` | Tampering | If Decision §2 Option A: `storage_path` is server-generated (`f"{user_id}/{doc_id}.{ext}"`) at upload time, NEVER user-supplied. Backfill reads it verbatim from the DB; no string interpolation needed |
| Docling RCE via crafted PDF | Tampering / Elevation of Privilege | Out of scope — same threat surface as existing `extract_text()` at upload time. Backfill does not introduce new attack surface |
| Service-role key allows any-row mutation | Elevation of Privilege | Defense in depth: backfill `_process_one` filters at the per-row level (only updates rows matching the `--document-id` and `--scope` filters); plus the SQL filter `WHERE content_markdown_status <> 'ready'` |
| Backfill runs against production while users are uploading → Docling OOM → API requests fail | Denial of Service (self-inflicted) | Document in script header: "for production, run during low-traffic window or use `--limit` for incremental rollout" |
| Storage bucket has wrong RLS policy → backfill marks 100% docs `requires_user_reupload` | Availability | One-time canary check at script start: download a known-good blob; abort if download fails with a clear error message pointing at bucket policy |

## Sources

### Primary (HIGH confidence)
- `backend/app/services/ingestion.py` (lines 62-142, 382-450, 453-526) — current Docling pipeline, including the markdown export call at lines 99 and 132 [VERIFIED: file read in this session]
- `backend/app/routers/files.py` (lines 1-118) — current upload path; semaphore at line 11; reveals that no Storage upload happens [VERIFIED: file read in this session]
- `backend/app/services/folder_service.py` — `normalize_path()` chokepoint (Phase 1 / Plan 01) [VERIFIED]
- `backend/migrations/014_content_markdown_column.sql` — landed `content_markdown` + `content_markdown_status` + partial index [VERIFIED]
- `backend/migrations/012_folder_path_and_scope.sql` (lines 13-15) — confirms BACKFILL-03 is a no-op via `NOT NULL DEFAULT '/'/'user'` [VERIFIED]
- `backend/migrations/016_search_indexes.sql` — confirms GIN trigram on `content_markdown` is in place; gates Phase 4 grep [VERIFIED]
- `backend/scripts/run_migrations.py` (lines 1-67) — CLI script convention reference [VERIFIED]
- `backend/scripts/test_helpers.py` (lines 1-232) — test harness conventions for the backfill test module [VERIFIED]
- `backend/scripts/test_files.py` (lines 1-150) — upload-and-poll test pattern to mirror [VERIFIED]
- `backend/scripts/test_all.py` — SUITES registry where the new test module must be registered [VERIFIED]
- `.planning/research/PITFALLS.md` Pitfall 6 (lines 165-196) — the central design constraint of this phase
- `.planning/REQUIREMENTS.md` BACKFILL-01..04 (lines 23-28) and SCHEMA-03 (line 12)
- `.planning/ROADMAP.md` Phase 2 section (lines 50-60) — success criteria
- `backend/venv/Lib/site-packages/docling_core/types/doc/document.py` (lines 5908-5934) — `export_to_markdown()` signature [VERIFIED]
- `pip show docling` output: `docling==2.91.0` [VERIFIED in this session]

### Secondary (MEDIUM confidence)
- `.planning/codebase/INTEGRATIONS.md` (lines 48-50) — claims Storage bucket usage; **CONTRADICTED** by codebase grep, treated as documentation error
- `.planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/04-PLAN.md` — confirms Migration 014's design intent for the partial index [VERIFIED]
- `.planning/STATE.md` (line 91) — confirms "Phase 2: Backfill re-runs Docling against original Storage blobs (NOT chunk stitching); blobs that are GC'd → `requires_user_reupload`" — this is the locked decision the planner must honor while resolving the Storage Gap

### Tertiary (LOW confidence)
- (None — every claim in this RESEARCH.md is either codebase-verified or marked [ASSUMED])

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | The user has not been asked which Storage Gap option (A/B/C) to take. | §2 | If wrong: planner can proceed with the documented user choice. If unaddressed: Wave 1 task ambiguity. **Mitigation: Surface to user before plan-checker.** |
| A2 | A Supabase Storage bucket named `documents` would be the right name (per Decision §2 Option A) | §2, Pattern 2 | LOW. Bucket name is a string; can be changed via constant. |
| A3 | `documents.storage_path` is the right column name (per Decision §2 Option A) | §2, Migration 017 sketch | LOW. Column name is a string; chosen for consistency with project conventions (snake_case, descriptive). |
| A4 | The Storage bucket policy permits service-role download | §2, Pitfall 5 | MEDIUM. If wrong: backfill marks 100% docs `requires_user_reupload`. **Mitigation: canary check at script start.** |
| A5 | Episode 1 production has `< 5000` documents (so backfill duration is bounded by Docling speed × 5000 ÷ semaphore-2 ≈ low-thousands of seconds = under an hour) | §11 (parallelism rec) | LOW. If actual count is higher, the recommendation to NOT add ThreadPoolExecutor still works; just documented runtime estimate is wrong. |
| A6 | Docling 2.91.0's `export_to_markdown()` is deterministic for the same input bytes + same options | §1, Pitfall 6 | MEDIUM. If wrong: success criterion 4 (byte-equivalence) is intermittently flaky. **Mitigation: pin `docling==2.91.0` in requirements.txt.** |
| A7 | The codebase intel claim "Document uploads stored in Supabase buckets" is documentation error, not test environment difference | §2 | LOW. Verified by exhaustive grep of `backend/` source. The intel doc is wrong; trust the code. |
| A8 | Phase 4 will follow the §6 contract (status: 'pending_reindex') | §6 | LOW. Phase 4 plan-checker will catch deviations. |

## Open Questions

1. **Storage Gap resolution (Decision §2 Option A vs B vs C)?**
   - What we know: The codebase has no Storage integration. The "re-run Docling against original blob" plan as stated cannot work for pre-Phase-2 documents.
   - What's unclear: Which option the user prefers — most invasive (A) restores the architecture, simplest (B) sacrifices recovery for delivery speed, deferred (C) restructures the roadmap.
   - Recommendation: **Option A.** Surface to user as the FIRST plan-time question; do not start Wave 1 task execution until answered.

2. **Should `documents.error_message` be populated on backfill failure or only logged?**
   - What we know: `error_message` is currently set by `ingest_document()`'s outer except.
   - What's unclear: Whether operators want backfill failures visible in the DB or only in logs.
   - Recommendation: Logs only for v1; future migration can add a dedicated `content_markdown_error` column if needed.

3. **Should the backfill script support a `--reset-failed` flag that flips `'failed'` rows back to `'pending'`?**
   - What we know: The state machine permits `'failed' → 'ready'` on re-run anyway.
   - What's unclear: Whether operators want to explicitly re-queue specific failed rows.
   - Recommendation: Not for v1 — re-run with `--document-id` provides the same effect.

4. **Should Phase 2 also pin `docling==2.91.0` in `requirements.txt`?**
   - What we know: `docling` is currently unpinned.
   - What's unclear: Whether other Phase 2 components depend on a different Docling version.
   - Recommendation: YES — pin to current installed version (2.91.0). Reduces byte-equivalence flake risk.

5. **For Decision §2 Option A: should existing uploads be retroactively uploaded to Storage?**
   - What we know: Pre-Phase-2 docs have no blob; the user must re-upload.
   - What's unclear: Whether there's a separate path (e.g., admin tool) to bulk re-upload existing docs.
   - Recommendation: NO — the user re-upload path through the existing UI is sufficient; Phase 6 surfaces the badge.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — zero new dependencies; everything verified via `pip show` and source-file grep
- Architecture (synchronous-on-upload): HIGH — insertion points are mechanical; existing markdown export already in scope
- Architecture (backfill of historical docs): MEDIUM-LOW — central Storage Gap is unresolved; planner must answer before Wave 1
- Pitfalls: HIGH — Pitfall 6 is well-documented and the avoidance is structural (use `extract_text()`, not `string_agg`)
- Status state machine: HIGH — Migration 014's enum + the §5 transitions are clean
- Tool-integration contract (§6): HIGH for shape; MEDIUM for adoption (depends on Phase 4 discipline)
- Testing: HIGH — patterns established by `test_files.py`
- Environment availability: MEDIUM — depends on Decision §2 outcome

**Research date:** 2026-05-04
**Valid until:** 2026-06-03 (30 days for stable; the Storage Gap resolution may invalidate parts if architecture shifts)

## RESEARCH COMPLETE

`.planning/phases/02-content-markdown-backfill-gated/02-RESEARCH.md` written. The phase has one trivial requirement (BACKFILL-01: capture and persist the Docling markdown export already in scope as a local variable in `ingest_document()`), one auto-satisfied requirement (BACKFILL-03: already a no-op via Migration 012's `NOT NULL DEFAULT`), one tool-integration contract requirement (BACKFILL-04: locked-shape JSON for Phase 4 to consume), and **one foundational architectural blocker (BACKFILL-02): the codebase has no Supabase Storage integration, so "re-run Docling against the original Storage blob" — the requirement's literal wording — is impossible for pre-Phase-2 documents until a Storage upload path is added or the backfill is descoped to mark all existing docs as `requires_user_reupload`. This Storage Gap is the single load-bearing decision the planner must surface to the user before plan files are written.**
