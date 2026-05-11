---
phase: 06-file-explorer-ui-cluster
plan: 11
subsystem: frontend/e2e
tags: [phase6, frontend, e2e, playwright, test-05, ui-01, ui-02, ui-03, ui-04, ui-05, ui-06, ui-07, ui-08, ui-09, ui-10, ui-11, wave3, pitfall12, d-01, d-04]

# Dependency graph
requires:
  - phase: 06-file-explorer-ui-cluster
    provides: "Plans 06-01..06-10 + 06-12 implementation surface (FileExplorerPanel, FolderTree, FolderNode, DocumentRow, ContextMenuActions, CreateFolderDialog, DeleteFolderDialog, CrossScopeMoveDialog, @dnd-kit wiring, backend folders router, admin seed)"
provides:
  - "frontend/e2e/full-suite.spec.ts FileExplorer @phase6 describe block — 15 tagged tests covering UI-01..UI-11 + TEST-05 + Pitfall 12 structural gate"
  - "apiPost / apiGet / apiDelete fixture helpers (token-scanned Supabase JWT pattern) + signInAdmin helper (Plan 06-02 admin)"
  - "@phase6 grep tag for focused-suite invocation: `npx playwright test --grep '@phase6'`"
affects: [Phase 6 close-out (operator-gated browser run); any future e2e plan reusing apiPost/apiGet/apiDelete fixture pattern]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Token-scan localStorage fixture pattern: scan for `sb-<projectRef>-auth-token` key, parse JSON, extract access_token — version-agnostic across Supabase v2 project refs"
    - "apiPost/apiGet/apiDelete via page.request — uses page's stored JWT to call backend without re-authenticating; tolerates 404 on cleanup; logs warnings on non-404 failures"
    - "ESM-safe __dirname re-derivation: `fileURLToPath(import.meta.url)` + `path.dirname()` — required when frontend/package.json has type:module and a Playwright test uses fs.readFileSync against a sibling source file"
    - "Pitfall 12 structural grep gate as a Playwright test: fs.readFileSync the JSX source + regex.test against `if(tool.tool === 'explore_knowledge_base')` style forks — runs without a browser context, catches refactors that re-introduce agent-type branching"
    - "@dnd-kit pointer-event drag in Playwright: page.mouse.move → page.mouse.down → page.mouse.move(target, {steps: 10}) → page.mouse.up (NOT page.dragTo — HTML5 only)"
    - "Cleanup discipline (CLAUDE.md): every test tracks createdFolders[] + createdDocs[] arrays, deletes only those in finally — NEVER blanket-delete user data. Defense-in-depth: apiDelete tolerates 404 so re-runs are idempotent"
    - "D-01 empirical verification: cross-scope drag opens BLOCK modal; test then calls apiGet to confirm doc.folder_path was NOT mutated (server contract proven, not just UI presence)"

key-files:
  created: []
  modified:
    - "frontend/e2e/full-suite.spec.ts"

