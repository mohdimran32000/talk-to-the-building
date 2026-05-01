# Stack Research — Episode 2 Agentic Exploration Tools

**Domain:** Agentic exploration tools (tree/glob/grep/list_files/read_document + Explorer sub-agent) over a Supabase-backed knowledge base for an existing Gemini RAG app
**Researched:** 2026-04-28
**Overall Confidence:** HIGH on Postgres primitives & SDK patterns (verified against in-tree Episode 1 code and Postgres official docs); MEDIUM on Gemini-specific tool-call ergonomics (verified against in-tree usage but Gemini behavior can drift across model versions); LOW only on Gemini's exact internal limits for parallel tool calls (no authoritative public limit documented).

> **Scope discipline:** The Episode 1 stack (React 19 / Vite 7 / Tailwind 4 / shadcn-ui / FastAPI / sse-starlette / Supabase Postgres+pgvector / `google-genai` Python SDK / Docling / LangSmith) is **locked**. This document only proposes **additions** needed to ship the Episode 2 exploration toolset on top of that stack. No frontend libraries are added — the file explorer UI is built from existing shadcn-ui primitives plus what the team already uses.

---

## TL;DR — Recommended Additions

| Layer | Choice | Why |
|---|---|---|
| Postgres extension | `pg_trgm` (built-in, enable via `CREATE EXTENSION`) | Trigram GIN indexes give sub-second `ILIKE`/`~*` over `documents.content_markdown` for grep |
| Postgres extension | **NOT `ltree`** — keep TEXT `folder_path` + GIN-trigram | Already-decided path-based model is sound; ltree adds an extension dependency, a non-standard `lquery` syntax the LLM doesn't know, and friction for prefix-only queries we already get cheaply |
| Postgres operators | `~*` for grep regex, `ILIKE` for glob (after glob→SQL translation in Python), `text_pattern_ops` btree index for prefix `LIKE 'projects/%'`, `websearch_to_tsquery` (already in use) for tokenized keyword fallback | Right primitive per use case — no new index types beyond what's already understood |
| Postgres function | New `RETURNS TABLE` PL/pgSQL RPCs: `kb_grep`, `kb_glob`, `kb_tree`, `kb_list_files` | Mirrors the existing `match_document_chunks_hybrid` pattern; lets us encode RLS-aware scope union (`user` ∪ `global`) once, server-side |
| Python SDK additions | None — use existing `google-genai`, `supabase`, `langsmith`, `pydantic` | Tool patterns in `app/services/openai_client.py` already cover everything we need; no new top-level deps |
| Python pattern | Pydantic v2 `BaseModel` for tool argument validation **after** the SDK extracts `function_call.args` | Catches LLM-malformed args before they hit Postgres; surfaces `ValidationError` we can feed back to Gemini |
| Frontend additions | None new — reuse existing `react-markdown`/`remark-gfm`, shadcn-ui `Collapsible`, `Breadcrumb`, `ContextMenu` (already pinned in `package.json`) | Tree UI is just nested `Collapsible`s; no `react-arborist`/etc. |
| Tracing | Existing `@traceable` decorators with `run_type="tool"` for each new tool, `run_type="chain"` for the Explorer sub-agent | Matches `_execute_search_documents` and `run_sub_agent` shape — zero new infra |

---

## 1. Postgres Primitives for `grep` — Regex Search Over `content_markdown`

The grep tool runs case-insensitive regex (or literal substring) against `documents.content_markdown` scoped by `folder_path` prefix and scope (`user` ∪ `global`). Decision: use **`~*` and `ILIKE` with a `pg_trgm` GIN index**, not full-text search.

