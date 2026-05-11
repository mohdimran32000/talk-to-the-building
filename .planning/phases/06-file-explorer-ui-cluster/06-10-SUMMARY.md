---
phase: 06-file-explorer-ui-cluster
plan: 10
subsystem: frontend/dnd
tags: [phase6, frontend, dnd, ui-06, d-01, pitfall11, wave2]
dependency-graph:
  requires:
    - "Plan 06-03 (@dnd-kit/core@6.3.1 dependency installed)"
    - "Plan 06-05 (api.ts: moveDocument(id, folder_path) — same-scope happy path)"
    - "Plan 06-06 (DocumentRow + FolderNode primitives w/ data-scope hooks)"
    - "Plan 06-08 (FileExplorerPanel composition + onPanelClick selection seam)"
    - "Plan 06-09 (FolderNode + DocumentRow CRUD wiring — inline rename / context menu / D-05 hover buttons preserved)"
  provides:
    - "frontend/src/components/explorer/CrossScopeMoveDialog.tsx: informational AlertDialog with D-01 locked copy + zero backend mutation surface"
    - "frontend/src/components/explorer/DocumentRow.tsx: @dnd-kit useDraggable wired on outer treeitem div (id='doc:<id>', data={type:'document', doc})"
    - "frontend/src/components/explorer/FolderNode.tsx: @dnd-kit useDroppable wired on outer treeitem div (id='folder:<scope>:<path>', data={type:'folder', scope, path}); into-folder ring highlight when active drag is a document"
    - "frontend/src/components/FileExplorerPanel.tsx: DndContext wrap of tree body + onDragEnd dispatcher (same-scope→moveDocument, cross-scope→CrossScopeMoveDialog)"
  affects:
    - "Plan 06-11 (Playwright e2e): can drive same-scope drag via page.mouse.down/move/up (NOT page.dragTo per RESEARCH §Wave 0 Gaps); cross-scope drag asserts dialog presence + verifies no moveDocument network call fired"
tech-stack:
  added: []
  patterns:
    - "Pitfall 11 / D-01 STRUCTURAL gate: exactly ONE moveDocument(...) call in FileExplorerPanel.tsx, inside the same-scope conditional — grep-verifiable invariant"
    - "PointerSensor activationConstraint distance:5 — disambiguates click-on-folder (existing onPanelClick selection) from drag-of-document (new dnd-kit drag start)"
    - "useDraggable.disabled = renameMode on DocumentRow — prevents the dnd-kit pointer-down listener from fighting the inline-rename input focus/typing flow"
    - "Drop-target highlight idiom: ring-1 ring-blue-400/40 + bg-blue-400/10 on the treeitem when isOver && active.data.type === 'document' (gated on document drag so future folder-drag would not visually activate)"
    - "AlertDialogDescription asChild + <div> wrapper to host multiple <p> children — avoids HTML-invalid <p>-in-<p> nesting that Radix's default <p> render would cause (Rule 1 deviation: prevents React DOM nesting warning)"
key-files:
  created:
    - frontend/src/components/explorer/CrossScopeMoveDialog.tsx
  modified:
    - frontend/src/components/explorer/DocumentRow.tsx
    - frontend/src/components/explorer/FolderNode.tsx
    - frontend/src/components/FileExplorerPanel.tsx
