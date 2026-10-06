"""test_sql_notes_companion.py - a small result carries the notes of the rows behind it
(spec-fix1 parts a and b, 2026-10-01).

A correction or a rival printed figure is often recorded in a row's own `notes` cell, right
next to the figure the question asks for, and it never reached the answer: the writer
selected only the figure's column (a one-row lookup), or aggregated (a SUM over one
matched row), and the SQL-writer rule that asks for notes exempts aggregates. The note
was on record and was lost before the answer writer saw it.

So `execute_sql_query` now reads it in code. For ONE plain SELECT over ONE table (no JOIN,
no set operation, no sub-SELECT) whose table has a column named `notes` that the SELECT
list did not ask for, it reads that column over the SAME `FROM ... WHERE`; when those
match 1 to 3 rows and a note is non-empty, the result ends with the block
"NOTES ON THE ROWS BEHIND THIS RESULT:", one line per distinct note, each cut at 800
characters. Aggregates are included; `remarks` is never read; any error drops the block
and nothing else.

Every check marked RED fails against sql_tool.py as it stood before this change.

Wave 3, G4 + G5 (2026-10-03) - one change to the notes format, sections 4 and 7-9:
  * a row the question NAMES WHOLE - a value of a read table's identifier column, printed whole
    in the question (the HIERARCHY walk's own `_printed_code` match) - that the result prints
    gets its notes whatever the query's shape or row count (G4 a);
  * a note over the cap keeps its start AND its end, around " … ": notes are append-only, so
    the end holds the newest finding (G4 b);
  * every bullet opens with its row's identifier, and the same note on two rows is two
    bullets (G5 2).
Checks marked RED fail against sql_tool.py as it stood before that change (bf20a37).

Fix round 1 (ruling W3A3-R1 and the review's minors): the note cap is 2,000 characters and both
cut points land on white space (section 4); a bullet's key adds the column that most raises
distinctness when the card identifier repeats (section 8). Checks marked "RED (W3A3-R1)" or
"RED (R1 key)" fail against 79c7680.

Run:
    venv/Scripts/python -X utf8 tests/test_sql_notes_companion.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import sql_tool  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


HEAD = "NOTES ON THE ROWS BEHIND THIS RESULT:"
NOTE_F1 = "printed 40 struck through by hand, 45 written beside it"
NOTE_METER = "the summary sheet prints 9 for the same meters"
# G4 b: a long note's start and end differ, so a test can tell which part a cut kept. Notes are
# append-only, so the END is the newest finding. Fix round 1 (ruling W3A3-R1): the cap is 2,000
# characters and a cut lands on white space, so the long note is over 2,000 characters and made of
# tokens that occur once each - a token split by a cut is then in no part of the original. Its
# newest segment is several hundred characters long and must survive whole.
LONG_HEAD = "first finding: the sheet prints 40 for this way"
# Token widths are chosen so that every cut position - the 800 cap's and the 2,000 cap's - falls
# inside a token, so a cut that does not move to white space is caught.
LONG_TAIL = ("latest finding: the owner answered that the way is 45 " +
             " ".join(f"answer{i:04d}" for i in range(52))).strip()
LONG_NOTE = (LONG_HEAD + " | " + " ".join(f"w{i:04d}q" for i in range(320)) + " | " + LONG_TAIL)
# A note under the cap with a figure in its middle: never cut.
MID_FIGURE = "the way is rated 46.75 kW on the second sheet"
MID_NOTE = (" ".join(f"m{i:03d}z" for i in range(125)) + " | " + MID_FIGURE + " | "
            + " ".join(f"n{i:03d}z" for i in range(125)))

FEEDERS = {
    "table_name": "bld_feeders",
    "columns": ["board", "feeder", "breaker_a", "notes", "remarks"],
    "rows": [
        {"board": "QX-1", "feeder": "QX-1-F1", "breaker_a": "40", "notes": NOTE_F1,
         "remarks": "remark of the first way"},
        {"board": "QX-1", "feeder": "QX-1-F2", "breaker_a": "32", "notes": "",
         "remarks": ""},
        {"board": "QX-2", "feeder": "QX-2-F1", "breaker_a": "25", "notes": "spare way kept",
         "remarks": ""},
        {"board": "QX-2", "feeder": "QX-2-F2", "breaker_a": "25", "notes": "spare way kept",
         "remarks": ""},
        {"board": "QX-2", "feeder": "QX-2-F3", "breaker_a": "16", "notes": "second meter row",
         "remarks": ""},
        {"board": "QX-4", "feeder": "QX-4-F1", "breaker_a": "10", "notes": "n1", "remarks": ""},
        {"board": "QX-4", "feeder": "QX-4-F2", "breaker_a": "10", "notes": "n2", "remarks": ""},
        {"board": "QX-4", "feeder": "QX-4-F3", "breaker_a": "10", "notes": "n3", "remarks": ""},
        {"board": "QX-4", "feeder": "QX-4-F4", "breaker_a": "10", "notes": "n4", "remarks": ""},
        {"board": "QX-5", "feeder": "QX-5-F1", "breaker_a": "63", "notes": LONG_NOTE,
         "remarks": ""},
        {"board": "QX-6", "feeder": "QX-6-F1", "breaker_a": "20", "notes": "", "remarks": ""},
        {"board": "QX-7", "feeder": "QX-7-F1", "breaker_a": "50", "notes": MID_NOTE,
         "remarks": ""},
    ],
    "row_count": 12,
}
METERS = {
    "table_name": "bld_meters",
    "columns": ["descr", "qty", "notes"],
    "rows": [
        {"descr": "Flow sensor", "qty": "12", "notes": ""},
        {"descr": "Energy meter", "qty": "7", "notes": NOTE_METER},
        {"descr": "Flow sensor spare", "qty": "2", "notes": ""},
    ],
    "row_count": 3,
}
PLAIN = {
    "table_name": "bld_plain",
    "columns": ["tag", "qty", "remarks"],
    "rows": [{"tag": "PX-1", "qty": "3", "remarks": "kept for the plain table"}],
    "row_count": 1,
}
TABLES = [FEEDERS, METERS, PLAIN]
CARDS = [{"table": "bld_feeders", "holds": "one row per outgoing way of a board",
          "caveats": ["a card caveat that rides as a NOTE line"]}]


class _ExecResult:
    def __init__(self, data):
        self.data = data


class _Supabase:
    """Serves every table for `structured_data`; `.eq()` and `.order()` are accepted."""

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


QUERIES = []  # every query `execute_sql_query` ran on DuckDB in the last `run`


def run(sql, cards=(), tables=TABLES, question="q"):
    """`execute_sql_query` end to end - the real loader, DuckDB and result assembly - with
    a fake SQL writer that returns `sql`. No model, no network. Every query it runs is
    recorded in QUERIES."""
    saved = (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
             sql_tool._load_table_cards, sql_tool._execute_with_timeout)
    real_exec = sql_tool._execute_with_timeout

    def _recording(con, query, timeout=sql_tool.SQL_QUERY_TIMEOUT):
        QUERIES.append(query)
        return real_exec(con, query, timeout)

    sql_tool.genai.Client = _Client
    sql_tool.get_llm_api_key = lambda: "fake-key"
    sql_tool.get_llm_model = lambda: "fake-model"
    sql_tool._load_table_cards = lambda *a, **k: list(cards)
    sql_tool._execute_with_timeout = _recording
    _Models.sql = sql
    QUERIES.clear()
    try:
        return sql_tool.execute_sql_query(question, "u-1", _Supabase(tables))
    finally:
        (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
         sql_tool._load_table_cards, sql_tool._execute_with_timeout) = saved


def notes_block(text):
    """The block's note lines, or None when the result carries no block."""
    if HEAD not in text:
        return None
    tail = text[text.index(HEAD) + len(HEAD):]
    return [ln[2:] for ln in tail.strip("\n").split("\n") if ln.startswith("- ")]


