"""test_sql_literal_elsewhere.py - a value looked for in a column that never holds it is caught
even when the result has rows (wave 3, F2, 2026-10-03).

THE DEFECT. One arm of a set operation compared a code with the PARENT column of a table where
that code only ever appears as a CHILD. The arm found nothing; the other arm's row filled the
result; the result was not empty, so the loop's EMPTY check never fired - and the answer called
the other arm's figure the one asked for. A second run hid the same miss on the unmatched side
of a LEFT JOIN, and a third shape put it in a scalar sub-SELECT that counted 0.

THE FIX, in two halves.
  * sql_tool: for each SELECT a set operation joins at the top of the query, every literal its
    WHERE clauses (sub-SELECTs included) compare with `=` or `IN` is placed in its table by
    sql_loop's own reader. A literal that none of the text columns it is compared with in a
    table T holds (any letter case or spacing), though ANOTHER text column of T does, gets a line
        LITERAL ELSEWHERE - '<lit>' never appears in T.<col>, though it does in T.<col2>
        (N entries)[; elsewhere in U.<c> (M entries)]. If the question meant the relation
        T.<col2> holds, query T.<col2>; if it meant the relation T.<col> holds, that relation
        is genuinely empty for '<lit>' and its empty answer stands
    (the other loaded tables' holding columns, minus those the query already compares it with).
    A literal held nowhere, or also compared with a holding column of T, says nothing. The
    loaded rows are read in Python first, so the common case costs no query.
  * sql_loop: `inspect_result` raises LITERAL_ELSEWHERE on that line whatever the rows (never on
    a failed query), ranked right after EMPTY, and the re-query quotes the sentence. With no
    step left nothing is added or removed: the line is already in the result.

FIX ROUND 1 (review of the wave). Ruling W3A1-R1: the empty relation may be HONEST - a "what did
it feed then, and now" question whose board fed nothing then fires the same way - so the line
and the re-query offer BOTH readings and steer to neither; the firing condition is unchanged.
And the fixed words of the line and the re-query score on no router card: "value" and "row(s)"
did, and each re-query is routed on its own text (section 3).

Every table, column and value here is invented. Every check marked RED fails against the code
as it stood before this change (sections 1-5 before F2; the fix-round checks before round 1).

Run:
    venv/Scripts/python -X utf8 tests/test_sql_literal_elsewhere.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import sql_loop, sql_tool  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


KIND = getattr(sql_loop, "LITERAL_ELSEWHERE", "LITERAL_ELSEWHERE")
LEAD = getattr(sql_loop, "LITERAL_ELSEWHERE_LEAD", "LITERAL ELSEWHERE - ")

OLD = {   # a superseded feeder schedule: each row is (parent board, child board, its load)
    "table_name": "bld_feeds_old",
    "columns": ["parent", "child", "kw", "rating_a"],
    "rows": [
        {"parent": "QZ-ROOT", "child": "QZ-MAIN", "kw": "305.5", "rating_a": "400"},
        {"parent": "QZ-MAIN", "child": "QZ-7F", "kw": "77.5", "rating_a": "125"},
        {"parent": "QZ-MAIN", "child": "QZ-8F", "kw": "31.0", "rating_a": "63"},
    ],
    "row_count": 3,
}
BOARDS = {   # the current boards, one row each
    "table_name": "bld_boards",
    "columns": ["board", "kw"],
    "rows": [{"board": "QZ-7F", "kw": "140.25"}, {"board": "QZ-MAIN", "kw": "402.0"}],
    "row_count": 2,
}
WAYS = {   # the ways of each board: another table that prints the board's code
    "table_name": "bld_ways",
    "columns": ["board_ref", "way", "watts"],
    "rows": [{"board_ref": "QZ-7F", "way": "1", "watts": "100"},
             {"board_ref": "QZ-7F", "way": "2", "watts": "200"}],
    "row_count": 2,
}
TABLES = [OLD, BOARDS, WAYS]
UNION_SQL = ("SELECT 'Before' AS state, kw FROM \"bld_feeds_old\" WHERE parent = 'QZ-7F' "
             "UNION ALL SELECT 'Now' AS state, kw FROM \"bld_boards\" WHERE board = 'QZ-7F'")
SENTENCE = ("'QZ-7F' never appears in bld_feeds_old.parent, though it does in bld_feeds_old.child "
            "(1 entry); elsewhere in bld_ways.board_ref (2 entries). If the question meant the "
            "relation bld_feeds_old.child holds, query bld_feeds_old.child; if it meant the "
            "relation bld_feeds_old.parent holds, that relation is genuinely empty for 'QZ-7F' "
            "and its empty answer stands")
# The round-0 wording, kept as the positive control for the router check in section 3.
OLD_INSTRUCTION = ("The previous query looked for a literal where it is never held, and missed "
                   "what it was looking for: 'QZ-7F' is never a value of bld_feeds_old.parent; "
                   "it is held by bld_feeds_old.child (1 row); elsewhere it is held by "
                   "bld_ways.board_ref (2 rows). Write the query again comparing each such "
                   "literal with where it is held, and keep everything else unchanged.")


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
    queue = []
    prompts = []

    def generate_content(self, *, model, contents, config=None):
        _Models.prompts.append(contents)
        text = _Models.queue.pop(0) if _Models.queue else "SELECT NULL WHERE FALSE"
        return type("_R", (), {"text": text, "usage_metadata": None})()


class _Client:
    def __init__(self, *a, **kw):
        self.models = _Models()


QUERIES = []


class _Patched:
    """execute_sql_query's model, keys and cards faked; every DuckDB query recorded."""

    def __enter__(self):
        self.saved = (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
                      sql_tool._load_table_cards, sql_tool._execute_with_timeout)
        real_exec = sql_tool._execute_with_timeout

        def _recording(con, query, timeout=sql_tool.SQL_QUERY_TIMEOUT):
            QUERIES.append(query)
            return real_exec(con, query, timeout)

        sql_tool.genai.Client = _Client
        sql_tool.get_llm_api_key = lambda: "fake-key"
        sql_tool.get_llm_model = lambda: "fake-model"
        sql_tool._load_table_cards = lambda *a, **k: []
        sql_tool._execute_with_timeout = _recording
        return self

    def __exit__(self, *exc):
        (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
         sql_tool._load_table_cards, sql_tool._execute_with_timeout) = self.saved


