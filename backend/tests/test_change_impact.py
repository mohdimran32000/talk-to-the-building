"""test_change_impact.py — the change-impact path (added 2026-09-16).

THE DEFECT THIS EXISTS FOR
Ask the app "can I replace camera <tag>?" and it answers with a generic
replacement checklist — check the mounting, check the PoE budget, check the
lens — written from general knowledge, because nothing in the app ever tells it
that the corpus holds the building's own dependency graph. The data side has
carried one for two weeks: ONE table of evidence-backed links for every system
(subject id / predicate / object id, typed by kind), plus a location key on
every stamped table and a room-by-room asset fold. The answer to "what would
this affect" is sitting in a table the router never selected, described by a
prompt that never mentioned it.

WHAT THE FIX IS, in three places, none of them system-specific:
  * `table_router` — a CHANGE GUARD. When the question asks about changing
    something, every card whose columns are a graph edge (`subject_id`,
    `predicate`, `object_id`) is appended, exactly like a declared join
    neighbour. It is a guard and not a scoring tier because that was measured:
    as a tier it could not reach k=3 for a question naming an asset by its tag
    (three of that system's own tables score 6 apiece) and it cost one eval
    question the table holding its answer. See the module docstring.
  * `openai_client` — CHANGE_IMPACT_RULES (how to work the question: identify
    the asset, walk the graph both ways, list what shares its place, pull the
    specs) and CHANGE_IMPACT_ANSWER_SHAPE (the four fixed sections and the "not
    on record" floor). The shape is injected at every site that injects
    OUTPUT_FORMAT_RULES, because the app writes its answer in a SECOND call
    that has no tools and never sees the tool-loop prompt.
  * `sql_tool` — DEPENDENCY_GRAPH_RULE, one domain rule in the voice of the ~20
    already there: polymorphic typed ids, both directions in one query, take the
    evidence columns, no negative proved.

FIXTURES, NEVER THE REAL CARDS
`fixtures/change_impact_cards.json` (14 cards) and
`fixtures/change_impact_graph.json` (14 edges, 5 room-asset rows) describe a
building that does not exist, with different table names from the real corpus —
which is itself part of the test: the router and both prompts must work off the
COLUMN SHAPE, never off a table name. A separate file rather than additions to
`router_place_cards.json` on purpose: adding cards to that file changes its
document-frequency denominators and would silently move the numbers
`test_table_router_place.py` measured.

Run:
    venv/Scripts/python -X utf8 tests/test_change_impact.py
"""
import importlib
import inspect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb  # noqa: E402

from app.services import table_router  # noqa: E402
from app.services.table_router import select_tables  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


FIX = Path(__file__).resolve().parent / "fixtures"
CARDS = json.loads((FIX / "change_impact_cards.json").read_text(encoding="utf-8"))
GRAPH = json.loads((FIX / "change_impact_graph.json").read_text(encoding="utf-8"))

GRAPH_TABLE = "bld_relationships"

# The three shapes the owner's requirement names, one per system, plus the
# controls. None of them is wired to a table name anywhere in the code.
Q_CAMERA = "Can I replace camera CAM-4F-B-01?"
Q_FCU = "Can I replace the FCU in room 4.04?"
Q_BOARD = "Can I add a load to board DB-01?"
Q_PLAIN = "What is the total connected load of the building?"
Q_PLAIN2 = "What is the breaker rating of the feeder from SMDB-1 to DB-01?"
Q_INSTALLED = "How many cameras are installed in total?"


# ---------------------------------------------------------------------------
print("1. A change-impact question reaches the graph AND the asset's own table")
# ---------------------------------------------------------------------------
r_cam = select_tables(Q_CAMERA, CARDS, k=3)
check("(a) replace-a-camera selects the dependency graph", GRAPH_TABLE in r_cam, r_cam)
check("(a) replace-a-camera still selects the camera register itself",
      "bld_cam_register" in r_cam, r_cam)

r_fcu = select_tables(Q_FCU, CARDS, k=3)
check("(b) replace-an-FCU selects the dependency graph", GRAPH_TABLE in r_fcu, r_fcu)
check("(b) replace-an-FCU selects the FCU table the graph joins to",
      "bld_fcu_commissioning" in r_fcu, r_fcu)
