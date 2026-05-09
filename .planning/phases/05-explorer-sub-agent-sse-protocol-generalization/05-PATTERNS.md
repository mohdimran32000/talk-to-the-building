# Phase 5: Explorer Sub-Agent + SSE Protocol Generalization — Pattern Map

**Mapped:** 2026-05-09
**Files analyzed:** 8 (1 backend service extension + 1 NEW helper package + 1 router extension + 1 SDK-wrapper extension + 2 frontend extensions + 2 test files)
**Analogs found:** 8 / 8 (100% — every Phase 5 file mirrors a Phase 1–4 precedent already shipped)

Phase 5 is *additive plumbing* over Module 8's `run_sub_agent` and Phase 4's five precision tools. The Explorer sub-agent reuses the **exact** generator-of-events shape from `sub_agent.py:18-97`; the SSE event_generator extension is purely additive on `messages.py:91-101`; the `openai_client.py` extension is one new factory + one new dispatch arm modeled on `analyze_document` (factory at L212–235, dispatch arm at L892–915). The frontend updates are minimal callback wiring extensions of lines that already exist (`api.ts:282-287`, `Chat.tsx:232-242`). The test suite mirrors `test_exploration_tools.py` line-for-line.

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `backend/app/services/sub_agent.py` (EXTEND) | service / sub-agent entrypoint (generator) | `(query: str, user_id: str, supabase_client) → Generator[(event_type, data)]` — multi-turn loop dispatching to 5 Phase 4 tools, ending with streaming compact summary | itself — `run_sub_agent` at `sub_agent.py:18-97` | exact (sibling function, same generator vocabulary) |
| `backend/app/services/explorer/` (NEW package — RECOMMENDED FOR HELPERS) | utility package — Pydantic args, no-progress hash, system prompt constants | `dict → ExplorerArgs (validated)`; `(tool_name, args) → sha256 signature str`; module-level prompt str | `backend/app/services/exploration_tools/{schemas,_truncate}.py` (Phase 4 helper package) | role-match (same package shape; new file content) |
| `backend/app/services/openai_client.py` (EXTEND) | service / dispatcher + tool factory | LLM `args` dict → factory builds `types.FunctionDeclaration`; `elif tool_name == "explore_knowledge_base":` arm forwards generator events from `run_explorer_sub_agent` and assigns final summary to `result_text` | itself — `_build_analyze_tool` at L212-235 + `analyze_document` dispatch arm at L892-915 | exact (additive — copy-paste-modify) |
| `backend/app/routers/messages.py` (EXTEND) | router / SSE generator | Sub-agent generator events → SSE JSON lines (dual-emit: legacy + generalized envelope) + `tool_metadata` accumulator with `tool_calls: [...]` array | itself — `event_generator` sub-agent arms at L91-101 | role-match (extended schema; same arm structure) |
| `frontend/src/lib/api.ts` (EXTEND) | frontend / SSE consumer | SSE JSON line → typed callback dispatch (parse new `{type:'sub_agent',...}` envelope + new `sub_agent_tool_*` types) | itself — sub-agent branches at L282-287 | exact (additive `else if` arms) |
| `frontend/src/pages/Chat.tsx` (EXTEND) | frontend / page wiring | callback events → React state (`setToolSteps`, `setSubAgentContent`, etc.) | itself — sub-agent callback wiring at L232-242 | exact (one new callback per new event) |
| `backend/scripts/test_explorer_sub_agent.py` (NEW) | test / integration suite | Live SSE chat → assertions on event ordering, span structure, MAX_TURNS bound, no-progress detection, tool_metadata persistence | `backend/scripts/test_exploration_tools.py` (1167 lines, 14 sections, canary + cleanup) blended with `test_sub_agents.py` (Module 8 sub-agent SSE assertions) | exact (3-way blend; same canary + cleanup discipline) |
| `backend/scripts/test_all.py` (EXTEND) | test runner / registry | static — one new import + one new SUITES tuple | itself — `("Folders", test_folders)` at L35 + `("Exploration", test_exploration_tools)` at L36 | exact (one-liner pattern Plan 04 already used twice) |

**Match quality legend:**
- `exact` — analog is the same role + data flow with the same toolchain; copy verbatim with surface-level edits.
- `role-match` — analog shares role and structural shape but the data being passed differs (extended schema, new file content); reuse the surface, swap the payload.

---

## Pattern Assignments

### `backend/app/services/sub_agent.py` (EXTEND — service / sub-agent entrypoint, generator-of-events)

**Analog:** itself — `run_sub_agent` at `sub_agent.py:18-97` is the singular precedent. Phase 5 adds `run_explorer_sub_agent` as a SIBLING function in the SAME file (Recommendation A from RESEARCH.md §Recommended Project Structure; Phase 5 retains single-file layout per `research/ARCHITECTURE.md:175`).

**Imports pattern** (`sub_agent.py:1-15`):
```python
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
```

**Phase 5 net-new imports** (RESEARCH.md §Pattern 1):
```python
import time
import hashlib
from app.services.exploration_tools._truncate import apply_12k_cap
# Lazy imports inside _build_explorer_tool_set (Pitfall 1 — circular import avoidance):
#   from app.services.openai_client import (_build_list_files_tool, _build_tree_tool, ...)
```

**Function signature pattern** (`sub_agent.py:18-25`):
```python
@traceable(name="sub_agent_analyze", run_type="chain")
def run_sub_agent(
    document_id: str,
    document_name: str,
    question: str,
    user_id: str,
    supabase_client,
) -> Generator[tuple[str, str], None, None]:
```

**Core pattern — generator yields three event types** (`sub_agent.py:35, 91, 97`):
```python
yield ("sub_agent_start", json.dumps({"document_name": document_name}))
# ... inside streaming loop:
yield ("sub_agent_token", chunk.text)
# at the end:
yield ("sub_agent_done", full_result)
```

**Error handling pattern — generator NEVER raises** (`sub_agent.py:80-95`):
```python
full_result = ""
try:
    response = client.models.generate_content_stream(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(system_instruction=system_prompt),
    )
    for chunk in response:
        if chunk.text:
            full_result += chunk.text
            yield ("sub_agent_token", chunk.text)
except Exception as e:
    logger.error(f"Sub-agent streaming failed: {e}")
    if not full_result:
        full_result = f"Sub-agent analysis failed: {e}"

yield ("sub_agent_done", full_result)
```

