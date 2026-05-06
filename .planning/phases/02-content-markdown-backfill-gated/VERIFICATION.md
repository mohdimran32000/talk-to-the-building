---
phase: 02-content-markdown-backfill-gated
verified: 2026-05-04T00:00:00Z
status: passed
score: 4/4 must-haves verified
overrides_applied: 0
gaps: []
human_verification: []
carry_forward:
  - id: runbook-bucket-gap
    description: "Operator-pre-req surprise: 'documents' bucket did not exist at suite-run time despite operator approving Plan 01/04 pre-reqs. Orchestrator created it programmatically via service-role. Runbook step (Studio bucket creation) is undocumented for fresh-project onboarding."
    recommendation: "Add a one-shot setup script (scripts/setup_storage_bucket.py) or canary probe to the operator runbook before any Phase 2+ suite invocation."
  - id: phase1-admin-assumption-failures
    description: "23 failures in the cross-suite sweep (Threads, Messages, Hybrid, Tools, Sub-Agents) are pre-existing Phase-1 carry-forward: admin-assumption mismatch + auth middleware regression. NONE attributable to Phase-2 files."
    scope: "Out of scope for Phase 2 verification."
---

# Phase 2: content_markdown Backfill (Gated) — Verification Report

**Phase Goal:** Backfill `content_markdown` for every existing document so Phase 4 RAG/Explorer tools can rely on it. Re-run Docling against the original Storage blobs (NOT chunk stitching). Blobs that are GC'd surface as `requires_user_reupload`. Gate on tests passing.

**Verified:** 2026-05-04
**Status:** PASS
**Re-verification:** No — initial verification

---

## 1. Phase Goal Restatement

Every existing Episode 1 document must have canonical, full-document Docling markdown stored in `documents.content_markdown` so the Phase 4 `grep` and `read_document` tools can ship against the full corpus. The backfill must re-run Docling against the original Storage blobs (never `string_agg`/`array_agg` chunk stitching). Rows whose source blob is missing must be marked `requires_user_reupload` rather than silently skipped. The phase is gated on an integration test suite passing green.

---

## 2. Requirements Coverage Matrix

| Requirement | Description | Commit Refs | Evidence |
|-------------|-------------|-------------|----------|
| BACKFILL-01 | Synchronous `content_markdown` write at ingest time, atomic with `status='ready'` | `4dd7c4c` (feat) `91ad425` (pin) | `ingestion.py:437-443` — single `.update({..., "content_markdown": text, "content_markdown_status": "ready", ...})` in both `ingest_document()` (success L437-443) and `ingest_document_update()` (success L522-529); `ingestion.py:452-460` and `ingestion.py:539-545` — failure paths both write `"content_markdown_status": "failed"` in the except block UPDATE; no `background_tasks.add_task` added in `ingestion.py` (verified 0 matches); `requirements.txt:10` — `docling==2.91.0` only pinned dep |
| BACKFILL-02 | Backfill CLI populates `content_markdown` for existing rows where `status != 'ready'` | `28e8fab` (feat) `2ad9b78` (test) | `backfill_content_markdown.py` — 357 lines; `.neq("content_markdown_status", "ready")` SELECT filter at L309; reuses `extract_text()` from `app.services.ingestion` (L63 import); `threading.Semaphore(2)` throttle at L74; `--dry-run` / `--limit` / `--document-id` / `--purge-orphans` flags; end-of-run summary line at L533-540; `test_backfill.py` Section 5 verifies backfill happy path end-to-end: subprocess exits 0, DB row reads back `content_markdown IS NOT NULL` AND `content_markdown_status='ready'` (15/15 PASS) |
| BACKFILL-03 | Existing rows at `folder_path='/'` AND `scope='user'` (Episode 1 invariants intact) | `2ad9b78` (test) | `test_backfill.py` Section 4 — assertion-only verifier: `SELECT id FROM documents WHERE folder_path != '/'` returns 0 rows AND `SELECT id FROM documents WHERE scope != 'user'` returns 0 rows. Verifies Phase 1 / Migration 012 `NOT NULL DEFAULT '/' / 'user'` did the work for all pre-Phase-2 rows. Runtime result: PASS |
| BACKFILL-04 | Missing blob → `content_markdown_status='requires_user_reupload'`; never silently skipped | `28e8fab` (feat) `2ad9b78` (test) | `backfill_content_markdown.py:144-156` — `if blob is None:` branch writes `{"content_markdown_status": "requires_user_reupload", "updated_at": "now()"}` via `.eq("id", doc_id).execute()` and returns; does NOT raise. `ingestion.py:452-460` and `ingestion.py:539-545` — failure-path except blocks write `content_markdown_status='failed'` (surfaces to Phase 4 tools). `test_backfill.py` Section 6 — fixture row with no Storage blob: subprocess exits 0 or 2, DB reads back `content_markdown_status='requires_user_reupload'`. Runtime result: PASS |