# ---------------------------------------------------------------------------
print("1. A one-row lookup that did not SELECT notes ends with the row's note (RED)")
# ---------------------------------------------------------------------------
out1 = run("SELECT breaker_a FROM \"bld_feeders\" WHERE board = 'QX-1' AND feeder = 'QX-1-F1'")
check("the figure itself is still there", "| 40.0 |" in out1, out1)
check("RED: the result carries the NOTES ON THE ROWS BEHIND THIS RESULT block",
      HEAD in out1, out1[-300:])
check("the block carries that row's note, verbatim",
      notes_block(out1) == [NOTE_F1], notes_block(out1))
check("and the result ENDS with it: nothing follows the note",
      out1.rstrip().endswith("- " + NOTE_F1), out1[-200:])
check("it comes after the SQL line, never inside the rendered table",
      HEAD in out1 and out1.index("SQL: `") < out1.index(HEAD)
      and not any(ln.startswith("|") and HEAD in ln for ln in out1.splitlines()), out1)
check("`remarks` is never read, though the matched row has one",
      "remark of the first way" not in out1, out1)

out1c = run("SELECT breaker_a FROM \"bld_feeders\" WHERE feeder = 'QX-1-F1'", cards=CARDS)
check("with a card behind the table, the SOURCE and NOTE lines come first and the block "
      "still ends the result",
      HEAD in out1c and "SOURCE - bld_feeders" in out1c
      and out1c.index("SOURCE - bld_feeders") < out1c.index(HEAD)
      and out1c.rstrip().endswith("- " + NOTE_F1), out1c[-400:])

out1d = run("SELECT feeder FROM \"bld_feeders\" WHERE board = 'QX-2'")
check("three matched rows: each DISTINCT note once, in row order",
      notes_block(out1d) == ["spare way kept", "second meter row"], notes_block(out1d))

out1e = run('SELECT breaker_a FROM "bld_feeders" AS f WHERE f.feeder = \'QX-1-F2\'')
check("matched rows whose notes are all empty: no block",
      HEAD not in out1e, out1e[-200:])

