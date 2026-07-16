# CLAUDE.md

RAG app with chat (default) and document ingestion interfaces. Config via admin settings UI.

## Stack
- Frontend: React + Vite + Tailwind + shadcn/ui
- Backend: Python + FastAPI
- Database: Supabase (Postgres, pgvector, Auth, Storage, Realtime)
- LLM: Google Gemini (native google-genai SDK)
- Document Parsing: Docling (layout-aware, OCR, 17+ format types)
- Observability: LangSmith

## Rules
- Python backend must use a `venv` virtual environment
- No LangChain, no LangGraph - raw SDK calls only
- Use Pydantic for structured LLM outputs
- All tables need Row-Level Security - users only see their own data
- Stream chat responses via SSE
- Use polling (not Realtime) for ingestion status updates
- Module 2+ uses stateless completions - store and send chat history yourself
- Ingestion is manual file upload only - no connectors or automated pipelines

## Planning
- Save all plans to `.agent/plans/` folder
- Naming convention: `{sequence}.{plan-name}.md` (e.g., `1.auth-setup.md`, `2.document-ingestion.md`)
- Plans should be detailed enough to execute without ambiguity
- Each task in the plan must include at least one validation test to verify it works
- Assess complexity and single-pass feasibility - can an agent realistically complete this in one go?
- Include a complexity indicator at the top of each plan:
  - ✅ **Simple** - Single-pass executable, low risk
  - ⚠️ **Medium** - May need iteration, some complexity
  - 🔴 **Complex** - Break into sub-plans before executing

## Development Flow
1. **Plan** - Create a detailed plan and save it to `.agent/plans/`
2. **Build** - Execute the plan to implement the feature
3. **Validate** - Test and verify the implementation works correctly. Use browser testing where applicable via an appropriate MCP
4. **Iterate** - Fix any issues found during validation

## Testing
- **Backend validation suite:** `cd backend && venv/Scripts/python scripts/test_all.py` (112 tests)
  - Covers: health, auth rejection, thread CRUD, messages + SSE, file upload/ingestion, record manager dedup, RAG retrieval + memory, RLS isolation, admin settings, metadata, hybrid search, additional tools
  - Requires backend running on localhost:8001
- **Frontend Playwright suite:** `cd frontend && npx playwright test e2e/full-suite.spec.ts` (26 tests)
  - Covers: auth flow, threads, messages, documents, theme toggle, console errors
  - Requires both backend (8001) and frontend (5173) running
- **When building new features:** Add tests to the appropriate backend test module or create a new one in `backend/scripts/`. Add UI tests to `frontend/e2e/full-suite.spec.ts`. Update `backend/scripts/test_all.py` if adding a new module.
- **When to run tests:** Do NOT run the full test suite automatically. Only run tests when the user explicitly asks (e.g., "run tests", "verify", "check if anything broke"). For small/cosmetic changes (colors, text, styling), tests are unnecessary unless requested.
- **Test helpers:** `backend/scripts/test_helpers.py` provides auth, SSE parsing, polling, and cleanup utilities
- **Eval suites (LLM-driven, slow):** `backend/scripts/eval_rag_vs_truth.py` (33 cases, supports `1-11` index-range args), `eval_routing.py` (18 cases, supports case-name args like `R3 M6`), `eval_sql_breakdown.py` (6 cases). A full pass takes 25–40 min. **Run them in short foreground chunks using those CLI filters — never as one long background job** (long background runs get killed when the session idles; each chunk banks its results so an interruption loses only that chunk). After ANY backend code change, kill ALL python/uvicorn processes on port 8001 and restart (no `--reload`; zombie workers serve stale code). Playbooks: `backend/scripts/EVAL_PLAYBOOK.md` (load-schedule SQL matrix) and `DOC_QA_PLAYBOOK.md` (O&M document QA).
- **CRITICAL: Tests must NEVER delete all user data.** Tests must only clean up resources they created (tracked by ID). Never use blanket "delete all threads/files" cleanup. Never run `DELETE FROM` or `TRUNCATE` on production tables. Never write migrations with `DROP TABLE` on tables that hold user data.

## Progress
Check PROGRESS.md for current module status. Update it as you complete tasks.