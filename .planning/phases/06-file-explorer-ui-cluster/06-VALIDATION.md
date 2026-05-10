---
phase: 6
slug: file-explorer-ui-cluster
status: ready
nyquist_compliant: true
wave_0_complete: true
created: 2026-05-10
updated: 2026-05-10
---

# Phase 6 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | Playwright 1.x (frontend e2e) + pytest (backend) |
| **Config file** | `frontend/playwright.config.ts`, `backend/scripts/test_all.py` |
| **Quick run command** | `cd frontend && npx playwright test e2e/full-suite.spec.ts --grep '@phase6'` |
| **Full suite command** | `cd frontend && npx playwright test e2e/full-suite.spec.ts && cd ../backend && venv/Scripts/python scripts/test_all.py` |
| **Estimated runtime** | ~90s frontend (full), ~60s backend (full) |

---

## Sampling Rate

- **After every task commit:** Run quick command (Phase-6-tagged Playwright tests)
- **After every plan wave:** Run full suite command
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 90 seconds

---

## Per-Task Verification Map

> Populated 2026-05-10 (revision iter 1). Every task has a documented `<automated>` command from its plan's `<verify>` block. `nyquist_compliant: true` because every task carries an `<automated>` gate. `wave_0_complete: true` because Wave 0 (plans 06-01 through 06-04 + 06-12) is now defined.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 06-01-01 | 06-01 | 0 | UI-08 | T-DocSchema | Pydantic exposes content_markdown_status on the wire | python | `cd backend && venv/Scripts/python -c "from app.models.schemas import DocumentResponse; assert 'content_markdown_status' in DocumentResponse.model_fields"` | ❌ W0 | ⬜ pending |
| 06-02-01 | 06-02 | 0 | UI-11, TEST-05 | T-AdminSeed | Admin user provisioned via service-role API | python | `cd backend && venv/Scripts/python -c "import importlib.util, pathlib; spec = importlib.util.spec_from_file_location('seed', pathlib.Path('scripts/seed_admin_user.py')); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)"` | ❌ W0 | ⬜ pending |
| 06-02-02 | 06-02 | 0 | UI-11, TEST-05 | T-AdminPromote | Migration 021 promotes profile is_admin idempotently | shell | `test -f backend/migrations/021_admin_test_user.sql && grep -q "is_admin" backend/migrations/021_admin_test_user.sql && grep -q "admin@test.com" backend/migrations/021_admin_test_user.sql` | ❌ W0 | ⬜ pending |
| 06-02-03 | 06-02 | 0 | UI-11, TEST-05 | T-AdminCheckpoint | Operator runs seed + migration | checkpoint | (operator) `cd backend && venv/Scripts/python scripts/seed_admin_user.py && venv/Scripts/python scripts/run_migrations.py` | ❌ W0 | ⬜ pending |
| 06-03-01 | 06-03 | 0 | UI-04, UI-06, UI-08 | T-DepInstall | dnd-kit pinned versions installed | node | `cd frontend && node -e "const p=require('./package.json'); if(!p.dependencies['@dnd-kit/core']) throw new Error('missing @dnd-kit/core'); if(!p.dependencies['@dnd-kit/sortable']) throw new Error('missing @dnd-kit/sortable')"` | ❌ W0 | ⬜ pending |
| 06-03-02 | 06-03 | 0 | UI-04, UI-06, UI-08 | T-ShadcnInstall | 6 shadcn primitives installed via CLI | shell | `cd frontend && for f in context-menu dialog alert-dialog badge tooltip separator; do test -f "src/components/ui/$f.tsx" \|\| exit 1; done && cd frontend && npx tsc --noEmit` | ❌ W0 | ⬜ pending |
| 06-04-01 | 06-04 | 0 | UI-10 | T-SSEDualEmit | Backend SSE emits ONLY generalized envelope | python+grep | `cd backend && venv/Scripts/python -c "import ast; ast.parse(open('app/routers/messages.py').read())" && grep -c '"type": "sub_agent_' backend/app/routers/messages.py` returns 0 | ❌ W0 | ⬜ pending |
| 06-04-02 | 06-04 | 0 | UI-10 | T-SSEFrontend | Frontend consumes ONLY generalized envelope | grep+tsc | `cd frontend && npx tsc --noEmit && grep -c "parsed.type === 'sub_agent_" frontend/src/lib/api.ts` returns 0 AND `grep -c "parsed.type === 'sub_agent'"` returns 1 | ❌ W0 | ⬜ pending |
| 06-12-01 | 06-12 | 0 | UI-04, UI-07, FOLDER-04, FOLDER-06 | T-FolderID | list_folder includes UUIDs in subfolder items | python | `cd backend && venv/Scripts/python -c "import ast; ast.parse(open('app/services/folder_service.py').read())"` AND `grep -q '"id, path"' backend/app/services/folder_service.py` | ❌ W0 | ⬜ pending |
| 06-12-02 | 06-12 | 0 | UI-04, UI-07, FOLDER-04, FOLDER-06 | T-FolderRefSchema | FolderRef + FolderListResponse Pydantic models exist; router uses response_model | python | `cd backend && venv/Scripts/python -c "from app.models.schemas import FolderRef, FolderListResponse; assert 'id' in FolderRef.model_fields and 'path' in FolderRef.model_fields"` AND `grep -q "response_model=FolderListResponse" backend/app/routers/folders.py` | ❌ W0 | ⬜ pending |
| 06-12-03 | 06-12 | 0 | UI-04, UI-07, FOLDER-04, FOLDER-06 | T-FolderIDTest | pytest verifies subfolders[].id is a UUID | python | `cd backend && venv/Scripts/python -c "import ast; ast.parse(open('scripts/test_folders_subfolder_id.py').read())" && grep -q "subfolders" backend/scripts/test_folders_subfolder_id.py` | ❌ W0 | ⬜ pending |
| 06-05-01 | 06-05 | 1 | UI-04, UI-05, UI-06, UI-07, UI-08 | T-APIType | api.ts types declare folder_path/scope/content_markdown_status + FolderRef + ListFolderResponse with D-06 subfolders shape | tsc+grep | `cd frontend && npx tsc --noEmit && grep -q "content_markdown_status" frontend/src/lib/api.ts && grep -q "FolderRef" frontend/src/lib/api.ts && grep -E "subfolders:\\s*Array<\\{id:\\s*string;\\s*path:\\s*string\\}>" frontend/src/lib/api.ts` | ❌ W0 | ⬜ pending |
| 06-05-02 | 06-05 | 1 | UI-04, UI-05, UI-06, UI-07, UI-08 | T-APIClient | 6 folder/doc CRUD methods exported; deleteFolder branches on 409 (Pitfall 5) | tsc+grep | `cd frontend && npx tsc --noEmit && grep -q "export async function listFolder\|createFolder\|renameFolder\|deleteFolder\|moveDocument\|renameDocument" frontend/src/lib/api.ts && grep -q "res.status === 409" frontend/src/lib/api.ts` | ❌ W0 | ⬜ pending |
| 06-06-01 | 06-06 | 1 | UI-02, UI-03, UI-08, UI-09 | T-Hook+Badges | useOpenFoldersStorage hook + ScopeBadge + StatusBadge with D-03 enum support | tsc+grep | `cd frontend && npx tsc --noEmit && test -f src/hooks/useOpenFoldersStorage.ts && grep -q "fileExplorer:open:" src/hooks/useOpenFoldersStorage.ts && grep -q "requires_user_reupload" src/components/explorer/StatusBadge.tsx` | ❌ W0 | ⬜ pending |
| 06-06-02 | 06-06 | 1 | UI-02, UI-03, UI-08, UI-09 | T-TreePrim | Recursive FolderNode/FolderTree/RootSection/DocumentRow with D-04 keyboard set + D-06 folderId thread | tsc+grep | `cd frontend && npx tsc --noEmit && grep -q "ArrowRight" src/components/explorer/FolderTree.tsx && grep -q "ArrowLeft" src/components/explorer/FolderTree.tsx && grep -E "folderId:\\s*string" src/components/explorer/FolderNode.tsx` | ❌ W0 | ⬜ pending |
| 06-07-01 | 06-07 | 1 | UI-10 | T-ToolCallRow | ToolCallRow extracted as named export with Explorer icon map | tsc+grep | `cd frontend && npx tsc --noEmit && grep -qE "export (function\|const) ToolCallRow" frontend/src/components/ToolActivity.tsx && grep -q "EXPLORER_TOOL_ICON\|TOOL_ICON" frontend/src/components/ToolActivity.tsx` | ❌ W0 | ⬜ pending |
| 06-07-02 | 06-07 | 1 | UI-10 | T-Pitfall12 | SubAgentSection rewritten without agent-type fork | tsc+grep | `cd frontend && npx tsc --noEmit && grep -q "tool_calls" frontend/src/components/MessageList.tsx && grep -q "ToolCallRow" frontend/src/components/MessageList.tsx && ! grep -E "if\\s*\\(\\s*tool\\.(tool\|name\|agent\|type)\\s*===?\\s*['\"](explore_knowledge_base\|analyze_document)['\"]" frontend/src/components/MessageList.tsx` | ❌ W0 | ⬜ pending |
| 06-07-03 | 06-07 | 1 | UI-10 | T-LiveTrace | Chat.tsx liveSubAgentTrace migration | tsc+grep | `cd frontend && npx tsc --noEmit && grep -q "liveSubAgentTrace" frontend/src/pages/Chat.tsx && ! grep -q "isSubAgent" frontend/src/pages/Chat.tsx` | ❌ W0 | ⬜ pending |
| 06-08-01 | 06-08 | 2 | UI-01, UI-02, UI-03, UI-05, UI-08 | T-Breadcrumbs | Breadcrumbs splits path on '/' | tsc+grep | `cd frontend && npx tsc --noEmit && test -f src/components/explorer/Breadcrumbs.tsx && grep -q "split('/')" src/components/explorer/Breadcrumbs.tsx` | ❌ W0 | ⬜ pending |
| 06-08-02 | 06-08 | 2 | UI-01, UI-02, UI-03, UI-05, UI-08 | T-PanelComp | FileExplorerPanel composes 2 RootSections (no Tabs); 4-arg polling callback | tsc+grep | `cd frontend && npx tsc --noEmit && grep -c '<RootSection' src/components/FileExplorerPanel.tsx >= 2 AND ! grep -q '<Tabs' src/components/FileExplorerPanel.tsx AND grep -q "onStatusUpdate(doc.id, doc.status, doc.error_message, doc.content_markdown_status)" src/components/FileExplorerPanel.tsx` | ❌ W0 | ⬜ pending |
| 06-08-03 | 06-08 | 2 | UI-01, UI-02, UI-03, UI-05, UI-08 | T-MountSwap | Chat.tsx mounts FileExplorerPanel; FileUploadPanel.tsx deleted; handleStatusUpdate signature extended | tsc+grep | `cd frontend && npx tsc --noEmit && ! test -f frontend/src/components/FileUploadPanel.tsx && grep -E "handleStatusUpdate.*contentMarkdownStatus" frontend/src/pages/Chat.tsx` | ❌ W0 | ⬜ pending |
| 06-09-01 | 06-09 | 2 | UI-04, UI-07, UI-11 | T-Dialogs | CreateFolderDialog + DeleteFolderDialog (Pitfall 5) | tsc+grep | `cd frontend && npx tsc --noEmit && grep -q "document_count" src/components/explorer/DeleteFolderDialog.tsx && grep -q "subfolder_count" src/components/explorer/DeleteFolderDialog.tsx && grep -q "FOLDER_NOT_EMPTY" src/components/explorer/DeleteFolderDialog.tsx` | ❌ W0 | ⬜ pending |
| 06-09-02 | 06-09 | 2 | UI-04, UI-07, UI-11 | T-CMActions | ContextMenuActions with isAdmin + hasFolderId guards | tsc+grep | `cd frontend && npx tsc --noEmit && grep -q "useAuth" src/components/explorer/ContextMenuActions.tsx && grep -q "isAdmin" src/components/explorer/ContextMenuActions.tsx && grep -q "hasFolderId" src/components/explorer/ContextMenuActions.tsx` | ❌ W0 | ⬜ pending |
| 06-09-03a | 06-09 | 2 | UI-04, UI-07, UI-11 | T-FolderNodeCRUD | FolderNode CRUD wired (D-05 inline + D-06 folderId direct call) | tsc+grep | `cd frontend && npx tsc --noEmit && grep -q "renameFolder(folderId" frontend/src/components/explorer/FolderNode.tsx && grep -q "DeleteFolderDialog" frontend/src/components/explorer/FolderNode.tsx && grep -q "Plus" frontend/src/components/explorer/FolderNode.tsx && grep -q "MoreVertical" frontend/src/components/explorer/FolderNode.tsx && grep -q "group-hover:opacity-100" frontend/src/components/explorer/FolderNode.tsx` | ❌ W0 | ⬜ pending |
| 06-09-03b | 06-09 | 2 | UI-04, UI-07, UI-11 | T-DocRowCRUD | DocumentRow context menu + inline rename | tsc+grep | `cd frontend && npx tsc --noEmit && grep -q "ContextMenu" frontend/src/components/explorer/DocumentRow.tsx && grep -q "renameDocument" frontend/src/components/explorer/DocumentRow.tsx && grep -q "renameMode" frontend/src/components/explorer/DocumentRow.tsx` | ❌ W0 | ⬜ pending |
| 06-09-03c | 06-09 | 2 | UI-04, UI-07, UI-11 | T-RootSecCreate | RootSection inline-create button (admin-gated) | tsc+grep | `cd frontend && npx tsc --noEmit && grep -q "isAdmin" frontend/src/components/explorer/RootSection.tsx && grep -q "CreateFolderDialog" frontend/src/components/explorer/RootSection.tsx && grep -q "scope === 'user' \|\| isAdmin" frontend/src/components/explorer/RootSection.tsx` | ❌ W0 | ⬜ pending |
| 06-09-03d | 06-09 | 2 | UI-04, UI-07, UI-11 | T-RefetchWiring | FolderTree refetch counter + FileExplorerPanel callback wiring | tsc+grep | `cd frontend && npx tsc --noEmit && grep -q "refetchCounter\|onAfterMutation" frontend/src/components/explorer/FolderTree.tsx && grep -q "onAfterMutation" frontend/src/components/explorer/FolderNode.tsx && grep -q "key={refetchCounter}" frontend/src/components/explorer/FolderTree.tsx` | ❌ W0 | ⬜ pending |
| 06-10-01 | 06-10 | 2 | UI-06 | T-CrossScopeBlock | CrossScopeMoveDialog with D-01 LOCKED copy; zero backend imports | tsc+grep | `cd frontend && npx tsc --noEmit && grep -q "Scope is permanent" src/components/explorer/CrossScopeMoveDialog.tsx && grep -q "admin must re-upload" src/components/explorer/CrossScopeMoveDialog.tsx && ! grep -q "from '@/lib/api'" src/components/explorer/CrossScopeMoveDialog.tsx && ! grep -q "moveDocument" src/components/explorer/CrossScopeMoveDialog.tsx` | ❌ W0 | ⬜ pending |
| 06-10-02 | 06-10 | 2 | UI-06 | T-DnDHooks | useDraggable on DocumentRow + useDroppable on FolderNode + ring highlight | tsc+grep | `cd frontend && npx tsc --noEmit && grep -q "useDraggable" frontend/src/components/explorer/DocumentRow.tsx && grep -q "useDroppable" frontend/src/components/explorer/FolderNode.tsx && grep -qE "ring-(blue\|primary)" frontend/src/components/explorer/FolderNode.tsx` | ❌ W0 | ⬜ pending |
| 06-10-03 | 06-10 | 2 | UI-06 | T-DnDDispatch | DndContext + onDragEnd dispatcher; same-scope calls moveDocument; cross-scope opens dialog | tsc+grep | `cd frontend && npx tsc --noEmit && grep -q "DndContext" frontend/src/components/FileExplorerPanel.tsx && grep -q "moveDocument" frontend/src/components/FileExplorerPanel.tsx && grep -q "CrossScopeMoveDialog" frontend/src/components/FileExplorerPanel.tsx && grep -q "PointerSensor" frontend/src/components/FileExplorerPanel.tsx` | ❌ W0 | ⬜ pending |
| 06-11-00 | 06-11 | 3 | TEST-05 | T-FixtureHelpers | apiPost/apiDelete fixture helpers (or UI-flow alternative) | grep | `cd frontend && grep -qE "apiPost\|apiDelete" frontend/e2e/full-suite.spec.ts && grep -q "getStoredToken\|sb-.*-auth-token" frontend/e2e/full-suite.spec.ts` (or SUMMARY documents UI alternative) | ❌ W0 | ⬜ pending |
| 06-11-01 | 06-11 | 3 | TEST-05 | T-AdminHelper | Admin signin helper + Pitfall 12 grep test | grep | `cd frontend && grep -q "TEST_ADMIN_EMAIL\|signInAdmin" frontend/e2e/full-suite.spec.ts && grep -q "@phase6" frontend/e2e/full-suite.spec.ts && grep -q "Pitfall 12" frontend/e2e/full-suite.spec.ts` | ❌ W0 | ⬜ pending |
| 06-11-02 | 06-11 | 3 | UI-01..UI-04, UI-07, UI-08, UI-10, UI-11, TEST-05 | T-PhaseE2E | 8+ @phase6 tests cover panel mount, sections, context menu, delete-non-empty, rename, breadcrumbs, keyboard, admin | playwright | `cd frontend && grep -c "@phase6" frontend/e2e/full-suite.spec.ts >= 8 AND grep -q "FileExplorer renders\|two scope sections\|delete non-empty\|rename document\|admin\|keyboard" frontend/e2e/full-suite.spec.ts` | ❌ W0 | ⬜ pending |
| 06-11-03 | 06-11 | 3 | UI-05, UI-06, TEST-05 | T-DragE2E | Drag tests use pointer events; D-01 locked-copy asserted | playwright | `cd frontend && grep -q "Scope is permanent" frontend/e2e/full-suite.spec.ts && grep -q "page.mouse.down\|mouse.down" frontend/e2e/full-suite.spec.ts && ! grep -q "page.dragTo" frontend/e2e/full-suite.spec.ts` | ❌ W0 | ⬜ pending |
| 06-11-04 | 06-11 | 3 | TEST-05 | T-OperatorRun | Operator runs full @phase6 suite | checkpoint | (operator) `cd frontend && npx playwright test e2e/full-suite.spec.ts --grep '@phase6'` | ❌ W0 | ⬜ pending |

