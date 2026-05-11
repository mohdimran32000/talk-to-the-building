---
phase: 06-file-explorer-ui-cluster
plan: 08
subsystem: frontend/explorer-panel
tags: [phase6, frontend, panel, wave2, ui-01, ui-02, ui-05, ui-08, ui-11, d-03]
dependency-graph:
  requires:
    - "Plan 06-05 (api.ts: extended uploadFile(file, folder_path, scope) + UploadedFile.content_markdown_status)"
    - "Plan 06-06 (RootSection / FolderTree / FolderNode / DocumentRow primitives)"
    - "Plan 06-07 (Chat.tsx liveSubAgentTrace state shape — preserved untouched)"
  provides:
    - "frontend/src/components/explorer/Breadcrumbs.tsx: path-splitting clickable segment list (first breadcrumb in project)"
    - "frontend/src/components/FileExplorerPanel.tsx: top-level panel composing TWO <RootSection> instances (Pitfall 11) + breadcrumbs header + upload affordance"
    - "frontend/src/pages/Chat.tsx: FileExplorerPanel mount; extended handleUploadFile(file, folder_path, scope); LOCKED 4-arg handleStatusUpdate signature"
  affects:
    - "Plan 06-09 (ContextMenu CRUD) — can hook into FileExplorerPanel's onPanelClick selection + Breadcrumbs.onNavigate"
    - "Plan 06-10 (@dnd-kit) — FileExplorerPanel body uses data-folder-path selection seam; DnD wraps RootSection children"
    - "Plan 06-11 (Playwright) — selects the panel via data-testid='file-explorer-body' and both root sections via data-root-scope"
tech-stack:
  added: ["lucide-react icons: Upload, Home (additional usages)"]
  patterns:
    - "UI-01 verifier-gate: legacy file deletion + mount swap committed atomically (single commit dab3064)"
    - "UI-02 invariant: two-RootSection composition stacked vertically, NEVER a Tabs primitive (grep '<Tabs' returns 0 matches)"
    - "UI-05 upload-into-selected-folder: handleFileChange reads selectedFolder state + UI-11 non-admin fallback to user/'/' if selected scope is global"
    - "UI-08 breadcrumb pattern: split path on '/', filter Boolean, cumulative paths for click handlers, Home icon for root + ChevronRight separators"
    - "D-03 / content_markdown_status polling: FileExplorerPanel watches pending content_markdown_status (in addition to status); 4-arg onStatusUpdate propagates the new field"
    - "Checker WARNING #3 LOCKED signature: handleStatusUpdate(id, status, errorMessage?, contentMarkdownStatus?) — 4th optional arg preserves prior field on undefined"
    - "data-* selection seam: onPanelClick uses event-delegation closest('[data-folder-path]') reading data-scope attribute — no React refs threaded through FolderNode"
key-files:
  created:
    - frontend/src/components/explorer/Breadcrumbs.tsx
    - frontend/src/components/FileExplorerPanel.tsx
  modified:
    - frontend/src/pages/Chat.tsx
  deleted:
    - frontend/src/components/FileUploadPanel.tsx
key-decisions:
  - "Atomic Task 3 commit (mount swap + delete in same commit dab3064) — UI-01 verifier-gate; PATTERNS.md / RESEARCH.md §UI-01 explicit requirement"
  - "Scrubbed a stray 'FileUploadPanel.tsx' reference from a comment in FileExplorerPanel.tsx to satisfy the orphan-grep gate verbatim (grep -rn 'FileUploadPanel' frontend/src/ → no matches)"
  - "metadataSchema prop accepted but renamed to '_metadataSchema' (no-unused-vars convention) — wired through but not rendered until Plan 06-09 expands DocumentRow detail"
  - "Default scope='user' on handleUploadFile extension preserves the single existing one-arg call-site assumption from Plan 06-05 back-compat audit"
  - "Upload button respects selectedFolder state; non-admin selecting global falls back silently to user/'/' (UI-11 defense — admin-only writes to global are server-side enforced too)"
  - "FolderTree/FolderNode keyboard handler is untouched; the new onPanelClick selection wrapper is additive — does not interfere with the existing role='tree' keyboard handler"
requirements-completed: [UI-01, UI-02, UI-03, UI-05, UI-08]
metrics:
  duration_minutes: 4
  completed_date: 2026-05-11
  tasks: 3
  files_touched: 4
  commits: 3
---

# Phase 06 Plan 08: FileExplorerPanel Composition + Mount Swap Summary