out1f = run("SELECT breaker_a FROM \"bld_feeders\" f WHERE f.board = 'QX-1' "
            "AND f.remarks <> 'FROM x JOIN y UNION SELECT notes'")
check("an aliased table, and SQL words inside a string literal, are read as what they "
      "are: the block still comes", notes_block(out1f) == [NOTE_F1], out1f[-300:])

# ---------------------------------------------------------------------------
print("\n2. A SUM over one matched row carries the note too (RED)")
# ---------------------------------------------------------------------------
out2 = run("SELECT SUM(qty) FROM \"bld_meters\" WHERE descr ILIKE '%energy meter%'")
check("the aggregate's figure is there", "| 7.0 |" in out2, out2)
check("RED: an aggregate is NOT exempt - the matched row's note rides with the figure",
      notes_block(out2) == [NOTE_METER], out2[-300:])
out2b = run("SELECT COUNT(*) AS n FROM \"bld_feeders\" WHERE board = 'QX-1' GROUP BY board")
check("RED: a GROUP BY over two matched rows reads the notes of those rows",
      notes_block(out2b) == [NOTE_F1], out2b[-300:])

# ---------------------------------------------------------------------------
print("\n3. Nothing for a JOIN, a set operation, a sub-SELECT, a 4-row match, or a "
      "table without notes")
# ---------------------------------------------------------------------------
cases3 = {
    "a JOIN": ("SELECT a.breaker_a FROM \"bld_feeders\" a JOIN \"bld_feeders\" b "
               "ON a.feeder = b.feeder WHERE a.feeder = 'QX-1-F1'"),
    "a UNION": ("SELECT breaker_a FROM \"bld_feeders\" WHERE feeder = 'QX-1-F1' UNION "
                "SELECT breaker_a FROM \"bld_feeders\" WHERE feeder = 'QX-2-F1'"),
    "a sub-SELECT": ("SELECT breaker_a FROM \"bld_feeders\" WHERE feeder IN "
                     "(SELECT feeder FROM \"bld_feeders\" WHERE feeder = 'QX-1-F1')"),
    "a 4-row match": "SELECT COUNT(*) FROM \"bld_feeders\" WHERE board = 'QX-4'",
    "a table with no notes column (remarks only)": "SELECT qty FROM \"bld_plain\" WHERE tag = 'PX-1'",
    "a query that already selected notes": ("SELECT breaker_a, notes FROM \"bld_feeders\" "
                                            "WHERE feeder = 'QX-1-F1'"),
    "SELECT *": "SELECT * FROM \"bld_feeders\" WHERE feeder = 'QX-1-F1'",
    "a comma join": ("SELECT breaker_a FROM \"bld_feeders\", \"bld_plain\" "
                     "WHERE feeder = 'QX-1-F1'"),
}
for label, sql3 in cases3.items():
    out3 = run(sql3)
    check(f"{label}: the query ran", "SQL query failed" not in out3, out3[:300])
    check(f"{label}: no notes block", HEAD not in out3, out3[-300:])
    if label != "a 4-row match":
        # Refused by its SHAPE, before reading anything - not by a companion query that
        # happened to fail (which would also leave no block, and would hide a missing guard).
        check(f"{label}: refused by its shape - no extra query ran", QUERIES == [sql3], QUERIES)
check("a 4-row match is refused by its COUNT: the one extra read saw four rows",
      (run(cases3["a 4-row match"]) and len(QUERIES) == 2
       and QUERIES[1].endswith(f"LIMIT {sql_tool.ROW_NOTES_MAX_ROWS + 1}")), QUERIES)
check("the plain table's remarks never surface",
      "kept for the plain table" not in run(cases3["a table with no notes column (remarks only)"]))
check("a query that already selected notes shows the note once, in its own table cell",
      run(cases3["a query that already selected notes"]).count(NOTE_F1) == 1)

# ---------------------------------------------------------------------------
print("\n4. A note is capped at 2,000 characters - its start AND its end kept, cut on white space "
      "(RED; G4 b, fix round 1)")
# ---------------------------------------------------------------------------
# Wave 3, G4 b: the cut used to keep the first 797 characters and end with "...". Notes are
# append-only - the newest finding, or an owner's answer, is written at the END - so a head cut
# dropped exactly the correction a question needed. A long note keeps its start, then " … ", then
# its end. Fix round 1 (ruling W3A3-R1): an 800 cap still cut a figure out of the middle of a note
# under 2,000 characters, and owner answers and tokens mid-word; the cap is 2,000 characters - a
# block holds at most three rows - half of it the start and half the end, and BOTH cut points move
# to white space, so no token is ever split.
check("RED (W3A3-R1): the cap is 2,000 characters per note", sql_tool.ROW_NOTE_MAX_CHARS == 2000,
      sql_tool.ROW_NOTE_MAX_CHARS)
