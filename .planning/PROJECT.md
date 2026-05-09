# Agentic RAG Application — Episode 2

## What This Is

A multi-tenant RAG (Retrieval-Augmented Generation) web application where users upload documents, organize them, and chat with an LLM that retrieves relevant context from their knowledge base. Episode 2 transforms the flat per-user document store into a Claude-Code-style explorable knowledge base — with a nested folder tree, two scopes (private per-user + shared global), and a new family of agentic exploration tools (tree, glob, grep, list_files, read_document, plus an Explorer sub-agent) that the LLM uses for precise, navigable retrieval alongside semantic search.

## Core Value

The agent can locate the *right* piece of information in a large, organized knowledge base — using semantic search when meaning matters and codebase-style traversal (tree/glob/grep/read) when precision matters — without hallucinating across unrelated material.

## Requirements

### Validated

<!-- Inferred from Episode 1 codebase. Locked unless explicitly revisited. -->

- ✓ User authentication via Supabase Auth (JWT) — Episode 1
- ✓ Per-user thread CRUD with RLS isolation — Episode 1
- ✓ SSE-streamed chat responses (Gemini native SDK) — Episode 1
- ✓ Document upload + ingestion pipeline (Docling for 17+ formats incl. OCR) — Episode 1
- ✓ Chunking, embeddings (gemini-embedding-001 @ 768 dims), pgvector retrieval — Episode 1
- ✓ Hybrid search (vector + tsvector RRF) with optional Cohere/Gemini reranking — Episode 1
- ✓ Record Manager dedup (skip/update/create on re-upload) — Episode 1
- ✓ Metadata extraction with admin-configurable schema, filtered retrieval — Episode 1
- ✓ Multi-tool dispatch: `search_documents`, `query_structured_data` (DuckDB text-to-SQL), `web_search` (Tavily), `analyze_document` (sub-agent) — Episode 1
- ✓ Admin settings UI (model selection, retrieval config, tool toggles, metadata schema) — Episode 1
- ✓ LangSmith tracing across LLM + tool calls — Episode 1
- ✓ Polling-based ingestion status updates in UI — Episode 1
- ✓ Light/dark theme, markdown rendering, stop-streaming controls — Episode 1

### Active

<!-- Episode 2 — Agentic Knowledge Base Exploration. Hypotheses until shipped. -->

