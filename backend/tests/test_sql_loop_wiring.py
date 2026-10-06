"""test_sql_loop_wiring.py - Task 4: the bounded SQL investigation wired into
`stream_response` (spec 2026-09-23 deliverable 3).

Until this task the `query_structured_data` branch of `stream_response` ran ONE
`execute_sql_query`, looked at the string it got back only far enough to decide
between two document fallbacks, and handed it to the answer writer. Task 3 built
`app/services/sql_loop.py`, which inspects that string by code, re-queries on a
deficiency it can name, cross-checks a quantity against the documents, and owns
BOTH of those fallbacks word for word. This file pins the wiring.

The load-bearing property, and the reason most of this file is event sequences:
spec 4 binds `SQL_LOOP_MAX_STEPS=1` to "byte-identical to today". The ruler
scores trajectories, so "today" is not only today's ANSWER - it is today's exact
sequence of `tool_start` / `tool_done` events, with today's `args` and today's
`detail` wording. Sections 5a/5b/5c write those three sequences out as literal
expected lists, derived by reading the code this task deletes (commit `a17da28`,
`openai_client.py` lines 1303-1366), so that a change in wording or an extra
event turns a check red rather than quietly moving the trajectory.

Two consequences of that pin, both deliberate and both asserted here:

* The first step emits NO `tool_start` of its own. The dispatcher already yields
  one for every tool at the top of the dispatch (`{"tool": tool_name, "args":
  args}`), and that event IS step 1's - adding a second would be a new event on
  a one-step question. Steps 2+ get their own, carrying `step` and `issue`.
* Step 1's `tool_done` keeps today's wording, `"Query executed"`, whenever the
  step raised no issue - which at `max_steps=1` is always, because the loop does
  not inspect at all when it cannot act. Later steps, and any step that DID
  raise an issue, say `step k: N rows` and name the issue.

One divergence from today is intentional, is directed by the task brief, and is
pinned by section 7 rather than hidden: the document search behind a FAILED
query now runs on the user's own wording, as the EMPTY fallback already did
today, instead of on the router's paraphrase.

Run:
    venv/Scripts/python -X utf8 tests/test_sql_loop_wiring.py
"""
import inspect
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


from app.services import settings as settings_mod  # noqa: E402
from app.services import sql_tool  # noqa: E402
from app.services import sql_loop  # noqa: E402
from app.services import openai_client as oc  # noqa: E402
from app.services import llm_usage as llm_usage_mod  # noqa: E402


# ===========================================================================
# 1. settings.get_sql_loop_max_steps() - the flag that switches the loop off
# ===========================================================================
print("1. get_sql_loop_max_steps() reads SQL_LOOP_MAX_STEPS")

_saved_env = os.environ.pop("SQL_LOOP_MAX_STEPS", None)


def _with_env(value):
    if value is None:
        os.environ.pop("SQL_LOOP_MAX_STEPS", None)
    else:
        os.environ["SQL_LOOP_MAX_STEPS"] = value
    return settings_mod.get_sql_loop_max_steps()


check("the setting exists", hasattr(settings_mod, "get_sql_loop_max_steps"))
if hasattr(settings_mod, "get_sql_loop_max_steps"):
    check("unset -> 3 (the loop is ON by default, spec 2 deliverable 3)",
          _with_env(None) == 3, _with_env(None))
    check("'1' -> 1 (the documented way to reproduce today's behaviour)",
          _with_env("1") == 1, _with_env("1"))
    check("'2' -> 2", _with_env("2") == 2, _with_env("2"))
    check("garbage -> 3, never a crash and never 0 (a mistyped setting must "
          "not silently disable the loop)", _with_env("banana") == 3, _with_env("banana"))
    check("'0' -> 3 (a step budget below one is not a budget)",
          _with_env("0") == 3, _with_env("0"))
    check("'-4' -> 3", _with_env("-4") == 3, _with_env("-4"))
    check("blank -> 3", _with_env("   ") == 3, _with_env("   "))
    check("'3.7' -> 3 (not a whole number of steps)", _with_env("3.7") == 3, _with_env("3.7"))
    check("the return type is int", isinstance(_with_env("2"), int), type(_with_env("2")))

os.environ.pop("SQL_LOOP_MAX_STEPS", None)
if _saved_env is not None:
    os.environ["SQL_LOOP_MAX_STEPS"] = _saved_env


# ===========================================================================
# 2. sql_tool.route_tables() - the routed CARDS, for the inspector
# ===========================================================================
print("\n2. sql_tool.route_tables() exposes the routed cards")


class _ExecResult:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, data):
        self._data = data

    def select(self, *a, **kw):
        return self

    def eq(self, *a, **kw):
        return self

    def order(self, *a, **kw):
        return self

    def execute(self):
        return _ExecResult(self._data)


class _FakeSupabase:
    """Serves table_cards rows in the shape `_load_table_cards` reads."""

    def __init__(self, cards):
        self._cards = cards

    def table(self, name):
        if name == "table_cards":
            return _Query([{"table_name": c["table"], "card": c} for c in self._cards])
        return _Query([])


CARD_A = {"table": "bld_alpha_units", "holds": "one row per unit on a level",
          "columns": ["unit_tag", "level_code", "source_page"],
          "identifier_column": "unit_tag",
          "keywords": ["unit", "units", "level", "alpha"]}
CARD_B = {"table": "bld_beta_readings", "holds": "one row per reading",
          "columns": ["reading_id", "taken_on", "source_page"],
          "identifier_column": "reading_id",
          "keywords": ["reading", "readings", "beta"]}


def _reset_cards_cache():
    sql_tool._table_cards_cache = None
    sql_tool._table_cards_cache_mtime = None
    sql_tool._table_cards_cache_user = None
    sql_tool._table_cards_cache_expires = 0


check("route_tables exists", hasattr(sql_tool, "route_tables"))
if hasattr(sql_tool, "route_tables"):
    _reset_cards_cache()
    routed = sql_tool.route_tables("which units are on the level", "u-1",
                                   _FakeSupabase([CARD_A, CARD_B]))
    check("returns CARDS (dicts), not table names",
          bool(routed) and all(isinstance(c, dict) for c in routed), routed)
    check("the routed card for a question about units is the units card",
          [c["table"] for c in routed][:1] == ["bld_alpha_units"],
          [c.get("table") for c in routed])
    check("the card it returns is the whole card, identifier_column and all "
          "(that field is what the inspector needs)",
          bool(routed) and routed[0].get("identifier_column") == "unit_tag", routed[:1])

    # "No cards" means the whole database -> file -> nothing chain came up
    # empty, not merely that the database did: an empty `table_cards` table on a
    # machine that still has `app/data/table_cards.json` legitimately routes off
    # the file, exactly as `execute_sql_query` does. Stub the loader, or this
    # asserts the fallback away on one machine and the degrade on another.
    _reset_cards_cache()
    _real_load = sql_tool._load_table_cards
    sql_tool._load_table_cards = lambda *a, **k: []
    try:
        no_cards = sql_tool.route_tables("anything at all", "u-1", _FakeSupabase([]))
    finally:
        sql_tool._load_table_cards = _real_load
        _reset_cards_cache()
    check("no cards at all -> [] (the documented degrade: the inspector's "
          "column-shaped issues then simply never fire)", no_cards == [], no_cards)

    _reset_cards_cache()
    from_file = sql_tool.route_tables("anything at all", "u-1", _FakeSupabase([]))
    check("an empty table_cards table still routes off the local file, exactly "
          "as execute_sql_query does - route_tables adds no new failure mode",
          isinstance(from_file, list)
          and all(isinstance(c, dict) and "table" in c for c in from_file),
          [c.get("table") for c in from_file])
    _reset_cards_cache()

    # I-3. The cards can drift behind the corpus - observed, and documented in
    # `_load_table_cards` ("drifted four tables behind the live corpus"). When
    # the router's pick matches NO live table `execute_sql_query` falls back to
    # the full schema, so the writer never saw that card; if `route_tables`
    # returned it anyway the inspector could demand a column of a table nobody
    # queried, spending a whole step on an instruction that cannot be obeyed.
    def _route_live(live):
        """route_tables with a live-table set, or the exception it raised."""
        _reset_cards_cache()
        try:
            return sql_tool.route_tables("which units are on the level", "u-1",
                                         _FakeSupabase([CARD_A, CARD_B]),
                                         live_table_names=live)
        except Exception as e:  # noqa: BLE001 - reported as the check's failure
            return e

    stale = _route_live(["bld_gamma_other"])
    check("a routed card naming a table that is not live -> [] (mirrors "
          "execute_sql_query's 'selection matched no live tables' fallback)",
          stale == [], repr(stale))

    partial = _route_live(["bld_alpha_units"])
    check("a partly-live selection keeps exactly the live cards, as "
          "execute_sql_query keeps the live tables",
          isinstance(partial, list)
          and [c["table"] for c in partial] == ["bld_alpha_units"], repr(partial))

    _reset_cards_cache()
    unknown = sql_tool.route_tables("which units are on the level", "u-1",
                                    _FakeSupabase([CARD_A, CARD_B]))
    check("no live names given -> no filtering at all (an unknown live set is "
          "not an empty one; the caller may not know it)",
          [c["table"] for c in unknown][:1] == ["bld_alpha_units"],
          [c.get("table") for c in unknown])

    _reset_cards_cache()
    _real_load = sql_tool._load_table_cards
    sql_tool._load_table_cards = lambda *a, **k: [{"no_table_key_here": True}]
    raised, bad = None, None
    try:
        bad = sql_tool.route_tables("anything", "u-1", _FakeSupabase([]))
    except Exception as e:  # noqa: BLE001 - not raising IS the assertion
        raised = e
    finally:
        sql_tool._load_table_cards = _real_load
        _reset_cards_cache()
    check("a malformed cards file degrades to [] instead of raising "
          "(same guard execute_sql_query has - I4)",
          raised is None and bad == [], f"{raised!r} {bad!r}")


