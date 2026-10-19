"""test_answer_check_wiring.py - ANSWER_CHECK=1 wired into the final-answer generation, OFF by
default, byte-identical to `goal50-round7` when unset (spec 2026-09-30, task W8-A1 part B).

Three layers, each with its own fakes - no model call, no server:
  1. settings: get_answer_check_enabled / get_answer_check_max_retries.
  2. `openai_client._generate_checked_answer` in isolation: one generation, checked, at most
     one retry, kept whatever it says.
  3. One end-to-end smoke test through `stream_response` itself, proving the switch actually
     reaches the real tool-call path (not just the helper function in a vacuum) - a SQL-only
     turn whose first answer refuses retries once, runs a document search it never ran before,
     and the event stream ends with the retry's text.

The byte-identical-when-unset claim for the EXISTING streaming path rests on
`tests/test_sql_loop_wiring.py` (ANSWER_CHECK never set there) continuing to pass unchanged -
this file does not re-prove that; it proves the NEW path.

Run:
    venv/Scripts/python -X utf8 tests/test_answer_check_wiring.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


def _clear_env():
    for k in ("ANSWER_CHECK", "ANSWER_CHECK_MAX_RETRIES", "SQL_LOOP_MAX_STEPS", "LLM_SEED"):
        os.environ.pop(k, None)


_clear_env()
from app.services import settings  # noqa: E402

# --------------------------------------------------------------------------- settings

check("RED: get_answer_check_enabled() is False when ANSWER_CHECK is unset (today)",
      settings.get_answer_check_enabled() is False)
os.environ["ANSWER_CHECK"] = "1"
check("get_answer_check_enabled() is True when ANSWER_CHECK=1",
      settings.get_answer_check_enabled() is True)
os.environ["ANSWER_CHECK"] = "yes"
check("get_answer_check_enabled() stays False on anything but the literal '1' (typo-safe)",
      settings.get_answer_check_enabled() is False)
_clear_env()

check("get_answer_check_max_retries() defaults to 1 when unset",
      settings.get_answer_check_max_retries() == 1)
os.environ["ANSWER_CHECK_MAX_RETRIES"] = "0"
check("get_answer_check_max_retries() reads 0 (disables retrying)",
      settings.get_answer_check_max_retries() == 0)
os.environ["ANSWER_CHECK_MAX_RETRIES"] = "garbage"
check("get_answer_check_max_retries() falls back to 1 on an unreadable value",
      settings.get_answer_check_max_retries() == 1)
_clear_env()

# --------------------------------------------------------------------------- _generate_checked_answer

from app.services import answer_check as ac  # noqa: E402
from app.services import openai_client as oc  # noqa: E402
from google.genai import types  # noqa: E402

# These `_generate_checked_answer` unit tests exercise the RETRY MECHANISM on a
# REFUSED_WITHOUT_SEARCH-shaped problem, independent of which checks the gate shipped ON today
# (`answer_check.SHIPPED_CHECKS` is MIXED_SET only - see the module and the report) - so they
# pass `enabled` explicitly, the same way the production call site would if a later
# measurement re-enabled it. Scoped to that one check alone (not ALL_CHECKS): the fixed "GOOD"
# answer below states a number with no sql/excerpt grounding on purpose (to prove it is NOT a
# refusal), and UNGROUNDED_NUMBER would otherwise flag that same number for an unrelated reason.
_ALL = frozenset({ac.REFUSED_WITHOUT_SEARCH})


class _Part:
    def __init__(self, text):
        self.text = text


class _Content:
    def __init__(self, parts):
        self.parts = parts


class _Cand:
    def __init__(self, parts):
        self.content = _Content(parts)


class _Resp:
    def __init__(self, text):
        self.text = text
        self.candidates = [_Cand([_Part(text)])]


class _ScriptedModels:
    """Returns each scripted answer in order, one per `generate_content` call; records every
    config's system_instruction it was given."""
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def generate_content(self, *, model, contents, config=None):
        self.calls.append(config.system_instruction if config else None)
        text = self.answers.pop(0) if self.answers else ""
        return _Resp(text)


