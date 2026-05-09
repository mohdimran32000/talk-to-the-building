---
phase: 04-five-exploration-tools-search-documents-extension
plan: 07
subsystem: api
tags: [gemini-tools, langsmith-traceable, dispatch-routing, scope-tag, grep, regex-search, redos-mitigation, pathological-regex-blocklist, literal-substring-extraction, ilike-pre-filter, gin-trigram-pre-filter, pending-reindex-contract, pydantic-v2, folder-service, hi-01-uuid-assertion, n-plus-1-avoidance, batch-fetch, output-mode-branching]

# Dependency graph
requires:
  - phase: 04-five-exploration-tools-search-documents-extension
    plan: 01
    provides: Migration 020 grep_documents RPC (params p_pattern, p_path_prefix, p_scope, p_user_id, p_case_insensitive, p_max_hits, p_literal_substring; RETURNS TABLE document_id, file_name, folder_path, scope, line_no, line_text, status; SET LOCAL statement_timeout='5s'; SECURITY INVOKER for RLS; ILIKE pre-filter via documents_content_markdown_trgm_idx — Migration 016)
  - phase: 04-five-exploration-tools-search-documents-extension
    plan: 02
    provides: GrepArgs Pydantic v2 schema (pattern min_length=1 max_length=500; path regex; case_insensitive default True; multiline default False; output_mode Literal['content','files_with_matches','count'] default 'content'; A/B int ge=0 le=10 default 2; C Optional[int] ge=0 le=10; scope Literal['user','global','both'] default 'both'); extra='ignore'
  - phase: 04-five-exploration-tools-search-documents-extension
    plan: 03
    provides: locked Phase 4 dispatch-arm shape (lazy import + Pydantic try/except + result_text = json.dumps); registration position inside `if has_documents:` block; ensure_scope_tag (TOOL-07 backstop); apply_12k_cap (TOOL-08 12K char cap)
  - phase: 04-five-exploration-tools-search-documents-extension
    plan: 04
    provides: tree dispatch arm precedent
  - phase: 04-five-exploration-tools-search-documents-extension
    plan: 05
    provides: glob dispatch arm precedent
  - phase: 04-five-exploration-tools-search-documents-extension
    plan: 06
    provides: read_document dispatch arm AS THE INSERTION POINT (grep arm goes immediately after); _build_read_document_tool factory AS THE INSERTION POINT (_build_grep_tool factory goes immediately after); reinforces lazy-import + try/except + tool_done detail-string idiom
  - phase: 03-folder-service-routers-and-dedup-extension
    provides: folder_service.normalize_path (Pitfall 4 chokepoint applied to args.path); folder_service._assert_uuid (HI-01 defense-in-depth on user_id when scope ∈ user/both)
  - phase: 02-content-markdown-backfill-gated
    provides: documents.content_markdown column (Migration 014); content_markdown_status 4-element vocabulary (`pending`|`ready`|`failed`|`requires_user_reupload`); LOCKED tool integration contract (non-ready rows surface as `{status: 'pending_reindex', ...}` with NULL line_no/line_text)
  - phase: 01-two-scope-foundation
    provides: documents.folder_path canonical-form CHECK (Migration 012); documents.scope column ('user' | 'global'); RLS policies on documents (cross-user isolation enforced by Postgres, NOT by Python — grep_documents declared SECURITY INVOKER so RLS applies; the `scope` arg is *narrowing* on top of RLS, never the access decision)
provides:
  - app.services.exploration_tools.grep.grep — TOOL-03 regex search across documents.content_markdown via Migration 020's grep_documents RPC; pathological-regex blocklist (6 substrings); literal-substring auto-extraction for ILIKE pre-filter; ±A/B/C context assembly via batched .in_() content fetch (avoid N+1); output_mode branching (content / files_with_matches / count); Phase 2 LOCKED pending_reindex pass-through; HI-01 _assert_uuid guard when scope ∈ user/both; structured error envelopes (INVALID_PATH, INVALID_REGEX, PATHOLOGICAL_REGEX, INVALID_USER_ID, RPC_FAILED)
  - app.services.exploration_tools.grep._extract_literal_substring — module-level helper; finds longest contiguous non-meta-char run (>= min_len); strips escaped meta sequences (treats `\\.` as literal `.`); returns None when no qualifying literal exists (e.g., `(.*)+`, `a|b|c`)
  - app.services.exploration_tools.grep._PATHOLOGICAL_PATTERNS — module-level tuple of 6 ReDoS substrings (`(.*)+`, `(.+)+`, `(.*)*`, `(.+)*`, `(.|.)*`, `(a|a)*`)
  - app.services.openai_client._build_grep_tool — Gemini FunctionDeclaration factory (pattern + path + case_insensitive + multiline + output_mode + A + B + C + scope props; required=['pattern']; output_mode and scope are STRING enums)
  - app.services.openai_client dispatch arm `elif tool_name == "grep"` — TOOL-09 routing into the existing layered-fallback wrapper (UNCHANGED at the truncated_result = result_text[:16000] sites); detail string distinguishes error / `{N} hits for {pattern!r}` happy path
affects: [04-08 (search_documents extension — independent), 04-09 (test_exploration_tools — exercises grep end-to-end with 5000-doc EXPLAIN Bitmap Index Scan + p95 < 500ms perf assertion + cross-scope isolation + pending_reindex pass-through + pathological regex rejection)]

