---
phase: 06-file-explorer-ui-cluster
plan: 06
subsystem: frontend/explorer-primitives
tags: [phase6, frontend, tree, wave1, recursive, a11y, localStorage, d-04, d-06]
dependency-graph:
  requires:
    - "Plan 06-03 (shadcn primitives available)"
    - "Plan 06-05 (typed api.ts: listFolder, FolderRef, ListFolderResponse, extended UploadedFile.{folder_path, scope, content_markdown_status})"
    - "Plan 06-12 (D-06 wire shape: subfolders[].id round-trip from backend)"
  provides:
    - "frontend/src/hooks/useOpenFoldersStorage.ts: per-user, per-scope open-folder persistence (localStorage, 250ms debounce)"
    - "frontend/src/components/explorer/FolderNode.tsx: recursive folder node with lazy listFolder fetch + folderId thread (D-06)"
    - "frontend/src/components/explorer/FolderTree.tsx: keyboard host + ExpansionContext + useExpandedState hook"
    - "frontend/src/components/explorer/RootSection.tsx: scope-bounded wrapper (Shared/My Files)"
    - "frontend/src/components/explorer/DocumentRow.tsx: leaf row with ScopeBadge + StatusBadge"
    - "frontend/src/components/explorer/ScopeBadge.tsx + StatusBadge.tsx: pill components"
    - "data-* attributes: data-folder-path, data-folder-id, data-scope, data-document-id, data-root-scope"
  affects:
    - "Plan 06-08 (FileExplorerPanel composes <RootSection scope=user/global />)"
    - "Plan 06-09 (ContextMenu wires CRUD to onDeleteDocument/onRenameDocument; calls renameFolder(folderId)/deleteFolder(folderId) using threaded UUIDs)"
    - "Plan 06-10 (@dnd-kit wraps DocumentRow + FolderNode header via data-scope reads)"
    - "Plan 06-11 (Playwright queries by data-folder-path / data-document-id / data-scope)"
tech-stack:
  added: []
  patterns:
    - "Recursive React component via internal child-boundary wrapper (FolderNodeChildBoundary) — avoids prop-drilling isOpen by reading internal ExpansionContext"
    - "First custom hook in the project (useOpenFoldersStorage) — debounced localStorage write pattern (250ms via useRef<setTimeout>)"
    - "Per-user storage keying: `fileExplorer:open:${userId}` (CONTEXT.md §localStorage persistence) — prevents leakage on shared machines"
    - "D-04 keyboard scope (LOCKED): exactly ArrowRight/Left/Down/Up + Enter/Space — Home/End/typeahead deferred to v2 (negative grep gate)"
    - "D-06 folderId thread (LOCKED): FolderNode props declare `folderId: string | null`; recursion uses `sub.id ?? null`"
    - "WAI-ARIA treeview: role='tree' on container, role='treeitem' + aria-level + aria-expanded on each node, role='group' on children"
    - "Pitfall-11 visual differentiator: bg-blue-50/dark:bg-blue-950 for Shared, bg-zinc-50/dark:bg-zinc-900 for My Files"
key-files:
  created:
    - frontend/src/hooks/useOpenFoldersStorage.ts
    - frontend/src/components/explorer/FolderNode.tsx
    - frontend/src/components/explorer/FolderTree.tsx
    - frontend/src/components/explorer/RootSection.tsx
    - frontend/src/components/explorer/DocumentRow.tsx
    - frontend/src/components/explorer/ScopeBadge.tsx
    - frontend/src/components/explorer/StatusBadge.tsx
  modified: []
key-decisions:
  - "Split FolderNode into FolderNode + internal FolderNodeChildBoundary so recursion reads isOpen from an internal ExpansionContext instead of prop-drilling — keeps the recursive props identical at every level except the boundary"
  - "useExpandedState hook is exported from FolderTree.tsx (not a separate file) — it is intentionally scoped to the tree subsystem and not a public API"
  - "Root '/' always renders open by default (FolderTree passes `isOpen={... || rootPath === '/'}`) so the user sees top-level docs/subfolders on first render without expanding"
  - "Inline rename UI is NOT implemented in DocumentRow (Plan 06-09 owns it); the onRename prop is exposed in the interface for downstream wiring but unused here — keeps Wave 1 focused on recursion shape per plan note"
  - "data-folder-id={folderId ?? ''} (empty string for null) rather than omitting the attribute — gives Playwright a deterministic selector to assert 'no folder UUID' for inferred-only paths"
