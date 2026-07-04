from fastapi import APIRouter, Depends, HTTPException
from app.auth import get_current_user, get_supabase_client
from app.models.schemas import ThreadCreate, ThreadResponse

router = APIRouter(prefix="/api/threads", tags=["threads"])


@router.post("", response_model=ThreadResponse)
async def create_thread(
    body: ThreadCreate,
    user_id: str = Depends(get_current_user),
):
    supabase = get_supabase_client()
    data = {"user_id": user_id}
    if body.title:
        data["title"] = body.title

    result = supabase.table("threads").insert(data).execute()
    return result.data[0]


@router.get("", response_model=list[ThreadResponse])
async def list_threads(user_id: str = Depends(get_current_user)):
    supabase = get_supabase_client()
    result = (
        supabase.table("threads")
        .select("*")
        .eq("user_id", user_id)
        .order("updated_at", desc=True)
        .execute()
    )
    return result.data


@router.get("/{thread_id}", response_model=ThreadResponse)
async def get_thread(thread_id: str, user_id: str = Depends(get_current_user)):
    supabase = get_supabase_client()
    result = (
        supabase.table("threads")
        .select("*")
        .eq("id", thread_id)
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    # maybe_single() returns None (not a response object) when no rows match
    # on this supabase-py version, so guard before touching .data
    if result is None or not result.data:
        raise HTTPException(status_code=404, detail="Thread not found")
    return result.data


@router.delete("/{thread_id}")
async def delete_thread(thread_id: str, user_id: str = Depends(get_current_user)):
    supabase = get_supabase_client()
    result = (
        supabase.table("threads")
        .delete()
        .eq("id", thread_id)
        .eq("user_id", user_id)
        .execute()
    )
    return {"ok": True}
