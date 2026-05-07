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
- [ ] **FOLDER-02**: `folder_service.py` provides `list_folder`, `create_folder`, `move_document`, `rename_folder`, `delete_folder`
- [x] **FOLDER-03**: Folder rename is transactional prefix update on both `documents.folder_path` AND `folders.path` via Supabase RPC ✅ Phase 3 / Plan 01 (2026-05-07) — `rename_folder_prefix(p_old_prefix, p_new_prefix, p_scope, p_user_id)` RPC in Migration 019 (commit ca017e7); applied to live DB; SUMMARY at .planning/phases/03-folder-service-routers-dedup-extension/03-01-SUMMARY.md
- [x] **FOLDER-04**: Folder delete rejects non-empty (returns structured `{error: "FOLDER_NOT_EMPTY", document_count, subfolder_count}`) ✅ Phase 3 / Plan 01 (2026-05-07) — `delete_folder_if_empty(p_folder_id)` RPC returns (deleted BOOLEAN, document_count INT, subfolder_count INT); router (Plan 04) maps deleted=FALSE to 409 with structured body. Migration 019 commit ca017e7
- [ ] **FOLDER-05**: `record_manager.py` dedup key extended to `(scope, user_id, folder_path, file_name, hash)` so the same file in two folders is allowed
- [ ] **FOLDER-06**: `folders` router with GET/POST/PATCH/DELETE endpoints; admin gate for `scope='global'` writes
- [ ] **FOLDER-07**: Extended `files` router: `POST /api/files/upload?folder_path=...&scope=...`, `PATCH /api/files/{id}` for rename and folder move

### Exploration Tools

- [ ] **TOOL-01**: `tree` tool — args `path`, `max_depth` (server-capped 4–6), `scope`; returns nested structure with `[N more folders, M more docs]` count summaries; max 500 entries with truncation marker
- [ ] **TOOL-02**: `glob` tool — args `pattern` (`**` and `*` semantics), `path`, `type` (file/folder/both), `scope`; matches against `folder_path` + `file_name`
- [ ] **TOOL-03**: `grep` tool — args `pattern`, `path`, `case_insensitive`, `multiline`, `output_mode` (content/files_with_matches/count), `-A`/`-B`/`-C` context, `scope`; max 50 hits with `±2` line context; statement timeout `5s`; rejects pathological regexes
- [ ] **TOOL-04**: `list_files` tool — args `path`, `scope`; single-level listing, folders-then-files-alpha order
- [ ] **TOOL-05**: `read_document` tool — args `document_id` OR `path`, `offset` (1-based), `limit` (default 2000, hard cap 5000); returns arrow-form `{n}→{content}`; CRLF normalized; UTF-8 codepoint-safe; line-by-line slicing
- [ ] **TOOL-06**: All tools use Pydantic v2 BaseModel for arg validation (`Literal["user","global","both"]` for scope, `Field(..., ge=, le=)` for numeric bounds, regex pattern for path)
- [ ] **TOOL-07**: Every tool result row carries `scope: 'user' | 'global'` (no exceptions)
- [ ] **TOOL-08**: Hard 12K-char cap per tool result with explicit `[...truncated, N more]` marker
- [ ] **TOOL-09**: Every new tool routed through Episode 1's layered-fallback empty-response wrapper in `openai_client.py`
- [ ] **TOOL-10**: LangSmith `@traceable(run_type="tool")` on each tool function

### Search Documents Extension

- [ ] **SEARCH-01**: `search_documents` tool schema extended with optional `folder_path` (prefix filter) and `scope` parameters; defaults preserve existing behavior
- [ ] **SEARCH-02**: `match_document_chunks_with_filters` and `match_document_chunks_hybrid` RPCs gain `match_folder_path` and `match_scope` parameters (NULL defaults)
- [ ] **SEARCH-03**: System prompt updated to describe when LLM should self-scope via folder_path/scope args

### Explorer Sub-Agent

- [ ] **EXPLORER-01**: `run_explorer_sub_agent()` extends existing `run_sub_agent` shape with `for turn in range(MAX_TURNS=8)` hard bound
- [ ] **EXPLORER-02**: 60s wall-clock timeout + no-progress detector (tool-name+args-hash repeat → short-circuit)
- [ ] **EXPLORER-03**: Hard exclusion of `analyze_document` from Explorer's toolset (no recursive sub-agents)
- [ ] **EXPLORER-04**: Generalized SSE event protocol (`agent_name`, `event`, `payload`) supporting both `analyze_document` and `explore_knowledge_base`; new event types `sub_agent_tool_start` / `sub_agent_tool_done` for nested tool calls
- [ ] **EXPLORER-05**: `messages.tool_metadata` JSONB persists Explorer trace so old chats render correctly on reload
- [ ] **EXPLORER-06**: LangSmith `@traceable(run_type="chain")` on Explorer entry; tool calls become nested children spans

### File Explorer UI