patterns-established:
  - "Per-user, per-scope localStorage keying for UI state — sets the precedent for future per-user UI prefs"
  - "Debounced localStorage write (250ms) via useEffect + useRef<setTimeout> with cleanup"
  - "Internal context to break prop-drilling without exposing the API as public (ExpansionContext is module-internal)"
requirements-completed: [UI-02, UI-03, UI-08, UI-09]
metrics:
  duration_minutes: 3
  completed_date: 2026-05-11
  tasks: 2
  files_touched: 7
  commits: 2
---

# Phase 06 Plan 06: Recursive Tree Primitives Summary

**Seven tree primitives — useOpenFoldersStorage hook, FolderNode (recursive + D-06 folderId thread), FolderTree (D-04 keyboard host), RootSection (Pitfall-11 scope tint), DocumentRow, ScopeBadge, StatusBadge — give Wave 2 plans a stable, type-checked surface to wire CRUD/DnD/Playwright on top of.**

## Performance

- **Duration:** 3 min
- **Started:** 2026-05-11T06:02:42Z
- **Completed:** 2026-05-11T06:05:42Z
- **Tasks:** 2
- **Files created:** 7

## Accomplishments

- Per-user, per-scope `useOpenFoldersStorage` hook (250ms debounced localStorage; key `fileExplorer:open:${userId}`)
- Recursive `FolderNode` with lazy `listFolder(path, scope)` fetch on first expand
- D-04 keyboard handler (ArrowRight/Left/Down/Up + Enter/Space ONLY; no Home/End/typeahead)
- D-06 `folderId: string | null` threaded through every level of the recursion via `sub.id ?? null`
- Pitfall-11 visual differentiator on `RootSection` (blue tint Shared / zinc tint My Files)
- D-03 enum `requires_user_reupload` rendered as distinct "Re-upload required" pill in `StatusBadge`
- All five data-* hooks shipped for downstream wiring: `data-folder-path`, `data-folder-id`, `data-scope` (FolderNode), `data-document-id` + `data-scope` (DocumentRow), `data-root-scope` (RootSection)

## Task Commits

1. **Task 1: useOpenFoldersStorage + ScopeBadge + StatusBadge** — `eab3090` (feat)
2. **Task 2: FolderNode + DocumentRow + RootSection + FolderTree** — `e7585c3` (feat)

_Plan metadata commit will be added after this SUMMARY.md write._

## Files Created

- `frontend/src/hooks/useOpenFoldersStorage.ts` — per-user open-folder persistence
- `frontend/src/components/explorer/ScopeBadge.tsx` — Shared/Private pill
- `frontend/src/components/explorer/StatusBadge.tsx` — ingest status + content_markdown_status pills
- `frontend/src/components/explorer/DocumentRow.tsx` — leaf row with badges + data-document-id/data-scope
- `frontend/src/components/explorer/FolderNode.tsx` — recursive node + folderId thread (D-06)
- `frontend/src/components/explorer/FolderTree.tsx` — keyboard host (D-04) + ExpansionContext + useExpandedState
- `frontend/src/components/explorer/RootSection.tsx` — scope wrapper with Pitfall-11 tint

## Public API surface for downstream plans

### `useOpenFoldersStorage(userId: string | null)` returns:

```ts
{
  isOpen: (scope: 'user' | 'global', path: string) => boolean
  toggle: (scope: 'user' | 'global', path: string) => void
  open:   (scope: 'user' | 'global', path: string) => void
  close:  (scope: 'user' | 'global', path: string) => void
}
```

State auto-initializes from `localStorage` key `fileExplorer:open:${userId}` on userId change; writes are 250ms-debounced.

### `<FolderNode>` props

