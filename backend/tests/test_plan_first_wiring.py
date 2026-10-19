"""test_plan_first_wiring.py - the plan-first multi-part step wired into `stream_response`,
OFF by default, byte-identical to `44533a1` when unset (spec 2026-09-30, task W11-A1).

Every fixture here is INVENTED - no table, column, value, card id or question text of the
project (this is a public repo): a generic "assets in a room, plus the manual's maintenance
for each" question, split into parts, each answered through the app's own faked tools.

Everything that can reach the network is faked: the Gemini/Claude clients, the tool-choice
call, `execute_sql_query`, `route_tables`, `_execute_search_documents`. No server, no real
model, no real tool.

Run:
    venv/Scripts/python -X utf8 tests/test_plan_first_wiring.py
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


ENV_KEYS = ("PLAN_FIRST", "AGENTIC_SECOND_LOOK", "ANSWER_CHECK", "SQL_LOOP_MAX_STEPS",
            "LLM_PROVIDER_ANSWER")


def _clear_env():
    for k in ENV_KEYS:
        os.environ.pop(k, None)


_clear_env()

from app.services import settings as settings_mod  # noqa: E402
from app.services import sql_tool  # noqa: E402
from app.services import sql_loop as sql_loop_mod  # noqa: E402
from app.services import openai_client as oc  # noqa: E402
from app.services import llm_usage as llm_usage_mod  # noqa: E402
from app.services import llm_provider  # noqa: E402
from app.services import plan_first as pf  # noqa: E402

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


class _Models:
    """Combined fake. `generate_content` (buffered) returns each of `buffered` in order -
    what PLAN/COMPOSE/CHECK all use regardless of provider setting (Gemini branch).
    `generate_content_stream` returns `stream_chunks` once, used ONLY by the pre-existing
    NORMAL path's own final streaming answer - reached when PLAN_FIRST decides there is
    nothing for it to do (one part, malformed JSON) or falls back after an error. Either
    list may be omitted, in which case that method raises if ever reached - most scenarios
    below only ever exercise one of the two."""
    def __init__(self, buffered=None, stream_chunks=None):
        self.buffered = list(buffered or [])
        self.stream_chunks = stream_chunks
        self.calls = []            # system_instruction of every generate_content call, in order
        self.contents_text = []    # the plain text of each call's first content part
        self.stream_calls = 0

    def generate_content(self, *, model, contents, config=None):
        if not self.buffered:
            raise AssertionError("generate_content called with no scripted answer left "
                                  "(an extra, unexpected generation happened)")
        self.calls.append(config.system_instruction if config else None)
        first = contents[0] if contents else None
        parts = getattr(first, "parts", None) or []
        self.contents_text.append("".join(getattr(p, "text", "") or "" for p in parts))
        return _FnResp([_FnPart(text=self.buffered.pop(0))])

    def generate_content_stream(self, *, model, contents, config=None):
        if self.stream_chunks is None:
            raise AssertionError("generate_content_stream must not be reached in this "
                                  "scenario (plan-first should have handled the turn, or "
                                  "no fallback to the normal path was expected)")
        self.stream_calls += 1
        return [_Chunk(t) for t in self.stream_chunks]


class _RaisingModels:
    """Gemini wired to raise on EITHER call shape - used for the Claude-provider test,
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
    system_text it was given (mirrors `_Models`, Claude-shaped)."""
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
         has_structured_data=True, tool_choice_reachable=True, tool_choice_args=None):
    """Drive stream_response with every external edge faked. Returns the raw list of
    (event_type, event_data) tuples. `tool_choice_reachable=False` wires the tool-choice
    turn's own call (`generate_with_usage`) to raise, proving it was never reached - used
    whenever plan-first is expected to handle the WHOLE turn itself."""
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

    def _tool_choice(client, **kw):
        if not tool_choice_reachable:
            raise AssertionError("the tool-choice turn must not be reached - plan-first "
                                  "was expected to handle the whole question itself")
        return _FnResp([_FnPart(
            function_call=_FC("query_structured_data", tool_choice_args or {"question": user_msg}))])

    llm_usage_mod.generate_with_usage = _tool_choice
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
            structured_tables=[{"table_name": "bld_assets", "row_count": 2}],
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

QUESTION_1PART = "How many widgets are in the warehouse?"
PLAN_1PART_JSON = ('{"parts": [{"question": "How many widgets are in the warehouse?", '
                    '"source": "data"}]}')

QUESTION_2PART = ("What assets are in the storage room, and what does the manual say "
                  "about the finish of each?")
PART_A = "What assets are in the storage room?"
# Deliberately NOT a frequency/maintenance question (task W12-A1's predicate reads the
# word 'maintenance' itself as a positive match - see test_table_router_maintenance.py) -
# this part exists to prove an ORDINARY documents-only part is untouched by that predicate.
PART_B = "What does the manual say about the finish of each asset?"
PLAN_2PART_JSON = (
    '{"parts": ['
    f'{{"question": "{PART_A}", "source": "data"}}, '
    f'{{"question": "{PART_B}", "source": "documents"}}'
    ']}'
)
SQL_RESULT_A = "| asset |\n| --- |\n| Unit A |\n| Unit B |"
DOC_CHUNKS_B = [{"file_name": "manual.md", "content": "Finish: brushed steel."}]
COMPOSE_2PART_ANSWER = ("The storage room has Unit A and Unit B. The manual says each "
                        "has a brushed steel finish.")
CHECK_CLEAN_JSON = '{"unanswered": []}'

NORMAL_SQL_RESULT = "| count |\n| --- |\n| 42 |"
NORMAL_STREAM_CHUNKS = ["42 widgets are in the warehouse."]

BASE_ENV = {"SQL_LOOP_MAX_STEPS": "1"}


def _env(**extra):
    out = dict(BASE_ENV)
    out.update(extra)
    return out


def _sql_fn(results_by_prefix, calls):
    def _fn(question, user_id, sb):
        calls.append(question)
        for prefix, result in results_by_prefix.items():
            if question.startswith(prefix):
                return result
        return "Query returned no results."
    return _fn


def _search_fn(chunks_by_prefix, calls):
    def _fn(search_query, metadata_filter, user_id, supabase_client=None, folder_path=None,
            scope=None):
        calls.append(search_query)
        for prefix, chunks in chunks_by_prefix.items():
            if search_query.startswith(prefix):
                return list(chunks)
        return []
    return _fn


# =============================================================================
# 1. switch unset -> byte-identical
# =============================================================================
print("1. PLAN_FIRST unset -> byte-identical to today (no planner call, normal path runs)")

check("the setting defaults to False", settings_mod.get_plan_first_enabled() is False)

models1 = _Models(stream_chunks=NORMAL_STREAM_CHUNKS)
sql_calls1 = []
events1 = _run(QUESTION_1PART, models=models1,
               exec_fn=_sql_fn({QUESTION_1PART: NORMAL_SQL_RESULT}, sql_calls1),
               search_fn=_search_fn({}, []), env=_env())
check("RED: with the switch unset, the normal streaming answer runs - no planner call, no "
      "buffered call at all (would have raised)",
      _tokens(events1) == "".join(NORMAL_STREAM_CHUNKS), _tokens(events1))
check("...exactly one SQL call, on the original question",
      sql_calls1 == [QUESTION_1PART], sql_calls1)
check("...exactly one streaming call", models1.stream_calls == 1)
check("...zero buffered calls (the planner was never even attempted)",
      models1.calls == [], models1.calls)

# =============================================================================
# 2. a one-part plan -> exactly the normal path + one planner call
# =============================================================================
print("\n2. a one-part plan -> exactly the normal path + one planner call")

models2 = _Models(buffered=[PLAN_1PART_JSON], stream_chunks=NORMAL_STREAM_CHUNKS)
sql_calls2 = []
events2 = _run(QUESTION_1PART, models=models2,
               exec_fn=_sql_fn({QUESTION_1PART: NORMAL_SQL_RESULT}, sql_calls2),
               search_fn=_search_fn({}, []), env=_env(PLAN_FIRST="1"))
check("RED: the normal streaming answer still runs, unchanged",
      _tokens(events2) == "".join(NORMAL_STREAM_CHUNKS), _tokens(events2))
check("exactly one buffered call (the planner) and one streaming call",
      len(models2.calls) == 1 and models2.stream_calls == 1,
      (len(models2.calls), models2.stream_calls))
check("the SQL call ran on the ORIGINAL question, exactly once - no part-level call at all",
      sql_calls2 == [QUESTION_1PART], sql_calls2)
check("the planner call used PLANNER_SYSTEM_PROMPT as its system text",
      models2.calls[0] == pf.PLANNER_SYSTEM_PROMPT, models2.calls[0])
check("...and its contents carried the original question",
      QUESTION_1PART in models2.contents_text[0], models2.contents_text[0])

# =============================================================================
# 3. malformed planner JSON -> the normal path + one (failed) planner call
# =============================================================================
print("\n3. malformed planner JSON -> the normal path, same as a one-part plan")

models3 = _Models(buffered=["not json at all, sorry!"], stream_chunks=NORMAL_STREAM_CHUNKS)
sql_calls3 = []
events3 = _run(QUESTION_1PART, models=models3,
               exec_fn=_sql_fn({QUESTION_1PART: NORMAL_SQL_RESULT}, sql_calls3),
               search_fn=_search_fn({}, []), env=_env(PLAN_FIRST="1"))
check("RED: malformed planner JSON falls back to the normal path, not an exception",
      _tokens(events3) == "".join(NORMAL_STREAM_CHUNKS), _tokens(events3))
check("exactly one buffered call (the failed planner attempt) + one streaming call",
      len(models3.calls) == 1 and models3.stream_calls == 1,
      (len(models3.calls), models3.stream_calls))
check("the SQL call ran on the original question only",
      sql_calls3 == [QUESTION_1PART], sql_calls3)

# =============================================================================
# 4. a two-part plan, clean CHECK -> both parts' tools run, one compose, no re-run
# =============================================================================
print("\n4. a two-part plan + a clean CHECK -> EXECUTE both parts, COMPOSE once, no re-run")

models4 = _Models(buffered=[PLAN_2PART_JSON, COMPOSE_2PART_ANSWER, CHECK_CLEAN_JSON])
sql_calls4, search_calls4 = [], []
events4 = _run(QUESTION_2PART, models=models4,
               exec_fn=_sql_fn({PART_A: SQL_RESULT_A}, sql_calls4),
               search_fn=_search_fn({PART_B: DOC_CHUNKS_B}, search_calls4),
               tool_choice_reachable=False, env=_env(PLAN_FIRST="1"))
tokens4 = _tokens(events4)
tools4 = _tool_events(events4)
check("RED: the streamed text is the COMPOSED answer",
      tokens4 == COMPOSE_2PART_ANSWER, tokens4)
check("exactly 3 buffered calls: planner, compose, check - no re-run, no 4th call",
      len(models4.calls) == 3, len(models4.calls))
check("the tool-choice turn was never reached - plan-first handled the whole question",
      True)  # tool_choice_reachable=False would have raised otherwise
check("part A's SQL call ran with part A's OWN text (source=data, no doc search for it)",
      sql_calls4 == [PART_A], sql_calls4)
check("part B's document search ran with part B's OWN text (source=documents, no SQL for it)",
      search_calls4 == [PART_B], search_calls4)
check("a tool_start/tool_done pair was emitted for EACH tool, per part, in order",
      tools4 == [
          ("tool_start", {"tool": "query_structured_data", "args": {"question": PART_A}}),
          ("tool_done", {"tool": "query_structured_data", "detail": "Plan part 1 complete"}),
          ("tool_start", {"tool": "search_documents", "args": {"query": PART_B}}),
          ("tool_done", {"tool": "search_documents", "detail": "Plan part 2: found 1 results"}),
      ], tools4)
check("the compose call's system text carries part A's own result, labelled",
      "Part 1" in models4.calls[1] and SQL_RESULT_A in models4.calls[1], models4.calls[1])
check("...and part B's own result, labelled",
      "Part 2" in models4.calls[1] and "Finish: brushed steel." in models4.calls[1],
      models4.calls[1])
check("the check call's own contents carry both parts and the composed answer",
      PART_A in models4.contents_text[2] and PART_B in models4.contents_text[2]
      and COMPOSE_2PART_ANSWER in models4.contents_text[2], models4.contents_text[2])

# =============================================================================
# 5. a three-part plan, CHECK names ONE part -> only that part re-runs, BOTH tools, broadened
# =============================================================================
print("\n5. a three-part plan, CHECK names one part -> re-run with BOTH tools, broadened text, "
     "one more compose, never a second check")

PART_X = "What assets are in the storage room?"
# Same deliberate choice as PART_B above: no 'maintenance'/'frequency'/'inspect' wording,
# so this documents-only part stays untouched by asks_about_maintenance_frequency.
PART_Y = "What does the manual say about the finish of each asset?"
PART_Z = "How often should the maintenance run?"   # the deliberately FREQUENCY part
QUESTION_3PART = f"{PART_X} {PART_Y} {PART_Z}"
PLAN_3PART_JSON = (
    '{"parts": ['
    f'{{"question": "{PART_X}", "source": "data"}}, '
    f'{{"question": "{PART_Y}", "source": "documents"}}, '
    f'{{"question": "{PART_Z}", "source": "both"}}'
    ']}'
)
COMPOSE1_ANSWER = "The finish is brushed steel. It should run every quarter."
CHECK_NAMES_X_JSON = '{"unanswered": [1]}'   # 1-based: part X (index 0)
BROADENED_X = pf.build_broadened_text(PART_X, QUESTION_3PART)
SQL_RESULT_X = "| asset |\n| --- |\n| Unit A |\n| Unit B |"
DOC_CHUNKS_X = [{"file_name": "manual.md", "content": "Units stored on shelf 3."}]
COMPOSE2_ANSWER = ("The storage room has Unit A and Unit B. The finish is brushed steel. "
                    "It should run every quarter.")

models5 = _Models(buffered=[PLAN_3PART_JSON, COMPOSE1_ANSWER, CHECK_NAMES_X_JSON,
                             COMPOSE2_ANSWER])
sql_calls5, search_calls5 = [], []
events5 = _run(QUESTION_3PART, models=models5,
               exec_fn=_sql_fn({BROADENED_X: SQL_RESULT_X, PART_Z: "| freq |\n| --- |\n| N/A |"},
                               sql_calls5),
               search_fn=_search_fn({BROADENED_X: DOC_CHUNKS_X, PART_Y: DOC_CHUNKS_B,
                                      PART_Z: []}, search_calls5),
               tool_choice_reachable=False, env=_env(PLAN_FIRST="1"))
tokens5 = _tokens(events5)
tools5 = _tool_events(events5)
check("RED: the final streamed text is the SECOND compose's answer",
      tokens5 == COMPOSE2_ANSWER, tokens5)
check("exactly 4 buffered calls: planner, compose 1, check, compose 2 - NEVER a second check",
      len(models5.calls) == 4, len(models5.calls))
check("part X's INITIAL sql call used its own (non-broadened) text (source=data)",
      sql_calls5[0] == PART_X, sql_calls5)
check("part X's RE-RUN sql call used the BROADENED text, not the bare part text",
      BROADENED_X in sql_calls5 and sql_calls5.count(PART_X) == 1, sql_calls5)
check("part X's RE-RUN ALSO ran a document search - 'run BOTH tools again' even though "
      "part X's own declared source was 'data' only the first time",
      BROADENED_X in search_calls5, search_calls5)
check("part Y's doc search ran once only (CHECK did not name it, no re-run for it)",
      search_calls5.count(PART_Y) == 1, search_calls5)
check("part Z (source=both) ran both tools once, and was NOT re-run (CHECK only named X)",
      PART_Z in sql_calls5 and PART_Z in search_calls5
      and sql_calls5.count(PART_Z) == 1 and search_calls5.count(PART_Z) == 1, sql_calls5)
check("the re-run's tool_start for part X names the ORIGINAL part number (Plan part 1), "
      "not its position within the retry subset",
      ("tool_start", {"tool": "query_structured_data", "args": {"question": BROADENED_X}})
      in tools5
      and ("tool_done", {"tool": "query_structured_data", "detail": "Plan part 1 complete"})
      in tools5, tools5)
check("the second compose's system text carries part X's NEW result",
      "Unit A" in models5.calls[3], models5.calls[3])

# =============================================================================
# 6. bounds: at most 6 parts are honoured even if the planner names 7
# =============================================================================
print("\n6. bounds: a 7-part plan -> only the first 6 are honoured")

SEVEN_PARTS_JSON = '{"parts": [' + ", ".join(
    f'{{"question": "Part {i} question?", "source": "both"}}' for i in range(1, 8)
) + ']}'
COMPOSE_SEVEN_ANSWER = "Here is everything, part by part."
models6 = _Models(buffered=[SEVEN_PARTS_JSON, COMPOSE_SEVEN_ANSWER, CHECK_CLEAN_JSON])
sql_calls6, search_calls6 = [], []
events6 = _run("Seven things, please.", models=models6,
               exec_fn=_sql_fn({f"Part {i} question?": f"result {i}" for i in range(1, 8)},
                               sql_calls6),
               search_fn=_search_fn({f"Part {i} question?": [] for i in range(1, 8)},
                                     search_calls6),
               tool_choice_reachable=False, env=_env(PLAN_FIRST="1"))
check("RED: only 6 distinct parts' worth of SQL calls happened, never a 7th",
      len(sql_calls6) == 6, sql_calls6)
check("the 6 kept are the FIRST 6, in order",
      sql_calls6 == [f"Part {i} question?" for i in range(1, 7)], sql_calls6)
check("the compose answer still streamed (bounded, not failed)",
      _tokens(events6) == COMPOSE_SEVEN_ANSWER, _tokens(events6))

# =============================================================================
# 7. a tool error during EXECUTE -> fails safe to the NORMAL path for the whole question
# =============================================================================
print("\n7. a part's tool call raises during EXECUTE -> falls back to the normal path "
     "for the WHOLE original question")


def _exec7(question, user_id, sb):
    sql_calls7.append(question)
    if len(sql_calls7) == 1:
        raise RuntimeError("boom: structured-data outage")
    return NORMAL_SQL_RESULT


sql_calls7 = []
models7 = _Models(buffered=[PLAN_2PART_JSON], stream_chunks=NORMAL_STREAM_CHUNKS)
events7 = _run(QUESTION_2PART, models=models7, exec_fn=_exec7,
               search_fn=_search_fn({}, []),
               tool_choice_reachable=True, tool_choice_args={"question": QUESTION_2PART},
               env=_env(PLAN_FIRST="1"))
check("RED: the final answer is the NORMAL path's own streamed answer for the ORIGINAL "
      "question - never an exception out of the turn",
      _tokens(events7) == "".join(NORMAL_STREAM_CHUNKS), _tokens(events7))
check("only the planner call happened on the buffered path - compose/check were never "
      "reached once EXECUTE failed",
      len(models7.calls) == 1, len(models7.calls))
check("exactly one streaming call ran afterwards (the normal path's own final answer)",
      models7.stream_calls == 1, models7.stream_calls)
check("the SQL tool was tried for part A (and failed), THEN tried again for the ORIGINAL "
      "question by the normal path's own dispatch - two calls total, in that order",
      sql_calls7 == [PART_A, QUESTION_2PART], sql_calls7)

# =============================================================================
# 8. malformed CHECK JSON -> KEEPS the step-3 composed answer (ruling W11-R1: CHECK is a
#    refinement, not a gate - only PLAN/EXECUTE/the FIRST compose still fall back to normal)
# =============================================================================
print("\n8. malformed CHECK JSON -> keeps and streams the step-3 COMPOSED answer, never the "
     "normal path (ruling W11-R1)")

sql_calls8, search_calls8 = [], []
models8 = _Models(buffered=[PLAN_2PART_JSON, COMPOSE_2PART_ANSWER, "not valid json, sorry"])
events8 = _run(QUESTION_2PART, models=models8,
               exec_fn=_sql_fn({PART_A: SQL_RESULT_A}, sql_calls8),
               search_fn=_search_fn({PART_B: DOC_CHUNKS_B}, search_calls8),
               tool_choice_reachable=False, env=_env(PLAN_FIRST="1"))
check("RED: a malformed CHECK reply KEEPS the already-produced step-3 composed answer - "
      "the normal path never runs at all (would have raised; no stream_chunks were even "
      "given to the fake)",
      _tokens(events8) == COMPOSE_2PART_ANSWER, _tokens(events8))
check("3 buffered calls happened (planner, compose, the failed check) - no 4th compose, "
      "since the check itself never validated a part to re-run",
      len(models8.calls) == 3, len(models8.calls))
check("the normal path's dispatch was never reached - no SQL call on the ORIGINAL question",
      QUESTION_2PART not in sql_calls8, sql_calls8)

# =============================================================================
# 9. the FIRST compose call itself raising -> STILL falls back to the normal path
#    (ruling W11-R1 leaves this one alone: only PLAN/EXECUTE/the first COMPOSE can still
#    trigger the whole-question fallback - there is no composed answer yet to keep)
# =============================================================================
print("\n9. the FIRST COMPOSE call itself raising -> still falls back to the normal path "
     "(unchanged by ruling W11-R1 - there is nothing yet to keep)")


class _ComposeRaises(_Models):
    def generate_content(self, *, model, contents, config=None):
        if len(self.calls) == 1:  # the SECOND call overall is the compose attempt
            self.calls.append(config.system_instruction if config else None)
            raise RuntimeError("boom: compose outage")
        return super().generate_content(model=model, contents=contents, config=config)


sql_calls9, search_calls9 = [], []
models9 = _ComposeRaises(buffered=[PLAN_2PART_JSON], stream_chunks=NORMAL_STREAM_CHUNKS)
events9 = _run(QUESTION_2PART, models=models9,
               exec_fn=_sql_fn({PART_A: SQL_RESULT_A, QUESTION_2PART: NORMAL_SQL_RESULT},
                               sql_calls9),
               search_fn=_search_fn({PART_B: DOC_CHUNKS_B}, search_calls9),
               tool_choice_reachable=True, tool_choice_args={"question": QUESTION_2PART},
               env=_env(PLAN_FIRST="1"))
check("RED: the FIRST compose call raising ALSO falls back to the normal path, never "
      "raises out of the turn (still unchanged - nothing was composed yet to keep)",
      _tokens(events9) == "".join(NORMAL_STREAM_CHUNKS), _tokens(events9))
check("both parts' tools HAD already run successfully before compose failed",
      sql_calls9[0] == PART_A and search_calls9 == [PART_B], (sql_calls9, search_calls9))
check("the normal path's own dispatch still ran on the original question afterwards",
      QUESTION_2PART in sql_calls9, sql_calls9)

# =============================================================================
# 10. the CHECK CALL ITSELF raising (not malformed JSON) -> KEEPS the step-3 composed
#     answer too (ruling W11-R1)
# =============================================================================
print("\n10. the CHECK call itself raising -> keeps and streams the step-3 composed answer "
     "(ruling W11-R1)")


class _CheckCallRaises(_Models):
    def generate_content(self, *, model, contents, config=None):
        if len(self.calls) == 2:  # PLAN + COMPOSE already succeeded; this is the CHECK try
            self.calls.append(config.system_instruction if config else None)
            raise RuntimeError("boom: check call outage")
        return super().generate_content(model=model, contents=contents, config=config)


sql_calls10b, search_calls10b = [], []
models10b = _CheckCallRaises(buffered=[PLAN_2PART_JSON, COMPOSE_2PART_ANSWER])
events10b = _run(QUESTION_2PART, models=models10b,
                 exec_fn=_sql_fn({PART_A: SQL_RESULT_A}, sql_calls10b),
                 search_fn=_search_fn({PART_B: DOC_CHUNKS_B}, search_calls10b),
                 tool_choice_reachable=False, env=_env(PLAN_FIRST="1"))
check("RED: the CHECK call raising KEEPS the step-3 composed answer - never the normal path",
      _tokens(events10b) == COMPOSE_2PART_ANSWER, _tokens(events10b))
check("exactly 3 calls were attempted (planner, compose, the failed check attempt)",
      len(models10b.calls) == 3, len(models10b.calls))
check("the normal path's dispatch was never reached",
      QUESTION_2PART not in sql_calls10b, sql_calls10b)

# =============================================================================
# 11. CHECK names a part whose RE-RUN raises -> KEEPS the step-3 (FIRST) composed answer
#     (ruling W11-R1: "if the re-run ... after a CHECK fails, stream the FIRST composed
#     answer")
# =============================================================================
print("\n11. CHECK names a part whose re-run raises -> keeps and streams the step-3 "
     "(FIRST) composed answer (ruling W11-R1)")

sql_calls11, search_calls11 = [], []


def _exec11(question, user_id, sb):
    sql_calls11.append(question)
    if question == BROADENED_X:
        raise RuntimeError("boom: retry structured-data outage")
    if question == PART_X:
        return SQL_RESULT_X
    if question == PART_Z:
        return "| freq |\n| --- |\n| N/A |"
    return "Query returned no results."


models11 = _Models(buffered=[PLAN_3PART_JSON, COMPOSE1_ANSWER, CHECK_NAMES_X_JSON])
events11 = _run(QUESTION_3PART, models=models11, exec_fn=_exec11,
               search_fn=_search_fn({PART_Y: DOC_CHUNKS_B, PART_Z: []}, search_calls11),
               tool_choice_reachable=False, env=_env(PLAN_FIRST="1"))
check("RED: a re-run tool call raising keeps the FIRST composed answer (step 3), never the "
      "normal path and never a 4th (second-compose) call",
      _tokens(events11) == COMPOSE1_ANSWER, _tokens(events11))
check("exactly 3 buffered calls happened: planner, compose 1, check - no second compose was "
      "ever attempted",
      len(models11.calls) == 3, len(models11.calls))
check("the re-run for part X WAS attempted (with the broadened text) before it raised",
      BROADENED_X in sql_calls11, sql_calls11)
check("the normal path's dispatch was never reached",
      QUESTION_3PART not in sql_calls11, sql_calls11)

# =============================================================================
# 12. CHECK names a part; the re-run succeeds, but the SECOND compose raises -> KEEPS the
#     step-3 (FIRST) composed answer (ruling W11-R1)
# =============================================================================
print("\n12. the SECOND compose (after a successful re-run) raising -> keeps and streams "
     "the step-3 (FIRST) composed answer (ruling W11-R1)")


class _SecondComposeRaises(_Models):
    def generate_content(self, *, model, contents, config=None):
        if len(self.calls) == 3:  # PLAN + COMPOSE1 + CHECK already succeeded; this is COMPOSE2
            self.calls.append(config.system_instruction if config else None)
            raise RuntimeError("boom: second compose outage")
        return super().generate_content(model=model, contents=contents, config=config)


sql_calls12, search_calls12 = [], []
models12b = _SecondComposeRaises(
    buffered=[PLAN_3PART_JSON, COMPOSE1_ANSWER, CHECK_NAMES_X_JSON])
events12b = _run(QUESTION_3PART, models=models12b,
                 exec_fn=_sql_fn({BROADENED_X: SQL_RESULT_X, PART_X: SQL_RESULT_X,
                                  PART_Z: "| freq |\n| --- |\n| N/A |"}, sql_calls12),
                 search_fn=_search_fn({BROADENED_X: DOC_CHUNKS_X, PART_Y: DOC_CHUNKS_B,
                                        PART_Z: []}, search_calls12),
                 tool_choice_reachable=False, env=_env(PLAN_FIRST="1"))
check("RED: the second compose raising (after a successful re-run) keeps the FIRST composed "
      "answer, never the normal path",
      _tokens(events12b) == COMPOSE1_ANSWER, _tokens(events12b))
check("exactly 4 calls were attempted: planner, compose 1, check, the failed compose-2 "
      "attempt",
      len(models12b.calls) == 4, len(models12b.calls))
check("the re-run DID succeed (both tools, broadened text) before the second compose failed",
      BROADENED_X in sql_calls12 and BROADENED_X in search_calls12,
      (sql_calls12, search_calls12))

# =============================================================================
# 13. works with the answer role on Claude - full flow, re-run included
# =============================================================================
print("\n13. the answer role on Claude (LLM_PROVIDER_ANSWER=anthropic) - Gemini never "
     "touched, full flow including a CHECK-triggered re-run")

models13 = _RaisingModels()
fake_claude13, claude_calls13 = _scripted_claude(
    [PLAN_3PART_JSON, COMPOSE1_ANSWER, CHECK_NAMES_X_JSON, COMPOSE2_ANSWER])
sql_calls13, search_calls13 = [], []
events13 = _run(QUESTION_3PART, models=models13,
               exec_fn=_sql_fn({BROADENED_X: SQL_RESULT_X,
                                 PART_Z: "| freq |\n| --- |\n| N/A |"}, sql_calls13),
               search_fn=_search_fn({BROADENED_X: DOC_CHUNKS_X, PART_Y: DOC_CHUNKS_B,
                                      PART_Z: []}, search_calls13),
               generate_answer_text_fn=fake_claude13,
               tool_choice_reachable=False,
               env=_env(PLAN_FIRST="1", LLM_PROVIDER_ANSWER="anthropic"))
check("RED: Claude carries the whole plan-first flow through to the final re-compose, "
      "Gemini never touched (would have raised otherwise)",
      _tokens(events13) == COMPOSE2_ANSWER, _tokens(events13))
check("exactly 4 generate_answer_text calls (Claude): plan, compose, check, re-compose",
      len(claude_calls13) == 4, len(claude_calls13))
check("the re-run tried BOTH tools for part X on Claude too (provider-independent)",
      BROADENED_X in sql_calls13 and BROADENED_X in search_calls13,
      (sql_calls13, search_calls13))

# =============================================================================
# 14. AGENTIC_SECOND_LOOK=1 + PLAN_FIRST=1 together: second look never also runs
# =============================================================================
print("\n14. AGENTIC_SECOND_LOOK=1 + PLAN_FIRST=1: the second look does not also run for a "
     "planned question")

models14 = _Models(buffered=[PLAN_2PART_JSON, COMPOSE_2PART_ANSWER, CHECK_CLEAN_JSON])
sql_calls14, search_calls14 = [], []
events14 = _run(QUESTION_2PART, models=models14,
               exec_fn=_sql_fn({PART_A: SQL_RESULT_A}, sql_calls14),
               search_fn=_search_fn({PART_B: DOC_CHUNKS_B}, search_calls14),
               tool_choice_reachable=False,
               env=_env(PLAN_FIRST="1", AGENTIC_SECOND_LOOK="1"))
check("RED: exactly the same 3 buffered calls as plan-first alone - no extra follow-up "
      "call layered on top by the second look",
      len(models14.calls) == 3 and _tokens(events14) == COMPOSE_2PART_ANSWER,
      (len(models14.calls), _tokens(events14)))

# =============================================================================
# 15. ANSWER_CHECK=1 + PLAN_FIRST=1: composes without extra calls when nothing to fix
# =============================================================================
print("\n15. ANSWER_CHECK=1 + PLAN_FIRST=1: the checker still gates the final answer, but "
     "adds no call when there is nothing to fix")

check("MIXED_SET is the only shipped check today (regression guard for this section's "
      "premise)", __import__("app.services.answer_check", fromlist=["SHIPPED_CHECKS"])
      .SHIPPED_CHECKS == frozenset({__import__(
          "app.services.answer_check", fromlist=["MIXED_SET"]).MIXED_SET}))

models15 = _Models(buffered=[PLAN_2PART_JSON, COMPOSE_2PART_ANSWER, CHECK_CLEAN_JSON])
sql_calls15, search_calls15 = [], []
events15 = _run(QUESTION_2PART, models=models15,
               exec_fn=_sql_fn({PART_A: SQL_RESULT_A}, sql_calls15),
               search_fn=_search_fn({PART_B: DOC_CHUNKS_B}, search_calls15),
               tool_choice_reachable=False,
               env=_env(PLAN_FIRST="1", ANSWER_CHECK="1"))
check("both switches on, nothing to fix: still exactly 3 buffered calls",
      len(models15.calls) == 3 and _tokens(events15) == COMPOSE_2PART_ANSWER,
      (len(models15.calls), _tokens(events15)))

# =============================================================================
# 16. an EMPTY first-pass SQL result is re-run even though CHECK says "clean"
#     (task W12-A1, requirement 4 - the empty-part retry does not depend on CHECK at all)
# =============================================================================
print("\n16. an empty first-pass SQL result is re-run even when CHECK returns a clean "
     "[]  - the empty-part retry is CHECK-independent")

# The literal sentinel `_second_look_sql` returns when the SQL writer deliberately comes
# back with no rows (e.g. a generated `SELECT NULL WHERE FALSE`) - verified against
# `sql_tool.py`'s own empty-result message shape and pinned here, per the brief.
EMPTY_SQL_RESULT = "Query returned no results.\n\nSQL: `SELECT NULL WHERE FALSE`"
check("pin: the empty-result sentinel a SQL tool call can return for a deliberate "
      "no-match query is exactly what sql_loop.result_is_empty calls empty",
      sql_loop_mod.result_is_empty(EMPTY_SQL_RESULT))

sql_calls16, search_calls16 = [], []


def _exec16(question, user_id, sb):
    sql_calls16.append(question)
    if question == BROADENED_X:
        return SQL_RESULT_X
    if question == PART_X:
        return EMPTY_SQL_RESULT
    if question == PART_Z:
        return "| freq |\n| --- |\n| N/A |"
    return "Query returned no results."


models16 = _Models(buffered=[PLAN_3PART_JSON, COMPOSE1_ANSWER, CHECK_CLEAN_JSON,
                               COMPOSE2_ANSWER])
events16 = _run(QUESTION_3PART, models=models16, exec_fn=_exec16,
               search_fn=_search_fn({BROADENED_X: DOC_CHUNKS_X, PART_Y: DOC_CHUNKS_B,
                                      PART_Z: []}, search_calls16),
               tool_choice_reachable=False, env=_env(PLAN_FIRST="1"))
tokens16 = _tokens(events16)
check("RED: CHECK said nothing was wrong, but the empty part still re-ran and the SECOND "
      "compose's answer streams",
      tokens16 == COMPOSE2_ANSWER, tokens16)
check("exactly 4 buffered calls: planner, compose 1, check, compose 2 - still only ONE "
      "check and ONE retry round",
      len(models16.calls) == 4, len(models16.calls))
check("part X's re-run used the BROADENED text and tried BOTH tools, even though CHECK "
      "never named it",
      sql_calls16 == [PART_X, PART_Z, BROADENED_X], sql_calls16)
check("part Y (documents-only) and part Z (both, non-empty) were NOT re-run - only part X's "
      "own re-run is new",
      search_calls16 == [PART_Y, PART_Z, BROADENED_X], search_calls16)

# =============================================================================
# 17. the CHECK CALL ITSELF raising still lets the empty-result part re-run - entirely
#     independent of CHECK (task W12-A1, requirement 4)
# =============================================================================
print("\n17. the CHECK call itself raising still lets the empty-result part re-run")

sql_calls17, search_calls17 = [], []


def _exec17(question, user_id, sb):
    sql_calls17.append(question)
    if question == BROADENED_X:
        return SQL_RESULT_X
    if question == PART_X:
        return EMPTY_SQL_RESULT
    if question == PART_Z:
        return "| freq |\n| --- |\n| N/A |"
    return "Query returned no results."


models17 = _CheckCallRaises(buffered=[PLAN_3PART_JSON, COMPOSE1_ANSWER, COMPOSE2_ANSWER])
events17 = _run(QUESTION_3PART, models=models17, exec_fn=_exec17,
               search_fn=_search_fn({BROADENED_X: DOC_CHUNKS_X, PART_Y: DOC_CHUNKS_B,
                                      PART_Z: []}, search_calls17),
               tool_choice_reachable=False, env=_env(PLAN_FIRST="1"))
check("RED: the check call raising did not stop the empty part from being re-run - the "
      "SECOND compose's answer streams",
      _tokens(events17) == COMPOSE2_ANSWER, _tokens(events17))
check("exactly 4 calls were attempted: planner, compose 1, the failed check attempt, "
      "compose 2",
      len(models17.calls) == 4, len(models17.calls))
check("part X's re-run DID happen (broadened, both tools) despite the check call raising",
      BROADENED_X in sql_calls17 and BROADENED_X in search_calls17,
      (sql_calls17, search_calls17))

# =============================================================================
# 18. an UNPARSEABLE check reply (as opposed to the call raising) ALSO lets the empty part
#     re-run (task W12-A1, requirement 4)
# =============================================================================
print("\n18. an unparseable CHECK reply also lets the empty-result part re-run")

sql_calls18, search_calls18 = [], []


def _exec18(question, user_id, sb):
    sql_calls18.append(question)
    if question == BROADENED_X:
        return SQL_RESULT_X
    if question == PART_X:
        return EMPTY_SQL_RESULT
    if question == PART_Z:
        return "| freq |\n| --- |\n| N/A |"
    return "Query returned no results."


models18 = _Models(buffered=[PLAN_3PART_JSON, COMPOSE1_ANSWER, "not valid json, sorry",
                               COMPOSE2_ANSWER])
events18 = _run(QUESTION_3PART, models=models18, exec_fn=_exec18,
               search_fn=_search_fn({BROADENED_X: DOC_CHUNKS_X, PART_Y: DOC_CHUNKS_B,
                                      PART_Z: []}, search_calls18),
               tool_choice_reachable=False, env=_env(PLAN_FIRST="1"))
check("RED: an unparseable check reply did not stop the empty part from being re-run",
      _tokens(events18) == COMPOSE2_ANSWER, _tokens(events18))
check("exactly 4 buffered calls: planner, compose 1, the unparseable check, compose 2",
      len(models18.calls) == 4, len(models18.calls))
check("part X's re-run DID happen (broadened, both tools) despite the unparseable check "
      "reply",
      BROADENED_X in sql_calls18 and BROADENED_X in search_calls18,
      (sql_calls18, search_calls18))

# =============================================================================
# 19. a NON-empty part is not re-run, even with an active empty-part retry elsewhere
#     (task W12-A1, requirement 4 - contrast case)
# =============================================================================
print("\n19. a non-empty part is not re-run, even while a DIFFERENT part's empty result "
     "triggers the retry")

check("part Z's own (non-empty) first-pass SQL ran exactly once in section 16, never "
      "broadened",
      sql_calls16.count(PART_Z) == 1
      and pf.build_broadened_text(PART_Z, QUESTION_3PART) not in sql_calls16,
      sql_calls16)

# =============================================================================
# 20. the UNION: CHECK names one part, a DIFFERENT part is independently empty - BOTH are
#     retried, each exactly once (task W12-A1, requirement 4)
# =============================================================================
print("\n20. the union: CHECK names one part, a different part is independently empty - "
     "both get retried, each once")

BROADENED_Z = pf.build_broadened_text(PART_Z, QUESTION_3PART)
CHECK_NAMES_Z_JSON = '{"unanswered": [3]}'   # 1-based: part Z (index 2)

sql_calls20, search_calls20 = [], []


def _exec20(question, user_id, sb):
    sql_calls20.append(question)
    if question == PART_X:
        return EMPTY_SQL_RESULT
    if question == BROADENED_X:
        return SQL_RESULT_X
    if question == BROADENED_Z:
        return "| freq |\n| --- |\n| Every quarter |"
    if question == PART_Z:
        return "| freq |\n| --- |\n| N/A |"
    return "Query returned no results."


models20 = _Models(buffered=[PLAN_3PART_JSON, COMPOSE1_ANSWER, CHECK_NAMES_Z_JSON,
                               COMPOSE2_ANSWER])
events20 = _run(QUESTION_3PART, models=models20, exec_fn=_exec20,
               search_fn=_search_fn({PART_Y: DOC_CHUNKS_B, BROADENED_X: DOC_CHUNKS_X,
                                      BROADENED_Z: [], PART_Z: []}, search_calls20),
               tool_choice_reachable=False, env=_env(PLAN_FIRST="1"))
check("RED: CHECK named Z and X was independently empty - the second compose streams",
      _tokens(events20) == COMPOSE2_ANSWER, _tokens(events20))
check("part X (empty on pass 1, not named by CHECK) WAS retried",
      BROADENED_X in sql_calls20, sql_calls20)
check("part Z (named by CHECK, not empty) WAS ALSO retried",
      BROADENED_Z in sql_calls20, sql_calls20)
check("each retried part ran its re-run EXACTLY once - the union does not double a part",
      sql_calls20.count(BROADENED_X) == 1 and sql_calls20.count(BROADENED_Z) == 1,
      sql_calls20)
check("part Y (source=documents, named by neither source) was not re-run at all",
      search_calls20.count(PART_Y) == 1, search_calls20)
check("exactly 4 buffered calls total - still one check, one retry round",
      len(models20.calls) == 4, len(models20.calls))

# =============================================================================
# 21. the SAME part named by BOTH CHECK and the empty-result rule is still retried only
#     ONCE - the union de-duplicates (task W12-A1, requirement 4)
# =============================================================================
print("\n21. a part named by BOTH check and the empty-result rule is retried only once")

sql_calls21, search_calls21 = [], []


def _exec21(question, user_id, sb):
    sql_calls21.append(question)
    if question == PART_X:
        return EMPTY_SQL_RESULT
    if question == BROADENED_X:
        return SQL_RESULT_X
    if question == PART_Z:
        return "| freq |\n| --- |\n| N/A |"
    return "Query returned no results."


models21 = _Models(buffered=[PLAN_3PART_JSON, COMPOSE1_ANSWER, CHECK_NAMES_X_JSON,
                               COMPOSE2_ANSWER])
events21 = _run(QUESTION_3PART, models=models21, exec_fn=_exec21,
               search_fn=_search_fn({PART_Y: DOC_CHUNKS_B, BROADENED_X: DOC_CHUNKS_X,
                                      PART_Z: []}, search_calls21),
               tool_choice_reachable=False, env=_env(PLAN_FIRST="1"))
check("RED: the second compose's answer streams", _tokens(events21) == COMPOSE2_ANSWER,
      _tokens(events21))
check("part X's re-run happened exactly ONCE, not twice, though it was named by CHECK AND "
      "empty on pass 1",
      sql_calls21.count(BROADENED_X) == 1, sql_calls21)
check("exactly 4 buffered calls: planner, compose 1, check, compose 2 - never a 5th",
      len(models21.calls) == 4, len(models21.calls))

# =============================================================================
# 22. a FREQUENCY part runs BOTH tools on its first pass, whatever its declared source
#     (task W12-A1, requirement 3)
# =============================================================================
print("\n22. a part whose text asks about maintenance frequency runs BOTH tools on its "
     "first pass even when the planner declared it 'documents' only")

PART_FREQ_DOCS = "How often should the unit be inspected?"
PART_PLAIN_DOCS = "What does the manual say about the unit's finish?"
QUESTION_FREQ = f"{PART_FREQ_DOCS} {PART_PLAIN_DOCS}"
PLAN_FREQ_JSON = (
    '{"parts": ['
    f'{{"question": "{PART_FREQ_DOCS}", "source": "documents"}}, '
    f'{{"question": "{PART_PLAIN_DOCS}", "source": "documents"}}'
    ']}'
)
SQL_RESULT_FREQ = "| frequency |\n| --- |\n| Every month |"
DOC_CHUNKS_FREQ = [{"file_name": "manual.md", "content": "Inspect it regularly."}]
DOC_CHUNKS_PLAIN = [{"file_name": "manual.md", "content": "Finish: brushed steel."}]
COMPOSE_FREQ_ANSWER = "It should be inspected every month. The finish is brushed steel."

sql_calls22, search_calls22 = [], []
models22 = _Models(buffered=[PLAN_FREQ_JSON, COMPOSE_FREQ_ANSWER, CHECK_CLEAN_JSON])
events22 = _run(QUESTION_FREQ, models=models22,
               exec_fn=_sql_fn({PART_FREQ_DOCS: SQL_RESULT_FREQ}, sql_calls22),
               search_fn=_search_fn({PART_FREQ_DOCS: DOC_CHUNKS_FREQ,
                                      PART_PLAIN_DOCS: DOC_CHUNKS_PLAIN}, search_calls22),
               tool_choice_reachable=False, env=_env(PLAN_FIRST="1"))
check("RED: the composed answer streams", _tokens(events22) == COMPOSE_FREQ_ANSWER,
      _tokens(events22))
check("the FREQUENCY part (declared 'documents') ALSO got a structured-data call on its "
      "own text - requirement 3",
      sql_calls22 == [PART_FREQ_DOCS], sql_calls22)
check("the frequency part's OWN document search still ran too (both tools, not instead of)",
      PART_FREQ_DOCS in search_calls22, search_calls22)
check("the ORDINARY part (declared 'documents', no frequency wording) got ONLY the "
      "document search - no structured-data call for it",
      PART_PLAIN_DOCS not in sql_calls22 and PART_PLAIN_DOCS in search_calls22,
      (sql_calls22, search_calls22))
check("exactly 3 buffered calls: planner, compose, check - nothing was empty, no retry",
      len(models22.calls) == 3, len(models22.calls))

# =============================================================================
# 23. RULING W12-R8 (fix round 1, review finding 1): a frequency part's forced SQL coming
#     back empty STILL triggers the retry round even though its OWN document search already
#     fully answered it and CHECK found nothing wrong.
#
#     THIS IS A REGRESSION GUARD, NOT A RED-FIRST TEST - it pins EXISTING behaviour on
#     `2259aa1` unchanged: requirement 4's retry-set union (CHECK's own list | the empty-SQL
#     indexes) is unconditional by construction, so it already retries a frequency part
#     whenever its forced SQL comes back empty, with no carve-out for "but the OTHER tool
#     already succeeded for this same part". This section passes against `2259aa1` as-is, no
#     production code changed for it - see the comment above `empty_indexes = ...` in
#     `_run_plan_first` (openai_client.py) for the ruling and why it is kept: a recorded case
#     found only one of two schedule rows through the document search alone, so a second,
#     broadened try at the data path is worth one extra round even on an already-correct part.
# =============================================================================
print("\n23. ruling W12-R8: a frequency part's forced SQL coming back empty still triggers "
     "the retry round even though its OWN document search already answered it and CHECK was "
     "clean (REGRESSION GUARD - pins existing 2259aa1 behaviour, not a new fix)")

BROADENED_FREQ = pf.build_broadened_text(PART_FREQ_DOCS, QUESTION_FREQ)
COMPOSE_FREQ_ANSWER2 = "It should be inspected every month. The finish is brushed steel. (re-confirmed)"

sql_calls23, search_calls23 = [], []
models23 = _Models(buffered=[PLAN_FREQ_JSON, COMPOSE_FREQ_ANSWER, CHECK_CLEAN_JSON,
                               COMPOSE_FREQ_ANSWER2])
events23 = _run(QUESTION_FREQ, models=models23,
               exec_fn=_sql_fn({PART_FREQ_DOCS: EMPTY_SQL_RESULT}, sql_calls23),
               search_fn=_search_fn({PART_FREQ_DOCS: DOC_CHUNKS_FREQ,
                                      PART_PLAIN_DOCS: DOC_CHUNKS_PLAIN}, search_calls23),
               tool_choice_reachable=False, env=_env(PLAN_FIRST="1"))
tokens23 = _tokens(events23)
check("the SECOND compose's answer streams - the retry round fired even though CHECK was "
      "clean and the frequency part's own document search had already succeeded",
      tokens23 == COMPOSE_FREQ_ANSWER2, tokens23)
check("exactly 4 buffered calls: planner, compose 1, check, compose 2 - still only ONE "
      "check and ONE retry round",
      len(models23.calls) == 4, len(models23.calls))
check("the frequency part WAS retried, broadened, with BOTH tools - solely because its "
      "forced SQL came back empty (ruling W12-R8), not because CHECK or the document "
      "search found anything wrong",
      sql_calls23.count(BROADENED_FREQ) == 1 and BROADENED_FREQ in search_calls23,
      (sql_calls23, search_calls23))
check("the ordinary part (never forced, never empty, not named by CHECK) was NOT retried",
      search_calls23.count(PART_PLAIN_DOCS) == 1, search_calls23)
check("the second compose's system text still carries the document content from the "
      "already-successful pass-1 search - the extra round never discards good content "
      "(ruling W11-R1's guarantee holds through ruling W12-R8 too)",
      "Inspect it regularly." in models23.calls[3], models23.calls[3])

# ===========================================================================
# 24. HEDGE RETRY (task W13-A1, requirement 2): a composed answer that hedges - even with a
#     CLEAN check - sends EVERY part to the retry set, once, with both tools and broadened
#     text, then one more compose
# ===========================================================================
print("\n24. a composed answer that hedges, with a clean CHECK, retries EVERY part once")

COMPOSE_2PART_HEDGE_ANSWER = ("The storage room has Unit A and Unit B. I can't say whether "
                              "the finish matches for both.")
COMPOSE_2PART_ANSWER2 = ("The storage room has Unit A and Unit B. The manual confirms a "
                         "brushed steel finish for both.")
BROADENED_A = pf.build_broadened_text(PART_A, QUESTION_2PART)
BROADENED_B = pf.build_broadened_text(PART_B, QUESTION_2PART)

sql_calls24, search_calls24 = [], []
models24 = _Models(buffered=[PLAN_2PART_JSON, COMPOSE_2PART_HEDGE_ANSWER, CHECK_CLEAN_JSON,
                               COMPOSE_2PART_ANSWER2])
events24 = _run(QUESTION_2PART, models=models24,
               exec_fn=_sql_fn({PART_A: SQL_RESULT_A}, sql_calls24),
               search_fn=_search_fn({PART_B: DOC_CHUNKS_B}, search_calls24),
               tool_choice_reachable=False, env=_env(PLAN_FIRST="1"))
check("RED: a hedging composed answer, even with a clean CHECK, triggers one retry round "
      "of EVERY part - the second compose's answer streams",
      _tokens(events24) == COMPOSE_2PART_ANSWER2, _tokens(events24))
check("exactly 4 buffered calls: planner, compose 1, check, compose 2 - still only ONE "
      "check and ONE retry round",
      len(models24.calls) == 4, len(models24.calls))
check("RED: BOTH parts were re-run, broadened, with BOTH tools - A's own data path AND a "
      "forced document search, B's own document path AND a forced data search",
      sql_calls24 == [PART_A, BROADENED_A, BROADENED_B]
      and search_calls24 == [PART_B, BROADENED_A, BROADENED_B],
      (sql_calls24, search_calls24))
check("the check call's own contents still carry the HEDGING composed answer (CHECK reads "
      "what was actually shipped, not a cleaned-up version)",
      COMPOSE_2PART_HEDGE_ANSWER in models24.contents_text[2], models24.contents_text[2])

# ===========================================================================
# 25. no hedge -> no extra round (the negative case; section 4's own non-hedging answer
#     already proves this for every run above - re-asserted here explicitly, by name, as
#     task W13-A1's own required negative)
# ===========================================================================
print("\n25. no hedge in the composed answer -> no extra round")

check("RED: section 4's non-hedging COMPOSE_2PART_ANSWER carries no hedge sentence at all",
      pf.hedge_sentences(COMPOSE_2PART_ANSWER) == [], pf.hedge_sentences(COMPOSE_2PART_ANSWER))
check("...and section 4 already measured exactly 3 buffered calls for it - no 4th, no "
      "retry (re-read, not re-run: proves the hedge check adds nothing when there is no "
      "hedge)",
      len(models4.calls) == 3, len(models4.calls))

# ===========================================================================
# 26. hedge + CHECK naming a part + an independently empty part, together - the union
#     still retries each part EXACTLY ONCE (task W13-A1, requirement 2)
# ===========================================================================
print("\n26. hedge + CHECK (naming one part) + an empty part, together - each part still "
     "retried exactly once")

COMPOSE1_HEDGE_ANSWER_26 = ("The finish is brushed steel. I can't say how often it runs, "
                            "though.")
CHECK_NAMES_Y_JSON = '{"unanswered": [2]}'   # 1-based: part Y (index 1)
COMPOSE2_ANSWER_26 = ("The storage room has Unit A and Unit B. The finish is brushed "
                      "steel. It runs every quarter, confirmed.")
BROADENED_Y = pf.build_broadened_text(PART_Y, QUESTION_3PART)
BROADENED_Z = pf.build_broadened_text(PART_Z, QUESTION_3PART)

sql_calls26, search_calls26 = [], []
models26 = _Models(buffered=[PLAN_3PART_JSON, COMPOSE1_HEDGE_ANSWER_26, CHECK_NAMES_Y_JSON,
                               COMPOSE2_ANSWER_26])
events26 = _run(QUESTION_3PART, models=models26,
               exec_fn=_sql_fn({PART_X: EMPTY_SQL_RESULT,
                                PART_Z: "| freq |\n| --- |\n| N/A |"}, sql_calls26),
               search_fn=_search_fn({PART_Y: DOC_CHUNKS_B, PART_Z: []}, search_calls26),
               tool_choice_reachable=False, env=_env(PLAN_FIRST="1"))
check("RED: three overlapping signals (hedge naming every part, CHECK naming Y, X's own "
      "empty first-pass result) still ship after exactly one retry round",
      _tokens(events26) == COMPOSE2_ANSWER_26, _tokens(events26))
check("exactly 4 buffered calls: planner, compose 1, check, compose 2 - still only ONE "
      "check and ONE retry round",
      len(models26.calls) == 4, len(models26.calls))
check("RED: every part's broadened text was tried exactly ONCE on EACH tool, though hedge, "
      "CHECK (naming Y alone) and X's own empty result all three point at a retry",
      sql_calls26.count(BROADENED_X) == 1 and sql_calls26.count(BROADENED_Y) == 1
      and sql_calls26.count(BROADENED_Z) == 1
      and search_calls26.count(BROADENED_X) == 1 and search_calls26.count(BROADENED_Y) == 1
      and search_calls26.count(BROADENED_Z) == 1,
      (sql_calls26, search_calls26))
check("RED: part Z was retried too, SOLELY because of the hedge - CHECK never named it and "
      "its own first pass was never empty",
      BROADENED_Z in sql_calls26 and BROADENED_Z in search_calls26,
      (sql_calls26, search_calls26))

# ===========================================================================
# 27. PLAN_FIRST unset -> byte-identical, even when the NORMAL path's own answer happens to
#     read like a hedge (task W13-A1's own code path is unreachable without the switch)
# ===========================================================================
print("\n27. PLAN_FIRST unset -> byte-identical even when the streamed answer hedges")

HEDGING_STREAM_CHUNKS = ["I can't say for certain, but roughly 42 widgets are stored there."]
models27 = _Models(stream_chunks=HEDGING_STREAM_CHUNKS)
sql_calls27 = []
events27 = _run(QUESTION_1PART, models=models27,
               exec_fn=_sql_fn({QUESTION_1PART: NORMAL_SQL_RESULT}, sql_calls27),
               search_fn=_search_fn({}, []), env=_env())
check("RED: the normal streaming answer runs untouched, hedge words and all - no planner, "
      "no compose, no check, no buffered call of any kind",
      _tokens(events27) == "".join(HEDGING_STREAM_CHUNKS), _tokens(events27))
check("...zero buffered calls (hedge_sentences is never even reached without PLAN_FIRST)",
      models27.calls == [], models27.calls)

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
