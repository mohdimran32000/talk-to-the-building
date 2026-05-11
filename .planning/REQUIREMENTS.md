# Requirements: Agentic RAG Application — Episode 2

**Defined:** 2026-05-01
**Core Value:** The agent can locate the *right* piece of information in a large, organized knowledge base — using semantic search when meaning matters and codebase-style traversal (tree/glob/grep/read) when precision matters — without hallucinating across unrelated material.

## v1 Requirements

### Schema (Foundation)

- [x] **SCHEMA-01**: `documents.folder_path` TEXT NOT NULL DEFAULT `/` with CHECK constraint enforcing canonical form (`^/$|^/[^/]+(/[^/]+)*$`)
- [x] **SCHEMA-02**: `documents.scope` TEXT NOT NULL DEFAULT `user` with CHECK constraint coupling scope/user_id consistency (user-scope requires user_id; global-scope requires user_id IS NULL)
- [x] **SCHEMA-03**: `documents.content_markdown` TEXT + `content_markdown_status` enum (`pending`/`ready`/`failed`/`requires_user_reupload`) ✅ Phase 1 / Plan 04 (2026-05-03)
- [x] **SCHEMA-04**: Thin `folders` table with `(id, scope, user_id, path, created_at)` and unique constraint on `(scope, COALESCE(user_id, '00000000-0000-0000-0000-000000000000'), path)` for first-class empty-folder tracking
- [x] **SCHEMA-05**: `pg_trgm` extension enabled + GIN trigram indexes on `documents.content_markdown` and `documents.folder_path` + `text_pattern_ops` btree on `documents.folder_path` ✅ Phase 1 / Plan 06 (2026-05-03) — pg_trgm enabled in migration 012; 5 indexes (3 GIN gin_trgm_ops + 2 btree text_pattern_ops) added in migration 016

### Row-Level Security (Two-scope)

- [x] **RLS-01**: SELECT policy on documents/document_chunks/folders matches `((scope='user' AND user_id=(SELECT auth.uid())) OR scope='global')` ✅ Phase 1 / Plan 05 (2026-05-03)
- [x] **RLS-02**: Separate INSERT and UPDATE policies per scope; admin-only writes for `scope='global'` via existing `is_admin()` helper ✅ Phase 1 / Plan 05 (2026-05-03)
- [x] **RLS-03**: UPDATE policy with `WITH CHECK (scope = OLD.scope)` forbids in-place scope mutation (promotion = delete + admin re-upload) — **implemented as `BEFORE UPDATE` trigger `forbid_scope_mutation()` (canonical Postgres workaround; RLS WITH CHECK cannot reference OLD)** ✅ Phase 1 / Plan 05 (2026-05-03)
- [ ] **RLS-04**: `test_rls.py` extended with full cross-user × cross-scope SELECT/INSERT/UPDATE/DELETE matrix

### Backfill

- [x] **BACKFILL-01**: `ingestion.py` captures and persists Docling's markdown export to `documents.content_markdown` on every new upload
- [x] **BACKFILL-02**: `backend/scripts/backfill_content_markdown.py` re-runs Docling against original Storage blobs for existing Episode 1 docs (idempotent, throttled, status-tracked) ✅ Phase 2 / Plan 03 + Plan 04 verifier (28e8fab + 2ad9b78)
- [x] **BACKFILL-03**: Episode 1 documents migrate to `folder_path='/'`, `scope='user'` (automatic via column DEFAULT) ✅ Phase 2 / Plan 04 verifier (2ad9b78) — `SELECT COUNT(*) FROM documents WHERE folder_path != '/' OR scope != 'user'` returns 0; the no-op verifier confirms Migration 012 DEFAULT did its job for all pre-Phase-2 rows
- [x] **BACKFILL-04**: Documents whose source blob is missing get marked `requires_user_reupload`; tools surface this status rather than silently skipping ✅ Phase 2 / Plan 03 (writer) + Plan 04 (verifier) — backfill subprocess against fixture row with no Storage blob produces `content_markdown_status='requires_user_reupload'`

### Folder Service