- [ ] **UI-01**: `FileExplorerPanel.tsx` replaces flat `FileUploadPanel.tsx` in `Chat.tsx`
- [ ] **UI-02**: Two top-level sections rendered simultaneously ("Shared" + "My Files"), not tabs
- [ ] **UI-03**: Recursive `FolderTree` with expand/collapse; open-folder state persisted in `localStorage` per user
- [ ] **UI-04**: Folder CRUD via right-click `ContextMenu` (Create / Rename / Delete with confirm) and inline buttons
- [ ] **UI-05**: Upload-into-folder (drop file onto folder, or pick folder before upload) — replaces flat upload
- [ ] **UI-06**: Drag-move single document with shadcn-style drop indicator; confirm-on-cross-scope move modal
- [ ] **UI-07**: Document rename in place
- [ ] **UI-08**: Breadcrumbs, inline file count per folder, scope badges on documents, `content_markdown_status` badge for pending/failed re-index
- [ ] **UI-09**: Keyboard navigation (arrow keys for tree expand/collapse)
- [ ] **UI-10**: `MessageList` `SubAgentSection` extended (recursively, not forked) to render Explorer's nested tool rows
- [ ] **UI-11**: Admin-only affordance for global-scope writes (visible only when `isAdmin === true`)

### Tests

- [ ] **TEST-01**: `test_folders.py` — folder CRUD, transactional rename, non-empty-delete rejection, concurrent-upload-no-orphan
- [ ] **TEST-02**: `test_exploration_tools.py` — 200-folder fixture for tree truncation, 5000-doc fixture for grep perf (assert Bitmap Index Scan in EXPLAIN), CRLF/Unicode/single-long-line/mixed-ending fixtures for read_document, adversarial-payload fixtures for empty-response guard
- [ ] **TEST-03**: `test_explorer_sub_agent.py` — MAX_TURNS bound, timeout, no-progress detector, recursive-sub-agent rejection
- [ ] **TEST-04**: `test_two_scope_rls.py` — full cross-user × cross-scope matrix
- [ ] **TEST-05**: Frontend Playwright additions in `e2e/full-suite.spec.ts` for folder tree, drag-move, sub-agent activity card

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
| RLS-02 | Phase 1 | ✅ Complete (Plan 05, 55077ad) |
| RLS-03 | Phase 1 | ✅ Complete (Plan 05, 55077ad) — implemented via BEFORE UPDATE trigger (canonical Postgres workaround for OLD.col reference) |
| RLS-04 | Phase 1 | Pending |
| BACKFILL-01 | Phase 2 | ✅ Complete (Plan 02-02, 4dd7c4c + 91ad425) |
| BACKFILL-02 | Phase 2 | ✅ Complete (Plan 02-03 writer 28e8fab + Plan 02-04 verifier 2ad9b78) |
| BACKFILL-03 | Phase 2 | ✅ Complete (Plan 02-04 verifier 2ad9b78) — no-op verifier confirms Migration 012 DEFAULT did its job |
| BACKFILL-04 | Phase 2 | ✅ Complete (Plan 02-03 writer 28e8fab + Plan 02-04 verifier 2ad9b78) |
| FOLDER-01 | Phase 1 | ✅ Complete (Plan 01, b608452) |
| FOLDER-02 | Phase 3 | Pending |
| FOLDER-03 | Phase 3 | ✅ Complete (Plan 03-01, Migration 019 commit ca017e7) |
| FOLDER-04 | Phase 3 | ✅ Complete (Plan 03-01, Migration 019 commit ca017e7) |
| FOLDER-05 | Phase 3 | Pending |
| FOLDER-06 | Phase 3 | Pending |
| FOLDER-07 | Phase 3 | Pending |
| TOOL-01 | Phase 4 | Pending |
| TOOL-02 | Phase 4 | Pending |
| TOOL-03 | Phase 4 | Pending |
| TOOL-04 | Phase 4 | Pending |
| TOOL-05 | Phase 4 | Pending |
| TOOL-06 | Phase 4 | Pending |
| TOOL-07 | Phase 4 | Pending |
| TOOL-08 | Phase 4 | Pending |
| TOOL-09 | Phase 4 | Pending |
| TOOL-10 | Phase 4 | Pending |
| SEARCH-01 | Phase 4 | Pending |
| SEARCH-02 | Phase 4 | Pending |
| SEARCH-03 | Phase 4 | Pending |
| EXPLORER-01 | Phase 5 | Pending |
| EXPLORER-02 | Phase 5 | Pending |
| EXPLORER-03 | Phase 5 | Pending |
| EXPLORER-04 | Phase 5 | Pending |
| EXPLORER-05 | Phase 5 | Pending |
| EXPLORER-06 | Phase 5 | Pending |
| UI-01 | Phase 6 | Pending |
| UI-02 | Phase 6 | Pending |
| UI-03 | Phase 6 | Pending |
| UI-04 | Phase 6 | Pending |
| UI-05 | Phase 6 | Pending |
| UI-06 | Phase 6 | Pending |
| UI-07 | Phase 6 | Pending |
| UI-08 | Phase 6 | Pending |
| UI-09 | Phase 6 | Pending |
| UI-10 | Phase 6 | Pending |
| UI-11 | Phase 6 | Pending |
| TEST-01 | Phase 3 | Pending |
| TEST-02 | Phase 4 | Pending |
| TEST-03 | Phase 5 | Pending |
| TEST-04 | Phase 1 | Pending |
| TEST-05 | Phase 6 | Pending |

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
*Last updated: 2026-05-04 — Phase 2 / Plan 04 complete: BACKFILL-02 + BACKFILL-03 + BACKFILL-04 marked complete (test_backfill.py integration suite + register in test_all.py; suite-level run 15/15 PASS; commits 2ad9b78 + 01f2782). Phase 2 closes green: all four BACKFILL-* requirements ✅.*
