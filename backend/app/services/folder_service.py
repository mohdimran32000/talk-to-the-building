"""
Folder Service — path canonicalization helper for the two-scope folder model.

This module is the SINGLE canonical chokepoint for folder-path normalization
across Episode 2 (per FOLDER-01 / Pitfall 4). Every code path that writes a
folder_path — UI upload, drag-move, folder rename, backfill, tool arg parsing —
MUST call `normalize_path()` first. The DB CHECK constraint on
`documents.folder_path` (migration 012) and `folders.path` (migration 013) is
defense-in-depth; this Python helper is the primary enforcement layer.

Phase 1 ships only `normalize_path`. Phase 3 extends this file with folder CRUD
(`list_folder`, `create_folder`, `move_document`, `rename_folder`, `delete_folder`).
"""
import logging
import re
import unicodedata
import uuid as _uuid

logger = logging.getLogger(__name__)

# Canonical path regex (mirrors the DB CHECK constraint added in migration 012/013).
# Matches: '/' OR '/segment' OR '/segment/segment/...'  where segment = [^/]+
_CANONICAL_PATH_RE = re.compile(r"^/$|^/[^/]+(/[^/]+)*$")

# Path segments that are forbidden after splitting on '/'. These are path-traversal
# attack vectors (Pitfalls §security: "LLM passes path traversal like ../other-user-folder").
# The DB CHECK regex DOES NOT reject `..` (it has no `/` inside, so it passes [^/]+),
# so Python is the enforcement layer for these.
_FORBIDDEN_SEGMENTS = frozenset({"..", "."})


def normalize_path(p: str | None) -> str:
    """Canonicalize a folder path string.

    Canonical form: leading slash always, no trailing slash (except root '/'),
    no double slashes, no backslashes, NFC-normalized Unicode, case preserved.

    Args:
        p: A possibly-malformed folder path from UI / LLM / API input. None or
           empty string is treated as the root '/'.

    Returns:
        The canonical form of `p`. Always begins with '/'. Always satisfies
        `_CANONICAL_PATH_RE`.

    Raises:
        ValueError: If any segment is '.' or '..' (path traversal attempt) or
                    if the result fails the canonical-form regex check.
    """
    if p is None or p == "":
        return "/"
    s = unicodedata.normalize("NFC", p)
    s = s.replace("\\", "/")
    while "//" in s:
        s = s.replace("//", "/")
    if not s.startswith("/"):
        s = "/" + s
    if len(s) > 1 and s.endswith("/"):
        s = s.rstrip("/")
    if s == "":
        s = "/"
    if s != "/":
        for seg in s.lstrip("/").split("/"):
            if seg in _FORBIDDEN_SEGMENTS or seg == "":
                raise ValueError(
                    f"Invalid path segment: {seg!r} in {p!r} "
                    f"(path traversal segments '.' and '..' are forbidden)"
                )
    if not _CANONICAL_PATH_RE.match(s):
        raise ValueError(f"Path failed canonical form check: {s!r} (input was {p!r})")
    return s


# ──────────────────────────────────────────────────────────────────────────
# Phase 3 — Folder service CRUD (FOLDER-02). All five functions below:
#   - take supabase_client as positional-untyped (matches record_manager.py)
#   - run normalize_path() on every path argument as the FIRST STATEMENT
#     (Pitfall 4 chokepoint enforcement; belt+suspenders alongside routers)
#   - have NO FastAPI imports — pure service layer, unit-testable in isolation
#   - return plain dicts (not Pydantic models — that's the router's job)
# ──────────────────────────────────────────────────────────────────────────


def _escape_like(s: str) -> str:
    """Escape LIKE wildcard metacharacters in a literal string.

    HI-03: Migration 012's canonical-form regex `^/[^/]+(/[^/]+)*$` ALLOWS `%`
    and `_` in folder segments. When a folder name contains these characters
    and we build a LIKE predicate `f"{prefix}/%"`, the literal `_` becomes a
    single-char wildcard and the literal `%` becomes a multi-char wildcard,
    causing over-matching (e.g. /foo_bar's predicate also matches /fooXbar/).

    Postgres LIKE uses `\\` as the default escape character (no explicit
    ESCAPE clause needed), so prefixing each `\\`, `%`, and `_` with `\\` is
    sufficient. Order matters: escape `\\` FIRST so we do not double-escape
    the backslashes we just inserted.
    """
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _assert_uuid(value: str | None, field_name: str = "user_id") -> None:
    """Defense-in-depth UUID validator.

    HI-01: list_folder() builds PostgREST `.or_()` filters via f-string
    interpolation of `user_id`. Today JWT-derived user_id values are UUIDs, but
    interpolating untrusted-shape strings into a query DSL is a defense-in-depth
    violation. If user_id ever picked up `,` `)` `(` `.` etc., the OR-clause
    structure could be subverted to drop the per-user filter. Validate at the
    service-layer entry point so the contract is enforced regardless of what
    the router passes.

    Allows None — callers that legitimately pass None (e.g. scope='global'
    paths in other helpers) are unaffected.

    Raises:
        ValueError: if `value` is neither None nor a syntactically valid UUID.
    """
    if value is None:
        return
    try:
        _uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError(f"invalid {field_name}: not a UUID")


