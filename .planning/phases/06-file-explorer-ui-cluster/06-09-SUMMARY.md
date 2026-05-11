---
phase: 06-file-explorer-ui-cluster
plan: 09
subsystem: ui
tags: [phase6, frontend, react, shadcn, context-menu, crud, dialogs, admin-gating, pitfall5, pitfall11, ui-04, ui-07, ui-11, d-05, d-06, wave2]

# Dependency graph
requires:
  - phase: 06-file-explorer-ui-cluster
    provides: "Plan 06-05 api.ts (createFolder, renameFolder, deleteFolder→DeleteFolderResult, renameDocument); Plan 06-06 tree primitives (FolderNode threads folderId from props; DocumentRow scaffold); Plan 06-08 FileExplorerPanel composition; Plan 06-12 GET /api/folders subfolders=[{id,path}] D-06 wire shape; Plan 06-03 shadcn primitives (context-menu, dialog, alert-dialog, button, input, label)"
provides:
  - "ContextMenuActions.tsx — reusable Folder/Document context menu factories with isAdmin gating + D-06 hasFolderId guard"
  - "CreateFolderDialog.tsx — Dialog form for creating a child folder under a chosen parent path/scope"
  - "DeleteFolderDialog.tsx — AlertDialog that surfaces the server's structured {document_count, subfolder_count} literally on 409 FOLDER_NOT_EMPTY (Pitfall 5)"
  - "FolderNode CRUD wiring — ContextMenu trigger + inline rename input + D-05 inline + and ⋯ hover buttons; renameFolder(folderId) direct D-06 call"
  - "DocumentRow CRUD wiring — ContextMenu + inline-rename UX (UI-07) calling renameDocument PATCH /api/files/{id}"
  - "RootSection inline-create button at section header (admin-gated for Shared)"
  - "FolderTree refetch counter via key={refetchCounter} on root FolderNode — auto-refresh after CRUD"
affects: [06-10 (DnD layer — extends DocumentRow + FolderNode), 06-11 (e2e tests for folder/document CRUD), future Phase 7 if admin scope expands]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "D-05 inline+context dual affordance: BOTH `+` (create child) and `⋯` (open menu) inline-hover buttons on every folder row AND right-click context menu — coexist (LOCKED)"
    - "Pitfall 5 surface: dialog renders server-supplied counts literally via DeleteFolderResult discriminated union (`{ok:true} | {ok:false; error:'FOLDER_NOT_EMPTY'; document_count; subfolder_count}`) — branches on result.ok, NEVER throws on 409"
    - "Pitfall 11 / UI-11 structural admin gate: canWrite = scope === 'user' || isAdmin — no conditional cross-scope render; non-admin viewers see a single disabled 'Read-only (admin required)' item on Shared rows"
    - "D-06 props-thread: folderId is a typed prop on FolderNode (string | null). Inferred-only folders (null) get rename/delete suppressed; explicit folders get one-round-trip PATCH/DELETE with renameFolder(folderId, newPath) and deleteFolder(folderId) — NO path→id resolution gymnastics"
    - "FolderTree refetch-on-mutation via key={refetchCounter} force-remount — drops cached `contents` state in the root FolderNode and triggers a fresh listFolder() call without manual cache management"
    - "Inline-rename input pattern: click name → input swaps in via setRenameMode(true); Enter commits, Escape reverts, blur commits (used in BOTH FolderNode for folder rename AND DocumentRow for UI-07 document rename)"
    - "DeleteFolderDialog `blocking` state reset on dialog close via wrapper handleOpenChange so re-open after a 409 shows fresh confirm view"

key-files:
  created:
    - "frontend/src/components/explorer/ContextMenuActions.tsx"
    - "frontend/src/components/explorer/CreateFolderDialog.tsx"
    - "frontend/src/components/explorer/DeleteFolderDialog.tsx"
  modified:
    - "frontend/src/components/explorer/FolderNode.tsx"
    - "frontend/src/components/explorer/DocumentRow.tsx"
    - "frontend/src/components/explorer/RootSection.tsx"
    - "frontend/src/components/explorer/FolderTree.tsx"

