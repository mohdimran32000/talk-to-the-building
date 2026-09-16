"""TOOL-05: read_document exploration tool.

Returns a line-numbered slice of `documents.content_markdown` in arrow-form
`{line_no}→{content}`. Honors:

  - 1-based external offset (Claude Code convention).
  - splitlines(keepends=False) — Pitfall 9 line-stability invariant; works for
    CRLF / LF / CR uniformly.
  - UTF-8 codepoint-safe truncation on the LAST visible line only.
  - Phase 2 LOCKED tool integration contract: rows where
    content_markdown_status != 'ready' return `{status: 'pending_reindex', ...}`.

Cross-cutting concerns:
  - Pitfall 4 chokepoint: normalize_path() applied to the folder portion when
    args.path is used.
  - TOOL-10 tracing: @traceable(name="read_document", run_type="tool").
  - TOOL-09 routing: returns dict; openai_client.py JSON-serializes into
    result_text and the existing layered-fallback wrapper handles streaming.

Note: read_document does NOT call apply_12k_cap. The `content` field is rendered
text (not a list) and is truncated in-tool via UTF-8-safe byte slicing — applying
apply_12k_cap on top would cap the JSON-serialized payload, which is fine but
redundant.
"""
import logging
from typing import Optional

from langsmith import traceable

from app.services.exploration_tools.schemas import ReadDocumentArgs
from app.services.folder_service import normalize_path

logger = logging.getLogger(__name__)

_CONTENT_CHAR_CAP = 12_000
_ARROW = "→"   # U+2192 RIGHTWARDS ARROW


@traceable(name="read_document", run_type="tool")
def read_document(
    args: ReadDocumentArgs,
    user_id: Optional[str],
    supabase_client,
) -> dict:
    """Read a line-numbered slice of a document's content_markdown."""
    # Resolve to a single row.
    try:
        row = _resolve_row(args, supabase_client)
    except ValueError as e:
        return {"tool": "read_document", "error": "INVALID_ARGS", "message": str(e)}
    except Exception as e:
        logger.warning("read_document resolve failed: %s", e, exc_info=True)
        return {
            "tool": "read_document",
            "error": "QUERY_FAILED",
            "message": f"{type(e).__name__}: {e}",
        }

    if not row:
        return {
            "tool": "read_document",
            "error": "NOT_FOUND",
            "message": (
                f"No document at {args.path!r}" if args.path
                else f"No document with id {args.document_id!r}"
            ),
        }

    # Phase 2 LOCKED contract — non-ready rows surface as pending_reindex.
    status = row.get("content_markdown_status")
    if status != "ready":
        return {
            "tool": "read_document",
            "document_id": row.get("id"),
            "file_name": row.get("file_name"),
            "scope": row.get("scope") or "user",
            "folder_path": row.get("folder_path") or "/",
            "status": "pending_reindex",
            "content_markdown_status": status,
        }

    # Line slicing — splitlines(keepends=False) is uniform across CRLF/LF/CR.
    full_text = row.get("content_markdown") or ""
    lines = full_text.splitlines(keepends=False)
    total_lines = len(lines)

    start_idx = args.offset - 1                                # 1-based -> 0-based
    end_idx = min(start_idx + args.limit, total_lines)
    slice_ = lines[start_idx:end_idx] if start_idx < total_lines else []

    # Arrow-form rendering: f"{line_no}→{line}".
    rendered = "\n".join(
        f"{start_idx + i + 1}{_ARROW}{line}"
        for i, line in enumerate(slice_)
    )

    truncation_marker = None
    if len(rendered) > _CONTENT_CHAR_CAP:
        # UTF-8 codepoint-safe truncation on the LAST visible line.
        truncated_bytes = rendered.encode("utf-8")[:_CONTENT_CHAR_CAP]
        truncated = truncated_bytes.decode("utf-8", errors="ignore")
        # Trim back to a complete line so the LAST line isn't half-shown.
        last_nl = truncated.rfind("\n")
        if last_nl != -1:
            truncated = truncated[:last_nl]
        # Recount how many rendered lines we kept.
        kept_lines = truncated.count("\n") + (1 if truncated else 0)
        truncated_end_line = start_idx + kept_lines
        remaining = total_lines - truncated_end_line
        truncation_marker = f"[...truncated, {remaining} more lines]"
        rendered = truncated + ("\n" + truncation_marker if truncation_marker else "")
        end_line = truncated_end_line
    else:
        end_line = start_idx + len(slice_)

    return {
        "tool": "read_document",
        "document_id": row.get("id"),
        "file_name": row.get("file_name"),
        "scope": row.get("scope") or "user",
        "folder_path": row.get("folder_path") or "/",
        "start_line": start_idx + 1 if total_lines > 0 else args.offset,
        "end_line": end_line if total_lines > 0 else args.offset - 1,
        "total_lines": total_lines,
        "content": rendered,
        "truncation_marker": truncation_marker,
    }


def _resolve_row(args: ReadDocumentArgs, supabase_client) -> Optional[dict]:
    """Resolve `args` to a single documents row via document_id OR path lookup.

    Returns None if RLS hides the row OR no row matches. Raises ValueError on
    malformed args (defense in depth — Plan 02's @model_validator already enforced
    exactly-one-of).
    """
    select_cols = "id, file_name, folder_path, scope, content_markdown, content_markdown_status"

    if args.document_id:
        try:
            resp = (
                supabase_client.table("documents")
                .select(select_cols)
                .eq("id", args.document_id)
                .maybe_single()
                .execute()
            )
            return resp.data if resp else None
        except Exception as e:
            logger.warning(
                "read_document by id failed for %r: %s", args.document_id, e, exc_info=True,
            )
            return None

    if args.path:
        # Split into folder + file_name. Path regex on ReadDocumentArgs guarantees
        # at least one folder segment + a file_name segment after.
        split_idx = args.path.rfind("/")
        if split_idx < 0:
            raise ValueError(f"path missing slash: {args.path!r}")
        folder_part = args.path[:split_idx] or "/"
        file_name = args.path[split_idx + 1:]
        if not file_name:
            raise ValueError(f"path missing file_name: {args.path!r}")

        try:
            norm_folder = normalize_path(folder_part)
        except ValueError as e:
            raise ValueError(f"path folder portion not canonical: {e}") from e

        try:
            resp = (
                supabase_client.table("documents")
                .select(select_cols)
                .eq("folder_path", norm_folder)
                .eq("file_name", file_name)
                .maybe_single()
                .execute()
            )
            return resp.data if resp else None
        except Exception as e:
            logger.warning(
                "read_document by path failed for %r: %s", args.path, e, exc_info=True,
            )
            return None

    # Plan 02's @model_validator already enforces exactly-one-of; defense in depth.
    raise ValueError("Specify exactly one of document_id or path")
