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
) -> RecordAction:
    """
    Check if this file has been ingested before.

    Logic:
    1. Look for existing doc with same (user_id, file_name)
    2. If found and same hash → skip (identical content)
    3. If found and different hash → update (content changed)
    4. If not found → create (new file)
    """
    try:
        result = supabase_client.table("documents") \
            .select("id, content_hash, status") \
            .eq("user_id", user_id) \
            .eq("file_name", file_name) \
            .maybe_single() \
            .execute()
    except Exception:
        # No match found (maybe_single returns 204)
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


