---
phase: 02
plan: 03
type: execute
wave: 2
depends_on: [01]
files_modified:
  - backend/scripts/backfill_content_markdown.py
autonomous: true
requirements:
  - BACKFILL-02
  - BACKFILL-04
must_haves:
  truths:
    - "backend/scripts/backfill_content_markdown.py exists and is invokable as 'cd backend && venv/Scripts/python scripts/backfill_content_markdown.py [--dry-run] [--limit N] [--document-id UUID] [--purge-orphans]'"
    - "The script selects every documents row WHERE content_markdown_status != 'ready' (using Migration 014's partial index documents_content_markdown_status_idx) — never re-processes already-ready rows (Pitfall 4 mitigation: idempotent re-run is a no-op for ready rows)"
    - "The script reuses extract_text() from app.services.ingestion (per RESEARCH.md §Standard Stack §Alternatives: 'direct call to extract_text() from the backfill script') — does NOT reimplement Docling, does NOT touch document_chunks (Pitfall 6 / RANK 2 mitigation: chunk-stitching forbidden)"
    - "On per-row Docling success: writes content_markdown=<markdown> + content_markdown_status='ready' in a single supabase-py UPDATE (BACKFILL-02 happy path)"
    - "On per-row Storage download failure (blob missing / 404 / Storage misconfigured): writes content_markdown_status='requires_user_reupload' and continues to the next row — never raises out of the loop (BACKFILL-04 path)"
    - "On per-row Docling exception (corrupt blob, unsupported format, OCR crash): writes content_markdown_status='failed' and continues to the next row — never raises out of the loop"
    - "Throttled via a script-local threading.Semaphore(2) matching the live ingestion semaphore capacity from files.py:11 (per CONTEXT.md §LOCKED—Concurrency throttle: 'acquires its own semaphore instance with the same capacity ... different process, different limit')"
    - "Exits 0 on clean run (every row reached a terminal state — ready / requires_user_reupload), exits 1 on missing-env-var or no rows / dry-run, exits 2 if any row reached 'failed' state (Docling exception)"
    - "The --purge-orphans flag is INTERACTIVE: SELECTs candidate rows (content_markdown_status='requires_user_reupload' AND content_markdown IS NULL), prints them in a human-readable table, and requires literal 'y' or 'yes' input before issuing any DELETE (per CLAUDE.md scoped-cleanup rule extended to production scripts; per CONTEXT.md §LOCKED—Episode 1 corpus disposition: 'interactive confirmation before any DELETE')"
    - "The --purge-orphans path deletes only the previously-printed IDs (no DELETE WHERE blanket queries; deletes document_chunks rows for each document first to satisfy any FK)"
    - "End-of-run summary is printed: total processed / ready / requires_user_reupload / failed counts (per CONTEXT.md §LOCKED—Logging)"
    - "Per-row structured log line emitted with: document_id, file_name, status_before, status_after, duration_ms, error_class (per CONTEXT.md §LOCKED—Logging)"
    - "Storage path lookup formula matches Plan 01's upload formula EXACTLY: f'{user_id}/{document_id}{ext}' where ext = os.path.splitext(file_name)[1] (key contract; mismatched formula would silently mark every row as requires_user_reupload)"
    - "Service-role client is instantiated via os.environ['SUPABASE_URL'] + os.environ['SUPABASE_SERVICE_ROLE_KEY'] (matches backend/app/auth.py:8-12); script does NOT import from app.auth (no FastAPI dependency)"
    - "The script does NOT call LangSmith @traceable (per CONTEXT.md §LOCKED—Logging: 'LangSmith @traceable is OUT OF SCOPE for the backfill script')"
    - "The script's first action (BEFORE iterating documents) is a canary Storage download against a known-existing path or a dry-probe; if the bucket is misconfigured, the script aborts with a clear error message pointing to Plan 01 / Migration 018 (Pitfall 5 mitigation)"
  artifacts:
    - path: "backend/scripts/backfill_content_markdown.py"
      provides: "CLI backfill: re-runs Docling against original Supabase Storage blobs to populate documents.content_markdown for every row with content_markdown_status != 'ready'; supports --dry-run, --limit, --document-id, --purge-orphans"
      exports: ["main"]
      contains: "argparse"
      contains_2: "_ingestion_semaphore"
      contains_3: "--dry-run"
      contains_4: "--purge-orphans"
      contains_5: "from app.services.ingestion import extract_text"
      contains_6: "requires_user_reupload"
      contains_7: "neq(\"content_markdown_status\", \"ready\")"
      min_lines: 200
  key_links:
    - from: "backfill_content_markdown.py per-row Docling call"
      to: "app.services.ingestion.extract_text"
      via: "from app.services.ingestion import extract_text — same function the synchronous-on-upload path uses (Plan 02); guarantees byte-equivalence (Phase 2 SC4)"
      pattern: "from app.services.ingestion import extract_text"
    - from: "backfill_content_markdown.py blob download"
      to: "Supabase Storage bucket 'documents' (created by Plan 01 / Migration 018 setup)"
      via: "supabase.storage.from_('documents').download(f'{user_id}/{document_id}{ext}') — exact inverse of Plan 01's upload formula"
      pattern: "supabase.storage.from_(\"documents\").download"
    - from: "backfill_content_markdown.py UPDATEs"
      to: "documents.content_markdown + content_markdown_status (Phase 1 / Migration 014 columns)"
      via: "single supabase-py .update({...}) per row, scoped via .eq('id', doc_id) — service-role bypasses RLS"
      pattern: "neq.*content_markdown_status.*ready"
