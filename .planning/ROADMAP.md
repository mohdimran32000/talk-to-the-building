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
- [x] **Phase 2: content_markdown Backfill (Gated)** — Re-run Docling against existing Storage blobs to populate `documents.content_markdown`; surface re-index status; gate `grep`/`read_document` until operational. ✅ 2026-05-04
- [x] **Phase 3: Folder Service + Routers + Dedup Extension** — Pure CRUD layer: `folder_service.py`, `folders` router, extended `files` router (upload-into-folder, rename, move), `record_manager` dedup key extended. ✅ 2026-05-09
- [x] **Phase 4: Five Exploration Tools + search_documents Extension** — `tree`, `glob`, `grep`, `list_files`, `read_document` with Pydantic v2 arg validation, hard token-budget caps, scope-tagged result rows; `search_documents` extended with `folder_path`/`scope` filters.
 (completed 2026-05-09)
- [x] **Phase 5: Explorer Sub-Agent + SSE Protocol Generalization** — `run_explorer_sub_agent` with `MAX_TURNS=8`, wall-clock timeout, no-progress detector; SSE sub-agent event protocol generalized; `messages.tool_metadata` persistence. ✅ 2026-05-10
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
- [x] 03-PLAN.md — backfill_content_markdown.py CLI (BACKFILL-02 + BACKFILL-04, --dry-run / --limit / --document-id / --purge-orphans) ✅ 2026-05-06
- [x] 04-PLAN.md — test_backfill.py integration suite + register in test_all.py (BACKFILL-03 verifier + Phase 2 SC4 byte-equivalence) ✅ 2026-05-04 — suite-level run 15/15 PASS
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
**Plans**: 7 plans in 5 waves

**Wave 1** *(blocking — Migration 019 RPCs needed by Wave 2+)*
- [x] 01-PLAN.md — Migration 019 (rename_folder_prefix + delete_folder_if_empty + create_folder_if_not_exists RPCs) + apply via Supabase MCP apply_migration (DATABASE_URL fallback) + Pydantic schemas (FolderResponse, FolderCreate, FolderPatch, FilePatch + DocumentResponse extensions) (FOLDER-03, FOLDER-04) ✅ 2026-05-07

**Wave 2** *(parallel after Wave 1 — different files)*
- [x] 02-PLAN.md — folder_service.py extensions: list_folder, create_folder, move_document, rename_folder, delete_folder (FOLDER-02) ✅ 2026-05-07
- [x] 03-PLAN.md — record_manager.determine_action() extended with scope+folder_path kwargs and the corresponding SELECT-filter branching (FOLDER-05) ✅ 2026-05-07

**Wave 3** *(parallel after Wave 2 — different router files)*
- [x] 04-PLAN.md — backend/app/routers/folders.py (NEW) + main.py registration: GET/POST/PATCH/DELETE with inline admin gate + structured 409 (FOLDER-06) ✅ 2026-05-07
- [x] 05-PLAN.md — backend/app/routers/files.py extended: POST /upload accepts folder_path + scope query args + PATCH /{id} for rename and folder move (FOLDER-07) ✅ 2026-05-07

**Wave 4** *(blocked on Waves 1-3 — integration suite tests against shipped code)*
- [x] 06-PLAN.md — backend/scripts/test_folders.py (591 lines / 10 sections / 36 h.test() assertions) + register in test_all.py (TEST-01; covers FOLDER-02..07 + Pitfalls 4/5/10 + SC1..SC5 + cross-user isolation) ✅ 2026-05-09 — focused suite 33/33 PASS after FOLDER-03 (RenameFolderResponse) + FOLDER-05 (synchronous content_hash) gap-closure fixes (commits 378cffb, b11a90f)

