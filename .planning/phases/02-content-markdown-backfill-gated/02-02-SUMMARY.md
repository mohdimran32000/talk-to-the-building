---
phase: 02-content-markdown-backfill-gated
plan: 02
subsystem: ingestion

tags:
  - docling
  - supabase-postgrest
  - atomic-update
  - content-markdown
  - version-pin

# Dependency graph
requires:
  - phase: 01-schema-foundation
    provides: "Migration 014 added documents.content_markdown TEXT (nullable) + documents.content_markdown_status TEXT NOT NULL DEFAULT 'pending' with CHECK IN ('pending','ready','failed','requires_user_reupload') — this plan transitions the status column to 'ready' on success and 'failed' on failure."
  - phase: 02-content-markdown-backfill-gated/01
    provides: "Storage upload at upload-time so future re-Docling can recover blobs (independent code path; this plan does not depend on Storage runtime — synchronous markdown comes directly from extract_text() return value)."
provides:
  - "Synchronous content_markdown population on every new upload via ingest_document() (no follow-up job)"
  - "Synchronous content_markdown population on every re-ingest via ingest_document_update() (identical edit shape)"
  - "Atomic single-statement UPDATE pattern: status='ready' + content_markdown=text + content_markdown_status='ready' all flip in one PostgREST call (no half-state where status='ready' AND content_markdown_status='pending')"
  - "Failure-path content_markdown_status='failed' write so Phase 4 grep/read_document tools surface 'pending_reindex' rows correctly (BACKFILL-04 surfacing precondition)"
  - "docling==2.91.0 version pin in requirements.txt — byte-equivalence determinism across upload-time and Plan 03 backfill-time Docling runs against the same blob (Phase 2 SC4 precondition)"
affects:
  - "02-03 (backfill_content_markdown.py — must use the same canonical 4-element status vocabulary 'pending'|'ready'|'failed'|'requires_user_reupload' established here; will reuse extract_text() to guarantee byte-equivalence with synchronous-on-upload writes)"
  - "02-04 (test_backfill.py — will assert byte-equivalence between synchronous-on-upload markdown and backfill markdown for the same blob; the docling pin from Task 2 is the precondition)"
  - "04-five-exploration-tools (grep + read_document — will read content_markdown populated by this plan; will honor content_markdown_status field per the LOCKED tool integration contract in 02-CONTEXT.md)"
  - "06-file-explorer-ui (UI-08 'needs re-index' badge — reads content_markdown_status to decide when to render)"

# Tech tracking
tech-stack:
  added:
    - "First version pin in backend/requirements.txt (docling==2.91.0) — establishes the precedent for future targeted pins; the other 12 deps remain unpinned per CONTEXT.md scope discipline"
  patterns:
    - "Atomic-multi-field UPDATE for state-machine transitions: when one column flips status (e.g., status='ready') and other columns must be coherent at the same moment (e.g., content_markdown_status='ready'), pack all of them into a SINGLE supabase-py .update({...}).execute() call so PostgREST issues one SQL UPDATE statement — atomic by Postgres semantics. NEVER split into two .update() calls (that admits a half-state window)."
    - "Multi-line UPDATE dict style for 5+ keys: switch from single-line update({...}) to multi-line update({\\n    'key': value,\\n    ...\\n}) for readability — matches the style used in plan 01's _upload_to_storage helper and Phase 1 migration headers"
    - "Inline requirement-ID comments (# BACKFILL-01, # BACKFILL-04) on the lines they implement — references REQUIREMENTS.md IDs for traceability without bloating commit messages"
    - "Targeted dependency pinning rationale: pin a dep ONLY when a downstream test asserts byte/byte determinism against its output (Docling here, for Phase 2 SC4); leave the rest unpinned to keep dep upgrades cheap"

key-files:
  modified:
    - "backend/app/services/ingestion.py — extended both ingest_document() and ingest_document_update() success/failure UPDATE dicts with content_markdown + content_markdown_status keys; logger.info on success extended with markdown char count"
    - "backend/requirements.txt — pinned docling==2.91.0 (was bare 'docling')"

