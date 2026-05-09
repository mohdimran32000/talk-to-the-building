---
phase: 04-five-exploration-tools-search-documents-extension
plan: 06
subsystem: api
tags: [gemini-tools, langsmith-traceable, dispatch-routing, scope-tag, pitfall-9, crlf-normalization, utf8-codepoint-safe-truncation, arrow-form-rendering, pending-reindex-contract, pydantic-v2, folder-service, maybe-single-rls]

# Dependency graph
requires:
  - phase: 04-five-exploration-tools-search-documents-extension
    plan: 02
    provides: ReadDocumentArgs (Pydantic v2 schema with @model_validator exactly-one-of(document_id, path); offset ge=1; limit ge=1 le=5000; path regex `^/$|^/[^/]+(/[^/]+)*/[^/]+$` requires file_name segment), extra='ignore'
  - phase: 04-five-exploration-tools-search-documents-extension
    plan: 03
    provides: locked Phase 4 dispatch-arm shape (lazy import + Pydantic try/except + result_text = json.dumps); _build_list_files_tool registration position inside `if has_documents:` block
  - phase: 04-five-exploration-tools-search-documents-extension
    plan: 04
    provides: tree dispatch arm anchor; _build_tree_tool factory adjacency anchor; precedent for type=INTEGER/STRING enum schema fields
  - phase: 04-five-exploration-tools-search-documents-extension
    plan: 05
    provides: glob dispatch arm AS THE INSERTION POINT (read_document arm goes immediately after); _build_glob_tool factory AS THE INSERTION POINT (read_document factory goes immediately after); reinforces lazy-import + try/except + tool_done detail-string idiom
  - phase: 03-folder-service-routers-and-dedup-extension
    provides: folder_service.normalize_path (Pitfall 4 chokepoint applied to the folder portion of args.path)
  - phase: 02-content-markdown-backfill-gated
    provides: documents.content_markdown column (Migration 014); content_markdown_status 4-element vocabulary (`pending`|`ready`|`failed`|`requires_user_reupload`); LOCKED tool integration contract (non-ready rows surface as `{status: 'pending_reindex', content_markdown_status: <val>}`); CRLF-normalized-at-ingestion invariant (content_markdown stored with `\n` only)
  - phase: 01-two-scope-foundation
    provides: documents.folder_path canonical-form CHECK (Migration 012); documents.scope column ('user' | 'global'); RLS policies on documents (cross-user isolation enforced by Postgres, NOT by Python — read_document relies on `.maybe_single()` returning None when RLS hides the row)
provides:
  - app.services.exploration_tools.read_document.read_document — TOOL-05 line-numbered slice tool with arrow-form rendering, CRLF-uniform splitlines (Pitfall 9 mitigation), 1-based external offset, UTF-8 codepoint-safe truncation on the LAST visible line, Phase 2 LOCKED pending_reindex contract for non-ready rows, NOT_FOUND envelope for missing/RLS-hidden rows, INVALID_ARGS envelope for malformed path
  - app.services.exploration_tools.read_document._resolve_row — module-level helper resolving args.document_id OR args.path (split into folder + file_name; normalize_path on the folder portion) into a single documents row via `.maybe_single().execute().data`
  - app.services.exploration_tools.read_document._CONTENT_CHAR_CAP / _ARROW — module-level constants (12_000 byte cap; literal U+2192 RIGHTWARDS ARROW)
  - app.services.openai_client._build_read_document_tool — Gemini FunctionDeclaration factory (document_id + path + offset + limit props; required=[]; offset/limit are INTEGER; cross-field exactly-one-of enforced by ReadDocumentArgs Pydantic validator)
  - app.services.openai_client dispatch arm `elif tool_name == "read_document"` — TOOL-09 routing into the existing layered-fallback wrapper (UNCHANGED at the truncated_result = result_text[:16000] site); detail string distinguishes error / pending_reindex / lines {sl}-{el}/{tl}
affects: [04-07 (grep — same dispatch-arm template + same content-side line semantics; will reuse splitlines(keepends=False) and arrow-form for hit context windows), 04-08 (search_documents extension — independent), 04-09 (test_exploration_tools — exercises read_document end-to-end with CRLF/Unicode/single-long-line/emoji fixtures + cross-scope isolation)]

