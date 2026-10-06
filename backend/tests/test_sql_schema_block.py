"""test_sql_schema_block.py - a truthful schema block (spec-fix5, task T9a, 2026-10-01).

The SQL writer reads every routed table's columns with their values, and the prompt told it
that a list marked 'possible values' was "the complete set". It was not: the values came from
the first 500 rows only, at most 8 of them, each silently cut at 28 characters. Measured on the
goal-function run of 2026-09-30, over the real tables:

  * on the tables longer than 500 rows the 'complete' label was false (a register's system
    column hid the systems whose rows come late; a date column hid one of its printed formats);
  * 'complete' lists held values cut mid-word, which an exact filter can never match;
  * columns with a couple of dozen values showed only 8 of them, as examples;
  * a table wider than 30 columns showed 3 sample rows x 20 columns - its notes column was
    invisible;
  * each table card's `holds` sentence - which says what the table does NOT carry and where
    that lives - never reached the writer.

Now every column is read off EVERY row (cached per table per upload): up to 40 distinct values
are all listed as 'possible values (complete)', more print 'N distinct values, examples: ...';
a value longer than 28 characters is printed cut and marked with '…', and the prompt says to
match such a value with ILIKE 'start%'; a wide table lists every column name and type; and each
routed card's `holds` sentence is printed under its table's heading. A printed date column with
an ISO companion `<x>_iso` gets one rule: compare, sort and filter on the companion.

T9a-R0: the level-code column's own whole-column listing (spec-fix3) is folded into this one
path. T9a-R1: numeric columns are unchanged. T9a-R3: routing, the loop and the result are
untouched - only the schema text the writer reads.

Fix round 1 (review): T9a-R4 - a cut form that an ILIKE on its start would match to several
values says so, '(N values)', counted over the whole column as DuckDB's ILIKE counts; T9a-R5 - a
holds that is only the generated column listing is not printed; a value is printed as the loaded
table holds it (a trailing space kept - trimmed, '=' matched nothing); the examples de-duplicate
by printed form; the cache key reads each cell under its own column.

Every fixture is invented (bld_* tables, nonsense values). Every check marked RED fails against
sql_tool.py as it stood before the change it tests: 4c07e45 for T9a, ee8ad51 for its fix round 1
(T9a-R4 counts, T9a-R5 listing holds, raw values, the per-column cache key).

Run:
    venv/Scripts/python -X utf8 tests/test_sql_schema_block.py
"""
import ast
import copy
import inspect
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb  # noqa: E402

from app.services import sql_tool  # noqa: E402

# The fake writer's runaway query is refused by design (see the harness); its log line is noise.
logging.getLogger(sql_tool.__name__).setLevel(logging.CRITICAL)

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


# ---------------------------------------------------------------------------
# The harness: `execute_sql_query` end to end up to the SQL writer, with a fake writer that
# keeps its prompt. The fake answers with a runaway query, which the tool refuses BEFORE any
# DuckDB load - the prompt is all these checks read.
# ---------------------------------------------------------------------------
class _Exec:
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
                return _Exec(copy.deepcopy(list(tables)))

        return _Q()


class _Models:
    prompts = []

    def generate_content(self, *, model, contents, config=None):
        _Models.prompts.append(contents)
        return type("_R", (), {"text": "SELECT " + ", ".join(["NULL"] * 25),
                               "usage_metadata": None})()


class _Client:
    def __init__(self, *a, **kw):
        self.models = _Models()


def prompt_for(tables, cards=(), routed=None, question="list every unit"):
    """The SQL-writer prompt for `tables`. No cards: the unrouted full schema. `routed`: the
    router's selection, replayed."""
    saved = (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
             sql_tool._load_table_cards, sql_tool._routed_table_names)
    sql_tool.genai.Client = _Client
    sql_tool.get_llm_api_key = lambda: "fake-key"
    sql_tool.get_llm_model = lambda: "fake-model"
    sql_tool._load_table_cards = lambda *a, **k: list(cards)
    if routed is not None:
        sql_tool._routed_table_names = lambda *a, **k: list(routed)
    _Models.prompts = []
    try:
        sql_tool.execute_sql_query(question, "u-1", _Supabase(list(tables)))
    finally:
        (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
         sql_tool._load_table_cards, sql_tool._routed_table_names) = saved
    return _Models.prompts[0] if _Models.prompts else ""


def schema_of(prompt):
    """The schema block: from 'Available tables:' to the exact-names list after it."""
    start = prompt.find("Available tables:")
    end = prompt.find("\n\nIMPORTANT", start)
    return prompt[start:end] if start >= 0 and end > start else ""


def section_of(prompt, table):
    """One table's part of the schema block: its heading line to the next heading."""
    block = schema_of(prompt)
    start = block.find(f"\nTable: {table} (")
    if start < 0:
        return ""
    end = block.find("\nTable: ", start + 1)
    return block[start + 1:end if end > 0 else len(block)]


def col_line(prompt, table, col):
    return next((ln for ln in section_of(prompt, table).splitlines()
                 if ln.startswith(f"  {col} (")), "")


#: One printed form: a Python string literal (values are printed with repr), and - for a cut form
#: an ILIKE on whose start matches more than one value (T9a-R4) - ' (N values)' after it.
_FORM = re.compile(r"""('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")(?: \((\d+) values\))?""")


