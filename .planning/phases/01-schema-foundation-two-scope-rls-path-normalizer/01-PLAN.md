---
phase: 01
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - backend/app/services/folder_service.py
autonomous: true
requirements:
  - FOLDER-01
must_haves:
  truths:
    - "normalize_path('/') returns '/'"
    - "normalize_path('/a/b') returns '/a/b' (case preserved)"
    - "normalize_path('/a/b/') returns '/a/b' (trailing slash stripped, except root)"
    - "normalize_path('a/b') returns '/a/b' (leading slash prepended)"
    - "normalize_path('/a//b') returns '/a/b' (double slash collapsed)"
    - "normalize_path('\\\\a\\\\b') returns '/a/b' (backslash to slash)"
    - "normalize_path('') returns '/' and normalize_path(None) returns '/'"
    - "normalize_path('/a/../b') raises ValueError (path traversal rejected)"
    - "normalize_path('/a/./b') raises ValueError (current-dir segment rejected)"
    - "Result of normalize_path is always matched by the canonical regex ^/$|^/[^/]+(/[^/]+)*$"
  artifacts:
    - path: "backend/app/services/folder_service.py"
      provides: "normalize_path() pure-function helper, _CANONICAL_PATH_RE constant, _FORBIDDEN_SEGMENTS constant"
      exports: ["normalize_path"]
      min_lines: 30
  key_links:
    - from: "backend/app/services/folder_service.py::normalize_path"
      to: "Phase 3 folder CRUD + Phase 4 tool arg parsing + tests"
      via: "from app.services.folder_service import normalize_path"
      pattern: "def normalize_path"
---

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| LLM/UI input -> normalize_path() | Untrusted folder_path strings cross here from LLM tool args, UI uploads, drag-move operations |
| normalize_path() -> DB CHECK regex | Defense-in-depth: Python rejects traversal segments; DB CHECK rejects malformed canonical form |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-1-02 | Tampering / Information Disclosure | folder_service.normalize_path | mitigate | Reject `..` and `.` path segments with `ValueError` (Pitfall 4 + Pitfalls §security path traversal). Reject backslashes (Windows-origin LLM hallucination). Apply Unicode NFC normalization (prevents visually-identical-bytes-different attack). The DB CHECK regex (delivered in plan 02) provides defense in depth — Python catches `..`, regex catches everything else. |
| T-1-04 (foundational) | Tampering | folder_service module surface | mitigate | Single canonical chokepoint — every Phase 3+ write path imports `normalize_path` from this module. No other code re-implements path canonicalization. (Pitfall 4 fix.) |
</threat_model>

<objective>
Create `backend/app/services/folder_service.py` containing the pure-function `normalize_path(p: str | None) -> str` helper. This is the single canonical chokepoint for folder-path canonicalization (FOLDER-01) and the project-wide defense against path-normalization drift (Pitfall 4) and path-traversal attacks (`..`, `.`). The function takes a possibly-malformed user/LLM/UI path string and returns a string in canonical form (leading slash, no trailing slash except root, no double slashes, no backslashes, NFC-normalized Unicode), or raises `ValueError` on path-traversal attempts. No DB access, no I/O — pure transformation. Phase 3 will extend this same file with folder CRUD; Phase 1 ships only `normalize_path` so it exists and is importable by tests in plan 08 and by every future write path.
</objective>

<execution_context>
@.claude/get-shit-done/workflows/execute-plan.md
@.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md

@.planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-RESEARCH.md
@.planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-PATTERNS.md
@.planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-VALIDATION.md
@.planning/research/PITFALLS.md
@.planning/codebase/CONVENTIONS.md
@CLAUDE.md

@backend/app/services/record_manager.py

<interfaces>
<!-- Module-level export contract that downstream phases (and plan 08 tests) consume.
     Executor must implement exactly this signature. -->

From backend/app/services/folder_service.py (NEW):
```python
def normalize_path(p: str | None) -> str:
    """Canonicalize a folder path. Returns canonical form or raises ValueError."""
```

