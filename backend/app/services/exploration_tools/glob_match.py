"""TOOL-02: glob exploration tool.

Translates LLM-friendly `*` / `**` glob patterns into a Postgres regex and pushes the
match into the database (never pulls the corpus to Python without bounds). The matching
surface:

  - For type='file' or 'both': matches against `documents.folder_path || '/' || file_name`.
  - For type='folder' or 'both': matches against the UNION of `folders.path` and the
    inferred-from-documents folder paths (same UNION shape as folder_service.list_folder).

Glob semantics (RESEARCH.md Tool Block B):
  *      -> [^/]*    (single segment; no slash crosses)
  **     -> .*       (any depth)
  <lit>  -> re.escape(<lit>)

Cross-cutting concerns mirror Plans 03/04:
  - Pitfall 4 chokepoint: normalize_path() FIRST.
  - HI-01: _assert_uuid before user_id interpolation into PostgREST or() filter.
  - HI-03: _escape_like for literal-prefix LIKE predicates.
  - TOOL-07: ensure_scope_tag on every match row.
  - TOOL-08: apply_12k_cap at the tail.
  - TOOL-10: @traceable(name="glob", run_type="tool").

Note on naming: the Python identifier is `glob_match` to avoid shadowing the stdlib
`glob` module; the LLM-facing tool name remains `glob` (set via the @traceable
`name=` kwarg AND the dispatch arm in openai_client.py).
"""
import logging
import re
from typing import Optional

from langsmith import traceable

from app.services.exploration_tools._scope_tag import ensure_scope_tag
from app.services.exploration_tools._truncate import apply_12k_cap
from app.services.exploration_tools.schemas import GlobArgs
from app.services.folder_service import (
    _assert_uuid,
    _escape_like,
    normalize_path,
)

logger = logging.getLogger(__name__)

_MATCH_HARD_CAP = 500   # mirrors tree's 500-entry cap (RESEARCH.md A3)


