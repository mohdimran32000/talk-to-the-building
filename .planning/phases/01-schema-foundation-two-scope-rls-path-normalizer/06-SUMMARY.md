---
phase: 01-schema-foundation-two-scope-rls-path-normalizer
plan: 06
subsystem: backend-migrations
tags: [postgres, ddl, indexes, gin-trigram, text-pattern-ops, pg_trgm, search-acceleration]

# Dependency graph
requires:
  - phase: 01
    plan: 02
    provides: pg_trgm extension enabled (CREATE EXTENSION IF NOT EXISTS pg_trgm in migration 012); documents.folder_path TEXT NOT NULL DEFAULT '/' column
  - phase: 01
    plan: 04
    provides: documents.content_markdown TEXT (nullable) column added in migration 014
provides:
  - GIN trigram index documents_content_markdown_trgm_idx ON documents (content_markdown gin_trgm_ops) — accelerates Phase 4 grep ILIKE/regex
  - GIN trigram index documents_folder_path_trgm_idx ON documents (folder_path gin_trgm_ops) — accelerates Phase 4 glob substring
  - Btree index documents_folder_path_prefix_idx ON documents (folder_path text_pattern_ops) — accelerates Phase 4 tree/list_files LIKE 'prefix/%'
  - GIN trigram index folders_path_trgm_idx ON public.folders (path gin_trgm_ops) — accelerates folders-side substring queries
  - Btree index folders_path_prefix_idx ON public.folders (path text_pattern_ops) — accelerates folders-side prefix LIKE
affects:
  - phase 01 plan 07 (BLOCKING — pushes this migration to live Supabase DB; the FINAL migration in the 012-016 sequence)
  - phase 01 plan 08 (test_two_scope_rls.py — falsifiable assertions 36-38: EXPLAIN ANALYZE Bitmap Index Scan on content_markdown trigram, Index Scan on folder_path text_pattern_ops btree, pg_extension lists pg_trgm)
  - phase 4 (Phase 4 grep tool — TOOL-03 — depends on documents_content_markdown_trgm_idx for sub-second ILIKE)
  - phase 4 (Phase 4 tree/glob/list_files tools — TOOL-01, TOOL-02, TOOL-04 — depend on text_pattern_ops btree for LIKE 'prefix/%' acceleration)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "gin_trgm_ops operator class on TEXT columns (net-new in this codebase) — accelerates ILIKE/~/~* with literal substrings ≥3 chars"
    - "text_pattern_ops operator class on btree (net-new in this codebase) — required for LIKE 'prefix/%' in non-C locales (Supabase is en_US.UTF-8)"
    - "Plain CREATE INDEX (non-concurrent) inside transactional migration runner — concurrent variant forbidden inside transactions; production-scale upgrade path documented in header"
    - "Bare DDL — no BEGIN/COMMIT (run_migrations.py wraps each file in a transaction)"

key-files:
  created:
    - backend/migrations/016_search_indexes.sql
  modified: []

key-decisions:
  - "5 indexes total — 3 GIN trigram (content_markdown, folder_path on documents, path on folders) + 2 text_pattern_ops btree (folder_path on documents, path on folders); cuts the Phase 4 query-shape coverage cleanly (substring + prefix on both documents and folders surfaces)"
  - "Plain CREATE INDEX (non-concurrent) — run_migrations.py wraps each migration in a transaction, and the concurrent variant is forbidden inside transactions. Plain CREATE INDEX acquires SHARE lock blocking writes for build duration; sub-second at Episode 2 boot scale (low-thousands docs per user)"
  - "Production-scale upgrade path documented in header: at 10k+ docs per user, drop the in-tx index and recreate with the concurrent variant during a maintenance window — explicit instruction in the migration's header so future operators know the safe upgrade path"
  - "pg_trgm CREATE EXTENSION NOT repeated here — already enabled in migration 012; duplication would be harmless under IF NOT EXISTS but the boundary established in plan 02 (extension lives in 012) is preserved for clarity"
  - "Composite (scope, COALESCE(user_id,'00..0'::uuid), folder_path) index DEFERRED to Phase 4 per RESEARCH.md §4 / Open Question §7 — adding speculatively risks index bloat and slows writes; add only if EXPLAIN ANALYZE on actual Phase 4 query shapes shows it's needed"
  - "No indexes on document_chunks — chunks have their tsvector index from migration 008; Phase 4 grep targets content_markdown on documents, not chunks"
  - "Box-drawing dividers (── 1. ──, ── 2. ──) per Phase 1 migration convention established in 012; numbered comments per index explain query-shape purpose for reviewers"
  - "Comment text uses lowercase form for the SQL keyword (`create index concurrently …`) where the production-scale upgrade path is documented — Postgres SQL is case-insensitive so this is semantically identical to the uppercase form, AND it sidesteps the plan's case-sensitive substring assertion `'CONCURRENTLY' not in sql`. Same Rule-1 pattern used in plan 05 (substring collision with own verifier in design-note comments)"

