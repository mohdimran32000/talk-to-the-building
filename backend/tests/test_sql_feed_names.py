"""test_sql_feed_names.py - when two records list one parent's children under different names, the
result says so (wave 4, G14 part a, 2026-10-03).

THE DEFECT. Asked what one parent feeds, the SQL writer read the children off the parent's own list
of ways - a second record that prints two of them under names no row of the tree carries, and a
spare way among them - where the children's own rows, whose parent reference IS the tree, name them
otherwise. The answer listed that record's names, the spare way included. Nothing links the two.

THE FIX, in sql_tool, as one line beside the result (FEED NAMES), built on the HIERARCHY walk's own
reading of a feed tree. Another table the SQL reads is a LIST of a tree's children when a pair of
its columns (parent, child) agrees with the tree's own effective parent on its rows. When one
column of the result prints two or more names that list gives one parent, and the two records each
hold, for that parent, a name the other lacks (folded for case, accents, punctuation and a trailing
bracketed label; a space or spare way never counting), the line names the tree's own children and
the names found on one side only. One answer rule reads it (OUTPUT_FORMAT_RULES only).

Every table, column and value here is invented. Every check marked RED fails against the code as it
stood before this change.

Run:
    venv/Scripts/python -X utf8 tests/test_sql_feed_names.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import openai_client, sql_loop, sql_tool  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


HEAD = "FEED NAMES - "


def _unit(name, fed_from, rolls="", kw=""):
    return {"unit": name, "fed_from": fed_from, "rolls_up_to": rolls, "kw": kw, "notes": ""}


# The tree: each unit's own row names its parent. QX-1 feeds four units, QX-2 two.
UNITS = {
    "table_name": "bld_units",
    "columns": ["unit", "fed_from", "rolls_up_to", "kw", "notes"],
    "rows": [_unit("QX-0", ""), _unit("QX-1", "QX-0"), _unit("QX-2", "QX-0"),
             _unit("QU-11-A", "QX-1", kw="8"), _unit("QU-12", "QX-1", kw="5"),
             _unit("QU-13", "QX-1", kw="3"), _unit("QU-14 (NEW)", "QX-1", kw="2"),
             _unit("QU-21", "QX-2", kw="4"), _unit("QU-22", "QX-2", kw="6")],
    "row_count": 9,
}


def _way(parent, way, kw=""):
    return {"parent_unit": parent, "way": way, "way_kw": kw}


# The second record: each parent's list of ways. QX-1's list prints QU-11-A as 'QU-11-B', leaves out
# QU-12 for 'QU-12-X', spells QU-14 without its label, and has a SPACE way; QX-2's list agrees.
WAYS = {
    "table_name": "bld_ways",
    "columns": ["parent_unit", "way", "way_kw"],
    "rows": [_way("QX-0", "QX-1"), _way("QX-0", "QX-2"),
             _way("QX-1", "QU-11-B", "8"), _way("QX-1", "QU-12-X", "5"), _way("QX-1", "QU-13", "3"),
             _way("QX-1", "QU-14", "2"), _way("QX-1", "SPACE"),
             _way("QX-2", "QU-21", "4"), _way("QX-2", "QU-22", "6"), _way("QX-2", "Spare")],
    "row_count": 10,
}
CARDS = [{"table": "bld_units", "identifier_column": "unit", "holds": "one row per unit"},
         {"table": "bld_ways", "identifier_column": "way", "holds": "one row per way of a parent"}]


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


def run(sql, question="q", tables=(UNITS, WAYS), cards=()):
    """`execute_sql_query` end to end with a fake SQL writer returning `sql`. No model, no network.
    No cards by default, so no router narrows the tables: every fixture table is loaded (the
    documented full-schema fallback) and the line is read off the loaded tables alone."""
    saved = (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
             sql_tool._load_table_cards)
    sql_tool.genai.Client = _Client
    sql_tool.get_llm_api_key = lambda: "fake-key"
    sql_tool.get_llm_model = lambda: "fake-model"
    sql_tool._load_table_cards = lambda *a, **k: list(cards)
    _Models.sql = sql
    try:
        return sql_tool.execute_sql_query(question, "u-1", _Supabase(list(tables)))
    finally:
        (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
         sql_tool._load_table_cards) = saved


def feed_lines(text):
    return [ln for ln in text.splitlines() if ln.startswith(HEAD)]


Q1 = "Which unit feeds QU-13, and what else does that unit feed?"
LIST_SQL = ("SELECT w.parent_unit, w.way FROM \"bld_units\" u JOIN \"bld_ways\" w ON u.unit = "
            "w.parent_unit WHERE u.unit = (SELECT fed_from FROM \"bld_units\" WHERE unit = 'QU-13')")
LINE1 = (HEAD + "QX-1: bld_units records 4 rows fed from it: QU-11-A, QU-12, QU-13, QU-14 (NEW); "
         "bld_ways, listing what it feeds, also prints QU-11-B, QU-12-X - names no bld_units row "
         "carries - and does not print QU-11-A, QU-12; it also lists 1 space or spare way")

# ---------------------------------------------------------------------------
print("1. A parent's children read off the second record, which names two of them otherwise (RED)")
# ---------------------------------------------------------------------------
out1 = run(LIST_SQL, Q1)
check("the writer's own rows are still there, the SPACE way included",
      "| QX-1 | QU-11-B |" in out1 and "| QX-1 | SPACE |" in out1, out1[:500])
check("RED: the result carries exactly one FEED NAMES line", len(feed_lines(out1)) == 1,
      feed_lines(out1) or out1[-600:])
check("RED: it names the tree's own children, the names found on one side only, and the spare way",
      feed_lines(out1) == [LINE1], feed_lines(out1))
check("RED: a name differing only by a trailing bracketed label is the same name (QU-14): on "
      "neither side's list", bool(feed_lines(out1)) and "also prints" in feed_lines(out1)[0]
      and "QU-14" not in feed_lines(out1)[0].split("also prints")[1], feed_lines(out1))
check("RED: it comes after the SQL line, never inside the table",
      bool(feed_lines(out1)) and out1.index("SQL: `") < out1.index(HEAD)
      and not any(ln.startswith("|") and HEAD in ln for ln in out1.splitlines()), out1[-800:])
out1b = run("SELECT way FROM \"bld_ways\" WHERE parent_unit = 'QX-1'", "What does QX-1 feed?")
check("RED: a plain list of the second record's ways, the same way", feed_lines(out1b) == [LINE1],
      feed_lines(out1b))
out1c = run("SELECT u.fed_from, w.way FROM \"bld_units\" u JOIN \"bld_ways\" w ON w.parent_unit = "
            "u.fed_from WHERE u.unit = 'QU-13' AND w.way <> 'QU-13'", Q1)
check("RED: the parent is the one the second record lists the printed names under, even when "
      "the query filtered some of them out", feed_lines(out1c) == [LINE1], feed_lines(out1c))

# ---------------------------------------------------------------------------
print("\n2. Silent shapes")
# ---------------------------------------------------------------------------
for label2, sql2 in [
    ("the two records agree (QX-2: the same two names, a spare way beside them)",
     "SELECT way FROM \"bld_ways\" WHERE parent_unit = 'QX-2'"),
    ("a value lookup of one way", "SELECT way_kw FROM \"bld_ways\" WHERE parent_unit = 'QX-1' AND "
                                  "way = 'QU-13'"),
    ("one name of that parent printed", "SELECT way, way_kw FROM \"bld_ways\" WHERE way = 'QU-13'"),
    ("only space or spare ways printed", "SELECT way FROM \"bld_ways\" WHERE way IN ('SPACE', "
                                         "'Spare')"),
    ("the tree alone read", "SELECT unit FROM \"bld_units\" WHERE fed_from = 'QX-1'"),
    ("the parent's own row only", "SELECT * FROM \"bld_units\" WHERE unit = 'QX-1'"),
]:
    out2 = run(sql2, "What does it feed?")
    check(f"{label2}: no line", "SQL query failed" not in out2 and feed_lines(out2) == [],
          feed_lines(out2) or out2[-300:])
NOT_A_LIST = dict(WAYS, table_name="bld_other", rows=[dict(r, parent_unit="ZZ-9") for r in WAYS["rows"]])
out2b = run("SELECT way FROM \"bld_other\" WHERE parent_unit = 'ZZ-9'", "What does ZZ-9 feed?",
            tables=(UNITS, NOT_A_LIST), cards=())
check("a table whose pairs never agree with the tree's own parents lists nobody's children: no line",
      feed_lines(out2b) == [] and "| QU-13 |" in out2b, feed_lines(out2b) or out2b[-300:])
EARLIER = dict(WAYS, table_name="bld_existing_ways")
out2c = run("SELECT way FROM \"bld_existing_ways\" WHERE parent_unit = 'QX-1'", "What did QX-1 feed?",
            tables=(UNITS, EARLIER), cards=())
check("a pre-takeover list is never compared with the current tree: no line",
      feed_lines(out2c) == [] and "| QU-11-B |" in out2c, feed_lines(out2c) or out2c[-300:])
# Fix round 1 (review minor 3): the names must come OUT of the query. A lookup of values for ways it
# names itself - their ratings, say - prints names the SQL was handed as literals, which tell nothing
# of what the parent feeds.
out2d = run("SELECT way, way_kw FROM \"bld_ways\" WHERE parent_unit = 'QX-1' AND way IN ('QU-11-B', "
            "'QU-12-X')", "What are the ratings of the ways QU-11-B and QU-12-X on QX-1?")
check("RED (minor 3): a lookup of two ways the SQL names as literals: no line",
      feed_lines(out2d) == [] and "| QU-11-B | 8.0 |" in out2d, feed_lines(out2d) or out2d[-300:])
out2e = run("SELECT way FROM \"bld_ways\" WHERE parent_unit = 'QX-1' AND way <> 'SPACE'",
            "What does QX-1 feed?")
check("names that come out of the query still count, whatever else the SQL names",
      feed_lines(out2e) == [LINE1], feed_lines(out2e))

# ---------------------------------------------------------------------------
print("\n3. It never touches the rows, the loop reads nothing new off it, an error drops only it")
# ---------------------------------------------------------------------------
rows1 = [ln for ln in out1.splitlines() if ln.startswith("|")]
check("the rendered rows are exactly the writer's (header, rule, 5 ways)", len(rows1) == 2 + 5, rows1)
issues_with = [i.kind for i in sql_loop.inspect_result(out1, Q1, CARDS)]
stripped = "\n".join(ln for ln in out1.splitlines() if not ln.startswith(HEAD))
issues_without = [i.kind for i in sql_loop.inspect_result(stripped, Q1, CARDS)]
check("the loop raises the same issues with or without the line", issues_with == issues_without,
      (issues_with, issues_without))
real_exec = sql_tool._execute_with_timeout


def _fail_tree(con, sql, timeout=sql_tool.SQL_QUERY_TIMEOUT):
    if "ORDER BY rowid" in sql and "COALESCE" in sql and "WITH RECURSIVE" not in sql:
        raise RuntimeError("tree read failed")
    return real_exec(con, sql, timeout)


sql_tool._execute_with_timeout = _fail_tree
try:
    out3 = run(LIST_SQL, Q1)
finally:
    sql_tool._execute_with_timeout = real_exec
check("an error drops the line and nothing else",
      feed_lines(out3) == [] and "| QX-1 | QU-13 |" in out3 and "SQL query failed" not in out3,
      out3[-300:])

# Fix round 1 (review minor 5): the trees are read only when the SQL reads a table that lists a
# tree's children - asked in Python off the loaded rows first - so a result reading none costs no
# query at all.
import duckdb  # noqa: E402


def loaded(tables):
    """A DuckDB connection holding `tables`, every column text - the trees' reads compare text."""
    con = duckdb.connect(":memory:")
    for t in tables:
        cols = t["columns"]
        con.execute(f'CREATE TABLE "{t["table_name"]}" ('
                    + ", ".join(f'"{c}" VARCHAR' for c in cols) + ")")
        for r in t["rows"]:
            con.execute(f'INSERT INTO "{t["table_name"]}" VALUES (' + ", ".join("?" * len(cols)) + ")",
                        [r.get(c) for c in cols])
    return con


