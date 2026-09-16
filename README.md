# Talk to the Building

An agentic RAG assistant for building operations. Upload O&M manuals, electrical load schedules, maintenance contracts, and drawings — then ask questions in plain English and get grounded, cited answers.

> *"What's the total connected load for Block B?"* → routed to text-to-SQL over the ingested load schedules → `1,234.30 kW` with a per-panel breakdown.

Built entirely by collaborating with Claude Code — no hand-written code. Started from [The AI Automators' Agentic RAG Masterclass](https://www.youtube.com/watch?v=xgPWCuqLoek) and extended well beyond the course.

## What It Does

**Chat**
- Threaded conversations with SSE streaming, stop button, and markdown rendering
- Tool-activity display — see which tool the agent picked and why
- Collapsible sub-agent reasoning sections
- Dark / light / system theme

**Documents**
- Drag-and-drop upload with live processing status (polling)
- File explorer with folders, breadcrumbs, and context menus
- Two scopes: personal documents and shared (global) documents, enforced by Row-Level Security
- Metadata badges (document type, topic, keywords, entities…) extracted by the LLM at ingest

**Ingestion pipeline**
- [Docling](https://github.com/docling-project/docling) parsing — layout-aware, OCR, 30+ formats (PDF, DOCX, PPTX, XLSX, images…)
- Record Manager deduplication: identical file → skip, changed file → re-ingest, new file → create
- CSV/XLSX rows also land in a `structured_data` table for SQL querying (smart header detection, typed columns)
- Chunking → `gemini-embedding-001` embeddings (1536 dims, migration 022) → pgvector

**Retrieval**
- Hybrid search: vector (pgvector) + keyword (tsvector) fused with Reciprocal Rank Fusion
- Optional reranking: Gemini (LLM-as-judge) or Cohere Rerank API
- Metadata filtering — manual via UI filter bar, or agentic (the LLM picks filters itself)
- Table routing for text-to-SQL: a deterministic scorer picks the two or three structured
  tables a question needs, so the SQL prompt carries those schemas instead of every table's
  (see [Table cards](#table-cards) — optional, the app runs without them)

**Agentic tools** — the LLM decides per message which tool (if any) to call:

| Tool | What it does |
|---|---|
| `search_documents` | Hybrid semantic + keyword search over document chunks |
| `query_structured_data` | Text-to-SQL over ingested spreadsheets (DuckDB in-memory) |
| `web_search` | Tavily web search fallback |
| `analyze_document` | Sub-agent reads an entire document in isolated context |
| Explorer sub-agent | 5 file-system-style tools: `tree`, `list_files`, `glob`, `grep`, `read_document` |

**Admin settings UI**
- Model picker (fetched live from the Gemini API), LLM + LangSmith config
- Toggles for hybrid search, reranking (+ provider), text-to-SQL, web search
- Editable metadata extraction schema

## Architecture

```
User message
     │
     ▼
Gemini call #1 (non-streaming, tools enabled) — picks a tool, or answers directly
     │
     ├── search_documents ──► embed query → hybrid RPC (vector + keyword + RRF) → rerank
     ├── query_structured_data ──► generate SQL → DuckDB over structured_data → result table
     ├── web_search ──► Tavily API → formatted results with sources
     └── analyze_document ──► sub-agent Gemini call with full document context
     │
     ▼
Tool result injected into system prompt
     │
     ▼
Gemini call #2 (streaming, no tools) ──► answer streamed to the browser via SSE
```

Design principles (see [CLAUDE.md](./CLAUDE.md)):
- **No LangChain / LangGraph** — raw SDK calls, hand-rolled orchestration in plain Python
- **Stateless completions** — chat history stored in Postgres and sent explicitly
- **RLS everywhere** — users only ever see their own data (plus shared global docs)

## Tech Stack

| Layer | Tech |
|---|---|
| Frontend | React, TypeScript, Vite, Tailwind v4, shadcn/ui |
| Backend | Python, FastAPI, SSE (sse-starlette) |
| Database | Supabase — Postgres, pgvector, Auth, Storage, RLS |
| LLM | Google Gemini via native `google-genai` SDK (model configurable in admin settings) |
| Doc parsing | Docling (layout analysis + OCR, GPU-accelerated) |
| Text-to-SQL | DuckDB (in-memory) |
| Web search | Tavily |
| Reranking | Gemini or Cohere (optional) |
| Observability | LangSmith |

## Table cards

The table router scores a question against one **card** per structured table — what the table
holds, its columns, a sample of its values, and its declared joins. Cards are **not produced by
this app**: they come from a separate document-preparation step that reads verified source
documents, and they are published into the `table_cards` table (migration 025), keyed by user.
They are deliberately not committed here — a card carries sample values from the owner's own
data, and this repository is public.

**The app runs without them.** `_load_table_cards()` tries the database, then an optional local
`backend/app/data/table_cards.json` (gitignored), then gives up and returns an empty list — every
step degrades, none raises. With no cards, `execute_sql_query` skips the routing block entirely
and sends the SQL generator the schema of *every* structured table the user owns, which is what
it did before the router existed. `select_tables(question, [])` returns `[]` by construction.
So: no cards → correct answers, larger prompts; cards → the same answers on a pruned schema.

## Testing & Evals

| Suite | Size | Command | Needs |
|---|---|---|---|
| Backend validation | 112 tests | `cd backend && venv/Scripts/python scripts/test_all.py` | backend on :8001 + Supabase |
| Backend unit tests | 10 standalone scripts | `cd backend && venv/Scripts/python -X utf8 tests/test_table_router.py` (one per file) | nothing — no network, no keys |
| Frontend E2E (Playwright) | 26 tests | `cd frontend && npx playwright test e2e/full-suite.spec.ts` | backend (:8001) + frontend (:5173) |

The LLM-driven **evaluation question banks are not in this repository**. They encode one
building's verified facts as ground truth, so they are specific to that corpus rather than to
this app, and they are kept privately with it. The results they produced are quoted here as
measurements of that corpus on that date, not as properties of the code:
as of **2026-07-22 (`v1.1`)** the two suites reached two consecutive 100% runs — 57/57 on the
load-schedule SQL matrix and 136/136 on document QA; on **2026-09-06** the project's retrieval
ruler measured 42/43 on its holdout split, twice, after the neighbouring-value fix listed under
`v1.2` below.

## Getting Started

1. **Supabase** — create a project, then run every file in `backend/migrations/` in the SQL
   Editor, in filename order, **001 through 025**. Notes: there are two `021_` files (run both:
   `021_admin_test_user.sql` and `021_fix_vector_index.sql`); `022` widens the embedding column
   to 1536 dims and must be applied before any document is ingested (it drops and recreates the
   column and both search RPCs); `023`–`025` add chunk identity, global-scope search and the
   router's `table_cards` table. `017` does not exist — the sequence skips it.
2. **Backend**
   ```bash
   cd backend
   python -m venv venv
   venv/Scripts/pip install -r requirements.txt   # Windows; use venv/bin/pip on macOS/Linux
   ```
   Create `backend/.env` (copy `backend/.env.example` and fill it in):
   ```
   SUPABASE_URL=...
   SUPABASE_ANON_KEY=...
   SUPABASE_SERVICE_ROLE_KEY=...
   GEMINI_API_KEY=...
   LANGSMITH_API_KEY=...             # optional, for tracing
   LANGSMITH_PROJECT=...             # optional
   TEST_USER_ADMIN_PASSWORD=...      # optional, only needed to run the backend tests
   ```
   Tavily (web search) and Cohere (reranking) keys are **not** env vars — set them in the admin **Settings** UI.

   Run: `venv/Scripts/python -m uvicorn app.main:app --port 8001`
3. **Frontend**
   ```bash
   cd frontend
   npm install
   ```
   Create `frontend/.env.local` (copy `frontend/.env.example` and fill it in):
   ```
   VITE_SUPABASE_URL=...
   VITE_SUPABASE_ANON_KEY=...
   ```
   Run: `npm run dev` → open http://localhost:5173
4. Sign up, promote your user to admin (`profiles.is_admin = true` in Supabase), configure models/tools in **Settings**, upload documents, and start asking questions.

## Project History

Built in public, checkpoint by checkpoint — each tag is a working snapshot you can check out:

| Tag | Milestone |
|---|---|
| `Module-2-With-Settings` … `Module-8-Sub-Agents` | Course modules: BYO retrieval, record manager, metadata + auto-filter, Docling, hybrid search, additional tools, sub-agents |
| `Episode-1-Complete` | End of Episode 1 (Modules 1–8) |
| `v1.0` | File explorer milestone — folders, two-scope RLS, 5 exploration tools, explorer sub-agent |
| `v1.1` | Eval hardening — 57-case SQL/routing matrix + 136-case doc-QA audit, both at two consecutive 100% runs |
| `v1.2` | Retrieval hardening — place-aware table router (a room named in the question reaches the tables whose rows carry a location), router cards read from Supabase instead of a repo file, Cohere reranking, migrations 022–025 (1536-dim embeddings, chunk identity, global-scope search, `table_cards`), and an answer fix that stops a neighbouring column's value standing in for the one asked for |

## Docs

- [PRD.md](./PRD.md) — original product requirements (the 8 course modules)
- [CLAUDE.md](./CLAUDE.md) — rules and context for Claude Code
- `backend/migrations/*.sql` — each migration's header records why it exists and what it changes

## Credits & Community

This project follows [The AI Automators' Claude Code Agentic RAG Masterclass](https://www.youtube.com/watch?v=xgPWCuqLoek) — a course where you don't write the code, you direct Claude Code and course-correct. Join builders creating production-grade AI systems at [The AI Automators community](https://www.theaiautomators.com/).
