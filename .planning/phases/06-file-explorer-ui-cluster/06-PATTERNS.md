# Phase 6: File-Explorer UI Cluster — Pattern Map

**Mapped:** 2026-05-10
**Files analyzed:** 22 (15 new, 7 modified, 1 new dep manifest entry)
**Analogs found:** 21 / 22 (1 file — `CrossScopeMoveDialog` — has no exact analog because the project has no shadcn AlertDialog yet; pattern derived from `FileUploadPanel` modal idioms + shadcn docs noted in RESEARCH.md)

**Key codebase verifications performed during mapping:**
- `DocumentResponse` Pydantic schema → CONFIRMED missing `content_markdown_status` field (D-03 trigger fires → Wave-0 backend plan REQUIRED)
- `admin@test.com` credentials → CONFIRMED already documented in `backend/scripts/test_helpers.py:26-29` with password `adminpassword123` (env override `TEST_USER_ADMIN_PASSWORD`); only the SQL promotion needs automation
- 5 legacy `yield json.dumps({"type": "sub_agent_*"...})` lines in `backend/app/routers/messages.py` at lines 120, 141, 160, 176, 198 — verbatim grep targets for Phase 6 cleanup
- 5 legacy frontend SSE branches in `frontend/src/lib/api.ts` at lines 306, 311, 313, 316, 319 — verbatim grep targets

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `frontend/src/components/FileExplorerPanel.tsx` | new component (top-level panel) | request-response (load + CRUD) | `frontend/src/components/FileUploadPanel.tsx` | exact (replacement) |
| `frontend/src/components/explorer/RootSection.tsx` | new component (subtree wrapper) | request-response | `frontend/src/components/FileUploadPanel.tsx` (collapsible header pattern, lines 102-110) | role-match |
| `frontend/src/components/explorer/FolderTree.tsx` | new component (recursion host + keyboard handler) | event-driven (DnD, keyboard) | `frontend/src/components/MessageList.tsx` (recursive render seam, lines 136-153) | partial — recursion seam shape only |
| `frontend/src/components/explorer/FolderNode.tsx` | new component (recursive node) | event-driven | `frontend/src/components/MessageList.tsx` (`SubAgentSection` lines 18-50, the existing recursive shape) | role-match |
| `frontend/src/components/explorer/DocumentRow.tsx` | new component (leaf row) | event-driven | `frontend/src/components/FileUploadPanel.tsx` (file-row render lines 139-188) | exact (extracted) |
| `frontend/src/components/explorer/ContextMenuActions.tsx` | new component (CRUD action set) | event-driven | none in current codebase (first ContextMenu use); shadcn install required first | no analog (new primitive) |
| `frontend/src/components/explorer/DeleteFolderDialog.tsx` | new component (AlertDialog) | request-response | `frontend/src/components/FileUploadPanel.tsx` delete pattern (line 166) + `Chat.tsx:handleDeleteFile` (lines 83-90) for the call site | partial — modal idiom new |
| `frontend/src/components/explorer/CrossScopeMoveDialog.tsx` | new component (AlertDialog — informational) | event-driven | none — first AlertDialog in project; copy structure from `DeleteFolderDialog` once written | no analog |
| `frontend/src/components/explorer/Breadcrumbs.tsx` | new component (presentation) | none | none in codebase | no analog (cosmetic) |
| `frontend/src/components/explorer/ScopeBadge.tsx` | new component (presentation) | none | `frontend/src/components/FileUploadPanel.tsx` `statusBadge()` (lines 12-26) and `metadataBadge()` (lines 28-37) | exact (badge pattern) |
| `frontend/src/components/explorer/StatusBadge.tsx` | new component (presentation) | none | `frontend/src/components/FileUploadPanel.tsx` `statusBadge()` (lines 12-26) | exact (extend with `content_markdown_status` colors) |
| `frontend/src/hooks/useOpenFoldersStorage.ts` | new hook (custom hook) | local state + localStorage | none — first custom hook in project | no analog (small new pattern) |
| `frontend/src/components/MessageList.tsx` | MODIFIED (extend `SubAgentSection`) | event-driven (rendering) | self — current `SubAgentSection` lines 18-50 IS the analog being generalized | exact (in-place refactor) |
| `frontend/src/components/ToolActivity.tsx` | MODIFIED (extract `ToolCallRow` for SubAgentSection re-use) | event-driven | self — current `ToolStepRow` lines 85-135 IS the pattern | exact (extract + reuse) |
| `frontend/src/pages/Chat.tsx` | MODIFIED (swap mount, migrate liveSubAgentTrace state) | request-response + SSE | self — `Chat.tsx:339-346` (mount point) and lines 38-42 (sub-agent state) | exact (in-place edit) |
| `frontend/src/lib/api.ts` | MODIFIED (folder client methods + `deleteFolder` typed return + SSE legacy removal) | request-response + streaming | self — existing `getThreads`/`createThread`/`uploadFile` methods (lines 133-194) | exact (extend) |
| `backend/app/routers/messages.py` | MODIFIED (delete 5 legacy `yield` lines) | streaming | self — see lines 120, 141, 160, 176, 198 | exact (delete-only) |
| `backend/app/models/schemas.py` | MODIFIED (add `content_markdown_status` to `DocumentResponse`) — Wave-0 plan | data | self — `DocumentResponse` lines 32-46 already shows the `Optional[str] = None` extension pattern (e.g. `error_message`, `content_hash`) | exact (single-field add) |
| `backend/migrations/021_admin_test_user.sql` (new) — Wave-0 migration | new migration | data | `backend/migrations/005_profiles_and_settings.sql` (lines 1-60: profile insert + idempotent CONFLICT) | role-match |
| `frontend/e2e/full-suite.spec.ts` | MODIFIED (append `test.describe('FileExplorer', …)` block) | test | self — existing `test.describe('Documents', …)` block (lines 188-280) | exact (append at EOF) |
| `frontend/package.json` | MODIFIED (3 new deps) | config | self — existing dependencies block (lines 12-28) | exact (extend) |
| `frontend/src/components/ui/{context-menu,dialog,alert-dialog,badge,tooltip,separator}.tsx` (×6 new) | shadcn primitive install | config | self — existing `frontend/src/components/ui/button.tsx`, `card.tsx` are CLI-installed shadcn output (no manual edits) | exact (CLI install) |

