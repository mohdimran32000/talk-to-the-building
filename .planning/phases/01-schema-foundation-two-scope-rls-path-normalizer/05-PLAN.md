---
phase: 01
plan: 05
type: execute
wave: 3
depends_on: [02, 03, 04]
files_modified:
  - backend/migrations/015_two_scope_rls.sql
autonomous: true
requirements:
  - RLS-01
  - RLS-02
  - RLS-03
must_haves:
  truths:
    - "public.is_admin() SQL function exists (LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public), reads from public.profiles, returns boolean"
    - "EXECUTE on public.is_admin() is GRANTed to authenticated"
    - "public.forbid_scope_mutation() trigger function exists (LANGUAGE plpgsql, BEFORE-UPDATE-trigger semantics)"
    - "Trigger function RAISEs check_violation when NEW.scope IS DISTINCT FROM OLD.scope"
    - "Trigger function RETURNs NEW after the IF block (so non-mutation updates still write)"
    - "All 4 Episode-1 single-axis policies on documents are dropped (Users can view/insert/update/delete own documents)"
    - "All 3 Episode-1 single-axis policies on document_chunks are dropped (Users can view/insert/delete own chunks — no UPDATE policy in 003)"
    - "7 CREATE POLICY statements on documents (SELECT, INSERT user, INSERT global, UPDATE user, UPDATE global, DELETE user, DELETE global)"
    - "5 CREATE POLICY statements on document_chunks (SELECT, INSERT user, INSERT global, DELETE user, DELETE global — no UPDATE; chunks are immutable, re-ingestion is delete-then-insert)"
    - "7 CREATE POLICY statements on folders (SELECT, INSERT user, INSERT global, UPDATE user, UPDATE global, DELETE user, DELETE global)"
    - "TOTAL: 7 + 5 + 7 = 19 CREATE POLICY statements (matches the acceptance-criterion grep `sql.count('CREATE POLICY') == 19`)"
    - "3 BEFORE UPDATE triggers (one per table) — these are additional protections counted SEPARATELY from CREATE POLICY (not a 8th/8th/6th policy)"
    - "BEFORE UPDATE trigger documents_forbid_scope_mutation attached to public.documents"
    - "BEFORE UPDATE trigger document_chunks_forbid_scope_mutation attached to public.document_chunks (defensive — chunks have no UPDATE policy but the trigger guards against future schema additions)"
    - "BEFORE UPDATE trigger folders_forbid_scope_mutation attached to public.folders"
    - "All policies use TO authenticated and the (SELECT auth.uid()) subquery form (Pitfall 5 perf-wrap)"
    - "All admin-gated INSERT/UPDATE/DELETE policies for global scope use public.is_admin() (DRY)"
    - "Global-scope INSERT policies require user_id IS NULL (defense in depth with the CHECK coupling from migration 012)"
  artifacts:
    - path: "backend/migrations/015_two_scope_rls.sql"
      provides: "is_admin() helper, forbid_scope_mutation() trigger function, dropped Episode-1 single-axis policies, 19 new two-scope CREATE POLICY statements (7 documents + 5 document_chunks + 7 folders), and 3 BEFORE UPDATE triggers (one per table) — 22 total protections"
      contains: "CREATE OR REPLACE FUNCTION public.is_admin"
      contains_2: "CREATE OR REPLACE FUNCTION public.forbid_scope_mutation"
      contains_3: "DROP POLICY IF EXISTS \"Users can view own documents\""
      contains_4: "documents_forbid_scope_mutation"
      contains_5: "is_admin()"
      min_lines: 200
  key_links:
    - from: "documents.scope + documents.user_id (from migration 012)"
      to: "8 documents RLS policies (SELECT/INSERT user/INSERT global/UPDATE user/UPDATE global/DELETE user/DELETE global)"
      via: "schema dependency — policies reference both columns"
      pattern: "scope = 'user' AND user_id"
    - from: "public.is_admin() helper"
      to: "All 6 admin-gated policies (INSERT global × 3 tables, UPDATE global × 2 tables (chunks have none), DELETE global × 3 tables)"
      via: "DRY admin gate — one function used in 8 policies (3 INSERT global + 2 UPDATE global on docs/folders + 3 DELETE global)"
      pattern: "AND public.is_admin()"
    - from: "public.forbid_scope_mutation() + 3 BEFORE UPDATE triggers"
      to: "RLS-03 implementation (Postgres RLS WITH CHECK cannot reference OLD; trigger is the canonical workaround per RESEARCH.md §1)"
      via: "trigger fires after RLS passes, before row write — RAISEs check_violation if NEW.scope IS DISTINCT FROM OLD.scope"
      pattern: "RAISE EXCEPTION 'Scope mutation forbidden"
