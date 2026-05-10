---
status: issues_found
phase: 05
files_reviewed: 7
files_reviewed_list:
  - backend/app/services/sub_agent.py
  - backend/app/services/openai_client.py
  - backend/app/routers/messages.py
  - frontend/src/lib/api.ts
  - frontend/src/pages/Chat.tsx
  - backend/scripts/test_explorer_sub_agent.py
  - backend/scripts/test_all.py
depth: standard
critical: 0
warning: 6
info: 7
total: 13
created: 2026-05-10T00:00:00Z
---

# Phase 5 — Code Review Report

**Reviewed:** 2026-05-10
**Depth:** standard
**Files Reviewed:** 7
**Status:** issues_found

## Summary

Phase 5 ships the Explorer sub-agent (`run_explorer_sub_agent`), the
generalized SSE sub-agent envelope, and corresponding frontend plumbing. The
LOCKED invariants called out in the phase context (recursion-ban triple-layer,
`for ... range(MAX_TURNS)` bound, `_signature` body, `@traceable` decorator,
dual-emit on all five SSE arms, the 16K wrapper bit-identity at the openai_client
context-injection sites) are all observed correctly and are NOT flagged here.

What I did flag: a logging defect in the legacy `run_sub_agent` that prints a
nonsense pre-truncation length, a fragile partial-content path in the Explorer
summary stream that drops in-flight tokens on streaming error, an anchor-to-LAST-
slot accumulator in `messages.py` that becomes incorrect when a future
sub-agent emits inner-tool events out of strict sequential order, several
defensive holes around malformed Gemini responses, and a few quality items
(dead variable, unused parameters, brittle SSE line parser, double-encode risk
on JSONB write). No critical / blocker issues found. The recursion-ban triple
defense and the dual-emit envelope itself are correct.

## Warnings

### WR-01: `run_sub_agent` truncation log prints post-truncation length, not original

**File:** `backend/app/services/sub_agent.py:308-310`
**Severity:** warning
**Issue:** Inside the legacy `run_sub_agent` (Module 8 carry-forward), the
truncation guard reassigns `full_text` to its truncated form on line 309, then
on line 310 logs `from {len(full_text)} chars to {MAX_CONTEXT_CHARS}`. By that
point `len(full_text)` is `MAX_CONTEXT_CHARS + len("\n\n[... document truncated due to length ...]")`,
not the pre-truncation length. The log message is therefore meaningless for
diagnosing oversized documents — it always reports a value barely larger than
the cap.
**Fix:**
```python
if len(full_text) > MAX_CONTEXT_CHARS:
    original_len = len(full_text)
    full_text = full_text[:MAX_CONTEXT_CHARS] + "\n\n[... document truncated due to length ...]"
    logger.warning(
        f"Document '{document_name}' truncated from {original_len} chars to {MAX_CONTEXT_CHARS}"
    )
```

### WR-02: Explorer summary streaming error discards already-streamed tokens

**File:** `backend/app/services/sub_agent.py:540-546`
**Severity:** warning
**Issue:** When `client.models.generate_content_stream` raises midway through
the compact-summary path, any chunks ALREADY yielded as `sub_agent_token`
events have been emitted to the SSE stream and accumulated into `full_summary`.
The handler then checks `if not full_summary:` and only overwrites
`full_summary` with the error string when nothing was streamed. When chunks
were streamed but the stream then errored, `full_summary` keeps the partial
text and the user receives a `sub_agent_done` carrying the truncated partial
with no indication that the summary is incomplete. The client cannot tell the
difference between a complete short summary and a half-streamed-then-aborted
one.
**Fix:** Append a marker when partial content was streamed before failure:
```python
except Exception as e:
    logger.error(f"Explorer summary streaming failed: {e}", exc_info=True)
    if not full_summary:
        full_summary = (
            f"Exploration ended ({short_circuit_reason or 'complete'}); "
            f"summary unavailable due to streaming error."
        )
    else:
        # Partial content already streamed — annotate so the caller sees the gap.
        full_summary += f"\n\n[Summary truncated: {type(e).__name__}]"
```

