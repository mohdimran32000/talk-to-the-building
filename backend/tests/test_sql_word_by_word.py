"""test_sql_word_by_word.py - a phrase matched with ILIKE '%...%' that found nothing is re-run once,
in code, with each of its words matched on its own (wave 3, G3, 2026-10-03).

THE DEFECT. A question named a place by a phrase of several words, and the SQL writer matched the
whole phrase: `area ILIKE '%Kiln Glaze%'`. The table prints the same words in another order, with
other words between them - 'Raku Glaze Kiln' - so nothing matched. A second empty result ends the
loop (the repeat guard), and the question fell to the documents, which missed part of the answer.
Matched word by word, the same query returns exactly the rows asked about.

THE FIX, in code, inside the executor, before an empty result is reported - so before any model
re-query: when a query's result holds nothing (no rows, or only NULLs, blanks or a lone zero) and a
WHERE clause of it - at any depth, under an OR or inside a sub-SELECT - holds a plain `col ILIKE
'%...%'` whose phrase has two or more words of three or more characters, the same SQL is re-run once
with each such condition written as one ILIKE per word, ANDed on the same column:

    (col ILIKE '%Kiln%' AND col ILIKE '%Glaze%')

If the re-run returns something, it becomes the result - its SQL is the result's SQL line - and one
line says how the rows were found:

    MATCHED WORD BY WORD - no row has <col> ILIKE '<phrase pattern>'; the rows above were found with
    <col> holding each of its words ('Kiln' and 'Glaze'), in any order and with other words between

A single-word phrase is never touched, nor is LIKE, a pattern without a wildcard at both ends, or a
result that already holds something. A re-run that still holds nothing leaves the result exactly as
it was; so does an error.

FIX ROUND 1 (review of the wave, ruling W3A2-R2). Word by word is a looser match than the phrase, and
a phrase that missed only on its formatting retried to rows across many different printed places. So:
a word is a run of letters and digits of TWO or more characters (a short code such as a room
abbreviation counts; punctuation is no word); a phrase is matched word by word only when at most
THREE printed values of its column hold every one of its words - more, and the empty result stands,
for the loop's EMPTY re-query, which lists the column's real values; and the line names the printed
values the words matched (section 4).

Every table, column and value here is invented. Every check marked RED fails against the code as it
stood before this change (sections 1-3: before G3; section 4 and the round-1 line: before fix round
1).

Run:
    venv/Scripts/python -X utf8 tests/test_sql_word_by_word.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import sql_loop, sql_tool  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


def _row(rig, shed, spot, draw):
    return {"rig": rig, "shed": shed, "spot_printed": spot, "draw_w": draw}


KILNS = {
    "table_name": "bld_kilns",
    "columns": ["rig", "shed", "spot_printed", "draw_w"],
    "rows": [
        _row("RG-1", "N8", "Raku Glaze Kiln", "1200"),
        _row("RG-2", "N8", "Raku Glaze Kiln", "800"),
        _row("RG-3", "N9", "Qv - Raku Glaze Kiln", "300"),
        _row("RG-4", "N8", "Clay Store", "100"),
        _row("RG-5", "N9", "Raku Kiln Shed", "50"),
        _row("KG-1", "N8", "Keg Glaze 1", "10"),
        _row("KG-2", "N8", "Keg Glaze 2", "10"),
        _row("KG-3", "N9", "Keg Glaze 3", "10"),
        _row("KG-4", "N9", "Keg Glaze 4", "10"),
    ],
    "row_count": 9,
}
SHEDS = {
    "table_name": "bld_sheds",
    "columns": ["shed_code", "shed_name"],
    "rows": [{"shed_code": "N8", "shed_name": "North eight"},
             {"shed_code": "N9", "shed_name": "North nine"}],
    "row_count": 2,
}
TABLES = [KILNS, SHEDS]
HEAD = "MATCHED WORD BY WORD - "
Q = "Which sheds feed the Kiln Glaze area?"
SQL_LIST = "SELECT DISTINCT shed FROM \"bld_kilns\" WHERE spot_printed ILIKE '%Kiln Glaze%'"


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


class _Models:
    sql = ""

    def generate_content(self, *, model, contents, config=None):
        return type("_R", (), {"text": _Models.sql, "usage_metadata": None})()


class _Client:
    def __init__(self, *a, **kw):
        self.models = _Models()


def run(sql, question=Q, tables=TABLES):
    """`execute_sql_query` end to end with a fake SQL writer that returns `sql`."""
    saved = (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
             sql_tool._load_table_cards)
    sql_tool.genai.Client = _Client
    sql_tool.get_llm_api_key = lambda: "fake-key"
    sql_tool.get_llm_model = lambda: "fake-model"
    sql_tool._load_table_cards = lambda *a, **k: []
    _Models.sql = sql
    try:
        return sql_tool.execute_sql_query(question, "u-1", _Supabase(tables))
    finally:
        (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
         sql_tool._load_table_cards) = saved


def lines(text):
    return [ln for ln in text.splitlines() if ln.startswith(HEAD)]


WORDWISE = "(spot_printed ILIKE '%Kiln%' AND spot_printed ILIKE '%Glaze%')"
LINE = (HEAD + "no row has spot_printed ILIKE '%Kiln Glaze%'; the rows above were found with "
        "spot_printed holding each of its words ('Kiln' and 'Glaze'), in any order and with other "
        "words between, and the only values of spot_printed that do are 'Raku Glaze Kiln' and "
        "'Qv - Raku Glaze Kiln'")

# ---------------------------------------------------------------------------
print("1. A phrase printed in another word order: re-run word by word (RED)")
# ---------------------------------------------------------------------------
o1 = run(SQL_LIST)
check("RED: the rows the phrase's words find are the result",
      "| N8 |" in o1 and "| N9 |" in o1 and not o1.startswith("Query returned no results"), o1[:300])
check("RED: the result's SQL line is the query that found them",
      sql_loop.result_sql(o1) == SQL_LIST.replace("spot_printed ILIKE '%Kiln Glaze%'", WORDWISE),
      sql_loop.result_sql(o1))
check("RED: one line says how they were found", lines(o1) == [LINE], lines(o1) or o1[-400:])
check("RED: the line follows the SQL line", HEAD in o1 and o1.index("SQL: `") < o1.index(HEAD), o1)
check("RED: the loop reads the result as holding rows - no EMPTY re-query",
      not sql_loop.result_is_empty(o1)
      and sql_loop.EMPTY not in [i.kind for i in sql_loop.inspect_result(o1, Q, [])],
      [i.kind for i in sql_loop.inspect_result(o1, Q, [])])

o1b = run("SELECT DISTINCT shed FROM \"bld_kilns\" WHERE spot_printed ILIKE '%Kiln Glaze%' "
          "OR spot_printed ILIKE '%7.77%'")
check("RED: a phrase under an OR at the top of the WHERE is re-run the same way",
      "| N8 |" in o1b and "| N9 |" in o1b and lines(o1b) == [LINE], o1b[-400:])
check("RED: only the phrase is rewritten; the single-word branch stays as written",
      sql_loop.result_sql(o1b).endswith("OR spot_printed ILIKE '%7.77%'")
      and WORDWISE in sql_loop.result_sql(o1b), sql_loop.result_sql(o1b))

o1c = run("SELECT COUNT(*) AS n FROM \"bld_kilns\" WHERE spot_printed ILIKE '%Kiln Glaze%'",
          "How many rigs serve the Kiln Glaze area?")
check("RED: a count that found nothing (a lone zero) is re-run, and the count is the words' rows",
      "| 3 |" in o1c.split("SQL: `")[0] and lines(o1c) == [LINE], o1c[:400])

o1d = run("SELECT shed_name FROM \"bld_sheds\" WHERE shed_code IN (SELECT k.shed FROM "
          "\"bld_kilns\" k WHERE k.spot_printed ILIKE '%Kiln Glaze%') ORDER BY shed_name")
check("RED: a phrase inside a sub-SELECT is re-run the same way, its qualifier kept",
      "| North eight |" in o1d and "| North nine |" in o1d
      and "(k.spot_printed ILIKE '%Kiln%' AND k.spot_printed ILIKE '%Glaze%')"
      in sql_loop.result_sql(o1d), o1d[-500:])
check("RED: and its line names the column as written",
      lines(o1d) == [LINE.replace("spot_printed", "k.spot_printed")], lines(o1d))

o1e = run("SELECT rig, draw_w FROM \"bld_kilns\" WHERE spot_printed ILIKE '%Kiln Glaze%' "
          "AND shed = 'N8' ORDER BY rig")
check("RED: the other filters stay as written", "| RG-1 | 1200.0 |" in o1e and "RG-3" not in
      o1e.split("SQL: `")[0], o1e[:300])
matched_e = [ln for ln in o1e.splitlines() if ln.startswith("MATCHED - ")]
check("RED: the MATCHED line reads the query that found the rows - it states the other filter, "
      "never the phrase as matched", matched_e == ["MATCHED - every row above has shed = 'N8'"],
      matched_e)

o1f = run("SELECT DISTINCT shed FROM \"bld_kilns\" WHERE spot_printed ILIKE '%Kiln Q Glaze%'")
check("RED: a one-character word is left out of the words matched",
      lines(o1f) == [LINE.replace("'%Kiln Glaze%'", "'%Kiln Q Glaze%'")], lines(o1f) or o1f)

# ---------------------------------------------------------------------------
print("\n2. Silent shapes: the result is exactly what it was")
# ---------------------------------------------------------------------------
for label2, sql2 in [
    ("a single-word phrase",
     "SELECT DISTINCT shed FROM \"bld_kilns\" WHERE spot_printed ILIKE '%Kilns%'"),
    ("a phrase with only one word of two or more characters",
     "SELECT DISTINCT shed FROM \"bld_kilns\" WHERE spot_printed ILIKE '%Kiln Q%'"),
    ("words no printed value holds together",
     "SELECT DISTINCT shed FROM \"bld_kilns\" WHERE spot_printed ILIKE '%Kiln Qq%'"),
    ("a re-run that still finds nothing",
     "SELECT DISTINCT shed FROM \"bld_kilns\" WHERE spot_printed ILIKE '%Kiln Throwing%'"),
    ("LIKE rather than ILIKE",
     "SELECT DISTINCT shed FROM \"bld_kilns\" WHERE spot_printed LIKE '%Kiln Glaze%'"),
    ("a pattern without a wildcard at both ends",
     "SELECT DISTINCT shed FROM \"bld_kilns\" WHERE spot_printed ILIKE 'Kiln Glaze%'"),
    ("NOT ILIKE",
     "SELECT DISTINCT shed FROM \"bld_kilns\" WHERE spot_printed NOT ILIKE '%Clay Store%' "
     "AND shed = 'N7'"),
]:
    out2 = run(sql2)
    check(f"{label2}: still the empty result, word for word",
          out2 == f"Query returned no results.\n\nSQL: `{sql2}`", out2[:300])

sql2b = "SELECT DISTINCT shed FROM \"bld_kilns\" WHERE spot_printed ILIKE '%Glaze Kiln%'"
out2b = run(sql2b)
check("a phrase that matched as written is never re-run",
      lines(out2b) == [] and sql_loop.result_sql(out2b) == sql2b, out2b[-300:])
sql2c = "SELECT COUNT(*) AS n FROM \"bld_kilns\" WHERE spot_printed ILIKE '%Kiln Throwing%'"
out2c = run(sql2c, "How many rigs serve the Kiln Throwing area?")
check("a count still zero after the words are matched keeps its zero and its SQL",
      "| 0 |" in out2c and lines(out2c) == [] and sql_loop.result_sql(out2c) == sql2c, out2c)

# ---------------------------------------------------------------------------
print("\n3. An error leaves the empty result exactly as it was")
# ---------------------------------------------------------------------------
real_exec = sql_tool._execute_with_timeout
calls3 = []


def _fail_on(marker):
    def fail(con_, query, timeout=sql_tool.SQL_QUERY_TIMEOUT):
        calls3.append(query)
        if "'%Kiln%'" in query and marker in query:
            raise RuntimeError("re-run failed")
        return real_exec(con_, query, timeout)
    return fail


for label3, marker in [("the re-run", "DISTINCT shed"),
                       ("the probe of the column's values (fix round 1)", "GROUP BY")]:
    calls3.clear()
    sql_tool._execute_with_timeout = _fail_on(marker)
    try:
        o3 = run(SQL_LIST)
    finally:
        sql_tool._execute_with_timeout = real_exec
    check(f"RED: {label3} was really attempted",
          any("'%Kiln%'" in c and marker in c for c in calls3), calls3)
    check(f"{'RED: ' if marker == 'GROUP BY' else ''}and an error in {label3} leaves the empty "
          f"result, word for word",
          o3 == f"Query returned no results.\n\nSQL: `{SQL_LIST}`", o3[:300])

# ---------------------------------------------------------------------------
print("\n4. Fix round 1 (W3A2-R2) - which words count, and how many printed values may match")
# ---------------------------------------------------------------------------
o4a = run("SELECT DISTINCT shed FROM \"bld_kilns\" WHERE spot_printed ILIKE '%Kiln Qv%'")
check("RED: a two-character word is kept and matched: only the value that prints it is found",
      "| N9 |" in o4a and "| N8 |" not in o4a.split("SQL: `")[0]
      and lines(o4a) == [HEAD + "no row has spot_printed ILIKE '%Kiln Qv%'; the rows above were "
                         "found with spot_printed holding each of its words ('Kiln' and 'Qv'), in "
                         "any order and with other words between, and the only value of "
                         "spot_printed that does is 'Qv - Raku Glaze Kiln'"], o4a[-500:])
o4b = run("SELECT DISTINCT shed FROM \"bld_kilns\" WHERE spot_printed ILIKE '%Glaze/Kiln%'")
check("RED: a word is a run of letters and digits: two words joined by punctuation are two words",
      lines(o4b) == [HEAD + "no row has spot_printed ILIKE '%Glaze/Kiln%'; the rows above were "
                     "found with spot_printed holding each of its words ('Glaze' and 'Kiln'), in "
                     "any order and with other words between, and the only values of spot_printed "
                     "that do are 'Raku Glaze Kiln' and 'Qv - Raku Glaze Kiln'"], o4b[-500:])
o4c = run("SELECT DISTINCT shed FROM \"bld_kilns\" WHERE spot_printed ILIKE '%Kiln Raku%'")
check("RED: three printed values holding every word: still matched, all three named",
      "| N8 |" in o4c and "| N9 |" in o4c
      and lines(o4c) == [HEAD + "no row has spot_printed ILIKE '%Kiln Raku%'; the rows above were "
                         "found with spot_printed holding each of its words ('Kiln' and 'Raku'), "
                         "in any order and with other words between, and the only values of "
                         "spot_printed that do are 'Raku Glaze Kiln', 'Qv - Raku Glaze Kiln' and "
                         "'Raku Kiln Shed'"], o4c[-500:])
sql4d = "SELECT DISTINCT shed FROM \"bld_kilns\" WHERE spot_printed ILIKE '%Glaze Keg%'"
o4d = run(sql4d)
check("RED: four printed values holding every word: too loose - the empty result stands, word for "
      "word, for the loop's EMPTY re-query", o4d == f"Query returned no results.\n\nSQL: `{sql4d}`",
      o4d[-400:])
iss4d = sql_loop.inspect_result(o4d, Q, [])
check("RED: and the loop reads it as EMPTY", sql_loop.EMPTY in [i.kind for i in iss4d],
      [i.kind for i in iss4d])

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
