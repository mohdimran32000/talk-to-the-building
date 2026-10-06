"""test_sql_other_kinds.py - a filter on ONE kind code says which longer codes of the same family the
same query would also have matched (wave 3, G1, 2026-10-03).

THE DEFECT. A class word in a question ("which <class> are on deck K3?") covers every kind code of
that class in a table's kind column - the plain code and the codes made by putting a qualifier in
front of it (an emergency one, a sub-main one, a main one). The SQL writer kept narrowing the class
word to the one plain code, `kind = 'QK'`, and every row of the qualified kinds was silently left
out. A rule in the writer's prompt saying exactly this had been in place for three measured runs and
had not taken.

THE FIX, in code, on the result: for each plain `col = 'C'` / `col IN (...)` ANDed at the top of the
query's own WHERE, on a text column, where C is a CODE (two or more characters, a letter, no
lower-case letter, no space) held by more than one row (a kind, not one thing's name), C is the ROOT
of a family in
that column (no other value of the column is a shorter ending of C) and the question does not print
C (plurals included): the same SQL is re-run once per family member X - each longer value ending in
C that the filter does not already name - with that one condition written `col = 'X'`. Every re-run
that returns something (not nothing, not only zeros) adds one line:

    OTHER KINDS - <condition> leaves out the kind 'X', a longer code ending in 'C'; run for 'X', the
    same query returns: <rows>. When the question names the whole class rather than the code 'C'
    itself, these rows belong in the answer too, each named with its kind.

The rows the query returned are left exactly as they were. An error drops the line and nothing else.
One answer rule (openai_client.OUTPUT_FORMAT_RULES, never the frozen tool-choice copy) reads it.

FIX ROUND 1 (review of the wave, ruling W3A2-R1). On a HIERARCHICAL table - one with a
parent-reference column - the longer codes are often the tiers ABOVE the plain one, and each of
their rows' figures already includes the rows below it: a load total of the plain code given beside
the upper tiers' load totals invited an answer that added them. So on such a table an AGGREGATE's
line gives only how many rows of each other kind the same filter matches, never the query's figures
for them; a row list still lists the rows (they are rows, not sums), and a flat table keeps its
figures. The answer rule says to combine counts, and never to add another kind's figures to a total
of a measure on a hierarchical table (section 5).

Every table, column and value here is invented. Every check marked RED fails against the code as it
stood before this change (sections 1-4: before G1; section 5 and the round-1 bullet checks: before
fix round 1).

Run:
    venv/Scripts/python -X utf8 tests/test_sql_other_kinds.py
"""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import openai_client, sql_tool  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


def _row(code, kind, deck, status, cap):
    return {"bin_code": code, "kind": kind, "deck_code": deck, "status": status, "cap_l": cap}


BINS = {
    "table_name": "bld_bins",
    "columns": ["bin_code", "kind", "deck_code", "status", "cap_l"],
    "rows": [
        _row("QK-1", "QK", "K3", "current", "10"),
        _row("QK-2", "QK", "K3", "current", "20"),
        _row("QK-3", "QK", "K4", "retired", "30"),
        _row("QK-4", "QK", "K6", "current", "15"),
        _row("EQK-1", "EQK", "K3", "current", "5"),
        _row("EQK-2", "EQK", "K3", "retired", "7"),
        _row("SMQK-1", "SMQK", "K4", "current", "50"),
        _row("MQK-1", "MQK", "K5", "current", "100"),
        _row("MQK-2", "MQK", "K5", "current", "90"),
        _row("SMQK-2", "SMQK", "K5", "current", "40"),
        _row("PUMP-1", "PUMP", "K3", "current", "1"),
    ],
    "row_count": 11,
}
FLAGS = {   # ordinary words, one ending in another, and a word ending a code: no code family
    "table_name": "bld_flags",
    "columns": ["flag_id", "state", "rung", "deck_code"],
    "rows": [
        {"flag_id": "F-1", "state": "active", "rung": "Lo", "deck_code": "K3"},
        {"flag_id": "F-2", "state": "active", "rung": "Lo", "deck_code": "K3"},
        {"flag_id": "F-3", "state": "inactive", "rung": "XLO", "deck_code": "K3"},
    ],
    "row_count": 3,
}
BOXES = {   # a HIERARCHICAL table: each box names the box it sits below
    "table_name": "bld_boxes",
    "columns": ["box_code", "kind", "parent", "deck_code", "rated_w"],
    "rows": [
        {"box_code": "MQK-3", "kind": "MQK", "parent": "", "deck_code": "K7", "rated_w": "9000"},
        {"box_code": "SMQK-3", "kind": "SMQK", "parent": "MQK-3", "deck_code": "K7",
         "rated_w": "6000"},
        {"box_code": "QK-5", "kind": "QK", "parent": "SMQK-3", "deck_code": "K7", "rated_w": "2000"},
        {"box_code": "QK-6", "kind": "QK", "parent": "SMQK-3", "deck_code": "K7", "rated_w": "1500"},
        {"box_code": "EQK-3", "kind": "EQK", "parent": "SMQK-3", "deck_code": "K7",
         "rated_w": "800"},
    ],
    "row_count": 5,
}
DECKS = {
    "table_name": "bld_decks",
    "columns": ["deck_code", "deck_name"],
    "rows": [{"deck_code": "K7", "deck_name": "Seven"}],
    "row_count": 1,
}
TABLES = [BINS, FLAGS, BOXES, DECKS]
HEAD = "OTHER KINDS - "
Q = "Which storage bins are on deck K3, and are they current?"
SQL_LIST = ("SELECT bin_code, status FROM \"bld_bins\" WHERE kind = 'QK' AND deck_code = 'K3' "
            "ORDER BY bin_code")
