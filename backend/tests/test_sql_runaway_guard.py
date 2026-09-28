"""test_sql_runaway_guard.py - the runaway-SQL guard (owner's question, 2026-09-28).

THE MEASURED DEFECT. "what is room 1.29? and what all assets there inside?" is two
questions about one entity. The SQL writer glued them together with a set operation
across tables of different widths:

    SELECT * FROM "hwu_locations" WHERE room_number = '1.29'
    UNION ALL SELECT NULL, NULL, NULL, NULL, NULL, ...

and then, having to pad the second arm out to the first arm's width without knowing
that width, emitted `NULL,` until the output cap stopped it: 8,188 output tokens of
almost nothing. DuckDB rejected it ("Set operations can only apply to expressions with
the same number of result columns"), the one LLM repair produced the same shape, and
the question fell through to document search, which answered "1 Daylight Sensor" from a
stray register chunk. The true answer is 14 units across 8 items.

Three things were wrong and this file pins the first of them: a query that is visibly
degenerate BEFORE it runs should never be executed, never be handed to the repair call
(a repair prompt carrying 8 KB of NULLs is a second runaway waiting to happen), and
never have had 8,192 output tokens to run away INTO. A one-line SELECT does not need
them; the earlier comment's worry - that a thinking model spends the same budget and
2,048 truncated the SQL mid-string - is answered by the guard, which now catches a
truncated-or-degenerate query by shape instead of by hoping a bigger budget avoids one.

The guard is a pure function so it is testable with no DuckDB, no model and no network.

Run:
    venv/Scripts/python -X utf8 tests/test_sql_runaway_guard.py
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import sql_tool  # noqa: E402
from app.services import sql_loop  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


NORMAL_SQL = ('SELECT room_number, room_name, area_m2, department FROM "bld_places" '
              "WHERE room_number ILIKE '%1.29%' ORDER BY NULLIF(room_number, '') NULLS LAST")

# A long query with no NULL run at all, so the LENGTH arm is what refuses it.
LONG_SQL = 'SELECT ' + ', '.join(f"col_{i}" for i in range(200)) + ' FROM "bld_places"'

# The measured shape, shortened: one real arm, one arm padded with NULLs.
UNION_NULL_SQL = ('SELECT * FROM "bld_places" WHERE room_number = \'1.29\' '
                  'UNION ALL SELECT ' + ', '.join(['NULL'] * 25))


print("1. _sql_looks_runaway is a pure function returning a reason, or None")
check("the guard exists", hasattr(sql_tool, "_sql_looks_runaway"))
guard = getattr(sql_tool, "_sql_looks_runaway", lambda s: None)

check("a normal ~200-char query passes the guard", guard(NORMAL_SQL) is None, guard(NORMAL_SQL))
check("the normal query really is around 200 chars, not accidentally long",
      120 < len(NORMAL_SQL) < 300, len(NORMAL_SQL))
check("an empty string passes (nothing to refuse yet)", guard("") is None, guard(""))


print("\n2. Too long is refused, with a reason that says so")
check("the fixture really is over 1,500 chars", len(LONG_SQL) > 1500, len(LONG_SQL))
reason_long = guard(LONG_SQL)
check("a 1,600-char query is refused", bool(reason_long), reason_long)
check("the reason names the length, not NULLs",
      bool(reason_long) and "char" in reason_long.lower() and "null" not in reason_long.lower(),
      reason_long)
check("the limit is a named constant", hasattr(sql_tool, "MAX_GENERATED_SQL_CHARS")
      and sql_tool.MAX_GENERATED_SQL_CHARS == 1500,
      getattr(sql_tool, "MAX_GENERATED_SQL_CHARS", None))
check("a query one char under the limit passes",
      guard("SELECT a FROM t WHERE b = '" + "x" * 1450 + "'") is None)


print("\n3. A run of repeated NULLs is refused - the measured shape")
reason_null = guard(UNION_NULL_SQL)
check("the UNION ALL SELECT NULL, NULL, ... (x25) query is refused", bool(reason_null),
      reason_null)
check("and it is refused for the NULL run, not for its length",
      len(UNION_NULL_SQL) < getattr(sql_tool, "MAX_GENERATED_SQL_CHARS", 1500)
      and bool(reason_null) and "null" in reason_null.lower(),
      (len(UNION_NULL_SQL), reason_null))
check("the threshold is a named constant", hasattr(sql_tool, "MAX_REPEATED_NULLS")
      and sql_tool.MAX_REPEATED_NULLS == 20,
      getattr(sql_tool, "MAX_REPEATED_NULLS", None))
check("exactly 20 NULLs is a run", bool(guard("SELECT " + ", ".join(["NULL"] * 20))),
      guard("SELECT " + ", ".join(["NULL"] * 20)))
check("19 NULLs is not - the boundary is not off by one",
      guard("SELECT " + ", ".join(["NULL"] * 19)) is None,
      guard("SELECT " + ", ".join(["NULL"] * 19)))
check("the match is case-insensitive", bool(guard("SELECT " + ", ".join(["null"] * 25))))
check("whitespace between the items does not hide the run",
      bool(guard("SELECT " + " ,\n ".join(["NULL"] * 25))))
check("a handful of legitimate NULLs is untouched",
      guard('SELECT NULL, NULL, a, b FROM "t" WHERE c IS NOT NULL') is None)


print("\n4. The refusal is worded so the loop's FAILED path handles it")
_wording = getattr(sql_tool, "_runaway_failure_text", None)
check("the shared wording helper exists", _wording is not None)
text = _wording(UNION_NULL_SQL, reason_null) if _wording else ""
check("it starts with the same prefix a real execution failure uses",
      text.startswith("SQL query failed:"), text[:80])
check("it says the SQL was malformed, and why",
      "generated SQL was malformed" in text and bool(reason_null) and reason_null in text,
      text[:200])
check("it shows the query, capped at 300 characters",
      "Generated SQL:" in text and 0 < len(text) < 600, len(text))
check("a query over 300 chars is elided",
      bool(_wording) and "…" in _wording(LONG_SQL, reason_long))
check("sql_loop reads it as a failure", sql_loop.result_is_failure(text))
check("sql_loop can still parse the query out of it",
      sql_loop.result_sql(text).startswith("SELECT * FROM"), sql_loop.result_sql(text))


print("\n5. The guard is wired in before execution AND before the repair")
SRC = inspect.getsource(sql_tool.execute_sql_query)
check("execute_sql_query calls the guard", "_sql_looks_runaway(" in SRC)
_at = SRC.index("_sql_looks_runaway(") if "_sql_looks_runaway(" in SRC else -1
check("it is called after the table-name fixer",
      _at > 0 and _at > SRC.index("_fix_table_names"), _at)
check("and BEFORE DuckDB is even connected, so nothing executes and the "
      "repair call is never reached",
      _at > 0 and _at < SRC.index("duckdb.connect("),
      (_at, SRC.index("duckdb.connect(")))
check("the refusal returns the shared wording helper",
      "_runaway_failure_text(" in SRC)
check("the guard logs what it refused",
      _at > 0 and "logger." in SRC.split("_sql_looks_runaway(")[1][:400],
      SRC.split("_sql_looks_runaway(")[1][:400] if _at > 0 else "")


print("\n6. The output budget no longer leaves room for 8,188 tokens of NULLs")
check("no 8192 budget survives in the module", "8192" not in inspect.getsource(sql_tool),
      [l.strip() for l in inspect.getsource(sql_tool).splitlines() if "8192" in l])
whole = inspect.getsource(sql_tool)
check("both the generation call and the repair call cap at 2048",
      whole.count("max_output_tokens=2048") == 2,
      whole.count("max_output_tokens=2048"))
check("the original thinking-token comment is kept, not deleted",
      "thought" in whole.lower() or "Thinking models" in whole)
check("and the measured case is recorded beside it",
      "8,188" in whole or "8188" in whole,
      [l.strip() for l in whole.splitlines() if "8,188" in l or "8188" in l])


print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
