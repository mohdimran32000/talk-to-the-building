"""Sub-agent service: deep single-document analysis via isolated Gemini call."""

import hashlib
import json
import logging
import time
from typing import Generator

from google.genai import types
from langsmith import traceable
from pydantic import BaseModel, Field

from app.services.exploration_tools._truncate import apply_12k_cap
from app.services.openai_client import _get_client
from app.services.settings import get_llm_model

logger = logging.getLogger(__name__)

MAX_CONTEXT_CHARS = 800_000  # Gemini context limit safety margin


# ============================================================================
# Phase 5 — Explorer sub-agent: bounded multi-turn knowledge-base exploration.
# Sibling to run_sub_agent (Module 8) above; reuses the same Generator-of-events
# shape. See 05-RESEARCH.md §Pattern 1 + §Tool Registration Boundary.
# ============================================================================

# EXPLORER-01 / EXPLORER-02: hard budgets enforced inside the per-turn loop.
MAX_TURNS = 8
WALL_CLOCK_BUDGET_S = 60.0
RESULT_CHAR_CAP = 12_000  # Pitfall 7 mitigation 3 — tighter than main agent's 16K
SSE_ARG_CAP = 500          # Per-arg cap when echoing tool args in sub_agent_tool_start

# EXPLORER-03 layer 1: module-level allowlist + setup-time assertion.
# The assert below fires AT MODULE IMPORT if a future maintainer accidentally
# adds analyze_document to the allowed set. Recursive sub-agents are forbidden
# — see PITFALLS.md Pitfall 7. Plan 02 enforces this at two more layers
# (tool-set builder + dispatch-time check) for defense in depth.
EXPLORER_ALLOWED_TOOLS = ("tree", "glob", "grep", "list_files", "read_document")
assert "analyze_document" not in EXPLORER_ALLOWED_TOOLS, (
    "EXPLORER-03 violation: analyze_document MUST NOT be registered in "
    "Explorer's toolset (no recursive sub-agents). See PITFALLS.md Pitfall 7."
)


class ExplorerArgs(BaseModel):
    """EXPLORER-04 Pydantic v2 args for the explore_knowledge_base tool.

    Single-purpose v1 surface: one open-ended query string. An optional `scope`
    narrowing arg is intentionally deferred to v2 (RESEARCH.md §Open Questions
    #6 [ASSUMED] — confirm with operator before changing). The LLM can always
    pass `scope` to individual tool calls inside the loop.
    """
    query: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Open-ended user question requiring multi-step KB exploration.",
    )
    model_config = {"extra": "ignore"}  # Phase 3 / Plan 01 LOCKED defense layer


