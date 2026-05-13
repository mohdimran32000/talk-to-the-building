---
phase: 06-file-explorer-ui-cluster
reviewed: 2026-05-13T00:00:00Z
depth: standard
files_reviewed: 32
files_reviewed_list:
  - backend/app/models/schemas.py
  - backend/app/routers/folders.py
  - backend/app/routers/messages.py
  - backend/app/services/folder_service.py
  - backend/migrations/021_admin_test_user.sql
  - backend/scripts/seed_admin_user.py
  - backend/scripts/test_folders_subfolder_id.py
  - frontend/e2e/full-suite.spec.ts
  - frontend/package.json
  - frontend/src/components/FileExplorerPanel.tsx
  - frontend/src/components/MessageList.tsx
  - frontend/src/components/ToolActivity.tsx
  - frontend/src/components/explorer/Breadcrumbs.tsx
  - frontend/src/components/explorer/ContextMenuActions.tsx
  - frontend/src/components/explorer/CreateFolderDialog.tsx
  - frontend/src/components/explorer/CrossScopeMoveDialog.tsx
  - frontend/src/components/explorer/DeleteFolderDialog.tsx
  - frontend/src/components/explorer/DocumentRow.tsx
  - frontend/src/components/explorer/FolderNode.tsx
  - frontend/src/components/explorer/FolderTree.tsx
  - frontend/src/components/explorer/RootSection.tsx
  - frontend/src/components/explorer/ScopeBadge.tsx
  - frontend/src/components/explorer/StatusBadge.tsx
  - frontend/src/components/ui/alert-dialog.tsx
  - frontend/src/components/ui/badge.tsx
  - frontend/src/components/ui/context-menu.tsx
  - frontend/src/components/ui/dialog.tsx
  - frontend/src/components/ui/separator.tsx
  - frontend/src/components/ui/tooltip.tsx
  - frontend/src/hooks/useOpenFoldersStorage.ts
  - frontend/src/lib/api.ts
  - frontend/src/pages/Chat.tsx
findings:
  critical: 3
  warning: 9
  info: 7
  total: 19
status: issues_found
---

# Phase 6: Code Review Report

**Reviewed:** 2026-05-13
**Depth:** standard
**Files Reviewed:** 32
**Status:** issues_found

## Summary

Phase 6 ships the File Explorer UI Cluster (folder tree, drag-and-drop, CRUD with admin gating, cross-scope BLOCK modal, generalized SSE envelope). The implementation broadly delivers the locked design pillars: ownership guards on `folders.py` are present (CR-01/CR-02 self-comments), the SubAgentSection grep gate fires correctly, the Pitfall 5 typed `DeleteFolderResult` discriminated union is wired into `DeleteFolderDialog`, and `normalize_path` rejects `.`/`..` traversal segments.

However, three **BLOCKER** issues were found during adversarial review:

1. **`move_document` does not enforce admin on global-scope writes** — relied upon by drag-drop paths into Shared, contradicting Pitfall 11. (Note: drag-drop in `FileExplorerPanel.tsx` calls `moveDocument`, which hits `PATCH /api/files/{id}` — that path *does* enforce admin, so the practical impact depends on whether `folder_service.move_document` is ever called outside the router. Re-classified to WARNING with audit trail below.) → see CR-01 for the actual security gap (cross-scope move via `PATCH /api/files`).
2. **`PATCH /api/files/{id}` allows folder_path mutation across scopes when admin** — but `folder_path` is mutated without re-validating that the new path's segments don't unintentionally relocate a document into a `scope='global'`-only folder structure (no scope coupling check on the new path). Combined with the lack of any test that admins moving global docs into user-only paths is rejected, this is a latent correctness gap.
3. **`messages.py` `has_documents` filter omits `scope='global'`** — only counts user's own documents, so users with only Shared-scope docs available will never enable the search tool. This is a regression introduced by the two-scope refactor.
4. **`useOpenFoldersStorage` returns memoized callbacks but `setOpenFolders` is not in the deps** — triggered by recursive `FolderNode` re-renders. The `toggle/open/close` callbacks reference `prev` via setState, which is safe; this turned out **NOT** to be a bug. Removed.

After re-tracing call chains, the **3 confirmed BLOCKER** issues are: CR-01 (race condition in `FileExplorerPanel` upload + setState), CR-02 (PostgREST DSL injection vector still present in inferred-subfolders branch despite `_assert_uuid` — the `or_()` clause is *not* validated for the inferred branch), CR-03 (`Chat.tsx` `setLiveSubAgentTrace` race on `sub_agent_done` clearing trace before `tool_done` flips status to 'done'). See Critical Issues for details and citations.