key-decisions:
  - "Between-row drop indicator (2px horizontal line) DEFERRED to v2 polish — folder-ring highlight on into-folder is the sole drop indicator for v1. UI-06 contract + D-01 modal + same-scope moveDocument call are the LOCKED gates; per-pixel sibling-reorder UX is explicitly noted as acceptable v1 omission in the plan body (line 249–251)."
  - "useDraggable.disabled = renameMode — prevents dnd-kit pointer listeners from blocking the inline-rename input introduced by Plan 06-09. Without this, clicking the document name to enter rename mode would also start a drag and the input would lose focus."
  - "AlertDialogDescription rendered with asChild + <div> wrapper, not the default <p>. The plan's literal code snippet placed two <p> tags as children of AlertDialogDescription, which by default renders a <p> — this produces invalid <p>-inside-<p> HTML (React DOM nesting warning). Switched to asChild+div to keep the visual structure (two paragraphs) while producing valid HTML. The locked-copy strings 'Scope is permanent for security' and 'admin must re-upload' are present verbatim in the rendered text."
  - "5px PointerSensor activationConstraint chosen (not 3px or 10px) — 5px is dnd-kit's idiomatic value for click-vs-drag disambiguation; small enough to feel immediate when dragging, large enough that a normal click on the row to select-folder (existing Plan 06-08 onPanelClick handler) does not trigger a phantom drag start."
  - "Cross-scope branch uses setCrossScopePending(...) — single state-setter; the dialog is mount-gated on the truthy state value, so closing the dialog (onOpenChange(false)) clears state and unmounts the dialog. No imperative `dialog.show()` API surface."
  - "isHotTarget gate includes active?.data.current?.type === 'document' — defends against a future folder-drag feature accidentally highlighting folders as drop targets. v1 explicitly excludes folder-level drag per CONTEXT.md out-of-scope; this guard makes that future extension a one-line addition."
requirements-completed: [UI-06]
metrics:
  duration_minutes: 3
  completed_date: 2026-05-11
  tasks: 3
  files_touched: 4
  commits: 3
---

# Phase 06 Plan 10: Drag-and-Drop Move (UI-06) + D-01 Cross-Scope Block Summary

**One-liner:** Wires `@dnd-kit/core` for single-document drag-move with `useDraggable` on `DocumentRow`, `useDroppable` on `FolderNode`, and a `DndContext`-wrapped `onDragEnd` dispatcher in `FileExplorerPanel.tsx` — same-scope drops call `moveDocument(id, target_path)`, cross-scope drops open the new `CrossScopeMoveDialog` (informational AlertDialog with D-01 LOCKED copy and zero backend mutation surface).

## Performance

- **Duration:** ~3 min
- **Started:** 2026-05-11T06:40:48Z
- **Completed:** 2026-05-11T06:43:19Z
- **Tasks:** 3
- **Files touched:** 4 (1 created, 3 modified)
- **Commits:** 3

## Task Commits

| # | Task                                                                | Commit    | Files                                                                 |
| - | ------------------------------------------------------------------- | --------- | --------------------------------------------------------------------- |
| 1 | Build CrossScopeMoveDialog (D-01 LOCKED informational modal)        | `1e1c515` | frontend/src/components/explorer/CrossScopeMoveDialog.tsx (NEW)       |
| 2 | useDraggable on DocumentRow + useDroppable on FolderNode            | `049fcd7` | frontend/src/components/explorer/DocumentRow.tsx, FolderNode.tsx      |
| 3 | DndContext + onDragEnd dispatcher in FileExplorerPanel.tsx          | `aef39cd` | frontend/src/components/FileExplorerPanel.tsx                          |

## Files Created vs Modified

### Created (1)

- `frontend/src/components/explorer/CrossScopeMoveDialog.tsx` — Informational `AlertDialog` (NOT a destructive dialog) that explains scope immutability and the supported "admin re-upload from target scope" workflow. Single "Got it" Cancel button — no `AlertDialogAction`. No `@/lib/api` import. No `moveDocument`/`fetch`/any backend mutation surface.

### Modified (3)

- `frontend/src/components/explorer/DocumentRow.tsx` — Added `useDraggable({id:'doc:<id>', data:{type:'document', doc}, disabled:renameMode})`. Spreads `attributes`+`listeners` onto outer treeitem div; `ref={setNodeRef}`. Adds `data-dragging` attribute + `cursor-grab active:cursor-grabbing` + `opacity-50` while dragging. `disabled:renameMode` keeps the Plan 06-09 inline-rename flow intact.
- `frontend/src/components/explorer/FolderNode.tsx` — Added `useDroppable({id:'folder:<scope>:<path>', data:{type:'folder', scope, path}})`. `setDropRef` on outer treeitem div. `isHotTarget = isOver && active?.data.current?.type === 'document'` drives a `ring-1 ring-blue-400/40 bg-blue-400/10 rounded-md` highlight. `data-drop-active` attribute exposed for Playwright in Plan 06-11.
- `frontend/src/components/FileExplorerPanel.tsx` — Added `DndContext` wrapping the tree body, `PointerSensor` with `distance:5` activation, `onDragEnd` dispatcher branching same-scope (calls `moveDocument` + toast) vs cross-scope (calls `setCrossScopePending` + mounts `CrossScopeMoveDialog`). Mount-gated dialog with `onOpenChange` clearing state on close.

