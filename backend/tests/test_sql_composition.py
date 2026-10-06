"""test_sql_composition.py - a one-figure aggregate says what its figure counts when its filter
value is also a word only SOME of the item names it read print (wave 3, F4, 2026-10-03).

THE DEFECT. A register's system code can be the very type word a question asks about - the
system 'x' holds the X units AND the accessories bought with them. "How many X units ..." was
written as `SUM(qty) ... WHERE system = 'x'`, the MATCHED line truthfully said every row had
system = 'x', and the bare figure - units plus accessories - was stated as the count of X units.

THE FIX, in code, on the result: for a one-figure aggregate (no GROUP BY, no window) over ONE
outer table - a sub-SELECT inside its WHERE is fine - that compares `col = 'lit'` ANDed at the top
of its WHERE, where `lit` has at least 3 characters and the question prints it as a word, on a
table with an ITEM column (a text column whose name says item, description, device or model)
that the SQL never names: when `lit` is a whole word in some but not all of the item values the
query read, the same SQL is re-run twice over a temporary view of the table - the rows whose
item names `lit`, and the rest - and one line says what the figure counts:

    WHAT THE FIGURE COUNTS - items named '<lit>' (<names>): <a>; other items (<names>): <b>

The figure the query returned is left exactly as it was. An error drops the line and nothing
else.

FIX ROUND 1 (review of the wave): the line used to end "'How many <lit>' is <a>." - a verdict
the trigger cannot make, since it cannot tell "how many <type>" from "how many things in system
<code>": for a question about the whole system the table's own figure is the answer, and the
sentence named the wrong one. The line now states the two figures and nothing else, and an
answer rule (openai_client.OUTPUT_FORMAT_RULES, never the frozen tool-choice copy) decides from
the question's wording - and covers sql_tool's LITERAL ELSEWHERE line too (section 4).

Every table, column and value here is invented. Every check marked RED fails against the code
as it stood before this change (sections 1-3: before F4; the fix-round checks: before round 1).

Run:
    venv/Scripts/python -X utf8 tests/test_sql_composition.py
"""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb  # noqa: E402

from app.services import openai_client, sql_tool  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


KIT = {
    "table_name": "bld_kit",
    "columns": ["place", "wing", "system", "item", "qty", "kg"],
    "rows": [
        {"place": "QZ-3", "wing": "N", "system": "gizmo", "item": "GIZMO 2X", "qty": "2", "kg": "4"},
        {"place": "QZ-3", "wing": "N", "system": "gizmo", "item": "GIZMO 5X N", "qty": "1",
         "kg": "9"},
        {"place": "QZ-3", "wing": "S", "system": "gizmo", "item": "Spare Cell Pack", "qty": "4",
         "kg": "2"},
        {"place": "QZ-3", "wing": "S", "system": "sprocket", "item": "SPROCKET A", "qty": "3",
         "kg": "1"},
        {"place": "QZ-4", "wing": "N", "system": "gizmo", "item": "GIZMO 2X", "qty": "5", "kg": "4"},
    ],
    "row_count": 5,
}
SPOTS = {
    "table_name": "bld_spots",
    "columns": ["spot", "deck_code"],
    "rows": [{"spot": "QZ-3", "deck_code": "K3"}, {"spot": "QZ-4", "deck_code": "K4"}],
    "row_count": 2,
}
REG = {
    "table_name": "bld_reg",
    "columns": ["reg_id", "system", "description", "model", "qty"],
    "rows": [
        {"reg_id": "R-1", "system": "gizmo", "description": "Gizmo bank", "model": "GZ-1",
         "qty": "2"},
        {"reg_id": "R-2", "system": "gizmo", "description": "Bobbin tray", "model": "GZ-2",
         "qty": "6"},
    ],
    "row_count": 2,
}
PLAIN = {
    "table_name": "bld_plain",
    "columns": ["system", "label", "qty"],
    "rows": [
        {"system": "gizmo", "label": "Gizmo bank", "qty": "1"},
        {"system": "gizmo", "label": "Bobbin tray", "qty": "1"},
    ],
    "row_count": 2,
}
LOTS = {   # a banner row per lot, labelled with the lot itself and carrying no quantity
    "table_name": "bld_lots",
    "columns": ["lot", "system", "item", "qty"],
    "rows": [
        {"lot": "QZ-5", "system": "gizmo", "item": "QZ-5", "qty": ""},
        {"lot": "QZ-5", "system": "gizmo", "item": "GIZMO 2X", "qty": "2"},
        {"lot": "QZ-7", "system": "gizmo", "item": "GIZMO 5X", "qty": "1"},
        {"lot": "QZ-7", "system": "gizmo", "item": "Spare Cell Pack", "qty": ""},
    ],
    "row_count": 4,
}
TABLES = [KIT, SPOTS, REG, PLAIN, LOTS]
HEAD = "WHAT THE FIGURE COUNTS - "
Q_COUNT = "how many gizmo units are on deck K3?"


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