def list_folder(
    path: str,
    scope: str,
    user_id: str | None,
    supabase_client,
) -> dict:
    """List one level of a folder: documents at this path + immediate subfolders.

    Returns:
        {
          "path": str,                # normalized path
          "documents": list[dict],    # rows where folder_path == path (filtered by scope)
          "subfolders": list[dict],   # each item: {"id": str | None, "path": str}
                                      # id is the folders-table UUID when an explicit row
                                      # exists, None when the folder is inferred from
                                      # documents only (D-06; consumed by Plan 06-05/06-06
                                      # frontend to wire PATCH/DELETE /api/folders/{id}).
        }

    Subfolder discovery:
    1. Explicit folders rows whose path is an immediate child of `path`
       (path LIKE norm||'/%' AND path NOT LIKE norm||'/%/%' — exactly one level deeper).
    2. Inferred subfolder names from documents.folder_path: take all docs where
       folder_path LIKE norm||'/%', strip the norm||'/' prefix, take everything
       up to the first '/'. Deduplicate.

    Scope handling: 'both' returns union of user (matching user_id) + global; 'user' filters
    to the calling user; 'global' filters to user_id IS NULL.

    HI-01 contract: when scope is 'user' or 'both', user_id is interpolated into a
    PostgREST `.or_()` DSL string. We validate it as a UUID at the boundary so a
    malformed value cannot subvert the OR clause structure. Callers that legitimately
    pass user_id=None must use scope='global' (which never interpolates user_id).
    """
    norm = normalize_path(path)

    # HI-01: defense in depth against PostgREST DSL injection via user_id f-strings.
    if scope in ("user", "both"):
        _assert_uuid(user_id, field_name="user_id")

    # ─ Documents at this exact folder ─
    docs_q = supabase_client.table("documents").select("*").eq("folder_path", norm)
    if scope == "user":
        docs_q = docs_q.eq("scope", "user").eq("user_id", user_id)
    elif scope == "global":
        docs_q = docs_q.eq("scope", "global").is_("user_id", "null")
    else:  # 'both' — union via or_(); see PostgREST or() syntax
        docs_q = docs_q.or_(
            f"and(scope.eq.user,user_id.eq.{user_id}),and(scope.eq.global,user_id.is.null)"
        )
    try:
        docs_resp = docs_q.execute()
        documents = docs_resp.data or []
    except Exception as e:
        # MD-03: log so operators can see real failures (mis-config, table-disabled,
        # column rename) instead of an empty result indistinguishable from "no data."
        # We still fall back to an empty list because the function returns a partial
        # result composed of three independent queries — failing one should not
        # blank the others. Future hardening: narrow to APIError + map to 5xx.
        logger.error(f"list_folder documents query failed for path={path!r} scope={scope!r}: {e}", exc_info=True)
        documents = []

    # ─ Explicit folders rows (immediate children) ─
    # Predicate "immediate child of norm":
    #   if norm == '/': path != '/' AND path NOT LIKE '/%/%'
    #   else:           path LIKE norm||'/%' AND path NOT LIKE norm||'/%/%'
    # D-06: each item is {"id": <uuid str>, "path": <str>} so the GET /api/folders
    # wire shape carries a UUID the frontend can use to call PATCH/DELETE
    # /api/folders/{id} without a separate path->id lookup round-trip.
    explicit_subfolders: list[dict] = []
    try:
        f_q = supabase_client.table("folders").select("id, path")
        if scope == "user":
            f_q = f_q.eq("scope", "user").eq("user_id", user_id)
        elif scope == "global":
            f_q = f_q.eq("scope", "global").is_("user_id", "null")
        else:
            f_q = f_q.or_(
                f"and(scope.eq.user,user_id.eq.{user_id}),and(scope.eq.global,user_id.is.null)"
            )
        if norm == "/":
            # Root: any path with two or more '/' is strictly deeper than /child.
            f_q = f_q.neq("path", "/").not_.like("path", "/%/%")
        else:
            # HI-03: escape `%` and `_` in `norm` so a folder name containing
            # those literals does not become a wildcard in the LIKE predicate.
            esc = _escape_like(norm)
            f_q = f_q.like("path", f"{esc}/%").not_.like("path", f"{esc}/%/%")
        f_resp = f_q.execute()
        explicit_subfolders = [
            {"id": row["id"], "path": row["path"]}
            for row in (f_resp.data or [])
        ]
    except Exception as e:
        # MD-03: log explicit-folders query failures (see comment on the
        # documents-query block above for rationale).
        logger.error(f"list_folder explicit-folders query failed for path={path!r} scope={scope!r}: {e}", exc_info=True)
        explicit_subfolders = []

    # ─ Inferred subfolders from documents.folder_path (descendants below norm) ─
    # Strip the norm||'/' prefix and take first segment.
    inferred_subfolders: set[str] = set()
    try:
        inf_q = supabase_client.table("documents").select("folder_path")
        if scope == "user":
            inf_q = inf_q.eq("scope", "user").eq("user_id", user_id)
        elif scope == "global":
            inf_q = inf_q.eq("scope", "global").is_("user_id", "null")
        else:
            inf_q = inf_q.or_(
                f"and(scope.eq.user,user_id.eq.{user_id}),and(scope.eq.global,user_id.is.null)"
            )
        prefix = "/" if norm == "/" else f"{norm}/"
        # HI-03: escape `%` and `_` in the prefix so folder names containing
        # those literals do not become wildcards in the LIKE predicate. The
        # Python-side `if fp.startswith(prefix):` filter below provides a
        # second-line cross-check using the original (unescaped) prefix.
        like_prefix = "/" if norm == "/" else f"{_escape_like(norm)}/"
        inf_q = inf_q.like("folder_path", f"{like_prefix}%")
        inf_resp = inf_q.execute()
        for row in (inf_resp.data or []):
            fp = row.get("folder_path") or ""
            if fp.startswith(prefix):
                rest = fp[len(prefix):]
                first_seg = rest.split("/", 1)[0]
                if first_seg:
                    # Reconstruct the canonical immediate-child path.
                    inferred_subfolders.add(prefix + first_seg if norm == "/" else f"{norm}/{first_seg}")
    except Exception as e:
        # MD-03: log inferred-subfolders query failures (see documents-query block above).
        logger.error(f"list_folder inferred-subfolders query failed for path={path!r} scope={scope!r}: {e}", exc_info=True)

    # D-06: Union explicit + inferred as List[{id, path}].
    # Explicit-folder paths carry their UUID; inferred-only paths carry id=None
    # so the frontend can disable rename/delete affordances on them.
    explicit_by_path: dict[str, str] = {f["path"]: f["id"] for f in explicit_subfolders}
    all_paths = sorted(set(explicit_by_path.keys()) | inferred_subfolders)
    all_subfolders = [
        {"id": explicit_by_path.get(p), "path": p}
        for p in all_paths
    ]

    return {
        "path": norm,
        "documents": documents,
        "subfolders": all_subfolders,
    }


