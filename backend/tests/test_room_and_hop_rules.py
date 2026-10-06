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

print(chr(10) + "8. Printed total rows are never summed with the items they total (spec-fix6)")
# Measured on the goal-function run of 2026-09-30: a table that keeps a sheet's own printed
# TOTAL rows beside its item rows was summed over both, and the answer stated the sum of the
# items plus their own printed total. The result now says so in code (PRINTED TOTAL ROWS);
# this rule is so the query is written right in the first place. From SHAPE: a row-kind
# column whose values include a total kind - no table, column or building named.
check("PRINTED_TOTAL_ROWS_RULE exists", hasattr(sql_tool, "PRINTED_TOTAL_ROWS_RULE"))
pt = getattr(sql_tool, "PRINTED_TOTAL_ROWS_RULE", "")
check("it is one prompt bullet", pt.startswith("- ") and pt.count(chr(10)) == 0, pt[:80])
check("it describes the shape: a row-kind column whose values include a total kind",
      "total" in pt.lower() and "kind" in pt.lower(), pt[:300])
check("it says the item rows are counted WITHOUT the printed total rows",
      "without the printed total rows" in pt.lower() and "is the answer" in pt.lower(), pt[:500])
check("it says the printed total is the document's own figure, returned beside the answer",
      "document's own" in pt.lower() and "beside" in pt.lower(), pt[:500])
check("and never added to the items", "never add" in pt.lower(), pt[:600])
check("it names no table or column: no project table prefix, no quoted identifier and no "
      "snake_case identifier at all - its column is a <placeholder>",
      "hwu_" not in pt and '"' not in pt and "<that column>" in pt
      and __import__("re").search(r"[A-Za-z0-9]+_[A-Za-z0-9_]+", pt) is None,
      pt[:400])
check("it reaches the SQL-generation prompt", "{PRINTED_TOTAL_ROWS_RULE}" in SRC)
check("the provenance map carries an entry for it",
      "printed total rows" in SRC.lower() and "spec-fix6" in SRC,
      [l.strip() for l in SRC.splitlines() if "printed total" in l.lower()])

print(chr(10) + "9. Kind words: a class word covers every kind code (spec-fix8)")
# Measured on the goal-function run of 2026-09-30: "distribution boards" became one kind
# code, dropping the boards of every other kind the class covers, and "X and Y" counted
# both as one bare sum with no count per kind. The prompt's own example taught the first:
# "('how many boards', kind = 'DB')" maps a CLASS word the question prints onto ONE code it
# never prints. Every example must now print the code it filters on.
import re as _re9  # noqa: E402
SENT9 = ([str(getattr(sql_tool, n)) for n in dir(sql_tool) if n.endswith("_RULE")]
         + [l for l in SRC.splitlines() if l.startswith("- ")])
EXAMPLE9 = _re9.compile(r"'(how many [^']+)'[^']{0,40}?\b\w+ = '([^']+)'", _re9.IGNORECASE)
found9 = [(m.group(1), m.group(2)) for text in SENT9 for m in EXAMPLE9.finditer(text)]
check("the sweep sees the counting examples it is about - it is not passing vacuously",
      len(found9) >= 1, found9)
mapped9 = [(q, code) for q, code in found9 if code.lower() not in q.lower()]
check("no prompt line maps a class word to a single kind code: every 'how many ...' "
      "example prints the code it filters on", mapped9 == [], mapped9)
check("the counting-by-category bullet itself is kept: a plain COUNT(*) with that WHERE",
      "is a plain SELECT COUNT(*) with that WHERE" in SRC,
      [l for l in SRC.splitlines() if "COUNT(*) with that WHERE" in l])
check("KIND_WORDS_RULE exists", hasattr(sql_tool, "KIND_WORDS_RULE"))
kw = getattr(sql_tool, "KIND_WORDS_RULE", "")
check("it is one prompt bullet", kw.startswith("- ") and kw.count(chr(10)) == 0, kw[:80])
check("a class word covers EVERY kind code of its class",
      "every kind code" in kw.lower(), kw[:300])
check("never narrowed to one code unless the question prints the code or its qualifier",
      "never narrow" in kw.lower() and "prints" in kw.lower() and "qualifier" in kw.lower(),
      kw[:400])
check("the kind column is SELECTed so each row says which kind it is",
      "select the kind column" in kw.lower(), kw[:500])