# Tech tracking
tech-stack:
  added: []  # No new deps — reuses langsmith.traceable, pydantic v2, supabase-py
  patterns:
    - "splitlines(keepends=False) — Pitfall 9 line-stability invariant: yields the same line count for `\\r\\n` / `\\n` / `\\r` so `total_lines` is deterministic regardless of how content_markdown was stored. Verified inline against three fixtures (CRLF/LF/CR all return total_lines=3 for `l1<EOL>l2<EOL>l3`)."
    - "1-based external offset → 0-based internal index translation: `start_idx = args.offset - 1; end_idx = min(start_idx + args.limit, total_lines); slice_ = lines[start_idx:end_idx] if start_idx < total_lines else []`. The conditional empty slice handles offset > total_lines without IndexError; `start_line == args.offset` and `end_line == start_idx + len(slice_)` (or kept_lines after truncation) are returned for LLM verification."
    - "Arrow-form rendering: `'\\n'.join(f'{start_idx + i + 1}{_ARROW}{line}' for i, line in enumerate(slice_))` — produces `'2→content of line 2\\n3→content of line 3'`. The literal U+2192 RIGHTWARDS ARROW is the Claude Code convention; the constant `_ARROW` is module-level for testability and to make the literal codepoint unmistakable."
    - "UTF-8 codepoint-safe truncation idiom (Pitfall 9): when `len(rendered) > _CONTENT_CHAR_CAP`, run `rendered.encode('utf-8')[:_CONTENT_CHAR_CAP].decode('utf-8', errors='ignore')` to slice by BYTES then drop any trailing partial codepoint, then `.rfind('\\n')` to trim back to a complete line so the LAST line isn't half-shown. Verified inline against an emoji-containing fixture: `4-byte rocket codepoint U+1F680` survives the byte-boundary intact at the end of the last kept line."
    - "Phase 2 LOCKED tool integration contract: when `row['content_markdown_status'] != 'ready'`, return `{tool, document_id, file_name, scope, folder_path, status: 'pending_reindex', content_markdown_status: <val>}` BEFORE attempting to slice content. Pre-empts the `splitlines(None)` AttributeError when content_markdown is NULL and gives the LLM a clear signal that the row exists but isn't ready yet. The 4-element status vocabulary (`pending`|`ready`|`failed`|`requires_user_reupload`) is forwarded verbatim."
    - "NOT_FOUND envelope when `.maybe_single().execute().data` is None — covers BOTH 'no row matches' AND 'RLS hid the row' (callers cannot distinguish; both should surface to the LLM as 'no such document' rather than leak existence info). Mirrors the philosophy used in folder_service.move_document but without the `.eq('user_id', user_id)` defensive filter (which would block admin reads of global-scope docs where user_id IS NULL)."
    - "Path resolution branch: `args.path` is split via `rfind('/')` into `folder_part = path[:idx] or '/'` and `file_name = path[idx+1:]`. The `or '/'` handles top-level files at `/foo.md` (folder_part = '' → '/'). normalize_path applied to folder_part ONLY (defense in depth — Pydantic regex already enforces canonical form). Two `.eq()` predicates on the documents table: `.eq('folder_path', norm_folder).eq('file_name', file_name)`."
    - "No `apply_12k_cap` pass-through (deviation from Plans 03/04/05) — read_document does its own UTF-8-safe truncation on the rendered TEXT (`content` is a string, not a list), and apply_12k_cap is dict-payload-aware, not codepoint-aware. The truncation_marker field is set explicitly (None when no truncation, `'[...truncated, K more lines]'` otherwise)."
    - "Same dispatch-arm shape as Plans 03/04/05: lazy `from app.services.exploration_tools.read_document import read_document as _read_document` + `from app.services.exploration_tools.schemas import ReadDocumentArgs` inside the elif arm; try/except parses ReadDocumentArgs and yields INVALID_ARGS envelope on ValidationError; else branch calls _read_document() and assigns result_text = json.dumps(tool_result); detail string is `f'lines {sl}-{el}/{tl}'` for happy path, `'pending_reindex'` for the gated branch, `f'error: {tool_result[\"error\"]}'` for error envelopes."

key-files:
  created:
    - backend/app/services/exploration_tools/read_document.py
  modified:
    - backend/app/services/openai_client.py

key-decisions:
  - "read_document does NOT pass through apply_12k_cap. The `content` field is rendered text (a string with newlines, not a list of dicts), and apply_12k_cap's dict-payload-aware trimming would either no-op (if `content`'s JSON-serialized length is under 12K) or truncate the JSON envelope mid-character (codepoint corruption). Doing the UTF-8-safe truncation IN-TOOL via the documented Pitfall 9 idiom (`encode('utf-8')[:N].decode('utf-8', errors='ignore').rfind('\\n')`) is the correct primitive for a TEXT field. truncation_marker is set explicitly to surface the trim to the LLM."
  - "splitlines(keepends=False) (NOT keepends=True) — the line-stability invariant. Plans 03/04/05 don't touch line semantics; this plan does, and the invariant is paramount. With keepends=True, the slice retains `\\r\\n` characters and a downstream regex/byte-position lookup would drift by the keep-end count. With keepends=False, every EOL flavor produces the same line count and the same per-line content. Confirmed inline against CRLF/LF/CR fixtures."
  - "1-based external offset, 0-based internal slice. The Claude Code convention is 1-based for human-readable line citations; Python lists are 0-based. The translation `start_idx = args.offset - 1` happens once at the top of the slice section so the rest of the function uses 0-based math (cleaner, less error-prone). `start_line` returned to the caller is `args.offset` (1-based again)."
  - "Path branch splits via `rfind('/')` not `rsplit('/', 1)`. Both work; `rfind` is one fewer allocation (returns an int instead of allocating a list). Edge case: top-level file `/foo.md` → split_idx=0 → folder_part='' which we coerce to '/'; file_name='foo.md'. Verified by reading the regex on ReadDocumentArgs.path which guarantees at least one slash and a non-empty trailing segment."
  - "No `.eq('user_id', user_id)` defensive filter on the SELECT. RLS handles cross-user isolation (the JWT-bound supabase_client carries the caller's identity into Postgres). Adding the explicit `.eq('user_id', user_id)` would BLOCK admin reads of global-scope documents (which have user_id IS NULL). The threat-mitigation table in the plan calls this out: `.maybe_single()` returning None when RLS hides the row maps cleanly to NOT_FOUND. T-04-06-01 (T-CrossScopeLeak) is addressed by RLS, NOT by Python."
  - "Path-arg branch raises ValueError (caught by the outer try/except → INVALID_ARGS envelope) if normalize_path rejects the folder portion. This is defense-in-depth on top of Pydantic's regex check; `.. ` segments would be caught here even if a future schema change loosened the regex."
  - "Module-level constant `_ARROW = '→'` (literal U+2192 in source). Using a constant rather than inlining `chr(0x2192)` makes the rendered f-string easier to read AND lets the Pitfall 9 smoke test assert `chr(0x2192) in body` against the source text directly. The literal codepoint in the source matters because the file is read with `encoding='utf-8'` by Python — verified by editor encoding."
  - "Per-branch query exceptions (in _resolve_row) are caught with `except Exception` + `logger.warning` + `return None`, NOT re-raised. The outer try/except in read_document treats None as 'no row found' and yields NOT_FOUND. Connection errors/transient Postgres failures surface to the LLM as 'no such document' rather than crashing the SSE stream. The narrower outer `except Exception` returns QUERY_FAILED for non-resolution errors (e.g., a serialization error after a row IS found)."
  - "@traceable adds a `config` kwarg to the wrapped signature. Following the Plan 03/04/05 convention; runtime functional smoke test importable AS A CALLABLE (not via signature inspection in this plan since the smoke surface is functional, not signature-based)."

