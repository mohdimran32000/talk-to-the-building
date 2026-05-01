# Project Research Summary

**Project:** Agentic RAG Application — Episode 2
**Domain:** Claude-Code-style agentic exploration tools layered onto an existing Supabase + Gemini + Docling RAG app (multi-tenant, two-scope: per-user private + admin-managed global)
**Researched:** 2026-05-01
**Confidence:** HIGH

## Executive Summary

Episode 2 transforms the existing flat per-user document store into a navigable, two-scope knowledge base with Claude-Code-style precision tools (`tree`, `glob`, `grep`, `list_files`, `read_document`) plus an isolated `explore_knowledge_base` sub-agent — all layered onto a locked Episode 1 stack (React/Vite + FastAPI + Supabase Postgres/pgvector + Gemini native SDK + Docling + LangSmith). Across all four research streams the recommended approach is the same: extend, don't refactor. Reuse existing patterns (the manual Gemini tool loop, the `run_sub_agent` SSE generator, the `is_admin` admin gate, the `@traceable` tracing decoration) and add five small, reviewable migrations to introduce `scope`, `folder_path`, `content_markdown`, the thin `folders` table, and a two-scope RLS rewrite — gated by `pg_trgm` enablement and a single canonical path-normalization helper.

The single most important architectural finding is that **schema must come first** and that **`content_markdown` backfill is a hidden critical-path phase**, not a follow-up. All four researchers converged on this: every new tool depends on `folder_path`, `scope`, and `content_markdown` being present and correct, and the only safe way to populate `content_markdown` for existing Episode 1 docs is to re-run Docling against the original Storage blobs (the seemingly cheaper "stitch from chunks" shortcut produces ~10% duplicated text because of the existing 50-word chunk overlap, which silently breaks `grep` line numbers and `read_document` slicing). Backfill must therefore ship as its own gated phase after the schema phase but before any tool that reads `content_markdown` is exposed.

The dominant risks are (1) two-scope RLS misconfiguration leaking private docs into the shared knowledge base or letting users mutate `scope` post-insert, (2) Gemini's empty-response failure mode recurring when oversized tree/grep/read tool results are injected into the next-turn context (Episode 1 already had this exact bug with the SQL tool — the layered fallback pattern in `openai_client.py` post-`53ff28d` must wrap every new tool dispatch path), and (3) the Explorer sub-agent looping unbounded on broad queries. Mitigations are concrete and consensual across the four research files: separate INSERT/UPDATE policies per scope with `WITH CHECK (scope = OLD.scope)` forbidding scope mutation, hard server-side caps on `max_depth`/result count/result-size with truncation markers, and a `MAX_TURNS=8` for-loop bound plus wall-clock timeout plus no-progress detector on the Explorer.

## Key Findings

### Recommended Stack

The Episode 1 stack is **locked**. Episode 2 ships **zero new top-level dependencies** on either side: backend reuses `google-genai`, `supabase-py`, `pydantic`, `langsmith`, `sse-starlette`, `Docling`; frontend reuses Radix-backed shadcn primitives (`Collapsible`, `Breadcrumb`, `ContextMenu`) plus native HTML5 drag-drop. The only additions are Postgres-side: enable the built-in `pg_trgm` extension and add five small migrations.

**Core stack confirmations and additions:**