def create_folder(
    path: str,
    scope: str,
    user_id: str | None,
    supabase_client,
) -> dict:
    """Create an explicit folders row via Migration 019's atomic upsert RPC.

    Idempotent: if a row already exists at (scope, COALESCE(user_id,'00..0'), path),
    returns the existing row with action='exists'. Otherwise inserts a new row and
    returns it with action='created'.

    For scope='global' the caller MUST pass user_id=None (the RPC raises a
    check_violation otherwise via the coupling rule).
    """
    norm = normalize_path(path)

    result = supabase_client.rpc("create_folder_if_not_exists", {
        "p_scope": scope,
        "p_user_id": user_id,   # None for scope='global'
        "p_path": norm,
    }).execute()

    if not result.data:
        # RPC returned no rows — should not happen for a well-formed call, but be defensive.
        return {"id": None, "scope": scope, "user_id": user_id, "path": norm,
                "created_at": None, "action": "exists"}

    row = result.data[0]
    # Hydrate the full folders row so the caller (router) gets created_at.
    # MD-02: supabase-py's `.maybe_single()` RAISES on 0 rows (HTTP 204) — the
    # `if full_q else {}` guard is dead code because the exception fires before
    # assignment. Wrap in try/except so a row vanishing between the RPC and
    # this hydration (concurrent delete) does not turn into a 500. Mirrors the
    # try/except idiom in record_manager.determine_action.
    try:
        full_q = supabase_client.table("folders").select("*").eq("id", row["id"]).maybe_single().execute()
        full = (full_q.data or {}) if full_q else {}
    except Exception:
        full = {}

    return {
        "id": row["id"],
        "scope": full.get("scope", scope),
        "user_id": full.get("user_id", user_id),
        "path": full.get("path", norm),
        "created_at": full.get("created_at"),
        "action": "created" if row.get("created") else "exists",
    }