## Verbatim D-01 grep gate output

```
$ grep -nE "Scope is permanent for security|admin must re-upload" \
    frontend/src/components/explorer/CrossScopeMoveDialog.tsx
44:                Scope is permanent for security. To move <span className="font-mono">{documentName}</span> from {sourceLabel} to {targetLabel},
45:                an admin must re-upload it from the {targetLabel} section.
```

Both locked strings present, verbatim, on adjacent lines inside the description.

## D-01 NO-BACKEND-CALL invariant (verbatim)

```
$ grep -qE "from '@/lib/api'|moveDocument|fetch\(|createFolder|deleteFolder|renameFolder" \
    frontend/src/components/explorer/CrossScopeMoveDialog.tsx; echo $?
1
```

Exit code `1` = no match = `CrossScopeMoveDialog.tsx` has zero `@/lib/api` imports and zero references to any backend mutation function or `fetch(` call. The dialog is structurally incapable of mutating the backend.

## D-01 STRUCTURAL GATE in FileExplorerPanel.tsx (verbatim)

```
$ grep -c "moveDocument(" frontend/src/components/FileExplorerPanel.tsx
1
```

Exactly ONE call site for `moveDocument(`. By construction, that call is inside the `doc.scope === targetData.scope` conditional (the same-scope branch). The cross-scope branch only invokes `setCrossScopePending(...)` to mount the dialog — confirmed by inspecting the diff:

```ts
// Same-scope move — call API (UI-06 happy path)
if (doc.scope === targetData.scope) {
  if (doc.folder_path === targetData.path) return                  // no-op
  try {
    await moveDocument(doc.id, targetData.path)                    // ← only moveDocument call
    toast.success(`Moved "${doc.file_name}" to ${targetData.path}`)
  } catch (err) {
    toast.error(err instanceof Error ? err.message : 'Move failed')
  }
  return
}

// Cross-scope: open the BLOCKING informational dialog (D-01 LOCKED).
// CRITICAL: do NOT call moveDocument here — Migration 015's trigger forbids
// scope mutation at the DB level; this dialog is the friendly explanation.
setCrossScopePending({                                              // ← no API call
  documentName: doc.file_name,
  sourceScope: doc.scope,
  targetScope: targetData.scope,
})
```

## Drop-indicator decision (between-row line vs folder-ring)

**Implemented:** folder-ring highlight only (`ring-1 ring-blue-400/40 bg-blue-400/10 rounded-md` on the treeitem `<div>` when `isOver && active.data.type === 'document'`).

**Deferred to v2 polish:** the 2px horizontal "between sibling rows" indicator for reorder UX. The plan body (line 249–251) explicitly notes this is acceptable for v1: the UI-06 contract (drag a doc onto a folder → moves) + D-01 modal + same-scope `moveDocument` call are the LOCKED gates; per-pixel sibling-reorder UX is explicitly an acceptable v1 omission.

Rationale: implementing the between-row line would require a `useDndMonitor` wrapper at the FolderTree level reading `over.rect` to render an absolutely-positioned 2px element — non-trivial when documents are interleaved with subfolders at the same indent level, and the underlying backend `moveDocument(id, folder_path)` has no concept of intra-folder ordering anyway (Phase 3 / Plan 03-04). The v1 contract: drop on a folder header → moves into that folder. v2 may add visual ordering hints once a backend ordering primitive exists.

## TypeScript build status

```
$ cd frontend && npx tsc --noEmit
$ echo $?
0
```

`tsc --noEmit` exits 0 after every task commit (Tasks 1, 2, 3 verified independently).

## Acceptance criteria (per task)

