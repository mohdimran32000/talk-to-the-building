"""test_sql_shared_value.py - a self-join that finds no other row sharing a value looks for it in
the other loaded tables too (wave 3, G8, 2026-10-03).

Measured on the goal-function run of 2026-10-01: asked whether any other unit used one unit's
network address, the writer joined a DERIVED table - one row per unit of the register - to itself
on the address, excluding the unit itself. That table cannot hold a row the register does not
have, and the commissioning sheet's untagged row with the same address is not in the register: the
self-join found nothing, and the answer said no other unit used the address. The commissioning
table, loaded beside it, held the clash and a note saying so.

So when the SQL joins a table to itself on `a.C = b.C` with `a.K <> b.K` (or the partner's K
compared unequal to a literal), and the asked rows found no partner, the values of C those rows
hold are looked up in every OTHER loaded table that has columns named C and K - leaving out the
rows whose K is one of the asked rows' own - and what is found is appended, with its notes, on one
line per table. A self-join that found a partner, a query of another shape, and a table without K
(where the asked row itself could not be told apart) add nothing; an error drops the line and
nothing else.

Every fixture is invented (bld_* tables). Every check marked RED fails against sql_tool.py as it
stood before this change.

Run:
    venv/Scripts/python -X utf8 tests/test_sql_shared_value.py
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


HEAD = "SHARED VALUE ELSEWHERE - "
NOTE_DUP = "DUPLICATE ADDRESS: 10.0.0.50 is also on row 9 of this sheet"
NOTE_UNTAGGED = "UNTAGGED and DUPLICATE ADDRESS - also row 5 of this sheet"

DEPS = {
    "table_name": "bld_deps",
    "columns": ["unit_tag", "net_addr", "switch_room", "notes"],
    "rows": [
        {"unit_tag": "UN-18", "net_addr": "10.0.0.50", "switch_room": "ZR-6", "notes": ""},
        {"unit_tag": "UN-20", "net_addr": "10.0.0.25", "switch_room": "ZR-6", "notes": ""},
        {"unit_tag": "UN-21", "net_addr": "10.0.0.26", "switch_room": "ZR-6", "notes": ""},
        {"unit_tag": "UN-22", "net_addr": "10.0.0.26", "switch_room": "ZR-6", "notes": ""},
    ],
    "row_count": 4,
}
COMMISH = {
    "table_name": "bld_commish",
    "columns": ["sheet_page", "row_no", "unit_tag", "net_addr", "result", "notes"],
    "rows": [
        {"sheet_page": "7", "row_no": "5", "unit_tag": "UN-18", "net_addr": "10.0.0.50",
         "result": "PASS", "notes": NOTE_DUP},
        {"sheet_page": "7", "row_no": "8", "unit_tag": "UN-20", "net_addr": "10.0.0.25",
         "result": "PASS", "notes": ""},
        {"sheet_page": "7", "row_no": "9", "unit_tag": "9F-C", "net_addr": "10.0.0.50",
         "result": "PASS", "notes": NOTE_UNTAGGED},
    ],
    "row_count": 3,
}
# Holds the address but no unit column: the asked unit's own row could not be left out.
PINGS = {
    "table_name": "bld_pings",
    "columns": ["net_addr", "status"],
    "rows": [{"net_addr": "10.0.0.50", "status": "up"}],
    "row_count": 1,
}
TABLES = (DEPS, COMMISH, PINGS)
CARDS = [{"table": "bld_deps", "identifier_column": "unit_tag", "holds": "one row per unit"},
         {"table": "bld_commish", "identifier_column": "row_no",
          "holds": "one row per commissioning-sheet row"},
         {"table": "bld_pings", "identifier_column": "net_addr", "holds": "h"}]


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


PROMPTS = []  # every prompt the fake SQL writer was handed


class _Models:
    sql = ""

    def generate_content(self, *, model, contents, config=None):
        PROMPTS.append(contents)
        return type("_R", (), {"text": _Models.sql, "usage_metadata": None})()


class _Client:
    def __init__(self, *a, **kw):
        self.models = _Models()


QUERIES = []


def run(sql, question="Is any other unit using that address?", tables=TABLES, cards=CARDS):
    """`execute_sql_query` end to end with a fake SQL writer that returns `sql`; every query it
    runs is recorded in QUERIES."""
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
        return sql_tool.execute_sql_query(question, "u-1", _Supabase(list(tables)))
    finally:
        (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
         sql_tool._load_table_cards, sql_tool._execute_with_timeout) = saved


def shared_lines(text):
    return [ln for ln in text.splitlines() if ln.startswith(HEAD)]


SELF = ("SELECT t1.net_addr, t1.switch_room, t2.unit_tag AS other_unit FROM \"bld_deps\" AS t1 "
        "LEFT JOIN \"bld_deps\" AS t2 ON t1.net_addr = t2.net_addr AND t1.unit_tag <> t2.unit_tag "
        "WHERE t1.unit_tag = '{tag}'")

# ---------------------------------------------------------------------------
print("1. A self-join that found no partner: the value is looked up in the other tables (RED)")
# ---------------------------------------------------------------------------
out1 = run(SELF.format(tag="UN-18"))
line1 = (shared_lines(out1) or [""])[0]
check("the writer's own row is still there, with no partner",
      "| 10.0.0.50 | ZR-6 |  |" in out1, out1[:300])
check("RED: the result carries one SHARED VALUE ELSEWHERE line", len(shared_lines(out1)) == 1,
      shared_lines(out1) or out1[-500:])
check("RED: it says the query looked only inside its own table, for that value",
      "only inside bld_deps" in line1 and "'10.0.0.50'" in line1, line1)
check("RED: it names the other table's row that holds the same value - the untagged one",
      "bld_commish" in line1 and "unit_tag = 9F-C" in line1 and "row_no = 9" in line1, line1)
check("RED: with that row's notes", NOTE_UNTAGGED in line1, line1)
check("RED: the asked unit's own row there is left out", "row_no = 5" not in line1
      and NOTE_DUP not in line1, line1)
check("a table holding the value but no unit column is not searched - the asked row could not be "
      "told apart there", "bld_pings" not in line1, line1)
check("RED: it comes after the SQL line, never inside the rendered table",
      bool(line1) and out1.index("SQL: `") < out1.index(HEAD)
      and not any(ln.startswith("|") and HEAD in ln for ln in out1.splitlines()), out1)
# Fix round 1 (the review's minor 4): the line says the other record PRINTS the same value - a row
# there may be another item, or the same item printed another way - never that the value is shared.
check("RED (R1): the line says the other record prints the same value - another item, or the same "
      "item printed another way",
      "bld_commish prints the same net_addr on 1 other row - another item, or the same item printed "
      "another way:" in line1, line1)
check("RED (R1): and never that the value is held or shared", " holds the same " not in line1
      and "is shared" not in line1, line1)

out1b = run("SELECT t1.unit_tag, t1.net_addr, t3.unit_tag AS other_unit FROM \"bld_deps\" AS t1 "
            "LEFT JOIN \"bld_deps\" AS t3 ON t1.net_addr = t3.net_addr AND "
            "t3.unit_tag != 'UN-18' WHERE t1.unit_tag = 'UN-18'")
check("RED: the partner's key compared unequal to the asked literal is the same probe",
      "unit_tag = 9F-C" in "".join(shared_lines(out1b)), shared_lines(out1b) or out1b[-400:])

# The asked table's pre-takeover / historical twin - its name with 'existing_' in it, the pairing the
# SQL prompt's existing-table note uses - prints the same items as they were before the takeover: its
# rows are not other rows of today's table and are not listed, in either direction (fix round 1).
TWIN = {"table_name": "bld_existing_deps", "columns": ["unit_tag", "net_addr", "notes"],
        "rows": [{"unit_tag": "UN-77", "net_addr": "10.0.0.50", "notes": "a pre-takeover unit"}],
        "row_count": 1}
out1t = run(SELF.format(tag="UN-18"), tables=(DEPS, COMMISH, PINGS, TWIN), cards=())
line1t = "\n".join(shared_lines(out1t))
check("RED (R1): the asked table's pre-takeover twin is not searched - its row is not listed",
      bool(line1t) and "bld_existing_deps" not in line1t and "UN-77" not in line1t, line1t)
check("the other record is still listed beside it", "unit_tag = 9F-C" in line1t, line1t)
out1u = run("SELECT t1.net_addr, t2.unit_tag AS other_unit FROM \"bld_existing_deps\" AS t1 "
            "LEFT JOIN \"bld_existing_deps\" AS t2 ON t1.net_addr = t2.net_addr AND "
            "t1.unit_tag <> t2.unit_tag WHERE t1.unit_tag = 'UN-77'",
            tables=(DEPS, COMMISH, PINGS, TWIN), cards=())
line1u = "\n".join(shared_lines(out1u))
check("RED (R1): asked of the twin, today's table is not searched either",
      "bld_commish prints the same net_addr on 2 other rows" in line1u
      and "bld_deps prints" not in line1u, line1u)

# ---------------------------------------------------------------------------
print("\n2. Silent: a unique value, a partner already found, other shapes")
# ---------------------------------------------------------------------------
out2a = run(SELF.format(tag="UN-20"))
check("a value no other row holds - its own commissioning row left out: no line",
      shared_lines(out2a) == [] and "| 10.0.0.25 | ZR-6 |  |" in out2a,
      shared_lines(out2a) or out2a[:300])
out2b = run(SELF.format(tag="UN-21"))
check("a self-join that found its partner does not probe",
      shared_lines(out2b) == [] and "| UN-22 |" in out2b, shared_lines(out2b) or out2b[:300])
check("and runs no lookup in another table", not any("bld_commish" in q for q in QUERIES), QUERIES)
for label2, sql2 in {
    "a join of two different tables": (
        "SELECT d.net_addr, c.unit_tag FROM \"bld_deps\" d LEFT JOIN \"bld_commish\" c "
        "ON d.net_addr = c.net_addr AND d.unit_tag <> c.unit_tag WHERE d.unit_tag = 'UN-20'"),
    "a self-join on two different columns": (
        "SELECT t1.net_addr, t2.unit_tag FROM \"bld_deps\" t1 LEFT JOIN \"bld_deps\" t2 "
        "ON t1.net_addr = t2.switch_room AND t1.unit_tag <> t2.unit_tag WHERE t1.unit_tag = 'UN-18'"),
    "a self-join with no inequality": (
        "SELECT t1.net_addr, t2.unit_tag FROM \"bld_deps\" t1 JOIN \"bld_deps\" t2 "
        "ON t1.net_addr = t2.net_addr WHERE t1.unit_tag = 'UN-18'"),
    "a grouped self-join": (
        "SELECT t1.net_addr, COUNT(t2.unit_tag) AS others FROM \"bld_deps\" t1 LEFT JOIN "
        "\"bld_deps\" t2 ON t1.net_addr = t2.net_addr AND t1.unit_tag <> t2.unit_tag "
        "WHERE t1.unit_tag = 'UN-18' GROUP BY t1.net_addr"),
    "a self-join cut by LIMIT": SELF.format(tag="UN-18") + " LIMIT 1",
    # The partner's key compared unequal to a literal that is NOT the asked row's own key excludes
    # no asked row, so it is no "does another row share this" probe (the extra condition makes the
    # partner side empty, so only that rule keeps the line away).
    "a partner key unequal to some other literal": (
        "SELECT t1.net_addr, t3.unit_tag AS other_unit FROM \"bld_deps\" AS t1 LEFT JOIN "
        "\"bld_deps\" AS t3 ON t1.net_addr = t3.net_addr AND t3.unit_tag != 'UN-99' AND "
        "t3.switch_room = 'none' WHERE t1.unit_tag = 'UN-18'"),
    "a plain lookup": "SELECT net_addr FROM \"bld_deps\" WHERE unit_tag = 'UN-18'",
}.items():
    out2 = run(sql2)
    check(f"{label2}: the query ran, and no line", "SQL query failed" not in out2
          and shared_lines(out2) == [], shared_lines(out2) or out2[-300:])

# ---------------------------------------------------------------------------
print("\n3. An error drops the line and nothing else")
# ---------------------------------------------------------------------------
real_exec = sql_tool._execute_with_timeout
calls = []


def _fail_lookup(con, sql, timeout=sql_tool.SQL_QUERY_TIMEOUT):
    calls.append(sql)
    if "bld_commish" in sql:
        raise RuntimeError("lookup failed")
    return real_exec(con, sql, timeout)


sql_tool._execute_with_timeout = _fail_lookup
try:
    out3 = run(SELF.format(tag="UN-18"))
finally:
    sql_tool._execute_with_timeout = real_exec
check("RED: the lookup was really attempted", any("bld_commish" in c for c in calls), calls)
check("an error drops the line; the row stands, with no failure text",
      shared_lines(out3) == [] and "| 10.0.0.50 | ZR-6 |  |" in out3
      and "SQL query failed" not in out3, out3[-300:])

# ---------------------------------------------------------------------------
print("\n4. One answer rule reads the line (answer prompts only)")
# ---------------------------------------------------------------------------
rule = next((ln for ln in openai_client.OUTPUT_FORMAT_RULES.splitlines()
             if "SHARED VALUE ELSEWHERE" in ln), "")
check("RED: OUTPUT_FORMAT_RULES has a SHARED VALUE ELSEWHERE bullet", bool(rule),
      openai_client.OUTPUT_FORMAT_RULES[-300:])
check("RED (R1): it never states that the value is shared, unconditionally",
      bool(rule) and "is shared" not in rule, rule)
check("RED (R1): a listed row may be another item, or the same item printed another way",
      "another item, or the same item printed another way" in rule, rule)
check("RED (R1): never say that no other row prints the value while the line lists one",
      "never say that no other row prints that value" in rule.lower(), rule)
check("RED: each listed row is named as its record prints it, with what its notes say",
      "as that record prints it" in rule and "notes" in rule, rule)
check("RED: the line, its heading and table or column names are never printed",
      "never print" in rule.lower(), rule)
check("the frozen tool-choice rules carry none of it (ruling W7)",
      "SHARED VALUE" not in openai_client.TOOL_CHOICE_FORMAT_RULES)

# ---------------------------------------------------------------------------
print("\n5. The SQL prompt's pre-takeover note is unchanged (its pairing rule is now shared)")
# ---------------------------------------------------------------------------
PROMPTS.clear()
run(SELF.format(tag="UN-18"), tables=(DEPS, COMMISH, PINGS, TWIN), cards=())
check("the writer's prompt still names the twin pair, word for word",
      bool(PROMPTS) and ('- "bld_existing_deps" is the PRE-TAKEOVER/historical counterpart of '
                         '"bld_deps". For a plain present-tense question that does NOT say ') in PROMPTS[0],
      (PROMPTS[0][-1500:] if PROMPTS else None))
PROMPTS.clear()
run(SELF.format(tag="UN-18"))
check("and carries no such note without a twin", bool(PROMPTS)
      and "PRE-TAKEOVER/historical counterpart" not in PROMPTS[0], None)

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