def run(*sqls, tables=TABLES):
    """`execute_sql_query` end to end, the fake writer returning `sqls` in turn."""
    _Models.queue = list(sqls)
    QUERIES.clear()
    with _Patched():
        return sql_tool.execute_sql_query("q", "u-1", _Supabase(tables))


def lines(text):
    return [ln for ln in text.splitlines() if ln.startswith(LEAD)]


# ---------------------------------------------------------------------------
print("1. sql_tool: the value compared with a column that never holds it, in a result with rows")
# ---------------------------------------------------------------------------
o1 = run(UNION_SQL)
check("the other arm's row is the whole result - it is NOT empty", "| Now | 140.25 |" in o1
      and "Before" not in o1.split("SQL: `")[0], o1[:300])
check("RED: one LITERAL ELSEWHERE line names where the value really is, and the other loaded "
      "table holding it", lines(o1) == [LEAD + SENTENCE], lines(o1) or o1[-500:])
check("RED: it comes after the SQL line, never inside the rendered table",
      LEAD in o1 and o1.index("SQL: `") < o1.index(LEAD)
      and not any(ln.startswith("|") and LEAD in ln for ln in o1.splitlines()), o1)
o1b = run("SELECT b.kw AS now_kw, f.kw AS before_kw FROM \"bld_boards\" b LEFT JOIN "
          "\"bld_feeds_old\" f ON f.parent = b.board WHERE b.board = 'QZ-7F' OR f.parent = 'QZ-7F'")
check("RED: the unmatched side of a LEFT JOIN, filtered under an OR, is caught too",
      lines(o1b) == [LEAD + SENTENCE], lines(o1b) or o1b[-400:])
o1c = run("SELECT b.kw, (SELECT COUNT(*) FROM \"bld_feeds_old\" WHERE parent = 'QZ-7F') AS n "
          "FROM \"bld_boards\" b WHERE b.board = 'QZ-7F'")
check("RED: and a scalar sub-SELECT that counted 0", lines(o1c) == [LEAD + SENTENCE],
      lines(o1c) or o1c[-400:])
# Fix round 1, ruling W3A1-R1: "what did QZ-7F feed then, and what now?" - a question about the
# relation the PARENT column records - is answered by exactly this UNION, and its empty arm is
# then the honest answer (the board fed nothing then). The same line fires, so it must offer
# both readings and steer to neither.
line1 = (lines(o1) or [""])[0]
check("RED: the line says where the literal never appears and where it does, in words no "
      "router card scores ('never appears in', counted in entries)",
      "never appears in bld_feeds_old.parent" in line1 and "(1 entry)" in line1
      and "never a value" not in line1 and " row" not in line1, line1)
