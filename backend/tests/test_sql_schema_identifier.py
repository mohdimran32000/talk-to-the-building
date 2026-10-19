"""test_sql_schema_identifier.py - the SQL writer's schema block marks each table's identifier
column, so LIST_IDENTIFIER_RULE's "the column the schema marks as the identifier" points at
something (task W13-A2, 2026-10-18).

THE DEFECT. LIST_IDENTIFIER_RULE (sql_tool.py) tells the SQL writer, when listing entities, to
select "the column the schema marks as the identifier ... FIRST" - but the schema block the
writer actually reads (`_schema_block`, built from each table's heading, its routed card's
`holds` sentence, then `_cached_table_body`) never marked any column as the identifier. Every
routed table's card already carries `identifier_column` (doc-prep's own card field); nothing
threaded it into the schema text, so the first half of the rule pointed at nothing. Paid runs
showed a list query on a folded per-room table selecting description and quantity but not the
per-unit tag column the card names as its identifier.

THE FIX. `_schema_block` takes a third dict, `identifier_by_table` ({table: column name} - the
same shape and the same routed-only scoping as the existing `holds_by_table`), and a new helper
`_routed_identifiers` builds it from the routed cards exactly as `_routed_holds` already builds
`holds_by_table`. For a table whose declared identifier IS one of the LOADED table's own columns
(resolved with the same `_column_named` helper `_row_key_column` already uses for this exact
purpose), one line prints right after the heading/Holds line: 'Identifier: <column>' - the
loaded table's own spelling, never the card's. No line when the card names none, names a column
the loaded table lacks, or there is no card for that table (unrouted fallback: same as holds).
The line is never part of `_cached_table_body` - that cache is keyed by a table's own content and
shared across every question, while which column counts as "the identifier" rides on the routed
card, not the table's content, and must never pollute or fork that cache.

LIST_IDENTIFIER_RULE's own text is UNCHANGED: "the column the schema marks as the identifier"
already reads correctly the moment the schema prints a line literally headed 'Identifier:' - no
word needed to change.

Every fixture is invented (bld_* tables, nonsense values). Every check marked RED fails against
sql_tool.py as it stood before this change (commit 2b04a1e) - calling `_schema_block` with a
third argument raises TypeError there; `block()` below catches that so one missing feature
doesn't crash every other check in the file.

Run:
    venv/Scripts/python -X utf8 tests/test_sql_schema_identifier.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import sql_tool  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


def table(name, columns, rows):
    return {"table_name": name, "columns": list(columns), "rows": rows, "row_count": len(rows)}


def block(tables, holds=None, idents=None):
    """`_schema_block`, tolerating the pre-fix two-argument signature: a TypeError (the
    identifier parameter does not exist yet) is reported as a normal failed check instead of
    crashing the whole file, so a RED run against the old code still prints every other check."""
    try:
        return sql_tool._schema_block(tables, holds, idents)
    except TypeError as e:
        return None, str(e)


def section_of(text, name):
    """One table's part of the schema block: its heading line to the next heading (or the end).
    Mirrors test_sql_schema_block.py's own `section_of`."""
    start = text.find(f"\nTable: {name} (")
    if start < 0:
        return ""
    end = text.find("\nTable: ", start + 1)
    return text[start + 1:end if end > 0 else len(text)]


UNITS = table("bld_units", ["unit_tag", "glaze"], [{"unit_tag": "UT-1", "glaze": "Zarn"}])
BINS = table("bld_bins", ["bin_ref", "qty"], [{"bin_ref": "BN-1", "qty": "4"}])

# ===========================================================================
print("1. The line appears for a carded table with a loaded identifier column")
# ===========================================================================
sql_tool._reset_schema_cache()
text1, _ = block([UNITS], {"bld_units": "one row per unit"}, {"bld_units": "unit_tag"})
check("fixture sanity: the schema block built", text1 is not None, text1)
check("RED: an Identifier line names the carded, loaded column",
      text1 is not None and "Identifier: unit_tag" in text1, text1)
check("RED: it sits right after the Holds line, before the column list",
      text1 is not None
      and "\nHolds: one row per unit\nIdentifier: unit_tag\nColumns:\n" in text1, text1)

# ===========================================================================
print("\n2. Absent: no card for the table at all (identifier_by_table has no entry, or is None)")
# ===========================================================================
text2, _ = block([UNITS], {"bld_units": "one row per unit"}, {})
check("RED: an untouched table (no identifier entry) carries no Identifier line",
      text2 is not None and "Identifier:" not in text2, text2)
text2b, _ = block([UNITS], {"bld_units": "one row per unit"}, None)
check("RED: identifier_by_table=None (the default) behaves the same as no card",
      text2b is not None and "Identifier:" not in text2b, text2b)

# ===========================================================================
print("\n3. Absent: the card names no identifier (None or blank)")
# ===========================================================================
text3, _ = block([UNITS], {}, {"bld_units": None})
check("a card entry of None -> no Identifier line",
      text3 is not None and "Identifier:" not in text3, text3)