---

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Authenticated user (anon-key + JWT) -> documents/document_chunks/folders | RLS evaluates per-row on every SELECT/INSERT/UPDATE/DELETE — bedrock defense |
| Service-role key (existing anti-pattern per CONCERNS.md) -> tables | Service-role bypasses RLS; backend service layer (Phase 3+) MUST add explicit `.eq('scope', ...)` and `.eq('user_id', ...)` filters as defense in depth |
| App UPDATE statement -> trigger -> table write | Trigger fires AFTER RLS policies grant the write but BEFORE the row is persisted; raises check_violation on scope mutation |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-1-01 (PRIMARY — RANK 1 PHASE THREAT) | Tampering / Information Disclosure | RLS policies on documents, document_chunks, folders | mitigate | Multi-layered defense: (1) Separate INSERT policy per scope — `documents_insert_user` requires `scope='user' AND user_id=(SELECT auth.uid())`; `documents_insert_global` requires `scope='global' AND user_id IS NULL AND public.is_admin()`. A non-admin attempting `INSERT scope='global'` is rejected because no policy grants that combination. (2) Separate UPDATE policy per scope; `documents_update_user` requires self-owned user-scope row. (3) BEFORE UPDATE trigger `forbid_scope_mutation` raises `check_violation` if NEW.scope IS DISTINCT FROM OLD.scope (Postgres RLS WITH CHECK cannot reference OLD; trigger is the canonical workaround). (4) `(SELECT auth.uid())` subquery form for per-query caching (Pitfall 5 perf optimization). (5) `TO authenticated` on every policy locks them away from the anon role. (Pitfall 1 mitigation §1-§3.) ROADMAP success criterion 1 + 2 are the gate. |
| T-1-01 (helper) | Tampering / Privilege Escalation | public.is_admin() function | mitigate | `LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public` — runs as the function owner (postgres role) so it can read public.profiles bypassing the policies on profiles table (which would otherwise deadlock). `STABLE` lets Postgres cache within a single statement. The function reads `is_admin` from `public.profiles WHERE id = auth.uid()` at query time — no JWT-cached `is_admin` claim (avoids the "admin demotion mid-session" stale-cache risk per Pitfall §security). EXECUTE granted only to `authenticated` role. |
| T-1-01 (chunks defense) | Tampering | document_chunks scope mutation | mitigate | Trigger attached even though chunks have no UPDATE policy (chunks are insert-and-delete only). Defensive — if a future migration adds a chunks UPDATE policy, the trigger is already in place to forbid scope mutation. |
| T-1-Aux (idempotency) | Operational | All 7 dropped policies + 3 triggers | mitigate | DROP POLICY IF EXISTS for every Episode-1 policy by exact name (matched verbatim from 003_byo_retrieval.sql:29-32 and :51-53). DROP TRIGGER IF EXISTS before each CREATE TRIGGER. Re-runnable. |
</threat_model>

<objective>
Write `backend/migrations/015_two_scope_rls.sql` — the security-critical migration that swaps Episode 1's single-axis user-isolation RLS for the two-scope (user × scope) policy catalog, adds the `public.is_admin()` SQL helper for DRY admin gating, and installs the `public.forbid_scope_mutation()` BEFORE UPDATE trigger on all three tables (documents, document_chunks, folders). This is the **Pitfall 1 / RANK 1 phase threat mitigation** and the **gate for Phase 2** — `test_two_scope_rls.py` (plan 08) must pass 100% on the cross-user × cross-scope matrix before any other phase advances. Critical correction from the original phase brief: `WITH CHECK (scope = OLD.scope)` is invalid Postgres syntax (RESEARCH.md §1) — the implementation uses a BEFORE UPDATE trigger instead.
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
@CLAUDE.md

@backend/migrations/003_byo_retrieval.sql
@backend/migrations/005_profiles_and_settings.sql
@backend/migrations/008_hybrid_search.sql
@backend/migrations/012_folder_path_and_scope.sql
@backend/migrations/013_folders_table.sql

<interfaces>
<!-- Existing Episode-1 policies that this migration MUST DROP first (names verbatim from 003). -->

backend/migrations/003_byo_retrieval.sql lines 29-32 — documents:
- "Users can view own documents"   (FOR SELECT)
- "Users can insert own documents" (FOR INSERT)
- "Users can update own documents" (FOR UPDATE)
- "Users can delete own documents" (FOR DELETE)

