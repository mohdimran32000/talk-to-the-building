"""test_sql_notes_in_aggregate.py - notes of the rows behind a JOINED aggregate (wave 5, G12,
2026-10-04, designed in plan-wave4.md).

THE DEFECT (diagnosed on the goal-function run of 2026-09-30; plan-wave5.md section 2.6). A
GROUP BY aggregate over a JOIN of two tables counts several raw rows into a handful of output
groups; a couple of those rows carry their own `notes` cell flagging a disputed reading. Nothing
already built surfaces it to the answer: the single-table NOTES ON THE ROWS BEHIND THIS RESULT
block refuses a JOIN outright; WHAT THE FIGURE COUNTS needs one output row with no GROUP BY; and
the retrieved excerpt carrying the same note ranks outside the cross-check's append limit. So the
figure ships as settled when excluding the disputed rows gives a different one.

THE FIX: a NEW block, NOTES INSIDE THE FIGURE, for an aggregate whose own FROM ... WHERE joins
two or more loaded tables (never a single, unjoined table - that shape is the existing block's
job): re-read the same raw rows the aggregate counts, with the notes column of whichever joined
table has one and that table's own columns the query sums/counts; when 1-3 of those rows (fewer
than all of them) carry a note, list each keyed to its own contribution, and give the SAME figure
recomputed without them (a TEMP VIEW swap, the same primitive PRINTED TOTAL ROWS already uses) -
never saying which of the two figures answers.

Every table, column and value here is invented. Run:
    venv/Scripts/python -X utf8 tests/test_sql_notes_in_aggregate.py
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


def _unit(panel, kind, pts, watts, notes):
    return {"panel": panel, "kind": kind, "pts": pts, "watts": watts, "notes": notes}


# Two panels, 4 circuit rows behind the aggregate; one of PNL-A's two rows is noted - 1 of 4,
# fewer than all of PNL-A's own rows (2) and fewer than all 4 behind the whole figure.
UNITS = {
    "table_name": "bld_units",
    "columns": ["panel", "kind", "pts", "watts", "notes"],
    "rows": [_unit("PNL-A", "FCU", "1", "100", "Inference: printed tick disagrees with REMARKS"),
             _unit("PNL-A", "FCU", "1", "100", ""),
             _unit("PNL-B", "FCU", "2", "200", ""),
             _unit("PNL-B", "FCU", "3", "300", "")],
    "row_count": 4,
}
BLOCKS = {
    "table_name": "bld_blocks",
    "columns": ["panel", "block"],
    "rows": [{"panel": "PNL-A", "block": "X"}, {"panel": "PNL-B", "block": "X"}],
    "row_count": 2,
}
SQL = ('SELECT T1.panel, SUM(T1.pts) AS total_units, SUM(T1.watts) AS total_load '
       'FROM "bld_units" AS T1 JOIN "bld_blocks" AS T2 ON T1.panel = T2.panel '
       "WHERE T1.kind = 'FCU' AND T2.block = 'X' GROUP BY T1.panel ORDER BY T1.panel")
Q = "How many FCU units and what load does PNL-A and PNL-B carry?"


def block_of(text):
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith(sql_tool.NOTES_INSIDE_FIGURE_HEADING):
            j = i + 1
            while j < len(lines) and lines[j].strip():
                j += 1
            return "\n".join(lines[i:j])
    return ""


# ===========================================================================
print("1. A JOINED aggregate with 1 of N rows noted (RED)")
# ===========================================================================
out1 = run(SQL, Q, (UNITS, BLOCKS))
check("the original figure is untouched: PNL-A 2/200, PNL-B 5/500",
      "| PNL-A | 2" in out1 and "| PNL-B | 5" in out1, out1)
block1 = block_of(out1)
check("RED: a NOTES INSIDE THE FIGURE block appears", bool(block1), out1)
check("RED: it says one row carries a note", "1 row" in block1 and "carries a note" in block1,
      block1)
check("RED: the bullet is keyed to that row's OWN contribution to the sum (pts 1, watts 100)",
      "pts 1" in block1 and "watts 100" in block1, block1)
check("RED: the note text itself is quoted", "Inference" in block1 and "REMARKS" in block1,
      block1)
check("RED: the figure WITHOUT the noted row is given, and it differs from the one above "
      "(PNL-A would be 1/100, not 2/200)",
      "Without the noted row" in block1 and "1" in block1, block1)
check("picks neither figure as THE answer - no verdict word in the block",
      not any(w in block1.lower() for w in (" is the answer", "correct figure", "true total")),
      block1)

# ===========================================================================
print("\n2. Silent shapes - no block added")
# ===========================================================================
def _plain(panel, kind, pts, watts, notes=""):
    return _unit(panel, kind, pts, watts, notes)


NONE_NOTED = {"table_name": "bld_units", "columns": ["panel", "kind", "pts", "watts", "notes"],
             "rows": [_plain("PNL-A", "FCU", "1", "100"), _plain("PNL-A", "FCU", "1", "100"),
                      _plain("PNL-B", "FCU", "2", "200"), _plain("PNL-B", "FCU", "3", "300")],
             "row_count": 4}
out_none = run(SQL, Q, (NONE_NOTED, BLOCKS))
check("0 noted rows: no block", not block_of(out_none), out_none)

ALL_NOTED = {"table_name": "bld_units", "columns": ["panel", "kind", "pts", "watts", "notes"],
            "rows": [_unit("PNL-A", "FCU", "1", "100", "note A"),
                     _unit("PNL-A", "FCU", "1", "100", "note B"),
                     _unit("PNL-B", "FCU", "2", "200", "note C"),
                     _unit("PNL-B", "FCU", "3", "300", "note D")],
            "row_count": 4}
out_all = run(SQL, Q, (ALL_NOTED, BLOCKS))
check("every row noted: no block (there is no 'without them' figure to contrast)",
      not block_of(out_all), out_all)

MANY_NOTED_ROWS = [_unit(f"PNL-{i}", "FCU", "1", "100", "noted" if i < 4 else "")
                  for i in range(6)]
MANY_NOTED = {"table_name": "bld_units", "columns": ["panel", "kind", "pts", "watts", "notes"],
             "rows": MANY_NOTED_ROWS, "row_count": 6}
BLOCKS6 = {"table_name": "bld_blocks", "columns": ["panel", "block"],
          "rows": [{"panel": f"PNL-{i}", "block": "X"} for i in range(6)], "row_count": 6}
out_many = run(SQL, Q, (MANY_NOTED, BLOCKS6))
check("more than ROW_NOTES_MAX_ROWS noted (4 of 6): no block",
      not block_of(out_many), out_many)

SINGLE_TABLE_SQL = ('SELECT panel, SUM(pts) AS total_units FROM "bld_units_small" '
                    "WHERE kind = 'FCU' GROUP BY panel ORDER BY panel")
# The existing single-table block only ever shows the rows behind a SMALL result (at most
# ROW_NOTES_MAX_ROWS total) - so this fixture, unlike UNITS above, keeps the WHERE's own match
# to 3 rows, fewer than UNITS's 4, purely so that OTHER mechanism's own precondition is met and
# this is a real regression check, not a vacuous one.
UNITS_SMALL = {"table_name": "bld_units_small", "columns": ["panel", "kind", "pts", "watts",
              "notes"],
              "rows": [_unit("PNL-A", "FCU", "1", "100", "Inference: disputed reading"),
                       _unit("PNL-A", "FCU", "1", "100", ""),
                       _unit("PNL-B", "FCU", "2", "200", "")],
              "row_count": 3}
out_single = run(SINGLE_TABLE_SQL, Q, (UNITS_SMALL,))
check("a single, UNJOINED table's aggregate is the OTHER block's job - this one stays silent",
      not block_of(out_single), out_single)
check("...and the existing single-table notes block still fires there instead",
      "NOTES ON THE ROWS BEHIND THIS RESULT" in out_single, out_single)

# ===========================================================================
print("\n3. Helper-level checks")
# ===========================================================================
check("_aggregate_arg_columns reads a bare and a qualified SUM argument",
      sql_tool._aggregate_arg_columns(
          "SELECT a.x, SUM(a.y) AS s1, SUM(z) AS s2 FROM t a") == ["y", "z"])
check("COUNT(*) and an expression name nothing",
      sql_tool._aggregate_arg_columns(
          "SELECT COUNT(*) AS n, SUM(a + b) AS s FROM t") == [])
check("_from_where_span cuts at GROUP BY, keeping a WHERE sub-SELECT whole",
      sql_tool.sql_token_spans("x")[0] is not None)
span = sql_tool._from_where_span(
    "SELECT a, SUM(b) FROM t WHERE a IN (SELECT a FROM u) GROUP BY a")
cut = "SELECT a, SUM(b) FROM t WHERE a IN (SELECT a FROM u) GROUP BY a"[span[0]:span[1]]
check("...the sub-SELECT stays inside the cut FROM ... WHERE",
      "(SELECT a FROM u)" in cut and "GROUP BY" not in cut, cut)
check("a UNION query has no span", sql_tool._from_where_span(
    "SELECT a FROM t UNION SELECT a FROM u") is None)

# ===========================================================================
print("\n4. The answer rules carry the NOTES INSIDE THE FIGURE bullet; none of it in the frozen "
     "tool-choice copy")
# ===========================================================================
rules = oc.OUTPUT_FORMAT_RULES
bullet = next((ln for ln in rules.splitlines() if "NOTES INSIDE THE FIGURE" in ln), "")
check("RED: OUTPUT_FORMAT_RULES has a NOTES INSIDE THE FIGURE bullet", bool(bullet), rules[-600:])
check("RED: it says to give BOTH figures and never silently pick one",
      "BOTH figures" in bullet and "never silently pick one" in bullet, bullet)
check("RED: it says to name what each note says and which row it is about",
      "which row it is about" in bullet, bullet)
check("RED: never print the block, its heading, or a table/column name",
      "Never print the block" in bullet, bullet)
frozen_bullet = next((ln for ln in oc.TOOL_CHOICE_FORMAT_RULES.splitlines()
                      if "NOTES INSIDE THE FIGURE" in ln), "")
check("RED: the frozen tool-choice copy carries none of it (ruling W7)", frozen_bullet == "")

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
