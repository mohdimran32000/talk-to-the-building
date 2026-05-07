# Phase 3: Folder Service + Routers + Dedup Extension — Research

**Researched:** 2026-05-07
**Domain:** FastAPI router design, transactional Supabase RPC for prefix updates, app-level dedup key extension, concurrent-upload safety on a sparse `folders` side table
**Confidence:** HIGH (Phase 1 schema and RLS catalog already shipped and gating; Phase 2 router-edit conventions established; only one truly new technique — the rename RPC — and it is a textbook PL/pgSQL prefix update wrapped in a transaction)

> No CONTEXT.md exists for Phase 3 yet (`/gsd-discuss-phase` has not been run). The phase is fully scoped by ROADMAP.md (5 SCs), REQUIREMENTS.md (FOLDER-02..07 + TEST-01), and STATE.md (Phase 1 + 2 conventions). All 7 requirement IDs are addressed below.

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| FOLDER-02 | `folder_service.py` provides `list_folder`, `create_folder`, `move_document`, `rename_folder`, `delete_folder` | §Folder Service API Surface — five new pure functions extending the existing `normalize_path()` chokepoint at `backend/app/services/folder_service.py:14`-67 |
| FOLDER-03 | Folder rename is transactional prefix update on both `documents.folder_path` AND `folders.path` via a single Supabase RPC | §Folder Rename RPC Design — `rename_folder_prefix(old_prefix, new_prefix, p_scope, p_user_id)` PL/pgSQL function, Migration 019 |
| FOLDER-04 | Folder delete rejects non-empty (returns structured `{error: 'FOLDER_NOT_EMPTY', document_count, subfolder_count}`) | §Folder Delete Implementation — recommended DB-side `delete_folder_if_empty()` PL/pgSQL function (single TX, no TOCTOU race), colocated in Migration 019 |
| FOLDER-05 | `record_manager.py` dedup key extended to `(scope, user_id, folder_path, file_name, hash)` | §Dedup Key Extension — pure app-only change to `determine_action()` SELECT clause; uses already-existing `documents_scope_user_path_filename_unique` index from Migration 012 |
| FOLDER-06 | `folders` router with GET/POST/PATCH/DELETE endpoints; admin gate for `scope='global'` writes | §Folders Router Design — new `backend/app/routers/folders.py`; `Depends(get_admin_user)` from `backend/app/auth.py:43` for global writes |
| FOLDER-07 | Extended `files` router: `POST /api/files/upload?folder_path=&scope=`, `PATCH /api/files/{id}` for rename + folder move | §Files Router Extensions — query args added to existing upload, new PATCH endpoint, scope-immutable enforced explicitly (defense in depth alongside `forbid_scope_mutation` trigger) |
| TEST-01 | `test_folders.py` — folder CRUD, transactional rename, non-empty-delete rejection, concurrent-upload-no-orphan | §Validation Architecture — 15th suite registered in `test_all.py` SUITES list; ThreadPoolExecutor for concurrent-upload race; deliberate-fail RPC variant for mid-rename rollback test |
</phase_requirements>

---

## Project Constraints (from CLAUDE.md)

| Directive | Phase 3 Implication |
|-----------|---------------------|
| Python backend must use `venv` virtual environment | All commands prefix with `cd backend && venv/Scripts/python ...` |
| No LangChain, no LangGraph — raw SDK calls only | Folder router uses supabase-py directly (already the convention; see `backend/app/routers/threads.py`, `files.py`) |
| Use Pydantic for structured LLM outputs | Folder router request/response models go in `backend/app/models/schemas.py` (`FolderResponse`, `FolderCreate`, `FolderPatch`, `FolderListResponse`); not LLM-related but mirrors existing `ThreadCreate`/`ThreadResponse` pattern |
| All tables need RLS — users only see their own data | **Already enforced** — Migration 015 ships 7 folders policies + 5 documents policies + scope-mutation trigger. Phase 3 router should NEVER use `.eq('user_id', ...)` defensively if the service-role client bypasses RLS — but per `CONCERNS.md` the codebase uses service-role everywhere, so app-level `.eq('scope',...).eq('user_id',...)` is **defense in depth and required** |
| Tests must NEVER delete all user data | `test_folders.py` MUST track every created folder/document ID and clean up only those — never blanket DELETE on `folders` or `documents`. Mirror Phase 1's `test_two_scope_rls.py` `_tracked_*` lists pattern |
| Do NOT run the full test suite automatically | Verification gates run only the new `test_folders.py` plus targeted Files smoke; full suite only when user requests |
| Stack: Supabase Postgres + supabase-py + FastAPI + Pydantic | All locked; folders router is pure-FastAPI/supabase-py; rename RPC is pure PL/pgSQL |

---

## Summary

Phase 3 is a **pure CRUD layer** on top of an already-locked schema. There is no new index, no new RLS policy, no new dependency, and no UI work. The work decomposes into four pieces:

1. **Five new functions in `folder_service.py`** (extending the file that today contains only `normalize_path()`): `list_folder`, `create_folder`, `move_document`, `rename_folder`, `delete_folder`. Pure service layer — no FastAPI imports — so it can be unit-tested directly.
2. **One new router `folders.py`** mirroring `threads.py` shape (auth dep, supabase client, four CRUD endpoints). Admin gate is `Depends(get_admin_user)` — already exists at `backend/app/auth.py:43` and is reused unchanged.
3. **Two extensions to `files.py`** (existing router): `POST /api/files/upload` gains `folder_path` and `scope` query args; new `PATCH /api/files/{id}` for rename and folder move. Scope mutation is already DB-blocked by Migration 015's `forbid_scope_mutation` trigger; the router rejects scope changes explicitly so the user gets a 400 instead of a 500.
4. **One PL/pgSQL migration (019)** containing two RPCs: `rename_folder_prefix()` for the transactional rename (FOLDER-03 + Pitfall 5 mid-rename rollback) and `delete_folder_if_empty()` for the transactional empty-check-and-delete (FOLDER-04 + Pitfall 5 TOCTOU race elimination).

The "hard" parts are not the SQL itself — they are: (a) **the architectural call between writing-folders-row-on-upload (strategy A) and writing-it-only-on-explicit-create (strategy B)** — Phase 1's STATE.md decision (`folders` is "sparse — only for explicitly-empty folders") locks strategy B, which sidesteps Pitfall 10 entirely; (b) **the dedup key extension** is one SELECT clause change in `determine_action()` and exploits the unique index Phase 1 already added; (c) **the rename RPC** is a textbook prefix update — `UPDATE documents SET folder_path = $new || substring(folder_path FROM length($old)+1) WHERE folder_path = $old OR folder_path LIKE $old || '/%'` — wrapped in PL/pgSQL so a single transaction covers both `documents` and `folders` writes.