def forms(line):
    """[(value, N or None)] a column line lists, or None when it lists none - or when the list
    does not read back WHOLE as forms joined by ', ' (so nothing is ever skipped silently)."""
    m = re.search(r"\(text; [^:]*: (.*)\)$", line)
    if not m:
        return None
    found = list(_FORM.finditer(m.group(1)))
    if not found or ", ".join(f.group(0) for f in found) != m.group(1):
        return None
    try:
        return [(ast.literal_eval(f.group(1)), int(f.group(2)) if f.group(2) else None)
                for f in found]
    except (ValueError, SyntaxError):
        return None


def listed(line):
    """The values a column line lists, read back as Python literals, or None."""
    got = forms(line)
    return None if got is None else [v for v, _ in got]


def counts(line):
    """{printed form: N} for the forms a column line prints with ' (N values)', or None."""
    got = forms(line)
    return None if got is None else {v: n for v, n in got if n is not None}


def table(name, columns, rows):
    return {"table_name": name, "columns": list(columns), "rows": rows, "row_count": len(rows)}


def table_card(name, holds):
    return {"table": name, "columns": [], "holds": holds}


# ===========================================================================
print("1. A value that first appears after row 500 is never hidden behind a 'complete' label")
# ===========================================================================
EARLY = ("Zarn", "Yelm", "Xoph")
ROWS1 = []
for i in range(600):
    glaze = "Wuld" if i == 500 else EARLY[i % 3]
    hue = f"Qa{1 + i % 3}" if i < 500 else f"Qz-{(i - 500) // 2:03d}"
    ROWS1.append({"unit_tag": f"UT-{i:04d}", "glaze": glaze, "hue": hue})
GLAZES = table("bld_glazes", ["unit_tag", "glaze", "hue"], ROWS1)
p1 = prompt_for([GLAZES])
glaze1, hue1 = col_line(p1, "bld_glazes", "glaze"), col_line(p1, "bld_glazes", "hue")
check("fixture sanity: the prompt was built and shows the columns", bool(glaze1) and bool(hue1),
      p1[:600])
check("RED: a column whose row 501 brings a fourth value lists it - read off every row",
      "'Wuld'" in glaze1, glaze1)
check("RED: and, all four being listed, the list is labelled complete",
      "possible values (complete): " in glaze1
      and listed(glaze1) == ["Zarn", "Yelm", "Xoph", "Wuld"], glaze1)
check("RED: never a 'complete' label on a list that misses a value the table holds",
      not ("possible values" in glaze1 and "'Wuld'" not in glaze1), glaze1)
check("RED: a column whose rows 501-600 bring 50 more values is never labelled complete",
      "possible values" not in hue1, hue1)
check("RED: and its count is right - all 53 distinct values, counted over every row",
      "(text; 53 distinct values, examples: " in hue1, hue1)
check("RED: its examples are the first 8 the table holds, in the order it first holds them",
      listed(hue1) == ["Qa1", "Qa2", "Qa3", "Qz-000", "Qz-001", "Qz-002", "Qz-003", "Qz-004"],
      hue1)
check("RED: the identifier column says how many it holds too",
      "(text; 600 distinct values, examples: 'UT-0000', " in col_line(p1, "bld_glazes",
                                                                      "unit_tag"),
      col_line(p1, "bld_glazes", "unit_tag"))

# ===========================================================================
print("\n2. Up to 40 distinct values across the whole table are ALL listed, as complete")
# ===========================================================================
ROWS2 = []
for i in range(900):
    ROWS2.append({"bin40": f"Bq{i % 30:02d}" if i < 500 else f"Bq{30 + i % 10:02d}",
                  "bin41": f"K{i % 41:02d}", "bin12": f"Nv{i % 12:02d}",
                  "qty": str(1 + i % 9), "blank": ""})
BINS = table("bld_bins", ["bin40", "bin41", "bin12", "qty", "blank"], ROWS2)
p2 = prompt_for([BINS])
b40, b41, b12 = (col_line(p2, "bld_bins", c) for c in ("bin40", "bin41", "bin12"))
check("RED: 40 distinct values, ten of them first seen after row 500, are listed in full",
      "possible values (complete): " in b40
      and listed(b40) == [f"Bq{n:02d}" for n in range(40)], b40)
check("RED: 41 distinct values are counted, never listed as complete",
      "(text; 41 distinct values, examples: " in b41 and "possible values" not in b41, b41)
check("its examples stay at today's count of 8, in first-appearance order",
      listed(b41) == [f"K{n:02d}" for n in range(8)], b41)
check("RED: a small column of 12 values (today shown as 8 examples) lists all 12 as complete",
      "possible values (complete): " in b12
      and listed(b12) == [f"Nv{n:02d}" for n in range(12)], b12)
check("T9a-R1: a numeric column is unchanged - '(numeric)' and no values",
      col_line(p2, "bld_bins", "qty") == "  qty (numeric)", col_line(p2, "bld_bins", "qty"))
check("a text column with no value at all is unchanged - '(text)' alone",
      col_line(p2, "bld_bins", "blank") == "  blank (text)", col_line(p2, "bld_bins", "blank"))
block2 = schema_of(p2) + schema_of(p1)
check("RED: the old labels are gone - no bare 'possible values:' and no 'many distinct values'",
      "(text; possible values: " not in block2 and "many distinct values" not in block2,
      [ln for ln in block2.splitlines() if "possible values: " in ln or "many distinct" in ln])
LABELS2 = re.findall(r"\(text; ([^:]*): ", block2)
check("RED: every value list carries one of exactly two labels",
      bool(LABELS2) and all(lab == "possible values (complete)"
                            or re.fullmatch(r"\d+ distinct values, examples", lab)
                            for lab in LABELS2), LABELS2)