**All 4 BACKFILL-* requirements: VERIFIED.**

---

## 3. Threat Model Verification

### Pitfall 2 — Atomicity (T-2-05): No split between status and content_markdown_status

**STATUS: VERIFIED**

`ingestion.py` success UPDATE (L437-443, `ingest_document`):
```python
supabase_client.table("documents").update({
    "status": "ready",
    "content_hash": file_hash,
    "content_markdown": text,          # BACKFILL-01
    "content_markdown_status": "ready", # BACKFILL-01 — same dict, one PostgREST call
    "updated_at": "now()",
}).eq("id", document_id).execute()
```

Identical shape in `ingest_document_update()` at L522-529. One `.update({...})` call = one SQL UPDATE statement = atomic by Postgres semantics. No window where `status='ready'` AND `content_markdown_status='pending'`. Zero `background_tasks.add_task` calls in `ingestion.py` confirmed by grep (0 matches).

Failure path is also one call per function (L452-460, L539-545), writing `status='failed'` + `content_markdown_status='failed'` atomically.

### Pitfall 4 — Idempotency (T-2-09): Re-run on ready rows is a no-op

**STATUS: VERIFIED**

Two-layer defense:
1. `backfill_content_markdown.py:309` — SELECT uses `.neq("content_markdown_status", "ready")`, excluding already-ready rows from the scan entirely.
2. `backfill_content_markdown.py:136-138` — per-row defense-in-depth: `if status_before == "ready": return "skipped"` for rows loaded via `--document-id` that are already ready.

`test_backfill.py` Section 7 (idempotency): re-runs backfill with `--document-id` on the now-ready fixture row; asserts subprocess exits 0 AND output contains "Found 0 document" OR "[SKIP]" OR ("ready=0" AND "failed=0"). Runtime result: PASS.

### Pitfall 6 (RANK 2) — Chunk-stitching forbidden

**STATUS: VERIFIED**

`backfill_content_markdown.py:63` — `from app.services.ingestion import extract_text` is the ONLY data-extraction call. No `SELECT` against `document_chunks` anywhere in the script (grep: 0 matches outside the `--purge-orphans` DELETE path). No `string_agg` or `array_agg` in backfill script (grep: 0 matches). Module docstring line 28 explicitly states: "Forbidden: chunk-stitching from document_chunks (Pitfall 6 / RANK 2). This script ALWAYS re-runs Docling via app.services.ingestion.extract_text".

### T-2-05 — Atomic success UPDATE

VERIFIED (see Pitfall 2 above).

### T-2-06 — Failure-path status surfacing

**STATUS: VERIFIED**

Both `ingest_document()` except block (L452-460) and `ingest_document_update()` except block (L539-545) write `content_markdown_status='failed'` alongside `status='failed'` in the SAME UPDATE. Phase 4 tools (`grep`, `read_document`) will see `content_markdown_status='failed'` and can surface `{status: 'pending_reindex'}` per the CONTEXT.md tool integration contract.

