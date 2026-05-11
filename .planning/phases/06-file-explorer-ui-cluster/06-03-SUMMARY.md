---
phase: 06-file-explorer-ui-cluster
plan: 03
subsystem: ui
tags: [phase6, frontend, deps, shadcn, dnd-kit, radix, wave0]

requires:
  - phase: 06-file-explorer-ui-cluster
    provides: "Plan 06-01 (DocumentResponse.content_markdown_status) + Plan 06-02 (admin@test.com seed) — Wave-0 prerequisites; 06-03 runs in parallel with 06-02 inside Wave 0 since they touch disjoint files"
provides:
  - "@dnd-kit/core@6.3.1 and @dnd-kit/sortable@10.0.0 installed at exact pinned versions for drag-and-drop (Plan 06-10)"
  - "Six shadcn primitives on disk under frontend/src/components/ui/: context-menu, dialog, alert-dialog, badge, tooltip, separator — un-edited CLI output, ready to import from Plans 06-06 / 06-09 / 06-10 / future status badges (UI-08)"
  - "Verified that the radix-ui umbrella package (^1.4.3) supplies all primitives the shadcn CLI generates for this project — no per-subpackage @radix-ui/react-* deps need to be added"
  - "Verified the existing TypeScript build (npx tsc --noEmit) still passes after install — no React-19 peer-dep conflict, no version drift"
affects: [06-06 FolderNode tree, 06-09 ContextMenu CRUD, 06-10 dnd-kit DnD wiring, future UI-08 status badge work]

tech-stack:
  added:
    - "@dnd-kit/core@6.3.1 (drag-and-drop primitives, React-19 compat)"
    - "@dnd-kit/sortable@10.0.0 (sortable list helpers, depends on @dnd-kit/core)"
    - "shadcn primitives: context-menu, dialog, alert-dialog, badge, tooltip, separator"
  patterns:
    - "Wave-0 deps-only plan — splitting dependency installs into their own atomic plan keeps subsequent wave plans focused on logic, not setup. First plan in Phase 6 that adds no application code."
    - "--save-exact for dnd-kit (no caret) — researcher-verified versions; minor drift could break things on React 19. Established as the convention for cross-cutting UI primitives where version stability matters more than minor improvements."
    - "shadcn CLI as source of truth — generated ui/*.tsx files are un-edited; matches existing convention (button.tsx, card.tsx). Hand-editing forbidden so future shadcn updates can re-generate cleanly."
    - "radix-ui umbrella package (v1.4.3) supplies all generated primitives — confirmed this project does NOT use individual @radix-ui/react-* subpackages. CLI generates files that import from the umbrella (`import { ContextMenu as ContextMenuPrimitive } from \"radix-ui\"`)."

key-files:
  created:
    - "frontend/src/components/ui/context-menu.tsx (250 LOC; UI-04 right-click CRUD primitive)"
    - "frontend/src/components/ui/dialog.tsx (158 LOC; folder-create form modal)"
    - "frontend/src/components/ui/alert-dialog.tsx (194 LOC; UI-04 delete-confirm + UI-06 cross-scope BLOCK modal)"
    - "frontend/src/components/ui/badge.tsx (48 LOC; UI-08 scope + status badges)"
    - "frontend/src/components/ui/tooltip.tsx (55 LOC; hover hints)"
    - "frontend/src/components/ui/separator.tsx (28 LOC; section dividers)"
  modified:
    - "frontend/package.json (+2 deps: @dnd-kit/core@6.3.1 exact, @dnd-kit/sortable@10.0.0 exact)"
    - "frontend/package-lock.json (npm lockfile updated; 4 packages added overall)"

key-decisions:
  - "Phase 6 / Plan 03: --save-exact for dnd-kit packages (6.3.1 + 10.0.0) — researcher verified React-19 compat; pin prevents minor-drift regressions on a load-bearing UI primitive set"
  - "Phase 6 / Plan 03: This project uses the radix-ui umbrella package (^1.4.3), NOT individual @radix-ui/react-* subpackages — the shadcn CLI's generated files import from `radix-ui` (umbrella) and the plan's `acceptance_criteria` line about `@radix-ui/react-context-menu` etc. being added to package.json does NOT apply to this codebase. The plan's acceptance criterion was tuned to the upstream shadcn default (subpackages); this project's components.json predates that and uses the umbrella. Documented here so future plans don't try to install the subpackages."

