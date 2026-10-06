"""test_sql_hierarchy.py - a question about a whole chain of a parent-reference tree gets
the chain walked in code (spec-fix2, 2026-10-01).

Two kinds of question ask for more than one hop of a parent -> child tree: everything BELOW
a node ("if X trips, what loses power?") and everything ABOVE it ("what feeds X, all the
way back to the main supply?"). The SQL writer only ever writes one hop, or a fixed two-hop
self-join, so the answer listed part of the subtree and a chain that stopped short of the
top. Recursive SQL from the writer would collide with the prompt's ban on set operations,
so `execute_sql_query` now walks the tree itself and appends ONE line:

    HIERARCHY - everything fed from X (N rows, D tiers): depth 1: A (level), ...; depth 2: ...
    HIERARCHY - X (level) is fed from A <- B <- ... <- ROOT

It fires only when the question uses tree wording AND prints, whole, a code from the
identifier column of a loaded table that has a FEED reference column (the fed_from family) -
never a containment reference such as parent_id. The parent is the EFFECTIVE parent - a
resolved `rolls_up_to` wins over the printed parent - and the level names come from the
places table the card's declared location join names.

Every fixture is invented (bld_* tables). Every check marked RED fails against sql_tool.py
as it stood before this change; those of section 7, and the "tiers" wording everywhere,
also fail against this change's first version (before review fix round 1).

Run:
    venv/Scripts/python -X utf8 tests/test_sql_hierarchy.py
"""
import inspect
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import openai_client, sql_loop, sql_tool  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


HEAD = "HIERARCHY - "


def _row(ref, kind, fed_from, rolls_up_to, location_id, notes=""):
    return {"unit_ref": ref, "kind": kind, "fed_from": fed_from, "rolls_up_to": rolls_up_to,
            "location_id": location_id, "notes": notes}


# A small supply tree. QD-13's printed parent is misspelt ('QS1-AUXX'); its resolved parent
# is in rolls_up_to. QB-9 has no place. QG-1 is fed from something that has no row of its own.
TREE = {
    "table_name": "bld_feedtree",
    "columns": ["unit_ref", "kind", "fed_from", "rolls_up_to", "location_id", "notes"],
    "rows": [
        _row("QM-1", "head", "UTILITY INTAKE", "ROOT", "ZL-0"),
        _row("QS-1", "branch", "QM-1", "", "ZL-1"),
        _row("QS-2", "branch", "QM-1", "", "ZL-2"),
        _row("QS-1-AUX", "branch", "QS-1", "", "ZL-1"),
        _row("QD-11", "leaf", "QS-1", "", "ZL-1", "spare way kept for a later unit"),
        _row("QD-12", "leaf", "QS-1-AUX", "", "ZL-1"),
        _row("QD-13", "leaf", "QS1-AUXX", "QS-1-AUX", "ZL-2"),
        _row("QD-21", "leaf", "QS-2", "", "ZL-2"),
        _row("QB-9", "leaf", "QM-1", "", ""),
        _row("QG-1", "head", "HIRED SET", "", "ZL-0"),
        # 'QS-2' is printed whole inside 'QS-2 (NEW)' (a space follows it), so only the
        # longest-match rule tells the two apart.
        _row("QS-2 (NEW)", "branch", "QG-1", "", "ZL-2"),
        _row("QD-22", "leaf", "QS-2 (NEW)", "", "ZL-2"),
    ],
    "row_count": 12,
}
PLACES = {
    "table_name": "bld_places",
    "columns": ["place_ref", "kind", "parent_id", "level_name"],
    "rows": [
        {"place_ref": "ZL-0", "kind": "level", "parent_id": "", "level_name": "Zero Deck"},
        {"place_ref": "ZL-1", "kind": "level", "parent_id": "ZL-0", "level_name": "First Deck"},
        {"place_ref": "ZL-2", "kind": "level", "parent_id": "ZL-0", "level_name": "Second Deck"},
    ],
    "row_count": 3,
}
TREE_CARD = {
    "table": "bld_feedtree", "columns": TREE["columns"], "identifier_column": "unit_ref",
    "joins_to": ["bld_places"],
    "declared_joins": ["bld_places — `bld_feedtree.location_id` = `bld_places.place_ref` "
                       "— the deck the unit stands on"],
    "holds": "one row per supply unit and the unit it is fed from",
}
PLACES_CARD = {
    "table": "bld_places", "columns": PLACES["columns"], "identifier_column": "place_ref",
    "joins_to": [], "declared_joins": [], "holds": "one row per deck",
}
CARDS = [TREE_CARD, PLACES_CARD]