# ===========================================================================
print("\n3. A value longer than 28 characters is printed cut, marked '…', and matched with ILIKE")
# ===========================================================================
SEVEN = "Quillwort gallery humidifier unit seven"
EIGHT = "Quillwort gallery humidifier unit eight"
EXACT = "Zephyr loft vent damper 28ch"
ROWS3 = ([{"label": v, "remark": f"Vellum ledger annotation number {i:03d}"}
          for i, v in enumerate([SEVEN, EIGHT, EXACT, "Short tag"])]
         + [{"label": "Short tag", "remark": f"Vellum ledger annotation number {i:03d}"}
            for i in range(4, 45)])
LABELS = table("bld_labels", ["label", "remark"], ROWS3)
p3 = prompt_for([LABELS])
label3, remark3 = col_line(p3, "bld_labels", "label"), col_line(p3, "bld_labels", "remark")
check("fixture sanity: the 28-character value really is 28 characters", len(EXACT) == 28)
check("RED: a value longer than 28 characters is printed as its first 28 and the '…' marker",
      "'Quillwort gallery humidifier…'" in label3, label3)
check("a value of exactly 28 characters is printed whole, with no marker",
      repr(EXACT) in label3 and EXACT + "…" not in label3, label3)
check("RED: two values that differ only after the cut share one printed form, printed once",
      listed(label3) == ["Quillwort gallery humidifier…", EXACT, "Short tag"], label3)
check("RED: T9a-R4: that shared form says how many values it stands for",
      "'Quillwort gallery humidifier…' (2 values), " in label3
      and counts(label3) == {"Quillwort gallery humidifier…": 2}, label3)
check("RED: 45 long values that one cut would collapse are counted as 45 - never one "
      "'complete' value", "(text; 45 distinct values, examples: " in remark3
      and "possible values" not in remark3, remark3)
check("fix round 1: the examples de-duplicate by printed form too - the 45 values print as ONE "
      "example, never the same form eight times",
      listed(remark3) == ["Vellum ledger annotation num…"], remark3)
check("RED: T9a-R4: ... and that one example says it stands for all 45",
      counts(remark3) == {"Vellum ledger annotation num…": 45}, remark3)
FULL3 = {v for r in ROWS3 for v in r.values()}
printed3 = [v for ln in schema_of(p3).splitlines() for v in (listed(ln) or [])]
check("RED: no value is printed silently cut - each is a value the table holds, or a marked "
      "cut of one", bool(printed3) and all(
          v in FULL3 or (v.endswith("…") and any(f.startswith(v[:-1]) for f in FULL3))
          for v in printed3), printed3)
check("a marked cut is never longer than 28 characters and the marker",
      all(len(v) <= 29 for v in printed3), printed3)
RULES3 = [ln for ln in p3.splitlines() if ln.startswith("- ")]
cut_rule = next((ln for ln in RULES3 if "…" in ln and "cut" in ln.lower()), "")
check("RED: the prompt says a value shown ending in '…' was cut for display",
      "value shown ending in '…' was cut for display" in cut_rule, cut_rule)
check("RED: ... match it with ILIKE 'start%', never with =",
      "ILIKE 'start%'" in cut_rule and "never with =" in cut_rule, cut_rule)
check("RED: T9a-R4: ... and says a '(N values)' after it is how many values that ILIKE matches",
      "a '(N values)' after it says that ILIKE matches N different values" in cut_rule, cut_rule)
labels_rule = next((ln for ln in RULES3 if ln.startswith("- Columns marked 'possible values")),
                   "")
check("RED: the labels rule names the two labels exactly as the block prints them",
      "'possible values (complete)'" in labels_rule
      and "'N distinct values, examples'" in labels_rule, labels_rule)
check("RED: and no longer calls a list 'the complete set' under the old label",
      "list the complete set" not in p3 and "Columns marked 'possible values' " not in p3,
      labels_rule)
check("the labels rule keeps its guard: a term not among the examples is still filtered with "
      "ILIKE, never swapped for an example", "ILIKE '%term%'" in labels_rule
      and "NEVER substitute a different example value" in labels_rule, labels_rule)

# ===========================================================================
print("\n4. A table wider than 30 columns lists every column name and type")
# ===========================================================================
WIDE_COLS = [f"w{n:02d}" for n in range(1, 38)] + ["jotting"]
ROWS4 = []
for i in range(5):
    row = {c: ("" if n >= 25 and i < 3 else f"v{n}-{i}") for n, c in enumerate(WIDE_COLS)}
    row["w01"] = str(10 + i)
    ROWS4.append(row)
WIDE = table("bld_wide", WIDE_COLS, ROWS4)
p4 = prompt_for([WIDE])
sec4 = section_of(p4, "bld_wide")
check("fixture sanity: 38 columns, more than MAX_COLS_DETAILED",
      len(WIDE_COLS) == 38 and len(WIDE_COLS) > getattr(sql_tool, "MAX_COLS_DETAILED", 30))
check("fixture sanity: the notes-like column is blank in the three sample rows, so today's "
      "sample rows never show it", "jotting" not in "".join(
          ln for ln in sec4.splitlines() if ln.startswith("  Row ")), sec4)
listing4 = next((ln for ln in sec4.splitlines() if ln.startswith("Columns (all 38): ")), "")
check("RED: the wide table lists all 38 columns on one line", bool(listing4), sec4[:500])
check("RED: every column name is on it, in the table's own order",
      re.findall(r"(\w+) \((?:text|numeric)\)", listing4) == WIDE_COLS, listing4)