**Cross-cutting constraints** *(must_haves shared across multiple plans)*
- `normalize_path()` is the SOLE chokepoint for every folder_path write — Plan 02 (service-layer entry), Plan 04 (router entry), Plan 05 (router entry); Pitfall 4 mitigation
- Strategy B (folders rows ONLY on explicit `POST /api/folders`, never on file upload) — Plans 04 (writes), 05 (does not write), 06 (asserts ZERO folders rows after 10 parallel uploads); Pitfall 10 mitigation; locked from STATE.md line 74
- Migration 015 `forbid_scope_mutation` trigger is bedrock — `FilePatch` Pydantic model omits `scope`; PATCH endpoints reject scope smuggling explicitly (Plan 01 + Plan 05)
- Path-prefix predicates use `LIKE prefix || '/%'` (NOT `prefix || '%'`) to avoid sibling-folder false matches — Plan 01 RPC, Plan 02 service queries
- Inline admin-gate mirror in folders/files routers (vs. `Depends(get_admin_user)`) is intentional: gate is body-conditional and FastAPI Depends evaluates before body parsing — Plan 04 `_require_admin` helper, Plan 05 inline; substantive 403 outcome (SC1) preserved

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
**Plans**: 9 plans in 7 waves

**Wave 1** *(parallel — disjoint files; both blocking foundation)*
- [x] 04-01-PLAN.md — Migration 020: grep_documents RPC + extend match_document_chunks_with_filters/hybrid with match_folder_path/match_scope (NULL defaults) + apply via run_migrations.py [BLOCKING] (TOOL-03 backend, SEARCH-02)
- [x] 04-02-PLAN.md — exploration_tools/ package: schemas.py (5 Pydantic v2 BaseModels) + _truncate.py (apply_12k_cap) + _scope_tag.py (ensure_scope_tag) (TOOL-06, TOOL-07, TOOL-08)

**Wave 2** *(blocked on Wave 1; locks the dispatch-arm template Plans 04-07 mirror)*
- [x] 04-03-PLAN.md — list_files tool + openai_client.py _build_list_files_tool factory + dispatch arm (TOOL-04 + TOOL-06/07/08/09/10 cross-cutting)

**Wave 3** *(blocked on Wave 2; openai_client.py serialized to avoid merge conflicts)*
- [x] 04-04-PLAN.md — tree tool with iterative-BFS budget + per-level summaries + openai_client.py extension (TOOL-01 + cross-cutting)

**Wave 4** *(blocked on Wave 3)*
- [x] 04-05-PLAN.md — glob tool with glob→regex translator + type=file/folder/both branches + openai_client.py extension (TOOL-02 + cross-cutting)

**Wave 5** *(blocked on Wave 4)*
- [x] 04-06-PLAN.md — read_document tool with arrow-form rendering + CRLF normalization + UTF-8-safe truncation + openai_client.py extension (TOOL-05 + cross-cutting)

**Wave 6** *(blocked on Wave 5 + depends on Wave 1 RPC)*
- [x] 04-07-PLAN.md — grep tool with pathological-regex blocklist + literal-hint extraction + ±A/B/C context + openai_client.py extension (TOOL-03 + cross-cutting)

**Wave 7** *(parallel — Plan 08 finishes openai_client.py edits; Plan 09 tests everything end-to-end)*
- [x] 04-08-PLAN.md — search_documents extension: _build_search_tool gains folder_path + scope properties; dispatch passes match_folder_path/match_scope to RPCs; system prompt updated (SEARCH-01, SEARCH-03)
- [x] 04-09-PLAN.md — test_exploration_tools.py: 600+ line integration suite covering TOOL-01..10 + SEARCH-01..03 + Phase 4 SC1..5; register in test_all.py SUITES (TEST-02)

