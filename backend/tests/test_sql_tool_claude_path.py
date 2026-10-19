"""test_sql_tool_claude_path.py - LLM_PROVIDER_SQL wires execute_sql_query onto Claude,
Gemini unchanged when unset (spec 2026-09-30, task W9-A1).

THE PROOF OF "BYTE-IDENTICAL WHEN UNSET" (base commit `3816958`, branch `answer-check`).
Section 1 is a source-text pin of the Gemini branch's two call sites (the primary
generation and the repair call) - both must still read EXACTLY
`temperature=0, max_output_tokens=2048` (the primary also keeps the thinking-budget
comment, pinned already by `test_sql_runaway_guard.py`; this file pins the keyword
VALUES specifically, which that file does not). Section 2 runs `execute_sql_query`
end-to-end through the SAME fake-Gemini-client pattern `test_sql_composition.py`
already uses, with every new switch unset, and proves the Claude path is never even
IMPORTED - monkeypatching `llm_provider.generate_sql_text` to raise would turn a
regression (an accidental unconditional Claude call) into a loud failure here, not a
silent behaviour change.

Section 3 proves the Claude path itself: `LLM_PROVIDER_SQL=anthropic` sends the EXACT
SAME prompt text a Gemini run would have built (captured from both and compared),
through `generate_sql_text`, and the fake's returned text becomes the executed SQL -
including the repair call's path (section 4), where a first query that DuckDB rejects
is repaired through `generate_sql_text(..., name="sql_repair")` too, never falling back
to Gemini mid-request.

No network call anywhere in this file - every provider is a fake.

Run:
    venv/Scripts/python -X utf8 tests/test_sql_tool_claude_path.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import llm_provider, sql_tool  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


# --------------------------------------------------------------------------- fixtures

WIDGETS = {
    "table_name": "fx_widgets",
    "columns": ["widget_id", "kind", "qty"],
    "rows": [
        {"widget_id": "W-1", "kind": "gadget", "qty": "3"},
        {"widget_id": "W-2", "kind": "gizmo", "qty": "5"},
    ],
    "row_count": 2,
}
TABLES = [WIDGETS]
QUESTION = "how many widgets are there?"


class _ExecResult:
    def __init__(self, data):
        self.data = data


class _Supabase:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        tables = self.tables if name == "structured_data" else []

        class _Q:
            def select(self, *a, **kw):
                return self

            def eq(self, *a, **kw):
                return self

            def order(self, *a, **kw):
                return self

            def execute(self):
                return _ExecResult(list(tables))

        return _Q()


class _GeminiModels:
    """Fakes `genai.Client().models` - records every prompt it is handed and never
    needs touching when the Claude path is under test (section 3/4 assert this fake
    is never called at all)."""
    sql = 'SELECT SUM(qty) FROM "fx_widgets"'
    calls = []

    def generate_content(self, *, model, contents, config=None):
        _GeminiModels.calls.append({"model": model, "contents": contents, "config": config})
        return type("_R", (), {"text": _GeminiModels.sql, "usage_metadata": None})()


class _GeminiClient:
    def __init__(self, *a, **kw):
        self.models = _GeminiModels()


def _run(question=QUESTION, tables=TABLES):
    """`execute_sql_query` end to end, with the Gemini fake always wired in (so an
    accidental fall-through to Gemini is visible via `_GeminiModels.calls`, not
    hidden by a missing fake)."""
    saved = (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
             sql_tool._load_table_cards)
    sql_tool.genai.Client = _GeminiClient
    sql_tool.get_llm_api_key = lambda: "fake-key"
    sql_tool.get_llm_model = lambda: "fake-gemini-model"
    sql_tool._load_table_cards = lambda *a, **k: []
    _GeminiModels.calls = []
    try:
        return sql_tool.execute_sql_query(question, "u-1", _Supabase(tables))
    finally:
        (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
         sql_tool._load_table_cards) = saved


def _clear_provider_env():
    import os
    for name in ("LLM_PROVIDER_SQL", "ANTHROPIC_MODEL_SQL", "ANTHROPIC_API_KEY"):
        os.environ.pop(name, None)


# --------------------------------------------------------------------------- 1. source-text pin

print("1. the Gemini branch's two call sites are untouched - exact keyword values pinned")
src = (Path(__file__).resolve().parents[1] / "app" / "services" / "sql_tool.py").read_text(
    encoding="utf-8")
check("the primary generation's Gemini config still reads temperature=0",
      "temperature=0,\n" in src)
check("both Gemini configs (primary + repair) still cap max_output_tokens at 2048, "
      "exactly twice - no new Gemini call site, none removed",
      src.count("max_output_tokens=2048") == 2, src.count("max_output_tokens=2048"))
check("the primary call is still named sql_generate for usage recording",
      'name="sql_generate",' in src)
check("get_llm_provider/generate_sql_text are imported for the new branch",
      "from app.services.llm_provider import get_llm_provider, generate_sql_text" in src)
check("exactly two `if get_llm_provider(\"sql\") == \"anthropic\":` branches "
      "(primary + repair, no more, no fewer)",
      src.count('if get_llm_provider("sql") == "anthropic":') == 2,
      src.count('if get_llm_provider("sql") == "anthropic":'))

# --------------------------------------------------------------------------- 2. unset -> Gemini, byte-identical

print("\n2. LLM_PROVIDER_SQL unset: Gemini runs exactly as before; Claude is never touched")
_clear_provider_env()


def _claude_boom(*a, **k):
    raise AssertionError("generate_sql_text must not be called when LLM_PROVIDER_SQL is unset")


saved_claude_fn = llm_provider.generate_sql_text
llm_provider.generate_sql_text = _claude_boom
try:
    result = _run()
finally:
    llm_provider.generate_sql_text = saved_claude_fn

check("execute_sql_query still returns a normal markdown result",
      "SELECT" not in result.split("\n")[0], result[:120])
check("the Gemini fake WAS called (exactly once for a query that succeeds first try)",
      len(_GeminiModels.calls) == 1, _GeminiModels.calls)
unset_call = _GeminiModels.calls[0]
check("Gemini's config still carries temperature=0 and max_output_tokens=2048, unchanged",
      unset_call["config"] is not None and unset_call["config"].temperature == 0
      and unset_call["config"].max_output_tokens == 2048)
unset_prompt = unset_call["contents"]
check("the prompt is the plain string sql_tool always built (not restructured)",
      isinstance(unset_prompt, str) and unset_prompt.startswith("You are a SQL expert."))

# --------------------------------------------------------------------------- 3. anthropic path: same prompt text

print("\n3. LLM_PROVIDER_SQL=anthropic: the SAME prompt text reaches a fake Claude call")
import os  # noqa: E402

os.environ["LLM_PROVIDER_SQL"] = "anthropic"
os.environ["ANTHROPIC_MODEL_SQL"] = "claude-haiku-5-5"

claude_calls = []


def _fake_generate_sql_text(prompt, *, max_output_tokens=2048, client=None, name="sql_generate"):
    claude_calls.append({"prompt": prompt, "max_output_tokens": max_output_tokens, "name": name})
    return type("_R", (), {"text": 'SELECT SUM(qty) FROM "fx_widgets"'})()


def _gemini_boom(*, model, contents, config=None):
    raise AssertionError("the Gemini fake must not be called when LLM_PROVIDER_SQL=anthropic")


saved_claude_fn = llm_provider.generate_sql_text
llm_provider.generate_sql_text = _fake_generate_sql_text
saved_gemini_generate = _GeminiModels.generate_content
_GeminiModels.generate_content = _gemini_boom
try:
    claude_result = _run()
finally:
    llm_provider.generate_sql_text = saved_claude_fn
    _GeminiModels.generate_content = saved_gemini_generate
    _clear_provider_env()

check("the Claude fake was called exactly once", len(claude_calls) == 1, claude_calls)
check("the Gemini fake was never called at all", _GeminiModels.calls == [], _GeminiModels.calls)
check('called with name="sql_generate", same as the Gemini path\'s usage-recording name',
      claude_calls[0]["name"] == "sql_generate")
check("max_output_tokens defaults to 2048, matching Gemini's own budget",
      claude_calls[0]["max_output_tokens"] == 2048)
check("the prompt text handed to Claude is EXACTLY what the Gemini path built for the "
      "identical question/tables (captured in section 2) - requirement 3's 'the SAME "
      "prompt text the Gemini path builds goes to Claude'",
      claude_calls[0]["prompt"] == unset_prompt)
check("the fake's returned SQL text drove the real result (DuckDB actually ran it: SUM(3+5)=8)",
      "8.0" in claude_result, claude_result[:300])

# --------------------------------------------------------------------------- 4. anthropic path: the repair call

print("\n4. LLM_PROVIDER_SQL=anthropic: a failing first query is repaired through Claude too")
os.environ["LLM_PROVIDER_SQL"] = "anthropic"

repair_calls = []
_first_sql = {"value": 'SELECT SUM(not_a_real_column) FROM "fx_widgets"'}


def _fake_generate_sql_text_with_repair(prompt, *, max_output_tokens=2048, client=None,
                                        name="sql_generate"):
    if name == "sql_repair":
        repair_calls.append({"prompt": prompt, "name": name})
        return type("_R", (), {"text": 'SELECT SUM(qty) FROM "fx_widgets"'})()
    return type("_R", (), {"text": _first_sql["value"]})()


saved_claude_fn = llm_provider.generate_sql_text
llm_provider.generate_sql_text = _fake_generate_sql_text_with_repair
_GeminiModels.generate_content = _gemini_boom
try:
    repaired_result = _run()
finally:
    llm_provider.generate_sql_text = saved_claude_fn
    _GeminiModels.generate_content = saved_gemini_generate
    _clear_provider_env()

check("the repair call happened (the first query's bad column forced it)",
      len(repair_calls) == 1, repair_calls)
check('the repair call is named "sql_repair"', repair_calls[0]["name"] == "sql_repair")
check("the repair prompt names the actual DuckDB error (same shape as the Gemini repair "
      "prompt text)",
      "not_a_real_column" in repair_calls[0]["prompt"]
      and "Fix the query" in repair_calls[0]["prompt"], repair_calls[0]["prompt"][:200])
check("the repaired SQL (from the fake) drove the final result, not the original failing one (SUM(3+5)=8)",
      "8.0" in repaired_result, repaired_result[:300])
check("the Gemini fake was never called during the repair flow either",
      _GeminiModels.calls == [], _GeminiModels.calls)

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
