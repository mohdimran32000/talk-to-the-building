"""TOOL-08: 12K-char truncation helper.

Applied at the END of every Phase 4 tool function. Stateless. Centralizes the
`[...truncated, N more entries]` marker contract so per-tool plans don't hand-roll
truncation.

Why 12K (not 16K)? The 12K cap is the Phase 4 LLM-readability heuristic — keeps
tool results legible. The 16K cap at openai_client.py:567 is the Gemini-context-
window heuristic — keeps the LLM call from returning empty. Both coexist; the 12K
cap fires FIRST (inside the tool), the 16K cap fires SECOND (in the wrapper).
"""
import json
from typing import Any


def apply_12k_cap(payload: dict, *, char_cap: int = 12_000) -> dict:
    """Truncate a tool result dict and append the `truncation_marker` if it overflows.

    Strategy:
      1. JSON-serialize the payload via `json.dumps(payload, default=str)`. If the
         result is under `char_cap`, set `payload['truncation_marker'] = None` and
         return the payload unchanged.
      2. Identify the main list field — first key in the priority order
         `('entries', 'hits', 'matches')` that is present in payload AND maps to
         a list. If none is found, set `payload['truncation_marker']` to
         '[...truncated; payload too large to summarize]' and return.
      3. Drop entries from the END of that list one at a time, re-serializing
         after each drop, until the serialized payload is under `char_cap`.
      4. Set `payload['truncation_marker'] = f'[...truncated, {drop_count} more entries]'`.
      5. Return the (mutated) payload.

    The marker is a SIBLING field, never embedded in the trimmed list — the LLM
    sees a clean list followed by a neighbor marker rather than a poisoned final
    element.
    """
    # Probe the current size.
    if len(_serialize(payload)) <= char_cap:
        payload["truncation_marker"] = None
        return payload

    # Find the main list to trim.
    list_key: str | None = None
    for candidate in ("entries", "hits", "matches"):
        if candidate in payload and isinstance(payload[candidate], list):
            list_key = candidate
            break

    if list_key is None:
        payload["truncation_marker"] = "[...truncated; payload too large to summarize]"
        return payload

    drop_count = 0
    while payload[list_key] and len(_serialize(payload)) > char_cap:
        payload[list_key].pop()
        drop_count += 1

    if drop_count > 0:
        payload["truncation_marker"] = f"[...truncated, {drop_count} more entries]"
    else:
        # Edge case: even with empty list the payload still exceeds cap.
        payload["truncation_marker"] = "[...truncated; non-list fields exceed cap]"

    return payload


def _serialize(obj: Any) -> str:
    """Stable JSON serialization for length-probing. default=str handles datetime, UUID."""
    return json.dumps(obj, default=str, ensure_ascii=False)