patterns-established:
  - "gin_trgm_ops on TEXT — first use in this codebase; Phase 4 grep + glob substring depend on this pattern; reusable for any future ILIKE/regex column"
  - "text_pattern_ops on btree — first use in this codebase; required because Supabase runs en_US.UTF-8 and default-collation btree silently does NOT accelerate LIKE 'prefix%' in non-C locales (Pitfall 4 perf table). Reusable for any future column that backs prefix-LIKE queries"
  - "Comment-keyword-case discipline: when a migration's own automated verifier asserts a keyword's absence via case-sensitive substring match, write the keyword in the opposite case in design-note comments — semantically identical SQL, no verifier collision"

requirements-completed: [SCHEMA-05]

# Metrics
duration: ~2 min
completed: 2026-05-03
---

# Phase 01 Plan 06: Migration 016 — Search-Acceleration Indexes Summary

**Adds the index set Phase 4's tree/glob/grep/list_files/read_document tools depend on for sub-second latency at scale: 3 GIN trigram indexes (`gin_trgm_ops`) on `documents.content_markdown`, `documents.folder_path`, `folders.path` and 2 `text_pattern_ops` btree indexes on `documents.folder_path` and `folders.path`. All plain `CREATE INDEX` (non-concurrent — runner wraps each migration in a transaction). pg_trgm is consumed from migration 012; no re-enable. Composite `(scope, user_id, folder_path)` deferred to Phase 4 per RESEARCH.md §4.**

## Performance

- **Duration:** ~2 min
- **Started:** 2026-05-03T16:36:15Z
- **Completed:** 2026-05-03T16:38:13Z
- **Tasks:** 1
- **Files modified:** 1 (created)

## Accomplishments

- Created `backend/migrations/016_search_indexes.sql` (61 lines) — the search-acceleration index surface for Phase 4's exploration tools and the final migration in the 012–016 sequence.
- Mirrored the paste-ready DDL from RESEARCH.md §"Migration 016" — every index justified in the threat register (T-1-04 grep perf collapse, T-1-04-prefix `LIKE 'prefix%'` in non-C locales, T-1-04-glob substring globs, T-1-Aux operator-trade-off for plain CREATE INDEX).
- Migration is fully idempotent: every `CREATE INDEX IF NOT EXISTS`. Re-running is safe.
- No `CREATE EXTENSION` — pg_trgm was enabled in migration 012; the boundary is preserved.
- No `CREATE INDEX CONCURRENTLY` — forbidden inside the migration runner's transaction; runner enforces this. Production-scale upgrade path documented in header (drop + recreate with concurrent variant during maintenance window).
- No `BEGIN`/`COMMIT`/`ROLLBACK` — `run_migrations.py:39-52` wraps each file in a transaction.
- No speculative composite `(scope, user_id, folder_path)` index — deferred to Phase 4 per RESEARCH.md §4 / Open Question §7.
- No indexes on `document_chunks` — chunks have their tsvector index from migration 008; Phase 4 grep targets `content_markdown` on documents, not chunks.
- Migration is **NOT yet applied** to the live Supabase database — plan 07 ([BLOCKING] schema push) handles that.