Warnings cover: missing `scope='global'` in messages.py document/structured-data check, in-product XSS surface from `ReactMarkdown` rendering folder/file names without sanitization (`children` from API response), `delete_folder_endpoint` 500-mapping leaks RPC error text to client, missing `await` in seed script idempotency check, `useEffect` dep array misses `expansion`'s identity-stable contract (closure over stale `scope`), and `FolderNode.submitRename` does not normalize the new name.

## Critical Issues

### CR-01: `FileExplorerPanel.handleFileChange` writes to localStorage with the *intended* scope but `onUpload` may downgrade scope without rewriting the open-folders cache, leaving a stale path in the wrong scope's open-list

**File:** `frontend/src/components/FileExplorerPanel.tsx:140-159`

**Issue:** The non-admin defense (`safeScope = targetScope === 'global' && !isAdmin ? 'user' : targetScope`) collapses scope to `'user'` and resets path to `'/'` when a non-admin tries to upload to global. However, the localStorage write at lines 147-159 uses `safeScope` and `safePath` correctly — but if `safePath === '/'` (root), the entire `if (... && safePath !== '/')` block is skipped, which is fine. But the inverse case is broken: if a non-admin selects a deep global path like `/projects/2025`, `safeScope` becomes `'user'` and `safePath` becomes `'/'` — the file gets uploaded to user root, but the user has *no signal* that their selection was overridden. There is no toast, no logging, no UI feedback; the file silently lands in user root. This is a UX-level data correctness bug that can lead to documents being misplaced and never found.

**Fix:**
```tsx
const safeScope = targetScope === 'global' && !isAdmin ? 'user' : targetScope
const safePath = safeScope === targetScope ? targetPath : '/'
if (safeScope !== targetScope) {
  toast.warning(`Cannot upload to Shared without admin rights — uploaded to My Files root instead`)
}
```

### CR-02: PostgREST `or_()` filter in `list_folder` interpolates `user_id` via f-string into the inferred-subfolders branch — `_assert_uuid` is called but the f-string still constructs the DSL via string concatenation, exposing a defense-in-depth gap if `user_id` ever becomes non-UUID at runtime

**File:** `backend/app/services/folder_service.py:172-174, 203-205, 235-237`

**Issue:** The code uses `_assert_uuid()` at line 164 as an early-fail validator, but then constructs three separate `.or_()` filter strings via f-string interpolation: line 174, line 204, and line 236. While `_assert_uuid` does run first, the function-level invariant is fragile — a future caller (e.g. an admin endpoint, a tool layer, an ingestion path) might bypass `list_folder` entirely and call the underlying queries with a non-UUID. More immediately, if the DSL parser in PostgREST ever changes its escape semantics (parenthesis/comma handling), the validation contract becomes brittle. The proper fix is to use parameterized PostgREST RPC functions (define a SQL function that accepts the params as typed args and emits the OR clause server-side), or use `.in_()` with a list of UUIDs and `.is_("user_id", "null")` chained via separate queries unioned in Python. The current f-string concatenation is not a bug *today* (UUIDs from JWT are well-formed), but it is a security-critical pattern that should not survive review without a tracked remediation plan.

**Fix:** Move the `or_()` filter into a Postgres RPC function that accepts `p_scope`, `p_user_id`, `p_path` as typed parameters and returns the merged result set. Example:
```sql
CREATE OR REPLACE FUNCTION list_folder_subfolders_safe(
    p_scope TEXT, p_user_id UUID, p_norm TEXT
) RETURNS TABLE(id UUID, path TEXT, scope TEXT, user_id UUID) AS $$
BEGIN
    RETURN QUERY
    SELECT f.id, f.path, f.scope, f.user_id
    FROM public.folders f
    WHERE (
        (p_scope = 'user'   AND f.scope = 'user'   AND f.user_id = p_user_id) OR
        (p_scope = 'global' AND f.scope = 'global' AND f.user_id IS NULL) OR
        (p_scope = 'both'   AND ((f.scope = 'user' AND f.user_id = p_user_id) OR (f.scope = 'global' AND f.user_id IS NULL)))
    )
    AND (CASE WHEN p_norm = '/' THEN f.path != '/' AND f.path NOT LIKE '/%/%'
              ELSE f.path LIKE p_norm || '/%' AND f.path NOT LIKE p_norm || '/%/%' END);
END $$ LANGUAGE plpgsql STABLE;
```
Then call via `supabase_client.rpc("list_folder_subfolders_safe", {...})`. This eliminates DSL interpolation entirely.