### WR-03: `tool_metadata` slot anchored to `tools_used[-1]` is unsafe when sub-agents interleave

**File:** `backend/app/routers/messages.py:128-204`
**Severity:** warning
**Issue:** All four arms — `sub_agent_tool_start`, `sub_agent_tool_done`,
`sub_agent_token`, `sub_agent_done` — index into `tool_metadata["tools_used"][-1]`
and assume the LAST slot is the active sub-agent. Today this works only because
(a) the main agent calls one tool per turn and (b) `analyze_document` never
emits `tool_*` events. The moment a future sub-agent design (or a malicious
SSE producer) interleaves events from two sub-agents — e.g. an analyze_document
finishes and a parallel Explorer's tool_done arrives later — the result_preview
will be written to the wrong slot. The frontend already passes `sub_agent_id`
end-to-end (line 99-110); the persistence path should match by id, not by
position. The same is true for `agent_name` resolution at line 173-175 and
192-196 (uses LAST slot's `tool` field as discriminator for token/done events
that carry no agent_name themselves).
**Fix:** Have the generator pass `sub_agent_id` (or at least `agent_name`) on
`sub_agent_tool_start`, `sub_agent_tool_done`, `sub_agent_token`, and
`sub_agent_done` payloads, then look up the slot by id rather than `[-1]`. For
Phase 5's actual scope (single-sub-agent-at-a-time), the bug is latent — fold
this into Phase 6 generalization rather than ship as-is.

### WR-04: `_extract_function_call`-None path bypasses summary streaming and `short_circuit_reason`