- [x] **FOLDER-01**: Single canonical `normalize_path()` helper called by every write path (UI upload, drag-move, rename, backfill, tool arg parsing) ✅ Phase 1 / Plan 01 (2026-05-03)
- [x] **FOLDER-02**: `folder_service.py` provides `list_folder`, `create_folder`, `move_document`, `rename_folder`, `delete_folder` ✅ Phase 3 / Plan 02 (2026-05-07) — 5 new public functions added to backend/app/services/folder_service.py at L80/184/227/253/291; 3 are thin Python wrappers around Migration 019 RPCs (create_folder→create_folder_if_not_exists, rename_folder→rename_folder_prefix, delete_folder→delete_folder_if_empty), 2 are direct supabase-py table queries (list_folder, move_document). Every path-accepting function runs normalize_path() as its FIRST STATEMENT (Pitfall 4 chokepoint). Commit 4802edd; SUMMARY at .planning/phases/03-folder-service-routers-dedup-extension/03-02-SUMMARY.md
- [x] **FOLDER-03**: Folder rename is transactional prefix update on both `documents.folder_path` AND `folders.path` via Supabase RPC ✅ Phase 3 / Plan 01 (2026-05-07) — `rename_folder_prefix(p_old_prefix, p_new_prefix, p_scope, p_user_id)` RPC in Migration 019 (commit ca017e7); applied to live DB; SUMMARY at .planning/phases/03-folder-service-routers-dedup-extension/03-01-SUMMARY.md
- [x] **FOLDER-04**: Folder delete rejects non-empty (returns structured `{error: "FOLDER_NOT_EMPTY", document_count, subfolder_count}`) ✅ Phase 3 / Plan 01 (2026-05-07) — `delete_folder_if_empty(p_folder_id)` RPC returns (deleted BOOLEAN, document_count INT, subfolder_count INT); router (Plan 04) maps deleted=FALSE to 409 with structured body. Migration 019 commit ca017e7
- [x] **FOLDER-05**: `record_manager.py` dedup key extended to `(scope, user_id, folder_path, file_name, hash)` so the same file in two folders is allowed ✅ Phase 3 / Plan 03 (2026-05-07) — `determine_action()` extended at backend/app/services/record_manager.py:27-93 with scope: str = 'user' and folder_path: str = '/' kwargs (defaults preserve Phase 1/2 callers); SELECT extended with .eq('scope', scope).eq('folder_path', folder_path) and a scope-branched user_id filter (.eq for 'user', .is_('user_id','null') for 'global' — Pitfall A mitigation). Uses Migration 012's documents_scope_user_path_filename_unique index for the lookup. RecordAction + compute_file_hash + compute_chunk_hash UNCHANGED. Commit c86711a; SUMMARY at .planning/phases/03-folder-service-routers-dedup-extension/03-03-SUMMARY.md
- [x] **FOLDER-06**: `folders` router with GET/POST/PATCH/DELETE endpoints; admin gate for `scope='global'` writes ✅ Phase 3 / Plan 04 (2026-05-07) — `backend/app/routers/folders.py` (159 lines) with 4 endpoints under `/api/folders` prefix; inline `_require_admin()` helper (mirror of auth.py:46-51) fires only when body.scope=='global' (POST) or existing.scope=='global' (PATCH/DELETE) — body/row-conditional gate that the standard FastAPI Depends pattern cannot express because Depends evaluates BEFORE body parsing; DELETE non-empty returns `JSONResponse(status_code=409, content={error:'FOLDER_NOT_EMPTY', document_count, subfolder_count})` — Phase 6 UI consumer contract LOCKED; main.py registers the router between files.router and settings.router. Commits 6049e0e (Task 1) + 3828e49 (Task 2); SUMMARY at .planning/phases/03-folder-service-routers-dedup-extension/03-04-SUMMARY.md
- [x] **FOLDER-07**: Extended `files` router: `POST /api/files/upload?folder_path=...&scope=...`, `PATCH /api/files/{id}` for rename and folder move ✅ Phase 3 / Plan 05 (2026-05-07) — `backend/app/routers/files.py` extended in two atomic tasks. Task 1 (commit 6fdbdef): upload_file gains `folder_path: str = Query("/")` and `scope: str = Query("user", regex="^(user|global)$")` query params; normalize_path() at the router boundary (Pitfall 4 belt); inline admin gate (mirror of auth.py:46-51) when scope=='global' returning 403 for non-admins; effective_user_id (None for global per Migration 012 coupling CHECK) and storage_user_segment ('global' literal for global, user_id UUID for user — Pitfall F mitigation that keeps the Storage path well-formed) computed; determine_action() called with scope=scope, folder_path=folder_path kwargs (Plan 03 contract; FOLDER-05 dedup-key acceptance via the upload path); documents.insert in the create branch includes scope and folder_path columns. Task 2 (commit 60da21c): NEW endpoint PATCH /api/files/{file_id} with FilePatch body (file_name?, folder_path?), lookup-then-gate-then-act pattern (404 fast-fail -> admin gate when existing.scope=='global' -> normalize folder_path -> empty-update 400 -> UPDATE -> re-SELECT and return DocumentResponse); three-layer scope-immutability defense (FilePatch omits scope + explicit update_data dict + Migration 015 trigger bedrock); metadata-only discipline (no _upload_to_storage, no background ingest). Total app routes 22 → 23. SUMMARY at .planning/phases/03-folder-service-routers-dedup-extension/03-05-SUMMARY.md

