"""test_answer_check.py - the deterministic final-answer checker, OFF by default
(spec 2026-09-30, task W8-A1 part B).

Every fixture here is INVENTED - no table, column, value, card id or question text of the
project. The module is pure functions: (question, answer, sql_results, excerpts, tools_ran) in,
a list of `Problem` out. Each of the four checks is tested in isolation first, then
`find_problems` for how `enabled` gates which ones run.

Run:
    venv/Scripts/python -X utf8 tests/test_answer_check.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


from app.services import answer_check as ac  # noqa: E402

# --------------------------------------------------------------------------- is_refusal_opening

check("RED: a refusal opening is detected",
      ac.is_refusal_opening("The records do not contain the serial number for this part."))
check("a refusal phrase buried in a LATER sentence, after a figure, is NOT an opening refusal "
      "(a two-part answer that already gave its own figure)",
      not ac.is_refusal_opening(
          "The widget's rating is 12 kW. The records do not state its warranty period."))
check("a plain affirmative answer is not a refusal",
      not ac.is_refusal_opening("There are 6 widgets installed in the room."))
check("empty string is not a refusal", not ac.is_refusal_opening(""))

# --------------------------------------------------------------------------- check 1: REFUSED_WITHOUT_SEARCH

REFUSAL = "The records do not contain the inspection interval for this device."
AFFIRMS = "The device is inspected every 6 months, per the maintenance schedule."

p = ac.check_refused_without_search(REFUSAL, ["query_structured_data"])
check("RED: fires - SQL-only turn, refusal opening, search never ran",
      p is not None and p.kind == ac.REFUSED_WITHOUT_SEARCH)

check("does not fire when search_documents also ran this turn",
      ac.check_refused_without_search(REFUSAL, ["query_structured_data", "search_documents"])
      is None)
check("does not fire when the SQL tool never ran at all (nothing to backstop)",
      ac.check_refused_without_search(REFUSAL, ["search_documents"]) is None)
check("does not fire when the SQL tool never ran at all (no tools)",
      ac.check_refused_without_search(REFUSAL, []) is None)
check("does not fire on an affirmative answer, even SQL-only",
      ac.check_refused_without_search(AFFIRMS, ["query_structured_data"]) is None)

# --------------------------------------------------------------------------- check 2: REFUSED_WITH_ROWS

NONEMPTY_RESULT = "| device | rating |\n| --- | --- |\n| widget-1 | 12 kW |\n"
EMPTY_RESULT = "Query returned no results for the given filters."
ALL_NULL_RESULT = "| total |\n| --- |\n|  |\n"

p2 = ac.check_refused_with_rows(REFUSAL, [NONEMPTY_RESULT])
check("RED: fires - last SQL result holds rows, answer opens with a refusal",
      p2 is not None and p2.kind == ac.REFUSED_WITH_ROWS and "1 row" in p2.instruction)

check("does not fire when the last SQL result is explicitly empty",
      ac.check_refused_with_rows(REFUSAL, [EMPTY_RESULT]) is None)
check("does not fire when the last SQL result's cells are all NULL (rendered blank)",
      ac.check_refused_with_rows(REFUSAL, [ALL_NULL_RESULT]) is None)
check("does not fire with no SQL result at all",
      ac.check_refused_with_rows(REFUSAL, []) is None)
check("does not fire on an affirmative answer even with rows present",
      ac.check_refused_with_rows(AFFIRMS, [NONEMPTY_RESULT]) is None)
check("only the LAST sql result is consulted: an earlier empty one does not save a later "
      "non-empty one from firing",
      ac.check_refused_with_rows(REFUSAL, [EMPTY_RESULT, NONEMPTY_RESULT]) is not None)
check("...and an earlier non-empty one does not make a later EMPTY one fire",
      ac.check_refused_with_rows(REFUSAL, [NONEMPTY_RESULT, EMPTY_RESULT]) is None)

# --------------------------------------------------------------------------- check 3: UNGROUNDED_NUMBER

SQL_R = "| count | total |\n| --- | --- |\n| 10 | 500 |\n"
EXCERPT = "The manual states a replacement cycle of 4 units per visit."
QUESTION = "How many widgets were installed in 2021?"

check("RED: a number present nowhere (question/SQL/excerpts) and not a sum/difference of two "
      "of theirs is flagged",
      any(pr.kind == ac.UNGROUNDED_NUMBER for pr in
          ac.check_ungrounded_number(QUESTION, "There are 777 widgets in total.", [SQL_R], [])))

check("a number copied verbatim from the SQL result is grounded (no problem)",
      ac.check_ungrounded_number(QUESTION, "The total is 500.", [SQL_R], []) == [])
check("a number copied verbatim from an excerpt is grounded",
      ac.check_ungrounded_number(QUESTION, "4 units per visit.", [], [EXCERPT]) == [])
check("a number copied verbatim from the question is grounded",
      ac.check_ungrounded_number(QUESTION, "The question concerns the year 2021 total.",
                                  [], []) == [])
check("the SUM of two numbers the data prints is grounded",
      ac.check_ungrounded_number(QUESTION, "Combined, that is 510 units.", [SQL_R], []) == [])
check("the DIFFERENCE of two numbers the data prints is grounded",
      ac.check_ungrounded_number(QUESTION, "That leaves 490 remaining.", [SQL_R], []) == [])
check("a trailing '.0' does not stop a whole number from being recognised as grounded",
      ac.check_ungrounded_number(QUESTION, "The total is 500.0.", [SQL_R], []) == [])
check("a comma-grouped SOURCE number grounds the same value stated plainly in the answer",
      ac.check_ungrounded_number(QUESTION, "The total is 1500.",
                                  ["| total |\n| --- |\n| 1,500 |\n"], []) == [])
check("a comma-grouped ANSWER number is recognised against a plain source value",
      ac.check_ungrounded_number(QUESTION, "The total is 1,500.",
                                  ["| total |\n| --- |\n| 1500 |\n"], []) == [])

CAPACITY_Q = "Is the capacity of this unit 77 units?"
check("a number grounded via the QUESTION alone (never printed in any result or excerpt)",
      ac.check_ungrounded_number(CAPACITY_Q, "Yes, the capacity is 77 units.", [], []) == [])

check("EXCLUSION - a bare year is never flagged",
      ac.check_ungrounded_number(QUESTION, "It was commissioned in 2019.", [], []) == [])
check("EXCLUSION - an ISO date's numbers are never flagged",
      ac.check_ungrounded_number(QUESTION, "Issued on 2024-03-15.", [], []) == [])
check("EXCLUSION - a 'D Month YYYY' date's numbers are never flagged",
      ac.check_ungrounded_number(QUESTION, "Signed on 12 April 2021.", [], []) == [])
check("EXCLUSION - a leading list ordinal is never flagged",
      ac.check_ungrounded_number(QUESTION, "1. Replace the filter.\n2. Clean the tray.",
                                  [], []) == [])
check("EXCLUSION - a small meta-enumeration count is never flagged",
      ac.check_ungrounded_number(QUESTION, "The following 3 items apply.", [], []) == [])
check("EXCLUSION - a digit fused into a hyphenated tag is never extracted as a bare number",
      ac.check_ungrounded_number(QUESTION, "See board TAG-07(X)-99 for details.", [], []) == [])

# --------------------------------------------------------------------------- check 4: MIXED_SET

NOTES_NO_INALL = (
    f"{ac.NOTES_INSIDE_FIGURE_HEADING}1 row behind this figure carries a note:\n"
    "- qty 1: a disputed reading\n"
    "Without the noted row(s), the same figure is: 9.\n"
)
NOTES_WITH_INALL = (
    f"{ac.NOTES_INSIDE_FIGURE_HEADING}2 rows behind this figure carry a note:\n"
    "- qty 1: a disputed reading\n"
    "- qty 1: a disputed reading\n"
    "Without the noted row(s), the same figure is: (count = 8, amount = 380).\n"
    "In all: count 10 (8 without the noted rows); amount 500 (380 without the noted rows)\n"
)

check("no fire with no NOTES INSIDE THE FIGURE block at all",
      ac.check_mixed_set("There are 10 units, totalling 500.", ["plain result, no block"])
      is None)
check("no fire when the block has no 'In all' line (single-group aggregate)",
      ac.check_mixed_set("There are 10 units.", [NOTES_NO_INALL]) is None)

mixed_answer = "There are 10 units, totalling 380 in all."
clean_with = "There are 10 units, totalling 500 in all."
clean_without = "There are 8 units, totalling 380 in all."

check("RED: fires - the answer pairs one reading's count with the OTHER reading's total",
      ac.check_mixed_set(mixed_answer, [NOTES_WITH_INALL]) is not None)
check("does not fire when both figures stated are the WITH reading",
      ac.check_mixed_set(clean_with, [NOTES_WITH_INALL]) is None)
check("does not fire when both figures stated are the WITHOUT reading",
      ac.check_mixed_set(clean_without, [NOTES_WITH_INALL]) is None)
check("does not fire when the answer states BOTH complete readings separately (good behaviour)",
      ac.check_mixed_set(
          "There are 10 units, totalling 500. Excluding the 2 disputed rows, that is 8 units "
          "totalling 380.", [NOTES_WITH_INALL]) is None)

# --------------------------------------------------------------------------- find_problems + enabled gating

all_on = ac.find_problems(QUESTION, REFUSAL, [NONEMPTY_RESULT], [], ["query_structured_data"],
                          enabled=ac.ALL_CHECKS)
check("find_problems with everything enabled catches both refusal checks on one bad answer",
      {p.kind for p in all_on} == {ac.REFUSED_WITHOUT_SEARCH, ac.REFUSED_WITH_ROWS})

none_on = ac.find_problems(QUESTION, REFUSAL, [NONEMPTY_RESULT], [], ["query_structured_data"],
                           enabled=frozenset())
check("find_problems with nothing enabled finds nothing, whatever the answer",
      none_on == [])

one_on = ac.find_problems(QUESTION, REFUSAL, [NONEMPTY_RESULT], [], ["query_structured_data"],
                          enabled={ac.REFUSED_WITH_ROWS})
check("find_problems runs ONLY the enabled checks",
      [p.kind for p in one_on] == [ac.REFUSED_WITH_ROWS])

check("SHIPPED_CHECKS is defined and is a subset of ALL_CHECKS (whatever the gate measured)",
      isinstance(ac.SHIPPED_CHECKS, frozenset) and ac.SHIPPED_CHECKS <= set(ac.ALL_CHECKS))

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