**One-liner:** Composes the Wave-1 tree primitives into the production `FileExplorerPanel` (two stacked `<RootSection>`s + Breadcrumbs + upload affordance), swaps the Chat.tsx mount point from `<FileUploadPanel>` to `<FileExplorerPanel>`, deletes the legacy panel in the same commit (UI-01 verifier-gate), and locks the 4-arg `handleStatusUpdate` signature so `content_markdown_status` propagates end-to-end through polling (D-03 / checker WARNING #3).

## Performance

- **Duration:** ~4 min
- **Started:** 2026-05-11T06:23:30Z (approx, after init)
- **Completed:** 2026-05-11T06:27:45Z
- **Tasks:** 3
- **Files touched:** 4 (2 created, 1 modified, 1 deleted)
- **Commits:** 3

## Task Commits

| # | Task | Commit | Files | Diff |
| - | ---- | ------ | ----- | ---- |
| 1 | Build Breadcrumbs.tsx | `feba471` | frontend/src/components/explorer/Breadcrumbs.tsx | +41 |
| 2 | Build FileExplorerPanel.tsx — two-RootSection composition + 4-arg polling | `4050349` | frontend/src/components/FileExplorerPanel.tsx | +128 |
| 3 | Swap Chat.tsx mount to FileExplorerPanel + delete FileUploadPanel (atomic UI-01 commit) | `dab3064` | frontend/src/pages/Chat.tsx (+32/-9), frontend/src/components/FileExplorerPanel.tsx (+1/-1 comment scrub), frontend/src/components/FileUploadPanel.tsx (DELETED -195) | net -177 |

## UI-02 invariant (verbatim grep gate output)

```
$ grep -c '<RootSection' frontend/src/components/FileExplorerPanel.tsx
2

$ grep -q '<Tabs' frontend/src/components/FileExplorerPanel.tsx; echo $?
1
```

Two `<RootSection>` instances stacked vertically (scope="global" first, scope="user" second). Zero `<Tabs>` usage — the panel is explicitly NOT a tabbed UI per Pitfall 11.

## UI-01 verifier-gate (verbatim)

```
$ test -f frontend/src/components/FileUploadPanel.tsx; echo $?
1

$ grep -rn "FileUploadPanel" frontend/src/
(no matches — exit 1)

$ git diff --diff-filter=D --name-only HEAD~1 HEAD
frontend/src/components/FileUploadPanel.tsx
```

`FileUploadPanel.tsx` deletion landed in the SAME commit (`dab3064`) as the Chat.tsx mount swap — UI-01 verifier-gate satisfied. No orphan references anywhere in `frontend/src/`.

## Chat.tsx — new `handleUploadFile` signature

```ts
const handleUploadFile = async (file: File, folder_path: string = '/', scope: 'user' | 'global' = 'user') => {
  setIsUploading(true)
  try {
    const uploaded = await uploadFile(file, folder_path, scope)
    // ...action branching unchanged
  } finally {
    setIsUploading(false)
  }
}
```

Default arguments preserve any latent one-arg callers; `FileExplorerPanel` always supplies all three.

## Chat.tsx — LOCKED `handleStatusUpdate` signature (checker WARNING #3)

```ts
const handleStatusUpdate = useCallback((
  documentId: string,
  status: string,
  errorMessage?: string,
  contentMarkdownStatus?: string,    // NEW — Phase 6 / D-03 / checker WARNING #3
) => {
  setFiles((prev) =>
    prev.map((f) => {
      if (f.id !== documentId) return f
      return {
        ...f,
        status: status as UploadedFile['status'],
        error_message: errorMessage ?? f.error_message,
        // Only overwrite content_markdown_status if the polling callback supplied a non-null value
        content_markdown_status:
          contentMarkdownStatus !== undefined
            ? (contentMarkdownStatus as UploadedFile['content_markdown_status'])
            : f.content_markdown_status,
      }
    })
  )
  // ...ready-reload tail unchanged
}, [])
```

**Verbatim grep gate output:**

```
$ grep -E "handleStatusUpdate.*contentMarkdownStatus|contentMarkdownStatus\?:\s*string" frontend/src/pages/Chat.tsx
    contentMarkdownStatus?: string,
```

`FileExplorerPanel.tsx` polling effect calls the handler with all four args:

```
$ grep -q "onStatusUpdate(doc.id, doc.status, doc.error_message, doc.content_markdown_status)" \
    frontend/src/components/FileExplorerPanel.tsx; echo $?
0
```

## FileExplorerPanel composition map

```
<div className="flex flex-col h-full">
  <div className="border-b ... flex items-center justify-between">    ← header bar
    <Breadcrumbs path scopeLabel onNavigate />                          ← UI-08
    <Button onClick={inputRef.current?.click()}>                        ← UI-05 upload affordance
      <Upload /> Upload
    </Button>
  </div>
  <div data-testid="file-explorer-body" onClick={onPanelClick}>          ← selection delegation
    <RootSection scope="global" onDeleteDocument onRenameDocument />     ← Pitfall 11 / UI-02 — Shared on top
    <RootSection scope="user"   onDeleteDocument onRenameDocument />     ← UI-04 — My Files below
  </div>
</div>
```

`onPanelClick` reads `closest('[data-folder-path]')` + the `data-scope` attribute (both set by Plan 06-06's FolderNode) to update `selectedFolder` — no refs threaded through the tree.

## TypeScript build status

```
$ cd frontend && npx tsc --noEmit
$ echo $?
0
```

`tsc --noEmit` exits 0 after every task commit (Tasks 1, 2, 3 verified independently).

## Acceptance criteria (per task)

**Task 1 (Breadcrumbs):**
- ✅ `test -f frontend/src/components/explorer/Breadcrumbs.tsx`
- ✅ `grep -q "export function Breadcrumbs"` (matches)
- ✅ `grep -q "split('/')"` (path-split present)
- ✅ `grep -q "ChevronRight"` (separator icon present)
- ✅ `npx tsc --noEmit` exits 0

**Task 2 (FileExplorerPanel):**
- ✅ `test -f frontend/src/components/FileExplorerPanel.tsx`
- ✅ `grep -c '<RootSection'` returns 2 (two instances)
- ✅ Both `scope="global"` and `scope="user"` present
- ✅ No `<Tabs` (UI-02 invariant)
- ✅ `Breadcrumbs` imported and used
- ✅ `selectedFolder` state present
- ✅ `content_markdown_status` watched in polling effect
- ✅ `isAdmin` used for UI-11 fallback
- ✅ CHECKER WARNING #3 GATE: 4-arg `onStatusUpdate(doc.id, doc.status, doc.error_message, doc.content_markdown_status)` present
- ✅ `npx tsc --noEmit` exits 0

**Task 3 (mount swap + delete):**
- ✅ `grep -q "FileExplorerPanel"` in Chat.tsx
- ✅ NO `FileUploadPanel` in Chat.tsx
- ✅ `FileUploadPanel.tsx` deleted (UI-01 verifier-gate)
- ✅ No `FileUploadPanel` references anywhere in `frontend/src/`
- ✅ Extended `handleUploadFile` signature with `folder_path` + `scope`
- ✅ CHECKER WARNING #3 GATE: `handleStatusUpdate` accepts `contentMarkdownStatus?: string`
- ✅ `npx tsc --noEmit` exits 0
- ✅ Mount swap + delete in same commit (`dab3064`)

## Decisions Made

1. **Atomic Task 3 commit** — The plan + PATTERNS.md / RESEARCH.md §UI-01 explicitly require mount swap and `FileUploadPanel.tsx` deletion in the SAME commit; staging both then committing once (commit `dab3064`) satisfies this verifier-gate.
2. **Comment scrub for orphan-grep gate** — `FileExplorerPanel.tsx` originally referenced `FileUploadPanel.tsx:60-85` in a comment crediting the polling-pattern source. The plan's verifier-grep `grep -rn "FileUploadPanel" frontend/src/` would surface this comment. Scrubbed to "Polling pattern — ALSO poll content_markdown_status (D-03 / UI-08)" — same code provenance is captured by this SUMMARY.md, the grep gate passes verbatim.
3. **`metadataSchema` renamed to `_metadataSchema`** — TypeScript's noUnusedParameters would flag the prop; renamed with leading underscore to forward the prop through the interface (so Plan 06-09 can wire it to DocumentRow detail expansion later) without triggering an unused-variable warning.
4. **Default scope='user' on handleUploadFile** — matches Plan 06-05's default; the existing one-arg call shape `handleUploadFile(file)` continues to type-check (relevant for any latent caller; FileExplorerPanel always passes all three).
5. **UI-11 fallback inline at the panel** — `handleFileChange` enforces `targetScope === 'global' && !isAdmin → safeScope='user', safePath='/'`. Server-side admin-only writes to global are still enforced (Phase 3 router); this is defensive UI-side guarding so the upload doesn't 403 surprise the user.

## Deviations from Plan

None requiring user adjudication. One minor non-content deviation:

**1. [Rule 2 - critical-functionality / lint hygiene] `metadataSchema` renamed to `_metadataSchema`**
- **Found during:** Task 2 (tsc unused-variable diagnostic)
- **Issue:** TypeScript's `noUnusedParameters` flagged the `metadataSchema` prop as unused (the plan explicitly says "metadataSchema is accepted but not yet wired into the panel — Plan 06-09 may render it inside DocumentRow detail expansion").
- **Fix:** Renamed the destructured param to `_metadataSchema` (leading underscore satisfies the lint convention) — keeps the prop on the interface so Plan 06-09 / future consumers can rely on the type signature.
- **Files modified:** `frontend/src/components/FileExplorerPanel.tsx`
- **Commit:** `4050349`

## Issues Encountered

None.

## Known Stubs

None. Every component fetches real data via Plan 06-06 primitives or receives real props from `Chat.tsx`. The `_metadataSchema` prop is intentionally unrendered until Plan 06-09 (the plan EXPLICITLY documents "metadataSchema is accepted but not yet wired into the panel"). The `onRename` prop is forwarded to `RootSection` for Plan 06-09 to consume — same forward-only pattern as the Wave-1 plan documented.

## Threat Flags

None — no new network endpoints, no auth-path changes, no schema changes. The polling effect hits the same `documents` table the legacy `FileUploadPanel` polled (RLS-gated server-side from Phase 1). The UI-11 non-admin scope-fallback in `handleFileChange` is a defense-in-depth UX guard, not a security boundary — server enforces admin-only writes to global scope at the router level (Phase 3 / Plan 03-05).

## Self-Check: PASSED

Files (verified via `test -f` / `! test -f`):
- ✅ `frontend/src/components/explorer/Breadcrumbs.tsx` — FOUND
- ✅ `frontend/src/components/FileExplorerPanel.tsx` — FOUND
- ✅ `frontend/src/pages/Chat.tsx` — FOUND
- ✅ `frontend/src/components/FileUploadPanel.tsx` — CONFIRMED DELETED

Commits (verified via `git log --oneline -4`):
- ✅ `feba471 feat(06-08): add Breadcrumbs component for explorer header (UI-08)`
- ✅ `4050349 feat(06-08): add FileExplorerPanel — two RootSections + breadcrumbs + 4-arg polling`
- ✅ `dab3064 feat(06-08): swap Chat.tsx mount to FileExplorerPanel + delete FileUploadPanel (UI-01)`

Plan-level verification:
- ✅ `tsc --noEmit` exits 0
- ✅ UI-02 invariant: `<RootSection` count = 2, `<Tabs` count = 0
- ✅ UI-01 verifier-gate: `FileUploadPanel.tsx` deleted in same commit as mount swap; zero orphan refs in `frontend/src/`
- ✅ Checker WARNING #3 grep gate: `handleStatusUpdate(..., contentMarkdownStatus?: string)` present in Chat.tsx
- ✅ Checker WARNING #3 polling gate: 4-arg `onStatusUpdate(doc.id, doc.status, doc.error_message, doc.content_markdown_status)` present in FileExplorerPanel.tsx
- ✅ Breadcrumbs `split('/')` + `ChevronRight` present
- ✅ `selectedFolder` state + `isAdmin` UI-11 fallback present in FileExplorerPanel
- ✅ `content_markdown_status` watched in FileExplorerPanel polling effect

## Next Plan Readiness

- Plan 06-09 (folder CRUD UI / ContextMenu) — can hook into the panel's `onPanelClick` selection seam via `selectedFolder`; can render inline rename inside `DocumentRow` (prop already forwarded); can use `Breadcrumbs.onNavigate` to drive selection from the header.
- Plan 06-10 (@dnd-kit) — the panel body is plain `<div data-testid="file-explorer-body">`; DnD wrappers around `DocumentRow` + `FolderNode` (Plan 06-06 primitives) read `data-scope` for cross-scope move confirmation.
- Plan 06-11 (Playwright) — selectors `[data-testid="file-explorer-body"]`, `[data-root-scope="global"]`, `[data-root-scope="user"]`, `nav[aria-label="Breadcrumb"]` all stable.

---
*Phase: 06-file-explorer-ui-cluster*
*Plan: 08*
*Completed: 2026-05-11*