# ===========================================================================
# 3. The branch source: the investigation replaced the one-shot call
# ===========================================================================
print("\n3. The query_structured_data branch calls the investigation")


def _branch_source():
    src = inspect.getsource(oc.stream_response)
    lines = src.splitlines()
    head = 'elif tool_name == "query_structured_data":'
    start = next((i for i, l in enumerate(lines) if l.strip() == head), None)
    if start is None:
        return ""
    indent = len(lines[start]) - len(lines[start].lstrip())
    for j in range(start + 1, len(lines)):
        s = lines[j]
        if s.strip().startswith("elif tool_name ==") and (len(s) - len(s.lstrip())) == indent:
            return "\n".join(lines[start:j])
    return "\n".join(lines[start:])


branch = _branch_source()
check("the branch was found in stream_response", bool(branch))
sql_calls = [l.strip() for l in branch.splitlines() if "execute_sql_query(" in l]
check("the one-shot call is gone - the branch never assigns a SQL result "
      "directly", "result_text = execute_sql_query(" not in branch, sql_calls)
# Fix round 1 (I-1) put a named guard around the executor, and that guard has to
# call `execute_sql_query`. So the rule is not "never name it" but "reach it only
# through the wrapper the loop is handed": every call site is a `return` inside
# `_execute_sql_for_loop`, and what goes into `execute=` is the wrapper.
check("execute_sql_query is reached only from inside the guarded executor, "
      "never called inline",
      bool(sql_calls)
      and all(c.startswith("return execute_sql_query(") for c in sql_calls), sql_calls)
check("and what the investigation is handed is that guarded executor",
      "execute=_execute_sql_for_loop," in branch,
      [l for l in branch.splitlines() if "execute=" in l])
check("the branch calls iter_sql_investigation(",
      "iter_sql_investigation(" in branch)
check("the branch passes the routed cards in",
      "routed_cards=" in branch)
check("the branch tells route_tables which tables are actually live (I-3)",
      "live_table_names=" in branch)
check("the branch takes its step budget from the setting",
      "get_sql_loop_max_steps()" in branch)
check("the old inline empty-result fallback block is gone",
      "_sql_result_is_empty(result_text)" not in branch,
      [l for l in branch.splitlines() if "_sql_result_is_empty" in l])
check("the old inline failed-result fallback block is gone",
      'result_text.startswith("SQL query failed")' not in branch,
      [l for l in branch.splitlines() if "SQL query failed" in l])
check("the question augmentation with the user's own wording survives "
      "(it predates the loop and is not part of it)",
      "Additional context from the assistant" in branch)
check("the branch still takes tool_name from the investigation's own "
      "source_tool (today's reassignment, now reported rather than inferred)",
      "source_tool" in branch)


# ===========================================================================
# 4. The prompt rules
# ===========================================================================
print("\n4. OUTPUT_FORMAT_RULES names INVESTIGATION and the last result")

rules = oc.OUTPUT_FORMAT_RULES
never_print = next((l for l in rules.splitlines()
                    if "SQL: `" in l and "IMPORTANT" in l), "")
check("the never-print sentence exists", bool(never_print))
check("the never-print sentence names INVESTIGATION (the trailer sql_loop "
      "appends must never be quoted back to the user)",
      "INVESTIGATION" in never_print, never_print[:200])

shape_bullet = next((l for l in rules.splitlines() if "RESULT SHAPE" in l), "")
check("the RESULT SHAPE bullet exists", bool(shape_bullet))
check("the RESULT SHAPE bullet tells the writer to answer from the LAST "
      "result when several investigation steps ran",
      "LAST" in shape_bullet and "step" in shape_bullet.lower(), shape_bullet[-260:])

# I-2. The cross-check excerpts used to arrive with nothing saying what they are
# FOR. The table figure is the answer; the excerpts are rivals to name, and they
# are the top hits of an unfiltered keyword search, so some of them print no
# figure at all. Without this the S/C shape the new cards grade ("421, from the
# mechanical register; the BMS manual prints 424") depended on the model's
# goodwill.
crosscheck_bullet = next((l for l in rules.splitlines()
                          if "cross-check" in l and "excerpt" in l.lower()), "")
check("there is a bullet about cross-check excerpts", bool(crosscheck_bullet))
check("it says the TABLE figure is the answer",
      "TABLE figure" in crosscheck_bullet, crosscheck_bullet[:200])
check("it says the excerpts are OTHER records that may print a different figure",
      "OTHER records" in crosscheck_bullet
      and "different figure" in crosscheck_bullet, crosscheck_bullet[:300])
check("it says to name each such figure and where it is printed",
      "where it is printed" in crosscheck_bullet, crosscheck_bullet[:400])
# Fix wave 1 (Task 5 diagnosis, ex-018): the prohibition used to be the LAST
# clause of a 60-word sentence, and the measurement caught the writer opening
# "Yes, 166 is the confirmed number of CCTV cameras" from an excerpt against the
# table's own 167. The rule now LEADS with the prohibition, and the ORDER is what
# is pinned - the words were all present when the answer inverted them.
check("it forbids replacing the table figure with an excerpt figure",
      "NEVER replace it with a figure from the cross-check excerpts" in crosscheck_bullet,
      crosscheck_bullet[:400])
check("it forbids opening with a yes/no about an excerpt figure",
      "never open with a yes/no" in crosscheck_bullet, crosscheck_bullet[:400])
check("and the prohibition comes BEFORE the instruction to name the rival figures",
      0 <= crosscheck_bullet.find("NEVER replace") < crosscheck_bullet.find("name each"),
      (crosscheck_bullet.find("NEVER replace"), crosscheck_bullet.find("name each")))

# The heading the loop writes over those excerpts used to over-claim: it said the
# excerpts state the quantity, when they need not mention a quantity at all.
check("sql_loop's cross-check heading no longer claims the excerpts state the "
      "quantity",
      sql_loop.CROSSCHECK_HEADING ==
      "Cross-check: document excerpts that mention this quantity "
      "(top matches, may be unrelated)", sql_loop.CROSSCHECK_HEADING)


# ===========================================================================
# 5. Event sequences - the trajectory pin
# ===========================================================================
print("\n5. Event sequences")

