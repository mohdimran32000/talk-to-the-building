"""TOOL-03: grep exploration tool.

Python wrapper around Migration 020's `grep_documents` RPC. Adds:
  - Pathological-regex blocklist (Pitfall 3 #6).
  - Literal-substring auto-extraction for the ILIKE pre-filter (RESEARCH.md A8).
  - +/-A/B/C context assembly (Python-side line slicing on hit-bearing docs).
  - output_mode branching (content / files_with_matches / count).
  - Phase 2 LOCKED pending_reindex pass-through.
  - Scope-tagging + 12K cap.

Cross-cutting concerns mirror Plans 03-06:
  - Pitfall 4 chokepoint: normalize_path() FIRST.
  - HI-01: _assert_uuid before user_id interpolation.
  - TOOL-07: ensure_scope_tag on every hit row.
  - TOOL-08: apply_12k_cap at the tail.
  - TOOL-10: @traceable(name="grep", run_type="tool").
"""
import logging
import re
from collections import defaultdict
from typing import Optional

from langsmith import traceable

from app.services.exploration_tools._scope_tag import ensure_scope_tag
from app.services.exploration_tools._truncate import apply_12k_cap
from app.services.exploration_tools.schemas import GrepArgs
from app.services.folder_service import _assert_uuid, normalize_path

logger = logging.getLogger(__name__)

# Pathological-regex blocklist — canonical ReDoS patterns.
# Pitfall 3 mitigation #6: cheap substring check BEFORE the RPC fires.
_PATHOLOGICAL_PATTERNS = (
    "(.*)+",
    "(.+)+",
    "(.*)*",
    "(.+)*",
    "(.|.)*",
    "(a|a)*",
)
_MAX_HITS = 50
_LITERAL_MIN_LEN = 3


