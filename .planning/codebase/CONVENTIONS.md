# Coding Conventions

**Analysis Date:** 2026-04-28

## Naming Patterns

**Files:**
- Python modules: `snake_case.py` (e.g., `openai_client.py`, `test_auth.py`)
- TypeScript/React files: `PascalCase.tsx` for components, `camelCase.ts` for utilities (e.g., `MessageList.tsx`, `api.ts`)
- Test files: `test_*.py` (backend), `*.spec.ts` (frontend e2e)

**Functions:**
- Python: `snake_case()` (e.g., `get_auth_token()`, `stream_sse()`, `ingest_document()`)
- TypeScript: `camelCase()` for functions and async handlers, `PascalCase()` for React components (e.g., `fetchApi()`, `AuthProvider()`)
- Getters/utilities: Prefix-less (e.g., `getProfile()`, `getToken()`)

**Variables:**
- Python: `snake_case` for all variables and module constants (e.g., `_token_cache`, `BASE_URL`)
- TypeScript: `camelCase` for variables, `UPPER_SNAKE_CASE` for environment/imported constants (e.g., `TEST_EMAIL`, `streamingContent`)
- Private/internal: Prefix with `_` in Python (e.g., `_client_cache`, `_get_client()`)

**Types & Interfaces:**
- TypeScript: `PascalCase` interfaces (e.g., `AuthContextType`, `MessageListProps`, `Thread`, `GlobalSettings`)
- Python Pydantic models: `PascalCase` (e.g., `ThreadCreate`, `MessageResponse`, `DocumentResponse`)
- Enums: `PascalCase` class names

**Constants:**
- Python: `UPPER_SNAKE_CASE` for module-level constants (e.g., `SYSTEM_PROMPT_NO_DOCS`, `EMBEDDING_MODEL`)
- TypeScript: `UPPER_SNAKE_CASE` for compile-time constants (e.g., `TEST_EMAIL`, `TEST_PASSWORD`)

## Code Style

**Formatting:**
- Frontend: No explicit Prettier config; ESLint 9 with flat config enforces style
- Backend: No explicit formatter config; Python conventions are implicit (PEP 8-ish)
- All frontend code uses Tailwind CSS for styling (no CSS files)

**Linting:**
- **Frontend:** ESLint 9 with TypeScript plugin (`typescript-eslint`)
  - Config: `frontend/eslint.config.js`
  - Rules: Recommended settings from `@eslint/js`, `tseslint.configs.recommended`, React hooks, React refresh
  - Command: `npm run lint`
- **Backend:** No linter config detected (implicit reliance on conventions)

**Line length:**
- Frontend TypeScript: Generally follows 80-120 char lines (Tailwind className chains may exceed)
- Backend Python: Functions and comments show 80-100 char preference

**Indentation:**
- Frontend: 2 spaces (React/TypeScript files)
- Backend: 4 spaces (Python standard)

## Import Organization

**Order:**
1. Standard library imports (`os`, `sys`, `json`, `logging`)
2. Third-party packages (`fastapi`, `pydantic`, `requests`, `google.genai`)
3. Local/relative imports (`from app.auth`, `from app.services`)

**Path Aliases:**
- Frontend: `@/*` maps to `./src/` (defined in `tsconfig.app.json`)
  - Used throughout: `import { supabase } from '@/lib/supabase'`, `import ProtectedRoute from '@/components/ProtectedRoute'`
- Backend: No aliases; relative imports from `app` root

**Imports style:**
- Frontend: Named imports preferred (`import { useAuth } from '@/contexts/AuthContext'`)
- Backend: Mix of `import` and `from` statements; service modules imported by function

## Error Handling

**Backend (FastAPI):**
- **HTTP Exceptions:** Use `HTTPException(status_code=..., detail=...)` from FastAPI
  - 401/403 for auth failures: `HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")`
  - 404 for not found: `HTTPException(status_code=404, detail="Thread not found")`
  - 403 for forbidden: `HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")`
- **Logging:** Use `logger` object (from `logging.getLogger(__name__)`)
  - Info: `logger.info(f"Converted PPTX to PDF: {abs_pdf}")`
  - Warning: `logger.warning(f"Document check failed (non-fatal): {e}")`
  - Error: `logger.error(f"Stream error for thread {thread_id}: {e}", exc_info=True)`
- **Try/Finally:** Resource cleanup (e.g., `_throttled_ingest` in `files.py`)
- **Return values:** Supabase queries return `.execute().data` or `.data` with defensive checks (`if not result.data: raise HTTPException(...)`)

**Frontend (React/TypeScript):**
- **Async/Await:** Used for API calls; errors thrown and caught at call site
  - Pattern: `try { ... } catch { setError(...) }` in `AuthContext.tsx`
- **Error messages:** Displayed via Sonner toast or error state
  - `api.ts` throws detailed errors: `throw new Error(body.detail || 'Request failed: ${res.status}')`
- **Null checks:** JSX uses conditional rendering (`{loading ? ... : ...}`)
  - Example: `ProtectedRoute.tsx` checks `if (!user) return <Navigate to="/login" />`

## Logging

**Framework:**
- Backend: Python built-in `logging` module
- Frontend: No explicit logging framework; console or Sonner for errors

**Patterns:**
- Python: `logger = logging.getLogger(__name__)` at module level
- Log on startup: Model initialization, client setup
- Log on errors: Exception details with `exc_info=True` for full traceback
- Operational warnings: Non-fatal issues (e.g., "Document check failed (non-fatal)")
- Frontend: Error messages via Sonner toast or console for debugging

