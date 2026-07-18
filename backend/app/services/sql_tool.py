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


def _infer_column_types(cols: list, rows: list, sample_limit: int = 200) -> dict:
    """Majority-vote type inference: a column is DOUBLE when >=80% of its
    non-empty sampled values parse as numbers. Tolerates stray text like
    'N/A' or unit notes in otherwise-numeric columns (real-world CSVs)."""
    col_types = {}
    for c in cols:
        numeric = 0
        non_empty = 0
        for row in rows[:sample_limit]:
            val = row.get(c)
            if val is None or val == "":
                continue
            non_empty += 1
            if isinstance(val, (int, float)):
                numeric += 1
            elif isinstance(val, str):
                try:
                    float(val.replace(",", ""))
                    numeric += 1
                except ValueError:
                    pass
        col_types[c] = "DOUBLE" if non_empty and numeric / non_empty >= 0.8 else "VARCHAR"
    return col_types


def _sample_values(rows: list, col: str, limit: int = 8, max_len: int = 28, scan: int = 500) -> tuple:
    """Distinct non-empty sample values for a column, so the LLM can see real
    value formats (e.g. floor='4F' not '4th floor'). Returns (samples,
    is_exhaustive): is_exhaustive is True when the samples are ALL distinct
    values in the scanned rows — only then may the LLM treat them as an enum."""
    seen = []
    overflow = False
    for row in rows[:scan]:
        val = row.get(col)
        if val is None:
            continue
        s = str(val).strip()
        if not s or s[:max_len] in seen:
            continue
        if len(seen) >= limit:
            overflow = True
            break
        seen.append(s[:max_len])
    return seen, not overflow


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
    table_col_types = {}
    for t in tables:
        cols = t["columns"]
        sample_rows = t["rows"][:3] if t["rows"] else []

        # Majority-vote type inference over the data — shared with the DuckDB
        # load below so the schema shown to the LLM matches the actual types
        col_types = _infer_column_types(cols, t["rows"])
        table_col_types[t["table_name"]] = col_types

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
                if col_types.get(c) == "DOUBLE":
                    col_descs.append(f"  {c} (numeric)")
                else:
                    samples, exhaustive = _sample_values(t["rows"], c)
                    sample_str = ", ".join(repr(s) for s in samples[:8])
                    if not samples:
                        col_descs.append(f"  {c} (text)")
                    elif exhaustive:
                        col_descs.append(f"  {c} (text; possible values: {sample_str})")
                    else:
                        col_descs.append(f"  {c} (text; many distinct values, examples: {sample_str})")
            schema_desc += f"\nTable: {t['table_name']} ({t['row_count']} rows)\nColumns:\n" + "\n".join(col_descs) + "\n"

    # 3. Use Gemini to generate DuckDB SQL
    # 3-minute request timeout — a wedged HTTP connection otherwise hangs the
    # SQL generation forever (same fix as openai_client._get_client).
    client = genai.Client(api_key=get_llm_api_key(),
                          http_options=genai_types.HttpOptions(timeout=180_000))
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
- Columns marked (numeric) are already DOUBLE type — do NOT use CAST or TRY_CAST on them, just use column names directly (e.g. SELECT SUM(jan + feb + mar) not SELECT SUM(CAST(jan AS DOUBLE) + ...))
- Columns marked (text) are VARCHAR — if arithmetic on a text column is unavoidable, wrap it in TRY_CAST(col AS DOUBLE)
- Keep queries compact on a single line
- For text comparisons use ILIKE for case-insensitive matching
- Match the FORMAT of the column samples — e.g. if floor values look like 'GF', '4F', '6F' then the 4th floor is floor = '4F' (never '%4th%' or 'fourth')
- Columns marked 'possible values' list the complete set — filter with those exact values. Columns marked 'examples' have MANY OTHER values — if the user's term (e.g. 'FCU') is not among the examples, still filter for it directly with ILIKE '%term%'; NEVER substitute a different example value for the user's term
- Tables often reference each other by shared identifier values (e.g. a board/panel name column in one table matching a name column in another) — use JOINs across tables when a question spans them
- NEVER drop a constraint from the question. If the user names an equipment/load type (e.g. 'FCU'), the WHERE clause MUST filter on it (e.g. load_type ILIKE '%FCU%') IN ADDITION TO any floor/block/area filters — a floor filter alone returns every load type on that floor, which is wrong
- When counting equipment/units and the table has a quantity column (e.g. 'points', 'qty', 'count'), SUM that column instead of COUNT(*) — one row can represent multiple units
- When the user asks for a breakdown, list, itemization, or table of items, do NOT select only identifier columns — also SELECT every column that makes a row meaningful on its own: any room/area/location/description column, the quantity column (e.g. 'points', 'qty'), and the load/rating column if relevant. Example: for "breakdown of FCUs" select db, cir_no, room_area, points — not just db and cir_no
- For breakdowns/lists, ORDER BY the natural reading order: the board/panel column first, then the serial/row number cast numerically (e.g. ORDER BY db, TRY_CAST(sl_no AS INTEGER)) so circuits appear in as-printed order
- For single-row value lookups (not aggregates), also SELECT the notes/remarks column when the table has one — source documents sometimes contain corrections (a printed value struck out and replaced by hand). The notes record this, and the answer must be able to report the current value versus the original
- If a table has a parent-reference column (e.g. 'fed_from'), a parent row's totals already INCLUDE its children — summing all rows in an area double-counts. Sum ONLY rows whose parent is OUTSIDE the filtered set, using this exact pattern: SELECT SUM(x.tcl_kw) FROM "panels" x WHERE <area filter on x> AND NOT EXISTS (SELECT 1 FROM "panels" p WHERE p.panel = x.fed_from AND <same area filter on p>)
- When the user asks for a single NAMED panel/board's value (its total connected load, maximum demand / MDL / diversified load, rating), read that panel's own row from the panel-schedule table (e.g. SELECT tcl_kw, mdl_kw, notes FROM "panels" WHERE panel = 'MDB-C-G2') — NEVER answer it by SUMming a per-load-type calculation table (e.g. "mdb_calc"): those rows itemize the design calculation and their sum ignores diversity, giving an inflated wrong number. This holds even if the question mentions the calculation table. Use the calculation table ONLY when the question is about a specific load type's row, diversity factors, or the design-calc breakdown itself
- When filtering rows by an equipment/usage KEYWORD (e.g. 'cleaner', 'cooker'), apply the ILIKE filter across ALL descriptive text columns OR-ed together (e.g. load_type ILIKE '%kw%' OR remarks ILIKE '%kw%') — source schedules record such labels in whichever text column the drafter chose
- When the question relates one panel to another ('the feeder X on/from board Y', 'the breaker rating of the feeder from Y to X'), read the FEEDER-SCHEDULE table whose rows pair a parent board column with a feeder column (e.g. "smdb_feeders" WHERE smdb='Y' AND feeder='X') — the flat panel list may have a blank row for the same name
- For superlative/comparison questions about panels' or boards' totals ('which board has the highest connected load'), rank the panel-schedule's own total column (e.g. ORDER BY tcl_kw DESC NULLS LAST) — do NOT re-derive totals by summing the circuits table; the printed schedule totals are authoritative
- The tables record CONNECTED LOADS and ratings (W, kW, A) — NOT energy consumption, runtime, or cost. If the question asks for something the tables do not record (kWh consumed, annual energy usage, operating hours, bills), NEVER approximate it from load columns (e.g. multiplying by hours) — return a query with no rows instead (SELECT NULL WHERE FALSE) so the system can look elsewhere

