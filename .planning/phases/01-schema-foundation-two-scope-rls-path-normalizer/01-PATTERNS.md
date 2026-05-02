# Phase 1: Schema Foundation + Two-Scope RLS + Path Normalizer — Pattern Map

**Mapped:** 2026-05-02
**Files analyzed:** 9 (7 new + 2 modified)
**Analogs found:** 9 / 9 (all exact or strong role-match)

---

## File Classification

| New / Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `backend/migrations/012_folder_path_and_scope.sql` | migration (DDL — schema evolution: ALTER + DROP CONSTRAINT + new UNIQUE INDEX + extension enable) | n/a (one-shot DDL) | `backend/migrations/006_record_manager.sql` (closest — adds columns + UNIQUE constraint on `documents`) AND `backend/migrations/008_hybrid_search.sql` (ALTER + CREATE INDEX IF NOT EXISTS + ENABLE EXTENSION pattern) | strong role-match (no prior migration drops a constraint, see "Deviations") |
| `backend/migrations/013_folders_table.sql` | migration (DDL — new table with CHECK constraints + unique-expression index + ENABLE RLS, no policies) | n/a | `backend/migrations/003_byo_retrieval.sql` (full table-create pattern) AND `backend/migrations/005_profiles_and_settings.sql` (CREATE TABLE IF NOT EXISTS in `public.` schema, ENABLE RLS, GRANT) | exact (table create + RLS-enable) |
| `backend/migrations/014_content_markdown_column.sql` | migration (DDL — ALTER TABLE ADD COLUMN with CHECK constraint, plus partial index) | n/a | `backend/migrations/007_document_metadata.sql` (closest — ALTER TABLE ADD COLUMN IF NOT EXISTS + JSONB) AND `backend/migrations/006_record_manager.sql` (ALTER TABLE ADD COLUMN with CHECK-equivalent semantics) | strong role-match |
| `backend/migrations/015_two_scope_rls.sql` | migration (DDL — DROP existing policies, CREATE FUNCTIONs (helper + trigger fn), CREATE POLICIES × 23, CREATE TRIGGERs × 3) | n/a | `backend/migrations/005_profiles_and_settings.sql` (admin-gated policy with `EXISTS (SELECT … FROM profiles WHERE is_admin)` AND `CREATE OR REPLACE FUNCTION … SECURITY DEFINER` + `DROP TRIGGER IF EXISTS / CREATE TRIGGER`) AND `backend/migrations/008_hybrid_search.sql` (CREATE OR REPLACE FUNCTION pattern) AND `backend/migrations/003_byo_retrieval.sql` (FOR SELECT/INSERT/UPDATE/DELETE policy quartet) | exact (every primitive used has an analog) |
| `backend/migrations/016_search_indexes.sql` | migration (DDL — CREATE INDEX IF NOT EXISTS using gin_trgm_ops + text_pattern_ops) | n/a | `backend/migrations/008_hybrid_search.sql` (closest — `CREATE INDEX IF NOT EXISTS … USING gin(...)`) AND `backend/migrations/007_document_metadata.sql` (`CREATE INDEX IF NOT EXISTS … USING gin (metadata)`) | exact for GIN; no prior `text_pattern_ops` use (see "Deviations") |
| `backend/app/services/folder_service.py` | service (pure-function utility module — no DB, no I/O) | transform (string in → string out) | `backend/app/services/record_manager.py` (closest — small stateless module with pure helpers `compute_file_hash`/`compute_chunk_hash` AND `@dataclass` for return types AND no global state) AND `backend/app/services/web_search.py` (single-function module with module-level `logger`) | exact (record_manager is the canonical "pure helpers" service file) |
| `backend/scripts/test_two_scope_rls.py` | test (multi-user RLS isolation, runner-compatible) | request-response (HTTP + DB) | `backend/scripts/test_rls.py` (exact analog — the existing single-axis user-isolation test that this new test extends with the scope axis) AND `backend/scripts/test_settings.py` (PUT /api/settings → 403 admin-gate test pattern) | exact (test_rls.py is the literal sibling) |
| `backend/scripts/test_helpers.py` (modify) | test fixture / helper module | n/a | itself (extend lines 18–19 fixture block + 56–86 `get_auth_token`) | self (in-place extension) |
| `backend/scripts/test_all.py` (modify) | test runner registration | n/a | itself (extend lines 12–23 imports + line 25 `SUITES` list) | self (in-place extension) |

