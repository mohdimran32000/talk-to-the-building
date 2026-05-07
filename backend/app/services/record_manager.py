"""
Record Manager — content hashing and deduplication for document ingestion.
Determines whether an upload should be created, skipped, or updated.
"""
import hashlib
from dataclasses import dataclass
from typing import Optional


@dataclass
class RecordAction:
    action: str  # "create" | "skip" | "update"
    document_id: Optional[str] = None  # existing doc ID for skip/update
    message: str = ""


def compute_file_hash(content: bytes) -> str:
    """SHA-256 hash of raw file bytes."""
    return hashlib.sha256(content).hexdigest()


def compute_chunk_hash(text: str) -> str:
    """SHA-256 hash of chunk text content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def determine_action(
    file_hash: str,
    file_name: str,
    user_id: str,
    supabase_client,
    scope: str = "user",          # NEW (Phase 3 / FOLDER-05) — 'user' | 'global'; default preserves Phase 1/2 behavior
    folder_path: str = "/",       # NEW (Phase 3 / FOLDER-05) — canonical path (normalized BY CALLER)
) -> RecordAction:
    """
    Check if this file has been ingested before.

    Logic (Phase 3 / FOLDER-05 dedup key — (scope, user_id, folder_path, file_name)):
    1. Look for existing doc with same (scope, user_id, folder_path, file_name).
       For scope='user' the user_id filter uses .eq(); for scope='global' it uses
       .is_('user_id', 'null') because supabase-py SELECT filters do NOT apply
       the COALESCE-equivalence trick that Migration 012's unique index uses for
       write-time dedup (Pitfall A in 03-RESEARCH.md).
    2. If found and same hash -> skip (identical content; same dedup key).
    3. If found and different hash -> update (content changed; same dedup key).
    4. If not found -> create (new file, OR same file at a different path/scope —
       which is allowed under FOLDER-05).

    The query benefits from the scope-aware unique index
    documents_scope_user_path_filename_unique from Migration 012:51-57 (the
    .eq filter columns match the index column list in the same order).

    Backwards compatibility: callers from Phase 1/2 that pass only the first
    4 positional args get scope='user' and folder_path='/', which matches
    Episode-1-style root-folder uploads. Plan 05's router upgrade explicitly
    passes the new kwargs.
    """
    try:
        query = (
            supabase_client.table("documents")
            .select("id, content_hash, status")
            .eq("scope", scope)
            .eq("folder_path", folder_path)
            .eq("file_name", file_name)
        )
        if scope == "user":
            query = query.eq("user_id", user_id)
        else:
            # global rows have user_id IS NULL per Migration 012 coupling CHECK.
            # .eq('user_id', user_id) would NEVER match (Pitfall A).
            query = query.is_("user_id", "null")
        result = query.maybe_single().execute()
    except Exception:
        # No match found (.maybe_single() returns 204 -> supabase-py raises),
        # or any other transient query error -> treat as a fresh create.
        return RecordAction(action="create", message="New document")

    if not result or not result.data:
        return RecordAction(action="create", message="New document")

    existing = result.data
    if existing["content_hash"] == file_hash:
        return RecordAction(
            action="skip",
            document_id=existing["id"],
            message="File content unchanged — skipping ingestion",
        )

    return RecordAction(
        action="update",
        document_id=existing["id"],
        message="File content changed — re-ingesting",
    )
