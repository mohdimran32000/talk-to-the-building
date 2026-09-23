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
noident = table_result(["level_code", "kind"], [["L1", "a"]],
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
a1 = table_result(["place_name", "area_m2"], [["Studio", 30]],
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
a3 = table_result(["area_m2", "department"], [[30, "X"]],
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
sp = table_result(["Name", "Description"], [["n", "d"]], 'SELECT "Name", "Description" FROM "bld_spaced"')
iss_sp = sql_loop.inspect_result(sp, "list all the things", [CARD_SPACED])
im_sp = next((i for i in iss_sp if i.kind == sql_loop.IDENTIFIER_MISSING), None)
check("an identifier containing a space is double-quoted, not left in backticks alone",
      im_sp is not None and '"Serial number"' in im_sp.instruction, im_sp.instruction if im_sp else "")

# ---------------------------------------------------------------------------
print("\n21. F4 — the priority order, pinned by a result that raises two issues")
# ---------------------------------------------------------------------------
q21 = "list the details of every asset"
both = table_result(["model", "make"], [["M-9", "K"]], 'SELECT model, make FROM "bld_assets"')
iss21 = sql_loop.inspect_result(both, q21, [CARD_WIDE])
check("both issues are raised", len(iss21) == 2, [i.kind for i in iss21])
check("IDENTIFIER_MISSING before NARROW_SELECT",
      [i.kind for i in iss21] == [sql_loop.IDENTIFIER_MISSING, sql_loop.NARROW_SELECT],
      [i.kind for i in iss21])
check("and the loop acts on IDENTIFIER_MISSING",
      sql_loop.first_requery_issue(iss21).kind == sql_loop.IDENTIFIER_MISSING)
# EMPTY outranks both — an all-NULL row still shows which columns were selected
both_empty = table_result(["model", "make"], [[None, None]], 'SELECT model, make FROM "bld_assets"')
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
elsewhere = table_result(["area_m2", "department"], [[30, "X"]], 'SELECT area_m2, department FROM "other"')
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
ex = FakeExec(["SQL query failed: boom\n\nGenerated SQL: `SELECT 1`"])
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
check("NARROW_SELECT caps the ask at the card's non-citation columns",
      "at most 8 columns" in ns25.instruction, ns25.instruction)
check("and asks only for the matching rows",
      "for the matching rows only" in ns25.instruction, ns25.instruction)

print("\nALL PASS" if not FAILS else f"\n{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