def run(sql, question=Q_COUNT, tables=TABLES):
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
print("1. A SUM filtered on a code that is also the asked type word (RED)")
# ---------------------------------------------------------------------------
q1 = ("SELECT SUM(qty) FROM \"bld_kit\" WHERE system = 'gizmo' AND place IN "
      "(SELECT spot FROM \"bld_spots\" WHERE deck_code = 'K3')")
o1 = run(q1)
check("the query's own figure is still the table's, untouched", "| 7.0 |" in o1, o1[:300])
check("RED: one line splits the figure into the items named for the type word and the rest - "
      "the two figures and nothing else (fix round 1)",
      lines(o1) == [HEAD + "items named 'gizmo' (GIZMO 2X, GIZMO 5X N): 3.0; other items "
                    "(Spare Cell Pack): 4.0"], lines(o1) or o1[-400:])
check("RED: it comes after the SQL line, never inside the rendered table",
      HEAD in o1 and o1.index("SQL: `") < o1.index(HEAD)
      and not any(ln.startswith("|") and HEAD in ln for ln in o1.splitlines()), o1)
o1b = run("SELECT COUNT(*) AS n FROM \"bld_kit\" WHERE system = 'gizmo' AND place = 'QZ-3'")
check("RED: a COUNT is split the same way", lines(o1b) == [
    HEAD + "items named 'gizmo' (GIZMO 2X, GIZMO 5X N): 2; other items (Spare Cell Pack): 1"],
      lines(o1b) or o1b[-300:])
o1c = run("SELECT SUM(qty) FROM \"bld_reg\" WHERE system = 'gizmo'", "how many GIZMO units?")
check("RED: a 'description' column is an item column too, and the question's own spelling "
      "names the word", lines(o1c) == [
          HEAD + "items named 'GIZMO' (Gizmo bank): 2.0; other items (Bobbin tray): 6.0"],
      lines(o1c) or o1c[-300:])
o1d = run("SELECT SUM(kg) FROM \"bld_kit\" WHERE system = 'gizmo' AND place = 'QZ-3'",
          "what do the gizmo things on QZ-3 weigh?")
check("RED: a figure that is no count is split too", lines(o1d) == [
    HEAD + "items named 'gizmo' (GIZMO 2X, GIZMO 5X N): 13.0; other items (Spare Cell Pack): "
           "2.0"], lines(o1d) or o1d[-300:])
# Fix round 1: a count question about the WHOLE system - "how many things does system x list" -
# is answered by the table's own figure. The trigger cannot tell it from "how many x units",
# so the line must never say which part answers 'how many'.
o1e = run("SELECT SUM(qty) FROM \"bld_kit\" WHERE system = 'gizmo' AND place = 'QZ-3'",
          "How many items does the gizmo system list on QZ-3?")
check("RED: a whole-system count question gets the two figures and no verdict on which answers",
      lines(o1e) == [HEAD + "items named 'gizmo' (GIZMO 2X, GIZMO 5X N): 3.0; other items "
                     "(Spare Cell Pack): 4.0"], lines(o1e) or o1e[-300:])
check("RED: no line ever says which part answers 'how many'",
      not any("how many" in ln.lower() for out in (o1, o1b, o1c, o1d, o1e)
              for ln in lines(out)), [lines(out) for out in (o1, o1b, o1c, o1e)])