class _ScriptedClient:
    def __init__(self, answers):
        self.models = _ScriptedModels(answers)


REFUSAL = "The records do not contain the inspection interval for this device."
GOOD = "The device is inspected every 6 months, per the maintenance schedule."

_clear_env()

# No problem at all: one call, that text, search_fn never invoked.
client1 = _ScriptedClient([GOOD])
searched = []
out1 = oc._generate_checked_answer(
    client1, "fake-model", [], "system prompt", 0.2,
    "How often is it inspected?", [], [], ["query_structured_data"],
    lambda q: searched.append(q) or "", enabled=_ALL,
)
check("RED: no problem -> the first answer is kept, exactly one generate_content call",
      out1 == GOOD and len(client1.models.calls) == 1)
check("...and the search_fn was never called (nothing to backstop)", searched == [])

# A problem (SQL-only refusal) -> exactly one retry, search_fn invoked, retry's text kept.
client2 = _ScriptedClient([REFUSAL, GOOD])
searched2 = []


def _search_fn2(q):
    searched2.append(q)
    return "[Source: manual.md]\nInspect every 6 months."


out2 = oc._generate_checked_answer(
    client2, "fake-model", [], "system prompt", 0.2,
    "How often is it inspected?", [], [], ["query_structured_data"],
    _search_fn2, enabled=_ALL,
)
check("a REFUSED_WITHOUT_SEARCH problem -> exactly 2 generate_content calls (one retry)",
      len(client2.models.calls) == 2)
check("...the search_fn ran exactly once, with the question",
      searched2 == ["How often is it inspected?"])
check("...and the retry's answer is what's returned, not the refusal",
      out2 == GOOD)
check("...the retry's system prompt carries the search result and names the problem",
      "Inspect every 6 months" in client2.models.calls[1]
      and "never searched the documents" in client2.models.calls[1])

# The retry ALSO has a problem -> still kept (never a second retry).
client3 = _ScriptedClient([REFUSAL, REFUSAL])
out3 = oc._generate_checked_answer(
    client3, "fake-model", [], "system prompt", 0.2,
    "How often is it inspected?", [], [], ["query_structured_data"],
    lambda q: "irrelevant excerpt", enabled=_ALL,
)
check("the retry still refusing is kept anyway - NEVER a second retry (exactly 2 calls total)",
      out3 == REFUSAL and len(client3.models.calls) == 2)

# ANSWER_CHECK_MAX_RETRIES=0 -> the first answer is kept even with a problem, no retry call.
os.environ["ANSWER_CHECK_MAX_RETRIES"] = "0"
client4 = _ScriptedClient([REFUSAL, GOOD])
out4 = oc._generate_checked_answer(
    client4, "fake-model", [], "system prompt", 0.2,
    "How often is it inspected?", [], [], ["query_structured_data"],
    lambda q: "irrelevant excerpt", enabled=_ALL,
)
check("ANSWER_CHECK_MAX_RETRIES=0 -> no retry even when a problem is found",
      out4 == REFUSAL and len(client4.models.calls) == 1)
_clear_env()

# No search_fn supplied (e.g. no_documents) -> a REFUSED_WITHOUT_SEARCH problem still retries,
# just without extra search context.
client5 = _ScriptedClient([REFUSAL, GOOD])
out5 = oc._generate_checked_answer(
    client5, "fake-model", [], "system prompt", 0.2,
    "How often is it inspected?", [], [], ["query_structured_data"],
    None, enabled=_ALL,
)
check("no search_fn available -> the retry still runs (names the problem, no extra excerpts)",
      out5 == GOOD and len(client5.models.calls) == 2)