**File:** `backend/app/services/sub_agent.py:436-446`
**Severity:** warning
**Issue:** On the natural-finish path (model emitted plain text instead of a
function_call on its first turn), the code yields `summary` as a single
`sub_agent_token` then a `sub_agent_done` and returns immediately. This skips
the compact-summary streaming call entirely AND skips the `Status: complete`
header in `summary_system`. The result: the natural-finish summary has whatever
shape the model emitted (often verbose), and downstream consumers (LangSmith
chain spans, the frontend's progressive rendering) see a single-chunk dump
rather than a streamed compact summary. This is a behavioral inconsistency
relative to the bounded-loop exit path.
**Fix:** Either (a) document this as intentional and add a comment, or (b)
keep `summary` as the model's preferred answer ONLY for natural-finish, but
still pass the compact-format constraint via a second streaming call when the
text is over some heuristic length (e.g. > 1500 chars). Minimum: tighten the
comment on line 437-442 to call out that no compact-format pass occurs and
that the model is trusted to have followed the system prompt's compact-summary
format on its own.

### WR-05: `tool_metadata` is double-JSON-encoded into a JSONB column

**File:** `backend/app/routers/messages.py:222`
**Severity:** warning
**Issue:** The column is declared `JSONB` (migration `010_sub_agents.sql`),
but the insert calls `json.dumps(tool_metadata)` first. PostgREST happens to
accept the resulting string and parse it back into JSONB — which is why
TEST-03 Section 8 round-trips and the test even handles "may already be a
dict or a JSON string depending on driver" (line 1010-1017). But a parser
upgrade or a strict-mode change could break this silently. Passing the dict
directly is the documented-correct pattern for supabase-py / PostgREST JSONB
columns.
**Fix:** Drop the wrapper:
```python
if tool_metadata:
    insert_data["tool_metadata"] = tool_metadata  # JSONB accepts dict directly
```
Verify with TEST-03 Section 8 (it already exercises the round-trip) and
test_sub_agents.py's `tool_metadata is populated` test.

### WR-06: SSE line parser splits on `\n` only and does not coalesce multi-line `data:` segments

**File:** `frontend/src/lib/api.ts:269-283`
**Severity:** warning
**Issue:** The reader does `buffer.split('\n')` and treats each line that
starts with `data:` as one complete event. SSE per spec uses `\r\n` line
terminators and allows multiple `data:` lines per event (joined with `\n`
before delivery). This implementation works for `sse-starlette`'s current
output (single-line JSON, plain `\n`), but if the backend ever emits
`\r\n` (e.g. through a CDN / reverse-proxy that re-terminates lines) or a
`data:` event ever spans two lines (large JSON payload that the server
flushes mid-line), parsing silently drops content. A trimmed-line check
on line 282 (`const trimmed = line.trim()`) hides the `\r` issue today.
**Fix:** Use a proper SSE parser, or at minimum:
1. Split events on `\n\n` (event delimiter) rather than `\n` (line delimiter)
2. Within an event, concatenate all `data:` lines with `\n` before parsing
3. Continue handling the trailing-buffer pattern (last partial event awaits
   the next chunk)

This is a robustness item — current backend wire format will not exercise
the bug.

## Info

### IN-01: Unused `original_dispatch` capture in test fixture

**File:** `backend/scripts/test_explorer_sub_agent.py:488,552`
**Severity:** info
**Issue:** Both `_section_2_max_turns` and `_section_3_wall_clock` capture
`original_dispatch = sa._dispatch_explorer_tool` and restore it in `finally`.
That's correct. The same sections also capture `original_get_client` but
in section 3 they reference `oc._get_client` directly (line 552 + 575), while
section 2 wraps the access in `if original_get_client:` guards (line 508,
518) that are not wrong but redundant given the import succeeds at module
top. Minor inconsistency; consider unifying the pattern across sections.
**Fix:** Drop the `if original_get_client:` guards in section 2 — the import
at line 488-494 will have raised before we got here if `oc._get_client`
weren't available.

### IN-02: `_dispatch_explorer_tool` defensive return is unreachable

**File:** `backend/app/services/sub_agent.py:260-261`
**Severity:** info
**Issue:** The final `return {"error": "UNHANDLED_TOOL_NAME", ...}` is
unreachable because every `tool_name` that passes the allowlist guard at
line 222 is handled by one of the five `elif` branches. If a future
maintainer adds a tool name to `EXPLORER_ALLOWED_TOOLS` without adding the
matching `elif`, the function falls through to this return — so the line
serves a defensive purpose. Recommend keeping it but tightening the comment:
**Fix:** Replace the comment with `# Sentinel: a name in the allowlist with
no dispatch branch (programmer error). DO NOT remove.`

### IN-03: `_extract_text` and `_extract_function_call` duplicate openai_client logic

**File:** `backend/app/services/sub_agent.py:161-190`
**Severity:** info
**Issue:** These helpers re-implement the part-iteration pattern at
`openai_client.py:826-838`, `1180-1184`, and `1241-1245`. Four copies of
nearly the same code now exist. Not a bug — but a quality smell that will
bite when the Gemini SDK changes the response shape. Consider extracting
to `app/services/_gemini_helpers.py` and importing into both modules.
**Fix:** Defer to Phase 6 cleanup; not phase-blocking.

### IN-04: `Message.tool_metadata` typed as `null`-able with non-strict shape

**File:** `frontend/src/lib/api.ts:39-53`
**Severity:** info
**Issue:** `tool_metadata` is typed `{ tools_used: Array<{...}> } | null`.
Backend can persist `null` (no slots), an object (current shape), or — in
the double-encoded path noted in WR-05 — a JSON string. The TS type does
not encode the string variant; a string would silently fail
`tool_metadata.tools_used[0]` access at runtime. Once WR-05 is fixed this
is moot. Until then, consider a runtime guard:
```typescript
const tm = typeof msg.tool_metadata === 'string'
  ? JSON.parse(msg.tool_metadata)
  : msg.tool_metadata
```

### IN-05: `tool_steps` match-by-name in `onSubAgentToolDone` ambiguous when sub_agent_id is dropped

**File:** `frontend/src/pages/Chat.tsx:283-291`
**Severity:** info
**Issue:** The matching predicate is
`s.isSubAgent && s.tool === data.tool && s.status === 'running' && s.turn === data.turn`.
Per the loop's single-turn-at-a-time discipline this works, but if Phase 6
ever batches turns OR if two sub-agents run concurrently with the same
`turn` index, the wrong step's status flips. The comment at line 281-282
acknowledges the risk; recommend wiring `sub_agent_id` through to the
front-end state object now so Phase 6 has the disambiguator.
**Fix:** Add `sub_agent_id?: string` to `ToolStep`, populate from
`data.sub_agent_id` (which the backend can include on the tool_start payload),
and match on `(sub_agent_id, tool, turn)`.

### IN-06: `_make_stub_gemini_client` defines `_StubChunk` but `summary_text` is reused for both stub paths

**File:** `backend/scripts/test_explorer_sub_agent.py:419-468`
**Severity:** info
**Issue:** When `repeat_function_call` is set, `generate_content` returns
a function_call response and `generate_content_stream` yields `summary_text`.
Section 4's no-progress test passes `summary_text="Stub no-progress summary."`
which never reaches the assertions (no test inspects the summary text). Not
a bug, just a dangling argument that can be removed once it's clear the
stream-summary text isn't asserted anywhere.

### IN-07: `_section_5_recursion_ban` mutates module state with no rollback if `importlib.reload` fails before `_build_explorer_tool_set` is exercised

**File:** `backend/scripts/test_explorer_sub_agent.py:685-709`
**Severity:** info
**Issue:** The `finally` block at 698-709 wraps `importlib.reload(sa)` in a
try/except that catches `AssertionError`, restores the original tuple, and
re-raises. If reload raises any OTHER exception (e.g. an unrelated import
inside the module fails because of test-env state), the reload is skipped
and `sa.EXPLORER_ALLOWED_TOOLS` is left with the tampered value
`(..., "analyze_document",)`. Subsequent sections that import from
`app.services.sub_agent` would observe the tampered tuple. This will
show up as cascading failures in section 6+ rather than a clear error.
**Fix:** Broaden the except to `except Exception` and always restore:
```python
finally:
    try:
        importlib.reload(sa)
    except AssertionError:
        sa.EXPLORER_ALLOWED_TOOLS = original_tuple
        raise
    except Exception:
        sa.EXPLORER_ALLOWED_TOOLS = original_tuple
        raise
```

---

## Items intentionally NOT flagged (LOCKED contracts per phase context)

For audit trail, the following were inspected and confirmed correct against
the locked invariants from the phase context:

- `EXPLORER_ALLOWED_TOOLS = ("tree", "glob", "grep", "list_files", "read_document")`
  + module-level assert (sub_agent.py:40-44) — recursion-ban layer 1
- `assert "analyze_document" not in EXPLORER_ALLOWED_TOOLS` setup gate at line 41
- `_signature(...)` uses `json.dumps(..., sort_keys=True, default=str)` (line 75-79) — LOCKED
- `for turn in range(MAX_TURNS):` at line 412 with `for...else` MAX_TURNS reason at line 508-513
- `@traceable(name="explore_knowledge_base", run_type="chain")` at line 346
- Dual-emit on ALL FIVE SSE arms in messages.py:92-204 (legacy + generalized envelope)
- `truncated_result = result_text[:16000] if len(result_text) > 16000 else result_text` wrapper at openai_client.py:1148 and 1224 (count: 2; bit-identity preservation)
- `apply_12k_cap(result_dict, char_cap=RESULT_CHAR_CAP)` at sub_agent.py:482 — kwarg name matches `_truncate.py:16` signature
- `EXPLORER-03` layer 3 dispatch-time guard at sub_agent.py:222-227
- Lazy-bind of `_openai_client` at sub_agent.py:14-15 (Plan 07 gap-closure for test patchability)

---

_Reviewed: 2026-05-10_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
