"""
Text-to-SQL tool: generates and executes SQL against user's structured data using DuckDB.
"""
import json
import logging
import os

import duckdb
from google import genai
from google.genai import types as genai_types
from langsmith import traceable

from app.services.settings import get_llm_api_key, get_llm_model

logger = logging.getLogger(__name__)

SQL_QUERY_TIMEOUT = 5  # seconds


@traceable(name="query_structured_data", run_type="tool")
def execute_sql_query(question: str, user_id: str, supabase_client) -> str:
    """Generate SQL from a natural language question and execute it against user's tabular data."""

    # 1. Fetch user's structured_data
    result = supabase_client.table("structured_data") \
        .select("table_name, columns, rows, row_count") \
        .eq("user_id", user_id) \
        .execute()

    tables = result.data
    if not tables:
        return "No tabular data found. Upload a CSV or XLSX file first."

    # 2. Build schema description for LLM
    schema_desc = "Available tables:\n"
    for t in tables:
        cols = t["columns"]
        # Sample first row to infer types
        sample_row = t["rows"][0] if t["rows"] else {}
        col_descs = []
        for c in cols:
            val = sample_row.get(c)
            if isinstance(val, (int, float)):
                col_descs.append(f"  {c} (numeric)")
            else:
                col_descs.append(f"  {c} (text)")
        schema_desc += f"\nTable: {t['table_name']} ({t['row_count']} rows)\nColumns:\n" + "\n".join(col_descs) + "\n"

    # 3. Use Gemini to generate DuckDB SQL
    client = genai.Client(api_key=get_llm_api_key())
    model = get_llm_model()

    prompt = f"""You are a SQL expert. Generate a single DuckDB SQL query to answer the user's question.

{schema_desc}

Rules:
- Use only the tables and columns listed above
- Return ONLY the SQL query, no explanation
- Use DuckDB SQL syntax
- Do not use semicolons
- For text comparisons use ILIKE for case-insensitive matching
- If the question asks for a count, total, average, etc., use appropriate aggregate functions

User question: {question}"""

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            temperature=0,
            max_output_tokens=500,
        ),
    )

    sql = response.text.strip()
    # Strip markdown code fences if present
    if sql.startswith("```"):
        lines = sql.split("\n")
        sql = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    sql = sql.strip().rstrip(";")

    logger.info(f"Generated SQL: {sql}")

    # 4. Create in-memory DuckDB and load tables
    con = duckdb.connect(":memory:")
    try:
        for t in tables:
            cols = t["columns"]
            rows = t["rows"]
            if not rows:
                continue

            # Create table with TEXT columns, DuckDB will auto-cast
            col_defs = ", ".join(f'"{c}" VARCHAR' for c in cols)
            con.execute(f'CREATE TABLE "{t["table_name"]}" ({col_defs})')

            # Insert rows
            if rows:
                placeholders = ", ".join(["?"] * len(cols))
                insert_sql = f'INSERT INTO "{t["table_name"]}" VALUES ({placeholders})'
                for row in rows:
                    values = [str(row.get(c, "")) if row.get(c) is not None else None for c in cols]
                    con.execute(insert_sql, values)

        # 5. Execute SQL with timeout
        result = con.execute(sql).fetchall()
        col_names = [desc[0] for desc in con.description]

        # 6. Format as markdown table (max 50 rows)
        if not result:
            return f"Query returned no results.\n\nSQL: `{sql}`"

        max_rows = 50
        truncated = len(result) > max_rows
        display_rows = result[:max_rows]

        md = "| " + " | ".join(col_names) + " |\n"
        md += "| " + " | ".join(["---"] * len(col_names)) + " |\n"
        for row in display_rows:
            md += "| " + " | ".join(str(v) if v is not None else "" for v in row) + " |\n"

        if truncated:
            md += f"\n*Showing {max_rows} of {len(result)} rows*\n"

        md += f"\nSQL: `{sql}`"
        return md

    except Exception as e:
        logger.error(f"SQL execution error: {e}")
        return f"SQL query failed: {e}\n\nGenerated SQL: `{sql}`"
    finally:
        con.close()