check("RED: with its type - numeric and text alike",
      "w01 (numeric)" in listing4 and "jotting (text)" in listing4, listing4)
check("RED: the listing comes BEFORE the sample rows",
      bool(listing4) and sec4.find(listing4) < sec4.find("Here are the first few sample rows"),
      sec4[:600])
check("the sample rows are still there, unchanged",
      "This table has many columns. Here are the first few sample rows:" in sec4
      and "  Row 0: " in sec4, sec4)

# ===========================================================================
print("\n5. Each routed card's holds sentence is printed under its table's heading")
# ===========================================================================
CRATES = table("bld_crates", ["crate_tag", "glaze"], [{"crate_tag": "CR-1", "glaze": "Zarn"}])
PALLETS = table("bld_pallets", ["pallet_tag"], [{"pallet_tag": "PL-1"}])
SPARE = table("bld_spare", ["spare_tag"], [{"spare_tag": "SP-1"}])
BARE = table("bld_bare", ["bare_tag"], [{"bare_tag": "BR-1"}])
HOLDS_CRATES = ("One row per crate on the quay; it carries no berth column - the berth of each "
                "crate is in bld_pallets.")
HOLDS_LONG = "  ".join(f"Pallet clause {n:02d} of the quay ledger." for n in range(1, 30))
CARDS5 = [table_card("bld_crates", HOLDS_CRATES), table_card("bld_pallets", HOLDS_LONG),
          table_card("bld_spare", "One row per spare kept beside the quay."),
          table_card("bld_bare", "")]
TABLES5 = [CRATES, PALLETS, SPARE, BARE]
p5 = prompt_for(TABLES5, CARDS5, routed=["bld_crates", "bld_pallets", "bld_bare"])
sec5c, sec5p, sec5b = (section_of(p5, n) for n in ("bld_crates", "bld_pallets", "bld_bare"))
check("fixture sanity: the router's selection was replayed - the unrouted table is not shown",
      bool(sec5c) and bool(sec5p) and "Table: bld_spare" not in p5, schema_of(p5)[:400])
check("RED: the routed card's holds sentence is the line right under its table's heading",
      sec5c.splitlines()[1:2] == ["Holds: " + HOLDS_CRATES], sec5c)
source5 = sql_tool._source_lines('SELECT 1 FROM "bld_pallets"', [PALLETS], CARDS5)
quoted5 = source5.splitlines()[0].split(": ", 1)[1] if source5 else ""
check("fixture sanity: the long holds sentence is longer than 400 characters, and its SOURCE "
      "line cuts it", len(HOLDS_LONG) > 400 and 0 < len(quoted5) <= 400, (len(HOLDS_LONG),
                                                                          len(quoted5)))
check("RED: a holds sentence over 400 characters is cut exactly as the SOURCE line cuts it",
      sec5p.splitlines()[1:2] == ["Holds: " + quoted5], sec5p[:700])
check("a card with no holds sentence adds no line", "Holds:" not in sec5b, sec5b)
p5u = prompt_for(TABLES5)
check("no cards (the unrouted full schema): no holds line anywhere", "Holds:" not in p5u
      and "Table: bld_spare" in p5u, schema_of(p5u)[:300])


def _router_fails(*a, **k):
    raise RuntimeError("router down")


saved5 = sql_tool._routed_table_names
sql_tool._routed_table_names = _router_fails
try:
    p5f = prompt_for(TABLES5, CARDS5)
finally:
    sql_tool._routed_table_names = saved5
check("cards loaded but routing failed (the full-schema fallback): no holds line anywhere",
      "Holds:" not in p5f and "Table: bld_spare" in p5f, schema_of(p5f)[:300])

# ===========================================================================
print("\n6. The scan is cached per table per upload, and a changed table invalidates it")
# ===========================================================================
reset6 = getattr(sql_tool, "_reset_schema_cache", None)
scan6 = getattr(sql_tool, "_column_values", None)
check("RED: the schema cache and the whole-column scan exist",
      callable(reset6) and callable(scan6), (reset6, scan6))
if callable(reset6) and callable(scan6):
    calls6 = []

    def _counting(rows, col):
        calls6.append(col)
        return scan6(rows, col)

    sql_tool._column_values = _counting
    try:
        reset6()
        a1 = schema_of(prompt_for([GLAZES]))
        n1 = len(calls6)
        a2 = schema_of(prompt_for([GLAZES]))
        check("the same table read twice prints the same block", a1 == a2 and bool(a1), a2[:300])
        check("RED: and is scanned once - the second read is served from the cache",
              n1 == 3 and len(calls6) == n1, (n1, len(calls6)))
        rows6 = copy.deepcopy(ROWS1)
        rows6[300]["glaze"] = "Vosk"
        a3 = schema_of(prompt_for([table("bld_glazes", GLAZES["columns"], rows6)]))
        check("RED: one changed middle cell - same rows, same columns - invalidates it",
              "'Vosk'" in a3 and len(calls6) > n1, (len(calls6), a3[:300]))
        n3 = len(calls6)
        rows6b = copy.deepcopy(ROWS1) + [{"unit_tag": "UT-0600", "glaze": "Zarn", "hue": "Qa1"}]
        a4 = schema_of(prompt_for([table("bld_glazes", GLAZES["columns"], rows6b)]))
        check("RED: a re-upload with one more row invalidates it",
              "(601 rows)" in a4 and "601 distinct values" in a4 and len(calls6) > n3, a4[:300])
        saved_max = getattr(sql_tool, "_SCHEMA_CACHE_MAX", None)
        sql_tool._SCHEMA_CACHE_MAX = 2
        try:
            for n in range(5):
                prompt_for([table(f"bld_lot{n}", ["lot_tag"], [{"lot_tag": f"LT-{n}"}])])
            size6 = len(getattr(sql_tool, "_schema_cache", {}) or {})
            check("the cache is bounded: the oldest tables are dropped", 0 < size6 <= 2, size6)
        finally:
            sql_tool._SCHEMA_CACHE_MAX = saved_max
    finally:
        sql_tool._column_values = scan6
        reset6()

