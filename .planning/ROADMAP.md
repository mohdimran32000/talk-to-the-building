# Roadmap: Agentic RAG Application — Episode 2

**Created:** 2026-05-01
**Granularity:** standard (5–8 phases) → **6 phases**
**Coverage:** 55/55 v1 requirements mapped
**Critical path:** Phase 1 (Schema) → Phase 2 (Backfill) → Phase 4 (Tools) → Phase 5 (Explorer)

## Overview

Episode 2 transforms the existing flat per-user document store into a Claude-Code-style explorable knowledge base — adding nested folders, a second admin-curated "global" scope, canonical full-document markdown, five precision tools (`tree`, `glob`, `grep`, `list_files`, `read_document`), and an isolated `explore_knowledge_base` sub-agent. The work flows along a strict early sequence (Schema must land first; `content_markdown` backfill must operate before any tool that reads it ships) and then parallelizes aggressively: folder service, the five precision tools, and the file-explorer UI can all be built concurrently once the schema and API contracts are locked. The Explorer sub-agent depends on the precision tools and is paired with the generalization of the SSE sub-agent event protocol so we don't pay forever for a bolted-on second protocol.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Schema Foundation + Two-Scope RLS + Path Normalizer** — Five small migrations (012–016) introduce `folder_path`, `scope`, `content_markdown`, the thin `folders` table, two-scope RLS, `pg_trgm` indexes, and the canonical `normalize_path()` helper. ✅ 2026-05-04
- [ ] **Phase 2: content_markdown Backfill (Gated)** — Re-run Docling against existing Storage blobs to populate `documents.content_markdown`; surface re-index status; gate `grep`/`read_document` until operational.
- [ ] **Phase 3: Folder Service + Routers + Dedup Extension** — Pure CRUD layer: `folder_service.py`, `folders` router, extended `files` router (upload-into-folder, rename, move), `record_manager` dedup key extended.
- [ ] **Phase 4: Five Exploration Tools + search_documents Extension** — `tree`, `glob`, `grep`, `list_files`, `read_document` with Pydantic v2 arg validation, hard token-budget caps, scope-tagged result rows; `search_documents` extended with `folder_path`/`scope` filters.
- [ ] **Phase 5: Explorer Sub-Agent + SSE Protocol Generalization** — `run_explorer_sub_agent` with `MAX_TURNS=8`, wall-clock timeout, no-progress detector; SSE sub-agent event protocol generalized; `messages.tool_metadata` persistence.
- [ ] **Phase 6: File-Explorer UI Cluster** — `FileExplorerPanel` cluster (two-section tree, folder CRUD, drag-move, breadcrumbs, scope badges, Explorer activity card); replaces `FileUploadPanel`; Playwright e2e additions.

## Phase Details

### Phase 1: Schema Foundation + Two-Scope RLS + Path Normalizer
**Goal**: Every downstream phase has the columns, indexes, RLS policies, and path-canonicalization chokepoint it needs — and the highest-rank pitfalls (RLS scope-leak, grep perf collapse, path drift, concurrent-upload race) are designed out of the data model up front.
**Depends on**: Nothing (foundation phase)
**Requirements**: SCHEMA-01, SCHEMA-02, SCHEMA-03, SCHEMA-04, SCHEMA-05, RLS-01, RLS-02, RLS-03, RLS-04, FOLDER-01, TEST-04
**Success Criteria** (what must be TRUE):
  1. Every table touched by Episode 2 (`documents`, `document_chunks`, `folders`) enforces the two-scope union read predicate `((scope='user' AND user_id=(SELECT auth.uid())) OR scope='global')`, with separate INSERT/UPDATE policies per scope and `WITH CHECK (scope = OLD.scope)` forbidding scope mutation entirely.
  2. A non-admin user attempting `INSERT ... scope='global'` or `UPDATE ... SET scope='global'` is rejected by RLS; the cross-user × cross-scope SELECT/INSERT/UPDATE/DELETE matrix in `test_two_scope_rls.py` passes 100%.
  3. `pg_trgm` is enabled and `EXPLAIN ANALYZE` on a representative `grep`-shape query against `documents.content_markdown` shows `Bitmap Index Scan`, not `Seq Scan`; `text_pattern_ops` btree on `folder_path` accelerates `LIKE 'prefix%'` queries.
  4. Existing Episode 1 documents are queryable at `folder_path='/'`, `scope='user'` immediately after migrations land, with no manual data movement; the canonical-form CHECK constraint rejects `INSERT ... folder_path='projects/'` (trailing slash) and `INSERT ... folder_path='projects'` (no leading slash).
  5. A single Python `normalize_path()` helper exists in `app/services/folder_service.py` and is the only chokepoint for path canonicalization; round-trip tests confirm `'/'`, `'/a/b'`, and `'/a/b/c'` survive through every write path unchanged.
