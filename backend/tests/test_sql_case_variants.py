"""test_sql_case_variants.py - a filter on one spelling of a value the column also prints in
another letter case or spacing is re-run widened to every spelling (wave 3, F1, 2026-10-03).

THE DEFECT. The schema block lists a short text column's every value, labelled complete, and
the prompt says to filter with those exact values. A column printing one value in two letter
cases - as the fixture below prints 'Kiln Bay (Wet)' on some rows and 'Kiln Bay (wet)' on
others - then got `= 'Kiln Bay (Wet)'` from the writer, and the result silently held only the
rows of that one spelling. The answer stated their count and total as the whole.

THE FIX, in code and on the result only (no schema-line or prompt change). Every top-level
`col = 'lit'` or `col IN ('a', ...)` of the final SQL - ANDed at the top of its own single
WHERE, as the MATCHED line reads them - on a text column of a loaded table is probed: when the
column also holds a value equal to the literal once letter case and surrounding spaces are
folded (`lower(trim(...))`, the same expression the re-run uses), the same SQL is re-run with
only that predicate widened to `lower(trim(col)) = lower(trim('lit'))` (an IN list: every
literal so), and the widened result is returned with the widened SQL. The MATCHED line then
names every spelling the table prints, with its row count. A column holding no variant is
never touched: the result text is byte for byte what it was. An error drops the widening and
nothing else.

Every table, column and value here is invented. Every check marked RED fails against
sql_tool.py as it stood before this change.

Run:
    venv/Scripts/python -X utf8 tests/test_sql_case_variants.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import sql_tool  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


ROOMS = {
    "table_name": "bld_rooms",
    "columns": ["room_no", "room_label", "area_m2", "dept", "wing"],
    "rows": [
        {"room_no": "KB-1", "room_label": "Kiln Store", "area_m2": "10.5", "dept": "Kiln Bay (Wet)",
         "wing": "N"},
        {"room_no": "KB-2", "room_label": "Kiln Yard", "area_m2": "20.0", "dept": "Kiln Bay (Wet)",
         "wing": "N"},
        {"room_no": "KB-3", "room_label": "Kiln Annex", "area_m2": "30.0", "dept": "Kiln Bay (wet)",
         "wing": "S"},
        {"room_no": "KB-4", "room_label": "Kiln Loft", "area_m2": "40.0", "dept": "Kiln Bay (wet)",
         "wing": "S"},
        {"room_no": "KB-5", "room_label": "Kiln Lobby", "area_m2": "5.5", "dept": "Kiln Bay (wet)",
         "wing": "S"},
        {"room_no": "PH-1", "room_label": "Pottery Hall East", "area_m2": "12.0",
         "dept": "Pottery Hall", "wing": "N"},
        {"room_no": "PH-2", "room_label": "Pottery Hall West", "area_m2": "13.0",
         "dept": "Pottery Hall ", "wing": "S"},
        {"room_no": "PS-1", "room_label": "Print Shop Front", "area_m2": "7.0",
         "dept": "Print Shop", "wing": "N"},
        {"room_no": "PS-2", "room_label": "Print Shop Back", "area_m2": "8.0",
         "dept": "Print Shop", "wing": "S"},
    ],
    "row_count": 9,
}
WINGS = {
    "table_name": "bld_wings",
    "columns": ["wing", "wing_label"],
    "rows": [{"wing": "N", "wing_label": "North quay"}, {"wing": "S", "wing_label": "South quay"}],
    "row_count": 2,
}
TABLES = [ROOMS, WINGS]
KB_ALL = ["KB-1", "KB-2", "KB-3", "KB-4", "KB-5"]
MT = "MATCHED - every row above has "


class _ExecResult:
    def __init__(self, data):
        self.data = data


class _Supabase:
    """Serves every table for `structured_data`; `.eq()` and `.order()` are accepted."""

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


QUERIES = []  # every query `execute_sql_query` ran on DuckDB in the last `run`


def run(sql, tables=TABLES):
    """`execute_sql_query` end to end - the real loader, DuckDB and result assembly - with a
    fake SQL writer that returns `sql`. No model, no network, no cards."""
    saved = (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
             sql_tool._load_table_cards, sql_tool._execute_with_timeout)
    real_exec = sql_tool._execute_with_timeout

    def _recording(con, query, timeout=sql_tool.SQL_QUERY_TIMEOUT):
        QUERIES.append(query)
        return real_exec(con, query, timeout)

    sql_tool.genai.Client = _Client
    sql_tool.get_llm_api_key = lambda: "fake-key"
    sql_tool.get_llm_model = lambda: "fake-model"
    sql_tool._load_table_cards = lambda *a, **k: []
    sql_tool._execute_with_timeout = _recording
    _Models.sql = sql
    QUERIES.clear()
    try:
        return sql_tool.execute_sql_query("q", "u-1", _Supabase(tables))
    finally:
        (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
         sql_tool._load_table_cards, sql_tool._execute_with_timeout) = saved


def table_rows(text):
    """The rendered table's data rows, each as a list of its cells."""
    lines = [ln for ln in text.splitlines() if ln.startswith("|")]
    return [[c.strip() for c in ln.strip("|").split("|")] for ln in lines[2:]]