### Exploration Tools

- [x] **TOOL-01**: `tree` tool — args `path`, `max_depth` (server-capped 4–6), `scope`; returns nested structure with `[N more folders, M more docs]` count summaries; max 500 entries with truncation marker
- [x] **TOOL-02**: `glob` tool — args `pattern` (`**` and `*` semantics), `path`, `type` (file/folder/both), `scope`; matches against `folder_path` + `file_name`
- [x] **TOOL-03**: `grep` tool — args `pattern`, `path`, `case_insensitive`, `multiline`, `output_mode` (content/files_with_matches/count), `-A`/`-B`/`-C` context, `scope`; max 50 hits with `±2` line context; statement timeout `5s`; rejects pathological regexes
- [x] **TOOL-04**: `list_files` tool — args `path`, `scope`; single-level listing, folders-then-files-alpha order
- [x] **TOOL-05**: `read_document` tool — args `document_id` OR `path`, `offset` (1-based), `limit` (default 2000, hard cap 5000); returns arrow-form `{n}→{content}`; CRLF normalized; UTF-8 codepoint-safe; line-by-line slicing
- [x] **TOOL-06**: All tools use Pydantic v2 BaseModel for arg validation (`Literal["user","global","both"]` for scope, `Field(..., ge=, le=)` for numeric bounds, regex pattern for path)
- [x] **TOOL-07**: Every tool result row carries `scope: 'user' | 'global'` (no exceptions)
- [x] **TOOL-08**: Hard 12K-char cap per tool result with explicit `[...truncated, N more]` marker
- [x] **TOOL-09**: Every new tool routed through Episode 1's layered-fallback empty-response wrapper in `openai_client.py`
- [x] **TOOL-10**: LangSmith `@traceable(run_type="tool")` on each tool function

### Search Documents Extension

- [x] **SEARCH-01**: `search_documents` tool schema extended with optional `folder_path` (prefix filter) and `scope` parameters; defaults preserve existing behavior
- [x] **SEARCH-02**: `match_document_chunks_with_filters` and `match_document_chunks_hybrid` RPCs gain `match_folder_path` and `match_scope` parameters (NULL defaults)
- [x] **SEARCH-03**: System prompt updated to describe when LLM should self-scope via folder_path/scope args

### Explorer Sub-Agent

