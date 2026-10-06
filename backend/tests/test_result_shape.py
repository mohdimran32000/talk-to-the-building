"""test_result_shape.py — a SQL result says what it IS before the writer reads it (2026-09-18).

The owner's question "list the specs of all cctvs" joined 88 cameras to 4 spec rows each =
352 rows, cut to 50; the writer called it "352 units, all one model". The tool now states
the shape: total rows, distinct items, and a breakdown of any low-variety column.

Fix round 1 (2026-09-18 review, controller rulings): key distinct/breakdown by column
INDEX, not name (a join returns duplicate column names, C1); the breakdown counts DISTINCT
identifiers per value when an identifier-like column exists, and rows otherwise, naming its
base, notation "value (N)" never "x" (C2/C3); skip breakdowns on all-numeric columns and on
columns where every value occurs once (k == n); omit the distinct clause when the
identifier column has <= 1 distinct value; values get whitespace/newline collapsed,
truncated at 40 chars, and a breakdown line is capped at 300 chars — never a line starting
with "|"; the block is renamed "RESULT SHAPE" so it cannot collide with
CHANGE_IMPACT_ANSWER_SHAPE in the prompt; the unseen-rows prohibition is its own
unconditional bullet, with a rule against printing the block itself.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import sql_tool, openai_client
FAILS = []
def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond: FAILS.append(name)

# ---------------------------------------------------------------------------
print("1. The camera x spec join — the brief's own fixture, updated notation")
# ---------------------------------------------------------------------------
cols = ["camera_tag", "model", "parameter", "value"]
rows = []
for i in range(88):
    m = "IMP231-1IS" if i < 65 else "IMP231-1IRS"
    for p in ("Back Box", "Description", "Lens", "Resolution"):
        rows.append((f"CAM-{i:03d}", m, p, "x"))
s = sql_tool._result_shape(cols, rows, shown=50)
check("states total and shown", "rows: 352" in s and "shown: 50" in s, s)
check("states the distinct count of the identifier-like column", "distinct camera_tag: 88" in s, s)
check("breaks down the low-variety column by DISTINCT camera_tag, not row count, and names its base",
      "model (per distinct camera_tag): IMP231-1IS (65), IMP231-1IRS (23)" in s, s)
check("does not break down a high-variety column", "CAM-000" not in s, s)
check("a single-row result has no SHAPE block", sql_tool._result_shape(["n"], [(68,)], shown=1) == "")
check("a 4-row result gets one", sql_tool._result_shape(["k", "v"], [("a", 1), ("a", 2), ("b", 3), ("b", 4)], shown=4).startswith("RESULT SHAPE"))
check("the block is labelled RESULT SHAPE, not the bare word SHAPE alone at line start",
      s.startswith("RESULT SHAPE"))
import inspect
check("execute_sql_query appends the shape to its result", "_result_shape(" in inspect.getsource(sql_tool.execute_sql_query))

# ---------------------------------------------------------------------------
print("\n2. C1 — duplicate column names on a join must not cross-contaminate")
# ---------------------------------------------------------------------------
# a.tag / a.v / b.tag / b.v — same names twice, as DuckDB really returns them on a join
dup_cols = ["tag", "v", "tag", "v"]
dup_rows = [
    ("t1", "x", "t1", "q"),
    ("t2", "x", "t2", "q"),
    ("t3", "x", "t3", "q"),
    ("t4", "y", "t4", "q"),
    ("t5", "y", "t5", "p"),
    ("t6", "y", "t6", "p"),
]
ds = sql_tool._result_shape(dup_cols, dup_rows, shown=6)
v_lines = [ln for ln in ds.split("\n") if ln.startswith("v (")]
check("both same-named 'v' columns get their OWN breakdown line (2 lines, not 1 merged)",
      len(v_lines) == 2, ds)
if len(v_lines) == 2:
    check("column-2's real distribution (a.v: x=3, y=3) is stated, not overwritten by column-4's",
          ("x (3)" in v_lines[0] and "y (3)" in v_lines[0]) or ("x (3)" in v_lines[1] and "y (3)" in v_lines[1]), ds)
    check("column-4's real distribution (b.v: q=4, p=2) is stated on its OWN line, not merged into column-2's",
          ("q (4)" in v_lines[0] and "p (2)" in v_lines[0]) or ("q (4)" in v_lines[1] and "p (2)" in v_lines[1]), ds)
    check("no single 'v' line states both distributions at once (the C1 bug)",
          not any(("x (3)" in ln and "q (4)" in ln) for ln in v_lines), ds)

# ---------------------------------------------------------------------------
print("\n3. C2 — an all-numeric column is never broken down (reads as a product)")
# ---------------------------------------------------------------------------
num_cols = ["item", "watt_per_unit"]
num_rows = [("A", "800"), ("B", "800"), ("C", "350"), ("D", "350"), ("E", "350"), ("F", "200")]
ns = sql_tool._result_shape(num_cols, num_rows, shown=6)
check("an all-numeric column gets no breakdown line at all",
      "watt_per_unit (" not in ns, ns)

# ---------------------------------------------------------------------------
print("\n4. k == n is skipped — a breakdown that restates the table one-for-one")
# ---------------------------------------------------------------------------
tie_cols = ["idA", "idB"]
tie_rows = [("A1", "B1"), ("A2", "B2"), ("A3", "B3"), ("A4", "B4")]
ts = sql_tool._result_shape(tie_cols, tie_rows, shown=4)
check("neither fully-unique column (k==n==4) gets a breakdown line, even the one that lost the identifier tie-break",
      "idA (" not in ts and "idB (" not in ts, ts)

# ---------------------------------------------------------------------------
print("\n5. distinct <= 1 omits the header clause entirely")
# ---------------------------------------------------------------------------
flat_cols = ["a", "b"]
flat_rows = [("x", "y")] * 5
fs = sql_tool._result_shape(flat_cols, flat_rows, shown=5)
check("a 5-row result where every column has exactly 1 distinct value carries no 'distinct' clause",
      "distinct" not in fs, fs)
check("but still states rows/shown", "rows: 5" in fs and "shown: 5" in fs, fs)

# ---------------------------------------------------------------------------
print("\n6. Value hygiene — newline + pipe, 40-char truncation, 300-char line cap")
# ---------------------------------------------------------------------------
hygiene_cols = ["item", "note"]
hygiene_rows = [
    ("A", "ok"), ("B", "ok"), ("C", "ok"),
    ("D", "line1\nline2 | this looks like a table row"),
    ("E", "line1\nline2 | this looks like a table row"),
]
hs = sql_tool._result_shape(hygiene_cols, hygiene_rows, shown=5)
check("no line of the block starts with '|' even though a value contains one after a newline",
      not any(ln.startswith("|") for ln in hs.split("\n")), hs)
check("the value's embedded newline is collapsed, not left to fragment the block into an extra line",
      hs.count("\n") == 1, hs)  # header line + one breakdown line, nothing extra from the embedded \n

long_val = "x" * 50
trunc_cols = ["item", "descr"]
trunc_rows = [("A", long_val), ("B", long_val), ("C", "short"), ("D", "short")]
tr = sql_tool._result_shape(trunc_cols, trunc_rows, shown=4)
check("a 50-char value is truncated to <= 40 displayed characters, with an ellipsis",
      long_val not in tr and "…" in tr, tr)
descr_line = [ln for ln in tr.split("\n") if ln.startswith("descr (")][0]
_, _, descr_values_part = descr_line.partition("): ")
check("the truncated value in the line is at most 40 characters long",
      all(len(part.rsplit(" (", 1)[0]) <= 40 for part in descr_values_part.split(", ")), descr_line)

cap_cols = ["item", "longcol"]
long_vals = [f"value number {i:02d} is quite a long piece of descriptive text here" for i in range(8)]
# 10 rows, 8 distinct longcol values (2 repeated) so k=8 (in range, and < n=10) — a
# k==n result would be skipped by the k==n rule tested in section 4, which is not what
# this case is exercising.
cap_rows = [(f"row{i}", long_vals[i]) for i in range(8)] + [("row8", long_vals[0]), ("row9", long_vals[1])]
cs = sql_tool._result_shape(cap_cols, cap_rows, shown=10)
cap_line = [ln for ln in cs.split("\n") if ln.startswith("longcol (")][0]
check("a breakdown line built from 8 long values is capped at 300 characters",
      len(cap_line) <= 300, f"len={len(cap_line)}")
check("a capped line ends with an ellipsis", cap_line.endswith("…"), cap_line)

# ---------------------------------------------------------------------------
print("\n7. The answer rules — unseen-rows bullet lifted out, RESULT SHAPE never printed")
# ---------------------------------------------------------------------------
r = openai_client.OUTPUT_FORMAT_RULES
check("the answer rule no longer says 'never truncate'", "never truncate" not in r)
check("the answer rule forbids describing rows the model did not see", "not shown" in r.lower() or "were not shown" in r.lower(), r[:300])
check("the answer rule names RESULT SHAPE as the source of the row and per-value counts",
      "RESULT SHAPE" in r)
check("the never-print rule for RESULT SHAPE exists",
      "never print" in r.lower() and "RESULT SHAPE" in r, r)

# the prohibition must be its own bullet, not nested inside the "DOES ask for a list" sentence
list_bullet_start = r.find('When the user DOES ask for a list')
list_bullet_end = r.find("MANDATORY", list_bullet_start)
list_bullet_end = r.find(".", list_bullet_end) + 1 if list_bullet_end != -1 else -1
check("both markers found in the rules text", list_bullet_start != -1 and list_bullet_end > list_bullet_start, r)
list_bullet_text = r[list_bullet_start:list_bullet_end] if list_bullet_start != -1 and list_bullet_end != -1 else ""
check("the unseen-rows prohibition is NOT inside the 'DOES ask for a list' sentence",
      "NEVER describe, count or generalise" not in list_bullet_text, list_bullet_text)
check("the unseen-rows prohibition DOES exist elsewhere in the rules, as its own bullet",
      "NEVER describe, count or generalise about rows you were not shown" in r, r)
check("the unconditional bullet applies whatever the question's form",
      "whatever the question" in r.lower(), r)

# ---------------------------------------------------------------------------
print("\n8. Bullets 2 and 3 no longer disagree about where a total comes from (I4)")
# ---------------------------------------------------------------------------
# 2026-09-19 (final fix wave, final-review finding I4). Bullet 2 MANDATES a Total
# row on any table with a quantity column; bullet 3 said "take every total and
# per-value count from that line", naming the RESULT SHAPE line - which carries
# row/shown counts and per-value distributions and NO SUMS AT ALL. The sums are on
# the result's own "TOTAL <col> (all N rows)" line (sql_tool, emitted for
# quantity-like columns on results of 4+ rows), which bullet 3 never named. So the
# two bullets sent the answer-writing model to two different places and neither one
# to the figure - and the fallback it is left with is summing the rows it was
# SHOWN, which on a truncated result is a wrong total presented as the whole.
check("the rules name the TOTAL <col> (all N rows) line as the source of sums",
      "TOTAL <col> (all N rows)" in r, r)
check("the old 'take every total ... from that line' wording is gone",
      "take every total" not in r, r)
check("RESULT SHAPE is named for the row/shown and per-value COUNTS",
      "per-value count" in r.lower() and "RESULT SHAPE" in r, r)
check("and it is stated that the RESULT SHAPE line carries no sums",
      "carries no sums" in r.lower(), r)
check("a rendered table's Total row is that TOTAL figure, never a sum over the shown rows",
      "never a sum over the rows you were shown" in r.lower(), r)

# ---------------------------------------------------------------------------
print("\n9. Printed total rows are never counted as items (spec-fix6)")
# ---------------------------------------------------------------------------
# A table can keep a sheet's own printed TOTAL rows beside the item rows they total,
# marked by a row-kind column whose card vocabulary lists a 'total' value. A SUM that
# never names that column adds the printed total to the items it totals - the measured
# failure was exactly that. The result now says so, with both figures recomputed in code:
# the same SQL re-run without the printed total rows, and over them alone.


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


def run_sql(sql, tables, cards=()):
    """`execute_sql_query` end to end with a fake SQL writer that returns `sql`."""
    saved = (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
             sql_tool._load_table_cards)
    sql_tool.genai.Client = _Client
    sql_tool.get_llm_api_key = lambda: "fake-key"
    sql_tool.get_llm_model = lambda: "fake-model"
    sql_tool._load_table_cards = lambda *a, **k: list(cards)
    _Models.sql = sql
    try:
        return sql_tool.execute_sql_query("q", "u-1", _Supabase(tables))
    finally:
        (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
         sql_tool._load_table_cards) = saved


POINTS = {
    "table_name": "bld_points",
    "columns": ["panel", "item", "kind", "di", "do"],
    "rows": [
        {"panel": "KP-7", "item": "Header row", "kind": "section", "di": "", "do": ""},
        {"panel": "KP-7", "item": "Pump A", "kind": "item", "di": "4", "do": "2"},
        {"panel": "KP-7", "item": "Pump B", "kind": "item", "di": "3", "do": "1"},
        {"panel": "KP-7", "item": "Fan bank", "kind": "group", "di": "5", "do": "1"},
        {"panel": "KP-7", "item": "Total points", "kind": "total", "di": "7", "do": "3"},
        {"panel": "KP-8", "item": "Pump C", "kind": "item", "di": "2", "do": "2"},
        {"panel": "KP-8", "item": "Total points", "kind": "Total", "di": "2", "do": "2"},
    ],
    "row_count": 7,
}
POINTS_CARD = {"table": "bld_points", "holds": "one row per point type of a control panel",
               "value_vocabulary": {"kind": ["section", "group", "item", "total"]}}
PT = "PRINTED TOTAL ROWS - "

pt1 = run_sql("SELECT SUM(di) FROM \"bld_points\" WHERE panel = 'KP-7'", [POINTS], [POINTS_CARD])
check("the combined figure the query returned is still the table's", "| 19.0 |" in pt1, pt1)
check("RED: a SUM over a table whose card enumerates a total row kind, with no filter on "
      "that column, carries PRINTED TOTAL ROWS", PT in pt1, pt1[-400:])
check("RED: with both figures recomputed in code - without the printed total rows, and "
      "the printed total rows alone",
      "without them: 12.0; the printed total row(s) alone: 7.0" in pt1, pt1[-400:])
check("RED: the line says the figure above counts the sheet's own printed total rows as items",
      "the figure above counts the sheet's own printed total row(s) as items" in pt1, pt1[-400:])
check("it is one line, after the SQL line, never inside the rendered table",
      PT in pt1 and pt1.index("SQL: `") < pt1.index(PT)
      and pt1.count(PT) == 1 and not any(l.startswith("|") and PT in l for l in pt1.splitlines()),
      pt1)

pt2 = run_sql("SELECT SUM(di) FROM \"bld_points\" WHERE panel = 'KP-7' AND kind = 'item'",
              [POINTS], [POINTS_CARD])
check("a SUM that filters the row-kind column carries nothing", PT not in pt2 and "| 7.0 |" in pt2,
      pt2[-300:])
pt2b = run_sql("SELECT SUM(di) FROM \"bld_points\" WHERE panel = 'KP-7' AND \"kind\" <> 'total'",
               [POINTS], [POINTS_CARD])
check("- nor one that names it in any other way (a quoted name, a negation)", PT not in pt2b,
      pt2b[-300:])
pt2c = run_sql("SELECT SUM(CASE WHEN kind <> 'total' THEN di END) AS items, "
               "SUM(CASE WHEN kind = 'total' THEN di END) AS printed FROM \"bld_points\" "
               "WHERE panel = 'KP-7'", [POINTS], [POINTS_CARD])
check("the shape the prompt rule asks for - the printed total in its own column beside the "
      "items - carries nothing, though re-running it without the total rows would change it",
      PT not in pt2c and "| 12.0 | 7.0 |" in pt2c, pt2c[-300:])
pt3 = run_sql("SELECT SUM(di) FROM \"bld_points\" WHERE item = 'Pump A'", [POINTS], [POINTS_CARD])
check("a SUM whose rows include no printed total row carries nothing (the figure is "
      "already without them)", PT not in pt3, pt3[-300:])
pt4 = run_sql("SELECT item, di FROM \"bld_points\" WHERE panel = 'KP-7'", [POINTS], [POINTS_CARD])
check("a row list, which is no aggregate, carries nothing", PT not in pt4, pt4[-300:])
pt5 = run_sql("SELECT SUM(di) FROM \"bld_points\" WHERE panel = 'KP-7'", [POINTS],
              [{"table": "bld_points", "holds": "h",
                "value_vocabulary": {"kind": ["section", "group", "item"]}}])
check("a card whose vocabulary names no total kind: nothing", PT not in pt5, pt5[-300:])
pt6 = run_sql("SELECT SUM(di) FROM \"bld_points\" WHERE panel = 'KP-7'", [POINTS], [])
check("no card at all: nothing", PT not in pt6, pt6[-300:])
OTHER = {"table_name": "bld_other", "columns": ["panel", "label"],
         "rows": [{"panel": "KP-7", "label": "x"}], "row_count": 1}
pt7 = run_sql("SELECT SUM(p.di) FROM \"bld_points\" p JOIN \"bld_other\" o ON o.panel = p.panel",
              [POINTS, OTHER], [POINTS_CARD, {"table": "bld_other", "holds": "h"}])
check("an aggregate reading TWO tables carries nothing", PT not in pt7 and "SQL query failed"
      not in pt7, pt7[-300:])
# Review fix round 1: a query that read ONLY printed total rows - filtered to them by another
# column - is a question about the printed total itself. Its figure IS the printed total, so
# there is nothing counted twice and nothing to say: the re-run without them holds nothing.
pt7b = run_sql("SELECT SUM(di) FROM \"bld_points\" WHERE item = 'Total points'", [POINTS],
               [POINTS_CARD])
check("RED: a SUM over only the printed total rows (filtered by another column) carries "
      "nothing - without them the re-run is all NULL", PT not in pt7b and "| 9.0 |" in pt7b,
      pt7b[-300:])
pt7c = run_sql("SELECT COUNT(*) FROM \"bld_points\" WHERE item = 'Total points' AND "
               "panel = 'KP-7'", [POINTS], [POINTS_CARD])
check("RED: a COUNT over only the printed total rows carries nothing - without them it is a "
      "lone 0", PT not in pt7c, pt7c[-300:])
pt7d = run_sql("SELECT panel, SUM(di) AS s FROM \"bld_points\" WHERE item = 'Total points' "
               "GROUP BY panel ORDER BY panel", [POINTS], [POINTS_CARD])
check("a grouped SUM over only the printed total rows carries nothing - without them there "
      "are no rows at all", PT not in pt7d, pt7d[-300:])

pt8 = run_sql("SELECT SUM(di), SUM(\"do\") FROM \"bld_points\" WHERE panel = 'KP-7'",
              [POINTS], [POINTS_CARD])
check("RED: several figures in one row are each named with their column",
      "without them: sum(di) = 12.0, sum(\"do\") = 4.0; the printed total row(s) alone: "
      "sum(di) = 7.0, sum(\"do\") = 3.0" in pt8, pt8[-400:])
pt9 = run_sql("SELECT panel, COUNT(*) AS n FROM \"bld_points\" GROUP BY panel ORDER BY panel",
              [POINTS], [POINTS_CARD])
check("RED: a grouped COUNT names every group, and a total value is matched whatever its case",
      "without them: (panel = KP-7, n = 4), (panel = KP-8, n = 1); the printed total row(s) "
      "alone: (panel = KP-7, n = 1), (panel = KP-8, n = 1)" in pt9, pt9[-400:])
GRAND = dict(POINTS, rows=[dict(r, kind=("Grand Total" if r["kind"].lower() == "total"
                                         else r["kind"])) for r in POINTS["rows"]])
pt10 = run_sql("SELECT SUM(di) FROM \"bld_points\" WHERE panel = 'KP-7'", [GRAND],
               [{"table": "bld_points", "holds": "h",
                 "value_vocabulary": {"kind": ["item", "Grand Total"]}}])
check("RED: 'grand total' is a printed total kind too",
      "without them: 12.0; the printed total row(s) alone: 7.0" in pt10, pt10[-300:])

# The re-run shadows the table under its own name; afterwards the table must be the table.
import duckdb  # noqa: E402
con9 = duckdb.connect(":memory:")
con9.execute('CREATE TABLE "bld_points" ("panel" VARCHAR, "item" VARCHAR, "kind" VARCHAR, '
             '"di" DOUBLE, "do" DOUBLE)')
for row in POINTS["rows"]:
    con9.execute('INSERT INTO "bld_points" VALUES (?, ?, ?, ?, ?)',
                 [row["panel"], row["item"], row["kind"],
                  float(row["di"]) if row["di"] else None, float(row["do"]) if row["do"] else None])
q9 = "SELECT SUM(di) FROM \"bld_points\" WHERE panel = 'KP-7'"
line9 = sql_tool._printed_total_rows_line(con9, q9, [POINTS], [POINTS_CARD], ["sum(di)"],
                                          con9.execute(q9).fetchall()) \
    if hasattr(sql_tool, "_printed_total_rows_line") else ""
check("RED: called on its own, it builds the line", line9.startswith(PT), line9)
check("and leaves the table exactly as loaded - no shadow survives the call",
      con9.execute(q9).fetchall() == [(19.0,)]
      and con9.execute("SELECT COUNT(*) FROM duckdb_views() WHERE NOT internal").fetchall()
      == [(0,)], con9.execute(q9).fetchall())

real_exec9 = sql_tool._execute_with_timeout
calls9 = []


def _fail_reruns(con, sql, timeout=sql_tool.SQL_QUERY_TIMEOUT):
    calls9.append(sql)
    if len(calls9) > 1:
        raise RuntimeError("re-run failed")
    return real_exec9(con, sql, timeout)


sql_tool._execute_with_timeout = _fail_reruns
try:
    pt11 = run_sql("SELECT SUM(di) FROM \"bld_points\" WHERE panel = 'KP-7'", [POINTS],
                   [POINTS_CARD])
finally:
    sql_tool._execute_with_timeout = real_exec9
check("RED: the re-run was really attempted", len(calls9) >= 2, calls9)
check("a re-run that raises drops the line and nothing else",
      PT not in pt11 and "| 19.0 |" in pt11 and "SQL query failed" not in pt11, pt11[-300:])

r9 = openai_client.OUTPUT_FORMAT_RULES
pt_bullet = next((l for l in r9.splitlines() if "PRINTED TOTAL ROWS" in l), "")
check("RED: the answer rules have a PRINTED TOTAL ROWS bullet", bool(pt_bullet))
check("RED: the figure WITHOUT the printed total rows is the answer",
      "without" in pt_bullet.lower() and "is the answer" in pt_bullet.lower(), pt_bullet)
check("RED: the printed total is named beside it as the document's own figure",
      "document's own" in pt_bullet.lower(), pt_bullet)
check("RED: the combined figure is never stated",
      "never state the combined figure" in pt_bullet.lower(), pt_bullet)
check("RED: and the line itself is never printed", "never print" in pt_bullet.lower(), pt_bullet)

# ---------------------------------------------------------------------------
print("\n10. A result says what its rows matched (spec-fix10)")
# ---------------------------------------------------------------------------
# A query that filters on a text column it does not select returns rows that never print
# the thing they matched - three unlabelled rows of areas and a department, the name the
# question asked about only inside the SQL line the writer is told to apply silently - and
# the answer then said no record of it exists. The result now states the filter, quoting the
# column and the literal exactly as the SQL wrote them, and never as "these are X".
SPACES = {
    "table_name": "bld_spaces",
    "columns": ["space_no", "space_name", "area_m2", "dept"],
    "rows": [
        {"space_no": "9.71", "space_name": "Quiet Hall", "area_m2": "40.5", "dept": "Studio Arts"},
        {"space_no": "9.72", "space_name": "Quiet Hall Annex", "area_m2": "12.0",
         "dept": "Studio Arts"},
        {"space_no": "9.73", "space_name": "Plant Store", "area_m2": "8.0", "dept": "Services"},
    ],
    "row_count": 3,
}
MT = "MATCHED - every row above has "


def matched_lines(text):
    return [l for l in text.splitlines() if l.startswith("MATCHED")]


m1 = run_sql("SELECT space_no, area_m2, dept FROM \"bld_spaces\" WHERE space_name ILIKE "
             "'%Quiet Hall%'", [SPACES])
check("RED: a result filtered on an unselected text column carries MATCHED with the column "
      "and the literal", matched_lines(m1) == [MT + "space_name ILIKE '%Quiet Hall%'"],
      matched_lines(m1) or m1[-300:])
check("it never says what the rows ARE - only the filter they meet",
      "these are" not in m1.lower() and "these rows are" not in m1.lower(), m1[-300:])
check("it is one line after the SQL line, never inside the rendered table",
      MT in m1 and m1.index("SQL: `") < m1.index(MT)
      and not any(l.startswith("|") and MT in l for l in m1.splitlines()), m1)

m2 = run_sql("SELECT space_no, space_name FROM \"bld_spaces\" WHERE space_name ILIKE "
             "'%Quiet Hall%'", [SPACES])
check("a filter on a column the result SHOWS: no MATCHED line", matched_lines(m2) == [], m2[-300:])
m3 = run_sql("SELECT space_no FROM \"bld_spaces\" WHERE space_name = 'Quiet Hall' OR "
             "dept = 'Services'", [SPACES])
check("an OR at the top of the WHERE guarantees no single condition: no MATCHED line",
      matched_lines(m3) == [], m3[-300:])
m4 = run_sql("SELECT space_no, dept FROM \"bld_spaces\" WHERE dept = 'Studio Arts' AND "
             "space_name ILIKE '%Annex%'", [SPACES])
check("RED: of two ANDed filters, only the one on a column the result does not show is stated",
      matched_lines(m4) == [MT + "space_name ILIKE '%Annex%'"], matched_lines(m4) or m4[-300:])
m5 = run_sql("SELECT space_no FROM \"bld_spaces\" WHERE space_name IN ('Quiet Hall', "
             "'Plant Store') AND dept LIKE 'S%'", [SPACES])
check("RED: an IN list and a LIKE, each quoted verbatim, joined with AND",
      matched_lines(m5) == [MT + "space_name IN ('Quiet Hall', 'Plant Store') AND dept LIKE 'S%'"],
      matched_lines(m5) or m5[-300:])
m6 = run_sql("SELECT space_no FROM \"bld_spaces\" WHERE dept = 'Studio Arts' AND "
             "(space_name = 'Quiet Hall' OR space_name = 'Quiet Hall Annex')", [SPACES])
check("RED: an ANDed condition is stated; a bracketed OR group beside it is not",
      matched_lines(m6) == [MT + "dept = 'Studio Arts'"], matched_lines(m6) or m6[-300:])
m7 = run_sql("SELECT s.space_no FROM \"bld_spaces\" s WHERE s.\"space_name\" = 'Plant Store'",
             [SPACES])
check("RED: a qualified, quoted column is quoted exactly as written",
      matched_lines(m7) == [MT + "s.\"space_name\" = 'Plant Store'"], matched_lines(m7) or m7[-300:])
m8 = run_sql("SELECT SUM(area_m2) FROM \"bld_spaces\" WHERE dept = 'Studio Arts'", [SPACES])
check("RED: an aggregate's figure is computed over the rows it matched, and says so",
      matched_lines(m8) == [MT + "dept = 'Studio Arts'"], matched_lines(m8) or m8[-300:])
for label9, sql9 in {
    "a number comparison": "SELECT space_no FROM \"bld_spaces\" WHERE area_m2 > 10",
    "a negated pattern": "SELECT space_no FROM \"bld_spaces\" WHERE space_name NOT ILIKE '%Hall%'",
    "a NOT before the condition": ("SELECT space_no FROM \"bld_spaces\" WHERE NOT "
                                   "space_name = 'Plant Store'"),
    "a filter only inside a sub-SELECT": (
        "SELECT space_no FROM \"bld_spaces\" WHERE space_no IN (SELECT space_no FROM "
        "\"bld_spaces\" WHERE dept = 'Services')"),
    "a set operation whose second arm has no WHERE": (
        "SELECT space_no FROM \"bld_spaces\" WHERE dept = 'Services' UNION "
        "SELECT space_no FROM \"bld_spaces\""),
    # Each of the next three is a query where a plain filter IS readable on its own, and
    # stating it would be FALSE for some of the rows returned.
    "an AND that a later top-level OR overrides (AND binds tighter)": (
        "SELECT space_no FROM \"bld_spaces\" WHERE dept = 'Studio Arts' AND "
        "space_name = 'Quiet Hall' OR space_name = 'Plant Store'"),
    "a WHERE on the second arm of a set operation only": (
        "SELECT space_no FROM \"bld_spaces\" UNION SELECT space_no FROM \"bld_spaces\" "
        "WHERE dept = 'Services'"),
    "a filter between ANDs inside an EXISTS": (
        "SELECT space_no FROM \"bld_spaces\" WHERE EXISTS (SELECT 1 FROM \"bld_spaces\" s2 "
        "WHERE s2.space_no = \"bld_spaces\".space_no AND s2.dept = 'Studio Arts' AND "
        "s2.area_m2 > 1)"),
    "a function around the column": ("SELECT space_no FROM \"bld_spaces\" WHERE "
                                     "lower(space_name) = 'plant store'"),
    "a literal in a condition that goes on": ("SELECT space_no FROM \"bld_spaces\" WHERE "
                                              "dept = 'Serv' || 'ices'"),
}.items():
    out9 = run_sql(sql9, [SPACES])
    check(f"{label9}: the query ran, and no MATCHED line",
          "SQL query failed" not in out9 and matched_lines(out9) == [], matched_lines(out9) or out9[-300:])
m10 = run_sql("SELECT SUM(area_m2) FROM \"bld_spaces\" WHERE dept = 'Nobody'", [SPACES])
check("a figure computed over nothing (an all-empty row) gets no MATCHED line",
      matched_lines(m10) == [], m10[-300:])

# Review fix round 1: the line is capped. A long IN (...) list shows its first values,
# verbatim, and how many there are in all; anything still too long is cut and says so.
long_in = ", ".join(["'Quiet Hall'", "'Plant Store'"] + [f"'Filler {i:02d}'" for i in range(38)])
m11 = run_sql(f"SELECT area_m2 FROM \"bld_spaces\" WHERE space_name IN ({long_in}) AND "
              f"dept LIKE 'S%'", [SPACES])
line11 = (matched_lines(m11) or [""])[0]
check("RED: a MATCHED line over a 40-value IN list is capped at "
      f"{getattr(sql_tool, 'MATCHED_MAX_CHARS', 300)} characters",
      bool(line11) and len(line11) <= getattr(sql_tool, "MATCHED_MAX_CHARS", 300), len(line11))
check("RED: the list shows its first values verbatim and how many there are in all",
      "space_name IN ('Quiet Hall', 'Plant Store', 'Filler 00', … 40 values in all)" in line11,
      line11)
check("and the other condition beside it is still quoted whole",
      line11.endswith(" AND dept LIKE 'S%'"), line11)
long_pat = "zz-pattern-" * 40
m12 = run_sql(f"SELECT area_m2 FROM \"bld_spaces\" WHERE space_name NOT LIKE 'x' AND "
              f"dept <> '{long_pat}' AND space_name ILIKE '%Quiet%{long_pat}%' OR "
              f"space_name = 'Plant Store'", [SPACES])
check("(a long literal under a top-level OR still gets no line at all)", matched_lines(m12) == [],
      matched_lines(m12))
m14 =run_sql(f"SELECT area_m2 FROM \"bld_spaces\" WHERE dept = 'Services' AND "
              f"space_name NOT ILIKE '%{long_pat}%' AND space_no IN ('9.73', '{long_pat}')", [SPACES])
line14 = (matched_lines(m14) or [""])[0]
check("RED: a line that is still too long after the list is shortened is cut, and says so",
      bool(line14) and len(line14) <= getattr(sql_tool, "MATCHED_MAX_CHARS", 300)
      and line14.endswith("…"), (len(line14), line14[-40:]))
check("a short IN list is still quoted whole (unchanged)",
      matched_lines(m5) == [MT + "space_name IN ('Quiet Hall', 'Plant Store') AND dept LIKE 'S%'"],
      matched_lines(m5))

r10 = openai_client.OUTPUT_FORMAT_RULES
mt_bullet = next((l for l in r10.splitlines() if "MATCHED" in l), "")
check("RED: the answer rules have a MATCHED bullet", bool(mt_bullet))
# Review fix round 1 (ruling B1-R3): the bullet is CONDITIONAL. A MATCHED line lends a loose
# filter authority, so the rows are the asked-for thing only when the filter IS the name or
# value the question asked about; a pattern or fragment that could match other things must
# not be presented as that thing without saying what it matched.
check("the unconditional sentence is gone",
      "Those rows are the thing the question named" not in mt_bullet, mt_bullet)
check("the rows are the asked-for thing only WHEN the filter is the name or value the "
      "question asked about", "when that filter is the name or value the question asked about"
      in mt_bullet.lower() and "even if none of their own columns prints its name"
      in mt_bullet.lower(), mt_bullet)
check("RED: so the answer comes from them", "answer from them" in mt_bullet.lower(), mt_bullet)
check("a pattern or fragment that could match other things is never presented as the "
      "asked-for thing without saying what the rows matched",
      "pattern or a fragment" in mt_bullet.lower()
      and "without saying what they matched" in mt_bullet.lower(), mt_bullet)
check("RED: and the line is never printed", "never print" in mt_bullet.lower(), mt_bullet)

# ---------------------------------------------------------------------------
print("\n11. The hierarchy note is built from the tables the SQL READ (spec-fix10)")
# ---------------------------------------------------------------------------
# The parent-reference warning used to be built from every ROUTED table, so a query that
# read only a door table carried "this data is hierarchical (fed_from, parent_id) ... use
# only the topmost row(s)" because a board table and a place table had been routed beside
# it - and that warning then rode on a floor-AREA answer as well. Built now as the SOURCE
# lines already are: from the tables the SQL names.
DOORS = {"table_name": "bld_doors", "columns": ["door_id", "level", "readers"],
         "rows": [{"door_id": "DR-31", "level": "Lower Deck", "readers": "2"},
                  {"door_id": "DR-32", "level": "Lower Deck", "readers": "1"},
                  {"door_id": "DR-33", "level": "Upper Deck", "readers": "1"}], "row_count": 3}
BOARDS = {"table_name": "bld_boards", "columns": ["board", "fed_from", "kw"],
          "rows": [{"board": "BB-1", "fed_from": "", "kw": "40"},
                   {"board": "BB-2", "fed_from": "BB-1", "kw": "15"}], "row_count": 2}
PLACES = {"table_name": "bld_places", "columns": ["place_id", "parent_id", "label"],
          "rows": [{"place_id": "P-1", "parent_id": "", "label": "Lower Deck"}], "row_count": 1}
IMP = "IMPORTANT (for interpreting these results): this data is hierarchical"

h1 = run_sql("SELECT COUNT(*) FROM \"bld_doors\" WHERE level = 'Lower Deck'",
             [DOORS, BOARDS, PLACES])
check("RED: a doors-only SQL carries no fed_from/parent_id hierarchy note",
      IMP not in h1 and "fed_from" not in h1 and "parent_id" not in h1, h1[-400:])
h2 = run_sql("SELECT board, kw FROM \"bld_boards\"", [DOORS, BOARDS, PLACES])
check("a SQL reading the board table still carries the note",
      IMP in h2, h2[-300:])
check("RED: naming only the column the tables it read hold (fed_from, not parent_id)",
      "parent-reference column: fed_from)" in h2 and "parent_id" not in h2, h2[-300:])
h3 = run_sql("SELECT d.door_id, p.label FROM \"bld_doors\" d JOIN \"bld_places\" p "
             "ON p.label = d.level", [DOORS, BOARDS, PLACES])
check("RED: a join names the parent columns of every table it read, and only those",
      "parent-reference column: parent_id)" in h3 and "fed_from" not in h3, h3[-300:])

# ---------------------------------------------------------------------------
print("\n12. A pattern filter on a column a row list leaves out: that column is added (wave 3, G5)")
# ---------------------------------------------------------------------------
# Measured on the goal-function run of 2026-10-01: a pattern filter on a name column the SELECT
# left out also caught a longer name, and the MATCHED rule said not to present the rows as the
# asked-for thing without saying what they matched - which the result could not say. So a row
# list (no aggregate, no GROUP BY, no DISTINCT) whose top-level WHERE applies a pattern filter
# (LIKE / ILIKE, a wildcard in its literal) to a column the result does not show is re-run with
# that column appended: the same rows, each printing the value it matched. The MATCHED line still
# states the filter as the writer wrote it.
DEPTS = {"table_name": "bld_depts", "columns": ["dept", "head"],
         "rows": [{"dept": "Studio Arts", "head": "A. Head"},
                  {"dept": "Services", "head": "B. Head"}], "row_count": 2}


def header(text):
    return next((l for l in text.splitlines() if l.startswith("|")), "")


g12a_sql = ("SELECT space_no, area_m2, dept FROM \"bld_spaces\" WHERE space_name ILIKE "
            "'%Quiet Hall%'")
g12a = run_sql(g12a_sql, [SPACES])
check("RED: the rows now show the column the pattern filtered",
      header(g12a) == "| space_no | area_m2 | dept | space_name |", header(g12a))
check("RED: each row prints the value it matched - the longer name visibly so",
      "| 9.71 | 40.5 | Studio Arts | Quiet Hall |" in g12a
      and "| 9.72 | 12.0 | Studio Arts | Quiet Hall Annex |" in g12a, g12a[:400])
check("the same rows, no more", sum(1 for l in g12a.splitlines() if l.startswith("| 9.")) == 2,
      g12a[:400])
check("RED: the SQL line is the query that produced them",
      "SQL: `SELECT space_no, area_m2, dept, space_name FROM \"bld_spaces\" WHERE space_name "
      "ILIKE '%Quiet Hall%'`" in g12a, g12a[-400:])
check("the MATCHED line still states the filter as the writer wrote it",
      matched_lines(g12a) == [MT + "space_name ILIKE '%Quiet Hall%'"], matched_lines(g12a))

g12b = run_sql("SELECT space_no FROM \"bld_spaces\" WHERE space_name ILIKE '%Hall%' "
               "ORDER BY space_no DESC LIMIT 1", [SPACES])
check("RED: ORDER BY ... LIMIT keeps its one row, which now prints what it matched",
      header(g12b) == "| space_no | space_name |" and "| 9.72 | Quiet Hall Annex |" in g12b
      and "| 9.71 |" not in g12b, g12b[:300])
g12c = run_sql("SELECT space_no, COUNT(*) OVER () AS n FROM \"bld_spaces\" WHERE space_name "
               "ILIKE '%Hall%' ORDER BY space_no", [SPACES])
check("RED: a window function is no aggregate - one row per row read, so the column is added",
      header(g12c) == "| space_no | n | space_name |", header(g12c))
g12d = run_sql("SELECT s.space_no, d.head FROM \"bld_spaces\" s JOIN \"bld_depts\" d "
               "ON d.dept = s.dept WHERE s.space_name ILIKE '%Hall%' ORDER BY s.space_no",
               [SPACES, DEPTS])
check("RED: in a join the column is added as the writer qualified it",
      header(g12d) == "| space_no | head | space_name |"
      and "s.space_no, d.head, s.space_name FROM" in g12d, g12d[-400:])
g12e = run_sql("SELECT space_no FROM \"bld_spaces\" WHERE space_name ILIKE '%Hall%' AND "
               "dept LIKE 'Studio%'", [SPACES])
check("RED: two pattern filters on two columns add both, in the order written",
      header(g12e) == "| space_no | space_name | dept |", header(g12e))
g12f = run_sql("SELECT space_no FROM \"bld_spaces\" WHERE space_name ILIKE '%Quiet%' AND "
               "space_name ILIKE '%Hall%'", [SPACES])
check("RED: two pattern filters on one column add it once",
      header(g12f) == "| space_no | space_name |", header(g12f))

for label12, sql12, tables12 in (
    ("an equality filter", "SELECT space_no FROM \"bld_spaces\" WHERE space_name = 'Quiet Hall'",
     [SPACES]),
    ("an IN list", "SELECT space_no FROM \"bld_spaces\" WHERE space_name IN ('Quiet Hall', "
                   "'Plant Store')", [SPACES]),
    ("a LIKE with no wildcard", "SELECT space_no FROM \"bld_spaces\" WHERE space_name LIKE "
                                "'Quiet Hall'", [SPACES]),
    ("a column the result already shows", "SELECT space_no, space_name FROM \"bld_spaces\" "
                                          "WHERE space_name ILIKE '%Hall%'", [SPACES]),
    ("SELECT *", "SELECT * FROM \"bld_spaces\" WHERE space_name ILIKE '%Hall%'", [SPACES]),
    ("an aggregate", "SELECT SUM(area_m2) FROM \"bld_spaces\" WHERE space_name ILIKE '%Hall%'",
     [SPACES]),
    ("a GROUP BY", "SELECT dept, COUNT(*) AS n FROM \"bld_spaces\" WHERE space_name ILIKE "
                   "'%Hall%' GROUP BY dept", [SPACES]),
    ("a DISTINCT row list", "SELECT DISTINCT dept FROM \"bld_spaces\" WHERE space_name ILIKE "
                            "'%Hall%'", [SPACES]),
    ("a negated pattern", "SELECT space_no FROM \"bld_spaces\" WHERE space_name NOT ILIKE "
                          "'%Hall%'", [SPACES]),
    ("a pattern under a top-level OR", "SELECT space_no FROM \"bld_spaces\" WHERE space_name "
                                       "ILIKE '%Quiet%' OR dept = 'Services'", [SPACES]),
    ("a pattern only inside a sub-SELECT", "SELECT space_no FROM \"bld_spaces\" WHERE space_no "
                                           "IN (SELECT space_no FROM \"bld_spaces\" WHERE "
                                           "space_name ILIKE '%Hall%')", [SPACES]),
    ("a set operation", "SELECT space_no FROM \"bld_spaces\" WHERE space_name ILIKE '%Quiet%' "
                        "UNION SELECT space_no FROM \"bld_spaces\" WHERE dept = 'Services'",
     [SPACES]),
):
    out12 = run_sql(sql12, tables12)
    check(f"{label12}: the query ran and was not re-run - its SQL line is the writer's",
          "SQL query failed" not in out12 and f"SQL: `{sql12}`" in out12, out12[-300:])

# Found by the replay of the recorded queries: an EMPTY row list was re-run too, so its "no
# results" text quoted the re-run SQL instead of the writer's - and that SQL line is what the loop's
# EMPTY re-query hands back to the writer. A result that holds nothing (no rows, or only NULL, blank
# or a lone zero - the loop's own notion of empty) is never re-run.
g12y_sql = "SELECT space_no FROM \"bld_spaces\" WHERE space_name ILIKE '%Nowhere%'"
g12y = run_sql(g12y_sql, [SPACES])
check("RED: an empty row list is not re-run - its no-results text keeps the writer's SQL",
      "Query returned no results" in g12y and f"SQL: `{g12y_sql}`" in g12y
      and ", space_name FROM" not in g12y, g12y)
g12x_sql = "SELECT NULL AS x FROM \"bld_spaces\" WHERE space_name ILIKE '%Plant%'"
g12x = run_sql(g12x_sql, [SPACES])
check("RED: nor one whose cells hold nothing - it stays empty to the loop, with the writer's SQL",
      f"SQL: `{g12x_sql}`" in g12x and "Plant Store" not in g12x, g12x)

real_exec12 = sql_tool._execute_with_timeout
calls12 = []


def _fail_rerun12(con, sql, timeout=sql_tool.SQL_QUERY_TIMEOUT):
    calls12.append(sql)
    if len(calls12) > 1:
        raise RuntimeError("re-run failed")
    return real_exec12(con, sql, timeout)


sql_tool._execute_with_timeout = _fail_rerun12
try:
    g12z = run_sql(g12a_sql, [SPACES])
finally:
    sql_tool._execute_with_timeout = real_exec12
check("RED: the re-run was really attempted", any(", space_name FROM" in c for c in calls12[1:]),
      calls12)
check("a re-run that raises keeps the writer's rows and SQL line, and nothing fails",
      header(g12z) == "| space_no | area_m2 | dept |" and f"SQL: `{g12a_sql}`" in g12z
      and "SQL query failed" not in g12z, g12z[:400])

# ---------------------------------------------------------------------------
print("\n13. A column that would only repeat its pattern is not added (wave 4, A1)")
# ---------------------------------------------------------------------------
# Measured on the goal-function run of 2026-10-03: a pattern matched one name that two different
# places print, the column section 12 adds printed that same name on every row, and the answer
# merged the two places into one - where every earlier answer, given the rows without that column,
# had named both. A column whose every value equals the pattern's literal core (its leading and
# trailing wildcards stripped), case and white space folded, says nothing the MATCHED line does not
# say, so it is not added; with nothing left to add, the result is the writer's own, byte for byte.
# A core with a wildcard inside it, or a column holding any longer value, is added as before.
TWINS = {"table_name": "bld_spaces", "columns": ["space_no", "space_name", "area_m2", "dept"],
         "rows": [{"space_no": "9.81", "space_name": "Quiet Hall", "area_m2": "40.5",
                   "dept": "Studio Arts"},
                  {"space_no": "9.82", "space_name": "Quiet Hall", "area_m2": "12.0",
                   "dept": "Services"},
                  {"space_no": "9.83", "space_name": "Plant Store", "area_m2": "8.0",
                   "dept": "Services"}],
         "row_count": 3}


def without_g5(sql, tables):
    """The same query with the column re-run switched off - the text before section 12."""
    saved = sql_tool._matched_column_rerun
    sql_tool._matched_column_rerun = lambda *a, **k: None
    try:
        return run_sql(sql, tables)
    finally:
        sql_tool._matched_column_rerun = saved


g13a_sql = "SELECT space_no, area_m2 FROM \"bld_spaces\" WHERE space_name ILIKE '%Quiet Hall%'"
g13a = run_sql(g13a_sql, [TWINS])
check("RED: two places printing the pattern's own name - the column is not added",
      header(g13a) == "| space_no | area_m2 |", header(g13a))
check("RED: the whole result is the text without the column re-run, byte for byte",
      g13a == without_g5(g13a_sql, [TWINS]), g13a)
check("RED: its SQL line is the writer's",
      f"SQL: `{g13a_sql}`" in g13a and ", space_name FROM" not in g13a, g13a[-300:])
check("the MATCHED line still states the filter the rows were selected by",
      matched_lines(g13a) == [MT + "space_name ILIKE '%Quiet Hall%'"], matched_lines(g13a))
check("both places are still two rows", "| 9.81 | 40.5 |" in g13a and "| 9.82 | 12.0 |" in g13a,
      g13a[:300])

SPELLINGS = dict(TWINS, rows=[dict(TWINS["rows"][0], space_name="QUIET HALL"),
                              dict(TWINS["rows"][1], space_name=" Quiet  hall "),
                              TWINS["rows"][2]])
g13b_sql = "SELECT space_no FROM \"bld_spaces\" WHERE space_name ILIKE '%quiet%hall%'"
g13b = run_sql(g13b_sql, [SPELLINGS])
check("a wildcard inside the core: the column is added, whatever it holds",
      header(g13b) == "| space_no | space_name |", header(g13b))
g13c_sql = "SELECT space_no FROM \"bld_spaces\" WHERE space_name ILIKE '%quiet hall%'"
SPELLINGS2 = dict(TWINS, rows=[dict(TWINS["rows"][0], space_name="QUIET HALL"),
                               dict(TWINS["rows"][1], space_name=" Quiet Hall "),
                               TWINS["rows"][2]])
g13c = run_sql(g13c_sql, [SPELLINGS2])
check("RED: values that differ from the core only in letter case or surrounding white space - "
      "not added", header(g13c) == "| space_no |" and g13c == without_g5(g13c_sql, [SPELLINGS2]),
      header(g13c))
g13d_sql = "SELECT space_no FROM \"bld_spaces\" WHERE space_name LIKE '__Quiet Hall%%'"
g13d = run_sql(g13d_sql, [dict(TWINS, rows=[dict(r, space_name="A Quiet Hall")
                                             for r in TWINS["rows"][:2]])])
check("single-character wildcards at a pattern's ends only ever match a longer value, which is "
      "added", header(g13d) == "| space_no | space_name |", header(g13d))

LONGER = dict(TWINS, rows=TWINS["rows"][:2] + [dict(TWINS["rows"][2], space_name="Quiet Hall Annex")])
g13e = run_sql(g13a_sql, [LONGER])
check("one longer value among the repeats: the column is added - it tells the rows apart",
      header(g13e) == "| space_no | area_m2 | space_name |"
      and "| Quiet Hall Annex |" in g13e, header(g13e))
PARTS = {"table_name": "bld_parts", "columns": ["part_no", "fits", "description"],
         "rows": [{"part_no": "P-7", "fits": "Unit 7", "description": "Rotor R:14,2 L:95 M4-M6"},
                  {"part_no": "P-8", "fits": "Unit 7", "description": "Rotor R:14,2 L:95 M4-M6"}],
         "row_count": 2}
g13f = run_sql("SELECT part_no FROM \"bld_parts\" WHERE description ILIKE '%Rotor%'", [PARTS])
check("a value that only STARTS with the core is no repeat of it: the column is added",
      header(g13f) == "| part_no | description |", header(g13f))

g13g_sql = ("SELECT space_no FROM \"bld_spaces\" WHERE space_name ILIKE '%Quiet Hall%' AND "
            "dept ILIKE '%S%'")
g13g = run_sql(g13g_sql, [TWINS])
check("RED: of two pattern columns, only the one that tells the rows apart is added",
      header(g13g) == "| space_no | dept |", header(g13g))
check("RED: its SQL line adds that column alone, and the rows are the same rows",
      "SQL: `SELECT space_no, dept FROM \"bld_spaces\" WHERE space_name ILIKE '%Quiet Hall%' AND "
      "dept ILIKE '%S%'`" in g13g and "| 9.81 | Studio Arts |" in g13g
      and "| 9.82 | Services |" in g13g, g13g)
check("the MATCHED line states both filters, as written",
      matched_lines(g13g) == [MT + "space_name ILIKE '%Quiet Hall%' AND dept ILIKE '%S%'"],
      matched_lines(g13g))

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"); sys.exit(1 if FAILS else 0)