Canonical form invariants (verified by _CANONICAL_PATH_RE):
- Leading '/' always
- No trailing '/' except for root '/'
- No '//' (double slashes)
- No '\\' (backslashes)
- Segments must not equal '..' or '.' (raises ValueError)
- NFC-normalized Unicode
- Case preserved

Reference regex: `^/$|^/[^/]+(/[^/]+)*$`
</interfaces>
</context>

<tasks>

<task id="1-01-01" type="auto" tdd="true">
  <name>Task 1: Create folder_service.py with normalize_path() and inline pytest-style assertions</name>
  <files>backend/app/services/folder_service.py</files>
  <read_first>
    - backend/app/services/record_manager.py (canonical "small stateless service module with pure helpers" analog — copy module-docstring-then-imports-then-pure-functions shape)
    - backend/app/services/web_search.py (single-function module pattern with module-level logger — use as reference for minimal surface)
    - .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-RESEARCH.md § Decisions §3 (lines ~209-285 — DEFINITIVE reference Python implementation including all 12 round-trip cases and the security rationale for `..`/`.` rejection)
    - .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-PATTERNS.md § "backend/app/services/folder_service.py" (lines ~258-305 — confirms record_manager.py is the analog, naming/style conventions)
    - .planning/codebase/CONVENTIONS.md § "Naming Patterns", § "Import Organization", § "Module Design" (snake_case, type hints required, no __all__, underscore-prefix for module-private)
    - .planning/research/PITFALLS.md § "Pitfall 4: Path normalization drift" (lines ~102-128 — security context for `..` rejection)
  </read_first>
  <behavior>
    - normalize_path('/') == '/'
    - normalize_path('/a/b') == '/a/b'
    - normalize_path('/a/b/c') == '/a/b/c'
    - normalize_path('/A/B') == '/A/B' (case preserved — no lowercasing)
    - normalize_path('/a//b') == '/a/b' (double slash collapsed)
    - normalize_path('a/b') == '/a/b' (leading slash prepended)
    - normalize_path('/a/b/') == '/a/b' (trailing slash stripped)
    - normalize_path('\\\\a\\\\b') == '/a/b' (backslash replaced with slash)
    - normalize_path('') == '/' (empty -> root)
    - normalize_path(None) == '/' (None -> root)
    - normalize_path('/a/../b') raises ValueError (path traversal rejected)
    - normalize_path('/a/./b') raises ValueError (current-dir segment rejected)
    - normalize_path('/foo/../../etc/passwd') raises ValueError
    - normalize_path of an NFD-decomposed Unicode string produces NFC-normalized output
    - Every successful return value matches the regex ^/$|^/[^/]+(/[^/]+)*$
  </behavior>
  <action>
    Create `backend/app/services/folder_service.py` with the EXACT module shape below. This is paste-ready from RESEARCH.md § Decisions §3 with the conventions from PATTERNS.md applied (module docstring, snake_case, underscore-prefix module-private constants, type hints, raises ValueError per CONVENTIONS.md `record_manager.py` style).

```python
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
```

Conventions to honor (per .planning/codebase/CONVENTIONS.md and 01-PATTERNS.md):
- Module docstring on lines 1-N (triple-quoted, describes purpose + Phase 1 vs Phase 3 scope split).
- Imports: stdlib only (re, unicodedata) — no third-party, no `from app.…` (this module is a leaf).
- snake_case function name `normalize_path` — NOT `normalizePath`.
- Type hints on every signature (`p: str | None) -> str`).
- `_CANONICAL_PATH_RE` and `_FORBIDDEN_SEGMENTS` use underscore prefix to mark module-private (matches `_token_cache` / `_client_cache` patterns in test_helpers.py / metadata.py / settings.py).
- Raise `ValueError` (Pythonic for invalid string input) — do NOT define a custom exception class for v1.
- No `__all__`, no barrel exports — direct `from app.services.folder_service import normalize_path` is the importable surface.
- No module-level logger — this is a pure function; no logging needed (and matches record_manager.py which also omits logger).

