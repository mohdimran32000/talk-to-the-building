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

Phase 4 forward contract: when grep / read_document encounter a row with content_markdown_status
!= 'ready' they return {status: 'pending_reindex', content_markdown_status: <status>}. Any change
to the status vocabulary written here ('ready' / 'failed' / 'requires_user_reupload') must be
coordinated with that contract (per CONTEXT.md §LOCKED—Tool integration contract).
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

# Load env BEFORE importing app.services.ingestion, because that module instantiates
# google-genai's Client at import time using os.environ.get("GEMINI_API_KEY").
load_dotenv(Path(__file__).parent.parent / ".env")

from app.services.ingestion import extract_text  # noqa: E402  reuses Docling pipeline (PATTERNS.md / RESEARCH.md §Alternatives)

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


def _download_blob(supabase, user_id: str, document_id: str, file_name: str):
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
        return 0

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
