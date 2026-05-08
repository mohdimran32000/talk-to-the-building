# Phase 4: Five Exploration Tools + search_documents Extension — Pattern Map

**Mapped:** 2026-05-08
**Files analyzed:** 12 (10 new + 2 modified)
**Analogs found:** 12 / 12 (100% — every new/modified file has a strong precedent in Phases 1–3)

Phase 4 is *additive plumbing* — every new file rhymes with a Phase 1/2/3 file already shipped. The planner should copy these patterns verbatim, only diverging where TOOL-01..10 / SEARCH-01..03 / TEST-02 explicitly demand new behavior.

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `backend/migrations/020_phase4_rpcs.sql` | migration / RPC | DB-side transform | `backend/migrations/019_folder_rename_and_delete_rpcs.sql` (+ `011_improved_keyword_search.sql` for `CREATE OR REPLACE` on existing functions) | exact (Phase 3 RPC migration shape) |
| `backend/app/services/exploration_tools/__init__.py` | package init / barrel | re-export | `backend/app/services/__init__.py` (empty package init) — no precedent for re-exporting symbols; planner picks shape | partial (no in-repo precedent for service sub-packages) |
| `backend/app/services/exploration_tools/schemas.py` | Pydantic v2 args module | validation | `backend/app/models/schemas.py` (FilePatch, FolderCreate, FolderPatch — `Optional`, defaults, `Pydantic v2 extra='ignore'` is implicit default) | role-match (request-body BaseModels, but routers vs. tool-args) |
| `backend/app/services/exploration_tools/_truncate.py` | utility / helper | transform | `backend/app/services/openai_client.py:567` (inline `result_text[:16000]` truncation precedent — char-based) | partial (idea exists inline; no extracted helper precedent) |
| `backend/app/services/exploration_tools/list_files.py` | service / tool | request-response | `backend/app/services/folder_service.py::list_folder` (`folder_service.py:126-255`) | exact (TOOL-04 is literally a sort + scope-tag wrapper around `list_folder`) |
| `backend/app/services/exploration_tools/tree.py` | service / tool | request-response | `backend/app/services/folder_service.py::list_folder` (recursed) | role-match (recursion is net-new but per-level shape identical) |
| `backend/app/services/exploration_tools/glob_match.py` | service / tool | request-response | `backend/app/services/folder_service.py::list_folder` (PostgREST `.like()` + `.or_()` UNION idiom) | role-match (LIKE→regex translation is net-new but query shape identical) |
| `backend/app/services/exploration_tools/read_document.py` | service / tool | request-response | `backend/app/services/folder_service.py::move_document` (single-row SELECT with RLS-aware filter) | role-match (single-doc fetch with `.maybe_single()`; line slicing is net-new pure-Python) |
| `backend/app/services/exploration_tools/grep.py` | service / tool | request-response (RPC wrapper) | `backend/app/services/folder_service.py::rename_folder` (RPC wrapper pattern; `folder_service.py:335-370`) | exact (thin Python wrapper around DB RPC) |
| `backend/scripts/test_exploration_tools.py` | test module | integration | `backend/scripts/test_folders.py` (591 lines, 10 sections, `_tracked_*`+`_cleanup`, canary `_verify_phase3_setup`, `_service_role_client`, ThreadPoolExecutor parallel insertion) | exact (Phase 4 test suite mirrors Phase 3 structure verbatim) |
| `backend/app/services/openai_client.py` (MODIFIED) | service / dispatcher | tool dispatch | itself — Phase 4 is additive `_build_*_tool()` factories (`openai_client.py:69-186`) + additive `elif tool_name == ...` arms (`openai_client.py:469-563`) + layered-fallback wrapper at `565-610` (TOOL-09 routing target) | exact (additive extension of existing in-file patterns) |
| `backend/scripts/test_all.py` (MODIFIED) | test runner | registry | itself — Phase 4 inserts one import + one `("Exploration", test_exploration_tools)` SUITES tuple between Folders and Backfill (already done for Folders at line 17 + line 34) | exact (Phase 3 set the precedent) |

**Match quality legend:**
- `exact` — analog is the same role + data flow with the same toolchain; copy verbatim with surface-level edits.
- `role-match` — analog shares role (e.g., service function with normalize_path + supabase query) but data-flow differs; reuse query/scope idiom, swap business logic.
- `partial` — analog shares one dimension only (idiom is inline elsewhere or barely precedented); planner uses RESEARCH.md patterns to fill the gap.

---

## Pattern Assignments

### `backend/migrations/020_phase4_rpcs.sql` (migration / RPC, DB-side transform)

**Analog:** `backend/migrations/019_folder_rename_and_delete_rpcs.sql` (Phase 3) — three RPCs in one file, shared design notes header. **Secondary analog:** `backend/migrations/011_improved_keyword_search.sql` for the `CREATE OR REPLACE` shape on an *existing* function (which is what SEARCH-02 does for `match_document_chunks_with_filters` and `match_document_chunks_hybrid`).

**Header / design-notes pattern** (`019_folder_rename_and_delete_rpcs.sql:1-37`):
```sql
-- Phase 3 / Migration 019: Folder rename + delete-if-empty + create-if-not-exists RPCs.
-- Bundles the three cross-table-transactional RPCs Phase 3's folders router needs.
-- Colocated here (vs. separate migration files) because they share PL/pgSQL idiom
-- and review surface; mirrors Phase 1's bundling of the full RLS catalog into 015.
--
-- DESIGN NOTES:
-- 1. ... [enumerated design rationale; HI-XX cross-references; SECURITY INVOKER reasoning]
-- 4. SECURITY INVOKER (the default) — RLS policies on documents / folders apply.
-- 7. CREATE OR REPLACE FUNCTION is idempotent — re-running this migration is a no-op.
```

**RPC declaration shape** (`019:47-56`):
```sql
CREATE OR REPLACE FUNCTION public.rename_folder_prefix(
  p_old_prefix TEXT,
  p_new_prefix TEXT,
  p_scope      TEXT,
  p_user_id    UUID DEFAULT NULL
)
RETURNS TABLE (documents_updated INT, folders_updated INT)
LANGUAGE plpgsql
SECURITY INVOKER       -- RLS applies; defense in depth
AS $$
DECLARE
  v_doc_count INT;
BEGIN
  -- Defense in depth: validate canonical form (matches CHECK from migrations 012/013)
  IF p_old_prefix !~ '^/$|^/[^/]+(/[^/]+)*$' THEN
    RAISE EXCEPTION 'old_prefix not canonical: %', p_old_prefix
      USING ERRCODE = 'check_violation';
  END IF;
  ...
END;
$$;

GRANT EXECUTE ON FUNCTION public.rename_folder_prefix(TEXT, TEXT, TEXT, UUID) TO authenticated;
```