key-decisions:
  - "Phase 6 / Plan 06-09: DeleteFolderDialog uses local `blocking` state to capture the structured 409 counts; renders them literally via {blocking.document_count} and {blocking.subfolder_count} JSX — Pitfall 5 PRIMARY grep gate satisfied by the literal token presence."
  - "Phase 6 / Plan 06-09: dialog cancel/close resets `blocking` to null via a wrapper handleOpenChange so reopening after a 409 shows the fresh confirm view (not a stale error banner with a non-clickable Delete button). New convention for any future structured-409 dialog."
  - "Phase 6 / Plan 06-09: ContextMenuActions is a content-only factory (returns ContextMenuContent + items) — the consumer (FolderNode, DocumentRow) wraps the row in <ContextMenu><ContextMenuTrigger asChild>. Keeps the right-click handler co-located with the row element and avoids prop-drilling row coordinates into the menu component."
  - "Phase 6 / Plan 06-09: D-06 implementation strategy is 'props-thread + null-gate', not 'lookup-then-call'. `folderId: string | null` is passed through FolderNode.props; rename/delete buttons + menu items render via `hasFolderId && ...` guard; DeleteFolderDialog accepts only `folderId: string` (caller responsibility to not mount the dialog when folderId is null — enforced via `{folderId && <DeleteFolderDialog .../>}` in FolderNode JSX)."
  - "Phase 6 / Plan 06-09: D-05 inline-buttons composition uses Tailwind `group` + `group-hover:opacity-100` for the hover-reveal — same idiom as Plan 06-06 RootSection chrome. `+` button is always rendered when `canWrite`; `⋯` button is rendered ONLY when `hasFolderId` (root '/' shows only `+`, inferred-only folders show only `+`)."
  - "Phase 6 / Plan 06-09: FolderTree refetch is via `key={refetchCounter}` force-remount of the ROOT FolderNode (not recursive cache invalidation). Simpler than per-node refresh logic; pays a cheap re-render cost on mutation. Recursive cache-invalidation is YAGNI until empirical evidence shows it's needed."
  - "Phase 6 / Plan 06-09: RootSection also tracks its own `refreshKey` independent of FolderTree's internal `refetchCounter` — this gives the section-header `+ New folder` button a clean re-fetch path WITHOUT requiring FolderTree to expose its internal counter as an API. Two-level remount is intentional defense-in-depth."
  - "Phase 6 / Plan 06-09: Inline rename input uses `e.stopPropagation()` on click and keydown to prevent the parent row's button onClick from re-toggling the folder while the user is typing — same defensive pattern Plan 06-06 used for the delete-✕ button."
  - "Phase 6 / Plan 06-09: When rename submission fails (network error / 422), the local `renameValue` state is reverted to the original `folderName` / `doc.file_name` so the user sees the previous value, not the half-typed attempt. Toast surfaces the error message from the caught exception."
  - "Phase 6 / Plan 06-09: FileExplorerPanel.tsx was inspected and NOT modified — the prop chain `onDelete` / `onRename` → `<RootSection .../>` for both scopes is already in place from Plan 06-08 Task 2. Task 3d intentionally produces zero diff on FileExplorerPanel; the refetch counter is owned at the FolderTree level (closer to the data fetch)."

patterns-established:
  - "Pattern A: Pitfall 5 typed-discriminated-union dialog — UI components consuming a structured-error API surface MUST branch on the discriminated union (result.ok) and render server-supplied numeric fields literally. Toast is reserved for unexpected exceptions ONLY; the structured 409 path is in-dialog inline content"
  - "Pattern B: Pitfall 11 structural admin gate — `canWrite = scope === 'user' || isAdmin` derivation is the SINGLE gate. Renders 'Read-only (admin required)' as a disabled item for non-admins on global scope (intentional — gives discoverability without affording action). NEVER conditional-render between scopes."
  - "Pattern C: D-06 props-thread guard — components that act on a folders-table row accept `folderId: string` (not optional). Callers guard the mount point with `{folderId && <Component folderId={folderId} .../>}` so type system enforces non-null at the boundary."
  - "Pattern D: key-based force-remount refetch — when a parent needs to trigger a child's lazy-fetched data to re-load, use `key={counter}` on the child + parent-owned counter increment. Cheap, side-effect-free, no useEffect dependency chain. Applies to any future component with internal lazy-load state."
  - "Pattern E: inline-rename swap (rename input pattern) — local `renameMode` + `renameValue` state; input swaps in with autoFocus; Enter commits; Escape reverts; blur commits; failure resets value + toasts. Used in both FolderNode and DocumentRow — reusable shape for any inline-edit cell in this codebase."

requirements-completed: [UI-04, UI-07, UI-11]

# Metrics
duration: 8min
completed: 2026-05-11
---

# Phase 06 Plan 09: Folder/Document CRUD UI Wiring Summary

