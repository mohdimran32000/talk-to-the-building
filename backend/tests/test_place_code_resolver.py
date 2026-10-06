"""test_place_code_resolver.py - a place code the question prints, resolved to its location key
(spec-fix3 part d, 2026-10-01).

A question that names a place by its printed CODE - a room number, a design tag - left the SQL
writer to guess where that code lives. Measured on the goal-function run of 2026-09-30: a design
tag existed only as the end of the location key ('<prefix>-<tag>'), the writer compared it with
'=' against columns that print the key or a bare number, matched nothing three steps running,
and the answer said the records held no asset list for the room.

The places table already knows: one row per place, keyed by the location id, with a kind. So
`execute_sql_query` resolves the code in code, before the writer runs: when a coded token in the
question EQUALS, or WHOLE-SEGMENT-ENDS (the key ends with '-' plus the token), exactly ONE
location id of kind 'room', the prompt gets one line:

    The place <token> is location_id <id> (<display_name>)

A level-block code never resolves (it is no room), an asset tag never resolves (a key never
ends with a whole tag, and a fragment inside a longer tag is never a token), a token matching
two rooms adds nothing, and an error drops the line and nothing else.

T11a-R3: the places table is found generically - the table the cards' declared joins name for
the location key, or a table carrying `location_id` and `kind` whose location id is its own key -
never by its name; when the question's routing did not load it, it is read from the user's
tables `execute_sql_query` already fetched.

Every fixture is invented (bld_* tables). Every check marked RED fails against sql_tool.py as it
stood before this change.

Run:
    venv/Scripts/python -X utf8 tests/test_place_code_resolver.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import sql_tool  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


def _place(location_id, kind, display_name, level_code=""):
    return {"location_id": location_id, "kind": kind, "display_name": display_name,
            "level_code": level_code}


# The places table, under a name that says nothing about places. Two rooms both END in
# '-9.47', so '9.47' is ambiguous; 'Q08-318' is a room's design tag, printed only as the end of
# its key; 'ZL8-N' is a level-and-block row and 'ZL8' a level row.
SPACES = {
    "table_name": "bld_spaces",
    "columns": ["location_id", "kind", "display_name", "level_code"],
    "rows": [
        _place("ZL8", "level", "Level 08", "08"),
        _place("ZL8-N", "level_block", "Level 08 Block N", "08"),
        _place("ZR-8.17", "room", "8.17 Hush Corner Block N", "08"),
        _place("ZR-Q08-318", "room", "318 Reading Nook Block N", "08"),
        _place("ZR-K.05", "room", "K.05 Velvet Bay", "00"),
        _place("ZR-9.47", "room", "9.47 Plant Space", "09"),
        _place("ZR-W-9.47", "room", "9.47 West Plant Space", "09"),
    ],
    "row_count": 7,
}
ASSETS = {
    "table_name": "bld_fixtures",
    "columns": ["fixture_tag", "item", "qty", "location_id"],
    "rows": [
        {"fixture_tag": "PMP-Q08-318", "item": "Booster Pump", "qty": "1", "location_id": "ZR-Q08-318"},
        {"fixture_tag": "LMP-8.17-A", "item": "Desk Lamp", "qty": "4", "location_id": "ZR-8.17"},
        {"fixture_tag": "LMP-K.05-A", "item": "Desk Lamp", "qty": "2", "location_id": "ZR-K.05"},
    ],
    "row_count": 3,
}
# A decoy with the same columns and a NAME that says places: the cards' declared join names
# bld_spaces, so this one must never be read for the places.
DECOY = dict(SPACES, table_name="bld_locations",
             rows=[_place("ZR-8.17", "room", "Decoy Room"),
                   _place("ZR-ZZ-8.17", "room", "Second Decoy Room")], row_count=2)
ASSETS_CARD = {
    "table": "bld_fixtures", "columns": ASSETS["columns"], "identifier_column": "fixture_tag",
    "joins_to": ["bld_spaces"],
    "declared_joins": ["bld_spaces — `bld_fixtures.location_id` = `bld_spaces.location_id` "
                       "— the place the fixture is in"],
    "holds": "one row per fixture and the place it is in",
}
SPACES_CARD = {
    "table": "bld_spaces", "columns": SPACES["columns"], "identifier_column": "location_id",
    "joins_to": [], "declared_joins": [], "holds": "one row per place",
}
DECOY_CARD = dict(SPACES_CARD, table="bld_locations", holds="an older list of places")
CARDS = [ASSETS_CARD, SPACES_CARD]

LINE = "The place {} is location_id {} ({})"


# --------------------------------------------------------------------------- the harness
class _Exec:
    def __init__(self, data):
        self.data = data


class _Supabase:
    """Serves every table for `structured_data`; `.eq()` and `.order()` are accepted."""

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
    prompts = []

    def generate_content(self, *, model, contents, config=None):
        _Models.prompts.append(contents)
        return type("_R", (), {"text": _Models.sql, "usage_metadata": None})()


class _Client:
    def __init__(self, *a, **kw):
        self.models = _Models()


FIXTURE_SQL = 'SELECT fixture_tag, item, qty FROM "bld_fixtures"'


def run(question, tables=(ASSETS, SPACES), cards=CARDS, sql=FIXTURE_SQL, routed=None):
    """(the SQL-writer prompt, the result) of `execute_sql_query` end to end - the real loader,
    DuckDB and result assembly - with a fake writer that keeps its prompt. `routed`, when given,
    is the router's selection, so a test can leave the places table out of what is loaded."""
    saved = (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
             sql_tool._load_table_cards, sql_tool._routed_table_names)
    sql_tool.genai.Client = _Client
    sql_tool.get_llm_api_key = lambda: "fake-key"
    sql_tool.get_llm_model = lambda: "fake-model"
    sql_tool._load_table_cards = lambda *a, **k: list(cards)
    if routed is not None:
        sql_tool._routed_table_names = lambda *a, **k: list(routed)
    _Models.sql, _Models.prompts = sql, []
    try:
        out = sql_tool.execute_sql_query(question, "u-1", _Supabase(list(tables)))
    finally:
        (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
         sql_tool._load_table_cards, sql_tool._routed_table_names) = saved
    return (_Models.prompts[0] if _Models.prompts else ""), out