check("two or more named kinds: one count per kind, plus the total",
      "one count per kind" in kw.lower() and "plus the total" in kw.lower(), kw)
check("by GROUP BY on the kind column or a SUM(CASE ...) per kind",
      "GROUP BY" in kw and "SUM(CASE" in kw, kw)
# Review fix round 1: a per-kind template of `THEN 1` counts ROWS, and the prompt's own
# counting rule says a quantity column is SUMmed, never replaced by COUNT(*) - one row can
# be many units. The template must offer the quantity column, with 1 only for one-item rows.
check("the per-kind SUM(CASE ...) never offers a hard-coded THEN 1 as its only form",
      "THEN 1 ELSE" not in kw and "THEN <" in kw, kw)
check("it names the quantity column case, and why: one row can be many units",
      "quantity column" in kw.lower() and "one row can be many units" in kw.lower(), kw)
# Wave 3, F3 (2026-10-03). "Count with the quantity column whenever the table has one" was
# followed to the letter: a per-kind count of door positions summed the number of READERS
# fitted to each position - the table's only count-like column - and the answer stated readers
# as positions. A column is summed only when it counts the asked items themselves; a count of
# something fitted to each item is not their quantity; one row per item -> count the rows.
check("RED: it no longer says to count with the quantity column 'whenever the table has one'",
      "whenever the table has one" not in kw, kw)
check("RED: it SUMs a column only when that column counts the asked items themselves",
      "sum a column only when it counts the asked items themselves" in kw.lower(), kw)
check("RED: ... a qty/quantity column, or a column named for the asked item",
      "qty/quantity column" in kw.lower() and "a column named for the asked item" in kw.lower(),
      kw)
check("RED: a count of something fitted to each item is not the items' quantity",
      "fitted to each item" in kw.lower() and "is not the items' quantity" in kw.lower(), kw)
check("RED: when each row is one item, the rows are counted per kind - COUNT(*) or THEN 1",
      "when each row is one item" in kw.lower() and "COUNT(*)" in kw and "THEN 1" in kw, kw)
check("RED: the per-kind SUM(CASE ...) names a column counting the asked items",
      "THEN <a column counting the asked items>" in kw, kw)
bullet9 = next((ln.strip() for ln in SRC.splitlines()
                if ln.strip().startswith("- When counting equipment/units")), "")
check("the older counting bullet is still in the prompt", bool(bullet9), bullet9)
check("RED: the older counting bullet sums only a column counting the asked items themselves",
      "counts the asked items themselves" in bullet9.lower(), bullet9)
check("RED: and says a count of something fitted to each item is not their quantity",
      "fitted to each item" in bullet9.lower() and "is not their quantity" in bullet9.lower(),
      bullet9)
check("RED: and that when each row is one item the rows are counted",
      "when each row is one item" in bullet9.lower() and "COUNT(*) the rows" in bullet9, bullet9)
check("it keeps why a quantity column is summed: one row can represent multiple units",
      "SUM that column instead of COUNT(*)" in bullet9
      and "one row can represent multiple units" in bullet9, bullet9)
check("it names no table, column or code of this project",
      "hwu_" not in kw and '"' not in kw and " = '" not in kw, kw[:400])
check("it reaches the SQL-generation prompt", "{KIND_WORDS_RULE}" in SRC)
check("the provenance map carries an entry for it",
      "kind words" in SRC.lower() and "spec-fix8" in SRC,
      [l.strip() for l in SRC.splitlines() if "kind words" in l.lower()])
PROV9 = SRC[SRC.index("PROVENANCE MAP"):SRC.index('prompt = f"""')]
check("RED: and the provenance map records the wave-3 rewording of both counting rules",
      "fitted to each item" in PROV9.lower() and "wave 3" in PROV9.lower(),
      [l.strip() for l in PROV9.splitlines() if "kind words" in l.lower() or "wave 3" in l.lower()])

print(chr(10) + "10. Floors and levels are addressed by the spine's keys (spec-fix3 b)")
# Measured on the goal-function run of 2026-09-30: floors were filtered on printed floor text
# that every source spells differently ('3F' guessed for a table that prints no such value,
# 'B%' for basements read off a Block letter), a room's display name was searched for its
# level (it never contains one), and a ranking of levels grouped the printed floor column -
# splitting one level in two - then LIMIT 1 hid a tie. The data side stamps a level-code
# column beside every location id; this rule sends the writer to it. From SHAPE: no table,
# column or code of the project is named.
check("PLACE_KEYS_RULE exists", hasattr(sql_tool, "PLACE_KEYS_RULE"))
pk = getattr(sql_tool, "PLACE_KEYS_RULE", "")
pkl = pk.lower()
check("it is one prompt bullet", pk.startswith("- ") and pk.count(chr(10)) == 0, pk[:80])
check("it covers a floor, a level, a storey and a basement",
      all(w in pkl for w in ("floor", "level", "storey", "basement")), pk[:200])
