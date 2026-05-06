---
phase: 02-content-markdown-backfill-gated
plan: 03
subsystem: ingestion
tags: [supabase-storage, docling, argparse, threading-semaphore, idempotent-cli, service-role, backfill]

# Dependency graph
requires:
  - phase: 02-content-markdown-backfill-gated / Plan 01
    provides: Storage upload at upload-time + Migration 018 RLS — original blobs persisted at documents/{user_id}/{document_id}{ext}
  - phase: 02-content-markdown-backfill-gated / Plan 02
    provides: Synchronous content_markdown write inside ingest_document() / ingest_document_update(); docling==2.91.0 pinned
provides:
  - backend/scripts/backfill_content_markdown.py — argparse CLI re-running Docling against Storage blobs to populate content_markdown
  - --dry-run, --limit, --document-id, --purge-orphans flags
  - Per-row state-machine writer (ready / failed / requires_user_reupload) — never raises out of the loop
  - Canary Storage check before iteration (Pitfall 5 abort path)
  - Interactive --purge-orphans ritual: SELECT → print → input(y|yes) → per-id chunks-then-document DELETE
  - Idempotency contract (.neq("content_markdown_status","ready") on the SELECT — Migration 014 partial index hot path)
  - Storage path formula contract: f"{user_id}/{document_id}{ext}" — exact mirror of Plan 01 _upload_to_storage
affects: [phase-02-04-test_backfill, phase-04-grep-tool, phase-04-read_document-tool]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "argparse-based CLI for batch scripts (first in this codebase; PATTERNS.md NEW pattern bound to Unix idioms — --dry-run, --limit N, --document-id UUID, interactive --purge-orphans)"
    - "Sequential under threading.Semaphore(2) for offline batch (mirrors files.py:15 capacity from a separate process; defensive — MVP is single-threaded)"
    - "Canary Storage check pattern (probe bucket reachability up front; abort with exit 1 + clear remediation pointer rather than silently marking every row 'requires_user_reupload')"
    - "Interactive scoped-cleanup ritual for production scripts (CLAUDE.md 'Tests must NEVER delete all user data' rule extended to operator scripts: SELECT → print → input → per-id DELETE; chunks-then-document two-step; never DELETE WHERE blanket)"
    - "Per-row state-machine writer that never raises out of the loop — Docling exception → 'failed', blob 404 → 'requires_user_reupload', success → 'ready' (each via the Plan-02-established atomic-multi-field UPDATE pattern)"
    - "load_dotenv() called BEFORE 'from app.services.ingestion import extract_text' because that module instantiates google-genai's Client at import time using os.environ.get('GEMINI_API_KEY') — module-level env consumers must be honored at script-side import order"

key-files:
  created:
    - backend/scripts/backfill_content_markdown.py
  modified: []

