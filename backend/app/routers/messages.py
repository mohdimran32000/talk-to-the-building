import json
import logging
from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse
from app.auth import get_current_user, get_supabase_client
from app.models.schemas import MessageCreate, MessageResponse
from app.services.openai_client import stream_response

logger = logging.getLogger(__name__)
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
    # Filter out empty assistant messages (can happen if a tool call's context injection failed)
    history = supabase.table("messages").select("role, content").eq("thread_id", thread_id).order("created_at", desc=False).execute()
    messages = [
        {"role": m["role"], "content": m["content"]}
        for m in history.data
        if m["content"] and m["content"].strip()
    ]

    # Check if user has ready documents (enables search tool)
    has_documents = False
    has_structured_data = False
    try:
        ready_docs = supabase.table("documents").select("id").eq("user_id", user_id).eq("status", "ready").limit(1).execute()
        has_documents = bool(ready_docs.data)
    except Exception as e:
        logger.warning(f"Document check failed (non-fatal): {e}")

    # Check if user has structured data (enables Text-to-SQL tool)
    try:
        struct_data = supabase.table("structured_data").select("id").eq("user_id", user_id).limit(1).execute()
        has_structured_data = bool(struct_data.data)
    except Exception as e:
        logger.warning(f"Structured data check failed (non-fatal): {e}")

    def event_generator():
        full_response = ""
        tool_metadata = None
        try:
            for event_type, data in stream_response(
                messages=messages,
                thread_id=thread_id,
                user_id=user_id,
                supabase_client=supabase,
                has_documents=has_documents,
                has_structured_data=has_structured_data,
                manual_metadata_filter=body.metadata_filter,
            ):
                if event_type == "token":
                    full_response += data
                    yield json.dumps({"type": "token", "content": data})
                elif event_type == "sub_agent_start":
                    parsed = json.loads(data)
                    tool_metadata = {"tools_used": [{"document_name": parsed.get("document_name", "")}]}
                    yield json.dumps({"type": "sub_agent_start", **parsed})
                elif event_type == "sub_agent_token":
                    yield json.dumps({"type": "sub_agent_token", "content": data})
                elif event_type == "sub_agent_done":
                    if tool_metadata and tool_metadata["tools_used"]:
                        tool_metadata["tools_used"][0]["tool"] = "analyze_document"
                        tool_metadata["tools_used"][0]["sub_agent_result"] = data[:300]
                    yield json.dumps({"type": "sub_agent_done"})
                elif event_type == "done":
                    yield json.dumps({"type": "done"})
        except Exception as e:
            yield json.dumps({"type": "error", "content": str(e)})
            return

        # Only persist if we got a non-empty response (avoids corrupting conversation history)
        if full_response.strip():
            insert_data = {
                "thread_id": thread_id,
                "user_id": user_id,
                "role": "assistant",
                "content": full_response,
            }
            if tool_metadata:
                insert_data["tool_metadata"] = json.dumps(tool_metadata)
            supabase.table("messages").insert(insert_data).execute()
        else:
            logger.warning(f"Empty assistant response for thread {thread_id} — not persisting")
        supabase.table("threads").update({"updated_at": "now()"}).eq("id", thread_id).execute()

    return EventSourceResponse(event_generator())
