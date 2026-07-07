"""
Goal-driven eval: RAG SQL-tool answers vs independently computed ground truth.

For each question, ground truth is computed by HAND-WRITTEN SQL (authored and
verified separately from the LLM pipeline) over the same structured_data
tables, loaded into DuckDB exactly the way sql_tool loads them. The question
is then run through the real execute_sql_query pipeline and the two are
compared:
  - kind="number": the truth value must appear among the numeric cells of the
    RAG result's markdown table (tolerance 0.01)
  - kind="text":   every expected string must appear in the RAG result

This is the automated alternative to manually testing questions in the UI.
Add new cases as bad answers are discovered — each becomes a regression test.

Usage: cd backend && venv/Scripts/python scripts/eval_rag_vs_truth.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

import duckdb
from supabase import create_client

from app.services.sql_tool import execute_sql_query, _infer_column_types

CASES = [
    {
        "q": "how many FCU's connected to 4th floor in Block B?",
        "kind": "number",
        "truth_sql": """SELECT SUM(TRY_CAST(points AS DOUBLE)) FROM db_circuits
                        WHERE db IN ('DB-04(B)-SP-01','DB-04(B)-SP-02') AND load_type ILIKE '%FCU%'""",
    },
    {
        "q": "what is the total connected load of the 4th floor in Block B?",
        "kind": "number",
        # Hierarchy: SMDB-B-4F feeds the 4F DBs — only the topmost row counts
        "truth_sql": """SELECT SUM(TRY_CAST(x.tcl_kw AS DOUBLE)) FROM panels x
                        WHERE x.block='B' AND x.floor='4F'
                        AND NOT EXISTS (SELECT 1 FROM panels p WHERE p.panel = x.fed_from
                                        AND p.block='B' AND p.floor='4F')""",
    },
    {
        "q": "what is the total connected load of Block B?",
        "kind": "number",
        "truth_sql": """SELECT SUM(TRY_CAST(x.tcl_kw AS DOUBLE)) FROM panels x
                        WHERE x.block='B'
                        AND NOT EXISTS (SELECT 1 FROM panels p WHERE p.panel = x.fed_from
                                        AND p.block='B')""",
    },
    {
        "q": "what is the incomer rating of DB-04(B)-SP-02?",
        "kind": "number",
        "truth_sql": """SELECT TRY_CAST(incomer_rating_a AS DOUBLE) FROM panels
                        WHERE panel = 'DB-04(B)-SP-02'""",
    },
    {
        "q": "which panel feeds DB-04(B)-SP-01?",
        "kind": "text",
        "expect": ["SMDB-B-4F"],
    },
    {
        "q": "list the panels on the 4th floor of Block B",
        "kind": "text",
        "expect": ["SMDB-B-4F", "DB-04(B)-LP-02", "DB-04(B)-SP-01", "DB-04(B)-SP-02"],
    },
    {
        "q": "how many cleaner socket points are there on the 4th floor of Block B?",
        "kind": "number",
        "truth_sql": """SELECT SUM(TRY_CAST(points AS DOUBLE)) FROM db_circuits
                        WHERE db IN ('DB-04(B)-SP-01','DB-04(B)-SP-02') AND remarks ILIKE '%cleaner%'""",
    },
    {
        "q": "what is the total FCU load in watts on the 4th floor of Block B?",
        "kind": "number",
        "truth_sql": """SELECT SUM(TRY_CAST(load_w AS DOUBLE)) FROM db_circuits
                        WHERE db IN ('DB-04(B)-SP-01','DB-04(B)-SP-02') AND load_type ILIKE '%FCU%'""",
    },
    {
        "q": "what is the breaker rating of the feeder from SMDB-B-4F to DB-04(B)-SP-02?",
        "kind": "number",
        "truth_sql": """SELECT TRY_CAST(breaker_a AS DOUBLE) FROM smdb_feeders
                        WHERE smdb='SMDB-B-4F' AND feeder='DB-04(B)-SP-02'""",
    },
    {
        "q": "what is the connected load of the feeder DB-04(B)-SP-01 on SMDB-B-4F?",
        "kind": "number",
        "truth_sql": """SELECT TRY_CAST(tcl_kw AS DOUBLE) FROM smdb_feeders
                        WHERE smdb='SMDB-B-4F' AND feeder='DB-04(B)-SP-01'""",
    },
    {
        "q": "which SMDB in Block B has the highest total connected load?",
        "kind": "text",
        "truth_sql": """SELECT panel FROM panels WHERE kind='SMDB' AND block='B'
                        ORDER BY TRY_CAST(tcl_kw AS DOUBLE) DESC NULLS LAST LIMIT 1""",
    },
    {
        "q": "what is the total demand load (TDL) for Small Power on MDB-C-G2?",
        "kind": "number",
        "truth_sql": """SELECT TRY_CAST(tdl_kw AS DOUBLE) FROM mdb_calc
                        WHERE mdb='MDB-C-G2' AND load_type ILIKE '%small power%'""",
    },
    {
        "q": "what diversity factor is applied to the Water Heater load on MDB-C-G2?",
        "kind": "number",
        "truth_sql": """SELECT TRY_CAST(diversity AS DOUBLE) FROM mdb_calc
                        WHERE mdb='MDB-C-G2' AND load_type ILIKE '%water heater%'""",
    },
    {
        "q": "how many distribution boards (kind DB) are there in Block C?",
        "kind": "number",
        "truth_sql": """SELECT COUNT(*) FROM panels WHERE kind='DB' AND block='C'""",
    },
    {
        "q": "what is the fault level of the MDB-C-G1 feeder from MDB-C?",
        "kind": "number",
        "truth_sql": """SELECT TRY_CAST(fault_ka AS DOUBLE) FROM smdb_feeders
                        WHERE smdb='MDB-C' AND feeder='MDB-C-G1'""",
    },
    {
        "q": "how many lighting points are there in DB-04(B)-LP-02?",
        "kind": "number",
        "truth_sql": """SELECT SUM(TRY_CAST(points AS DOUBLE)) FROM db_circuits
                        WHERE db='DB-04(B)-LP-02' AND load_type ILIKE '%LTG%'""",
    },
    {
        "q": "what is the total load in watts of all circuits in DB-04(B)-LP-02?",
        "kind": "number",
        "truth_sql": """SELECT SUM(TRY_CAST(load_w AS DOUBLE)) FROM db_circuits
                        WHERE db='DB-04(B)-LP-02'""",
    },
    {
        "q": "which panel feeds SMDB-B-4F?",
        "kind": "text",
        "expect": ["MDB-C-G2"],
    },
    {
        "q": "what is the demand factor of MDB-C?",
        "kind": "number",
        "truth_sql": """SELECT TRY_CAST(demand_factor AS DOUBLE) FROM panels WHERE panel='MDB-C'""",
    },
    {
        "q": "what is the total connected load of MDB-C?",
        "kind": "number",
        "truth_sql": """SELECT TRY_CAST(tcl_kw AS DOUBLE) FROM panels WHERE panel='MDB-C'""",
    },
    {
        "q": "what is the maximum demand load (MDL) of MDB-C?",
        "kind": "number",
        "truth_sql": """SELECT TRY_CAST(mdl_kw AS DOUBLE) FROM panels WHERE panel='MDB-C'""",
    },
    {
        "q": "how many cooker circuits are there in total?",
        "kind": "number",
        "truth_sql": """SELECT COUNT(*) FROM db_circuits WHERE load_type ILIKE '%cooker%'""",
    },
    {
        # Data-gap regression: SMDB-B-6F was missing from panels (present only on
        # the MDB-C-G2 feeder schedule) — added 2026-07-09 from smdb_feeders
        "q": "what is the total connected load of SMDB-B-6F?",
        "kind": "number",
        "truth_sql": """SELECT TRY_CAST(tcl_kw AS DOUBLE) FROM panels WHERE panel='SMDB-B-6F'""",
    },
    {
        # DEWA-corrected value: printed MDL 1120.40 was struck and replaced with
        # 1156.36 — the ingested mdl_kw holds the current (corrected) value
        "q": "what is the maximum demand of MDB-C-G2?",
        "kind": "number",
        "truth_sql": """SELECT TRY_CAST(mdl_kw AS DOUBLE) FROM panels WHERE panel='MDB-C-G2'""",
    },
]


def load_truth_db(sb):
    """Load structured_data into DuckDB the same way sql_tool does."""
    con = duckdb.connect(":memory:")
    res = sb.table("structured_data").select("user_id, table_name, columns, rows").execute()
    user_id = res.data[0]["user_id"]
    for t in res.data:
        cols, rows = t["columns"], t["rows"]
        if not rows:
            continue
        col_types = _infer_column_types(cols, rows)
        con.execute(f'CREATE TABLE "{t["table_name"]}" ({", ".join(f_q(c) + " VARCHAR" for c in cols)})')
        ph = ", ".join(["?"] * len(cols))
        for row in rows:
            con.execute(f'INSERT INTO "{t["table_name"]}" VALUES ({ph})',
                        [None if row.get(c) is None else str(row.get(c)) for c in cols])
    return con, user_id


def f_q(c):
    return f'"{c}"'


def numeric_cells(result: str) -> list:
    """Numbers appearing as whole cells in the markdown table of a RAG result."""
    nums = []
    for line in result.splitlines():
        if not line.strip().startswith("|"):
            continue
        for cell in line.strip().strip("|").split("|"):
            cell = cell.strip().replace(",", "")
            if re.fullmatch(r"-?\d+(\.\d+)?", cell):
                nums.append(float(cell))
    return nums


def main():
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    con, user_id = load_truth_db(sb)

    passed = 0
    for case in CASES:
        q = case["q"]
        rag = execute_sql_query(q, user_id, sb)
        failures = []

        if case["kind"] == "number":
            truth = con.execute(case["truth_sql"]).fetchone()[0]
            if truth is None:
                failures.append("ground-truth SQL returned NULL — fix the truth query")
            else:
                cells = numeric_cells(rag)
                if not any(abs(v - float(truth)) < 0.01 for v in cells):
                    failures.append(f"truth={truth}, RAG numeric cells={cells}")
        else:
            expect = case.get("expect")
            if expect is None:
                expect = [str(con.execute(case["truth_sql"]).fetchone()[0])]
            for exp in expect:
                if exp.lower() not in rag.lower():
                    failures.append(f"expected text missing: '{exp}'")

        status = "PASS" if not failures else "FAIL"
        if not failures:
            passed += 1
        print(f"\n[{status}] {q}")
        for f in failures:
            print(f"  - {f}")
        if failures:
            sql_line = next((l for l in rag.splitlines() if l.startswith("SQL:")), "(no SQL line)")
            print(f"  {sql_line}")
            print("  --- RAG result head ---")
            print("  " + "\n  ".join(rag.splitlines()[:6]))

    print(f"\n{passed}/{len(CASES)} cases passed")
    sys.exit(0 if passed == len(CASES) else 1)


if __name__ == "__main__":
    main()