# ===========================================================================
print("\n7. A printed date column with an ISO companion: dates are compared on the companion")
# ===========================================================================
# Invented dates. A day-first printed date whose day starts with a digit above the year's first
# sorts ABOVE every ISO date as text, so a text MAX picks it although it is the earlier date.
BUYS = table("bld_buys", ["item_tag", "bought_on", "bought_on_iso"], [
    {"item_tag": "IT-1", "bought_on": "28/03/2031", "bought_on_iso": "2031-03-28"},
    {"item_tag": "IT-2", "bought_on": "2031-04-02", "bought_on_iso": "2031-04-02"},
    {"item_tag": "IT-3", "bought_on": "06/01/2029", "bought_on_iso": "2029-01-06"},
    {"item_tag": "IT-4", "bought_on": "", "bought_on_iso": ""},
])
p7 = prompt_for([BUYS])
RULES7 = [ln for ln in p7.splitlines() if ln.startswith("- ")]
iso7 = next((ln for ln in RULES7 if "<x>_iso" in ln), "")
iso_rule = getattr(sql_tool, "ISO_DATE_RULE", "")
check("RED: a table with <x> and <x>_iso puts the ISO-date rule in the prompt",
      bool(iso7) and iso7 == iso_rule, iso7)
iso7l = iso7.lower()
check("RED: compare, sort, MIN/MAX and filter dates on <x>_iso",
      all(w in iso7l for w in ("compare", "sort", "min/max", "filter")) and "on <x>_iso" in iso7,
      iso7)
check("RED: never on the printed <x>, because a text sort of day-first dates is wrong",
      "never on the printed <x>" in iso7 and "day-first" in iso7l, iso7)
check("RED: its blank cells are skipped (a blank sorts before every date)",
      "NULLIF(<x>_iso, '')" in iso7, iso7)
check("RED: and the printed <x> is selected beside it, so the answer quotes it as printed",
      "printed <x>" in iso7 and "quote" in iso7l, iso7)
check("the companion column itself is listed like any other column",
      listed(col_line(p7, "bld_buys", "bought_on_iso")) == ["2031-03-28", "2031-04-02",
                                                             "2029-01-06"],
      col_line(p7, "bld_buys", "bought_on_iso"))
check("no pair, no rule: a table with no <x>_iso column gets no ISO-date line",
      "<x>_iso" not in prompt_for([LABELS]))
LONE = table("bld_lone", ["item_tag", "fitted_iso"], [{"item_tag": "IT-9",
                                                       "fitted_iso": "2032-02-03"}])
check("an <x>_iso column with no <x> beside it is no pair: no rule",
      "<x>_iso" not in prompt_for([LONE]))
check("RED: the rule is one bullet, written from shape: no table, no quoted identifier",
      iso_rule.startswith("- ") and "\n" not in iso_rule and "bld_" not in iso_rule
      and "hwu_" not in iso_rule and '"' not in iso_rule, iso_rule)
# Why the rule says each thing, on the fixture: the printed text sorts day-first dates wrong,
# and a blank companion cell is the MIN unless it is skipped.
con7 = duckdb.connect(":memory:")
con7.execute("CREATE TABLE b (bought_on VARCHAR, bought_on_iso VARCHAR)")
con7.executemany("INSERT INTO b VALUES (?, ?)",
                 [(r["bought_on"], r["bought_on_iso"]) for r in BUYS["rows"]])
check("guard (the fixture's own data): a text MAX of the printed column picks the wrong date",
      con7.execute("SELECT MAX(bought_on) FROM b").fetchone()[0] == "28/03/2031")
check("guard: MAX over the companion picks the latest date",
      con7.execute("SELECT MAX(bought_on_iso) FROM b").fetchone()[0] == "2031-04-02")
check("guard: MIN over the companion without NULLIF returns the blank cell",
      con7.execute("SELECT MIN(bought_on_iso) FROM b").fetchone()[0] == ""
      and con7.execute("SELECT MIN(NULLIF(bought_on_iso, '')) FROM b").fetchone()[0]
      == "2029-01-06")
con7.close()

# ===========================================================================
print("\n8. T9a-R0: one value-list path - the level-code column is read like any other")
# ===========================================================================
check("RED: the level-code-only listing cap is gone",
      not hasattr(sql_tool, "LEVEL_CODES_LISTED_MAX"))
SCHEMA_SRC = inspect.getsource(sql_tool.execute_sql_query) + "".join(
    inspect.getsource(getattr(sql_tool, n)) for n in ("_schema_block", "_table_schema_body",
                                                      "_text_column_line")
    if callable(getattr(sql_tool, n, None)))
check("RED: no column name is special-cased in the schema block",
      "is_level_code_column" not in SCHEMA_SRC and "_schema_block" in SCHEMA_SRC)