- **Postgres `pg_trgm` extension** — built-in to Supabase, just `CREATE EXTENSION IF NOT EXISTS pg_trgm`. GIN trigram index on `documents.content_markdown` accelerates `ILIKE`/`~*` for grep; GIN trigram index on `folder_path` covers contains-style queries; `text_pattern_ops` btree on `folder_path` covers prefix LIKE queries (critical because Supabase's default `en_US.UTF-8` locale doesn't use the default btree for `LIKE 'x/%'`).
- **TEXT `folder_path` + thin `folders` side table** — confirmed correct over `ltree`. `ltree` rejects realistic folder names (`-`, `.`, spaces) and forces the LLM to learn `lquery` syntax. TEXT path is what the LLM already understands; prefix queries are sub-millisecond with the right index.
- **Pydantic v2 tool-arg validation** — new pattern beyond Episode 1's `args.get(...)` defaults. Per-tool `BaseModel` with `Field(..., description=...)` + `Literal` enums catches LLM-malformed args before they hit Postgres and produces a clean `ValidationError` we can feed back to Gemini as a tool-error turn.
- **Manual Gemini tool loop** — keep `automatic_function_calling=disable` (Episode 1's existing pattern). Required for per-tool `@traceable` spans, SSE event forwarding, and the deliberate single-tool-per-turn discipline.
- **Pinned versions:** `google-genai>=1.30,<2.0` (currently unpinned in `requirements.txt`), `pydantic>=2.5,<3.0` (currently unpinned). No frontend pins change.
- **Frontend:** No `react-arborist` / `react-complex-tree` / `dnd-kit` / `react-dnd`. Native HTML5 drag-drop is sufficient because the "single-item only" Out-of-Scope decision keeps drag semantics simple.

Detail in `STACK.md`.

### Expected Features

The feature surface splits cleanly into two parallel tracks: agent-tool surface (what the LLM calls) and user-UI surface (the file explorer panel). Both are P1 for Episode 2 v1; they share the same schema foundation but can be built in parallel once the API contract is locked.

**Must have (table stakes — Episode 2 v1):**

- **[Tool]** Five precision tools: `tree` (with `path`, `max_depth`, count summaries, scope arg), `glob` (`**` and `*` semantics + `type` arg), `grep` (regex, case-insensitive flag, multiline flag, output_mode `content`/`files_with_matches`/`count`, `-C` context, line numbers, path scope), `list_files` (single-level, folders-then-files-alpha order), `read_document` (offset/limit, line-numbered output via arrow form `{n}→{content}`, newline clamp, accepts `path` OR `document_id`).
- **[Tool]** `search_documents` extended with optional `folder_path` prefix filter — LLM-driven scope, **not** added to the UI metadata-filter bar.
- **[Tool]** All tools default to `scope='both'` (global ∪ user) with `'user'`/`'global'`/`'both'` override.
- **[Tool]** `explore_knowledge_base` sub-agent — isolated context, multi-turn tool loop, returns compact summary, streams `sub_agent_*` SSE events.
- **[Both]** Schema additions: `documents.folder_path TEXT NOT NULL DEFAULT '/'`, `documents.scope TEXT NOT NULL DEFAULT 'user'` (CHECK constraint), `documents.content_markdown TEXT`, thin `folders` table; backfill existing Episode 1 docs to `folder_path='/'`, `scope='user'`.
- **[UI]** Two-section tree (Shared / My Files), expand-collapse with persisted open state, folder CRUD (create empty / rename / delete with confirm), upload-into-folder, drag-move single document, rename document, breadcrumbs, empty-state placeholder, inline file count per folder, scope badges on documents, right-click context menu (Rename / Move / Delete / Copy path), sub-agent activity card in chat.
- **[Both]** LangSmith traces nested under parent for new tools and Explorer.
- **[Both]** RLS preserved with two-scope union read pattern + admin-only writes on `scope='global'`.

**Should have (differentiator — ship in v1 if budget allows):**

- **[UI]** Mention chip in chat input (`@/path` autocomplete from current tree) — bridge between explorer and chat; reduces hand-typed-path errors.

**Defer (v2+):**

- Multi-select + bulk move/delete (explicitly Out of Scope this Episode)
- Auto-organize: LLM-suggested folder structure for flat KBs
- Folder-level permissions / sharing with specific users (third scope)
- Trash bin / soft-delete with restore
- Local-folder mount / sync, connectors (Drive/S3/Dropbox)
- Folder-change audit log

**Anti-features to actively reject:**

- Tree-search box in the explorer panel — competes with the agent as the search surface
- In-app find-in-files panel — same reason
- In-app document viewer — same reason; `read_document` is the surface
- Folder filter as a UI dropdown in `MetadataFilterBar` — folder is structural, metadata is content classification; mixing them muddles both
- Symlinks / cross-folder document references — single canonical location only
- Always-on Explorer sub-agent — defeats the LLM-agency principle
- Drag-from-desktop directly onto a tree folder — mixes file-input with positional drop target
- Realtime tree updates — violates project rule (polling, not Realtime)
- Folder-icon-from-name heuristics — brittle, localization-breaking
- Move-to-trash / soft-delete — out of phase scope

Detail in `FEATURES.md`.

### Architecture Approach

The architecture is **additive** — no Episode 1 component is rewritten. Two new backend services (`folder_service.py`, `exploration_tools.py`), one new router (`folders.py`), targeted edits to `openai_client.py` (register five new tools, add five new dispatch arms), `sub_agent.py` (add `run_explorer_sub_agent`), `ingestion.py` (capture and persist `content_markdown` from Docling's existing markdown export, propagate `folder_path`/`scope` to chunks), `record_manager.py` (dedup key includes `(scope, folder_path)`), and `messages.py` (forward two new `sub_agent_tool_*` SSE event types). The frontend gains a `FileExplorerPanel.tsx` cluster (FolderTree, FolderNode, DocumentNode, Breadcrumbs, ScopeSwitcher, UploadIntoFolder) that **replaces** the current flat `FileUploadPanel`; `MessageList.tsx`'s existing `SubAgentSection` is extended (not forked) to render Explorer's nested tool calls.

**Major components:**

1. **Schema migrations 012–016** — add `scope`/`folder_path`/`content_markdown` columns, drop `user_id NOT NULL` constraint with a CHECK constraint coupling `scope` and `user_id` consistency, create the `folders` table, rewrite RLS policies on `documents`/`document_chunks`/`folders` to use the two-scope union read pattern with separate INSERT/UPDATE policies per scope, extend the search RPCs (`match_document_chunks_with_filters`, `match_document_chunks_hybrid`) with optional `match_folder_path` and `match_scope` parameters defaulting NULL for backwards compatibility.
2. **`folder_service.py`** — single canonical `normalize_path()` chokepoint plus `list_folder`, `create_folder`, `move_document`, `rename_folder`, `delete_folder`. Pure CRUD via supabase-py, no LLM calls. Used exclusively by the HTTP path (folders router + extended files router).
3. **`exploration_tools.py`** — the five new tools, each with hard token-budget caps, scope-tagged result rows, and Pydantic-validated arg models. Used exclusively by the LLM tool dispatch path (`openai_client.py` and Explorer sub-agent).
4. **Explorer sub-agent (`sub_agent.run_explorer_sub_agent`)** — extends the existing `run_sub_agent` shape. Bounded `for turn in range(MAX_TURNS=8)` loop with wall-clock timeout, no-progress detector (tool-name+args-hash), hard exclusion of `analyze_document` from its toolset (no recursive sub-agents), generalized `sub_agent_*` event protocol with new `sub_agent_tool_start`/`sub_agent_tool_done` event types passed through `messages.py:event_generator`.
5. **File-explorer UI cluster** — built from already-installed shadcn/Radix primitives plus HTML5 drag-drop. Two top-level sections (Shared / My Files) rendered simultaneously, not as tabs. Persists open-folder state in `localStorage` per user.

**Patterns to follow:**

- **Two-scope RLS via union predicate:** `((scope='user' AND user_id=(SELECT auth.uid())) OR scope='global')` for SELECT — wrap `auth.uid()` in `(SELECT ...)` per Supabase 2024 best practice (otherwise re-evaluates per row, 10× perf hit). Separate INSERT and UPDATE policies per scope; admin gate is the existing `is_admin()` STABLE SECURITY DEFINER helper from migration 005. Forbid scope mutation in UPDATE: `WITH CHECK (scope = OLD.scope)`.
- **Path-as-string + thin folders side table:** Canonical form is leading slash always, no trailing slash, root is `/`. Empty-folder rows in `folders` only exist for explicitly-created empty folders, **not** auto-created on document insert (sidesteps the concurrent-upload race entirely). Folder rename = transactional prefix update on both tables via Supabase RPC.
- **Tool dispatch — additive, not refactored:** Add five `elif` arms to the existing `stream_response` chain. Resist the urge to refactor to a registry of callables this milestone — the chain is 9 entries after Episode 2 and a refactor blocks parallel work and increases regression surface. Threshold for refactor is ~15 tools.
- **Token-budget truncation in every tool:** Hard char cap (12K–16K) per tool result with explicit `[...truncated, N more]` marker. The existing `openai_client.py:567` already caps at 16K — apply the same discipline to all new tools.
- **Generalize SSE protocol now, not later:** Two sub-agents in Episode 2 (`analyze_document`, `explore_knowledge_base`) make this the cheapest moment to parameterize the event payload (`agent_name`, `event`, `payload`) before forking under deadline pressure.

Detail in `ARCHITECTURE.md`.

### Critical Pitfalls

Twelve pitfalls catalogued in `PITFALLS.md`, ranked here by impact × likelihood:

1. **Two-scope RLS scope-leak (Pitfall 1) — RANK 1.** A user inserting `scope='global'` or UPDATE'ing their private doc to `scope='global'` leaks data into the shared KB visible to every authenticated user. **Avoid:** separate INSERT/UPDATE policies per scope; CHECK constraint coupling `scope` and `user_id`; forbid scope mutation entirely (`WITH CHECK (scope = OLD.scope)`); promotion to global is delete + admin re-upload, not in-place. Test matrix in extended `test_rls.py`.
2. **`content_markdown` backfill done wrong (Pitfall 6) — RANK 2.** Reconstructing from chunks via `string_agg` produces ~10% duplicated text (50-word chunk overlap), silently breaking grep line numbers and `read_document` slicing — tests pass on simple cases, fail subtly on complex ones. **Avoid:** re-run Docling against the original Storage blob, NOT reconstruct. Add `content_markdown_status` enum (`null`/`pending`/`ready`/`failed`) and surface it in UI. Backfill is its own gated phase, not a SQL migration. For docs whose source blob is gone, mark `requires_user_reupload` and don't silently skip.
3. **Gemini empty-response on oversized/malformed tool results (Pitfall 8) — RANK 3.** Already happened once in Episode 1 (the SQL tool empty-response bug, fixed in `53ff28d`). Five new tools + Explorer sub-agent multiply the risk surface. **Avoid:** route every new tool through Episode 1's existing layered-fallback wrapper in `openai_client.py` (non-streaming retry → raw tool result yield → error event). Hard 12K char cap per tool result with `[truncated]` marker. Validate JSON structure of any JSON-returning tool. Always emit `done` SSE event even on failure. LangSmith assertion: `len(streamed_tokens) > 0` after `done`.
4. **Path normalization drift (Pitfall 4).** Four-strings-for-one-folder bug: `/projects/floor-plans` vs `projects/floor-plans` vs `projects/floor-plans/` vs `/projects/floor-plans/`. **Avoid:** single canonical form (leading slash, no trailing, root = `/`), DB CHECK constraint enforcing the regex `^/$|^/[^/]+(/[^/]+)*$`, single Python `normalize_path()` helper called by every write path (UI upload, drag-move, rename, backfill, tool arg parsing). The backfill migration assigning Episode 1 docs to `/` is the first place this rule lands — get it wrong here and every subsequent query inherits the bug.
5. **Explorer sub-agent infinite-loop (Pitfall 7).** Broad query → Explorer calls tree → glob → grep → tree again, burning 15+ tool calls and 200K+ tokens without converging. **Avoid:** `for i in range(MAX_TURNS=8)` hard bound (not `while not done`); 60s wall-clock timeout; no-progress detector (tool-name + args-hash repeat → short-circuit); aggressive result-size truncation inside sub-agent; system prompt explicitly states the budget; hard-exclude `analyze_document` from Explorer's toolset (no recursive sub-agents); LangSmith assertion that Explorer spans never exceed the cap.

Other pitfalls covered in detail: `tree` context blow-up (P2), `grep` perf collapse without pg_trgm (P3), folder deletion orphans/cascade (P5), `read_document` line-numbering edge cases — CRLF, Unicode, single-long-line, off-by-one (P9), concurrent upload race on folders table (P10), scope confusion in answers when `scope='both'` (P11), SSE protocol fork between sub-agents (P12).

Detail in `PITFALLS.md`.

## Implications for Roadmap

Based on consensus across all four research streams, the roadmap should follow a strict early sequence (schema and backfill must land before tools) and then parallelize aggressively. The build order below maps directly to research findings — the dependency graph from `ARCHITECTURE.md` Build Order, the pitfall-to-phase mapping from `PITFALLS.md`, and the feature dependency graph from `FEATURES.md` all converged.

### Phase A: Schema Foundation + Path Normalizer + RLS

**Rationale:** Every downstream phase depends on `folder_path`, `scope`, `content_markdown`, the `folders` table, two-scope RLS, `pg_trgm`, and the `normalize_path()` helper. This is the keystone phase across all four research streams. Five small migrations (012–016) ordered by dependency, not one mega-migration. Pitfalls 1, 3, 4, 10 (the highest-rank scope-leak, perf collapse, path drift, concurrent-upload race) are all addressed here.

**Delivers:**
- Migration 012: `documents.folder_path` + `documents.scope` columns (+CHECK constraint coupling scope/user_id, drop `user_id NOT NULL`); same on `document_chunks`.
- Migration 013: `documents.content_markdown TEXT` + `content_markdown_status` enum.
- Migration 014: `folders` table with unique `(scope, COALESCE(user_id,'00..0'), path)` constraint.
- Migration 015: drop+recreate read/insert/update/delete RLS policies on `documents`/`document_chunks`/`folders` using two-scope union; `(SELECT auth.uid())` form throughout; separate INSERT/UPDATE per scope; `WITH CHECK (scope = OLD.scope)` forbidding scope mutation; `is_admin()` STABLE SECURITY DEFINER helper.
- Migration 016: `pg_trgm` extension + GIN trigram index on `content_markdown` and on `folder_path`; `text_pattern_ops` btree on `folder_path`; extend `match_document_chunks_with_filters` and `match_document_chunks_hybrid` RPCs with optional `match_folder_path` and `match_scope` parameters (NULL defaults preserve existing call sites).
- `app/services/folder_service.py:normalize_path()` as the single canonicalization chokepoint.
- Backfill: Episode 1 docs land at `folder_path='/'`, `scope='user'` (automatic via DEFAULT).
- Extended `test_rls.py` covering the cross-user × cross-scope SELECT/INSERT/UPDATE/DELETE matrix.

**Avoids:** Pitfalls 1 (RLS scope-leak), 3 (grep perf — index lands here), 4 (path drift — CHECK constraint and normalizer land here), 10 (concurrent-upload race — unique constraint lands here).

### Phase B: content_markdown Backfill (Gated)

**Rationale:** Re-run Docling against original Storage blobs to populate `content_markdown` for existing Episode 1 documents. This is the hidden critical path — `grep` and `read_document` cannot ship until this lands, and the seemingly cheaper "stitch from chunks" shortcut produces ~10% duplicated text that silently corrupts line numbers (Pitfall 6). Cannot be a follow-up.

**Delivers:**
- `ingestion.py` extension: capture Docling's markdown export (currently produced and discarded after chunking) and persist to `documents.content_markdown` on every new upload.
- `backend/scripts/backfill_content_markdown.py`: paginates `documents WHERE content_markdown IS NULL`, downloads original blob from Storage, re-runs Docling, updates row. Idempotent, throttled via existing `_ingestion_semaphore`. Logs success/failure/missing-blob counts.
- Status surface: `content_markdown_status` populated to `pending`/`ready`/`failed` per row; surfaced in the file-explorer UI as a small badge so users know which docs aren't yet grep-able.
- Tools (`grep`, `read_document`) explicitly surface `status: 'pending_reindex'` rather than silently skipping NULL `content_markdown` rows.
- Decision documented: docs whose original Storage blob is GC'd are marked `requires_user_reupload`.

**Avoids:** Pitfall 6 (silently broken text from chunk-stitching).

### Phase C: Folder Service + Folders Router + Extended Files Router

**Rationale:** Pure CRUD layer; depends only on Phase A schema. Parallel-safe with Phases D and E once API contracts are locked.

**Delivers:**
- `app/routers/folders.py` with GET/POST/PATCH/DELETE endpoints, admin gate for `scope='global'` writes (reuses Episode 1's `get_admin_user` dependency).
- `app/services/folder_service.py` with `list_folder`, `create_folder`, `move_document`, `rename_folder`, `delete_folder`. Folder rename = transactional prefix update on `documents.folder_path` AND `folders.path` via Supabase RPC. Folder delete rejects non-empty (returns structured `{error: "FOLDER_NOT_EMPTY", document_count, subfolder_count}`); cascade is a separate explicit endpoint.
- Extended `app/routers/files.py`: `POST /api/files/upload?folder_path=...&scope=...`, `PATCH /api/files/{id}` for rename and folder_path move.
- `record_manager.py` dedup key extended to `(scope, user_id, folder_path, file_name, hash)` so the same file in two folders is allowed but the same file twice in one folder still dedups.
- Tests: empty-folder-required-for-delete, concurrent-upload-no-orphan, rename-moves-all-children.

**Avoids:** Pitfall 5 (folder-delete orphans/silent cascade), Pitfall 10 (concurrent upload race — paired with the unique constraint from Phase A).

### Phase D: Five Exploration Tools (Parallel-Safe)

**Rationale:** Each tool is independently testable against a fixture supabase. Build order within the phase: `list_files` (simplest) → `tree` → `glob` → `read_document` → `grep` (most complex). All five share a Pydantic-validation module for tool args — that shared module lands first.

**Delivers:**
- `app/services/exploration_tools.py` with `tree`, `glob`, `grep`, `list_files`, `read_document` functions returning Pydantic dataclasses → JSON-serialized.
- Per-tool Pydantic `BaseModel` for arg validation with `Literal["user","global","both"]` for scope, `Field(..., ge=1, le=...)` for numeric bounds, regex pattern validation for `path` (matches the canonical form CHECK).
- Hard token-budget caps: `tree` `max_depth` server-capped at 4–6, max 500 entries with `[N more folders, M more docs — narrow path or increase max_depth]` summaries; `grep` max 50 hits with `±2` line context; `read_document` 1-based offset, default `limit=2000` lines, hard cap 5000, arrow-form `{n}→{content}` output, `\r\n` normalized at ingestion, line-by-line slicing (never char/byte), UTF-8 codepoint-safe last-line truncation, returns `start_line`+`end_line` for verifiability.
- Every result row carries `scope: 'user' | 'global'` (no exceptions) — surfaces scope through to the LLM and through to source citations.
- `grep` enforces statement timeout (`SET LOCAL statement_timeout = '5s'`), rejects pathological regexes (`(.*)+`), uses `LATERAL regexp_split_to_table` line-split pattern with ILIKE pre-filter for index acceleration.
- LangSmith `@traceable(run_type="tool")` on each tool function.
- Tests: 200-folder fixture for `tree` truncation; 5000-doc fixture for `grep` perf (assert `Bitmap Index Scan` in EXPLAIN); Windows-CRLF / Unicode / single-long-line / mixed-ending fixtures for `read_document`; adversarial-payload fixtures for empty-response guard.

**Avoids:** Pitfalls 2 (tree blow-up), 3 (grep perf), 8 (Gemini empty-response — every tool routed through Episode 1's layered-fallback wrapper), 9 (read_document line drift), 11 (scope confusion — every result row tagged).

### Phase E: search_documents folder_path Filter

**Rationale:** One-arg extension to an existing tool. Trivial; can ship anytime after Phase A. Grouped here for visibility but parallel-safe with Phase D.

**Delivers:**
- `search_documents` schema extended with optional `folder_path` and `scope` parameters (descriptions clarify defaults).
- `retrieve_chunks()` passes them through to the extended RPC; NULL → no extra WHERE clause → existing behavior preserved.
- System-prompt update describing when to use the new args.

### Phase F: Explorer Sub-Agent + SSE Protocol Generalization (Coupled)

**Rationale:** Do these together, not separately — Pitfall 12 is specifically the cost of bolting Explorer onto Episode 1's bespoke `sub_agent_*` events under deadline pressure. Generalize the protocol when adding the second sub-agent or pay forever. Depends on Phase D.

**Delivers:**
- `sub_agent.run_explorer_sub_agent()` — multi-turn loop with `MAX_TURNS=8` for-bound, 60s wall-clock timeout, no-progress detector (tool-name+args-hash repeat), tool budget surfaced in system prompt, hard exclusion of `analyze_document` from the toolset.
- Generalized SSE event payload: `{type: 'sub_agent', agent_name: 'analyze_document'|'explore_knowledge_base', event: 'start'|'token'|'tool_call'|'tool_result'|'done', payload: ...}`. Emit both old and new event names for one release to not break existing clients; flip frontend to the generalized handler; remove old emissions.
- `messages.py:event_generator` extended to forward `sub_agent_tool_start`/`sub_agent_tool_done`.
- `messages.tool_metadata` JSONB persists Explorer trace so old chats render correctly on reload.
- LangSmith `@traceable(run_type="chain")` on Explorer entry; tool calls inside become nested children spans (not flat siblings).
- Frontend `SubAgentSection` extended (recursive, not forked) to render the nested tool rows.

**Avoids:** Pitfalls 7 (infinite loop), 12 (SSE protocol fork).

### Phase G: File-Explorer UI Cluster

**Rationale:** Replaces the flat `FileUploadPanel` in `Chat.tsx`. Parallel-safe with Phases C–F once API contracts are locked. Uses only already-installed Radix-backed shadcn primitives plus native HTML5 drag-drop.

**Delivers:**
- `FileExplorerPanel.tsx` root component.
- `FolderTree.tsx` (recursive renderer), `FolderNode.tsx`, `DocumentNode.tsx`.
- `Breadcrumbs.tsx`, `ScopeSwitcher.tsx` (Shared / My Files as two simultaneous sections, not tabs).
- `UploadIntoFolder.tsx` (wraps existing upload, adds `folder_path`+`scope` to payload).
- Right-click `ContextMenu` (Rename / Move / Delete / Copy path).
- Persist open-folder state in `localStorage` per user.
- Empty-folder placeholder, scope badges on documents, inline file count per folder.
- Empty-state copy for new Shared (admin hasn't curated yet).
- Drag-move with shadcn-style drop indicator (horizontal line "between" vs folder-highlight "into"); confirm-on-cross-scope move modal.
- Keyboard navigation (arrow keys for tree expand/collapse) — matches Claude Code / VS Code expectations from the target audience.
- `MessageList` sub-agent activity card extended for Explorer trace rendering.
- Playwright e2e additions in `e2e/full-suite.spec.ts`.

### Phase H: Tests, Admin Polish, Observability

**Rationale:** Final hardening. Some test coverage lands incrementally in earlier phases; this phase ensures full e2e coverage including LangSmith trace assertions and the "looks done but isn't" checklist from `PITFALLS.md`.

**Delivers:**
- `backend/scripts/test_all.py` modules: `test_folders`, `test_exploration_tools`, `test_explorer_sub_agent`, `test_two_scope_rls`. Includes the cross-user × cross-scope matrix, concurrent-upload safety, transactional folder rename, adversarial-payload empty-response guards.
- Admin global-scope upload affordance: simplest implementation is to reuse the same file-explorer panel and surface global-scope write actions only when `isAdmin === true`. Alternative is a separate "Shared Knowledge Base" admin page.
- LangSmith trace assertions in CI: Explorer span never exceeds 8 tool-call children; tool result size < 12K chars; non-empty assistant tokens after every `done` event.
- `global_audit_log` table — every global-scope write logged with `admin_id`, action, before/after.
- Documentation: `PROGRESS.md` update; `CLAUDE.md` extension if new conventions emerge.

### Phase Ordering Rationale

- **Phase A (schema) blocks everything.** All four research streams independently arrived at this. Tools depend on columns; columns depend on RLS; RLS depends on the admin pattern. The five small migrations are individually reviewable and revertable; one mega-migration would be a single-point-of-failure for the whole milestone.
- **Phase B (backfill) blocks tools that read `content_markdown` (`grep`, `read_document`).** Cannot ship as a follow-up because the alternative (NULL `content_markdown` → silent skip) makes the new tools half-broken on the corpus that matters most (existing user data).
- **Phases C, D, E parallelize once Phase A lands.** Folder service is independent of tools; tools are independent of each other (via shared Pydantic module); search_documents extension is one arg.
- **Phase F (Explorer + SSE generalization) depends on Phase D.** The Explorer composes the five precision tools.
- **Phase G (UI) parallelizes with C–F once API contracts are locked.** Frontend can build against stub endpoints behind a feature flag.
- **Phase H (tests + admin polish) is final hardening.**
- **Critical path:** A → B → D → F. Everything else can fan out.

### Convergent Recommendations Across All Four Research Streams

These were independently arrived at by all four researchers and are the most load-bearing decisions:

1. **Schema must come first** — five small migrations, not one mega-migration.
2. **`content_markdown` backfill is its own gated phase** — re-run Docling, NOT reconstruct from chunks.
3. **Path normalization helper + DB CHECK constraint must land in the schema phase** — single canonicalization point.
4. **Two-scope RLS pattern:** `((scope='user' AND user_id=(SELECT auth.uid())) OR scope='global')` for SELECT, separate INSERT/UPDATE policies per scope, admin gate via `is_admin()` helper, forbid scope mutation.
5. **`pg_trgm` + `text_pattern_ops` are the right Postgres primitives** — NOT `ltree` (charset rejects realistic folder names), NOT FTS for grep (tokenizes/stems, can't match exact identifiers like `MDB-C-G3`).
6. **Reuse Episode 1 patterns** — manual Gemini tool loop with `automatic_function_calling=disable`, `run_sub_agent` SSE generator shape, `@traceable` decoration, layered-fallback empty-response guard. Don't refactor the tool dispatcher to a registry yet.
7. **Explorer must hard-exclude `analyze_document`** (no recursive sub-agents), have `MAX_TURNS=8` for-loop bound, 60s wall-clock timeout, no-progress detector.
8. **Anti-features to lock in:** tree-search box, in-app find-in-files, in-app document viewer, bulk multi-select, folder-level permissions, symlinks, version history.
9. **No new top-level dependencies** — neither backend nor frontend. Pin `google-genai>=1.30,<2.0` and `pydantic>=2.5,<3.0`.
10. **Generalize the SSE sub-agent event protocol when adding Explorer** — pay the small cost now, not the larger cost later.

### Divergences (Minor, Roadmap-Time Decisions)

- **Whether `scope` is a separate tool arg vs. implicit-from-folder_path.** STACK and FEATURES recommend an explicit `scope` arg defaulting to `'both'`; ARCHITECTURE notes that `folder_path` filtering naturally narrows scope when set. Both can coexist (and should), but the system prompt needs to be clear that the explicit arg wins when both are set.
- **Empty-folder cleanup policy.** PITFALLS recommends rejecting non-empty folder delete and providing a separate explicit cascade endpoint; ARCHITECTURE describes the same but does not specify the endpoint shape. FEATURES recommends a confirmation dialog with the count. Decision: keep `folders` row when last document is removed (treat empty folder as a first-class concept).
- **Drag-drop library.** All four agree HTML5 native is sufficient; STACK explicitly says "do not add `react-arborist`/`dnd-kit`/`react-dnd`". Take this as a hard constraint unless the UI build phase surfaces a concrete blocker.
- **Token budget for Explorer's compact summary.** None of the four pinned a number. Open question for planning.
- **Storage retention for re-ingest backfill.** PITFALLS flags that some Episode 1 blobs may be GC'd; ARCHITECTURE describes a `requires_user_reupload` fallback. Treat as operational research item for Phase B.

### Key Constraints for Roadmap (Lift Directly)

- **Stack is locked.** No new top-level dependencies on either side. Pin `google-genai>=1.30,<2.0` and `pydantic>=2.5,<3.0` in Phase A.
- **Schema phase is non-negotiable first.** Five small migrations (012–016), not one mega-migration. Order matters: columns → folders table → RLS rewrite → RPC extension. Each migration is individually reviewable in 10 minutes and individually revertable.
- **Backfill is its own phase, not a SQL migration.** Re-run Docling against original Storage blobs; mark unrecoverable docs `requires_user_reupload`; surface status in UI; don't ship `grep`/`read_document` until backfill is operational.
- **Path canonical form:** leading slash always, no trailing slash, root is `/`. Enforced by DB CHECK + Python `normalize_path()` helper. Every write path calls the helper. Period.
- **RLS pattern:** `((scope='user' AND user_id=(SELECT auth.uid())) OR scope='global')` for SELECT; separate INSERT and UPDATE policies per scope; `WITH CHECK (scope = OLD.scope)` on UPDATE; admin gate via `is_admin()` STABLE SECURITY DEFINER helper; CHECK constraint coupling scope/user_id consistency.
- **Tool dispatch is additive, not refactored.** Add five `elif` arms; do not refactor to a registry until tool count crosses ~15.
- **Every tool has hard token-budget caps with truncation markers.** Server-side caps on `max_depth`, result count, content size — independent of LLM-supplied values.
- **Explorer hard limits:** `for turn in range(MAX_TURNS=8)`; 60s wall-clock timeout; no-progress detector; hard-exclude `analyze_document` from toolset; isolated context (does not share parent's chat history).
- **SSE protocol is generalized in Phase F**, not bolted on. Emit both old and new event names for one release; migrate frontend; stop emitting old events.
- **Every result row carries `scope`.** Tool results, source citations, frontend badges. No exceptions.
- **Defense in depth:** RLS at DB level + explicit `.eq('user_id', ...)` + `.eq('scope', ...)` filters in app code (the existing service-role-key anti-pattern from CONCERNS.md compounds risk on the new scope axis).
- **Empty-response defense:** Every new tool dispatch path routes through Episode 1's existing layered-fallback wrapper in `openai_client.py`. Always emit `done` SSE event even on failure.
- **Anti-features are anti-features:** tree-search box, find-in-files panel, in-app document viewer, multi-select, folder-level permissions, symlinks, version history, always-on Explorer, drag-from-desktop-to-tree, Realtime tree updates. Reject them on sight.
- **Tests must NEVER delete all user data** (existing CLAUDE.md rule extended to: never let folder-delete cascade to documents in a shared codepath that tests could accidentally invoke).

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Verified against in-tree Episode 1 code (`openai_client.py`, `sub_agent.py`, migrations 003/005/008/010/011); Postgres primitives are textbook; Supabase 2024 RLS perf best-practice (`(SELECT auth.uid())` wrapping) directly cited. |
| Features | HIGH | Tool semantics directly modeled on observed Claude Code tool contracts. UI patterns are conventional file-explorer norms. Anti-features grounded directly in `PROJECT.md` Out of Scope. |
| Architecture | HIGH | Every recommendation names a specific file or table that exists or must be created. Two-scope RLS extends the verified Episode 1 admin pattern from migration 005. |
| Pitfalls | HIGH | Twelve pitfalls grounded in Episode 1 codebase, prior bug history (the SQL tool empty-response bug at `53ff28d` is the directly applicable precedent for Pitfall 8), and CONCERNS.md anti-patterns. |

**Overall confidence:** HIGH

### Gaps to Address

- Token budget for Explorer sub-agent's compact summary output. Decide during Phase F planning.
- Empty-folder cleanup policy. Recommended: keep `folders` row when last document is removed.
- Storage retention for re-ingest backfill. Operational research item for Phase B.
- Drag-drop library beyond HTML5 native. Confirm during Phase G if blocker surfaces.
- Whether `scope` is a separate tool arg vs. implicit-from-folder_path. Decide during Phase D planning.
- Token budget defaults for `tree`/`grep` tools. Acceptable to defer to first iteration of Phase D.
- Whether to add `grep` named-capture-group output. Decide during Phase F planning based on Explorer trace observations.

---
*Research completed: 2026-05-01*
*Ready for roadmap: yes*