```ts
interface FolderNodeProps {
  scope: 'user' | 'global'
  folderId: string | null              // D-06: UUID; null for root '/' or inferred-only folders
  path: string                          // canonical e.g. '/' or '/projects/2025'
  depth: number                         // 0 for root section's immediate children
  isOpen: boolean
  onToggle: (scope: 'user' | 'global', path: string) => void
  onDeleteDocument?: (id: string) => void
  onRenameDocument?: (id: string, newName: string) => void
}
```

### `<FolderTree>` props

```ts
interface FolderTreeProps {
  scope: 'user' | 'global'
  rootPath: string
  onDeleteDocument?: (id: string) => void
  onRenameDocument?: (id: string, newName: string) => void
}
```

### `<RootSection>` props

```ts
interface RootSectionProps {
  scope: 'user' | 'global'
  onDeleteDocument?: (id: string) => void
  onRenameDocument?: (id: string, newName: string) => void
}
```

### `<DocumentRow>` props

```ts
interface DocumentRowProps {
  doc: UploadedFile                     // from @/lib/api (Plan 06-05)
  depth: number
  onDelete?: (id: string) => void
  onRename?: (id: string, newName: string) => void
}
```

## Confirmed data-* attribute selectors

| Attribute | Element | File | Consumed by |
|-----------|---------|------|-------------|
| `data-folder-path` | FolderNode outer div | FolderNode.tsx | Plan 06-11 Playwright + FolderTree keyboard handler (`active.closest('[data-folder-path]')`) |
| `data-folder-id` | FolderNode outer div | FolderNode.tsx | Plan 06-09 (rename/delete by UUID, disabled when empty string) — D-06 NEW |
| `data-scope` | FolderNode outer div | FolderNode.tsx | Plan 06-10 cross-scope DnD detection |
| `data-document-id` | DocumentRow outer div | DocumentRow.tsx | Plan 06-11 Playwright |
| `data-scope` | DocumentRow outer div | DocumentRow.tsx | Plan 06-10 cross-scope DnD detection |
| `data-root-scope` | RootSection `<section>` | RootSection.tsx | Plan 06-11 Playwright section identification |

## D-04 keyboard scope confirmation (verbatim)

```
$ grep -E "case '(Arrow|Enter| ')" frontend/src/components/explorer/FolderTree.tsx
        case 'ArrowRight':
        case 'ArrowLeft':
        case 'ArrowDown':
        case 'ArrowUp':
        case 'Enter':
        case ' ':
```

Negative gate (Home/End must NOT be present):

```
$ grep -qE "case 'Home'|case 'End'" frontend/src/components/explorer/FolderTree.tsx; echo $?
1
```

Exit code `1` = no match = Home/End absent as required by D-04.

## D-06 grep gate (verbatim)

```
$ grep -E "folderId:\s*string" frontend/src/components/explorer/FolderNode.tsx
  folderId: string | null                            // D-06 / Plan 06-12: UUID of the explicit folders row;
```

The literal `folderId: string` substring appears in the FolderNode props declaration (followed by ` | null`). The recursion uses `sub.id ?? null` to feed the threaded UUID into child `FolderNodeChildBoundary` instances:

```ts
{contents.subfolders.map((sub) => (
  <FolderNodeChildBoundary
    ...
    folderId={sub.id ?? null}                /* D-06: thread UUID through; null for inferred-only */
    ...
  />
))}
```

`data-folder-id={folderId ?? ''}` exposes the UUID (or empty string for null) on the DOM for Plan 06-09 / Plan 06-11.

## TypeScript build status

```
$ cd frontend && npx tsc --noEmit
$ echo $?
0
```

`tsc --noEmit` exits 0 after Task 1 and after Task 2.

## Decisions Made

1. **FolderNodeChildBoundary internal wrapper** — to avoid prop-drilling `isOpen` through every level, recursion goes through a small wrapper that reads from an internal `ExpansionContext` and forwards isOpen to FolderNode. The root FolderNode (rendered by FolderTree) gets isOpen passed explicitly; all descendants get it from context. Cleaner than passing `expansion` as a prop everywhere.
2. **`useExpandedState` lives in FolderTree.tsx** — it is module-internal to the tree subsystem; not promoted to a standalone hook file.
3. **Root '/' opens by default** — `isOpen={expansion.isOpen(scope, rootPath) || rootPath === '/'}` ensures users see top-level content immediately.
4. **`data-folder-id={folderId ?? ''}`** — empty string for null folderId (rather than omitting the attribute) gives Playwright + Plan 06-09 a deterministic selector to assert "no folder UUID".
5. **`onRename` exposed but unused in DocumentRow** — inline rename UI lands in Plan 06-09 per plan note; Wave 1 keeps recursion shape pure.

