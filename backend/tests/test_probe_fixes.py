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

# 2026-09-19 (final fix wave, final-review finding I3). `caveats` is a card field
# written by doc-prep's 11_table_cards.py and documented as a card field in
# table_router.py, and NO CODE IN THIS APP READ IT. So the count-views card's
# "never SUM `count` across `view_id`" warning - written precisely because a SUM
# over a long-format table produced "486 doors", "1,267 fan coil units" and "451
# card readers" - reached the document index and never the model writing the SQL
# or the answer. The caveat now travels with the SOURCE line, and is bounded the
# same way: at most 3 per table, each cut to 300 characters, so a card with a long
# caveat list cannot flood the prompt it rides in.
CARDS_CAV = [
    {"table": "bld_views", "holds": "486 rows, ten stacked count views.",
     "caveats": ["Never SUM `count` across `view_id` - the rows are different views.",
                 "A disputed view lists every printed number in `other_counts`."]},
    {"table": "bld_equipment", "holds": "1188 equipment rows.", "caveats": []},
]
TABLES_CAV = [{"table_name": c["table"], "columns": ["a"]} for c in CARDS_CAV]
src3 = sql_tool._source_lines('SELECT * FROM "bld_views"', TABLES_CAV, CARDS_CAV)
check("a card with two caveats emits two NOTE lines for that table",
      src3.count("NOTE - bld_views:") == 2, src3)
check("the caveat's own text travels, not a placeholder",
      "Never SUM `count` across `view_id`" in src3, src3)
check("the SOURCE line still comes first, the NOTEs under it",
      src3.splitlines()[0].startswith("SOURCE - bld_views"), src3)
src4 = sql_tool._source_lines('SELECT * FROM "bld_equipment"', TABLES_CAV, CARDS_CAV)
check("a card with no caveats emits no NOTE line at all", "NOTE" not in src4, src4)
many = [{"table": "bld_views", "holds": "h",
         "caveats": ["c" * 900, "b1", "b2", "b3", "b4"]}]
src5 = sql_tool._source_lines('SELECT * FROM "bld_views"', TABLES_CAV, many)
check("at most three NOTE lines per table", src5.count("NOTE - ") == 3, src5)
check("each caveat is cut to 300 characters",
      all(len(l.split(": ", 1)[1]) <= 300
          for l in src5.splitlines() if l.startswith("NOTE - ")),
      str([len(l) for l in src5.splitlines()]))
check("a card whose caveats key is missing entirely is unaffected (the pre-2026-09-19 shape)",
      "NOTE" not in sql_tool._source_lines('SELECT 1 FROM "bld_doors"', TABLES, CARDS),
      sql_tool._source_lines('SELECT 1 FROM "bld_doors"', TABLES, CARDS))
rules = openai_client.OUTPUT_FORMAT_RULES
check("the answer rules tell the model to NAME the source when it states a count or total",
      "SOURCE" in rules and "count" in rules.lower(), "no SOURCE rule in OUTPUT_FORMAT_RULES")
check("the rule text changed 2026-09-18: 'never truncate' is gone, replaced by an unseen-rows rule",
      "never truncate" not in rules and "were not shown" in rules.lower(), rules[:300])


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


# ---------------------------------------------------------------------------
print("\n6. A note's rival figure is stated beside the figure (spec-fix1 part b)")
# ---------------------------------------------------------------------------
# A note can carry more than a correction: a DIFFERENT figure printed elsewhere for the
# same quantity, or which group of items a value applies to. The EXCEPTION bullet used to
# license only corrections ("struck out, superseded ..."), so a writer handed a note that
# names a rival printed figure had a rule telling it to drop the note and none telling it
# to state the rival. It now covers all three, wherever the note rides - a notes cell, a
# card's NOTE line, or the block sql_tool appends of the notes behind a small result.
exc = next((l for l in rules.splitlines() if l.startswith("- EXCEPTION")), "")
check("the EXCEPTION bullet exists", bool(exc), rules[:200])
check("it covers a DIFFERENT printed figure for the same quantity, not only a correction",
      "different figure" in exc.lower() and "same quantity" in exc.lower(), exc)
check("it covers which group a value applies to", "which group" in exc.lower(), exc)
check("the answer must state the current value AND the other figure",
      "current value and the other figure" in exc.lower(), exc)
check("each with where it is printed", "each with where it is printed" in exc.lower(), exc)
check("it names the card NOTE lines and the notes block as notes it applies to",
      '"NOTE - ' in exc and sql_tool.ROW_NOTES_HEADING.rstrip(":") in exc
      if hasattr(sql_tool, "ROW_NOTES_HEADING") else False, exc)
check("it still forbids presenting a superseded value as current",
      "never present a superseded value as current" in exc.lower(), exc)
check("and the block's heading is never printed", "never print" in exc.lower(), exc)
check("the drop-the-data-entry-note bullet before it is unchanged",
      "Present only the meaningful value" in rules and "drop the note" in rules)


# ---------------------------------------------------------------------------
print("\n7. T7 (2026-10-01) — a lifespan is never a warranty, and the contact rule is "
      "present")
# ---------------------------------------------------------------------------
# A purchase date plus a lifespan/service-life column is not a warranty record, and must
# not be read as one just because both are durations/dates.
check("the rules say a lifespan/service life/expected life is NEVER a warranty period "
      "or a warranty expiry",
      "lifespan" in rules.lower() and "never a warranty period" in rules.lower()
      and "warranty expiry" in rules.lower(),
      rules[:400])
check("and the same holds for a bare purchase date",
      "purchase date" in rules.lower(), rules[:400])
check("with no warranty record, the rule says to SAY SO rather than infer one",
      "no warranty record was found" in rules.lower(), rules[:400])

# A who/phone-number question must be answered from the source that actually names the
# asked-about party/system, not whichever table or excerpt happened to come back.
check("a who/contact rule exists",
      "who/contact" in rules.lower() or "contact question" in rules.lower(), rules[:400])
check("it says to answer from whichever source names the asked-about party or system",
      "names the party" in rules.lower() or "names the asked-about party" in rules.lower(),
      rules[:400])
check("a contact for a different system/party is explicitly not the answer",
      "different system" in rules.lower() and "different party" in rules.lower(),
      rules[:400])
check("and when more than one system's contacts appear, the rule says to name which "
      "system each belongs to",
      "which system" in rules.lower() or "which system or party" in rules.lower(),
      rules[:400])

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