**Task 1 (CrossScopeMoveDialog):**
- ✅ `test -f frontend/src/components/explorer/CrossScopeMoveDialog.tsx`
- ✅ D-01 PRIMARY GATE: `grep -q "Scope is permanent for security"` matches
- ✅ D-01 SECONDARY GATE: `grep -q "admin must re-upload"` matches
- ✅ D-01 NO-BACKEND-CALL GATE: zero `from '@/lib/api'` imports, zero `moveDocument`/`fetch(`/CRUD function references
- ✅ `npx tsc --noEmit` exits 0

**Task 2 (useDraggable + useDroppable):**
- ✅ `grep -q "useDraggable" frontend/src/components/explorer/DocumentRow.tsx`
- ✅ `grep -q "useDroppable" frontend/src/components/explorer/FolderNode.tsx`
- ✅ `grep -q "isOver" frontend/src/components/explorer/FolderNode.tsx`
- ✅ `grep -qE "ring-(blue|primary)" frontend/src/components/explorer/FolderNode.tsx`
- ✅ `data-document-id` preserved on DocumentRow; `data-folder-path` preserved on FolderNode (Plan 06-11 selectors intact)
- ✅ `npx tsc --noEmit` exits 0

**Task 3 (DndContext + onDragEnd):**
- ✅ `grep -q "DndContext"` in FileExplorerPanel.tsx
- ✅ `grep -q "onDragEnd"` in FileExplorerPanel.tsx
- ✅ `grep -q "moveDocument"` in FileExplorerPanel.tsx (same-scope branch)
- ✅ `grep -q "CrossScopeMoveDialog"` in FileExplorerPanel.tsx (cross-scope branch)
- ✅ D-01 STRUCTURAL GATE: `grep -c "moveDocument("` returns `1`, and that single call is inside the same-scope conditional
- ✅ `grep -q "PointerSensor"` (activation constraint for click vs drag)
- ✅ `npx tsc --noEmit` exits 0

## Decisions Made

1. **Between-row indicator deferred** — folder-ring highlight covers the UI-06 LOCKED contract; per-pixel sibling-reorder UX is v2 polish per plan body. Documented above.
2. **`AlertDialogDescription` with `asChild` + `<div>`** — the plan's literal code placed two `<p>` tags as children of the description. Radix's default description renders a `<p>`, which would produce invalid `<p>`-in-`<p>` HTML and a React DOM-nesting warning. Switching to `asChild` + `<div>` keeps the same visual structure (two paragraphs) and the same locked-copy strings, while producing valid HTML. This is a Rule 1 (bug) preventive fix — the plan's render code as literally written would warn at runtime.
3. **`useDraggable.disabled = renameMode`** — without this, clicking a document name to enter inline-rename (Plan 06-09 UX) would also start a pointer-down drag-listener, blurring the input mid-click. The `disabled` flag suspends dnd-kit's pointer listeners while the row is in rename mode.
4. **`isHotTarget` gates on `active.data.type === 'document'`** — defends a future folder-level drag feature (CONTEXT.md out-of-scope today) from accidentally highlighting folders as drop targets. v1 doesn't support folder drag; this gate makes that future extension safe.
5. **PointerSensor `distance:5`** — dnd-kit idiomatic value; small enough to feel immediate, large enough that the existing onPanelClick selection handler (Plan 06-08) still fires on plain clicks. Tested visually that clicking a folder row still selects it without triggering a drag.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] AlertDialogDescription children would produce invalid <p>-in-<p> HTML**
- **Found during:** Task 1
- **Issue:** The plan's literal code block placed `<p>...</p>` and `<p className="mt-2 ...">...</p>` as direct children of `<AlertDialogDescription>`. Radix UI's `AlertDialogDescription` renders a `<p>` by default; nesting `<p>` inside `<p>` is invalid HTML and produces a React DOM-nesting console warning at runtime.
- **Fix:** Used `asChild` prop on `AlertDialogDescription` and replaced the inner element with a `<div>` wrapper containing both `<p>` paragraphs. Visual structure preserved verbatim. The locked D-01 copy strings ("Scope is permanent for security" + "admin must re-upload") are still present, literally, in the rendered DOM.
- **Files modified:** `frontend/src/components/explorer/CrossScopeMoveDialog.tsx`
- **Commit:** `1e1c515` (initial creation — fix applied during write)

