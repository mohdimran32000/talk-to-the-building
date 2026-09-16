"""TOOL-07: defense-in-depth scope-tag invariant helper.

Every Phase 4 tool result row MUST carry `scope: 'user' | 'global'`. The primary
enforcement is the SQL projection (RLS-permitted SELECT projects the column from
the documents table directly). This helper is the BACKSTOP for future regressions
where someone forgets to project scope or builds a row dict from scratch.

Usage pattern:
    for raw_row in db_rows:
        row = ensure_scope_tag(raw_row, default='user')
        entries.append(row)

Pitfall 11 (scope confusion in citations) mitigation.
"""
import logging
from typing import Literal

logger = logging.getLogger(__name__)


def ensure_scope_tag(row: dict, default: Literal["user", "global"] = "user") -> dict:
    """Ensure `row['scope']` is set and is a valid scope value.

    - If `'scope'` is missing: log a warning and inject `default`.
    - Assert `row['scope'] in ('user', 'global')` — invalid scopes raise AssertionError
      (louder than silent miscitation; surfaces as a 5xx in the dispatch loop).

    Returns the same row dict (mutated in place) for chaining.
    """
    if "scope" not in row:
        logger.warning(
            "Tool result row missing scope tag (id=%s) — injecting default=%r",
            row.get("id") or row.get("document_id") or "?",
            default,
        )
        row["scope"] = default
    assert row["scope"] in ("user", "global"), (
        f"Invalid scope on tool result row: {row['scope']!r}"
    )
    return row
