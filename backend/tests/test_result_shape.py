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
check("the answer rule names RESULT SHAPE as the source of totals", "RESULT SHAPE" in r)
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

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"); sys.exit(1 if FAILS else 0)
