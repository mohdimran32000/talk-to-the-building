# Phase 6: File-Explorer UI Cluster — Context

**Gathered:** 2026-05-10
**Status:** Ready for planning
**Source:** Inline disambiguation of three RESEARCH.md open questions (no full /gsd-discuss-phase run). Project-level context lives in `.planning/PROJECT.md`, `.planning/ROADMAP.md`, `.planning/REQUIREMENTS.md`, and `.planning/codebase/`.

<domain>
## Phase Boundary

Phase 6 replaces the flat `FileUploadPanel` with a recursive two-scope file-explorer panel ("Shared" + "My Files"), wires folder CRUD + single-document drag-move + scope visualization through the Phase 3 folder API, and extends `MessageList`'s `SubAgentSection` recursively to render Phase 5's Explorer SSE trace inline in chat.

**In-scope:**
- `FileExplorerPanel.tsx` (replaces `FileUploadPanel.tsx`)
- Folder CRUD via shadcn `ContextMenu` + inline buttons
- Single-document drag-move with shadcn-style drop indicators (`@dnd-kit/core`)
- Scope badges + distinct iconography for Shared vs My Files
- Recursive `SubAgentSection` extension (no per-agent-type forks)
- Playwright additions in `frontend/e2e/full-suite.spec.ts`
- Admin test account provisioning (for UI-11 scope visibility test)

**Out-of-scope (deferred):**
- Multi-document drag-move (single-doc only this phase)
- Folder-level drag-move (only documents drag)
- Real-time folder updates (polling/refetch on mutation only — no Realtime)
- Promote-to-Shared admin endpoint (cross-scope moves redirect to re-upload instead)

</domain>

<decisions>
## Implementation Decisions

### D-01: Cross-scope drag-move = block + redirect
- A non-admin (or any user) dragging a document from My Files onto Shared (or vice versa) triggers a confirmation modal that **does NOT call any backend endpoint**.
- Modal copy: explains scope is immutable at the DB level (Migration 015 RLS trigger is ground truth), suggests "Delete this document and re-upload as admin to Shared" as the supported path.
- No "Promote to Shared" backend endpoint is added in Phase 6.
- Drag onto a same-scope folder remains fully functional (calls the Phase 3 document-move endpoint).
- **Why:** Honors the existing RLS trigger as the source of truth for scope immutability; avoids adding scope-promotion semantics that weren't designed for in Phase 1; satisfies the literal REQUIREMENTS.md text "cross-scope moves trigger a confirmation modal" (the modal exists; it just blocks rather than mutates).

### D-02: Admin test account = provisioned in Phase 6
- Add a Phase 6 task that creates a dedicated `admin@test.com` admin user via a Supabase seed script or migration (sets `is_admin = true` on the user/profile row).
- Plan emits `ADMIN_TEST_EMAIL` and `ADMIN_TEST_PASSWORD` env vars (or hardcodes them in `frontend/playwright.config.ts` test fixtures — planner picks the more idiomatic pattern for this codebase after reading config).
- UI-11 Playwright test logs in as the admin account, asserts "Create folder in Shared" affordance is visible, then logs in as the regular `test@test.com` account and asserts the affordance is NOT visible.
- **Why:** Self-contained and reproducible; no environment-specific manual setup; admin scope is security-sensitive enough to warrant a real DB user (not a test-only API hook).

