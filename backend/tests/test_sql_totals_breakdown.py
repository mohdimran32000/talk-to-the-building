"""test_sql_totals_breakdown.py - a flat TOTAL beside a column that splits it into categories
says the split too (wave 5, 2026-10-04).

THE DEFECT (diagnosed on the goal-function run of 2026-09-30; plan-wave5.md section 2.4). The
deterministic-totals block sums a qty-like column across every row of a 4+ row result and prints
one flat `TOTAL qty (all N rows): T` line, blind to another column in the SAME result that is not
grouped by and splits those rows into a handful of different KINDS of thing - "2 spare widget-A
units + 2 spare widget-B units" summed to a flat "4", which an answer-writer sometimes read and
called "4 spare widget-A units" (true answer: 2). The result already carried the right split one
line above, in RESULT SHAPE's own breakdown - but nothing connected the two lines, and the writer
picked whichever one it sampled.

THE FIX, in code, beside the flat total: when another column in the result holds 2-8 distinct,
non-numeric values across fewer than N rows (the same qualifying shape `_result_shape` already
uses for its own breakdown lines), the qty-like column is also SUMMED per that column's value and
appended to the SAME line - never replacing the flat total, only adding to it:

    TOTAL qty (all 4 rows): 4 - by item: Widget A(Spare) 2, Widget B(Spare) 2

A second qualifying column gets its own " - by <col>: ..." clause too, UNLESS it splits the rows
into exactly the same groups another already-shown column does (two labels for the one split,
such as a room id and that room's own display name, are noise, not a second fact) - the first
column in SELECT order wins, the later one is silently skipped.

Every table, column and value here is invented. Every check marked RED fails against the code as
it stood before this change.

Run:
    venv/Scripts/python -X utf8 tests/test_sql_totals_breakdown.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
    """`execute_sql_query` end to end with a fake SQL writer returning `sql`. No model, no network."""
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


def total_lines(text):
    return [ln for ln in text.splitlines() if ln.startswith("TOTAL ")]


def _spare(loc, name, item, qty):
    return {"location_id": loc, "display_name": name, "item": item, "qty": qty, "notes": ""}


# Mirrors the real diagnosed SHAPE (never its values): two rooms, each holding one spare of two
# different kinds - `display_name` is just another spelling of `location_id` (Hub 1 <-> RM-A1),
# so it must be skipped as a duplicate split, not shown as a second clause.
SPARES = {
    "table_name": "bld_spares",
    "columns": ["location_id", "display_name", "item", "qty", "notes"],
    "rows": [_spare("RM-A1", "Hub 1", "Widget A(Spare)", "1"),
             _spare("RM-A1", "Hub 1", "Widget B(Spare)", "1"),
             _spare("RM-A2", "Hub 2", "Widget A(Spare)", "1"),
             _spare("RM-A2", "Hub 2", "Widget B(Spare)", "1")],
    "row_count": 4,
}
SQL = ("SELECT location_id, display_name, item, qty, notes FROM \"bld_spares\" "
       "WHERE item ILIKE '%spare%'")
Q = "How many spare Widget A units are there?"

# ===========================================================================
print("1. A flat TOTAL beside a column that splits the rows into kinds (RED)")
# ===========================================================================
out1 = run(SQL, Q, (SPARES,))
check("the flat total is still exactly as before - never replaced",
      "\nTOTAL qty (all 4 rows): 4" in out1, out1)
check("RED: the same line also gives the true per-item split (2 Widget A + 2 Widget B, not a "
      "flat 4) and location_id's own split (2 per room) - the first non-duplicate split in "
      "column order",
      total_lines(out1) == ["TOTAL qty (all 4 rows): 4 — by location_id: RM-A1 2, RM-A2 2 "
                            "— by item: Widget A(Spare) 2, Widget B(Spare) 2"],
      total_lines(out1))
check("RED: display_name is skipped - it splits the four rows exactly the way location_id "
      "already does, so it is not shown a second time",
      "by display_name" not in (total_lines(out1)[0] if total_lines(out1) else ""),
      total_lines(out1))
check("still only ONE TOTAL line for the one qty-like column", len(total_lines(out1)) == 1,
      total_lines(out1))

# ===========================================================================
print("\n2. Two INDEPENDENT splits both get their own clause (not a duplicate of each other)")
# ===========================================================================
def _asset(system, item, qty):
    return {"system": system, "item": item, "qty": qty}


# 6 rows, two columns that split the rows two DIFFERENT ways: system cuts 4/2, item cuts
# 3/2/1 - neither is a relabelling of the other.
ASSETS = {
    "table_name": "bld_assets",
    "columns": ["system", "item", "qty"],
    "rows": [_asset("fire", "Detector", "1"), _asset("fire", "Detector", "1"),
             _asset("fire", "Detector", "1"), _asset("fire", "Panel", "1"),
             _asset("ups", "Battery", "1"), _asset("ups", "Battery", "1")],
    "row_count": 6,
}
out2 = run("SELECT system, item, qty FROM \"bld_assets\"", "What assets are there?", (ASSETS,))
line2 = total_lines(out2)[0] if total_lines(out2) else ""
check("RED: both independent columns get their own by-clause", "by system:" in line2
      and "by item:" in line2, line2)
check("system: fire 4, ups 2", "fire 4" in line2 and "ups 2" in line2, line2)
check("item: Detector 3, Panel 1, Battery 2", all(s in line2 for s in
      ("Detector 3", "Panel 1", "Battery 2")), line2)

# ===========================================================================
print("\n3. Silent shapes - no by-clause added")
# ===========================================================================
def _row(a, b, qty):
    return {"a": a, "b": b, "qty": qty}


for label, rows, extra_check in [
    ("under 4 rows", [_row("x", "1", "1"), _row("x", "2", "1"), _row("y", "3", "1")], None),
    ("the other column is all-numeric", [_row("1", "9", "1"), _row("1", "9", "1"),
                                         _row("2", "9", "1"), _row("2", "9", "1")], None),
    ("the other column is unique per row (k == n)", [_row("r1", "9", "1"), _row("r2", "9", "1"),
                                                      _row("r3", "9", "1"), _row("r4", "9", "1")],
     None),
    ("the other column has only 1 distinct value", [_row("same", "9", "1"), _row("same", "9", "1"),
                                                     _row("same", "9", "1"), _row("same", "9", "1")],
     None),
    ("more than 8 distinct values (9 distinct over 10 rows - not k == n either)",
     [_row(f"k{i}", "9", "1") for i in range(9)] + [_row("k0", "9", "1")], None),
]:
    table = {"table_name": "bld_rows", "columns": ["a", "b", "qty"], "rows": rows,
             "row_count": len(rows)}
    out3 = run("SELECT a, b, qty FROM \"bld_rows\"", "How many are there?", (table,))
    lines3 = total_lines(out3)
    check(f"{label}: no 'by' clause added" + (" (no TOTAL line at all)" if len(rows) < 4 else ""),
          (lines3 == [] if len(rows) < 4 else (len(lines3) == 1 and "—" not in lines3[0])),
          lines3)

# ===========================================================================
print("\n4. Nothing else about the result changes - purely additive")
# ===========================================================================
check("the SQL line, the markdown table and RESULT SHAPE are all still present",
      "SQL: `" in out1 and out1.startswith("| ") and "RESULT SHAPE" in out1, out1[:120])
no_other_col = {"table_name": "bld_single", "columns": ["qty"],
                "rows": [{"qty": "1"}, {"qty": "2"}, {"qty": "3"}, {"qty": "4"}], "row_count": 4}
out4 = run("SELECT qty FROM \"bld_single\"", "What is the total?", (no_other_col,))
check("a result with no OTHER column at all keeps the flat total exactly as before",
      total_lines(out4) == ["TOTAL qty (all 4 rows): 10"], total_lines(out4))

# ===========================================================================
print("\n5. Three or more qualifying columns are CAPPED on one TOTAL line (W5-A1 review minor)")
# ===========================================================================
def _wide(kind, team, zone, qty):
    return {"kind": kind, "team": team, "zone": zone, "qty": qty}


# 6 rows, THREE columns that each independently qualify: kind splits 2 ways, team 3 ways, zone
# 5 ways - none a relabelling of another (different row-grouping signatures).
WIDE = {
    "table_name": "bld_wide",
    "columns": ["kind", "team", "zone", "qty"],
    "rows": [_wide("K1", "T1", "Z1", "1"), _wide("K1", "T2", "Z2", "1"),
             _wide("K1", "T1", "Z3", "1"), _wide("K2", "T2", "Z4", "1"),
             _wide("K2", "T1", "Z5", "1"), _wide("K2", "T3", "Z1", "1")],
    "row_count": 6,
}
out5 = run("SELECT kind, team, zone, qty FROM \"bld_wide\"", "How many are there?", (WIDE,))
line5 = total_lines(out5)[0] if total_lines(out5) else ""
check("RED: at most 2 '— by <col>:' clauses appear on the one TOTAL line",
      line5.count(" — by ") == 2, line5)
check("RED: the fewest-distinct-values column (kind, 2 values) is shown first - the most "
      "informative, quickest-to-read split", "by kind:" in line5
      and line5.index("by kind:") < line5.index("by team:"), line5)
check("RED: team (3 values) is the second clause shown", "by team:" in line5, line5)
check("RED: zone (5 values, the least informative of the three) is left out, and the line says "
      "how many more were left out", "by zone:" not in line5 and "more" in line5, line5)

# ===========================================================================
print("\n6. The qualifying-column rule is ONE helper, shared with _result_shape (never two "
     "copies that could drift)")
# ===========================================================================
import ast
import inspect


def names_read(func):
    return {n.id for n in ast.walk(ast.parse(inspect.getsource(func)))
            if isinstance(n, ast.Name)}


shape_names = names_read(sql_tool._result_shape)
breakdown_names = names_read(sql_tool._totals_breakdown)
shared = shape_names & breakdown_names & {n for n in dir(sql_tool) if n.startswith("_qualif")}
check("RED: _result_shape and _totals_breakdown both call the SAME qualifying-column helper",
      len(shared) == 1, (shape_names & {n for n in dir(sql_tool) if n.startswith("_qualif")},
                         breakdown_names & {n for n in dir(sql_tool) if n.startswith("_qualif")}))
if shared:
    helper = getattr(sql_tool, next(iter(shared)))
    check("the shared helper applies the 2-8 distinct / not-all-rows / not-all-numeric rule",
          helper(["a", "a", "b", "b"], 4) is True and helper(["a", "b", "c", "d"], 4) is False
          and helper(["1", "2", "3", "4", "5", "6", "7", "8", "9"], 9) is False
          and helper(["1", "1", "2", "2"], 4) is False)

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