class _ExecResult:
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
                return _ExecResult(list(tables))

        return _Q()


class _Models:
    sql = ""

    def generate_content(self, *, model, contents, config=None):
        return type("_R", (), {"text": _Models.sql, "usage_metadata": None})()


class _Client:
    def __init__(self, *a, **kw):
        self.models = _Models()


def run(question, sql, tables=(TREE, PLACES), cards=CARDS):
    """`execute_sql_query` end to end - the real loader, router, DuckDB and result assembly -
    with a fake SQL writer that returns `sql`. No model, no network."""
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


def hierarchy_lines(text):
    return [ln for ln in text.splitlines() if ln.startswith(HEAD)]


def direction(question):
    """`sql_tool.tree_direction(question)`, or a marker string when the helper is missing
    (the code before this change), so the file runs to its end and reports every RED."""
    fn = getattr(sql_tool, "tree_direction", None)
    if fn is None:
        return "<no tree_direction>"
    return fn(question)


ONE_HOP = ("SELECT unit_ref, fed_from FROM \"bld_feedtree\" "
           "WHERE COALESCE(NULLIF(rolls_up_to, ''), fed_from) = 'QS-1'")

# ---------------------------------------------------------------------------
print("1. An outage question naming a node lists every descendant, by depth, via the "
      "EFFECTIVE parent (RED)")
# ---------------------------------------------------------------------------
Q1 = "If QS-1 trips, which units lose power?"
LINE1 = ("HIERARCHY - everything fed from QS-1 (4 rows, 2 tiers): depth 1: QD-11 (First Deck), "
         "QS-1-AUX (First Deck); depth 2: QD-12 (First Deck), QD-13 (Second Deck)")
out1 = run(Q1, ONE_HOP)
check("the writer's own one-hop result is still there", "| QS-1-AUX | QS-1 |" in out1, out1[:300])
check("RED: the result carries exactly one HIERARCHY line, listing every descendant with its "
      "depth and level", hierarchy_lines(out1) == [LINE1], hierarchy_lines(out1) or out1[-500:])
check("RED: the child whose rolls_up_to overrides a misspelt fed_from is listed (fed_from alone "
      "would never reach it)", "QD-13 (Second Deck)" in "".join(hierarchy_lines(out1)),
      hierarchy_lines(out1))
check("a unit fed from elsewhere is not in the subtree",
      "QG-1" not in "".join(hierarchy_lines(out1)) and "QD-21" not in "".join(hierarchy_lines(out1)),
      hierarchy_lines(out1))
check("the line comes after the SQL line and is never part of the rendered table",
      bool(hierarchy_lines(out1)) and out1.index("SQL: `") < out1.index(HEAD)
      and not any(ln.startswith("|") and HEAD in ln for ln in out1.splitlines()), out1)
imp = "IMPORTANT (for interpreting these results)"
check("it comes before the hierarchy note and the SOURCE lines",
      bool(hierarchy_lines(out1)) and imp in out1 and out1.index(HEAD) < out1.index(imp)
      < out1.index("SOURCE - bld_feedtree"), out1[-900:])
# Wave 3, G5: every notes bullet opens with its row's identifier (the card's identifier column) -
# re-pinned deliberately from the unkeyed "- spare way kept for a later unit".
check("the notes block still ENDS the result",
      out1.rstrip().endswith("- QD-11: spare way kept for a later unit"), out1[-300:])

Q1b = "If QM-1 is switched off, which units lose supply?"
LINE1b = ("HIERARCHY - everything fed from QM-1 (8 rows, 3 tiers): depth 1: QB-9, QS-1 (First Deck), "
          "QS-2 (Second Deck); depth 2: QD-11 (First Deck), QD-21 (Second Deck), QS-1-AUX "
          "(First Deck); depth 3: QD-12 (First Deck), QD-13 (Second Deck)")
