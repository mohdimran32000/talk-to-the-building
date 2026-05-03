---
phase: 01-schema-foundation-two-scope-rls-path-normalizer
plan: 04
subsystem: backend-migrations
tags: [postgres, ddl, schema-evolution, content-markdown, status-enum, partial-index, check-constraints]

# Dependency graph
requires:
  - phase: 01
    plan: 02
    provides: documents table mutated cleanly with folder_path + scope columns; idempotent ALTER ADD COLUMN convention established
provides:
  - documents.content_markdown TEXT NULL column (Phase 2 backfill target)
  - documents.content_markdown_status TEXT NOT NULL DEFAULT 'pending' column
  - CHECK content_markdown_status IN ('pending','ready','failed','requires_user_reupload') — canonical 4-element vocabulary per REQUIREMENTS.md SCHEMA-03
  - Partial index documents_content_markdown_status_idx ON (content_markdown_status) WHERE content_markdown_status <> 'ready' — Phase 2 backfill scan efficiency
affects:
  - phase 01 plan 06 (migration 016 — references content_markdown column for GIN trigram index using gin_trgm_ops)
  - phase 01 plan 07 (BLOCKING — pushes this migration to live Supabase DB)
  - phase 01 plan 08 (test_two_scope_rls.py — falsifiable assertion 34 validates 'processing' rejection by CHECK)
  - phase 02 (backfill_content_markdown.py — flips 'pending' → 'ready' / 'failed' / 'requires_user_reupload' after Docling re-run)
  - phase 04 (grep + read_document tools — surface non-'ready' status explicitly per Pitfall 6)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Multi-line ALTER TABLE with comma-separated ADD COLUMN IF NOT EXISTS clauses"
    - "TEXT + CHECK enum (preferred over Postgres ENUM type — easier to evolve via DROP/ADD CONSTRAINT than ALTER TYPE ADD VALUE)"
    - "Partial index for selective scans (new convention for this codebase) — keeps index small in steady state by filtering out the dominant value"
    - "Multi-line CHECK IN list (one value per line) — readability when call-out comment is justified"
    - "Bare DDL — no BEGIN/COMMIT (run_migrations.py wraps each file in a transaction)"

key-files:
  created:
    - backend/migrations/014_content_markdown_column.sql
  modified: []

key-decisions:
  - "TEXT + CHECK constraint over Postgres ENUM type per RESEARCH.md §2: ALTER TYPE ADD VALUE is irreversible and locks the migration into Postgres-version-specific gotchas; TEXT + CHECK evolves cleanly via DROP CONSTRAINT … ADD CONSTRAINT … with a new value list"
  - "Default 'pending' on content_markdown_status — existing Episode 1 documents migrate without manual data movement; PG11+ stored DEFAULT applies as metadata-only, no row rewrite"
  - "content_markdown nullable (no NOT NULL) — backfill is Phase 2's deferred work; making the column NOT NULL would block migration application on existing rows"
  - "Canonical 4-element vocabulary: 'pending', 'ready', 'failed', 'requires_user_reupload' — REQUIREMENTS.md SCHEMA-03 is canonical (NOT 'ok' from ROADMAP additional context, NOT 'processing' from documents.status)"
  - "Partial index WHERE content_markdown_status <> 'ready' — Phase 2 backfill scan is O(rows-needing-backfill) not O(all-documents); index stays small in steady state since most rows become 'ready'"
  - "Multi-line CHECK IN list (one value per line) instead of single-line — reviewer-friendly for the new partial-index pattern + new convention call-outs"
  - "GIN trigram index on content_markdown deliberately deferred to migration 016 — keeps 014 focused on column + status enum; 016 owns search-acceleration"
  - "No backfill data movement in Phase 1 — Phase 2's backfill_content_markdown.py is the dedicated mechanism (re-runs Docling against original Storage blobs)"

patterns-established:
  - "TEXT + CHECK enum convention for scoped vocabularies: extends documents.status (003) and documents.scope (012) to a third axis (content_markdown_status); reusable for any future scoped-vocabulary column"
  - "Partial index pattern for selective scans: new convention, applicable to any future column where one value dominates and the minority needs efficient lookup (e.g., a future is_archived flag)"
  - "Phase 2 backfill scan contract: WHERE <status_col> <> '<terminal_value>' partial index makes backfill scripts O(remaining-work) instead of O(all-rows)"

requirements-completed: [SCHEMA-03]

# Metrics
duration: ~1 min
completed: 2026-05-03
---

# Phase 01 Plan 04: Migration 014 — content_markdown Column + Status Enum Summary