check("(b) replace-an-FCU still selects the room-placed registers",
      "bld_equipment" in r_fcu and "bld_room_assets" in r_fcu, r_fcu)

r_board = select_tables(Q_BOARD, CARDS, k=3)
check("(c) add-a-load-to-a-board selects the dependency graph",
      GRAPH_TABLE in r_board, r_board)
check("(c) add-a-load-to-a-board still selects the panel schedule",
      "bld_panels" in r_board, r_board)
check("(c) ls-014's neighbour rule is untouched: the feeder schedule still travels "
      "with the panel schedule", "bld_smdb_feeders" in r_board, r_board)

# The asset table arrives because the GRAPH declares a join to it — that is the
# mechanism, and it is what makes this generic. Prove it rather than assume it.
graph_card = next(c for c in CARDS if c["table"] == GRAPH_TABLE)
check("the graph card's declared joins are what reach the asset tables",
      {"bld_cam_register", "bld_fcu_commissioning", "bld_panels"}
      <= set(graph_card["joins_to"]), graph_card["joins_to"])


# ---------------------------------------------------------------------------
print("\n2. A plain factual question is unchanged")
# ---------------------------------------------------------------------------
r_plain = select_tables(Q_PLAIN, CARDS, k=3)
check("(d) a plain total question does NOT pull in the graph",
      GRAPH_TABLE not in r_plain, r_plain)
r_plain2 = select_tables(Q_PLAIN2, CARDS, k=3)
check("(d) a plain feeder-rating question does NOT pull in the graph",
      GRAPH_TABLE not in r_plain2, r_plain2)
check("(d) and it still answers from the feeder schedule",
      "bld_smdb_feeders" in r_plain2, r_plain2)
check("'installed' is not a change verb — a plain count question is untouched",
      GRAPH_TABLE not in select_tables(Q_INSTALLED, CARDS, k=3),
      select_tables(Q_INSTALLED, CARDS, k=3))

# The guard is APPEND-ONLY. This is the property that made the offline replay
# come back 0 lost, so it is asserted directly and not inferred from the replay.
for q in (Q_CAMERA, Q_FCU, Q_BOARD):
    before = [t for t in select_tables(q, CARDS, k=3) if t != GRAPH_TABLE]
    saved = table_router._CHANGE_VERB_RES, table_router._IMPACT_WORD_RES
    table_router._CHANGE_VERB_RES, table_router._IMPACT_WORD_RES = [], []
    try:
        unguarded = select_tables(q, CARDS, k=3)
    finally:
        table_router._CHANGE_VERB_RES, table_router._IMPACT_WORD_RES = saved
    check(f"the guard only ADDS — nothing is lost from: {q[:34]}...",
          all(t in before for t in unguarded), (unguarded, before))

check("determinism: identical question -> identical result",
      select_tables(Q_CAMERA, CARDS, k=3) == select_tables(Q_CAMERA, CARDS, k=3))
check("empty card list still returns empty", select_tables(Q_CAMERA, [], k=3) == [])


# ---------------------------------------------------------------------------
print("\n3. What counts as a graph, and what counts as a change")
# ---------------------------------------------------------------------------
log_card = next(c for c in CARDS if c["table"] == "bld_maintenance_log")
check("a table with only an object_id is NOT a graph (it names one end)",
      not table_router._card_is_graph(log_card))
check("a table with subject_id + predicate + object_id IS a graph",
      table_router._card_is_graph(graph_card))
check("the maintenance log is never appended by the guard",
      "bld_maintenance_log" not in r_cam, r_cam)

for q in ["can I swap the UPS in room 1.28?", "what would be affected if I remove DR-0210-D5?",
          "is CAM-4F-B-01 compatible with the existing cabling?",
          "what does the server room depend on?",
          "I want to relocate board DB-01 to level 2"]:
    check(f"fires: {q[:46]}", table_router._asks_about_a_change(q))
for q in [Q_PLAIN, Q_PLAIN2, Q_INSTALLED, "what is the address of the supplier?",
          "how many sprinkler heads are installed?",
          "which company supplied and installed the 30 litre water heaters?",
          "how often should the anode be replaced?"]:
    check(f"silent: {q[:46]}", not table_router._asks_about_a_change(q))