key-decisions:
  - "Single atomic UPDATE per success path (vs. two-step write-markdown-then-flip-status): the success UPDATE carries status='ready' + content_markdown=text + content_markdown_status='ready' together so a half-state cannot exist (Pitfall 2 mitigation per CONTEXT.md §LOCKED—Synchronous-on-upload paragraph 4)"
  - "Reuse the `text` variable already returned by extract_text() at L395 / L481 (zero re-extraction, zero new helper function, zero new background_tasks.add_task call) — minimal-shape edit"
  - "Failure-path UPDATE in BOTH ingest functions writes content_markdown_status='failed' alongside status='failed' so Phase 4 tools can surface the row as {status: 'pending_reindex', content_markdown_status: 'failed'} per the LOCKED tool integration contract in 02-CONTEXT.md §LOCKED—Tool integration contract — never silently empty"
  - "Pin docling==2.91.0 (single-line edit; other 12 deps unchanged) — required for Phase 2 SC4 byte-equivalence between upload-time markdown (Task 1) and backfill-time markdown (Plan 03)"
  - "Use canonical 4-element status vocabulary 'pending'|'ready'|'failed'|'requires_user_reupload' from Migration 014 — explicitly NOT 'ok' (ROADMAP additional-context error) and NOT 'processing' (belongs to documents.status)"

patterns-established:
  - "State-machine atomic-UPDATE pattern: any future ingestion-pipeline edit that adds a new derived column whose population must be coherent with documents.status MUST extend the same .update({...}) dict — never add a follow-up call. This is now the convention referenced in CONTEXT.md §specifics ('one atomic UPDATE — if X write fails the row should not be left half-updated')"
  - "Synchronous-derived-column convention: any column that is a deterministic function of extract_text() output (content_markdown today, future doc_summary or doc_outline if added) is computed in the SAME try-block and written in the SAME UPDATE — never deferred to a background task (Pitfall 2 mitigation extends to all such columns)"
  - "Status-vocabulary discipline: Migration 014's CHECK constraint enforces 4 values ('pending'|'ready'|'failed'|'requires_user_reupload'); Plan 02 occupies 'ready' (success) and 'failed' (Docling exception path); Plan 03 backfill occupies 'ready' (re-Docling success) + 'failed' (re-Docling exception) + 'requires_user_reupload' (blob missing). The vocabulary partition is now locked across upload-time and backfill-time code paths"

requirements-completed:
  - BACKFILL-01

# Metrics
duration: ~5 min
completed: 2026-05-06
---

# Phase 2 Plan 02: Synchronous content_markdown Write Summary

**Atomic single-UPDATE captures Docling's canonical markdown export inside ingest_document() and ingest_document_update() — content_markdown=text + content_markdown_status='ready' flip together with status='ready'; failure path writes content_markdown_status='failed'. Plus docling==2.91.0 pinned for byte-equivalence determinism.**

## Performance

- **Duration:** ~5 min (active execution)
- **Started:** 2026-05-06T06:53Z
- **Completed:** 2026-05-06T06:58:47Z
- **Tasks:** 2/2
- **Files modified:** 2 (no files created — both edits are in-place)

## Accomplishments

- **ingest_document() success UPDATE (formerly L437-439, now L437-444):** extended dict with `content_markdown=text` + `content_markdown_status='ready'` + multi-line readable dict style; status, content_hash, content_markdown, content_markdown_status, updated_at all flip together in one PostgREST call. Logger now reports markdown char count alongside chunk count.
- **ingest_document() failure UPDATE (formerly L446-448, now L451-455):** extended dict with `content_markdown_status='failed'` so Phase 4 tools can surface non-ready rows correctly (BACKFILL-04 surfacing precondition).
- **ingest_document_update() success UPDATE (formerly L513-515, now L519-526):** identical edit shape as ingest_document() — re-ingest path also captures markdown synchronously.
- **ingest_document_update() failure UPDATE (formerly L522-524, now L535-539):** identical failure-path edit as ingest_document().
- **requirements.txt:** bare `docling` → `docling==2.91.0` pin (the FIRST version pin in the file; other 12 deps unchanged).

## Task Commits

Each task was committed atomically:

1. **Task 1: Add synchronous content_markdown write to ingest_document() and ingest_document_update() (success + failure paths)** — `4dd7c4c` (feat)
2. **Task 2: Pin docling==2.91.0 in requirements.txt for byte-equivalence determinism** — `91ad425` (chore)

**Plan metadata:** to be committed alongside this SUMMARY + STATE.md + ROADMAP.md updates.

## Files Created/Modified

- `backend/app/services/ingestion.py` — 4 in-place edits (success UPDATE in ingest_document, failure UPDATE in ingest_document, success UPDATE in ingest_document_update, failure UPDATE in ingest_document_update); +35 lines / -15 lines net change. The `text` variable returned by `extract_text()` at L395 / L481 is reused — zero re-extraction, zero new helper functions, zero new background_tasks calls. File grew from 527 to 546 lines (above the must_haves min_lines=520 gate).
- `backend/requirements.txt` — single-line edit on line 10: `docling` → `docling==2.91.0`. Total file line count unchanged at 13. The other 12 dependencies (fastapi, uvicorn[standard], python-dotenv, pydantic, google-genai, supabase, langsmith, sse-starlette, python-multipart, cohere, duckdb, tavily-python) remain unpinned per CONTEXT.md scope discipline.

## Decisions Made

None beyond what was already locked in 02-CONTEXT.md and 02-PATTERNS.md — the plan was executed exactly as written. Specifically:

- Atomic single-UPDATE shape (status + content_markdown + content_markdown_status flip together) is verbatim from CONTEXT.md §LOCKED—Synchronous-on-upload + §specifics paragraph 7
- The `text` variable reuse from extract_text() return is verbatim from RESEARCH.md §Pattern 1 + PATTERNS.md "MODIFY — synchronous-on-upload write"
- The canonical 4-element status vocabulary (`'pending'|'ready'|'failed'|'requires_user_reupload'`) is from Migration 014's CHECK constraint
- The multi-line UPDATE dict style (`update({\n    "key": value,\n    ...\n})`) for 5+ keys matches Plan 01's `_upload_to_storage` helper style established in commit 41e3eeb
- Inline `# BACKFILL-01` and `# BACKFILL-04` comments reference REQUIREMENTS.md IDs per project traceability convention
- The docling==2.91.0 pin is the version verified-currently-installed per RESEARCH.md §Standard Stack `pip show docling`

## Deviations from Plan

**None — plan executed exactly as written.**

The four edit sites all matched the PATTERNS.md paste-ready excerpts verbatim. Both functions' signatures are unchanged. No background_tasks.add_task calls were added. No re-extraction, no new helper functions, no langchain/langgraph imports, no `string_agg`/`array_agg` chunk-stitching anti-patterns introduced.

## Issues Encountered

**Plan-verification-gate inconsistency (NOT a deviation in code; observation only):**

