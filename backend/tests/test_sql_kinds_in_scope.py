"""test_sql_kinds_in_scope.py - a word pattern on a kind column that leaves out kinds of its own scope
says so (wave 4, A3, 2026-10-03).

THE DEFECT. Asked how many of two kinds of thing one storey holds, the SQL writer narrowed the
table's kind column with a word pattern of each asked word - `kind ILIKE '%a%' OR kind ILIKE '%b%'`
- though the schema block had listed that column's complete values. One kind of the asked class
prints neither word, so its row was left out of the figure, and the query gave no count per kind.
No line could see it: MATCHED leaves out a bracketed OR, WHAT THE FIGURE COUNTS needs '=', and
OTHER KINDS needs one kind code.

THE FIX, in sql_tool, as one more line beside the result. For ONE plain SELECT over one loaded table
whose top-level WHERE holds, as one ANDed condition, a word pattern on a text column whose name says
it holds a kind (a word 'type' or 'kind') - `col ILIKE '%w%'`, or a bracketed OR of such on that one
column - and whose question prints every pattern's word, the same scope is re-read grouped by that
column with that condition dropped. When 1 to 3 kinds are left out:
    KINDS IN SCOPE - <col>, rows per kind with the same filters and no pattern on it: A 6, B 1
    (matched); C 1 (not matched by the pattern)
One answer rule reads it (OUTPUT_FORMAT_RULES only; the frozen tool-choice rules are untouched).

FIX ROUND 1 (ruling W4A1-R1, section 5 and the rule checks of section 4): the figure per kind is the
query's OWN aggregate re-run per kind - SUM(qty) per kind for a SUM(qty), labelled with that
expression; rows for COUNT(*) and for a row list; no line when the aggregate cannot be re-run that
way - and the rule says that when the question names ONE kind, that kind's figure is the answer.

Every table, column and value here is invented. Every check marked RED fails against the code as it
stood before this change.

Run:
    venv/Scripts/python -X utf8 tests/test_sql_kinds_in_scope.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import openai_client, sql_tool  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


HEAD = "KINDS IN SCOPE - "


def _gate(gid, deck, kind, readers="1"):
    return {"gate_id": gid, "deck_code": deck, "gate_type_printed": kind, "readers": readers,
            "space_name": "Hall " + gid}


GATES = {
    "table_name": "bld_gates",
    "columns": ["gate_id", "deck_code", "gate_type_printed", "readers", "space_name"],
    "rows": [_gate("G-01", "00", "SWING HATCH"), _gate("G-02", "00", "STILE", "2"),
             _gate("G-03", "00", "STILE", "2"), _gate("G-04", "00", "STILE"),
             _gate("G-05", "00", "GLAZED FRAME"),
             _gate("G-11", "01", "SWING HATCH"), _gate("G-12", "01", "SLIDING HATCH"),
             _gate("G-13", "01", "SWING HATCH"),
             _gate("G-21", "02", "SWING HATCH"), _gate("G-22", "02", "ROLLER SHUTTER"),
             _gate("G-23", "02", "GLAZED FRAME"), _gate("G-24", "02", "BOOM BARRIER"),
             _gate("G-25", "02", "AIRLOCK")],
    "row_count": 13,
}
CARDS = [{"table": "bld_gates", "identifier_column": "gate_id",
          "holds": "one row for each controlled gate position"}]


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


def run(sql, question, tables=(GATES,), cards=CARDS):
    """`execute_sql_query` end to end with a fake SQL writer returning `sql`. No model, no network."""
    saved = (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
             sql_tool._load_table_cards)
    sql_tool.genai.Client = _Client
    sql_tool.get_llm_api_key = lambda: "fake-key"
    sql_tool.get_llm_model = lambda: "fake-model"
    sql_tool._load_table_cards = lambda *a, **k: list(cards)
    _Models.sql = sql
    try:
        return sql_tool.execute_sql_query(question, "u-1", _Supabase(list(tables)))
    finally:
        (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
         sql_tool._load_table_cards) = saved


def kinds_lines(text):
    return [ln for ln in text.splitlines() if ln.startswith(HEAD)]


Q1 = "How many hatches and stiles are on deck 00?"
PATTERN_SQL = ("SELECT COUNT(*) FROM \"bld_gates\" WHERE deck_code = '00' AND "
               "(gate_type_printed ILIKE '%hatch%' OR gate_type_printed ILIKE '%stile%')")
LINE1 = (HEAD + "gate_type_printed, rows per kind with the same filters and no pattern on it: "
         "STILE 3, SWING HATCH 1 (matched); GLAZED FRAME 1 (not matched by the pattern)")

# ---------------------------------------------------------------------------
print("1. A word pattern on a kind column that leaves out one kind of its scope (RED)")
# ---------------------------------------------------------------------------
out1 = run(PATTERN_SQL, Q1)
check("the writer's own figure is still the result's", "| 4 |" in out1, out1[:200])
check("RED: the result carries exactly one KINDS IN SCOPE line", len(kinds_lines(out1)) == 1,
      kinds_lines(out1) or out1[-400:])
check("RED: every kind of the scope with its rows, the matched ones first, the left-out one marked",
      kinds_lines(out1) == [LINE1], kinds_lines(out1))
check("RED: it comes after the SQL line, never inside the table",
      bool(kinds_lines(out1)) and out1.index("SQL: `") < out1.index(HEAD)
      and not any(ln.startswith("|") and HEAD in ln for ln in out1.splitlines()), out1[-600:])
out1b = run("SELECT gate_id, gate_type_printed FROM \"bld_gates\" WHERE deck_code = '00' AND "
            "gate_type_printed ILIKE '%hatch%'", "Which hatches are on deck 00?")
check("RED: one pattern alone, in a row list, the same way",
      kinds_lines(out1b) == [HEAD + "gate_type_printed, rows per kind with the same filters and no "
                             "pattern on it: SWING HATCH 1 (matched); STILE 3, GLAZED FRAME 1 "
                             "(not matched by the pattern)"], kinds_lines(out1b))
out1c = run("SELECT COUNT(*) FROM \"bld_gates\" g WHERE g.deck_code = '00' AND "
            "(g.gate_type_printed ILIKE '%hatch%' OR g.gate_type_printed ILIKE '%stile%')", Q1)
check("RED: a qualified column is named as written", kinds_lines(out1c) == [
    LINE1.replace(HEAD + "gate_type_printed", HEAD + "g.gate_type_printed")], kinds_lines(out1c))
out1d = run(PATTERN_SQL, Q1 + "\n(Investigation step 2: an instruction; Previous SQL: `x`)")
check("RED: a step suffix after the question changes nothing", kinds_lines(out1d) == [LINE1],
      kinds_lines(out1d))
out1e = run(PATTERN_SQL, "How many controlled positions are on deck 00?\n(Investigation step 2: "
            "count every hatch and stile; Previous SQL: `x`)")
check("the pattern's words printed only in the loop's step suffix: no line - the question is read "
      "without it", kinds_lines(out1e) == [], kinds_lines(out1e))

# ---------------------------------------------------------------------------
print("\n2. Silent shapes")
# ---------------------------------------------------------------------------
for label2, sql2, q2 in [
    ("the question does not print the pattern's words", PATTERN_SQL,
     "How many controlled positions are on deck 00?"),
    ("nothing left out: every kind of the scope matched",
     "SELECT COUNT(*) FROM \"bld_gates\" WHERE deck_code = '01' AND gate_type_printed ILIKE '%hatch%'",
     "How many hatches are on deck 01?"),
    ("more than three kinds left out",
     "SELECT COUNT(*) FROM \"bld_gates\" WHERE deck_code = '02' AND gate_type_printed ILIKE '%hatch%'",
     "How many hatches are on deck 02?"),
    ("a pattern on a column whose name says no kind (a place name)",
     "SELECT COUNT(*) FROM \"bld_gates\" WHERE deck_code = '00' AND space_name ILIKE '%hall%'",
     "How many gates in a hall are on deck 00?"),
    ("an equality on the kind column", "SELECT COUNT(*) FROM \"bld_gates\" WHERE deck_code = '00' "
                                       "AND gate_type_printed = 'SWING HATCH'",
     "How many swing hatches are on deck 00?"),
    ("a negated pattern", "SELECT COUNT(*) FROM \"bld_gates\" WHERE deck_code = '00' AND "
                          "gate_type_printed NOT ILIKE '%hatch%'", "How many are not hatches on deck 00?"),
    ("a pattern under a top-level OR", "SELECT COUNT(*) FROM \"bld_gates\" WHERE deck_code = '00' "
                                       "OR gate_type_printed ILIKE '%hatch%'",
     "How many hatches are on deck 00?"),
    ("a pattern with a wildcard inside its word",
     "SELECT COUNT(*) FROM \"bld_gates\" WHERE deck_code = '00' AND gate_type_printed ILIKE "
     "'%sw%hatch%'", "How many swing hatches are on deck 00?"),
    ("an OR mixing two columns", "SELECT COUNT(*) FROM \"bld_gates\" WHERE deck_code = '00' AND "
                                 "(gate_type_printed ILIKE '%hatch%' OR space_name ILIKE '%stile%')",
     Q1),
    ("a join", "SELECT COUNT(*) FROM \"bld_gates\" a JOIN \"bld_gates\" b ON a.gate_id = b.gate_id "
               "WHERE a.deck_code = '00' AND a.gate_type_printed ILIKE '%hatch%'",
     "How many hatches are on deck 00?"),
]:
    out2 = run(sql2, q2)
    check(f"{label2}: the query ran, and no line", "SQL query failed" not in out2
          and kinds_lines(out2) == [], kinds_lines(out2) or out2[-300:])

# ---------------------------------------------------------------------------
print("\n3. An error drops the line and nothing else")
# ---------------------------------------------------------------------------
real_exec = sql_tool._execute_with_timeout
calls = []


def _fail_scope(con, sql, timeout=sql_tool.SQL_QUERY_TIMEOUT):
    calls.append(sql)
    if "GROUP BY" in sql and "MAX(CASE WHEN" in sql:
        raise RuntimeError("scope read failed")
    return real_exec(con, sql, timeout)


sql_tool._execute_with_timeout = _fail_scope
try:
    out3 = run(PATTERN_SQL, Q1)
finally:
    sql_tool._execute_with_timeout = real_exec
check("RED: the scope read was really attempted", any("MAX(CASE WHEN" in c for c in calls), calls)
check("an error drops the line and nothing else",
      kinds_lines(out3) == [] and "| 4 |" in out3 and "SQL query failed" not in out3, out3[-300:])

# ---------------------------------------------------------------------------
print("\n4. One answer rule reads the line (answer prompts only)")
# ---------------------------------------------------------------------------
rule = next((ln for ln in openai_client.OUTPUT_FORMAT_RULES.splitlines() if "KINDS IN SCOPE" in ln),
            "")
check("RED: OUTPUT_FORMAT_RULES has a KINDS IN SCOPE bullet", bool(rule))
# Fix round 1 (ruling W4A1-R1): the line gives the query's own figure per kind, so the rule reads
# one FIGURE per kind (it read "one count per kind" before).
check("RED: a class word covers every kind of its class: one figure per kind, and the total",
      "every kind of its class" in rule and "one figure per kind" in rule, rule)
check("RED (R1): the rule says the line's figures are the query's own - rows, or its own sum or count",
      "the query's own figure" in rule and "rows, or the query's own sum or count" in rule, rule)
check("RED (R1): when the question names ONE kind, that kind's figure is the answer and the other "
      "kinds are named as what the class also holds",
      "When the question names ONE kind, that kind's figure is the answer" in rule
      and "what the class also holds" in rule, rule)
check("RED: a kind the pattern did not match belongs to the class when the SOURCE line says each "
      "row is one such item", "SOURCE line says each row is one such item" in rule, rule)
check("RED: the line and its heading are never printed", "never print" in rule.lower(), rule)
check("the frozen tool-choice rules carry none of it (ruling W7)",
      "KINDS IN SCOPE" not in openai_client.TOOL_CHOICE_FORMAT_RULES)

# ---------------------------------------------------------------------------
print("\n5. The line gives the query's OWN figure per kind (fix round 1, ruling W4A1-R1)")
# ---------------------------------------------------------------------------
# The review found the line counting rows whatever the query added up: a SUM of a quantity column
# read 8 in the table and "1" on the line. Per kind the line now re-runs the query's own aggregate -
# the same expression, the pattern written TRUE, grouped by the kind column, every other filter kept
# - labelled with that expression; rows for COUNT(*) and for a row list; no line when the aggregate
# cannot be re-run that way.
def _fit(fid, deck, kind, qty, points):
    return {"fitting_id": fid, "deck_code": deck, "fitting_type": kind, "qty": qty,
            "points": points}


FITTINGS = {
    "table_name": "bld_fittings",
    "columns": ["fitting_id", "deck_code", "fitting_type", "qty", "points"],
    "rows": [_fit("F-1", "00", "WALL LAMP", "3", "6"), _fit("F-2", "00", "WALL LAMP", "2", "4"),
             _fit("F-3", "00", "CEILING LAMP", "4", "8"), _fit("F-4", "00", "FLOOD LIGHT", "1", "3"),
             _fit("F-5", "00", "EXIT SIGN", "2", "2"), _fit("F-6", "01", "WALL LAMP", "9", "9")],
    "row_count": 6,
}
FCARDS = [{"table": "bld_fittings", "identifier_column": "fitting_id",
           "holds": "one row per fitting line, with how many units it holds"}]
QL = "How many lamps are on deck 00?"
LAMP_WHERE = "FROM \"bld_fittings\" WHERE deck_code = '00' AND fitting_type ILIKE '%lamp%'"
REST = " per kind with the same filters and no pattern on it: "


def fit_lines(sql, question=QL):
    return kinds_lines(run(sql, question, tables=(FITTINGS,), cards=FCARDS))


out5 = run(f"SELECT SUM(qty) {LAMP_WHERE}", QL, tables=(FITTINGS,), cards=FCARDS)
check("the query's own figure is still the result's", "| 9.0 |" in out5, out5[:200])
check("RED (R1): a SUM of a quantity column gives that SUM per kind, labelled with its expression",
      kinds_lines(out5) == [HEAD + "fitting_type, SUM(qty)" + REST + "WALL LAMP 5, CEILING LAMP 4 "
                            "(matched); EXIT SIGN 2, FLOOD LIGHT 1 (not matched by the pattern)"],
      kinds_lines(out5))
check("RED (R1): a points total, aliased, the same way",
      fit_lines(f"SELECT SUM(points) AS total_points {LAMP_WHERE}",
                "How many lamp points are on deck 00?")
      == [HEAD + "fitting_type, SUM(points)" + REST + "WALL LAMP 10, CEILING LAMP 8 (matched); "
          "FLOOD LIGHT 3, EXIT SIGN 2 (not matched by the pattern)"],
      fit_lines(f"SELECT SUM(points) AS total_points {LAMP_WHERE}",
                "How many lamp points are on deck 00?"))
check("RED (R1): a grouped SUM - its one aggregate item - the same way",
      fit_lines(f"SELECT fitting_type, SUM(qty) AS n {LAMP_WHERE} GROUP BY fitting_type",
                "How many lamps of each kind are on deck 00?")
      == [HEAD + "fitting_type, SUM(qty)" + REST + "WALL LAMP 5, CEILING LAMP 4 (matched); EXIT "
          "SIGN 2, FLOOD LIGHT 1 (not matched by the pattern)"],
      fit_lines(f"SELECT fitting_type, SUM(qty) AS n {LAMP_WHERE} GROUP BY fitting_type",
                "How many lamps of each kind are on deck 00?"))
ROWS5 = [HEAD + "fitting_type, rows" + REST + "WALL LAMP 2, CEILING LAMP 1 (matched); FLOOD LIGHT "
         "1, EXIT SIGN 1 (not matched by the pattern)"]
check("COUNT(*), aliased: rows per kind", fit_lines(f"SELECT COUNT(*) AS n {LAMP_WHERE}") == ROWS5,
      fit_lines(f"SELECT COUNT(*) AS n {LAMP_WHERE}"))
check("a row list: rows per kind", fit_lines(f"SELECT fitting_id, qty {LAMP_WHERE}") == ROWS5,
      fit_lines(f"SELECT fitting_id, qty {LAMP_WHERE}"))
check("RED (R1): a COUNT of a column is labelled with its own expression",
      fit_lines(f"SELECT COUNT(DISTINCT fitting_id) {LAMP_WHERE}")
      == [ROWS5[0].replace("fitting_type, rows per", "fitting_type, COUNT(DISTINCT fitting_id) per")],
      fit_lines(f"SELECT COUNT(DISTINCT fitting_id) {LAMP_WHERE}"))
for label5, sql5 in [
    ("an aggregate inside an expression", f"SELECT SUM(qty) + SUM(points) {LAMP_WHERE}"),
    ("two aggregates", f"SELECT SUM(qty), COUNT(*) {LAMP_WHERE}"),
    ("a window over a row list", f"SELECT fitting_id, SUM(qty) OVER () AS t {LAMP_WHERE}"),
]:
    check(f"RED (R1): {label5} cannot be re-run per kind that way: no line",
          fit_lines(sql5) == [], fit_lines(sql5))

# ---------------------------------------------------------------------------
print("\n6. A second detector: an explicit IN(...)/equality-OR-chain kind filter (wave 5)")
# ---------------------------------------------------------------------------
# Deck 00 holds three kinds: STILE x3 (G-02, G-03, G-04), SWING HATCH x1 (G-01), GLAZED FRAME x1
# (G-05) - five positions in all. Unlike the word-pattern path above, naming EVERY kind in scope
# (nothing "left out") does not suppress this line - it is unconditional - and the WHERE clause
# is kept exactly as written, never rewritten to TRUE.
REST6 = " per kind with the same filters: "
in_sql = ("SELECT COUNT(*) FROM \"bld_gates\" WHERE deck_code = '00' AND gate_type_printed IN "
          "('STILE', 'SWING HATCH', 'GLAZED FRAME')")
out6a = run(in_sql, "How many stiles, swing hatches and glazed frames are on deck 00?")
check("the writer's own figure is still the result's (all 3 kinds named - none left out)",
      "| 5 |" in out6a, out6a[:200])
check("RED: an IN-list naming every kind in scope still gets a breakdown",
      kinds_lines(out6a) == [HEAD + "gate_type_printed, rows" + REST6
                            + "STILE 3, SWING HATCH 1, GLAZED FRAME 1"], kinds_lines(out6a))

or_sql = ("SELECT COUNT(*) FROM \"bld_gates\" WHERE deck_code = '00' AND "
          "(gate_type_printed = 'STILE' OR gate_type_printed = 'SWING HATCH')")
out6b = run(or_sql, "How many stiles and swing hatches are on deck 00?")
check("RED: a chained OR of '=' on the same column, the same way (GLAZED FRAME simply unmatched, "
      "not 'left out' of anything)",
      kinds_lines(out6b) == [HEAD + "gate_type_printed, rows" + REST6 + "STILE 3, SWING HATCH 1"],
      kinds_lines(out6b))

qty_sql = (f"SELECT SUM(qty) FROM \"bld_fittings\" WHERE deck_code = '00' AND fitting_type IN "
          f"('WALL LAMP', 'CEILING LAMP')")
out6c = run(qty_sql, "How many wall lamps and ceiling lamps are on deck 00?", tables=(FITTINGS,),
           cards=FCARDS)
check("RED: the query's own aggregate (SUM), not a row count, the same way as the pattern path",
      kinds_lines(out6c) == [HEAD + "fitting_type, SUM(qty)" + REST6 + "WALL LAMP 5, CEILING LAMP 4"],
      kinds_lines(out6c))

for label6, sql6, q6 in [
    ("only one real kind present once the data is read (AIRLOCK is deck 02, not 00)",
     "SELECT COUNT(*) FROM \"bld_gates\" WHERE deck_code = '00' AND gate_type_printed IN "
     "('SWING HATCH', 'AIRLOCK')", "How many swing hatches or airlocks are on deck 00?"),
    ("a single-value IN-list", "SELECT COUNT(*) FROM \"bld_gates\" WHERE deck_code = '00' AND "
                               "gate_type_printed IN ('STILE')", "How many stiles are on deck 00?"),
    ("a lone equality (no OR, no IN)", "SELECT COUNT(*) FROM \"bld_gates\" WHERE deck_code = '00' "
                                       "AND gate_type_printed = 'STILE'", "How many stiles are on deck 00?"),
    ("an IN-list on a column whose name says no kind (a place name)",
     "SELECT COUNT(*) FROM \"bld_gates\" WHERE deck_code = '00' AND space_name IN "
     "('Hall G-01', 'Hall G-02')", "How many gates are in Hall G-01 or Hall G-02?"),
    ("an OR mixing '=' and a word pattern on the same column",
     "SELECT COUNT(*) FROM \"bld_gates\" WHERE deck_code = '00' AND (gate_type_printed = 'STILE' "
     "OR gate_type_printed ILIKE '%hatch%')", "How many stiles and hatches are on deck 00?"),
    ("an OR naming two different columns", "SELECT COUNT(*) FROM \"bld_gates\" WHERE deck_code = "
     "'00' AND (gate_type_printed = 'STILE' OR space_name = 'Hall G-01')",
     "How many stiles are in Hall G-01 on deck 00?"),
]:
    out6 = run(sql6, q6)
    check(f"{label6}: the query ran, and no line", "SQL query failed" not in out6
          and kinds_lines(out6) == [], kinds_lines(out6) or out6[-300:])

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