patterns-established:
  - "Wave-0 deps-only plan: dependency installation is split into its own atomic plan so subsequent wave plans (06-06 / 06-09 / 06-10) can focus on logic. First example in Phase 6 of a deps-only plan."
  - "shadcn CLI overwrite-prompt handling: shadcn@3.8.4 add ... --yes does NOT auto-skip the per-file overwrite prompt when the file already exists. Workaround: pipe `printf 'n\\n...'` into the CLI to decline each overwrite, OR scope the install to primitives the project does not yet have. Used the piped-n trick to install alert-dialog after the multi-primitive call broke on the button.tsx overwrite prompt."
  - "Verifier idiom for dep-only plans: `node -e \"require('./node_modules/<pkg>/package.json').version\"` is more authoritative than reading package.json — it confirms the package is actually on disk at the expected version, not just listed in the manifest."

requirements-completed: [UI-04, UI-06, UI-08]

duration: 9min
completed: 2026-05-11
---

# Phase 6 Plan 03: Frontend Deps + shadcn Primitives Summary

**Installed @dnd-kit/core@6.3.1 + @dnd-kit/sortable@10.0.0 at exact pins, then generated six shadcn primitives (context-menu, dialog, alert-dialog, badge, tooltip, separator) via shadcn@3.8.4 CLI — Wave-0 foundation for Phase 6 file-explorer UI cluster.**

## Performance

- **Duration:** ~9 min
- **Started:** 2026-05-11T05:23:20Z
- **Completed:** 2026-05-11T05:32:15Z
- **Tasks:** 2 / 2
- **Files modified:** 8 (2 package files + 6 new ui primitives)

## Accomplishments
- @dnd-kit/core@6.3.1 and @dnd-kit/sortable@10.0.0 installed at exact pinned versions (no caret); both `node -e require(...).version` and `npm ls` confirm 6.3.1 + 10.0.0 on disk.
- Six shadcn primitives generated via CLI: context-menu, dialog, alert-dialog, badge, tooltip, separator — 733 LOC total, un-edited CLI output.
- Existing TypeScript build remains green: `npx tsc --noEmit` exits 0 after both installs.
- Confirmed this project uses the `radix-ui` umbrella package (^1.4.3) — the generated primitives import from `radix-ui` (not `@radix-ui/react-*` subpackages), so no transitive subpackage installs were needed.

## Task Commits

Each task was committed atomically:

1. **Task 1: Install @dnd-kit/core + @dnd-kit/sortable** — `b6e064b` (chore)
2. **Task 2: Install six shadcn primitives via CLI** — `bb9af5c` (feat)

**Plan metadata:** _(final commit below — SUMMARY.md + STATE.md + ROADMAP.md)_

## Files Created/Modified

- `frontend/package.json` — Added `"@dnd-kit/core": "6.3.1"` and `"@dnd-kit/sortable": "10.0.0"` to `dependencies` (exact pins, no caret).
- `frontend/package-lock.json` — Lockfile updated; 4 packages added total.
- `frontend/src/components/ui/context-menu.tsx` (new, 250 LOC) — shadcn ContextMenu primitive backing UI-04 right-click CRUD.
- `frontend/src/components/ui/dialog.tsx` (new, 158 LOC) — shadcn Dialog primitive backing folder-create form.
- `frontend/src/components/ui/alert-dialog.tsx` (new, 194 LOC) — shadcn AlertDialog primitive backing UI-04 delete-confirm + UI-06 cross-scope BLOCK modal.
- `frontend/src/components/ui/badge.tsx` (new, 48 LOC) — shadcn Badge primitive backing UI-08 scope + status badges.
- `frontend/src/components/ui/tooltip.tsx` (new, 55 LOC) — shadcn Tooltip primitive for hover hints.
- `frontend/src/components/ui/separator.tsx` (new, 28 LOC) — shadcn Separator primitive for section dividers.

## Decisions Made