@traceable(name="grep", run_type="tool")
def grep(
    args: GrepArgs,
    user_id: Optional[str],
    supabase_client,
) -> dict:
    """Regex search across documents.content_markdown.

    Returns hits with +/-A/B/C line context (output_mode='content'), a doc list
    (output_mode='files_with_matches'), or a per-doc count (output_mode='count').
    """
    # Pitfall 4 chokepoint — first statement.
    try:
        norm = normalize_path(args.path)
    except ValueError as e:
        return {"tool": "grep", "error": "INVALID_PATH", "message": str(e)}

    # Regex pre-screen.
    try:
        re.compile(args.pattern)
    except re.error as e:
        return {"tool": "grep", "error": "INVALID_REGEX", "message": str(e)}

    # Pathological-regex blocklist (cheap substring check).
    if any(banned in args.pattern for banned in _PATHOLOGICAL_PATTERNS):
        return {
            "tool": "grep",
            "error": "PATHOLOGICAL_REGEX",
            "message": "Pattern contains nested unbounded repetition "
                       "(e.g., (.*)+ or (.+)+) — refusing to evaluate (Pitfall 3).",
        }

    # HI-01 defense.
    if args.scope in ("user", "both"):
        try:
            _assert_uuid(user_id, "user_id")
        except ValueError as e:
            return {"tool": "grep", "error": "INVALID_USER_ID", "message": str(e)}

    # Auto-extract a literal substring for the ILIKE pre-filter (>=3 chars).
    literal_hint = _extract_literal_substring(args.pattern, min_len=_LITERAL_MIN_LEN)

    scope_param = None if args.scope == "both" else args.scope

    # Invoke the RPC.
    try:
        resp = supabase_client.rpc("grep_documents", {
            "p_pattern": args.pattern,
            "p_path_prefix": norm,
            "p_scope": scope_param,
            "p_user_id": user_id,
            "p_case_insensitive": args.case_insensitive,
            "p_max_hits": _MAX_HITS,
            "p_literal_substring": literal_hint,
        }).execute()
        rows = resp.data or []
    except Exception as e:
        logger.warning("grep RPC failed: %s", e, exc_info=True)
        return {
            "tool": "grep",
            "error": "RPC_FAILED",
            "message": f"{type(e).__name__}: {e}",
            "path": norm,
            "scope_arg": args.scope,
        }

    # Branch by output_mode.
    if args.output_mode == "count":
        counts: dict[str, dict] = {}
        for r in rows:
            if r.get("status") != "matched":
                continue
            did = r.get("document_id")
            if did not in counts:
                counts[did] = {
                    "document_id": did,
                    "file_name": r.get("file_name"),
                    "folder_path": r.get("folder_path"),
                    "scope": r.get("scope") or "user",
                    "match_count": 0,
                }
            counts[did]["match_count"] += 1
        count_list = [ensure_scope_tag(c, default="user") for c in counts.values()]
        return apply_12k_cap({
            "tool": "grep",
            "scope_arg": args.scope,
            "pattern": args.pattern,
            "path": norm,
            "count_per_document": count_list,
            "total_hits": sum(c["match_count"] for c in count_list),
        })

    if args.output_mode == "files_with_matches":
        seen: set[str] = set()
        files: list[dict] = []
        for r in rows:
            if r.get("status") != "matched":
                continue
            did = r.get("document_id")
            if did in seen:
                continue
            seen.add(did)
            entry = {
                "document_id": did,
                "file_name": r.get("file_name"),
                "folder_path": r.get("folder_path"),
                "scope": r.get("scope") or "user",
            }
            files.append(ensure_scope_tag(entry, default="user"))
        # Pass through pending_reindex rows.
        for r in rows:
            if r.get("status") == "pending_reindex":
                entry = {
                    "document_id": r.get("document_id"),
                    "file_name": r.get("file_name"),
                    "folder_path": r.get("folder_path"),
                    "scope": r.get("scope") or "user",
                    "status": "pending_reindex",
                }
                files.append(ensure_scope_tag(entry, default="user"))
        return apply_12k_cap({
            "tool": "grep",
            "scope_arg": args.scope,
            "pattern": args.pattern,
            "path": norm,
            "files": files,
            "total_hits": len(files),
        })

    # output_mode='content' — assemble hits with +/-A/B/C context.
    before = args.C if args.C is not None else args.B
    after = args.C if args.C is not None else args.A

    # Group hit line_nos by document_id so we can fetch content_markdown once per doc.
    doc_hits: dict[str, list[dict]] = defaultdict(list)
    pending_rows: list[dict] = []
    for r in rows:
        if r.get("status") == "pending_reindex":
            pending_rows.append(r)
            continue
        doc_hits[r.get("document_id")].append(r)

    # Batch fetch content_markdown for hit-bearing docs (one query, IN clause).
    content_by_doc: dict[str, str] = {}
    if doc_hits:
        try:
            content_resp = (
                supabase_client.table("documents")
                .select("id, content_markdown")
                .in_("id", list(doc_hits.keys()))
                .execute()
            )
            for d in content_resp.data or []:
                content_by_doc[d["id"]] = d.get("content_markdown") or ""
        except Exception as e:
            logger.warning("grep content_markdown batch fetch failed: %s", e, exc_info=True)

    hits: list[dict] = []
    for did, doc_rows in doc_hits.items():
        content = content_by_doc.get(did) or ""
        lines = content.splitlines(keepends=False)
        for r in doc_rows:
            ln = r.get("line_no")
            if ln is None:
                continue
            ln_int = int(ln)
            start_idx = max(ln_int - 1 - before, 0)
            end_idx = min(ln_int + after, len(lines))
            context = [
                {"line_no": start_idx + i + 1, "text": lines[start_idx + i]}
                for i in range(end_idx - start_idx)
            ]
            hit = {
                "document_id": did,
                "file_name": r.get("file_name"),
                "folder_path": r.get("folder_path"),
                "scope": r.get("scope") or "user",
                "line_no": ln_int,
                "context": context,
            }
            hits.append(ensure_scope_tag(hit, default="user"))

    # Pass through pending rows last (don't conflate with matched hits).
    for r in pending_rows:
        entry = {
            "document_id": r.get("document_id"),
            "file_name": r.get("file_name"),
            "folder_path": r.get("folder_path"),
            "scope": r.get("scope") or "user",
            "status": "pending_reindex",
        }
        hits.append(ensure_scope_tag(entry, default="user"))

    return apply_12k_cap({
        "tool": "grep",
        "scope_arg": args.scope,
        "pattern": args.pattern,
        "path": norm,
        "hits": hits,
        "total_hits": sum(1 for h in hits if "line_no" in h),
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REGEX_META_CHARS = set(r".^$*+?()[]{}|\\")


def _extract_literal_substring(pattern: str, min_len: int = 3) -> Optional[str]:
    """Find the longest literal substring (>= min_len contiguous non-meta chars).

    Examples:
      'panel-2026'    -> 'panel-2026' (no meta chars)
      'panel|switch'  -> 'panel' (or 'switch'; longest tiebreak wins — both 5)
      'foo.+bar'      -> 'foo' or 'bar' (longest tiebreak — both 3)
      '(.*)+'         -> None
      'a|b|c'         -> None (no run >= 3)

    Drives the GIN trigram ILIKE pre-filter via Migration 020's
    `p_literal_substring` parameter — narrows the candidate doc set to those
    whose content contains the literal substring BEFORE the regex runs.
    """
    best: Optional[str] = None
    current: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            # Escaped meta — treat as a single literal char.
            current.append(pattern[i + 1])
            i += 2
            continue
        if ch in _REGEX_META_CHARS:
            if len(current) >= min_len:
                candidate = "".join(current)
                if best is None or len(candidate) > len(best):
                    best = candidate
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    # Final flush.
    if len(current) >= min_len:
        candidate = "".join(current)
        if best is None or len(candidate) > len(best):
            best = candidate
    return best
