# Architecture Research

**Domain:** Agentic-exploration tools layered on an existing FastAPI + Supabase + Gemini RAG app
**Researched:** 2026-04-28
**Confidence:** HIGH (grounded in the actual Episode 1 codebase: `app/services/sub_agent.py`, `app/routers/messages.py`, `backend/migrations/003_byo_retrieval.sql`, `005_profiles_and_settings.sql`, `010_sub_agents.sql`)

> Scope reminder: this document is **not** a generic ecosystem survey. The Episode 1 stack and module layout are locked. Every recommendation below names a specific file or table that already exists or that must be created. Read this alongside `.planning/codebase/ARCHITECTURE.md` and `.planning/PROJECT.md`.

## System Overview — Episode 2 Additions

```
┌────────────────────────────────────────────────────────────────────────┐
│                       Frontend (React + Vite)                          │
│                                                                        │
│  Chat.tsx                                                              │
│    ├── ThreadSidebar.tsx          (unchanged)                          │
│    ├── MessageList.tsx            (extended: nested SubAgentSection    │
│    │                               now renders Explorer traces too)    │
│    ├── MetadataFilterBar.tsx      (unchanged — folder is NOT here)     │
│    └── FileExplorerPanel.tsx      [NEW — replaces FileUploadPanel]     │
│         ├── FolderTree.tsx        [NEW]                                │
│         ├── FolderNode.tsx        [NEW]   (expand/rename/delete/move)  │
│         ├── DocumentNode.tsx      [NEW]   (rename/move/delete)         │
│         ├── Breadcrumbs.tsx       [NEW]                                │
│         ├── UploadIntoFolder.tsx  [NEW]   (dropzone scoped to path)    │
│         └── ScopeSwitcher.tsx     [NEW]   ("Shared" / "My Files" tabs) │
└────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────────┐
│                  FastAPI Routers   (app/routers/)                      │
│  threads.py  messages.py  files.py  settings.py    [unchanged shells]  │
│  folders.py                                          [NEW]             │
│    GET  /api/folders?scope={user|global|both}                          │
│    POST /api/folders          (create — admin gate if scope=global)    │
│    PATCH /api/folders/{id}    (rename/move)                            │
│    DELETE /api/folders/{id}                                            │
│  files.py extended:                                                    │
│    POST /api/files/upload?folder_path=/x/y&scope=user|global           │
│    PATCH /api/files/{id}      (rename, move folder_path)               │
└────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────────┐
│              Backend Service Layer (app/services/)                     │
│                                                                        │
│  openai_client.py        (extended — registers 5 new tools + Explorer  │
│                            sub-agent in dynamic tool list, dispatches  │
│                            them in the same Call#1/Call#2 pattern)     │
│  sub_agent.py            (extended — exposes run_explorer_sub_agent    │
│                            alongside existing run_sub_agent;           │
│                            shared SSE event vocabulary)                │
│  ingestion.py            (extended — now writes documents.content_     │
│                            markdown alongside chunks; respects         │
│                            folder_path + scope on insert)              │
│  reranker.py             (unchanged)                                   │
│  metadata.py             (unchanged)                                   │
│  record_manager.py       (extended — dedup key now includes scope +    │
│                            folder_path; same name in different folder  │
│                            is NOT a duplicate)                         │
│  sql_tool.py             (unchanged)                                   │
│  web_search.py           (unchanged)                                   │
│                                                                        │
│  folder_service.py       [NEW]                                         │
│    - normalize_path()             ('/', '/a/b/', strip trailing slash) │
│    - list_folder(path, scope, user_id, supabase)                       │
│    - create_folder(path, scope, user_id)                               │
│    - move_document(doc_id, new_path, user_id)                          │
│    - rename_folder(old_path, new_path, scope)                          │
│    - delete_folder(path, scope, cascade)                               │
│                                                                        │
│  exploration_tools.py    [NEW]                                         │
│    - tree(path, max_depth, scope, user_id, supabase)                   │
│    - glob_match(pattern, scope, user_id, supabase)                     │
│    - grep(pattern, path_scope, scope, user_id, supabase)               │
│    - list_files(path, scope, user_id, supabase)                        │
│    - read_document(doc_id_or_path, offset, limit, user_id, supabase)   │
│    - All return Pydantic dataclasses → JSON-serialized for tool result │
│    - Hard token-budget caps with truncation note appended              │
└────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────────┐
│              Supabase (Postgres + pgvector)                            │
│                                                                        │
│  documents       (extended)                                            │
│    + folder_path     TEXT NOT NULL DEFAULT '/'                         │
│    + scope           TEXT NOT NULL DEFAULT 'user'                      │
│                       CHECK (scope IN ('user','global'))               │
│    + content_markdown TEXT                                             │
│    user_id NULLABLE for scope='global' rows                            │
│    NEW unique index: (scope, COALESCE(user_id,'00..0'),                │
│                       folder_path, file_name) for dedup                │
│                                                                        │
│  document_chunks (extended)                                            │
│    + scope           TEXT NOT NULL DEFAULT 'user'                      │
│    user_id NULLABLE for scope='global' rows                            │
│                                                                        │
│  folders         [NEW — thin table for empty/named folders]            │
│    id UUID PK, scope TEXT, user_id UUID NULLABLE,                      │
│    path TEXT NOT NULL,        -- normalized, e.g. '/projects/2026/'    │
│    name TEXT NOT NULL,        -- leaf name, e.g. '2026'                │
│    created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ                      │
│    UNIQUE (scope, COALESCE(user_id,'00..0'), path)                     │
│                                                                        │
│  RPCs                                                                  │
│    match_document_chunks_hybrid          (extended w/ folder_path,     │
│                                           scope filters)               │
│    match_document_chunks_with_filters    (extended likewise)           │
│    grep_documents(pattern, path_prefix, scope, user_id) [NEW, optional │
│                   — can also do this in Python over a SELECT]          │
└────────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Implementation |
|-----------|----------------|----------------|
| `folder_service.py` | Path normalization, folder CRUD, move/rename, two-scope-aware reads | New service; pure SQL via supabase-py; no LLM calls |
| `exploration_tools.py` | The 5 new tools (`tree`, `glob`, `grep`, `list_files`, `read_document`); token-budget enforcement | New service; called from `openai_client.py`'s dispatch loop |
| `folders.py` router | HTTP CRUD on `folders` table; admin gate for `scope='global'` writes | New router, mirrors `files.py` shape |
| `files.py` (extended) | Accept `folder_path`, `scope`; rename/move via PATCH | Add fields to existing endpoints |
| `ingestion.py` (extended) | Persist `content_markdown` (the Docling markdown export, pre-chunking); propagate `folder_path` + `scope` to chunks | Single new column write; chunker already produces the markdown — currently thrown away after chunking |
| `sub_agent.py` (extended) | Add `run_explorer_sub_agent()` that runs an isolated Gemini loop with **only** the 5 exploration tools, streams trace events, returns a compact summary | Mirrors `run_sub_agent` shape but with multi-turn tool dispatch inside; reuses SSE event vocabulary |
| `openai_client.py` (extended) | Register new tools in `_build_tools()`; add dispatch arms in the tool loop; extend `search_documents` schema with optional `folder_path` + `scope` | Same Call#1 (non-streaming, detect tool) / Call#2 (streaming, with context) pattern as today |
| `FileExplorerPanel.tsx` | Replaces flat `FileUploadPanel`; renders two-section tree (Shared / My Files); folder expand/collapse, breadcrumbs, drag-move, rename, upload-into-folder | New component cluster; keeps existing polling cadence for ingestion status |
| `MessageList.tsx` (extended) | Reuse `SubAgentSection` to render Explorer's nested tool calls (tree/glob/grep/read events); add icons per tool type | Extend the existing nested SSE rendering — no new event-type plumbing needed at the outer layer |

## Recommended Project Structure

```
backend/app/
├── routers/
│   ├── folders.py           [NEW] folder CRUD
│   ├── files.py             [edit] folder_path/scope on upload + PATCH
│   └── messages.py          [edit] forward new SSE event types (see below)
├── services/
│   ├── folder_service.py    [NEW] path ops, list, move, rename
│   ├── exploration_tools.py [NEW] tree/glob/grep/list_files/read_document
│   ├── sub_agent.py         [edit] add run_explorer_sub_agent
│   ├── ingestion.py         [edit] write content_markdown, accept folder/scope
│   ├── record_manager.py    [edit] dedup key includes (scope, folder_path)
│   └── openai_client.py     [edit] tool registry + dispatch arms
└── models/
    └── schemas.py           [edit] FolderResponse, FolderCreateRequest,
                                    DocumentResponse += folder_path, scope,
                                    ToolResultModels for exploration tools

