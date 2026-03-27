"""Sub-agent service: deep single-document analysis via isolated Gemini call."""

import json
import logging
from typing import Generator

from google.genai import types
from langsmith import traceable

from app.services.openai_client import _get_client
from app.services.settings import get_llm_model

logger = logging.getLogger(__name__)

MAX_CONTEXT_CHARS = 800_000  # Gemini context limit safety margin


@traceable(name="sub_agent_analyze", run_type="chain")
def run_sub_agent(
    document_id: str,
    document_name: str,
    question: str,
    user_id: str,
    supabase_client,
) -> Generator[tuple[str, str], None, None]:
    """
    Load all chunks for a document, send to Gemini as full context,
    and stream the analysis back.

    Yields event tuples:
      ("sub_agent_start", json_payload)
      ("sub_agent_token", chunk_text)
      ("sub_agent_done", full_result_text)
    """
    yield ("sub_agent_start", json.dumps({"document_name": document_name}))

    # 1. Load ALL chunks for this document, ordered by chunk_index
    try:
        result = (
            supabase_client.table("document_chunks")
            .select("content")
            .eq("document_id", document_id)
            .order("chunk_index", desc=False)
            .execute()
        )
        chunks = [row["content"] for row in (result.data or [])]
    except Exception as e:
        logger.error(f"Failed to load chunks for document {document_id}: {e}")
        error_msg = f"Failed to load document content: {e}"
        yield ("sub_agent_done", error_msg)
        return

    if not chunks:
        msg = f"No content found for document '{document_name}'."
        yield ("sub_agent_done", msg)
        return

    # 2. Concatenate into a single context string
    full_text = "\n\n".join(chunks)

    # 3. Truncate if too large
    if len(full_text) > MAX_CONTEXT_CHARS:
        full_text = full_text[:MAX_CONTEXT_CHARS] + "\n\n[... document truncated due to length ...]"
        logger.warning(f"Document '{document_name}' truncated from {len(full_text)} chars to {MAX_CONTEXT_CHARS}")

    # 4-5. Build focused system prompt with full document content
    system_prompt = (
        f"You are a document analyst. Below is the full content of '{document_name}'. "
        f"Analyze it thoroughly to answer the user's question.\n\n"
        f"--- DOCUMENT CONTENT ---\n{full_text}\n--- END DOCUMENT ---"
    )

    # 6. Single streaming Gemini call (no tools needed)
    client = _get_client()
    model = get_llm_model()

    contents = [types.Content(role="user", parts=[types.Part(text=question)])]

    full_result = ""
    try:
        response = client.models.generate_content_stream(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=system_prompt),
        )

        # 7. Yield tokens as they stream
        for chunk in response:
            if chunk.text:
                full_result += chunk.text
                yield ("sub_agent_token", chunk.text)
    except Exception as e:
        logger.error(f"Sub-agent streaming failed: {e}")
        if not full_result:
            full_result = f"Sub-agent analysis failed: {e}"

    yield ("sub_agent_done", full_result)