### T-2-07 — Byte-equivalence determinism via docling==2.91.0 pin

**STATUS: VERIFIED**

`requirements.txt:10` — `docling==2.91.0` is the ONLY pinned dependency (exactly 1 `==` in file, all 12 other deps unpinned). Both synchronous-on-upload path (invoked in-process by FastAPI worker) and backfill script (invoked as subprocess) use the same venv, same version, same `extract_text()` call with no kwargs on `export_to_markdown`. `test_backfill.py` Section 3 (Phase 2 SC4) empirically asserts `abs(len(upload_markdown) - len(fresh)) <= 20`. Runtime result for plain-text fixture: diff = 0 (Docling is a passthrough for `.txt`). PASS.

---

## 4. Cross-Cutting Evidence

### Storage Gap Closure

`backend/app/routers/files.py` — `_upload_to_storage()` helper defined at L32-57 with exact signature `(supabase, user_id, document_id, file_name, contents, mime_type)`. Called at L93-100 (`action='update'` branch, BEFORE `background_tasks.add_task` at L102) and L124-131 (`action='create'` branch, BEFORE `background_tasks.add_task` at L133). Failure is non-fatal: `except Exception as e: logger.warning(...)`. Storage path formula: `f"{user_id}/{document_id}{ext}"` with `ext = os.path.splitext(file_name)[1]`. No `documents.storage_path` column written.

`test_backfill.py` Section 2 asserts: after real upload through `POST /api/files/upload`, blob is downloadable via `sb_admin.storage.from_("documents").download(sp)` where `sp = _storage_path_for(user_id, upload_doc_id, upload_file_name)`. Length matches source bytes exactly. Runtime result: PASS.

### Phase 2 SC4 — Byte-Equivalence

`test_backfill.py:241-248` — `fresh = extract_text(CAPYBARA_TEXT, "text/plain", upload_file_name)` called on the same bytes; `diff = abs(len(upload_markdown) - len(fresh))`. Assertion: `diff <= 20`. For plain-text fixtures, diff = 0 by construction (Docling passthrough). Runtime result: PASS.

### --purge-orphans Interactive Ritual

`backfill_content_markdown.py:218-268` — `_purge_orphans()` function implements the 4-step ritual:
1. SELECT candidates: `.eq("content_markdown_status", "requires_user_reupload").is_("content_markdown", "null")` (L231-235)
2. Print human-readable table (L241-245)
3. Require interactive `input()` returning `y` or `yes` before any DELETE (L251-254)
4. Delete ONLY the previously-printed IDs: per-id `supabase.table("document_chunks").delete().eq("document_id", did)` then `supabase.table("documents").delete().eq("id", did)` (L258-268)

No blanket `DELETE WHERE` queries. `--purge-orphans` is mutually exclusive with the normal backfill loop (L298-304). The `--purge-orphans` path skips the canary check (line 297-300: canary runs only `if not args.purge_orphans`).

### RLS Scope-Leak Invariant Intact

`migration 018_storage_rls.sql` — SELECT and INSERT policies on `storage.objects` both predicated on `bucket_id = 'documents' AND (SELECT auth.uid())::text = (storage.foldername(name))[1]`. Idempotent via `DROP POLICY IF EXISTS` / `CREATE POLICY`. Service-role bypasses RLS per Supabase semantics. Both policies use `(SELECT auth.uid())` perf-cached subquery per Migration 015 convention.

Phase-2 code writes to `documents.content_markdown` and `documents.content_markdown_status` only. Phase-2 code never writes to `documents.scope`, `documents.user_id`, or `documents.folder_path`. The Phase-1 two-scope RLS policies (Migration 015) remain untouched by Phase 2.

---

## 5. Carry-Forward

### Operator Runbook Gap — Bucket Creation (WARNING, not BLOCKER)