out1b = run(Q1b, "SELECT unit_ref FROM \"bld_feedtree\" WHERE fed_from = 'QM-1'")
check("RED: three tiers deep; a node with no place is listed without a level",
      hierarchy_lines(out1b) == [LINE1b], hierarchy_lines(out1b) or out1b[-500:])

# ---------------------------------------------------------------------------
print("\n2. An all-the-way-back question gets the chain to ROOT with each hop's level (RED)")
# ---------------------------------------------------------------------------
Q2 = "What feeds QD-13, all the way back to the main intake?"
LINE2 = ("HIERARCHY - QD-13 (Second Deck) is fed from QS-1-AUX (First Deck) <- QS-1 (First Deck) "
         "<- QM-1 (Zero Deck) <- ROOT")
out2 = run(Q2, "SELECT fed_from FROM \"bld_feedtree\" WHERE unit_ref = 'QD-13'")
check("RED: the chain to ROOT, through the resolved parent, with each hop's level",
      hierarchy_lines(out2) == [LINE2], hierarchy_lines(out2) or out2[-500:])

out2b = run("Trace the full supply path of QG-1.",
            "SELECT fed_from FROM \"bld_feedtree\" WHERE unit_ref = 'QG-1'")
check("RED: a parent with no row of its own ends the chain, named as printed",
      hierarchy_lines(out2b) == ["HIERARCHY - QG-1 (Zero Deck) is fed from HIRED SET"],
      hierarchy_lines(out2b) or out2b[-400:])

out2c = run(Q2, "SELECT fed_from FROM \"bld_feedtree\" WHERE unit_ref = 'QD-13'",
            tables=(TREE,))
check("RED: with no places table loaded the chain is walked all the same, without levels",
      hierarchy_lines(out2c) == ["HIERARCHY - QD-13 is fed from QS-1-AUX <- QS-1 <- QM-1 <- ROOT"],
      hierarchy_lines(out2c) or out2c[-400:])

# A table with no rolls_up_to and no card at all: its feed column itself - any name in the
# fed_from family, here `fed_from_duct` - and the column its values point at found from the
# data.
DUCTS = {
    "table_name": "bld_ducts",
    "columns": ["duct_no", "fed_from_duct", "notes"],
    "rows": [
        {"duct_no": "VX-1", "fed_from_duct": "", "notes": ""},
        {"duct_no": "VX-2", "fed_from_duct": "VX-1", "notes": ""},
        {"duct_no": "VX-3", "fed_from_duct": "VX-2", "notes": ""},
        {"duct_no": "VX-4", "fed_from_duct": "VX-2", "notes": ""},
    ],
    "row_count": 4,
}
out2d = run("What is upstream of VX-3?",
            "SELECT fed_from_duct FROM \"bld_ducts\" WHERE duct_no = 'VX-3'",
            tables=(DUCTS,), cards=())
check("RED: no rolls_up_to, no card: the feed column walks to the topmost row, then ROOT",
      hierarchy_lines(out2d) == ["HIERARCHY - VX-3 is fed from VX-2 <- VX-1 <- ROOT"],
      hierarchy_lines(out2d) or out2d[-400:])
out2e = run("Which ducts are downstream of VX-1?",
            "SELECT duct_no FROM \"bld_ducts\" WHERE fed_from_duct = 'VX-1'",
            tables=(DUCTS,), cards=())
check("RED: and downward the same way",
      hierarchy_lines(out2e) == ["HIERARCHY - everything fed from VX-1 (3 rows, 2 tiers): "
                                 "depth 1: VX-2; depth 2: VX-3, VX-4"],
      hierarchy_lines(out2e) or out2e[-400:])

# ---------------------------------------------------------------------------
print("\n3. No line without tree wording, without a node, or on a word that only starts "
      "like one")
# ---------------------------------------------------------------------------
out3a = run("Which units does QS-1 feed?", ONE_HOP)
check("no tree wording ('which units does X feed?'): no line", hierarchy_lines(out3a) == [],
      hierarchy_lines(out3a))
check("RED: tree_direction says so: None", direction("Which units does QS-1 feed?") is None,
      direction("Which units does QS-1 feed?"))
q3b = "If the power fails, how long does the backup battery last?"
out3b = run(q3b, "SELECT unit_ref FROM \"bld_feedtree\" WHERE kind = 'head'")
check("RED: tree wording with no node: the wording matches ...", direction(q3b) == "down",
      direction(q3b))