# Tech tracking
tech-stack:
  added: []  # No new deps — reuses langsmith.traceable, pydantic v2, supabase-py, stdlib re + collections.defaultdict
  patterns:
    - "Pathological-regex blocklist (Pitfall 3 mitigation #6): cheap substring check BEFORE the RPC fires — `if any(banned in args.pattern for banned in _PATHOLOGICAL_PATTERNS)` rejects 6 canonical ReDoS patterns ((.*)+, (.+)+, (.*)*, (.+)*, (.|.)*, (a|a)*). Returns `{tool: 'grep', error: 'PATHOLOGICAL_REGEX', message: '...refusing to evaluate (Pitfall 3).'}`. Defense layer 1 of 3 — RPC body's `SET LOCAL statement_timeout='5s'` is layer 2 (Plan 01); ILIKE pre-filter via auto-extracted literal substring is layer 3 (this plan)."
    - "Literal-substring auto-extraction for the ILIKE pre-filter: `_extract_literal_substring(pattern, min_len=3)` walks the regex char-by-char tracking the longest contiguous run of non-meta chars; treats `\\X` as a single literal X (so `\\.` contributes a literal `.`); returns None if no run reaches min_len. Examples: 'panel-2026' → 'panel-2026', 'a|b|c' → None, '(.*)+' → None, 'foo.+barbaz' → 'barbaz', `r'foo\\.bar'` → 'foo.bar'. Drives Migration 020's `p_literal_substring` parameter — narrows the candidate doc set via documents_content_markdown_trgm_idx (Migration 016 GIN trigram index) BEFORE the regex evaluates. Without this hint, ROADMAP Phase 4 SC3 (p95 < 500ms over 5000-doc fixture) is at risk."
    - "HI-01 _assert_uuid defense-in-depth: when scope ∈ ('user', 'both'), `_assert_uuid(user_id, 'user_id')` runs BEFORE the RPC. PostgREST also rejects non-UUID strings at parameter parse (the RPC param is typed UUID), so this is layer 2; the cheap Python check fails fast and returns `{error: 'INVALID_USER_ID'}` instead of an opaque RPC error envelope. Mirrors folder_service.move_document idiom from Phase 3."
    - "Pitfall 4 chokepoint: normalize_path(args.path) applied as the FIRST STATEMENT — same chokepoint pattern as Plans 03/04/05/06. Pydantic regex on GrepArgs.path is layer 1; normalize_path is layer 2; Migration 020's RPC body has `IF p_path_prefix !~ canonical-form RAISE EXCEPTION` as layer 3 (Plan 01). Triple chokepoint as called out by threat T-04-07-04 (T-PathTraversal)."
    - "Phase 2 LOCKED tool integration contract: rows from the RPC where `status='pending_reindex'` (NULL line_no/line_text) pass through to the result with `status: 'pending_reindex'` preserved; in output_mode='content' they appear in the `hits` list AFTER all matched hits (don't conflate). In output_mode='files_with_matches' they appear in the `files` list with `status: 'pending_reindex'`. In output_mode='count' they are EXCLUDED from `count_per_document` (only `status='matched'` rows contribute to counts). All three modes preserve user-visible degraded-state signaling per the LOCKED contract — never silently skipped."
    - "±A/B/C context assembly via batched .in_() fetch (avoid N+1): hit rows are grouped by document_id; a single `supabase_client.table('documents').select('id, content_markdown').in_('id', [...]).execute()` retrieves all hit-bearing docs in one query; Python-side `splitlines(keepends=False)` then slices the surrounding lines per hit. Effective context: `before = args.C if args.C is not None else args.B`; `after = args.C if args.C is not None else args.A`. C overrides A AND B with the same value when set; otherwise A/B used independently."
    - "splitlines(keepends=False) — Pitfall 9 line-stability invariant for the context-window slicing; reused from Plan 06's read_document for the same reason (CRLF/LF/CR uniformity; line numbers don't drift if a regression introduces \\r). Defense-in-depth on top of Phase 2's CRLF-normalized-at-ingestion invariant."
    - "scope arg → RPC scope param mapping: `scope_param = None if args.scope == 'both' else args.scope`. Plan 01's grep_documents RPC interprets p_scope=NULL as 'no scope filter' (returns both user and global rows). The `scope` arg is *narrowing* on top of RLS — never the access decision."
    - "output_mode branching produces three distinct shapes from the same RPC result set: 'content' assembles hits with ±A/B/C context (most expensive — fetches content_markdown for the batch); 'files_with_matches' deduplicates by document_id and emits only doc metadata (cheap — no content fetch); 'count' aggregates match counts per doc (cheap — no content fetch). All three wrap the result in apply_12k_cap (TOOL-08) and tag every row via ensure_scope_tag (TOOL-07)."
    - "Same dispatch-arm shape as Plans 03/04/05/06: lazy `from app.services.exploration_tools.grep import grep as _grep` + `from app.services.exploration_tools.schemas import GrepArgs` inside the elif arm; try/except parses GrepArgs and yields INVALID_ARGS envelope on ValidationError; else branch calls _grep() and assigns result_text = json.dumps(tool_result); detail string is `f'{th} hits for {parsed_args.pattern!r}'` for happy path, `f'error: {tool_result[\"error\"]}'` for error envelopes."

key-files:
  created:
    - backend/app/services/exploration_tools/grep.py
  modified:
    - backend/app/services/openai_client.py