---

## Pattern Assignments

### `backend/migrations/012_folder_path_and_scope.sql` (migration / DDL)

**Primary analog:** `backend/migrations/006_record_manager.sql` — adds columns to `documents` AND creates a UNIQUE constraint on `documents`. This is the only prior migration that adds a UNIQUE on `documents`, so 012's "drop the old one, replace with a new one" lands here.

**Secondary analog:** `backend/migrations/008_hybrid_search.sql` — canonical `CREATE EXTENSION IF NOT EXISTS …` + `ALTER TABLE … ADD COLUMN IF NOT EXISTS …` shape.

**Header-comment pattern** (006 line 1, 003 line 1, 008 line 1):
```sql
-- Module 3: Record Manager — content hashing + unique constraint
```
Convention: single line, format `-- Module N: <Name> — <one-line purpose>`. Phase 1 should use `-- Phase 1 / Migration 012: <purpose>` (matches RESEARCH.md DDL skeleton).

**ALTER TABLE ADD COLUMN pattern** (006 lines 3–7):
```sql
-- Add content_hash column to documents
ALTER TABLE documents ADD COLUMN content_hash TEXT;

-- Add content_hash column to document_chunks (for chunk-level diffing)
ALTER TABLE document_chunks ADD COLUMN content_hash TEXT;
```
Note 006 does **not** use `IF NOT EXISTS`. **Use `IF NOT EXISTS` for 012** — the more recent 007/008/011 pattern, and it makes re-runs safe.

**Idempotent ALTER + CHECK pattern** (007 lines 4–6):
```sql
ALTER TABLE documents ADD COLUMN IF NOT EXISTS metadata JSONB;
CREATE INDEX IF NOT EXISTS documents_metadata_idx ON documents USING gin (metadata);
```

**UNIQUE constraint pattern** (006 lines 11–12 — the constraint 012 must DROP):
```sql
ALTER TABLE documents ADD CONSTRAINT documents_user_filename_unique
  UNIQUE (user_id, file_name);
```

**Conventions to preserve:**
- Filename: `NNN_snake_case.sql` (zero-padded 3 digits, `012_folder_path_and_scope.sql` matches existing sequence).
- DDL is bare SQL — no `BEGIN`/`COMMIT` (the runner wraps each file in a txn; see `run_migrations.py:39-58`).
- Two-space indent inside `ALTER TABLE …` continuations (see 003 lines 12–23).
- Index naming: `<table>_<column>_<purpose>_idx` (e.g., `documents_metadata_idx`, `documents_content_hash_idx`).

---

### `backend/migrations/013_folders_table.sql` (migration / DDL — new table)

**Primary analog:** `backend/migrations/003_byo_retrieval.sql` lines 11–32 — full table-create + indexes + RLS pattern.

**Secondary analog:** `backend/migrations/005_profiles_and_settings.sql` lines 6–15 — `CREATE TABLE IF NOT EXISTS public.<name>` form (013 should use `public.folders` like 005 uses `public.profiles`, not bare `folders` like 003).

**Table-create + indexes + RLS pattern** (003 lines 12–32):
```sql
CREATE TABLE documents (
  id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  file_name     TEXT        NOT NULL,
  ...
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX documents_user_id_idx ON documents(user_id);
CREATE INDEX documents_status_idx  ON documents(status);

ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Users can view own documents"   ON documents FOR SELECT USING (auth.uid() = user_id);
```

**`public.` schema + GRANT pattern** (005 lines 6–14, 100–102):
```sql
CREATE TABLE IF NOT EXISTS public.profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    ...
);

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
...
GRANT SELECT ON public.profiles TO service_role;
```