def place_lines(prompt):
    return [ln for ln in prompt.splitlines() if ln.startswith("The place ")]


tokens_of = getattr(sql_tool, "place_tokens", None)
resolve = getattr(sql_tool, "resolve_place_codes", None)


def resolved(question, rows=SPACES["rows"]):
    """`resolve_place_codes` over the fixture places table, or a marker when it is missing (the
    code before this change), so the file runs to its end and reports every RED."""
    if resolve is None:
        return "<no resolve_place_codes>"
    return resolve(question, rows, "location_id", "kind", "display_name")


# ---------------------------------------------------------------------------
print("1. Which tokens are place codes (RED)")
# ---------------------------------------------------------------------------
check("RED: place_tokens exists", callable(tokens_of))
if callable(tokens_of):
    check("a room number, a lettered room number, a design tag and a whole key are codes",
          tokens_of("rooms 8.17, K.05 and Q08-318, and ZR-8.17")
          == ["8.17", "K.05", "Q08-318", "ZR-8.17"], tokens_of("rooms 8.17, K.05 and Q08-318, and ZR-8.17"))
    check("a bare number is never one, however long - nor is a short code",
          tokens_of("the 318 lamps of 8093 on ZL8 and Z3") == [],
          tokens_of("the 318 lamps of 8093 on ZL8 and Z3"))
    check("prose punctuation around a code is not part of it",
          tokens_of("is it (8.17)? or K.05's, or Q08-318.") == ["8.17", "K.05", "Q08-318"],
          tokens_of("is it (8.17)? or K.05's, or Q08-318."))
    check("a tag with a bracketed segment is ONE token, never its fragments",
          tokens_of("board DB-08(N)-SP-01 feeds it") == ["DB-08(N)-SP-01"],
          tokens_of("board DB-08(N)-SP-01 feeds it"))
    check("a code printed inside a longer tag is never a token of its own",
          tokens_of("camera CAM-L8N-S-HUB-1-DET-02 dropped") == ["CAM-L8N-S-HUB-1-DET-02"],
          tokens_of("camera CAM-L8N-S-HUB-1-DET-02 dropped"))
    check("each token once, in the order printed, whatever its case",
          tokens_of("8.17 or k.05 then K.05 and 8.17 again") == ["8.17", "k.05"],
          tokens_of("8.17 or k.05 then K.05 and 8.17 again"))