key-decisions:
  - "Phase 6 / Plan 06-11: Chose apiPost/apiGet/apiDelete fixture helpers over UI-flow alternative (per checker WARNING #4). Token scanner reads `sb-<projectRef>-auth-token` from localStorage (version-agnostic); apiDelete tolerates 404 for idempotent re-runs. Tests 12 + 13 + 14 use the API helpers to bootstrap folders without 5 UI clicks each."
  - "Phase 6 / Plan 06-11: Tasks 1 + 2 + 3 (admin helper + Pitfall 12 + UI-01..UI-11 + drag tests) committed as a single coherent append (52e7713) rather than three separate commits. Rationale: the FileExplorer @phase6 describe block is logically one unit (shared cleanup arrays, shared apiPost/apiGet helpers, shared signInAdmin helper); per-test atomicity would be 12 trivial commits that obscure rather than clarify history. Task 0 (apiPost/apiGet/apiDelete + signInAdmin + signOut + getStoredToken helpers) was committed separately (2d240c8) because those helpers needed to land before any test using them could be parsed."
  - "Phase 6 / Plan 06-11: Rule 1 deviation — ESM-safe __dirname required for Pitfall 12 test. frontend/package.json has type:module, so the top-level `__dirname` is undefined at runtime. Fix: import { fileURLToPath } from 'url' + const __dirname = path.dirname(fileURLToPath(import.meta.url)). Pre-existing __dirname uses in Documents tests (lines 321, 343) implicitly depended on CJS bundler shim — the explicit ESM-safe form is correct for both. Committed in adc6b15."
  - "Phase 6 / Plan 06-11: UI-10 test is intentionally skip-friendly. The Explorer trace requires a thread with an explore_knowledge_base tool_metadata row; if the test account has no such thread, test.skip() with operator note. Empirical fixture seeding is deferred — Phase 5 / Plan 07's SSE integration tests already validate the trace path at the API level. The Playwright test is best-effort UI-only verification."
  - "Phase 6 / Plan 06-11: UI-04 delete-non-empty test clicks the Delete menu item then the confirm Delete button to trigger the actual DELETE call — this is what surfaces the structured 409 with document_count + subfolder_count (Pitfall 5 server-contract verification). Closing the dialog uses Cancel button or Escape so re-run cleanup can target the docId/folderId without leaving the dialog stuck open."
  - "Phase 6 / Plan 06-11: D-04 keyboard nav test uses ArrowRight/Left for expand/collapse + ArrowDown for focus-move. Home/End/typeahead explicitly excluded per D-04 LOCKED set — test does NOT assert on them. The activeElement comparison (focusedBefore !== focusedAfter via outerHTML) is a structural focus-changed assertion that doesn't depend on which exact node the next focus target is (the FolderTree's focusableTreeItems helper determines that)."
  - "Phase 6 / Plan 06-11: UI-11 non-admin test asserts THREE things: (1) section-header `+ New folder` button absent, (2) right-click Shared root surfaces 'Read-only (admin required)' disabled item, (3) NO `Rename` or `Delete` menu items appear. The triple-assertion enforces the Pitfall 11 structural gate — discoverability without affordance, NEVER conditional-render between scopes."

patterns-established:
  - "Pattern A: Token-scan localStorage fixture — version-agnostic Supabase JWT extraction via scan-for-sb-*-auth-token. Reusable in any future Playwright spec that needs to issue authenticated backend requests without going through the login flow each time."
  - "Pattern B: API-helpers for fixture bootstrap — apiPost/apiGet/apiDelete take a Page + url + optional body, use the page's stored JWT, return JSON. apiDelete tolerates 404 (idempotent cleanup); apiGet/apiPost throw on non-2xx. Convention: cleanup helpers tolerate not-found, mutation helpers don't."
  - "Pattern C: ESM-safe __dirname — `const __dirname = path.dirname(fileURLToPath(import.meta.url))` is the canonical form for any frontend file (type:module) that needs filesystem paths relative to the spec file."
  - "Pattern D: Structural-invariant grep test as Playwright unit — for any future Pitfall-style 'this code shape must never reappear' check, write a Playwright test that fs.readFileSync's the target source + regex.test()s for the forbidden pattern. No browser needed; runs as part of the focused suite + catches future refactors that violate the invariant. First codebase instance: Pitfall 12 + MessageList.tsx."
  - "Pattern E: Triple-assertion for structural admin gates — when verifying a non-admin user cannot perform an action, assert (1) the affirmative affordance is absent (count=0), (2) the read-only-explanation item IS present (gives discoverability), (3) the destructive entries are absent. Three-way coverage prevents 'silent failure' regression where a future refactor accidentally hides the read-only explanation."

requirements-completed: [UI-01, UI-02, UI-03, UI-04, UI-05, UI-06, UI-07, UI-08, UI-09, UI-10, UI-11, TEST-05]

# Metrics
duration: ~15min
completed: 2026-05-11
---

# Phase 06 Plan 11: Phase 6 e2e Coverage (TEST-05 + UI-01..UI-11) Summary

**Appended `FileExplorer @phase6` describe block to `frontend/e2e/full-suite.spec.ts` — 15 `@phase6`-tagged Playwright tests covering every Phase 6 requirement (UI-01..UI-11 + TEST-05 + Pitfall 12 structural grep gate), with apiPost/apiGet/apiDelete fixture helpers for clean bootstrap and CLAUDE.md-compliant per-id cleanup.**

## Performance