**Cross-cutting constraints** *(must_haves shared across multiple plans)*
- normalize_path() FIRST statement of every tool function (Pitfall 4 chokepoint) — Plans 03/04/05/06/07; SEARCH-01 dispatch (Plan 08)
- @traceable(name="<tool>", run_type="tool") on every tool fn (TOOL-10) — Plans 03-07
- result_text = json.dumps(tool_result) flows through unchanged layered-fallback wrapper at openai_client.py:565-610 (TOOL-09) — Plans 03-08
- Every result row carries scope ∈ {user,global} via ensure_scope_tag (TOOL-07) — Plans 03/04/05/06/07
- apply_12k_cap() at the tail of every tool except read_document which does its own UTF-8-safe truncation (TOOL-08) — Plans 03/04/05/07
- _assert_uuid(user_id, "user_id") before any PostgREST or() interpolation (HI-01 from Phase 3) — Plans 05 (glob), 07 (grep)
- _escape_like() on literal-prefix LIKE predicates (HI-03 from Phase 3) — Plan 05 (glob)
- Phase 2 LOCKED contract: non-ready content_markdown rows surface as {status: "pending_reindex", content_markdown_status: <X>} — Plans 01 (RPC), 06 (read_document), 07 (grep)
- Tools NEVER accept user_id as a Pydantic Args field — derived from JWT in dispatch loop (Episode 1 invariant; T-UserIdSmuggling) — Plan 02 (schemas)
- openai_client.py:565-610 layered-fallback wrapper UNCHANGED across all 7 edits — Plans 03/04/05/06/07/08

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
**Plans**: 7 plans in 5 waves

**Wave 0** *(foundation — constants/helpers/types ABOVE the existing run_sub_agent)*
- [x] 05-01-PLAN.md — sub_agent.py extension: ExplorerArgs Pydantic v2 model + 4 budget constants (MAX_TURNS=8, WALL_CLOCK_BUDGET_S=60, RESULT_CHAR_CAP=12_000, SSE_ARG_CAP=500) + EXPLORER_ALLOWED_TOOLS tuple + setup-time recursion-ban assert (layer 1 of EXPLORER-03 triple-defense) + EXPLORER_SYSTEM_PROMPT + _signature no-progress hash helper (EXPLORER-01, EXPLORER-02, EXPLORER-03)

**Wave 1** *(blocked on Wave 0 — depends on every Plan 01 helper)*
- [x] 05-02-PLAN.md — sub_agent.py: run_explorer_sub_agent generator + _build_explorer_tool_set (EXPLORER-03 layer 2) + _dispatch_explorer_tool (EXPLORER-03 layer 3) + _extract_function_call/_extract_text/_truncate_args_for_sse helpers; bounded for-range loop with for-else MAX_TURNS exhaustion + wall-clock guard + no-progress detector + apply_12k_cap on tool results + final compact-summary streaming + @traceable(run_type='chain') (EXPLORER-01, EXPLORER-02, EXPLORER-03, EXPLORER-06)

**Wave 2** *(blocked on Wave 1 — openai_client.py serialized to avoid cross-plan merge conflicts)*
- [x] 05-03-PLAN.md — openai_client.py: _build_explore_knowledge_base_tool factory + registration in if has_documents block + elif tool_name=='explore_knowledge_base' dispatch arm forwarding generator events + _build_system_prompt update with explore_knowledge_base bullet + disambiguation rule; TOOL-09 layered-fallback wrapper at L1070+L1146 UNCHANGED bit-identically (EXPLORER-01, EXPLORER-03)

**Wave 3** *(parallel after Wave 2 — disjoint files: backend SSE generator vs. frontend wiring)*
- [x] 05-04-PLAN.md — messages.py: event_generator extended with TWO new arms (sub_agent_tool_start, sub_agent_tool_done) + dual-emit BOTH legacy AND generalized {type:sub_agent, agent_name, event, payload} envelope on ALL FIVE arms + tool_metadata accumulator refactored to ARRAY (tools_used[].tool_calls[]) + V8 300-char cap on result_preview/sub_agent_result; persistence path at L111-123 UNCHANGED (EXPLORER-04, EXPLORER-05)
- [x] 05-05-PLAN.md — frontend/src/lib/api.ts + frontend/src/pages/Chat.tsx: Message interface extended with question?/sub_agent_id?/tool_calls? optional fields + sendMessage signature gains onSubAgentToolStart/onSubAgentToolDone callbacks (positional-end for back-compat) + two new SSE branches (legacy channel only — Phase 6 owns the generalized envelope switch) + Chat.tsx wires callbacks into setToolSteps with isSubAgent=true marker; ToolStep type extended (EXPLORER-04 frontend half)

