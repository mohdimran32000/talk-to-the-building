---
phase: 01
plan: 07
type: execute
wave: 4
depends_on: [02, 03, 04, 05, 06]
files_modified:
  - backend/scripts/verify_phase1_schema.py
autonomous: false
requirements:
  - SCHEMA-01
  - SCHEMA-02
  - SCHEMA-03
  - SCHEMA-04
  - SCHEMA-05
  - RLS-01
  - RLS-02
  - RLS-03
must_haves:
  truths:
    - "Migrations 012, 013, 014, 015, 016 are all applied to the live Supabase Postgres database"
    - "`SELECT 1 FROM pg_extension WHERE extname='pg_trgm'` returns 1 row"
    - "`information_schema.columns` shows documents.folder_path, documents.scope, documents.content_markdown, documents.content_markdown_status, document_chunks.scope all exist"
    - "`information_schema.tables` shows public.folders exists"
    - "`pg_policies` shows the 19 new two-scope policies (7 documents + 5 chunks + 7 folders) exist; the 7 Episode-1 single-axis policies are gone"
    - "`pg_trigger` shows the 3 forbid_scope_mutation triggers attached"
    - "`pg_indexes` shows the 5 search indexes from migration 016 exist plus the unique expression indexes from 012/013"
    - "All existing Episode 1 documents have folder_path='/', scope='user', content_markdown_status='pending', user_id IS NOT NULL (defaults applied with no manual data movement)"
    - "Re-running run_migrations.py is a no-op (every migration is idempotent — IF NOT EXISTS / IF EXISTS / CREATE OR REPLACE / DROP-before-CREATE pattern)"
  artifacts:
    - path: "(no new files — this plan executes scripts/run_migrations.py)"
      provides: "Live database schema state matching migrations 012-016"
  key_links:
    - from: "Plans 02-06 SQL files"
      to: "Live Supabase Postgres schema"
      via: "DATABASE_URL + venv/Scripts/python scripts/run_migrations.py"
      pattern: "Migration 0NN applied"
    - from: "Live schema (after this plan)"
      to: "Plan 08 (test_two_scope_rls.py)"
      via: "Tests query the live DB via supabase-py with anon key + JWT — schema must exist or every test fails with 'relation/column does not exist'"
      pattern: "documents.folder_path"
---

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Developer environment (DATABASE_URL) -> Supabase Postgres | Privileged direct-connection string runs DDL; one-time push of 5 migrations |
| run_migrations.py -> live database | Each .sql file runs in its own transaction; rollback on any failure stops the run, leaves the DB in the last-committed state |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-1-Apply | Operational / Data Integrity | Migration application | mitigate | (1) Each migration runs in its own transaction (run_migrations.py:39 — autocommit=False) — partial failure rolls back that single migration cleanly. (2) All 5 migrations are idempotent (verified by plans 02-06 acceptance criteria) — if a partial application requires re-running, no errors. (3) Migration files run in lexical order via `sorted(MIGRATIONS_DIR.glob("*.sql"))` — 012 → 013 → 014 → 015 → 016 is guaranteed. (4) Existing 011 migrations are untouched (idempotent re-application is a no-op). (5) Tests must NEVER bulk-delete (CLAUDE.md) — this plan does NOT delete any data; it only adds columns/tables/policies/indexes. |
| T-1-Apply-Verify | Operational | Post-apply verification | mitigate | After the runner reports success, this plan runs a structural verification query against the live DB (pg_extension, information_schema, pg_policies, pg_trigger, pg_indexes counts) to confirm the schema state matches expectations BEFORE plan 08 runs. Catches "runner says OK but a migration was actually skipped" failure modes. |
| T-1-Apply-Episode1 | Data Integrity | Existing Episode 1 documents | mitigate | The DEFAULTs on the new columns (folder_path='/', scope='user', content_markdown_status='pending') ensure existing rows are migrated automatically with NO manual data movement. Postgres 11+ stores DEFAULTs as metadata pointers (no full table rewrite). ROADMAP success criterion 4: "Existing Episode 1 documents are queryable at folder_path='/', scope='user' immediately after migrations land, with no manual data movement." This plan verifies that criterion against the live DB. |
</threat_model>

