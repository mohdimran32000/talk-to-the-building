---
phase: 04-five-exploration-tools-search-documents-extension
plan: 05
subsystem: api
tags: [gemini-tools, langsmith-traceable, dispatch-routing, scope-tag, truncation, pydantic-v2, folder-service, glob-to-regex, like-prefilter, hi-01-uuid-guard, hi-03-like-escape]

# Dependency graph
requires:
  - phase: 04-five-exploration-tools-search-documents-extension
    plan: 02
    provides: GlobArgs (Pydantic v2 schema with pattern min/max length, path regex, type+scope literals, extra=ignore), apply_12k_cap (TOOL-08), ensure_scope_tag (TOOL-07)
  - phase: 04-five-exploration-tools-search-documents-extension
    plan: 03
    provides: locked Phase 4 dispatch-arm shape (lazy import + Pydantic try/except + result_text = json.dumps), _build_list_files_tool registration position inside `if has_documents:` block
  - phase: 04-five-exploration-tools-search-documents-extension
    plan: 04
    provides: tree dispatch arm anchor for the AFTER position; _build_tree_tool factory adjacency anchor; precedent for type=INTEGER/STRING enum schema fields
  - phase: 03-folder-service-routers-and-dedup-extension
    provides: folder_service._escape_like (HI-03 LIKE-wildcard escape), folder_service._assert_uuid (HI-01 UUID defense), folder_service.normalize_path (Pitfall 4 chokepoint)
  - phase: 01-two-scope-foundation
    provides: Migration 012 canonical-form CHECK on documents.folder_path (third leg of triple chokepoint); Migration 016 documents_folder_path_prefix_idx + documents_folder_path_trgm_idx (acceleration for the LIKE-prefilter + regex two-stage filter)
provides:
  - app.services.exploration_tools.glob_match.glob_match — TOOL-02 glob/regex tool with `**`/`*` semantics, type=file/folder/both branches, two-stage LIKE-prefix-then-regex filter, scope-tagged rows, 500-entry hard cap, 12K char cap, structured error envelopes (INVALID_PATH / INVALID_USER_ID / INVALID_PATTERN)
  - app.services.exploration_tools.glob_match._glob_to_regex — pure-Python helper translating `**` → `.*`, `*` → `[^/]*`, escaping regex metacharacters in literal segments, anchoring at the canonical prefix
  - app.services.openai_client._build_glob_tool — Gemini FunctionDeclaration factory (pattern + path + type + scope props; required=['pattern']; type.enum=['file','folder','both']; scope.enum=['user','global','both'])
  - app.services.openai_client dispatch arm `elif tool_name == "glob"` — TOOL-09 routing into the existing layered-fallback wrapper (UNCHANGED at the truncated_result = result_text[:16000] site)
affects: [04-06 (read_document — same dispatch-arm template), 04-07 (grep — also ships pattern matching but in document content; will reuse the LIKE-prefilter pattern), 04-08 (search_documents extension), 04-09 (test_exploration_tools — exercises glob end-to-end with %/_ folder names + cross-scope isolation)]

# Tech tracking
tech-stack:
  added: []  # No new deps — reuses langsmith.traceable, pydantic v2, supabase-py, re/stdlib
  patterns:
    - "Glob → Postgres regex translation in pure Python: `**` → `.*` (cross-slash), `*` → `[^/]*` (within-segment), `re.escape` per literal char. Anchored at the canonical path prefix; `^/?` makes the leading slash optional only at root for UNIX-glob friendliness"
    - "Two-stage filter pattern: PostgREST `.like()` prefix prefilter (HI-03 _escape_like on the literal segment) bounds the candidate set inside the database (exploits Migration 016 documents_folder_path_prefix_idx); then a Python `re.fullmatch` applies the precise regex on the LIMIT-bounded slice. Acceptable because LIMIT is _MATCH_HARD_CAP * 2 = 1000 worst-case rows, not the full corpus"
    - "type=file/folder/both branching: file branch queries documents only; folder branch queries explicit folders side table + inferred-from-documents UNION (mirrors folder_service.list_folder shape); both branch unions both with seen-set dedup"
    - "Mirrors Plans 03/04 dispatch-arm shape exactly: lazy `from app.services.exploration_tools.glob_match import glob_match as _glob` + `from app.services.exploration_tools.schemas import GlobArgs` inside the elif arm; try/except parses GlobArgs and yields INVALID_ARGS envelope on ValidationError; else branch calls _glob() and assigns result_text = json.dumps(tool_result)"
    - "Cumulative match cap _MATCH_HARD_CAP = 500 applied via Python-side `[: _MATCH_HARD_CAP - len(matches)]` slicing across both branches — ensures the folder branch never inflates the result past the cap when the file branch already filled it"
    - "Module identifier `glob_match` (not `glob`) to avoid shadowing the stdlib `glob` module that other code may import; LLM-facing tool name is still `glob` set via the @traceable name= kwarg AND the FunctionDeclaration name= AND the elif arm string match"