check("they are filtered or grouped on a level-code column",
      "level-code column" in pkl and "filtered or grouped" in pkl, pk[:300])
check("the table's own, or the places table's reached through the location key",
      "the table's own" in pkl and "places table" in pkl and "location-id column" in pkl,
      pk[:600])
check("never on printed floor text",
      "never filter a storey on printed floor text" in pkl, pk[:700])
check("and never on a display or place-name column",
      "display or place-name column" in pkl, pk[:800])
check("T11a-R5: filter with the codes the column actually holds - its value list",
      "actually holds" in pkl and "value list" in pkl, pk[:900])
check("T11a-R5: the same storey can be coded differently in different tables",
      "coded differently in different tables" in pkl, pk)
check("T11a-R5: when in doubt, through the location key to the places table's level code",
      "when in doubt" in pkl and "places table's level code" in pkl, pk)
check("a ranking of levels or rooms groups by the key",
      "ranking of levels or rooms" in pkl and "group" in pkl and "by the key" in pkl, pk)
check("for rooms: the location-id column, only rows whose resolution column = 'room'",
      "resolution column = 'room'" in pkl, pk)
check("for levels: roll up by the level code", "roll up by the level code" in pkl, pk)
check("every group, ordered, never LIMIT 1",
      "every group" in pkl and "order by" in pkl and "never limit 1" in pkl, pk)
check("a level with no matching rows is itself the answer",
      "a level with no matching rows is itself the answer" in pkl, pk)
check("and the filter is never widened to other levels or codes",
      "never widen the filter to other levels or codes" in pkl, pk)
check("T11a-R1: for storeys and rankings only - one named place keeps the what-is-in-a-place "
      "rule", "storeys and rankings only" in pkl and "one place" in pkl
      and "what-is-in-a-place rule" in pkl, pk)
import re as _re10  # noqa: E402
SQL_LITERALS10 = _re10.findall(r"(?:=|\bIN\s*\(|\bI?LIKE)\s*'([^']*)'", pk, _re10.IGNORECASE)
check("it names no table, column, code or building of this project: no table prefix, no "
      "quoted identifier, no snake_case identifier, and no SQL literal but 'room' and "
      "<placeholders>",
      "hwu_" not in pk and '"' not in pk and "RM-" not in pk
      and _re10.search(r"[A-Za-z0-9]+_[A-Za-z0-9_]+", pk) is None
      and bool(SQL_LITERALS10)
      and all(lit == "room" or lit.startswith("<") for lit in SQL_LITERALS10),
      SQL_LITERALS10)
check("it reaches the SQL-generation prompt", "{PLACE_KEYS_RULE}" in SRC)
check("the provenance map carries an entry for it",
      "floors and levels" in SRC.lower() and "spec-fix3" in SRC,
      [l.strip() for l in SRC.splitlines() if "spec-fix3" in l])

print(chr(10) + "11. No rule offers printed floor text as the way to filter a storey (spec-fix3 b)")
# The prompt's own FORMAT example said "if floor values look like 'GF', '4F', '6F' then the
# 4th floor is floor = '4F'", and the never-infer-from-a-NAME rule resolved a floor with
# "floor = '4F'" - two lines steering the writer to the printed floor column. Swept over
# every line the model is sent, like section 7's UNION sweep.
SENT11 = ([str(getattr(sql_tool, n)) for n in dir(sql_tool) if n.endswith("_RULE")]
          + [l for l in SRC.splitlines() if l.startswith("- ")])
FLOOR_FILTER11 = _re10.compile(
    r"\b(?!\w*code\b)\w*(?:floor|storey|level)\w*\s*(?:=|ILIKE|LIKE)\s*'", _re10.IGNORECASE)
offers11 = [m.group(0) for text in SENT11 for m in FLOOR_FILTER11.finditer(text)]
check("no line the model is sent compares a floor or level column with a literal",
      offers11 == [], offers11)