out4m = run("SELECT breaker_a FROM \"bld_feeders\" WHERE feeder = 'QX-7-F1'")
check("(the mid-size note is between the old cap and the new one)", 800 < len(MID_NOTE) < 2000,
      len(MID_NOTE))
check("RED (W3A3-R1): a note under the cap with a figure in its middle comes whole, the figure in it",
      notes_block(out4m) == [MID_NOTE] and MID_FIGURE in out4m, (notes_block(out4m) or [""])[0][-80:])

out4 = run("SELECT breaker_a FROM \"bld_feeders\" WHERE feeder = 'QX-5-F1'")
got4 = notes_block(out4) or []
cut4 = got4[0] if got4 else ""
check("(the long note is over the cap)", len(LONG_NOTE) > 2000, len(LONG_NOTE))
check("RED: the long note is carried", len(got4) == 1, out4[-300:])
check("cut to at most 2,000 characters", bool(cut4) and len(cut4) <= 2000, len(cut4))
check("RED (G4 b): it says where it was cut - ' … ' stands between its start and its end",
      cut4.count(" … ") == 1 and not cut4.endswith("..."), cut4[-40:])
check("the start of the note is kept verbatim", cut4.startswith(LONG_HEAD + " | w0000q w0001q"),
      cut4[:80])
check("RED (G4 b): and its END is kept verbatim - the newest segment whole",
      cut4.endswith(" | " + LONG_TAIL) or cut4.endswith(" … " + LONG_TAIL), cut4[-120:])
original_tokens = set(LONG_NOTE.split(" "))
split4 = [tok for tok in cut4.split(" ") if tok != "…" and tok not in original_tokens]
check("RED (W3A3-R1): no cut lands inside a token - every token of the cut note is one of the "
      "original's", bool(cut4) and not split4, split4[:5])
head4 = cut4.split(" … ")[0] if " … " in cut4 else ""
check("RED (W3A3-R1): about half the cap is the start, the rest the end",
      sql_tool.ROW_NOTE_HEAD_CHARS - 12 <= len(head4) <= sql_tool.ROW_NOTE_HEAD_CHARS
      and len(cut4) >= 2000 - 12, (len(head4), len(cut4)))
check("a note shorter than the cap is never cut", notes_block(out1) == [NOTE_F1])
short4 = ("aaaa bbbb cccc dddd " * 30).strip() + " eeeeeeee"
cut4s = sql_tool._cut_note(short4, 60, 30)
check("RED (W3A3-R1): a shorter cap (the shared-value line's) cuts on white space too",
      " … " in cut4s and len(cut4s) <= 60
      and all(tok in set(short4.split(" ")) for tok in cut4s.split(" ") if tok != "…")
      and cut4s.endswith("eeeeeeee"), cut4s)

# ---------------------------------------------------------------------------
print("\n5. An error in the companion drops the block and nothing else")
# ---------------------------------------------------------------------------
real_exec = sql_tool._execute_with_timeout
calls = []


def _fail_after_first(con, sql, timeout=sql_tool.SQL_QUERY_TIMEOUT):
    calls.append(sql)
    if len(calls) > 1:
        raise RuntimeError("companion read failed")
    return real_exec(con, sql, timeout)


sql_tool._execute_with_timeout = _fail_after_first
try:
    out5 = run("SELECT breaker_a FROM \"bld_feeders\" WHERE feeder = 'QX-1-F1'")
finally:
    sql_tool._execute_with_timeout = real_exec
check("RED: the companion really did try a second read", len(calls) >= 2, calls)
check("the answer is still the result - no failure text", "SQL query failed" not in out5
      and "| 40.0 |" in out5, out5[:300])
check("the block is dropped", HEAD not in out5, out5[-200:])
check("the SQL line is still there", "SQL: `SELECT breaker_a" in out5, out5)

# ---------------------------------------------------------------------------
print("\n6. Only the rows behind the result: LIMIT keeps its rows, HAVING is refused "
      "(review fix round 1)")
# ---------------------------------------------------------------------------
# The block says its notes are those "of the rows behind this result". A row list cut by
# ORDER BY ... LIMIT returns only some of the rows its WHERE matched, so its notes query
# keeps that ORDER BY and LIMIT; a HAVING (or QUALIFY) filters rows after the WHERE, and a
# LIMIT over groups returns only some of them, so neither can be read off the WHERE: those
# are refused by their shape, before any extra query - like a JOIN.
o6a = run("SELECT feeder FROM \"bld_feeders\" WHERE board = 'QX-2' ORDER BY feeder DESC LIMIT 1")
check("RED: a row list with ORDER BY ... LIMIT 1 carries the note of the ONE row it returned",
      notes_block(o6a) == ["second meter row"], notes_block(o6a))
check("its notes query keeps the query's own ORDER BY and LIMIT",
      len(QUERIES) == 2 and "ORDER BY feeder DESC LIMIT 1" in QUERIES[1], QUERIES)
o6b = run("SELECT feeder FROM \"bld_feeders\" WHERE board = 'QX-2' ORDER BY feeder LIMIT 2")
check("RED: LIMIT 2 - the notes of the two rows returned, and not of the third",
      notes_block(o6b) == ["spare way kept"], notes_block(o6b))