backend/migrations/003_byo_retrieval.sql lines 51-53 — document_chunks (NOTE: only 3, no UPDATE):
- "Users can view own chunks"   (FOR SELECT)
- "Users can insert own chunks" (FOR INSERT)
- "Users can delete own chunks" (FOR DELETE)

backend/migrations/005_profiles_and_settings.sql lines 38-50 — handle_new_user pattern (SECURITY DEFINER + SET search_path = public):
```sql
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN ... RETURN NEW; END; $$;
```
^ Mirror this header style for is_admin() and forbid_scope_mutation().

backend/migrations/005_profiles_and_settings.sql lines 86-93 — admin-gate predicate that 015 factors into is_admin():
```sql
EXISTS (SELECT 1 FROM public.profiles WHERE id = auth.uid() AND is_admin = true)
```

backend/migrations/008_hybrid_search.sql lines 11-22 — DROP TRIGGER IF EXISTS … CREATE TRIGGER pattern (idempotent).

Critical Postgres semantics:
- RLS WITH CHECK cannot reference OLD or NEW (Postgres docs verified, RESEARCH.md §1) — must use trigger
- Multiple permissive policies per (table, command) are OR'd — splitting INSERT into "_user" and "_global" is correct
- (SELECT auth.uid()) is cached per-query; bare auth.uid() is per-row (Pitfall 5; Supabase docs verified)
- TO authenticated locks policies to logged-in users (anon role rejected — best practice)
- BEFORE UPDATE triggers fire AFTER RLS grants the write but BEFORE the row is persisted
- Trigger function MUST `RETURN NEW;` after the IF block (otherwise non-mutation updates are discarded — PATTERNS.md "No Analog Found" warning)
</interfaces>
</context>

<tasks>

<task id="1-05-01" type="auto">
  <name>Task 1: Write migration 015 — two-scope RLS policies + is_admin() helper + forbid_scope_mutation() trigger</name>
  <files>backend/migrations/015_two_scope_rls.sql</files>
  <read_first>
    - backend/migrations/003_byo_retrieval.sql (lines 28-53 — exact policy names that this migration MUST DROP IF EXISTS, verbatim including capitalization and the word "own"; also the canonical 4-policy quartet shape on documents)
    - backend/migrations/005_profiles_and_settings.sql (lines 23-29, 38-50, 53-56, 86-93 — SECURITY DEFINER function pattern, DROP TRIGGER IF EXISTS / CREATE TRIGGER pattern, admin-gate predicate that 015 factors into is_admin())
    - backend/migrations/008_hybrid_search.sql (lines 11-22 — CREATE OR REPLACE FUNCTION + DROP TRIGGER IF EXISTS + CREATE TRIGGER idempotent shape; closest analog for the trigger function structure)
    - backend/migrations/012_folder_path_and_scope.sql (the just-written file from plan 02 — confirms scope/user_id columns exist on documents and document_chunks before 015 references them in policies)
    - backend/migrations/013_folders_table.sql (the just-written file from plan 03 — confirms public.folders table exists with scope/user_id columns and ENABLE RLS, before 015 adds policies)
    - .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-RESEARCH.md § "Migration 015 — Two-scope RLS policies + scope-mutation trigger" (lines ~640-720 for skeleton) AND § "RLS Policy Catalog" (lines ~768-944 for the full SQL of all 21 policies — DEFINITIVE, paste-ready) AND § Decisions §1 (lines ~145-183 — explains why trigger not WITH CHECK OLD.scope; lists exact trigger function body) AND § Decisions §3 / §6 (is_admin helper rationale)
    - .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-PATTERNS.md § "backend/migrations/015_two_scope_rls.sql" (lines ~152-221 — confirms 005 + 003 + 008 are the analogs; documents the policy-naming convention shift from sentence-case to snake_case and the (SELECT auth.uid()) perf optimization that has no prior precedent)
    - .planning/research/PITFALLS.md § Pitfall 1 (lines ~13-41 — the THREAT this entire migration mitigates, RANK 1)
  </read_first>
  <action>
    Create `backend/migrations/015_two_scope_rls.sql` with the EXACT SQL below. This is a long file (~200 lines); the structure is: (1) is_admin() helper, (2) forbid_scope_mutation() trigger function, (3) drop Episode-1 policies, (4) 21 new policies (8 documents + 6 chunks + 7 folders, with the trigger counting as the eighth protection on each table that has UPDATE), (5) attach 3 triggers. Sections are paste-ready from RESEARCH.md § Migration 015 and § RLS Policy Catalog.

