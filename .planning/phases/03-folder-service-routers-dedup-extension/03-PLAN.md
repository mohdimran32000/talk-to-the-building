---
phase: 03
plan: 03
type: execute
wave: 2
depends_on: [01]
files_modified:
  - backend/app/services/record_manager.py
autonomous: true
requirements:
  - FOLDER-05
must_haves:
  truths:
    - "backend/app/services/record_manager.py determine_action() signature gains two new keyword arguments: scope: str = 'user' and folder_path: str = '/' (defaults preserve Phase 1/2 callers' behavior)"
    - "The dedup SELECT query is extended with .eq('scope', scope) AND .eq('folder_path', folder_path) — the dedup key is now (scope, user_id, folder_path, file_name) per FOLDER-05"
    - "For scope='user', the query uses .eq('user_id', user_id); for scope='global', the query uses .is_('user_id', 'null') — branching is explicit because COALESCE-equality (Migration 012's unique index trick) does NOT work on supabase-py SELECT filters (Pitfall A)"
    - "The dedup query benefits from the existing scope-aware unique index documents_scope_user_path_filename_unique (Migration 012:51-57) without requiring a new index — the .eq filter columns match the index column list in the same order"
    - "Existing call sites in backend/app/routers/files.py are NOT changed by this plan (Plan 05 owns the upload-handler edits that pass scope and folder_path); when called WITHOUT the new kwargs, determine_action is back-compat with Phase 1/2 behavior (defaults to scope='user', folder_path='/')"
    - "compute_file_hash and compute_chunk_hash are UNCHANGED (Phase 1 contract; not part of FOLDER-05)"
    - "The RecordAction dataclass is UNCHANGED (action='create'|'skip'|'update', document_id, message)"
    - "The function still returns RecordAction(action='create', ...) when no match found OR when supabase raises (preserved try/except shape from L42-51)"
    - "Module imports cleanly: `from app.services.record_manager import compute_file_hash, determine_action, RecordAction` succeeds in a venv Python -c smoke check"
    - "Calling determine_action with scope='user', user_id=<uuid>, folder_path='/a' AND a row exists with same hash at the same (scope, user_id, folder_path, file_name) returns action='skip'"
    - "Calling determine_action with scope='user', user_id=<uuid>, folder_path='/a' AND a row exists at folder_path='/b' (different path, same file_name) returns action='create' (the same file in two different folders is no longer a duplicate — FOLDER-05 acceptance)"
  artifacts:
    - path: "backend/app/services/record_manager.py"
      provides: "Extended determine_action() with scope-aware + folder-path-aware dedup key"
      contains: "scope: str = \"user\""
      contains_2: "folder_path: str = \"/\""
      contains_3: ".eq(\"scope\","
      contains_4: ".eq(\"folder_path\","
      contains_5: ".is_(\"user_id\", \"null\")"
  key_links:
    - from: "record_manager.determine_action() extended SELECT"
      to: "documents_scope_user_path_filename_unique unique index (Migration 012:51-57)"
      via: ".eq filter column list matches the index column list in the same order — Postgres uses the index for the lookup"
      pattern: ".eq\\(\"scope\".*\\.eq\\(\"folder_path\".*\\.eq\\(\"file_name\""
    - from: "record_manager.determine_action()"
      to: "backend/app/routers/files.py upload_file (Plan 05)"
      via: "determine_action(file_hash, file_name, user_id, supabase, scope=scope, folder_path=folder_path) — keyword args added by Plan 05"
      pattern: "scope=scope, folder_path=folder_path"