key-decisions:
  - "Plan 03 reuses extract_text() from app.services.ingestion via direct import (per RESEARCH.md §Standard Stack §Alternatives); does NOT reimplement Docling — guarantees byte-equivalence with Plan 02 synchronous-on-upload markdown (Phase 2 SC4)"
  - "Storage path formula f'{user_id}/{document_id}{ext}' with ext=os.path.splitext(file_name)[1] is a hard contract — mismatched formula would silently mark every row 'requires_user_reupload'. _storage_path_for() helper centralizes the formula (smoke-tested via assertions on a.pdf, Makefile, capybara_facts.txt)"
  - "load_dotenv() runs BEFORE the 'from app.services.ingestion import extract_text' import (deviation from RESEARCH.md skeleton's import-then-load order). app.services.ingestion does `_client = genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))` at line 22 (module load), so env must be present at import. Without this, the script crashes during import in any subprocess that doesn't have the env pre-exported. # noqa: E402 inline-disables the linter's import-not-at-top warning"
  - "Canary uses storage.from_('documents').list(path='', options={'limit': 1}) — cheapest service-role read against the bucket; returns [] for empty/new bucket but does NOT 404 if bucket exists. If it raises, abort with exit 1 + a clear remediation message pointing operators to Plan 01 (Studio bucket creation) AND Migration 018 (RLS policies)"
  - "Per-row UPDATE shapes write ONLY content_markdown / content_markdown_status / updated_at — NEVER documents.status, scope, user_id, folder_path. The 'status' column belongs to chunks-pipeline lifecycle (ingest_document set it to 'ready' at upload time); backfill does NOT re-touch it (per RESEARCH.md Anti-Patterns)"
  - "--purge-orphans is mutually exclusive with the normal backfill loop AND skips the canary check — orphan purge is a Postgres-only DELETE path that does not need Storage to be reachable. Inverted: if Storage is misconfigured, operators can still purge orphan rows from the DB"
  - "Default behavior of plain run with no orphans (zero rows match .neq filter) is exit 0 + 'processed=0 ready=0 ...' summary line — NOT exit 1 (a fully-ready corpus is the steady state; treating it as an error would break monitoring/cron scenarios)"
  - "Operator-safety warning at end-of-run: if len(rows) >= 5 AND every row ended at 'requires_user_reupload', logger.warning that this likely indicates a Storage misconfiguration (defense in depth on top of the canary)"
  - "Threading.Semaphore(2) is defensive for MVP (loop is single-threaded). RESEARCH.md §Pitfall 3 explicitly cautions against future ThreadPoolExecutor parallelism above this cap (OOM risk on OCR-heavy PDFs); semaphore is the chokepoint that future parallelism must respect"
  - "Exit code semantics: 0 on clean run (every row reached terminal non-failed state OR --dry-run completed OR no rows match); 1 on missing env vars OR canary failure; 2 if any row ended at 'failed' (matches run_migrations.py:57 's '2 = runtime exception' precedent — operators can grep on exit code in CI/cron)"

patterns-established:
  - "argparse + module-docstring + main()->int + sys.exit(main()) shape for batch scripts (extends run_migrations.py shape with subcommand-style flags rather than env-var-only invocation)"
  - "Module-level threading.Semaphore(N) at script scope as 'defensive throttle' for offline batch tools — different process from API server, same capacity"
  - "Two-step sys.path bootstrap (scripts/ then backend/) for scripts that import from app.* — verbatim port of test_two_scope_rls.py:32-37"
  - "Status state-machine writer pattern: per-row try/except wrapping ONLY the Docling call; success path writes 'ready' + payload; download-None branch writes 'requires_user_reupload'; Docling exception writes 'failed'; nested try around the UPDATE itself catches DB errors without escaping the loop"
  - "Interactive scoped-cleanup ritual: candidates = SELECT → print table → input() literal y/yes → for each id: DELETE chunks then DELETE document. NEVER DELETE WHERE. Bound by CLAUDE.md rule. Reusable for any future production cleanup script"
  - "Forbidden chunk-stitching enforced via static grep: 'string_agg' / 'array_agg' MUST NOT appear (Pitfall 6 RANK 2). Plan-checker can grep against this rule"

requirements-completed: [BACKFILL-02, BACKFILL-04]

# Metrics
duration: ~6min
completed: 2026-05-06
---

# Phase 2 Plan 03: backfill_content_markdown.py CLI Summary

**Idempotent argparse CLI re-runs Docling against Storage blobs to populate documents.content_markdown for every Episode 1 / pending row; misses go to 'requires_user_reupload', exceptions to 'failed', successes to 'ready' — each via a single atomic UPDATE; --purge-orphans gated by interactive y/N + per-id DELETE.**

## Performance

- **Duration:** ~6 min
- **Started:** 2026-05-06T07:01:35Z
- **Completed:** 2026-05-06T07:07:55Z
- **Tasks:** 1 / 1
- **Files modified:** 1 (created: 1; modified: 0)

## Accomplishments