TAIL = ("When the question names the whole class rather than the code 'QK' itself, these rows "
        "belong in the answer too, each named with its kind.")


class _ExecResult:
    def __init__(self, data):
        self.data = data


class _Supabase:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        tables = self.tables if name == "structured_data" else []

        class _Q:
            def select(self, *a, **kw):
                return self

            def eq(self, *a, **kw):
                return self

            def order(self, *a, **kw):
                return self

            def execute(self):
                return _ExecResult(list(tables))

        return _Q()


class _Models:
    sql = ""

    def generate_content(self, *, model, contents, config=None):
        return type("_R", (), {"text": _Models.sql, "usage_metadata": None})()


class _Client:
    def __init__(self, *a, **kw):
        self.models = _Models()


def run(sql, question=Q, tables=TABLES):
    """`execute_sql_query` end to end with a fake SQL writer that returns `sql`."""
    saved = (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
             sql_tool._load_table_cards)
    sql_tool.genai.Client = _Client
    sql_tool.get_llm_api_key = lambda: "fake-key"
    sql_tool.get_llm_model = lambda: "fake-model"
    sql_tool._load_table_cards = lambda *a, **k: []
    _Models.sql = sql
    try:
        return sql_tool.execute_sql_query(question, "u-1", _Supabase(tables))
    finally:
        (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
         sql_tool._load_table_cards) = saved


def lines(text):
    return [ln for ln in text.splitlines() if ln.startswith(HEAD)]


# ---------------------------------------------------------------------------
print("1. A class word narrowed to the root code: the other kinds' rows are named (RED)")
# ---------------------------------------------------------------------------
o1 = run(SQL_LIST)
check("the query's own rows are untouched", "| QK-1 | current |" in o1 and "| QK-2 | current |"
      in o1 and "EQK-1 |" not in o1.split("SQL: `")[0], o1[:400])
check("RED: one line names the kind left out, and the rows the same query returns for it",
      lines(o1) == [HEAD + "kind = 'QK' leaves out the kind 'EQK', a longer code ending in 'QK'; "
                    "run for 'EQK', the same query returns: (bin_code = EQK-1, status = current), "
                    "(bin_code = EQK-2, status = retired). " + TAIL], lines(o1) or o1[-500:])
check("RED: it comes after the SQL line and after the MATCHED line, never inside the table",
      HEAD in o1 and o1.index("SQL: `") < o1.index("MATCHED - ") < o1.index(HEAD)
      and not any(ln.startswith("|") and HEAD in ln for ln in o1.splitlines()), o1)
check("a family member with no row on that deck adds no line (no line names SMQK or MQK)",
      not any("'SMQK'" in ln or "'MQK'" in ln for ln in lines(o1)), lines(o1))

o1b = run("SELECT COUNT(*) AS n FROM \"bld_bins\" WHERE kind = 'QK' AND deck_code = 'K3'",
          "How many storage bins are on deck K3?")
check("RED: a count is run for the other kind too, and the line gives its figure",
      lines(o1b) == [HEAD + "kind = 'QK' leaves out the kind 'EQK', a longer code ending in "
                     "'QK'; run for 'EQK', the same query returns: 2. " + TAIL],
      lines(o1b) or o1b[-400:])
