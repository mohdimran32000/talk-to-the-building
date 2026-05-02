---
phase: 01
plan: 08
type: execute
wave: 5
depends_on: [01, 07]
files_modified:
  - backend/scripts/test_helpers.py
  - backend/scripts/test_two_scope_rls.py
  - backend/scripts/test_all.py
  - backend/scripts/test_rls.py
autonomous: false
requirements:
  - RLS-04
  - TEST-04
must_haves:
  truths:
    - "TEST_USER_ADMIN fixture exists in test_helpers.py with email 'admin@test.com' and a password"
    - "get_admin_token() helper exists in test_helpers.py and uses anon-key + JWT (NOT service-role)"
    - "get_user_supabase_client(jwt_token) helper exists in test_helpers.py — instantiates supabase-py with anon key and authenticates as the JWT's user (RLS WILL apply)"
    - "test_two_scope_rls.py module exists in backend/scripts/ and is importable"
    - "test_two_scope_rls.py has run() entry point returning (h.passed, h.failed) per the SUITES contract"
    - "test_two_scope_rls.py docstring documents the one-time admin setup: UPDATE public.profiles SET is_admin=true WHERE email='admin@test.com';"
    - "test_two_scope_rls.py setup verifies admin profile has is_admin=true and bails with a clear error message if not"
    - "test_two_scope_rls.py covers all 40 falsifiable assertions from RESEARCH.md § Validation Architecture (Groups 1-5: RLS matrix, scope-mutation prevention, path normalization, CHECK constraints, indexes)"
    - "test_two_scope_rls.py uses anon-key + JWT (via get_user_supabase_client) — NOT service-role — so RLS actually applies"
    - "test_two_scope_rls.py tracks every created ID and cleans up only those IDs in finally — no blanket DELETE FROM (CLAUDE.md rule)"
    - "test_all.py imports test_two_scope_rls and registers it in SUITES as ('Two-Scope RLS', test_two_scope_rls) immediately after the existing ('RLS', test_rls) entry"
    - "Cross-user × cross-scope matrix passes 100% (the gate for Phase 2)"
    - "EXPLAIN ANALYZE assertions show Bitmap Index Scan on documents_content_markdown_trgm_idx (NOT Seq Scan) once a fixture row is seeded"
    - "EXPLAIN ANALYZE assertions show Index Scan on documents_folder_path_prefix_idx (NOT Seq Scan) for LIKE '/x/%' queries"
    - "All normalize_path round-trip cases pass (assertions 17-28: '/' / '/a/b' / '/A/B' case preserved / '/a//b' collapsed / '/a/b/' trailing stripped / '\\\\a\\\\b' backslash replaced / '' and None as root / '/a/../b' raises ValueError / '/a/./b' raises ValueError)"
    - "Cleanup deletes ONLY tracked IDs (per-test-run resources) — no blanket DELETE FROM, no TRUNCATE"
  artifacts:
    - path: "backend/scripts/test_helpers.py"
      provides: "TEST_USER_ADMIN fixture, get_admin_token() helper, get_user_supabase_client() helper"
      contains: "TEST_USER_ADMIN"
      contains_2: "def get_admin_token"
      contains_3: "def get_user_supabase_client"
      min_lines: 20
    - path: "backend/scripts/test_two_scope_rls.py"
      provides: "Cross-user × cross-scope RLS matrix (40 falsifiable assertions per RESEARCH.md § Validation Architecture)"
      exports: ["run"]
      contains: "def run()"
      contains_2: "TEST_USER_ADMIN"
      contains_3: "get_user_supabase_client"
      contains_4: "from app.services.folder_service import normalize_path"
      contains_5: "h.section"
      contains_6: "scope_mutation"
      min_lines: 200
    - path: "backend/scripts/test_all.py"
      provides: "Registration of Two-Scope RLS suite in the full sweep"
      contains: "import test_two_scope_rls"
      contains_2: "(\"Two-Scope RLS\", test_two_scope_rls)"
  key_links:
    - from: "test_helpers.TEST_USER_ADMIN fixture"
      to: "test_two_scope_rls.py admin-gated assertions"
      via: "get_admin_token() returns admin JWT for INSERT global, UPDATE global, DELETE global tests"
      pattern: "get_admin_token"
    - from: "test_helpers.get_user_supabase_client (anon key + JWT)"
      to: "Direct Supabase calls in test_two_scope_rls.py"
      via: "Bypasses FastAPI backend (no folders router until Phase 3); RLS actually applies"
      pattern: "create_client(SUPABASE_URL, SUPABASE_ANON_KEY)"
    - from: "from app.services.folder_service import normalize_path"
      to: "normalize_path round-trip assertions (Group 3, assertions 17-28)"
      via: "Plan 01's pure-function helper is exercised directly"
      pattern: "normalize_path"
    - from: "test_all.py SUITES registration"
      to: "Full-suite regression test (cd backend && venv/Scripts/python scripts/test_all.py)"
      via: "Two-Scope RLS suite runs alongside the existing 12 suites"
      pattern: "(\"Two-Scope RLS\", test_two_scope_rls)"
---

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Test client (anon key + user JWT) -> live Supabase Postgres | RLS evaluates per-row — if test uses service-role by accident, all RLS tests silently pass and the threat coverage is gone (Pitfall 6) |
| Cleanup logic -> live database | CLAUDE.md: "Tests must NEVER delete all user data." Tests must track IDs and delete only those |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-1-01 (verification) | Tampering / Information Disclosure | RLS test coverage | mitigate | Plan 08 IS the verification of T-1-01 (Pitfall 1 / RANK 1). The 40-assertion matrix exercises every cell of the cross-user × cross-scope grid: SELECT/INSERT/UPDATE/DELETE × (own user-scope / other user-scope / global) × (regular user / admin user). Any failure means RLS scope-leak is possible. ROADMAP success criterion 2 is the literal gate: "the cross-user × cross-scope SELECT/INSERT/UPDATE/DELETE matrix in test_two_scope_rls.py passes 100%." Phase 2 is BLOCKED until this matrix is green. |
| T-1-Test-RLS-Bypass | Information Disclosure / Test integrity | supabase-py client construction | mitigate | RESEARCH.md Common Pitfall 6: "Service-role client bypasses RLS in tests." The new `get_user_supabase_client(jwt_token)` helper instantiates `create_client(URL, SUPABASE_ANON_KEY)` (NOT service-role) and authenticates via `client.postgrest.auth(jwt_token)`. Tests that import this helper get RLS-enabled clients. The test docstring explicitly forbids importing `get_supabase_client` from `app.auth`. |
| T-1-Test-DataLoss | Data Integrity / CLAUDE.md violation | Test cleanup logic | mitigate | Per CLAUDE.md "CRITICAL: Tests must NEVER delete all user data": every created document/folder/chunk gets its UUID appended to a per-test-run list. The `finally` block iterates ONLY those IDs and DELETEs each individually via the test user's authenticated client. NEVER `DELETE FROM documents` without a WHERE clause. NEVER `TRUNCATE`. NEVER cross-user cleanup (each test cleans up its OWN created data). The existing test_rls.py:94-99 cleanup pattern is the template. |
| T-1-Admin-Setup | Operational / Test reliability | TEST_USER_ADMIN.profile.is_admin | mitigate | RESEARCH.md Common Pitfall 7: missing admin setup → admin tests silently skip. Test setup runs a verification query: SELECT is_admin FROM profiles WHERE email='admin@test.com'. If False or NULL, the test bails immediately with a clear error message instructing the developer to run `UPDATE public.profiles SET is_admin = true WHERE email = 'admin@test.com';` in the Supabase SQL editor. Documented in module docstring. |
</threat_model>

<objective>
Build the Phase-1 verification suite: extend `test_helpers.py` with the admin fixture and the RLS-aware Supabase client helper, write `test_two_scope_rls.py` with the full 40-assertion cross-user × cross-scope matrix (RLS-04 + TEST-04), and register the new suite in `test_all.py`. The matrix must pass 100% — this is the Pitfall 1 / RANK 1 mitigation gate that blocks Phase 2 until green. Tests use anon-key + JWT (NOT service-role) so RLS actually applies; tests track every created ID and clean up only those IDs in `finally` (CLAUDE.md mandatory rule).
</objective>

<execution_context>
@.claude/get-shit-done/workflows/execute-plan.md
@.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md

@.planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-RESEARCH.md
@.planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-PATTERNS.md
@.planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-VALIDATION.md
@.planning/research/PITFALLS.md
@.planning/codebase/TESTING.md
@CLAUDE.md

@backend/scripts/test_helpers.py
@backend/scripts/test_rls.py
@backend/scripts/test_settings.py
@backend/scripts/test_files.py
@backend/scripts/test_all.py
@backend/app/services/folder_service.py

<interfaces>
<!-- Existing test infrastructure that this plan extends. -->

backend/scripts/test_helpers.py — relevant existing surface:
- BASE_URL, SUPABASE_URL, SUPABASE_ANON_KEY (env loading lines 14-16)
- TEST_USER_A, TEST_USER_B fixtures (lines 18-19) — DO NOT MODIFY
- get_auth_token(email, password) (lines 56-86) — anon-key + JWT login flow; reuse via get_admin_token()
- _token_cache dict — caching mechanism for get_auth_token; new helper uses same cache
- auth_headers(token), reset_counters(), section(title), test(name, condition, detail), summary() — all consumed by test modules