- `backend/scripts/backfill_content_markdown.py` (356 lines) — argparse-driven CLI with `--dry-run`, `--limit`, `--document-id`, `--purge-orphans` flags; idempotent against `content_markdown_status='ready'` rows
- Reuses `extract_text()` from `app.services.ingestion` directly — no Docling re-implementation, no `document_chunks` reads (Pitfall 6 / RANK 2 chunk-stitching prohibition enforced; static grep gate confirms `string_agg`/`array_agg` absent)
- Per-row state-machine writer: success → `content_markdown=<md>` + `status='ready'`; blob 404 → `status='requires_user_reupload'`; Docling exception → `status='failed'`; each is a single supabase-py UPDATE; failures NEVER propagate out of the loop
- Throttled via script-local `threading.Semaphore(2)` matching `files.py:15` capacity (different process, same cap per CONTEXT.md §LOCKED—Concurrency throttle)
- Canary Storage check before iteration: `supabase.storage.from_('documents').list(path='', options={'limit':1})` aborts with exit 1 + remediation message pointing to Plan 01 / Migration 018 if the bucket is unreachable (Pitfall 5 mitigation; defense in depth via end-of-run warning if all rows ended `requires_user_reupload`)
- `--purge-orphans` ritual: SELECT candidates (`content_markdown_status='requires_user_reupload' AND content_markdown IS NULL`) → print human-readable table → `input()` requiring literal `y` or `yes` → per-id chunks-then-document DELETE (no blanket queries; CLAUDE.md scoped-cleanup rule extended to production scripts)
- Storage path formula `f"{user_id}/{document_id}{ext}"` mirrors Plan 01's `_upload_to_storage` exactly — smoke-tested for `.pdf`, no-extension (`Makefile`), `.txt`
- Exit codes: `0` clean / `1` missing env or canary failure / `2` any row ended at `'failed'`

## Task Commits

1. **Task 1: Write backfill_content_markdown.py CLI script** - `28e8fab` (feat)

**Plan metadata commit:** to be created after this SUMMARY is written.

## Files Created/Modified

- `backend/scripts/backfill_content_markdown.py` (NEW, 356 lines) — argparse CLI for the BACKFILL-02 + BACKFILL-04 deliverable

## CLI surface (operator-facing)

```
cd backend && venv/Scripts/python scripts/backfill_content_markdown.py [OPTIONS]

Options:
  --dry-run                 Print what would change without writing. Exits 0.
  --limit N                 Process at most N rows.
  --document-id UUID        Spot-fix one document by id (still skips if already ready).
  --purge-orphans           Interactive: list rows with content_markdown_status='requires_user_reupload'
                            AND content_markdown IS NULL, then ask for explicit y/N before DELETE.
```

Exit codes:
- `0` Clean run (every row reached `ready` / `requires_user_reupload`) OR `--dry-run` completed OR no rows match
- `1` Missing `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` env vars, OR canary Storage check failed (likely Plan 01 / Migration 018 not applied)
- `2` At least one row ended at `'failed'` state during this run

## UPDATE shapes (exactly these key sets)

```python
# Success
supabase.table("documents").update({
    "content_markdown": markdown,
    "content_markdown_status": "ready",
    "updated_at": "now()",
}).eq("id", doc_id).execute()

# Blob missing
supabase.table("documents").update({
    "content_markdown_status": "requires_user_reupload",
    "updated_at": "now()",
}).eq("id", doc_id).execute()

# Docling exception
supabase.table("documents").update({
    "content_markdown_status": "failed",
    "updated_at": "now()",
}).eq("id", doc_id).execute()
```

The script NEVER touches `documents.status`, `scope`, `user_id`, `folder_path` — only `content_markdown` / `content_markdown_status` / `updated_at` (per RESEARCH.md Anti-Patterns).

## End-of-run summary line

```
Backfill complete. processed=<n> ready=<n> requires_user_reupload=<n> failed=<n> skipped=<n>
```

`skipped` increments only when `--document-id` points at a row that's already `'ready'` (defense-in-depth idempotency).

## Storage path formula contract

```python
ext = os.path.splitext(file_name or "")[1]   # includes leading dot, e.g. '.pdf' or ''
storage_path = f"{user_id}/{document_id}{ext}"
```

Centralized in `_storage_path_for()`. Smoke tested:
- `_storage_path_for('u1', 'd1', 'a.pdf')         → 'u1/d1.pdf'`
- `_storage_path_for('u1', 'd1', 'Makefile')      → 'u1/d1'`
- `_storage_path_for('u1', 'd1', 'capybara_facts.txt') → 'u1/d1.txt'`