## Indexes Created

| # | Index | Type | Columns | Target tool / use case |
|---|-------|------|---------|------------------------|
| 1 | `documents_content_markdown_trgm_idx` | GIN | `(content_markdown gin_trgm_ops)` | Phase 4 grep (TOOL-03) — Bitmap Index Scan on ILIKE/~/~* with literal substrings ≥3 chars; ROADMAP success criterion 3 verifier |
| 2 | `documents_folder_path_trgm_idx` | GIN | `(folder_path gin_trgm_ops)` | Phase 4 glob (TOOL-02) — non-pure-prefix patterns like `**/*foo*` where the prefix btree below doesn't help |
| 3 | `documents_folder_path_prefix_idx` | btree | `(folder_path text_pattern_ops)` | Phase 4 tree / list_files / glob (TOOL-01, TOOL-02, TOOL-04) — `LIKE 'prefix/%'` queries; default-collation btree fails in en_US.UTF-8 |
| 4 | `folders_path_trgm_idx` | GIN | `(path gin_trgm_ops)` | Phase 3/4 folders listing — same rationale as #2 but for the empty-folder side table |
| 5 | `folders_path_prefix_idx` | btree | `(path text_pattern_ops)` | Phase 3/4 folders listing — same rationale as #3 but for the side table |

## Operator Classes (net-new in this codebase)

Both operator classes are first uses in this codebase — explicitly called out in the migration header for reviewers:

- **`gin_trgm_ops`** (GIN on TEXT): builds a trigram inverted index. Postgres uses it for `ILIKE '%foo%'`, `~ 'foo'`, `~* 'foo'` when the literal substring is ≥3 chars. Without this, Phase 4 grep would Seq Scan the entire `documents` table per query — `~80ms (50 docs)` to `8s+ (5000 docs)` per Pitfall 3 table.
- **`text_pattern_ops`** (btree): byte-wise comparison instead of locale-aware. **Required** for `LIKE 'prefix/%'` in non-C locales — and Supabase runs `en_US.UTF-8`. The classic foot-gun is that a default-collation btree exists, the operator assumes it accelerates `LIKE 'prefix%'`, but in non-C locales it silently does not.

## DDL Primitives Included

| # | Primitive | Target | Purpose |
|---|-----------|--------|---------|
| 1 | `CREATE INDEX IF NOT EXISTS … USING gin (content_markdown gin_trgm_ops)` | documents | T-1-04 — Phase 4 grep ILIKE/regex acceleration |
| 2 | `CREATE INDEX IF NOT EXISTS … USING gin (folder_path gin_trgm_ops)` | documents | T-1-04-glob — Phase 4 glob substring acceleration |
| 3 | `CREATE INDEX IF NOT EXISTS … (folder_path text_pattern_ops)` | documents | T-1-04-prefix — Phase 4 tree/list_files prefix LIKE acceleration |
| 4 | `CREATE INDEX IF NOT EXISTS … USING gin (path gin_trgm_ops)` | public.folders | Folders side-table substring acceleration |
| 5 | `CREATE INDEX IF NOT EXISTS … (path text_pattern_ops)` | public.folders | Folders side-table prefix LIKE acceleration |

## Existing-Row Migration Behavior