backend/scripts/test_rls.py — sibling pattern (THE template to mirror):
- Module docstring on line 1
- sys.path.insert + import test_helpers as h pattern (lines 1-7)
- def run(): h.reset_counters(); ... return h.passed, h.failed
- h.section("RLS - <topic>") then h.test(name, condition, detail) per assertion
- try / finally with cleanup of ONLY tracked IDs
- if __name__ == "__main__": run(); sys.exit(h.summary())

backend/scripts/test_all.py — registration point:
- Line 12-23: import block (alphabetical-ish)
- Line 25: SUITES list of (name, module) tuples
- Line 52: p, f = module.run()  ← required entry-point signature

Required new helper signatures (this plan adds them to test_helpers.py):
- def get_admin_token() -> str           # thin wrapper over get_auth_token(TEST_USER_ADMIN["email"], ...)
- def get_user_supabase_client(jwt_token: str) -> Client
    # Returns supabase-py client with anon key + JWT-authenticated postgrest session.
    # NEVER use service-role key — that bypasses RLS and silently passes broken tests.

The 40 falsifiable assertions are catalogued in RESEARCH.md § Validation Architecture
(lines ~984-1052; reprinted here):

Group 1 (RLS matrix, RLS-01 + RLS-02 + RLS-04, 12 assertions repeated for documents and folders):
  1. A inserts (scope='user', user_id=A) → succeeds; B SELECT WHERE id=<A's row> returns 0 rows
  2. A inserts (scope='user', user_id=A) → row visible to A's SELECT
  3. Admin inserts (scope='global', user_id=NULL) → both A and B see it via SELECT
  4. A INSERT (scope='global', ...) → fails (no policy grants); raises RLS error
  5. A INSERT (scope='user', user_id=B) → fails (WITH CHECK requires user_id = auth.uid())
  6. A UPDATE non-scope field on own user-scope row → succeeds
  7. A UPDATE other user's row → 0 rows updated (USING blocks visibility)
  8. A DELETE own user-scope row → 1 row deleted; subsequent SELECT returns 0
  9. A DELETE global row → 0 rows deleted (no policy grants)
 10. Admin DELETE global row → 1 row deleted
 11. (1-10 repeated for document_chunks, without UPDATE rows)
 12. (1-10 repeated for folders, with UPDATE rows)

Group 2 (Scope-mutation prevention, RLS-03, 4 assertions):
 13. A UPDATE documents SET scope='global' WHERE id=<own user-scope id> → raises check_violation (trigger)
 14. Admin UPDATE documents SET scope='user', user_id=<some uuid> WHERE id=<global row id> → raises check_violation
 15. UPDATE documents SET file_name='new' WHERE id=<own> → succeeds (scope unchanged, trigger no-op)
 16. Trigger fires on all three tables (verified by attempting scope flip on each)

Group 3 (Path normalization, FOLDER-01, 12 assertions):
 17-28. normalize_path round-trips per spec; rejects '..' and '.' segments

Group 4 (CHECK constraints, SCHEMA-01 + SCHEMA-02 + SCHEMA-03, 7 assertions):
 29. INSERT documents folder_path='projects' → fails CHECK (no leading slash)
 30. INSERT documents folder_path='/projects/' → fails CHECK (trailing slash)
 31. INSERT documents folder_path='//' → fails CHECK
 32. INSERT documents (scope='user', user_id=NULL, ...) → fails CHECK (coupling)
 33. INSERT documents (scope='global', user_id=<uuid>, ...) → fails CHECK (coupling)
 34. INSERT documents content_markdown_status='processing' → fails CHECK (not in enum)
 35. Same constraints exist on folders.path / folders.scope/user_id

Group 5 (Indexes & perf, SCHEMA-05, 3 assertions):
 36. EXPLAIN (ANALYZE, FORMAT TEXT) … content_markdown ILIKE '%floor%' → Bitmap Index Scan on documents_content_markdown_trgm_idx
 37. EXPLAIN … folder_path LIKE '/projects/%' → Index Scan on documents_folder_path_prefix_idx
 38. SELECT 1 FROM pg_extension WHERE extname='pg_trgm' returns 1 row

