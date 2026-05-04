---
phase: 02
plan: 02
type: execute
wave: 1
depends_on: []
files_modified:
  - backend/app/services/ingestion.py
  - backend/requirements.txt
autonomous: true
requirements:
  - BACKFILL-01
must_haves:
  truths:
    - "ingest_document() in ingestion.py writes content_markdown=text AND content_markdown_status='ready' in the SAME UPDATE that flips status to 'ready' (atomic single-statement UPDATE per CONTEXT.md §specifics — no half-updated rows where status='ready' AND content_markdown_status='pending')"
    - "ingest_document_update() in ingestion.py applies the identical edit at its UPDATE-to-'ready' site (same shape, same atomicity guarantee)"
    - "If Docling extraction returns empty/whitespace text, the existing ValueError raises and the except branch fires; the except UPDATE writes content_markdown_status='failed' alongside status='failed' (so Phase 4 tools surface 'pending_reindex' rather than silently skip the row)"
    - "Both ingest_document() and ingest_document_update() apply the failure-path edit identically (same shape)"
    - "Synchronous in-pipeline write — NO new background_tasks.add_task call is added (Pitfall 2 mitigation: no atomicity-breaking deferred write)"
    - "The text variable is already in scope (returned by extract_text() at L395 / L471) — no re-extraction, no second Docling call, no new helper function"
    - "backend/requirements.txt pins docling==2.91.0 (the verified-currently-installed version per RESEARCH.md §Standard Stack) so synchronous-on-upload markdown is byte-equivalent to backfill markdown for the same blob (success criterion 4 depends on this)"
    - "No other dependency in requirements.txt is pinned (single-line edit; the other 12 deps remain unpinned per CONTEXT.md scope discipline)"
  artifacts:
    - path: "backend/app/services/ingestion.py"
      provides: "Synchronous content_markdown write inside ingest_document() and ingest_document_update(); failure-path content_markdown_status='failed' write in both functions' except blocks"
      contains: "content_markdown"
      contains_2: "content_markdown_status"
      contains_3: "ingest_document_update"
      min_lines: 520
    - path: "backend/requirements.txt"
      provides: "Pinned Docling version for byte-equivalence determinism"
      contains: "docling==2.91.0"
      min_lines: 13
  key_links:
    - from: "backend/app/services/ingestion.py::ingest_document UPDATE-to-ready (currently L437-439)"
      to: "documents.content_markdown column + documents.content_markdown_status column (added by Phase 1 / Migration 014)"
      via: "single supabase-py .update({...}) call carrying status='ready' + content_markdown=text + content_markdown_status='ready' atomically"
      pattern: "content_markdown.*ready.*content_markdown_status.*ready"
    - from: "backend/app/services/ingestion.py::ingest_document_update UPDATE-to-ready (currently L513-515)"
      to: "documents.content_markdown / content_markdown_status (same columns)"
      via: "identical atomic UPDATE shape — re-ingest path also captures markdown"
      pattern: "ingest_document_update"
    - from: "backend/requirements.txt docling==2.91.0 pin"
      to: "Plan 03 backfill_content_markdown.py + Plan 04 byte-equivalence test"
      via: "deterministic Docling output requires identical version across upload-time and backfill-time runs"
      pattern: "docling==2.91.0"
