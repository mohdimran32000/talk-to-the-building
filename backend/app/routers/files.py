from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from app.auth import get_current_user, get_supabase_client
from app.models.schemas import DocumentResponse
from app.services.ingestion import ingest_document

router = APIRouter(prefix="/api/files", tags=["files"])


@router.post("/upload", response_model=DocumentResponse)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user),
):
    supabase = get_supabase_client()
    # Must read before add_task — UploadFile stream closes after handler returns
    contents = await file.read()

    doc = supabase.table("documents").insert({
        "user_id": user_id,
        "file_name": file.filename or "unnamed",
        "file_size": len(contents),
        "mime_type": file.content_type or "application/octet-stream",
        "status": "pending",
    }).execute().data[0]

    background_tasks.add_task(
        ingest_document,
        document_id=doc["id"],
        file_content=contents,
        mime_type=file.content_type or "application/octet-stream",
        file_name=file.filename or "unnamed",
        user_id=user_id,
        supabase_client=supabase,
    )
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
