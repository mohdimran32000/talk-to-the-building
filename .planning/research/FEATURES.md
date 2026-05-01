# Feature Research

**Domain:** Agentic exploration over a managed knowledge base (Claude-Code-style tools + file-explorer UI), layered onto an existing multi-tenant RAG app
**Researched:** 2026-04-28
**Confidence:** HIGH for tool semantics (grounded in observed Claude Code tool contracts), MEDIUM for UI patterns (grounded in well-known file-tree conventions), HIGH for what NOT to build (grounded in PROJECT.md Out-of-Scope and Episode 1 constraints)

---

## Scope of This Research

Two paired-but-distinct surfaces are covered:

1. **Agent-Tool Surface** — what the LLM calls (`tree`, `glob`, `grep`, `list_files`, `read_document`, `explore_knowledge_base`, plus the `folder_path` extension to `search_documents`). This is the "Claude-Code-style" exploration capability translated from a filesystem to a Postgres-backed knowledge base.
2. **User-UI Surface** — the file-explorer panel users see in the React app (Shared / My Files tree, expand-collapse, folder CRUD, upload-into-folder, drag-move, rename, etc.).

Each feature below is tagged **[Tool]** or **[UI]** (or **[Both]**) so the roadmap consumer can plan them on parallel tracks.

Episode 1 features (auth, ingestion, hybrid search, metadata filter bar, sub-agent for `analyze_document`, web search, text-to-SQL, admin settings, LangSmith) are explicitly **not** re-researched.

---

## Feature Landscape

### Table Stakes (Users Expect These)

Features whose absence makes the product feel broken. Missing any of these and either the LLM produces low-quality exploration or users complain that "the file explorer doesn't even let me…"