---

## Pattern Assignments

### `frontend/src/components/FileExplorerPanel.tsx` (new top-level panel — UI-01, UI-02)

**Analog:** `frontend/src/components/FileUploadPanel.tsx`

**Imports + module signature pattern** (from `FileUploadPanel.tsx:1-4`):
```tsx
import { useRef, useState, useEffect } from 'react'
import { Button } from '@/components/ui/button'
import { supabase } from '@/lib/supabase'
import type { UploadedFile, MetadataFieldDefinition } from '@/lib/api'
```

**Props shape pattern** (`FileUploadPanel.tsx:39-46`) — copy verbatim, drop `isUploading` (each `RootSection` will own its own upload state):
```tsx
interface FileUploadPanelProps {
  files: UploadedFile[]
  isUploading: boolean
  onUpload: (file: File) => void
  onDelete: (fileId: string) => void
  onStatusUpdate: (documentId: string, status: string, errorMessage?: string) => void
  metadataSchema?: MetadataFieldDefinition[] | null
}
```
Adapt: `onUpload: (file: File, folder_path: string, scope: 'user' | 'global') => void` per RESEARCH.md §FileExplorerPanelProps.

**Polling pattern** (`FileUploadPanel.tsx:60-85`) — copy verbatim into the new panel; the 2s polling on documents whose `status === 'pending' | 'processing'` is the project-locked SSE-vs-polling contract from CLAUDE.md ("Use polling … for ingestion status updates"):
```tsx
useEffect(() => {
  const hasPending = files.some((f) => f.status === 'pending' || f.status === 'processing')
  if (!hasPending) return
  const interval = setInterval(async () => {
    try {
      const { data } = await supabase
        .from('documents')
        .select('id, status, error_message')
        .in('id', files.filter((f) => f.status === 'pending' || f.status === 'processing').map((f) => f.id))
      if (data) {
        for (const doc of data) {
          const current = files.find((f) => f.id === doc.id)
          if (current && current.status !== doc.status) {
            onStatusUpdate(doc.id, doc.status, doc.error_message)
          }
        }
      }
    } catch { /* swallow */ }
  }, 2000)
  return () => clearInterval(interval)
}, [files, onStatusUpdate])
```
Adapt: also poll `content_markdown_status` once UI-08 lands (extend the `select(...)` column list).

**Mount-point pattern in `Chat.tsx`** (`Chat.tsx:339-346`):
```tsx
<FileUploadPanel
  files={files}
  isUploading={isUploading}
  onUpload={handleUploadFile}
  onDelete={handleDeleteFile}
  onStatusUpdate={handleStatusUpdate}
  metadataSchema={metadataSchema}
/>
```
Plan instruction: replace the JSX element name only; keep the prop wiring as-is for `files`/`onDelete`/`onStatusUpdate`/`metadataSchema`. Extend `handleUploadFile` signature to accept `(file, folder_path, scope)` per RESEARCH.md.

---

### `frontend/src/components/explorer/RootSection.tsx` (new — one of "Shared" / "My Files")

**Analog:** `frontend/src/components/FileUploadPanel.tsx` (collapsible-section header pattern)

**Collapsible-header pattern** (`FileUploadPanel.tsx:102-111`):
```tsx
return (
  <div className="border-b">
    <button
      onClick={() => setIsOpen(!isOpen)}
      className="flex w-full items-center justify-between px-4 py-2 text-sm font-medium text-muted-foreground hover:bg-muted/50 transition-colors"
    >
      <span>Documents ({files.length})</span>
      <span className="text-xs">{isOpen ? '▲' : '▼'}</span>
    </button>
```
Adapt: render TWO of these — one with `<Globe />` icon + "Shared (global)" + `bg-blue-50/50 dark:bg-blue-950/20` tint; one with `<User />` icon + "My Files" + `bg-zinc-50/50 dark:bg-zinc-900/30` tint (Pitfall 11 mitigation per CONTEXT.md §Two simultaneous root sections).

---

### `frontend/src/components/explorer/FolderNode.tsx` (new — recursive seam)

