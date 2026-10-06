"""test_sql_places_line.py - a PLACES line names each place a where-question's result points
at, with its level (wave 6, W6-A1, 2026-10-06).

THE DEFECT (diagnosed on the goal-function run of 2026-09-30). A where-question's SQL read a
folded assets table: a location-key column, a display-name column, item, qty. The answer named
each place by its display name but never its FLOOR - nothing in the result states it in words,
so an answer built only from the result left out the one thing a "where" question actually asks.

THE FIX. When the question - read WITHOUT the loop's step suffix - asks where something is (a
small word-boundary, case-insensitive detector: "where", "located", "location(s)", "which
floor/level/room/block"), and the result's own columns carry the spine's location key
(`_LOCATION_KEY`, exactly as `_places_of`/`_places_table` already recognise it - never a table
name) with 1 to PLACES_MAX_IDS (12) distinct, non-blank values: each id is looked up in the
places table (`_places_table`, found the same generic way `_place_lines` finds it - never by its
name) for its own display name and its level's name (`_level_name_column`, the SAME rule
`_places_of` already uses for a joined table's level column - refactored out so both share it).
An id that IS a level (its own row's kind is "level") gives just its name - there is no separate
level to add. An id the places table does not hold is skipped. The whole line is left out when
nothing resolves, when there is no such column, when there are 0 or more than PLACES_MAX_IDS
distinct ids, or when the question does not ask where. Pure - no query; the caller
(`_companion`) drops the line, and nothing else, if it raises.

Every table, column and value here is invented. Every check marked RED fails against the code
as it stood before this change.

Run:
    venv/Scripts/python -X utf8 tests/test_sql_places_line.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import openai_client as oc  # noqa: E402
from app.services import sql_tool  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


def _place(location_id, kind, display_name, level_name=""):
    return {"location_id": location_id, "kind": kind, "display_name": display_name,
            "level_name": level_name}


# A places table under a name that says nothing about places - one LEVEL row (its own
# level_name is blank: a level names itself through its display_name, never a second column
# naming its own level) and several ROOM rows across two levels.
SPACES = {
    "table_name": "bld_spaces",
    "columns": ["location_id", "kind", "display_name", "level_name"],
    "rows": [
        _place("LVL-SB1", "level", "Sub-Level 1", ""),
        _place("RM-SB-STORE-1", "room", "Store 1", "Sub-Level 1"),
        _place("RM-SB-STORE-2", "room", "Store 2", "Sub-Level 1"),
        _place("RM-2-OFFICE-1", "room", "Office 201", "Level 02"),
    ],
    "row_count": 4,
}
ASSETS = {
    "table_name": "bld_spares",
    "columns": ["item", "qty", "location_id"],
    "rows": [
        {"item": "Pump Seal Kit", "qty": "3", "location_id": "RM-SB-STORE-1"},
        {"item": "Fan Belt", "qty": "5", "location_id": "RM-SB-STORE-2"},
        {"item": "Fuse Pack", "qty": "9", "location_id": "RM-9-NOWHERE"},
    ],
    "row_count": 3,
}
ASSETS_CARD = {
    "table": "bld_spares", "columns": ASSETS["columns"], "identifier_column": "item",
    "joins_to": ["bld_spaces"],
    "declared_joins": ["bld_spaces — `bld_spares.location_id` = `bld_spaces.location_id` "
                       "— the place the item is kept"],
    "holds": "one row per spare item and the place it is kept",
}
SPACES_CARD = {
    "table": "bld_spaces", "columns": SPACES["columns"], "identifier_column": "location_id",
    "joins_to": [], "declared_joins": [], "holds": "one row per place",
}
CARDS = [ASSETS_CARD, SPACES_CARD]


# --------------------------------------------------------------------------- the harness
class _Exec:
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
                return _Exec(list(tables))

        return _Q()


class _Models:
    sql = ""

    def generate_content(self, *, model, contents, config=None):
        return type("_R", (), {"text": _Models.sql, "usage_metadata": None})()


class _Client:
    def __init__(self, *a, **kw):
        self.models = _Models()


FIXTURE_SQL = 'SELECT item, qty, location_id FROM "bld_spares" WHERE qty IS NOT NULL'


def run(question, tables=(ASSETS, SPACES), cards=CARDS, sql=FIXTURE_SQL):
    """`execute_sql_query` end to end - the real loader, DuckDB and result assembly - with a
    fake writer returning `sql`. No model, no network."""
    saved = (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
             sql_tool._load_table_cards)
    sql_tool.genai.Client = _Client
    sql_tool.get_llm_api_key = lambda: "fake-key"
    sql_tool.get_llm_model = lambda: "fake-model"
    sql_tool._load_table_cards = lambda *a, **k: list(cards)
    _Models.sql = sql
    try:
        return sql_tool.execute_sql_query(question, "u-1", _Supabase(list(tables)))
    finally:
        (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
         sql_tool._load_table_cards) = saved


PLACES_HEADING = getattr(sql_tool, "PLACES_HEADING", "PLACES - ")


def places_line_of(text):
    return next((ln for ln in text.splitlines() if ln.startswith(PLACES_HEADING)), "")


# ===========================================================================
print("1. A where-question's result, two resolvable ids (RED)")
# ===========================================================================
out1 = run("Where are the spare parts kept?")
check("the original figure is untouched", "Pump Seal Kit" in out1, out1[:300])
line1 = places_line_of(out1)
check("RED: a PLACES line appears", bool(line1), out1[-600:])
check("RED: it names each id's display name and its level, in the format the spec gives",
      line1 == "PLACES - RM-SB-STORE-1 = Store 1, Sub-Level 1; "
               "RM-SB-STORE-2 = Store 2, Sub-Level 1",
      line1)

# ===========================================================================
print("\n2. Silent shapes - no line")
# ===========================================================================
out2 = run("List the spare parts and their quantities.")
check("a non-where question: no line, even though location_id is right there",
      not places_line_of(out2), out2[-400:])

out2b = run("Is this part listed anywhere else in the register?")
check("'anywhere' is not 'where' - no word-boundary match, so no line",
      not places_line_of(out2b), out2b[-400:])

out2c = run("Was this item ever relocated from its original place?")
check("'relocated' is not 'located' - no word-boundary match, so no line",
      not places_line_of(out2c), out2c[-400:])

SUFFIX = sql_tool._STEP_SUFFIX_MARKER + "2: filter on the printed name.)"
out2d = run("List the spare parts and their quantities." + SUFFIX
           + "\n`SQL: " + FIXTURE_SQL + "`)")
check("the loop's own step suffix is never read for 'where' - a plain question stays plain "
      "even when a later step's own text carries the word",
      not places_line_of(out2d), out2d[-400:])

MANY_ROWS = [{"item": f"Part {i}", "qty": "1", "location_id": f"RM-{i}-X"} for i in range(13)]
MANY_SPACES = [_place(f"RM-{i}-X", "room", f"Room {i}", "Level 1") for i in range(13)]
out2e = run("Where are all the parts kept?",
           tables=({"table_name": "bld_spares", "columns": ASSETS["columns"],
                    "rows": MANY_ROWS, "row_count": 13},
                   {"table_name": "bld_spaces", "columns": SPACES["columns"],
                    "rows": MANY_SPACES, "row_count": 13}),
           sql='SELECT item, qty, location_id FROM "bld_spares"')
check("RED: 13 distinct ids (over PLACES_MAX_IDS) - no line at all",
      not places_line_of(out2e), out2e[-400:])

out2f = run("Where is the fuse pack kept?",
           sql='SELECT item, location_id FROM "bld_spares" WHERE item = \'Fuse Pack\'')
check("RED: an id the places table does not hold (RM-9-NOWHERE) resolves nothing - no line",
      not places_line_of(out2f), out2f[-400:])

# ===========================================================================
print("\n3. A level id names itself alone")
# ===========================================================================
out3 = run("Where is the standby pump kept?",
          tables=({"table_name": "bld_spares", "columns": ASSETS["columns"],
                   "rows": [{"item": "Standby Pump", "qty": "1", "location_id": "LVL-SB1"}],
                   "row_count": 1}, SPACES),
          sql='SELECT item, qty, location_id FROM "bld_spares"')
line3 = places_line_of(out3)
check("RED: a level id gives just its own name - no second ', <level>' part",
      line3 == "PLACES - LVL-SB1 = Sub-Level 1", line3)

# ===========================================================================
print("\n4. An error inside drops only the line")
# ===========================================================================
real_places_table = sql_tool._places_table


def _boom(*a, **k):
    raise RuntimeError("places table lookup failed")


sql_tool._places_table = _boom
try:
    out4 = run("Where are the spare parts kept?")
finally:
    sql_tool._places_table = real_places_table
check("RED: the result still carries everything else",
      "Pump Seal Kit" in out4 and "SQL: `" in out4, out4[:300])
check("...and only the PLACES line is gone", not places_line_of(out4), out4[-400:])

# ===========================================================================
print("\n5. Helper-level: the where-question detector")
# ===========================================================================
asks_where = getattr(sql_tool, "_asks_where", None)
check("_asks_where exists", callable(asks_where))
if callable(asks_where):
    check("plain 'where'", asks_where("Where are the spares kept?"))
    check("case-insensitive", asks_where("WHERE IS IT"))
    check("'located'", asks_where("What room is this located in?"))
    check("'location'", asks_where("What is its location?"))
    check("'locations'", asks_where("What are the locations of the spares?"))
    check("'which floor'", asks_where("Which floor is the server room on?"))
    check("'which level'", asks_where("Which level is it on?"))
    check("'which room'", asks_where("Which room is the pump in?"))
    check("'which block'", asks_where("Which block supplies this panel?"))
    check("a plain count question does not ask where",
          not asks_where("How many spare parts are there?"))
    check("'anywhere' does not match 'where' (word boundary)",
          not asks_where("Is this listed anywhere else?"))
    check("'relocated' does not match 'located' (word boundary)",
          not asks_where("Was it ever relocated?"))
    check("'which' alone, with no floor/level/room/block after it, does not match",
          not asks_where("Which pump is the biggest?"))

# ===========================================================================
print("\n6. The answer rule carries the PLACES bullet; none of it in the frozen tool-choice copy")
# ===========================================================================
rules = oc.OUTPUT_FORMAT_RULES
bullet = next((ln for ln in rules.splitlines() if "PLACES" in ln), "")
check("RED: OUTPUT_FORMAT_RULES has a PLACES bullet", bool(bullet), rules[-600:])
check("RED: it says to answer with the name and level the line gives",
      "level" in bullet.lower(), bullet)
check("RED: it says never to print the ids themselves",
      "ids" in bullet.lower(), bullet)
check("RED: never print the line, its heading, or a table or column name",
      "Never print the line" in bullet or "Never print" in bullet, bullet)
frozen_bullet = next((ln for ln in oc.TOOL_CHOICE_FORMAT_RULES.splitlines() if "PLACES" in ln),
                     "")
check("RED: the frozen tool-choice copy carries none of it (ruling W7)", frozen_bullet == "")

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