| Feature | Surface | Why Expected | Complexity | Notes |
|---|---|---|---|---|
| **Expandable / collapsible folder tree with persistent open state** | UI | Every file explorer (Finder, Explorer, VS Code, Drive) has this. State must persist across page reloads or it feels broken. | LOW | Persist `openFolders: Set<path>` in `localStorage` per user. Don't auto-collapse on every render. |
| **Two visually distinct top-level sections: "Shared" and "My Files"** | UI | Per Key Decision in PROJECT.md — avoids path-collision ambiguity between scopes. Users need a one-glance read of "is this mine or everyone's." | LOW | Different icon + small badge ("admin-managed" on Shared root). No drag between scopes in this phase. |
| **Folder CRUD (create / rename / delete, with empty-folder support)** | UI | Without create-empty-folder, users can't pre-organize before upload. Without delete, they can't clean up. Without rename, typos are permanent. | MEDIUM | Backend needs the `folders` thin table to track empties. Delete-folder-with-contents needs a confirm dialog ("12 documents will be moved to root" or "deleted"). |
| **Upload-into-folder dialog** (right-click folder → Upload here, or "+" menu inside a folder) | UI | Existing Episode 1 upload lands at root; without this, users must upload then move every file. | LOW | Reuse `FileUploadPanel`; pass `folder_path` through to the create endpoint. |
| **Drag-to-move single document between folders** | UI | Standard explorer affordance. Without it, users do click→menu→pick-folder for every move. | MEDIUM | Single-item only this phase (per Out of Scope). HTML5 drag-drop or a lightweight library. Snap drop targets to folder rows. |
| **Rename document (single-item)** | UI | Companion to folder rename. Users will fix typos and standardize names. | LOW | One field on documents table; affects `file_name` only, not `folder_path`. |
| **Breadcrumbs in the tree header / details pane** | UI | When deep-nested, users lose their place. Standard pattern. | LOW | Click a crumb segment to navigate. Truncate middle when path is long. |
| **Empty-state for folders ("This folder is empty — upload a file")** | UI | Without it, an empty folder looks broken or like a load failure. | LOW | Tiny dashed-border placeholder card. |
| **`tree` returns folder structure with `path` arg + `max_depth` arg** | Tool | The defining shape of the tool. Without `max_depth`, output blows the context window on any non-trivial KB. | LOW | Output as indented text; LLM is good at parsing this. |
| **`tree` count-summary truncation at deep levels** | Tool | Without count summaries (e.g., `+ 47 more files`), the tool either lies by silent truncation or floods context. | MEDIUM | At/below `max_depth`, replace listing with `[14 folders, 86 files]`. Hard byte/token cap with explicit `(truncated)` marker. |
| **`glob` supports `**` recursive and `*` segment wildcards** | Tool | These are universal expectations. `projects/**/floor-plans/*.pdf` should "just work." | MEDIUM | Match against `folder_path + '/' + file_name`. Use Python `fnmatch` for `*`/`?` and a small recursive matcher for `**`. SQL-side LIKE pre-filter for performance. |
| **`grep` supports case-insensitive flag** | Tool | First thing anyone reaches for. Episode 1 retrieval debugging surfaced casing as a real pain. | LOW | Pass to Postgres `~*` operator or compiled regex flag. |
| **`grep` supports a `path` (folder prefix) scope filter** | Tool | Without it, `grep` against the entire global+private corpus is unfocused and slow. | LOW | Same `LIKE 'prefix%'` filter used by `tree`/`glob`. |
| **`grep` returns surrounding line context (à la `-A`/`-B`/`-C`)** | Tool | One-line hits without context force a follow-up `read_document` for every match. Unnecessarily wasteful. | MEDIUM | Slice ±N lines from `content_markdown` per hit. Default `-C 2`. |
| **`grep` returns line numbers** | Tool | The whole point of pairing grep with `read_document` is "give me line N — open it." Without numbers, the chain breaks. | LOW | Already a free byproduct of how line-by-line iteration works. |
| **`read_document` with `offset` + `limit` and line-numbered output** | Tool | Direct Claude-Code analog. Line numbers in output are what makes the tool composable with `grep`. | MEDIUM | `cat -n` style: `   42  some line of markdown`. Newline-boundary clamp on slice. Offset is 1-indexed (matches grep output). Default limit ~2000 lines. |
| **`list_files` returns files + immediate subfolders for a single path** | Tool | The "ls" of the KB. Without it, agent must always `tree` (heavier) just to peek into one folder. | LOW | Single-level only — no recursion. Ordering: folders first, then files alpha. |
| **All tools default to `scope='both'` (global ∪ user)** | Tool | Per Key Decision in PROJECT.md. The user mental model is "my knowledge base includes shared." Anything else surprises the LLM. | LOW | One `scope` param, three values, default `'both'`. |
| **Backfill migration: all existing Episode 1 docs land at `/`** | Both | Without it, Episode 2 ships with users' existing files invisible. | LOW | One-time `UPDATE documents SET folder_path='/' WHERE folder_path IS NULL`. |
| **RLS preserved on all new tables/columns** | Both | Project rule. Global-scope rows readable by all authenticated users; writable only by admins; private rows isolated by user_id. | MEDIUM | New `folders` table needs the same dual-policy pattern; `documents.scope` column changes the existing RLS predicate. |
| **LangSmith tracing for new tools** | Tool | Without it, debugging multi-tool exploration is impossible. Existing traces already exist for old tools — parity is expected. | LOW | `@traceable` on each tool function. |
| **Hard token-budget truncation note in every tool that can produce long output** | Tool | Otherwise the LLM's context window silently fills, hurts later turns, and the agent never knows it was truncated. | LOW | Append `\n\n[truncated: showing first 5000 of N matches]` when limits hit. |

### Differentiators (Competitive Advantage)

These are where this Episode beats a generic "RAG with folders" product. They map to the Core Value: *the agent locates the right piece of information in a large, organized KB without hallucinating across unrelated material.*

