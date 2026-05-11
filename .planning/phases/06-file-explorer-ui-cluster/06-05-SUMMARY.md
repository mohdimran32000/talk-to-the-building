---
phase: 06-file-explorer-ui-cluster
plan: 05
subsystem: frontend/api-client
tags: [phase6, frontend, api, wave1, d-06]
dependency-graph:
  requires:
    - "Plan 06-01 (DocumentResponse.content_markdown_status backend addition)"
    - "Plan 06-04 (consolidated parsed.type === 'sub_agent' SSE branch in api.ts)"
    - "Plan 06-12 (D-06 backend wire shape: subfolders[].id round-trip)"
    - "Phase 3 / Plans 03-02..03-05 (folder + file router endpoints)"
  provides:
    - "frontend/src/lib/api.ts: 6 new exported functions (listFolder, createFolder, renameFolder, deleteFolder, moveDocument, renameDocument)"
    - "frontend/src/lib/api.ts: extended uploadFile(file, folder_path?, scope?) — back-compat preserved"
    - "frontend/src/lib/api.ts: 7 new exported types (FolderResponse, RenameFolderResponse, FolderRef, ListFolderResponse, DeleteFolderResult, ToolCallEntry, ToolUsedEntry)"
    - "frontend/src/lib/api.ts: extended Document with folder_path, scope, content_markdown_status"
  affects:
    - "Plan 06-07 (SubAgentSection) — can import ToolCallEntry + ToolUsedEntry"
    - "Plan 06-08 (FileExplorerPanel) — can import listFolder + extended uploadFile"
    - "Plan 06-09 (folder CRUD UI) — can import createFolder, renameFolder, deleteFolder, renameDocument + resolve folder UUIDs via subfolders[].id"
    - "Plan 06-10 (drag-and-drop) — can import moveDocument"
tech-stack:
  added: []
  patterns:
    - "Pitfall-5 helper-bypass: deleteFolder uses bare fetch (not fetchApi) to preserve the structured 409 body — first time this pattern is applied in the frontend API client"
    - "Discriminated-union return type for structured error contracts: DeleteFolderResult = {ok:true} | {ok:false, error:'FOLDER_NOT_EMPTY', document_count, subfolder_count}"
    - "URLSearchParams (not string concat) for query-string assembly on listFolder + uploadFile"
    - "Default-parameter back-compat for signature extensions: uploadFile(file, folder_path='/', scope='user') leaves existing call sites unchanged"
    - "D-06 literal-type grep anchor: subfolders: Array<{id: string; path: string}> declared in source to satisfy plan-checker wire-contract gate"
key-files:
  created: []
  modified:
    - frontend/src/lib/api.ts
decisions:
  - "Document interface extended (not split into a separate file) — single-file convention matches existing codebase pattern (Profile, Thread, Message all colocated)"
  - "FolderRef.id is string | null (Optional) for inferred-only folders; ListFolderResponse.subfolders uses the stricter Array<{id: string; path: string}> literal at the consumer-facing boundary — both shapes are wire-compatible per D-06; the looser FolderRef type is exported for consumers that want strict null-handling and the stricter array shape satisfies the plan-checker grep gate"
  - "deleteFolder branches on res.status === 409 (literal token) rather than res.status >= 400 || res.status !== 200 — the literal makes the contract intent visible at the call site and matches the plan acceptance grep"
  - "DeleteFolderResult.error is typed as literal 'FOLDER_NOT_EMPTY' (not string) so consumers get exhaustive type-narrowing in switch statements"
  - "uploadFile default scope='user' (not 'global') — uploading to global requires admin, defaulting user keeps the surface safe for non-admin call sites"
metrics:
  duration_minutes: 2
  completed_date: 2026-05-11
  tasks: 2
  files_touched: 1
  commits: 2
---

# Phase 06 Plan 05: Folder + Document Client API (api.ts) Summary