**Key idioms to copy for the new `grep_documents` RPC:**
- `LANGUAGE plpgsql SECURITY INVOKER` (RLS applies; never `SECURITY DEFINER` for tools)
- `RETURNS TABLE (...)` over OUT parameters
- `RAISE EXCEPTION ... USING ERRCODE = 'check_violation'` for canonical-form validation
- `GRANT EXECUTE ... TO authenticated` after every function (not `service_role` — RLS path)
- `CREATE OR REPLACE` so re-running is idempotent

**LIKE-escape pattern** (`019:83-93`) to copy verbatim into `grep_documents` for the path-prefix predicate:
```sql
-- HI-03: Migration 012/013's canonical-form regex `^/[^/]+(/[^/]+)*$` allows
-- `_` and `%` in folder segments. Without escaping, LIKE would over-match.
v_old_prefix_like := replace(replace(replace(p_old_prefix,
                        '\', '\\'),
                        '%', '\%'),
                        '_', '\_');

UPDATE public.documents
   SET folder_path = p_new_prefix || substring(folder_path FROM length(p_old_prefix) + 1)
 WHERE scope = p_scope
   AND (p_user_id IS NULL OR user_id = p_user_id)
   AND (folder_path = p_old_prefix
        OR folder_path LIKE v_old_prefix_like || '/%' ESCAPE '\');
```

**`CREATE OR REPLACE` extending an existing function** (`011_improved_keyword_search.sql:6-15`) — this is the SEARCH-02 shape for adding `match_folder_path TEXT DEFAULT NULL` and `match_scope TEXT DEFAULT NULL`:
```sql
CREATE OR REPLACE FUNCTION match_document_chunks_hybrid(
  query_embedding  vector(768),
  query_text       TEXT,
  match_user_id    UUID,
  match_count      INT DEFAULT 20,
  metadata_filter  JSONB DEFAULT NULL,
  rrf_k            INT DEFAULT 60
)
RETURNS TABLE (id UUID, document_id UUID, content TEXT, rrf_score FLOAT)
LANGUAGE plpgsql AS $$
BEGIN
  RETURN QUERY
  WITH vector_results AS (
    SELECT dc.id, dc.document_id, dc.content,
           ROW_NUMBER() OVER (ORDER BY dc.embedding <=> query_embedding) AS vector_rank
    FROM document_chunks dc
    JOIN documents d ON dc.document_id = d.id
    WHERE dc.user_id = match_user_id
      AND dc.embedding IS NOT NULL
      AND (metadata_filter IS NULL OR d.metadata @> metadata_filter)
    ORDER BY dc.embedding <=> query_embedding
    LIMIT match_count * 2
  ), ...
```
Phase 4 adds two new tail-position parameters with `DEFAULT NULL` (non-breaking — supabase-py / PostgREST never requires keyword args to be sent). Apply identical NULL-guarded predicates in BOTH the `vector_results` CTE and the `keyword_results` CTE for `match_document_chunks_hybrid`.

**Net-new (no in-repo precedent — see RESEARCH.md §Tool Block C):**
- `SET LOCAL statement_timeout = '5s';` inside the PL/pgSQL function body (Pitfall 3 mitigation; PostgREST opens one transaction per `.execute()` call, so `SET LOCAL` is correctly scoped).
- `CROSS JOIN LATERAL regexp_split_to_table(c.content_markdown, E'\n') WITH ORDINALITY AS lines(line_text, line_no)` — Postgres-standard idiom for line-resolved regex.
- ILIKE pre-filter to make `documents_content_markdown_trgm_idx` (Migration 016) fire.

---

### `backend/app/services/exploration_tools/__init__.py` (package init / barrel)

**Analog:** `backend/app/services/__init__.py` (empty) — there is no in-repo precedent for a service-package barrel file. Existing services are flat single files imported by symbol (`from app.services.folder_service import normalize_path, list_folder`).

**Recommended shape** (synthesizing CONVENTIONS.md "Named exports preferred" + RESEARCH.md §Recommended Project Structure):
```python
"""Phase 4 exploration tools — public surface.

Re-exports the five tool entry points and the shared schemas module so callers
can `from app.services.exploration_tools import tree, glob_match, grep, ...`
without depth-walking each submodule.
"""
from app.services.exploration_tools.tree import tree
from app.services.exploration_tools.glob_match import glob_match
from app.services.exploration_tools.grep import grep
from app.services.exploration_tools.list_files import list_files
from app.services.exploration_tools.read_document import read_document
from app.services.exploration_tools import schemas  # re-export the module

__all__ = ["tree", "glob_match", "grep", "list_files", "read_document", "schemas"]
```

**Why `__all__` despite CONVENTIONS.md saying "no explicit __all__":** the existing convention is single-file modules where `_`-prefix is the public/private signal. A package-with-multiple-public-symbols is net-new in the codebase — being explicit costs nothing and aids the planner's import-smoke test in `test_exploration_tools.py [Phase 4 module import smoke]`.

---

### `backend/app/services/exploration_tools/schemas.py` (Pydantic v2 args module, validation)

**Analog:** `backend/app/models/schemas.py` (the only existing Pydantic v2 home in the repo).

**Existing patterns** (`models/schemas.py:57-69`):
```python
from pydantic import BaseModel
from typing import Optional


class FolderCreate(BaseModel):
    path: str
    scope: str = "user"                 # 'user' | 'global'


class FolderPatch(BaseModel):
    new_path: str


class FilePatch(BaseModel):
    # Mutable fields ONLY. scope is IMMUTABLE per Migration 015 forbid_scope_mutation
    # trigger; Pydantic v2 ignores unknown fields by default, so a smuggled "scope"
    # in the request body is silently dropped here (defense in depth).
    file_name: Optional[str] = None
    folder_path: Optional[str] = None
```

**Phase 3 / Plan 01 LOCKED Pydantic v2 idioms** (already in production):
1. **`extra='ignore'` is the silently-drop-smuggled-fields defense** — currently Pydantic v2's *implicit default*; the comment on `FilePatch` documents the contract (scope smuggling test in `test_folders.py:495-505` proves it works). Phase 4 makes this **explicit** via `model_config = {"extra": "ignore"}` because the LLM is the caller and exotic args are likely.
2. **`Optional[str] = None` for nullable fields** with default — see `DocumentResponse.user_id: Optional[str] = None` (`schemas.py:34`).
3. **String defaults for enum-like fields** (`scope: str = "user"`) — Phase 4 upgrades these to `Literal["user","global","both"]` because the LLM tool-args layer benefits from strict enum rejection.