| Feature | Surface | Value Proposition | Complexity | Notes |
|---|---|---|---|---|
| **`explore_knowledge_base` Explorer sub-agent (isolated context, returns compact summary)** | Tool | The headline feature. Mirrors Claude Code's Explore subagent. Lets the main agent delegate "find me everything about X" without polluting its own context with raw tool output. | HIGH | Same `run_sub_agent` shape used by `analyze_document` in Episode 1. Different system prompt + access to tree/glob/grep/list/read tools. Streams `sub_agent_*` SSE events for UI visibility. Returns a structured summary (paths cited, snippets, confidence note). |
| **Sub-agent traces nested under main-agent trace in LangSmith** | Tool | Episode 1's existing sub-agent already does this; users will expect Explorer to appear the same way — drill into the parent trace, see the sub-agent's tool calls inline. | LOW | Pass parent run_id; reuse existing pattern. |
| **`search_documents` extended with `folder_path` prefix filter (LLM-driven, not UI-driven)** | Tool | Lets the agent scope semantic search when context already established a folder ("now find me everything similar to this in `/projects/atlas/`"). Better precision than pure embedding similarity over the full corpus. | LOW | One arg added to existing tool. SQL-side `folder_path LIKE 'prefix%'` joined to existing RPC. |
| **Scope-aware tools with `scope='user' \| 'global' \| 'both'` override** | Tool | The agent can reason about "this is a personal task, only look at my files" or "this is policy, look at shared." A flat single-scope model can't express that. | LOW | One enum arg per tool; one `WHERE` clause. |
| **Count summaries on `tree` (folders/files per branch)** | Tool | Even at depths within budget, `[3 folders, 41 files]` next to a folder name is a free orientation cue for the agent. | LOW | `SELECT COUNT(*)` per branch during traversal. |
| **`grep` supports multiline regex / cross-line patterns** | Tool | Markdown documents have headings followed by content; the most useful queries (`## Section\n[\\s\\S]*?keyword`) span lines. Single-line grep misses these. | MEDIUM | A `multiline: bool` flag, similar to ripgrep's `--multiline-dotall`. Apply pattern with re.DOTALL when set. |
| **`grep` `output_mode`: `content` / `files_with_matches` / `count`** | Tool | The Claude Code grep tool's three modes are surprisingly useful — "just tell me which files contain X" is much cheaper than fetching all hits. | LOW | A union of three behaviors over the same SQL scan. |
| **`glob` filtered by file extension as a first-class param** | Tool | `glob "**/*" type:"pdf"` is more idiomatic than parsing patterns; the agent uses it more reliably. | LOW | Optional `type` arg in addition to `pattern`. Defaults: pattern wins if both set. |
| **Scope badges on documents in the tree (a tiny "shared" or "private" pip)** | UI | After moves and copies, users lose track of what's shared. A 6×6px badge prevents "wait, did I just put my draft in Shared?" panic. | LOW | One CSS class driven by `documents.scope`. |
| **Inline file-count on every folder in the tree** | UI | "Projects (47)" is the orientation cue users actually use to navigate. Beats expanding-to-find-out. | LOW | One JOIN on documents per folder render. Cache per session. |
| **Right-click context menu (Rename / Move… / Delete / Copy path)** | UI | "Copy path" specifically is what makes the tree useful as input to chat ("look in `/projects/atlas/floor-plans/`"). | MEDIUM | Standard radix-ui or shadcn `ContextMenu`. "Copy path" puts `folder_path/file_name` on clipboard. |
| **"Mention" chip in the chat input that drops a folder path or file path into the message** | UI | The bridge between explorer and chat. Without it, users hand-type paths and typo-fail. | MEDIUM | Slash-command or `@` trigger; suggest from current tree. Inserts `[/path/to/file]` token the agent can recognize. Out-of-scope-adjacent — flag but recommend if budget allows. |
| **Sub-agent activity card in the chat UI ("Explorer is investigating…")** | UI | Episode 1 already has tool-activity rendering; Explorer sub-agent should plug in cleanly with a distinguishing label and a collapsed view of its internal tool steps. | LOW | Reuse `ToolActivity` component; new event types `sub_agent_tool_start`/`done` with `agent: 'explorer'`. |
| **`tree` and `list_files` order: folders before files, alphabetic within each** | Tool | Stable, predictable output. The LLM's pattern recognition gets better when format is consistent across calls. | LOW | One ORDER BY on each query. |
| **Tools never return raw HTML — only canonical markdown from `content_markdown`** | Tool | Episode 1's retrieval debugging session showed mid-table HTML splits hurt precision. New tools dodge this entirely by reading from the cleaned-markdown column. | LOW | Already implied by the schema decision; document it as a contract. |
| **`read_document` accepts `path` OR `document_id`** | Tool | Path is what the LLM has after `tree`/`glob`; document_id is what it gets back from `search_documents`. Supporting both makes the tool composable with both retrieval styles. | LOW | One disambiguation in input parsing; same code path after lookup. |