check("... and still no line", hierarchy_lines(out3b) == [], hierarchy_lines(out3b))
q3c = "If the Tripp Volt unit beside QS-1 is replaced, what changes?"
out3c = run(q3c, ONE_HOP)
check("RED: a word that merely starts with 'trip' is not tree wording", direction(q3c) is None,
      direction(q3c))
check("... so a question naming a node and such a word gets no line", hierarchy_lines(out3c) == [],
      hierarchy_lines(out3c))

WORDING = [
    ("If QS-1 trips, what goes off?", "down"),
    ("If QS-1 were to trip, what goes off?", "down"),
    ("If QS-1 fails, what goes off?", "down"),
    ("If QS-1 is isolated, what goes off?", "down"),
    ("If QS-1 is switched off, what goes off?", "down"),
    ("Which units lose power with QS-1?", "down"),
    ("Which unit loses supply with QS-1?", "down"),
    ("Is anything losing power when QS-1 is opened?", "down"),
    ("What is downstream of QS-1?", "down"),
    ("Which units does QS-1 serve directly or indirectly?", "down"),
    ("What feeds QD-12, all the way back?", "up"),
    ("Follow QD-12 all the way up.", "up"),
    ("Trace QD-12 back to the main intake.", "up"),
    ("Trace QD-12 back to the source.", "up"),
    ("Show the full supply path of QD-12.", "up"),
    ("Show the whole supply chain of QD-12.", "up"),
    ("What is upstream of QD-12?", "up"),
    ("How many isolation valves does QS-1 have?", None),
    ("If the isolation valve on QS-1 is closed, what changes?", None),
    ("What trip setting does QS-1 have?", None),
    ("Which Tripp Volt model sits beside QS-1?", None),
    ("What does QS-1 feed, and what feeds QS-1?", None),
    ("If it rains. The unit trips sometimes.", None),
]
for text, want in WORDING:
    check(f"RED: tree_direction({text!r}) == {want!r}", direction(text) == want, direction(text))
check("RED: both directions worded - the earliest wording wins (down first)",
      direction("If QS-1 trips, what loses power, and what is upstream of it?") == "down",
      direction("If QS-1 trips, what loses power, and what is upstream of it?"))
check("RED: both directions worded - the earliest wording wins (up first)",
      direction("What is upstream of QS-1, and if it trips what loses power?") == "up",
      direction("What is upstream of QS-1, and if it trips what loses power?"))

LINE3 = ("HIERARCHY - everything fed from QS-1-AUX (2 rows, 1 tier): depth 1: QD-12 (First Deck), "
         "QD-13 (Second Deck)")
out3d = run("If QS-1-AUX trips, which units lose power?",
            "SELECT unit_ref FROM \"bld_feedtree\" WHERE fed_from = 'QS-1-AUX'")
check("RED: the longest code printed whole wins - QS-1-AUX, never the QS-1 inside it",
      hierarchy_lines(out3d) == [LINE3], hierarchy_lines(out3d) or out3d[-400:])
out3e = run("if qs-1-aux trips, which units lose power?",
            "SELECT unit_ref FROM \"bld_feedtree\" WHERE fed_from = 'QS-1-AUX'")
check("RED: the code is matched whatever its case, and named as the table prints it",
      hierarchy_lines(out3e) == [LINE3], hierarchy_lines(out3e) or out3e[-400:])
out3f = run("If QS-1x trips, which units lose power?", ONE_HOP)
check("a code run on into other code characters is not that code: no line",
      hierarchy_lines(out3f) == [], hierarchy_lines(out3f))
out3j = run("If QS-1-SPARE trips, which units lose power?", ONE_HOP)
check("a longer code the table does not hold never resolves to the shorter code inside it "
      "(a hyphen joins a code, it does not end one): no line",
      hierarchy_lines(out3j) == [], hierarchy_lines(out3j))
out3i = run("If QS-2 (NEW) trips, which units lose power?",
            "SELECT unit_ref FROM \"bld_feedtree\" WHERE fed_from = 'QS-2 (NEW)'")
check("RED: two codes printed whole, one inside the other: the longer one is the node",
      hierarchy_lines(out3i) == ["HIERARCHY - everything fed from QS-2 (NEW) (1 row, 1 tier): "
                                 "depth 1: QD-22 (Second Deck)"],
      hierarchy_lines(out3i) or out3i[-400:])

