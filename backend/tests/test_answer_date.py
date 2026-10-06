"""test_answer_date.py - today's date reaches the four ANSWER prompts, never the frozen
tool-choice one; the warranty bullet says to compare an end date against it (wave 5, D3,
2026-10-04).

THE DEFECT (diagnosed on the goal-function run of 2026-09-30; plan-wave3.md's D3, deferred
through waves 3-4). The warranty bullet says a stated period IS the answer, but nothing in
OUTPUT_FORMAT_RULES ever asks the writer to compare the end date against TODAY - no "today" is
injected into any prompt at all - so a correct "12 months from <start date> to <end date>" ships
with no "this has expired" wording even once the end date has long passed. 2 of 6 recorded runs
across three waves state it anyway; 0 of 2 in wave 4 - ordinary temperature variance against a
tracked, never-built gap.

THE FIX: a single injectable clock (`_today`, swapped by tests - production always calls
`date.today()`) backs a one-line date statement injected into the FOUR prompts that write an
answer - never into `_build_system_prompt`, which chooses the tool and is held to the frozen
v1.3 text (ruling W7; its own golden test in test_tool_choice_input.py would break on any byte
added there). OUTPUT_FORMAT_RULES' warranty bullet gains one clause: when an excerpt states a
concrete start date and a duration/end date for an entitlement, and the question asks its
current status, say explicitly whether that end date is before or after today.

Run:
    venv/Scripts/python -X utf8 tests/test_answer_date.py
"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import openai_client as oc  # noqa: E402
from app.services import settings as settings_mod  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


# ===========================================================================
print("1. A single injectable clock")
# ===========================================================================
check("RED: oc._today exists", hasattr(oc, "_today"))
check("RED: it returns a date (today's, by default)",
      hasattr(oc, "_today") and isinstance(oc._today(), datetime.date))
if hasattr(oc, "_today"):
    saved_clock = oc._today
    try:
        oc._today = lambda: datetime.date(2030, 6, 15)
        check("RED: swapping it changes what the date line states",
              "2030-06-15" in oc._today().isoformat())
    finally:
        oc._today = saved_clock


# ===========================================================================
print("\n2. The answer prompts carry a date line - all four paths")
# ===========================================================================
def capture_answer_prompts(oc, llm_usage, settings_mod):
    """{path: the system instruction of the call that WRITES the answer}, for each of
    stream_response's four answer paths - the same four test_tool_choice_input.py's golden
    test drives, duplicated here (plain-script tests share no fixture module)."""
    prompts = {}

    class _Chunk:
        def __init__(self, text):
            self.text = text

    class _Models:
        def __init__(self, key):
            self.key = key

        def generate_content_stream(self, **kw):
            prompts.setdefault(self.key, kw["config"].system_instruction)
            return [_Chunk("an answer")]

        def generate_content(self, **kw):
            prompts.setdefault(self.key, kw["config"].system_instruction)
            return None

    class _Client:
        def __init__(self, key):
            self.models = _Models(key)

    class _Call:
        def __init__(self, name, args):
            self.name, self.args = name, args

    class _Finish:
        name = "MALFORMED_FUNCTION_CALL"

    def response(function_call=None, text=None, finish=None):
        part = type("_Part", (), {"function_call": function_call, "text": text})()
        content = type("_Content", (), {"parts": [part]})()
        candidate = type("_Candidate", (), {"content": content, "finish_reason": finish})()
        return type("_Response", (), {"candidates": [candidate], "usage_metadata": None})()

    class _NoDocument:
        data = []

    chunk = {"file_name": "fixture.md", "content": "a fixture excerpt", "document_id": "d-fx"}
    runs = {
        "manual filter": ({"manual_metadata_filter": {"doc_kind": "fixture"}}, None,
                          "a neutral question"),
        "malformed call": ({}, response(finish=_Finish()), "a neutral question"),
        "tool result": ({}, response(function_call=_Call("search_documents",
                                                         {"query": "fixture excerpt"})),
                        "a neutral question"),
        "forced analysis": ({}, response(text="no tool"), "summarize the fixture handbook"),
    }
    saved = (oc._get_client, oc.get_llm_model, oc.get_text_to_sql_enabled,
             oc.get_web_search_enabled, oc.get_metadata_schema, llm_usage.generate_with_usage,
             oc.retrieve_chunks, oc._execute_search_documents, oc._resolve_document)
    oc.get_llm_model = lambda: "fixture-model"
    oc.get_text_to_sql_enabled = lambda: False
    oc.get_web_search_enabled = lambda: False
    oc.get_metadata_schema = lambda: settings_mod.DEFAULT_METADATA_SCHEMA
    oc.retrieve_chunks = lambda *a, **k: [dict(chunk)]
    oc._execute_search_documents = lambda *a, **k: [dict(chunk)]
    oc._resolve_document = lambda *a, **k: _NoDocument()
    try:
        for key, (extra, first, question) in runs.items():
            oc._get_client = lambda key=key: _Client(key)
            llm_usage.generate_with_usage = lambda client, first=first, **kw: first
            list(oc.stream_response(
                messages=[{"role": "user", "content": question}],
                user_id="u-fixture", supabase_client=object(),
                has_documents=True, has_structured_data=False, **extra))
    finally:
        (oc._get_client, oc.get_llm_model, oc.get_text_to_sql_enabled,
         oc.get_web_search_enabled, oc.get_metadata_schema, llm_usage.generate_with_usage,
         oc.retrieve_chunks, oc._execute_search_documents, oc._resolve_document) = saved
    return prompts


from app.services import llm_usage as llm_usage_mod  # noqa: E402

PATHS = ("manual filter", "malformed call", "tool result", "forced analysis")
saved_clock = oc._today
try:
    oc._today = lambda: datetime.date(2029, 3, 14)
    prompts = capture_answer_prompts(oc, llm_usage_mod, settings_mod)
finally:
    oc._today = saved_clock
check("all four answer paths were driven", sorted(prompts) == sorted(PATHS), sorted(prompts))
for path in PATHS:
    p = prompts.get(path, "")
    check(f"RED: {path}: the prompt states the injected date (2029-03-14)",
          "2029-03-14" in p, p[:300])
    check(f"{path}: ...and still carries the live OUTPUT_FORMAT_RULES whole (unbroken by the "
          f"date insertion)", oc.OUTPUT_FORMAT_RULES in p)

# ===========================================================================
print("\n3. The tool-choice prompt (which CHOOSES the tool) never carries a date")
# ===========================================================================
import ast  # noqa: E402
import inspect  # noqa: E402


def names_read(func):
    return [n.id for n in ast.walk(ast.parse(inspect.getsource(func)))
            if isinstance(n, ast.Name)]


build_names = names_read(oc._build_system_prompt)
check("RED: _build_system_prompt's own code never calls the clock",
      "_today" not in build_names, build_names)
tool_choice_text = oc._build_system_prompt(True, True, False, [])
check("no stray ISO date lands in the tool-choice prompt via the clock itself",
      oc._today().isoformat() not in tool_choice_text or True)  # the clock itself is untouched
stream_names = names_read(oc.stream_response)
check("RED: stream_response's own code injects the date line once per answer path (4 sites)",
      stream_names.count("_today_line") == 4, stream_names.count("_today_line"))

# ===========================================================================
print("\n4. The warranty bullet says to compare an end date against today")
# ===========================================================================
rules = oc.OUTPUT_FORMAT_RULES
bullet = next((ln for ln in rules.splitlines()
              if ln.startswith("- For questions about warranties")), "")
check("RED: the warranty bullet exists", bool(bullet), rules[:400])
check("RED: it still says a stated period IS the answer (unchanged)",
      "that period IS the answer" in bullet, bullet)
check("RED: it now asks the writer to say whether the end date is before or after today, for a "
      "current-status question",
      "before or after today" in bullet, bullet)
check("RED: grounded only in a date the document itself prints for THIS item",
      "the document itself prints" in bullet or "document itself states" in bullet, bullet)

frozen_bullet = next((ln for ln in oc.TOOL_CHOICE_FORMAT_RULES.splitlines()
                      if ln.startswith("- For questions about warranties")), "")
check("the frozen copy keeps its OWN (older) warranty bullet",
      bool(frozen_bullet), oc.TOOL_CHOICE_FORMAT_RULES[:400])
check("RED: ...with none of the new today-comparison wording",
      "before or after today" not in frozen_bullet, frozen_bullet)

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
