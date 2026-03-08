from typing import Generator, Optional, List

from google import genai
from google.genai import types
from langsmith import traceable

from app.services.settings import get_llm_api_key, get_llm_model

_client_cache = {"key": None, "client": None}


def _get_client() -> genai.Client:
    key = get_llm_api_key()
    if _client_cache["key"] != key:
        _client_cache["key"] = key
        _client_cache["client"] = genai.Client(api_key=key)
    return _client_cache["client"]

SYSTEM_PROMPT_NO_CONTEXT = "You are a helpful assistant. Answer the user's questions clearly and concisely."

SYSTEM_PROMPT_WITH_CONTEXT = """You are a helpful assistant with access to the user's uploaded documents.
Use the provided document excerpts to answer questions accurately.
If the excerpts do not contain enough information to answer, say so and answer from general knowledge if applicable.

Document excerpts:
{context}"""


def retrieve_chunks(query: str, user_id: str, supabase_client, top_k: int = 5) -> List[str]:
    """Embed query and do cosine similarity search via pgvector RPC."""
    from app.services.ingestion import embed_text
    query_embedding = embed_text(query)
    result = supabase_client.rpc("match_document_chunks", {
        "query_embedding": query_embedding,
        "match_user_id": user_id,
        "match_count": top_k,
    }).execute()
    if not result.data:
        return []
    return [row["content"] for row in result.data]


@traceable(name="gemini_chat", run_type="llm")
def stream_response(
    messages: List[dict],
    thread_id: Optional[str] = None,
    user_id: Optional[str] = None,
    context_chunks: Optional[List[str]] = None,
) -> Generator[tuple[str, str], None, None]:
    """
    Stream a Gemini chat completion using stateless completions.
    messages: full conversation history as [{"role": "user"|"assistant", "content": "..."}]
    context_chunks: retrieved document chunks (may be empty or None)
    """
    if context_chunks:
        system_text = SYSTEM_PROMPT_WITH_CONTEXT.format(
            context="\n\n---\n\n".join(context_chunks)
        )
    else:
        system_text = SYSTEM_PROMPT_NO_CONTEXT

    contents = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": msg["content"]}]})

    client = _get_client()
    model = get_llm_model()

    response = client.models.generate_content_stream(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(system_instruction=system_text),
    )

    for chunk in response:
        if chunk.text:
            yield ("token", chunk.text)

    yield ("done", "")