QUESTION = "which panels are recorded on level 3"

GOOD_RESULT = (
    "| panel | level |\n"
    "| --- | --- |\n"
    "| P-1 | 3 |\n"
    "| P-2 | 3 |\n"
    "| P-3 | 3 |\n\n"
    "SQL: `SELECT panel, level FROM \"bld_alpha_units\"`"
)
# Fix wave 1: an empty result now carries its SQL line, exactly as
# `sql_tool.execute_sql_query` really formats one (it appends "SQL: `...`" to every
# result including this message), because the loop now reads that line to tell a
# too-tight filter from a writer that queried no routed table at all and abstained.
EMPTY_RESULT = ("Query returned no results.\n\n"
                "SQL: `SELECT panel, level FROM \"bld_alpha_units\" WHERE level = '9'`")
FAILED_RESULT = "SQL query failed: Binder Error: no such column"

# The card the loop is handed for these runs. It declares the columns GOOD_RESULT
# actually selects, so the inspector sees a routed table behind the SQL (which is what
# tells a too-tight filter from an abstention) and a list that already names its
# entities - the two-step sequence below is about the EVENTS, not about detection.
CARD_ROUTED = {"table": "bld_alpha_units", "holds": "one row per panel on a level",
               "columns": ["panel", "level", "source_page"],
               "identifier_column": "panel",
               "keywords": ["panel", "panels", "level", "alpha"]}

CHUNKS = [
    {"file_name": "alpha_manual.md", "content": "Level 3 carries panels P-1, P-2 and P-3."},
    {"file_name": "beta_register.csv", "content": "P-4 is recorded on level 3 as well."},
]


class _FC:
    def __init__(self, name, args):
        self.name, self.args = name, args


class _Part:
    def __init__(self, function_call=None, text=None):
        self.function_call, self.text = function_call, text


class _Content:
    def __init__(self, parts):
        self.parts = parts


class _Cand:
    def __init__(self, parts):
        self.content, self.finish_reason = _Content(parts), None


class _Resp:
    def __init__(self, parts):
        self.candidates, self.usage_metadata = [_Cand(parts)], None


class _Chunk:
    def __init__(self, text):
        self.text = text


class _Models:
    def generate_content_stream(self, **kw):
        return [_Chunk("the answer")]

    def generate_content(self, **kw):
        return _Resp([_Part(text="the answer")])


class _Client:
    def __init__(self):
        self.models = _Models()


