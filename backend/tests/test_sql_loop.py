"""test_sql_loop.py — the bounded SQL verifying loop (spec 2026-09-23 §3, task 3).

One-shot SQL is the residual failure: the writer picks too few columns, the wrong filter,
or a filter that matches nothing, and nothing looks twice. `app/services/sql_loop.py`
inspects the result text with CODE ONLY (never by asking a model "is this right"), takes
the first issue it can act on, re-queries with a deterministic instruction, and stops at a
hard bound.

Every fixture here is synthetic: `bld_*` tables and made-up columns. The module under test
must contain no table, column value, system or building name at all — section 9 scans its
source for exactly that, and for the `while` the spec forbids.

Reading recorded for the record (section 7): the brief's "max_steps=1 -> exactly one
execute call and no search" is about the loop's own extra calls (the re-query and the count
cross-check). Spec §4 binds `SQL_LOOP_MAX_STEPS=1` to "byte-identical to today", and today
an empty SQL result still falls back to the document index — so that fallback still runs at
max_steps=1, and section 7b pins it.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import sql_loop

FAILS = []
def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond: FAILS.append(name)


# --------------------------------------------------------------------------- helpers
def table_result(cols, rows, sql, extra=""):
    """A markdown result exactly as `sql_tool.execute_sql_query` formats one."""
    md = "| " + " | ".join(cols) + " |\n"
    md += "| " + " | ".join(["---"] * len(cols)) + " |\n"
    for r in rows:
        md += "| " + " | ".join("" if v is None else str(v) for v in r) + " |\n"
    if extra:
        md += "\n" + extra + "\n"
    md += f"\nSQL: `{sql}`"
    return md


def empty_result(sql):
    return f"Query returned no results.\n\nSQL: `{sql}`"


CARD_LIST = {
    "table": "bld_units",
    "columns": ["unit_tag", "level_code", "kind", "notes"],
    "identifier_column": "unit_tag",
    "holds": "one row per unit",
    "caveats": [],
}
CARD_WIDE = {
    "table": "bld_assets",
    "columns": ["asset_tag", "model", "make", "rating", "voltage",
                "phase", "mounting", "finish", "source_page"],
    "identifier_column": "asset_tag",
    "holds": "one row per asset",
    "caveats": [],
}
CARD_SMALL = {
    "table": "bld_small",
    "columns": ["item_tag", "value", "source_page"],
    "identifier_column": "item_tag",
    "holds": "one row per item",
    "caveats": [],
}
CARD_NO_IDENT = {
    "table": "bld_shaped",
    "columns": ["thing_name", "colour", "notes"],
    "identifier_column": None,
    "holds": "one row per thing",
    "caveats": [],
}


class FakeExec:
    """Scripted `execute(question, user_id, sb) -> str`, recording every question."""
    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.questions = []

    def __call__(self, question, user_id, sb):
        self.questions.append(question)
        assert self.scripted, "the loop ran more queries than the script allows"
        return self.scripted.pop(0)


class FakeSearch:
    def __init__(self, text="EXCERPT-A\n\n---\n\nEXCERPT-B"):
        self.text = text
        self.queries = []

    def __call__(self, query):
        self.queries.append(query)
        return self.text


# ---------------------------------------------------------------------------
print("1. An empty result is EMPTY, and its instruction says what to do instead")
# ---------------------------------------------------------------------------
sql1 = 'SELECT * FROM "bld_units" WHERE unit_id = \'L1-B-07\''
issues = sql_loop.inspect_result(empty_result(sql1), "what is in unit 7?", [CARD_LIST])
kinds = [i.kind for i in issues]
check("EMPTY is raised", sql_loop.EMPTY in kinds, kinds)
e = next((i for i in issues if i.kind == sql_loop.EMPTY), None)
check("the instruction says the query returned no rows", e is not None and "no rows" in e.instruction,
      e.instruction if e else "")
check("the instruction names ILIKE as the way to filter by printed name",
      e is not None and "ILIKE" in e.instruction, e.instruction if e else "")
check("the instruction quotes the SQL that returned nothing",
      e is not None and sql1 in e.instruction, e.instruction if e else "")
check("EMPTY is a re-query issue", sql_loop.first_requery_issue(issues) is not None
      and sql_loop.first_requery_issue(issues).kind == sql_loop.EMPTY)
check("requery_instruction returns the issue's instruction",
      e is not None and sql_loop.requery_instruction(e) == e.instruction)

# a single 0, an all-NULL row: also empty, via the duplicated check
check("a lone 0 counts as empty", sql_loop.result_is_empty(table_result(["n"], [[0]], "s")))
check("an all-NULL row counts as empty", sql_loop.result_is_empty(table_result(["a", "b"], [[None, None]], "s")))
check("a 0 among real values is NOT empty", not sql_loop.result_is_empty(table_result(["a", "b"], [[0, 5]], "s")))

# ---------------------------------------------------------------------------
print("\n2. A list question whose result has no identifier column")
# ---------------------------------------------------------------------------
q2 = "list all the units on level 1"
sql2 = 'SELECT level_code, kind FROM "bld_units" WHERE level_code = \'L1\''
r2 = table_result(["level_code", "kind"], [["L1", "a"], ["L1", "b"]], sql2)
issues = sql_loop.inspect_result(r2, q2, [CARD_LIST])
im = next((i for i in issues if i.kind == sql_loop.IDENTIFIER_MISSING), None)
check("IDENTIFIER_MISSING is raised", im is not None, [i.kind for i in issues])
check("the instruction names the missing column", im is not None and "unit_tag" in im.instruction,
      im.instruction if im else "")
check("the instruction names the table", im is not None and "bld_units" in im.instruction,
      im.instruction if im else "")
check("the detail names the column too", im is not None and "unit_tag" in im.detail,
      im.detail if im else "")

# ---------------------------------------------------------------------------
print("\n3. The same result WITH the identifier column raises nothing")
# ---------------------------------------------------------------------------
r3 = table_result(["unit_tag", "level_code", "kind"],
                  [["U-1", "L1", "a"], ["U-2", "L1", "b"]],
                  'SELECT unit_tag, level_code, kind FROM "bld_units"')
check("no issues at all", sql_loop.inspect_result(r3, q2, [CARD_LIST]) == [],
      [i.kind for i in sql_loop.inspect_result(r3, q2, [CARD_LIST])])

# a card with no identifier_column falls back to column-name SHAPE (_name here)
r3b = table_result(["colour"], [["red"], ["blue"]], 'SELECT colour FROM "bld_shaped"')
issues = sql_loop.inspect_result(r3b, "list all the things", [CARD_NO_IDENT])
check("with no identifier_column the _name-shaped column is demanded",
      any(i.kind == sql_loop.IDENTIFIER_MISSING and "thing_name" in i.instruction for i in issues),
      [i.kind for i in issues])

# ---------------------------------------------------------------------------
print("\n4. A details question answered with 2 columns of a 9-column table")
# ---------------------------------------------------------------------------
q4 = "what are the specs of asset A-1?"
sql4 = 'SELECT asset_tag, model FROM "bld_assets" WHERE asset_tag = \'A-1\''
r4 = table_result(["asset_tag", "model"], [["A-1", "M-9"]], sql4)
issues = sql_loop.inspect_result(r4, q4, [CARD_WIDE])
ns = next((i for i in issues if i.kind == sql_loop.NARROW_SELECT), None)
check("NARROW_SELECT is raised", ns is not None, [i.kind for i in issues])
check("no IDENTIFIER_MISSING — the identifier IS in the result",
      not any(i.kind == sql_loop.IDENTIFIER_MISSING for i in issues), [i.kind for i in issues])
# Fix wave 1 (section 27): NARROW_SELECT is a FINDING - still detected, still reported,
# never acted on. The measurement that withdrew its instruction is quoted there.
check("it carries no re-query instruction", ns is not None and ns.instruction == "",
      ns.instruction if ns else "")
check("and its detail still says how narrow the selection was",
      ns is not None and "2" in ns.detail and "8" in ns.detail, ns.detail if ns else "")
check("source_page is not counted as a selectable column",
      "source_page" not in sql_loop.non_citation_columns(CARD_WIDE),
      sql_loop.non_citation_columns(CARD_WIDE))

# ---------------------------------------------------------------------------
print("\n5. A 2-column result from a 3-column table is NOT narrow")
# ---------------------------------------------------------------------------
r5 = table_result(["item_tag", "value"], [["I-1", "7"]],
                  'SELECT item_tag, value FROM "bld_small" WHERE item_tag = \'I-1\'')
check("no issues", sql_loop.inspect_result(r5, "what are the details of item I-1?", [CARD_SMALL]) == [],
      [i.kind for i in sql_loop.inspect_result(r5, "what are the details of item I-1?", [CARD_SMALL])])

# ---------------------------------------------------------------------------
print("\n6. A count question cross-checks and never re-queries")
# ---------------------------------------------------------------------------
q6 = "how many units are there?"
r6 = table_result(["n"], [[12]], 'SELECT COUNT(*) AS n FROM "bld_units"')
issues = sql_loop.inspect_result(r6, q6, [CARD_LIST])
check("COUNT_CROSSCHECK and nothing else", [i.kind for i in issues] == [sql_loop.COUNT_CROSSCHECK],
      [i.kind for i in issues])
check("it carries no re-query instruction", issues and issues[0].instruction == "", issues)
check("so the loop finds nothing to re-query", sql_loop.first_requery_issue(issues) is None)

# ---------------------------------------------------------------------------
print("\n7. A clean list result raises nothing; the guard fires on a shapeless truncation")
# ---------------------------------------------------------------------------
r7 = table_result(["unit_tag", "level_code", "kind"],
                  [["U-1", "L1", "a"], ["U-2", "L1", "b"]],
                  'SELECT unit_tag, level_code, kind FROM "bld_units"')
check("clean list result -> []", sql_loop.inspect_result(r7, "list all the units", [CARD_LIST]) == [])

trunc = r7.replace("\nSQL:", "\n*Showing 50 of 352 rows*\n\nSQL:")
issues = sql_loop.inspect_result(trunc, "list all the units", [CARD_LIST])
check("truncated with no RESULT SHAPE line -> TRUNCATED_NO_SHAPE",
      any(i.kind == sql_loop.TRUNCATED_NO_SHAPE for i in issues), [i.kind for i in issues])
check("and it is not a re-query", sql_loop.first_requery_issue(issues) is None)
withshape = r7.replace("\nSQL:", "\n*Showing 50 of 352 rows*\n\nRESULT SHAPE - rows: 352; shown: 50\n\nSQL:")
check("with a RESULT SHAPE line the guard is silent",
      not any(i.kind == sql_loop.TRUNCATED_NO_SHAPE
              for i in sql_loop.inspect_result(withshape, "list all the units", [CARD_LIST])))

# ---------------------------------------------------------------------------
print("\n8. No cards: the identifier issue can never fire")
# ---------------------------------------------------------------------------
check("empty card list -> no identifier issue",
      not any(i.kind == sql_loop.IDENTIFIER_MISSING for i in sql_loop.inspect_result(r2, q2, [])))
check("None card list -> no identifier issue",
      not any(i.kind == sql_loop.IDENTIFIER_MISSING for i in sql_loop.inspect_result(r2, q2, None)))
check("and no narrow issue either",
      sql_loop.inspect_result(r4, q4, []) == [], sql_loop.inspect_result(r4, q4, []))

# ---------------------------------------------------------------------------
print("\n9. Only the cards the SQL actually read are consulted")
# ---------------------------------------------------------------------------
cards_seen = sql_loop.cards_in_sql(sql2, [CARD_LIST, CARD_WIDE])
check("the card whose table is in the SQL", [c["table"] for c in cards_seen] == ["bld_units"],
      [c["table"] for c in cards_seen])
# Fix round 1 (F3): NEVER fall back to all routed cards. Attribution is exactly what is
# missing when the SQL names none of them, so naming a table the query never read is the
# one thing that must not happen here.
check("no table named in the SQL -> no cards at all",
      sql_loop.cards_in_sql("SELECT 1", [CARD_LIST, CARD_WIDE]) == [],
      sql_loop.cards_in_sql("SELECT 1", [CARD_LIST, CARD_WIDE]))

# ---------------------------------------------------------------------------
print("\n10. The question-shape regexes")
# ---------------------------------------------------------------------------
check("asks_to_list", all(sql_loop.asks_to_list(q) for q in
      ["list the units", "what are the units", "which units are on L1",
       "all the units", "show me the units", "give me the units"]))
check("asks_to_list is not fooled", not sql_loop.asks_to_list("how heavy is unit 7"))
check("asks_for_details", all(sql_loop.asks_for_details(q) for q in
      ["specs of A-1", "the specification of A-1", "details of A-1", "its attributes",
       "list the parameters", "breakdown of A-1", "everything about A-1"]))
check("asks_for_details is not fooled", not sql_loop.asks_for_details("list the units"))
check("asks_count", all(sql_loop.asks_count(q) for q in
      ["how many units", "the total load", "the number of units", "count the units"]))
check("asks_count is not fooled", not sql_loop.asks_count("list the units"))

# ---------------------------------------------------------------------------
print("\n11. run_sql_investigation: empty then good -> two steps, the good result wins")
# ---------------------------------------------------------------------------
good = table_result(["unit_tag", "level_code", "kind"],
                    [["U-1", "L1", "a"]], 'SELECT unit_tag, level_code, kind FROM "bld_units"')
ex = FakeExec([empty_result(sql2), good])
se = FakeSearch()
inv = sql_loop.run_sql_investigation(q2, "u1", None, execute=ex, search=se,
                                     routed_cards=[CARD_LIST], max_steps=3)
check("two SQL calls", len(ex.questions) == 2, ex.questions)
check("two steps recorded", len(inv.steps) == 2, inv.steps)
check("step 1 carries no issue", inv.steps[0]["issue"] is None, inv.steps[0])
check("step 2 is driven by EMPTY", inv.steps[1]["issue"] == sql_loop.EMPTY, inv.steps[1])
check("the final text is the good result", good in inv.result_text, inv.result_text[:120])
check("no document search happened", se.queries == [], se.queries)
# spec-fix10: the trailer counts the steps and names no issue kind - the kinds stay with
# the investigation (its `issues` and each step's events), never in the writer's text.
check("the trailer counts the steps and names no issue",
      inv.result_text.endswith("\n\nINVESTIGATION - steps: 2")
      and "issues:" not in inv.result_text, inv.result_text[-160:])
check("the issue that drove step 2 is still on the investigation itself",
      inv.issues == [sql_loop.EMPTY], inv.issues)

expected_q = (q2 + "\n(Investigation step 2: "
              + sql_loop.inspect_result(empty_result(sql2), q2, [CARD_LIST])[0].instruction
              + " Previous SQL: `" + sql2 + "`)")
check("the instruction was appended to the SECOND question, with the previous SQL",
      ex.questions[1] == expected_q, repr(ex.questions[1]))
check("the first question was untouched", ex.questions[0] == q2, repr(ex.questions[0]))

# ---------------------------------------------------------------------------
print("\n12. Empty twice: the repeat guard stops it, then the document fallback")
# ---------------------------------------------------------------------------
# Fix round 1 (F13): the same issue kind twice in a row means the instruction did not work,
# so a third identical instruction is a wasted step. The brief's original expectation was
# 3 steps here; the repeat guard supersedes it, and the cap itself is pinned below with a
# script whose issue kind CHANGES between steps.
ex = FakeExec([empty_result(sql2)] * 3)
se = FakeSearch("DOCTEXT")
inv = sql_loop.run_sql_investigation(q2, "u1", None, execute=ex, search=se,
                                     routed_cards=[CARD_LIST], max_steps=3)
check("the repeat guard stops after two SQL calls", len(ex.questions) == 2, len(ex.questions))
check("two steps recorded", len(inv.steps) == 2, inv.steps)
check("one document search, with the question", se.queries == [q2], se.queries)
check("the fallback text replaces the empty result",
      sql_loop.EMPTY_FALLBACK_PREFIX in inv.result_text and "DOCTEXT" in inv.result_text,
      inv.result_text[:200])
check("the fallback keeps today's do-not-guess closing line",
      sql_loop.EMPTY_FALLBACK_SUFFIX in inv.result_text, inv.result_text[-300:])
check("the trailer counts the steps", inv.result_text.endswith("INVESTIGATION - steps: 2"),
      inv.result_text[-160:])
check("the answer text now says it came from the documents",
      inv.source_tool == "search_documents", inv.source_tool)

# 12b — the CAP itself, with a different issue kind at each step so the repeat guard cannot
# be what stops it: empty -> a list with no identifier -> the same, cut off by max_steps.
# TWO rows: fix wave 1 made a ONE-row result the answer rather than a list missing its
# labels, so a one-row fixture would no longer raise IDENTIFIER_MISSING and could not pin
# the cap. Nothing else about the case changed.
noident = table_result(["level_code", "kind"], [["L1", "a"], ["L1", "b"]],
                       'SELECT level_code, kind FROM "bld_units"')
ex = FakeExec([empty_result(sql2), noident, noident])
inv = sql_loop.run_sql_investigation(q2, "u1", None, execute=ex, search=FakeSearch(),
                                     routed_cards=[CARD_LIST], max_steps=3)
check("exactly three SQL calls — max_steps is the bound", len(ex.questions) == 3, len(ex.questions))
check("the issues acted on, in order", [s["issue"] for s in inv.steps]
      == [None, sql_loop.EMPTY, sql_loop.IDENTIFIER_MISSING], [s["issue"] for s in inv.steps])

# a search that finds nothing leaves the SQL outcome standing
ex = FakeExec([empty_result(sql2)] * 3)
inv = sql_loop.run_sql_investigation(q2, "u1", None, execute=ex, search=FakeSearch(""),
                                     routed_cards=[CARD_LIST], max_steps=3)
check("no excerpts -> the empty SQL result is kept", "Query returned no results" in inv.result_text,
      inv.result_text[:120])

# ---------------------------------------------------------------------------
print("\n13. max_steps=1 is today's behaviour: one call, no loop, no cross-check, no trailer")
# ---------------------------------------------------------------------------
ex = FakeExec([good])
se = FakeSearch()
inv = sql_loop.run_sql_investigation(q2, "u1", None, execute=ex, search=se,
                                     routed_cards=[CARD_LIST], max_steps=1)
check("exactly one execute call", len(ex.questions) == 1, ex.questions)
check("no search", se.queries == [], se.queries)
check("the result text is byte-identical to the SQL result", inv.result_text == good, inv.result_text[:120])
check("no INVESTIGATION trailer", "INVESTIGATION" not in inv.result_text)

# a count question at max_steps=1 must not cross-check either
ex = FakeExec([r6])
se = FakeSearch()
inv = sql_loop.run_sql_investigation(q6, "u1", None, execute=ex, search=se,
                                     routed_cards=[CARD_LIST], max_steps=1)
check("a count question does not cross-check at max_steps=1", se.queries == [], se.queries)
check("and its text is unchanged", inv.result_text == r6)
check("crosscheck is None", inv.crosscheck is None)

# 13b — but the empty->documents fallback IS today's behaviour, so it still runs
ex = FakeExec([empty_result(sql2)])
se = FakeSearch("DOCTEXT")
inv = sql_loop.run_sql_investigation(q2, "u1", None, execute=ex, search=se,
                                     routed_cards=[CARD_LIST], max_steps=1)
check("one execute call only", len(ex.questions) == 1, ex.questions)
check("the empty result still falls back to the documents (today's behaviour)",
      se.queries == [q2] and "DOCTEXT" in inv.result_text, (se.queries, inv.result_text[:120]))
check("and still no trailer at max_steps=1", "INVESTIGATION" not in inv.result_text)

# ---------------------------------------------------------------------------
print("\n14. A failed query falls back to the documents - unchanged at max_steps=1")
# ---------------------------------------------------------------------------
failed = "SQL query failed: Binder Error: no such column\n\nGenerated SQL: `SELECT nope FROM \"bld_units\"`"
ex = FakeExec([failed])
se = FakeSearch("DOCTEXT")
inv = sql_loop.run_sql_investigation(q2, "u1", None, execute=ex, search=se,
                                     routed_cards=[CARD_LIST], max_steps=1)
check("with the loop off, a failure is not re-queried", len(ex.questions) == 1, ex.questions)
check("it searches the documents", se.queries == [q2], se.queries)
check("the excerpts replace the error text, with no empty-result preamble",
      "DOCTEXT" in inv.result_text and sql_loop.EMPTY_FALLBACK_PREFIX not in inv.result_text,
      inv.result_text[:200])
check("result_is_failure recognises it", sql_loop.result_is_failure(failed))
check("and a normal result is not a failure", not sql_loop.result_is_failure(good))

# ---------------------------------------------------------------------------
print("\n15. The count cross-check: one retrieval call, appended, never a re-query")
# ---------------------------------------------------------------------------
ex = FakeExec([r6])
se = FakeSearch("EXA\n\n---\n\nEXB\n\n---\n\nEXC\n\n---\n\nEXD")
inv = sql_loop.run_sql_investigation(q6, "u1", None, execute=ex, search=se,
                                     routed_cards=[CARD_LIST], max_steps=3)
check("only one SQL call — a count is never re-queried", len(ex.questions) == 1, ex.questions)
check("exactly one retrieval call", len(se.queries) == 1, se.queries)
check("the SQL result is still there", r6 in inv.result_text, inv.result_text[:120])
check("the excerpts are labelled as a cross-check",
      sql_loop.CROSSCHECK_HEADING in inv.result_text, inv.result_text[-400:])
# Fix round 1 (Task 4 review, finding I-2): the heading used to read "Other
# records that state this quantity:", which over-claims. These excerpts are the
# top hits of an unfiltered keyword search — they need not state any quantity at
# all, and a writer trusting the old heading could present an unrelated number as
# a rival count. Pinned literally, not only symbolically, so the wording cannot
# drift back without turning a check red.
check("the heading says the excerpts MENTION the quantity and may be unrelated",
      sql_loop.CROSSCHECK_HEADING == "Cross-check: document excerpts that mention "
      "this quantity (top matches, may be unrelated)", sql_loop.CROSSCHECK_HEADING)
check("and it never claims they STATE it",
      "state this quantity" not in sql_loop.CROSSCHECK_HEADING, sql_loop.CROSSCHECK_HEADING)
check("at most three excerpts are kept",
      "EXC" in inv.result_text and "EXD" not in inv.result_text, inv.result_text[-400:])
check("Investigation.crosscheck carries the excerpt text", inv.crosscheck and "EXA" in inv.crosscheck,
      inv.crosscheck)
check("the trailer counts the step and names no issue; the cross-check stays on the "
      "investigation (spec-fix10)",
      inv.result_text.endswith("\n\nINVESTIGATION - steps: 1")
      and sql_loop.COUNT_CROSSCHECK not in inv.result_text
      and sql_loop.COUNT_CROSSCHECK in inv.issues, inv.result_text[-160:])

# no search callable at all -> degrade quietly
ex = FakeExec([r6])
inv = sql_loop.run_sql_investigation(q6, "u1", None, execute=ex, search=None,
                                     routed_cards=[CARD_LIST], max_steps=3)
check("no search callable -> no cross-check, no crash", inv.crosscheck is None
      and sql_loop.CROSSCHECK_HEADING not in inv.result_text)

# ---------------------------------------------------------------------------
print("\n16. The event sequence from iter_sql_investigation")
# ---------------------------------------------------------------------------
ex = FakeExec([empty_result(sql2), good])
events = list(sql_loop.iter_sql_investigation(q2, "u1", None, execute=ex, search=FakeSearch(),
                                              routed_cards=[CARD_LIST], max_steps=3))
check("kinds in order: step/step_done per query, then final",
      [k for k, _ in events] == ["step", "step_done", "step", "step_done", "final"],
      [k for k, _ in events])
check("step 1 payload", events[0][1]["step"] == 1 and events[0][1]["issue"] is None
      and events[0][1]["sql"] == "", events[0][1])
check("step 2 payload names the issue and the SQL it is reacting to",
      events[2][1]["step"] == 2 and events[2][1]["issue"] == sql_loop.EMPTY
      and events[2][1]["sql"] == sql2, events[2][1])
check("every step event carries a human detail",
      all(isinstance(p.get("detail"), str) and p["detail"] for k, p in events if k == "step"),
      [p for k, p in events if k == "step"])
check("the final payload is an Investigation", isinstance(events[-1][1], sql_loop.Investigation))
check("the events' step payloads are the Investigation's steps",
      [p for k, p in events if k == "step"] == events[-1][1].steps)

ex = FakeExec([r6])
events = list(sql_loop.iter_sql_investigation(q6, "u1", None, execute=ex, search=FakeSearch(),
                                              routed_cards=[CARD_LIST], max_steps=3))
check("a count question: step, step_done, crosscheck, final",
      [k for k, _ in events] == ["step", "step_done", "crosscheck", "final"], [k for k, _ in events])
check("the crosscheck event names its kind",
      events[2][1].get("kind") == sql_loop.COUNT_CROSSCHECK, events[2][1])

ex = FakeExec([empty_result(sql2)] * 3)
events = list(sql_loop.iter_sql_investigation(q2, "u1", None, execute=ex, search=FakeSearch(),
                                              routed_cards=[CARD_LIST], max_steps=3))
check("two empties, then the fallback retrieval, then final",
      [k for k, _ in events] == ["step", "step_done", "step", "step_done", "crosscheck", "final"],
      [k for k, _ in events])
check("the fallback retrieval event is marked EMPTY, not COUNT_CROSSCHECK",
      events[4][1].get("kind") == sql_loop.EMPTY, events[4][1])

# ---------------------------------------------------------------------------
print("\n17. result_is_empty is byte-for-byte the check openai_client already ships")
# ---------------------------------------------------------------------------
from app.services.openai_client import _sql_result_is_empty
CASES = [
    "Query returned no results.\n\nSQL: `SELECT 1`",
    table_result(["n"], [[0]], "s"),
    table_result(["a", "b"], [[None, None]], "s"),
    table_result(["unit_tag", "level_code"], [["U-1", "L1"], ["U-2", "L1"]], "s"),
    table_result(["a", "b"], [[0, 5]], "s"),
]
for i, c in enumerate(CASES):
    check(f"case {i + 1} agrees ({sql_loop.result_is_empty(c)})",
          sql_loop.result_is_empty(c) == _sql_result_is_empty(c),
          f"{sql_loop.result_is_empty(c)} vs {_sql_result_is_empty(c)}")

# ---------------------------------------------------------------------------
print("\n18. The module is generic by construction, and bounded by construction")
# ---------------------------------------------------------------------------
import re as _re
SRC = Path(sql_loop.__file__).read_text(encoding="utf-8")
for tok in ("hwu", "room", "panel", "camera", "door", "fcu", "location_id"):
    hits = len(_re.findall(tok, SRC, _re.IGNORECASE))
    check(f"no '{tok}' anywhere in the module", hits == 0, f"{hits} hits")
check("no `while` — the loop is `for step in range(max_steps)`",
      not _re.search(r"\bwhile\b", SRC))
check("the bound is written as a range over max_steps",
      "for step in range(max_steps)" in SRC)

# ===========================================================================
# FIX ROUND 1 — the review of commit 3e5f71e (C1, F1-F6, minors). Every check
# below was written and watched fail before the module changed.
# ===========================================================================

CARD_PLACES = {   # the shape of a place table: an internal key AND a printed number
    "table": "bld_places",
    "columns": ["place_id", "place_number", "place_name", "area_m2", "department"],
    "identifier_column": "place_id",
}
CARD_SPECS = {    # a one-row-per-(entity, parameter, value) table: its "identifier" is a value
    "table": "bld_specs",
    "columns": ["source_page", "subject", "parameter", "value"],
    "identifier_column": "value",
}
CARD_SPACED = {   # an identifier whose printed name contains a space
    "table": "bld_spaced",
    "columns": ["S.N", "Name", "Serial number", "Description", "x1", "x2"],
    "identifier_column": "Serial number",
}

# ---------------------------------------------------------------------------
print("\n19. C1 — an aggregate result is never treated as an entity list")
# ---------------------------------------------------------------------------
q19 = "give me the breakdown of assets by kind"
grouped = table_result(["kind", "count"], [["a", 3], ["b", 2], ["c", 9], ["d", 1],
                                           ["e", 5], ["f", 4], ["g", 7], ["h", 6]],
                       'SELECT kind, COUNT(*) AS count FROM "bld_assets" GROUP BY kind')
check("a GROUP BY breakdown raises nothing", sql_loop.inspect_result(grouped, q19, [CARD_WIDE]) == [],
      [i.kind for i in sql_loop.inspect_result(grouped, q19, [CARD_WIDE])])
check("sql_is_aggregate sees GROUP BY",
      sql_loop.sql_is_aggregate('SELECT kind, COUNT(*) FROM "t" GROUP BY kind'))
check("sql_is_aggregate sees a bare aggregate call in the SELECT list",
      sql_loop.sql_is_aggregate('SELECT SUM(load_kw) AS total FROM "t"'))
check("but NOT an aggregate that is only in a subquery of the WHERE clause",
      not sql_loop.sql_is_aggregate(
          'SELECT asset_tag, model FROM "bld_assets" WHERE rating > (SELECT AVG(rating) FROM "bld_assets")'))
# and the non-aggregate cases still behave
sub = table_result(["asset_tag", "model"], [["A-1", "M-9"]],
                   'SELECT asset_tag, model FROM "bld_assets" WHERE rating > (SELECT AVG(rating) FROM "bld_assets")')
check("a plain list query is still inspected",
      any(i.kind == sql_loop.NARROW_SELECT for i in sql_loop.inspect_result(sub, q4, [CARD_WIDE])),
      [i.kind for i in sql_loop.inspect_result(sub, q4, [CARD_WIDE])])
check("a clean 4-column list is still clean", sql_loop.inspect_result(
    table_result(["unit_tag", "level_code", "kind", "notes"], [["U-1", "L1", "a", ""]],
                 'SELECT * FROM "bld_units"'), "list all the units", [CARD_LIST]) == [])

# ---------------------------------------------------------------------------
print("\n20. F1/F2 — what makes a list nameable, and which column it is asked for")
# ---------------------------------------------------------------------------
q20 = "list all the places on level 1"
# (a2) the writer complied with LIST_IDENTIFIER_RULE by selecting the printed number
a2 = table_result(["place_number", "place_name", "area_m2"], [["1.01", "Studio", 30]],
                  'SELECT place_number, place_name, area_m2 FROM "bld_places"')
check("a result carrying a _number column is nameable — no issue",
      sql_loop.inspect_result(a2, q20, [CARD_PLACES]) == [],
      [i.kind for i in sql_loop.inspect_result(a2, q20, [CARD_PLACES])])
# (a) THE OWNER'S OWN CASE, 2026-09-23: a list of places with names and no numbers. Fix
# round 2 ruling — a printed name is a LABEL, not an identifier a person can act on, so
# "_name" is not in NAMEABLE_SUFFIXES and this list is re-queried for its number.
a1 = table_result(["place_name", "area_m2"], [["Studio", 30], ["Store", 12]],
                  'SELECT place_name, area_m2 FROM "bld_places"')
iss_a1 = sql_loop.inspect_result(a1, q20, [CARD_PLACES])
im_a1 = next((i for i in iss_a1 if i.kind == sql_loop.IDENTIFIER_MISSING), None)
check("a list of names with no number is NOT nameable — it fires", im_a1 is not None,
      [i.kind for i in iss_a1])
check("and it asks for the printed number", im_a1 is not None and "`place_number`" in im_a1.instruction,
      im_a1.instruction if im_a1 else "")
check("a _name column alone does not make a list nameable",
      not sql_loop.result_is_nameable(["place_name", "area_m2"], [CARD_PLACES]))
check("a _number, an _id or a _tag does",
      all(sql_loop.result_is_nameable([c], [CARD_PLACES])
          for c in ("place_number", "other_id", "thing_tag")))
check("and so does the card's own declared identifier",
      sql_loop.result_is_nameable(["place_id", "area_m2"], [CARD_PLACES]))
# (a3) nothing nameable at all: it fires, and asks for the PRINTED number, not the key
a3 = table_result(["area_m2", "department"], [[30, "X"], [12, "Y"]],
                  'SELECT area_m2, department FROM "bld_places"')
iss20 = sql_loop.inspect_result(a3, q20, [CARD_PLACES])
im20 = next((i for i in iss20 if i.kind == sql_loop.IDENTIFIER_MISSING), None)
check("with nothing nameable it fires", im20 is not None, [i.kind for i in iss20])
check("and asks for the printed number, never the internal key",
      im20 is not None and "`place_number`" in im20.instruction and "place_id" not in im20.instruction,
      im20.instruction if im20 else "")
# (b) a join: the driving table IS identified, so the value-table's "identifier" is not demanded
b = table_result(["asset_tag", "spec_value"], [["A-1", "2MP"]],
                 'SELECT a.asset_tag, s.value AS spec_value FROM "bld_assets" a '
                 'JOIN "bld_specs" s ON s.subject = a.asset_tag')
iss_b = sql_loop.inspect_result(b, "list the specs of all assets", [CARD_WIDE, CARD_SPECS])
check("the spec join raises NARROW_SELECT, not IDENTIFIER_MISSING",
      [i.kind for i in iss_b] == [sql_loop.NARROW_SELECT], [i.kind for i in iss_b])
# F7 — an identifier with a space is quoted so the re-query is valid SQL
sp = table_result(["Name", "Description"], [["n", "d"], ["n2", "d2"]],
                  'SELECT "Name", "Description" FROM "bld_spaced"')
iss_sp = sql_loop.inspect_result(sp, "list all the things", [CARD_SPACED])
im_sp = next((i for i in iss_sp if i.kind == sql_loop.IDENTIFIER_MISSING), None)
check("an identifier containing a space is double-quoted, not left in backticks alone",
      im_sp is not None and '"Serial number"' in im_sp.instruction, im_sp.instruction if im_sp else "")

# ---------------------------------------------------------------------------
print("\n21. F4 — the priority order, pinned by a result that raises two issues")
# ---------------------------------------------------------------------------
q21 = "list the details of every asset"
both = table_result(["model", "make"], [["M-9", "K"], ["M-8", "J"]],
                    'SELECT model, make FROM "bld_assets"')
iss21 = sql_loop.inspect_result(both, q21, [CARD_WIDE])
check("both issues are raised", len(iss21) == 2, [i.kind for i in iss21])
check("IDENTIFIER_MISSING before NARROW_SELECT",
      [i.kind for i in iss21] == [sql_loop.IDENTIFIER_MISSING, sql_loop.NARROW_SELECT],
      [i.kind for i in iss21])
check("and the loop acts on IDENTIFIER_MISSING",
      sql_loop.first_requery_issue(iss21).kind == sql_loop.IDENTIFIER_MISSING)
# EMPTY outranks both — an all-NULL row still shows which columns were selected
both_empty = table_result(["model", "make"], [[None, None], [None, None]],
                          'SELECT model, make FROM "bld_assets"')
iss21b = sql_loop.inspect_result(both_empty, q21, [CARD_WIDE])
check("EMPTY comes first of the three",
      [i.kind for i in iss21b] == [sql_loop.EMPTY, sql_loop.IDENTIFIER_MISSING, sql_loop.NARROW_SELECT],
      [i.kind for i in iss21b])
check("and the loop acts on EMPTY",
      sql_loop.first_requery_issue(iss21b).kind == sql_loop.EMPTY)

# ---------------------------------------------------------------------------
print("\n22. F3 — a SQL written across lines, and a SQL naming no routed table")
# ---------------------------------------------------------------------------
multi = ("| place_name | area_m2 |\n| --- | --- |\n| Studio | 30 |\n\n"
         'SQL: `SELECT place_name,\n       area_m2\nFROM "bld_places"\nWHERE level_code = \'L1\'`')
check("result_sql captures a multi-line query",
      'FROM "bld_places"' in sql_loop.result_sql(multi), repr(sql_loop.result_sql(multi)))
check("so the right card is consulted",
      [c["table"] for c in sql_loop.cards_in_sql(sql_loop.result_sql(multi), [CARD_WIDE, CARD_PLACES])]
      == ["bld_places"])
# and when the SQL names none of the routed tables, the identifier issue degrades to silence
elsewhere = table_result(["area_m2", "department"], [[30, "X"], [12, "Y"]],
                         'SELECT area_m2, department FROM "other"')
check("a SQL naming no routed table raises no identifier issue",
      not any(i.kind == sql_loop.IDENTIFIER_MISSING
              for i in sql_loop.inspect_result(elsewhere, q20, [CARD_PLACES, CARD_WIDE])),
      [i.kind for i in sql_loop.inspect_result(elsewhere, q20, [CARD_PLACES, CARD_WIDE])])

# ---------------------------------------------------------------------------
print("\n23. F6 — every step reports its outcome, and the answer names its tool")
# ---------------------------------------------------------------------------
ex = FakeExec([empty_result(sql2), good])
events = list(sql_loop.iter_sql_investigation(q2, "u1", None, execute=ex, search=FakeSearch(),
                                              routed_cards=[CARD_LIST], max_steps=3))
dones = [p for k, p in events if k == "step_done"]
check("one step_done per step", len(dones) == 2, dones)
check("step_done 1 reports the empty outcome and what was found",
      dones[0]["step"] == 1 and dones[0]["empty"] is True
      and dones[0]["issues_found"] == [sql_loop.EMPTY], dones[0])
check("step_done 2 reports rows and no issues",
      dones[1]["step"] == 2 and dones[1]["empty"] is False
      and dones[1]["rows"] == 1 and dones[1]["issues_found"] == [], dones[1])
inv = events[-1][1]
check("source_tool says the answer came from the tables",
      inv.source_tool == "query_structured_data", inv.source_tool)
check("Investigation.issues lists what was found", inv.issues == [sql_loop.EMPTY], inv.issues)
boom = "SQL query failed: boom\n\nGenerated SQL: `SELECT 1`"
ex = FakeExec([boom, boom])   # the loop now tries again once; both fail
inv = sql_loop.run_sql_investigation(q2, "u1", None, execute=ex, search=FakeSearch("DOCTEXT"),
                                     routed_cards=[CARD_LIST], max_steps=3)
check("after a fallback, source_tool says search_documents",
      inv.source_tool == "search_documents", inv.source_tool)
check("a truncated result reports its TRUE row count",
      sql_loop.result_total_rows(trunc) == 352, sql_loop.result_total_rows(trunc))

# ---------------------------------------------------------------------------
print("\n24. F5 — the wall-clock guard reuses SQL_QUERY_TIMEOUT")
# ---------------------------------------------------------------------------
class FakeClock:
    def __init__(self, *times):
        self.times = list(times)
    def __call__(self):
        return self.times.pop(0) if len(self.times) > 1 else self.times[0]

# Fix round 2 ruling: the investigation has its OWN budget. SQL_QUERY_TIMEOUT stays the
# per-execution cap it always was (5 s) — using it here would have made the loop one-shot in
# production, because one SQL-generation call alone routinely outlives it.
import os as _os
_saved = _os.environ.pop("SQL_LOOP_TIMEOUT", None)
try:
    check("the default budget is 60 seconds", sql_loop.investigation_budget_seconds() == 60.0,
          sql_loop.investigation_budget_seconds())
    _os.environ["SQL_LOOP_TIMEOUT"] = "20"
    check("the env setting is read", sql_loop.investigation_budget_seconds() == 20.0,
          sql_loop.investigation_budget_seconds())
    _os.environ["SQL_LOOP_TIMEOUT"] = "soon"
    check("garbage falls back to the default", sql_loop.investigation_budget_seconds() == 60.0,
          sql_loop.investigation_budget_seconds())
    _os.environ["SQL_LOOP_TIMEOUT"] = "0"
    check("so does a value that would disable the loop outright",
          sql_loop.investigation_budget_seconds() == 60.0, sql_loop.investigation_budget_seconds())
finally:
    _os.environ.pop("SQL_LOOP_TIMEOUT", None)
    if _saved is not None:
        _os.environ["SQL_LOOP_TIMEOUT"] = _saved
check("and it is NOT the per-execution SQL_QUERY_TIMEOUT",
      sql_loop.investigation_budget_seconds() != 5.0)
ex = FakeExec([empty_result(sql2), good])
inv = sql_loop.run_sql_investigation(q2, "u1", None, execute=ex, search=FakeSearch("DOCTEXT"),
                                     routed_cards=[CARD_LIST], max_steps=3,
                                     clock=FakeClock(0.0, 99.0))
check("a step that blows the budget stops the loop", len(ex.questions) == 1, ex.questions)
check("and the empty result still reaches the document fallback", "DOCTEXT" in inv.result_text,
      inv.result_text[:120])
ex = FakeExec([empty_result(sql2), good])
inv = sql_loop.run_sql_investigation(q2, "u1", None, execute=ex, search=FakeSearch(),
                                     routed_cards=[CARD_LIST], max_steps=3,
                                     clock=FakeClock(0.0, 0.1))
check("inside the budget the loop runs on", len(ex.questions) == 2, ex.questions)

# ---------------------------------------------------------------------------
print("\n25. The remaining minors")
# ---------------------------------------------------------------------------
# F9 — a finding that is not a re-query still reaches the Investigation. (It used to reach
# the trailer too; since spec-fix10 the trailer counts the steps and names no issue kind.)
ex = FakeExec([trunc])
inv = sql_loop.run_sql_investigation("list all the units", "u1", None, execute=ex,
                                     search=FakeSearch(), routed_cards=[CARD_LIST], max_steps=3)
check("TRUNCATED_NO_SHAPE reaches Investigation.issues",
      sql_loop.TRUNCATED_NO_SHAPE in inv.issues, inv.issues)
check("and never the trailer the answer writer reads (spec-fix10)",
      "TRUNCATED_NO_SHAPE" not in inv.result_text
      and inv.result_text.endswith("INVESTIGATION - steps: 1"), inv.result_text[-160:])
check("without causing a re-query", len(ex.questions) == 1, ex.questions)

# NEW-1 (fix round 3) — the round-1 narrowing to single-cell/aggregate results silenced the
# cross-check on the shape the original review had validated as CORRECT: a quantity question
# whose SQL answers with a plain ROW LIST, the total readable only from the truncation or
# RESULT SHAPE line. That is this corpus's commonest quantity shape and the one the
# cross-check exists for (its disputed counts are all of this kind). The rule is now the
# spec's own: a count question whose tables answered gets one cross-check. Silence is for
# questions that do not ask a count — and for an empty result, which goes to the fallback.
f_case = table_result(["place_number", "place_name"], [[f"1.{i:02d}", "x"] for i in range(50)],
                      'SELECT place_number, place_name FROM "bld_places" WHERE level_code = \'L01\'',
                      "*Showing 50 of 121 rows*\n\nRESULT SHAPE - rows: 121; shown: 50")
iss_f = sql_loop.inspect_result(f_case, "how many places are on the first floor", [CARD_PLACES])
check("a count question answered by a row list IS cross-checked",
      [i.kind for i in iss_f] == [sql_loop.COUNT_CROSSCHECK], [i.kind for i in iss_f])
check("and it still causes no re-query", sql_loop.first_requery_issue(iss_f) is None)
# case h, the round-1 fix's own case: with the rule above it fires too, and that is wanted —
# "total load of block B" is a quantity question whatever shape the SQL answered in.
listy = table_result(["unit_tag", "kw"], [["U-1", 3], ["U-2", 4], ["U-3", 5], ["U-4", 6]],
                     'SELECT unit_tag, kw FROM "bld_units" WHERE block = \'B\'')
check("a total question answered by a list is cross-checked too",
      [i.kind for i in sql_loop.inspect_result(listy, "what is the total load of block B?", [CARD_LIST])]
      == [sql_loop.COUNT_CROSSCHECK],
      [i.kind for i in sql_loop.inspect_result(listy, "what is the total load of block B?", [CARD_LIST])])
check("but the same list under a question that asks no count is silent",
      not any(i.kind == sql_loop.COUNT_CROSSCHECK
              for i in sql_loop.inspect_result(listy, "which units are in block B?", [CARD_LIST])),
      [i.kind for i in sql_loop.inspect_result(listy, "which units are in block B?", [CARD_LIST])])
check("and an EMPTY result is not cross-checked — it goes to the document fallback",
      not any(i.kind == sql_loop.COUNT_CROSSCHECK for i in sql_loop.inspect_result(
          empty_result('SELECT COUNT(*) FROM "bld_units"'), "how many units are there?", [CARD_LIST])))
check("a single-cell total is cross-checked",
      any(i.kind == sql_loop.COUNT_CROSSCHECK for i in sql_loop.inspect_result(
          table_result(["total_kw"], [[1234.56]], 'SELECT SUM(kw) AS total_kw FROM "bld_units"'),
          "what is the total load of the site?", [CARD_LIST])))
check("and so is a GROUP BY count", any(i.kind == sql_loop.COUNT_CROSSCHECK
      for i in sql_loop.inspect_result(
          table_result(["level_code", "n"], [["L1", 4], ["L2", 9], ["L3", 2], ["L4", 7]],
                       'SELECT level_code, COUNT(*) AS n FROM "bld_units" GROUP BY level_code'),
          "how many units are on each level?", [CARD_LIST])))

# F10 — the search gets the USER's own wording, not the tool's paraphrase
ex = FakeExec([empty_result(sql2)] * 2)
se = FakeSearch("DOCTEXT")
inv = sql_loop.run_sql_investigation("paraphrased question", "u1", None, execute=ex, search=se,
                                     routed_cards=[CARD_LIST], max_steps=3,
                                     user_question="the user's own words")
check("search receives the original user question", se.queries == ["the user's own words"], se.queries)
check("and the SQL still got the tool's question", ex.questions[0] == "paraphrased question",
      ex.questions)

# F14 — the widening ask is bounded and says which rows
ns25 = next(i for i in sql_loop.inspect_result(both, q21, [CARD_WIDE])
            if i.kind == sql_loop.NARROW_SELECT)
# F14 asked that the widening instruction be bounded and say which rows. Fix wave 1
# withdrew the instruction entirely (section 27), so what is left to pin is that there is
# none - and that the finding still reports the two counts a reader would want.
check("NARROW_SELECT asks for nothing at all", ns25.instruction == "", ns25.instruction)
check("and its detail names the columns shown and the columns available",
      "2" in ns25.detail and "8" in ns25.detail, ns25.detail)

# ===========================================================================
# FIX WAVE 1 — the Task 5 diagnosis (2026-09-23). The loop was measured over 164
# real questions: its 44 re-queries bought 4 cards and lost 3. Each check below
# was written and watched fail before the module changed, and each one removes a
# firing the measurement showed paying nothing.
# ===========================================================================

# ---------------------------------------------------------------------------
print("\n26. W1 — _COUNT_RE reaches the plural forms real questions are asked in")
# ---------------------------------------------------------------------------
# Measured: two cards written FOR the cross-check never got one, because
# `\btotal\b` does not match "totals" and `\bcount\b` does not match "counts".
check("'counts' is a quantity question",
      sql_loop.asks_count("what are the FCU counts for each level?"))
check("'totals' is a quantity question",
      sql_loop.asks_count("what different door totals do the records print?"))
check("the singular forms still match", all(sql_loop.asks_count(q) for q in
      ["how many units", "the total load", "the number of units", "count the units"]))
# Deliberately NOT added, both from the diagnosis: "how much" is a rating/cost
# question, and a bare "numbers" is how this corpus asks for serials and part
# numbers — cross-checking either buys a retrieval call and no rival quantity.
check("'how much load' is still NOT a count question",
      not sql_loop.asks_count("how much load does the fourth floor draw?"))
check("a serial-number question is still NOT a count question",
      not sql_loop.asks_count("what are the serial numbers of the UPS units?"))
check("and neither is a refuse-shaped question naming model numbers",
      not sql_loop.asks_count("do the records give the model numbers of the pumps?"))

# ---------------------------------------------------------------------------
print("\n27. W2 — NARROW_SELECT is a FINDING: reported, never re-queried")
# ---------------------------------------------------------------------------
# Measured: it fired 3 times in 164 questions for 0 wins and 1 loss — it replaced
# a COMPLETE key/value answer with a narrower one, because the loop keeps the LAST
# result and has no "keep the better one" rule. It stays detected and reported.
q27 = "what are the specs of every asset?"
sql27 = ('SELECT a.asset_tag, s.value AS spec_value FROM "bld_assets" a '
         'JOIN "bld_specs" s ON s.subject = a.asset_tag')
r27 = table_result(["asset_tag", "spec_value"], [["A-1", "2MP"], ["A-2", "4MP"]], sql27)
iss27 = sql_loop.inspect_result(r27, q27, [CARD_WIDE, CARD_SPECS])
check("NARROW_SELECT is still detected", [i.kind for i in iss27] == [sql_loop.NARROW_SELECT],
      [i.kind for i in iss27])
check("but it carries no instruction", iss27 and iss27[0].instruction == "",
      [i.instruction for i in iss27])
check("so the loop finds nothing to act on", sql_loop.first_requery_issue(iss27) is None)
ex = FakeExec([r27, r27])   # a spare, so an unwanted re-query is COUNTED, not an abort
inv = sql_loop.run_sql_investigation(q27, "u1", None, execute=ex, search=FakeSearch(),
                                     routed_cards=[CARD_WIDE, CARD_SPECS], max_steps=3)
check("exactly one SQL call — the complete result is not replaced",
      len(ex.questions) == 1, ex.questions)
check("the finding still reaches Investigation.issues",
      inv.issues == [sql_loop.NARROW_SELECT], inv.issues)
check("and never the trailer the answer writer reads (spec-fix10)",
      "NARROW_SELECT" not in inv.result_text
      and inv.result_text.endswith("INVESTIGATION - steps: 1"), inv.result_text[-160:])

# ---------------------------------------------------------------------------
print("\n28. W3 — a one-row result IS the answer; no identifier is demanded")
# ---------------------------------------------------------------------------
# Measured: 3 of the 12 IDENTIFIER_MISSING firings were on one-row results and
# none of the loop's three wins was; one of those three destroyed a correct
# single-fact answer by sending the writer off to a different table.
q28 = "which department is recorded for the studio?"
sql28 = 'SELECT department, area_m2 FROM "bld_places" WHERE place_name ILIKE \'%studio%\''
one_row = table_result(["department", "area_m2"], [["X", 30]], sql28)
iss28 = sql_loop.inspect_result(one_row, q28, [CARD_PLACES])
check("a one-row result raises nothing at all", iss28 == [], [i.kind for i in iss28])
two_rows = table_result(["department", "area_m2"], [["X", 30], ["Y", 12]], sql28)
check("two rows of the very same shape still raise IDENTIFIER_MISSING",
      any(i.kind == sql_loop.IDENTIFIER_MISSING
          for i in sql_loop.inspect_result(two_rows, q28, [CARD_PLACES])),
      [i.kind for i in sql_loop.inspect_result(two_rows, q28, [CARD_PLACES])])
ex = FakeExec([one_row, one_row])   # a spare, as above
inv = sql_loop.run_sql_investigation(q28, "u1", None, execute=ex, search=FakeSearch(),
                                     routed_cards=[CARD_PLACES], max_steps=3)
check("and no second query is spent on it", len(ex.questions) == 1, ex.questions)

# ---------------------------------------------------------------------------
print("\n29. W4 — an empty result whose SQL names no routed table is an ABSTENTION")
# ---------------------------------------------------------------------------
# Measured: 5 EMPTY firings had a tableless first SQL and none produced a win; one
# of them turned a correct refusal into a stated figure. A writer that emitted a
# query reading no routed table has already said the data is not there, and telling
# it to look again is asking an abstention to become a claim.
q29 = "what is the energy consumption of the units?"
w4 = empty_result("SELECT NULL WHERE FALSE")
iss29 = sql_loop.inspect_result(w4, q29, [CARD_LIST])
check("EMPTY is still reported", [i.kind for i in iss29] == [sql_loop.EMPTY],
      [i.kind for i in iss29])
check("but it carries no instruction", iss29 and iss29[0].instruction == "",
      [i.instruction for i in iss29])
check("so there is nothing to re-query", sql_loop.first_requery_issue(iss29) is None)
ex = FakeExec([w4, w4])   # a spare, as above
se = FakeSearch("DOCTEXT")
inv = sql_loop.run_sql_investigation(q29, "u1", None, execute=ex, search=se,
                                     routed_cards=[CARD_LIST], max_steps=3)
check("exactly one SQL call", len(ex.questions) == 1, ex.questions)
check("and it goes straight to the document fallback",
      se.queries == [q29] and "DOCTEXT" in inv.result_text, (se.queries, inv.result_text[:120]))
check("EMPTY is still on the record", sql_loop.EMPTY in inv.issues, inv.issues)
# the discriminator: the same empty result whose SQL DOES read a routed table
check("an empty result on a routed table keeps its re-query instruction",
      sql_loop.first_requery_issue(sql_loop.inspect_result(empty_result(sql2), q2, [CARD_LIST]))
      is not None,
      [i.instruction for i in sql_loop.inspect_result(empty_result(sql2), q2, [CARD_LIST])])

# ===========================================================================
# 2026-09-28 - A FAILED FIRST STEP IS A RE-QUERY, NOT THE END OF THE ROAD.
#
# The owner asked "what is <place>? and what all assets there inside?" - two
# questions about one entity. The writer glued them with a set operation across
# tables of different widths, padded the second arm with NULLs until the output
# cap stopped it, and DuckDB refused the whole thing. The one repair call inside
# the executor produced the same shape. The loop then treated FAILED as terminal
# - the behaviour deliberately carried over from before it existed - and the
# question went to document search, which answered from a stray chunk.
#
# A Binder Error is the most re-queryable thing there is: the writer is being
# told, in the data, exactly what it got wrong. So a failure now raises an issue
# WITH an instruction, ranked above EMPTY, and the document fallback waits until
# the LAST step has also failed.
# ===========================================================================
print("\n30. A failed result raises FAILED_SQL, and it carries an instruction")
# ---------------------------------------------------------------------------
BINDER = ("SQL query failed: Binder Error: Set operations can only apply to "
          "expressions with the same number of result columns"
          "\n\nGenerated SQL: `SELECT * FROM \"bld_units\" UNION ALL SELECT NULL, NULL`")
iss30 = sql_loop.inspect_result(BINDER, q2, [CARD_LIST])
check("the kind exists", hasattr(sql_loop, "FAILED_SQL"))
check("a failed result raises exactly one issue, FAILED_SQL",
      [i.kind for i in iss30] == [getattr(sql_loop, "FAILED_SQL", "FAILED_SQL")],
      [i.kind for i in iss30])
check("it outranks EMPTY in the priority order",
      getattr(sql_loop, "FAILED_SQL", None) in sql_loop.ISSUE_ORDER
      and sql_loop.ISSUE_ORDER.index(getattr(sql_loop, "FAILED_SQL", ""))
          < sql_loop.ISSUE_ORDER.index(sql_loop.EMPTY),
      sql_loop.ISSUE_ORDER)
check("it is something the loop can act on", sql_loop.first_requery_issue(iss30) is not None)
instr30 = iss30[0].instruction if iss30 else ""
check("the instruction quotes the error it is reacting to",
      "Binder Error" in instr30, instr30[:200])
_err = getattr(sql_loop, "result_failure_error", None)
check("the error extractor exists", _err is not None)
check("and only the first 200 characters of it - an 8 KB error does not become "
      "the next prompt",
      bool(_err) and len(_err("SQL query failed: " + "x" * 5000)) <= 200,
      len(_err("SQL query failed: " + "x" * 5000)) if _err else "-")
check("it forbids the set operation that caused this", "no UNION" in instr30, instr30)
check("and names the other two set operations too",
      "INTERSECT" in instr30 and "EXCEPT" in instr30, instr30)
check("it asks for ONE table", "ONE table" in instr30, instr30)
check("it asks for one plain SELECT", "ONE plain SELECT" in instr30, instr30)
check("it forbids inventing columns", "invented column" in instr30, instr30)
check("it says how to answer a two-part question instead: the specific part, "
      "JOINed to the entity's own row",
      "two parts" in instr30 and "JOIN" in instr30, instr30)
check("a normal result raises no FAILED_SQL",
      not any(i.kind == getattr(sql_loop, "FAILED_SQL", "FAILED_SQL")
              for i in sql_loop.inspect_result(good, q2, [CARD_LIST])))

# ---------------------------------------------------------------------------
print("\n31. [failed, good] - the loop re-queries and keeps the good result")
# ---------------------------------------------------------------------------
GOOD31 = table_result(["unit_tag", "level_code", "kind", "notes"],
                      [["U-1", "L1", "a", ""]], 'SELECT * FROM "bld_units"')
ex = FakeExec([BINDER, GOOD31])
se = FakeSearch("DOCTEXT")
inv = sql_loop.run_sql_investigation("what is unit 7 and what is inside it?", "u1", None,
                                     execute=ex, search=se, routed_cards=[CARD_LIST],
                                     max_steps=3)
check("exactly two SQL calls", len(ex.questions) == 2, len(ex.questions))
check("the second question carries the re-query instruction",
      "no UNION" in ex.questions[1] and "Investigation step 2" in ex.questions[1],
      ex.questions[1][-300:])
check("the good result is what the answer writer gets",
      "U-1" in inv.result_text and "Binder Error" not in inv.result_text,
      inv.result_text[:200])
check("and the documents were never searched - the tables answered",
      se.queries == [], se.queries)
check("the failure is on the record for the answer writer",
      getattr(sql_loop, "FAILED_SQL", "") in inv.issues, inv.issues)
check("source_tool still says the tables", inv.source_tool == "query_structured_data",
      inv.source_tool)

# ---------------------------------------------------------------------------
print("\n32. [failed, failed] - two steps, then the document fallback")
# ---------------------------------------------------------------------------
ex = FakeExec([BINDER, BINDER])
se = FakeSearch("DOCTEXT")
inv = sql_loop.run_sql_investigation(q2, "u1", None, execute=ex, search=se,
                                     routed_cards=[CARD_LIST], max_steps=3)
check("two SQL calls, not three - the repeat-issue guard stops it",
      len(ex.questions) == 2, len(ex.questions))
check("then the documents are searched", se.queries == [q2], se.queries)
check("the excerpts replace the error text, with no empty-result preamble",
      "DOCTEXT" in inv.result_text and sql_loop.EMPTY_FALLBACK_PREFIX not in inv.result_text,
      inv.result_text[:200])
check("the terminal fallback is still reported as FAILED",
      sql_loop.FAILED in inv.issues, inv.issues)
check("and source_tool says the documents answered",
      inv.source_tool == "search_documents", inv.source_tool)

# ---------------------------------------------------------------------------
print("\n33. max_steps=1 - one query, the old fallback, no re-query at all")
# ---------------------------------------------------------------------------
ex = FakeExec([BINDER])
se = FakeSearch("DOCTEXT")
inv = sql_loop.run_sql_investigation(q2, "u1", None, execute=ex, search=se,
                                     routed_cards=[CARD_LIST], max_steps=1)
check("exactly one SQL call", len(ex.questions) == 1, ex.questions)
check("the question is not augmented at all", ex.questions == [q2], ex.questions)
check("the documents answer", se.queries == [q2] and "DOCTEXT" in inv.result_text,
      (se.queries, inv.result_text[:120]))
check("no trailer line, exactly as before the loop existed",
      "INVESTIGATION" not in inv.result_text, inv.result_text[-120:])

# ---------------------------------------------------------------------------
print("\n34. The module is still generic - the new instruction names nothing")
# ---------------------------------------------------------------------------
SRC34 = Path(sql_loop.__file__).read_text(encoding="utf-8")
for tok in ("hwu", "room", "panel", "camera", "door", "fcu", "location_id"):
    hits = len(_re.findall(tok, SRC34, _re.IGNORECASE))
    check(f"still no '{tok}' anywhere in the module", hits == 0, f"{hits} hits")
check("still no `while`", not _re.search(r"\bwhile\b", SRC34))

# ===========================================================================
# 2026-09-28 - AN EMPTY RESULT, AND THE QUESTION ITSELF NAMES THE ROW.
#
# Traced from card ex-047. Step 1 wrote a nonsense self-join, matched nothing,
# and the generic EMPTY instruction ("filter a place by its printed NAME ...
# if the question names a place ...") then sent the writer to a different table
# joined to the place index. It came back with one row of five columns, none of
# them the ones asked about, and the answer said there was no link on record -
# when the answer was sitting on the entity's OWN row in a routed table all
# along.
#
# The question printed that entity's coded identifier, and the routed card
# DECLARES both the column that identifier lives in and the prefixes it starts
# with. That is a deterministic address, not a guess, so the EMPTY instruction
# becomes the address: select the entity's own row, every column, nothing else.
# The kind stays EMPTY, so the priority order and the repeat guard do not move;
# only the `detail` says which of the two EMPTY instructions fired.
# ===========================================================================
print("\n35. A coded identifier the question prints becomes the EMPTY instruction")
# ---------------------------------------------------------------------------
CARD_CAM = {
    "table": "bld_cam",
    "columns": ["cam_tag", "level_code", "link_id", "backup_units", "notes"],
    "identifier_column": "cam_tag",
    "identifier_prefixes": ["CAM"],
}
q35 = "which support units back up unit CAM-4F-B-01, and where are they?"
sql35 = ('SELECT T1.cam_tag FROM "bld_cam" T1 JOIN "bld_cam" T2 '
         "ON T1.level_code = T2.level_code AND T2.kind = 'SUPPORT'")
iss35 = sql_loop.inspect_result(empty_result(sql35), q35, [CARD_CAM])
check("the kind is still EMPTY", [i.kind for i in iss35] == [sql_loop.EMPTY],
      [i.kind for i in iss35])
e35 = iss35[0] if iss35 else None
WANT35 = 'SELECT * FROM "bld_cam" WHERE "cam_tag" = \'CAM-4F-B-01\''
check("the instruction is the entity's own row, written out in full",
      e35 is not None and WANT35 in e35.instruction, e35.instruction if e35 else "")
check("it names the identifier the question printed",
      e35 is not None and "CAM-4F-B-01" in e35.instruction and "identifier" in e35.instruction,
      e35.instruction if e35 else "")
check("it names the table and the column the card declares",
      e35 is not None and '"bld_cam"' in e35.instruction and '"cam_tag"' in e35.instruction,
      e35.instruction if e35 else "")
check("it asks for every column and nothing else",
      e35 is not None and "every column" in e35.instruction
      and "nothing else" in e35.instruction, e35.instruction if e35 else "")
check("it does not also carry the generic advice it replaces",
      e35 is not None and "ILIKE" not in e35.instruction, e35.instruction if e35 else "")
check("the detail says IDENTIFIER_EMPTY", e35 is not None and hasattr(sql_loop, "IDENTIFIER_EMPTY")
      and e35.detail == sql_loop.IDENTIFIER_EMPTY, e35.detail if e35 else "")
check("it is something the loop can act on",
      sql_loop.first_requery_issue(iss35) is not None
      and sql_loop.first_requery_issue(iss35).kind == sql_loop.EMPTY)
# Re-pinned in wave 3 (F2), deliberately: LITERAL_ELSEWHERE is a new KIND, ranked right after
# EMPTY (tests/test_sql_literal_elsewhere.py section 3). IDENTIFIER_EMPTY is still no kind.
# Re-pinned again in wave 3 (G6), deliberately: PLACE_RANKED_ON_TEXT is a new KIND, ranked right
# after LITERAL_ELSEWHERE (tests/test_sql_place_ranked.py section 1).
# Re-pinned again in wave 4 (A2), deliberately: PLACE_COMPARED_ON_TEXT, G6 generalised to set
# comparisons, is a new KIND ranked right after PLACE_RANKED_ON_TEXT (test_sql_place_ranked.py
# section 5).
check("IDENTIFIER_EMPTY is a DETAIL, not a kind - the priority order is unchanged "
      "apart from T7's LETTER_CROSSCHECK appended at the end, wave 3's LITERAL_ELSEWHERE "
      "(now leading the whole group, wave 6, W6-A1 - see below) and PLACE_RANKED_ON_TEXT "
      "after it, wave 4's PLACE_COMPARED_ON_TEXT after that, and wave 5's CURRENT_TWIN right "
      "after LITERAL_ELSEWHERE (ahead of both PLACE_RANKED_ON_TEXT and "
      "PLACE_COMPARED_ON_TEXT: the wrong ERA of table outranks how a right-era result is "
      "grouped or compared). Re-pinned in wave 6 (W6-A1), deliberately: once an EMPTY "
      "result's own text can carry the LITERAL_ELSEWHERE line too, that finding - it names "
      "the exact column where the value IS - must be chosen ahead of either EMPTY "
      "instruction (the identifier address, the generic advice), so EMPTY moves to right "
      "before the column-shaped issues. CURRENT_TWIN, PLACE_RANKED_ON_TEXT and "
      "PLACE_COMPARED_ON_TEXT are all raised only on a NON-empty result by construction, so "
      "this move changes nothing for any of them - it matters only for the new "
      "EMPTY+LITERAL_ELSEWHERE overlap (test_sql_literal_elsewhere.py section 6).",
      getattr(sql_loop, "IDENTIFIER_EMPTY", None) not in sql_loop.ISSUE_ORDER
      and sql_loop.ISSUE_ORDER == (sql_loop.FAILED_SQL,
                                   sql_loop.LITERAL_ELSEWHERE,
                                   getattr(sql_loop, "CURRENT_TWIN", None),
                                   sql_loop.PLACE_RANKED_ON_TEXT,
                                   getattr(sql_loop, "PLACE_COMPARED_ON_TEXT", None),
                                   sql_loop.EMPTY,
                                   sql_loop.IDENTIFIER_MISSING, sql_loop.NARROW_SELECT,
                                   sql_loop.TRUNCATED_NO_SHAPE, sql_loop.COUNT_CROSSCHECK,
                                   sql_loop.LETTER_CROSSCHECK),
      sql_loop.ISSUE_ORDER)

# ---------------------------------------------------------------------------
print("\n36. No prefix match, no coded token: the generic EMPTY text stands, word for word")
# ---------------------------------------------------------------------------
def generic_empty_text(sql):
    """The wording `_empty_issue` uses when it has no values to list, written out here so
    a change to it turns this check red rather than sliding through.

    REWRITTEN 2026-10-01 (spec-fix7 part c). The old text said "Re-read the column
    samples" and "the one-row-per-place-and-item table". The re-query is routed on its own
    question text, and the router reads the head of every HYPHENATED word as a coded
    identifier prefix - so every generic EMPTY re-query asked the router for tables whose
    identifiers start "RE" or "ONE", and a table declaring RE took a ranked slot on most
    of them. The advice is unchanged; only the two hyphenated words are gone."""
    return ("The previous query `" + sql + "` returned no rows. Read the column "
            "samples again; filter a place or entity by its printed NAME on the name column "
            "with ILIKE '%…%', never by a built id; if the question names a place, "
            "use the table with one row per place and item.")

# same question shape, a card whose declared prefixes do NOT cover it
CARD_OTHER = dict(CARD_CAM, identifier_prefixes=["ZZZ"])
iss36 = sql_loop.inspect_result(empty_result(sql35), q35, [CARD_OTHER])
check("an unmatched prefix leaves the generic instruction exactly as written above",
      bool(iss36) and iss36[0].instruction == generic_empty_text(sql35),
      iss36[0].instruction if iss36 else "")
check("and the two hyphenated words it used to carry are gone",
      bool(iss36) and "Re-read" not in iss36[0].instruction
      and "one-row-per" not in iss36[0].instruction,
      iss36[0].instruction if iss36 else "")
check("and its detail is not the identifier one",
      bool(iss36) and iss36[0].detail != getattr(sql_loop, "IDENTIFIER_EMPTY", "IDENTIFIER_EMPTY"),
      iss36[0].detail if iss36 else "")
# a question with no coded token at all
q36 = "which support units back up the ground unit, and where are they?"
iss36b = sql_loop.inspect_result(empty_result(sql35), q36, [CARD_CAM])
check("a question printing no coded token gets the generic instruction",
      bool(iss36b) and iss36b[0].instruction == generic_empty_text(sql35),
      iss36b[0].instruction if iss36b else "")
# a card that declares prefixes but no identifier column cannot address anything
CARD_NO_COL = dict(CARD_CAM, identifier_column=None)
iss36c = sql_loop.inspect_result(empty_result(sql35), q35, [CARD_NO_COL])
check("a card with prefixes but no identifier column is skipped",
      bool(iss36c) and iss36c[0].instruction == generic_empty_text(sql35),
      iss36c[0].instruction if iss36c else "")
# and a result that is NOT empty raises none of this
check("a good result raises no EMPTY at all",
      not any(i.kind == sql_loop.EMPTY for i in sql_loop.inspect_result(
          table_result(["cam_tag", "link_id"], [["CAM-4F-B-01", "X"]],
                       'SELECT cam_tag, link_id FROM "bld_cam"'), q35, [CARD_CAM])))

# ---------------------------------------------------------------------------
print("\n37. The tag is quoted safely, and routed order orders the choices")
# ---------------------------------------------------------------------------
_quote = getattr(sql_loop, "quote_literal", None)
check("the tag goes in as a SQL string literal, and a single quote in it is DOUBLED",
      _quote is not None and _quote("QM-O'BRIEN-2") == "'QM-O''BRIEN-2'",
      _quote("QM-O'BRIEN-2") if _quote else "no quote_literal")
check("an ordinary tag is quoted and otherwise untouched",
      _quote is not None and _quote("CAM-4F-B-01") == "'CAM-4F-B-01'",
      _quote("CAM-4F-B-01") if _quote else "-")
_build = getattr(sql_loop, "_empty_identifier_issue", None)
_i37 = _build([("QM-O'BRIEN-2", "bld_q", "q_tag")]) if _build else None
check("the instruction carries the doubled form in BOTH places it prints the tag",
      _i37 is not None and _i37.instruction.count("QM-O''BRIEN-2") == 2,
      _i37.instruction if _i37 else "no _empty_identifier_issue")
check("and the unescaped form appears nowhere in it",
      _i37 is not None and "QM-O'BRIEN-2'" not in _i37.instruction.replace("QM-O''BRIEN-2", ""),
      _i37.instruction if _i37 else "-")
# ...and the tokeniser cannot hand it one in the first place, which is the point of
# copying the router's token shape rather than inventing a wider one: a printed
# apostrophe ENDS the token, so an English possessive stuck to a tag can never be
# read as part of it.
_qi = getattr(sql_loop, "question_identifiers", None)
check("a printed apostrophe ends the token - it is never swallowed into the tag",
      _qi is not None and [t for _, t in _qi("what is unit QM-O'BRIEN-2?")] == ["QM-O", "BRIEN-2"],
      [t for _, t in _qi("what is unit QM-O'BRIEN-2?")] if _qi else "-")
check("an ordinary word is not a coded token, and a hyphenated or numbered one is",
      _qi is not None
      and [t for _, t in _qi("list the units")] == []
      and [t for _, t in _qi("list unit CAM-4F-B-01 and unit 7")] == ["CAM-4F-B-01", "7"],
      [_qi("list unit CAM-4F-B-01 and unit 7")] if _qi else "-")
CARD_CAM_B = {"table": "bld_cam_b", "columns": ["tag_b", "y"], "identifier_column": "tag_b",
              "identifier_prefixes": ["CAM"]}
first = sql_loop.inspect_result(empty_result(sql35), q35, [CARD_CAM, CARD_CAM_B])[0]
second = sql_loop.inspect_result(empty_result(sql35), q35, [CARD_CAM_B, CARD_CAM])[0]
check("two cards match: BOTH are offered, the first routed one first",
      0 <= first.instruction.find('"bld_cam"') < first.instruction.find('"bld_cam_b"'),
      first.instruction)
check("and reversing the routed order reverses the listing - order is the whole rule",
      0 <= second.instruction.find('"bld_cam_b"') < second.instruction.find('"bld_cam" '),
      second.instruction)
# the tokenising agrees with the router's, which is where the prefixes come from
from app.services import table_router as _tr
check("the prefix this module reads is the prefix the router scored the card on",
      _tr._question_prefixes(q35) >= {"CAM"}, _tr._question_prefixes(q35))

# ---------------------------------------------------------------------------
print("\n38. An abstention with a printed identifier is addressed, not abandoned")
# ---------------------------------------------------------------------------
# W4 (section 29) makes an empty result whose SQL read NO routed table a finding:
# the writer declined to look anywhere, and telling an abstention to look again
# asks it to become a claim. That reasoning is about a VAGUE instruction. When the
# question prints the identifier of a routed table, the re-query is not "look
# again" - it is an address, and the row either exists or it does not.
iss38 = sql_loop.inspect_result(empty_result("SELECT NULL WHERE FALSE"), q35, [CARD_CAM])
check("the abstention still reports EMPTY", [i.kind for i in iss38] == [sql_loop.EMPTY],
      [i.kind for i in iss38])
check("but it now carries the address", bool(iss38) and WANT35 in iss38[0].instruction,
      iss38[0].instruction if iss38 else "")
check("an abstention with NO identifier match is still a finding",
      sql_loop.first_requery_issue(sql_loop.inspect_result(
          empty_result("SELECT NULL WHERE FALSE"), q36, [CARD_CAM])) is None)

# ---------------------------------------------------------------------------
print("\n39. The run: [empty, good] - two steps, and the entity's own row wins")
# ---------------------------------------------------------------------------
GOOD39 = table_result(["cam_tag", "level_code", "link_id", "backup_units", "notes"],
                      [["CAM-4F-B-01", "L4", "LNK-9", "U-1, U-2", ""]],
                      'SELECT * FROM "bld_cam" WHERE "cam_tag" = \'CAM-4F-B-01\'')
ex = FakeExec([empty_result(sql35), GOOD39])
se = FakeSearch("DOCTEXT")
inv39 = sql_loop.run_sql_investigation(q35, "u1", None, execute=ex, search=se,
                                       routed_cards=[CARD_CAM], max_steps=3)
check("exactly two SQL calls", len(ex.questions) == 2, len(ex.questions))
check("the second question carries the address",
      len(ex.questions) == 2 and WANT35 in ex.questions[1]
      and "Investigation step 2" in ex.questions[1],
      ex.questions[-1][-300:])
check("the good result is what the answer writer gets",
      "U-1, U-2" in inv39.result_text, inv39.result_text[:200])
check("two steps on the record", len(inv39.steps) == 2, len(inv39.steps))
check("the second step's detail names IDENTIFIER_EMPTY",
      len(inv39.steps) == 2
      and inv39.steps[1]["detail"] == getattr(sql_loop, "IDENTIFIER_EMPTY", "IDENTIFIER_EMPTY"),
      [s["detail"] for s in inv39.steps])
check("the step event still reports the kind as EMPTY - the caller's wiring is unmoved",
      len(inv39.steps) == 2 and inv39.steps[1]["issue"] == sql_loop.EMPTY,
      [s["issue"] for s in inv39.steps])
check("and the documents were never searched - the tables answered",
      se.queries == [], se.queries)
# [empty, empty] - the repeat guard is unchanged: same kind twice, then the documents
ex = FakeExec([empty_result(sql35)] * 2)
se = FakeSearch("DOCTEXT")
inv39b = sql_loop.run_sql_investigation(q35, "u1", None, execute=ex, search=se,
                                        routed_cards=[CARD_CAM], max_steps=3)
check("empty twice: two SQL calls, not three", len(ex.questions) == 2, len(ex.questions))
check("then the document fallback, exactly as before",
      se.queries == [q35] and "DOCTEXT" in inv39b.result_text,
      (se.queries, inv39b.result_text[:120]))
# ===========================================================================
# THE COORDINATOR'S RULING, 2026-09-28. Replayed against the real cards, four
# of the seven questions the address fires on reached a table whose name marks
# it as an EARLIER state of the building, because the caller hands these cards
# over in name order and the router's own ranking never survives the trip. The
# ruling: do not reorder anything upstream - when MORE THAN ONE routed card can
# address the identifier, OFFER them all, in routed order, and let the writer
# pick by subject. It is the one choice a model is better placed to make than
# this module: which table holds what the question asks about, and which one
# its own name says is superseded.
#
# With exactly one match nothing changes: there is nothing to choose between.
# ===========================================================================
print("\n40. Several routed cards can address the tag: offer them all, in routed order")
# ---------------------------------------------------------------------------
CARD_NOW = {"table": "bld_now", "columns": ["feed_tag", "fed_from", "rating"],
            "identifier_column": "feed_tag", "identifier_prefixes": ["FD"]}
CARD_WAS = {"table": "bld_was_earlier", "columns": ["feed_tag", "fed_from"],
            "identifier_column": "feed_tag", "identifier_prefixes": ["FD"]}
CARD_THIRD = {"table": "bld_third", "columns": ["ref", "x"],
              "identifier_column": "ref", "identifier_prefixes": ["FD"]}
q40 = "what does FD-06(B)-SP-01 feed, and what feeds it?"
sql40 = 'SELECT fed_from FROM "bld_now" WHERE feed_tag = \'FD-6-B-SP-1\''
i40 = sql_loop.inspect_result(empty_result(sql40), q40, [CARD_WAS, CARD_NOW])[0]
check("the kind is still EMPTY and the detail still IDENTIFIER_EMPTY",
      i40.kind == sql_loop.EMPTY and i40.detail == sql_loop.IDENTIFIER_EMPTY,
      (i40.kind, i40.detail))
check("both SELECTs are written out in full",
      'SELECT * FROM "bld_was_earlier" WHERE "feed_tag" = \'FD-06(B)-SP-01\'' in i40.instruction
      and 'SELECT * FROM "bld_now" WHERE "feed_tag" = \'FD-06(B)-SP-01\'' in i40.instruction,
      i40.instruction)
check("in ROUTED order - the first routed card is offered first",
      0 <= i40.instruction.find('"bld_was_earlier"') < i40.instruction.find('"bld_now"'),
      i40.instruction)
check("it says to write exactly ONE of them",
      "Write exactly ONE of:" in i40.instruction, i40.instruction)
check("it names both tables with their identifier columns",
      'these tables: "bld_was_earlier" (column "feed_tag"), "bld_now" (column "feed_tag")'
      in i40.instruction, i40.instruction)
check("the choose-the-table clause is there",
      "choose the table whose columns hold what the question asks for" in i40.instruction,
      i40.instruction)
check("and it says to prefer the current-state table over a superseded one",
      "prefer a current-state table over one whose name or description marks it as an "
      "earlier or superseded state" in i40.instruction, i40.instruction)
check("it still forbids anything else", "Nothing else." in i40.instruction, i40.instruction)
check("the tag is printed once as the identifier and once per SELECT",
      i40.instruction.count("'FD-06(B)-SP-01'") == 3, i40.instruction)
# three cards, and the reverse order
i40c = sql_loop.inspect_result(empty_result(sql40), q40,
                               [CARD_NOW, CARD_THIRD, CARD_WAS])[0]
check("three matching cards: all three are offered, in routed order",
      0 <= i40c.instruction.find('"bld_now"') < i40c.instruction.find('"bld_third"')
      < i40c.instruction.find('"bld_was_earlier"'), i40c.instruction)
check("and the third one's own identifier column comes with it",
      '"bld_third" (column "ref")' in i40c.instruction
      and 'SELECT * FROM "bld_third" WHERE "ref" = \'FD-06(B)-SP-01\'' in i40c.instruction,
      i40c.instruction)

# ---------------------------------------------------------------------------
print("\n41. Exactly one match keeps the single-table wording, word for word")
# ---------------------------------------------------------------------------
SINGLE = ("The previous query returned no rows. The question names the identifier "
          "'CAM-4F-B-01', which is the identifier of table \"bld_cam\" (column "
          "\"cam_tag\"). Write exactly: SELECT * FROM \"bld_cam\" WHERE "
          "\"cam_tag\" = 'CAM-4F-B-01' \u2014 the entity's own row with every column "
          "\u2014 and nothing else.")
i41 = sql_loop.inspect_result(empty_result(sql35), q35, [CARD_CAM])[0]
check("one match: the single-table instruction is exactly as it was",
      i41.instruction == SINGLE, i41.instruction)
check("it says 'table', singular, and never offers a choice",
      "these tables" not in i41.instruction and "ONE of" not in i41.instruction,
      i41.instruction)
# a routed card that matches a DIFFERENT printed tag is not listed alongside
CARD_OTHERTAG = {"table": "bld_other", "columns": ["o_tag"], "identifier_column": "o_tag",
                 "identifier_prefixes": ["ZED"]}
q41 = "does unit CAM-4F-B-01 depend on unit ZED-9?"
i41b = sql_loop.inspect_result(empty_result(sql35), q41, [CARD_CAM, CARD_OTHERTAG])[0]
check("a card addressing a DIFFERENT tag is not offered beside this one - every "
      "SELECT offered filters the same printed value",
      "bld_other" not in i41b.instruction and "CAM-4F-B-01" in i41b.instruction
      and "ZED-9" not in i41b.instruction, i41b.instruction)
# the run is unchanged: still two steps, still the good result
ex = FakeExec([empty_result(sql40),
               table_result(["feed_tag", "fed_from", "rating"],
                            [["FD-06(B)-SP-01", "BOARD-X", "100"]],
                            'SELECT * FROM "bld_now" WHERE "feed_tag" = \'FD-06(B)-SP-01\'')])
se = FakeSearch("DOCTEXT")
inv41 = sql_loop.run_sql_investigation(q40, "u1", None, execute=ex, search=se,
                                       routed_cards=[CARD_WAS, CARD_NOW], max_steps=3)
check("the offered choice is still one re-query, not two", len(ex.questions) == 2,
      len(ex.questions))
check("and the chosen table's row is what the answer writer gets",
      "BOARD-X" in inv41.result_text and se.queries == [],
      (inv41.result_text[:160], se.queries))
# ===========================================================================
# THE REVIEW FINDING, 2026-09-28 (task-8-9-review-report.md). The offer is
# filtered to ONE printed identifier so that every SELECT in it filters the
# same value - but that identifier was being taken from the FIRST ROUTED CARD's
# match, which is card order, not question order. On a question naming two
# entities, a card that can only address the SECOND-named one sorts first often
# enough (the caller hands these over in table-name order), and the whole offer
# then locks onto the entity the question mentions second.
#
# The ruling: the lead tag is the one printed FIRST IN THE QUESTION TEXT, by
# token position, whatever order the cards arrive in. Card order still decides
# the order of the OFFERS - it just no longer decides WHICH ENTITY is offered.
# ===========================================================================
print("\n42. The lead tag is the question's first, never the first routed card's")
# ---------------------------------------------------------------------------
CARD_ZED = {"table": "bld_zed", "columns": ["z_tag", "x"], "identifier_column": "z_tag",
            "identifier_prefixes": ["ZED"]}
q42 = "does unit CAM-4F-B-01 depend on unit ZED-9?"
forward = sql_loop.inspect_result(empty_result(sql35), q42, [CARD_CAM, CARD_ZED])[0]
reverse = sql_loop.inspect_result(empty_result(sql35), q42, [CARD_ZED, CARD_CAM])[0]
check("cards in question order: the first-named entity is the one addressed",
      "'CAM-4F-B-01'" in forward.instruction and "ZED-9" not in forward.instruction,
      forward.instruction)
# THE REPRODUCTION: same question, the ZED card handed over first.
check("cards in the OTHER order: still the first-named entity, not the first card's",
      "'CAM-4F-B-01'" in reverse.instruction and "ZED-9" not in reverse.instruction,
      reverse.instruction)
check("and the table offered is the one that can address it",
      '"bld_cam"' in reverse.instruction and "bld_zed" not in reverse.instruction,
      reverse.instruction)
check("card order cannot change the instruction at all when one card matches",
      forward.instruction == reverse.instruction,
      (forward.instruction, reverse.instruction))
# the fall-through: the question's first coded token that ANY card can address wins
q42b = "is LMN-1 related to CAM-4F-B-01?"
i42b = sql_loop.inspect_result(empty_result(sql35), q42b, [CARD_CAM, CARD_ZED])[0]
check("a first coded token no card declares is passed over, not fatal",
      "'CAM-4F-B-01'" in i42b.instruction and "LMN-1" not in i42b.instruction,
      i42b.instruction)
# and when both cards can address the SAME first-named tag, both are still offered
CARD_CAM_C = {"table": "bld_cam_c", "columns": ["c_tag", "z"], "identifier_column": "c_tag",
              "identifier_prefixes": ["CAM"]}
i42c = sql_loop.inspect_result(empty_result(sql35), q42, [CARD_CAM_C, CARD_CAM])[0]
check("two cards on the first-named tag: both offered, in routed order",
      0 <= i42c.instruction.find('"bld_cam_c"') < i42c.instruction.find('"bld_cam" ')
      and "ZED-9" not in i42c.instruction, i42c.instruction)
check("the matcher itself reports the same thing",
      [m[0] for m in sql_loop.card_identifier_matches(q42, [CARD_ZED, CARD_CAM])]
      == ["CAM-4F-B-01"],
      sql_loop.card_identifier_matches(q42, [CARD_ZED, CARD_CAM]))

# ===========================================================================
# 2026-10-01 - SPEC-FIX7 PART (c). THE EMPTY INSTRUCTION CARRIES NO HYPHENATED
# WORD, AND IT LISTS THE REAL VALUES OF EVERY COLUMN THE FAILED WHERE FILTERED.
#
# Measured on the goal-function run of 2026-10-01. Where the first query
# filtered a text column with a value the column does not hold - a level
# printed one way and asked another, a device named in words the register does
# not use - the generic advice gave the writer nothing new to read. It guessed
# a second code, matched nothing again, and the question fell to the documents.
# The values were in the loaded table all along. So the instruction now lists
# them: every value when a column holds at most VALUE_LIST_MAX, otherwise the
# count and the values that contain the filter's words. They are read through
# an injected `column_values(table, column)`, so this module still needs no
# database to test.
#
# And the wording: the re-query is ROUTED on its own text, and the router takes
# the head of every hyphenated word as a coded identifier prefix. "Re-read" and
# "one-row-per-place-and-item" asked every generic EMPTY re-query for tables
# whose identifiers start RE and ONE.
# ===========================================================================
print("\n43. The EMPTY instruction contains no hyphenated word and lists the values "
      "of each filtered column")
# ---------------------------------------------------------------------------


class FakeValues:
    """Scripted `column_values(table, column)`: the distinct values of a column as the
    loaded table holds them, or None for a table or a column it does not have. Records
    every call, so a check can prove WHEN the loop reads and when it does not."""
    def __init__(self, tables, raises=None):
        self.tables = tables
        self.raises = raises
        self.calls = []

    def __call__(self, table, column):
        self.calls.append((table, column))
        if self.raises is not None:
            raise self.raises
        cols = self.tables.get(table)
        if cols is None:
            return None
        for name, values in cols.items():
            if name.lower() == str(column).lower():
                return list(values)
        return None


CARD_KIT = {"table": "bld_kit",
            "columns": ["kit_tag", "deck", "kind", "label", "grade", "shade", "notes"],
            "identifier_column": "kit_tag"}
CARD_BAY = {"table": "bld_bay", "columns": ["bay_code", "deck", "bay_title"],
            "identifier_column": "bay_code"}
DECKS = ["Upper Deck", "Lower Deck", "Mid Deck", "Sub Deck 1", "Sub Deck 2"]
KINDS = ["widget", "sprocket", "gear"]
LABELS = [f"Label {i:03d}" for i in range(50)] + ["Gauge Alpha", "Zeta Gauge Housing",
                                                   "Gauge Zeta"]
KIT_VALUES = {"bld_kit": {"deck": DECKS, "kind": KINDS, "label": LABELS,
                          "grade": ["A", "B"], "shade": ["Teal", "Ochre"]},
              "bld_bay": {"deck": DECKS, "bay_title": ["North Bay", "South Bay"]}}
HYPHENATED = _re.compile(r"[A-Za-z0-9]-[A-Za-z0-9]")
q43 = "which kits are on the top deck?"
sql43 = 'SELECT kit_tag, deck FROM "bld_kit" WHERE deck = \'Top Deck\''


def empty43(sql, cards=(CARD_KIT,), values=None, question=q43):
    """The first issue `inspect_result` raises on an empty result of `sql`."""
    found = sql_loop.inspect_result(empty_result(sql), question, list(cards),
                                    column_values=values)
    return found[0] if found else None


# --- 43a. no hyphenated word, with or without values -------------------------
plain43 = empty43(sql43)
check("without a reader the instruction is the generic advice, word for word",
      plain43 is not None and plain43.instruction == generic_empty_text(sql43),
      plain43.instruction if plain43 else "")
check("it carries no hyphenated word at all",
      plain43 is not None and not HYPHENATED.search(plain43.instruction),
      HYPHENATED.findall(plain43.instruction) if plain43 else "")
check("so the router reads no coded prefix out of it (the defect was RE and ONE)",
      plain43 is not None and _tr._question_prefixes(plain43.instruction) == set(),
      _tr._question_prefixes(plain43.instruction) if plain43 else "")
check("nor out of the whole step suffix it is routed as",
      plain43 is not None and _tr._question_prefixes(
          sql_loop.step_instruction_suffix(2, plain43, sql43)) == set())
vals43 = FakeValues(KIT_VALUES)
full43 = empty43(sql43, values=vals43)
check("with the values listed there is still no hyphenated word",
      full43 is not None and not HYPHENATED.search(full43.instruction),
      HYPHENATED.findall(full43.instruction) if full43 else "")
check("and still no coded prefix",
      full43 is not None and _tr._question_prefixes(full43.instruction) == set(),
      _tr._question_prefixes(full43.instruction) if full43 else "")
check("the kind and the detail do not move, so neither do the priority order and the "
      "repeat guard",
      full43 is not None and full43.kind == sql_loop.EMPTY
      and full43.detail == plain43.detail, (full43.kind, full43.detail) if full43 else "")

# --- 43b. a column of at most VALUE_LIST_MAX values: every one of them ----------
check("the caps are the spec's: 40 values, 80 characters a value, 4 columns",
      (getattr(sql_loop, "VALUE_LIST_MAX", None), getattr(sql_loop, "VALUE_MAX_CHARS", None),
       getattr(sql_loop, "VALUE_COLUMNS_MAX", None)) == (40, 80, 4))
FULL43 = (generic_empty_text(sql43)
          + " The real values of the columns it filtered: \"bld_kit\".\"deck\" holds "
          "exactly these 5 values: 'Upper Deck', 'Lower Deck', 'Mid Deck', 'Sub Deck 1', "
          "'Sub Deck 2'. Filter with the listed values that mean what the question asks "
          "for, copied exactly as listed; never substitute a different value for the "
          "question's term.")
check("the whole instruction, word for word: the advice, then every value the column "
      "holds in the table's own order, then the guidance",
      full43 is not None and full43.instruction == FULL43,
      full43.instruction if full43 else "")
check("the reader was asked for exactly the filtered column",
      vals43.calls == [("bld_kit", "deck")], vals43.calls)

# --- 43c. more than VALUE_LIST_MAX: the count, and the values holding its words --
i43c = empty43('SELECT kit_tag FROM "bld_kit" WHERE label ILIKE \'%zeta gauge%\'',
               values=FakeValues(KIT_VALUES))
check("more than 40 values: the count, then only the values holding the filter's words, "
      "those holding every word first",
      i43c is not None and "\"bld_kit\".\"label\" holds 53 values, and the 3 containing "
      "'zeta' or 'gauge' are: 'Zeta Gauge Housing', 'Gauge Zeta', 'Gauge Alpha'"
      in i43c.instruction, i43c.instruction if i43c else "")
check("and no value without those words is listed",
      i43c is not None and "Label 000" not in i43c.instruction,
      i43c.instruction if i43c else "")
i43d = empty43('SELECT kit_tag FROM "bld_kit" WHERE label ILIKE \'%omega%\'',
               values=FakeValues(KIT_VALUES))
check("when no value holds the words, it says so rather than listing others",
      i43d is not None and "\"bld_kit\".\"label\" holds 53 values, and none contains "
      "'omega'" in i43d.instruction and "'Label" not in i43d.instruction,
      i43d.instruction if i43d else "")

# --- 43d. IN lists, AND/OR groups, one listing per column -----------------------
sql43e = ("SELECT kit_tag FROM \"bld_kit\" WHERE kind IN ('gadget', 'gizmo') "
          "AND (deck ILIKE 'Top%' OR deck ILIKE 'Roof%') ORDER BY kit_tag")
_fl = getattr(sql_loop, "filtered_literals", None)
check("the parser reads = / ILIKE / IN literals in WHERE order, one entry per column",
      _fl is not None and _fl(sql43e) == [(None, "kind", ["gadget", "gizmo"]),
                                          (None, "deck", ["Top%", "Roof%"])],
      _fl(sql43e) if _fl else "no filtered_literals")
vals43e = FakeValues(KIT_VALUES)
i43e = empty43(sql43e, values=vals43e)
check("both columns are listed, in the order the WHERE filters them",
      i43e is not None
      and 0 <= i43e.instruction.find('"bld_kit"."kind" holds exactly these 3 values')
      < i43e.instruction.find('"bld_kit"."deck" holds exactly these 5 values'),
      i43e.instruction if i43e else "")
check("a column filtered twice is listed once, and read once",
      i43e is not None and i43e.instruction.count('"bld_kit"."deck"') == 1
      and vals43e.calls == [("bld_kit", "kind"), ("bld_kit", "deck")],
      (vals43e.calls, i43e.instruction if i43e else ""))
i43in = empty43("SELECT kit_tag FROM \"bld_kit\" WHERE label IN ('Alpha', 'Housing')",
                values=FakeValues(KIT_VALUES))
check("the words of EVERY literal of a column count: an IN list over a long column finds "
      "a value for each",
      i43in is not None and "the 2 containing 'Alpha' or 'Housing' are: 'Gauge Alpha', "
      "'Zeta Gauge Housing'" in i43in.instruction, i43in.instruction if i43in else "")

# --- 43e. which TABLE a column belongs to -----------------------------------------
vals43f = FakeValues(KIT_VALUES)
i43f = empty43('SELECT k.kit_tag FROM "bld_kit" AS k JOIN "bld_bay" b ON k.deck = b.deck '
               "WHERE b.bay_title ILIKE '%east%' AND k.kind = 'gadget'",
               cards=(CARD_KIT, CARD_BAY), values=vals43f)
check("an alias resolves through FROM ... AS and through a bare JOIN alias",
      vals43f.calls == [("bld_bay", "bay_title"), ("bld_kit", "kind")], vals43f.calls)
check("and a join's ON condition is not a WHERE filter, so it lists nothing",
      i43f is not None and '"deck"' not in i43f.instruction.split("returned no rows.")[1],
      i43f.instruction if i43f else "")
vals43g = FakeValues(KIT_VALUES)
empty43('SELECT k.kit_tag FROM "bld_kit" k JOIN "bld_bay" b ON k.deck = b.deck '
        "WHERE bay_title = 'East Bay'", cards=(CARD_KIT, CARD_BAY), values=vals43g)
check("an unqualified column belongs to the read table whose card lists it",
      vals43g.calls == [("bld_bay", "bay_title")], vals43g.calls)
vals43h = FakeValues(KIT_VALUES)
empty43("SELECT kit_tag FROM \"bld_kit\" WHERE kind = 'gadget' AND deck IN "
        "(SELECT deck FROM \"bld_bay\" WHERE deck = 'East Deck')",
        cards=(CARD_KIT, CARD_BAY), values=vals43h)
check("a subquery's own filter belongs to the subquery's table, even when the outer "
      "table has a column of the same name; `IN (SELECT ...)` itself lists nothing",
      vals43h.calls == [("bld_kit", "kind"), ("bld_bay", "deck")], vals43h.calls)
vals43i = FakeValues(KIT_VALUES)
i43i = empty43("SELECT kit_tag FROM \"bld_kit\" WHERE colour = 'red'", values=vals43i)
check("a column no read card lists is not guessed at: nothing read, the advice unchanged",
      vals43i.calls == [] and i43i is not None
      and i43i.instruction == generic_empty_text("SELECT kit_tag FROM \"bld_kit\" WHERE colour = 'red'"),
      (vals43i.calls, i43i.instruction if i43i else ""))

# --- 43f. anything but the three simple forms lists nothing -----------------------
for bad in ("SELECT kit_tag FROM \"bld_kit\" WHERE LOWER(kind) = 'gadget'",
            "SELECT kit_tag FROM \"bld_kit\" WHERE kind ILIKE '%' || deck || '%'",
            "SELECT kit_tag FROM \"bld_kit\" WHERE NOT kind = 'gadget'",
            "SELECT kit_tag FROM \"bld_kit\" WHERE kind NOT IN ('widget')",
            "SELECT kit_tag FROM \"bld_kit\" WHERE kind NOT ILIKE '%widget%'",
            "SELECT kit_tag FROM \"bld_kit\" WHERE kind <> 'widget'",
            "SELECT kit_tag FROM \"bld_kit\" WHERE grade = 5",
            "SELECT kit_tag FROM \"bld_kit\" WHERE kind IN (SELECT kind FROM \"bld_bay\")",
            "SELECT kit_tag FROM \"bld_kit\" WHERE kind = 'gadget",
            "SELECT kit_tag, CASE WHEN kind = 'gear' THEN 1 END FROM \"bld_kit\" WHERE FALSE"):
    v = FakeValues(KIT_VALUES)
    got = empty43(bad, values=v)
    plain_prefix = 'SELECT kit_tag FROM "bld_kit" '
    shown_bad = bad[len(plain_prefix):] if bad.startswith(plain_prefix) else bad
    check(f"lists nothing, keeps the advice word for word: {shown_bad}",
          v.calls == [] and got is not None and got.instruction == generic_empty_text(bad),
          (v.calls, got.instruction if got else ""))
tricky = ("SELECT kit_tag FROM \"bld_kit\" WHERE deck = 'O''Neil (ORDER BY) AND x' "
          "AND kind = 'gadget'")
check("a literal holding a doubled quote, parentheses and keywords does not derail it",
      _fl is not None and _fl(tricky) == [(None, "deck", ["O'Neil (ORDER BY) AND x"]),
                                          (None, "kind", ["gadget"])],
      _fl(tricky) if _fl else "-")
check("a qualified column keeps its qualifier",
      _fl is not None and _fl("SELECT * FROM \"bld_kit\" k WHERE k.\"Kind\" = 'x'")
      == [("k", "Kind", ["x"])],
      _fl("SELECT * FROM \"bld_kit\" k WHERE k.\"Kind\" = 'x'") if _fl else "-")

# --- 43g. the caps ---------------------------------------------------------------
FORTY = [f"Tone {i:02d}" for i in range(40)]
i40v = empty43("SELECT kit_tag FROM \"bld_kit\" WHERE shade = 'Tone 99'",
               values=FakeValues({"bld_kit": {"shade": FORTY}}))
check("exactly 40 values: all of them, 'exactly'",
      i40v is not None and '"bld_kit"."shade" holds exactly these 40 values' in i40v.instruction
      and "'Tone 39'" in i40v.instruction, i40v.instruction if i40v else "")
i41v = empty43("SELECT kit_tag FROM \"bld_kit\" WHERE shade = 'Tone 07'",
               values=FakeValues({"bld_kit": {"shade": FORTY + ["Tone 40"]}}))
check("41 values: the count, and at most 40 of the matches, the closest first",
      i41v is not None and '"bld_kit"."shade" holds 41 values, and 41 contain \'Tone\' or '
      "'07', the first 40 being: 'Tone 07', 'Tone 00', " in i41v.instruction
      and "'Tone 40'" not in i41v.instruction, i41v.instruction if i41v else "")
check("and the list is 40 values long",
      i41v is not None and i41v.instruction.count("'Tone ") == 40 + 1,
      i41v.instruction.count("'Tone ") if i41v else "")
LONG = "Z" * 100
ilong = empty43("SELECT kit_tag FROM \"bld_kit\" WHERE notes = 'short'",
                values=FakeValues({"bld_kit": {"notes": [LONG, "two\nlines", "fine"]}}))
check("a value over 80 characters is cut to 80 with a marker",
      ilong is not None and "'" + "Z" * 79 + "…'" in ilong.instruction
      and "Z" * 80 not in ilong.instruction, ilong.instruction if ilong else "")
check("a value with a line break is cut at the break",
      ilong is not None and "'two…'" in ilong.instruction and "lines" not in ilong.instruction,
      ilong.instruction if ilong else "")
check("and a cut value comes with the note that says how to match it",
      ilong is not None and ilong.instruction.endswith(
          " A listed value that ends in … is longer than shown: match it on its start "
          "with ILIKE."), ilong.instruction if ilong else "")
check("no note when nothing was cut", "longer than shown" not in full43.instruction)
vals5 = FakeValues(KIT_VALUES)
i5 = empty43("SELECT kit_tag FROM \"bld_kit\" WHERE kind = 'a' AND deck = 'b' "
             "AND label = 'c' AND grade = 'd' AND shade = 'e'", values=vals5)
check("five filtered columns: the first four are listed, the fifth is not read",
      i5 is not None and vals5.calls == [("bld_kit", c) for c in ("kind", "deck", "label", "grade")]
      and '"bld_kit"."shade"' not in i5.instruction, (vals5.calls, i5.instruction if i5 else ""))
iblank = empty43("SELECT kit_tag FROM \"bld_kit\" WHERE grade = 'Z'",
                 values=FakeValues({"bld_kit": {"grade": ["A", "", "  ", None, "B", "A"]}}))
check("blanks and NULLs are dropped and duplicates collapsed, in first appearance order",
      iblank is not None and '"bld_kit"."grade" holds exactly these 2 values: \'A\', \'B\''
      in iblank.instruction, iblank.instruction if iblank else "")
iquote = empty43("SELECT kit_tag FROM \"bld_kit\" WHERE grade = 'Z'",
                 values=FakeValues({"bld_kit": {"grade": ["O'Neil"]}}))
check("a value is shown as a SQL literal, its quote doubled",
      iquote is not None and "holds exactly 1 value: 'O''Neil'" in iquote.instruction,
      iquote.instruction if iquote else "")

# --- 43h. a reader that fails never costs the instruction ----------------------
check("a reader that answers None lists nothing and keeps the advice word for word",
      empty43(sql43, values=FakeValues({})).instruction == generic_empty_text(sql43))
boom43 = FakeValues(KIT_VALUES, raises=RuntimeError("read failed"))
raised43 = None
try:
    got43 = empty43(sql43, values=boom43)
except Exception as e:  # noqa: BLE001 - not raising IS the assertion
    raised43, got43 = e, None
check("a reader that RAISES does not escape, and the advice stands word for word",
      raised43 is None and got43 is not None and got43.instruction == generic_empty_text(sql43),
      repr(raised43))

# --- 43i. the address and the abstention do not read values ---------------------
v_addr = FakeValues({"bld_cam": {"level_code": ["L4"]}})
i_addr = empty43('SELECT cam_tag FROM "bld_cam" WHERE level_code = \'L9\'', cards=(CARD_CAM,),
                 values=v_addr, question=q35)
check("when the question prints a routed identifier, the address wins and nothing is read",
      i_addr is not None and i_addr.detail == sql_loop.IDENTIFIER_EMPTY and v_addr.calls == [],
      (v_addr.calls, i_addr.detail if i_addr else ""))
v_abst = FakeValues(KIT_VALUES)
i_abst = empty43("SELECT kit_tag FROM \"elsewhere\" WHERE kind = 'gadget'", values=v_abst)
check("an abstention (no routed table read) stays a finding and reads nothing",
      i_abst is not None and i_abst.instruction == "" and v_abst.calls == [],
      (v_abst.calls, i_abst.instruction if i_abst else ""))

# --- 43j. the loop: values reach the re-query, and are read only when usable -----
GOOD43 = table_result(["kit_tag", "deck"], [["kt1", "Upper Deck"], ["kt2", "Upper Deck"]],
                      'SELECT kit_tag, deck FROM "bld_kit" WHERE deck = \'Upper Deck\'')
vals_run = FakeValues(KIT_VALUES)
ex = FakeExec([empty_result(sql43), GOOD43])
se = FakeSearch("DOCTEXT")
inv43 = sql_loop.run_sql_investigation(q43, "u1", None, execute=ex, search=se,
                                       routed_cards=[CARD_KIT], max_steps=3,
                                       column_values=vals_run)
check("[empty, good]: two SQL calls, and the second question carries the real values",
      len(ex.questions) == 2 and "Investigation step 2" in ex.questions[1]
      and "holds exactly these 5 values: 'Upper Deck'" in ex.questions[1],
      ex.questions[-1][-400:])
check("the good result is the answer and the documents were not searched",
      "kt1" in inv43.result_text and se.queries == [], (se.queries, inv43.result_text[:120]))
check("read once, for the one step that could use it", vals_run.calls == [("bld_kit", "deck")],
      vals_run.calls)
vals_twice = FakeValues(KIT_VALUES)
ex = FakeExec([empty_result(sql43)] * 2)
sql_loop.run_sql_investigation(q43, "u1", None, execute=ex, search=FakeSearch("DOCTEXT"),
                               routed_cards=[CARD_KIT], max_steps=3, column_values=vals_twice)
check("[empty, empty]: the repeat guard ends it after two, and step 2's EMPTY is never "
      "read for - an instruction the guard will not send is not worth a read",
      len(ex.questions) == 2 and vals_twice.calls == [("bld_kit", "deck")],
      (len(ex.questions), vals_twice.calls))
vals_last = FakeValues(KIT_VALUES)
ex = FakeExec(["SQL query failed: Binder Error: nope\n\nGenerated SQL: `SELECT kit_tag "
               "FROM \"bld_kit\"`", empty_result(sql43)])
sql_loop.run_sql_investigation(q43, "u1", None, execute=ex, search=FakeSearch("DOCTEXT"),
                               routed_cards=[CARD_KIT], max_steps=2, column_values=vals_last)
check("an EMPTY on the LAST step is not read for either - no step is left to use it",
      len(ex.questions) == 2 and vals_last.calls == [], (len(ex.questions), vals_last.calls))
vals_off = FakeValues(KIT_VALUES)
ex_on = FakeExec([empty_result(sql43)])
inv_off = sql_loop.run_sql_investigation(q43, "u1", None, execute=ex_on,
                                         search=FakeSearch("DOCTEXT"), routed_cards=[CARD_KIT],
                                         max_steps=1, column_values=vals_off)
ex_none = FakeExec([empty_result(sql43)])
inv_none = sql_loop.run_sql_investigation(q43, "u1", None, execute=ex_none,
                                          search=FakeSearch("DOCTEXT"), routed_cards=[CARD_KIT],
                                          max_steps=1)
check("max_steps=1: the reader is never called", vals_off.calls == [], vals_off.calls)
check("and the result is byte-identical to a run with no reader at all",
      inv_off.result_text == inv_none.result_text and inv_off.steps == inv_none.steps
      and ex_on.questions == ex_none.questions, (inv_off.result_text, inv_none.result_text))
vals_boom = FakeValues(KIT_VALUES, raises=RuntimeError("read failed"))
ex = FakeExec([empty_result(sql43), GOOD43])
inv_boom = sql_loop.run_sql_investigation(q43, "u1", None, execute=ex, search=FakeSearch(),
                                          routed_cards=[CARD_KIT], max_steps=3,
                                          column_values=vals_boom)
check("a raising reader inside the loop: the re-query still runs, with today's advice",
      len(ex.questions) == 2 and generic_empty_text(sql43) in ex.questions[1]
      and "kt1" in inv_boom.result_text, ex.questions[-1][-300:])
check("the module stays generic and bounded after the values code",
      not _re.search(r"\bwhile\b", Path(sql_loop.__file__).read_text(encoding="utf-8")))

# ---------------------------------------------------------------------------
print("\n44. The trailer counts the steps and names no issue kind (spec-fix10)")
# ---------------------------------------------------------------------------
# The writer used to read "INVESTIGATION - steps: 2; issues: IDENTIFIER_MISSING" under a
# result that held every value its question asked for, and answered that the records had
# no such entry: an issue name ending in MISSING, printed under a complete answer, read as
# a verdict on it. The kinds are bookkeeping for the trace and the events, not evidence for
# the answer, so the trailer now counts the steps and nothing else.
ex = FakeExec([empty_result(sql2), noident, good])
inv44 = sql_loop.run_sql_investigation(q2, "u1", None, execute=ex, search=FakeSearch(),
                                       routed_cards=[CARD_LIST], max_steps=3)
check("the fixture really raised two issue kinds on the way", len(ex.questions) == 3
      and [s["issue"] for s in inv44.steps] == [None, sql_loop.EMPTY, sql_loop.IDENTIFIER_MISSING],
      [s["issue"] for s in inv44.steps])
try:
    trailer44 = sql_loop.investigation_trailer(3)
except TypeError as e:  # the old signature also took the issue kinds
    trailer44 = f"TypeError: {e}"
check("investigation_trailer takes the step count and prints nothing else",
      trailer44 == "INVESTIGATION - steps: 3", trailer44)
check("the final text ends with exactly that line",
      inv44.result_text.endswith("\n\nINVESTIGATION - steps: 3"), inv44.result_text[-120:])
named44 = [k for k in sql_loop.ISSUE_ORDER + (sql_loop.IDENTIFIER_EMPTY,) if k in inv44.result_text]
check("no issue kind is named anywhere in the text the answer writer reads",
      named44 == [] and "issues:" not in inv44.result_text, named44)
check("the kinds stay with the investigation, for the trace and the events",
      inv44.issues == [sql_loop.EMPTY, sql_loop.IDENTIFIER_MISSING], inv44.issues)

# ===========================================================================
# TASK T7 (2026-10-01) — a contact- or warranty-shaped question with a non-empty
# result gets the SAME one-call document cross-check a count question gets.
# ===========================================================================

# ---------------------------------------------------------------------------
print("\n45. T7 — a who/phone question with a non-empty result raises LETTER_CROSSCHECK")
# ---------------------------------------------------------------------------
q45 = "Who is the contact for the units, and what is their phone number?"
r45 = table_result(["name", "phone"], [["A Person", "0551234567"]],
                   'SELECT name, phone FROM "bld_units"')
iss45 = sql_loop.inspect_result(r45, q45, [CARD_LIST])
check("LETTER_CROSSCHECK and nothing else",
      [i.kind for i in iss45] == [sql_loop.LETTER_CROSSCHECK], [i.kind for i in iss45])
check("it carries no re-query instruction", iss45 and iss45[0].instruction == "", iss45)
check("so the loop finds nothing to re-query", sql_loop.first_requery_issue(iss45) is None)
check("asks_contact reads the question as contact-shaped", sql_loop.asks_contact(q45))
check("is_letter_shaped agrees", sql_loop.is_letter_shaped(q45))

# ---------------------------------------------------------------------------
print("\n46. T7 — a warranty question with a non-empty result raises LETTER_CROSSCHECK")
# ---------------------------------------------------------------------------
q46 = "Is the unit still under warranty?"
r46 = table_result(["unit_tag", "lifespan"], [["U-1", "2 years"]],
                   'SELECT unit_tag, lifespan FROM "bld_units"')
iss46 = sql_loop.inspect_result(r46, q46, [CARD_LIST])
check("LETTER_CROSSCHECK and nothing else",
      [i.kind for i in iss46] == [sql_loop.LETTER_CROSSCHECK], [i.kind for i in iss46])
check("asks_warranty reads the question as warranty-shaped", sql_loop.asks_warranty(q46))
check("asks_contact does NOT (no contact words in it)", not sql_loop.asks_contact(q46))
check("is_letter_shaped agrees", sql_loop.is_letter_shaped(q46))

# ---------------------------------------------------------------------------
print("\n47. T7-R1 — a question that is BOTH count- and letter-shaped gets the count "
      "cross-check only")
# ---------------------------------------------------------------------------
q47 = "How many units are still under warranty?"
r47 = table_result(["n"], [[4]], 'SELECT COUNT(*) AS n FROM "bld_units"')
check("the fixture really is both shapes",
      sql_loop.asks_count(q47) and sql_loop.is_letter_shaped(q47))
iss47 = sql_loop.inspect_result(r47, q47, [CARD_LIST])
check("COUNT_CROSSCHECK fires and LETTER_CROSSCHECK never does",
      [i.kind for i in iss47] == [sql_loop.COUNT_CROSSCHECK], [i.kind for i in iss47])

# ---------------------------------------------------------------------------
print("\n48. T7-R4 — an EMPTY or FAILED result is unchanged: the document fallback only, "
      "never a letter cross-check")
# ---------------------------------------------------------------------------
q48 = "Who is the contact, and what is the phone number?"
iss48a = sql_loop.inspect_result(
    empty_result('SELECT phone FROM "bld_units" WHERE unit_tag = \'X\''), q48, [CARD_LIST])
check("EMPTY fires, LETTER_CROSSCHECK does not", sql_loop.EMPTY in [i.kind for i in iss48a]
      and sql_loop.LETTER_CROSSCHECK not in [i.kind for i in iss48a], [i.kind for i in iss48a])
iss48b = sql_loop.inspect_result("SQL query failed: boom\n\nGenerated SQL: `SELECT 1`",
                                 q48, [CARD_LIST])
check("FAILED_SQL fires, LETTER_CROSSCHECK does not",
      sql_loop.FAILED_SQL in [i.kind for i in iss48b]
      and sql_loop.LETTER_CROSSCHECK not in [i.kind for i in iss48b], [i.kind for i in iss48b])

# ---------------------------------------------------------------------------
print("\n49. T7 — a plain non-letter question raises neither cross-check kind")
# ---------------------------------------------------------------------------
r49 = table_result(["board", "level"], [["B-1", "2"], ["B-2", "2"]],
                   'SELECT board, level FROM "bld_units" WHERE level = \'2\'')
iss49 = sql_loop.inspect_result(r49, "which boards are on level 2", [CARD_LIST])
check("no LETTER_CROSSCHECK, no COUNT_CROSSCHECK",
      not any(i.kind in (sql_loop.LETTER_CROSSCHECK, sql_loop.COUNT_CROSSCHECK)
              for i in iss49), [i.kind for i in iss49])

# ---------------------------------------------------------------------------
print("\n50. T7 — word boundaries hold: 'whole' and 'phoneme'-style partial words never fire")
# ---------------------------------------------------------------------------
check("'whole' is not read as 'who'",
      not sql_loop.asks_contact("tell me about the whole building"))
check("'phoneme' is not read as 'phone'", not sql_loop.asks_contact("what is a phoneme"))
check("a real 'phone' question still fires", sql_loop.asks_contact("what is the phone number"))
check("a real 'who' question still fires", sql_loop.asks_contact("who installed this"))

# ---------------------------------------------------------------------------
print("\n51. T7 — ISSUE_ORDER and APPENDED_CROSSCHECKS carry the new kind")
# ---------------------------------------------------------------------------
check("LETTER_CROSSCHECK is in ISSUE_ORDER (inspect_result sorts on it)",
      sql_loop.LETTER_CROSSCHECK in sql_loop.ISSUE_ORDER)
check("APPENDED_CROSSCHECKS names both appended kinds, in this order",
      sql_loop.APPENDED_CROSSCHECKS == (sql_loop.COUNT_CROSSCHECK, sql_loop.LETTER_CROSSCHECK),
      sql_loop.APPENDED_CROSSCHECKS)

# ---------------------------------------------------------------------------
print("\n52. T7 — the letter cross-check: one retrieval call, appended under its own "
      "heading, the table result kept whole before it")
# ---------------------------------------------------------------------------
ex = FakeExec([r45])
se = FakeSearch("LETTER-A\n\n---\n\nLETTER-B\n\n---\n\nLETTER-C\n\n---\n\nLETTER-D")
inv52 = sql_loop.run_sql_investigation(q45, "u1", None, execute=ex, search=se,
                                       routed_cards=[CARD_LIST], max_steps=3)
check("only one SQL call — a letter question is never re-queried",
      len(ex.questions) == 1, ex.questions)
check("exactly one retrieval call", len(se.queries) == 1, se.queries)
check("the SQL result is kept whole, and comes before the cross-check",
      r45 in inv52.result_text
      and inv52.result_text.index(r45) < inv52.result_text.index(sql_loop.LETTER_CROSSCHECK_HEADING),
      inv52.result_text)
check("the excerpts are labelled under LETTER_CROSSCHECK_HEADING",
      sql_loop.LETTER_CROSSCHECK_HEADING in inv52.result_text, inv52.result_text[-400:])
check("the heading says MAY NAME, worded exactly as spec'd",
      sql_loop.LETTER_CROSSCHECK_HEADING == "Cross-check: document excerpts that may name "
      "this party, contact or warranty (top matches, may be unrelated)",
      sql_loop.LETTER_CROSSCHECK_HEADING)
check("at most three excerpts are kept",
      "LETTER-C" in inv52.result_text and "LETTER-D" not in inv52.result_text,
      inv52.result_text[-400:])
check("Investigation.crosscheck carries the excerpt text",
      inv52.crosscheck and "LETTER-A" in inv52.crosscheck, inv52.crosscheck)
check("the trailer counts the step; LETTER_CROSSCHECK never prints, but stays on "
      "Investigation.issues",
      inv52.result_text.endswith("\n\nINVESTIGATION - steps: 1")
      and sql_loop.LETTER_CROSSCHECK not in inv52.result_text
      and sql_loop.LETTER_CROSSCHECK in inv52.issues, inv52.result_text[-160:])

# no search callable at all -> degrade quietly, exactly like the count cross-check
ex = FakeExec([r45])
inv52b = sql_loop.run_sql_investigation(q45, "u1", None, execute=ex, search=None,
                                        routed_cards=[CARD_LIST], max_steps=3)
check("no search callable -> no cross-check, no crash", inv52b.crosscheck is None
      and sql_loop.LETTER_CROSSCHECK_HEADING not in inv52b.result_text)

# ---------------------------------------------------------------------------
print("\n53. T7-R3 — max_steps=1 raises no cross-check of any kind, byte-identical to today")
# ---------------------------------------------------------------------------
ex = FakeExec([r45])
se = FakeSearch()
inv53 = sql_loop.run_sql_investigation(q45, "u1", None, execute=ex, search=se,
                                       routed_cards=[CARD_LIST], max_steps=1)
check("exactly one execute call", len(ex.questions) == 1, ex.questions)
check("no retrieval call at max_steps=1", se.queries == [], se.queries)
check("the result text is byte-identical to the SQL result", inv53.result_text == r45,
      inv53.result_text[:160])
check("no INVESTIGATION trailer either", "INVESTIGATION" not in inv53.result_text)
check("crosscheck is None", inv53.crosscheck is None)

# ---------------------------------------------------------------------------
print("\n54. T7 — the event sequence for a letter-shaped question matches a count "
      "question's, kind for kind")
# ---------------------------------------------------------------------------
ex = FakeExec([r45])
events54 = list(sql_loop.iter_sql_investigation(q45, "u1", None, execute=ex, search=FakeSearch(),
                                                routed_cards=[CARD_LIST], max_steps=3))
check("step, step_done, crosscheck, final",
      [k for k, _ in events54] == ["step", "step_done", "crosscheck", "final"],
      [k for k, _ in events54])
check("the crosscheck event names LETTER_CROSSCHECK",
      events54[2][1].get("kind") == sql_loop.LETTER_CROSSCHECK, events54[2][1])
check("its detail never says SQL failed or falling back (it is an appended cross-check, "
      "not a fallback)",
      "fail" not in events54[2][1].get("detail", "").lower()
      and "falling back" not in events54[2][1].get("detail", "").lower(), events54[2][1])

# ===========================================================================
# SPEC-FIX3 PART (c), 2026-10-01 - AN EMPTY RESULT THAT FILTERED A STOREY.
#
# Measured on the goal-function run of 2026-09-30: a storey filtered on printed floor text
# came back empty, and the generic advice - filter by the printed NAME with ILIKE - sent the
# writer back to printed text. It guessed another spelling, matched nothing again, or
# WIDENED the filter to other codes until something matched, and stated that figure. The
# data side stamps a level-code column beside every location id, so the re-query is told to
# re-express the storey through it, keep the filter, and never widen it.
#
# T11a-R6: the stamped level code is NOT on the cards (so routing cannot move), so the
# loop also reads the LOADED table's columns - through the reader's `columns` capability -
# both to place a filtered column no card lists and to know whether a table has a level
# code at all. The new instruction fires only where it can be obeyed: on a table with a
# level-code column, its card's or its loaded table's.
# ===========================================================================
print("\n55. An EMPTY result that filtered a storey gets the level code instruction (fix3 c)")


class FakeLoaded(FakeValues):
    """A reader that can also list a loaded table's columns, as
    `sql_tool.column_value_reader` can: `columns(table) -> [column, ...] | None`."""
    def __init__(self, tables, listed, raises=None):
        super().__init__(tables, raises)
        self.listed = listed
        self.column_calls = []

    def columns(self, table):
        self.column_calls.append(table)
        cols = self.listed.get(table)
        return None if cols is None else list(cols)


# The stock table's level code is on the LOADED table only - like every stamped table - and
# its card lists a location key; the spots table's card lists its level code; the plain
# table has no level code anywhere.
CARD_STOCK = {"table": "bld_stock", "identifier_column": "stock_tag",
              "columns": ["stock_tag", "floor_text", "kind", "notes", "location_id"]}
CARD_SPOTS = {"table": "bld_spots", "identifier_column": "spot_ref",
              "columns": ["spot_ref", "display_name", "kind", "qty", "level_code"]}
CARD_PLAIN = {"table": "bld_plain", "identifier_column": "plain_tag",
              "columns": ["plain_tag", "floor_text", "kind"]}
LISTED55 = {"bld_stock": CARD_STOCK["columns"] + ["grade", "level_code"],
            "bld_spots": list(CARD_SPOTS["columns"]),
            "bld_plain": list(CARD_PLAIN["columns"])}
VALUES55 = {"bld_stock": {"floor_text": ["Floor Two", "Floor Nine North", "Floor Ten"],
                          "kind": ["gizmo", "doohickey"], "grade": ["V7", "V8"],
                          "level_code": ["Z2", "Z9"], "notes": ["kept"]},
            "bld_spots": {"display_name": ["Quiet Nook Nine", "Long Hall Two"],
                          "kind": ["gizmo"], "level_code": ["Z2", "Z9"]},
            "bld_plain": {"floor_text": ["Floor Two", "Floor Nine North"],
                          "kind": ["gizmo"]}}
q55 = "which stock items are on the ninth floor?"


def empty55(sql, cards=(CARD_STOCK,), question=q55, reader="loaded"):
    """The first issue raised on an empty result of `sql`, and the reader it was handed."""
    if reader == "loaded":
        reader = FakeLoaded(VALUES55, LISTED55)
    found = sql_loop.inspect_result(empty_result(sql), question, list(cards),
                                    column_values=reader)
    return (found[0] if found else None), reader


_KEYS_DETAIL = getattr(sql_loop, "PLACE_KEYS_EMPTY", "PLACE_KEYS_EMPTY")

# --- 55a. printed floor text on a table whose loaded columns hold a level code -------------
sql55a = 'SELECT stock_tag FROM "bld_stock" WHERE floor_text = \'Floor Nine\''
e55a, rd55a = empty55(sql55a)
ins55a = e55a.instruction if e55a else ""
check("PLACE_KEYS_EMPTY is a DETAIL, not a kind: the priority order does not move",
      hasattr(sql_loop, "PLACE_KEYS_EMPTY") and sql_loop.PLACE_KEYS_EMPTY not in sql_loop.ISSUE_ORDER)
check("the kind is still EMPTY and the loop acts on it",
      e55a is not None and e55a.kind == sql_loop.EMPTY and bool(ins55a))
check("its detail says the level code instruction fired", e55a is not None
      and e55a.detail == _KEYS_DETAIL, e55a.detail if e55a else "")
check("it quotes the SQL that returned nothing, and says so",
      sql55a in ins55a and "returned no rows" in ins55a, ins55a)
check("it re-expresses the storey through a level code column - the table's own, or the "
      "places table's through the location key",
      "level code column" in ins55a and "places table" in ins55a and "location key" in ins55a,
      ins55a)
check("never on printed floor wording or a place name",
      "never on printed floor wording or a place name" in ins55a, ins55a)
check("it KEEPS the filter, to the storey the question names",
      "keep that filter to the storey the question names" in ins55a.lower(), ins55a)
check("and never widens it: every 'widen' it prints is a 'never widen'",
      "never widen it to further levels or codes" in ins55a
      and len(_re.findall(r"widen", ins55a)) == len(_re.findall(r"never widen", ins55a)),
      ins55a)
check("a storey with no matching rows is the answer",
      "that storey has no such rows, and that is the answer" in ins55a, ins55a)
check("it does not also carry the generic printed-name advice it replaces",
      "by its printed NAME" not in ins55a and "with ILIKE" not in ins55a, ins55a)
check("no hyphenated word, so the router reads no coded prefix out of it",
      not HYPHENATED.search(ins55a.replace(sql55a, ""))
      and _tr._question_prefixes(ins55a.replace(sql55a, "")) == set(),
      (HYPHENATED.findall(ins55a), _tr._question_prefixes(ins55a)))
check("the printed floor column's values are NOT listed - they are what it must not filter on",
      '"bld_stock"."floor_text"' not in ins55a and "Floor Nine North" not in ins55a, ins55a)
check("the loaded table's columns were asked for, so the stamped level code was seen",
      "bld_stock" in rd55a.column_calls, rd55a.column_calls)

# --- 55b. another filter on the same query still gets its real values -------------------
e55b, _ = empty55('SELECT stock_tag FROM "bld_stock" WHERE floor_text = \'Floor Nine\' '
                  "AND kind = 'gizmoo'")
ins55b = e55b.instruction if e55b else ""
check("the other filtered column's values are listed after the instruction",
      e55b is not None and e55b.detail == _KEYS_DETAIL
      and '"bld_stock"."kind" holds exactly these 2 values: \'gizmo\', \'doohickey\''
      in ins55b and '"bld_stock"."floor_text"' not in ins55b, ins55b)

# --- 55c. a place-name column searched for a storey -------------------------------------
sql55c = 'SELECT spot_ref FROM "bld_spots" WHERE display_name ILIKE \'%Level Nine%\''
e55c, _ = empty55(sql55c, cards=(CARD_SPOTS,), question="what is on level nine?")
check("a display-name column searched for a storey's words gets the level code instruction",
      e55c is not None and e55c.detail == _KEYS_DETAIL, e55c.instruction if e55c else "")
e55c0, _ = empty55(sql55c, cards=(CARD_SPOTS,), question="what is on level nine?", reader=None)
check("its card lists the level code, so it fires with no reader at all",
      e55c0 is not None and e55c0.detail == _KEYS_DETAIL, e55c0.instruction if e55c0 else "")

# --- 55d. T11a-R1: a place-name column naming ONE place keeps today's advice ------------
sql55d = 'SELECT spot_ref FROM "bld_spots" WHERE display_name ILIKE \'%Quiet Nook%\''
e55d, _ = empty55(sql55d, cards=(CARD_SPOTS,), question="what is in the quiet nook?",
                  reader=None)
check("T11a-R1: a place named by its printed name keeps the generic advice, word for word",
      e55d is not None and e55d.instruction == generic_empty_text(sql55d),
      e55d.instruction if e55d else "")

# --- 55e. the level code itself, filtered and empty: kept, never widened; R6 values -------
sql55e = 'SELECT stock_tag FROM "bld_stock" WHERE level_code = \'Z7\''
e55e, _ = empty55(sql55e, question="which stock items are on storey seven?")
ins55e = e55e.instruction if e55e else ""
check("an empty filter on the level code itself gets the same instruction - keep it, never "
      "widen it", e55e is not None and e55e.detail == _KEYS_DETAIL
      and "never widen" in ins55e, ins55e)
check("T11a-R6: its real values are listed although no card lists the column - the loaded "
      "table has it",
      '"bld_stock"."level_code" holds exactly these 2 values: \'Z2\', \'Z9\'' in ins55e, ins55e)

# --- 55f. T11a-R6 for any column: placed by the loaded table, listed under today's advice --
sql55f = 'SELECT stock_tag FROM "bld_stock" WHERE grade = \'V9\''
e55f, rd55f = empty55(sql55f, question="which stock items are graded nine?")
check("T11a-R6: an unqualified column only the LOADED table has is placed and its values "
      "listed, under today's advice",
      e55f is not None and e55f.instruction.startswith(generic_empty_text(sql55f))
      and '"bld_stock"."grade" holds exactly these 2 values: \'V7\', \'V8\'' in e55f.instruction,
      e55f.instruction if e55f else "")
plain55f = FakeValues(VALUES55)
e55f0, _ = empty55(sql55f, question="which stock items are graded nine?", reader=plain55f)
check("a reader that cannot list columns places nothing new: today's advice, word for word, "
      "and nothing read", e55f0 is not None
      and e55f0.instruction == generic_empty_text(sql55f) and plain55f.calls == [],
      (plain55f.calls, e55f0.instruction if e55f0 else ""))
check("a card that lists the column still wins the placement - no columns call needed",
      empty55('SELECT stock_tag FROM "bld_stock" WHERE kind = \'gizmoo\'')[1].column_calls
      == [], "columns asked for a card-listed column")

# --- 55g. no level code anywhere: the instruction cannot be obeyed, so today's stands ----
sql55g = 'SELECT plain_tag FROM "bld_plain" WHERE floor_text = \'Floor Nine\''
e55g, _ = empty55(sql55g, cards=(CARD_PLAIN,))
check("a table with no level code, on its card or loaded, keeps today's advice and lists "
      "its printed values",
      e55g is not None and e55g.detail != _KEYS_DETAIL
      and e55g.instruction.startswith(generic_empty_text(sql55g))
      and '"bld_plain"."floor_text" holds exactly these 2 values' in e55g.instruction,
      e55g.instruction if e55g else "")

# --- 55h. a storey word inside an item's own text is not a storey ------------------------
sql55h = 'SELECT stock_tag FROM "bld_stock" WHERE notes ILIKE \'%floor mat%\''
e55h, _ = empty55(sql55h, question="which stock items are floor mats?")
check("a floor word in an item or notes column is no storey filter: today's advice",
      e55h is not None and e55h.detail != _KEYS_DETAIL
      and e55h.instruction.startswith(generic_empty_text(sql55h)),
      e55h.instruction if e55h else "")

# --- 55i. a sub-SELECT's storey filter, qualified ----------------------------------------
sql55i = ('SELECT stock_tag FROM "bld_stock" WHERE stock_tag IN (SELECT s.stock_tag FROM '
          '"bld_stock" s WHERE s.floor_text = \'Floor Nine\')')
e55i, _ = empty55(sql55i)
check("a qualified storey filter inside a sub-SELECT is read too",
      e55i is not None and e55i.detail == _KEYS_DETAIL, e55i.instruction if e55i else "")

# --- 55j/k. the address still wins; an abstention stays a finding -------------------------
e55j, rd55j = empty55('SELECT cam_tag FROM "bld_cam" WHERE floor_text = \'Floor Nine\'',
                      cards=(CARD_CAM,), question=q35)
check("when the question prints a routed identifier, the address wins and nothing is read",
      e55j is not None and e55j.detail == sql_loop.IDENTIFIER_EMPTY and rd55j.calls == []
      and rd55j.column_calls == [], (e55j.detail if e55j else "", rd55j.calls))
e55k, rd55k = empty55('SELECT x FROM "elsewhere" WHERE floor_text = \'Floor Nine\'')
check("an abstention (no routed table read) stays a finding and reads nothing",
      e55k is not None and e55k.instruction == "" and rd55k.calls == []
      and rd55k.column_calls == [], (e55k.instruction if e55k else "", rd55k.calls))

# --- 55l. the shape rules, one by one -----------------------------------------------------
_lc = getattr(sql_loop, "is_level_code_column", None)
check("is_level_code_column: a level code by its words, in any case or spacing",
      _lc is not None and _lc("level_code") and _lc("Level Code") and _lc("floor_level_code"))
check("is_level_code_column: a level name, a bare level, a floor code read off a tag are not",
      _lc is not None and not _lc("level_name") and not _lc("level")
      and not _lc("story_code_from_tag"))
_st = getattr(sql_loop, "_storey_text", None)
check("_storey_text: a floor or storey column, any literal",
      _st is not None and _st("floor", ["B%"]) and _st("floor_as_printed", ["9X"])
      and _st("storey_text", ["Nine"]))
check("_storey_text: a place-name or printed column only with a storey's words in the literal",
      _st is not None and _st("display_name", ["%Level Nine%"]) and _st("level_name", ["Ground"])
      and _st("spot_area", ["%Ground%"]) and _st("thing_as_printed", ["Roof"])
      and not _st("display_name", ["%Quiet Nook%"]) and not _st("floor_area", ["%Nook%"]))
check("_storey_text: never the level code, never an id, never an item's own text",
      _st is not None and not _st("level_code", ["Z9"]) and not _st("place_id", ["%Level 9%"])
      and not _st("notes", ["%Level Nine%"]) and not _st("kind", ["floor"]))

# --- 55m. the loop: the instruction reaches the re-query, and the guard still holds -------
GOOD55 = table_result(["stock_tag", "level_code"], [["st1", "Z9"], ["st2", "Z9"]],
                      'SELECT stock_tag, level_code FROM "bld_stock" WHERE level_code = \'Z9\'')
ex = FakeExec([empty_result(sql55a), GOOD55])
inv55 = sql_loop.run_sql_investigation(q55, "u1", None, execute=ex, search=FakeSearch(),
                                       routed_cards=[CARD_STOCK], max_steps=3,
                                       column_values=FakeLoaded(VALUES55, LISTED55))
check("[empty, good]: two SQL calls, and the second question carries the level code "
      "instruction", len(ex.questions) == 2 and "Investigation step 2" in ex.questions[1]
      and "level code column" in ex.questions[1] and "never widen" in ex.questions[1],
      ex.questions[-1][-500:])
check("the steps say EMPTY drove the re-query",
      [s["issue"] for s in inv55.steps] == [None, sql_loop.EMPTY], inv55.steps)
ex = FakeExec([empty_result(sql55a)] * 3)
se55 = FakeSearch("DOCTEXT")
inv55b = sql_loop.run_sql_investigation(q55, "u1", None, execute=ex, search=se55,
                                        routed_cards=[CARD_STOCK], max_steps=3,
                                        column_values=FakeLoaded(VALUES55, LISTED55))
check("[empty, empty]: the repeat guard stops after two, then the documents",
      len(ex.questions) == 2 and se55.queries == [q55]
      and sql_loop.EMPTY_FALLBACK_PREFIX in inv55b.result_text, (len(ex.questions), se55.queries))
rd_off = FakeLoaded(VALUES55, LISTED55)
ex_on = FakeExec([empty_result(sql55a)])
inv_off55 = sql_loop.run_sql_investigation(q55, "u1", None, execute=ex_on, search=FakeSearch("D"),
                                           routed_cards=[CARD_STOCK], max_steps=1,
                                           column_values=rd_off)
ex_none = FakeExec([empty_result(sql55a)])
inv_none55 = sql_loop.run_sql_investigation(q55, "u1", None, execute=ex_none,
                                            search=FakeSearch("D"), routed_cards=[CARD_STOCK],
                                            max_steps=1)
check("max_steps=1: nothing read, and byte-identical to a run with no reader",
      rd_off.calls == [] and rd_off.column_calls == []
      and inv_off55.result_text == inv_none55.result_text and inv_off55.steps == inv_none55.steps,
      (rd_off.calls, rd_off.column_calls))
boom55 = FakeLoaded(VALUES55, LISTED55, raises=RuntimeError("read failed"))
boom55.columns = lambda table: (_ for _ in ()).throw(RuntimeError("list failed"))
raised55 = None
try:
    e55z, _ = empty55(sql55e, reader=boom55)
except Exception as e:  # noqa: BLE001 - not raising IS the assertion
    raised55, e55z = e, None
check("a reader whose reads and column lists RAISE never escapes; the level code filter is "
      "still kept, with nothing listed", raised55 is None and e55z is not None
      and e55z.detail == _KEYS_DETAIL and sql_loop.VALUES_LEAD not in e55z.instruction,
      (repr(raised55), e55z.instruction if e55z else ""))

# ===========================================================================
# FIX ROUND 1 (task review): two filters misread as a storey, and an answer claimed too early.
# ===========================================================================
print("\n56. A resolution enum and a measure are no storey; 'that is the answer' only alone")

# --- 56a. what is NOT storey text ------------------------------------------------------
check("RED: a location-resolution enum is no storey text, though its value spells a level",
      _st is not None and not _st("location_resolution", ["level_block"])
      and not _st("linked_location_resolution", ["level"]))
check("RED: a measure whose name merely holds 'level' is no storey text, whatever the literal",
      _st is not None and not _st("lumen_level_pct", ["50"])
      and not _st("lumen_level_pct", ["%Level 2%"]))
check("storey columns still match on their whole name parts",
      _st is not None and _st("storey_block_as_printed", ["9N"]) and _st("level", ["Deck North"])
      and _st("story_code_from_tag", ["9F"]) and _st("storey_text", ["Nine"]))
check("and a place-name column still needs a storey's words in its literal",
      _st is not None and _st("spot_name", ["%Level Nine%"]) and not _st("spot_name", ["%Nook%"])
      and _st("thing_as_printed", ["Ground Floor"]))
CARD_RES = dict(CARD_STOCK, columns=CARD_STOCK["columns"] + ["location_resolution"])
sql56a = 'SELECT stock_tag FROM "bld_stock" WHERE location_resolution = \'level_block\''
e56a, _ = empty55(sql56a, cards=(CARD_RES,))
check("RED: an empty filter on the resolution enum keeps today's advice",
      e56a is not None and e56a.detail != _KEYS_DETAIL
      and e56a.instruction.startswith(generic_empty_text(sql56a)),
      e56a.instruction if e56a else "")
CARD_LUMEN = dict(CARD_STOCK, columns=CARD_STOCK["columns"] + ["lumen_level_pct"])
sql56b = 'SELECT stock_tag FROM "bld_stock" WHERE lumen_level_pct = \'50\''
e56b, _ = empty55(sql56b, cards=(CARD_LUMEN,))
check("RED: an empty filter on a measure column keeps today's advice",
      e56b is not None and e56b.detail != _KEYS_DETAIL
      and e56b.instruction.startswith(generic_empty_text(sql56b)),
      e56b.instruction if e56b else "")

# --- 56b. 'that is the answer' only when the storey is the ONLY filter ------------------
ANSWER = "that storey has no such rows, and that is the answer"
OTHERS = "The remaining filters may be what matched nothing"
e56c, _ = empty55(sql55a)
check("the storey filter alone: an empty storey is the answer",
      e56c is not None and ANSWER in e56c.instruction and OTHERS not in e56c.instruction,
      e56c.instruction if e56c else "")
for alone in ('SELECT stock_tag FROM "bld_stock" WHERE level_code IN (\'Z1\', \'Z2\')',
              'SELECT stock_tag FROM "bld_stock" WHERE floor_text = \'Floor Nine\' '
              "OR floor_text = 'Floor Ten'",
              'SELECT stock_tag FROM "bld_stock" WHERE location_id IN (SELECT location_id '
              'FROM "bld_places" WHERE level_code = \'Z3\')'):
    e, _ = empty55(alone)
    check(f"several storey filters and nothing else are still alone: {alone[38:]}",
          e is not None and e.detail == _KEYS_DETAIL and ANSWER in e.instruction,
          e.instruction if e else "")
sql56d = ('SELECT stock_tag FROM "bld_stock" WHERE floor_text = \'Floor Nine\' '
          "AND kind = 'gizmoo'")
e56d, _ = empty55(sql56d)
ins56d = e56d.instruction if e56d else ""
check("RED: with another filter beside it, the storey is kept and never widened - but the "
      "empty result is not called the answer",
      e56d is not None and e56d.detail == _KEYS_DETAIL and ANSWER not in ins56d
      and "never widen it to further levels or codes" in ins56d, ins56d)
check("RED: the writer is pointed at the other filters, whose values are listed",
      OTHERS in ins56d and '"bld_stock"."kind" holds exactly these 2 values' in ins56d
      and ins56d.index(OTHERS) < ins56d.index(sql_loop.VALUES_LEAD), ins56d)
sql56e = ('SELECT SUM(qty) FROM "bld_stock" WHERE kind ILIKE \'%widget%\' AND location_id IN '
          '(SELECT location_id FROM "bld_places" WHERE level_code = \'Z3\')')
e56e, _ = empty55(sql56e, question="how many widgets are on storey three?")
check("RED: a right level code inside a sub-SELECT and a wrong item filter: not the answer",
      e56e is not None and e56e.detail == _KEYS_DETAIL and ANSWER not in e56e.instruction
      and OTHERS in e56e.instruction, e56e.instruction if e56e else "")
sql56f = 'SELECT stock_tag FROM "bld_stock" WHERE level_code = \'Z7\' AND qty > 5'
e56f, _ = empty55(sql56f)
check("RED: a condition that is no literal filter counts as another filter too",
      e56f is not None and ANSWER not in e56f.instruction and OTHERS in e56f.instruction,
      e56f.instruction if e56f else "")
check("neither variant carries a hyphenated word or a coded prefix",
      all(not HYPHENATED.search(i.replace(s, "")) and _tr._question_prefixes(i.replace(s, "")) == set()
          for i, s in ((ins56d, sql56d), (e56c.instruction if e56c else "", sql55a))))

print("\nALL PASS" if not FAILS else f"\n{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