check("the FORMAT example no longer teaches floor = '4F'",
      "floor = '4F'" not in SRC and "'GF', '4F', '6F'" not in SRC,
      [l for l in SRC.splitlines() if "4F" in l])
# A value literal: its opening quote never follows a letter or digit (a prose apostrophe -
# "a room's" - always does), and it is at most two words. A printed floor value is one or
# two ('4F', 'Level 01', 'Ground Floor'); a quoted QUESTION example ('total load of the 4th
# floor') is a phrase, and quoting how a user asks is not offering a filter value.
PRINTED_FLOOR11 = _re10.compile(
    r"(?<![A-Za-z0-9])'(?=[^'\s]*(?:\s[^'\s]+)?')[^']*?(?:\b\d+F\b|\bGF\b"
    r"|\b\d+(?:st|nd|rd|th)\b|ground floor|level \d+)[^']*'", _re10.IGNORECASE)
loose11, seen11 = [], 0
for text in SENT11:
    for m in PRINTED_FLOOR11.finditer(text):
        seen11 += 1
        window = text[max(0, m.start() - 90):m.start()].lower()
        if not any(neg in window for neg in ("never", "not ", "no ")):
            loose11.append(text[max(0, m.start() - 60):m.end() + 20])
check("the sweep sees the prohibited name pattern it is about - it is not passing vacuously",
      seen11 >= 1, seen11)
check("every printed-floor literal left in the prompt sits in a prohibition",
      loose11 == [], loose11)
check("the FORMAT rule's own principle survives the new example",
      "Match the FORMAT of the column samples" in SRC
      and "never a paraphrase" in SRC.lower(),
      [l for l in SRC.splitlines() if "FORMAT" in l])
never_infer11 = next((l for l in SRC.splitlines()
                      if l.startswith("- NEVER infer a panel's block or floor")), "")
check("the never-infer-a-block-or-floor-from-a-NAME rule survives, prohibition and all",
      "'%-4F-%'" in never_infer11 and "silently returns 0 rows" in never_infer11,
      never_infer11[:200])
check("and resolves the floor through the level code, not the printed floor column",
      "level-code column" in never_infer11 and "floor = '" not in never_infer11,
      never_infer11[-260:])

print(chr(10) + "12. T11a-R2: the area-total NOT EXISTS pattern works with a level-code area filter")
# The load schedule's area totals sum only the rows whose effective parent is OUTSIDE the
# filtered set. With storeys filtered on a level code, the area filter IS a level-code
# filter; the pattern the prompt prints is run here, as printed, on a neutral fixture.
import duckdb  # noqa: E402
TEMPLATE12 = _re10.search(
    r"SELECT SUM\(x\.tcl_kw\) FROM \"panels\" x WHERE <area filter> AND NOT EXISTS "
    r"\(SELECT 1 FROM \"panels\" p WHERE p\.panel = COALESCE\(NULLIF\(x\.rolls_up_to, ''\), "
    r"x\.fed_from\) AND <same area filter on p>\)", SRC)
PLAIN12 = _re10.search(
    r"SELECT SUM\(x\.tcl_kw\) FROM \"panels\" x WHERE <area filter on x> AND NOT EXISTS "
    r"\(SELECT 1 FROM \"panels\" p WHERE p\.panel = x\.fed_from AND <same area filter on p>\)",
    SRC)
check("the prompt still prints both area-total patterns, unchanged",
      TEMPLATE12 is not None and PLAIN12 is not None)
con12 = duckdb.connect(":memory:")
con12.execute('CREATE TABLE "panels" (panel VARCHAR, fed_from VARCHAR, rolls_up_to VARCHAR, '
              'block VARCHAR, level_code VARCHAR, tcl_kw DOUBLE)')
# QM-0 on Z0 feeds QS-4 (on Z4), which feeds QD-4A and QD-4B (on Z4) and QD-5A (on Z5).
# QD-4C on Z4 prints a misspelt parent ('QM0'); its resolved parent QM-0 is in rolls_up_to.
# Topmost on Z4: QS-4 (40) and QD-4C (7), so the Z4 total is 47 - never 47 + 12 + 9.
for row in (("QM-0", "UTILITY", "ROOT", "B", "Z0", 100.0),
            ("QS-4", "QM-0", "", "B", "Z4", 40.0),
            ("QD-4A", "QS-4", "", "B", "Z4", 12.0),
            ("QD-4B", "QS-4", "", "B", "Z4", 9.0),
            ("QD-5A", "QS-4", "", "B", "Z5", 6.0),
            ("QD-4C", "QM0", "QM-0", "B", "Z4", 7.0)):
    con12.execute('INSERT INTO "panels" VALUES (?, ?, ?, ?, ?, ?)', row)