---

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Caller (Plan 05 upload handler) -> determine_action() | The caller has already normalized folder_path via normalize_path() (belt at the router); determine_action accepts the canonical form as-is |
| determine_action -> Postgres SELECT via supabase_client | Service-role bypasses RLS; defense in depth via .eq('scope',...).eq('folder_path',...) AND (when scope='user') .eq('user_id', user_id) — matches CONCERNS.md anti-pattern documentation |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-3-03-PitfallA | Information Disclosure / Tampering | dedup query missing the COALESCE-equivalent .is_(user_id, null) branch for scope='global' | mitigate | Pitfall A in 03-RESEARCH.md (line 880) — `.eq('user_id', user_id)` would NEVER match a global row's NULL user_id; the dedup falsely returns action='create' and the subsequent INSERT then fails on the unique index. Mitigation: explicit `if scope == 'user': .eq(user_id) else: .is_(user_id, null)` branch in this plan. The unique index uses `COALESCE` for write-time dedup; the SELECT must explicitly match the column's actual NULL state. |
| T-3-03-DedupBypass | Tampering | Caller passes folder_path='/foo/' (non-canonical) | mitigate | The caller (Plan 05 router) MUST normalize_path() before calling. determine_action does NOT re-normalize — that's the caller's contract. If a malformed value reaches the SELECT, the .eq('folder_path', '/foo/') matches zero rows (because the index stores canonical '/foo' only), forcing action='create'. The subsequent INSERT then fails on the DB CHECK constraint (Migration 012:40-42). Multi-layer defense; here we accept the risk that an unnormalized caller produces a clean 500 instead of a clean 400 (Plan 05's router MUST normalize first). |
| T-3-03-CrossUser | Information Disclosure | User A's upload sees User B's existing row | accept | The .eq('user_id', user_id) filter for scope='user' prevents cross-user matches. For scope='global', the .is_('user_id', 'null') filter prevents user-row leakage. Phase 1 RLS policies are the bedrock; this is the app-layer defense. Already covered by Phase 1 / Plan 08's test_two_scope_rls.py for the SELECT side; Plan 06's test_folders.py asserts the dedup behavior end-to-end. |
| T-3-03-CompatRegression | Operational | Existing Phase 1/2 callers break when this plan ships | mitigate | The new arguments are KEYWORD with defaults (scope='user', folder_path='/'). Existing callers (`backend/app/routers/files.py:73` for now) call determine_action positionally with 4 args (file_hash, file_name, user_id, supabase) — they continue to work, hitting the path of (scope='user', folder_path='/') which matches Phase 1/2 semantics for Episode-1-style root-folder uploads. Plan 05 explicitly upgrades the call site to pass scope=scope, folder_path=folder_path. |
</threat_model>

<objective>
Extend `backend/app/services/record_manager.py::determine_action()` with two new keyword arguments — `scope: str = 'user'` and `folder_path: str = '/'` — and update the SELECT query to filter by all four columns of the Phase 1 unique index `documents_scope_user_path_filename_unique` (scope, user_id, folder_path, file_name). This satisfies FOLDER-05: same file in two different folders is allowed (creates two rows); same file in same (scope, user_id, folder_path) is deduped.

The change is small (~10 net LOC) but security-relevant: Pitfall A (RESEARCH.md line 880) requires explicit branching between `.eq('user_id', user_id)` for scope='user' and `.is_('user_id', 'null')` for scope='global' because supabase-py's SELECT filter does NOT use COALESCE-equivalence (the unique index does, for write-time dedup; SELECTs must explicitly match the actual NULL state).

Existing call sites are NOT changed by this plan — defaults preserve Phase 1/2 behavior. Plan 05 owns the upgrade of `backend/app/routers/files.py:73`'s call site to pass the new kwargs. This plan and Plan 05 can land in either order safely; the keyword-arg-with-default design makes the change additive.
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

@backend/app/services/record_manager.py
@backend/migrations/012_folder_path_and_scope.sql

<interfaces>
<!-- The contract this plan ESTABLISHES — Plan 05's upload handler calls this. -->

determine_action(
    file_hash: str,
    file_name: str,
    user_id: str,
    supabase_client,
    scope: str = "user",          # NEW — 'user' | 'global'
    folder_path: str = "/",       # NEW — canonical (normalized BY CALLER)
) -> RecordAction
  Returns RecordAction(action='create'|'skip'|'update', document_id, message).

  Lookup logic (extended dedup key):
    1. Query documents WHERE scope = scope AND folder_path = folder_path AND file_name = file_name
       AND (user_id = user_id  if scope='user'  else  user_id IS NULL  if scope='global').
    2. If found and content_hash == file_hash: action='skip' (identical content; same dedup key).
    3. If found and content_hash != file_hash: action='update' (content changed; same dedup key).
    4. If not found OR query raises (maybe_single returns 204 -> exception): action='create' (new file).

  Caller responsibilities:
    - folder_path MUST be canonical (caller calls normalize_path() before this).
    - For scope='global', user_id is the JWT-derived calling user's id (used for tracing/logs only;
      the actual filter uses .is_('user_id', 'null') for global rows).
    - The supabase_client may be service-role or anon-with-JWT — the function does not care
      because the .eq filters are explicit; RLS adds defense in depth for non-service-role.