def move_document(
    document_id: str,
    new_folder_path: str,
    user_id: str,
    supabase_client,
) -> dict | None:
    """Move a document to a new folder. Scope is immutable (Migration 015 trigger).

    UPDATE filters .eq('id', document_id).eq('user_id', user_id) — defense in depth
    against cross-user moves alongside RLS. Returns the updated document row, or
    None if no row matched (e.g., wrong owner).
    """
    norm = normalize_path(new_folder_path)

    result = (
        supabase_client.table("documents")
        .update({"folder_path": norm})
        .eq("id", document_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        return None
    return result.data[0]


def rename_folder(
    old_path: str,
    new_path: str,
    scope: str,
    user_id: str | None,
    supabase_client,
) -> dict:
    """Rename a folder (transactional prefix update on documents + folders).

    Calls Migration 019's rename_folder_prefix RPC — the only cross-table-atomic
    unit available from supabase-py (PostgREST executes each .execute() in its own
    transaction; only RPCs span multiple statements atomically). FOLDER-03.

    Raises:
        ValueError: if old_path or new_path normalize to '/' (root rename forbidden;
                    defense in depth alongside the RPC's own root-rename check).
    """
    old_norm = normalize_path(old_path)
    new_norm = normalize_path(new_path)
    if old_norm == "/" or new_norm == "/":
        raise ValueError("cannot rename root path")

    result = supabase_client.rpc("rename_folder_prefix", {
        "p_old_prefix": old_norm,
        "p_new_prefix": new_norm,
        "p_scope": scope,
        "p_user_id": user_id,    # None for scope='global'
    }).execute()

    if not result.data:
        return {"documents_updated": 0, "folders_updated": 0}
    row = result.data[0]
    return {
        "documents_updated": row.get("documents_updated", 0),
        "folders_updated": row.get("folders_updated", 0),
    }


def delete_folder(
    folder_id: str,
    supabase_client,
) -> dict:
    """Delete a folder iff empty (single-transaction empty-check + delete).

    Calls Migration 019's delete_folder_if_empty RPC, which uses SELECT ... FOR UPDATE
    on the folders row to serialize concurrent rename / delete attempts on that
    row. FOLDER-04 + Pitfall 5.

    HI-02 / LO-03 (Phase 3 review): the FOR UPDATE lock is row-scoped and does
    NOT block concurrent INSERTs into documents at the same folder_path. Under
    Strategy B (folders is a sparse, explicit-empty-only side table), the
    interleaving "delete folder while concurrent upload" is race-free for data
    integrity but user-visibly confusing: the folder appears to "come back" on
    next list as an inferred folder. UIs MUST refresh after delete; callers
    needing strict serialization with uploads should obtain an external lock.

    Returns:
        On success: {deleted: True, document_count: 0, subfolder_count: 0}
        On non-empty: {deleted: False, error: 'FOLDER_NOT_EMPTY',
                       document_count: int, subfolder_count: int}

    The 'no_data_found' SQLSTATE from the RPC (folder missing) propagates as an
    Exception — the router (Plan 04) catches it and returns 404. The service
    layer does NOT catch the missing-folder case; the caller decides.
    """
    result = supabase_client.rpc("delete_folder_if_empty", {
        "p_folder_id": folder_id,
    }).execute()

    if not result.data:
        return {"deleted": False, "error": "FOLDER_NOT_EMPTY",
                "document_count": 0, "subfolder_count": 0}

    row = result.data[0]
    if row.get("deleted"):
        return {"deleted": True, "document_count": 0, "subfolder_count": 0}
    return {
        "deleted": False,
        "error": "FOLDER_NOT_EMPTY",
        "document_count": row.get("document_count", 0),
        "subfolder_count": row.get("subfolder_count", 0),
    }


# Inline self-tests — runnable via `python -m app.services.folder_service` for fast sanity checks.
# The full normalize_path test matrix lives in scripts/test_two_scope_rls.py (plan 08).
if __name__ == "__main__":
    cases_ok = [
        ("/", "/"),
        ("/a", "/a"),
        ("/a/b", "/a/b"),
        ("/a/b/c", "/a/b/c"),
        ("/A/B", "/A/B"),
        ("/a//b", "/a/b"),
        ("a/b", "/a/b"),
        ("/a/b/", "/a/b"),
        ("\\a\\b", "/a/b"),
        ("", "/"),
        (None, "/"),
    ]
    for inp, want in cases_ok:
        got = normalize_path(inp)
        assert got == want, f"normalize_path({inp!r}) -> {got!r}, want {want!r}"
    cases_raise = ["/a/../b", "/a/./b", "/foo/../../etc/passwd", "/."]
    for inp in cases_raise:
        try:
            normalize_path(inp)
            raise AssertionError(f"normalize_path({inp!r}) should have raised ValueError")
        except ValueError:
            pass
    print(f"folder_service.normalize_path: {len(cases_ok) + len(cases_raise)} self-tests passed")