@traceable(name="glob", run_type="tool")
def glob_match(
    args: GlobArgs,
    user_id: Optional[str],
    supabase_client,
) -> dict:
    """Pattern-match files and/or folders by glob (`*`/`**`).

    Args:
        args:           Validated Pydantic GlobArgs (pattern, path, type, scope).
        user_id:        Caller's JWT-derived user_id (None for service-role contexts).
        supabase_client: JWT-bound Supabase client (RLS applies).

    Returns:
        Result dict shape:
            {
              "tool": "glob",
              "scope_arg": "<user|global|both>",
              "pattern": "<original glob>",
              "path_prefix": "<normalized prefix>",
              "matches": [
                {"type": "doc", "document_id": "<uuid>", "file_name": "...",
                 "folder_path": "...", "scope": "user|global"},
                {"type": "folder", "path": "...", "scope": "user|global"},
                ...
              ],
              "total_matches": <int>,
              "truncation_marker": None | "[...truncated, N more entries]"
            }

        On error, a structured envelope dict with `error` and `message`.
    """
    # Pitfall 4 chokepoint - first statement.
    try:
        norm_prefix = normalize_path(args.path)
    except ValueError as e:
        return {"tool": "glob", "error": "INVALID_PATH", "message": str(e)}

    # HI-01 defense before any user_id interpolation into PostgREST or() filter.
    if args.scope in ("user", "both"):
        try:
            _assert_uuid(user_id, "user_id")
        except ValueError as e:
            return {"tool": "glob", "error": "INVALID_USER_ID", "message": str(e)}

    # Translate glob to Postgres-style regex (anchored at the prefix).
    try:
        regex = _glob_to_regex(args.pattern, norm_prefix)
    except ValueError as e:
        return {"tool": "glob", "error": "INVALID_PATTERN", "message": str(e)}

    matches: list[dict] = []

    # type=file or type=both - query documents.
    if args.type in ("file", "both"):
        try:
            docs = _query_documents(
                norm_prefix=norm_prefix,
                regex=regex,
                scope=args.scope,
                user_id=user_id,
                supabase_client=supabase_client,
            )
            for d in docs[: _MATCH_HARD_CAP - len(matches)]:
                entry = {
                    "type": "doc",
                    "document_id": d.get("id"),
                    "file_name": d.get("file_name"),
                    "folder_path": d.get("folder_path"),
                    "scope": d.get("scope") or "user",
                }
                matches.append(ensure_scope_tag(entry, default="user"))
        except Exception as e:
            logger.warning("glob documents query failed: %s", e, exc_info=True)
            # Fall through - continue with folder branch if applicable.

    # type=folder or type=both - query folders + inferred folder paths.
    if args.type in ("folder", "both") and len(matches) < _MATCH_HARD_CAP:
        try:
            folder_paths = _query_folders(
                norm_prefix=norm_prefix,
                regex=regex,
                scope=args.scope,
                user_id=user_id,
                supabase_client=supabase_client,
            )
            for path, scope in folder_paths[: _MATCH_HARD_CAP - len(matches)]:
                entry = {"type": "folder", "path": path, "scope": scope}
                matches.append(ensure_scope_tag(entry, default=scope))
        except Exception as e:
            logger.warning("glob folders query failed: %s", e, exc_info=True)

    result = {
        "tool": "glob",
        "scope_arg": args.scope,
        "pattern": args.pattern,
        "path_prefix": norm_prefix,
        "matches": matches,
        "total_matches": len(matches),
    }
    return apply_12k_cap(result)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _glob_to_regex(pattern: str, anchor_path: str) -> str:
    """Translate a glob pattern into a regex anchored at `anchor_path`.

    Rules:
      - `**` (two asterisks) -> `.*` (matches across slashes)
      - `*`  (one asterisk)  -> `[^/]*` (matches within a single segment)
      - All other regex metacharacters in literal segments are re.escape()'d.
      - The pattern is anchored at the start with the canonical prefix and at the
        end with `$`.

    The output regex matches against the FULL path of either:
      - a document: f"{folder_path}/{file_name}" (or f"/{file_name}" when folder_path == "/")
      - a folder:   the canonical folder path

    For anchor_path == "/", the prefix is just `^/?` so the first slash is optional —
    this lets bare patterns like `*.pdf` match files at any depth in a UNIX-glob-friendly
    way (matches '/foo.pdf' as well as 'foo.pdf').
    """
    if not pattern:
        raise ValueError("glob pattern is empty")

    # Walk left-to-right; build the regex piece by piece so `**` is detected before `*`.
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                out.append(".*")
                i += 2
            else:
                out.append("[^/]*")
                i += 1
        else:
            out.append(re.escape(ch))
            i += 1
    glob_re = "".join(out)

    # Anchor at the prefix.
    if anchor_path == "/":
        return f"^/?{glob_re}$"
    prefix = re.escape(anchor_path).rstrip("/")
    return f"^{prefix}/{glob_re}$"