### Anti-Features (Commonly Requested, Often Problematic)

Things that *look* attractive — they exist in Finder, in VS Code, in Notion — but pollute this Episode's experience or violate constraints. Documented so they don't sneak in.

| Feature | Why Requested | Why Problematic | Alternative |
|---|---|---|---|
| **Multi-select + bulk move/delete in the explorer** | "I want to clean up 30 files at once." | (a) Out of Scope per PROJECT.md. (b) Bulk RLS-aware operations are a different transactional pattern than single-item; mixing them this phase risks data-integrity bugs. (c) Drag semantics with multi-select introduce keyboard-modifier UX (shift-click range, ctrl-click toggle) that's a whole sub-feature. | Single-item ops only. Defer bulk to a future "Folder Operations" phase. |
| **Drag-from-desktop directly onto a folder in the tree** | "I want to drop a PDF onto Projects/Atlas/ and be done." | (a) Mixes file-input affordance with positional drag-target. (b) Edge cases: dropping onto a leaf vs a folder vs a section header. (c) Browser drag-drop quirks on folders-vs-files. (d) Users miss the upload-progress / dedup / status flow that the existing FileUploadPanel handles cleanly. | Right-click folder → "Upload here" → existing dialog. Or "+" button on hovered folder row. Same code path as Episode 1 upload, just with `folder_path` plumbed through. |
| **Folder-level retrieval as a UI dropdown filter (next to metadata filters)** | "I want to limit chat search to one folder." | (a) Explicitly Out of Scope per PROJECT.md Key Decision. (b) The metadata filter bar is for content classification; folder is structural — mixing them muddles both. (c) The LLM can already self-scope via `folder_path` arg on `search_documents`. UI dropdown duplicates that affordance and creates two sources of truth. | Let the LLM scope via tool args. Users can guide it with a `@/folder/` mention chip in chat. |
| **Symlinks / cross-folder document references** | "I want this contract to appear in both Clients/AcmeCo and Legal/Active." | (a) Explicitly Out of Scope per PROJECT.md. (b) Breaks the simple `folder_path TEXT` model — every read becomes a UNION. (c) Confuses RLS (does scope follow the symlink target or source?). (d) Confuses the agent — `tree` and `glob` would either over-report or under-report. | One canonical location per document. Tags / metadata are the right primitive for cross-cutting groupings. |
| **Folder-change audit log / version history** | "Who moved Q4-numbers.pdf?" | (a) Explicitly Out of Scope. (b) Record Manager already handles content versioning at ingest. (c) Movement audit needs an event-sourced design that's out of scope this phase. | Defer. If demanded later, add an `events` table behind a feature flag. |
| **Per-folder permissions / sharing with specific other users** | "I want to share /projects/atlas with Bob but not Carol." | (a) Explicitly Out of Scope — only two scopes (private, global). (b) Real ACLs need a permissions table + policy resolver + UI for managing them. Whole feature in itself. | Use Shared scope (admin-curated) for group material. Bob and Carol both see Shared. |
| **Local-folder mount / sync** | "Watch my `~/Documents/RAG-corpus/` and auto-ingest." | (a) Explicitly Out of Scope. (b) Adds file-watcher, conflict-resolution, and binary-format-on-disk concerns. (c) Project rule: "manual file upload only." | Manual upload-into-folder. Future phase if validated. |
| **Connectors (Drive, Dropbox, S3) feeding folders** | "Pull everything from my Drive 'Reports' folder into Shared/Reports." | (a) Project rule against connectors / automated pipelines. (b) Each connector is a multi-week integration. (c) Auth, throttling, partial-failure recovery are non-trivial. | Manual upload. |
| **Always-on Explorer sub-agent (runs automatically every turn)** | "Why force the LLM to invoke it? Just always explore first." | (a) Doubles the cost of every turn. (b) Defeats the LLM-agency principle that makes Claude Code's Explore work — the model decides when delegation is worth it. (c) Worse latency on simple queries. | LLM invokes `explore_knowledge_base` when it deems delegation worthwhile. Per Key Decision in PROJECT.md. |
| **`grep` returns full file content for each match** | "Just give me the whole file so I have all the context." | (a) Token-budget catastrophe — one match in a 200-page PDF returns 200 pages. (b) Defeats the point of grep being cheap orientation. (c) Forces the agent to read content it didn't ask for. | Return surrounding-line context (`-C N`) by default; let the agent follow up with `read_document` when it actually wants the full slice. |
| **`tree` with no depth limit "for completeness"** | "I just want to see everything." | (a) Floods context on any real KB. (b) Truthful-when-truncated > complete-when-impossible. | Default `max_depth=3`; require explicit override. Always include count summaries past the depth. |
| **`read_document` returns the entire markdown by default (no offset/limit)** | "It's easier than computing offsets." | (a) Same context-window problem. (b) Encourages the agent to read whole documents when it only needs a section — exactly the Episode 1 retrieval-precision problem we're solving. | Require offset/limit; default `limit=2000` lines; document the contract clearly so the agent uses grep-then-read. |
| **Folder icons that infer "folder type" from name** ("legal" gets a scales icon, "projects" a hammer) | "Looks polished." | (a) Brittle heuristic. (b) Localization breaks it. (c) Adds visual noise that competes with scope badges. | Plain folder icon. Scope and counts carry meaning. |
| **Auto-organize: LLM suggests a folder structure for existing flat docs** | "Help me clean up after backfill." | (a) Distinct feature with its own UX (preview, accept/reject, undo). (b) Risks user trust if it moves things wrong. (c) Out of phase scope. | Manual organization post-backfill. Could be a future "Auto-organize" feature behind a flag. |
| **Tree-search box that fuzzy-matches file/folder names in the explorer panel** | "Quick way to find a file by partial name." | (a) Subtle: the explorer is for navigation; agent tools (glob/grep) are for search. Adding UI search creates two-paths-to-the-same-thing confusion. (b) Pulls scope away from the LLM as the search interface. | If users want to find files by name they can ask the agent — `glob "**/*atlas*"` — which is exactly the new tool surface. |
| **Move-to-trash / soft-delete for folders or documents** | "I might want to undo." | (a) New table or status column. (b) UX for a Trash bin. (c) Out of phase scope. | Confirm dialog on delete. Database-level point-in-time recovery is the recovery mechanism. |
| **Realtime updates of the tree when another session adds files** | "Cool, like Google Drive." | (a) Project rule: polling, not Realtime, for ingestion status — same applies here. (b) Cost / connection-count overhead. (c) Multi-tab sync edge cases. | Refetch tree on focus; manual refresh button; opportunistic refetch after a known mutation. |

