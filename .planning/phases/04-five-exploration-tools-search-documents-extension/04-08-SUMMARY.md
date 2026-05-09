---
phase: 04-five-exploration-tools-search-documents-extension
plan: 08
subsystem: api
tags: [gemini-tools, search-documents-extension, llm-self-scope, folder-narrowing, scope-narrowing, system-prompt, search-01, search-03, pitfall-4-chokepoint, pitfall-11-mitigation, backwards-compat, null-default-no-op, optional-args, tool-disambiguation, scope-citation]

# Dependency graph
requires:
  - phase: 04-five-exploration-tools-search-documents-extension
    plan: 01
    provides: Migration 020 — match_document_chunks_with_filters and match_document_chunks_hybrid both gained `match_folder_path TEXT DEFAULT NULL` and `match_scope TEXT DEFAULT NULL` tail-position params (overload-coexistence — old signatures preserved in pg_proc; passing the new kwargs routes to the new overload by argument count). NULL defaults short-circuit the predicates (`AND (match_folder_path IS NULL OR ...)` and `AND (match_scope IS NULL OR d.scope = match_scope)`)
  - phase: 04-five-exploration-tools-search-documents-extension
    plan: 03
    provides: locked openai_client.py dispatch shape (the `elif tool_name == "search_documents":` arm is the EXTENSION TARGET for SEARCH-01); reinforces `args.pop(...)` BEFORE metadata_filter assembly to avoid leaking new args into the JSON metadata_filter envelope
  - phase: 04-five-exploration-tools-search-documents-extension
    plan: 04
    provides: tree dispatch arm precedent (lazy import + Pydantic args + dispatch routing — UNCHANGED in Plan 08)
  - phase: 04-five-exploration-tools-search-documents-extension
    plan: 05
    provides: glob dispatch arm precedent — UNCHANGED in Plan 08
  - phase: 04-five-exploration-tools-search-documents-extension
    plan: 06
    provides: read_document dispatch arm precedent — UNCHANGED in Plan 08
  - phase: 04-five-exploration-tools-search-documents-extension
    plan: 07
    provides: grep dispatch arm precedent (LAST openai_client.py edit before Plan 08); locked layered-fallback wrapper at L965-1010 (`truncated_result = result_text[:16000]` site) — UNCHANGED in Plan 08
  - phase: 03-folder-service-routers-and-dedup-extension
    provides: folder_service.normalize_path (Pitfall 4 chokepoint) — applied to LLM-supplied folder_path BEFORE forwarding to the RPC; ValueError on traversal segments ('..', '.') falls back to None (no narrowing) rather than failing the whole search
  - phase: 01-two-scope-foundation
    provides: documents.scope column ('user' | 'global') + RLS policies (cross-user isolation enforced by Postgres, NOT by Python — RPCs are SECURITY INVOKER so RLS applies regardless of `match_scope`; the `scope` arg is *narrowing* on top of RLS, never the access decision)
provides:
  - app.services.openai_client._build_search_tool — extended properties dict with two NEW optional STRING properties: `folder_path` (prefix-narrowing description; canonical path shape) and `scope` (enum=['user','global','both']; description references RLS-aware narrowing). `required=['query']` UNCHANGED. `**filter_properties` (metadata_schema-driven filters) UNCHANGED.
  - app.services.openai_client.retrieve_chunks — extended signature with two NEW optional kwargs `folder_path: Optional[str] = None` and `scope: Optional[str] = None`. Both forwarded as `match_folder_path` and `match_scope` keys into BOTH RPC dicts (`match_document_chunks_hybrid` AND `match_document_chunks_with_filters`). Existing 5-arg callers see zero behavior change — Migration 020 NULL defaults preserve Phase 1/2/3 results bit-for-bit.
  - app.services.openai_client._execute_search_documents — extended signature to thread `folder_path` and `scope` through to retrieve_chunks. Existing callers (the MALFORMED_FUNCTION_CALL fallback path and the SQL-tool fallback path) unchanged because both new args default to None.
  - app.services.openai_client search_documents dispatch arm — extended to (a) `args.pop("folder_path", None)` + normalize_path() chokepoint with ValueError fallback to None; (b) `args.pop("scope", None)` with enum validation; (c) `'both'` → None translation (the RPC interprets None as "no narrowing"; the literal string `'both'` would never match `d.scope = 'both'` since the documents.scope CHECK only allows `'user'|'global'`). Pop happens BEFORE the `metadata_filter = {k: v for k, v in args.items() if v is not None}` assembly so the new args don't leak into the metadata_filter JSON.
  - app.services.openai_client._build_system_prompt — extended with THREE new bullets in the TOOL SELECTION RULES section, gated on `if has_documents:` so non-document chats keep their existing prompt:
    - SEARCH-03 self-scope hint (when to pass folder_path / scope)
    - Phase 4 precision-tools overview (introduces tree, glob, grep, list_files, read_document)
    - Scope-disambiguation citation hint (TOOL-07 invariant / Pitfall 11 mitigation — every row carries scope; cite scope='user' vs scope='global' explicitly)