```sql
-- Phase 1 / Migration 015: Two-scope RLS policies + scope-mutation trigger
-- Replaces Episode 1's single-axis user-isolation RLS (migration 003 lines 28-53)
-- with the two-scope (user × scope) policy catalog. This is the security-critical
-- migration — Pitfall 1 / RANK 1 threat mitigation.
--
-- DESIGN NOTES:
-- 1. Policy names shift from sentence-case ("Users can view own documents") to
--    snake_case ("documents_select", "documents_insert_user"). This is deliberate —
--    the new naming makes the (table, op, scope) decomposition obvious in the
--    pg_policy catalog.
-- 2. (SELECT auth.uid()) subquery form — Postgres caches the result per query
--    (10× faster than bare auth.uid() per row on hot tables). Supabase RLS perf
--    best practice — first use of this pattern in the codebase.
-- 3. RLS-03 (forbid scope mutation): Postgres RLS WITH CHECK cannot reference
--    OLD.col (raises "missing FROM-clause entry for table 'old'"). The canonical
--    workaround is a BEFORE UPDATE trigger that RAISEs on scope change.
-- 4. is_admin() SQL function factors out the EXISTS-from-profiles admin check
--    used in 6+ policies. SECURITY DEFINER bypasses profiles RLS for the lookup.
-- 5. Splitting INSERT into "_user" and "_global" policies (and same for UPDATE/DELETE)
--    works because Postgres OR's multiple permissive policies per (table, command).
--    Trivially reviewable: who can do what reads top-to-bottom.

-- ── 1. is_admin() helper ──
-- DRY admin gate used in 6+ policies. SECURITY DEFINER + SET search_path=public
-- mirrors handle_new_user pattern from migration 005:38-50. STABLE lets Postgres
-- cache the result within a single statement.
CREATE OR REPLACE FUNCTION public.is_admin() RETURNS BOOLEAN
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(
    (SELECT is_admin FROM public.profiles WHERE id = auth.uid()),
    false
  );
$$;

GRANT EXECUTE ON FUNCTION public.is_admin() TO authenticated;

-- ── 2. forbid_scope_mutation() trigger function ──
-- WORKAROUND for RLS-03: Postgres RLS WITH CHECK cannot reference OLD.col, so
-- the canonical pattern is a BEFORE UPDATE trigger. Trigger fires after RLS
-- policies pass but before the row is persisted. Raises check_violation (a
-- standard SQLSTATE) if scope is being changed.
-- IMPORTANT: RETURN NEW must be after the IF block (NOT inside it), otherwise
-- non-mutation updates are silently discarded.
CREATE OR REPLACE FUNCTION public.forbid_scope_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.scope IS DISTINCT FROM OLD.scope THEN
    RAISE EXCEPTION
      'Scope mutation forbidden: cannot change scope from % to % (use delete + admin re-insert)',
      OLD.scope, NEW.scope
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$;

-- ── 3. Drop Episode-1 single-axis policies ──
-- Names matched verbatim (including capitalization and "own") from
-- backend/migrations/003_byo_retrieval.sql:29-32 and :51-53.
-- No prior migration drops policies — IF EXISTS is the canonical safe form.
DROP POLICY IF EXISTS "Users can view own documents"   ON public.documents;
DROP POLICY IF EXISTS "Users can insert own documents" ON public.documents;
DROP POLICY IF EXISTS "Users can update own documents" ON public.documents;
DROP POLICY IF EXISTS "Users can delete own documents" ON public.documents;

DROP POLICY IF EXISTS "Users can view own chunks"   ON public.document_chunks;
DROP POLICY IF EXISTS "Users can insert own chunks" ON public.document_chunks;
DROP POLICY IF EXISTS "Users can delete own chunks" ON public.document_chunks;

-- ── 4a. documents: 7 new policies (SELECT, INSERT user/global, UPDATE user/global, DELETE user/global) ──
-- The trigger from §2 attached in §5 below is the "8th protection" on documents.

CREATE POLICY "documents_select"
  ON public.documents FOR SELECT
  TO authenticated
  USING (
    scope = 'global'
    OR (scope = 'user' AND user_id = (SELECT auth.uid()))
  );

CREATE POLICY "documents_insert_user"
  ON public.documents FOR INSERT
  TO authenticated
  WITH CHECK (
    scope = 'user' AND user_id = (SELECT auth.uid())
  );

CREATE POLICY "documents_insert_global"
  ON public.documents FOR INSERT
  TO authenticated
  WITH CHECK (
    scope = 'global'
    AND user_id IS NULL
    AND public.is_admin()
  );

CREATE POLICY "documents_update_user"
  ON public.documents FOR UPDATE
  TO authenticated
  USING (scope = 'user' AND user_id = (SELECT auth.uid()))
  WITH CHECK (scope = 'user' AND user_id = (SELECT auth.uid()));

CREATE POLICY "documents_update_global"
  ON public.documents FOR UPDATE
  TO authenticated
  USING (scope = 'global' AND public.is_admin())
  WITH CHECK (scope = 'global' AND public.is_admin());

CREATE POLICY "documents_delete_user"
  ON public.documents FOR DELETE
  TO authenticated
  USING (scope = 'user' AND user_id = (SELECT auth.uid()));

CREATE POLICY "documents_delete_global"
  ON public.documents FOR DELETE
  TO authenticated
  USING (scope = 'global' AND public.is_admin());

-- ── 4b. document_chunks: 5 new policies (SELECT, INSERT user/global, DELETE user/global) ──
-- NO UPDATE policy — chunks are insert-and-delete only. Re-ingestion is
-- delete-then-insert per record_manager pattern in migration 006. The trigger
-- attached in §5 is defensive (fires only if a future migration adds an UPDATE policy).

CREATE POLICY "document_chunks_select"
  ON public.document_chunks FOR SELECT
  TO authenticated
  USING (
    scope = 'global'
    OR (scope = 'user' AND user_id = (SELECT auth.uid()))
  );

CREATE POLICY "document_chunks_insert_user"
  ON public.document_chunks FOR INSERT
  TO authenticated
  WITH CHECK (
    scope = 'user' AND user_id = (SELECT auth.uid())
  );

CREATE POLICY "document_chunks_insert_global"
  ON public.document_chunks FOR INSERT
  TO authenticated
  WITH CHECK (
    scope = 'global'
    AND user_id IS NULL
    AND public.is_admin()
  );

CREATE POLICY "document_chunks_delete_user"
  ON public.document_chunks FOR DELETE
  TO authenticated
  USING (scope = 'user' AND user_id = (SELECT auth.uid()));

CREATE POLICY "document_chunks_delete_global"
  ON public.document_chunks FOR DELETE
  TO authenticated
  USING (scope = 'global' AND public.is_admin());

-- ── 4c. folders: 7 new policies (same shape as documents) ──

CREATE POLICY "folders_select"
  ON public.folders FOR SELECT
  TO authenticated
  USING (
    scope = 'global'
    OR (scope = 'user' AND user_id = (SELECT auth.uid()))
  );

CREATE POLICY "folders_insert_user"
  ON public.folders FOR INSERT
  TO authenticated
  WITH CHECK (
    scope = 'user' AND user_id = (SELECT auth.uid())
  );

CREATE POLICY "folders_insert_global"
  ON public.folders FOR INSERT
  TO authenticated
  WITH CHECK (
    scope = 'global'
    AND user_id IS NULL
    AND public.is_admin()
  );

CREATE POLICY "folders_update_user"
  ON public.folders FOR UPDATE
  TO authenticated
  USING (scope = 'user' AND user_id = (SELECT auth.uid()))
  WITH CHECK (scope = 'user' AND user_id = (SELECT auth.uid()));

CREATE POLICY "folders_update_global"
  ON public.folders FOR UPDATE
  TO authenticated
  USING (scope = 'global' AND public.is_admin())
  WITH CHECK (scope = 'global' AND public.is_admin());

CREATE POLICY "folders_delete_user"
  ON public.folders FOR DELETE
  TO authenticated
  USING (scope = 'user' AND user_id = (SELECT auth.uid()));

CREATE POLICY "folders_delete_global"
  ON public.folders FOR DELETE
  TO authenticated
  USING (scope = 'global' AND public.is_admin());

-- ── 5. Attach BEFORE UPDATE triggers (RLS-03) ──
-- Idempotent shape: DROP TRIGGER IF EXISTS, then CREATE TRIGGER (matches
-- migration 005:53-56 and 008:18-22).
DROP TRIGGER IF EXISTS documents_forbid_scope_mutation ON public.documents;
CREATE TRIGGER documents_forbid_scope_mutation
  BEFORE UPDATE ON public.documents
  FOR EACH ROW EXECUTE FUNCTION public.forbid_scope_mutation();

DROP TRIGGER IF EXISTS document_chunks_forbid_scope_mutation ON public.document_chunks;
CREATE TRIGGER document_chunks_forbid_scope_mutation
  BEFORE UPDATE ON public.document_chunks
  FOR EACH ROW EXECUTE FUNCTION public.forbid_scope_mutation();

DROP TRIGGER IF EXISTS folders_forbid_scope_mutation ON public.folders;
CREATE TRIGGER folders_forbid_scope_mutation
  BEFORE UPDATE ON public.folders
  FOR EACH ROW EXECUTE FUNCTION public.forbid_scope_mutation();
```

