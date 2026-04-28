# Codebase Structure

**Analysis Date:** 2026-04-28

## Directory Layout

```
project-root/
├── backend/                        # Python FastAPI backend
│   ├── app/
│   │   ├── main.py                 # FastAPI app instance, router registration, middleware
│   │   ├── auth.py                 # JWT validation, user extraction, role checks
│   │   ├── models/
│   │   │   ├── schemas.py           # Pydantic request/response models
│   │   │   └── __init__.py
│   │   ├── routers/
│   │   │   ├── threads.py           # POST/GET/DELETE /api/threads
│   │   │   ├── messages.py          # GET /api/threads/{id}/messages, POST (SSE streaming)
│   │   │   ├── files.py             # POST /api/files/upload, GET, DELETE
│   │   │   ├── settings.py          # GET /api/settings, PUT (admin only)
│   │   │   └── __init__.py
│   │   ├── services/
│   │   │   ├── openai_client.py     # Gemini chat streaming, RAG retrieval, tool building
│   │   │   ├── ingestion.py         # File parse → chunk → embed → store pipeline
│   │   │   ├── record_manager.py    # File hashing, duplicate detection logic
│   │   │   ├── reranker.py          # Rerank chunks if enabled
│   │   │   ├── sub_agent.py         # Deep single-document analysis
│   │   │   ├── web_search.py        # Tavily web search integration
│   │   │   ├── sql_tool.py          # Text-to-SQL execution via DuckDB
│   │   │   ├── metadata.py          # Document metadata extraction
│   │   │   ├── settings.py          # Settings cache, feature flag lookups
│   │   │   └── __init__.py
│   │   └── __init__.py
│   ├── migrations/                  # Supabase SQL migrations (schema, RLS, RPC)
│   ├── scripts/
│   │   ├── test_all.py              # Main test runner (112 tests across modules)
│   │   ├── test_helpers.py          # Auth, SSE parsing, polling utilities
│   │   └── run_migrations.py        # Apply pending migrations
│   ├── requirements.txt             # Python dependencies
│   ├── venv/                        # Virtual environment (git-ignored)
│   └── .env                         # Environment variables (git-ignored)
│
├── frontend/                        # React + Vite + TypeScript
│   ├── src/
│   │   ├── main.tsx                 # ReactDOM.createRoot entry
│   │   ├── App.tsx                  # BrowserRouter, ThemeProvider, AuthProvider, Routes
│   │   ├── index.css                # Tailwind + global styles
│   │   ├── pages/
│   │   │   ├── Chat.tsx             # Main chat interface (threads, messages, files, tools)
│   │   │   ├── Login.tsx            # Email/password login form
│   │   │   ├── Signup.tsx           # Email/password signup form
│   │   │   └── AdminSettings.tsx    # Admin-only config UI
│   │   ├── components/
│   │   │   ├── ThreadSidebar.tsx    # Thread list, create, delete
│   │   │   ├── MessageList.tsx      # Render messages, stream tokens, markdown
│   │   │   ├── MessageInput.tsx     # User input form, metadata filter UI
│   │   │   ├── FileUploadPanel.tsx  # File upload, status polling, metadata display
│   │   │   ├── MetadataFilterBar.tsx # Metadata filter controls
│   │   │   ├── ToolActivity.tsx     # Tool call visualization (start, progress, done)
│   │   │   ├── ThemeToggle.tsx      # Light/dark theme switch
│   │   │   ├── ProtectedRoute.tsx   # Auth guard for routes
│   │   │   ├── AdminRoute.tsx       # Admin-only route guard
│   │   │   ├── ui/                  # shadcn/ui components (Button, Input, Dialog, etc.)
│   │   │   └── .../                 # Other shadcn components
│   │   ├── contexts/
│   │   │   └── AuthContext.tsx      # Auth state (user, session, profile, isAdmin)
│   │   ├── lib/
│   │   │   ├── api.ts               # HTTP fetch wrapper, endpoints, TypeScript interfaces
│   │   │   ├── supabase.ts          # Supabase client initialization
│   │   │   └── utils.ts             # Shared utilities (formatters, etc.)
│   │   └── vite-env.d.ts            # Vite type definitions
│   ├── e2e/
│   │   ├── full-suite.spec.ts       # Playwright end-to-end tests (26 tests)
│   │   └── playwright.config.ts
│   ├── public/                      # Static assets
│   ├── package.json                 # Dependencies (React, Vite, Tailwind, shadcn, Playwright)
│   ├── tsconfig.json                # TypeScript config
│   ├── vite.config.ts               # Vite build config
│   ├── eslint.config.js             # ESLint rules
│   ├── .env.local                   # Frontend env vars (git-ignored)
│   └── node_modules/                # Dependencies (git-ignored)
│
├── supabase/                        # Supabase project config
│   ├── config.toml                  # Local dev config
│   └── migrations/                  # Supabase CLI migrations (symlink to backend/migrations)
│
├── .planning/
│   └── codebase/
│       ├── ARCHITECTURE.md          # Architecture & data flow (this file)
│       ├── STRUCTURE.md             # Directory layout & conventions (this file)
│       ├── STACK.md                 # Technology stack
│       ├── INTEGRATIONS.md          # External APIs & services
│       ├── CONVENTIONS.md           # Code style & patterns
│       ├── TESTING.md               # Test framework & patterns
│       └── CONCERNS.md              # Tech debt & issues
│
├── .agent/
│   └── plans/                       # Implementation plans (numbered, markdown)
│
├── .claude/
│   ├── agents/                      # Claude agent definitions
│   ├── commands/                    # GSD command implementations
│   └── get-shit-done/               # GSD framework files
│
├── CLAUDE.md                        # Project constraints & rules
├── PROGRESS.md                      # Module completion status
├── .gitignore
└── README.md
```