# ---------------------------------------------------------------------------
print("\n2. A code resolves to its single room - exactly, or as the whole end of the key (RED)")
# ---------------------------------------------------------------------------
check("RED: resolve_place_codes exists", callable(resolve))
check("RED: a design tag printed only as the END of the key resolves to that room",
      resolved("What is room Q08-318, and what is installed in it?")
      == [("Q08-318", "ZR-Q08-318", "318 Reading Nook Block N")],
      resolved("What is room Q08-318, and what is installed in it?"))
check("RED: a room number resolves the same way",
      resolved("what is in room 8.17?") == [("8.17", "ZR-8.17", "8.17 Hush Corner Block N")],
      resolved("what is in room 8.17?"))
check("RED: the key printed whole resolves by equality",
      resolved("list everything at ZR-K.05") == [("ZR-K.05", "ZR-K.05", "K.05 Velvet Bay")],
      resolved("list everything at ZR-K.05"))
check("RED: any case", resolved("what is in k.05?") == [("k.05", "ZR-K.05", "K.05 Velvet Bay")],
      resolved("what is in k.05?"))
check("RED: two codes, two lines, in the order printed",
      resolved("compare 8.17 with Q08-318") == [("8.17", "ZR-8.17", "8.17 Hush Corner Block N"),
                                               ("Q08-318", "ZR-Q08-318", "318 Reading Nook Block N")],
      resolved("compare 8.17 with Q08-318"))

# ---------------------------------------------------------------------------
print("\n3. What never resolves (RED where the function is missing)")
# ---------------------------------------------------------------------------
check("a level-and-block code adds nothing - it is no room",
      resolved("what is on ZL8-N?") == [], resolved("what is on ZL8-N?"))
check("an asset tag adds nothing, even one that ENDS with a room's tag",
      resolved("where is pump PMP-Q08-318?") == [], resolved("where is pump PMP-Q08-318?"))
check("a token matching TWO rooms adds nothing",
      resolved("what is in room 9.47?") == [], resolved("what is in room 9.47?"))
check("a partial segment is no match: '08-318' is not the end of 'Q08-318'",
      resolved("what is in 08-318?") == [], resolved("what is in 08-318?"))
check("a code no room carries adds nothing", resolved("what is in room 8.93?") == [],
      resolved("what is in room 8.93?"))
check("a bare number adds nothing, though a key ends in it", resolved("what is in room 318?") == [],
      resolved("what is in room 318?"))
check("a question printing no code adds nothing", resolved("what is in the hush corner?") == [],
      resolved("what is in the hush corner?"))

# ---------------------------------------------------------------------------
print("\n4. The line reaches the SQL-writer prompt, verbatim (RED)")
# ---------------------------------------------------------------------------
p4, out4 = run("What is room Q08-318, and what is installed in it?")
want4 = LINE.format("Q08-318", "ZR-Q08-318", "318 Reading Nook Block N")
check("RED: the prompt carries the line, word for word", place_lines(p4) == [want4],
      place_lines(p4) or p4[-600:])
check("it stands after the rules and right before the user question",
      bool(place_lines(p4)) and p4.index(want4) > p4.index("Rules:")
      and p4.index(want4) < p4.index("User question:"), p4[-400:])
check("the query still runs: the result is the writer's", "| PMP-Q08-318 | Booster Pump |" in out4,
      out4[:300])
p4b, _ = run("what is on ZL8-N, and in 9.47?")
check("a level-and-block code and an ambiguous number add no line to the prompt",
      place_lines(p4b) == [], place_lines(p4b))

# ---------------------------------------------------------------------------
print("\n5. T11a-R3: the places table is found generically, and read when not loaded (RED)")
# ---------------------------------------------------------------------------
p5, out5 = run("what is in room 8.17?", routed=["bld_fixtures"])
check("RED: with the places table NOT routed, the line is still there - read from the user's "
      "tables", place_lines(p5) == [LINE.format("8.17", "ZR-8.17", "8.17 Hush Corner Block N")],
      place_lines(p5) or p5[-400:])
