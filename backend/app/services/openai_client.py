import json
import logging
from typing import Generator, Optional, List

from google import genai
from google.genai import types
from langsmith import traceable

from app.services.settings import (
    get_llm_api_key, get_llm_model, get_metadata_schema,
    get_text_to_sql_enabled, get_web_search_enabled,
)

logger = logging.getLogger(__name__)

_client_cache = {"key": None, "client": None}


def _get_client() -> genai.Client:
    key = get_llm_api_key()
    if _client_cache["key"] != key:
        _client_cache["key"] = key
        _client_cache["client"] = genai.Client(api_key=key)
    return _client_cache["client"]


SYSTEM_PROMPT_NO_DOCS = "You are a helpful assistant. Answer the user's questions clearly and concisely."


def _build_system_prompt(has_documents: bool, has_structured_data: bool, web_search_enabled: bool) -> str:
    """Build system prompt dynamically based on which tools are available."""
    if not has_documents and not has_structured_data and not web_search_enabled:
        return SYSTEM_PROMPT_NO_DOCS

    parts = ["You are a helpful assistant with access to the following tools:"]

    if has_documents:
        parts.append("- search_documents: Search the user's uploaded documents for relevant excerpts.")
        parts.append("- analyze_document: Analyze a specific document in depth — use for summaries, key findings, or when the user references a document by name.")
    if has_structured_data:
        parts.append("- query_structured_data: Query tabular data (from CSV/XLSX files) using SQL. Use this for quantitative questions (totals, averages, counts, comparisons).")
    if web_search_enabled:
        parts.append("- web_search: Search the web for information not found in the user's documents.")

    parts.append("")
    parts.append("Use the appropriate tool based on the question:")
    if has_documents:
        parts.append("- For questions about document content or finding specific snippets, use search_documents.")
        parts.append("- For summarizing, analyzing, or extracting information from a specific document by name, use analyze_document.")
    if has_structured_data:
        parts.append("- For quantitative questions about tabular data (numbers, totals, averages), use query_structured_data.")
    if web_search_enabled:
        parts.append("- For current events or information not in the user's documents, use web_search.")
    parts.append("- For casual greetings or questions clearly unrelated to any tool, respond directly without calling a tool.")
    parts.append("- Only call ONE tool per turn.")

    return "\n".join(parts)


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


def _build_sql_tool() -> types.FunctionDeclaration:
    """Build the query_structured_data tool definition."""
    return types.FunctionDeclaration(
        name="query_structured_data",
        description=(
            "Query the user's tabular data (from uploaded CSV/XLSX files) to answer quantitative questions. "
            "Use this for questions about totals, counts, averages, comparisons, or any numeric analysis."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "question": types.Schema(
                    type="STRING",
                    description="The natural language question about the tabular data",
                ),
            },
            required=["question"],
        ),
    )


def _build_web_search_tool() -> types.FunctionDeclaration:
    """Build the web_search tool definition."""
    return types.FunctionDeclaration(
        name="web_search",
        description=(
            "Search the web for information not found in the user's uploaded documents. "
            "Use this for current events, external facts, or when document search returns no relevant results."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "query": types.Schema(
                    type="STRING",
                    description="The search query to find relevant web results",
                ),
            },
            required=["query"],
        ),
    )