backend/migrations/
├── 012_folder_path_and_scope.sql   [NEW] add columns, defaults, backfill='/'
├── 013_content_markdown.sql        [NEW] add column + backfill job hook
├── 014_folders_table.sql           [NEW] folders table + RLS policies
├── 015_two_scope_rls.sql           [NEW] update doc/chunk RLS for scope
└── 016_search_filters_with_path.sql [NEW] update RPCs to accept path/scope

frontend/src/
├── components/
│   ├── FileExplorerPanel.tsx       [NEW] root of the explorer
│   ├── FolderTree.tsx              [NEW] recursive renderer
│   ├── FolderNode.tsx              [NEW]
│   ├── DocumentNode.tsx            [NEW]
│   ├── Breadcrumbs.tsx             [NEW]
│   ├── ScopeSwitcher.tsx           [NEW] Shared | My Files tabs/sections
│   ├── UploadIntoFolder.tsx        [NEW] dropzone bound to current path
│   └── MessageList.tsx             [edit] render Explorer trace blocks
└── lib/
    └── api.ts                      [edit] foldersApi, document.move/rename,
                                            new SSE event types
```

### Structure Rationale

- **`exploration_tools.py` separated from `openai_client.py`:** keeps the dispatcher slim and lets each tool be unit-tested in isolation against a fixture supabase. The dispatcher imports tool functions and registers their schemas; that's it. Mirrors how `web_search.py` and `sql_tool.py` are organized today.
- **`folder_service.py` separate from `exploration_tools.py`:** folder mutations (create/move/rename/delete) come from the **HTTP** path (the explorer UI). Exploration tools come from the **LLM tool dispatch** path. Different callers, different validation rules, different RLS contexts (mutations need admin gate for global; reads do not). Co-locating both in one file blurs that boundary.
- **`sub_agent.py` extended, not split:** the Explorer sub-agent reuses the streaming-Gemini-with-isolated-context pattern that already exists. Adding `run_explorer_sub_agent` next to `run_sub_agent` keeps the two siblings discoverable and lets them share helpers (e.g. `MAX_CONTEXT_CHARS`).
- **One migration per concern:** four small migrations are easier to review and roll back than one mega-migration. The order encodes the dependency chain — schema columns must exist before RPCs can reference them.
- **Frontend explorer cluster lives under `components/`, not `pages/`:** it's a panel inside `Chat.tsx`, not a route of its own. Matches the placement of `FileUploadPanel` and `MetadataFilterBar` today.

## Architectural Patterns

### Pattern 1: Two-Scope RLS via Union Predicate

**What:** Every read policy on `documents`, `document_chunks`, `folders` allows rows where `scope = 'global'` OR `(scope = 'user' AND user_id = auth.uid())`. Writes are split: a `'user'`-scoped write requires `user_id = auth.uid()`; a `'global'`-scoped write requires the caller to be an admin (the same `is_admin = true` check used by `global_settings`).

**When to use:** Whenever a single table needs to mix tenant-private rows with shared rows readable by all authenticated users.

**Trade-offs:**
- Pro: One table, one query — no UNIONs at the application layer; LLM tools see a unified view automatically.
- Pro: Reuses the existing `is_admin` admin role; no new role concept.
- Con: `user_id` must be `NULLABLE` for `scope='global'` rows, which complicates dedup uniqueness keys (use `COALESCE(user_id,'00000000-0000-0000-0000-000000000000')`).
- Con: A bug in the predicate leaks all global data — covered by an integration test that asserts user A cannot see user B's `'user'` rows but **can** see all `'global'` rows.

**Example sketch:**
```sql
-- documents: SELECT
DROP POLICY IF EXISTS "Users can view own documents" ON documents;
CREATE POLICY "Read user-scoped or global documents"
  ON documents FOR SELECT
  USING (
    scope = 'global'
    OR (scope = 'user' AND user_id = auth.uid())
  );

