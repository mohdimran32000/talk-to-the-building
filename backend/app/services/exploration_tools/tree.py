"""TOOL-01: tree exploration tool.

Returns a nested folder structure with hard caps on depth (Pydantic-clamped to <= 4)
and total entries (500 hard cap during traversal; 12K char cap on serialized result).
Per-level summary nodes `{more_folders, more_docs}` replace deeper subtrees when the
budget runs out (ROADMAP SC1: '[N more folders, M more docs]' summaries).

Algorithm: iterative BFS with a running `entries_remaining` counter (RESEARCH.md
Open Questions #5 recommendation - cleaner shutdown than recursion-with-budget;
recursion is intentionally NOT used here).

Cross-cutting concerns mirror Plan 03's list_files.py:
  - Pitfall 4 chokepoint: normalize_path() FIRST.
  - TOOL-07 invariant: every entry/child carries scope.
  - TOOL-08 cap: apply_12k_cap() at the tail.
  - TOOL-10 tracing: @traceable(name="tree", run_type="tool").
"""
import logging
from collections import deque
from typing import Optional

from langsmith import traceable

from app.services.exploration_tools._scope_tag import ensure_scope_tag
from app.services.exploration_tools._truncate import apply_12k_cap
from app.services.exploration_tools.schemas import TreeArgs
from app.services.folder_service import list_folder, normalize_path

logger = logging.getLogger(__name__)

_ENTRY_BUDGET = 500   # Pitfall 2 RANK 4 - hard entry cap during traversal


