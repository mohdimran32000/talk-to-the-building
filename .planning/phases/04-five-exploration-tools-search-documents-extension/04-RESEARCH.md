# Phase 4: Five Exploration Tools + search_documents Extension — Research

**Researched:** 2026-05-08
**Domain:** Claude-Code-style precision tools layered onto an existing FastAPI + Supabase + Gemini RAG agent — backed by Phase 1 schema (`folder_path`, `scope`, `content_markdown`), Phase 2 backfill, and Phase 3 folder service primitives
**Confidence:** HIGH (every claim grounded in shipped code or shipped migration; assumptions explicitly tagged)

---

## Project Constraints (from CLAUDE.md)

These directives apply with the same authority as locked decisions and override any default suggestion below:

- **Python venv** mandatory for backend (`cd backend && venv/Scripts/python ...`)
- **No LangChain / LangGraph** — raw `google-genai` SDK only
- **Pydantic** for structured outputs (TOOL-06 inherits this rule)
- **Row-Level Security** on every table (every Phase 4 query must compose with RLS, not bypass it)
- **SSE for chat streaming** (Phase 4 tools emit `tool_start`/`tool_done` SSE events through the existing `messages.py:event_generator` pipeline)
- **Polling, not Realtime** for ingestion status — irrelevant to Phase 4 tool dispatch but binds Phase 6
- **Stateless completions** — chat history is loaded from `messages` table per request; tool args carry no implicit session state
- **Tests must NEVER delete all user data** — `_tracked_documents` + per-id `.delete().eq()` in `finally`; no blanket DELETE / TRUNCATE; no whole-table wipes (verified by static grep gates as of Phase 3)
- **Plans saved to `.agent/plans/`** with `{sequence}.{name}.md` naming (per CLAUDE.md) **OR** `.planning/phases/04-.../04-NN-PLAN.md` (per project convention adopted in Phases 1–3) — Phase 3 used the latter; Phase 4 should match Phase 3 to preserve continuity. Confirm with operator if conflict surfaces.

---

## Phase Requirements

| ID | Description (verbatim from REQUIREMENTS.md L42-57+85) | Research Support |
|----|--------|------------------|
| TOOL-01 | `tree` tool — args `path`, `max_depth` (server-capped 4–6), `scope`; returns nested structure with `[N more folders, M more docs]` count summaries; max 500 entries with truncation marker | §Tool Block A; §Tree Truncation Algorithm |
| TOOL-02 | `glob` tool — args `pattern` (`**`/`*` semantics), `path`, `type` (file/folder/both), `scope`; matches against `folder_path` + `file_name` | §Tool Block B; §Glob semantics |
| TOOL-03 | `grep` tool — args `pattern`, `path`, `case_insensitive`, `multiline`, `output_mode`, `-A`/`-B`/`-C`, `scope`; max 50 hits with ±2 line context; statement timeout `5s`; rejects pathological regexes | §Tool Block C; §grep query design |
| TOOL-04 | `list_files` tool — args `path`, `scope`; single-level listing, folders-then-files-alpha order | §Tool Block D |
| TOOL-05 | `read_document` tool — args `document_id` OR `path`, `offset` (1-based), `limit` (default 2000, hard cap 5000); returns arrow-form `{n}→{content}`; CRLF normalized; UTF-8 codepoint-safe; line-by-line slicing | §Tool Block E; §read_document semantics |
| TOOL-06 | All tools use Pydantic v2 BaseModel for arg validation (`Literal["user","global","both"]` for scope, `Field(..., ge=, le=)` for numeric bounds, regex pattern for path) | §Shared Pydantic Schema Module |
| TOOL-07 | Every tool result row carries `scope: 'user' \| 'global'` (no exceptions) | §Scope-tag invariant |
| TOOL-08 | Hard 12K-char cap per tool result with explicit `[...truncated, N more]` marker | §12K cap + truncation marker pattern |
| TOOL-09 | Every new tool routed through Episode 1's layered-fallback empty-response wrapper in `openai_client.py` | §TOOL-09 Layered-Fallback Wrapper |
| TOOL-10 | LangSmith `@traceable(run_type="tool")` on each tool function | §Tracing pattern (already established in 4 sites) |
| SEARCH-01 | `search_documents` schema extended with optional `folder_path` + `scope`; defaults preserve existing behavior | §SEARCH-01..03 |
| SEARCH-02 | `match_document_chunks_with_filters` + `match_document_chunks_hybrid` RPCs gain `match_folder_path` + `match_scope` parameters (NULL defaults) | §SEARCH-02 RPC Migration shape |
| SEARCH-03 | System prompt updated to describe when LLM should self-scope via folder_path/scope | §SEARCH-03 system-prompt insertion |
| TEST-02 | `test_exploration_tools.py` — 200-folder fixture, 5000-doc grep fixture (Bitmap Index Scan EXPLAIN), CRLF/Unicode/single-long-line/mixed-ending fixtures, adversarial-payload empty-response guard | §TEST-02 Fixture Engineering |

---

## Summary

Phase 4 is a **pure-additive code expansion** layered on the bedrock that Phases 1–3 already cemented: the schema, indexes, RLS, two-scope model, `normalize_path()` chokepoint, structured-error envelopes, scope-tagging discipline, and the `_tracked_*` cleanup ritual. Five new precision tools (`list_files` → `tree` → `glob` → `read_document` → `grep`) get registered alongside the existing four (`search_documents`, `analyze_document`, `query_structured_data`, `web_search`) in `openai_client.py:_build_*_tool()` factories and dispatched as additive `elif` arms in `stream_response()` (file `backend/app/services/openai_client.py`, lines 274–610). A shared Pydantic v2 argument-schema module (recommended path: `backend/app/services/exploration_tools/schemas.py`) lands first; each tool function lives in a sibling module and is decorated with `@traceable(run_type="tool")`. Every tool result row carries `scope` and is truncated at 12K chars with `[...truncated, N more]`. `search_documents` gains optional `folder_path` + `scope` arguments with NULL defaults at both the tool-schema layer (`_build_search_tool`) and the RPC layer (`match_document_chunks_with_filters` + `match_document_chunks_hybrid` via Migration 020).

The hardest pieces are: (a) **grep's perf-correctness contract** (pg_trgm GIN index from Phase 1 + ILIKE pre-filter + `LATERAL regexp_split_to_table` for line-resolved hits + `SET LOCAL statement_timeout = '5s'` per-RPC; **must** be a Postgres function because supabase-py runs each `.execute()` in its own implicit transaction and there is no Python-side hook to set the timeout), and (b) **read_document's line-stability contract** (CRLF normalization happened upstream at ingestion in Phase 2; on the read side, `splitlines(keepends=False)` consistently, 1-based external offsets, UTF-8 codepoint-safe truncation of the last visible line). The "**layered-fallback empty-response wrapper**" referenced by the phase brief is **not a discrete callable** — it is the inline pattern at `openai_client.py:565-610` (truncate at 16K → streaming → non-streaming retry → raw yield), and TOOL-09 compliance means **every new tool's result text must flow through that exact pattern by being routed via the unified `tool_name` dispatch loop in `stream_response()`** (additive `elif` arms — never invent a parallel context-injection path).

**Primary recommendation:** Stage the work in five waves matching the locked build order (`list_files` → `tree` → `glob` → `read_document` → `grep`), each landing one tool function + its Pydantic args model + its dispatch arm in `openai_client.py` + its test section in `test_exploration_tools.py`. Migration 020 (RPC extensions for SEARCH-02 + a new `grep_documents` RPC for TOOL-03) ships in Wave 0 alongside the shared schemas module. Reuse Phase 3's `_tracked_documents` / `_tracked_folders` / `_tracked_storage_paths` cleanup discipline verbatim; reuse the `_verify_phaseN_setup` canary pattern; reuse the `_service_role_client()` helper.

---

## Cross-Phase Dependencies & Locked Inheritances

| Inherited from | What's locked | How Phase 4 consumes it |
|----------------|---------------|------------------------|
| **Phase 1 / Migration 012** | `documents.folder_path TEXT NOT NULL DEFAULT '/'` + canonical CHECK regex; `documents.scope TEXT` with coupling CHECK; `document_chunks.scope` denormalized; nullable `user_id` for global rows | Every tool query filters/groups on these columns; `_build_*_tool()` validates `path` against the same regex via Pydantic |
| **Phase 1 / Migration 014** | `documents.content_markdown TEXT` + `content_markdown_status` (`pending`/`ready`/`failed`/`requires_user_reupload`) | `grep` and `read_document` SELECT this column; non-`ready` rows surface as `{status: 'pending_reindex', content_markdown_status: <X>}` (LOCKED contract from `02-CONTEXT.md` lines 60–73) |
| **Phase 1 / Migration 015** | Two-scope RLS policies on `documents`, `document_chunks`, `folders` (separate INSERT/UPDATE/DELETE per scope; `forbid_scope_mutation` BEFORE-UPDATE trigger; `is_admin()` SQL helper); `(SELECT auth.uid())` perf-cached subquery convention | RLS does scope filtering for free on every Phase 4 SELECT — tools do NOT add `WHERE scope IN (...)` themselves; the `scope` arg is *narrowing* on top of RLS, not a substitute (per `research/ARCHITECTURE.md:362-363`) |
| **Phase 1 / Migration 016** | `documents_content_markdown_trgm_idx` (GIN gin_trgm_ops); `documents_folder_path_trgm_idx`; `documents_folder_path_prefix_idx` (btree text_pattern_ops); folders parallels (`migrations/016_search_indexes.sql:35-61`) | grep depends on `documents_content_markdown_trgm_idx` (TEST-02 EXPLAIN assertion); tree/glob/list_files prefix queries depend on `documents_folder_path_prefix_idx`; substring-glob falls back to `documents_folder_path_trgm_idx` |
| **Phase 1 / Plan 01** | `folder_service.normalize_path()` (`backend/app/services/folder_service.py:32-71`) — leading slash always, no trailing, `..`/`.` rejected, NFC unicode, case preserved | Every tool that takes a `path` arg runs it through `normalize_path()` as the FIRST line of the function body (Pitfall 4 chokepoint; same convention Phase 3 used at every router/service entry) |
| **Phase 2 / Plan 02** | `documents.content_markdown` populated synchronously on upload via single atomic UPDATE (`status='ready'` and `content_markdown=<markdown>` and `content_markdown_status='ready'` written together) | `grep`/`read_document` can rely on `content_markdown_status='ready'` rows having non-NULL `content_markdown`; non-`ready` rows MUST surface as `pending_reindex` per LOCKED contract |
| **Phase 2 / Plan 02** | `docling==2.91.0` pinned in `requirements.txt` for byte-equivalence | `read_document` line counts on backfilled docs are byte-stable (TEST-02 Unicode/CRLF/single-long-line fixtures rely on this) |
| **Phase 2 / Plan 04** | `_verify_storage_setup` canary pattern; `_tracked_documents`/`_tracked_storage_paths` cleanup; `_service_role_client()` helper inline at top of test module | `test_exploration_tools.py` mirrors this verbatim — canary asserts `documents_content_markdown_trgm_idx` exists + at least one ready row exists, then proceeds |
| **Phase 3 / Plan 02** | `folder_service.list_folder()` UNION-pattern (explicit folders + inferred from documents); `_assert_uuid()` defense-in-depth on PostgREST `or_()` interpolation; `_escape_like()` for `%`/`_` in folder names | `tree` and `list_files` reuse this UNION shape verbatim; **CRITICAL: any tool that builds an `or_()` filter with user_id MUST run `_assert_uuid()` first** (HI-01 contract from Phase 3) |
| **Phase 3 / Plan 02** | `RPC-wrapper service pattern` — service functions normalize input, call RPC, hide PostgREST plumbing | `grep_documents` RPC (Migration 020 NEW) is wrapped by an `exploration_tools.grep` service function the same way `rename_folder` wraps `rename_folder_prefix` |
| **Phase 3 / Plan 03** | `record_manager.determine_action(scope, folder_path, ...)` — service layer accepts canonical form as-is; no normalize_path inside (caller-owned chokepoint) | Phase 4 honors the same caller-owned chokepoint principle: service layer does NOT re-normalize what the schema/router already normalized |
| **Phase 3 / Plan 04** | `JSONResponse(status_code=409, content={error: 'FOLDER_NOT_EMPTY', ...})` — structured envelope for multi-field domain errors | Phase 4 tool-result error shape mirrors this: `{error, message, scope?, folder_path?, ...}` keys; never bare strings |
| **Phase 3 / Plan 04** | Body-conditional inline admin gate (`_require_admin(user_id, action)`) | NOT applicable to Phase 4 tools — they are READ-ONLY; no admin gate needed |
| **Phase 3 / Plan 06** | `_verify_phaseN_setup` canary; `_track_doc`/`_track_folder`/`_tracked_storage_paths`; `_raises()`; `_service_role_client()` mirror of `auth.py:8-12`; `concurrent.futures.ThreadPoolExecutor` for parallel-uploads; `RuntimeResponse` stub pattern | `test_exploration_tools.py` reuses these patterns verbatim — same module-top fixtures, same finally-block cleanup, same canary discipline |

