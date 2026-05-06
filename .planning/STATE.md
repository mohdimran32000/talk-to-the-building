---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
stopped_at: Phase 2 / Plan 01 EXECUTED — Storage Gap closed. Storage upload wired into both branches of files.py upload_file() before the Docling background task; Migration 018 ships idempotent SELECT + INSERT RLS policies on storage.objects scoped to bucket_id='documents' AND auth.uid() folder. Computed-from-id storage path locked. Operator pre-reqs (create 'documents' bucket via Studio + apply Migration 018) documented in 02-01-SUMMARY.md.
last_updated: "2026-05-06T06:51:12Z"
last_activity: 2026-05-06 -- Phase 2 / Plan 01 executed; 2 atomic commits (41e3eeb feat + e256c91 feat); next plan in Phase 2 is 02 (synchronous content_markdown write inside ingest_document)
progress:
  total_phases: 6
  completed_phases: 1
  total_plans: 12
  completed_plans: 9
  percent: 75
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-01)

**Core value:** The agent can locate the *right* piece of information in a large, organized knowledge base — using semantic search when meaning matters and codebase-style traversal (tree/glob/grep/read) when precision matters — without hallucinating across unrelated material.
**Current focus:** Phase 2 — content_markdown Backfill (Gated)

## Current Position

Phase: 2 of 6 EXECUTING — content_markdown Backfill (Gated)
Plan: 1 of 4 in phase 2 done; next is 02-PLAN.md (synchronous content_markdown write inside ingest_document)
Status: Phase 2 / Plan 01 (Storage Gap closure) complete; Migration 018 written but NOT YET applied (operator runs run_migrations.py); 'documents' bucket pending one-time creation via Supabase Studio
Last activity: 2026-05-06 -- Phase 2 / Plan 01 executed; 2 atomic commits + SUMMARY

Progress: [██░░░░░░░░] 25% (1/4 plans in Phase 2); Project: 17% (1/6 phases complete; Phase 2 in flight)

## Performance Metrics

**Velocity:**

- Total plans completed: 9 (Phase 1: 8, Phase 2: 1)
- Average duration: ~2.0 min
- Total execution time: ~17 min

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1 | 8 | ~12 min | ~1.5 min |
| 2 | 1 (in flight) | ~5 min | ~5 min |

**Recent Trend:**

- Last 7 plans: 01-02 (~2 min, 1 file, 1 task) → 01-03 (~1 min, 1 file, 1 task) → 01-04 (~1 min, 1 file, 1 task) → 01-05 (~3 min, 1 file, 1 task — 1 Rule-1 auto-fix) → 01-06 (~2 min, 1 file, 1 task — same Rule-1 pattern) → 01-07 (apply migrations) → 01-08 (RLS matrix tests passed 49/0) → **02-01 (~5 min, 2 files, 2 tasks — Storage Gap closure: files.py upload + Migration 018 RLS; zero deviations, paste-from-PATTERNS succeeded on first iteration)**
- Trend: ✅ on-spec, paste-from-PATTERNS succeeded for Phase 2's first plan; the computed-from-id Storage path contract and storage.objects RLS naming convention are now established for downstream plans 03/04 to inherit

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
Stopped at: Phase 2 / Plan 01 EXECUTED — Storage Gap closed.
  - ✅ 01-PLAN.md: Storage upload at upload-time + Migration 018 storage.objects RLS (commits 41e3eeb, e256c91; SUMMARY at 02-01-SUMMARY.md)
  - [ ] 02-PLAN.md: Synchronous content_markdown write inside ingest_document() + docling==2.91.0 pin (BACKFILL-01) — NEXT
  - [ ] 03-PLAN.md: backfill_content_markdown.py CLI (BACKFILL-02 + BACKFILL-04 — --dry-run / --limit / --document-id / --purge-orphans)
  - [ ] 04-PLAN.md: test_backfill.py integration suite + register in test_all.py (BACKFILL-03 verifier + SC4 byte-equivalence)
Wave 1 (parallel): 01 (DONE) + 02. Wave 2: 03 then 04 (04 has human-verify checkpoint for operator pre-reqs).
Operator pre-reqs before plan 04 checkpoint clears: (1) Create Supabase Storage bucket `documents` (private, 50MB limit) via Studio; (2) Apply Migration 018 via run_migrations.py; (3) Backend running on localhost:8001.
Carry-forward from Phase 1: still pending — commit 017.sql; align Episode-1 test_settings/test_hybrid/test_tools admin assumption.
Resume file: next is plan 02 of Phase 2 (synchronous content_markdown write).
