# Phase 1 Research: Schema Foundation + Two-Scope RLS + Path Normalizer

**Researched:** 2026-05-02
**Domain:** Postgres schema design, two-axis RLS (user × scope), pg_trgm indexing, path canonicalization
**Confidence:** HIGH (Postgres primitives, in-tree migration patterns, official RLS docs); MEDIUM (Gemini SDK ergonomics — not exercised this phase)

> No CONTEXT.md exists for Phase 1 (`/gsd-discuss-phase` was not run). The phase is fully scoped by ROADMAP.md success criteria, REQUIREMENTS.md SCHEMA-01..05/RLS-01..04/FOLDER-01/TEST-04, and PROJECT.md key decisions. All 11 requirement IDs are addressed below.

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SCHEMA-01 | `documents.folder_path TEXT NOT NULL DEFAULT '/'` + CHECK regex `^/$\|^/[^/]+(/[^/]+)*$` | Section: Migration 012; canonical form regex verified in Pitfall 4, codebase ARCHITECTURE.md, STACK.md §2 |
| SCHEMA-02 | `documents.scope TEXT NOT NULL DEFAULT 'user'` + CHECK coupling scope/user_id | Section: Migration 012; coupling CHECK pattern from ARCHITECTURE.md lines 539-543 |
| SCHEMA-03 | `documents.content_markdown TEXT` + `content_markdown_status` enum (`pending`/`ready`/`failed`/`requires_user_reupload`) | Section: Migration 014; enum vs CHECK trade-off resolved in Decisions §2 |
| SCHEMA-04 | Thin `folders` table with unique `(scope, COALESCE(user_id, '00..0'), path)` | Section: Migration 013; COALESCE-in-UNIQUE syntax verified via Postgres docs (must be expression index, not table constraint) |
| SCHEMA-05 | `pg_trgm` enabled + GIN trigram on `documents.content_markdown` + `text_pattern_ops` btree on `folder_path` | Section: Migration 016; STACK.md §1-§2 confirms operator class choices |
| RLS-01 | SELECT policy `((scope='user' AND user_id=(SELECT auth.uid())) OR scope='global')` on documents/document_chunks/folders | Section: RLS Policy Catalog; Supabase `(SELECT auth.uid())` perf wrap verified |
| RLS-02 | Separate INSERT/UPDATE per scope; admin-only writes for `scope='global'` | Section: RLS Policy Catalog (12 policies = 4 per table × 3 tables) |
| RLS-03 | Forbid scope mutation on UPDATE | **CRITICAL FINDING** — `WITH CHECK (scope = OLD.scope)` is **invalid Postgres syntax**; use BEFORE UPDATE trigger instead. See Decisions §1 and Migration 015 |
| RLS-04 | `test_two_scope_rls.py` cross-user × cross-scope SELECT/INSERT/UPDATE/DELETE matrix | Section: Validation Architecture + Decisions §7; 3 users × 4 ops × 2 scopes = 24 cells (collapsible to 18) |
| FOLDER-01 | Single canonical `normalize_path()` helper, only chokepoint for path canonicalization | Section: Decisions §3; lives in `backend/app/services/folder_service.py` |
| TEST-04 | Cross-user × cross-scope matrix in `test_two_scope_rls.py`, registered in `test_all.py` SUITES | Section: Validation Architecture; new suite registered between `RLS` and `Settings` |
</phase_requirements>

---

## Project Constraints (from CLAUDE.md)

| Directive | Phase 1 Implication |
|-----------|---------------------|
| Python backend must use `venv` virtual environment | All migration runs and test runs prefixed with `cd backend && venv/Scripts/python ...` |
| No LangChain, no LangGraph — raw SDK calls only | N/A this phase (no LLM code) |
| Use Pydantic for structured LLM outputs | N/A this phase |
| All tables need RLS — users only see their own data | **Mandatory** — `folders` table MUST have `ENABLE ROW LEVEL SECURITY` and a policy set; the two-scope SELECT policy satisfies "users only see their own data" by adding `OR scope='global'` |
| Save plans to `.agent/plans/` | Plans go to `.agent/plans/` per CLAUDE.md naming `{seq}.{name}.md` (note: GSD framework writes to `.planning/phases/`; planner should clarify which is canonical) |
| Tests must NEVER delete all user data | `test_two_scope_rls.py` MUST track created IDs and clean up only those — never blanket DELETE on `documents`/`folders` |
| Do NOT run the full test suite automatically | Phase 1 verification gates run only the new `test_two_scope_rls.py` plus targeted RLS smoke; full suite only when user requests |
| **Stack: Supabase Postgres + pgvector + supabase-py + FastAPI** | All schema work is Postgres DDL + supabase-py service layer; no new languages/frameworks |

---

## Summary

Phase 1 is a **pure-schema + one helper-function** phase. There is no UI work, no LLM code, no SSE plumbing. The "hard" parts are not the SQL itself — they are: (a) **RLS-03 cannot be implemented as written in the original phase brief** (Postgres RLS forbids `OLD.col` references in `WITH CHECK`; we need a BEFORE UPDATE trigger or a SECURITY DEFINER function), (b) the `run_migrations.py` driver runs each migration in a transaction (`autocommit = False`), so `CREATE INDEX CONCURRENTLY` is **not available** — we must accept short lock windows or split index creation into a manual step, and (c) the unique constraint on `folders` involves a `COALESCE` expression which forces a `CREATE UNIQUE INDEX ON ... (COALESCE(...))` instead of a table-level `UNIQUE` constraint.

The remaining work is mechanical: five migrations (012–016), a 30-line Python `normalize_path()` helper, and a cross-user × cross-scope test matrix. The Episode 1 codebase already has the patterns we extend: migration 003 establishes the documents/document_chunks RLS shape, migration 005 establishes `profiles.is_admin` and the `EXISTS (SELECT 1 FROM profiles WHERE id=auth.uid() AND is_admin)` admin-gate pattern, and `backend/app/auth.py:get_admin_user` is the dependency to reuse in the (Phase 3) folders router. This phase only puts the **DB-level** policies in place; the router-level admin gate is Phase 3's job.

**Primary recommendation:** Land migrations 012 → 013 → 014 → 015 → 016 in that order; ship `normalize_path()` in `backend/app/services/folder_service.py` with no other folder logic (CRUD is Phase 3); ship `test_two_scope_rls.py` with a 3-user (admin, userA, userB) × 4-op × 2-scope matrix and gate Phase 2 on its 100% pass.

---

## Existing Codebase Anchors

The planner should know these files exist and how they shape what Phase 1 can do:

### Migration runner — runs each file in a transaction

`backend/scripts/run_migrations.py` (lines 38-58):

```python
conn = psycopg2.connect(db_url)
conn.autocommit = False
try:
    for f in files:
        sql = f.read_text(encoding="utf-8")
        ...
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()    # ← per-file commit
            print("OK")
        except Exception as e:
            conn.rollback()  # ← failure rolls back THIS migration
            ...
            return 2
```

**Implication:** `CREATE INDEX CONCURRENTLY` raises `ERROR: CREATE INDEX CONCURRENTLY cannot run inside a transaction block`. We have three options:
1. Use plain `CREATE INDEX` (acquires `SHARE` lock, blocks writes for the duration). For an empty/small `documents` table at Episode 2 boot, lock window is sub-second — **acceptable**.
2. Add a `--manual` migration file that the script skips (rename to `.sql.manual` and document a "run-by-hand" step).
3. Modify `run_migrations.py` to set `autocommit=True` for files containing `CREATE INDEX CONCURRENTLY`. **Out of scope for Phase 1** — adds complexity without clear win.

**Recommendation:** Option 1. At Episode 2 boot the documents table has at most a few hundred rows per user; index build is fast and lock is brief. Document this in the migration header comment. [VERIFIED: `backend/scripts/run_migrations.py:39`]

### Existing RLS pattern (migration 003)

`backend/migrations/003_byo_retrieval.sql:28-32`:

```sql
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Users can view own documents"   ON documents FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Users can insert own documents" ON documents FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users can update own documents" ON documents FOR UPDATE USING (auth.uid() = user_id);
CREATE POLICY "Users can delete own documents" ON documents FOR DELETE USING (auth.uid() = user_id);
```

**Implications for Migration 015:** We must `DROP POLICY IF EXISTS "Users can view own documents" ON documents;` (and the three siblings) **explicitly** before creating the new two-scope policies. Same for `document_chunks` (lines 51-53 of the same file: 3 policies — view/insert/delete; **no UPDATE policy** on `document_chunks`, which is correct since chunks are insert-and-delete-only, never updated). The Phase 1 plan for migration 015 must drop the existing 4 policies on documents and 3 on document_chunks, then create 7 (or 8 with UPDATE) per the catalog below.

### Admin pattern (migration 005)

`backend/migrations/005_profiles_and_settings.sql:23-29`:

```sql
CREATE POLICY "Admins read all profiles"
    ON public.profiles FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.profiles p
            WHERE p.id = auth.uid() AND p.is_admin = true
        )
    );
```

**Implication:** The exact admin-gate predicate is `EXISTS (SELECT 1 FROM public.profiles WHERE id = auth.uid() AND is_admin = true)`. Phase 1 reuses this verbatim inside the `documents_insert_global` and similar policies. We **could** factor it into a `public.is_admin()` SQL function (per STACK.md §3 recommendation) — that is cleaner and DRY-er across 6 admin-gated policies. **Recommendation: add `public.is_admin()` in migration 015** and use it everywhere.

### Existing `documents` schema (what we're extending)

`backend/migrations/003_byo_retrieval.sql:12-23` plus `006_record_manager.sql` adds `content_hash` + the `(user_id, file_name)` UNIQUE constraint. Migration 007 adds `metadata JSONB`. So at the start of Phase 1, `documents` has:
- `id`, `user_id NOT NULL`, `file_name`, `file_size`, `mime_type`, `status`, `error_message`, `created_at`, `updated_at` (from 003)
- `content_hash`, plus `UNIQUE (user_id, file_name)` (from 006)
- `metadata JSONB` with GIN index (from 007)

**Implication:** Migration 012 must **DROP the existing `documents_user_filename_unique` constraint** (which assumes one filename per user) and replace it with a multi-column unique that respects scope+folder_path. Per FOLDER-05 (Phase 3), the new dedup key is `(scope, user_id, folder_path, file_name, hash)` — so the table-level unique should match. Phase 1 establishes the **database-level** uniqueness; Phase 3's `record_manager.py` extension uses it. **Recommend:** drop the old constraint in migration 012; replace with a unique expression index that handles NULL `user_id` (for global): `UNIQUE (scope, COALESCE(user_id, '00..0'::uuid), folder_path, file_name)`. Hash-level dedup remains in app code (record_manager) — DB just enforces "no two rows with same path+name in same scope/user."

### Test harness

`backend/scripts/test_helpers.py` provides `get_auth_token(email, password)` (lines 56-86), `auth_headers(token)`, `track_thread`/`track_file` for cleanup, and `TEST_USER_A`/`TEST_USER_B` fixtures (lines 18-19). **There is currently no admin test user.** Phase 1's `test_two_scope_rls.py` needs a third user with `is_admin=true`; either (a) add `TEST_USER_ADMIN` fixture and document the manual `UPDATE profiles SET is_admin=true WHERE email=...` step, or (b) the test SQL-elevates one of A/B mid-test via service-role and reverts. **Recommendation: option (a)** — add `TEST_USER_ADMIN = {"email": "admin@test.com", "password": "..."}` and document the one-time `is_admin=true` setup in the test docstring.