**Analog:** `frontend/src/components/MessageList.tsx` `SubAgentSection` (lines 18-50)

**Recursive expand/collapse pattern** (`MessageList.tsx:18-50`):
```tsx
function SubAgentSection({
  documentName, content, isActive, defaultExpanded = false,
}: { /* … */ }) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  return (
    <div className="border-l-2 border-blue-400/50 pl-3 ml-1 my-2">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300 transition-colors"
      >
        <span className="font-mono">{expanded ? '▼' : '▶'}</span>
        <span>{ /* label */ }</span>
      </button>
      {expanded && content && (<div className="mt-1 text-xs opacity-80">…</div>)}
    </div>
  )
}
```
Pattern notes: copy the `expanded` state + `▼`/`▶` chevron idiom verbatim. Adapt for: (a) lazy-fetch children via `GET /api/folders?path=…` on first expand, (b) wrap the header in `@radix-ui/react-context-menu` Trigger, (c) recurse `<FolderNode>` children for `subfolders[]` and `<DocumentRow>` for `documents[]` from the API response.

**Recursion seam shape** (where the children loop lives) — see `MessageList.tsx:136-144`:
```tsx
{msg.tool_metadata.tools_used.map((tool, i) => (
  <SubAgentSection key={i} documentName={tool.document_name || 'unknown'} content={tool.sub_agent_result} />
))}
```
Adapt: `subfolders.map((path, i) => <FolderNode key={path} folder={…} depth={depth+1} />)`.

---

### `frontend/src/components/explorer/DocumentRow.tsx` (new — leaf row, drag source, inline rename)

**Analog:** `frontend/src/components/FileUploadPanel.tsx` (file-row render block lines 139-188)

**Row + badges + delete pattern** (`FileUploadPanel.tsx:139-172`):
```tsx
{files.map((f) => (
  <div key={f.id}>
    <div
      className="flex items-center justify-between rounded-md bg-muted/30 px-3 py-1.5 text-sm cursor-pointer"
      onClick={() => setExpandedFileId(expandedFileId === f.id ? null : f.id)}
    >
      <div className="flex items-center gap-2 min-w-0 flex-wrap">
        <span className="truncate" title={f.file_name}>{f.file_name}</span>
        <span className="shrink-0 text-xs text-muted-foreground">{formatSize(f.file_size)}</span>
        {statusBadge(f.status)}
        {f.status === 'failed' && f.error_message && (
          <span className="text-xs text-red-600 truncate max-w-[200px]" title={f.error_message}>
            {f.error_message}
          </span>
        )}
        {f.status === 'ready' && f.metadata && (
          <>
            {f.metadata.document_type && metadataBadge('Type', f.metadata.document_type)}
            {f.metadata.topic && metadataBadge('Topic', f.metadata.topic)}
          </>
        )}
      </div>
      <button
        onClick={(e) => { e.stopPropagation(); onDelete(f.id) }}
        className="ml-2 shrink-0 text-muted-foreground hover:text-destructive text-xs"
        title="Delete file"
      >✕</button>
    </div>
  </div>
))}
```
Pattern notes: keep the `bg-muted/30` row, the `truncate` title, the badges row, and the `✕` delete button. Adapt by:
- Wrapping the outer `<div>` with `@dnd-kit/core`'s `useDraggable` hook (UI-06).
- Adding `<ScopeBadge scope={f.scope} />` next to `statusBadge` (Pitfall 11 defense in depth).
- Replacing the `onClick` expand with click-to-edit-name (UI-07): swap the `<span>` for an `<input>` when in edit mode; Enter calls `PATCH /api/files/{id}` with `{file_name}`.

---

### `frontend/src/components/explorer/StatusBadge.tsx` and `ScopeBadge.tsx` (new — UI-08)

**Analog:** `frontend/src/components/FileUploadPanel.tsx` `statusBadge()` and `metadataBadge()` (lines 12-37)

**Badge factory pattern** (`FileUploadPanel.tsx:12-26`):
```tsx
function statusBadge(status: string) {
  const colors: Record<string, string> = {
    ready: 'bg-green-100 text-green-800',
    processing: 'bg-yellow-100 text-yellow-800',
    pending: 'bg-blue-100 text-blue-800',
    failed: 'bg-red-100 text-red-800',
  }
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${colors[status] || 'bg-gray-100 text-gray-800'}`}>
      {status}
    </span>
  )
}
```
Pattern notes:
- Copy the function shape verbatim. Adapt the `colors` map for `content_markdown_status` enum: `ready`/`pending`/`failed`/`requires_user_reupload` (Migration 014). Add an orange "Re-index pending" pill for `pending`, red "Re-upload required" for `requires_user_reupload`.
- For `ScopeBadge`: same factory, two values: `global` → green "Shared", `user` → gray "Private". Verbatim Tailwind class shape from `metadataBadge()` lines 28-37.

---

### `frontend/src/components/explorer/DeleteFolderDialog.tsx` (new — UI-04, Pitfall 5)

**Analog:** `frontend/src/components/FileUploadPanel.tsx:166-172` (delete-button trigger) + `Chat.tsx:83-90` (delete handler with toast).

**Delete-button trigger pattern** (`FileUploadPanel.tsx:166-172`):
```tsx
<button
  onClick={(e) => { e.stopPropagation(); onDelete(f.id) }}
  className="ml-2 shrink-0 text-muted-foreground hover:text-destructive text-xs"
  title="Delete file"