check("and the table's own figure is still the plain code's", "| 2 |" in o1b.split("SQL: `")[0],
      o1b[:300])

o1c = run("SELECT bin_code, kind FROM \"bld_bins\" WHERE kind IN ('QK', 'SMQK', 'MQK') "
          "AND deck_code = 'K3' ORDER BY bin_code")
check("RED: an IN list naming some of the family: only the member it leaves out gets a line",
      lines(o1c) == [HEAD + "kind IN ('QK', 'SMQK', 'MQK') leaves out the kind 'EQK', a longer "
                     "code ending in 'QK'; run for 'EQK', the same query returns: "
                     "(bin_code = EQK-1, kind = EQK), (bin_code = EQK-2, kind = EQK). " + TAIL],
      lines(o1c) or o1c[-500:])
check("RED: a selected kind column prints each row's own kind, never the code filtered on",
      "kind = EQK" in "".join(lines(o1c)) and "kind = QK)" not in "".join(lines(o1c)),
      lines(o1c))

o1d = run("SELECT bin_code FROM \"bld_bins\" WHERE kind = 'QK' AND deck_code = 'K4'",
          "Which storage bins are on deck K4?")
check("RED: a sub-main kind ending in the code is named the same way",
      lines(o1d) == [HEAD + "kind = 'QK' leaves out the kind 'SMQK', a longer code ending in "
                     "'QK'; run for 'SMQK', the same query returns: SMQK-1. " + TAIL],
      lines(o1d) or o1d[-400:])

q1e = Q + ("\n(Investigation step 2: The previous query `SELECT 1 FROM \"bld_bins\" WHERE "
           "kind = 'QK'` returned no rows. Previous SQL: `x`)")
check("RED: the loop's step text quoting the code back is not the question printing it",
      len(lines(run(SQL_LIST, q1e))) == 1, lines(run(SQL_LIST, q1e)))

# ---------------------------------------------------------------------------
print("\n2. Silent shapes")
# ---------------------------------------------------------------------------
for label2, sql2, q2 in [
    ("the question prints the code itself", SQL_LIST, "Which QK bins are on deck K3?"),
    ("the question prints its plural", SQL_LIST, "Which QKs are on deck K3?"),
    ("the question prints it in lower case", SQL_LIST, "list the qk bins on deck K3"),
    ("the code filtered on is no root: a shorter code of the column ends it (though a longer "
     "code ends in it and has rows there)",
     "SELECT bin_code FROM \"bld_bins\" WHERE kind = 'MQK' AND deck_code = 'K5'",
     "Which main storage bins are on deck K5?"),
    ("an IN list already naming the other kind",
     "SELECT bin_code FROM \"bld_bins\" WHERE kind IN ('QK', 'EQK') AND deck_code = 'K3'", Q),
    ("no other kind of the family has a row there (the re-runs are empty)",
     "SELECT bin_code FROM \"bld_bins\" WHERE kind = 'QK' AND deck_code = 'K6'",
     "Which storage bins are on deck K6?"),
    ("an aggregate whose re-runs hold only a zero and a NULL",
     "SELECT COUNT(*) AS n, STRING_AGG(bin_code, ', ') AS codes FROM \"bld_bins\" "
     "WHERE kind = 'QK' AND deck_code = 'K6'", "How many storage bins are on deck K6?"),
    ("the filter sits under an OR at the top of the WHERE",
     "SELECT bin_code FROM \"bld_bins\" WHERE kind = 'QK' OR deck_code = 'K9'", Q),
    ("the code is compared with ILIKE, not = or IN",
     "SELECT bin_code FROM \"bld_bins\" WHERE kind ILIKE 'QK' AND deck_code = 'K3'", Q),
    ("ordinary words, one the ending of another, are no code family",
     "SELECT flag_id FROM \"bld_flags\" WHERE state = 'active' AND deck_code = 'K3'",
     "Which flags are up on deck K3?"),
    ("a word filtered on is no code, even when a code ends in it",
     "SELECT flag_id FROM \"bld_flags\" WHERE rung = 'Lo' AND deck_code = 'K3'",
     "Which flags are on the bottom rung on deck K3?"),
    ("a value held by one row is a thing's name, never a kind",
     "SELECT deck_code FROM \"bld_bins\" WHERE bin_code = 'QK-1'",
     "Which deck holds the first storage bin?"),
    ("the column holds no longer code ending in the one filtered on",
     "SELECT bin_code FROM \"bld_bins\" WHERE kind = 'PUMP' AND deck_code = 'K3'",
     "Which pumps are on deck K3?"),
]:
    out2 = run(sql2, q2)
    check(f"{label2}: the query ran", "SQL query failed" not in out2, out2[:300])
    check(f"{label2}: no line", lines(out2) == [], lines(out2))