if TEMPLATE12 is not None:
    sql12 = (TEMPLATE12.group(0).replace("<area filter>", "x.level_code = 'Z4'")
             .replace("<same area filter on p>", "p.level_code = 'Z4'"))
    got12 = con12.execute(sql12).fetchall()
    check("the effective-parent pattern with a level-code area filter sums the topmost rows "
          "of that level only", got12 == [(47.0,)], (sql12, got12))
    sql12z5 = (TEMPLATE12.group(0).replace("<area filter>", "x.level_code = 'Z5'")
               .replace("<same area filter on p>", "p.level_code = 'Z5'"))
    check("a child whose parent stands on another level is topmost on its own level",
          con12.execute(sql12z5).fetchall() == [(6.0,)], con12.execute(sql12z5).fetchall())
if PLAIN12 is not None:
    sql12p = (PLAIN12.group(0).replace("<area filter on x>", "x.level_code = 'Z4'")
              .replace("<same area filter on p>", "p.level_code = 'Z4'"))
    # Printed parents only: QD-4C's misspelt parent is no row at all, so it counts as topmost.
    check("the printed-parent pattern with a level-code area filter works the same way",
          con12.execute(sql12p).fetchall() == [(47.0,)], con12.execute(sql12p).fetchall())
con12.close()

print(chr(10) + "13. A level-code column's schema line lists every code it holds (T11a-R5)")
# The rule sends the writer to the level-code column's value list. The schema block samples
# a column's first 500 rows and shows 8 values, and calls them 'possible values' (the
# complete set) when no ninth appeared in those rows - so on a long table whose upper and
# lower storeys come late, it showed some storeys as the complete set and hid the rest.
# Measured on the live corpus: the circuits table's list read '00', '01', '02' of ten codes.
# T9a-R0 (spec-fix5): the schema block now reads EVERY column off every row, so the level code's
# special case is folded into that one path - and the ordinary column of the same shape, which
# this section pinned at its false 500-row 'possible values' line, is read the same way.


class _Exec13:
    def __init__(self, data):
        self.data = data


class _Supabase13:
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
                return _Exec13(list(tables))

        return _Q()


class _Models13:
    prompts = []

    def generate_content(self, *, model, contents, config=None):
        _Models13.prompts.append(contents)
        return type("_R", (), {"text": 'SELECT unit_tag FROM "bld_units13"',
                               "usage_metadata": None})()


class _Client13:
    def __init__(self, *a, **kw):
        self.models = _Models13()


def prompt13(question, tables, cards):
    """The SQL-writer prompt `execute_sql_query` builds, through the real loader and router,
    with a fake writer that keeps it. No model, no network."""
    saved = (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
             sql_tool._load_table_cards)
    sql_tool.genai.Client = _Client13
    sql_tool.get_llm_api_key = lambda: "fake-key"
    sql_tool.get_llm_model = lambda: "fake-model"
    sql_tool._load_table_cards = lambda *a, **k: list(cards)
    _Models13.prompts = []
    try:
        sql_tool.execute_sql_query(question, "u-1", _Supabase13(list(tables)))
    finally:
        (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
         sql_tool._load_table_cards) = saved
    return _Models13.prompts[0] if _Models13.prompts else ""


EARLY13, LATE13 = ["J1", "J2", "J3"], ["J4", "J5", "J6", "J7", "J8", "J9", "JB1", "JB2", "JR"]
ROWS13 = ([{"unit_tag": f"UT-{i:04d}", "shade": EARLY13[i % 3], "level_code": EARLY13[i % 3]}
           for i in range(600)]
          + [{"unit_tag": f"UT-{600 + i:04d}", "shade": c, "level_code": c}
             for i, c in enumerate(LATE13)])
UNITS13 = {"table_name": "bld_units13", "columns": ["unit_tag", "shade", "level_code"],
           "rows": ROWS13, "row_count": len(ROWS13)}
CARD13 = {"table": "bld_units13", "columns": ["unit_tag", "shade"],
          "identifier_column": "unit_tag", "holds": "one row per unit"}
