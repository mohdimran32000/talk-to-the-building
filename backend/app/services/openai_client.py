import json
import logging
from typing import Generator, Optional, List

from google import genai
from google.genai import types
from langsmith import traceable

from app.services.settings import get_llm_api_key, get_llm_model, get_metadata_schema

logger = logging.getLogger(__name__)

_client_cache = {"key": None, "client": None}


def _get_client() -> genai.Client:
    key = get_llm_api_key()
    if _client_cache["key"] != key:
        _client_cache["key"] = key
        _client_cache["client"] = genai.Client(api_key=key)
    return _client_cache["client"]


SYSTEM_PROMPT = """You are a helpful assistant with access to the user's uploaded documents.
When the user asks a question that could be answered from their documents, use the search_documents tool to find relevant excerpts.
If the tool returns no results, say so and answer from general knowledge if applicable.
For casual greetings or questions clearly unrelated to documents, respond directly without searching."""

SYSTEM_PROMPT_NO_DOCS = "You are a helpful assistant. Answer the user's questions clearly and concisely."


def _build_search_tool() -> types.Tool:
    """Build the search_documents tool definition dynamically from the metadata schema."""
    schema = get_metadata_schema()

    filter_properties = {}
    for field in schema:
        field_type = field.get("type", "text")
        if field_type == "text":
            filter_properties[field["name"]] = types.Schema(
                type="STRING", description=field.get("description", ""),
            )
        elif field_type == "list":
            filter_properties[field["name"]] = types.Schema(
                type="ARRAY", items=types.Schema(type="STRING"),
                description=field.get("description", ""),
            )
        elif field_type == "boolean":
            filter_properties[field["name"]] = types.Schema(
                type="BOOLEAN", description=field.get("description", ""),
            )
        elif field_type == "number":
            filter_properties[field["name"]] = types.Schema(
                type="NUMBER", description=field.get("description", ""),
            )
        elif field_type == "date":
            filter_properties[field["name"]] = types.Schema(
                type="STRING",
                description=f"{field.get('description', '')} (YYYY-MM-DD format)",
            )

    return types.FunctionDeclaration(
        name="search_documents",
        description=(
            "Search the user's uploaded documents. Use the query parameter for semantic search. "
            "Optionally use metadata filter parameters to narrow results by document properties. "
            "Only include filter parameters you are confident about based on the user's question."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "query": types.Schema(
                    type="STRING",
                    description="The search query — a rephrased version of the user's question optimized for semantic similarity search",
                ),
                **filter_properties,
            },
            required=["query"],
        ),
    )


def retrieve_chunks(query: str, user_id: str, supabase_client, top_k: int = 5, metadata_filter: Optional[dict] = None) -> List[str]:
    """Embed query and search via hybrid (vector + keyword RRF) or vector-only RPC."""
    from app.services.ingestion import embed_text
    from app.services.settings import get_hybrid_search_enabled, get_reranking_enabled

    query_embedding = embed_text(query)
    hybrid = get_hybrid_search_enabled()
    reranking = get_reranking_enabled()

    if hybrid:
        fetch_count = top_k * 4 if reranking else top_k
        result = supabase_client.rpc("match_document_chunks_hybrid", {
            "query_embedding": query_embedding,
            "query_text": query,
            "match_user_id": user_id,
            "match_count": fetch_count,
            "metadata_filter": json.dumps(metadata_filter) if metadata_filter else None,
        }).execute()
    else:
        result = supabase_client.rpc("match_document_chunks_with_filters", {
            "query_embedding": query_embedding,
            "match_user_id": user_id,
            "match_count": top_k,
            "metadata_filter": json.dumps(metadata_filter) if metadata_filter else None,
        }).execute()

    chunks = [row["content"] for row in (result.data or [])]

    if hybrid and reranking and len(chunks) > top_k:
        from app.services.reranker import rerank_chunks
        chunks = rerank_chunks(query, chunks, top_k)

    return chunks


@traceable(name="search_documents", run_type="tool")
def _execute_search_documents(
    search_query: str,
    metadata_filter: Optional[dict],
    user_id: Optional[str],
    supabase_client=None,
) -> List[str]:
    """Execute the search_documents tool call — traced as a tool in LangSmith."""
    if not supabase_client or not user_id:
        return []
    try:
        return retrieve_chunks(
            query=search_query,
            user_id=user_id,
            supabase_client=supabase_client,
            top_k=5,
            metadata_filter=metadata_filter,
        )
    except Exception as e:
        logger.warning(f"Tool retrieval failed: {e}")
        return []