affects: [04-09 (test_exploration_tools — SEARCH-01 backward-compat regression + SEARCH-01 narrowing happy path + SEARCH-03 system prompt assertions + cross-scope isolation regression)]

# Tech tracking
tech-stack:
  added: []  # No new deps — reuses google.genai.types.Schema + supabase-py rpc + folder_service.normalize_path
  patterns:
    - "LLM-supplied path treated as untrusted input (Pitfall 4 chokepoint #4): `normalize_path()` is applied to args.get('folder_path') BEFORE forwarding to the RPC. ValueError (path traversal segments like `..`) falls back to None — silently disables narrowing rather than failing the whole search. Rationale: the LLM may hallucinate the path shape; refusing the search is worse UX than searching the full knowledge base. Migration 020's anchored prefix LIKE predicate (`d.folder_path = match_folder_path OR d.folder_path LIKE match_folder_path || '/%'`) is layer 2 — never resolves `..` segments at the SQL level either."
    - "Tail-position kwarg extension with NULL defaults (Plan 01 RPC convention extended into Python): `retrieve_chunks(...)` and `_execute_search_documents(...)` both gain `folder_path: Optional[str] = None, scope: Optional[str] = None` at the END of the signature. Pre-existing callers (the MALFORMED_FUNCTION_CALL fallback at L661-685, the SQL-tool fallback at L767-778) DON'T pass the new args — Python defaults them to None, the RPC interprets NULL as `no narrowing`, results are bit-for-bit identical to Phase 1/2/3. SEARCH-01 backwards compat strategy: NULL DEFAULTS ALL THE WAY DOWN."
    - "Args.pop BEFORE metadata_filter assembly: `folder_path_arg = args.pop('folder_path', None)` and `scope_arg = args.pop('scope', None)` happen BEFORE the existing `metadata_filter = {k: v for k, v in args.items() if v is not None}` line. Without the pop, the new args would leak into metadata_filter and be JSON-encoded into the RPC's `metadata_filter` parameter (which expects a JSONB filter on `documents.metadata`, NOT folder/scope keys) — causing silent zero-result returns since `metadata->'folder_path'` is never populated."
    - "scope='both' → None translation at the dispatch boundary: `rpc_scope = None if scope_arg in ('both', None) else scope_arg`. The Migration 020 RPC contract is `(match_scope IS NULL OR d.scope = match_scope)`; passing the literal string 'both' would produce `d.scope = 'both'` which is never true (documents.scope CHECK enforces 'user'|'global'). The Gemini tool schema declares scope as enum=['user','global','both'] for LLM convenience; the dispatch translates the LLM-friendly 'both' into the SQL-correct NULL."
    - "Enum validation defense-in-depth: `if scope_arg not in ('user', 'global', 'both', None): scope_arg = None`. Gemini's function-calling layer SHOULD enforce the enum on its side, but a hallucinated value (e.g., 'admin', 'public') would silently bypass without this check and produce zero results. Three-line guard catches the regression cheaply."
    - "System prompt extension gated on `if has_documents:` block — matches the surrounding tool-selection block. Non-document chats keep `SYSTEM_PROMPT_NO_DOCS` (the early-return path) OR the no-documents prompt body. Verified: `_build_system_prompt(has_documents=False, ...)` returns a string that does NOT contain `tree` / `glob` / `grep` / `list_files` / `read_document` / `folder_path`."
    - "Three system-prompt bullets are CONCATENATED into the existing parts list via parts.append() — same pattern Plans 03-07 used for the precision-tools registration. The bullets are placed BETWEEN the existing search_documents rule and the existing 'For casual greetings...' / 'Only call ONE tool per turn' rules, preserving the rule-priority ordering."