# ---------------------------------------------------------------------------
print("\n3. An error drops the line only")
# ---------------------------------------------------------------------------
real_exec = sql_tool._execute_with_timeout
calls3 = []


def _fail_reruns(con_, query, timeout=sql_tool.SQL_QUERY_TIMEOUT):
    calls3.append(query)
    if "'EQK'" in query:
        raise RuntimeError("re-run failed")
    return real_exec(con_, query, timeout)


sql_tool._execute_with_timeout = _fail_reruns
try:
    o3 = run(SQL_LIST)
finally:
    sql_tool._execute_with_timeout = real_exec
check("RED: the line's own re-run was really attempted",
      any("'EQK'" in c for c in calls3), calls3)
check("an error drops the line and nothing else",
      lines(o3) == [] and "| QK-1 | current |" in o3 and "SQL query failed" not in o3, o3[-300:])

# ---------------------------------------------------------------------------
print("\n4. One answer rule reads the line, in the answer prompts only")
# ---------------------------------------------------------------------------
# The line says what it left out; the ANSWER writer decides from the question's wording whether the
# question named the class or the one code. The MATCHED rule ("those rows are that thing") would
# otherwise present the narrowed rows as the whole class, so this bullet comes after it and says it
# goes ahead of it. Never in TOOL_CHOICE_FORMAT_RULES, the frozen copy the temperature-0 tool choice
# reads.
RULES = openai_client.OUTPUT_FORMAT_RULES.splitlines()
bullet = next((ln for ln in RULES if "OTHER KINDS" in ln), "")
low = bullet.lower()
check("RED: the answer rules have a bullet for the OTHER KINDS line", bullet.startswith("- "),
      bullet)
check("RED: it says what the line is: one code filtered on, and longer codes of the same column "
      "ending in it", "one code" in low and "longer codes" in low and "ending in it" in low,
      bullet)
check("RED: a question naming the whole class gets those rows too, each named with its kind, "
      "ahead of the MATCHED rule", "names the whole class" in low and "named with its kind" in low
      and "ahead of the matched rule" in low, bullet)
check("RED (fix round 1): a question asking how many gets the combined count - the table's own "
      "row count does not include them",
      "when it asks how many, give the combined count" in low
      and "row count does not include them" in low, bullet)
check("RED (fix round 1, W3A2-R1): a total of a measure on a hierarchical table never adds the "
      "other kinds' figures, and follows the hierarchy note and the area-total rule",
      "a total of a measure on a hierarchical table" in low
      and "never adds the other kinds' figures" in low
      and "follow the hierarchy note and the area-total rule" in low, bullet)
check("RED (fix round 1): on a table with no hierarchy a total adds the line's figures, each part "
      "named with its kind", "on a table with no hierarchy, a total adds the line's figures" in low,
      bullet)
check("RED (fix round 1): the old 'combined count or total' wording is gone",
      "give the combined count or total" not in low, bullet)
check("RED: a question naming that one code does not get them",
      "names that one code" in low, bullet)
check("RED: and the line and its heading are never printed",
      "never print the line or its heading" in low, bullet)
matched_at = next((i for i, ln in enumerate(RULES) if "MATCHED" in ln), -1)
check("RED: it follows the MATCHED bullet, which stays the first line naming MATCHED",
      bullet in RULES and 0 <= matched_at < RULES.index(bullet)
      and "MATCHED - every row above has" in RULES[matched_at], (matched_at, bullet[:60]))
frozen = openai_client.TOOL_CHOICE_FORMAT_RULES
check("the frozen tool-choice rules never name the line", "OTHER KINDS" not in frozen)
# The pin itself lives in tests/test_tool_choice_input.py (FROZEN_RULES_SHA256); repeated here so a
# bullet pasted into the wrong constant fails in this file too.
check("and they are byte for byte the frozen v1.3 text",
      hashlib.sha256(frozen.encode("utf-8")).hexdigest()
      == "5a9dac889675814df1765c3ba08419ac80788e187d6dcac409bd576076d784b0")