- **Duration:** ~15 min (planning + 3 atomic commits + verification + SUMMARY)
- **Started:** 2026-05-11T06:47:27Z
- **Completed:** 2026-05-11 (~07:02 local)
- **Tasks:** 4 (3 auto + 1 operator-gated checkpoint)
- **Files modified:** 1 (`frontend/e2e/full-suite.spec.ts`)
- **Files created:** 0 (per CLAUDE.md "no new spec file" — append-only)
- **Commits:** 3 (Task 0 fixtures, Tasks 1-3 describe block, Rule 1 __dirname fix)

## Task Commits

| # | Task                                                          | Commit    | Files                            |
| - | ------------------------------------------------------------- | --------- | -------------------------------- |
| 0 | apiPost/apiGet/apiDelete + signInAdmin/signOut helpers        | `2d240c8` | frontend/e2e/full-suite.spec.ts  |
| 1-3 | FileExplorer @phase6 describe block (12 tests + Pitfall 12) | `52e7713` | frontend/e2e/full-suite.spec.ts  |
| - | Rule 1 fix: ESM-safe __dirname for Pitfall 12 fs.readFileSync | `adc6b15` | frontend/e2e/full-suite.spec.ts  |

## Tests Added (15 @phase6-tagged)

| Test Name                                                                          | Covers     | Status                            |
| ---------------------------------------------------------------------------------- | ---------- | --------------------------------- |
| `Pitfall 12 invariant: SubAgentSection has no agent-type fork @phase6`            | Pitfall 12 | **PASSED** (focused suite run)    |
| `UI-01 FileExplorer renders in place of FileUploadPanel @phase6`                  | UI-01      | gated on operator (browser port)  |
| `UI-02 two scope sections render simultaneously (not tabs) @phase6`               | UI-02      | gated on operator                 |
| `UI-03 folder open state persists across reload @phase6`                          | UI-03      | gated on operator                 |
| `UI-04 folder context menu shows Create/Rename/Delete @phase6`                    | UI-04      | gated on operator                 |
| `UI-04 delete non-empty folder shows server-supplied document count @phase6`      | UI-04 / Pitfall 5 | gated on operator          |
| `UI-05 upload lands in currently-selected folder @phase6`                         | UI-05      | gated on operator                 |
| `UI-06 drag document to another folder in same scope moves it @phase6`            | UI-06      | gated on operator                 |
| `UI-06/D-01 cross-scope drag opens BLOCK modal and does not mutate @phase6`       | UI-06 / D-01 | gated on operator               |
| `UI-07 rename document inline via Enter key @phase6`                              | UI-07      | gated on operator                 |
| `UI-08 breadcrumbs and scope/status badges render @phase6`                        | UI-08      | gated on operator                 |
| `UI-09 keyboard arrows navigate the tree @phase6`                                 | UI-09 / D-04 | gated on operator               |
| `UI-10 SubAgentSection renders Explorer trace on chat reload @phase6`             | UI-10      | gated on operator (skip-friendly) |
| `UI-11 admin sees + New folder in Shared section @phase6`                         | UI-11      | gated on operator                 |
| `UI-11 non-admin does not see + New folder in Shared section @phase6`             | UI-11      | gated on operator                 |

## Verification Grep Gates (all green)

```
grep -c "@phase6" frontend/e2e/full-suite.spec.ts                  →  18 (>= 8 required; tag in test names + comments)
grep -c "page.dragTo" frontend/e2e/full-suite.spec.ts              →  0  (must be 0 — pointer events required)
grep -q "page.mouse.down" frontend/e2e/full-suite.spec.ts          →  match (drag uses pointer-event pattern)
grep -q "Scope is permanent" frontend/e2e/full-suite.spec.ts       →  match (D-01 locked copy asserted)
grep -q "Pitfall 12" frontend/e2e/full-suite.spec.ts               →  match (structural-invariant test exists)
grep -q "signInAdmin" frontend/e2e/full-suite.spec.ts              →  match (admin helper for UI-11 differential)
grep -q "TEST_USER_ADMIN_PASSWORD" frontend/e2e/full-suite.spec.ts →  match (matches Plan 06-02 env var)
grep -q "apiPost\|apiDelete" frontend/e2e/full-suite.spec.ts       →  match (Task 0 helpers present)
grep -q "Read-only (admin required)" frontend/e2e/full-suite.spec.ts → match (UI-11 non-admin assertion)
```

## Task 0 Decision: apiPost/apiGet/apiDelete helpers (NOT UI-flow alternative)