- **--save-exact for dnd-kit:** Both `@dnd-kit/core` (6.3.1) and `@dnd-kit/sortable` (10.0.0) are pinned without a caret. Researcher verified these versions are React-19 compatible on 2026-05-10; pinning prevents accidental minor drift breaking the drag-and-drop layer that Plan 06-10 depends on.
- **radix-ui umbrella, not subpackages:** This codebase predates the shadcn CLI's switch to individual `@radix-ui/react-*` subpackages — `components.json` configures the umbrella `radix-ui@^1.4.3`. The CLI's generated files import from `radix-ui`, not subpackages, and no `@radix-ui/react-context-menu` (etc.) was added to `package.json`. The plan's acceptance criterion checking for those subpackages does NOT apply to this codebase; future plans should NOT try to install them.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] shadcn CLI overwrite prompt blocked alert-dialog install**

- **Found during:** Task 2 (Install six shadcn primitives via CLI)
- **Issue:** `npx shadcn@3.8.4 add context-menu dialog alert-dialog badge tooltip separator --yes` got stuck at an interactive overwrite prompt for `button.tsx` (which already exists from prior phases). Despite `--yes`, the CLI does NOT auto-decline the per-file overwrite prompt; it created context-menu, dialog, badge, separator, tooltip on disk and then hung on the button.tsx prompt before reaching alert-dialog. After killing the process, 5 of 6 primitives existed but `alert-dialog.tsx` was missing. `package.json` was NOT modified.
- **Fix:** Re-ran with the prompt declined via stdin: `printf 'n\n...' | npx shadcn@3.8.4 add alert-dialog`. This time the CLI created `alert-dialog.tsx` and reported `Skipped 1 files: button.tsx` cleanly.
- **Files modified:** `frontend/src/components/ui/alert-dialog.tsx`
- **Verification:** All 6 files now exist; `npx tsc --noEmit` exits 0.
- **Committed in:** bb9af5c (Task 2 commit, combined with the other 5 primitives)

**2. [Rule 1 - Plan Assumption Mismatch] Plan acceptance criterion `@radix-ui/react-*` subpackages does not apply to this codebase**

- **Found during:** Task 2 (verification step)
- **Issue:** Plan acceptance_criteria line 181 says `frontend/package.json dependencies includes @radix-ui/react-context-menu, @radix-ui/react-dialog, @radix-ui/react-alert-dialog, @radix-ui/react-tooltip, @radix-ui/react-separator (transitive shadcn install)`. After running the shadcn CLI, `package.json` showed NO new `@radix-ui/react-*` entries — only `@dnd-kit/core` and `@dnd-kit/sortable` (from Task 1) were added.
- **Fix:** Inspected the generated files (e.g. `context-menu.tsx` line 3: `import { ContextMenu as ContextMenuPrimitive } from "radix-ui"`). Confirmed this project uses the `radix-ui` umbrella package (v1.4.3 already in deps), NOT the individual subpackages. The shadcn CLI's generated files correctly import from the umbrella. No fix was needed — the acceptance criterion was tuned to upstream shadcn's default-subpackage mode and does not match this codebase's umbrella-mode `components.json`. Documented in key-decisions so future plans don't try to install subpackages.
- **Files modified:** None (no fix needed; documentation-only deviation)
- **Verification:** All 6 primitives compile (`npx tsc --noEmit` exits 0), confirming the umbrella package supplies the needed exports.
- **Committed in:** bb9af5c (Task 2 commit; documented in commit message)

**3. [Rule 1 - Over-claiming Plan Frontmatter] requirements: [UI-04, UI-06, UI-08] is foundation-only, not user-functional-complete**

- **Found during:** STATE.md / ROADMAP.md update step (after Task 2 commit)
- **Issue:** The plan frontmatter at line 17 declares `requirements: [UI-04, UI-06, UI-08]` as if completing this plan completes those reqs. But Plan 06-03 only installs primitives — the actual UI wiring lands later:
  - UI-04 (Folder CRUD via ContextMenu) → Plan 06-09
  - UI-06 (Drag-move + cross-scope BLOCK modal) → Plan 06-10
  - UI-08 (Badges + breadcrumbs UI placement) → later in the phase
  The SDK marked UI-04 and UI-06 as `[x]` Complete in `.planning/REQUIREMENTS.md` based on the frontmatter declaration, which would mislead the next planner.