---

## Feature Dependencies

```text
[documents.content_markdown column]
        │
        ├──required by──> [grep tool] ──required by──> [explore_knowledge_base sub-agent]
        │                                                       ▲
        ├──required by──> [read_document tool] ─────────────────┤
        │                          ▲                            │
        │                          │ composes-with              │
        │                  [grep returns line numbers]          │
        │                                                       │
[documents.folder_path column + folders table]                  │
        │                                                       │
        ├──required by──> [tree tool] ──────────────────────────┤
        ├──required by──> [list_files tool] ────────────────────┤
        ├──required by──> [glob tool] ──────────────────────────┘
        │
        ├──required by──> [search_documents folder_path filter]
        │
        ├──required by──> [File Explorer UI tree render]
        │                          │
        │                          ├──required by──> [Folder CRUD UI]
        │                          ├──required by──> [Drag-to-move UI]
        │                          ├──required by──> [Upload-into-folder UI]
        │                          └──required by──> [Rename-document UI]
        │
[documents.scope column ('user'|'global')]
        │
        ├──required by──> [Two-section tree (Shared / My Files)]
        ├──required by──> [Scope arg on every tool]
        └──required by──> [Admin-only-write RLS on global rows]

[Backfill migration: existing docs → /]
        │
        └──blocks──> [Episode 2 ship — must run before any tree render]

[Existing run_sub_agent infrastructure (Episode 1)]
        │
        └──reused by──> [explore_knowledge_base sub-agent]

[Existing SSE event stream + ToolActivity component]
        │
        └──reused by──> [Sub-agent activity card in chat UI]
```

