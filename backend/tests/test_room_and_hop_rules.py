"""test_room_and_hop_rules.py - the two SQL-writer rules added in Task 8 round 2.

The ruler's R shape (per-room lists) and J shape (two-hop joins) stood at 1/10
and 2/10 on the answers layer after fix wave 1. Reading the SQL the app actually
wrote, from the shipped run files and the LangSmith traces behind them, the
failures are two writer habits, not twenty separate bugs:

  R - a room is filtered by a location ID the writer BUILDS rather than by the
      place name the question prints. `location_id = 'L06-B'` is the LEVEL, not
      room RM-6.01 (ex-039: 42 level assets, then "the records do not contain
      information regarding an IDF hub room"); `location_id = 'L02-B' AND
      display_name ILIKE '%MDF%'` names two different rows and returns nothing
      (ex-040). Where the room-placed table was routed the writer still reached
      for a per-system register instead (ex-036 14 of 36 units, ex-041 18 of 23,
      ex-052 12 rows of 26 units), and no query ever returned the place's unit
      TOTAL, which is the group ex-042/044/045 each failed on.

  J - a two-part question about ONE named thing is answered with two or three
      columns chosen for the FIRST part. ex-046 selected network_room_id and
      feeding_room from a row that also prints the switch model and the VMS
      room, then answered "the provided records do not contain information
      regarding the specific switch". ex-048 the same. ex-047 invented
      `subject`/`subject_id` on that table and self-joined - a Binder Error.
      ex-049 counted a board's circuits in the FEEDER schedule (0) instead of
      the circuits table (42). ex-050/051/055 returned the neighbours and never
      the thing's own 143.6 / 192.63 / 1445.45 kW.

Both rules are written from SHAPE - a place-name column, a quantity column, a
row's own id - and name no table, no column and no building, like every other
rule in that prompt.

Every check below fails against sql_tool.py as it stood before these rules.

Run:
    venv/Scripts/python -X utf8 tests/test_room_and_hop_rules.py
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import sql_tool  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


SRC = inspect.getsource(sql_tool.execute_sql_query)

print("1. The rule for what is IN a place")
check("ROOM_CONTENTS_RULE exists", hasattr(sql_tool, "ROOM_CONTENTS_RULE"))
r = getattr(sql_tool, "ROOM_CONTENTS_RULE", "")
check("it is one prompt bullet", r.startswith("- ") and r.count(chr(10)) == 0, r[:80])
check("it says to filter on the place-NAME column with ILIKE",
      "ILIKE" in r and "name" in r.lower(), r[:200])
check("it forbids ALSO equating the location-id column - the measured 0-row query",
      "location-id" in r.lower() or "location id" in r.lower(), r[:200])
check("it says a level/block id is a different row from the room's own id",
      "level" in r.lower() and "different" in r.lower(), r[:300])
check("it prefers the table that already carries one row per place and item "
      "over a per-system register",
      "register" in r.lower(), r[:300])
check("it asks for the quantity column and a total",
      "quantit" in r.lower() and "total" in r.lower(), r[:300])
check("it names no table, column value or building of this project",
      not any(t in r for t in ("hwu_", "Energy Laboratory", "Heriot", "RM-", "L06-B")), r[:300])
check("it is in the SQL-generation prompt", "ROOM_CONTENTS_RULE" in SRC)

print(chr(10) + "2. The rule for a two-part question about one named thing")
check("TWO_HOP_RULE exists", hasattr(sql_tool, "TWO_HOP_RULE"))
h = getattr(sql_tool, "TWO_HOP_RULE", "")
check("it is one prompt bullet", h.startswith("- ") and h.count(chr(10)) == 0, h[:80])
check("it says to select EVERY column of the thing's own row",
      ("every column" in h.lower() or "all its columns" in h.lower() or "select *" in h.lower()),
      h[:300])
check("it says a narrow SELECT is why an answer claims the record is silent",
      "do not contain" in h.lower() or "not contain" in h.lower() or "silent" in h.lower(),
      h[:400])
check("it forbids inventing a column to make a join",
      "invent" in h.lower() or "not listed" in h.lower(), h[:400])
check("it says a COUNT of what something has comes from the table whose rows "
      "ARE those things",
      "count" in h.lower(), h[:400])
check("it says to include the thing's OWN totals/ratings alongside its neighbours",
      ("own" in h.lower() and ("total" in h.lower() or "rating" in h.lower())), h[:400])
check("it names no table, column value or building of this project",
      not any(t in h for t in ("hwu_", "SMDB", "MDB-C", "CCTV-L", "Heriot")), h[:300])
check("it is in the SQL-generation prompt", "TWO_HOP_RULE" in SRC)

print(chr(10) + "3. Neither rule undoes a rule already in the prompt")
check("the existing equipment-count rule is still there",
      "EQUIPMENT_COUNT_RULE" in SRC and hasattr(sql_tool, "EQUIPMENT_COUNT_RULE"))
check("the existing dependency-graph rule is still there",
      "DEPENDENCY_GRAPH_RULE" in SRC and hasattr(sql_tool, "DEPENDENCY_GRAPH_RULE"))
check("SUM-the-quantity-column is still stated for counting units",
      "SUM that column instead of COUNT(*)" in SRC)
check("the two new rules are appended after them, not woven into the f-string "
      "body (tags stay OUT of the prompt text - see the PROVENANCE MAP)",
      SRC.index("EQUIPMENT_COUNT_RULE") < SRC.index("ROOM_CONTENTS_RULE")
      if "ROOM_CONTENTS_RULE" in SRC else False)

print(chr(10) + "4. Lists carry identifiers; spec tables return every parameter")
check("LIST_IDENTIFIER_RULE exists", hasattr(sql_tool, "LIST_IDENTIFIER_RULE"))
li = getattr(sql_tool, "LIST_IDENTIFIER_RULE", "")
check("it mentions the identifier column", "identifier" in li.lower(), li[:200])
check("it says to SELECT it", "SELECT" in li, li[:200])
check("it names no table of this project",
      not any(t in li for t in ("hwu_", "Heriot", "RM-", "L06-B", "MDB-C", "SMDB")), li[:300])
check("it is in the SQL-generation prompt", "LIST_IDENTIFIER_RULE" in SRC)

check("ALL_PARAMETERS_RULE exists", hasattr(sql_tool, "ALL_PARAMETERS_RULE"))
ap = getattr(sql_tool, "ALL_PARAMETERS_RULE", "")
check("it says to return every/all parameter",
      "every parameter" in ap.lower() or "all parameter" in ap.lower(), ap[:300])
check("it names no table of this project",
      not any(t in ap for t in ("hwu_", "product_specs", "cctv", "Heriot")), ap[:300])
check("it is in the SQL-generation prompt", "ALL_PARAMETERS_RULE" in SRC)

check("both new rules follow TWO_HOP_RULE in the prompt, not woven into the "
      "f-string body (tags stay OUT of the prompt text)",
      SRC.index("TWO_HOP_RULE") < SRC.index("LIST_IDENTIFIER_RULE") < SRC.index("ALL_PARAMETERS_RULE")
      if "LIST_IDENTIFIER_RULE" in SRC and "ALL_PARAMETERS_RULE" in SRC else False)

print(chr(10) + "5. A list never opens with the rows that have no identifier")
# The owner's own question on 2026-09-23 - "what are the rooms on the first floor?" -
# came back as 50 rows whose Room Number cell was EMPTY. Nothing was wrong with the
# routing or the loop: LIST_IDENTIFIER_RULE had done its job and room_number was
# selected, but the query ended `ORDER BY room_number`, and an empty string sorts
# before every real number, so the 50 rows shown were exactly the 50 with no number.
# The fix is one sentence of SQL, not a model: sort the blanks LAST.
check("ORDER_BY_BLANKS_RULE exists", hasattr(sql_tool, "ORDER_BY_BLANKS_RULE"))
ob = getattr(sql_tool, "ORDER_BY_BLANKS_RULE", "")
check("it is about ORDER BY on an identifier column",
      "order by" in ob.lower() and "identifier" in ob.lower(), ob[:300])
check("it says NULLS LAST", "NULLS LAST" in ob, ob[:300])
check("it handles the BLANK, not just the NULL (an empty string sorts first)",
      "NULLIF(" in ob, ob[:300])
check("it says last, never first", "last" in ob.lower() and "first" in ob.lower(), ob[:300])
check("it names no table, column or building of this project",
      not any(t in ob for t in ("hwu_", "Heriot", "RM-", "room_number", "MDB-C", "SMDB")),
      ob[:300])
check("it is in the SQL-generation prompt", "ORDER_BY_BLANKS_RULE" in SRC)
check("it is appended after the two rules it completes, not woven into the f-string body",
      SRC.index("LIST_IDENTIFIER_RULE") < SRC.index("ORDER_BY_BLANKS_RULE")
      if "ORDER_BY_BLANKS_RULE" in SRC else False)
check("the provenance map carries an entry for it",
      "blanks/NULLs last" in SRC)

print(chr(10) + "6. A two-part question about one entity is ONE query, never a UNION")
# The owner asked, 2026-09-28: "what is room 1.29? and what all assets there inside?"
# The writer answered it with a set operation across two tables of different widths -
# SELECT * FROM <places> ... UNION ALL SELECT NULL, NULL, NULL, ... - and then padded
# the narrow arm with NULLs until the 8,192-token output cap stopped it (8,188 output
# tokens). DuckDB: "Set operations can only apply to expressions with the same number
# of result columns". The repair call produced the same shape, and the question fell
# through to document search, which answered "1 Daylight Sensor, nothing else" from a
# stray register chunk. The true answer is 14 units across 8 items, in two tables that
# share a key. The runaway guard and the loop's FAILED_SQL re-query both catch this
# AFTER the fact; this rule is the one that stops it being written.
check("TWO_PART_RULE exists", hasattr(sql_tool, "TWO_PART_RULE"))
tp = getattr(sql_tool, "TWO_PART_RULE", "")
check("it is one prompt bullet", tp.startswith("- ") and tp.count(chr(10)) == 0, tp[:80])
check("it says a two-part question about one entity is ONE query",
      "one query" in tp.lower() and "two parts" in tp.lower(), tp[:300])
check("it names UNION - the operation that actually failed", "UNION" in tp, tp[:400])
check("and the other two set operations with it",
      "INTERSECT" in tp and "EXCEPT" in tp, tp[:400])
check("it says WHY they are never the answer here: identical column lists",
      "identical column" in tp.lower(), tp[:500])
check("it says to select from the table holding the specific part",
      "select" in tp.lower() and ("list" in tp.lower() or "count" in tp.lower()), tp[:400])
check("it says to JOIN the entity's own row for its descriptive columns",
      "JOIN" in tp and "own row" in tp.lower(), tp[:500])
check("it names the descriptive columns a 'what is X' half needs",
      "name" in tp.lower() and "area" in tp.lower(), tp[:500])
check("it names no table, column value or building of this project",
      not any(t in tp for t in ("hwu_", "Heriot", "RM-", "1.29", "MDB-C", "SMDB",
                                "room_number", "Digital Classroom")), tp[:400])
check("it is in the SQL-generation prompt", "TWO_PART_RULE" in SRC)
check("it is injected after ORDER_BY_BLANKS_RULE, not woven into the f-string body",
      SRC.index("ORDER_BY_BLANKS_RULE") < SRC.index("TWO_PART_RULE")
      if "TWO_PART_RULE" in SRC else False)
check("the provenance map carries an entry for it",
      "set operations require identical" in SRC or "two-part question -> ONE query" in SRC
      or "two-part question: ONE query" in SRC,
      [l.strip() for l in SRC.splitlines() if "two-part" in l.lower()])
check("the constant is defined beside TWO_HOP_RULE, the rule it completes",
      abs(inspect.getsource(sql_tool).index("TWO_PART_RULE = ")
          - inspect.getsource(sql_tool).index("TWO_HOP_RULE = ")) < 6000,
      abs(inspect.getsource(sql_tool).index("TWO_PART_RULE = ")
          - inspect.getsource(sql_tool).index("TWO_HOP_RULE = "))
      if "TWO_PART_RULE = " in inspect.getsource(sql_tool) else "not defined")
check("and the rules it was added beside are all still in the prompt",
      all(n in SRC for n in ("TWO_HOP_RULE", "LIST_IDENTIFIER_RULE",
                             "ORDER_BY_BLANKS_RULE", "ALL_PARAMETERS_RULE")))

print(chr(10) + "7. No rule in the prompt recommends a set operation any more")
# Owner's ruling, 2026-09-28. TWO_HOP_RULE used to end "(UNION ALL one labelled block
# per part is fine, with a constant column naming which part each block answers)" - and
# on the room-1.29 trace that is precisely what the writer did, across two tables of
# different widths, padding the narrow arm with NULLs until the output cap stopped it.
# TWO_PART_RULE now says the opposite, and two rules contradicting each other in one
# prompt is the wrong end state, so the clause is withdrawn rather than outvoted.
h7 = getattr(sql_tool, "TWO_HOP_RULE", "")
check("TWO_HOP_RULE no longer mentions UNION at all", "UNION" not in h7.upper(), h7[-400:])
check("it still sends the second part to a JOIN in the same query",
      "JOIN" in h7.upper() and "same query" in h7.lower(), h7[-400:])
check("and it now says a set operation is never the way",
      "never a set operation" in h7.lower(), h7[-400:])
check("it is still one prompt bullet", h7.startswith("- ") and h7.count(chr(10)) == 0,
      h7[:80])
check("the rest of the rule survives the edit - SELECT * of the thing's own row",
      "SELECT *" in h7 and "EVERY COLUMN" in h7.upper(), h7[:300])
check("- and the count-from-the-children-table clause",
      "count" in h7.lower(), h7[:600])
check("- and the include-its-own-totals clause",
      "own" in h7.lower() and ("total" in h7.lower() or "rating" in h7.lower()), h7)
check("the withdrawal is recorded in the source, with its date",
      "2026-09-28" in inspect.getsource(sql_tool)
      and "withdrawn" in inspect.getsource(sql_tool).lower(),
      [l.strip() for l in inspect.getsource(sql_tool).splitlines()
       if "withdrawn" in l.lower()])
check("TWO_HOP_RULE keeps its provenance line",
      "two-part question -> SELECT * of" in SRC,
      [l.strip() for l in SRC.splitlines() if "SELECT * of" in l])

# The real assertion: assemble the text the model is actually SENT - every named rule
# constant, plus the bullets written inline in the prompt f-string (those start at
# column 0; the PROVENANCE MAP lines are comments and start with '#', so they are not
# swept in) - and prove every UNION in it is a prohibition. 60 characters is generous:
# the point is that no occurrence stands on its own as a recommendation.
RULE_CONSTANTS = [n for n in dir(sql_tool) if n.endswith("_RULE")]
check("the sweep actually found the rule constants", len(RULE_CONSTANTS) >= 7,
      RULE_CONSTANTS)
INLINE_BULLETS = [l for l in SRC.splitlines() if l.startswith("- ")]
check("the sweep actually found the inline prompt bullets", len(INLINE_BULLETS) >= 20,
      len(INLINE_BULLETS))
SENT = [str(getattr(sql_tool, n)) for n in RULE_CONSTANTS] + INLINE_BULLETS

NEGATORS = ("never", "no ", "not ")
offenders = []
total_unions = 0
for text in SENT:
    low = text.lower()
    start = 0
    while True:
        i = low.find("union", start)
        if i < 0:
            break
        total_unions += 1
        window = low[max(0, i - 60):i]
        if not any(neg in window for neg in NEGATORS):
            offenders.append(text[max(0, i - 80):i + 80])
        start = i + 1
check("the sweep found UNION somewhere - it is not passing vacuously",
      total_unions >= 2, total_unions)
check("every UNION the model is sent is a prohibition, within 60 chars of "
      "never/no/not", offenders == [], offenders)

print(f"{chr(10)}{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