### D-03: DocumentResponse field exposure = conditional Wave-0 plan
- Planner runs a grep on `backend/app/schemas/` (or wherever `DocumentResponse` lives — researcher noted it's a Pydantic model) for `content_markdown_status` field.
- **If field is absent:** Add a Wave-0 backend plan "Extend DocumentResponse with content_markdown_status" that adds the field to the Pydantic schema + ensures the FastAPI router serializes it.
- **If field is present:** No extra plan; planner notes confirmation in the wave-0 frontmatter of the first frontend plan.
- Frontend plans (Wave 1+) consume `document.content_markdown_status` regardless — they don't gate on the backend plan beyond standard `depends_on` ordering.
- **Why:** Self-correcting; keeps Phase 6 frontend-focused unless a small backend gap genuinely blocks success criterion #1 ("content_markdown re-index status").

### Tree component
- Build in-house: ~150 LOC recursive `FolderNode` component (researcher recommendation, accepted as default).
- Compose with `@radix-ui/react-context-menu` for right-click CRUD.
- Reject `react-arborist` (fights two-root-section spec) and HTML5 native (no a11y).

### Drag-and-drop library
- `@dnd-kit/core@6.3.1` + `@dnd-kit/sortable@10.0.0` (researcher-verified versions on 2026-05-10, React-19 compatible).
- Drop indicator: shadcn-style horizontal line for "between sibling nodes", folder-highlight for "into folder" — implementation pattern lives in researcher's RESEARCH.md.

### Two simultaneous root sections
- "Shared" (`scope='global'`) and "My Files" (`scope='user'`) render as two `<RootSection>` components stacked vertically (NOT tabs).
- Each gets its own visual treatment for Pitfall-11 mitigation: distinct icon (Users vs User), distinct badge color, distinct section header.

### localStorage persistence
- Per-user open-folder state: key `fileExplorer:open:{userId}`, value `JSON.stringify([...folderIds])`.
- Restored on mount; updated on toggle.

### SubAgentSection extension (Pitfall 12)
- Same component renders `analyze_document` and Explorer — no `if (agentType === 'explorer')` branches.
- Recursion seam: `tool.tool_calls?.map(...)` (empty array is the natural no-op for `analyze_document`).
- Tool-icon mapping (lucide): `list_files` → Folder, `read_document` → File, `grep` → Search, `tree`/`glob` → Eye (planner confirms after reading existing icon imports).
- Backwards-compat: old chat reload from `messages.tool_metadata` must render correctly — verified via Playwright test that reloads a pre-Phase-6 thread.

### SSE legacy-emit removal
- Phase 6 owns deleting both:
  - Backend: 5 legacy `yield` lines in `backend/app/routers/messages.py` that emit pre-generalization `sub_agent_*` events
  - Frontend: 5 `parsed.type === 'sub_agent_*'` branches in `frontend/src/lib/api.ts`
- These are deleted in the same release that switches to the generalized `parsed.type === 'sub_agent'` envelope from Phase 5.
- Researcher flagged this — accepted as in-scope for Phase 6 to avoid leaving dead code.

### Claude's Discretion
- Exact shadcn primitive composition (Dialog vs AlertDialog for delete confirm — planner picks based on convention)
- State management approach (TanStack Query vs raw fetch+useState — planner reads existing Chat.tsx pattern and matches)
- Folder tree state shape (flat normalized vs nested — planner picks based on cache-invalidation simplicity)
- Test fixture layout for admin account (env var vs Playwright fixture — planner matches existing pattern)
- Lucide icon for "between sibling" drop indicator vs "into folder" highlight — visual polish details

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase boundary + requirements
- `.planning/ROADMAP.md` — Phase 6 section (success criteria, threats/pitfalls)
- `.planning/REQUIREMENTS.md` — UI-01..UI-11, TEST-05 verbatim text
- `.planning/PROJECT.md` — overall product goals + scope-promotion stance

### Codebase landscape
- `.planning/codebase/ARCHITECTURE.md` — frontend/backend boundaries, SSE flow
- `.planning/codebase/CONVENTIONS.md` — naming, file layout, shadcn patterns
- `.planning/codebase/STRUCTURE.md` — directory map
- `.planning/codebase/TESTING.md` — Playwright + backend test conventions
- `.planning/codebase/CONCERNS.md` — RLS, scope, security stance

### Upstream phase contracts (DEPENDS ON)
- `.planning/phases/03-folder-service-routers-dedup-extension/*-SUMMARY.md` — folder API contract (list/create/rename/delete/move endpoints, structured 409 error)
- `.planning/phases/05-explorer-sub-agent-sse-protocol-generalization/*-SUMMARY.md` — generalized SSE event protocol (`sub_agent_tool_start` / `sub_agent_tool_done` shapes)
- `.planning/phases/05-explorer-sub-agent-sse-protocol-generalization/05-RESEARCH.md` — SSE design rationale
- `.planning/phases/05-explorer-sub-agent-sse-protocol-generalization/05-VERIFICATION.md` — what shipped + tool_metadata persistence shape

### Pitfalls (MUST mitigate, all from .planning/research/PITFALLS.md)
- Pitfall 5 — folder delete UX (count from server's structured error)
- Pitfall 11 — scope confusion / scope leak (visual differentiation + cross-scope guard)
- Pitfall 12 — `SubAgentSection` extension must be recursive, not forked

### Project rules
- `CLAUDE.md` — testing rules, no LangChain/LangGraph, RLS, SSE for chat / polling for ingestion

</canonical_refs>

<specifics>
## Specific Ideas

- Modal copy for cross-scope drag (D-01) — short, instructional, names the supported path: "Scope is permanent for security. To move this to Shared, an admin must re-upload it from the Shared section."
- Admin test account credentials (D-02) — `admin@test.com` / strong test password; planner picks env-var name to match existing `TEST_EMAIL`/`TEST_PASSWORD` convention in `frontend/e2e/`.
- Lucide icon mapping (verified candidates): `Folder`, `FolderOpen`, `File`, `FileText`, `Search`, `Eye`, `Users`, `User`, `ChevronRight`, `ChevronDown`, `Plus`, `MoreVertical`.
- localStorage key: `fileExplorer:open:{userId}` (researcher's suggestion, accepted).
- Drop indicator visual: 2px horizontal `bg-primary` line for "between"; `bg-primary/10` background ring for "into folder" (matches shadcn convention).

</specifics>

<deferred>
## Deferred Ideas

- Multi-document selection + drag (single-doc only this phase)
- Folder-level drag-move (only documents drag)
- Realtime folder/document updates (polling/refetch on mutation only)
- "Promote to Shared" admin endpoint (out of scope — re-upload is the supported flow)
- Bulk folder operations (delete-many, move-many)
- Folder-color / icon customization
- Drag-to-upload-into-folder (uploads still default to "current folder context with fallback to root" per success criterion #3, but no special drag-from-OS UX)

</deferred>

---

*Phase: 06-file-explorer-ui-cluster*
*Context locked: 2026-05-10 via inline RESEARCH.md disambiguation*