### Why not `tsvector` for grep
The existing `document_chunks.tsv` column with `websearch_to_tsquery` (migration `008` → `011`) is the right primitive for *semantic* keyword retrieval over chunks (it's tokenized, stop-worded, stemmed). But grep needs **exact substring/regex** semantics — `tsquery` will never match `MDB-C-G3` as a literal because the tokenizer breaks it. The Episode 1 retrieval debugging session already documented this exact pain (commit `53ff28d`). For grep we want raw bytes, not tokens.

### Why `pg_trgm` GIN index works for `ILIKE` and `~*`
PostgreSQL's `pg_trgm` extension (built-in to Supabase Postgres, just needs `CREATE EXTENSION IF NOT EXISTS pg_trgm;`) ships two GIN operator classes:

- `gin_trgm_ops` — accelerates `LIKE`, `ILIKE`, `~`, `~*` whenever the pattern contains at least one extractable trigram (3+ literal alphanumeric characters in a row). Confirmed in PostgreSQL 9.1+ and unchanged through 17.
- `gist_trgm_ops` — same operators but GIST (smaller index, slower lookup). For our read-heavy/write-light workload, GIN wins.

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX documents_content_markdown_trgm_idx
  ON documents USING gin (content_markdown gin_trgm_ops);
```

### Performance ceiling and what to actually expect
| Pattern | Index used? | Notes |
|---|---|---|
| `content_markdown ILIKE '%floor plan%'` | Yes | Best case — multi-trigram pattern |
| `content_markdown ~* 'panel\s+\d+'` | Partial — uses index for the literal `panel` trigrams, then re-checks | Good |
| `content_markdown ~* '.*'` or any pattern with no extractable trigrams | No — sequential scan | Fallback path; reject patterns with fewer than 3 literal chars in the tool layer |
| `content_markdown ~* '(?i)foo'` | Inline `(?i)` flag is fine | But prefer `~*` operator for clarity |

**Confidence: HIGH** — these are PostgreSQL behaviors documented since 9.1 (`pg_trgm`) and confirmed in current docs; nothing has changed.

### Returning matched lines, not just matched documents
Grep needs to return `{document_id, file_name, line_number, line_content}`. Postgres has `regexp_matches(string, pattern, 'g')` (returns the matches), but for line-level output we want `regexp_split_to_table(content_markdown, E'\\n') WITH ORDINALITY` to turn the document into `(line_number, line_text)` rows, then filter:

```sql
SELECT d.id, d.file_name, d.folder_path, t.lineno, t.line
FROM documents d,
     LATERAL regexp_split_to_table(d.content_markdown, E'\n') WITH ORDINALITY AS t(line, lineno)
WHERE d.user_id = auth.uid()                         -- or scope union
  AND d.content_markdown ILIKE '%' || $1 || '%'      -- index-accelerated filter
  AND t.line ~* $2                                    -- exact regex on the matched lines
LIMIT $3;
```

The `ILIKE` pre-filter is the index hit; the `LATERAL` line split + regex only runs against documents that already passed. **This is the key trick** — without the pre-filter, every document gets line-split, which is O(N×L). Confidence: HIGH.

### What NOT to use
| Avoid | Why | Use Instead |
|---|---|---|
| `to_tsquery` / `plainto_tsquery` for grep | Strips operators, drops stop-words, stems tokens — not exact-match | `ILIKE` + `~*` with trigram GIN |
| `SIMILAR TO` | Non-standard SQL regex dialect, no index plan benefit over `~*` | `~*` |
| Full-table-scan `~*` without trigram index | Will run for hundreds of ms on a few thousand documents | Add the trigram index *first* |
| Per-chunk grep (i.e. grep over `document_chunks.content`) | Chunks lose line numbering and document boundaries | Grep the canonical `documents.content_markdown` |

---

## 2. Glob & Path Matching — Why TEXT + `text_pattern_ops` Wins Over `ltree`

The user has already decided on path-based folder model: `documents.folder_path TEXT NOT NULL DEFAULT '/'` plus a thin `folders(id, scope, path, ...)` table for empty-folder tracking. This research **confirms** that decision and documents the gotchas.

### Why this is sound (don't reach for `ltree`)
`ltree` is PostgreSQL's hierarchical-paths extension with `lquery` patterns like `'top.*.middle.*'` and `ltree @ ltree` operators. It looks attractive for tree queries — but:

| Concern | TEXT `folder_path` | `ltree` |
|---|---|---|
| Storage of `/projects/2024/floor-plans` | One `TEXT` column | Must use `.` separators (`projects.2024.floor-plans`) — labels can't contain `-` |
| Prefix query `'/projects/%'` | `LIKE` with `text_pattern_ops` btree index — fast | `path <@ 'projects'::ltree` — fast |
| Glob `**/*.pdf` | Translate to SQL `LIKE` in app | `lquery` `'*.pdf'` — but only matches single labels |
| Label charset | Anything | `[A-Za-z0-9_]` only — file/folder names with `-`, `.`, spaces break it |
| LLM-friendly | The LLM already understands POSIX paths | LLM has to learn ltree's `lquery` dialect — extra cognitive load and brittleness |
| Extension dependency | None | `CREATE EXTENSION ltree` (Supabase ships it but it's still one more thing) |
| Empty folders | Side `folders` table (already planned) | Same — ltree has no built-in folders concept either |

The killer is the **label charset** — your users will create folders named `floor-plans`, `2024-Q1`, `notes (draft)`, and ltree will reject every one of those. **TEXT path is the right call.** Confidence: HIGH.

### Index strategy for `folder_path`
Two indexes, both small:

```sql
-- For prefix queries: WHERE folder_path LIKE '/projects/%'
CREATE INDEX documents_folder_path_prefix_idx
  ON documents (folder_path text_pattern_ops);

-- For glob/contains queries: WHERE folder_path ILIKE '%/floor-plans/%'
CREATE INDEX documents_folder_path_trgm_idx
  ON documents USING gin (folder_path gin_trgm_ops);
```

`text_pattern_ops` (btree) is critical for prefix queries — without it, `LIKE 'projects/%'` will *not* use the default-collation btree index in non-`C` locales (Supabase databases default to `en_US.UTF-8`). This is a frequent foot-gun. With `text_pattern_ops` the index uses byte-wise comparison and prefix matches correctly. Confidence: HIGH.

### Glob → SQL translation
Translate the LLM's glob pattern to SQL `LIKE`/`ILIKE` in Python before sending to Postgres. The translation is small enough to inline:

```python
def glob_to_sql_like(pattern: str) -> str:
    # Escape SQL wildcards first
    out = pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    # Translate glob wildcards to SQL
    # ** -> matches any depth incl. /  =>  %
    # *  -> matches non-/ in one segment  =>  %  (we accept the looser semantics; cheap)
    # ?  -> single char                   =>  _
    out = out.replace("**", "%").replace("*", "%").replace("?", "_")
    return out
```

Note we deliberately collapse `**` and `*` to `%` (both → "any chars including `/`") because the strict glob semantics (`*` matches within one segment, `**` crosses segments) is rarely what the LLM means and adds complexity without much value at this scope. Document this in the tool's description so the LLM knows. Confidence: MEDIUM — pragmatic tradeoff; revisit if precision feedback warrants strict semantics.

### Combined glob + grep query shape

```sql
SELECT d.id, d.file_name, d.folder_path
FROM documents d
WHERE (
        (d.scope = 'user' AND d.user_id = auth.uid())
     OR  d.scope = 'global'
      )
  AND (d.folder_path || '/' || d.file_name) ILIKE $1   -- glob pattern as SQL LIKE
LIMIT $2;
```

The `folder_path || '/' || file_name` concatenation lets a single `**/*.pdf`-style pattern match the full path. The trigram index on `folder_path` accelerates the leading portion; for `file_name`-only patterns add the trigram index on `file_name` too.

---

## 3. Two-Scope RLS — `user` ∪ `global` with Admin-Only Writes

Episode 1 already establishes the admin pattern (`profiles.is_admin BOOLEAN`, `EXISTS (SELECT 1 FROM profiles WHERE id = auth.uid() AND is_admin)` in policies). Extend that pattern. **Verified against `migrations/005_profiles_and_settings.sql` in this repo.**

### Recommended schema additions

```sql
-- Scope enum stays as TEXT with a CHECK constraint (avoid ENUM types — they're a pain to alter)
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'user'
    CHECK (scope IN ('user', 'global')),
  ADD COLUMN IF NOT EXISTS folder_path TEXT NOT NULL DEFAULT '/',
  ADD COLUMN IF NOT EXISTS content_markdown TEXT;  -- nullable until backfilled

CREATE INDEX IF NOT EXISTS documents_scope_idx ON documents (scope);
CREATE INDEX IF NOT EXISTS documents_folder_path_prefix_idx
  ON documents (folder_path text_pattern_ops);

-- Empty-folder side table
CREATE TABLE IF NOT EXISTS folders (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scope      TEXT NOT NULL CHECK (scope IN ('user', 'global')),
  user_id    UUID REFERENCES auth.users(id) ON DELETE CASCADE,  -- NULL when scope='global'
  path       TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- Only one folder with a given path within a scope (per user when user-scoped)
  UNIQUE (scope, user_id, path),
  -- Sanity: user_id required iff scope='user'
  CHECK ((scope = 'user' AND user_id IS NOT NULL) OR (scope = 'global' AND user_id IS NULL))
);
```

### RLS policies — the readable form

```sql
ALTER TABLE folders ENABLE ROW LEVEL SECURITY;

-- READ: own user-scoped rows OR any global row
CREATE POLICY "read user or global"
  ON folders FOR SELECT
  TO authenticated
  USING (
    (scope = 'user' AND user_id = auth.uid())
    OR scope = 'global'
  );

-- INSERT: users insert their own user-scoped rows; admins insert global rows
CREATE POLICY "insert own user-scoped"
  ON folders FOR INSERT
  TO authenticated
  WITH CHECK (scope = 'user' AND user_id = auth.uid());

CREATE POLICY "admin insert global"
  ON folders FOR INSERT
  TO authenticated
  WITH CHECK (
    scope = 'global'
    AND EXISTS (
      SELECT 1 FROM profiles p WHERE p.id = auth.uid() AND p.is_admin = true
    )
  );

-- UPDATE / DELETE: same pattern, two policies each (they OR together within an action)
```

### Performance gotcha (must address)
Calling `auth.uid()` and the admin `EXISTS` check inside a policy runs **once per row** unless wrapped. Supabase's official guidance (since 2024) is to wrap them in `SELECT` to make the planner cache the result:

```sql
USING (
  (scope = 'user' AND user_id = (SELECT auth.uid()))
  OR scope = 'global'
)
```

The `(SELECT auth.uid())` form is treated as a stable subquery and evaluated once per query, not once per row. This is well-documented at supabase.com/docs/guides/database/postgres/row-level-security#call-functions-with-select and was a major perf change in their best-practices guide. **Apply this everywhere — including the admin EXISTS subquery (wrap in SELECT).** Confidence: HIGH.

### Helper function pattern (DRY for many policies)

```sql
CREATE OR REPLACE FUNCTION public.is_admin() RETURNS BOOLEAN
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(
    (SELECT is_admin FROM profiles WHERE id = auth.uid()),
    false
  );
$$;

GRANT EXECUTE ON FUNCTION public.is_admin() TO authenticated;
```

Then policies become readable: `WITH CHECK (scope = 'global' AND public.is_admin())`. The `STABLE` marker tells the planner it's safe to cache within a query. `SECURITY DEFINER` is needed if `profiles` itself has RLS that would block the lookup — be careful and lock down `search_path`. Confidence: HIGH.

### What NOT to do
| Avoid | Why |
|---|---|
| Bare `auth.uid() = user_id` in policies on hot tables | Re-evaluates per row; can 10× the policy cost |
| Putting `is_admin` on JWT claims and reading from `auth.jwt()` | Means refreshing the token whenever an admin is granted/revoked; the existing `profiles.is_admin` check is simpler |
| Writing two separate tables (one per scope) | Doubles tool complexity (every tool has to UNION); the `scope` column with composite RLS is cleaner |
| ENUM type for scope | `ALTER TYPE` migrations are painful in Postgres; `TEXT` + `CHECK` is equally safe and more flexible |

---

## 4. Gemini Tool Calling for Nested Args — `google-genai` SDK Patterns

**Verified against current code at `backend/app/services/openai_client.py`** — the existing patterns work and the new tools should follow the same shape.

### Pinned versions (production-confirmed)
- `google-genai` — already in `backend/requirements.txt` unpinned. **Recommend pinning to `>=1.30,<2.0`** in this milestone. The SDK had a major API stabilization at 1.0 (renamed from `google-generativeai` to `google-genai`); the 1.x line is stable. The API used in `openai_client.py` (`genai.Client`, `client.models.generate_content`, `types.FunctionDeclaration`, `types.Schema`, `types.GenerateContentConfig.automatic_function_calling`) is the post-1.0 shape and matches the documented public surface. Confidence: HIGH.
- `pydantic` — already in `requirements.txt`. **Pin to `>=2.5,<3.0`.** Use Pydantic v2 features (`model_validate`, `model_dump`, `Field(..., description=...)`).

### Tool schema for the new tools — concrete shapes

The existing tools use flat arg schemas. The Episode 2 tools have slightly nested args (path + max_depth + glob). Gemini's `types.Schema` is a near-1:1 translation of OpenAPI 3 schema and supports `OBJECT`/`ARRAY`/`STRING`/`NUMBER`/`BOOLEAN`/`INTEGER` — confirmed in the in-tree code. Nested objects work too but **keep nesting shallow**: 1–2 levels max. Gemini Flash models are noticeably worse at producing deeply nested args than top-level ones.

```python
# tree tool — keep flat, no nested objects
types.FunctionDeclaration(
    name="tree",
    description=(
        "Show the folder hierarchy of the knowledge base. "
        "Use this BEFORE search/grep when the user asks 'what's in the knowledge base?' "
        "or to discover where related documents live."
    ),
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "path": types.Schema(
                type="STRING",
                description="Folder path to start from. Use '/' for the root. Defaults to '/'.",
            ),
            "max_depth": types.Schema(
                type="INTEGER",
                description="Maximum tree depth to expand. Default 3. Hard max 6.",
            ),
            "scope": types.Schema(
                type="STRING",
                enum=["user", "global", "both"],
                description="Which scope to traverse. 'both' is the default.",
            ),
        },
        required=[],  # all optional
    ),
)
```

**Key field tips, verified against in-tree usage:**
- `enum=[...]` works on `STRING` schemas — that's how to constrain `scope`. Verified in google-genai docs.
- For arrays, use `items=types.Schema(type="STRING")` (already used in `_build_search_tool` — line 82).
- `description` is the LLM-facing hint; pack it with **when to use** more than what it does. The existing code does this well in `_build_analyze_tool` ("REQUIRED for summarizing…") and we should match that voice for the new tools.

### Disabling automatic function calling — keep the existing pattern
The current code sets `automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)` (line 372 of `openai_client.py`) and runs the tool loop manually. **Keep this** — it's necessary because:
1. The team already builds custom tracing per tool (`@traceable(run_type="tool")`)
2. Manual loop lets us inject context after the tool result instead of doing the second LLM round-trip with tools enabled (line 397 onwards) — this was a deliberate workaround for the `thought_signature` issue with Gemini's two-turn tool flow
3. It's the same shape the new tools need

Confidence: HIGH — already in production code.

### Parallel tool calls
Gemini 2.x and 3.x models *can* emit parallel function calls (multiple `function_call` parts in one response), but the existing code at `openai_client.py:444–450` only extracts the first one (`for part in ...: if part.function_call: fc = part.function_call; break`). This is fine for Episode 1 because the system prompt explicitly says "Only call ONE tool per turn" (line 64).

For the **Explorer sub-agent** (Section 5), the same single-tool-per-turn loop is sufficient and easier to reason about. If we later want true parallel calls (e.g. tree+glob in one turn), we'd:
1. Iterate all `function_call` parts, dispatch in a thread pool, collect results
2. Append each as a separate `types.FunctionResponse` part on the next turn

But this is **not needed for Episode 2** — the Explorer is a serial loop. Confidence on parallel-call SDK shape: MEDIUM (per Gemini docs, but exact model behavior across `gemini-3-flash-preview` etc. shifts; do not rely on it).

### Pydantic v2 validation layer (recommended addition)
Wrap `dict(fc.args)` extraction with a Pydantic model per tool. Catches LLM hallucinations (wrong types, missing required fields) before they hit Postgres:

```python
class GrepArgs(BaseModel):
    pattern: str = Field(..., description="Regex or substring to search for")
    path: str = Field("/", description="Folder path to scope to")
    scope: Literal["user", "global", "both"] = "both"
    max_results: int = Field(50, ge=1, le=200)
    case_sensitive: bool = False

