---
phase: 03-folder-service-routers-dedup-extension
reviewed: 2026-05-07T00:00:00Z
depth: deep
files_reviewed: 9
files_reviewed_list:
  - backend/migrations/019_folder_rename_and_delete_rpcs.sql
  - backend/app/models/schemas.py
  - backend/app/services/folder_service.py
  - backend/app/services/record_manager.py
  - backend/app/routers/folders.py
  - backend/app/main.py
  - backend/app/routers/files.py
  - backend/scripts/test_folders.py
  - backend/scripts/test_all.py
findings:
  critical: 3
  high: 4
  medium: 6
  low: 4
  total: 17
status: issues
---

# Phase 3: Code Review Report

**Reviewed:** 2026-05-07
**Depth:** deep (cross-file analysis with migration & contract tracing)
**Files Reviewed:** 9
**Status:** issues — three CRITICAL cross-user authorization bypasses + four HIGH-severity correctness/safety findings

## Summary

Phase 3 ships the folder-service-routers + dedup-extension surface. Migration 019's RPCs (rename / delete-if-empty / create-if-not-exists) are well-structured and the substring-math + ON-CONFLICT-expression match-points to Migration 013 are VERBATIM correct. The `record_manager.determine_action()` extension correctly implements the Pitfall-A `.is_("user_id", "null")` branching for global scope. The `forbid_scope_mutation` trigger and the three-layer scope-immutability defense for PATCH /api/files/{id} are in place.

**However**, the routers (`folders.py` rename + delete; `files.py` PATCH) have a systemic, **CRITICAL** authorization gap: they use the service-role Supabase client (`get_supabase_client()` returns SERVICE_ROLE_KEY which bypasses RLS) and perform their lookups + mutations WITHOUT any application-level `.eq("user_id", user_id)` ownership check on user-scope rows. The router pattern only gates `scope='global'` paths via the inline admin gate; for `scope='user'` rows, **any authenticated user can rename or delete any other user's folder, and rename or move any other user's document, by knowing the UUID**. RLS is the documented "second line of defense," but service-role keys bypass RLS entirely — there is no second line.

In addition: the `delete_folder_if_empty` RPC's FOR-UPDATE lock on `folders` does not prevent a concurrent upload from inserting a document at the same path (the lock is only on the folders row, not on the documents path), creating a user-visible race where a "deleted" folder reappears as an inferred folder. PostgREST `or_()` filters in `folder_service.list_folder()` interpolate the user_id into the query DSL string (defense-in-depth concern). The pre-existing `"updated_at": "now()"` literal in the upload-update branch is suspicious. Several queries use `LIKE prefix||'/%'` without escaping `%`/`_` literals in path segments, causing over-matching for folder names containing those characters.

## Critical Issues

### CR-01: Cross-user folder DELETE — any user can delete any other user's user-scope folder

**File:** `backend/app/routers/folders.py:116-159` (`delete_folder_endpoint`)

**Issue:** The DELETE handler uses the SERVICE-ROLE supabase client (`sb = get_supabase_client()` -> `SUPABASE_SERVICE_ROLE_KEY`, see `backend/app/auth.py:8-12`). Service-role bypasses ALL RLS policies. The lookup at line 80 does NOT filter by `user_id`:

```python
existing_resp = sb.table("folders").select("*").eq("id", folder_id).maybe_single().execute()
...
if folder["scope"] == "global":
    _require_admin(user_id, "global folder delete")
# <-- NO ownership check for scope='user' folders!
result = delete_folder(folder_id, sb)  # Calls RPC with service-role; RLS bypassed.
```

User A, knowing the folder_id of user B's user-scope folder, can DELETE it. The Migration 019 `delete_folder_if_empty` RPC is `SECURITY INVOKER` but is invoked through the service-role client — RLS does not apply. The RPC's own `DELETE FROM public.folders WHERE id = p_folder_id` has no user_id predicate.

The threat model in `03-PLAN.md` claims "Router-level Depends(get_admin_user) is the first line of defense for global-scope writes; RLS is the second" — but RLS is bypassed by service-role and is therefore not a defense at all here.

The `test_folders.py` cross-user test (lines 508-535) only validates GET-side isolation; PATCH/DELETE cross-user attacks are not tested.