-- documents: INSERT
CREATE POLICY "Insert own user-scoped documents"
  ON documents FOR INSERT
  WITH CHECK (scope = 'user' AND user_id = auth.uid());

CREATE POLICY "Admins insert global documents"
  ON documents FOR INSERT
  WITH CHECK (
    scope = 'global'
    AND EXISTS (
      SELECT 1 FROM public.profiles
      WHERE id = auth.uid() AND is_admin = true
    )
  );

-- documents: UPDATE / DELETE — same split (user-self for 'user', admin for 'global')
```

Apply the same shape to `document_chunks` and `folders`. The `is_admin` subquery is exactly the predicate already used by `global_settings` (migration 005), so it's a known-working pattern.

### Pattern 2: Path-as-String + Thin `folders` Side Table

**What:** Documents carry their location as `folder_path TEXT` (e.g. `/projects/2026/floor-plans/`, always normalized with leading + trailing slash). The `folders` table only exists to remember **empty** folders and to give folders explicit existence (so an admin can create `/templates/` before any doc lives there). Folder listing for a path = `SELECT … FROM documents WHERE folder_path = $path` UNION `SELECT … FROM folders WHERE parent_path = $path`.

**When to use:** Hierarchical organization where most queries are "list under prefix" or "exact-folder match", and you want to avoid recursive CTEs.

**Trade-offs:**
- Pro: `WHERE folder_path LIKE '/projects/%'` answers tree queries with a single `text_pattern_ops` index — no recursion.
- Pro: Moves are cheap when documents move (one row update); folder rename is a `LIKE` mass-update plus a `folders` row update.
- Con: Folder rename touches every descendant document — fine at our scale (per-user document counts in low thousands), risky at millions.
- Con: No referential integrity between `documents.folder_path` and `folders.path`; have to enforce in app code or trigger.

**Example sketch:**
```sql
CREATE INDEX documents_folder_path_idx
  ON documents (scope, COALESCE(user_id, '00000000-0000-0000-0000-000000000000'),
                folder_path text_pattern_ops);

-- list immediate children of '/projects/'
SELECT id, file_name, folder_path FROM documents
WHERE folder_path = '/projects/'
UNION ALL
SELECT id, NULL, path FROM folders
WHERE parent_path = '/projects/';
```

`folder_service.normalize_path()` is the single chokepoint for trailing-slash policy — every router and tool calls it.

### Pattern 3: Explorer Sub-Agent — Isolated Multi-Tool Loop with SSE Pass-Through

**What:** When the main agent calls `explore_knowledge_base(question)`, the dispatcher invokes `run_explorer_sub_agent()` instead of returning chunk text. That function spins up a fresh Gemini context whose tools are **only** `tree`, `glob`, `grep`, `list_files`, `read_document`. It runs a multi-turn loop (LLM → tool → LLM → tool → … → final summary), streaming every token and tool event back through the same SSE side-channel that `analyze_document` already uses.

**When to use:** Open-ended exploratory questions where the main agent doesn't know the right entry point — Claude-Code-style "go figure out what's in this codebase" delegation.

**Trade-offs:**
- Pro: The main agent's context stays clean — only the compact summary returns, not the raw tree/grep dumps.
- Pro: The Explorer can iterate (grep finds 3 candidates → reads each → narrows to one) which a single tool call can't.
- Con: Token spend can balloon inside the sub-agent; need a hard turn cap (e.g. 8 turns) and a per-call token-budget cap.
- Con: User-visible latency before any token streams to the main thread — surface the sub-agent trace eagerly via SSE so the user sees activity.

**Example shape:**
```python
@traceable(name="explorer_sub_agent", run_type="chain")
def run_explorer_sub_agent(
    question: str, user_id: str, supabase_client,
) -> Generator[tuple[str, str], None, None]:
    yield ("sub_agent_start", json.dumps({
        "agent": "explore_knowledge_base", "question": question
    }))
    tools = build_exploration_tools_schema()  # tree, glob, grep, list_files, read
    contents = [user_message(question)]
    summary_text = ""
    for turn in range(MAX_EXPLORER_TURNS):
        # non-streaming detection call
        resp = client.models.generate_content(model=model, contents=contents,
                                              config=types.GenerateContentConfig(
                                                  system_instruction=EXPLORER_SYS,
                                                  tools=tools))
        if has_tool_call(resp):
            tool_name, tool_args = extract_tool(resp)
            yield ("sub_agent_tool_start", json.dumps({"tool": tool_name, "args": tool_args}))
            result = dispatch_exploration_tool(tool_name, tool_args, user_id, supabase_client)
            yield ("sub_agent_tool_done", json.dumps({"tool": tool_name, "result_preview": result[:300]}))
            contents.append(model_message(resp))
            contents.append(tool_response(tool_name, result))
            continue
        # final answer — stream it
        for chunk in client.models.generate_content_stream(...):
            if chunk.text:
                summary_text += chunk.text
                yield ("sub_agent_token", chunk.text)
        break
    yield ("sub_agent_done", summary_text)