ALL3 = [UNITS, WAYS, NOT_A_LIST]
con3 = loaded(ALL3)
counted = []


def _counting(con, sql, timeout=sql_tool.SQL_QUERY_TIMEOUT):
    counted.append(sql)
    return real_exec(con, sql, timeout)


def feed_queries(sql):
    """`(line, queries run)` of FEED NAMES alone for `sql` over the three tables."""
    rows = con3.execute(sql).fetchall()
    counted.clear()
    sql_tool._execute_with_timeout = _counting
    try:
        line = sql_tool._feed_names_line(con3, sql, ALL3, [], rows)
    finally:
        sql_tool._execute_with_timeout = real_exec
    return line, len(counted)


tree_only = feed_queries("SELECT unit FROM \"bld_units\" WHERE fed_from = 'QX-1'")
check("RED (minor 5): a result reading only the tree runs no query for the line",
      tree_only == ("", 0), tree_only)
not_list = feed_queries("SELECT way FROM \"bld_other\" WHERE parent_unit = 'ZZ-9'")
check("RED (minor 5): a result reading a table that lists no tree's children runs no query",
      not_list == ("", 0), not_list)
listing = feed_queries("SELECT way FROM \"bld_ways\" WHERE parent_unit = 'QX-1'")
check("a result reading the list still reads the tree, and the line is unchanged",
      listing[0] == LINE1 and listing[1] >= 1, listing)