# 'how often should the anode be replaced?' is the line this condition draws:
# a change verb with nothing named is a maintenance question, not an impact one.
check("a change verb ALONE does not fire the guard",
      not table_router._asks_about_a_change("should this be replaced?"))
check("the same verb WITH a named place does",
      table_router._asks_about_a_change("should the unit in room 4.04 be replaced?"))
check("the same verb WITH a coded tag does",
      table_router._asks_about_a_change("should CAM-4F-B-01 be replaced?"))


# ---------------------------------------------------------------------------
print("\n4. The prompts carry the rule — and carry it where the answer is written")
# ---------------------------------------------------------------------------
from app.services import openai_client as oc  # noqa: E402
from app.services import sql_tool  # noqa: E402

SECTIONS = ["What it is now", "What a replacement must match",
            "What it is connected to", "What the records do not say"]


def prompt_has_shape(text):
    return all(s in text for s in SECTIONS) and "not on record" in text


tool_prompt = oc._build_system_prompt(True, True, False,
                                      [{"table_name": "bld_panels", "columns": ["panel"]}])
check("(e) the tool-loop prompt states all four answer sections, in order",
      prompt_has_shape(tool_prompt))
check("(e) the tool-loop prompt forbids filling a gap from general knowledge",
      "NEVER fill a gap" in tool_prompt and "not on record" in tool_prompt)
check("(e) the sections are in the required order",
      [tool_prompt.index(s) for s in SECTIONS]
      == sorted(tool_prompt.index(s) for s in SECTIONS))
check("(e) the prompt tells it to walk the graph in BOTH directions",
      "BOTH WAYS" in tool_prompt and "2 hops" in tool_prompt)
check("(e) the prompt tells it to say how each link is known",
      "how the link is known" in tool_prompt)
check("(e) the prompt distinguishes no recorded link from no link",
      "no link on record" in tool_prompt)
check("every pre-existing output rule survives",
      "OUTPUT FORMAT RULES (strict)" in tool_prompt
      and "THE ATTRIBUTE AND THE OWNER MUST BOTH MATCH" in tool_prompt
      and "READ THE COLUMN NAME BEFORE YOU ATTRIBUTE A VALUE" in tool_prompt)
check("no tool-selection rule was dropped",
      "TOOL SELECTION RULES (follow strictly)" in tool_prompt
      and "Only call ONE tool per turn." in tool_prompt)
check("a no-tools session is untouched",
      oc._build_system_prompt(False, False, False) == oc.SYSTEM_PROMPT_NO_DOCS)

# THE ROUTING TEST, not the detection test. The app writes its final answer in a
# SECOND model call whose system prompt is built at the tool-result sites inside
# stream_response — a rule that only reaches `_build_system_prompt` never
# reaches the model that writes the answer (doc-prep/CLAUDE.md §12).
stream_src = inspect.getsource(oc.stream_response)
n_fmt = stream_src.count("{OUTPUT_FORMAT_RULES}")
n_shape = stream_src.count("{CHANGE_IMPACT_ANSWER_SHAPE}")
check("(e) the answer shape reaches EVERY final-answer prompt, not just the tool "
      f"loop ({n_shape} of {n_fmt} sites)", n_fmt > 0 and n_shape == n_fmt)

check("the SQL rule describes the typed polymorphic ids",
      "POLYMORPHIC" in sql_tool.DEPENDENCY_GRAPH_RULE
      and "subject_type/object_type" in sql_tool.DEPENDENCY_GRAPH_RULE)
check("the SQL rule names the predicates as a family, not one system",
      "ONE graph for every system" in sql_tool.DEPENDENCY_GRAPH_RULE)
check("the SQL rule demands both directions in one query",
      "BOTH DIRECTIONS" in sql_tool.DEPENDENCY_GRAPH_RULE
      and "subject_id = '<id>' OR object_id = '<id>'" in sql_tool.DEPENDENCY_GRAPH_RULE)
check("the SQL rule demands the evidence columns",
      "evidence" in sql_tool.DEPENDENCY_GRAPH_RULE)
check("the SQL rule states the graph proves no negative",
      "no negative" in sql_tool.DEPENDENCY_GRAPH_RULE)