---

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Operator CLI invocation -> backfill script | Operator-supplied flags (`--purge-orphans`, `--limit`, `--document-id`) gate destructive vs. read-only behavior; `--purge-orphans` is the only flag that can delete data and is gated by interactive confirmation |
| Service-role Supabase client -> documents / storage.objects | Service-role bypasses RLS by design (per CONCERNS.md anti-pattern); defense in depth via `.neq('content_markdown_status', 'ready')` scope filter |
| Storage SDK download -> Docling parse | Untrusted blob bytes flow into Docling; Docling exceptions are caught and surfaced as `content_markdown_status='failed'` (do not propagate out of the loop) |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-2-08 | Tampering / Data Loss | --purge-orphans interactive gate | mitigate | The flag triggers a 4-step ritual per PATTERNS.md §"CLAUDE.md scoped-cleanup rule": (1) SELECT candidates with `content_markdown_status='requires_user_reupload' AND content_markdown IS NULL`; (2) print them in a human-readable table with id/user_id/file_name/created_at; (3) require interactive `input()` returning literal `y` or `yes` (anything else aborts with exit 0); (4) DELETE only the previously-printed IDs (per-row `.delete().eq('id', candidate_id)` — never `.delete().neq(...)` or any blanket query). Cleanup of document_chunks is two-step (chunks first, then documents) so any FK is satisfied without relying on CASCADE. CLAUDE.md "Tests must NEVER delete all user data" extends in spirit to production scripts (per PATTERNS.md). |
| T-2-09 | Information Disclosure / Silent Bypass | RLS bypass via service-role | mitigate | Backfill needs cross-user reads (every user's pending rows) which is impossible under per-user RLS. Service-role is the only client that can do this. Defense in depth: every DB write is `.eq('id', doc_id)` (single row, no broadcast); every SELECT carries `.neq('content_markdown_status', 'ready')` (scope filter — even if a regression introduces a buggy filter, only non-ready rows are eligible for write). The script never UPDATEs other columns (no `documents.scope`, no `documents.user_id`, no `documents.folder_path`) — pure content_markdown / content_markdown_status / updated_at. Minimal blast radius. |
| T-2-10 | Denial of Service | Per-row Docling memory blow-up | mitigate | Per-row processing is sequential under a `threading.Semaphore(2)` matching the live ingestion semaphore (per CONTEXT.md §LOCKED—Concurrency throttle and PATTERNS.md §"Semaphore-throttle pattern"). For MVP the script is single-threaded (semaphore is mostly defensive — see RESEARCH.md §Pitfall 3); if parallelism is added later, max workers cap at 2. 300-second acquire timeout matches files.py:15 — appropriate for OCR-heavy PDFs. Per-row try/except prevents one OOM/timeout from killing the entire run. |
| T-2-11 | Tampering / Misconfiguration | Storage bucket policy blocks service-role download | mitigate | RESEARCH.md §Pitfall 5: a misconfigured `storage.objects` policy can block even the service-role from downloading. Mitigation: the script's first action (after env-var validation) is a CANARY check — attempt to LIST the `documents` bucket (which service-role should always be able to do) and if it fails, abort with exit code 1 and a clear error pointing operators to Plan 01 / Migration 018 setup. Also: if 100% of processed rows in a non-trivial run end up `requires_user_reupload`, the end-of-run summary prints a warning that this likely indicates a bucket misconfiguration rather than a corpus state. |
| T-2-12 | Information Disclosure | Logging of file content | accept | Per-row log lines include `document_id, file_name, blob_size, markdown_chars, duration_ms, error_class` — they do NOT log `content_markdown` content itself. `file_name` may contain operator-meaningful names (e.g. "tax_returns_2025.pdf") which is acceptable operational visibility. Stdlib `logging` to stdout (no LangSmith per CONTEXT.md). |
</threat_model>

<objective>
Deliver BACKFILL-02 (re-run Docling against original Storage blobs to populate `documents.content_markdown` for every existing Episode 1 / future-pending document) and BACKFILL-04 (rows whose source blob is missing get marked `'requires_user_reupload'` and surface in tool results, never silently skipped). Build `backend/scripts/backfill_content_markdown.py` as a standalone, idempotent, throttled CLI: argparse-driven, service-role Supabase client, reuses the existing `extract_text()` from `app.services.ingestion` (so byte-equivalence with synchronous-on-upload markdown holds — Phase 2 SC4), per-row UPDATE with rich logging, end-of-run summary. The `--purge-orphans` flag operationalizes the user-permitted opt-in deletion of Episode 1 orphans (per CONTEXT.md §LOCKED—Episode 1 corpus disposition) under a strict interactive confirmation ritual (per CLAUDE.md "tests must NEVER delete all user data" extended to production scripts).

The script does NOT touch `document_chunks`, `embeddings`, or `record_manager.py` (chunk-stitching is forbidden by Pitfall 6 / RANK 2; chunks are owned by `ingest_document()` only). The script does NOT add LangSmith tracing (offline tool, not an LLM call path). The script does NOT have a `--reset-failed` flag (out of scope per CONTEXT.md §Deferred Ideas). The script does NOT process rows with `content_markdown_status='ready'` (idempotent — Pitfall 4 mitigation).
</objective>

<execution_context>
@.claude/get-shit-done/workflows/execute-plan.md
@.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md

@.planning/phases/02-content-markdown-backfill-gated/02-CONTEXT.md
@.planning/phases/02-content-markdown-backfill-gated/02-RESEARCH.md
@.planning/phases/02-content-markdown-backfill-gated/02-PATTERNS.md
@.planning/research/PITFALLS.md
@.planning/REQUIREMENTS.md
@.planning/codebase/CONVENTIONS.md
@CLAUDE.md

@backend/scripts/run_migrations.py
@backend/scripts/test_helpers.py
@backend/app/services/ingestion.py
@backend/app/auth.py
@backend/app/routers/files.py
@backend/migrations/014_content_markdown_column.sql

@.planning/phases/02-content-markdown-backfill-gated/02-01-PLAN.md
@.planning/phases/02-content-markdown-backfill-gated/02-02-PLAN.md

<interfaces>
<!-- Contracts this plan produces and consumes. -->

CLI surface (operator-facing):
```
cd backend && venv/Scripts/python scripts/backfill_content_markdown.py [OPTIONS]

Options:
  --dry-run                 Print what would change without writing. Exits 0.
  --limit N                 Process at most N rows. Operator-safety on large corpora.
  --document-id UUID        Spot-fix one document by id (still respects 'skip if already ready').
  --purge-orphans           Interactive: list rows with content_markdown_status='requires_user_reupload'
                            AND content_markdown IS NULL, then ask for explicit y/N before DELETE.

Exit codes:
  0  All rows reached a terminal non-failed state (ready / requires_user_reupload), or --dry-run completed.
  1  Missing env vars (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY) or canary Storage check failed.
  2  At least one row ended in 'failed' state (Docling exception during run).
```

Storage path formula (MUST be identical to Plan 01's upload formula):
```python
import os
ext = os.path.splitext(file_name)[1]
storage_path = f"{user_id}/{document_id}{ext}"
blob_bytes = supabase.storage.from_("documents").download(storage_path)
```

Per-row UPDATE shapes (use exactly these key sets — never extra columns):
```python
# Success
supabase.table("documents").update({
    "content_markdown": markdown,
    "content_markdown_status": "ready",
    "updated_at": "now()",
}).eq("id", doc_id).execute()

# Blob missing
supabase.table("documents").update({
    "content_markdown_status": "requires_user_reupload",
    "updated_at": "now()",
}).eq("id", doc_id).execute()

# Docling failure
supabase.table("documents").update({
    "content_markdown_status": "failed",
    "updated_at": "now()",
}).eq("id", doc_id).execute()
```

SELECT shape (idempotent scan; uses Migration 014 partial index documents_content_markdown_status_idx):
```python
supabase.table("documents").select(
    "id, user_id, file_name, mime_type, content_markdown_status"
).neq("content_markdown_status", "ready") \
 .order("created_at", desc=False) \
 .execute()
```

Per-row log line format (matches CONTEXT.md §LOCKED—Logging):
```
[OK]   doc=<uuid> file=<name> blob_size=<n> docling_ms=<n> markdown_chars=<n>
[REUP] doc=<uuid> file=<name> reason=blob_missing
[FAIL] doc=<uuid> file=<name> err=<ExceptionClass>: <message>
[SKIP] doc=<uuid> file=<name> reason=already_ready  # only when --document-id points at a ready row
```

End-of-run summary line:
```
Backfill complete. processed=<n> ready=<n> requires_user_reupload=<n> failed=<n> skipped=<n>
```
</interfaces>
</context>

<tasks>

<task id="2-03-01" type="auto">
  <name>Task 1: Write backfill_content_markdown.py CLI script (argparse, service-role client, per-row Docling re-run, --purge-orphans interactive ritual)</name>
  <files>backend/scripts/backfill_content_markdown.py</files>
  <read_first>
    - backend/scripts/run_migrations.py (PRIMARY analog — module-docstring-then-imports-then-main shape; `main() -> int` returning exit codes; `sys.exit(main())` at module bottom; per CONVENTIONS.md and PATTERNS.md §"Module docstring + imports pattern")
    - backend/app/services/ingestion.py L62-142 (`extract_text(file_content, mime_type, file_name) -> str` — the function this script reuses; verifies the signature so import + call shape are correct) AND L19 (`logger = logging.getLogger(__name__)` — module-logger pattern to mirror)
    - backend/app/routers/files.py L11-27 (`_ingestion_semaphore = threading.Semaphore(2)` and `_throttled_ingest` helper — the throttle pattern this script mirrors with its own `_backfill_semaphore = threading.Semaphore(2)` per CONTEXT.md §LOCKED—Concurrency throttle)
    - backend/app/routers/files.py (full file — Plan 01's `_upload_to_storage` helper defines the EXACT path formula `f"{user_id}/{document_id}{ext}"` with `ext = os.path.splitext(file_name)[1]`; this script's download MUST use the identical formula)
    - backend/app/auth.py L8-12 (`get_supabase_client()` — service-role client instantiation pattern: `create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])`; this script reuses the pattern but does NOT import from app.auth — keeps the script free of FastAPI dependency)
    - backend/scripts/test_helpers.py L10-12 (`from dotenv import load_dotenv; load_dotenv(...)` — the env-loading convention the backfill script also uses)
    - backend/scripts/test_two_scope_rls.py L28-37 (`sys.path.insert` two-step bootstrap — first puts scripts/ on path for sibling imports, second puts backend/ on path so `app.services.ingestion` resolves)
    - backend/migrations/014_content_markdown_column.sql (the canonical 4-element vocabulary `'pending' | 'ready' | 'failed' | 'requires_user_reupload'` — DO NOT introduce 'ok' or 'processing' values; the partial index this script's SELECT relies on)
    - .planning/phases/02-content-markdown-backfill-gated/02-CONTEXT.md (ALL §LOCKED sections — Storage Gap Resolution, Episode 1 corpus disposition, Backfill scope reframe, Synchronous-on-upload, Status state machine, Tool integration contract, Concurrency throttle, Logging, Forward-compatibility) — these are the contracts this script implements
    - .planning/phases/02-content-markdown-backfill-gated/02-PATTERNS.md §"backend/scripts/backfill_content_markdown.py" (lines ~30-180 — paste-ready imports, main() shape, semaphore pattern, argparse pattern, --purge-orphans ritual)
    - .planning/phases/02-content-markdown-backfill-gated/02-RESEARCH.md §Pattern 2 (lines ~270-453 — full skeleton including _process_one, _process_throttled, main; references to RESEARCH.md Anti-Patterns) AND §"Common Pitfalls" Pitfalls 1, 3, 4, 5, 6, 7 (chunk-stitching forbidden; OOM via parallelism; idempotency via .neq filter; bucket policy canary; Docling version pin; tmpfile leak)
    - .planning/research/PITFALLS.md §Pitfall 6 (RANK 2 — chunk-stitching forbidden; the central design constraint)
    - CLAUDE.md ("Python backend uses venv"; "No LangChain/LangGraph"; "Tests must NEVER delete all user data" — extends to production scripts per PATTERNS.md §"CLAUDE.md scoped-cleanup rule"; "NEVER DELETE FROM or TRUNCATE in migrations" — script-side deletes are allowed but must be opt-in + interactive)
  </read_first>
  <action>
    Create `backend/scripts/backfill_content_markdown.py` with the following structure. Paste this script verbatim, adjusting only what the comments mark as TUNABLE.

```python
"""Backfill documents.content_markdown for existing Episode 1 (and any post-Phase-2 pending) documents.

Re-runs Docling against the original Supabase Storage blob (uploaded by Plan 01's
files.py::_upload_to_storage at documents/{user_id}/{document_id}{ext}) and persists
the canonical markdown export to documents.content_markdown. Idempotent — only
processes rows where content_markdown_status != 'ready' (uses the partial index
documents_content_markdown_status_idx from Phase 1 / Migration 014).

Usage:
    cd backend && venv/Scripts/python scripts/backfill_content_markdown.py
    cd backend && venv/Scripts/python scripts/backfill_content_markdown.py --dry-run
    cd backend && venv/Scripts/python scripts/backfill_content_markdown.py --limit 10
    cd backend && venv/Scripts/python scripts/backfill_content_markdown.py --document-id <uuid>
    cd backend && venv/Scripts/python scripts/backfill_content_markdown.py --purge-orphans

Required env vars (loaded from backend/.env via python-dotenv):
    SUPABASE_URL                  e.g. https://<project>.supabase.co
    SUPABASE_SERVICE_ROLE_KEY     service-role key (bypasses RLS — required for cross-user backfill)

Exit codes:
    0   Clean run (every row reached a terminal state: ready / requires_user_reupload), or --dry-run completed.
    1   Missing env vars OR canary Storage bucket access failed (likely Plan 01 / Migration 018 not applied).
    2   At least one row ended in 'failed' state (Docling exception during this run).

Idempotency: re-running the script after a successful pass is a no-op (every row is now 'ready').
Throttle: per-row Docling runs are gated by a script-local threading.Semaphore(2) matching the
          live ingestion semaphore (files.py:11). MVP is single-threaded; semaphore is defensive.
Forbidden: chunk-stitching from document_chunks (Pitfall 6 / RANK 2). This script ALWAYS re-runs
          Docling via app.services.ingestion.extract_text — never queries document_chunks.

The --purge-orphans flag is the user-permitted opt-in cleanup of Episode 1 documents whose
Storage blob is missing AND content_markdown is NULL. It SELECTs candidates, prints them in a
human-readable table, and requires interactive 'y' or 'yes' before any DELETE. Per CLAUDE.md
'Tests must NEVER delete all user data' (extended to production scripts), the DELETEs are
strictly per-id; no blanket DELETE WHERE queries are issued.
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

# Two-step sys.path bootstrap: scripts/ first (for sibling imports if any),
# then backend/ so that `from app.services.ingestion import extract_text` resolves.
# Mirrors backend/scripts/test_two_scope_rls.py:32-37.
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from app.services.ingestion import extract_text  # reuses Docling pipeline (PATTERNS.md / RESEARCH.md §Alternatives)

load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
)
logger = logging.getLogger("backfill")

# Same throttle capacity as files.py:11 (Semaphore(2)). Per CONTEXT.md §LOCKED—Concurrency
# throttle: this is the script's OWN semaphore (different process from the API server),
# the SAME capacity. RESEARCH.md §Pitfall 3 warns against parallelism above this.
_backfill_semaphore = threading.Semaphore(2)

STORAGE_BUCKET = "documents"  # MUST match Plan 01 / Migration 018 bucket name


def _storage_path_for(user_id: str, document_id: str, file_name: str) -> str:
    """Compute the Storage path. MUST mirror files.py::_upload_to_storage exactly.

    Plan 01 contract: documents/{user_id}/{document_id}{ext} where
    ext = os.path.splitext(file_name)[1] (includes leading dot, e.g. '.pdf' or '').
    """
    ext = os.path.splitext(file_name or "")[1]
    return f"{user_id}/{document_id}{ext}"


def _canary_storage_check(supabase) -> bool:
    """Verify the 'documents' bucket is reachable BEFORE iterating rows.

    Per RESEARCH.md §Pitfall 5, a misconfigured bucket policy makes every download
    fail and the script silently marks every row 'requires_user_reupload'. Catching
    the misconfiguration up front saves an entire run + manual rollback.

    Returns True if the bucket is reachable. Returns False (and logs) otherwise.
    """
    try:
        # list() is the cheapest service-role read against the bucket; it returns []
        # for an empty/new bucket but does not 404 if the bucket exists.
        supabase.storage.from_(STORAGE_BUCKET).list(path="", options={"limit": 1})
        logger.info(f"Canary OK: bucket '{STORAGE_BUCKET}' is reachable")
        return True
    except Exception as e:
        logger.error(
            f"Canary FAILED: bucket '{STORAGE_BUCKET}' is not reachable ({type(e).__name__}: {e}). "
            f"Check that Plan 01 created the bucket in Supabase Studio AND Migration 018 RLS policies are applied."
        )
        return False


def _download_blob(supabase, user_id: str, document_id: str, file_name: str) -> bytes | None:
    """Download the original blob for a document. Returns bytes or None if missing."""
    storage_path = _storage_path_for(user_id, document_id, file_name)
    try:
        return supabase.storage.from_(STORAGE_BUCKET).download(storage_path)
    except Exception as e:
        logger.warning(f"Storage download failed for {document_id} path={storage_path}: {type(e).__name__}: {e}")
        return None


def _process_one(supabase, row: dict, dry_run: bool) -> str:
    """Process one document row.

    Returns one of {'ready', 'requires_user_reupload', 'failed', 'skipped'}.
    Never raises (per RESEARCH.md Anti-Patterns: only Docling exceptions cause 'failed';
    everything else either returns a status or is logged as warning and continues).
    """
    doc_id = row["id"]
    user_id = row.get("user_id")
    file_name = row.get("file_name") or "<unknown>"
    mime_type = row.get("mime_type") or "application/octet-stream"
    status_before = row.get("content_markdown_status", "pending")

    # Defense in depth (Pitfall 4): if --document-id pointed at a ready row, do nothing.
    if status_before == "ready":
        logger.info(f"[SKIP] doc={doc_id} file={file_name} reason=already_ready")
        return "skipped"

    started = time.monotonic()

    # Step 1: download the original blob.
    blob = _download_blob(supabase, user_id=user_id, document_id=doc_id, file_name=file_name)
    if blob is None:
        if dry_run:
            logger.info(f"[REUP] [DRY] doc={doc_id} file={file_name} reason=blob_missing")
            return "requires_user_reupload"
        try:
            supabase.table("documents").update({
                "content_markdown_status": "requires_user_reupload",
                "updated_at": "now()",
            }).eq("id", doc_id).execute()
        except Exception as e:
            logger.error(f"DB update failed for {doc_id} (requires_user_reupload): {type(e).__name__}: {e}")
        logger.info(f"[REUP] doc={doc_id} file={file_name} reason=blob_missing")
        return "requires_user_reupload"

    # Step 2: re-run Docling via the canonical extract_text() (Pitfall 6: NEVER stitch from chunks).
    try:
        markdown = extract_text(blob, mime_type, file_name)
        if not markdown or not markdown.strip():
            raise ValueError("Docling returned empty markdown")
    except Exception as e:
        if dry_run:
            logger.error(f"[FAIL] [DRY] doc={doc_id} file={file_name} err={type(e).__name__}: {e}")
            return "failed"
        try:
            supabase.table("documents").update({
                "content_markdown_status": "failed",
                "updated_at": "now()",
            }).eq("id", doc_id).execute()
        except Exception as inner:
            logger.error(f"DB update failed for {doc_id} (failed): {type(inner).__name__}: {inner}")
        logger.error(f"[FAIL] doc={doc_id} file={file_name} err={type(e).__name__}: {e}")
        return "failed"

    duration_ms = int((time.monotonic() - started) * 1000)

    # Step 3: write content_markdown + flip status='ready'.
    if dry_run:
        logger.info(
            f"[OK] [DRY] doc={doc_id} file={file_name} blob_size={len(blob)} "
            f"docling_ms={duration_ms} markdown_chars={len(markdown)}"
        )
        return "ready"
    try:
        supabase.table("documents").update({
            "content_markdown": markdown,
            "content_markdown_status": "ready",
            "updated_at": "now()",
        }).eq("id", doc_id).execute()
    except Exception as e:
        logger.error(f"DB update failed for {doc_id} (ready): {type(e).__name__}: {e}")
        return "failed"

    logger.info(
        f"[OK] doc={doc_id} file={file_name} blob_size={len(blob)} "
        f"docling_ms={duration_ms} markdown_chars={len(markdown)}"
    )
    return "ready"


def _process_throttled(supabase, row: dict, dry_run: bool, counts: dict) -> None:
    """Acquire the semaphore around _process_one. Increments counts in place."""
    acquired = _backfill_semaphore.acquire(timeout=600)
    try:
        if not acquired:
            logger.error(f"semaphore timeout for doc={row.get('id')}")
            counts["timeout"] = counts.get("timeout", 0) + 1
            return
        outcome = _process_one(supabase, row, dry_run)
        counts[outcome] = counts.get(outcome, 0) + 1
    finally:
        if acquired:
            _backfill_semaphore.release()


def _purge_orphans(supabase, dry_run: bool) -> int:
    """Interactive: SELECT orphans (requires_user_reupload + content_markdown IS NULL),
    print them, ask for explicit 'y'/'yes' confirmation, then DELETE only those IDs.

    Per CLAUDE.md scoped-cleanup rule extended to production scripts:
      - SELECT first
      - Print all candidates
      - Require interactive y/N
      - DELETE only the previously-printed IDs (no blanket DELETE WHERE)
      - Cleanup document_chunks for each ID first (defensive against absent FK CASCADE)

    Returns process exit code (0 on clean operation including operator-abort).
    """
    candidates = supabase.table("documents").select(
        "id, user_id, file_name, created_at"
    ).eq("content_markdown_status", "requires_user_reupload") \
     .is_("content_markdown", "null") \
     .order("created_at", desc=False).execute().data or []

    if not candidates:
        print("No orphan documents to purge (no rows with content_markdown_status='requires_user_reupload' AND content_markdown IS NULL).")
        return 0

    print(f"\nFound {len(candidates)} orphan document(s) (no Storage blob, NULL content_markdown):")
    print(f"{'id':<38} {'user_id':<38} {'created_at':<28} file_name")
    print("-" * 130)
    for d in candidates:
        print(f"{d['id']:<38} {str(d.get('user_id') or '<global>'):<38} {str(d.get('created_at') or ''):<28} {d.get('file_name') or '<unknown>'}")

    if dry_run:
        print(f"\n[DRY RUN] Would prompt for confirmation, then DELETE {len(candidates)} document(s) and their chunks. No writes performed.")
        return 0

    answer = input(f"\nDELETE these {len(candidates)} rows and their document_chunks? Type 'y' or 'yes' to proceed, anything else to abort: ").strip().lower()
    if answer not in ("y", "yes"):
        print("Aborted. No rows deleted.")
        return 0

    deleted = 0
    for d in candidates:
        did = d["id"]
        try:
            # Two-step delete: chunks first, then document. No blanket queries.
            supabase.table("document_chunks").delete().eq("document_id", did).execute()
            supabase.table("documents").delete().eq("id", did).execute()
            deleted += 1
            print(f"  deleted: {did}")
        except Exception as e:
            print(f"  FAILED to delete {did}: {type(e).__name__}: {e}")
    print(f"\nPurge complete. Deleted {deleted} of {len(candidates)} orphan document(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill documents.content_markdown for existing Episode 1 documents (BACKFILL-02 + BACKFILL-04).",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="[DRY RUN] Print what would change without writing. Exits 0.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N rows (operator-safety on large corpora).")
    parser.add_argument("--document-id", type=str, default=None,
                        help="Spot-fix a single row by UUID (still skips if already ready).")
    parser.add_argument("--purge-orphans", action="store_true",
                        help="Interactive: list rows with content_markdown_status='requires_user_reupload' AND "
                             "content_markdown IS NULL, then ask for explicit y/N before DELETE.")
    args = parser.parse_args()

    if args.dry_run:
        print("[DRY RUN] No writes will be performed.")

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        logger.error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY required (set in backend/.env)")
        return 1

    supabase = create_client(url, key)

    # Pitfall 5 mitigation: canary Storage check BEFORE iterating any rows.
    if not args.purge_orphans:
        if not _canary_storage_check(supabase):
            return 1

    # --purge-orphans is mutually exclusive with the normal backfill loop.
    if args.purge_orphans:
        return _purge_orphans(supabase, args.dry_run)

    # Build the SELECT (idempotent: scope filter via .neq is the canonical idempotency record per RESEARCH.md §3).
    query = supabase.table("documents").select(
        "id, user_id, file_name, mime_type, content_markdown_status"
    ).neq("content_markdown_status", "ready").order("created_at", desc=False)

    if args.document_id:
        query = query.eq("id", args.document_id)
    if args.limit:
        query = query.limit(args.limit)

    result = query.execute()
    rows = result.data or []
    logger.info(
        f"Found {len(rows)} document(s) needing backfill "
        f"(dry_run={args.dry_run}, limit={args.limit}, document_id={args.document_id})"
    )

    if not rows:
        print("Backfill complete. processed=0 ready=0 requires_user_reupload=0 failed=0 skipped=0")
        return 0 if not args.dry_run else 0

    counts: dict = {}
    for row in rows:
        # Sequential under semaphore — see RESEARCH.md §Pitfall 3 (parallelism risks OOM).
        _process_throttled(supabase, row, args.dry_run, counts)

    summary = (
        f"Backfill complete. processed={len(rows)} "
        f"ready={counts.get('ready', 0)} "
        f"requires_user_reupload={counts.get('requires_user_reupload', 0)} "
        f"failed={counts.get('failed', 0)} "
        f"skipped={counts.get('skipped', 0)}"
    )
    logger.info(summary)
    print(summary)

    # Operator-safety warning (Pitfall 5): if everything became 'requires_user_reupload',
    # the bucket is likely misconfigured rather than the corpus actually being orphaned.
    n_reup = counts.get("requires_user_reupload", 0)
    if len(rows) >= 5 and n_reup == len(rows):
        logger.warning(
            "All processed rows ended at 'requires_user_reupload' — this likely indicates a "
            "Storage bucket misconfiguration rather than a real corpus state. Verify the "
            "'documents' bucket exists and Migration 018 RLS policies are applied."
        )

    return 0 if counts.get("failed", 0) == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
```

    Conventions to honor (per CONVENTIONS.md, PATTERNS.md, and CONTEXT.md):
    - Module docstring on lines 1-N: triple-quoted, includes Usage block with the exact `cd backend && venv/Scripts/python …` Windows invocation, env-var requirements, exit-code semantics, idempotency / throttle / forbidden notes.
    - Imports: stdlib first (argparse, logging, os, sys, threading, time, pathlib), then third-party (dotenv, supabase). `from app.services.ingestion import extract_text` comes AFTER the sys.path bootstrap.
    - Module-level `logger = logging.getLogger("backfill")` (named "backfill" so log lines are clearly attributed; matches RESEARCH.md §Pattern 2).
    - `main() -> int` returning exit code; `sys.exit(main())` at module bottom (matches `run_migrations.py:62-66`).
    - Service-role client: direct `os.environ[...]` access (KeyError raises if missing) inside main(); script does NOT import from `app.auth`.
    - Status vocabulary EXACTLY: `'ready'`, `'failed'`, `'requires_user_reupload'`, `'skipped'` (the last is an internal-only outcome label, not a DB value). DO NOT write `'pending'` (DB default; script never resets to it). DO NOT write `'ok'` (canonical is `'ready'`).
    - Per-row UPDATE writes ONLY content_markdown / content_markdown_status / updated_at — NEVER touches scope, user_id, folder_path, status (the `status` column is for chunks-pipeline lifecycle per RESEARCH.md Anti-Patterns: "Updating documents.status from the backfill script... that column belongs to the chunks/embeddings ingestion lifecycle").
    - Storage path computed via the `_storage_path_for()` helper that delegates to the same `os.path.splitext(file_name)[1]` formula as Plan 01's `_upload_to_storage`. This is a HARD contract — mismatched formulas would silently break BACKFILL-02.

    Do NOT:
    - Import or query `document_chunks` (Pitfall 6 / RANK 2 — chunk-stitching forbidden).
    - Add LangSmith `@traceable` (per CONTEXT.md §LOCKED—Logging).
    - Use `concurrent.futures.ThreadPoolExecutor` or any parallelism above the Semaphore(2) cap (Pitfall 3).
    - Add a `--reset-failed` flag (per CONTEXT.md §Deferred Ideas).
    - Update `documents.status` (per RESEARCH.md Anti-Patterns).
    - Auto-purge orphans without `--purge-orphans` (per CONTEXT.md §Deferred Ideas: "Auto-cleanup of orphaned rows on script run (without --purge-orphans flag) — explicitly rejected").
    - Add LangChain or LangGraph (project rule).
    - Use `psycopg2` directly (use supabase-py per RESEARCH.md §Standard Stack §Alternatives).
    - Persist a `documents.storage_path` column (Plan 01's contract — computed-from-id).
    - Catch `KeyboardInterrupt` / `SystemExit` in the per-row loop (let those propagate; only Docling exceptions become 'failed').
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -c "import ast, pathlib; src = pathlib.Path('scripts/backfill_content_markdown.py').read_text(encoding='utf-8'); ast.parse(src); body = '\n'.join(line for line in src.splitlines() if not line.lstrip().startswith('#')); assert 'argparse' in body and 'def main()' in body and 'sys.exit(main())' in body, 'CLI shape missing'; assert 'from app.services.ingestion import extract_text' in body, 'must reuse extract_text from ingestion.py'; assert 'threading.Semaphore(2)' in body, 'semaphore throttle missing'; assert '--dry-run' in body and '--limit' in body and '--document-id' in body and '--purge-orphans' in body, 'CLI flags missing'; assert 'requires_user_reupload' in body, 'requires_user_reupload status not handled'; assert 'document_chunks' not in body or 'document_chunks' in body and 'delete' in body.lower(), 'document_chunks may only appear in the --purge-orphans delete path'; assert 'string_agg' not in body and 'array_agg' not in body, 'chunk-stitching forbidden (Pitfall 6)'; assert '@traceable' not in body and 'langsmith' not in body.lower(), 'LangSmith out of scope'; assert 'os.path.splitext' in body, 'storage path formula missing splitext'; assert 'storage.from_' in body, 'Storage SDK call missing'; assert 'create_client' in body and 'SUPABASE_SERVICE_ROLE_KEY' in body, 'service-role client missing'; assert body.count('.neq(\"content_markdown_status\", \"ready\")') &gt;= 1, 'idempotent .neq filter missing'; assert 'input(' in body, '--purge-orphans interactive input() missing'; print('backfill script structure OK')"</automated>
  </verify>
  <acceptance_criteria>
    - File `backend/scripts/backfill_content_markdown.py` exists.
    - File parses as valid Python (`ast.parse` succeeds).
    - `grep -c "import argparse" backend/scripts/backfill_content_markdown.py` returns 1.
    - `grep -c "from app.services.ingestion import extract_text" backend/scripts/backfill_content_markdown.py` returns 1.
    - `grep -c "threading.Semaphore(2)" backend/scripts/backfill_content_markdown.py` returns 1.
    - `grep -c "STORAGE_BUCKET = \"documents\"" backend/scripts/backfill_content_markdown.py` returns 1.
    - `grep -c "def main()" backend/scripts/backfill_content_markdown.py` returns 1.
    - `grep -c "sys.exit(main())" backend/scripts/backfill_content_markdown.py` returns 1.
    - `grep -c -- "--dry-run" backend/scripts/backfill_content_markdown.py` returns at least 1.
    - `grep -c -- "--limit" backend/scripts/backfill_content_markdown.py` returns at least 1.
    - `grep -c -- "--document-id" backend/scripts/backfill_content_markdown.py` returns at least 1.
    - `grep -c -- "--purge-orphans" backend/scripts/backfill_content_markdown.py` returns at least 1.
    - `grep -c "requires_user_reupload" backend/scripts/backfill_content_markdown.py` returns at least 4 (status writes, log messages, doc string).
    - `grep -c "neq(\"content_markdown_status\", \"ready\")" backend/scripts/backfill_content_markdown.py` returns at least 1 (idempotent SELECT filter).
    - `grep -v '^[[:space:]]*#' backend/scripts/backfill_content_markdown.py | grep -E "string_agg|array_agg"` returns no matches (Pitfall 6: no chunk-stitching).
    - `grep -E "from\s+(langchain\|langgraph)" backend/scripts/backfill_content_markdown.py` returns no matches (project rule).
    - `grep -E "@traceable|langsmith" backend/scripts/backfill_content_markdown.py` returns no matches (LangSmith out of scope per CONTEXT.md).
    - `grep -c "input(" backend/scripts/backfill_content_markdown.py` returns at least 1 (--purge-orphans interactive ritual).
    - `grep -c "supabase.storage.from_(" backend/scripts/backfill_content_markdown.py` returns at least 2 (canary list + per-row download).
    - `grep -c "os.path.splitext" backend/scripts/backfill_content_markdown.py` returns at least 1 (storage path formula must match Plan 01).
    - `grep -c "create_client" backend/scripts/backfill_content_markdown.py` returns 1 (service-role client).
    - `grep -c "SUPABASE_SERVICE_ROLE_KEY" backend/scripts/backfill_content_markdown.py` returns at least 1.
    - `grep -E "from app.auth import" backend/scripts/backfill_content_markdown.py` returns no matches (script must NOT depend on app.auth — keeps it free of FastAPI dependency).
    - `grep -c "_canary_storage_check" backend/scripts/backfill_content_markdown.py` returns at least 2 (def + call site — Pitfall 5 mitigation).
    - `cd backend && venv/Scripts/python scripts/backfill_content_markdown.py --help` exits 0 and prints the `--dry-run`, `--limit`, `--document-id`, and `--purge-orphans` flags in the help text.
    - `cd backend && venv/Scripts/python -c "import sys, os; sys.path.insert(0, 'scripts'); sys.path.insert(0, '.'); from backfill_content_markdown import _storage_path_for, main; assert _storage_path_for('u1', 'd1', 'a.pdf') == 'u1/d1.pdf'; assert _storage_path_for('u1', 'd1', 'Makefile') == 'u1/d1'; assert _storage_path_for('u1', 'd1', 'capybara_facts.txt') == 'u1/d1.txt'; print('storage path formula OK')"` prints "storage path formula OK".
  </acceptance_criteria>
  <done>
    `backend/scripts/backfill_content_markdown.py` exists as an idempotent CLI that re-runs Docling against original Storage blobs (Plan 01's upload contract) for every documents row with content_markdown_status != 'ready'. Reuses `extract_text()` from `app.services.ingestion`. Throttled via `threading.Semaphore(2)`. Supports `--dry-run`, `--limit`, `--document-id`, `--purge-orphans`. Per-row failures are surfaced as `requires_user_reupload` (blob missing) or `failed` (Docling exception) — never silently skipped, never raise out of the loop. The `--purge-orphans` flag is interactive (SELECT, print, prompt y/N, then per-id DELETE — no blanket queries). End-of-run summary printed. Exit code 0 on clean run, 1 on env / canary failure, 2 if any row ended in 'failed'. Script never touches `document_chunks` outside the `--purge-orphans` cleanup path. Script does NOT add LangSmith tracing. Script's `--help` output is invokable via venv Python.
  </done>
</task>

</tasks>

<verification>
This plan delivers BACKFILL-02 (re-run Docling against Storage blobs; idempotent, throttled, status-tracked) and BACKFILL-04 (rows whose blob is missing get `requires_user_reupload` and surface to tools). It depends on Plan 01 (Storage upload contract) and Plan 02 (synchronous-on-upload contract for byte-equivalence).

Verification steps:
- Static structure: Python AST parse + grep gates confirm CLI shape, `extract_text` reuse, semaphore throttle, all four flags, no chunk-stitching, no LangSmith, idempotent SELECT filter, interactive input.
- CLI invocability: `--help` prints usage with all four flags.
- Path formula determinism: `_storage_path_for('u1', 'd1', 'a.pdf')` returns `'u1/d1.pdf'` (asserts the formula matches Plan 01's `_upload_to_storage`).
- Operational verification (deferred to Plan 04 integration test): a real document with a Storage blob has its content_markdown populated by a backfill run; a real document WITHOUT a Storage blob is marked `requires_user_reupload`; running backfill twice is a no-op the second time.
</verification>

<success_criteria>
- BACKFILL-02 satisfied: `backend/scripts/backfill_content_markdown.py` re-runs Docling for every documents row needing backfill, idempotent, throttled, logs counts.
- BACKFILL-04 satisfied: rows whose blob is missing get marked `'requires_user_reupload'` (not silently skipped, not raising); status surfaces correctly so Phase 4 tools can return `pending_reindex` per CONTEXT.md §LOCKED—Tool integration contract.
- Per-row Docling success / blob-missing / Docling-exception are all observable via the per-row log lines AND the end-of-run summary.
- The `--purge-orphans` flag implements the user-permitted opt-in deletion of Episode 1 orphans under interactive confirmation (CLAUDE.md scoped-cleanup rule).
- The script never touches `document_chunks` outside the `--purge-orphans` cleanup (Pitfall 6 mitigation).
- The script never adds LangSmith tracing (CONTEXT.md §LOCKED—Logging).
- The script reuses `extract_text()` from `app.services.ingestion` so the markdown produced is byte-equivalent to the synchronous-on-upload markdown from Plan 02 (Phase 2 SC4 precondition).
</success_criteria>

<output>
After completion, create `.planning/phases/02-content-markdown-backfill-gated/02-03-SUMMARY.md` recording: file created, line count, the four CLI flags and their semantics, the canary check rationale (Pitfall 5), the Storage path formula `f"{user_id}/{doc_id}{ext}"` (and the contract that Plan 04's test will assert), the exact UPDATE shapes for each terminal status, the end-of-run summary format, and the operational note that operators must apply Migration 018 + create the Studio bucket before invoking the script.
</output>
</content>
