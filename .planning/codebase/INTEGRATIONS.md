# External Integrations

**Analysis Date:** 2026-04-28

## APIs & External Services

**LLM & Embeddings:**
- Google Gemini - Primary LLM for chat completions and embeddings
  - SDK/Client: `google-genai` package (`backend/requirements.txt`)
  - Model: Configured via admin settings, accessed in `backend/app/services/openai_client.py`
  - Embeddings: `models/gemini-embedding-001` with 768 dimensions, used in `backend/app/services/ingestion.py`
  - Auth: `GEMINI_API_KEY` environment variable
  - Usage: Raw SDK calls only (NO LangChain per CLAUDE.md) - see `backend/app/services/openai_client.py:20-26`

**Web Search:**
- Tavily - Real-time web search for answering questions outside documents
  - SDK/Client: `tavily-python` package
  - Client: `TavilyClient` initialized in `backend/app/services/web_search.py:21`
  - Auth: `TAVILY_API_KEY` environment variable (optional, retrieved via `get_tavily_api_key()` from settings)
  - Feature: Optional/configurable via admin settings

**Reranking & Relevance Scoring:**
- Google Gemini (Primary) - Uses Gemini to score chunk relevance (`backend/app/services/reranker.py:23-52`)
- Cohere Rerank API (Optional) - Reranking service for document relevance
  - SDK/Client: `cohere` package
  - Model: `rerank-v3.5`
  - Auth: `COHERE_API_KEY` environment variable (optional)
  - Usage: Configured provider selection in `backend/app/services/reranker.py:74-89`

## Data Storage

**Databases:**
- Supabase (Postgres + pgvector)
  - Provider: Supabase Cloud
  - Connection: `SUPABASE_URL`, `SUPABASE_ANON_KEY` (frontend), `SUPABASE_SERVICE_ROLE_KEY` (backend)
  - Client: `supabase` Python package for backend, `@supabase/supabase-js` for frontend
  - Features: Row-Level Security (RLS) enforced on all user data tables
  - Vector storage: pgvector extension for semantic search (768-dim embeddings)
  - Usage: `backend/app/auth.py` - Auth client initialization; `backend/app/routers/*.py` - CRUD operations

**In-Memory/Structured Data:**
- DuckDB - SQL query engine for CSV/XLSX/structured data
  - Package: `duckdb`
  - Usage: `backend/app/services/sql_tool.py:9` - Query tabular data via SQL
  - Feature: Optional structured data analysis

**File Storage:**
- Supabase Storage (cloud object storage)
  - Connection: Via Supabase client
  - Usage: Document uploads stored in Supabase buckets

## Authentication & Identity

**Auth Provider:**
- Supabase Auth (Postgres-backed)
  - Implementation: Built into Supabase
  - Frontend: `supabase.auth.signUp()`, `supabase.auth.signIn()`, JWT token management in `frontend/src/contexts/AuthContext.tsx`
  - Backend: `get_current_user()` dependency in `backend/app/auth.py:14-34` validates JWT token against Supabase
  - Token: HTTP Bearer token in Authorization header
  - User isolation: RLS policies on all tables ensure users only see their own data

**Admin Authentication:**
- Role-based check via `is_admin` flag in profiles table
  - Gating: `backend/app/auth.py:43-52` - `get_admin_user()` checks admin flag

## Monitoring & Observability

**Error Tracking & Tracing:**
- LangSmith - Observability platform for LLM tracing and debugging
  - Package: `langsmith`
  - Auth: `LANGSMITH_API_KEY` environment variable
  - Project: `LANGSMITH_PROJECT` environment variable (default: "rag-masterclass")
  - Configuration: `LANGSMITH_TRACING` boolean flag (default: true)
  - Usage: `@traceable()` decorator on functions in:
    - `backend/app/services/openai_client.py` - LLM completions
    - `backend/app/services/web_search.py:14` - Web search tool
    - `backend/app/services/reranker.py:74` - Chunk reranking
  - Storage: Traces sent to LangSmith cloud service for analysis

**Logs:**
- Standard Python logging - Configured via `logging` module
- No centralized log aggregation configured

## CI/CD & Deployment

**Hosting:**
- Supabase (Database, Auth, Storage, Realtime) - Cloud-hosted Postgres
- Google Cloud (Gemini API) - Cloud LLM service
- LangSmith Cloud - Observability platform

**CI Pipeline:**
- Not configured (local development only per project context)

## Environment Configuration

**Required env vars - Frontend:**
- `VITE_SUPABASE_URL` - Supabase project URL
- `VITE_SUPABASE_ANON_KEY` - Supabase public anon key

**Required env vars - Backend:**
- `SUPABASE_URL` - Supabase project URL
- `SUPABASE_ANON_KEY` - Supabase public anon key
- `SUPABASE_SERVICE_ROLE_KEY` - Supabase service role key (for admin operations)
- `GEMINI_API_KEY` - Google Gemini API key

**Optional env vars - Backend:**
- `LANGSMITH_API_KEY` - LangSmith tracing API key
- `LANGSMITH_PROJECT` - LangSmith project name (default: "rag-masterclass")
- `LANGSMITH_TRACING` - Enable/disable LangSmith tracing (default: true)
- `TAVILY_API_KEY` - Tavily web search API key (required for web search feature)
- `COHERE_API_KEY` - Cohere reranking API key (optional alternative to Gemini reranking)

**Secrets location:**
- Frontend: `.env.local` (git-ignored)
- Backend: `.env` (git-ignored)
- Template: `.env.example` files in both `frontend/` and `backend/` directories

## Document Processing

**Document Parsing:**
- Docling - Layout-aware document converter supporting 17+ format types
  - Package: `docling`
  - Usage: `backend/app/services/ingestion.py` - Extract text from documents
  - Formats supported: PDF, DOCX, PPTX, XLSX, CSV, TXT, MD, JSON, XML, and more
  - Feature: OCR support for scanned documents
  - PPTX handling: Converts PPTX to PDF via PowerPoint COM (Windows-specific) before parsing

**Text Chunking:**
- Custom implementation in ingestion pipeline
  - Location: `backend/app/services/ingestion.py`
  - Process: Extract text → chunk by size → compute embeddings → store in pgvector

## Webhooks & Callbacks

**Incoming:**
- SSE (Server-Sent Events) - Real-time chat message streaming
  - Implementation: `sse-starlette` package
  - Endpoint: `/api/messages/{thread_id}/stream` (POST with streaming response)
  - Usage: Frontend listens to SSE for streamed LLM responses

**Outgoing:**
- None configured (internal system only)

## Record Deduplication

**Record Manager:**
- Custom implementation in `backend/app/services/record_manager.py`
- Purpose: Prevent ingesting duplicate documents
- Method: File hash matching + determine action (skip/new/update)

## Metadata & Content Storage

**Metadata Storage:**
- Configurable metadata schema in admin settings
- Storage: Supabase table with schema definition
- Usage: Dynamic filter construction for document search (`backend/app/services/openai_client.py:70-97`)

---

*Integration audit: 2026-04-28*
