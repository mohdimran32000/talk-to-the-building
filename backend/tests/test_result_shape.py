"""test_result_shape.py — a SQL result says what it IS before the writer reads it (2026-09-18).

The owner's question "list the specs of all cctvs" joined 88 cameras to 4 spec rows each =
352 rows, cut to 50; the writer called it "352 units, all one model". The tool now states
the shape: total rows, distinct items, and a breakdown of any low-variety column.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import sql_tool, openai_client
FAILS = []
def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond: FAILS.append(name)
cols = ["camera_tag", "model", "parameter", "value"]
rows = []
for i in range(88):
    m = "IMP231-1IS" if i < 65 else "IMP231-1IRS"
    for p in ("Back Box", "Description", "Lens", "Resolution"):
        rows.append((f"CAM-{i:03d}", m, p, "x"))
s = sql_tool._result_shape(cols, rows, shown=50)
check("states total and shown", "rows: 352" in s and "shown: 50" in s, s)
check("states the distinct count of the identifier-like column", "distinct camera_tag: 88" in s, s)
check("breaks down the low-variety column with counts", "IMP231-1IS x 260" in s and "IMP231-1IRS x 92" in s, s)
check("does not break down a high-variety column", "CAM-000" not in s, s)
check("a single-row result has no SHAPE block", sql_tool._result_shape(["n"], [(68,)], shown=1) == "")
check("a 4-row result gets one", sql_tool._result_shape(["k", "v"], [("a", 1), ("a", 2), ("b", 3), ("b", 4)], shown=4).startswith("SHAPE"))
import inspect
check("execute_sql_query appends the shape to its result", "_result_shape(" in inspect.getsource(sql_tool.execute_sql_query))
r = openai_client.OUTPUT_FORMAT_RULES
check("the answer rule no longer says 'never truncate'", "never truncate" not in r)
check("the answer rule forbids describing rows the model did not see", "not shown" in r.lower() or "were not shown" in r.lower(), r[:300])
check("the answer rule names the SHAPE block as the source of totals", "SHAPE" in r)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"); sys.exit(1 if FAILS else 0)
