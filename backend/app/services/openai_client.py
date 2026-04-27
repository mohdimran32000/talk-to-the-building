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
    if not key:
        raise ValueError("No Gemini API key configured. Set GEMINI_API_KEY in .env or configure it in Admin Settings.")
    if _client_cache["key"] != key or _client_cache["client"] is None:
        _client_cache["client"] = genai.Client(api_key=key)
        _client_cache["key"] = key
    return _client_cache["client"]


SYSTEM_PROMPT_NO_DOCS = "You are a helpful assistant. Answer the user's questions clearly and concisely."

OUTPUT_FORMAT_RULES = """
OUTPUT FORMAT RULES (strict):
- Never output raw HTML in your answer. Tags like <table>, <tr>, <td>, <th>, <br>, <span>, <div> are forbidden. If the source excerpts contain HTML, extract the data into clean markdown.
- For tabular source data, prefer a concise markdown bulleted list unless the user explicitly asked for a table. If a markdown table is warranted, keep it small and relevant to the question — do not include every row and column.
- Never paste, echo, or reproduce source excerpts verbatim. Always synthesize the answer in your own words.
- Keep answers focused on what was asked. If a source has extra detail, leave it out."""


def _build_system_prompt(has_documents: bool, has_structured_data: bool, web_search_enabled: bool) -> str:
    """Build system prompt dynamically based on which tools are available."""
    if not has_documents and not has_structured_data and not web_search_enabled:
        return SYSTEM_PROMPT_NO_DOCS

    parts = ["You are a helpful assistant with access to the following tools:"]

    if has_documents:
        parts.append("- analyze_document: Summarize, review, or analyze a specific document in depth. Loads the FULL document. MUST be used for any summarization request.")
        parts.append("- search_documents: Find specific facts or snippets across documents. Only returns a few excerpts — NOT for summarization.")
    if has_structured_data:
        parts.append("- query_structured_data: Query tabular data (from CSV/XLSX files) using SQL. Use this for quantitative questions (totals, averages, counts, comparisons).")
    if web_search_enabled:
        parts.append("- web_search: Search the web for information not found in the user's documents.")

    parts.append("")
    parts.append("TOOL SELECTION RULES (follow strictly):")
    if has_documents:
        parts.append("- ALWAYS use analyze_document when the user asks to summarize, analyze, review, or extract key findings from a specific document by name. This tool loads the FULL document — search_documents only returns a few snippets and cannot produce a real summary.")
        parts.append("- Use search_documents ONLY for finding specific facts, quotes, or snippets across multiple documents (e.g. 'what does the contract say about payment terms?').")
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
            "Find specific facts, quotes, or snippets across the user's documents. "
            "Returns only a few matching excerpts — NOT suitable for summarizing or analyzing a whole document. "
            "Do NOT use this for summarization requests — use analyze_document instead."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "query": types.Schema(
                    type="STRING",
                    description="A SHORT, focused search query (under 50 words). Extract specific identifiers (codes, model numbers, names) and the core question. Do NOT include email headers, greetings, or background context.",
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
            "REQUIRED for summarizing, reviewing, or analyzing a document. "
            "Loads the FULL document content for comprehensive analysis. "
            "Use this whenever the user says 'summarize', 'analyze', 'review', or 'explain' a document."
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


def _sanitize_keyword_query(q: str) -> str:
    """Strip websearch_to_tsquery operators so user identifiers don't become NOT clauses.
    A space-prefixed hyphen (e.g. `MDB -C-G3` from email formatting) is the NOT operator
    in websearch syntax, which silently excludes the very chunks the user is searching for.
    """
    import re
    q = q.replace("-", " ").replace('"', " ")
    q = re.sub(r"\bor\b", " ", q, flags=re.IGNORECASE)
    q = re.sub(r"\s+", " ", q).strip()
    return q


def retrieve_chunks(query: str, user_id: str, supabase_client, top_k: int = 5, metadata_filter: Optional[dict] = None) -> List[dict]:
    """Embed query and search via hybrid (vector + keyword RRF) or vector-only RPC.
    Returns list of dicts with keys: content, document_id, file_name."""
    from app.services.ingestion import embed_text
    from app.services.settings import get_hybrid_search_enabled, get_reranking_enabled

    query_embedding = embed_text(query)
    hybrid = get_hybrid_search_enabled()
    reranking = get_reranking_enabled()

    if hybrid:
        fetch_count = top_k * 4 if reranking else top_k
        result = supabase_client.rpc("match_document_chunks_hybrid", {
            "query_embedding": query_embedding,
            "query_text": _sanitize_keyword_query(query),
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

    rows = result.data or []

    # Fetch document names for source attribution
    doc_ids = list(set(row["document_id"] for row in rows))
    doc_names = {}
    if doc_ids:
        docs = supabase_client.table("documents").select("id, file_name").in_("id", doc_ids).execute()
        doc_names = {d["id"]: d["file_name"] for d in (docs.data or [])}

    chunks = [
        {"content": row["content"], "document_id": row["document_id"], "file_name": doc_names.get(row["document_id"], "Unknown")}
        for row in rows
    ]

    if hybrid and reranking and len(chunks) > top_k:
        from app.services.reranker import rerank_chunks
        content_strings = [c["content"] for c in chunks]
        reranked_contents = rerank_chunks(query, content_strings, top_k)
        content_to_chunk = {c["content"]: c for c in chunks}
        chunks = [content_to_chunk[rc] for rc in reranked_contents if rc in content_to_chunk]

    return chunks[:top_k]


@traceable(name="search_documents", run_type="tool")
def _execute_search_documents(
    search_query: str,
    metadata_filter: Optional[dict],
    user_id: Optional[str],
    supabase_client=None,
) -> List[dict]:
    """Execute the search_documents tool call — traced as a tool in LangSmith."""
    if not supabase_client or not user_id:
        return []
    try:
        return retrieve_chunks(
            query=search_query,
            user_id=user_id,
            supabase_client=supabase_client,
            top_k=10,
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
            supabase_client=supabase_client, top_k=10,
            metadata_filter=manual_metadata_filter,
        )
        context = "\n\n---\n\n".join(
            f"[Source: {c['file_name']}]\n{c['content']}" for c in chunks
        ) if chunks else "No relevant documents found."
        system_text = f"""You are a helpful assistant with access to the user's uploaded documents.
Use the provided document excerpts to answer questions accurately.
If the excerpts do not contain enough information to answer, say so and answer from general knowledge if applicable.
{OUTPUT_FORMAT_RULES}

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

    # Build dynamic tool list — analyze_document listed first to reduce positional bias
    function_declarations = []
    if has_documents:
        try:
            function_declarations.append(_build_analyze_tool())
        except Exception as e:
            logger.warning(f"Failed to build analyze tool (non-fatal): {e}")
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

    # Emit a thinking event when tools are available so the frontend can show status
    if function_declarations:
        tool_names = [fd.name for fd in function_declarations]
        yield ("tool_thinking", json.dumps({"available_tools": tool_names}))

    # Pre-check: detect summarization intent and force analyze_document
    # Gemini Flash has a strong bias toward search_documents even when analyze_document
    # is clearly the right tool. This deterministic override catches the obvious cases.
    forced_tool = None
    if has_documents and function_declarations:
        import re
        last_user_msg = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "").lower()
        summarize_pattern = re.search(r'\b(summarize|summarise|summary|analyze|analyse|review|explain|overview)\b', last_user_msg)
        if summarize_pattern:
            # Check if user references a specific document (not a generic question)
            generic_terms = {'document', 'documents', 'file', 'files', 'my'}
            words = set(re.findall(r'\b\w+\b', last_user_msg))
            non_generic = words - generic_terms - {summarize_pattern.group()} - {'the', 'a', 'an', 'for', 'me', 'can', 'you', 'please', 'of', 'this', 'that', 'it', 'to', 'in', 'do'}
            if non_generic:
                forced_tool = "analyze_document"
                logger.info(f"Forcing analyze_document — detected summarization intent with specific terms: {non_generic}")

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
        context = "\n\n---\n\n".join(
            f"[Source: {c['file_name']}]\n{c['content']}" for c in chunks
        ) if chunks else "No relevant documents found."
        fallback_system = f"""You are a helpful assistant with access to the user's uploaded documents.
Use the provided document excerpts to answer questions accurately.
If the excerpts do not contain enough information to answer, say so and answer from general knowledge if applicable.
{OUTPUT_FORMAT_RULES}

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

        # Override tool choice if forced_tool is set and Gemini picked wrong
        if forced_tool and tool_name != forced_tool:
            logger.info(f"Overriding {tool_name} -> {forced_tool} (summarization intent detected)")
            last_msg = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
            # Keep the original search query as document_name hint — the ilike
            # fuzzy match in the analyze_document handler will resolve it
            tool_name = forced_tool
            original_query = args.get("query", "") or args.get("question", "") or last_msg
            args = {"document_name": original_query, "question": last_msg}

        logger.info(f"Tool call: {tool_name}(args={args})")

        # Emit tool_start event so frontend can show activity indicator
        yield ("tool_start", json.dumps({"tool": tool_name, "args": args}))

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
            if chunks:
                from collections import Counter
                doc_counts = Counter(c["file_name"] for c in chunks)
                result_text = "\n\n---\n\n".join(
                    f"[Source: {c['file_name']}]\n{c['content']}" for c in chunks
                )
                dominant_doc, dominant_count = doc_counts.most_common(1)[0]
                if dominant_count / len(chunks) >= 0.6:
                    result_text += (
                        f"\n\nNote: {dominant_count}/{len(chunks)} results came from '{dominant_doc}'. "
                        f"If these excerpts are insufficient, suggest the user ask to analyze that document in full."
                    )
                yield ("tool_done", json.dumps({"tool": tool_name, "detail": f"Found {len(chunks)} relevant excerpts"}))
            elif web_search_enabled:
                # Fallback: document search returned nothing, try web search
                logger.info(f"search_documents returned empty, falling back to web_search for: {search_query}")
                yield ("tool_done", json.dumps({"tool": tool_name, "detail": "No documents found"}))
                yield ("tool_start", json.dumps({"tool": "web_search", "args": {"query": search_query}}))
                from app.services.web_search import execute_web_search
                result_text = execute_web_search(search_query)
                tool_name = "web_search"
                yield ("tool_done", json.dumps({"tool": "web_search", "detail": "Web results retrieved"}))
            else:
                result_text = "No relevant documents found."
                yield ("tool_done", json.dumps({"tool": tool_name, "detail": "No documents found"}))

        elif tool_name == "query_structured_data":
            from app.services.sql_tool import execute_sql_query
            question = args.get("question", "")
            result_text = execute_sql_query(question, user_id, supabase_client)

            # If SQL failed and user has documents, fall back to document search
            if result_text.startswith("SQL query failed") and has_documents:
                logger.info(f"SQL tool failed, falling back to search_documents for: {question}")
                yield ("tool_done", json.dumps({"tool": tool_name, "detail": "SQL failed, falling back"}))
                yield ("tool_start", json.dumps({"tool": "search_documents", "args": {"query": question}}))
                chunks = _execute_search_documents(
                    search_query=question,
                    metadata_filter=None,
                    user_id=user_id,
                    supabase_client=supabase_client,
                )
                if chunks:
                    result_text = "\n\n---\n\n".join(
                        f"[Source: {c['file_name']}]\n{c['content']}" for c in chunks
                    )
                    tool_name = "search_documents"
                yield ("tool_done", json.dumps({"tool": tool_name, "detail": f"Found {len(chunks) if chunks else 0} results"}))
            else:
                yield ("tool_done", json.dumps({"tool": tool_name, "detail": "Query executed"}))

        elif tool_name == "web_search":
            from app.services.web_search import execute_web_search
            query = args.get("query", "")
            result_text = execute_web_search(query)
            yield ("tool_done", json.dumps({"tool": "web_search", "detail": "Web results retrieved"}))

        elif tool_name == "analyze_document":
            from app.services.sub_agent import run_sub_agent
            doc_name = args.get("document_name", "")
            question = args.get("question", "")

            # Resolve document_name → document_id via fuzzy match
            doc = supabase_client.table("documents") \
                .select("id, file_name") \
                .eq("user_id", user_id) \
                .ilike("file_name", f"%{doc_name}%") \
                .order("created_at", desc=True) \
                .limit(1).execute()

            if not doc.data:
                result_text = f"No document matching '{doc_name}' found."
            else:
                doc_id = doc.data[0]["id"]
                actual_name = doc.data[0]["file_name"]
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
        # Truncate very large results to avoid empty Gemini responses
        truncated_result = result_text[:16000] if len(result_text) > 16000 else result_text
        system_with_context = f"""You are a helpful assistant. Use the provided tool results to answer the user's question accurately.
If the tool encountered an error, explain the issue to the user in simple terms and suggest they rephrase their question.
If the results do not contain enough information, clearly state that the available documents do not contain the answer. Do NOT dump or echo the raw tool results back to the user. Instead, briefly explain what information was found (if any) and suggest the user try a different query or upload a document that might contain the answer. You may answer from general knowledge if applicable, but clearly label it as such.
When citing web sources, include the URLs.
{OUTPUT_FORMAT_RULES}

Tool ({tool_name}) results:
{truncated_result}"""

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

        # Safeguard: if streaming returned nothing, try non-streaming as fallback
        if not has_response2_text:
            logger.warning(f"Context injection streaming returned empty for tool={tool_name}, trying non-streaming fallback")
            try:
                fallback = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(system_instruction=system_with_context),
                )
                if fallback.candidates and fallback.candidates[0].content and fallback.candidates[0].content.parts:
                    for part in fallback.candidates[0].content.parts:
                        if part.text:
                            has_response2_text = True
                            yield ("token", part.text)
            except Exception as e:
                logger.warning(f"Non-streaming fallback also failed: {e}")

        # Last resort: yield the tool result directly
        if not has_response2_text and result_text:
            logger.warning(f"All context injection attempts failed for tool={tool_name}, yielding raw result")
            yield ("token", result_text)
    elif forced_tool == "analyze_document" and has_documents:
        # Gemini didn't call any tool but we detected summarization intent — force analyze_document
        logger.info("Gemini skipped tools but summarization detected — forcing analyze_document")
        last_msg = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        tool_name = "analyze_document"
        args = {"document_name": last_msg, "question": last_msg}
        yield ("tool_start", json.dumps({"tool": tool_name, "args": args}))

        from app.services.sub_agent import run_sub_agent
        doc_name = args.get("document_name", "")
        question = args.get("question", "")

        doc = supabase_client.table("documents") \
            .select("id, file_name") \
            .eq("user_id", user_id) \
            .ilike("file_name", f"%{doc_name}%") \
            .order("created_at", desc=True) \
            .limit(1).execute()

        if not doc.data:
            result_text = f"No document matching '{doc_name}' found."
        else:
            doc_id = doc.data[0]["id"]
            actual_name = doc.data[0]["file_name"]
            sub_agent_result = ""
            for evt_type, evt_data in run_sub_agent(doc_id, actual_name, question, user_id, supabase_client):
                yield (evt_type, evt_data)
                if evt_type == "sub_agent_done":
                    sub_agent_result = evt_data
            result_text = sub_agent_result

        if result_text:
            truncated_result = result_text[:16000] if len(result_text) > 16000 else result_text
            system_with_context = f"""You are a helpful assistant. Use the provided tool results to answer the user's question accurately.
{OUTPUT_FORMAT_RULES}

Tool (analyze_document) results:
{truncated_result}"""
            response2 = client.models.generate_content_stream(
                model=model, contents=contents,
                config=types.GenerateContentConfig(system_instruction=system_with_context),
            )
            for chunk in response2:
                if chunk.text:
                    yield ("token", chunk.text)
        yield ("tool_done", json.dumps({"tool": "analyze_document", "detail": "Document analyzed"}))
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