### Dependency Notes

- **`grep` and `read_document` require `documents.content_markdown` to be populated** — this is the single most load-bearing schema change. If it's not backfilled for every document, those tools silently miss content. The migration path needs care: either re-run Docling on existing docs (slow, expensive) or stitch together existing chunks to approximate full markdown (overlap-dedup edge cases — see PROJECT.md carryover note). Roadmap should flag this as a Phase-0 / pre-flight item.
- **`tree`, `list_files`, `glob` require `folder_path` populated and the `folders` thin table for empties** — straightforward column add; the only subtlety is that the empties-tracking table must be RLS'd identically to documents.
- **All tools require the `scope` column on documents** — adding it is one column + one RLS policy update; not a hard dependency but lifts the work from "trivial" to "moderate" because RLS changes always need verification tests.
- **Explorer sub-agent requires all five precision tools first** — it composes them; build order is precision tools → Explorer.
- **UI tree requires the backend tree endpoint + scope column** — frontend can be parallel-built once the API contract is locked.
- **`grep`-line-numbers and `read_document`-offset/limit must use the same indexing convention** (1-indexed, line-based, newline-clamped). Off-by-one between them breaks the chain.
- **Scope-badges-in-tree (UI) requires scope column (Both)** — implicit from above but worth calling out: any UI that distinguishes Shared/private depends on a single source-of-truth column on documents.
- **Mention chip (UI) does not strictly require any new backend** — it's purely a UI layer on top of the existing chat input + tree state. Can be built independently if prioritized.

---

## MVP Definition

### Launch With (Episode 2 v1)

The minimum to make "agentic exploration of a knowledge base" feel real, without overreach:

**Backend / Tools:**
- [ ] `documents.content_markdown` + `documents.folder_path` + `documents.scope` columns; thin `folders` table with same RLS — *foundation*
- [ ] Backfill migration: existing docs → `folder_path='/'`, `scope='user'` — *unblocks UI*
- [ ] `tree` tool with `path`, `max_depth`, count-summaries, scope arg — *defining tool*
- [ ] `glob` tool with `**` and `*` semantics, `type` arg, scope arg — *cheap pattern lookup*
- [ ] `grep` tool with regex, case-insensitive flag, multiline flag, output_mode (`content`/`files_with_matches`/`count`), `-C` context, line numbers, path scope, scope arg — *precision lookup*
- [ ] `list_files` tool with single path, scope arg — *cheap directory listing*
- [ ] `read_document` tool with `path` OR `document_id`, `offset`/`limit`, line-numbered output, newline clamp — *deterministic read*
- [ ] `search_documents` extended with `folder_path` filter — *unifies semantic + structural*
- [ ] `explore_knowledge_base` sub-agent with isolated context, summary output, SSE event stream — *headline differentiator*
- [ ] LangSmith tracing for all of the above, sub-agent nested under parent — *operability*