o6c = run("SELECT breaker_a, feeder FROM \"bld_feeders\" WHERE board = 'QX-2' "
          "ORDER BY 2 DESC LIMIT 1")
check("RED: a positional ORDER BY still orders by the column it names in the query's own "
      "SELECT list", notes_block(o6c) == ["second meter row"], notes_block(o6c))
o6d = run("SELECT feeder, COUNT(*) OVER () AS n FROM \"bld_feeders\" WHERE board = 'QX-2' "
          "ORDER BY feeder DESC LIMIT 1")
check("RED: a window function is no aggregate - its LIMIT still picks the rows behind it",
      notes_block(o6d) == ["second meter row"], notes_block(o6d))
o6e = run("SELECT SUM(qty) FROM \"bld_meters\" WHERE descr ILIKE '%energy meter%' LIMIT 1")
check("an aggregate's LIMIT chooses among its output, not the rows behind it: every "
      "matched row still counts", notes_block(o6e) == [NOTE_METER], o6e[-300:])

for label6, sql6 in {
    "a HAVING": ("SELECT board, COUNT(*) AS n FROM \"bld_feeders\" WHERE board = 'QX-1' "
                 "GROUP BY board HAVING COUNT(*) > 1"),
    "a QUALIFY": ("SELECT feeder FROM \"bld_feeders\" WHERE board = 'QX-2' "
                  "QUALIFY row_number() OVER (ORDER BY feeder) = 1"),
    "a LIMIT over groups": ("SELECT board, COUNT(*) AS n FROM \"bld_feeders\" WHERE feeder IN "
                            "('QX-1-F1', 'QX-2-F3') GROUP BY board ORDER BY board LIMIT 1"),
    "a DISTINCT row list with a LIMIT": (
        "SELECT DISTINCT board FROM \"bld_feeders\" WHERE feeder IN ('QX-1-F1', 'QX-2-F3') "
        "ORDER BY board LIMIT 1"),
}.items():
    out6 = run(sql6)
    check(f"{label6}: the query ran", "SQL query failed" not in out6, out6[:300])
    check(f"RED: {label6}: no notes block", HEAD not in out6, out6[-300:])
    check(f"RED: {label6}: refused by its shape - no extra query ran", QUERIES == [sql6], QUERIES)

# ---------------------------------------------------------------------------
print("\n7. A row the question names whole carries its notes, whatever the query's shape or "
      "row count (G4 a)")
# ---------------------------------------------------------------------------
# Measured on the goal-function run of 2026-10-01: a question named one unit by its code, the
# writer walked its parents with a nested sub-SELECT, and the unit's own correction - its printed
# location is a sheet-template leftover; the owner placed it elsewhere - sat in that row's notes.
# The block above refuses a sub-SELECT, a JOIN and more than three rows, so it never came. Now a
# row the question NAMES WHOLE (the HIERARCHY walk's own `_printed_code` match against the card's
# identifier column) that the result prints carries its notes, whatever the query's shape.
NOTE_QD7 = "the printed location is a sheet-template leftover; the owner places this unit on Deck Zero"
UNITS = {
    "table_name": "bld_units",
    "columns": ["unit_ref", "kind", "fed_from", "location", "notes"],
    "rows": [
        {"unit_ref": "QM-1", "kind": "head", "fed_from": "", "location": "Deck Zero",
         "notes": "head unit note"},
        {"unit_ref": "QS-1", "kind": "branch", "fed_from": "QM-1", "location": "Deck One",
         "notes": "branch unit note"},
        {"unit_ref": "QD-7", "kind": "leaf", "fed_from": "QS-1", "location": "Deck Six Lab",
         "notes": NOTE_QD7},
        {"unit_ref": "QD-7-1", "kind": "leaf", "fed_from": "QD-7", "location": "Deck Six Lab",
         "notes": "sub unit note"},
        {"unit_ref": "QD-8", "kind": "leaf", "fed_from": "QS-1", "location": "Deck One",
         "notes": "leaf eight note"},
        {"unit_ref": "QD-9", "kind": "leaf", "fed_from": "QS-1", "location": "Deck One",
         "notes": ""},
    ] + [{"unit_ref": "QX-9", "kind": "spare", "fed_from": "QS-1", "location": "Deck One",
          "notes": f"spare copy {i}"} for i in range(4)],
    "row_count": 10,
}
UNITS_CARD = {"table": "bld_units", "identifier_column": "unit_ref",
              "holds": "one row per supply unit and the unit it is fed from"}
UTABLES = [UNITS]


def urun(question, sql, cards=(UNITS_CARD,)):
    return run(sql, cards=list(cards), tables=UTABLES, question=question)


Q7 = "Where is unit QD-7, and what feeds it all the way back to the head unit?"
SUBSEL = ("SELECT unit_ref, location, fed_from FROM \"bld_units\" WHERE unit_ref = 'QD-7' OR "
          "unit_ref IN (SELECT fed_from FROM \"bld_units\" WHERE unit_ref = 'QD-7')")
