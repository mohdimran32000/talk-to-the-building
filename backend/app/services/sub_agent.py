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
