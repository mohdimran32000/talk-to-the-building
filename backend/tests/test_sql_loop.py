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
check("the instruction names the table", ns is not None and "bld_assets" in ns.instruction,
      ns.instruction if ns else "")
check("the instruction asks for every non-citation column",
      ns is not None and "non-citation column" in ns.instruction, ns.instruction if ns else "")
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
check("no table named in the SQL -> all routed cards (fallback)",
      len(sql_loop.cards_in_sql("SELECT 1", [CARD_LIST, CARD_WIDE])) == 2)

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
print("\n12. Three empty results in a row: bounded at 3, then the document fallback")
# ---------------------------------------------------------------------------
ex = FakeExec([empty_result(sql2)] * 3)
se = FakeSearch("DOCTEXT")
inv = sql_loop.run_sql_investigation(q2, "u1", None, execute=ex, search=se,
                                     routed_cards=[CARD_LIST], max_steps=3)
check("exactly three SQL calls", len(ex.questions) == 3, len(ex.questions))
check("three steps recorded", len(inv.steps) == 3, inv.steps)
check("one document search, with the question", se.queries == [q2], se.queries)
check("the fallback text replaces the empty result",
      sql_loop.EMPTY_FALLBACK_PREFIX in inv.result_text and "DOCTEXT" in inv.result_text,
      inv.result_text[:200])
check("the fallback keeps today's do-not-guess closing line",
      sql_loop.EMPTY_FALLBACK_SUFFIX in inv.result_text, inv.result_text[-300:])
check("the trailer counts three steps", "INVESTIGATION - steps: 3;" in inv.result_text,
      inv.result_text[-160:])

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
print("\n14. A failed query falls back to the documents, exactly as today")
# ---------------------------------------------------------------------------
failed = "SQL query failed: Binder Error: no such column\n\nGenerated SQL: `SELECT nope FROM \"bld_units\"`"
ex = FakeExec([failed])
se = FakeSearch("DOCTEXT")
inv = sql_loop.run_sql_investigation(q2, "u1", None, execute=ex, search=se,
                                     routed_cards=[CARD_LIST], max_steps=3)
check("a failure is not re-queried", len(ex.questions) == 1, ex.questions)
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
check("the excerpts are labelled as other records stating the quantity",
      sql_loop.CROSSCHECK_HEADING in inv.result_text, inv.result_text[-400:])
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
check("kinds in order: step, step, final", [k for k, _ in events] == ["step", "step", "final"],
      [k for k, _ in events])
check("step 1 payload", events[0][1]["step"] == 1 and events[0][1]["issue"] is None
      and events[0][1]["sql"] == "", events[0][1])
check("step 2 payload names the issue and the SQL it is reacting to",
      events[1][1]["step"] == 2 and events[1][1]["issue"] == sql_loop.EMPTY
      and events[1][1]["sql"] == sql2, events[1][1])
check("every step event carries a human detail",
      all(isinstance(p.get("detail"), str) and p["detail"] for k, p in events if k == "step"),
      [p for k, p in events if k == "step"])
check("the final payload is an Investigation", isinstance(events[-1][1], sql_loop.Investigation))
check("the events' step payloads are the Investigation's steps",
      [p for k, p in events if k == "step"] == events[-1][1].steps)

ex = FakeExec([r6])
events = list(sql_loop.iter_sql_investigation(q6, "u1", None, execute=ex, search=FakeSearch(),
                                              routed_cards=[CARD_LIST], max_steps=3))
check("a count question: step, crosscheck, final",
      [k for k, _ in events] == ["step", "crosscheck", "final"], [k for k, _ in events])
check("the crosscheck event names its kind",
      events[1][1].get("kind") == sql_loop.COUNT_CROSSCHECK, events[1][1])

ex = FakeExec([empty_result(sql2)] * 3)
events = list(sql_loop.iter_sql_investigation(q2, "u1", None, execute=ex, search=FakeSearch(),
                                              routed_cards=[CARD_LIST], max_steps=3))
check("three empties: step x3, then the fallback retrieval, then final",
      [k for k, _ in events] == ["step", "step", "step", "crosscheck", "final"],
      [k for k, _ in events])
check("the fallback retrieval event is marked EMPTY, not COUNT_CROSSCHECK",
      events[3][1].get("kind") == sql_loop.EMPTY, events[3][1])

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

print("\nALL PASS" if not FAILS else f"\n{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