check("and the places table is not loaded for the writer: its schema is not in the prompt",
      "Table: bld_spaces" not in p5 and "Table: bld_fixtures" in p5, p5[:400])
p5b, _ = run("what is in room 8.17?", tables=(ASSETS, DECOY, SPACES),
             cards=[ASSETS_CARD, SPACES_CARD, DECOY_CARD])
check("RED: the declared join names the places table - a decoy whose NAME says places is "
      "never read", place_lines(p5b) == [LINE.format("8.17", "ZR-8.17", "8.17 Hush Corner Block N")],
      place_lines(p5b))
KEYED = {
    "table_name": "bld_plots", "columns": ["plot_key", "kind", "label"],
    "rows": [{"plot_key": "ZR-8.17", "kind": "room", "label": "Seven Twelve"}], "row_count": 1,
}
KEYED_ASSETS_CARD = dict(ASSETS_CARD, declared_joins=[
    "bld_plots — `bld_fixtures.location_id` = `bld_plots.plot_key` — the plot"])
p5c, _ = run("what is in room 8.17?", tables=(ASSETS, KEYED), cards=[KEYED_ASSETS_CARD])
check("RED: the declared join's own key column is the one read, whatever it is called; with no "
      "display column the line names the id alone",
      place_lines(p5c) == ["The place 8.17 is location_id ZR-8.17"], place_lines(p5c))
p5d, _ = run("what is in room 8.17?", cards=[])
check("RED: with no cards at all, a loaded table carrying location_id and kind, keyed by it, "
      "is the places table", place_lines(p5d) == [LINE.format("8.17", "ZR-8.17",
                                                              "8.17 Hush Corner Block N")],
      place_lines(p5d))
STAMPED = {   # location_id and kind, but location_id repeats: its rows are not places
    "table_name": "bld_boards", "columns": ["board", "kind", "location_id"],
    "rows": [{"board": "QB-1", "kind": "room", "location_id": "ZR-8.17"},
             {"board": "QB-2", "kind": "room", "location_id": "ZR-8.17"},
             {"board": "QB-3", "kind": "room", "location_id": "ZR-ZZ-8.17"}],
    "row_count": 3,
}
p5e, _ = run("what is in room 8.17?", tables=(ASSETS, STAMPED), cards=[],
             sql='SELECT board FROM "bld_boards"')
check("a table whose location_id repeats is no places table: nothing resolves from it",
      place_lines(p5e) == [], place_lines(p5e))

# ---------------------------------------------------------------------------
print("\n6. An error drops the line and nothing else; the step suffix is never resolved")
# ---------------------------------------------------------------------------
saved6 = getattr(sql_tool, "resolve_place_codes", None)


def _boom(*a, **k):
    raise RuntimeError("resolver down")


sql_tool.resolve_place_codes = _boom
try:
    p6, out6 = run("what is in room 8.17?")
finally:
    if saved6 is None:
        del sql_tool.resolve_place_codes
    else:
        sql_tool.resolve_place_codes = saved6
check("a resolver that raises: no line, and the prompt is otherwise built",
      place_lines(p6) == [] and "User question: what is in room 8.17?" in p6, place_lines(p6))
check("and the query runs and answers as ever", "| LMP-8.17-A | Desk Lamp |" in out6, out6[:300])
# Not routed, so only the resolver reads it: a malformed table LOADED into DuckDB breaks the
# loader whatever this change does, and that is not what is being asked here.
BROKEN = dict(SPACES, rows=[{"location_id": "ZR-8.17"}, "not a row"])
p6b, out6b = run("what is in room 8.17?", tables=(ASSETS, BROKEN), routed=["bld_fixtures"])
check("malformed places rows: no line, and the result is unharmed",
      place_lines(p6b) == [] and "| LMP-8.17-A | Desk Lamp |" in out6b, (place_lines(p6b), out6b[:200]))