Do NOT add any folder CRUD logic (list_folder, create_folder, move_document, rename_folder, delete_folder) — those are Phase 3's job per RESEARCH.md.
Do NOT add DB access — this is a pure-function module.
Do NOT lowercase the path (Pitfall 4 warning: "/Projects" and "/projects" are intentionally distinct in Postgres case-sensitive comparison).
  </action>
  <verify>
    <automated>cd backend &amp;&amp; venv/Scripts/python -m app.services.folder_service</automated>
  </verify>
  <acceptance_criteria>
    - File `backend/app/services/folder_service.py` exists.
    - `grep -c "def normalize_path" backend/app/services/folder_service.py` returns at least 1.
    - `grep -c "_CANONICAL_PATH_RE" backend/app/services/folder_service.py` returns at least 2 (definition + use).
    - `grep -c "_FORBIDDEN_SEGMENTS" backend/app/services/folder_service.py` returns at least 2.
    - `grep -c "unicodedata.normalize" backend/app/services/folder_service.py` returns at least 1 (NFC normalization present).
    - `grep -c "raise ValueError" backend/app/services/folder_service.py` returns at least 1.
    - `grep -c "import unicodedata" backend/app/services/folder_service.py` returns 1.
    - `grep -c "import re" backend/app/services/folder_service.py` returns 1.
    - `grep -E "from\\s+(langchain|langgraph)" backend/app/services/folder_service.py` returns no matches (CLAUDE.md: no LangChain/LangGraph).
    - `cd backend && venv/Scripts/python -m app.services.folder_service` exits 0 and prints "folder_service.normalize_path: 15 self-tests passed".
    - `cd backend && venv/Scripts/python -c "from app.services.folder_service import normalize_path; assert normalize_path('/a//b') == '/a/b'; assert normalize_path(None) == '/'; print('OK')"` prints "OK".
    - `cd backend && venv/Scripts/python -c "from app.services.folder_service import normalize_path; \nfor bad in ['/a/../b', '/a/./b']:\n    try:\n        normalize_path(bad); raise SystemExit('FAIL: ' + bad)\n    except ValueError:\n        pass\nprint('OK')"` prints "OK".
  </acceptance_criteria>
  <done>
    folder_service.py exists with normalize_path() exported. All 15 inline self-tests pass when invoked via `python -m app.services.folder_service`. Module is importable from `app.services.folder_service`. No CRUD code added (Phase 3 scope). No path lowercasing. `..` and `.` segments raise ValueError.
  </done>
</task>

</tasks>

<verification>
Maps to .planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-VALIDATION.md row "FOLDER-01" (line ~52). Falsifiable assertions 17–28 from RESEARCH.md § Validation Architecture (Group 3: Path normalization). The full Python test matrix is owned by plan 08 (`test_two_scope_rls.py`) and re-imports `normalize_path` from this file; the inline `if __name__ == "__main__"` block is a sanity check that runs in <1 second.

Run after task: `cd backend && venv/Scripts/python -m app.services.folder_service` -> exit code 0.
</verification>

<success_criteria>
- `backend/app/services/folder_service.py` exists with `normalize_path` exported.
- Inline self-test block exits 0 and confirms 15 cases (11 OK + 4 raise).
- `from app.services.folder_service import normalize_path` succeeds from any backend module.
- Round-trip identities hold: `'/'`, `'/a/b'`, `'/a/b/c'` survive normalization unchanged (ROADMAP success criterion 5).
- `..` and `.` segments raise `ValueError` (Pitfall 4 path-traversal mitigation).
</success_criteria>

<output>
After completion, create `.planning/phases/01-schema-foundation-two-scope-rls-path-normalizer/01-01-SUMMARY.md` recording: file created, line count, list of normalization rules implemented, list of round-trip cases verified, list of rejection cases verified, and the import path Phase 3 will use.
</output>
