"""TOOL-04: list_files exploration tool.

Single-folder, single-level listing. Reuses folder_service.list_folder() for the
UNION-pattern (explicit folders + inferred from documents) and adds the Phase 4
cross-cutting concerns:

  - Pitfall 4 chokepoint: normalize_path() FIRST line of the function body.
  - TOOL-04 ordering: folders-then-files, alpha-sorted within each.
  - TOOL-07 invariant: every entry carries `scope` in {'user','global'}.
  - TOOL-08 cap: apply_12k_cap() at the tail.
  - TOOL-10 tracing: @traceable(name="list_files", run_type="tool").

The LLM dispatcher in openai_client.py routes calls here via:
    from app.services.exploration_tools.list_files import list_files
    from app.services.exploration_tools.schemas import ListFilesArgs
    result = list_files(ListFilesArgs(**args), user_id, supabase_client)
    result_text = json.dumps(result)   # flows into the layered-fallback wrapper
"""
import logging
from typing import Optional

from langsmith import traceable

from app.services.exploration_tools._scope_tag import ensure_scope_tag
from app.services.exploration_tools._truncate import apply_12k_cap
from app.services.exploration_tools.schemas import ListFilesArgs
from app.services.folder_service import list_folder, normalize_path

logger = logging.getLogger(__name__)


@traceable(name="list_files", run_type="tool")
def list_files(
    args: ListFilesArgs,
    user_id: Optional[str],
    supabase_client,
) -> dict:
    """List one folder's immediate children (folders + files) with TOOL-04 ordering.

    Args:
        args:           Validated Pydantic ListFilesArgs (path, scope).
        user_id:        Caller's JWT-derived user_id (None for service-role contexts).
        supabase_client: JWT-bound Supabase client (RLS applies).

    Returns:
        Result dict with `entries` (folders-first-alpha then files-alpha), `total`,
        `truncation_marker`, and `scope`-tagged rows. On error, a dict with `error`
        and `message`.
    """
    # Pitfall 4 chokepoint - first statement.
    try:
        norm = normalize_path(args.path)
    except ValueError as e:
        return {
            "tool": "list_files",
            "error": "INVALID_PATH",
            "message": str(e),
        }

    try:
        folder = list_folder(norm, args.scope, user_id, supabase_client)
    except Exception as e:
        logger.warning(
            "list_files list_folder query failed for path=%r scope=%r: %s",
            norm, args.scope, e, exc_info=True,
        )
        return {
            "tool": "list_files",
            "error": "QUERY_FAILED",
            "message": f"{type(e).__name__}: {e}",
            "path": norm,
            "scope_arg": args.scope,
        }

    documents = folder.get("documents") or []
    subfolders = folder.get("subfolders") or []

    # TOOL-04 ordering contract: folders-then-files; alpha-sorted within each.
    folders_sorted = sorted(subfolders)
    docs_sorted = sorted(documents, key=lambda d: (d.get("file_name") or "").lower())

    entries: list[dict] = []

    # Subfolder entries - re-derive scope per RESEARCH.md A5 option (b):
    # filter the documents list by `folder_path.startswith(sub)` and take any row's
    # scope. Falls back to args.scope (best guess) if no doc lives under that subfolder.
    for sub in folders_sorted:
        sub_scope = _infer_subfolder_scope(sub, documents, args.scope)
        entry = {"type": "folder", "path": sub, "scope": sub_scope}
        entries.append(ensure_scope_tag(entry, default=sub_scope))

    # Document entries - scope is already projected by folder_service.list_folder().
    for d in docs_sorted:
        entry = {
            "type": "doc",
            "document_id": d.get("id"),
            "file_name": d.get("file_name"),
            "folder_path": d.get("folder_path"),
            "scope": d.get("scope") or "user",   # ensure_scope_tag asserts validity
        }
        entries.append(ensure_scope_tag(entry, default="user"))

    result = {
        "tool": "list_files",
        "scope_arg": args.scope,
        "path": norm,
        "entries": entries,
        "total": len(entries),
    }
    return apply_12k_cap(result)


def _infer_subfolder_scope(sub_path: str, documents: list, fallback: str) -> str:
    """Re-derive a subfolder's scope from the documents list (RESEARCH.md A5 option b).

    Walks the documents list looking for any row whose folder_path starts with sub_path.
    Returns that row's scope. If no doc lives under the subfolder (e.g., it is an
    explicit-empty folder from the folders side table), falls back to `fallback` -
    if `fallback` is 'both', defaults to 'user' (the safer default; admin can re-derive
    if needed).
    """
    for d in documents:
        fp = d.get("folder_path") or ""
        if fp == sub_path or fp.startswith(sub_path + "/"):
            scope = d.get("scope")
            if scope in ("user", "global"):
                return scope
    # No doc lives under sub_path - fall back to the caller's narrowing arg.
    if fallback in ("user", "global"):
        return fallback
    return "user"