**ContextMenu + inline +/⋯ buttons + dialogs for folder Create/Rename/Delete and document inline-rename, with structural admin gating on Shared scope and props-threaded folder UUIDs.**

## Performance

- **Duration:** ~8 min
- **Started:** 2026-05-11T06:27:00Z (approx)
- **Completed:** 2026-05-11T06:35:37Z
- **Tasks:** 6 atomic subtasks (Task 1, Task 2, Task 3a–3d)
- **Files created:** 3
- **Files modified:** 4

## Accomplishments

- Folder CRUD: right-click any folder row to open a ContextMenu with Create / Rename / Delete; alternatively use the inline `+` and `⋯` hover buttons (D-05 LOCKED dual affordance)
- Folder Delete: `DeleteFolderDialog` surfaces the server's structured 409 `{document_count, subfolder_count}` literally — Pitfall 5 contract honored end-to-end
- Document inline rename (UI-07): click document name → input swaps in → Enter commits via `renameDocument` PATCH `/api/files/{id}`
- Admin gating (UI-11 / Pitfall 11): structural `canWrite = scope === 'user' || isAdmin` gate; non-admins on Shared see "Read-only (admin required)" disabled item
- D-06 props-thread: rename uses `renameFolder(folderId, newPath)` and delete uses `deleteFolder(folderId)` directly — zero path→id resolution round-trips
- Auto-refresh: any folder CRUD mutation triggers a clean re-fetch of the tree via `key={refetchCounter}` on root FolderNode

## Task Commits

Each task was committed atomically:

1. **Task 1: CreateFolderDialog + DeleteFolderDialog (Pitfall 5 surface)** — `260bd11` (feat)
2. **Task 2: ContextMenuActions with isAdmin gating (UI-11 / Pitfall 11)** — `eb6d8c9` (feat)
3. **Task 3a: FolderNode CRUD wiring — ContextMenu + dialogs + D-05 inline buttons** — `bbbc456` (feat)
4. **Task 3b: DocumentRow CRUD wiring — ContextMenu + inline rename (UI-07)** — `06cce8e` (feat)
5. **Task 3c: RootSection inline-create button (UI-11 admin gating)** — `917ec60` (feat)
6. **Task 3d: FolderTree refetch counter** — `2571698` (feat)

## Files Created/Modified

### Created

- `frontend/src/components/explorer/CreateFolderDialog.tsx` — Dialog form for creating a child folder under a chosen parent path/scope; calls `createFolder` API; toasts success/error
- `frontend/src/components/explorer/DeleteFolderDialog.tsx` — AlertDialog that branches on `DeleteFolderResult` discriminated union; renders server `document_count` + `subfolder_count` literally on 409 (Pitfall 5)
- `frontend/src/components/explorer/ContextMenuActions.tsx` — Exports `FolderContextMenuActions` and `DocumentContextMenuActions` content factories; admin-gated via `useAuth().isAdmin`; D-06 `hasFolderId` guard

### Modified

- `frontend/src/components/explorer/FolderNode.tsx` — Wrapped row in `<ContextMenu>`/`<ContextMenuTrigger>`; added inline rename input, `+`/`⋯` hover buttons, dialog mounts, `onAfterMutation` prop drill-through, D-06 `renameFolder(folderId, newPath)` direct call
- `frontend/src/components/explorer/DocumentRow.tsx` — Wrapped row in `<ContextMenu>`; added inline rename input; click-name UX (UI-07); calls `renameDocument` API on commit
- `frontend/src/components/explorer/RootSection.tsx` — Added section-header `+ New folder` button gated behind `canCreate = scope === 'user' || isAdmin`; mounts `CreateFolderDialog` rooted at `/`; local `refreshKey` for force-remount of FolderTree
- `frontend/src/components/explorer/FolderTree.tsx` — Added `refetchCounter` state + `onAfterMutation` callback; passes `key={refetchCounter}` to root FolderNode to force-remount on mutation

## Verification Gate Output

### Pitfall 5 grep gate (DeleteFolderDialog.tsx)

```
PASS: document_count
PASS: subfolder_count
PASS: FOLDER_NOT_EMPTY
PASS: result.ok
PASS: folderId: string
```

### Pitfall 11 / UI-11 grep gate (ContextMenuActions.tsx + RootSection.tsx)