key-files:
  created:
    - backend/app/services/exploration_tools/glob_match.py
  modified:
    - backend/app/services/openai_client.py

key-decisions:
  - "Module name `glob_match` not `glob`. The stdlib has a `glob` module; importing `from app.services.exploration_tools.glob import glob` would shadow that for any caller in the package. Naming the module `glob_match` and exporting the function as `glob_match` keeps the import line unambiguous (`from app.services.exploration_tools.glob_match import glob_match`). LLM-facing tool name is still `glob` (set via @traceable name=, FunctionDeclaration name=, and the elif tool_name match)."
  - "Glob → regex translation is pure Python (no regex library beyond stdlib `re`). The translation is small and stable; pulling in a third-party globber (e.g., wcmatch) would be over-engineering for the locked semantics (just `*` and `**`)."
  - "Two-stage filter (LIKE prefix prefilter in DB, then Python re.fullmatch on the bounded slice). Pure database-side regex on `folder_path || '/' || file_name` would require either a generated column or a SQL function. For an MVP the LIKE prefilter is enough to bound the candidate set to <= _MATCH_HARD_CAP * 2 rows, and the Python regex is fast on that slice. Plan 09 will measure the worst-case latency on the 200-folder fixture; if it's slow we can add a generated `full_path` column in a follow-up plan."
  - "Anchor `^/?` at root specifically. When `args.path == '/'`, the matched surface might or might not have a leading slash (a doc at `folder_path='/' file_name='foo.pdf'` has full path `/foo.pdf` per `_full_path`). The `^/?` makes the slash optional so a bare pattern `*.pdf` matches `/foo.pdf` (and conceptually `foo.pdf`); a pattern `**/*.pdf` matches `/sub/foo.pdf` and similar. When `args.path != '/'`, the prefix is escaped and a literal `/` separates it from the glob regex."
  - "Per-branch hard-cap slice `[: _MATCH_HARD_CAP - len(matches)]`. Without this, the folder branch could push the result past 500 entries even though the file branch already filled it. Python-side slicing is intentional — happens AFTER the database returns its LIMIT-bounded rows, so it's free."
  - "Error envelope shape mirrors Plans 03/04. INVALID_PATH (normalize_path ValueError), INVALID_USER_ID (HI-01 _assert_uuid ValueError), INVALID_PATTERN (_glob_to_regex ValueError on empty pattern). Error path skips apply_12k_cap (small dicts; asymmetry signals 'this is an error envelope' to the LLM)."
  - "Both file-branch and folder-branch failures are caught with `except Exception` + logger.warning, NOT re-raised. This keeps a partial result available — if the file query succeeds and the folder query fails, the LLM still sees the file matches with a logged backend warning. Mirrors tree's per-subfolder SUBQUERY_FAILED handling philosophy."
  - "@traceable adds a `config` kwarg to the wrapped signature. Smoke-test signature assertion uses `params[:3] == ['args','user_id','supabase_client'] AND 'config' in params` (same convention as Plans 03/04)."

patterns-established:
  - "Pattern: glob-to-regex translation in pure Python with double-star precedence (peek-next-char). Reusable for grep (Plan 07) if grep ever needs glob-style include/exclude pattern args."
  - "Pattern: two-stage filter (DB LIKE prefilter + Python regex on bounded slice). Reusable for Plan 07 (grep) when content-side regex needs a similar structure (folder-prefix narrowing in SQL, then per-doc grep in Python or via a SQL function call)."
  - "Pattern (REINFORCED): per-branch hard-cap slicing with cumulative budget (`[: BUDGET - len(accum)]`). Same idiom as tree's per-level entries_remaining counter, but applied across two independent branches instead of BFS levels."