**Phase 4 net-new (RESEARCH.md §TOOL-06 Shared Pydantic Schema Module — direct copy):**
```python
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator

# Canonical path regex — mirrors Migration 012 CHECK and folder_service._CANONICAL_PATH_RE
_PATH_RE = r"^/$|^/[^/]+(/[^/]+)*$"


class TreeArgs(BaseModel):
    path: str = Field("/", pattern=_PATH_RE)
    max_depth: int = Field(2, ge=1, le=4)
    scope: Literal["user", "global", "both"] = Field("both")
    model_config = {"extra": "ignore"}


class GlobArgs(BaseModel):
    pattern: str = Field(..., min_length=1, max_length=200)
    path: str = Field("/", pattern=_PATH_RE)
    type: Literal["file", "folder", "both"] = Field("both")
    scope: Literal["user", "global", "both"] = Field("both")
    model_config = {"extra": "ignore"}


class GrepArgs(BaseModel):
    pattern: str = Field(..., min_length=1, max_length=500)
    path: str = Field("/", pattern=_PATH_RE)
    case_insensitive: bool = Field(True)
    multiline: bool = Field(False)
    output_mode: Literal["content", "files_with_matches", "count"] = Field("content")
    A: int = Field(2, ge=0, le=10)
    B: int = Field(2, ge=0, le=10)
    C: Optional[int] = Field(None, ge=0, le=10)
    scope: Literal["user", "global", "both"] = Field("both")
    model_config = {"extra": "ignore"}


class ListFilesArgs(BaseModel):
    path: str = Field("/", pattern=_PATH_RE)
    scope: Literal["user", "global", "both"] = Field("both")
    model_config = {"extra": "ignore"}


class ReadDocumentArgs(BaseModel):
    document_id: Optional[str] = Field(None)
    path: Optional[str] = Field(None, pattern=r"^/$|^/[^/]+(/[^/]+)*/[^/]+$")
    offset: int = Field(1, ge=1)
    limit: int = Field(2000, ge=1, le=5000)
    model_config = {"extra": "ignore"}

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.document_id is None) == (self.path is None):
            raise ValueError("Specify exactly one of document_id or path")
        return self
```

**Notes on the path regex string:** `_PATH_RE` MUST be byte-identical to `folder_service._CANONICAL_PATH_RE` (`folder_service.py:23`) and Migration 012's CHECK constraint. Triple chokepoint = Pitfall 4 mitigation. **DO NOT** redefine the regex with subtly-different escaping.

---

### `backend/app/services/exploration_tools/_truncate.py` (utility, transform)

**Analog:** **inline** at `backend/app/services/openai_client.py:567` (Phase 1 char-truncation precedent).

**Inline precedent** (`openai_client.py:565-575`):
```python
# Context injection for the final answer (skip the tool round-trip)
# Truncate very large results to avoid empty Gemini responses
truncated_result = result_text[:16000] if len(result_text) > 16000 else result_text
system_with_context = f"""You are a helpful assistant. Use the provided tool results to answer the user's question accurately.
...
Tool ({tool_name}) results:
{truncated_result}"""
```

**Phase 4 helper** (RESEARCH.md §Pattern B):
```python
"""TOOL-08 12K-char truncation helper.

Applied at the END of every tool function. Stateless. Centralizes the
'[...truncated, N more]' marker contract so the planner doesn't hand-roll
per-tool truncation.
"""
import json


def apply_12k_cap(payload: dict, *, char_cap: int = 12_000) -> dict:
    """Truncate a tool result dict and append the marker if it overflows.

    Strategy:
      1. JSON-serialize the payload.
      2. If serialized < char_cap: return payload unchanged (truncation_marker=None).
      3. Otherwise: identify the main list field ('entries' | 'hits' | 'matches'),
         drop entries from the END until under cap, count drops, set
         payload['truncation_marker'] = '[...truncated, N more entries]'.
      4. Return the trimmed payload. The marker is a string field NEXT TO the
         trimmed list — never embedded in it (the LLM sees a clean list + a
         neighbor marker rather than a poisoned final element).
    """
    # implementation — leverages json.dumps(payload, default=str) for length probe
```

**Why 12K instead of 16K:** the existing `openai_client.py:567` constant is 16K (a Gemini-context-window heuristic). Phase 4 / TOOL-08 specifies 12K (a Phase 4 LLM-readability heuristic — the goal is to keep tool results legible, not to fit them into the Gemini window — that's the wrapper's job). Both caps coexist; the 12K cap fires first inside the tool, the 16K cap fires second in the wrapper.

---

### `backend/app/services/exploration_tools/list_files.py` (TOOL-04, request-response)

**Analog:** `backend/app/services/folder_service.py::list_folder` (`folder_service.py:126-255`).

**Service function shape** (`folder_service.py:126-156`):
```python
def list_folder(
    path: str,
    scope: str,
    user_id: str | None,
    supabase_client,
) -> dict:
    """List one level of a folder: documents at this path + immediate subfolders.

    Returns:
        {
          "path": str,                # normalized path
          "documents": list[dict],
          "subfolders": list[str],
        }
    """
    norm = normalize_path(path)

    # HI-01: defense in depth against PostgREST DSL injection via user_id f-strings.
    if scope in ("user", "both"):
        _assert_uuid(user_id, field_name="user_id")

    # ─ Documents at this exact folder ─
    docs_q = supabase_client.table("documents").select("*").eq("folder_path", norm)
    if scope == "user":
        docs_q = docs_q.eq("scope", "user").eq("user_id", user_id)
    elif scope == "global":
        docs_q = docs_q.eq("scope", "global").is_("user_id", "null")
    else:  # 'both' — union via or_(); see PostgREST or() syntax
        docs_q = docs_q.or_(
            f"and(scope.eq.user,user_id.eq.{user_id}),and(scope.eq.global,user_id.is.null)"
        )
    try:
        docs_resp = docs_q.execute()
        documents = docs_resp.data or []
    except Exception as e:
        logger.error(f"list_folder documents query failed for path={path!r} scope={scope!r}: {e}", exc_info=True)
        documents = []
    ...
```

**TOOL-04 implementation pattern** (RESEARCH.md §Tool Block D — direct adaptation):
```python
from langsmith import traceable
from app.services.folder_service import normalize_path, list_folder
from app.services.exploration_tools.schemas import ListFilesArgs
from app.services.exploration_tools._truncate import apply_12k_cap


@traceable(name="list_files", run_type="tool")
def list_files(args: ListFilesArgs, user_id: Optional[str], supabase_client) -> dict:
    norm = normalize_path(args.path)
    folder = list_folder(norm, args.scope, user_id, supabase_client)

    # Phase 4 ORDERING contract (TOOL-04): folders-then-files, alpha within each.
    folders_sorted = sorted(folder["subfolders"])
    docs_sorted = sorted(folder["documents"], key=lambda d: d.get("file_name", "").lower())

    entries = []
    for sf in folders_sorted:
        entries.append({"type": "folder", "path": sf,
                        "scope": _infer_scope(sf, folder["documents"])})  # see RESEARCH §A5
    for d in docs_sorted:
        entries.append({
            "type": "doc",
            "document_id": d["id"],
            "file_name": d["file_name"],
            "folder_path": d["folder_path"],
            "scope": d["scope"],   # already projected from folder_service.list_folder
        })

    return apply_12k_cap({
        "tool": "list_files",
        "scope_arg": args.scope,
        "path": norm,
        "entries": entries,
        "total": len(entries),
    })
```