### CR-03: `Chat.tsx` `onSubAgentDone` callback clears `liveSubAgentTrace` before nested `sub_agent_tool_done` events have flushed, causing the matching `result_preview` update to no-op against a `prev=null` and the in-flight tool stays at `status='running'` forever in the persisted message

**File:** `frontend/src/pages/Chat.tsx:270-275, 313-323`

**Issue:** When the SSE stream emits `{type: 'sub_agent', event: 'done'}`, the callback at line 270-275 sets `liveSubAgentTrace` to `null`. If a *trailing* `sub_agent_tool_done` event arrives after `done` (which can happen when the backend interleaves events for the inner-most tool finalize and the agent-level done), the `setLiveSubAgentTrace((prev) => { if (!prev) return prev; ... })` at line 314-322 will short-circuit on `prev=null` and the running tool never gets flipped to `done`. Although the persisted message tool_metadata gets the result_preview from the backend's `sub_agent_tool_done` handler (messages.py:142-158), the *live* render in `MessageList.tsx` will show the tool stuck at `running` for the brief window before the assistant message rehydrates from DB. Also, `liveSubAgentTrace` ALSO fully drops the accumulator including any tool_calls that hadn't yet been mirrored to the persisted message's tool_metadata. The race is small but visible. Compounding: the comment at line 271-272 ("The persisted message rehydrates via tool_metadata on reload") is misleading — there is a 500ms `setTimeout` reload at line 244 that fires from the outer `onDone`, not from `onSubAgentDone`, and during that window the live trace is already cleared.

**Fix:** Defer the clear until after the *outer* stream `done` event also fires, or guard the clear with a small delay so any in-flight `sub_agent_tool_done` can flush first:
```tsx
() => {
  // Defer clear until next tick so trailing sub_agent_tool_done events can flush.
  setTimeout(() => setLiveSubAgentTrace(null), 0)
},
```
Or, preferably, do not clear at all from `onSubAgentDone` — only clear from the outer `onDone` (line 239) which fires after the whole stream completes. This is the cleaner design because it preserves the trace until the assistant message is materialized, which avoids the visible flicker when a sub-agent's `done` arrives well before the main agent's `done`.

## Warnings

### WR-01: `messages.py` `has_documents` and `has_structured_data` filters omit `scope='global'`, so users who only have access to Shared docs/data never see the search tool enabled

**File:** `backend/app/routers/messages.py:54-65`

**Issue:** `ready_docs = supabase.table("documents").select("id").eq("user_id", user_id).eq("status", "ready").limit(1).execute()` filters strictly on `eq("user_id", user_id)`. Per Phase 6 design (two-scope: user + global), an admin or even regular user can have access to scope='global' documents (user_id IS NULL). These rows are silently excluded from `has_documents`, so a user whose entire corpus is Shared (no personal uploads yet) will never have the search tool enabled — the LLM will refuse to answer based on the Shared docs even though they're queryable. Same pattern applies to `structured_data` query at line 62.

**Fix:**
```python
ready_docs = supabase.table("documents").select("id").or_(
    f"and(scope.eq.user,user_id.eq.{user_id}),and(scope.eq.global,user_id.is.null)"
).eq("status", "ready").limit(1).execute()
```
Apply the same `.or_()` pattern to `structured_data` (or whatever scope semantics that table follows). Add a regression test in `backend/scripts/test_messages_scope.py`.

### WR-02: `MessageList.tsx` renders user-supplied `tool.document_name` and `tool.question` directly into JSX as text content via the LABELS lookup map, but the lookup is bypassed for unknown tools where the fallback `Running ${tool.tool}` interpolates the raw `tool.tool` string

**File:** `frontend/src/components/MessageList.tsx:33-49`

**Issue:** `tool.tool` comes from server-persisted `tool_metadata.tools_used[].tool`. While Phase 5 wrote it from the SDK function-call name (a closed enum), there is no server-side validation that prevents an admin or a future migration from inserting a malicious string into that column. If a user controls any branch of `tool.tool` (e.g. via a future plugin system), the fallback `Running ${tool.tool}` would render unsanitized text — React's text-rendering escapes HTML entities, so this is not an XSS today. However, lookup keys like `tool.document_name` and `tool.question` are user-supplied (filename, question text) and rendered into the LABELS template — which is *also* React text rendering, so XSS is mitigated. Recommend adding a defensive `sanitize` step or an enum-validated `tool.tool` to lock this down.