**Severity:** CRITICAL — direct cross-user data destruction.

**Fix:** Either (a) switch to a user-JWT-bound supabase client for these endpoints (so RLS filters apply), or (b) explicitly add the ownership check after lookup:

```python
# After fetching `folder`:
if folder["scope"] == "user" and folder.get("user_id") != user_id:
    raise HTTPException(status_code=404, detail="Folder not found")
```

Apply the SAME guard at lines 84-86 (after `folder = existing_resp.data`). 404 (not 403) avoids leaking whether the UUID exists.

---

### CR-02: Cross-user folder PATCH (rename) — any user can rename any other user's user-scope folder

**File:** `backend/app/routers/folders.py:69-113` (`rename_folder_endpoint`)

**Issue:** Identical pattern to CR-01. Service-role lookup at line 80, admin gate only fires for `scope='global'`, then `rename_folder()` -> `rename_folder_prefix` RPC runs as service-role. The RPC's UPDATE statements do filter by `(p_user_id IS NULL OR user_id = p_user_id)`, but the router PASSES `folder.get("user_id")` into the RPC unconditionally — i.e., it passes the VICTIM's user_id, so the RPC happily renames the victim's documents and folders.

```python
result = rename_folder(
    folder["path"], new_path_norm, folder["scope"], folder.get("user_id"), sb
)
```

The attacker doesn't even need to spoof — the bug uses the VICTIM's stored user_id from the row.

**Severity:** CRITICAL — cross-user data tampering (folder paths and all child document paths re-written under a path the victim did not author; possible disclosure if attacker GETs the new path through their own scope='both' query).

**Fix:** Add the ownership guard after `folder = existing_resp.data`:

```python
if folder["scope"] == "user" and folder.get("user_id") != user_id:
    raise HTTPException(status_code=404, detail="Folder not found")
```

Place it BEFORE the admin gate so the missing-row 404 is identical for "not yours" and "doesn't exist."

---

### CR-03: Cross-user document PATCH — any user can rename/move any other user's user-scope document

**File:** `backend/app/routers/files.py:198-238` (`patch_file`)

**Issue:** Same systemic pattern. The lookup at line 208 has no user_id filter, the admin gate at line 216 only triggers for `scope='global'`, and the UPDATE at line 237 uses `.eq("id", file_id)` — no `.eq("user_id", user_id)` — through the service-role client.

```python
doc_resp = sb.table("documents").select("*").eq("id", file_id).maybe_single().execute()
...
if existing["scope"] == "global":
    # admin gate
# <-- NO ownership check for scope='user' rows!
sb.table("documents").update(update_data).eq("id", file_id).execute()
```

User A can rename or move user B's documents by knowing the doc UUID. Note the existing `delete_file` handler at lines 183-195 DOES include `.eq("user_id", user_id)` — the new PATCH endpoint diverges from the safe pattern.

**Severity:** CRITICAL — cross-user data tampering. Combined with CR-01/CR-02 forms a complete cross-user folder/document mutation surface.

**Fix:** Mirror the safe pattern from `delete_file`:

```python
doc_resp = (
    sb.table("documents").select("*")
    .eq("id", file_id)
    .or_(f"and(scope.eq.user,user_id.eq.{user_id}),scope.eq.global")
    .maybe_single()
    .execute()
)
```

Or the simpler explicit guard after lookup:

```python
if existing["scope"] == "user" and existing.get("user_id") != user_id:
    raise HTTPException(status_code=404, detail="Document not found")
```

---

## High Issues

### HI-01: PostgREST `or_()` interpolates user_id into the query DSL — defense-in-depth violation

**File:** `backend/app/services/folder_service.py:114-116, 135-137, 156-158`

**Issue:** Three places construct PostgREST `.or_()` filters by f-string interpolation:

```python
docs_q = docs_q.or_(
    f"and(scope.eq.user,user_id.eq.{user_id}),and(scope.eq.global,user_id.is.null)"
)
```

`user_id` is JWT-derived and *should* be a UUID, but interpolating untrusted-shaped strings into a query DSL is a defense-in-depth violation. If a future change validates the JWT differently, or if `user_id` ever picks up `,` `)` or `(` (it cannot today, but the code does not enforce that), the OR-clause structure can be subverted to drop the per-user filter, allowing one user to read another's documents via `GET /api/folders?scope=both`.