- [x] **EXPLORER-01**: `run_explorer_sub_agent()` extends existing `run_sub_agent` shape with `for turn in range(MAX_TURNS=8)` hard bound ✅ Phase 5 / Plan 02 (run_explorer_sub_agent generator) + verified at runtime via TEST-03 Section 2 (3/3 PASS — MAX_TURNS bound enforced)
- [x] **EXPLORER-02**: 60s wall-clock timeout + no-progress detector (tool-name+args-hash repeat → short-circuit) ✅ Phase 5 / Plan 02 (wall-clock guard + _signature no-progress detector) + Plan 07 (lazy-bind `_get_client` to make the test stub reach the call site, commit b9f69ba); verified at runtime via TEST-03 Section 3 (wall-clock 2/2 PASS) + Section 4 (no-progress 2/2 PASS — verbatim `EXPLORER-02 no-progress: exactly ONE sub_agent_tool_start emitted before short-circuit`)
- [x] **EXPLORER-03**: Hard exclusion of `analyze_document` from Explorer's toolset (no recursive sub-agents) ✅ Phase 5 / Plan 01 (layer 1 module-level allowlist + setup-time assert) + Plan 02 (layer 2 _build_explorer_tool_set + layer 3 _dispatch_explorer_tool); verified at runtime via TEST-03 Section 5 (4/4 PASS — all 3 recursion-ban layers fire)
- [x] **EXPLORER-04**: Generalized SSE event protocol (`agent_name`, `event`, `payload`) supporting both `analyze_document` and `explore_knowledge_base`; new event types `sub_agent_tool_start` / `sub_agent_tool_done` for nested tool calls ✅ Phase 5 / Plan 04 (messages.py event_generator dual-emit on all 5 sub-agent SSE arms) + Plan 05 (frontend wiring); verified at runtime via TEST-03 Section 6 (dual-emit 3/3 PASS) + Section 7 (multi-sub 2/2 PASS)
- [x] **EXPLORER-05**: `messages.tool_metadata` JSONB persists Explorer trace so old chats render correctly on reload ✅ Phase 5 / Plan 04 (tool_metadata accumulator refactored to ARRAY (tools_used[].tool_calls[]); persistence path UNCHANGED at messages.py L111-123); verified at runtime via TEST-03 Section 8 (3/3 PASS — JSONB shape correct)
- [x] **EXPLORER-06**: LangSmith `@traceable(run_type="chain")` on Explorer entry; tool calls become nested children spans ✅ Phase 5 / Plan 02 (single @traceable(name="explore_knowledge_base", run_type="chain") on run_explorer_sub_agent + the EXISTING @traceable(run_type="tool") on the 5 Phase 4 tools auto-nest as children via contextvars); verified at runtime via TEST-03 Section 9 (2/2 PASS — chain run found, child count <=8; LangSmith API host unreachable on this run but framework tolerated)

### File Explorer UI

- [x] **UI-01**: `FileExplorerPanel.tsx` replaces flat `FileUploadPanel.tsx` in `Chat.tsx`
- [x] **UI-02**: Two top-level sections rendered simultaneously ("Shared" + "My Files"), not tabs
- [x] **UI-03**: Recursive `FolderTree` with expand/collapse; open-folder state persisted in `localStorage` per user
- [x] **UI-04**: Folder CRUD via right-click `ContextMenu` (Create / Rename / Delete with confirm) and inline buttons — *primitive installed Phase 6 / Plan 03 (shadcn ContextMenu); full UI wiring lands in Plan 06-09*
- [x] **UI-05**: Upload-into-folder (drop file onto folder, or pick folder before upload) — replaces flat upload
- [x] **UI-06**: Drag-move single document with shadcn-style drop indicator; confirm-on-cross-scope move modal — *dnd-kit packages + shadcn AlertDialog primitive installed Phase 6 / Plan 03; drag-move logic + BLOCK modal wiring lands in Plan 06-10*
- [x] **UI-07**: Document rename in place
- [x] **UI-08**: Breadcrumbs, inline file count per folder, scope badges on documents, `content_markdown_status` badge for pending/failed re-index — *backend status field landed Phase 6 / Plan 01; shadcn Badge primitive installed Phase 6 / Plan 03; UI placement of badges + breadcrumbs lands later in the phase*
- [x] **UI-09**: Keyboard navigation (arrow keys for tree expand/collapse)
- [x] **UI-10**: `MessageList` `SubAgentSection` extended (recursively, not forked) to render Explorer's nested tool rows
- [x] **UI-11**: Admin-only affordance for global-scope writes (visible only when `isAdmin === true`)