**Primary recommendation:** Land Migration 019 first (RPCs), then `folder_service.py` extensions, then `folders.py` router, then `files.py` extensions, then `record_manager.py` dedup key, then `test_folders.py` registration as the 15th suite. The non-trivial test coverage (concurrent-upload-no-orphan, mid-rename rollback) needs its own design discussion; see §Validation Architecture below.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Path canonicalization (every write path) | Backend service (`folder_service.normalize_path`) | DB CHECK constraint (defense in depth) | Phase 1 chokepoint locked at `backend/app/services/folder_service.py:28-67`; router converts user input → canonical form before any DB call |
| Folder CRUD (create/rename/delete/list) | Backend router (`folders.py`) | Backend service (`folder_service.py`) | Router handles auth + JSON serialization; service handles supabase-py calls + RPC invocation |
| Folder rename atomic prefix update | DB (PL/pgSQL RPC `rename_folder_prefix`) | Backend service (calls `.rpc('rename_folder_prefix', ...)`) | A single transaction spanning `documents` UPDATE + `folders` UPDATE cannot live in app code (supabase-py executes each call as a separate HTTP round-trip); RPC is the only cross-table-transactional unit |
| Folder delete empty-check + delete | DB (PL/pgSQL RPC `delete_folder_if_empty`) | Backend service | Eliminates TOCTOU race; alternative app-side `SELECT count → DELETE` admits a concurrent upload between the two statements |
| Admin gate for `scope='global'` writes | Backend router (`Depends(get_admin_user)`) | DB RLS policies | Phase 1 RLS catalog (Migration 015) is the bedrock; router-level admin gate exists for clean 403 responses (RLS rejection surfaces as obscure PostgREST error if app doesn't pre-check) |
| Dedup at upload time | Backend service (`record_manager.determine_action`) | DB unique index | The unique index `documents_scope_user_path_filename_unique` (Migration 012) is the bedrock; the app-level pre-check exists to return a clean `action='skip'/'update'` response and to compare hashes (the index doesn't know about hash) |
| Storage upload (original blob) | Backend router (`files._upload_to_storage`) | Storage RLS (Migration 018) | Phase 2 LOCKED contract — Phase 3 must preserve `_upload_to_storage()` call ordering inside the upload handler when adding `folder_path`/`scope` query args |
| Concurrent upload safety | DB unique index (folders) + app strategy "no folders write on upload" | — | STATE.md Phase 1 decision: most folders exist by inference; uploads only set `documents.folder_path` and never insert into `folders`. The unique index is bedrock for the rare case where two `POST /api/folders` arrive simultaneously |

---

## Folder Rename RPC Design (FOLDER-03 + Pitfall 5)

### Why an RPC and not two app-level UPDATEs

supabase-py's PostgREST client executes each `.update().execute()` as a separate HTTP request to PostgREST, each in its own transaction. Two sequential calls **cannot** form a single transaction from the app side. If the second UPDATE fails (network blip, deadlock, server restart), `documents.folder_path` is rewritten but `folders.path` is not — partial state on disk, exactly what SC2 forbids.

The canonical solution: a single PL/pgSQL function called via `.rpc()` that wraps both UPDATEs in one transaction. PostgREST treats the RPC invocation as a single statement; the function body is implicitly transactional.

### Function signature

```sql
CREATE OR REPLACE FUNCTION public.rename_folder_prefix(
  p_old_prefix TEXT,           -- e.g., '/projects/q4'
  p_new_prefix TEXT,           -- e.g., '/projects/q4-archive'
  p_scope      TEXT,           -- 'user' or 'global'
  p_user_id    UUID DEFAULT NULL  -- NULL for scope='global', JWT-derived for scope='user'
)
RETURNS TABLE (documents_updated INT, folders_updated INT)
LANGUAGE plpgsql
SECURITY INVOKER  -- RLS still applies; admin gate enforced at router AND policies
AS $$
DECLARE
  v_doc_count    INT;
  v_folder_count INT;
BEGIN
  -- Validate canonical form (defense in depth alongside CHECK constraints)
  IF p_old_prefix !~ '^/$|^/[^/]+(/[^/]+)*$' THEN
    RAISE EXCEPTION 'old_prefix not canonical: %', p_old_prefix
      USING ERRCODE = 'check_violation';
  END IF;
  IF p_new_prefix !~ '^/$|^/[^/]+(/[^/]+)*$' THEN
    RAISE EXCEPTION 'new_prefix not canonical: %', p_new_prefix
      USING ERRCODE = 'check_violation';
  END IF;
  IF p_old_prefix = '/' THEN
    RAISE EXCEPTION 'cannot rename root path /'
      USING ERRCODE = 'check_violation';
  END IF;

  -- Update documents whose folder_path equals the prefix OR descends from it.
  -- The descend predicate uses '/' separator to avoid /projects matching /projectsX.
  UPDATE public.documents
     SET folder_path = p_new_prefix || substring(folder_path FROM length(p_old_prefix) + 1)
   WHERE scope = p_scope
     AND (p_user_id IS NULL OR user_id = p_user_id)
     AND (folder_path = p_old_prefix
          OR folder_path LIKE p_old_prefix || '/%');
  GET DIAGNOSTICS v_doc_count = ROW_COUNT;

  -- Update folders rows (the sparse side table) with the same prefix logic.
  UPDATE public.folders
     SET path = p_new_prefix || substring(path FROM length(p_old_prefix) + 1)
   WHERE scope = p_scope
     AND (p_user_id IS NULL OR user_id = p_user_id)
     AND (path = p_old_prefix
          OR path LIKE p_old_prefix || '/%');
  GET DIAGNOSTICS v_folder_count = ROW_COUNT;

  RETURN QUERY SELECT v_doc_count, v_folder_count;
END;
$$;

GRANT EXECUTE ON FUNCTION public.rename_folder_prefix(TEXT, TEXT, TEXT, UUID) TO authenticated;
```

**Naming convention:** `rename_folder_prefix` (snake_case, verb-first) matches the existing RPC convention — `match_document_chunks`, `match_document_chunks_hybrid` (Migration 008) — and is descriptive of the prefix-update semantics rather than the user-facing "rename" action.

**SECURITY mode:** `SECURITY INVOKER` (the default) is correct here — RLS policies on `documents` and `folders` apply normally so a non-admin attempting to rename a global folder hits the policy gate. The router-level `Depends(get_admin_user)` is the first line of defense; RLS is the second.

**Why `LIKE p_old_prefix || '/%'` and not `LIKE p_old_prefix || '%'`:** Without the trailing `/`, renaming `/projects` would also catch `/projectsX/foo` — a sibling folder that happens to share a prefix. The `/` separator is the canonical boundary and Phase 1's CHECK constraint guarantees the path regex respects it.

**Why `substring(folder_path FROM length(p_old_prefix) + 1)`:** Postgres `substring(s FROM n)` is 1-indexed and extracts from position n to end. For `folder_path = '/projects/q4/floor-plans'` and `p_old_prefix = '/projects/q4'`, length is 12, so we extract from position 13: `/floor-plans`. Concatenating with `p_new_prefix = '/projects/q4-archive'` yields `/projects/q4-archive/floor-plans`. For the case `folder_path = '/projects/q4'` (the prefix itself), we extract from position 13 of a 12-char string, which Postgres returns as `''`, yielding exactly `p_new_prefix`. Correct in both branches without an `IF`.

**Migration number:** **019** (Phase 1 ended at 016; Phase 2 used 018 for storage RLS; the next free slot is 019). [VERIFIED: `ls backend/migrations/*.sql` shows 012-016 + 018; 017 and 019 are open]. Note that 017 was reserved by Phase 1 carry-forward (per STATE.md "017.sql carry-forward is a documentation/migration-naming follow-up") and should not be reused for Phase 3 work.

**File name:** `backend/migrations/019_folder_rename_and_delete_rpcs.sql` — colocate both RPCs (rename + delete-if-empty) since they share the same PL/pgSQL idiom and are both Phase 3 deliverables. Mirrors Phase 1's bundling pattern (Migration 015 lands the full RLS catalog in one reviewable file).

### Mid-rename rollback test (SC2 + TEST-01)

PL/pgSQL functions are implicitly transactional — if the second UPDATE raises, the first UPDATE rolls back automatically. To prove this empirically, the test creates a deliberately-failing variant (the test code does NOT modify Migration 019's RPC; it creates a parallel test fixture):

```sql
-- For test only — NOT shipped in Migration 019.
CREATE OR REPLACE FUNCTION test_rename_folder_prefix_fails_midway(
  p_old_prefix TEXT, p_new_prefix TEXT, p_scope TEXT, p_user_id UUID
) RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
  UPDATE public.documents SET folder_path = p_new_prefix
   WHERE scope = p_scope AND (p_user_id IS NULL OR user_id = p_user_id)
     AND folder_path LIKE p_old_prefix || '%';
  RAISE EXCEPTION 'deliberate test failure mid-rename';
END;
$$;
```

The test then:
1. Inserts a document at `/test-rename/doc.txt` (tracked for cleanup)
2. Calls the failing RPC via `sb.rpc('test_rename_folder_prefix_fails_midway', ...)`
3. Asserts the call raised
4. Reads the document back and asserts `folder_path == '/test-rename'` (UNCHANGED)
5. Drops the test function

Recommend the test ALSO uses `pg_class` / `information_schema.routines` to assert the **production** `rename_folder_prefix` function exists and is `LANGUAGE plpgsql` — proves it runs in a transaction without exercising the rollback path on every test invocation.

---

## Concurrent-upload-no-orphan Strategy (Pitfall 10 + SC5)

### Two strategies considered

**Strategy A: Write `folders` row at upload time using `INSERT ... ON CONFLICT DO NOTHING`**

```python
# Inside upload_file() in files.py, after computing folder_path:
sb.table("folders").upsert(
    {"scope": scope, "user_id": user_id_or_none, "path": folder_path},
    on_conflict="scope,user_id,path",  # uses Migration 013's unique expression index
).execute()
```

Pros: makes folders show up in `GET /api/folders` immediately after the first upload into a brand-new path; no need to also insert via `POST /api/folders`.

Cons: every upload now does an extra DB write. Two-tab uploads to the same new path race: both INSERTs fire ON CONFLICT — only one wins; the other no-ops. Harmless **but** the unique index uses a `COALESCE` expression (`COALESCE(user_id, '00..0')`), and supabase-py's `.upsert(... on_conflict=...)` syntax targets named columns, not expression indexes. Workaround: use the `.rpc()` form or fall back to a try/except around `.insert()`.

**Strategy B: Don't write `folders` row at upload time at all (folder is implicit from `documents.folder_path`)**

Per Phase 1's STATE.md key decision (line 74): *"public.folders is a sparse side table for first-class empty-folder tracking — no FK from documents.folder_path to folders.path; most folders exist by inference from documents.folder_path, and rows in folders exist only for explicitly-empty folders"*.

Pros: zero race surface on upload (the upload path doesn't touch `folders` at all); cleaner separation (folders ⇒ "user explicitly created an empty folder"); fewer DB writes per upload.

Cons: `GET /api/folders?path=/projects/q4` must UNION two sources — `folders` rows AND `SELECT DISTINCT folder_path FROM documents WHERE folder_path LIKE '/projects/q4/%'`. Slightly more complex listing logic.

### Recommendation: Strategy B (LOCKED by Phase 1 STATE.md)

Phase 1 explicitly committed to Strategy B as part of the schema design. The unique index on `folders` is bedrock for the rare case where two `POST /api/folders` arrive simultaneously creating the same explicitly-empty folder, NOT for the upload path. The phrase "*or zero*" in SC5 (`10 parallel uploads to a brand-new path produces exactly one (or zero) folders row`) is the smoking gun — Strategy B produces **zero** folders rows from uploads alone (the SC tolerates both outcomes only because Strategy B is the locked answer).

**Implications for Phase 3:**

- `POST /api/files/upload?folder_path=/x/y&scope=user` ONLY inserts into `documents`. Does NOT touch `folders`.
- `POST /api/folders` (the explicit-create endpoint) writes ONE row into `folders` using `INSERT ... ON CONFLICT DO NOTHING` (or PostgREST equivalent — see below).
- `GET /api/folders` returns the UNION: explicitly-tracked rows (from `folders`) + inferred-from-documents (DISTINCT `folder_path` where it doesn't appear in `folders`). Listing is more complex; uploads stay simple. Net win.
- The concurrent-upload-no-orphan test then asserts that 10 parallel uploads to `/test-race-path` produce **zero** folders rows (the test is a stronger version of SC5: tightens "or zero" to "exactly zero").

### supabase-py `INSERT ... ON CONFLICT DO NOTHING` syntax

The PostgREST behind supabase-py exposes ON CONFLICT via `.upsert()`. For the unique expression index `(scope, COALESCE(user_id, '00..0'), path)`, the cleanest reliable pattern is **try/except on `.insert()`**:

```python
try:
    sb.table("folders").insert({
        "scope": scope, "user_id": user_id_or_none, "path": folder_path,
    }).execute()
    return {"created": True}
except Exception as e:
    # Postgres unique violation maps to PostgREST 409 / 23505.
    if "duplicate" in str(e).lower() or "23505" in str(e):
        return {"created": False, "reason": "already_exists"}
    raise
```

Alternative (cleaner): expose a small RPC `create_folder_if_not_exists(p_scope TEXT, p_user_id UUID, p_path TEXT)` that does the `INSERT ... ON CONFLICT DO NOTHING` server-side. Since we are already shipping Migration 019 with two RPCs, adding a third is essentially free. Recommend doing this (cleaner error handling, atomic on the DB side).

---

## Dedup Key Extension (FOLDER-05)

### Current state

`backend/app/services/record_manager.py:42-48` queries:

```python
result = supabase_client.table("documents") \
    .select("id, content_hash, status") \
    .eq("user_id", user_id) \
    .eq("file_name", file_name) \
    .maybe_single() \
    .execute()
```

### Required change

Extend the SELECT to include `scope` and `folder_path`:

```python
def determine_action(
    file_hash: str,
    file_name: str,
    user_id: str,
    supabase_client,
    scope: str = "user",          # NEW — defaults preserve Phase 1/2 behavior
    folder_path: str = "/",       # NEW — defaults preserve Phase 1/2 behavior
) -> RecordAction:
    """
    Check if this file has been ingested before.

    Logic:
    1. Look for existing doc with same (scope, user_id, folder_path, file_name)
    2. If found and same hash → skip (identical content)
    3. If found and different hash → update (content changed)
    4. If not found → create (new file)

    NOTE: For scope='global', user_id is None and we use COALESCE-equivalent
    semantics — same as the unique index documents_scope_user_path_filename_unique.
    """
    try:
        query = supabase_client.table("documents") \
            .select("id, content_hash, status") \
            .eq("scope", scope) \
            .eq("folder_path", folder_path) \
            .eq("file_name", file_name)
        if scope == "user":
            query = query.eq("user_id", user_id)
        else:
            query = query.is_("user_id", "null")  # global rows have user_id IS NULL
        result = query.maybe_single().execute()
    except Exception:
        return RecordAction(action="create", message="New document")
    ...  # rest unchanged
```

### App-only or new index?

**App-only.** Phase 1's Migration 012 already added the scope-aware unique index:

```sql
CREATE UNIQUE INDEX documents_scope_user_path_filename_unique
  ON documents (
    scope,
    COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid),
    folder_path,
    file_name
  );
```

The dedup query's `eq` filters cover the same column list (in the same order) so Postgres can use this index for the lookup. Verified by reading `backend/migrations/012_folder_path_and_scope.sql:51-57`.

### Caller updates

`backend/app/routers/files.py:73` currently calls:

```python
record_action = determine_action(file_hash, file_name, user_id, supabase)
```

Phase 3 changes the call to pass `scope` and `folder_path` (extracted from query args):

```python
record_action = determine_action(
    file_hash, file_name, user_id, supabase,
    scope=scope, folder_path=normalize_path(folder_path or "/"),
)
```

---

## Folder Delete Implementation (FOLDER-04 + Pitfall 5)

### Two implementations considered

**App-side:** `SELECT COUNT(*)` against documents matching the path; if 0, `DELETE FROM folders WHERE id=$1`.

Risk: TOCTOU race. Between the SELECT and the DELETE, another request can `INSERT INTO documents ... folder_path = $deleted_path` — the document is now orphaned at insert time. PostgREST runs each `.execute()` as a separate transaction; there is no app-level way to hold a row lock across two HTTP round-trips.

**DB function:** Single PL/pgSQL `delete_folder_if_empty(p_folder_id, p_scope, p_user_id)` that does the count + delete + raise-on-non-empty inside one transaction.

Pros: race window eliminated; structured error embedded in the function (uses `RAISE EXCEPTION` with custom data via `USING`); RLS still applies via SECURITY INVOKER.

Cons: requires a migration. (We're already shipping Migration 019, so cost is one extra function definition.)

### Recommendation: DB function

Colocate with the rename RPC in Migration 019. Function shape:

```sql
CREATE OR REPLACE FUNCTION public.delete_folder_if_empty(
  p_folder_id UUID
)
RETURNS TABLE (deleted BOOLEAN, document_count INT, subfolder_count INT)
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
DECLARE
  v_path           TEXT;
  v_scope          TEXT;
  v_user_id        UUID;
  v_doc_count      INT;
  v_subfolder_count INT;
BEGIN
  -- Lock the folders row to block concurrent renames (sub-second; harmless)
  SELECT path, scope, user_id INTO v_path, v_scope, v_user_id
    FROM public.folders
   WHERE id = p_folder_id
   FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'folder not found: %', p_folder_id
      USING ERRCODE = 'no_data_found';
  END IF;

  -- Count documents at-or-under this path (RLS applies via SECURITY INVOKER)
  SELECT COUNT(*) INTO v_doc_count
    FROM public.documents
   WHERE scope = v_scope
     AND (v_user_id IS NULL OR user_id = v_user_id)
     AND (folder_path = v_path OR folder_path LIKE v_path || '/%');

  -- Count strict-descendant folders rows
  SELECT COUNT(*) INTO v_subfolder_count
    FROM public.folders
   WHERE scope = v_scope
     AND (v_user_id IS NULL OR user_id = v_user_id)
     AND path LIKE v_path || '/%';

  IF v_doc_count > 0 OR v_subfolder_count > 0 THEN
    -- Return without deleting; router maps this to {error: 'FOLDER_NOT_EMPTY', ...}
    RETURN QUERY SELECT FALSE, v_doc_count, v_subfolder_count;
    RETURN;
  END IF;

  DELETE FROM public.folders WHERE id = p_folder_id;
  RETURN QUERY SELECT TRUE, 0, 0;
END;
$$;

GRANT EXECUTE ON FUNCTION public.delete_folder_if_empty(UUID) TO authenticated;
```

The router consumes the result row:

```python
# backend/app/routers/folders.py
result = sb.rpc("delete_folder_if_empty", {"p_folder_id": folder_id}).execute()
row = result.data[0] if result.data else None
if row is None:
    raise HTTPException(status_code=404, detail="Folder not found")
if not row["deleted"]:
    return JSONResponse(
        status_code=409,
        content={
            "error": "FOLDER_NOT_EMPTY",
            "document_count": row["document_count"],
            "subfolder_count": row["subfolder_count"],
        },
    )
return {"status": "deleted"}
```

**409 vs 400:** Use 409 Conflict for "folder not empty" — semantically correct (the request conflicts with the current state of the resource); aligns with HTTP standards. A 400 implies the request is malformed; this request is well-formed but the state forbids it.

---

## Path-Prefix Matching Predicates (exact SQL)

These predicates appear in Migration 019's RPCs and in `folder_service.list_folder()`:

| Operation | Predicate | Why |
|-----------|-----------|-----|
| **Is folder X non-empty?** (delete check) | `folder_path = '/X' OR folder_path LIKE '/X/%'` | Includes the folder itself AND every strict descendant. Without the descendant clause, a folder containing only nested subfolder-with-docs would falsely report empty |
| **Rename: documents matching prefix /X** | `folder_path = '/X' OR folder_path LIKE '/X/%'` | Same shape as above. Prefix without `/` (`LIKE '/X%'`) would catch sibling `/Xperiment` — bug |
| **Rename: folders matching prefix /X** | `path = '/X' OR path LIKE '/X/%'` | Same shape on the side table |
| **List documents at folder /X (one level)** | `folder_path = '/X'` (NOT a prefix match) | `list_folder` returns docs at exactly /X; subfolders are listed by aggregating distinct `folder_path` values matching `LIKE '/X/%'` and stripping the path after the next `/` |
| **List folders at /X (one level)** | `path = '/X'` for the row representing /X itself; subfolders identified via `path LIKE '/X/%' AND path NOT LIKE '/X/%/%'` (immediate children only) | The "no nested grandchildren" pattern is the canonical SQL idiom for one-level-down folder listing |

**Why `LIKE` and not regex:** the `text_pattern_ops` btree index (Migration 016) accelerates `LIKE 'prefix%'` patterns specifically. Regex-based matches (`~`, `~*`) would fall back to Seq Scan or use the GIN trigram index (Migration 016 #4) which is slower for prefix queries.

**Why NOT `path STARTS WITH '/X'`:** Postgres does not have a `STARTS WITH` operator. `LIKE 'prefix%'` is the canonical form.

---

## Folder Service API Surface (FOLDER-02)

Five new functions in `backend/app/services/folder_service.py`, all PURE service-layer (no FastAPI imports — keeps the module unit-testable). All take an injected `supabase_client` (matches `record_manager.determine_action()` style).

```python
# Pseudocode for the planner — exact signatures and bodies belong in PLAN.md.

def list_folder(
    path: str,
    scope: str,           # 'user' | 'global' | 'both'
    user_id: str | None,  # None when scope='global'
    supabase_client,
) -> dict:
    """Return {documents: [...], subfolders: [...]} for path at one level deep.
    Aggregates documents at folder_path == path, plus distinct one-level-down
    subfolder names from documents.folder_path LIKE path||'/%' that don't
    appear in folders, plus folders rows where path matches the immediate
    children predicate."""

def create_folder(
    path: str,
    scope: str,           # 'user' | 'global'
    user_id: str | None,
    supabase_client,
) -> dict:
    """Insert into folders with ON CONFLICT DO NOTHING semantics.
    Returns {id, scope, user_id, path, created_at, action: 'created'|'exists'}.
    Caller (router) is responsible for normalize_path() and admin gate."""

def move_document(
    document_id: str,
    new_folder_path: str,
    user_id: str,         # JWT-derived; never trust client-passed user_id
    supabase_client,
) -> dict:
    """UPDATE documents SET folder_path = new_folder_path WHERE id = document_id.
    Caller already enforced scope-immutability; this function ONLY moves within scope.
    Returns the updated document row."""

def rename_folder(
    old_path: str,
    new_path: str,
    scope: str,
    user_id: str | None,
    supabase_client,
) -> dict:
    """Calls Migration 019's rename_folder_prefix RPC. Returns
    {documents_updated: N, folders_updated: M}."""

def delete_folder(
    folder_id: str,
    supabase_client,
) -> dict:
    """Calls Migration 019's delete_folder_if_empty RPC. Returns either
    {deleted: True} or {deleted: False, error: 'FOLDER_NOT_EMPTY',
    document_count: N, subfolder_count: M}."""
```

**Path normalization:** every function above runs its `path`/`old_path`/`new_path`/`new_folder_path` argument through `normalize_path()` AS THE FIRST STATEMENT. This is Pitfall 4 enforcement — the service layer is the chokepoint, not the router. Belt + suspenders: the router ALSO normalizes user input (so a malformed value gets a clean 400 instead of a 500 from a service-layer ValueError). The DB CHECK constraint is the third layer.

---

## Folders Router Design (FOLDER-06)

New file `backend/app/routers/folders.py`. Mirror the existing `threads.py` shape (auth dep, supabase client, four CRUD endpoints). Register in `main.py:8` import line and `:23` `include_router` call.

```python
# backend/app/routers/folders.py — pseudocode for planner

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from app.auth import get_current_user, get_admin_user, get_supabase_client
from app.models.schemas import FolderResponse, FolderCreate, FolderPatch
from app.services.folder_service import (
    normalize_path, list_folder, create_folder,
    rename_folder, delete_folder,
)

router = APIRouter(prefix="/api/folders", tags=["folders"])


@router.get("")
async def list_folders(
    path: str = Query("/", description="Folder path to list"),
    scope: str = Query("both", regex="^(user|global|both)$"),
    user_id: str = Depends(get_current_user),
):
    sb = get_supabase_client()
    norm = normalize_path(path)
    return list_folder(norm, scope, user_id, sb)


@router.post("", response_model=FolderResponse)
async def create_folder_endpoint(
    body: FolderCreate,
    user_id: str = Depends(get_current_user),
):
    sb = get_supabase_client()
    norm = normalize_path(body.path)
    if body.scope == "global":
        # Re-check via get_admin_user dependency in a separate "admin" route is
        # ergonomically awkward; a simpler approach: call get_admin_user(user_id)
        # explicitly inside the body when scope=='global'. See implementation
        # note in §"Admin gate two-shape" below.
        from app.auth import get_user_profile
        profile = get_user_profile(user_id)
        if not profile or not profile.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin required for global scope")
        return create_folder(norm, "global", None, sb)
    return create_folder(norm, "user", user_id, sb)


@router.patch("/{folder_id}", response_model=FolderResponse)
async def rename_folder_endpoint(
    folder_id: str,
    body: FolderPatch,    # has new_path: str
    user_id: str = Depends(get_current_user),
):
    sb = get_supabase_client()
    # Look up the existing folder to determine scope (admin check below)
    existing = sb.table("folders").select("*").eq("id", folder_id).maybe_single().execute()
    if not existing or not existing.data:
        raise HTTPException(status_code=404, detail="Folder not found")
    folder = existing.data
    if folder["scope"] == "global":
        from app.auth import get_user_profile
        profile = get_user_profile(user_id)
        if not profile or not profile.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin required")
    new_path_norm = normalize_path(body.new_path)
    result = rename_folder(folder["path"], new_path_norm, folder["scope"],
                           folder.get("user_id"), sb)
    return {**folder, "path": new_path_norm, **result}


@router.delete("/{folder_id}")
async def delete_folder_endpoint(
    folder_id: str,
    user_id: str = Depends(get_current_user),
):
    sb = get_supabase_client()
    existing = sb.table("folders").select("*").eq("id", folder_id).maybe_single().execute()
    if not existing or not existing.data:
        raise HTTPException(status_code=404, detail="Folder not found")
    folder = existing.data
    if folder["scope"] == "global":
        from app.auth import get_user_profile
        profile = get_user_profile(user_id)
        if not profile or not profile.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin required")
    result = delete_folder(folder_id, sb)
    if not result.get("deleted"):
        return JSONResponse(status_code=409, content={
            "error": "FOLDER_NOT_EMPTY",
            "document_count": result.get("document_count", 0),
            "subfolder_count": result.get("subfolder_count", 0),
        })
    return {"status": "deleted"}
```

### Admin gate two-shape

The admin gate has two shapes available:
1. `Depends(get_admin_user)` — clean, but applies to the whole endpoint regardless of body
2. Inline `get_user_profile(user_id)` + `is_admin` check — needed when the gate depends on the request body (e.g., POST with `scope='global'` is admin; with `scope='user'` is any user)

Phase 3 needs **both shapes**. Use:
- `Depends(get_admin_user)` for endpoints that are unconditionally admin (none in the folders router itself, but any future "global folder reorganize" endpoint would use this).
- Inline check for `POST /api/folders` (admin only when `body.scope == 'global'`).
- Inline check for `PATCH/DELETE /api/folders/{id}` (admin only when the existing folder has `scope == 'global'`).

The inline pattern is essentially what `auth.py:get_admin_user` does internally — call `get_user_profile()`, check `is_admin`. Mirror it inline rather than restructuring the dependency.

---

## Files Router Extensions (FOLDER-07)

Two changes in `backend/app/routers/files.py`:

### 1. Upload accepts `folder_path` and `scope` query args

```python
# Existing signature at files.py:60-65:
@router.post("/upload", response_model=DocumentResponse)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user),
):

# New signature:
@router.post("/upload", response_model=DocumentResponse)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    folder_path: str = Query("/", description="Canonical folder path"),
    scope: str = Query("user", regex="^(user|global)$"),
    user_id: str = Depends(get_current_user),
):
    # ... at top of body:
    folder_path = normalize_path(folder_path)
    if scope == "global":
        profile = get_user_profile(user_id)
        if not profile or not profile.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin required for global scope")
        effective_user_id = None  # global rows have user_id IS NULL per coupling CHECK
    else:
        effective_user_id = user_id

    # determine_action gets the new args:
    record_action = determine_action(
        file_hash, file_name, user_id, supabase,
        scope=scope, folder_path=folder_path,
    )
    # ... rest of handler:
    # documents.insert() must include scope and folder_path:
    #   {"user_id": effective_user_id, "scope": scope, "folder_path": folder_path, ...}
```

**Important:** the document row's `user_id` is `NULL` when `scope='global'` (per Migration 012's coupling CHECK). The Storage upload path `_upload_to_storage()` uses `user_id` to compute the folder; for global uploads, use a sentinel `'global'` instead of None to keep paths well-formed (e.g., `documents/global/{doc_id}.pdf`). Migration 018's RLS predicate is `auth.uid()::text = (storage.foldername(name))[1]`, so global blobs are unreadable by non-admin users via Storage — admins must use the service-role key (already the convention). The Phase 3 plan should explicitly capture this Storage-path-for-global decision.

### 2. New PATCH endpoint for rename + folder move

```python
@router.patch("/{file_id}", response_model=DocumentResponse)
async def patch_file(
    file_id: str,
    body: FilePatch,  # NEW Pydantic model — file_name?: str, folder_path?: str
    user_id: str = Depends(get_current_user),
):
    sb = get_supabase_client()
    doc = sb.table("documents").select("*").eq("id", file_id).maybe_single().execute()
    if not doc or not doc.data:
        raise HTTPException(status_code=404, detail="Document not found")
    existing = doc.data

    # Admin gate for global-scope writes
    if existing["scope"] == "global":
        profile = get_user_profile(user_id)
        if not profile or not profile.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin required")

    # CRITICAL: scope is IMMUTABLE — even if the body somehow contains scope, reject.
    # Migration 015's forbid_scope_mutation trigger is the bedrock; this is defense
    # in depth. body model deliberately does not contain a scope field.
    update_data = {}
    if body.file_name is not None:
        update_data["file_name"] = body.file_name
    if body.folder_path is not None:
        update_data["folder_path"] = normalize_path(body.folder_path)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    sb.table("documents").update(update_data).eq("id", file_id).execute()
    return sb.table("documents").select("*").eq("id", file_id).single().execute().data
```

**Why is FilePatch limited to `file_name` and `folder_path`:** these are the only mutable user-facing fields. `scope` is immutable (Migration 015 trigger). `user_id` is JWT-derived. `content_hash`/`mime_type`/`file_size`/`status` are managed by ingestion. `content_markdown`/`content_markdown_status` are managed by Phase 2.

**Why no `move-and-rename-in-one-call` complication:** the body is two optional fields; the handler updates whichever are present. Single PATCH covers both rename and move (and both at once if desired) without a more complex API.

---

## Files to Create / Modify (concrete paths for pattern-mapper)

### Create

| Path | Purpose | Approximate size |
|------|---------|------------------|
| `backend/migrations/019_folder_rename_and_delete_rpcs.sql` | `rename_folder_prefix` + `delete_folder_if_empty` + (optional) `create_folder_if_not_exists` PL/pgSQL functions | ~120 lines |
| `backend/app/routers/folders.py` | New folders CRUD router (GET/POST/PATCH/DELETE) | ~120 lines |
| `backend/scripts/test_folders.py` | Integration tests covering FOLDER-02..07 + concurrent-upload-no-orphan + mid-rename rollback | ~400 lines |

### Modify

| Path | Change | Lines affected |
|------|--------|----------------|
| `backend/app/services/folder_service.py` | Add 5 functions (`list_folder`, `create_folder`, `move_document`, `rename_folder`, `delete_folder`); preserve existing `normalize_path()` unchanged | +~150 lines |
| `backend/app/services/record_manager.py` | Extend `determine_action()` signature with `scope='user'` and `folder_path='/'` defaults; add `.eq('scope', ...).eq('folder_path', ...)` filters and the `.is_('user_id', 'null')` branch for `scope='global'` | ~10 lines net |
| `backend/app/routers/files.py` | Add `folder_path` + `scope` Query args to `upload_file()`; pass them to `determine_action()`; insert into `documents` with the new columns; new `patch_file` endpoint | +~50 lines |
| `backend/app/main.py` | Add `from app.routers import ... folders`; add `app.include_router(folders.router)` | +2 lines |
| `backend/app/models/schemas.py` | Add `FolderResponse`, `FolderCreate`, `FolderPatch`, `FilePatch` Pydantic models; extend `DocumentResponse` with `folder_path: str = '/'` and `scope: str = 'user'` | +~30 lines |
| `backend/scripts/test_all.py` | Register `import test_folders` and append `("Folders", test_folders)` to SUITES list (15th suite) | +2 lines |

### NOT modified (Phase 3 must NOT touch these — locked Phase 1/2 contracts)

- `backend/app/services/folder_service.py:normalize_path()` — unchanged signature and body
- `backend/migrations/012_*.sql` through `018_*.sql` — locked
- `backend/app/services/ingestion.py` — Phase 2 LOCKED contract; Phase 3 only changes how `documents` rows are CREATED (in files.py) — ingestion logic is untouched
- `backend/app/auth.py:get_admin_user` — reused unchanged
- `backend/app/services/record_manager.py:compute_file_hash` and `compute_chunk_hash` — unchanged

---

## Pitfall 4 / 5 / 10 Mitigations (mapped to specific code changes)

### Pitfall 4: Path normalization drift

**Where it could happen in Phase 3:** any router endpoint that accepts a path argument (folders router create/patch, files router upload/patch).

**Mitigation:**
1. Every router endpoint runs `folder_path` / `path` / `new_path` through `normalize_path()` BEFORE passing to the service layer. Belt.
2. Every service-layer function ALSO runs `normalize_path()` as its first statement. Suspenders.
3. The DB CHECK constraint (Migration 012) is the third layer — rejects malformed values that somehow got past 1+2.
4. The `rename_folder_prefix` RPC (Migration 019) does its own canonical-form regex check on `p_old_prefix` and `p_new_prefix` (defense in depth at the DB function layer).

**Test:** `test_folders.py` includes assertions that `POST /api/folders {path: 'projects/'}` (trailing slash) is rejected (or auto-normalized — depends on whether the planner picks "reject" or "normalize-and-accept"; recommend "normalize-and-accept" for ergonomics, with the DB CHECK as the failsafe).

### Pitfall 5: Folder deletion orphans / cascade

**Where it could happen in Phase 3:** the `DELETE /api/folders/{id}` endpoint.

**Mitigation:**
1. **No `ON DELETE CASCADE`** anywhere — the `folders` table has no FK from `documents.folder_path` (per Phase 1 ARCHITECTURE.md Pattern 2). Even if a developer adds a CASCADE later, the Phase 3 design doesn't depend on it.
2. **Empty-only delete** enforced by the `delete_folder_if_empty` RPC (Migration 019). Returns structured `{deleted: false, document_count: N, subfolder_count: M}` instead of doing the delete.
3. **Single-transaction check-and-delete** eliminates the TOCTOU race — `FOR UPDATE` on the folders row blocks concurrent renames; the count + delete happen in one PL/pgSQL block.
4. The empty-check predicate is `folder_path = $path OR folder_path LIKE $path || '/%'` — catches both same-folder and descendant docs.

**Test:** `test_folders.py` asserts that `DELETE /api/folders/{id}` on a non-empty folder returns 409 with the structured body, and that the folder still exists, and that the document at the path still exists. A concurrent-upload-during-delete test would be ideal but is hard to time deterministically; recommend the static "is the function transactional" assertion via `pg_class` instead.

### Pitfall 10: Concurrent upload race

**Where it could happen in Phase 3:** two parallel `POST /api/files/upload?folder_path=/new-path` requests.

**Mitigation (locked by Strategy B):**
1. The upload path does NOT touch `folders` — it only inserts into `documents`. The unique index on `documents` (`documents_scope_user_path_filename_unique` from Migration 012) handles dedup of identical-named files at the same path; for distinct file names, both inserts succeed. No race on `folders` because nothing is inserted there.
2. The unique index on `folders` (Migration 013) is bedrock for the rare `POST /api/folders` race — two concurrent explicit-create calls for the same path produce exactly one row (the second hits the unique constraint). The router catches the unique violation and returns "already exists" (200 idempotent OR 409, planner picks).
3. Optional: ship the `create_folder_if_not_exists` RPC in Migration 019 to do the `INSERT ... ON CONFLICT DO NOTHING` server-side, sidestepping the supabase-py exception handling for the unique violation.

**Test:** `test_folders.py` runs 10 parallel `POST /api/files/upload?folder_path=/test-race-{uuid}` calls via `concurrent.futures.ThreadPoolExecutor(max_workers=10)`, then queries `SELECT COUNT(*) FROM folders WHERE scope='user' AND user_id=$u AND path='/test-race-{uuid}'`, asserts result is **0** (Strategy B: uploads never write folders rows).

---

## Code Examples

### Calling the rename RPC from Python (supabase-py)

```python
# In backend/app/services/folder_service.py
def rename_folder(old_path, new_path, scope, user_id, supabase_client):
    old_norm = normalize_path(old_path)
    new_norm = normalize_path(new_path)
    if old_norm == "/" or new_norm == "/":
        raise ValueError("cannot rename root path")
    result = supabase_client.rpc("rename_folder_prefix", {
        "p_old_prefix": old_norm,
        "p_new_prefix": new_norm,
        "p_scope": scope,
        "p_user_id": user_id,  # None for scope='global'
    }).execute()
    if not result.data:
        return {"documents_updated": 0, "folders_updated": 0}
    row = result.data[0]
    return {
        "documents_updated": row["documents_updated"],
        "folders_updated": row["folders_updated"],
    }
```

### Calling the delete RPC

```python
def delete_folder(folder_id, supabase_client):
    result = supabase_client.rpc("delete_folder_if_empty", {
        "p_folder_id": folder_id,
    }).execute()
    if not result.data:
        return {"deleted": False, "document_count": 0, "subfolder_count": 0}
    row = result.data[0]
    return {
        "deleted": row["deleted"],
        "document_count": row["document_count"],
        "subfolder_count": row["subfolder_count"],
    }
```

### Concurrent-upload-no-orphan test fixture

```python
# In backend/scripts/test_folders.py
import concurrent.futures
import uuid
import requests

def test_concurrent_upload_no_orphan(token, headers):
    h.section("Pitfall 10 — concurrent upload no-orphan")
    test_path = f"/test-race-{uuid.uuid4().hex[:8]}"
    file_bytes = b"race test content"

    def _upload(idx):
        return requests.post(
            f"{h.BASE_URL}/api/files/upload",
            headers={"Authorization": f"Bearer {token}"},
            params={"folder_path": test_path, "scope": "user"},
            files={"file": (f"race-{idx}.txt", file_bytes, "text/plain")},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(_upload, range(10)))

    success_count = sum(1 for r in results if r.status_code == 200)
    h.test("All 10 parallel uploads return 200", success_count == 10,
           f"got {success_count} successes")

    # Verify folders table did NOT acquire a row at test_path (Strategy B)
    sb_admin = h.get_user_supabase_client(h.get_admin_token())
    folders_check = sb_admin.table("folders").select("id").eq("path", test_path).execute()
    h.test("Strategy B: folders table has 0 rows at brand-new upload path",
           len(folders_check.data) == 0,
           f"got {len(folders_check.data)} folder rows")

    # Cleanup tracked documents (per CLAUDE.md never-delete-all rule)
    for r in results:
        if r.status_code == 200:
            doc_id = r.json().get("id")
            if doc_id:
                requests.delete(f"{h.BASE_URL}/api/files/{doc_id}", headers=headers)
```

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Cross-table transaction (folder rename) | Two sequential supabase-py UPDATEs | Single PL/pgSQL RPC `rename_folder_prefix` | PostgREST executes each `.execute()` in its own transaction; only RPCs span multiple statements atomically |
| Empty-folder check + delete | `SELECT count → if 0 → DELETE` from app code | PL/pgSQL RPC `delete_folder_if_empty` with `FOR UPDATE` lock | TOCTOU race: a concurrent INSERT between SELECT and DELETE orphans a doc |
| Path canonicalization | New regex / string ops in router | Existing `normalize_path()` from Phase 1 | Already the locked chokepoint; reusing prevents drift |
| Admin gate | New decorator / middleware | Existing `Depends(get_admin_user)` from `backend/app/auth.py:43` | Already shipped in Episode 1; conditional admin gate (when scope='global') uses inline `get_user_profile()` mirror |
| Dedup uniqueness | App-level "is this file already here" SELECT followed by INSERT | `documents_scope_user_path_filename_unique` index (Migration 012) + `determine_action()` extension | DB index is the bedrock; app pre-check returns clean `action='skip'/'update'` and compares hash |
| Concurrent-create race on `folders` | Application-level lock or mutex | Migration 013's unique expression index `(scope, COALESCE(user_id,'00..0'), path)` + `INSERT ... ON CONFLICT DO NOTHING` (or RPC wrapper) | DB unique constraint is bedrock; ON CONFLICT is the canonical idiom |
| Scope mutation prevention | Application-level if-checks | Migration 015 `forbid_scope_mutation` trigger + router-level explicit field rejection | Trigger is the DB-level guard; router rejection produces clean 400 instead of opaque 500 |
| Path regex on prefix queries | Custom string-prefix matching in app code | `LIKE 'prefix/%'` against `documents_folder_path_prefix_idx` btree (Migration 016) | Phase 1 already added the `text_pattern_ops` btree specifically for this |

**Key insight:** Every "concurrency-safe" or "transactional" requirement in Phase 3 has a Postgres primitive that solves it cleanly. The DB is the bedrock; the app is the ergonomics layer. Don't invert that.

---

## Common Pitfalls

### Pitfall A: Forgetting that `scope='global'` rows have `user_id IS NULL`

**What goes wrong:** `determine_action()` does `.eq('user_id', user_id)` for both scopes — fails to find the existing global doc because `'00..0' != NULL`. Returns `action='create'` and the INSERT then fails on the unique index.

**How to avoid:** branch the query — `scope='user'` uses `.eq('user_id', user_id)`; `scope='global'` uses `.is_('user_id', 'null')`. The unique index uses `COALESCE(user_id, '00..0')` to make NULLs compare equal, but the SELECT must explicitly match the column's NULL state.

### Pitfall B: Updating a document's scope via PATCH /api/files/{id}

**What goes wrong:** even if the router accepts a `scope` field in the body, Migration 015's `forbid_scope_mutation` trigger raises `check_violation` — the user sees an opaque 500.

**How to avoid:** the `FilePatch` Pydantic model deliberately does NOT include a `scope` field. If the request body contains `scope`, FastAPI ignores it (Pydantic model_dump excludes unknown fields). Router asserts `update_data` does not contain `'scope'` after build; safety net.

### Pitfall C: Using `path = '/X' OR path LIKE '/X%'` instead of `... LIKE '/X/%'`

**What goes wrong:** `'/X%'` matches sibling folders like `/Xperiment` — rename or empty-check spans unintended paths. Subtle; only triggers when folder names share a prefix.

**How to avoid:** ALWAYS use `path LIKE prefix || '/%'` for descendant matches. The `/` separator is the canonical boundary. Only the standalone-prefix check uses `path = prefix` (no `LIKE`).

### Pitfall D: Returning 500 instead of structured 409 on FOLDER_NOT_EMPTY

**What goes wrong:** if the router doesn't unpack the RPC result and naïvely returns `result.data`, the response body is `[{"deleted": false, "document_count": 12, ...}]` with status 200 — frontend can't tell the difference between success and failure.

**How to avoid:** router explicitly checks `row['deleted']` and returns `JSONResponse(status_code=409, content={...})` for the not-empty case. The structured body matches SC3's contract and the Phase 6 UI's "show actual count" expectation.

### Pitfall E: Forgetting to register the new router in `main.py`

**What goes wrong:** the `folders.py` router is built and tested in isolation but `POST /api/folders` returns 404 because `main.py` never includes it. Test suite passes (assuming the test mocks the router) — production breaks.

**How to avoid:** the plan checklist includes BOTH (a) creating `folders.py` AND (b) editing `main.py:8` import + `:23` `include_router`. Test suite hits real HTTP endpoints (mirrors Episode 1 `test_threads.py` shape) so a missing router registration produces a 404 in the suite.

### Pitfall F: Storage path `documents/{user_id}/{doc_id}{ext}` for global uploads

**What goes wrong:** `user_id` is `None` for global rows; Python f-string produces `documents/None/{doc_id}.pdf` — Storage RLS rejects it because `(storage.foldername(name))[1]` is `'None'`, which does not equal any `auth.uid()::text`.

**How to avoid:** the upload handler computes `storage_user_segment = user_id if scope == 'user' else 'global'`. The storage path is `f"{storage_user_segment}/{doc_id}{ext}"`. Migration 018's RLS policy already excludes 'global' segment for the authenticated role; service-role bypasses RLS so backend reads/writes still work. Document this decision in the plan.

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Single dedup key `(user_id, file_name)` (Migration 006) | `(scope, COALESCE(user_id,'00..0'), folder_path, file_name)` (Migration 012) | Phase 1 (2026-05-03) | Same file in two folders is no longer a duplicate; same file in same folder still deduped |
| Single-axis RLS `user_id = auth.uid()` | Two-axis RLS with separate INSERT/UPDATE per scope + scope-mutation trigger | Phase 1 (Migration 015) | Phase 3 router admin gate is defense in depth, not the primary security control |
| Folders implicit-only (Episode 1, no `folders` table) | Sparse `folders` side table for explicit empty folders only | Phase 1 (Migration 013) | Phase 3 upload path must NOT write to `folders`; only `POST /api/folders` writes |
| Schema `(SELECT * from documents)` returns documents only | Now must UNION with `folders` rows in folder listing | Phase 3 (this phase) | `list_folder()` is more complex than a single table read |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Migration 017 slot is "reserved" by Phase 1 carry-forward; Migration 019 is the next free slot for Phase 3 RPCs | §Folder Rename RPC Design | Low — if 017 turns out to be claimed by Phase 3, simply renumber (PostgreSQL function names are unaffected). [VERIFIED: `ls backend/migrations/*.sql` confirms 012-016, 018 exist; 017 and 019 absent] |
| A2 | supabase-py raises a Python `Exception` (not `APIError`) when an INSERT hits a unique constraint violation | §Concurrent-upload-no-orphan Strategy | Low — the try/except idiom catches Exception; the SQLSTATE / message check (`'23505' in str(e)`) is the discriminator. Recommend the planner verifies the actual exception class supabase-py raises during planning |
| A3 | `concurrent.futures.ThreadPoolExecutor` with the existing test infrastructure correctly multiplexes `requests.post` calls without sharing connection state | §Validation Architecture | Low — `requests` is thread-safe; each Session call holds its own response state. Phase 1 patterns don't yet exercise concurrent calls; Phase 3 introduces this pattern |
| A4 | The `delete_folder_if_empty` RPC's `FOR UPDATE` lock on the `folders` row does NOT block reads (only other UPDATE/DELETE) | §Folder Delete Implementation | Low — Postgres `FOR UPDATE` is row-level write lock; concurrent SELECTs are unaffected. Standard MVCC behavior |
| A5 | Storage RLS (Migration 018) uses `(storage.foldername(name))[1]` which extracts the FIRST path segment | §Pitfall F | [VERIFIED: `backend/migrations/018_storage_rls.sql:38` and `:52` confirm the predicate `(SELECT auth.uid())::text = (storage.foldername(name))[1]`] |
| A6 | Phase 1's STATE.md key decision (line 74) — "rows in folders exist only for explicitly-empty folders" — is binding for Phase 3 | §Concurrent-upload-no-orphan Strategy | Medium — if the planner or user revisits this decision and chooses Strategy A (write on upload), the upload handler complexity and the concurrent-upload-no-orphan test design both change. Recommend the planner confirm Strategy B in the plan-discuss phase before locking |
| A7 | The `409 Conflict` HTTP status is the right choice for FOLDER_NOT_EMPTY (vs. 400) | §Folder Delete Implementation | Low — both are valid HTTP semantics; 409 is more precise. Phase 6 UI consumes the structured body regardless of status code |

**If this table is empty:** All claims in this research were verified or cited. (It is not empty — A6 in particular is worth confirming with the user during plan-discuss.)

---

## Open Questions

1. **Should `POST /api/folders {path: 'projects/'}` (trailing slash) reject with 400, or auto-normalize and accept?**
   - What we know: `normalize_path('projects/')` returns `'/projects'` — auto-normalize works.
   - What's unclear: API ergonomics — strict (400 with "use canonical form") is more honest; lenient (auto-normalize) is more friendly.
   - Recommendation: auto-normalize and accept (matches the spirit of `normalize_path` as the canonical chokepoint; matches Phase 1's "every write path runs through normalize_path" convention). Document this in the OpenAPI description so callers know the field is normalized server-side.

2. **Should the file-rename PATCH check for filename collision in the new (or current) folder?**
   - What we know: the unique index `documents_scope_user_path_filename_unique` rejects the rename if a file with the new name already exists at the same `folder_path` — the supabase-py UPDATE raises a Python exception with SQLSTATE 23505.
   - What's unclear: should the router catch this and return a clean 409 with `{error: 'FILENAME_EXISTS_IN_FOLDER'}`?
   - Recommendation: yes — mirrors the FOLDER_NOT_EMPTY structured-error pattern; cleaner DX for the Phase 6 UI.

3. **Should the move-document PATCH validate that the target folder exists in `folders`, or accept any well-formed canonical path?**
   - What we know: Strategy B says folders exist by inference from `documents.folder_path`; therefore move-to-a-path-with-no-existing-folders-row is normal.
   - What's unclear: should the API force an explicit `POST /api/folders` first, or accept the move and let the inference logic catch up?
   - Recommendation: accept any canonical path (no folders-row check). Matches Strategy B's "folders are sparse, mostly inferred" semantics. Phase 6 UI surfaces the new path as a folder automatically once a doc lives in it.

4. **Do we ship `create_folder_if_not_exists` as a third RPC in Migration 019, or do the ON CONFLICT logic in Python via try/except?**
   - What we know: the RPC is ~10 lines of PL/pgSQL and gives clean, atomic, exception-free semantics. The try/except is brittle (depends on the exact PostgREST error shape).
   - Recommendation: ship the RPC. Cost is one extra function in Migration 019; clarity benefit is substantial.

5. **What is the response shape for `GET /api/folders`?** (Two reasonable shapes; planner picks.)
   - Option A: `{path: '/x', documents: [...], subfolders: [...]}` — single-folder view (matches `list_folder()` service function signature)
   - Option B: `[{id, scope, user_id, path, ...}, ...]` — flat list of all folders, frontend reconstructs the tree
   - Recommendation: ship A as the default; the Phase 6 UI uses one folder at a time anyway. If Phase 6 reveals the tree-reconstruction is needed, add a `?flat=true` query param later.

6. **Should the rename RPC return a `before/after` diff for audit purposes?**
   - Recommendation: not this phase. AUDIT-01 / AUDIT-02 are explicitly v2 (REQUIREMENTS.md lines 117-120). Just return the row counts.

7. **How does PATCH `/api/files/{id}` interact with Storage when the file is renamed?**
   - What we know: Storage path is `{user_id}/{doc_id}{ext}` — computed from `doc_id` and the file's *original* extension. Renaming `file_name` from `report.pdf` to `q4-report.pdf` doesn't change `doc_id` or extension, so the Storage object is unaffected.
   - What's unclear: if the rename changes the extension (e.g., `report.pdf` → `report.txt`), the Storage path becomes stale.
   - Recommendation: reject extension-changing renames in the router with a 400. Or, ignore — the Storage blob stays at the original `{ext}` and Phase 2 backfill still finds it via `doc_id` lookup. Recommend ignoring; document the behavior. Alternatively, freeze rename to filename-stem-only (no extension change). Decide in plan-discuss.

8. **Should `move_document()` validate that the target scope matches the document's existing scope?**
   - What we know: Migration 015's trigger blocks scope changes regardless. The PATCH endpoint doesn't accept a `scope` field anyway.
   - Recommendation: explicit assert in service-layer for defense in depth (`if document_row['scope'] != target_scope: raise`). Trivial belt-and-suspenders.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Supabase Postgres + RPC | Migration 019, all routes | ✓ | 16+ (Supabase managed) | — |
| supabase-py | All routers / services | ✓ | per backend/requirements.txt | — |
| FastAPI | New folders router + extended files router | ✓ | per requirements.txt | — |
| Pydantic v2 | New schemas | ✓ | per requirements.txt | — |
| Python `concurrent.futures` (stdlib) | `test_folders.py` concurrent-upload test | ✓ | stdlib | — |
| `psycopg2` (for `run_migrations.py`) | Apply Migration 019 | ✓ | per requirements.txt | — |
| Backend running on `localhost:8001` | Integration tests in `test_folders.py` | Verify before run | — | Start via `cd backend && venv/Scripts/python -m uvicorn app.main:app --reload --port 8001` |

**Missing dependencies with no fallback:** none.

**Missing dependencies with fallback:** none.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | Custom Python test suite (matches `test_helpers.py` + `test_all.py`) |
| Config file | `backend/scripts/test_all.py` SUITES list |
| Quick run command | `cd backend && venv/Scripts/python scripts/test_folders.py` (single-suite) |
| Full suite command | `cd backend && venv/Scripts/python scripts/test_all.py` |
| Pre-req | Backend running on `localhost:8001`; `.env` with `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `GEMINI_API_KEY`; admin@test.com promoted via `UPDATE public.profiles SET is_admin=true WHERE email='admin@test.com'` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| FOLDER-02 | `list_folder` / `create_folder` / `move_document` / `rename_folder` / `delete_folder` exposed in `folder_service.py` | unit (import + signature check) + integration (router-driven) | `cd backend && venv/Scripts/python scripts/test_folders.py` (section: "FOLDER-02 service surface") | ❌ Wave 0 |
| FOLDER-03 | Rename atomically updates `documents.folder_path` (every descendant) AND `folders.path` via single RPC | integration | same | ❌ Wave 0 |
| FOLDER-03 (rollback) | Mid-rename failure leaves no partial state | integration with deliberate-fail RPC variant | same (section: "FOLDER-03 transactional rollback") | ❌ Wave 0 |
| FOLDER-04 | Non-empty delete returns structured `{error, document_count, subfolder_count}` | integration | same (section: "FOLDER-04 non-empty rejected") | ❌ Wave 0 |
| FOLDER-04 (no-orphan) | Rejected delete leaves all documents in place | integration | same | ❌ Wave 0 |
| FOLDER-05 | Same file in two folders creates two rows; same file in same folder deduped | integration (upload twice with different `folder_path`, then twice with same) | same (section: "FOLDER-05 dedup key") | ❌ Wave 0 |
| FOLDER-06 | GET/POST/PATCH/DELETE /api/folders work; admin gate enforced for `scope='global'` writes | integration | same (section: "FOLDER-06 router CRUD") | ❌ Wave 0 |
| FOLDER-06 (admin-403) | Non-admin POST `scope='global'` returns 403 | integration | same | ❌ Wave 0 |
| FOLDER-07 | POST /api/files/upload accepts `folder_path` + `scope`; PATCH /api/files/{id} for rename + folder move | integration | same (section: "FOLDER-07 files router extensions") | ❌ Wave 0 |
| FOLDER-07 (concurrent-no-orphan) | 10 parallel uploads to brand-new path produce 0 folders rows (Strategy B) | integration with ThreadPoolExecutor | same (section: "Pitfall 10 concurrent upload") | ❌ Wave 0 |
| TEST-01 | `test_folders.py` registered as 15th suite in `test_all.py` SUITES list | smoke (test_all.py runs successfully) | `cd backend && venv/Scripts/python scripts/test_all.py` | ❌ Wave 0 |

### SC-to-Test Mapping (5 Success Criteria from ROADMAP)

| SC | Behavior | Test name (in `test_folders.py`) |
|----|----------|----------------------------------|
| SC1 | Routers work end-to-end with admin gate enforced; non-admin → 403 | `[FOLDER-06 router CRUD]` (multiple h.test calls covering create/list/patch/delete + admin-403) |
| SC2 | Folder rename atomically updates documents+folders; mid-rename rollback verified | `[FOLDER-03 transactional rollback]` (uses deliberate-fail RPC variant) |
| SC3 | Non-empty delete returns structured 409 with counts; no docs deleted | `[FOLDER-04 non-empty rejected]` |
| SC4 | Same file in two folders → 2 docs; same path → deduped | `[FOLDER-05 dedup key]` |
| SC5 | POST /api/files/upload accepts query args; PATCH supports rename+move; concurrent-upload no orphan | `[FOLDER-07 files router]` + `[Pitfall 10 concurrent upload]` |

### Sampling Rate

- **Per task commit:** `cd backend && venv/Scripts/python scripts/test_folders.py` (single-suite; <30 sec when backend is warm)
- **Per wave merge:** `cd backend && venv/Scripts/python scripts/test_folders.py` (still single-suite — full suite is the phase gate)
- **Phase gate:** full suite green via `cd backend && venv/Scripts/python scripts/test_all.py` before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `backend/scripts/test_folders.py` — covers FOLDER-02..07 + TEST-01 + SC1..SC5
- [ ] `backend/scripts/test_helpers.py` — extend if shared concurrent-upload helper is useful (recommend keeping the 10-thread executor inline in `test_folders.py` for now)
- [ ] Migration 019 must be applied via `cd backend && venv/Scripts/python scripts/run_migrations.py` BEFORE running `test_folders.py`. The test fixture begins with a canary check that asserts the RPCs exist (mirrors `test_two_scope_rls.py::_verify_admin_setup` pattern). Failure mode = single FAIL h.test + early return + actionable [FATAL] message naming Migration 019.
- [ ] `test_all.py` SUITES list — append `("Folders", test_folders)` after `("Files", test_files)` and before `("Backfill", test_backfill)` (folders is logically a Files extension and runs in <30s, so Files → Folders → Backfill is the natural order)

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | Existing Supabase JWT validation at `backend/app/auth.py:14-34`; Phase 3 reuses `Depends(get_current_user)` and `Depends(get_admin_user)` unchanged |
| V3 Session Management | no | Stateless completions; no session state in Phase 3 |
| V4 Access Control | yes | Two-scope RLS catalog (Migration 015); router-level admin gate `Depends(get_admin_user)` for `scope='global'` writes; `forbid_scope_mutation` trigger (Migration 015) blocks scope mutation |
| V5 Input Validation | yes | Pydantic v2 models (`FolderCreate`, `FolderPatch`, `FilePatch`); `Query(..., regex='^(user|global)$')` for scope arg; `normalize_path()` chokepoint with ValueError on path traversal segments |
| V6 Cryptography | no | No new crypto in Phase 3; `compute_file_hash()` reuses Episode 1's SHA-256 from `record_manager.py:18` |
| V8 Data Protection | yes | Storage RLS (Migration 018) protects original blobs; admin-curated globals visible to all auth users only via RLS SELECT |
| V13 API & Web Service | yes | RESTful design; structured error responses (`{error: 'FOLDER_NOT_EMPTY', ...}`); HTTP status codes match semantics (409 for state conflict) |

### Known Threat Patterns for FastAPI + Supabase + Postgres stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Path traversal via `folder_path='../../etc/passwd'` | Tampering | `normalize_path()` raises `ValueError` on `'.'` and `'..'` segments at the chokepoint (Phase 1 `folder_service.py:25, 59-64`); DB CHECK regex enforces canonical form as defense in depth |
| Cross-user folder access (e.g., User A `DELETE /api/folders/{B's folder id}`) | Information disclosure / tampering | Phase 1 RLS policies (Migration 015) reject the lookup before the delete; router-level lookup with `.eq('user_id', user_id)` is defense in depth |
| Scope escalation (`PATCH /api/files/{id}` with `scope='global'`) | Privilege escalation | Three-layer: (1) `FilePatch` Pydantic model omits scope; (2) Migration 015 `forbid_scope_mutation` trigger raises check_violation; (3) router explicitly rejects `scope` if smuggled in |
| Concurrent upload race producing duplicate folders rows | Tampering / availability | DB unique index `(scope, COALESCE(user_id,'00..0'), path)` (Migration 013); Strategy B (no folders write on upload) eliminates the race surface entirely |
| TOCTOU on folder delete (concurrent INSERT during empty-check) | Tampering | PL/pgSQL `delete_folder_if_empty` RPC with `FOR UPDATE` lock; single-transaction check-and-delete |
| Filename injection via PATCH (e.g., `file_name='../../etc/passwd'`) | Tampering | `file_name` is a TEXT column; never used in path construction outside Storage upload (which uses `{doc_id}{ext}`, not file_name); document this design assumption |
| RLS bypass via service-role key (existing CONCERNS.md anti-pattern) | Privilege escalation | Defense in depth: every service-layer call also passes `.eq('scope', ...)` and (when `scope='user'`) `.eq('user_id', user_id)`. The codebase already follows this convention (see `routers/files.py:150`); Phase 3 must extend it |

---

## Sources

### Primary (HIGH confidence)
- `backend/migrations/012_folder_path_and_scope.sql` — scope-aware unique index column list (lines 51-57)
- `backend/migrations/013_folders_table.sql` — folders table + `(scope, COALESCE(user_id,'00..0'), path)` unique expression index (lines 38-43)
- `backend/migrations/015_two_scope_rls.sql` — RLS catalog + `forbid_scope_mutation` trigger
- `backend/migrations/016_search_indexes.sql` — `text_pattern_ops` btree on `folder_path` (line 49) and `path` (line 60)
- `backend/migrations/018_storage_rls.sql` — Storage RLS predicate `(storage.foldername(name))[1]` (lines 38, 52)
- `backend/app/services/folder_service.py:14-67` — `normalize_path()` chokepoint (existing)
- `backend/app/services/record_manager.py:27-69` — current `determine_action()` shape
- `backend/app/routers/files.py:32-58` — `_upload_to_storage()` Phase 2 contract
- `backend/app/auth.py:43-52` — `get_admin_user` dependency
- `backend/app/main.py:8, 23` — router registration site
- `.planning/STATE.md` line 74 — Phase 1 STATE decision: "rows in folders exist only for explicitly-empty folders"
- `.planning/research/PITFALLS.md` Pitfalls 4, 5, 10 — definitive descriptions
- `.planning/phases/02-content-markdown-backfill-gated/02-CONTEXT.md` §LOCKED—Storage Gap Resolution

### Secondary (MEDIUM confidence)
- `.planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-RESEARCH.md` — Phase 1 research providing migration patterns and admin-gate references
- `.planning/research/ARCHITECTURE.md` — `folder_service.py` API surface preview (lines 64-71)
- `.planning/codebase/TESTING.md` — test pattern reference (`run()` returning `(passed, failed)`, `h.test()` API, scoped cleanup)

### Tertiary (LOW confidence — flagged for plan-discuss)
- A6 (STATE.md Strategy B is binding) — confirm with user before locking in plan
- A2 (supabase-py exception class for unique violations) — confirm at implementation time

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — supabase-py + FastAPI + Pydantic + PL/pgSQL are all already in use in the codebase
- Architecture: HIGH — every recommendation maps to an existing file or a small new file colocated with siblings of similar shape
- Pitfalls: HIGH — Pitfalls 4/5/10 are explicitly named in ROADMAP for this phase, and Phase 1 already designed the schema-level mitigations; Phase 3's job is to use them correctly
- Validation: HIGH — `test_folders.py` mirrors `test_two_scope_rls.py` and `test_files.py` in structure
- Folder rename RPC design: HIGH — the prefix-update PL/pgSQL idiom is textbook; the SECURITY INVOKER + RLS interaction is verified against Phase 1's existing function patterns

**Research date:** 2026-05-07
**Valid until:** 2026-06-06 (30 days; the schema and conventions are stable; only ecosystem assumptions like supabase-py exception class might shift)

---

## RESEARCH COMPLETE
