# Phase 6: File-Explorer UI Cluster — Research

**Researched:** 2026-05-10
**Domain:** React + Vite + Tailwind v4 + shadcn/ui (Radix primitives) + Supabase REST + SSE; replacing the flat `FileUploadPanel` with a two-section recursive folder explorer that consumes the Phase 3 folder API and the Phase 5 generalized SSE sub-agent envelope.
**Confidence:** HIGH (every contract in this research was verified by reading the actual shipped backend code at HEAD; no placeholder assumptions about endpoint shapes).

> **No CONTEXT.md exists** for this phase — `/gsd-discuss-phase` was not run. Every decision in this research is a Claude recommendation, not a user-locked decision. The planner should either run `/gsd-discuss-phase` first, or treat the recommendations here as the default and let the user override during plan review.

---

## Project Constraints (from CLAUDE.md)

The following directives from `CLAUDE.md` apply to every plan in this phase. The planner MUST honor these — they override any recommendation in this research that contradicts them.

- **Frontend stack is locked**: React + Vite + Tailwind + shadcn/ui. No Next.js, no CRA, no alternative CSS framework.
- **No LangChain / LangGraph** — irrelevant here (frontend phase) but noted for completeness.
- **All tables have RLS** — Phase 6 must visibly respect scope boundaries (the Pitfall-11 mitigation IS this rule made user-visible).
- **Stream chat responses via SSE; ingestion uses polling** — keep the existing 2s document-status polling loop in the new explorer; do NOT introduce Realtime subscriptions.
- **Module 2+ uses stateless completions** — irrelevant for the explorer panel itself, but the SubAgentSection extension must keep `messages.tool_metadata` as the source of truth for old-chat reload (no client-side cache that survives a hard refresh).
- **Tests live in `frontend/e2e/full-suite.spec.ts`** — TEST-05 additions append to that file (don't create a new spec).
- **Tests must NEVER delete all user data** — any Playwright fixture that creates folders/uploads must clean up only what it created (track by ID), never bulk-delete.
- **Save plans to `.agent/plans/`** — note that the GSD framework uses `.planning/phases/06-file-explorer-ui-cluster/` for plan files; the `.agent/plans/` rule predates the GSD migration. The planner should follow the GSD convention for this phase (consistent with Phases 1–5).

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| UI-01 | `FileExplorerPanel.tsx` replaces flat `FileUploadPanel.tsx` in `Chat.tsx` | Component Architecture §FileExplorerPanel; Per-Requirement Mapping |
| UI-02 | Two top-level sections rendered simultaneously ("Shared" + "My Files"), not tabs | Component Architecture §RootSection (×2 instances, not Tabs primitive) |
| UI-03 | Recursive `FolderTree` with expand/collapse; open-folder state persisted in `localStorage` per user | Library + Primitive Choices §Tree approach; localStorage key contract |
| UI-04 | Folder CRUD via right-click `ContextMenu` (Create / Rename / Delete with confirm) and inline buttons | Library + Primitive Choices §shadcn primitives needed; API Contract §Folders router |
| UI-05 | Upload-into-folder (drop file onto folder, or pick folder before upload) — replaces flat upload | API Contract §POST /api/files/upload?folder_path=&scope=; Component Architecture §UploadAffordance |
| UI-06 | Drag-move single document with shadcn-style drop indicator; confirm-on-cross-scope move modal | Library + Primitive Choices §Drag-and-drop; Pitfall Mitigation §Pitfall 11 |
| UI-07 | Document rename in place | API Contract §PATCH /api/files/{id}; Component Architecture §DocumentRow |
| UI-08 | Breadcrumbs, inline file count per folder, scope badges on documents, `content_markdown_status` badge for pending/failed re-index | Component Architecture §DetailsPane breadcrumbs; §StatusBadges |
| UI-09 | Keyboard navigation (arrow keys for tree expand/collapse) | Library + Primitive Choices §Tree approach (custom keyboard handler); Open Questions Q1 |
| UI-10 | `MessageList` `SubAgentSection` extended (recursively, not forked) to render Explorer's nested tool rows | Pitfall Mitigation §Pitfall 12; Component Architecture §SubAgentSection v2 |
| UI-11 | Admin-only affordance for global-scope writes (visible only when `isAdmin === true`) | API Contract §Admin gate; Component Architecture (gated render via `useAuth().isAdmin`) |
| TEST-05 | Frontend Playwright additions in `e2e/full-suite.spec.ts` for folder tree, drag-move, sub-agent activity card | Validation Architecture §Playwright additions |

</phase_requirements>

---

## Executive Summary

- **Scope is purely frontend.** The backend contracts (folders router, files router PATCH, SSE generalized envelope, `tool_metadata` JSONB shape) all shipped in Phases 3 + 5 and are documented and verified via running tests. Phase 6 builds on stable APIs — no new backend work expected. [VERIFIED: read `backend/app/routers/folders.py`, `backend/app/routers/files.py`, `backend/app/routers/messages.py`, `backend/app/models/schemas.py`].
- **Two simultaneous root sections (not tabs).** Render two independent `<RootSection>` components — one for `scope='global'` ("Shared") and one for `scope='user'` ("My Files") — each with its own subtree, both visible at the same time. Different background tint + icon + badge make scope unmistakable (Pitfall 11). Tabs were explicitly rejected by ROADMAP.md success criterion 1 ("not tabs").
- **Build the tree component in-house, do not adopt `react-arborist`.** The phase requires VS-Code-style keyboard nav, scope-aware rendering, drag-and-drop, context menus, and inline rename — `react-arborist` would force-fit each of these and add a 30KB dep that mostly duplicates Radix primitives the project already pulls in. A ~150-line recursive `FolderNode` component composed with `@radix-ui/react-context-menu` (already on the dep tree via `radix-ui@1.4.3`) is the lower-risk path.
- **Adopt `@dnd-kit/core` + `@dnd-kit/sortable`** for the drag-move requirement. It's the modern React-19-compatible standard, ~10KB minified, has first-class accessibility, and the shadcn ecosystem references it (the official "sortable" community recipe uses it). HTML5 drag-and-drop is not acceptable — it's known-broken across modern browsers for tree UIs and cannot give the "horizontal-line between vs. folder-highlight into" UX the spec demands.
- **`SubAgentSection` extension is the highest-care work and must be done WITHOUT a `if (agentType === 'explorer')` branch** (Pitfall 12 explicit veto). The same component renders `analyze_document` (no `tool_calls`) and `explore_knowledge_base` (with nested `tool_calls`). Recursion seam: the existing per-tool-row component becomes a `<ToolCallRow>` and `SubAgentSection` maps over `tool.tool_calls?.map(call => <ToolCallRow ... />)`. No new component class names; just an internal generalization. The Phase 5 `messages.py` dual-emit window means Phase 6 also OWNS removing the legacy `sub_agent_*` SSE branches in `frontend/src/lib/api.ts` and switching to `parsed.type === 'sub_agent'` (per the locked plan-checker hook in 05-04-SUMMARY.md "Next Phase Readiness").

**Primary recommendation:** Build a ~6-plan phase: (1) `FileExplorerPanel` skeleton with two root sections + folder list/expand/collapse via `GET /api/folders`; (2) folder CRUD via ContextMenu + AlertDialog + inline buttons (consumes the structured 409 from `delete_folder_endpoint`); (3) drag-and-drop via `@dnd-kit/core` with cross-scope confirmation modal; (4) upload-into-folder + document rename in place; (5) `SubAgentSection` recursive extension + frontend SSE switchover from legacy to generalized envelope (with backend cleanup hook per the dual-emit contract); (6) Playwright additions in `full-suite.spec.ts` covering folder tree nav, drag-move, sub-agent card, scope visibility (admin vs non-admin).

---

## Architectural Responsibility Map

Phase 6 is a frontend-only tier. All capabilities live in the **Browser / Client** tier, with the **API / Backend** tier already shipped (Phases 3 + 5). No new backend or DB work is expected.

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Folder tree rendering + expand/collapse state | Browser / Client | — | Pure UI state; localStorage for persistence |
| Folder CRUD HTTP calls | Browser / Client → API / Backend | — | Routes already shipped (`/api/folders`); UI calls them |
| Drag-and-drop interaction + drop indicator | Browser / Client | — | DOM event handling via `@dnd-kit/core` |
| Cross-scope move confirmation | Browser / Client | API / Backend | UI gates the call; backend trigger `forbid_scope_mutation` is bedrock if UI bypassed |
| `isAdmin` gating of global-scope affordances | Browser / Client | — | Visibility only; backend `_require_admin` is the actual security boundary |
| Folder-delete count display | Browser / Client | API / Backend | Backend supplies `{document_count, subfolder_count}` in 409 body; UI renders them |
| `content_markdown_status` badge | Browser / Client | Database | Status field on existing `Document` row; UI just maps enum → badge |
| Sub-agent activity card extension | Browser / Client | API / Backend | Backend persists `tool_metadata.tools_used[].tool_calls[]`; UI consumes |
| SSE envelope switch (legacy → generalized) | Browser / Client | API / Backend | Frontend listens to new shape; backend removes legacy emissions in same release |
| Playwright e2e tests | Browser / Client | — | Test runner exercises full stack via UI |

---

## API Contract (Phase 3 + Phase 5 — verified verbatim from shipped code)

All shapes below were copied or derived from `backend/app/routers/folders.py`, `backend/app/routers/files.py`, `backend/app/routers/messages.py`, and `backend/app/models/schemas.py` at the current `master` HEAD (commit `0f4194e`). These are stable contracts — they do not need confirmation from the user.

### Folders router (`backend/app/routers/folders.py`)

#### `GET /api/folders?path=/&scope=both`

- **Query args:**
  - `path` (string, default `"/"`): canonical folder path (must satisfy `^/$|^/[^/]+(/[^/]+)*$`).
  - `scope` (`"user" | "global" | "both"`, default `"both"`): regex-validated server-side.
- **Response (200):**
  ```jsonc
  {
    "path": "/",                             // normalized
    "documents": [                           // documents AT this path (not descendants)
      {
        "id": "uuid",
        "user_id": "uuid | null",            // null when scope='global'
        "file_name": "string",
        "file_size": 12345,
        "mime_type": "string",
        "status": "pending|processing|ready|failed",
        "error_message": "string | null",
        "content_hash": "string | null",
        "metadata": { /* JSONB */ } ,
        "folder_path": "/",                  // Phase 3 / FOLDER-07 — added
        "scope": "user",                     // Phase 3 / FOLDER-07 — added
        "content_markdown_status": "ready|pending|failed|requires_user_reupload",  // [VERIFIED: column exists per Migration 014; NOT yet in DocumentResponse Pydantic — see Open Questions Q4]
        "created_at": "ISO8601",
        "updated_at": "ISO8601"
      }
    ],
    "subfolders": [                          // immediate-child folder paths only
      "/projects",
      "/notes"
    ]
  }
  ```
- **Empty-state**: returns `{path, documents: [], subfolders: []}` (200, never 404 for a real path that has nothing in it).
- **Error 400**: invalid path (non-canonical, contains `..`).
- **Error 401**: missing/invalid JWT.

#### `POST /api/folders` (FolderCreate body)

- **Body:** `{path: "/projects", scope: "user" | "global"}` (default `"user"`).
- **Admin gate**: `_require_admin` raised IFF `body.scope == "global"` → 403 for non-admins.
- **Response (200):** `FolderResponse` `{id, scope, user_id, path, created_at}` (`user_id` null when scope='global').
- **Idempotency**: backed by `create_folder_if_not_exists` RPC — returns existing row with `action: "exists"` if path already exists at that (scope, user_id) — see `folder_service.create_folder` line 286 onward.

#### `PATCH /api/folders/{folder_id}` (FolderPatch body)

- **Body:** `{new_path: "/projects-renamed"}`.
- **Lookup-then-gate**: 404 if folder doesn't exist; ownership check (CR-02) returns 404 if `scope='user'` and `user_id != caller`; admin gate fires if `scope='global'`.
- **Response (200):** `RenameFolderResponse` = `FolderResponse` + `{documents_updated: int, folders_updated: int}` (counters from the atomic `rename_folder_prefix` RPC).
- **Atomicity**: backend RPC updates both `documents.folder_path` AND `folders.path` for every descendant in a single transaction.

#### `DELETE /api/folders/{folder_id}`

- **Lookup-then-gate**: 404 if not found / not owned; admin gate if `scope='global'`.
- **Response (200) on success:** `{status: "deleted"}`.
- **Response (409) on non-empty folder** (THIS IS THE PITFALL 5 CONTRACT — Phase 6 UI consumer):
  ```json
  {
    "error": "FOLDER_NOT_EMPTY",
    "document_count": 12,
    "subfolder_count": 3
  }
  ```
  Note: this is `JSONResponse(status_code=409, content=...)`, NOT `HTTPException(detail=...)`. The frontend `fetchApi()` helper at `frontend/src/lib/api.ts:19-22` currently throws on `!res.ok` with `body.detail`. **For 409 with structured body, the helper needs to NOT swallow the body** — see Pitfall Mitigation §Pitfall 5.

### Files router (extended in Phase 3 / Plan 05)

#### `POST /api/files/upload?folder_path=/&scope=user`

- **Multipart file** body (existing); two NEW query args:
  - `folder_path` (string, default `"/"`): destination folder.
  - `scope` (`"user" | "global"`, default `"user"`): regex-validated. Admin gate fires if `scope='global'`.
- **Response (200):** `DocumentResponse` (now includes `folder_path` and `scope` fields).
- **Storage path** for global-scope uploads: `documents/global/{document_id}{ext}` (Pitfall F mitigation — backend handles segment automatically; frontend just passes `scope=global`).

#### `PATCH /api/files/{file_id}` (FilePatch body) — NEW endpoint, supports rename + folder move

- **Body:** `{file_name?: "new.txt", folder_path?: "/destination"}` — both optional, but at least one required (empty body → 400).
- **Smuggled `scope` field**: silently dropped by Pydantic v2 (FilePatch model omits scope) — defense in depth alongside Migration 015 trigger.
- **Lookup-then-gate**: 404 if not found / not owned; admin gate if `existing.scope == 'global'`.
- **Response (200):** updated `DocumentResponse`.

### SSE Envelope (Phase 5 / Plan 04 + 05) — DUAL-EMIT WINDOW

Backend `messages.py:event_generator` currently emits BOTH the legacy `{type: "sub_agent_*"}` events AND the generalized `{type: "sub_agent", agent_name, event, payload}` envelope. The contract:

#### Generalized envelope (Phase 6 frontend MUST switch to this)

```jsonc
// sub_agent_start (analyze_document or explore_knowledge_base)
{
  "type": "sub_agent",
  "agent_name": "analyze_document" | "explore_knowledge_base",
  "event": "start",
  "payload": {
    "sub_agent_id": "uuid",            // server-generated per sub-agent invocation
    "document_name": "string",         // analyze_document only
    "question": "string"               // explore_knowledge_base only
  }
}

// sub_agent_tool_start (Explorer ONLY — analyze_document never emits this)
{
  "type": "sub_agent",
  "agent_name": "explore_knowledge_base",
  "event": "tool_start",
  "payload": {
    "tool": "tree" | "glob" | "grep" | "list_files" | "read_document",
    "args": { /* truncated to SSE_ARG_CAP=500 chars total */ },
    "turn": 1                          // 1-indexed Explorer turn number, max 8
  }
}

// sub_agent_tool_done (Explorer ONLY)
{
  "type": "sub_agent",
  "agent_name": "explore_knowledge_base",
  "event": "tool_done",
  "payload": {
    "tool": "string",
    "result_preview": "string",        // capped at 300 chars
    "turn": 1
  }
}

// sub_agent_token (both — running summary text)
{
  "type": "sub_agent",
  "agent_name": "<resolved-server-side from tools_used[-1].tool>",
  "event": "token",
  "payload": { "content": "raw text chunk" }
}

// sub_agent_done (both — final summary)
{
  "type": "sub_agent",
  "agent_name": "<resolved-server-side>",
  "event": "done",
  "payload": { "content": "<sub_agent_result, capped at 300 chars>" }
}
```

#### Legacy envelope (currently still emitted; Phase 6 backend cleanup REMOVES these — explicit hook in 05-04-SUMMARY.md)

```jsonc
{"type": "sub_agent_start",     ...}
{"type": "sub_agent_tool_start", ...}
{"type": "sub_agent_tool_done",  ...}
{"type": "sub_agent_token",      "content": "..."}
{"type": "sub_agent_done"}
```

**Phase 6 plan-checker hook** (locked from 05-04-SUMMARY.md "Next Phase Readiness"): the planner MUST include a task to delete these 5 legacy `yield json.dumps(...)` lines from `backend/app/routers/messages.py` AND the legacy SSE branches from `frontend/src/lib/api.ts:306-323` in the same release that the new envelope frontend ships. The dual-emit window expires with this phase.

### `messages.tool_metadata` shape (Phase 5 / Plan 04 — persisted JSONB for chat reload)

```jsonc
{
  "tools_used": [                                  // ARRAY (Phase 5 changed from [0]-fixed to recursive list)
    {
      "tool": "analyze_document",
      "sub_agent_id": "uuid",
      "tool_calls": [],                            // empty for analyze_document
      "document_name": "phages-overview.pdf",
      "sub_agent_result": "<300-char summary>"
    },
    {
      "tool": "explore_knowledge_base",
      "sub_agent_id": "uuid",
      "tool_calls": [
        { "tool": "list_files",       "args": { /* */ }, "turn": 1, "result_preview": "<300 chars>" },
        { "tool": "search_documents", "args": { /* */ }, "turn": 2, "result_preview": "<300 chars>" }
      ],
      "question": "What does my KB say about phage-CRISPR?",
      "sub_agent_result": "<300-char compact summary>"
    }
  ]
}
```

**Backwards-compat for old chats**: existing `analyze_document` rows from before Phase 5 may have `tool_calls` absent (pre-refactor format). Code reading `tool.tool_calls?.map(...)` (with optional chaining) handles both shapes — no migration needed. The TypeScript interface in `frontend/src/lib/api.ts:39-52` already declares `tool_calls?: Array<...>` as optional.

### `isAdmin` exposure to frontend (existing — verified)

`frontend/src/contexts/AuthContext.tsx:73` exposes `isAdmin: profile?.is_admin ?? false` via `useAuth()`. Phase 6 components import `useAuth` and gate global-scope affordances behind this flag. No backend or auth changes needed.

---

## Component Architecture

### Recommended file structure (additive — no deletions of existing files until Phase 6 closes)

```
frontend/src/components/
├── FileExplorerPanel.tsx         # NEW — top-level panel; replaces FileUploadPanel in Chat.tsx
├── FileUploadPanel.tsx           # KEEP until UI-01 swap; delete in same commit as Chat.tsx swap
├── explorer/                     # NEW — internals of FileExplorerPanel
│   ├── RootSection.tsx           # one of "Shared" / "My Files" — wraps a FolderTree
│   ├── FolderTree.tsx            # recursive tree of FolderNodes
│   ├── FolderNode.tsx            # single expandable folder with its docs + child folders
│   ├── DocumentRow.tsx           # single doc with rename + drag handle + scope badge
│   ├── ContextMenuActions.tsx    # shared CRUD action set (Create, Rename, Delete) for both folders + docs
│   ├── DeleteFolderDialog.tsx    # AlertDialog; renders the 409 doc/subfolder count
│   ├── CrossScopeMoveDialog.tsx  # AlertDialog for the cross-scope drag confirmation
│   ├── Breadcrumbs.tsx           # for the details pane
│   ├── ScopeBadge.tsx            # "Shared" / "Private" pill
│   └── StatusBadge.tsx           # generalizes the existing status badge + adds re-index status
├── ui/
│   ├── context-menu.tsx          # NEW shadcn primitive (install via shadcn CLI)
│   ├── dialog.tsx                # NEW shadcn primitive
│   ├── alert-dialog.tsx          # NEW shadcn primitive
│   ├── badge.tsx                 # NEW shadcn primitive
│   ├── tooltip.tsx               # NEW shadcn primitive
│   ├── separator.tsx             # NEW shadcn primitive
│   └── (existing: button, card, input, label, sonner)
├── MessageList.tsx               # MODIFIED — SubAgentSection extended (recursive)
└── ToolActivity.tsx              # MODIFIED — extract ToolCallRow as the recursive seam
```

### `FileExplorerPanel.tsx` — top-level composition

```tsx
// Consumers pass NO props that aren't already in Chat.tsx
interface FileExplorerPanelProps {
  files: UploadedFile[]                          // existing prop from Chat.tsx
  onUpload: (file: File, folder_path: string, scope: 'user' | 'global') => void
  onDelete: (fileId: string) => void
  onStatusUpdate: (id: string, status: string, error?: string) => void
  metadataSchema?: MetadataFieldDefinition[] | null
}

// Internal state:
//   - openFolders: Map<scope, Set<path>>  — persisted to localStorage per user
//   - selectedFolder: { scope, path } | null  — drives breadcrumbs + upload destination
//   - currentEditingId: string | null  — inline rename mode
```

The panel renders TWO `<RootSection>` instances side-by-side (or stacked vertically — UX detail for the planner):
- `<RootSection scope="global" label="Shared" icon={<GlobeIcon />} />`
- `<RootSection scope="user"   label="My Files" icon={<UserIcon />} />`

Each `RootSection` calls `GET /api/folders?path=/&scope={scope}` once on mount and caches the response. On folder expand, it calls `GET /api/folders?path={folder_path}&scope={scope}` — lazy loading prevents loading the entire tree upfront for a 200-folder corpus.

### `FolderNode.tsx` — recursive seam

```tsx
function FolderNode({ scope, folder, depth, isOpen, onToggle, onContextMenu }: Props) {
  // Wrap in @radix-ui/react-context-menu trigger
  // Render: <chevron> <icon> <name> <count-badge>
  // If isOpen: fetch children via GET /api/folders, render <DocumentRow> for each doc + <FolderNode> for each subfolder (recursion)
  // Keyboard: ArrowRight = expand, ArrowLeft = collapse (handled at the FolderTree level, not here)
}
```

### `SubAgentSection` (in `MessageList.tsx`) — Pitfall-12-compliant extension

Current shape (line 18-50 of `MessageList.tsx`) only handles a single `analyze_document` rendering. The Phase 6 generalization:

```tsx
function SubAgentSection({ tool }: { tool: ToolUsedEntry }) {  // tool from msg.tool_metadata.tools_used[i]
  const [expanded, setExpanded] = useState(false)
  const agentLabel = tool.tool === 'analyze_document'
    ? `Analyzed "${tool.document_name}"`
    : `Explored: ${tool.question}`
  return (
    <div className="border-l-2 border-blue-400/50 pl-3 ml-1 my-2">
      <button onClick={() => setExpanded(!expanded)}>...{agentLabel}</button>
      {expanded && (
        <>
          {tool.tool_calls?.map((call, i) => <ToolCallRow key={i} call={call} />)}    // recursive seam — empty for analyze_document
          {tool.sub_agent_result && <MarkdownContent content={tool.sub_agent_result} />}
        </>
      )}
    </div>
  )
}
```

NO `if (tool.tool === 'explore_knowledge_base')` branches anywhere. The component renders the same shape for both; the difference is that `analyze_document` has `tool_calls: []` (empty) so the `.map` produces nothing.

`<ToolCallRow call={call} />` renders one Explorer-internal tool call:
```tsx
function ToolCallRow({ call }: { call: ToolCallEntry }) {
  const Icon = TOOL_ICON[call.tool]      // folder | file | search | eye
  return (
    <div className="ml-4 flex items-center gap-2 text-xs">
      <Icon className="w-3 h-3" />
      <span className="font-mono">{call.tool}</span>
      <span className="text-muted-foreground italic truncate">{summarizeArgs(call.args)}</span>
      <span className="text-muted-foreground/60">turn {call.turn}</span>
    </div>
  )
}
```

Tool-icon mapping (lucide-react, already a dep):
| Tool | Icon | Rationale |
|------|------|-----------|
| `tree`            | `FolderTree` | matches lucide name |
| `list_files`      | `Folder` | folder listing |
| `glob`            | `FileSearch` | file pattern match |
| `grep`            | `Search` | content search (magnifying glass) |
| `read_document`   | `Eye` | viewer |
| `search_documents`| `Search` | (consistency with main-agent search) |

### Live-streaming Explorer activity (during chat stream)

`MessageList.tsx`'s in-flight `<SubAgentSection>` (line 175-182) currently uses `subAgentDocName`/`subAgentContent` props from `Chat.tsx`. Phase 6 needs to surface the in-flight tool calls (turns 1..8 as they arrive). Recommended state shape addition in `Chat.tsx`:

```tsx
const [liveSubAgentTrace, setLiveSubAgentTrace] = useState<{
  agent_name: string
  question?: string
  document_name?: string
  tool_calls: Array<{ tool: string; args?: any; turn?: number; result_preview?: string; status: 'running' | 'done' }>
  sub_agent_result?: string
} | null>(null)
```

The two SSE callbacks (`onSubAgentToolStart` / `onSubAgentToolDone` — already wired in Phase 5) push into `liveSubAgentTrace.tool_calls`; on `onSubAgentDone`, the trace is committed (or simply allowed to render until the next message). This replaces the flat `toolSteps` push at `Chat.tsx:269-290` (which was Phase 5 minimum-viable plumbing).

### Existing `toolSteps` migration

Phase 5 wrote sub-agent tool calls into the same flat `toolSteps[]` array as main-agent tools, with an `isSubAgent: true` discriminator (`Chat.tsx:269-291`). Phase 6 separates them: `toolSteps` stays for main-agent tools only (search_documents, query_structured_data, web_search, analyze_document outer call); `liveSubAgentTrace.tool_calls` holds Explorer's nested calls. The discriminator becomes structural (different state slot) instead of a boolean flag — cleaner, avoids the `s.isSubAgent && ...` filter pattern.

---

## Library + Primitive Choices

### Tree component — BUILD (don't adopt `react-arborist`)

**Recommendation**: a custom recursive `FolderTree` + `FolderNode` (~150 LOC total) using `@radix-ui/react-context-menu` for right-click and a manual keyboard handler.

| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| `react-arborist` | Built-in keyboard + drag + virtualization; handles 10K nodes | 30KB, opinionated rendering, hard to slot scope-aware visual treatment + custom CRUD context menu, needs adapters for shadcn styling | REJECT — fights the spec |
| `@radix-ui/react-accordion` | shadcn-blessed; keyboard nav free | Designed for flat panels, not nested trees; no immediate-child-only expansion | REJECT — wrong primitive |
| Custom recursive component + Radix context-menu | Full control; small surface; matches every spec point | ~150 LOC of in-house code | **ADOPT** |

**Keyboard nav** (UI-09) implementation plan:
- `tabIndex={0}` on each `FolderNode`'s header button.
- `onKeyDown` handler at the `FolderTree` level (event delegation):
  - `ArrowRight`: if collapsed, expand; if expanded, focus first child.
  - `ArrowLeft`: if expanded, collapse; if collapsed, focus parent.
  - `ArrowUp` / `ArrowDown`: focus prev/next visible row (folders + docs flattened in DOM order).
  - `Enter` / `Space`: toggle expand on folders, open detail pane on docs.

Reference: WAI-ARIA Authoring Practices "Treeview" pattern — implement to match VS Code/Finder. [CITED: https://www.w3.org/WAI/ARIA/apg/patterns/treeview/]

### Drag-and-drop — `@dnd-kit/core` + `@dnd-kit/sortable`

**Recommendation**: `@dnd-kit/core@^6.3.1` + `@dnd-kit/sortable@^10.0.0` [VERIFIED: `npm view` returned these as the current published versions on 2026-05-10].

| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| HTML5 native (drag/drop events) | No dep | Inconsistent across browsers; no a11y; can't render custom drop indicators | REJECT |
| `react-dnd` | Mature | Last meaningful release ~2 years stale; React-19 compat untested; backend-context-provider boilerplate | REJECT |
| `@dnd-kit/core` | Modern, React-19-compat, accessibility-first, ~10KB, supports custom drop indicators trivially | Newer API to learn | **ADOPT** |

**Drop-indicator UX** (UI-06):
- "Between" indicator: render a 2px horizontal `<div>` absolutely positioned at the drop edge using `over.rect` from `useDndMonitor`.
- "Into" indicator: apply a `bg-blue-400/10 ring-1 ring-blue-400/40` class to the target `FolderNode` while it's the active `over`.
- Reference: shadcn community "Sortable" recipe pattern. [CITED: https://github.com/clauderic/dnd-kit#examples]

### shadcn primitives needed

The current install (`frontend/src/components/ui/`) only has `button`, `card`, `input`, `label`, `sonner`. Phase 6 needs SIX new primitives. Install command (run from `frontend/`):

```bash
npx shadcn@3.8.4 add context-menu dialog alert-dialog badge tooltip separator
```

| Primitive | Purpose | Backed by |
|-----------|---------|-----------|
| `context-menu` | Right-click CRUD on folders/docs (UI-04) | `@radix-ui/react-context-menu@2.2.16` |
| `dialog` | Generic modal (folder create form, inline rename fallback) | `@radix-ui/react-dialog@1.1.15` |
| `alert-dialog` | Delete-confirm + cross-scope-move-confirm (UI-04, UI-06) | `@radix-ui/react-alert-dialog` |
| `badge` | Scope badges + status badges (UI-08) | none — primitive Tailwind component |
| `tooltip` | Hover hints on icons | `@radix-ui/react-tooltip` |
| `separator` | Section dividers | `@radix-ui/react-separator` |

`radix-ui@1.4.3` is already installed [VERIFIED: `frontend/package.json`]; the shadcn CLI install pulls only the per-component Radix subpackages it needs. No version conflict expected.

### localStorage persistence shape (UI-03)

```ts
// Key: `fileExplorer:open:${userId}`
// Value: JSON.stringify({ user: ['/projects', '/notes'], global: ['/templates'] })

interface OpenFoldersByScope {
  user:   string[]      // open folder paths in user scope
  global: string[]      // open folder paths in global scope
}
```

- Per-user keying prevents leaking one user's open-folder state to another after sign-out/sign-in on a shared machine.
- JSON-serialized array (not `Set`) for storage; deserialize to `Set<string>` in memory for O(1) lookup.
- Eviction: none — total size is bounded (a power user with 1000 folders open is ~30KB; well under localStorage's 5MB).
- Write strategy: debounce 250ms after each toggle (avoid synchronous IO on every chevron click).

### State management — raw `useState` + `useCallback` (no TanStack Query)

The codebase already uses raw `useState` + `useCallback` + ad-hoc `loadFiles`/`loadThreads` callbacks (`Chat.tsx:45-52, 104-113`). Phase 6 should follow that pattern for consistency. TanStack Query would be an architectural change out of scope for this phase.

**Cache invalidation strategy after CRUD:**
- Folder create: optimistically push to local subtree state; on 200, replace with server row (gives server-assigned `id`); on error, pop.
- Folder rename: optimistic update; on 200, refresh affected subtree from `GET /api/folders?path={parent}` (cheap — ~1 round trip).
- Folder delete: optimistic remove; on 409, restore + show count modal.
- Document upload: use existing `setFiles((prev) => [uploaded, ...prev])` pattern from `Chat.tsx:74`.
- Document move: optimistic move between subtrees; on 200 confirm; on 4xx revert.

---

## Per-Requirement Mapping

| ID | Plan target file(s) | Details |
|----|---------------------|---------|
| UI-01 | `Chat.tsx` (swap import); `FileUploadPanel.tsx` (delete); `FileExplorerPanel.tsx` (new) | Replace the `<FileUploadPanel ... />` JSX at `Chat.tsx:339-346`. Delete `FileUploadPanel.tsx` in the same commit (verifier gate). |
| UI-02 | `FileExplorerPanel.tsx` | Render TWO `<RootSection>` components (`scope="global"` + `scope="user"`); explicitly NOT a Tabs component. Visually distinct via icon + background tint. |
| UI-03 | `RootSection.tsx`, `FolderTree.tsx`, custom hook `useOpenFoldersStorage(userId)` | Recursive expand/collapse via `openFolders: Set<string>` state; persisted via `localStorage[`fileExplorer:open:${userId}`]` |
| UI-04 | `FolderNode.tsx`, `DocumentRow.tsx`, `ContextMenuActions.tsx`, `DeleteFolderDialog.tsx` | Right-click via `<ContextMenu>`; inline buttons (`+` to create child, `⋯` to open menu); Delete uses `<AlertDialog>` with the 409 count rendered |
| UI-05 | `FileExplorerPanel.tsx` (selectedFolder state), upload click flow | Upload button uploads into the currently-selected folder (or `/` if none); drop-on-folder is a stretch goal — can defer to v2 if drag-and-drop scope blows up. PROJECT.md spec: "drop file onto folder, or pick folder before upload" — both required. |
| UI-06 | `FolderTree.tsx` + `DocumentRow.tsx` (DnD wiring); `CrossScopeMoveDialog.tsx` | `@dnd-kit/core`'s `<DndContext>` wraps the panel; each `<DocumentRow>` is a `useDraggable`; each `<FolderNode>` is a `useDroppable`; on drop, check if `source.scope !== target.scope` → open confirmation modal before calling `PATCH /api/files/{id}` |
| UI-07 | `DocumentRow.tsx` (inline edit state), `FolderNode.tsx` (inline edit state) | Click name → input field; Enter commits `PATCH /api/files/{id}` with `{file_name}`; Escape cancels |
| UI-08 | `Breadcrumbs.tsx`, `StatusBadge.tsx`, `ScopeBadge.tsx`, `FolderNode.tsx` (count) | Breadcrumbs: split selected folder path on `/`, render each segment clickable; count: `documents.length + subfolders.length` from `GET /api/folders` response; status badge: extends current `statusBadge()` from `FileUploadPanel:12-26` to also handle `content_markdown_status` |
| UI-09 | `FolderTree.tsx` (keyboard handler) | Single delegated `onKeyDown` at the tree root; ArrowRight/Left/Up/Down behavior per WAI-ARIA treeview pattern |
| UI-10 | `MessageList.tsx` (`SubAgentSection` v2), `ToolActivity.tsx` (extract `ToolCallRow`), `Chat.tsx` (state migration), `frontend/src/lib/api.ts` (envelope switch) | See Pitfall Mitigation §Pitfall 12 below |
| UI-11 | `RootSection.tsx`, `ContextMenuActions.tsx`, `FolderNode.tsx` (context menu) | `useAuth().isAdmin` gates: (a) "Create folder" button on `<RootSection scope="global">`; (b) Create/Rename/Delete entries inside the `scope="global"` ContextMenu; (c) drag-target acceptance on global folders (non-admin's drag is rejected client-side with a toast before the cross-scope dialog shows) |
| TEST-05 | `frontend/e2e/full-suite.spec.ts` | See Validation Architecture below — additions are appended at end of file as `test.describe('FileExplorer', () => {...})` block |

---

## Pitfall Mitigation

### Pitfall 5: Folder delete UX (delete confirmation must show real counts from server)

**Concrete code-level guard:**

`frontend/src/lib/api.ts:fetchApi()` currently throws on `!res.ok` with `body.detail || 'Request failed: ...'` (line 19-22). For the FOLDER 409 case, this would lose the structured `{document_count, subfolder_count}` body. Two approaches:

1. **Preferred:** Add a typed `deleteFolder(id)` function that does NOT use `fetchApi` for the 409 case — it must distinguish 200 vs 409 vs 4xx and return the structured body on 409 instead of throwing. Sketch:
   ```ts
   export async function deleteFolder(id: string): Promise<
     { ok: true } | { ok: false; error: 'FOLDER_NOT_EMPTY'; document_count: number; subfolder_count: number }
   > {
     const token = await getToken()
     const res = await fetch(`/api/folders/${id}`, {
       method: 'DELETE',
       headers: { Authorization: `Bearer ${token}` },
     })
     if (res.status === 200) return { ok: true }
     if (res.status === 409) {
       const body = await res.json()
       return { ok: false, ...body }
     }
     const err = await res.json().catch(() => ({}))
     throw new Error(err.detail || `Delete failed: ${res.status}`)
   }
   ```
2. The `<DeleteFolderDialog>` consumer:
   ```tsx
   const result = await deleteFolder(folder.id)
   if (!result.ok && result.error === 'FOLDER_NOT_EMPTY') {
     // Show: "This folder contains 12 documents and 3 subfolders. Move them first."
     setBlockingMessage(`This folder contains ${result.document_count} document${result.document_count === 1 ? '' : 's'} and ${result.subfolder_count} subfolder${result.subfolder_count === 1 ? '' : 'es'}. Move them first.`)
   }
   ```

**Verifier-gate** for the planner: grep `deleteFolder` in `api.ts` should return a function that branches on `res.status === 409` (not just `!res.ok`). The `<DeleteFolderDialog>` component should reference both `document_count` and `subfolder_count` literally.

### Pitfall 11: Scope confusion (badges + cross-scope move modal)

**Concrete code-level guards:**

1. **Visual differentiation between Shared and My Files:**
   - "Shared" section: `bg-blue-50/50 dark:bg-blue-950/20` background tint; `<Globe />` lucide icon; section header reads "Shared (global)".
   - "My Files" section: `bg-zinc-50/50 dark:bg-zinc-900/30` tint; `<User />` icon; section header reads "My Files".
   - Inside each section, every document row has a `<ScopeBadge scope={doc.scope} />` (small pill: green "Shared" or gray "Private") even though the parent section already implies scope — defense in depth, especially during drag-move when a doc is mid-flight between sections.

2. **Cross-scope drag confirmation:**
   ```tsx
   function onDragEnd({ active, over }: DragEndEvent) {
     if (!over) return
     const sourceDoc: Document = active.data.current
     const targetFolder: Folder = over.data.current
     if (sourceDoc.scope !== targetFolder.scope) {
       // Open <CrossScopeMoveDialog> — requires explicit confirmation
       setPendingCrossScopeMove({ doc: sourceDoc, target: targetFolder })
       return
     }
     // Same-scope move: proceed directly
     await moveDocument(sourceDoc.id, targetFolder.path)
   }
   ```
   The dialog text: "Move 'budget.pdf' from My Files (Private) to Shared? Once moved, all users will be able to read it. Only admins can complete this action."
   
   **CRITICAL**: cross-scope move is currently **NOT supported by the backend**. `PATCH /api/files/{id}` accepts `folder_path` and `file_name` but the `FilePatch` Pydantic model omits `scope`, AND Migration 015's `forbid_scope_mutation` trigger blocks scope changes at the DB layer. The frontend cross-scope move modal must therefore EITHER (a) be a no-op that shows "cross-scope moves are not supported in v1; please re-upload to the target scope", OR (b) the backend gets a NEW endpoint in this phase. **See Open Questions Q2 — this is the highest-impact unresolved decision.**

3. **`isAdmin` gating** (UI-11 — defense in depth alongside backend `_require_admin`):
   - All "Create folder" / "Rename" / "Delete" affordances inside the Shared section are conditionally rendered: `{isAdmin && <ContextMenuItem>Create folder</ContextMenuItem>}`.
   - Drag-target highlighting on global folders is suppressed for non-admins (can't drop into Shared).
   - If a non-admin somehow triggers a write to global (shouldn't be possible via UI, but if it is via DevTools), the backend returns 403 — the UI catches and shows "Admin access required" toast.

### Pitfall 12: SubAgentSection extension without a fork (NO `if (agentType === 'explorer')` branch)

**Concrete refactor pattern** for `MessageList.tsx`:

**BEFORE** (current, Phase 5 shape — only handles single-document analyze case):
```tsx
function SubAgentSection({ documentName, content, isActive, defaultExpanded }) {
  // Renders only the analyze_document case
}
```

**AFTER** (Phase 6 — same component handles both):
```tsx
import type { ToolCallEntry } from '@/lib/api'

function SubAgentSection({ tool, isLive }: { tool: ToolUsedEntry; isLive?: boolean }) {
  const [expanded, setExpanded] = useState(isLive ?? false)
  const label = useMemo(() => {
    if (tool.tool === 'analyze_document') {
      return isLive ? `Analyzing "${tool.document_name}"...` : `Analyzed "${tool.document_name}"`
    }
    return isLive ? `Exploring: ${tool.question}` : `Explored: ${tool.question}`
  }, [tool, isLive])
  return (
    <div className="border-l-2 border-blue-400/50 pl-3 ml-1 my-2">
      <button onClick={() => setExpanded(!expanded)}>{label}</button>
      {expanded && (
        <>
          {/* Recursive seam — empty array for analyze_document, populated for explore_knowledge_base */}
          {tool.tool_calls && tool.tool_calls.length > 0 && (
            <div className="mt-1 space-y-1">
              {tool.tool_calls.map((call, i) => <ToolCallRow key={i} call={call} />)}
            </div>
          )}
          {tool.sub_agent_result && (
            <div className="mt-1 text-xs opacity-80">
              <MarkdownContent content={tool.sub_agent_result} />
            </div>
          )}
        </>
      )}
    </div>
  )
}
```

**Verifier-gate for the planner**: grep `MessageList.tsx` for the strings `'analyze_document'` and `'explore_knowledge_base'` — if EITHER string appears in a conditional (`if (... === '...')`, `?:` ternary on the value, etc.), the gate fails. The only allowed location for those literals is in the `label` computation, which is a presentation-layer string formatting concern (not a behavior fork).

**Backwards-compat for old chats**: `tool.tool_calls?.map(...)` with optional chaining handles pre-Phase-5 rows that lack the field. The TypeScript interface in `frontend/src/lib/api.ts:42` already declares it optional. Tested by reloading any pre-Phase-5 thread that used `analyze_document` — the section should render identically to before.

**SSE envelope switchover** (Phase 6 OWNS this — explicit hook from 05-04-SUMMARY.md):
- Remove the 5 legacy branches in `frontend/src/lib/api.ts:306-323` (lines for `sub_agent_start`, `sub_agent_token`, `sub_agent_tool_start`, `sub_agent_tool_done`, `sub_agent_done`).
- Add ONE generalized branch:
   ```ts
   } else if (parsed.type === 'sub_agent') {
     switch (parsed.event) {
       case 'start':      onSubAgentStart?.({ ...parsed.payload, agent_name: parsed.agent_name }); break
       case 'token':      onSubAgentToken?.(parsed.payload.content); break
       case 'tool_start': onSubAgentToolStart?.(parsed.payload); break
       case 'tool_done':  onSubAgentToolDone?.(parsed.payload); break
       case 'done':       onSubAgentDone?.(); break
     }
   }
   ```
- Same release: remove the 5 legacy `yield json.dumps({"type": "sub_agent_*", ...})` lines from `backend/app/routers/messages.py` (verbatim grep targets in 05-04-SUMMARY.md "Next Phase Readiness" section).

---

## Validation Architecture

> Phase 6 has `nyquist_validation` enabled by default (no `.planning/config.json` opt-out detected).

### Test Framework

| Property | Value |
|----------|-------|
| Framework | Playwright `@playwright/test@^1.58.2` (frontend e2e); existing `backend/scripts/test_all.py` (backend regressions, but no backend changes expected for Phase 6) |
| Config file | `frontend/playwright.config.ts` (existing); existing patterns at `frontend/e2e/full-suite.spec.ts` |
| Quick run command | `cd frontend && npx playwright test e2e/full-suite.spec.ts -g "FileExplorer"` |
| Full suite command | `cd frontend && npx playwright test e2e/full-suite.spec.ts` |

### Phase Requirements → Test Map

| Req | Behavior | Test Type | Automated Command | File Exists? |
|-----|----------|-----------|-------------------|--------------|
| UI-01 | `FileUploadPanel` is gone, `FileExplorerPanel` renders in its place | e2e + grep | `npx playwright test -g "FileExplorer renders"` + `! grep -q FileUploadPanel frontend/src/pages/Chat.tsx` | ❌ Wave 0 |
| UI-02 | Both "Shared" and "My Files" headings visible simultaneously | e2e | `npx playwright test -g "two scope sections"` | ❌ Wave 0 |
| UI-03 | Click chevron → folder expands; reload page → still expanded | e2e | `npx playwright test -g "folder open state persists"` | ❌ Wave 0 |
| UI-04 | Right-click on folder → ContextMenu with Create/Rename/Delete | e2e | `npx playwright test -g "folder context menu"` | ❌ Wave 0 |
| UI-04 (delete-non-empty) | Delete on a non-empty folder shows count from server | e2e | `npx playwright test -g "delete non-empty shows count"` | ❌ Wave 0 |
| UI-05 | Upload while folder selected → file lands in that folder | e2e | `npx playwright test -g "upload into selected folder"` | ❌ Wave 0 |
| UI-06 | Drag doc to another folder → moves; cross-scope opens dialog | e2e | `npx playwright test -g "drag move document"` and `-g "cross scope move dialog"` | ❌ Wave 0 |
| UI-07 | Click doc name → editable input → Enter renames | e2e | `npx playwright test -g "rename document inline"` | ❌ Wave 0 |
| UI-08 | Breadcrumbs visible; doc count badges visible; scope badge visible; re-index status badge visible for pending docs | e2e | `npx playwright test -g "breadcrumbs and badges"` | ❌ Wave 0 |
| UI-09 | Arrow keys: ArrowRight expands, ArrowLeft collapses, Up/Down navigates | e2e | `npx playwright test -g "keyboard navigation"` | ❌ Wave 0 |
| UI-10 | Reload an old chat that used Explorer → tool_calls array renders as nested rows | e2e + manual | `npx playwright test -g "Explorer trace renders on reload"` | ❌ Wave 0 |
| UI-10 (live) | Send a message that triggers Explorer → live nested rows appear during stream | e2e | `npx playwright test -g "Explorer live trace"` | ❌ Wave 0 |
| UI-11 | As non-admin: no "Create" affordance in Shared section; as admin: visible | e2e | `npx playwright test -g "admin global affordances"` | ❌ Wave 0 (NEEDS admin test account) |
| Pitfall 12 invariant | No `if (agentType === 'explorer')` style fork | grep gate | `! grep -E "(===\\s*['\"]explore_knowledge_base['\"]|===\\s*['\"]analyze_document['\"])" frontend/src/components/MessageList.tsx | grep -v label` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `cd frontend && npx playwright test e2e/full-suite.spec.ts -g "<this-task's-test-name>"` (~30s for a focused test).
- **Per wave merge:** `cd frontend && npx playwright test e2e/full-suite.spec.ts -g "FileExplorer"` (full new test block, ~3min).
- **Phase gate:** `cd frontend && npx playwright test e2e/full-suite.spec.ts` (full 26+N suite green) before `/gsd-verify-work`.

### Wave 0 Gaps

- [ ] `frontend/e2e/full-suite.spec.ts` — append new `test.describe('FileExplorer', () => {...})` block with the test cases above. No new spec file (CLAUDE.md rule).
- [ ] **Admin test account** for UI-11 — current `TEST_EMAIL = 'test@test.com'` is a non-admin. Need a second account `TEST_ADMIN_EMAIL` (and password) provisioned in Supabase with `profiles.is_admin = true`. Decision needed: does it exist already? If not, add a Wave 0 task to create it via SQL migration or admin UI step. **See Open Questions Q3.**
- [ ] **Drag-and-drop in Playwright** — `page.dragTo()` works for HTML5 native drag, but `@dnd-kit/core` uses pointer events. Need `page.locator(source).hover() → page.mouse.down() → page.mouse.move(x, y) → page.mouse.up()` pattern. Confirm against the docs at planning time. [CITED: https://playwright.dev/docs/input#dragging]
- [ ] **Sub-agent activity test** — needs an end-to-end flow that triggers Explorer. The test prompt should be deterministic enough to reliably trigger `explore_knowledge_base` (e.g., "Use explore_knowledge_base to find all docs about X"). Plan to use this fixture; alternative is mocking the SSE response, which is more brittle.

---

## Security Domain

`security_enforcement` is presumed enabled (no opt-out detected).

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | Inherited | Existing `AuthContext` + JWT injection in `fetchApi()` |
| V3 Session Management | Inherited | Supabase session storage (browser IndexedDB); no Phase 6 changes |
| V4 Access Control | Yes | `useAuth().isAdmin` gates UI affordances; backend `_require_admin` is the actual security boundary (UI cannot enforce; only display-gates) |
| V5 Input Validation | Yes | Folder paths normalized server-side; client also calls `normalize_path` shape via Pydantic-validated body. **Client must NOT skip server validation** even if it pre-validates — defense in depth. |
| V6 Cryptography | No | No new key material |
| V7 Generator-never-raises | Inherited | Backend SSE generator already wrapped; frontend SSE consumer must continue to swallow malformed JSON gracefully (existing pattern in `api.ts:289-291`) |
| V8 Result-Preview Truncation | Inherited | Backend caps tool results at 300 chars; frontend just renders. No new XSS surface. |
| V13 SSE auth | Inherited | Existing JWT-authenticated endpoint; no Phase 6 SSE auth changes |

### Known Threat Patterns for {React + SSE + Supabase REST}

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| LLM-generated `result_preview` injects HTML/markdown XSS | Tampering / Information disclosure | `react-markdown` with `remarkGfm` (existing) sanitizes by default; do NOT bypass with `dangerouslySetInnerHTML` |
| User pastes `<script>` into folder name → renders unsafely | Tampering | React escapes text by default; ensure folder name is rendered as text (not innerHTML) — should be the default |
| Drag-and-drop bypass: malicious script triggers a `drop` event without user gesture | Spoofing | `@dnd-kit/core` requires actual pointer events; no mitigation needed beyond using the library correctly |
| localStorage tampered → app loads attacker's open-folder list | Tampering | Open-folder list has no security implications (no secrets, no PII paths beyond folder names which are already on-screen). Acceptable risk. |
| Cross-scope move sneaks past UI dialog via direct API call | Elevation of privilege | Backend trigger `forbid_scope_mutation` is bedrock; UI confirmation is UX-only. UI cannot enforce; rely on DB. |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Cross-scope document move requires backend opt-in (currently blocked by `forbid_scope_mutation` trigger). UI either (a) no-ops with a "re-upload to target scope" message, or (b) the planner adds a new backend endpoint. **Defaulting to (a) for v1** — keeps phase scope frontend-only. | Pitfall Mitigation §Pitfall 11 #2 | If user actually wants in-place cross-scope move, this becomes an out-of-phase gap discovered at UAT time — would require a new mini-phase with a Migration to relax the trigger or a "promote-to-global" admin endpoint with audit logging. **HIGH IMPACT** — see Open Questions Q2. |
| A2 | `content_markdown_status` field IS exposed on the `Document` API response. The Pydantic `DocumentResponse` model in `backend/app/models/schemas.py:32-46` does NOT currently include this field. Either it's filtered through `**row` spread in the router (silently included) or it's missing. The frontend `Document` interface in `api.ts:154-167` does NOT include it. | API Contract §GET /api/folders | If missing, the planner needs a tiny backend Plan to add `content_markdown_status: Optional[str] = None` to `DocumentResponse` AND extend the frontend `Document` interface. **MEDIUM IMPACT** — see Open Questions Q4. |
| A3 | A second test user with `is_admin=true` is needed for UI-11 testing. No such user is documented in the existing test setup. | Validation Architecture §Wave 0 Gaps | If missing, UI-11 e2e test cannot run. **MEDIUM IMPACT** — see Open Questions Q3. |
| A4 | The `@dnd-kit/core` recommendation is correct for React 19. Verified via `npm view` that v6.3.1 is the latest published version (2026-05-10), but did not exhaustively check React-19 compat. dnd-kit's GitHub README claims React 19 support. | Library + Primitive Choices §Drag-and-drop | If incompatible, fallback is `react-aria` drag-and-drop primitives (also React-19-compat per Adobe). **LOW IMPACT** — discoverable at install time. |
| A5 | The `documents_updated`/`folders_updated` counters returned by `RenameFolderResponse` are correct names — verified in `backend/app/models/schemas.py:66-71`. UI doesn't strictly need them, but they could power a "renamed: 12 documents updated" toast. | API Contract §PATCH /api/folders/{id} | Cosmetic only. **NIL IMPACT.** |
| A6 | Build-vs-buy decision on the tree component favors building. If the corpus grows past ~500 folders per scope, virtualization (e.g. `react-virtual`) becomes necessary, and `react-arborist` would have given that for free. For v1, building is the right call given the small expected corpus. | Library + Primitive Choices §Tree | If a single user has 1000+ folders, performance regresses; mitigation is to add virtualization later. **LOW IMPACT** — discoverable in UAT. |
| A7 | The "drop file onto folder" upload path (UI-05) can be implemented with native HTML5 file drag (drop event from external source) — separate from the document-move drag (intra-app). The two drag systems coexist by checking `event.dataTransfer.types.includes('Files')` for external file drops vs. dnd-kit pointer drags for internal moves. | Component Architecture §UI-05 | If they conflict, fall back to "click to upload" only and defer drop-on-folder to v2. **LOW-MEDIUM IMPACT.** |

---

## Open Questions

1. **Q1 (UI-09 keyboard nav scope): Does "arrow keys for tree expand/collapse" mean ONLY ArrowRight/ArrowLeft on the focused folder, or full WAI-ARIA treeview navigation (Up/Down between visible nodes, Home/End to first/last, Enter/Space to toggle)?**
   - What we know: ROADMAP.md success criterion 5 says "matching VS Code/Finder conventions" — both have full Up/Down navigation.
   - What's unclear: scope of v1 keyboard support. Minimum is Right/Left expand/collapse (per UI-09 verbatim); maximum is full WAI-ARIA treeview.
   - **Recommendation:** ship Right/Left + Up/Down + Enter/Space in v1 (covers 95% of use); defer Home/End/typeahead to v2. Confirm with user during plan review.

2. **Q2 (Pitfall 11 cross-scope semantics): What is the spec for "cross-scope drag-move"?**
   - What we know: ROADMAP.md success criterion 3 says "cross-scope moves trigger a confirmation modal". REQUIREMENTS.md UI-06 says "drag-move single document with shadcn-style drop indicator; confirm-on-cross-scope move modal". PROJECT.md Out-of-Scope says "in-place scope promotion (private → global) — Security risk; promotion is delete + admin re-upload". Migration 015 trigger blocks scope mutation at DB.
   - What's unclear: the requirement says "modal", PROJECT.md says "not supported". These contradict.
   - **Recommendation:** modal exists, but the action it confirms is "I understand this is not supported in v1; you'll need to delete and re-upload to the target scope" — i.e., the modal is informational + redirects to the right workflow. Alternative: modal triggers a backend "delete + re-upload" macro. Discussion with user is essential here. **HIGHEST-IMPACT OPEN QUESTION.**

3. **Q3 (TEST-05 admin account): Is a second admin Supabase user already provisioned for e2e testing?**
   - What we know: `frontend/e2e/full-suite.spec.ts` only references `test@test.com` (non-admin per the `Settings link not visible for non-admin user` test at L324).
   - What's unclear: whether admin test credentials exist or need to be created.
   - **Recommendation:** Add a Wave 0 task to either (a) create `admin@test.com` via SQL or (b) document existing admin credentials in `frontend/e2e/full-suite.spec.ts`. Confirm with user.

4. **Q4 (UI-08 re-index status visibility): Is `content_markdown_status` already returned by `GET /api/files` and `GET /api/folders`?**
   - What we know: Migration 014 added `content_markdown_status` enum to `documents` table. The `DocumentResponse` Pydantic model does NOT explicitly declare this field. supabase-py's `.select("*")` returns all columns; the FastAPI auto-serialize via `response_model=DocumentResponse` would silently STRIP fields not declared in the model.
   - What's unclear: whether the field is already in the wire response or silently dropped.
   - **Recommendation:** The planner should run `curl -H "Authorization: Bearer $JWT" http://localhost:8001/api/files | jq '.[0]'` once during research-phase verification to confirm. If absent, add a tiny "extend DocumentResponse" plan as Wave 0. Also extend the frontend `Document` interface in `api.ts:154-167`.

5. **Q5 (Inline buttons vs. ContextMenu — UI-04 redundancy):** UI-04 says folder CRUD via right-click ContextMenu AND inline buttons. Concretely: which inline buttons? "+" to create child, "⋯" to open the same menu, or both?
   - **Recommendation:** ship both — `+` (Create) on hover for affordance discoverability + `⋯` (kebab menu) for everything else. Right-click is a power-user shortcut.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Node.js / npm | Frontend build + Playwright | ✓ | ≥18 (existing) | — |
| `@playwright/test` | TEST-05 | ✓ | 1.58.2 | — |
| `@dnd-kit/core` | UI-06 | ✗ | npm-published 6.3.1 | Install via `npm install @dnd-kit/core @dnd-kit/sortable` (no fallback — required) |
| `@radix-ui/react-context-menu` etc. | UI-04 | ✗ (only `radix-ui` umbrella installed) | per-package | shadcn CLI install: `npx shadcn add context-menu dialog alert-dialog badge tooltip separator` |
| Lucide React icons | UI-08 | ✓ | 0.563.0 (existing) | — |
| Backend running on :8001 | Playwright e2e | ✓ (assumed for test runs) | — | Documented in CLAUDE.md |
| Frontend running on :5173 | Playwright e2e | ✓ (assumed) | — | Documented in CLAUDE.md |
| Admin test account in Supabase | UI-11 e2e | ? (Q3) | — | Create via SQL or admin UI |

**Missing dependencies with no fallback:** None — all are installable via `npm`/`shadcn` CLI.

**Missing dependencies with fallback:** None.

---

## Sources

### Primary (HIGH confidence — verified by reading shipped code at HEAD)

- `backend/app/routers/folders.py` (current HEAD `0f4194e`) — folders router endpoints, structured 409 contract, admin gate logic.
- `backend/app/routers/files.py` (HEAD) — extended upload + PATCH endpoints.
- `backend/app/routers/messages.py` (HEAD) — SSE event_generator dual-emit envelope.
- `backend/app/models/schemas.py` (HEAD) — DocumentResponse, FolderResponse, FilePatch, FolderPatch, RenameFolderResponse, FolderCreate.
- `backend/app/services/folder_service.py` (HEAD) — `normalize_path`, `list_folder` semantics.
- `frontend/src/lib/api.ts`, `Chat.tsx`, `MessageList.tsx`, `ToolActivity.tsx`, `FileUploadPanel.tsx`, `AuthContext.tsx` (HEAD) — current frontend baseline.
- `frontend/e2e/full-suite.spec.ts` (HEAD) — existing Playwright patterns.
- `frontend/package.json` (HEAD) — dependency versions.
- `.planning/phases/03-*-04-SUMMARY.md`, `03-*-05-SUMMARY.md`, `05-*-04-SUMMARY.md`, `05-*-05-SUMMARY.md` — upstream phase contracts.
- `.planning/REQUIREMENTS.md`, `ROADMAP.md`, `research/PITFALLS.md`, `PROJECT.md`, `codebase/CONVENTIONS.md`, `codebase/STACK.md`, `codebase/ARCHITECTURE.md` — project doc baseline.

### Secondary (MEDIUM confidence — npm registry verified, project usage not exhaustively confirmed)

- `npm view @dnd-kit/core version` → `6.3.1` (verified 2026-05-10).
- `npm view @dnd-kit/sortable version` → `10.0.0` (verified 2026-05-10).
- `npm view @radix-ui/react-context-menu version` → `2.2.16` (verified 2026-05-10).
- `npm view @radix-ui/react-dialog version` → `1.1.15` (verified 2026-05-10).

### Tertiary (LOW confidence — knowledge cutoff / training data, not verified this session)

- WAI-ARIA Treeview pattern reference (URL cited but not fetched).
- `@dnd-kit/core` React-19 compat (claimed in dep README per training data; not fetched this session).
- shadcn CLI command syntax for adding multiple primitives (claimed correct; not fetched this session).

---

## Metadata

**Confidence breakdown:**

- **API contracts (Phase 3 + Phase 5):** HIGH — every shape was copied verbatim from shipped, tested code at HEAD.
- **Standard stack (`@dnd-kit/core`, shadcn primitives):** HIGH — npm versions verified; library choices match modern React-19 ecosystem patterns.
- **Component architecture:** MEDIUM — recommendations based on existing codebase conventions and Pitfall 11/12 constraints, but no prototype exists yet.
- **Pitfall mitigations:** HIGH — concrete code-level guards traceable to PITFALLS.md and verifiable via grep.
- **Cross-scope move semantics (Q2):** LOW — backend currently blocks; spec ambiguous; needs user decision.
- **Tests:** HIGH — Playwright pattern is established; gap is just the admin test account (Q3).

**Research date:** 2026-05-10
**Valid until:** ~2026-06-10 (30 days for stable backend contracts) — invalidate sooner if any Phase 3 / Phase 5 contract changes.

---

## RESEARCH COMPLETE

Phase 6 is bounded, frontend-heavy, and unblocked by stable Phase 3 + Phase 5 API contracts; the planner's main scoping decisions are around **cross-scope move semantics** (Q2 — needs user disambiguation between modal-redirects-to-reupload vs. new admin endpoint) and **admin test account provisioning** (Q3 — needed for UI-11 e2e). All other paths are clear, with concrete code-level mitigations for Pitfalls 5, 11, and 12.
