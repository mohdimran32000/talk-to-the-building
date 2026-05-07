---
phase: 03
plan: 02
type: execute
wave: 2
depends_on: [01]
files_modified:
  - backend/app/services/folder_service.py
autonomous: true
requirements:
  - FOLDER-02
must_haves:
  truths:
    - "backend/app/services/folder_service.py exports five new public functions: list_folder, create_folder, move_document, rename_folder, delete_folder"
    - "Every new function takes a positional-untyped supabase_client parameter (matches record_manager.determine_action style; no FastAPI imports added to the service module)"
    - "Every new function whose signature includes a path argument runs that argument through normalize_path() AS THE FIRST STATEMENT (Pitfall 4 chokepoint enforcement — service-layer suspenders alongside the router-layer belt in Plans 04 and 05)"
    - "rename_folder calls Migration 019's rename_folder_prefix RPC; delete_folder calls delete_folder_if_empty; create_folder calls create_folder_if_not_exists — by exact name, with exact parameter shapes locked in Plan 01's interfaces block"
    - "rename_folder raises ValueError BEFORE invoking the RPC if old_path or new_path normalize to '/' (defense-in-depth alongside the RPC's own root-rename check)"
    - "list_folder returns {documents: [...], subfolders: [...]} — single-folder view at one level deep; subfolders are the UNION of folders rows (path = path predicate) AND inferred folders (DISTINCT one-level-down folder_path values from documents that don't appear in folders)"
    - "move_document is scope-respectful: it does NOT accept a target scope arg (scope is immutable per Migration 015); the UPDATE filters .eq('id', document_id).eq('user_id', user_id) for defense in depth"
    - "Existing normalize_path() function (L28-67) is UNCHANGED — Phase 1 Plan 01 contract is preserved; the inline __main__ self-tests at L72-96 remain runnable"
    - "The new functions live AFTER normalize_path() and BEFORE the `if __name__ == '__main__':` block (preserves the inline self-test trailer)"
    - "Module imports cleanly: `from app.services.folder_service import list_folder, create_folder, move_document, rename_folder, delete_folder` succeeds in a venv Python -c smoke check"
  artifacts:
    - path: "backend/app/services/folder_service.py"
      provides: "Five service-layer functions for folder CRUD; pure service module (no FastAPI imports); supabase_client injected"
      contains: "def list_folder("
      contains_2: "def create_folder("
      contains_3: "def move_document("
      contains_4: "def rename_folder("
      contains_5: "def delete_folder("
      contains_6: "rpc(\"rename_folder_prefix\""
      contains_7: "rpc(\"delete_folder_if_empty\""
      contains_8: "rpc(\"create_folder_if_not_exists\""
      contains_9: "normalize_path("
      min_lines: 200
  key_links:
    - from: "folder_service.rename_folder / delete_folder / create_folder"
      to: "Migration 019 RPCs (Plan 01)"
      via: "supabase_client.rpc('<name>', {...}).execute()"
      pattern: "rpc\\(\"(rename_folder_prefix|delete_folder_if_empty|create_folder_if_not_exists)\""
    - from: "folder_service service surface"
      to: "backend/app/routers/folders.py (Plan 04)"
      via: "from app.services.folder_service import normalize_path, list_folder, create_folder, rename_folder, delete_folder"
      pattern: "from app.services.folder_service import"
    - from: "Every path argument"
      to: "normalize_path() (Phase 1 Plan 01)"
      via: "First statement of every new function"
      pattern: "norm = normalize_path\\("