key-files:
  created: []
  modified:
    - backend/app/services/openai_client.py

key-decisions:
  - "ValueError on folder_path falls back to None (no narrowing), NOT a hard refusal. The plan called this out explicitly: 'failing the whole search is worse UX than searching the full knowledge base'. The user's question is still answered — just over the full RLS-permitted corpus instead of the LLM's intended narrow slice."
  - "Both RPC sites (match_document_chunks_hybrid AND match_document_chunks_with_filters) get the kwargs ALWAYS — regardless of whether the LLM passed them. Migration 020's NULL defaults make this a no-op when the LLM omits both. This avoids a code branch (the alternative would be conditionally adding the kwargs to the dict, which doubles the surface area of the dispatch and creates a maintenance hazard for future overload extensions)."
  - "scope='both' is translated to None at the dispatch layer, not at the RPC layer. Translation at the dispatch keeps the Migration 020 RPC contract simple ('NULL means no narrowing'). The alternative would be teaching the RPC to handle 'both' as an alias for NULL, but that adds SQL-side complexity for a Python-side problem."
  - "_execute_search_documents and retrieve_chunks BOTH get the new params (rather than passing through opaquely). Threading both lets the langsmith @traceable trace see the narrowing args on `_execute_search_documents` (visible in LangSmith UI as tool inputs); without this the narrowing would be invisible in tracing. Cost: 2 extra param declarations + 2 extra forwarding lines."
  - "System prompt bullets use BACKTICKED tool names (`tree`, `glob`, `grep`, `list_files`, `read_document`) for visual parity with the existing tool descriptions — also makes the bullet self-documenting in the Gemini system instruction (the LLM sees the same surface it sees in the tool list)."
  - "Scope citation hint mentions BOTH scope='user' and scope='global' explicitly with their semantics ('user' = private, 'global' = shared knowledge base) — Pitfall 11 is exactly the conflation between these two; the prompt teaches the LLM to disambiguate them in citations rather than (e.g.) always saying 'from your documents' which would be wrong for shared/global rows."
  - "args.pop() is used (not args.get()) for folder_path AND scope: pop removes the key from the dict so the subsequent `metadata_filter = {k: v for k, v in args.items() if v is not None}` line doesn't see them. The `query` arg already used pop for the same reason — we're consistent with that pattern."

patterns-established:
  - "Pattern: optional-args-with-NULL-defaults extension to an EXISTING tool. Reusable any time you need to add narrowing/filtering args to a deployed LLM tool without breaking existing callers. The four-layer defense (Pydantic-style enum validation in dispatch + Pitfall 4 chokepoint normalize + tail-position Python kwargs with None defaults + RPC-side NULL DEFAULT predicates) is the locked recipe."
  - "Pattern: 'both' → None semantic-equivalence translation at the LLM-to-SQL boundary. Reusable any time the LLM-friendly enum includes a 'no filter' value that the SQL contract represents as NULL."
  - "Pattern (LOCKED ACROSS PHASE 4): args.pop BEFORE metadata_filter assembly. Plans 03-08 all touch the search_documents dispatch arm in different ways; the pop ordering is the invariant that prevents new args from polluting the metadata_filter JSON envelope."
  - "Pattern: system-prompt bullet insertion gated on `if has_documents:` block. Reusable any time a system-prompt rule should fire only when the surrounding tool family is registered. Avoids the smell of a stranded rule referencing tools that aren't in the current tool list."

# Threat surface
threat_flags: []  # No new attack surface — extension is additive optional-args on a tool that already enforces RLS at the RPC layer (SECURITY INVOKER, Plan 01). T-04-08-01 (cross-scope leak) mitigated by RLS — the new match_scope param is *narrowing* on top of RLS, never the access decision. T-04-08-02 (path traversal) mitigated by the Pitfall 4 chokepoint AND the RPC's anchored-prefix LIKE predicate. T-04-08-03 (scope confusion / Pitfall 11) mitigated by the new system-prompt bullets. T-04-08-04 (empty response) mitigated by the layered-fallback wrapper at L965-1010 which is UNCHANGED.

requirements-completed: [SEARCH-01, SEARCH-03]

duration: ~5min
completed: 2026-05-09
---

# Phase 04 / Plan 08: search_documents extension — folder_path/scope narrowing + system prompt (SEARCH-01, SEARCH-03)