ZONES = {
    "table_name": "bld_zones",
    "columns": ["zone", "fed_from", "notes"],
    "rows": [{"zone": "Zenith", "fed_from": "", "notes": ""},
             {"zone": "Nadir", "fed_from": "Zenith", "notes": ""},
             {"zone": "Meridian", "fed_from": "Zenith", "notes": ""}],
    "row_count": 3,
}
out3g = run("If the Zenith loses power, what else goes off?",
            "SELECT zone FROM \"bld_zones\" WHERE fed_from = 'Zenith'", tables=(ZONES,), cards=())
check("an identifier that is a plain word, not a code, never makes a node: no line",
      hierarchy_lines(out3g) == [], hierarchy_lines(out3g))
INTAKES = {
    "table_name": "bld_intakes",
    "columns": ["intake_ref", "fed_from"],
    "rows": [{"intake_ref": "IN-7", "fed_from": "UTILITY LINE SEVEN"},
             {"intake_ref": "IN-8", "fed_from": "UTILITY LINE EIGHT"}],
    "row_count": 2,
}
out3h = run("What feeds IN-7, all the way back?",
            "SELECT fed_from FROM \"bld_intakes\" WHERE intake_ref = 'IN-7'",
            tables=(INTAKES,), cards=())
check("a table none of whose parents is one of its own rows holds no tree: no line",
      hierarchy_lines(out3h) == [], hierarchy_lines(out3h))

# ---------------------------------------------------------------------------
print("\n4. The depth and row caps hold, a loop terminates, an error drops only the line")
# ---------------------------------------------------------------------------
CHAIN = {
    "table_name": "bld_chain",
    "columns": ["link_ref", "fed_from"],
    "rows": [{"link_ref": f"CH-{i:02d}", "fed_from": (f"CH-{i - 1:02d}" if i else "")}
             for i in range(13)],
    "row_count": 13,
}
out4a = run("What is downstream of CH-00?", "SELECT link_ref FROM \"bld_chain\" WHERE fed_from = 'CH-00'",
            tables=(CHAIN,), cards=())
want4a = ("HIERARCHY - everything fed from CH-00 (10 rows, 10 tiers): "
          + "; ".join(f"depth {i}: CH-{i:02d}" for i in range(1, 11))
          + " … and more below depth 10")
check("RED: a twelve-deep chain is walked 10 tiers down, and says more lie below",
      hierarchy_lines(out4a) == [want4a], hierarchy_lines(out4a) or out4a[-600:])
out4b = run("What is upstream of CH-12?", "SELECT fed_from FROM \"bld_chain\" WHERE link_ref = 'CH-12'",
            tables=(CHAIN,), cards=())
want4b = ("HIERARCHY - CH-12 is fed from "
          + " <- ".join(f"CH-{i:02d}" for i in range(11, 1, -1)) + " <- …")
check("RED: and 10 hops up, ending in an ellipsis", hierarchy_lines(out4b) == [want4b],
      hierarchy_lines(out4b) or out4b[-600:])

FAN = {
    "table_name": "bld_fan",
    "columns": ["port_ref", "fed_from"],
    "rows": [{"port_ref": "HUB-0", "fed_from": ""}]
    + [{"port_ref": f"FN-{i:02d}", "fed_from": "HUB-0"} for i in range(1, 66)],
    "row_count": 66,
}
out4c = run("What lies downstream of HUB-0?", "SELECT COUNT(*) FROM \"bld_fan\" WHERE fed_from = 'HUB-0'",
            tables=(FAN,), cards=())
want4c = ("HIERARCHY - everything fed from HUB-0 (65 rows, 1 tier): depth 1: "
          + ", ".join(f"FN-{i:02d}" for i in range(1, 61)) + " … and 5 more")
check("RED: 65 rows below: 60 listed, the count whole, and '… and 5 more'",
      hierarchy_lines(out4c) == [want4c], hierarchy_lines(out4c) or out4c[-400:])