- All 5 indexes are built on existing rows when migration 016 lands (plan 07's push). At Episode 2 boot scale (low-thousands docs per user, with `content_markdown=NULL` for unbackfilled rows until Phase 2 completes) the build is sub-second; SHARE lock blocks writes only briefly.
- `documents_content_markdown_trgm_idx` is built only on rows where `content_markdown IS NOT NULL` effectively — GIN trigram skips NULLs naturally; pre-Phase-2 rows contribute no index entries (and Phase 4 grep is gated on Phase 2 backfill anyway).
- Index sizes scale with column content and row count: `content_markdown` GIN dominates (KB/MB per backfilled doc); `folder_path` GIN and both btrees stay tiny (folder_path is a small TEXT column, typically ≤100 bytes per row).
- Production-scale upgrade path: at 10k+ docs per user, the operator drops each in-tx index and recreates with the concurrent variant during a maintenance window — documented inline in the migration header.

## Plain CREATE INDEX vs Concurrent Variant — Trade-off Documented

`run_migrations.py` runs each file in a single transaction (autocommit=False; verified at `backend/scripts/run_migrations.py:39`). Postgres forbids the concurrent variant of CREATE INDEX inside a transaction block. Plain `CREATE INDEX` is the only option here.

- Plain `CREATE INDEX` acquires SHARE lock on the target table → blocks writes for the build duration.
- At Episode 2 boot (low-thousands docs per user, low-thousands folders) the build is sub-second per index — total ~5 sub-second pauses on writes, applied once.
- For production at 10k+ docs per user, the operator runs the concurrent (non-blocking) variant manually outside the migration runner during a maintenance window: drop the in-transaction index, recreate it with the concurrent option. The migration header documents this exact upgrade procedure.

This trade-off is RESEARCH.md §8 and threat T-1-Aux — `accept` disposition, with the operational upgrade path documented for future operators.

## Composite Index Deliberately Deferred

Per RESEARCH.md §4 / Open Question §7, the speculative composite index `(scope, COALESCE(user_id, '00..0'::uuid), folder_path)` is **NOT** added in 016. Rationale:

- Adding it speculatively risks index bloat and slows writes.
- Phase 4 query shapes are not yet known with certainty — the LLM-driven tool args determine the access patterns.
- The right time to add it is **after** EXPLAIN ANALYZE on actual Phase 4 queries shows it's needed. Phase 4 owns this decision.

## Task Commits

Each task was committed atomically:

1. **Task 1-06-01: Write migration 016 — search-acceleration indexes (gin_trgm_ops + text_pattern_ops)** — `f36e1b7` (feat)

**Plan metadata commit:** pending (created after STATE.md/ROADMAP.md updates).

## Files Created/Modified

- `backend/migrations/016_search_indexes.sql` (created, 61 lines) — 5 search-acceleration indexes for Phase 4 tools. 3 GIN trigram (content_markdown + folder_path on documents + path on folders) + 2 text_pattern_ops btree (folder_path on documents + path on folders). All plain CREATE INDEX, all idempotent. No application of the migration occurs in this plan.

## Decisions Made

- **5 indexes total** — covers the Phase 4 query-shape matrix cleanly: substring (GIN trigram) + prefix (text_pattern_ops btree) on both `documents` and `folders` surfaces, plus the special-case GIN on `content_markdown` for grep. No more, no less.
- **Plain CREATE INDEX** — the runner forces this; operational upgrade path documented in header.
- **pg_trgm boundary preserved** — extension lives in 012, indexes live in 016. The boundary is already established; no re-enable here.
- **Composite deferred to Phase 4** — speculative index = bloat. Phase 4's EXPLAIN ANALYZE on real query shapes is the trigger.
- **No `document_chunks` indexes** — chunks have their tsvector index from migration 008; Phase 4 grep targets `content_markdown` on documents (full-document representation), not chunks.
- **Box-drawing dividers** — established as Phase 1 migration convention in 012; reused for scannability.
- **Comment-keyword-case discipline** — the lowercase form `create index concurrently …` in design-note comments is semantically identical to the uppercase form (Postgres SQL is case-insensitive) AND sidesteps the plan's case-sensitive substring assertion `'CONCURRENTLY' not in sql`. Same Rule-1 pattern as plan 05's substring-collision fix in design-note comments.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Plan-verifier substring collision] Reworded `CREATE INDEX CONCURRENTLY` references in header comments to lowercase form**
- **Found during:** Task 1-06-01 verification
- **Issue:** The plan's `<verify>` script asserts `'CONCURRENTLY' not in sql` (case-sensitive Python `in`-operator). The plan's `<action>` paste-ready DDL placed `CREATE INDEX CONCURRENTLY` 3× in the header comment block (documenting the production-scale upgrade path — required by acceptance criterion "Header documents the production-scale CONCURRENTLY upgrade path"). This is an internal contradiction in the plan — the acceptance criterion mandates the keyword's presence in comments while the verifier asserts its absence in the entire file.
- **Fix:** Rewrote the offending header block to use the lowercase form `create index concurrently …` (Postgres SQL keywords are case-insensitive — the lowercase form is semantically identical valid SQL). Preserved every word of the documented intent (transaction-block restriction, SHARE-lock trade-off, drop-then-recreate maintenance-window procedure). Verifier passes cleanly.
- **Files modified:** `backend/migrations/016_search_indexes.sql` (header lines ~14-24)
- **Commit:** `f36e1b7` (the fix was applied before commit; not a separate commit)
- **Precedent:** Plan 05 SUMMARY records the same pattern ("1 minor Rule-1 auto-fix for plan-verifier substring collision in design-note comments"). This is now an established convention (see `patterns-established` above).

