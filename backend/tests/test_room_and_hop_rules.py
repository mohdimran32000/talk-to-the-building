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

print(f"{chr(10)}{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