LOOP = {
    "table_name": "bld_loop",
    "columns": ["ring_ref", "fed_from"],
    "rows": [{"ring_ref": "RG-1", "fed_from": "RG-2"},
             {"ring_ref": "RG-2", "fed_from": "RG-1"},
             {"ring_ref": "RG-3", "fed_from": "RG-1"}],
    "row_count": 3,
}
out4d = run("If RG-1 trips, what loses power?", "SELECT ring_ref FROM \"bld_loop\" WHERE fed_from = 'RG-1'",
            tables=(LOOP,), cards=())
check("RED: a loop downward terminates, each node once",
      hierarchy_lines(out4d) == ["HIERARCHY - everything fed from RG-1 (2 rows, 1 tier): "
                                 "depth 1: RG-2, RG-3"], hierarchy_lines(out4d) or out4d[-400:])
out4e = run("What is upstream of RG-3?", "SELECT fed_from FROM \"bld_loop\" WHERE ring_ref = 'RG-3'",
            tables=(LOOP,), cards=())
check("RED: a loop upward terminates and says it looped",
      hierarchy_lines(out4e) == ["HIERARCHY - RG-3 is fed from RG-1 <- RG-2 <- RG-1 (loop)"],
      hierarchy_lines(out4e) or out4e[-400:])

# An error anywhere inside the helper drops the line and leaves the result byte for byte as it
# is when the helper never fires.
saved_dir = getattr(sql_tool, "tree_direction", None)
sql_tool.tree_direction = lambda q: None
try:
    base1 = run(Q1, ONE_HOP)
finally:
    if saved_dir is None:
        del sql_tool.tree_direction
    else:
        sql_tool.tree_direction = saved_dir
check("(the baseline without the helper carries no line)", hierarchy_lines(base1) == [], base1[-300:])

saved_line = getattr(sql_tool, "_hierarchy_line", None)


def _boom(*a, **k):
    raise RuntimeError("walk failed")


sql_tool._hierarchy_line = _boom
try:
    err1 = run(Q1, ONE_HOP)
finally:
    if saved_line is None:
        del sql_tool._hierarchy_line
    else:
        sql_tool._hierarchy_line = saved_line
check("RED: the helper raising leaves the result byte-identical to the no-line result",
      saved_line is not None and err1 == base1, err1[-400:])

real_exec = sql_tool._execute_with_timeout
walks = []


def _fail_walks(con, query, timeout=sql_tool.SQL_QUERY_TIMEOUT):
    if "WITH RECURSIVE" in query.upper() and query != ONE_HOP:
        walks.append(query)
        raise RuntimeError("recursive walk failed")
    return real_exec(con, query, timeout)


sql_tool._execute_with_timeout = _fail_walks
try:
    err2 = run(Q1, ONE_HOP)
finally:
    sql_tool._execute_with_timeout = real_exec
check("RED: the walk query really ran (and was made to fail)", len(walks) >= 1, walks)
check("a failing walk query also leaves the result byte-identical", err2 == base1, err2[-400:])

# ---------------------------------------------------------------------------
print("\n5. The same line on every step of an investigation; never a re-query (T10-R2)")
# ---------------------------------------------------------------------------
suffix = (f"{sql_tool._STEP_SUFFIX_MARKER}2: Re-query. {sql_loop.VALUES_LEAD} unit_ref: "
          f"'QS-1-AUX', 'QD-12'{sql_loop.PREVIOUS_SQL_MARKER} `SELECT unit_ref FROM "
          f"\"bld_feedtree\" WHERE unit_ref = 'QS-1-AUX'`)")
out5 = run(Q1 + suffix, ONE_HOP)
check("RED: a later step's result carries the same line - the loop's own instruction (which "
      "names a longer code) is never read for the node", hierarchy_lines(out5) == [LINE1],
      hierarchy_lines(out5) or out5[-400:])
issues_with = [i.kind for i in sql_loop.inspect_result(out1, Q1, CARDS)]
issues_without = [i.kind for i in sql_loop.inspect_result(base1, Q1, CARDS)]
check("the line changes nothing the loop reads off the result: the same issues, so never a "
      "re-query", issues_with == issues_without, (issues_with, issues_without))

