---
phase: 02
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - backend/app/routers/files.py
  - backend/migrations/018_storage_rls.sql
autonomous: true
requirements: []
must_haves:
  truths:
    - "A new file uploaded to POST /api/files/upload has its original bytes persisted to Supabase Storage at documents/{user_id}/{document_id}{ext} BEFORE the Docling ingest background task is scheduled"
    - "Storage upload failure is non-fatal — the ingest path still runs and the document still reaches status='ready' even if Storage was unavailable (per the 'non-fatal' convention from ingestion.py:407-408, 444-450)"
    - "Both the action='create' branch and the action='update' branch in upload_file() perform the Storage upload (so re-uploads of an existing file overwrite the blob via upsert)"
    - "The Storage path is computed deterministically as f'{user_id}/{doc[\"id\"]}{ext}' — NOT persisted as a documents column (per CONTEXT.md §LOCKED—Storage Gap: 'recommend computed-from-id to avoid a migration')"
    - "Migration 018 adds two RLS policies on storage.objects scoped to bucket_id='documents' so authenticated users can SELECT and INSERT only inside their own {auth.uid()}/ folder"
    - "Service-role bypasses storage.objects RLS automatically (no explicit grant needed) — backfill_content_markdown.py and the FastAPI server's get_supabase_client() both use service-role and can download/upload any blob"
    - "Migration 018 is idempotent (DROP POLICY IF EXISTS … then CREATE POLICY … — same pattern as migration 015)"
    - "Bucket creation (INSERT INTO storage.buckets) is documented in the migration header as a one-time Supabase Studio task, NOT performed by the migration SQL"
  artifacts:
    - path: "backend/app/routers/files.py"
      provides: "Storage upload at multipart-parse time for both action='create' and action='update' branches; per-doc path documents/{user_id}/{doc_id}{ext}"
      contains: "supabase.storage.from_(\"documents\").upload"
      contains_2: "os.path.splitext"
      min_lines: 150
    - path: "backend/migrations/018_storage_rls.sql"
      provides: "Two RLS policies on storage.objects (SELECT + INSERT) scoped to bucket_id='documents' and per-user foldername; documents the one-time bucket-creation Studio task"
      contains: "documents_storage_select_own"
      contains_2: "documents_storage_insert_own"
      contains_3: "bucket_id = 'documents'"
      contains_4: "storage.foldername(name)"
      min_lines: 25
  key_links:
    - from: "backend/app/routers/files.py upload_file()"
      to: "Supabase Storage bucket 'documents'"
      via: "supabase.storage.from_('documents').upload(path, contents, file_options={'content-type': mime_type, 'upsert': 'true'})"
      pattern: "supabase\\.storage\\.from_\\(\"documents\"\\)\\.upload"
    - from: "backend/scripts/backfill_content_markdown.py (Plan 03)"
      to: "Supabase Storage bucket 'documents'"
      via: "supabase.storage.from_('documents').download(f'{user_id}/{doc_id}{ext}') — same path formula as the upload"
      pattern: "computed-from-id storage path"
    - from: "Migration 018 RLS policies"
      to: "Phase 6 file-explorer UI (per UI-08, future: surface 'needs re-index' badge, possibly fetch blobs)"
      via: "authenticated-role SELECT policy on storage.objects WHERE bucket_id='documents' AND auth.uid()::text = (storage.foldername(name))[1]"
      pattern: "documents_storage_select_own"