shape3 = sql_tool._tree_shape(con3, UNITS, None)
duck3 = {}
for node3, parent3 in con3.execute(f"SELECT {sql_tool._text_sql(shape3.id_col)}, {shape3.parent_sql} "
                                   f"FROM \"bld_units\" ORDER BY rowid").fetchall():
    duck3.setdefault(node3, parent3)
python3 = getattr(sql_tool, "_loaded_tree_parents", lambda *a: None)(UNITS, None)
check("RED (minor 5): the Python reading of a tree, which decides when to read it, equals "
      "_tree_shape's own", python3 == duck3, (python3, duck3))

# ---------------------------------------------------------------------------
print("\n4. One answer rule reads the line (answer prompts only)")
# ---------------------------------------------------------------------------
rule = next((ln for ln in openai_client.OUTPUT_FORMAT_RULES.splitlines() if "FEED NAMES" in ln), "")
check("RED: OUTPUT_FORMAT_RULES has a FEED NAMES bullet", bool(rule))
check("RED: the answer comes from the tree's own names, and names what the other record prints, as "
      "printed", "own names" in rule and "as that record prints them" in rule, rule)
# Fix round 1 (review minor 4): the other record's names that match no row of the tree are not
# necessarily renamings - they may be other loads, provisions or another spelling - so the rule names
# them as printed and never calls them what the record prints "instead".
check("RED (minor 4): the other record's unmatched names may be other loads, provisions or another "
      "spelling, and the line does not say which",
      "may be other loads, provisions or the same thing spelled another way" in rule
      and "the line does not say which" in rule, rule)
check("RED (minor 4): the rule never says the other record prints them 'instead'",
      "instead" not in rule, rule)
check("RED: a space or spare way is never counted as a child", "space or spare way" in rule, rule)
check("RED: the line, its heading and table or column names are never printed",
      "never print" in rule.lower(), rule)
check("the frozen tool-choice rules carry none of it (ruling W7)",
      "FEED NAMES" not in openai_client.TOOL_CHOICE_FORMAT_RULES)

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
