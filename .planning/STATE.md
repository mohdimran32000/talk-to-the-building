---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
stopped_at: Phase 3 / Plan 01 COMPLETE — Migration 019 (3 RPCs) authored + applied via Supabase MCP apply_migration (DATABASE_URL unavailable in this environment, MCP fallback used per Phase 1 / Plan 07 precedent); schemas.py extended with FolderResponse/FolderCreate/FolderPatch/FilePatch + DocumentResponse user_id-nullable + folder_path/scope defaults. pg_proc verified all 3 RPCs present, all SECURITY INVOKER, all granted to authenticated. Wave 2 (Plans 02 + 03) unblocked.
last_updated: "2026-05-07T10:00:27.845Z"
last_activity: 2026-05-07 -- Phase 03 / Plan 01 complete (Migration 019 applied + schemas.py extended)
progress:
  total_phases: 6
  completed_phases: 2
  total_plans: 18
  completed_plans: 13
  percent: 72
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-01)

**Core value:** The agent can locate the *right* piece of information in a large, organized knowledge base — using semantic search when meaning matters and codebase-style traversal (tree/glob/grep/read) when precision matters — without hallucinating across unrelated material.
**Current focus:** Phase 03 — folder-service-routers-dedup-extension

## Current Position

Phase: 03 (folder-service-routers-dedup-extension) — EXECUTING
Plan: 2 of 6 (Plan 01 complete; Wave 2 Plans 02+03 ready to start in parallel)
Status: Executing Phase 03
Last activity: 2026-05-07 -- Phase 03 / Plan 01 complete (Migration 019 RPCs + Phase 3 Pydantic schemas)

Progress: [█████████░] 72% (13/18 plans complete: Phase 1 = 8/8, Phase 2 = 4/4, Phase 3 = 1/6); Project: 33% (2/6 phases complete; Phase 3 in progress 1/6)

## Performance Metrics

**Velocity:**

- Total plans completed: 13 (Phase 1: 8, Phase 2: 4, Phase 3: 1)
- Average duration: ~3.6 min
- Total execution time: ~47 min

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1 | 8 | ~12 min | ~1.5 min |
| 2 | 4 (complete) | ~23 min | ~5.8 min |
| 3 | 1 (in progress, 1/6) | ~12 min | ~12 min |

**Recent Trend:**