# ---------------------------------------------------------------------------
print("\n6. The answer rules carry the HIERARCHY line rule; the code names no domain noun")
# ---------------------------------------------------------------------------
rules = openai_client.OUTPUT_FORMAT_RULES
bullet = next((ln for ln in rules.splitlines() if "HIERARCHY" in ln), "")
check("RED: OUTPUT_FORMAT_RULES has a HIERARCHY bullet", bool(bullet), rules[-400:])
check("RED: it says the line is computed in code from the recorded parent references",
      "computed in code" in bullet and "recorded parent references" in bullet, bullet)
check("RED: a question about what lies below or above is answered from it in full",
      "below or above" in bullet and "in full" in bullet and "every node it lists" in bullet,
      bullet)
check("RED: the line itself is never printed", "never print" in bullet.lower(), bullet)

helpers = [getattr(sql_tool, n, None) for n in (
    "tree_direction", "_is_parent_column", "_is_feed_column", "_looks_like_code",
    "_printed_code", "_tree_shape", "_hierarchy_target", "_places_of", "_walk_sources",
    "_walk_down_line", "_walk_up_line", "_hierarchy_line")]
source = "".join(inspect.getsource(h) for h in helpers if h is not None)
check("RED: the new helpers exist", all(h is not None for h in helpers), helpers)
check("T10-R3: the walk's code never names a domain noun ('panel', 'board')",
      bool(source) and "panel" not in source.lower() and "board" not in source.lower(),
      [w for w in ("panel", "board") if w in source.lower()])

# ---------------------------------------------------------------------------
print("\n7. Review fix round 1: depth counted in tiers (T10-R4); only FEED references are "
      "walked, never containment (T10-R5)")
# ---------------------------------------------------------------------------
# T10-R4. The down line counted the tree's depth in "levels", right beside the floor-level
# names in brackets - an answer writer could read "2 levels" as two floors.
downs = [ln for o in (out1, out1b, out2e, out3d, out3e, out3i, out4a, out4c, out4d)
         for ln in hierarchy_lines(o) if "everything fed from" in ln]
check("(every down line of sections 1-4 was produced)", len(downs) == 9, downs)
check("RED: every down line counts its depth in tiers",
      bool(downs) and all(re.search(r"^HIERARCHY - everything fed from .+? \(\d+ rows?, \d+ tiers?\): "
                                    r"depth 1: ", ln) for ln in downs), downs)
check("RED: and never in 'levels', the word the floor names in brackets already mean",
      bool(downs) and not any(re.search(r"\d+ levels?\)", ln) for ln in downs), downs)

# T10-R5. The walk followed ANY parent-reference column, a containment tree's included, so a
# supply question naming a level listed every room INSIDE it as losing power. Sitting inside
# something is not being supplied by it: only a FEED reference - the fed_from family, with
# rolls_up_to as its override - is walked; a containment reference (parent / parent_id) never.


def is_feed(column):
    fn = getattr(sql_tool, "_is_feed_column", None)
    return "<no _is_feed_column>" if fn is None else fn(column)


for column, want in (("fed_from", True), ("FED_FROM", True), ("fed_from_duct", True),
                     ("parent", False), ("parent_id", False), ("rolls_up_to", False),
                     ("feeder", False), ("feeds_to", False)):
    check(f"RED: _is_feed_column({column!r}) is {want}", is_feed(column) is want, is_feed(column))
check("the double-counting note's own test is unchanged: parent_id is still a parent reference",
      sql_tool._is_parent_column("parent_id") and sql_tool._is_parent_column("parent")
      and sql_tool._is_parent_column("fed_from"))

ROOMS = dict(PLACES, rows=PLACES["rows"] + [
    {"place_ref": "ZR-101", "kind": "room", "parent_id": "ZL-1", "level_name": "First Deck"},
    {"place_ref": "ZR-102", "kind": "room", "parent_id": "ZL-1", "level_name": "First Deck"},
], row_count=5)
out7a = run("If ZL-1 loses power, which rooms lose power too?",
            "SELECT place_ref FROM \"bld_places\" WHERE parent_id = 'ZL-1'", tables=(TREE, ROOMS))
check("RED: a supply question naming a place code, over a places table whose parent_id is "
      "containment: no HIERARCHY line", hierarchy_lines(out7a) == [], hierarchy_lines(out7a))
check("the query's own rows are still there, and the double-counting note still names "
      "parent_id", "| ZR-101 |" in out7a and "parent-reference column: parent_id" in out7a,
      out7a[-500:])