**Deltas Phase 5 introduces** (over the analog `run_sub_agent`):
1. **Bounded multi-turn loop** with `for turn in range(MAX_TURNS=8)` + `for-else` clause for natural exhaustion (analog is single-shot).
2. **Wall-clock timeout guard** — `start = time.monotonic()` once outside loop; check `time.monotonic() - start > 60.0` at top of each turn (EXPLORER-02).
3. **No-progress detector** — `last_signature: str` closure variable; per turn `sig = sha256(json.dumps({"tool":fc.name,"args":dict(fc.args)}, sort_keys=True).encode()).hexdigest()`; `if sig == last_signature: break` (Pitfall 7 mitigation 2).
4. **Two NEW yield event types** — `("sub_agent_tool_start", json)` per inner tool dispatch + `("sub_agent_tool_done", json)` per inner tool result (in addition to the existing `start`/`token`/`done` triplet).
5. **Tool-set builder + dispatch helper** — `_build_explorer_tool_set()` returns 5 Phase 4 declarations (NOT analyze_document); `_dispatch_explorer_tool(tool_name, args, user_id, supabase_client)` runs Pydantic validation + invokes the Phase 4 service function. EXPLORER-03 setup-time assertion at module load: `assert "analyze_document" not in EXPLORER_ALLOWED_TOOLS`.
6. **Aggressive 12K cap on tool results** via `apply_12k_cap(result_dict, char_cap=12_000)` BEFORE injecting back into Gemini contents (Pitfall 7 mitigation 3 — more aggressive than main agent's 16K).
7. **Final compact-summary streaming call** — after loop exit (or natural finish), one `client.models.generate_content_stream` call with NO tools registered + EXPLORER_SYSTEM_PROMPT instructing "≤ 8 sentences. Cite paths/names. State if early-stopped."
8. **Tracing decorator** — same `@traceable(name="explore_knowledge_base", run_type="chain")` (EXPLORER-06; matches `sub_agent.py:18`).

---

### `backend/app/services/explorer/` (NEW package — utility helpers)

**Analog:** `backend/app/services/exploration_tools/` (Phase 4 helper package). Specifically:
- `exploration_tools/schemas.py` — Pydantic v2 args module (analog for Phase 5's `ExplorerArgs`)
- `exploration_tools/_truncate.py` — single-purpose helper module (analog for Phase 5's `_signature` no-progress hash + `EXPLORER_SYSTEM_PROMPT` constant module)

**Pydantic v2 args pattern** (`exploration_tools/schemas.py:1-44`):
```python
"""TOOL-06: Pydantic v2 BaseModel argument schemas for the five exploration tools.

Every model uses:
  - Literal["user","global","both"] for scope
  - Field(..., ge=, le=) for numeric bounds
  - regex pattern for path
  - extra='ignore' (Phase 3 / Plan 01 LOCKED defense layer)
"""
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator

_PATH_RE = r"^/$|^/[^/]+(/[^/]+)*$"


class TreeArgs(BaseModel):
    """TOOL-01 args."""
    path: str = Field("/", pattern=_PATH_RE, description="Canonical folder path; '/' for root.")
    max_depth: int = Field(2, ge=1, le=4, description="...")
    scope: Literal["user", "global", "both"] = Field("both", description="...")

    model_config = {"extra": "ignore"}
```

**Phase 5 `ExplorerArgs` adaptation** (RESEARCH.md §Open Questions #6 — minimal v1):
```python
"""EXPLORER args — Pydantic v2 single-arg model for explore_knowledge_base."""
from pydantic import BaseModel, Field

class ExplorerArgs(BaseModel):
    """EXPLORER-04 args. Single-purpose: open-ended question."""
    query: str = Field(..., min_length=1, max_length=2000,
                       description="Open-ended user question requiring KB exploration.")
    model_config = {"extra": "ignore"}
```

**Single-purpose helper module pattern** (`_truncate.py:1-15`):
```python
"""TOOL-08: 12K-char truncation helper.

Applied at the END of every Phase 4 tool function. Stateless. Centralizes the
`[...truncated, N more entries]` marker contract so per-tool plans don't hand-roll
truncation.
"""
import json
from typing import Any


def apply_12k_cap(payload: dict, *, char_cap: int = 12_000) -> dict:
    """Truncate a tool result dict and append the `truncation_marker` if it overflows."""
```

**Phase 5 net-new files inside `explorer/`:**
1. `__init__.py` — empty or barrel re-exporting `ExplorerArgs`, `_signature`, `EXPLORER_SYSTEM_PROMPT`, `EXPLORER_ALLOWED_TOOLS`, `MAX_TURNS`, `WALL_CLOCK_BUDGET_S`, `RESULT_CHAR_CAP`.
2. `schemas.py` — `ExplorerArgs` (above; mirrors `exploration_tools/schemas.py` shape).
3. `_signature.py` — pure function (RESEARCH.md §Pattern 1):
   ```python
   import hashlib
   import json

   def _signature(tool_name: str, args: dict) -> str:
       """Stable hash of (tool_name, args) for the no-progress detector.

       Whitespace-insensitive on dict keys via sort_keys=True; stable across
       CPython versions via hashlib.sha256.
       """
       canonical = json.dumps({"tool": tool_name, "args": args},
                              sort_keys=True, default=str)
       return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
   ```
4. `prompt.py` — module-level `EXPLORER_SYSTEM_PROMPT` constant (RESEARCH.md §Explorer System Prompt Design — verbatim 8-turn / 60s / 12K-cap mandate).

**Decision deferred to discuss-phase / planner:** RESEARCH.md presents Recommendation A (single-file extension of `sub_agent.py`) as preferred. If the planner chooses A, the `explorer/` package collapses to module-level constants/helpers inside `sub_agent.py` itself; the per-helper file boundary above becomes section boundaries inside one file. The pattern excerpts remain valid either way.

**Cross-cutting constraints:**
- `_PATH_RE` byte-identical to `folder_service._CANONICAL_PATH_RE` if Explorer adds path-style args in v2 (currently v1 has no path).
- `model_config = {"extra": "ignore"}` is mandatory (Phase 3 / Plan 01 LOCKED — silently drop smuggled fields).

---

### `backend/app/services/openai_client.py` (EXTEND — SDK wrapper / dispatcher)

**Analog:** itself. Three insertion targets, all additive:

#### 1. `_build_explore_knowledge_base_tool()` factory

**Analog:** `_build_analyze_tool()` at `openai_client.py:212-235`:
```python
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
```

**Phase 5 net-new factory shape** (insertion point: after `_build_grep_tool()`):
```python
def _build_explore_knowledge_base_tool() -> types.FunctionDeclaration:
    """Build the explore_knowledge_base tool definition for open-ended exploration.

    Use when the user's question is open-ended ('where are X', 'what's in the KB
    about Y', 'find me all docs related to Z'). Spawns an isolated sub-agent that
    iteratively calls tree/glob/grep/list_files/read_document up to 8 times then
    returns a compact summary. Distinct from analyze_document (which targets a
    SPECIFIC named document). Recursive sub-agents forbidden — Explorer cannot
    call analyze_document or itself.
    """
    return types.FunctionDeclaration(
        name="explore_knowledge_base",
        description=(
            "REQUIRED for OPEN-ENDED exploration of the user's knowledge base. "
            "Use when the user asks 'where is X', 'find me everything about Y', or "
            "'what does the KB say about Z' and the answer requires multiple steps. "
            "Spawns an exploration sub-agent that uses tree, glob, grep, list_files, "
            "read_document for up to 8 turns then returns a compact summary. "
            "Distinct from analyze_document — that tool needs a specific document name."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "query": types.Schema(
                    type="STRING",
                    description="The open-ended exploration question.",
                ),
            },
            required=["query"],
        ),
    )
```

#### 2. Tool-registration loop addition

**Analog:** `openai_client.py:651-679` (Phase 4 registration loop):
```python
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
    # ... five Phase 4 try/except blocks ...
    try:
        function_declarations.append(_build_grep_tool())
    except Exception as e:
        logger.warning(f"Failed to build grep tool (non-fatal): {e}")
```

**Phase 5 inserts** (after `_build_grep_tool()` block):
```python
try:
    function_declarations.append(_build_explore_knowledge_base_tool())
except Exception as e:
    logger.warning(f"Failed to build explore_knowledge_base tool (non-fatal): {e}")
```

#### 3. Dispatch arm — sub-agent generator forwarding

**Analog:** `analyze_document` dispatch arm at `openai_client.py:892-915`:
```python
elif tool_name == "analyze_document":
    from app.services.sub_agent import run_sub_agent  # lazy import — circular avoidance
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
```

**Phase 5 net-new arm** (insertion point: after `analyze_document` arm OR after `grep` Phase 4 arm — order doesn't matter, just before `else: result_text = f"Unknown tool: {tool_name}"` at L1064):
```python
elif tool_name == "explore_knowledge_base":
    from app.services.sub_agent import run_explorer_sub_agent  # lazy import
    query = args.get("query", "")

    if not query:
        result_text = "explore_knowledge_base called with empty query."
    else:
        sub_agent_result = ""
        for evt_type, evt_data in run_explorer_sub_agent(query, user_id, supabase_client):
            yield (evt_type, evt_data)
            if evt_type == "sub_agent_done":
                sub_agent_result = evt_data
        result_text = sub_agent_result
```

#### 4. System prompt update (`_build_system_prompt`)

**Analog:** `openai_client.py:39-92` already has Phase 4 SEARCH-03 + precision-tools-overview bullets at L62-84.

**Phase 5 inserts** one new bullet inside `if has_documents:` block (after the precision-tools-overview bullet at L70-77):
```python
parts.append(
    "- For OPEN-ENDED exploration ('where are X', 'find me all docs about Y'), "
    "use `explore_knowledge_base`. It spawns a sub-agent that iteratively uses "
    "tree/glob/grep/list_files/read_document and returns a compact summary. "
    "Use analyze_document for specific named documents; use explore_knowledge_base "
    "when the user's question requires multi-step exploration to even find what to read."
)
```

#### 5. TOOL-09 layered-fallback wrapper — UNCHANGED

**Source:** `openai_client.py:1068-1113`. Phase 5's `result_text` flows through this UNCHANGED. Plan 03's plan-checker MUST verify these lines remain bit-identical.

```python
truncated_result = result_text[:16000] if len(result_text) > 16000 else result_text
system_with_context = f"""You are a helpful assistant. ...
{OUTPUT_FORMAT_RULES}

Tool ({tool_name}) results:
{truncated_result}"""
# ... streaming + non-streaming retry + raw-yield fallback ...
```

**Cross-cutting constraints:**
- Lazy import inside the `elif` arm (Pitfall 1 — `sub_agent.py` imports `_get_client` from `openai_client.py`; eager top-level import would be circular).
- `result_text` MUST be assigned in the new arm (TOOL-09 invariant — wrapper at L1068+ executes after the `if has_function_call:` branch closes; no tool reinvents the wrapper).
- The new factory MUST be wrapped in `try/except` like Phase 4 — non-fatal failure (RESEARCH.md doesn't mandate this, but precedent does at every other factory site).

---

### `backend/app/routers/messages.py` (EXTEND — SSE generator + tool_metadata accumulator)

**Analog:** itself — `event_generator` sub-agent arms at `messages.py:91-101`:

**Existing arms** (verbatim from HEAD):
```python
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
```

**Existing tool_metadata persistence** (`messages.py:111-120`):
```python
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
```

**Phase 5 deltas — additive only, dual-emit for one release window** (RESEARCH.md §Pattern 2 + §SSE Protocol Generalization Strategy):

1. **Refactor `sub_agent_start` arm** to support BOTH agent names + dual-emit:
```python
elif event_type == "sub_agent_start":
    parsed = json.loads(data)
    sub_agent_id = str(uuid.uuid4())
    if not tool_metadata:
        tool_metadata = {"tools_used": []}
    agent_name = parsed.get("agent_name", "analyze_document")  # legacy fallback
    slot = {"tool": agent_name, "sub_agent_id": sub_agent_id, "tool_calls": []}
    if agent_name == "analyze_document":
        slot["document_name"] = parsed.get("document_name", "")
    elif agent_name == "explore_knowledge_base":
        slot["question"] = parsed.get("question", "")
    tool_metadata["tools_used"].append(slot)
    # OLD shape (kept for one release — BACKWARDS-COMPAT)
    yield json.dumps({"type": "sub_agent_start", **parsed})
    # NEW shape (Phase 5 NEW)
    yield json.dumps({
        "type": "sub_agent",
        "agent_name": agent_name,
        "event": "start",
        "payload": {"sub_agent_id": sub_agent_id, **parsed},
    })
```

2. **TWO new arms** — `sub_agent_tool_start` + `sub_agent_tool_done` (NEW event types from Explorer's per-turn yields):
```python
elif event_type == "sub_agent_tool_start":
    parsed = json.loads(data)
    if tool_metadata and tool_metadata["tools_used"]:
        slot = tool_metadata["tools_used"][-1]
        slot.setdefault("tool_calls", []).append({
            "tool": parsed["tool"],
            "args": parsed.get("args", {}),
        })
    yield json.dumps({"type": "sub_agent_tool_start", **parsed})  # legacy
    yield json.dumps({                                               # generalized
        "type": "sub_agent",
        "agent_name": "explore_knowledge_base",
        "event": "tool_start",
        "payload": parsed,
    })

elif event_type == "sub_agent_tool_done":
    parsed = json.loads(data)
    if tool_metadata and tool_metadata["tools_used"]:
        slot = tool_metadata["tools_used"][-1]
        if slot.get("tool_calls"):
            slot["tool_calls"][-1]["result_preview"] = parsed.get("result_preview", "")[:300]
    yield json.dumps({"type": "sub_agent_tool_done", **parsed})
    yield json.dumps({
        "type": "sub_agent",
        "agent_name": "explore_knowledge_base",
        "event": "tool_done",
        "payload": parsed,
    })
```

3. **Refactor `sub_agent_token` arm** for dual-emit:
```python
elif event_type == "sub_agent_token":
    yield json.dumps({"type": "sub_agent_token", "content": data})
    yield json.dumps({
        "type": "sub_agent",
        "event": "token",
        "payload": {"content": data},
    })
```

4. **Refactor `sub_agent_done` arm** — accumulator now `tools_used[-1]` (last sub-agent slot, not `[0]`):
```python
elif event_type == "sub_agent_done":
    if tool_metadata and tool_metadata["tools_used"]:
        slot = tool_metadata["tools_used"][-1]
        slot["sub_agent_result"] = data[:300]
    yield json.dumps({"type": "sub_agent_done"})
    yield json.dumps({
        "type": "sub_agent",
        "event": "done",
        "payload": {"summary": data[:300]},
    })
```

5. **Add `import uuid` at top** of messages.py (currently imports `json`, `logging`).

**Cross-cutting constraints:**
- **Dual-emit invariant:** ALL four sub-agent event arms emit BOTH the legacy shape AND the generalized envelope for ONE release window. Phase 6's frontend update consumes the generalized shape; legacy emissions are removed in Phase 6's plan-checker (Pitfall 12 mitigation 1).
- **`tool_metadata.tools_used` is now an ARRAY** (was [0]-indexed for one analyze_document slot; Phase 5 supports multi-sub-agent messages). All accumulator updates target `tools_used[-1]` (the most recent slot).
- **`result_preview` capped at 300 chars** in JSONB (V8 Data Protection — bounds doc-content exposure in trace logs; matches Phase 4 `result_preview: data[:300]` discipline).
- **Generator never raises** — outer `try/except Exception as e:` block at L104-108 already handles all errors (mirrors `sub_agent.py:92-95` failure path).

---

### `frontend/src/lib/api.ts` (EXTEND — SSE consumer + Message interface)

**Analog:** itself — sub-agent SSE branches at `api.ts:282-287`:

**Existing branches** (verbatim from HEAD):
```typescript
} else if (parsed.type === 'sub_agent_start') {
  onSubAgentStart?.(parsed)
} else if (parsed.type === 'sub_agent_token') {
  onSubAgentToken?.(parsed.content)
} else if (parsed.type === 'sub_agent_done') {
  onSubAgentDone?.()
} else if (parsed.type === 'done') {
  onDone(parsed.response_id)
}
```

**Existing `Message` interface** (`api.ts:34-47`):
```typescript
export interface Message {
  id: string
  thread_id: string
  role: 'user' | 'assistant'
  content: string
  tool_metadata?: {
    tools_used: Array<{
      tool: string
      document_name?: string
      sub_agent_result?: string
    }>
  } | null
  created_at: string
}
```

**Existing `sendMessage` callback signature** (`api.ts:204-218`):
```typescript
export async function sendMessage(
  threadId: string,
  content: string,
  onToken: (token: string) => void,
  onDone: (responseId: string) => void,
  signal?: AbortSignal,
  metadataFilter?: Record<string, any>,
  onSubAgentStart?: (data: { document_name: string }) => void,
  onSubAgentToken?: (token: string) => void,
  onSubAgentDone?: () => void,
  onError?: (message: string) => void,
  onToolThinking?: (data: ToolThinkingEvent) => void,
  onToolStart?: (data: ToolStartEvent) => void,
  onToolDone?: (data: ToolDoneEvent) => void,
)
```

**Phase 5 deltas:**

1. **Extend `Message.tool_metadata.tools_used[]` shape** to support new fields:
```typescript
export interface Message {
  // ...
  tool_metadata?: {
    tools_used: Array<{
      tool: string
      document_name?: string                     // analyze_document only
      question?: string                          // explore_knowledge_base only
      sub_agent_id?: string                      // NEW (Phase 5)
      tool_calls?: Array<{                       // NEW (Phase 5 — Explorer trace)
        tool: string
        args?: Record<string, any>
        result_preview?: string
      }>
      sub_agent_result?: string
    }>
  } | null
  // ...
}
```

2. **Extend `sendMessage` signature** with two new callbacks:
```typescript
onSubAgentStart?: (data: {
  agent_name?: string
  document_name?: string
  question?: string
  sub_agent_id?: string
}) => void,
onSubAgentToolStart?: (data: { tool: string; args?: Record<string, any>; turn?: number }) => void,  // NEW
onSubAgentToolDone?: (data: { tool: string; result_preview?: string; turn?: number }) => void,    // NEW
```

3. **Add new SSE branches** (insertion point: after `sub_agent_done` branch at L286):
```typescript
} else if (parsed.type === 'sub_agent_tool_start') {       // NEW (Phase 5 legacy)
  onSubAgentToolStart?.(parsed)
} else if (parsed.type === 'sub_agent_tool_done') {        // NEW (Phase 5 legacy)
  onSubAgentToolDone?.(parsed)
} else if (parsed.type === 'sub_agent') {                  // NEW (Phase 5 generalized envelope)
  // Phase 5 dual-emit: prefer generalized envelope when present.
  // Phase 6's frontend overhaul switches exclusively to this branch.
  if (parsed.event === 'start') onSubAgentStart?.({ ...parsed.payload, agent_name: parsed.agent_name })
  else if (parsed.event === 'tool_start') onSubAgentToolStart?.(parsed.payload)
  else if (parsed.event === 'tool_done') onSubAgentToolDone?.(parsed.payload)
  else if (parsed.event === 'token') onSubAgentToken?.(parsed.payload.content)
  else if (parsed.event === 'done') onSubAgentDone?.()
}
```

**Cross-cutting constraints:**
- **Dual-listening invariant:** Phase 5 frontend listens to BOTH legacy types (`sub_agent_start`, `sub_agent_token`, `sub_agent_done`, `sub_agent_tool_start`, `sub_agent_tool_done`) AND the generalized envelope (`sub_agent` with `event` field). To avoid double-firing callbacks, Phase 5's minimal wiring listens to EITHER the legacy events OR the generalized envelope, NOT both — pick one as the canonical channel for Phase 5 (legacy is fine; Phase 6 switches to generalized exclusively).
- **No breaking changes to existing callbacks:** `onSubAgentStart` signature must accept the legacy `{document_name}` payload AND the new `{agent_name, question, sub_agent_id}` payload. Use optional fields (already shown above).
- **TypeScript discriminated union recommended** for `tool_metadata.tools_used[]` to type-narrow on `tool: 'analyze_document' | 'explore_knowledge_base'` — but Phase 5 minimal wiring uses optional fields (Phase 6 can refine).

---

### `frontend/src/pages/Chat.tsx` (EXTEND — callback wiring)

**Analog:** itself — sub-agent callback wiring at `Chat.tsx:232-242`:

**Existing wiring** (verbatim from HEAD):
```typescript
// Sub-agent callbacks
(data) => {
  setIsSubAgentActive(true)
  setSubAgentDocName(data.document_name)
  setSubAgentContent('')
},
(token) => {
  setSubAgentContent((prev) => prev + token)
},
() => {
  setIsSubAgentActive(false)
},
```

**Existing tool-activity wiring** (`Chat.tsx:244-260`):
```typescript
// Tool activity callbacks
() => {
  setIsToolThinking(true)
},
(data) => {
  setIsToolThinking(false)
  setToolSteps((prev) => [...prev, { tool: data.tool, args: data.args, status: 'running' }])
},
(data) => {
  setToolSteps((prev) =>
    prev.map((s) =>
      s.tool === data.tool && s.status === 'running'
        ? { ...s, status: 'done', detail: data.detail }
        : s
    )
  )
},
```

**Existing state** (`Chat.tsx:38-42`):
```typescript
const [subAgentContent, setSubAgentContent] = useState('')
const [isSubAgentActive, setIsSubAgentActive] = useState(false)
const [subAgentDocName, setSubAgentDocName] = useState('')
const [toolSteps, setToolSteps] = useState<ToolStep[]>([])
const [isToolThinking, setIsToolThinking] = useState(false)
```

**Phase 5 deltas — minimal viable wiring** (full UI rendering deferred to Phase 6's UI-10):

1. **Extend `onSubAgentStart` callback** to handle both agent names:
```typescript
(data) => {
  setIsSubAgentActive(true)
  setSubAgentDocName(data.document_name || data.question || '')  // either field
  setSubAgentContent('')
},
```

2. **Add `onSubAgentToolStart` callback** — append to `toolSteps` (under the active sub-agent):
```typescript
(data) => {
  // Explorer's inner tool dispatch — render under the active sub-agent banner.
  // Phase 5 minimum-viable: just append to toolSteps with a sub_agent flag.
  setToolSteps((prev) => [...prev, {
    tool: data.tool,
    args: data.args,
    status: 'running',
    isSubAgent: true,           // NEW field on ToolStep type — Phase 6 renders nested
    turn: data.turn,
  }])
},
```

3. **Add `onSubAgentToolDone` callback** — flip status:
```typescript
(data) => {
  setToolSteps((prev) => prev.map((s) =>
    s.isSubAgent && s.tool === data.tool && s.status === 'running'
      ? { ...s, status: 'done', detail: data.result_preview, turn: data.turn }
      : s
  ))
},
```

4. **Optionally extend `ToolStep` type** in `frontend/src/components/ToolActivity.tsx` to include `isSubAgent?: boolean` and `turn?: number` (Phase 5 minimum viable; Phase 6 finalizes).

**Cross-cutting constraints:**
- **Phase 5 is plumbing-only** — full nested-tree rendering inside `SubAgentSection` (or a new `ExplorerSection`) is **Phase 6's UI-10 deliverable**. Phase 5 just ensures events flow through and state updates reflect them.
- **State variable reuse:** `subAgentDocName` repurposed to hold either document name (analyze_document) OR query (explore_knowledge_base). Phase 6 likely renames this and adds a typed discriminator.
- **No new state slots required** — `toolSteps` already exists; `isSubAgent` + `turn` fields are additive.

---

### `backend/scripts/test_explorer_sub_agent.py` (NEW — TEST-03 integration suite)

**Analog:** `backend/scripts/test_exploration_tools.py` (1167 lines, Phase 4 TEST-02) — primary structural analog. Secondary blends:
- `backend/scripts/test_folders.py` (Phase 3, 591 lines) — original 10-section template, `_tracked_*` discipline, ThreadPoolExecutor pattern.
- `backend/scripts/test_sub_agents.py` (Module 8) — sub-agent SSE assertion idioms (`sub_agent_start` → `sub_agent_token` → `sub_agent_done` ordering).

**Top-of-module bootstrap pattern** (`test_exploration_tools.py:1-76`):
```python
"""Phase 4 / TEST-02: integration tests for the five exploration tools + search_documents extension.

Sections:
  [Phase 4 setup canary]              — Migration 020 RPCs + indexes + backend reachable
  [Tool surface smoke]                — all 5 tools + 5 Pydantic Args importable
  [TOOL-06 strict args]               — Pydantic v2 validation
  ...
PREREQUISITE (must be complete before running this test):
  1. Migration 020 applied via: cd backend && venv/Scripts/python scripts/run_migrations.py
  2. Backend running on http://localhost:8001
  3. backend/.env contains SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY.

If any prerequisite is missing, the canary precheck (_verify_phase4_setup) returns
a single FAIL h.test + early-returns with an actionable [FATAL] message naming Plan 01.

CRITICAL: per CLAUDE.md, tests must NEVER delete all user data.
"""
import concurrent.futures
import json
import os
import re
import sys
import time
import uuid
from collections import defaultdict

import requests

# Reconfigure stdout/stderr to UTF-8 so emoji/arrow/box-drawing chars don't crash
# the suite on Windows cp1252 consoles.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

# Two-step sys.path bootstrap (matches test_folders.py:43-45).
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

import test_helpers as h  # noqa: E402
from supabase import create_client  # noqa: E402

STORAGE_BUCKET = "documents"

# Tracking lists for scoped cleanup. Per CLAUDE.md: never bulk-delete.
_tracked_documents: list = []
_tracked_folders: list = []
_tracked_storage_paths: list = []
```

**Canary precheck pattern** (`test_exploration_tools.py:97-198`):
```python
def _verify_phase4_setup(sb_admin):
    """Pre-flight canary: Migration 020 + Phase 4 tool surface + backend reachable.

    Probes:
      1. grep_documents RPC exists (Plan 01).
      2. match_document_chunks_with_filters accepts match_folder_path keyword (Plan 01).
      3. backend responds at BASE_URL.

    Returns (ok: bool, message: str). Mirrors test_folders.py::_verify_phase3_setup.
    """
    last_err = None
    rpc_ok = False
    for attempt in range(3):
        try:
            r = sb_admin.rpc("grep_documents", {...}).execute()
            if r.data is None:
                last_err = "grep_documents returned no data..."
                continue
            rpc_ok = True
            break
        except Exception as e:
            err_str = str(e).lower()
            transient = "520" in err_str or "timeout" in err_str or "cloudflare" in err_str
            last_err = (...)
            if not transient: break
            time.sleep(2 ** attempt)
    if not rpc_ok: return False, last_err
    # Probe 2: backend reachable
    try:
        r2 = requests.get(f"{h.BASE_URL}/health", timeout=5)
        if r2.status_code != 200: return False, (...)
    except Exception as e: return False, (...)
    return True, "ok"
```

**Cleanup pattern** (`test_exploration_tools.py:201-253`):
```python
def _cleanup():
    """Per-id batched .delete().in_(...) discipline (CLAUDE.md mandatory rule).

    Two-step delete: chunks first, then document. NEVER `DELETE FROM` without a
    tracked-id WHERE clause; never TRUNCATE; never bulk-delete the whole table.
    """
    BATCH = 500
    docs_by_client: dict = defaultdict(list)
    for did, client in _tracked_documents:
        docs_by_client[id(client)].append((did, client))
    for _client_id, items in docs_by_client.items():
        client = items[0][1]
        ids = [did for did, _ in items]
        for batch_start in range(0, len(ids), BATCH):
            batch = ids[batch_start:batch_start + BATCH]
            try:
                client.table("document_chunks").delete().in_("document_id", batch).execute()
            except Exception: pass
            try:
                client.table("documents").delete().in_("id", batch).execute()
            except Exception: pass
    # ... folders + storage ...
    _tracked_documents.clear(); _tracked_folders.clear(); _tracked_storage_paths.clear()
```

**Phase 5 deltas — 10 sections** (RESEARCH.md §Test Suite Structure):

| Section | Coverage | Required ID |
|---|---|---|
| 1 | `_verify_phase5_setup` canary — `run_explorer_sub_agent` importable + `explore_knowledge_base` registered as Gemini tool + backend reachable | TEST-03 |
| 2 | MAX_TURNS=8 hard bound — monkeypatch loop counter; assert ≤ 8 inner tool calls in one Explorer chat (deliberately broad query against 50-doc fixture) | EXPLORER-01 |
| 3 | Wall-clock 60s timeout — monkeypatch `WALL_CLOCK_BUDGET_S=0.1`; assert generator yields `sub_agent_done` within 1s | EXPLORER-02 |
| 4 | No-progress detector — stub Gemini to repeat the same `(tool, args)` twice; assert short-circuit + `sub_agent_done` within 2 turns | EXPLORER-02 |
| 5 | Recursive sub-agent rejection — assert `analyze_document` NOT in `EXPLORER_ALLOWED_TOOLS`; assert AssertionError on tampered constant; assert `_build_explorer_tool_set()` returns 5 names | EXPLORER-03 |
| 6 | Generalized SSE — live chat via `/api/threads/{id}/messages`; assert both old shape (`sub_agent_start`, `sub_agent_token`, `sub_agent_done`, `sub_agent_tool_start`, `sub_agent_tool_done`) AND new generalized envelope (`{type:'sub_agent', event, payload}`) emitted | EXPLORER-04 |
| 7 | Dual sub-agent in one conversation — chat triggering BOTH `analyze_document` AND `explore_knowledge_base`; assert `tool_metadata.tools_used` array has TWO entries | EXPLORER-04 |
| 8 | tool_metadata JSONB persistence — chat → GET messages → parse `tool_metadata`; assert `tool_calls: [...]` array intact, `sub_agent_id` present, `result_preview` ≤ 300 chars | EXPLORER-05 |
| 9 | LangSmith span structure — query LangSmith SDK for the run; assert chain span has ≤ 8 tool children, each with `result_size_bytes ≤ 12_000`; SKIP gracefully without `LANGSMITH_API_KEY` | EXPLORER-06 |
| 10 | Empty-response Pitfall 8 carry-forward — Explorer summary on a 50K-char fixture flows through TOOL-09 wrapper; `len(tokens) > 0` | TOOL-09 cross-cutting |

**Cross-cutting constraints:**
- **Cleanup discipline:** ZERO bulk `DELETE FROM` / `TRUNCATE`. ALL deletes use `.delete().in_("id", batch)` with tracked-id batches of 500 (CLAUDE.md mandatory; Phase 4 already enforces via static grep gate).
- **Canary returns single FAIL on missing prereq:** `_verify_phase5_setup` returns `(False, "[FATAL] Plan 02 not applied — run_explorer_sub_agent missing from sub_agent.py")` with actionable Plan reference. Suite early-returns `(passed=0, failed=1)` — no contamination from incomplete fixtures.
- **`@traceable` import smoke:** Module top imports `from app.services.sub_agent import run_explorer_sub_agent` to surface EXPLORER-03 setup-time `AssertionError` in CI even before any chat triggers it.
- **Optional LangSmith assertion** SKIPs gracefully without `LANGSMITH_API_KEY` (matches Phase 4 / Section 9 psycopg2 EXPLAIN SKIP idiom at `test_exploration_tools.py:303-308`).
- **Live SSE parsing** uses `h.parse_sse_stream(response)` helper (test_helpers.py — already exists for Phase 4).
- **UTF-8 stdout reconfigure** at module top (Windows console safety; `test_exploration_tools.py:48-52`).

---

### `backend/scripts/test_all.py` (EXTEND — test runner / registry)

**Analog:** itself. Two precedents already in place — line 17 imports `test_folders` (Phase 3); line 18 imports `test_exploration_tools` (Phase 4); SUITES list at L29-46 has `("Folders", test_folders)` at L35 and `("Exploration", test_exploration_tools)` at L36.

**Existing pattern** (`test_all.py:14-46`):
```python
import test_messages
import test_files
import test_folders         # NEW (Phase 3)
import test_exploration_tools  # NEW (Phase 4)
import test_backfill
import test_rag
# ...
SUITES = [
    ("Health", test_health),
    ("Auth", test_auth),
    ("Threads", test_threads),
    ("Messages", test_messages),
    ("Files", test_files),
    ("Folders", test_folders),       # NEW (Phase 3 — folders is logically a Files extension)
    ("Exploration", test_exploration_tools),  # NEW (Phase 4)
    ("Backfill", test_backfill),
    ("RAG", test_rag),
    # ...
]
```

**Phase 5 diff** (RESEARCH.md §Files To Be Created/Modified):
```python
# After line 18 (import test_exploration_tools), add:
import test_explorer_sub_agent  # NEW (Phase 5)

# Inside SUITES list, after the Exploration tuple at L36, add:
("Explorer", test_explorer_sub_agent),       # NEW (Phase 5 — explore_knowledge_base sub-agent)
```

**Why between Exploration and Backfill:** Topological order — Explorer depends on Phase 4's five precision tools (Exploration suite must pass first to confirm tools work standalone); Backfill is independent and runs after.

---

## Shared Patterns

These cross-cutting patterns apply to **multiple** Phase 5 files. Plans should reference them once and apply consistently.

### LangSmith @traceable Decorator (EXPLORER-06)

**Source:** four existing sites — `sub_agent.py:18` (`@traceable(name="sub_agent_analyze", run_type="chain")`), `openai_client.py:251` (`@traceable(name="search_documents", run_type="tool")`), `openai_client.py:553` (`_execute_search_documents`), Phase 4 tools at `exploration_tools/{list_files,tree,glob_match,read_document,grep}.py:32,34,48,39,46` (all `@traceable(...,run_type="tool")`).

**Apply to:** `run_explorer_sub_agent` (Phase 5 Plan 02) — single decorator placement at function definition.

```python
from langsmith import traceable

@traceable(name="explore_knowledge_base", run_type="chain")
def run_explorer_sub_agent(query: str, user_id: str, supabase_client) -> Generator[tuple[str, str], None, None]:
    ...
```

**Auto-nesting invariant:** Phase 4 tool functions (`list_files`, `tree`, `glob_match`, `read_document`, `grep`) are ALREADY `@traceable(run_type="tool")`. When invoked from inside the Explorer's `chain` span, LangSmith's contextvars-based context propagation picks them up as nested children automatically — NO manual `with trace(...)` block needed. EXPLORER-06 is satisfied by **a single decorator** on `run_explorer_sub_agent`.

### `apply_12k_cap` 12K Truncation (Pitfall 7 + Pitfall 8 mitigation)

**Source:** `backend/app/services/exploration_tools/_truncate.py:1-50`
**Apply to:** Every Explorer tool result BEFORE injection back into Gemini contents (RESEARCH.md §Sub-Agent Loop Architecture row "Tool result truncation").

```python
from app.services.exploration_tools._truncate import apply_12k_cap

# Inside the Explorer turn loop, after dispatching a tool:
result_dict = _dispatch_explorer_tool(fc.name, fc.args, user_id, supabase_client)
truncated_dict = apply_12k_cap(result_dict, char_cap=RESULT_CHAR_CAP)  # 12_000
truncated_text = json.dumps(truncated_dict, default=str)
# Inject truncated_text as FunctionResponse.response = {"result": truncated_text}
```

**Reuse, don't reinvent:** Phase 5 imports the existing helper bit-for-bit. The `char_cap=12_000` argument is already the helper's default; explicit passing for clarity.

### TOOL-09 Layered-Fallback Wrapper — UNCHANGED

**Source:** `backend/app/services/openai_client.py:1068-1113`
**Apply to:** `result_text` from `explore_knowledge_base` dispatch arm flows through this wrapper UNCHANGED (TOOL-09 invariant; Phase 4 also obeys this).

```python
truncated_result = result_text[:16000] if len(result_text) > 16000 else result_text
system_with_context = f"""You are a helpful assistant. ...
{OUTPUT_FORMAT_RULES}

Tool ({tool_name}) results:
{truncated_result}"""
# ... streaming + non-streaming retry + raw-yield fallback ...
```

**Plan 03's plan-checker MUST verify these lines remain bit-identical.** No tool reinvents the wrapper. No tool calls `client.models.generate_content_stream` itself — the wrapper does.

### Lazy Import Inside Dispatch Arm (Pitfall 1 — circular avoidance)

**Source:** `openai_client.py:893` (`from app.services.sub_agent import run_sub_agent` inside the `elif tool_name == "analyze_document":` arm).
**Apply to:** Phase 5's new `elif tool_name == "explore_knowledge_base":` arm (`from app.services.sub_agent import run_explorer_sub_agent`).

```python
elif tool_name == "explore_knowledge_base":
    from app.services.sub_agent import run_explorer_sub_agent  # lazy import
    ...
```

**Why lazy:** `sub_agent.py` imports `_get_client` from `openai_client.py`. Eager top-level import would be circular. The lazy `import` inside the elif resolves at first chat that triggers Explorer, AFTER `openai_client.py` has fully loaded.

### Generator Never Raises (V7 Error Handling)

**Source:** `sub_agent.py:80-95`
**Apply to:** `run_explorer_sub_agent` AND each Gemini call inside the turn loop.

```python
try:
    response = client.models.generate_content(...)
except Exception as e:
    logger.error(f"Explorer Gemini call failed at turn {turn}: {e}")
    short_circuit_reason = "gemini_error"
    break  # exit loop — proceed to compact-summary call

# After loop:
yield ("sub_agent_done", full_summary)  # always emitted, even on errors
```

**Invariant:** generators that raise corrupt the SSE stream and break the FastAPI response. Mirror `sub_agent.py:92-95` failure path — log + set `full_result` to error message + still emit `sub_agent_done`.

### Cleanup Discipline (CLAUDE.md verbatim)

**Source:** `backend/scripts/test_exploration_tools.py:201-253`
**Apply to:** `test_explorer_sub_agent.py::_cleanup()` — verbatim batched per-id `.delete().in_(...)` pattern.

```python
def _cleanup():
    BATCH = 500
    # Per-client per-id batched deletes — NEVER bulk DELETE FROM, NEVER TRUNCATE.
    docs_by_client: dict = defaultdict(list)
    for did, client in _tracked_documents:
        docs_by_client[id(client)].append((did, client))
    for _client_id, items in docs_by_client.items():
        client = items[0][1]
        ids = [did for did, _ in items]
        for batch_start in range(0, len(ids), BATCH):
            batch = ids[batch_start:batch_start + BATCH]
            try:
                client.table("document_chunks").delete().in_("document_id", batch).execute()
            except Exception: pass
            try:
                client.table("documents").delete().in_("id", batch).execute()
            except Exception: pass
    # ... folders + storage_paths ...
    _tracked_documents.clear()
```

**Static grep gate:** Phase 3 added a CI gate that fails any test file containing `DELETE FROM` or `TRUNCATE` outside an `.eq("id", ...)` clause. Phase 5 inherits this gate.

### Canary `_verify_phaseN_setup` Pattern

**Source:** `test_exploration_tools.py:97-198`, `test_folders.py:94-128`
**Apply to:** `test_explorer_sub_agent.py::_verify_phase5_setup` — probe Plan 02 (run_explorer_sub_agent importable), Plan 03 (explore_knowledge_base in tool registry), backend reachable. Three transient-infra retries on each probe (Cloudflare 5xx, timeouts).

### Pydantic v2 `model_config = {"extra": "ignore"}`

**Source:** `backend/app/services/exploration_tools/schemas.py:44, 60, 81, ...` (every model)
**Apply to:** `ExplorerArgs` (Phase 5).

```python
class ExplorerArgs(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    model_config = {"extra": "ignore"}   # Phase 3 / Plan 01 LOCKED defense layer
```

**Why explicit:** Pydantic v2's implicit default IS `extra='ignore'`, but explicit declaration documents the contract for the LLM-tool-args layer where exotic args are likely.

### Dual-Emit SSE Invariant (Pitfall 12 mitigation)

**Apply to:** `messages.py::event_generator` for ALL sub-agent event arms (`sub_agent_start`, `sub_agent_token`, `sub_agent_done`, `sub_agent_tool_start`, `sub_agent_tool_done`).

**Pattern:** every arm emits TWO `yield` statements — one with the legacy shape, one with the generalized envelope. Phase 6's frontend update consumes the generalized shape; legacy emissions are removed in Phase 6's plan-checker (one-release dual-emit window).

```python
yield json.dumps({"type": "sub_agent_start", **parsed})           # LEGACY
yield json.dumps({                                                  # GENERALIZED
    "type": "sub_agent",
    "agent_name": agent_name,
    "event": "start",
    "payload": {"sub_agent_id": sub_agent_id, **parsed},
})
```

**Pitfall 12 mitigation 1 mandate:** "generalize NOW, not later" at the second sub-agent integration point (which is Phase 5).

### EXPLORER-03 Triple Defense Against Recursive Sub-Agents

**Source:** RESEARCH.md §Tool Registration Boundary (3 layers).
**Apply to:** `sub_agent.py` (Phase 5).

```python
# Layer 1: setup-time assertion (fires at MODULE IMPORT)
EXPLORER_ALLOWED_TOOLS = ("tree", "glob", "grep", "list_files", "read_document")
assert "analyze_document" not in EXPLORER_ALLOWED_TOOLS, (
    "EXPLORER-03 violation: analyze_document MUST NOT be registered. "
    "Recursive sub-agents are forbidden — see PITFALLS.md Pitfall 7."
)

# Layer 2: tool-set builder runtime guard
def _build_explorer_tool_set() -> list[types.Tool]:
    declarations = [_build_list_files_tool(), _build_tree_tool(), ...]  # 5 only
    names = {fd.name for fd in declarations}
    assert names == set(EXPLORER_ALLOWED_TOOLS), f"Explorer tool-set drift: {names}"
    assert "analyze_document" not in names, "EXPLORER-03 violation"
    return [types.Tool(function_declarations=declarations)]

# Layer 3: dispatch-time tool-name check
def _dispatch_explorer_tool(tool_name, args, user_id, supabase_client):
    if tool_name not in EXPLORER_ALLOWED_TOOLS:
        return {"error": "TOOL_NOT_ALLOWED_IN_EXPLORER", "tool": tool_name}
    # ... dispatch ...
```

**Test coverage:** `test_explorer_sub_agent.py` Section 5 explicitly imports `run_explorer_sub_agent` at module top to surface Layer 1 AssertionError in CI.

---

## No Analog Found

No Phase 5 file is fully analog-less. Every component has a Phase 1–4 precedent:

| File | Role | Closest Analog | Net-new content |
|---|---|---|---|
| `sub_agent.py::run_explorer_sub_agent` | sub-agent generator | `sub_agent.py::run_sub_agent` | Multi-turn loop, no-progress detector, EXPLORER_SYSTEM_PROMPT, tool-set builder + dispatch helpers |
| `explorer/` package | helper package | `exploration_tools/` (schemas + _truncate) | `_signature` hash function, `EXPLORER_SYSTEM_PROMPT` constant |
| `messages.py::event_generator` (extended) | SSE generator | itself | Generalized envelope dual-emit, `tool_calls: [...]` array accumulator |
| `api.ts::sendMessage` (extended) | SSE consumer | itself | `parsed.type === 'sub_agent'` envelope branch, two new callbacks |
| `Chat.tsx` callback wiring | frontend | itself | Two new callbacks for `tool_start`/`tool_done` |
| `test_explorer_sub_agent.py` | integration suite | `test_exploration_tools.py` blended with `test_sub_agents.py` | Section 2 (MAX_TURNS bound), Section 3 (wall-clock), Section 4 (no-progress), Section 9 (LangSmith span) |
| `test_all.py` registration | test runner | itself | One import + one SUITES tuple |

**Net-new components inside otherwise-precedented files:**

- **Bounded `for turn in range(MAX_TURNS)` loop with `for-else` natural-exhaustion clause** — RESEARCH.md §Pattern 1 is the source of truth (no in-tree precedent for multi-turn LLM loops).
- **`_signature` no-progress hash** — `hashlib.sha256(json.dumps(..., sort_keys=True).encode()).hexdigest()` — no in-tree precedent (RESEARCH.md §Sub-Agent Loop Architecture row "No-progress detector" is the spec).
- **Wall-clock budget guard** — `time.monotonic()` checkpoint at top of each turn (no in-tree precedent; standard Python idiom).
- **Generalized SSE envelope** `{type:'sub_agent', agent_name, event, payload}` — RESEARCH.md §Pattern 2 / §SSE Protocol Generalization Strategy is the spec; in-tree precedent is the LEGACY shape it replaces.
- **`tool_metadata.tools_used[].tool_calls[]` array** — additive JSONB schema; in-tree precedent is the flat structure at `messages.py:99-100`. RESEARCH.md §Pattern 3 has the recursive schema.
- **EXPLORER_SYSTEM_PROMPT** — RESEARCH.md §Explorer System Prompt Design provides the verbatim text (8-turn / 60s / 12K-cap budget mandate).

---

## Metadata

**Analog search scope:**
- `backend/app/services/sub_agent.py` (97 lines — full Module 8 sub-agent — the singular precedent for `run_explorer_sub_agent`)
- `backend/app/services/openai_client.py` (1173 lines — `_build_*_tool` factories at L95-336, dispatch arms at L800-1066, TOOL-09 wrapper at L1068-1113, system prompt at L39-92)
- `backend/app/routers/messages.py` (125 lines — full SSE event_generator at L66-125, tool_metadata accumulator at L91-101+L111-120)
- `backend/app/services/exploration_tools/{schemas,_truncate}.py` (Phase 4 helper package — analogs for Phase 5's `explorer/` package shape)
- `backend/scripts/test_exploration_tools.py` (1167 lines — Phase 4 TEST-02 — primary structural analog for Phase 5 TEST-03)
- `backend/scripts/test_all.py` (84 lines — full SUITES registry)
- `frontend/src/lib/api.ts` (295 lines — full SSE consumer + Message interface + sendMessage signature)
- `frontend/src/pages/Chat.tsx` (340 lines — full state + callback wiring)
- `.planning/phases/05-explorer-sub-agent-sse-protocol-generalization/05-RESEARCH.md` (1497 lines — exhaustive technical research)
- `.planning/phases/04-five-exploration-tools-search-documents-extension/04-PATTERNS.md` (1211 lines — Phase 4 pattern map for the analog work)

**Files scanned:** 12 (8 source/test files at HEAD + 2 planning docs + 2 helper modules)

**Pattern extraction date:** 2026-05-09

**Confidence breakdown:**
- File classification: HIGH — every file's role + data flow is explicitly enumerated in RESEARCH.md §Files To Be Created/Modified + §Pattern-mapper inputs
- Analog selection: HIGH — Phase 4 just shipped (78/0 tests green per `04-09-SUMMARY.md`); patterns are fresh and codified; Module 8's `run_sub_agent` is the singular precedent for sub-agent shape
- Shared-pattern extraction: HIGH — `@traceable`, `apply_12k_cap`, lazy-import-in-dispatch, generator-never-raises, cleanup discipline, dual-emit invariant, EXPLORER-03 triple defense are all single-source-of-truth lines/functions
- Net-new components: MEDIUM — RESEARCH.md provides skeletons (§Pattern 1, §Pattern 2, §Pattern 3, §Sub-Agent Loop Architecture, §Tool Registration Boundary, §SSE Protocol Generalization, §Explorer System Prompt Design); planner must compose `run_explorer_sub_agent` + `_dispatch_explorer_tool` + dual-emit arm body, but every primitive is well-documented and every behavior has a corresponding TEST-03 section

---

## PATTERN MAPPING COMPLETE

**Phase:** 5 — Explorer Sub-Agent + SSE Protocol Generalization
**Files classified:** 8
**Analogs found:** 8 / 8 (100%)

### Coverage
- Files with exact analog: 6 (`sub_agent.py` extension; `openai_client.py` factory + dispatch + prompt; `api.ts` SSE branches; `Chat.tsx` callbacks; `test_explorer_sub_agent.py`; `test_all.py` registration)
- Files with role-match analog: 2 (`messages.py` SSE generator extension — extended schema shape; `explorer/` helper package — same `exploration_tools/` package shape, new file content)
- Files with no analog: 0

### Key Patterns Identified
- All sub-agents follow the `Generator[tuple[str, str], None, None]` shape with `("sub_agent_start", json)` / `("sub_agent_token", text)` / `("sub_agent_done", text)` event vocabulary; Phase 5 adds `("sub_agent_tool_start", json)` and `("sub_agent_tool_done", json)` for inner tool dispatch.
- Tool dispatch in `openai_client.py` is purely additive: `_build_*_tool()` factory + `try/except` registration + `elif tool_name == "...":` arm assigning to `result_text`; the TOOL-09 layered-fallback wrapper at L1068-1113 handles all final streaming.
- SSE events are emitted from `messages.py::event_generator` with a one-release dual-emit window: legacy `sub_agent_*` types AND generalized `{type:'sub_agent', event, payload}` envelope; Phase 6 removes legacy.
- Test suites use `_verify_phaseN_setup` canary + `_tracked_*` per-id batched cleanup + 10-section structure + module-top UTF-8 stdout reconfigure (Windows safety).
- LangSmith spans auto-nest via `@traceable` decorator's contextvars — single decorator on `run_explorer_sub_agent`, no manual `with trace(...)` blocks.

### File Created
`C:\RAG Automators\claude-code-agentic-rag-masterclass-ep2\.planning\phases\05-explorer-sub-agent-sse-protocol-generalization\05-PATTERNS.md`

### Ready for Planning
Pattern mapping complete. Planner can now reference analog file paths + line numbers + concrete excerpts in PLAN.md files for Plans 02 (sub_agent.py), 03 (openai_client.py), 04 (messages.py), 05 (frontend), and 06 (tests).