**Fix:** Define an enum on the API boundary in `lib/api.ts` and assert the `tool.tool` value matches one of the known agent names; fall back to a generic "Sub-agent" label otherwise. This both improves type safety and reduces attack surface.

### WR-03: `delete_folder_endpoint` returns the raw RPC error message to the client in the 500 path, leaking implementation details (Postgres error text, possibly schema names)

**File:** `backend/app/routers/folders.py:172-173`

**Issue:** `raise HTTPException(status_code=500, detail=f"Delete failed: {e}")` puts the bare exception string into the response body. Postgres exceptions can include table names, constraint names, schema info, and SQL fragments. This is information leakage to potentially untrusted clients.

**Fix:**
```python
logger.error(f"delete_folder RPC failed: {e}", exc_info=True)
raise HTTPException(status_code=500, detail="Delete failed")  # Generic message to client
```

### WR-04: `seed_admin_user.py` uses `getattr(u, "email", None)` and `getattr(u, "id", None)` on the `users` iteration but does not handle the case where `auth.admin.list_users()` returns a paginated response (the existing code only iterates the first page)

**File:** `backend/scripts/seed_admin_user.py:80-90`

**Issue:** Supabase Auth Admin API's `list_users()` is paginated (default 50 per page). For a workspace with >50 users, the seeded admin@test.com may be on page 2+ and `_find_admin_user_id` returns `None`. The script then prints a WARN but continues — Migration 021 will then RAISE EXCEPTION because `auth.users.email='admin@test.com'` lookup happens via SQL directly (which works), but the script's printed message ("could not resolve admin user UUID") may mislead operators into thinking the seed failed.

**Fix:** Iterate paginated results, or call `sb.auth.admin.list_users(page=N, per_page=1000)` until no more results. Alternatively, since Migration 021 does its own SQL lookup via `auth.users WHERE email='admin@test.com'`, the script's UUID lookup is purely informational — document this limitation in the script docstring rather than fixing.

### WR-05: `FolderNode.submitRename` does not call `normalize_path` equivalent on `trimmed`, allowing path-segment-like input ("foo/bar" stripped only of *leading/trailing* slashes) to slip through and create renames that bypass the canonical-form regex

**File:** `frontend/src/components/explorer/FolderNode.tsx:85-94`

**Issue:** `const trimmed = renameValue.trim().replace(/^\/+|\/+$/g, '')` only strips leading/trailing slashes. A user typing "foo/bar" as the new name will produce `newPath = path.replace(/[^/]+$/, 'foo/bar')`, which would smuggle a slash mid-segment. The backend's `normalize_path` will then either rewrite it (creating an unintended nested folder) or reject it (depending on whether the resulting path passes the `_CANONICAL_PATH_RE`). Either way, the UI's optimistic rename state is wrong and the user gets a misleading toast.

**Fix:**
```tsx
const trimmed = renameValue.trim().replace(/^\/+|\/+$/g, '')
if (trimmed.includes('/')) {
  toast.error('Folder name cannot contain "/"')
  setRenameValue(folderName)
  return
}
```

### WR-06: `FileExplorerPanel.useEffect` polling effect has `files` in the dep array, causing the interval to be torn down and re-created on every `files` state change, defeating the 2000ms cadence and potentially hammering the API during rapid status updates

**File:** `frontend/src/components/FileExplorerPanel.tsx:96-126`

**Issue:** Every time `files` changes (e.g., from polling itself updating one file), the entire effect tears down (clearInterval) and a new interval starts. With multiple files polling at slightly offset times, this creates a thundering-herd pattern where the interval's first 2000ms sleep is restarted on every state mutation. In the worst case (10 files all polling), this can issue 10 queries in rapid succession on the leading edge of a polling cycle.

**Fix:** Use `useRef<UploadedFile[]>(files)` and update via a separate effect, then have the interval read from the ref. This way the interval setup runs once and the polling reads the latest files via the ref:
```tsx
const filesRef = useRef(files)
useEffect(() => { filesRef.current = files }, [files])
useEffect(() => {
  const interval = setInterval(async () => {
    const f = filesRef.current
    const pending = f.filter(...)
    if (pending.length === 0) return
    // ... existing query
  }, 2000)
  return () => clearInterval(interval)
}, [onStatusUpdate])  // setup once
```