o7a = urun(Q7, SUBSEL)
check("(the sub-SELECT returned the unit and its parent)",
      "| QD-7 | Deck Six Lab | QS-1 |" in o7a and "| QS-1 | Deck One | QM-1 |" in o7a, o7a[:400])
check("RED: under a sub-SELECT the named row's notes come, keyed to it",
      notes_block(o7a) == ["QD-7: " + NOTE_QD7], notes_block(o7a))
check("RED: and the block still ends the result, after the SOURCE line",
      o7a.rstrip().endswith("- QD-7: " + NOTE_QD7)
      and o7a.index("SOURCE - bld_units") < o7a.index(HEAD), o7a[-400:])
check("only the named row's: the parent the result also prints is not named",
      "branch unit note" not in o7a, o7a[-300:])

o7b = urun(Q7, "SELECT u.unit_ref, p.location AS parent_location FROM \"bld_units\" u JOIN "
               "\"bld_units\" p ON p.unit_ref = u.fed_from WHERE u.unit_ref = 'QD-7'")
check("RED: under a JOIN the named row's notes come", notes_block(o7b) == ["QD-7: " + NOTE_QD7],
      notes_block(o7b) or o7b[-300:])

o7c = urun("Which leaf units are there, and is QD-7 one of them?",
           "SELECT unit_ref, location FROM \"bld_units\" WHERE kind = 'leaf'")
check("(four rows matched - more than the block's three)", "| QD-9 | Deck One |" in o7c, o7c[:400])
check("RED: over more than three rows the named row's notes come - and only its",
      notes_block(o7c) == ["QD-7: " + NOTE_QD7], notes_block(o7c) or o7c[-300:])

o7d = urun(Q7, "SELECT p.location FROM \"bld_units\" u JOIN \"bld_units\" p "
               "ON p.unit_ref = u.fed_from WHERE u.unit_ref = 'QD-7'")
check("a named row the result does not print: no block", HEAD not in o7d, o7d[-300:])

o7e = urun("What feeds unit QD-7-1?",
           "SELECT u.unit_ref, u.fed_from FROM \"bld_units\" u JOIN \"bld_units\" p "
           "ON p.unit_ref = u.fed_from WHERE u.unit_ref = 'QD-7-1'")
check("RED: a code printed inside a longer code names the longer one only - the longest match "
      "wins, though the result prints both", notes_block(o7e) == ["QD-7-1: sub unit note"],
      notes_block(o7e) or o7e[-300:])

o7f = urun("What is unit QX-9?", "SELECT unit_ref, location FROM \"bld_units\" "
                                 "WHERE unit_ref = 'QX-9' OR unit_ref = 'QD-8'")
check("a value more than three rows hold names no single row: no block", HEAD not in o7f,
      o7f[-300:])

o7g = urun(Q7, SUBSEL, cards=())
check("no card, so no identifier column to name a row by: no block", HEAD not in o7g, o7g[-300:])

o7h = urun(Q7, "SELECT u.unit_ref, u.notes FROM \"bld_units\" u JOIN \"bld_units\" p "
               "ON p.unit_ref = u.fed_from WHERE u.unit_ref = 'QD-7'")
check("a note the result already prints in a cell is not repeated",
      HEAD not in o7h and o7h.count(NOTE_QD7) == 1, o7h[-300:])

o7i = urun("Which units stand on Deck Six?\n(Investigation step 2: the previous query matched "
           "nothing. Previous SQL: `SELECT unit_ref FROM \"bld_units\" WHERE unit_ref = 'QD-7'`)",
           SUBSEL)
check("a code printed only in the loop's step suffix names no row: no block", HEAD not in o7i,
      o7i[-300:])

o7j = urun(Q7, "SELECT unit_ref, location FROM \"bld_units\" WHERE unit_ref = 'QD-7'")
check("RED: a one-row lookup that the block already reads: the named row's note comes once, keyed",
      notes_block(o7j) == ["QD-7: " + NOTE_QD7], notes_block(o7j) or o7j[-300:])

o7k = urun("Is QD-7 on Deck Six?", "SELECT COUNT(*) AS n FROM \"bld_units\" WHERE kind = 'leaf'")
check("an aggregate that prints no row's identifier: no block", HEAD not in o7k, o7k[-300:])

# The named-row read on its own: a row whose notes are blank names nothing - no empty pair.
import duckdb  # noqa: E402

con7 = duckdb.connect(":memory:")
con7.execute('CREATE TABLE "bld_units" (' + ", ".join(f'"{c}" VARCHAR' for c in UNITS["columns"]) + ")")
for row7 in UNITS["rows"]:
    con7.execute('INSERT INTO "bld_units" VALUES (?, ?, ?, ?, ?)', [row7[c] for c in UNITS["columns"]])