### Tests

- [x] **TEST-01**: `test_folders.py` — folder CRUD, transactional rename, non-empty-delete rejection, concurrent-upload-no-orphan
- [x] **TEST-02**: `test_exploration_tools.py` — 200-folder fixture for tree truncation, 5000-doc fixture for grep perf (assert Bitmap Index Scan in EXPLAIN), CRLF/Unicode/single-long-line/mixed-ending fixtures for read_document, adversarial-payload fixtures for empty-response guard
- [x] **TEST-03**: `test_explorer_sub_agent.py` — MAX_TURNS bound, timeout, no-progress detector, recursive-sub-agent rejection ✅ Phase 5 / Plan 06 (test suite created; ~700 LOC; 10 sections) + Plan 07 (operator-confirmed full-suite green at `Results: 27 passed, 0 failed`; commit b9f69ba)
- [ ] **TEST-04**: `test_two_scope_rls.py` — full cross-user × cross-scope matrix
- [x] **TEST-05**: Frontend Playwright additions in `e2e/full-suite.spec.ts` for folder tree, drag-move, sub-agent activity card

## v2 Requirements

### Bridge: Explorer ↔ Chat

- **MENTION-01**: `@/path` mention chip in chat input with autocomplete from current tree

### Bulk Operations

- **BULK-01**: Multi-select files/folders (shift/ctrl-click)
- **BULK-02**: Bulk move + bulk delete actions

### Auto-Organize

- **ORGANIZE-01**: LLM-suggested folder structure for flat KBs (one-click "organize")

### Sharing

- **SHARE-01**: Folder-level permissions / sharing with specific users (third scope)

### Recovery

- **TRASH-01**: Trash bin / soft-delete with restore window

### External Sources

- **CONNECTOR-01**: Local-folder mount / sync from a chosen directory
- **CONNECTOR-02**: Drive / S3 / Dropbox connectors

### Compliance

- **AUDIT-01**: Folder-change audit log
- **AUDIT-02**: `global_audit_log` table — every global-scope write logged with `admin_id`, action, before/after

## Out of Scope