q6 = ("what is in room 8.17?\n(Investigation step 2: The previous query returned no rows. "
      "Previous SQL: `SELECT * FROM \"bld_fixtures\" WHERE location_id = 'ZR-K.05'`)")
p6c, _ = run(q6)
check("RED: a loop step's own text is never resolved - only the question's codes are",
      place_lines(p6c) == [LINE.format("8.17", "ZR-8.17", "8.17 Hush Corner Block N")],
      place_lines(p6c))
p6d, _ = run("what is in the hush corner?")
check("a question printing no code: no line at all", place_lines(p6d) == [], place_lines(p6d))

# ---------------------------------------------------------------------------
print("\n7. T11a-R8: a number that is a MEASUREMENT never names a place (fix round 1, RED)")
# ---------------------------------------------------------------------------
# Found in review: a two-decimal number in the room-number range was taken for a place code -
# "the 8.71 kW ..." put "The place 8.71 is location_id ..." in the prompt - because a room
# numbered that way exists. A NUMBER-ONLY token (no letter) now names a place only when it is no
# measurement: never when a unit of measure follows it (with or without a space, any case), and
# never when it is joined to another number by '/', 'x', '×' or '-'. A token with a letter keeps
# its old behaviour. These rooms carry exactly the numbers the probes print.
ROOMS_R8 = [_place(f"ZR-{n}", "room", f"{n} Plant Space Block N", n[0].zfill(2))
            for n in ("8.71", "8.79", "8.63", "9.69", "9.73")]
ROWS_R8 = SPACES["rows"] + ROOMS_R8
MEASURED = [
    "Which circuits feed the 8.71 kW pumps?",
    "Is the 8.79kW heater on that board?",
    "Which boards carry 8.63 KW of load?",
    "Which sockets sit 9.69 m above the slab?",
    "Is there a 8.71 kVA unit?", "a 8.71 A breaker", "rated 8.71 V", "at 8.71 Hz",
    "a cable of 8.71 mm", "a floor of 8.71 m² in all", "8.71 m2 of carpet", "8.71 sq m of tiles",
    "a tank of 8.71 L", "8.71 litres of water", "a 8.71 kg extinguisher", "8.71% of the load",
    "8.71 % of the load", "after 8.71 hours", "for 8.71 hrs", "held at 8.71 °C",
    "a run of 8.71 kWh", "the 8.71 MVA transformer",
]
for q in MEASURED:
    check(f"RED: a measurement names no place: {q!r}", resolved(q, ROWS_R8) == [],
          resolved(q, ROWS_R8))
JOINED = ["a panel of 8.71 x 9.73", "a panel of 8.71 × 9.73", "a panel of 8.71×9.73",
          "the ratio 8.71 / 9.73", "the span 8.71 - 9.73", "the span 8.71-9.73"]
for q in JOINED:
    check(f"RED: a number joined to another number names no place: {q!r}",
          resolved(q, ROWS_R8) == [], resolved(q, ROWS_R8))
STILL = {"what is in room 8.71?": "8.71", "what is in 8.71?": "8.71", "8.71": "8.71",
         "8.71, the plant space - what is in it?": "8.71",
         "is room 8.71 a plant space?": "8.71", "list everything in 9.73 and its loads": "9.73"}
for q, code in STILL.items():
    got = resolved(q, ROWS_R8)
    check(f"a place number with no unit still resolves: {q!r}",
          isinstance(got, list) and [g[:2] for g in got] == [(code, f"ZR-{code}")], got)
check("a code with a LETTER keeps its old behaviour, a unit after it or not",
      resolved("is K.05 kW rated?", ROWS_R8) == [("K.05", "ZR-K.05", "K.05 Velvet Bay")],
      resolved("is K.05 kW rated?", ROWS_R8))
p7, _ = run("Which circuits feed the 8.71 kW pumps, and what is in 8.79?",
            tables=(ASSETS, dict(SPACES, rows=ROWS_R8, row_count=len(ROWS_R8))))
check("RED: end to end, the measurement adds no line and the room number still does",
      place_lines(p7) == [LINE.format("8.79", "ZR-8.79", "8.79 Plant Space Block N")],
      place_lines(p7))

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED'}")
sys.exit(1 if FAILS else 0)