>✕</button>
```

**Delete-handler pattern** (`Chat.tsx:83-90`):
```tsx
const handleDeleteFile = async (fileId: string) => {
  try {
    await deleteFile(fileId)
    setFiles((prev) => prev.filter((f) => f.id !== fileId))
  } catch (err) {
    toast.error(err instanceof Error ? err.message : 'Failed to delete file')
  }
}
```

**Pitfall 5 contract** — the new dialog's confirm handler MUST call the new typed `deleteFolder()` from `api.ts` and branch on the structured 409 body (NOT throw). See `backend/app/routers/folders.py:169-174` for the exact server response shape:
```py
if not result.get("deleted"):
    return JSONResponse(status_code=409, content={
        "error": "FOLDER_NOT_EMPTY",
        "document_count": result.get("document_count", 0),
        "subfolder_count": result.get("subfolder_count", 0),
    })
```
Frontend dialog renders BOTH counts literally (verifier-gate from RESEARCH.md §Pitfall 5).

---

### `frontend/src/components/MessageList.tsx` MODIFIED (Pitfall 12 — recursive `SubAgentSection`)

**Analog:** self — `MessageList.tsx:18-50` (current `SubAgentSection`) is the in-place refactor target.

**BEFORE pattern (current, lines 18-50)** — only handles `analyze_document`, takes `documentName`/`content`/`isActive`:
```tsx
function SubAgentSection({
  documentName, content, isActive, defaultExpanded = false,
}: {
  documentName: string
  content?: string
  isActive?: boolean
  defaultExpanded?: boolean
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  return (
    <div className="border-l-2 border-blue-400/50 pl-3 ml-1 my-2">
      <button onClick={() => setExpanded(!expanded)} className="...">
        <span className="font-mono">{expanded ? '▼' : '▶'}</span>
        <span>
          {isActive ? `Analyzing "${documentName}"...` : `Used: analyze_document on "${documentName}"`}
        </span>
        {isActive && <span className="animate-pulse ml-1">●</span>}
      </button>
      {expanded && content && (<div className="mt-1 text-xs opacity-80"><MarkdownContent content={content} /></div>)}
    </div>
  )
}
```

**AFTER pattern (Phase 6 — Pitfall 12 compliant)** — see RESEARCH.md §Pitfall 12 lines 599-631. Copy that pattern verbatim. **Recursion seam (the line that decides this is not a fork):**
```tsx
{tool.tool_calls && tool.tool_calls.length > 0 && (
  <div className="mt-1 space-y-1">
    {tool.tool_calls.map((call, i) => <ToolCallRow key={i} call={call} />)}
  </div>
)}
```
- `tool.tool_calls` is empty `[]` for `analyze_document` (per `messages.py:111` `"tool_calls": []` initialization) — the `.map` produces nothing → no special-case needed.
- For `explore_knowledge_base`, the array is populated by `messages.py:135-139` and `messages.py:155-158`.

**Verifier-gate to add to the plan:** grep `MessageList.tsx` for `'analyze_document'` and `'explore_knowledge_base'` — they MUST appear ONLY inside the `label` computation (presentation string), NEVER inside an `if (tool.tool === '...')` conditional that controls component output. RESEARCH.md §Pitfall 12 line 634.

**Caller-site update (lines 138-144):**
```tsx
// BEFORE
{msg.tool_metadata.tools_used.map((tool, i) => (
  <SubAgentSection key={i} documentName={tool.document_name || 'unknown'} content={tool.sub_agent_result} />
))}
// AFTER (Phase 6)
{msg.tool_metadata.tools_used.map((tool, i) => (
  <SubAgentSection key={i} tool={tool} />
))}
```

---

### `frontend/src/components/ToolActivity.tsx` MODIFIED (extract `ToolCallRow`)

**Analog:** self — `ToolActivity.tsx:85-135` (current `ToolStepRow`) is the model.

**Pattern to extract** (`ToolActivity.tsx:85-119`):
```tsx
function ToolStepRow({ step }: { step: ToolStep }) {
  const [expanded, setExpanded] = useState(false)
  const config = TOOL_LABELS[step.tool] || { active: `Running ${step.tool}`, done: `Used ${step.tool}`, icon: 'search' }
  const label = step.status === 'running' ? config.active : config.done
  const hasDetails = step.args && Object.keys(step.args).length > 0
  const queryArg = step.args?.query || step.args?.question || step.args?.document_name
  return (
    <div className="group">
      <button
        onClick={() => hasDetails && setExpanded(!expanded)}
        className={`flex items-center gap-1.5 text-xs transition-colors ${...}`}
      >
        <ToolIcon type={config.icon} />
        <span>{label}</span>
        {step.status === 'running' && <DotSpinner />}
      </button>
      {expanded && queryArg && (<div className="ml-5 mt-1 …">{queryArg}</div>)}
    </div>
  )
}
```
Pattern notes:
- Promote `ToolStepRow` → reusable `ToolCallRow` exported from `ToolActivity.tsx` (or a sibling `ToolCallRow.tsx`).
- Adapt for Explorer's tool set per RESEARCH.md §Tool-icon mapping (lucide): `tree → FolderTree`, `list_files → Folder`, `glob → FileSearch`, `grep → Search`, `read_document → Eye`. Replace the inline SVG `ToolIcon` switch with lucide imports.
- Drop `isSubAgent` flag from `ToolStep` interface — the structural separation (`liveSubAgentTrace.tool_calls` vs `toolSteps`) replaces the boolean flag per RESEARCH.md §Existing toolSteps migration.

---

### `frontend/src/lib/api.ts` MODIFIED (folder client + typed `deleteFolder` + SSE legacy removal)

**Analog:** self — existing methods at lines 133-194 are the template.

**Standard typed-method pattern** (`api.ts:133-150`):
```ts
export async function getThreads(): Promise<Thread[]> {
  return fetchApi('/api/threads')
}
export async function createThread(title?: string): Promise<Thread> {
  return fetchApi('/api/threads', { method: 'POST', body: JSON.stringify({ title: title || null }) })
}
export async function deleteThread(id: string): Promise<void> {
  await fetchApi(`/api/threads/${id}`, { method: 'DELETE' })
}
```
Pattern notes — new folder methods to add:
- `listFolder(path: string, scope: 'user'|'global'|'both' = 'both'): Promise<{path, documents, subfolders}>` — `fetchApi('/api/folders?path=...&scope=...')`
- `createFolder(path: string, scope: 'user'|'global' = 'user'): Promise<FolderResponse>` — POST
- `renameFolder(id: string, new_path: string): Promise<RenameFolderResponse>` — PATCH
- `moveDocument(id: string, folder_path: string): Promise<Document>` — PATCH `/api/files/{id}` with `{folder_path}`
- `renameDocument(id: string, file_name: string): Promise<Document>` — PATCH `/api/files/{id}` with `{file_name}`
- `uploadFile(file, folder_path = '/', scope = 'user')` — extend signature; the existing `uploadFile` at lines 176-190 already shows the `FormData` + token + non-`fetchApi` pattern needed for upload.

**Special-case `deleteFolder` (Pitfall 5)** — must NOT use `fetchApi()` because `fetchApi` throws on `!res.ok` and would lose the structured 409 body. Pattern from RESEARCH.md §Pitfall 5 — copy verbatim:
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

**SSE legacy-branch removal pattern** — DELETE these 5 branches verbatim from `api.ts:306-320`:
```ts
} else if (parsed.type === 'sub_agent_start') { ... }     // line 306
} else if (parsed.type === 'sub_agent_token') { ... }     // line 311
} else if (parsed.type === 'sub_agent_tool_start') { ... } // line 313
} else if (parsed.type === 'sub_agent_tool_done') { ... }  // line 316
} else if (parsed.type === 'sub_agent_done') { ... }       // line 319
```
ADD ONE generalized branch (RESEARCH.md §SSE envelope switchover lines 642-650 — copy verbatim):
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

**`Document` interface extension** (`api.ts:154-167`) — add `folder_path: string`, `scope: 'user' | 'global'`, and (after Wave-0 backend plan) `content_markdown_status?: 'ready' | 'pending' | 'failed' | 'requires_user_reupload'`.

---

### `backend/app/routers/messages.py` MODIFIED (delete 5 legacy `yield` lines)

**Analog:** self — verbatim grep targets.

**Lines to delete** (verbatim — keep ONLY the second `yield json.dumps({"type": "sub_agent", ...})` in each branch):

| Line | Verbatim text | Action |
|------|---------------|--------|
| 120 | `yield json.dumps({"type": "sub_agent_start", **parsed})` | DELETE |
| 141 | `yield json.dumps({"type": "sub_agent_tool_start", **parsed})` | DELETE |
| 160 | `yield json.dumps({"type": "sub_agent_tool_done", **parsed})` | DELETE |
| 176 | `yield json.dumps({"type": "sub_agent_token", "content": data})` | DELETE |
| 198 | `yield json.dumps({"type": "sub_agent_done"})` | DELETE |

Also delete the inline comment "Dual-emit window (Phase 5 ONLY — removed in Phase 6 cleanup):" at line 118 and the "1) LEGACY shape" / "2) GENERALIZED envelope" comments at lines 119, 121, 140, 159, 168, 197 since they reference the obsolete dual-emit contract.

**Verifier-gate:** after deletion, `grep -n '"type": "sub_agent_' backend/app/routers/messages.py` should return ZERO matches (all remaining envelopes are `"type": "sub_agent"` without trailing underscore).

---

### `backend/app/models/schemas.py` MODIFIED — Wave-0 plan (D-03 trigger fired)

**Analog:** self — `DocumentResponse` lines 32-46 already shows the pattern.

**Existing pattern** (`schemas.py:32-46`):
```py
class DocumentResponse(BaseModel):
    id: str
    user_id: Optional[str] = None
    file_name: str
    file_size: int
    mime_type: str
    status: str
    error_message: Optional[str] = None
    content_hash: Optional[str] = None
    metadata: Optional[dict] = None
    folder_path: str = "/"
    scope: str = "user"
    action: Optional[str] = None
    created_at: datetime
    updated_at: datetime