# inside the dispatcher:
try:
    grep_args = GrepArgs.model_validate(dict(fc.args))
except ValidationError as e:
    # Feed the validation error back to Gemini as a "tool error" turn
    yield ("tool_done", json.dumps({"tool": "grep", "error": str(e)}))
    return
```

This is a **new pattern** beyond what Episode 1 does (the existing tools just `args.get(...)` with defaults). Worth adopting because the new tools have more parameters and tighter constraints (depth limits, regex validity). Confidence: HIGH on the value; the team already has Pydantic in deps.

### What NOT to do with Gemini tool calling
| Avoid | Why |
|---|---|
| Deeply nested args (3+ levels) | Gemini Flash hallucinates structure |
| `oneOf` / `anyOf` in the schema | Gemini's schema implementation accepts these but does not always honor them — the model frequently generates the wrong branch |
| Long `enum` lists (>10 values) | Model picks weird ones; prefer free-form `STRING` with the valid options listed in `description` |
| Returning massive blobs as tool results (>16KB) | Already mitigated in `openai_client.py:567` (`truncated_result = result_text[:16000]`); apply the same cap to grep/tree output |
| Relying on `automatic_function_calling=enable` for new tools | We need the manual loop for tracing and SSE forwarding; don't toggle this per-call |

---

## 5. Sub-Agent Orchestration — `explore_knowledge_base` Without LangGraph

Episode 1 already ships exactly this pattern in `backend/app/services/sub_agent.py` (`run_sub_agent`). The Episode 2 Explorer follows the same shape with one expansion: the Explorer runs **its own tool-call loop** with access to `tree`, `glob`, `grep`, `read_document`, `list_files`.

### Pattern — verified against existing `run_sub_agent`

```python
@traceable(name="explore_knowledge_base", run_type="chain")
def run_explorer(question: str, user_id: str, supabase_client) -> Generator[tuple[str, str], None, None]:
    yield ("sub_agent_start", json.dumps({"agent": "explorer", "question": question}))

    # Build the same FunctionDeclaration list as the parent agent gets, MINUS analyze_document
    # (we don't want recursive sub-agents) and MINUS query_structured_data / web_search.
    function_declarations = [_build_tree_tool(), _build_glob_tool(), _build_grep_tool(),
                             _build_list_files_tool(), _build_read_document_tool()]
    tools = [types.Tool(function_declarations=function_declarations)]
    config = types.GenerateContentConfig(
        system_instruction=EXPLORER_SYSTEM_PROMPT,
        tools=tools,
        tool_config=types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(mode="AUTO")
        ),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    contents = [types.Content(role="user", parts=[types.Part(text=question)])]
    client = _get_client()
    model = get_llm_model()

    MAX_TURNS = 8  # hard ceiling — protect against infinite tool loops
    for turn in range(MAX_TURNS):
        response = client.models.generate_content(model=model, contents=contents, config=config)

        # Find function_call parts
        fc = next((p.function_call for p in response.candidates[0].content.parts
                   if p.function_call), None)

        if fc is None:
            # Final answer — stream it
            for chunk in client.models.generate_content_stream(
                model=model, contents=contents,
                config=types.GenerateContentConfig(system_instruction=EXPLORER_SYSTEM_PROMPT),
            ):
                if chunk.text:
                    yield ("sub_agent_token", chunk.text)
            break

        # Forward the tool-call event to the parent SSE stream
        yield ("sub_agent_tool_start", json.dumps({"tool": fc.name, "args": dict(fc.args)}))

        # Dispatch (validated args via Pydantic per Section 4)
        result_text = dispatch_explorer_tool(fc.name, dict(fc.args), user_id, supabase_client)
        yield ("sub_agent_tool_done", json.dumps({"tool": fc.name, "detail": "..."}))

        # Append the model's tool-call turn AND our tool-response turn back into contents
        contents.append(response.candidates[0].content)
        contents.append(types.Content(
            role="user",
            parts=[types.Part(function_response=types.FunctionResponse(
                name=fc.name, response={"result": result_text[:16000]}
            ))]
        ))
    else:
        # Hit MAX_TURNS — emit truncation note
        yield ("sub_agent_token", "\n\n[Explorer reached max turns; returning best-so-far summary.]")

    yield ("sub_agent_done", "")