User question: {question}"""

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            temperature=0,
            # Thinking models (gemini-2.5+/3) spend "thought" tokens from this same
            # budget — 2048 sometimes truncated the SQL mid-string. Keep it high.
            max_output_tokens=8192,
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

            # Reuse the majority-vote types computed for the schema description
            # so the LLM's view and the actual DuckDB types always agree
            col_types = table_col_types.get(t["table_name"]) or _infer_column_types(cols, rows)

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

        # 5. Execute SQL — on failure, give the LLM one shot at repairing the
        # query with the actual error message before falling back
        try:
            result = con.execute(sql).fetchall()
        except Exception as first_err:
            logger.warning(f"SQL failed ({first_err}), attempting LLM repair")
            repair_prompt = (
                f"The following DuckDB SQL query failed.\n\n"
                f"Query: {sql}\n\nError: {first_err}\n\n{schema_desc}\n"
                f"Fix the query. Columns marked (text) are VARCHAR — use TRY_CAST(col AS DOUBLE) "
                f"for arithmetic on them. Return ONLY the corrected SQL on a single line, "
                f"no explanation, no semicolons, no code fences."
            )
            repair_resp = client.models.generate_content(
                model=model,
                contents=repair_prompt,
                config=genai_types.GenerateContentConfig(temperature=0, max_output_tokens=8192),
            )
            sql = (repair_resp.text or "").strip().rstrip(";")
            if sql.startswith("```"):
                lines = sql.split("\n")
                sql = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
            sql = _fix_table_names(sql, real_table_names)
            logger.info(f"Repaired SQL: {sql}")
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

        # Hierarchy warning must travel WITH the result — the answer-writing
        # model never sees the SQL-generation prompt, and without this it adds
        # parent totals to child totals (double-counting)
        parent_cols = sorted({
            c for t in tables for c in t["columns"]
            if "fed_from" in str(c).lower() or str(c).lower() in ("parent", "parent_id")
        })
        if parent_cols:
            md += (
                f"\n\nIMPORTANT (for interpreting these results): this data is hierarchical "
                f"(parent-reference column: {', '.join(parent_cols)}). A parent row's totals "
                f"already INCLUDE everything fed from it — when reporting a total for an area, "
                f"use only the topmost row(s); never add a parent's total to its children's totals."
            )
        return md

    except Exception as e:
        logger.error(f"SQL execution error: {e}")
        return f"SQL query failed: {e}\n\nGenerated SQL: `{sql}`"
    finally:
        con.close()