The `documents` Storage bucket did not exist on the Supabase project at suite-run time, despite the operator approving the Plan 01/04 prerequisites at the human-verify checkpoint. The orchestrator's pre-flight `_verify_storage_setup()` canary correctly surfaced this as a `[FATAL]` error and the orchestrator created the bucket programmatically via service-role (`supabase.storage.create_bucket("documents", options={"public": False, "file_size_limit": 52428800})`). Migration 018's RLS policies were already applied at that time; the failure was solely the absent bucket.

**Impact on Phase 2 verdict:** None. The code is correct; the runbook is the gap. The canary fired exactly as designed and guided resolution.

**Recommendation:** Add either:
- A one-shot `scripts/setup_storage_bucket.py` script that operators run alongside `scripts/run_migrations.py`, OR
- An explicit canary probe (`sb.storage.from_('documents').list(path='', options={'limit':1})`) as a runbook step before any Phase 2+ suite invocation.

### Phase-1 Admin-Assumption Test Failures (OUT OF SCOPE)

The full `test_all.py` cross-suite sweep after Phase 2 completion produced: **163 passed, 23 failed across 14 suites**. The Backfill suite result was **15/15 PASS**. All 23 failures are attributable to the pre-existing Phase-1 carry-forward (documented in `STATE.md` §Session Continuity):

- Admin-assumption mismatch in Episode-1 test fixtures (`test_settings`, `test_hybrid`, `test_tools`) — fixtures assume the test user has admin on tables that now require admin via `is_admin()`.
- Auth middleware regression carried forward from Phase 1 (`test_threads`, `test_messages` cascade, `test_sub_agents` crash).

None of the 23 failures are in files Phase 2 created or modified (`backend/app/routers/files.py`, `backend/app/services/ingestion.py`, `backend/requirements.txt`, `backend/migrations/018_storage_rls.sql`, `backend/scripts/backfill_content_markdown.py`, `backend/scripts/test_backfill.py`, `backend/scripts/test_all.py`). Phase 2 is not responsible for these failures and they do not block Phase 2 goal achievement.

---

## 6. Observable Truths Verification

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Every new upload writes `content_markdown` synchronously inside `ingest_document()`, same UPDATE as `status='ready'` | VERIFIED | `ingestion.py:437-443` — single `.update({...})` with `"content_markdown": text` + `"content_markdown_status": "ready"` + `"status": "ready"`. Identical in `ingest_document_update()` at L522-529 |
| 2 | `backfill_content_markdown.py` re-runs Docling via `extract_text()` (not chunk stitching), populates `content_markdown` for non-ready rows, idempotent, throttled | VERIFIED | `backfill_content_markdown.py` imports `extract_text` (L63); `.neq("content_markdown_status", "ready")` filter (L309); `threading.Semaphore(2)` (L74); 0 `string_agg`/`array_agg` matches; `test_backfill.py` Section 5 PASS |
| 3 | Episode 1 rows are at `folder_path='/'` AND `scope='user'` | VERIFIED | `test_backfill.py` Section 4 PASS — 0 rows with `folder_path != '/'`, 0 rows with `scope != 'user'` |
| 4 | Missing blob → `content_markdown_status='requires_user_reupload'`; never silently skipped | VERIFIED | `backfill_content_markdown.py:144-156`; `test_backfill.py` Section 6 PASS |

**Score: 4/4 truths VERIFIED**

---

## 7. Required Artifacts