**Conventions to preserve:**
- Use `public.folders` (qualified) — matches 005 (the most recent `CREATE TABLE` that 013 most resembles).
- `id UUID PRIMARY KEY DEFAULT gen_random_uuid()` (003 line 13).
- `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()` (not `now()` lowercase — 003 uses `NOW()`; 005 uses `now()`. **Either works**; prefer `NOW()` for consistency with the documents table since folders sits next to it.).
- `ON DELETE CASCADE` on `user_id REFERENCES auth.users(id)` (003 line 14).
- ENABLE RLS in this file but defer policy creation to migration 015 (per RESEARCH.md §Migration 013 design — keeps the policy catalog reviewable in one file).
- `GRANT SELECT, INSERT, UPDATE, DELETE … TO authenticated` (per RESEARCH.md skeleton; matches 005's GRANT pattern but for `authenticated` role, not `service_role`).

---

### `backend/migrations/014_content_markdown_column.sql` (migration / DDL — ALTER + CHECK)

**Primary analog:** `backend/migrations/007_document_metadata.sql` lines 4–6 — the canonical "add a nullable column to documents + GIN index" shape.

**Secondary analog:** `backend/migrations/003_byo_retrieval.sql` lines 18–19 — the canonical `status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'ready', 'failed'))` pattern. **014's `content_markdown_status` column reuses this exact shape** — TEXT + NOT NULL + DEFAULT 'pending' + CHECK with parenthesized IN list.

**TEXT + CHECK enum pattern** (003 lines 18–19):
```sql
status        TEXT        NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'processing', 'ready', 'failed')),
```

**ALTER ADD COLUMN with default pattern** (007 lines 4–6, condensed):
```sql
ALTER TABLE documents ADD COLUMN IF NOT EXISTS metadata JSONB;
CREATE INDEX IF NOT EXISTS documents_metadata_idx ON documents USING gin (metadata);
```

**Combined target shape for 014** (synthesizing 003 + 007):
```sql
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS content_markdown        TEXT,
  ADD COLUMN IF NOT EXISTS content_markdown_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (content_markdown_status IN ('pending', 'ready', 'failed', 'requires_user_reupload'));

CREATE INDEX IF NOT EXISTS documents_content_markdown_status_idx
  ON documents (content_markdown_status)
  WHERE content_markdown_status <> 'ready';
```

**Conventions to preserve:**
- Multi-column `ALTER TABLE` uses one statement with comma-separated `ADD COLUMN IF NOT EXISTS` clauses (cleaner; matches the multi-column style implied by RESEARCH.md skeleton).
- CHECK constraint vocabulary matches existing `status` column (`'pending'`, `'ready'`, `'failed'`) — consistency over invention. Adds only `'requires_user_reupload'` as the new state.
- Partial index (`WHERE content_markdown_status <> 'ready'`) is a new pattern not seen in earlier migrations — call it out in a header comment so reviewers know the intent (Phase 2 backfill scan).

---

### `backend/migrations/015_two_scope_rls.sql` (migration / DDL — RLS overhaul + functions + triggers)

**Primary analog:** `backend/migrations/005_profiles_and_settings.sql` — the only prior migration with all four primitives 015 needs:
1. `CREATE OR REPLACE FUNCTION … LANGUAGE plpgsql SECURITY DEFINER` (lines 38–50 — `handle_new_user()`)
2. `DROP TRIGGER IF EXISTS … ; CREATE TRIGGER … BEFORE/AFTER … ON … FOR EACH ROW EXECUTE FUNCTION …` (lines 53–56)
3. Admin-gated policy with `EXISTS (SELECT 1 FROM public.profiles WHERE id = auth.uid() AND is_admin = true)` (lines 23–29 and 86–93)
4. `ENABLE ROW LEVEL SECURITY` + multi-policy `CREATE POLICY` set (lines 14, 17–34, 77–93)

**Secondary analog:** `backend/migrations/003_byo_retrieval.sql` lines 28–53 — the canonical 4-policy quartet (SELECT/INSERT/UPDATE/DELETE) on `documents` and the 3-policy (SELECT/INSERT/DELETE — no UPDATE) on `document_chunks`. **These are the policies 015 must DROP first.**

**Tertiary analog:** `backend/migrations/008_hybrid_search.sql` lines 11–22 — `CREATE OR REPLACE FUNCTION … LANGUAGE plpgsql AS $$ … $$;` + `DROP TRIGGER IF EXISTS …` + `CREATE TRIGGER … BEFORE INSERT OR UPDATE OF content … FOR EACH ROW EXECUTE FUNCTION …` shape. 015's `forbid_scope_mutation` trigger follows this exact form (substituting `BEFORE UPDATE` for `BEFORE INSERT OR UPDATE OF content`).

**`SECURITY DEFINER` function pattern** (005 lines 38–50):
```sql
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    INSERT INTO public.profiles (id, email)
    VALUES (NEW.id, NEW.email)
    ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END;
$$;
```
015's `is_admin()` SQL helper and `forbid_scope_mutation()` plpgsql trigger function follow this header style verbatim, including `SET search_path = public`.

**`DROP TRIGGER IF EXISTS … CREATE TRIGGER` idempotent pattern** (005 lines 53–56, 008 lines 18–22):
```sql
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();
```

**Admin-gated policy predicate** (005 lines 86–93 — exact text 015 may reuse if it does NOT factor `is_admin()` into a helper):
```sql
CREATE POLICY "Admins update settings"
    ON public.global_settings FOR UPDATE
    USING (
        EXISTS (
            SELECT 1 FROM public.profiles
            WHERE id = auth.uid() AND is_admin = true
        )
    );
```
**Recommendation per RESEARCH.md §3:** factor into `public.is_admin()` helper, then policies become `USING (public.is_admin())`. RESEARCH.md gives the exact function body.

**Policy DROP-then-CREATE pattern** (no prior migration drops policies — see "Deviations"; the canonical drop syntax is `DROP POLICY IF EXISTS "<exact-policy-name>" ON public.<table>;`). Policy names to drop come verbatim from `003_byo_retrieval.sql:29-32` and `:51-53`:
- `"Users can view own documents"`, `"Users can insert own documents"`, `"Users can update own documents"`, `"Users can delete own documents"` ON `documents`
- `"Users can view own chunks"`, `"Users can insert own chunks"`, `"Users can delete own chunks"` ON `document_chunks` (no UPDATE policy in 003 — chunks are insert+delete only)

**4-op policy quartet shape** (003 lines 28–32):
```sql
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Users can view own documents"   ON documents FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Users can insert own documents" ON documents FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users can update own documents" ON documents FOR UPDATE USING (auth.uid() = user_id);
CREATE POLICY "Users can delete own documents" ON documents FOR DELETE USING (auth.uid() = user_id);
```
**Conventions to preserve:** `USING` for SELECT/UPDATE/DELETE, `WITH CHECK` for INSERT, column-aligned formatting. New 015 policies should match this column-aligned style for the 23-policy catalog.

**Conventions to preserve / call out:**
- All new policies should use `TO authenticated` (not present in 003 but present in 005:80 — best practice; RESEARCH.md catalog shows this).
- Use `(SELECT auth.uid())` (subquery form) per RESEARCH.md catalog — Postgres caches the result per query, faster than bare `auth.uid()` per row. **No prior migration uses this optimization** — flag for planner.
- Policy naming: shift from sentence-case (`"Users can view own documents"` in 003/005) to snake_case (`"documents_select"`, `"documents_select_global_admin"` per RESEARCH.md catalog). **This is a deliberate naming-convention shift** the planner must justify in the migration header comment.

---

### `backend/migrations/016_search_indexes.sql` (migration / DDL — GIN + btree indexes)

**Primary analog:** `backend/migrations/008_hybrid_search.sql` lines 5–8 — exact GIN index pattern.

**Secondary analog:** `backend/migrations/007_document_metadata.sql` line 6 — `CREATE INDEX IF NOT EXISTS … USING gin (col)` shape.

**GIN index pattern** (008 lines 5–8):
```sql
-- 1. Add tsvector column to document_chunks
ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS tsv tsvector;

-- 2. Create GIN index for fast full-text search
CREATE INDEX IF NOT EXISTS document_chunks_tsv_idx ON document_chunks USING gin(tsv);
```

**GIN with operator class** (007 line 6):
```sql
CREATE INDEX IF NOT EXISTS documents_metadata_idx ON documents USING gin (metadata);
```
Note: 007 does NOT pass an explicit operator class (relies on default `jsonb_ops`). 016 must pass `gin_trgm_ops` explicitly because trigram is not the default GIN op for TEXT.

**`CREATE EXTENSION IF NOT EXISTS` precedent** (003 line 9, applied to `vector`):
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```
Per RESEARCH.md, `CREATE EXTENSION IF NOT EXISTS pg_trgm` ships in migration **012** (not 016), so 016 just consumes the already-enabled extension. 016 header comment should note this dependency.

**Conventions to preserve:**
- All `CREATE INDEX IF NOT EXISTS` (idempotent — matches 007/008/011).
- Index naming `<table>_<column>_<purpose>_idx` (`documents_content_markdown_trgm_idx`, `documents_folder_path_prefix_idx`, etc.).
- Comment each index with its query-shape purpose (matches 008's numbered comments `-- 1.`, `-- 2.`).

---

### `backend/app/services/folder_service.py` (service / pure helpers)

**Primary analog:** `backend/app/services/record_manager.py` (70 lines, exact role match). It is the canonical "small stateless service module with pure functions and a `@dataclass` for return shapes" file.

**Secondary analog:** `backend/app/services/web_search.py` (44 lines) — even smaller, single-function module with module-level `logger`. Use this if `folder_service.py` ends up as just `normalize_path` with no other helpers.

**Module-docstring + import pattern** (record_manager.py lines 1–8):
```python
"""
Record Manager — content hashing and deduplication for document ingestion.
Determines whether an upload should be created, skipped, or updated.
"""
import hashlib
from dataclasses import dataclass
from typing import Optional
```
Convention: triple-quoted module docstring on line 1–3, blank line, then imports (stdlib first, then third-party, then `from app.…`). Per CONVENTIONS.md §"Import Organization".

**Pure-function pattern** (record_manager.py lines 17–24):
```python
def compute_file_hash(content: bytes) -> str:
    """SHA-256 hash of raw file bytes."""
    return hashlib.sha256(content).hexdigest()


def compute_chunk_hash(text: str) -> str:
    """SHA-256 hash of chunk text content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
```
Convention: type-hinted signatures, single-line docstring, no global state, no logging on the hot path.

**Module-level logger pattern** (web_search.py lines 5, 11) — only if folder_service ever needs to log:
```python
import logging
...
logger = logging.getLogger(__name__)
```

**Conventions to preserve (from CONVENTIONS.md):**
- snake_case function names (`normalize_path`, not `normalizePath`).
- Type hints on every parameter and return value.
- `from app.services.folder_service import normalize_path` is the importable surface — keep `normalize_path` at module level (no class wrapper).
- Per CONVENTIONS.md §"Module Design": no `__all__`, no barrel re-exports — direct import is the convention.
- Constants UPPER_SNAKE_CASE: `_CANONICAL_PATH_RE`, `_FORBIDDEN_SEGMENTS` (underscore prefix marks module-private; matches CONVENTIONS.md §"Variables" and the existing `_token_cache` / `_client_cache` / `_cache` patterns in `test_helpers.py`, `metadata.py`, `settings.py`).
- Raise `ValueError` on invalid input (no custom exception class for v1 — matches the implicit convention where `record_manager.py` returns a sentinel `RecordAction` object rather than raising; `normalize_path` is simpler and `ValueError` is the right Pythonic choice for invalid string inputs).

**Reference Python implementation** is in RESEARCH.md §3 (lines 244+) — copy that into the file.

---

### `backend/scripts/test_two_scope_rls.py` (test — multi-user RLS isolation)

**Primary analog:** `backend/scripts/test_rls.py` (107 lines, exact role match — the existing single-axis user-isolation test that this new test extends with the scope axis).

**Secondary analog:** `backend/scripts/test_settings.py` (66 lines) — for the admin 403/admin-allowed test pattern (lines 34–40), which 015 needs for `documents_insert_global` admin-gate validation.

**Tertiary analog:** `backend/scripts/test_files.py` lines 1–60 — for the file-upload-and-poll setup pattern that some assertions need.

**Test module shell + imports + section pattern** (test_rls.py lines 1–18):
```python
"""Row-Level Security isolation tests — two users cannot see each other's data."""
import sys
import os
import requests

sys.path.insert(0, os.path.dirname(__file__))
import test_helpers as h

CAPYBARA_TEXT = b"""The capybara is the largest living rodent. Native to South America."""


def run():
    h.reset_counters()
    token_a = h.get_auth_token(h.TEST_USER_A["email"], h.TEST_USER_A["password"])
    token_b = h.get_auth_token(h.TEST_USER_B["email"], h.TEST_USER_B["password"])
    headers_a = h.auth_headers(token_a)
    headers_b = h.auth_headers(token_b)
```
Convention: module docstring, `sys.path.insert(0, os.path.dirname(__file__))` then `import test_helpers as h`. `run()` is the entry point called by `test_all.py`. Always start with `h.reset_counters()`.

**Section + assertion pattern** (test_rls.py lines 40–47):
```python
# RLS: threads
h.section("RLS - Threads")
r = requests.get(f"{h.BASE_URL}/api/threads", headers=headers_b)
b_thread_ids = [t["id"] for t in r.json()] if r.status_code == 200 else []
h.test("B cannot see A's threads", a_thread_id not in b_thread_ids, str(b_thread_ids))
```
Convention: `h.section("…")` headers group related assertions; each assertion is `h.test(name, condition, detail)` — the `detail` argument is shown only on failure. Test names should be **falsifiable, single-claim sentences**. Phase 1 needs 40 such assertions (RLS-01..04, SCHEMA-01..05, FOLDER-01).

**Cleanup-in-finally pattern** (test_rls.py lines 94–99):
```python
finally:
    if a_thread_id:
        requests.delete(f"{h.BASE_URL}/api/threads/{a_thread_id}", headers=headers_a)
    if a_doc_id:
        requests.delete(f"{h.BASE_URL}/api/files/{a_doc_id}", headers=headers_a)
    # User B doesn't create any persistent data in these tests
```
**CRITICAL per CLAUDE.md:** "Tests must NEVER delete all user data. Tests must only clean up resources they created (tracked by ID)." `test_two_scope_rls.py` MUST follow this — track each created doc/folder ID in a list and delete only those in `finally`. **No `DELETE FROM` or `TRUNCATE`. No "delete all docs for user X" cleanup.**

**Admin-gate 403 pattern** (test_settings.py lines 34–40):
```python
# 4. PUT /api/settings returns 403 for non-admin (test user A is not admin by default)
r3 = requests.put(
    f"{h.BASE_URL}/api/settings",
    headers=h.auth_headers(token_a),
    json={"llm_model": "test-model"},
)
h.test("PUT /api/settings -> 403 non-admin", r3.status_code == 403, f"status={r3.status_code}")
```
Phase 1 reuses this exact shape for "non-admin user A cannot insert global-scope document → expect 403/RLS rejection."

**Standalone runnable footer** (test_rls.py lines 104–106, test_settings.py lines 61–65):
```python
if __name__ == "__main__":
    run()
    sys.exit(h.summary())
```
Convention: every test module must be runnable standalone via `python scripts/test_two_scope_rls.py`. Keep this footer.

**Conventions to preserve:**
- Filename `test_two_scope_rls.py` (snake_case, `test_` prefix).
- `run()` returns `(h.passed, h.failed)` tuple — required by `test_all.py:52`'s `p, f = module.run()`.
- Assertion count target = 40 (per RESEARCH.md / phase brief).
- Use `h.poll_document_status(token, doc_id, "ready", max_wait=30)` (test_helpers.py:130) for ingestion polling — never busy-loop manually.
- File-upload setup mirrors test_files.py:30–34 — `requests.post(.../api/files/upload, files={"file": (name, bytes, mime)})`.

---

### `backend/scripts/test_helpers.py` (modify — add admin fixture and helper)

**Existing pattern to extend** (test_helpers.py lines 14–19):
```python
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8001")
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]