**Frontend / UI:**
- [ ] Two-section tree (Shared / My Files) with expand-collapse + persisted open state — *defining UX*
- [ ] Folder CRUD (create empty / rename / delete with confirm) — *organizational utility*
- [ ] Upload-into-folder (right-click / "+" menu reusing existing FileUploadPanel) — *fundamental flow*
- [ ] Drag-move single document — *standard affordance*
- [ ] Rename single document — *companion to folder rename*
- [ ] Breadcrumbs in details pane — *navigation*
- [ ] Empty-folder placeholder — *correctness signal*
- [ ] Inline file-count on folders — *orientation*
- [ ] Scope badges on documents — *correctness signal*
- [ ] Right-click context menu (Rename / Move… / Delete / Copy path) — *power-user path*
- [ ] Sub-agent activity card in chat (Explorer label, collapsible inner steps) — *visibility*

### Add After Validation (v1.x — same Episode, late additions or fast-follow)

- [ ] Mention chip in chat input (`@/path` autocompletes from current tree) — *bridge between explorer and chat; ship if user testing shows hand-typed paths are common*
- [ ] Tree count refresh on focus (cheap polling on tab focus, not Realtime) — *if users complain about stale counts after upload*
- [ ] `grep` with named capture groups in output — *if Explorer sub-agent benefits from structured matches*
- [ ] Recent-folders shortcut bar in upload dialog — *if user testing shows repetitive folder navigation during upload*

### Future Consideration (v2+ — Episode 3 or later)

- [ ] Multi-select + bulk move/delete (Out of Scope this Episode but the obvious next ask)
- [ ] Auto-organize: LLM-suggested folder structure for flat or messy KBs
- [ ] Folder-level permissions (a third scope between private and global)
- [ ] Trash bin / soft-delete with restore
- [ ] Local-folder mount / sync
- [ ] Connectors (Drive, S3, Dropbox, Notion) feeding into folders
- [ ] Folder-change audit log

---

## Feature Prioritization Matrix

| Feature | Surface | User/Agent Value | Implementation Cost | Priority |
|---|---|---|---|---|
| `tree` tool | Tool | HIGH | LOW | P1 |
| `list_files` tool | Tool | MEDIUM | LOW | P1 |
| `glob` tool | Tool | HIGH | MEDIUM | P1 |
| `grep` tool (with line numbers, context, modes, multiline) | Tool | HIGH | MEDIUM | P1 |
| `read_document` tool (offset/limit, line-numbered) | Tool | HIGH | MEDIUM | P1 |
| `explore_knowledge_base` sub-agent | Tool | HIGH | HIGH | P1 |
| `search_documents` `folder_path` filter | Tool | MEDIUM | LOW | P1 |
| Scope arg on every tool, default `'both'` | Tool | MEDIUM | LOW | P1 |
| `content_markdown` column + populate strategy | Schema | HIGH | MEDIUM | P1 |
| `folder_path` + `scope` columns + thin `folders` table | Schema | HIGH | LOW | P1 |
| Backfill migration for existing docs | Schema | HIGH | LOW | P1 |
| RLS policies (admin write on global, user write on private) | Schema | HIGH | MEDIUM | P1 |
| LangSmith traces on new tools, nested sub-agent | Tool | MEDIUM | LOW | P1 |
| Two-section tree (Shared / My Files), expand-collapse, persisted state | UI | HIGH | LOW | P1 |
| Folder CRUD (create/rename/delete) | UI | HIGH | MEDIUM | P1 |
| Upload-into-folder dialog | UI | HIGH | LOW | P1 |
| Drag-to-move single document | UI | MEDIUM | MEDIUM | P1 |
| Rename document | UI | MEDIUM | LOW | P1 |
| Breadcrumbs | UI | MEDIUM | LOW | P1 |
| Empty-state placeholder | UI | LOW | LOW | P1 |
| Scope badges | UI | MEDIUM | LOW | P1 |
| Inline file-count per folder | UI | MEDIUM | LOW | P1 |
| Right-click context menu | UI | MEDIUM | MEDIUM | P1 |
| Sub-agent activity card in chat | UI | HIGH | LOW | P1 |
| Mention-chip / `@path` autocomplete | UI | MEDIUM | MEDIUM | P2 |
| Tree refetch-on-focus | UI | LOW | LOW | P2 |
| `grep` named-capture output | Tool | LOW | LOW | P2 |
| Multi-select + bulk ops | UI | HIGH | HIGH | P3 |
| Auto-organize LLM-suggested structure | Both | HIGH | HIGH | P3 |
| Soft-delete / Trash | UI | MEDIUM | MEDIUM | P3 |
| Local-folder mount/sync | Both | HIGH | HIGH | P3 |
| Connectors | Both | HIGH | HIGH | P3 |
| Folder-level permissions / per-folder ACLs | Both | MEDIUM | HIGH | P3 |
| Folder-change audit log | Both | LOW | MEDIUM | P3 |