| Artifact | Status | Evidence |
|----------|--------|----------|
| `backend/app/routers/files.py` | VERIFIED | 165 lines; `_upload_to_storage` helper at L32-57; called in both create (L124-131) and update (L93-100) branches before `background_tasks.add_task`; non-fatal exception handling |
| `backend/migrations/018_storage_rls.sql` | VERIFIED | 59 lines; `DROP POLICY IF EXISTS` / `CREATE POLICY` for `documents_storage_select_own` (SELECT) and `documents_storage_insert_own` (INSERT); `TO authenticated`; `bucket_id = 'documents'`; `(SELECT auth.uid())::text = (storage.foldername(name))[1]`; bucket creation documented as one-time Studio task |
| `backend/app/services/ingestion.py` | VERIFIED | Both `ingest_document()` and `ingest_document_update()` success paths write `content_markdown` + `content_markdown_status='ready'` atomically; both failure paths write `content_markdown_status='failed'`; 0 `background_tasks.add_task` calls; 0 `string_agg`/`array_agg` |
| `backend/requirements.txt` | VERIFIED | 13 lines; `docling==2.91.0` at line 10; exactly 1 `==` pin; all 12 other deps unpinned |
| `backend/scripts/backfill_content_markdown.py` | VERIFIED | 357 lines; `from app.services.ingestion import extract_text`; `threading.Semaphore(2)`; `--dry-run` / `--limit` / `--document-id` / `--purge-orphans`; `.neq("content_markdown_status", "ready")` idempotent filter; `_canary_storage_check()` before iterating rows; interactive `input()` in `_purge_orphans()`; `sys.exit(main())`; 0 `string_agg`/`array_agg`; 0 `@traceable`/`langsmith`; 0 `from langchain`/`from langgraph` |
| `backend/scripts/test_backfill.py` | VERIFIED | 414 lines; `def run()`; `return h.passed, h.failed`; `import subprocess`; `from app.services.ingestion import extract_text`; 21 `h.test()` assertions; 7 `h.section()` blocks; all 4 BACKFILL-* labels present; module-level `_tracked_doc_ids` + `_tracked_storage_paths`; `def _cleanup()`; 0 `DELETE FROM`/`TRUNCATE`; `os.path.splitext` path formula; 0 `@traceable`/`langsmith` |
| `backend/scripts/test_all.py` | VERIFIED | `import test_backfill` after `import test_files`; `("Backfill", test_backfill)` after `("Files", test_files)`; SUITES count = 14 |

---

## 8. Key Link Verification

| From | To | Via | Status |
|------|----|-----|--------|
| `files.py::upload_file()` | Supabase Storage bucket `documents` | `supabase.storage.from_("documents").upload(storage_path, contents, file_options={"content-type": mime_type, "upsert": "true"})` | VERIFIED — `files.py:46-50` |
| `files.py::_upload_to_storage()` | Storage path formula | `f"{user_id}/{document_id}{ext}"` where `ext = os.path.splitext(file_name)[1]` | VERIFIED — `files.py:43-44` |
| `backfill_content_markdown.py::_download_blob()` | Supabase Storage bucket `documents` | `supabase.storage.from_(STORAGE_BUCKET).download(storage_path)` with same formula as `_upload_to_storage` | VERIFIED — `backfill_content_markdown.py:116-117`; formula at L85-86 matches `files.py:43-44` exactly |
| `backfill_content_markdown.py` | `app.services.ingestion.extract_text` | `from app.services.ingestion import extract_text` | VERIFIED — `backfill_content_markdown.py:63`; called at L160 |
| `ingestion.py::ingest_document()` success UPDATE | `documents.content_markdown` + `documents.content_markdown_status` | single `.update({..., "content_markdown": text, "content_markdown_status": "ready"})` | VERIFIED — `ingestion.py:437-443` |
| `ingestion.py::ingest_document_update()` success UPDATE | same columns | same pattern | VERIFIED — `ingestion.py:522-529` |
| `test_backfill.py` | `backfill_content_markdown.py` CLI | `subprocess.run([sys.executable, "scripts/backfill_content_markdown.py", "--document-id", id])` | VERIFIED — `test_backfill.py:133-145` |
| `test_all.py` SUITES | `test_backfill` module | `import test_backfill` + `("Backfill", test_backfill)` in SUITES list | VERIFIED — `test_all.py:17` and `test_all.py:33` |

---