**Key cross-phase invariant — the 4-element status vocabulary:** `'pending'` | `'ready'` | `'failed'` | `'requires_user_reupload'` (Migration 014 / canonical). Any state-machine code in Phase 4 (e.g., `read_document` deciding whether to return text or `pending_reindex`) MUST emit one of these four strings, not a synonym.

---

## Architectural Responsibility Map

Phase 4 is single-tier (FastAPI/backend). Documenting tier ownership for the planner's sanity check:

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Tool argument validation (TOOL-06) | **Backend / Pydantic schemas** | — | Pydantic v2 BaseModel is the codebase convention (`models/schemas.py`); validation cannot live in browser (LLM is the caller) or DB (no Pydantic in PL/pgSQL) |
| Path normalization | **Backend / `folder_service.normalize_path`** | DB CHECK (defense-in-depth) | Phase 1 / Plan 01 locked the chokepoint; routers and services call it, DB rejects non-canonical input as a belt |
| Tool dispatch (`elif` arms) | **Backend / `openai_client.stream_response`** | — | Existing dispatch lives at `openai_client.py:443-610`; additive elif arms keep regression surface localized (research/ARCHITECTURE Pattern 4) |
| RLS scope filtering | **DB / RLS policies** | App-layer `.eq()` (defense-in-depth per `CONCERNS.md`) | Phase 1 / Migration 015 owns this; tool args' optional `scope` is *narrowing on top* of RLS, not the primary gate |
| grep regex line resolution | **DB / PL/pgSQL `grep_documents` RPC** | — | `LATERAL regexp_split_to_table` + `SET LOCAL statement_timeout = '5s'` MUST be DB-side; supabase-py has no per-query timeout hook (per `Grep` of `backend/`: zero existing `statement_timeout` usage) |
| read_document line slicing | **Backend / Python `splitlines()`** | — | Python's stdlib already handles CRLF/LF/CR uniformly; UTF-8 codepoint-safe slicing is straightforward in Python 3 |
| 12K-char truncation marker | **Backend / each tool function tail** | — | Per-tool concern; happens in Python after DB rows return |
| Layered-fallback empty-response (TOOL-09) | **Backend / `stream_response` Call#2 path** | — | Inline at `openai_client.py:565-610`; tools must route results through the unified dispatch loop, not invent parallel context-injection |
| LangSmith tracing (TOOL-10) | **Backend / `@traceable` decorator on every tool fn** | — | Existing convention at 4 sites already (`openai_client.py:251,274`, `sub_agent.py:18`, `sql_tool.py:56`, `web_search.py:14`) |

---

## Standard Stack

### Core (already in place — no new deps required)

| Library | Version (verified) | Purpose | Why Standard |
|---------|-------|---------|--------------|
| `pydantic` | unpinned in `requirements.txt:4`; FastAPI ≥ 0.100 ships v2 — assume v2 | Argument-schema models for TOOL-06 | Already the project convention (`models/schemas.py` uses `BaseModel`); v2 silently-drops-unknown is a Phase 3-locked defense layer |
| `google-genai` | unpinned (Episode 1 lock) | Tool-calling SDK | Project rule: no LangChain/LangGraph; raw SDK only |
| `langsmith` | unpinned | `@traceable(run_type="tool")` | 4 existing sites; TOOL-10 |
| `supabase-py` | unpinned (via `supabase` package) | DB access | Standard for every other service file; RLS auto-applied via JWT `postgrest.auth()` |

### Net-new convention (no new packages)

- **Pydantic v2 `Literal` + `Field`** — already used in `models/schemas.py` style; Phase 4 introduces `Literal["user","global","both"]` for scope and `Field(..., ge=N, le=M)` for numeric bounds. No new dep.
- **`re.compile()` once at module load + `re.search(line)` per row** — for grep's pathological-regex rejection pre-check (Python `re` rejects nothing by default; the *test* here is "does compilation succeed and does a `re.fullmatch('a' * 100, pattern)` complete in < 100ms?" — see Pitfall 3 mitigation).

### Alternatives Considered (and rejected)

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Per-tool dispatch `elif` arms | Registry-of-callables refactor | `research/ARCHITECTURE.md:301-310` Pattern 4 explicitly rejects this for Phase 4 — only 9 entries; refactor blocks every other change. Defer to a future cleanup phase. |
| One mega-tool `explore` taking a `mode` arg | Five distinct tools | Spec calls for five explicit tools (claude-code parity); LLM tool selection is more reliable with named functions; LangSmith traces are cleaner |
| `string_agg(content, '\n\n' ORDER BY chunk_index)` to reconstruct content for grep | `documents.content_markdown` | **FORBIDDEN** by Pitfall 6 (RANK 2) and the static grep gate already locked in Phase 2 — chunk overlap (50 words) corrupts line numbers and inflates byte counts |
| Python-side glob `**`/`*` regex translation | DB-side `LIKE` rewriting | Glob → SQL `LIKE` rewrite preserves index usage (`documents_folder_path_prefix_idx` for prefix patterns). See §Glob semantics |
| LangChain RetrievalQA chain wrapping search_documents | Direct supabase-py RPC | Project rule (CLAUDE.md): no LangChain |

**Installation:** No new `pip install` required. Migration 020 (next sequential after 019) is the only schema-side artifact.

**Version verification (recommended for the planner):** Run `cd backend && venv/Scripts/python -c "import pydantic; print(pydantic.VERSION)"` to confirm v2 is the resolved version before committing TOOL-06 schemas. If v1, the `Field(..., ge=, le=)` + `Literal` syntax still works but `extra='ignore'` default is different — flag for re-evaluation. [VERIFIED: `requirements.txt:4` shows `pydantic` unpinned; FastAPI is also unpinned; the codebase commit history shows Phase 3 already uses Pydantic v2 features (`extra='ignore'` was the locked Phase 3 / Plan 01 default — see STATE.md decision "Pydantic v2 silently-drop-unknown is the FIRST defense layer")].

---

## Architecture Patterns

### System Architecture (Phase 4 additive overlay)

```
                                   POST /api/threads/{tid}/messages
                                              │
                                              ▼
                          ┌──────────────────────────────────────────────────┐
                          │   messages.py event_generator (UNCHANGED)        │
                          │   yields SSE events → frontend                   │
                          └──────────────────────────────────────────────────┘
                                              │
                                              ▼
                          ┌──────────────────────────────────────────────────┐
                          │ openai_client.stream_response()  (EXTENDED)      │
                          │   _build_*_tool() factories:                     │
                          │     existing: search_documents, analyze_document,│
                          │               query_structured_data, web_search │
                          │     NEW (Phase 4):                               │
                          │       _build_tree_tool()                         │
                          │       _build_glob_tool()                         │
                          │       _build_grep_tool()                         │
                          │       _build_list_files_tool()                   │
                          │       _build_read_document_tool()                │
                          │   plus: search_documents schema EXTENDED         │
                          │         w/ folder_path + scope (SEARCH-01)       │
                          │                                                  │
                          │   dispatch elif chain (after analyze_document):  │
                          │     elif tool_name == "tree":          → tool_fn │
                          │     elif tool_name == "glob":          → tool_fn │
                          │     elif tool_name == "grep":          → tool_fn │
                          │     elif tool_name == "list_files":    → tool_fn │
                          │     elif tool_name == "read_document": → tool_fn │
                          │                                                  │
                          │   ALL results flow through:                      │
                          │     truncated_result = result_text[:16000]       │
                          │     Call#2 streaming with system_with_context    │
                          │     non-streaming fallback if empty              │
                          │     raw yield as last resort                     │
                          │     [openai_client.py:565-610]                   │
                          └──────────────────────────────────────────────────┘
                                              │
                                              ▼
                          ┌──────────────────────────────────────────────────┐
                          │ exploration_tools/  (NEW PACKAGE)                │
                          │   __init__.py           — public re-exports      │
                          │   schemas.py            — TOOL-06 Pydantic v2    │
                          │   tree.py               — TOOL-01 + @traceable   │
                          │   glob.py               — TOOL-02 + @traceable   │
                          │   grep.py               — TOOL-03 + @traceable   │
                          │   list_files.py         — TOOL-04 + @traceable   │
                          │   read_document.py      — TOOL-05 + @traceable   │
                          │   _truncate.py          — TOOL-08 12K cap helper │
                          │   _scope_tag.py         — TOOL-07 invariant      │
                          │                                                  │
                          │   ALL functions take (args_model, user_id,       │
                          │                       supabase_client) and       │
                          │   return a dict containing scope-tagged rows     │
                          │   + a truncation marker if the 12K cap fired     │
                          └──────────────────────────────────────────────────┘
                                              │
                                              ▼
                          ┌──────────────────────────────────────────────────┐
                          │ Supabase / Postgres                              │
                          │   documents      (RLS enforces scope; index:     │
                          │                   documents_content_markdown_    │
                          │                   trgm_idx for grep)             │
                          │   document_chunks (RLS for hybrid search; un-    │
                          │                    touched by tree/glob/grep/    │
                          │                    list_files/read_document)     │
                          │   folders        (sparse explicit-empty side    │
                          │                   table; tree + list_files       │
                          │                   UNION inferred-from-documents) │
                          │                                                  │
                          │   RPCs (Migration 020 NEW):                      │
                          │     grep_documents(p_pattern, p_path, p_scope,   │
                          │                    p_user_id, p_case_insens,     │
                          │                    p_max_hits, p_context_lines,  │
                          │                    p_timeout_ms)                 │
                          │     match_document_chunks_with_filters           │
                          │       — gains match_folder_path TEXT DEFAULT NULL│
                          │       — gains match_scope TEXT DEFAULT NULL      │
                          │     match_document_chunks_hybrid                 │
                          │       — gains same two parameters                │
                          └──────────────────────────────────────────────────┘
```

### Recommended Project Structure

The planner picks the layout. Two viable shapes:

**Option A (recommended) — package directory.** Easier to test in isolation; mirrors how `models/`, `routers/`, and `services/` are already organized:

```
backend/app/services/
├── exploration_tools/        [NEW package]
│   ├── __init__.py           # re-exports tree, glob, grep, list_files, read_document, schemas
│   ├── schemas.py            # TOOL-06: TreeArgs, GlobArgs, GrepArgs, ListFilesArgs, ReadDocumentArgs
│   ├── _truncate.py          # TOOL-08 12K-cap helper + truncation marker
│   ├── _scope_tag.py         # TOOL-07 helper: ensure every dict row has 'scope'
│   ├── tree.py               # @traceable tree() function
│   ├── glob.py               # @traceable glob_match() function
│   ├── grep.py               # @traceable grep() function — wraps grep_documents RPC
│   ├── list_files.py         # @traceable list_files() function (delegates to folder_service.list_folder + sort)
│   └── read_document.py      # @traceable read_document() function — Python-side line slicer
├── folder_service.py         # UNCHANGED — Phase 4 imports normalize_path + list_folder
├── openai_client.py          # EXTENDED — _build_*_tool() + dispatch arms
└── ...
```

**Option B — single file.** Lower discovery cost for reviewers but worse test isolation:

```
backend/app/services/
├── exploration_tools.py      [NEW single file ~600-800 LOC]
└── ...
```

**Recommendation:** Option A. Phase 3 / Plan 02 already moved toward "one concern per file" (folder_service stayed as one file because the 5 functions share the `list_folder` UNION shape; for Phase 4, each tool has independent semantics and a distinct test surface). Verify with operator if file-count proliferation is a concern.

### Pattern A: Tool Function Skeleton (Reusable Template for All 5)

```python
# Source: synthesizes openai_client.py:251-271 (search_documents @traceable pattern)
# + folder_service.py:126-255 (scope-aware queries) + _scope_tag invariant
from typing import Optional
from langsmith import traceable
from app.services.folder_service import normalize_path
from app.services.exploration_tools.schemas import TreeArgs
from app.services.exploration_tools._truncate import apply_12k_cap

@traceable(name="tree", run_type="tool")
def tree(
    args: TreeArgs,
    user_id: Optional[str],
    supabase_client,
) -> dict:
    """TOOL-01. Returns nested folder structure within token-budget caps.

    Returns:
        {
          "tool": "tree",
          "scope_arg": args.scope,
          "path": <normalized>,
          "max_depth": args.max_depth,
          "entries": [...],          # each entry has 'scope' field (TOOL-07)
          "truncation_marker": "[...truncated, N more]" | None,  # TOOL-08
        }
    """
    norm = normalize_path(args.path)              # Pitfall 4 chokepoint
    # ... query logic that filters by scope, max_depth, 500-entry cap ...
    # Every entry dict carries 'scope': 'user' | 'global' (TOOL-07)
    result_dict = {...}
    return apply_12k_cap(result_dict, json_serialize=True)   # TOOL-08
```

### Pattern B: 12K-Cap Truncation Helper (TOOL-08)