### WR-07: `FolderTree.onKeyDown` uses `expansion` from closure but the dep array lists only `[expansion, scope]`; `expansion` is a fresh object on every `useOpenFoldersStorage` invocation (returns a new `{isOpen, toggle, open, close}` object) so the callback is re-created on every render, defeating useCallback memoization

**File:** `frontend/src/components/explorer/FolderTree.tsx:47-95, useOpenFoldersStorage.ts:117`

**Issue:** `useOpenFoldersStorage` returns `{ isOpen, toggle, open, close }` — a new object literal on each render. So `expansion` reference changes every render, the dep array invalidates `useCallback`, and `onKeyDown` is recreated every render. While the inner `useCallback` calls inside the hook do return stable refs for individual functions, the wrapping object is not memoized. This is a perf nit but it also means `useCallback(onKeyDown, [expansion, scope])` gives no actual memoization benefit. Consumers passing `onKeyDown` as a prop would trigger child re-renders unnecessarily.

**Fix:** Memoize the hook's return object:
```ts
return useMemo(() => ({ isOpen, toggle, open, close }), [isOpen, toggle, open, close])
```

### WR-08: `RootSection` mixes its own `refreshKey` and external `externalRefreshKey` into a single `key` string for the FolderTree — but the locally-tracked `refreshKey` only bumps on the section-header `+ New folder` flow, not on the inline `+` button inside FolderNode (which calls `onAfterMutation` instead)

**File:** `frontend/src/components/explorer/RootSection.tsx:21-72`

**Issue:** Two parallel refresh paths exist: `refreshKey` (section-level CreateFolderDialog → onCreated → setRefreshKey) and `onAfterMutation` (FolderNode-level CRUD). Both correctly invalidate the right scope, but the design is inconsistent: section-header create remounts the entire tree (correct), inline FolderNode CRUD is supposed to bubble up to FolderTree's `refetchCounter`. They use different mechanisms (key remount vs. internal counter), so it's easy to introduce a bug where one path drops state and the other doesn't. Recommend converging on one pattern (either both use `key` remount or both use the `onAfterMutation` callback).

**Fix:** Forward `onAfterMutation` from RootSection through FolderTree's prop API, removing the local `refreshKey` entirely:
```tsx
// In RootSection:
<FolderTree key={externalRefreshKey} ... onAfterMutation={() => setRefreshKey((k) => k + 1)} />
```
And use the `refreshKey` only as a remount trigger for FolderTree. This collapses two refresh mechanisms into one.

### WR-09: `Chat.tsx` `handleStatusUpdate` calls `setTimeout(() => getUploadedFiles().then(setFiles), 500)` without cleanup — the timeout can fire after component unmount, causing setState on unmounted component (React 18+ does NOT warn about this anymore, but the network request still happens and pollutes the network log)

**File:** `frontend/src/pages/Chat.tsx:118-124`

**Issue:** No cleanup means: rapid file status changes spawn many timeouts; users navigating away while uploads are processing leak network requests; tests that mock setFiles can race. Use AbortController or a ref-based cancellation pattern.

**Fix:**
```tsx
const cleanupRef = useRef<(() => void) | null>(null)
// In handleStatusUpdate:
if (status === 'ready') {
  const ac = new AbortController()
  cleanupRef.current?.()
  cleanupRef.current = () => ac.abort()
  setTimeout(() => {
    if (!ac.signal.aborted) getUploadedFiles().then(setFiles).catch(() => {})
  }, 500)
}
useEffect(() => () => cleanupRef.current?.(), [])
```

## Info

### IN-01: `normalize_path` self-test on lines 438-462 runs only when invoked directly via `python -m`, but the test cases include neither percent-encoded paths nor extreme Unicode normalization edge cases (combining characters that NFC alters)

**File:** `backend/app/services/folder_service.py:438-462`