---

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Multipart upload (HTTP request) -> FastAPI process | Untrusted file bytes + filename + claimed MIME type cross here from authenticated user |
| FastAPI service-role client -> Supabase Storage | Storage upload uses service-role (bypasses storage.objects RLS); path is server-computed from `auth.uid()`-derived `user_id` (NOT trusted client input) |
| Other authenticated users -> storage.objects | Per-user RLS on storage.objects must restrict SELECT to a user's own folder; cross-user blob access would be PII / data exfiltration |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-2-01 | Information Disclosure / Tampering | Storage path computation | mitigate | Storage path is computed server-side as `f"{user_id}/{doc['id']}{ext}"` where `user_id` comes from `Depends(get_current_user)` (JWT-validated) and `doc['id']` is a freshly-INSERTed UUID under the row's own user_id. The user cannot inject a `user_id` segment via filename, MIME, or any HTTP field. The extension comes from `os.path.splitext(file.filename)` — even if hostile (e.g. `../etc/passwd`), `os.path.splitext` only returns the trailing extension token; the upload path is constructed as `f"{user_id}/{doc_id}{ext}"` which keeps `{user_id}/` as the literal first segment regardless of what `ext` contains (Storage rejects path-traversal characters anyway). |
| T-2-02 | Information Disclosure | storage.objects cross-user read | mitigate | Migration 018 adds `documents_storage_select_own` RLS policy: `bucket_id = 'documents' AND auth.uid()::text = (storage.foldername(name))[1]`. Service-role bypasses RLS for backend operations; authenticated frontend reads (Phase 6 future) get only their own folder. INSERT policy mirrors the same predicate so a non-admin cannot upload into another user's folder via the Storage REST API. |
| T-2-03 | Denial of Service | Storage upload failure cascades to ingest failure | mitigate | Storage upload is wrapped in try/except and logged as `warning` (non-fatal) per the existing `ingestion.py:407-408` convention (matches pattern called out in PATTERNS.md §"Error handling — non-fatal pattern"). Ingest proceeds even if Storage is unavailable; only the future re-index path is impacted. |
| T-2-04 | Tampering / Operational | Bucket creation pre-condition | accept | Bucket creation (`INSERT INTO storage.buckets ('documents', 'documents', false)`) is documented in the migration header as a **one-time Supabase Studio task**. The migration does NOT create the bucket (Studio is the canonical surface for bucket-level config per Supabase). If the bucket is missing, every Storage upload fails with a non-fatal warning — backfill (Plan 03) detects this state and surfaces it via `requires_user_reupload`, never silent. |
</threat_model>

<objective>
Close the foundational Storage Gap surfaced by RESEARCH.md §2: Episode 1 ingestion never persisted original blobs, leaving zero recoverable source for any future re-index. This plan adds Supabase Storage upload at multipart-parse time in `files.py` (per CONTEXT.md §LOCKED—Storage Gap Option A: "upload to documents/{user_id}/{document_id}{ext} BEFORE Docling parsing begins") and ships Migration 018 with the per-user RLS policies on `storage.objects`. No existing column is added (path is computed-from-id per CONTEXT.md). This plan unblocks Plan 03 (backfill script) which downloads blobs via the inverse operation. Both `action='create'` and `action='update'` branches must perform the upload; failure is non-fatal so the existing ingest contract is preserved.
</objective>

<execution_context>
@.claude/get-shit-done/workflows/execute-plan.md
@.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md

@.planning/phases/02-content-markdown-backfill-gated/02-CONTEXT.md
@.planning/phases/02-content-markdown-backfill-gated/02-RESEARCH.md
@.planning/phases/02-content-markdown-backfill-gated/02-PATTERNS.md
@.planning/research/PITFALLS.md
@.planning/codebase/CONVENTIONS.md
@.planning/codebase/INTEGRATIONS.md
@CLAUDE.md

@backend/app/routers/files.py
@backend/app/auth.py
@backend/migrations/015_two_scope_rls.sql

<interfaces>
<!-- Contracts this plan creates that downstream plans (03, 04) consume.
     Executor must implement exactly these signatures and path formulas. -->

Storage path formula (DETERMINISTIC — Plan 03 backfill MUST use the identical formula):
```python
import os
ext = os.path.splitext(file_name)[1]   # includes leading dot, e.g. '.pdf' or '' if no extension
storage_path = f"{user_id}/{doc['id']}{ext}"
# Example: "550e8400-e29b-41d4-a716-446655440000/6ba7b810-9dad-11d1-80b4-00c04fd430c8.pdf"
```

Storage upload call (verified signature per PATTERNS.md from
backend/venv/Lib/site-packages/storage3/_sync/file_api.py:574):
```python
supabase.storage.from_("documents").upload(
    storage_path,
    contents,                                          # bytes from `await file.read()`
    file_options={"content-type": mime_type, "upsert": "true"},
)
```

Storage download call (Plan 03 will use this — included here so downstream sees the contract):
```python
blob_bytes: bytes = supabase.storage.from_("documents").download(storage_path)
```