The query is also more brittle than it needs to be — supabase-py supports parameterized `.or_()` constructions that don't require f-string injection.

**Severity:** HIGH (no exploit today; would become CRITICAL the moment user_id formatting changes).

**Fix:** Either (a) split into two separate `.execute()` calls and merge in Python, or (b) validate the UUID shape explicitly before interpolation:

```python
import uuid as _uuid
try:
    _ = _uuid.UUID(user_id)
except (ValueError, TypeError):
    raise HTTPException(status_code=400, detail="invalid user_id")
```

Add the validator either at this service-layer entry point or as a router-side concern. Document the escaping contract in the docstring.

---

### HI-02: `delete_folder_if_empty` FOR-UPDATE lock does not prevent concurrent uploads from re-creating the folder

**File:** `backend/migrations/019_folder_rename_and_delete_rpcs.sql:106-141`

**Issue:** The DESIGN-NOTES claim "FOR UPDATE on folders row eliminates the TOCTOU race." The FOR UPDATE acquires a row-level write lock on the **folders** row only. A concurrent transaction that INSERTs into **documents** at `folder_path = v_path` is NOT blocked by this lock. The classic interleaving:

1. T1 (RPC): `SELECT ... FROM folders WHERE id=X FOR UPDATE` (locks folders row)
2. T1 (RPC): `SELECT COUNT(*) FROM documents WHERE folder_path = v_path` (sees 0, T1's snapshot was fixed at txn start)
3. T2 (upload): `INSERT INTO documents (folder_path = v_path, ...)` — proceeds, `documents` is not locked, T2 commits
4. T1 (RPC): `DELETE FROM folders WHERE id = X` — succeeds
5. Result: documents row exists at v_path, but folders row is gone. This is technically valid under Strategy B (folders is sparse; the path is now an INFERRED folder). But:
   - The user clicked "delete folder /projects" and expected /projects to vanish from the UI.
   - The user's concurrent upload has materialized a doc at /projects.
   - On next list, /projects is re-inferred as a subfolder.
   - The user perceives this as "delete didn't work."

The Test "Pitfall 10 concurrent upload no-orphan" (test_folders.py:538-581) does NOT exercise this race — it tests only concurrent uploads to a NEW path, not the delete-vs-upload race.

**Severity:** HIGH — user-visible semantic bug. Not a data-integrity violation under Strategy B, but the docstring promises race-freeness it does not deliver.

**Fix:** Two possible mitigations, choose one:

(a) **Document-level lock**: Inside the RPC, acquire `LOCK TABLE public.documents IN SHARE ROW EXCLUSIVE MODE` before the COUNT — prevents concurrent INSERTs. Trade-off: serializes all uploads during a delete.

(b) **Stricter test + accept the race**: Update the docstring to say "race-free for empty folders that have no concurrent uploads in flight"; require Strategy-B-aware UIs to refresh. Add a TOCTOU test in test_folders.py asserting the actual behavior so future regressions are visible.

Recommended: (a) for the RPC; document the tradeoff in the migration header.

---

### HI-03: `LIKE prefix||'/%'` does not escape `%` and `_` in folder names

**File:** Multiple
- `backend/migrations/019_folder_rename_and_delete_rpcs.sql:69-70, 78, 122-123, 130`
- `backend/app/services/folder_service.py:139, 141, 161`

**Issue:** Migration 012's canonical-form regex `^/[^/]+(/[^/]+)*$` ALLOWS `%` and `_` in folder segments (they are not `/`). When the rename or delete RPC uses `folder_path LIKE p_old_prefix || '/%'`, a literal `_` in the prefix becomes a single-char wildcard; a literal `%` becomes a multi-char wildcard.

Concrete bug:
- Folder `/foo_bar` exists; user renames it to `/foo_bar` -> `/foo_baz`.
- The UPDATE WHERE clause becomes `... LIKE '/foo_bar/%'` — `_` matches any single char, so `'/fooXbar/baz'` would also be rewritten if it existed.
- Migration 012's CHECK does not protect against this — `[^/]+` happily accepts `_`.

Worse case for over-matching:
- Folder `/100%discount` exists; rename `/100%discount` -> `/100%off`. The LIKE `'/100%discount/%'` matches anything starting with `/100` followed by anything followed by `discount/...` — over-broad.

For `delete_folder_if_empty` (lines 122-123) the same bug means a folder whose name contains `_` may be incorrectly classified as non-empty (because docs at sibling paths match the LIKE).

**Severity:** HIGH — silent data corruption / incorrect rejection. Triggers on naturally-named folders containing `_` or `%`.

**Fix:** Add `ESCAPE` clause and escape the prefix:

```sql
-- In rename_folder_prefix:
WHERE scope = p_scope
  AND (p_user_id IS NULL OR user_id = p_user_id)
  AND (folder_path = p_old_prefix
       OR folder_path LIKE replace(replace(p_old_prefix, '\', '\\'), '_', '\_') 
                       || '/%' ESCAPE '\');
```

Better: use `starts_with(folder_path, p_old_prefix || '/')` (Postgres 14+) or a regex anchor `folder_path ~ ('^' || regexp_quote(p_old_prefix) || '/')`. For the Python side (folder_service.py), prefer `.eq()`-and-prefix-match split, or escape the LIKE pattern:

```python
def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
```

(supabase-py / PostgREST: investigate whether `.like()` supports ESCAPE; if not, use `.match()` regex with anchored pattern.)

---

### HI-04: `"updated_at": "now()"` is sent as a string literal — likely silently broken

**File:** `backend/app/routers/files.py:115`

**Issue:** The update branch does:

```python
supabase.table("documents").update({
    ...
    "updated_at": "now()",
}).eq("id", record_action.document_id).execute()
```

This is sent as the JSON value `"now()"`. PostgREST passes JSON strings through to Postgres as text; Postgres cast text->timestamptz accepts `'now'` (without parens) as a special value but `'now()'` is NOT a recognized timestamp literal. Behavior:
- If `updated_at` has a column-level trigger that auto-stamps, the bogus value is overwritten and the bug is invisible.
- If no trigger, this either rejects with a 400 (data type) or silently drops the field on the JSON path (depends on PostgREST version).

This was likely intended to call the SQL function `now()`, which requires either a server-side trigger or an explicit `datetime.utcnow().isoformat()` string from Python, or omitting the field entirely and letting a DB default fire.

**Severity:** HIGH (touched in Phase 3's update branch; pre-existing in upstream code). Either the field never updates (incorrect), or every update raises 400 (which would have been caught in test).

**Fix:** Either:

```python
from datetime import datetime, timezone
"updated_at": datetime.now(timezone.utc).isoformat(),
```

Or omit the field entirely and rely on a DB trigger / default `NOW()` on UPDATE.

(Investigate whether the table has an `updated_at` trigger; if yes, drop the field from the dict.)

---

## Medium Issues

### MD-01: PATCH /api/files updates `file_name` without normalization or validation

**File:** `backend/app/routers/files.py:226-227`

**Issue:** `file_name` is written verbatim to the documents table:

```python
if body.file_name is not None:
    update_data["file_name"] = body.file_name
```

No length cap, no character whitelist, no slash check. The dedup unique index `(scope, user_id, folder_path, file_name)` will reject collisions with a SQLSTATE 23505 (a leaked Postgres error rather than a structured 409 — already deferred to Phase 6 per RESEARCH §Open Questions Q2). But `file_name` containing `/` or null bytes is not blocked; downstream consumers (UI, storage path computation in `_upload_to_storage` extracts `ext` via `os.path.splitext`) are not robust against this.

**Fix:** Add a basic validator:

```python
if body.file_name is not None:
    if "/" in body.file_name or "\\" in body.file_name or "\x00" in body.file_name or len(body.file_name) > 255:
        raise HTTPException(status_code=400, detail="invalid file_name")
    update_data["file_name"] = body.file_name
```

---

### MD-02: `create_folder` hydration query may hit 204-on-empty raise

**File:** `backend/app/services/folder_service.py:213-215`

**Issue:** After the RPC returns, the code re-fetches the full folders row:

```python
full_q = supabase_client.table("folders").select("*").eq("id", row["id"]).maybe_single().execute()
full = (full_q.data or {}) if full_q else {}
```

`maybe_single()` on supabase-py raises (not returns None) when 0 rows match — the `if full_q else {}` guard does not save you, because the exception happens BEFORE assignment. Result: any race where the row is deleted between the RPC and this hydration produces a 500 instead of returning the canonical id from the RPC.

The same `maybe_single()` raise-on-204 idiom is correctly handled with try/except in `record_manager.determine_action()` (lines 58-76); inconsistent here.

**Fix:** Wrap in try/except matching the record_manager pattern:

```python
try:
    full_q = supabase_client.table("folders").select("*").eq("id", row["id"]).maybe_single().execute()
    full = (full_q.data or {}) if full_q else {}
except Exception:
    full = {}
```

---

### MD-03: `list_folder` swallows ALL exceptions — silent failure on real errors

**File:** `backend/app/services/folder_service.py:117-121, 128-145, 150-172`

**Issue:** Three broad `except Exception:` blocks reduce the scope of error reporting to the caller. A misconfigured supabase client, a disabled `folders` table, or a typo in column name produces an empty result set indistinguishable from "no data." The router has no way to surface the failure, and integration tests pass while production lies to users.

This is a pattern violation — `record_manager.determine_action` catches a SPECIFIC failure mode (`maybe_single` raising on 204). Here, the catches mask all failures.

**Fix:** Either narrow the catch (e.g., `except APIError as e: if e.code == 'PGRST116': ...`) or let the exception propagate and let the router map it to a 500. Add a logger.error log line at minimum.

---

### MD-04: `_verify_phase3_setup` probe leaves no audit trail when it FAILS the function-not-found check

**File:** `backend/scripts/test_folders.py:101-115`

**Issue:** Probe 1 calls `rename_folder_prefix` with a non-matching prefix. If the function exists and runs, this produces a side-effect-free RPC — fine. But:
- The probe passes user_id = `'00000000-0000-0000-0000-000000000000'` (the all-zeros UUID). If a folder/doc happened to have user_id NULL (impossible per CHECK constraint, but possible in dev), the COALESCE-based predicate inside the RPC could match unintended rows during the probe. Low probability here because the WHERE also filters by an unlikely path prefix. Risk: LOW.
- The probe does not assert that all THREE Phase 3 RPCs exist. If `delete_folder_if_empty` was added but `create_folder_if_not_exists` wasn't, probe 1 still passes — and the suite later fails on FOLDER-04 with an opaque "function does not exist" error.

**Fix:** Probe each of the three RPCs by name (cheap; they're idempotent on no-match inputs).

---

### MD-05: Test creates a `test_rename_folder_prefix_fails_midway` SQL function as side effect

**File:** `backend/scripts/test_folders.py:323-336`

**Issue:** The test installs a PL/pgSQL function `public.test_rename_folder_prefix_fails_midway` on each run via direct psycopg2. The `finally` block at line 350-358 attempts to drop it, but:
- If psycopg2 connection fails mid-block (line 311), the function is never installed (OK).
- If the DROP fails (line 352-358 swallows all exceptions), the function persists in the database indefinitely.
- The function is created at the `public` schema with no namespace prefix — collisions with future test isolation could be subtle.

Per CLAUDE.md: "Tests must NEVER delete all user data." This is not a bulk-delete violation, but installing arbitrary public-schema functions is also a side-effect that strict track-and-clean discipline should avoid.

**Fix:** Either (a) namespace the function under a unique random suffix per run, or (b) install in a per-test schema that gets dropped at the end, or (c) use a transaction-bound temporary function.

---

### MD-06: Test inserts via service-role bypassing RLS — does not validate that the upload path enforces RLS coupling CHECK

**File:** `backend/scripts/test_folders.py:263-267, 315-319, 364-368, 519-523`

**Issue:** The test inserts documents via service-role (`sb_admin`) repeatedly. Service-role bypasses BOTH RLS and the `forbid_scope_mutation` trigger (well — actually the trigger is BEFORE UPDATE so still fires, but RLS is bypassed). This means:
- The "Cross-user isolation" test (line 533) inserts user-A's doc via service role — fine for setup.
- But several FOLDER-03/04 tests insert via service-role and never exercise the upload path's RLS+coupling enforcement for those specific files. That's tested elsewhere (Plan 08), but this test assumes those paths are correct.

**Severity:** MEDIUM (test architecture, not correctness).

**Fix:** Where possible, prefer the upload endpoint over service-role inserts in test_folders.py — exercises real defense-in-depth. The current shape is acceptable but trades coverage for setup speed.

---

## Low Issues

### LO-01: `_FORBIDDEN_SEGMENTS` does not block Windows reserved names or UNC-style paths

**File:** `backend/app/services/folder_service.py:25`

**Issue:** Only `..` and `.` are forbidden. Names like `CON`, `PRN`, `NUL` (Windows reserved), or paths with embedded null bytes are accepted. The DB CHECK constraint `^/[^/]+(/[^/]+)*$` also allows them. Not a security issue per se; the storage layer is Linux-flavored (Supabase Storage) so this is probably benign. Worth documenting in the docstring.

**Fix:** Consider expanding `_FORBIDDEN_SEGMENTS` if Windows clients are in scope. Otherwise, document the decision.

---

### LO-02: `_upload_to_storage` parameter name `user_id` is misleading

**File:** `backend/app/routers/files.py:33-58`

**Issue:** The function parameter is `user_id: str`, but callers pass `user_id=storage_user_segment` where `storage_user_segment` may be the literal string `"global"`. The log line `f"Storage upload OK: doc={document_id} path={storage_path} ..."` doesn't leak it, but a future refactor could read `user_id` and treat it as the calling user's id. Rename to `storage_segment` for clarity.

**Fix:** Rename parameter and update call sites.

---

### LO-03: Docstring on `delete_folder` claims FOR-UPDATE eliminates TOCTOU but it doesn't (see HI-02)

**File:** `backend/app/services/folder_service.py:295-307`

**Issue:** `"""... uses SELECT ... FOR UPDATE on the folders row to eliminate the TOCTOU race. FOLDER-04 + Pitfall 5."""` — the lock only prevents concurrent rename/delete on the same folder row; it does NOT prevent concurrent upload. Update the docstring to reflect actual semantics (or fix the underlying issue).

**Fix:** Tighten the wording or apply the HI-02 fix.

---

### LO-04: Inline `if __name__ == "__main__"` self-tests in `folder_service.py` increase production import surface

**File:** `backend/app/services/folder_service.py:330-354`

**Issue:** The `__main__` block adds 24 lines of test cases at module-import time but only run when `python -m app.services.folder_service`. It pollutes the file with test data and assertions. CLAUDE.md says tests live in `backend/scripts/`. Move these into `test_folders.py` or a focused `test_normalize_path.py`.

**Fix:** Migrate to `backend/scripts/test_folders.py` and delete from the service module.

---

## Recommended Fix Order

The three CRITICAL findings share root cause (service-role client + missing ownership guards) and should be fixed together as a single hardening pass:

1. **CR-01, CR-02, CR-03** (cross-user authorization bypass on folder DELETE/PATCH and document PATCH) — block this phase from production. Add ownership guards (or switch to JWT-bound supabase clients) in `routers/folders.py:rename_folder_endpoint`, `routers/folders.py:delete_folder_endpoint`, `routers/files.py:patch_file`. Add a regression test in `test_folders.py` that confirms user A's PATCH/DELETE on user B's folder/doc returns 404.

2. **HI-04** (`"updated_at": "now()"` literal) — quick fix; low surface area; high impact if currently silently broken.

3. **HI-03** (LIKE escaping in RPCs and folder_service) — adds a `_escape_like` helper and updates four call sites + two RPC predicates.

4. **HI-02** (delete-vs-upload race) — choose between document-level lock vs. accepting the documented race + updating the docstring; add a TOCTOU regression test.

5. **HI-01** (PostgREST or_() interpolation) — UUID validation at the boundary; consider a router-level `Annotated[str, AfterValidator(_uuid)]` dependency.

6. **MD-01..MD-06** — straightforward defensive improvements; can ship in a follow-up.

7. **LO-01..LO-04** — cleanup; defer to Phase 6 polish.

---

_Reviewed: 2026-05-07_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep (cross-file analysis with migration & contract tracing)_