@traceable(name="tree", run_type="tool")
def tree(
    args: TreeArgs,
    user_id: Optional[str],
    supabase_client,
) -> dict:
    """Iterative-BFS tree traversal with per-level summary cutoff.

    Args:
        args:           Validated Pydantic TreeArgs (path, max_depth, scope).
        user_id:        Caller's JWT-derived user_id (None for service-role contexts).
        supabase_client: JWT-bound Supabase client (RLS applies).

    Returns:
        Result dict shape:
            {
              "tool": "tree",
              "scope_arg": "<user|global|both>",
              "path": "<normalized>",
              "max_depth": <int 1-4>,
              "entries": [...nested children...],
              "total_folders": <int>,
              "total_docs": <int>,
              "truncation_marker": None | "[...truncated, N more entries]"
            }

        On error, a structured envelope dict with `error` and `message`.
    """
    # Pitfall 4 chokepoint - first statement.
    try:
        norm = normalize_path(args.path)
    except ValueError as e:
        return {"tool": "tree", "error": "INVALID_PATH", "message": str(e)}

    entries_remaining = _ENTRY_BUDGET
    total_folders = 0
    total_docs = 0

    # Root-level expansion.
    try:
        root_folder = list_folder(norm, args.scope, user_id, supabase_client)
    except Exception as e:
        logger.warning(
            "tree root list_folder failed for path=%r scope=%r: %s",
            norm, args.scope, e, exc_info=True,
        )
        return {
            "tool": "tree",
            "error": "QUERY_FAILED",
            "message": f"{type(e).__name__}: {e}",
            "path": norm,
            "scope_arg": args.scope,
        }

    root_entries: list[dict] = []
    # BFS queue: (parent_entries_list, parent_path, current_depth, folder_data)
    bfs_queue: deque = deque()
    bfs_queue.append((root_entries, norm, 0, root_folder))

    # Track unreached items per-level when budget is exhausted mid-iteration.
    # Tuples of (parent_entries_list, parent_path, parent_scope, more_folders, more_docs).
    pending_level_summaries: list[tuple] = []

    while bfs_queue and entries_remaining > 0:
        parent_entries, parent_path, depth, folder_data = bfs_queue.popleft()
        documents = folder_data.get("documents") or []
        subfolders = sorted(folder_data.get("subfolders") or [])

        # Re-derive a fallback scope for this level's summary (mirrors per-folder logic).
        level_scope = args.scope if args.scope in ("user", "global") else "user"
        if documents:
            for d in documents:
                s = d.get("scope")
                if s in ("user", "global"):
                    level_scope = s
                    break

        # Add this level's docs.
        docs_added = 0
        for d in documents:
            if entries_remaining <= 0:
                break
            doc_entry = {
                "type": "doc",
                "path": _join(d.get("folder_path") or parent_path, d.get("file_name") or ""),
                "scope": d.get("scope") or "user",
                "document_id": d.get("id"),
                "file_name": d.get("file_name"),
            }
            parent_entries.append(ensure_scope_tag(doc_entry, default="user"))
            entries_remaining -= 1
            total_docs += 1
            docs_added += 1

        # Add this level's subfolders.
        folders_added = 0
        for sub_path in subfolders:
            if entries_remaining <= 0:
                break
            sub_scope = _infer_subfolder_scope(sub_path, documents, args.scope)
            folder_entry = {
                "type": "folder",
                "path": sub_path,
                "scope": sub_scope,
            }
            parent_entries.append(ensure_scope_tag(folder_entry, default=sub_scope))
            entries_remaining -= 1
            total_folders += 1
            folders_added += 1

            # Queue child expansion if depth allows AND budget allows.
            next_depth = depth + 1
            if next_depth < args.max_depth and entries_remaining > 0:
                try:
                    child_folder = list_folder(sub_path, args.scope, user_id, supabase_client)
                    folder_entry["children"] = []
                    bfs_queue.append((folder_entry["children"], sub_path, next_depth, child_folder))
                except Exception as e:
                    logger.warning(
                        "tree subfolder list_folder failed for path=%r: %s",
                        sub_path, e, exc_info=True,
                    )
                    # Mark this folder as having an unknown subtree rather than failing the whole tree.
                    folder_entry["error"] = f"SUBQUERY_FAILED: {type(e).__name__}"
            else:
                # Depth cap or budget cap - emit per-folder summary placeholder.
                # Counts are not yet known (would require an extra query); set to 0/0 as
                # a best-effort placeholder. The truncation_marker on the root-level
                # apply_12k_cap signals further cutoffs.
                folder_entry["more_folders"] = 0
                folder_entry["more_docs"] = 0

        # If we couldn't add every doc/folder at this level (budget cut us off mid-iter),
        # record an in-place summary so the LLM sees `[N more folders, M more docs]`.
        unreached_docs_here = len(documents) - docs_added
        unreached_folders_here = len(subfolders) - folders_added
        if unreached_docs_here > 0 or unreached_folders_here > 0:
            pending_level_summaries.append(
                (parent_entries, parent_path, level_scope, unreached_folders_here, unreached_docs_here)
            )

    # Append per-level summary nodes for levels where the inner-loop break left items unreached.
    # Done AFTER the BFS loop so we don't perturb iteration order.
    for parent_entries, parent_path, level_scope, more_f, more_d in pending_level_summaries:
        summary_entry = {
            "type": "folder",
            "path": parent_path,
            "scope": level_scope,
            "more_folders": more_f,
            "more_docs": more_d,
        }
        parent_entries.append(ensure_scope_tag(summary_entry, default=level_scope))

    # If BFS terminated with queue still non-empty (budget exhausted), summarize the
    # remaining queued folders as one cumulative top-level summary node. This is in
    # ADDITION to per-level summaries above (different signals).
    budget_exhausted = entries_remaining <= 0 and len(bfs_queue) > 0
    if budget_exhausted:
        # Drain the queue to count what was skipped (cheap - entries are already in memory).
        unreached_folders = 0
        unreached_docs = 0
        while bfs_queue:
            _entries, _path, _depth, folder_data = bfs_queue.popleft()
            unreached_docs += len(folder_data.get("documents") or [])
            unreached_folders += len(folder_data.get("subfolders") or [])

        # Attach a top-level summary placeholder for the LLM to surface.
        summary_scope = args.scope if args.scope in ("user", "global") else "user"
        summary_entry = {
            "type": "summary",
            "path": norm,
            "scope": summary_scope,
            "more_folders": unreached_folders,
            "more_docs": unreached_docs,
        }
        root_entries.append(ensure_scope_tag(summary_entry, default=summary_scope))

    result = {
        "tool": "tree",
        "scope_arg": args.scope,
        "path": norm,
        "max_depth": args.max_depth,
        "entries": root_entries,
        "total_folders": total_folders,
        "total_docs": total_docs,
    }
    return apply_12k_cap(result)


def _join(folder_path: str, file_name: str) -> str:
    """Build a doc's full path for the entry's `path` field."""
    if not file_name:
        return folder_path
    if folder_path == "/":
        return f"/{file_name}"
    return f"{folder_path}/{file_name}"


def _infer_subfolder_scope(sub_path: str, documents: list, fallback: str) -> str:
    """Re-derive a subfolder's scope from the parent's documents list (RESEARCH.md A5 b).

    Identical idiom to list_files._infer_subfolder_scope - kept local to avoid a
    cross-tool helper module before the pattern hardens.
    """
    for d in documents:
        fp = d.get("folder_path") or ""
        if fp == sub_path or fp.startswith(sub_path + "/"):
            scope = d.get("scope")
            if scope in ("user", "global"):
                return scope
    if fallback in ("user", "global"):
        return fallback
    return "user"