The RecordAction dataclass at L10-14 is UNCHANGED; compute_file_hash and compute_chunk_hash
at L17-25 are UNCHANGED.
</interfaces>
</context>

<tasks>

<task id="3-03-01" type="auto">
  <name>Task 1: Extend determine_action() with scope and folder_path kwargs and the corresponding SELECT filter branches</name>
  <files>backend/app/services/record_manager.py</files>
  <read_first>
    - backend/app/services/record_manager.py FULL FILE (the in-place edit point — determine_action at L27-69; entire function body changes; RecordAction at L10-14 and the two hash helpers at L17-25 are unchanged)
    - backend/migrations/012_folder_path_and_scope.sql L44-57 (the unique expression index `documents_scope_user_path_filename_unique` — column list (scope, COALESCE(user_id,'00..0'), folder_path, file_name); the dedup query MUST .eq filter on the SAME columns in the SAME order so Postgres uses the index)
    - .planning/phases/03-folder-service-routers-dedup-extension/03-RESEARCH.md §Dedup Key Extension (lines 235-323 — paste-ready function body with branching for scope='user' vs 'global')
    - .planning/phases/03-folder-service-routers-dedup-extension/03-RESEARCH.md §Pitfall A (line 880 — definitive explanation of why .eq vs .is_ must branch)
    - .planning/phases/03-folder-service-routers-dedup-extension/03-PATTERNS.md §`backend/app/services/record_manager.py` (paste-ready edit with inline comments)
    - backend/app/routers/files.py L73 (the existing call site — `determine_action(file_hash, file_name, user_id, supabase)` — Plan 03 must NOT break this positional call; the new args are kwargs with defaults)
  </read_first>
  <action>
    Modify `backend/app/services/record_manager.py` to extend `determine_action()` with two new keyword arguments and update the SELECT query. The RecordAction dataclass (L10-14), compute_file_hash (L17-19), and compute_chunk_hash (L22-24) are PRESERVED unchanged.

    Replace the entire body of `determine_action` (currently L27-68) with the version below. The existing function signature L27-32 grows from 4 parameters to 6 (the new ones are at the end with defaults). The docstring at L33-40 is rewritten to reflect the extended dedup key.

    ### Replacement function (paste-ready)

    ```python
    def determine_action(
        file_hash: str,
        file_name: str,
        user_id: str,
        supabase_client,
        scope: str = "user",          # NEW (Phase 3 / FOLDER-05) — 'user' | 'global'; default preserves Phase 1/2 behavior
        folder_path: str = "/",       # NEW (Phase 3 / FOLDER-05) — canonical path (normalized BY CALLER)
    ) -> RecordAction:
        """
        Check if this file has been ingested before.

        Logic (Phase 3 / FOLDER-05 dedup key — (scope, user_id, folder_path, file_name)):
        1. Look for existing doc with same (scope, user_id, folder_path, file_name).
           For scope='user' the user_id filter uses .eq(); for scope='global' it uses
           .is_('user_id', 'null') because supabase-py SELECT filters do NOT apply
           the COALESCE-equivalence trick that Migration 012's unique index uses for
           write-time dedup (Pitfall A in 03-RESEARCH.md).
        2. If found and same hash -> skip (identical content; same dedup key).
        3. If found and different hash -> update (content changed; same dedup key).
        4. If not found -> create (new file, OR same file at a different path/scope —
           which is allowed under FOLDER-05).

        The query benefits from the scope-aware unique index
        documents_scope_user_path_filename_unique from Migration 012:51-57 (the
        .eq filter columns match the index column list in the same order).

        Backwards compatibility: callers from Phase 1/2 that pass only the first
        4 positional args get scope='user' and folder_path='/', which matches
        Episode-1-style root-folder uploads. Plan 05's router upgrade explicitly
        passes the new kwargs.
        """
        try:
            query = (
                supabase_client.table("documents")
                .select("id, content_hash, status")
                .eq("scope", scope)
                .eq("folder_path", folder_path)
                .eq("file_name", file_name)
            )
            if scope == "user":
                query = query.eq("user_id", user_id)
            else:
                # global rows have user_id IS NULL per Migration 012 coupling CHECK.
                # .eq('user_id', user_id) would NEVER match (Pitfall A).
                query = query.is_("user_id", "null")
            result = query.maybe_single().execute()
        except Exception:
            # No match found (.maybe_single() returns 204 -> supabase-py raises),
            # or any other transient query error -> treat as a fresh create.
            return RecordAction(action="create", message="New document")

        if not result or not result.data:
            return RecordAction(action="create", message="New document")

        existing = result.data
        if existing["content_hash"] == file_hash:
            return RecordAction(
                action="skip",
                document_id=existing["id"],
                message="File content unchanged — skipping ingestion",
            )

        return RecordAction(
            action="update",
            document_id=existing["id"],
            message="File content changed — re-ingesting",
        )
    ```

    Critical DON'Ts:
    - DO NOT change the parameter ORDER for the first four positional args (file_hash, file_name, user_id, supabase_client). Existing callers (e.g., `backend/app/routers/files.py:73`) call them positionally; reordering breaks back-compat.
    - DO NOT remove the try/except wrapper around the .execute() call — `.maybe_single()` returns 204 when no row matches, and supabase-py raises on 204. Removing the try/except surfaces this as a 500 instead of a clean action='create'.
    - DO NOT add a new index. The existing `documents_scope_user_path_filename_unique` (Migration 012:51-57) is sufficient — `.eq` filters on the same columns in the same order let Postgres use the index for the lookup.
    - DO NOT call normalize_path() inside determine_action. Normalization is the CALLER's responsibility (belt at the router); the function accepts the canonical form as-is. Adding normalization here would duplicate work and violate the chokepoint principle (one canonical place).
    - DO NOT add `from app.services.folder_service import normalize_path` to record_manager — circular-import risk in the future and unnecessary right now.
    - DO NOT change RecordAction's fields. The dataclass at L10-14 is the contract for downstream callers (the upload handler reads `record_action.action`, `record_action.document_id`, `record_action.message`).
    - DO NOT add LangSmith `@traceable` (out of scope per CONVENTIONS.md — record_manager is a pure data-layer helper).
    - DO NOT change compute_file_hash or compute_chunk_hash. They're SHA-256 with no scope/path semantics; same input -> same output.
    - DO NOT use `.is_(user_id, "null")` for scope='user' (Pitfall A in reverse — would never find user rows).
    - DO NOT remove the message strings; downstream code (e.g., logs) reads them.
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -c "import pathlib, ast; src = pathlib.Path('app/services/record_manager.py').read_text(encoding='utf-8'); ast.parse(src); body = '\n'.join(line for line in src.splitlines() if not line.lstrip().startswith('#')); assert 'def determine_action(' in body, 'determine_action missing'; assert 'scope: str = \"user\"' in body or \"scope: str = 'user'\" in body, 'scope kwarg with default missing'; assert 'folder_path: str = \"/\"' in body or \"folder_path: str = '/'\" in body, 'folder_path kwarg with default missing'; assert '.eq(\"scope\",' in body, 'scope filter missing'; assert '.eq(\"folder_path\",' in body, 'folder_path filter missing'; assert '.eq(\"file_name\",' in body, 'file_name filter missing (existing)'; assert '.is_(\"user_id\", \"null\")' in body, 'global-scope NULL filter missing (Pitfall A)'; assert 'compute_file_hash' in body, 'compute_file_hash must remain'; assert 'compute_chunk_hash' in body, 'compute_chunk_hash must remain'; assert 'class RecordAction' in body, 'RecordAction dataclass must remain'; assert 'action=\"create\"' in body, 'create-action return path must remain'; assert 'action=\"skip\"' in body, 'skip-action return path must remain'; assert 'action=\"update\"' in body, 'update-action return path must remain'; assert 'from fastapi' not in src, 'record_manager must NOT import from fastapi'; assert '@traceable' not in src, '@traceable out of scope'; from app.services.record_manager import determine_action, compute_file_hash, RecordAction; import inspect; sig = inspect.signature(determine_action); params = list(sig.parameters.keys()); assert params[:4] == ['file_hash', 'file_name', 'user_id', 'supabase_client'], f'first 4 positional params must be unchanged for back-compat, got {params}'; assert 'scope' in params and 'folder_path' in params, f'scope and folder_path missing from signature, got {params}'; assert sig.parameters['scope'].default == 'user', 'scope default must be user'; assert sig.parameters['folder_path'].default == '/', 'folder_path default must be /'; print('record_manager.py determine_action OK')"</automated>
  </verify>
  <acceptance_criteria>
    - File `backend/app/services/record_manager.py` parses as valid Python (`ast.parse` succeeds).
    - `grep -c "^def determine_action(" backend/app/services/record_manager.py` returns 1.
    - `grep -c "^def compute_file_hash(" backend/app/services/record_manager.py` returns 1 (preserved).
    - `grep -c "^def compute_chunk_hash(" backend/app/services/record_manager.py` returns 1 (preserved).
    - `grep -c "^class RecordAction" backend/app/services/record_manager.py` returns 1 (preserved).
    - File contains `scope: str = "user"` (the new default kwarg).
    - File contains `folder_path: str = "/"` (the new default kwarg).
    - File contains `.eq("scope",` (new SELECT filter).
    - File contains `.eq("folder_path",` (new SELECT filter).
    - File contains `.is_("user_id", "null")` (Pitfall A mitigation for scope='global').
    - File contains `.eq("user_id", user_id)` (preserved for scope='user' branch).
    - File contains NO `from fastapi` import (pure service module).
    - File contains NO `@traceable` decorator (out of scope).
    - Module imports cleanly: `cd backend && venv/Scripts/python -c "from app.services.record_manager import determine_action, compute_file_hash, compute_chunk_hash, RecordAction; print('OK')"` prints `OK`.
    - Function signature has the first 4 parameters in their original order (file_hash, file_name, user_id, supabase_client) — verified via inspect.signature(). This preserves back-compat with `backend/app/routers/files.py:73`'s positional call.
    - Function signature has `scope` parameter with default `'user'` and `folder_path` parameter with default `'/'`.
    - Sanity: a Python smoke check confirms calling `determine_action('hash', 'file.txt', '<user>', <fake_sb_that_returns_no_data>)` (no kwargs) still returns `RecordAction(action='create', ...)` (back-compat path).
    - File length is approximately 70-90 lines (was 70; +5 to +20 for the docstring expansion + branching).
  </acceptance_criteria>
  <done>
    `backend/app/services/record_manager.py::determine_action()` extended with scope and folder_path kwargs (defaults preserve back-compat) and the matching SELECT-filter branching for scope='user' vs scope='global'. The function uses the existing scope-aware unique index from Migration 012 — no new index needed. Module imports cleanly. Plan 05 can now upgrade `backend/app/routers/files.py:73`'s call site to pass the new kwargs.
  </done>