**Wave 4** *(blocked on Waves 0-3 — integration suite tests against shipped code)*
- [x] 05-06-PLAN.md — backend/scripts/test_explorer_sub_agent.py NEW (~700 LOC, 10 sections) + register in test_all.py SUITES as ('Explorer', test_explorer_sub_agent) between Exploration and Backfill; covers EXPLORER-01..06 + Pitfall 8 carry-forward; canary precheck names missing Plan; per-id batched cleanup (CLAUDE.md mandatory) (EXPLORER-01..06, TEST-03)

**Wave 5** *(gap-closure — added post-verification; closes SC1 runtime regression caught by TEST-03 Section 4)*
- [x] 05-07-PLAN.md — backend/app/services/sub_agent.py: lazy-bind `_get_client` (change `from app.services.openai_client import _get_client` to `from app.services import openai_client as _openai_client`; update both call sites in `run_sub_agent` and `run_explorer_sub_agent` to `_openai_client._get_client()`) so test stubs at `oc._get_client = lambda: stub_client` reach Explorer's call site; closes the no-progress detector regression (SC1 runtime gate); operator-run TEST-03 rerun verifies Section 4 flips to PASS; updates 05-HUMAN-UAT.md with closure record (EXPLORER-02, TEST-03) ✅ 2026-05-10 — operator-confirmed `Results: 27 passed, 0 failed` with verbatim Section 4 PASS line; commit b9f69ba; SUMMARY at 05-07-SUMMARY.md