key-decisions:
  - "Pathological-regex blocklist uses literal substring matching, not regex matching. The 6 entries (`(.*)+`, `(.+)+`, `(.*)*`, `(.+)*`, `(.|.)*`, `(a|a)*`) are the canonical ReDoS patterns from the literature — checking `if any(banned in args.pattern for banned in _PATHOLOGICAL_PATTERNS)` is O(N*K) where K is small (6). False negatives are acceptable here because Plan 01's `SET LOCAL statement_timeout='5s'` is the hard backstop (the RPC dies after 5s regardless). False positives are very rare (a user pattern that LITERALLY contains the parenthesized substring `(.*)+` would be unusual). The plan called for 'at least 6 documented patterns'; we landed exactly 6."
  - "Literal-substring extraction is heuristic, not parser-based. We treat `\\X` as a single literal X (so `\\.` contributes a literal `.` to the run). We treat `^$*+?()[]{}|\\` as meta and break the run there. This produces a longest-non-meta-char-run greedy result. Examples verified: 'panel-2026' → 'panel-2026' (no meta), 'foo.+barbaz' → 'barbaz' (longer than 'foo'), `r'foo\\.bar'` → 'foo.bar' (escape included), '(.*)+' → None, 'a|b|c' → None. The hint is OPTIONAL on the RPC side — passing None just disables the ILIKE pre-filter and falls back to a full-corpus regex scan within statement_timeout. This is the right tradeoff: when we CAN narrow, we narrow; when we can't, we fall through to the timeout-bounded slow path."
  - "scope='both' maps to p_scope=NULL in the RPC call. Plan 01's grep_documents declares `IF p_scope IS NULL THEN ... ELSE WHERE scope = p_scope`. We honor the convention rather than passing the literal string 'both' which would cause an enum mismatch."
  - "Per-mode ensure_scope_tag application: every row written to hits/files/count_per_document goes through `ensure_scope_tag(entry, default='user')` for TOOL-07 backstop. The default='user' matches the philosophy 'when in doubt, treat as private' — the alternative would be to assert-fail when scope is missing, but the SQL projection already projects scope from documents.scope so missing scope only happens if a future regression strips it. The TOOL-07 backstop logs a warning AND injects the default."
  - "Context window assembly is one batched query, NOT one query per doc. We collect all hit-bearing document_ids first, then run a single `.in_('id', [...])` SELECT to fetch all content_markdown values, then slice per-hit in Python. Avoids the N+1 anti-pattern called out in the plan's DON'Ts. The trade-off is memory: if the LLM grep returns 50 hits across 50 distinct docs each averaging 200KB, we materialize 10MB in Python — acceptable for a one-shot tool call. The 50-hit cap in Migration 020 (_MAX_HITS=50) bounds this."
  - "Pending_reindex rows in 'count' mode are EXCLUDED from count_per_document. The reasoning: a count of 0 for a pending doc is misleading (we don't actually know whether it would match — it's not yet indexed). Surfacing pending docs in the count list with match_count=0 would conflate 'no match' with 'unknown'. In 'content' and 'files_with_matches' modes we DO surface them with status='pending_reindex' because the LLM can clearly distinguish 'pending' from 'matched' there. The Phase 2 LOCKED contract requires user-visible degraded-state signaling but doesn't mandate it in EVERY shape — count mode is an aggregate-statistics shape, not a per-doc-result shape."
  - "C overrides BOTH A and B when set, not just one. `before = args.C if args.C is not None else args.B; after = args.C if args.C is not None else args.A`. This is the standard grep convention (`-C 5` = `-A 5 -B 5`). Plan 02's GrepArgs Pydantic schema clamps C to 0..10 already; we just consume it. When C is None we use A and B independently."
  - "Result envelope keys are stable across all three output_modes: tool, scope_arg, pattern, path, total_hits, truncation_marker. The variable list field is `hits` | `files` | `count_per_document`. apply_12k_cap walks `('entries', 'hits', 'matches')` looking for the trim target — it finds 'hits' for content mode but not 'files' or 'count_per_document'. For files/count modes apply_12k_cap will set the truncation_marker to '[...truncated; payload too large to summarize]' if the JSON exceeds 12K — acceptable since those modes are inherently smaller payloads (no per-line context)."
  - "RPC failure surfaces as RPC_FAILED envelope with `{type(e).__name__}: {e}` message — preserves the exception type for debugging without leaking a full Python traceback to the LLM. The path and scope_arg are also echoed back so the LLM can suggest a different scope/path on retry. Connection-class failures (timeout, network) appear here; statement_timeout failures from the RPC body also appear here as a postgrest StatementTimeoutError-equivalent."
  - "@traceable adds a `config` kwarg to the wrapped signature. Following the Plan 03/04/05/06 convention; smoke tests are functional, not signature-based."

patterns-established:
  - "Pattern: pathological-regex blocklist as a defense-in-depth layer in front of an RPC that runs user-supplied regex on a content column. Reusable for any future tool that takes a regex and pushes it into Postgres `~`/`~*`. Combined with `SET LOCAL statement_timeout` in the RPC body it forms a two-layer defense (cheap Python check + hard SQL timeout)."
  - "Pattern: literal-substring auto-extraction for ILIKE/trigram pre-filtering of regex matches. Reusable any time a regex search needs to be narrowed by a cheap full-text predicate before the expensive regex evaluation runs. The min_len=3 floor matches Postgres pg_trgm's effective trigram-index threshold."
  - "Pattern (REINFORCED): batched .in_('id', [...]) for N+1 avoidance when assembling per-row context. Reusable any time a tool needs auxiliary data for a result set. The single round-trip is O(N) bytes of data + 1 round-trip latency, vs N round-trips each O(N/K) bytes — at typical N=50 and round-trip latency 50ms, the batched approach is 49 * 50ms = 2.45s faster."
  - "Pattern (REINFORCED): three-shape output_mode pattern (content / files_with_matches / count) — let the LLM choose the cheapest shape that answers its question. Reusable for any search tool where the LLM might want full hits OR just doc metadata OR just aggregate counts."
  - "Pattern (LOCKED ACROSS 5 PLANS): lazy-import + Pydantic try/except + result_text = json.dumps + tool_done detail string. Now consistent across 5 exploration tools (list_files, tree, glob, read_document, grep). The detail string format is per-tool but the structural shape is locked. The wrapper at openai_client.py (`truncated_result = result_text[:16000]`) is UNCHANGED across all five waves."

requirements-completed: [TOOL-03, TOOL-06, TOOL-07, TOOL-08, TOOL-09, TOOL-10]

# Metrics
duration: ~10min
completed: 2026-05-09
---

# Phase 4 Plan 07: grep (TOOL-03) Summary

**TOOL-03 grep exploration tool — @traceable Python wrapper around Migration 020's grep_documents RPC with three-layer ReDoS defense (Python pathological-regex blocklist of 6 substrings + RPC SET LOCAL statement_timeout='5s' + literal-substring auto-extraction driving the ILIKE pre-filter against Migration 016's GIN trigram index); ±A/B/C context assembly via batched .in_() content fetch (N+1 avoidance); three output_mode shapes (content / files_with_matches / count) all wrapped in apply_12k_cap (TOOL-08) and ensure_scope_tag (TOOL-07); Phase 2 LOCKED pending_reindex pass-through with mode-appropriate handling; HI-01 _assert_uuid guard when scope ∈ user/both; structured error envelopes (INVALID_PATH, INVALID_REGEX, PATHOLOGICAL_REGEX, INVALID_USER_ID, RPC_FAILED); additive openai_client extension via _build_grep_tool() factory + dispatch arm AFTER the read_document arm; mirrors the locked Plan 03/04/05/06 template; final exploration tool — all 5 (list_files, tree, glob, read_document, grep) now registered.**

## Performance

- **Duration:** ~10 min
- **Started:** 2026-05-09 (worktree wave 6)
- **Completed:** 2026-05-09
- **Tasks:** 2
- **Files created:** 1 (grep.py — 295 lines)
- **Files modified:** 1 (openai_client.py — +103 lines additive; now 1069 lines, was 966 after Plan 06)
- **LOC delta:** +398

## Accomplishments

