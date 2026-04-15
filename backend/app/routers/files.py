import threading

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from app.auth import get_current_user, get_supabase_client
from app.models.schemas import DocumentResponse
from app.services.ingestion import ingest_document, ingest_document_update
from app.services.record_manager import compute_file_hash, determine_action

router = APIRouter(prefix="/api/files", tags=["files"])

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

    # Record Manager: check for duplicates
    file_hash = compute_file_hash(contents)
    record_action = determine_action(file_hash, file_name, user_id, supabase)

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
        "user_id": user_id,
        "file_name": file_name,
        "file_size": len(contents),
        "mime_type": mime_type,
        "status": "pending",
    }).execute().data[0]

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
