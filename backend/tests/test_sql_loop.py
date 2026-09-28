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
check("the trailer names the steps and the issue",
      "INVESTIGATION - steps: 2; issues: EMPTY" in inv.result_text, inv.result_text[-160:])

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
check("the trailer counts the steps", "INVESTIGATION - steps: 2;" in inv.result_text,
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
check("the trailer names the cross-check",
      "INVESTIGATION - steps: 1; issues: COUNT_CROSSCHECK" in inv.result_text, inv.result_text[-160:])

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
# F9 — a finding that is not a re-query still reaches the trailer and the Investigation
ex = FakeExec([trunc])
inv = sql_loop.run_sql_investigation("list all the units", "u1", None, execute=ex,
                                     search=FakeSearch(), routed_cards=[CARD_LIST], max_steps=3)
check("TRUNCATED_NO_SHAPE reaches Investigation.issues",
      sql_loop.TRUNCATED_NO_SHAPE in inv.issues, inv.issues)
check("and the trailer", "TRUNCATED_NO_SHAPE" in inv.result_text, inv.result_text[-160:])
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
          table_result(["total_kw"], [[5785.87]], 'SELECT SUM(kw) AS total_kw FROM "bld_units"'),
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
check("and the trailer", "NARROW_SELECT" in inv.result_text, inv.result_text[-160:])

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
check("IDENTIFIER_EMPTY is a DETAIL, not a kind - the priority order is unchanged",
      getattr(sql_loop, "IDENTIFIER_EMPTY", None) not in sql_loop.ISSUE_ORDER
      and sql_loop.ISSUE_ORDER == (sql_loop.FAILED_SQL, sql_loop.EMPTY,
                                   sql_loop.IDENTIFIER_MISSING, sql_loop.NARROW_SELECT,
                                   sql_loop.TRUNCATED_NO_SHAPE, sql_loop.COUNT_CROSSCHECK),
      sql_loop.ISSUE_ORDER)

# ---------------------------------------------------------------------------
print("\n36. No prefix match, no coded token: the generic EMPTY text stands, word for word")
# ---------------------------------------------------------------------------
def generic_empty_text(sql):
    """The wording `_empty_issue` has always used, written out here so a change to it
    turns this check red rather than sliding through."""
    return ("The previous query `" + sql + "` returned no rows. Re-read the column "
            "samples; filter a place or entity by its printed NAME on the name column "
            "with ILIKE '%…%', never by a built id; if the question names a place, "
            "use the one-row-per-place-and-item table.")

# same question shape, a card whose declared prefixes do NOT cover it
CARD_OTHER = dict(CARD_CAM, identifier_prefixes=["ZZZ"])
iss36 = sql_loop.inspect_result(empty_result(sql35), q35, [CARD_OTHER])
check("an unmatched prefix leaves the generic instruction exactly as it was",
      bool(iss36) and iss36[0].instruction == generic_empty_text(sql35),
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
print("\n37. The tag is quoted safely, and routed order picks the card")
# ---------------------------------------------------------------------------
_quote = getattr(sql_loop, "quote_literal", None)
check("the tag goes in as a SQL string literal, and a single quote in it is DOUBLED",
      _quote is not None and _quote("QM-O'BRIEN-2") == "'QM-O''BRIEN-2'",
      _quote("QM-O'BRIEN-2") if _quote else "no quote_literal")
check("an ordinary tag is quoted and otherwise untouched",
      _quote is not None and _quote("CAM-4F-B-01") == "'CAM-4F-B-01'",
      _quote("CAM-4F-B-01") if _quote else "-")
_build = getattr(sql_loop, "_empty_identifier_issue", None)
_i37 = _build("QM-O'BRIEN-2", "bld_q", "q_tag") if _build else None
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
check("two cards match: the FIRST in routed order is the one addressed",
      '"bld_cam"' in first.instruction and "bld_cam_b" not in first.instruction,
      first.instruction)
check("and reversing the routed order reverses the choice - order is the whole rule",
      '"bld_cam_b"' in second.instruction, second.instruction)
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

print("\nALL PASS" if not FAILS else f"\n{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