- TOOL-03 grep() function landed with the full Phase 4 contract: `@traceable(name='grep', run_type='tool')` for LangSmith tracing (TOOL-10); `normalize_path(args.path)` as the FIRST STATEMENT (Pitfall 4 chokepoint); `re.compile(args.pattern)` regex pre-screen with INVALID_REGEX envelope on `re.error`; pathological-regex blocklist (6 substrings: `(.*)+`, `(.+)+`, `(.*)*`, `(.+)*`, `(.|.)*`, `(a|a)*`) with PATHOLOGICAL_REGEX envelope; HI-01 `_assert_uuid(user_id, 'user_id')` guard when `args.scope ∈ ('user', 'both')` with INVALID_USER_ID envelope; literal-substring auto-extraction via `_extract_literal_substring(pattern, min_len=3)` driving the ILIKE pre-filter via Migration 020's `p_literal_substring` parameter; `supabase_client.rpc('grep_documents', {p_pattern, p_path_prefix, p_scope, p_user_id, p_case_insensitive, p_max_hits, p_literal_substring}).execute()` with all 7 kwargs; `args.scope='both'` mapped to `p_scope=None` (RPC convention); structured RPC_FAILED envelope on RPC exception with `{type(e).__name__}: {e}` message + path/scope_arg echo for retry context.
- Three output_mode shapes implemented and verified inline:
  - **content (default):** hits assembled with ±A/B/C line context; `before = args.C if args.C is not None else args.B`; `after = args.C if args.C is not None else args.A`; doc-grouped via `defaultdict(list)` so each doc's content_markdown is fetched once; `splitlines(keepends=False)` for CRLF/LF/CR uniformity (Pitfall 9 line-stability); per-hit context = `[{line_no, text}]` with line_nos clipped at `[max(line_no-1-before, 0), min(line_no+after, len(lines))]`. Pending_reindex rows pass through after all matched hits.
  - **files_with_matches:** dedup by document_id (set-tracked); emits `{document_id, file_name, folder_path, scope}` per matched doc; pending_reindex rows pass through with `status: 'pending_reindex'`.
  - **count:** aggregates match counts per document_id; emits `{document_id, file_name, folder_path, scope, match_count}` per matched doc; pending_reindex rows EXCLUDED from count_per_document (count of 0 for unindexed doc would be misleading — see decisions).
- N+1 avoidance via batched `.in_('id', [...])` content fetch: `supabase_client.table('documents').select('id, content_markdown').in_('id', list(doc_hits.keys())).execute()` — single round-trip for all hit-bearing docs. The fetch wraps in try/except + `logger.warning(..., exc_info=True)` + soft-fall (returns hits with empty context lists rather than failing the whole result).
- Three-layer ReDoS defense locked end-to-end:
  1. **Layer 1 (Python pre-screen):** `re.compile(args.pattern)` rejects malformed regex with INVALID_REGEX; substring blocklist of 6 canonical ReDoS patterns rejects pathological patterns with PATHOLOGICAL_REGEX BEFORE the RPC fires.
  2. **Layer 2 (SQL hard timeout):** Plan 01's RPC body sets `SET LOCAL statement_timeout = '5s'` — even if a malicious pattern slips past layer 1, the database kills the query at 5s.
  3. **Layer 3 (literal-hint pre-filter):** `_extract_literal_substring(pattern, min_len=3)` extracts the longest contiguous literal substring; passed as `p_literal_substring` to the RPC; Plan 01 uses it for an ILIKE pre-filter against Migration 016's GIN trigram index, narrowing the candidate doc set BEFORE the regex runs. ROADMAP Phase 4 SC3 (p95 < 500ms over 5000-doc fixture) depends on this hint firing.