def run_stream(*, user_msg, model_question, sql_results, chunks, max_steps,
               routed=(), has_documents=True, supabase=None):
    """Drive stream_response with every external edge faked, and return
    (tool events, the questions execute_sql_query saw, the queries the document
    search saw). `supabase` replaces the empty default fake when a run needs
    loaded tables behind it (section 10)."""
    asked, searched = [], []

    def fake_execute(question, user_id, sb):
        asked.append(question)
        i = min(len(asked) - 1, len(sql_results) - 1)
        scripted = sql_results[i]
        # A scripted BaseException means "this call raises" - `execute_sql_query`
        # really can, because its own try/except starts below the SQL-generation
        # call and below the structured_data fetch.
        if isinstance(scripted, BaseException):
            raise scripted
        return scripted

    def fake_search(search_query, metadata_filter, user_id, supabase_client=None,
                    folder_path=None, scope=None):
        searched.append(search_query)
        return list(chunks)

    def fake_generate(client, **kw):
        return _Resp([_Part(function_call=_FC("query_structured_data",
                                              {"question": model_question}))])

    saved = {
        "client": oc._get_client, "model": oc.get_llm_model,
        "sql_on": oc.get_text_to_sql_enabled, "web_on": oc.get_web_search_enabled,
        "meta": oc.get_metadata_schema, "search": oc._execute_search_documents,
        "gen": llm_usage_mod.generate_with_usage,
        "exec": sql_tool.execute_sql_query,
        "route": getattr(sql_tool, "route_tables", None),
        "steps": os.environ.get("SQL_LOOP_MAX_STEPS"),
    }
    # Cleared BEFORE the run, not only set after it: when stream_response raises,
    # `events` is never assigned, and a stale list from the previous run would
    # let a check about THIS run pass on the last one's events.
    run_stream.last_events = []
    oc._get_client = lambda: _Client()
    oc.get_llm_model = lambda: "fake-model"
    oc.get_text_to_sql_enabled = lambda: True
    oc.get_web_search_enabled = lambda: False
    oc.get_metadata_schema = lambda: settings_mod.DEFAULT_METADATA_SCHEMA
    oc._execute_search_documents = fake_search
    llm_usage_mod.generate_with_usage = fake_generate
    sql_tool.execute_sql_query = fake_execute
    sql_tool.route_tables = lambda *a, **k: list(routed)
    os.environ["SQL_LOOP_MAX_STEPS"] = str(max_steps)
    try:
        events = list(oc.stream_response(
            messages=[{"role": "user", "content": user_msg}],
            user_id="u-1",
            supabase_client=supabase if supabase is not None else _FakeSupabase([]),
            has_documents=has_documents, has_structured_data=True,
            structured_tables=[{"table_name": "bld_alpha_units", "row_count": 3}],
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
        if saved["steps"] is None:
            os.environ.pop("SQL_LOOP_MAX_STEPS", None)
        else:
            os.environ["SQL_LOOP_MAX_STEPS"] = saved["steps"]

    tools = [(k, json.loads(v)) for k, v in events if k in ("tool_start", "tool_done")]
    run_stream.last_events = events
    return tools, asked, searched


# --- 5a. a non-empty result ------------------------------------------------
# TODAY (a17da28, openai_client.py 1197 + 1365-1366):
#   tool_start {"tool": "query_structured_data", "args": {"question": Q}}
#   tool_done  {"tool": "query_structured_data", "detail": "Query executed"}
TODAY_A = [
    ("tool_start", {"tool": "query_structured_data", "args": {"question": QUESTION}}),
    ("tool_done", {"tool": "query_structured_data", "detail": "Query executed"}),
]
got_a, asked_a, searched_a = run_stream(
    user_msg=QUESTION, model_question=QUESTION,
    sql_results=[GOOD_RESULT], chunks=CHUNKS, max_steps=1)
check("5a. max_steps=1, rows came back: the event sequence is today's, "
      "literally", got_a == TODAY_A, got_a)
check("5a. exactly one SQL call", len(asked_a) == 1, asked_a)
check("5a. no document search", searched_a == [], searched_a)

# --- 5b. an empty result, with documents -----------------------------------
# TODAY (openai_client.py 1320-1345):
#   tool_start query_structured_data {"question": Q}
#   tool_done  query_structured_data "No rows - checking documents"  (em dash)
#   tool_start search_documents {"query": doc_query}
#   tool_done  search_documents "Found 2 results"
TODAY_B = [
    ("tool_start", {"tool": "query_structured_data", "args": {"question": QUESTION}}),
    ("tool_done", {"tool": "query_structured_data",
                   "detail": "No rows — checking documents"}),
    ("tool_start", {"tool": "search_documents", "args": {"query": QUESTION}}),
    ("tool_done", {"tool": "search_documents", "detail": "Found 2 results"}),
]
got_b, asked_b, searched_b = run_stream(
    user_msg=QUESTION, model_question=QUESTION,
    sql_results=[EMPTY_RESULT], chunks=CHUNKS, max_steps=1)
check("5b. max_steps=1, no rows + documents: the event sequence is today's, "
      "literally", got_b == TODAY_B, got_b)
check("5b. exactly one SQL call (no re-query at max_steps=1)",
      len(asked_b) == 1, asked_b)
check("5b. exactly one document search, on the user's own wording",
      searched_b == [QUESTION], searched_b)

# --- 5c. a failed query, with documents ------------------------------------
# TODAY (openai_client.py 1347-1363):
#   tool_start query_structured_data {"question": Q}
#   tool_done  query_structured_data "SQL failed, falling back"
#   tool_start search_documents {"query": question}
#   tool_done  search_documents "Found 2 results"
TODAY_C = [
    ("tool_start", {"tool": "query_structured_data", "args": {"question": QUESTION}}),
    ("tool_done", {"tool": "query_structured_data", "detail": "SQL failed, falling back"}),
    ("tool_start", {"tool": "search_documents", "args": {"query": QUESTION}}),
    ("tool_done", {"tool": "search_documents", "detail": "Found 2 results"}),
]
got_c, asked_c, searched_c = run_stream(
    user_msg=QUESTION, model_question=QUESTION,
    sql_results=[FAILED_RESULT], chunks=CHUNKS, max_steps=1)
check("5c. max_steps=1, the query failed + documents: the event sequence is "
      "today's, literally", got_c == TODAY_C, got_c)
check("5c. a failed query is never re-queried", len(asked_c) == 1, asked_c)

# --- 5d. the fallbacks stay gated on has_documents -------------------------
got_d, asked_d, searched_d = run_stream(
    user_msg=QUESTION, model_question=QUESTION,
    sql_results=[EMPTY_RESULT], chunks=CHUNKS, max_steps=1, has_documents=False)
check("5d. no documents: an empty result closes with today's plain "
      "'Query executed' and searches nothing",
      got_d == [("tool_start", {"tool": "query_structured_data",
                                "args": {"question": QUESTION}}),
                ("tool_done", {"tool": "query_structured_data",
                               "detail": "Query executed"})], got_d)
check("5d. and no document search ran", searched_d == [], searched_d)

# --- 5e. the found-count still reports 0 when nothing was found ------------
got_e, _, _ = run_stream(
    user_msg=QUESTION, model_question=QUESTION,
    sql_results=[EMPTY_RESULT], chunks=[], max_steps=1)
check("5e. no chunks: the closing tool_done still names the SQL tool and "
      "reports 0 (today's `tool_name` was only reassigned when chunks "
      "came back)",
      got_e[-1:] == [("tool_done", {"tool": "query_structured_data",
                                    "detail": "Found 0 results"})], got_e[-1:])


# ===========================================================================
# 6. Two steps - the loop actually looping
# ===========================================================================
print("\n6. A two-step investigation emits two tool_start/tool_done pairs")

got_2, asked_2, searched_2 = run_stream(
    user_msg=QUESTION, model_question=QUESTION,
    sql_results=[EMPTY_RESULT, GOOD_RESULT], chunks=CHUNKS, max_steps=3,
    routed=(CARD_ROUTED,))

EXPECTED_2 = [
    ("tool_start", {"tool": "query_structured_data", "args": {"question": QUESTION}}),
    ("tool_done", {"tool": "query_structured_data",
                   "detail": "step 1: 0 rows — EMPTY"}),
    ("tool_start", {"tool": "query_structured_data",
                    "args": {"question": QUESTION, "step": 2, "issue": "EMPTY"}}),
    ("tool_done", {"tool": "query_structured_data", "detail": "step 2: 3 rows"}),
]
check("6. two pairs, the second carrying its step number and the issue that "
      "caused it", got_2 == EXPECTED_2, got_2)
check("6. two SQL calls", len(asked_2) == 2, len(asked_2))
check("6. the second question is the original plus the deterministic "
      "instruction, never a stack of them",
      len(asked_2) == 2 and asked_2[1].startswith(QUESTION)
      and "Investigation step 2" in asked_2[1], asked_2[1:])
check("6. the re-query succeeded, so no document fallback ran",
      searched_2 == [], searched_2)

# The count cross-check, which only exists when the loop is on.
COUNT_Q = "how many panels are recorded on level 3"
got_cc, asked_cc, searched_cc = run_stream(
    user_msg=COUNT_Q, model_question=COUNT_Q,
    sql_results=[GOOD_RESULT], chunks=CHUNKS, max_steps=3)
check("6b. a quantity question cross-checks the documents once, as a "
      "search_documents event marked as a cross-check",
      got_cc == [
          ("tool_start", {"tool": "query_structured_data", "args": {"question": COUNT_Q}}),
          ("tool_done", {"tool": "query_structured_data",
                         "detail": "step 1: 3 rows — COUNT_CROSSCHECK"}),
          ("tool_start", {"tool": "search_documents",
                          "args": {"query": COUNT_Q, "purpose": "cross-check"}}),
          ("tool_done", {"tool": "search_documents", "detail": "Found 2 results"}),
      ], got_cc)
check("6b. the cross-check is ONE retrieval call and no extra SQL",
      len(asked_cc) == 1 and len(searched_cc) == 1, (asked_cc, searched_cc))

# M-1. Retrieval returns up to 12 chunks; `sql_loop._top_excerpts` appends 3.
# Reporting 12 tells the user - and the trace - that twelve records were weighed
# against the table's figure when three were.
MANY = [{"file_name": f"doc{i}.md", "content": f"record {i} prints a count"}
        for i in range(12)]
got_m1, _, _ = run_stream(
    user_msg=COUNT_Q, model_question=COUNT_Q,
    sql_results=[GOOD_RESULT], chunks=MANY, max_steps=3)
check("6c. the cross-check reports the excerpts APPENDED (3), not the chunks "
      "retrieved (12)",
      got_m1[-1:] == [("tool_done", {"tool": "search_documents",
                                     "detail": "Found 3 results"})], got_m1[-1:])

# M-2. A user-visible string. "1 rows" is the kind of detail that makes a
# careful reader distrust the numbers next to it.
ONE_ROW = ("| panel | level |\n| --- | --- |\n| P-1 | 3 |\n\n"
           "SQL: `SELECT panel, level FROM \"bld_alpha_units\"`")
got_m2, _, _ = run_stream(
    user_msg=COUNT_Q, model_question=COUNT_Q,
    sql_results=[ONE_ROW], chunks=CHUNKS, max_steps=3)
check("6d. one row is reported as '1 row', not '1 rows'",
      got_m2[1:2] == [("tool_done", {"tool": "query_structured_data",
                                     "detail": "step 1: 1 row — COUNT_CROSSCHECK"})],
      got_m2[1:2])


# ===========================================================================
# 7. The one intentional divergence, pinned rather than hidden
# ===========================================================================
print("\n7. A failed query now searches the user's own wording")

PARAPHRASE = "panels by level"
got_7, asked_7, searched_7 = run_stream(
    user_msg=QUESTION, model_question=PARAPHRASE,
    sql_results=[FAILED_RESULT], chunks=CHUNKS, max_steps=1)
check("7. the SQL question still carries BOTH the user's wording and the "
      "router's paraphrase (the pre-existing augmentation)",
      len(asked_7) == 1 and asked_7[0].startswith(QUESTION)
      and PARAPHRASE in asked_7[0], asked_7)
check("7. but the document search behind the failure runs on the user's own "
      "wording, not the augmented text - today it used the augmented text; "
      "the EMPTY fallback already behaved this way and now both do",
      searched_7 == [QUESTION], searched_7)
check("7. the dispatcher's tool_start still carries the model's own args, "
      "untouched by the augmentation",
      got_7[:1] == [("tool_start", {"tool": "query_structured_data",
                                    "args": {"question": PARAPHRASE}})], got_7[:1])


# ===========================================================================
# 8. I-1 — a re-query that RAISES must not destroy an answer today would give
# ===========================================================================
print("\n8. A raising re-query falls back to the documents, never to an error")

# `execute_sql_query` catches DuckDB failures and returns "SQL query failed: ...",
# but its try/except starts BELOW the SQL-generation call and BELOW the
# structured_data fetch - so a Gemini 429/503 or a PostgREST error raises out of
# it. Today that could happen once per question and the outer dispatch handled
# it; with the loop it can happen on step 2 or 3, on a question step 1 had
# already sent to the document fallback. Unguarded, the user gets an SSE error
# where today they got an answer, and the AFTER arm of the paid run counts it as
# a quality change.
BOOM = RuntimeError("duckdb blew up on step 2")

raised_out = None
try:
    got_8, asked_8, searched_8 = run_stream(
        user_msg=QUESTION, model_question=QUESTION,
        sql_results=[EMPTY_RESULT, BOOM], chunks=CHUNKS, max_steps=3,
        routed=(CARD_ROUTED,))
except BaseException as e:  # noqa: BLE001 - not propagating IS the assertion
    raised_out, got_8, asked_8, searched_8 = e, [], [], []

check("8. the exception does not escape stream_response", raised_out is None,
      repr(raised_out))
# 2026-09-28: a failed step is now a RE-QUERY issue (`sql_loop.FAILED_SQL`), not a
# terminal one, so the raise on step 2 is re-queried once before the fallback. The
# subject of this section is unchanged and still pinned above - the exception must
# never reach the user as an SSE error - and the run still ENDS in the document
# fallback; it just gets there one step later. The step budget still bounds it.
check("8. three SQL calls were made (step 2 raised, step 3 is the re-query)",
      len(asked_8) == 3, len(asked_8))
check("8. the run still ends through the FAILED document fallback, one "
      "re-query later than it used to",
      got_8 == [
          ("tool_start", {"tool": "query_structured_data", "args": {"question": QUESTION}}),
          ("tool_done", {"tool": "query_structured_data",
                         "detail": "step 1: 0 rows — EMPTY"}),
          ("tool_start", {"tool": "query_structured_data",
                          "args": {"question": QUESTION, "step": 2, "issue": "EMPTY"}}),
          ("tool_done", {"tool": "query_structured_data",
                         "detail": "step 2: 0 rows — FAILED_SQL"}),
          ("tool_start", {"tool": "query_structured_data",
                          "args": {"question": QUESTION, "step": 3,
                                   "issue": "FAILED_SQL"}}),
          ("tool_done", {"tool": "query_structured_data",
                         "detail": "SQL failed, falling back"}),
          ("tool_start", {"tool": "search_documents", "args": {"query": QUESTION}}),
          ("tool_done", {"tool": "search_documents", "detail": "Found 2 results"}),
      ], got_8)
check("8. the documents were searched, on the user's own wording",
      searched_8 == [QUESTION], searched_8)
check("8. the stream still closes with a done event",
      getattr(run_stream, "last_events", [])[-1:] == [("done", "")],
      getattr(run_stream, "last_events", [])[-1:])
check("8. and the answer writer is handed the document excerpts, not a "
       "traceback",
      any(k == "token" for k, _ in getattr(run_stream, "last_events", [])),
      [k for k, _ in getattr(run_stream, "last_events", [])])

# Step 1 stays unguarded: the outer dispatch has always handled it, and guarding
# it would change the max_steps=1 identity the whole of section 5 pins.
raised_1 = None
try:
    run_stream(user_msg=QUESTION, model_question=QUESTION,
               sql_results=[BOOM], chunks=CHUNKS, max_steps=3)
except BaseException as e:  # noqa: BLE001
    raised_1 = e
check("8. a step-1 exception still propagates to the dispatch, exactly as "
      "today (guarding it would move the max_steps=1 behaviour)",
      raised_1 is BOOM, repr(raised_1))


# ===========================================================================
# 9. spec-fix7(b): a step-2 query is routed on the step-1 selection plus the
#    step text, and no step-1 table is dropped
# ===========================================================================
print("\n9. A step-2 query is routed on the step-1 selection plus the step "
      "text, and no step-1 table is dropped")

# CARD_STEP1 is what the plain, un-suffixed question is about. CARD_F1/F2/F3
# match nothing in the plain question, but each shares an identifier prefix
# with a coded value quoted inside the PREVIOUS SQL that `step_instruction_
# suffix` embeds in step 2's question text - exactly how a re-query's own
# quoted SQL out-scores the step-1 selection in the live cards (task-7-
# diagnosis.md).
CARD_STEP1 = {"table": "bld_widget_roster", "holds": "one row per widget",
              "columns": ["widget_tag", "roster_position", "source_page"],
              "identifier_column": "widget_tag",
              "keywords": ["widget", "widgets", "roster"]}
CARD_F1 = {"table": "bld_alpha_extra", "holds": "one row per extra alpha record",
           "columns": ["notes"], "identifier_column": "notes",
           "identifier_prefixes": ["ALP"]}
CARD_F2 = {"table": "bld_beta_extra", "holds": "one row per extra beta record",
           "columns": ["notes"], "identifier_column": "notes",
           "identifier_prefixes": ["BET"]}
CARD_F3 = {"table": "bld_gamma_extra", "holds": "one row per extra gamma record",
           "columns": ["notes"], "identifier_column": "notes",
           "identifier_prefixes": ["GAM"]}
STEP7_CARDS = [CARD_STEP1, CARD_F1, CARD_F2, CARD_F3]

BASE_Q7 = "list the widgets on the roster"
PREVIOUS_SQL_7 = ("SELECT * FROM \"bld_widget_roster\" WHERE tag = 'ALP-1' "
                  "OR tag = 'BET-2' OR tag = 'GAM-3'")
issue_7 = sql_loop.Issue(kind="EMPTY", detail="0 rows",
                         instruction="Re-check the roster filter.")
suffix_7 = sql_loop.step_instruction_suffix(2, issue_7, PREVIOUS_SQL_7)
FULL_Q7 = BASE_Q7 + suffix_7
check("fixture sanity: the suffix carries the loop's own marker",
      "(Investigation step 2:" in suffix_7, suffix_7)

_reset_cards_cache()
step1_routed = sql_tool.route_tables(BASE_Q7, "u-1", _FakeSupabase(STEP7_CARDS))
step1_names = [c["table"] for c in step1_routed]
check("9. sanity: the plain, un-suffixed question routes to its own table alone",
      step1_names == ["bld_widget_roster"], step1_names)

# Without the union fix, routing on the full re-query text ALONE drops
# bld_widget_roster entirely - the quoted previous SQL's coded values outscore
# the step-1 words and push it out of the ranked top-3. This is the defect
# spec-fix7(b) exists to fix.
_reset_cards_cache()
unfixed_full = sql_tool.select_tables(FULL_Q7, STEP7_CARDS, k=3)
check("fixture sanity: the full re-query text ALONE would drop the step-1 "
      "table (the defect spec-fix7(b) fixes)",
      "bld_widget_roster" not in unfixed_full, unfixed_full)

_reset_cards_cache()
step2_routed = sql_tool.route_tables(FULL_Q7, "u-1", _FakeSupabase(STEP7_CARDS))
step2_names = [c["table"] for c in step2_routed]
check("9. the step-2 (suffixed) question keeps every step-1 table - none is "
      "ever dropped",
      set(step1_names) <= set(step2_names), (step1_names, step2_names))
check("9. and it also carries the table(s) the step text alone points at",
      {"bld_alpha_extra", "bld_beta_extra", "bld_gamma_extra"} <= set(step2_names),
      step2_names)

# `route_tables`/`execute_sql_query` immediately reduce the selection to a SET
# for membership testing against an already (alphabetically) ordered card/table
# list, so the union's own order never survives either public entry point -
# that re-ordering predates this fix and is not its job to change. The "step-1
# selection first" contract is the shared helper's own, and is pinned there
# directly.
check("9. the shared helper itself is deterministic and ORDERED - the step-1 "
      "selection comes first, then any newly-added table",
      sql_tool._routed_table_names(FULL_Q7, STEP7_CARDS, k=3)
      == ["bld_widget_roster", "bld_alpha_extra", "bld_beta_extra", "bld_gamma_extra"],
      sql_tool._routed_table_names(FULL_Q7, STEP7_CARDS, k=3))
_reset_cards_cache()


# ===========================================================================
# 10. spec-fix7(c): an EMPTY re-query is handed the REAL values of the columns
#     the failed WHERE filtered, read from the user's loaded tables
# ===========================================================================
print("\n10. The EMPTY re-query carries the filtered columns' real values, read "
      "from the loaded tables")

# `sql_loop` asks for values through an injected `column_values(table, column)`;
# `sql_tool.column_value_reader` is the one the app hands it. The load-bearing
# property is that it reads the table AS THE EXECUTOR LOADS IT - the same rows,
# the same majority-vote column types, the same cell conversion - because a value
# the writer is told to filter with must be one DuckDB will actually match.


class _TablesSupabase:
    """Serves `structured_data` rows, honouring `.eq()` filters (the plain `_Query`
    ignores them), and records every fetch so a check can prove when the database
    is - and is not - read."""

    def __init__(self, tables, raises=None):
        self.tables = tables
        self.raises = raises
        self.fetches = []

    def table(self, name):
        outer = self

        class _Q:
            def __init__(self):
                self.filters = {}

            def select(self, *a, **kw):
                return self

            def eq(self, col, val):
                self.filters[col] = val
                return self

            def order(self, *a, **kw):
                return self

            def execute(self):
                if name != "structured_data":
                    return _ExecResult([])
                outer.fetches.append(dict(self.filters))
                if outer.raises is not None:
                    raise outer.raises
                return _ExecResult([t for t in outer.tables
                                    if all(t.get(k) == v for k, v in self.filters.items()
                                           if k != "user_id")])

        return _Q()


MIXED = {
    "table_name": "bld_mixed_values",
    "columns": ["tag", "shade", "amount", "code"],
    "rows": [
        {"tag": "mv1", "shade": "Teal", "amount": "9,431", "code": "08642"},
        {"tag": "mv2", "shade": "Ochre", "amount": "35", "code": "08642"},
        {"tag": "mv3", "shade": "Teal ", "amount": "not given", "code": "09753"},
        {"tag": "mv4", "shade": "", "amount": "35", "code": None},
        {"tag": "mv5", "shade": None, "amount": "7.5", "code": "09753"},
        {"tag": "mv6", "shade": "Ochre", "amount": 12, "code": "08642"},
    ],
    "row_count": 6,
}
WIRE_UNITS = {
    "table_name": "bld_alpha_units",
    "columns": ["panel", "level", "source_page"],
    "rows": [{"panel": "P-1", "level": "Deck North", "source_page": "4"},
             {"panel": "P-2", "level": "Deck South", "source_page": "4"},
             {"panel": "P-3", "level": "Deck North", "source_page": "5"}],
    "row_count": 3,
}

check("sql_tool.column_value_reader exists", hasattr(sql_tool, "column_value_reader"))
if hasattr(sql_tool, "column_value_reader"):
    # --- 10a. what it returns, and when it reads -----------------------------
    sb10 = _TablesSupabase([WIRE_UNITS, MIXED])
    reader10 = sql_tool.column_value_reader("u-1", sb10)
    check("10a. building the reader reads nothing - the loop may never need it",
          sb10.fetches == [], sb10.fetches)
    check("10a. a text column: its distinct values, first appearance first, exactly as "
          "stored (a trailing space is a different value; a blank is kept for the "
          "loop to drop)",
          reader10("bld_mixed_values", "shade") == ["Teal", "Ochre", "Teal ", ""],
          reader10("bld_mixed_values", "shade"))
    check("10a. a numeric column: the values DuckDB holds, as the result table prints "
          "them (a comma is read through, text that is not a number is NULL)",
          reader10("bld_mixed_values", "amount") == ["9431.0", "35.0", "7.5", "12.0"],
          reader10("bld_mixed_values", "amount"))
    check("10a. a leading-zero code stays text, zero and all",
          reader10("bld_mixed_values", "code") == ["08642", "09753"],
          reader10("bld_mixed_values", "code"))
    check("10a. column names match regardless of case, as DuckDB's do",
          reader10("bld_mixed_values", "SHADE") == reader10("bld_mixed_values", "shade"))
    check("10a. one fetch per table, filtered to this user and this table",
          sb10.fetches == [{"user_id": "u-1", "table_name": "bld_mixed_values"}],
          sb10.fetches)
    check("10a. a column the table does not have -> None",
          reader10("bld_mixed_values", "colour") is None)
    check("10a. a table the user does not have -> None",
          reader10("bld_nowhere", "shade") is None)
    reader10("bld_alpha_units", "level")
    check("10a. a second table is a second fetch, and a repeat read is not",
          len(sb10.fetches) == 3, sb10.fetches)
    boom10 = sql_tool.column_value_reader(
        "u-1", _TablesSupabase([MIXED], raises=RuntimeError("postgrest down")))
    raised10 = None
    try:
        got10 = boom10("bld_mixed_values", "shade")
    except Exception as e:  # noqa: BLE001 - not raising IS the assertion
        raised10, got10 = e, "raised"
    check("10a. a fetch that raises -> None, never an exception", raised10 is None
          and got10 is None, repr(raised10))
    # The plain `_Query` ignores filters and serves every table: the reader must pick
    # the row that IS the table it was asked for, never the first row it is handed.
    class _LooseSupabase:
        def table(self, name):
            return _Query([WIRE_UNITS, MIXED] if name == "structured_data" else [])

    loose = sql_tool.column_value_reader("u-1", _LooseSupabase())
    check("10a. handed every table at once, it still reads only the one it was asked for",
          loose("bld_mixed_values", "shade") == ["Teal", "Ochre", "Teal ", ""],
          loose("bld_mixed_values", "shade"))

    # --- 10b. the values ARE the loaded table's: DuckDB's own DISTINCT agrees ---
    # Drive `execute_sql_query` itself - the real loader - with a fake SQL writer that
    # asks DuckDB for the distinct values of one column, and compare.
    class _DistinctModels:
        sql = ""

        def generate_content(self, *, model, contents, config=None):
            return type("_R", (), {"text": _DistinctModels.sql, "usage_metadata": None})()

    class _DistinctClient:
        def __init__(self, *a, **kw):
            self.models = _DistinctModels()

    saved10 = (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
               sql_tool._load_table_cards)
    sql_tool.genai.Client = _DistinctClient
    sql_tool.get_llm_api_key = lambda: "fake-key"
    sql_tool.get_llm_model = lambda: "fake-model"
    sql_tool._load_table_cards = lambda *a, **k: []
    try:
        for col in ("shade", "amount", "code"):
            _DistinctModels.sql = f'SELECT DISTINCT "{col}" FROM "bld_mixed_values"'
            out = sql_tool.execute_sql_query("q", "u-1", _TablesSupabase([MIXED]))
            cells = [l.strip().strip("|").strip() for l in out.splitlines()
                     if l.strip().startswith("|")][2:]
            duck = sorted(c for c in cells if c)
            mine = sorted(v.strip() for v in sql_tool.column_value_reader(
                "u-1", _TablesSupabase([MIXED]))("bld_mixed_values", col) if v.strip())
            check(f"10b. {col}: the reader's values are exactly DuckDB's SELECT DISTINCT "
                  f"over the table as execute_sql_query loads it", duck == mine,
                  (duck, mine))
    finally:
        (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
         sql_tool._load_table_cards) = saved10

# --- 10c. the branch hands the reader to the investigation ---------------------
branch10 = _branch_source()
check("10c. the branch hands the investigation a reader built for this user's tables",
      "column_values=column_value_reader(user_id, supabase_client)" in branch10,
      [l.strip() for l in branch10.splitlines() if "column_value" in l])

# --- 10d. end to end: the values reach the step-2 question, and nothing else moves
sb10d = _TablesSupabase([WIRE_UNITS])
got_10, asked_10, searched_10 = run_stream(
    user_msg=QUESTION, model_question=QUESTION,
    sql_results=[EMPTY_RESULT, GOOD_RESULT], chunks=CHUNKS, max_steps=3,
    routed=(CARD_ROUTED,), supabase=sb10d)
check("10d. the event sequence is section 6's exactly - the values change the "
      "question, never the trajectory", got_10 == EXPECTED_2, got_10)
check("10d. the step-2 question lists the filtered column's real values",
      len(asked_10) == 2 and '"bld_alpha_units"."level" holds exactly these 2 values: '
      "'Deck North', 'Deck South'" in asked_10[1], asked_10[-1:])
check("10d. and the table was read once, for the one column the WHERE filtered",
      sb10d.fetches == [{"user_id": "u-1", "table_name": "bld_alpha_units"}], sb10d.fetches)

sb10e = _TablesSupabase([WIRE_UNITS])
got_10e, asked_10e, _ = run_stream(
    user_msg=QUESTION, model_question=QUESTION,
    sql_results=[EMPTY_RESULT], chunks=CHUNKS, max_steps=1,
    routed=(CARD_ROUTED,), supabase=sb10e)
check("10e. max_steps=1 with loaded tables behind it: today's sequence, literally",
      got_10e == TODAY_B, got_10e)
check("10e. and the reader never went to the database", sb10e.fetches == [], sb10e.fetches)

sb10f = _TablesSupabase([WIRE_UNITS], raises=RuntimeError("postgrest down"))
got_10f, asked_10f, _ = run_stream(
    user_msg=QUESTION, model_question=QUESTION,
    sql_results=[EMPTY_RESULT, GOOD_RESULT], chunks=CHUNKS, max_steps=3,
    routed=(CARD_ROUTED,), supabase=sb10f)
check("10f. a reader whose fetch fails costs nothing: same trajectory, and the "
      "re-query still carries today's advice",
      got_10f == EXPECTED_2 and len(asked_10f) == 2
      and "Read the column samples again" in asked_10f[1], (got_10f, asked_10f[-1:]))

# --- 10g. spec-fix3(c), T11a-R6: the reader also lists a LOADED table's columns ---------
# The stamped level code is on the loaded tables and never on the cards, so the loop asks
# the reader which columns a table really has - to place a filtered column no card lists,
# and to know whether the table has a level code to re-express a storey filter through.
sb10g = _TablesSupabase([WIRE_UNITS, MIXED])
reader10g = sql_tool.column_value_reader("u-1", sb10g)
cols10g = getattr(reader10g, "columns", None)
check("10g. the reader can list a loaded table's columns", callable(cols10g))
if callable(cols10g):
    check("10g. building it reads nothing", sb10g.fetches == [], sb10g.fetches)
    check("10g. the loaded table's own columns, in its own order",
          cols10g("bld_alpha_units") == ["panel", "level", "source_page"],
          cols10g("bld_alpha_units"))
    reader10g("bld_alpha_units", "level")
    check("10g. one fetch serves the column list and the values alike",
          sb10g.fetches == [{"user_id": "u-1", "table_name": "bld_alpha_units"}], sb10g.fetches)
    check("10g. a table the user does not have -> None", cols10g("bld_nowhere") is None)
    boom10g = sql_tool.column_value_reader(
        "u-1", _TablesSupabase([MIXED], raises=RuntimeError("postgrest down")))
    raised10g = None
    try:
        got10g = boom10g.columns("bld_mixed_values")
    except Exception as e:  # noqa: BLE001 - not raising IS the assertion
        raised10g, got10g = e, "raised"
    check("10g. a fetch that raises -> None, never an exception",
          raised10g is None and got10g is None, repr(raised10g))

# End to end through the real branch: the same empty storey filter, on a loaded table that
# ALSO carries a level code its card does not list, re-queries through the level code and
# lists no printed storey values; 10d above, on the same table WITHOUT one, keeps today's
# advice and its values.
WIRE_CODED = dict(WIRE_UNITS, columns=WIRE_UNITS["columns"] + ["level_code"],
                  rows=[dict(r, level_code=c) for r, c in zip(WIRE_UNITS["rows"],
                                                              ("Z3", "Z4", "Z3"))])
sb10h = _TablesSupabase([WIRE_CODED])
got_10h, asked_10h, _ = run_stream(
    user_msg=QUESTION, model_question=QUESTION,
    sql_results=[EMPTY_RESULT, GOOD_RESULT], chunks=CHUNKS, max_steps=3,
    routed=(CARD_ROUTED,), supabase=sb10h)
check("10h. the event sequence is section 6's exactly", got_10h == EXPECTED_2, got_10h)
check("10h. the step-2 question re-expresses the storey through the level code, keeps it, "
      "and never widens it - and, the storey being its only filter, an empty storey is the "
      "answer",
      len(asked_10h) == 2 and "level code column" in asked_10h[1]
      and "never widen" in asked_10h[1]
      and "that storey has no such rows, and that is the answer" in asked_10h[1], asked_10h[-1:])
check("10h. and lists no printed storey value - the table was read once, for its columns",
      len(asked_10h) == 2 and "Deck North" not in asked_10h[1]
      and sb10h.fetches == [{"user_id": "u-1", "table_name": "bld_alpha_units"}],
      (asked_10h[-1:], sb10h.fetches))


# ===========================================================================
# 11. spec-fix7(c), review fix 1: the listed values reach the SQL WRITER and
#     are never ROUTED on
# ===========================================================================
print("\n11. A re-query's listed values reach the SQL writer, never the router")

# Measured on the recorded re-queries (task-T3-report.md): the values clause is
# the data's own words and hyphenated codes, and the union router read it like
# any other step text - the mean re-query route grew from 12.7 tables to 16.9,
# mostly tables that merely print the same words. The values come from a table
# the failed query ALREADY read, so routing on them can only ever add noise. The
# fix cuts the clause out of the ROUTED text only, from its fixed lead-in up to
# the " Previous SQL:" the step suffix writes after every instruction; the writer
# is still handed the whole question.
#
# CARD_ZQX shares no word with the base question, the advice or the SQL; the one
# thing that can route it is the prefix of the hyphenated values listed below.
CARD_SHELF = {"table": "bld_shelf_roster", "holds": "the shelves on the roster",
              "columns": ["shelf_tag", "linked_item", "source_page"],
              "identifier_column": "shelf_tag"}
CARD_ZQX = {"table": "zqx_registry", "holds": "the zqx registry",
            "columns": ["zqx_code", "remark_text"], "identifier_column": "zqx_code",
            "identifier_prefixes": ["ZQX"]}
CARDS11 = [CARD_SHELF, CARD_ZQX]
BASE11 = "list the shelves on the roster"
SQL11 = "SELECT shelf_tag FROM \"bld_shelf_roster\" WHERE linked_item = 'absent item'"


def _values11(table, column):
    return ["ZQX-01", "ZQX-02"] if (table, column) == ("bld_shelf_roster", "linked_item") else None


VALUES11 = sql_loop._filtered_values_text(SQL11, CARDS11, _values11)
PLAIN_SUFFIX11 = sql_loop.step_instruction_suffix(2, sql_loop._empty_issue(SQL11), SQL11)
VALUES_SUFFIX11 = sql_loop.step_instruction_suffix(2, sql_loop._empty_issue(SQL11, VALUES11),
                                                   SQL11)
PLAIN11, WITH_VALUES11 = BASE11 + PLAIN_SUFFIX11, BASE11 + VALUES_SUFFIX11

_lead = getattr(sql_loop, "VALUES_LEAD", None)
_marker = getattr(sql_loop, "PREVIOUS_SQL_MARKER", None)
check("11. the clause's lead-in is one shared constant, and the clause opens with it",
      _lead is not None and VALUES11.startswith(_lead), (_lead, VALUES11[:60]))
check("11. so is the marker the step suffix writes after every instruction",
      _marker is not None and f"{_marker} `{SQL11}`)" in VALUES_SUFFIX11,
      (_marker, VALUES_SUFFIX11[-80:]))
check("11. sql_tool reads the SAME constants, so the two files cannot drift",
      getattr(sql_tool, "VALUES_LEAD", 0) is _lead
      and getattr(sql_tool, "PREVIOUS_SQL_MARKER", 0) is _marker)
check("fixture sanity: the step text carries a hyphenated value whose prefix a card declares",
      "'ZQX-01'" in WITH_VALUES11 and "ZQX" not in PLAIN11, VALUES11)
check("fixture sanity: routed on as it stands, the values clause pulls that card in",
      "zqx_registry" in sql_tool.select_tables(WITH_VALUES11, CARDS11, k=3)
      and "zqx_registry" not in sql_tool.select_tables(PLAIN11, CARDS11, k=3),
      (sql_tool.select_tables(WITH_VALUES11, CARDS11, k=3),
       sql_tool.select_tables(PLAIN11, CARDS11, k=3)))

check("11. a step text carrying a values clause routes EXACTLY like the same step "
      "text without it",
      sql_tool._routed_table_names(WITH_VALUES11, CARDS11, k=3)
      == sql_tool._routed_table_names(PLAIN11, CARDS11, k=3) == ["bld_shelf_roster"],
      (sql_tool._routed_table_names(WITH_VALUES11, CARDS11, k=3),
       sql_tool._routed_table_names(PLAIN11, CARDS11, k=3)))
_cut = getattr(sql_tool, "_without_values_clause", None)
check("11. because the text it routes is byte for byte the text without the clause",
      _cut is not None and _cut(VALUES_SUFFIX11) == PLAIN_SUFFIX11,
      _cut(VALUES_SUFFIX11) if _cut else "no _without_values_clause")
check("11. a text with no lead-in comes back unchanged, so its routing is today's",
      _cut is not None and _cut(PLAIN_SUFFIX11) == PLAIN_SUFFIX11
      and _cut(BASE11) == BASE11 and _cut("") == "")
check("11. a clause with no marker after it is cut to the end - a value is never routed",
      _cut is not None and _cut("advice. " + VALUES11) == "advice.",
      _cut("advice. " + VALUES11) if _cut else "-")
check("11. a question with no step suffix routes exactly as before, lead-in or not",
      sql_tool._routed_table_names(BASE11 + " " + VALUES11, CARDS11, k=3)
      == sql_tool.select_tables(BASE11 + " " + VALUES11, CARDS11, k=3))
_reset_cards_cache()
via_route_tables = [c["table"] for c in sql_tool.route_tables(
    WITH_VALUES11, "u-1", _FakeSupabase(CARDS11))]
_reset_cards_cache()
check("11. the public entry point agrees: route_tables hands back the step-1 card alone",
      via_route_tables == ["bld_shelf_roster"], via_route_tables)


# And the SQL WRITER still gets the whole question, values and all - driven
# through `execute_sql_query` itself with a fake writer that keeps its prompt.
class _PromptModels:
    prompts = []

    def generate_content(self, *, model, contents, config=None):
        _PromptModels.prompts.append(contents)
        return type("_R", (), {"text": 'SELECT shelf_tag FROM "bld_shelf_roster"',
                               "usage_metadata": None})()


class _PromptClient:
    def __init__(self, *a, **kw):
        self.models = _PromptModels()


TABLES11 = [
    {"table_name": "bld_shelf_roster", "columns": ["shelf_tag", "linked_item", "source_page"],
     "rows": [{"shelf_tag": "sh1", "linked_item": "ZQX-01", "source_page": "2"}], "row_count": 1},
    {"table_name": "zqx_registry", "columns": ["zqx_code", "remark_text"],
     "rows": [{"zqx_code": "ZQX-01", "remark_text": "zqx remark"}], "row_count": 1},
]
saved11 = (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
           sql_tool._load_table_cards)
sql_tool.genai.Client = _PromptClient
sql_tool.get_llm_api_key = lambda: "fake-key"
sql_tool.get_llm_model = lambda: "fake-model"
sql_tool._load_table_cards = lambda *a, **k: CARDS11
try:
    _PromptModels.prompts = []
    sql_tool.execute_sql_query(WITH_VALUES11, "u-1", _TablesSupabase(TABLES11))
finally:
    (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
     sql_tool._load_table_cards) = saved11
prompt11 = _PromptModels.prompts[0] if _PromptModels.prompts else ""
check("11. the SQL writer's prompt still carries the whole question, values clause and all",
      ("User question: " + WITH_VALUES11) in prompt11, prompt11[-600:])
check("11. while the schema it is shown is the routed one: the step-1 table, and not "
      "the table only the values pointed at",
      "Table: bld_shelf_roster" in prompt11 and "Table: zqx_registry" not in prompt11,
      [l for l in prompt11.splitlines() if l.startswith("Table: ")])


# ===========================================================================
# 12. T7 (2026-10-01) — a contact/warranty question cross-checks exactly like a
#     count question: appended, under its own heading, never "falling back"
# ===========================================================================
print("\n12. A letter-shaped (contact/warranty) question cross-checks the documents "
      "once, as a search_documents event marked as a cross-check")

LETTER_Q = "who is the contact for the units, and what is their phone number"
got_lc, asked_lc, searched_lc = run_stream(
    user_msg=LETTER_Q, model_question=LETTER_Q,
    sql_results=[GOOD_RESULT], chunks=CHUNKS, max_steps=3)
check("12. step, step_done (naming LETTER_CROSSCHECK), crosscheck tool_start/tool_done",
      got_lc == [
          ("tool_start", {"tool": "query_structured_data", "args": {"question": LETTER_Q}}),
          ("tool_done", {"tool": "query_structured_data",
                         "detail": "step 1: 3 rows — LETTER_CROSSCHECK"}),
          ("tool_start", {"tool": "search_documents",
                          "args": {"query": LETTER_Q, "purpose": "cross-check"}}),
          ("tool_done", {"tool": "search_documents", "detail": "Found 2 results"}),
      ], got_lc)
check("12. the cross-check is ONE retrieval call and no extra SQL",
      len(asked_lc) == 1 and len(searched_lc) == 1, (asked_lc, searched_lc))
check("12. never reported as a fallback: no 'falling back' or 'SQL failed' anywhere "
      "in the trajectory",
      not any("falling back" in str(p).lower() or "sql failed" in str(p).lower()
              for _, p in got_lc), got_lc)

WARRANTY_Q = "is the unit still under warranty"
got_w, asked_w, searched_w = run_stream(
    user_msg=WARRANTY_Q, model_question=WARRANTY_Q,
    sql_results=[GOOD_RESULT], chunks=CHUNKS, max_steps=3)
check("12b. a warranty question gets the identical treatment as the who/phone one",
      got_w == [
          ("tool_start", {"tool": "query_structured_data", "args": {"question": WARRANTY_Q}}),
          ("tool_done", {"tool": "query_structured_data",
                         "detail": "step 1: 3 rows — LETTER_CROSSCHECK"}),
          ("tool_start", {"tool": "search_documents",
                          "args": {"query": WARRANTY_Q, "purpose": "cross-check"}}),
          ("tool_done", {"tool": "search_documents", "detail": "Found 2 results"}),
      ], got_w)

# T7-R1: a question that is BOTH count-shaped and letter-shaped gets the count
# cross-check only — today's heading, byte-identical.
COUNT_WARRANTY_Q = "how many units are still under warranty"
got_cw, asked_cw, searched_cw = run_stream(
    user_msg=COUNT_WARRANTY_Q, model_question=COUNT_WARRANTY_Q,
    sql_results=[GOOD_RESULT], chunks=CHUNKS, max_steps=3)
check("12c. T7-R1: a both-shaped question gets COUNT_CROSSCHECK, never LETTER_CROSSCHECK",
      got_cw == [
          ("tool_start", {"tool": "query_structured_data",
                          "args": {"question": COUNT_WARRANTY_Q}}),
          ("tool_done", {"tool": "query_structured_data",
                         "detail": "step 1: 3 rows — COUNT_CROSSCHECK"}),
          ("tool_start", {"tool": "search_documents",
                          "args": {"query": COUNT_WARRANTY_Q, "purpose": "cross-check"}}),
          ("tool_done", {"tool": "search_documents", "detail": "Found 2 results"}),
      ], got_cw)

# T7-R3: max_steps=1 is unchanged — no cross-check of any kind.
got_lc1, asked_lc1, searched_lc1 = run_stream(
    user_msg=LETTER_Q, model_question=LETTER_Q,
    sql_results=[GOOD_RESULT], chunks=CHUNKS, max_steps=1)
check("12d. T7-R3: max_steps=1, a letter question is unchanged — today's plain sequence",
      got_lc1 == [
          ("tool_start", {"tool": "query_structured_data", "args": {"question": LETTER_Q}}),
          ("tool_done", {"tool": "query_structured_data", "detail": "Query executed"}),
      ], got_lc1)
check("12d. no document search ran", searched_lc1 == [], searched_lc1)


print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