- [x] Two-scope knowledge base: per-user private folders + admin-managed global folders, both visible in a unified explorer — validated in Phase 03 (folder CRUD endpoints + admin gate)
- [x] Path-based folder model: `documents.folder_path` TEXT + thin `folders` table for empty/named folders — validated in Phase 01 schema + Phase 03 service layer
- [x] Canonical full-document markdown stored alongside chunks (`documents.content_markdown`) for grep/read tools — validated in Phase 02 backfill
- [ ] File explorer UI with two top-level sections ("Shared" + "My Files"), expandable tree, breadcrumbs, folder CRUD, upload-into-folder, drag-move documents, rename document
- [x] `tree` tool — returns nested folder structure with `path` + `max_depth` args, count-summary at deeper levels, hard token-budget truncation note — validated in Phase 04 (iterative-BFS, 500-entry budget, max_depth Pydantic-clamped to ≤4)
- [x] `glob` tool — file-pattern matching against `folder_path` + `file_name` (e.g. `**/*.pdf`, `projects/**/floor-plans/*`) — validated in Phase 04 (pure-Python glob→regex, PostgREST .like()/.match() pushes filter to DB)
- [x] `grep` tool — regex/keyword search across `documents.content_markdown` with `path` scope filter — validated in Phase 04 (Migration 020 grep_documents RPC + 5s statement_timeout + ILIKE pre-filter + pathological-regex Python blocklist; median 213ms p95 < 500ms)
- [x] `list_files` tool — list files (and subfolders) within a given folder path — validated in Phase 04 (delegates to folder_service.list_folder, folders-then-files-alpha ordering)
- [x] `read_document` tool — line-numbered slice of `content_markdown` with `offset`/`limit` args, newline-boundary clamping — validated in Phase 04 (arrow-form rendering, splitlines for CRLF/LF/CR stability, UTF-8 codepoint-safe truncation)
- [ ] `explore_knowledge_base` sub-agent — isolated-context investigator that combines tree/glob/grep/read for multi-step exploration, returns compact summary
- [x] `search_documents` tool extended with `folder_path` prefix filter so the LLM can scope vector search when useful — validated in Phase 04 (Migration 020 extends both match_document_chunks_with_filters and _hybrid with NULL-default tail params; backwards-compat preserved bit-for-bit)
- [x] Tools default to searching `global ∪ user` scopes, with optional `scope` arg ('user' | 'global' | 'both') for narrowing — validated in Phase 04 (every tool's GrepArgs/TreeArgs/etc. has Literal['user','global','both'] scope field; ensure_scope_tag enforces every result row carries scope)
- [ ] Admin-only writes to global tree (reuses existing admin role); all authenticated users can read global
- [ ] Backfill migration: existing Episode 1 documents land at root `/`
- [ ] LangSmith traces for new tools + Explorer sub-agent

### Out of Scope

<!-- Explicit boundaries with reasoning to prevent re-adding. -->

- **Local folder mount/sync** — programmatic load of a local directory tree into the KB. Deferred to a future phase; would overcomplicate this scope and force decisions about file watching, conflict resolution, and binary-format ingestion ergonomics.
- **Folder-level permissions / sharing between users** — only two scopes exist (private, global). No per-folder ACLs, no sharing private folders with specific other users.
- **Symlinks / cross-folder document references** — each document lives in exactly one folder. No appearing-in-multiple-places.
- **Versioning / audit history of folder changes** — no log of moves, renames, or deletes. Record Manager already handles content-version dedup at ingest time.
- **Folder-scoped retrieval as a visible UI filter dropdown** — the existing metadata filter bar (Module 4) stays focused on content classification (topic, type, etc.). Folder scoping is exposed only through the LLM's `folder_path` filter on `search_documents` and through the precision tools.
- **Multi-select + bulk move/delete in the explorer UI** — single-item operations only this phase. Bulk ops can come later.
- **Connectors / automated ingestion pipelines** — manual file upload only, per existing project rule.

## Context

**Codebase state (entering Episode 2):**

- Episode 1 (Modules 1–8 + improvements) is complete and tagged `Episode-1-Complete` on the upstream repo
- This repo is the Episode 2 fork; master is at `ba2d771` ("docs: map existing codebase")
- Codebase map exists at `.planning/codebase/` (ARCHITECTURE, STACK, STRUCTURE, CONCERNS, CONVENTIONS, INTEGRATIONS, TESTING)
- Backend: 119 tests across 11 modules (`backend/scripts/test_all.py`)
- Frontend: 26 Playwright e2e tests (`frontend/e2e/full-suite.spec.ts`)

**Why this shape for Episode 2:**

- The Episode 1 retrieval debugging session (commit `53ff28d`) revealed that pure vector + hybrid search on chunked content struggles with precision: the LLM hallucinated the wrong panel/floor when chunks lost identifying context (mid-table HTML splits, etc.). Adding codebase-style precision tools (tree/glob/grep/read) gives the agent a recourse beyond vector recall — it can navigate to the right document by name/path/pattern when it knows what it's looking for.
- Claude Code's exploration UX is a proven pattern for agent navigation over a corpus. Replicating it inside a managed Supabase-backed knowledge base (rather than a local filesystem) is the central technical challenge.
- The two-scope model (private + global) reflects a real multi-tenant need: each user has their own private files, but a shared knowledge base — curated by admins — is also valuable.

**Carryover technical debt to keep in mind (not blocking Episode 2):**

- Header-aware table chunking and HTML→markdown normalization at ingest time were deferred from the Episode 1 retrieval-debugging session. Adding `content_markdown` opens a path to address these later (we now have a place to store cleaned, normalized full-document text).

## Constraints

- **Tech stack**: React + Vite + TypeScript + Tailwind v4 + shadcn/ui (frontend); Python + FastAPI + sse-starlette (backend); Supabase Postgres + pgvector + Auth + Storage; Google Gemini (native `google-genai` SDK); Docling for parsing; LangSmith for tracing — locked, established by Episode 1.
- **No LangChain / LangGraph** — raw SDK calls only. Pydantic for structured LLM outputs. Per existing CLAUDE.md project rule.
- **Row-Level Security on every table** — users only see their own data; global-scope rows must be readable by all authenticated users but writable only by admins. Episode 1 RLS pattern must be preserved.
- **Polling for ingestion status, SSE for chat** — no Realtime subscriptions for status (Episode 1 fix). New tools must integrate with the existing SSE event stream for sub-agent traces.
- **Manual file upload only** — no connectors, no automated pipelines.
- **Python backend uses `venv`** — per project rule.
- **Stateless completions** — chat history is loaded from `messages` table and sent as context per request. New tools must respect this; nothing stored mid-turn outside the request lifecycle.
- **Dependencies**: existing tables (`documents`, `document_chunks`, `messages`, `threads`, `profiles`, `global_settings`, `structured_data`) and their RPCs (`match_document_chunks`, `match_document_chunks_hybrid`) — extensions must be backwards-compatible since data exists.
- **Token budget**: tree/grep tool outputs cannot blow the LLM context window — explicit truncation, depth limits, and count summaries are required.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Path-based folder model on documents (`folder_path` TEXT) plus a thin `folders` table for empty-folder tracking | Avoids recursive CTEs and closure tables; documents stay simple to query; empty folders need only a tiny side table | — Pending |
| Add `documents.content_markdown` column rather than reconstructing from chunks on demand | grep/read tools need fast, deterministic access to canonical text; reconstructing has overlap-dedup edge cases; storage cost is small for text | — Pending |
| Two scopes (`user` private + `global` shared, admin-only writes) instead of per-user-only | Reflects real multi-tenant pattern; reuses existing admin role; tools default to union of both with optional scope override | — Pending |
| Explorer presented as two top-level sections ("Shared" + "My Files") rather than merged tree | Avoids path-collision ambiguity; clearer mental model; visual differentiation for the two scopes | — Pending |
| Tools default to `scope='both'` with override arg (rather than user-only or two parallel tool sets) | Matches the user mental model that "my knowledge base" includes shared; LLM gets one tool surface; can narrow when needed | — Pending |
| `search_documents` extended with `folder_path` filter (LLM auto-filter), but folder NOT added as a visible UI metadata-filter dropdown | Keeps UI filter bar focused on content classification; LLM can still self-scope when context warrants it; precision tools (grep/glob/tree) are the primary folder-scoping surface | — Pending |
| `read_document` does line-numbered slicing with offset/limit; existing `analyze_document` sub-agent stays for full-document isolated analysis | Clean division of labor: precise reads for the main agent, full-document analysis stays in isolated context where appropriate | — Pending |
| Explorer triggered as an LLM tool call (`explore_knowledge_base`), not always-on or user-toggled | LLM agency over when delegation is worth it; mirrors Claude Code's Explore subagent pattern; predictable cost profile | — Pending |
| Existing Episode 1 documents migrate to root `/` (not `/imported`, not wiped) | Simplest backfill; keeps user data; folder organization is a user task post-migration | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-05-09 after Phase 04 (five exploration tools + search_documents extension) completion*