```
**Plan instruction:** add ONE field, mirroring the `Optional[str] = None` pattern of `error_message`/`content_hash`:
```py
content_markdown_status: Optional[str] = None  # 'ready' | 'pending' | 'failed' | 'requires_user_reupload' (Migration 014)
```
**Why required:** GREP confirmed (2026-05-10) the field is absent from `DocumentResponse`. Per FastAPI's `response_model=DocumentResponse` serialization, the field is silently STRIPPED from the wire response even though the DB row carries it (Migration 014). UI-08's "re-index status badge for pending/failed re-index" (RESEARCH.md §Per-Requirement Mapping line 509) therefore cannot be implemented without this Wave-0 backend extension.

**No router code change needed** — the routers already pass `**row` / dict spreads through `DocumentResponse(**row)`-style construction (e.g. `files.py` upload response). Adding the field to the Pydantic model auto-includes it in the wire response.

---

### `backend/migrations/021_admin_test_user.sql` (new — D-02, Wave-0)

**Analog:** `backend/migrations/005_profiles_and_settings.sql` (lines 1-60) — profile-row INSERT + idempotent ON CONFLICT.

**Existing pattern** (`005_profiles_and_settings.sql:38-50`):
```sql
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
    INSERT INTO public.profiles (id, email)
    VALUES (NEW.id, NEW.email)
    ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END;
