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
import re
import unicodedata

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