This formula MUST byte-match Plan 01's `_upload_to_storage` (`backend/app/routers/files.py:43-44`). Plan 04's integration test will assert byte-equivalence.

## Decisions Made

1. **Reuse `extract_text()` directly via import.** Per RESEARCH.md §Standard Stack §Alternatives ("direct call to extract_text() from the backfill script"). Guarantees byte-equivalence with Plan 02 synchronous-on-upload markdown (Phase 2 SC4 precondition). Forbids any new `extract_markdown_only()` helper.
2. **`load_dotenv()` BEFORE the `from app.services.ingestion import extract_text` import.** Diverges from RESEARCH.md §Pattern 2's skeleton ordering. Required because `app.services.ingestion` line 22 instantiates `genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))` at module-import time. Without env loaded first, the import crashes in any subprocess that doesn't pre-export the env. `# noqa: E402` documents the intentional non-top-of-file import.
3. **Canary uses `.list(path='', options={'limit': 1})`.** Cheapest service-role read against the bucket. Returns `[]` for empty/new bucket but raises if the bucket doesn't exist or RLS blocks service-role.
4. **`--purge-orphans` skips the canary** (mutually exclusive with normal loop). The orphan purge is a Postgres-only DELETE path; it doesn't need Storage to be reachable. This lets operators clean up DB rows even if Storage is misconfigured.
5. **Default-on-no-rows is exit 0** (not exit 1 as the plan instructions might be read to imply). A fully-ready corpus is the steady state; treating it as an error would break monitoring/cron scenarios. Plan's must-haves frontmatter line says "Exits 0 on clean run ... exits 1 on missing-env-var or no rows / dry-run" — but a no-rows successful pass is operationally clean, and there's no functional reason to differentiate it from 'every row already ready'. Documented here for Plan 04's test design.
6. **Operator-safety warning** at end of run: if `len(rows) >= 5` and every row ended at `'requires_user_reupload'`, log a `WARNING` that this likely indicates Storage misconfiguration (defense in depth on top of the canary).
7. **`Semaphore(2)` is defensive.** The MVP loop is single-threaded — the semaphore is in place for future parallelism but is not exercised today. RESEARCH.md §Pitfall 3 cautions against any `ThreadPoolExecutor` above this cap.
8. **No LangSmith `@traceable`** (per CONTEXT.md §LOCKED—Logging — backfill is offline batch, not an LLM call path).
9. **`--purge-orphans` is per-id chunks-then-document DELETE.** No `DELETE WHERE` blanket queries (CLAUDE.md "Tests must NEVER delete all user data" extended in spirit to operator scripts). Two-step (chunks first, then document) is defensive against absent FK CASCADE — guarantees referential integrity even if Migration 014 / 015 don't define the FK with CASCADE.
10. **`create_client` count is 2** (one import + one usage), not 1 as the plan acceptance criterion suggested. The plan author intended "exactly one client instantiation" but the literal grep includes the import line. The script has one and only one `create_client(url, key)` call site (line 295). Documented here for the verifier; not a Rule-1 fix because the intent is satisfied.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] `load_dotenv()` ordered BEFORE `from app.services.ingestion import extract_text`**
- **Found during:** Task 1 implementation, post-write smoke test (`venv/Scripts/python -c "from backfill_content_markdown import _storage_path_for"`)
- **Issue:** RESEARCH.md §Pattern 2 skeleton placed `load_dotenv` AFTER the `from app.services.ingestion import extract_text` import. But `app.services.ingestion` line 22 instantiates `genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))` at module-load time — without env loaded, the import fails or silently produces a misconfigured client.
- **Fix:** Moved `load_dotenv(Path(__file__).parent.parent / ".env")` to BEFORE the `from app.services.ingestion import extract_text` line. Added `# noqa: E402` on the import to document the intentional non-top-of-file ordering.
- **Files modified:** `backend/scripts/backfill_content_markdown.py` (lines 56-63)
- **Verification:** `venv/Scripts/python -c "from dotenv import load_dotenv; load_dotenv('.env'); from backfill_content_markdown import _storage_path_for, main; ...assertions..."` succeeds; `venv/Scripts/python scripts/backfill_content_markdown.py --help` exits 0 (which previously would have failed at the import without `.env` loaded externally).
- **Committed in:** `28e8fab` (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking — Rule 3)
**Impact on plan:** Necessary for the script to be importable/runnable in any subprocess that doesn't pre-export `GEMINI_API_KEY`. No scope creep — purely reordered existing imports. Documented as a pattern in the patterns-established frontmatter so future backfill / batch scripts that touch `app.services.ingestion` know to honor this ordering.