def first_cells(text):
    return [row[0] for row in table_rows(text)]


def sql_line(text):
    found = [ln for ln in text.splitlines() if ln.startswith("SQL: `")]
    return found[-1] if found else ""


def matched(text):
    return [ln for ln in text.splitlines() if ln.startswith("MATCHED")]


# ---------------------------------------------------------------------------
print("1. `col = 'lit'` on a column that also prints the value in another letter case (RED)")
# ---------------------------------------------------------------------------
q1 = "SELECT room_no, area_m2 FROM \"bld_rooms\" WHERE dept = 'Kiln Bay (Wet)' ORDER BY room_no"
o1 = run(q1)
check("RED: the result holds the rows of BOTH spellings - all five", first_cells(o1) == KB_ALL,
      first_cells(o1))
check("RED: the SQL line is the widened query - only that predicate changed",
      sql_line(o1) == "SQL: `SELECT room_no, area_m2 FROM \"bld_rooms\" WHERE "
                      "lower(trim(dept)) = lower(trim('Kiln Bay (Wet)')) ORDER BY room_no`",
      sql_line(o1))
check("RED: the RESULT SHAPE counts the widened rows", "RESULT SHAPE - rows: 5; shown: 5" in o1,
      o1[:400])
m1 = (matched(o1) or [""])[0]
check("RED: the MATCHED line names the filter as written and both spellings with their row "
      "counts", m1 == MT + "dept = 'Kiln Bay (Wet)' (in any letter case or spacing: the table "
                           "prints 'Kiln Bay (Wet)' on 2 rows and 'Kiln Bay (wet)' on 3 rows)", m1)
check("RED: the widened query ran exactly once", sum(
    1 for q in QUERIES if "lower(trim(dept)) = lower(trim('Kiln Bay (Wet)'))" in q) == 1, QUERIES)

o1b = run("SELECT SUM(area_m2) AS total FROM \"bld_rooms\" WHERE dept = 'Kiln Bay (Wet)'")
check("RED: an aggregate is computed over both spellings: 106.0, not 30.5",
      table_rows(o1b) == [["106.0"]], table_rows(o1b))
check("RED: and its MATCHED line names both spellings",
      "'Kiln Bay (Wet)' on 2 rows and 'Kiln Bay (wet)' on 3 rows" in (matched(o1b) or [""])[0],
      matched(o1b))

# ---------------------------------------------------------------------------
print("\n2. The same for an IN list (RED)")
# ---------------------------------------------------------------------------
q2 = ("SELECT room_no FROM \"bld_rooms\" WHERE dept IN ('Kiln Bay (Wet)', 'Print Shop') "
      "ORDER BY room_no")