```

The router (`messages.py:event_generator`) needs two new event types passed through (`sub_agent_tool_start`, `sub_agent_tool_done`); existing `sub_agent_token` and `sub_agent_done` are reused as-is. The frontend's existing `SubAgentSection` already groups events between `_start` and `_done` — extending it to render tool sub-events is an additive change.

### Pattern 4: Tool Dispatch Registry — Additive Extension, Not Refactor

**What:** Today `openai_client.stream_response` has an `if/elif` chain over `tool_name` and a `_build_tools()` that lists tool schemas. Add five new arms and five new schemas. Resist the urge to refactor the chain into a registry-of-callables right now — it's only 9 entries, the existing pattern is reviewable, and a refactor blocks every other change in this milestone.

**When to use:** Always — when extending existing patterns, additive change minimizes regression surface.

**Trade-offs:**
- Pro: Each new tool is a localized diff; reviewer sees only the addition.
- Pro: Existing 5 tools (`search_documents`, `analyze_document`, `query_structured_data`, `web_search`, `analyze_document` again as sub-agent dispatch) keep working byte-for-byte.
- Con: The dispatcher grows to ~10 arms — at 15 we should consider a registry. Note it as future cleanup.

### Pattern 5: Token-Budget Truncation in Tools

**What:** Every tool in `exploration_tools.py` enforces an output-size cap and appends a truncation marker when exceeded. `tree` truncates at `max_depth` and shows `(N more items)`. `grep` caps at e.g. 50 hits with `… 23 more matches not shown`. `read_document` honors `offset` + `limit` and clamps to newline boundaries.

**When to use:** Every tool whose output is sent back into the LLM context. Always.

**Trade-offs:**
- Pro: Predictable token cost per turn.
- Pro: The LLM learns to drill down (refine grep, paginate read) rather than dump everything.
- Con: Choosing budget defaults requires tuning; start conservative (4–8k tokens of tool output) and revisit.

## Data Flow

### Primary Flow: Main Agent Calls a New Exploration Tool

```
User message
   │
   ▼
POST /api/threads/{tid}/messages              (messages.py)
   │
   ▼
stream_response()                             (openai_client.py)
   │
   ├── _build_tools(has_documents, has_global, scope_options)
   │     └── now includes: search_documents (extended), analyze_document,
   │         query_structured_data, web_search, tree, glob, grep,
   │         list_files, read_document, explore_knowledge_base
   │
   ├── Call #1 (non-streaming) → Gemini picks tool, e.g. grep(pattern="floor 3")
   │
   ├── dispatch_tool("grep", args)
   │     └── exploration_tools.grep(pattern, path_scope, scope, user_id, supabase)
   │           ├── SELECT id, file_name, folder_path, content_markdown
   │           │   FROM documents
   │           │   WHERE folder_path LIKE $prefix
   │           │     AND content_markdown ~* $pattern   ← Postgres regex
   │           │   (RLS enforces scope visibility automatically)
   │           ├── For each hit: extract surrounding lines (Python regex over
   │           │   content_markdown, with line numbers)
   │           └── Return JSON: [{document_id, file_name, folder_path,
   │                              line_numbers, snippets}]
   │
   ├── yield ("tool_done", grep_result_json)   → SSE to frontend
   │
   ├── Call #2 (streaming) with grep results injected as context
   │     └── Gemini composes the user-facing answer, citing file paths
   │
   └── yield tokens → SSE
```

RLS does the scope filtering for free — `exploration_tools.grep` does **not** add `WHERE scope IN (...)`, the policy does. Tools accept an optional `scope` arg (`'user'`, `'global'`, `'both'`) which becomes an **additional narrowing** filter on top of RLS, not a substitute.

### Secondary Flow: Explorer Sub-Agent Trace Forwarding

```
Main agent calls explore_knowledge_base(question="where are the 2026 floor plans?")
   │
   ▼
dispatch_tool("explore_knowledge_base", args)
   │
   └── for event in run_explorer_sub_agent(question, user_id, supabase):
         match event:
           ("sub_agent_start", payload)      → yield through to messages.py
           ("sub_agent_tool_start", payload) → yield through (NEW event type)
           ("sub_agent_tool_done",  payload) → yield through (NEW event type)
           ("sub_agent_token",      text)    → yield through
           ("sub_agent_done",       summary) → yield through, also append
                                                summary as the tool result
                                                to the main agent's context
   │
   ▼
messages.py event_generator forwards to client as SSE JSON:
   {"type": "sub_agent_start", "agent": "explore_knowledge_base", ...}
   {"type": "sub_agent_tool_start", "tool": "tree", "args": {...}}
   {"type": "sub_agent_tool_done", "tool": "tree", "result_preview": "..."}
   {"type": "sub_agent_token", "content": "Found three candidate..."}
   {"type": "sub_agent_done"}
   │
   ▼