**Example from `messages.py`:**
```python
logger.error(f"Stream error for thread {thread_id}: {e}", exc_info=True)
```

## Comments

**When to Comment:**
- **Complex logic:** Explain *why* not what (code shows what)
  - Example from `openai_client.py`: "PPTX/PPT: convert to PDF via PowerPoint first, then process as PDF — This gives us: perfect rendering, OCR on images, and slide numbers = page numbers"
- **Non-obvious decisions:** Business rules, constraints, workarounds
  - Example from `messages.py`: "Stateless completions: load full history from DB" and "Filter out empty assistant messages (can happen if a tool call's context injection failed)"
- **Integration points:** External service calls, special handling
  - Example from `files.py`: "Record Manager: check for duplicates"

**JSDoc/TSDoc:**
- Not systematically used
- Function parameters documented inline (e.g., in interface definitions like `MessageListProps`)
- Module docstrings used in Python services (e.g., `ingestion.py`: "Ingestion service for Module 2...")

**Avoid:**
- Trivial comments restating code: `const x = 5 // set x to 5`
- Outdated comments (code evolves, comments don't)

## Function Design

**Size:**
- Prefer short functions (50-100 lines for route handlers)
- Long functions broken into helpers (e.g., `_build_search_tool()`, `_build_system_prompt()` in `openai_client.py`)
- Test functions: ~15-50 lines (one focused test per function)

**Parameters:**
- Backend route handlers: Use FastAPI `Depends()` for auth/dependency injection
  - Example: `async def create_thread(body: ThreadCreate, user_id: str = Depends(get_current_user))`
- Python service functions: Explicit parameters over globals (e.g., pass `user_id`, `supabase_client`)
- Frontend components: Props as single interface object
  - Example: `function SubAgentSection({ documentName, content, isActive, defaultExpanded = false }: {...})`

**Return Values:**
- Backend: Pydantic models returned directly (FastAPI serializes as JSON)
  - Example: `@router.post("", response_model=ThreadResponse)` returns `ThreadResponse` instance
- Frontend: Typed returns (e.g., `Promise<void>` for async functions)
- Service functions: Return data structures or tuples when multiple values needed
  - Example: `poll_document_status()` returns `(final_status, error_message)`

## Module Design

**Exports:**
- Python: No explicit `__all__` used; functions/classes are public if not prefixed with `_`
- TypeScript: Named exports preferred
  - Example: `export interface Thread { ... }`, `export async function fetchApi() { ... }`
- Default exports used for React components: `export default function App() { ... }`

**Barrel Files:**
- Frontend: Components imported directly (no index.ts re-exports in component directories)
- Backend: Routers imported in `main.py` explicitly: `from app.routers import threads, messages, files, settings`

**Module Organization:**
- **Backend:**
  - `app/routers/` — Route handlers (dependency injection layer)
  - `app/services/` — Business logic, LLM calls, external integrations
  - `app/models/schemas.py` — Pydantic request/response models
  - `app/auth.py` — Auth helpers (get_current_user, get_admin_user)
- **Frontend:**
  - `src/pages/` — Full-page components (Login, Chat, AdminSettings)
  - `src/components/` — Reusable components + UI primitives
  - `src/contexts/` — React context (AuthContext)
  - `src/lib/` — Utilities (api.ts, supabase.ts, utils.ts)

## Raw SDK Usage

**Project Rule:** No LangChain, no LangGraph — raw SDK calls only.

**Observed Patterns:**
- **Google Gemini:** Direct `google.genai` imports, raw `genai.Client()` calls
  - Example: `from google import genai` → `_client = genai.Client(api_key=key)`
- **Pydantic:** Used for ALL structured LLM outputs
  - Request/response models in `app/models/schemas.py`
  - Tool definitions built with `types.FunctionDeclaration()` (not schema builders)
- **Supabase:** Direct SDK calls (`supabase.table(...).select(...).execute()`)
  - No ORM layer; raw queries with RLS enforcement built into database

## Row-Level Security (RLS)

**Pattern:** Every database query filters by `user_id`

**Example from `threads.py`:**
```python
result = (
    supabase.table("threads")
    .select("*")
    .eq("id", thread_id)
    .eq("user_id", user_id)  # RLS filter
    .maybe_single()
    .execute()
)
```

**All routes:**
- Get `user_id` from `Depends(get_current_user)`
- Chain `.eq("user_id", user_id)` on every table query
- No bulk deletes or admin queries without explicit intent

## Streaming & Async

**Chat Streaming (SSE):**
- Backend: `sse_starlette.sse.EventSourceResponse` with generator
- Events: JSON objects with `type` field (`"token"`, `"done"`, `"tool_thinking"`, etc.)
- Frontend: Fetch with `stream: true`, parse `SSE` format (lines starting with `"data:"`)

**Polling (Ingestion Status):**
- No Realtime subscriptions; client polls GET `/api/files` on 2-second intervals
- Loop until status in `("ready", "failed")` or timeout
- Helper: `poll_document_status(token, doc_id, target="ready", max_wait=30)` in `test_helpers.py`

**Stateless Completions:**
- Chat state NOT stored server-side per session
- Client loads full message history from DB before each request
- LLM sees `messages` array built from history (user + assistant pairs)
- Avoids token/context accumulation bugs

---

*Convention analysis: 2026-04-28*
