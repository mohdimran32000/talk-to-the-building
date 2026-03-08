import json
import logging
from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse
from app.auth import get_current_user, get_supabase_client
from app.models.schemas import MessageCreate, MessageResponse
from app.services.openai_client import stream_response, retrieve_chunks

router = APIRouter(prefix="/api/threads/{thread_id}/messages", tags=["messages"])


@router.get("", response_model=list[MessageResponse])
async def get_messages(thread_id: str, user_id: str = Depends(get_current_user)):
    supabase = get_supabase_client()
    thread = supabase.table("threads").select("id").eq("id", thread_id).eq("user_id", user_id).maybe_single().execute()
    if not thread.data:
        raise HTTPException(status_code=404, detail="Thread not found")
    return supabase.table("messages").select("*").eq("thread_id", thread_id).order("created_at", desc=False).execute().data


@router.post("")
async def send_message(
    thread_id: str,
    body: MessageCreate,
    user_id: str = Depends(get_current_user),
):
    supabase = get_supabase_client()

    thread = supabase.table("threads").select("*").eq("id", thread_id).eq("user_id", user_id).maybe_single().execute()
    if not thread.data:
        raise HTTPException(status_code=404, detail="Thread not found")

    supabase.table("messages").insert({
        "thread_id": thread_id,
        "user_id": user_id,
        "role": "user",
        "content": body.content,
    }).execute()

    # Stateless completions: load full history from DB
    history = supabase.table("messages").select("role, content").eq("thread_id", thread_id).order("created_at", desc=False).execute()
    messages = [{"role": m["role"], "content": m["content"]} for m in history.data]

    # BYO retrieval: embed query + similarity search (skipped if no ready documents)
    context_chunks = []
    try:
        ready_docs = supabase.table("documents").select("id").eq("user_id", user_id).eq("status", "ready").limit(1).execute()
        if ready_docs.data:
            context_chunks = retrieve_chunks(query=body.content, user_id=user_id, supabase_client=supabase, top_k=5)
    except Exception as e:
        logging.getLogger(__name__).warning(f"Retrieval failed (non-fatal): {e}")

    def event_generator():
        full_response = ""
        try:
            for event_type, data in stream_response(
                messages=messages,
                thread_id=thread_id,
                user_id=user_id,
                context_chunks=context_chunks,
            ):
                if event_type == "token":
                    full_response += data
                    yield json.dumps({"type": "token", "content": data})
                elif event_type == "done":
                    yield json.dumps({"type": "done"})
        except Exception as e:
            yield json.dumps({"type": "error", "content": str(e)})
            return

        supabase.table("messages").insert({
            "thread_id": thread_id,
            "user_id": user_id,
            "role": "assistant",
            "content": full_response,
        }).execute()
        supabase.table("threads").update({"updated_at": "now()"}).eq("id", thread_id).execute()

    return EventSourceResponse(event_generator())