Conventions to honor (per .planning/phases/01-.../01-PATTERNS.md):
- Filename `015_two_scope_rls.sql`.
- Header: long context comment block (15+ lines) — this migration warrants extensive context because of the policy-naming shift, the (SELECT auth.uid()) optimization, and the trigger workaround for RLS-03.
- All policies use `TO authenticated` (matches 005:80; not present in 003 — best practice; reviewers may "fix" it back without the comment, so keep the design-notes block).
- All policies use `(SELECT auth.uid())` subquery form (Pitfall 5 perf optimization; explicitly called out in design notes — first use in the codebase).
- Snake_case policy names: `documents_select`, `documents_insert_user`, `documents_insert_global`, etc. (deliberate shift from 003's "Users can view own documents" sentence-case).
- DROP POLICY IF EXISTS uses EXACT names from 003 (verbatim including capitalization and "own").
- `CREATE OR REPLACE FUNCTION` for both helper functions (idempotent).
- `DROP TRIGGER IF EXISTS … ; CREATE TRIGGER …` for each of the 3 triggers (idempotent).
- No `BEGIN`/`COMMIT`.

Critical DON'Ts:
- DO NOT write `WITH CHECK (scope = OLD.scope)` — invalid Postgres syntax, will fail at policy creation time (RESEARCH.md §1, Pitfall 1 lines ~1126-1131). The trigger is the enforcement mechanism.
- DO NOT inline the `EXISTS (SELECT 1 FROM profiles WHERE …)` predicate in 6+ places — use `public.is_admin()` (DRY, RESEARCH.md §3 strong recommendation).
- DO NOT use bare `auth.uid()` in policies — always wrap as `(SELECT auth.uid())` (Pitfall 5 perf — 10× difference on hot tables).
- DO NOT add an UPDATE policy to document_chunks (chunks are immutable; re-ingestion is delete-then-insert per migration 006 record_manager pattern).
- DO NOT leave any Episode-1 policy in place — the DROP POLICY IF EXISTS section is mandatory.
- DO NOT include `RETURN NEW;` inside the IF block of the trigger function — must be AFTER, otherwise non-mutation updates are discarded (PATTERNS.md "No Analog Found" warning).
- DO NOT use `CONCURRENTLY`.
- DO NOT touch any data — this migration only changes policies, functions, and triggers.
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -c "sql = open('migrations/015_two_scope_rls.sql', encoding='utf-8').read(); assert 'CREATE OR REPLACE FUNCTION public.is_admin' in sql; assert 'SECURITY DEFINER' in sql; assert 'CREATE OR REPLACE FUNCTION public.forbid_scope_mutation' in sql; assert 'NEW.scope IS DISTINCT FROM OLD.scope' in sql; assert \"USING ERRCODE = 'check_violation'\" in sql; assert 'DROP POLICY IF EXISTS \"Users can view own documents\"' in sql; assert 'DROP POLICY IF EXISTS \"Users can insert own documents\"' in sql; assert 'DROP POLICY IF EXISTS \"Users can update own documents\"' in sql; assert 'DROP POLICY IF EXISTS \"Users can delete own documents\"' in sql; assert 'DROP POLICY IF EXISTS \"Users can view own chunks\"' in sql; assert 'DROP POLICY IF EXISTS \"Users can insert own chunks\"' in sql; assert 'DROP POLICY IF EXISTS \"Users can delete own chunks\"' in sql; assert sql.count('CREATE POLICY') == 19, f'expected 19 CREATE POLICY (7+5+7), got {sql.count(\"CREATE POLICY\")}'; assert 'documents_forbid_scope_mutation' in sql; assert 'document_chunks_forbid_scope_mutation' in sql; assert 'folders_forbid_scope_mutation' in sql; assert sql.count('CREATE TRIGGER') == 3, f'expected 3 CREATE TRIGGER, got {sql.count(\"CREATE TRIGGER\")}'; assert sql.count('DROP TRIGGER IF EXISTS') == 3; assert 'OLD.scope' in sql and 'WITH CHECK (scope = OLD' not in sql, 'WITH CHECK (scope = OLD.scope) is invalid Postgres'; assert '(SELECT auth.uid())' in sql; assert sql.count('public.is_admin()') >= 8; assert sql.count('TO authenticated') >= 19; assert 'CONCURRENTLY' not in sql; print(f'migration 015 structure OK: {sql.count(chr(10))} lines, {sql.count(\"CREATE POLICY\")} policies, {sql.count(\"CREATE TRIGGER\")} triggers')"</automated>
  </verify>
  <acceptance_criteria>
    - File `backend/migrations/015_two_scope_rls.sql` exists.
    - File starts with `-- Phase 1 / Migration 015: Two-scope RLS policies + scope-mutation trigger`.
    - `grep -c "CREATE OR REPLACE FUNCTION public.is_admin" backend/migrations/015_two_scope_rls.sql` returns 1.
    - `grep -c "LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public" backend/migrations/015_two_scope_rls.sql` returns 1.
    - `grep -c "GRANT EXECUTE ON FUNCTION public.is_admin() TO authenticated" backend/migrations/015_two_scope_rls.sql` returns 1.
    - `grep -c "CREATE OR REPLACE FUNCTION public.forbid_scope_mutation" backend/migrations/015_two_scope_rls.sql` returns 1.
    - `grep -c "NEW.scope IS DISTINCT FROM OLD.scope" backend/migrations/015_two_scope_rls.sql` returns 1.
    - `grep -c "USING ERRCODE = 'check_violation'" backend/migrations/015_two_scope_rls.sql` returns 1.
    - Tightened RETURN NEW check (bounded to forbid_scope_mutation function body, not the whole file): `cd backend && venv/Scripts/python -c "import re; sql = open('migrations/015_two_scope_rls.sql', encoding='utf-8').read(); body = re.search(r'forbid_scope_mutation\(\).*?\$\$;', sql, re.DOTALL); assert body is not None, 'forbid_scope_mutation function body not found'; assert body.group(0).count('RETURN NEW') == 1, f\"expected exactly 1 RETURN NEW in forbid_scope_mutation body, got {body.group(0).count(chr(82)+chr(69)+chr(84)+chr(85)+chr(82)+chr(78)+chr(32)+chr(78)+chr(69)+chr(87))}\"; print('RETURN NEW bounded check OK')"` exits 0. (Replaces the file-wide `grep -c "RETURN NEW" == 1` which would silently break if a future maintenance edit added another function with `RETURN NEW` to migration 015.)
    - File contains `DROP POLICY IF EXISTS "Users can view own documents" ON public.documents;` (verbatim).
    - File contains `DROP POLICY IF EXISTS "Users can insert own documents" ON public.documents;` (verbatim).
    - File contains `DROP POLICY IF EXISTS "Users can update own documents" ON public.documents;` (verbatim).
    - File contains `DROP POLICY IF EXISTS "Users can delete own documents" ON public.documents;` (verbatim).
    - File contains `DROP POLICY IF EXISTS "Users can view own chunks" ON public.document_chunks;` (verbatim).
    - File contains `DROP POLICY IF EXISTS "Users can insert own chunks" ON public.document_chunks;` (verbatim).
    - File contains `DROP POLICY IF EXISTS "Users can delete own chunks" ON public.document_chunks;` (verbatim).
    - `grep -cE "DROP POLICY IF EXISTS .* ON public\\.document_chunks" backend/migrations/015_two_scope_rls.sql` returns 3 (no UPDATE policy in 003 — only 3 chunks policies to drop).
    - Total CREATE POLICY count is exactly 19 (7 documents + 5 document_chunks + 7 folders): `grep -c "CREATE POLICY" backend/migrations/015_two_scope_rls.sql` returns 19.
    - Snake_case policy names present: documents_select, documents_insert_user, documents_insert_global, documents_update_user, documents_update_global, documents_delete_user, documents_delete_global; document_chunks_select, document_chunks_insert_user, document_chunks_insert_global, document_chunks_delete_user, document_chunks_delete_global; folders_select, folders_insert_user, folders_insert_global, folders_update_user, folders_update_global, folders_delete_user, folders_delete_global.
    - `grep -cE "document_chunks_(update_user|update_global)" backend/migrations/015_two_scope_rls.sql` returns 0 (no UPDATE on chunks).
    - All policies use `TO authenticated`: `grep -c "TO authenticated" backend/migrations/015_two_scope_rls.sql` returns at least 19.
    - All policies use the (SELECT auth.uid()) subquery form: `grep -c "(SELECT auth.uid())" backend/migrations/015_two_scope_rls.sql` returns at least 13 (used in SELECT/INSERT user/UPDATE user/DELETE user across 3 tables).
    - `grep -c "public.is_admin()" backend/migrations/015_two_scope_rls.sql` returns at least 8 (admin-gate uses: 3× INSERT global + 2× UPDATE global on docs/folders + 3× DELETE global = 8 minimum).
    - `grep -c "DROP TRIGGER IF EXISTS" backend/migrations/015_two_scope_rls.sql` returns 3 (one per table).
    - `grep -c "CREATE TRIGGER" backend/migrations/015_two_scope_rls.sql` returns 3.
    - Trigger names: `grep -c "documents_forbid_scope_mutation" backend/migrations/015_two_scope_rls.sql` >= 2; same for document_chunks_forbid_scope_mutation and folders_forbid_scope_mutation.
    - `grep -c "BEFORE UPDATE ON public.documents" backend/migrations/015_two_scope_rls.sql` returns 1.
    - `grep -c "BEFORE UPDATE ON public.document_chunks" backend/migrations/015_two_scope_rls.sql` returns 1.
    - `grep -c "BEFORE UPDATE ON public.folders" backend/migrations/015_two_scope_rls.sql` returns 1.
    - INVALID syntax check: `grep "WITH CHECK (scope = OLD" backend/migrations/015_two_scope_rls.sql` returns no matches (RLS WITH CHECK cannot reference OLD — must use trigger).
    - `grep -c "CONCURRENTLY" backend/migrations/015_two_scope_rls.sql` returns 0.
    - `grep -E "^(BEGIN|COMMIT|ROLLBACK);" backend/migrations/015_two_scope_rls.sql` returns no matches.
    - Python sanity check in `<verify>` exits 0 and prints the policy/trigger counts.
  </acceptance_criteria>
  <done>
    Migration 015 SQL file written, idempotent, contains is_admin() helper + forbid_scope_mutation() trigger function + drops of all 7 Episode-1 single-axis policies (4 documents + 3 chunks) by exact verbatim names + 19 new two-scope policies (7+5+7) + 3 BEFORE UPDATE triggers attached. All policies use TO authenticated and (SELECT auth.uid()) subquery form. All admin-gated policies use public.is_admin(). No invalid `WITH CHECK (scope = OLD.scope)` syntax. Migration is NOT yet applied (plan 07 handles the push).
  </done>
</task>

</tasks>

<verification>
Maps to .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-VALIDATION.md rows "RLS-01", "RLS-02", "RLS-03" (lines ~48-50). Falsifiable assertions 1-16 from RESEARCH.md § Validation Architecture (Group 1: RLS matrix; Group 2: Scope-mutation prevention) — those run in plan 08's test_two_scope_rls.py against the live DB after plan 07.

Static structural verification (this plan): the Python one-liner in `<automated>` validates ALL required policies, triggers, helper functions, drops, and the absence of the invalid `WITH CHECK (scope = OLD)` syntax. The 19-CREATE-POLICY count is the canary for completeness.
</verification>

<success_criteria>
- `backend/migrations/015_two_scope_rls.sql` exists with all 7 dropped Episode-1 policies, 19 new two-scope policies (7 documents + 5 chunks + 7 folders), is_admin() helper, forbid_scope_mutation() trigger function, and 3 BEFORE UPDATE triggers attached.
- File is idempotent (CREATE OR REPLACE for functions, DROP-before-CREATE for policies and triggers).
- All policies use the (SELECT auth.uid()) perf-wrap subquery form (Pitfall 5).
- All admin-gated policies use public.is_admin() (DRY).
- No invalid WITH CHECK (scope = OLD.scope) syntax (Pitfall 1 / Common Pitfall 1; trigger is the enforcement).
- chunks have no UPDATE policy (only SELECT/INSERT user/INSERT global/DELETE user/DELETE global = 5).
- All structural assertions in the acceptance criteria hold.
</success_criteria>

<output>
After completion, create `.planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-05-SUMMARY.md` recording: file created, line count, policy count breakdown (7 documents + 5 chunks + 7 folders = 19), trigger count (3), helper function count (2 — is_admin + forbid_scope_mutation), the deliberate snake_case naming shift (call it out for reviewers), the deliberate (SELECT auth.uid()) subquery form (call it out — first use in codebase), and a one-line note that the trigger is the canonical workaround for RLS-03 because Postgres RLS cannot reference OLD/NEW.
</output>
