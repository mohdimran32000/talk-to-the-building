"""test_second_look_wiring.py - the agentic second look wired into `stream_response`,
OFF by default, byte-identical to `8c45f92` when unset (spec 2026-09-30, task W10-A1).

Every fixture here is INVENTED - no table, column, value, card id or question text of the
project (this is a public repo): a generic "devices in a room, still under warranty"
question, answered first from a structured-data table that never looked at warranty at
all, then re-answered once a follow-up looks for it.

Everything that can reach the network is faked: the Gemini/Claude clients, the tool-choice
call, `execute_sql_query`, `route_tables`, `_execute_search_documents`. No server, no real
model, no real tool.

Run:
    venv/Scripts/python -X utf8 tests/test_second_look_wiring.py
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


ENV_KEYS = ("AGENTIC_SECOND_LOOK", "ANSWER_CHECK", "SQL_LOOP_MAX_STEPS", "LLM_PROVIDER_ANSWER")


def _clear_env():
    for k in ENV_KEYS:
        os.environ.pop(k, None)


_clear_env()

from app.services import settings as settings_mod  # noqa: E402
from app.services import sql_tool  # noqa: E402
from app.services import openai_client as oc  # noqa: E402
from app.services import llm_usage as llm_usage_mod  # noqa: E402
from app.services import llm_provider  # noqa: E402
from app.services import second_look as sl  # noqa: E402

# =============================================================================
# shared fakes
# =============================================================================


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


class _Chunk:
    def __init__(self, text):
        self.text = text


class _BufferedModels:
    """Scripts a sequence of BUFFERED answers, one per `generate_content` call, in order -
    what the second look's own generations use regardless of provider setting (Gemini
    branch). `generate_content_stream` is wired to raise: the second look never streams,
    it buffers (task brief requirement 1)."""
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []
        # The follow-up-writer call puts its DATA (question/answer/sentences) in `contents`,
        # not `system_instruction` (which carries the fixed FOLLOWUP_SYSTEM_PROMPT for that
        # one call) - recorded here, as the plain text of each call's first content part, so
        # a test can inspect either field regardless of which call it is.
        self.contents_text = []

    def generate_content_stream(self, *, model, contents, config=None):
        raise AssertionError("the second look must never use the streaming call")

    def generate_content(self, *, model, contents, config=None):
        if not self.answers:
            raise AssertionError("generate_content called with no scripted answer left "
                                  "(an extra, unexpected generation happened)")
        self.calls.append(config.system_instruction if config else None)
        first = contents[0] if contents else None
        parts = getattr(first, "parts", None) or []
        self.contents_text.append("".join(getattr(p, "text", "") or "" for p in parts))
        return _FnResp([_FnPart(text=self.answers.pop(0))])


class _ReanswerRaises(_BufferedModels):
    """Like `_BufferedModels`, but the call AFTER its scripted answers run out raises a
    plain RuntimeError instead of asserting - proving the RE-ANSWER's own generation
    failing (not just a follow-up TOOL failing) also falls back to the first answer."""
    def generate_content(self, *, model, contents, config=None):
        if not self.answers:
            raise RuntimeError("boom: re-answer generation outage")
        return super().generate_content(model=model, contents=contents, config=config)


class _FollowupWriterRaises(_BufferedModels):
    """Like `_BufferedModels`, but the SECOND call - the follow-up-WRITER call itself,
    `_write_followup_question`'s own generation - raises instead of returning a scripted
    answer. Distinct from `_ReanswerRaises` (which fails the THIRD call): this proves the
    follow-up-writer call failing, as opposed to it succeeding and returning NONE or a
    downstream tool/re-answer call raising, also falls back to the first answer - with no
    tool ever attempted and no re-answer ever attempted (only one scripted answer, the
    first pass, needs to be supplied; a second one would never be consumed)."""
    def generate_content(self, *, model, contents, config=None):
        if len(self.calls) == 1:
            raise RuntimeError("boom: follow-up-writer outage")
        return super().generate_content(model=model, contents=contents, config=config)


class _StreamOnlyModels:
    """TODAY's shape (switch unset): only the plain streaming call is ever used.
    `generate_content` is wired to raise, proving the second look's buffered path is
    never reached when AGENTIC_SECOND_LOOK is unset."""
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.stream_calls = 0

    def generate_content(self, *, model, contents, config=None):
        raise AssertionError("switch unset: the buffered/second-look path must not run")

    def generate_content_stream(self, *, model, contents, config=None):
        self.stream_calls += 1
        return [_Chunk(t) for t in self.chunks]


class _RaisingModels:
    """Gemini wired to raise on EITHER call shape - used for the Claude-provider tests,
    proving Gemini is never touched at all."""
    def generate_content(self, *, model, contents, config=None):
        raise AssertionError("Gemini must not be called - LLM_PROVIDER_ANSWER=anthropic")

    def generate_content_stream(self, *, model, contents, config=None):
        raise AssertionError("Gemini must not be called - LLM_PROVIDER_ANSWER=anthropic")


class _Client:
    def __init__(self, models):
        self.models = models


class _FakeSupabase:
    def table(self, *a, **k):
        raise AssertionError("not used by this fake path - every tool is faked directly")


def _scripted_claude(answers):
    """A fake `generate_answer_text` - returns each scripted answer in order, records every
    system_text it was given (mirrors `_BufferedModels`, Claude-shaped)."""
    remaining = list(answers)
    calls = []

    def _fn(*, system_text, contents, max_tokens=64000, client=None, name="answer_stream"):
        if not remaining:
            raise AssertionError("generate_answer_text called with no scripted answer left")
        calls.append(system_text)
        return remaining.pop(0)

    return _fn, calls


def _run(user_msg, *, models, exec_fn, search_fn, route_fn=None,
         generate_answer_text_fn=None, env=None, has_documents=True,
         has_structured_data=True, tool_choice_args=None):
    """Drive stream_response with every external edge faked. Returns the raw list of
    (event_type, event_data) tuples."""
    saved = {
        "client": oc._get_client, "model": oc.get_llm_model,
        "sql_on": oc.get_text_to_sql_enabled, "web_on": oc.get_web_search_enabled,
        "meta": oc.get_metadata_schema, "search": oc._execute_search_documents,
        "gen": llm_usage_mod.generate_with_usage, "exec": sql_tool.execute_sql_query,
        "route": getattr(sql_tool, "route_tables", None),
        "gat": llm_provider.generate_answer_text,
    }
    oc._get_client = lambda: _Client(models)
    oc.get_llm_model = lambda: "fake-model"
    oc.get_text_to_sql_enabled = lambda: True
    oc.get_web_search_enabled = lambda: False
    oc.get_metadata_schema = lambda: settings_mod.DEFAULT_METADATA_SCHEMA
    oc._execute_search_documents = search_fn
    llm_usage_mod.generate_with_usage = lambda client, **kw: _FnResp([_FnPart(
        function_call=_FC("query_structured_data", tool_choice_args or {"question": user_msg}))])
    sql_tool.execute_sql_query = exec_fn
    if route_fn is not None:
        sql_tool.route_tables = route_fn
    else:
        sql_tool.route_tables = lambda *a, **k: []
    if generate_answer_text_fn is not None:
        llm_provider.generate_answer_text = generate_answer_text_fn

    _clear_env()
    for k, v in (env or {}).items():
        os.environ[k] = v
    try:
        events = list(oc.stream_response(
            messages=[{"role": "user", "content": user_msg}],
            user_id="u-1", supabase_client=_FakeSupabase(),
            has_documents=has_documents, has_structured_data=has_structured_data,
            structured_tables=[{"table_name": "bld_devices", "row_count": 2}],
        ))
    finally:
        oc._get_client = saved["client"]
        oc.get_llm_model = saved["model"]
        oc.get_text_to_sql_enabled = saved["sql_on"]
        oc.get_web_search_enabled = saved["web_on"]
        oc.get_metadata_schema = saved["meta"]
        oc._execute_search_documents = saved["search"]
        llm_usage_mod.generate_with_usage = saved["gen"]
        sql_tool.execute_sql_query = saved["exec"]
        if saved["route"] is not None:
            sql_tool.route_tables = saved["route"]
        llm_provider.generate_answer_text = saved["gat"]
        _clear_env()
    return events


def _tokens(events):
    return "".join(d for k, d in events if k == "token")


def _tool_events(events):
    return [(k, json.loads(v)) for k, v in events if k in ("tool_start", "tool_done")]


# =============================================================================
# generic fixtures
# =============================================================================

QUESTION = "What devices are installed in this room, and are they still under warranty?"
FIRST_SQL_RESULT = ("| device | install_date |\n| --- | --- |\n| Unit A | 2018-07-22 |\n"
                     "| Unit B | 2018-07-22 |\n\nSQL: `SELECT device, install_date "
                     "FROM \"bld_devices\"`")
REFUSAL_ANSWER = ("The devices are Unit A and Unit B. The records do not state their "
                   "warranty period.")
FOLLOWUP_QUESTION = "Are Unit A and Unit B still under warranty?"
FOLLOWUP_SQL_RESULT = "Unit A and Unit B: warranty 5 years from 2018-07-22."
FOLLOWUP_CHUNKS = [{"file_name": "warranty.md",
                     "content": "Unit A and Unit B carry a 5-year warranty from purchase."}]
CLEAN_REANSWER = ("Unit A and Unit B are covered by a 5-year warranty from 2018-07-22, "
                   "which has since expired.")
CLEAN_ANSWER_NO_REFUSAL = "Unit A and Unit B are installed in this room."
STILL_REFUSING_REANSWER = "The records still do not state the warranty period."

BASE_ENV = {"SQL_LOOP_MAX_STEPS": "1"}


def _env(**extra):
    out = dict(BASE_ENV)
    out.update(extra)
    return out


# =============================================================================
# 1. switch unset -> byte-identical
# =============================================================================
print("1. AGENTIC_SECOND_LOOK unset -> byte-identical to today (streaming, unbuffered)")

check("the setting defaults to False", settings_mod.get_agentic_second_look_enabled() is False)

models1 = _StreamOnlyModels(["Unit A and Unit B are installed in this room."])
sql_calls1 = []


def exec1(question, user_id, sb):
    sql_calls1.append(question)
    return FIRST_SQL_RESULT


search_calls1 = []


def search1(search_query, metadata_filter, user_id, supabase_client=None, folder_path=None,
            scope=None):
    search_calls1.append(search_query)
    return []


events1 = _run(QUESTION, models=models1, exec_fn=exec1, search_fn=search1, env=_env())
check("RED: with the switch unset, the answer streams via generate_content_stream "
      "(today's call shape) - the buffered path never ran (it would have raised)",
      _tokens(events1) == "Unit A and Unit B are installed in this room.", _tokens(events1))
check("...exactly one SQL call, no follow-up anything",
      sql_calls1 == [QUESTION], sql_calls1)
check("...no document search at all", search_calls1 == [], search_calls1)
check("...exactly one streaming call", models1.stream_calls == 1)

# =============================================================================
# 2. a refusal triggers the full second look (Gemini)
# =============================================================================
print("\n2. a refusal sentence -> one follow-up call, both tools on the follow-up text, "
      "one re-answer")

models2 = _BufferedModels([REFUSAL_ANSWER, FOLLOWUP_QUESTION, CLEAN_REANSWER])
sql_calls2 = []


def exec2(question, user_id, sb):
    sql_calls2.append(question)
    return FIRST_SQL_RESULT if len(sql_calls2) == 1 else FOLLOWUP_SQL_RESULT


search_calls2 = []


def search2(search_query, metadata_filter, user_id, supabase_client=None, folder_path=None,
            scope=None):
    search_calls2.append(search_query)
    return list(FOLLOWUP_CHUNKS)


route_calls2 = []


def route2(question, user_id, supabase_client, live_table_names=None):
    route_calls2.append(question)
    return []


events2 = _run(QUESTION, models=models2, exec_fn=exec2, search_fn=search2, route_fn=route2,
               env=_env(AGENTIC_SECOND_LOOK="1"))
tokens2 = _tokens(events2)
tools2 = _tool_events(events2)

check("RED: the streamed text is the RE-ANSWER, not the refusal",
      tokens2 == CLEAN_REANSWER, tokens2)
check("exactly 3 generate_content calls: first pass, ONE follow-up call, one re-answer",
      len(models2.calls) == 3, len(models2.calls))
check("both SQL calls happened - the original question, then the follow-up text",
      sql_calls2 == [QUESTION, FOLLOWUP_QUESTION], sql_calls2)
check("the document search ran on the FOLLOW-UP text",
      search_calls2 == [FOLLOWUP_QUESTION], search_calls2)
check("the structured-data path routed AGAIN for the follow-up text specifically "
      "(its OWN routing, not the original question's)",
      route_calls2 == [QUESTION, FOLLOWUP_QUESTION], route_calls2)
check("a tool_start event was emitted for EACH follow-up tool, exactly as the normal path "
      "shapes them",
      tools2 == [
          ("tool_start", {"tool": "query_structured_data", "args": {"question": QUESTION}}),
          ("tool_done", {"tool": "query_structured_data", "detail": "Query executed"}),
          ("tool_start", {"tool": "query_structured_data",
                           "args": {"question": FOLLOWUP_QUESTION}}),
          ("tool_done", {"tool": "query_structured_data", "detail": "Second look complete"}),
          ("tool_start", {"tool": "search_documents", "args": {"query": FOLLOWUP_QUESTION}}),
          ("tool_done", {"tool": "search_documents", "detail": "Found 1 results"}),
      ], tools2)
check("the re-answer's own input (system prompt) holds the follow-up's SQL result",
      FOLLOWUP_SQL_RESULT in models2.calls[2], models2.calls[2])
check("...and the follow-up's document result",
      "Unit A and Unit B carry a 5-year warranty" in models2.calls[2])
check("...and the original tool result is still there too (PLUS, not instead of)",
      "Unit A" in models2.calls[2] and "2018-07-22" in models2.calls[2])
check("...and the one instruction sentence naming what the second look is for",
      sl.SECOND_LOOK_INSTRUCTION in models2.calls[2])
check("the follow-up-writer's own call used the fixed FOLLOWUP_SYSTEM_PROMPT as its system "
      "text (the per-call data goes in `contents`, not here)",
      models2.calls[1] == sl.FOLLOWUP_SYSTEM_PROMPT, models2.calls[1])
check("...and its `contents` carried the question, the first answer and its refusal "
      "sentence (the actual per-call data)",
      QUESTION in models2.contents_text[1] and REFUSAL_ANSWER in models2.contents_text[1]
      and "warranty period" in models2.contents_text[1], models2.contents_text[1])

# =============================================================================
# 3. no refusal sentence -> no extra call at all
# =============================================================================
print("\n3. no refusal sentence -> no extra call")

models3 = _BufferedModels([CLEAN_ANSWER_NO_REFUSAL])
sql_calls3 = []


def exec3(question, user_id, sb):
    sql_calls3.append(question)
    return FIRST_SQL_RESULT


search_calls3 = []


def search3(search_query, metadata_filter, user_id, supabase_client=None, folder_path=None,
            scope=None):
    search_calls3.append(search_query)
    return []


events3 = _run(QUESTION, models=models3, exec_fn=exec3, search_fn=search3,
               env=_env(AGENTIC_SECOND_LOOK="1"))
check("RED: the clean first answer streams unchanged",
      _tokens(events3) == CLEAN_ANSWER_NO_REFUSAL, _tokens(events3))
check("exactly ONE generate_content call - no follow-up call, no re-answer",
      len(models3.calls) == 1, len(models3.calls))
check("no second SQL call", sql_calls3 == [QUESTION], sql_calls3)
check("no document search at all", search_calls3 == [], search_calls3)

# =============================================================================
# 4. the follow-up call answers NONE -> no tools run, the first answer streams
# =============================================================================
print("\n4. follow-up call answers NONE -> no tools run, the first (refusing) answer streams")

models4 = _BufferedModels([REFUSAL_ANSWER, "NONE"])
sql_calls4 = []


def exec4(question, user_id, sb):
    sql_calls4.append(question)
    return FIRST_SQL_RESULT


search_calls4 = []


def search4(search_query, metadata_filter, user_id, supabase_client=None, folder_path=None,
            scope=None):
    search_calls4.append(search_query)
    return list(FOLLOWUP_CHUNKS)


events4 = _run(QUESTION, models=models4, exec_fn=exec4, search_fn=search4,
               env=_env(AGENTIC_SECOND_LOOK="1"))
check("RED: NONE -> the first (refusing) answer is what streams",
      _tokens(events4) == REFUSAL_ANSWER, _tokens(events4))
check("exactly 2 generate_content calls: first pass + the one follow-up call, no re-answer",
      len(models4.calls) == 2, len(models4.calls))
check("no second SQL call - NONE means no tools run at all",
      sql_calls4 == [QUESTION], sql_calls4)
check("no document search - NONE means no tools run at all",
      search_calls4 == [], search_calls4)
check("no tool_start/tool_done beyond the original pass's own",
      _tool_events(events4) == [
          ("tool_start", {"tool": "query_structured_data", "args": {"question": QUESTION}}),
          ("tool_done", {"tool": "query_structured_data", "detail": "Query executed"}),
      ], _tool_events(events4))

# =============================================================================
# 5. a second look never triggers a third
# =============================================================================
print("\n5. a second look never triggers a third, even if the re-answer ALSO refuses")

models5 = _BufferedModels([REFUSAL_ANSWER, FOLLOWUP_QUESTION, STILL_REFUSING_REANSWER])
sql_calls5 = []


def exec5(question, user_id, sb):
    sql_calls5.append(question)
    return FIRST_SQL_RESULT if len(sql_calls5) == 1 else FOLLOWUP_SQL_RESULT


search_calls5 = []


def search5(search_query, metadata_filter, user_id, supabase_client=None, folder_path=None,
            scope=None):
    search_calls5.append(search_query)
    return list(FOLLOWUP_CHUNKS)


events5 = _run(QUESTION, models=models5, exec_fn=exec5, search_fn=search5,
               env=_env(AGENTIC_SECOND_LOOK="1"))
check("RED: the re-answer is kept EVEN THOUGH it still reads as a refusal - never a third "
      "look (if a third call had been attempted, the fake would have raised)",
      _tokens(events5) == STILL_REFUSING_REANSWER, _tokens(events5))
check("exactly 3 generate_content calls total, never a 4th",
      len(models5.calls) == 3, len(models5.calls))
check("both follow-up tools still ran exactly once each",
      sql_calls5 == [QUESTION, FOLLOWUP_QUESTION]
      and search_calls5 == [FOLLOWUP_QUESTION], (sql_calls5, search_calls5))

# =============================================================================
# 6. works with the answer role on Claude
# =============================================================================
print("\n6. the answer role on Claude (LLM_PROVIDER_ANSWER=anthropic) - Gemini never touched")

models6 = _RaisingModels()
fake_claude6, claude_calls6 = _scripted_claude([REFUSAL_ANSWER, FOLLOWUP_QUESTION,
                                                CLEAN_REANSWER])
sql_calls6 = []


def exec6(question, user_id, sb):
    sql_calls6.append(question)
    return FIRST_SQL_RESULT if len(sql_calls6) == 1 else FOLLOWUP_SQL_RESULT


search_calls6 = []


def search6(search_query, metadata_filter, user_id, supabase_client=None, folder_path=None,
            scope=None):
    search_calls6.append(search_query)
    return list(FOLLOWUP_CHUNKS)


events6 = _run(QUESTION, models=models6, exec_fn=exec6, search_fn=search6,
               generate_answer_text_fn=fake_claude6,
               env=_env(AGENTIC_SECOND_LOOK="1", LLM_PROVIDER_ANSWER="anthropic"))
check("RED: Claude carries the whole second look through to the re-answer, Gemini never "
      "touched (would have raised otherwise)",
      _tokens(events6) == CLEAN_REANSWER, _tokens(events6))
check("exactly 3 generate_answer_text calls (Claude): first pass, follow-up, re-answer",
      len(claude_calls6) == 3, len(claude_calls6))
check("both follow-up tools ran on the follow-up text (provider-independent)",
      sql_calls6 == [QUESTION, FOLLOWUP_QUESTION]
      and search_calls6 == [FOLLOWUP_QUESTION], (sql_calls6, search_calls6))
check("the re-answer's Claude system text holds the follow-up results and the instruction",
      FOLLOWUP_SQL_RESULT in claude_calls6[2] and sl.SECOND_LOOK_INSTRUCTION in claude_calls6[2])

# =============================================================================
# 7. a follow-up SQL error -> fails safe, the first answer streams
# =============================================================================
print("\n7. a follow-up structured-data error -> the first answer streams (fails safe)")

models7 = _BufferedModels([REFUSAL_ANSWER, FOLLOWUP_QUESTION])
sql_calls7 = []


def exec7(question, user_id, sb):
    sql_calls7.append(question)
    if len(sql_calls7) == 1:
        return FIRST_SQL_RESULT
    raise RuntimeError("boom: structured-data outage")


search_calls7 = []


def search7(search_query, metadata_filter, user_id, supabase_client=None, folder_path=None,
            scope=None):
    search_calls7.append(search_query)
    return list(FOLLOWUP_CHUNKS)


events7 = _run(QUESTION, models=models7, exec_fn=exec7, search_fn=search7,
               env=_env(AGENTIC_SECOND_LOOK="1"))
check("RED: a follow-up SQL error falls back to the first (refusing) answer, never raises "
      "out of the turn",
      _tokens(events7) == REFUSAL_ANSWER, _tokens(events7))
check("no re-answer call was attempted (only first pass + the one follow-up call)",
      len(models7.calls) == 2, len(models7.calls))
check("the document search was never reached - the SQL error aborted the whole second look "
      "before it got there",
      search_calls7 == [], search_calls7)

# =============================================================================
# 8. a follow-up document-search error -> fails safe too (symmetry)
# =============================================================================
print("\n8. a follow-up document-search error -> the first answer streams (fails safe)")

models8 = _BufferedModels([REFUSAL_ANSWER, FOLLOWUP_QUESTION])
sql_calls8 = []


def exec8(question, user_id, sb):
    sql_calls8.append(question)
    return FIRST_SQL_RESULT if len(sql_calls8) == 1 else FOLLOWUP_SQL_RESULT


search_calls8 = []


def search8(search_query, metadata_filter, user_id, supabase_client=None, folder_path=None,
            scope=None):
    search_calls8.append(search_query)
    raise RuntimeError("boom: document search outage")


events8 = _run(QUESTION, models=models8, exec_fn=exec8, search_fn=search8,
               env=_env(AGENTIC_SECOND_LOOK="1"))
check("RED: a follow-up document-search error ALSO falls back to the first answer",
      _tokens(events8) == REFUSAL_ANSWER, _tokens(events8))
check("no re-answer call was attempted",
      len(models8.calls) == 2, len(models8.calls))
check("the structured-data follow-up DID succeed before the search failed - a partial "
      "success still fails the whole second look safe, never a half-finished re-answer",
      sql_calls8 == [QUESTION, FOLLOWUP_QUESTION], sql_calls8)
check("the document search was attempted (and recorded) before it raised",
      search_calls8 == [FOLLOWUP_QUESTION], search_calls8)

# =============================================================================
# 9. ANSWER_CHECK=1 together with AGENTIC_SECOND_LOOK=1: the checker still applies
#    to whichever generation ends up final (requirement 4)
# =============================================================================
print("\n9. ANSWER_CHECK=1 + AGENTIC_SECOND_LOOK=1: the checker still gates the final answer")

check("MIXED_SET is the only shipped check today (regression guard for this section's "
      "premise)", __import__("app.services.answer_check", fromlist=["SHIPPED_CHECKS"])
      .SHIPPED_CHECKS == frozenset({__import__(
          "app.services.answer_check", fromlist=["MIXED_SET"]).MIXED_SET}))

# A clean answer with no refusal and no MIXED_SET shape: ANSWER_CHECK finds nothing to fix,
# second look finds no refusal sentence either - one call total, same as section 3, proving
# the two features compose without adding calls when neither has anything to do.
models9 = _BufferedModels([CLEAN_ANSWER_NO_REFUSAL])
sql_calls9 = []


def exec9(question, user_id, sb):
    sql_calls9.append(question)
    return FIRST_SQL_RESULT


search_calls9 = []


def search9(search_query, metadata_filter, user_id, supabase_client=None, folder_path=None,
            scope=None):
    search_calls9.append(search_query)
    return []


events9 = _run(QUESTION, models=models9, exec_fn=exec9, search_fn=search9,
               env=_env(AGENTIC_SECOND_LOOK="1", ANSWER_CHECK="1"))
check("both switches on, nothing to fix: exactly one generate_content call",
      len(models9.calls) == 1 and _tokens(events9) == CLEAN_ANSWER_NO_REFUSAL,
      (len(models9.calls), _tokens(events9)))

# =============================================================================================
# 10. the re-answer generation itself failing ALSO fails safe (not just a follow-up tool)
# =============================================================================================
print("\n10. the re-answer's OWN generation failing -> the first answer streams (fails safe)")

models10 = _ReanswerRaises([REFUSAL_ANSWER, FOLLOWUP_QUESTION])
sql_calls10 = []


def exec10(question, user_id, sb):
    sql_calls10.append(question)
    return FIRST_SQL_RESULT if len(sql_calls10) == 1 else FOLLOWUP_SQL_RESULT


search_calls10 = []


def search10(search_query, metadata_filter, user_id, supabase_client=None, folder_path=None,
             scope=None):
    search_calls10.append(search_query)
    return list(FOLLOWUP_CHUNKS)


events10 = _run(QUESTION, models=models10, exec_fn=exec10, search_fn=search10,
               env=_env(AGENTIC_SECOND_LOOK="1"))
check("RED: the re-answer generation itself raising ALSO falls back to the first answer, "
      "never raises out of the turn",
      _tokens(events10) == REFUSAL_ANSWER, _tokens(events10))
check("both follow-up tools HAD already run successfully before the re-answer call failed "
      "(their work was not wasted, it is simply not shown because the re-answer never "
      "finished)",
      sql_calls10 == [QUESTION, FOLLOWUP_QUESTION] and search_calls10 == [FOLLOWUP_QUESTION],
      (sql_calls10, search_calls10))

# =============================================================================================
# 11. the follow-up-WRITER call itself failing (distinct from it succeeding with NONE, or a
#     downstream tool/re-answer raising) ALSO fails safe: no tool runs, no re-answer attempted
# =============================================================================================
print("\n11. the follow-up-writer call itself raising -> the first answer streams, no tool "
      "runs, no re-answer")

models11 = _FollowupWriterRaises([REFUSAL_ANSWER])
sql_calls11 = []


def exec11(question, user_id, sb):
    sql_calls11.append(question)
    return FIRST_SQL_RESULT


search_calls11 = []


def search11(search_query, metadata_filter, user_id, supabase_client=None, folder_path=None,
             scope=None):
    search_calls11.append(search_query)
    return list(FOLLOWUP_CHUNKS)


events11 = _run(QUESTION, models=models11, exec_fn=exec11, search_fn=search11,
               env=_env(AGENTIC_SECOND_LOOK="1"))
check("RED: the follow-up-writer call itself raising falls back to the first (refusing) "
      "answer, never raises out of the turn",
      _tokens(events11) == REFUSAL_ANSWER, _tokens(events11))
check("only the first pass's own generate_content call happened - the follow-up-writer "
      "attempt raised before producing anything, no re-answer was attempted",
      len(models11.calls) == 1, len(models11.calls))
check("no SQL follow-up call - only the original pass's own call",
      sql_calls11 == [QUESTION], sql_calls11)
check("no document search at all - the follow-up-writer call never got as far as producing "
      "a follow-up question for either tool to run on",
      search_calls11 == [], search_calls11)

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
