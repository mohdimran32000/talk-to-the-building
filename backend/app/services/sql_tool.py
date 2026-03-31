"""
Text-to-SQL tool: generates and executes SQL against user's structured data using DuckDB.
"""
import json
import logging
import os
import re

import duckdb
from google import genai
from google.genai import types as genai_types
from langsmith import traceable

from app.services.settings import get_llm_api_key, get_llm_model

logger = logging.getLogger(__name__)

SQL_QUERY_TIMEOUT = 5  # seconds


def _fix_table_names(sql: str, real_table_names: list[str]) -> str:
    """Fix truncated or incorrect table names in generated SQL by fuzzy matching."""
    # Find all table references in SQL: FROM "xxx" or FROM xxx or JOIN "xxx" etc.
    # Also handle unquoted table names
    words = re.findall(r'(?:FROM|JOIN)\s+"?([a-zA-Z0-9_]+)"?', sql, re.IGNORECASE)
    real_set = set(real_table_names)

    for word in words:
        if word in real_set:
            continue  # Already correct

        # Find best match: prefer table name that starts with the generated name
        best_match = None
        best_len = 0
        for real in real_table_names:
            if real.startswith(word) and len(real) > best_len:
                best_match = real
                best_len = len(real)

        # Also check if any real name contains the word
        if not best_match:
            for real in real_table_names:
                if word in real and len(real) > best_len:
                    best_match = real
                    best_len = len(real)

        if best_match and best_match != word:
            logger.info(f"SQL table name fix: '{word}' → '{best_match}'")
            # Replace both quoted and unquoted forms
            sql = sql.replace(f'"{word}"', f'"{best_match}"')
            sql = re.sub(rf'\b{re.escape(word)}\b', f'"{best_match}"', sql)

    return sql


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
    # For wide tables (>30 cols), include sample rows so the LLM can understand the structure
    MAX_COLS_DETAILED = 30
    schema_desc = "Available tables:\n"
    for t in tables:
        cols = t["columns"]
        sample_rows = t["rows"][:3] if t["rows"] else []
        sample_row = sample_rows[0] if sample_rows else {}

        if len(cols) > MAX_COLS_DETAILED:
            # Wide table — show sample rows instead of column list
            schema_desc += f"\nTable: {t['table_name']} ({t['row_count']} rows, {len(cols)} columns)\n"
            schema_desc += "This table has many columns. Here are the first few sample rows:\n"
            for i, row in enumerate(sample_rows[:3]):
                # Show only non-empty values
                non_empty = {k: v for k, v in row.items() if v is not None and str(v).strip()}
                # Limit to first 20 non-empty columns for readability
                items = list(non_empty.items())[:20]
                schema_desc += f"  Row {i}: {dict(items)}\n"
        else:
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

    # Build a list of exact table names for the prompt and post-processing
    real_table_names = [t["table_name"] for t in tables]

    prompt = f"""You are a SQL expert. Generate a single DuckDB SQL query to answer the user's question.

{schema_desc}

IMPORTANT — exact table names (copy-paste these, do NOT abbreviate or truncate):
{chr(10).join(f'  - "{name}"' for name in real_table_names)}

Rules:
- Use ONLY the exact table names listed above — copy them exactly, do not shorten them
- Always quote table names with double quotes (e.g. FROM "my_table_name")
- Use only the columns listed above
- Return ONLY the SQL query on a single line, no explanation, no formatting, no newlines
- Use DuckDB SQL syntax
- Do not use semicolons
- Numeric columns are already DOUBLE type — do NOT use CAST or TRY_CAST, just use column names directly (e.g. SELECT SUM(jan + feb + mar) not SELECT SUM(CAST(jan AS DOUBLE) + ...))
- Keep queries compact on a single line
- For text comparisons use ILIKE for case-insensitive matching

User question: {question}"""

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            temperature=0,
            max_output_tokens=2048,
        ),
    )

    sql = response.text.strip()
    # Strip markdown code fences if present
    if sql.startswith("```"):
        lines = sql.split("\n")
        sql = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    sql = sql.strip().rstrip(";")

    # Fix truncated/incorrect table names by fuzzy matching
    sql = _fix_table_names(sql, real_table_names)

    logger.info(f"Generated SQL: {sql}")

    # 4. Create in-memory DuckDB and load tables with inferred types
    con = duckdb.connect(":memory:")
    try:
        for t in tables:
            cols = t["columns"]
            rows = t["rows"]
            if not rows:
                continue

            # Infer column types from sample data (check first 10 rows)
            col_types = {}
            for c in cols:
                is_numeric = False
                for row in rows[:10]:
                    val = row.get(c)
                    if val is not None and val != "":
                        if isinstance(val, (int, float)):
                            is_numeric = True
                        elif isinstance(val, str):
                            try:
                                float(val.replace(",", ""))
                                is_numeric = True
                            except (ValueError, AttributeError):
                                is_numeric = False
                                break
                col_types[c] = "DOUBLE" if is_numeric else "VARCHAR"

            col_defs = ", ".join(f'"{c}" {col_types[c]}' for c in cols)
            con.execute(f'CREATE TABLE "{t["table_name"]}" ({col_defs})')

            # Insert rows with type-appropriate values
            placeholders = ", ".join(["?"] * len(cols))
            insert_sql = f'INSERT INTO "{t["table_name"]}" VALUES ({placeholders})'
            for row in rows:
                values = []
                for c in cols:
                    val = row.get(c)
                    if val is None:
                        values.append(None)
                    elif col_types[c] == "DOUBLE":
                        try:
                            values.append(float(str(val).replace(",", "")))
                        except (ValueError, TypeError):
                            values.append(None)
                    else:
                        values.append(str(val))
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