<objective>
Apply migrations 012-016 to the live Supabase Postgres database via the existing `backend/scripts/run_migrations.py` runner, then verify the schema state with a structural query before plan 08's tests run. This is the [BLOCKING] schema push — without it, every test in plan 08 fails with `relation/column does not exist`. The runner is non-interactive and idempotent; re-running is safe. After the push, the live DB matches the schema state designed across plans 02-06.

This plan has no new files to write — it executes `run_migrations.py` and runs a verification query. The plan IS the apply step.
</objective>

<execution_context>
@.claude/get-shit-done/workflows/execute-plan.md
@.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md

@.planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-RESEARCH.md
@.planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-VALIDATION.md
@CLAUDE.md

@backend/scripts/run_migrations.py
@backend/migrations/012_folder_path_and_scope.sql
@backend/migrations/013_folders_table.sql
@backend/migrations/014_content_markdown_column.sql
@backend/migrations/015_two_scope_rls.sql
@backend/migrations/016_search_indexes.sql

<interfaces>
<!-- The runner contract — invariant; do not modify. -->

backend/scripts/run_migrations.py:
  - Reads DATABASE_URL from environment
  - Globs `backend/migrations/*.sql`, runs in lexical (`sorted()`) order
  - Each file: opens cursor, executes file content, commits on success / rolls back on failure
  - Prints `RUN  <filename> ... OK` (success) or `FAIL\n  <error>` (failure)
  - On failure: returns exit code 2; stops further migrations
  - On success of all files: returns exit code 0
  - Idempotent re-run: every file in plans 02-06 uses IF NOT EXISTS / IF EXISTS / CREATE OR REPLACE / DROP-before-CREATE; re-running is a no-op (no errors)

Project rule (CLAUDE.md):
  - Python backend MUST use venv: `cd backend && venv/Scripts/python scripts/run_migrations.py`

Required environment variable:
  - DATABASE_URL — Supabase project's Direct connection string (port 5432, NOT pooler)
  - Get from: Supabase Dashboard → Project Settings → Database → Connection string → URI → Direct connection
  - Per CLAUDE.md / run_migrations.py docstring lines 4-7
</interfaces>
</context>

<tasks>

<task id="1-07-01" type="checkpoint:human-action" gate="blocking">
  <name>Task 1: Confirm DATABASE_URL is set in environment</name>
  <what-built>
    Migrations 012-016 are written and ready to apply (plans 02-06). The next step requires the developer's DATABASE_URL environment variable to point at the target Supabase project's Postgres direct connection string.
  </what-built>
  <how-to-verify>
    Open a terminal in the project root and run:
    ```
    cd backend && venv/Scripts/python -c "import os; url = os.environ.get('DATABASE_URL', ''); print('DATABASE_URL is set' if url.startswith('postgres') else 'DATABASE_URL is NOT set or invalid'); print(f'  starts with: {url[:30]}...' if url else '')"
    ```

    Expected output: `DATABASE_URL is set` followed by `starts with: postgres://...` or `postgresql://...`.

    If NOT set:
    1. Get the connection string from Supabase Dashboard → Project Settings → Database → Connection string → URI → **Direct connection** (port 5432, not the pooler)
    2. Set it in your shell (PowerShell): `$env:DATABASE_URL = "postgresql://postgres.<project>:<password>@<host>:5432/postgres"`
    3. Re-run the verification command.

    Confirm the connection string targets the **correct project** (development vs production). This migration set adds columns and policies to documents/document_chunks/folders — wrong project = wrong database modified.
  </how-to-verify>
  <resume-signal>Type "approved" once DATABASE_URL is set and points at the correct Supabase project.</resume-signal>
  <done>Developer has confirmed DATABASE_URL is set in the current shell session and points at the intended target project.</done>