$$;
```
**Pattern notes:** the migration must be IDEMPOTENT (re-running cannot fail). Pattern: `ON CONFLICT … DO NOTHING` for the auth.users seed, then `UPDATE … SET is_admin = true … WHERE email = 'admin@test.com'` for the profile promotion. Email + password match the already-documented convention from `backend/scripts/test_helpers.py:26-29`:
```py
TEST_USER_ADMIN = {
    "email": "admin@test.com",
    "password": os.environ.get("TEST_USER_ADMIN_PASSWORD", "adminpassword123"),
}
```
**Important:** Supabase Auth users CANNOT be seeded purely via SQL (`auth.users.encrypted_password` requires Supabase's bcrypt + GoTrue-internal columns). The realistic path is one of:
- (Preferred) A Python seed script `backend/scripts/seed_admin_user.py` that uses the Supabase Admin API (`supabase.auth.admin.create_user(...)`) — see existing service-role client pattern in `backend/app/auth.py:get_supabase_client()`.
- A SQL migration that ONLY does the `UPDATE profiles SET is_admin = true` step, with a runtime precondition check that fails loudly if `admin@test.com` doesn't exist in `auth.users` yet (and a clear error message telling the operator to run the seed script first).

**Plan recommendation:** ship BOTH — the seed script is the source of truth for creating the user; the migration is the source of truth for the admin promotion (so re-running migrations alone makes any newly-created admin user actually admin).

---

### `frontend/e2e/full-suite.spec.ts` MODIFIED (TEST-05 — append `FileExplorer` describe block)

**Analog:** self — existing `test.describe('Documents', …)` block at lines 188-280.

**Auth + navigation pattern** (`full-suite.spec.ts:1-15`):
```ts
import { test, expect } from '@playwright/test'
import path from 'path'
import fs from 'fs'

const TEST_EMAIL = 'test@test.com'
const TEST_PASSWORD = 'supabase123'