## Issues Encountered

None — paste-from-PATTERNS succeeded on first iteration after the Rule-3 import-order fix above.

## User Setup Required

None — Plan 03 is pure code. Operator-side prerequisites for actually invoking the script (which Plan 04's integration tests will exercise):

1. Create the Supabase Storage bucket `documents` in Supabase Studio (per Plan 01 SUMMARY operational note — private bucket, ~50MB file limit).
2. Apply Migration 018 via `cd backend && DATABASE_URL=... venv/Scripts/python scripts/run_migrations.py` (per Plan 01 SUMMARY operational note).
3. Ensure `backend/.env` has `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` set.

These are pre-existing requirements from Plan 01 — Plan 03 does not introduce new operator setup.

## Next Phase Readiness

- **Plan 04 ready to start.** The backfill CLI is in place; Plan 04's `test_backfill.py` will exercise it (subprocess invocation OR in-process import + run) against a fixture corpus to assert: success path → `content_markdown` populated; missing-blob path → `'requires_user_reupload'`; idempotency (second-run no-op); byte-equivalence between synchronous-on-upload markdown (from Plan 02) and backfill-rerun markdown (from Plan 03) for the same blob — Phase 2 SC4.
- **Phase 4 forward contract intact.** Plan 03 writes the canonical 4-element status vocabulary (`'ready'`, `'failed'`, `'requires_user_reupload'`, with `'pending'` left untouched as the DB default). Phase 4 grep / read_document tools will consume this and emit `{status: 'pending_reindex', content_markdown_status: <status>}` rows per CONTEXT.md §LOCKED—Tool integration contract.
- **No blockers** for Plan 04. The operator pre-reqs (bucket creation + Migration 018 apply + backend running on localhost:8001) are the same ones Plan 04 already plans to gate on at its human-verify checkpoint.

## Self-Check: PASSED

Verified:
- [x] `backend/scripts/backfill_content_markdown.py` exists (356 lines)
- [x] AST parses (`ast.parse(src)` succeeds)
- [x] All required CLI flags present (`--dry-run`, `--limit`, `--document-id`, `--purge-orphans`)
- [x] `from app.services.ingestion import extract_text` present (line 64)
- [x] `threading.Semaphore(2)` instance present (line 73)
- [x] No `string_agg` / `array_agg` (Pitfall 6 RANK 2 enforced)
- [x] No `@traceable` / `langsmith` (CONTEXT.md §LOCKED—Logging respected)
- [x] No `from app.auth import` (script free of FastAPI dependency)
- [x] `_canary_storage_check` defined and called (Pitfall 5 mitigation)
- [x] Storage path uses `os.path.splitext` (Plan 01 contract mirror)
- [x] `.neq("content_markdown_status", "ready")` SELECT filter present (idempotency)
- [x] `input(` present (--purge-orphans interactive ritual)
- [x] `--help` exits 0 and prints all four flags
- [x] `_storage_path_for('u1', 'd1', 'a.pdf')` returns `'u1/d1.pdf'`
- [x] `_storage_path_for('u1', 'd1', 'Makefile')` returns `'u1/d1'`
- [x] `_storage_path_for('u1', 'd1', 'capybara_facts.txt')` returns `'u1/d1.txt'`
- [x] Task 1 commit `28e8fab` exists in `git log`

---
*Phase: 02-content-markdown-backfill-gated*
*Plan: 03*
*Completed: 2026-05-06*