# ---------------------------------------------------------------------------
print("\n2. Silent shapes")
# ---------------------------------------------------------------------------
for label2, sql2, q2 in [
    ("the SQL names the item column",
     "SELECT SUM(qty) FROM \"bld_kit\" WHERE system = 'gizmo' AND item ILIKE '%gizmo%'", Q_COUNT),
    ("the query groups",
     "SELECT place, SUM(qty) FROM \"bld_kit\" WHERE system = 'gizmo' GROUP BY place", Q_COUNT),
    ("the literal is not a word of the question",
     "SELECT SUM(qty) FROM \"bld_kit\" WHERE system = 'gizmo' AND place = 'QZ-3'",
     "how many units are on deck K3?"),
    ("the literal is shorter than 3 characters (a one-letter code inside an item name)",
     "SELECT SUM(qty) FROM \"bld_kit\" WHERE wing = 'N'", "how many units in wing N?"),
    ("every item read names the literal",
     "SELECT SUM(qty) FROM \"bld_kit\" WHERE system = 'gizmo' AND place = 'QZ-4'", Q_COUNT),
    ("no item read names the literal (a place code the question prints)",
     "SELECT SUM(qty) FROM \"bld_kit\" WHERE system = 'gizmo' AND place = 'QZ-3'",
     "how many units are on QZ-3?"),
    ("a row list, which is no aggregate",
     "SELECT qty FROM \"bld_kit\" WHERE system = 'gizmo' AND place = 'QZ-3'", Q_COUNT),
    ("a window aggregate",
     "SELECT SUM(qty) OVER () AS t FROM \"bld_kit\" WHERE system = 'gizmo' AND place = 'QZ-4'",
     Q_COUNT),
    ("two tables at the top of the query",
     "SELECT SUM(k.qty) FROM \"bld_kit\" k JOIN \"bld_spots\" s ON s.spot = k.place "
     "WHERE k.system = 'gizmo' AND s.deck_code = 'K3'", Q_COUNT),
    ("a table with no item column",
     "SELECT SUM(qty) FROM \"bld_plain\" WHERE system = 'gizmo'", "how many gizmo units?"),
    ("the literal compared with LIKE, not =",
     "SELECT SUM(qty) FROM \"bld_kit\" WHERE system LIKE 'gizmo' AND place = 'QZ-3'", Q_COUNT),
    ("the part named for the literal holds nothing (a banner row labelled with the filter "
     "value, no quantity)", "SELECT SUM(qty) FROM \"bld_lots\" WHERE lot = 'QZ-5'",
     "how many units are in lot QZ-5?"),
    ("the other part holds nothing (the figure IS the named items' figure)",
     "SELECT SUM(qty) FROM \"bld_lots\" WHERE system = 'gizmo' AND lot = 'QZ-7'",
     "how many gizmo units are in lot QZ-7?"),
    ("the literal is only in the loop's step text, never the user's words",
     "SELECT SUM(qty) FROM \"bld_kit\" WHERE system = 'gizmo' AND place = 'QZ-3'",
     "how many units are on deck K3?\n(Investigation step 2: The previous query `SELECT 1 "
     "WHERE system = 'gizmo'` returned no rows. Previous SQL: `x`)"),
]:
    out2 = run(sql2, q2)
    check(f"{label2}: the query ran", "SQL query failed" not in out2, out2[:300])
    check(f"{label2}: no line", lines(out2) == [], lines(out2))

# ---------------------------------------------------------------------------
print("\n3. The re-runs leave the connection as loaded; an error drops the line only")
# ---------------------------------------------------------------------------
con = duckdb.connect(":memory:")
con.execute('CREATE TABLE "bld_kit" ("place" VARCHAR, "wing" VARCHAR, "system" VARCHAR, '
            '"item" VARCHAR, "qty" DOUBLE, "kg" DOUBLE)')
for row in KIT["rows"]:
    con.execute('INSERT INTO "bld_kit" VALUES (?, ?, ?, ?, ?, ?)',
                [row["place"], row["wing"], row["system"], row["item"], float(row["qty"]),
                 float(row["kg"])])
q3 = "SELECT SUM(qty) FROM \"bld_kit\" WHERE system = 'gizmo' AND place = 'QZ-3'"
types3 = {"bld_kit": sql_tool._infer_column_types(KIT["columns"], KIT["rows"])}
build3 = getattr(sql_tool, "_figure_counts_line", None)
line3 = (build3(con, Q_COUNT, q3, [KIT], types3, ["sum(qty)"], con.execute(q3).fetchall())
         if build3 else "")
