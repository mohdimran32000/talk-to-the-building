"""test_menu_seq_order.py - the tool-choice table menu orders itself off the router cards'
own `menu_seq`, never off table-name alphabetical order (wave 5, 2026-10-04).

THE DEFECT (diagnosed on the goal-function run of 2026-09-30; plan-wave5.md section 2.1).
`_format_structured_tables` builds the menu from its `structured_tables` argument's INCOMING
order, which is alphabetical by table name (the database read is `.order("table_name")`). The
menu is baked once, uncapped, into the frozen temperature-0 tool-choice prompt, printed twice.
Adding one new table shifts the byte offset of every table that sorts after it, which silently
changed the tool's own self-composed sub-question text for an unrelated question and, with it,
the route and the generated SQL for a question that had nothing to do with the new table.

THE FIX (ruling W5-R1: no static table-name list may live in this public repo - a baseline
tuple of client table names would be exactly that). Doc-prep now writes an integer `menu_seq`
onto each router card: the tables that existed before this fix shipped keep their old
alphabetical index, a newly added table appends past the end. `_format_structured_tables` reads
`menu_seq` off the cards it is already given (for the columns) and sorts its tables by it; a
table whose card carries no `menu_seq` - every fixture in this file, and every card today,
before doc-prep's write lands - falls back to the incoming order exactly as before, so the F0
golden fixture (tests/test_tool_choice_input.py, whose CARDS carry no `menu_seq`) stays
byte-identical. `select_tables`'s own list order and scoring are untouched: menu_seq is read
nowhere else.

Every table, column and card in this file is invented. Every check marked RED fails against the
code as it stood before this change.

Run:
    venv/Scripts/python -X utf8 tests/test_menu_seq_order.py
"""
import ast
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import openai_client, table_router  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


def names_read(func):
    """Every name the function's CODE reads (comments and prose cannot satisfy this)."""
    return [n.id for n in ast.walk(ast.parse(inspect.getsource(func))) if isinstance(n, ast.Name)]


def menu_order(menu: str):
    """The table names a rendered menu lists, in the order they appear."""
    return [item.partition("(")[0] for item in menu.split("; ")] if menu else []


# ---------------------------------------------------------------------------
# Fixtures. Three "existing" tables (alphabetical: bravo, mike, zulu) as they were before this
# wave, each card already carrying the menu_seq doc-prep assigns from that same alphabetical
# order; one "new" table (alphabetically FIRST: alpha) whose card appends past the end.
# ---------------------------------------------------------------------------
EXISTING = [
    {"table_name": "fx_bravo", "columns": ["b1"]},
    {"table_name": "fx_mike", "columns": ["m1"]},
    {"table_name": "fx_zulu", "columns": ["z1"]},
]
EXISTING_CARDS = [
    {"table": "fx_bravo", "columns": ["b1"], "menu_seq": 0},
    {"table": "fx_mike", "columns": ["m1"], "menu_seq": 1},
    {"table": "fx_zulu", "columns": ["z1"], "menu_seq": 2},
]
NEW_TABLE = {"table_name": "fx_alpha", "columns": ["a1"]}
NEW_CARD = {"table": "fx_alpha", "columns": ["a1"], "menu_seq": 3}
# The database always hands `structured_tables` back alphabetical (ingestion.py / messages.py
# both `.order("table_name")`), so "today's order" with the new table included is alpha first.
ALPHABETICAL_WITH_NEW = sorted(EXISTING + [NEW_TABLE], key=lambda t: t["table_name"])

# ===========================================================================
print("1. A table inserted alphabetically BEFORE existing ones appends instead (RED)")
# ===========================================================================
before = openai_client._format_structured_tables(EXISTING, EXISTING_CARDS)
check("fixture sanity: the three existing tables, alphabetical", menu_order(before) ==
      ["fx_bravo", "fx_mike", "fx_zulu"], menu_order(before))

after = openai_client._format_structured_tables(ALPHABETICAL_WITH_NEW,
                                                EXISTING_CARDS + [NEW_CARD])