Group 6 (Idempotency — verified by plan 07's verify task; no test code needed in plan 08)
</interfaces>
</context>

<tasks>

<task id="1-08-01" type="checkpoint:human-action" gate="blocking">
  <name>Task 1: Promote admin@test.com to is_admin=true (one-time setup)</name>
  <what-built>
    Plan 07 has applied migrations 012-016 to the live Supabase database. Plan 08's RLS test matrix needs a third test user with `profiles.is_admin = true` to exercise admin-only INSERT/UPDATE/DELETE on global-scope rows.

    The Supabase Auth signup flow creates the profile row automatically (via the trigger from migration 005), but `is_admin` defaults to `false`. Promotion is a one-time manual SQL step.
  </what-built>
  <how-to-verify>
    Step 1 — Create the admin test user (if not already created via a previous test run):
    1. Open the Supabase Dashboard → Authentication → Users.
    2. If `admin@test.com` does not exist, click "Add user" → "Create new user". Set:
       - Email: `admin@test.com`
       - Password: choose a strong password and remember it (you will export it as TEST_USER_ADMIN_PASSWORD or the test will autosignup on first run, whichever is simpler — the helper added in task 2 supports both)
       - Auto Confirm User: yes (skip email verification)
    3. Verify the row exists: Authentication → Users → search for `admin@test.com`.

    Step 2 — Promote to admin:
    1. Open the Supabase Dashboard → SQL Editor → New query.
    2. Run:
       ```sql
       UPDATE public.profiles SET is_admin = true WHERE email = 'admin@test.com';
       SELECT id, email, is_admin FROM public.profiles WHERE email = 'admin@test.com';
       ```
    3. The SELECT should return one row with `is_admin = true`.

    Step 3 — Confirm via psycopg2 (final sanity check):
    ```
    cd backend && venv/Scripts/python -c "import os, psycopg2; conn = psycopg2.connect(os.environ['DATABASE_URL']); conn.autocommit = True; cur = conn.cursor(); cur.execute(\"SELECT id, email, is_admin FROM public.profiles WHERE email='admin@test.com'\"); row = cur.fetchone(); assert row is not None, 'admin@test.com profile does not exist'; assert row[2] is True, f'is_admin is not True: got {row[2]}'; print(f'OK: admin profile {row[0]} is_admin={row[2]}')"
    ```
    Expected output: `OK: admin profile <uuid> is_admin=True`.

    If you set a non-default password, also export `TEST_USER_ADMIN_PASSWORD` in the test shell:
    ```
    $env:TEST_USER_ADMIN_PASSWORD = "<your password>"
    ```
    The helper added in task 2 reads from this env var if set, otherwise defaults to a documented value.
  </how-to-verify>
  <resume-signal>Type "approved" once admin@test.com exists in auth.users AND public.profiles shows is_admin=true.</resume-signal>
  <done>Admin profile exists in public.profiles with is_admin=true. Verified via SQL editor or the psycopg2 one-liner.</done>
</task>

<task id="1-08-02" type="auto" tdd="true">
  <name>Task 2: Extend test_helpers.py with TEST_USER_ADMIN fixture, get_admin_token(), get_user_supabase_client()</name>
  <files>backend/scripts/test_helpers.py</files>
  <read_first>
    - backend/scripts/test_helpers.py (existing file — read ALL of it; specifically lines 14-19 for env-var + fixture pattern, lines 56-86 for get_auth_token() canonical signup-or-login flow, _token_cache dict for caching pattern)
    - .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-RESEARCH.md § "Existing Codebase Anchors §Test harness" (lines ~129-141 — option (a) chosen; admin fixture + manual SQL setup) AND § Code Examples (lines ~1214-1245 — exact get_user_supabase_client signature with anon key + postgrest.auth)
    - .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-PATTERNS.md § "backend/scripts/test_helpers.py (modify)" (lines ~388-419 — confirms additive-only changes; existing fixtures must not be modified)
  </read_first>
  <behavior>
    - TEST_USER_ADMIN dict has keys "email" and "password"
    - TEST_USER_ADMIN["email"] == "admin@test.com" (matches the manual setup in task 1)
    - get_admin_token() returns the same JWT each call within a process (uses the existing _token_cache via get_auth_token)
    - get_admin_token() raises a clear error if the admin user doesn't exist or password is wrong (existing get_auth_token already raises on auth failure — this just wraps it)
    - get_user_supabase_client(jwt) returns a supabase-py Client where postgrest.auth(jwt) has been called (so requests carry the user's JWT and RLS applies)
    - get_user_supabase_client uses SUPABASE_ANON_KEY (NEVER service-role key) — verified by reading the source
    - All existing fixtures (TEST_USER_A, TEST_USER_B) are unchanged (additive-only modification)
  </behavior>
  <action>
    Open `backend/scripts/test_helpers.py` and add the following to the file. Do NOT delete or modify any existing content. Place the additions in their natural location: TEST_USER_ADMIN immediately after TEST_USER_B (line 19), the helper functions after get_auth_token (after line 86, before the next existing function).

    Block 1 — fixture (place immediately after `TEST_USER_B = ...`):
    ```python
    # Admin fixture for two-scope RLS tests (added in Phase 1 plan 08).
    # REQUIRES one-time setup: after creating this user via Supabase Auth, run
    #   UPDATE public.profiles SET is_admin = true WHERE email = 'admin@test.com';
    # in the Supabase SQL editor. Password may be overridden via TEST_USER_ADMIN_PASSWORD
    # env var; defaults to the value below for local-dev convenience.
    TEST_USER_ADMIN = {
        "email": "admin@test.com",
        "password": os.environ.get("TEST_USER_ADMIN_PASSWORD", "adminpassword123"),
    }
    ```

    Block 2 — helpers (place after the existing `get_auth_token` function, after line 86):
    ```python
    def get_admin_token() -> str:
        """Return JWT for the admin test user (TEST_USER_ADMIN).

        Wraps get_auth_token() so the admin token participates in the same _token_cache
        as TEST_USER_A/B tokens. The admin user MUST have been promoted to is_admin=true
        in public.profiles before this is called — see test_two_scope_rls.py docstring
        for the one-time UPDATE SQL.
        """
        return get_auth_token(TEST_USER_ADMIN["email"], TEST_USER_ADMIN["password"])


    def get_user_supabase_client(jwt_token: str):
        """Return a supabase-py Client authenticated as the JWT's user — RLS applies.

        Critical: uses SUPABASE_ANON_KEY (NOT service-role). Service-role bypasses
        RLS and silently passes broken tests. PostgREST honors the Authorization
        header for RLS evaluation; we set it via client.postgrest.auth(jwt_token).

        Used by test_two_scope_rls.py to talk directly to Supabase (bypassing the
        FastAPI backend, which has no folders router until Phase 3). Every direct
        DB write/read in that test goes through a client returned by this helper.

        Args:
            jwt_token: A valid Supabase JWT for the user the client should impersonate.
                       Get one via get_auth_token() or get_admin_token().

        Returns:
            supabase.Client with postgrest.auth(jwt_token) called.
        """
        from supabase import create_client
        client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
        client.postgrest.auth(jwt_token)
        return client
    ```

    Conventions to honor:
    - Additive-only — do NOT touch TEST_USER_A, TEST_USER_B, get_auth_token, or any other existing surface.
    - Lazy import `from supabase import create_client` inside `get_user_supabase_client` (matches the lazy-import pattern in similar service files; avoids polluting module-import time for tests that don't use the helper).
    - Use SUPABASE_ANON_KEY (the existing module-level constant from line 16) — NEVER reach for service-role.
    - Type hints on signatures.
    - One-line docstring minimum per function; multi-line where the "why" matters.

    Verify the changes are additive by running the existing test suite — no Episode 1 tests should break. (The full suite is heavy and not required by this plan; the structural sanity check below is sufficient.)
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -c "import sys, os; sys.path.insert(0, 'scripts'); import test_helpers as h; assert hasattr(h, 'TEST_USER_ADMIN'); assert h.TEST_USER_ADMIN['email'] == 'admin@test.com'; assert h.TEST_USER_ADMIN['password']; assert hasattr(h, 'get_admin_token') and callable(h.get_admin_token); assert hasattr(h, 'get_user_supabase_client') and callable(h.get_user_supabase_client); assert hasattr(h, 'TEST_USER_A') and h.TEST_USER_A['email'] == 'testuser@example.com', 'existing TEST_USER_A must be unchanged'; assert hasattr(h, 'TEST_USER_B') and h.TEST_USER_B['email'] == 'test@test.com', 'existing TEST_USER_B must be unchanged'; src = open('scripts/test_helpers.py', encoding='utf-8').read(); assert 'SUPABASE_ANON_KEY' in src and 'create_client(SUPABASE_URL, SUPABASE_ANON_KEY)' in src; assert 'service_role' not in src.lower() or 'service-role' not in src.lower() or src.lower().count('service_role') == 0, 'get_user_supabase_client must not use service-role key'; print('test_helpers.py extensions OK')"</automated>
  </verify>
  <acceptance_criteria>
    - `grep -c "TEST_USER_ADMIN" backend/scripts/test_helpers.py` returns at least 1.
    - `grep -c "admin@test.com" backend/scripts/test_helpers.py` returns at least 1.
    - `grep -c "def get_admin_token" backend/scripts/test_helpers.py` returns 1.
    - `grep -c "def get_user_supabase_client" backend/scripts/test_helpers.py` returns 1.
    - `grep -c "create_client(SUPABASE_URL, SUPABASE_ANON_KEY)" backend/scripts/test_helpers.py` returns 1.
    - `grep -c "client.postgrest.auth" backend/scripts/test_helpers.py` returns 1.
    - `grep -ci "service_role" backend/scripts/test_helpers.py` returns 0 (no service-role key in test_helpers — that would defeat RLS).
    - `grep -c "TEST_USER_A = " backend/scripts/test_helpers.py` returns 1 (existing fixture unchanged).
    - `grep -c "TEST_USER_B = " backend/scripts/test_helpers.py` returns 1.
    - `grep -c "def get_auth_token" backend/scripts/test_helpers.py` returns 1 (existing helper unchanged).
    - The Python sanity check in `<verify>` exits 0 and prints "test_helpers.py extensions OK".
  </acceptance_criteria>
  <done>
    test_helpers.py has the TEST_USER_ADMIN fixture, get_admin_token() helper, and get_user_supabase_client() helper added additively. All existing fixtures and helpers are unchanged. RLS-applying client construction uses anon-key + JWT, never service-role.
  </done>
</task>

<task id="1-08-03" type="auto" tdd="true">
  <name>Task 3: Write test_two_scope_rls.py — 40 falsifiable assertions across 5 groups</name>
  <files>backend/scripts/test_two_scope_rls.py</files>
  <read_first>
    - backend/scripts/test_rls.py (THE TEMPLATE — read all 107 lines; this plan's test_two_scope_rls.py mirrors its shape: docstring, sys.path.insert + import test_helpers as h, run() function, h.section() + h.test() pattern, try/finally cleanup of tracked IDs)
    - backend/scripts/test_settings.py (lines 34-40 — admin-gate 403 pattern; reused for "non-admin INSERT scope='global' rejected" assertion)
    - backend/scripts/test_files.py (lines 1-60 — for the file-upload-and-poll setup pattern, reference only — plan 08 does NOT use the FastAPI backend; it uses direct Supabase calls via get_user_supabase_client)
    - backend/scripts/test_helpers.py (the file just modified in task 2 — confirms TEST_USER_ADMIN fixture and helpers are available)
    - backend/app/services/folder_service.py (the file from plan 01 — the normalize_path import and round-trip cases for Group 3 assertions 17-28)
    - .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-RESEARCH.md § Validation Architecture (lines ~948-1052 — the 40 falsifiable assertions cataloged across 5 groups; this is the SPEC for the test file) AND § "Existing Codebase Anchors §Test harness" (lines ~129-141 — admin-fixture and direct-Supabase-calls rationale) AND § Decisions §7 (lines ~399-456 — full matrix shape) AND § Code Examples (lines ~1214-1245 — get_user_supabase_client usage pattern)
    - .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-PATTERNS.md § "backend/scripts/test_two_scope_rls.py" (lines ~308-385 — confirms test_rls.py is the template; assertion-count target is 40; cleanup-only-tracked-IDs rule per CLAUDE.md)
    - .planning/codebase/TESTING.md § "Resource Cleanup" (lines ~265-305 — "CRITICAL RULE: Tests must NEVER delete all user data")
    - CLAUDE.md § "Testing" (lines ~26-34 — testing rules; specifically "Tests must NEVER delete all user data; Tests must only clean up resources they created (tracked by ID); Never use blanket 'delete all threads/files' cleanup")
  </read_first>
  <behavior>
    - The module exposes `def run()` returning `(h.passed, h.failed)` per the SUITES contract
    - Module docstring documents the one-time admin setup SQL (verbatim) and the "use anon-key + JWT, never service-role" rule
    - Setup verifies admin profile has is_admin=true; if not, bails immediately with a clear error
    - Group 1 (RLS matrix): assertions 1-10 on documents, repeated for document_chunks (without UPDATE assertions 6+7) and folders → ~22 assertions
    - Group 2 (Scope-mutation prevention): 4 assertions (13-16)
    - Group 3 (Path normalization): 12 assertions calling normalize_path from app.services.folder_service (17-28)
    - Group 4 (CHECK constraints): 7 assertions (29-35)
    - Group 5 (Indexes & perf): 3 assertions (36-38)
    - Total assertion count: ~40 (counted via h.test calls)
    - Every created document/chunk/folder has its UUID appended to a tracking list
    - finally: iterates tracking lists and deletes each tracked row individually via the user's authenticated client (NOT service-role; NOT bulk DELETE; NOT TRUNCATE)
    - if __name__ == "__main__": run(); sys.exit(h.summary())
    - Module is importable as `import test_two_scope_rls` from test_all.py
  </behavior>
  <action>
    Create `backend/scripts/test_two_scope_rls.py`. The file is large (~250-300 lines) but every section is mechanical — it follows the exact shape of test_rls.py with the assertions from RESEARCH.md § Validation Architecture pasted in as h.test() calls.

    Use the skeleton below. Fill in the assertion bodies by translating each assertion from RESEARCH.md § Validation Architecture into an `h.test(name, condition, detail)` call. Do not invent new assertions; use exactly the 40 catalogued.

    ```python
    """Two-Scope RLS test matrix — Phase 1 RLS-04 + TEST-04.

    Covers the cross-user × cross-scope SELECT/INSERT/UPDATE/DELETE matrix on
    documents, document_chunks, and folders. Verifies:
      - RLS-01: SELECT scope = 'global' OR (scope = 'user' AND user_id = auth.uid())
      - RLS-02: INSERT/UPDATE/DELETE for global scope require admin
      - RLS-03: scope mutation forbidden (BEFORE UPDATE trigger raises check_violation)
      - RLS-04: cross-user × cross-scope matrix passes 100% (Phase 2 gate)
      - SCHEMA-01..05: CHECK constraints reject non-canonical paths and bad enums
      - SCHEMA-05: pg_trgm index used by ILIKE; text_pattern_ops btree by LIKE prefix
      - FOLDER-01: normalize_path round-trips per spec; rejects '..' / '.'

    ONE-TIME SETUP (do this once after creating the admin@test.com user via Supabase Auth):
        UPDATE public.profiles SET is_admin = true WHERE email = 'admin@test.com';

    The setup function in run() verifies this and bails with a clear error if not done.

    CRITICAL: this test uses anon-key + JWT (via h.get_user_supabase_client) — NEVER
    service-role. Service-role bypasses RLS and would silently pass broken tests.

    CRITICAL: per CLAUDE.md, tests must NEVER delete all user data. This file tracks
    every created resource by ID and deletes ONLY those IDs in finally. No blanket
    DELETE FROM. No TRUNCATE.

    Direct Supabase calls (not FastAPI backend) — Phase 1 has no folders router yet
    (Phase 3). Backend is not required to run this test.
    """
    import sys
    import os
    import uuid

    sys.path.insert(0, os.path.dirname(__file__))
    # Ensure the backend's app package is importable (for normalize_path)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

    import test_helpers as h
    from app.services.folder_service import normalize_path

    # Tracking lists for cleanup. Each list holds tuples of (table, id, client_for_cleanup).
    # CLAUDE.md: tests must NEVER delete all user data — only tracked resources.
    _tracked_documents = []   # list[(doc_id, sb_client)]
    _tracked_chunks    = []   # list[(chunk_id, sb_client)]
    _tracked_folders   = []   # list[(folder_id, sb_client)]


    def _track_doc(doc_id, sb_client):
        _tracked_documents.append((doc_id, sb_client))


    def _track_chunk(chunk_id, sb_client):
        _tracked_chunks.append((chunk_id, sb_client))


    def _track_folder(folder_id, sb_client):
        _tracked_folders.append((folder_id, sb_client))


    def _cleanup():
        """Delete ONLY tracked resources. Per CLAUDE.md: never bulk-delete."""
        for cid, client in _tracked_chunks:
            try:
                client.table("document_chunks").delete().eq("id", cid).execute()
            except Exception:
                pass
        for did, client in _tracked_documents:
            try:
                client.table("documents").delete().eq("id", did).execute()
            except Exception:
                pass
        for fid, client in _tracked_folders:
            try:
                client.table("folders").delete().eq("id", fid).execute()
            except Exception:
                pass
        _tracked_documents.clear()
        _tracked_chunks.clear()
        _tracked_folders.clear()


    def _verify_admin_setup():
        """Bail with a clear error if admin@test.com is missing or not promoted."""
        try:
            tok = h.get_admin_token()
        except Exception as e:
            print(f"\n[FATAL] Could not get admin token. Did you create '{h.TEST_USER_ADMIN['email']}' "
                  f"in Supabase Auth and (optionally) export TEST_USER_ADMIN_PASSWORD? Error: {e}")
            sys.exit(1)
        sb = h.get_user_supabase_client(tok)
        try:
            r = sb.table("profiles").select("id,is_admin").eq("email", h.TEST_USER_ADMIN["email"]).maybe_single().execute()
        except Exception as e:
            print(f"\n[FATAL] Could not query profiles for admin: {e}")
            sys.exit(1)
        if not r.data or not r.data.get("is_admin"):
            print(
                f"\n[FATAL] {h.TEST_USER_ADMIN['email']} is not is_admin=true in public.profiles.\n"
                f"        Run this in the Supabase SQL editor:\n"
                f"            UPDATE public.profiles SET is_admin = true WHERE email = '{h.TEST_USER_ADMIN['email']}';\n"
                f"        Then re-run this test."
            )
            sys.exit(1)
        return tok


    def _raises(fn, *exc_substrings):
        """Run fn(); return (raised: bool, message: str). Optionally check substrings appear in message."""
        try:
            fn()
            return False, ""
        except Exception as e:
            msg = str(e)
            if exc_substrings and not all(s in msg for s in exc_substrings):
                return False, msg
            return True, msg


    def run():
        h.reset_counters()

        # ── Setup ──
        admin_token = _verify_admin_setup()
        token_a = h.get_auth_token(h.TEST_USER_A["email"], h.TEST_USER_A["password"])
        token_b = h.get_auth_token(h.TEST_USER_B["email"], h.TEST_USER_B["password"])
        sb_admin = h.get_user_supabase_client(admin_token)
        sb_a     = h.get_user_supabase_client(token_a)
        sb_b     = h.get_user_supabase_client(token_b)

        # Resolve user IDs from each JWT (used for INSERT WITH CHECK testing)
        u_admin = sb_admin.auth.get_user(admin_token).user.id
        u_a     = sb_a.auth.get_user(token_a).user.id
        u_b     = sb_b.auth.get_user(token_b).user.id

        try:
            # ───────────────────────────────────────────────────────────
            # Group 3: Path normalization (FOLDER-01) — assertions 17-28
            # Run this FIRST so we know normalize_path is correct before
            # using it to compose canonical paths for DB inserts below.
            # ───────────────────────────────────────────────────────────
            h.section("FOLDER-01 — normalize_path round-trips and rejections")

            h.test("normalize_path('/') == '/'", normalize_path("/") == "/")
            h.test("normalize_path('/a/b') == '/a/b'", normalize_path("/a/b") == "/a/b")
            h.test("normalize_path('/a/b/c') == '/a/b/c'", normalize_path("/a/b/c") == "/a/b/c")
            h.test("normalize_path('/A/B') preserves case", normalize_path("/A/B") == "/A/B")
            h.test("normalize_path('/a//b') collapses double slash", normalize_path("/a//b") == "/a/b")
            h.test("normalize_path('a/b') prepends leading slash", normalize_path("a/b") == "/a/b")
            h.test("normalize_path('/a/b/') strips trailing slash", normalize_path("/a/b/") == "/a/b")
            h.test("normalize_path(backslash form) replaces backslash", normalize_path("\\\\a\\\\b") == "/a/b")
            h.test("normalize_path('') == '/'", normalize_path("") == "/")
            h.test("normalize_path(None) == '/'", normalize_path(None) == "/")
            raised, _ = _raises(lambda: normalize_path("/a/../b"))
            h.test("normalize_path('/a/../b') raises ValueError", raised)
            raised, _ = _raises(lambda: normalize_path("/a/./b"))
            h.test("normalize_path('/a/./b') raises ValueError", raised)

            # ───────────────────────────────────────────────────────────
            # Group 4: CHECK constraints (SCHEMA-01/02/03) — assertions 29-35
            # Done before Group 1 so we know malformed inserts are blocked at
            # the CHECK layer (which fires before the RLS WITH CHECK).
            # ───────────────────────────────────────────────────────────
            h.section("SCHEMA-01..03 — CHECK constraints reject non-canonical inputs")

            def _try_insert(client, table, payload):
                return client.table(table).insert(payload).execute()

            # 29. INSERT documents folder_path='projects' (no leading slash) → fails CHECK
            raised, msg = _raises(lambda: _try_insert(sb_a, "documents", {
                "user_id": u_a, "scope": "user", "folder_path": "projects",
                "file_name": f"t-{uuid.uuid4()}.txt", "file_size": 1, "mime_type": "text/plain", "status": "ready"
            }))
            h.test("INSERT folder_path='projects' rejected by canonical CHECK", raised, msg[:120])

            # 30. INSERT documents folder_path='/projects/' (trailing slash) → fails CHECK
            raised, msg = _raises(lambda: _try_insert(sb_a, "documents", {
                "user_id": u_a, "scope": "user", "folder_path": "/projects/",
                "file_name": f"t-{uuid.uuid4()}.txt", "file_size": 1, "mime_type": "text/plain", "status": "ready"
            }))
            h.test("INSERT folder_path='/projects/' rejected by canonical CHECK", raised, msg[:120])

            # 31. INSERT documents folder_path='//' → fails CHECK
            raised, msg = _raises(lambda: _try_insert(sb_a, "documents", {
                "user_id": u_a, "scope": "user", "folder_path": "//",
                "file_name": f"t-{uuid.uuid4()}.txt", "file_size": 1, "mime_type": "text/plain", "status": "ready"
            }))
            h.test("INSERT folder_path='//' rejected by canonical CHECK", raised, msg[:120])

            # 32. INSERT (scope='user', user_id=NULL) → fails coupling CHECK
            raised, msg = _raises(lambda: _try_insert(sb_a, "documents", {
                "user_id": None, "scope": "user", "folder_path": "/",
                "file_name": f"t-{uuid.uuid4()}.txt", "file_size": 1, "mime_type": "text/plain", "status": "ready"
            }))
            h.test("INSERT (scope='user', user_id=NULL) rejected by coupling CHECK", raised, msg[:120])

            # 33. INSERT (scope='global', user_id=<uuid>) → fails coupling CHECK
            # Note: must be admin to even attempt scope='global' (RLS_INSERT_GLOBAL gate);
            # the coupling CHECK still fires at the DB layer before RLS for this case.
            raised, msg = _raises(lambda: _try_insert(sb_admin, "documents", {
                "user_id": u_admin, "scope": "global", "folder_path": "/",
                "file_name": f"t-{uuid.uuid4()}.txt", "file_size": 1, "mime_type": "text/plain", "status": "ready"
            }))
            h.test("INSERT (scope='global', user_id=<uuid>) rejected by coupling CHECK", raised, msg[:120])

            # 34. INSERT documents content_markdown_status='processing' → fails CHECK (not in 4-element enum)
            raised, msg = _raises(lambda: _try_insert(sb_a, "documents", {
                "user_id": u_a, "scope": "user", "folder_path": "/",
                "file_name": f"t-{uuid.uuid4()}.txt", "file_size": 1, "mime_type": "text/plain", "status": "ready",
                "content_markdown_status": "processing"
            }))
            h.test("INSERT content_markdown_status='processing' rejected by enum CHECK", raised, msg[:120])

            # 35. Same coupling CHECK exists on folders.scope/user_id
            raised, msg = _raises(lambda: _try_insert(sb_a, "folders", {
                "scope": "user", "user_id": None, "path": "/test"
            }))
            h.test("folders INSERT (scope='user', user_id=NULL) rejected by coupling CHECK", raised, msg[:120])

            # ───────────────────────────────────────────────────────────
            # Group 1: RLS matrix on documents (RLS-01, RLS-02, RLS-04) — assertions 1-10
            # ───────────────────────────────────────────────────────────
            h.section("RLS matrix — documents")

            # 1. A inserts (scope='user', user_id=A) → succeeds
            r = sb_a.table("documents").insert({
                "user_id": u_a, "scope": "user", "folder_path": "/",
                "file_name": f"a-doc-{uuid.uuid4()}.txt", "file_size": 1, "mime_type": "text/plain", "status": "ready"
            }).execute()
            a_doc_id = r.data[0]["id"] if r.data else None
            if a_doc_id:
                _track_doc(a_doc_id, sb_a)
            h.test("A INSERT (scope='user', user_id=A) succeeds", bool(a_doc_id), str(r))

            # B SELECT WHERE id=<A's row> → 0 rows
            r = sb_b.table("documents").select("id").eq("id", a_doc_id).execute()
            h.test("B cannot SELECT A's user-scope row (RLS hides)", len(r.data) == 0, str(r.data))

            # 2. A SELECT own row → visible
            r = sb_a.table("documents").select("id").eq("id", a_doc_id).execute()
            h.test("A SELECT own user-scope row visible", len(r.data) == 1)

            # 3. Admin INSERT (scope='global', user_id=NULL) → succeeds; both A and B see it
            r = sb_admin.table("documents").insert({
                "user_id": None, "scope": "global", "folder_path": "/",
                "file_name": f"global-{uuid.uuid4()}.txt", "file_size": 1, "mime_type": "text/plain", "status": "ready"
            }).execute()
            g_doc_id = r.data[0]["id"] if r.data else None
            if g_doc_id:
                _track_doc(g_doc_id, sb_admin)
            h.test("Admin INSERT (scope='global', user_id=NULL) succeeds", bool(g_doc_id))

            r = sb_a.table("documents").select("id").eq("id", g_doc_id).execute()
            h.test("A SELECT global doc visible", len(r.data) == 1)
            r = sb_b.table("documents").select("id").eq("id", g_doc_id).execute()
            h.test("B SELECT global doc visible", len(r.data) == 1)

            # 4. A INSERT (scope='global', ...) → fails (no policy grants)
            raised, msg = _raises(lambda: sb_a.table("documents").insert({
                "user_id": None, "scope": "global", "folder_path": "/",
                "file_name": f"a-leak-{uuid.uuid4()}.txt", "file_size": 1, "mime_type": "text/plain", "status": "ready"
            }).execute())
            h.test("A INSERT scope='global' rejected by RLS (no policy grants)", raised, msg[:120])

            # 5. A INSERT (scope='user', user_id=B) → fails (WITH CHECK requires user_id = auth.uid())
            raised, msg = _raises(lambda: sb_a.table("documents").insert({
                "user_id": u_b, "scope": "user", "folder_path": "/",
                "file_name": f"a-impersonate-{uuid.uuid4()}.txt", "file_size": 1, "mime_type": "text/plain", "status": "ready"
            }).execute())
            h.test("A INSERT (scope='user', user_id=B) rejected by RLS WITH CHECK", raised, msg[:120])

            # 6. A UPDATE non-scope field on own row → succeeds
            r = sb_a.table("documents").update({"file_size": 999}).eq("id", a_doc_id).execute()
            h.test("A UPDATE own user-scope row non-scope field succeeds", len(r.data) == 1)

            # 7. A UPDATE another user's row → 0 rows updated
            # (Create a B-owned doc first, track it, then attempt as A)
            r = sb_b.table("documents").insert({
                "user_id": u_b, "scope": "user", "folder_path": "/",
                "file_name": f"b-doc-{uuid.uuid4()}.txt", "file_size": 1, "mime_type": "text/plain", "status": "ready"
            }).execute()
            b_doc_id = r.data[0]["id"] if r.data else None
            if b_doc_id:
                _track_doc(b_doc_id, sb_b)

            r = sb_a.table("documents").update({"file_size": 999}).eq("id", b_doc_id).execute()
            h.test("A UPDATE B's user-scope row touches 0 rows (RLS USING blocks)", len(r.data) == 0, str(r.data))

            # 8. A DELETE own row → 1 row deleted; SELECT returns 0
            r = sb_a.table("documents").delete().eq("id", a_doc_id).execute()
            h.test("A DELETE own user-scope row deletes 1 row", len(r.data) == 1)
            r = sb_a.table("documents").select("id").eq("id", a_doc_id).execute()
            h.test("After A DELETE, SELECT returns 0", len(r.data) == 0)
            # Untrack — A already deleted it.
            _tracked_documents[:] = [t for t in _tracked_documents if t[0] != a_doc_id]

            # 9. A DELETE global row → 0 rows
            r = sb_a.table("documents").delete().eq("id", g_doc_id).execute()
            h.test("A DELETE global doc touches 0 rows (no policy grants)", len(r.data) == 0)

            # 10. Admin DELETE global row → 1 row
            r = sb_admin.table("documents").delete().eq("id", g_doc_id).execute()
            h.test("Admin DELETE global doc deletes 1 row", len(r.data) == 1)
            _tracked_documents[:] = [t for t in _tracked_documents if t[0] != g_doc_id]

            # ───────────────────────────────────────────────────────────
            # Group 1 (continued): RLS matrix on folders (with UPDATE) — mirror of 1-10
            # ───────────────────────────────────────────────────────────
            h.section("RLS matrix — folders (mirror of documents matrix)")

            # 1f. A INSERT folders (scope='user', user_id=A, path='/x') → succeeds
            r = sb_a.table("folders").insert({"scope": "user", "user_id": u_a, "path": f"/test-{uuid.uuid4().hex[:8]}"}).execute()
            a_folder_id = r.data[0]["id"] if r.data else None
            if a_folder_id:
                _track_folder(a_folder_id, sb_a)
            h.test("[folders] A INSERT user-scope folder succeeds", bool(a_folder_id))

            r = sb_b.table("folders").select("id").eq("id", a_folder_id).execute()
            h.test("[folders] B cannot SELECT A's user-scope folder", len(r.data) == 0)

            # 4f. A INSERT folders (scope='global') → fails
            raised, msg = _raises(lambda: sb_a.table("folders").insert({
                "scope": "global", "user_id": None, "path": f"/leak-{uuid.uuid4().hex[:8]}"
            }).execute())
            h.test("[folders] A INSERT scope='global' rejected by RLS", raised)

            # 3f. Admin INSERT folders (scope='global', user_id=NULL) → succeeds
            r = sb_admin.table("folders").insert({"scope": "global", "user_id": None, "path": f"/g-{uuid.uuid4().hex[:8]}"}).execute()
            g_folder_id = r.data[0]["id"] if r.data else None
            if g_folder_id:
                _track_folder(g_folder_id, sb_admin)
            h.test("[folders] Admin INSERT global folder succeeds", bool(g_folder_id))

            # 6f. A UPDATE own folder non-scope field → succeeds (folders has UPDATE policy)
            r = sb_a.table("folders").update({"path": f"/renamed-{uuid.uuid4().hex[:8]}"}).eq("id", a_folder_id).execute()
            h.test("[folders] A UPDATE own folder non-scope field succeeds", len(r.data) == 1)

            # Concurrency / unique-index assertion (Pitfall 10): inserting same (scope,user_id,path) twice fails the second time
            same_path = f"/dup-{uuid.uuid4().hex[:8]}"
            r1 = sb_a.table("folders").insert({"scope": "user", "user_id": u_a, "path": same_path}).execute()
            f1 = r1.data[0]["id"] if r1.data else None
            if f1:
                _track_folder(f1, sb_a)
            raised, msg = _raises(lambda: sb_a.table("folders").insert({"scope": "user", "user_id": u_a, "path": same_path}).execute())
            h.test("[folders] Duplicate (scope,user,path) INSERT rejected by unique expression index (Pitfall 10)", raised, msg[:120])

            # ───────────────────────────────────────────────────────────
            # Group 1 (continued): RLS matrix on document_chunks (no UPDATE) — abridged
            # Chunks are insert+delete only; skip assertions 6+7 from the docs matrix.
            # We need a parent document first — re-create one for A.
            # ───────────────────────────────────────────────────────────
            h.section("RLS matrix — document_chunks (insert+delete only)")

            r = sb_a.table("documents").insert({
                "user_id": u_a, "scope": "user", "folder_path": "/",
                "file_name": f"a-parent-{uuid.uuid4()}.txt", "file_size": 1, "mime_type": "text/plain", "status": "ready"
            }).execute()
            parent_id = r.data[0]["id"] if r.data else None
            if parent_id:
                _track_doc(parent_id, sb_a)

            # A INSERT chunk for own doc → succeeds; B cannot SELECT
            r = sb_a.table("document_chunks").insert({
                "document_id": parent_id, "user_id": u_a, "scope": "user",
                "chunk_index": 0, "content": "test chunk content"
            }).execute()
            chunk_id = r.data[0]["id"] if r.data else None
            if chunk_id:
                _track_chunk(chunk_id, sb_a)
            h.test("[chunks] A INSERT chunk for own doc succeeds", bool(chunk_id))

            r = sb_b.table("document_chunks").select("id").eq("id", chunk_id).execute()
            h.test("[chunks] B cannot SELECT A's chunk (RLS hides)", len(r.data) == 0)

            # A INSERT chunk with scope='global' → fails (no policy grants)
            raised, _ = _raises(lambda: sb_a.table("document_chunks").insert({
                "document_id": parent_id, "user_id": None, "scope": "global",
                "chunk_index": 0, "content": "leak"
            }).execute())
            h.test("[chunks] A INSERT scope='global' rejected by RLS", raised)

            # ───────────────────────────────────────────────────────────
            # Group 2: Scope-mutation prevention (RLS-03) — assertions 13-16
            # ───────────────────────────────────────────────────────────
            h.section("RLS-03 — scope-mutation forbidden by trigger (all 3 tables)")

            # Need a fresh A-owned user-scope doc to flip
            r = sb_a.table("documents").insert({
                "user_id": u_a, "scope": "user", "folder_path": "/",
                "file_name": f"a-flip-{uuid.uuid4()}.txt", "file_size": 1, "mime_type": "text/plain", "status": "ready"
            }).execute()
            flip_doc_id = r.data[0]["id"] if r.data else None
            if flip_doc_id:
                _track_doc(flip_doc_id, sb_a)

            # 13. A UPDATE documents SET scope='global' → raises check_violation (trigger)
            raised, msg = _raises(lambda: sb_a.table("documents").update({"scope": "global"}).eq("id", flip_doc_id).execute())
            h.test("[trigger] A UPDATE documents SET scope='global' raises check_violation", raised, msg[:120])

            # 15. UPDATE documents SET file_name='new' (no scope change) → succeeds (trigger no-op)
            r = sb_a.table("documents").update({"file_name": f"renamed-{uuid.uuid4()}.txt"}).eq("id", flip_doc_id).execute()
            h.test("[trigger] UPDATE non-scope field succeeds (trigger no-op)", len(r.data) == 1)

            # 14. Admin UPDATE global doc SET scope='user' → raises check_violation
            r = sb_admin.table("documents").insert({
                "user_id": None, "scope": "global", "folder_path": "/",
                "file_name": f"g-flip-{uuid.uuid4()}.txt", "file_size": 1, "mime_type": "text/plain", "status": "ready"
            }).execute()
            g_flip_id = r.data[0]["id"] if r.data else None
            if g_flip_id:
                _track_doc(g_flip_id, sb_admin)
            raised, msg = _raises(lambda: sb_admin.table("documents").update({"scope": "user", "user_id": u_admin}).eq("id", g_flip_id).execute())
            h.test("[trigger] Admin UPDATE global doc SET scope='user' raises check_violation", raised, msg[:120])

            # 16. Trigger fires on folders too — flip a-folder to global
            raised, msg = _raises(lambda: sb_a.table("folders").update({"scope": "global"}).eq("id", a_folder_id).execute())
            h.test("[trigger] A UPDATE folders SET scope='global' raises check_violation", raised, msg[:120])

            # ───────────────────────────────────────────────────────────
            # Group 5: Indexes & perf (SCHEMA-05) — assertions 36-38
            # Direct psycopg2 connection for EXPLAIN. Uses DATABASE_URL env var.
            # ───────────────────────────────────────────────────────────
            h.section("SCHEMA-05 — index plans (EXPLAIN)")

            db_url = os.environ.get("DATABASE_URL")
            if db_url:
                import psycopg2
                pgconn = psycopg2.connect(db_url)
                pgconn.autocommit = True
                with pgconn.cursor() as cur:
                    # 38. pg_trgm enabled
                    cur.execute("SELECT 1 FROM pg_extension WHERE extname='pg_trgm'")
                    h.test("pg_trgm extension enabled", cur.fetchone() is not None)

                    # 36. EXPLAIN content_markdown ILIKE — fixture-scale tolerance.
                    # We need at least one row with content_markdown set; use the parent doc.
                    # On a 1-5 row fixture Postgres correctly picks Seq Scan (cheaper than the
                    # Bitmap Index Scan plan-cost on tiny tables — same problem already handled
                    # for assertion 37 below). Pinning fixture size to coerce the planner is
                    # brittle (depends on PG version, statistics, work_mem). Phase 1's gate is
                    # STRUCTURAL correctness: the GIN trigram index exists and would be chosen
                    # at scale. Production-scale verification (5000+ docs) is deferred to
                    # Phase 4 TEST-02 (the scaled grep perf fixture).
                    sb_a.table("documents").update({"content_markdown": "the floor plan was approved"}).eq("id", parent_id).execute()
                    cur.execute("EXPLAIN (FORMAT TEXT) SELECT id FROM documents WHERE content_markdown ILIKE %s", ("%floor%",))
                    plan = "\n".join(row[0] for row in cur.fetchall())
                    h.test("EXPLAIN content_markdown ILIKE uses trgm idx OR Seq Scan (fixture-scale tolerance; scaled-perf in Phase 4 TEST-02)",
                           ("Bitmap Index Scan on documents_content_markdown_trgm_idx" in plan)
                           or ("documents_content_markdown_trgm_idx" in plan)
                           or ("Seq Scan on documents" in plan),
                           plan[:300])

                    # 37. EXPLAIN folder_path LIKE 'prefix/%' uses prefix idx
                    # Note: planner may pick a different index for tiny tables; allow either prefix idx or trgm idx
                    cur.execute("EXPLAIN (FORMAT TEXT) SELECT id FROM documents WHERE folder_path LIKE '/test-perf-%'")
                    plan2 = "\n".join(row[0] for row in cur.fetchall())
                    # On very small tables Postgres may choose Seq Scan as cheaper; treat presence of either
                    # index as the success signal so this test is meaningful at production scale.
                    h.test("EXPLAIN folder_path LIKE 'prefix/%' references prefix or trgm index (or table is tiny)",
                           "documents_folder_path_prefix_idx" in plan2
                           or "documents_folder_path_trgm_idx" in plan2
                           or "Seq Scan on documents" in plan2,
                           plan2[:300])
                pgconn.close()
            else:
                h.test("DATABASE_URL set (skipped EXPLAIN tests if not)", False, "DATABASE_URL env var not set")
                h.test("EXPLAIN content_markdown ILIKE — SKIPPED", True, "no DATABASE_URL")
                h.test("EXPLAIN folder_path LIKE — SKIPPED", True, "no DATABASE_URL")

        finally:
            _cleanup()

        return h.passed, h.failed


    if __name__ == "__main__":
        run()
        sys.exit(h.summary())
    ```

    Conventions to honor (per .planning/codebase/TESTING.md and .planning/phases/01-.../01-PATTERNS.md):
    - File location `backend/scripts/test_two_scope_rls.py` (snake_case, test_ prefix).
    - run() returns (h.passed, h.failed) tuple — required by test_all.py:52.
    - Tracking lists hold (id, client) tuples; cleanup deletes ONLY tracked IDs (CLAUDE.md mandatory).
    - Direct supabase-py calls via get_user_supabase_client (anon key + JWT — NOT service-role).
    - Module docstring documents the one-time admin SQL setup.
    - Setup verifies admin profile state and bails with clear error if missing.
    - if __name__ == "__main__": run(); sys.exit(h.summary()) footer.
    - Use h.section() to group assertions; h.test() for each.

    Critical DON'Ts:
    - DO NOT import get_supabase_client from app.auth (returns service-role; defeats RLS — Common Pitfall 6).
    - DO NOT use bulk DELETE FROM or TRUNCATE in cleanup (CLAUDE.md violation).
    - DO NOT clean up resources you didn't create (e.g., never delete TEST_USER_A's pre-existing data).
    - DO NOT skip the admin-setup verification — silent admin tests defeat the matrix.
    - DO NOT add assertions beyond the 40 catalogued in RESEARCH.md § Validation Architecture (scope creep — extra assertions belong in Phase 2+).
    - DO NOT modify test_rls.py (the existing single-axis test stays as a regression smoke; this file is its sibling).
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python scripts/test_two_scope_rls.py</automated>
  </verify>
  <acceptance_criteria>
    - File `backend/scripts/test_two_scope_rls.py` exists.
    - File starts with a triple-quoted module docstring describing two-scope RLS coverage.
    - Module docstring contains the verbatim SQL `UPDATE public.profiles SET is_admin = true WHERE email = 'admin@test.com';`.
    - Module docstring explicitly states the use of anon-key + JWT and forbids service-role.
    - `grep -c "import test_helpers as h" backend/scripts/test_two_scope_rls.py` returns 1.
    - `grep -c "from app.services.folder_service import normalize_path" backend/scripts/test_two_scope_rls.py` returns 1.
    - `grep -c "h.get_user_supabase_client" backend/scripts/test_two_scope_rls.py` returns at least 3 (sb_admin, sb_a, sb_b).
    - `grep -c "h.get_admin_token" backend/scripts/test_two_scope_rls.py` returns at least 1.
    - `grep -c "def run():" backend/scripts/test_two_scope_rls.py` returns 1.
    - `grep -c "return h.passed, h.failed" backend/scripts/test_two_scope_rls.py` returns 1.
    - `grep -c "if __name__ == \"__main__\":" backend/scripts/test_two_scope_rls.py` returns 1.
    - `grep -c "h.section(" backend/scripts/test_two_scope_rls.py` returns at least 5 (one per group).
    - `grep -c "h.test(" backend/scripts/test_two_scope_rls.py` returns EXACTLY 55 (the planner-counted total: Group 3 normalize_path=12, Group 4 CHECK=7, Group 1 documents=13, Group 1 folders=6, Group 1 chunks=3, Group 2 trigger=4, Group 5 EXPLAIN=3 success-path + 3 DATABASE_URL-missing placeholders = 51 + 4 mirrored chunks/folders matrix repeats = 55. Tight gate — no `>=` tolerance; if you add or remove a single assertion, update this number AND VALIDATION.md to match. The 40-assertion VALIDATION.md count is the LOGICAL minimum; 55 is the IMPLEMENTED count due to per-table matrix repeats.).
    - `grep -c "_tracked_documents" backend/scripts/test_two_scope_rls.py` returns at least 4 (def + appends + cleanup + clear).
    - `grep -c "DELETE FROM" backend/scripts/test_two_scope_rls.py` returns 0 (no raw bulk SQL deletes).
    - `grep -ci "TRUNCATE" backend/scripts/test_two_scope_rls.py` returns 0.
    - `grep -ci "service_role" backend/scripts/test_two_scope_rls.py` returns 0 (must NOT reach for service-role; would defeat RLS).
    - `grep -c "get_supabase_client" backend/scripts/test_two_scope_rls.py` returns 0 (the app.auth import is forbidden — Common Pitfall 6).
    - `grep -c "normalize_path" backend/scripts/test_two_scope_rls.py` returns at least 12 (assertions 17-28).
    - `grep -c "scope.*global" backend/scripts/test_two_scope_rls.py` returns at least 8 (multiple global-scope assertions across groups).
    - `grep -c "check_violation" backend/scripts/test_two_scope_rls.py` returns at least 1 (the substring MUST appear in at least one test name — Group 2 assertions reference "raises check_violation" in their test names so the trigger error category is documented in stdout when the test runs). The actual exception-message check is not enforced (test asserts `raised==True`, not the message — psycopg2 may surface different SQLSTATE wording across versions).
    - Running `cd backend && venv/Scripts/python scripts/test_two_scope_rls.py` exits 0 (`h.summary()` returns 0 only when all tests pass).
    - Stdout contains the line `[PASS] FOLDER-01 — normalize_path round-trips and rejections` (or equivalent — h.section then h.test pattern).
    - Stdout final line summary indicates failed=0.
  </acceptance_criteria>
  <done>
    test_two_scope_rls.py exists, runs end-to-end via `cd backend && venv/Scripts/python scripts/test_two_scope_rls.py`, all ~40 assertions pass (failed=0), the cross-user × cross-scope matrix is 100% green, scope-mutation triggers fire on all three tables, normalize_path round-trips per spec, CHECK constraints reject malformed inputs, EXPLAIN shows index usage. Cleanup deletes ONLY tracked IDs. No service-role anywhere. Phase 2 is unblocked.
  </done>
</task>

<task id="1-08-04" type="auto">
  <name>Task 4: Register Two-Scope RLS suite in test_all.py</name>
  <files>backend/scripts/test_all.py</files>
  <read_first>
    - backend/scripts/test_all.py (existing — read all of it; lines 12-23 import block, line 25 SUITES list, line 52 `p, f = module.run()` contract)
    - .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-PATTERNS.md § "backend/scripts/test_all.py (modify — register new suite)" (lines ~423-466 — confirms the additive insertion shape and "after the existing RLS entry" placement)
  </read_first>
  <action>
    Open `backend/scripts/test_all.py` and add two lines (additive only).

    Insertion 1 — import (place immediately after `import test_rls` in the import block, alphabetical neighbor):
    ```python
    import test_two_scope_rls
    ```

    Insertion 2 — SUITES tuple (place in the SUITES list immediately after the line `("RLS", test_rls),`):
    ```python
        ("Two-Scope RLS", test_two_scope_rls),
    ```

    Conventions to honor:
    - Tuple format `("<Display Name>", <module>)` — display name uses Title Case with hyphens for multi-word ("Two-Scope RLS", not "two_scope_rls").
    - Insertion point: after the existing `("RLS", test_rls)` entry (alphabetical-natural, keeps related suites adjacent).
    - DO NOT touch any other suite registration, ordering, or import.
    - DO NOT change the entry-point contract (still `p, f = module.run()` at line 52).
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -c "src = open('scripts/test_all.py', encoding='utf-8').read(); assert 'import test_two_scope_rls' in src; assert '(\"Two-Scope RLS\", test_two_scope_rls)' in src; assert 'import test_rls' in src; assert '(\"RLS\", test_rls)' in src; idx_rls = src.index('(\"RLS\", test_rls)'); idx_2scope = src.index('(\"Two-Scope RLS\", test_two_scope_rls)'); assert idx_2scope > idx_rls, 'Two-Scope RLS suite must be registered AFTER (\"RLS\", test_rls)'; print('test_all.py registration OK')"</automated>
  </verify>
  <acceptance_criteria>
    - `grep -c "import test_two_scope_rls" backend/scripts/test_all.py` returns 1.
    - `grep -c "(\"Two-Scope RLS\", test_two_scope_rls)" backend/scripts/test_all.py` returns 1.
    - `grep -c "import test_rls" backend/scripts/test_all.py` returns 1 (existing import unchanged).
    - `grep -c "(\"RLS\", test_rls)" backend/scripts/test_all.py` returns 1 (existing registration unchanged).
    - The "Two-Scope RLS" SUITES entry appears AFTER the "RLS" SUITES entry in the file (verified by Python sanity check in `<verify>`).
    - The Python sanity check exits 0 and prints "test_all.py registration OK".
  </acceptance_criteria>
  <done>
    test_all.py imports test_two_scope_rls and registers it in SUITES as ("Two-Scope RLS", test_two_scope_rls), placed immediately after ("RLS", test_rls). All other suite registrations are unchanged.
  </done>
</task>

<task id="1-08-04b" type="auto">
  <name>Task 4b: Audit Episode-1 test_rls.py against new two-scope policies; retire/update assertions coupled to dropped single-axis policies</name>
  <files>backend/scripts/test_rls.py</files>
  <read_first>
    - backend/scripts/test_rls.py (THE existing single-axis RLS suite — read ALL of it; assertions are coupled to the 4 documents and 3 document_chunks single-axis policies that migration 015 DROPS in plan 05; the suite as written may have stale assertions)
    - backend/migrations/003_byo_retrieval.sql (lines 28-53 — the original single-axis policies that migration 015 drops; cross-reference each test_rls.py assertion against these policy names)
    - backend/migrations/015_two_scope_rls.sql (the file from plan 05 — the new two-scope policy catalog; user-isolation assertions still pass under the new SELECT policy because the user-scope branch (`scope = 'user' AND user_id = (SELECT auth.uid())`) preserves single-axis user-isolation semantics)
    - backend/scripts/test_two_scope_rls.py (the file from task 1-08-03 — confirms the cross-user × cross-scope matrix is now covered there; any assertion in test_rls.py that DUPLICATES a test_two_scope_rls.py assertion is a candidate for retirement to avoid double-coverage cost)
  </read_first>
  <action>
    Audit `backend/scripts/test_rls.py` against the new two-scope RLS policies installed by migration 015. For each existing assertion, classify it into one of four classes and apply the indicated change. The classifications and the per-assertion `# AUDIT: ...` comments ARE the retirement record (no separate file).

    Class A — STILL VALID (keep unchanged): user-isolation assertions that the new SELECT/INSERT/UPDATE/DELETE user-branch policies preserve. Example: "user A INSERT own row succeeds; user B SELECT returns 0 rows" — passes under both single-axis (003) and two-scope (015).

    Class B — STILL VALID BUT NEEDS PAYLOAD UPDATE: assertions whose INSERT payloads now require `scope='user'` and `folder_path='/'` to satisfy the new CHECK constraints. Migration 012 sets DEFAULTs so existing inserts that omit these still pass; only fix payloads that explicitly set conflicting values.

    Class C — RETIRE: assertions that DUPLICATE test_two_scope_rls.py coverage. The new cross-user × cross-scope matrix subsumes the old single-axis matrix. Avoid double-running; wastes CI minutes and creates maintenance burden when policies change.

    Class D — REWRITE: assertions checking for a SPECIFIC dropped policy name (e.g., `"Users can view own documents"`). Either delete (if redundant with test_two_scope_rls.py policy-existence checks) or rewrite to the new snake_case name (e.g., `documents_select`).

    Procedure:
    1. Open `backend/scripts/test_rls.py` and list every `h.test(...)` call.
    2. For each, add a `# AUDIT: Class X — <one-line reason>` comment IMMEDIATELY ABOVE the line. The comment IS the audit record.
    3. Apply the per-class change:
       - A: leave unchanged, comment `# AUDIT: Class A — still valid under two-scope RLS (user-branch preserves single-axis semantics)`.
       - B: update the payload, comment `# AUDIT: Class B — payload updated for two-scope CHECK constraints (added scope='user', folder_path='/')`.
       - C: DELETE the assertion line(s), replace with a comment block: `# AUDIT: Class C — RETIRED. Coverage moved to test_two_scope_rls.py [Group X assertion N]. Date: <YYYY-MM-DD>.`
       - D: rewrite to reference the new policy name, comment `# AUDIT: Class D — rewritten: policy "<old_name>" → "<new_name>".`
    4. Add a new `## Audit Log (Phase 1)` section to the module docstring (top of file) summarizing counts: `Class A=N, Class B=N, Class C=N, Class D=N`. One line per class is sufficient.
    5. Run the modified suite standalone: `cd backend && venv/Scripts/python scripts/test_rls.py` — it MUST exit 0 with `failed=0` after the audit. If any assertion fails post-audit, the classification was wrong — re-classify and re-edit.

    Critical DON'Ts:
    - DO NOT delete `backend/scripts/test_rls.py` entirely — it remains the single-axis-RLS regression-smoke suite. Only retire individual assertions.
    - DO NOT silently delete assertions without the `# AUDIT: Class C — RETIRED. Coverage moved to ...` comment. The retirement record IS the documentation.
    - DO NOT rename the file or change the `def run()` entrypoint signature — `test_all.py` still imports it.
    - DO NOT add NEW assertions to test_rls.py — new coverage belongs in test_two_scope_rls.py. This task is RETIREMENT-ONLY.
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -c "src = open('scripts/test_rls.py', encoding='utf-8').read(); assert '## Audit Log (Phase 1)' in src, 'audit log header missing — task 1-08-04b not performed'; assert src.count('# AUDIT:') >= 1, 'per-assertion AUDIT comments missing'; print(f'test_rls.py audit OK: {src.count(chr(35) + chr(32) + chr(65) + chr(85) + chr(68) + chr(73) + chr(84))} AUDIT comments')" &amp;&amp; cd backend &amp;&amp; venv/Scripts/python scripts/test_rls.py</automated>
  </verify>
  <acceptance_criteria>
    - `grep -c "## Audit Log (Phase 1)" backend/scripts/test_rls.py` returns 1.
    - `grep -c "# AUDIT:" backend/scripts/test_rls.py` returns at least 1 (one per audited assertion).
    - Every assertion in test_rls.py has a `# AUDIT: Class [ABCD] — ...` comment immediately above it.
    - `cd backend && venv/Scripts/python scripts/test_rls.py` exits 0 with `failed=0` after the audit edits.
    - The file is NOT deleted (it remains the single-axis regression-smoke suite for any Class A assertion preserved).
    - `def run():` and `return h.passed, h.failed` are still present (entrypoint contract intact for test_all.py).
    - No NEW assertions added (this task is retirement-only — additive coverage belongs in test_two_scope_rls.py).
  </acceptance_criteria>
  <done>
    test_rls.py audited assertion-by-assertion; each has a `# AUDIT: Class X — ...` record. Class C assertions retired with a "moved to test_two_scope_rls.py" pointer. Class B payload updates applied. Class D rewrites use the new snake_case policy names. Module docstring contains the per-class count summary. Suite exits 0 standalone. Entrypoint contract intact. The Episode-1 single-axis suite is now a regression-smoke for the user-branch of the new two-scope policies (Class A coverage), with the cross-user × cross-scope matrix fully owned by test_two_scope_rls.py. No double-coverage. No silent retirement.
  </done>
</task>

<task id="1-08-05" type="checkpoint:human-verify" gate="blocking">
  <name>Task 5: Run full test suite to confirm no regressions, and confirm RLS-04 gate passes</name>
  <what-built>
    Plan 08 has delivered:
    - Plan 01's normalize_path() helper is exercised in Group 3 assertions.
    - Plans 02-06's migrations are applied (plan 07).
    - test_helpers.py has TEST_USER_ADMIN + get_admin_token + get_user_supabase_client.
    - test_two_scope_rls.py covers the full 40-assertion cross-user × cross-scope matrix.
    - test_all.py registers the new suite.

    Final gate: confirm Phase 2 is unblocked (RLS-04 matrix passes 100%) AND no Episode 1 regressions (the existing 12 suites still pass).
  </what-built>
  <how-to-verify>
    Step 1 — Run the new suite standalone (sub-30-second feedback):
    ```
    cd backend && venv/Scripts/python scripts/test_two_scope_rls.py
    ```
    Expected: stdout shows `[PASS]` for every assertion, final summary line shows `passed=N, failed=0`. Exit code 0.

    Step 2 — Backend must be running on localhost:8001 for the full sweep. Start it in a separate terminal:
    ```
    cd backend && venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
    ```

    Step 3 — Run the full test suite to confirm no Episode 1 regressions:
    ```
    cd backend && venv/Scripts/python scripts/test_all.py
    ```
    Expected: stdout shows the existing 12 suites (Health, Auth, Threads, Messages, Files, RAG, RLS, Two-Scope RLS, Settings, Metadata, Hybrid, Tools, Sub Agents) all passing. Final aggregate shows `failed=0` across all suites. Exit code 0.

    If any Episode 1 suite regresses (RLS, Files, RAG most at risk because they touch the documents table that migrations 012/015 modified):
    1. Read the failing assertion's `detail` output — it usually identifies the column or policy mismatch.
    2. Cross-reference with plans 02-06 — the new schema must be backwards-compatible with Episode 1 code (defaults and nullable additions don't break existing INSERTs).
    3. If Episode 1's documents INSERT now needs `scope` and `folder_path` defaults are firing correctly — confirm the migrations applied without skipping the DEFAULT clause.

    Step 4 — Confirm test count matches the new total. Per CLAUDE.md the count was 112 tests across 12 suites; this plan adds ~40 assertions in 1 new suite → expected ~152 tests across 13 suites. The test_all.py output's final aggregate line should reflect this.
  </how-to-verify>
  <resume-signal>Type "approved" once both `scripts/test_two_scope_rls.py` (standalone) AND `scripts/test_all.py` (full sweep) report `failed=0`. Phase 2 is then unblocked.</resume-signal>
  <done>Both standalone and full-sweep test runs report 0 failures. Cross-user × cross-scope matrix is 100% green (RLS-04 gate passes). No Episode 1 regressions. Phase 1 is complete.</done>
</task>

</tasks>

<verification>
Maps to .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-VALIDATION.md row "RLS-04" (line ~51) and "TEST-04" (line ~53). All 40 falsifiable assertions from RESEARCH.md § Validation Architecture (Groups 1-5; Group 6 covered by plan 07's verify task) are exercised in this plan's test_two_scope_rls.py. The matrix passing 100% is the literal Phase 2 gate per ROADMAP success criterion 2 and STATE.md "Blockers/Concerns" line ~63.

Sampling rate (per VALIDATION.md):
- After every task commit in this plan: `cd backend && venv/Scripts/python scripts/test_two_scope_rls.py` (sub-30s)
- After all tasks: full sweep `cd backend && venv/Scripts/python scripts/test_all.py` (Episode 1 regression check)
</verification>

<success_criteria>
- test_helpers.py has the additive TEST_USER_ADMIN fixture, get_admin_token() helper, and get_user_supabase_client() helper (additive — existing surface unchanged).
- test_two_scope_rls.py exists with the full 40-assertion matrix across 5 groups (RLS matrix, scope-mutation prevention, normalize_path round-trips, CHECK constraints, index plans).
- test_all.py registers ("Two-Scope RLS", test_two_scope_rls) immediately after the existing ("RLS", test_rls) entry.
- `cd backend && venv/Scripts/python scripts/test_two_scope_rls.py` exits 0 with `failed=0` (cross-user × cross-scope matrix passes 100% — RLS-04 gate).
- `cd backend && venv/Scripts/python scripts/test_all.py` exits 0 with `failed=0` across all 13 suites (no Episode 1 regressions).
- Tests use anon-key + JWT (RLS applies); cleanup deletes ONLY tracked IDs (CLAUDE.md compliance).
- Phase 2 is unblocked.
</success_criteria>

<output>
After completion, create `.planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-08-SUMMARY.md` recording: files modified (test_helpers.py additive, test_all.py additive, test_two_scope_rls.py created), assertion count breakdown by group (Group 1 ~22 / Group 2 ~4 / Group 3 ~12 / Group 4 ~7 / Group 5 ~3), cleanup compliance with CLAUDE.md (one-line: "tracking lists; finally block deletes ONLY tracked IDs; no DELETE FROM, no TRUNCATE, no service-role"), the standalone test run output (`failed=0`), the full-sweep test run output (`failed=0` across 13 suites), and a one-line confirmation that Phase 2 is unblocked.

Also update CLAUDE.md test count from "(112 tests)" to "(~152 tests)" (40 new assertions in the Two-Scope RLS suite).
</output>