**Single-file plan extending the search_documents LLM tool with optional folder_path + scope narrowing args (LLM-driven self-scoping) and updating the system prompt to teach the LLM when to use them, when to prefer the 5 precision tools over search_documents, and how to disambiguate scope='user' vs scope='global' in citations. NULL defaults preserve Phase 1/2/3 behavior bit-for-bit. Layered-fallback wrapper and all 8 other dispatch arms UNCHANGED.**

## What was built

| Edit point | Location | Change |
|---|---|---|
| `_build_search_tool()` properties dict | `openai_client.py` ~L106-145 | Added 2 NEW optional STRING properties: `folder_path` (prefix-narrowing) + `scope` (enum=['user','global','both']). `required=['query']` UNCHANGED. `**filter_properties` UNCHANGED. |
| `retrieve_chunks(...)` signature + RPC dicts | `openai_client.py` ~L432-490 | Added 2 NEW optional kwargs `folder_path: Optional[str] = None, scope: Optional[str] = None`. Forwarded as `match_folder_path` + `match_scope` keys into BOTH RPC dicts (`match_document_chunks_hybrid` AND `match_document_chunks_with_filters`). |
| `_execute_search_documents(...)` signature | `openai_client.py` ~L495-525 | Added 2 NEW optional kwargs threading through to `retrieve_chunks`. langsmith @traceable picks the args up as tool-input fields. |
| `search_documents` dispatch arm extraction | `openai_client.py` ~L745-790 | `args.pop("folder_path", None)` + Pitfall 4 normalize_path() with ValueError fallback to None; `args.pop("scope", None)` with enum validation + `'both'` → None translation. Pop happens BEFORE `metadata_filter = {...}` assembly. |
| `_build_system_prompt()` 3 new bullets | `openai_client.py` ~L56-90 | Inside the `if has_documents:` block: SEARCH-03 self-scope hint + Phase 4 precision-tools overview + scope-disambiguation citation hint. |

Total: +107 lines, -3 lines, ONE file.

## SEARCH-01 backwards-compat strategy: NULL defaults all the way down

The wire chain — Gemini tool schema → dispatch arm → `_execute_search_documents` → `retrieve_chunks` → `supabase_client.rpc(...)` → Migration 020 RPC body — preserves NULL at every hop when the LLM omits the new args:

1. **Gemini tool schema**: `folder_path` and `scope` are optional (NOT in `required=["query"]`). The LLM is free to omit them.
2. **Dispatch extraction**: `args.pop("folder_path", None)` and `args.pop("scope", None)` default to None when the LLM omitted the keys.
3. **Pitfall 4 chokepoint**: `if folder_path_arg:` guard skips normalize_path() when None — no exception path.
4. **scope translation**: `rpc_scope = None if scope_arg in ("both", None) else scope_arg` — None stays None.
5. **`_execute_search_documents` → `retrieve_chunks`**: both functions default the new kwargs to None.
6. **RPC dict**: `"match_folder_path": None, "match_scope": None` — supabase-py serializes Python None as JSON null, PostgREST passes NULL to the RPC.
7. **Migration 020 RPC body**: `AND (match_folder_path IS NULL OR ...)` and `AND (match_scope IS NULL OR d.scope = match_scope)` — NULL short-circuits both predicates to TRUE; the WHERE clause reduces to the pre-Phase-4 shape.

Result: pre-existing search_documents callers (no new args) get bit-for-bit identical results.

## SEARCH-03 system prompt diff: 3 new bullets

Inside the `if has_documents:` block of `_build_system_prompt()`, between the existing search_documents rule and the existing 'For casual greetings...' rule:

1. **SEARCH-03 self-scope hint**: "When the user's question is clearly scoped to a folder, pass `folder_path` to search_documents to narrow the search... When the question is about admin-curated shared content vs. the user's private docs, pass `scope='global'` or `scope='user'`. Otherwise leave both unset."
2. **Phase 4 precision-tools overview**: "For codebase-style precision: use `tree` to see the folder structure, `glob` to find files by name pattern (e.g. '**/*.pdf'), `grep` to search inside document text by regex, `list_files` to see one folder's contents, and `read_document` to read specific lines of a doc. Prefer these over search_documents when the user asks 'where is X' or 'show me all PDFs in /projects'."
3. **Scope-disambiguation citation hint**: "Tool results carry a 'scope' field on every row. When citing a result, mention whether it came from the user's private docs (scope='user') or the shared knowledge base (scope='global'). Don't conflate the two."

