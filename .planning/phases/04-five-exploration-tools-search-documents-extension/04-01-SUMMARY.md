---
phase: 04-five-exploration-tools-search-documents-extension
plan: 01
subsystem: database
tags: [postgres, plpgsql, rpc, regex, pgtrgm, statement_timeout, security_invoker, hybrid_search]

requires:
  - phase: 01
    provides: documents.content_markdown column + content_markdown_status enum (Migration 014); documents_content_markdown_trgm_idx GIN trigram index (Migration 016); two-scope RLS policies (Migration 015)
  - phase: 02
    provides: synchronous content_markdown writes at upload-time (Plan 02-02) + LOCKED tool integration contract (non-ready rows surfaced as pending_reindex)
  - phase: 03
    provides: matching path-prefix predicate convention used in folder_service.list_folder (Plan 03-02)
provides:
  - grep_documents PL/pgSQL RPC — line-resolved regex search with 5s statement_timeout, ILIKE pre-filter, pending_reindex surfacing
  - match_document_chunks_with_filters extended with match_folder_path TEXT DEFAULT NULL + match_scope TEXT DEFAULT NULL (NULL defaults preserve every Phase 1/2/3 caller)
  - match_document_chunks_hybrid extended with the same two tail-position params; predicates applied identically to vector_results AND keyword_results CTEs so RRF-merged results are consistent under narrowing
affects: [Plan 04-07 grep tool wrapper, Plan 04-08 search_documents extension, Plan 04-09 integration tests, Phase 5 Explorer sub-agent]

tech-stack:
  added: []
  patterns:
    - "PL/pgSQL CROSS JOIN LATERAL regexp_split_to_table(text, E'\\n') WITH ORDINALITY for natively 1-based line numbers (no Python-side line counting)"
    - "SET LOCAL statement_timeout = '5s' inside PL/pgSQL body — the only place this can live since supabase-py has no per-query GUC hook (Pitfall 3 mitigation #5)"
    - "Tail-position parameter extension with NULL defaults — extends two existing search RPCs without breaking any caller. CREATE OR REPLACE FUNCTION with new arity creates a NEW overload; old signature remains for backwards-compat (verified in pg_proc — 5 rows total: 1 grep_documents + 2 with_filters overloads + 2 hybrid overloads)"

key-files:
  created:
    - backend/migrations/020_phase4_rpcs.sql
  modified: []

key-decisions:
  - "Three RPCs colocated in one migration file (mirrors Migration 015 RLS catalog + Migration 019 folder RPCs) — they share the Phase 4 review surface; not splitting"
  - "Module 9's existing match_document_chunks_hybrid keyword path (dc.tsv + websearch_to_tsquery) preserved verbatim (handles hyphenated technical identifiers like MDB-C-G3); only the two new tail-position params + their predicates were added. The plan's inline snippet showed a simplified to_tsvector form which would have regressed Module 9; per the plan's read_first directive, the existing function body was copied verbatim."
  - "ILIKE pre-filter (p_literal_substring) lives BEFORE the regex in the candidates CTE so documents_content_markdown_trgm_idx (Migration 016) fires; without an extractable literal, the regex falls back to seq-scan over the path-prefix-bounded candidate set — still bounded, just slower. Plan 04-07's Python wrapper auto-extracts a literal of length >=3 from the regex when possible."
  - "Migration applied via Supabase MCP (apply_migration) instead of run_migrations.py because DATABASE_URL is not exported in this environment — same fallback path Phase 3 / Plan 01 documented and Phase 2 / Plan 01 set the precedent for. Both apply paths run identical SQL."

patterns-established:
  - "WITH ORDINALITY for 1-based line resolution: avoids any Python-side line-counting in tool wrappers; line_no comes straight out of the RPC as BIGINT"
  - "pending_reindex surfacing UNION ALL pattern: candidates CTE → pending CTE (status='pending_reindex' for non-ready rows, NULL line_no/line_text) UNION ALL matches CTE (status='matched'). Phase 4 tools NEVER silently skip non-ready docs; the LOCKED Phase 2 contract is enforced at the DB layer"
  - "Extended-RPC overload coexistence: when extending an existing PostgREST-callable RPC with new tail-position params + DEFAULT NULL, the old signature remains as a separate overload in pg_proc. Callers that pass the new args route to the new overload by argument count; callers that don't route to the old overload. This is the safest possible backwards-compat strategy — the only failure mode would be a Postgres ambiguous-function-call error which can't fire here because the tail params have explicit types"