- **Fix:** Manually reverted UI-04, UI-06, UI-08 to `[ ]` Pending in `.planning/REQUIREMENTS.md` AND fixed the Traceability table at the bottom. Added inline `*primitive installed Phase 6 / Plan 03; wiring in Plan 06-NN*` annotations to each so the foundation work is still visible. Updated the `Last updated` footer to call out the correction so future planners see it.
- **Files modified:** `.planning/REQUIREMENTS.md`
- **Verification:** `grep "UI-0[468]"` shows `[ ]` boxes and the inline foundation-installed annotations; the Traceability table rows now say `Pending (Plan 06-03 installed X primitive; wiring in Plan 06-NN)`.
- **Committed in:** _(this fix lands in the SUMMARY.md / final metadata commit below)_

---

**Total deviations:** 3 (1 Rule-3 blocking fix, 1 Rule-1 plan-vs-codebase mismatch documented, 1 Rule-1 over-claiming corrected)
**Impact on plan:** Deviations 1 and 2 were procedural — no design/scope changes. Deviation 3 corrects the requirements ledger so the next planner / verifier doesn't think the UI is functionally complete. All artifacts in the plan's `must_haves` block are present.

## Issues Encountered

- **`tsc --noEmit` silent-output convention:** `npx tsc --noEmit` produces no output on success. Both verifications relied on `tail -20` showing empty output (exit code 0 implicit via no error). Documented here so future verifier agents don't mistake empty output for a hung command.
- **`@dnd-kit/sortable@10.0.0` deduplicates `@dnd-kit/core`:** `npm ls @dnd-kit/core @dnd-kit/sortable` reports `@dnd-kit/core@6.3.1 deduped` under sortable's subtree — this is expected (npm hoists the shared `@dnd-kit/core` peer dep to the top-level node_modules); no duplicate copies on disk.

## User Setup Required

None — no external service configuration; pure local dep install.

## Next Phase Readiness

- ✅ Wave 0 of Phase 6 now has Plan 06-01 (status field) complete + Plan 06-03 (deps + primitives) complete; Plan 06-02 (admin@test.com seed) remains in Wave 0 to close the wave.
- ✅ Plans 06-06 (FolderNode tree primitives), 06-09 (ContextMenu CRUD UI), and 06-10 (dnd-kit DnD wiring) can now `import` from `@dnd-kit/core`, `@dnd-kit/sortable`, `@/components/ui/context-menu`, `@/components/ui/dialog`, `@/components/ui/alert-dialog`, `@/components/ui/badge`, `@/components/ui/tooltip`, `@/components/ui/separator` without runtime or type errors.
- ✅ Frontend build is green (`npx tsc --noEmit` exits 0). No outstanding blockers.

## Self-Check: PASSED

Verified before publishing:

- **Files exist:**
  - `frontend/src/components/ui/context-menu.tsx` — FOUND (250 LOC)
  - `frontend/src/components/ui/dialog.tsx` — FOUND (158 LOC)
  - `frontend/src/components/ui/alert-dialog.tsx` — FOUND (194 LOC)
  - `frontend/src/components/ui/badge.tsx` — FOUND (48 LOC)
  - `frontend/src/components/ui/tooltip.tsx` — FOUND (55 LOC)
  - `frontend/src/components/ui/separator.tsx` — FOUND (28 LOC)
- **Commits exist (verified via `git log --oneline`):**
  - `b6e064b` (Task 1) — FOUND
  - `bb9af5c` (Task 2) — FOUND
- **Verification commands pass:**
  - `node -e require('./frontend/node_modules/@dnd-kit/core/package.json').version` → `6.3.1` ✅
  - `node -e require('./frontend/node_modules/@dnd-kit/sortable/package.json').version` → `10.0.0` ✅
  - `npx tsc --noEmit` → exit 0 (no output, no errors) ✅
  - `ls frontend/src/components/ui/*.tsx | wc -l` → 11 (5 existing + 6 new) ✅

---
*Phase: 06-file-explorer-ui-cluster*
*Completed: 2026-05-11*