---

## Wave 0 Requirements

- [x] `frontend/e2e/full-suite.spec.ts` — extend with `@phase6` tagged tests for: folder tree nav, drag-move (same-scope), cross-scope drag block modal, sub-agent activity card, scope visibility (admin vs regular), keyboard navigation (Plan 06-11)
- [x] Admin test account seed — `backend/scripts/seed_admin_user.py` + `backend/migrations/021_admin_test_user.sql` create `admin@test.com` with `is_admin=true` (Plan 06-02)
- [x] `frontend/playwright.config.ts` or test fixture — expose `TEST_ADMIN_EMAIL` / `TEST_USER_ADMIN_PASSWORD` (matching existing `TEST_EMAIL` / `TEST_PASSWORD` convention; Plan 06-11)
- [x] `@dnd-kit/core@6.3.1` + `@dnd-kit/sortable@10.0.0` + `@radix-ui/react-context-menu` installed in `frontend/package.json` (Plan 06-03)
- [x] D-06 backend folder-id resolution: `GET /api/folders` returns subfolders as `Array<{id, path}>` (Plan 06-12)
- [x] D-03 backend content_markdown_status exposed in DocumentResponse (Plan 06-01)
- [x] SSE legacy-emit cleanup: backend + frontend speak only the generalized envelope (Plan 06-04)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Visual polish of drop indicator (line vs highlight) | UI-04 | Subjective visual fidelity hard to assert programmatically | Drag a doc between sibling rows — confirm 2px primary-color horizontal line; drag onto a folder — confirm folder highlight ring |
| localStorage persistence across browser restarts | UI-02 | Playwright clears storage between tests | Open folders, close browser, reopen, confirm same folders open |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references (admin account, dnd-kit install, D-06 folder-id resolution, D-03 content_markdown_status, SSE cleanup)
- [x] No watch-mode flags
- [x] Feedback latency < 90s
- [x] `nyquist_compliant: true` set in frontmatter (set by planner after task map fills)

**Approval:** approved (revision iter 1, 2026-05-10)