**Plans**: 8 plans
- [x] 01-PLAN.md — normalize_path() pure-function helper in folder_service.py (FOLDER-01) ✅ 2026-05-03
- [x] 02-PLAN.md — Migration 012: folder_path + scope columns + CHECK coupling + scope-aware unique index + pg_trgm extension (SCHEMA-01, SCHEMA-02) ✅ 2026-05-03
- [x] 03-PLAN.md — Migration 013: folders table + COALESCE-based unique expression index + RLS-enable (SCHEMA-04) ✅ 2026-05-03
- [x] 04-PLAN.md — Migration 014: content_markdown column + status enum + backfill-scan partial index (SCHEMA-03) ✅ 2026-05-03
- [x] 05-PLAN.md — Migration 015: two-scope RLS policies (19 total) + is_admin() helper + forbid_scope_mutation() trigger (RLS-01, RLS-02, RLS-03) ✅ 2026-05-03
- [x] 06-PLAN.md — Migration 016: search-acceleration indexes (gin_trgm_ops + text_pattern_ops) (SCHEMA-05) ✅ 2026-05-03
- [x] 07-PLAN.md — [BLOCKING] Apply migrations 012-016 via run_migrations.py + structural verify ✅ 2026-05-04
- [x] 08-PLAN.md — test_two_scope_rls.py: cross-user × cross-scope RLS matrix (49 assertions; passed 49/0); register in test_all.py (RLS-04, TEST-04) ✅ 2026-05-04
**Threats / pitfalls**: Pitfall 1 (RLS scope-leak — RANK 1: separate INSERT/UPDATE per scope, `WITH CHECK (scope = OLD.scope)`, CHECK coupling scope/user_id, defense in depth via app-layer `.eq('scope',...)`); Pitfall 3 (grep perf — pg_trgm GIN + `text_pattern_ops` btree both land here); Pitfall 4 (path normalization drift — DB CHECK regex `^/$|^/[^/]+(/[^/]+)*$` + single Python helper); Pitfall 10 (concurrent upload race — unique constraint `(scope, COALESCE(user_id,'00..0'), path)` on `folders`).

### Phase 2: content_markdown Backfill (Gated)
**Goal**: Every existing Episode 1 document has canonical full markdown stored in `documents.content_markdown` so `grep` and `read_document` can ship without being half-broken on the corpus that matters most.
**Depends on**: Phase 1
**Requirements**: BACKFILL-01, BACKFILL-02, BACKFILL-03, BACKFILL-04
**Success Criteria** (what must be TRUE):
  1. Every new document upload writes Docling's markdown export to `documents.content_markdown` synchronously inside `ingest_document()` — no follow-up job required.
  2. `backend/scripts/backfill_content_markdown.py` re-runs Docling against the original Storage blob for every Episode 1 document with NULL `content_markdown`, populating it with canonical markdown (NOT stitched-from-chunks); the script is idempotent, throttled via the existing `_ingestion_semaphore`, and logs success/failure/missing-blob counts.
  3. Documents whose source blob is missing from Storage are explicitly marked `content_markdown_status = 'requires_user_reupload'` and surfaced in tool results (not silently skipped); `grep` and `read_document` return `status: 'pending_reindex'` rather than empty matches when they encounter such rows.
  4. Spot-checking 10 random backfilled documents shows their `content_markdown` is byte-equivalent (±20 chars) to a fresh Docling export of the same blob — no overlap duplication, no chunk stitching artifacts.