LEAVES = "SELECT unit_ref FROM \"bld_units\" WHERE kind = 'leaf'"
pairs7 = sql_tool._named_row_notes(con7, Q7, LEAVES, UTABLES, [UNITS_CARD], [("QD-7",), ("QD-9",)])
check("called on its own, it returns the named row's (identifier, note)",
      pairs7 == [("QD-7", NOTE_QD7)], pairs7)
blank7 = sql_tool._named_row_notes(con7, "Is unit QD-9 on Deck One?", LEAVES, UTABLES, [UNITS_CARD],
                                   [("QD-7",), ("QD-9",)])
check("RED: a named row whose notes are blank names nothing - no empty pair comes back",
      blank7 == [], blank7)

# An error in the named-row read drops the block and nothing else.
real_exec7 = sql_tool._execute_with_timeout
calls7 = []


def _fail_named(con, sql, timeout=sql_tool.SQL_QUERY_TIMEOUT):
    calls7.append(sql)
    if len(calls7) > 1:
        raise RuntimeError("named-row read failed")
    return real_exec7(con, sql, timeout)


sql_tool._execute_with_timeout = _fail_named
try:
    o7l = urun(Q7, SUBSEL)
finally:
    sql_tool._execute_with_timeout = real_exec7
check("RED: the named-row read was really attempted",
      any('"notes"' in c and "'QD-7'" in c for c in calls7[1:]), calls7)
check("an error there drops the block; the result stands, with no failure text",
      HEAD not in o7l and "| QD-7 | Deck Six Lab | QS-1 |" in o7l
      and "SQL query failed" not in o7l, o7l[-300:])

# ---------------------------------------------------------------------------
print("\n8. Every bullet opens with its row's identifier; one note on two rows is two "
      "bullets (G5)")
# ---------------------------------------------------------------------------
# Measured on the goal-function run of 2026-10-01: rows came back for a place the question named,
# and one row's note - an alias it is also printed as - was read as a separate place,
# because no bullet said which row it belonged to. Every bullet now opens with its row's identifier
# - the column the table's card names as its identifier; a column guessed from its name could be a
# parent or place key and would mislabel the row - and the same note on two rows is two facts, so
# it is no longer folded into one.
FEEDERS_CARD = {"table": "bld_feeders", "identifier_column": "feeder",
                "holds": "one row per outgoing way of a board"}
o8a = run("SELECT breaker_a FROM \"bld_feeders\" WHERE board = 'QX-2'", cards=[FEEDERS_CARD])
check("RED: each bullet opens with its row's identifier, though the result does not print it, "
      "and the same note on two rows is two bullets",
      notes_block(o8a) == ["QX-2-F1: spare way kept", "QX-2-F2: spare way kept",
                           "QX-2-F3: second meter row"], notes_block(o8a))
o8b = run("SELECT feeder FROM \"bld_feeders\" WHERE board = 'QX-2' ORDER BY feeder DESC LIMIT 1",
          cards=[FEEDERS_CARD])
check("RED: a row list cut by ORDER BY ... LIMIT keys the one row it returned",
      notes_block(o8b) == ["QX-2-F3: second meter row"], notes_block(o8b))
o8c = run("SELECT SUM(qty) FROM \"bld_meters\" WHERE descr ILIKE '%energy meter%'")
check("a table with no identifier column at all: the bullet goes unkeyed, as before",
      notes_block(o8c) == [NOTE_METER], notes_block(o8c))
check("and with no key, the same note on two rows still comes once - it would say nothing new",
      notes_block(out1d) == ["spare way kept", "second meter row"], notes_block(out1d))
TAGGED = {
    "table_name": "bld_tagged",
    "columns": ["descr", "unit_tag", "qty", "notes"],
    "rows": [{"descr": "Sensor", "unit_tag": "TG-1", "qty": "1", "notes": "moved by the owner"},
             {"descr": "Sensor", "unit_tag": "", "qty": "1", "notes": "no tag printed"}],
    "row_count": 2,
}
o8d = run("SELECT qty FROM \"bld_tagged\" WHERE descr = 'Sensor'", tables=[TAGGED],
          cards=[{"table": "bld_tagged", "identifier_column": "unit_tag", "holds": "h"}])
check("RED: the card's identifier column keys the bullet; a row whose identifier is blank goes "
      "unkeyed", notes_block(o8d) == ["TG-1: moved by the owner", "no tag printed"],
      notes_block(o8d))
o8d2 = run("SELECT qty FROM \"bld_tagged\" WHERE descr = 'Sensor'", tables=[TAGGED])
check("no card: unkeyed, though a column's name ends like an identifier's - a guessed key could "
      "be a parent or place key", notes_block(o8d2) == ["moved by the owner", "no tag printed"],
      notes_block(o8d2))
o8d3 = run("SELECT qty FROM \"bld_tagged\" WHERE descr = 'Sensor'", tables=[TAGGED],
           cards=[{"table": "bld_tagged", "identifier_column": "unit_code", "holds": "h"}])
check("a card naming a column the loaded table lacks: unkeyed",
      notes_block(o8d3) == ["moved by the owner", "no tag printed"], notes_block(o8d3))