check("RED: called on its own, it builds the line", line3.startswith(HEAD), line3)
check("and leaves the table exactly as loaded - no view survives the call",
      con.execute(q3).fetchall() == [(7.0,)]
      and con.execute("SELECT COUNT(*) FROM duckdb_views() WHERE NOT internal").fetchall()
      == [(0,)], con.execute(q3).fetchall())

real_exec = sql_tool._execute_with_timeout
calls3 = []


def _fail_reruns(con_, query, timeout=sql_tool.SQL_QUERY_TIMEOUT):
    calls3.append(query)
    if len(calls3) > 1:
        raise RuntimeError("re-run failed")
    return real_exec(con_, query, timeout)


sql_tool._execute_with_timeout = _fail_reruns
try:
    o3 = run(q3)
finally:
    sql_tool._execute_with_timeout = real_exec
check("RED: the line's own reads were really attempted", len(calls3) >= 2, calls3)
check("an error drops the line and nothing else",
      lines(o3) == [] and "| 7.0 |" in o3 and "SQL query failed" not in o3, o3[-300:])

# ---------------------------------------------------------------------------
print("\n4. Fix round 1 - one answer rule reads both new lines, in the answer prompts only")
# ---------------------------------------------------------------------------
# The line no longer says which figure answers, so the ANSWER writer decides, from the question's
# wording - and on the measured run the MATCHED rule ("those rows are that thing") wrote the
# combined figure instead. One bullet, in OUTPUT_FORMAT_RULES (the four answer prompts), never in
# TOOL_CHOICE_FORMAT_RULES, the frozen copy the temperature-0 tool choice reads.
RULES = openai_client.OUTPUT_FORMAT_RULES.splitlines()
bullet = next((ln for ln in RULES if "WHAT THE FIGURE COUNTS" in ln), "")
low = bullet.lower()
check("RED: the answer rules have a bullet for the WHAT THE FIGURE COUNTS line",
      bullet.startswith("- "), bullet)
check("RED: it says what the line splits: the items whose names carry the asked word, and the "
      "other items the same filter caught",
      "items whose names carry the asked word" in low and "same filter caught" in low, bullet)
check("RED: when the question asks for those items themselves, that part is the answer - ahead "
      "of the MATCHED rule - and the other items are named as what the figure also counted",
      "asks for those items themselves" in low and "that part is the answer" in low
      and "ahead of the matched rule" in low and "also counted" in low, bullet)
check("RED: when the question asks for everything under that filter, the table's figure stands",
      "everything under that filter" in low and "the table's figure stands" in low, bullet)
check("RED: the same bullet covers a LITERAL ELSEWHERE line: no figure in that result belongs "
      "to the value", "LITERAL ELSEWHERE" in bullet
      and "no figure in that result belongs to it" in low, bullet)
check("RED: and neither line, its heading, nor a table or column name is ever printed",
      "never print either line, its heading, or a table or column name" in low, bullet)
matched_at = next((i for i, ln in enumerate(RULES) if "MATCHED" in ln), -1)
check("RED: it follows the MATCHED bullet, which stays the first line naming MATCHED (other "
      "tests read that bullet as the first such line)",
      bullet in RULES and 0 <= matched_at < RULES.index(bullet)
      and "MATCHED - every row above has" in RULES[matched_at], (matched_at, bullet[:60]))
frozen = openai_client.TOOL_CHOICE_FORMAT_RULES
check("the frozen tool-choice rules carry neither new line's name",
      "WHAT THE FIGURE COUNTS" not in frozen and "LITERAL ELSEWHERE" not in frozen)
# The pin itself lives in tests/test_tool_choice_input.py (FROZEN_RULES_SHA256); repeated here so
# a bullet pasted into the wrong constant fails in this file too.
check("and they are byte for byte the frozen v1.3 text",
      hashlib.sha256(frozen.encode("utf-8")).hexdigest()
      == "5a9dac889675814df1765c3ba08419ac80788e187d6dcac409bd576076d784b0")

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
