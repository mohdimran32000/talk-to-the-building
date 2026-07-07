"""
Eval harness for the text-to-SQL tool: FCU breakdown quality (4th floor, Block B).

Runs several phrasings of the same underlying question directly through
execute_sql_query and scores each result against the known-good answer
derived from the source data:
  - 16 FCU circuits across DB-04(B)-SP-01 (6) and DB-04(B)-SP-02 (10)
  - total 29 FCU points (11 + 18)
  - breakdown must include the room/area column and per-circuit counts

Usage: cd backend && venv/Scripts/python scripts/eval_sql_breakdown.py
Exit code 0 = all checks pass, 1 = failures (prints per-question detail).
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from supabase import create_client

from app.services.sql_tool import execute_sql_query

EXPECTED_TOTAL = 29
EXPECTED_CIRCUITS = 16
AREA_MARKERS = ["Open Collaboration Space", "Studio 1"]


def _get_owner(sb):
    res = sb.table("structured_data").select("user_id").eq("table_name", "db_circuits").limit(1).execute()
    if not res.data:
        raise SystemExit("db_circuits not found in structured_data")
    return res.data[0]["user_id"]


def _parse_md_table(text: str):
    """Return (headers, rows) of the first markdown table in text."""
    lines = [l for l in text.splitlines() if l.strip().startswith("|")]
    if len(lines) < 2:
        return [], []
    headers = [c.strip().lower() for c in lines[0].strip("|").split("|")]
    rows = []
    for l in lines[2:]:
        rows.append([c.strip() for c in l.strip("|").split("|")])
    return headers, rows


def check_total(result: str) -> list:
    """The count question: 29 must be derivable directly."""
    failures = []
    if not re.search(r"\b29(\.0)?\b", result):
        failures.append("total 29 not present in result")
    return failures


def check_breakdown(result: str) -> list:
    """Breakdown questions: per-circuit rows with area + count columns."""
    failures = []
    headers, rows = _parse_md_table(result)

    data_rows = [r for r in rows if any("DB-04(B)-SP" in c for c in r)]
    if len(data_rows) != EXPECTED_CIRCUITS:
        failures.append(f"expected {EXPECTED_CIRCUITS} circuit rows, got {len(data_rows)}")

    for marker in AREA_MARKERS:
        if marker.lower() not in result.lower():
            failures.append(f"area/location missing (no '{marker}')")

    # A count column must exist and sum to the expected total
    count_col = next((i for i, h in enumerate(headers)
                      if any(k in h for k in ("points", "qty", "count", "units", "fcu"))), None)
    if count_col is None:
        failures.append(f"no quantity column in headers {headers}")
    else:
        total = 0.0
        for r in data_rows:
            try:
                total += float(r[count_col])
            except (ValueError, IndexError):
                pass
        if int(total) != EXPECTED_TOTAL:
            failures.append(f"quantity column sums to {total}, expected {EXPECTED_TOTAL}")
    return failures


CASES = [
    ("how many FCU's connected to 4th floor in Block B?", check_total),
    ("how many FCU's connected to 4th floor in Block B? - Provide me the breakdown in an Excel sheet format", check_breakdown),
    ("can you give a breakdown of the FCUs on the 4th floor of Block B?", check_breakdown),
    ("list down all FCU circuits on the 4th floor of Block B", check_breakdown),
]


def main():
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    user_id = _get_owner(sb)

    passed = 0
    for question, checker in CASES:
        result = execute_sql_query(question, user_id, sb)
        failures = checker(result)
        status = "PASS" if not failures else "FAIL"
        print(f"\n[{status}] {question}")
        if failures:
            for f in failures:
                print(f"  - {f}")
            sql_line = next((l for l in result.splitlines() if l.startswith("SQL:")), "")
            print(f"  {sql_line}")
            print("  --- result head ---")
            print("  " + "\n  ".join(result.splitlines()[:8]))
        else:
            passed += 1

    print(f"\n{passed}/{len(CASES)} cases passed")
    sys.exit(0 if passed == len(CASES) else 1)


if __name__ == "__main__":
    main()