</task>

</tasks>

<verification>
This plan delivers FOLDER-05 (record_manager dedup key extended to (scope, user_id, folder_path, file_name)). Maps to .planning/phases/03-folder-service-routers-dedup-extension/03-VALIDATION.md row "3-05-* | 05 (record_manager dedup) | 3 | FOLDER-05".

Verification steps:
- AST parse + grep gates confirm determine_action signature has the new kwargs and the body has the corrected filter branching.
- Runtime import + signature inspection confirms the first 4 params are unchanged (back-compat with the existing positional caller in files.py:73).
- Plan 06's test_folders.py exercises the FOLDER-05 dedup behavior end-to-end:
    a. Upload file F to /a -> action='create'.
    b. Upload SAME file F to /a (same dedup key) -> action='skip'.
    c. Upload SAME file F to /b (different folder_path; same scope, user_id, file_name) -> action='create' (FOLDER-05 acceptance).
- Plan 06 also asserts the global scope branch works (.is_('user_id', 'null') matches a fixture-inserted global doc).
</verification>

<success_criteria>
- determine_action gains scope='user' and folder_path='/' kwargs (defaults preserve back-compat).
- The SELECT query filters by (scope, user_id-or-null, folder_path, file_name) — the four columns of Migration 012's unique index, in order.
- For scope='global', the query uses .is_('user_id', 'null') (Pitfall A mitigation).
- compute_file_hash, compute_chunk_hash, RecordAction are unchanged.
- The existing call site at backend/app/routers/files.py:73 (4 positional args) continues to work without modification.
- Plan 05 unblocked to upgrade the call site to pass the new kwargs.
- Plan 06's FOLDER-05 dedup test (same file in two folders -> 2 docs; same path -> 1 doc) is empirically supported by this change.
</success_criteria>

<output>
After completion, create `.planning/phases/03-folder-service-routers-dedup-extension/03-03-SUMMARY.md` recording: file modified (record_manager.py), the extended dedup key column list, the branching pattern (.eq vs .is_), the LOC delta (~+10), confirmation that compute_file_hash + compute_chunk_hash + RecordAction are unchanged, and a one-line confirmation that FOLDER-05 is satisfied at the service layer (Plan 05's router upgrade is the second half).
</output>