MessageList.tsx renders nested SubAgentSection with per-tool sub-blocks
```

The compact summary (the `sub_agent_done` payload) is what's appended to the main agent's tool history — **not** the trace. The trace is for human eyes only via SSE.

### Tertiary Flow: Folder CRUD from the Explorer UI

```
User clicks "New Folder" → "/projects/2026/"
   │
   ▼
POST /api/folders {scope: "user", path: "/projects/2026/"}
   │
   ▼
folders.py: get_current_user → folder_service.create_folder
   │  (if scope=="global", swap dependency for get_admin_user)
   │
   ▼
INSERT INTO folders (scope, user_id, path, name, parent_path) VALUES (...)
RLS enforces: scope='user' INSERT must have user_id=auth.uid();
              scope='global' INSERT must have admin row in profiles.
   │
   ▼
Return FolderResponse → frontend updates FolderTree state
```

Folder mutations do **not** flow through SSE — they're plain REST, like thread creation today.

## Build Order

The dependency graph dictates a strict early sequence; later items can parallelize.

### Stage 1 — Schema foundation (must come first, blocks everything)

1. **Migration 012**: add `documents.folder_path TEXT NOT NULL DEFAULT '/'` and `documents.scope TEXT NOT NULL DEFAULT 'user'`. Backfill is automatic via `DEFAULT`. Same on `document_chunks`. Make `documents.user_id` and `document_chunks.user_id` NULLABLE (was NOT NULL).
2. **Migration 013**: add `documents.content_markdown TEXT`. Nullable — backfill happens in stage 2.
3. **Migration 014**: create `folders` table with RLS.
4. **Migration 015**: drop and re-create read/insert/update/delete RLS policies on `documents`, `document_chunks` to use the two-scope union predicate. **Run integration test from Pattern 1's note immediately** — this is the blast-radius migration.
5. **Migration 016**: extend `match_document_chunks_with_filters` and `match_document_chunks_hybrid` RPCs to accept new optional parameters: `match_folder_path TEXT DEFAULT NULL`, `match_scope TEXT DEFAULT NULL`. Default-NULL means existing call sites keep working.

### Stage 2 — content_markdown backfill (blocks grep/read tools)

**Decision: re-process via Docling, do NOT reconstruct from chunks.**

Reasoning:
- Reconstructing from chunks requires knowing the overlap boundaries (50 chars per chunker config) and de-duplicating; the chunker uses word-boundary splits which don't preserve original whitespace exactly. The reconstructed text would be *almost* the original markdown but not byte-identical, and grep regex hits would be subtly off.
- Re-processing via Docling is deterministic, gives canonical markdown, and exercises the same pipeline new uploads use. Cost is the original ingestion cost again per existing doc — acceptable for per-user document counts in the low thousands.
- The Episode 1 retrieval-debugging session already flagged HTML→markdown normalization as future work; re-processing into `content_markdown` opens that door.

Implementation:
- Add `ingestion.py:reprocess_for_markdown(document_id)` that runs Docling on the **current uploaded file in Supabase Storage** and writes the markdown export to `documents.content_markdown` (does NOT re-chunk, does NOT re-embed — chunks are already correct).
- `backend/scripts/backfill_content_markdown.py`: queries `SELECT id FROM documents WHERE content_markdown IS NULL ORDER BY created_at`, processes in batches of N with the existing ingestion semaphore, logs progress, idempotent.
- Run this once after Stage 1 lands. New uploads automatically populate `content_markdown` because we add the write to the existing `ingest_document()` path.
- Fallback for documents whose source file is missing from Storage: leave `content_markdown` NULL; grep/read skip those with a warning. Log a count of skipped docs.

### Stage 3 — Backend services (parallelizable group A)

These can happen in any order or in parallel once Stage 1 + 2 are done:

- **3a. `folder_service.py`** + folders router + extended files router (folder_path/scope on upload, PATCH for rename/move). Pure CRUD. Tests against real Supabase using existing `test_helpers`.
- **3b. `exploration_tools.py`** with the 5 tools. Each tool is independently testable. Start with `list_files` (simplest), then `tree`, then `glob`, then `read_document`, then `grep` (most complex due to regex + snippet extraction).
- **3c. `record_manager.py` extension** for scope/path-aware dedup keys. Small change, test in isolation.

### Stage 4 — Tool dispatch wiring (depends on 3a + 3b)

- **4a.** Extend `openai_client._build_tools()` to register the 5 new exploration tool schemas; extend `search_documents` schema with optional `folder_path` and `scope` parameters. Update `_build_system_prompt()` to describe when to use each new tool.
- **4b.** Add dispatch arms for the 5 new tools in `stream_response()`. Each arm calls `exploration_tools.X` and yields `tool_done` with the JSON result.
- **4c.** Extend `retrieve_chunks()` (used by `search_documents`) to accept `folder_path` and `scope` and pass them to the RPC. Default-None for both → existing behavior unchanged.

### Stage 5 — Explorer sub-agent (depends on 4a + 4b)

- **5a.** Add `run_explorer_sub_agent()` to `sub_agent.py`. Internal multi-turn loop dispatching to the same `exploration_tools.py` functions used by 4b.
- **5b.** Register `explore_knowledge_base` tool in `_build_tools()` and add dispatch arm in `stream_response()` that consumes the generator and forwards `sub_agent_*` events.
- **5c.** Extend `messages.py:event_generator` to pass through the two new event types (`sub_agent_tool_start`, `sub_agent_tool_done`).

### Stage 6 — Frontend (parallelizable with stages 3-5 once Stage 1 lands)

The whole frontend cluster can be built against a stub backend (or a single feature flag that hides the explorer until Stage 4 is ready):

- **6a. FileExplorerPanel + FolderTree + FolderNode + DocumentNode + Breadcrumbs.** Replaces FileUploadPanel in `Chat.tsx`. Uses `foldersApi` from `lib/api.ts` (new). Drag-move via HTML5 drag events, no extra library.
- **6b. UploadIntoFolder.** Wraps existing upload logic; just adds `folder_path` + `scope` to the multipart payload.
- **6c. ScopeSwitcher.** Two top-level sections, "Shared" (scope=global) and "My Files" (scope=user). Not a tab toggle — both visible simultaneously, expandable.
- **6d. MessageList rendering for explorer traces.** Extend `SubAgentSection` to render `sub_agent_tool_start`/`_done` as nested rows with per-tool icons (folder, file, magnifying glass, eye).

### Stage 7 — Tests + admin UI

- Backend tests added to `backend/scripts/test_all.py` as new module(s): test_folders, test_exploration_tools, test_explorer_sub_agent, test_two_scope_rls.
- Frontend Playwright additions in `e2e/full-suite.spec.ts`: explorer expand/collapse, create folder, move document, scope visibility (admin can write global, regular user cannot).
- Admin UI gains a global-scope upload affordance in `AdminSettings.tsx` or a separate "Shared Knowledge Base" admin page. The simplest: re-use the same explorer in the chat page but only show the global-scope write actions when `isAdmin === true`.

### What can parallelize

Once Stage 1 lands:
- Stage 2 backfill (running) || Stage 3a folder_service || Stage 3b exploration_tools || Stage 6a-c frontend tree UI

Once Stages 3 + 4 are done:
- Stage 5 explorer sub-agent || Stage 6d frontend trace rendering || Stage 7 tests

The single critical path: **Stage 1 → Stage 2 → Stage 4 → Stage 5**, because each later stage depends on the column/RLS/dispatch surface of the prior one.

## Migration / Backfill Strategy

### `folder_path` for existing Episode 1 documents

Trivial — the migration adds the column with `DEFAULT '/'`. Existing rows land at root automatically. No data movement, no script. Per the Project Decision: "Existing Episode 1 documents migrate to root `/` (not `/imported`, not wiped) — simplest backfill; folder organization is a user task post-migration."

### `scope` for existing documents

`DEFAULT 'user'`. All existing data belongs to specific users (no global content existed pre-Episode-2), so this is correct. Admins can move documents to global scope post-migration via the explorer UI.

### `user_id` nullability

Required for `scope='global'` rows. The migration runs `ALTER COLUMN user_id DROP NOT NULL`. Application code must always set `user_id = auth.uid()` for `scope='user'` writes — enforced by RLS WITH CHECK (see Pattern 1 sketch). A `CHECK ((scope='user' AND user_id IS NOT NULL) OR (scope='global' AND user_id IS NULL))` constraint defends against bugs at the DB layer.

### `content_markdown` backfill

Re-process via Docling, as decided in Stage 2. Concrete plan:

1. Add the column nullable.
2. New uploads write it inline in `ingest_document()` (the markdown export from Docling is currently produced and discarded after chunking — capture it).
3. One-time backfill script `scripts/backfill_content_markdown.py`:
   - Pages through `documents WHERE content_markdown IS NULL` ordered by `created_at` ASC.
   - For each, downloads the original from Supabase Storage, runs Docling, updates the row.
   - Uses the existing `_ingestion_semaphore` (or its own) to throttle.
   - Idempotent — safe to re-run.
   - Logs to stdout + writes a summary row count of successes / Docling failures / missing-source-file skips.
4. Operationally: run after Stage 1 lands, monitor, accept failures (those documents lose grep/read functionality but keep search_documents/analyze_document).

### Backwards-compat for tool callers

- `search_documents` adds optional `folder_path`, `scope` — existing message flows that don't pass them get `None`/`'both'` defaults, which preserves current behavior.
- The two extended RPCs add **optional** parameters with NULL defaults. Existing Python callers that don't pass them are unchanged.
- Episode 1 frontend UI for `MetadataFilterBar` is unchanged — folder filtering is intentionally not added there (per Project Decision).

## RLS Strategy (Two-Scope Data Model)

### Goals

1. Reads: every authenticated user sees `(global) ∪ (their own user-scoped)` rows on `documents`, `document_chunks`, `folders`.
2. Writes: `scope='user'` writes are tenant-self-only; `scope='global'` writes are admin-only.
3. No regression: an existing test that asserts user A cannot see user B's documents must continue to pass.

### Policy patterns (sketch — final SQL lives in migration 015)

```sql
-- documents
ALTER TABLE documents ALTER COLUMN user_id DROP NOT NULL;
ALTER TABLE documents
  ADD CONSTRAINT documents_scope_user_id_consistency CHECK (
    (scope = 'user' AND user_id IS NOT NULL)
    OR (scope = 'global' AND user_id IS NULL)
  );