Bucket name: `"documents"` (singular bucket; multi-tenant via path prefix `{user_id}/`).

RLS policy invariants (added by Migration 018):
- Bucket: `documents` (private; created via Supabase Studio one-time task)
- SELECT policy `documents_storage_select_own`: authenticated, USING `bucket_id='documents' AND auth.uid()::text = (storage.foldername(name))[1]`
- INSERT policy `documents_storage_insert_own`: authenticated, WITH CHECK same predicate
- Service-role bypasses both policies automatically (no explicit grant needed)
</interfaces>
</context>

<tasks>

<task id="2-01-01" type="auto">
  <name>Task 1: Add Supabase Storage upload to files.py upload_file() — both create and update branches</name>
  <files>backend/app/routers/files.py</files>
  <read_first>
    - backend/app/routers/files.py (the file being modified — lines 30-96; the action='create' branch is L76-96, action='update' branch is L51-74; bytes are captured at L37 via `contents = await file.read()`; the supabase service-role client is at L36 via `get_supabase_client()`)
    - backend/app/services/ingestion.py L407-408 and L444-450 (the canonical "non-fatal pattern" — `try: … except Exception as e: logger.warning(f"… (non-fatal) for {document_id}: {e}")`; per PATTERNS.md §"Error handling — non-fatal pattern" Storage upload follows this exact shape)
    - .planning/phases/02-content-markdown-backfill-gated/02-CONTEXT.md §LOCKED—Storage Gap Resolution (lines ~24-30 — directs the upload location: "pick the location that holds the in-memory bytes immediately after the multipart parse"; computed-from-id path; bucket name 'documents')
    - .planning/phases/02-content-markdown-backfill-gated/02-PATTERNS.md §"backend/app/routers/files.py" (lines ~418-505 — paste-ready code excerpt for the action='create' branch insertion; documents the verified storage3 SDK signature and upsert='true' rationale)
    - .planning/phases/02-content-markdown-backfill-gated/02-RESEARCH.md §"Decision §2 — Storage Gap" (lines ~755-810 — Option A rationale; pre-Phase-2 docs accepted as requires_user_reupload)
    - .planning/codebase/CONVENTIONS.md (logging: `logger = logging.getLogger(__name__)` module-level; type hints on signatures; non-fatal pattern uses `logger.warning`)
    - CLAUDE.md (no LangChain/LangGraph; the Storage upload is a raw supabase-py call, not a higher-level wrapper)
  </read_first>
  <action>
    Modify `backend/app/routers/files.py` to upload the original blob to Supabase Storage immediately after the multipart parse, in both the `action='create'` and `action='update'` branches. The Storage upload MUST occur BEFORE the `background_tasks.add_task(_throttled_ingest, …)` call so the blob is persisted regardless of whether Docling later succeeds or fails. Failure is non-fatal (per PATTERNS.md §"Error handling — non-fatal pattern" and the existing convention from `ingestion.py:407-408, 444-450`).

    Step 1: Add `import os` and `import logging` to the top of the file (next to the existing `import threading`). The file currently has no `import os` and only an inline `import logging` inside the `_throttled_ingest` function — promote it to module-level for use by the new Storage upload code.

    Step 2: Add a module-level logger immediately after the imports:

    ```python
    logger = logging.getLogger(__name__)
    ```

    Step 3: Add a private helper function `_upload_to_storage()` after `_throttled_ingest` (around line 27) and before the `@router.post("/upload", …)` decorator. This factors the try/except wrapper out of the two branches:

    ```python
    def _upload_to_storage(supabase, user_id: str, document_id: str, file_name: str,
                           contents: bytes, mime_type: str) -> None:
        """Persist the original blob to Supabase Storage so future re-indexing
        (Phase 2 backfill_content_markdown.py + any future re-Docling pass) can
        recover it. Path: documents/{user_id}/{document_id}{ext}.

        Failure is NON-FATAL — the ingest path still runs and the document still
        reaches status='ready' even if Storage is unavailable. Plan 03's backfill
        marks rows whose blobs are missing as 'requires_user_reupload' (per CONTEXT.md
        §LOCKED—Backfill scope reframe).
        """
        ext = os.path.splitext(file_name)[1]   # includes leading dot, e.g. '.pdf' or ''
        storage_path = f"{user_id}/{document_id}{ext}"
        try:
            supabase.storage.from_("documents").upload(
                storage_path,
                contents,
                file_options={"content-type": mime_type, "upsert": "true"},
            )
            logger.info(
                f"Storage upload OK: doc={document_id} path={storage_path} bytes={len(contents)}"
            )
        except Exception as e:
            logger.warning(
                f"Storage upload failed (non-fatal) for {document_id} path={storage_path}: {e}"
            )
    ```

    Step 4: In the `action='update'` branch (currently L51-74), insert a call to `_upload_to_storage` AFTER the doc-fetch (`doc = supabase.table("documents").select("*") … .execute().data`, currently L60-61) and BEFORE `background_tasks.add_task(_throttled_ingest, ingest_document_update, …)` (currently L63):

    ```python
    _upload_to_storage(
        supabase,
        user_id=user_id,
        document_id=doc["id"],
        file_name=file_name,
        contents=contents,
        mime_type=mime_type,
    )
    ```

    Step 5: In the `action='create'` branch (currently L76-96), insert the same call AFTER the doc-insert (`doc = supabase.table("documents").insert({…}).execute().data[0]`, currently L77-83) and BEFORE `background_tasks.add_task(_throttled_ingest, ingest_document, …)` (currently L85):

    ```python
    _upload_to_storage(
        supabase,
        user_id=user_id,
        document_id=doc["id"],
        file_name=file_name,
        contents=contents,
        mime_type=mime_type,
    )
    ```

    Conventions to honor:
    - The Storage path uses `os.path.splitext(file_name)[1]` (includes the leading dot). Files without an extension (e.g. `Makefile`) get an empty `ext`, producing path `{user_id}/{doc_id}` (no trailing dot). This is correct.
    - `upsert='true'` is REQUIRED on the update branch (re-uploaded file overwrites the same path); harmless on the create branch (it's a new path).
    - Pass `mime_type` from the existing local variable (already on L39: `mime_type = file.content_type or "application/octet-stream"`). Do NOT re-derive it.
    - Logger is module-level. No inline `import logging` inside `_upload_to_storage` (clean up the existing inline import in `_throttled_ingest` if it would otherwise become unused — leave it alone if removing it adds risk; the duplicate-import is benign).
    - Do NOT add a `documents.storage_path` column (per CONTEXT.md §LOCKED—Storage Gap: "computed-from-id to avoid a migration"). Plan 03 reconstructs the path from `(user_id, id, file_name)` at lookup time using the identical `os.path.splitext` formula.
    - Do NOT change the `action == 'skip'` branch (L45-49) — it returns the existing doc without re-uploading. The blob is already in Storage from a previous upload (or absent if pre-Phase-2; that's fine).

    Do NOT:
    - Add LangChain or LangGraph (project rule).
    - Use a background task for the Storage upload (it must complete before the ingest background task is scheduled, per CONTEXT.md "BEFORE Docling parsing begins").
    - Raise on Storage failure (non-fatal — the warning log is the only signal).
    - Persist `storage_path` to the documents table (computed-from-id contract).
    - Touch the GET /api/files endpoints, the DELETE endpoint, or the `_throttled_ingest` helper.
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -c "import ast, pathlib; src = pathlib.Path('app/routers/files.py').read_text(encoding='utf-8'); ast.parse(src); assert 'def _upload_to_storage' in src, 'helper missing'; assert src.count('_upload_to_storage(') &gt;= 3, f'expected 1 def + 2 call sites, got {src.count(\"_upload_to_storage(\")}'; assert 'supabase.storage.from_(\"documents\").upload' in src, 'storage upload call missing'; assert 'os.path.splitext' in src, 'splitext missing'; assert 'upsert' in src.lower(), 'upsert option missing'; assert src.count('background_tasks.add_task') == 2, f'expected 2 add_task calls (create + update), got {src.count(\"background_tasks.add_task\")}'; assert 'logger.warning' in src and 'non-fatal' in src.lower(), 'non-fatal warning log missing'; assert 'import os' in src and 'import logging' in src, 'module-level imports missing'; print('files.py structure OK')"</automated>
  </verify>
  <acceptance_criteria>
    - File `backend/app/routers/files.py` parses as valid Python (`ast.parse` succeeds).
    - `grep -c "def _upload_to_storage" backend/app/routers/files.py` returns exactly 1.
    - `grep -c "_upload_to_storage(" backend/app/routers/files.py` returns at least 3 (1 def + 2 call sites).
    - `grep -c "supabase.storage.from_(\"documents\").upload" backend/app/routers/files.py` returns at least 1.
    - `grep -c "os.path.splitext" backend/app/routers/files.py` returns at least 1.
    - `grep -c "background_tasks.add_task" backend/app/routers/files.py` returns exactly 2 (one for `ingest_document_update`, one for `ingest_document` — unchanged from before).
    - `grep -E "logger\\.warning.*non-fatal" backend/app/routers/files.py` returns at least 1 match.
    - `grep -c "^import os$" backend/app/routers/files.py` returns 1 (module-level os import).
    - `grep -c "^import logging$" backend/app/routers/files.py` returns 1 (module-level logging import).
    - `grep -E "from\\s+(langchain|langgraph)" backend/app/routers/files.py` returns no matches (CLAUDE.md: no LangChain/LangGraph).
    - `grep -c "storage_path" backend/app/routers/files.py` returns 0 OR only inside `_upload_to_storage` (no `documents` table UPDATE writing storage_path — computed-from-id contract per CONTEXT.md §LOCKED—Storage Gap).
    - `grep -E "supabase.table\\(.documents.\\).*storage_path" backend/app/routers/files.py` returns no matches (no DB column named storage_path is being written).
    - `cd backend && venv/Scripts/python -c "from app.routers.files import _upload_to_storage; import inspect; sig = inspect.signature(_upload_to_storage); params = list(sig.parameters); assert params == ['supabase', 'user_id', 'document_id', 'file_name', 'contents', 'mime_type'], f'signature mismatch: {params}'; print('signature OK')"` prints "signature OK".
    - `cd backend && venv/Scripts/python -c "from app.routers.files import upload_file, list_files, delete_file; print('router imports OK')"` prints "router imports OK" (router still loads).
  </acceptance_criteria>
  <done>
    `_upload_to_storage` helper exists and is called in BOTH the `action='create'` and `action='update'` branches of `upload_file()`. Calls happen BEFORE `background_tasks.add_task` so the blob lands first. Storage path is computed as `f"{user_id}/{document_id}{ext}"` with `ext = os.path.splitext(file_name)[1]`. Failure is logged as warning and does not raise. No `documents.storage_path` column is written. Module loads and the public router functions still import cleanly.
  </done>
</task>

<task id="2-01-02" type="auto">
  <name>Task 2: Write migration 018 — RLS policies on storage.objects for the 'documents' bucket</name>
  <files>backend/migrations/018_storage_rls.sql</files>
  <read_first>
    - backend/migrations/015_two_scope_rls.sql (the canonical RLS policy migration analog — header comment shape, DROP POLICY IF EXISTS … then CREATE POLICY pattern, snake_case policy naming convention `<table>_<operation>_<scope>`, `TO authenticated` role qualifier, USING for SELECT/UPDATE/DELETE, WITH CHECK for INSERT, the `(SELECT auth.uid())` perf-cached pattern)
    - backend/migrations/014_content_markdown_column.sql (header-comment shape: "-- Phase X / Migration NNN: <purpose>" + 4-6 lines of context including any new conventions called out for reviewers)
    - .planning/phases/02-content-markdown-backfill-gated/02-PATTERNS.md §"backend/migrations/018_storage_rls.sql" (lines ~296-347 — paste-ready DDL for both policies; documents the bucket-creation-via-Studio convention)
    - .planning/phases/02-content-markdown-backfill-gated/02-CONTEXT.md §LOCKED—Storage Gap Resolution (lines ~24-30 — bucket name 'documents'; service-role bypass; the "Migration 017 is the carry-forward slot — DO NOT bundle Storage into 017; create 018" directive)
    - .planning/phases/02-content-markdown-backfill-gated/02-RESEARCH.md §"Pitfall 5: Storage bucket policy blocks service-role download" (lines ~591-601 — verification step that backfill script must run a canary download)
    - CLAUDE.md ("All tables need Row-Level Security"; "NEVER DROP TABLE on tables that hold user data" — N/A here since we're only adding policies, not dropping tables)
  </read_first>
  <action>
    Create `backend/migrations/018_storage_rls.sql` with the EXACT SQL below. This adds two RLS policies on `storage.objects` scoped to `bucket_id='documents'` so authenticated users can only SELECT and INSERT blobs inside their own `{auth.uid()}/` folder. Service-role bypasses RLS automatically (used by `get_supabase_client()` in the FastAPI process and by Plan 03's backfill script). Bucket creation is documented in the header as a Supabase Studio one-time task, NOT performed by this migration (Studio is canonical for bucket-level config; bucket settings include public/private toggle, MIME allowlist, file-size limits — these belong in Studio, not SQL).

```sql
-- Phase 2 / Migration 018: Supabase Storage RLS for the 'documents' bucket.
-- Adds two policies on storage.objects gating per-user access to original
-- document blobs uploaded by ingest_document() (Phase 2 Storage Gap mitigation
-- per .planning/phases/02-content-markdown-backfill-gated/02-CONTEXT.md
-- §LOCKED—Storage Gap Resolution). Closes the foundational gap surfaced by
-- 02-RESEARCH.md §2: Episode 1 ingest discarded original bytes, leaving zero
-- recoverable source for any future re-Docling pass. Plan 03's
-- backfill_content_markdown.py downloads blobs via the inverse of the upload
-- path documents/{user_id}/{document_id}{ext} added in plan 01 / files.py.
--
-- ONE-TIME SETUP (NOT performed by this migration — operator action via
-- Supabase Studio): create the 'documents' bucket as PRIVATE before deploying
-- the Phase 2 application code. Studio path:
--   Storage -> Create bucket -> Name: documents, Public: OFF, File size limit: 50MB.
-- Equivalent SDK call (one-time, from a Python REPL using the service-role key):
--   supabase.storage.create_bucket("documents", options={"public": False})
-- Migration SQL is the wrong layer for bucket creation (bucket-level config
-- includes MIME allowlists and size limits that don't belong in DDL).
--
-- Path convention: documents/{user_id}/{document_id}{ext}
-- The user_id is at storage.foldername(name)[1] (Postgres array; 1-indexed),
-- enabling the auth.uid()::text = (storage.foldername(name))[1] RLS predicate
-- below. Mirrors the Phase 1 / Migration 015 convention of wrapping auth.uid()
-- as a perf-cached subquery (`(SELECT auth.uid())`) — see 01-PLAN.md plan 05.
--
-- Service-role automatically bypasses storage.objects RLS — both the FastAPI
-- server's get_supabase_client() (auth.py:8-12) and the backfill script
-- (Plan 03) use service-role and can read/write any blob without RLS friction.

-- SELECT policy: authenticated users can read only blobs in their own folder.
-- Idempotent via DROP POLICY IF EXISTS (matches migration 015 convention).
DROP POLICY IF EXISTS "documents_storage_select_own" ON storage.objects;
CREATE POLICY "documents_storage_select_own"
  ON storage.objects FOR SELECT
  TO authenticated
  USING (
    bucket_id = 'documents'
    AND (SELECT auth.uid())::text = (storage.foldername(name))[1]
  );

-- INSERT policy: authenticated users can write only into their own folder.
-- Defense in depth alongside the server-side path computation in
-- backend/app/routers/files.py::_upload_to_storage (path is built from the
-- JWT-validated user_id, so a malicious client cannot inject another user's
-- folder via filename — but RLS catches it even if app code regresses).
DROP POLICY IF EXISTS "documents_storage_insert_own" ON storage.objects;
CREATE POLICY "documents_storage_insert_own"
  ON storage.objects FOR INSERT
  TO authenticated
  WITH CHECK (
    bucket_id = 'documents'
    AND (SELECT auth.uid())::text = (storage.foldername(name))[1]
  );

-- No UPDATE / DELETE policies for the authenticated role this phase. Blobs are
-- write-once from the user's perspective (re-upload uses upsert=true which is
-- an INSERT with overwrite semantics). Service-role handles backend-side
-- delete-on-document-delete in a future phase.
```

    Conventions to honor (per migration 015 + 014 patterns):
    - Filename `018_storage_rls.sql`.
    - Header comment block: 4-6 lines of context PLUS the "ONE-TIME SETUP" call-out (this migration warrants more context than usual because the bucket-creation-in-Studio split is a NEW convention for this codebase — call it out explicitly so reviewers and future operators know the intent).
    - snake_case policy naming `<table>_<operation>_<scope>`: `documents_storage_select_own`, `documents_storage_insert_own` (mirrors plan 05's `documents_select`, `documents_insert_user`, etc.; see STATE.md Decisions §"Phase 1 / Plan 05" line 81).
    - Quoted policy names (`"documents_storage_select_own"`) — matches migration 015's quoting style (preserves case in pg_policy, which is helpful for diffing).
    - `TO authenticated` role qualifier on every policy (matches every policy in 015).
    - `USING (...)` for SELECT; `WITH CHECK (...)` for INSERT.
    - Wrap `auth.uid()` as `(SELECT auth.uid())` per the perf-cached pattern from Plan 1 / Plan 05 / migration 015 (see STATE.md Decisions line 82).
    - `(storage.foldername(name))[1]` is a Postgres function that returns the path components as a TEXT[] (1-indexed). For `'<uuid>/<file>.pdf'` it returns `{<uuid>, <file>.pdf}` so index 1 is the user UUID — exactly what we want to compare against `auth.uid()::text`.
    - All DDL idempotent via `DROP POLICY IF EXISTS` immediately before `CREATE POLICY` (matches migration 015 convention; see plan 05's design notes in STATE.md line 81).
    - No `BEGIN`/`COMMIT` (run_migrations.py wraps each migration in a transaction).
    - No `ALTER TABLE storage.objects ENABLE ROW LEVEL SECURITY` — Supabase already enables RLS on `storage.objects` by default; calling `ENABLE` again would be a no-op but might require ownership we don't have.

    Do NOT:
    - Create the bucket via SQL (`INSERT INTO storage.buckets …` is allowed but conventionally lives in Studio; the header documents this).
    - Add UPDATE or DELETE policies for the authenticated role this phase (out of scope; service-role handles backend deletes).
    - Reference any column from `public.documents` (this migration touches `storage.objects` only).
    - Use `USING (true)` or `WITH CHECK (true)` (would defeat the per-user isolation purpose).
    - Add `CONCURRENTLY` (not applicable to policies).
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -c "sql = open('migrations/018_storage_rls.sql', encoding='utf-8').read(); assert sql.startswith('-- Phase 2 / Migration 018'), 'header missing'; assert 'DROP POLICY IF EXISTS \"documents_storage_select_own\"' in sql, 'select drop missing'; assert 'CREATE POLICY \"documents_storage_select_own\"' in sql, 'select create missing'; assert 'DROP POLICY IF EXISTS \"documents_storage_insert_own\"' in sql, 'insert drop missing'; assert 'CREATE POLICY \"documents_storage_insert_own\"' in sql, 'insert create missing'; assert sql.count('TO authenticated') &gt;= 2, f'expected 2 TO authenticated, got {sql.count(\"TO authenticated\")}'; assert sql.count(\"bucket_id = 'documents'\") &gt;= 2, 'bucket_id predicate missing'; assert sql.count('storage.foldername(name)') &gt;= 2, 'foldername predicate missing'; assert '(SELECT auth.uid())' in sql, 'perf-cached auth.uid() missing'; assert 'BEGIN;' not in sql.upper() and 'COMMIT;' not in sql.upper(), 'transaction wrapper forbidden'; assert 'CONCURRENTLY' not in sql.upper(), 'concurrently not applicable to policies'; print('migration 018 structure OK')"</automated>
  </verify>
  <acceptance_criteria>
    - File `backend/migrations/018_storage_rls.sql` exists.
    - File starts with comment line `-- Phase 2 / Migration 018: Supabase Storage RLS for the 'documents' bucket.`.
    - `grep -c "DROP POLICY IF EXISTS \"documents_storage_select_own\"" backend/migrations/018_storage_rls.sql` returns 1.
    - `grep -c "CREATE POLICY \"documents_storage_select_own\"" backend/migrations/018_storage_rls.sql` returns 1.
    - `grep -c "DROP POLICY IF EXISTS \"documents_storage_insert_own\"" backend/migrations/018_storage_rls.sql` returns 1.
    - `grep -c "CREATE POLICY \"documents_storage_insert_own\"" backend/migrations/018_storage_rls.sql` returns 1.
    - `grep -c "TO authenticated" backend/migrations/018_storage_rls.sql` returns at least 2.
    - `grep -c "bucket_id = 'documents'" backend/migrations/018_storage_rls.sql` returns at least 2.
    - `grep -c "storage.foldername(name)" backend/migrations/018_storage_rls.sql` returns at least 2.
    - `grep -c "(SELECT auth.uid())" backend/migrations/018_storage_rls.sql` returns at least 2.
    - `grep -c "FOR SELECT" backend/migrations/018_storage_rls.sql` returns 1.
    - `grep -c "FOR INSERT" backend/migrations/018_storage_rls.sql` returns 1.
    - `grep -iE "ON storage\\.objects" backend/migrations/018_storage_rls.sql` returns at least 4 (2 DROP + 2 CREATE).
    - `grep -iE "(BEGIN|COMMIT);" backend/migrations/018_storage_rls.sql` returns no matches.
    - `grep -c "CONCURRENTLY" backend/migrations/018_storage_rls.sql` returns 0.
    - `grep -iE "DROP TABLE|TRUNCATE|DELETE FROM" backend/migrations/018_storage_rls.sql` returns no matches (CLAUDE.md rule).
    - `grep -iE "INSERT INTO storage\\.buckets" backend/migrations/018_storage_rls.sql` returns no matches (bucket creation is a Studio task per header).
    - Python sanity check in `<verify>` exits 0 and prints "migration 018 structure OK".
  </acceptance_criteria>
  <done>
    Migration 018 SQL written: idempotent, two policies on `storage.objects` for the `documents` bucket (SELECT + INSERT, both `TO authenticated`, both `auth.uid()::text = (storage.foldername(name))[1]`), perf-cached `(SELECT auth.uid())` per Phase 1 convention, header documents the one-time Studio bucket-creation task. Migration NOT yet applied (operator runs `run_migrations.py` separately; Plan 03's backfill includes a canary download check that surfaces if the bucket / policies are misconfigured).
  </done>
</task>

</tasks>

<verification>
This plan delivers the foundational Storage Gap closure (Option A from RESEARCH.md §2 / CONTEXT.md §LOCKED). It does not satisfy any BACKFILL-* requirement directly, but unblocks Plan 03 (which depends on the Storage upload contract this plan establishes) and Plan 04 (which integration-tests the full path).

Verification steps:
- Task 1: Python AST parse + grep gates confirm `_upload_to_storage` exists, is called in both branches of `upload_file()`, and follows the non-fatal pattern.
- Task 2: SQL structural verification confirms both policies present, idempotent shape, perf-cached `auth.uid()`, no destructive SQL.
- Operational verification (deferred to Plan 04 integration test): a successful upload through `POST /api/files/upload` results in a downloadable blob at `documents/{user_id}/{doc_id}{ext}` via service-role.
</verification>

<success_criteria>
- `backend/app/routers/files.py` performs Storage upload before scheduling the ingest background task in both the create and update branches.
- Storage upload failure is logged as warning (non-fatal) and does NOT block the ingest path.
- `backend/migrations/018_storage_rls.sql` exists with two policies idempotently dropping-and-creating, both scoped to `bucket_id='documents'` with the per-user foldername predicate.
- No `documents.storage_path` column is added (computed-from-id per CONTEXT.md).
- Service-role retains full Storage access (no policy restricts service-role).
</success_criteria>

<output>
After completion, create `.planning/phases/02-content-markdown-backfill-gated/02-01-SUMMARY.md` recording: files modified, Storage path formula `f"{user_id}/{doc_id}{ext}"` with `ext = os.path.splitext(file_name)[1]`, exactly which two lines in `files.py` got the new `_upload_to_storage` call, the bucket name `documents`, the two RLS policy names (`documents_storage_select_own`, `documents_storage_insert_own`), and the operational note that Migration 018 must be applied via `run_migrations.py` AND the `documents` bucket must be created in Supabase Studio before Plan 03's backfill is run.
</output>
</content>
</invoke>