async function signIn(page: import('@playwright/test').Page) {
  await page.goto('/login')
  await page.getByLabel('Email').fill(TEST_EMAIL)
  await page.getByLabel('Password').fill(TEST_PASSWORD)
  await page.getByRole('button', { name: 'Sign In' }).click()
  await expect(page.getByRole('button', { name: '+ New Chat' })).toBeVisible({ timeout: 15000 })
}
```
**Pattern notes for new tests:**
- Append `const TEST_ADMIN_EMAIL = 'admin@test.com'` and `const TEST_ADMIN_PASSWORD = process.env.TEST_USER_ADMIN_PASSWORD ?? 'adminpassword123'` (matches backend `test_helpers.py:26-29` convention).
- Add a sibling `signInAdmin(page)` helper using the same shape as `signIn`.
- Append `test.describe('FileExplorer', () => { test.describe.configure({ mode: 'serial' }); … })` at end of file — same describe-block structure as `Documents` (lines 188-280) which is the closest semantic neighbor.

**Existing upload-test pattern** to mirror (`full-suite.spec.ts:207-227`, partial — read full file at planning time):
```ts
test('Upload file shows in list with pending badge', async ({ page }) => {
  await signIn(page)
  await page.getByText(/Documents \(\d+\)/).click()
  await expect(page.getByRole('button', { name: 'Upload File' })).toBeVisible()
  // Create a test text file
  const tmpPath = path.join(__dirname, 'test_upload.txt')
  fs.writeFileSync(tmpPath, testContent)
  try {
    const fileInput = page.locator('input[type="file"]')
    await fileInput.setInputFiles(tmpPath)
    await expect(page.getByText('test_upload.txt')).toBeVisible({ timeout: 10000 })
  } finally {
    fs.unlinkSync(tmpPath)
  }
})
```
**Pattern notes:**
- The `try { … } finally { fs.unlinkSync(tmpPath) }` pattern is the project's resource-cleanup convention (CLAUDE.md "tests must NEVER delete all user data" — only the resource the test created).
- For drag-and-drop tests, `page.dragTo()` does NOT work with `@dnd-kit/core` (pointer events). Use `page.locator(source).hover() → page.mouse.down() → page.mouse.move(x, y) → page.mouse.up()` per RESEARCH.md §Wave 0 Gaps line 698.

---

### `frontend/package.json` MODIFIED (3 new deps)

**Analog:** self — existing `dependencies` block (lines 12-28).

**Existing dependency block pattern** (`package.json:12-28`):
```json
"dependencies": {
  "@supabase/supabase-js": "^2.95.3",
  "@tailwindcss/vite": "^4.1.18",
  "class-variance-authority": "^0.7.1",
  "clsx": "^2.1.1",
  "lucide-react": "^0.563.0",
  "next-themes": "^0.4.6",
  "radix-ui": "^1.4.3",
  "react": "^19.2.0",
  ...
}
```
**Plan instruction:** add three entries (versions verified by RESEARCH.md against `npm view` on 2026-05-10):
```json
"@dnd-kit/core": "^6.3.1",
"@dnd-kit/sortable": "^10.0.0",
"@radix-ui/react-context-menu": "^2.2.16"
```
The other 5 Radix sub-packages (`react-dialog`, `react-alert-dialog`, `react-tooltip`, `react-separator`) are pulled transitively when `npx shadcn add dialog alert-dialog tooltip separator` runs — they end up in `package.json` automatically. The `radix-ui` umbrella package at line 19 covers most of them; verify with `npm ls @radix-ui/react-dialog` after install to confirm no duplicate versions.

---

### `frontend/src/components/ui/{context-menu,dialog,alert-dialog,badge,tooltip,separator}.tsx` (×6 — new shadcn primitives)

**Analog:** `frontend/src/components/ui/button.tsx`, `card.tsx` (existing CLI-installed shadcn output)

**Pattern note:** these are CLI-generated. NEVER hand-write them. Plan instruction:
```bash
cd frontend && npx shadcn@3.8.4 add context-menu dialog alert-dialog badge tooltip separator
```
The CLI output goes to `frontend/src/components/ui/`. Do not modify the generated files except for narrow, clearly-marked customizations (the existing `button.tsx`/`card.tsx` are unmodified outputs — that's the convention to maintain).

---

## Shared Patterns

### Auth header injection (apply to: every new `api.ts` method that calls a protected endpoint)

**Source:** `frontend/src/lib/api.ts:3-24`
**Apply to:** All folder-API methods (`listFolder`, `createFolder`, `renameFolder`, `moveDocument`, `renameDocument`, etc.)
```ts
async function getToken(): Promise<string> {
  const { data: { session } } = await supabase.auth.getSession()
  if (!session) throw new Error('Not authenticated')
  return session.access_token
}