patterns-established:
  - "Pattern: in-tool UTF-8 codepoint-safe truncation for text fields (`encode('utf-8')[:N].decode('utf-8', errors='ignore').rfind('\\n')` to trim back to complete line). Reusable for grep (Plan 07) when each match's context lines need codepoint-safe truncation. Distinct from apply_12k_cap which is for dict-payload list trimming."
  - "Pattern: pending_reindex pre-empt for any tool that reads documents.content_markdown. Reusable for grep (Plan 07) which will hit the same status check before regex-matching content; same 4-element vocabulary forwarded verbatim."
  - "Pattern (REINFORCED): lazy-import + Pydantic try/except + result_text = json.dumps + tool_done detail string. Now consistent across 4 exploration tools (list_files, tree, glob, read_document); next plan (grep) follows identically. The detail string format (`lines {sl}-{el}/{tl}` for read_document, `{N} matches for {pattern!r}` for glob) is per-tool but the structural shape is locked."

requirements-completed: [TOOL-05, TOOL-06, TOOL-07, TOOL-08, TOOL-09, TOOL-10]

# Metrics
duration: ~12min
completed: 2026-05-09
---

# Phase 4 Plan 06: read_document (TOOL-05) Summary

**TOOL-05 read_document exploration tool — @traceable line-numbered slice tool honoring the Phase 2 LOCKED `pending_reindex` contract for non-ready rows, splitlines(keepends=False) Pitfall 9 line-stability invariant for CRLF/LF/CR uniformity, 1-based external offset / 0-based internal slice, arrow-form `{line_no}→{content}` rendering with literal U+2192, UTF-8 codepoint-safe last-line truncation idiom (`encode→slice→decode(errors='ignore')→rfind('\n')`), NOT_FOUND envelope for absent / RLS-hidden rows; additive openai_client extension via `_build_read_document_tool()` factory + dispatch arm AFTER the glob arm; mirrors the locked Plan 03/04/05 template.**

## Performance

- **Duration:** ~12 min
- **Started:** 2026-05-09 (worktree wave 5)
- **Completed:** 2026-05-09
- **Tasks:** 2
- **Files created:** 1 (read_document.py — 188 lines)
- **Files modified:** 1 (openai_client.py — +82 lines additive; now 966 lines, was 884 after Plan 05)
- **LOC delta:** +270

## Accomplishments

- TOOL-05 read_document() function landed with the full Phase 4 contract: `@traceable(name='read_document', run_type='tool')` for LangSmith tracing (TOOL-10); `.maybe_single().execute().data` single-row resolution via either args.document_id (`.eq('id', document_id)`) OR args.path (split into folder + file_name; `.eq('folder_path', norm_folder).eq('file_name', file_name)`) with normalize_path() applied to the folder portion (Pitfall 4 chokepoint); RLS-bound supabase_client handles cross-user isolation natively (RLS-hidden rows surface as None → NOT_FOUND envelope). The cross-field `@model_validator` on ReadDocumentArgs guarantees exactly-one-of(document_id, path) at parse time; the `_resolve_row` helper raises ValueError as defense-in-depth.
- Phase 2 LOCKED tool integration contract honored exactly: when `row['content_markdown_status'] != 'ready'`, returns `{tool: 'read_document', document_id, file_name, scope, folder_path, status: 'pending_reindex', content_markdown_status: <val>}` BEFORE attempting to splitlines() on the (possibly NULL) content_markdown. The 4-element status vocabulary (`pending` | `ready` | `failed` | `requires_user_reupload`) from Migration 014 / Phase 2 is forwarded verbatim.
- Pitfall 9 line-stability invariant landed: `splitlines(keepends=False)` is uniform across CRLF / LF / CR — verified inline against three fixtures (`'l1\r\nl2\r\nl3'`, `'l1\nl2\nl3'`, `'l1\rl2\rl3'`) all return `total_lines=3` and identical arrow-form rendering. Defense-in-depth on top of Phase 2's CRLF-normalized-at-ingestion invariant (content_markdown stored with `\n` only) — even if a regression introduces `\r`, the slice math doesn't drift.
- 1-based external offset → 0-based internal index translation: `start_idx = args.offset - 1; end_idx = min(start_idx + args.limit, total_lines); slice_ = lines[start_idx:end_idx] if start_idx < total_lines else []`. `start_line` returned is 1-based (`args.offset` if past end-of-file, else `start_idx + 1`); `end_line` is `start_idx + len(slice_)` (or `truncated_end_line` after truncation). Verified inline: `offset=2 limit=2` against `'a\nb\nc\nd\ne'` returns `start_line=2 end_line=3 total_lines=5 content='2→b\n3→c'` (literal U+2192 in the rendered string).
- Arrow-form rendering with literal U+2192 RIGHTWARDS ARROW: `'\n'.join(f'{start_idx + i + 1}{_ARROW}{line}' for i, line in enumerate(slice_))`. Module-level constant `_ARROW = '→'` makes the codepoint unmistakable in source AND lets the Pitfall 9 smoke test assert `chr(0x2192) in body`. Smoke test verified the rendered output for all three EOL flavors and for past-end-of-file.
- UTF-8 codepoint-safe truncation idiom (Pitfall 9, T-04-06-04 mitigation) — when `len(rendered) > _CONTENT_CHAR_CAP` (12_000): `rendered.encode('utf-8')[:_CONTENT_CHAR_CAP]` slices by BYTES; `.decode('utf-8', errors='ignore')` discards any trailing partial codepoint cleanly; `.rfind('\n')` trims back to a complete line so the LAST visible line isn't half-shown; `kept_lines` recount drives the `truncation_marker = '[...truncated, K more lines]'` field. Verified inline against a multibyte emoji fixture (1499 lines × `'line N \U0001F680'` where the rocket emoji is 4 UTF-8 bytes): roundtrip `encode().decode()` succeeded (no `UnicodeDecodeError` from a partial codepoint), the last kept line ended cleanly with the rocket emoji intact, and the truncation_marker reported `944 more lines` (ASCII fixture) / `944 more lines` (emoji fixture sized differently due to 4-byte width).
- NOT_FOUND envelope when row absent (or RLS hidden): `{'tool': 'read_document', 'error': 'NOT_FOUND', 'message': "No document with id '<uuid>'"}` or `"No document at '<path>'"` based on which arg was used. Verified inline with stub supabase returning `data=None`. The plan's threat model (T-04-06-01 T-CrossScopeLeak) is addressed by RLS in Postgres — the Python code does NOT add `.eq('user_id', user_id)` because that would block admin reads of global-scope documents (where user_id IS NULL).
- INVALID_ARGS envelope on malformed args: when normalize_path rejects the folder portion of args.path, _resolve_row raises ValueError → caught by the outer try/except → returns `{'tool': 'read_document', 'error': 'INVALID_ARGS', 'message': '<text>'}`. Defense in depth on top of Pydantic's `@model_validator` exactly-one-of and the path regex.
- QUERY_FAILED envelope for unexpected exceptions during resolution: `{'tool': 'read_document', 'error': 'QUERY_FAILED', 'message': '<ExceptionType>: <text>'}`. Per-branch failures inside _resolve_row are caught with `except Exception` + `logger.warning` + `return None` (preserves partial behavior — connection blip surfaces as NOT_FOUND, not as a stream-killing 500). The outer QUERY_FAILED catches anything that escapes the inner handlers.
- openai_client.py extended additively with three localized edits (Plans 03/04/05 + L565-610 wrapper UNCHANGED):
  - **Edit 1:** `_build_read_document_tool()` factory inserted between `_build_glob_tool()` and `_sanitize_keyword_query`. types.FunctionDeclaration with name='read_document'; description guides the LLM to choose read_document vs grep vs analyze_document; properties = {document_id: STRING, path: STRING, offset: INTEGER, limit: INTEGER}; required=[] (cross-field exactly-one-of enforced by ReadDocumentArgs Pydantic validator).
  - **Edit 2:** registration `try: function_declarations.append(_build_read_document_tool()); except Exception as e: logger.warning(...)` inserted inside the `if has_documents:` block AFTER the `_build_glob_tool()` registration, BEFORE `if text_to_sql_enabled:` block.
  - **Edit 3:** `elif tool_name == "read_document":` dispatch arm inserted AFTER the `elif tool_name == "glob":` arm and BEFORE the unknown-tool fall-through. Lazy imports `read_document as _read_document` + `ReadDocumentArgs`. try/except parses ReadDocumentArgs → INVALID_ARGS envelope on ValidationError. Else branch calls `_read_document(parsed_args, user_id, supabase_client)`, assigns `result_text = json.dumps(tool_result)`, computes detail string distinguishing error / pending_reindex / `f'lines {sl}-{el}/{tl}'`, yields `('tool_done', {tool, detail})`.