## Directory Purposes

**`backend/app/`** — Python FastAPI application code
- **main.py**: Bootstraps FastAPI app, adds CORS middleware, registers all routers
- **auth.py**: Supabase JWT validation; dependency injection for `get_current_user` and `get_admin_user`
- **models/**: Pydantic BaseModel definitions for API contracts
- **routers/**: Request handlers organized by resource (threads, messages, files, settings)
- **services/**: Reusable business logic (LLM calls, ingestion, RAG, tools)

**`backend/migrations/`** — SQL schema definitions
- Run via `python scripts/run_migrations.py`
- Includes: table definitions, RLS policies, custom RPC functions (match_document_chunks_hybrid, etc.)
- Managed by Supabase migration system

**`backend/scripts/`** — Testing and utilities
- **test_all.py**: Main test runner; runs all test suites and reports pass/fail
- **test_helpers.py**: Shared test utilities (auth flows, SSE parsing, cleanup)
- Must be run with backend running on localhost:8001

**`frontend/src/pages/`** — React page components
- Each corresponds to a route in `App.tsx`
- **Chat.tsx**: Main application; manages threads, messages, file uploads, ingestion polling
- **Login.tsx**, **Signup.tsx**: Auth pages
- **AdminSettings.tsx**: Admin configuration (API keys, feature toggles, metadata schema)

**`frontend/src/components/`** — Reusable UI components
- Organized by feature (sidebar, message rendering, file upload, metadata filters, tools)
- Use Tailwind CSS for styling
- Import shadcn/ui components from `./ui/`

**`frontend/src/contexts/`** — React Context providers
- **AuthContext.tsx**: Manages Supabase session, user profile, admin role
- Consumed by route guards and pages

**`frontend/src/lib/`** — Shared frontend utilities
- **api.ts**: HTTP client wrapping fetch, type definitions for all API contracts
- **supabase.ts**: Supabase client initialization (minimal config)
- **utils.ts**: Formatting, helper functions

**`frontend/e2e/`** — Playwright end-to-end tests
- Tests full app flows (auth, threads, messages, documents, UI interactions)
- Run via `npx playwright test`
- Requires both backend (8001) and frontend (5173) running

## Key File Locations

**Entry Points:**
- Backend: `backend/app/main.py` — FastAPI app
- Frontend: `frontend/src/main.tsx` → `src/App.tsx` — React entry
- Backend start: `uvicorn app.main:app --reload --port 8001`
- Frontend start: `npm run dev` (Vite dev server)

**Configuration:**
- Backend env: `.env` in `backend/` (GEMINI_API_KEY, SUPABASE_URL, etc.)
- Frontend env: `.env.local` in `frontend/` (VITE_SUPABASE_URL, etc.)
- Project rules: `CLAUDE.md` (constraints, testing, planning conventions)
- Progress tracking: `PROGRESS.md` (module completion status)

**Core Logic:**
- Chat/RAG: `backend/app/services/openai_client.py`
- File ingestion: `backend/app/services/ingestion.py`
- Duplicate detection: `backend/app/services/record_manager.py`
- Document analysis: `backend/app/services/sub_agent.py`
- Settings/cache: `backend/app/services/settings.py`

**Testing:**
- Test runner: `backend/scripts/test_all.py`
- Test helpers: `backend/scripts/test_helpers.py`
- E2E tests: `frontend/e2e/full-suite.spec.ts`
- Playwright config: `frontend/e2e/playwright.config.ts`

## Naming Conventions

**Files:**
- Backend modules: `snake_case.py` (e.g., `openai_client.py`, `record_manager.py`)
- Frontend components: `PascalCase.tsx` (e.g., `Chat.tsx`, `MessageList.tsx`)
- Frontend utilities: `camelCase.ts` (e.g., `api.ts`, `utils.ts`)
- Test files: `test_*.py` (backend) or `*.spec.ts` (frontend)

**Directories:**
- Python packages: `snake_case/` (e.g., `services/`, `routers/`, `migrations/`)
- React component groups: `lowercase/` (e.g., `components/`, `pages/`, `contexts/`)
- Nested components: Under parent feature directory with same name (e.g., `components/ui/` for shadcn primitives)

**Functions & Classes:**
- Python functions: `snake_case` (e.g., `get_current_user`, `retrieve_chunks`, `embed_text`)
- Python classes: `PascalCase` (e.g., `RecordAction`, `MessageResponse`)
- React components: `PascalCase` (e.g., `MessageList`, `FileUploadPanel`)
- React hooks: `use*` prefix (e.g., `useAuth()` from AuthContext)
- Utility functions: `camelCase` (e.g., `formatSize()`, `statusBadge()`)

**Variables:**
- Constants: `UPPER_SNAKE_CASE` (e.g., `EMBEDDING_MODEL`, `CACHE_TTL`)
- State/data: `camelCase` (e.g., `messages`, `isStreaming`, `toolSteps`)

## Where to Add New Code

**New API Endpoint:**
- Create handler in `backend/app/routers/{resource}.py`
- Add Pydantic model to `backend/app/models/schemas.py` if needed
- Endpoint pattern: FastAPI router with `@router.get/post/put/delete()`, dependency injection for auth
- Example: `@router.get("/{thread_id}", response_model=ThreadResponse)` + `user_id: str = Depends(get_current_user)`

**New Backend Service/Tool:**
- Create file in `backend/app/services/{service_name}.py`
- Add main function/class, import in routers as needed
- Example: `web_search.search(query)` → called from `openai_client.py` as a tool
- Add tests to `backend/scripts/test_*.py` module

**New Frontend Page:**
- Create file in `frontend/src/pages/{PageName}.tsx`
- Export React component as default
- Add route to `App.tsx` inside `<Routes>`
- Wrap with `<ProtectedRoute>` if auth required, `<AdminRoute>` if admin-only
- Example: `<Route path="/documents" element={<ProtectedRoute><Documents /></ProtectedRoute>} />`

**New Frontend Component:**
- Create file in `frontend/src/components/{ComponentName}.tsx`
- Export React component as default
- Use props for inputs, callbacks for outputs
- Import shadcn/ui components from `./ui/`
- Style with Tailwind classes; group related styles with `className="..."`

**New Metadata Field:**
- Update `DEFAULT_METADATA_SCHEMA` in `backend/app/services/settings.py`
- Update `MetadataFieldDefinition` in `backend/app/models/schemas.py` and `frontend/src/lib/api.ts`
- UI will auto-generate filter UI in `MetadataFilterBar.tsx` based on schema
- Metadata extraction happens automatically in `ingest_document()` via LLM

**New Database Table:**
- Create migration file in `backend/migrations/{timestamp}_add_*.sql`
- Include RLS policy: `ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;`
- Add policy: `CREATE POLICY "{table}_isolation" ON {table} FOR SELECT USING (user_id = auth.uid());`
- Run `python backend/scripts/run_migrations.py`
- Add Pydantic response model if exposing via API

**New Feature Toggle:**
- Add column to `global_settings` table in migration
- Add getter in `backend/app/services/settings.py` (e.g., `get_feature_enabled()`)
- Add to `GlobalSettingsResponse` in `backend/app/models/schemas.py`
- Add UI control in `frontend/src/pages/AdminSettings.tsx`
- Add test in `backend/scripts/test_settings.py`

## Special Directories

**`backend/venv/`:**
- Purpose: Python virtual environment
- Generated: Yes (by `python -m venv venv`)
- Committed: No (in .gitignore)
- Note: Activate with `source venv/Scripts/activate` (Windows) or `venv/bin/activate` (Unix)

**`frontend/node_modules/`:**
- Purpose: npm dependencies
- Generated: Yes (by `npm install`)
- Committed: No (in .gitignore)
- Note: Clean with `npm ci` if broken; commit `package-lock.json` instead

**`frontend/dist/`:**
- Purpose: Vite production build output
- Generated: Yes (by `npm run build`)
- Committed: No (in .gitignore)
- Note: Generated before deploy; contains optimized JS/CSS bundles

**`backend/migrations/`:**
- Purpose: SQL schema versions managed by Supabase
- Generated: No (hand-written)
- Committed: Yes
- Convention: `{timestamp}_{description}.sql`, e.g., `20250101120000_create_threads_table.sql`
- Note: Supabase CLI tracks applied migrations; revert by deleting files and running migrate down

**`.planning/codebase/`:**
- Purpose: GSD codebase analysis documents (ARCHITECTURE.md, STRUCTURE.md, etc.)
- Generated: Yes (by `/gsd-map-codebase`)
- Committed: Yes
- Note: Used by `/gsd-plan-phase` and `/gsd-execute-phase`; regenerate when arch changes significantly

**`.agent/plans/`:**
- Purpose: Implementation plans created by `/gsd-plan-phase`
- Generated: Yes (by `/gsd-plan-phase`)
- Committed: Yes
- Convention: `{sequence}.{plan-name}.md`, e.g., `1.auth-setup.md`, `2.document-ingestion.md`
- Note: Each plan should include complexity indicator and validation tests

---

*Structure analysis: 2026-04-28*
