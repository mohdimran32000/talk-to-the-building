import logging
import os
import threading

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks, Query
from app.auth import get_current_user, get_supabase_client, get_user_profile
from app.models.schemas import DocumentResponse, FilePatch
from app.services.folder_service import normalize_path
from app.services.ingestion import ingest_document, ingest_document_update
from app.services.record_manager import compute_file_hash, determine_action

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/files", tags=["files"])

_ingestion_semaphore = threading.Semaphore(2)


def _throttled_ingest(func, *args, **kwargs):
    acquired = _ingestion_semaphore.acquire(timeout=300)
    try:
        if not acquired:
            logger.error("Ingestion queue full — skipping")
            return
        func(*args, **kwargs)
    except Exception as e:
        logger.error(f"Ingestion crashed: {e}", exc_info=True)
    finally:
        if acquired:
            _ingestion_semaphore.release()


def _upload_to_storage(supabase, user_id: str, document_id: str, file_name: str,
                       contents: bytes, mime_type: str) -> None:
    """Persist the original blob to Supabase Storage so future re-indexing
    (Phase 2 backfill_content_markdown.py + any future re-Docling pass) can
    recover it. Path: documents/{user_id}/{document_id}{ext}.

    Failure is NON-FATAL — the ingest path still runs and the document still
    reaches status='ready' even if Storage is unavailable. Plan 03's backfill
    marks rows whose blobs are missing as 'requires_user_reupload' (per CONTEXT.md
    §LOCKED—Backfill scope reframe).
    """
    ext = os.path.splitext(file_name)[1]   # includes leading dot, e.g. '.pdf' or ''
    storage_path = f"{user_id}/{document_id}{ext}"
    try:
        supabase.storage.from_("documents").upload(
            storage_path,
            contents,
            file_options={"content-type": mime_type, "upsert": "true"},
        )
        logger.info(
            f"Storage upload OK: doc={document_id} path={storage_path} bytes={len(contents)}"
        )
    except Exception as e:
        logger.warning(
            f"Storage upload failed (non-fatal) for {document_id} path={storage_path}: {e}"
        )


@router.post("/upload", response_model=DocumentResponse)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    folder_path: str = Query("/", description="Canonical folder path"),
    scope: str = Query("user", regex="^(user|global)$",
                       description="'user' (default) or 'global'; admin required for 'global'"),
    user_id: str = Depends(get_current_user),
):
    supabase = get_supabase_client()
    contents = await file.read()
    file_name = file.filename or "unnamed"
    mime_type = file.content_type or "application/octet-stream"

    # Pitfall 4 belt: normalize the path at the router boundary.
    try:
        folder_path = normalize_path(folder_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Admin gate for global scope (inline mirror of auth.py:46-51).
    if scope == "global":
        profile = get_user_profile(user_id)
        if not profile or not profile.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin required for global scope")
        # Migration 012 coupling CHECK: scope='global' requires user_id IS NULL.
        effective_user_id = None
        # Pitfall F: avoid 'None' in storage path; use 'global' segment so the
        # path is well-formed. Migration 018 RLS rejects this for the authenticated
        # role; service-role bypasses (admin / backend reads work).
        storage_user_segment = "global"
    else:
        effective_user_id = user_id
        storage_user_segment = user_id

    # Record Manager: check for duplicates (Plan 03 extended dedup key).
    file_hash = compute_file_hash(contents)
    record_action = determine_action(
        file_hash, file_name, user_id, supabase,
        scope=scope, folder_path=folder_path,
    )

    if record_action.action == "skip":
        doc = supabase.table("documents").select("*") \
            .eq("id", record_action.document_id).single().execute().data
        doc["action"] = "skipped"
        return doc

    if record_action.action == "update":
        supabase.table("documents").update({
            "file_size": len(contents),
            "mime_type": mime_type,
            "status": "pending",
            "error_message": None,
            "updated_at": "now()",
        }).eq("id", record_action.document_id).execute()

        doc = supabase.table("documents").select("*") \
            .eq("id", record_action.document_id).single().execute().data

        _upload_to_storage(
            supabase,
            user_id=storage_user_segment,
            document_id=doc["id"],
            file_name=file_name,
            contents=contents,
            mime_type=mime_type,
        )

        background_tasks.add_task(
            _throttled_ingest,
            ingest_document_update,
            document_id=doc["id"],
            file_content=contents,
            mime_type=mime_type,
            file_name=file_name,
            user_id=user_id,
            supabase_client=supabase,
        )
        doc["action"] = "updated"
        return doc

    # action == "create": new document
    doc = supabase.table("documents").insert({
        "user_id": effective_user_id,
        "scope": scope,                  # NEW (Phase 3 / FOLDER-07)
        "folder_path": folder_path,      # NEW (Phase 3 / FOLDER-07)
        "file_name": file_name,
        "file_size": len(contents),
        "mime_type": mime_type,
        "status": "pending",
    }).execute().data[0]

    _upload_to_storage(
        supabase,
        user_id=storage_user_segment,
        document_id=doc["id"],
        file_name=file_name,
        contents=contents,
        mime_type=mime_type,
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


@router.get("", response_model=list[DocumentResponse])
async def list_files(user_id: str = Depends(get_current_user)):
    supabase = get_supabase_client()
    return supabase.table("documents").select("*").eq("user_id", user_id).order("created_at", desc=True).execute().data


@router.delete("/{file_id}")
async def delete_file(file_id: str, user_id: str = Depends(get_current_user)):
    supabase = get_supabase_client()
    try:
        record = supabase.table("documents").select("id").eq("id", file_id).eq("user_id", user_id).maybe_single().execute()
        if not record or not record.data:
            raise HTTPException(status_code=404, detail="Document not found")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail="Document not found")
    supabase.table("documents").delete().eq("id", file_id).execute()
    return {"status": "deleted"}


@router.patch("/{file_id}", response_model=DocumentResponse)
async def patch_file(
    file_id: str,
    body: FilePatch,
    user_id: str = Depends(get_current_user),
):
    sb = get_supabase_client()

    # Lookup for admin-gate decision; 404 cleanly if missing.
    try:
        doc_resp = sb.table("documents").select("*").eq("id", file_id).maybe_single().execute()
    except Exception:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc_resp or not doc_resp.data:
        raise HTTPException(status_code=404, detail="Document not found")
    existing = doc_resp.data

    # Admin gate AFTER lookup — gate decision depends on existing.scope.
    if existing["scope"] == "global":
        profile = get_user_profile(user_id)
        if not profile or not profile.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin required for global document")

    # CRITICAL: scope is IMMUTABLE — Migration 015's forbid_scope_mutation trigger
    # is the bedrock. FilePatch model deliberately omits scope (Pydantic v2 ignores
    # unknown fields on body parsing); explicit safety net via update_data dict
    # building (only file_name and folder_path get passed through).
    update_data: dict = {}
    if body.file_name is not None:
        update_data["file_name"] = body.file_name
    if body.folder_path is not None:
        try:
            update_data["folder_path"] = normalize_path(body.folder_path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    sb.table("documents").update(update_data).eq("id", file_id).execute()
    return sb.table("documents").select("*").eq("id", file_id).single().execute().data
