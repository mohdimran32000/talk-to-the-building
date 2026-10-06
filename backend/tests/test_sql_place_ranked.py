"""test_sql_place_ranked.py - a ranking of places grouped on the place as each source printed it is
re-queried through the location key (wave 3, G6, 2026-10-03).

THE DEFECT. "Which <place> holds the most ...?" was written as a ranking - GROUP BY a printed place
column, ORDER BY the count DESC - over registers whose printed place text is the place as each
source happened to spell it. One place printed several ways was split several ways, and a
printed word naming a whole storey or an open area ranked first as if it were one place. The
writer's prompt already says a ranking of places groups on the location key, keeping the rows
resolved to that kind of place; the writer obeyed the rule's "never LIMIT 1" and not its key. The
loop had no handle: a grouped result raises no column-shaped issue.

THE FIX, in sql_loop, as a code-detected re-query - the way IDENTIFIER_MISSING and the storey EMPTY
already make a prompt rule enforceable. `inspect_result` raises PLACE_RANKED_ON_TEXT when:
  * the result holds rows and its query is a RANKING: a GROUP BY at the top of the query, and an
    ORDER BY at the top whose first item is sorted DESC and is no column it groups on;
  * a column it groups on belongs to a table the query reads that has a location key and its
    resolution column (read off the loaded table through the injected reader), and no column it
    groups on is that key, that resolution column or a level code;
  * the grouped column's name, apart from words like "as printed", names a storey - and the table
    has a level code - or names a kind of place its resolution column holds.
The instruction, built from those names and that value - never from words written in sql_loop:
    storey: "The previous query ranked by <col>, whose spelling varies: the same storey is spelled
             several ways there. Rank by <level code> instead, and return them all, largest first."
    place:  "The previous query ranked by <col>, whose spelling varies: the same spot is spelled
             several ways there, and some entries cover a whole storey instead. Rank by <key>
             instead, keeping just the entries whose <resolution> = '<kind>', take each spot's
             wording from the listing keyed by <key>, and return them all, largest first."
Its fixed words score on no router card (each re-query is routed on its own text). With no step
left, or with the loop off, nothing is raised - there is no reader then.

WAVE 4, A2 (sections 5-8): the same rule for a COMPARISON of places - `c [NOT] IN (SELECT c ...)`,
EXCEPT, INTERSECT - on a printed place column: PLACE_COMPARED_ON_TEXT, ranked right after
PLACE_RANKED_ON_TEXT, with the instruction "The previous query compared spots by <col>, whose
spelling varies: ... Compare by <key> instead, keeping just the entries whose <resolution> =
'<kind>', and take each spot's wording from the listing keyed by <key>."

Every table, column and value here is invented. Every check marked RED fails against the code as it
stood before this change.

Run:
    venv/Scripts/python -X utf8 tests/test_sql_place_ranked.py
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


KIND = getattr(sql_loop, "PLACE_RANKED_ON_TEXT", "PLACE_RANKED_ON_TEXT")

# The loaded tables, as the reader sees them: columns in order, and each column's values.
GEAR_COLUMNS = ["gear_id", "system", "cubby_as_printed", "storey_as_printed", "block", "qty",
                "location_id", "location_resolution", "level_code"]
LOADED = {
    "bld_gear": {
        "columns": GEAR_COLUMNS,
        # 'storey' is a kind of place here AND a storey word: the storey reading must win.
        "values": {"location_resolution": ["cubby", "deck_block", "deck", "storey", "site"],
                   "cubby_as_printed": ["Nook", "K.07", "Nook K.07"],
                   "level_code": ["01", "02"]},
    },
    "bld_gear_counts": {
        "columns": ["count_id", "system", "cubby_as_printed", "storey_as_printed", "qty",
                    "location_id", "location_resolution", "level_code"],
        "values": {"location_resolution": ["cubby", "deck"]},
    },
    "bld_flat": {   # printed places and no location key at all
        "columns": ["thing_id", "cubby_as_printed", "qty"],
        "values": {},
    },
    "bld_nolevel": {   # a location key, but no level code
        "columns": ["thing_id", "storey_as_printed", "qty", "location_id", "location_resolution"],
        "values": {"location_resolution": ["deck"]},
    },
}


class Reader:
    """`column_values(table, column)` and its `columns(table)`, shaped like
    sql_tool.column_value_reader: None for anything it cannot answer."""
    def __init__(self, loaded):
        self.loaded = loaded
        self.calls = []

    def __call__(self, table, column):
        self.calls.append(("values", table, column))
        found = self.loaded.get(str(table).lower())
        return None if found is None else found["values"].get(column)

    def columns(self, table):
        self.calls.append(("columns", table))
        found = self.loaded.get(str(table).lower())
        return None if found is None else list(found["columns"])


def table_result(cols, rows, sql):
    md = "| " + " | ".join(cols) + " |\n| " + " | ".join(["---"] * len(cols)) + " |\n"
    for r in rows:
        md += "| " + " | ".join(str(v) for v in r) + " |\n"
    return md + f"\nSQL: `{sql}`"


RANKED = [["Nook", 41.0], ["K.07", 9.0], ["Nook K.07", 4.0]]
Q = "Which cubby holds the most gear, and how much?"


def issues(sql, rows=RANKED, cols=("cubby_as_printed", "n"), question=Q, reader="new"):
    reader = Reader(LOADED) if reader == "new" else reader
    return sql_loop.inspect_result(table_result(list(cols), rows, sql), question, [],
                                   column_values=reader)


def hit(found):
    return next((i for i in found if i.kind == KIND), None)


PLACE_SQL = ("SELECT cubby_as_printed, SUM(qty) AS n FROM \"bld_gear\" GROUP BY cubby_as_printed "
             "ORDER BY n DESC NULLS LAST")
PLACE_INSTRUCTION = (
    "The previous query ranked by cubby_as_printed, whose spelling varies: the same spot is "
    "spelled several ways there, and some entries cover a whole storey instead. Rank by "
    "location_id instead, keeping just the entries whose location_resolution = 'cubby', take "
    "each spot's wording from the listing keyed by location_id, and return them all, largest "
    "first.")
STOREY_SQL = ("SELECT storey_as_printed, COUNT(*) AS n FROM \"bld_gear\" WHERE system = 'chime' "
              "GROUP BY storey_as_printed ORDER BY n DESC LIMIT 1")
STOREY_INSTRUCTION = (
    "The previous query ranked by storey_as_printed, whose spelling varies: the same storey is "
    "spelled several ways there. Rank by level_code instead, and return them all, largest first.")

# ---------------------------------------------------------------------------
print("1. A ranking of places on printed place text is re-queried through the key (RED)")
# ---------------------------------------------------------------------------
i1 = hit(issues(PLACE_SQL))
check("RED: the issue is raised", i1 is not None, [i.kind for i in issues(PLACE_SQL)])
check("RED: its instruction names the key, the resolution column and the kind of place, all "
      "read off the loaded table", i1 is not None and i1.instruction == PLACE_INSTRUCTION,
      i1.instruction if i1 else "")
check("RED: and the loop can act on it", sql_loop.first_requery_issue(issues(PLACE_SQL)) is not None
      and sql_loop.first_requery_issue(issues(PLACE_SQL)).kind == KIND)

UNION_SQL = ("SELECT cubby_as_printed, SUM(qty) AS n FROM (SELECT cubby_as_printed, qty FROM "
             "\"bld_gear\" WHERE cubby_as_printed <> '' UNION ALL SELECT cubby_as_printed, qty FROM "
             "\"bld_gear_counts\" WHERE cubby_as_printed <> '') GROUP BY cubby_as_printed ORDER BY "
             "n DESC NULLS LAST")
i1b = hit(issues(UNION_SQL))
check("RED: the same over two registers joined in a sub-SELECT",
      i1b is not None and i1b.instruction == PLACE_INSTRUCTION, i1b.instruction if i1b else "")
i1c = hit(issues(STOREY_SQL, rows=[["Q2", 17.0]], cols=("storey_as_printed", "n"),
                 question="Which storey has the most chime devices?"))
check("RED: a ranking of storeys on printed storey text is re-queried through the level code",
      i1c is not None and i1c.instruction == STOREY_INSTRUCTION, i1c.instruction if i1c else "")
i1d = hit(issues("SELECT g.cubby_as_printed, COUNT(*) FROM \"bld_gear\" g GROUP BY "
                 "g.cubby_as_printed ORDER BY COUNT(*) DESC"))
check("RED: a qualified column ordered by the aggregate itself", i1d is not None
      and "ranked by cubby_as_printed," in i1d.instruction, i1d.instruction if i1d else "")
i1e = hit(issues("SELECT cubby_as_printed, SUM(qty) FROM \"bld_gear\" GROUP BY 1 ORDER BY 2 DESC"))
check("a positional GROUP BY names no column, and stays silent", i1e is None)
both = issues(PLACE_SQL, question="How many items does the busiest cubby hold?")
check("RED: ranked right after LITERAL_ELSEWHERE and wave 5's CURRENT_TWIN, and ahead of the "
      "count cross-check, which carries no instruction",
      KIND in sql_loop.ISSUE_ORDER
      and sql_loop.ISSUE_ORDER.index(KIND) == sql_loop.ISSUE_ORDER.index(
          sql_loop.LITERAL_ELSEWHERE) + 2
      and [i.kind for i in both] == [KIND, sql_loop.COUNT_CROSSCHECK]
      and sql_loop.first_requery_issue(both).kind == KIND, [i.kind for i in both])

# ---------------------------------------------------------------------------
print("\n2. Silent shapes")
# ---------------------------------------------------------------------------
for label2, sql2 in [
    ("grouped on the location key", "SELECT location_id, SUM(qty) AS n FROM \"bld_gear\" "
                                    "WHERE location_resolution = 'cubby' GROUP BY location_id "
                                    "ORDER BY n DESC"),
    ("grouped on the level code", "SELECT level_code, SUM(qty) AS n FROM \"bld_gear\" GROUP BY "
                                  "level_code ORDER BY n DESC LIMIT 1"),
    ("grouped on the resolution column",
     "SELECT location_resolution, COUNT(*) AS n FROM \"bld_gear\" GROUP BY location_resolution "
     "ORDER BY n DESC"),
    ("grouped on a column that is no place (a block)",
     "SELECT block, SUM(qty) AS n FROM \"bld_gear\" GROUP BY block ORDER BY n DESC"),
    ("grouped on the key AND the printed text", "SELECT location_id, cubby_as_printed, SUM(qty) "
                                                "AS n FROM \"bld_gear\" GROUP BY location_id, "
                                                "cubby_as_printed ORDER BY n DESC"),
    ("ordered by the column it groups on (a listing, not a ranking)",
     "SELECT cubby_as_printed, SUM(qty) AS n FROM \"bld_gear\" GROUP BY cubby_as_printed "
     "ORDER BY cubby_as_printed DESC"),
    ("ordered ascending", "SELECT cubby_as_printed, SUM(qty) AS n FROM \"bld_gear\" GROUP BY "
                          "cubby_as_printed ORDER BY n"),
    ("not ordered at all", "SELECT cubby_as_printed, SUM(qty) AS n FROM \"bld_gear\" GROUP BY "
                           "cubby_as_printed"),
    ("a table with no location key", "SELECT cubby_as_printed, SUM(qty) AS n FROM \"bld_flat\" "
                                     "GROUP BY cubby_as_printed ORDER BY n DESC"),
    ("a storey column on a table with no level code",
     "SELECT storey_as_printed, SUM(qty) AS n FROM \"bld_nolevel\" GROUP BY storey_as_printed "
     "ORDER BY n DESC"),
    ("a place word its resolution column does not hold",
     "SELECT system, SUM(qty) AS n FROM \"bld_gear\" GROUP BY system ORDER BY n DESC"),
    ("a GROUP BY only inside a sub-SELECT", "SELECT * FROM (SELECT cubby_as_printed, SUM(qty) AS n "
                                            "FROM \"bld_gear\" GROUP BY cubby_as_printed ORDER BY "
                                            "n DESC) LIMIT 5"),
]:
    check(f"{label2}: no issue", hit(issues(sql2)) is None, [i.kind for i in issues(sql2)])

check("no reader (the last step, or the loop off): no issue",
      hit(issues(PLACE_SQL, reader=None)) is None)
check("a reader that answers nothing: no issue", hit(issues(PLACE_SQL, reader=Reader({}))) is None)


class Broken(Reader):
    def columns(self, table):
        raise RuntimeError("no columns")


check("a reader that raises: no issue, and no exception",
      hit(issues(PLACE_SQL, reader=Broken(LOADED))) is None)
empty_iss = sql_loop.inspect_result(f"Query returned no results.\n\nSQL: `{PLACE_SQL}`", Q, [],
                                    column_values=Reader(LOADED))
check("an EMPTY ranking raises EMPTY and not this", hit(empty_iss) is None
      and sql_loop.EMPTY in [i.kind for i in empty_iss], [i.kind for i in empty_iss])
failed_iss = sql_loop.inspect_result(f"SQL query failed: Binder Error\n\nSQL: `{PLACE_SQL}`", Q,
                                     [], column_values=Reader(LOADED))
check("a FAILED query raises FAILED_SQL and not this", hit(failed_iss) is None
      and sql_loop.FAILED_SQL in [i.kind for i in failed_iss], [i.kind for i in failed_iss])

# ---------------------------------------------------------------------------
print("\n3. The instruction's own words: no hyphen, and no router card scores on them")
# ---------------------------------------------------------------------------
# Each re-query is routed on its own text. Measured on the real router cards, each of these plain
# English words is a column or vocabulary word of at least one card, so in a re-query's FIXED
# wording it pulls tables into the route. The data's own words - the column names and the quoted
# kind - are taken out first: routing on them is the point, and the previous SQL carries them too.
SCORING = ("value", "column", "row", "table", "other", "result", "so", "as", "field", "part",
           "record", "one", "both", "cell", "none", "occurrence", "time", "area", "count", "floor",
           "group", "name", "only", "place", "printed", "source", "text", "beside", "different",
           "document", "kind", "label", "listed", "order", "per", "title", "total", "written")
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


check("the check is not vacuous: a wording with 'printed place text' scores",
      bool({t for t, s in router_scores("ranked on printed place text").items() if s}))
for label3, found, names in [("place", i1, ("cubby_as_printed", "location_id",
                                             "location_resolution")),
                             ("storey", i1c, ("storey_as_printed", "level_code"))]:
    own = fixed_wording(found.instruction, names) if found else "-"
    scored = {t: s for t, s in router_scores(own).items() if s}
    check(f"RED: the {label3} instruction's fixed words score on no card",
          found is not None and not scored, scored)
    check(f"RED: the {label3} instruction's own words carry no hyphen, name no place and ask for "
          f"no change", found is not None and "-" not in own
          and not table_router._names_a_place(own) and not table_router._asks_about_a_change(own),
          own)

# ---------------------------------------------------------------------------
print("\n4. In the loop: one re-query, and the loop off is unchanged")
# ---------------------------------------------------------------------------


class FakeExec:
    def __init__(self, scripted):
        self.scripted, self.questions = list(scripted), []

    def __call__(self, question, user_id, sb):
        self.questions.append(question)
        return self.scripted.pop(0)


KEYED_SQL = ("SELECT location_id, SUM(qty) AS n FROM \"bld_gear\" WHERE location_resolution = "
             "'cubby' GROUP BY location_id ORDER BY n DESC")
first = table_result(["cubby_as_printed", "n"], RANKED, PLACE_SQL)
keyed = table_result(["location_id", "n"], [["CB-K.07", 30.0], ["CB-K.09", 12.0]], KEYED_SQL)
ex = FakeExec([first, keyed])
inv = sql_loop.run_sql_investigation(Q, "u-1", None, execute=ex, routed_cards=[], max_steps=3,
                                     column_values=Reader(LOADED))
check("RED: two queries; the second is asked with the instruction", len(ex.questions) == 2
      and PLACE_INSTRUCTION in ex.questions[1], ex.questions[1:] or ex.questions)
check("RED: the keyed result is the answer, and the issue is on record",
      inv.result_text.startswith(keyed) and KIND in inv.issues, (inv.issues, inv.result_text[:80]))
ex1 = FakeExec([first])
inv1 = sql_loop.run_sql_investigation(Q, "u-1", None, execute=ex1, routed_cards=[], max_steps=1,
                                      column_values=Reader(LOADED))
check("max_steps=1: one query, the result exactly as executed, no issue recorded",
      len(ex1.questions) == 1 and inv1.result_text == first and inv1.issues == [],
      (len(ex1.questions), inv1.issues))
ex2 = FakeExec([first, first])
inv2 = sql_loop.run_sql_investigation(Q, "u-1", None, execute=ex2, routed_cards=[], max_steps=3,
                                      column_values=Reader(LOADED))
check("RED: a writer that ignores it spends one step: the repeat guard ends the loop",
      len(ex2.questions) == 2, len(ex2.questions))

# ===========================================================================
# WAVE 4, A2 (2026-10-03) - G6 generalised from rankings to comparisons: PLACE_COMPARED_ON_TEXT.
#
# "Which <places> have one thing but not another?" was written as `c NOT IN (SELECT c FROM ... WHERE
# <the other thing>)` on a printed place column of a register that prints one place several ways:
# places holding both things under two spellings came back as holding one, beside entries covering
# a whole storey. The result had rows, so nothing fired; G6 needs a ranking. Now a comparison of
# places - `[NOT] IN` a sub-SELECT of the same column, EXCEPT, INTERSECT - on a printed place column
# whose table carries the key and its resolution column, and whose name words name a kind that
# column holds, is re-queried through the key. Every table, column and value is invented.
# ===========================================================================
KIND2 = getattr(sql_loop, "PLACE_COMPARED_ON_TEXT", "PLACE_COMPARED_ON_TEXT")


def hit2(found):
    return next((i for i in found if i.kind == KIND2), None)


SET_ROWS = [["Nook"], ["K.07"], ["Deck 2 west"]]
Q2 = "Which cubbies have lamps but no chime devices?"
SET_SQL = ("SELECT cubby_as_printed FROM \"bld_gear_counts\" WHERE system = 'lamp' AND "
           "cubby_as_printed NOT IN (SELECT cubby_as_printed FROM \"bld_gear_counts\" WHERE "
           "system = 'chime') GROUP BY cubby_as_printed")
COMPARE_INSTRUCTION = (
    "The previous query compared spots by cubby_as_printed, whose spelling varies: the same spot is "
    "spelled several ways there, and some entries cover a whole storey instead. Compare by "
    "location_id instead, keeping just the entries whose location_resolution = 'cubby', and take "
    "each spot's wording from the listing keyed by location_id.")


def set_issues(sql, rows=SET_ROWS, cols=("cubby_as_printed",), question=Q2, reader="new"):
    return issues(sql, rows=rows, cols=cols, question=question, reader=reader)


# ---------------------------------------------------------------------------
print("\n5. Places compared as sets on printed place text: re-queried through the key (A2, RED)")
# ---------------------------------------------------------------------------
i5 = hit2(set_issues(SET_SQL))
check("RED: NOT IN a sub-SELECT of the same printed place column raises the issue", i5 is not None,
      [i.kind for i in set_issues(SET_SQL)])
check("RED: its instruction names the key, the resolution column and the kind of place, all read "
      "off the loaded table", i5 is not None and i5.instruction == COMPARE_INSTRUCTION,
      i5.instruction if i5 else "")
first5 = sql_loop.first_requery_issue(set_issues(SET_SQL))
check("RED: and the loop can act on it", first5 is not None and first5.kind == KIND2,
      first5.kind if first5 else None)
for label5, sql5 in [
    ("two registers, qualified, DISTINCT",
     "SELECT DISTINCT g.cubby_as_printed FROM \"bld_gear\" g WHERE g.system = 'lamp' AND "
     "g.cubby_as_printed NOT IN (SELECT DISTINCT c.cubby_as_printed FROM \"bld_gear_counts\" c "
     "WHERE c.system = 'chime')"),
    ("IN a sub-SELECT (has both)",
     "SELECT cubby_as_printed FROM \"bld_gear\" WHERE system = 'lamp' AND cubby_as_printed IN "
     "(SELECT cubby_as_printed FROM \"bld_gear_counts\" WHERE system = 'chime')"),
    ("EXCEPT", "SELECT cubby_as_printed FROM \"bld_gear\" WHERE system = 'lamp' EXCEPT "
               "SELECT cubby_as_printed FROM \"bld_gear_counts\" WHERE system = 'chime'"),
    ("INTERSECT", "SELECT DISTINCT cubby_as_printed FROM \"bld_gear\" WHERE system = 'lamp' "
                  "INTERSECT SELECT cubby_as_printed FROM \"bld_gear\" WHERE system = 'chime'"),
    ("inside a bracketed group of the WHERE",
     "SELECT cubby_as_printed FROM \"bld_gear\" WHERE (system = 'lamp' AND cubby_as_printed NOT IN "
     "(SELECT cubby_as_printed FROM \"bld_gear\" WHERE system = 'chime'))"),
]:
    found5 = hit2(set_issues(sql5))
    check(f"RED: {label5}: raised, with the same instruction",
          found5 is not None and found5.instruction == COMPARE_INSTRUCTION,
          found5.instruction if found5 else [i.kind for i in set_issues(sql5)])
both5 = set_issues(SET_SQL, question="How many cubbies have lamps but no chime devices?")
check("RED: ranked right after PLACE_RANKED_ON_TEXT, and ahead of the count cross-check",
      KIND2 in sql_loop.ISSUE_ORDER
      and sql_loop.ISSUE_ORDER.index(KIND2) == sql_loop.ISSUE_ORDER.index(KIND) + 1
      and [i.kind for i in both5] == [KIND2, sql_loop.COUNT_CROSSCHECK]
      and sql_loop.first_requery_issue(both5).kind == KIND2, [i.kind for i in both5])
check("a ranking with no comparison still raises PLACE_RANKED_ON_TEXT and not this",
      hit(issues(PLACE_SQL)) is not None and hit2(issues(PLACE_SQL)) is None)

# ---------------------------------------------------------------------------
print("\n6. Silent shapes (A2)")
# ---------------------------------------------------------------------------
for label6, sql6 in [
    ("compared on the location key",
     "SELECT location_id FROM \"bld_gear\" WHERE system = 'lamp' AND location_id NOT IN "
     "(SELECT location_id FROM \"bld_gear\" WHERE system = 'chime')"),
    ("compared on the resolution column",
     "SELECT location_resolution FROM \"bld_gear\" WHERE location_resolution NOT IN "
     "(SELECT location_resolution FROM \"bld_gear_counts\")"),
    ("compared on the level code",
     "SELECT level_code FROM \"bld_gear\" WHERE level_code NOT IN (SELECT level_code FROM "
     "\"bld_gear_counts\")"),
    ("a column that is no place", "SELECT system FROM \"bld_gear\" WHERE system NOT IN "
                                  "(SELECT system FROM \"bld_gear_counts\")"),
    ("two different columns", "SELECT cubby_as_printed FROM \"bld_gear\" WHERE cubby_as_printed "
                              "NOT IN (SELECT storey_as_printed FROM \"bld_gear\")"),
    ("a storey column (compared through its level code, no kind of place)",
     "SELECT storey_as_printed FROM \"bld_gear\" WHERE system = 'lamp' AND storey_as_printed NOT IN "
     "(SELECT storey_as_printed FROM \"bld_gear\" WHERE system = 'chime')"),
    ("a table with no location key", "SELECT cubby_as_printed FROM \"bld_flat\" WHERE "
                                     "cubby_as_printed NOT IN (SELECT cubby_as_printed FROM "
                                     "\"bld_flat\" WHERE qty > 1)"),
    ("the other side's table has no location key",
     "SELECT cubby_as_printed FROM \"bld_gear\" WHERE cubby_as_printed NOT IN "
     "(SELECT cubby_as_printed FROM \"bld_flat\")"),
    ("an IN list of literals", "SELECT cubby_as_printed FROM \"bld_gear\" WHERE cubby_as_printed "
                               "IN ('Nook', 'K.07')"),
    ("a UNION, which combines and compares nothing",
     "SELECT cubby_as_printed FROM \"bld_gear\" WHERE system = 'lamp' UNION "
     "SELECT cubby_as_printed FROM \"bld_gear\" WHERE system = 'chime'"),
    ("EXCEPT over arms of two columns", "SELECT cubby_as_printed, system FROM \"bld_gear\" EXCEPT "
                                        "SELECT cubby_as_printed, system FROM \"bld_gear_counts\""),
    ("a sub-SELECT of more than one column", "SELECT cubby_as_printed FROM \"bld_gear\" WHERE "
                                             "cubby_as_printed NOT IN (SELECT cubby_as_printed, qty "
                                             "FROM \"bld_gear_counts\")"),
]:
    check(f"{label6}: no issue", hit2(set_issues(sql6)) is None, [i.kind for i in set_issues(sql6)])
check("no reader (the last step, or the loop off): no issue", hit2(set_issues(SET_SQL, reader=None))
      is None)
check("a reader that answers nothing: no issue",
      hit2(set_issues(SET_SQL, reader=Reader({}))) is None)
check("a reader that raises: no issue, and no exception",
      hit2(set_issues(SET_SQL, reader=Broken(LOADED))) is None)
empty6 = sql_loop.inspect_result(f"Query returned no results.\n\nSQL: `{SET_SQL}`", Q2, [],
                                 column_values=Reader(LOADED))
check("EMPTY outranks it: an empty comparison raises EMPTY and not this",
      hit2(empty6) is None and sql_loop.EMPTY in [i.kind for i in empty6], [i.kind for i in empty6])
failed6 = sql_loop.inspect_result(f"SQL query failed: Binder Error\n\nSQL: `{SET_SQL}`", Q2, [],
                                  column_values=Reader(LOADED))
check("a FAILED query raises FAILED_SQL and not this", hit2(failed6) is None
      and sql_loop.FAILED_SQL in [i.kind for i in failed6], [i.kind for i in failed6])

# ---------------------------------------------------------------------------
print("\n7. The A2 instruction's own words: no hyphen, and no router card scores on them")
# ---------------------------------------------------------------------------
own7 = fixed_wording(i5.instruction, ("cubby_as_printed", "location_id", "location_resolution")) \
    if i5 else "-"
scored7 = {t: s for t, s in router_scores(own7).items() if s}
check("RED: its fixed words score on no card", i5 is not None and not scored7, scored7)
check("RED: its own words carry no hyphen, name no place and ask for no change",
      i5 is not None and "-" not in own7 and not table_router._names_a_place(own7)
      and not table_router._asks_about_a_change(own7), own7)

# ---------------------------------------------------------------------------
print("\n8. In the loop: one re-query, and the loop off is unchanged (A2)")
# ---------------------------------------------------------------------------
KEYED_SET_SQL = ("SELECT location_id FROM \"bld_gear_counts\" WHERE location_resolution = 'cubby' "
                 "AND system = 'lamp' AND location_id NOT IN (SELECT location_id FROM "
                 "\"bld_gear_counts\" WHERE system = 'chime')")
first8 = table_result(["cubby_as_printed"], SET_ROWS, SET_SQL)
keyed8 = table_result(["location_id"], [["CB-K.07"]], KEYED_SET_SQL)
ex8 = FakeExec([first8, keyed8])
inv8 = sql_loop.run_sql_investigation(Q2, "u-1", None, execute=ex8, routed_cards=[], max_steps=3,
                                      column_values=Reader(LOADED))
check("RED: two queries; the second is asked with the instruction", len(ex8.questions) == 2
      and COMPARE_INSTRUCTION in ex8.questions[1], ex8.questions[1:] or ex8.questions)
check("RED: the keyed result is the answer, and the issue is on record",
      inv8.result_text.startswith(keyed8) and KIND2 in inv8.issues, (inv8.issues, inv8.result_text[:80]))
ex8b = FakeExec([first8])
inv8b = sql_loop.run_sql_investigation(Q2, "u-1", None, execute=ex8b, routed_cards=[], max_steps=1,
                                       column_values=Reader(LOADED))
check("max_steps=1: one query, the result exactly as executed, no issue recorded",
      len(ex8b.questions) == 1 and inv8b.result_text == first8 and inv8b.issues == [],
      (len(ex8b.questions), inv8b.issues))

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