---

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Docling output (`text` variable) -> documents.content_markdown column | Untrusted in the sense that Docling may return arbitrary content from arbitrary file types; the column is TEXT and downstream Phase 4 tools (grep/read_document) must treat it as data, not as a trust signal |
| ingest_document() try-block -> except-block | Atomicity boundary: an exception raised between extract_text() and the final UPDATE leaves the row at status='processing'; Pitfall 2 mitigation requires that the success UPDATE be a single statement (no split between "write markdown" and "flip status") |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-2-05 | Tampering / Data Integrity | Atomic UPDATE on success | mitigate | The success UPDATE in `ingest_document` (and `ingest_document_update`) is a single supabase-py `.update({...})` call carrying ALL three transitions (`status='ready'`, `content_markdown=text`, `content_markdown_status='ready'`) in one PostgREST request. PostgREST translates this to a single SQL UPDATE statement — atomic by Postgres semantics. There is NO window where `status='ready' AND content_markdown_status='pending'`. Pitfall 2 from RESEARCH.md ("Background-tasking the synchronous write breaks the atomicity guarantee") is mitigated by NOT introducing any new `background_tasks.add_task` call — the patch lives entirely inside the existing function bodies. |
| T-2-06 | Information Disclosure / Silent Failure | Failure-path status surfacing | mitigate | When Docling raises (e.g. extracted text is empty per the existing L396-397 ValueError, or Docling's converter throws), the except block currently flips `documents.status='failed'`. The plan adds `content_markdown_status='failed'` to the SAME except-block UPDATE. This ensures Phase 4 tools (grep/read_document) surface a `{status: 'pending_reindex', content_markdown_status: 'failed'}` row per CONTEXT.md §LOCKED—Tool integration contract — never a silent empty result. Per RESEARCH.md Anti-Patterns: "if Docling fails, propagate the exception — both chunks and content_markdown fail together" (existing semantics preserved). |
| T-2-07 | Tampering / Determinism | Byte-equivalence of markdown across upload-time vs backfill-time | mitigate | `requirements.txt` pins `docling==2.91.0` (the verified-currently-installed version per RESEARCH.md §Standard Stack). Phase 2 success criterion 4 ("backfilled doc content_markdown is byte-equivalent ±20 chars to fresh Docling export of the same blob") is mathematically true if both calls use the same Docling version with the same options. The pin is the single point of determinism. RESEARCH.md Pitfall 6 documents the failure mode if this pin is omitted. |
</threat_model>

<objective>
Deliver BACKFILL-01: every new upload writes Docling's canonical markdown export to `documents.content_markdown` synchronously inside `ingest_document()` (and the parallel `ingest_document_update()` re-ingest path), in the SAME single UPDATE statement that flips `documents.status` to `'ready'`. The Docling export string is already computed by `extract_text()` and currently discarded after chunking — the patch captures it before discard and adds two key=value pairs to the existing success UPDATE. Failure path also gets `content_markdown_status='failed'` so Phase 4 tools surface non-ready rows correctly per CONTEXT.md §LOCKED—Tool integration contract. Pin `docling==2.91.0` in `requirements.txt` so the byte-equivalence success criterion (Phase 2 SC4) holds across upload-time vs. backfill-time runs of the same blob.

Per CONTEXT.md §LOCKED—Synchronous-on-upload: insertion point is `ingestion.py:437-439` (the existing UPDATE-to-'ready'); ~10 lines of edits; zero new background tasks, zero re-extraction, zero new helper functions.
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
@.planning/REQUIREMENTS.md
@CLAUDE.md

@backend/app/services/ingestion.py
@backend/migrations/014_content_markdown_column.sql
@.planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/04-PLAN.md

<interfaces>
<!-- The schema this plan writes to (added by Phase 1 / Migration 014):

documents:
  status                   TEXT  (existing — vocabulary 'pending' | 'processing' | 'ready' | 'failed')
  content_markdown         TEXT  (Phase 1 / 014 — nullable; this plan populates on upload success)
  content_markdown_status  TEXT NOT NULL DEFAULT 'pending'
                           CHECK IN ('pending','ready','failed','requires_user_reupload')
                           (Phase 1 / 014 — this plan transitions to 'ready' on success or 'failed' on except)

Status state machine for content_markdown_status (per CONTEXT.md §LOCKED—Status state machine):
  - On synchronous upload success: -> 'ready'      (this plan)
  - On synchronous upload failure: -> 'failed'     (this plan)
  - On backfill success:           -> 'ready'      (Plan 03)
  - On backfill blob-missing:      -> 'requires_user_reupload'  (Plan 03)
  - On backfill Docling exception: -> 'failed'     (Plan 03)
-->

Existing UPDATE-to-'ready' SQL shape (ingestion.py:437-439):
```python
supabase_client.table("documents").update(
    {"status": "ready", "content_hash": file_hash, "updated_at": "now()"}
).eq("id", document_id).execute()
```

After this plan's edit:
```python
supabase_client.table("documents").update({
    "status": "ready",
    "content_hash": file_hash,
    "content_markdown": text,                  # NEW (BACKFILL-01)
    "content_markdown_status": "ready",        # NEW (BACKFILL-01)
    "updated_at": "now()",
}).eq("id", document_id).execute()
```

Existing failure-path UPDATE (ingestion.py:446-448):
```python
supabase_client.table("documents").update(
    {"status": "failed", "error_message": error_msg, "updated_at": "now()"}
).eq("id", document_id).execute()
```

After this plan's edit:
```python
supabase_client.table("documents").update({
    "status": "failed",
    "content_markdown_status": "failed",       # NEW (BACKFILL-04 surfacing — Phase 4 contract)
    "error_message": error_msg,
    "updated_at": "now()",
}).eq("id", document_id).execute()
```

Same edit applies to ingest_document_update() at the parallel sites (L513-515 success, L522-524 failure).
</interfaces>
</context>

<tasks>

<task id="2-02-01" type="auto">
  <name>Task 1: Add synchronous content_markdown write to ingest_document() and ingest_document_update() (success + failure paths)</name>
  <files>backend/app/services/ingestion.py</files>
  <read_first>
    - backend/app/services/ingestion.py L382-450 (the file being modified — `ingest_document` body; the success UPDATE is at L437-439 and the except-block UPDATE is at L446-448; the `text` variable is in scope from L395 `text = extract_text(file_content, mime_type, file_name)`; the existing L396-397 `if not text.strip(): raise ValueError(...)` already prevents writing empty markdown to the success path)
    - backend/app/services/ingestion.py L453-526 (`ingest_document_update` body — the same UPDATE pattern at L513-515 success and L522-524 failure; the `text` variable is in scope from L471)
    - .planning/phases/02-content-markdown-backfill-gated/02-CONTEXT.md §LOCKED—Synchronous-on-upload (lines ~43-47 — directs the insertion point and the atomic-single-UPDATE requirement) AND §LOCKED—Status state machine (lines ~49-57 — defines the 4 transitions this plan implements two of)
    - .planning/phases/02-content-markdown-backfill-gated/02-PATTERNS.md §"backend/app/services/ingestion.py (MODIFY — synchronous-on-upload write)" (lines ~351-414 — paste-ready edit for both functions, both branches; explains why atomicity matters)
    - .planning/phases/02-content-markdown-backfill-gated/02-RESEARCH.md §Pattern 1 (lines ~211-267 — same paste-ready edit) AND §Anti-Patterns to Avoid (lines ~498-507 — explains why try/except wrapping ONLY the markdown capture is wrong and why we propagate the exception instead)
    - backend/migrations/014_content_markdown_column.sql (the canonical 4-element vocabulary `'pending' | 'ready' | 'failed' | 'requires_user_reupload'` — DO NOT introduce 'ok' or 'processing' values)
    - .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/04-PLAN.md (Phase 1 / Plan 04 — the migration that added these columns; must_haves describe the exact shape this plan writes to)
    - .planning/research/PITFALLS.md §Pitfall 6 (lines ~165-196 — chunk-stitching is forbidden; this plan uses the actual Docling output via the existing `text` variable, never any join-from-chunks)
  </read_first>
  <action>
    Modify `backend/app/services/ingestion.py` at exactly four sites: two in `ingest_document()` (success at L437-439, failure at L446-448) and two in `ingest_document_update()` (success at L513-515, failure at L522-524). All four edits are SHAPE PRESERVING — they extend the dict passed to `supabase_client.table("documents").update({...})` with new keys; they do NOT add any new function calls, do NOT add any background tasks, do NOT introduce any new helpers.

    Edit 1 — `ingest_document()` success UPDATE (replace L437-440):

    Before:
    ```python
            file_hash = compute_file_hash(file_content)
            supabase_client.table("documents").update(
                {"status": "ready", "content_hash": file_hash, "updated_at": "now()"}
            ).eq("id", document_id).execute()
            logger.info(f"Ingested document {document_id}: {len(chunks)} chunks")
    ```

    After:
    ```python
            file_hash = compute_file_hash(file_content)
            supabase_client.table("documents").update({
                "status": "ready",
                "content_hash": file_hash,
                "content_markdown": text,                  # BACKFILL-01: synchronous markdown capture
                "content_markdown_status": "ready",        # BACKFILL-01: same UPDATE = atomic
                "updated_at": "now()",
            }).eq("id", document_id).execute()
            logger.info(
                f"Ingested document {document_id}: {len(chunks)} chunks, "
                f"{len(text)} markdown chars"
            )
    ```

    Edit 2 — `ingest_document()` failure UPDATE (replace L446-448):

    Before:
    ```python
            try:
                supabase_client.table("documents").update(
                    {"status": "failed", "error_message": error_msg, "updated_at": "now()"}
                ).eq("id", document_id).execute()
            except Exception as inner_e:
                logger.error(f"Could not update failed status: {inner_e}")
    ```

    After:
    ```python
            try:
                supabase_client.table("documents").update({
                    "status": "failed",
                    "content_markdown_status": "failed",   # BACKFILL-04: surface to Phase 4 tools
                    "error_message": error_msg,
                    "updated_at": "now()",
                }).eq("id", document_id).execute()
            except Exception as inner_e:
                logger.error(f"Could not update failed status: {inner_e}")
    ```

    Edit 3 — `ingest_document_update()` success UPDATE (replace L513-515):

    Before:
    ```python
            file_hash = compute_file_hash(file_content)
            supabase_client.table("documents").update(
                {"status": "ready", "content_hash": file_hash, "updated_at": "now()"}
            ).eq("id", document_id).execute()

            logger.info(f"Re-ingested document {document_id}: {len(chunks)} chunks")
    ```

    After:
    ```python
            file_hash = compute_file_hash(file_content)
            supabase_client.table("documents").update({
                "status": "ready",
                "content_hash": file_hash,
                "content_markdown": text,                  # BACKFILL-01: synchronous markdown capture (re-ingest path)
                "content_markdown_status": "ready",        # BACKFILL-01: same UPDATE = atomic
                "updated_at": "now()",
            }).eq("id", document_id).execute()

            logger.info(
                f"Re-ingested document {document_id}: {len(chunks)} chunks, "
                f"{len(text)} markdown chars"
            )
    ```

    Edit 4 — `ingest_document_update()` failure UPDATE (replace L522-524):

    Before:
    ```python
            try:
                supabase_client.table("documents").update(
                    {"status": "failed", "error_message": error_msg, "updated_at": "now()"}
                ).eq("id", document_id).execute()
            except Exception as inner_e:
                logger.error(f"Could not update failed status: {inner_e}")
    ```

    After:
    ```python
            try:
                supabase_client.table("documents").update({
                    "status": "failed",
                    "content_markdown_status": "failed",   # BACKFILL-04: surface to Phase 4 tools
                    "error_message": error_msg,
                    "updated_at": "now()",
                }).eq("id", document_id).execute()
            except Exception as inner_e:
                logger.error(f"Could not update failed status: {inner_e}")
    ```

    Conventions to honor:
    - Atomic single-UPDATE per CONTEXT.md §specifics: the success UPDATE carries status + content_markdown + content_markdown_status together. **Do NOT split into two UPDATEs** — that risks half-updated rows (Pitfall 2).
    - The `text` variable is already in scope from L395 (`ingest_document`) and L471 (`ingest_document_update`). Zero re-extraction. The existing `if not text.strip(): raise ValueError(...)` guard at L396-397 / L472-473 already handles "Docling returned empty" by raising — the except block fires with `error_msg = "No extractable text found in document"` and writes `content_markdown_status='failed'`. This is the correct behavior per CONTEXT.md §LOCKED—Synchronous-on-upload paragraph 4.
    - Status vocabulary EXACTLY: `'ready'` and `'failed'` (from the canonical 4 — `'pending' | 'ready' | 'failed' | 'requires_user_reupload'` — per Phase 1 / Plan 04 / Migration 014). DO NOT use `'ok'` (that was a ROADMAP additional-context error; canonical is `'ready'`).
    - Update format style: switch from the single-line `update({"status": "ready", ...})` to multi-line `update({\n    "status": "ready",\n    ...\n})` for readability when there are 5+ keys (matches the style used in plan 01's `_upload_to_storage` helper and in Phase 1's migrations).
    - Inline comments `# BACKFILL-01: ...` and `# BACKFILL-04: ...` reference the requirement IDs being implemented (per project tracability convention).
    - The logger.info line on success is extended to include `{len(text)} markdown chars` — matches the style suggested in PATTERNS.md and gives operators visibility without requiring a separate log line.

    Do NOT:
    - Add any `background_tasks.add_task` call (Pitfall 2 — would break atomicity).
    - Wrap the markdown capture in its own try/except (RESEARCH.md Anti-Patterns: "lets the document reach status='ready' while content_markdown_status='failed'" — wrong; if Docling fails, both chunks and markdown fail together via the existing top-level except).
    - Add a new helper function (`extract_markdown_only`, `_capture_markdown`, etc.). The text is already computed; reuse it directly.
    - Touch any other function in ingestion.py (`extract_text`, `chunk_text`, `embed_batch`, `_extract_structured_data`, `_convert_pptx_to_pdf`).
    - Modify `record_manager.py` (per CONTEXT.md §canonical_refs: "DO NOT modify in this phase"; see plan-level `files_modified` — record_manager.py is not listed).
    - Add LangChain or LangGraph (project rule).
    - Use a custom exception class for empty-markdown — the existing `ValueError("No extractable text found in document")` already covers this.
    - Change the chunking, embedding, or metadata-extraction code paths.
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -c "import ast, pathlib; src = pathlib.Path('app/services/ingestion.py').read_text(encoding='utf-8'); ast.parse(src); body = '\n'.join(line for line in src.splitlines() if not line.lstrip().startswith('#')); assert body.count(chr(34)+'content_markdown_status'+chr(34)+': '+chr(34)+'ready'+chr(34)) == 2, 'expected 2 success-path content_markdown_status=ready (one per ingest function)'; assert body.count(chr(34)+'content_markdown_status'+chr(34)+': '+chr(34)+'failed'+chr(34)) == 2, 'expected 2 failure-path content_markdown_status=failed'; assert body.count(chr(34)+'content_markdown'+chr(34)+': text') == 2, 'expected 2 content_markdown=text writes (one per ingest function)'; assert 'background_tasks.add_task' not in body, 'no new background_tasks calls allowed (Pitfall 2)'; assert 'def ingest_document(' in body and 'def ingest_document_update(' in body, 'both functions must still exist'; assert body.count('extract_text(') == 2, 'extract_text should be called exactly twice (one per ingest function)'; print('ingestion.py edit structure OK')"</automated>
  </verify>
  <acceptance_criteria>
    - File `backend/app/services/ingestion.py` parses as valid Python (`ast.parse` succeeds).
    - `grep -v '^[[:space:]]*#' backend/app/services/ingestion.py | grep -c '"content_markdown": text'` returns exactly 2 (one in `ingest_document`, one in `ingest_document_update` — comments excluded so header prose doesn't inflate the count).
    - `grep -v '^[[:space:]]*#' backend/app/services/ingestion.py | grep -c '"content_markdown_status": "ready"'` returns exactly 2 (one per function, success path).
    - `grep -v '^[[:space:]]*#' backend/app/services/ingestion.py | grep -c '"content_markdown_status": "failed"'` returns exactly 2 (one per function, failure path).
    - `grep -v '^[[:space:]]*#' backend/app/services/ingestion.py | grep -c '"content_markdown_status": "ok"'` returns 0 (canonical value is 'ready', not 'ok' — Migration 014 vocabulary lock).
    - `grep -v '^[[:space:]]*#' backend/app/services/ingestion.py | grep -c '"content_markdown_status": "requires_user_reupload"'` returns 0 (that transition belongs to Plan 03 backfill, not synchronous upload).
    - `grep -c "background_tasks.add_task" backend/app/services/ingestion.py` returns 0 (Pitfall 2: no new background-task call may be added inside ingestion.py).
    - `grep -c "def ingest_document(" backend/app/services/ingestion.py` returns 1.
    - `grep -c "def ingest_document_update(" backend/app/services/ingestion.py` returns 1.
    - `grep -c "extract_text(" backend/app/services/ingestion.py` returns exactly 2 (one call per ingest function — no third call introduced for re-extraction).
    - `grep -E "from\s+(langchain\|langgraph)" backend/app/services/ingestion.py` returns no matches.
    - `grep -E "string_agg|array_agg" backend/app/services/ingestion.py` returns no matches (Pitfall 6: no chunk-stitching).
    - `cd backend && venv/Scripts/python -c "from app.services.ingestion import ingest_document, ingest_document_update, extract_text; import inspect; sig1 = list(inspect.signature(ingest_document).parameters); sig2 = list(inspect.signature(ingest_document_update).parameters); assert sig1 == ['document_id','file_content','mime_type','file_name','user_id','supabase_client'], f'ingest_document sig: {sig1}'; assert sig2 == sig1, f'ingest_document_update sig: {sig2}'; print('ingestion signatures OK')"` prints "ingestion signatures OK".
  </acceptance_criteria>
  <done>
    Both `ingest_document()` and `ingest_document_update()` write `content_markdown=text` and `content_markdown_status='ready'` in the SAME UPDATE statement that flips `status='ready'` (single atomic PostgREST call). Both functions' except blocks write `content_markdown_status='failed'` alongside `status='failed'`. No new background tasks, no new helper functions, no re-extraction. The `text` variable is reused from the existing `extract_text()` call. Module loads and the function signatures are unchanged.
  </done>
</task>

<task id="2-02-02" type="auto">
  <name>Task 2: Pin docling==2.91.0 in requirements.txt for byte-equivalence determinism</name>
  <files>backend/requirements.txt</files>
  <read_first>
    - backend/requirements.txt (the file being modified — currently every dep is unpinned; line 10 is `docling`; this plan pins ONLY docling, leaves the other 12 deps unpinned per CONTEXT.md scope discipline)
    - .planning/phases/02-content-markdown-backfill-gated/02-CONTEXT.md §Claude's Discretion (line ~96 — "Whether to pin docling==2.91.0 in requirements.txt (recommended yes, per researcher §Decisions §3)")
    - .planning/phases/02-content-markdown-backfill-gated/02-RESEARCH.md §Standard Stack (lines ~74-76 — `docling 2.91.0` verified via `pip show docling`) AND §Pitfall 6 (lines ~603-614 — explains the byte-equivalence drift risk if Docling is unpinned)
    - .planning/phases/02-content-markdown-backfill-gated/02-PATTERNS.md §"backend/requirements.txt (MODIFY — version pin)" (lines ~509-537 — confirms this is the first version pin in the file and that no other dependency should be pinned in this PR)
  </read_first>
  <action>
    Edit `backend/requirements.txt` to change line 10 from:

    ```
    docling
    ```

    to:

    ```
    docling==2.91.0
    ```

    Leave every other line UNCHANGED. The file's other 12 dependencies remain unpinned. This is the FIRST version pin in the file — that is intentional (per CONTEXT.md §Claude's Discretion and PATTERNS.md "Do NOT pin other dependencies in this PR — that's a separate concern outside Phase 2 scope").

    Why pin Docling specifically:
    - Phase 2 success criterion 4 requires byte-equivalence (±20 chars) between synchronous-on-upload markdown and backfill markdown for the same blob.
    - That's mathematically true if both calls use the same Docling version with the same options.
    - Without a pin, a `pip install -U` between upload-time and backfill-time can swap Docling to a version with subtly different output (whitespace, image placeholders, ordering) — the test would fail intermittently.
    - 2.91.0 is the verified-currently-installed version per RESEARCH.md `pip show docling`.

    Conventions to honor:
    - Filename and file location unchanged.
    - Single-line edit; total file line count remains 13.
    - Use `==` (exact pin), not `~=` or `>=` — exact pin is what the byte-equivalence guarantee requires.
    - No comment added (the requirements.txt has no other comments; staying consistent).

    Do NOT:
    - Pin any other dependency (out of scope per CONTEXT.md / PATTERNS.md).
    - Reorder lines.
    - Add or remove dependencies.
    - Switch to a `pyproject.toml` / `poetry` / `uv` format (project uses pip + requirements.txt).
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -c "lines = open('requirements.txt', encoding='utf-8').read().splitlines(); assert len(lines) == 13, f'expected 13 lines, got {len(lines)}'; assert 'docling==2.91.0' in lines, f'docling==2.91.0 not pinned. Lines: {lines}'; assert 'docling' not in lines, 'unpinned bare docling line still present (line equals exactly docling)'; pinned = [l for l in lines if '==' in l]; assert pinned == ['docling==2.91.0'], f'expected only docling pinned, got: {pinned}'; print('requirements.txt pin OK')"</automated>
  </verify>
  <acceptance_criteria>
    - File `backend/requirements.txt` has exactly 13 non-empty lines (`wc -l` returns 13).
    - File contains the literal line `docling==2.91.0` (exact match including the `==`).
    - File does NOT contain a bare `docling` line (i.e. the unpinned form was replaced; `grep -E "^docling$" backend/requirements.txt` returns no matches).
    - `grep -c "==" backend/requirements.txt` returns exactly 1 (only docling is pinned; the other 12 deps remain unpinned).
    - The 12 other dependencies are present and unchanged: `grep -cE "^(fastapi|uvicorn\[standard\]|python-dotenv|pydantic|google-genai|supabase|langsmith|sse-starlette|python-multipart|cohere|duckdb|tavily-python)$" backend/requirements.txt` returns 12.
    - Python sanity check in `<verify>` exits 0 and prints "requirements.txt pin OK".
  </acceptance_criteria>
  <done>
    `backend/requirements.txt` has `docling==2.91.0` pinned (was previously unpinned `docling`). All other 12 dependencies are unchanged and unpinned. File is exactly 13 lines. The pin guarantees byte-equivalence determinism for Plan 03's backfill and Plan 04's byte-equivalence assertion against the synchronous-on-upload write from Task 1.
  </done>
</task>

</tasks>

<verification>
This plan delivers BACKFILL-01 (synchronous content_markdown write on every new upload) and the determinism precondition for Phase 2 success criterion 4 (byte-equivalence). It does NOT touch Storage (Plan 01's job), the backfill script (Plan 03's job), or the test suite (Plan 04's job).

Verification steps:
- Task 1: Python AST parse + grep-with-comment-strip gates confirm exactly 2 success-path and 2 failure-path content_markdown_status writes (one per function); confirm no new background tasks; confirm no chunk-stitching anti-pattern (no `string_agg`/`array_agg`); confirm function signatures are preserved.
- Task 2: Confirm exactly one `==` pin in requirements.txt and that it is `docling==2.91.0`; confirm the 12 other deps are unchanged.
- Operational verification (deferred to Plan 04 integration test): a real upload through `POST /api/files/upload` polls to status='ready' AND has non-empty `documents.content_markdown` AND has `content_markdown_status='ready'`.
</verification>

<success_criteria>
- BACKFILL-01 satisfied: every new upload writes content_markdown synchronously inside ingest_document() (and ingest_document_update()), in the SAME UPDATE that flips status='ready'.
- Failure path writes content_markdown_status='failed' alongside status='failed' (BACKFILL-04 surfacing precondition for Phase 4 tools).
- No new background tasks, no atomicity gap between status and content_markdown_status (Pitfall 2 mitigation).
- docling==2.91.0 is pinned in requirements.txt; this is the only pinned dependency.
- record_manager.py is unchanged (per CONTEXT.md §canonical_refs).
</success_criteria>

<output>
After completion, create `.planning/phases/02-content-markdown-backfill-gated/02-02-SUMMARY.md` recording: the four exact line ranges modified in `ingestion.py` (success and failure paths in both ingest functions), the canonical 4-element status vocabulary (call it out — Plan 03 must use the same vocabulary), confirmation that no new background tasks were added, the docling version pin, and the byte-equivalence guarantee that Plan 04's test will assert against.
</output>
</content>