WIDE_KEY = dict(UNITS, rows=[dict(UNITS["rows"][2], unit_ref="QD-7 " + "wide " * 30)],
                row_count=1)
o8e = run("SELECT location FROM \"bld_units\" WHERE kind = 'leaf'", cards=[UNITS_CARD],
          tables=[WIDE_KEY])
got8e = notes_block(o8e) or [""]
check("RED: a very long identifier is shortened in its bullet, never the note",
      len(got8e) == 1 and got8e[0].endswith(": " + NOTE_QD7)
      and len(got8e[0]) - len(NOTE_QD7) - 2 <= 80, got8e)

# Fix round 1 (the review's minor 2): a card identifier that repeats over the table's rows - a room
# text printed on several ways, or blank on some - keys two rows the same, and their same note then
# reads as one. The key adds the column that most raises distinctness, chosen from the data among
# the table's parent-reference and identifier-shaped columns.
WAYS = {
    "table_name": "bld_ways",
    "columns": ["room_text", "sheet_no", "way_no", "fed_from", "load_w", "notes"],
    "rows": [
        {"room_text": "Quiet Lab", "sheet_no": "S1", "way_no": "W1", "fed_from": "PX-1",
         "load_w": "400", "notes": "spare way reused"},
        {"room_text": "Quiet Lab", "sheet_no": "S1", "way_no": "W2", "fed_from": "PX-1",
         "load_w": "600", "notes": "spare way reused"},
        {"room_text": "", "sheet_no": "S1", "way_no": "W3", "fed_from": "PX-2", "load_w": "100",
         "notes": "no room printed on this way"},
        {"room_text": "Store", "sheet_no": "S1", "way_no": "W4", "fed_from": "PX-2", "load_w": "200",
         "notes": ""},
    ],
    "row_count": 4,
}
WAYS_CARD = {"table": "bld_ways", "identifier_column": "room_text",
             "holds": "one row per outgoing way of a board"}
o8f = run("SELECT load_w FROM \"bld_ways\" WHERE fed_from = 'PX-1'", cards=[WAYS_CARD],
          tables=[WAYS])
check("RED (R1 key): a repeated identifier is keyed with the column that most raises "
      "distinctness - two rows, the same note, two bullets",
      notes_block(o8f) == ["Quiet Lab (way_no W1): spare way reused",
                           "Quiet Lab (way_no W2): spare way reused"], notes_block(o8f))
o8g = run("SELECT load_w FROM \"bld_ways\" WHERE way_no = 'W3'", cards=[WAYS_CARD], tables=[WAYS])
check("RED (R1 key): a row whose identifier is blank is keyed by that column alone",
      notes_block(o8g) == ["way_no W3: no room printed on this way"], notes_block(o8g))
check("the column is chosen from the data: the parent reference and the column that is the same "
      "on every row raise distinctness less, so they are not chosen",
      "fed_from PX-1" not in o8f and "sheet_no" not in o8f, o8f[-300:])
check("an identifier that is unique over the table keeps its key alone (unchanged)",
      notes_block(o8a) == ["QX-2-F1: spare way kept", "QX-2-F2: spare way kept",
                           "QX-2-F3: second meter row"], notes_block(o8a))
TIED = dict(WAYS, columns=["room_text", "sheet_no", "way_no", "unit_tag", "load_w", "notes"],
            rows=[dict({k: v for k, v in r.items() if k != "fed_from"},
                       unit_tag=f"TG-{i}") for i, r in enumerate(WAYS["rows"], 1)])
o8h = run("SELECT load_w FROM \"bld_ways\" WHERE sheet_no = 'S1' AND way_no = 'W1'",
          cards=[WAYS_CARD], tables=[TIED])
check("RED (R1 key): two columns raising distinctness equally - the first in the table wins "
      "when neither is more distinct on its own",
      notes_block(o8h) == ["Quiet Lab (way_no W1): spare way reused"], notes_block(o8h))

# ---------------------------------------------------------------------------
print("\n9. The answer rule reads the key (G5; answer prompts only)")
# ---------------------------------------------------------------------------
from app.services import openai_client  # noqa: E402

notes_rule = next((ln for ln in openai_client.OUTPUT_FORMAT_RULES.splitlines()
                   if "NOTES ON THE ROWS BEHIND THIS RESULT" in ln), "")
check("(the notes bullet is there)", bool(notes_rule), openai_client.OUTPUT_FORMAT_RULES[-300:])
check("RED: it says each line of the block opens with the identifier of the row it belongs to",
      "opens with the identifier of the row it belongs to" in notes_rule, notes_rule)
check("RED: and that what a note says describes that row - another name it is printed under "
      "included - never a separate item or place",
      "describes that row" in notes_rule and "never a separate item or place" in notes_rule,
      notes_rule)
check("the frozen tool-choice rules carry none of it (ruling W7)",
      "opens with the identifier" not in openai_client.TOOL_CHOICE_FORMAT_RULES)

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
