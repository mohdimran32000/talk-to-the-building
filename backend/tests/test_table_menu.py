"""test_table_menu.py — the tool-choice prompt must list EVERY table (2026-09-18).

Found in review of the 2026-09-17 research: `_format_structured_tables` kept a `[:40]` cap
set on 2026-08-19 when the corpus had 28 tables. The corpus has 69, fetched alphabetically,
so the panel schedule, feeder schedule, room-assets and every LV/UPS/water-heater/zip-tap
table were invisible to the model deciding "SQL or document search".
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.openai_client import _format_structured_tables, _build_system_prompt
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

# --- 2026-09-18, fix wave 1 (F5). Removing the [:40] cap above moved the app OFF document
# search: query_structured_data 60->63 on the ext set and 58->64 on the holdout, while
# search_documents went 26->24 and 38->30 (task-8-diagnosis.md, proof 3). Two cards broke
# for the same reason, and neither is a routing bug: ex-021's answer is a sentence in the LV
# MANIFEST (Block B, "owner ruling 2026-09-15") and ex-023's model string lives in a table
# that is in neither routed set - so when the app stopped calling search_documents at all,
# the only path to the answer closed. The fix is one tool-selection rule: a question asking
# WHICH RECORD says something is answered from the record, not from a table that happens to
# hold a similar number.
prompt = _build_system_prompt(has_documents=True, has_structured_data=True,
                              web_search_enabled=False)
check("the WHICH-RECORD rule is in the tool-selection rules",
      "asks WHICH RECORD" in prompt, prompt[-400:])
check("...and it sends those questions to search_documents",
      any("asks WHICH RECORD" in line and "search_documents" in line
          for line in prompt.splitlines()),
      str([l for l in prompt.splitlines() if "WHICH RECORD" in l]))
check("...and it sits under TOOL SELECTION RULES, not in the tool list",
      prompt.index("TOOL SELECTION RULES") < prompt.index("asks WHICH RECORD"))
check("it is not emitted when there are no tables to be tempted by",
      "asks WHICH RECORD" not in _build_system_prompt(has_documents=True,
                                                      has_structured_data=False,
                                                      web_search_enabled=False))

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"); sys.exit(1 if FAILS else 0)