## Deviations from Plan

None — plan executed exactly as written.

The plan's File B code block placed an `import { useExpandedState } from './FolderTree'` near the bottom of FolderNode.tsx; this was hoisted to the top of the file as a normal ES module import (semantically identical, but matches conventional import order). All required identifiers, props, attributes, and behaviors are present verbatim.

## Issues Encountered

None.

## Known Stubs

None. Every component fetches real data via `listFolder(path, scope)` or receives real props from parents. The `onRename` prop on DocumentRow is declared-but-unused — but the plan EXPLICITLY notes "inline-rename UI lands in Plan 06-09 — this Wave-1 component just exposes the `onRename` callback". The prop is reserved interface surface, not a stub.

## Threat Flags

None — this plan adds no new network endpoints, no auth-path changes, and no schema changes. `listFolder` is the only network call and it was already authenticated + RLS-gated server-side (Plan 03-04 + 06-12). localStorage writes are scoped per-user via the `${userId}` key suffix.

## Self-Check: PASSED

Files (verified via `test -f`):
- ✅ `frontend/src/hooks/useOpenFoldersStorage.ts`
- ✅ `frontend/src/components/explorer/ScopeBadge.tsx`
- ✅ `frontend/src/components/explorer/StatusBadge.tsx`
- ✅ `frontend/src/components/explorer/DocumentRow.tsx`
- ✅ `frontend/src/components/explorer/FolderNode.tsx`
- ✅ `frontend/src/components/explorer/FolderTree.tsx`
- ✅ `frontend/src/components/explorer/RootSection.tsx`

Commits (verified via `git log --oneline -3`):
- ✅ `eab3090 feat(06-06): add useOpenFoldersStorage hook + ScopeBadge + StatusBadge`
- ✅ `e7585c3 feat(06-06): add recursive tree primitives (FolderNode/FolderTree/RootSection/DocumentRow)`

Plan-level verification:
- ✅ `tsc --noEmit` exits 0
- ✅ D-06 grep gate matches `folderId: string` in FolderNode.tsx
- ✅ D-04 positive gate: all 4 ArrowKey cases + Enter + Space present in FolderTree.tsx
- ✅ D-04 negative gate: Home/End absent from FolderTree.tsx
- ✅ Pitfall-11 visual: `bg-blue-50` + `bg-zinc-50` both present in RootSection.tsx
- ✅ Storage key shape `fileExplorer:open:` present in useOpenFoldersStorage.ts
- ✅ Debounce (`setTimeout`) present in useOpenFoldersStorage.ts
- ✅ D-03 enum value `requires_user_reupload` present in StatusBadge.tsx
- ✅ `listFolder(` call present in FolderNode.tsx
- ✅ data-* attributes (folder-path, folder-id, scope, document-id, root-scope) all present

## Next Plan Readiness

- Plan 06-08 (FileExplorerPanel) — can `import { RootSection } from '@/components/explorer/RootSection'` and compose `<RootSection scope="user" />` + `<RootSection scope="global" />` with admin-gated visibility per UI-04.
- Plan 06-09 (ContextMenu CRUD) — can read `folderId` from FolderNode props OR from `data-folder-id` DOM attribute; call `renameFolder(folderId, newPath)` / `deleteFolder(folderId)` directly; disable Rename/Delete affordances when folderId is null.
- Plan 06-10 (@dnd-kit) — can wrap `DocumentRow` in `useDraggable` and FolderNode header div in `useDroppable`; read `data-scope` for cross-scope confirmation prompt.
- Plan 06-11 (Playwright) — can select treeitems via `[data-folder-path="/projects"]`, files via `[data-document-id="..."]`, and scope via `[data-root-scope="global"]`.

---
*Phase: 06-file-explorer-ui-cluster*
*Plan: 06*
*Completed: 2026-05-11*