@traceable(name="gemini_chat", run_type="llm")
def stream_response(
    messages: List[dict],
    thread_id: Optional[str] = None,
    user_id: Optional[str] = None,
    supabase_client=None,
    has_documents: bool = False,
    manual_metadata_filter: Optional[dict] = None,
) -> Generator[tuple[str, str], None, None]:
    """
    Stream a Gemini chat completion with tool calling for document search.
    Uses Gemini's automatic function calling — the SDK handles the tool loop
    and thought_signature internally.
    """
    # If manual filter is set, pre-retrieve and use context injection (no tool calling)
    if manual_metadata_filter and has_documents and supabase_client and user_id:
        last_user_msg = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        chunks = retrieve_chunks(
            query=last_user_msg, user_id=user_id,
            supabase_client=supabase_client, top_k=5,
            metadata_filter=manual_metadata_filter,
        )
        context = "\n\n---\n\n".join(chunks) if chunks else "No relevant documents found."
        system_text = f"""You are a helpful assistant with access to the user's uploaded documents.
Use the provided document excerpts to answer questions accurately.
If the excerpts do not contain enough information to answer, say so and answer from general knowledge if applicable.

Document excerpts:
{context}"""

        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append(types.Content(role=role, parts=[types.Part(text=msg["content"])]))

        client = _get_client()
        model = get_llm_model()
        response = client.models.generate_content_stream(
            model=model, contents=contents,
            config=types.GenerateContentConfig(system_instruction=system_text),
        )
        for chunk in response:
            if chunk.text:
                yield ("token", chunk.text)
        yield ("done", "")
        return

    contents = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part(text=msg["content"])]))

    client = _get_client()
    model = get_llm_model()

    # Build the search function that Gemini will call automatically
    # Capture user_id and supabase_client in closure
    def search_documents(query: str, **kwargs) -> str:
        """Search the user's uploaded documents with optional metadata filters."""
        metadata_filter = {k: v for k, v in kwargs.items() if v is not None} or None
        logger.info(f"Tool call: search_documents(query='{query}', filter={metadata_filter})")

        chunks = _execute_search_documents(
            search_query=query,
            metadata_filter=metadata_filter,
            user_id=user_id,
            supabase_client=supabase_client,
        )

        if chunks:
            return "\n\n---\n\n".join(chunks)
        return "No relevant documents found."

    # Configure tools — use automatic function calling so SDK handles thought_signature
    tools = None
    tool_config = None
    system_text = SYSTEM_PROMPT_NO_DOCS
    if has_documents:
        try:
            tools = [types.Tool(function_declarations=[_build_search_tool()])]
            system_text = SYSTEM_PROMPT
            tool_config = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="AUTO")
            )
        except Exception as e:
            logger.warning(f"Failed to build search tool (non-fatal): {e}")

    config = types.GenerateContentConfig(
        system_instruction=system_text,
        tools=tools,
        tool_config=tool_config,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    # Use non-streaming generate_content to handle the full tool call loop
    # Then stream the final response to the user
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=config,
    )

    # Handle malformed function calls — retry without tools using context injection
    if (response.candidates and
        response.candidates[0].finish_reason and
        response.candidates[0].finish_reason.name == "MALFORMED_FUNCTION_CALL"):
        logger.warning("Gemini returned MALFORMED_FUNCTION_CALL — falling back to pre-retrieval")
        last_user_msg = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        chunks = _execute_search_documents(
            search_query=last_user_msg, metadata_filter=None,
            user_id=user_id, supabase_client=supabase_client,
        )
        context = "\n\n---\n\n".join(chunks) if chunks else "No relevant documents found."
        fallback_system = f"""You are a helpful assistant with access to the user's uploaded documents.
Use the provided document excerpts to answer questions accurately.
If the excerpts do not contain enough information to answer, say so and answer from general knowledge if applicable.

Document excerpts:
{context}"""
        response_fb = client.models.generate_content_stream(
            model=model, contents=contents,
            config=types.GenerateContentConfig(system_instruction=fallback_system),
        )
        for chunk in response_fb:
            if chunk.text:
                yield ("token", chunk.text)
        yield ("done", "")
        return

    # Check if we got a function call
    has_function_call = False
    if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
        for part in response.candidates[0].content.parts:
            if part.function_call:
                has_function_call = True
                break

    if has_function_call:
        # Extract the function call args
        fc = None
        for part in response.candidates[0].content.parts:
            if part.function_call:
                fc = part.function_call
                break

        args = dict(fc.args) if fc.args else {}
        search_query = args.pop("query", "")
        metadata_filter = args if args else None

        # Execute the search
        result_text = search_documents(search_query, **(metadata_filter or {}))

        # Now do context injection for the final answer (skip the tool round-trip)
        system_with_context = f"""You are a helpful assistant with access to the user's uploaded documents.
Use the provided document excerpts to answer questions accurately.
If the excerpts do not contain enough information to answer, say so and answer from general knowledge if applicable.

Document excerpts:
{result_text}"""

        # Stream the final answer without tools (avoids thought_signature issue)
        response2 = client.models.generate_content_stream(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=system_with_context),
        )

        for chunk in response2:
            if chunk.text:
                yield ("token", chunk.text)
    else:
        # No tool call — LLM responded directly
        has_text = False
        if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.text:
                    has_text = True
                    yield ("token", part.text)
        if not has_text:
            logger.warning(f"Gemini returned empty response (finish_reason={response.candidates[0].finish_reason if response.candidates else 'no candidates'})")
            yield ("token", "I'm sorry, I couldn't generate a response. Please try again.")

    yield ("done", "")