**Key cross-cutting patterns to copy:**
- `normalize_path()` as **first statement** (Pitfall 4 chokepoint — `folder_service.py:156`).
- `@traceable(name="...", run_type="tool")` decorator (TOOL-10 — matches `sql_tool.py:56`, `openai_client.py:251`).
- Reuse `list_folder` rather than reimplementing the UNION (Don't Hand-Roll table from RESEARCH.md).

---

### `backend/app/services/exploration_tools/tree.py` (TOOL-01, request-response)

**Analog:** `backend/app/services/folder_service.py::list_folder` (recursive caller).

**Reuse:** Phase 4 tree.py is a recursive driver of `list_folder()` with a budget counter and per-level summarization. Each recursion step inherits the scope-aware UNION query from `list_folder`. **Do not duplicate the UNION query** — call `list_folder()` per level.

**Per-level summary shape** (RESEARCH.md §Tool Block A):
```python
{"type": "folder", "path": "/projects/2026", "scope": "user",
 "more_folders": 3, "more_docs": 12}
```
This is the `[N more folders, M more docs]` structure ROADMAP SC1 requires (literal text rendering happens at the LLM-prompt boundary).

**Algorithm idioms shared with list_files.py:**
- `normalize_path()` first
- `@traceable(name="tree", run_type="tool")`
- Wrap final dict in `apply_12k_cap()`
- Every entry/child carries a `scope` field (TOOL-07 invariant)

**Net-new (no in-repo precedent):** the iterative-BFS-with-`entries_remaining` budget loop. RESEARCH.md §Open Questions #5 recommends iterative BFS for cleaner shutdown and easier testing.

---

### `backend/app/services/exploration_tools/glob_match.py` (TOOL-02, request-response)

**Analog:** `backend/app/services/folder_service.py::list_folder` for the `.like()` + `.or_()` UNION query shape.

**Reusable PostgREST idioms** (`folder_service.py:200-208`):
```python
if norm == "/":
    f_q = f_q.neq("path", "/").not_.like("path", "/%/%")
else:
    # HI-03: escape `%` and `_` in `norm` so a folder name containing
    # those literals does not become a wildcard in the LIKE predicate.
    esc = _escape_like(norm)
    f_q = f_q.like("path", f"{esc}/%").not_.like("path", f"{esc}/%/%")
```

**Net-new for glob_match.py:**
- Glob-→-regex translation (RESEARCH.md §Tool Block B walking left-to-right: `*` → `[^/]*`, `**` → `.*`, anchor with `^/?`).
- `documents.folder_path ~ <regex>` predicate (PostgREST `match` operator).
- Type-branch (file | folder | both): file uses documents query; folder uses `folders` UNION inferred-from-documents (same UNION shape as list_folder).
- Hard cap on matches (TOOL-08 `apply_12k_cap` post-filter).

**Cross-cutting:** `_escape_like()` (`folder_service.py:84-98`) is the existing chokepoint for LIKE-wildcard escaping; reuse it for any literal-prefix component of the glob pattern.

---

### `backend/app/services/exploration_tools/read_document.py` (TOOL-05, request-response)

**Analog:** `backend/app/services/folder_service.py::move_document` (`folder_service.py:309-332`) for the single-row SELECT-by-id + RLS-aware filter idiom.

**Single-row SELECT pattern** (`folder_service.py:323-329`):
```python
result = (
    supabase_client.table("documents")
    .update({"folder_path": norm})
    .eq("id", document_id)
    .eq("user_id", user_id)
    .execute()
)
if not result.data:
    return None
return result.data[0]
```

**Phase 4 read_document.py — adaptation** (RESEARCH.md §Tool Block E):
```python
@traceable(name="read_document", run_type="tool")
def read_document(args: ReadDocumentArgs, user_id: Optional[str], supabase_client) -> dict:
    # Resolve to a single row
    if args.document_id:
        row = supabase_client.table("documents") \
            .select("id, file_name, folder_path, scope, content_markdown, content_markdown_status") \
            .eq("id", args.document_id).maybe_single().execute().data
    else:
        norm_path = normalize_path(args.path[:args.path.rfind("/")] or "/")
        file_name = args.path[args.path.rfind("/")+1:]
        row = supabase_client.table("documents") \
            .select("id, file_name, folder_path, scope, content_markdown, content_markdown_status") \
            .eq("folder_path", norm_path).eq("file_name", file_name) \
            .maybe_single().execute().data

    if not row:
        return {"tool": "read_document", "error": "NOT_FOUND",
                "message": f"No document at {args.path or args.document_id}"}

    # LOCKED contract from 02-CONTEXT.md (Phase 2): non-ready -> pending_reindex
    if row["content_markdown_status"] != "ready":
        return {
            "tool": "read_document",
            "document_id": row["id"], "file_name": row["file_name"],
            "scope": row["scope"], "folder_path": row["folder_path"],
            "status": "pending_reindex",
            "content_markdown_status": row["content_markdown_status"],
        }

    # Line slicing — CRLF normalized at ingestion (Phase 2 contract); splitlines is safe.
    lines = (row["content_markdown"] or "").splitlines(keepends=False)
    ...
```

**Key invariants** (Pitfall 9 mitigation):
- `splitlines(keepends=False)` consistently — line count stable across CRLF/LF/CR.
- `start_idx = args.offset - 1` — 1-based external, 0-based internal.
- UTF-8 codepoint-safe truncation: `bytes_or_str.encode("utf-8")[:N].decode("utf-8", errors="ignore")`.
- Arrow-form: `f"{n}→{line}"` literal `→` (U+2192).
- RLS handles cross-user isolation; `.maybe_single()` returns None when RLS hides the row (TEST-02 fixture verifies).

---

### `backend/app/services/exploration_tools/grep.py` (TOOL-03, request-response — RPC wrapper)

**Analog:** `backend/app/services/folder_service.py::rename_folder` (`folder_service.py:335-370`) — the established **RPC-wrapper service-function pattern**.

**RPC-wrapper pattern** (`folder_service.py:335-370`):
```python
def rename_folder(
    old_path: str,
    new_path: str,
    scope: str,
    user_id: str | None,
    supabase_client,
) -> dict:
    """Rename a folder (transactional prefix update on documents + folders).

    Calls Migration 019's rename_folder_prefix RPC — the only cross-table-atomic
    unit available from supabase-py (PostgREST executes each .execute() in its own
    transaction; only RPCs span multiple statements atomically). FOLDER-03.
    """
    old_norm = normalize_path(old_path)
    new_norm = normalize_path(new_path)
    if old_norm == "/" or new_norm == "/":
        raise ValueError("cannot rename root path")

    result = supabase_client.rpc("rename_folder_prefix", {
        "p_old_prefix": old_norm,
        "p_new_prefix": new_norm,
        "p_scope": scope,
        "p_user_id": user_id,
    }).execute()

    if not result.data:
        return {"documents_updated": 0, "folders_updated": 0}
    row = result.data[0]
    return {
        "documents_updated": row.get("documents_updated", 0),
        "folders_updated": row.get("folders_updated", 0),
    }
```

**Phase 4 grep.py adaptation** (RESEARCH.md §Tool Block C):
```python
import re
from langsmith import traceable
from app.services.folder_service import normalize_path, _assert_uuid
from app.services.exploration_tools.schemas import GrepArgs
from app.services.exploration_tools._truncate import apply_12k_cap


@traceable(name="grep", run_type="tool")
def grep(args: GrepArgs, user_id: Optional[str], supabase_client) -> dict:
    norm = normalize_path(args.path)

    # Pre-screen: reject pathological regexes (Pitfall 3 #6)
    try:
        re.compile(args.pattern)
    except re.error as e:
        return {"tool": "grep", "error": "INVALID_REGEX", "message": str(e)}
    if any(banned in args.pattern for banned in ("(.*)+", "(.+)+")):
        return {"tool": "grep", "error": "PATHOLOGICAL_REGEX",
                "message": "Pattern contains nested unbounded repetition"}

    # Auto-extract a literal substring (≥3 chars) for the ILIKE pre-filter
    literal_hint = _extract_literal_substring(args.pattern, min_len=3)

    scope_param = None if args.scope == "both" else args.scope
    if scope_param in ("user", None):
        _assert_uuid(user_id, "user_id")    # HI-01 from Phase 3

    result = supabase_client.rpc("grep_documents", {
        "p_pattern": args.pattern,
        "p_path_prefix": norm,
        "p_scope": scope_param,
        "p_user_id": user_id,
        "p_case_insensitive": args.case_insensitive,
        "p_max_hits": 50,
        "p_literal_substring": literal_hint,
    }).execute()
    rows = result.data or []
    ...
    return apply_12k_cap({
        "tool": "grep",
        "scope_arg": args.scope,
        "pattern": args.pattern,
        "path": norm,
        "hits": [...],   # each carries scope (TOOL-07)
        "total_hits": len(rows),
    })
```

**Key idioms reused from `folder_service`:**
- `normalize_path()` first (Pitfall 4)
- `_assert_uuid(user_id, "user_id")` before any RPC where user_id is interpolated (HI-01)
- `supabase_client.rpc("...", {...}).execute()` — same call shape as `rename_folder`
- `result.data or []` defensive default

---

### `backend/scripts/test_exploration_tools.py` (TEST-02, integration)

**Analog:** `backend/scripts/test_folders.py` — Phase 3's 591-line, 10-section, 36-h.test integration suite. Phase 4 mirrors it line-for-line.

**Top-of-module bootstrap** (`test_folders.py:36-69`):
```python
import concurrent.futures
import os
import sys
import uuid

import requests

# Two-step sys.path bootstrap (matches test_two_scope_rls.py:32-37 + test_backfill.py:39-40).
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

import test_helpers as h  # noqa: E402
from app.services.folder_service import normalize_path  # noqa: E402,F401
from supabase import create_client  # noqa: E402

CAPYBARA_TEXT = b"..."
STORAGE_BUCKET = "documents"

# Tracking lists for scoped cleanup. Per CLAUDE.md: never bulk-delete.
_tracked_documents: list = []
_tracked_folders: list = []
_tracked_storage_paths: list = []


def _service_role_client():
    """Return a service-role Supabase client (mirrors auth.py:8-12)."""
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )


def _track_doc(doc_id, sb_client):
    if doc_id:
        _tracked_documents.append((doc_id, sb_client))
```

**Canary precheck pattern** (`test_folders.py:94-128`) — to be mirrored as `_verify_phase4_setup`:
```python
def _verify_phase3_setup(sb_admin):
    """Pre-flight: assert Migration 019's RPCs exist AND folders router is registered.

    Mirrors test_two_scope_rls.py::_verify_admin_setup and
    test_backfill.py::_verify_storage_setup. Returns (ok, message).
    """
    # Probe 1: rename_folder_prefix exists. Call with non-matching prefix -> no-op.
    try:
        r = sb_admin.rpc("rename_folder_prefix", {
            "p_old_prefix": f"/probe-{uuid.uuid4().hex[:8]}",
            "p_new_prefix": f"/probe-renamed-{uuid.uuid4().hex[:8]}",
            "p_scope": "user",
            "p_user_id": "00000000-0000-0000-0000-000000000000",
        }).execute()
        if r.data is None:
            return False, "rename_folder_prefix returned no data - function exists but is broken"
    except Exception as e:
        return False, (
            f"rename_folder_prefix RPC missing or errored: {type(e).__name__}: {e}. "
            f"Did you apply Migration 019 via run_migrations.py?"
        )
    # Probe 2: GET /api/folders responds (router registered in main.py).
    try:
        r2 = requests.get(f"{h.BASE_URL}/api/folders", timeout=5)
        if r2.status_code == 404:
            return False, (...)
    except Exception as e:
        return False, (
            f"Backend unreachable: {e}. Start with: "
            f"cd backend && venv/Scripts/python -m uvicorn app.main:app --reload --port 8001"
        )
    return True, "ok"
```

**Phase 4 canary probes:**
1. `grep_documents` RPC exists (Migration 020 applied)
2. `match_document_chunks_with_filters` accepts `match_folder_path` keyword (call with `{"match_folder_path": None}` → no error)
3. `documents_content_markdown_trgm_idx` exists (probe via psycopg2 if `DATABASE_URL` is set; SKIP gracefully otherwise — `test_folders.py:303-308` is the SKIP idiom)
4. Backend responds at `BASE_URL`
5. At least one ready document exists in the corpus

**Cleanup discipline** (`test_folders.py:131-155`) — per-id deletes only, never bulk:
```python
def _cleanup():
    """Delete only tracked resources. Per CLAUDE.md: never bulk-delete."""
    for did, client in _tracked_documents:
        try:
            client.table("document_chunks").delete().eq("document_id", did).execute()
        except Exception:
            pass
        try:
            client.table("documents").delete().eq("id", did).execute()
        except Exception:
            pass
    for fid, client in _tracked_folders:
        try:
            client.table("folders").delete().eq("id", fid).execute()
        except Exception:
            pass
    if _tracked_storage_paths:
        try:
            sb = _service_role_client()
            sb.storage.from_(STORAGE_BUCKET).remove(_tracked_storage_paths)
        except Exception:
            pass
    _tracked_documents.clear()
    _tracked_folders.clear()
    _tracked_storage_paths.clear()
```

**Service-role bulk-insert helper** (NEW for Phase 4 — RESEARCH.md §Sub-section: 5000-doc grep fixture):
```python
def _seed_5000_doc_grep_fixture(sb_admin, user_id):
    base_path = f"/grep-fixture-{uuid.uuid4().hex[:8]}"
    rows = []
    for i in range(5000):
        marker = f"M{i:05d}"
        content = f"# Doc {i}\nLine 1.\nLine 2 contains capybara reference {marker}.\nLine 3.\nLine 4.\n"
        rows.append({
            "user_id": user_id, "scope": "user", "folder_path": base_path,
            "file_name": f"doc-{i:04d}.txt",
            "file_size": len(content), "mime_type": "text/plain", "status": "ready",
            "content_markdown": content, "content_markdown_status": "ready",
        })
    # Bulk insert in batches of 500 (Supabase default request size limit)
    BATCH = 500
    inserted_ids = []
    for batch_start in range(0, len(rows), BATCH):
        result = sb_admin.table("documents").insert(rows[batch_start:batch_start+BATCH]).execute()
        inserted_ids.extend(d["id"] for d in (result.data or []))
    for did in inserted_ids: _tracked_documents.append((did, sb_admin))
    return base_path, inserted_ids
```

**Section structure idiom** — `h.section()` + `h.test()` per assertion (test_folders.py uses 10 sections; Phase 4 uses 10 sections matching VALIDATION.md's [Tool surface], [TOOL-01..05], [TOOL-06..10], [SEARCH-01..03], [TOOL-09 empty-response], [Cross-user isolation]).

**ThreadPoolExecutor parallel-insertion pattern** (`test_folders.py:559-560`) — reusable for any future "concurrent-uploads" or "concurrent-tool-calls" stress test:
```python
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
    results = list(ex.map(_upload, range(10)))
```

---

### `backend/app/services/openai_client.py` (MODIFIED — service / dispatcher)

**Analog:** itself. Phase 4 is *additive* — new factories alongside existing four; new dispatch arms after `analyze_document`; unchanged everything else.

**`_build_*_tool()` factory pattern** (`openai_client.py:69-186`) — copy the shape from `_build_search_tool()` for SEARCH-01 extension and from `_build_analyze_tool()` (a non-dynamic factory) as the closest model for the five new factories:
```python
def _build_analyze_tool() -> types.FunctionDeclaration:
    """Build the analyze_document tool definition for deep single-document analysis."""
    return types.FunctionDeclaration(
        name="analyze_document",
        description=(
            "REQUIRED for summarizing, reviewing, or analyzing a document. "
            "Loads the FULL document content for comprehensive analysis. "
            "Use this whenever the user says 'summarize', 'analyze', 'review', or 'explain' a document."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "document_name": types.Schema(
                    type="STRING",
                    description="Name of the document to analyze",
                ),
                "question": types.Schema(
                    type="STRING",
                    description="What to analyze about this document",
                ),
            },
            required=["document_name", "question"],
        ),
    )
```

**SEARCH-01 in-place extension target** (`openai_client.py:99-117`):
```python
return types.FunctionDeclaration(
    name="search_documents",
    description=(
        "Find specific facts, quotes, or snippets across the user's documents. "
        "Returns only a few matching excerpts — NOT suitable for summarizing or analyzing a whole document. "
        "Do NOT use this for summarization requests — use analyze_document instead."
    ),
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "query": types.Schema(
                type="STRING",
                description="A SHORT, focused search query (under 50 words). ...",
            ),
            **filter_properties,
            # SEARCH-01 NEW: add folder_path + scope optional properties (RESEARCH.md §SEARCH-01)
        },
        required=["query"],
    ),
)
```

**Tool-registration loop** (`openai_client.py:337-357`) — additive insertion target:
```python
function_declarations = []
if has_documents:
    try:
        function_declarations.append(_build_analyze_tool())
    except Exception as e:
        logger.warning(f"Failed to build analyze tool (non-fatal): {e}")
    try:
        function_declarations.append(_build_search_tool())
    except Exception as e:
        logger.warning(f"Failed to build search tool (non-fatal): {e}")
    # Phase 4 inserts 5 new try/except registrations here, identical shape.
```

**Dispatch elif arm** (`openai_client.py:469-563`) — additive insertion target after `analyze_document` branch (`openai_client.py:561`):
```python
elif tool_name == "analyze_document":
    from app.services.sub_agent import run_sub_agent
    doc_name = args.get("document_name", "")
    question = args.get("question", "")

    doc = supabase_client.table("documents") \
        .select("id, file_name") \
        .eq("user_id", user_id) \
        .ilike("file_name", f"%{doc_name}%") \
        .order("created_at", desc=True) \
        .limit(1).execute()

    if not doc.data:
        result_text = f"No document matching '{doc_name}' found."
    else:
        doc_id = doc.data[0]["id"]
        actual_name = doc.data[0]["file_name"]
        sub_agent_result = ""
        for evt_type, evt_data in run_sub_agent(doc_id, actual_name, question, user_id, supabase_client):
            yield (evt_type, evt_data)
            if evt_type == "sub_agent_done":
                sub_agent_result = evt_data
        result_text = sub_agent_result

else:
    logger.warning(f"Unknown tool: {tool_name}")
    result_text = f"Unknown tool: {tool_name}"
```

**Phase 4 new arm shape** (RESEARCH.md §Code Examples — verbatim contract):
```python
elif tool_name == "tree":
    from app.services.exploration_tools import tree as tool_tree
    from app.services.exploration_tools.schemas import TreeArgs
    try:
        tree_args = TreeArgs(**args)
    except Exception as e:
        result_text = json.dumps({"error": "INVALID_ARGS", "message": str(e)})
        yield ("tool_done", json.dumps({"tool": tool_name, "detail": "Invalid arguments"}))
    else:
        tree_result = tool_tree(tree_args, user_id, supabase_client)
        result_text = json.dumps(tree_result)
        yield ("tool_done", json.dumps({"tool": tool_name,
                                        "detail": f"{tree_result.get('total_folders', 0)} folders, "
                                                  f"{tree_result.get('total_docs', 0)} docs"}))
```

**Layered-fallback wrapper (TOOL-09 routing target)** — `openai_client.py:565-610` — **DO NOT MODIFY**, only ROUTE TO:
```python
# Context injection for the final answer (skip the tool round-trip)
# Truncate very large results to avoid empty Gemini responses
truncated_result = result_text[:16000] if len(result_text) > 16000 else result_text
system_with_context = f"""You are a helpful assistant. Use the provided tool results to answer the user's question accurately.
If the tool encountered an error, explain the issue to the user in simple terms and suggest they rephrase their question.
If the results do not contain enough information, clearly state that the available documents do not contain the answer. Do NOT dump or echo the raw tool results back to the user. Instead, briefly explain what information was found (if any) and suggest the user try a different query or upload a document that might contain the answer. You may answer from general knowledge if applicable, but clearly label it as such.
When citing web sources, include the URLs.
{OUTPUT_FORMAT_RULES}

Tool ({tool_name}) results:
{truncated_result}"""

# Stream the final answer without tools (avoids thought_signature issue)
response2 = client.models.generate_content_stream(
    model=model,
    contents=contents,
    config=types.GenerateContentConfig(system_instruction=system_with_context),
)

has_response2_text = False
for chunk in response2:
    if chunk.text:
        has_response2_text = True
        yield ("token", chunk.text)

# Safeguard: if streaming returned nothing, try non-streaming as fallback
if not has_response2_text:
    logger.warning(f"Context injection streaming returned empty for tool={tool_name}, trying non-streaming fallback")
    try:
        fallback = client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=system_with_context),
        )
        if fallback.candidates and fallback.candidates[0].content and fallback.candidates[0].content.parts:
            for part in fallback.candidates[0].content.parts:
                if part.text:
                    has_response2_text = True
                    yield ("token", part.text)
    except Exception as e:
        logger.warning(f"Non-streaming fallback also failed: {e}")

# Last resort: yield the tool result directly
if not has_response2_text and result_text:
    logger.warning(f"All context injection attempts failed for tool={tool_name}, yielding raw result")
    yield ("token", result_text)
```

**TOOL-09 compliance contract:** every new tool dispatch arm must assign to the same `result_text` variable — the wrapper at lines 565-610 already executes after the `if has_function_call:` branch closes. **No tool reinvents the wrapper.** No tool calls `client.models.generate_content_stream` itself.

**System prompt (SEARCH-03) insertion target** — `_build_system_prompt()` at `openai_client.py:39-66`:
```python
def _build_system_prompt(has_documents: bool, has_structured_data: bool, web_search_enabled: bool) -> str:
    if not has_documents and not has_structured_data and not web_search_enabled:
        return SYSTEM_PROMPT_NO_DOCS

    parts = ["You are a helpful assistant with access to the following tools:"]

    if has_documents:
        parts.append("- analyze_document: ...")
        parts.append("- search_documents: ...")
    if has_structured_data:
        parts.append("- query_structured_data: ...")
    if web_search_enabled:
        parts.append("- web_search: ...")

    parts.append("")
    parts.append("TOOL SELECTION RULES (follow strictly):")
    if has_documents:
        parts.append("- ALWAYS use analyze_document when the user asks to summarize ...")
        parts.append("- Use search_documents ONLY for finding specific facts ...")
        # Phase 4 / SEARCH-03: insert two new bullets here (folder_path/scope guidance + tool overview).
    ...
    return "\n".join(parts)
```

**SEARCH-03 inserted bullets** (RESEARCH.md §SEARCH-03):
```python
parts.append("- When the user's question is clearly scoped to a folder, pass `folder_path` to search_documents to narrow the search. ...")
if has_documents:
    parts.append("- For codebase-style precision: use `tree` to see the folder structure, `glob` to find files by name pattern, `grep` to search inside document text by regex, `list_files` to see one folder's contents, and `read_document` to read specific lines of a doc. ...")
    parts.append("- Tool results carry a 'scope' field on every row. When citing a result, mention whether it came from the user's private docs (scope='user') or the shared knowledge base (scope='global'). ...")
```

---

### `backend/scripts/test_all.py` (MODIFIED — test runner / registry)

**Analog:** itself. Phase 3 already set the precedent — line 17 imports `test_folders` and line 34 registers `("Folders", test_folders)` between Files and Backfill.

**Existing pattern** (`test_all.py:14-44`):
```python
import test_files
import test_folders         # NEW (Phase 3)
import test_backfill
import test_rag
...

SUITES = [
    ("Health", test_health),
    ...
    ("Files", test_files),
    ("Folders", test_folders),       # NEW (Phase 3 — folders is logically a Files extension)
    ("Backfill", test_backfill),
    ...
]
```

**Phase 4 diff** (RESEARCH.md §Registration in test_all.py):
```python
# After line 17 (import test_folders), add:
import test_exploration_tools  # NEW (Phase 4)

# Inside SUITES list, after the Folders tuple, add:
("Exploration", test_exploration_tools),       # NEW (Phase 4 — five exploration tools)
```

**Why between Folders and Backfill:** the suites are already topologically ordered (foundation → application). Exploration depends on the schema/folder primitives Folders validates and the content_markdown Backfill validates — placing it after Folders and before Backfill keeps the order coherent.

---

## Shared Patterns

These cross-cutting patterns apply to **multiple** Phase 4 files. Plans should reference them once at the top and apply consistently.

### Path Normalization Chokepoint (Pitfall 4)

**Source:** `backend/app/services/folder_service.py::normalize_path` (`folder_service.py:32-71`)
**Apply to:** Every tool function (tree.py, glob_match.py, grep.py, list_files.py, read_document.py) — **first statement** of the function body.

```python
def normalize_path(p: str | None) -> str:
    """Canonicalize a folder path string.

    Canonical form: leading slash always, no trailing slash (except root '/'),
    no double slashes, no backslashes, NFC-normalized Unicode, case preserved.
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
    if s != "/":
        for seg in s.lstrip("/").split("/"):
            if seg in _FORBIDDEN_SEGMENTS or seg == "":
                raise ValueError(...)
    if not _CANONICAL_PATH_RE.match(s):
        raise ValueError(...)
    return s
```

**Usage idiom** (every Phase 4 tool):
```python
norm = normalize_path(args.path)
# All subsequent queries use `norm`, never `args.path`.
```

### UUID Defense-in-Depth (HI-01)

**Source:** `backend/app/services/folder_service.py::_assert_uuid` (`folder_service.py:101-123`)
**Apply to:** Any tool function that interpolates `user_id` into a PostgREST `.or_()` filter (tree.py, glob_match.py, list_files.py, grep.py — anywhere scope='user' or 'both').

```python
def _assert_uuid(value: str | None, field_name: str = "user_id") -> None:
    """Defense-in-depth UUID validator.

    HI-01: list_folder() builds PostgREST `.or_()` filters via f-string
    interpolation of `user_id`. ... Validate at the service-layer entry
    point so the contract is enforced regardless of what the router passes.

    Raises:
        ValueError: if `value` is neither None nor a syntactically valid UUID.
    """
    if value is None:
        return
    try:
        _uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError(f"invalid {field_name}: not a UUID")
```

**Usage idiom:**
```python
if scope in ("user", "both"):
    _assert_uuid(user_id, field_name="user_id")
```

### LIKE-Wildcard Escape (HI-03)

**Source:** `backend/app/services/folder_service.py::_escape_like` (`folder_service.py:84-98`)
**Apply to:** Any service function building a `.like()` predicate where `path` or a path segment contains user-supplied content.

```python
def _escape_like(s: str) -> str:
    """Escape LIKE wildcard metacharacters in a literal string.

    HI-03: Migration 012's canonical-form regex `^/[^/]+(/[^/]+)*$` ALLOWS `%`
    and `_` in folder segments. When a folder name contains these characters
    and we build a LIKE predicate `f"{prefix}/%"`, the literal `_` becomes a
    single-char wildcard and the literal `%` becomes a multi-char wildcard,
    causing over-matching.
    """
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
```

### Two-Scope UNION via PostgREST `.or_()` (Phase 3 / Plan 02)

**Source:** `backend/app/services/folder_service.py:165-171, 191-198, 220-227` — three near-identical `or_()` invocations
**Apply to:** Every tool's query builder when scope == "both".

```python
# Predicate: (scope='user' AND user_id=<my>) OR (scope='global' AND user_id IS NULL)
docs_q = docs_q.or_(
    f"and(scope.eq.user,user_id.eq.{user_id}),and(scope.eq.global,user_id.is.null)"
)
```

**Critical:** `user_id` MUST be `_assert_uuid`-validated before this f-string interpolation.

### LangSmith @traceable Decorator (TOOL-10)

**Source:** four existing sites — `openai_client.py:251` (`@traceable(name="search_documents", run_type="tool")`), `openai_client.py:274` (`@traceable(name="gemini_chat", run_type="llm")`), `sub_agent.py:18` (`@traceable(name="sub_agent_analyze", run_type="chain")`), `sql_tool.py:56` (`@traceable(name="query_structured_data", run_type="tool")`)
**Apply to:** Every Phase 4 tool function (tree, glob_match, grep, list_files, read_document).

```python
from langsmith import traceable

@traceable(name="grep", run_type="tool")
def grep(args: GrepArgs, user_id: Optional[str], supabase_client) -> dict:
    ...
```

### Structured Error Envelope (Phase 3 / Plan 04)

**Source:** `backend/app/routers/folders.py` (folder DELETE 409 envelope) + `folder_service.py:404-406, 411-416` (structured `{deleted, error, document_count, subfolder_count}` dict)
**Apply to:** Every Phase 4 tool function when returning an error condition (NOT_FOUND, INVALID_REGEX, PATHOLOGICAL_REGEX, INVALID_ARGS, pending_reindex).

```python
return {"tool": "<tool_name>", "error": "<ERROR_CODE>", "message": "<human-readable>",
        "scope": ..., "folder_path": ...}   # additional context fields when relevant
```

Never return bare strings as errors. Never raise HTTPException from a tool function (tools are not router handlers; they return dicts that the dispatch arm JSON-stringifies).

### Test-Module Top-of-File Bootstrap

**Source:** `backend/scripts/test_folders.py:36-69`
**Apply to:** `test_exploration_tools.py` (verbatim, with name swaps).

Imports + `_service_role_client` + `_track_doc/_track_folder` + tracking lists + module-top fixture constants. Already enumerated in the PATTERNS.md per-file section above.

### Cleanup Discipline (CLAUDE.md verbatim)

**Source:** `backend/scripts/test_folders.py::_cleanup` (`test_folders.py:131-155`)
**Apply to:** Every test-module's `finally` block. **NEVER** bulk-delete; **NEVER** `DELETE FROM` on production tables.

Already enumerated above. The static grep gate Phase 3 added enforces this — any `DELETE` without a per-id `.eq()` will trip CI.

### Canary `_verify_phaseN_setup` Pattern

**Source:** `backend/scripts/test_folders.py::_verify_phase3_setup` (`test_folders.py:94-128`)
**Apply to:** `test_exploration_tools.py::_verify_phase4_setup`. Probes routes + RPCs + migrations; emits actionable `[FATAL]` messages naming the responsible plan. If the canary fails, the suite returns immediately with a single FAIL h.test and zero contamination from incomplete fixtures.

```python
def _verify_phase3_setup(sb_admin):
    """Pre-flight: assert Migration 019's RPCs exist AND folders router is registered."""
    try:
        r = sb_admin.rpc("rename_folder_prefix", {...}).execute()
        if r.data is None:
            return False, "rename_folder_prefix returned no data - function exists but is broken"
    except Exception as e:
        return False, (
            f"rename_folder_prefix RPC missing or errored: {type(e).__name__}: {e}. "
            f"Did you apply Migration 019 via run_migrations.py?"
        )
    ...
    return True, "ok"


# In run():
ok, msg = _verify_phase3_setup(sb_admin)
if not ok:
    h.test("Phase 3 setup (Migration 019 + folders router)", False, f"[FATAL] {msg}")
    return h.passed, h.failed
```

---

## No Analog Found

No Phase 4 file is fully analog-less, but two have only **partial** in-repo precedent. The planner should consult RESEARCH.md for the missing dimensions:

| File | Role | Data Flow | What's net-new (no in-repo precedent) |
|---|---|---|---|
| `backend/app/services/exploration_tools/__init__.py` | package barrel | re-export | Service-package barrel files don't exist elsewhere — `app/services/__init__.py` is empty. Planner picks the named-re-export shape (RESEARCH.md §Recommended Project Structure recommends Option A package layout). |
| `backend/app/services/exploration_tools/_truncate.py` | utility helper | transform | Char-truncation idiom is **inline** at `openai_client.py:567`; no extracted helper precedent. Planner extracts the new helper from RESEARCH.md §Pattern B (12K-Cap Truncation Helper). |

**Net-new components inside otherwise-precedented files:**
- `grep_documents` RPC body (LATERAL regexp_split_to_table + SET LOCAL statement_timeout) — RESEARCH.md §Tool Block C is the source of truth.
- Glob-→-regex translator (Python helper inside `glob_match.py`) — RESEARCH.md §Tool Block B.
- Tree iterative-BFS budget loop — RESEARCH.md §Open Questions #5.
- Pydantic v2 `Literal` + `Field(pattern=, ge=, le=)` is implicit (FastAPI ≥ 0.100 pulls v2; Phase 3 / Plan 01 LOCKED `extra='ignore'`); explicit `model_config = {"extra": "ignore"}` declarations are net-new style.

---

## Metadata

**Analog search scope:**
- `backend/app/services/` (folder_service, openai_client, sub_agent, sql_tool, web_search, ingestion, record_manager — all read for tracing/dispatch/RPC-wrapper patterns)
- `backend/app/models/schemas.py` (Pydantic v2 idioms)
- `backend/app/routers/folders.py` + `routers/files.py` (admin-gate + body-conditional patterns; NOT applicable to Phase 4 tools but referenced for completeness)
- `backend/migrations/` (007, 011, 012, 014, 015, 016, 019 — RPC shapes + RLS conventions)
- `backend/scripts/test_folders.py` + `test_helpers.py` + `test_all.py` (test infrastructure)
- `.planning/phases/04-five-exploration-tools-search-documents-extension/04-RESEARCH.md` (full ~1,740-line technical research)
- `.planning/REQUIREMENTS.md` (TOOL-01..10, SEARCH-01..03, TEST-02 verbatim)
- `.planning/ROADMAP.md` (Phase 4 success criteria + threats)
- `.planning/codebase/STRUCTURE.md` + `CONVENTIONS.md` (project conventions)

**Files scanned:** 18 (12 source/test files + 6 planning docs)

**Pattern extraction date:** 2026-05-08

**Confidence breakdown:**
- File classification: HIGH — every file's role + data flow is explicitly enumerated in RESEARCH.md §Wave 0 Gaps + §Per-Tool Research Blocks
- Analog selection: HIGH — Phase 3 just shipped 5 weeks ago (`folder_service.py`, `test_folders.py`); patterns are fresh, exhaustive, and codified
- Shared-pattern extraction: HIGH — `normalize_path`, `_assert_uuid`, `_escape_like`, `or_()` UNION, `@traceable`, structured-error envelope, `_tracked_*` cleanup, canary pattern are all single-source-of-truth lines/functions
- Net-new components: MEDIUM — RESEARCH.md provides skeletons; planner must compose (grep RPC body, glob→regex, tree BFS) but every primitive is well-documented