The plan's automated verification step asserts `body.count('extract_text(') == 2` ("extract_text should be called exactly twice"). However, `extract_text` is also defined in this file at L62 (`def extract_text(...)`), so `body.count('extract_text(')` returns 3 (1 definition + 2 calls). This was true before my edit too — the gate's expected value of 2 is unachievable for any version of the file that defines `extract_text` locally. The intent of the gate is clear: ensure no NEW call to `extract_text()` is introduced (i.e., no re-extraction). The actual call count is unchanged at 2 (L395 and L481 — the same two pre-existing call sites). I confirmed this by computing `body.count('extract_text(') - body.count('def extract_text(') == 2`. Same applies to the parallel grep-based acceptance criterion. All other 12 grep-based acceptance gates pass exactly. Recommendation for future plans: either the verifier should subtract `def`-definition occurrences, or it should use `body.count('extract_text(file_content')` to target the call signature specifically.

This was the only friction point; the actual edits are correct and the function signatures + behavior are preserved exactly as intended.

## Threat Model Compliance

- **T-2-05 (Tampering / Atomicity)** — MITIGATED: success UPDATE is a single supabase-py `.update({...})` call carrying ALL three transitions (`status='ready'`, `content_markdown=text`, `content_markdown_status='ready'`). PostgREST → single SQL UPDATE → atomic by Postgres semantics. There is NO window where `status='ready' AND content_markdown_status='pending'`. No new background_tasks.add_task call was introduced (the patch lives entirely inside existing function bodies). Verified: `grep -c "background_tasks.add_task" backend/app/services/ingestion.py` returns 0.
- **T-2-06 (Information Disclosure / Silent Failure)** — MITIGATED: both except blocks now write `content_markdown_status='failed'` alongside `status='failed'` and `error_message`. Phase 4 tools (grep/read_document) can surface `{status: 'pending_reindex', content_markdown_status: 'failed'}` per the LOCKED tool integration contract — never silent empty result. Verified: `"content_markdown_status": "failed"` appears in exactly 2 except-branch UPDATEs.
- **T-2-07 (Determinism / Byte-Equivalence)** — MITIGATED: `requirements.txt` now pins `docling==2.91.0`. Phase 2 SC4 (byte-equivalence ±20 chars between upload-time and backfill-time markdown for the same blob) is mathematically true if both calls use the same Docling version. The pin is the single point of determinism. Verified: `grep -c "==" backend/requirements.txt` returns exactly 1, and that line is `docling==2.91.0`.

## User Setup Required

None for this plan. Plan 01's pre-requisites (create the `documents` Storage bucket via Supabase Studio + apply Migration 018) carry forward and remain pre-conditions for Plan 04's integration tests.

## Next Phase Readiness

- **Plan 03 (backfill_content_markdown.py CLI)** is unblocked. The status vocabulary partition is now locked: Plan 03's backfill occupies `'ready'` (re-Docling success), `'failed'` (re-Docling exception), and `'requires_user_reupload'` (blob missing). Plan 03 must `from app.services.ingestion import extract_text` to inherit byte-equivalent Docling output (the docling==2.91.0 pin is the precondition).
- **Plan 04 (test_backfill.py integration suite)** is unblocked at the contract level. The byte-equivalence assertion (SC4) compares (a) `content_markdown` written by Plan 02 Task 1's synchronous UPDATE against (b) `content_markdown` written by Plan 03's backfill against the same blob — both call `extract_text()` so they will be byte-equal modulo any non-deterministic OCR pass (which is bounded by the ±20 char SC4 tolerance).
- **Phase 4 (Five Exploration Tools)** has its content-data dependency satisfied for new uploads landing post-merge of this plan. Existing pre-Phase-2 documents will still have NULL `content_markdown` until Plan 03's backfill runs against them — Phase 4's tools must honor `content_markdown_status` to gracefully surface those rows (the LOCKED tool integration contract is the enforced shape).

## Self-Check: PASSED

- File `backend/app/services/ingestion.py` exists, parses cleanly via `ast.parse`, signatures of `ingest_document` and `ingest_document_update` unchanged: VERIFIED
- File `backend/requirements.txt` exists, contains the literal line `docling==2.91.0`, has exactly 13 lines, exactly 1 `==` pin, and 12 other deps unchanged: VERIFIED
- Commit `4dd7c4c` exists in git log (Task 1: feat — synchronous content_markdown write): FOUND
- Commit `91ad425` exists in git log (Task 2: chore — docling pin): FOUND
- Min-line gates: ingestion.py 546 lines (≥520), requirements.txt 13 lines (=13): PASS
- Acceptance counts (excluding `^[[:space:]]*#` comment lines): `"content_markdown": text` × 2, `"content_markdown_status": "ready"` × 2, `"content_markdown_status": "failed"` × 2, `"content_markdown_status": "ok"` × 0, `"content_markdown_status": "requires_user_reupload"` × 0: VERIFIED
- No `background_tasks.add_task` in ingestion.py: VERIFIED (Pitfall 2)
- No `langchain` or `langgraph` imports in ingestion.py: VERIFIED (project rule)
- No `string_agg` or `array_agg` in ingestion.py: VERIFIED (Pitfall 6 RANK 2)
- `def ingest_document(` × 1, `def ingest_document_update(` × 1: VERIFIED
- `extract_text(` call sites: 2 (L395, L481) — no third call introduced for re-extraction: VERIFIED (call count = total occurrences 3 minus 1 definition = 2)

---

*Phase: 02-content-markdown-backfill-gated*
*Plan: 02 — Synchronous content_markdown write + docling pin*
*Completed: 2026-05-06*