requirements-completed: [TOOL-03, SEARCH-02]

duration: ~4min
completed: 2026-05-09
---

# Phase 04 / Plan 01: Migration 020 — grep_documents RPC + match_document_chunks_* filter extension

**Three Phase 4 PL/pgSQL RPCs land in one migration: a new line-resolved regex tool with 5s statement_timeout + ILIKE pre-filter (Pitfall 3 mitigated at the DB layer), and the two existing chunk-search RPCs gain optional folder_path/scope narrowing without breaking any Phase 1/2/3 caller.**

## What was built

| RPC | Status | Signature | Notes |
|---|---|---|---|
| `grep_documents` | NEW | 7 params: p_pattern, p_path_prefix, p_scope, p_user_id, p_case_insensitive, p_max_hits, p_literal_substring | RETURNS TABLE(document_id, file_name, folder_path, scope, line_no, line_text, status); SET LOCAL statement_timeout='5s'; ILIKE pre-filter; CROSS JOIN LATERAL regexp_split_to_table WITH ORDINALITY; pending_reindex UNION ALL surfacing |
| `match_document_chunks_with_filters` | EXTENDED | +2 tail params (match_folder_path, match_scope) | Old 4-arg overload preserved in pg_proc — backwards-compat |
| `match_document_chunks_hybrid` | EXTENDED | +2 tail params (match_folder_path, match_scope) | Old 6-arg overload preserved; new predicates applied IDENTICALLY in vector_results AND keyword_results CTEs |

All three are SECURITY INVOKER (RLS from Migration 015 applies), all three are granted EXECUTE to `authenticated`, none use SECURITY DEFINER (Pitfall 1 RANK 1 enforced), zero `string_agg`/`array_agg` (Pitfall 6 RANK 2 enforced), zero `CONCURRENTLY` (CREATE OR REPLACE inside transactions).

## How it was applied

`run_migrations.py` requires `DATABASE_URL` which is not exported in this environment. Per the Phase 3 / Plan 01 documented fallback chain: applied via Supabase MCP `apply_migration` with name=`020_phase4_rpcs` and the SQL body extracted from the committed `backend/migrations/020_phase4_rpcs.sql`. Verified via Supabase MCP `execute_sql` against `pg_proc` — all 5 expected rows present (1 grep_documents + 2 with_filters overloads + 2 hybrid overloads), all SECURITY INVOKER, all `auth_can_exec=true`.

## Verification

```sql
SELECT p.proname, p.prosecdef, pg_get_function_identity_arguments(p.oid),
       has_function_privilege('authenticated', p.oid, 'EXECUTE')
FROM pg_proc p JOIN pg_namespace n ON p.pronamespace = n.oid
WHERE n.nspname = 'public' AND p.proname IN ('grep_documents',
       'match_document_chunks_with_filters', 'match_document_chunks_hybrid');
```

Returns 5 rows (verified). All 11 must_haves.truths from the plan frontmatter satisfied; all 11 artifact `contains_*` substrings present in the SQL file.

## Self-Check: PASSED

## Issues Encountered

None. Two minor deviations during authoring (both Rule 1 — bug-prevention):

1. The plan's inline snippet for `match_document_chunks_hybrid` showed a simplified keyword-CTE shape (`to_tsvector('english', dc.content)` + `plainto_tsquery`). The plan's `<read_first>` directive explicitly says "Copy the existing function body verbatim from `migrations/011_improved_keyword_search.sql:6-55`" which uses `dc.tsv` + `websearch_to_tsquery('english', query_text)` (Module 9's improvement that handles hyphenated technical identifiers). The verbatim-copy directive was followed; only the two new tail params + their predicates were added.

2. Whitespace normalization on `match_folder_path TEXT` declarations to satisfy the plan's literal-substring verifier (`body.count('match_folder_path TEXT') >= 2`). Pure formatting, zero semantic effect.

## Next Up

Plan 04-07 will write the Python `grep` tool wrapper that calls `grep_documents` via `supabase_client.rpc('grep_documents', {...})`. Plan 04-08 will extend the search_documents dispatch site to pass `match_folder_path`/`match_scope` to whichever match RPC the existing code path uses.