# ---------------------------------------------------------------------------
print("\n5. Fix round 1 (W3A2-R1) - a hierarchical table: an aggregate gets counts, never figures")
# ---------------------------------------------------------------------------
# The longer codes of a hierarchical table can be the tiers ABOVE the plain one, whose figures
# already include it: their totals must never be put beside its total to be added.
Q5 = "What is the total rating of the boxes on deck K7?"
COUNT_TAIL = (". The table is hierarchical, so that kind's figures are not given here: a row's "
              "figures already include the rows below it. When the question names the whole class "
              "rather than the code 'QK' itself, these rows count too, each under its own kind.")


def count_line(kind, n):
    return (HEAD + f"kind = 'QK' leaves out the kind '{kind}', a longer code ending in 'QK'; the "
            f"same filter matches {n} row{'' if n == 1 else 's'} of kind '{kind}'" + COUNT_TAIL)


EXPECT5 = [count_line("MQK", 1), count_line("SMQK", 1), count_line("EQK", 1)]
o5 = run("SELECT SUM(rated_w) AS w FROM \"bld_boxes\" WHERE kind = 'QK' AND deck_code = 'K7'", Q5)
check("the query's own figure is untouched", "| 3500.0 |" in o5.split("SQL: `")[0], o5[:300])
check("RED: a SUM of a measure on a hierarchical table: each other kind gets its row count, in "
      "the order the table holds them", lines(o5) == EXPECT5, lines(o5) or o5[-500:])
check("RED: and no line carries another kind's figures",
      not any(fig in "".join(lines(o5)) for fig in ("9000", "6000", "800"))
      and "the same query returns" not in "".join(lines(o5)), lines(o5))
o5b = run("SELECT COUNT(*) AS n FROM \"bld_boxes\" WHERE kind = 'QK' AND deck_code = 'K7'",
          "How many boxes are on deck K7?")
check("RED: a COUNT on a hierarchical table gets the same row counts (the wider family counts)",
      lines(o5b) == EXPECT5, lines(o5b) or o5b[-500:])
o5c = run("SELECT kind, SUM(rated_w) AS w FROM \"bld_boxes\" WHERE kind IN ('QK', 'SMQK') AND "
          "deck_code = 'K7' GROUP BY kind", Q5)
check("RED: a grouped aggregate the same way, for the members its IN list leaves out",
      lines(o5c) == [count_line("MQK", 1).replace("kind = 'QK' leaves", "kind IN ('QK', 'SMQK') "
                                                  "leaves"),
                     count_line("EQK", 1).replace("kind = 'QK' leaves", "kind IN ('QK', 'SMQK') "
                                                  "leaves")], lines(o5c) or o5c[-500:])
o5z = run("SELECT SUM(rated_w) AS w FROM \"bld_boxes\" WHERE kind = 'QK' AND box_code <> 'MQK-3' "
          "AND parent = 'SMQK-3'", Q5)
check("RED: a kind none of whose rows the filter matches adds no count line (no 'matches 0 rows')",
      lines(o5z) == [count_line("EQK", 1)], lines(o5z))
o5d = run("SELECT box_code FROM \"bld_boxes\" WHERE kind = 'QK' AND deck_code = 'K7' "
          "ORDER BY box_code", "Which boxes are on deck K7?")
check("a row LIST on a hierarchical table still lists the other kinds' rows (rows, not sums)",
      [ln.split("; ", 1)[1].split(". When")[0] for ln in lines(o5d)]
      == ["run for 'MQK', the same query returns: MQK-3", "run for 'SMQK', the same query returns: "
          "SMQK-3", "run for 'EQK', the same query returns: EQK-3"], lines(o5d))
o5e = run("SELECT SUM(b.rated_w) AS w FROM \"bld_boxes\" b JOIN \"bld_decks\" d ON d.deck_code = "
          "b.deck_code WHERE b.kind = 'QK' AND d.deck_name = 'Seven'", Q5)
check("RED: an aggregate joining a hierarchical table, whose row count cannot be read off one "
      "table, adds no line", "SQL query failed" not in o5e and lines(o5e) == [], lines(o5e))
o5f = run("SELECT SUM(cap_l) AS litres FROM \"bld_bins\" WHERE kind = 'QK' AND deck_code = 'K3'",
          "What do the storage bins on deck K3 hold?")
check("a flat table keeps its figures: the SUM for the other kind is given",
      lines(o5f) == [HEAD + "kind = 'QK' leaves out the kind 'EQK', a longer code ending in 'QK'; "
                     "run for 'EQK', the same query returns: 12.0. " + TAIL], lines(o5f))

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