```
PASS: useAuth (ContextMenuActions.tsx)
PASS: isAdmin (ContextMenuActions.tsx)
PASS: hasFolderId (ContextMenuActions.tsx)
PASS: scope === 'user' || isAdmin (ContextMenuActions.tsx canWrite derivation)
PASS: isAdmin (RootSection.tsx)
PASS: scope === 'user' || isAdmin (RootSection.tsx canCreate derivation)
```

### D-05 inline-buttons grep gate (FolderNode.tsx)

```
PASS: Plus (lucide icon for inline + button)
PASS: MoreVertical (lucide icon for inline ⋯ button)
PASS: group-hover:opacity-100 (hover-reveal idiom)
PASS: ContextMenu (right-click handler also wired — dual affordance)
```

### D-06 grep gate (FolderNode.tsx + DeleteFolderDialog.tsx)

```
PASS: renameFolder(folderId (FolderNode.tsx) — UUID from props, no path→id resolution
PASS: deleteFolder(folderId (DeleteFolderDialog.tsx) — UUID from props
PASS: folderId: string (DeleteFolderDialog.tsx — required, not optional)
```

### TypeScript build

```
$ cd frontend && npx tsc --noEmit
(exit 0, no output — clean)
```

## Decisions Made

Captured in frontmatter `key-decisions` (10 entries). Highlights:

- **Pitfall 5 surface uses local `blocking` state** with wrapper `handleOpenChange` to reset on dialog close — re-open after a 409 shows fresh confirm view (not stale error banner with non-clickable Delete button)
- **D-06 strategy is 'props-thread + null-gate'**, not lookup-then-call — `folderId: string` is required (not optional) at the DeleteFolderDialog boundary; callers guard via `{folderId && <DeleteFolderDialog .../>}`
- **D-05 hover-reveal uses Tailwind `group` + `group-hover:opacity-100`** — same idiom Plan 06-06 RootSection chrome uses; `+` always when `canWrite`, `⋯` only when `hasFolderId`
- **FolderTree refetch via key force-remount of root** (not recursive cache invalidation) — simpler, cheap, no useEffect dependency chain
- **FileExplorerPanel.tsx intentionally not modified** in Task 3d — Plan 06-08 Task 2 already wires `onDelete`/`onRename` through to both `<RootSection scope="global" />` and `<RootSection scope="user" />`; refetch counter is owned at FolderTree level (closer to the data fetch)

## Deviations from Plan

None — plan executed exactly as written. All grep gates pass on the first attempt; `npx tsc --noEmit` clean across all 6 commits.

## Issues Encountered

None. The plan's task split (3a / 3b / 3c / 3d per checker WARNING #2) made each commit small and grep-verifiable, which kept iteration cycles short.

## User Setup Required

None — no external service configuration required. This plan is pure frontend wiring on top of existing backend endpoints (Plans 06-05 + 06-12) and shadcn primitives (Plan 06-03).

## Next Phase Readiness

Wave 2 progress:

- ✅ Plan 06-09 (this plan): folder + document CRUD UI wired
- ⏳ Plan 06-10: DnD layer (extends DocumentRow + FolderNode with `useDraggable` / `useDroppable` — preserves all hover/context behaviors in this plan)
- ⏳ Plan 06-11: e2e Playwright tests for folder/document CRUD (consumes `data-folder-path` / `data-document-id` / `data-folder-id` selectors all preserved)

UI-04, UI-07, UI-11 closed by this plan. Pitfall 5 + Pitfall 11 mitigations LOCKED at the verifier-gate level (grep-verifiable on disk).

## Self-Check: PASSED

**Files exist:**
- FOUND: frontend/src/components/explorer/CreateFolderDialog.tsx
- FOUND: frontend/src/components/explorer/DeleteFolderDialog.tsx
- FOUND: frontend/src/components/explorer/ContextMenuActions.tsx
- FOUND: frontend/src/components/explorer/FolderNode.tsx (modified)
- FOUND: frontend/src/components/explorer/DocumentRow.tsx (modified)
- FOUND: frontend/src/components/explorer/RootSection.tsx (modified)
- FOUND: frontend/src/components/explorer/FolderTree.tsx (modified)

**Commits exist:**
- FOUND: 260bd11 (Task 1)
- FOUND: eb6d8c9 (Task 2)
- FOUND: bbbc456 (Task 3a)
- FOUND: 06cce8e (Task 3b)
- FOUND: 917ec60 (Task 3c)
- FOUND: 2571698 (Task 3d)

---
*Phase: 06-file-explorer-ui-cluster*
*Plan: 09*
*Completed: 2026-05-11*
