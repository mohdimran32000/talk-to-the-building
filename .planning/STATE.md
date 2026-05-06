---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
stopped_at: Phase 2 / Plan 02 EXECUTED — Synchronous content_markdown write delivered. Both ingest_document() and ingest_document_update() success UPDATEs atomically carry status='ready' + content_markdown=text + content_markdown_status='ready' (single PostgREST call — no half-state); both failure UPDATEs write content_markdown_status='failed' (BACKFILL-04 surfacing precondition for Phase 4 tools). docling==2.91.0 pinned in requirements.txt for byte-equivalence determinism (Phase 2 SC4 precondition). 2 atomic commits (4dd7c4c feat + 91ad425 chore).
last_updated: "2026-05-06T06:58:47Z"
last_activity: 2026-05-06 -- Phase 2 / Plan 02 executed; 2 atomic commits (4dd7c4c feat + 91ad425 chore); next plan in Phase 2 is 03 (backfill_content_markdown.py CLI)
progress:
  total_phases: 6
  completed_phases: 1
  total_plans: 12
  completed_plans: 10
  percent: 83
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-01)

**Core value:** The agent can locate the *right* piece of information in a large, organized knowledge base — using semantic search when meaning matters and codebase-style traversal (tree/glob/grep/read) when precision matters — without hallucinating across unrelated material.
**Current focus:** Phase 2 — content_markdown Backfill (Gated)

## Current Position

Phase: 2 of 6 EXECUTING — content_markdown Backfill (Gated)
Plan: 2 of 4 in phase 2 done; next is 03-PLAN.md (backfill_content_markdown.py CLI — BACKFILL-02 + BACKFILL-04)
Status: Phase 2 / Plan 02 (synchronous content_markdown write + docling pin) complete; new uploads now populate content_markdown atomically inside ingest_document() / ingest_document_update(); Migration 018 still PENDING operator apply; 'documents' bucket still PENDING one-time Supabase Studio creation
Last activity: 2026-05-06 -- Phase 2 / Plan 02 executed; 2 atomic commits + SUMMARY

Progress: [█████░░░░░] 50% (2/4 plans in Phase 2); Project: 17% (1/6 phases complete; Phase 2 in flight)

## Performance Metrics

**Velocity:**

- Total plans completed: 10 (Phase 1: 8, Phase 2: 2)
- Average duration: ~2.2 min
- Total execution time: ~22 min

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1 | 8 | ~12 min | ~1.5 min |
| 2 | 2 (in flight) | ~10 min | ~5 min |

**Recent Trend:**