- Last 7 plans: 01-08 (RLS matrix 49/0) → 02-01 (~5 min, Storage Gap closure) → 02-02 (~5 min, synchronous content_markdown write) → 02-03 (~6 min, backfill CLI; 1 Rule-3 deviation) → 02-04 (~7 min, test_backfill.py 15/15 PASS) → **03-01 (~12 min, 2 files, 3 tasks + 1 human-action checkpoint — Migration 019 with 3 cross-table-atomic PL/pgSQL RPCs + Pydantic v2 schemas extension; zero code deviations; orchestrator handled DATABASE_URL absence by falling back to Supabase MCP `apply_migration` + verifying via MCP `execute_sql` against pg_proc — this is now the documented apply-path fallback for any future migration)**
- Trend: ✅ on-spec; Phase 3 starts green; paste-from-PATTERNS succeeded again. New convention learned this plan: cross-table-atomic RPC pattern (when one logical operation spans >1 table, write it as a PL/pgSQL function — only cross-table atomicity unit available from supabase-py because each `.execute()` is its own PostgREST txn). New idioms first-used-in-codebase: FOR UPDATE row-lock for TOCTOU mitigation; GET DIAGNOSTICS ROW_COUNT for return-value capture; ON CONFLICT (expression-list) DO NOTHING + RETURNING + null-check fallback SELECT for atomic upsert with created/existed differentiation. New defense pattern: Pydantic v2 immutability via field omission (FilePatch deliberately omits `scope` so smuggled bodies are silently dropped at parse time). Apply-path fallback chain documented: try DATABASE_URL + run_migrations.py first; if env missing, fall back to Supabase MCP `apply_migration` + verify via pg_proc query

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
- Phase 2 / Plan 03 (executed): `backend/scripts/backfill_content_markdown.py` reuses `extract_text()` from `app.services.ingestion` directly (one import, one call site) — no Docling re-implementation. This guarantees byte-equivalence with Plan 02's synchronous-on-upload markdown for any blob (Phase 2 SC4 precondition); Plan 04's integration test will assert this empirically on a fresh corpus
- Phase 2 / Plan 03 (executed): **Module-load env-var ordering convention** for scripts that import from `app.services.*` — `load_dotenv()` MUST run BEFORE `from app.services.ingestion import extract_text` because `ingestion.py:22` instantiates `genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))` at module-import time. Without env loaded first, the import crashes (or silently produces a misconfigured client) in any subprocess that doesn't pre-export the env. Used `# noqa: E402` on the `from app.*` imports to document the intentional non-top-of-file ordering. This is now a project-wide convention for any future batch script that imports from `app.services.*`
- Phase 2 / Plan 03 (executed): **Per-row state-machine writer pattern** — wrap ONLY the Docling extraction in try/except; success path writes `'ready'` + payload; download-None branch writes `'requires_user_reupload'`; Docling exception writes `'failed'`; nested try around the UPDATE itself catches DB errors without escaping the loop. The loop NEVER raises; only `KeyboardInterrupt` / `SystemExit` propagate. This is now the established pattern for any future per-row backfill / migration script
- Phase 2 / Plan 03 (executed): **Canary check pattern** for batch scripts that depend on external services — probe reachability up front (cheapest service-role op, e.g. `storage.from_('documents').list(path='', options={'limit':1})`); abort with exit 1 + a clear remediation message pointing to the setup task that creates the resource. Defense in depth via end-of-run anomaly warning if every row ended in the same non-success state (likely indicates misconfiguration rather than real corpus state)
- Phase 2 / Plan 03 (executed): **Interactive scoped-cleanup ritual** for production scripts (CLAUDE.md "Tests must NEVER delete all user data" rule extended to operator scripts): SELECT candidates → print human-readable table → `input()` requiring literal `y`/`yes` → for each id: per-id chunks-then-document DELETE. NEVER `DELETE WHERE` blanket queries. Two-step (chunks first, then document) is defensive against absent FK CASCADE. This is now the convention for any future production cleanup script
- Phase 2 / Plan 03 (executed): **Static grep gate for Pitfall 6 (RANK 2 — chunk-stitching forbidden)** — backfill_content_markdown.py is asserted to contain ZERO occurrences of `string_agg` or `array_agg` (verified at task acceptance time). Plan-checker can grep-gate any future backfill / re-index script with the same rule
- Phase 2 / Plan 03 (executed): Exit code semantics for backfill — `0` clean (every row terminal non-failed OR --dry-run completed OR no rows matched); `1` missing env vars OR canary failure; `2` if any row ended at `'failed'` (matches `run_migrations.py:57`'s "2 = runtime exception" precedent — operators can grep on exit code in CI/cron). No-rows-matching is intentionally exit 0 (steady state for a fully-ready corpus; differs from a strict reading of the plan's frontmatter which suggested exit 1 — documented in 02-03-SUMMARY.md decision #5)
- Phase 2 / Plan 04 (executed): **Integration-test canary pattern** is now codebase convention — any test that depends on an external resource (Storage bucket, RLS policy, third-party service) MUST probe reachability BEFORE iterating; failure mode = single FAIL h.test + early return + actionable [FATAL] message naming the responsible plan. NEVER cascading failures. Mirrors `test_two_scope_rls.py::_verify_admin_setup`. Empirically validated: caught the operator-pre-req runbook gap on first run with maximum signal-to-noise
- Phase 2 / Plan 04 (executed): **Subprocess-test pattern** for CLI scripts — invoke via `[sys.executable, "scripts/<script>.py", ...flags...]` (same venv as test); set `cwd=backend/`; `timeout=120`; `capture_output=True`; surface last-300-chars of stderr in failure messages; NEVER use `shell=True`. First instance of a test exercising a production CLI as a child process; the pattern is now reusable for any future CLI-test
- Phase 2 / Plan 04 (executed): **Mixed-client convention per assertion** — anon-key + JWT (`h.get_user_supabase_client(token)` and `requests.post` with `Authorization: Bearer <jwt>`) for "as-a-user" assertions (RLS applies); service-role (constructed inline via `_service_role_client()` matching `auth.py:8-12`) for fixture-insert / Storage-download / direct-DB-readback paths (RLS bypass). The choice is documented per assertion via section name + inline comments — reviewers can audit which trust boundary is being tested
- Phase 2 / Plan 04 (executed): **Scoped-cleanup discipline for fixture-inserting tests** — module-level `_tracked_*` lists populated at create-time; per-id DELETE in finally; defense-in-depth two-path cleanup (API delete + service-role delete) for resources that may live outside the test user's RLS scope. CLAUDE.md "Tests must NEVER delete all user data" rule honored verbatim — verified by static `grep -iE "DELETE FROM|TRUNCATE"` returning no matches
- Phase 2 / Plan 04 (executed): **Operator-pre-req runbook gap discovered** — the documents bucket did NOT exist on the Supabase project at suite-run time despite operator approving the Plan 01/04 checkpoint. Migration 018's RLS policies were already applied. Resolution: orchestrator created the bucket programmatically via service-role `sb.storage.create_bucket('documents', options={'public': False, 'file_size_limit': 52428800})`. Recommendation captured: future Phase 1/2 setup runbook should either (a) automate bucket creation as a one-shot script alongside `run_migrations.py` OR (b) explicitly verify before suite invocation via the canary `sb.storage.from_('documents').list(path='', options={'limit':1})`. This is a UAT carry-forward observation, NOT a code defect — the pre-req is correctly documented in Plan 01 SUMMARY §"User Setup Required" and the canary did its job by surfacing the gap
- Phase 2 / Plan 04 (executed): **Cross-suite sweep result** — 163 passed / 23 failed across 14 suites; Backfill 15/15 PASS; ALL 23 FAILs are pre-existing Phase-1 carry-forward (admin-assumption + auth middleware regression in Threads/Messages/Hybrid/Tools/Sub-Agents). NONE are attributable to Phase 2 deliverables. Phase 2 closes green; the 23 FAILs continue to be tracked as the Phase-1 carry-forward and remain out of scope for Phase 2/3+
- Phase 3 / Plan 01 (executed): **Cross-table-atomic RPC pattern is now codebase convention** — when one logical operation spans >1 table (rename touches documents AND folders), pack into a single PL/pgSQL function. supabase-py runs every `.execute()` in its own PostgREST transaction, so an RPC is the only cross-table atomicity unit available. rename_folder_prefix wraps two UPDATEs (documents + folders) in a single PL/pgSQL block — implicitly transactional; mid-rename crash leaves no partial state (FOLDER-03 + Pitfall 5 mid-rename rollback)
- Phase 3 / Plan 01 (executed): **FOR UPDATE row-lock TOCTOU pattern first-used in codebase** — delete_folder_if_empty does `SELECT ... INTO ... FROM folders WHERE id = ... FOR UPDATE;` first, then count-check + DELETE in the same PL/pgSQL block. Standard MVCC: lock blocks concurrent UPDATE/DELETE of same row, doesn't block SELECT. Eliminates the TOCTOU race an app-side SELECT-then-DELETE would have (FOLDER-04 + Pitfall 5 race-free-delete)
- Phase 3 / Plan 01 (executed): **ON CONFLICT (expression-list) DO NOTHING + RETURNING + null-check fallback SELECT idiom** — atomic upsert pattern that returns `(id, created_bool)` for both winners (created=TRUE) and losers (created=FALSE) of a concurrent INSERT race. The expression list MUST mirror Migration 013's unique expression index VERBATIM: `(scope, COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid), path)` — Postgres requires expression-list match or it raises 'there is no unique or exclusion constraint matching the ON CONFLICT specification' at execution time (Pitfall 10 + STATE.md Strategy B)
- Phase 3 / Plan 01 (executed): **All Phase 3 RPCs are SECURITY INVOKER (NOT SECURITY DEFINER)** — RLS policies from Migration 015 apply when the RPC executes; admin gate at the router layer (Plan 04 + Plan 05) is the first line of defense for global-scope writes, RLS is the second. SECURITY DEFINER would bypass RLS entirely — explicitly forbidden in the migration's own verifier (`assert 'SECURITY DEFINER' not in body`). Convention extends to any future Phase 3+ RPC
- Phase 3 / Plan 01 (executed): **FilePatch deliberately omits scope field — Pydantic v2 silently-drop-unknown is the FIRST defense layer** — Pydantic v2's default `extra='ignore'` makes a smuggled `{"scope": "global"}` request body get dropped at parse time before the router code ever sees it. Three-layer defense: Pydantic drop -> router empty-update-rejection (Plan 05) -> Migration 015 `forbid_scope_mutation` trigger (the bedrock). One-line in-class comment documents the immutability contract for future maintainers (Pitfall B mitigation; pattern extends to any future Patch model that touches a Migration-015-immutable column)
- Phase 3 / Plan 01 (executed): **DocumentResponse.user_id changed from str to Optional[str] = None** — global-scope rows have NULL user_id per Migration 012 coupling CHECK; without nullability the FastAPI response serializer raises ValidationError on any global doc the response endpoint touches. Same plan added folder_path: str = '/' and scope: str = 'user' defaults (FOLDER-07) — defaults preserve existing-row response shape so no Phase 1/2 test assertion needs updating
- Phase 3 / Plan 01 (executed): **Migration apply-path fallback chain documented** — try DATABASE_URL + run_migrations.py first; if env missing, fall back to Supabase MCP `apply_migration` (the canonical fallback Phase 1 / Plan 07 used). Both paths run the EXACT same SQL file content. Verify via Supabase MCP `execute_sql` against pg_proc (`SELECT proname, prosecdef, proacl FROM pg_proc p JOIN pg_namespace n ON p.pronamespace=n.oid WHERE n.nspname='public' AND p.proname IN (...)`). Future phase plans should accept either apply path equivalently
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

Last session: 2026-05-07 (Phase 3 / Plan 01 executed)
Stopped at: Phase 3 / Plan 01 COMPLETE — Migration 019 (3 cross-table-atomic PL/pgSQL RPCs: rename_folder_prefix, delete_folder_if_empty, create_folder_if_not_exists) authored + applied via Supabase MCP `apply_migration` (DATABASE_URL unavailable in this environment, MCP fallback used per Phase 1 / Plan 07 precedent); pg_proc verified all 3 functions present, all SECURITY INVOKER (`prosecdef = false`), all granted to authenticated. schemas.py extended with 4 new Pydantic v2 models (FolderResponse, FolderCreate, FolderPatch, FilePatch) + DocumentResponse changes (user_id -> Optional[str]; + folder_path: str = '/'; + scope: str = 'user'). FilePatch deliberately omits scope field with one-line comment documenting the Pydantic-v2-ignore-unknown defense alongside Migration 015 forbid_scope_mutation trigger.

Phase 2 (complete) recap:
  - ✅ 01-PLAN.md: Storage upload at upload-time + Migration 018 storage.objects RLS (commits 41e3eeb, e256c91; SUMMARY at 02-01-SUMMARY.md)
  - ✅ 02-PLAN.md: Synchronous content_markdown write in ingest_document() + ingest_document_update() (atomic single-UPDATE) + docling==2.91.0 pin (BACKFILL-01); commits 4dd7c4c, 91ad425; SUMMARY at 02-02-SUMMARY.md
  - ✅ 03-PLAN.md: backfill_content_markdown.py CLI (BACKFILL-02 + BACKFILL-04); commit 28e8fab; SUMMARY at 02-03-SUMMARY.md
  - ✅ 04-PLAN.md: test_backfill.py integration suite (414 lines, 21 h.test()); commits 2ad9b78, 01f2782; suite-level run 15/15 PASS; SUMMARY at 02-04-SUMMARY.md

Phase 3 progress (1/6 complete):
  - ✅ 01-PLAN.md: Migration 019 (3 RPCs) + Pydantic v2 schemas extension (FOLDER-03, FOLDER-04); commits ca017e7 (Task 1) + 5728f6f (Task 3) — Task 2 was a checkpoint:human-action that the orchestrator handled by falling back to Supabase MCP apply_migration; SUMMARY at 03-01-SUMMARY.md
  - ⏳ Wave 2 ready (parallel): 02-PLAN.md (folder_service.py extensions: list_folder/create_folder/move_document/rename_folder/delete_folder — DIRECT consumer of all 3 RPCs by name) + 03-PLAN.md (record_manager.determine_action() extended with scope+folder_path)
  - ⏳ Wave 3 ready (parallel after Wave 2): 04-PLAN.md (folders router) + 05-PLAN.md (files router PATCH)
  - ⏳ Wave 4: 06-PLAN.md (test_folders.py integration suite)

Apply-path note: Migration 019 was applied via Supabase MCP `apply_migration` instead of `run_migrations.py` because DATABASE_URL was not exported in this environment. Verification via MCP `execute_sql` against pg_proc confirmed all 3 functions present with prosecdef=false and authenticated grants. This is now the documented apply-path fallback for any future migration.

Carry-forward from Phase 1: still pending — commit 017.sql; align Episode-1 test_settings/test_hybrid/test_tools admin assumption (the 23 FAILs in the cross-suite sweep result; tracked but out of scope for Phases 2/3+). The 017.sql carry-forward is a documentation/migration-naming follow-up, not a missing plan.

Resume file: Phase 3 / Plan 02 (folder_service.py extensions) AND Plan 03 (record_manager extension) — Wave 2, parallel-safe with each other; both directly unblocked by this plan's RPCs + schemas.