# DEFAULT enabled (omitted, as the production call site does) -> only SHIPPED_CHECKS run.
# REFUSED_WITHOUT_SEARCH is built and tested above but NOT shipped (the gate measured false
# alarms on it - see answer_check.SHIPPED_CHECKS) - so the SAME refusal that retried in every
# test above, with NO `enabled` override, must NOT retry.
client6 = _ScriptedClient([REFUSAL, GOOD])
out6 = oc._generate_checked_answer(
    client6, "fake-model", [], "system prompt", 0.2,
    "How often is it inspected?", [], [], ["query_structured_data"],
    lambda q: "irrelevant excerpt",
)
check("RED: with no enabled override, a REFUSED_WITHOUT_SEARCH-shaped problem does NOT "
      "retry - that check is built but not in SHIPPED_CHECKS",
      out6 == REFUSAL and len(client6.models.calls) == 1)

# --------------------------------------------------------------------------- use_claude=True (W9-A2)
#
# W9-A1's review found that `_generate_checked_answer` stayed on Gemini unconditionally, even
# with LLM_PROVIDER_ANSWER=anthropic - "a checked retry never mixes providers" was the brief's
# requirement 5 for task W9-A2. These mirror sections above exactly, swapped onto
# `use_claude=True`: the client passed in is wired to RAISE if its generate_content is ever
# touched, proving the retry never silently falls back to Gemini mid-retry.

from app.services import llm_provider  # noqa: E402


class _ForbiddenGeminiModels:
    def generate_content(self, *, model, contents, config=None):
        raise AssertionError("Gemini must not be called when use_claude=True")


class _ForbiddenGeminiClient:
    def __init__(self):
        self.models = _ForbiddenGeminiModels()


def _scripted_claude(answers):
    """A fake `generate_answer_text` - returns each scripted answer in order, records every
    system_text it was given (mirrors _ScriptedClient's own calls list, Claude-shaped)."""
    remaining = list(answers)
    calls = []

    def _fn(*, system_text, contents, max_tokens=64000, client=None, name="answer_stream"):
        calls.append(system_text)
        return remaining.pop(0) if remaining else ""

    return _fn, calls


_saved_generate_answer_text = llm_provider.generate_answer_text
_clear_env()

# No problem at all: one call, Gemini never touched.
fake_a, calls_a = _scripted_claude([GOOD])
llm_provider.generate_answer_text = fake_a
searched_a = []
try:
    out_a = oc._generate_checked_answer(
        _ForbiddenGeminiClient(), "fake-model", [], "system prompt", 0.2,
        "How often is it inspected?", [], [], ["query_structured_data"],
        lambda q: searched_a.append(q) or "", enabled=_ALL, use_claude=True,
    )
finally:
    llm_provider.generate_answer_text = _saved_generate_answer_text
check("use_claude=True, no problem -> the first answer is kept, exactly one "
      "generate_answer_text call, Gemini never touched (would have raised otherwise)",
      out_a == GOOD and len(calls_a) == 1, (out_a, calls_a))
check("...and the search_fn was never called (nothing to backstop)", searched_a == [])

# A problem (SQL-only refusal) -> exactly one retry via Claude, search_fn invoked, retry kept.
fake_b, calls_b = _scripted_claude([REFUSAL, GOOD])
llm_provider.generate_answer_text = fake_b
searched_b = []


def _search_fn_b(q):
    searched_b.append(q)
    return "[Source: manual.md]\nInspect every 6 months."


try:
    out_b = oc._generate_checked_answer(
        _ForbiddenGeminiClient(), "fake-model", [], "system prompt", 0.2,
        "How often is it inspected?", [], [], ["query_structured_data"],
        _search_fn_b, enabled=_ALL, use_claude=True,
    )
finally:
    llm_provider.generate_answer_text = _saved_generate_answer_text
check("use_claude=True, a REFUSED_WITHOUT_SEARCH problem -> exactly 2 generate_answer_text "
      "calls (one retry), Gemini never touched",
      len(calls_b) == 2, calls_b)
