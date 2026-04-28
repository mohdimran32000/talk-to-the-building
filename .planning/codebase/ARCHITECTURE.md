<!-- refreshed: 2026-04-28 -->
# Architecture

**Analysis Date:** 2026-04-28

## System Overview

```text
┌───────────────────────────────────────────────────────────────────┐
│                         Frontend (React + Vite)                    │
│  ┌─────────────┬──────────────┬─────────────┬────────────────────┐
│  │ Login/Auth  │ Chat Thread  │ File Upload │ Admin Settings     │
│  │  Pages      │  Management  │   Panel     │   Config UI        │
│  └─────────────┴──────────────┴─────────────┴────────────────────┘
│                             │
│                             ↓
│              ┌──────────────────────────┐
│              │ API Client & Auth Layer  │
│              │ `src/lib/api.ts`         │
│              │ `src/contexts/AuthContext.tsx`
│              └──────────────────────────┘
└───────────────────────────────────────────────────────────────────┘
                             │
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│ FastAPI Routers  │ │ FastAPI Routers  │ │ FastAPI Routers  │
│ /api/threads     │ │ /api/files       │ │ /api/settings    │
│ /api/messages    │ │ (file upload)    │ │ (admin config)   │
│ `app/routers/`   │ │ `routers/files`  │ │ `routers/settings`
└────────┬─────────┘ └────────┬─────────┘ └────────┬─────────┘
         │                    │                    │
         ▼                    ▼                    ▼
┌──────────────────────────────────────────────────────────────────┐
│                     Business Logic Services                       │
│  ┌──────────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ Chat Service     │  │ Ingestion    │  │ Settings & Auth  │   │
│  │ `openai_client`  │  │ Pipeline     │  │ `settings.py`    │   │
│  │ (stream_response)│  │ `ingestion`  │  │ `auth.py`        │   │
│  └──────────────────┘  └──────────────┘  └──────────────────┘   │
│                                                                    │
│  ┌──────────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ RAG Retrieval    │  │ Reranking    │  │ Sub-Agent &Tools │   │
│  │ `openai_client`  │  │ `reranker`   │  │ `sub_agent`      │   │
│  │ (retrieve_chunks)│  │ (hybrid mode)│  │ `web_search`     │   │
│  └──────────────────┘  └──────────────┘  │ `sql_tool`       │   │
│                                           └──────────────────┘   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Record Manager & Deduplication                           │   │
│  │ `record_manager.py` - hash-based dup detection           │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Supabase (PostgreSQL Backend)                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │ Auth & User  │  │ Threads &    │  │ Documents & Chunks   │   │
│  │ Data         │  │ Messages     │  │ (pgvector search)    │   │
│  │ `profiles`   │  │ `threads`    │  │ `documents`          │   │
│  │ `profiles`   │  │ `messages`   │  │ `document_chunks`    │   │
│  └──────────────┘  └──────────────┘  └──────────────────────┘   │
│                                                                    │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │ Metadata     │  │ Global       │  │ Structured Data      │   │
│  │ Storage      │  │ Settings     │  │ (CSV/XLSX tables)    │   │
│  │ (on chunks)  │  │ (API keys,   │  │ `structured_data`    │   │
│  │              │  │ feature       │  │ (DuckDB queries)     │   │
│  └──────────────┘  │ toggles)     │  └──────────────────────┘   │
│                    │ `global_settings` │ RLS: user isolation   │
│                    └──────────────────┘                          │
│                                                                    │
│  RPC Functions: match_document_chunks_hybrid, match_document_   │
│  chunks_with_filters (vector + keyword search with filters)     │
│  Row-Level Security: All tables filtered by user_id on SELECT  │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────┐
│                  External LLM & Services                          │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────────┐ │
│  │ Google Gemini  │  │ Docling        │  │ Cohere/Tavily      │ │
│  │ (native SDK)   │  │ (doc parsing)  │  │ (reranking/search) │ │
│  │ - Chat         │  │ - Text extract │  │ - Rerank chunks    │ │
│  │ - Embedding    │  │ - OCR support  │  │ - Web search       │ │
│  │ - Tool calls   │  │ - 17+ formats  │  │                    │ │
│  └────────────────┘  └────────────────┘  └────────────────────┘ │
│                                                                    │
│  LangSmith (observability): chat, tool calls, sub-agent traced   │
└──────────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| **FastAPI App** | HTTP server, routing, CORS, middleware setup | `app/main.py` |
| **Auth Layer** | Token validation, user lookup, role checks | `app/auth.py` |
| **Thread Router** | CRUD operations on conversation threads | `app/routers/threads.py` |
| **Message Router** | Message persistence + stateless chat streaming | `app/routers/messages.py` |
| **File Router** | Document upload, duplicate detection, async ingestion trigger | `app/routers/files.py` |
| **Settings Router** | Global config (API keys, features), admin-only | `app/routers/settings.py` |
| **Chat Service** | LLM chat streaming, tool calling, system prompt building | `app/services/openai_client.py` |
| **Ingestion Pipeline** | File parse → chunk → embed → store (background task) | `app/services/ingestion.py` |
| **Record Manager** | File hashing, deduplication logic | `app/services/record_manager.py` |
| **RAG Retrieval** | Vector/hybrid search, chunk fetching for context | `app/services/openai_client.py:retrieve_chunks()` |
| **Reranker** | Re-score retrieved chunks if enabled | `app/services/reranker.py` |
| **Sub-Agent** | Deep single-document analysis, full-context loading | `app/services/sub_agent.py` |
| **Web Search Tool** | External web query execution | `app/services/web_search.py` |
| **SQL Tool** | Text-to-SQL on structured data (CSV/XLSX) | `app/services/sql_tool.py` |
| **Metadata Service** | Document metadata extraction via LLM | `app/services/metadata.py` |
| **Settings Service** | Settings cache, feature flag lookups | `app/services/settings.py` |
| **Frontend App** | React SPA routing, auth context, page layout | `src/App.tsx` |
| **Chat Page** | Thread mgmt, message streaming, file polling, tools UI | `src/pages/Chat.tsx` |
| **Auth Context** | Session management, profile, login/logout flow | `src/contexts/AuthContext.tsx` |
| **API Client** | HTTP fetch wrapper, token injection, response parsing | `src/lib/api.ts` |
| **Message List** | Render messages, stream tokens, tool activity display | `src/components/MessageList.tsx` |
| **File Upload Panel** | File selection, progress, status polling, delete | `src/components/FileUploadPanel.tsx` |
| **Metadata Filter Bar** | UI for document metadata filtering | `src/components/MetadataFilterBar.tsx` |

## Pattern Overview

**Overall:** Multi-tier REST API with Server-Sent Events (SSE) for chat streaming and polling for async document ingestion status.

**Key Characteristics:**
- **Stateless chat**: Client loads full message history from DB, sends with each request; no server-side session state
- **Background ingestion**: File uploads trigger async background tasks with concurrency control (semaphore)
- **Content-addressable deduplication**: File hashes detect identical re-uploads; can skip or update
- **Hybrid search**: Vector + keyword (BM25) search combined via RRF (rank reciprocal fusion); optional reranking
- **Tool-oriented LLM**: Gemini models with function calling for search_documents, analyze_document, query_structured_data, web_search
- **Row-Level Security**: Every Supabase table enforces user_id isolation via RLS policies
- **Frontend polling**: Documents status updated via polling (5-10s intervals), not Realtime

## Layers

**Frontend (React + TypeScript):**
- Purpose: User interface for chat, document management, admin settings
- Location: `frontend/src/`
- Contains: Pages (Chat, Login, AdminSettings), components (MessageList, FileUploadPanel, etc.), API client, auth context
- Depends on: Supabase Auth client for session, HTTP API for backend calls, Tailwind + shadcn/ui for UI
- Used by: Browser users via Vite dev server or built dist bundle

**Backend Route Layer:**
- Purpose: HTTP request handling, dependency injection (auth), response formatting
- Location: `app/routers/` (threads.py, messages.py, files.py, settings.py)
- Contains: FastAPI routers with endpoint definitions, Pydantic models for request/response validation
- Depends on: Auth layer, service layer, Supabase client
- Used by: FastAPI main app for routing

**Backend Service Layer:**
- Purpose: Business logic — chat streaming, RAG retrieval, ingestion, tool execution
- Location: `app/services/` (openai_client.py, ingestion.py, settings.py, sub_agent.py, etc.)
- Contains: Stateless functions for LLM calls, embedding, search, SQL queries, web search
- Depends on: External APIs (Google Gemini, Cohere, Tavily), Supabase client
- Used by: Routers and background tasks

**Auth Layer:**
- Purpose: Token validation, user ID extraction, admin role checks
- Location: `app/auth.py`
- Contains: FastAPI dependency functions (get_current_user, get_admin_user), Supabase client builders
- Depends on: Supabase Auth for JWT verification
- Used by: All routers via Depends() injection

**Data Layer (Supabase):**
- Purpose: Persistent storage of users, threads, messages, documents, chunks, settings
- Location: PostgreSQL backend accessed via supabase-py SDK
- Contains: Tables with RLS policies, pgvector indexes, custom RPC functions for search
- Depends on: none (terminal)
- Used by: All backend services

## Data Flow

### Primary Path: Chat with RAG Retrieval (Chat Message + SSE Response)

1. **User sends message** (`frontend/src/pages/Chat.tsx:handleSendMessage()`)
   - Frontend calls `sendMessage(thread_id, content, metadata_filter)` in `src/lib/api.ts`
   - Adds Authorization header with Supabase session token
   - Sends POST `/api/threads/{thread_id}/messages` with user query

2. **Backend receives message** (`app/routers/messages.py:send_message()`)
   - `get_current_user` dependency validates token → extracts user_id
   - Verifies thread ownership (user_id + thread_id match)
   - Inserts user message into `messages` table
   - Fetches full conversation history from DB (stateless)

3. **Check document & data availability** (`app/routers/messages.py`)
   - Queries `documents` table for `user_id` + `status='ready'` → has_documents flag
   - Queries `structured_data` table for `user_id` → has_structured_data flag
   - These flags determine which tools are available in system prompt

4. **Build system prompt & start streaming** (`app/services/openai_client.py:stream_response()`)
   - `_build_system_prompt()` constructs prompt with available tools (search_documents, query_structured_data, analyze_document, web_search)
   - Builds tool definitions: search_documents with metadata schema, SQL tool, analyze_document, web_search
   - Calls Gemini with `stream=True` for token-by-token generation
   - LangSmith traces the call if enabled (`@traceable` decorator)

5. **Process Gemini stream & tool calls** (`app/services/openai_client.py:stream_response()`)
   - Iterate over stream events:
     - **token**: Yield streaming text content
     - **tool_call_start**: Tool invoked (search_documents, query_structured_data, analyze_document, web_search)
     - **tool_call_in_progress**: Executing tool, capture metadata (e.g., document_name for analyze_document)
     - **tool_call_done**: Tool result received, yield result content, append tool result to ongoing conversation
   - Handle each tool:
     - **search_documents**: Call `retrieve_chunks(query, user_id, metadata_filter)` → returns top-k chunks
     - **analyze_document**: Delegate to `run_sub_agent()` for full-document deep analysis → sub_agent_token events
     - **query_structured_data**: Execute Text-to-SQL via `sql_tool.execute_sql()`
     - **web_search**: Call `web_search.search(query)` via Tavily

6. **Stream response to frontend** (`app/routers/messages.py:event_generator()`)
   - Yield `EventSourceResponse` with JSON-encoded events:
     - `{"type": "token", "content": "..."}`
     - `{"type": "tool_start", "tool": "...", "input": ...}`
     - `{"type": "tool_done", "tool": "...", "result": ...}`
     - `{"type": "sub_agent_start", "document_name": "..."}`
     - `{"type": "sub_agent_token", "content": "..."}`
     - `{"type": "sub_agent_done"}`
     - `{"type": "done"}`

7. **Frontend consumes SSE stream** (`frontend/src/pages/Chat.tsx:handleSendMessage()`)
   - Opens EventSource to `/api/threads/{thread_id}/messages`
   - Listens for SSE events, parses JSON
   - Updates state for each event type:
     - token events → append to `streamingContent`
     - tool_thinking/tool_start/tool_done → append to `toolSteps[]` for ToolActivity display
     - sub_agent_token → accumulate in `subAgentContent`
     - done → finalize response, clear streaming flag

8. **Persist final assistant message** (`app/routers/messages.py:event_generator()`)
   - After stream completes, collects full `full_response` text
   - If non-empty, inserts into `messages` table with role='assistant'
   - Appends tool metadata (which tools were used, document names, results) if present
   - Updates thread `updated_at` timestamp

### Secondary Path: Document Upload + Ingestion Pipeline (Background Task)

1. **User uploads file** (`frontend/src/pages/Chat.tsx:handleUploadFile()`)
   - Calls `uploadFile(file)` → POST `/api/files/upload` with multipart file
   - Backend receives in `app/routers/files.py:upload_file()`

2. **Record Manager checks for duplicates** (`app/routers/files.py:upload_file()`)
   - Compute `file_hash = SHA-256(file_contents)`
   - Call `determine_action(file_hash, file_name, user_id, supabase)` → returns RecordAction
   - If action == "skip": file identical to existing doc → return existing doc with action='skipped'
   - If action == "update": file name matches but content differs → mark existing doc as pending, re-ingest
   - If action == "create": new file → insert document record with status='pending'

3. **Trigger async ingestion with throttle** (`app/routers/files.py:upload_file()`)
   - `background_tasks.add_task(_throttled_ingest, ingest_document, ...)` queues task
   - `_ingestion_semaphore` (max 2 concurrent) prevents overload
   - Task acquires semaphore, calls `ingest_document()` or `ingest_document_update()`, releases semaphore

4. **Extract text from file** (`app/services/ingestion.py:extract_text()`)
   - Route by file type:
     - **Plain text** (.txt, .md, .csv, .xml): decode UTF-8
     - **JSON**: parse and pretty-print
     - **PPTX/PPT**: convert to PDF via PowerPoint COM (Windows only), then process as PDF
     - **PDF/DOCX/XLSX/images/etc**: use Docling with OCR enabled for PDFs
   - Docling parses layout-aware structure, exports to Markdown
   - Fallback to UTF-8 decode if Docling fails

5. **Chunk and embed text** (`app/services/ingestion.py:ingest_document()`)
   - `chunk_text(text, chunk_size=500, overlap=50)` splits by word boundaries
   - `embed_batch(chunks)` calls Google Gemini embedding model (768-dim)
   - Uses token budget batching to stay under rate limits
   - Exponential backoff retry on 429 errors

6. **Extract structured data (if CSV/XLSX)** (`app/services/ingestion.py:_extract_structured_data()`)
   - Detect header row (scoring heuristic on text/numeric ratio)
   - Store table in DuckDB, serialize SQL schema
   - Insert into `structured_data` table for Text-to-SQL queries

7. **Compute and store chunk metadata** (`app/services/ingestion.py:ingest_document()`)
   - Call LLM to extract metadata (document_type, topic, summary, language, entities, keywords, etc.)
   - Batch metadata extraction if many chunks
   - Store metadata JSONB on `document_chunks` table for filtering

8. **Insert chunks and update status** (`app/services/ingestion.py:ingest_document()`)
   - For each chunk, insert:
     - `document_id`, `chunk_index`, `content`, `embedding`, `metadata`
   - Update `documents` table: `status='ready'`, clear `error_message`
   - If error: `status='failed'`, store error message

9. **Frontend polls for status** (`frontend/src/components/FileUploadPanel.tsx`)
   - Interval polling: every 5-10 seconds while status in ['pending', 'processing']
   - Query `documents` table for `id, status, error_message`
   - Update `files` state when status changes
   - When status='ready', reload file list to get new metadata

## Key Abstractions

**Message History:**
- Purpose: Store conversation context for stateless inference
- Examples: `app/routers/messages.py`, `app/models/schemas.py:MessageResponse`
- Pattern: Immutable log of (role, content) pairs; client responsible for building request context

**Document Chunks:**
- Purpose: Store parsed document content with embeddings for RAG retrieval
- Examples: `document_chunks` table in Supabase
- Pattern: Content-addressed storage; chunk hash deduplicates identical snippets; metadata JSONB enables filtering

**Tool Definitions:**
- Purpose: Schema & descriptions for LLM function calling
- Examples: `search_documents`, `analyze_document`, `query_structured_data`, `web_search` in `app/services/openai_client.py`
- Pattern: Dynamic schema built from settings (e.g., metadata_schema influences search_documents filters)

**Settings & Feature Flags:**
- Purpose: Centralized admin configuration with caching
- Examples: `app/services/settings.py`, `global_settings` table
- Pattern: Cached at service layer (60s TTL) to avoid DB hammering

**Record Action:**
- Purpose: Deduplication decision — whether to create/skip/update a document upload
- Examples: `app/services/record_manager.py:RecordAction`
- Pattern: Dataclass with action + existing_document_id for two-step upsert

## Entry Points

**Backend:**
- **HTTP Server**: `app/main.py:app` — FastAPI instance, mounted routers, CORS middleware
  - Entry: `uvicorn app.main:app --host 0.0.0.0 --port 8001`
  - Health check: `GET /health`
  - Triggers: HTTP requests from frontend

**Frontend:**
- **React App**: `src/main.tsx` — ReactDOM render
  - Entry: `npm run dev` → Vite dev server on localhost:5173
  - Root component: `src/App.tsx` (BrowserRouter, ThemeProvider, AuthProvider)
  - Triggers: Browser page load

**Background:**
- **Ingestion Tasks**: `app/routers/files.py:_throttled_ingest()` with semaphore
  - Triggered: When file upload completes (background_tasks)
  - Runs: In FastAPI background worker pool

## Architectural Constraints

- **Threading:** Backend is async (FastAPI/uvicorn) with background thread pool for ingestion; frontend is single-threaded React event loop. Ingestion concurrency controlled by Semaphore(2).
- **Global state:** 
  - Backend: `_client_cache` in `app/services/openai_client.py` (cached Gemini client), `_cache` in `app/services/settings.py` (settings cache with 60s TTL)
  - Frontend: React context for auth state (AuthContext), local useState for UI state per page
- **Circular imports:** None known; services are imported by routers one-way
- **Stateless chat:** Server does not maintain conversation context; client must fetch full history before each request
- **Single LLM provider:** Locked to Google Gemini (native SDK); no LangChain/LangGraph abstraction
- **RLS enforcement:** All DB reads filtered by `user_id`; DELETE/UPDATE queries must include user_id check

## Anti-Patterns

### Mutable Tool Metadata

**What happens:** Tool call metadata (document_name, tool results) is built incrementally during stream processing, passed to message insertion at the end. If stream crashes mid-tool-call, metadata is incomplete.

**Why it's wrong:** Tool calls may not finish if connection drops or API error occurs; partial tool metadata persists in DB with truncated results.

**Do this instead:** Only persist tool_metadata after full response is received and validated; consider tool-call completion guarantees (acknowledge before moving to next turn).

### No Request Batching on Frontend

**What happens:** Frontend sends one message at a time, waits for stream completion before allowing next message. If user rapidly submits multiple queries, they queue up.

**Why it's wrong:** No front-end deduplication of concurrent requests; user could submit same query twice thinking it didn't go through.

**Do this instead:** Disable message input during streaming; show "sending..." indicator; ignore duplicate rapid clicks.

## Error Handling

**Strategy:** Graceful degradation with client notification via SSE error events.

**Patterns:**
- LLM errors (rate limit, API down): Catch in stream_response(), yield error event, exit generator
- Chunk retrieval failure: Log warning, continue stream with note that search failed
- Ingestion errors: Catch exception, update document status='failed' with error_message
- Auth failures: Return 401 from get_current_user dependency; FastAPI converts to HTTP response
- RLS violations: Supabase RLS policies silently return empty result sets (no error to client)

## Cross-Cutting Concerns

**Logging:** Python logging module in backend services; use `logger = logging.getLogger(__name__)` pattern. Frontend uses console.log (silent in production). LangSmith provides structured traces for LLM calls.

**Validation:** Pydantic models on all request/response schemas (`app/models/schemas.py`). Frontend TypeScript interfaces in `src/lib/api.ts`. Form validation via React onChange handlers.

**Authentication:** Supabase JWT tokens passed in Authorization header; backend validates with `supabase.auth.get_user(token)`. Frontend stores token in Supabase SDK's internal session storage (via browser IndexedDB).

**Observability:** LangSmith integration via `@traceable` decorators on `stream_response()`, `_execute_search_documents()`, `run_sub_agent()`. Traces capture tool calls, LLM inputs/outputs, token usage.

---

*Architecture analysis: 2026-04-28*