text3b, _ = block([UNITS], {}, {"bld_units": ""})
check("a card entry of '' -> no Identifier line",
      text3b is not None and "Identifier:" not in text3b, text3b)

# ===========================================================================
print("\n4. Absent: the card names a column the loaded table lacks")
# ===========================================================================
text4, _ = block([UNITS], {}, {"bld_units": "no_such_column"})
check("RED: a declared identifier absent from the loaded table prints no line",
      text4 is not None and "Identifier:" not in text4, text4)
check("RED: and the rest of the block is untouched - the columns still list normally",
      text4 is not None and "unit_tag (text" in text4, text4)

# ===========================================================================
print("\n5. Case-insensitive match, printed as the LOADED table spells it")
# ===========================================================================
text5, _ = block([UNITS], {}, {"bld_units": "UNIT_TAG"})
check("RED: a card spelled in another case still resolves, printed as the table holds it",
      text5 is not None and "Identifier: unit_tag" in text5
      and "Identifier: UNIT_TAG" not in text5, text5)

# ===========================================================================
print("\n6. Placement holds with no Holds line too - straight after the heading")
# ===========================================================================
text6, _ = block([UNITS], {}, {"bld_units": "unit_tag"})
check("RED: with no Holds line the Identifier line follows the heading directly",
      text6 is not None
      and bool(re.search(r"\nTable: bld_units \(1 rows\)\nIdentifier: unit_tag\nColumns:\n",
                         text6)),
      text6)

# ===========================================================================
print("\n7. Two tables, two marks - each in its own section, never swapped")
# ===========================================================================
text7, _ = block([UNITS, BINS], {}, {"bld_units": "unit_tag", "bld_bins": "bin_ref"})
sec_units = section_of(text7, "bld_units") if text7 else ""
sec_bins = section_of(text7, "bld_bins") if text7 else ""
check("RED: bld_units' own section carries its identifier, once",
      sec_units.count("Identifier: unit_tag") == 1 and "Identifier: bin_ref" not in sec_units,
      sec_units)
check("RED: bld_bins' own section carries its identifier, once",
      sec_bins.count("Identifier: bin_ref") == 1 and "Identifier: unit_tag" not in sec_bins,
      sec_bins)

# ===========================================================================
print("\n8. One table carded, the other not - only the carded one is marked")
# ===========================================================================
text8, _ = block([UNITS, BINS], {}, {"bld_units": "unit_tag"})
check("RED: the uncarded table gets no line while its neighbour does",
      text8 is not None and "Identifier: unit_tag" in text8
      and "Identifier:" not in section_of(text8, "bld_bins"), text8)

# ===========================================================================
print("\n9. The cached body is byte-identical with and without identifiers - cache not polluted")
# ===========================================================================
sql_tool._reset_schema_cache()
plain, _ = block([UNITS], {})
with_id, _ = block([UNITS], {}, {"bld_units": "unit_tag"})
check("fixture sanity: both calls built", plain is not None and with_id is not None,
      (plain, with_id))
if plain is not None and with_id is not None:
    plain_body = plain.split("(1 rows)\n", 1)[1]
    with_id_body = with_id.split("(1 rows)\n", 1)[1].replace("Identifier: unit_tag\n", "", 1)
    check("RED: the column body text itself never differs because of the identifier line",
          plain_body == with_id_body, (plain_body, with_id_body))
check("RED: the schema cache holds exactly one entry for bld_units - its body was computed "
      "once and reused, never re-keyed by which identifier was asked for",
      len(sql_tool._schema_cache) == 1, list(sql_tool._schema_cache.keys()))

# ===========================================================================
print("\n10. End to end: execute_sql_query's own caller wires the routed card's "
      "identifier_column into the real prompt, scoped to routed tables only (like holds)")
# ===========================================================================
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
                return _Exec(list(tables))

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


def prompt_for(tables, cards, routed, question="list every unit"):
    """The SQL-writer prompt built by the real `execute_sql_query`, routing replayed exactly as
    the router would select it. Mirrors test_sql_schema_block.py's own `prompt_for`."""
    saved = (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
             sql_tool._load_table_cards, sql_tool._routed_table_names)
    sql_tool.genai.Client = _Client
    sql_tool.get_llm_api_key = lambda: "fake-key"
    sql_tool.get_llm_model = lambda: "fake-model"
    sql_tool._load_table_cards = lambda *a, **k: list(cards)
    sql_tool._routed_table_names = lambda *a, **k: list(routed)
    _Models.prompts = []
    try:
        sql_tool.execute_sql_query(question, "u-1", _Supabase(list(tables)))
    finally:
        (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
         sql_tool._load_table_cards, sql_tool._routed_table_names) = saved
    return _Models.prompts[0] if _Models.prompts else ""


