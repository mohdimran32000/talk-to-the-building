# Technology Stack

**Analysis Date:** 2026-04-28

## Languages

**Primary:**
- TypeScript 5.9.3 - Frontend React components and configuration
- Python 3.x - Backend API and services

**Secondary:**
- JavaScript - Build tooling and configuration
- HTML/CSS - Frontend markup and styling

## Runtime

**Environment:**
- Node.js (frontend build and dev server)
- Python 3.x with venv virtual environment (required per CLAUDE.md)

**Package Manager:**
- npm (frontend) - Pinned versions in `frontend/package.json`
- pip (backend) - Requirements specified in `backend/requirements.txt`
- Lockfile: `frontend/package-lock.json` present, backend uses direct requirements file

## Frameworks

**Core:**
- React 19.2.0 - Frontend UI library (`frontend/package.json`)
- FastAPI - Backend REST API framework (`backend/requirements.txt`)
- Vite 7.3.1 - Frontend build tool and dev server (`frontend/package.json`)

**UI Components:**
- shadcn/ui - Radix UI component library (via `radix-ui` 1.4.3, `class-variance-authority` 0.7.1)
- Tailwind CSS 4.1.18 - Utility-first CSS framework (`frontend/package.json`)
- @tailwindcss/vite 4.1.18 - Vite integration for Tailwind
- Lucide React 0.563.0 - Icon library

**Routing:**
- react-router-dom 7.13.0 - Client-side routing (`frontend/package.json`)

**Styling:**
- tailwind-merge 3.4.0 - Tailwind class merging utility
- next-themes 0.4.6 - Theme management (light/dark mode)

**Content Rendering:**
- react-markdown 10.1.0 - Markdown rendering in React
- remark-gfm 4.0.1 - GitHub Flavored Markdown support

**UI Notifications:**
- sonner 2.0.7 - Toast notification library

**Database Client:**
- @supabase/supabase-js 2.95.3 - Supabase client library (`frontend/package.json`)
- supabase (Python) - Supabase client for backend (`backend/requirements.txt`)

**Backend HTTP:**
- uvicorn[standard] - ASGI server for FastAPI (`backend/requirements.txt`)
- sse-starlette - Server-Sent Events support (`backend/requirements.txt`)

**Python Core:**
- python-dotenv - Environment variable loading (`backend/requirements.txt`)
- pydantic - Data validation and serialization (`backend/requirements.txt`)
- python-multipart - Multipart form parsing for file uploads (`backend/requirements.txt`)

## Testing & Development

**Frontend Testing:**
- @playwright/test 1.58.2 - E2E testing framework

**Frontend Linting:**
- ESLint 9.39.1 - JavaScript/TypeScript linting
- @eslint/js 9.39.1 - ESLint recommended rules
- typescript-eslint 8.48.0 - TypeScript ESLint support
- eslint-plugin-react-hooks 7.0.1 - React Hooks linting
- eslint-plugin-react-refresh 0.4.24 - React Fast Refresh linting

**Frontend Build:**
- @vitejs/plugin-react 5.1.1 - React plugin for Vite
- TypeScript ~5.9.3 - TypeScript compiler

**UI Development:**
- shadcn 3.8.4 - shadcn/ui CLI tool
- tw-animate-css 1.4.0 - Tailwind animation utilities

**Frontend Utilities:**
- @types/node 24.10.1 - Node.js types
- @types/react 19.2.7 - React type definitions
- @types/react-dom 19.2.3 - React DOM type definitions
- globals 16.5.0 - Global variable definitions

## Key Dependencies

**Critical:**
- google-genai - Google Gemini API SDK for LLM and embeddings (`backend/requirements.txt`)
- docling - Document parsing library (17+ format types) (`backend/requirements.txt`)
- supabase - Database, auth, storage, realtime (`frontend/package.json`, `backend/requirements.txt`)
- FastAPI - Web framework for REST API

**Infrastructure:**
- langsmith - Observability and tracing (`backend/requirements.txt`)
- cohere - Reranking provider (optional, for document relevance scoring) (`backend/requirements.txt`)
- tavily-python - Web search API client (`backend/requirements.txt`)
- duckdb - In-memory SQL engine for structured data queries (`backend/requirements.txt`)

## Configuration

**Environment:**
- Frontend: `.env.example` in `frontend/` requires `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`
- Backend: `.env.example` in `backend/` requires:
  - `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`
  - `GEMINI_API_KEY`
  - `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, `LANGSMITH_TRACING`

**Build:**
- Frontend: `vite.config.ts` at `frontend/vite.config.ts` - Configures React plugin, Tailwind, path aliases, dev proxy
- Frontend: `tsconfig.json` at `frontend/tsconfig.json` - TypeScript compilation settings
- Frontend: `eslint.config.js` at `frontend/eslint.config.js` - ESLint configuration

**Backend:**
- Entry point: `backend/app/main.py` - FastAPI app initialization
- CORS configuration: Allows localhost:5173 and localhost:5174

## Platform Requirements

**Development:**
- Node.js (for frontend build)
- Python 3.x with venv virtual environment
- git (for version control)

**Production:**
- Deployment target: Cloud-based (Supabase for database, Google Gemini API)
- No specific OS requirements documented

---

*Stack analysis: 2026-04-28*