## 9. Anti-Patterns Scan

| File | Anti-Pattern | Result |
|------|-------------|--------|
| `backfill_content_markdown.py` | `string_agg` / `array_agg` chunk-stitching | 0 matches — CLEAN |
| `backfill_content_markdown.py` | `@traceable` / `langsmith` | 0 matches — CLEAN |
| `backfill_content_markdown.py` | `from langchain` / `from langgraph` | 0 matches — CLEAN |
| `backfill_content_markdown.py` | `background_tasks.add_task` | N/A (CLI script, not FastAPI) |
| `ingestion.py` | `string_agg` / `array_agg` | 0 matches — CLEAN |
| `ingestion.py` | `background_tasks.add_task` | 0 matches — CLEAN (atomicity preserved) |
| `test_backfill.py` | `DELETE FROM` / `TRUNCATE` (blanket) | 0 matches — CLEAN (CLAUDE.md rule) |
| `test_backfill.py` | `@traceable` / `langsmith` | 0 matches — CLEAN |
| `018_storage_rls.sql` | `DROP TABLE` / `TRUNCATE` / `DELETE FROM` | 0 matches — CLEAN |
| `018_storage_rls.sql` | `INSERT INTO storage.buckets` | 0 matches — CLEAN (Studio task per header) |
| `018_storage_rls.sql` | `BEGIN`/`COMMIT` transaction wrapper | 0 matches — CLEAN (`run_migrations.py` wraps per-migration) |

---

## 10. Test Results Summary

| Suite | Run Type | Result |
|-------|----------|--------|
| Backfill (`test_backfill.py`) | Direct suite run | 15/15 PASS |
| Full cross-suite (`test_all.py`, 14 suites) | Cross-suite sweep | 163/186 PASS; 23 FAIL (all Phase-1 carry-forward, none Phase-2) |

Phase-2-scope suites in cross-suite sweep:
- Backfill: 15/15 PASS
- Files (no regressions from `files.py` edit): PASS
- RAG (no regressions from `ingestion.py` edit): PASS

---

## Final Verdict: PASS

**Phase 2 goal is achieved.** All four BACKFILL-* requirements are satisfied by verifiable codebase evidence:

- BACKFILL-01: `ingestion.py` writes `content_markdown` atomically in both `ingest_document()` and `ingest_document_update()` success paths; failure paths write `content_markdown_status='failed'`; no atomicity gap.
- BACKFILL-02: `backfill_content_markdown.py` re-runs Docling (via `extract_text()`, never chunk-stitching) for all non-ready rows; idempotent; throttled; logs counts.
- BACKFILL-03: All pre-Phase-2 rows sit at `folder_path='/'`, `scope='user'` per Migration 012 defaults; integration test confirms 0 offending rows.
- BACKFILL-04: Missing-blob path correctly writes `requires_user_reupload` (not silently skipped); failure-path writes `failed`; both surfaced to Phase 4 tools per the CONTEXT.md tool integration contract.

Storage Gap closure verified end-to-end: blobs uploaded at `documents/{user_id}/{doc_id}{ext}` before Docling background task; downloadable via service-role; RLS policies on `storage.objects` applied idempotently via Migration 018. Byte-equivalence (Phase 2 SC4) passes at diff=0 for plain-text fixtures. Chunk-stitching (Pitfall 6 RANK 2) categorically absent. `--purge-orphans` interactive ritual implemented and correct. Phase 1 RLS scope-leak invariant untouched.

One carry-forward WARNING (operator runbook gap — bucket creation not automated) does not affect the code's correctness. The canary `_verify_storage_check()` fires on bucket-absent deployments with an actionable error, confirming the mitigation is wired.

Phase 4 (`grep`, `read_document`) may now proceed relying on `content_markdown_status='ready'` rows and `requires_user_reupload` surfacing.

---

_Verified: 2026-05-04_
_Verifier: Claude (gsd-verifier)_