sql_src = inspect.getsource(sql_tool.execute_sql_query)
check("the SQL rule is interpolated into the prompt that is actually sent",
      "{DEPENDENCY_GRAPH_RULE}" in sql_src)
check("the existing ~20 domain rules are all still in that prompt",
      all(frag in sql_src for frag in [
          "NEVER infer a panel's block or floor",
          "NEVER drop a constraint from the question",
          "SUM that column instead of COUNT(*)",
          "rolls_up_to",
          "the feeder-schedule fallback join",
          "NEVER approximate it from load columns",
      ]))

# Generic by construction: no rule may name a table of any one building.
for name, text in [("openai_client shape", oc.CHANGE_IMPACT_ANSWER_SHAPE),
                   ("openai_client rules", oc.CHANGE_IMPACT_RULES),
                   ("sql_tool rule", sql_tool.DEPENDENCY_GRAPH_RULE)]:
    check(f"{name} names no project's table", "hwu_" not in text and "bld_" not in text)
check("the router matches no table name either",
      "hwu_relationships" not in inspect.getsource(table_router._card_is_graph)
      and "relationships" not in repr(table_router._GRAPH_EDGE_COLUMNS))


# ---------------------------------------------------------------------------
print("\n5. The SQL pattern the rule ships actually walks the graph")
# ---------------------------------------------------------------------------
# The rule tells the model to write a specific query. If that query does not
# work, the rule is worse than no rule. Run it — on the fixture graph, in the
# same engine the app uses.
def _load(con, table, rows):
    cols = list(rows[0].keys())
    col_defs = ", ".join(f'"{c}" VARCHAR' for c in cols)
    con.execute(f'CREATE TABLE "{table}" ({col_defs})')
    con.executemany(f'INSERT INTO "{table}" VALUES ({", ".join("?" * len(cols))})',
                    [[r[c] for c in cols] for r in rows])


con = duckdb.connect(":memory:")
_load(con, GRAPH_TABLE, GRAPH["relationships"])
_load(con, "bld_room_assets", GRAPH["room_assets"])

ASSET = "CAM-4F-B-01"
hop1 = con.execute(
    f'SELECT subject_id, predicate, object_id, derived_by FROM "{GRAPH_TABLE}" '
    f"WHERE subject_id = '{ASSET}' OR object_id = '{ASSET}'").fetchall()
upstream = {r[0] for r in hop1 if r[2] == ASSET}
downstream = {r[2] for r in hop1 if r[0] == ASSET}
check("one query returns BOTH directions", len(hop1) == 5, hop1)
check("upstream: what feeds the asset", upstream == {"DB-01/C3"}, upstream)
check("downstream: what the asset is wired to, recorded by, backed by and in",
      downstream == {"RM-1.28", "RM-4.04"}, downstream)
check("the evidence class travels with every link",
      all(r[3] in {"printed", "one-to-one", "owner-confirmed"} for r in hop1), hop1)
check("one direction alone loses half the answer",
      len(con.execute(f'SELECT 1 FROM "{GRAPH_TABLE}" '
                      f"WHERE subject_id = '{ASSET}'").fetchall()) == 4)

hop2 = con.execute(
    f'SELECT DISTINCT subject_id FROM "{GRAPH_TABLE}" WHERE object_id IN '
    f'(SELECT subject_id FROM "{GRAPH_TABLE}" WHERE object_id = \'{ASSET}\')').fetchall()
check("a second hop reaches the board behind the circuit",
      {r[0] for r in hop2} == {"DB-01"}, hop2)
check("an unrelated board is never reached",
      "DB-02" not in {r[0] for r in hop2} | upstream | downstream)

shared = con.execute(
    'SELECT system, item, qty FROM "bld_room_assets" WHERE location_id = '
    f"(SELECT object_id FROM \"{GRAPH_TABLE}\" WHERE subject_id = '{ASSET}' "
    "AND predicate = 'located_in')").fetchall()
check("what else is in the same room is one join away", len(shared) == 4, shared)
check("and it spans systems, not one", len({s[0] for s in shared}) == 4, shared)
con.close()


# ---------------------------------------------------------------------------
print("\n6. Named mutations — each must turn its own check red")
# ---------------------------------------------------------------------------
# M1 — the rule block is deleted from the answer prompt.
saved_rules = oc.CHANGE_IMPACT_RULES
try:
    oc.CHANGE_IMPACT_RULES = ""
    check("M1 with the rule block removed, the prompt loses the answer shape",
          not prompt_has_shape(oc._build_system_prompt(True, True, False)))
