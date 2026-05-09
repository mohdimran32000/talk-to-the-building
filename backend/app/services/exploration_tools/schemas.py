"""TOOL-06: Pydantic v2 BaseModel argument schemas for the five exploration tools.

Every model uses:
  - Literal["user","global","both"] for scope (rejects invalid scope at parse time)
  - Field(..., ge=, le=) for numeric bounds (server-side cap on max_depth, limit, A/B/C)
  - regex pattern for path (matches Migration 012 canonical-form CHECK byte-identical)
  - extra='ignore' (Phase 3 / Plan 01 LOCKED defense layer — silently drops smuggled fields)

Public API: TreeArgs, GlobArgs, GrepArgs, ListFilesArgs, ReadDocumentArgs.

NOTE: these models validate the LLM's tool-call arguments, NOT user input. The LLM
is the only caller. There is no router-layer Pydantic FastAPI binding here.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

# Canonical path regex — MUST be byte-identical to:
#   - backend/app/services/folder_service.py:23  (_CANONICAL_PATH_RE)
#   - backend/migrations/012_folder_path_and_scope.sql L42 (CHECK constraint)
# Pitfall 4 (path normalization drift) triple chokepoint — DO NOT redefine with
# subtly-different escaping.
_PATH_RE = r"^/$|^/[^/]+(/[^/]+)*$"


class TreeArgs(BaseModel):
    """TOOL-01 args."""
    path: str = Field(
        "/",
        pattern=_PATH_RE,
        description="Canonical folder path; '/' for root.",
    )
    max_depth: int = Field(
        2,
        ge=1,
        le=4,
        description="Max recursion depth. Server-capped at 4 (Pitfall 2 RANK 4); default 2.",
    )
    scope: Literal["user", "global", "both"] = Field(
        "both",
        description="'user' | 'global' | 'both'. Default 'both'.",
    )

    model_config = {"extra": "ignore"}


class GlobArgs(BaseModel):
    """TOOL-02 args."""
    pattern: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Glob with `**` (any depth) and `*` (no slash) semantics. "
                    "Examples: '**/*.pdf', 'projects/**/floor-plans/*'.",
    )
    path: str = Field(
        "/",
        pattern=_PATH_RE,
        description="Restrict matching to this prefix.",
    )
    type: Literal["file", "folder", "both"] = Field(
        "both",
        description="Match files only, folders only, or both.",
    )
    scope: Literal["user", "global", "both"] = Field("both")

    model_config = {"extra": "ignore"}


class GrepArgs(BaseModel):
    """TOOL-03 args."""
    pattern: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Postgres-flavor regex. Pathological patterns (nested unbounded "
                    "quantifiers like (.*)+ ) rejected by Python wrapper pre-screen.",
    )
    path: str = Field(
        "/",
        pattern=_PATH_RE,
        description="Restrict to documents under this folder prefix.",
    )
    case_insensitive: bool = Field(
        True,
        description="If True, regex matches case-insensitively.",
    )
    multiline: bool = Field(
        False,
        description="If True, '.' matches newlines (rarely needed).",
    )
    output_mode: Literal["content", "files_with_matches", "count"] = Field(
        "content",
        description="'content' returns lines + context; 'files_with_matches' returns "
                    "only matching docs; 'count' returns hit counts per doc.",
    )
    A: int = Field(2, ge=0, le=10, description="Lines AFTER each match.")
    B: int = Field(2, ge=0, le=10, description="Lines BEFORE each match.")
    C: Optional[int] = Field(
        None,
        ge=0,
        le=10,
        description="If set, overrides A and B with the same value.",
    )
    scope: Literal["user", "global", "both"] = Field("both")

    model_config = {"extra": "ignore"}


class ListFilesArgs(BaseModel):
    """TOOL-04 args."""
    path: str = Field("/", pattern=_PATH_RE)
    scope: Literal["user", "global", "both"] = Field("both")

    model_config = {"extra": "ignore"}


class ReadDocumentArgs(BaseModel):
    """TOOL-05 args. Specify exactly one of document_id OR path."""
    document_id: Optional[str] = Field(
        None,
        description="UUID of the document. Either this OR `path` is required.",
    )
    path: Optional[str] = Field(
        None,
        pattern=r"^/$|^/[^/]+(/[^/]+)*/[^/]+$",
        description="Folder + file_name combo, e.g. '/projects/readme.md'. "
                    "Either this OR `document_id` is required.",
    )
    offset: int = Field(
        1,
        ge=1,
        description="1-based line number to START at (Claude Code convention).",
    )
    limit: int = Field(
        2000,
        ge=1,
        le=5000,
        description="Lines to return. Default 2000. Hard cap 5000.",
    )

    model_config = {"extra": "ignore"}

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.document_id is None) == (self.path is None):
            raise ValueError("Specify exactly one of document_id or path")
        return self