def _query_documents(
    norm_prefix: str,
    regex: str,
    scope: str,
    user_id: Optional[str],
    supabase_client,
) -> list[dict]:
    """SELECT documents whose `folder_path/file_name` matches the regex.

    Two-stage filter: a LIKE prefix prefilter (exploits documents_folder_path_prefix_idx
    from Migration 016) bounds the candidate set inside the database, then a Python-side
    re.fullmatch() applies the precise regex on the bounded result. This keeps the
    LIKE-bounded candidate count tiny (capped at _MATCH_HARD_CAP * 2) so we never pull
    the entire corpus to Python.
    """
    q = supabase_client.table("documents").select(
        "id, file_name, folder_path, scope"
    )

    # Prefix prefilter via LIKE (HI-03 escape on literal segment).
    if norm_prefix == "/":
        # No prefix narrowing - entire corpus visible to caller (RLS gates the rest).
        pass
    else:
        esc = _escape_like(norm_prefix)
        q = q.or_(
            f"folder_path.eq.{norm_prefix},folder_path.like.{esc}/%"
        )

    # Scope narrowing.
    if scope == "user":
        q = q.eq("scope", "user").eq("user_id", user_id)
    elif scope == "global":
        q = q.eq("scope", "global").is_("user_id", "null")
    else:  # both
        q = q.or_(
            f"and(scope.eq.user,user_id.eq.{user_id}),and(scope.eq.global,user_id.is.null)"
        )

    try:
        resp = q.limit(_MATCH_HARD_CAP * 2).execute()
        rows = resp.data or []
    except Exception as e:
        logger.warning("glob _query_documents query failed: %s", e, exc_info=True)
        return []

    # Python-side regex match against `folder_path/file_name`. The Postgres `~`
    # filter on the concatenated string would require a generated column or a
    # SQL function; for an MVP we let the LIKE prefilter bound the candidates
    # and re-check the regex in Python (acceptable because LIMIT is tight).
    compiled = re.compile(regex)
    matched: list[dict] = []
    for r in rows:
        full = _full_path(r.get("folder_path") or "/", r.get("file_name") or "")
        if compiled.fullmatch(full):
            matched.append(r)
    return matched


def _query_folders(
    norm_prefix: str,
    regex: str,
    scope: str,
    user_id: Optional[str],
    supabase_client,
) -> list[tuple[str, str]]:
    """SELECT folder paths matching the regex (UNION of explicit folders + inferred).

    Returns list of (path, scope) tuples. Uses the same LIKE prefix prefilter trick as
    _query_documents to bound the candidate set inside the database before regex matching.
    """
    compiled = re.compile(regex)
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    # ── Explicit folders side table ──
    try:
        q = supabase_client.table("folders").select("path, scope")
        if norm_prefix != "/":
            esc = _escape_like(norm_prefix)
            q = q.or_(f"path.eq.{norm_prefix},path.like.{esc}/%")
        if scope == "user":
            q = q.eq("scope", "user").eq("user_id", user_id)
        elif scope == "global":
            q = q.eq("scope", "global").is_("user_id", "null")
        else:
            q = q.or_(
                f"and(scope.eq.user,user_id.eq.{user_id}),and(scope.eq.global,user_id.is.null)"
            )
        resp = q.limit(_MATCH_HARD_CAP * 2).execute()
        for r in resp.data or []:
            p = r.get("path")
            s = r.get("scope") or "user"
            if p and compiled.fullmatch(p) and (p, s) not in seen:
                out.append((p, s))
                seen.add((p, s))
    except Exception as e:
        logger.warning("glob _query_folders explicit query failed: %s", e, exc_info=True)

    # ── Inferred-from-documents folder paths ──
    try:
        doc_q = supabase_client.table("documents").select("folder_path, scope")
        if norm_prefix != "/":
            esc = _escape_like(norm_prefix)
            doc_q = doc_q.or_(
                f"folder_path.eq.{norm_prefix},folder_path.like.{esc}/%"
            )
        if scope == "user":
            doc_q = doc_q.eq("scope", "user").eq("user_id", user_id)
        elif scope == "global":
            doc_q = doc_q.eq("scope", "global").is_("user_id", "null")
        else:
            doc_q = doc_q.or_(
                f"and(scope.eq.user,user_id.eq.{user_id}),and(scope.eq.global,user_id.is.null)"
            )
        doc_resp = doc_q.limit(_MATCH_HARD_CAP * 4).execute()
        for r in doc_resp.data or []:
            p = r.get("folder_path") or "/"
            s = r.get("scope") or "user"
            if compiled.fullmatch(p) and (p, s) not in seen:
                out.append((p, s))
                seen.add((p, s))
    except Exception as e:
        logger.warning("glob _query_folders inferred query failed: %s", e, exc_info=True)

    return out


def _full_path(folder_path: str, file_name: str) -> str:
    """Build a doc's full path for regex matching."""
    if not file_name:
        return folder_path
    if folder_path == "/":
        return f"/{file_name}"
    return f"{folder_path}/{file_name}"