**Adds the `documents.content_markdown TEXT` (nullable) column and `documents.content_markdown_status TEXT NOT NULL DEFAULT 'pending'` enum with CHECK constraint enforcing the canonical 4-element status vocabulary (`'pending'`, `'ready'`, `'failed'`, `'requires_user_reupload'`) per REQUIREMENTS.md SCHEMA-03, plus a partial index scoped to non-'ready' rows for Phase 2 backfill scan efficiency.**

## Performance

- **Duration:** ~1 min
- **Started:** 2026-05-03T16:21:23Z
- **Completed:** 2026-05-03T16:22:22Z
- **Tasks:** 1
- **Files modified:** 1 (created)

## Accomplishments

- Created `backend/migrations/014_content_markdown_column.sql` (29 lines) — the content_markdown surface for Phase 2 backfill and Phase 4 grep/read_document tools.
- Mirrored the paste-ready DDL from RESEARCH.md §"Migration 014" verbatim — every primitive justified in the threat register (T-1-Aux Tampering CHECK lock, T-1-Aux-2 accepted markdown=NULL gap until Phase 2).
- Migration is fully idempotent: `IF NOT EXISTS` on ALTER ADD COLUMN; `CREATE INDEX IF NOT EXISTS` on the partial index. Re-running is safe.
- Existing Episode 1 documents migrate cleanly: `content_markdown_status='pending'` via DEFAULT (metadata-only PG11+ change, no row rewrite); `content_markdown=NULL` (backfill is Phase 2's job).
- Partial index `documents_content_markdown_status_idx` makes Phase 2's "find every document still needing markdown work" scan efficient (covers only non-'ready' rows; index stays small in steady state).
- No data backfill (Phase 2's job). No GIN trigram index on `content_markdown` (migration 016's job — deliberate scope boundary). No `document_chunks` mutations (markdown is documents-only).
- No `BEGIN`/`COMMIT`/`ROLLBACK` — `run_migrations.py:39-52` wraps each file in a transaction.
- No `CREATE INDEX CONCURRENTLY` — forbidden inside transactions; runner enforces this.
- Migration is **NOT yet applied** to the live Supabase database — plan 07 ([BLOCKING] schema push) handles that.

## Canonical Status Vocabulary (Phase 2 reference)

The CHECK constraint locks `content_markdown_status` to exactly these 4 values per REQUIREMENTS.md SCHEMA-03:

| Value | Meaning | Set by | Phase consuming |
|-------|---------|--------|-----------------|
| `'pending'` | New column default; existing Episode 1 docs start here; not yet processed | Migration 014 DEFAULT | Phase 2 backfill (initial state to flip away from) |
| `'ready'` | content_markdown populated and searchable | Phase 2 backfill_content_markdown.py (success path) | Phase 4 grep + read_document tools |
| `'failed'` | Docling raised on re-run; markdown stays NULL | Phase 2 backfill_content_markdown.py (Docling exception path) | Phase 4 tools (surface explicitly per Pitfall 6 — never silently skip) |
| `'requires_user_reupload'` | Source Storage blob is GC'd; cannot regenerate without re-upload | Phase 2 backfill_content_markdown.py (blob-missing path) | Phase 4 tools (surface explicitly with reupload guidance to the user) |

**NOT in the vocabulary** (deliberately rejected):
- `'ok'` — ROADMAP additional context says 'ok'; per RESEARCH.md §2 this is wrong, REQUIREMENTS.md is canonical.
- `'processing'` — that exists for `documents.status`, a different column.

The CHECK constraint will reject any INSERT/UPDATE attempting to set these wrong values; falsifiable assertion 34 in plan 08 validates this.

## DDL Primitives Included

| # | Primitive | Target | Purpose |
|---|-----------|--------|---------|
| 1 | `ADD COLUMN IF NOT EXISTS content_markdown TEXT` | documents | Phase 2 backfill target; nullable so existing rows don't block migration |
| 2 | `ADD COLUMN IF NOT EXISTS content_markdown_status TEXT NOT NULL DEFAULT 'pending'` | documents | Status state machine; default applies to existing rows as metadata-only on PG11+ |
| 2 | `CHECK (content_markdown_status IN ('pending','ready','failed','requires_user_reupload'))` | documents | Locks vocabulary to canonical 4-element set per REQUIREMENTS.md SCHEMA-03 (T-1-Aux mitigation) |
| 3 | `CREATE INDEX IF NOT EXISTS documents_content_markdown_status_idx ON documents (content_markdown_status) WHERE content_markdown_status <> 'ready'` | documents | Phase 2 backfill scan efficiency — partial index covers only non-terminal rows |

## Existing-Row Migration Behavior

PG11+ stored DEFAULT applies as metadata-only — **no row rewrite, no data movement** for existing Episode 1 rows:

- `documents.content_markdown` defaults to NULL for all existing rows (nullable column, no DEFAULT).
- `documents.content_markdown_status` defaults to `'pending'` for all existing rows (DEFAULT 'pending' applied as metadata-only on PG11+).
- The new CHECK constraint is satisfied: 'pending' ∈ canonical vocabulary.
- The partial index includes existing rows (status='pending' ≠ 'ready') — Phase 2 backfill scan starts O(all-existing-Episode-1-docs); after backfill flips them to 'ready', the index empties out and stays small.

Existing Episode 1 documents will be queryable at `content_markdown_status='pending'`, `content_markdown=NULL` immediately after migration 014 lands (plan 07's push). Phase 2 owns the work to make `content_markdown_status='ready'` and populate `content_markdown`.

## Task Commits

Each task was committed atomically:

1. **Task 1-04-01: Write migration 014 — content_markdown column + status enum + partial index** — `d744518` (feat)

**Plan metadata commit:** pending (created after STATE.md/ROADMAP.md updates).

## Files Created/Modified

- `backend/migrations/014_content_markdown_column.sql` (created, 29 lines) — adds content_markdown TEXT (nullable) + content_markdown_status TEXT NOT NULL DEFAULT 'pending' with canonical CHECK + partial index for Phase 2 backfill scan. No application of the migration occurs in this plan.

## Decisions Made

- **TEXT + CHECK over Postgres ENUM** (RESEARCH.md §2): `ALTER TYPE ADD VALUE` in Postgres ENUMs is painful and Postgres-version-sensitive; TEXT + CHECK is identically safe and evolves cleanly via `DROP CONSTRAINT … ADD CONSTRAINT …` with a new value list. Mirrors the existing `documents.status` and `documents.scope` shape.
- **Default `'pending'`** on existing rows: `ALTER TABLE … ADD COLUMN … NOT NULL DEFAULT '…'` is metadata-only on PG11+, so existing Episode 1 documents migrate without row rewrite. Phase 2 owns the transition from 'pending' to terminal states.
- **`content_markdown` nullable** (no NOT NULL, no DEFAULT): keeping it NULL is intentional. Making it NOT NULL would block migration application on existing rows; making it `DEFAULT ''` would force a meaningless value that violates the "Phase 2 owns backfill" boundary. Phase 4 tools must surface NULL explicitly (Pitfall 6).
- **Canonical 4-element vocabulary** (`'pending'`, `'ready'`, `'failed'`, `'requires_user_reupload'`): REQUIREMENTS.md SCHEMA-03 is canonical. Deliberately rejected `'ok'` (ROADMAP additional context error) and `'processing'` (belongs to `documents.status`).
- **Multi-line CHECK IN list**: one value per line. The single-line form (003 lines 18-19) is fine for the existing 4-value `status` vocabulary, but 014's call-out comments justify the multi-line form so reviewers can scan the canonical vocabulary at a glance.
- **Partial index `WHERE content_markdown_status <> 'ready'`**: makes Phase 2 backfill scan O(rows-needing-backfill) instead of O(all-documents). New convention for this codebase (no prior migration uses a partial index) — called out in the migration's header comment so reviewers know the intent.
- **No GIN trigram index on `content_markdown` here**: deliberately deferred to migration 016 (plan 06). Keeps 014 focused on column-add + status enum; 016 owns search-acceleration indexes (`documents_content_markdown_trgm_idx` using `gin_trgm_ops`).
- **No backfill data movement in Phase 1**: Phase 2's `backfill_content_markdown.py` is the dedicated mechanism (re-runs Docling against original Storage blobs). Phase 1 only ensures the column + status enum exist with the correct vocabulary.
- **No `document_chunks` changes**: `content_markdown` is a documents-only column. Chunks already store the chunked content; markdown is a separate full-document representation for grep/read_document tools.

## Deviations from Plan

None — plan executed exactly as written. The reference DDL skeleton in `<action>` (sourced from RESEARCH.md §"Migration 014") was paste-applied verbatim and passed every acceptance-criterion grep on first run.

## Issues Encountered

None.

## Threat Mitigation Coverage

- **T-1-Aux (Tampering / Data Integrity — content_markdown_status vocabulary):** Mitigated. TEXT + CHECK constraint locks the status vocabulary to the canonical 4-element set per REQUIREMENTS.md SCHEMA-03. Phase 2 backfill cannot accidentally introduce `'ok'`, `'processing'`, or any other value — the CHECK rejects them at the DB level. Falsifiable assertion 34 in plan 08 validates rejection of `'processing'` after live DB push (plan 07).
- **T-1-Aux-2 (Information Disclosure / Silent Failure — content_markdown=NULL on existing Episode 1 docs):** Accepted with mitigation deferred. Existing rows get `content_markdown=NULL` after this migration — INTENTIONAL because Phase 2's backfill_content_markdown.py is the dedicated re-run mechanism. Phase 4's `grep` and `read_document` tools are GATED on Phase 2 backfill completion (per ROADMAP critical-path: Phase 2 blocks Phase 4). The partial index added here makes Phase 2's backfill scan efficient. Pitfall 6 mitigation (surface non-'ready' status to LLM/user) is Phase 4's responsibility, not Phase 1's; Phase 1 only ensures the column + status enum exist with the correct vocabulary.

## Idempotency Verification (Static)

Every DDL primitive in this migration uses one of:

- `ALTER TABLE … ADD COLUMN IF NOT EXISTS …` (×2 — content_markdown, content_markdown_status)
- `CREATE INDEX IF NOT EXISTS …` (partial index)

Re-running the migration is safe; no statement raises on second execution. The CHECK constraint inline on `ADD COLUMN IF NOT EXISTS content_markdown_status` is only added when the column itself is added, so re-runs skip the CHECK definition (no need for the drop-then-add pattern that 012 used for table-level CHECK constraints).

## Acceptance Criterion Verification (grep counts)

All 14 acceptance criteria verified post-write:

| Criterion | Required | Got |
|---|---|---|
| `ADD COLUMN IF NOT EXISTS content_markdown` | ≥ 2 | 2 |
| `TEXT NOT NULL DEFAULT 'pending'` | 1 | 1 |
| `'pending'` | ≥ 2 | 3 |
| `'ready'` | ≥ 1 | 6 (incl. `'requires_user_reupload'` substring + WHERE clause) |
| `'failed'` | ≥ 1 | 2 |
| `'requires_user_reupload'` | ≥ 1 | 2 |
| `'ok'` | 0 | 0 |
| `'processing'` | 0 | 0 |
| `CREATE INDEX IF NOT EXISTS documents_content_markdown_status_idx` | 1 | 1 |
| `WHERE content_markdown_status <> 'ready'` | 1 | 1 |
| `CREATE TYPE …ENUM` (case-insensitive) | 0 | 0 |
| `CONCURRENTLY` | 0 | 0 |
| `document_chunks` (outside comments) | 0 | 0 |
| `BEGIN;` / `COMMIT;` at top level | 0 | 0 |

Python sanity check: `cd backend && venv/Scripts/python -c "<plan-automated-check>"` → exit 0, prints `migration 014 structure OK`.

## User Setup Required

None — this plan only writes the migration file. Plan 07 (BLOCKING) is the human-action checkpoint where the user runs `DATABASE_URL=… venv/Scripts/python scripts/run_migrations.py` to apply 012-016 to the live Supabase database.

## Next Phase Readiness

- **Plan 05 (migration 015 RLS policies)** is independent of this column; it operates on `scope` + `user_id` (added by 012) and `folders` (added by 013). 014 does not affect 015's RLS catalog.
- **Plan 06 (migration 016 search indexes)** can now reference `documents.content_markdown` for the GIN trigram index (`CREATE INDEX … USING gin (content_markdown gin_trgm_ops)`) knowing the column exists. `pg_trgm` is already enabled (migration 012). Lexical migration order (014 < 016) guarantees this dependency.
- **Plan 07 (BLOCKING — schema push)** can apply 014 against the live DB; existing Episode 1 documents will land at `content_markdown=NULL`, `content_markdown_status='pending'` automatically.
- **Plan 08 (test_two_scope_rls.py)** can write falsifiable assertion 34 against the CHECK constraint (`INSERT … content_markdown_status='processing'` rejected because 'processing' is not in the canonical vocabulary).
- **Phase 2 (backfill_content_markdown.py)** has its scan target (`SELECT … WHERE content_markdown_status <> 'ready'`) backed by the partial index added here. Backfill writes to `content_markdown` and flips `content_markdown_status` to `'ready'` / `'failed'` / `'requires_user_reupload'`.
- **Phase 4 (grep + read_document tools)** has the column shape they need; tools must surface non-'ready' status explicitly per Pitfall 6 — that's Phase 4's responsibility, not Phase 1's.

Migration is queued for plan 07's push.

## Self-Check: PASSED

**Files exist:**
- FOUND: `backend/migrations/014_content_markdown_column.sql` (29 lines)

**Commits exist:**
- FOUND: `d744518` (feat(01-04): add migration 014 content_markdown column + status enum)

**Verification commands run:**
- `cd backend && venv/Scripts/python -c "<plan-automated-check>"` → exit 0, prints `migration 014 structure OK`
- All 14 acceptance-criterion grep counts match expected values
- Migration is structurally valid; live DB validation deferred to plan 08 (post plan 07 push)

---
*Phase: 01-schema-foundation-two-scope-rls-path-normalizer*
*Completed: 2026-05-03*