```python
# Source: NEW for Phase 4. Stateless. Applied at the *end* of every tool function.
def apply_12k_cap(payload: dict, *, char_cap: int = 12_000,
                  json_serialize: bool = True) -> dict:
    """Truncate a tool's serialized result and append the marker.

    Strategy:
      1. JSON-serialize the payload's main 'entries' (or 'hits' or 'lines') list.
      2. If serialized < char_cap: return payload unchanged.
      3. Otherwise: drop entries from the END until under cap, count drops,
         set payload['truncation_marker'] = '[...truncated, N more entries]'.
      4. Re-emit. The marker is a string field, NOT a list element — the LLM sees
         it next to the trimmed list rather than embedded in it.
    """
    # implementation detail elided
```

[ASSUMED: 12K is a char count, not a token count — the spec's "12K-char hard cap" wording is consistent with `openai_client.py:567`'s `result_text[:16000]` char-based truncation. The planner SHOULD confirm with operator if "12K" is meant to be tokens (would require `tiktoken` or Gemini count_tokens RPC). My read: chars.]

### Pattern C: Scope-Tag Invariant Helper (TOOL-07)

```python
# Source: NEW for Phase 4. Defense-in-depth: even if a SELECT forgets to project scope,
# this helper catches the omission at result-assembly time.
def ensure_scope_tag(row: dict, default: str = "user") -> dict:
    """Assert every row dict carries a scope key. Add default if missing (with logger.warning)."""
    if "scope" not in row:
        logger.warning(f"Tool result row missing scope tag: {row.get('id', '?')} — inferring '{default}'")
        row["scope"] = default
    assert row["scope"] in ("user", "global"), f"Invalid scope: {row['scope']!r}"
    return row
```

### Anti-Patterns to Avoid