`backend/scripts/test_all.py` (lines 12-23) imports each suite by name; `SUITES` list at line 25 is the registration point. **Phase 1 must add `import test_two_scope_rls` and append `("Two-Scope RLS", test_two_scope_rls)` to SUITES** so it runs in the full sweep.

`backend/scripts/test_rls.py` (existing) covers single-axis user-isolation today; **do NOT replace it** — extend with a sibling `test_two_scope_rls.py`. The existing test stays the smoke for "user A can't see user B's data"; the new test adds the scope axis.

### Service layer convention

All services live in `backend/app/services/*.py` (snake_case modules with module-level functions). Per `.planning/codebase/CONVENTIONS.md` lines 153-167: "Backend service functions: explicit parameters over globals (e.g., pass `user_id`, `supabase_client`)." The `normalize_path()` helper is a pure function — no parameters beyond the input string, no DB access — so it ships as a module-level function in `backend/app/services/folder_service.py` and is importable by Phase 3's CRUD code via `from app.services.folder_service import normalize_path`.

---

## Key Decisions and Recommendations

### §1. RLS-03: Forbidding scope mutation — **`WITH CHECK (scope = OLD.scope)` is INVALID; use a trigger**

**The brief says:** `WITH CHECK (scope = OLD.scope)` on UPDATE policies forbids scope mutation.

**Reality:** Postgres RLS policy expressions do **not support `OLD` or `NEW` row aliases**. From the Postgres docs (`CREATE POLICY`): "the `check_expression` is evaluated against the proposed new contents of the row, not the original contents." Bare column refs in `WITH CHECK` mean the new row; in `USING` they mean the existing row. There is **no syntax to compare old vs. new** inside a policy. `[CITED: postgresql.org/docs/current/sql-createpolicy.html]` `[CITED: github.com/orgs/supabase/discussions/37459]`

**Consequence:** Attempting `WITH CHECK (scope = OLD.scope)` raises `ERROR: missing FROM-clause entry for table "old"` at policy creation time. We need a different mechanism.

**Recommended workaround — BEFORE UPDATE trigger that raises on scope change:**

```sql
CREATE OR REPLACE FUNCTION public.forbid_scope_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.scope IS DISTINCT FROM OLD.scope THEN
    RAISE EXCEPTION 'Scope mutation forbidden: cannot change scope from % to % (use delete + admin re-insert instead)',
                    OLD.scope, NEW.scope
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER documents_forbid_scope_mutation
  BEFORE UPDATE ON documents
  FOR EACH ROW EXECUTE FUNCTION public.forbid_scope_mutation();

-- Repeat trigger for document_chunks and folders.
```