def _signature(tool_name: str, args: dict) -> str:
    """Stable hash of (tool_name, args) for the no-progress detector.

    Whitespace-insensitive on dict keys via sort_keys=True; stable across
    CPython versions via hashlib.sha256. Per RESEARCH.md §Open Questions #2:
    hash args VERBATIM (no value normalization) — case sensitivity in regex
    and glob patterns is real and lowercase-normalizing string values would
    introduce false-equivalence bugs. Phase 4 already runs paths through
    normalize_path() at tool entry, so trailing/leading whitespace on paths
    is a non-issue.
    """
    canonical = json.dumps(
        {"tool": tool_name, "args": args},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


EXPLORER_SYSTEM_PROMPT = """You are an isolated knowledge-base exploration sub-agent.

Your job: given a user question that requires open-ended exploration of a document
knowledge base, use the precision tools below to locate the relevant information,
then return a COMPACT summary.

Available tools (5 only):
- tree(path, max_depth, scope) — see folder structure
- list_files(path, scope) — list one folder one level deep
- glob(pattern, path, type, scope) — find files by name pattern (e.g. '**/*.pdf')
- grep(pattern, path, scope, output_mode, A, B, C) — search inside document text
- read_document(document_id|path, offset, limit) — read line-numbered slice

HARD LIMITS (do not exceed):
- 8 tool calls maximum across this whole exploration
- 60 seconds wall-clock time
- Each tool result you receive is capped at 12,000 characters; if you see a
  truncation marker, your next call should NARROW (e.g. add `path` filter,
  reduce `max_depth`, narrow regex)

STRATEGY:
1. Start broad (tree at depth 2 or list_files at the most likely root).
2. Narrow to 1-2 candidate folders quickly — prefer adding `path` filter over
   re-searching with broader scope.
3. If you find the target, call read_document or grep with tight bounds to
   confirm and gather quotes.
4. STOP as soon as you have enough to answer. Do NOT call additional tools
   "just to be thorough" — token budget is precious.

DO NOT:
- Repeat the same tool call with the same arguments (no progress).
- Use analyze_document — it is NOT in your toolset (recursive sub-agents
  are forbidden).
- Echo raw tool output verbatim. Synthesize.

When you are ready, RESPOND WITH PLAIN TEXT (no further tool calls). Your text
will be the compact summary returned to the main agent.

Compact-summary format:
- <= 8 sentences
- Cite folder paths and document names with the scope tag (user|global)
- If you stopped early, say why ('hit turn budget', 'found enough', etc.)
"""


def _build_explorer_tool_set() -> list:
    """EXPLORER-03 layer 2: build the 5-tool set, asserting no analyze_document.

    Lazy import inside this function avoids the openai_client.py <-> sub_agent.py
    circular import (openai_client imports run_sub_agent; sub_agent imports the
    Phase 4 tool factories that live in openai_client). The import resolves the
    first time Explorer is invoked — AFTER both modules have loaded.
    """
    from app.services.openai_client import (
        _build_list_files_tool,
        _build_tree_tool,
        _build_glob_tool,
        _build_read_document_tool,
        _build_grep_tool,
    )
    declarations = [
        _build_list_files_tool(),
        _build_tree_tool(),
        _build_glob_tool(),
        _build_read_document_tool(),
        _build_grep_tool(),
    ]
    names = {fd.name for fd in declarations}
    # Defense-in-depth: even if a future maintainer mutates EXPLORER_ALLOWED_TOOLS
    # without touching the factory list (or vice versa), this assertion fires.
    assert names == set(EXPLORER_ALLOWED_TOOLS), (
        f"Explorer tool-set drift: declared={sorted(names)}, "
        f"allowed={sorted(EXPLORER_ALLOWED_TOOLS)}"
    )
    assert "analyze_document" not in names, "EXPLORER-03 violation"
    return [types.Tool(function_declarations=declarations)]


def _extract_function_call(response):
    """Return the first function_call part from a Gemini response, or None.

    Mirrors the extraction shape used in openai_client.py:843-859 (the main
    agent's tool-call detection). Returns the function_call object directly
    (with .name and .args attributes) or None if the model emitted plain text.
    """
    try:
        parts = response.candidates[0].content.parts
    except (AttributeError, IndexError):
        return None
    for part in parts or []:
        fc = getattr(part, "function_call", None)
        if fc and getattr(fc, "name", None):
            return fc
    return None


def _extract_text(response) -> str:
    """Concatenate all text parts of a Gemini response, returning '' if none."""
    try:
        parts = response.candidates[0].content.parts
    except (AttributeError, IndexError):
        return ""
    out = []
    for part in parts or []:
        text = getattr(part, "text", None)
        if text:
            out.append(text)
    return "".join(out)


def _truncate_args_for_sse(args: dict) -> dict:
    """Per-arg cap to SSE_ARG_CAP chars before echoing in sub_agent_tool_start.

    RESEARCH.md §Open Questions #4 recommendation: bound SSE message size by
    truncating long arg values at emit time (matches Phase 4's result_preview
    300-char discipline). Non-string values pass through untouched.
    """
    out = {}
    for k, v in (args or {}).items():
        if isinstance(v, str) and len(v) > SSE_ARG_CAP:
            out[k] = v[:SSE_ARG_CAP] + "...[truncated]"
        else:
            out[k] = v
    return out


def _dispatch_explorer_tool(
    tool_name: str,
    args,
    user_id: str,
    supabase_client,
) -> dict:
    """EXPLORER-03 layer 3: dispatch ONLY if tool_name is in the allowlist.

    Validates args via the Phase 4 Pydantic schemas (TOOL-06 invariant), then
    invokes the Phase 4 tool function (UNCHANGED — same contract Phase 4 ships).
    Lazy imports inside this function avoid eager-load cost when Explorer is
    not invoked.
    """
    if tool_name not in EXPLORER_ALLOWED_TOOLS:
        logger.warning(
            f"EXPLORER-03 dispatch-time block: tool_name={tool_name!r} "
            f"not in EXPLORER_ALLOWED_TOOLS"
        )
        return {"error": "TOOL_NOT_ALLOWED_IN_EXPLORER", "tool": tool_name}

    # Lazy imports (Pitfall 1 + lazy-load discipline)
    from app.services.exploration_tools.schemas import (
        TreeArgs, GlobArgs, GrepArgs, ListFilesArgs, ReadDocumentArgs,
    )
    from app.services.exploration_tools.list_files import list_files
    from app.services.exploration_tools.tree import tree
    from app.services.exploration_tools.glob_match import glob_match
    from app.services.exploration_tools.read_document import read_document
    from app.services.exploration_tools.grep import grep

    args_dict = dict(args) if args else {}
    try:
        if tool_name == "list_files":
            parsed = ListFilesArgs(**args_dict)
            return list_files(parsed, user_id, supabase_client)
        elif tool_name == "tree":
            parsed = TreeArgs(**args_dict)
            return tree(parsed, user_id, supabase_client)
        elif tool_name == "glob":
            parsed = GlobArgs(**args_dict)
            return glob_match(parsed, user_id, supabase_client)
        elif tool_name == "read_document":
            parsed = ReadDocumentArgs(**args_dict)
            return read_document(parsed, user_id, supabase_client)
        elif tool_name == "grep":
            parsed = GrepArgs(**args_dict)
            return grep(parsed, user_id, supabase_client)
    except Exception as e:
        logger.error(f"Explorer dispatch failed for {tool_name}: {e}", exc_info=True)
        return {"error": "DISPATCH_FAILED", "tool": tool_name, "detail": str(e)}

    # Defensive — should be unreachable due to allowlist guard above.
    return {"error": "UNHANDLED_TOOL_NAME", "tool": tool_name}


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


@traceable(name="explore_knowledge_base", run_type="chain")
def run_explorer_sub_agent(
    query: str,
    user_id: str,
    supabase_client,
) -> Generator[tuple[str, str], None, None]:
    """EXPLORER-01..06: bounded multi-turn loop over the 5 Phase 4 precision tools.

    Yields (event_type, data) tuples consumed by openai_client.stream_response()
    which forwards them through messages.event_generator (Plan 04) to the SSE
    wire. The contract is identical to run_sub_agent's three-event vocabulary
    plus two NEW event types for inner tool dispatch:

        ("sub_agent_start",      json)  — emitted ONCE at entry
        ("sub_agent_tool_start", json)  — per inner tool call (NEW Phase 5)
        ("sub_agent_tool_done",  json)  — per inner tool result (NEW Phase 5)
        ("sub_agent_token",      text)  — per token of compact summary
        ("sub_agent_done",       text)  — emitted ONCE at exit

    Bounds (EXPLORER-01 / EXPLORER-02):
      - MAX_TURNS=8 hard `for...range` ceiling (Pitfall 7 mitigation 1)
      - WALL_CLOCK_BUDGET_S=60.0 polled at top of each turn
      - No-progress detector via _signature(tool_name, args) — short-circuits
        on consecutive duplicate signatures
      - In-loop tool result truncation via apply_12k_cap(..., RESULT_CHAR_CAP)

    Recursive sub-agents (EXPLORER-03): the toolset built by
    _build_explorer_tool_set() does NOT include analyze_document. Module-level
    assert (Plan 01) + tool-set assert + dispatch-time guard provide three
    layers of defense.

    LangSmith (EXPLORER-06): the `@traceable(run_type="chain")` decorator
    creates a chain span; Phase 4 tool functions decorated with
    `@traceable(run_type="tool")` auto-nest as children via contextvars.
    No manual `with trace(...)` block needed.
    """
    # EXPLORER-04: emit start event with agent_name (Plan 04 routes this).
    yield ("sub_agent_start", json.dumps({
        "agent_name": "explore_knowledge_base",
        "question": query,
    }))

    start_time = time.monotonic()
    last_signature = ""
    short_circuit_reason: str | None = None

    try:
        client = _get_client()
        model = get_llm_model()
    except Exception as e:
        logger.error(f"Explorer client init failed: {e}", exc_info=True)
        yield ("sub_agent_done", f"Exploration unavailable: client init failed ({e})")
        return

    contents = [types.Content(role="user", parts=[types.Part(text=query)])]

    try:
        tools = _build_explorer_tool_set()
    except AssertionError as e:
        # EXPLORER-03 setup-time / build-time assert fired. Surface as a
        # graceful error rather than corrupting the SSE stream.
        logger.error(f"Explorer tool-set assertion failed: {e}", exc_info=True)
        yield ("sub_agent_done", f"Exploration unavailable: {e}")
        return

    # EXPLORER-01: hard `for ... in range(MAX_TURNS)` bound. NEVER use `while`.
    for turn in range(MAX_TURNS):
        # EXPLORER-02: wall-clock timeout at the top of each turn.
        if time.monotonic() - start_time > WALL_CLOCK_BUDGET_S:
            short_circuit_reason = "wall_clock_timeout"
            logger.info(f"Explorer wall-clock timeout at turn {turn}")
            break

        # Gemini call (non-streaming — we need the full function_call payload).
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=EXPLORER_SYSTEM_PROMPT,
                    tools=tools,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
        except Exception as e:
            logger.error(f"Explorer Gemini call failed at turn {turn}: {e}", exc_info=True)
            short_circuit_reason = "gemini_error"
            break

        fc = _extract_function_call(response)
        if fc is None:
            # Natural finish — Gemini emitted plain text, not a tool call.
            # Use the response text as the summary directly (no second call needed).
            summary = _extract_text(response) or "Exploration complete (no findings)."
            # Stream a coarse approximation of tokens (no-tool stream-finish path).
            # We forgo a second streaming call here because we already have the
            # full text; just emit it as a single sub_agent_token then done.
            if summary:
                yield ("sub_agent_token", summary)
            yield ("sub_agent_done", summary)
            return

        # EXPLORER-02: no-progress detector.
        args_dict = dict(fc.args) if fc.args else {}
        sig = _signature(fc.name, args_dict)
        if sig == last_signature:
            short_circuit_reason = "no_progress"
            logger.info(
                f"Explorer no-progress at turn {turn}: repeated {fc.name} "
                f"with same args"
            )
            break
        last_signature = sig

        # NEW Phase 5 event: emit tool_start BEFORE dispatch.
        yield ("sub_agent_tool_start", json.dumps({
            "tool": fc.name,
            "args": _truncate_args_for_sse(args_dict),
            "turn": turn,
        }))

        # Dispatch (EXPLORER-03 layer 3 enforced inside).
        try:
            result_dict = _dispatch_explorer_tool(
                fc.name, args_dict, user_id, supabase_client,
            )
        except Exception as e:
            # _dispatch_explorer_tool already catches its own exceptions and
            # returns an error dict, but be defensive — generator must not raise.
            logger.error(
                f"Explorer dispatch raised at turn {turn} for {fc.name}: {e}",
                exc_info=True,
            )
            result_dict = {"error": "DISPATCH_FAILED", "tool": fc.name, "detail": str(e)}

        # In-sub-agent 12K cap (Pitfall 7 mitigation 3 — tighter than main agent's 16K).
        truncated_dict = apply_12k_cap(result_dict, char_cap=RESULT_CHAR_CAP)
        truncated_text = json.dumps(truncated_dict, default=str)

        # NEW Phase 5 event: emit tool_done AFTER truncation.
        yield ("sub_agent_tool_done", json.dumps({
            "tool": fc.name,
            "result_preview": truncated_text[:300],
            "turn": turn,
        }))

        # Append the model's tool-call message + the tool's response to contents
        # for the next turn. This is the standard manual-loop idiom (Phase 1-4).
        try:
            contents.append(response.candidates[0].content)  # model's tool-call
        except (AttributeError, IndexError) as e:
            logger.error(f"Explorer contents.append failed at turn {turn}: {e}")
            short_circuit_reason = "contents_append_error"
            break

        contents.append(types.Content(
            role="user",
            parts=[types.Part(function_response=types.FunctionResponse(
                name=fc.name,
                response={"result": truncated_text},
            ))],
        ))
    else:
        # Python for-else: fires when the `for` loop exhausts naturally
        # (i.e., MAX_TURNS hit without `break`). Mark the reason for the
        # compact-summary prompt to mention "hit turn budget" if relevant.
        short_circuit_reason = "max_turns"
        logger.info(f"Explorer hit MAX_TURNS={MAX_TURNS}")

    # Compact-summary streaming call (NO tools registered; system prompt
    # mentions short_circuit_reason if present).
    summary_system = (
        f"{EXPLORER_SYSTEM_PROMPT}\n\n"
        f"Status: {short_circuit_reason or 'complete'}.\n"
        f"Now synthesize a COMPACT summary (<= 8 sentences) of what you found. "
        f"Cite folder paths and document names with their scope tag (user|global). "
        f"Do not echo raw tool output. "
    )
    if short_circuit_reason:
        summary_system += (
            f"You stopped early ({short_circuit_reason}); state that explicitly "
            f"in the summary."
        )

    full_summary = ""
    try:
        for chunk in client.models.generate_content_stream(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=summary_system),
        ):
            if chunk.text:
                full_summary += chunk.text
                yield ("sub_agent_token", chunk.text)
    except Exception as e:
        logger.error(f"Explorer summary streaming failed: {e}", exc_info=True)
        if not full_summary:
            full_summary = (
                f"Exploration ended ({short_circuit_reason or 'complete'}); "
                f"summary unavailable due to streaming error."
            )

    # V7 generator-never-raises: ALWAYS emit done, even on errors above.
    yield ("sub_agent_done", full_summary)
