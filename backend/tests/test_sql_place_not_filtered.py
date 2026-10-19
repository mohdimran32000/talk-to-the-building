"""test_sql_place_not_filtered.py - a location filter on the wrong VALUE is re-queried with
the question's own resolved place id (task W13-A1, 2026-10-18).

THE DEFECT. Asked about two rooms named by their own printed code, the SQL writer's prompt
already carried a line resolving each code to its location id (`sql_tool._place_lines` /
`resolve_place_codes`), yet the SQL filtered the location-id column on two OTHER rooms' ids.
The result was not empty - it held the wrong rooms' rows - so nothing in the loop caught it:
the filtered column WAS the key, so `PLACE_COMPARED_ON_TEXT` never looks (its own job is a
column that is NOT the key); the result had rows, so `EMPTY` never looks either. The answer
said the asked rooms were not shown.

THE FIX, in sql_loop, as a code-detected re-query. `inspect_result` raises
`PLACE_NOT_FILTERED` when:
  * the question - read without the loop's own step suffix - resolves one or more printed
    place codes to location ids, through an injected `place_codes` reader (the SAME
    resolution `sql_tool._place_lines` offers the writer, never a second resolver);
  * the WHERE of the executed SQL compares a column name-shaped like the location key
    (wherever it is spelled) with one or more plain string literals, via `=` or `IN (...)`;
  * NONE of those literals is one of the resolved ids.
The instruction names every resolved code and its id, verbatim, and asks the writer to filter
on them instead. With no step left, or with the loop off, nothing is raised - there is no
resolver then.

Every table, column and value here is invented. Every check marked RED fails against the
code as it stood before this change.

Run:
    venv/Scripts/python -X utf8 tests/test_sql_place_not_filtered.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import sql_loop, table_router  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


KIND = getattr(sql_loop, "PLACE_NOT_FILTERED", "PLACE_NOT_FILTERED")


def table_result(cols, rows, sql):
    md = "| " + " | ".join(cols) + " |\n| " + " | ".join(["---"] * len(cols)) + " |\n"
    for r in rows:
        md += "| " + " | ".join(str(v) for v in r) + " |\n"
    return md + f"\nSQL: `{sql}`"


def hit(found):
    return next((i for i in found if i.kind == KIND), None)


# The places a question might name, and their real location ids - invented, generic.
PLACES = {"CB-K.07": "loc-aaa-111", "CB-N.02": "loc-bbb-222"}


class Resolver:
    """`place_codes(text) -> [(token, location id, display name), …]`, shaped like
    `sql_tool.place_code_resolver`'s own return - a fixed dict of printed codes this
    question might contain, each found once if its token appears in `text` at all (good
    enough for a fake; the real token/ambiguity rules are `resolve_place_codes`'s own,
    tested in `test_place_code_resolver.py`)."""

    def __init__(self, places):
        self.places = dict(places)
        self.calls = []

    def __call__(self, text):
        self.calls.append(text)
        return [(token, loc, token) for token, loc in self.places.items() if token in text]


def issues(sql, rows=None, cols=("gear_tag", "location_id"), question=None, resolver="new"):
    rows = rows if rows is not None else [["G-1", "loc-zzz-999"]]
    question = question or "What gear is in CB-K.07?"
    resolver = Resolver(PLACES) if resolver == "new" else resolver
    return sql_loop.inspect_result(table_result(list(cols), rows, sql), question, [],
                                   place_codes=resolver)


WRONG_SQL = "SELECT gear_tag, location_id FROM \"bld_gear\" WHERE location_id = 'loc-zzz-999'"
WRONG_INSTRUCTION = (
    "The previous query filtered location_id with an id that is not the question's own: "
    "the question's CB-K.07 is location_id loc-aaa-111. Filter location_id with it instead, "
    "never with the previous query's own id.")

# ---------------------------------------------------------------------------
print("1. A location filter on the wrong value is re-queried with the resolved id (RED)")
# ---------------------------------------------------------------------------
i1 = hit(issues(WRONG_SQL))
check("RED: the issue is raised", i1 is not None, [i.kind for i in issues(WRONG_SQL)])
check("RED: its instruction names the column, the resolved code and its id, verbatim",
      i1 is not None and i1.instruction == WRONG_INSTRUCTION, i1.instruction if i1 else "")
check("RED: and the loop can act on it",
      sql_loop.first_requery_issue(issues(WRONG_SQL)) is not None
      and sql_loop.first_requery_issue(issues(WRONG_SQL)).kind == KIND)

WRONG_IN_SQL = ("SELECT gear_tag, location_id FROM \"bld_gear\" WHERE location_id IN "
                "('loc-zzz-999', 'loc-yyy-888')")
i1b = hit(issues(WRONG_IN_SQL))
check("RED: an IN list of wrong literals is the same shape",
      i1b is not None and i1b.instruction == WRONG_INSTRUCTION, i1b.instruction if i1b else "")

TWO_CODES_SQL = ("SELECT gear_tag, location_id FROM \"bld_gear\" WHERE location_id IN "
                 "('loc-zzz-999', 'loc-yyy-888')")
i1c = hit(issues(TWO_CODES_SQL, question="Compare CB-K.07 with CB-N.02."))
TWO_CODES_INSTRUCTION = (
    "The previous query filtered location_id with an id that is not the question's own: "
    "the question's CB-K.07 is location_id loc-aaa-111; the question's CB-N.02 is "
    "location_id loc-bbb-222. Filter location_id with them instead, never with the "
    "previous query's own id.")
check("RED: two resolved codes are both named, verbatim, and the wording says 'them'",
      i1c is not None and i1c.instruction == TWO_CODES_INSTRUCTION,
      i1c.instruction if i1c else "")

QUALIFIED_SQL = ("SELECT g.gear_tag, g.location_id FROM \"bld_gear\" g WHERE g.location_id = "
                 "'loc-zzz-999'")
i1d = hit(issues(QUALIFIED_SQL))
check("RED: a qualified column (t.location_id) is still recognised",
      i1d is not None and i1d.instruction == WRONG_INSTRUCTION, i1d.instruction if i1d else "")

CASED_SQL = "SELECT gear_tag, LOCATION_ID FROM \"bld_gear\" WHERE LOCATION_ID = 'loc-zzz-999'"
i1e = hit(issues(CASED_SQL))
check("RED: a differently-cased column name is still recognised",
      i1e is not None and "location_id" in i1e.instruction.lower(), i1e)

both = issues(WRONG_SQL, question="How many items are in CB-K.07?")
check("RED: ranked right after PLACE_COMPARED_ON_TEXT, ahead of the count cross-check",
      KIND in sql_loop.ISSUE_ORDER
      and sql_loop.ISSUE_ORDER.index(KIND)
          == sql_loop.ISSUE_ORDER.index(sql_loop.PLACE_COMPARED_ON_TEXT) + 1
      and sql_loop.ISSUE_ORDER.index(KIND) < sql_loop.ISSUE_ORDER.index(sql_loop.EMPTY)
      and [i.kind for i in both] == [KIND, sql_loop.COUNT_CROSSCHECK]
      and sql_loop.first_requery_issue(both).kind == KIND, [i.kind for i in both])

# ---------------------------------------------------------------------------
print("\n2. Silent shapes")
# ---------------------------------------------------------------------------
RIGHT_SQL = "SELECT gear_tag, location_id FROM \"bld_gear\" WHERE location_id = 'loc-aaa-111'"
check("a matching literal (the resolved id itself): no issue",
      hit(issues(RIGHT_SQL)) is None, [i.kind for i in issues(RIGHT_SQL)])

RIGHT_IN_SQL = ("SELECT gear_tag, location_id FROM \"bld_gear\" WHERE location_id IN "
               "('loc-zzz-999', 'loc-aaa-111')")
check("ONE matching literal among several: no issue, even though another is wrong",
      hit(issues(RIGHT_IN_SQL)) is None, [i.kind for i in issues(RIGHT_IN_SQL)])

for label2, sql2 in [
    ("no WHERE filter at all", "SELECT gear_tag, location_id FROM \"bld_gear\""),
    ("a filter on a different column entirely",
     "SELECT gear_tag, location_id FROM \"bld_gear\" WHERE gear_tag = 'G-1'"),
    ("a LIKE/ILIKE filter on the key column (not '=' or 'IN')",
     "SELECT gear_tag, location_id FROM \"bld_gear\" WHERE location_id ILIKE '%loc-zzz%'"),
    ("a printed-text column compared, not the key - PLACE_COMPARED_ON_TEXT's own job",
     "SELECT cubby_as_printed FROM \"bld_gear\" WHERE cubby_as_printed = 'Nook'"),
    ("the resolution column, not the key, filtered",
     "SELECT location_resolution FROM \"bld_gear\" WHERE location_resolution = 'cubby'"),
]:
    check(f"{label2}: no issue", hit(issues(sql2)) is None, [i.kind for i in issues(sql2)])

check("nothing resolves (the question names no place code at all): no issue",
      hit(issues(WRONG_SQL, question="What gear is in the lobby?")) is None)
# The existing rule in `resolve_place_codes`/`test_place_code_resolver.py`: a code matching
# TWO rooms resolves to nothing. At this (unit) level that is simply a resolver answering [].
check("RED: a code resolving to two rooms resolves to nothing (existing rule) -> silent",
      hit(issues(WRONG_SQL, resolver=lambda text: [])) is None)
check("place_codes is None (the default, or the last step): no issue, no crash",
      hit(issues(WRONG_SQL, resolver=None)) is None)


class Raises:
    def __call__(self, text):
        raise RuntimeError("resolver down")


check("a resolver that raises: no issue, and no exception",
      hit(issues(WRONG_SQL, resolver=Raises())) is None)

empty_iss = sql_loop.inspect_result(f"Query returned no results.\n\nSQL: `{WRONG_SQL}`",
                                    "What gear is in CB-K.07?", [],
                                    place_codes=Resolver(PLACES))
check("an EMPTY result raises EMPTY and not this", hit(empty_iss) is None
      and sql_loop.EMPTY in [i.kind for i in empty_iss], [i.kind for i in empty_iss])
failed_iss = sql_loop.inspect_result(f"SQL query failed: Binder Error\n\nSQL: `{WRONG_SQL}`",
                                     "What gear is in CB-K.07?", [], place_codes=Resolver(PLACES))
check("a FAILED query raises FAILED_SQL and not this", hit(failed_iss) is None
      and sql_loop.FAILED_SQL in [i.kind for i in failed_iss], [i.kind for i in failed_iss])

# ---------------------------------------------------------------------------
print("\n3. The question is read WITHOUT the loop's own step suffix")
# ---------------------------------------------------------------------------
suffixed_resolver = Resolver(PLACES)
suffixed_question = ("What gear is in CB-K.07?"
                      "\n(Investigation step 2: some earlier advice. "
                      "Previous SQL: `SELECT * FROM \"bld_gear\" WHERE gear_tag = 'CB-N.02'`)")
_ = issues(WRONG_SQL, question=suffixed_question, resolver=suffixed_resolver)
check("RED: the resolver is asked with the suffix stripped, never the previous SQL's own "
      "quoted text", suffixed_resolver.calls == ["What gear is in CB-K.07?"],
      suffixed_resolver.calls)

# ---------------------------------------------------------------------------
print("\n4. The instruction's own words: no hyphen, and no router card scores on them")
# ---------------------------------------------------------------------------
# The EXACT measured list `test_sql_place_ranked.py` uses for this module's other place
# issues (never extended with an unmeasured guess): each of these plain English words is a
# column or vocabulary word of at least one REAL router card, so a re-query's fixed wording
# must avoid all of them.
SCORING = ("value", "column", "row", "table", "other", "result", "so", "as", "field", "part",
           "record", "one", "both", "cell", "none", "occurrence", "time", "area", "count",
           "floor", "group", "name", "only", "place", "printed", "source", "text", "beside",
           "different", "document", "kind", "label", "listed", "order", "per", "title",
           "total", "written")
ROUTER_CARDS = [{"table": f"bld_{w}", "columns": [w]} for w in SCORING]


def fixed_wording(text, names):
    text = re.sub(r"'[^']*'", " ", text)
    for name in names:
        text = text.replace(name, " ")
    return text


def router_scores(text):
    words = {id(c): (table_router._card_subject_words(c), table_router._card_vocab_words(c))
             for c in ROUTER_CARDS}
    df = table_router._document_frequency(ROUTER_CARDS, words)
    qw, qp = table_router._question_words(text), table_router._question_prefixes(text)
    return {c["table"]: table_router._score(qw, qp, c, df, words) for c in ROUTER_CARDS}


own4 = fixed_wording(i1.instruction, ("location_id", "CB-K.07", "loc-aaa-111")) if i1 else "-"
scored4 = {t: s for t, s in router_scores(own4).items() if s}
check("RED: the instruction's fixed words score on no card", i1 is not None and not scored4,
      scored4)
check("RED: the instruction's own words carry no hyphen, name no place and ask for no change",
      i1 is not None and "-" not in own4 and not table_router._names_a_place(own4)
      and not table_router._asks_about_a_change(own4), own4)

# ---------------------------------------------------------------------------
print("\n5. In the loop: one re-query, and the loop off is unchanged")
# ---------------------------------------------------------------------------


class FakeExec:
    def __init__(self, scripted):
        self.scripted, self.questions = list(scripted), []

    def __call__(self, question, user_id, sb):
        self.questions.append(question)
        return self.scripted.pop(0)


KEYED_SQL = "SELECT gear_tag, location_id FROM \"bld_gear\" WHERE location_id = 'loc-aaa-111'"
Q5 = "What gear is in CB-K.07?"
first5 = table_result(["gear_tag", "location_id"], [["G-1", "loc-zzz-999"]], WRONG_SQL)
keyed5 = table_result(["gear_tag", "location_id"], [["G-2", "loc-aaa-111"]], KEYED_SQL)
ex5 = FakeExec([first5, keyed5])
inv5 = sql_loop.run_sql_investigation(Q5, "u-1", None, execute=ex5, routed_cards=[],
                                      max_steps=3, place_codes=Resolver(PLACES))
check("RED: two queries; the second carries the instruction naming the resolved id",
      len(ex5.questions) == 2 and WRONG_INSTRUCTION in ex5.questions[1], ex5.questions[1:])
check("RED: the keyed (second) result is the answer, and the issue is on record",
      inv5.result_text.startswith(keyed5) and KIND in inv5.issues,
      (inv5.issues, inv5.result_text[:80]))

ex5b = FakeExec([first5])
inv5b = sql_loop.run_sql_investigation(Q5, "u-1", None, execute=ex5b, routed_cards=[],
                                       max_steps=1, place_codes=Resolver(PLACES))
check("max_steps<=1: one query, the result exactly as executed, no issue recorded "
      "(pinned byte-identical)",
      len(ex5b.questions) == 1 and inv5b.result_text == first5 and inv5b.issues == [],
      (len(ex5b.questions), inv5b.issues))

ex5c = FakeExec([first5, first5])
inv5c = sql_loop.run_sql_investigation(Q5, "u-1", None, execute=ex5c, routed_cards=[],
                                       max_steps=3, place_codes=Resolver(PLACES))
check("RED: a writer that ignores it spends one step: the repeat guard ends the loop",
      len(ex5c.questions) == 2, len(ex5c.questions))

# place_codes=None (no reader at all) through the whole loop: never raised, never asked
ex5d = FakeExec([first5])
inv5d = sql_loop.run_sql_investigation(Q5, "u-1", None, execute=ex5d, routed_cards=[],
                                       max_steps=3, place_codes=None)
check("place_codes never given: the loop runs once and stops (nothing to act on)",
      len(ex5d.questions) == 1 and KIND not in inv5d.issues,
      (len(ex5d.questions), inv5d.issues))

# ---------------------------------------------------------------------------
print("\n6. The module is still generic - no table, column value, system or building is "
     "named by this change")
# ---------------------------------------------------------------------------
SRC = Path(sql_loop.__file__).read_text(encoding="utf-8")
for tok in ("hwu", "room", "panel", "camera", "door", "fcu", "location_id"):
    hits = len(re.findall(tok, SRC, re.IGNORECASE))
    check(f"no '{tok}' anywhere in the module", hits == 0, f"{hits} hits")

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
