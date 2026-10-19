"""test_llm_repeatability.py - LLM_SEED and ANSWER_TEMPERATURE reach every generate call
(spec 2026-09-30, task W8-A1 part A).

THE FACT THIS GUARDS: `google-genai`'s `GenerateContentConfig` is built, separately, at every
one of the app's own Gemini call sites. Unset, `LLM_SEED`/`ANSWER_TEMPERATURE` must leave every
one of them byte-identical to today (no `seed` field at all, answer temperature 0.2); set, EVERY
one must carry the value - tool choice, SQL writer and repair, the four answer prompts, the
reranker's LLM path, the sub-agent/explorer calls and metadata extraction. One helper
(`app.services.llm_config.apply_llm_seed`) does the carrying, so this file both unit-tests it and
structurally enumerates every call site that must wrap its own `GenerateContentConfig(...)` with
it - a call site added later with no wrapper fails the count, not a human review.

FIX ROUND 1 (review finding W8-R1): the per-file `SITES` list below is a hardcoded regression
pin for the 5 files known to need wrapping - it is NOT the safety net. The "discovery, not a
hardcoded list" section further down recursively scans every `.py` file under `backend/app/`
(not a named list) and fails on ANY unwrapped call anywhere in it, RED-proofed first against a
planted violation in a scratch directory. `backend/scripts/` - dormant CLI prototypes such as
`scripts/assemble_verify.py` that the live app never imports - is DELIBERATELY excluded from
that scan and is never wrapped or edited by this fix; that exclusion is itself asserted below,
not just claimed in this comment.

Run:
    venv/Scripts/python -X utf8 tests/test_llm_repeatability.py
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


def _clear_env():
    os.environ.pop("LLM_SEED", None)
    os.environ.pop("ANSWER_TEMPERATURE", None)


# --------------------------------------------------------------------------- settings getters

_clear_env()
from app.services import settings  # noqa: E402

check("RED->GREEN: get_llm_seed() is None when LLM_SEED is unset (today's behaviour)",
      settings.get_llm_seed() is None)

os.environ["LLM_SEED"] = "7"
check("get_llm_seed() reads an integer from LLM_SEED", settings.get_llm_seed() == 7)

os.environ["LLM_SEED"] = "  12  "
check("get_llm_seed() tolerates surrounding whitespace", settings.get_llm_seed() == 12)

os.environ["LLM_SEED"] = "not-a-number"
check("get_llm_seed() falls back to None on an unreadable value (never crashes the request)",
      settings.get_llm_seed() is None)

os.environ["LLM_SEED"] = ""
check("get_llm_seed() treats an empty string as unset", settings.get_llm_seed() is None)
_clear_env()

check("get_answer_temperature() defaults to 0.2 (today's hardcoded value) when unset",
      settings.get_answer_temperature() == 0.2)

os.environ["ANSWER_TEMPERATURE"] = "0"
check("get_answer_temperature() reads 0 from ANSWER_TEMPERATURE",
      settings.get_answer_temperature() == 0.0)

os.environ["ANSWER_TEMPERATURE"] = "0.7"
check("get_answer_temperature() reads a float from ANSWER_TEMPERATURE",
      settings.get_answer_temperature() == 0.7)

os.environ["ANSWER_TEMPERATURE"] = "garbage"
check("get_answer_temperature() falls back to 0.2 on an unreadable value",
      settings.get_answer_temperature() == 0.2)
_clear_env()

# --------------------------------------------------------------------------- apply_llm_seed

from google.genai import types  # noqa: E402
from app.services.llm_config import apply_llm_seed  # noqa: E402

_clear_env()
cfg = types.GenerateContentConfig(temperature=0.2)
out = apply_llm_seed(cfg)
check("apply_llm_seed returns the SAME object (a decorator, not a rebuild)", out is cfg)
check("apply_llm_seed is a no-op with LLM_SEED unset: dump is byte-identical to a plain config",
      out.model_dump(exclude_none=True) == {"temperature": 0.2},
      out.model_dump(exclude_none=True))

os.environ["LLM_SEED"] = "7"
cfg2 = apply_llm_seed(types.GenerateContentConfig(temperature=0.2))
check("apply_llm_seed carries LLM_SEED as config.seed",
      cfg2.model_dump(exclude_none=True) == {"temperature": 0.2, "seed": 7},
      cfg2.model_dump(exclude_none=True))

cfg3 = apply_llm_seed(types.GenerateContentConfig(temperature=0, max_output_tokens=2048))
check("apply_llm_seed carries the seed onto a temperature-0 (tool choice / SQL writer) config too",
      cfg3.seed == 7 and cfg3.temperature == 0)
_clear_env()

# --------------------------------------------------------------------------- every call site, enumerated
#
# Every file below builds `types.GenerateContentConfig(...)` or `genai_types.GenerateContentConfig(...)`
# one or more times. Each occurrence MUST be wrapped `apply_llm_seed(<alias>.GenerateContentConfig(...))`
# - counted structurally so a call site added later with no wrapper fails this test, not a review.
APP = Path(__file__).resolve().parents[1] / "app" / "services"

# (file, expected total GenerateContentConfig( occurrences)
#
# openai_client.py is 11, not 6: part B's `_generate_checked_answer` (the ANSWER_CHECK=1 path)
# added 2 more (its own first generation and its one retry), both wrapped exactly like the
# other 6 - see the `temperature=temperature` check below, which is those two sites reading
# the temperature THROUGH a parameter whose only caller passes `get_answer_temperature()`.
# Task W10-A1 (the agentic second look) added a 9th: `_write_followup_question`'s Gemini
# branch, the ONE new model call this feature introduces (every other generation in that
# flow reuses `_generate_checked_answer`'s own two already-counted sites via `_buffered_
# answer`, so it adds no NEW config site of its own). Task W11-A1 (plan-first) added 2 more,
# its OWN new calls: `_write_question_plan`'s and `_write_check_response`'s Gemini branches -
# the PLAN and CHECK calls. Everything else in that flow (both COMPOSE calls) reuses
# `_buffered_answer`'s own two already-counted sites too, so it adds no site of its own either.
SITES = [
    ("openai_client.py", 11),
    ("sql_tool.py", 2),
    ("reranker.py", 1),
    ("sub_agent.py", 3),
    ("metadata.py", 2),
]

_RAW_RE = re.compile(r"(?<!apply_llm_seed\()\bGenerateContentConfig\(")
_WRAPPED_RE = re.compile(r"apply_llm_seed\(\s*\w+\.GenerateContentConfig\(")
_IMPORT_RE = re.compile(r"\bapply_llm_seed\b")

total_sites = 0
for fname, expected in SITES:
    src = (APP / fname).read_text(encoding="utf-8")
    total = len(re.findall(r"\bGenerateContentConfig\(", src))
    wrapped = len(_WRAPPED_RE.findall(src))
    total_sites += expected
    check(f"{fname}: {expected} GenerateContentConfig( call site(s) found (none added or removed "
          f"unnoticed)", total == expected, f"found {total}")
    check(f"{fname}: EVERY GenerateContentConfig( call is wrapped in apply_llm_seed(...) "
          f"(no call site missed)", wrapped == total, f"{wrapped} of {total} wrapped")
    check(f"{fname}: imports apply_llm_seed", bool(_IMPORT_RE.search(src)))

check("19 call sites total across the app (14 from part A, +2 from part B's checked-answer "
      "retry path, +1 from task W10-A1's follow-up-question call, +2 from task W11-A1's "
      "plan/check calls, each wrapped the same way)",
      total_sites == 19, total_sites)

# --------------------------------------------------------------------------- discovery, not a hardcoded list
#
# Review finding W8-R1 (fix round 1): the SITES list above is a hardcoded set of 5 files, so a
# NEW file anywhere under backend/app/ with its own unwrapped GenerateContentConfig( call would
# never be checked at all. This section is the actual safety net: it DISCOVERS every .py file
# under backend/app/ recursively and demands every GenerateContentConfig( in every one of them
# is wrapped - no file list to fall out of date. `backend/scripts/` (dormant CLI prototypes the
# live app never imports - e.g. `scripts/assemble_verify.py`, doc-prep's stage-3 assembly tool)
# is DELIBERATELY out of scope and is never scanned, wrapped or edited by this fix: Controller
# ruling W8-R1 is explicit that those scripts stay untouched.
import ast  # noqa: E402
import tempfile  # noqa: E402

BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_ROOT / "app"
SCRIPTS_ROOT = BACKEND_ROOT / "scripts"


def _call_name(node) -> str:
    """The bare or attribute name a `Call` node invokes (`f(...)` -> 'f',
    `mod.f(...)` -> 'f'), or "" when it is neither (a call through a subscript, say)."""
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return ""


def _discover_config_sites(root: Path):
    """(path, total GenerateContentConfig(...) CALLS, how many are the direct argument of an
    apply_llm_seed(...) call) for every .py file under `root` with at least one such call.

    PARSED WITH `ast`, not a text regex - a text scan over `\\bGenerateContentConfig\\(`
    counts the name wherever it appears, docstrings and comments included, which is exactly
    how a first version of this fix mis-flagged `llm_config.py` itself (its own module
    docstring names `GenerateContentConfig(` twice in prose). `ast.walk` only ever sees real
    code, so a file that merely MENTIONS the name in a comment or a docstring example is never
    counted - recursive, no hardcoded file list either way."""
    out = []
    for py_file in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError:
            continue
        parent_of = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parent_of[child] = node
        total = wrapped = 0
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _call_name(node) == "GenerateContentConfig"):
                continue
            total += 1
            parent = parent_of.get(node)
            if isinstance(parent, ast.Call) and _call_name(parent) == "apply_llm_seed":
                wrapped += 1
        if total:
            out.append((py_file, total, wrapped))
    return out


# RED FIRST: prove the discovery function itself would catch a planted unwrapped call, in a
# scratch directory well outside the real tree - before trusting it to give the real tree a
# clean bill of health below.
with tempfile.TemporaryDirectory() as tmp:
    tmp_root = Path(tmp)
    planted = tmp_root / "planted_violation.py"

    planted.write_text(
        "from google.genai import types\n"
        "config = types.GenerateContentConfig(temperature=0.5)\n",
        encoding="utf-8",
    )
    red = [(p, t, w) for p, t, w in _discover_config_sites(tmp_root) if w < t]
    check("RED: a planted UNWRAPPED GenerateContentConfig( call in a scratch file is caught "
          "by the discovery function",
          len(red) == 1 and red[0][1:] == (1, 0), red)

    planted.write_text(
        "from google.genai import types\n"
        "from app.services.llm_config import apply_llm_seed\n"
        "config = apply_llm_seed(types.GenerateContentConfig(temperature=0.5))\n",
        encoding="utf-8",
    )
    green = [(p, t, w) for p, t, w in _discover_config_sites(tmp_root) if w < t]
    check("GREEN: the SAME call, once wrapped, is not flagged (no false positive)", green == [])

# GREEN ON THE REAL TREE: every .py file under backend/app/, discovered - not SITES above.
real_sites = _discover_config_sites(APP_ROOT)
real_unwrapped = [(str(p.relative_to(BACKEND_ROOT)), t, w) for p, t, w in real_sites if w < t]
check("every GenerateContentConfig( call anywhere under backend/app/ (discovered recursively, "
      "every .py file, not a hardcoded list) is wrapped in apply_llm_seed(...)",
      real_unwrapped == [], real_unwrapped)
check("the recursive discovery over backend/app/ finds the same 19 total sites SITES does",
      sum(t for _, t, _ in real_sites) == 19, sum(t for _, t, _ in real_sites))

# The exclusion, stated AND verified - not just asserted in a comment: backend/scripts/ is a
# real directory, genuinely holds an unwrapped call, and is never touched by this fix.
check("backend/scripts/ exists (the directory this test deliberately does not scan)",
      SCRIPTS_ROOT.is_dir())
assemble_verify = SCRIPTS_ROOT / "assemble_verify.py"
check("backend/scripts/assemble_verify.py exists and genuinely has an unwrapped "
      "GenerateContentConfig( call - confirming there really is something out of scope here, "
      "left untouched on purpose (Controller ruling W8-R1), not silently already compliant",
      assemble_verify.is_file()
      and "GenerateContentConfig(" in assemble_verify.read_text(encoding="utf-8")
      and "apply_llm_seed" not in assemble_verify.read_text(encoding="utf-8"))

# --------------------------------------------------------------------------- temperature wiring
#
# The four ANSWER prompts (openai_client ~L1197, 1361, 1976, 1992, 2066 - 5 call sites, one pair
# of them the streaming/non-streaming attempt of the SAME prompt) read ANSWER_TEMPERATURE; the
# tool choice and the SQL writer/repair NEVER do - they are pinned at a literal temperature=0.
oc_src = (APP / "openai_client.py").read_text(encoding="utf-8")
answer_temp_sites = len(re.findall(r"temperature=get_answer_temperature\(\)", oc_src))
check("openai_client.py: exactly 5 call sites read get_answer_temperature() DIRECTLY (the 4 "
      "answer prompts; one of them tried twice, streaming then non-streaming)",
      answer_temp_sites == 5, answer_temp_sites)
checked_answer_temp_sites = len(re.findall(r"temperature=temperature\b", oc_src))
check("openai_client.py: the checked-answer path's 2 configs read temperature through a "
      "parameter (never a second literal 0.2 or a second hardcoded 0)",
      checked_answer_temp_sites == 2, checked_answer_temp_sites)
check("openai_client.py: 8 uses of get_answer_temperature() in total (5 direct answer-"
      "prompt sites + 1 passed into _generate_checked_answer's parameter + 1 for task "
      "W10-A1's second-look flow + 1 for task W11-A1's plan-first flow, each of the latter "
      "two reading it once and reusing that one value across its own buffered generations "
      "and plan/follow-up call rather than calling it again)",
      len(re.findall(r"get_answer_temperature\(\)", oc_src)) == 8)
check("openai_client.py: the tool choice stays a literal temperature=0, never ANSWER_TEMPERATURE",
      "temperature=0,\n" in oc_src or re.search(r"temperature=0,\s*\n", oc_src))
check("openai_client.py: get_answer_temperature is imported",
      "get_answer_temperature" in oc_src and "from app.services.settings import" in oc_src)

sql_src = (APP / "sql_tool.py").read_text(encoding="utf-8")
check("sql_tool.py: the SQL writer and its repair call never read ANSWER_TEMPERATURE "
      "(temperature=0 literal, twice)",
      "get_answer_temperature" not in sql_src
      and len(re.findall(r"temperature=0\b", sql_src)) == 2)

# --------------------------------------------------------------------------- behavioural: byte-identical when unset
#
# Mirrors exactly what an answer-prompt call site now does; with both switches unset the
# resulting config must dump identically to literal `GenerateContentConfig(system_instruction=...,
# temperature=0.2)` - today's byte-for-byte call.
_clear_env()
answer_cfg = apply_llm_seed(types.GenerateContentConfig(
    system_instruction="x", temperature=settings.get_answer_temperature()))
check("an answer-prompt config, switches unset, dumps byte-identical to today's literal 0.2",
      answer_cfg.model_dump(exclude_none=True) == {"system_instruction": "x", "temperature": 0.2},
      answer_cfg.model_dump(exclude_none=True))

os.environ["ANSWER_TEMPERATURE"] = "0"
os.environ["LLM_SEED"] = "42"
answer_cfg2 = apply_llm_seed(types.GenerateContentConfig(
    system_instruction="x", temperature=settings.get_answer_temperature()))
check("both switches set: the answer config carries both the new temperature and the seed",
      answer_cfg2.model_dump(exclude_none=True) ==
      {"system_instruction": "x", "temperature": 0.0, "seed": 42},
      answer_cfg2.model_dump(exclude_none=True))
_clear_env()

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