CODES8 = ["J1", "J2", "J3"] * 200 + ["J4", "JB1", "JR"]
SAME8 = table("bld_storeys", ["unit_tag", "level_code", "shade_word"],
              [{"unit_tag": f"SU-{i:04d}", "level_code": c, "shade_word": c}
               for i, c in enumerate(CODES8)])
p8 = prompt_for([SAME8])
lc8, sw8 = col_line(p8, "bld_storeys", "level_code"), col_line(p8, "bld_storeys", "shade_word")
check("RED: the level-code column lists every code, the late ones included, as complete",
      "possible values (complete): " in lc8
      and listed(lc8) == ["J1", "J2", "J3", "J4", "JB1", "JR"], lc8)
check("RED: an ordinary column holding the same values gets the very same line",
      lc8.replace("level_code", "shade_word", 1) == sw8, (lc8, sw8))

# ===========================================================================
print("\n9. The new prompt text is written from shape")
# ===========================================================================
for name, text in (("the labels rule", labels_rule), ("RED: the cut-value rule", cut_rule),
                   ("RED: the ISO-date rule", iso_rule)):
    check(f"{name} names no table of this project and quotes no identifier",
          bool(text) and "hwu_" not in text and "bld_" not in text and '"' not in text,
          text)
check("the prompt keeps the FORMAT rule's principle beside the labels",
      "Match the FORMAT of the column samples" in p3, "")

# ===========================================================================
print("\n10. T9a-R4: a shared cut form counts every value an ILIKE on its start would match")
# ===========================================================================
# The count is the ILIKE's own: over the WHOLE column, case-insensitive, '_' and '%' in the
# shown start acting as its wildcards. Each count is checked against DuckDB's ILIKE itself.
P10 = "Wuldmoor quay ledger entries"
P10B = "Yelmsby crate spool relay ab"
P10C = "Zarn_bay drum spool cabinets"
P10D = "Quoddle vane sorter carriage"
check("fixture sanity: each shared start is exactly 28 characters",
      [len(p) for p in (P10, P10B, P10C, P10D)] == [28] * 4)
VALUES10 = ([P10 + " for alpha", P10 + " for beta", P10 + " for gamma"]
            + [f"Kp-{n:02d}" for n in range(1, 46)]
            + [P10 + " for omega", P10.upper() + " FOR ZETA"])
LEDGERS = table("bld_ledgers", ["entry"], [{"entry": v} for v in VALUES10])
SPOOLS = table("bld_spools", ["cased", "wild", "whole", "lone"], [
    {"cased": P10B + " north", "wild": P10C + " north", "whole": P10D,
     "lone": "Xophian vellum drum spool assembly"},
    {"cased": P10B.upper() + " SOUTH", "wild": "ZarnXbay drum spool cabinets south",
     "whole": P10D + " left", "lone": "Quoddle"},
    {"cased": "Quoddle", "wild": "Quoddle", "whole": P10D + " right", "lone": "Quoddle"},
])
p10 = prompt_for([LEDGERS, SPOOLS])
entry10 = col_line(p10, "bld_ledgers", "entry")
check("fixture sanity: 50 distinct values - the examples branch",
      "(text; 50 distinct values, examples: " in entry10, entry10)
check("fix round 1: the examples are 8 DISTINCT printed forms in first-appearance order - the "
      "three values sharing the first form print it once",
      listed(entry10) == [P10 + "…"] + [f"Kp-{n:02d}" for n in range(1, 8)], entry10)
check("RED: T9a-R4: the count is over the WHOLE column, case-insensitive: the two late values (one "
      "in capitals) count too", counts(entry10) == {P10 + "…": 5}, entry10)
cased10 = col_line(p10, "bld_spools", "cased")
check("RED: T9a-R4: two forms differing only in case each match both values",
      listed(cased10) == [P10B + "…", P10B.upper() + "…", "Quoddle"]
      and counts(cased10) == {P10B + "…": 2, P10B.upper() + "…": 2}, cased10)
wild10 = col_line(p10, "bld_spools", "wild")
check("RED: T9a-R4: an '_' in the shown start is the ILIKE's any-character - it matches the other "
      "value; that value's own start matches only itself",
      counts(wild10) == {P10C + "…": 2}, wild10)
whole10 = col_line(p10, "bld_spools", "whole")
check("RED: T9a-R4: a value printed whole that the start matches is counted, and itself carries no "
      "count", listed(whole10) == [P10D, P10D + "…"] and counts(whole10) == {P10D + "…": 3},
      whole10)
lone10 = col_line(p10, "bld_spools", "lone")
check("RED: T9a-R4: a cut form standing for one value carries no count",
      counts(lone10) == {} and any(v.endswith("…") for v in listed(lone10) or []), lone10)
con10 = duckdb.connect(":memory:")
agree10, seen10 = [], 0
for tbl in (LEDGERS, SPOOLS):
    for col in tbl["columns"]:
        con10.execute("CREATE OR REPLACE TABLE v (x VARCHAR)")
        con10.executemany("INSERT INTO v VALUES (?)",
                          [[sql_tool._loaded_value(r.get(col), "VARCHAR")] for r in tbl["rows"]])
        line10 = col_line(p10, tbl["table_name"], col)
        for form, n in forms(line10) or []:
            if not form.endswith("…"):
                continue
            seen10 += 1
            duck = con10.execute("SELECT COUNT(DISTINCT x) FROM v WHERE x ILIKE ?",
                                 [form[:-1] + "%"]).fetchone()[0]
            if (n or 1) != duck:
                agree10.append((tbl["table_name"], col, form, n, duck))