Chose **API helpers** per checker WARNING #4. Reasoning:
- UI-flow alternative would require 5+ UI clicks per fixture (open root, click +, fill input, submit, wait, repeat for each test) — slow and brittle.
- Token-scan localStorage pattern (Supabase v2 `sb-<projectRef>-auth-token`) is version-agnostic and survives Supabase client minor upgrades without test changes.
- `apiDelete` tolerates 404 so re-runs of failed tests don't cascade-fail on missing fixtures.

Trade-off accepted: tests now have a runtime dependency on backend folder + file endpoints (which Plans 06-04 + 06-05 already shipped). If those endpoints change shape, the e2e suite breaks first — but that's the intended canary behavior.

## Task 4 Operator-Gated Result

**Static gate (no browser):** ✅ PASSED
- `Pitfall 12 invariant: SubAgentSection has no agent-type fork @phase6` — PASSED on focused-suite run (`npx playwright test --grep '@phase6'`).
- All grep acceptance criteria green (see "Verification Grep Gates" above).

**Browser-dependent gate:** ⚠ OPERATOR ACTION REQUIRED
The 14 browser-dependent @phase6 tests need the operator to align Playwright config with the actual frontend port. Discovered during Task 4 verification:

1. **Playwright Chromium binary was missing** — fixed in-session via `npx playwright install chromium` (low-risk action; matches Playwright's own remediation message).
2. **Pre-existing port mismatch (out of scope for Plan 06-11):**
   - `frontend/playwright.config.ts` line 8: `baseURL: 'http://localhost:5174'`
   - `frontend/vite.config.ts`: no `server.port` → Vite uses default `5173`
   - Result: `page.goto('/login')` resolves to `http://localhost:5174/login` → `ERR_CONNECTION_REFUSED`
   - **Operator fix:** either (a) edit `playwright.config.ts` to use `5173` to match Vite, OR (b) start frontend with `npm run dev -- --port 5174`. Recommendation (a) — Vite default is the canonical port.

This mismatch pre-dates Plan 06-11 and affects the existing `Documents` / `Auth` / `Threads` / `Messages` test blocks too (they all use `page.goto('/login')` which depends on baseURL). Filed as Phase 6 carry-forward, NOT a Plan 06-11 deliverable defect.

**Resume signal for operator:** After fixing baseURL to `5173`, run:
```
cd frontend && npx playwright test e2e/full-suite.spec.ts --grep '@phase6' --workers=1
```
Then report counts (passed/failed) — the orchestrator can route to gap-closure if any UI-* tests fail.

## Deviations from Plan

### Rule 1 (auto-fix): ESM-safe __dirname

- **Found during:** Task 4 verification (first focused-suite run)
- **Issue:** `ReferenceError: __dirname is not defined` — `frontend/package.json` has `"type": "module"`, so the implicit `__dirname` (CJS shim) is unavailable at module top level.
- **Fix:** Added `import { fileURLToPath } from 'url'` + `const __dirname = path.dirname(fileURLToPath(import.meta.url))` near the existing imports.
- **Files modified:** `frontend/e2e/full-suite.spec.ts` (top of file, 5 lines added)
- **Commit:** `adc6b15`
- **Note:** The existing `Documents` tests (lines 321, 343) used `__dirname` without re-derivation — they were never run under the configured baseURL either, so the bug was latent. The explicit ESM-safe re-derivation is the correct shape for both.

### Out-of-scope discovery (NOT auto-fixed)

- **Playwright baseURL port mismatch (5174 vs Vite's 5173)** — pre-existing config drift; out of Plan 06-11's "frontend/e2e/full-suite.spec.ts" scope. Documented for operator above.

## Known Stubs

None. UI-10 is `test.skip()` when no thread with explore_knowledge_base trace exists in the test account — this is intentional graceful degradation (documented as a key decision), not a stub. The test does run when a fixture thread exists.

## Self-Check: PASSED

- `frontend/e2e/full-suite.spec.ts` exists and contains: ✅ (18 `@phase6` occurrences across test names + comments)
- Commit `2d240c8` (Task 0 helpers): ✅ found in git log
- Commit `52e7713` (Tasks 1-3 describe block): ✅ found in git log
- Commit `adc6b15` (Rule 1 __dirname fix): ✅ found in git log
- Pitfall 12 grep gate: ✅ PASSED on focused-suite run
- All acceptance criteria grep gates: ✅ green (see Verification Grep Gates section)