---

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Router (Plan 04 / 05) -> service-layer function | The router has already normalized the path (belt); the service layer normalizes AGAIN (suspenders) — Pitfall 4 multi-layer enforcement |
| Service layer -> Migration 019 RPCs | RLS policies on documents/folders apply via SECURITY INVOKER; admin gate is enforced at the router (Plan 04 / 05) BEFORE service-layer entry |
| supabase_client (service-role from auth.py:8-12) -> Postgres | Service-role bypasses RLS; defense in depth via `.eq('scope',...)` and (when scope='user') `.eq('user_id', user_id)` filters in service-layer queries (matches CONCERNS.md anti-pattern documentation) |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-3-02-PathTraversal | Tampering | Every path argument to every new function | mitigate | Each function's FIRST STATEMENT is `norm = normalize_path(<path arg>)`. normalize_path raises ValueError on `'.'` and `'..'` segments (Phase 1 / Plan 01 contract at folder_service.py:25, 59-64). Belt is Plan 04/05 routers; suspenders is here; bedrock is the DB CHECK from Migrations 012/013; fourth layer is Migration 019's RPC own canonical-form regex. |
| T-3-02-RootRename | Tampering / Data Integrity | rename_folder called with old_path='/' or new_path='/' | mitigate | After normalize_path (which preserves '/' for empty/root inputs), the function raises `ValueError("cannot rename root path")` BEFORE invoking the RPC. Defense in depth — Migration 019's rename_folder_prefix RPC also raises check_violation on `p_old_prefix='/'` (RPC body line — Plan 01 Task 1). |
| T-3-02-CrossUserMove | Information Disclosure | move_document for a doc that belongs to another user | mitigate | The UPDATE filters `.eq('id', document_id).eq('user_id', user_id)`. If user A passes user B's document_id, the UPDATE matches zero rows and returns no data. Defense in depth: Migration 015 RLS policies on documents.UPDATE require `scope='user' AND user_id = (SELECT auth.uid())` so even service-role queries that pretend to be user A would be blocked at the policy level if RLS were enforced (service-role bypasses by design — but the .eq filter at the app layer is the explicit gate per CONCERNS.md). |
| T-3-02-RPCFailure | Availability | RPC call returns no data (RLS blocked, network blip) | mitigate | Each call wraps `result.data` access defensively: `if not result.data: return {...zero counts...}` for rename/delete/create. The service layer never raises on empty data; the router (Plan 04) consumes the structured zero-count response and returns clean HTTP responses. |
| T-3-02-CouplingViolation | Tampering | create_folder called with mismatched scope/user_id (e.g., scope='global' + user_id=<uuid>) | accept | Migration 019's create_folder_if_not_exists RPC raises check_violation on coupling violations (Plan 01 Task 1 spec). The service layer passes scope and user_id as-is; the RPC is the gate. Plan 04's router computes `effective_user_id = None` for global scope before calling create_folder, so well-formed router calls never trigger this. |
</threat_model>

<objective>
Extend `backend/app/services/folder_service.py` with five new pure service-layer functions — `list_folder`, `create_folder`, `move_document`, `rename_folder`, `delete_folder` — that the Phase 3 routers (Plan 04 + Plan 05) call. The functions are pure service-layer: no FastAPI imports, supabase_client injected positional-untyped (matches `record_manager.determine_action()` style at L27-69), normalize_path() is the first statement of every function that takes a path argument (Pitfall 4 chokepoint enforcement).

Three of the five functions (rename_folder, delete_folder, create_folder) are thin Python wrappers around Migration 019's RPCs landed in Plan 01. The other two (list_folder, move_document) are direct supabase-py table queries.

The existing `normalize_path()` (L28-67) and the inline `__main__` self-tests (L72-96) are PRESERVED unchanged; new functions are inserted between them. Phase 1 / Plan 01's docstring at L11-13 explicitly anticipates this extension: "Phase 3 extends this file with folder CRUD (`list_folder`, `create_folder`, `move_document`, `rename_folder`, `delete_folder`)."
</objective>

<execution_context>
@.claude/get-shit-done/workflows/execute-plan.md
@.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/REQUIREMENTS.md
@CLAUDE.md

@.planning/phases/03-folder-service-routers-dedup-extension/03-RESEARCH.md
@.planning/phases/03-folder-service-routers-dedup-extension/03-PATTERNS.md
@.planning/phases/03-folder-service-routers-dedup-extension/03-VALIDATION.md
@.planning/codebase/CONVENTIONS.md
@.planning/codebase/ARCHITECTURE.md