check("RED: every existing table's blurb is byte-identical and at the same offset - the new "
      "table's menu_seq (3) puts it last although its name (fx_alpha) sorts first",
      after.startswith(before), (before, after))
check("RED: the new table is appended, not inserted where its name would sort",
      after == before + "; fx_alpha(a1)", after)
check("RED: ...and the menu order follows menu_seq, never table-name order",
      menu_order(after) == ["fx_bravo", "fx_mike", "fx_zulu", "fx_alpha"], menu_order(after))

# What happens with NO fix (today's alphabetical rebuild) for contrast/documentation: inserting
# fx_alpha shifts fx_bravo off offset 0. This is the shape of the live diagnosed regression.
naive = "; ".join(f"{t['table_name']}({', '.join(t['columns'])})" for t in ALPHABETICAL_WITH_NEW)
check("sanity: the naive (unfixed) alphabetical rebuild is what the fix must avoid",
      not naive.startswith(before) and naive != after, naive)

# ===========================================================================
print("\n2. No card carries menu_seq at all: unchanged from today (the F0 golden's own shape)")
# ===========================================================================
NO_SEQ_CARDS = [{"table": "fx_bravo", "columns": ["b1"]},
               {"table": "fx_mike", "columns": ["m1"]},
               {"table": "fx_zulu", "columns": ["z1"]}]
same = openai_client._format_structured_tables(EXISTING, NO_SEQ_CARDS)
check("identical to the incoming (alphabetical) order when no card has menu_seq", same == before,
      (before, same))
no_cards = openai_client._format_structured_tables(EXISTING, None)
check("identical with no cards at all", no_cards == before, no_cards)
no_cards_new = openai_client._format_structured_tables(ALPHABETICAL_WITH_NEW, None)
check("...even once the new table is loaded: no menu_seq anywhere means table order is untouched",
      menu_order(no_cards_new) == ["fx_alpha", "fx_bravo", "fx_mike", "fx_zulu"],
      menu_order(no_cards_new))

# ===========================================================================
print("\n3. Mixed: a table with no menu_seq falls back to today's order, after the ordered ones")
# ===========================================================================
PARTIAL_CARDS = [{"table": "fx_bravo", "columns": ["b1"], "menu_seq": 0},
                 {"table": "fx_mike", "columns": ["m1"]},  # no menu_seq - not yet republished
                 {"table": "fx_zulu", "columns": ["z1"], "menu_seq": 2}]
partial = openai_client._format_structured_tables(EXISTING, PARTIAL_CARDS)
check("the ordered tables keep their menu_seq places; the unordered one falls to the end",
      menu_order(partial) == ["fx_bravo", "fx_zulu", "fx_mike"], menu_order(partial))

# ===========================================================================
print("\n4. A malformed menu_seq (not a plain int) is treated as absent, never raises")
# ===========================================================================
for bad in ("2", 2.0, True, None, [2]):
    BAD_CARDS = [{"table": "fx_bravo", "columns": ["b1"], "menu_seq": 0},
                {"table": "fx_mike", "columns": ["m1"], "menu_seq": bad},
                {"table": "fx_zulu", "columns": ["z1"], "menu_seq": 2}]
    out = openai_client._format_structured_tables(EXISTING, BAD_CARDS)
    check(f"menu_seq={bad!r} ({type(bad).__name__}) on fx_mike is ignored, not fatal: fx_mike "
          f"falls back to its incoming position (after the two that do carry a valid menu_seq)",
          menu_order(out) == ["fx_bravo", "fx_zulu", "fx_mike"], menu_order(out))

# ===========================================================================
print("\n5. The menu's column text is unaffected - this fix only reorders, never reformats")
# ===========================================================================
check("columns, parens and the '; ' join are exactly as before",
      after.replace("; fx_alpha(a1)", "") == before, after)

# ===========================================================================
print("\n6. select_tables' own order and scoring never read menu_seq")
# ===========================================================================
router_src = inspect.getsource(table_router)
check("table_router's source never mentions menu_seq at all",
      "menu_seq" not in router_src)
select_names = names_read(table_router.select_tables)
check("select_tables's own code reads no name called menu_seq",
      "menu_seq" not in select_names)

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