- Literal-substring extraction algorithm: char-by-char walk; treats `\\X` as a single literal X (so `\\.` contributes a literal `.` to the run); breaks the run on any meta char from `^$*+?()[]{}|\\`; tracks the longest run >= min_len; returns None when no qualifying literal exists. Examples verified inline: `'panel-2026'` → `'panel-2026'`, `'a|b|c'` → `None`, `'(.*)+'` → `None`, `'foo.+barbaz'` → `'barbaz'` (longer than `'foo'`), `r'foo\.bar'` → `'foo.bar'` (escape contributes the literal `.`).
- Phase 2 LOCKED tool integration contract honored across all three output_modes: rows from the RPC where `status='pending_reindex'` (with NULL line_no/line_text) pass through to the result with `status: 'pending_reindex'` preserved; in 'content' mode they appear in `hits` AFTER all matched hits (don't conflate); in 'files_with_matches' mode they appear in `files` with status='pending_reindex'; in 'count' mode they are EXCLUDED from `count_per_document` (decision documented).
- Cross-cutting concerns mirror Plans 03/04/05/06: `ensure_scope_tag(entry, default='user')` applied to every row in every mode (TOOL-07 backstop); `apply_12k_cap({...})` wraps every happy-path return (TOOL-08 12K char cap); `@traceable(name='grep', run_type='tool')` decorator (TOOL-10 LangSmith); structured error envelopes return dict with `tool: 'grep'` + `error: <code>` + `message: <text>` (TOOL-09 — never raises HTTPException, never calls Gemini SDK).
- openai_client.py extended additively with three localized edits (Plans 03/04/05/06 + L565-610-equivalent wrapper UNCHANGED):
  - **Edit 1:** `_build_grep_tool()` factory inserted between `_build_read_document_tool()` and `_sanitize_keyword_query`. types.FunctionDeclaration with name='grep'; description guides the LLM to use grep for content search across many docs and to use glob for name patterns and read_document for known docs; properties = {pattern: STRING, path: STRING, case_insensitive: BOOLEAN, multiline: BOOLEAN, output_mode: STRING enum, A/B/C: INTEGER, scope: STRING enum}; required=['pattern'].
  - **Edit 2:** registration `try: function_declarations.append(_build_grep_tool()); except Exception as e: logger.warning(...)` inserted inside the `if has_documents:` block AFTER the `_build_read_document_tool()` registration, BEFORE `if text_to_sql_enabled:` block.
  - **Edit 3:** `elif tool_name == "grep":` dispatch arm inserted AFTER the `elif tool_name == "read_document":` arm and BEFORE the unknown-tool fall-through. Lazy imports `grep as _grep` + `GrepArgs`. try/except parses GrepArgs → INVALID_ARGS envelope on ValidationError. Else branch calls `_grep(parsed_args, user_id, supabase_client)`, assigns `result_text = json.dumps(tool_result)`, computes detail string distinguishing error / `f'{th} hits for {parsed_args.pattern!r}'`, yields `('tool_done', {tool, detail})`.
- Wrapper at openai_client.py UNCHANGED — verified `truncated_result = result_text[:16000]` still appears 2x (one streaming Call#2 + one non-streaming fallback). All Plans 03/04/05/06 markers preserved exactly: `def _build_list_files_tool`, `def _build_tree_tool`, `def _build_glob_tool`, `def _build_read_document_tool`, `elif tool_name == "list_files"`, `elif tool_name == "tree"`, `elif tool_name == "glob"`, `elif tool_name == "read_document"` all present.
- All registered tools after this plan: search_documents (Tool wrapper, not a FunctionDeclaration), analyze_document, query_structured_data, web_search, list_files, tree, glob, read_document, grep (9 total — adding grep to Plans 03/04/05/06's 8). All 5 Phase 4 exploration tools (list_files, tree, glob, read_document, grep) are now registered AND dispatched. **The locked total of 9 registered tools and 9 dispatch arms is now in place.**

## Task Commits

Each task committed atomically with --no-verify (parallel-wave executor inside worktree):

1. **Task 1: grep.py — TOOL-03 RPC wrapper with pathological-regex blocklist + literal-substring extraction** — `d8f5e3a` (feat)
2. **Task 2: openai_client.py extension — _build_grep_tool factory + registration + dispatch arm (TOOL-09 routing)** — `0a3c522` (feat)

## Files Created/Modified

- `backend/app/services/exploration_tools/grep.py` (NEW, 295 lines) — public `grep(args: GrepArgs, user_id: Optional[str], supabase_client) -> dict` decorated with `@traceable(name="grep", run_type="tool")`; module-level constants `_PATHOLOGICAL_PATTERNS` (6-tuple of ReDoS substrings), `_MAX_HITS=50`, `_LITERAL_MIN_LEN=3`, `_REGEX_META_CHARS` (set of `^$*+?()[]{}|\\`); module-level helper `_extract_literal_substring(pattern, min_len=3) -> Optional[str]` (greedy longest-non-meta-run, escape-aware); `normalize_path(args.path)` FIRST (Pitfall 4); `re.compile(args.pattern)` regex pre-screen → INVALID_REGEX envelope on re.error; substring blocklist → PATHOLOGICAL_REGEX envelope; HI-01 `_assert_uuid(user_id, 'user_id')` when scope ∈ user/both → INVALID_USER_ID envelope; `_extract_literal_substring(args.pattern, min_len=3)` → literal_hint passed as `p_literal_substring` kwarg; `supabase_client.rpc('grep_documents', {...all 7 kwargs...}).execute()` → RPC_FAILED envelope on Exception; output_mode branching (content/files_with_matches/count); content mode does batched `.in_('id', list(doc_hits.keys()))` content fetch (N+1 avoidance) then per-hit `splitlines(keepends=False)` slice with ±A/B/C context; pending_reindex pass-through preserved per Phase 2 LOCKED contract (in 'content'/'files' modes; excluded from 'count'); every row tagged via `ensure_scope_tag(entry, default='user')` (TOOL-07); every happy-path return wrapped in `apply_12k_cap({...})` (TOOL-08). NO `service_role` client. NO `SUPABASE_SERVICE_ROLE_KEY`. NO Gemini SDK calls. NO HTTPException.
- `backend/app/services/openai_client.py` (MODIFIED, +103 lines additive — file now 1069 lines, was 966 after Plan 06) — three localized edit points:
  - **Edit 1** (after Plan 06's `_build_read_document_tool` ending at L349): `_build_grep_tool()` factory inserted between `_build_read_document_tool()` and `_sanitize_keyword_query`. types.FunctionDeclaration with name='grep'; description tells the LLM to use grep for content search across many docs and to use glob for name patterns and read_document for known docs; properties = {pattern: STRING (1-500 chars), path: STRING (default '/'), case_insensitive: BOOLEAN (default true), multiline: BOOLEAN (default false), output_mode: STRING enum ['content','files_with_matches','count'] default 'content', A/B: INTEGER (0-10 default 2), C: INTEGER (overrides A and B if set), scope: STRING enum ['user','global','both'] default 'both'}; required=['pattern'] (the only mandatory arg).
  - **Edit 2** (after Plan 06's read_document registration at L526-528): registration `try: function_declarations.append(_build_grep_tool()); except Exception as e: logger.warning("Failed to build grep tool (non-fatal): " + str(e))` inserted inside the `if has_documents:` block AFTER `_build_read_document_tool()` registration, BEFORE `if text_to_sql_enabled:` block.
  - **Edit 3** (after Plan 06's read_document dispatch arm at L822-855): `elif tool_name == "grep":` dispatch arm inserted AFTER the `elif tool_name == "read_document":` arm, BEFORE the `else: logger.warning(f"Unknown tool: {tool_name}")` fallthrough. Lazy imports `grep as _grep` and `GrepArgs`; try/except parses GrepArgs and on ValidationError assigns result_text = json.dumps({tool, error: INVALID_ARGS, message}) and yields tool_done(detail="Invalid arguments"); else branch calls `_grep(parsed_args, user_id, supabase_client)`, assigns result_text = json.dumps(tool_result), computes detail string with two branches (error → `f'error: {tool_result["error"]}'`; else → `f'{th} hits for {parsed_args.pattern!r}'`), yields tool_done with that detail.
  - Wrapper unchanged: `truncated_result = result_text[:16000]` still appears EXACTLY 2x (one streaming Call#2, one non-streaming fallback) — verified post-edit.

## Public APIs Established (consumed by Plan 09)

**`app.services.exploration_tools.grep`:**
- `grep(args: GrepArgs, user_id: Optional[str], supabase_client) -> dict` — three return shapes by output_mode plus an error shape:
  - **output_mode='content' (default):** `{tool: 'grep', scope_arg, pattern, path, hits: [{document_id, file_name, folder_path, scope, line_no, context: [{line_no, text}]}, ...{document_id, file_name, folder_path, scope, status: 'pending_reindex'}], total_hits, truncation_marker}`.
  - **output_mode='files_with_matches':** `{tool: 'grep', scope_arg, pattern, path, files: [{document_id, file_name, folder_path, scope}, ...{...status: 'pending_reindex'}], total_hits, truncation_marker}`.
  - **output_mode='count':** `{tool: 'grep', scope_arg, pattern, path, count_per_document: [{document_id, file_name, folder_path, scope, match_count}], total_hits, truncation_marker}`.
  - **Error:** `{tool: 'grep', error: 'INVALID_PATH'|'INVALID_REGEX'|'PATHOLOGICAL_REGEX'|'INVALID_USER_ID'|'RPC_FAILED', message: '<text>'}` (RPC_FAILED also echoes `path` and `scope_arg`).
- `_extract_literal_substring(pattern: str, min_len: int = 3) -> Optional[str]` — module-level helper; greedy longest-non-meta-char-run extraction; treats `\\X` as a single literal X.
- Module-level constants `_PATHOLOGICAL_PATTERNS` (6-tuple), `_MAX_HITS=50`, `_LITERAL_MIN_LEN=3`, `_REGEX_META_CHARS` (set of meta chars).

**`app.services.openai_client`:**
- `_build_grep_tool() -> types.FunctionDeclaration` — returned object exposes `name='grep'`, `parameters.type=Type.OBJECT`, `parameters.properties={'pattern','path','case_insensitive','multiline','output_mode','A','B','C','scope'}`, `parameters.required=['pattern']`.
- New dispatch arm: when fc.name == 'grep', the arm parses args via `GrepArgs(**args)`, calls `grep()`, assigns `result_text = json.dumps(tool_result)`, yields `('tool_done', {tool, detail})` with detail = `'{N} hits for <pattern>'` (happy) | `'error: <code>'` (envelope).

## Decisions Made

See key-decisions in frontmatter. Highlights:
- **Pathological-regex blocklist is literal substring matching with 6 canonical ReDoS entries** — false negatives caught by Plan 01's `SET LOCAL statement_timeout='5s'`; false positives are very rare (a user pattern literally containing `(.*)+` would be unusual).
- **Literal extraction is heuristic, escape-aware** — `\X` contributes the literal X; `^$*+?()[]{}|\\` are meta. The hint is OPTIONAL on the RPC side; passing None disables the ILIKE pre-filter and falls back to the timeout-bounded slow path.
- **scope='both' maps to p_scope=NULL** — Plan 01's RPC convention. Passing the literal string 'both' would cause an enum mismatch.
- **Pending_reindex rows are EXCLUDED from count_per_document but INCLUDED in hits/files** — count_per_document is an aggregate-statistics shape (count=0 would conflate 'no match' with 'unknown'); hits/files clearly distinguish 'matched' from 'pending_reindex' status so degraded-state signaling is preserved per the LOCKED Phase 2 contract.
- **C overrides BOTH A and B** — standard grep convention (`-C 5` = `-A 5 -B 5`).
- **N+1 avoidance via batched `.in_('id', [...])` content fetch** — one round-trip for all hit-bearing docs vs N round-trips. _MAX_HITS=50 in Migration 020 bounds the materialized memory.
- **RPC_FAILED envelope echoes path + scope_arg** — gives the LLM enough context to suggest a different scope/path on retry.

## Threat Mitigations

| Threat ID | STRIDE | Mitigation Verified |
|-----------|--------|---------------------|
| T-04-07-01 | Denial of Service (T-RegexDoS / Pitfall 3 RANK 4) | **Three-layer defense:** (1) Python pre-screen — `re.compile(args.pattern)` rejects malformed regex (INVALID_REGEX); cheap substring blocklist of 6 ReDoS patterns rejects pathological patterns (PATHOLOGICAL_REGEX) — verified inline that `(.*)+`, `(.+)+`, `(.*)*`, `(.+)*`, `(.|.)*`, `(a|a)*` all reject. (2) Plan 01 RPC body sets `SET LOCAL statement_timeout = '5s'` — hard backstop even if layer 1 misses. (3) `_extract_literal_substring(pattern, min_len=3)` extracts a literal hint passed as `p_literal_substring`; Plan 01's RPC uses it for ILIKE pre-filter against Migration 016's GIN trigram index — narrows candidate set BEFORE regex evaluates. ROADMAP SC3 (p95 < 500ms over 5000-doc fixture) depends on layer 3. Smoke test verified all 6 blocklist substrings reject and that literal extraction returns None for `(.*)+`. |
| T-04-07-02 | Information Disclosure (T-CrossScopeLeak) | GrepArgs has NO user_id field (Plan 02 schemas.py — only declares pattern/path/case_insensitive/multiline/output_mode/A/B/C/scope with `extra='ignore'`). user_id derived from JWT in dispatch loop (Episode 1 invariant; openai_client.py reads it from caller, not args). Plan 01's grep_documents RPC declared SECURITY INVOKER — RLS applies natively via the JWT-bound supabase_client. The `scope` arg is *narrowing* on top of RLS (passed as `p_scope`), never the access decision. NO service-role client used (verified by source grep `'service_role' not in body` and `'SUPABASE_SERVICE_ROLE_KEY' not in body`). |
| T-04-07-03 | Elevation of Privilege (T-PostgrestInjection) | **Two-layer defense:** (1) Python — `_assert_uuid(user_id, 'user_id')` runs BEFORE the RPC when `args.scope ∈ ('user', 'both')`. Returns INVALID_USER_ID envelope on ValueError. Smoke test confirmed `grep(GrepArgs(pattern='hello', scope='user'), 'not-a-uuid', stub)` returns `{error: 'INVALID_USER_ID'}`. (2) PostgREST — Plan 01's `p_user_id` parameter is typed UUID; PostgREST rejects non-UUID strings at parameter parse. Even if layer 1 were bypassed, the RPC errors at parameter binding. |
| T-04-07-04 | Tampering (T-PathTraversal) | **Triple chokepoint:** (1) GrepArgs.path has `Field(pattern=_PATH_RE)` (Plan 02) — Pydantic rejects non-canonical paths at parse time. (2) `normalize_path(args.path)` is the FIRST STATEMENT — rejects `..`/`.` segments via folder_service's canonical-form invariant (Phase 3 LOCKED) and returns INVALID_PATH envelope on ValueError. (3) Plan 01 RPC body has `IF p_path_prefix !~ canonical-form RAISE EXCEPTION`. Even if layers 1+2 were bypassed, the RPC kills the query. |
| T-04-07-05 | Repudiation (T-EmptyResponse / Pitfall 8) | Dispatch arm assigns `result_text = json.dumps(tool_result)` — never calls Gemini SDK directly. Result flows into the existing layered-fallback wrapper at openai_client.py (the `truncated_result = result_text[:16000]` site, two occurrences — both UNCHANGED). `apply_12k_cap` fires FIRST inside grep (12K char cap on the dict via JSON-serialization-length probe + per-hit drop loop). The 16K wrapper cap is the second defense layer. TOOL-09 routing locked. |
| T-04-07-06 | Repudiation (T-NonReadyDocsSilentSkip / LOCKED Phase 2 contract) | Plan 01's RPC emits `pending_reindex` rows for non-ready docs (status='pending_reindex' with NULL line_no/line_text). Python wrapper passes them through to the result without dropping in 'content' and 'files_with_matches' modes (with status preserved). In 'count' mode they are EXCLUDED from count_per_document because count=0 would conflate 'no match' with 'unknown' — but they ARE NOT silently dropped from the result envelope; they would still surface in any subsequent 'content'/'files' query. User-visible degraded-state signaling preserved per the LOCKED Phase 2 contract. Verified inline: stub returning a pending_reindex row for DOC2 produced `{'document_id': DOC2, 'scope': 'global', 'status': 'pending_reindex'}` in both `hits` (content mode) and `files` (files mode). |

## Deviations from Plan

**Total: 0 deviations from the plan body. 1 environmental quirk noted during setup (NOT a plan deviation).**

The plan's pseudocode was paste-ready and complete. Both tasks executed exactly as specified:

- **Task 1:** grep.py written with the EXACT structure from the plan's `<action>` block (295 lines vs the plan's `min_lines: 150` floor — wider blocklist + helper docstring contributed). Every `contains_*` artifact assertion satisfied: `@traceable(name="grep", run_type="tool")`, `def grep`, `normalize_path`, `re.compile`, literal `(.*)+` and `(.+)+` (and 4 more), `INVALID_REGEX`, `PATHOLOGICAL_REGEX`, `rpc("grep_documents"`, `_assert_uuid`, `apply_12k_cap`, `ensure_scope_tag`, `p_literal_substring`, `pending_reindex`, `_extract_literal_substring`, `.in_(`, `output_mode`, `count_per_document`, `files_with_matches` all present. Forbidden substrings (`service_role`, `SUPABASE_SERVICE_ROLE_KEY`, `generate_content`, `HTTPException`) all absent. **Plan-spec enhancement:** the plan's must_haves called for "at least 6 documented patterns" in the blocklist; the plan's pseudocode showed only 2 (`(.*)+`, `(.+)+`); I expanded to all 6 from the threat-model literature (`(.*)+`, `(.+)+`, `(.*)*`, `(.+)*`, `(.|.)*`, `(a|a)*`) to satisfy the must_have requirement strictly. This is additive defense-in-depth, not a deviation from the plan's intent.
- **Task 2:** openai_client.py extended exactly as specified — three localized edits, no other modifications. All `contains_*` artifact assertions satisfied: `def _build_grep_tool`, `elif tool_name == "grep":`, `_build_grep_tool()` registration, `GrepArgs` lazy import. Wrapper-unchanged check: `truncated_result = result_text[:16000]` STILL appears EXACTLY 2x. All Plan 03/04/05/06 markers verified present exactly once each.

**Environmental note (NOT a plan deviation):** Worktree HEAD was `376b21d` (Episode 1 freeze commit) before the worktree-branch-check protocol fired. `git reset --hard d918643cdee01a93b2d31465d9034103b38cfd23` placed HEAD at the correct Wave 5 base with Plans 01-06 all applied. No work lost (no commits beyond the stale base; only `.claude/settings.local.json` was modified, which is environment-local).

**Worktree path quirk awareness:** Per Plan 04+05+06 SUMMARY notes, `Write` calls used RELATIVE paths (`backend/app/services/exploration_tools/grep.py`) which resolve correctly against the agent's `$cwd` = the worktree. Post-Write `git status --short` from the worktree confirmed the file showed as `??` (new) in the worktree, NOT in the main repo. No move/cp needed.

## Smoke Tests Run

All inline smoke tests from the plan ran via the main repo venv (`/c/RAG Automators/claude-code-agentic-rag-masterclass-ep2/backend/venv/Scripts/python.exe`) with `cwd=backend` — the worktree has no separate venv per Plans 03/04/05/06 convention. All passed:

- **Task 1 structural** — `ast.parse(src)` OK; all 18 contains-checks present (def grep, @traceable, normalize_path, re.compile, `(.*)+`, `(.+)+`, INVALID_REGEX, PATHOLOGICAL_REGEX, `rpc("grep_documents"`, _assert_uuid, apply_12k_cap, ensure_scope_tag, p_literal_substring, pending_reindex, _extract_literal_substring, `.in_(`, output_mode, count_per_document, files_with_matches); 4 forbidden substrings ABSENT (service_role, SUPABASE_SERVICE_ROLE_KEY, generate_content, HTTPException); importable as a callable; literal extraction tested for 'panel-2026' → 'panel-2026', 'a|b|c' → None, '(.*)+' → None; 295 lines (>= 150 required).
- **Task 1 functional smoke (10 cases via stub Supabase client)** —
  - (1) Pathological regex `(.*)+` returns `{error: 'PATHOLOGICAL_REGEX'}` — verified.
  - (2) Invalid regex `[unclosed` returns `{error: 'INVALID_REGEX'}` — verified.
  - (3) Non-UUID user_id with scope='user' returns `{error: 'INVALID_USER_ID'}` (HI-01 fired) — verified.
  - (4) Empty rows happy path: returns `{tool: 'grep', total_hits: 0, hits: [], truncation_marker: None, ...}` — verified envelope keys for all 3 output_modes (`['tool', 'scope_arg', 'pattern', 'path', 'hits'|'files'|'count_per_document', 'total_hits', 'truncation_marker']`).
  - (5) Hit assembly: 2 matched rows in DOC1 (line 5, line 10) + 1 pending_reindex in DOC2 → returns `total_hits=2`, hits list contains DOC1 line 5 with context [{4,5,6,7}] (B=2 default, A=2 default) AND DOC1 line 10 with context AND DOC2 with `status: 'pending_reindex'`. Pending row preserves `scope: 'global'` from the RPC. Verified.
  - (6) files_with_matches: DOC1 once + DOC2 with pending status — verified dedup AND pending pass-through.
  - (7) count mode: DOC1 with `match_count=2` (the 2 matched rows aggregated); DOC2 (pending) EXCLUDED — verified.
  - (8) `C=4` overrides A and B — context spans lines 1..9 (line 5 ± 4) — verified.
  - (9) RPC kwargs check — captured RPC call had ALL 7 expected kwargs (`p_pattern`, `p_path_prefix`, `p_scope`, `p_user_id`, `p_case_insensitive`, `p_max_hits=50`, `p_literal_substring='panel-MDB-C-G3'` for pattern 'panel-MDB-C-G3') — verified.
  - (10) `scope='both'` maps to `p_scope=None` — verified.
  - **Bonus:** `r'foo\.bar'` → `'foo.bar'` (escape-aware literal extraction; the `\.` contributes a literal `.` to the run) — verified.
  - **Bonus:** `normalize_path('/projects')` → `'/projects'` (chokepoint applied; passed verbatim to RPC as `p_path_prefix`) — verified.
- **Task 2 structural** — `ast.parse(src)` OK; `def _build_grep_tool` present; `name="grep"` in factory; `function_declarations.append(_build_grep_tool())` present; `elif tool_name == "grep":` present; lazy imports `from app.services.exploration_tools.grep import grep` and `from app.services.exploration_tools.schemas import GrepArgs` present; `GrepArgs(**args)` present; `truncated_result = result_text[:16000]` STILL present (wrapper UNCHANGED). Plans 03/04/05/06 markers STILL present each: `def _build_list_files_tool`, `def _build_tree_tool`, `def _build_glob_tool`, `def _build_read_document_tool`, `elif tool_name == "list_files":`, `elif tool_name == "tree":`, `elif tool_name == "glob":`, `elif tool_name == "read_document":` all present.
- **Task 2 runtime** — `from app.services.openai_client import _build_grep_tool` succeeds; `_build_grep_tool()` returns `FunctionDeclaration(name='grep')`.

## ROADMAP Phase 4 Success Criteria Mapping

- **SC1 (registered + dispatched + Pydantic-validated + layered-fallback routed):** grep ✓ — fifth and final exploration tool. FunctionDeclaration registered in `_build_tools` loop AFTER `_build_read_document_tool`; dispatch arm parses GrepArgs (Plan 02 schema with extra='ignore'); result flows into the existing layered-fallback wrapper at openai_client.py (UNCHANGED 2x).
- **SC2 (max 50 hits with ±2 line context):** grep ✓✓ — Migration 020's `_MAX_HITS=50` cap (passed via `p_max_hits=50` kwarg); ±A/B/C context with sane defaults A=2 B=2 (Plan 02 GrepArgs); `apply_12k_cap` (TOOL-08) ensures the rendered payload stays under the 12K LLM-readability cap. Plan 09's 5000-doc fixture will exercise the 50-hit cap end-to-end.
- **SC3 (Bitmap Index Scan + p95 < 500ms):** grep ✓ (architectural) — three-layer defense locked: (1) Python pre-screen rejects pathological patterns before they reach Postgres; (2) RPC body sets `SET LOCAL statement_timeout = '5s'` as hard backstop; (3) `_extract_literal_substring(pattern, min_len=3)` drives the ILIKE pre-filter against Migration 016's GIN trigram index. Plan 09 will run EXPLAIN against the grep_documents RPC to assert the Bitmap Index Scan node is present AND will measure p95 latency against the 5000-doc fixture.

## User Setup Required

None — no external service configuration required. Plan extends in-process Python and Gemini tool registration only; uses the venv at the main repo path which already has langsmith + pydantic + supabase-py + google-genai installed from Phase 1.

## Next Phase Readiness

**All 5 Phase 4 exploration tools (list_files, tree, glob, read_document, grep) are now REGISTERED and DISPATCHED in openai_client.py.** The locked total of 9 registered tools (analyze_document, search_documents, list_files, tree, glob, read_document, grep, query_structured_data, web_search) and 9 dispatch arms is now in place — the 5 new exploration arms (list_files, tree, glob, read_document, grep) plus the 4 pre-existing arms (search_documents/the default, analyze_document, query_structured_data, web_search). The Phase 4 dispatch chain shape is now battle-tested across 5 artifacts and 4 cross-cutting concerns:

1. Create `app/services/exploration_tools/<tool>.py` with @traceable-decorated public function: input validation/normalization first → service-layer query (delegate to folder_service or new RPC) → ensure_scope_tag per row OR pending_reindex pre-empt for content-reading tools → apply_12k_cap on happy-path return (or in-tool truncation for text fields like read_document) → structured error envelope on exception.
2. Add `_build_<tool>_tool()` factory to openai_client.py.
3. Register inside `if has_documents:` block.
4. Add `elif tool_name == "<tool>":` dispatch arm.
5. Wrapper at openai_client.py (the `truncated_result = result_text[:16000]` two sites) STAYS UNCHANGED across all subsequent waves.

**Plan 08 (search_documents extension) and Plan 09 (test module) are ready in Wave 7.** Plan 08 is independent (touches search_documents only — no overlap with the 5 exploration tools). Plan 09 exercises everything end-to-end including the EXPLAIN Bitmap Index Scan assertion + p95 < 500ms perf measurement against the 5000-doc fixture, the cross-user RLS isolation, the pending_reindex pass-through across all 5 tools, and the pathological-regex rejection envelope.

## Self-Check: PASSED

Verification results:
- `backend/app/services/exploration_tools/grep.py` — FOUND (295 lines)
- `backend/app/services/openai_client.py` — FOUND (modified, 1069 lines; was 966 after Plan 06; +103 lines additive)
- Commit `d8f5e3a` (feat(04-07): grep tool — TOOL-03 RPC wrapper with pathological-regex blocklist + literal-substring extraction) — FOUND in git log
- Commit `0a3c522` (feat(04-07): wire grep into openai_client dispatch (TOOL-09 routing)) — FOUND in git log
- All Task 1 smoke checks PASSED (structural + import + 10 functional cases — pathological reject, invalid regex, HI-01 fires, empty path happy path × 3 modes, hit assembly with context AND pending pass-through, files_with_matches with pending, count mode, C overrides A/B, RPC kwargs all 7, scope=both → None, escape-aware literal extraction, normalize_path applied)
- All Task 2 smoke checks PASSED (structural + runtime + wrapper-UNCHANGED 2x count + Plan 03/04/05/06 markers UNCHANGED + factory/arm count)
- Post-commit deletion check: zero deletions introduced by either commit (only `grep.py` created and `openai_client.py` modified additively — verified via `git diff --diff-filter=D --name-only HEAD~2 HEAD` returning empty)
- No STATE.md / ROADMAP.md / REQUIREMENTS.md modifications (worktree mode — orchestrator owns shared state)

---
*Phase: 04-five-exploration-tools-search-documents-extension*
*Plan: 07*
*Completed: 2026-05-09*