These bullets fire ONLY when `has_documents=True` (verified: a `_build_system_prompt(has_documents=False, ...)` call returns a string that does not contain any of `tree`, `glob`, `grep`, `list_files`, `read_document`, `folder_path`).

## Verification (inline smoke tests, all PASS)

```
PASS [SC1] _build_search_tool: folder_path + scope STRING optional; required=[query] unchanged
PASS [SC2] dispatch site: match_folder_path/match_scope kwargs flow into BOTH RPC sites
PASS [SC3] backwards compat: folder_path/scope default None — Migration 020 NULL defaults preserve Phase 1/2/3 behavior
PASS [SC4] system prompt: 3 new bullets fire only when has_documents=True
PASS [SC5] layered-fallback wrapper UNCHANGED (truncate + system_with_context + response2 stream)
PASS [SC6] all 8 other dispatch arms UNCHANGED (analyze_document, query_structured_data, web_search, list_files, tree, glob, read_document, grep)
PASS [SC7] Pitfall 4 chokepoint: normalize_path() applied to folder_path BEFORE forwarding
PASS [SC8] scope='both' translated to None (RPC interprets as no narrowing)
PASS [SC9] folder_path/scope popped (not leaked into metadata_filter JSON)
```

Plus the three task-level AST/grep verifiers from the plan all pass; runtime instantiation of `_build_search_tool()` (with stubbed metadata_schema=[]) confirms `properties=['folder_path', 'query', 'scope']` and `required=['query']`.

## Self-Check: PASSED

- Created files: none (single-file modify plan).
- Modified file: `backend/app/services/openai_client.py` — verified via `git status --short` shows ` M backend/app/services/openai_client.py` in THIS worktree (not the main repo).
- All 9 success criteria pass via inline smoke test.
- All 3 task-level verifiers pass.

## Issues Encountered

None. Three minor authoring decisions worth noting (none are deviations from the plan):

1. **Pop vs get for folder_path/scope.** The plan's example used `args.get(...)`, but inspection of the existing dispatch shows it uses `args.pop("query", "")` for the same reason — to prevent leakage into the subsequent `metadata_filter = {k: v for k, v in args.items() if v is not None}` assembly. Used `args.pop(...)` for consistency. The plan's verifier accepts either via `'args.pop("folder_path", None)' in body or 'args.get("folder_path")' in body` — both are valid; pop is correct given the surrounding code.
2. **Threading folder_path/scope through retrieve_chunks AND _execute_search_documents (not just one).** The plan called for the dispatch site to extract+forward; the cleanest implementation puts the new kwargs on BOTH layers so the @traceable wrapper around `_execute_search_documents` records them as tool-input fields in LangSmith (visible in tracing UI). This also keeps `retrieve_chunks` symmetric — any future caller (sub_agent, manual_metadata_filter path, etc.) can pass narrowing without monkey-patching.
3. **Both RPC sites (`hybrid` and `with_filters`) get the kwargs unconditionally.** The plan said "AND/OR" — i.e., whichever path the existing code uses. The existing code uses BOTH (the `if hybrid: ... else: ...` branches at L442-457). Adding the kwargs to both branches is the correct Phase 4 wiring; Migration 020 extended both RPCs identically.

## Next Up

Plan 04-09 (test_exploration_tools — the LAST plan in Phase 4) is now unblocked. It will exercise:
- SEARCH-01 backward-compat regression (no new args → identical results to Phase 1/2/3 fixture)
- SEARCH-01 happy path: `folder_path='/projects'` narrows to docs under that prefix
- SEARCH-01 happy path: `scope='global'` narrows to admin-curated shared docs only
- SEARCH-01 happy path: `scope='both'` translates to NULL → no scope narrowing
- SEARCH-01 path-traversal defense: `folder_path='../../etc'` falls back to None silently (no error, no narrowing)
- SEARCH-03 system prompt assertions: `_build_system_prompt(has_documents=True, ...)` contains all 5 backticked tool names + `folder_path` + `scope='user'` + `scope='global'`
- Cross-user scope isolation regression (RLS still enforces — `scope='global'` from user A does NOT see user B's `scope='user'` rows)