check("RED: reading one - the question meant the relation the holding column records: query it",
      "If the question meant the relation bld_feeds_old.child holds, query bld_feeds_old.child"
      in line1, line1)
check("RED: reading two - the question meant the relation the empty column records: that "
      "relation is genuinely empty and the empty answer stands",
      "if it meant the relation bld_feeds_old.parent holds, that relation is genuinely empty "
      "for 'QZ-7F' and its empty answer stands" in line1, line1)

# ---------------------------------------------------------------------------
print("\n2. sql_tool: silent shapes")
# ---------------------------------------------------------------------------
for label2, sql2 in [
    ("the same branch also compares the value with the column that holds it",
     "SELECT 'Before' AS state, kw FROM \"bld_feeds_old\" WHERE parent = 'QZ-7F' OR child = "
     "'QZ-7F' UNION ALL SELECT 'Now' AS state, kw FROM \"bld_boards\" WHERE board = 'QZ-7F'"),
    ("another branch already compares it with the column that holds it",
     "SELECT kw FROM \"bld_feeds_old\" WHERE parent = 'QZ-7F' UNION ALL "
     "SELECT kw FROM \"bld_feeds_old\" WHERE child = 'QZ-7F'"),
    ("a literal no column of the table holds",
     "SELECT 'Before' AS state, kw FROM \"bld_feeds_old\" WHERE parent = 'QZ-NONE' "
     "UNION ALL SELECT 'Now' AS state, kw FROM \"bld_boards\" WHERE board = 'QZ-7F'"),
    ("the column compared holds it",
     "SELECT kw FROM \"bld_feeds_old\" WHERE child = 'QZ-7F'"),
    ("a pattern (LIKE), not an equality",
     "SELECT 'Before' AS state, kw FROM \"bld_feeds_old\" WHERE parent LIKE 'QZ-7F' "
     "UNION ALL SELECT 'Now' AS state, kw FROM \"bld_boards\" WHERE board = 'QZ-7F'"),
    ("a numeric column compared with a quoted number",
     "SELECT child FROM \"bld_feeds_old\" WHERE rating_a = '125' AND parent = 'QZ-MAIN'"),
]:
    out2 = run(sql2)
    check(f"{label2}: the query ran", "SQL query failed" not in out2, out2[:300])
    check(f"{label2}: no line", lines(out2) == [], lines(out2))
o2 = run("SELECT kw FROM \"bld_feeds_old\" WHERE child = 'QZ-7F'")
check("a value its compared column holds costs no extra query at all (the loaded rows are read "
      "first)", QUERIES == ["SELECT kw FROM \"bld_feeds_old\" WHERE child = 'QZ-7F'"], QUERIES)
o2b = run("SELECT kw FROM \"bld_feeds_old\" WHERE parnt = 'QZ-7F'",
          "SELECT kw FROM \"bld_feeds_old\" WHERE parnt = 'QZ-7F'")
check("a FAILED query carries no line", o2b.startswith("SQL query failed") and LEAD not in o2b,
      o2b[:300])

real_exec = sql_tool._execute_with_timeout
calls3 = []


def _fail_after_first(con, query, timeout=sql_tool.SQL_QUERY_TIMEOUT):
    calls3.append(query)
    if len(calls3) > 1:
        raise RuntimeError("probe failed")
    return real_exec(con, query, timeout)


sql_tool._execute_with_timeout = _fail_after_first
try:
    o3 = run(UNION_SQL)
finally:
    sql_tool._execute_with_timeout = real_exec
check("RED: the line's own count really was attempted", len(calls3) >= 2, calls3)
check("an error drops the line and nothing else",
      lines(o3) == [] and "| Now | 140.25 |" in o3 and "SQL query failed" not in o3, o3[-300:])

# ---------------------------------------------------------------------------
print("\n3. sql_loop: the line is an issue, even on a result with rows")
# ---------------------------------------------------------------------------
CARD = {"table": "bld_feeds_old", "columns": ["parent", "child", "kw", "rating_a"],
        "identifier_column": "child"}
NOW = ("| state | kw |\n| --- | --- |\n| Now | 140.25 |\n\nSQL: `" + UNION_SQL + "`\n\n"
       + LEAD + SENTENCE)
FIXED_SQL = UNION_SQL.replace("WHERE parent = 'QZ-7F'", "WHERE child = 'QZ-7F'")
GOOD = ("| state | kw |\n| --- | --- |\n| Before | 77.5 |\n| Now | 140.25 |\n\nSQL: `" + FIXED_SQL
        + "`")