- Last 7 plans: 01-04 (~1 min, 1 file, 1 task) → 01-05 (~3 min, 1 file, 1 task — 1 Rule-1 auto-fix) → 01-06 (~2 min, 1 file, 1 task — same Rule-1 pattern) → 01-07 (apply migrations) → 01-08 (RLS matrix tests passed 49/0) → 02-01 (~5 min, 2 files, 2 tasks — Storage Gap closure: files.py upload + Migration 018 RLS; zero deviations, paste-from-PATTERNS succeeded on first iteration) → **02-02 (~5 min, 2 files, 2 tasks — synchronous content_markdown write in ingest_document() + ingest_document_update() success/failure UPDATEs atomic; docling==2.91.0 pin; zero deviations, paste-from-PATTERNS succeeded; only friction was a plan-verifier gate inconsistency on the extract_text count noted in SUMMARY)**
- Trend: ✅ on-spec, paste-from-PATTERNS succeeded for both Phase 2 plans so far; the atomic-multi-field-UPDATE pattern (state-machine transitions packed into a single PostgREST call) is now established and inherited by Plan 03's backfill writes

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Phase 1: Path-based folder model (`folder_path TEXT` + thin `folders` side table) chosen over `ltree` — folder-name charset (hyphens, dots, spaces) and LLM ergonomics
- Phase 1: `documents.content_markdown` stored alongside chunks rather than reconstructed on demand — chunk overlap would corrupt grep line numbers
- Phase 1: Two-scope model (`user` + `global`) with admin-only writes; tools default `scope='both'` with override arg
- Phase 1: Five small migrations (012–016) over one mega-migration — individually reviewable + revertable
- Phase 1 / Plan 01: `normalize_path()` uses stdlib only (`re`, `unicodedata`); raises `ValueError` (not custom exception) for invalid input; NFC Unicode normalization; case preserved (Postgres comparison is case-sensitive — `/Projects` ≠ `/projects` is intentional)
- Phase 1 / Plan 01: Inline `__main__` self-tests (15 cases) — fast sanity check; full matrix lives in plan 08's `test_two_scope_rls.py`
- Phase 1 / Plan 02: Migration 012 enables `pg_trgm` early (not in 016) — eliminates dependency-ordering surprises since `CREATE EXTENSION IF NOT EXISTS` is sub-second on Supabase
- Phase 1 / Plan 02: Scope-aware unique index uses `COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid)` sentinel — Postgres treats NULL as distinct in unique indexes by default; sentinel forces global rows to compete in the same uniqueness namespace (Pitfall 10 mitigation)
- Phase 1 / Plan 02: `folder_path` is NOT denormalized onto `document_chunks` — chunks get `scope` only; defer path denormalization until Phase 4 query plans show join cost is unacceptable (RESEARCH.md Open Question §8)
- Phase 1 / Plan 02: Idempotent migration shape: drop-then-add for CHECK constraints (Postgres has no `ADD CONSTRAINT IF NOT EXISTS`), `IF NOT EXISTS` everywhere else; established as Phase 1 migration convention
- Phase 1 / Plan 03: `public.folders` is a sparse side table for first-class empty-folder tracking — no FK from `documents.folder_path` to `folders.path` (per ARCHITECTURE.md Pattern 2); most folders exist by inference from `documents.folder_path`, and rows in `folders` exist only for explicitly-empty folders
- Phase 1 / Plan 03: RLS policies for `public.folders` deferred to migration 015 (lands the full Phase 1 RLS catalog — documents, document_chunks, folders — in one reviewable file); RLS-enabled-no-policies = fail-closed default for the authenticated role until 015 runs
- Phase 1 / Plan 03: Inline `CONSTRAINT` clauses in `CREATE TABLE` for new tables (vs. drop-then-add for existing tables); the simpler form applies when the table itself is gated by `IF NOT EXISTS`
- Phase 1 / Plan 03: Re-used the COALESCE sentinel (`'00000000-0000-0000-0000-000000000000'::uuid`) idiom from migration 012 — same Pitfall 10 mitigation pattern, this time on the folders side table; bedrock for Phase 3's `INSERT ... ON CONFLICT DO NOTHING` concurrent-upload safety
- Phase 1 / Plan 04: Migration 014 uses TEXT + CHECK constraint (not Postgres ENUM type) for `content_markdown_status` — `ALTER TYPE ADD VALUE` is painful and Postgres-version-sensitive; TEXT + CHECK evolves cleanly via DROP/ADD CONSTRAINT
- Phase 1 / Plan 04: Canonical 4-element vocabulary `'pending'`, `'ready'`, `'failed'`, `'requires_user_reupload'` per REQUIREMENTS.md SCHEMA-03 — deliberately rejected `'ok'` (ROADMAP additional context error) and `'processing'` (belongs to documents.status)
- Phase 1 / Plan 04: Partial index `WHERE content_markdown_status <> 'ready'` is a new convention for this codebase — makes Phase 2 backfill scan O(rows-needing-backfill); index stays small in steady state; called out in migration header
- Phase 1 / Plan 04: `content_markdown` deliberately nullable with no DEFAULT — Phase 2's backfill_content_markdown.py owns population; making it NOT NULL would block migration on existing rows
- Phase 1 / Plan 05: Migration 015 uses **snake_case policy naming** (`documents_select`, `documents_insert_user`, `documents_insert_global`, ...) — deliberate shift from Episode-1 sentence-case (`"Users can view own documents"`); makes the (table, op, scope) decomposition obvious in `pg_policy`; called out in migration header
- Phase 1 / Plan 05: Migration 015 wraps every `auth.uid()` reference as `(SELECT auth.uid())` — Postgres caches the subquery result per query (10× faster than bare `auth.uid()` per row on hot tables); first use of this Pitfall 5 perf-wrap pattern in the codebase; explicitly called out in migration design notes
- Phase 1 / Plan 05: RLS-03 enforcement uses a **BEFORE UPDATE trigger** (`forbid_scope_mutation` raising `check_violation` when `NEW.scope IS DISTINCT FROM OLD.scope`) — NOT `WITH CHECK (scope = OLD.scope)` (which is invalid Postgres; RLS WITH CHECK cannot reference OLD.col). Critical correction from the original phase brief
- Phase 1 / Plan 05: `public.is_admin()` SQL helper (LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public) factors out the EXISTS-from-profiles admin check used in 8 policies — DRY; STABLE for per-statement caching; reads `is_admin` from profiles at query time (no JWT-cached claim, avoids "admin demotion mid-session stale-cache" risk)
- Phase 1 / Plan 05: 5 chunks policies (no UPDATE) vs. 7 documents/folders policies — `document_chunks` is insert-and-delete only (re-ingestion is delete-then-insert per record_manager); the trigger is still attached to chunks defensively in case a future migration adds a chunks UPDATE policy
- Phase 1 / Plan 05: Global-scope INSERT policies require `user_id IS NULL` alongside `scope='global' AND public.is_admin()` — defense in depth with the scope/user_id coupling CHECK from plan 02; even an admin cannot insert a malformed `(scope='global', user_id=<uuid>)` row
- Phase 1 / Plan 06: Migration 016 adds 5 search-acceleration indexes (3 GIN `gin_trgm_ops` + 2 btree `text_pattern_ops`) — both operator classes are net-new in this codebase; called out in migration header. `gin_trgm_ops` for ILIKE/regex acceleration on TEXT (Phase 4 grep + glob substring); `text_pattern_ops` REQUIRED for `LIKE 'prefix/%'` because Supabase runs en_US.UTF-8 and default-collation btree silently does NOT accelerate prefix LIKE in non-C locales (Pitfall 4 perf table foot-gun)
- Phase 1 / Plan 06: All indexes use plain `CREATE INDEX` (non-concurrent) — runner wraps each migration in a transaction, concurrent variant forbidden inside transactions. Production-scale upgrade path (drop + recreate with concurrent variant during maintenance window) documented in migration header for operators at 10k+ docs per user
- Phase 1 / Plan 06: pg_trgm extension boundary preserved — extension lives in 012, indexes live in 016; no re-enable in 016. Composite `(scope, COALESCE(user_id,'00..0'::uuid), folder_path)` index DEFERRED to Phase 4 per RESEARCH.md §4 / Open Question §7 — speculative addition risks index bloat and slows writes; add only when EXPLAIN ANALYZE on actual Phase 4 query shapes shows it's needed
- Phase 1 / Plan 06: Comment-keyword-case discipline established as convention — when a migration's own verifier asserts a keyword's absence via case-sensitive substring match (e.g., `'CONCURRENTLY' not in sql`), use the lowercase form of the keyword in design-note comments. Postgres SQL is case-insensitive so the lowercase form is semantically identical valid SQL, AND it sidesteps the verifier collision. Same Rule-1 pattern as plan 05's fix
- Phase 2: Backfill re-runs Docling against original Storage blobs (NOT chunk stitching); blobs that are GC'd → `requires_user_reupload`
- Phase 2 / Plan 01-04 (planning): **Storage Gap discovered** — pre-Phase-2 codebase has zero Supabase Storage calls; "re-run Docling on original blobs" is impossible for Episode 1 docs. Resolved as Option A: add Storage upload now (Migration 018 + files.py edit). User explicitly permitted opt-in `--purge-orphans` cleanup of Episode 1 orphans via the backfill script (NOT a migration, per CLAUDE.md "no DELETE/TRUNCATE in migrations" rule). Cleanup is interactive (confirmation prompt), per-id (never blanket), and chunks-then-document two-step.
- Phase 2 / Plan 01-04 (planning): Tool integration contract for Phase 4 LOCKED in 02-CONTEXT.md §LOCKED—Tool integration contract — when Phase 4 grep/read encounter `content_markdown_status != 'ready'` they return `{document_id, file_name, scope, folder_path, status: 'pending_reindex', content_markdown_status: <pending|failed|requires_user_reupload>}`. Phase 4 plan-checker will enforce this shape.
- Phase 2 / Plan 02 (planning): Synchronous content_markdown write is a single atomic UPDATE extension at ingestion.py L437 (and L513 for the update path); reuses the `text` variable already in scope from `extract_text()` — zero re-extraction; status='ready' AND content_markdown=text written together so a half-state cannot exist
- Phase 2 / Plan 03 (planning): backfill script reuses `extract_text()` directly (`from app.services.ingestion import extract_text`) instead of re-implementing Docling — guarantees byte-equivalence for SC4. `string_agg`/`array_agg` are forbidden by static grep gate (Pitfall 6 RANK 2 enforcement)
- Phase 2 / Plan 01 (executed): Storage path is computed-from-id `f"{user_id}/{doc_id}{ext}"` with `ext = os.path.splitext(file_name)[1]` — NOT persisted as a `documents.storage_path` column (avoids a migration). Plan 03's backfill MUST mirror the identical formula on download. Files without an extension produce `{user_id}/{doc_id}` (no trailing dot)
- Phase 2 / Plan 01 (executed): Storage upload helper `_upload_to_storage()` is a private module-level function in `files.py`, called BEFORE `background_tasks.add_task` in BOTH the action='create' and action='update' branches. Failure is non-fatal (try/except + `logger.warning`) — extends the existing `ingestion.py:407-408,444-450` non-fatal convention to a third site (Storage); ingest still reaches `status='ready'` even if Storage is unavailable
- Phase 2 / Plan 01 (executed): Migration 018 follows the migration-015 RLS-policy convention — quoted snake_case names (`documents_storage_select_own`, `documents_storage_insert_own`), `TO authenticated`, perf-cached `(SELECT auth.uid())`, idempotent via `DROP POLICY IF EXISTS` before `CREATE POLICY`. New convention established: `<bucket>_storage_<operation>_<scope>` for storage.objects policies (extends the table-policy naming pattern to the storage schema)
- Phase 2 / Plan 01 (executed): Bucket creation is documented in the migration header as a one-time Supabase Studio task — NOT performed by SQL. Bucket-level config (MIME allowlist, file-size limit) doesn't belong in DDL. Operator must (a) create the `documents` bucket via Studio AND (b) apply Migration 018 before Plan 04's integration tests can pass
- Phase 2 / Plan 02 (executed): Atomic-multi-field UPDATE pattern is now the codebase convention for state-machine column transitions — when one column flips status (e.g., `documents.status='ready'`) and other columns must be coherent at the same moment (e.g., `content_markdown_status='ready'`), pack ALL of them into a SINGLE supabase-py `.update({...}).execute()` call so PostgREST issues one SQL UPDATE statement. NEVER split into two `.update()` calls (admits a half-state window). Mitigates Pitfall 2 ("Background-tasking the synchronous write breaks the atomicity guarantee") for content_markdown and any future derived columns
- Phase 2 / Plan 02 (executed): Failure-path UPDATEs in BOTH `ingest_document()` and `ingest_document_update()` now write `content_markdown_status='failed'` alongside `status='failed'` so Phase 4 grep/read_document tools surface non-ready rows as `{status: 'pending_reindex', content_markdown_status: 'failed'}` per the LOCKED tool integration contract — never silently empty. The canonical 4-element status vocabulary `'pending'|'ready'|'failed'|'requires_user_reupload'` (from Migration 014) is partitioned across plans: 02 occupies 'ready' (success) + 'failed' (Docling exception); 03 will occupy 'ready' (re-Docling success) + 'failed' (re-Docling exception) + 'requires_user_reupload' (blob missing)
- Phase 2 / Plan 02 (executed): First version pin in `backend/requirements.txt` — `docling==2.91.0` (was bare `docling`). Other 12 deps remain unpinned. Targeted-pin convention established: pin a dep ONLY when a downstream test asserts byte/byte determinism against its output (Phase 2 SC4 byte-equivalence between upload-time markdown from Plan 02 and backfill-time markdown from Plan 03 depends on identical Docling version). Plan 03 inherits the pin via the shared backend venv
- Phase 2 / Plan 02 (executed): Multi-line UPDATE dict style adopted for `update({...})` calls with 5+ keys — one key per line for readability, matches Plan 01's `_upload_to_storage` helper style. Inline `# BACKFILL-01` and `# BACKFILL-04` comments reference REQUIREMENTS.md IDs on the lines they implement (project traceability convention)
- Phase 5: SSE sub-agent event protocol generalized at the second sub-agent (Explorer), not bolted on
- Phase 6: Drag-drop uses native HTML5 (no `react-arborist` / `dnd-kit` / `react-dnd`)