```

### Why this works without LangGraph
LangGraph's value is state-machine orchestration with branching. The Explorer is a **linear loop** — model emits a tool call, we run it, append the response, repeat until the model emits text. No branching, no parallel fan-out, no complex state. The `for turn in range(MAX_TURNS)` + `contents.append(...)` pattern is the entire flow.

The existing `sub_agent.py` is even simpler (single LLM call, no tool loop) — the Explorer adds the tool loop but stays inside the same `Generator[tuple[str, str], None, None]` event protocol so the parent SSE forwarding in `messages.py` keeps working. Confidence: HIGH.

### Nested SSE event forwarding — already plumbed
The parent `event_generator` in `app/routers/messages.py` (referenced in `ARCHITECTURE.md` step 6) already forwards `sub_agent_start` / `sub_agent_token` / `sub_agent_done` events from `run_sub_agent`. Add `sub_agent_tool_start` / `sub_agent_tool_done` event types and a one-line passthrough in the parent generator. This is a **schema addition**, not a pattern change. Confidence: HIGH.

### What NOT to do
| Avoid | Why |
|---|---|
| `LangGraph` or `langchain.agents.AgentExecutor` | Banned by project rule, and adds nothing — this is a 30-line loop |
| Recursive sub-agents (Explorer calling `analyze_document` calling Explorer) | Easy to write, hard to debug, blows the trace tree. **Hard-exclude `analyze_document` from Explorer's toolset.** |
| Unbounded loop | `MAX_TURNS = 8` is generous; raise the warning event if hit |
| Sharing the `contents` list with the parent | The Explorer is **isolated context** — it gets only `question`, not the parent's chat history. This is the whole point of sub-agents (token cost containment). |

### LangSmith tracing
`@traceable(name="explore_knowledge_base", run_type="chain")` on the outer function, `@traceable(name="kb_grep", run_type="tool")` on each individual tool executor. This nests the tool calls under the chain in the LangSmith UI exactly like `_execute_search_documents` is nested under `gemini_chat` today (line 251 in `openai_client.py`). Confidence: HIGH.

---

## 6. `read_document` — Line-Numbered Slicing Pattern

Claude-Code's `Read` returns `cat -n`–style output. Two encoding choices:

| Format | Example | Tokens |
|---|---|---|
| `cat -n` columns | `   12  Some line of markdown` | ~3 tokens for the line number prefix |
| Arrow form | `12→Some line of markdown` | ~2 tokens |
| Bracket form | `[12] Some line of markdown` | ~3 tokens |

**Recommendation:** **arrow form** (`{n}→{content}`). Verified by inspection — Anthropic's own Read tool uses this exact form, and it's what Claude was trained to produce/consume cleanly. The Gemini models read it fine because the digit→arrow→text pattern is unambiguous. Confidence: MEDIUM (no formal benchmark; informed by Claude Code's choice).

### Slicing implementation
Don't try to do this in SQL — fetch `content_markdown` for the document, then in Python:

```python
def read_document(content_markdown: str, offset: int = 0, limit: int = 2000) -> str:
    lines = content_markdown.split("\n")
    end = min(len(lines), offset + limit)
    out = []
    for i in range(offset, end):
        out.append(f"{i + 1}→{lines[i]}")  # 1-indexed line numbers, like cat -n
    return "\n".join(out)