iss = sql_loop.inspect_result(NOW, "what was the board's load before, and now?", [CARD])
hit = next((i for i in iss if i.kind == KIND), None)
check("RED: inspect_result raises LITERAL_ELSEWHERE on a non-empty result carrying the line",
      hit is not None, [i.kind for i in iss])
check("RED: its instruction quotes the sentence, word for word",
      hit is not None and SENTENCE in hit.instruction, hit.instruction if hit else "")
check("RED: and it can be acted on", sql_loop.first_requery_issue(iss) is not None
      and sql_loop.first_requery_issue(iss).kind == KIND, iss)
check("RED: LITERAL_ELSEWHERE is ranked right after EMPTY",
      KIND in sql_loop.ISSUE_ORDER
      and sql_loop.ISSUE_ORDER.index(KIND) == sql_loop.ISSUE_ORDER.index(sql_loop.EMPTY) + 1,
      sql_loop.ISSUE_ORDER)
own_words = hit.instruction.replace(SENTENCE, "") if hit else "-"
check("RED: the instruction's own words carry no hyphen (each re-query is routed on its text)",
      hit is not None and "-" not in own_words, own_words)
# Fix round 1, ruling W3A1-R1: the re-query offers both readings and steers to neither.
inst = hit.instruction if hit else ""
check("RED: the re-query asks which relation the question means before writing again",
      "Settle which relation the question means before writing the query again" in inst, inst)
check("RED: ... compare the literal where that relation is held, OR keep the empty answer",
      "compare the literal where that relation is held, or keep the empty answer where the "
      "question meant the empty relation" in inst, inst)
check("RED: and no longer steers every case to the holding column",
      bool(inst) and "comparing each such literal with where it is held" not in inst, inst)

# Fix round 1: route the instruction text WITH its sentence. Measured on the real router cards,
# each of these plain English words is a column or vocabulary word of at least one card, so in a
# re-query's FIXED wording it pulls tables into the route ("so" alone pulled one in). The cards
# below carry exactly those words; the data's own words (the quoted literal, the table.column
# names, the counts) are taken out first, because routing on them is the point.
from app.services import table_router  # noqa: E402
SCORING = ("value", "column", "row", "table", "other", "result", "so", "as", "field", "part",
           "record", "one", "both", "cell", "none", "occurrence", "time")
ROUTER_CARDS = [{"table": f"bld_{w}", "columns": [w]} for w in SCORING]


def fixed_wording(text):
    """`text` without the data's own words: quoted literals, table.column names, numbers."""
    text = re.sub(r"'[^']*'", " ", text)
    text = re.sub(r"\b[A-Za-z0-9_]+\.[A-Za-z0-9_]+\b", " ", text)
    return re.sub(r"\b\d+\b", " ", text)


def router_scores(text):
    words = {id(c): (table_router._card_subject_words(c), table_router._card_vocab_words(c))
             for c in ROUTER_CARDS}
    df = table_router._document_frequency(ROUTER_CARDS, words)
    qw, qp = table_router._question_words(text), table_router._question_prefixes(text)
    return {c["table"]: table_router._score(qw, qp, c, df, words) for c in ROUTER_CARDS}


scored_old = {t: s for t, s in router_scores(fixed_wording(OLD_INSTRUCTION)).items() if s}
# ('row' in "(1 row)" does not score: the router's tokeniser keeps the ')' on the token. The new
# wording counts in "entries" anyway, so it does not lean on that.)
check("the check is not vacuous: the round-0 wording scores on these cards ('value')",
      "bld_value" in scored_old, scored_old)
# The re-query exactly as the loop would send it after section 1's real executor result: the
# instruction around the sentence sql_tool itself wrote - so both halves' wording is routed.
real_inst = (sql_loop._literal_elsewhere_issue(sql_loop.literal_elsewhere_sentences(o1))
             .instruction if lines(o1) else "")
scored_new = {t: s for t, s in router_scores(fixed_wording(real_inst)).items() if s}
check("RED: the instruction WITH the sentence sql_tool really writes: no card scores on a "
      "single fixed word", bool(real_inst) and not scored_new, scored_new)
check("and the full re-query names no place and asks for no change (the router's place tier and "
      "change guard stay silent, though the sentence quotes a coded tag)",
      bool(real_inst) and not table_router._names_a_place(real_inst)
      and not table_router._asks_about_a_change(real_inst), real_inst[:200])