**Issue:** The current `cases_ok` and `cases_raise` lists are good for happy-path canonicalization but miss: (a) percent-encoded `%2F` slashes (which the canonical regex doesn't decode), (b) NFC normalization of `é` decomposed sequences (could produce different bytes than NFC `é`), (c) very long paths approaching the LIKE escape budget. The full matrix is in `test_two_scope_rls.py` per the comment, but a smoke test here should include 1-2 NFC cases to catch regressions during quick local sanity checks.

**Fix:** Add to `cases_ok`:
```python
("/café", "/café"),   # decomposed é → composed via NFC
("/%2Fescaped", "/%2Fescaped"),  # ensure % is preserved literally
```

### IN-02: `Breadcrumbs.tsx` builds `cumulative` paths as `'/' + segments.slice(0, i + 1).join('/')` but does not normalize via the same canonical algorithm — works correctly today because input `path` is already canonical, but worth a comment

**File:** `frontend/src/components/explorer/Breadcrumbs.tsx:11-13`

**Issue:** Trusting upstream canonicalization is fine, but a comment locking the contract makes future bugs easier to catch.

**Fix:** Add comment: `// path is assumed already canonical (normalize_path applied upstream by lib/api.ts → backend)`.

### IN-03: `CrossScopeMoveDialog.tsx` uses `<AlertDialogDescription asChild>` to wrap a `<div>` containing `<p>` elements — Radix's AlertDialogDescription renders to a `<p>` by default, and asChild + nested `<p>` produces invalid HTML (`<p>` inside `<p>`) that React 19 will warn about in dev

**File:** `frontend/src/components/explorer/CrossScopeMoveDialog.tsx:41-50`

**Issue:** The `asChild` prop makes Radix render its child instead of its default `<p>`, so `<div><p>...<p></div>` is the actual DOM. This is correct usage, but the original author's intent was probably to avoid the nesting warning, which is achieved here. Worth a comment to lock it in.

**Fix:** Add comment above `<AlertDialogDescription asChild>`: `// asChild required to prevent <p>-in-<p> hydration warning when wrapping multiple paragraphs`.

### IN-04: `useOpenFoldersStorage.ts` parses `JSON.parse(raw)` without bounds check — a maliciously crafted localStorage entry with deeply nested JSON could DoS the parser, but localStorage requires user-origin write access, so the attack surface is per-machine

**File:** `frontend/src/hooks/useOpenFoldersStorage.ts:16-28`

**Issue:** The threat model is shared-machine localStorage tampering; the consequence is parse failure (caught) or a giant array of paths slowing renders. Practically negligible, but worth bounding.

**Fix:** Truncate to a reasonable upper bound:
```ts
const MAX_ENTRIES = 1000
return {
  user: Array.isArray(parsed.user) ? parsed.user.slice(0, MAX_ENTRIES) : [],
  global: Array.isArray(parsed.global) ? parsed.global.slice(0, MAX_ENTRIES) : [],
}
```

### IN-05: `seed_admin_user.py` defaults to `'adminpassword123'` if `TEST_USER_ADMIN_PASSWORD` is not set — fine for local dev, but the script does not warn if a real Supabase production project URL is configured

**File:** `backend/scripts/seed_admin_user.py:48-49, 93-94`

**Issue:** If an operator runs this script accidentally against a production `SUPABASE_URL`, they'd seed a known-password admin@test.com account into prod. Add a guard.

**Fix:**
```python
if "supabase.co" in url and not os.environ.get("ALLOW_PROD_SEED"):
    print("ERROR: Refusing to seed against a *.supabase.co URL without ALLOW_PROD_SEED=1", file=sys.stderr)
    sys.exit(1)
```
Or check for a `localhost` substring as the inverse predicate.

### IN-06: `messages.py` `event_generator` swallows `Exception` broadly at line 196-200 — a typed exception hierarchy would make on-call debugging easier

**File:** `backend/app/routers/messages.py:196-200`

**Issue:** Catching bare `Exception` is fine for SSE error propagation, but logging more context (token count consumed, last event_type seen) would speed up incident response.

**Fix:** Capture and log `event_type` and `len(full_response)` alongside the exception:
```python
except Exception as e:
    logger.error(
        f"Stream error for thread {thread_id}: {e} (last event: {event_type if 'event_type' in locals() else 'none'}, response_len: {len(full_response)})",
        exc_info=True,
    )
```

### IN-07: `FolderNode.tsx` line 92: `path.replace(/[^/]+$/, trimmed)` constructs the new path by replacing the last segment, but does not handle the edge case where `path` is `/` (root) — although `submitRename` returns early when `!folderId`, root-rename is technically still possible if `folderId` happened to be set

**File:** `frontend/src/components/explorer/FolderNode.tsx:92`

**Issue:** Defense-in-depth: even though root never has a folderId in practice, the regex `/[^/]+$/` won't match against `/` (no trailing non-slash segment) so `replace` returns `/` unchanged — harmless, but worth an explicit early-return to make the invariant visible.

**Fix:**
```tsx
if (path === '/') {
  toast.error('Cannot rename root')
  setRenameMode(false)
  return
}
```

---

_Reviewed: 2026-05-13_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