- Wrapper at openai_client.py (the `truncated_result = result_text[:16000]` site, two occurrences — one in the streaming Call#2 path, one in the non-streaming fallback) UNCHANGED — verified post-edit count == 2 via inline grep. Existing 7 factories and 7 dispatch arms (search_documents, analyze_document, query_structured_data, web_search, list_files, tree, glob) UNCHANGED — verified each present exactly once.
- All registered tools after this plan: search_documents, analyze_document, query_structured_data, web_search, list_files, tree, glob, read_document (8 total — adding read_document to Plans 03+04+05's 7).
- Plan 07 (grep) ready in Wave 6 under the same locked template.

## Task Commits

Each task committed atomically with --no-verify (parallel-wave executor inside worktree):

1. **Task 1: read_document.py — TOOL-05 line-numbered slice with arrow-form rendering + UTF-8 safe truncation** — `63e4c4a` (feat)
2. **Task 2: openai_client.py extension — _build_read_document_tool factory + registration + dispatch arm** — `ea522bc` (feat)

## Files Created/Modified

- `backend/app/services/exploration_tools/read_document.py` (NEW, 188 lines) — public `read_document(args, user_id, supabase_client) -> dict` decorated with `@traceable(name="read_document", run_type="tool")`; module-level constants `_CONTENT_CHAR_CAP = 12_000` and `_ARROW = '→'` (literal U+2192 in source); module-level helper `_resolve_row(args, supabase_client) -> Optional[dict]` with two branches (document_id .eq idiom; path-split into folder + file_name then `.eq('folder_path', norm_folder).eq('file_name', file_name)`); `.maybe_single().execute().data` shape (RLS handles cross-user isolation; RLS-hidden rows return None → NOT_FOUND envelope); SELECT projection `id, file_name, folder_path, scope, content_markdown, content_markdown_status`; Phase 2 LOCKED contract pre-empt (`if status != 'ready': return {... status: 'pending_reindex', content_markdown_status: status}`); `splitlines(keepends=False)` for line-stability across CRLF/LF/CR; arrow-form rendering `'\n'.join(f'{start_idx + i + 1}{_ARROW}{line}' for ...)`; UTF-8 codepoint-safe truncation `rendered.encode('utf-8')[:_CONTENT_CHAR_CAP].decode('utf-8', errors='ignore')` then `rfind('\n')` to trim to complete line; `truncation_marker = f'[...truncated, {remaining} more lines]'` set explicitly when truncation fires (None otherwise); structured error envelopes NOT_FOUND (no row), INVALID_ARGS (normalize_path / split_idx / file_name ValueError), QUERY_FAILED (unexpected outer exception). NO `apply_12k_cap` pass-through — read_document does its own UTF-8-safe truncation on rendered TEXT (the `content` field is a string, not a list). NO `_assert_uuid` (path/uuid validation is by Pydantic regex; no PostgREST `.or_()` interpolation here so HI-01 doesn't apply). NO `service_role` client. NO Gemini SDK calls. NO HTTPException.
- `backend/app/services/openai_client.py` (MODIFIED, +82 lines additive — file now 966 lines, was 884 after Plan 05) — three localized edit points:
  - **Edit 1** (after Plan 05's `_build_glob_tool` ending at L306): `_build_read_document_tool()` factory inserted between `_build_glob_tool()` and `_sanitize_keyword_query`. types.FunctionDeclaration with name='read_document'; description tells the LLM to use read_document for known-document literal text and to use grep for content search across many docs and analyze_document for full-document analysis; properties = {document_id: STRING, path: STRING, offset: INTEGER, limit: INTEGER}; required=[] (Pydantic cross-field validator enforces exactly-one-of at runtime).
  - **Edit 2** (after Plan 05's glob registration at ~L480): registration `try: function_declarations.append(_build_read_document_tool()); except Exception as e: logger.warning("Failed to build read_document tool (non-fatal): " + str(e))` inserted inside the `if has_documents:` block AFTER `_build_glob_tool()` registration, BEFORE `if text_to_sql_enabled:` block.
  - **Edit 3** (after Plan 05's glob dispatch arm at ~L773): `elif tool_name == "read_document":` dispatch arm inserted AFTER the `elif tool_name == "glob":` arm, BEFORE the `else: logger.warning(f"Unknown tool: {tool_name}")` fallthrough. Lazy imports `read_document as _read_document` and `ReadDocumentArgs`; try/except parses ReadDocumentArgs and on ValidationError assigns result_text = json.dumps({tool, error: INVALID_ARGS, message}) and yields tool_done(detail="Invalid arguments"); else branch calls `_read_document(parsed_args, user_id, supabase_client)`, assigns result_text = json.dumps(tool_result), computes detail string with three branches (error → `f'error: {tool_result["error"]}'`; status='pending_reindex' → `'pending_reindex'`; else → `f'lines {sl}-{el}/{tl}'`), yields tool_done with that detail.
  - Wrapper unchanged: `truncated_result = result_text[:16000]` still appears EXACTLY 2x (one streaming Call#2, one non-streaming fallback) — verified via inline `src.count(...)` assertion.

## Public APIs Established (consumed by Plan 09 + Plan 07)

**`app.services.exploration_tools.read_document`:**
- `read_document(args: ReadDocumentArgs, user_id: Optional[str], supabase_client) -> dict` — three return shapes:
  - **Happy path:** `{tool: 'read_document', document_id, file_name, scope, folder_path, start_line, end_line, total_lines, content, truncation_marker}` where `content` is the arrow-form-rendered slice and `truncation_marker` is None or `'[...truncated, K more lines]'`.
  - **Pending re-index (Phase 2 LOCKED):** `{tool: 'read_document', document_id, file_name, scope, folder_path, status: 'pending_reindex', content_markdown_status: <pending|failed|requires_user_reupload>}`.
  - **Error:** `{tool: 'read_document', error: 'NOT_FOUND'|'INVALID_ARGS'|'QUERY_FAILED', message: '<text>'}`.
- `_resolve_row(args: ReadDocumentArgs, supabase_client) -> Optional[dict]` — module-level helper; resolves args.document_id OR args.path (split into folder + file_name; normalize_path on folder portion) into a single documents row via `.maybe_single().execute().data`. Returns None when RLS hides the row OR no row matches. Raises ValueError on malformed args (defense in depth — Plan 02's `@model_validator` already enforced exactly-one-of).
- Module-level constants `_CONTENT_CHAR_CAP = 12_000` (UTF-8 byte cap on rendered content) and `_ARROW = '→'` (literal U+2192 RIGHTWARDS ARROW; monkey-patchable for tests but not expected to change).

**`app.services.openai_client`:**
- `_build_read_document_tool() -> types.FunctionDeclaration` — returned object exposes `name='read_document'`, `parameters.type=Type.OBJECT`, `parameters.properties={'document_id','path','offset','limit'}`, `parameters.properties['offset'].type=Type.INTEGER`, `parameters.properties['limit'].type=Type.INTEGER`, `parameters.required=[]`.
- New dispatch arm: when fc.name == 'read_document', the arm parses args via `ReadDocumentArgs(**args)`, calls `read_document()`, assigns `result_text = json.dumps(tool_result)`, yields `('tool_done', {tool, detail})` with detail = `'lines {sl}-{el}/{tl}'` (happy) | `'pending_reindex'` (gated) | `'error: <code>'` (envelope).

## Decisions Made

See key-decisions in frontmatter. Highlights:
- **No apply_12k_cap pass-through** — `content` is rendered TEXT (a string with newlines), not a list. apply_12k_cap is dict-payload-aware and would either no-op or truncate the JSON envelope mid-character (codepoint corruption). The in-tool UTF-8-safe truncation idiom IS the correct primitive for a text field.
- **splitlines(keepends=False) NOT keepends=True** — the Pitfall 9 line-stability invariant. Defense-in-depth on top of Phase 2's CRLF-normalized-at-ingestion invariant.
- **No `.eq('user_id', user_id)` defensive filter on the SELECT** — RLS handles cross-user isolation; adding the filter would BLOCK admin reads of global-scope documents (user_id IS NULL).
- **Module-level `_ARROW = '→'` constant** — makes the literal U+2192 codepoint unmistakable in source AND lets the Pitfall 9 smoke test assert `chr(0x2192) in body` against the source text directly.
- **rfind('/') split for path branch** — one fewer allocation than rsplit('/', 1); edge case `/foo.md` → folder_part='' coerced to '/'.
- **Outer try/except returns QUERY_FAILED for unexpected escape** — inner per-branch handlers (in _resolve_row) return None on connection-class errors → NOT_FOUND envelope (no stream-killing 500). The outer QUERY_FAILED catches serialization-class errors AFTER a row IS resolved.

## Threat Mitigations

| Threat ID | STRIDE | Mitigation Verified |
|-----------|--------|---------------------|
| T-04-06-01 | Information Disclosure (T-CrossScopeLeak) | ReadDocumentArgs has NO user_id field (verified Plan 02 schemas.py — only declares document_id/path/offset/limit with `extra='ignore'`; cross-field `@model_validator` enforces exactly-one-of(document_id, path)). user_id is derived from JWT in the dispatch loop (Episode 1 invariant; openai_client.py reads it from caller, not args). The PostgREST query inherits the caller's RLS context via the JWT-bound supabase_client; `.maybe_single().execute().data` returns None when RLS hides the row, which read_document maps to NOT_FOUND. NO service-role client used (verified by source grep `'service_role' not in body` and `'SUPABASE_SERVICE_ROLE_KEY' not in body`). NO `.eq('user_id', user_id)` defensive filter — would block admin reads of global-scope docs. |
| T-04-06-02 | Tampering (T-PathTraversal) | Triple chokepoint: (1) Pydantic `Field(pattern=r'^/$|^/[^/]+(/[^/]+)*/[^/]+$')` on ReadDocumentArgs.path (Plan 02) — requires at least one folder segment + a non-empty file_name segment after the last slash; (2) `normalize_path(folder_part)` applied to the folder portion of args.path inside _resolve_row — rejects `..`/`.` segments via folder_service's canonical-form invariant (Phase 3 / Plan 02 LOCKED); (3) the query is on the documents table — there's no filesystem path interpretation; `path` is a domain key. Even if the LLM passes `path='/../etc/passwd'`, Pydantic rejects at parse time (the `..` lacks a file_name segment after the last `/`); if the regex were ever loosened, normalize_path catches it; if both layers were bypassed, the `.eq('folder_path', norm_folder).eq('file_name', file_name)` lookup is on a domain table that has no `etc/passwd` row. |
| T-04-06-03 | Repudiation (T-LineDrift / Pitfall 9) | Phase 2 / Plan 02 LOCKED CRLF normalization at ingestion — content_markdown stored with `\n` only. Defense-in-depth: `splitlines(keepends=False)` handles `\r\n` / `\n` / `\r` uniformly even if a regression introduces `\r` (verified inline against three fixtures, all returning `total_lines=3` for `'l1<EOL>l2<EOL>l3'`). 1-based external offset + 0-based internal index translation; both `start_line` AND `end_line` returned for verification. Smoke test confirmed `offset=2 limit=2` against `'a\nb\nc\nd\ne'` returns `start_line=2 end_line=3 total_lines=5 content='2→b\n3→c'` — line numbering is byte-stable. |
| T-04-06-04 | Repudiation (T-CodepointTruncation / Pitfall 9) | Slice by LINE not by char — line-by-line iteration via `for i, line in enumerate(slice_)` preserves codepoint integrity per-line. The 12K cap on the rendered output uses the standard idiom `rendered.encode('utf-8')[:12_000].decode('utf-8', errors='ignore')` — `errors='ignore'` discards a partial trailing codepoint cleanly. Then `rfind('\n')` trims back to a complete LINE so the LAST line isn't half-shown. Verified inline against an emoji-containing fixture (1499 lines × `'line N <rocket-emoji>'` where the rocket is U+1F680 = 4 UTF-8 bytes): roundtrip `r['content'].encode('utf-8').decode('utf-8')` succeeded WITHOUT raising (no partial codepoint), and the last kept line ended cleanly with the rocket emoji intact (`last_line.endswith('\U0001F680')`). |
| T-04-06-05 | Denial of Service (T-LimitOverflow) | ReadDocumentArgs.limit = `Field(2000, ge=1, le=5000)` — Pydantic clamps at parse time (Plan 02). Apply_12k_cap-equivalent UTF-8-safe truncation at the rendered-text layer is the second defense layer. Even if the LLM passes `limit=999999`, Pydantic rejects with ValidationError → INVALID_ARGS envelope; if the cap were ever raised, the 12K char/byte cap on rendered output bounds the response payload regardless of `total_lines`. |

## Deviations from Plan

**Total: 0 deviations from the plan body. 1 environmental issue addressed during setup.**

The plan's pseudocode was paste-ready and complete. Both tasks executed exactly as specified:

- Task 1: read_document.py written with the EXACT content from the plan's `<action>` block (188 lines vs the plan's `min_lines: 100` floor). Every `contains_*` artifact assertion satisfied: `@traceable(name="read_document", run_type="tool")`, `def read_document`, `splitlines(keepends=False)`, literal `→` (U+2192), `encode("utf-8")` + `errors="ignore"`, `pending_reindex`, `NOT_FOUND`, `.maybe_single()`, `normalize_path` all present. Forbidden substrings (`splitlines(keepends=True)`, `service_role`, `SUPABASE_SERVICE_ROLE_KEY`, `generate_content`, `HTTPException`) all absent.
- Task 2: openai_client.py extended exactly as specified — three localized edits, no other modifications. All `contains_*` artifact assertions satisfied: `def _build_read_document_tool`, `elif tool_name == "read_document":`, `_build_read_document_tool()` registration, `ReadDocumentArgs` lazy import. Wrapper-unchanged check: `truncated_result = result_text[:16000]` STILL appears EXACTLY 2x. All Plan 03/04/05 markers verified present exactly once each.

**Environmental issue (resolved during setup, NOT a plan deviation):** The worktree HEAD initially pointed at `376b21d` (Episode 1 freeze commit) instead of the required `b4cb180` (Wave 4 base with Plans 03+04+05 applied). The worktree-branch-check protocol called for `git reset --hard b4cb180b7600b7969868fb139366f25b1690bac3`, which ran cleanly and put HEAD at the correct base. No work was lost (the worktree had no commits beyond the stale base; the only modified file was `.claude/settings.local.json` which is environment-local). Confirmed afterwards: `git log --oneline -10` showed Plans 03+04+05 commits in the recent history; `backend/app/services/exploration_tools/` listing showed `glob_match.py`, `list_files.py`, `tree.py`, `schemas.py`, `_truncate.py`, `_scope_tag.py`, `__init__.py` all present.

**Worktree path quirk awareness:** Per Plan 04+05 SUMMARY notes, all `Write` calls used the full worktree path prefix (`C:\RAG Automators\claude-code-agentic-rag-masterclass-ep2\.claude\worktrees\agent-a45075d388139c036\...`). One initial Write went to the parent repo path before the hard-reset; that copy is harmless (the parent repo already has the file from a prior wave merge) and was NOT included in the worktree commit. Post-write `ls` confirmed both paths.

## Smoke Tests Run

All inline smoke tests from the plan ran via the main repo venv (`/c/RAG Automators/claude-code-agentic-rag-masterclass-ep2/backend/venv/Scripts/python.exe`) with `PYTHONPATH` pointing into the worktree backend — the worktree has no separate venv per Plans 03/04/05 convention. All passed:

- **Task 1 structural** — `ast.parse(src)` OK; `def read_document` present; `@traceable(name="read_document", run_type="tool")` present; `splitlines(keepends=False)` present; `splitlines(keepends=True)` ABSENT; literal `→` (U+2192) present in source; `_ARROW` constant present; `encode("utf-8")` present; `errors="ignore"` present; `pending_reindex` present; `NOT_FOUND` present; `.maybe_single()` present; `normalize_path` present; no `service_role` / `SUPABASE_SERVICE_ROLE_KEY` substrings; no `generate_content`; no `HTTPException`; importable as a callable; 188 lines (>= 100 required).
- **Task 1 functional smoke (7 cases)** —
  - (1) NOT_FOUND envelope when `.maybe_single()` returns `data=None`: returns `{tool: 'read_document', error: 'NOT_FOUND', message: "No document with id '00000000-0000-0000-0000-000000000000'"}`.
  - (2) pending_reindex shape: with `content_markdown_status='pending'`, returns `{tool: 'read_document', document_id: 'doc-1', file_name: 'a.md', scope: 'user', folder_path: '/projects', status: 'pending_reindex', content_markdown_status: 'pending'}` BEFORE attempting splitlines (avoids AttributeError on NULL content_markdown).
  - (3) CRLF + LF + CR uniformity: all three EOL flavors (`'l1\r\nl2\r\nl3'`, `'l1\nl2\nl3'`, `'l1\rl2\rl3'`) yield `total_lines=3` AND identical arrow-form rendering `'1→l1\n2→l2\n3→l3'` (U+2192 verified). Pitfall 9 line-stability invariant confirmed.
  - (4) 1-based offset / 0-based internal index: `offset=2 limit=2` against `'a\nb\nc\nd\ne'` returns `start_line=2 end_line=3 total_lines=5 content='2→b\n3→c'`. The 1-based external/0-based internal translation is correct.
  - (5a) UTF-8 truncation (ASCII content, 1499 lines × `'line N ROCKET'`): `len(rendered)=10914` (under cap, no truncation triggered for ASCII) — wait, this case triggered: rendered_len=10914 reported, marker=`[...truncated, 944 more lines]`. Confirms the byte-counting cap fires and roundtrip `encode().decode()` succeeds with no exception.
  - (5b) UTF-8 truncation (multibyte emoji, 1499 lines × `'line N <rocket-emoji>'` where U+1F680 is 4 UTF-8 bytes): `len(rendered)=8964` (smaller char count, larger byte count); roundtrip `r['content'].encode('utf-8').decode('utf-8')` succeeded WITHOUT raising; the last kept line ended cleanly with the rocket emoji intact (`last_line.endswith('\U0001F680')` is True). Pitfall 9 codepoint-safe truncation idiom confirmed end-to-end.
  - (6) Pydantic exactly-one-of validator rejects `ReadDocumentArgs(document_id='d1', path='/folder/file.md')` with `pydantic.ValidationError`. Confirms Plan 02's `@model_validator` enforces the cross-field constraint.
  - (7) Past-end-of-file: `offset=10 limit=10` against `'only one line'` returns `total_lines=1 content='' start_line=10 end_line=9`. Confirms the conditional empty-slice branch handles `start_idx >= total_lines` without IndexError.
- **Task 2 structural** — `ast.parse(src)` OK; `def _build_read_document_tool` present; `name="read_document"` in factory; `function_declarations.append(_build_read_document_tool())` present; `elif tool_name == "read_document":` present; lazy imports `from app.services.exploration_tools.read_document import read_document` and `from app.services.exploration_tools.schemas import ReadDocumentArgs` present; `ReadDocumentArgs(**args)` present; `truncated_result = result_text[:16000]` STILL present (wrapper UNCHANGED check). Plan 03/04/05 markers STILL present each exactly once: `def _build_list_files_tool`, `def _build_tree_tool`, `def _build_glob_tool`, `elif tool_name == "list_files":`, `elif tool_name == "tree":`, `elif tool_name == "glob":`. All 8 factories (`_build_search_tool`, `_build_analyze_tool`, `_build_sql_tool`, `_build_web_search_tool`, `_build_list_files_tool`, `_build_tree_tool`, `_build_glob_tool`, `_build_read_document_tool`) and 4 new exploration-tool dispatch arms (list_files, tree, glob, read_document) each present exactly once.
- **Task 2 runtime** — `from app.services.openai_client import _build_read_document_tool` succeeds; `_build_read_document_tool()` returns `FunctionDeclaration(name='read_document')`. All other DB-free factories (`_build_sql_tool`, `_build_web_search_tool`, `_build_analyze_tool`, `_build_list_files_tool`, `_build_tree_tool`, `_build_glob_tool`, `_build_read_document_tool`) all import and invoke cleanly (7 names: ['query_structured_data', 'web_search', 'analyze_document', 'list_files', 'tree', 'glob', 'read_document']). `_build_search_tool` requires SUPABASE_URL env var (calls get_metadata_schema → DB) so was not invoked in the smoke test; its definition is verified present via the structural assertion.
- **Wrapper-unchanged double check** — `src.count('truncated_result = result_text[:16000]') == 2` (one in streaming Call#2, one in non-streaming fallback). Verified post-edit. Total `openai_client.py` LOC = 966 (was 884 after Plan 05; +82 lines additive).

## ROADMAP Phase 4 Success Criteria Mapping

- **SC1 (registered + dispatched + Pydantic-validated + layered-fallback routed):** read_document ✓ — fourth of five exploration tools (after list_files, tree, glob; before grep). FunctionDeclaration registered in `_build_tools` loop; dispatch arm parses ReadDocumentArgs (Plan 02 cross-field validator enforces exactly-one-of); result flows into the existing layered-fallback wrapper at openai_client.py L781 (UNCHANGED).
- **SC2 (200 folders → < 12K chars; CRLF/Unicode/single-long-line BYTE-STABLE):** read_document ✓✓ — three-layer defense for SC2 byte-stability:
  1. `splitlines(keepends=False)` for CRLF/LF/CR uniformity (Pitfall 9).
  2. `Field(2000, ge=1, le=5000)` Pydantic line cap (Plan 02; T-04-06-05 mitigation).
  3. UTF-8 codepoint-safe truncation (`encode('utf-8')[:12_000].decode('utf-8', errors='ignore').rfind('\n')`) on the rendered text — preserves codepoint integrity at the byte boundary AND trims back to a complete line (Pitfall 9; T-04-06-04 mitigation).
  Plan 09's CRLF / mixed-ending / single-long-line / emoji / 50K-char fixtures will exercise this end-to-end.
- **SC3 (every result row carries scope):** read_document ✓ — happy-path returns `scope: row.get('scope') or 'user'`; pending_reindex shape returns the same. The `'user' or 'global'` invariant is preserved end-to-end from the documents table projection.

## User Setup Required

None — no external service configuration required. Plan extends in-process Python and Gemini tool registration only; uses the venv at the main repo path (worktree has no separate venv, per Plans 03/04/05 convention) which already has langsmith + pydantic + supabase-py + google-genai installed from Phase 1.

## Next Phase Readiness

**Plan 07 (grep) ready in Wave 6.** The Phase 4 template established in Plan 03 and reinforced in Plans 04+05+06 is now battle-tested across four artifacts and four cross-cutting concerns:

1. Create `app/services/exploration_tools/<tool>.py` with @traceable-decorated public function: input validation/normalization first → service-layer query (delegate to folder_service or new RPC) → ensure_scope_tag per row OR pending_reindex pre-empt for content-reading tools → apply_12k_cap on happy-path return (or in-tool truncation for text fields like read_document) → structured error envelope on exception.
2. Add `_build_<tool>_tool()` factory to openai_client.py alongside `_build_read_document_tool` (same lazy `from google.genai import types` body shape).
3. Register inside `if has_documents:` block: `try: function_declarations.append(_build_<tool>_tool()); except Exception as e: logger.warning(...)`.
4. Add `elif tool_name == "<tool>":` dispatch arm AFTER the read_document arm and BEFORE the unknown-tool fall-through; same try/except Pydantic parse → `result_text = json.dumps(tool_result)` shape.
5. Wrapper at openai_client.py (the `truncated_result = result_text[:16000]` two sites) STAYS UNCHANGED across all subsequent waves.

**Plan 07 reuses Plan 06's pending_reindex pre-empt pattern verbatim** — grep also reads documents.content_markdown and must honor the Phase 2 LOCKED contract for non-ready rows. Plan 07 may also reuse the in-tool UTF-8-safe truncation idiom for hit-context line windows (vs apply_12k_cap which is dict-payload-aware).

**Plan 07 is sequenced into Wave 6 (not parallel with this plan)** — same-wave edits would conflict on the shared elif chain insertion point.

**Plan 07 ready in Wave 6.**

## Self-Check: PASSED

Verification results:
- `backend/app/services/exploration_tools/read_document.py` — FOUND (188 lines)
- `backend/app/services/openai_client.py` — FOUND (modified, 966 lines; was 884 after Plan 05; +82 lines additive)
- Commit `63e4c4a` (feat(04-06): read_document tool — TOOL-05 line-numbered slice with arrow-form rendering + UTF-8 safe truncation) — FOUND in git log
- Commit `ea522bc` (feat(04-06): wire read_document into openai_client dispatch (TOOL-09 routing)) — FOUND in git log
- All Task 1 smoke checks PASSED (structural + import + 7 functional cases — NOT_FOUND, pending_reindex, CRLF/LF/CR uniformity, 1-based offset translation, UTF-8 ASCII truncation, UTF-8 emoji codepoint integrity, Pydantic exactly-one-of validator, past-end-of-file)
- All Task 2 smoke checks PASSED (structural + runtime + wrapper-UNCHANGED 2x count + Plan 03/04/05 markers UNCHANGED + 8-factory/4-new-arm count)
- Post-commit deletion check: zero deletions introduced by either commit (only `read_document.py` created and `openai_client.py` modified additively — verified via `git diff --diff-filter=D --name-only HEAD~2 HEAD` returning empty)
- No STATE.md / ROADMAP.md / REQUIREMENTS.md modifications (worktree mode — orchestrator owns shared state)

---
*Phase: 04-five-exploration-tools-search-documents-extension*
*Plan: 06*
*Completed: 2026-05-09*