def _build_analyze_tool() -> types.FunctionDeclaration:
    """Build the analyze_document tool definition for deep single-document analysis."""
    return types.FunctionDeclaration(
        name="analyze_document",
        description=(
            "Analyze a specific document in depth — use for summaries, key findings, "
            "or when the user references a document by name. "
            "NOT for cross-document search."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "document_name": types.Schema(
                    type="STRING",
                    description="Name of the document to analyze",
                ),
                "question": types.Schema(
                    type="STRING",
                    description="What to analyze about this document",
                ),
            },
            required=["document_name", "question"],
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
    has_structured_data: bool = False,
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

    # Check which tools are enabled
    text_to_sql_enabled = get_text_to_sql_enabled() and has_structured_data
    web_search_enabled = get_web_search_enabled()

    # Build dynamic tool list
    function_declarations = []
    if has_documents:
        try:
            function_declarations.append(_build_search_tool())
        except Exception as e:
            logger.warning(f"Failed to build search tool (non-fatal): {e}")
    if text_to_sql_enabled:
        try:
            function_declarations.append(_build_sql_tool())
        except Exception as e:
            logger.warning(f"Failed to build SQL tool (non-fatal): {e}")
    if web_search_enabled:
        try:
            function_declarations.append(_build_web_search_tool())
        except Exception as e:
            logger.warning(f"Failed to build web search tool (non-fatal): {e}")
    if has_documents:
        try:
            function_declarations.append(_build_analyze_tool())
        except Exception as e:
            logger.warning(f"Failed to build analyze tool (non-fatal): {e}")

    tools = None
    tool_config = None
    system_text = _build_system_prompt(has_documents, text_to_sql_enabled, web_search_enabled)
    if function_declarations:
        tools = [types.Tool(function_declarations=function_declarations)]
        tool_config = types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(mode="AUTO")
        )

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
        # Extract the function call
        fc = None
        for part in response.candidates[0].content.parts:
            if part.function_call:
                fc = part.function_call
                break

        args = dict(fc.args) if fc.args else {}
        tool_name = fc.name
        logger.info(f"Tool call: {tool_name}(args={args})")

        # Dispatch to the correct tool executor
        if tool_name == "search_documents":
            search_query = args.pop("query", "")
            metadata_filter = {k: v for k, v in args.items() if v is not None} or None
            chunks = _execute_search_documents(
                search_query=search_query,
                metadata_filter=metadata_filter,
                user_id=user_id,
                supabase_client=supabase_client,
            )
            result_text = "\n\n---\n\n".join(chunks) if chunks else "No relevant documents found."

        elif tool_name == "query_structured_data":
            from app.services.sql_tool import execute_sql_query
            question = args.get("question", "")
            result_text = execute_sql_query(question, user_id, supabase_client)

        elif tool_name == "web_search":
            from app.services.web_search import execute_web_search
            query = args.get("query", "")
            result_text = execute_web_search(query)

        elif tool_name == "analyze_document":
            from app.services.sub_agent import run_sub_agent
            doc_name = args.get("document_name", "")
            question = args.get("question", "")

            # Resolve document_name → document_id via fuzzy match
            doc = supabase_client.table("documents") \
                .select("id, original_filename") \
                .eq("user_id", user_id) \
                .ilike("original_filename", f"%{doc_name}%") \
                .order("created_at", desc=True) \
                .limit(1).execute()

            if not doc.data:
                result_text = f"No document matching '{doc_name}' found."
            else:
                doc_id = doc.data[0]["id"]
                actual_name = doc.data[0]["original_filename"]
                sub_agent_result = ""
                for evt_type, evt_data in run_sub_agent(doc_id, actual_name, question, user_id, supabase_client):
                    yield (evt_type, evt_data)
                    if evt_type == "sub_agent_done":
                        sub_agent_result = evt_data
                result_text = sub_agent_result

        else:
            logger.warning(f"Unknown tool: {tool_name}")
            result_text = f"Unknown tool: {tool_name}"

        # Context injection for the final answer (skip the tool round-trip)
        system_with_context = f"""You are a helpful assistant. Use the provided tool results to answer the user's question accurately.
If the results do not contain enough information, say so and answer from general knowledge if applicable.
When citing web sources, include the URLs.

Tool ({tool_name}) results:
{result_text}"""

        # Stream the final answer without tools (avoids thought_signature issue)
        response2 = client.models.generate_content_stream(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=system_with_context),
        )

        has_response2_text = False
        for chunk in response2:
            if chunk.text:
                has_response2_text = True
                yield ("token", chunk.text)

        # Safeguard: if context injection returned nothing, yield the tool result directly
        if not has_response2_text and result_text:
            logger.warning(f"Context injection returned empty for tool={tool_name}, yielding raw result")
            yield ("token", result_text)
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