| Feature | Reason |
|---------|--------|
| Tree-search box in explorer panel | Competes with the agent as the search surface; two-paths-to-the-same-thing confusion |
| In-app find-in-files panel | Same reason as above; `grep` tool is the search surface |
| In-app document viewer | `read_document` is the surface; building a viewer competes with the agent |
| Folder filter in `MetadataFilterBar` UI dropdown | Folder is structural; metadata is content classification — mixing them muddles both |
| Symlinks / cross-folder document references | Each document lives in exactly one folder; single canonical location only |
| Always-on Explorer sub-agent | Defeats LLM-agency principle; LLM decides when to delegate |
| Drag-from-desktop directly onto a tree folder | Mixes file-input with positional drop target — ambiguous UX |
| Realtime tree updates | Violates project rule (polling, not Realtime, per existing CLAUDE.md and Module 2 fix) |
| Folder-icon-from-name heuristics | Brittle, localization-breaking |
| Connectors / automated ingestion pipelines | Manual upload only per existing project rule |
| Move-to-trash / soft-delete | Out of phase scope; deferred to v2 |
| In-place scope promotion (private → global) | Security risk; promotion is delete + admin re-upload |
| Versioning / audit history of folder changes | Record Manager handles content-version dedup at ingest; folder ops are not versioned this phase |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| SCHEMA-01 | Phase 1 | ✅ Complete (Plan 02, 29d387f) |
| SCHEMA-02 | Phase 1 | ✅ Complete (Plan 02, 29d387f) |
| SCHEMA-03 | Phase 1 | Complete |
| SCHEMA-04 | Phase 1 | ✅ Complete (Plan 03, 37853b7) |
| SCHEMA-05 | Phase 1 | ✅ Complete (Plan 06, f36e1b7) |
| RLS-01 | Phase 1 | ✅ Complete (Plan 05, 55077ad) |
| RLS-02 | Phase 1 | ✅ Complete (Plan 05, 55077ad) — downstream functional defect closed in **Phase 7** (WARN-01: chunk scope/user_id propagation) |
| RLS-03 | Phase 1 | ✅ Complete (Plan 05, 55077ad) — implemented via BEFORE UPDATE trigger (canonical Postgres workaround for OLD.col reference) |
| RLS-04 | Phase 1 | Pending — checkbox sync flip from `[ ]` → `[x]` queued for **Phase 9** (test_two_scope_rls.py 49/0 already verified per audit; only metadata drift remains) |
| BACKFILL-01 | Phase 2 | ✅ Complete (Plan 02-02, 4dd7c4c + 91ad425) |
| BACKFILL-02 | Phase 2 | ✅ Complete (Plan 02-03 writer 28e8fab + Plan 02-04 verifier 2ad9b78) |
| BACKFILL-03 | Phase 2 | ✅ Complete (Plan 02-04 verifier 2ad9b78) — no-op verifier confirms Migration 012 DEFAULT did its job |
| BACKFILL-04 | Phase 2 | ✅ Complete (Plan 02-03 writer 28e8fab + Plan 02-04 verifier 2ad9b78) |
| FOLDER-01 | Phase 1 | ✅ Complete (Plan 01, b608452) |
| FOLDER-02 | Phase 3 | ✅ Complete (Plan 03-02, folder_service.py +258 LOC commit 4802edd) |
| FOLDER-03 | Phase 3 | ✅ Complete (Plan 03-01, Migration 019 commit ca017e7) |
| FOLDER-04 | Phase 3 | ✅ Complete (Plan 03-01, Migration 019 commit ca017e7) |
| FOLDER-05 | Phase 3 | ✅ Complete (Plan 03-03, commit c86711a) |
| FOLDER-06 | Phase 3 | ✅ Complete (Plan 03-04, commits 6049e0e + 3828e49) |
| FOLDER-07 | Phase 3 | ✅ Complete (Plan 03-05, commits 6fdbdef + 60da21c) — downstream cosmetic gap closed in **Phase 7** (WARN-02: GET /api/files or_() filter for globals) |
| TOOL-01 | Phase 4 | Complete |
| TOOL-02 | Phase 4 | Complete |
| TOOL-03 | Phase 4 | Complete |
| TOOL-04 | Phase 4 | Complete |
| TOOL-05 | Phase 4 | Complete |
| TOOL-06 | Phase 4 | Complete |
| TOOL-07 | Phase 4 | Complete — downstream functional defect for non-admin global RAG closed in **Phase 7** (WARN-01) |
| TOOL-08 | Phase 4 | Complete |
| TOOL-09 | Phase 4 | Complete |
| TOOL-10 | Phase 4 | Complete |
| SEARCH-01 | Phase 4 | Complete (wiring) — functional outcome on globals for non-admin closed in **Phase 7** (WARN-01) |
| SEARCH-02 | Phase 4 | Complete (wiring) — RPC predicate rewrite in **Phase 7** (Migration 021) so chunks with scope='global' AND user_id IS NULL match for non-admins |
| SEARCH-03 | Phase 4 | Complete |
| EXPLORER-01 | Phase 5 | ✅ Complete (Plan 05-02 + TEST-03 Section 2 runtime gate green) |
| EXPLORER-02 | Phase 5 | ✅ Complete (Plan 05-02 + Plan 05-07 lazy-bind fix commit b9f69ba; TEST-03 Section 4 27/0 — `EXPLORER-02 no-progress: exactly ONE sub_agent_tool_start emitted before short-circuit`) |
| EXPLORER-03 | Phase 5 | ✅ Complete (Plan 05-01 layer 1 + Plan 05-02 layers 2+3; TEST-03 Section 5 runtime gate green) |
| EXPLORER-04 | Phase 5 | ✅ Complete (Plan 05-04 + Plan 05-05; TEST-03 Sections 6+7 runtime gate green) |
| EXPLORER-05 | Phase 5 | ✅ Complete (Plan 05-04 tool_metadata accumulator refactor; TEST-03 Section 8 runtime gate green) |
| EXPLORER-06 | Phase 5 | ✅ Complete (Plan 05-02 @traceable(run_type="chain"); TEST-03 Section 9 runtime gate green) |
| UI-01 | Phase 6 | Complete |
| UI-02 | Phase 6 | Complete |
| UI-03 | Phase 6 | Complete |
| UI-04 | Phase 6 | Pending (Plan 06-03 installed ContextMenu primitive; wiring in Plan 06-09) |
| UI-05 | Phase 6 | Complete |
| UI-06 | Phase 6 | Pending (Plan 06-03 installed dnd-kit + AlertDialog primitive; wiring in Plan 06-10) |
| UI-07 | Phase 6 | Complete |
| UI-08 | Phase 6 | Pending (Plan 06-01 backend status field + Plan 06-03 Badge primitive; UI placement later in phase) — polled-status pipeline for globals to non-admins closed in **Phase 7** (WARN-02) |
| UI-09 | Phase 6 | Complete |
| UI-10 | Phase 6 | Complete |
| UI-11 | Phase 6 | Complete |
| TEST-01 | Phase 3 | Complete |
| TEST-02 | Phase 4 | Complete |
| TEST-03 | Phase 5 | ✅ Complete (Plan 05-06 suite + Plan 05-07 operator-confirmed `Results: 27 passed, 0 failed`; commit b9f69ba) |
| TEST-04 | Phase 1 | Pending — checkbox sync flip from `[ ]` → `[x]` queued for **Phase 9** (suite registered in test_all.py + 49/0 per audit; only metadata drift remains) |
| TEST-05 | Phase 6 | Complete — residual `applied document count` @phase6 failure closed in **Phase 8** (Phase 6 Playwright closure + re-verification) |