TEST_USER_A = {"email": "testuser@example.com", "password": "testpassword123"}
TEST_USER_B = {"email": "test@test.com", "password": "supabase123"}
```
**Add adjacent to lines 18–19:**
```python
TEST_USER_ADMIN = {"email": "admin@test.com", "password": "<from-env-or-fixed>"}
```

**Existing `get_auth_token()` to mirror** (test_helpers.py lines 56–86) — this is the canonical anon-key + JWT login flow. The new `get_admin_token()` is a thin wrapper:
```python
def get_admin_token():
    """Get JWT for the admin test user.

    REQUIRES one-time setup: UPDATE public.profiles SET is_admin = true
    WHERE email = 'admin@test.com'; — see test_two_scope_rls.py docstring.
    """
    return get_auth_token(TEST_USER_ADMIN["email"], TEST_USER_ADMIN["password"])
```

**Conventions to preserve:**
- Use anon-key + JWT (NOT service-role) — matches existing `get_auth_token()` lines 64, 70, 78. Service-role bypasses RLS, which would invalidate every Phase 1 RLS test.
- Cache via the existing `_token_cache` dict (no new cache).
- Auto-signup fallback (test_helpers.py:67-80) means the first test run auto-creates `admin@test.com`; admin elevation is then a one-time manual SQL.
- Keep all changes additive — do not modify `TEST_USER_A` / `TEST_USER_B` (other suites depend on them).

---

### `backend/scripts/test_all.py` (modify — register new suite)

**Existing pattern to extend** (test_all.py lines 12–23):
```python
import test_helpers as h
import test_health
import test_auth
import test_threads
import test_messages
import test_files
import test_rag
import test_rls
import test_settings
import test_metadata
import test_hybrid
import test_tools
import test_sub_agents
```
**Add (alphabetical position is OK; after `test_rls` is the natural neighbor):**
```python
import test_two_scope_rls
```

**Existing SUITES list pattern** (test_all.py lines 25–38):
```python
SUITES = [
    ("Health", test_health),
    ("Auth", test_auth),
    ...
    ("RLS", test_rls),
    ("Settings", test_settings),
    ...
]
```
**Add (after `("RLS", test_rls)`):**
```python
    ("Two-Scope RLS", test_two_scope_rls),
