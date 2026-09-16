"""test_table_router_place.py — the place-signal tier (added 2026-09-14).

THE DEFECT THIS EXISTS FOR — measured, not hypothesised
A LangSmith trace on 2026-09-14: the question

    "what are the assets inside classroom 4.04?"

selected `hwu_om_acs_asset_register`, `hwu_om_bms_asset_register` and
`hwu_om_cctv_asset_register` — three O&M *supply lists* that have no room
column at all — because the word "assets" overlaps their subject words. The
two tables whose rows actually carry `location_id = 'RM-4.04'`
(`hwu_equipment`, `hwu_equipment_counts`) were never selected. The generated
SQL found nothing, the app fell back to text search, and answered "8 LCS
devices". The truth for that room is ACS 6 · fire alarm 5 · LCS 4 · central
battery 5.

WHAT THE FIX IS
A fourth scoring tier in `table_router`: when the question NAMES A PLACE,
every card that carries a location column is boosted, and — when the place
named is a room specifically — a card whose own `location_resolution`
vocabulary says its rows reach room granularity is boosted a little further.
See `_PLACE_SIGNAL_RES`, `_PLACE_WEIGHT` and `_ROOM_GRANULARITY_WEIGHT` in
`app/services/table_router.py` for the shapes and the weights.

FIXTURE, NOT THE LIVE 67-CARD FILE (2026-09-15)
This test used to read the real production card set from an absolute local
path (`C:\\RAG Automators\\doc-prep\\eval\\table_cards.json`), which only
existed on one machine and failed for anyone else cloning the repo. It now
reads a small, committed, neutral-named fixture
(`tests/fixtures/router_place_cards.json`, 10 cards) built to reproduce the
same defect shape: three roomless "supply list" tables that only score on
subject-word overlap ("asset register"), two owner-maintained registers with
their own `location_id` (one of them declaring room-granularity resolution),
a doors table, a locations table, a panels table with a real identifier
prefix, a circuits table that resolves only `via_board`, and a relationships
table that is placeable ONLY through a declared join (no `location_id`
column of its own). Every check below that used to read the real cards now
runs against this fixture and is proved, by mutation, still to fail without
the fix.

The full owner-question replay that justified shipping this tier — all 183
eval questions run before vs. after, offline, no network or model call — is
no longer in this repo (it named the real building's tables). It now lives
at `doc-prep/work_spine/router-place-replay-2026-09-15.md`. The counts:
**183 replayed, 29 changed (every one of them naming a place — 0
regressions), 154 unchanged, evidence-table reach 76 -> 79, 0 lost.**

Run:
    venv/Scripts/python -X utf8 tests/test_table_router_place.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import table_router
from app.services.table_router import select_tables

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


CARDS_PATH = Path(__file__).resolve().parent / "fixtures" / "router_place_cards.json"
CARDS = json.loads(CARDS_PATH.read_text(encoding="utf-8"))
BY_NAME = {c["table"]: c for c in CARDS}

Q_ROOM = "what are the assets inside classroom 4.02?"

# ---------------------------------------------------------------------------
# (a) The reported defect: a question naming a room by its CODE must reach
#     the tables that carry that room's rows, not the roomless supply lists.
# ---------------------------------------------------------------------------
print("1. The room-code question reaches the tables that hold that room's rows")
r = select_tables(Q_ROOM, CARDS, k=3)
check("bld_equipment_counts is selected (per-room quantities)",
      "bld_equipment_counts" in r, r)
check("bld_equipment is selected (the per-row register whose location_id "
      "resolves to the room)", "bld_equipment" in r, r)
check("bld_locations travels with them (declared join — the room number "
      "only becomes a location_id through it)", "bld_locations" in r, r)
check("the three roomless O&M supply lists no longer take the ranked slots",
      not any(n in r[:3] for n in
              ("bld_om_a_asset_register", "bld_om_b_asset_register",
               "bld_om_c_asset_register")), r)

# ---------------------------------------------------------------------------
# (b) A question that names NO place must never be affected by the place
#     tier at all: identical selection whether the tier's weights are on or
#     zeroed, and the place detector itself must not fire.
# ---------------------------------------------------------------------------
print("\n2. No place named -> the place tier changes nothing")
NO_PLACE_QUESTIONS = [
    "What is the total connected load of DB-05(B)-SP-01?",
    "How many rows does the asset register have?",
    "Who is the fire fighting service provider?",
]


def _with(**weights):
    saved = {c: getattr(table_router, c) for c in weights}
    try:
        for c, v in weights.items():
            setattr(table_router, c, v)
        return {q: select_tables(q, CARDS, k=3) for q in NO_PLACE_QUESTIONS}
    finally:
        for c, v in saved.items():
            setattr(table_router, c, v)


BEFORE = {q: select_tables(q, CARDS, k=3) for q in NO_PLACE_QUESTIONS}
ZEROED = _with(_PLACE_WEIGHT=0.0, _ROOM_GRANULARITY_WEIGHT=0.0)
for q in NO_PLACE_QUESTIONS:
    check(f"unaffected: {q[:52]}...", BEFORE[q] == ZEROED[q], (BEFORE[q], ZEROED[q]))
check("none of the three no-place questions trips the place detector",
      not any(table_router._names_a_place(q) for q in NO_PLACE_QUESTIONS))

# ---------------------------------------------------------------------------
# (c) A place AND an explicit identifier: the prefix tier (6) must still win,
#     which is why the place tier is deliberately capped below it.
# ---------------------------------------------------------------------------
print("\n3. An explicit board tag still outranks the place signal")
Q_EXPLICIT = "What is the total connected load of DB-05(B)-SP-01 on the 5th floor of Block B?"
r = select_tables(Q_EXPLICIT, CARDS, k=3)
check("the question does name a place", table_router._names_a_place(Q_EXPLICIT))
check("bld_panels (identifier-prefix 'DB') is still ranked first",
      r and r[0] == "bld_panels", r)
check("bld_locations still travels with it (declared join)",
      "bld_locations" in r, r)
check("the place tier's maximum is below the identifier-prefix weight 6",
      table_router._PLACE_WEIGHT + table_router._ROOM_GRANULARITY_WEIGHT < 6,
      (table_router._PLACE_WEIGHT, table_router._ROOM_GRANULARITY_WEIGHT))

# ---------------------------------------------------------------------------
# The place detector itself: every documented shape fires, and shapes that
# only look like one do not. Cards play no part here — these are pure regex
# checks against the shapes the building's own numbering uses.
# ---------------------------------------------------------------------------
print("\n4. The place detector — every documented shape, and its near-misses")
FIRES = [
    ("a dot room number (locations.room_number)", "what is inside 4.02?"),
    ("a ground-floor dot number", "what is in G.21?"),
    ("a legacy number (locations.legacy_number)", "what is in EX-00-055?"),
    ("a location_id", "list the equipment with location_id RM-4.02"),
    ("a design tag", "what is room D01-256?"),
    ("the word 'floor'", "how many units are on the 5th floor?"),
    ("the word 'block'", "what is in Block C?"),
    ("the word 'basement'", "what is in the basement?"),
    ("a short floor code", "how many cameras on 6F?"),
    ("a level code", "what is on L04?"),
]
for label, q in FIRES:
    check(f"fires on {label}", table_router._names_a_place(q), q)

MISSES = [
    ("a decimal reading inside a longer number", "the board totals 47.39 kW"),
    ("a plain warranty question", "what is the warranty on the rack-mount storage server?"),
    ("an equipment model", "what model are the indoor IR dome cameras?"),
]
for label, q in MISSES:
    check(f"silent on {label}", not table_router._names_a_place(q), q)

print("\n4a. A room CODE and a room NAME are not the same signal")
check("'4.02' is a room code", table_router._names_a_room_by_code("assets in 4.02?"))
check("'RM-4.02' is a room code", table_router._names_a_room_by_code("rows for RM-4.02"))
check("'the ICT Hub Room' is a place but NOT a room code — a printed room NAME "
      "lives in ordinary text columns all over this corpus, so it must not "
      "narrow the tier to room-resolving tables",
      table_router._names_a_place("what is in the ICT Hub Room?") and
      not table_router._names_a_room_by_code("what is in the ICT Hub Room?"))
check("a card with its own location_id is placeable",
      table_router._card_is_placeable(BY_NAME["bld_equipment"]))
check("bld_relationships is placeable through its DECLARED JOIN onto "
      "bld_locations.location_id, though it has no location column of its own",
      "location_id" not in BY_NAME["bld_relationships"]["columns"] and
      table_router._card_is_placeable(BY_NAME["bld_relationships"]))
check("a card with no place at all is not placeable",
      not table_router._card_is_placeable(BY_NAME["bld_om_a_asset_register"]))

# ---------------------------------------------------------------------------
# (d) The mutations. Each weight turns exactly ITS OWN check red — which is
#     also the measurement that decided the tier's shape: a flat +4 alone
#     (M2 below) does NOT fix the reported defect.
# ---------------------------------------------------------------------------
AREA_Q = "what equipment is installed on the 5th floor of Block B?"


def _with_room(**weights):
    saved = {c: getattr(table_router, c) for c in weights}
    try:
        for c, v in weights.items():
            setattr(table_router, c, v)
        return select_tables(Q_ROOM, CARDS, k=3), select_tables(AREA_Q, CARDS, k=3)
    finally:
        for c, v in saved.items():
            setattr(table_router, c, v)


print("\n5. Mutation: removing a weight must bring back the defect it fixes")
rroom, _ = _with_room(_PLACE_WEIGHT=0.0, _ROOM_GRANULARITY_WEIGHT=0.0)
check("M1 whole tier removed -> the room question misses the room-carrying tables",
      not ({"bld_equipment", "bld_equipment_counts"} <= set(rroom)), rroom)

rroom, _ = _with_room(_ROOM_GRANULARITY_WEIGHT=0.0)
check("M2 a FLAT weight with no room-granularity refinement -> the room "
      "question still misses at least one of them (ties, card order decides)",
      not ({"bld_equipment", "bld_equipment_counts"} <= set(rroom)), rroom)

_, rarea = _with_room(_PLACE_WEIGHT=0.0)
check("M3 _PLACE_WEIGHT removed -> a floor question hands rank 0 back to a "
      "table with no place column at all",
      rarea[0] == "bld_om_a_asset_register", rarea)
check("M3 control: with the weight, that placeless table is not ranked first",
      select_tables(AREA_Q, CARDS, k=3)[0] != "bld_om_a_asset_register",
      select_tables(AREA_Q, CARDS, k=3))

# M4 — the declared-join arm of `_card_is_placeable`. Without it,
# `bld_relationships` (placed only through `object_id`, never a `location_id`
# column of its own) stops counting as placeable, drops out of the ranked
# top-k, and takes its only path for `bld_circuits` — its declared
# join-neighbour, and the table this question's answer is actually in — down
# with it.
Q_JOIN = "Which line feeds this in the ground floor server room, and why is the total wrong?"

def _columns_only(card):  # _card_is_placeable as it was before the join arm
    return bool(table_router._LOCATION_COLUMNS & set(card.get("columns", [])))

saved_fn = table_router._card_is_placeable
try:
    table_router._card_is_placeable = _columns_only
    r = select_tables(Q_JOIN, CARDS, k=3)
    check("M4 declared-join arm removed -> loses bld_circuits, the table "
          "its answer is in", "bld_circuits" not in r, r)
finally:
    table_router._card_is_placeable = saved_fn
check("M4 control: with the arm, the question keeps bld_relationships and "
      "bld_circuits",
      {"bld_circuits", "bld_relationships"} <= set(select_tables(Q_JOIN, CARDS, k=3)),
      select_tables(Q_JOIN, CARDS, k=3))

# M5 — the room CODE / room NAME line. Let the bare word "room" fire the
# room-granularity refinement and a question resolved only `via_board` loses
# bld_circuits: at the flat place score it falls behind room-resolving
# tables that were never room-resolving evidence for THIS question. "server
# room" is a printed room NAME, not a key.
Q_ROOMNAME = "What line powers this near the ground floor server room?"
saved_res = table_router._ROOM_CODE_RES
try:
    table_router._ROOM_CODE_RES = saved_res + [table_router.re.compile(
        r"\broom\b", table_router.re.IGNORECASE)]
    r = select_tables(Q_ROOMNAME, CARDS, k=3)
    check("M5 a room NAME treated as a room code -> loses bld_circuits, the "
          "table its answer is in", "bld_circuits" not in r, r)
finally:
    table_router._ROOM_CODE_RES = saved_res
check("M5 control: with the line drawn at codes, the question keeps bld_circuits",
      "bld_circuits" in select_tables(Q_ROOMNAME, CARDS, k=3),
      select_tables(Q_ROOMNAME, CARDS, k=3))

check("both weights restored after the mutations",
      {"bld_equipment", "bld_equipment_counts"} <= set(select_tables(Q_ROOM, CARDS, k=3)))

print("\n6. Determinism is unchanged")
check("identical (question, cards, k) -> identical result",
      select_tables(Q_ROOM, CARDS, k=3) == select_tables(Q_ROOM, CARDS, k=3))
check("empty card list still returns empty", select_tables(Q_ROOM, [], k=3) == [])

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