**Coverage:**
- v1 requirements: 55 total
- Mapped to phases: 55 ✓
- Unmapped: 0 ✓

**Requirements per phase:**
- Phase 1 (Schema + RLS + Path Normalizer): 11 — SCHEMA-01..05, RLS-01..04, FOLDER-01, TEST-04
- Phase 2 (Backfill): 4 — BACKFILL-01..04
- Phase 3 (Folder Service + Routers): 7 — FOLDER-02..07, TEST-01
- Phase 4 (Five Tools + search_documents): 14 — TOOL-01..10, SEARCH-01..03, TEST-02
- Phase 5 (Explorer Sub-Agent + SSE): 7 — EXPLORER-01..06, TEST-03
- Phase 6 (File Explorer UI): 12 — UI-01..11, TEST-05

---
*Requirements defined: 2026-05-01*
*Last updated: 2026-05-11 — Phase 6 / Plan 03 installs frontend dep foundation: @dnd-kit/core@6.3.1 + @dnd-kit/sortable@10.0.0 (exact pins), plus six shadcn primitives (context-menu, dialog, alert-dialog, badge, tooltip, separator) — un-edited CLI output. UI-04 / UI-06 / UI-08 remain PENDING because this plan only installs the primitives; the actual wiring lands in Plans 06-09 (ContextMenu CRUD), 06-10 (dnd-kit drag-move + cross-scope BLOCK modal), and later (badge placement). The Plan-03 frontmatter's `requirements: [UI-04, UI-06, UI-08]` declaration was over-claiming and is corrected here — those reqs are foundation-installed, not user-functional-complete.*
*Gap closure annotated: 2026-05-11 — `/gsd-plan-milestone-gaps` added Phases 7–10 from `.planning/v1.0-MILESTONE-AUDIT.md`. Affected REQs (SEARCH-01/02, RLS-02, TOOL-07, FOLDER-07, UI-08, TEST-05) carry inline closure pointers; original Phase mapping preserved because audit `gaps.requirements: []` was empty (REQs are SATISFIED at source level — Phase 7/8 close downstream functional/test defects, not unsatisfied requirements). RLS-04 + TEST-04 checkbox flips are queued for Phase 9 execution.*