**Why a trigger and not `SECURITY DEFINER` function in `WITH CHECK`:**
- `SECURITY DEFINER` functions inside WITH CHECK are awkward (you'd have to fetch the OLD row inside the function via SELECT, which is racy and slow).
- A BEFORE UPDATE trigger is the **canonical Postgres pattern** for "forbid changing column X" and is what Supabase community recommends. `[CITED: github.com/orgs/supabase/discussions/37459]`
- The trigger fires **after RLS policies pass** but **before the row is written**, so it's a clean defense-in-depth layer.

**This is a critical correction the planner MUST apply.** The roadmap, requirements, and pitfalls docs all say "WITH CHECK (scope = OLD.scope)" — those are aspirational; the implementation is a trigger.

`[VERIFIED: postgresql.org/docs/current/sql-createpolicy.html]` HIGH

---

### §2. SCHEMA-03: `content_markdown_status` — TEXT + CHECK, not ENUM

**Trade-off:**
- ENUM type (`CREATE TYPE markdown_status AS ENUM (...)`) is type-safe at the catalog level, but `ALTER TYPE ... ADD VALUE` is locked to single transactions and adding values requires careful migration. STACK.md §3 explicitly flags this: "ENUM type for `scope` — painful `ALTER TYPE` migrations." Same logic applies to `content_markdown_status`.
- TEXT + CHECK constraint is equally safe at insert time, easier to evolve (just `ALTER TABLE ... DROP CONSTRAINT ... ADD CONSTRAINT ...` with a new value list).

**Recommendation: TEXT + CHECK.**

```sql
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS content_markdown        TEXT,
  ADD COLUMN IF NOT EXISTS content_markdown_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (content_markdown_status IN ('pending', 'ready', 'failed', 'requires_user_reupload'));
```

**Allowed values verified against REQUIREMENTS.md SCHEMA-03:** `pending` / `ready` / `failed` / `requires_user_reupload`. (Note: ROADMAP.md additional context mentioned `'ok'` — that is **wrong**; the canonical name is `'ready'` per REQUIREMENTS.md and matches the existing `documents.status` enum vocabulary from migration 003.)

**Default is `'pending'`:** Existing Episode 1 documents get `'pending'` automatically (column added with default). Phase 2's backfill flips them to `'ready'` after Docling re-run, or `'requires_user_reupload'` if the Storage blob is missing, or `'failed'` if Docling errors. **New uploads in Phase 2 also start at `'pending'`** and the ingest flow flips to `'ready'` synchronously after writing `content_markdown` — Phase 2's job, not Phase 1's.

`[VERIFIED: REQUIREMENTS.md:12 SCHEMA-03]` `[VERIFIED: STACK.md §3 ENUM warning]` HIGH

---

### §3. FOLDER-01: `normalize_path()` semantics

**Canonical form:**
- Leading `/` always.
- Trailing `/` only on root (`/`).
- Separator `/`.
- No double slashes (`//` → `/`).
- No backslashes (Windows).
- Examples: `/`, `/projects`, `/projects/2026`, `/projects/2026/floor-plans`.

**DB CHECK regex:** `^/$|^/[^/]+(/[^/]+)*$`

Verifying with the requested round-trip cases:
| Input | After normalize → | CHECK pass? |
|-------|--------------------|-------------|
| `/` | `/` | ✓ (matches `^/$`) |
| `/a/b` | `/a/b` | ✓ |
| `/a/b/c` | `/a/b/c` | ✓ |
| `/A/B` | `/A/B` (preserve case) | ✓ |
| `/a//b` | `/a/b` (collapse) | ✓ |
| `a/b` | `/a/b` (prepend leading) | ✓ |
| `/a/b/` | `/a/b` (strip trailing, except root) | ✓ |
| `\a\b` | `/a/b` (replace backslashes) | ✓ |
| `` (empty) | `/` (treat as root) | ✓ |
| `/a/./b` | `/a/b` (collapse `.`)? **OPEN** | — |
| `/a/../b` | reject? **OPEN** | — |

**Open semantics flagged for planner:** Should `normalize_path()` accept and resolve `..` / `.` segments, or reject them? Path traversal via `..` is a security pitfall (per Pitfalls §security: "LLM passes path traversal like `../other-user-folder`"). **Recommendation: reject any segment that is `..`, `.`, or contains backslash after normalization** — raise `ValueError("Invalid path segment")`. The DB CHECK regex already rejects `..` (since `..` has no `/` inside, it passes `[^/]+` — so DB does NOT reject it; **Python validation is the enforcement layer** for `..`).

**Case sensitivity:** **Preserve case.** Postgres compares case-sensitively; lowercasing would silently merge `/Projects` and `/projects`. Users entering `/Projects/2026` should see exactly that; if they want lowercase they enter lowercase.

**Unicode normalization:** **NFC.** Two visually-identical strings can have different byte sequences if one is precomposed and the other decomposed. NFC ensures `unicodedata.normalize("NFC", path)` gives canonical form. Low-frequency concern but free to implement.

**Reference Python implementation:**

```python
# backend/app/services/folder_service.py
import re
import unicodedata

_CANONICAL_PATH_RE = re.compile(r"^/$|^/[^/]+(/[^/]+)*$")
_FORBIDDEN_SEGMENTS = {"..", "."}

def normalize_path(p: str | None) -> str:
    """Canonicalize a folder path string.

    Canonical form: leading slash, no trailing slash (except root '/'),
    no double slashes, no backslashes, NFC-normalized Unicode, case preserved.

    Raises ValueError on path traversal attempts ('..', '.') or
    segments containing backslashes after normalization.
    """
    if p is None or p == "":
        return "/"
    s = unicodedata.normalize("NFC", p)
    s = s.replace("\\", "/")
    while "//" in s:
        s = s.replace("//", "/")
    if not s.startswith("/"):
        s = "/" + s
    if len(s) > 1 and s.endswith("/"):
        s = s.rstrip("/")
    if s == "":
        s = "/"
    # Validate segments
    if s != "/":
        for seg in s.lstrip("/").split("/"):
            if seg in _FORBIDDEN_SEGMENTS or seg == "":
                raise ValueError(f"Invalid path segment: {seg!r} in {p!r}")
    if not _CANONICAL_PATH_RE.match(s):
        raise ValueError(f"Path failed canonical form check: {s!r}")
    return s
```

**Location:** `backend/app/services/folder_service.py`. Per `.planning/codebase/CONVENTIONS.md` and STRUCTURE.md §"Where to Add New Code: New Backend Service/Tool", services live in `backend/app/services/{name}.py`. The Phase 3 folder CRUD adds `list_folder`, `create_folder`, `move_document`, `rename_folder`, `delete_folder` to the same file; Phase 1 ships only `normalize_path` (and the regex constant) so the file exists and is importable.

`[ASSUMED]` Round-trip tests run against this implementation; the planner should write `test_normalize_path.py` (or fold tests into `test_two_scope_rls.py`'s setup). MEDIUM (semantics fully specified above; minor disagreement risk on `..` handling).

---

### §4. SCHEMA-05: Indexes — exact DDL, including pg_trgm placement

**`pg_trgm` extension:** Enable in **migration 012 (the earliest)**, not migration 016. Reason: extensions are "slow to enable on first connect" only in cold databases; in Supabase the cost is sub-second and one-time. Putting it early lets later migrations reference `gin_trgm_ops` without ordering surprise.

**GIN trigram index on `documents.content_markdown`:**

```sql
CREATE INDEX IF NOT EXISTS documents_content_markdown_trgm_idx
  ON documents USING gin (content_markdown gin_trgm_ops);
```

Justification: STACK.md §1 verified that `gin_trgm_ops` accelerates `LIKE`/`ILIKE`/`~`/`~*` whenever the pattern has ≥3 literal chars. Phase 4's `grep` will use this. `[VERIFIED: postgresql.org/docs/current/pgtrgm.html]`

**btree on `folder_path` using `text_pattern_ops`:**

```sql
CREATE INDEX IF NOT EXISTS documents_folder_path_prefix_idx
  ON documents (folder_path text_pattern_ops);
```

Justification: Default-collation btree on `folder_path` will **NOT** be used for `LIKE 'projects/%'` queries when DB locale is non-`C` (Supabase default is `en_US.UTF-8`). `text_pattern_ops` forces byte-wise comparison, enabling the index. `[VERIFIED: postgresql.org/docs/current/indexes-opclass.html]` HIGH.

**Composite `(scope, COALESCE(user_id, '00..0'::uuid), folder_path)` for scope-filtered prefix queries — DEFER to Phase 4.**

The roadmap says `text_pattern_ops` btree on `folder_path` "lands here" (Phase 1). The composite index that Phase 4's `tree`/`glob`/`list_files` will benefit from depends on the actual query shapes Phase 4 produces. **Recommendation:** Phase 1 ships the simple `(folder_path text_pattern_ops)` btree; Phase 4 adds the composite `(scope, user_id, folder_path text_pattern_ops)` if `EXPLAIN ANALYZE` shows it's needed. Adding it speculatively now risks index bloat and slows writes. (ARCHITECTURE.md line 238 sketches the composite — but for Phase 1 the simple form is sufficient.)

**Optional but recommended additional index — trigram on `folder_path`:**

```sql
CREATE INDEX IF NOT EXISTS documents_folder_path_trgm_idx
  ON documents USING gin (folder_path gin_trgm_ops);
```

For Phase 4's glob `**/*foo*` queries that are not pure-prefix. STACK.md §2 recommends both indexes coexist. **Phase 1 may or may not include this — recommend YES, ship together with the markdown trigram index** since pg_trgm is enabled either way and the build is cheap on a small column.

`[VERIFIED: STACK.md §2]` HIGH

---

### §5. SCHEMA-04: Folders table + unique constraint with COALESCE

**Critical Postgres syntax constraint:** `UNIQUE (scope, COALESCE(user_id, '00..0'::uuid), path)` is **NOT valid as a table constraint** — table-level `UNIQUE` accepts only column lists, not expressions. The expression form must be a `CREATE UNIQUE INDEX`. `[VERIFIED: postgresql.org/docs/current/indexes-unique.html]`

**Correct DDL:**

```sql
CREATE TABLE IF NOT EXISTS public.folders (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scope      TEXT NOT NULL CHECK (scope IN ('user', 'global')),
  user_id    UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  path       TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- Coupling: user_id required iff scope='user'
  CONSTRAINT folders_scope_user_id_consistency CHECK (
    (scope = 'user'   AND user_id IS NOT NULL)
    OR (scope = 'global' AND user_id IS NULL)
  ),
  -- Canonical-form path
  CONSTRAINT folders_path_canonical CHECK (
    path = '/' OR path ~ '^/[^/]+(/[^/]+)*$'
  )
);

-- Expression-based unique: COALESCE handles NULL user_id for global rows
CREATE UNIQUE INDEX IF NOT EXISTS folders_scope_user_path_unique
  ON public.folders (scope, COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid), path);

CREATE INDEX IF NOT EXISTS folders_scope_user_idx
  ON public.folders (scope, user_id);
```

**The "all-zeros UUID" sentinel** is required because Postgres treats NULLs as distinct in unique indexes by default (you'd allow infinite duplicate global rows otherwise). The sentinel forces NULL → `00..0`, making them comparable. `[VERIFIED: postgresql.org/docs/current/indexes-unique.html]`

**Note: PG15+ has `NULLS NOT DISTINCT`** which is an alternative: `CREATE UNIQUE INDEX ... ON folders (scope, user_id, path) NULLS NOT DISTINCT;`. Cleaner but requires PG15. **Supabase runs PG15+** (verified — Supabase shipped PG15 in 2023 and PG17 in 2025). **Recommendation: prefer `NULLS NOT DISTINCT` if confirmed, fall back to COALESCE sentinel otherwise.** Planner should decide; the COALESCE form is universally compatible.

**FK from `documents.folder_path` → `folders.path`?** **NO.** Per ARCHITECTURE.md Pattern 2 line 224 ("hybrid model — `folders` table for empty-folder tracking + `folder_path` denormalized on `documents` — has no foreign-key relationship between them. There's no DB-level concept of 'this folder owns these documents.'") and Pitfall 5: orphan/cascade is an application-level decision (Phase 3). Keeping `folder_path` as plain TEXT lets users move documents into folders that don't exist yet (auto-implicit) and avoids cascade-delete surprises.

**Folders RLS:** `ENABLE ROW LEVEL SECURITY` plus the same 4-policy-per-action shape as documents (see Catalog below).

`[VERIFIED: postgresql.org/docs/current/indexes-unique.html]` `[VERIFIED: ARCHITECTURE.md Pattern 2]` HIGH

---

### §6. Migration ordering — 012 → 013 → 014 → 015 → 016

**Why this order:**
1. **012 first:** Adds columns `folder_path`, `scope`, drops the old `(user_id, file_name)` UNIQUE constraint, replaces with scope-aware unique. Makes `user_id` NULLABLE. Adds CHECK coupling. **Also enables `pg_trgm`** here (early, free).
2. **013 second:** Creates `folders` table with its own RLS, CHECK, and unique-expression index. Depends on 012 only because of Postgres extension dependency.
3. **014 third:** Adds `documents.content_markdown` (nullable) + `content_markdown_status` (NOT NULL DEFAULT `'pending'`). Independent of folders.
4. **015 fourth:** Drops Episode-1 RLS policies on `documents` and `document_chunks`; creates 8 new policies on each (4 ops × 2 scopes); creates the `forbid_scope_mutation()` trigger function and attaches BEFORE UPDATE triggers to all three tables. Depends on 012 (scope column must exist).
5. **016 fifth:** Creates the GIN trigram index on `documents.content_markdown` and `documents.folder_path` + the `text_pattern_ops` btree on `folder_path`. Depends on 012 (folder_path) and 014 (content_markdown) and 012 again (pg_trgm).

**Backfill safety:** All column adds use `DEFAULT` values for existing rows:
- `documents.folder_path TEXT NOT NULL DEFAULT '/'` — every existing row becomes `'/'` instantly. Postgres 11+ stores the default as a metadata pointer (no full table rewrite). `[VERIFIED]`
- `documents.scope TEXT NOT NULL DEFAULT 'user'` — same.
- `documents.content_markdown TEXT` (nullable) — no default needed.
- `documents.content_markdown_status TEXT NOT NULL DEFAULT 'pending'` — same metadata pointer trick.

**`ALTER COLUMN user_id DROP NOT NULL`:** Safe. No data movement. Existing rows keep their non-null user_id.

**`DROP CONSTRAINT documents_user_filename_unique`:** Safe. No data is changed; just removes the index that backed it.

**`CREATE UNIQUE INDEX (scope, COALESCE(user_id,...), folder_path, file_name)` on documents:** Builds against existing rows. Will succeed because all existing rows are scope='user' with user_id NOT NULL and folder_path='/' (defaults), and the existing `(user_id, file_name)` constraint already prevented duplicates. **No conflict possible.**

**Cross-migration RLS consideration:** Between 012 and 015 the existing single-axis RLS policies are still in place. Reads still work; writes still work; the new `scope` column is silently ignored by old policies (it's not in their predicates). **The window of "old RLS policies looking at new column" is benign** — nothing breaks. Migration 015 cleanly swaps the policies.

**Idempotency:** Every DDL uses `IF NOT EXISTS` / `IF EXISTS` / `CREATE OR REPLACE` so the migration set can be re-run. Verified pattern across existing migrations 003-011.

`[VERIFIED: existing migration patterns 003-011]` HIGH

---

### §7. TEST-04: Cross-user × cross-scope test matrix

**Test users (3 users, not 2):**
- `TEST_USER_A` — regular user (existing fixture)
- `TEST_USER_B` — regular user (existing fixture)
- `TEST_USER_ADMIN` — new fixture, has `profiles.is_admin = true`

**Setup:** Test docstring documents one-time setup: after first signup, run `UPDATE profiles SET is_admin = true WHERE email = 'admin@test.com';` via the SQL editor. Helper: `h.get_admin_token()` analogous to `h.get_auth_token()`.

**Matrix shape (smallest covering set):**

| Actor | Op | Target row | Expected |
|-------|----|-----------| --------|
| A | SELECT | A's user-scope doc | ✅ visible |
| A | SELECT | B's user-scope doc | ❌ invisible (RLS) |
| A | SELECT | global doc | ✅ visible |
| A | INSERT | scope='user', user_id=A | ✅ allowed |
| A | INSERT | scope='user', user_id=B | ❌ rejected (RLS WITH CHECK) |
| A | INSERT | scope='global' | ❌ rejected (admin-only) |
| A | UPDATE | A's user-scope doc, set non-scope field | ✅ allowed |
| A | UPDATE | A's user-scope doc, **set scope='global'** | ❌ rejected (trigger) |
| A | UPDATE | B's user-scope doc | ❌ rejected (RLS USING) |
| A | UPDATE | global doc | ❌ rejected (admin-only) |
| A | DELETE | A's user-scope doc | ✅ allowed |
| A | DELETE | B's user-scope doc | ❌ rejected (RLS) |
| A | DELETE | global doc | ❌ rejected (admin-only) |
| ADMIN | INSERT | scope='global', user_id=NULL | ✅ allowed |
| ADMIN | INSERT | scope='global', user_id=ADMIN.id | ❌ rejected (CHECK constraint scope/user_id coupling) |
| ADMIN | UPDATE | global doc, set scope='user' | ❌ rejected (trigger) |
| ADMIN | DELETE | global doc | ✅ allowed |
| B | INSERT | folders row scope='user', user_id=B, path='/x' | ✅ allowed |
| B | (race test) | INSERT same `(scope=user, user_id=B, path='/x')` twice | second fails on unique index |

**18 distinct cells, ~22 actual tests (some get repeated for chunks/folders).** Each table (documents, document_chunks, folders) gets its own subsection. Use `h.section()` per table.

**Shape:** New file `backend/scripts/test_two_scope_rls.py` following the `run() → return (passed, failed)` pattern from `test_rls.py`. Track every created ID, clean up in `finally`. **Per CLAUDE.md: never blanket-delete.**

**Registration:** Append to `test_all.py` SUITES list:

```python
import test_two_scope_rls
SUITES = [
    ...,
    ("RLS", test_rls),
    ("Two-Scope RLS", test_two_scope_rls),  # NEW
    ("Settings", test_settings),
    ...,
]
```

**Verification approach:** Tests use **direct supabase-py calls with the user's anon JWT** (not service-role) so RLS actually applies. The existing `test_helpers.py` pattern uses HTTP API calls to backend routes — but Phase 1 has no folders router yet (Phase 3). So `test_two_scope_rls.py` must talk **directly to Supabase** via supabase-py with the user's JWT, not through the FastAPI backend. This requires importing `from supabase import create_client` and instantiating `create_client(SUPABASE_URL, SUPABASE_ANON_KEY)`, then calling `.auth.set_session({"access_token": token, ...})` or passing the JWT in headers. Document this pattern in the test file's docstring.

**Alternative pattern (cleaner but requires Phase 3):** Wait until Phase 3 lands the folders router and then write the matrix using HTTP API. **Reject** — TEST-04 is the gate for Phase 2; cannot wait for Phase 3.

`[VERIFIED: existing test_rls.py and test_helpers.py patterns]` HIGH

---

### §8. CONCURRENT INDEX safety

**Confirmed:** `run_migrations.py` runs each migration **inside a transaction** (`conn.autocommit = False`, explicit `conn.commit()` per file at line 52). `CREATE INDEX CONCURRENTLY` raises `cannot run inside a transaction block`. `[VERIFIED: backend/scripts/run_migrations.py:39, 52]`

**Decision: use plain `CREATE INDEX` (non-concurrent).**

Justification:
- At Episode 2 boot the `documents` table holds at most a few hundred rows per user (per ARCHITECTURE.md "per-user document counts in the low thousands"). A GIN trigram build over a few hundred TEXT rows takes <1 second.
- Plain `CREATE INDEX` acquires `SHARE` lock — **blocks writes** for the build duration but allows reads. Sub-second window is acceptable for a development-stage product.
- For production at scale (10k+ docs per user), the operator can manually run `CREATE INDEX CONCURRENTLY` outside the migration runner; document this in the migration 016 header comment.

**Alternative considered and rejected:** Modify `run_migrations.py` to detect `CONCURRENTLY` and switch to `autocommit=True`. Out of scope for Phase 1 — adds a moving part for marginal benefit at current scale.

`[VERIFIED: postgresql.org/docs/current/sql-createindex.html (CONCURRENTLY restrictions)]` HIGH

---

### §9. Validation Architecture (Nyquist gate) — see dedicated section below

The Nyquist-gate validation architecture is detailed in its own section per the GSD framework requirement. Summary: 5 validation categories, ~30 falsifiable assertions, all runnable via the new `test_two_scope_rls.py` plus a small `test_normalize_path.py` (or merged) plus `EXPLAIN ANALYZE` smoke against a seeded fixture.

---

## Migration Plan (012–016)

### Migration 012 — Folder path + scope columns + pg_trgm enable

**File:** `backend/migrations/012_folder_path_and_scope.sql`

**Purpose:** Add `folder_path` and `scope` columns to `documents` and `document_chunks`. Make `user_id` NULLABLE on both. Add CHECK coupling. Replace dedup unique constraint. Enable `pg_trgm`.

**DDL skeleton:**

```sql
-- Phase 1 / Migration 012: folder_path + scope columns + pg_trgm
-- Adds the two new axes to documents/document_chunks and prepares for two-scope RLS.

-- Enable trigram extension up front so later migrations can reference gin_trgm_ops.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ── documents ──
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS folder_path TEXT NOT NULL DEFAULT '/',
  ADD COLUMN IF NOT EXISTS scope       TEXT NOT NULL DEFAULT 'user'
    CHECK (scope IN ('user', 'global'));

-- Make user_id nullable for scope='global' rows
ALTER TABLE documents ALTER COLUMN user_id DROP NOT NULL;

-- Coupling CHECK: user_id required iff scope='user'
ALTER TABLE documents
  ADD CONSTRAINT documents_scope_user_id_consistency CHECK (
    (scope = 'user'   AND user_id IS NOT NULL)
    OR (scope = 'global' AND user_id IS NULL)
  );

-- Canonical-form folder_path
ALTER TABLE documents
  ADD CONSTRAINT documents_folder_path_canonical CHECK (
    folder_path = '/' OR folder_path ~ '^/[^/]+(/[^/]+)*$'
  );

-- Replace old (user_id, file_name) unique with scope+folder-aware unique
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_user_filename_unique;

CREATE UNIQUE INDEX IF NOT EXISTS documents_scope_user_path_filename_unique
  ON documents (
    scope,
    COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid),
    folder_path,
    file_name
  );

-- ── document_chunks ──
ALTER TABLE document_chunks
  ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'user'
    CHECK (scope IN ('user', 'global'));

ALTER TABLE document_chunks ALTER COLUMN user_id DROP NOT NULL;

ALTER TABLE document_chunks
  ADD CONSTRAINT document_chunks_scope_user_id_consistency CHECK (
    (scope = 'user'   AND user_id IS NOT NULL)
    OR (scope = 'global' AND user_id IS NULL)
  );
```

**What it does NOT do:** Does not touch RLS policies (that's 015), does not create indexes on the new columns (that's 016), does not create the folders table (that's 013).

**Rollback notes:** Reversible via `ALTER TABLE ... DROP COLUMN folder_path, DROP COLUMN scope; ALTER COLUMN user_id SET NOT NULL;` (the last only safe if no scope='global' rows exist yet, which they don't until Phase 2). Restoring the old unique constraint requires re-checking for new duplicates.

---

### Migration 013 — Folders table + unique-expression index + RLS-enable

**File:** `backend/migrations/013_folders_table.sql`

**Purpose:** Create the thin `folders` side table for empty-folder tracking. Includes the COALESCE-based unique expression index and `ENABLE ROW LEVEL SECURITY` (policies in 015).

**DDL skeleton:**

```sql
-- Phase 1 / Migration 013: folders table + unique constraint
-- Side table for empty-folder tracking. Documents reference folders
-- only by string path, not by FK (see ARCHITECTURE.md Pattern 2).

CREATE TABLE IF NOT EXISTS public.folders (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scope      TEXT NOT NULL CHECK (scope IN ('user', 'global')),
  user_id    UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  path       TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT folders_scope_user_id_consistency CHECK (
    (scope = 'user'   AND user_id IS NOT NULL)
    OR (scope = 'global' AND user_id IS NULL)
  ),
  CONSTRAINT folders_path_canonical CHECK (
    path = '/' OR path ~ '^/[^/]+(/[^/]+)*$'
  )
);

-- Unique with COALESCE sentinel for nullable user_id
CREATE UNIQUE INDEX IF NOT EXISTS folders_scope_user_path_unique
  ON public.folders (
    scope,
    COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid),
    path
  );

CREATE INDEX IF NOT EXISTS folders_scope_user_idx
  ON public.folders (scope, user_id);

-- Enable RLS now; policies land in migration 015 alongside the
-- two-scope policies for documents and document_chunks (kept together
-- so the policy catalog is reviewable in one file).
ALTER TABLE public.folders ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.folders TO authenticated;
```

**What it does NOT do:** No policies created. No data inserted (migration 014's backfill creates `'/'` if anything; in practice we don't pre-populate — Phase 3 creates folders on demand).

**Rollback:** `DROP TABLE public.folders;` — no FKs into it from anywhere yet.

---

### Migration 014 — content_markdown column + status enum

**File:** `backend/migrations/014_content_markdown.sql`

**Purpose:** Add the `content_markdown TEXT` column (nullable) and `content_markdown_status` (TEXT + CHECK, NOT NULL DEFAULT `'pending'`).

**DDL skeleton:**

```sql
-- Phase 1 / Migration 014: content_markdown column + status enum
-- The column is nullable; backfill happens in Phase 2.
-- The status defaults to 'pending'; ingestion code flips to 'ready'.

ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS content_markdown        TEXT,
  ADD COLUMN IF NOT EXISTS content_markdown_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (content_markdown_status IN (
      'pending',
      'ready',
      'failed',
      'requires_user_reupload'
    ));

-- Optional helper index for "find all pending re-index" queries (Phase 2 backfill)
CREATE INDEX IF NOT EXISTS documents_content_markdown_status_idx
  ON documents (content_markdown_status)
  WHERE content_markdown_status <> 'ready';
```

**What it does NOT do:** No backfill — that's Phase 2. No GIN index on `content_markdown` — that's migration 016.

**Rollback:** `ALTER TABLE documents DROP COLUMN content_markdown, DROP COLUMN content_markdown_status;`

---

### Migration 015 — Two-scope RLS policies + scope-mutation trigger

**File:** `backend/migrations/015_two_scope_rls.sql`

**Purpose:** Drop Episode-1 single-axis policies on `documents` and `document_chunks`. Create 4-policies-per-action two-scope policies on documents, document_chunks, and folders (12 policies per table × 3 tables — actually 8 per table for documents/folders since chunks have no UPDATE = 7 for chunks; final count: 8+7+8 = 23 policies). Create the `forbid_scope_mutation()` trigger function and attach to all three tables.

**DDL skeleton:**

```sql
-- Phase 1 / Migration 015: Two-scope RLS + scope-mutation trigger
-- This is the blast-radius migration. Run integration tests immediately after.

-- ── 1. is_admin() helper (DRY) ──
CREATE OR REPLACE FUNCTION public.is_admin() RETURNS BOOLEAN
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(
    (SELECT is_admin FROM public.profiles WHERE id = auth.uid()),
    false
  );
$$;

GRANT EXECUTE ON FUNCTION public.is_admin() TO authenticated;

-- ── 2. forbid_scope_mutation() trigger function ──
-- WORKAROUND: Postgres RLS WITH CHECK cannot reference OLD.col.
-- Trigger fires after RLS passes, before row write. RAISE EXCEPTION
-- if scope is being changed.
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

-- ── 3. Drop existing single-axis policies ──
DROP POLICY IF EXISTS "Users can view own documents"   ON public.documents;
DROP POLICY IF EXISTS "Users can insert own documents" ON public.documents;
DROP POLICY IF EXISTS "Users can update own documents" ON public.documents;
DROP POLICY IF EXISTS "Users can delete own documents" ON public.documents;

DROP POLICY IF EXISTS "Users can view own chunks"   ON public.document_chunks;
DROP POLICY IF EXISTS "Users can insert own chunks" ON public.document_chunks;
DROP POLICY IF EXISTS "Users can delete own chunks" ON public.document_chunks;

-- ── 4. documents: 8 new policies (4 ops × 2 scopes), see RLS Policy Catalog ──
-- (full SQL in catalog section below)

-- ── 5. document_chunks: 6 new policies (no UPDATE — chunks are insert+delete only) ──
-- (full SQL in catalog section below)

-- ── 6. folders: 8 new policies ──
-- (full SQL in catalog section below)

-- ── 7. Attach scope-mutation triggers ──
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

**What it does NOT do:** Does not promote any existing rows to global (everything stays scope='user' from the 012 default). Does not change service-role behavior — service_role bypasses RLS as before.

**Rollback:** Drop the new policies, drop the triggers, drop the functions, recreate the 4 Episode-1 documents policies and 3 chunks policies. Risky if Phase 2 has already started writing global-scope rows.

---

### Migration 016 — pg_trgm + text_pattern_ops indexes

**File:** `backend/migrations/016_search_indexes.sql`

**Purpose:** Add the GIN trigram index on `documents.content_markdown` (powers Phase 4 grep), GIN trigram on `documents.folder_path` (powers Phase 4 glob substring), and `text_pattern_ops` btree on `documents.folder_path` (powers Phase 4 prefix LIKE).

**DDL skeleton:**

```sql
-- Phase 1 / Migration 016: search-acceleration indexes
-- pg_trgm extension was enabled in migration 012.
-- Indexes are plain (non-CONCURRENT) because run_migrations.py runs
-- each migration in a transaction. At current scale (low-thousands docs)
-- the SHARE lock window is sub-second.
-- For production at 10k+ docs per user, run CREATE INDEX CONCURRENTLY
-- versions of these manually outside the migration runner.

-- ── 1. GIN trigram on content_markdown (powers grep) ──
CREATE INDEX IF NOT EXISTS documents_content_markdown_trgm_idx
  ON documents USING gin (content_markdown gin_trgm_ops);

-- ── 2. GIN trigram on folder_path (powers glob substring) ──
CREATE INDEX IF NOT EXISTS documents_folder_path_trgm_idx
  ON documents USING gin (folder_path gin_trgm_ops);

-- ── 3. text_pattern_ops btree on folder_path (powers LIKE 'prefix/%') ──
-- Critical: default-collation btree is NOT used for prefix LIKE in
-- non-C locales (Supabase is en_US.UTF-8). text_pattern_ops forces
-- byte-wise comparison and enables the index.
CREATE INDEX IF NOT EXISTS documents_folder_path_prefix_idx
  ON documents (folder_path text_pattern_ops);

-- Same trigram + prefix indexes on folders for Phase 3/4 listing
CREATE INDEX IF NOT EXISTS folders_path_trgm_idx
  ON public.folders USING gin (path gin_trgm_ops);

CREATE INDEX IF NOT EXISTS folders_path_prefix_idx
  ON public.folders (path text_pattern_ops);
```

**What it does NOT do:** Does not add the `(scope, user_id, folder_path)` composite — defer to Phase 4 once query shapes are known.

**Rollback:** `DROP INDEX IF EXISTS documents_content_markdown_trgm_idx, documents_folder_path_trgm_idx, documents_folder_path_prefix_idx, folders_path_trgm_idx, folders_path_prefix_idx;`

---

## RLS Policy Catalog

Complete SQL for all policies, paste-ready for migration 015. **23 policies total** (8 documents + 7 chunks + 8 folders).

### documents (8 policies)

```sql
-- SELECT: own user-scoped OR any global
CREATE POLICY "documents_select"
  ON public.documents FOR SELECT
  TO authenticated
  USING (
    scope = 'global'
    OR (scope = 'user' AND user_id = (SELECT auth.uid()))
  );

-- INSERT (user scope): self only
CREATE POLICY "documents_insert_user"
  ON public.documents FOR INSERT
  TO authenticated
  WITH CHECK (
    scope = 'user' AND user_id = (SELECT auth.uid())
  );

-- INSERT (global scope): admin only, user_id must be NULL
CREATE POLICY "documents_insert_global"
  ON public.documents FOR INSERT
  TO authenticated
  WITH CHECK (
    scope = 'global'
    AND user_id IS NULL
    AND public.is_admin()
  );

-- UPDATE (user scope): self only on user-scope rows
CREATE POLICY "documents_update_user"
  ON public.documents FOR UPDATE
  TO authenticated
  USING (scope = 'user' AND user_id = (SELECT auth.uid()))
  WITH CHECK (scope = 'user' AND user_id = (SELECT auth.uid()));

-- UPDATE (global scope): admin only
CREATE POLICY "documents_update_global"
  ON public.documents FOR UPDATE
  TO authenticated
  USING (scope = 'global' AND public.is_admin())
  WITH CHECK (scope = 'global' AND public.is_admin());

-- DELETE (user scope): self only
CREATE POLICY "documents_delete_user"
  ON public.documents FOR DELETE
  TO authenticated
  USING (scope = 'user' AND user_id = (SELECT auth.uid()));

-- DELETE (global scope): admin only
CREATE POLICY "documents_delete_global"
  ON public.documents FOR DELETE
  TO authenticated
  USING (scope = 'global' AND public.is_admin());

-- Note: scope-mutation prevention enforced by BEFORE UPDATE trigger
-- (see public.forbid_scope_mutation), not by WITH CHECK (which cannot
-- reference OLD).

-- 7 policies above + the trigger = "8th protection"
```

### document_chunks (6 policies — no UPDATE since chunks are immutable)

```sql
-- SELECT: own user-scoped OR any global
CREATE POLICY "document_chunks_select"
  ON public.document_chunks FOR SELECT
  TO authenticated
  USING (
    scope = 'global'
    OR (scope = 'user' AND user_id = (SELECT auth.uid()))
  );

-- INSERT (user scope)
CREATE POLICY "document_chunks_insert_user"
  ON public.document_chunks FOR INSERT
  TO authenticated
  WITH CHECK (
    scope = 'user' AND user_id = (SELECT auth.uid())
  );

-- INSERT (global scope)
CREATE POLICY "document_chunks_insert_global"
  ON public.document_chunks FOR INSERT
  TO authenticated
  WITH CHECK (
    scope = 'global'
    AND user_id IS NULL
    AND public.is_admin()
  );

-- DELETE (user scope)
CREATE POLICY "document_chunks_delete_user"
  ON public.document_chunks FOR DELETE
  TO authenticated
  USING (scope = 'user' AND user_id = (SELECT auth.uid()));

-- DELETE (global scope)
CREATE POLICY "document_chunks_delete_global"
  ON public.document_chunks FOR DELETE
  TO authenticated
  USING (scope = 'global' AND public.is_admin());

-- Note: chunks are insert+delete only. Re-ingestion is delete-then-insert
-- (per record_manager pattern in migration 006). No UPDATE policy needed.
-- Trigger still attached defensively in case future code adds UPDATE.
```

### folders (8 policies — same shape as documents minus chunks)

```sql
-- SELECT: own user-scoped OR any global
CREATE POLICY "folders_select"
  ON public.folders FOR SELECT
  TO authenticated
  USING (
    scope = 'global'
    OR (scope = 'user' AND user_id = (SELECT auth.uid()))
  );

-- INSERT (user scope)
CREATE POLICY "folders_insert_user"
  ON public.folders FOR INSERT
  TO authenticated
  WITH CHECK (
    scope = 'user' AND user_id = (SELECT auth.uid())
  );

-- INSERT (global scope)
CREATE POLICY "folders_insert_global"
  ON public.folders FOR INSERT
  TO authenticated
  WITH CHECK (
    scope = 'global'
    AND user_id IS NULL
    AND public.is_admin()
  );

-- UPDATE (user scope)
CREATE POLICY "folders_update_user"
  ON public.folders FOR UPDATE
  TO authenticated
  USING (scope = 'user' AND user_id = (SELECT auth.uid()))
  WITH CHECK (scope = 'user' AND user_id = (SELECT auth.uid()));

-- UPDATE (global scope)
CREATE POLICY "folders_update_global"
  ON public.folders FOR UPDATE
  TO authenticated
  USING (scope = 'global' AND public.is_admin())
  WITH CHECK (scope = 'global' AND public.is_admin());

-- DELETE (user scope)
CREATE POLICY "folders_delete_user"
  ON public.folders FOR DELETE
  TO authenticated
  USING (scope = 'user' AND user_id = (SELECT auth.uid()));

-- DELETE (global scope)
CREATE POLICY "folders_delete_global"
  ON public.folders FOR DELETE
  TO authenticated
  USING (scope = 'global' AND public.is_admin());
```

**Pattern justification (why 4 policies per write op rather than one OR'd):** Postgres RLS allows multiple permissive policies per `(table, command)`; row passes if **any** policy grants. Splitting by scope:
1. Makes the migration trivially reviewable — "who can do what" reads top-to-bottom.
2. Future change like "admins can also write into other users' user-scoped folders" is one new policy, not editing a tangled OR.
3. Each policy is testable in isolation (the test matrix asserts each policy independently).

`[VERIFIED: ARCHITECTURE.md lines 605-609 — multi-permissive-policy semantics is well-established Postgres]` HIGH

---

## Validation Architecture

> Required by Nyquist gate (`workflow.nyquist_validation: true` in config.json).

### Test Framework

| Property | Value |
|----------|-------|
| Framework | Custom Python harness — `backend/scripts/test_helpers.py` (see existing pattern in `test_rls.py`) |
| Config file | None — convention-based (`test_*.py` files in `backend/scripts/`, registered in `test_all.py` SUITES) |
| Quick run command | `cd backend && venv/Scripts/python scripts/test_two_scope_rls.py` |
| Full suite command | `cd backend && venv/Scripts/python scripts/test_all.py` |
| Backend running? | Required for HTTP-based tests; `test_two_scope_rls.py` talks **directly to Supabase via supabase-py** (no backend needed for the RLS matrix), but `test_all.py` requires backend on `localhost:8001` for the other suites |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SCHEMA-01 | `folder_path` CHECK rejects `'projects'` (no leading slash) and `'projects/'` (trailing slash) | unit (SQL) | `cd backend && venv/Scripts/python scripts/test_two_scope_rls.py` (section: CHECK constraints) | ❌ Wave 0 |
| SCHEMA-02 | scope/user_id coupling CHECK rejects `(scope='user', user_id=NULL)` and `(scope='global', user_id=<uuid>)` | unit (SQL) | same | ❌ Wave 0 |
| SCHEMA-03 | `content_markdown_status` CHECK rejects values not in the 4-element set | unit (SQL) | same | ❌ Wave 0 |
| SCHEMA-04 | folders unique-expression-index rejects duplicate `(scope, user_id, path)` insert | unit (SQL) | same | ❌ Wave 0 |
| SCHEMA-05 | `EXPLAIN ANALYZE SELECT ... FROM documents WHERE content_markdown ILIKE '%foo%'` shows `Bitmap Index Scan on documents_content_markdown_trgm_idx` | integration (EXPLAIN) | `cd backend && venv/Scripts/python scripts/test_two_scope_rls.py` (section: index plans) | ❌ Wave 0 |
| RLS-01 | User A cannot SELECT user B's user-scope row; both users CAN SELECT global rows | integration | same | ❌ Wave 0 |
| RLS-02 | Non-admin INSERT scope='global' fails; admin INSERT scope='global' succeeds; user INSERT scope='user' with `user_id != self` fails | integration | same | ❌ Wave 0 |
| RLS-03 | UPDATE setting `scope='global'` on a user-scope row raises `check_violation` (trigger) | integration | same | ❌ Wave 0 |
| RLS-04 | Cross-user × cross-scope matrix passes 100% (18+ assertions) | integration | same | ❌ Wave 0 |
| FOLDER-01 | `normalize_path()` round-trips per spec; rejects `..` and `.` segments | unit (Python) | included in same file (or separate `test_normalize_path.py`) | ❌ Wave 0 |
| TEST-04 | `test_two_scope_rls.py` registered in `test_all.py` SUITES | integration | `cd backend && venv/Scripts/python scripts/test_all.py` (verify "Two-Scope RLS" suite ran) | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `cd backend && venv/Scripts/python scripts/test_two_scope_rls.py` (sub-30s — direct Supabase calls, no backend HTTP)
- **Per wave merge:** Same — full RLS matrix + normalize_path tests
- **Phase gate:** All assertions in `test_two_scope_rls.py` pass 100% AND `cd backend && venv/Scripts/python scripts/test_all.py` shows no regressions in existing 12 suites (RLS, Files, RAG must still pass — the new policies must not break Episode 1 behavior)

### Falsifiable Assertions (grouped)

#### Group 1: RLS matrix (RLS-01, RLS-02, RLS-04)

1. User A inserts `(scope='user', user_id=A)` → succeeds; user B `SELECT WHERE id = <A's row>` returns 0 rows.
2. User A inserts `(scope='user', user_id=A)` → row visible to A's `SELECT` (own data check).
3. Admin inserts `(scope='global', user_id=NULL)` → both A and B see it via `SELECT`.
4. User A `INSERT (scope='global', ...)` → fails (no policy grants); raises RLS error.
5. User A `INSERT (scope='user', user_id=B)` → fails (WITH CHECK requires `user_id = auth.uid()`).
6. User A `UPDATE` non-scope field on own user-scope row → succeeds.
7. User A `UPDATE` other user's row → 0 rows updated (USING blocks visibility).
8. User A `DELETE` own user-scope row → 1 row deleted; subsequent `SELECT` returns 0.
9. User A `DELETE` global row → 0 rows deleted (no policy grants).
10. Admin `DELETE` global row → 1 row deleted.
11. All 1-10 repeated for `document_chunks` (without UPDATE rows).
12. All 1-10 repeated for `folders` (with UPDATE rows).

#### Group 2: Scope-mutation prevention (RLS-03)

13. User A `UPDATE documents SET scope='global' WHERE id = <own user-scope id>` → raises `check_violation` (trigger).
14. Admin `UPDATE documents SET scope='user', user_id=<some uuid> WHERE id = <global row id>` → raises `check_violation`.
15. `UPDATE documents SET file_name='new' WHERE id = <own>` → succeeds (scope unchanged, trigger no-op).
16. Trigger fires on all three tables (documents, document_chunks, folders) — verified by attempting scope flip on each.

#### Group 3: Path normalization (FOLDER-01)

17. `normalize_path('/')` == `'/'`
18. `normalize_path('/a/b')` == `'/a/b'`
19. `normalize_path('/a/b/c')` == `'/a/b/c'`
20. `normalize_path('/A/B')` == `'/A/B'` (case preserved)
21. `normalize_path('/a//b')` == `'/a/b'`
22. `normalize_path('a/b')` == `'/a/b'`
23. `normalize_path('/a/b/')` == `'/a/b'`
24. `normalize_path('\\a\\b')` == `'/a/b'`
25. `normalize_path('')` == `'/'`
26. `normalize_path(None)` == `'/'`
27. `normalize_path('/a/../b')` raises `ValueError`
28. `normalize_path('/a/./b')` raises `ValueError`

#### Group 4: CHECK constraints (SCHEMA-01, SCHEMA-02, SCHEMA-03)

29. `INSERT INTO documents (folder_path, ...) VALUES ('projects', ...)` → fails CHECK (no leading slash).
30. `INSERT INTO documents (folder_path, ...) VALUES ('/projects/', ...)` → fails CHECK (trailing slash).
31. `INSERT INTO documents (folder_path, ...) VALUES ('//', ...)` → fails CHECK.
32. `INSERT INTO documents (scope, user_id, ...) VALUES ('user', NULL, ...)` → fails CHECK (coupling).
33. `INSERT INTO documents (scope, user_id, ...) VALUES ('global', '<uuid>', ...)` → fails CHECK (coupling).
34. `INSERT INTO documents (content_markdown_status, ...) VALUES ('processing', ...)` → fails CHECK (not in enum).
35. Same constraints exist on `folders.path` (canonical regex) and `folders.scope`/`user_id`.

#### Group 5: Indexes & perf (SCHEMA-05)

36. `EXPLAIN (ANALYZE, FORMAT TEXT) SELECT id FROM documents WHERE content_markdown ILIKE '%floor%';` shows `Bitmap Index Scan on documents_content_markdown_trgm_idx` (NOT `Seq Scan`). Requires a seeded fixture with at least one row containing the literal "floor".
37. `EXPLAIN ... WHERE folder_path LIKE '/projects/%';` shows `Index Scan on documents_folder_path_prefix_idx` (NOT `Seq Scan`).
38. `pg_extension` lists `pg_trgm`: `SELECT 1 FROM pg_extension WHERE extname='pg_trgm'` returns 1 row.

#### Group 6: Idempotency (migration runner safety)

39. Running migrations 012-016 a second time produces no errors (every DDL is `IF NOT EXISTS` / `IF EXISTS` / `CREATE OR REPLACE`).
40. Running migration 015 a second time correctly re-creates triggers via `DROP TRIGGER IF EXISTS` + `CREATE TRIGGER`.

### Wave 0 Gaps

- [ ] `backend/scripts/test_two_scope_rls.py` — covers RLS-01..04, SCHEMA-01..05, FOLDER-01
- [ ] `backend/app/services/folder_service.py` — exports `normalize_path()` (importable by tests)
- [ ] `backend/scripts/test_helpers.py` extension — add `TEST_USER_ADMIN` fixture and `get_admin_token()` helper
- [ ] `backend/scripts/test_all.py` — add `import test_two_scope_rls` and append to `SUITES`
- [ ] One-time setup documented in test_two_scope_rls.py docstring: `UPDATE profiles SET is_admin=true WHERE email='admin@test.com';`

(Existing test infrastructure covers everything else — no framework changes needed.)

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Two-axis RLS enforcement | Database (Supabase Postgres policies) | Backend service layer (defense-in-depth `.eq('scope', ...)` per CONCERNS.md service-role anti-pattern) | DB is the bedrock — even a bug in app code cannot leak data if RLS is correct. App layer is defensive only. |
| Scope-mutation prevention | Database (BEFORE UPDATE trigger) | Backend service layer (don't write `scope` on UPDATE) | Trigger is the final enforcement; app code follows convention but is not the gate. |
| Path canonicalization | Backend service layer (`normalize_path()`) | Database (CHECK constraint on canonical regex) | Single Python chokepoint normalizes; DB rejects any non-canonical that slips through. Two layers because the Python layer can't see SQL-direct calls (test scripts, future RPC). |
| Schema migration application | Migration runner (`run_migrations.py`) | Manual SQL editor for `CREATE INDEX CONCURRENTLY` at scale | Runner handles transactional DDL; non-transactional DDL is a one-off manual step (out of Phase 1 scope). |
| Folder uniqueness (concurrent-upload race) | Database (unique expression index) | Backend service layer (Phase 3 will use `INSERT ... ON CONFLICT DO NOTHING` or omit the folders write per ARCHITECTURE.md Pattern 2 alt) | DB unique is the bedrock; app strategy is Phase 3's call. |
| Test of RLS matrix | Test harness (`test_two_scope_rls.py`) | CI gate on `test_all.py` | Per-file test for fast iteration; full suite for regression. |

---

## Open Questions for Planner (RESOLVED)

All 9 questions below were resolved during planning (plans 01-08). No question is left open.

1. **`normalize_path()` `..` and `.` semantics — accept-and-resolve or reject?**
   - Pitfalls doc says reject (path traversal security risk).
   - This research recommends **reject with `ValueError`**.
   - Planner: confirm this matches user intent or ask user. **Default: reject.**
   - **RESOLVED:** REJECT — `normalize_path` raises `ValueError` on `..` or `.` segments. Decided in plan 01 (`backend/app/services/folder_service.py`); enforced by VALIDATION.md assertions 27-28 and exercised by plan 08 task 1-08-03 (`h.test("normalize_path('/a/../b') raises ValueError", ...)` and the equivalent for `/a/./b`).

2. **`NULLS NOT DISTINCT` (PG15+) vs COALESCE sentinel for the folders unique index.**
   - Both work. `NULLS NOT DISTINCT` is cleaner; COALESCE is universally compatible.
   - Supabase runs PG15+ (verified — major version is 15+ across all current projects).
   - **Recommendation: COALESCE sentinel** (zero risk of cross-project portability issue, identical performance). Planner can decide.
   - **RESOLVED:** COALESCE sentinel via expression index (NOT a table-level UNIQUE constraint, which can't take expressions). Decided in plans 02 and 03: `CREATE UNIQUE INDEX ... ON ... (scope, COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid), <path-or-filename>);`. Pitfall 3 in this document (line 1137-1140) documents the table-constraint trap that would fail if attempted.

3. **Path-traversal inside Python `normalize_path()` — does it apply to scope='global' paths too?**
   - Yes. The function is scope-agnostic (it operates on a single string). Validation happens at write time regardless of scope.
   - **RESOLVED:** YES — the function is scope-agnostic and is invoked at every write site regardless of scope. Plan 01 confirms a single chokepoint; the DB CHECK constraint (plan 02) is defense-in-depth for any SQL-direct write that bypasses the Python layer.

4. **Should `scope='both'` ever appear in the database?**
   - **No.** `'both'` is a tool-arg value (LLM passes `scope='both'` to `tree`/`grep`/etc to mean "search both scopes"). The database CHECK constraint only allows `'user'` or `'global'`. The tool layer (Phase 4) translates `'both'` into "no `.eq('scope', ...)` filter."
   - **RESOLVED:** NO — the column-level `scope` is `Literal["user", "global"]` ONLY. The Phase 1 CHECK constraint (plan 02) enforces this at the DB layer. The value `"both"` is a Phase 4 tool-arg default for `tree`/`grep`/etc. that the tool layer translates into "omit the `.eq('scope', ...)` filter." `"both"` is OUT OF SCOPE for Phase 1 schema.

5. **`is_admin()` SQL function — `SECURITY DEFINER` and `search_path = public` — any RLS recursion risk?**
   - The function reads `public.profiles` which has its own RLS. With `SECURITY DEFINER`, it runs as the function owner (typically the migration role / postgres) — bypassing the RLS on profiles for that read. This is intentional (otherwise admin checks would deadlock with profile-read policies).
   - **Recommend planner verify the migration role has `SELECT` on `public.profiles`** (it does by default in Supabase) and document this in the function comment.
   - **RESOLVED:** NO recursion risk. Plan 05 implements `public.is_admin()` as `LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public`. SECURITY DEFINER runs the read as the function owner (postgres role), bypassing the RLS on `profiles` that would otherwise depend on `is_admin` to grant the read (deadlock avoidance). Owner has `SELECT` on `public.profiles` by Supabase default. Function comment in migration 015 documents this rationale.

6. **TEST_USER_ADMIN setup — manual SQL one-time step or automated in test setup?**
   - **Recommend manual one-time** documented in test docstring. Automating it requires service-role access from the test, which works but adds a dependency. The same person who runs migrations can run one `UPDATE profiles SET is_admin=true ...` statement.
   - **RESOLVED:** MANUAL one-time. Plan 08 task 1-08-01 is a `checkpoint:human-action` documenting the exact SQL: `UPDATE public.profiles SET is_admin=true WHERE email='admin@test.com';`. Not automated because automating it would require service-role access from the test fixture, which is the exact anti-pattern (per CONCERNS.md) that the rest of plan 08 carefully avoids. The test setup function (`_verify_admin_setup`) bails with a clear error if the promotion was not done.

7. **Composite scope-aware index on `documents (scope, user_id, folder_path)` — Phase 1 or Phase 4?**
   - **Recommend Phase 4.** Add only after `EXPLAIN ANALYZE` on real Phase 4 queries shows it's needed.
   - Risk if deferred: Phase 4 plans need a quick "add this index" task. Acceptable.
   - **RESOLVED:** DEFERRED to Phase 4. Phase 1 (plan 06, migration 016) adds only the two minimum-viable indexes — GIN trigram on `content_markdown` and btree `text_pattern_ops` on `folder_path`. Composite index design needs the actual TOOL-01..10 query plans which only exist after Phase 4 builds them. Phase 4 plan must include a quick "EXPLAIN ANALYZE → add composite index if needed" task.

8. **`document_chunks.folder_path` column — should chunks denormalize folder_path too?**
   - The research/architecture docs **don't add `folder_path` to chunks** — only `scope`. Joins back to `documents` give the path.
   - **Recommendation: do not add folder_path to chunks** unless Phase 4 query plans show join cost is unacceptable.
   - The roadmap text says "Five small migrations 012 (folder_path + scope columns + CHECK constraints)" — ambiguous whether this means chunks too. Default: chunks get `scope` only (matching ARCHITECTURE.md System Overview lines 96-97).
   - **RESOLVED:** `document_chunks` gets `scope` ONLY (no `folder_path`). Decided in plan 02. Chunks always inherit a document's folder via the `document_id` FK; denormalizing `folder_path` onto chunks would create an update-anomaly when documents are moved between folders in Phase 3. The `scope` column on chunks IS required for RLS (cannot derive scope from a join inside an RLS policy without performance pain).

9. **CLAUDE.md says "Save plans to `.agent/plans/`"; GSD framework writes to `.planning/phases/`. Which is canonical for Phase 1?**
   - This research goes to `.planning/phases/01-.../01-RESEARCH.md` per GSD convention.
   - Planner should clarify with user whether implementation plans live in `.agent/plans/` (CLAUDE.md) or `.planning/phases/` (GSD). **Recommend: GSD location for now**, since the rest of GSD framework is set up.
   - **RESOLVED:** `.planning/phases/` (GSD convention). All 8 Phase-1 plans live at `.planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/`. Test files follow the project's existing convention: `backend/scripts/test_two_scope_rls.py` matching the sibling `backend/scripts/test_rls.py` pattern (not a `tests/` directory; CLAUDE.md uses `scripts/` as the test root).

---
---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| RLS scope mutation prevention | A SECURITY DEFINER function called from WITH CHECK that does `SELECT old_scope FROM ...` | BEFORE UPDATE trigger | Trigger is canonical, atomic, no race window |
| Scope+user_id coupling | App-level `if scope == 'global' and user_id is not None: raise` | Postgres CHECK constraint | DB-level invariant; app layer can have bugs, CHECK can't |
| Path canonical-form rejection | App-level regex validation only | Both Python `normalize_path()` AND DB CHECK constraint | Defense in depth — direct SQL calls bypass app layer |
| Folder-table uniqueness across NULL user_id | App-level "is this path already in folders?" check before insert | DB unique expression index with COALESCE | Concurrent uploads race; only DB constraint is atomic |
| Admin-gate predicate inlined in 6 policies | Repeating `EXISTS (SELECT 1 FROM profiles WHERE id=auth.uid() AND is_admin=true)` | `public.is_admin()` SQL function (STABLE SECURITY DEFINER) | DRY; one place to fix bugs |
| pg_trgm-equivalent custom indexing | Trigram trigger maintaining a side column | Built-in `gin_trgm_ops` operator class | Postgres ships this for free; reinventing is folly |
| NULL-aware unique constraint | Application-side de-duplication | `CREATE UNIQUE INDEX ... ON ... (..., COALESCE(user_id, sentinel), ...)` or `NULLS NOT DISTINCT` | Atomic at write time |
| Test runner | Pytest setup | Existing `test_helpers.py` + `test_all.py` SUITES pattern | Project convention; consistent with 11 other test suites |

---

## Common Pitfalls

### Pitfall 1: `WITH CHECK (scope = OLD.scope)` syntax error
**What goes wrong:** Migration 015 fails with `ERROR: missing FROM-clause entry for table "old"`.
**Why it happens:** Postgres RLS policy expressions cannot reference OLD/NEW row aliases. They are trigger-only constructs.
**How to avoid:** Use a BEFORE UPDATE trigger (`forbid_scope_mutation()`). See Migration 015.
**Warning signs:** Plan calls for `WITH CHECK (scope = OLD.scope)` — this is the symptom; reject the plan.

### Pitfall 2: `CREATE INDEX CONCURRENTLY` in a transaction
**What goes wrong:** Migration 016 fails with `ERROR: CREATE INDEX CONCURRENTLY cannot run inside a transaction block`.
**Why it happens:** `run_migrations.py` runs each migration in a transaction.
**How to avoid:** Use plain `CREATE INDEX` for Phase 1; document the manual `CONCURRENTLY` upgrade for production scale.

### Pitfall 3: COALESCE in table-level UNIQUE constraint
**What goes wrong:** `UNIQUE (scope, COALESCE(user_id, '00..0'), path)` syntax error — table constraints accept only column lists.
**Why it happens:** Confusion between table constraint and expression index.
**How to avoid:** Use `CREATE UNIQUE INDEX ... ON ... (scope, COALESCE(user_id, '00..0'::uuid), path);` — never inside `CREATE TABLE`.

### Pitfall 4: Default-collation btree on folder_path doesn't accelerate `LIKE 'prefix/%'`
**What goes wrong:** `EXPLAIN ANALYZE` shows `Seq Scan` even though `documents_folder_path_idx` exists.
**Why it happens:** Supabase locale is `en_US.UTF-8`; default-collation btree skips index for `LIKE` patterns.
**How to avoid:** Use `text_pattern_ops` operator class explicitly (migration 016).

### Pitfall 5: `auth.uid()` re-evaluated per row
**What goes wrong:** RLS policies on hot tables run 10× slower than necessary.
**Why it happens:** Bare `auth.uid()` is treated as VOLATILE, called per row.
**How to avoid:** Wrap as `(SELECT auth.uid())` — Postgres caches the result for the query. Apply throughout migration 015. `[VERIFIED: supabase.com/docs/guides/database/postgres/row-level-security]`

### Pitfall 6: Service-role client bypasses RLS in tests
**What goes wrong:** Test asserts "user A cannot see user B's row" but the test client uses service-role; RLS doesn't apply; test passes incorrectly.
**Why it happens:** `backend/app/auth.py:get_supabase_client()` returns a service-role client. Tests that import this for convenience defeat the test.
**How to avoid:** `test_two_scope_rls.py` MUST instantiate supabase-py with the **anon key** and authenticate using a real user JWT (`get_auth_token()` returns a JWT, then `client = create_client(URL, ANON_KEY)` and pass JWT in headers via PostgREST). Do not import `get_supabase_client` from `app.auth`.

### Pitfall 7: Missing TEST_USER_ADMIN setup → admin tests silently skip
**What goes wrong:** `TEST_USER_ADMIN` exists in profiles but `is_admin = false`; admin INSERT tests fail not because RLS is wrong but because the user isn't actually admin.
**Why it happens:** Profile creation trigger from migration 005 sets `is_admin = false` by default.
**How to avoid:** Test docstring documents one-time `UPDATE profiles SET is_admin=true WHERE email='admin@test.com';`. Test setup VERIFIES `is_admin=true` before running admin assertions and bails with a clear error if not.

---

## Code Examples

### Verified pattern: SECURITY DEFINER + SET search_path

```sql
-- Source: backend/migrations/005_profiles_and_settings.sql:38-50 (handle_new_user pattern)
CREATE OR REPLACE FUNCTION public.is_admin() RETURNS BOOLEAN
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(
    (SELECT is_admin FROM public.profiles WHERE id = auth.uid()),
    false
  );
$$;

GRANT EXECUTE ON FUNCTION public.is_admin() TO authenticated;
```

### Verified pattern: BEFORE UPDATE trigger

```sql
-- Source: backend/migrations/008_hybrid_search.sql:11-22 (document_chunks_tsv_trigger pattern)
CREATE OR REPLACE FUNCTION public.forbid_scope_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.scope IS DISTINCT FROM OLD.scope THEN
    RAISE EXCEPTION 'Scope mutation forbidden';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS documents_forbid_scope_mutation ON public.documents;
CREATE TRIGGER documents_forbid_scope_mutation
  BEFORE UPDATE ON public.documents
  FOR EACH ROW EXECUTE FUNCTION public.forbid_scope_mutation();
```

### Verified pattern: idempotent column add with default

```sql
-- Source: backend/migrations/007_document_metadata.sql:5
ALTER TABLE documents ADD COLUMN IF NOT EXISTS metadata JSONB;

-- Phase 1 equivalent:
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS folder_path TEXT NOT NULL DEFAULT '/',
  ADD COLUMN IF NOT EXISTS scope       TEXT NOT NULL DEFAULT 'user'
    CHECK (scope IN ('user', 'global'));
```

### Direct supabase-py call with user JWT (for tests that need RLS to apply)

```python
# Pattern for test_two_scope_rls.py — bypass FastAPI backend, talk to Supabase directly
import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

def get_user_supabase_client(jwt_token: str):
    """Return a supabase-py client authenticated as the JWT's user.
    RLS WILL apply (anon key, not service-role)."""
    client = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_ANON_KEY"],
    )
    # PostgREST honors the Authorization header for RLS
    client.postgrest.auth(jwt_token)
    return client

# Usage in test:
token_a = h.get_auth_token(h.TEST_USER_A["email"], h.TEST_USER_A["password"])
sb_a = get_user_supabase_client(token_a)
result = sb_a.table("documents").insert({
    "scope": "global",  # should fail — not admin
    "file_name": "leak.txt",
    "file_size": 0,
    # ...
}).execute()
# Assert insert was rejected by RLS
```

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python venv | All migration runs and tests | Assumed ✓ (per CLAUDE.md mandate) | 3.x | None — blocking |
| psycopg2 | `run_migrations.py` | Assumed ✓ (in `requirements.txt`) | — | None |
| supabase-py | Test direct-Supabase calls | ✓ (already in `backend/requirements.txt`) | — | Use HTTP via `requests` (verbose) |
| `DATABASE_URL` env var | `run_migrations.py` | Configured per developer (`.env`) | — | None |
| Supabase project (Postgres ≥15) | All DDL | Assumed ✓ | PG15+ | None |
| `pg_trgm` extension | Migration 012/016 | Available in Supabase by default | bundled | None |
| `pgvector` extension | Existing — not Phase 1 dependency | ✓ (migration 003) | — | — |
| Backend running on `localhost:8001` | `test_all.py` (full sweep) | Required for non-RLS suites | — | Run only `test_two_scope_rls.py` standalone (no backend needed) |
| `is_admin=true` on `TEST_USER_ADMIN.profile` | Admin RLS tests | Manual one-time setup | — | None — tests skip with clear error if missing |

**Missing dependencies with no fallback:** None — all required tooling is already in place.

**Missing dependencies with fallback:** `TEST_USER_ADMIN` profile setup is a one-time manual step; tests should verify and provide a clear error message if not done.

---

## Security Domain

> Required by `workflow.security_enforcement: true` in config.json. ASVS Level 1.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V1 Architecture | yes | Two-scope RLS as defense-in-depth — DB enforces what app should also enforce |
| V2 Authentication | no | Reuses Episode 1's Supabase Auth; not changed this phase |
| V3 Session Management | no | Same as above |
| V4 Access Control | **yes (PRIMARY)** | RLS policies + admin gate via `is_admin()` + scope-mutation trigger; this is the entire phase |
| V5 Input Validation | yes | `normalize_path()` rejects `..`/`.`/backslash; CHECK constraints reject non-canonical paths |
| V6 Cryptography | no | No crypto added this phase |
| V7 Error Handling | partial | Trigger raises with clear message; CHECK violations bubble up cleanly |
| V8 Data Protection | yes | Two-scope model enforces user data isolation; admin actions auditable (Phase 1 doesn't add audit logging — deferred to v2 AUDIT-02) |
| V9 Communication | no | Reuses HTTPS / Supabase TLS |
| V10 Malicious Code | no | N/A |
| V11 Business Logic | yes | Scope mutation forbidden = business rule enforced at DB layer |
| V12 Files & Resources | yes | Path traversal prevented by `normalize_path()` + CHECK regex |
| V13 API Security | partial | Tool args (Phase 4) need Pydantic validation; Phase 1 lays the foundation |
| V14 Configuration | yes | Migrations are versioned and idempotent |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| User writes `scope='global'` row, planting content into shared KB | Tampering, Repudiation | Two-scope RLS — INSERT policies require `scope='user'` for non-admins (Pitfall 1) |
| User UPDATE flips own private row to global, leaking to all users | Tampering | BEFORE UPDATE trigger forbidding scope change (Pitfall 1, RLS-03) |
| Path traversal via `../other-user-folder` | Tampering, Information Disclosure | `normalize_path()` rejects `..`; DB CHECK constraint rejects malformed paths (Pitfalls §security) |
| Race on `INSERT INTO folders` for new path → duplicate folders rows | Concurrency / data integrity | Unique expression index on `(scope, COALESCE(user_id, '00..0'), path)` (Pitfall 10) |
| Service-role bypass: bug in app code exposes cross-user data even with RLS | Information Disclosure | Defense in depth — `.eq('scope', ...)` and `.eq('user_id', ...)` per query in service layer (Phase 3+); RLS as bedrock if app fails |
| SQL injection via `folder_path` parameter | Tampering | parameterized queries via supabase-py; no string concatenation; CHECK regex rejects `;`-bearing input as not matching `^/[^/]+...` (slashes only) |
| Admin demotion mid-session leaves stale `is_admin=true` cached in JWT | Privileged Access Misuse | `is_admin()` SQL function reads from `profiles` table at query time — no JWT cache (verified in STACK.md §3 "What NOT to do: Storing JWT `is_admin` claim") |

---

## Standard Stack

### Core (Phase 1 net-new)

| Library/Component | Version | Purpose | Why Standard |
|-------------------|---------|---------|--------------|
| `pg_trgm` (Postgres extension) | bundled with PG15+ | GIN trigram index for `ILIKE`/`~*`/`~` queries | Built into Postgres; STACK.md §1 verified pre-decision |
| `text_pattern_ops` (Postgres opclass) | bundled | Btree for `LIKE 'prefix/%'` in non-C locale | Postgres core; standard for prefix queries (STACK.md §2) |
| Postgres BEFORE UPDATE triggers | bundled | Workaround for RLS-not-able-to-reference-OLD | Canonical Postgres pattern (verified §1) |
| Postgres `SECURITY DEFINER` functions | bundled | `is_admin()` helper that bypasses profile RLS for the lookup | Reuses Episode 1's `handle_new_user` pattern from migration 005 |

### Supporting (already in stack)

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `psycopg2` | from `requirements.txt` | Migration runner DB connection | Already in use; no change |
| `supabase` (supabase-py) | from `requirements.txt` | Direct Supabase calls in `test_two_scope_rls.py` | Tests that need RLS to apply (anon key + JWT) |
| `python-dotenv` | from `requirements.txt` | Test env var loading | Already in test_helpers |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| BEFORE UPDATE trigger for scope-mutation | SECURITY DEFINER function inside WITH CHECK | Function would have to fetch OLD row via SELECT — racy, slower, weirder; trigger is canonical |
| TEXT + CHECK for `content_markdown_status` | ENUM type | ENUM `ALTER TYPE` is painful in migrations; TEXT+CHECK is identically safe and easier to evolve |
| COALESCE sentinel in unique index | `NULLS NOT DISTINCT` (PG15+) | NULLS NOT DISTINCT is cleaner but requires PG15+; COALESCE works on every PG version |
| Plain `CREATE INDEX` | `CREATE INDEX CONCURRENTLY` | CONCURRENTLY can't run in transaction (run_migrations limitation); plain is acceptable at current scale |
| Path-as-string with side `folders` table | `ltree` extension | ltree label charset rejects `-`/`.`/spaces in folder names (STACK.md §2); not viable |
| Composite `(scope, user_id, folder_path)` index in Phase 1 | Defer to Phase 4 | Speculative now; add when EXPLAIN ANALYZE on Phase 4 queries shows need |

**Installation:** No new packages required. All extensions enabled by SQL.

**Version verification:**

```sql
SELECT extversion FROM pg_extension WHERE extname='pg_trgm';
-- Should return e.g. '1.6' on PG15+
SELECT version();
-- Should show PostgreSQL 15.x or higher
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `WITH CHECK (scope = OLD.scope)` | BEFORE UPDATE trigger comparing NEW vs OLD | Postgres has never supported OLD/NEW in policies | Phase brief was aspirational; this research corrects it |
| Default-collation btree for prefix `LIKE` | `text_pattern_ops` btree | Postgres ≥9.0 | Mandatory for non-C locales; well-known foot-gun |
| `auth.uid()` raw in RLS policies | `(SELECT auth.uid())` wrap | Supabase 2024 best-practices update | 10× perf for hot tables; verified via Supabase docs |
| `UNIQUE` constraints with NULL columns allowing duplicates | `CREATE UNIQUE INDEX (..., COALESCE(col, sentinel), ...)` or `NULLS NOT DISTINCT` (PG15+) | PG15 (2023) added NULLS NOT DISTINCT; COALESCE pattern older | Phase 1 picks COALESCE for portability |
| ENUM types for status columns | TEXT + CHECK constraint | Postgres convention shift driven by migration pain | Project already uses TEXT+CHECK for `documents.status` (migration 003) |

**Deprecated/outdated (in this research):**
- The phrase "WITH CHECK (scope = OLD.scope)" appearing in ROADMAP.md, REQUIREMENTS.md, PITFALLS.md, and the additional context — replaced with trigger pattern. Planner should reference this research, not the originals, for the actual implementation.
- The phrase "ENUM" for `content_markdown_status` (additional context #2) — use TEXT+CHECK.
- The value `'ok'` (additional context #2) — use `'ready'` per REQUIREMENTS.md SCHEMA-03.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `normalize_path()` should reject `..` and `.` segments rather than resolve them | Decisions §3 | If wrong: legitimate uses of `..` would fail. Low risk — `..` in folder paths is almost always a mistake or an attack |
| A2 | TEST_USER_ADMIN setup is a manual one-time SQL update, not automated | Decisions §7 | If wrong: tests are slightly less self-contained. Low risk; common pattern |
| A3 | Defer composite `(scope, user_id, folder_path)` index to Phase 4 | Decisions §4 | If wrong: Phase 4 may need a quick "add this index" task. Acceptable |
| A4 | Phase 1 plans live in `.planning/phases/`, not `.agent/plans/` | Project Constraints | If wrong: planner reorganizes file location. Trivial |
| A5 | Supabase Postgres is PG15+ everywhere | Decisions §5 | If wrong: NULLS NOT DISTINCT is unavailable; COALESCE workaround already chosen, so no impact |
| A6 | Migration runner runs each file in a transaction (verified at runtime; not guaranteed across forks) | Decisions §8 | If wrong: could use CONCURRENTLY. Improves perf at scale; not blocking |
| A7 | Service-role client bypasses RLS (well-documented Supabase behavior, verified in CONCERNS.md) | Pitfalls §6 | If wrong: tests may behave differently. Already mitigated by using anon key + JWT in tests |
| A8 | Episode 1's `documents.status` enum vocabulary (`pending`/`processing`/`ready`/`failed`) is the canonical pattern; `content_markdown_status` follows the same shape | Decisions §2 | If wrong: vocabulary mismatch. Minor — easily renamed |

**For the planner:** A1 and A2 should be confirmed with the user before locking. A3-A8 are technical details with low blast radius if revisited.

---

## Sources

### Primary (HIGH confidence)
- `backend/migrations/003_byo_retrieval.sql` — existing `documents`/`document_chunks` schema and RLS policies (the patterns we extend)
- `backend/migrations/005_profiles_and_settings.sql` — `profiles.is_admin` BOOLEAN, EXISTS-clause admin check, SECURITY DEFINER trigger pattern
- `backend/migrations/006_record_manager.sql` — existing `(user_id, file_name)` UNIQUE constraint we replace
- `backend/migrations/007_document_metadata.sql` — pattern for adding nullable column with `IF NOT EXISTS`
- `backend/migrations/008_hybrid_search.sql` — BEFORE INSERT/UPDATE trigger function pattern
- `backend/migrations/010_sub_agents.sql` — minimal additive ALTER TABLE pattern
- `backend/scripts/run_migrations.py` — runner runs each file in a transaction (lines 38-58); verified `autocommit=False`
- `backend/scripts/test_helpers.py` — auth, SSE, polling, cleanup utilities; `TEST_USER_A`/`TEST_USER_B` fixtures
- `backend/scripts/test_rls.py` — existing single-axis RLS tests (sibling to extend)
- `backend/scripts/test_all.py` — SUITES list registration pattern
- `backend/app/auth.py` — `get_admin_user` dependency exists at line 43; reuse in Phase 3 routers
- `.planning/codebase/CONVENTIONS.md` — RLS pattern, naming, service-layer organization
- `.planning/codebase/STACK.md` — Postgres version, supabase-py present
- `.planning/research/STACK.md` — pg_trgm/text_pattern_ops/RLS-perf-wrap details (HIGH confidence per source author)
- `.planning/research/PITFALLS.md` — RLS scope-leak as RANK 1 pitfall, exact mitigation strategies
- `.planning/research/ARCHITECTURE.md` — System overview lines 86-105 (folders table sketch), Pattern 1 RLS sketch lines 538-600

### Secondary (MEDIUM-HIGH confidence — official docs cross-referenced)
- [PostgreSQL CREATE POLICY](https://www.postgresql.org/docs/current/sql-createpolicy.html) — confirmed WITH CHECK cannot reference OLD/NEW
- [PostgreSQL Row Security Policies](https://www.postgresql.org/docs/current/ddl-rowsecurity.html) — multiple permissive policies are OR'd
- [PostgreSQL pg_trgm](https://www.postgresql.org/docs/current/pgtrgm.html) — gin_trgm_ops operator class
- [PostgreSQL text_pattern_ops](https://www.postgresql.org/docs/current/indexes-opclass.html) — required for non-C locale prefix LIKE
- [PostgreSQL Unique Indexes](https://www.postgresql.org/docs/current/indexes-unique.html) — NULL handling, expression indexes
- [Supabase RLS performance: wrap auth.uid() in SELECT](https://supabase.com/docs/guides/database/postgres/row-level-security) — `(SELECT auth.uid())` pattern
- [Supabase Discussion #37459: RLS WITH CHECK cannot reference OLD/NEW](https://github.com/orgs/supabase/discussions/37459) — confirms trigger workaround

### Tertiary (LOW confidence / not load-bearing)
- General PG15+ availability on Supabase — consensus across community sources but not pinned to a specific Supabase release note

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — every choice verified against in-tree code or Postgres official docs
- Architecture (RLS catalog, migration order): HIGH — directly extends existing patterns
- RLS-03 trigger correction: HIGH — confirmed via two independent sources (Postgres docs + Supabase discussion)
- Path normalization semantics (`..` rejection, NFC): MEDIUM — pragmatic decision, planner should confirm with user
- Composite index deferral to Phase 4: MEDIUM — based on principle, not measurement
- Migration runner transaction behavior: HIGH — verified at code line `backend/scripts/run_migrations.py:39`
- Test matrix shape: HIGH — directly extends `test_rls.py` shape

**Research date:** 2026-05-02
**Valid until:** 2026-05-30 (Postgres/Supabase RLS semantics are stable; only project-internal patterns can drift)

---

## RESEARCH COMPLETE