@.planning/phases/03-folder-service-routers-dedup-extension/01-PLAN.md
@backend/migrations/019_folder_rename_and_delete_rpcs.sql

@backend/app/services/folder_service.py
@backend/app/services/record_manager.py
@backend/app/services/ingestion.py

<interfaces>
<!-- The contracts this plan ESTABLISHES — Plans 04 and 05 consume these. -->

list_folder(path: str, scope: str, user_id: str | None, supabase_client) -> dict
  Returns {path: str, documents: list[dict], subfolders: list[str]} for ONE level of depth.
  - documents: rows from public.documents WHERE folder_path = norm AND scope-filter applied
  - subfolders: UNION of:
      (a) folders rows where path matches "immediate child of norm" predicate
          path LIKE norm||'/%' AND path NOT LIKE norm||'/%/%' (when norm != '/'), or
          path != '/' AND path NOT LIKE '/%/%' (when norm == '/')
      (b) DISTINCT one-level-down subfolder names extracted from documents.folder_path
          where folder_path LIKE norm||'/%' (strip prefix, take first segment after split on '/')
  - Scope handling: scope='both' returns union; 'user' filters to user_id; 'global' filters to user_id IS NULL
  - Path normalized via normalize_path() as first statement.

create_folder(path: str, scope: str, user_id: str | None, supabase_client) -> dict
  Calls Migration 019's rpc('create_folder_if_not_exists', {p_scope, p_user_id, p_path}).
  Returns {id: str, scope: str, user_id: str | None, path: str, created_at: str, action: 'created'|'exists'}.
  - For scope='global', user_id MUST be None (caller's responsibility; RPC also enforces).
  - Path normalized via normalize_path() as first statement.

move_document(document_id: str, new_folder_path: str, user_id: str, supabase_client) -> dict | None
  UPDATEs documents.folder_path WHERE id=document_id AND user_id=user_id (scope is immutable).
  Returns the updated document row, or None if no row matched (cross-user attempt).
  - new_folder_path normalized via normalize_path() as first statement.
  - NEVER changes scope (Migration 015 trigger blocks it; explicit not-touched here).

rename_folder(old_path: str, new_path: str, scope: str, user_id: str | None, supabase_client) -> dict
  Calls Migration 019's rpc('rename_folder_prefix', {p_old_prefix, p_new_prefix, p_scope, p_user_id}).
  Returns {documents_updated: int, folders_updated: int}.
  - Raises ValueError if normalized old_path == '/' or new_path == '/' (root rename forbidden).
  - Both paths normalized via normalize_path() as first statements.

delete_folder(folder_id: str, supabase_client) -> dict
  Calls Migration 019's rpc('delete_folder_if_empty', {p_folder_id}).
  Returns one of:
    {deleted: True, document_count: 0, subfolder_count: 0}, or
    {deleted: False, error: 'FOLDER_NOT_EMPTY', document_count: int, subfolder_count: int}
  - The router (Plan 04) maps the FALSE branch to a 409 with {error, document_count, subfolder_count}.
  - The 'no_data_found' SQLSTATE from the RPC (folder missing) propagates as a Python Exception;
    the router catches it and returns 404. Service layer does NOT catch it (let the router decide).

normalize_path(p: str | None) -> str
  UNCHANGED from Phase 1 / Plan 01. Imported by the new functions; not redefined.
</interfaces>
</context>

<tasks>

<task id="3-02-01" type="auto">
  <name>Task 1: Add list_folder, create_folder, move_document, rename_folder, delete_folder to folder_service.py</name>
  <files>backend/app/services/folder_service.py</files>
  <read_first>
    - backend/app/services/folder_service.py FULL FILE (the in-place insertion point — new functions go AFTER L67's `return s` and BEFORE L72's `if __name__ == "__main__":` block)
    - backend/app/services/record_manager.py FULL FILE (PRIMARY style analog — function shape with `supabase_client` positional-untyped parameter; triple-quoted docstring with numbered logic block; try/except around supabase queries that may raise on `.maybe_single()` returning 204; type hints on every other parameter)
    - backend/app/services/ingestion.py L1-50 (SECONDARY style analog — the supabase_client parameter shape `supabase_client: Any` style is NOT used here; record_manager's untyped form is the convention)
    - .planning/phases/03-folder-service-routers-dedup-extension/03-RESEARCH.md §Folder Service API Surface (paste-ready function bodies; signatures locked in Plan 01's interfaces)
    - .planning/phases/03-folder-service-routers-dedup-extension/03-RESEARCH.md §Path-Prefix Matching Predicates (the `LIKE 'prefix/%'` predicates for descendant matches)
    - .planning/phases/03-folder-service-routers-dedup-extension/03-RESEARCH.md §Code Examples (lines 776-815 — verbatim Python for rename_folder + delete_folder RPC invocations)
    - .planning/phases/03-folder-service-routers-dedup-extension/03-PATTERNS.md §`backend/app/services/folder_service.py` (paste-ready pseudocode + convention notes)
    - .planning/phases/03-folder-service-routers-dedup-extension/01-PLAN.md (Migration 019 RPC contracts — exact parameter names and RETURNS shapes)
    - backend/migrations/019_folder_rename_and_delete_rpcs.sql (verify exact RPC parameter names match what this plan invokes)
    - .planning/research/PITFALLS.md §Pitfall 4 (path drift) — explains why every service function's first statement is normalize_path()
  </read_first>
  <action>
    Modify `backend/app/services/folder_service.py` to add five new public functions AFTER L67 (the existing `return s` of normalize_path) and BEFORE L72 (the existing `if __name__ == "__main__":` block). Preserve `normalize_path()` and the inline self-tests UNCHANGED.

    ### Insertion point structure

    The file becomes:
    ```
    L1-13:   module docstring (unchanged)
    L14-26:  imports + _CANONICAL_PATH_RE + _FORBIDDEN_SEGMENTS (unchanged)
    L28-67:  def normalize_path(...) (UNCHANGED)
    L68-71:  blank lines (unchanged)
    L72+:    NEW: five new functions (this task adds them HERE)
    L72+N:   (one blank line)
    L73+N+:  if __name__ == "__main__": (UNCHANGED — moved down by N lines)
    ```

    ### Function bodies (paste-ready)

    Insert these five function definitions in the order shown. The order matters: it follows the natural call-graph from "least dependent" (list_folder reads only) to "most dependent" (delete_folder relies on the delete RPC).

    ```python


    # ──────────────────────────────────────────────────────────────────────────
    # Phase 3 — Folder service CRUD (FOLDER-02). All five functions below:
    #   - take supabase_client as positional-untyped (matches record_manager.py)
    #   - run normalize_path() on every path argument as the FIRST STATEMENT
    #     (Pitfall 4 chokepoint enforcement; belt+suspenders alongside routers)
    #   - have NO FastAPI imports — pure service layer, unit-testable in isolation
    #   - return plain dicts (not Pydantic models — that's the router's job)
    # ──────────────────────────────────────────────────────────────────────────


    def list_folder(
        path: str,
        scope: str,
        user_id: str | None,
        supabase_client,
    ) -> dict:
        """List one level of a folder: documents at this path + immediate subfolders.

        Returns:
            {
              "path": str,                # normalized path
              "documents": list[dict],    # rows where folder_path == path (filtered by scope)
              "subfolders": list[str],    # UNION of explicit folders rows + inferred from documents
            }

        Subfolder discovery:
        1. Explicit folders rows whose path is an immediate child of `path`
           (path LIKE norm||'/%' AND path NOT LIKE norm||'/%/%' — exactly one level deeper).
        2. Inferred subfolder names from documents.folder_path: take all docs where
           folder_path LIKE norm||'/%', strip the norm||'/' prefix, take everything
           up to the first '/'. Deduplicate.

        Scope handling: 'both' returns union of user (matching user_id) + global; 'user' filters
        to the calling user; 'global' filters to user_id IS NULL.
        """
        norm = normalize_path(path)

        # ─ Documents at this exact folder ─
        docs_q = supabase_client.table("documents").select("*").eq("folder_path", norm)
        if scope == "user":
            docs_q = docs_q.eq("scope", "user").eq("user_id", user_id)
        elif scope == "global":
            docs_q = docs_q.eq("scope", "global").is_("user_id", "null")
        else:  # 'both' — union via or_(); see PostgREST or() syntax
            docs_q = docs_q.or_(
                f"and(scope.eq.user,user_id.eq.{user_id}),and(scope.eq.global,user_id.is.null)"
            )
        try:
            docs_resp = docs_q.execute()
            documents = docs_resp.data or []
        except Exception:
            documents = []

        # ─ Explicit folders rows (immediate children) ─
        # Predicate "immediate child of norm":
        #   if norm == '/': path != '/' AND path NOT LIKE '/%/%'
        #   else:           path LIKE norm||'/%' AND path NOT LIKE norm||'/%/%'
        explicit_subfolders: list[str] = []
        try:
            f_q = supabase_client.table("folders").select("path")
            if scope == "user":
                f_q = f_q.eq("scope", "user").eq("user_id", user_id)
            elif scope == "global":
                f_q = f_q.eq("scope", "global").is_("user_id", "null")
            else:
                f_q = f_q.or_(
                    f"and(scope.eq.user,user_id.eq.{user_id}),and(scope.eq.global,user_id.is.null)"
                )
            if norm == "/":
                f_q = f_q.neq("path", "/").not_.like("path", "/%/%")
            else:
                f_q = f_q.like("path", f"{norm}/%").not_.like("path", f"{norm}/%/%")
            f_resp = f_q.execute()
            explicit_subfolders = [row["path"] for row in (f_resp.data or [])]
        except Exception:
            explicit_subfolders = []

        # ─ Inferred subfolders from documents.folder_path (descendants below norm) ─
        # Strip the norm||'/' prefix and take first segment.
        inferred_subfolders: set[str] = set()
        try:
            inf_q = supabase_client.table("documents").select("folder_path")
            if scope == "user":
                inf_q = inf_q.eq("scope", "user").eq("user_id", user_id)
            elif scope == "global":
                inf_q = inf_q.eq("scope", "global").is_("user_id", "null")
            else:
                inf_q = inf_q.or_(
                    f"and(scope.eq.user,user_id.eq.{user_id}),and(scope.eq.global,user_id.is.null)"
                )
            prefix = "/" if norm == "/" else f"{norm}/"
            inf_q = inf_q.like("folder_path", f"{prefix}%")
            inf_resp = inf_q.execute()
            for row in (inf_resp.data or []):
                fp = row.get("folder_path") or ""
                if fp.startswith(prefix):
                    rest = fp[len(prefix):]
                    first_seg = rest.split("/", 1)[0]
                    if first_seg:
                        # Reconstruct the canonical immediate-child path.
                        inferred_subfolders.add(prefix + first_seg if norm == "/" else f"{norm}/{first_seg}")
        except Exception:
            pass

        # Union explicit + inferred; deduplicate; sort for deterministic output.
        all_subfolders = sorted(set(explicit_subfolders) | inferred_subfolders)

        return {
            "path": norm,
            "documents": documents,
            "subfolders": all_subfolders,
        }


    def create_folder(
        path: str,
        scope: str,
        user_id: str | None,
        supabase_client,
    ) -> dict:
        """Create an explicit folders row via Migration 019's atomic upsert RPC.

        Idempotent: if a row already exists at (scope, COALESCE(user_id,'00..0'), path),
        returns the existing row with action='exists'. Otherwise inserts a new row and
        returns it with action='created'.

        For scope='global' the caller MUST pass user_id=None (the RPC raises a
        check_violation otherwise via the coupling rule).
        """
        norm = normalize_path(path)

        result = supabase_client.rpc("create_folder_if_not_exists", {
            "p_scope": scope,
            "p_user_id": user_id,   # None for scope='global'
            "p_path": norm,
        }).execute()

        if not result.data:
            # RPC returned no rows — should not happen for a well-formed call, but be defensive.
            return {"id": None, "scope": scope, "user_id": user_id, "path": norm,
                    "created_at": None, "action": "exists"}

        row = result.data[0]
        # Hydrate the full folders row so the caller (router) gets created_at.
        full_q = supabase_client.table("folders").select("*").eq("id", row["id"]).maybe_single().execute()
        full = (full_q.data or {}) if full_q else {}

        return {
            "id": row["id"],
            "scope": full.get("scope", scope),
            "user_id": full.get("user_id", user_id),
            "path": full.get("path", norm),
            "created_at": full.get("created_at"),
            "action": "created" if row.get("created") else "exists",
        }


    def move_document(
        document_id: str,
        new_folder_path: str,
        user_id: str,
        supabase_client,
    ) -> dict | None:
        """Move a document to a new folder. Scope is immutable (Migration 015 trigger).

        UPDATE filters .eq('id', document_id).eq('user_id', user_id) — defense in depth
        against cross-user moves alongside RLS. Returns the updated document row, or
        None if no row matched (e.g., wrong owner).
        """
        norm = normalize_path(new_folder_path)

        result = (
            supabase_client.table("documents")
            .update({"folder_path": norm})
            .eq("id", document_id)
            .eq("user_id", user_id)
            .execute()
        )
        if not result.data:
            return None
        return result.data[0]


    def rename_folder(
        old_path: str,
        new_path: str,
        scope: str,
        user_id: str | None,
        supabase_client,
    ) -> dict:
        """Rename a folder (transactional prefix update on documents + folders).

        Calls Migration 019's rename_folder_prefix RPC — the only cross-table-atomic
        unit available from supabase-py (PostgREST executes each .execute() in its own
        transaction; only RPCs span multiple statements atomically). FOLDER-03.

        Raises:
            ValueError: if old_path or new_path normalize to '/' (root rename forbidden;
                        defense in depth alongside the RPC's own root-rename check).
        """
        old_norm = normalize_path(old_path)
        new_norm = normalize_path(new_path)
        if old_norm == "/" or new_norm == "/":
            raise ValueError("cannot rename root path")

        result = supabase_client.rpc("rename_folder_prefix", {
            "p_old_prefix": old_norm,
            "p_new_prefix": new_norm,
            "p_scope": scope,
            "p_user_id": user_id,    # None for scope='global'
        }).execute()

        if not result.data:
            return {"documents_updated": 0, "folders_updated": 0}
        row = result.data[0]
        return {
            "documents_updated": row.get("documents_updated", 0),
            "folders_updated": row.get("folders_updated", 0),
        }


    def delete_folder(
        folder_id: str,
        supabase_client,
    ) -> dict:
        """Delete a folder iff empty (race-free single-transaction empty-check + delete).

        Calls Migration 019's delete_folder_if_empty RPC, which uses SELECT ... FOR UPDATE
        on the folders row to eliminate the TOCTOU race. FOLDER-04 + Pitfall 5.

        Returns:
            On success: {deleted: True, document_count: 0, subfolder_count: 0}
            On non-empty: {deleted: False, error: 'FOLDER_NOT_EMPTY',
                           document_count: int, subfolder_count: int}

        The 'no_data_found' SQLSTATE from the RPC (folder missing) propagates as an
        Exception — the router (Plan 04) catches it and returns 404. The service
        layer does NOT catch the missing-folder case; the caller decides.
        """
        result = supabase_client.rpc("delete_folder_if_empty", {
            "p_folder_id": folder_id,
        }).execute()

        if not result.data:
            return {"deleted": False, "error": "FOLDER_NOT_EMPTY",
                    "document_count": 0, "subfolder_count": 0}

        row = result.data[0]
        if row.get("deleted"):
            return {"deleted": True, "document_count": 0, "subfolder_count": 0}
        return {
            "deleted": False,
            "error": "FOLDER_NOT_EMPTY",
            "document_count": row.get("document_count", 0),
            "subfolder_count": row.get("subfolder_count", 0),
        }


    ```

    Critical DON'Ts:
    - DO NOT add `from fastapi import ...` — folder_service.py is a pure service module (matches `record_manager.py` and `ingestion.py`).
    - DO NOT type-hint `supabase_client` (matches `record_manager.py:31` convention; type-hinting it would import the supabase Client class and add an unnecessary dependency).
    - DO NOT change `normalize_path()` (Phase 1 / Plan 01 contract) or the inline `__main__` self-tests (preserve runnability via `python -m app.services.folder_service`).
    - DO NOT call `normalize_path` lazily — it MUST be the first statement of every new function (Pitfall 4 chokepoint enforcement).
    - DO NOT raise on RPC empty-data branches in `rename_folder` and `delete_folder` — return zero-count dicts. Routers handle empty data uniformly.
    - DO NOT swallow the `no_data_found` SQLSTATE from `delete_folder_if_empty` in this layer — let it propagate; the router decides 404 vs other handling.
    - DO NOT use `.delete()` directly anywhere (CLAUDE.md scoped-cleanup rule generalizes to operator scripts and this service surface — the only DELETE in this codebase is via the `delete_folder_if_empty` RPC, which gates on emptiness).
    - DO NOT add a `move_folder` function — moving folders is a rename (handled by rename_folder); add the helper only if Plan 04's router specifically needs it (it does NOT this phase).
    - DO NOT change the `supabase_client` parameter position (it MUST be last positional — matches record_manager.determine_action so callers can pass it positionally without keyword).
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -c "import pathlib, ast; src = pathlib.Path('app/services/folder_service.py').read_text(encoding='utf-8'); ast.parse(src); body = '\n'.join(line for line in src.splitlines() if not line.lstrip().startswith('#')); assert 'def list_folder(' in body, 'list_folder missing'; assert 'def create_folder(' in body, 'create_folder missing'; assert 'def move_document(' in body, 'move_document missing'; assert 'def rename_folder(' in body, 'rename_folder missing'; assert 'def delete_folder(' in body, 'delete_folder missing'; assert 'def normalize_path(' in body, 'existing normalize_path() must remain'; assert body.count('normalize_path(') &gt;= 6, f'expected normalize_path called in 5 new fns + 1 existing self-test, got {body.count(chr(110)+chr(111)+chr(114)+chr(109)+chr(97)+chr(108)+chr(105)+chr(122)+chr(101)+chr(95)+chr(112)+chr(97)+chr(116)+chr(104)+chr(40))}'; assert 'rpc(\"rename_folder_prefix\"' in body, 'rename_folder_prefix RPC call missing'; assert 'rpc(\"delete_folder_if_empty\"' in body, 'delete_folder_if_empty RPC call missing'; assert 'rpc(\"create_folder_if_not_exists\"' in body, 'create_folder_if_not_exists RPC call missing'; assert 'from fastapi' not in src, 'folder_service must NOT import from fastapi (pure service)'; assert 'cannot rename root path' in body, 'rename_folder root-rename guard missing'; assert 'if __name__ == \"__main__\"' in src, 'inline self-tests must remain'; assert '_CANONICAL_PATH_RE' in src, 'normalize_path infrastructure preserved'; print('folder_service.py extensions OK; line count =', len(src.splitlines()))"</automated>
  </verify>
  <acceptance_criteria>
    - File `backend/app/services/folder_service.py` parses as valid Python (`ast.parse` succeeds).
    - `grep -c "^def list_folder(" backend/app/services/folder_service.py` returns 1.
    - `grep -c "^def create_folder(" backend/app/services/folder_service.py` returns 1.
    - `grep -c "^def move_document(" backend/app/services/folder_service.py` returns 1.
    - `grep -c "^def rename_folder(" backend/app/services/folder_service.py` returns 1.
    - `grep -c "^def delete_folder(" backend/app/services/folder_service.py` returns 1.
    - `grep -c "^def normalize_path(" backend/app/services/folder_service.py` returns 1 (existing function preserved).
    - File contains `rpc("rename_folder_prefix"` (exact match — Migration 019 RPC name).
    - File contains `rpc("delete_folder_if_empty"` (exact match).
    - File contains `rpc("create_folder_if_not_exists"` (exact match).
    - Each new function (5 of them) contains `normalize_path(` somewhere in its body — `grep -c "normalize_path(" backend/app/services/folder_service.py` returns at least 6 (5 new + at least 1 in existing self-tests).
    - File contains NO `from fastapi` import (pure service-layer).
    - File contains the string `cannot rename root path` (rename_folder ValueError).
    - File still contains `if __name__ == "__main__":` (inline self-tests preserved).
    - File still contains `_CANONICAL_PATH_RE = re.compile` (normalize_path infrastructure preserved).
    - Module imports cleanly: `cd backend && venv/Scripts/python -c "from app.services.folder_service import normalize_path, list_folder, create_folder, move_document, rename_folder, delete_folder; assert all(callable(f) for f in [normalize_path, list_folder, create_folder, move_document, rename_folder, delete_folder]); print('OK')"` prints `OK`.
    - Inline self-tests still run: `cd backend && venv/Scripts/python -m app.services.folder_service` exits 0 and prints `folder_service.normalize_path: 15 self-tests passed`.
    - rename_folder root-rename guard tests cleanly: `cd backend && venv/Scripts/python -c "from app.services.folder_service import rename_folder; class FakeSb: ...
" not part of runtime; instead verified by inspecting `body.count('cannot rename root path') >= 1`.
    - File length is at least 200 lines (was 96; +120 LOC for the five functions).
  </acceptance_criteria>
  <done>
    `backend/app/services/folder_service.py` extended with five new functions (list_folder, create_folder, move_document, rename_folder, delete_folder), all calling Migration 019 RPCs by exact name with the exact parameter shapes locked in Plan 01. Existing normalize_path() and inline self-tests are unchanged. Module imports cleanly via venv Python. Plans 04 and 05 can now `from app.services.folder_service import ...` the new functions.
  </done>
</task>

</tasks>

<verification>
This plan delivers FOLDER-02 (the five service-layer functions exposed in folder_service.py). Maps to .planning/phases/03-folder-service-routers-dedup-extension/03-VALIDATION.md row "3-02-* | 02 (folder_service extensions) | 2 | FOLDER-02 | T-path-traversal (Pitfall 4)".

Verification steps:
- AST parse + grep gates confirm all five new function definitions exist with their canonical names.
- normalize_path() is preserved (Phase 1 / Plan 01 contract).
- Inline self-tests run successfully (15 cases pass via `python -m app.services.folder_service`).
- All three Migration 019 RPC names appear verbatim in the file (rpc("rename_folder_prefix", ...), etc.).
- Plan 06's test_folders.py imports the five functions and asserts they are callable in the FOLDER-02 service-surface section.
</verification>

<success_criteria>
- folder_service.py exports five new public functions (FOLDER-02).
- Every path-accepting function calls normalize_path() as its first statement (Pitfall 4 chokepoint).
- rename_folder, delete_folder, create_folder are thin wrappers around Migration 019's RPCs.
- list_folder returns the {documents, subfolders, path} shape Plan 04's router expects.
- move_document filters .eq('user_id', user_id) for cross-user defense in depth.
- No FastAPI imports added (pure service module).
- Inline self-tests still pass via `python -m app.services.folder_service`.
- Module imports cleanly; Plan 04's router can use the new functions immediately after this plan completes.
</success_criteria>

<output>
After completion, create `.planning/phases/03-folder-service-routers-dedup-extension/03-02-SUMMARY.md` recording: file modified (folder_service.py), the five new functions added, their RPC mappings (rename -> rename_folder_prefix, delete -> delete_folder_if_empty, create -> create_folder_if_not_exists), the line count delta (+~120 lines), and a one-line confirmation that Plans 04 and 05 are unblocked.
</output>