CARDS10 = [{"table": "bld_units", "holds": "one row per unit", "identifier_column": "unit_tag",
            # identifier_pinned=True (task W13-A3): this fixture exercises the POSITIVE path
            # (a routed card's identifier reaching the real prompt), so from here on it must be
            # a card doc-prep's config actually PINNED, not merely one with a non-empty
            # identifier_column - see section 11 for the negative cases this change requires.
            "identifier_pinned": True},
           {"table": "bld_bins", "holds": "one row per bin", "identifier_column": "bin_ref",
            "identifier_pinned": True}]
p10 = prompt_for([UNITS, BINS], CARDS10, routed=["bld_units"])
check("fixture sanity: the prompt was built", bool(p10), p10[:200])
check("RED: the routed table's own card identifier reaches the real prompt",
      "Identifier: unit_tag" in p10, p10)
check("RED: an unrouted table's card contributes no identifier line - the router narrowed "
      "the schema block to bld_units alone, so bld_bins has no section at all",
      "Identifier: bin_ref" not in p10 and "Table: bld_bins" not in p10, p10)

p10b = prompt_for([UNITS, BINS], CARDS10, routed=["bld_units", "bld_bins"])
check("RED: routing both tables in marks both, each once",
      p10b.count("Identifier: unit_tag") == 1 and p10b.count("Identifier: bin_ref") == 1, p10b)

NO_ID_CARDS = [{"table": "bld_units", "holds": "one row per unit"}]
p10c = prompt_for([UNITS], NO_ID_CARDS, routed=["bld_units"])
check("a card with no identifier_column key at all still builds a clean prompt, unmarked",
      bool(p10c) and "Identifier:" not in p10c, p10c)

# ===========================================================================
print("\n11. identifier_pinned gates the mark (task W13-A3, 2026-10-18): only a card "
      "DECLARED-pinned by doc-prep's own config reaches the schema's Identifier line")
# ===========================================================================
# THE DEFECT task W13-A2 fixed marked EVERY routed card's identifier_column - but doc-prep's
# own `_identifier_column` picks most cards' value by a "most distinct column" GUESS (room
# areas, product-spec values, ip addresses, descriptions...), not a decision: only a column
# named in doc-prep's IDENTIFIER_COLUMN_BY_TABLE is a deliberate pin, and the new
# `identifier_pinned` card field says which is which. `_routed_identifiers` now keeps a card
# only when that field is True; a card that lacks the field entirely (an older card set, from
# before it existed) is treated exactly like an unpinned one - no line, the behaviour before
# W13-A2 ever shipped.

# Direct unit check of _routed_identifiers itself, bypassing the full prompt pipeline.
PINNED11 = {"table": "bld_units", "identifier_column": "unit_tag", "identifier_pinned": True}
UNPINNED11 = {"table": "bld_bins", "identifier_column": "bin_ref", "identifier_pinned": False}
NO_FIELD11 = {"table": "bld_crates", "identifier_column": "crate_tag"}  # older card, no key
got11 = sql_tool._routed_identifiers([PINNED11, UNPINNED11, NO_FIELD11],
                                     {"bld_units", "bld_bins", "bld_crates"})
check("a PINNED card (identifier_pinned=True) is kept",
      got11.get("bld_units") == "unit_tag", got11)
check("an UNPINNED card with a real identifier_column (identifier_pinned=False) is dropped",
      "bld_bins" not in got11, got11)
check("a card with NO identifier_pinned key at all (an older card set) is dropped too",
      "bld_crates" not in got11, got11)
check("...and nothing else leaked in: exactly one table reaches the dict",
      got11 == {"bld_units": "unit_tag"}, got11)

# End to end, through the real execute_sql_query wiring, like section 10.
CRATES11 = table("bld_crates", ["crate_tag", "qty"], [{"crate_tag": "CR-1", "qty": "9"}])
CARDS11 = [
    {"table": "bld_units", "holds": "one row per unit", "identifier_column": "unit_tag",
     "identifier_pinned": True},
    {"table": "bld_bins", "holds": "one row per bin", "identifier_column": "bin_ref",
     "identifier_pinned": False},
    {"table": "bld_crates", "holds": "one row per crate", "identifier_column": "crate_tag"},
]
p11 = prompt_for([UNITS, BINS, CRATES11], CARDS11,
                 routed=["bld_units", "bld_bins", "bld_crates"])
check("fixture sanity: the prompt was built", bool(p11), p11[:200])
check("pinned card -> line: bld_units keeps its Identifier line in the real prompt",
      "Identifier: unit_tag" in p11, p11)
check("unpinned card with an identifier -> no line: bld_bins' declared identifier_column is "
      "real but identifier_pinned=False, so no line",
      "Identifier: bin_ref" not in p11, p11)
check("card without the field -> no line: bld_crates has identifier_column but no "
      "identifier_pinned key at all (an older card set), so no line either",
      "Identifier: crate_tag" not in p11, p11)
check("...and all three tables are still in the prompt, routed and loaded normally",
      "Table: bld_units" in p11 and "Table: bld_bins" in p11 and "Table: bld_crates" in p11,
      p11)

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED'}")
sys.exit(1 if FAILS else 0)