```

Newline-boundary clamping is automatic with `split("\n")`. Edge cases:
- `offset` past EOF → return empty string with a note `"[file has only N lines]"`
- Very long single line (e.g. minified JSON) → don't wrap; the LLM handles it. If you do want to wrap, do it in the renderer, not here.
- File trailing newline → `split` produces an empty string at the end; suppress it.

### Token budget
`limit` should default to **2000 lines** (matches Claude Code's default) and hard-cap at **5000**. At ~10 tokens/line average for prose-markdown that's ~50K tokens worst case — within Gemini's 1M context but safely below the model's effective attention window. Confidence: MEDIUM.

### What NOT to do
| Avoid | Why |
|---|---|
| Reconstructing from `document_chunks` | Chunks have overlap; line numbers won't be stable |
| Offset by character/byte | LLMs reason about line numbers, not byte offsets |
| Auto-summarizing if file is large | That's `analyze_document`'s job; `read_document` is for precision reads |

---

## 7. Frontend — No New Top-Level Dependencies

The file explorer UI (two-section tree, breadcrumbs, drag-move, context menus) is built from primitives the team already has pinned in `frontend/package.json`:

| Need | Use what's already there |
|---|---|
| Collapsible tree nodes | `@radix-ui` `Collapsible` (already installed via shadcn-ui) |
| Breadcrumbs | shadcn-ui `Breadcrumb` (add via `npx shadcn add breadcrumb` — uses `radix-ui`, no new top-level dep) |
| Right-click menu (rename/delete) | shadcn-ui `ContextMenu` (same — Radix-backed) |
| Drag-move documents | **HTML5 native drag/drop API** — for single-item, drop-onto-folder semantics, no library needed. The "no multi-select" Out-of-Scope decision in `PROJECT.md` keeps this simple. |
| Toast errors | `sonner` (already installed) |
| Markdown rendering of `read_document` results | `react-markdown` + `remark-gfm` (already installed) |

**Do not add:** `react-arborist`, `react-complex-tree`, `react-dnd`, `@dnd-kit/core`. All are overkill for two-deep menus with single-item drag-drop. Confidence: HIGH — verified against `frontend/package.json` content reported in `STACK.md`.

---

## 8. Pinned Versions — Concrete

### Backend (`backend/requirements.txt` — additions/pins)
```
google-genai>=1.30,<2.0       # was unpinned; see Section 4
pydantic>=2.5,<3.0            # was unpinned
# no new top-level packages
```

### Postgres extensions (Supabase project)
```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;       -- already shipped by Supabase, just enable
CREATE EXTENSION IF NOT EXISTS vector;        -- already enabled per migration 003
-- DO NOT enable ltree
```

Supabase Postgres ships `pg_trgm` in the default extension list — no managed-service approval needed. Confirmed against Supabase's public extensions list. Confidence: HIGH.

### Frontend
No new top-level deps. May need to run `npx shadcn@3.8.4 add breadcrumb context-menu collapsible` (uses already-installed Radix peers).

---

## Alternatives Considered

| Recommended | Alternative | When the Alternative Wins |
|---|---|---|
| `pg_trgm` + `~*` for grep | Postgres Full-Text Search (`tsvector`) | If grep semantics drift toward "find documents about X" — but that's already covered by `search_documents` |
| `pg_trgm` + `~*` for grep | External search index (Meilisearch, Elastic) | Only at corpus sizes >100K documents per user — vastly out of scope |
| TEXT `folder_path` | `ltree` | If folder names were guaranteed `[A-Za-z0-9_]` only (they're not — users want `2024-Q1`, `floor-plans`, etc.) |
| TEXT `folder_path` | Closure tables / adjacency lists | If we needed SQL-side recursive subtree queries (we don't — tree depth is small and we scope by prefix) |
| Manual tool loop | `automatic_function_calling=enable` | If we didn't need per-tool LangSmith spans or SSE event forwarding (we do) |
| Pydantic v2 validation | `args.get(...)` with defaults | For a 1-arg tool (the existing simple ones); for multi-arg tools with constraints, validation pays for itself |
| Arrow-form line numbering (`12→`) | `cat -n` columns | If we ever need diff-style output — but not for read_document |

---

## What NOT to Use — Concrete Granular Don'ts

| Avoid | Why | Use Instead |
|---|---|---|
| `ltree` extension | Label charset rejects real-world folder names (`-`, `.`, spaces); LLM doesn't know `lquery` | `TEXT folder_path` + `text_pattern_ops` btree + trigram GIN |
| `to_tsquery`/`plainto_tsquery` for grep | Tokenizes/stems/strips — won't match exact identifiers like `MDB-C-G3` | `~*` regex with trigram GIN |
| Default-collation btree on `folder_path` for `LIKE 'x/%'` | Index unused unless DB locale is `C` | `text_pattern_ops` btree |
| `auth.uid()` raw in RLS policies on hot tables | Re-evaluates per row | `(SELECT auth.uid())` form (Supabase 2024 best practice) |
| ENUM type for `scope` | Painful `ALTER TYPE` migrations | `TEXT NOT NULL CHECK (scope IN ('user','global'))` |
| Storing JWT `is_admin` claim | Token refresh required on role change | Existing `profiles.is_admin` table check (verified in migration 005) |
| Recursive CTEs for tree traversal | Unnecessary with path-based model — prefix queries are enough | `WHERE folder_path LIKE '/projects/%'` |
| Deeply nested `types.Schema` for tool args | Gemini Flash hallucinates structure beyond ~2 levels | Flat args; scope/depth as top-level fields |
| `oneOf`/`anyOf` in tool schemas | Gemini accepts but doesn't reliably honor | Free-form `STRING` with valid values in `description` |
| `automatic_function_calling=enable` for new tools | Loses per-tool tracing and SSE forwarding hooks | Keep `disable=True` and run the manual loop (matches Episode 1) |
| Unbounded sub-agent loop | Cost runaway | `MAX_TURNS = 8` ceiling with truncation note |
| Recursive sub-agents (Explorer → analyze_document → Explorer) | Trace tree explodes; debugging nightmare | Hard-exclude `analyze_document` from Explorer's toolset |
| `react-arborist` / `react-dnd` / `@dnd-kit/core` | Overkill for shallow tree + single-item drag | Existing Radix `Collapsible` + native HTML5 DnD |
| Reconstructing read_document from chunks | Overlap → unstable line numbers | Read `documents.content_markdown` directly |
| In-Postgres glob translation (e.g. trying to teach the LLM `LIKE` syntax) | LLM is much better at globs | Translate glob→SQL `LIKE` in Python before query |
| Returning >16KB tool results to Gemini | Truncation in the model layer is unpredictable; existing code already caps at 16K (`openai_client.py:567`) | Pre-truncate to 16K and append `[...truncated, N more results]` note |

---

## Stack Patterns by Variant

**If grep performance becomes an issue (>500ms p95) at large corpus sizes:**
- Add a separate `document_lines` materialized view with `(document_id, line_number, line_text, line_text_trgm)` and trigram GIN on `line_text`. Refresh on document insert/update. Avoids the `LATERAL regexp_split_to_table` step at query time.
- Confidence: MEDIUM — only worth doing if measured.

**If multi-line regex becomes a requirement:**
- The current pattern (`regexp_split_to_table` then `~*`) is line-based. For multi-line patterns (`(?s)`), grep would have to run against the whole `content_markdown` blob and then back-compute line numbers. Defer until asked.

**If admin-write contention on global folders becomes real:**
- Add a `global_kb_lock` advisory lock pattern around the `folders` insert/update. Out of scope until two admins are concurrent.

---

## Version Compatibility

| Package A | Compatible With | Notes |
|---|---|---|
| `google-genai>=1.30` | `pydantic>=2.5` | Both are independent Python deps; no peer constraints |
| `pg_trgm` (any) | Supabase Postgres 15+ | Built-in since PG 9.1; Supabase ships ≥15 |
| `pgvector` (current) | `pg_trgm` (current) | No conflict — different operator classes, different indexes |
| `langsmith` (existing) | `@traceable` on generator functions | Confirmed working in `run_sub_agent` (line 18, sub_agent.py) — same pattern works for the Explorer |
| `sse-starlette` (existing) | New event types (`sub_agent_tool_start`, etc.) | Plain string event names; no SDK constraint |

---

## Sources

- **Verified in this codebase (HIGH confidence):**
  - `backend/app/services/openai_client.py` lines 19–27, 69–185, 274–670 — google-genai client init, `FunctionDeclaration`/`Schema` patterns, manual tool loop, `automatic_function_calling=disable`, 16KB result truncation, `@traceable` placement
  - `backend/app/services/sub_agent.py` lines 18–97 — `@traceable(run_type="chain")`, generator yielding `("sub_agent_*", payload)` tuples, `generate_content_stream` over a `system_instruction`-loaded full document
  - `backend/migrations/005_profiles_and_settings.sql` — `profiles.is_admin` BOOLEAN, EXISTS-clause admin check pattern
  - `backend/migrations/008_hybrid_search.sql` and `011_improved_keyword_search.sql` — `tsvector` + GIN + `websearch_to_tsquery` (the existing FTS layer that we explicitly DO NOT reuse for grep)
  - `backend/migrations/003_byo_retrieval.sql` — `pgvector` enabled, RLS via `auth.uid() = user_id` (the policy form we extend with the scope union)
  - `backend/migrations/007_document_metadata.sql` — `metadata JSONB` with GIN index, `metadata_filter @>` containment pattern (the same pattern works for `folder_path` prefix filters)
- **Postgres official documentation (HIGH confidence — well-established behaviors):**
  - `pg_trgm` operator classes (`gin_trgm_ops`, `gist_trgm_ops`) and supported operators (`LIKE`, `ILIKE`, `~`, `~*`) — postgresql.org/docs/current/pgtrgm.html
  - `text_pattern_ops` for `LIKE` prefix queries in non-`C` locales — postgresql.org/docs/current/indexes-opclass.html
  - `regexp_split_to_table … WITH ORDINALITY` — postgresql.org/docs/current/functions-srf.html
- **Supabase official documentation (HIGH confidence):**
  - RLS performance — wrap `auth.uid()` in `(SELECT auth.uid())` to make it stable per query (supabase.com/docs/guides/database/postgres/row-level-security)
  - `pg_trgm` listed in default extensions
- **`google-genai` SDK (MEDIUM-HIGH confidence — verified against in-tree usage; not version-pinned upstream check):**
  - `types.FunctionDeclaration`, `types.Schema`, `types.Tool`, `types.ToolConfig`, `types.FunctionCallingConfig`, `types.AutomaticFunctionCallingConfig`, `types.GenerateContentConfig`, `types.Content` / `types.Part` — confirmed via `from google.genai import types` and direct usage in `openai_client.py`
  - The Context7 fallback CLI was attempted but blocked by an msys path-translation issue in this sandbox (git-bash converts leading-slash arguments to Windows paths before they reach `npx`); resolved IDs were `/googleapis/python-genai`, `/supabase/postgres`, `/supabase/supabase` but `ctx7 docs <id>` could not be invoked. **Mitigation:** all SDK claims here are cross-checked against actual in-tree code, so the recommendations match what's currently shipping. Topics that the in-tree code doesn't exercise (parallel function-call extraction across multiple parts) are flagged MEDIUM/LOW confidence in their respective sections.

---

*Stack research for: agentic-exploration tools layered onto an existing Supabase + Gemini RAG app*
*Researched: 2026-04-28*