DROP POLICY IF EXISTS "Users can view own documents" ON documents;
DROP POLICY IF EXISTS "Users can insert own documents" ON documents;
DROP POLICY IF EXISTS "Users can update own documents" ON documents;
DROP POLICY IF EXISTS "Users can delete own documents" ON documents;

CREATE POLICY "documents_select"
  ON documents FOR SELECT
  USING (
    scope = 'global'
    OR (scope = 'user' AND user_id = auth.uid())
  );

CREATE POLICY "documents_insert_user"
  ON documents FOR INSERT
  WITH CHECK (scope = 'user' AND user_id = auth.uid());

CREATE POLICY "documents_insert_global"
  ON documents FOR INSERT
  WITH CHECK (
    scope = 'global'
    AND user_id IS NULL
    AND EXISTS (
      SELECT 1 FROM public.profiles
      WHERE id = auth.uid() AND is_admin = true
    )
  );

CREATE POLICY "documents_update_user"
  ON documents FOR UPDATE
  USING (scope = 'user' AND user_id = auth.uid())
  WITH CHECK (scope = 'user' AND user_id = auth.uid());

CREATE POLICY "documents_update_global"
  ON documents FOR UPDATE
  USING (
    scope = 'global'
    AND EXISTS (
      SELECT 1 FROM public.profiles
      WHERE id = auth.uid() AND is_admin = true
    )
  )
  WITH CHECK (scope = 'global');