**Priority key:**
- **P1** — Must have for Episode 2 launch
- **P2** — Should have, add late in Episode 2 or fast-follow
- **P3** — Future Episode

---

## Reference Pattern Analysis

| Capability | Claude Code (filesystem) | Common file-explorer UI (Finder/VS Code/Drive) | This Episode (KB-over-Postgres) |
|---|---|---|---|
| `tree` / sidebar | `tree` / `LS` tool with depth limits, ignore-globs, count summaries | Persistent expandable tree, file counts often shown in metadata | `tree` tool (depth + count summary + scope) **and** UI tree with two-section render |
| Find by name | `Glob` tool with `**` recursive | Cmd+P / file-search box | `glob` tool only (no UI search box — anti-feature: keeps the agent as the search surface) |
| Find by content | `Grep` tool (ripgrep-backed): regex, modes (content/files/count), context, multiline | Find-in-files panel | `grep` tool only (no UI find-in-files — same anti-feature reasoning) |
| Open at a position | `Read` tool with `offset`/`limit`, line-numbered output | Click file → editor opens at line | `read_document` tool with `offset`/`limit`/line-numbered output (no in-app document viewer this phase) |
| Delegated investigation | `Explore` / `Task` subagent with isolated context, returns summary | n/a | `explore_knowledge_base` sub-agent (isolated context, summary, nested LangSmith trace) |
| Scope/visibility | `.gitignore`, workspace roots | Per-folder permissions, sharing | Two scopes only (`user`, `global`); admin-write on global; no per-folder ACLs (Out of Scope) |
| Cross-references | symlinks | shortcuts/aliases | None this phase (Out of Scope — single canonical location) |

The agent-tool surface is closely modeled on Claude Code's. The UI surface is closely modeled on conventional file explorers, **minus** the searches and previews that would compete with the agent as the interaction surface. That deliberate split — *the tree is for organizing; the agent is for finding* — is the design thesis of this Episode.

---

## Sources

- `.planning/PROJECT.md` — authoritative requirements + Out of Scope (HIGH confidence; used for Active/Out-of-Scope tagging)
- `.planning/codebase/ARCHITECTURE.md` — existing dispatch model, sub-agent pattern, SSE event types (HIGH confidence; used to predict reuse vs new build)
- `CLAUDE.md` — project rules: no LangChain/LangGraph, polling-not-Realtime, manual-upload-only, RLS on every table (HIGH confidence)
- Observed Claude Code tool contracts (Read with `cat -n` line-numbered output + offset/limit; Glob with recursive `**` and modification-time ordering; Grep with `-A`/`-B`/`-C` context, `output_mode`, `multiline`, `type`, `glob` filter) — directly applicable as the design target for the new tools (HIGH confidence)
- WebSearch attempts blocked in this environment — no external corroboration of secondary sources beyond the above. Confidence on UI conventions (tree/expand/breadcrumbs/right-click) is MEDIUM-grounded-in-industry-norm rather than cited.

---

*Feature research for: Claude-Code-style agentic exploration over a managed knowledge base*
*Researched: 2026-04-28*