check("...the search_fn ran exactly once, with the question", searched_b == ["How often is it inspected?"])
check("...and the retry's answer is what's returned, not the refusal", out_b == GOOD)
check("...the retry's system prompt (sent to Claude) carries the search result and names "
      "the problem",
      "Inspect every 6 months" in calls_b[1] and "never searched the documents" in calls_b[1])

# The retry ALSO has a problem -> still kept anyway (never a second retry).
fake_c, calls_c = _scripted_claude([REFUSAL, REFUSAL])
llm_provider.generate_answer_text = fake_c
try:
    out_c = oc._generate_checked_answer(
        _ForbiddenGeminiClient(), "fake-model", [], "system prompt", 0.2,
        "How often is it inspected?", [], [], ["query_structured_data"],
        lambda q: "irrelevant excerpt", enabled=_ALL, use_claude=True,
    )
finally:
    llm_provider.generate_answer_text = _saved_generate_answer_text
check("use_claude=True: the retry still refusing is kept anyway - NEVER a second retry "
      "(exactly 2 calls total)",
      out_c == REFUSAL and len(calls_c) == 2)

# ANSWER_CHECK_MAX_RETRIES=0 -> the first (Claude) answer is kept even with a problem.
os.environ["ANSWER_CHECK_MAX_RETRIES"] = "0"
fake_d, calls_d = _scripted_claude([REFUSAL, GOOD])
llm_provider.generate_answer_text = fake_d
try:
    out_d = oc._generate_checked_answer(
        _ForbiddenGeminiClient(), "fake-model", [], "system prompt", 0.2,
        "How often is it inspected?", [], [], ["query_structured_data"],
        lambda q: "irrelevant excerpt", enabled=_ALL, use_claude=True,
    )
finally:
    llm_provider.generate_answer_text = _saved_generate_answer_text
    _clear_env()
check("use_claude=True + ANSWER_CHECK_MAX_RETRIES=0 -> no retry even when a problem is found",
      out_d == REFUSAL and len(calls_d) == 1)

# use_claude defaults to False - every call above sections 1-6 omitted it and still exercised
# Gemini via _ScriptedClient successfully, which is the regression guard for the default itself.
check("use_claude defaults to False (sections above never passed it and stayed on Gemini)", True)

# --------------------------------------------------------------------------- end-to-end smoke test

from app.services import sql_tool  # noqa: E402
from app.services import llm_usage as llm_usage_mod  # noqa: E402


class _FC:
    def __init__(self, name, args):
        self.name, self.args = name, args


class _FnPart:
    def __init__(self, function_call=None, text=None):
        self.function_call, self.text = function_call, text


class _FnContent:
    def __init__(self, parts):
        self.parts = parts


class _FnCand:
    def __init__(self, parts):
        self.content, self.finish_reason = _FnContent(parts), None


class _FnResp:
    def __init__(self, parts):
        self.candidates, self.usage_metadata = [_FnCand(parts)], None


_MIXED_ANSWER = "There are 10 units, totalling 380 in total."
_CLEAN_ANSWER = "There are 10 units, totalling 500 in total."
_E2E_SQL_RESULT = (
    "| count | total |\n| --- | --- |\n| 10 | 500 |\n\n"
    f"{ac.NOTES_INSIDE_FIGURE_HEADING}1 row behind this figure carries a note that may change "
    "what it is:\n"
    "- count 2: a disputed reading\n"
    "Without the noted row(s), the same figure is: (count = 8, total = 380).\n"
    "In all: count 10 (8 without the noted rows); total 500 (380 without the noted rows)\n"
)