**Cross-cutting constraints** *(must_haves shared across multiple plans)*
- Recommendation A LOCKED: extend sub_agent.py rather than create sub_agents/ package (research/ARCHITECTURE.md:175; revisit when third sub-agent appears) — Plans 01 + 02
- LangSmith span auto-nesting: a SINGLE @traceable(run_type='chain') decorator on run_explorer_sub_agent + the EXISTING @traceable(run_type='tool') decorators on the 5 Phase 4 tools (verified at list_files.py:32, tree.py:34, glob_match.py:48, read_document.py:39, grep.py:46) — no manual with trace(...) blocks needed (EXPLORER-06) — Plan 02
- TOOL-09 layered-fallback wrapper at openai_client.py:1068-1113 + 1144-1180 UNCHANGED bit-identically — verified post-Plan-03 by grep -c on the canary line returning 2 (Pitfall 8 carry-forward) — Plan 03
- Dual-emit window LOCKED: Plan 04 emits BOTH legacy and generalized for ONE release; Phase 6 plan-checker enforces removal of LEGACY emissions when the frontend rewrite ships the generalized-only path (Pitfall 12 mitigation 1) — Plan 04 (writer) + Phase 6 (cleanup hook)
- ZERO new SQL / migrations: messages.tool_metadata JSONB column already exists (Migration 010); schema is additive (new keys nested under existing ones) — Plan 04
- ExplorerArgs single-arg v1 LOCKED: query: str = Field(..., min_length=1, max_length=2000) only; optional scope arg deferred to v2 (RESEARCH.md §Open Questions #6 [ASSUMED]) — Plan 01
- No-progress hash policy LOCKED: hash args VERBATIM via json.dumps(..., sort_keys=True, default=str) — no value normalization (case sensitivity in regex/glob is real); Phase 4 normalize_path() at tool entry handles path whitespace already (RESEARCH.md §Open Questions #2) — Plan 01
- sub_agent_id (server-generated UUID per sub_agent_start) is INDEPENDENT from LangSmith run_id — keep them separate to avoid LangSmith availability becoming a hard dependency for chat (RESEARCH.md §Open Questions #3) — Plan 04
- SSE per-arg cap LOCKED at SSE_ARG_CAP=500 chars in sub_agent_tool_start payloads (matches Phase 4's 300-char result_preview discipline) — Plans 01 + 02
- Module-top import of run_explorer_sub_agent in test_explorer_sub_agent.py surfaces EXPLORER-03 layer 1 setup-time AssertionError in CI before any chat triggers it — Plan 06

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
**Plans**: 11 plans in 4 waves

**Wave 0** *(parallel-safe foundation — no inter-deps)*
- [ ] 06-01-PLAN.md — Extend DocumentResponse with content_markdown_status (D-03 backend Pydantic add) (UI-08)
- [ ] 06-02-PLAN.md — Provision admin@test.com via seed script + migration 021 (D-02) (UI-11, TEST-05)
- [ ] 06-03-PLAN.md — Install @dnd-kit + 6 shadcn primitives (UI-04, UI-06, UI-08)
- [ ] 06-04-PLAN.md — Close Phase 5 dual-emit window: delete 5 backend yields + 5 frontend SSE branches; add 1 generalized branch (UI-10)

**Wave 1** *(blocked on Wave 0 — depends on backend types + new deps + closed SSE window)*
- [ ] 06-05-PLAN.md — api.ts folder/document CRUD methods + Pitfall 5 typed deleteFolder (UI-04, UI-05, UI-06, UI-07, UI-08)
- [ ] 06-06-PLAN.md — Tree primitives: FolderNode/FolderTree/RootSection/DocumentRow + Scope/StatusBadge + useOpenFoldersStorage hook (UI-02, UI-03, UI-08, UI-09)
- [ ] 06-07-PLAN.md — SubAgentSection recursive extension (Pitfall 12) + ToolCallRow extract + Chat.tsx liveSubAgentTrace migration (UI-10)

**Wave 2** *(blocked on Wave 1 — composition + interaction wiring)*
- [ ] 06-08-PLAN.md — FileExplorerPanel composition + Breadcrumbs + Chat.tsx mount swap; delete FileUploadPanel.tsx (UI-01, UI-02, UI-03, UI-05, UI-08)
- [ ] 06-09-PLAN.md — Folder CRUD UI: ContextMenu + CreateFolderDialog + DeleteFolderDialog (Pitfall 5 surface) + inline rename + admin gating (UI-04, UI-07, UI-11)
- [ ] 06-10-PLAN.md — DnD wiring with @dnd-kit + same-scope move + cross-scope BLOCK modal (D-01, Pitfall 11) (UI-06)

**Wave 3** *(blocked on Wave 2 — e2e gate)*
- [ ] 06-11-PLAN.md — Playwright @phase6 spec block: 12+ tests covering UI-01..UI-11 + TEST-05 + Pitfall 12 grep assertion + D-01 cross-scope block test (TEST-05)
**UI hint**: yes
**Threats / pitfalls**: Pitfall 11 (scope confusion: scope badges + distinct visual treatment for shared vs private; cross-scope move confirmation modal); Pitfall 5 (folder delete UX: confirmation modal shows actual document/subfolder count from server's structured error, not a guessed number); Pitfall 12 (UI rendering: `SubAgentSection` extended recursively — same component renders both `analyze_document` and Explorer; no `if (agentType === 'explorer')` branch).

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Schema Foundation + Two-Scope RLS + Path Normalizer | 8/8 | Complete | 2026-05-04 |
| 2. content_markdown Backfill (Gated) | 4/4 | Complete | 2026-05-04 |
| 3. Folder Service + Routers + Dedup Extension | 6/6 | Complete    | 2026-05-09 |
| 4. Five Exploration Tools + search_documents Extension | 9/9 | Complete    | 2026-05-09 |
| 5. Explorer Sub-Agent + SSE Protocol Generalization | 7/7 | Complete    | 2026-05-10 |
| 6. File-Explorer UI Cluster | 0/11 | Planned | - |

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