```

**Conventions to preserve:**
- Tuple format: `("<Suite display name>", <module>)` — display name uses Title Case with hyphens for multi-word ("Two-Scope RLS", not "two_scope_rls").
- The module's `.run()` must return `(passed, failed)` (enforced at line 52: `p, f = module.run()`).
- Insertion point: after the existing `RLS` entry — keeps related suites adjacent in the output.
- Update the comment in CLAUDE.md test count from "(112 tests)" to "(152 tests)" (40 new assertions) — flag for planner.

---

## Shared Patterns

### Migration file shape (applies to 012, 013, 014, 015, 016)

**Source:** `backend/migrations/006_record_manager.sql` (header line 1), `008_hybrid_search.sql` (header lines 1–2).

**Required header comment style:**
```sql
-- Phase 1 / Migration 0NN: <one-line purpose>
-- <optional second line of context>
```
Then bare DDL — no `BEGIN`/`COMMIT` (the runner at `backend/scripts/run_migrations.py:50-52` wraps each file in a transaction and commits on success or rolls back on failure).

**Idempotency convention** (010, 011 example):
- `ALTER TABLE … ADD COLUMN IF NOT EXISTS …`
- `CREATE INDEX IF NOT EXISTS …`
- `CREATE EXTENSION IF NOT EXISTS …`
- `CREATE OR REPLACE FUNCTION …`
- `DROP TRIGGER IF EXISTS … ; CREATE TRIGGER …` (DROP-before-CREATE is the established idempotent pattern for triggers; same pattern applies to policies in 015)
- `DROP POLICY IF EXISTS "<exact name>" ON <table>` then `CREATE POLICY …` (no `OR REPLACE` for policies — Postgres doesn't support it)

**`CREATE INDEX CONCURRENTLY` is forbidden inside the migration runner** because the runner wraps each file in an explicit transaction (`autocommit = False` on `run_migrations.py:39`). All Phase 1 indexes use plain `CREATE INDEX IF NOT EXISTS` — verified in RESEARCH.md §"Existing Codebase Anchors".

### Service-layer file shape (applies to folder_service.py)

**Source:** `backend/app/services/record_manager.py` (the smallest pure-helpers service in the codebase).
- Module docstring on line 1.
- Imports ordered: stdlib, third-party, `from app.…`.
- Type hints on every signature.
- No global mutable state for pure helpers (compare to settings.py's `_cache` dict, which is acceptable for cached IO functions but not for pure helpers).
- snake_case functions, PascalCase classes, `_underscore_prefix` for module-private.

### Test module shape (applies to test_two_scope_rls.py)

**Source:** `backend/scripts/test_rls.py` + `backend/scripts/test_settings.py`.
- Module docstring describing what's tested.
- `sys.path.insert(0, os.path.dirname(__file__))` + `import test_helpers as h` (mandatory — required by the runner).
- `def run(): … return h.passed, h.failed` is the required entry-point signature (test_all.py:52 unpacks the tuple).
- `h.section("…")` to group assertions; `h.test(name, condition, detail)` for each individual assertion.
- `try / finally` cleanup that deletes ONLY tracked IDs.
- `if __name__ == "__main__": run(); sys.exit(h.summary())` footer.

### RLS-isolation test pattern (applies to test_two_scope_rls.py)

**Source:** `backend/scripts/test_rls.py` lines 12–101 (entire file is the template).
- Two-user setup: `token_a = h.get_auth_token(TEST_USER_A…)`, `token_b = h.get_auth_token(TEST_USER_B…)`.
- For Phase 1, **add a third user**: `token_admin = h.get_admin_token()` (the new helper).
- For each protected resource: A creates → B attempts to read/modify/delete → assert B cannot → assert A's data still intact.
- Use HTTP status codes 404/403 for "RLS hid the row" assertions: `r.status_code in (404, 500)` is the established idiom (test_rls.py:47, :51, :61, :68 — accepts both because some endpoints return 404 (RLS hides) and some return 500 (FK constraint trips first)).
- Track every created ID in a list; cleanup ONLY those IDs in `finally`. **Per CLAUDE.md: never bulk-delete.**

---

## No Analog Found

| File or Pattern | Reason | Recommendation for Planner |
|---|---|---|
| `DROP CONSTRAINT documents_user_filename_unique` (in migration 012) | No prior migration drops a constraint. Migration 003 drops *tables* (`DROP TABLE IF EXISTS uploaded_files CASCADE`) but not constraints. | Use `ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_user_filename_unique;` — `IF EXISTS` is the canonical safe form. Document in 012's header comment: "Replaces UNIQUE (user_id, file_name) from migration 006 with a scope+folder-aware unique index." |
| `DROP POLICY IF EXISTS …` (in migration 015) | No prior migration drops policies. The existing policy quartet from 003 has never been removed. | Use `DROP POLICY IF EXISTS "<exact-name-from-003>" ON public.<table>;` — names must match `003_byo_retrieval.sql:29-32` (documents) and `:51-53` (chunks) **verbatim** including capitalization and the word "own". |
| `CREATE POLICY … TO authenticated USING (… (SELECT auth.uid()) …)` | No prior migration uses `(SELECT auth.uid())` subquery form (Supabase's recommended optimization for caching the UID per query). All existing policies use bare `auth.uid() = user_id`. | Adopt the subquery form per RESEARCH.md catalog. Add a comment in 015 explaining "Postgres caches the (SELECT auth.uid()) result per query, faster than bare auth.uid() per row." Planner must be explicit so reviewers don't "fix" it back. |
| `CREATE INDEX … USING gin (col gin_trgm_ops)` and `CREATE INDEX … (col text_pattern_ops)` (in migration 016) | No prior migration uses `gin_trgm_ops` (007 uses default `jsonb_ops`, 008 uses default for tsvector). No prior migration uses any operator class on a btree index. | These are net-new patterns. Add a comment per index naming the op class and its purpose. Verify `pg_trgm` is enabled by 012 before 016 references `gin_trgm_ops` — runner runs files in lexical order, so 012 → 013 → … → 016 is guaranteed (run_migrations.py:28 uses `sorted()`). |
| `BEFORE UPDATE … forbid_scope_mutation()` trigger that RAISEs on column change | No prior trigger raises an exception. Existing triggers (005:`handle_new_user`, 008:`document_chunks_tsv_trigger`) just mutate `NEW` and return. | Pattern is novel but uses standard plpgsql `RAISE EXCEPTION … USING ERRCODE = 'check_violation';` — RESEARCH.md §1 has the exact body. Critical: the trigger must `RETURN NEW;` after the IF block (do not put `RETURN NEW;` inside the IF, or non-mutation updates will be discarded). |
| `TEST_USER_ADMIN` fixture + manual `UPDATE profiles SET is_admin=true …` SQL setup step | No prior test uses an admin user. test_settings.py:34-40 only tests the *negative* path (non-admin gets 403); it never exercises the admin-allowed path. | Per RESEARCH.md "Existing Codebase Anchors §Test harness": go with option (a) — add fixture, document one-time SQL setup in `test_two_scope_rls.py` module docstring. The docstring should include the literal SQL: `UPDATE public.profiles SET is_admin = true WHERE email = 'admin@test.com';` |

---

## Metadata

**Analog search scope:**
- `backend/migrations/*.sql` (12 files: 001–011 plus 003b)
- `backend/app/services/*.py` (10 files including `__init__.py`)
- `backend/scripts/test_*.py` (15 files)
- `backend/scripts/test_helpers.py`, `test_all.py`, `run_migrations.py`

**Files scanned:** 39

**Pattern extraction date:** 2026-05-02

**Sources cross-referenced:**
- `.planning/codebase/STRUCTURE.md` (directory layout, naming conventions)
- `.planning/codebase/CONVENTIONS.md` (code style, imports, error handling)
- `CLAUDE.md` (planning, testing, RLS rules, no-LangChain rule)
- `.planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-RESEARCH.md` (definitive file list, DDL skeletons, design decisions)
