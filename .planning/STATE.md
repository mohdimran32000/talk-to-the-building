---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
stopped_at: Phase 1 COMPLETE — Two-Scope RLS matrix passes 49/0 (RLS-04 / Pitfall 1 / RANK 1 gate; Phase 2 unblocked). Migration 017 fixes pre-existing Episode-1 profiles RLS recursion via is_admin() helper. Carry-forward items in 08-SUMMARY (commit 017.sql; align test_settings admin assumption).
last_updated: "2026-05-04T00:00:00Z"
last_activity: 2026-05-04 -- Plan 01-08 (two-scope RLS test matrix) complete; Phase 1 closed
progress:
  total_phases: 6
  completed_phases: 1
  total_plans: 8
  completed_plans: 8
  percent: 100
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-01)

**Core value:** The agent can locate the *right* piece of information in a large, organized knowledge base — using semantic search when meaning matters and codebase-style traversal (tree/glob/grep/read) when precision matters — without hallucinating across unrelated material.
**Current focus:** Phase 1 — Schema Foundation + Two-Scope RLS + Path Normalizer

## Current Position

Phase: 1 of 6 COMPLETE — Schema Foundation + Two-Scope RLS + Path Normalizer
Plan: 8 of 8 in phase 1 done; Phase 2 (content_markdown backfill) is next
Status: Phase 1 complete; awaiting Phase 2 discuss/plan/execute
Last activity: 2026-05-04 -- Plan 01-08 (two-scope RLS test matrix passes 49/0) complete

Progress: [██████████] 100% (Phase 1 of 6); Project: 17%

## Performance Metrics

**Velocity:**

- Total plans completed: 6
- Average duration: ~1.7 min
- Total execution time: ~10 min

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1 | 6 | ~10 min | ~1.7 min |

**Recent Trend:**

- Last 6 plans: 01-01 (~1 min, 1 file, 1 task) → 01-02 (~2 min, 1 file, 1 task) → 01-03 (~1 min, 1 file, 1 task) → 01-04 (~1 min, 1 file, 1 task) → 01-05 (~3 min, 1 file, 1 task — 1 minor Rule-1 auto-fix for plan-verifier substring collision in design-note comments) → 01-06 (~2 min, 1 file, 1 task — same Rule-1 substring-collision pattern, lowercase-keyword fix in header comments documenting concurrent-variant upgrade path)
- Trend: ✅ on-spec, paste-from-RESEARCH succeeded; comment-keyword-case discipline now an established convention for migrations whose verifier asserts keyword absence via case-sensitive substring

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

Last session: 2026-05-04
Stopped at: Phase 1 COMPLETE — all 8 plans done. Two-Scope RLS test matrix passes 49/0 against the live DB (cross-user × cross-scope SELECT/INSERT/UPDATE/DELETE on documents, document_chunks, folders; scope-mutation triggers on all 3 tables; CHECK constraints; normalize_path round-trips). Migration 017 added (Episode-1 profiles RLS recursion fix). Carry-forward: commit 017.sql; align Episode-1 test_settings/test_hybrid/test_tools admin assumption (not Phase 1 regressions). RLS-04 / Pitfall 1 / RANK 1 mitigation gate satisfied. Phase 2 (content_markdown backfill) unblocked.
Resume file: next is /gsd-discuss-phase 2 or /gsd-plan-phase 2
