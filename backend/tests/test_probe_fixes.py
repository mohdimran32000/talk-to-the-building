"""test_probe_fixes.py — the app-side defects the 2026-09-17 probe measured.

Ten questions were written against the verified data and run through the full
answer path (doc-prep/eval/items/probe_2026-09-17.json, run
20260917-124527-answers-probe2). Two passed. Of the eight that did not, three
were the cards' fault (fixed in the cards) and five were the app's, with causes:

  1. `_infer_column_types` voted a phone-number column DOUBLE because
     "0552002270" parses as a float — so the SQL layer printed 552002270.0 and
     the answer gave the owner a number nobody can dial. Any identifier with a
     leading zero (phone numbers, DEWA meter numbers like 003003455114) dies
     the same way. A leading zero means "this is a code, not a quantity".
  2. A COUNT/SUM answer came back as a bare number ("68 access-control doors")
     with no way for the answer-writing model to say WHICH record it counted,
     although the card for that table says exactly that in one sentence. The SQL
     result now carries a SOURCE line per table the query read, built from the
     card's `holds`, and the answer rules say to name it.
  3. "How many fan coil units" was answered with SUM(points) over the electrical
     circuits table = 550 outlets. Equipment is counted from register tables
     (one row per asset, or a per-room quantity), never from circuit points.
  4. "What is the warranty on the fan coil units?" was answered "12 months"
     from a certificate whose printed scope is "Building Management System &
     PICV (BMS)". A certificate covers what it names, nothing else.
  5. A change-impact answer said "the records do not state the specifications"
     of a camera whose own row prints its model and class, and whose model has
     a specification table. "What it is now" must carry every printed
     attribute of the item's own row, and the spec lookup goes through the
     model.

Every check below fails against the code as it stood before these fixes.

Run:
    venv/Scripts/python -X utf8 tests/test_probe_fixes.py
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import openai_client, sql_tool  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


# ---------------------------------------------------------------------------
print("1. A leading zero makes a column text, not a number")
# ---------------------------------------------------------------------------
phones = [{"phone": "0552002270"}, {"phone": "0552002282"}]
check("a column of leading-zero digit strings is VARCHAR",
      sql_tool._infer_column_types(["phone"], phones)["phone"] == "VARCHAR",
      sql_tool._infer_column_types(["phone"], phones))
meters = [{"meter": "003003455114"}, {"meter": "003003455061"}, {"meter": "003003455099"}]
check("DEWA meter numbers (leading zeros) are VARCHAR",
      sql_tool._infer_column_types(["meter"], meters)["meter"] == "VARCHAR")
mixed = [{"v": "0552002270"}] + [{"v": str(i)} for i in range(1, 10)]
check("one leading-zero code among nine numbers still makes the column text (a code column never rounds)",
      sql_tool._infer_column_types(["v"], mixed)["v"] == "VARCHAR")
decimals = [{"v": "0.5"}, {"v": "0.75"}, {"v": "0"}, {"v": "0.0"}]
check("'0.5', '0' and '0.0' are still numbers (a zero before a point is not a leading zero)",
      sql_tool._infer_column_types(["v"], decimals)["v"] == "DOUBLE")
plain = [{"w": "1,600"}, {"w": "2200"}, {"w": "N/A"}, {"w": "600"}, {"w": "13921"}]
check("the existing 80% majority vote is unchanged for ordinary numeric columns",
      sql_tool._infer_column_types(["w"], plain)["w"] == "DOUBLE")
ints = [{"n": 5}, {"n": 0}, {"n": 12}]
check("real ints (not strings) are still numeric",
      sql_tool._infer_column_types(["n"], ints)["n"] == "DOUBLE")


# ---------------------------------------------------------------------------
print("\n2. A SQL result names the record it was read from")
# ---------------------------------------------------------------------------
CARDS = [
    {"table": "bld_doors", "holds": "One row for each of the 68 access-controlled door positions drawn on the 14 as-built drawings."},
    {"table": "bld_equipment", "holds": "1188 equipment rows across 7 systems, the owner's own asset registers."},
    {"table": "bld_circuits", "holds": "2371 circuit rows of the approved load schedule."},
]
TABLES = [{"table_name": c["table"], "columns": ["a"]} for c in CARDS]
src = sql_tool._source_lines('SELECT SUM(qty) FROM (SELECT 1 AS qty FROM "bld_doors")', TABLES, CARDS)
check("the SOURCE line names the table the SQL read and quotes its card's holds sentence",
      "bld_doors" in src and "68 access-controlled door positions" in src, src)
check("tables the SQL did NOT read are not named",
      "bld_equipment" not in src and "bld_circuits" not in src, src)
src2 = sql_tool._source_lines('SELECT * FROM "bld_equipment" e JOIN "bld_circuits" c ON e.a = c.a', TABLES, CARDS)
check("a join names every table it reads, one line each",
      src2.count("SOURCE") == 2 and "bld_equipment" in src2 and "bld_circuits" in src2, src2)
check("no cards -> no SOURCE line (the fallback path stays silent)",
      sql_tool._source_lines('SELECT 1 FROM "bld_doors"', TABLES, []) == "")
long_card = [{"table": "bld_doors", "holds": "x" * 2000}]
check("a holds sentence is cut to a bounded length so it cannot flood the answer prompt",
      len(sql_tool._source_lines('SELECT 1 FROM "bld_doors"', TABLES, long_card)) < 700)
check("the source line is appended inside execute_sql_query (it must travel with the result)",
      "_source_lines(" in inspect.getsource(sql_tool.execute_sql_query))
rules = openai_client.OUTPUT_FORMAT_RULES
check("the answer rules tell the model to NAME the source when it states a count or total",
      "SOURCE" in rules and "count" in rules.lower(), "no SOURCE rule in OUTPUT_FORMAT_RULES")


# ---------------------------------------------------------------------------
print("\n3. Equipment is counted from registers, never from circuit points")
# ---------------------------------------------------------------------------
check("EQUIPMENT_COUNT_RULE exists", hasattr(sql_tool, "EQUIPMENT_COUNT_RULE"))
if hasattr(sql_tool, "EQUIPMENT_COUNT_RULE"):
    r = sql_tool.EQUIPMENT_COUNT_RULE
    check("it says circuit points are outlets, not equipment", "points" in r and "outlet" in r.lower(), r[:200])
    check("it names the register shapes (one row per asset / per-room quantity)",
          "register" in r.lower() and "quantity" in r.lower(), r[:200])
    check("it is in the SQL-generation prompt",
          "EQUIPMENT_COUNT_RULE" in inspect.getsource(sql_tool.execute_sql_query))


# ---------------------------------------------------------------------------
print("\n4. A certificate covers only what it names")
# ---------------------------------------------------------------------------
check("the warranty rule says a certificate's printed SCOPE bounds what it covers",
      "scope" in rules.lower() and "certificate" in rules.lower(), "no scope rule")
check("and that equipment the scope does not name is not covered by that record",
      "not name" in rules.lower() or "does not name" in rules.lower() or "not named" in rules.lower())
check("identifiers are copied verbatim, leading zeros included",
      "leading zero" in rules.lower(), "no verbatim-identifier rule")


# ---------------------------------------------------------------------------
print("\n5. 'What it is now' carries the item's own printed attributes")
# ---------------------------------------------------------------------------
ci = openai_client.CHANGE_IMPACT_RULES
check("the investigation says to SELECT every column of the item's own row",
      "every column" in ci.lower() or "all columns" in ci.lower() or "select *" in ci.lower(), ci[:300])
check("and to look the model up in a specification table where one exists",
      "model" in ci.lower() and "specification" in ci.lower())
check("the answer shape names class and model as recorded identity",
      "model" in openai_client.CHANGE_IMPACT_ANSWER_SHAPE.lower())

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
