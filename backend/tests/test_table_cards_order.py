"""test_table_cards_order.py — the router's card list must be DETERMINISTIC (2026-09-18).

THE DEFECT, measured, not hypothesised.

`table_router.select_tables` scores every card and sorts with
`scored.sort(key=lambda t: (-t[0], t[1]))` — score first, then the card's POSITION in the
list. That is deliberate: it makes the router a pure function of (question, cards, k). But
`sql_tool._load_table_cards` read those cards out of Supabase with **no ORDER BY**, so the
list's order was PostgREST heap order — which `11_table_cards.py --publish` rewrites every
time, because it does `delete()` then `upsert()`.

Consequence, from the 2026-09-18 change-impact run (task-8-diagnosis.md, proof 2): four room
questions tie TEN WAYS at score 0.1111 for the third routed slot. In the BEFORE run the
index tie-break gave that slot to `hwu_room_assets` — the table that holds the answer. After
a republish that changed nothing but row order, it went to `hwu_om_acs_asset_register`, whose
query returned 0 rows. ex-038, ex-044 and ex-045 lost their answer; ex-038 and ex-045 lost
their SQL verdict too. The router replay says their top-3 is BYTE-IDENTICAL before and after,
which is the proof that the cards did not change — the ORDER did.

Shuffling the 70 live cards 40 times changes the top-3 on 39 of 70 questions. Until this
test, nothing in the system said the order of that list mattered at all.

This file pins the fix: one `.order("table_name")` on the database read, and the same
alphabetical order on the local-file fallback, so the two sources agree and a republish can
never silently re-rank the corpus again. It does NOT change the tie-break rule itself
(diagnosis fix 3(b)) — that perturbs 22 other questions and must be measured, not shipped
blind.

Run:
    venv/Scripts/python -X utf8 tests/test_table_cards_order.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.services.sql_tool as sql_tool

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


def card(name):
    return {"table": name, "document": "d", "row_count": 1, "columns": ["a"],
            "one_row_is": "one row", "holds": name}


class RecordingQuery:
    """Records the query chain, so the test can assert what production SENDS — not merely
    what it happens to get back. A fake that sorted its own rows regardless would pass a
    test of the output while the production query stayed unordered.

    Same shape as the `_FakeQuery` in doc-prep/test_9_eval.py, which pins the identical
    guarantee for the eval harness's own router-table fetch.
    """

    def __init__(self, rows):
        self.rows = rows
        self.ordered_by = None
        self.raises = False

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def order(self, col, **_k):
        self.ordered_by = col
        return self

    def execute(self):
        if self.raises:
            raise RuntimeError("connection refused")
        rows = list(self.rows)
        if self.ordered_by:
            rows.sort(key=lambda r: r[self.ordered_by])
        return type("R", (), {"data": rows})()


class RecordingClient:
    def __init__(self, rows):
        self.q = RecordingQuery(rows)

    def table(self, name):
        assert name == "table_cards", f"unexpected table {name!r}"
        return self.q


class NoOrderClient:
    """A client whose query chain has NO `.order()` — an older supabase-py, or a stub. The
    loader's documented contract is that ANY failure to obtain cards degrades to the
    fallback chain rather than raising, and adding a call to the chain must not break it.
    """

    class Q:
        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def execute(self):
            return type("R", (), {"data": []})()

    def table(self, _name):
        return self.Q()


USER = "aaaaaaaa-0000-0000-0000-000000000001"
# Deliberately NOT alphabetical, and with the two tables from the measured defect adjacent:
# heap order put hwu_room_assets after hwu_om_acs_asset_register, which is exactly how the
# third routed slot changed hands without a single card changing.
HEAP_ORDER = ["hwu_room_assets", "hwu_panels", "hwu_om_acs_asset_register", "hwu_equipment"]
SORTED_ORDER = sorted(HEAP_ORDER)


def load(user_id, sb, path=None):
    sql_tool._reset_table_cards_cache()
    if path is not None:
        sql_tool._TABLE_CARDS_PATH = Path(path)
    return sql_tool._load_table_cards(user_id, sb)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="cards_order_"))
    unsorted_file = tmp / "cards.json"
    unsorted_file.write_text(json.dumps([card(n) for n in HEAP_ORDER]), encoding="utf-8")
    missing_file = tmp / "nope.json"

    original_path = sql_tool._TABLE_CARDS_PATH
    try:
        print("1. The database read is ORDERED — a republish cannot re-rank the router")
        sb = RecordingClient([{"table_name": n, "card": card(n)} for n in HEAP_ORDER])
        cards = load(USER, sb, unsorted_file)
        check("the query asks the database to order by table_name",
              sb.q.ordered_by == "table_name", f"ordered_by={sb.q.ordered_by!r}")
        check("the loaded cards come back in table_name order",
              [c["table"] for c in cards] == SORTED_ORDER,
              str([c["table"] for c in cards]))
        check("...which is NOT the order the rows were stored in",
              HEAP_ORDER != SORTED_ORDER)

        print("\n2. Two reads of the same rows give the same list, whatever the heap order")
        shuffled = list(reversed(HEAP_ORDER))
        sb2 = RecordingClient([{"table_name": n, "card": card(n)} for n in shuffled])
        cards2 = load(USER, sb2, unsorted_file)
        check("a different stored order yields an identical card list",
              [c["table"] for c in cards2] == [c["table"] for c in cards],
              str([c["table"] for c in cards2]))

        print("\n3. The local-file fallback is ordered the same way")
        sb3 = RecordingClient([])
        cards3 = load(USER, sb3, unsorted_file)
        check("the file's cards are sorted by table name too",
              [c["table"] for c in cards3] == SORTED_ORDER,
              str([c["table"] for c in cards3]))
        check("database order and file order agree",
              [c["table"] for c in cards3] == [c["table"] for c in cards])

        print("\n4. The safety net still holds — ordering must never take the SQL tool down")
        sb4 = RecordingClient([{"table_name": n, "card": card(n)} for n in HEAP_ORDER])
        sb4.q.raises = True
        cards4 = load(USER, sb4, unsorted_file)
        check("database unreachable -> the sorted local file",
              [c["table"] for c in cards4] == SORTED_ORDER, str(cards4))
        cards5 = load(USER, NoOrderClient(), unsorted_file)
        check("a client with no .order() degrades to the file, never raises",
              [c["table"] for c in cards5] == SORTED_ORDER, str(cards5))
        cards6 = load(USER, NoOrderClient(), missing_file)
        check("no database, no file -> empty list, no exception", cards6 == [], str(cards6))

        print("\n5. A malformed row is still skipped, and does not break the sort")
        sb5 = RecordingClient([{"table_name": "hwu_z", "card": "not-a-dict"},
                               {"table_name": "hwu_a", "card": card("hwu_a")}])
        cards7 = load(USER, sb5, missing_file)
        check("only the parseable card survives, sort unaffected",
              [c["table"] for c in cards7] == ["hwu_a"], str(cards7))
    finally:
        sql_tool._TABLE_CARDS_PATH = original_path
        sql_tool._reset_table_cards_cache()

    print("\nALL PASS" if not FAILS else f"\n{len(FAILS)} FAILED: {', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