con10.close()
check("RED: T9a-R4: every cut form's count is what DuckDB's own ILIKE returns (7 forms checked)",
      seen10 == 7 and agree10 == [], (seen10, agree10))

# ===========================================================================
print("\n11. T9a-R5: a holds that is only the generated column listing is not printed")
# ===========================================================================
LISTING = "`bld_quay.csv` — 3 rows. Columns: quay_tag, berth_no, notes."
LISTING_LONG = ("`bld_reels.csv` — 9 rows. Columns: "
                + ", ".join(f"reel_part_{n:02d}" for n in range(30)) + ".")
LISTING_MORE = ("`bld_berths.csv` — 2 rows. Columns: berth_tag. It carries no quay column - the "
                "quay of each berth is in bld_quay.")
QUAY = table("bld_quay", ["quay_tag"], [{"quay_tag": "QY-1"}])
REELS = table("bld_reels", ["reel_tag"], [{"reel_tag": "RL-1"}])
BERTHS = table("bld_berths", ["berth_tag"], [{"berth_tag": "BT-1"}])
CARDS11 = [table_card("bld_quay", LISTING), table_card("bld_reels", LISTING_LONG),
           table_card("bld_berths", LISTING_MORE)]
p11 = prompt_for([QUAY, REELS, BERTHS], CARDS11, routed=["bld_quay", "bld_reels", "bld_berths"])
check("fixture sanity: all three are routed and the long listing runs past 400 characters",
      all(section_of(p11, n) for n in ("bld_quay", "bld_reels", "bld_berths"))
      and len(LISTING_LONG) > 400, schema_of(p11)[:300])
check("RED: a holds that is only '`<file>.csv` — N rows. Columns: <list>' prints no Holds line",
      "Holds:" not in section_of(p11, "bld_quay"), section_of(p11, "bld_quay"))
check("RED: ... detected on the whole sentence, before the 400-character cut",
      "Holds:" not in section_of(p11, "bld_reels"), section_of(p11, "bld_reels")[:300])
check("a listing followed by a sentence of its own says more than the columns, and is printed",
      section_of(p11, "bld_berths").splitlines()[1:2] == ["Holds: " + LISTING_MORE],
      section_of(p11, "bld_berths"))
check("T9a-R3: the SOURCE line the answer writer reads is untouched - it still quotes a listing",
      LISTING in sql_tool._source_lines('SELECT 1 FROM "bld_quay"', [QUAY], CARDS11))

# ===========================================================================
print("\n12. Values are printed as the loaded table holds them, never trimmed (fix round 1)")
# ===========================================================================
ROWS12 = [{"shelf": "Zarn shelf ", "bay": " Yelm bay"},
          {"shelf": "Zarn shelf ", "bay": "Yelm bay"},
          {"shelf": "Zarn shelf ", "bay": "   "},
          {"shelf": "", "bay": ""}]
SHELVES = table("bld_shelves", ["shelf", "bay"], ROWS12)
p12 = prompt_for([SHELVES])
shelf12, bay12 = col_line(p12, "bld_shelves", "shelf"), col_line(p12, "bld_shelves", "bay")
check("RED: a value whose every cell ends in a space is printed with that space",
      listed(shelf12) == ["Zarn shelf "], shelf12)
check("RED: a leading space is kept too - with and without it they are two values; blank and "
      "space-only cells are no value", listed(bay12) == [" Yelm bay", "Yelm bay"], bay12)
con12 = duckdb.connect(":memory:")
con12.execute("CREATE TABLE s (shelf VARCHAR)")
con12.executemany("INSERT INTO s VALUES (?)",
                  [[sql_tool._loaded_value(r["shelf"], "VARCHAR")] for r in ROWS12])
printed12 = (listed(shelf12) or [""])[0]
check("RED: the printed value, filtered with =, matches every row it stands for",
      con12.execute("SELECT COUNT(*) FROM s WHERE shelf = ?", [printed12]).fetchone()[0] == 3,
      repr(printed12))
con12.close()

# ===========================================================================
print("\n13. The cache key reads each cell under its own column (fix round 1)")
# ===========================================================================
# The same cells in another key order, with the values swapped between the two columns, read
# the same in row order: a key over the rows' values in order would serve the first table's
# block for the second.
if callable(getattr(sql_tool, "_reset_schema_cache", None)):
    sql_tool._reset_schema_cache()
SWAP_A = table("bld_swap", ["near_tag", "far_tag"],
               [{"near_tag": "Zarn", "far_tag": "Yelm"} for _ in range(3)])
SWAP_B = table("bld_swap", ["near_tag", "far_tag"],
               [{"far_tag": "Zarn", "near_tag": "Yelm"} for _ in range(3)])
near_a = col_line(prompt_for([SWAP_A]), "bld_swap", "near_tag")
near_b = col_line(prompt_for([SWAP_B]), "bld_swap", "near_tag")
check("fixture sanity: the first table lists its own value", listed(near_a) == ["Zarn"], near_a)
check("RED: values swapped between columns under another key order are a different table - "
      "never the cached block", listed(near_b) == ["Yelm"], near_b)

# ===========================================================================
print("\n14. A SOURCE line says when its table's own count is the WHOLE table, not the result "
     "(wave 5)")