No other deviations. Tasks 2 + 3 executed exactly as written.

## Issues Encountered

None.

## Known Stubs

None. Every wired path uses real data:
- `useDraggable`/`useDroppable` data payloads carry the live `doc` object and `{scope, path}` from props.
- `onDragEnd` calls the real `moveDocument` API (Plan 06-05) on same-scope.
- `CrossScopeMoveDialog` renders props-supplied `documentName`/`sourceScope`/`targetScope` strings — no mock data, no placeholders.

The folder-ring highlight is a styling concern, not a data stub.

## Threat Flags

None — no new network endpoints, no auth-path changes, no schema changes. The single new network call (`moveDocument` in the same-scope branch) hits an endpoint that was already authenticated + RLS-gated server-side (Phase 3 / Plan 03-04). Migration 015's `forbid_scope_mutation` trigger remains the row-level source of truth for scope immutability; the new `CrossScopeMoveDialog` is a friendly UI explanation, not a security boundary.

The D-01 informational dialog is *defensive in depth*: even if a future developer accidentally wired the cross-scope branch to call `moveDocument`, the DB trigger would still 422/forbid the write. The dialog's "zero backend mutation surface" property (verifiable by grep) makes that future mistake structurally impossible at the UI layer.

## Self-Check: PASSED

**Files exist:**
- ✅ FOUND: `frontend/src/components/explorer/CrossScopeMoveDialog.tsx`
- ✅ FOUND (modified): `frontend/src/components/explorer/DocumentRow.tsx`
- ✅ FOUND (modified): `frontend/src/components/explorer/FolderNode.tsx`
- ✅ FOUND (modified): `frontend/src/components/FileExplorerPanel.tsx`

**Commits exist (verified via `git log --oneline -4`):**
- ✅ `1e1c515 feat(06-10): add CrossScopeMoveDialog (D-01 LOCKED informational modal)`
- ✅ `049fcd7 feat(06-10): wire useDraggable on DocumentRow + useDroppable on FolderNode`
- ✅ `aef39cd feat(06-10): wrap tree body in DndContext + onDragEnd dispatcher (UI-06)`

**Plan-level verification:**
- ✅ `tsc --noEmit` exits 0
- ✅ D-01 PRIMARY GATE: "Scope is permanent for security" present in CrossScopeMoveDialog.tsx
- ✅ D-01 SECONDARY GATE: "admin must re-upload" present in CrossScopeMoveDialog.tsx
- ✅ D-01 NO-BACKEND-CALL GATE: zero `@/lib/api` imports / mutation calls / `fetch(` in CrossScopeMoveDialog.tsx
- ✅ D-01 STRUCTURAL GATE: exactly 1 `moveDocument(` call in FileExplorerPanel.tsx, inside the same-scope conditional
- ✅ `useDraggable` present in DocumentRow.tsx
- ✅ `useDroppable` + `isOver` + `ring-blue` present in FolderNode.tsx
- ✅ `PointerSensor` + `DndContext` + `CrossScopeMoveDialog` mount all present in FileExplorerPanel.tsx
- ✅ Data attributes preserved (`data-document-id`, `data-folder-path`, `data-scope`) for Plan 06-11 Playwright

## Next Plan Readiness

- **Plan 06-11 (Playwright e2e for CRUD + DnD):** Selectors stable. Drag-test guidance: use `page.mouse.down/move/up` against `[data-document-id]` → `[data-folder-path]` (per RESEARCH.md §Wave 0 Gaps line 698 — `page.dragTo()` is incompatible with `@dnd-kit` pointer-event listeners). Cross-scope test: assert the AlertDialog appears with "Scope is permanent for security" text + zero network call to `/api/files/<id>/move`.

---
*Phase: 06-file-explorer-ui-cluster*
*Plan: 10*
*Completed: 2026-05-11*
