"""test_table_menu.py — the tool-choice prompt must list EVERY table (2026-09-18).

Found in review of the 2026-09-17 research: `_format_structured_tables` kept a `[:40]` cap
set on 2026-08-19 when the corpus had 28 tables. The corpus has 69, fetched alphabetically,
so the panel schedule, feeder schedule, room-assets and every LV/UPS/water-heater/zip-tap
table were invisible to the model deciding "SQL or document search".
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.openai_client import _format_structured_tables
FAILS = []
def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond: FAILS.append(name)
tables = [{"table_name": f"hwu_t{i:02d}", "columns": ["a", "b"]} for i in range(69)]
s = _format_structured_tables(tables)
check("all 69 tables are listed", all(f"hwu_t{i:02d}(" in s for i in range(69)), s[-120:])
check("the 69th table is present (was cut at 40)", "hwu_t68(" in s)
wide = [{"table_name": "w", "columns": [f"c{i}" for i in range(30)]}]
check("columns are still capped at 15 per table", "c14" in _format_structured_tables(wide) and "c15" not in _format_structured_tables(wide))
check("empty input -> empty string", _format_structured_tables([]) == "")
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"); sys.exit(1 if FAILS else 0)