async function fetchApi(path: string, options: RequestInit = {}) {
  const token = await getToken()
  const res = await fetch(path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
      ...options.headers,
    },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed: ${res.status}`)
  }
  return res.json()
}
```
Use `fetchApi(...)` for everything EXCEPT (a) `deleteFolder` (needs structured-409 branch — see Pitfall 5 above) and (b) file uploads (need `FormData` body without `Content-Type: application/json` — see existing `uploadFile` lines 176-190).

### Error handling + user feedback (apply to: every CRUD handler in `Chat.tsx` and the new explorer)

**Source:** `frontend/src/pages/Chat.tsx:63-90`
**Apply to:** `handleCreateFolder`, `handleRenameFolder`, `handleDeleteFolder`, `handleMoveDocument`, `handleRenameDocument`, `handleUploadFile` (extended)
```tsx
const handleUploadFile = async (file: File) => {
  setIsUploading(true)
  try {
    const uploaded = await uploadFile(file)
    if (uploaded.action === 'skipped') {
      toast.info('File already uploaded with identical content — skipped')
    } else if (uploaded.action === 'updated') {
      toast.success('File content changed — re-ingesting updated content')
      setFiles((prev) => prev.map((f) => f.id === uploaded.id ? uploaded : f))
    } else {
      setFiles((prev) => [uploaded, ...prev])
    }
  } catch (err) {
    toast.error(err instanceof Error ? err.message : 'Failed to upload file')
  } finally {
    setIsUploading(false)
  }
}
```
Pattern: `try { await api(); optimistic state update } catch (err) { toast.error(err instanceof Error ? err.message : '<fallback>') }`. NEVER use `alert()`; always `toast` from `sonner` (already imported at `Chat.tsx:3`).

### Admin gating (apply to: every "global" affordance — UI-11)

**Source:** `frontend/src/contexts/AuthContext.tsx:73`
**Apply to:** `RootSection scope="global"` (Create folder button), `ContextMenuActions` items inside global scope (Create/Rename/Delete entries), drag-target acceptance for global folders.
```tsx
import { useAuth } from '@/contexts/AuthContext'

function MyComponent() {
  const { isAdmin } = useAuth()
  return (
    <>
      {isAdmin && <Button onClick={createGlobal}>+ New Folder (Shared)</Button>}
      {/* OR */}
      <ContextMenuItem disabled={!isAdmin}>Rename</ContextMenuItem>
    </>
  )
}
```
Defense in depth: backend `_require_admin` (`folders.py:21-33` and `files.py` upload) is the actual security boundary. The UI gate is UX-only.

### Recursive-render seam (apply to: `FolderNode`, `SubAgentSection`)

**Source:** `frontend/src/components/MessageList.tsx:138-144` (top-level `tools_used.map`) + the same component recursing on `tool.tool_calls?.map(...)`
**Apply to:** `FolderNode` recursing on `subfolders.map(<FolderNode />)` and `documents.map(<DocumentRow />)`; `SubAgentSection` recursing on `tool.tool_calls.map(<ToolCallRow />)`.
**Critical rule (Pitfall 12):** the recursion seam is the ONLY allowed branch on `agentType` / `nodeType` — and even then, the branch is in *what is passed to children* (different child component class), NEVER in the parent's render tree. NO `if (tool === 'explorer') { … } else { … }` patterns.

### Stop-event-propagation on nested clickables (apply to: any row with both row-click and inner button)

**Source:** `frontend/src/components/FileUploadPanel.tsx:166-169`
**Apply to:** `DocumentRow` (delete button inside drag-handle row), `FolderNode` (delete button inside expand row).
```tsx
<button onClick={(e) => { e.stopPropagation(); onDelete(f.id) }}>✕</button>
```

---

## No Analog Found

Files with no close match in the existing codebase (planner should fall back to RESEARCH.md patterns or external library docs):

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `frontend/src/components/explorer/CrossScopeMoveDialog.tsx` | new component | event-driven | First AlertDialog in project; no prior modal pattern beyond the inline `Sign Out` confirm pattern. After `DeleteFolderDialog` is written, copy its structure. |
| `frontend/src/components/explorer/Breadcrumbs.tsx` | new component | none (pure presentation) | No breadcrumb component exists. Build from RESEARCH.md sketch (split path on `/`, render each segment as a button). |
| `frontend/src/components/explorer/ContextMenuActions.tsx` | new component | event-driven | First ContextMenu in project. Pattern comes entirely from `@radix-ui/react-context-menu` docs after the shadcn CLI installs `ui/context-menu.tsx`. |
| `frontend/src/hooks/useOpenFoldersStorage.ts` | custom hook | local + localStorage | First custom hook in project (`useAuth` is a context-bound consumer, not a custom hook in the traditional sense). Build from RESEARCH.md §localStorage persistence shape. |

For all four, the per-file specs in RESEARCH.md are detailed enough that the planner can write a plan without an in-codebase analog.

---

## Cross-File Consistency Notes

- **Lucide icons:** the project already imports from `lucide-react@^0.563.0` (`package.json:17`). Phase 6 uses `Folder`, `FolderOpen`, `FolderTree`, `File`, `FileText`, `FileSearch`, `Search`, `Eye`, `Users`, `User`, `Globe`, `ChevronRight`, `ChevronDown`, `Plus`, `MoreVertical` — all confirmed available in 0.563.0.
- **Tailwind class conventions:** existing components use `text-xs`, `text-muted-foreground`, `bg-muted/30`, `rounded-md` — match these. Don't introduce custom CSS or new shadcn theme tokens.
- **TypeScript style:** project uses `interface` for component prop types (not `type`), `Optional` chaining `?.` for nullable access, `as` only when truly needed. Match in new files.
- **State management:** raw `useState` + `useCallback` everywhere (`Chat.tsx:28-43, 45-113`). Do NOT introduce TanStack Query / Redux / Zustand for this phase.

---

## Metadata

**Analog search scope:** `frontend/src/components/`, `frontend/src/lib/`, `frontend/src/contexts/`, `frontend/src/pages/`, `frontend/e2e/`, `backend/app/routers/`, `backend/app/models/`, `backend/migrations/`, `backend/scripts/`
**Files scanned:** ~45 (full-read: 8; targeted-grep: ~37)
**Files fully read for excerpt extraction:** `FileUploadPanel.tsx`, `MessageList.tsx`, `ToolActivity.tsx`, `api.ts`, `AuthContext.tsx`, `Chat.tsx` (lines 1-120, 330-360), `schemas.py`, `folders.py`, `messages.py` (lines 1-205), `files.py` (lines 1-80), `005_profiles_and_settings.sql` (lines 1-60), `015_two_scope_rls.sql` (lines 1-50), `full-suite.spec.ts` (lines 1-120 + grep), `test_helpers.py` (lines 15-60), `package.json`
**Pattern extraction date:** 2026-05-10

## PATTERN MAPPING COMPLETE

Pattern map covers all 22 in-scope files for Phase 6 with concrete analog excerpts; the only "no analog" entries (4 files) are first-of-kind components for which RESEARCH.md already supplies detailed specs. Two findings drive Wave-0 plans the planner MUST schedule before frontend work: (1) `DocumentResponse` is missing `content_markdown_status` (D-03 trigger fired — single-field Pydantic add), and (2) the admin user provisioning needs a Supabase Admin API seed script PLUS a SQL `UPDATE profiles SET is_admin = true` migration because Supabase Auth credentials cannot be seeded by SQL alone.
