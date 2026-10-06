"""test_sql_children_by_place.py - a parent-reference tree's nodes counted by their children's
place (wave 5, G13 part b, 2026-10-04, designed in plan-wave4.md).

THE DEFECT. "Which board supplies Block B and which supplies Block C?" has no column that
answers it directly: every board's OWN place column names where its OWN switchgear sits, never
the place its output reaches - and that column holds the SAME one value for every top-level
board in the tree. The only way to answer "supplies" is to count where a board's CHILDREN sit.
No SQL in any recorded run ever computed that, and the existing HIERARCHY line does not apply
either - it only fires on "everything below/above X" wording, never "which of these feeds which
place".

THE FIX, in sql_tool only. A new CHILDREN BY PLACE line: when the SQL result prints 2+ of a
loaded parent-reference tree's own node identifiers, and the question itself names 2+ PRINTED
values of some OTHER column of that table as "<column name> <value>" (never its identifier or a
parent-reference column): for each such node, count its DIRECT children (one hop) per value of
that column, leaving out a node with no children at all.

Every table, column and value here is invented. Every check marked RED fails against the code as
it stood before this change.

Run:
    venv/Scripts/python -X utf8 tests/test_sql_children_by_place.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import openai_client as oc  # noqa: E402
from app.services import sql_tool  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


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


def run(sql, question, tables, cards=()):
    """`execute_sql_query` end to end with a fake SQL writer returning `sql`. No model, no
    network."""
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


def _board(name, fed_from, block, tcl):
    return {"board": name, "fed_from": fed_from, "block": block, "tcl": tcl}


# Two top-level boards (MAIN-A, MAIN-B), each feeding sub-boards across two blocks; MAIN-A's
# children split 2 block-B / 1 block-C, MAIN-B's split all block-C.
MAINS = {
    "table_name": "bld_mains",
    "columns": ["board", "fed_from", "block", "tcl"],
    "rows": [_board("MAIN-A", None, "Z", "100"),
             _board("MAIN-B", None, "Z", "90"),
             _board("SUB-1", "MAIN-A", "B", "10"),
             _board("SUB-2", "MAIN-A", "C", "5"),
             _board("SUB-3", "MAIN-A", "B", "8"),
             _board("SUB-4", "MAIN-B", "C", "7"),
             _board("SUB-5", "MAIN-B", "C", "6")],
    "row_count": 7,
}
SQL = ("SELECT board, tcl FROM \"bld_mains\" WHERE board IN ('MAIN-A', 'MAIN-B') "
       "ORDER BY tcl DESC")
Q = "Which main board supplies Block B and which supplies Block C?"


def line_of(text):
    return next((ln for ln in text.splitlines()
                if ln.startswith(sql_tool.CHILDREN_BY_PLACE_HEADING)), "")


# ===========================================================================
print("1. Two tree nodes, the question naming 2 values of their table's own category (RED)")
# ===========================================================================
out1 = run(SQL, Q, (MAINS,))
line1 = line_of(out1)
check("RED: a CHILDREN BY PLACE line appears", bool(line1), out1)
check("RED: MAIN-A feeds 2 in block B, 1 in block C",
      "MAIN-A feeds 2 in block B, 1 in block C" in line1, line1)
check("RED: MAIN-B feeds 2 in block C", "MAIN-B feeds 2 in block C" in line1, line1)
check("the original result is untouched", "| MAIN-A | 100" in out1 and "| MAIN-B | 90" in out1,
      out1)

# ===========================================================================
print("\n2. Silent shapes - no line added")
# ===========================================================================
check("only 1 node printed: no line",
      not line_of(run("SELECT board, tcl FROM \"bld_mains\" WHERE board = 'MAIN-A'",
                      Q, (MAINS,))), "")

check("the question names only ONE value of the category column",
      not line_of(run(SQL, "Which main board supplies Block B?", (MAINS,))), "")

check("the question names no column/value pair at all",
      not line_of(run(SQL, "Which main boards are the biggest?", (MAINS,))), "")

NO_TREE = {"table_name": "bld_flat", "columns": ["board", "block", "tcl"],
          "rows": [{"board": "MAIN-A", "block": "Z", "tcl": "100"},
                   {"board": "MAIN-B", "block": "Z", "tcl": "90"}], "row_count": 2}
check("no feed-reference column at all: no line",
      not line_of(run("SELECT board, tcl FROM \"bld_flat\"", Q, (NO_TREE,))), "")

ORPHANS = {"table_name": "bld_mains", "columns": ["board", "fed_from", "block", "tcl"],
          "rows": [_board("MAIN-A", None, "Z", "100"), _board("MAIN-B", None, "Z", "90")],
          "row_count": 2}
check("matched nodes exist but NEITHER has a child: no line (nothing to say)",
      not line_of(run(SQL, Q, (ORPHANS,))), "")

# ===========================================================================
print("\n3. Helper-level checks")
# ===========================================================================
found = sql_tool._named_column_values(
    "Which board supplies Block B and which supplies Block C?", MAINS, {"board", "fed_from"})
check("_named_column_values finds 'block' with both printed values",
      found.get("block") == ["B", "C"], found)
check("...and never the excluded identifier/parent-reference columns",
      "board" not in found and "fed_from" not in found, found)
check("a numeric column (tcl) is never matched, even if named oddly",
      "tcl" not in found, found)

# ===========================================================================
print("\n4. The answer rules carry the CHILDREN BY PLACE bullet; none of it in the frozen "
     "tool-choice copy")
# ===========================================================================
rules = oc.OUTPUT_FORMAT_RULES
bullet = next((ln for ln in rules.splitlines() if "CHILDREN BY PLACE" in ln), "")
check("RED: OUTPUT_FORMAT_RULES has a CHILDREN BY PLACE bullet", bool(bullet), rules[-600:])
check("RED: it says a parent supplies/belongs to the place most of its children are in",
      "supplies" in bullet and "most of its own children are in" in bullet, bullet)
check("RED: never print the line, its heading, or a table/column name",
      "Never print the line" in bullet, bullet)
frozen_bullet = next((ln for ln in oc.TOOL_CHOICE_FORMAT_RULES.splitlines()
                      if "CHILDREN BY PLACE" in ln), "")
check("RED: the frozen tool-choice copy carries none of it (ruling W7)", frozen_bullet == "")

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