# ===========================================================================
# Measured on the goal-function run of 2026-09-30: a card's `holds` sentence states the table's WHOLE size
# ("940 widget row(s)..."), printed directly under the RESULT's own much smaller filtered row
# count - and an answer-writer sometimes quoted the whole-table number instead. `_source_lines`
# takes the query's own result size as a 4th, OPTIONAL argument (every existing 3-arg call, like
# section 5's, is untouched) and appends one clause to a table's OWN line only when its `holds`
# sentence's leading count is at least double the result AND at least 10 rows larger.
BIG = table("bld_crates", ["crate_tag"], [{"crate_tag": "CR-1"}])
SMALL = table("bld_pallets", ["pallet_tag"], [{"pallet_tag": "PL-1"}])
HOLDS_BIG = "940 crate row(s) across 4 kind(s) (nuts: 500, bolts: 300, washers: 140)."
HOLDS_PREFIXLESS = "One row per pallet on the quay."
CARDS14 = [table_card("bld_crates", HOLDS_BIG), table_card("bld_pallets", HOLDS_PREFIXLESS)]
SQL14 = 'SELECT 1 FROM "bld_crates"'
CLAUSE_SMALL = " (this describes the WHOLE table; your result above has 50 row(s))."

check("fixture sanity: with no result_rows argument, the SOURCE line is exactly as section 5 "
      "already pins it - the 4th argument is optional",
      sql_tool._source_lines(SQL14, [BIG], CARDS14) == f"SOURCE - bld_crates: {HOLDS_BIG}")
check("RED: a result far smaller than the table's own stated count gets the clarifying clause",
      sql_tool._source_lines(SQL14, [BIG], CARDS14, 50)
      == f"SOURCE - bld_crates: {HOLDS_BIG}{CLAUSE_SMALL}",
      sql_tool._source_lines(SQL14, [BIG], CARDS14, 50))
check("RED: explicitly passing None is the same as omitting it",
      sql_tool._source_lines(SQL14, [BIG], CARDS14, None)
      == sql_tool._source_lines(SQL14, [BIG], CARDS14))

check("not materially larger (480 of 940, under 2x): no clause",
      sql_tool._source_lines(SQL14, [BIG], CARDS14, 480) == f"SOURCE - bld_crates: {HOLDS_BIG}")
RATIO_ONLY = [table_card("bld_crates", "10 crate row(s) across 2 kind(s).")]
check("ratio satisfied (10 >= 2x4) but under 10 rows apart: no clause",
      sql_tool._source_lines(SQL14, [BIG], RATIO_ONLY, 4)
      == "SOURCE - bld_crates: 10 crate row(s) across 2 kind(s).")
DIFF_ONLY = [table_card("bld_crates", "30 crate row(s) across 2 kind(s).")]
check("over 10 rows apart (30 vs 19) but under the 2x ratio: no clause",
      sql_tool._source_lines(SQL14, [BIG], DIFF_ONLY, 19)
      == "SOURCE - bld_crates: 30 crate row(s) across 2 kind(s).")
check("RED: a holds sentence with no leading count never gets the clause, however small the "
      "result is", sql_tool._source_lines('SELECT 1 FROM "bld_pallets"', [SMALL], CARDS14, 1)
      == f"SOURCE - bld_pallets: {HOLDS_PREFIXLESS}")
check("a result row count of 0 never fires it (the empty-result path never reaches this line "
      "in production, and this must not crash if it ever did)",
      sql_tool._source_lines(SQL14, [BIG], CARDS14, 0) == f"SOURCE - bld_crates: {HOLDS_BIG}")

both_sql = 'SELECT 1 FROM "bld_crates", "bld_pallets"'
both = sql_tool._source_lines(both_sql, [BIG, SMALL], CARDS14, 50)
both_lines = both.splitlines()
check("RED: with two SOURCE lines, only the one that qualifies gets the clause",
      both_lines == [f"SOURCE - bld_crates: {HOLDS_BIG}{CLAUSE_SMALL}",
                     f"SOURCE - bld_pallets: {HOLDS_PREFIXLESS}"], both_lines)

# Integration: the real call site inside execute_sql_query passes the query's OWN result size,
# not a guess - using the same lightweight end-to-end harness the sibling SQL-line test files use
# (a fake writer returning fixed SQL, no model, no network).
class _Exec14:
    def __init__(self, data):
        self.data = data


class _Supabase14:
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
                return _Exec14(list(tables))

        return _Q()


class _Models14:
    sql = ""

    def generate_content(self, *, model, contents, config=None):
        return type("_R", (), {"text": _Models14.sql, "usage_metadata": None})()


class _Client14:
    def __init__(self, *a, **kw):
        self.models = _Models14()


def run14(sql, tables, cards):
    saved = (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
             sql_tool._load_table_cards)
    sql_tool.genai.Client = _Client14
    sql_tool.get_llm_api_key = lambda: "fake-key"
    sql_tool.get_llm_model = lambda: "fake-model"
    sql_tool._load_table_cards = lambda *a, **k: list(cards)
    _Models14.sql = sql
    try:
        return sql_tool.execute_sql_query("list every crate", "u-1", _Supabase14(list(tables)))
    finally:
        (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
         sql_tool._load_table_cards) = saved


MANY_ROWS = [{"crate_tag": f"CR-{i}"} for i in range(80)]
CRATES14 = table("bld_crates", ["crate_tag"], MANY_ROWS)
out14 = run14('SELECT crate_tag FROM "bld_crates" LIMIT 4', [CRATES14], CARDS14)
check("RED: execute_sql_query's own SOURCE line carries the clause, sized to the REAL result "
      "(4 rows), not the card's 940",
      f"{CLAUSE_SMALL.replace('50', '4')}" in out14, out14[-400:])

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED'}")
sys.exit(1 if FAILS else 0)