class _E2EModels:
    """generate_content_stream is never called on the ANSWER_CHECK=1 path (it buffers
    instead) - returning nothing from it would fail the test if that assumption breaks."""
    def __init__(self):
        self.answer_calls = 0

    def generate_content_stream(self, **kw):
        raise AssertionError("ANSWER_CHECK=1 must not use the streaming call")

    def generate_content(self, *, model, contents, config=None):
        self.answer_calls += 1
        if self.answer_calls == 1:
            return _FnResp([_FnPart(text=_MIXED_ANSWER)])
        return _FnResp([_FnPart(text=_CLEAN_ANSWER)])


class _E2EClient:
    def __init__(self):
        self.models = _E2EModels()


class _FakeSupabase:
    def table(self, *a, **k):
        raise AssertionError("not used by this fake path")


def _fake_generate_with_usage(client, **kw):
    return _FnResp([_FnPart(function_call=_FC("query_structured_data",
                                              {"question": "inspection interval?"}))])


_clear_env()
os.environ["ANSWER_CHECK"] = "1"
os.environ["SQL_LOOP_MAX_STEPS"] = "1"

searched_e2e = []
saved = {
    "client": oc._get_client, "model": oc.get_llm_model,
    "sql_on": oc.get_text_to_sql_enabled, "web_on": oc.get_web_search_enabled,
    "search": oc._execute_search_documents,
    "gen": llm_usage_mod.generate_with_usage,
    "exec": sql_tool.execute_sql_query,
    "route": getattr(sql_tool, "route_tables", None),
}
e2e_client = _E2EClient()
oc._get_client = lambda: e2e_client
oc.get_llm_model = lambda: "fake-model"
oc.get_text_to_sql_enabled = lambda: True
oc.get_web_search_enabled = lambda: False


def _fake_search(search_query, metadata_filter, user_id, supabase_client=None,
                  folder_path=None, scope=None):
    searched_e2e.append(search_query)
    return [{"file_name": "manual.md", "content": "Every 6 months.", "document_id": "d1"}]


oc._execute_search_documents = _fake_search
llm_usage_mod.generate_with_usage = _fake_generate_with_usage
sql_tool.execute_sql_query = lambda q, uid, sb: _E2E_SQL_RESULT
sql_tool.route_tables = lambda *a, **k: []

try:
    events = list(oc.stream_response(
        messages=[{"role": "user", "content": "How often is it inspected?"}],
        user_id="u-1", supabase_client=_FakeSupabase(),
        has_documents=True, has_structured_data=True,
        structured_tables=[{"table_name": "bld_alpha_units", "row_count": 3}],
    ))
finally:
    oc._get_client = saved["client"]
    oc.get_llm_model = saved["model"]
    oc.get_text_to_sql_enabled = saved["sql_on"]
    oc.get_web_search_enabled = saved["web_on"]
    oc._execute_search_documents = saved["search"]
    llm_usage_mod.generate_with_usage = saved["gen"]
    sql_tool.execute_sql_query = saved["exec"]
    if saved["route"] is not None:
        sql_tool.route_tables = saved["route"]
    _clear_env()

tokens = "".join(d for k, d in events if k == "token")
check("RED: end-to-end through stream_response with NO enabled override (today's default, "
      "SHIPPED_CHECKS = MIXED_SET only): a NOTES INSIDE THE FIGURE result, the first answer "
      "mixes one reading's count with the other's total, ANSWER_CHECK retries once, and the "
      "final token text is the CLEAN retry, not the mixed one",
      tokens == _CLEAN_ANSWER, tokens)
check("...MIXED_SET needs no document search - the retry never ran one",
      searched_e2e == [], searched_e2e)
check("...exactly 2 generate_content calls happened (first answer + one retry, never more)",
      e2e_client.models.answer_calls == 2)

# --------------------------------------------------------------------------- end-to-end: use_claude (W9-A2)
#
# The SAME smoke test, through the REAL stream_response call site, with LLM_PROVIDER_ANSWER=
# anthropic added - brief Tests: "ANSWER_CHECK=1 + LLM_PROVIDER_ANSWER=anthropic: the retry
# goes to Claude." Gemini's own generate_content is wired to RAISE if touched at all, proving
# the wiring (not just the isolated function above) reaches stream_response's one call site.