**Plans**: 4 plans
- [x] 01-PLAN.md — Storage upload at upload-time + Migration 018 storage.objects RLS (Storage Gap closure) ✅ 2026-05-06
- [x] 02-PLAN.md — Synchronous content_markdown write inside ingest_document() + docling==2.91.0 pin (BACKFILL-01) ✅ 2026-05-06
- [ ] 03-PLAN.md — backfill_content_markdown.py CLI (BACKFILL-02 + BACKFILL-04, --dry-run / --limit / --document-id / --purge-orphans)
- [ ] 04-PLAN.md — test_backfill.py integration suite + register in test_all.py (BACKFILL-03 verifier + Phase 2 SC4 byte-equivalence)
**Threats / pitfalls**: Pitfall 6 (content_markdown backfill done wrong — RANK 2: re-run Docling, NEVER `string_agg` from chunks; the 50-word chunk overlap silently breaks grep line numbers); operational risk that Storage blobs may be GC'd → `requires_user_reupload` fallback is non-negotiable.

### Phase 3: Folder Service + Routers + Dedup Extension
**Goal**: Users can create, rename, delete, and move folders/documents through HTTP endpoints with admin-gated writes for global scope, transactional folder rename, and concurrent-upload safety.
**Depends on**: Phase 1
**Requirements**: FOLDER-02, FOLDER-03, FOLDER-04, FOLDER-05, FOLDER-06, FOLDER-07, TEST-01
**Success Criteria** (what must be TRUE):
  1. `POST /api/folders`, `PATCH /api/folders/{id}`, `DELETE /api/folders/{id}`, and `GET /api/folders` work end-to-end with admin gate enforced for `scope='global'` writes (reuses Episode 1's `get_admin_user` dependency); a non-admin attempting a global write gets 403.
  2. Folder rename atomically updates both `documents.folder_path` (every descendant) AND `folders.path` via a single Supabase RPC; a simulated mid-rename crash leaves no partial state (verified by transactional rollback test).
  3. `DELETE /api/folders/{id}` on a non-empty folder returns a structured `{error: "FOLDER_NOT_EMPTY", document_count, subfolder_count}` instead of cascading; `test_folders.py` confirms no documents are deleted on rejected calls.
  4. `record_manager` dedup key is `(scope, user_id, folder_path, file_name, hash)` — uploading the same file to two different folders succeeds (creates two rows); uploading the same file to the same folder is deduped.
  5. `POST /api/files/upload` accepts `folder_path` and `scope` query args; `PATCH /api/files/{id}` supports rename and folder move; concurrent-upload-no-orphan test (10 parallel uploads to a brand-new path) produces exactly one (or zero) `folders` row.
**Plans**: TBD
**Threats / pitfalls**: Pitfall 5 (folder deletion orphans/cascade: empty-only delete + structured error + transactional check-and-delete); Pitfall 10 (concurrent upload race: paired with Phase 1 unique constraint, app uses `INSERT ... ON CONFLICT DO NOTHING` or no-write-on-upload model); Pitfall 4 (path drift: every router endpoint runs `folder_path` through `normalize_path()` before any DB write).

### Phase 4: Five Exploration Tools + search_documents Extension
**Goal**: The main agent can call `tree`, `glob`, `grep`, `list_files`, and `read_document` with hard token-budget discipline, scope-tagged results, and Pydantic-validated args; `search_documents` accepts optional `folder_path`/`scope` for LLM-driven scope narrowing.
**Depends on**: Phase 1 (schema + indexes), Phase 2 (content_markdown for grep/read), Phase 3 (folder service patterns + dedup)
**Requirements**: TOOL-01, TOOL-02, TOOL-03, TOOL-04, TOOL-05, TOOL-06, TOOL-07, TOOL-08, TOOL-09, TOOL-10, SEARCH-01, SEARCH-02, SEARCH-03, TEST-02
**Success Criteria** (what must be TRUE):
  1. All five tools (`tree`, `glob`, `grep`, `list_files`, `read_document`) are registered in `_build_tools()`, dispatched as additive `elif` arms in `stream_response()`, validated via per-tool Pydantic v2 `BaseModel` (with `Literal["user","global","both"]` for scope and `Field(..., ge=, le=)` for numeric bounds), and routed through Episode 1's layered-fallback empty-response wrapper.
  2. Token-budget discipline holds under adversarial fixtures: `tree` against 200 folders stays under 12K chars with `[N more folders, M more docs]` summaries; `grep` returns at most 50 hits with ±2 line context; `read_document` honors 1-based offset, default `limit=2000`, hard cap 5000, arrow-form `{n}→{content}` output, and is byte-stable on Windows-CRLF/Unicode/single-long-line/mixed-ending fixtures.
  3. Every result row from every tool carries `scope: 'user' | 'global'` (no exceptions); `EXPLAIN ANALYZE` on the grep query shows `Bitmap Index Scan on documents_content_trgm_idx`, and a 5000-doc fixture grep runs in < 500ms p95.
  4. `search_documents` accepts optional `folder_path` (prefix filter) and `scope` parameters; both default to existing behavior (no narrowing) when omitted; `match_document_chunks_with_filters` and `match_document_chunks_hybrid` RPCs accept `match_folder_path TEXT DEFAULT NULL` and `match_scope TEXT DEFAULT NULL`; existing call sites are unaffected.
  5. LangSmith `@traceable(run_type="tool")` is on every new tool function; an adversarial 50K-char tool result reproduces no empty-response failure (the SQL-tool empty-response bug regression test passes for every new tool).
**Plans**: TBD
**Threats / pitfalls**: Pitfall 2 (`tree` context blow-up — RANK 4: server-side `max_depth` cap, hard 500-entry cap with truncation marker); Pitfall 3 (grep perf: index from Phase 1 + ILIKE pre-filter + `LATERAL regexp_split_to_table` line-split + `SET LOCAL statement_timeout = '5s'`); Pitfall 8 (Gemini empty-response — RANK 3: layered-fallback wrapper from `openai_client.py:567` post-`53ff28d` is the ONLY context-injection path); Pitfall 9 (`read_document` line drift: CRLF normalized at ingestion, `splitlines(keepends=False)` consistently, 1-based offsets, UTF-8 codepoint-safe last-line truncation); Pitfall 11 (scope confusion: every tool result row carries `scope` field; system prompt instructs LLM to disambiguate).

### Phase 5: Explorer Sub-Agent + SSE Protocol Generalization
**Goal**: The main agent can delegate open-ended exploration to `explore_knowledge_base`, an isolated-context sub-agent that composes the five precision tools under hard turn/timeout/no-progress bounds; the SSE sub-agent event protocol is generalized once (now) instead of bolted on (later).
**Depends on**: Phase 4 (Explorer composes the five precision tools)
**Requirements**: EXPLORER-01, EXPLORER-02, EXPLORER-03, EXPLORER-04, EXPLORER-05, EXPLORER-06, TEST-03
**Success Criteria** (what must be TRUE):
  1. `run_explorer_sub_agent()` runs a `for turn in range(MAX_TURNS=8):` bounded loop with a 60s wall-clock timeout and a no-progress detector (tool-name+args-hash repeat → short-circuit); a deliberately broad fixture query never exceeds 8 tool calls in LangSmith trace.
  2. `analyze_document` is hard-excluded from Explorer's toolset (no recursive sub-agents); attempting to register it raises a setup-time error.
  3. The SSE event protocol is generalized to `{type: 'sub_agent', agent_name, event, payload}` with new `sub_agent_tool_start`/`sub_agent_tool_done` events forwarded by `messages.py:event_generator`; both `analyze_document` and `explore_knowledge_base` flows render correctly in the same conversation, and `messages.tool_metadata` JSONB persists Explorer traces so old chats render correctly on reload.
  4. LangSmith shows Explorer as a `chain` span with its tool calls as nested children (not flat siblings); a CI assertion confirms Explorer spans never exceed 8 tool-call children and tool-result size stays under 12K chars.
**Plans**: TBD
**Threats / pitfalls**: Pitfall 7 (Explorer infinite-loop — RANK 5: hard `for`-loop bound, wall-clock timeout, no-progress detector, aggressive in-sub-agent result truncation, system prompt states budget); Pitfall 12 (SSE protocol fork: generalize event payload at the second sub-agent — pay the small cost now, not the larger cost later; emit both old and new event names for one release if frontend backwards-compat matters); Pitfall 8 (empty-response: Explorer's compact summary still flows through layered-fallback wrapper).

### Phase 6: File-Explorer UI Cluster
**Goal**: Users see and manipulate the two-scope knowledge base through a file-explorer panel that replaces the flat `FileUploadPanel`, with folder CRUD, drag-move, scope badges, breadcrumbs, content_markdown re-index status, and an inline rendering of Explorer sub-agent activity.
**Depends on**: Phase 3 (folder API endpoints), Phase 5 (Explorer SSE event protocol)
**Requirements**: UI-01, UI-02, UI-03, UI-04, UI-05, UI-06, UI-07, UI-08, UI-09, UI-10, UI-11, TEST-05
**Success Criteria** (what must be TRUE):
  1. `FileExplorerPanel.tsx` replaces `FileUploadPanel.tsx` in `Chat.tsx` and renders two simultaneous top-level sections ("Shared" + "My Files", not tabs) with recursive expand/collapse, open-folder state persisted to `localStorage` per user, breadcrumbs in the details pane, and visual scope differentiation (badges + distinct iconography).
  2. Folder CRUD works via right-click `ContextMenu` (Create / Rename / Delete with confirm) and inline buttons; non-empty-folder delete shows the document/subfolder count from the Phase 3 structured error; admin-only affordances for `scope='global'` writes are visible only when `isAdmin === true`.
  3. Single-document drag-move works with a shadcn-style drop indicator (horizontal line for "between", folder-highlight for "into"); cross-scope moves trigger a confirmation modal; document rename works in place; uploads default into the current folder context with a fallback to root.
  4. `MessageList` `SubAgentSection` is extended (recursively, not forked) to render `sub_agent_tool_start`/`sub_agent_tool_done` events as nested rows under the parent Explorer card, with per-tool icons (folder, file, magnifying glass, eye); reloading an old chat that used Explorer shows the nested trace correctly from `messages.tool_metadata`.
  5. Keyboard navigation works (arrow keys for tree expand/collapse, matching VS Code/Finder conventions); Playwright additions in `e2e/full-suite.spec.ts` cover folder tree navigation, drag-move, sub-agent activity card, and scope visibility (admin can write global, regular user cannot).
**Plans**: TBD
**UI hint**: yes
**Threats / pitfalls**: Pitfall 11 (scope confusion: scope badges + distinct visual treatment for shared vs private; cross-scope move confirmation modal); Pitfall 5 (folder delete UX: confirmation modal shows actual document/subfolder count from server's structured error, not a guessed number); Pitfall 12 (UI rendering: `SubAgentSection` extended recursively — same component renders both `analyze_document` and Explorer; no `if (agentType === 'explorer')` branch).

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Schema Foundation + Two-Scope RLS + Path Normalizer | 8/8 | Complete | 2026-05-04 |
| 2. content_markdown Backfill (Gated) | 2/4 | In progress | - |
| 3. Folder Service + Routers + Dedup Extension | 0/TBD | Not started | - |
| 4. Five Exploration Tools + search_documents Extension | 0/TBD | Not started | - |
| 5. Explorer Sub-Agent + SSE Protocol Generalization | 0/TBD | Not started | - |
| 6. File-Explorer UI Cluster | 0/TBD | Not started | - |

## Critical Path & Parallelization

**Critical path** (each blocks the next):
1. Phase 1 (Schema) blocks everything — columns, RLS, indexes, normalize_path helper
2. Phase 2 (Backfill) blocks Phase 4's `grep` and `read_document` — cannot ship those tools against half-NULL `content_markdown`
3. Phase 4 (Tools) blocks Phase 5 (Explorer) — Explorer composes the five precision tools
4. Phase 5 (Explorer + SSE generalization) blocks Phase 6 (UI rendering of Explorer trace)

**Parallel-safe pairs** (once Phase 1 lands):
- Phase 2 (Backfill running in background) || Phase 3 (Folder service) || Phase 4 stub of `tree`/`glob`/`list_files` (don't read content_markdown)
- Phase 3 || Phase 4 — share the schema, no inter-dependencies
- Phase 6 (UI) parallel-safe with Phases 3–5 once API contracts are locked; build against stub endpoints behind a feature flag

**Build order within Phase 4** (per ARCHITECTURE.md): `list_files` (simplest) → `tree` → `glob` → `read_document` → `grep` (most complex). Shared Pydantic-validation module lands first.

## Watch out for

**Rank-1 pitfall: Two-scope RLS scope-leak.** A naive RLS policy lets a non-admin user `INSERT ... scope='global'` or `UPDATE ... SET scope='global'`, leaking their private docs into the shared knowledge base visible to every authenticated user — or worse, lets them plant content the LLM retrieves for everyone. Phase 1 designs this out with separate INSERT/UPDATE policies per scope, `WITH CHECK (scope = OLD.scope)` forbidding scope mutation entirely (promotion = delete + admin re-upload), and a CHECK constraint coupling `scope`/`user_id` consistency. Phase 1's `test_two_scope_rls.py` cross-user × cross-scope matrix is the gate — do not advance to Phase 2 until 100% of that matrix passes.

---

*Roadmap created: 2026-05-01*