o2 = run(q2)
check("RED: the IN list is widened - all five kiln rows and both print-shop rows",
      first_cells(o2) == KB_ALL + ["PS-1", "PS-2"], first_cells(o2))
check("RED: every literal of the list is folded the same way",
      sql_line(o2) == "SQL: `SELECT room_no FROM \"bld_rooms\" WHERE lower(trim(dept)) IN "
                      "(lower(trim('Kiln Bay (Wet)')), lower(trim('Print Shop'))) ORDER BY "
                      "room_no`", sql_line(o2))
check("RED: the MATCHED line names every spelling of every literal, with its rows",
      (matched(o2) or [""])[0] == MT + "dept IN ('Kiln Bay (Wet)', 'Print Shop') (in any letter "
      "case or spacing: the table prints 'Kiln Bay (Wet)' on 2 rows, 'Kiln Bay (wet)' on 3 rows "
      "and 'Print Shop' on 2 rows)", matched(o2))

# ---------------------------------------------------------------------------
print("\n3. A column holding no variant of the literal is never touched")
# ---------------------------------------------------------------------------
q3 = "SELECT room_no, area_m2 FROM \"bld_rooms\" WHERE dept = 'Print Shop' ORDER BY room_no"
o3 = run(q3)
expected3 = ("| room_no | area_m2 |\n| --- | --- |\n| PS-1 | 7.0 |\n| PS-2 | 8.0 |\n\n"
             f"SQL: `{q3}`\n\n" + MT + "dept = 'Print Shop'")
check("the result text is byte for byte what it was before this change", o3 == expected3,
      repr(o3))
check("the query was never rewritten or re-run - and a column holding no other spelling "
      "costs no extra query at all (its loaded rows are read first)", QUERIES == [q3], QUERIES)
o3b = run("SELECT room_no FROM \"bld_rooms\" WHERE dept = 'No Such Dept'")
check("a literal no row holds in any spelling: still no rows, the query untouched",
      o3b == "Query returned no results.\n\nSQL: `SELECT room_no FROM \"bld_rooms\" WHERE dept = "
             "'No Such Dept'`", o3b)

# ---------------------------------------------------------------------------
print("\n4. Spacing, a literal only a variant matches, a shown column, a join (RED)")
# ---------------------------------------------------------------------------
o4a = run("SELECT room_no FROM \"bld_rooms\" WHERE dept = 'Pottery Hall' ORDER BY room_no")
check("RED: a value the table prints with a trailing space is the same value",
      first_cells(o4a) == ["PH-1", "PH-2"], first_cells(o4a))
check("RED: and both spellings are named, the spaced one quoted with its space",
      "'Pottery Hall' on 1 row and 'Pottery Hall ' on 1 row" in (matched(o4a) or [""])[0],
      matched(o4a))
o4b = run("SELECT room_no FROM \"bld_rooms\" WHERE dept = 'KILN BAY (WET)' ORDER BY room_no")
check("RED: a literal the column prints only in other cases matches them, rather than nothing",
      first_cells(o4b) == KB_ALL, o4b[:300])
check("RED: the MATCHED line says which spellings the table prints",
      "'Kiln Bay (Wet)' on 2 rows and 'Kiln Bay (wet)' on 3 rows" in (matched(o4b) or [""])[0],
      matched(o4b))
o4c = run("SELECT room_no, dept FROM \"bld_rooms\" WHERE dept = 'Kiln Bay (Wet)' ORDER BY room_no")
check("RED: with the column shown, the rows of both spellings come back",
      first_cells(o4c) == KB_ALL, first_cells(o4c))
check("RED: and the MATCHED line still names the spellings, so two printed forms read as one "
      "value", "on 2 rows and 'Kiln Bay (wet)' on 3 rows" in (matched(o4c) or [""])[0],
      matched(o4c))
o4d = run("SELECT r.room_no, w.wing_label FROM \"bld_rooms\" r JOIN \"bld_wings\" w "
          "ON w.wing = r.wing WHERE r.dept = 'Kiln Bay (Wet)' ORDER BY r.room_no")