requirements-completed: [TOOL-02, TOOL-06, TOOL-07, TOOL-08, TOOL-09, TOOL-10]

# Metrics
duration: ~10min
completed: 2026-05-09
---

# Phase 4 Plan 05: glob (TOOL-02) Summary

**TOOL-02 glob exploration tool — @traceable wrapper translating LLM-friendly `*` / `**` patterns into a Postgres regex with a two-stage LIKE-prefilter + Python regex filter, type=file/folder/both branches, HI-01 UUID guard + HI-03 LIKE-escape defenses, scope-tagged rows, 500-entry hard cap, 12K char cap, and additive openai_client dispatch wiring; mirrors the locked Plan 03/04 template.**

## Performance

- **Duration:** ~10 min
- **Started:** 2026-05-09 (worktree wave 4)
- **Completed:** 2026-05-09
- **Tasks:** 2
- **Files created:** 1 (glob_match.py — 335 lines)
- **Files modified:** 1 (openai_client.py — +77 lines additive; now 884 lines, was 807 after Plan 04)
- **LOC delta:** +412

## Accomplishments

- TOOL-02 glob_match() function landed: pure-Python `_glob_to_regex` helper translates `**` → `.*` and `*` → `[^/]*` with regex-metacharacter escaping in literal segments and prefix anchoring; type=file/folder/both branches push different table queries; two-stage filter (PostgREST `.like()` prefix prefilter inside the database + Python `re.fullmatch` on the LIMIT-bounded slice) keeps the corpus from streaming to Python; per-branch cumulative hard-cap slicing keeps total matches at or under _MATCH_HARD_CAP = 500; structured error envelopes (INVALID_PATH, INVALID_USER_ID, INVALID_PATTERN) on caught exceptions.
- Six cross-cutting Phase 4 concerns honored: normalize_path chokepoint as the FIRST function statement (Pitfall 4), @traceable tracing (TOOL-10 with name='glob'), ensure_scope_tag invariant on every entry (TOOL-07), apply_12k_cap on the result (TOOL-08), no-Gemini-SDK + no-HTTPException (TOOL-09 routing contract), Pydantic v2 args via GlobArgs (TOOL-06).
- Two Phase 3 inherited defenses applied: `folder_service._assert_uuid(user_id, 'user_id')` gated when scope ∈ ('user','both') BEFORE any PostgREST `.or_()` interpolation (HI-01); `folder_service._escape_like(norm_prefix)` on every LIKE predicate that interpolates the literal path prefix (HI-03 — `%`/`_` in folder names cannot become wildcards).
- openai_client.py extended additively with three localized edits: (1) `_build_glob_tool()` factory inserted between `_build_tree_tool()` and `_sanitize_keyword_query` with FunctionDeclaration name='glob', properties={pattern, path, type, scope}, type.enum=['file','folder','both'], scope.enum=['user','global','both'], required=['pattern']; (2) registration `function_declarations.append(_build_glob_tool())` inside the `if has_documents:` block AFTER the `_build_tree_tool()` registration, wrapped in try/except logger.warning; (3) `elif tool_name == "glob":` dispatch arm AFTER the tree arm and BEFORE the unknown-tool fall-through, with lazy imports of glob_match (aliased _glob) + GlobArgs, try/except Pydantic parse → INVALID_ARGS envelope on ValidationError, else branch calls _glob() and assigns result_text = json.dumps(tool_result), both branches yield tool_done with `{total_matches} matches for {pattern!r}` detail string.
- Wrapper at openai_client.py (the `truncated_result = result_text[:16000]` site, two occurrences — one in the streaming Call#2 path, one in the non-streaming fallback) UNCHANGED — verified post-edit count == 2. Existing 6 factories and 6 dispatch arms (search_documents, analyze_document, query_structured_data, web_search, list_files, tree) UNCHANGED — verified each present exactly once.
- All registered tools after this plan: search_documents, analyze_document, query_structured_data, web_search, list_files, tree, glob (7 total — adding glob to Plans 03+04's 6).
- Plan 06 (read_document) ready in Wave 5 under the same locked template.

## Task Commits

Each task committed atomically with --no-verify (parallel-wave executor inside worktree):

1. **Task 1: glob_match.py — TOOL-02 + glob→regex helper + cross-cutting concerns** — `88b1e58` (feat)
2. **Task 2: openai_client.py extension — _build_glob_tool factory + registration + dispatch arm** — `08873b2` (feat)

## Files Created/Modified

- `backend/app/services/exploration_tools/glob_match.py` (NEW, 335 lines) — public `glob_match(args, user_id, supabase_client)` function decorated with `@traceable(name="glob", run_type="tool")`; FIRST STATEMENT calls `normalize_path(args.path)` (Pitfall 4 chokepoint); HI-01 `_assert_uuid(user_id, 'user_id')` gated when `args.scope in ('user','both')`; `_glob_to_regex(args.pattern, norm_prefix)` translates `**` → `.*` (peek-next-char before single `*`) and `*` → `[^/]*` with `re.escape` on literal chars and anchoring (`^/?` at root or `^{escaped_prefix}/` otherwise, `$` always); type=file branch calls `_query_documents` (PostgREST `.or_(folder_path.eq.<norm>,folder_path.like.<esc>/%)` prefix prefilter via HI-03 `_escape_like`, scope narrowing via `.eq()`/`.is_()` or `.or_()` based on scope arg, LIMIT _MATCH_HARD_CAP*2 = 1000, then Python `re.fullmatch` on `folder_path/file_name`); type=folder branch calls `_query_folders` (UNION of explicit folders side table + inferred-from-documents folder paths, both LIKE-prefiltered + Python regex-matched, deduped via seen-set); per-branch slicing `[: _MATCH_HARD_CAP - len(matches)]` enforces cumulative cap; every match wrapped in `ensure_scope_tag(entry, default=...)`; result wrapped in `apply_12k_cap`; structured error envelopes INVALID_PATH (normalize_path ValueError), INVALID_USER_ID (HI-01 ValueError), INVALID_PATTERN (_glob_to_regex empty-pattern ValueError); per-branch query exceptions caught with `except Exception` + `logger.warning` (do NOT re-raise — partial result preserved); helpers `_glob_to_regex`, `_query_documents`, `_query_folders`, `_full_path` are module-level for testability.
- `backend/app/services/openai_client.py` (MODIFIED, +77 lines additive — file now 884 lines, was 807 after Plan 04) — three localized edit points:
  - **Edit 1** (~line 264): `_build_glob_tool()` factory inserted between `_build_tree_tool()` and `_sanitize_keyword_query`. types.FunctionDeclaration with name='glob'; description guides the LLM to choose glob vs tree vs grep; properties = {pattern: STRING, path: STRING, type: STRING enum=['file','folder','both'], scope: STRING enum=['user','global','both']}; required=['pattern'].
  - **Edit 2** (~line 432): registration `try: function_declarations.append(_build_glob_tool()); except Exception as e: logger.warning(...)` inserted inside the `if has_documents:` block AFTER `_build_tree_tool()` registration, BEFORE `if text_to_sql_enabled:` block.
  - **Edit 3** (~line 698): `elif tool_name == "glob":` dispatch arm inserted AFTER the `elif tool_name == "tree":` arm, BEFORE the `else: logger.warning(f"Unknown tool: {tool_name}")` fallthrough. Lazy imports `glob_match as _glob` and `GlobArgs`; try/except parses GlobArgs and on ValidationError assigns result_text = json.dumps({tool, error: INVALID_ARGS, message}) and yields tool_done(detail="Invalid arguments"); else branch calls `_glob(parsed_args, user_id, supabase_client)`, assigns result_text = json.dumps(tool_result), reads total_matches (with isinstance dict guard), yields tool_done(detail=f"{tm} matches for {parsed_args.pattern!r}").
  - Wrapper unchanged: `truncated_result = result_text[:16000]` still appears twice (verified by smoke test count assertion).

## Public APIs Established (consumed by Plan 09 + future tools)

**`app.services.exploration_tools.glob_match`:**
- `glob_match(args: GlobArgs, user_id: Optional[str], supabase_client) -> dict` — happy-path returns `{tool: 'glob', scope_arg, pattern, path_prefix, matches: [...], total_matches, truncation_marker}`. Each match is `{type: 'doc', document_id, file_name, folder_path, scope}` or `{type: 'folder', path, scope}`; every match carries `scope ∈ {'user','global'}`. Error-path returns `{tool: 'glob', error: 'INVALID_PATH'|'INVALID_USER_ID'|'INVALID_PATTERN', message}`.
- `_glob_to_regex(pattern: str, anchor_path: str) -> str` — pure-function helper translating glob to regex; module-level for unit testing. Plan 09 will exercise the documented translations: `*.pdf` → `^/?[^/]*\.pdf$`, `**/*.pdf` → `^/?.*/[^/]*\.pdf$`, `foo` at `/projects` → `^/projects/foo$`.
- Module-level constant `_MATCH_HARD_CAP = 500` (mirrors tree's _ENTRY_BUDGET; monkey-patchable for tests).

**`app.services.openai_client`:**
- `_build_glob_tool() -> types.FunctionDeclaration` — returned object exposes `name='glob'`, `parameters.type=Type.OBJECT`, `parameters.properties={'pattern','path','type','scope'}`, `parameters.properties['type'].enum=['file','folder','both']`, `parameters.properties['scope'].enum=['user','global','both']`, `parameters.required=['pattern']`.
- New dispatch arm: when fc.name == 'glob', the arm parses args via GlobArgs(**args), calls glob_match(), assigns result_text = json.dumps(tool_result), yields ('tool_done', detail with match count and pattern).

## Decisions Made

See key-decisions in frontmatter. Highlights:
- **Module name `glob_match`, LLM tool name `glob`** — avoids shadowing the stdlib `glob` module while keeping the LLM-facing API natural.
- **Pure-Python glob → regex translation** — small, stable, no third-party globber needed for the locked `*`/`**` semantics.
- **Two-stage filter (DB LIKE prefilter + Python regex on bounded slice)** — pragmatic MVP; pure database-side regex on the concatenated `folder_path/file_name` would require a generated column or SQL function (deferred unless Plan 09 latency measurements demand it).
- **Per-branch cumulative slicing `[: _MATCH_HARD_CAP - len(matches)]`** — enforces the 500-entry cap across both file and folder branches without one branch starving the other.
- **Per-branch query failures DO NOT bubble** — `except Exception` + `logger.warning` preserves partial results (mirrors tree's SUBQUERY_FAILED philosophy).
- **`^/?` anchor at root specifically** — leading slash is optional only when `args.path == '/'`; non-root prefixes use `^{escaped_prefix}/` for strict matching.

## Threat Mitigations

| Threat ID | STRIDE | Mitigation Verified |
|-----------|--------|---------------------|
| T-04-05-01 | Information Disclosure (T-CrossScopeLeak) | GlobArgs has no user_id field (verified Plan 02 schemas.py — GlobArgs only declares pattern/path/type/scope with `extra='ignore'`). user_id is derived from JWT in the dispatch loop (Episode 1 invariant; openai_client.py reads it from caller, not args). Every PostgREST query inherits the caller's RLS context via the JWT-bound supabase_client. The `scope` arg is NARROWING on top of RLS, never the access decision. HI-01 `_assert_uuid(user_id, 'user_id')` is called BEFORE any user_id interpolation into a `.or_()` filter, defending in depth against a future bug where user_id might pick up non-UUID characters. |
| T-04-05-02 | Tampering (T-PathTraversal) | Triple chokepoint verified inline: (1) Pydantic `Field(pattern=_PATH_RE)` on path rejects most malformed paths at parse time; (2) `normalize_path(args.path)` runs as the FIRST STATEMENT and rejects '..'/'.' segments — confirmed via smoke test where `GlobArgs(pattern='*.pdf', path='/..', scope='user')` passed Pydantic but `glob_match(args, ...)` returned `{'tool': 'glob', 'error': 'INVALID_PATH', 'message': "Invalid path segment: '..' in '/..' (path traversal segments '.' and '..' are forbidden)"}`; (3) Migration 012 CHECK on documents.folder_path enforces canonical form at the DB. The glob `**` pattern is correctly bounded by the LIKE prefix prefilter — `folder_path LIKE prefix||'/%'` AND then `re.fullmatch(regex, full_path)` — so the LIKE narrows the candidate set BEFORE the regex is even evaluated. |
| T-04-05-03 | Elevation of Privilege (T-PostgrestInjection) | Two-layer defense: (1) HI-03 `_escape_like(norm_prefix)` is called on every literal-path-prefix LIKE predicate (verified via grep — every `.like()` and `.or_()` call that interpolates the prefix uses `esc = _escape_like(norm_prefix)` and `f"{esc}/%"` rather than the raw value); the function escapes `\\`, `%`, `_` to neutralize wildcards so a folder name like `/foo_bar` cannot match `/fooXbar` via the `_` wildcard. (2) HI-01 `_assert_uuid(user_id, 'user_id')` is called BEFORE the `.or_(f"and(scope.eq.user,user_id.eq.{user_id},...)")` interpolation when scope is 'user' or 'both' — verified via smoke test where `glob_match(args, 'not-a-uuid', None)` returns `{'tool': 'glob', 'error': 'INVALID_USER_ID', 'message': 'invalid user_id: not a UUID'}`. Both defenses inherited from Phase 3 / Plan 02. |
| T-04-05-04 | Denial of Service (T-ResultBlowUp) | Three-layer defense: (1) Pydantic `Field(min_length=1, max_length=200)` on GlobArgs.pattern bounds the pattern length; (2) `_MATCH_HARD_CAP = 500` enforced via per-branch cumulative slicing `[: _MATCH_HARD_CAP - len(matches)]` AND via DB-side `.limit(_MATCH_HARD_CAP * 2)` (= 1000) on each query — so the candidate set is bounded BEFORE Python regex matching begins; (3) `apply_12k_cap` at the tail trims to 12K chars + emits `[...truncated, N more entries]` truncation_marker. Even on a `**/*` pattern against a 10K-doc corpus, the LIMIT 1000 + Python slice 500 caps Python work at 1000 fullmatch calls, well within latency budget. |
| T-04-05-05 | Repudiation (T-EmptyResponse / Pitfall 8) | Dispatch arm assigns `result_text = json.dumps(tool_result)` to the SAME variable the wrapper at the `truncated_result = result_text[:16000]` site consumes — verified via assertion that the wrapper line still appears twice in openai_client.py. Wrapper layered fallback (16K Layer-1 truncation, Layer-2 streaming Call#2, Layer-3 non-streaming, Layer-4 raw yield) UNCHANGED. glob_match function NEVER calls the Gemini SDK directly — verified by grep gate `assert 'generate_content' not in body` in glob_match.py source. |

## Deviations from Plan

**Total: 0 deviations.**

The plan's pseudocode and inline code block were paste-ready and complete. Both tasks executed exactly as specified:

- Task 1: glob_match.py written with the exact contract (335 lines vs the plan's `min_lines: 100` floor). Every `contains_*` artifact assertion satisfied: `@traceable(name="glob", run_type="tool")`, `def glob_match`, `normalize_path`, `apply_12k_cap`, `ensure_scope_tag`, `_glob_to_regex`, `_assert_uuid` all present.
- Task 2: openai_client.py extended exactly as specified — three localized edits, no other modifications. All `contains_*` artifact assertions satisfied: `def _build_glob_tool`, `elif tool_name == "glob":`, `_build_glob_tool()` registration, `GlobArgs` lazy import.

Plan 03's documented signature-assertion convention (`@traceable` adds a `config` kwarg) was applied from the start; smoke test reported `sig core=['args', 'user_id', 'supabase_client']; full=['args', 'user_id', 'supabase_client', 'config']` — same idiom as Plans 03/04.

## Issues Encountered

- **Worktree base reset:** The worktree HEAD initially pointed at `376b21d` (Episode 1 freeze commit) instead of the required `3f3b1be` (Wave 4 base with Plans 03+04 applied). The worktree-branch-check protocol called for a `git reset --hard 3f3b1be5d2bc582601ea2fd2948b8005daf9d208`, which ran cleanly and put HEAD at the correct base. No work was lost (the worktree had no commits beyond the stale base; the only modified file was `.claude/settings.local.json` which is environment-local). Confirmed afterwards: `git log --oneline -5` showed Plans 03+04 commits in the recent history; `git status --short` was clean.
- **Worktree path quirk awareness:** Per Plan 04 SUMMARY's note, all `Write` calls used the full worktree path prefix (`C:\RAG Automators\claude-code-agentic-rag-masterclass-ep2\.claude\worktrees\agent-abcdab565d755ad3e\...`). Post-write `ls` confirmed both `glob_match.py` (in worktree) and the absence of any stray copy in the main repo path. No rework needed.

## Smoke Tests Run

All inline smoke tests from the plan ran via the main repo venv (`/c/RAG Automators/claude-code-agentic-rag-masterclass-ep2/backend/venv/Scripts/python.exe`) — the worktree has no separate venv per Plan 03 convention. All passed:

- **Task 1 structural** — `ast.parse(src)` OK; `def glob_match` present; `@traceable(name="glob", run_type="tool")` present; `normalize_path` present; `_escape_like` present (HI-03); `_assert_uuid` present (HI-01); `_glob_to_regex` helper present; `apply_12k_cap` present; `ensure_scope_tag` present; error codes `INVALID_PATH` + `INVALID_PATTERN` present; `_MATCH_HARD_CAP = 500` present; no `generate_content` substring (TOOL-09 routing); no `HTTPException`; importable; signature core = `('args','user_id','supabase_client')` with langsmith config kwarg appended; 335 lines (>= 100 required).
- **Task 1 helper functional smoke** — `_glob_to_regex('*.pdf', '/')` → `'^/?[^/]*\\.pdf$'`; `_glob_to_regex('**/*.pdf', '/')` → `'^/?.*/[^/]*\\.pdf$'`; `_glob_to_regex('foo', '/projects')` → `'^/projects/foo$'`. Re-checked via `re.fullmatch`: `^/?[^/]*\.pdf$` matches `/foo.pdf` AND `foo.pdf` but NOT `/sub/foo.pdf` (single segment); `^/?.*/[^/]*\.pdf$` matches `/foo.pdf`, `/sub/foo.pdf`, `/a/b/c/foo.pdf` (any depth); `^/projects/foo$` matches `/projects/foo` but not `/other/foo` (prefix anchor).
- **Task 1 INVALID_PATH chokepoint** — `GlobArgs(pattern='*.pdf', path='/..', scope='user')` passes Pydantic regex (because `..` matches `[^/]+`), but `glob_match(args, ...)` returns the documented INVALID_PATH envelope `{'tool': 'glob', 'error': 'INVALID_PATH', 'message': "Invalid path segment: '..' in '/..' (path traversal segments '.' and '..' are forbidden)"}`. Triple chokepoint confirmed.
- **Task 1 INVALID_USER_ID guard (HI-01)** — `glob_match(GlobArgs(pattern='*.pdf', path='/', scope='user'), 'not-a-uuid', None)` returns `{'tool': 'glob', 'error': 'INVALID_USER_ID', 'message': 'invalid user_id: not a UUID'}`. Confirms `_assert_uuid` runs before any PostgREST interpolation.
- **Task 1 scope=global zero-result happy path** — Stubbed supabase_client returning empty data; `glob_match(GlobArgs(pattern='*.pdf', path='/', type='both', scope='global'), None, StubSupa())` returns `{'tool': 'glob', 'scope_arg': 'global', 'pattern': '*.pdf', 'path_prefix': '/', 'matches': [], 'total_matches': 0, 'truncation_marker': None}`. Confirms scope='global' does NOT trigger uuid check (None user_id is allowed).
- **Task 1 functional happy path with stub data** — Stubbed `documents` table returns 4 rows: `{d1: foo.pdf @ /projects/2026, d2: bar.txt @ /projects/2026, d3: baz.pdf @ /projects/2026/sub, d4: qux.pdf @ /other}`. `glob_match(GlobArgs(pattern='**/*.pdf', path='/projects', type='file', scope='user'), <uuid>, StubSupa())` returns `total_matches=2` with `document_id ∈ {d1, d3}` — d2 filtered out (.txt extension), d4 filtered out (not under /projects). Confirms regex translation + LIKE prefilter + Python re.fullmatch end-to-end.
- **Task 2 structural** — `ast.parse(src)` OK; `def _build_glob_tool` present; `name="glob"` in factory; `function_declarations.append(_build_glob_tool())` present; `elif tool_name == "glob":` present; lazy imports `from app.services.exploration_tools.glob_match import glob_match` and `from app.services.exploration_tools.schemas import GlobArgs` present; `GlobArgs(**args)` present; `truncated_result = result_text[:16000]` STILL present (wrapper UNCHANGED check). Plan 03 markers STILL present: `def _build_list_files_tool`, `elif tool_name == "list_files":`. Plan 04 markers STILL present: `def _build_tree_tool`, `elif tool_name == "tree":`. All 7 factories (`_build_search_tool`, `_build_analyze_tool`, `_build_sql_tool`, `_build_web_search_tool`, `_build_list_files_tool`, `_build_tree_tool`, `_build_glob_tool`) and 7 dispatch arms (1 `if` + 6 `elif`: search_documents, query_structured_data, web_search, analyze_document, list_files, tree, glob) each present exactly once.
- **Task 2 runtime** — `from app.services.openai_client import _build_glob_tool, _build_tree_tool, _build_list_files_tool` succeeds; `_build_glob_tool()` returns `FunctionDeclaration(name='glob')` with properties keys `['pattern','path','type','scope']`, `type.enum=['file','folder','both']`, `scope.enum=['user','global','both']`, `required=['pattern']`. Plan 04 `_build_tree_tool()` and Plan 03 `_build_list_files_tool()` still work (their names are 'tree' and 'list_files' respectively).
- **Wrapper-unchanged double check** — `src.count('truncated_result = result_text[:16000] if len(result_text) > 16000 else result_text') == 2` (one in streaming Call#2, one in non-streaming fallback). Verified post-edit.

## User Setup Required

None — no external service configuration required. Plan extends in-process Python and Gemini tool registration only; uses the venv at the main repo path (worktree has no separate venv, per Plan 03 convention) which already has langsmith + pydantic + supabase-py + google-genai installed from Phase 1.

## Next Phase Readiness

**Plan 06 (read_document) ready in Wave 5.** The Phase 4 template established in Plan 03 and reinforced in Plans 04+05 is now battle-tested across three artifacts:

1. Create `app/services/exploration_tools/<tool>.py` with @traceable-decorated public function: normalize_path-first → service-layer query (delegate to folder_service or new RPC) → ensure_scope_tag per row → apply_12k_cap on happy-path return → structured error envelope on exception.
2. Add `_build_<tool>_tool()` factory to openai_client.py alongside `_build_glob_tool` (same lazy `from google.genai import types` body shape).
3. Register inside `if has_documents:` block: `try: function_declarations.append(_build_<tool>_tool()); except Exception as e: logger.warning(...)`.
4. Add `elif tool_name == "<tool>":` dispatch arm AFTER the glob arm and BEFORE the unknown-tool fall-through; same try/except Pydantic parse → `result_text = json.dumps(tool_result)` shape.
5. Wrapper at openai_client.py (the `truncated_result = result_text[:16000]` two sites) STAYS UNCHANGED across all subsequent waves.

**Plan 06 is sequenced into Wave 5 (not parallel with this plan)** — same-wave edits would conflict on the shared elif chain insertion point.

**ROADMAP Phase 4 Success Criteria mapping after this plan:**
- SC1 (registered + dispatched + Pydantic-validated + layered-fallback routed): glob ✓ — third of five tools.
- SC2 (200 folders → < 12K chars): glob ✓ — three-layer defense (Pydantic min/max length + 500-entry hard cap via DB LIMIT and Python slice + apply_12k_cap 12K char cap). Plan 09's 200-folder fixture exercises end-to-end.
- SC3 (every result row carries scope): glob ✓ — file matches use d['scope'] from documents projection, folder matches use scope from folders side table or inferred-from-documents — all wrapped in ensure_scope_tag.

**Plan 06 ready in Wave 5.**

## Self-Check: PASSED

Verification results:
- `backend/app/services/exploration_tools/glob_match.py` — FOUND (335 lines)
- `backend/app/services/openai_client.py` — FOUND (modified, 884 lines; was 807 after Plan 04)
- Commit `88b1e58` (feat(04-05): glob tool — TOOL-02 pattern matching against folder_path + file_name) — FOUND in git log
- Commit `08873b2` (feat(04-05): wire glob into openai_client dispatch (TOOL-09 routing)) — FOUND in git log
- All Task 1 smoke checks PASSED (structural + import + helper translation + INVALID_PATH chokepoint + INVALID_USER_ID HI-01 guard + scope=global zero-result + functional happy path with stub data)
- All Task 2 smoke checks PASSED (structural + runtime + wrapper-UNCHANGED + Plan 03/04 markers UNCHANGED + 7-factory/7-arm count)
- Post-commit deletion check: zero deletions introduced by either commit (only `glob_match.py` created and `openai_client.py` modified additively — verified via `git diff --diff-filter=D --name-only HEAD~2 HEAD` returning empty)
- No STATE.md / ROADMAP.md / REQUIREMENTS.md modifications (worktree mode — orchestrator owns shared state)

---
*Phase: 04-five-exploration-tools-search-documents-extension*
*Plan: 05*
*Completed: 2026-05-09*
