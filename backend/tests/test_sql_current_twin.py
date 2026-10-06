"""test_sql_current_twin.py - a present-tense question stuck on the pre-takeover table is
re-queried onto its current twin (wave 5, G13 part a, 2026-10-04, designed in plan-wave4.md).

THE DEFECT. Step 1 of a present-tense question read a pre-takeover/historical table because the
question's words overlap it as well as its current-state twin; that query came back EMPTY, and
the generic EMPTY re-query dropped the filter that made it empty but never moved the writer off
the historical table. The final, non-empty result still answered from the before-state - no step
of any run ever read the current twin, though it was routed right alongside the historical one.

THE FIX, in sql_loop only. A new issue, CURRENT_TWIN: when the result holds rows (never on an
EMPTY result - EMPTY's own re-query already runs first - or a FAILED one), the SQL reads a table
whose current-state twin was ALSO among the routed cards, and the question never says it means
the historical state on purpose (existing / before / pre-takeover / original / historical) - one
more re-query names both tables and asks for the current one.

Every table, column and value here is invented. Every check marked RED fails against the code as
it stood before this change.

Run:
    venv/Scripts/python -X utf8 tests/test_sql_current_twin.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import sql_loop, sql_tool  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


KIND = getattr(sql_loop, "CURRENT_TWIN", "CURRENT_TWIN")


def table_result(cols, rows, sql):
    md = "| " + " | ".join(cols) + " |\n| " + " | ".join(["---"] * len(cols)) + " |\n"
    for r in rows:
        md += "| " + " | ".join(str(v) for v in r) + " |\n"
    return md + f"\nSQL: `{sql}`"


CARDS = [{"table": "bld_existing_boards"}, {"table": "bld_boards"}]
SQL = ('SELECT board, tcl FROM "bld_existing_boards" WHERE board IN (\'A\', \'B\') '
       "ORDER BY tcl DESC")
ROWS = [["A", 100.0], ["B", 90.0]]
Q = "Which board supplies Block B and which supplies Block C?"


def issues(sql=SQL, rows=ROWS, question=Q, cards=CARDS):
    return sql_loop.inspect_result(table_result(["board", "tcl"], rows, sql), question, cards)


def hit(found):
    return next((i for i in found if i.kind == KIND), None)


# ===========================================================================
print("1. A present-tense question stuck on the historical twin, with the current one routed "
     "(RED)")
# ===========================================================================
i1 = hit(issues())
check("RED: the issue is raised", i1 is not None, [i.kind for i in issues()])
check("RED: it names the historical table it read", i1 is not None
      and '"bld_existing_boards"' in i1.instruction, i1.instruction if i1 else "")
check("RED: and the current table it should read instead",
      i1 is not None and '"bld_boards"' in i1.instruction, i1.instruction if i1 else "")
check("RED: it quotes the previous SQL", i1 is not None and SQL in i1.instruction,
      i1.instruction if i1 else "")
check("RED: and the loop can act on it",
      sql_loop.first_requery_issue(issues()) is not None
      and sql_loop.first_requery_issue(issues()).kind == KIND)
check("RED: ranked right after LITERAL_ELSEWHERE, ahead of the column-shaped issues",
      KIND in sql_loop.ISSUE_ORDER
      and sql_loop.ISSUE_ORDER.index(KIND)
      == sql_loop.ISSUE_ORDER.index(sql_loop.LITERAL_ELSEWHERE) + 1
      and sql_loop.ISSUE_ORDER.index(KIND) < sql_loop.ISSUE_ORDER.index(
          sql_loop.IDENTIFIER_MISSING))

# ===========================================================================
print("\n2. Silent shapes - no issue raised")
# ===========================================================================
check("the question says 'existing' on purpose",
      hit(issues(question="Which board supplied Block B in the existing arrangement?")) is None)
check("...'before'", hit(issues(question="Which board supplied Block B before?")) is None)
check("...'pre-takeover'",
      hit(issues(question="Which board supplied Block B pre-takeover?")) is None)
check("...'original'", hit(issues(question="Which was the original board for Block B?")) is None)
check("...'historical'",
      hit(issues(question="Looking at the historical records, which board fed Block B?")) is None)
check("'beforehand' is not 'before' - word-bounded",
      hit(issues(question="Which board fed it beforehand and now?")) is not None)

check("the current twin was NOT routed - nothing to switch to", hit(issues(
    cards=[{"table": "bld_existing_boards"}])) is None)
check("no routed cards at all", hit(issues(cards=[])) is None)

EMPTY_TEXT = f"Query returned no results.\n\nSQL: `{SQL}`"
check("an EMPTY result: EMPTY's own re-query runs first, this stays silent",
      hit(sql_loop.inspect_result(EMPTY_TEXT, Q, CARDS)) is None)
FAILED_TEXT = f"SQL query failed: Binder Error\n\nSQL: `{SQL}`"
check("a FAILED result: no table is reliably read, this stays silent",
      hit(sql_loop.inspect_result(FAILED_TEXT, Q, CARDS)) is None)

NON_TWIN_SQL = 'SELECT board, tcl FROM "bld_boards" WHERE board IN (\'A\', \'B\') ORDER BY tcl DESC'
check("the SQL already reads the CURRENT table - nothing to switch",
      hit(issues(sql=NON_TWIN_SQL)) is None)

# ===========================================================================
print("\n3. _twin_name agrees with sql_tool._current_twin on every input (never imported - "
     "that would be circular)")
# ===========================================================================
for name in ("bld_existing_boards", "bld_boards", "bld_wing_existing_fixture_log", "",
            None, "existing_only", "a_existing_b_existing_c"):
    check(f"agree on {name!r}", sql_loop._twin_name(name) == sql_tool._current_twin(name),
          (sql_loop._twin_name(name), sql_tool._current_twin(name)))

# ===========================================================================
print("\n4. End to end through the loop - reaches the current table in a third step")
# ===========================================================================
EXISTING_SQL = ('SELECT board, tcl FROM "bld_existing_boards" WHERE board IN (\'A\', \'B\') '
               "AND block = 'B' ORDER BY tcl DESC")
STEP2_SQL = 'SELECT board, tcl FROM "bld_existing_boards" WHERE board IN (\'A\', \'B\') ORDER BY tcl DESC'
STEP3_SQL = 'SELECT board, tcl FROM "bld_boards" WHERE board IN (\'A\', \'B\') ORDER BY tcl DESC'
RESPONSES = {
    1: f"Query returned no results.\n\nSQL: `{EXISTING_SQL}`",
    2: table_result(["board", "tcl"], ROWS, STEP2_SQL),
    3: table_result(["board", "tcl"], [["A", 70.0], ["B", 65.0]], STEP3_SQL),
}
calls = []


def fake_execute(ask, user_id, sb):
    calls.append(ask)
    return RESPONSES[len(calls)]


final = sql_loop.run_sql_investigation(
    Q, "u-1", None, execute=fake_execute, search=None, routed_cards=CARDS, max_steps=3)
check("RED: three steps ran", len(calls) == 3, len(calls))
check("RED: step 3's ask carries the CURRENT_TWIN instruction naming the current table",
      len(calls) == 3 and '"bld_boards"' in calls[2], calls[-1] if calls else "")
check("RED: the final result is step 3's - the current table's own figures",
      final is not None and "70.0" in final.result_text and "65.0" in final.result_text,
      final.result_text if final else "")
check("RED: CURRENT_TWIN is recorded in the investigation's own issue trail",
      final is not None and KIND in final.issues, final.issues if final else "")

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