### Pending Todos

[From .planning/todos/pending/ — ideas captured during sessions]

None yet.

### Blockers/Concerns

[Issues that affect future work]

- **Rank-1 pitfall to design out in Phase 1: two-scope RLS scope-leak.** Separate INSERT/UPDATE policies per scope, `WITH CHECK (scope = OLD.scope)` forbidding scope mutation, CHECK constraint coupling scope/user_id. Gate Phase 2 on `test_two_scope_rls.py` cross-user × cross-scope matrix passing 100%.
- Phase 2 operational risk: Storage retention for original blobs (some Episode 1 blobs may be GC'd) — `requires_user_reupload` fallback is non-negotiable.
- Open question for Phase 4 planning: token budget defaults for `tree`/`grep` and `read_document.limit`; whether `scope` is explicit arg vs. implicit-from-folder_path (likely both, with explicit winning).
- Open question for Phase 5 planning: token budget for Explorer's compact summary output.

## Deferred Items

Items acknowledged and carried forward from previous milestone close:

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| *(none)* | | | |

## Session Continuity

Last session: 2026-05-06
Stopped at: Phase 2 / Plan 02 EXECUTED — Synchronous content_markdown write delivered.
  - ✅ 01-PLAN.md: Storage upload at upload-time + Migration 018 storage.objects RLS (commits 41e3eeb, e256c91; SUMMARY at 02-01-SUMMARY.md)
  - ✅ 02-PLAN.md: Synchronous content_markdown write in ingest_document() + ingest_document_update() (atomic single-UPDATE) + docling==2.91.0 pin (BACKFILL-01); commits 4dd7c4c, 91ad425; SUMMARY at 02-02-SUMMARY.md
  - [ ] 03-PLAN.md: backfill_content_markdown.py CLI (BACKFILL-02 + BACKFILL-04 — --dry-run / --limit / --document-id / --purge-orphans) — NEXT
  - [ ] 04-PLAN.md: test_backfill.py integration suite + register in test_all.py (BACKFILL-03 verifier + SC4 byte-equivalence)
Wave 1 (parallel): 01 + 02 BOTH DONE. Wave 2: 03 then 04 (04 has human-verify checkpoint for operator pre-reqs).
Operator pre-reqs before plan 04 checkpoint clears: (1) Create Supabase Storage bucket `documents` (private, 50MB limit) via Studio; (2) Apply Migration 018 via run_migrations.py; (3) Backend running on localhost:8001.
Carry-forward from Phase 1: still pending — commit 017.sql; align Episode-1 test_settings/test_hybrid/test_tools admin assumption.
Resume file: next is plan 03 of Phase 2 (backfill_content_markdown.py CLI).