check("RED: a qualified column of a joined table is widened through its alias",
      first_cells(o4d) == KB_ALL and "lower(trim(r.dept)) = lower(trim('Kiln Bay (Wet)'))"
      in sql_line(o4d), (first_cells(o4d), sql_line(o4d)))
o4e = run("SELECT room_no FROM \"bld_rooms\" WHERE wing = 'S' AND dept = 'Kiln Bay (Wet)' "
          "ORDER BY room_no")
check("RED: only the predicate with a variant is widened; the one beside it stays as written",
      first_cells(o4e) == ["KB-3", "KB-4", "KB-5"]
      and "wing = 'S' AND lower(trim(dept)) = lower(trim('Kiln Bay (Wet)'))" in sql_line(o4e)
      and (matched(o4e) or [""])[0].startswith(MT + "wing = 'S' AND dept = 'Kiln Bay (Wet)' (in "),
      (first_cells(o4e), sql_line(o4e), matched(o4e)))

# ---------------------------------------------------------------------------
print("\n5. Shapes that are never widened")
# ---------------------------------------------------------------------------
for label5, sql5, rows5 in [
    ("an ILIKE, which already ignores case",
     "SELECT room_no FROM \"bld_rooms\" WHERE dept ILIKE 'Kiln Bay (Wet)' ORDER BY room_no", KB_ALL),
    ("an OR at the top of the WHERE",
     "SELECT room_no FROM \"bld_rooms\" WHERE dept = 'Kiln Bay (Wet)' OR room_no = 'PS-1' "
     "ORDER BY room_no", ["KB-1", "KB-2", "PS-1"]),
    ("a filter only inside a sub-SELECT",
     "SELECT room_no FROM \"bld_rooms\" WHERE room_no IN (SELECT room_no FROM \"bld_rooms\" "
     "WHERE dept = 'Kiln Bay (Wet)') ORDER BY room_no", ["KB-1", "KB-2"]),
    ("a negation", "SELECT room_no FROM \"bld_rooms\" WHERE dept <> 'Kiln Bay (Wet)' AND "
                   "wing = 'N' ORDER BY room_no", ["PH-1", "PS-1"]),
    ("a set operation",
     "SELECT room_no FROM \"bld_rooms\" WHERE dept = 'Kiln Bay (Wet)' UNION ALL "
     "SELECT room_no FROM \"bld_rooms\" WHERE dept = 'Print Shop' ORDER BY room_no",
     ["KB-1", "KB-2", "PS-1", "PS-2"]),
    ("a numeric column compared with a quoted number",
     "SELECT room_no FROM \"bld_rooms\" WHERE area_m2 = '10.5'", ["KB-1"]),
]:
    out5 = run(sql5)
    check(f"{label5}: the query ran as written", sql_line(out5) == f"SQL: `{sql5}`",
          sql_line(out5))
    check(f"{label5}: its own rows, nothing widened", first_cells(out5) == rows5,
          first_cells(out5))

# ---------------------------------------------------------------------------
print("\n6. An error drops the widening and nothing else")
# ---------------------------------------------------------------------------
real_exec = sql_tool._execute_with_timeout
calls6 = []


def _fail_after_first(con, query, timeout=sql_tool.SQL_QUERY_TIMEOUT):
    calls6.append(query)
    if len(calls6) > 1:
        raise RuntimeError("probe failed")
    return real_exec(con, query, timeout)


sql_tool._execute_with_timeout = _fail_after_first
try:
    o6 = run(q1)
finally:
    sql_tool._execute_with_timeout = real_exec
check("RED: the probe really was attempted", len(calls6) >= 2, calls6)
check("the result is the query's own: its two rows, the SQL as written, no failure text",
      first_cells(o6) == ["KB-1", "KB-2"] and sql_line(o6) == f"SQL: `{q1}`"
      and "SQL query failed" not in o6, o6[:400])
check("and its MATCHED line is the plain one", matched(o6) == [MT + "dept = 'Kiln Bay (Wet)'"],
      matched(o6))

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