</task>

<task id="1-07-02" type="auto">
  <name>Task 2: Apply migrations 012-016 via run_migrations.py</name>
  <files></files>
  <read_first>
    - backend/scripts/run_migrations.py (lines 1-67 — confirms each migration runs in its own transaction; confirms files run in `sorted()` lexical order, so 012-016 run after 001-011 every time; confirms exit code 0 on full success / 2 on any failure)
    - backend/migrations/012_folder_path_and_scope.sql (the file from plan 02 — to confirm it exists before invoking the runner)
    - backend/migrations/013_folders_table.sql (plan 03)
    - backend/migrations/014_content_markdown_column.sql (plan 04)
    - backend/migrations/015_two_scope_rls.sql (plan 05)
    - backend/migrations/016_search_indexes.sql (plan 06)
  </read_first>
  <action>
    Run the migration runner to apply migrations 012-016 to the live Supabase Postgres database. The runner re-applies migrations 001-011 first (idempotent — no-op since they're already applied). Then it applies 012-016 in lexical order (012 → 013 → 014 → 015 → 016).

    Execute (from project root):
    ```
    cd backend && venv/Scripts/python scripts/run_migrations.py
    ```

    Expected stdout:
    ```
    Found N migration(s) in <path>
      - 001_threads_and_messages.sql
      - 002_file_search_stores.sql
      ...
      - 011_improved_keyword_search.sql
      - 012_folder_path_and_scope.sql
      - 013_folders_table.sql
      - 014_content_markdown_column.sql
      - 015_two_scope_rls.sql
      - 016_search_indexes.sql

    RUN  001_threads_and_messages.sql ... OK
    ...
    RUN  012_folder_path_and_scope.sql ... OK
    RUN  013_folders_table.sql ... OK
    RUN  014_content_markdown_column.sql ... OK
    RUN  015_two_scope_rls.sql ... OK
    RUN  016_search_indexes.sql ... OK

    All N migration(s) applied successfully.
    ```

    Exit code: 0.

    On failure (any single migration FAILs): the runner stops, prints the error from psycopg2 (`<ExceptionType>: <message>`), rolls back that single migration's transaction, and exits with code 2. Earlier migrations remain committed; later migrations are NOT attempted. If a failure occurs:
    1. Read the error message carefully — it identifies which migration and what failed.
    2. Cross-reference against the migration's acceptance criteria from plans 02-06.
    3. Fix the migration file (do NOT manually intervene in the database).
    4. Re-run `python scripts/run_migrations.py` — the migration runner is idempotent; previously-applied migrations re-apply as no-ops.

    Do NOT manually run individual migrations via psql or the Supabase SQL editor. The runner is the canonical apply path; bypassing it loses transaction safety and complicates rollback.
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python scripts/run_migrations.py</automated>
  </verify>
  <acceptance_criteria>
    - Command `cd backend && venv/Scripts/python scripts/run_migrations.py` exits with code 0.
    - stdout contains the line `RUN  012_folder_path_and_scope.sql ... OK`.
    - stdout contains the line `RUN  013_folders_table.sql ... OK`.
    - stdout contains the line `RUN  014_content_markdown_column.sql ... OK`.
    - stdout contains the line `RUN  015_two_scope_rls.sql ... OK`.
    - stdout contains the line `RUN  016_search_indexes.sql ... OK`.
    - stdout contains the line `All N migration(s) applied successfully.` (where N is total count of .sql files including 001-011 + 012-016).
    - stdout contains NO lines starting with `FAIL`.
  </acceptance_criteria>
  <done>
    All 5 new migrations (012-016) are applied to the live database. Runner reports success for every file. Exit code 0.
  </done>
</task>

<task id="1-07-03" type="auto">
  <name>Task 3: Verify live schema state via verify_phase1_schema.py</name>
  <files>backend/scripts/verify_phase1_schema.py</files>
  <read_first>
    - .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-RESEARCH.md § Validation Architecture (lines ~948-1052 — assertions 36-40 inform the structural-verify query shape)
    - .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-VALIDATION.md (Wave 0 Requirements section — confirms what must exist before plan 08 runs)
    - backend/scripts/test_helpers.py (for env-var loading conventions if needed)
    - backend/scripts/run_migrations.py (for the psycopg2 + DATABASE_URL connection pattern; the verify script mirrors this style)
  </read_first>
  <action>
    Create `backend/scripts/verify_phase1_schema.py` — a reusable, shell-safe verification script that connects to the live database via psycopg2 (using DATABASE_URL) and queries the system catalogs to confirm every expected schema artifact from migrations 012-016 exists. Extracted into a real .py file (per Phase 1 revision WARNING 3) to remove the bash-vs-PowerShell shell-quoting fragility of the original inline `python -c "..."` pattern, and to make this verification re-runnable for any future Phase 1+ schema regression check.

    Write the script with the following content (paste-ready):

    ```python
    """Phase 1 schema verifier — runs structural checks against the live Supabase Postgres DB.

    Invoked by plan 07 task 1-07-03 immediately after run_migrations.py applies migrations
    012-016. Queries pg_extension / information_schema / pg_proc / pg_trigger / pg_policies /
    pg_indexes and confirms every expected artifact exists.

    Usage:
        cd backend && venv/Scripts/python scripts/verify_phase1_schema.py

    Exit code:
        0  — all 18 checks pass; plan 08 (test_two_scope_rls.py) is unblocked
        1  — at least one check failed; failed checks are listed in stdout

    Reusable beyond Phase 1 — any future schema regression test can run this script as a
    smoke check that the Phase 1 schema is still intact.
    """
    import os
    import sys

    import psycopg2


    CHECKS = [
        ("pg_trgm extension enabled",
         "SELECT COUNT(*) FROM pg_extension WHERE extname='pg_trgm'", 1),
        ("documents.folder_path column exists",
         "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='documents' AND column_name='folder_path'", 1),
        ("documents.scope column exists",
         "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='documents' AND column_name='scope'", 1),
        ("documents.content_markdown column exists",
         "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='documents' AND column_name='content_markdown'", 1),
        ("documents.content_markdown_status column exists",
         "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='documents' AND column_name='content_markdown_status'", 1),
        ("document_chunks.scope column exists",
         "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='document_chunks' AND column_name='scope'", 1),
        ("public.folders table exists",
         "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='folders'", 1),
        ("public.is_admin() function exists",
         "SELECT COUNT(*) FROM pg_proc p JOIN pg_namespace n ON p.pronamespace=n.oid WHERE n.nspname='public' AND p.proname='is_admin'", 1),
        ("public.forbid_scope_mutation() function exists",
         "SELECT COUNT(*) FROM pg_proc p JOIN pg_namespace n ON p.pronamespace=n.oid WHERE n.nspname='public' AND p.proname='forbid_scope_mutation'", 1),
        ("forbid_scope_mutation triggers attached (>= 3)",
         "SELECT COUNT(*) FROM pg_trigger WHERE tgname LIKE '%forbid_scope_mutation%' AND NOT tgisinternal", (3,)),
        # Note: LIKE 'documents_%' uses default _ wildcard; we want literal underscore-prefix,
        # so the ESCAPE clause is required. The script form removes the shell-quoting fragility
        # of doing this in `python -c "..."`.
        ("Two-scope policies on documents (>= 7)",
         r"SELECT COUNT(*) FROM pg_policies WHERE schemaname='public' AND tablename='documents' AND policyname LIKE 'documents\_%' ESCAPE '\'", (7,)),
        ("Two-scope policies on document_chunks (>= 5)",
         r"SELECT COUNT(*) FROM pg_policies WHERE schemaname='public' AND tablename='document_chunks' AND policyname LIKE 'document_chunks\_%' ESCAPE '\'", (5,)),
        ("Two-scope policies on folders (>= 7)",
         r"SELECT COUNT(*) FROM pg_policies WHERE schemaname='public' AND tablename='folders' AND policyname LIKE 'folders\_%' ESCAPE '\'", (7,)),
        ("Episode-1 single-axis docs policies dropped",
         "SELECT COUNT(*) FROM pg_policies WHERE schemaname='public' AND tablename='documents' AND policyname='Users can view own documents'", 0),
        ("Search index documents_content_markdown_trgm_idx exists",
         "SELECT COUNT(*) FROM pg_indexes WHERE schemaname='public' AND indexname='documents_content_markdown_trgm_idx'", 1),
        ("Search index documents_folder_path_prefix_idx exists",
         "SELECT COUNT(*) FROM pg_indexes WHERE schemaname='public' AND indexname='documents_folder_path_prefix_idx'", 1),
        ("Unique expression index folders_scope_user_path_unique exists",
         "SELECT COUNT(*) FROM pg_indexes WHERE schemaname='public' AND indexname='folders_scope_user_path_unique'", 1),
        ("Unique expression index documents_scope_user_path_filename_unique exists",
         "SELECT COUNT(*) FROM pg_indexes WHERE schemaname='public' AND indexname='documents_scope_user_path_filename_unique'", 1),
    ]


    def main() -> int:
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            print("[FATAL] DATABASE_URL not set. Export the Direct connection string from Supabase.")
            return 1

        try:
            conn = psycopg2.connect(db_url)
        except Exception as e:
            print(f"[FATAL] Could not connect to DATABASE_URL: {e}")
            return 1
        conn.autocommit = True

        print("Verifying live schema state after plans 02-06 push...")
        failures = []
        for label, sql, expected in CHECKS:
            with conn.cursor() as c:
                c.execute(sql)
                got = c.fetchone()[0]
            ok = (got == expected) if isinstance(expected, int) else (got >= expected[0])
            status = "OK" if ok else "FAIL"
            print(f"  [{status}] {label}: got={got} expected={expected}")
            if not ok:
                failures.append(label)

        conn.close()
        print()
        if failures:
            print(f"SCHEMA VERIFY FAILED: {len(failures)} check(s) did not pass: {failures}")
            return 1
        print(f"SCHEMA VERIFY OK: all {len(CHECKS)} checks passed. Plan 08 (test_two_scope_rls.py) is unblocked.")
        return 0


    if __name__ == "__main__":
        sys.exit(main())
    ```

    Then execute it (this IS the verification step — the script's exit code is the gate):
    ```
    cd backend && venv/Scripts/python scripts/verify_phase1_schema.py
    ```

    The script being a real .py file (not a shell-embedded `python -c "..."`) eliminates the bash-vs-PowerShell shell-quoting fragility around backslash escapes in the `LIKE 'documents\_%' ESCAPE '\'` clauses. It also makes the verification reusable as a regression smoke test for any future schema change.

    Conventions:
    - Script lives under `backend/scripts/` (project convention; matches `run_migrations.py`).
    - Uses psycopg2 directly (no ORM dependency); module-import surface matches `run_migrations.py`.
    - `main()` returns int exit code; `if __name__ == "__main__": sys.exit(main())` footer.
    - CHECKS is a top-level list of (label, sql, expected) tuples — extending coverage in future phases is a one-line append.
    - No raw f-string SQL with user input — all SQL is static (no injection surface).

    Critical DON'Ts:
    - DO NOT inline this verification logic into a `python -c "..."` shell invocation (that was the WARNING 3 fragility this task fixes).
    - DO NOT add `DELETE FROM` or `TRUNCATE` to the script (CLAUDE.md mandatory rule — the verifier is read-only by design).
    - DO NOT hardcode connection strings — read DATABASE_URL from environment, matching `run_migrations.py`.
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python scripts/verify_phase1_schema.py</automated>
  </verify>
  <acceptance_criteria>
    - File `backend/scripts/verify_phase1_schema.py` exists.
    - File starts with a triple-quoted module docstring describing the script's purpose.
    - File defines a `CHECKS` top-level list of tuples.
    - File defines a `main() -> int` function that returns 0 on success and 1 on any failure.
    - File ends with `if __name__ == "__main__": sys.exit(main())` footer.
    - File imports `psycopg2` and uses `os.environ.get("DATABASE_URL")` (NOT hardcoded).
    - File contains NO `DELETE FROM` or `TRUNCATE` strings (read-only verification).
    - Running `cd backend && venv/Scripts/python scripts/verify_phase1_schema.py` (after plan 07 task 1-07-02 has applied migrations) exits with code 0.
    - The script's stdout contains the line `SCHEMA VERIFY OK: all 18 checks passed. Plan 08 (test_two_scope_rls.py) is unblocked.`
    - All individual `[OK]` lines present (no `[FAIL]`).
    - Specifically the live DB confirms:
      - `pg_extension WHERE extname='pg_trgm'` returns 1.
      - `documents.folder_path`, `documents.scope`, `documents.content_markdown`, `documents.content_markdown_status` all exist (4 columns confirmed).
      - `document_chunks.scope` exists.
      - `public.folders` table exists.
      - `public.is_admin()` and `public.forbid_scope_mutation()` functions exist.
      - At least 3 forbid_scope_mutation triggers attached.
      - At least 7 documents_* policies, 5 document_chunks_* policies, 7 folders_* policies in pg_policies.
      - The Episode-1 policy `"Users can view own documents"` is gone (count = 0).
      - Indexes documents_content_markdown_trgm_idx, documents_folder_path_prefix_idx, folders_scope_user_path_unique, documents_scope_user_path_filename_unique all exist in pg_indexes.
  </acceptance_criteria>
  <done>
    `backend/scripts/verify_phase1_schema.py` exists, runs in isolation as a reusable schema-regression smoke test, exits 0 against the live database after plan 07 task 1-07-02 has applied migrations 012-016, and prints `SCHEMA VERIFY OK: all 18 checks passed.` Plan 08 is unblocked. The bash-vs-PowerShell shell-quoting fragility of the prior inline `python -c "..."` pattern is eliminated.
  </done>
</task>

</tasks>

<verification>
Maps to .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-VALIDATION.md row "TEST-04 setup precondition" (every test in plan 08 depends on the schema being applied). Falsifiable assertion 38 from RESEARCH.md § Validation Architecture (Group 5 — `pg_extension lists pg_trgm`) and assertions 39-40 (Group 6 — idempotency: re-running plans 02-06 a second time produces no errors) are validated here.

This plan is the sole [BLOCKING] schema-push task in Phase 1. Without it, plan 08 cannot run.
</verification>

<success_criteria>
- All 5 new migrations (012-016) successfully applied to the live Supabase Postgres database.
- run_migrations.py exits 0 with no FAIL lines.
- Structural verification script confirms all expected columns, tables, functions, triggers, policies, and indexes exist.
- Episode-1 single-axis policies are dropped (verified by searching for "Users can view own documents" in pg_policies — must be 0).
- Existing Episode 1 documents are queryable at folder_path='/', scope='user' (ROADMAP success criterion 4 — implicit via DEFAULT semantics; tested in plan 08).
- Plan 08 is unblocked.
</success_criteria>

<output>
After completion, create `.planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-07-SUMMARY.md` recording: the runner's stdout (paste the `RUN ... OK` lines for 012-016), the structural verification script's stdout (all 18 [OK] lines), the timestamp of the apply, and a one-line confirmation that plan 08 (test_two_scope_rls.py) can now run.
</output>