finally:
    oc.CHANGE_IMPACT_RULES = saved_rules
check("M1 control: restored, the shape is back",
      prompt_has_shape(oc._build_system_prompt(True, True, False)))

# M2 — the router signal is removed.
saved = table_router._CHANGE_VERB_RES, table_router._IMPACT_WORD_RES
try:
    table_router._CHANGE_VERB_RES, table_router._IMPACT_WORD_RES = [], []
    check("M2 with the router signal removed, (a) loses the graph table",
          GRAPH_TABLE not in select_tables(Q_CAMERA, CARDS, k=3))
    check("M2 and so do (b) and (c)",
          GRAPH_TABLE not in select_tables(Q_FCU, CARDS, k=3)
          and GRAPH_TABLE not in select_tables(Q_BOARD, CARDS, k=3))
finally:
    table_router._CHANGE_VERB_RES, table_router._IMPACT_WORD_RES = saved
check("M2 control: restored, (a) selects the graph again",
      GRAPH_TABLE in select_tables(Q_CAMERA, CARDS, k=3))

# M3 — the edge shape is loosened to a single id column. A maintenance log that
# merely REFERENCES an asset would then be walked as if it were a graph.
saved_cols = table_router._GRAPH_EDGE_COLUMNS
try:
    table_router._GRAPH_EDGE_COLUMNS = frozenset({"object_id"})
    check("M3 with the edge shape loosened, a non-graph table is treated as one",
          "bld_maintenance_log" in select_tables(Q_CAMERA, CARDS, k=3))
finally:
    table_router._GRAPH_EDGE_COLUMNS = saved_cols
check("M3 control: restored, the log is excluded again",
      "bld_maintenance_log" not in select_tables(Q_CAMERA, CARDS, k=3))

# M4 —'install' back in the verb list. This is the false-positive control: the
# word is how every asset in an O&M corpus is described, and 8 plain factual
# eval questions fired on it in the offline replay.
saved = table_router._CHANGE_VERB_RES
try:
    table_router._CHANGE_VERB_RES = saved + [
        table_router.re.compile(r"\binstall\w*\b", table_router.re.IGNORECASE)]
    check("M4 with 'installed' as a change verb, a plain count question drags in "
          "the graph", GRAPH_TABLE in select_tables(
              "How many cameras are installed on level 4?", CARDS, k=3))
finally:
    table_router._CHANGE_VERB_RES = saved
check("M4 control: restored, it does not",
      GRAPH_TABLE not in select_tables(
          "How many cameras are installed on level 4?", CARDS, k=3))

# M5 — the answer shape is injected in the tool loop but NOT at the final-answer
# sites. This is the mutation that would let the whole rule ship while never
# reaching the model that writes the answer.
mutated = stream_src.replace("{CHANGE_IMPACT_ANSWER_SHAPE}\n", "", 1)
check("M5 with one final-answer site missing the shape, the site count check fails",
      mutated.count("{CHANGE_IMPACT_ANSWER_SHAPE}") != mutated.count("{OUTPUT_FORMAT_RULES}"))

# M6 — the SQL rule is emptied.
saved_rule = sql_tool.DEPENDENCY_GRAPH_RULE
try:
    sql_tool.DEPENDENCY_GRAPH_RULE = ""
    check("M6 with the SQL rule emptied, its content check fails",
          "BOTH DIRECTIONS" not in sql_tool.DEPENDENCY_GRAPH_RULE)
finally:
    sql_tool.DEPENDENCY_GRAPH_RULE = saved_rule
check("M6 control: restored", "BOTH DIRECTIONS" in sql_tool.DEPENDENCY_GRAPH_RULE)

# Everything is back where it started — a mutation that leaks poisons every
# check after it.
importlib.reload(table_router)
check("all mutations restored: (a) still selects the graph",
      GRAPH_TABLE in table_router.select_tables(Q_CAMERA, CARDS, k=3))
check("all mutations restored: (d) still does not",
      GRAPH_TABLE not in table_router.select_tables(Q_PLAIN, CARDS, k=3))

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