out7b = run("What is upstream of ZR-101?",
            "SELECT parent_id FROM \"bld_places\" WHERE place_ref = 'ZR-101'", tables=(TREE, ROOMS))
check("RED: and upward from a room: no line", hierarchy_lines(out7b) == [], hierarchy_lines(out7b))
out7c = run(Q1, ONE_HOP, tables=(TREE, ROOMS))
check("the feed tree loaded beside it still fires, unchanged", hierarchy_lines(out7c) == [LINE1],
      hierarchy_lines(out7c) or out7c[-400:])

# One table recording BOTH: what each unit is housed in (parent_id, listed first) and what
# feeds it (fed_from). Only the feed is walked.
OUTLETS = {
    "table_name": "bld_outlets",
    "columns": ["outlet_ref", "parent_id", "fed_from", "notes"],
    "rows": [
        {"outlet_ref": "EN-1", "parent_id": "", "fed_from": "", "notes": ""},
        {"outlet_ref": "PX-1", "parent_id": "EN-1", "fed_from": "", "notes": ""},
        {"outlet_ref": "PX-2", "parent_id": "EN-1", "fed_from": "PX-1", "notes": ""},
        {"outlet_ref": "PX-3", "parent_id": "EN-1", "fed_from": "PX-2", "notes": ""},
    ],
    "row_count": 4,
}
out7d = run("If PX-1 trips, what loses power?",
            "SELECT outlet_ref FROM \"bld_outlets\" WHERE fed_from = 'PX-1'", tables=(OUTLETS,),
            cards=())
check("RED: a table with a containment column AND a feed column walks the feed",
      hierarchy_lines(out7d) == ["HIERARCHY - everything fed from PX-1 (2 rows, 2 tiers): "
                                 "depth 1: PX-2; depth 2: PX-3"], hierarchy_lines(out7d) or out7d[-400:])
out7e = run("If EN-1 trips, what loses power?",
            "SELECT outlet_ref FROM \"bld_outlets\" WHERE parent_id = 'EN-1'", tables=(OUTLETS,),
            cards=())
check("RED: what only HOUSES the units feeds none of them: no line",
      hierarchy_lines(out7e) == [], hierarchy_lines(out7e))
out7f = run("What is upstream of PX-3?",
            "SELECT fed_from FROM \"bld_outlets\" WHERE outlet_ref = 'PX-3'", tables=(OUTLETS,),
            cards=())
check("RED: and upward the chain is the feed chain, never the housing",
      hierarchy_lines(out7f) == ["HIERARCHY - PX-3 is fed from PX-2 <- PX-1 <- ROOT"],
      hierarchy_lines(out7f) or out7f[-400:])

# ---------------------------------------------------------------------------
print("\n8. The answer rule says what a node's bracket is (wave 3, G4 c)")
# ---------------------------------------------------------------------------
# Measured on the goal-function run of 2026-10-01: the line printed a unit's level in brackets -
# read off its location key - while the table above it printed a different location, a sheet-
# template leftover; no answer rule said what the bracket meant, and both answers stated the
# printed cell. The bullet now says: the bracket is the level the node's location key records;
# state it, and name a different printed location as printed. Answer prompts only (ruling W7).
bullet8 = next((ln for ln in openai_client.OUTPUT_FORMAT_RULES.splitlines() if "HIERARCHY" in ln), "")
check("RED: the HIERARCHY bullet says the bracket after a node is the level its location key "
      "records", "brackets after a node" in bullet8 and "location key records" in bullet8, bullet8)
check("RED: when the question asks where that thing is, that level is stated",
      "where that thing is" in bullet8 and "state that level" in bullet8, bullet8)
check("RED: a different location printed for it is named as printed, beside it",
      "different location printed for it" in bullet8 and "as printed" in bullet8, bullet8)
check("the earlier sentences are kept (computed in code, in full, never printed)",
      "computed in code" in bullet8 and "every node it lists" in bullet8
      and "never print" in bullet8.lower(), bullet8)
check("the frozen tool-choice rules carry none of it (ruling W7)",
      "location key records" not in openai_client.TOOL_CHOICE_FORMAT_RULES)
check("(the line it describes really prints the bracket from the location key)",
      "QD-11 (First Deck)" in (hierarchy_lines(out1) or [""])[0], hierarchy_lines(out1))

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