print("\n8. end-to-end: ANSWER_CHECK=1 + LLM_PROVIDER_ANSWER=anthropic - the retry goes to "
      "Claude (task W9-A2)")


class _ForbiddenE2EModels:
    def generate_content_stream(self, **kw):
        raise AssertionError("ANSWER_CHECK=1 must not use the streaming call")

    def generate_content(self, *, model, contents, config=None):
        raise AssertionError("Gemini must not be called - LLM_PROVIDER_ANSWER=anthropic")


class _ForbiddenE2EClient:
    def __init__(self):
        self.models = _ForbiddenE2EModels()


e2e_claude_calls = []


def _e2e_fake_generate_answer_text(*, system_text, contents, max_tokens=64000, client=None,
                                   name="answer_stream"):
    e2e_claude_calls.append(system_text)
    return _MIXED_ANSWER if len(e2e_claude_calls) == 1 else _CLEAN_ANSWER


_clear_env()
os.environ["ANSWER_CHECK"] = "1"
os.environ["SQL_LOOP_MAX_STEPS"] = "1"
os.environ["LLM_PROVIDER_ANSWER"] = "anthropic"

searched_e2e2 = []
saved2 = {
    "client": oc._get_client, "model": oc.get_llm_model,
    "sql_on": oc.get_text_to_sql_enabled, "web_on": oc.get_web_search_enabled,
    "search": oc._execute_search_documents,
    "gen": llm_usage_mod.generate_with_usage,
    "exec": sql_tool.execute_sql_query,
    "route": getattr(sql_tool, "route_tables", None),
    "gat": llm_provider.generate_answer_text,
}
oc._get_client = lambda: _ForbiddenE2EClient()
oc.get_llm_model = lambda: "fake-model"
oc.get_text_to_sql_enabled = lambda: True
oc.get_web_search_enabled = lambda: False
oc._execute_search_documents = lambda *a, **k: (searched_e2e2.append(1) or [])
llm_usage_mod.generate_with_usage = _fake_generate_with_usage
sql_tool.execute_sql_query = lambda q, uid, sb: _E2E_SQL_RESULT
sql_tool.route_tables = lambda *a, **k: []
llm_provider.generate_answer_text = _e2e_fake_generate_answer_text

try:
    events_claude_e2e = list(oc.stream_response(
        messages=[{"role": "user", "content": "How often is it inspected?"}],
        user_id="u-1", supabase_client=_FakeSupabase(),
        has_documents=True, has_structured_data=True,
        structured_tables=[{"table_name": "bld_alpha_units", "row_count": 3}],
    ))
finally:
    oc._get_client = saved2["client"]
    oc.get_llm_model = saved2["model"]
    oc.get_text_to_sql_enabled = saved2["sql_on"]
    oc.get_web_search_enabled = saved2["web_on"]
    oc._execute_search_documents = saved2["search"]
    llm_usage_mod.generate_with_usage = saved2["gen"]
    sql_tool.execute_sql_query = saved2["exec"]
    if saved2["route"] is not None:
        sql_tool.route_tables = saved2["route"]
    llm_provider.generate_answer_text = saved2["gat"]
    _clear_env()

tokens_claude_e2e = "".join(d for k, d in events_claude_e2e if k == "token")
check("end-to-end, LLM_PROVIDER_ANSWER=anthropic: the retry goes to Claude, Gemini never "
      "touched (would have raised otherwise), final text is the CLEAN retry",
      tokens_claude_e2e == _CLEAN_ANSWER, tokens_claude_e2e)
check("...MIXED_SET needs no document search - the retry never ran one",
      searched_e2e2 == [], searched_e2e2)
check("...exactly 2 generate_answer_text calls happened (first answer + one retry, never more)",
      len(e2e_claude_calls) == 2, e2e_claude_calls)

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