p13 = prompt13("how many units are there?", [UNITS13], [CARD13])
line13 = next((l for l in p13.splitlines() if l.strip().startswith("level_code ")), "")
shade13 = next((l for l in p13.splitlines() if l.strip().startswith("shade ")), "")
check("fixture sanity: the prompt was built and shows both columns",
      bool(line13) and bool(shade13), p13[:600])
check("the level-code column lists EVERY code it holds, the late ones included, as its "
      "possible values",
      "possible values" in line13 and all(repr(c) in line13 for c in EARLY13 + LATE13),
      line13)
check("T9a-R0: an ordinary text column of the same shape is read the same way - every value, "
      "the late ones included, as complete (it was pinned at its false 500-row line)",
      shade13.strip() == "shade (text; possible values (complete): "
      + ", ".join(repr(c) for c in EARLY13 + LATE13) + ")", shade13)

print(chr(10) + "14. T11a-R7: a location id the prompt states replaces the place-name filter")
# Fix round 1. The place-code resolver now writes "The place <code> is location_id <id> (...)"
# into the prompt, and the what-is-in-a-place rule still said "you cannot know a room's id"
# and "NEVER also equate the location-id column". A code printed only as the end of the key is
# in no name column, so for that place the name filter matches nothing: the stated id must
# REPLACE the name filter, and the two clauses must say so. Nothing pinned "you cannot know a
# room's id" before this.
r14 = getattr(sql_tool, "ROOM_CONTENTS_RULE", "")
r14l = r14.lower()
check("RED: the rule defers to a line of the prompt stating the place's location id",
      "the place <code> is location_id <id>" in r14l and "unless this prompt states" in r14l,
      r14[:900])
check("RED: then it filters the location-id column on that id INSTEAD of the name",
      "= '<that id>'" in r14 and "instead of the name" in r14l, r14[:1000])
check("RED: 'you cannot know a room's id' now holds only when no such line states it",
      "you cannot know a room's id unless such a line states it" in r14l, r14[:1300])
check("RED: the never-also-equate clause is scoped to the name filter - a stated id is never "
      "forbidden", "never also equate the location-id column alongside the name filter" in r14l,
      r14[:1100])
check("the measured reason for that clause is kept: a level-and-block id is a different row",
      "level-and-block id is a different row" in r14l, r14[:1200])
check("and it is still one bullet in the prompt",
      r14.startswith("- ") and r14.count(chr(10)) == 0 and "{ROOM_CONTENTS_RULE}" in SRC)

print(chr(10) + "15. The printed-floor prohibition covers only tables that HAVE the keys (fix round 1)")
# The NEVER had no exception: a table with no level-code column and no location key has
# nothing but its printed floor column, and the loop's EMPTY instruction already keeps today's
# advice there (spec-fix3 c). The rule must say the same.
pk15 = getattr(sql_tool, "PLACE_KEYS_RULE", "").lower()
check("RED: the prohibition is scoped to a table with a level-code column or a location key",
      "when the table has a level-code column or a location-id column" in pk15, pk15[:900])
check("RED: a table with neither keeps filtering its own printed floor column, in its format",
      "a table with neither" in pk15 and "its own printed floor column" in pk15, pk15[:1000])

print(chr(10) + "16. A per-level roll-up counts items; a parent/child total keeps the area rule "
      "(fix round 1)")
# "For levels, roll up by the level code" invited a plain per-level SUM of a quantity that
# rolls up a parent/child tree, which double-counts it; and the equipment-type rule's "no
# WHERE" breakdown could be read as dropping the storey a question names. One clause each.
check("RED: the level roll-up is for COUNTS of items",
      "roll up by the level code" in pk15 and "counts of items" in pk15, pk15)
check("RED: a total of a parent/child quantity follows the area-total rule, never a plain "
      "per-level SUM", "area-total" in pk15 and "never a plain per-level sum" in pk15, pk15)
type16 = next((l for l in SRC.splitlines()
               if l.startswith("- For 'how many <equipment type>' questions")), "")
check("fixture sanity: the equipment-type bullet is found", bool(type16))
check("RED: its breakdown drops only the TYPE filter - no longer 'no WHERE' at all",
      ", no WHERE)" not in type16 and "no type filter" in type16.lower(), type16[-500:])
check("RED: and a storey or place filter the question names always stays",
      "storey or place filter" in type16.lower() and "always stays" in type16.lower(),
      type16[-500:])

print(f"{chr(10)}{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