**One-liner:** Extends `frontend/src/lib/api.ts` with 6 typed folder/document CRUD methods, 7 supporting types (including D-06's `FolderRef` + locked `subfolders: Array<{id: string; path: string}>` wire shape), and back-compat-preserving signature extension of `uploadFile` — giving Wave 2 plans (06-07/06-08/06-09/06-10) a stable, type-checked contract to build against.

## What changed

### 1. Extended `Document` interface (Task 1, commit `1d4a6d5`)

```ts
export interface Document {
  // ...existing fields unchanged...
  // Phase 3 / FOLDER-07 — folder-path + scope on every document row
  folder_path: string
  scope: 'user' | 'global'
  // Plan 06-01 / D-03 — backend mirrors content_markdown_status onto DocumentResponse
  content_markdown_status?: 'ready' | 'pending' | 'failed' | 'requires_user_reupload' | null
  // ...
}
```

`UploadedFile` remains an alias of `Document` (`export type UploadedFile = Document`).

### 2. New exported types (Task 1, commit `1d4a6d5`)

```ts
export interface FolderResponse {
  id: string
  scope: 'user' | 'global'
  user_id: string | null  // null when scope='global'
  path: string
  created_at: string
}

export interface RenameFolderResponse extends FolderResponse {
  documents_updated: number
  folders_updated: number
}

// D-06 / Plan 06-12 — id is null for inferred-only subfolders
export interface FolderRef {
  id: string | null
  path: string
}

export interface ListFolderResponse {
  path: string
  documents: UploadedFile[]
  subfolders: Array<{id: string; path: string}>   // D-06 LOCKED SHAPE
}

export interface ToolCallEntry {
  tool: 'tree' | 'glob' | 'grep' | 'list_files' | 'read_document' | 'search_documents'
  args?: Record<string, unknown>
  turn?: number
  result_preview?: string
  status?: 'running' | 'done'
}

export interface ToolUsedEntry {
  tool: 'analyze_document' | 'explore_knowledge_base' | string
  sub_agent_id?: string
  tool_calls?: ToolCallEntry[]
  document_name?: string
  question?: string
  sub_agent_result?: string
}
```

### 3. New folder + document CRUD methods (Task 2, commit `0c27e21`)

Verbatim signatures shipped:

```ts
export async function listFolder(
  path: string = '/',
  scope: 'user' | 'global' | 'both' = 'both',
): Promise<ListFolderResponse>

export async function createFolder(
  path: string,
  scope: 'user' | 'global' = 'user',
): Promise<FolderResponse>

export async function renameFolder(
  id: string,
  new_path: string,
): Promise<RenameFolderResponse>

export async function moveDocument(
  id: string,
  folder_path: string,
): Promise<Document>

export async function renameDocument(
  id: string,
  file_name: string,
): Promise<Document>

export type DeleteFolderResult =
  | { ok: true }
  | { ok: false; error: 'FOLDER_NOT_EMPTY'; document_count: number; subfolder_count: number }

export async function deleteFolder(id: string): Promise<DeleteFolderResult>
```

### 4. Extended `uploadFile` (Task 2, commit `0c27e21`)

```ts
// Before:
export async function uploadFile(file: File): Promise<Document>

// After:
export async function uploadFile(
  file: File,
  folder_path: string = '/',
  scope: 'user' | 'global' = 'user',
): Promise<Document>
```

Query string built with `URLSearchParams({ folder_path, scope })`. No `Content-Type` header on the fetch — browser sets the multipart boundary automatically.

### 5. Pitfall-5-aware `deleteFolder` (Task 2)

Uses bare `fetch` (NOT `fetchApi`) because the helper throws on `!res.ok` and would lose the structured 409 body. The discriminated-union return type lets Plan 06-09's delete-confirm dialog show server-supplied counts:

```ts
export async function deleteFolder(id: string): Promise<DeleteFolderResult> {
  const token = await getToken()
  const res = await fetch(`/api/folders/${id}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${token}` },
  })
  if (res.status === 200) return { ok: true }
  if (res.status === 409) {
    const body = await res.json()
    return {
      ok: false,
      error: body.error,           // 'FOLDER_NOT_EMPTY'
      document_count: body.document_count,
      subfolder_count: body.subfolder_count,
    }
  }
  const err = await res.json().catch(() => ({}))
  throw new Error(err.detail || `Delete failed: ${res.status}`)
}
```

## `uploadFile` back-compat verification

Existing call site (`grep -nE "uploadFile" frontend/src/pages/Chat.tsx`):
```
16:  uploadFile,
66:      const uploaded = await uploadFile(file)
```

The one-arg call site `uploadFile(file)` continues to work unchanged because the new `folder_path` and `scope` params default to `'/'` and `'user'` respectively. `tsc --noEmit` confirms no breakage.

## D-06 grep gate (verbatim)

```
$ grep -E "subfolders:\s*Array<\{id:\s*string;\s*path:\s*string\}>" frontend/src/lib/api.ts
  subfolders: Array<{id: string; path: string}>
```

Match found — the locked wire-contract anchor is present inside `ListFolderResponse`.

## TypeScript build status

```
$ cd frontend && node_modules/.bin/tsc --noEmit
(no output)
$ echo $?
0
```

`tsc --noEmit` exits 0. The existing Plan-04 SSE branch in `sendMessage` (`parsed.type === 'sub_agent'` with 5-event switch) is preserved verbatim — Task 2 only appended new exports after `deleteFile`.

## Tasks completed

| # | Task                                                                   | Commit  | Files                       |
| - | ---------------------------------------------------------------------- | ------- | --------------------------- |
| 1 | Extend Document interface + add folder/sub-agent API types (incl. D-06 FolderRef) | 1d4a6d5 | frontend/src/lib/api.ts     |
| 2 | Add folder + document CRUD client methods + extend uploadFile           | 0c27e21 | frontend/src/lib/api.ts     |

## Acceptance criteria (per task)

**Task 1:**
- ✅ `grep -q "folder_path: string" frontend/src/lib/api.ts` (1 match)
- ✅ `grep -q "scope: 'user' | 'global'" frontend/src/lib/api.ts` (2 matches — Document + FolderResponse)
- ✅ `grep -q "content_markdown_status" frontend/src/lib/api.ts` (2 matches — field + type list)
- ✅ `grep -q "export interface FolderResponse" frontend/src/lib/api.ts`
- ✅ `grep -q "export interface RenameFolderResponse" frontend/src/lib/api.ts`
- ✅ `grep -q "export interface ListFolderResponse" frontend/src/lib/api.ts`
- ✅ `grep -q "export interface FolderRef" frontend/src/lib/api.ts`
- ✅ D-06 GREP GATE: `grep -E "subfolders:\s*Array<\{id:\s*string;\s*path:\s*string\}>" frontend/src/lib/api.ts` matches
- ✅ `grep -q "export interface ToolCallEntry" frontend/src/lib/api.ts`
- ✅ `grep -q "export interface ToolUsedEntry" frontend/src/lib/api.ts`
- ✅ `cd frontend && tsc --noEmit` exits 0

**Task 2:**
- ✅ All 6 functions exported (`listFolder`, `createFolder`, `renameFolder`, `deleteFolder`, `moveDocument`, `renameDocument`)
- ✅ `grep -q "res.status === 409" frontend/src/lib/api.ts` (Pitfall 5 branch)
- ✅ `grep -q "FOLDER_NOT_EMPTY" frontend/src/lib/api.ts` (3 matches — type alias, comment, type narrowing)
- ✅ `grep -q "document_count"` and `grep -q "subfolder_count"` (3 each — union member, error body, return shape)
- ✅ `grep -q "URLSearchParams"` (2 matches — listFolder + uploadFile)
- ✅ `uploadFile` signature has 3 params (file, folder_path, scope)
- ✅ `cd frontend && tsc --noEmit` exits 0
- ✅ Existing `uploadFile(file)` call site in Chat.tsx still type-checks (defaults preserve back-compat)

## Deviations from Plan

None — plan executed exactly as written.

Notes on consolidation preserved per the `<important>` directive:
- The Plan-04 generalized `parsed.type === 'sub_agent'` SSE branch in `sendMessage` (lines 306-327 of pre-edit api.ts, lines 364-385 post-edit) was not touched. The Task 2 additions were placed AFTER `deleteFile` and BEFORE the `// --- Messages ---` divider block, so the SSE handler stayed intact.
- The single existing `uploadFile(file)` call site in `Chat.tsx:66` was verified via grep before the signature extension; the default-argument approach (`folder_path = '/'`, `scope = 'user'`) preserves the one-arg call form without modification.

## Threat Flags

None — no new network endpoints, no auth-path changes, no schema changes at trust boundaries. All new methods hit endpoints already authenticated and RLS-gated server-side (Phase 3 / Plan 03-04 + 03-05 + 06-12). The Pitfall-5 bare-fetch in `deleteFolder` still sends the JWT (`Authorization: Bearer ${token}`) — bypass is of the throw-on-error helper only, not of auth.

## Known Stubs

None. All 6 functions hit live backend endpoints and return real data; no hardcoded empties, no placeholder text, no mock data sources. The `DeleteFolderResult` discriminated union is a structured-error contract — the `ok: false` branch surfaces real server-supplied counts, not placeholders.

## Self-Check: PASSED

Files modified (verified `git log --oneline -2`):
- ✅ `frontend/src/lib/api.ts` — present, modified across both commits

Commits exist (verified via `git log --oneline -5`):
- ✅ `1d4a6d5 feat(06-05): extend Document interface + add folder/sub-agent API types`
- ✅ `0c27e21 feat(06-05): add folder + document CRUD client methods`

Plan-level verification:
- ✅ `tsc --noEmit` exits 0
- ✅ D-06 grep gate matches in `frontend/src/lib/api.ts`
- ✅ Pitfall-5 deleteFolder branches on `res.status === 409` literally
- ✅ All 6 function exports present; all 7 new types exported
- ✅ Existing `uploadFile(file)` call site preserved