CREATE POLICY "documents_delete_user"
  ON documents FOR DELETE
  USING (scope = 'user' AND user_id = auth.uid());

CREATE POLICY "documents_delete_global"
  ON documents FOR DELETE
  USING (
    scope = 'global'
    AND EXISTS (
      SELECT 1 FROM public.profiles
      WHERE id = auth.uid() AND is_admin = true
    )
  );
```

Apply the same pattern verbatim to `document_chunks` and `folders` (substitute table name).

### Why two policies per write op (user + global) instead of one combined OR'd policy

Postgres RLS allows multiple permissive policies per `(table, command)`, and the row passes if any policy grants. This is cleaner than one big WHERE because:
- Reading the migration tells you immediately who can do what.
- A future change (e.g. "admins can also write into other users' user-scoped folders") requires adding one new policy, not editing a tangled OR.

### Service-role escape hatch

The backend uses `SUPABASE_SERVICE_ROLE_KEY` for some operations (admin tasks, ingestion writes). Service role bypasses RLS — be deliberate about which routes use it. Today `ingestion.py` runs with service-role for chunk inserts; that's fine. New routes (`folders.py`, the extended `files.py`) should use the **user's JWT** so RLS enforces the rules. The backfill script uses service-role.

### RLS test cases that must exist

- User A inserts scope='user' doc → visible to A, invisible to user B.
- User B cannot SELECT or DELETE user A's doc.
- Admin inserts scope='global' doc → visible to A and B; A and B cannot UPDATE or DELETE it.
- Non-admin user attempts INSERT scope='global' → fails (RLS WITH CHECK rejects).
- Admin demoted (is_admin → false) → loses ability to UPDATE existing global rows mid-session.
- `document_chunks` inherit the same isolation (test the chunk visibility via the search RPC, not just direct SELECT).

These extend `backend/scripts/test_rls_isolation.py` (or whichever existing module covers RLS today).

## Integration with Existing Tool Dispatch

### Registering new tools

`openai_client._build_tools()` returns a list of `types.Tool` objects. Today it builds 4-5 of them depending on flags. Add five more straightforward `types.Tool` entries (one per new tool) — schemas defined inline next to the existing ones. The `analyze_document` and `explore_knowledge_base` sub-agent tools deserve "use this when..." text in `_build_system_prompt()` matching the precision Episode 1 already uses for `analyze_document`.

### Extending `search_documents` non-breakingly

The current schema has `query` and (depending on metadata schema) filter parameters. Add two optional parameters:
- `folder_path: string` — "Only search within this folder prefix, e.g. '/projects/2026/'. Omit to search all accessible folders."
- `scope: 'user' | 'global' | 'both'` — "Default 'both'. Use 'user' to search only the user's private files, 'global' for the shared knowledge base."

In `retrieve_chunks()`, pass these through to the RPC. The RPC's new parameters default NULL — when NULL, the RPC adds no extra WHERE clause, so behavior is identical to today. Existing callers don't break.

### Explorer sub-agent fits the existing `analyze_document` pattern

The `analyze_document` flow today:
1. `stream_response` detects `tool_call_done` for `analyze_document`.
2. Calls `run_sub_agent()` (a generator).
3. For each `(event_type, data)` yielded, the dispatcher yields `(event_type, data)` — they pass through.
4. `messages.py:event_generator` translates `sub_agent_*` events into SSE JSON.
5. Frontend's `MessageList` accumulates them in a `SubAgentSection`.

`explore_knowledge_base` follows **exactly** this shape:
1. Same `tool_call_done` detection in dispatcher.
2. Calls `run_explorer_sub_agent()`.
3. Yields the same `sub_agent_start`, `sub_agent_token`, `sub_agent_done` events, **plus** new `sub_agent_tool_start` and `sub_agent_tool_done` for the inner tool calls.
4. `messages.py` adds two case arms forwarding the new event types.
5. Frontend `SubAgentSection` extends to render nested tool rows.

Net new SSE plumbing: two event types passed through. That's the only delta between extending sub-agent infrastructure for Explorer and adding it from scratch.

## Scaling Considerations

| Scale | Concerns | Adjustments |
|-------|----------|-------------|
| Demo / 1-10 users | None | The whole design works as-described |
| 10-100 users, ~1k docs/user | grep regex on `content_markdown` may slow at long docs | Add a GIN tsvector index on `content_markdown` and use `tsvector @@ to_tsquery` for grep where the pattern is keyword-y; fall back to regex for true regex patterns |
| 100+ users, 10k+ docs each | Folder rename touches all descendants | Move that mass-update into a Postgres function / background job; cap rename depth in UI |
| Very deep trees (>50 levels) | `tree` blows token budget even with depth caps | Already handled by `max_depth` arg + count-summary truncation |

The architecture is comfortably correct through the first tier; the second tier adds optional indexes; only the third needs structural change.

## Anti-Patterns

### Anti-Pattern 1: Reconstructing `content_markdown` from chunks at query time

**What people do:** "We already have chunks, why store full markdown? Just `SELECT content FROM document_chunks WHERE document_id=X ORDER BY chunk_index` and concatenate."

**Why it's wrong:** Chunks have 50-char overlap; concatenation duplicates that overlap region inside the reconstructed text. Word-boundary splitting also drops/normalizes whitespace. Grep regex hits will be off-by-a-few-chars, line numbers will be lies, `read_document` will double-show overlap regions. Tests appear to pass on simple cases and fail subtly on complex ones.

**Do this instead:** Store the canonical markdown export from Docling once at ingestion time; query it directly. Storage cost is negligible for text.

### Anti-Pattern 2: Folder filter as a UI dropdown in `MetadataFilterBar`

**What people do:** Add folder to the metadata filter dropdown alongside topic/document_type. Users select folder before sending a message; backend ANDs it into search.

**Why it's wrong:** Mixes content classification (topic/type) with location. Forces users to manually scope every query. Defeats the purpose of an agentic system that should pick the right tool.

**Do this instead:** Folder scoping is the LLM's job (via `search_documents.folder_path` arg or via the precision tools). The metadata filter bar stays focused on content classification only.

### Anti-Pattern 3: One mega-migration

**What people do:** A single 600-line `012_episode_2.sql` adding columns, RLS policies, RPCs, the folders table, and a backfill loop.

**Why it's wrong:** Reviewing it is a full afternoon; rolling back any one piece requires a custom revert; failure halfway leaves the DB in an unknown state.

**Do this instead:** Five small migrations as listed in Build Order. Each is reviewable in 10 minutes and individually revertable.

### Anti-Pattern 4: Refactoring the tool dispatcher into a registry "while we're in there"

**What people do:** "The if/elif chain is ugly, let me convert it to `TOOL_REGISTRY = {...}` and dispatch by lookup."

**Why it's wrong:** The chain is 9 entries after this milestone. Refactoring it touches every single tool — increasing the regression surface for the milestone — to save no real reading time. It also blocks parallel work (every tool PR fights merge conflicts in one file).

**Do this instead:** Add new `elif` arms next to existing ones. When the count crosses ~15 tools, then refactor.

### Anti-Pattern 5: Eager-loading `content_markdown` in `documents` SELECT queries

**What people do:** `SELECT * FROM documents` in `files.py:list_files` and `folder_service.list_folder`.

**Why it's wrong:** `content_markdown` can be hundreds of KB per row. Listing 200 documents pulls 50MB into the UI for no reason — only `grep`/`read_document` need that column.

**Do this instead:** Always enumerate columns explicitly. The folder explorer only needs `id, file_name, folder_path, scope, status, file_size, created_at`. `content_markdown` is read only by the two tools that need it.

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Google Gemini | Existing `_get_client()` cached client, `generate_content` (Call #1, detect) + `generate_content_stream` (Call #2, stream) | Explorer sub-agent does multi-turn `generate_content` followed by a final `generate_content_stream` — same SDK calls, just looped |
| Docling | Existing `extract_text()` | New: capture the markdown export it already produces internally and persist to `content_markdown` |
| Supabase | Existing supabase-py SDK; service-role for ingestion, user-JWT for routes | New folders router uses user-JWT exclusively |
| LangSmith | Existing `@traceable` decorator | Add `@traceable` to `run_explorer_sub_agent`, the new tool functions, and the new dispatch arms — Episode 1 trace coverage carries through |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| Explorer UI ↔ folders router | REST JSON over fetch + JWT | No SSE; folder mutations are synchronous |
| Explorer UI ↔ ingestion | Same as today: upload via REST, status via polling | New: upload payload carries `folder_path` + `scope` |
| Main agent ↔ exploration tools | In-process function call from `openai_client.stream_response` dispatch arm | Each tool returns a JSON-serializable dict; `tool_done` SSE event carries it back to the UI |
| Main agent ↔ Explorer sub-agent | Generator-of-events from `run_explorer_sub_agent()`; events forwarded through `messages.py:event_generator` | Same plumbing as `analyze_document`, plus two new event types |
| Exploration tools ↔ Postgres | Direct supabase-py `.table().select()` for tree/list_files/glob; raw RPC or `.rpc()` call for grep (Postgres regex) and for path-prefix LIKE queries with proper indexing | RLS handles scope filtering; tools add optional path/scope narrowing |
| `record_manager` ↔ documents | Same as today, dedup key extended to `(scope, user_id, folder_path, file_name, hash)` | Same file in two folders is now allowed; same file twice in one folder still dedups |

## Sources

- `.planning/codebase/ARCHITECTURE.md` (Episode 1 architecture, dated 2026-04-28)
- `.planning/codebase/STRUCTURE.md` (Episode 1 file layout)
- `.planning/codebase/INTEGRATIONS.md` (external services map)
- `.planning/PROJECT.md` (locked decisions for Episode 2)
- `backend/app/services/sub_agent.py` (existing single-document sub-agent — template for Explorer)
- `backend/app/services/openai_client.py` lines around `_build_system_prompt`, `_build_tools`, `stream_response` dispatch (existing tool dispatch shape)
- `backend/app/routers/messages.py` lines 89-103 (existing `sub_agent_*` SSE event forwarding)
- `backend/migrations/003_byo_retrieval.sql` (existing documents/chunks RLS — pattern to extend)
- `backend/migrations/005_profiles_and_settings.sql` (admin-gate RLS pattern via `is_admin` — directly reusable for global-scope writes)
- `backend/migrations/010_sub_agents.sql` (existing sub-agent schema additions — minimal pattern; we likely don't need new columns on `messages` for Explorer beyond what's there)
- Postgres RLS multi-policy semantics (well-established Postgres feature; multiple permissive policies are OR'd at evaluation time)

---
*Architecture research for: Episode 2 agentic-exploration tools on existing FastAPI + Supabase + Gemini RAG*
*Researched: 2026-04-28*