- **Inventing a parallel context-injection path** — never call `client.models.generate_content_stream()` from a tool function. Tools return `dict` or `str`; `stream_response()` owns the LLM call. (Pitfall 8 mitigation.)
- **Filtering by scope at the app layer instead of letting RLS do it** — apps that say `WHERE scope='user' AND user_id=?` duplicate the RLS predicate; if the tool is later called with service-role-key (sub-agent path), the explicit filter is a defense-in-depth belt, but it must AND with RLS, not replace it. (Per `research/ARCHITECTURE.md:362-363`.)
- **Building grep in Python over a SELECT of all rows** — the corpus may exceed memory; the index must be used. Push regex into Postgres with the GIN-trigram pre-filter. (Pitfall 3.)
- **Returning bare strings instead of structured dicts** — every tool result is a `dict` so `result_text` post-processing in `stream_response` can JSON-stringify it deterministically. The 12K cap is then a `len(json.dumps(payload))` check.
- **Using `splitlines(keepends=True)` for read_document** — keeps `\r\n` characters in the slice, breaking byte-stable assertions. Use `splitlines(keepends=False)` (`Pitfall 9` HOW TO AVOID #2).
- **Slicing content by `[:N]` chars** — corrupts UTF-8 codepoints. Use line-by-line slicing with codepoint-safe truncation of the LAST line only. (Pitfall 9 HOW TO AVOID #5.)
- **Adding `WHERE user_id IS NOT NULL` thinking it filters out global-scope rows** — global rows have `user_id IS NULL` legitimately. RLS already gates this; trust RLS for visibility, use `scope` for narrowing.
- **`Depends(get_admin_user)` on Phase 4 dispatch** — Phase 4 tools are READ-ONLY; no admin gate. The Phase 3 inline-admin pattern is irrelevant here.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Path canonicalization | Custom string-trimming | `folder_service.normalize_path()` (already exists at `folder_service.py:32-71`) | Phase 1 / Plan 01 locked the chokepoint; the DB CHECK regex matches the Python `_CANONICAL_PATH_RE`; bypassing is a Pitfall 4 violation |
| Folder listing UNION (explicit + inferred) | Re-roll the UNION | `folder_service.list_folder()` (already exists at `folder_service.py:126-255`) | Phase 3 / Plan 02 locked this shape; tree/list_files reuse it |
| `or_()` filter for scope='both' UNION | Manual two-query stitch | `f"and(scope.eq.user,user_id.eq.{user_id}),and(scope.eq.global,user_id.is.null)"` (Phase 3 idiom) | First-used in `folder_service.py:170,196,225`; the only way to express two-AND-clauses in one PostgREST round-trip |
| LIKE-prefix wildcard escaping | Trust user input | `folder_service._escape_like()` (`folder_service.py:84-98`) | Folder names can contain `%`/`_` (HI-03 from Phase 3); without escaping, `/foo_bar/%` over-matches `/fooXbar/` |
| UUID validation before f-string interpolation into PostgREST DSL | Trust JWT-derived user_id | `folder_service._assert_uuid()` (`folder_service.py:101-123`) | HI-01 from Phase 3 — the `or_()` filter interpolates user_id as a literal; defense-in-depth against future leaks |
| Postgres regex line-resolved hits | Python loop over full content_markdown | `LATERAL regexp_split_to_table(content_markdown, E'\n') WITH ORDINALITY` (DB-side) | Pulling a 5000-doc corpus to Python is OOM territory; the GIN index pre-filter narrows the row set first, then `LATERAL` extracts line numbers |
| Per-query `statement_timeout` on grep | Python timeout wrapper | `SET LOCAL statement_timeout = '5s'` inside the PL/pgSQL function body (DB-side) | supabase-py has no per-query GUC hook; a Python `concurrent.futures` timeout cancels the awaitable but leaves Postgres still grinding (connection pool starvation; Pitfall 3) |
| LangSmith tool tracing | Manual span emission | `@traceable(run_type="tool")` decorator (4 existing sites) | Free with the `langsmith` SDK; nests automatically under the parent `gemini_chat` span |
| Tool-result truncation | Hand-roll per tool | Single `apply_12k_cap()` helper + `[...truncated, N more]` marker | TOOL-08 says one cap, one marker, every tool — DRY |
| CRLF normalization | Hand-roll byte-level | `splitlines(keepends=False)` (stdlib) | Stdlib already handles `\r\n`/`\n`/`\r`; documented behavior; no edge cases for the codebase to own |
| UTF-8 codepoint-safe truncation | Slice by bytes | `bytes_or_str.encode('utf-8')[:N].decode('utf-8', errors='ignore')` (Pitfall 9 idiom) | Standard Python idiom; `errors='ignore'` discards a partial trailing codepoint cleanly |

**Key insight:** Phase 4's job is **plumbing five well-defined tools through battle-tested Phase 1–3 primitives**. Every tool's "hard part" already has an answer in the existing codebase or in stdlib. The ONLY net-new building blocks are: (1) the per-tool Pydantic args models, (2) the 12K truncation helper, (3) the grep DB function, (4) the read_document line slicer. Everything else is composition.

---

## Tool Block A — TOOL-01 `tree`

**Args (TreeArgs):**

```python
# In schemas.py
from typing import Literal
from pydantic import BaseModel, Field

class TreeArgs(BaseModel):
    path: str = Field("/", pattern=r"^/$|^/[^/]+(/[^/]+)*$",
                      description="Canonical folder path; '/' for root")
    max_depth: int = Field(2, ge=1, le=4,
                           description="Server-capped at 4. Default 2 keeps results small.")
    scope: Literal["user", "global", "both"] = Field("both",
                           description="Filter scope; 'both' returns union (default).")

    model_config = {"extra": "ignore"}   # Phase 3 / Plan 01 LOCKED defense layer
```

**Server-side cap:** even if the LLM passes `max_depth=99`, Pydantic clamps to 4 via `le=4`. Default is 2 (per Pitfall 2 HOW TO AVOID #2: "Default to 2"). [VERIFIED: REQUIREMENTS.md TOOL-01 says "server-capped 4–6"; Pitfall 2 mitigation says cap at 4. Resolution: cap at 4 — the lower of the two. Confirm with operator if 5 or 6 is preferred — `le=` value is a 1-character change.]

**Algorithm (per-level rolling counters):**

1. Run `folder_service.list_folder(norm, scope, user_id, sb)` for the root path to get level-0 documents + level-0 subfolders.
2. For each level-0 subfolder, recurse to level 1, level 2, … up to `max_depth`. Track `depth` and `entry_count` cumulatively.
3. While `entry_count < 500` AND `current_depth <= max_depth`: keep expanding.
4. When the 500-entry cap is hit OR `current_depth > max_depth`: replace the deeper subtree with a count-summary node `{"path": "/foo", "scope": "user", "more_folders": N, "more_docs": M}` (per ROADMAP success criterion 1: `[N more folders, M more docs]` summaries).
5. After traversal, run `apply_12k_cap()` on the assembled JSON. If it fires, append `truncation_marker = '[...truncated, N more entries]'`.

**Output shape (sketch):**

```json
{
  "tool": "tree",
  "scope_arg": "both",
  "path": "/",
  "max_depth": 2,
  "entries": [
    {"type": "folder", "path": "/projects", "scope": "user", "children": [
      {"type": "folder", "path": "/projects/2026", "scope": "user",
       "more_folders": 3, "more_docs": 12},
      {"type": "doc", "path": "/projects/readme.md", "scope": "user",
       "document_id": "<uuid>", "file_name": "readme.md"}
    ]},
    {"type": "folder", "path": "/shared", "scope": "global", "children": [...]}
  ],
  "total_folders": 18, "total_docs": 47,
  "truncation_marker": null
}
```

**Scope-tagging:** `entries[*].scope` and every nested `children[*].scope` is mandatory (TOOL-07). Use `_scope_tag.ensure_scope_tag()` defensively.

**12K cap:** post-traversal serialization check (TOOL-08).

**Layered-fallback routing:** the `dict` is JSON-serialized to `result_text` in the `elif tool_name == "tree":` arm, then flows through the existing Call#2 path at `openai_client.py:567` (TOOL-09).

**@traceable:** `@traceable(name="tree", run_type="tool")` on the public `tree()` function (TOOL-10).

---

## Tool Block B — TOOL-02 `glob`

**Args (GlobArgs):**

```python
class GlobArgs(BaseModel):
    pattern: str = Field(..., min_length=1, max_length=200,
                         description="Glob with `**` and `*` semantics. Examples: "
                                     "'**/*.pdf', 'projects/**/floor-plans/*'")
    path: str = Field("/", pattern=r"^/$|^/[^/]+(/[^/]+)*$",
                      description="Restrict matching to this prefix")
    type: Literal["file", "folder", "both"] = Field("both",
                      description="Match files only, folders only, or both")
    scope: Literal["user", "global", "both"] = Field("both")

    model_config = {"extra": "ignore"}
```

**Glob → SQL semantics (recommended approach):**

The matching surface is `folder_path + '/' + file_name` for files, `folders.path` for folders. Glob translation:

| Glob | SQL `LIKE` | Notes |
|------|-----------|-------|
| `*.pdf` (no slashes) | `LIKE '%.pdf'` (file_name) | Files at root only |
| `projects/*` | `LIKE '/projects/%'` (folder_path) AND `NOT LIKE '/projects/%/%'` (folder_path) | Immediate children of /projects |
| `**/*.pdf` | `LIKE '%.pdf'` (file_name); folder_path unconstrained | Any depth |
| `projects/**/floor-plans/*` | folder_path `~ '^/projects/.*/floor-plans$'` | Two-`**` patterns push to regex |

**Implementation strategy (recommendation):**

1. Walk `pattern` left-to-right, build a `regexp` pattern. Anchor with `^/?`.
2. `*` (single asterisk) → `[^/]*` (no slash crosses); `**` → `.*` (any-depth).
3. Use `documents.folder_path ~ <regex>` AND optionally `documents.file_name LIKE 'last-segment-pattern'` for a fast prefix prefilter when the pattern starts with a literal segment (e.g., `projects/**` → `folder_path LIKE '/projects/%'` AND `folder_path ~ '^/projects/.*$'` — the `LIKE` exploits `documents_folder_path_prefix_idx`, the `~` filters precisely).
4. Fall back to pure `~` for patterns that have no literal prefix (`**/*.pdf`) — `documents_folder_path_trgm_idx` (GIN gin_trgm_ops) accelerates the substring component.

**Output shape:**

```json
{
  "tool": "glob",
  "scope_arg": "both",
  "pattern": "**/*.pdf",
  "path_prefix": "/",
  "matches": [
    {"type": "doc", "document_id": "<uuid>", "file_name": "report.pdf",
     "folder_path": "/projects/2026", "scope": "user"},
    {"type": "doc", "document_id": "<uuid>", "file_name": "policy.pdf",
     "folder_path": "/", "scope": "global"}
  ],
  "total_matches": 2,
  "truncation_marker": null
}
```

**Hard cap on matches:** [ASSUMED 500, mirroring tree's hard cap; the spec gives no explicit number for glob. Confirm with operator.] Apply `apply_12k_cap()` afterward.

**Type=`folder` branch:** SELECT from `folders` UNION (DISTINCT) inferred-from-documents (same UNION shape as `list_folder`).

**Type=`both` branch:** combine file matches (from documents) + folder matches (from folders + inferred). Use `type` field on each row.

---

## Tool Block C — TOOL-03 `grep`

**Args (GrepArgs):**

```python
class GrepArgs(BaseModel):
    pattern: str = Field(..., min_length=1, max_length=500,
                         description="Postgres-flavor regex. Pathological patterns rejected.")
    path: str = Field("/", pattern=r"^/$|^/[^/]+(/[^/]+)*$",
                      description="Restrict to documents under this folder prefix")
    case_insensitive: bool = Field(True)
    multiline: bool = Field(False, description="If true, '.' matches newlines (rarely needed)")
    output_mode: Literal["content", "files_with_matches", "count"] = Field("content")
    A: int = Field(2, ge=0, le=10, description="Lines AFTER match")
    B: int = Field(2, ge=0, le=10, description="Lines BEFORE match")
    C: Optional[int] = Field(None, ge=0, le=10,
                             description="If set, overrides A and B with the same value")
    scope: Literal["user", "global", "both"] = Field("both")

    model_config = {"extra": "ignore"}
```

**Server-side regex pre-screen (Pitfall 3 HOW TO AVOID #6):** before sending to Postgres, run `re.compile(pattern)`. If compilation fails OR if the pattern contains banned constructs (`(.*)+`, `(.+)+`, unbounded backreferences with quantifiers), return `{error: "PATHOLOGICAL_REGEX", message: "..."}`. [ASSUMED: ban list is `(.*)+` and `(.+)+`; planner can extend. Use a simple substring blocklist for v1; full ReDoS detection is out of scope.]

**grep_documents RPC (Migration 020 NEW — DB-side; the only place `SET LOCAL statement_timeout = '5s'` can live):**

```sql
-- Migration 020 / part 1: grep RPC
-- Returns line-resolved hits with ±2 line context, capped at 50, with 5s statement timeout.
-- Uses documents_content_markdown_trgm_idx (Migration 016) via the ILIKE pre-filter.
-- Uses LATERAL regexp_split_to_table for line-number resolution.
CREATE OR REPLACE FUNCTION grep_documents(
  p_pattern         TEXT,
  p_path_prefix     TEXT     DEFAULT '/',         -- documents.folder_path LIKE p_path_prefix || '%'
  p_scope           TEXT     DEFAULT NULL,        -- NULL = both; 'user' | 'global'
  p_user_id         UUID     DEFAULT NULL,        -- required when p_scope IN ('user','both')
  p_case_insensitive BOOLEAN DEFAULT TRUE,
  p_max_hits        INT      DEFAULT 50,
  p_literal_substring TEXT   DEFAULT NULL         -- ILIKE pre-filter; LLM-supplied or auto-extracted
)
RETURNS TABLE (
  document_id  UUID,
  file_name    TEXT,
  folder_path  TEXT,
  scope        TEXT,
  line_no      BIGINT,
  line_text    TEXT,
  status       TEXT     -- 'matched' | 'pending_reindex'
)
LANGUAGE plpgsql
SECURITY INVOKER       -- RLS applies; defense-in-depth (Phase 3 / Plan 01 convention)
AS $$
BEGIN
  -- Per-RPC statement timeout. SET LOCAL is scoped to the enclosing transaction
  -- (PostgREST opens one per .execute() call). Pitfall 3 mitigation #5.
  SET LOCAL statement_timeout = '5s';

  RETURN QUERY
  WITH candidates AS (
    SELECT d.id, d.file_name, d.folder_path, d.scope, d.content_markdown,
           d.content_markdown_status
    FROM documents d
    WHERE d.folder_path = p_path_prefix
       OR d.folder_path LIKE p_path_prefix || (CASE WHEN p_path_prefix = '/' THEN '%' ELSE '/%' END)
      AND (p_scope IS NULL OR d.scope = p_scope)
      AND (p_literal_substring IS NULL
           OR (p_case_insensitive AND d.content_markdown ILIKE '%' || p_literal_substring || '%')
           OR (NOT p_case_insensitive AND d.content_markdown LIKE '%' || p_literal_substring || '%'))
  ),
  pending AS (
    -- Pitfall 6 / Phase 2 LOCKED contract: surface pending_reindex rows
    SELECT id AS document_id, file_name, folder_path, scope,
           NULL::BIGINT AS line_no, NULL::TEXT AS line_text,
           'pending_reindex'::TEXT AS status
    FROM candidates
    WHERE content_markdown_status <> 'ready'
  ),
  matches AS (
    SELECT c.id AS document_id, c.file_name, c.folder_path, c.scope,
           lines.line_no, lines.line_text,
           'matched'::TEXT AS status
    FROM candidates c
    CROSS JOIN LATERAL regexp_split_to_table(c.content_markdown, E'\n')
                       WITH ORDINALITY AS lines(line_text, line_no)
    WHERE c.content_markdown_status = 'ready'
      AND CASE
            WHEN p_case_insensitive THEN lines.line_text ~* p_pattern
            ELSE                          lines.line_text ~  p_pattern
          END
    LIMIT p_max_hits
  )
  SELECT * FROM matches
  UNION ALL
  SELECT * FROM pending
  LIMIT p_max_hits;
END;
$$;
```

**Notes on the RPC:**

- `SECURITY INVOKER` per Phase 3 / Plan 01 convention — RLS applies; service-role callers bypass RLS as expected, JWT callers see only their scope-permitted rows.
- `p_literal_substring`: optional ILIKE pre-filter. If the LLM passes a literal-substring hint in the args (`literal_hint`), use it; otherwise auto-extract a literal substring of length ≥ 3 from the regex (e.g., `panel|switch` → no usable literal; `panel-2026` → use `panel-`). Without a literal, the GIN trigram index can't help and the query falls back to seq-scan over `candidates` — still bounded by `p_path_prefix`. [ASSUMED: TOOL-03 args don't currently include a `literal_hint` field. Recommend planner ADDS one OR auto-extracts at the Python wrapper layer.]
- `WITH ORDINALITY` gives the line number (1-based natively in Postgres — perfect for arrow-form output).
- `LIMIT p_max_hits` applied twice is intentional: once inside `matches` to cap regex evaluation, once on the final UNION to bound total response size.

**Python wrapper (services/exploration_tools/grep.py):**

```python
@traceable(name="grep", run_type="tool")
def grep(args: GrepArgs, user_id: Optional[str], supabase_client) -> dict:
    norm = normalize_path(args.path)
    # Pre-screen: reject pathological regexes
    try:
        re.compile(args.pattern)
    except re.error as e:
        return {"tool": "grep", "error": "INVALID_REGEX", "message": str(e)}
    # Cheap blocklist
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

    # Build output with ±A/B/C context (Python-side; the RPC returned single-line hits).
    # For each (document_id, line_no), fetch ±A/B context from the same content_markdown
    # via a follow-up query — OR cache content_markdown in the candidates CTE and slice
    # in Python. Recommendation: slice in Python (cheaper than two round trips).
    # ...

    return apply_12k_cap({
        "tool": "grep",
        "scope_arg": args.scope,
        "pattern": args.pattern,
        "path": norm,
        "hits": [_build_hit_row(r, args) for r in rows],   # each carries scope (TOOL-07)
        "total_hits": len(rows),
        "truncation_marker": ...,
    })
```

**Output shape (mode=content):**

```json
{
  "tool": "grep",
  "scope_arg": "both",
  "pattern": "panel.*2026",
  "path": "/projects",
  "hits": [
    {
      "document_id": "<uuid>",
      "file_name": "electrical-spec.md",
      "folder_path": "/projects/2026",
      "scope": "user",
      "line_no": 47,
      "context": [
        {"line_no": 45, "text": "## Section 4.2 — Distribution Panels"},
        {"line_no": 46, "text": ""},
        {"line_no": 47, "text": "Panel MDB-C-G3 is the main 2026 distribution unit."},
        {"line_no": 48, "text": "Capacity: 800A. Phases: 3."},
        {"line_no": 49, "text": ""}
      ]
    },
    {
      "document_id": "<uuid>",
      "file_name": "old-doc.pdf",
      "folder_path": "/imports",
      "scope": "user",
      "status": "pending_reindex",
      "content_markdown_status": "requires_user_reupload"
    }
  ],
  "total_hits": 2,
  "truncation_marker": null
}
```

**Output shape (mode=files_with_matches):** same shape but each hit only carries `document_id`, `file_name`, `folder_path`, `scope`. **mode=count:** `{"hits": [], "count_per_document": [{"document_id": "<uuid>", "scope": "user", "match_count": 5}]}`.

**EXPLAIN-Bitmap-Index-Scan verification (TEST-02):**

```sql
-- Run inside the test BEFORE the actual grep RPC call against the 5000-doc fixture.
-- Assert the output contains 'Bitmap Index Scan on documents_content_markdown_trgm_idx'.
EXPLAIN (ANALYZE, FORMAT TEXT)
SELECT id FROM documents
WHERE content_markdown ILIKE '%capybara%'
  AND folder_path LIKE '/grep-fixture/%'
LIMIT 50;
```

The test asserts the literal string `Bitmap Index Scan` appears in the EXPLAIN output. (Phase 1 / Plan 06 already established this verification idiom; pattern is reusable.)

---

## Tool Block D — TOOL-04 `list_files`

**Args (ListFilesArgs):**

```python
class ListFilesArgs(BaseModel):
    path: str = Field("/", pattern=r"^/$|^/[^/]+(/[^/]+)*$")
    scope: Literal["user", "global", "both"] = Field("both")

    model_config = {"extra": "ignore"}
```

**Algorithm:** thin wrapper around `folder_service.list_folder()`.

```python
@traceable(name="list_files", run_type="tool")
def list_files(args: ListFilesArgs, user_id: Optional[str], supabase_client) -> dict:
    norm = normalize_path(args.path)
    folder = folder_service.list_folder(norm, args.scope, user_id, supabase_client)
    # folder['documents'] and folder['subfolders'] are present

    # Phase 4 ORDERING contract (TOOL-04): folders-then-files, alpha within each.
    folders_sorted = sorted(folder["subfolders"])
    docs_sorted = sorted(folder["documents"], key=lambda d: d.get("file_name", "").lower())

    entries = []
    for sf in folders_sorted:
        # sf is a path str; need to know its scope. Re-derive from the doc list or
        # query. Recommendation: list_folder() should return tuples (path, scope)
        # for subfolders — this is a Phase 3 / Plan 02 enhancement (LO ticket).
        entries.append({"type": "folder", "path": sf, "scope": _infer_scope(sf, folder["documents"])})
    for d in docs_sorted:
        entries.append({
            "type": "doc",
            "document_id": d["id"],
            "file_name": d["file_name"],
            "folder_path": d["folder_path"],
            "scope": d["scope"],   # already projected from folder_service.list_folder()
        })

    return apply_12k_cap({
        "tool": "list_files",
        "scope_arg": args.scope,
        "path": norm,
        "entries": entries,
        "total": len(entries),
        "truncation_marker": ...,
    })
```

**Note on subfolder scope:** `folder_service.list_folder()` currently returns `subfolders: list[str]` (just paths, no scope). Phase 4 either:
- (a) **Extend `list_folder()` to return `subfolders: list[{path, scope}]`** (Phase 3 LO-side improvement; mildly breaking change to `folders.py:list_folders` consumer in Phase 6), or
- (b) **Re-derive scope per subfolder** in the wrapper (cheap: filter the documents list by `folder_path.startswith(sub)` and take any row's scope).

[ASSUMED option (b) is simpler and preserves Phase 3's public contract; recommend planner adopts it. Confirm if the planner prefers (a).]

---

## Tool Block E — TOOL-05 `read_document`

**Args (ReadDocumentArgs):**

```python
class ReadDocumentArgs(BaseModel):
    document_id: Optional[str] = Field(None,
        description="Either document_id OR path (document_id+file_name combo) is required")
    path: Optional[str] = Field(None,
        pattern=r"^/$|^/[^/]+(/[^/]+)*/[^/]+$",   # /folder/file_name shape
        description="Folder + file_name combo, e.g. '/projects/readme.md'")
    offset: int = Field(1, ge=1,
        description="1-based line number to START at (Claude Code convention).")
    limit: int = Field(2000, ge=1, le=5000,
        description="Lines to return. Default 2000. Hard cap 5000.")

    model_config = {"extra": "ignore"}

    # Cross-field validator: at least one of document_id or path must be set
    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.document_id is None) == (self.path is None):
            raise ValueError("Specify exactly one of document_id or path")
        return self
```

**Algorithm:**

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
    total_lines = len(lines)

    start_idx = args.offset - 1                           # external 1-based -> internal 0-based
    end_idx = min(start_idx + args.limit, total_lines)
    slice_ = lines[start_idx:end_idx]

    # Arrow-form rendering: {line_no}→{content}\n
    rendered = "\n".join(f"{start_idx + i + 1}→{line}" for i, line in enumerate(slice_))

    # 12K cap with UTF-8 codepoint-safe truncation of the LAST line only
    if len(rendered) > 12_000:
        truncated = rendered.encode("utf-8")[:12_000].decode("utf-8", errors="ignore")
        # Trim back to a complete line so the LAST line isn't half-shown
        last_nl = truncated.rfind("\n")
        if last_nl != -1:
            truncated = truncated[:last_nl]
        # Append the marker
        truncated += f"\n[...truncated, {total_lines - (start_idx + len(truncated.splitlines()))} more lines]"
        rendered = truncated

    return {
        "tool": "read_document",
        "document_id": row["id"], "file_name": row["file_name"],
        "scope": row["scope"], "folder_path": row["folder_path"],
        "start_line": start_idx + 1,
        "end_line": start_idx + len(slice_),
        "total_lines": total_lines,
        "content": rendered,
    }
```

**Key invariants:**

- **CRLF normalization happened at ingestion** (Phase 2 / Plan 02 LOCKED): the markdown stored in `content_markdown` has `\n` only — no `\r`. Verified by inspecting `extract_text()` flow in Phase 2 research; if a regression introduces `\r`, `splitlines(keepends=False)` still handles it correctly (returns lines without the `\r\n`). Defense-in-depth.
- **`splitlines(keepends=False)` consistently** — line count is stable.
- **1-based external offset, 0-based internal index** — `offset=1` returns line 1.
- **Slicing by line, not by char** — UTF-8 codepoint integrity preserved naturally; the last-line truncation uses `bytes_or_str.encode('utf-8')[:N].decode('utf-8', errors='ignore')` (Pitfall 9 idiom).
- **Arrow-form**: `f"{n}→{content}"` literal `→` (U+2192). Test fixtures must include this exact codepoint.

**Edge cases the fixtures cover (TEST-02):**

| Fixture | What it tests |
|---------|---------------|
| Mixed-ending doc (`\r\n` + `\n` mix) | `splitlines()` handles uniformly; line count matches expected |
| CRLF-only doc | Same |
| Single 50K-char line | Truncation hits mid-line; UTF-8 safety holds |
| Emoji + combining characters in tables | UTF-8 codepoint integrity; no `'invalid utf-8'` SSE errors |
| Tail offset (`offset=total_lines`) | Returns last 1 line; `end_line == total_lines`; `start_line == total_lines` |
| Beyond-EOF offset (`offset=total_lines + 100`) | Returns empty `content`; `start_line == offset`; `end_line == offset - 1` (or similar — planner picks shape) |

---

## TOOL-06 Shared Pydantic Schema Module

**Recommended path:** `backend/app/services/exploration_tools/schemas.py` (Option A package layout) OR `backend/app/services/exploration_tools_schemas.py` (Option B sibling-module).

**Module shape:**

```python
# backend/app/services/exploration_tools/schemas.py
"""TOOL-06: Pydantic v2 BaseModel argument schemas for the five exploration tools.

Every model uses:
  - Literal["user","global","both"] for scope (TOOL-06)
  - Field(..., ge=, le=) for numeric bounds (TOOL-06)
  - regex pattern for path (matches Migration 012 canonical-form CHECK)
  - extra='ignore' (Phase 3 / Plan 01 LOCKED defense layer — silently drops smuggled fields)

Public API: TreeArgs, GlobArgs, GrepArgs, ListFilesArgs, ReadDocumentArgs.

NOTE: these models validate the LLM's tool-call arguments, NOT user input. The LLM
is the only caller. There is no router-layer Pydantic FastAPI binding here.
"""
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

**Where they're used:**

1. **Inside `_build_*_tool()` factories in `openai_client.py`** — to derive `types.Schema` for the Gemini SDK. Two options:
   - (a) **Manual**: hand-write `types.Schema(type="OBJECT", properties={...}, required=[...])` matching each Pydantic model. Mirrors the existing `_build_search_tool()` style at `openai_client.py:69-117`.
   - (b) **Auto**: `pydantic_to_genai_schema(TreeArgs)` helper that introspects `TreeArgs.model_json_schema()` and emits `types.Schema`. Net-new helper but DRY across 5 tools. [ASSUMED: option (a) is the project norm; option (b) is a small library worth building if the operator agrees.]
2. **Inside each tool function** as the first arg — `def tree(args: TreeArgs, user_id, sb): ...` — Pydantic re-validates at the entry point (defense-in-depth, since the LLM may smuggle unknown fields the SDK doesn't strip).
3. **Inside the dispatch arms in `stream_response()`** — `args = TreeArgs(**raw_args_dict); result = tree(args, user_id, supabase)`.

---

## TOOL-09 Layered-Fallback Wrapper

**The wrapper is NOT a discrete callable.** It is the inline pattern at `backend/app/services/openai_client.py:565-610` (within the `if has_function_call:` branch of `stream_response()`). The pattern has four layers:

```
Layer 1: Truncation        result_text[:16000] if len(result_text) > 16000 else result_text
                           (line 567)
Layer 2: Streaming Call#2  client.models.generate_content_stream(...)  with
                           system_instruction = system_with_context
                           yield ('token', chunk.text) for each non-empty chunk
                           (lines 578-588)
Layer 3: Non-streaming     If has_response2_text is False (zero tokens emitted):
         fallback          client.models.generate_content(...) with same config
                           Yield ('token', part.text) for each part with text
                           (lines 591-605)
Layer 4: Raw yield         If still no text and result_text is non-empty:
                           yield ('token', result_text)
                           (lines 608-610)
```

**Verbatim signature:** there is none — the pattern is inline. The `system_with_context` string template at lines 568-575 is the contract:

```python
truncated_result = result_text[:16000] if len(result_text) > 16000 else result_text
system_with_context = f"""You are a helpful assistant. Use the provided tool results to answer the user's question accurately.
If the tool encountered an error, explain the issue to the user in simple terms and suggest they rephrase their question.
If the results do not contain enough information, clearly state that the available documents do not contain the answer. Do NOT dump or echo the raw tool results back to the user. Instead, briefly explain what information was found (if any) and suggest the user try a different query or upload a document that might contain the answer. You may answer from general knowledge if applicable, but clearly label it as such.
When citing web sources, include the URLs.
{OUTPUT_FORMAT_RULES}

Tool ({tool_name}) results:
{truncated_result}"""
```

**Phase 4 compliance for TOOL-09 means:**

1. **Each new tool function returns a `dict` or `str`** — never streams its own response. The dispatch arm in `stream_response()` JSON-stringifies the dict into `result_text`.
2. **The dispatch arm assigns to the same `result_text` variable** that the existing arms use (`search_documents`, `query_structured_data`, `web_search`, `analyze_document`). This is the literal hook that flows into the existing Layer 1–4 wrapper.
3. **No tool function calls `client.models.generate_content_stream` itself.**
4. **Each new tool emits `tool_start` and `tool_done` SSE events** at dispatch boundaries, matching the existing arms' yields:

```python
# Sketch — within the if has_function_call: block, after detecting tool_name == "tree"
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

The crucial element: `result_text = json.dumps(tree_result)` lands in the same variable that the existing layered-fallback at lines 565-610 consumes. **No tool reinvents the wrapper.**

---

## SEARCH-01..03 search_documents Extension

### SEARCH-01 — Tool schema diff in `_build_search_tool()` (`openai_client.py:69-117`)

**Current shape (current):**

```python
parameters=types.Schema(
    type="OBJECT",
    properties={
        "query": types.Schema(type="STRING", ...),
        **filter_properties,    # dynamic per metadata_schema
    },
    required=["query"],
)
```

**Phase 4 diff:** add two optional properties; preserve `required=["query"]`:

```python
properties={
    "query": types.Schema(...),
    **filter_properties,
    "folder_path": types.Schema(
        type="STRING",
        description="OPTIONAL prefix filter. Only documents under this canonical path "
                    "are searched. Use when the user's question is clearly scoped to a "
                    "specific area of the knowledge base (e.g., 'in /projects/2026'). "
                    "Default: null (no narrowing).",
    ),
    "scope": types.Schema(
        type="STRING",
        enum=["user", "global", "both"],
        description="OPTIONAL. Restrict search to user-private docs, global shared docs, "
                    "or both. Default: null (no narrowing).",
    ),
},
required=["query"],
```

### SEARCH-02 — RPC migration shape (Migration 020)

**Current signatures (verbatim from `migrations/007_document_metadata.sql:36-56` and `migrations/011_improved_keyword_search.sql:6-55`):**

```sql
-- match_document_chunks_with_filters
CREATE OR REPLACE FUNCTION match_document_chunks_with_filters(
  query_embedding  vector(768),
  match_user_id    UUID,
  match_count      INT DEFAULT 5,
  metadata_filter  JSONB DEFAULT NULL
) RETURNS TABLE (id UUID, document_id UUID, content TEXT, similarity FLOAT)

-- match_document_chunks_hybrid
CREATE OR REPLACE FUNCTION match_document_chunks_hybrid(
  query_embedding  vector(768),
  query_text       TEXT,
  match_user_id    UUID,
  match_count      INT DEFAULT 20,
  metadata_filter  JSONB DEFAULT NULL,
  rrf_k            INT DEFAULT 60
) RETURNS TABLE (id UUID, document_id UUID, content TEXT, rrf_score FLOAT)
```

**Phase 4 extensions (Migration 020 / part 2):** add `match_folder_path TEXT DEFAULT NULL` and `match_scope TEXT DEFAULT NULL` to BOTH functions. NULL defaults preserve existing behavior — REQUIREMENTS.md SEARCH-02 explicit: "(NULL defaults)".

```sql
-- Migration 020 / part 2: extend two RPCs with folder_path + scope filters.
-- Phase 4 / SEARCH-02. NULL defaults preserve existing call sites.
CREATE OR REPLACE FUNCTION match_document_chunks_with_filters(
  query_embedding   vector(768),
  match_user_id     UUID,
  match_count       INT     DEFAULT 5,
  metadata_filter   JSONB   DEFAULT NULL,
  match_folder_path TEXT    DEFAULT NULL,           -- NEW
  match_scope       TEXT    DEFAULT NULL            -- NEW
) RETURNS TABLE (id UUID, document_id UUID, content TEXT, similarity FLOAT)
LANGUAGE plpgsql AS $$
BEGIN
  RETURN QUERY
  SELECT dc.id, dc.document_id, dc.content,
         1 - (dc.embedding <=> query_embedding) AS similarity
  FROM document_chunks dc
  JOIN documents d ON dc.document_id = d.id
  WHERE dc.user_id = match_user_id
    AND dc.embedding IS NOT NULL
    AND (metadata_filter IS NULL OR d.metadata @> metadata_filter)
    AND (match_folder_path IS NULL                                       -- NEW
         OR d.folder_path = match_folder_path
         OR d.folder_path LIKE match_folder_path || (CASE WHEN match_folder_path = '/' THEN '%' ELSE '/%' END))
    AND (match_scope IS NULL OR d.scope = match_scope)                   -- NEW
  ORDER BY dc.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;

-- match_document_chunks_hybrid: same two new params, applied identically
-- in BOTH the vector_results CTE and the keyword_results CTE.
```

**Critical non-breaking confirmation:** existing call sites in `openai_client.py:212-225` pass parameters by keyword (`{"query_embedding": ..., "match_user_id": ..., "match_count": ..., "metadata_filter": ...}`). Adding two new keyword-args with `DEFAULT NULL` is backward-compatible — supabase-py / PostgREST does NOT require the caller to send them. **Default-NULL is the only safe non-breaking shape — confirmed.**

**Migration shape:** `CREATE OR REPLACE FUNCTION` — Postgres allows changing parameter defaults / adding new tail-position parameters via `CREATE OR REPLACE`. Both RPCs change in the same migration file (Migration 020) for review atomicity. Same pattern as Migration 011 already established (`CREATE OR REPLACE` on `match_document_chunks_hybrid`).

### SEARCH-03 — System prompt insertion

**Current shape (from `openai_client.py:39-66`):** `_build_system_prompt(has_documents, has_structured_data, web_search_enabled)` returns a string. The "TOOL SELECTION RULES" section starts at line 55.

**Phase 4 insertion (after the existing `analyze_document` rule, BEFORE the "Only call ONE tool per turn." rule):**

```python
# Insert at the appropriate position in the parts list — after the search_documents rule:
parts.append("- Use search_documents ONLY for finding specific facts, quotes, or snippets across multiple documents (e.g. 'what does the contract say about payment terms?').")

# NEW (Phase 4 / SEARCH-03):
parts.append("- When the user's question is clearly scoped to a folder, pass `folder_path` to search_documents to narrow the search (e.g. 'in /projects/2026'). When the question is about admin-curated shared content vs. the user's private docs, pass `scope='global'` or `scope='user'`. Otherwise leave both unset.")

# NEW (Phase 4 / TOOL-01..05 awareness):
if has_documents:
    parts.append("- For codebase-style precision: use `tree` to see the folder structure, `glob` to find files by name pattern, `grep` to search inside document text by regex, `list_files` to see one folder's contents, and `read_document` to read specific lines of a doc. Prefer these over search_documents when the user asks 'where is X' or 'show me all PDFs in /projects'.")
    parts.append("- Tool results carry a 'scope' field on every row. When citing a result, mention whether it came from the user's private docs (scope='user') or the shared knowledge base (scope='global'). Don't conflate the two.")
```

**Insertion site:** `_build_system_prompt()` lives in the same file (`openai_client.py:39-66`) — the diff is localized.

---

## TEST-02 Test Fixture Engineering

**File:** `backend/scripts/test_exploration_tools.py` (NEW). Module structure mirrors `test_folders.py` exactly:

```python
# Top-of-file (mirror test_folders.py:36-69)
import concurrent.futures
import os
import re
import sys
import uuid

import requests

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

import test_helpers as h  # noqa: E402
from supabase import create_client  # noqa: E402

CAPYBARA_TEXT = b"..."   # reused fixture
EMOJI_TEXT = b"..."      # NEW for read_document Unicode fixture
CRLF_TEXT = b"line1\r\nline2\r\nline3"
MIXED_TEXT = b"line1\r\nline2\nline3\rline4"
LONG_LINE_TEXT = b"x" * 50_000
STORAGE_BUCKET = "documents"

_tracked_documents: list = []
_tracked_folders: list = []
_tracked_storage_paths: list = []


def _service_role_client():
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )


def _track_doc(doc_id, sb_client):
    if doc_id:
        _tracked_documents.append((doc_id, sb_client))


def _verify_phase4_setup(sb_admin):
    """Canary: assert prerequisites for Phase 4 tests.

    Probes:
      1. documents_content_markdown_trgm_idx exists (Migration 016)
      2. grep_documents RPC exists (Migration 020)
      3. match_document_chunks_with_filters has match_folder_path parameter (Migration 020)
      4. POST /api/threads + send_message endpoints respond (backend running)
      5. At least one ready document exists in the corpus (otherwise grep returns empty)
    """
    # (... mirroring test_folders.py:_verify_phase3_setup pattern ...)
```

### Sub-section: 200-folder fixture (TOOL-01 truncation)

```python
def _seed_200_folder_fixture(sb_admin, user_id):
    """Insert 200 explicit folders + 200 docs distributed under them.

    Returns: (folder_ids, doc_ids) — both tracked for cleanup.
    """
    folder_ids = []
    doc_ids = []
    base = f"/tree-fixture-{uuid.uuid4().hex[:8]}"
    # Tree shape: 10 top-level + 19 under each (200 total)
    for i in range(10):
        top = f"{base}/top-{i:02d}"
        f1 = sb_admin.table("folders").insert({
            "scope": "user", "user_id": user_id, "path": top,
        }).execute()
        folder_ids.append(f1.data[0]["id"])
        for j in range(19):
            sub = f"{top}/sub-{j:02d}"
            f2 = sb_admin.table("folders").insert({
                "scope": "user", "user_id": user_id, "path": sub,
            }).execute()
            folder_ids.append(f2.data[0]["id"])
            d = sb_admin.table("documents").insert({
                "user_id": user_id, "scope": "user", "folder_path": sub,
                "file_name": f"doc-{i:02d}-{j:02d}.txt",
                "file_size": 1, "mime_type": "text/plain", "status": "ready",
                "content_markdown": f"# Doc {i}-{j}\nContent line.",
                "content_markdown_status": "ready",
            }).execute()
            doc_ids.append(d.data[0]["id"])
    for fid in folder_ids: _tracked_folders.append((fid, sb_admin))
    for did in doc_ids: _tracked_documents.append((did, sb_admin))
    return folder_ids, doc_ids, base
```

**Assertions:**

- Tree result with `path=base, max_depth=2, scope='user'` returns serialized JSON `< 12_000` chars.
- Tree result contains `truncation_marker` non-null (200 folders > 500 entry hard cap not hit, but max_depth=2 forces summarization at level 2; verify `[N more folders, M more docs]` appears).
- Tree result with `path=base, max_depth=99, scope='user'` returns the **same** result as `max_depth=4` (server-side cap; `Field(le=4)` clamps).

### Sub-section: 5000-doc grep fixture (TOOL-03 perf + Bitmap Index Scan EXPLAIN)

```python
def _seed_5000_doc_grep_fixture(sb_admin, user_id):
    """Bulk-insert 5000 docs with deterministic content_markdown.

    Each doc has a unique 5-char marker plus the literal 'capybara' on a known line
    so grep can be exercised. Path: /grep-fixture/{uuid_prefix}
    """
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

**Assertions:**

- **Bitmap Index Scan EXPLAIN:** before invoking the grep RPC, run a probe via `psycopg2` (DATABASE_URL required, gracefully SKIP if missing — same idiom as `test_folders.py:303-308`):

```python
import psycopg2
db_url = os.environ.get("DATABASE_URL")
if not db_url:
    h.test("TOOL-03 EXPLAIN Bitmap Index Scan SKIPPED (no DATABASE_URL)", True,
           "set DATABASE_URL env var to run; perf assertion below still validates speed")
else:
    pg = psycopg2.connect(db_url); pg.autocommit = True
    try:
        with pg.cursor() as cur:
            cur.execute(
                "EXPLAIN (ANALYZE, FORMAT TEXT) "
                "SELECT id FROM documents "
                "WHERE content_markdown ILIKE '%capybara%' "
                "  AND folder_path LIKE %s "
                "LIMIT 50;",
                (base_path + "/%",))
            plan = "\n".join(row[0] for row in cur.fetchall())
            h.test("TOOL-03 EXPLAIN shows Bitmap Index Scan on documents_content_markdown_trgm_idx",
                   "Bitmap Index Scan" in plan and "documents_content_markdown_trgm_idx" in plan,
                   f"plan: {plan[:400]}")
    finally:
        pg.close()
```

- **Perf < 500ms p95** (ROADMAP Phase 4 SC3 + Pitfall 3):

```python
import time
durations = []
for _ in range(10):
    t0 = time.perf_counter()
    sb_admin.rpc("grep_documents", {
        "p_pattern": "capybara",
        "p_path_prefix": base_path,
        "p_scope": "user",
        "p_user_id": user_id,
        "p_case_insensitive": True,
        "p_max_hits": 50,
        "p_literal_substring": "capybara",
    }).execute()
    durations.append((time.perf_counter() - t0) * 1000)
durations.sort()
p95 = durations[int(0.95 * len(durations))]
h.test("TOOL-03 grep p95 < 500ms over 5000-doc fixture", p95 < 500,
       f"p95={p95:.1f}ms, all={[round(d,1) for d in durations]}")
```

### Sub-section: read_document fixtures (TOOL-05 byte-stable)

For each of {CRLF doc, mixed-ending doc, single-50K-char-line doc, emoji+combining-char doc}:

1. Insert via service-role with the fixture content already in `content_markdown` (no Docling needed — direct insert).
2. Call `read_document(document_id=<id>, offset=1, limit=10)` via the dispatch (or directly via the service function for unit-style assertions).
3. Assert: arrow-form is exact (`"1→<expected line 1>"`); `total_lines` matches the fixture; UTF-8 codepoints intact (no `�` REPLACEMENT CHARACTER).
4. For the 50K-char single-line doc: call with `offset=1, limit=1`; assert truncation marker fired and the truncated line is valid UTF-8.

### Sub-section: Adversarial empty-response guard (Pitfall 8 / TOOL-09)

```python
def _test_empty_response_guard(sb_admin, user_id, token):
    """Verify the layered-fallback wrapper at openai_client.py:565-610 catches
    a 50K-char tool result and produces a non-empty assistant response.

    Strategy:
      1. Seed a single doc with content_markdown of length ~50K (above the 16K
         truncation in the wrapper).
      2. Create a thread; send a message that forces grep with a literal that
         matches in 50 hits each with ±2 line context (max grep payload).
      3. Stream the SSE response; collect tokens; assert len(tokens) > 0 AND
         a 'done' event fired.
    """
    # ...
    h.test("TOOL-09 adversarial 50K-char tool result produces non-empty response",
           full_text and len(full_text) > 0 and has_done_event,
           f"got tokens={len(full_text)} done={has_done_event}")
```

**This is the gate that proves TOOL-09 routing works for every new tool.**

### Cleanup discipline (CLAUDE.md verbatim)

```python
def _cleanup():
    """Per-id .delete().eq() in finally. Mirror test_folders.py:131-155."""
    for did, client in _tracked_documents:
        try: client.table("document_chunks").delete().eq("document_id", did).execute()
        except Exception: pass
        try: client.table("documents").delete().eq("id", did).execute()
        except Exception: pass
    for fid, client in _tracked_folders:
        try: client.table("folders").delete().eq("id", fid).execute()
        except Exception: pass
    if _tracked_storage_paths:
        try:
            sb = _service_role_client()
            sb.storage.from_(STORAGE_BUCKET).remove(_tracked_storage_paths)
        except Exception: pass
    _tracked_documents.clear(); _tracked_folders.clear(); _tracked_storage_paths.clear()
```

### Registration in `test_all.py`

Append `("Exploration", test_exploration_tools)` to the SUITES list, between `("Folders", test_folders)` and `("Backfill", test_backfill)` — same convention Phase 3 / Plan 06 used. Plus `import test_exploration_tools  # NEW (Phase 4)`.

---

## Common Pitfalls (Phase 4 active set)

### Pitfall 2: tree context blow-up

**What goes wrong:** 1000+ folder corpus → tree returns 100K+ tokens → Gemini empty-response.
**Mitigations enforced:** `Field(le=4)` server-side cap on `max_depth`; 500-entry hard cap (Python-side counter during traversal); `[N more folders, M more docs]` summary at cutoff; `apply_12k_cap()` post-traversal; layered-fallback at `stream_response`.
**Warning signs:** `tool_done` SSE event detail shows `entries=500` repeatedly; assistant message empty post-tool-call.

### Pitfall 3: grep perf collapse

**What goes wrong:** Seq Scan on `documents.content_markdown` instead of Bitmap Index Scan; 5s+ latencies; connection pool starvation.
**Mitigations enforced:** ILIKE pre-filter with literal substring (auto-extracted or LLM-passed); `documents_content_markdown_trgm_idx` (Migration 016 already shipped); `SET LOCAL statement_timeout = '5s'` inside the RPC; max-hits cap at 50; pathological-regex blocklist at Python wrapper.
**Warning signs:** `EXPLAIN ANALYZE` shows `Seq Scan`; p95 > 500ms; `statement timeout` errors in logs.

### Pitfall 8: Gemini empty-response

**What goes wrong:** Tool result is malformed JSON or > 16K chars; Gemini returns zero stream chunks.
**Mitigations enforced:** Existing layered-fallback at `openai_client.py:565-610` (truncate → stream → non-stream → raw yield). Phase 4 compliance = every tool routes through the unified dispatch loop, not a parallel context-injection path.
**Warning signs:** Empty assistant message persisted (Phase 1 bugfix already filters these out at `messages.py:111-122`); `tool_thinking` + `done` SSE without `token` events between.

### Pitfall 9: read_document line drift

**What goes wrong:** CRLF/LF mismatch; mid-codepoint truncation; off-by-one between 1-based and 0-based offsets.
**Mitigations enforced:** Phase 2 ingestion already normalizes CRLF to LF in `content_markdown`; `splitlines(keepends=False)` consistently; 1-based external offset; `bytes.encode('utf-8')[:N].decode('utf-8', errors='ignore')` for codepoint-safe truncation; both `start_line` AND `end_line` returned for verification.
**Warning signs:** LLM cites "line 47" but content is actually at line 46; SSE stream errors with "invalid UTF-8".

### Pitfall 11: scope confusion

**What goes wrong:** Tool flattens user + global rows; LLM cites a global doc as user's own.
**Mitigations enforced:** TOOL-07 invariant (every row carries `scope`); SEARCH-03 system prompt instructs LLM to disambiguate; `_scope_tag.ensure_scope_tag()` defense-in-depth helper; LangSmith trace audit assertion in TEST-02.
**Warning signs:** User reports "I never wrote that"; a/b trace check shows scope=both answers vs scope=user answers conflate sources.

---

## Code Examples (verified patterns)

### Pattern: New tool registration + dispatch arm (additive)

```python
# Source: openai_client.py:337-403 (existing _build_tools loop)
# Phase 4 inserts the 5 tool factories alongside _build_analyze_tool, _build_search_tool, etc.
function_declarations = []
if has_documents:
    try: function_declarations.append(_build_analyze_tool())
    except Exception as e: logger.warning(f"...")
    try: function_declarations.append(_build_search_tool())   # SEARCH-01 extension lives inside this factory
    except Exception as e: logger.warning(f"...")
    # NEW (Phase 4):
    try: function_declarations.append(_build_tree_tool())
    except Exception as e: logger.warning(f"Failed to build tree tool (non-fatal): {e}")
    try: function_declarations.append(_build_glob_tool())
    except Exception as e: logger.warning(f"Failed to build glob tool (non-fatal): {e}")
    try: function_declarations.append(_build_grep_tool())
    except Exception as e: logger.warning(f"Failed to build grep tool (non-fatal): {e}")
    try: function_declarations.append(_build_list_files_tool())
    except Exception as e: logger.warning(f"Failed to build list_files tool (non-fatal): {e}")
    try: function_declarations.append(_build_read_document_tool())
    except Exception as e: logger.warning(f"Failed to build read_document tool (non-fatal): {e}")
```

```python
# Source: openai_client.py:469-563 (existing dispatch elif chain)
# Phase 4 inserts 5 new arms after the analyze_document branch (line 561).
elif tool_name == "tree":
    from app.services.exploration_tools import tree as tool_tree
    from app.services.exploration_tools.schemas import TreeArgs
    try:
        tree_args = TreeArgs(**args)
    except Exception as e:
        result_text = json.dumps({"error": "INVALID_ARGS", "message": str(e)})
    else:
        result_text = json.dumps(tool_tree(tree_args, user_id, supabase_client))
    yield ("tool_done", json.dumps({"tool": tool_name, "detail": "..."}))

elif tool_name == "glob":
    # ... same shape ...

elif tool_name == "grep":
    # ... same shape ...

elif tool_name == "list_files":
    # ... same shape ...

elif tool_name == "read_document":
    # ... same shape ...
```

### Pattern: PostgREST or() filter for scope='both' (Phase 3 / Plan 02 idiom)

```python
# Source: folder_service.py:170 (verbatim)
docs_q = docs_q.or_(
    f"and(scope.eq.user,user_id.eq.{user_id}),and(scope.eq.global,user_id.is.null)"
)
# user_id MUST be _assert_uuid() before f-string interpolation (HI-01)
```

### Pattern: SET LOCAL statement_timeout in PL/pgSQL

```sql
-- Source: NEW for Phase 4 — no existing precedent in backend/
-- Per-RPC GUC; scoped to the enclosing transaction (PostgREST opens one per .execute()).
CREATE OR REPLACE FUNCTION grep_documents(...) AS $$
BEGIN
  SET LOCAL statement_timeout = '5s';
  RETURN QUERY ...;
END;
$$ LANGUAGE plpgsql;
```

[VERIFIED: Postgres docs — `SET LOCAL` resets at transaction end; PostgREST wraps each `.execute()` in a transaction; ergo `SET LOCAL` inside the RPC body is the canonical idiom for per-call timeout. Cross-checked against `https://www.postgresql.org/docs/current/sql-set.html`.]

---

## State of the Art (2026-05-08)

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Reconstruct content_markdown from chunks via string_agg | Re-run Docling and store canonical export | Phase 2 / Plan 02 (2026-05-06) | grep line numbers stable; FORBIDDEN by static grep gate |
| Single-axis user_id RLS | Two-scope RLS (user + global) | Phase 1 / Plan 05 (2026-05-03) | Phase 4 SELECTs trust RLS for visibility, use scope for narrowing |
| Embedded Pydantic in router signatures only | `extra='ignore'` Pydantic v2 as a drop-smuggling defense layer | Phase 3 / Plan 01 (2026-05-07) | TOOL-06 inherits `extra='ignore'` for arg models |
| `HTTPException(detail=str)` for everything | `JSONResponse(status_code, content={error, ...})` for multi-field errors | Phase 3 / Plan 04 (2026-05-07) | Phase 4 tool error rows mirror this shape |
| Bare `auth.uid()` per-row in RLS predicates | `(SELECT auth.uid())` perf-cached subquery | Phase 1 / Plan 05 (2026-05-03) | Phase 4 grep RPC inherits this convention if we add any inline RLS-style predicates |

**Deprecated/outdated:**
- `match_document_chunks` (Module 6 plain version, no metadata filter) — superseded by `match_document_chunks_with_filters` (Module 7) and `match_document_chunks_hybrid` (Module 9). Phase 4 only modifies the latter two.
- Any tool that reads chunks instead of `content_markdown` for grep — superseded by Phase 2 (Pitfall 6 RANK 2).

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | "12K-char" cap means UTF-8 chars (codepoints), not tokens | TOOL-08, Pattern B | If tokens: need `tiktoken` or Gemini count_tokens RPC; affects truncation math. Char interpretation is consistent with `openai_client.py:567` precedent. |
| A2 | TOOL-01 server-side `max_depth` cap is 4 (resolving REQUIREMENTS.md "4–6" range to the lower bound for safety) | Tool Block A | If 6 is preferred: 1-character change to `Field(le=6)`. Cap of 4 is more conservative re Pitfall 2. |
| A3 | TOOL-02 glob hard match cap is 500 (mirroring tree's cap) | Tool Block B | If different: 1-line change. Spec is silent on glob cap. |
| A4 | TOOL-03 pathological-regex blocklist is `(.*)+` and `(.+)+` (cheap substring ban) | Tool Block C | A more thorough ReDoS detector exists (e.g., `regex` library's atomic-group detection) but is out of scope for v1. |
| A5 | `list_folder()` will NOT be extended to return `subfolders: list[{path,scope}]` — Phase 4 re-derives scope per subfolder in the wrapper | Tool Block D | If chosen otherwise: small breaking change to `folder_service.list_folder` consumers (currently only `folders.py:list_folders` and Phase 4). |
| A6 | The Pydantic v2 `model_json_schema() → genai types.Schema` auto-helper is NOT built; each `_build_*_tool()` factory hand-writes `types.Schema` to match its Pydantic model | TOOL-06 | If auto-helper preferred: net-new ~30-line library function with matching test coverage. |
| A7 | Plans land in `.planning/phases/04-.../04-NN-PLAN.md` (Phase 3 convention), NOT `.agent/plans/` (CLAUDE.md text) | Project Constraints | Phase 1–3 used the phases/ convention; CLAUDE.md text predates it. Confirm with operator. |
| A8 | `literal_hint` arg is auto-extracted from regex by the Python grep wrapper, not added to TOOL-03's args schema | Tool Block C | If LLM should pass hint explicitly: add `literal_hint: Optional[str]` to GrepArgs. |
| A9 | Phase 4 does NOT extend Storage RLS or add a new bucket — all tools are read-only metadata operations | Architectural Responsibility Map | Confirmed by reading the spec; no Storage interaction in any of the 5 tools. |
| A10 | `read_document` 12K cap is char-based and applied AFTER arrow-form rendering | Tool Block E | If cap should be applied to source content BEFORE rendering: changes the truncation marker calculation. |

**These assumptions need user confirmation before any locked decision in PLAN.md.**

---

## Open Questions / Risks for the Planner

1. **Tool args schema export to Gemini SDK — manual vs. auto?**
   - What we know: existing `_build_search_tool()` hand-writes `types.Schema`; loop-over-`metadata_schema` style.
   - What's unclear: whether to introduce a `pydantic_to_genai_schema(BaseModel) -> types.Schema` helper in Phase 4 to avoid 5 hand-written factories.
   - Recommendation: hand-write each `_build_*_tool()` factory in Phase 4 (5 small functions; review surface stays localized; matches the existing Episode 1 pattern). Add the auto-helper as a Phase 5 cleanup if 5 hand-writes feel painful.

2. **Where does grep's "literal substring" come from?**
   - What we know: ILIKE pre-filter is needed for the GIN trigram index to fire.
   - What's unclear: should the LLM pass it explicitly (+1 arg in GrepArgs) or should the Python wrapper auto-extract from the regex?
   - Recommendation: auto-extract first; if regex has no extractable literal of length ≥ 3, accept seq-scan fallback (the path-prefix bound limits damage). Add explicit `literal_hint` arg in v2 if telemetry shows the LLM consistently chooses regexes that don't auto-extract.

3. **`folder_service.list_folder()` subfolder scope projection — extend now or re-derive?**
   - What we know: `subfolders` is `list[str]` today (Phase 3 / Plan 02).
   - What's unclear: extending now is mildly breaking but saves a query in tree/list_files; re-deriving is non-breaking but adds Python work per call.
   - Recommendation: re-derive in Phase 4 wrappers (option B in Tool Block D); revisit in Phase 6 if UI rendering needs `(path, scope)` tuples.

4. **Should `read_document` accept `path` (folder + file_name) OR only `document_id`?**
   - What we know: spec says "args document_id OR path".
   - What's unclear: regex pattern for the `path` arg in `ReadDocumentArgs` (must include a file_name segment, unlike other tools' `path` which terminates at a folder).
   - Recommendation: define a separate regex `^/$|^/[^/]+(/[^/]+)*/[^/]+$` (must end in a non-slash segment after at least one folder) for ReadDocumentArgs.path. Different pattern, different semantic — clearly documented at the schema.

5. **Tree truncation algorithm — top-down counter or bottom-up summary?**
   - What we know: 500-entry cap + max_depth cap + per-level summaries.
   - What's unclear: should the planner choose a recursive-with-budget or iterative-BFS approach?
   - Recommendation: iterative BFS with a running `entries_remaining` counter; cleaner shutdown when the budget hits zero; easier to test (TEST-02 200-folder fixture).

6. **Should TOOL-03 grep's `path` arg accept the full file path (folder + file_name) for "this single document" semantics?**
   - What we know: spec says `path` is a folder prefix.
   - What's unclear: do we want a `document_id` filter on grep too?
   - Recommendation: NO — that's `read_document`'s job. Keep grep folder-scoped for clean tool boundaries.

7. **Migration 020 numbering vs. Phase 3 / Migration 019 — confirm next sequential number is 020.**
   - What we know: Phase 3 / Plan 01 added Migration 019 (`rename_folder_prefix`, `delete_folder_if_empty`, `create_folder_if_not_exists`).
   - What's unclear: nothing — straightforward sequential numbering. Documenting for the planner's checklist.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python venv (`backend/venv`) | All Phase 4 code | ✓ (assumed — Phase 1–3 used it) | 3.x (project unspecified pinning) | — |
| `google-genai` SDK | LLM tool dispatch | ✓ | unpinned (Episode 1 lock) | — |
| `langsmith` SDK | TOOL-10 `@traceable` | ✓ | unpinned | — |
| `pydantic` v2 | TOOL-06 schemas | ✓ | unpinned (FastAPI ≥ 0.100 pulls v2) | If v1: rewrite Field/Literal usage; flag operator |
| `psycopg2` | TEST-02 EXPLAIN assertion | ✓ (present in test_folders.py:310) | unpinned | Skip EXPLAIN test gracefully (mirror test_folders.py:307) |
| Postgres `pg_trgm` extension | grep | ✓ | enabled in Migration 012 | — |
| Postgres `LATERAL` + `regexp_split_to_table` + `WITH ORDINALITY` | grep RPC | ✓ | Postgres 12+ (Supabase ≥ 14) | — |
| Supabase service-role client | TEST-02 fixtures (bulk insert) | ✓ | — | — |
| Backend on `localhost:8001` | TEST-02 dispatch tests | ✓ (operator pre-req — Phase 3 canary precedent) | — | Skip dispatch tests; canary fails fast |
| Migration 020 applied | grep RPC + extended hybrid/filters RPCs | ❌ Wave 0 | — | Block: Wave 0 must apply Migration 020 first |

**Missing dependencies with no fallback:** Migration 020 must ship in Wave 0 before any tool function is testable end-to-end.

**Missing dependencies with fallback:** `DATABASE_URL` env var for `psycopg2` EXPLAIN test — gracefully SKIP per Phase 3 idiom.

---

## Validation Architecture (Nyquist Dimensions)

> This section is parsed verbatim into `04-VALIDATION.md`. Mirror Phase 3 / `03-VALIDATION.md` shape.

### Test Framework

| Property | Value |
|----------|-------|
| Framework | Custom Python test suite (`test_helpers.py` + `test_all.py`) |
| Config file | `backend/scripts/test_all.py` SUITES list |
| Quick run command | `cd backend && venv/Scripts/python scripts/test_exploration_tools.py` |
| Full suite command | `cd backend && venv/Scripts/python scripts/test_all.py` |
| Estimated runtime | ~60 sec single-suite (5000-doc fixture is the long pole); ~4 min full suite |

**Pre-reqs:** Backend on `localhost:8001` (Phase 3 canary discipline); `.env` with `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `GEMINI_API_KEY`; Migration 020 applied; admin@test.com promoted; `documents` Storage bucket exists (Phase 2 carry-forward).

### Sampling Rate

- **Per task commit:** `cd backend && venv/Scripts/python scripts/test_exploration_tools.py` (single-suite, ~60s warm)
- **Per wave merge:** Same single-suite (full suite is the phase gate)
- **Phase gate:** Full suite green via `cd backend && venv/Scripts/python scripts/test_all.py` (16 suites: existing 15 + Exploration)
- **Max feedback latency:** ~60 seconds per task

### Per-Dimension Validation Map

| Dim | Concern | What's Checked | Where in `test_exploration_tools.py` |
|-----|---------|----------------|--------------------------------------|
| **1: Signature** | All five tool functions exist + extended search_documents | `from app.services.exploration_tools import tree, glob_match, grep, list_files, read_document; assert callable(...)`; `_build_search_tool()` includes `folder_path` + `scope` properties | `[Tool surface]` section |
| **2: Token-budget contracts** | 12K cap + truncation marker; tree max_depth cap; grep 50-hit cap; read_document 1-based offset / 5000-line hard cap | Adversarial fixtures + JSON char-count assertions; `Field(le=...)` clamping; mock-LLM args with absurd numbers | `[TOOL-01 truncation]` + `[TOOL-03 50-hit cap]` + `[TOOL-05 line bounds]` |
| **3: RLS scope-tag invariant** | TOOL-07: every result row carries `scope` ∈ {'user','global'} | Walk every tool's result dict, assert `'scope' in row` for each entry/hit/match; cross-user fixture verifies user B can't see user A's user-scope rows in any tool | `[TOOL-07 scope tag]` + `[Cross-user isolation]` |
| **4: Perf** | grep < 500ms p95 over 5000-doc fixture; tree < 12K chars over 200-folder fixture; EXPLAIN shows Bitmap Index Scan | 10-iteration latency timing; EXPLAIN ANALYZE assertion via psycopg2 (gracefully SKIP without DATABASE_URL) | `[TOOL-03 perf]` + `[TOOL-01 char budget]` |
| **5: Empty-response (Pitfall 8 / TOOL-09)** | 50K-char adversarial result flows through layered-fallback; assistant message non-empty | Adversarial doc with 50K-char `content_markdown` → grep against it → SSE stream collected → `len(full_text) > 0` + `has_done_event` | `[TOOL-09 empty-response guard]` |
| **6: SDK contract (TOOL-06 + TOOL-10)** | `@traceable` on every tool fn; Pydantic v2 strict (`extra='ignore'`); scope is `Literal` | `inspect.getattr_static(tree, '__wrapped__')` for `@traceable` presence; `TreeArgs(**{'scope':'invalid'})` raises ValidationError | `[TOOL-10 tracing]` + `[TOOL-06 strict args]` |
| **7: Regression** | Existing search_documents callers unaffected by NULL-default extension | Run a search via the existing dispatch with NO `folder_path`/`scope` args → verify same chunk count + same chunk IDs as a snapshot | `[SEARCH-01 backward compat]` |
| **8: Cross-cutting** | Build order obeyed (list_files → tree → glob → read_document → grep); shared schemas module imports cleanly | Static check: `from app.services.exploration_tools.schemas import TreeArgs, GlobArgs, GrepArgs, ListFilesArgs, ReadDocumentArgs` — all five importable; circular-import probe | `[Phase 4 module import smoke]` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists |
|--------|----------|-----------|-------------------|-------------|
| TOOL-01 | tree() returns nested + summarizes | unit + integration | `cd backend && venv/Scripts/python scripts/test_exploration_tools.py` ([TOOL-01]) | ❌ Wave 0 |
| TOOL-02 | glob() with `**`/`*` matches expected docs | unit + integration | same ([TOOL-02]) | ❌ Wave 0 |
| TOOL-03 | grep() returns ≤50 hits with ±2 context; EXPLAIN shows Bitmap | integration + EXPLAIN assertion | same ([TOOL-03]) | ❌ Wave 0 |
| TOOL-04 | list_files() folders-then-files-alpha | unit | same ([TOOL-04]) | ❌ Wave 0 |
| TOOL-05 | read_document() arrow-form, CRLF/Unicode/long-line stable | unit + adversarial fixtures | same ([TOOL-05 fixtures]) | ❌ Wave 0 |
| TOOL-06 | Pydantic args reject invalid scope/path/numeric bounds | unit | same ([TOOL-06]) | ❌ Wave 0 |
| TOOL-07 | Every row carries scope | integration walk-through | same ([TOOL-07]) | ❌ Wave 0 |
| TOOL-08 | 12K cap + truncation marker | integration | same ([TOOL-08]) | ❌ Wave 0 |
| TOOL-09 | 50K-char adversarial → non-empty SSE | integration via real dispatch | same ([TOOL-09 empty-response]) | ❌ Wave 0 |
| TOOL-10 | `@traceable` on every fn | static introspection | same ([TOOL-10]) | ❌ Wave 0 |
| SEARCH-01 | search_documents accepts folder_path + scope; defaults preserve behavior | integration via dispatch | same ([SEARCH-01]) | ❌ Wave 0 |
| SEARCH-02 | RPCs accept new params with NULL defaults; existing call shape works | direct RPC + dispatch | same ([SEARCH-02]) | ❌ Wave 0 |
| SEARCH-03 | system prompt insertion verified by string-contains on `_build_system_prompt()` output | unit | same ([SEARCH-03]) | ❌ Wave 0 |
| TEST-02 | test_exploration_tools.py registered as 16th suite in test_all.py | smoke (test_all runs) | `cd backend && venv/Scripts/python scripts/test_all.py` | ❌ Wave 0 |

### Wave 0 Gaps

- [ ] `backend/migrations/020_phase4_rpcs.sql` — grep_documents RPC + extend match_document_chunks_with_filters + match_document_chunks_hybrid (SEARCH-02)
- [ ] `backend/app/services/exploration_tools/__init__.py` + `schemas.py` (TOOL-06 shared module)
- [ ] `backend/app/services/exploration_tools/_truncate.py` (TOOL-08 helper)
- [ ] `backend/scripts/test_exploration_tools.py` — covers TOOL-01..10 + SEARCH-01..03 + TEST-02
- [ ] Migration 020 applied via `cd backend && venv/Scripts/python scripts/run_migrations.py` BEFORE running test_exploration_tools.py
- [ ] `test_all.py` SUITES list — append `("Exploration", test_exploration_tools)` after `("Folders", test_folders)`

---

## Security Domain

> security_enforcement is enabled by default in this codebase (CLAUDE.md mandates RLS on every table). Phase 4 is read-only metadata + content access; the surface area is narrower than Phase 3 (no admin gates, no mutations) but still touches all the same RLS rails.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes (inherited) | JWT via `get_current_user` dependency on `messages.py`; tools never authenticate independently |
| V3 Session Management | yes (inherited) | Supabase Auth session + JWT — same as Phases 1–3 |
| V4 Access Control | YES — critical | Two-scope RLS (Migration 015) is the bedrock; tools' `scope` arg narrows on top of RLS, never replaces it. App-layer defense-in-depth: explicit `_assert_uuid()` on user_id before PostgREST `or_()` interpolation (HI-01). |
| V5 Input Validation | YES — critical | Pydantic v2 `Field(pattern=)` for paths; `Literal` for scope; `Field(ge=, le=)` for numeric bounds; `extra='ignore'` for smuggling-drop (TOOL-06) |
| V6 Cryptography | no | No new cryptographic surfaces; LLM API keys / Supabase keys inherited |

### Known Threat Patterns for Phase 4

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Prompt-injected `path` arg traversal (`../../etc/passwd`) | Tampering | `normalize_path()` rejects `..` and `.` segments; `_PATH_RE` regex enforced by Pydantic AND DB CHECK |
| Prompt-injected `user_id` smuggling (LLM passes another user's ID) | Spoofing / Elevation | Tools NEVER accept `user_id` as an arg — derived from JWT in the dispatch loop (Episode 1 invariant; Pitfall 11 reinforces) |
| PostgREST `or_()` filter injection via crafted user_id | Elevation | `_assert_uuid()` on user_id before f-string interpolation (HI-01 from Phase 3 / Plan 02) |
| Pathological regex DoS (Pitfall 3) | DoS | Python pre-screen + blocklist; Postgres `SET LOCAL statement_timeout = '5s'` per RPC |
| Tree context blow-up (Pitfall 2) | DoS | server-side `max_depth` cap; 500-entry hard cap; 12K char cap |
| 50K-char tool result (Pitfall 8 recurrence) | DoS / Repudiation | Layered fallback at `openai_client.py:565-610`; non-empty assistant message gate at `messages.py:111-122` |
| Cross-scope leak (Pitfall 1 reinforcement) | Information Disclosure | Two-scope RLS does the gating; tool args' `scope` is *narrowing*, not the access decision |
| Scope confusion in citations (Pitfall 11) | Repudiation | TOOL-07 invariant: every row carries `scope`; SEARCH-03 system prompt instructs LLM to disambiguate |

### Zero-Trust Reminders

- **Tools ALWAYS receive `user_id` from `get_current_user`-derived JWT, never from LLM args** — same rule as Episode 1, extended.
- **`folder_path` and `path` args ALWAYS pass through `normalize_path()`** — even if Pydantic regex matches; defense-in-depth.
- **Service-role client usage is forbidden in any of the 5 tool functions** — they take the JWT-bound supabase_client passed by `stream_response`. RLS applies. (The TEST-02 fixtures use service-role for bulk insert; tools themselves never do.)

---

## Sources

### Primary (HIGH confidence)

- `backend/app/services/openai_client.py:1-670` — verbatim `stream_response`, `_build_*_tool` factories, layered-fallback wrapper at lines 565-610
- `backend/app/routers/messages.py:1-125` — verbatim `event_generator` SSE plumbing
- `backend/app/services/folder_service.py:1-446` — verbatim `normalize_path`, `_assert_uuid`, `_escape_like`, `list_folder` UNION pattern
- `backend/migrations/012_folder_path_and_scope.sql` — `pg_trgm` enabled; `folder_path` CHECK regex
- `backend/migrations/014_content_markdown_column.sql` — content_markdown column + status enum
- `backend/migrations/016_search_indexes.sql:35-61` — `documents_content_markdown_trgm_idx`, `documents_folder_path_prefix_idx`, `documents_folder_path_trgm_idx`
- `backend/migrations/007_document_metadata.sql:36-56` — `match_document_chunks_with_filters` current signature
- `backend/migrations/011_improved_keyword_search.sql:6-55` — `match_document_chunks_hybrid` current signature
- `backend/scripts/test_folders.py` — verbatim Phase 3 test patterns Phase 4 mirrors
- `backend/scripts/test_helpers.py` — `h.section`, `h.test`, `get_user_supabase_client`, `stream_sse`
- `backend/scripts/test_all.py` — SUITES list registration pattern
- `.planning/STATE.md` — accumulated decisions ledger (Phases 1–3 LOCKED conventions)
- `.planning/REQUIREMENTS.md:42-57+85` — TOOL-01..10, SEARCH-01..03, TEST-02 specifications
- `.planning/ROADMAP.md:101-112` — Phase 4 success criteria + threats/pitfalls
- `.planning/research/PITFALLS.md` — Pitfalls 2, 3, 8, 9, 11 (Phase 4 references)
- `.planning/research/ARCHITECTURE.md` — system overview + Pattern 4 (additive dispatch) + Pattern 5 (token-budget truncation)
- `.planning/phases/02-content-markdown-backfill-gated/02-CONTEXT.md:60-73` — LOCKED `pending_reindex` tool integration contract
- `.planning/phases/03-folder-service-routers-dedup-extension/03-VALIDATION.md` — Validation Architecture template Phase 4 mirrors
- `CLAUDE.md` — project rules + test cleanup discipline + planning conventions

### Secondary (MEDIUM confidence — derived/inferred)

- `pydantic` v2 `Field(pattern=, ge=, le=)` semantics — inferred from FastAPI ≥ 0.100 → pydantic v2; verified by Phase 3 `extra='ignore'` LOCKED usage
- `LATERAL regexp_split_to_table(... , E'\n') WITH ORDINALITY AS lines(line, line_no)` — Postgres-standard idiom for line-resolved regex; verified at `https://www.postgresql.org/docs/current/queries-table-expressions.html#QUERIES-LATERAL`
- `SET LOCAL statement_timeout` scoped to PostgREST's per-`.execute()` transaction — Postgres-standard; cross-checked against `https://www.postgresql.org/docs/current/sql-set.html`

### Tertiary (LOW confidence — flagged for validation)

- The exact byte length of the `→` arrow-form character (3 bytes UTF-8: 0xE2 0x86 0x92) — needs fixture-side byte-count assertion to lock
- The "MAX_CALLS=8" / 500-entry / 12K-char numbers' exact rationale across REQUIREMENTS vs. ROADMAP vs. PITFALLS — three documents agree on the SHAPE but differ slightly on numbers; planner should pick the lower bound when conflict occurs

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — every package present in `requirements.txt`; conventions documented in 4+ codebase sites
- Architecture: HIGH — every `elif` arm and dispatch hook verified at openai_client.py exact line numbers
- Pitfalls: HIGH — 10-pitfall research already exists in `.planning/research/PITFALLS.md`; Phase 4 maps cleanly to 5 of them
- Test infra: HIGH — Phase 3 / Plan 06 just locked the test patterns Phase 4 reuses verbatim
- Migration 020 shape: MEDIUM — extending two existing RPCs is well-precedented; the new grep_documents RPC is novel but every primitive (LATERAL, ILIKE, SET LOCAL) is Postgres-standard
- TOOL-09 layered-fallback: HIGH — pattern is inline at `openai_client.py:565-610`; "compliance" = use the same `result_text` variable, no parallel context injection

**Research date:** 2026-05-08
**Valid until:** 2026-06-08 (estimate — 30 days; phase moves quickly but the upstream contracts are stable)

---

## RESEARCH COMPLETE

Phase 4 = additive plumbing: 5 tools + 1 search-extension routed through existing Phase 1–3 primitives (`normalize_path`, two-scope RLS, GIN trigram index, `or_()` UNION, layered-fallback wrapper, `@traceable`, scoped-cleanup test discipline) — net-new building blocks limited to (a) Pydantic v2 args module, (b) 12K cap helper, (c) Migration 020 grep_documents RPC, (d) read_document Python line slicer.