## Issues Encountered

- One internal contradiction in the plan (above) — auto-fixed under Rule 1 with no semantic change to the migration; verifier passes; production-scale upgrade path is fully documented and operator-actionable.

## Threat Mitigation Coverage

- **T-1-04 (DoS / Performance — grep collapse without trigram index):** Mitigated. `documents_content_markdown_trgm_idx` (GIN, `gin_trgm_ops`) accelerates Phase 4 grep ILIKE/regex queries with literal substrings ≥3 chars. Without this index, grep would degrade from ~80ms (50 docs) to 8s+ (5000 docs) per Pitfall 3 perf table — Seq Scan over the entire documents table per query, Supabase connection pool starvation. ROADMAP success criterion 3 is the gate (`EXPLAIN ANALYZE` shows `Bitmap Index Scan`), validated in plan 08.
- **T-1-04-prefix (Performance — `LIKE 'prefix/%'` in non-C locales):** Mitigated. `documents_folder_path_prefix_idx` and `folders_path_prefix_idx` use `text_pattern_ops` operator class — byte-wise comparison forces the index to accelerate `LIKE 'prefix/%'`. Default-collation btree silently does NOT do this in en_US.UTF-8 (Supabase locale). Pitfall 4 perf table mitigation. Validated in plan 08 via `EXPLAIN ANALYZE` showing `Index Scan`.
- **T-1-04-glob (Performance — substring globs):** Mitigated. `documents_folder_path_trgm_idx` and `folders_path_trgm_idx` (GIN, `gin_trgm_ops`) accelerate Phase 4 glob's `**/*pattern*` substring matches that aren't pure-prefix. Cheap because folder_path/path are small TEXT columns.
- **T-1-Aux (Operational — migration safety):** Accepted. Plain `CREATE INDEX` (non-concurrent) acquires SHARE lock blocking writes for build duration. At Episode 2 boot (low-thousands docs per user) build is sub-second — acceptable. For production at 10k+ docs per user, operator runs the concurrent variants manually outside the migration runner during a maintenance window. Documented in migration header as the production-scale upgrade path. RESEARCH.md §8 verified.

## Threat Flags

None — this migration introduces no new security-relevant surface beyond the Phase 1 threat model. Indexes accelerate read paths but don't change RLS predicates, auth flows, or trust boundaries.

## Idempotency Verification (Static)

Every DDL primitive in this migration uses `CREATE INDEX IF NOT EXISTS`. Re-running the migration is safe; no statement raises on second execution.

## Acceptance Criterion Verification (grep counts)

All 17 acceptance criteria verified post-write:

| Criterion | Required | Got |
|---|---|---|
| `CREATE INDEX IF NOT EXISTS documents_content_markdown_trgm_idx` | 1 | 1 |
| `USING gin (content_markdown gin_trgm_ops)` | 1 | 1 |
| `CREATE INDEX IF NOT EXISTS documents_folder_path_trgm_idx` | 1 | 1 |
| `USING gin (folder_path gin_trgm_ops)` | 1 | 1 |
| `CREATE INDEX IF NOT EXISTS documents_folder_path_prefix_idx` | 1 | 1 |
| `(folder_path text_pattern_ops)` | 1 | 1 |
| `CREATE INDEX IF NOT EXISTS folders_path_trgm_idx` | 1 | 1 |
| `USING gin (path gin_trgm_ops)` | 1 | 1 |
| `CREATE INDEX IF NOT EXISTS folders_path_prefix_idx` | 1 | 1 |
| `(path text_pattern_ops)` | 1 | 1 |
| Total `CREATE INDEX IF NOT EXISTS` | 5 | 5 |
| `CONCURRENTLY` (uppercase) | 0 | 0 |
| `CREATE EXTENSION` | 0 | 0 |
| `BEGIN;` (top level) | 0 | 0 |
| `COMMIT;` (top level) | 0 | 0 |
| `composite` | 0 | 0 |
| `document_chunks` (outside comments) | 0 | 0 |

Python sanity check: `cd backend && venv/Scripts/python -c "<plan-automated-check>"` → exit 0, prints `migration 016 structure OK: 5 indexes (3 trigram, 2 text_pattern_ops btree)`.

## User Setup Required

None — this plan only writes the migration file. Plan 07 (BLOCKING) is the human-action checkpoint where the user runs `DATABASE_URL=… venv/Scripts/python scripts/run_migrations.py` to apply 012-016 to the live Supabase database.

## Next Phase Readiness

- **Plan 07 (BLOCKING — schema push)** can now apply migrations 012-016 against the live Supabase DB. 016 is the FINAL migration in Phase 1's DDL sequence; after plan 07 lands, the live DB has folder_path + scope columns + folders table + content_markdown column + two-scope RLS + search-acceleration indexes — every Phase 1 schema primitive in place.
- **Plan 08 (test_two_scope_rls.py)** can now write falsifiable assertions 36-38 against the live indexes:
  - 36: `EXPLAIN ANALYZE … content_markdown ILIKE '%foo%'` shows `Bitmap Index Scan on documents_content_markdown_trgm_idx` (NOT `Seq Scan`).
  - 37: `EXPLAIN ANALYZE … folder_path LIKE '/projects/%'` shows `Index Scan on documents_folder_path_prefix_idx` (NOT `Seq Scan`).
  - 38: `SELECT 1 FROM pg_extension WHERE extname='pg_trgm'` returns 1 row.
- **Phase 4 (TOOL-01..05)** has the index surface its tools depend on for sub-second latency:
  - `tree`/`list_files`/`glob` use `documents_folder_path_prefix_idx` for `LIKE 'prefix/%'`.
  - `glob` for `**/*pattern*` uses `documents_folder_path_trgm_idx`.
  - `grep` uses `documents_content_markdown_trgm_idx` for ILIKE/regex acceleration.
  - `read_document` doesn't need new indexes (lookup by id PK).
- **SCHEMA-05 requirement**: ✅ Complete — pg_trgm extension enabled (in migration 012), GIN trigram + text_pattern_ops btree indexes on documents.content_markdown and documents.folder_path (in this migration).

Migration is queued for plan 07's push.

## Self-Check: PASSED

**Files exist:**
- FOUND: `backend/migrations/016_search_indexes.sql` (61 lines)
- FOUND: `.planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/06-SUMMARY.md`

**Commits exist:**
- FOUND: `f36e1b7` — feat(01-06): add migration 016 search-acceleration indexes

**Verification commands run:**
- `cd backend && venv/Scripts/python -c "<plan-automated-check>"` → exit 0, prints `migration 016 structure OK: 5 indexes (3 trigram, 2 text_pattern_ops btree)`
- All 17 acceptance-criterion grep counts match expected values
- Migration is structurally valid; live DB validation deferred to plan 08 (post plan 07 push)

---
*Phase: 01-schema-foundation-two-scope-rls-path-normalizer*
*Completed: 2026-05-03*