check("a result with no such line raises no such issue",
      not any(i.kind == KIND for i in sql_loop.inspect_result(GOOD, "q", [CARD])))
check("a FAILED result raises no such issue, whatever its text",
      not any(i.kind == KIND for i in sql_loop.inspect_result(
          "SQL query failed: Binder Error\n" + LEAD + SENTENCE, "q", [CARD])))
EMPTY_WITH = "Query returned no results.\n\nSQL: `x`\n\n" + LEAD + SENTENCE
iss_e = sql_loop.inspect_result(EMPTY_WITH, "q", [CARD])
check("on an EMPTY result, EMPTY still comes first",
      bool(iss_e) and iss_e[0].kind == sql_loop.EMPTY, [i.kind for i in iss_e])


class FakeExec:
    def __init__(self, scripted):
        self.scripted, self.questions = list(scripted), []

    def __call__(self, question, user_id, sb):
        self.questions.append(question)
        return self.scripted.pop(0)


ex = FakeExec([NOW, GOOD])
inv = sql_loop.run_sql_investigation("what was the board's load before, and now?", "u", None,
                                     execute=ex, routed_cards=[CARD], max_steps=3)
check("RED: [line, good] - the loop re-queries once and keeps the good result",
      len(ex.questions) == 2 and inv.result_text.startswith(GOOD), ex.questions)
check("RED: the re-query carries the sentence", len(ex.questions) == 2
      and SENTENCE in ex.questions[1], ex.questions[-1][-400:])
check("RED: the step says which issue drove it",
      [s["issue"] for s in inv.steps] == [None, KIND], [s["issue"] for s in inv.steps])
ex2 = FakeExec([NOW, NOW])
inv2 = sql_loop.run_sql_investigation("q", "u", None, execute=ex2, routed_cards=[CARD],
                                      max_steps=3)
check("RED: the same issue twice running stops the loop (the repeat guard)", len(ex2.questions) == 2,
      ex2.questions)
check("with no step left, the sentence stays in the result the answer writer reads",
      (LEAD + SENTENCE) in inv2.result_text, inv2.result_text[-400:])
ex3 = FakeExec([NOW])
inv3 = sql_loop.run_sql_investigation("q", "u", None, execute=ex3, routed_cards=[CARD],
                                      max_steps=1)
check("max_steps=1: one call, the text exactly as executed - nothing added or taken away",
      len(ex3.questions) == 1 and inv3.result_text == NOW, inv3.result_text[-300:])

# ---------------------------------------------------------------------------
print("\n4. sql_loop: the WHERE reader keeps an operator filter, off by default")
# ---------------------------------------------------------------------------
q4 = ("SELECT * FROM \"bld_feeds_old\" WHERE parent = 'A-1' AND child LIKE 'B%' AND kw IN "
      "('1', '2') AND rating_a ILIKE 'x'")
cards4 = [{"table": "bld_feeds_old", "columns": ["parent", "child", "kw", "rating_a"]}]
try:
    with_ops = sql_loop._filtered_columns(q4, cards4, ops=("=", "IN"))
except TypeError as e:
    with_ops = f"TypeError: {e}"
check("RED: with ops ('=', 'IN'), only the equality and the IN list are read",
      with_ops == [("bld_feeds_old", "parent", ["A-1"]), ("bld_feeds_old", "kw", ["1", "2"])],
      with_ops)
check("without ops, all four filters are read exactly as before",
      [c for _, c, _ in sql_loop._filtered_columns(q4, cards4)]
      == ["parent", "child", "kw", "rating_a"], sql_loop._filtered_columns(q4, cards4))

# ---------------------------------------------------------------------------
print("\n5. End to end: the real executor inside the loop fixes the arm")
# ---------------------------------------------------------------------------
_Models.queue = [UNION_SQL, FIXED_SQL]
_Models.prompts = []
with _Patched():
    inv5 = sql_loop.run_sql_investigation(
        "what was the board's load before, and now?", "u-1", _Supabase(TABLES),
        execute=sql_tool.execute_sql_query, routed_cards=[CARD], max_steps=3)
check("RED: two queries were written", len(_Models.prompts) == 2, len(_Models.prompts))
check("RED: the second writer prompt carries the sentence",
      len(_Models.prompts) == 2 and SENTENCE in _Models.prompts[1], len(_Models.prompts))
check("RED: the final result holds both rows, and no line any more",
      "| Before | 77.5 |" in inv5.result_text and "| Now | 140.25 |" in inv5.result_text
      and LEAD not in inv5.result_text, inv5.result_text[:400])

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
