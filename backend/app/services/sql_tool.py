"""
Text-to-SQL tool: generates and executes SQL against user's structured data using DuckDB.
"""
import json
import logging
import os
import time
import re
import threading
import unicodedata
from collections import OrderedDict, namedtuple
from pathlib import Path

import duckdb
from google import genai
from google.genai import types as genai_types
from langsmith import traceable

from app.services.settings import get_llm_api_key, get_llm_model
from app.services.sql_loop import (LITERAL_ELSEWHERE_LEAD, PREVIOUS_SQL_MARKER, VALUES_LEAD,
                                   _declare_tables, _filtered_columns, _is_name,
                                   _literal_condition, _opens_condition, _table_ref,
                                   result_is_empty, sql_is_aggregate, sql_token_spans)
from app.services.table_router import select_tables

logger = logging.getLogger(__name__)

SQL_QUERY_TIMEOUT = 5  # seconds

# ---------------------------------------------------------------------------
# THE DEPENDENCY-GRAPH RULE — one domain rule, in the same voice as the ~20
# already in the prompt, kept as a named constant purely so a test can prove it
# reaches the prompt (test the routing, not just the detection —
# doc-prep/CLAUDE.md §12); the text itself is interpolated verbatim like every
# other rule.
#
# WHAT IT DESCRIBES, and why it cannot be left to the schema block. The schema
# block already shows this table's columns and its enumerated predicate values,
# so a model can SEE the graph — what it cannot see is that `subject_id` and
# `object_id` are POLYMORPHIC (the type column decides which table an id
# resolves to), that a dependency question must be asked in BOTH directions, or
# that a link with no evidence column selected is half a fact. All three are
# properties of the shape, not of this building, so the rule is written from the
# COLUMN SHAPE and names no table: a second project's graph, under any name,
# gets the same rule as soon as its card carries subject/predicate/object.
#
# Kind -> table is deliberately given as a METHOD ("the kind names the table
# family; resolve it against the tables listed above") rather than a hard-coded
# map. A hard-coded map is a per-building fact and would be wrong for the next
# project, and the id kinds are already enumerated in the schema block's own
# `possible values` for the type columns.
DEPENDENCY_GRAPH_RULE = (
    "- A table whose rows are a SUBJECT id, a PREDICATE and an OBJECT id (columns like "
    "subject_id/predicate/object_id, with subject_type/object_type naming each end's KIND) is the "
    "corpus's DEPENDENCY GRAPH: one row is one evidence-backed link between two things in the "
    "building, and it is ONE graph for every system — never a table per system. Use it for any "
    "question about what something depends on, what depends on it, or what changing/replacing/"
    "adding/removing/relocating it would affect. THE TWO ID COLUMNS ARE POLYMORPHIC: read the "
    "type column first — it names the KIND of thing the id is (a panel/board, a circuit, a "
    "location/room, a door, a controller, a piece of equipment, an FCU, a table, or plain printed "
    "text), and the kind says which of the tables listed above that id joins to and on which "
    "column; kinds that are plain printed text join nothing and are quoted as printed. WALK IT IN "
    "BOTH DIRECTIONS IN ONE QUERY — the rows whose object_id is the asset are what it depends on "
    "(upstream) and the rows whose subject_id is the asset are what depends on it (downstream): "
    "SELECT * FROM \"<graph table>\" WHERE subject_id = '<id>' OR object_id = '<id>'. Never one "
    "direction alone: half the answer is worse than none. For a second hop, repeat with the ids "
    "the first hop returned: WHERE subject_id IN (SELECT object_id FROM \"<graph table>\" WHERE "
    "subject_id = '<id>') OR object_id IN (...). ALWAYS also SELECT the evidence columns (how the "
    "link was derived, and the source file/page/quote) — a link without its evidence is half a "
    "fact. Match the id EXACTLY as the graph prints it (use ILIKE when the question's spelling may "
    "differ). Two limits: a link row is never a COUNT of assets (one circuit feeding 'Zip Tap' is "
    "one circuit, not one tap — count the register), and the graph proves no negative — no row for "
    "an asset means no document records a link, never that nothing depends on it."
)

# Probe pr-003 (2026-09-17): "How many fan coil units does the building have?" was
# answered SUM(points) FROM the circuits table WHERE load_type ILIKE '%FCU%' = 550. A
# circuit's `points` are the OUTLETS it serves - the load schedule's count of what is
# wired, not the owner's count of what is installed. Equipment counts live in register
# tables: one row per asset (with a qty column) or one row per room and device type
# with a quantity. Generic by shape: no table name is used.
EQUIPMENT_COUNT_RULE = (
    "- COUNTING EQUIPMENT ('how many <equipment type>', 'how many units'): count from a REGISTER-"
    "shaped table - one row per asset (SUM its qty/quantity column, or COUNT rows when qty is "
    "text) or one row per room and device type with a quantity column. NEVER count equipment "
    "from an electrical circuits/load-schedule table: its `points` column is the number of "
    "OUTLETS a circuit serves and its rows are circuits, not the equipment installed - a "
    "load-type filter there answers 'how many circuit points feed X', a different question. "
    "When both a register and a circuits table match the type word, the register is the answer; "
    "if the registers list the type under several rows or systems, SUM them all and keep the "
    "type filter on the description/device column."
)


# ---------------------------------------------------------------------------
# WHAT IS IN A PLACE — Task 8 round 2 (2026-09-18), the ruler's R shape.
#
# Measured on the shipped runs, not reasoned about. Nine of the ten per-room
# cards failed, and the SQL says why:
#   * `location_id = 'L06-B'` — the LEVEL-and-block row, not the room's own id.
#     42 level assets came back and the answer said "the records do not contain
#     information regarding an IDF hub room" (ex-039).
#   * `location_id = 'L02-B' AND display_name ILIKE '%MDF%'` — two filters that
#     name two DIFFERENT rows, so: 0 rows (ex-040).
#   * a per-system O&M supply list with no place column was filtered on its
#     `remarks` instead: 0 rows, four times (ex-038, ex-042, ex-044, ex-045 —
#     the routing half of those is fixed in table_router).
#   * the folded per-room table was routed and a narrower register was used
#     anyway: 14 of 36 units (ex-036), 18 of 23 (ex-041), 12 rows for a room
#     holding 26 units (ex-052).
#   * no query ever returned the place's own unit TOTAL, which is the one group
#     ex-042, ex-044 and ex-045 each failed on after getting everything else
#     right.
# Written from SHAPE — a location-id column, a human-readable place-name column,
# a quantity column — so it names no table and no building.
#
# T11a-R7 (spec-fix3 fix round 1, 2026-10-01): the place-code resolver writes "The place
# <code> is location_id <id> (...)" into the prompt for a code the question prints, and a
# code printed only as the end of a location key is in no name column - so for that place the
# name filter matches nothing. When such a line is present the stated id REPLACES the name
# filter; "you cannot know a room's id" and the never-also-equate clause now say so.
ROOM_CONTENTS_RULE = (
    "- WHAT IS IN A PLACE ('what assets are in X', 'what is installed in <room>', 'what else is "
    "in that room'): answer from the table that already carries ONE ROW PER PLACE AND ITEM — it "
    "has BOTH a location-id column and a human-readable place-name column (a display/room-name "
    "column printing things like '<number> <Room Name> <Block>') — in preference to any "
    "per-system asset register, which carries no place column at all and can only be searched "
    "in its free text. FILTER ON THE PLACE-NAME COLUMN, with ILIKE on the place exactly as the "
    "question prints it (e.g. <place-name column> ILIKE '%<the name in the question>%') — "
    "UNLESS THIS PROMPT STATES the place's id in a line 'The place <code> is location_id <id> "
    "(...)': then filter <location-id column> = '<that id>' INSTEAD OF THE NAME, because that "
    "id was resolved in code and a printed code is often in no name column. NEVER also equate "
    "the location-id column ALONGSIDE the name filter in the same WHERE: a level-and-block id "
    "is a DIFFERENT row from that room's own id, so the two together return nothing, and the "
    "level-and-block id on its own answers about the whole level instead of the room. Do not "
    "build a location id out of a floor and a block — you cannot know a room's id unless such "
    "a line states it, and the printed name is what the question gave you. SELECT the "
    "place-name column, the "
    "system/category column, the item/description column and the quantity column, and ORDER BY "
    "the place-name column then the system so each place's items stay together. ALSO RETURN THE "
    "TOTALS in the same query — the total quantity for the place and the quantity per system "
    "— as EXTRA COLUMNS on every row, with window functions (e.g. SUM(<quantity>) OVER () "
    "AS place_total, SUM(<quantity>) OVER (PARTITION BY <system column>) AS system_total), and "
    "never as an extra UNION ALL row: a set operation needs both arms to project identical "
    "column lists, which is where an arm padded with NULLs comes from. Return them, "
    "because 'what is in this place' is answered by the list AND its count, and a list with no "
    "total makes the answer invent one or count rows instead of units. If the printed name "
    "matches more than one place, return them all, each with its place-name column, so the "
    "answer can say which is which rather than merging them."
)

# ---------------------------------------------------------------------------
# A TWO-PART QUESTION ABOUT ONE NAMED THING — Task 8 round 2, the ruler's J shape.
#
# Measured, every one from the SQL the app wrote:
#   * ex-046 asked "which network room and which switch" and the query selected
#     two columns of a row that also prints the switch model and the VMS room;
#     the answer then said "the provided records do not contain information
#     regarding the specific switch". ex-048 the same, for the UPS room.
#   * ex-047 invented `subject`/`subject_id` on that table to self-join with —
#     columns that are not on it — and the query died with a Binder Error.
#   * ex-049 counted a board's circuits in the FEEDER schedule (0) instead of
#     the table whose rows ARE circuits (42).
#   * ex-050, ex-051 and ex-055 returned the neighbours and never the thing's
#     own printed rating: 143.6 / 192.63 / 1445.45 kW, each the missing group.
# Written from SHAPE — a row with an id column, a table whose rows are the
# children — so it names no table and no building.
#
# ONE CLAUSE WITHDRAWN, 2026-09-28 (the owner's ruling on his own room-1.29
# trace). This rule used to end: "(UNION ALL one labelled block per part is
# fine, with a constant column naming which part each block answers)". Read
# literally against two tables of DIFFERENT WIDTHS, that is the query the writer
# produced - and, having to pad the narrow arm to a width it could not know, it
# emitted `NULL,` until the 8,192-token output cap stopped it (8,188 output
# tokens), for a Binder Error and an answer taken from a stray document chunk.
# TWO_PART_RULE below now says the opposite, and two rules contradicting each
# other in one prompt is the wrong end state, so the clause is withdrawn rather
# than left to be outvoted. Nothing is lost: the JOIN it offered alongside is
# kept and is now the only answer on offer. The same construction was withdrawn
# from ROOM_CONTENTS_RULE above on the same day, where the totals now come from
# window functions. `tests/test_room_and_hop_rules.py` section 7 sweeps every
# rule the model is sent and proves that no UNION in the prompt is anything but
# a prohibition.
TWO_HOP_RULE = (
    "- A QUESTION IN TWO PARTS ABOUT ONE NAMED THING ('which network room AND which switch', "
    "'what does X feed AND what feeds X', 'which room does it control AND what else is in that "
    "room', '… and how many circuits does it have'): START FROM THAT THING'S OWN ROW AND SELECT "
    "EVERY COLUMN OF IT (SELECT * FROM \"<table>\" WHERE <its id column> = '<id>'), never two or "
    "three columns picked for the first part only. These tables are wide on purpose: one row "
    "often already carries the whole chain — the room, the equipment model, the units it is "
    "backed up by, its class — and a narrow SELECT is exactly why an answer goes on to say the "
    "records do not contain a value that was printed on the row it just read. NEVER INVENT A "
    "COLUMN that is not listed above in order to make a join; if no column of that row answers "
    "the second part, join to the table whose rows ARE those things, on the value this row "
    "prints, in the SAME query — a JOIN on that shared key, and never a set operation, "
    "whose arms must project identical column lists. A COUNT of what something HAS comes from the "
    "table whose rows are those things (count the circuit rows in the circuits table), never "
    "from the thing's own row and never from the parent's feeder row, which holds one row per "
    "child board and not one per circuit. And when the question asks about a thing AND its "
    "neighbours, include THE THING'S OWN totals, ratings and notes as well as the neighbour "
    "list: a list of what X feeds does not contain X's own rating, and the answer will say it "
    "is not recorded."
)

# ---------------------------------------------------------------------------
# THE SAME QUESTION, ONE STEP EARLIER - measured on the owner's own question,
# 2026-09-28, and traced in LangSmith.
#
# "what is <place>? and what all assets there inside?" is TWO_HOP_RULE's shape,
# and TWO_HOP_RULE offers "UNION ALL one labelled block per part is fine" as one
# way to answer it. Taken literally against two tables of different widths, that
# is what the writer wrote:
#
#     SELECT * FROM <places> WHERE <number> = '...'
#     UNION ALL SELECT NULL, NULL, NULL, NULL, ...
#
# It cannot work: a set operation needs both arms to project the same number of
# columns, and the second arm was being padded out to a width the writer had to
# guess. It guessed by emitting `NULL,` until the 8,192-token output cap stopped
# it - 8,188 output tokens. DuckDB refused it ("Set operations can only apply to
# expressions with the same number of result columns"), the repair call produced
# the same shape, and the question reached the document index, which answered
# "1 Daylight Sensor" off one stray chunk. The true answer was 14 units across 8
# items, sitting in a table that shares a key with the entity's own row.
#
# Two other guards catch this after the fact (`_sql_looks_runaway` refuses the
# query, `sql_loop.FAILED_SQL` re-queries it). This one stops it being written:
# a JOIN, never a set operation. Written from SHAPE - an entity's own row, a
# table whose rows are its parts, a shared key - so it names no table and no
# building.
TWO_PART_RULE = (
    "- A QUESTION WITH TWO PARTS ABOUT ONE ENTITY ('what is X and what is in it', "
    "'describe X and list its Y') is ONE query: select from the table that holds the "
    "specific part (the list or the count), JOIN the entity's own row on the shared "
    "location/identifier key to bring its name, area, department or other descriptive "
    "columns alongside, and NEVER combine tables with UNION/INTERSECT/EXCEPT - set "
    "operations require identical column lists and are never the answer here."
)

# ---------------------------------------------------------------------------
# LISTING ENTITIES / SPEC-TABLE COMPLETENESS — spec 2026-09-23 item 1, proactive
# (no single eval case number: these guard the two writer habits the plan
# calls IDENTIFIER_MISSING and spec-table under-selection, ahead of the
# verifying loop that will catch them live). Written from SHAPE — an
# identifier-shaped column by name or schema role, a one-row-per-(entity,
# parameter) table shape — so neither names a table, column value or
# building.
LIST_IDENTIFIER_RULE = (
    "- LISTING ENTITIES ('what are the rooms/boards/doors/cameras \u2026', 'list \u2026', "
    "'which \u2026'): ALWAYS SELECT the table's identifier column FIRST \u2014 the column that "
    "names or numbers each row (a room number, a board name, a door id, a camera tag: the column "
    "the schema marks as the identifier or whose name ends in _number/_id/_tag/_name) \u2014 then "
    "the descriptive columns. A list without identifiers cannot be acted on."
)

ALL_PARAMETERS_RULE = (
    "- A table with one row per (entity, parameter, value) is a specification table: when asked "
    "for specs/specifications/details of an entity, return EVERY parameter row for the matching "
    "entities (filter on the entity column only, never on the parameter column unless one "
    "parameter is asked for), so the answer can list all of them."
)

# Measured, not proactive, unlike the two above: the owner asked "what are the rooms on
# the first floor?" on 2026-09-23 and got 50 rows whose room-number cell was EMPTY. The
# identifier column HAD been selected - the rule above worked - and the query ended
# `ORDER BY <that column>`, where an empty string sorts before every real value, so the
# rows shown were exactly the ones with no identifier. Written from SHAPE, like the
# others: no table, column or building is named.
ORDER_BY_BLANKS_RULE = (
    "- ORDER BY on an identifier column must put the rows that have NO identifier LAST, "
    "never first: write ORDER BY NULLIF(<that column>, '') NULLS LAST (a blank cell is an "
    "empty string, which sorts BEFORE every real value, so a plain ORDER BY opens the list "
    "with the unidentified rows and a row limit then shows only those). Keep those rows \u2014 "
    "they belong in the list \u2014 at the end of it."
)

# Measured on the goal-function run of 2026-09-30 (spec-fix6): a table keeping a sheet's
# own printed TOTAL rows beside the item rows they total was summed over both, and the
# answer stated the items plus their own printed total. The result now says so in code
# (`_printed_total_rows_line`); this rule is so the query is written right in the first
# place. Written from SHAPE - a row-kind column whose values include a total word - so no
# table, column or building is named.
PRINTED_TOTAL_ROWS_RULE = (
    "- PRINTED TOTAL ROWS: a table can keep a sheet's own printed total rows beside the item "
    "rows they total, marked by a row-kind/type column whose possible values include 'total', "
    "'totals', 'subtotal' or 'grand total'. A SUM or COUNT of the items must leave those rows "
    "out (<that column> IS DISTINCT FROM '<the total value>'): the figure without the printed "
    "total rows is the answer. The printed total is the document's own figure - when it is "
    "wanted, return it beside the answer in its own column (SUM(CASE WHEN <that column> = "
    "'<the total value>' THEN <quantity> END)) and never add it to the items."
)

# Measured on the goal-function run of 2026-09-30 (spec-fix8): "distribution boards" was
# written as ONE kind code, dropping every board of the other kinds the class covers, and
# "X and Y" came back as one bare sum with no count per kind. The prompt's own counting
# example taught the first - it mapped the class word 'boards' onto a code the question
# never printed - and is reworded to print its code. Written from SHAPE: the class words
# are examples of the shape, and no table, column, code or building is named.
# Review fix round 1: the per-kind count is a SUM of the QUANTITY column whenever the table
# has one - a template of `THEN 1` would count rows, against the counting rule above that
# says one row can be many units.
# Wave 3, F3 (2026-10-03): "whenever the table has one" was followed to the letter. Measured on
# the goal-function run of 2026-10-01: a per-kind count of positions summed the number of
# things fitted to each position - the table's only count-like column - and the answer stated
# those as the positions. A column is summed only when it counts the asked items themselves;
# when each row is one item, the rows are counted. The older counting bullet in the prompt
# says the same.
KIND_WORDS_RULE = (
    "- KIND WORDS: a class word in the question ('boards', 'distribution boards', 'doors', "
    "'cameras') covers EVERY kind code of that class in the table's kind/type column - never "
    "narrow it to one code unless the question prints that code or its qualifier (e.g. "
    "'emergency', 'sub-main'). SELECT the kind column so each row says which kind it is. When "
    "the question names two or more kinds, return one count per kind plus the total - GROUP BY "
    "the kind column, or one SUM(CASE WHEN <that kind> THEN <a column counting the asked items> "
    "ELSE 0 END) per kind. SUM a column only when it counts the asked items themselves - a "
    "qty/quantity column (one row can be many units) or a column named for the asked item; a "
    "count of something fitted to each item (the readers on a door, the buttons on a panel) is "
    "not the items' quantity. When each row is one item, count the rows per kind: COUNT(*) "
    "with GROUP BY, or THEN 1."
)

# Measured on the goal-function run of 2026-09-30 (spec-fix3): a storey was filtered on
# printed floor text that every source spells differently - a code guessed for a table that
# prints no such value, a basement read off a Block letter - a room's display name was
# searched for its level, which it never contains, and a ranking of levels grouped the
# printed floor column, splitting one level in two, before LIMIT 1 hid a tie. The data side
# stamps a level-code column beside every location id (doc-prep 14_spine.py); this rule sends
# the writer to it. T11a-R5: the column does not mean the same thing in every table - a
# table's own printed level code can spell a ground floor as letters - so the rule says to
# filter with the codes the column holds and, when in doubt, to go through the location key
# to the places table's. T11a-R1: storeys and rankings only - a question naming one place
# keeps ROOM_CONTENTS_RULE's name filter. Written from SHAPE: no table, column or code of
# the project is named.
#
# Fix round 1: the prohibition covers only a table that HAS the keys - one with neither keeps
# filtering its own printed floor column, as the loop's EMPTY instruction already does - and
# the per-level roll-up is for COUNTS of items: a total of a quantity that rolls up a
# parent/child tree follows the area-total rule, never a plain per-level SUM.
PLACE_KEYS_RULE = (
    "- FLOORS AND LEVELS: a floor, level, storey, basement or roof the question names is "
    "filtered or grouped on a level-code column - the table's own, or the places table's "
    "(one row per place, keyed by the location id) reached through the location-id column: "
    "<location-id column> IN (SELECT <its key> FROM <places table> WHERE <level-code column> = "
    "'<code>'), or a JOIN on that key. NEVER filter a storey on printed floor text (a floor or "
    "level column written the way each source happened to print it) or on a display or "
    "place-name column when the table has a level-code column or a location-id column: a "
    "room's display name does not contain its level, and every source spells a floor "
    "differently (a table with neither keeps filtering its own printed floor column, in that "
    "column's own format). Filter with the codes the level-code column actually holds - "
    "its value list above - because the same storey can be coded differently in different "
    "tables (a ground floor as a number in one, as letters in another); when in doubt, go "
    "through the location-id column to the places table's level code. A ranking of levels or "
    "rooms ('which floor has the most ...', 'which room holds the most ...') GROUPs BY the "
    "key: for rooms, the location-id column, keeping only the rows whose resolution column = "
    "'room'; for levels, roll up by the level code - for COUNTS of items; a TOTAL of a "
    "quantity that rolls up a parent/child tree follows the area-total rule above (only the "
    "rows whose parent is outside the level), never a plain per-level SUM. Return every group "
    "with its count, "
    "ORDER BY the count DESC - never LIMIT 1, so a tie shows. A level with no matching rows is "
    "itself the answer: say none are recorded there, and never widen the filter to other "
    "levels or codes to find some. This rule is for storeys and rankings only: a question "
    "naming ONE place keeps the what-is-in-a-place rule above (filter by its printed name, or "
    "by its location id when this prompt states it)."
)

# The level-code column's value list - "its value list above", the codes the rule above sends
# the writer to - was first read off every row by a special case for that one column shape
# (T11a-R5). The schema block now reads EVERY column off every row (spec-fix5), so the special
# case is folded into that one path (T9a-R0): a level-code column is listed like any other.


# Measured on the goal-function run of 2026-09-30 (spec-fix5): a latest-date question sorted a
# printed date column that holds two formats - day-first text and ISO dates - as text, so every
# ISO date sorted last and a text MAX picked the wrong date. The data side
# now ships an ISO companion `<x>_iso` beside such a column (doc-prep 15_registers.py, task
# T9d). Like the stamped level code it is on no card, so the schema block, which reads the
# loaded table, is where the writer sees it - and this rule rides with it,
# emitted only when a table in the schema block has a column `<x>_iso` beside a column `<x>`
# (`_has_iso_companion`). Blank companion cells are '' (not NULL) once loaded, so a MIN or an
# ascending sort would return a blank: NULLIF skips them. Written from SHAPE: no table or column
# of the project is named.
ISO_DATE_RULE = (
    "- DATES WITH AN ISO COMPANION: when a table has a column <x>_iso beside a column <x>, "
    "<x>_iso is <x>'s date written yyyy-mm-dd (blank where <x> prints no date). Compare, sort, "
    "MIN/MAX and filter dates on <x>_iso, never on the printed <x> - a text sort of day-first "
    "dates is wrong - and skip its blank cells with NULLIF(<x>_iso, ''); SELECT the printed <x> "
    "beside it, so the answer can quote the date as printed."
)



# ---------------------------------------------------------------------------
# THE RUNAWAY GUARD - measured on the owner's own question, 2026-09-28.
#
# "what is room 1.29? and what all assets there inside?" is two questions about
# one entity. The writer glued them together with a set operation across tables
# of different widths - SELECT * FROM <places> ... UNION ALL SELECT NULL, NULL,
# NULL, ... - and, having to pad the second arm to the first arm's width without
# knowing that width, emitted `NULL,` until the output cap stopped it: 8,188
# output tokens. DuckDB refused it ("Set operations can only apply to expressions
# with the same number of result columns"), the one repair call produced the same
# shape, and the question fell through to document search, which answered from a
# stray chunk.
#
# A query this shape is degenerate BEFORE it runs, by two signals a regex can
# read: it is far longer than any single-line SELECT this prompt asks for, or it
# carries a run of padding NULLs. Both are refused here - no execution, and no
# repair either, because a repair prompt carrying kilobytes of NULLs is the next
# runaway. The refusal is worded as a SQL failure so the investigation loop's
# FAILED path picks it up and re-queries (see `sql_loop.FAILED_SQL`).
#
# Shape only: no table, column or building is named, so a second project gets the
# same guard.
# ---------------------------------------------------------------------------

#: A single-line SELECT longer than this is not a query, it is a generation that
#: ran away. The longest legitimate query this prompt has produced in the eval
#: suite is well under half of it.
MAX_GENERATED_SQL_CHARS = 1500

#: This many `NULL` items in a row - commas and whitespace between them - is
#: padding, not a projection anyone wrote.
MAX_REPEATED_NULLS = 20

_NULL_RUN_RE = re.compile(
    r"(?:\bNULL\b\s*,\s*){%d}\bNULL\b" % (MAX_REPEATED_NULLS - 1), re.IGNORECASE)


def _sql_looks_runaway(sql: str) -> str | None:
    """The reason this generated SQL must not be executed, or None if it is sane.

    Pure and deterministic - no model, no database - so the guard is testable on
    its own (`tests/test_sql_runaway_guard.py`).
    """
    text = sql or ""
    if len(text) > MAX_GENERATED_SQL_CHARS:
        return (f"{len(text)} characters, over the "
                f"{MAX_GENERATED_SQL_CHARS}-character limit for one query")
    if _NULL_RUN_RE.search(text):
        return f"a run of {MAX_REPEATED_NULLS} or more repeated NULL items"
    return None


def _runaway_failure_text(sql: str, reason: str) -> str:
    """The refusal, worded exactly like a real execution failure so every caller
    that already handles one handles this too."""
    snippet = (sql or "")[:300]
    if len(sql or "") > 300:
        snippet += "…"
    return (f"SQL query failed: generated SQL was malformed ({reason})\n\n"
            f"Generated SQL: `{snippet}`")


class _QueryTimeoutError(Exception):
    """Raised when a DuckDB query is aborted for running past SQL_QUERY_TIMEOUT."""


def _execute_with_timeout(con, sql, timeout=SQL_QUERY_TIMEOUT):
    """Run con.execute(sql).fetchall(), aborting it if it runs past `timeout`
    seconds.

    DuckDB has no built-in query-timeout setting; the documented way to cancel
    an in-flight query is con.interrupt(), called from another thread while
    the query thread is blocked inside con.execute(). A threading.Timer does
    exactly that. The timer is always cancelled in `finally` — win or lose —
    so nothing is left running after this function returns: on the happy path
    the timer is cancelled before it would ever fire, and on timeout the timer
    thread has already finished (it only calls interrupt() once and exits).

    con.interrupt() raises duckdb.InterruptException in the executing thread.
    That alone doesn't say WHY the query was interrupted, so a `timed_out`
    flag set inside the timer callback (before calling interrupt) lets us
    tell "our timeout fired" apart from any other reason a query might be
    interrupted, and raise a clear, specific message for the former.
    """
    timed_out = threading.Event()

    def _abort():
        timed_out.set()
        con.interrupt()

    timer = threading.Timer(timeout, _abort)
    timer.daemon = True
    timer.start()
    try:
        return con.execute(sql).fetchall()
    except duckdb.InterruptException:
        if timed_out.is_set():
            raise _QueryTimeoutError(
                f"query exceeded the {timeout}s timeout and was aborted"
            ) from None
        raise
    finally:
        timer.cancel()


# Cards are generated by doc-prep/11_table_cards.py (deterministically, from
# verified manifests) into TWO places: doc-prep/eval/table_cards.json (the
# doc-prep-side artifact its own eval scripts/tests read) and this repo's
# backend/app/data/table_cards.json (committed here, versioned with the code
# that reads it). PRODUCTION READS ONLY THE REPO-INTERNAL COPY BELOW — a
# sibling doc-prep checkout being absent, relocated, or mid-edit must never
# affect this service. `TABLE_CARDS_PATH` overrides the path entirely (tests,
# or a container layout that keeps data elsewhere).
#
# The path is built with two fixed `.parent` hops (this file's own directory,
# whatever that is) — never a deep, layout-dependent `parents[N]` — so it
# cannot raise at import on an unexpected checkout/container layout (a
# previous version used `parents[5]` to reach a doc-prep sibling folder and
# would IndexError on any shallower layout, taking down every import of this
# module before the file was even read).
_DEFAULT_TABLE_CARDS_PATH = Path(__file__).resolve().parent.parent / "data" / "table_cards.json"
_TABLE_CARDS_PATH = Path(os.environ.get("TABLE_CARDS_PATH", str(_DEFAULT_TABLE_CARDS_PATH)))
_table_cards_cache = None
_table_cards_cache_mtime = None
_table_cards_cache_user = None
_table_cards_cache_expires = 0.0
# The cards change only when doc-prep uploads a corpus, so a short TTL is plenty and
# keeps a per-question database round trip off the hot path. Matches settings.py.
_TABLE_CARDS_TTL = 60.0


def _reset_table_cards_cache() -> None:
    """Drop the cache. Exists for the tests, which must not leak state between cases."""
    global _table_cards_cache, _table_cards_cache_mtime
    global _table_cards_cache_user, _table_cards_cache_expires
    _table_cards_cache = None
    _table_cards_cache_mtime = None
    _table_cards_cache_user = None
    _table_cards_cache_expires = 0.0


def _cards_from_file() -> list[dict]:
    """The offline fallback: the cards file that used to be the only source.

    Kept so local development and any deployment without the table still work, and so
    that a database outage degrades to stale-but-useful rather than to nothing.

    Sorted by table name for the same reason the database read is ordered — see
    `_load_table_cards`. The two sources must agree, or a fallback would silently
    re-rank every tied question the moment the database went away.
    """
    try:
        return _ordered(json.loads(_TABLE_CARDS_PATH.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"table_router: could not load {_TABLE_CARDS_PATH} ({e}); "
                       f"falling back to the unrouted schema block")
        return []


def _ordered(cards) -> list[dict]:
    """Cards sorted by table name, tolerating anything that is not a well-formed card.

    `table_router.select_tables` breaks score ties by each card's POSITION in this list
    (`scored.sort(key=lambda t: (-t[0], t[1]))`), so the list's order is part of the
    router's answer. Sorting here is what makes that answer reproducible.
    """
    if not isinstance(cards, list):
        return []
    return sorted((c for c in cards if isinstance(c, dict)),
                  key=lambda c: str(c.get("table", "")))


def _load_table_cards(user_id: str | None = None, supabase_client=None) -> list[dict]:
    """Load and cache the router's table cards for one user.

    SOURCE OF TRUTH IS THE DATABASE (`table_cards`, migration 025), not the repo.
    The cards used to live in `backend/app/data/table_cards.json`, and that had three
    faults, all of them observed on 2026-09-06:

      * the file had never been pushed, so a deploy from git got NO cards and silently
        ran unrouted — the router worked on exactly one machine;
      * the committed copy had drifted four tables behind the live corpus, including a
        167-row camera register, and nothing detected it;
      * this repository is public and the cards carry sample values from a named client
        building.

    The file remains as a FALLBACK, in this order: database -> file -> no cards. Every
    step degrades rather than raising. That is deliberate and load-bearing: `sql_tool`
    treats "no cards" as the documented full-schema behaviour, so a cards problem must
    never be able to take the SQL tool down. The fallback matters most exactly when the
    database is unreachable, which is why the tests cover that case explicitly.

    Cards are scoped per user because what they describe is per user — `execute_sql_query`
    already filters `structured_data` by user_id, and a router that could see another
    tenant's tables would be a leak, not a feature.
    """
    global _table_cards_cache, _table_cards_cache_mtime
    global _table_cards_cache_user, _table_cards_cache_expires

    now = time.time()
    try:
        mtime = _TABLE_CARDS_PATH.stat().st_mtime
    except OSError:
        mtime = None

    fresh = (_table_cards_cache is not None
             and _table_cards_cache_user == user_id
             and now < _table_cards_cache_expires
             and mtime == _table_cards_cache_mtime)
    if fresh:
        return _table_cards_cache

    cards: list[dict] = []
    if user_id and supabase_client is not None:
        try:
            # 🔴 `.order("table_name")` is load-bearing, not tidiness (2026-09-18,
            # task-8-diagnosis.md proof 2). Without it PostgREST returns heap order, and
            # `11_table_cards.py --publish` does delete() + upsert(), so every republish
            # rewrote it. `table_router.select_tables` breaks score ties by a card's
            # position in this list, and the third routed slot is a TEN-WAY tie at 0.1111
            # on the room questions - so republishing the cards, changing not one byte of
            # any card, moved ex-038/ex-044/ex-045 off `hwu_room_assets` and onto
            # `hwu_om_acs_asset_register`, which returns 0 rows. Shuffling the 70 live
            # cards 40 times changes the top-3 on 39 of 70 questions. Ordering the read
            # does not make the tie-break RIGHT (that is a separate, measured change); it
            # makes it the SAME every time, which is the precondition for measuring it.
            res = (supabase_client.table("table_cards")
                   .select("table_name, card")
                   .eq("user_id", user_id)
                   .order("table_name")
                   .execute())
            rows = getattr(res, "data", None) or []
            # A row whose `card` is not an object is skipped rather than fatal: the
            # router's own guard would survive it, but failing here would defeat the
            # point of the fallback chain.
            # Re-sorted rather than trusted: the ORDER BY above is what the database is
            # asked for, and this is what the router is given. A client whose chain
            # ignores `.order` (or a row whose card names a different table) can then
            # never leave the list in an order the next deploy would not reproduce.
            cards = _ordered([r["card"] for r in rows
                              if isinstance(r, dict) and isinstance(r.get("card"), dict)])
            if rows and not cards:
                logger.warning("table_router: table_cards rows present but none parsed "
                               "as card objects; falling back to the local file")
        except Exception as e:
            logger.warning(f"table_router: could not read table_cards from the database "
                           f"({type(e).__name__}: {e}); falling back to the local file")
            cards = []

    if not cards:
        cards = _cards_from_file()

    _table_cards_cache = cards
    _table_cards_cache_mtime = mtime
    _table_cards_cache_user = user_id
    _table_cards_cache_expires = now + _TABLE_CARDS_TTL
    return cards


# A digit string that starts with 0 and is not a decimal ("0552002270", "003003455114",
# "007") - an identifier. "0", "0.5", "0,5" are numbers and do not match.
_LEADING_ZERO_CODE_RE = re.compile(r"^0\d+$")


def _infer_column_types(cols: list, rows: list, sample_limit: int = 200) -> dict:
    """Majority-vote type inference: a column is DOUBLE when >=80% of its
    non-empty sampled values parse as numbers. Tolerates stray text like
    'N/A' or unit notes in otherwise-numeric columns (real-world CSVs)."""
    col_types = {}
    for c in cols:
        numeric = 0
        non_empty = 0
        has_code = False
        for row in rows[:sample_limit]:
            val = row.get(c)
            if val is None or val == "":
                continue
            non_empty += 1
            if isinstance(val, (int, float)):
                numeric += 1
            elif isinstance(val, str):
                if _LEADING_ZERO_CODE_RE.match(val.strip()):
                    # "0552002270", "003003455114": a leading zero on a run of digits
                    # is an identifier (phone, meter, account), never a quantity.
                    # One such value makes the whole column text - as DOUBLE it
                    # would print 552002270.0, a number nobody can dial (2026-09-17).
                    has_code = True
                    continue
                try:
                    float(val.replace(",", ""))
                    numeric += 1
                except ValueError:
                    pass
        if has_code:
            col_types[c] = "VARCHAR"
        else:
            col_types[c] = "DOUBLE" if non_empty and numeric / non_empty >= 0.8 else "VARCHAR"
    return col_types


def _loaded_value(val, col_type: str):
    """One cell exactly as `execute_sql_query` loads it into DuckDB: NULL stays NULL, a
    DOUBLE column reads the number through thousands commas (text that is no number is
    NULL), and any other column holds the text as stored. Shared with
    `column_value_reader`, so the values an EMPTY re-query lists are, by construction, the
    values the loaded table holds."""
    if val is None:
        return None
    if col_type == "DOUBLE":
        try:
            return float(str(val).replace(",", ""))
        except (ValueError, TypeError):
            return None
    return str(val)


# ---------------------------------------------------------------------------
# THE SCHEMA BLOCK - what the SQL writer is told each table holds (spec-fix5, task T9a,
# 2026-10-01).
#
# Measured on the goal-function run of 2026-09-30, over the real tables. A column's values came
# from its first 500 rows only, at most 8 of them, each cut silently at 28 characters, and the
# prompt called a list with no ninth value "the complete set". On the tables longer than 500
# rows that label was false (a register's system column hid the systems whose rows come late; a
# date column hid one of its printed formats); 'complete' lists held values cut mid-word, which
# an exact filter can never match; columns of a couple of dozen values showed 8 of them; a table
# wider than 30 columns showed 3 sample rows x 20 columns, so its notes column was invisible; and
# no card's `holds` sentence - what a table does NOT carry, and where that lives - reached the
# writer. Three failures of that run trace to it.
#
# Now every text column is read off EVERY row (`_column_values`). Up to COMPLETE_VALUES_MAX
# distinct values are all listed, labelled 'possible values (complete)'; a column with more
# prints 'N distinct values, examples:' and the first EXAMPLES_SHOWN values the table holds. A
# value longer than VALUE_DISPLAY_MAX is printed cut and marked CUT_MARK, and the prompt says to
# match a marked value with ILIKE on its start, never '='. A table wider than MAX_COLS_DETAILED
# lists every column name and type before its sample rows, and each routed card's `holds`
# sentence is printed under its table's heading. Numeric columns are unchanged (T9a-R1). The
# level-code column's own whole-column listing (T11a-R5) is this same path now (T9a-R0).
#
# A table is scanned once per upload, not once per question: `_cached_table_body` keys what it
# prints on the table's content (`_table_signature`).
# ---------------------------------------------------------------------------

#: A text column with at most this many distinct non-empty values lists them all, labelled
#: complete; a column with more prints its count and EXAMPLES_SHOWN of them.
COMPLETE_VALUES_MAX = 40
#: How many values a column with more than COMPLETE_VALUES_MAX shows: the first ones the table
#: holds - the count the schema block has always shown.
EXAMPLES_SHOWN = 8
#: A value longer than this is printed as its first VALUE_DISPLAY_MAX characters and CUT_MARK.
VALUE_DISPLAY_MAX = 28
CUT_MARK = "…"
#: A table with more columns than this lists its column names and types on one line and shows
#: sample rows, instead of one line per column.
MAX_COLS_DETAILED = 30
#: The bound on a card's `holds` sentence wherever it is quoted - `_source_lines` cuts it the
#: same way (tests/test_sql_schema_block.py pins the two equal).
HOLDS_MAX_CHARS = 400


def _column_values(rows: list, col: str) -> list:
    """Every distinct value of one text column EXACTLY as the loaded table holds it - the text
    `_loaded_value` loads, never trimmed - in the order the table first holds them, read off
    EVERY row: the first 500 rows of a long table could miss whole systems and still read as
    the complete set (spec-fix5). A blank or space-only cell is no value. Fix round 1: a value
    printed trimmed, for cells that hold it with a trailing space, matched no row with '='; repr
    shows such a space."""
    seen = {}
    for row in rows:
        value = _loaded_value(row.get(col), "VARCHAR")
        if value is not None and value.strip() and value not in seen:
            seen[value] = None
    return list(seen)


def _ilike_start_count(start: str, lowered: list) -> int:
    """How many of the column's distinct values (`lowered`, each lower-cased) DuckDB's
    `value ILIKE '<start>%'` matches: both sides lower-cased (DuckDB lower-cases, it does not
    case-fold), and in `start` a '%' is any run of characters and an '_' any one character, as
    in the ILIKE itself; DuckDB's LIKE has no default escape character."""
    start = start.lower()
    if "%" not in start and "_" not in start:
        return sum(1 for value in lowered if value.startswith(start))
    pattern = re.compile("".join(".*" if ch == "%" else "." if ch == "_" else re.escape(ch)
                                 for ch in start), re.DOTALL)
    return sum(1 for value in lowered if pattern.match(value))


def _display_value(value: str) -> str:
    """A value as the schema block prints it: whole when it is at most VALUE_DISPLAY_MAX
    characters, else its first VALUE_DISPLAY_MAX and CUT_MARK - so a value the writer sees is
    either one the column holds or visibly the start of one, never a silent fragment that an
    exact filter cannot match."""
    if len(value) <= VALUE_DISPLAY_MAX:
        return value
    return value[:VALUE_DISPLAY_MAX].rstrip() + CUT_MARK


def _text_column_line(col: str, values: list) -> str:
    """One text column's schema line, from ALL its distinct values (`_column_values`): every
    one when there are at most COMPLETE_VALUES_MAX, labelled complete, else their count and the
    first EXAMPLES_SHOWN. Values that differ only after the cut print one marked form, once - in
    both branches - and a cut form whose ILIKE on its start matches more than one value of the
    whole column says how many, '(N values)' (T9a-R4)."""
    if not values:
        return f"  {col} (text)"
    complete = len(values) <= COMPLETE_VALUES_MAX
    limit = len(values) if complete else EXAMPLES_SHOWN
    shown, seen = [], set()
    for value in values:
        printed = _display_value(value)
        if printed not in seen:
            seen.add(printed)
            shown.append((printed, len(value) > VALUE_DISPLAY_MAX))
            if len(shown) >= limit:
                break
    lowered = None
    parts = []
    for printed, cut in shown:
        part = repr(printed)
        if cut:
            if lowered is None:
                lowered = [value.lower() for value in values]
            matched = _ilike_start_count(printed[:-len(CUT_MARK)], lowered)
            if matched > 1:
                part += f" ({matched} values)"
        parts.append(part)
    listed = ", ".join(parts)
    if complete:
        return f"  {col} (text; possible values (complete): {listed})"
    return f"  {col} (text; {len(values)} distinct values, examples: {listed})"


def _table_schema_body(cols: list, rows: list, col_types: dict) -> str:
    """What the schema block prints for one table below its heading and holds line. Up to
    MAX_COLS_DETAILED columns: one line per column, '(numeric)' or its values. A wider table:
    every column name and type on one line, then its first three rows (their first 20
    non-empty cells, as before)."""
    if len(cols) > MAX_COLS_DETAILED:
        listing = ", ".join(f"{c} ({'numeric' if col_types.get(c) == 'DOUBLE' else 'text'})"
                            for c in cols)
        body = f"Columns (all {len(cols)}): {listing}\n"
        body += "This table has many columns. Here are the first few sample rows:\n"
        for i, row in enumerate(rows[:3]):
            non_empty = {k: v for k, v in row.items() if v is not None and str(v).strip()}
            body += f"  Row {i}: {dict(list(non_empty.items())[:20])}\n"
        return body
    lines = [f"  {c} (numeric)" if col_types.get(c) == "DOUBLE"
             else _text_column_line(c, _column_values(rows, c)) for c in cols]
    return "Columns:\n" + "\n".join(lines) + "\n"


#: Each table's schema body, keyed by its content, least recently used first. Bounded: past
#: _SCHEMA_CACHE_MAX tables the oldest is dropped. Shared by every request in the process,
#: hence the lock; two requests building the same body at once both get the same text.
_SCHEMA_CACHE_MAX = 256
_schema_cache = OrderedDict()
_schema_cache_lock = threading.Lock()


def _reset_schema_cache() -> None:
    """Drop the cache. Exists for the tests, which must not leak state between cases."""
    with _schema_cache_lock:
        _schema_cache.clear()


def _table_signature(cols: list, rows: list):
    """A content signature of one loaded table - its column names and every cell, each read
    under its own column (`row.get(c)` in column order, as the DuckDB load reads it) - so a
    re-upload that changes one cell, with the same rows and the same columns, is a different
    table. Never the row's own key order (fix round 1): rows holding the same values in another
    key order, swapped between two columns, read alike in key order. Python's own tuple hash:
    measured at about a quarter of the cost of the body it saves (7 ms against 30 ms on a table
    of several thousand rows), where an md5 of a JSON or repr dump costs about as much as the
    body. Its one blind spot is cells
    Python hashes alike although they print differently - 1, 1.0 and True, or -1 and -2 - which
    a CSV upload, whose cells are all text, never holds. A cell that cannot be hashed falls back
    to a hash of a JSON dump of the same cells."""
    try:
        return hash((tuple(cols), tuple(tuple(map(row.get, cols)) for row in rows)))
    except TypeError:
        return hash(json.dumps([cols, [[row.get(c) for c in cols] for row in rows]],
                               default=str, ensure_ascii=False))


def _cached_table_body(table_name: str, cols: list, rows: list, col_types: dict) -> str:
    """`_table_schema_body`, scanned once per table per upload: keyed on the table's name, its
    row count, its content signature and its column types, so an unchanged table re-fetched
    for the next question is served from the cache and a changed one is scanned afresh."""
    key = (table_name, len(rows), _table_signature(cols, rows),
           tuple(col_types.get(c) for c in cols))
    with _schema_cache_lock:
        body = _schema_cache.get(key)
        if body is not None:
            _schema_cache.move_to_end(key)
            return body
    body = _table_schema_body(cols, rows, col_types)
    with _schema_cache_lock:
        _schema_cache[key] = body
        _schema_cache.move_to_end(key)
        while len(_schema_cache) > max(1, _SCHEMA_CACHE_MAX):
            _schema_cache.popitem(last=False)
    return body


def _holds_sentence(holds) -> str:
    """A card's `holds` sentence as it is quoted: whitespace collapsed, and cut to
    HOLDS_MAX_CHARS with '...' when longer - the cut `_source_lines` makes."""
    text = re.sub(r"\s+", " ", str(holds or "")).strip()
    if len(text) > HOLDS_MAX_CHARS:
        text = text[:HOLDS_MAX_CHARS - 3].rstrip() + "..."
    return text


#: The `holds` a card carries when no manifest describes its table: the file, its row count and
#: its column names, and nothing else - "`<file>.csv` — N rows. Columns: <names>." (T9a-R5).
_COLUMN_LISTING_HOLDS_RE = re.compile(
    r"^`[^`\s]+\.csv` — [\d,]+ rows?\. Columns: [A-Za-z0-9_]+(?:, [A-Za-z0-9_]+)*\.?$")


def _holds_is_column_listing(holds) -> bool:
    """True when a card's WHOLE `holds` - read before any cut, a long listing runs past
    HOLDS_MAX_CHARS - is only the generated column listing (`_COLUMN_LISTING_HOLDS_RE`). It says
    nothing the column lines under its heading do not, so the schema block skips it (T9a-R5);
    the SOURCE line still quotes it (`_source_lines`, T9a-R3). A listing followed by anything of
    its own says more, and is printed. Shape only."""
    return bool(_COLUMN_LISTING_HOLDS_RE.match(re.sub(r"\s+", " ", str(holds or "")).strip()))


def _routed_holds(cards: list, selected) -> dict:
    """{table: holds sentence} of the routed cards - what each routed table holds, and often
    what it does NOT carry and where that lives - for the schema block (spec-fix5)."""
    return {c["table"]: c.get("holds") for c in cards
            if isinstance(c, dict) and isinstance(c.get("table"), str)
            and c["table"] in selected and c.get("holds")}


def _schema_block(tables: list, holds_by_table: dict | None = None) -> tuple:
    """`(text, types)`: the 'Available tables:' block the SQL writer reads, and each table's
    column types (`_infer_column_types`) - the types the DuckDB load uses, so the writer's view
    and the loaded table agree. Each table gets its heading, its routed card's holds sentence
    (when there is one that says more than the column list - T9a-R5) and its body
    (`_cached_table_body`)."""
    text = "Available tables:\n"
    types_by_table = {}
    for t in tables:
        cols = t["columns"]
        rows = t["rows"] or []
        col_types = _infer_column_types(cols, rows)
        types_by_table[t["table_name"]] = col_types
        if len(cols) > MAX_COLS_DETAILED:
            text += f"\nTable: {t['table_name']} ({t['row_count']} rows, {len(cols)} columns)\n"
        else:
            text += f"\nTable: {t['table_name']} ({t['row_count']} rows)\n"
        raw_holds = (holds_by_table or {}).get(t["table_name"])
        holds = "" if _holds_is_column_listing(raw_holds) else _holds_sentence(raw_holds)
        if holds:
            text += f"Holds: {holds}\n"
        text += _cached_table_body(t["table_name"], cols, rows, col_types)
    return text, types_by_table


def _has_iso_companion(tables: list) -> bool:
    """True when a table carries a column `<x>_iso` beside a column `<x>` - the ISO companion
    of a printed date column - so ISO_DATE_RULE goes into the prompt. Shape only."""
    for t in tables:
        names = {str(c).lower() for c in t.get("columns") or []}
        if any(n.endswith("_iso") and n[:-len("_iso")] in names for n in names):
            return True
    return False


def _is_numeric_value(v) -> bool:
    """True when a value parses as a plain number (comma thousands allowed) — a
    breakdown of such a column reads as a product ("800 x 17" is this corpus's own
    load-calculation notation for 17 points at 800 W each), never as a count."""
    s = str(v).strip().replace(",", "")
    if not s:
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False


def _clean_shape_value(v, max_len: int = 40) -> str:
    """Collapse whitespace/newlines to a single space (a raw newline in a value would
    otherwise fragment the block into an extra physical line, and — if the text after
    it happened to start with a pipe — that line would read as a markdown table row
    to _sql_result_is_empty), then cap the DISPLAYED value at max_len characters. The
    value used for counting/grouping is always the uncleaned, untruncated original."""
    s = re.sub(r"\s+", " ", str(v)).strip()
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip() + "…"
    return s


def _qualifies_as_breakdown_column(values, n: int) -> bool:
    """True when a column's non-blank `values` (over `n` total rows) earns a breakdown line:
    2-8 distinct values, fewer than one per row (`k == n` just restates the table), and not
    EVERY value numeric (`_is_numeric_value`) - a breakdown of a number reads as a product
    ("800 x 17" is this corpus's own load-calculation notation for 17 points at 800 W each),
    never as a count. The ONE rule `_result_shape`'s own breakdown and `_totals_breakdown`'s
    per-value sums both apply (W5-A1 review minor), so the two can no longer drift apart by
    one of them changing a bound the other keeps."""
    k = len(set(map(str, values)))
    if not (2 <= k <= 8) or k == n:
        return False
    if values and all(_is_numeric_value(v) for v in values):
        return False
    return True


def _result_shape(col_names: list, result: list, shown: int) -> str:
    """What the result IS, stated before the writer reads it: total rows, rows shown,
    the distinct count of the identifier-like column, and a breakdown of every
    low-variety column. Empty for results under 4 rows. 2026-09-18: 88 cameras x 4
    spec rows = 352 rows, cut to 50, were reported as "352 units, all one model".

    Fix round 1 (2026-09-18 review): a join can return the SAME column name twice
    (e.g. a.v / b.v), so everything below is keyed by column INDEX, never by name —
    keying by name silently overwrote one column's distribution with the other's. A
    breakdown counts DISTINCT identifier-column values per bucket when an
    identifier-like column exists (352 rows / 4 spec-rows-per-camera must read as 65
    cameras, not 260 rows), and row counts otherwise — and always NAMES which base it
    used ("per distinct <col>" or "rows"), notation "value (N)", never "value x N"
    (this corpus's own drawings write "800 x 17" to mean 17 points at 800 W each, so
    "x" reads as multiplication here). All-numeric columns and columns where every
    value occurs once (k == n, a breakdown that just restates the table) are skipped.
    The header's "distinct" clause is omitted when the identifier column has <= 1
    distinct value. The block is labelled RESULT SHAPE, not SHAPE, so it cannot be
    confused with CHANGE_IMPACT_ANSWER_SHAPE in the same prompt."""
    n = len(result)
    if n < 4:
        return ""
    lines = [f"RESULT SHAPE - rows: {n}; shown: {min(shown, n)}"]
    ncols = len(col_names)
    distinct = {}  # column index -> (distinct count, non-empty values)
    for ci in range(ncols):
        vals = [row[ci] for row in result if row[ci] is not None and str(row[ci]).strip() != ""]
        distinct[ci] = (len(set(map(str, vals))), vals)

    # identifier-like: the column (by INDEX) with the most distinct values
    ident_idx = max(range(ncols), key=lambda ci: distinct[ci][0]) if ncols else None
    ident_k = distinct[ident_idx][0] if ident_idx is not None else 0
    use_ident = ident_idx is not None and 1 < ident_k < n
    if use_ident:
        lines[0] += f" | distinct {col_names[ident_idx]}: {ident_k}"

    for ci, cn in enumerate(col_names):
        if ci == ident_idx:
            continue
        k, vals = distinct[ci]
        if not _qualifies_as_breakdown_column(vals, n):
            continue
        if use_ident:
            groups = {}
            for row in result:
                v = row[ci]
                if v is None or str(v).strip() == "":
                    continue
                idv = row[ident_idx]
                if idv is None or str(idv).strip() == "":
                    continue
                groups.setdefault(str(v), set()).add(str(idv))
            counts = {v: len(ids) for v, ids in groups.items()}
            basis = f"per distinct {col_names[ident_idx]}"
        else:
            counts = {}
            for v in vals:
                sv = str(v)
                counts[sv] = counts.get(sv, 0) + 1
            basis = "rows"
        if not counts:
            continue
        parts = [f"{_clean_shape_value(v)} ({c})" for v, c in sorted(counts.items(), key=lambda kv: -kv[1])]
        line = f"{cn} ({basis}): " + ", ".join(parts)
        if len(line) > 300:
            line = line[:299].rstrip() + "…"
        if line.startswith("|"):
            line = "- " + line
        lines.append(line)
    return "\n".join(lines)


# A FLAT TOTAL BESIDE A COLUMN THAT SPLITS IT INTO KINDS - wave 5, 2026-10-04.
#
# Measured on the goal-function run of 2026-09-30: a 4-row
# result held 2 of one spare kind and 2 of another, in an `item` column the query did not GROUP
# BY. The deterministic-totals block below sums the qty-like column across every row with no
# awareness that another column splits those rows into different KINDS of thing, so the flat
# "TOTAL qty (all 4 rows): 4" is correct only for "spare units of either kind" - an answer-writer
# sometimes read it and named the WRONG kind's count as the total (true answer for the asked
# kind: 2). The result already carried the right split one line above, in RESULT SHAPE's own
# breakdown - but nothing connected the two lines.
#
# `_totals_breakdown` appends a per-value SUM of the SAME qty-like column, grouped by every OTHER
# column that qualifies (`_qualifies_as_breakdown_column`: 2-8 distinct, non-numeric values,
# fewer than the row count) - the same shape `_result_shape` already uses for its own breakdown,
# through the ONE shared helper, so a column that would not earn a breakdown line there does not
# earn one here either and the two can no longer silently drift apart. Two qualifying columns
# that split the SAME rows the SAME way (a row's own id and that row's display name, printed as
# two columns) are the one split spelled twice: only the first, in SELECT order, is shown.
#
# CAPPED AT TOTALS_BREAKDOWN_MAX_CLAUSES (W5-A1 review minor, 2026-10-04): a result can qualify
# several columns at once, and one guard card in the corpus showed four " — by <col>:" clauses
# stacked on one line - verbose rather than wrong, but a TOTAL line is meant to be read at a
# glance. The MOST INFORMATIVE clauses are kept: fewest distinct values first (a 2-way split
# reads at a glance; an 8-way one does not), ties kept in SELECT order (Python's stable sort,
# over columns already built in that order) - the same tie-break a repeated split already used.
# Left-out columns are named only by their count, never by name: naming them would itself be
# close to showing the clause.
#: At most this many " — by <col>:" clauses on one TOTAL line.
TOTALS_BREAKDOWN_MAX_CLAUSES = 2


def _totals_breakdown(col_names: list, result: list, qty_ci: int, qty_vals: list) -> str:
    """' — by <col>: v1 X, v2 Y' for the TOTALS_BREAKDOWN_MAX_CLAUSES most informative OTHER
    columns that split `result` into 2-8 kinds (never replacing the flat TOTAL, only adding to
    it) - a SUM of the qty-like column at `qty_ci`, grouped by that column's printed value, most
    informative (fewest distinct values) first. `qty_vals` is the parsed float (or None) for
    EVERY row, aligned to `result`, so a row the flat total could not parse contributes to no
    group either. Empty when no other column qualifies."""
    n = len(result)
    candidates = []
    seen_signatures = set()
    for ci, cn in enumerate(col_names):
        if ci == qty_ci:
            continue
        seen_vals = [row[ci] for row in result if row[ci] is not None and str(row[ci]).strip() != ""]
        if not _qualifies_as_breakdown_column(seen_vals, n):
            continue
        first_seen, signature, sums = {}, [], {}
        for row, qv in zip(result, qty_vals):
            v = row[ci]
            if v is None or str(v).strip() == "":
                signature.append(-1)
                continue
            sv = str(v)
            signature.append(first_seen.setdefault(sv, len(first_seen)))
            if qv is not None:
                sums[sv] = sums.get(sv, 0.0) + qv
        if not sums or tuple(signature) in seen_signatures:
            continue
        seen_signatures.add(tuple(signature))
        candidates.append((cn, sums, len(first_seen)))
    if not candidates:
        return ""
    shown = sorted(candidates, key=lambda c: c[2])[:TOTALS_BREAKDOWN_MAX_CLAUSES]
    out = []
    for cn, sums, _ in shown:
        parts = []
        for v, s in sorted(sums.items(), key=lambda kv: -kv[1]):
            s_str = str(int(s)) if s == int(s) else f"{s:.2f}"
            parts.append(f"{_clean_shape_value(v)} {s_str}")
        out.append(f" — by {cn}: " + ", ".join(parts))
    left_out = len(candidates) - len(shown)
    if left_out > 0:
        out.append(f" (and {_counted(left_out, 'more column')} not shown)")
    return "".join(out)


# Bounds on the caveats that ride with a SOURCE line (see `_source_lines`). Three
# is what the widest card in the corpus carries; 300 characters is the same cut
# `_result_shape` uses on a breakdown line.
CAVEATS_PER_TABLE = 3
CAVEAT_MAX_CHARS = 300


def _sql_reads_table(sql: str, name: str) -> bool:
    """Whether `sql` names the table `name` as a whole word, quoted or not. How both the
    SOURCE lines and the hierarchy note decide which tables a query READ - as opposed to
    the tables the router merely loaded beside them."""
    return bool(re.search(r'(?<![A-Za-z0-9_])"?' + re.escape(name) + r'"?(?![A-Za-z0-9_])',
                          sql or ""))


#: A table's own SOURCE line gets the whole-vs-filtered clause (wave 5) when its card's
#: `holds` sentence states a whole-table count at least this many TIMES the query's own result...
SOURCE_WHOLE_TABLE_RATIO = 2
#: ...AND at least this many rows larger - both, so a small table's SOURCE line is never flagged
#: over a difference of a handful of rows.
SOURCE_WHOLE_TABLE_MIN_DIFF = 10


def _holds_leading_count(holds: str):
    """The first integer `holds` states (comma thousands allowed), or None. A card's `holds`
    sentence always opens by saying how many of what the WHOLE table is ('940 widget row(s)
    across 4 kind(s)...') when it is counting anything at all; one that opens some other way
    ('One row per...') states no whole-table count for this check to compare against."""
    m = re.match(r"\s*([\d,]+)\b", holds or "")
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _source_lines(sql: str, tables: list, cards: list, result_rows: int = None) -> str:
    """One 'SOURCE' line per table the SQL reads, quoting that table's card `holds`
    sentence, so the answer-writing model (which never sees the schema or the
    cards) can say WHICH record a number came from. Probe pr-008 (2026-09-17):
    "How many access-control doors?" came back as a bare 68 - correct, and
    unattributable, although the doors card says in one sentence that the 68 are
    the positions drawn on the as-built drawings, and the manual prints three
    other counts. Empty when there are no cards (the unrouted fallback).

    Each SOURCE line is followed by that card's own `caveats`, as NOTE lines.
    2026-09-19 (final-review finding I3): `caveats` was written by doc-prep and
    documented as a card field HERE, and no code in this app read it - so the
    count-views card's "never SUM `count` across `view_id`" warning reached the
    document index and never the model that writes the SQL or the answer. That
    warning exists because a SUM over a long-format table shipped "486 doors",
    "1,267 fan coil units" and "451 card readers" - three confidently-wrong
    totals off a table whose every row is correct. Bounded exactly like `holds`:
    at most CAVEATS_PER_TABLE per table, each cut to CAVEAT_MAX_CHARS, so a card
    with a long caveat list cannot flood the prompt it rides in.

    THE WHOLE TABLE IS NOT THE RESULT (wave 5, 2026-10-04). A card's `holds` sentence
    states the table's WHOLE size ("940 widget row(s)..."), printed directly under the
    RESULT's own, often much smaller, filtered row count - and an answer-writer sometimes quoted
    the whole-table number instead of the result it was asked about. `result_rows` - the caller's
    `len(result)`, OPTIONAL so every existing call keeps today's text - lets each table's OWN line
    say so: when its `holds` sentence's leading count (`_holds_leading_count`) is at least
    SOURCE_WHOLE_TABLE_RATIO times `result_rows` AND at least SOURCE_WHOLE_TABLE_MIN_DIFF rows
    larger, one clause is appended to THAT line only - a `holds` with no leading count (nothing to
    compare) and a `result_rows` of zero or less both add nothing."""
    if not cards or not sql:
        return ""
    holds_by_table = {c.get("table"): (c.get("holds") or "").strip() for c in cards if c.get("table")}
    caveats_by_table = {c.get("table"): (c.get("caveats") or []) for c in cards if c.get("table")}
    lines = []
    for tbl in tables:
        name = tbl["table_name"]
        if not holds_by_table.get(name):
            continue
        if not _sql_reads_table(sql, name):
            continue
        holds = re.sub(r"\s+", " ", holds_by_table[name])
        if len(holds) > 400:
            holds = holds[:397].rstrip() + "..."
        line = f"SOURCE - {name}: {holds}"
        if result_rows is not None and result_rows > 0:
            whole = _holds_leading_count(holds)
            if (whole is not None and whole >= SOURCE_WHOLE_TABLE_RATIO * result_rows
                    and whole - result_rows >= SOURCE_WHOLE_TABLE_MIN_DIFF):
                line += (f" (this describes the WHOLE table; your result above has "
                        f"{result_rows} row(s)).")
        lines.append(line)
        cavs = [re.sub(r"\s+", " ", str(c)).strip() for c in caveats_by_table.get(name) or []]
        for cav in [c for c in cavs if c][:CAVEATS_PER_TABLE]:
            if len(cav) > CAVEAT_MAX_CHARS:
                cav = cav[:CAVEAT_MAX_CHARS - 3].rstrip() + "..."
            lines.append(f"NOTE - {name}: {cav}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# THE NOTES BEHIND A SMALL RESULT - spec-fix1 part (a), 2026-10-01.
#
# A correction or a rival printed figure is often recorded in a row's own
# `notes` cell, right beside the figure a question asks for - and on the
# goal-function run of 2026-09-30 it never reached the answer. A one-row lookup
# selected only the figure's column, ignoring the rule that asks for notes; a
# SUM over one matched row returned a bare number, because that rule exempts
# aggregates. Both notes were on record, and the answer writer saw neither.
#
# So the notes are read in code rather than asked for. For ONE plain SELECT over
# ONE loaded table - no JOIN, no comma join, no set operation, no sub-SELECT -
# whose table has a column named `notes` that the SELECT list did not ask for,
# the same FROM ... WHERE is read again for that column. When it matches 1 to
# ROW_NOTES_MAX_ROWS rows and a note is non-empty, the result ends with the
# distinct notes, one line each, cut at ROW_NOTE_MAX_CHARS. Aggregates are
# included: the rows BEHIND a SUM are exactly what such a note is about. Only
# `notes` is read, never `remarks`: `notes` is where the extraction writes what
# it found about a row, `remarks` is a column printed by the source itself.
# Written from SHAPE - a column named `notes`, a single-table query - so no
# table, value or building is named.
#
# ONLY THE ROWS BEHIND THE RESULT (review fix round 1). A row list cut by
# ORDER BY ... LIMIT (or OFFSET) returns only some of the rows its WHERE
# matched, so its notes are read from the query itself, run whole with the
# notes column appended to its SELECT list: the same ORDER BY, LIMIT and
# positional references, only the rows it returned. A HAVING or QUALIFY filters
# rows after the WHERE, a LIMIT over groups returns only some of them, and a
# DISTINCT row list's LIMIT keeps values rather than rows - none of those can be
# read off the WHERE, so each is refused by its shape, before any extra query.
# An aggregate's own LIMIT chooses among its output, never among the rows
# behind it, so a single aggregate row still reads every row its WHERE matched.
#
# A ROW THE QUESTION NAMES, WHATEVER THE QUERY'S SHAPE - wave 3, G4 (a). Measured
# on the goal-function run of 2026-10-01: a question named one unit by its code,
# the writer walked its parents with a nested sub-SELECT, and the unit's own
# correction - its printed location a sheet-template leftover, the owner's answer
# placing it elsewhere - sat in that row's notes; the refusals above meant it
# never came. So, besides the rows behind a small single-table result, a row the
# question NAMES WHOLE is read too: for each loaded table the SQL reads that has a
# `notes` column, the question is matched against the card's identifier column
# with `_printed_code` - the HIERARCHY walk's own match: a code-shaped value
# printed whole, the longest one winning - and when the result prints that value
# in a cell, the row's notes are added whatever the query's shape or row count. A
# value more than ROW_NOTES_MAX_ROWS rows hold names no single row and adds
# nothing; a note the result already prints in a cell is not repeated; the
# question is read without the loop's step suffix. Its read goes through
# `_companion`: an error drops that part and nothing else.
#
# THE END OF A LONG NOTE - wave 3, G4 (b). A note over ROW_NOTE_MAX_CHARS used to
# keep its first 797 characters. Notes are append-only - the newest finding or
# an owner's answer is written at the END - and that cut dropped exactly the
# correction a question needed, which started past the cap. A long note now keeps
# its first ROW_NOTE_HEAD_CHARS characters, then ROW_NOTE_CUT, then its end,
# within the same cap.
#
# Fix round 1 (ruling W3A3-R1): with an 800 cap the cut still removed a passing
# answer's figure from the middle of a note under 2,000 characters, cut owner
# answers that open a note, and split tokens mid-word. The cap is 2,000 characters
# - a block holds at most three rows, and very few notes are longer - half of it for
# the start and half for the end, and BOTH cut points move to white space
# (`_cut_note`): the start ends at the last space within its share, the end starts
# just after the first space within its share, so no token is ever split.
#
# EACH NOTE NAMES ITS ROW - wave 3, G5 (2). Measured on the same run: rows came
# back for a place the question named, and one row's note - another name that
# row is also printed under - was read as a separate place, because no bullet
# said whose note it was. Every bullet now opens with its row's
# identifier - the column the table's card names (`_row_key_column`); a column
# guessed from its name could be a parent or place key and would mislabel the
# row - and the same note on two rows is two bullets, no longer folded into one.
# A table with no card identifier keeps unkeyed bullets, and there an identical
# note is still said once: repeated without a key it would say nothing new.
#
# Fix round 1 (the review's minor 2): a card identifier that repeats over the
# table's loaded rows - a room text printed on several circuits, blank on others -
# keyed two rows alike, so their same note read as one again. When the identifier
# is not unique, the key adds ONE more column (`_row_key_columns`): of the table's
# parent-reference columns (`_is_parent_column`) and columns whose names end like
# an identifier's (ROW_KEY_ID_SUFFIXES), the one that most raises the number of
# distinct keys over the loaded rows - then the one more distinct on its own, then
# the first in the table - and only when it raises it at all. Chosen from the data
# and the column shapes, never from a table's name. The key reads
# '<identifier> (<column> <value>)', or '<column> <value>' where the identifier
# is blank.
# ---------------------------------------------------------------------------
ROW_NOTES_HEADING = "NOTES ON THE ROWS BEHIND THIS RESULT:"
ROW_NOTES_MAX_ROWS = 3
#: Ruling W3A3-R1: at most this many characters of one note.
ROW_NOTE_MAX_CHARS = 2000
#: G4 (b), W3A3-R1: at most this much of a long note's start is kept; the rest of the cap is its
#: end - half each, so a long note's newest segment comes whole.
ROW_NOTE_HEAD_CHARS = 1000
#: G4 (b): what stands where a long note was cut.
ROW_NOTE_CUT = " … "
#: G5: an identifier longer than this is shortened in its bullet - the note never is.
ROW_KEY_MAX_CHARS = 80
#: Fix round 1: name endings that mark a column as identifying a row - with the parent references,
#: the columns a repeated identifier's key may add one of.
ROW_KEY_ID_SUFFIXES = ("_id", "_no", "_number", "_tag", "_ref", "_code")

#: Clauses that filter rows after the WHERE, and clauses that cut the rows returned.
_FILTERS_AFTER_WHERE = frozenset(("HAVING", "QUALIFY"))
_ROW_CUTS = frozenset(("LIMIT", "OFFSET"))

#: What `_single_table_query` reads off a query: the table it names, its SELECT list as
#: `[(token, depth), …]`, its FROM ... WHERE verbatim, where its FROM starts in the SQL,
#: and the clauses that follow the WHERE (GROUP, ORDER, HAVING, LIMIT, ...).
_SingleTableQuery = namedtuple("_SingleTableQuery",
                               "table select_list from_where from_pos clauses")

#: The clauses that can follow a WHERE, so they end the FROM ... WHERE a companion
#: query reuses. `sql_loop` ends a WHERE with the same words plus the set operations,
#: which never get this far: a query carrying one is refused first.
_CLAUSES_AFTER_WHERE = frozenset(("GROUP", "ORDER", "HAVING", "LIMIT", "OFFSET", "QUALIFY",
                                  "WINDOW"))
_SET_OPERATIONS = frozenset(("UNION", "INTERSECT", "EXCEPT"))

#: Words that can follow a FROM's table name without being its alias.
_NOT_AN_ALIAS = _CLAUSES_AFTER_WHERE | _SET_OPERATIONS | frozenset((
    "WHERE", "AS", "ON", "USING", "JOIN", "NATURAL", "CROSS", "INNER", "LEFT", "RIGHT",
    "FULL", "OUTER", "POSITIONAL", "ASOF", "ANTI", "SEMI", "LATERAL", "TABLESAMPLE",
    "SAMPLE", "PIVOT", "UNPIVOT"))


def _word(token) -> str:
    """The upper-cased word a bare-word token spells; "" for any other token."""
    return token[2].upper() if token[0] == "word" else ""


def _paren_depths(tokens) -> list:
    """Each token's parenthesis depth - 0 for the query's own clauses, 1 inside a
    function call or a parenthesised condition, and so on. A bracket is at the depth of
    what encloses it."""
    depths, depth = [], 0
    for token in tokens:
        if token[1] == ")":
            depth = max(0, depth - 1)
        depths.append(depth)
        if token[1] == "(":
            depth += 1
    return depths


def _single_table_query(sql: str, table_names, nested: bool = False):
    """A `_SingleTableQuery` when `sql` is ONE plain SELECT over ONE of `table_names`, else
    None.

    "One plain SELECT" means: it opens with SELECT and holds no other SELECT (so no
    sub-SELECT and no CTE), no JOIN and no set operation, and its own FROM names a single
    table - optionally schema-qualified, optionally aliased - followed only by its WHERE
    or a later clause, so a comma join, a table function or a sample clause is refused.
    The SQL is read with `sql_loop`'s tokenizer, so a keyword inside a string literal or a
    quoted name is never taken for syntax. `select list` is `[(token, depth), …]`; the
    FROM ... WHERE is cut out of `sql` verbatim, up to the first clause that follows the
    WHERE (GROUP BY, ORDER BY, LIMIT, ...).

    `nested` (wave 3, F4): a sub-SELECT inside the query - in its WHERE, say - is allowed,
    and only the query's own SELECT, JOINs and set operations (depth 0) are counted; the
    FROM ... WHERE cut out then carries the sub-SELECT whole."""
    tokens = sql_token_spans(sql)
    if not tokens or _word(tokens[0]) != "SELECT":
        return None
    words = [_word(t) for t in tokens]
    depths = _paren_depths(tokens)
    own = [w for w, d in zip(words, depths) if d == 0] if nested else words
    if own.count("SELECT") != 1 or "JOIN" in own or _SET_OPERATIONS.intersection(own):
        return None
    froms = [i for i, w in enumerate(words) if w == "FROM" and depths[i] == 0]
    if len(froms) != 1:
        return None
    start = froms[0]

    def is_name(k):
        return k < len(tokens) and (tokens[k][0] == "qid"
                                    or (tokens[k][0] == "word" and words[k] not in _NOT_AN_ALIAS))

    j = start + 1
    if not is_name(j):
        return None
    name, j = tokens[j][2], j + 1
    if j + 1 < len(tokens) and tokens[j][1] == "." and is_name(j + 1):
        name, j = tokens[j + 1][2], j + 2
    if j < len(tokens) and words[j] == "AS":
        j += 1
    if is_name(j):
        j += 1
    if j < len(tokens) and words[j] != "WHERE" and words[j] not in _CLAUSES_AFTER_WHERE:
        return None
    table = next((t for t in table_names if str(t).lower() == str(name).lower()), None)
    if table is None:
        return None
    clause_at = [k for k in range(j, len(tokens))
                 if depths[k] == 0 and words[k] in _CLAUSES_AFTER_WHERE]
    end = tokens[clause_at[0]][3] if clause_at else len(sql)
    select_list = list(zip(tokens[1:start], depths[1:start]))
    return _SingleTableQuery(table, select_list, sql[tokens[start][3]:end].rstrip(),
                             tokens[start][3], frozenset(words[k] for k in clause_at))


def _row_key_column(table: dict, cards):
    """The column naming each row of `table` - the one its card names as its identifier, when
    the loaded table has that column - or None (G5). Only the card's: a column guessed from its
    name could be a parent or place key, and a bullet keyed by it would name the wrong thing."""
    card = next((c for c in cards or [] if c.get("table") == table.get("table_name")), None)
    declared = (card or {}).get("identifier_column")
    return _column_named(table, declared) if declared else None


def _row_key_columns(table: dict, cards) -> tuple:
    """`(identifier column, second column)` of `table`'s note keys - the identifier from
    `_row_key_column` (None: unkeyed), and, when that column is not unique over the loaded rows
    (a value on two rows, or a blank one), the column that most raises the number of distinct keys
    among its parent-reference and identifier-shaped columns (see the comment above), or None when
    none raises it. Read in Python off the rows already loaded."""
    key = _row_key_column(table, cards)
    rows = table.get("rows") or []
    if key is None:
        return None, None

    def cell(row, column):
        return re.sub(r"\s+", " ", "" if row.get(column) is None else str(row.get(column))).strip()

    ids = [cell(row, key) for row in rows]
    distinct = len({value for value in ids if value})
    if distinct == len(rows):
        return key, None
    best = None
    for order, column in enumerate(table.get("columns") or []):
        if (column == key or str(column).lower() == "notes"
                or not (_is_parent_column(column)
                        or str(column).lower().endswith(ROW_KEY_ID_SUFFIXES))):
            continue
        values = [cell(row, column) for row in rows]
        pairs = len({pair for pair in zip(ids, values) if pair[0] or pair[1]})
        rank = (pairs, len({value for value in values if value}), -order)
        if pairs > distinct and (best is None or rank > best[0]):
            best = (rank, column)
    return key, (best[1] if best else None)


def _key_text(key_value, column, value):
    """A note's key: '<identifier> (<column> <value>)', the identifier alone, '<column> <value>'
    where the identifier is blank, or None with neither - each part shortened to
    ROW_KEY_MAX_CHARS."""
    shown = "" if key_value is None else _clean_shape_value(key_value, ROW_KEY_MAX_CHARS)
    extra = ""
    if column is not None and value is not None and str(value).strip():
        extra = f"{column} {_clean_shape_value(value, ROW_KEY_MAX_CHARS)}"
    if shown and extra:
        return f"{shown} ({extra})"
    return shown or extra or None


def _cut_note(text: str, max_chars: int = ROW_NOTE_MAX_CHARS,
              head_chars: int = ROW_NOTE_HEAD_CHARS) -> str:
    """`text` within `max_chars`: whole when it fits, else at most its first `head_chars`
    characters, ROW_NOTE_CUT and its end - notes are append-only, so the end holds the newest
    finding (G4 b). Both cut points land on white space (ruling W3A3-R1): the start ends at the
    last space within its share, the end starts after the first space within its share, so no
    token is split; a separator left standing at a cut (`|`) is dropped with it. A share holding
    no space at all is cut where it ends."""
    if len(text) <= max_chars:
        return text
    tail_chars = max_chars - head_chars - len(ROW_NOTE_CUT)
    head = text[:head_chars]
    if not text[head_chars].isspace():
        space = max(head.rfind(" "), head.rfind("\t"), head.rfind("\n"))
        if space > 0:
            head = head[:space]
    start = len(text) - tail_chars
    if not text[start - 1].isspace():
        spaces = [i for i in (text.find(" ", start), text.find("\t", start), text.find("\n", start))
                  if i != -1]
        if spaces and min(spaces) + 1 < len(text):
            start = min(spaces) + 1
    head = head.rstrip()
    tail = text[start:].lstrip()
    if head.endswith(" |"):
        head = head[:-2].rstrip()
    if tail.startswith("| "):
        tail = tail[2:].lstrip()
    return head + ROW_NOTE_CUT + tail


def _shorten_words(value, max_chars: int) -> str:
    """`value` with its white space folded: whole when it fits in `max_chars`, else cut at the last
    space within it and ended with '…' - never inside a token (ruling W3A3-R1, for the short
    values a computed line shows). A first word longer than the cap is cut where the cap ends."""
    text = re.sub(r"\s+", " ", "" if value is None else str(value)).strip()
    if len(text) <= max_chars:
        return text
    head = text[:max_chars - 1]
    if not text[max_chars - 1].isspace():
        space = head.rfind(" ")
        if space > 0:
            head = head[:space]
    return head.rstrip() + "…"


def _note_bullets(pairs) -> list:
    """The bullets for `[(key, note), …]` - each key as `_key_text` wrote it - in order:
    '<key>: <note>', the note cut by `_cut_note`, or the note alone where there is no key.
    Empty notes are left out, and a bullet identical to an earlier one is not repeated: with
    keys, the same note on two rows is two bullets (G5)."""
    bullets = []
    for key, value in pairs:
        text = re.sub(r"\s+", " ", "" if value is None else str(value)).strip()
        if not text:
            continue
        shown = "" if key is None else str(key)
        bullet = f"{shown}: {_cut_note(text)}" if shown else _cut_note(text)
        if bullet not in bullets:
            bullets.append(bullet)
    return bullets


def _rows_behind_notes(con, sql: str, tables: list, cards) -> list:
    """`[(row key, note), …]` of the rows behind a small single-table result, or [] when that
    does not apply - see the comment above for when it does. Runs ONE extra query on `con`."""
    shape = _single_table_query(sql, [t["table_name"] for t in tables])
    if not shape or shape.clauses & _FILTERS_AFTER_WHERE:
        return []
    select_words = {_word(token) for token, _ in shape.select_list}
    # A query returning one row per row it read - no aggregate, or a window function -
    # against one that folds its rows into an aggregate's output.
    row_per_row = "OVER" in select_words or not sql_is_aggregate(sql)
    cut = bool(shape.clauses & _ROW_CUTS)
    if cut and "GROUP" in shape.clauses:
        return []  # a LIMIT over groups returns only some of them
    if cut and row_per_row and shape.select_list and _word(shape.select_list[0][0]) == "DISTINCT":
        return []  # a DISTINCT row list's LIMIT keeps values, not rows
    table = next((t for t in tables if t["table_name"] == shape.table), {})
    notes_col = _column_named(table, "notes")
    if notes_col is None:
        return []
    for token, depth in shape.select_list:
        # Already asked for: by name, or by a bare `*` / `t.*`, which carries every column.
        if token[0] in ("word", "qid") and str(token[2]).lower() == "notes":
            return []
        if token[1] == "*" and depth == 0:
            return []
    quoted = _quoted_name(notes_col)
    key_col, second_col = _row_key_columns(table, cards)
    key = _quoted_name(key_col) if key_col is not None else "NULL"
    second = _quoted_name(second_col) if second_col is not None else "NULL"
    if cut and row_per_row:
        # The query itself, whole - same ORDER BY, LIMIT and positional references - with
        # the key and notes columns appended to its SELECT list, so only the rows it returned
        # count.
        query = (f'SELECT "__row_key", "__row_key2", "__row_notes" FROM '
                 f'({sql[:shape.from_pos].rstrip()}, {key} AS "__row_key", {second} AS '
                 f'"__row_key2", {quoted} AS "__row_notes" {sql[shape.from_pos:]}) '
                 f'AS "__returned" LIMIT {ROW_NOTES_MAX_ROWS + 1}')
    else:
        query = (f"SELECT {key}, {second}, {quoted} {shape.from_where} ORDER BY rowid "
                 f"LIMIT {ROW_NOTES_MAX_ROWS + 1}")
    rows = _execute_with_timeout(con, query)
    if not 1 <= len(rows) <= ROW_NOTES_MAX_ROWS:
        return []
    return [(_key_text(key_value, second_col, value), note) for key_value, value, note in rows]


def _codes_in_text(text: str, values) -> list:
    """The distinct `values` a cheap test cannot rule out of `text` - every value whose
    lower-cased, space-folded form `text` contains, and every non-ASCII one - in table order.
    `_printed_code` then decides; this only spares it a regex per value of a long column."""
    haystack = re.sub(r"\s+", " ", text or "").lower()
    found, seen = [], set()
    for value in values:
        if value is None or value in seen:
            continue
        seen.add(value)
        shown = re.sub(r"\s+", " ", str(value)).strip()
        if shown and (not shown.isascii() or shown.lower() in haystack):
            found.append(value)
    return found


def _named_row_notes(con, question: str, sql: str, tables: list, cards, result) -> list:
    """`[(row key, note), …]` of the row the question NAMES WHOLE, in every loaded table the SQL
    reads that has a `notes` column and a card identifier column - when the result prints that
    row's identifier in a cell, whatever the query's shape or row count (G4 a; see the comment
    above). One query per such table; the caller drops these, and nothing else, if one raises."""
    text = _split_step_suffix(question)[0]
    cells = {str(v).strip() for row in result or [] for v in row
             if v is not None and str(v).strip()}
    if not cells:
        return []
    found = []
    for table in tables:
        if not table.get("rows") or not _sql_reads_table(sql, table["table_name"]):
            continue
        notes_col, key_col = _column_named(table, "notes"), _row_key_column(table, cards)
        if notes_col is None or key_col is None:
            continue
        node = _printed_code(text, _codes_in_text(text, (row.get(key_col)
                                                        for row in table["rows"])))
        if node is None or str(node).strip() not in cells:
            continue
        second_col = _row_key_columns(table, cards)[1]
        second = _quoted_name(second_col) if second_col is not None else "NULL"
        rows = _execute_with_timeout(con, (
            f"SELECT {_quoted_name(key_col)}, {second}, {_quoted_name(notes_col)} "
            f"FROM {_quoted_name(table['table_name'])} "
            f"WHERE {_text_sql(key_col)} = {_sql_literal(str(node).strip())} "
            f"ORDER BY rowid LIMIT {ROW_NOTES_MAX_ROWS + 1}"))
        if not 1 <= len(rows) <= ROW_NOTES_MAX_ROWS:
            continue  # a value that many rows hold names no single row
        # A blank note names nothing; a note the result already prints is not repeated.
        found.extend((_key_text(key_value, second_col, value), note)
                     for key_value, value, note in rows
                     if note is not None and str(note).strip()
                     and str(note).strip() not in cells)
    return found


def _row_notes_block(con, question: str, sql: str, tables: list, cards, result) -> str:
    """The NOTES ON THE ROWS BEHIND THIS RESULT block, or "" when no note applies: the notes of
    the rows behind a small single-table result (`_rows_behind_notes`), then those of a row the
    question names whole that are not there already (`_named_row_notes`), one keyed bullet each.
    Each part's reads go through `_companion`, so an error drops that part and nothing else."""
    behind = _companion("row notes", _rows_behind_notes, con, sql, tables, cards) or []
    named = _companion("named row notes", _named_row_notes, con, question, sql, tables, cards,
                       result) or []
    bullets = _note_bullets(list(behind) + list(named))
    if not bullets:
        return ""
    return "\n".join([ROW_NOTES_HEADING] + ["- " + bullet for bullet in bullets])


# ---------------------------------------------------------------------------
# PRINTED TOTAL ROWS ARE NEVER COUNTED AS ITEMS - spec-fix6, 2026-10-01.
#
# A table can keep a sheet's own printed total rows beside the item rows they
# total, marked by a row-kind column - its card's `value_vocabulary` lists a
# total kind for it. An aggregate over such a table that never names that
# column adds the printed total to the items it totals. Measured on the
# goal-function run of 2026-09-30: a SUM over one controller's points added the
# sheet's printed total row to its item rows and the answer stated the sum of
# the two. The schema block showed the kind column's values; the loop exempts
# aggregates from its checks; nothing caught it.
#
# So, for an aggregate that reads ONE table whose card marks printed-total rows,
# and whose SQL never names that column, the same SQL is re-run in code twice:
# with the table replaced by its rows WITHOUT the printed totals, and by the
# printed total rows ALONE. When the first differs from what the query returned
# - so the printed totals really were counted - one line names both figures.
# The replacement is a TEMP VIEW under the table's own name, which DuckDB
# resolves before the table itself, so the SQL is re-run byte for byte and never
# rewritten; it is dropped again before anything else reads the connection.
# Written from SHAPE - a kind column whose vocabulary holds a total word - so no
# table, column or building is named.
# ---------------------------------------------------------------------------
PRINTED_TOTALS_HEADING = "PRINTED TOTAL ROWS - "
TOTAL_ROW_KINDS = ("total", "totals", "subtotal", "grand total")
#: A recomputed result longer than this is not spelled out inline; the line is dropped.
PRINTED_TOTALS_MAX_ROWS = 10


def _quoted_name(name) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _printed_total_columns(card) -> list:
    """The columns a card's `value_vocabulary` marks as holding printed total rows: those
    whose listed values include one of TOTAL_ROW_KINDS, whatever its case."""
    vocab = (card or {}).get("value_vocabulary")
    if not isinstance(vocab, dict):
        return []
    return [col for col, values in vocab.items()
            if isinstance(values, (list, tuple))
            and any(str(v).strip().lower() in TOTAL_ROW_KINDS for v in values)]


def _names_in_sql(sql: str) -> set:
    """Every name `sql` spells - bare words and quoted names, lower-cased - read with the
    tokenizer, so a word inside a string literal is never one of them."""
    return {str(t[2]).lower() for t in (sql_token_spans(sql) or []) if t[0] in ("word", "qid")}


def _rerun_with_table_as(con, sql: str, table: str, condition: str):
    """`sql` re-run with `table` standing for its rows that meet `condition` - a TEMP VIEW
    of the same name, which DuckDB resolves before the table - and dropped again whatever
    happens, so the connection is left exactly as it was."""
    catalog = con.execute("SELECT current_database()").fetchone()[0]
    view = _quoted_name(table)
    con.execute(f"CREATE OR REPLACE TEMP VIEW {view} AS SELECT * FROM "
                f"{_quoted_name(catalog)}.main.{view} WHERE {condition}")
    try:
        return _execute_with_timeout(con, sql)
    finally:
        con.execute(f"DROP VIEW IF EXISTS temp.main.{view}")


def _rows_hold_nothing(rows) -> bool:
    """True when a recomputed result holds nothing: no rows, every cell NULL or blank, or a
    lone zero - the same notion of empty the loop reads off a result's table."""
    cells = [v for row in rows or [] for v in row]
    if all(v is None or str(v).strip() == "" for v in cells):
        return True
    return len(cells) == 1 and str(cells[0]).strip() in ("0", "0.0")


def _inline_result(col_names, rows):
    """A recomputed result written into one line: a single figure as itself, one row as
    `col = value, ...`, several rows each in brackets. None when there are no rows or more
    than PRINTED_TOTALS_MAX_ROWS of them."""
    if not rows or len(rows) > PRINTED_TOTALS_MAX_ROWS:
        return None

    def cell(v):
        return "NULL" if v is None else str(v)

    if len(rows) == 1 and len(col_names) == 1:
        return cell(rows[0][0])
    parts = [", ".join(f"{c} = {cell(v)}" for c, v in zip(col_names, row)) for row in rows]
    return parts[0] if len(parts) == 1 else ", ".join(f"({p})" for p in parts)


def _printed_total_rows_line(con, sql: str, tables: list, cards: list, col_names, result) -> str:
    """The PRINTED TOTAL ROWS line for `sql`, or "" when it does not apply - see the
    comment above for when it does. Runs up to two extra queries on `con`; the caller drops
    the line, and nothing else, if either raises."""
    if not sql_is_aggregate(sql):
        return ""
    names = _names_in_sql(sql)
    read = [t for t in tables if str(t["table_name"]).lower() in names]
    if len(read) != 1:
        return ""
    table = read[0]["table_name"]
    by_lower = {str(c).lower(): c for c in read[0].get("columns") or []}
    card = next((c for c in cards or [] if c.get("table") == table), None)
    kind_cols = [by_lower[str(c).lower()] for c in _printed_total_columns(card)
                 if str(c).lower() in by_lower]
    if not kind_cols or any(str(c).lower() in names for c in kind_cols):
        return ""  # no printed total rows to count - or the writer already filtered them
    kinds = ", ".join("'" + k.replace("'", "''") + "'" for k in TOTAL_ROW_KINDS)
    is_total = " OR ".join(
        f"COALESCE(lower(trim(CAST({_quoted_name(c)} AS VARCHAR))) IN ({kinds}), FALSE)"
        for c in kind_cols)
    without = _rerun_with_table_as(con, sql, table, f"NOT ({is_total})")
    if without == result:
        return ""  # no printed total row was counted: the figure is already without them
    if _rows_hold_nothing(without):
        # The query read ONLY printed total rows - filtered to them by some other column -
        # so it asks about the printed total itself, and its figure IS that total: nothing
        # was counted twice (review fix round 1).
        return ""
    alone = _rerun_with_table_as(con, sql, table, is_total)
    shown_without, shown_alone = _inline_result(col_names, without), _inline_result(col_names, alone)
    if shown_without is None or shown_alone is None:
        return ""
    return (f"{PRINTED_TOTALS_HEADING}the figure above counts the sheet's own printed total "
            f"row(s) as items; without them: {shown_without}; the printed total row(s) alone: "
            f"{shown_alone}")


# ---------------------------------------------------------------------------
# NOTES INSIDE THE FIGURE - wave 5, G12 (2026-10-04), designed in plan-wave4.md.
#
# Measured on the goal-function run of 2026-09-30: a GROUP BY aggregate over a JOIN of two
# tables counted several rows behind a handful of output groups; a couple of those rows carry
# their own `notes` cell flagging a disputed reading ("the page ticks X, its REMARKS reads Y
# ... Not owner-confirmed"). Nothing already built surfaces it: the single-table notes block above
# refuses a JOIN outright; WHAT A ONE-FIGURE AGGREGATE COUNTS (below) needs one output row,
# never a GROUP BY; and the retrieved excerpt that carries the same note ranked outside the
# 3-excerpt cross-check append limit. So the figure shipped as settled when excluding the two
# disputed rows gives a different one.
#
# So: for an aggregate whose own FROM ... WHERE joins two or more loaded tables (the shape the
# single-table block above does NOT cover), the SAME raw rows behind it are re-read, with the
# notes column of whichever joined table has one, plus that table's own columns summed by the
# query's aggregate calls (so a bullet can show what THAT row itself contributed). When 1 to
# ROW_NOTES_MAX_ROWS of those rows - and fewer than all of them - carry a non-blank note, each
# is listed keyed to its own contribution, and the identical query is re-run over a TEMP VIEW
# of the noted table excluding just those rows (`_rerun_with_table_as`, the same primitive
# PRINTED TOTAL ROWS uses above). Both figures are given; the line never says which one
# answers - a note may describe an inference, a dispute or a correction, and only the
# question and the note's own words can decide that. Mutually exclusive with WHAT A ONE-FIGURE
# AGGREGATE COUNTS by construction: that one fires only on a single JOINED-nowhere table, this
# one only when two or more are joined. An error drops the block and nothing else
# (`_companion`). One answer rule reads it. Written from SHAPE: no table, column or value of
# the project is named.
# ---------------------------------------------------------------------------
NOTES_INSIDE_FIGURE_HEADING = "NOTES INSIDE THE FIGURE - "
#: Aggregate function names a per-row value can be read back out of - the ones a bare column
#: argument still means one raw per-row number for (never COUNT(*) or an expression).
_AGGREGATE_FUNCS = frozenset(("SUM", "COUNT", "AVG", "MIN", "MAX"))


def _from_where_span(sql: str):
    """`(start, end)` of `sql`'s own FROM ... WHERE, verbatim - from its one top-level FROM to
    the first clause that follows a WHERE (GROUP BY, ORDER BY, HAVING, LIMIT, ...), or the end
    of `sql` - or None when `sql` is not exactly one top-level SELECT with exactly one FROM and
    no set operation. A sub-SELECT (in the WHERE, say) is read whole: only the query's OWN
    top-level clauses end the span. The JOIN-permitting sibling of `_single_table_query`,
    which this needs only the span from, never the single-table shape."""
    tokens = sql_token_spans(sql)
    if not tokens or _word(tokens[0]) != "SELECT":
        return None
    words = [_word(t) for t in tokens]
    depths = _paren_depths(tokens)
    top = [k for k in range(len(tokens)) if depths[k] == 0]
    if (sum(1 for k in top if words[k] == "SELECT") != 1
            or any(words[k] in _SET_OPERATIONS for k in top)):
        return None
    froms = [k for k in top if words[k] == "FROM"]
    if len(froms) != 1:
        return None
    clause_at = [k for k in top if k > froms[0] and words[k] in _CLAUSES_AFTER_WHERE]
    end = tokens[clause_at[0]][3] if clause_at else len(sql)
    return tokens[froms[0]][3], end


def _aggregate_arg_columns(sql: str) -> list:
    """The columns this query's own aggregate calls (SUM/COUNT/AVG/MIN/MAX) read, in its
    SELECT list, in call order, without duplicates - only when a call's whole argument is one
    column, bare or table-qualified, optionally DISTINCT. `COUNT(*)` and a call over an
    expression (arithmetic, a CASE, two columns, ...) name nothing: there is no single raw
    per-row value to show for either. Only the SELECT list - before the query's own top-level
    FROM - is read, so an aggregate inside a WHERE sub-SELECT is never one of this query's
    own."""
    tokens = sql_token_spans(sql)
    if not tokens:
        return []
    words = [_word(t) for t in tokens]
    depths = _paren_depths(tokens)
    froms = [i for i, w in enumerate(words) if w == "FROM" and depths[i] == 0]
    end = froms[0] if froms else len(tokens)
    found = []
    i = 0
    while i < end:
        if words[i] in _AGGREGATE_FUNCS and i + 1 < end and tokens[i + 1][1] == "(":
            call_depth = depths[i + 1]
            j = i + 2
            if j < end and words[j] == "DISTINCT":
                j += 1
            name = None
            if j < end and _is_name(tokens[j]):
                if j + 2 < end and tokens[j + 1][1] == "." and _is_name(tokens[j + 2]):
                    name, j = tokens[j + 2][2], j + 3
                else:
                    name, j = tokens[j][2], j + 1
            if name is not None and j < end and tokens[j][1] == ")" and depths[j] == call_depth:
                if name not in found:
                    found.append(name)
        i += 1
    return found


def _table_reference(tokens, depths, table_name: str):
    """The alias - or, with none, the bare name as written - `sql`'s first FROM or JOIN names
    `table_name` under: what a column of it must be qualified with to read it out of a JOIN
    unambiguously. None when no FROM or JOIN of this query names that table."""
    low = str(table_name).lower()
    for k in range(len(tokens)):
        if depths[k] == 0 and _word(tokens[k]) in ("FROM", "JOIN"):
            ref = _table_ref(tokens, k + 1)
            if ref and str(ref[0]).lower() == low:
                return ref[1] or ref[0]
    return None


def _aggregate_rows_with_notes(con, sql: str, tables: list):
    """`(table name, notes column, [aggregated column, …], [row, …])` for the first loaded,
    JOINED table behind aggregate `sql` that has a `notes` column and 1 to ROW_NOTES_MAX_ROWS
    - fewer than all - of its rows behind this figure carrying one, or None - see the comment
    above for when this applies. Each returned row is `(note, value of aggregated column 1,
    …)`, read off the SAME raw rows the aggregate counts. Runs one extra query; the caller
    (`_companion`) drops the whole block, and nothing else, if this raises."""
    if not sql_is_aggregate(sql):
        return None
    span = _from_where_span(sql)
    if span is None:
        return None
    tokens = sql_token_spans(sql)
    depths = _paren_depths(tokens)
    read_tables, _ = _query_tables(tokens, depths)
    if len(read_tables) < 2:
        return None  # a single, unjoined table is the block above's job, not this one's
    agg_cols = _aggregate_arg_columns(sql)
    from_where = sql[span[0]:span[1]]
    for name in read_tables:
        table = next((t for t in tables if t["table_name"] == name), None)
        if table is None:
            continue
        notes_col = _column_named(table, "notes")
        if notes_col is None:
            continue
        ref = _table_reference(tokens, depths, name)
        if ref is None:
            continue
        own_agg_cols = [c for c in agg_cols if _column_named(table, c) is not None]
        select_list = [f"{_quoted_name(ref)}.{_quoted_name(notes_col)}"] + [
            f"{_quoted_name(ref)}.{_quoted_name(c)}" for c in own_agg_cols]
        rows = _execute_with_timeout(
            con, f"SELECT {', '.join(select_list)} {from_where}")
        noted = [r for r in rows if r[0] is not None and str(r[0]).strip()]
        if not 1 <= len(noted) <= ROW_NOTES_MAX_ROWS or len(noted) == len(rows):
            continue
        return name, notes_col, own_agg_cols, noted
    return None


def _notes_inside_aggregate_line(con, sql: str, tables: list, col_names, result) -> str:
    """The NOTES INSIDE THE FIGURE block for `sql`, or "" - see the comment above for when it
    applies. One extra query to find the noted rows (`_aggregate_rows_with_notes`), one more
    to recompute the figure without them (`_rerun_with_table_as`); the caller drops the block,
    and nothing else, if either raises."""
    found = _aggregate_rows_with_notes(con, sql, tables)
    if found is None:
        return ""
    table, notes_col, agg_cols, noted = found
    quoted_notes = _quoted_name(notes_col)
    condition = f"({quoted_notes} IS NULL OR TRIM(CAST({quoted_notes} AS VARCHAR)) = '')"
    without = _rerun_with_table_as(con, sql, table, condition)
    if without == result:
        return ""
    shown_without = _inline_result(col_names, without)
    if shown_without is None:
        return ""
    bullets = []
    for row in noted:
        note, values = row[0], row[1:]
        contributed = ", ".join(f"{c} {v}" for c, v in zip(agg_cols, values) if v is not None)
        key = f"{contributed}: " if contributed else ""
        bullets.append(f"- {key}{_cut_note(str(note).strip())}")
    heading = (f"{NOTES_INSIDE_FIGURE_HEADING}{_counted(len(noted), 'row')} behind this figure "
              f"carries a note that may change what it is" if len(noted) == 1 else
              f"{NOTES_INSIDE_FIGURE_HEADING}{_counted(len(noted), 'row')} behind this figure "
              f"carry a note that may change what they are")
    return "\n".join([heading + ":"] + bullets
                     + [f"Without the noted row(s), the same figure is: {shown_without}."])


# ---------------------------------------------------------------------------
# WHAT A ONE-FIGURE AGGREGATE COUNTS - wave 3, F4, 2026-10-03.
#
# Measured on the goal-function run of 2026-10-01: a register's system code was the very type
# word the question asked about, and that system also held the accessories bought with the
# units. "How many <type> units ..." was written as a SUM of the quantity WHERE the system is
# that code; the MATCHED line truthfully said every row had that system, and the answer stated
# units and accessories together as the count of units. The item names the query read told
# them apart; the bare figure could not.
#
# So, for a one-figure aggregate - one row, no GROUP BY, no window - over ONE outer table (a
# sub-SELECT inside its WHERE is fine, and the table is named nowhere else in the query) that
# compares `col = 'lit'` ANDed at the top of its WHERE, where `lit` has at least
# FIGURE_WORD_MIN_CHARS characters and the question prints it as a word, on a table with an
# ITEM column (`_item_column`) that the SQL never names: when `lit` is a whole word in some but
# not all of the item values the query read, the same SQL is re-run twice over a TEMP VIEW of
# the table, as PRINTED TOTAL ROWS does (`_rerun_with_table_as`) - over the rows whose item
# names `lit`, and over the rest - and one line states both figures with what each counts. It
# never says which of them answers (review fix round 1): the trigger cannot tell "how many
# <type>" from "how many things in system <code>", and for a question about the whole system
# the table's own figure is the answer - so the answer rules decide, from the question's wording
# (openai_client.OUTPUT_FORMAT_RULES). When either part holds nothing (`_rows_hold_nothing`) the
# figure already is the other part's and nothing is said: measured over the recorded queries,
# a section banner printing the filter value as its item, with no quantity, was the one other
# place this fired. The figure the query returned is never touched; an error drops the line
# and nothing else. The question is read without the loop's step suffix, whose previous SQL
# quotes the literal back. Written from SHAPE: no table, column or value is named.
# ---------------------------------------------------------------------------
FIGURE_COUNTS_HEADING = "WHAT THE FIGURE COUNTS - "
#: A shorter literal is too often a letter or code that happens to stand inside a name.
FIGURE_WORD_MIN_CHARS = 3
#: How many item names each part of the line lists before it says how many there are.
FIGURE_NAMES_SHOWN = 4
#: Words that, in a column's name, say it names what each row's item is.
_ITEM_NAME_WORDS = frozenset(("item", "items", "description", "descriptions", "device",
                              "devices", "model", "models"))


def _item_column(table: dict, col_types: dict):
    """The table's ITEM column - its first text column whose name has a word saying item,
    description, device or model - or None. Shape only."""
    for column in table.get("columns") or []:
        if (col_types.get(column) == "VARCHAR"
                and _ITEM_NAME_WORDS.intersection(re.findall(r"[a-z]+", str(column).lower()))):
            return column
    return None


def _item_names(values) -> str:
    """Item names as the line lists them: the first FIGURE_NAMES_SHOWN, then how many."""
    text = ", ".join(_clean_shape_value(v) for v in values[:FIGURE_NAMES_SHOWN])
    if len(values) > FIGURE_NAMES_SHOWN:
        text += f", … {len(values)} in all"
    return text


def _figure_counts_line(con, question: str, sql: str, tables: list, col_types: dict,
                        col_names, result) -> str:
    """The WHAT THE FIGURE COUNTS line for `sql`, or "" when it does not apply - see the
    comment above. `col_types` is each loaded table's column types. Reads the item names the
    query read, then re-runs it twice; the caller drops the line, and nothing else, if any of
    that raises."""
    if len(result or []) != 1 or not sql_is_aggregate(sql):
        return ""
    shape = _single_table_query(sql, [t["table_name"] for t in tables], nested=True)
    if (shape is None or shape.clauses & (_FILTERS_AFTER_WHERE | {"GROUP"})
            or "OVER" in {_word(token) for token, _ in shape.select_list}):
        return ""
    named_table = str(shape.table).lower()
    if sum(1 for t in sql_token_spans(sql) or []
           if t[0] in ("word", "qid") and str(t[2]).lower() == named_table) != 1:
        return ""  # the table is read elsewhere in the query too: a view of it would change that
    table = next((t for t in tables if str(t["table_name"]).lower() == named_table), None)
    item = _item_column(table, col_types.get(table["table_name"]) or {}) if table else None
    if item is None or str(item).lower() in _names_in_sql(sql):
        return ""
    text = _split_step_suffix(question)[0]
    quoted = _quoted_name(item)
    for condition in _top_level_conditions(sql):
        parts = _comparison(condition)
        if not parts or parts[2] != "=" or len(parts[3]) != 1:
            continue
        literal = str(parts[3][0][2]).strip()
        if len(literal) < FIGURE_WORD_MIN_CHARS:
            continue
        word = re.compile(r"(?<![A-Za-z0-9])" + re.escape(literal) + r"(?![A-Za-z0-9])",
                          re.IGNORECASE)
        asked = word.search(text)
        if not asked:
            continue
        values = [value for (value,) in _execute_with_timeout(
            con, f"SELECT {quoted} {shape.from_where} GROUP BY {quoted} ORDER BY MIN(rowid)")
            if value is not None and str(value).strip()]
        named = [v for v in values if word.search(str(v))]
        other = [v for v in values if not word.search(str(v))]
        if not named or not other:
            continue
        listed = ", ".join(_sql_literal(v) for v in named)
        part = _rerun_with_table_as(con, sql, table["table_name"], f"{quoted} IN ({listed})")
        rest = _rerun_with_table_as(con, sql, table["table_name"],
                                    f"{quoted} IS NULL OR {quoted} NOT IN ({listed})")
        if _rows_hold_nothing(part) or _rows_hold_nothing(rest):
            # One part adds nothing to the figure - a banner row labelled with the filter value,
            # say - so the figure is already the other part's, and a split says nothing.
            return ""
        shown, shown_other = _inline_result(col_names, part), _inline_result(col_names, rest)
        if shown is None or shown_other is None:
            return ""
        return (f"{FIGURE_COUNTS_HEADING}items named '{asked.group(0)}' "
                f"({_item_names(named)}): {shown}; other items ({_item_names(other)}): "
                f"{shown_other}")
    return ""


# ---------------------------------------------------------------------------
# A RESULT SAYS WHAT ITS ROWS MATCHED - spec-fix10, 2026-10-01.
#
# A query that filters on a text column it does not select returns rows that
# never print what they matched. Measured on the goal-function run of
# 2026-09-30: three rows of areas and a department came back for a place the
# question named, the name appearing only inside the SQL line - which the
# answer rules tell the writer to apply silently - and both runs answered that
# the records held no entry for it. Every value asked for was in the result.
#
# So the result now states the filter. Every plain literal filter that the
# query's OWN WHERE applies to EVERY row it returns - `col = 'x'`, `col LIKE
# 'x'`, `col ILIKE 'x'`, `col IN ('x', ...)`, ANDed at the top of that WHERE
# with no OR beside them, in a query with no set operation - on a column the
# result does not show, is quoted verbatim, column and literal as the SQL wrote
# them, on one line: "MATCHED - every row above has <col> <op> <literal>". It
# never says what the rows ARE: a loose filter stays visibly loose. A filter
# only inside a sub-SELECT, a bracketed group or an OR branch guarantees
# nothing about the rows returned, and is left out. Written from SHAPE: no
# table, column or building is named.
#
# The line is capped at MATCHED_MAX_CHARS (review fix round 1): when it would
# be longer, an IN (...) list with more than MATCHED_IN_SHOWN values shows its
# first values verbatim and how many there are in all, and a line still too
# long is cut and ends with an ellipsis.
# ---------------------------------------------------------------------------
MATCHED_HEADING = "MATCHED - every row above has "
MATCHED_MAX_CHARS = 300
MATCHED_IN_SHOWN = 3

#: Words that read as a name by shape and can never be the column a filter compares.
_NOT_A_COLUMN = frozenset(("NOT", "TRUE", "FALSE", "NULL"))


def _literal_filter_column(condition) -> str:
    """The column `condition` - one ANDed condition's tokens - compares with string
    literals, when it is exactly `col = 'x'`, `col LIKE 'x'`, `col ILIKE 'x'` or
    `col IN ('x', ...)` (the column optionally qualified); "" for any other shape: a
    function around the column, a negation, a number, a literal that goes on into an
    expression, IN (SELECT ...)."""
    n = len(condition)

    def is_name(k):
        return k < n and (condition[k][0] == "qid"
                          or (condition[k][0] == "word"
                              and _word(condition[k]) not in _NOT_A_COLUMN))

    if not is_name(0):
        return ""
    column, j = condition[0][2], 1
    if j + 1 < n and condition[j][1] == "." and is_name(j + 1):
        column, j = condition[j + 1][2], j + 2
    if j >= n:
        return ""
    op = _word(condition[j]) or condition[j][1]
    if op in ("=", "LIKE", "ILIKE"):
        return column if j + 2 == n and condition[j + 1][0] == "str" else ""
    items = condition[j + 2:-1]
    if (op != "IN" or j + 1 >= n or condition[j + 1][1] != "(" or condition[-1][1] != ")"
            or len(items) % 2 == 0):
        return ""
    for k, token in enumerate(items):
        if (k % 2 == 0 and token[0] != "str") or (k % 2 == 1 and token[1] != ","):
            return ""
    return column


def _shortened_condition(sql: str, condition) -> str:
    """`condition` (one ANDed condition's tokens) as written in `sql` - or, for an
    IN (...) list of more than MATCHED_IN_SHOWN values, written up to its bracket with its
    first values verbatim and how many there are in all."""
    written = sql[condition[0][3]:condition[-1][4]]
    values = [t for t in condition if t[0] == "str"]
    opener = next((t for t in condition if t[1] == "("), None)
    if opener is None or len(values) <= MATCHED_IN_SHOWN:
        return written
    first = ", ".join(t[1] for t in values[:MATCHED_IN_SHOWN])
    return f"{sql[condition[0][3]:opener[4]]}{first}, … {len(values)} values in all)"


def _top_level_conditions(sql: str) -> list:
    """The conditions ANDed at the top of the query's own single WHERE, each as its list of
    tokens (`sql_token_spans`), in the order written - or [] when the query holds a set
    operation, has no WHERE or more than one at its top, or has an OR at the top of its WHERE:
    then no one condition is met by every row it returns."""
    tokens = sql_token_spans(sql)
    if not tokens:
        return []
    words = [_word(t) for t in tokens]
    depths = _paren_depths(tokens)
    top = [k for k in range(len(tokens)) if depths[k] == 0]
    if any(words[k] in _SET_OPERATIONS for k in top):
        return []
    wheres = [k for k in top if words[k] == "WHERE"]
    if len(wheres) != 1:
        return []
    start = wheres[0] + 1
    end = next((k for k in top if k > wheres[0] and words[k] in _CLAUSES_AFTER_WHERE),
               len(tokens))
    if any(words[k] == "OR" for k in top if start <= k < end):
        return []
    conditions, current = [], []
    for k in range(start, end):
        if depths[k] == 0 and words[k] == "AND":
            conditions.append(current)
            current = []
        else:
            current.append(k)
    conditions.append(current)
    return [[tokens[k] for k in ks] for ks in conditions if ks]


def _matched_conditions(sql: str) -> list:
    """`[(column, condition as written, condition shortened, where it starts in sql), …]` -
    every plain literal filter the query's own WHERE applies to every row it returns (see the
    comment above), in the order written; the shortened form abbreviates a long IN (...)
    list."""
    found = []
    for condition in _top_level_conditions(sql):
        column = _literal_filter_column(condition)
        if column:
            found.append((column, sql[condition[0][3]:condition[-1][4]],
                          _shortened_condition(sql, condition), condition[0][3]))
    return found


# ---------------------------------------------------------------------------
# A ROW LIST SHOWS WHAT ITS PATTERN MATCHED - wave 3, G5 (1), 2026-10-03.
#
# Measured on the goal-function run of 2026-10-01: a pattern filter on a name column the SELECT
# left out (`name ILIKE '%A B%'`) also caught a longer name ('C A B'); the MATCHED line quoted the
# filter, and its answer rule says a pattern's rows are not to be presented as the asked-for thing
# without saying what they matched - which the result could not say, since no row printed its
# name. Both runs refused.
#
# So a ROW LIST - one row per row read: no aggregate (a window function is none), no GROUP BY or
# HAVING, no DISTINCT - whose top-level WHERE (`_top_level_conditions`, the conditions the MATCHED
# line reads) applies a pattern filter - LIKE or ILIKE whose literal holds a wildcard, `%` or `_` -
# to a column the result does not show is re-run with that column appended to its SELECT list, as
# the writer wrote it (qualified or not), each once. The rows are the same rows, in the same order,
# each now printing the value it matched; the re-run becomes the result and its SQL line, as F1's
# widening does, and every later line reads it. The MATCHED line still states the filter, from the
# writer's own columns. A result that holds nothing (`_rows_hold_nothing`) is never re-run - its SQL
# line is the one the loop's EMPTY re-query hands back to the writer (found by the replay of the
# recorded queries) - and a re-run returning a different number of rows, or an error, keeps the
# result as it was (`_companion`). Written from SHAPE: no table, column or value is named.
#
# A COLUMN THAT ONLY REPEATS ITS PATTERN IS NOT ADDED - wave 4, A1, 2026-10-03. Measured on the
# goal-function run of that day: a pattern matched one name that two different places print, the
# column added here printed that same name on every row, and the answer merged the two places into
# one - where every earlier answer, given the rows without that column, had named both. A column
# whose every returned value equals the pattern's literal core - the literal with its leading and
# trailing wildcards stripped - once case and white space are folded (`_loose_fold`) says nothing
# the MATCHED line does not already say, so it is dropped from what is added; when nothing is left
# to add, the writer's own result stands, byte for byte. A core with a wildcard inside it keeps its
# column, as before, and so does a column holding any longer value: that is what the column is for.
# ---------------------------------------------------------------------------
#: The characters that make a LIKE / ILIKE literal a pattern rather than a whole value.
PATTERN_WILDCARDS = ("%", "_")


def _pattern_core(literal):
    """The text a LIKE / ILIKE literal matches once its leading and trailing wildcards are
    stripped - or None when a wildcard stands inside it as well, or nothing is left."""
    core = str(literal).strip("".join(PATTERN_WILDCARDS))
    if not core.strip() or any(mark in core for mark in PATTERN_WILDCARDS):
        return None
    return core


def _repeats_pattern(values, cores) -> bool:
    """True when one of `cores` equals every one of `values` once case and white space are folded
    (`_loose_fold`): a column holding them would only repeat the pattern it was filtered with."""
    if not values or any(value is None for value in values):
        return False
    folded = {_loose_fold(value) for value in values}
    return any(folded == {_loose_fold(core)} for core in cores)


def _matched_column_rerun(con, sql: str, col_names, result):
    """`(re-run SQL, rows, column names)` - `sql` re-run with each column a top-level pattern
    filter compares but the result leaves out appended to its SELECT list (see the comment
    above) - or None when there is no such column, the query is no row list, the result holds
    nothing, the re-run returns another number of rows, or every column it adds would only
    repeat its pattern's literal (wave 4, A1). Runs at most one query."""
    if _rows_hold_nothing(result):
        return None  # an empty result keeps the writer's SQL line, which the loop hands back
    tokens = sql_token_spans(sql)
    if not tokens or _word(tokens[0]) != "SELECT":
        return None
    depths = _paren_depths(tokens)
    words = [_word(t) for t in tokens]
    top = [k for k in range(len(tokens)) if depths[k] == 0]
    froms = [k for k in top if words[k] == "FROM"]
    if (len(froms) != 1 or (len(words) > 1 and words[1] == "DISTINCT")
            or any(words[k] in ("GROUP", "HAVING") for k in top)):
        return None
    if sql_is_aggregate(sql) and "OVER" not in {words[k] for k in top if k < froms[0]}:
        return None
    shown = {str(c).lower() for c in col_names}
    added = []
    cores = {}  # each added column, lower-cased as written -> the cores of its patterns
    for condition in _top_level_conditions(sql):
        parts = _comparison(condition)
        if (not parts or parts[2] not in ("LIKE", "ILIKE")
                or not any(mark in str(parts[3][0][2]) for mark in PATTERN_WILDCARDS)
                or str(parts[1]).lower() in shown):
            continue
        written = sql[condition[0][3]:parts[4]]
        if written.lower() not in {a.lower() for a in added}:
            added.append(written)
        core = _pattern_core(parts[3][0][2])
        if core is not None:
            cores.setdefault(written.lower(), []).append(core)
    if not added:
        return None
    start = tokens[froms[0]][3]
    rerun = f"{sql[:start].rstrip()}, {', '.join(added)} {sql[start:]}"
    rows = _execute_with_timeout(con, rerun)
    if len(rows) != len(result or []):
        return None
    names = [d[0] for d in con.description]
    width = len(names) - len(added)
    if width != len(col_names):
        return rerun, rows, names  # the columns do not line up: added as before
    kept = [k for k, written in enumerate(added)
            if not _repeats_pattern([row[width + k] for row in rows],
                                    cores.get(written.lower(), ()))]
    if not kept:
        return None  # every added column only repeats its pattern: the writer's result stands
    if len(kept) < len(added):
        rerun = f"{sql[:start].rstrip()}, {', '.join(added[k] for k in kept)} {sql[start:]}"
        rows = [tuple(row[:width]) + tuple(row[width + k] for k in kept) for row in rows]
        names = names[:width] + [names[width + k] for k in kept]
    return rerun, rows, names


def _matched_line(sql: str, col_names, result_text: str, spellings=None) -> str:
    """The MATCHED line for `sql`, or "" - when no such filter is on a column the result
    leaves out, or the result holds nothing (all-empty cells, or a lone zero: what the
    loop reads as an empty result). At most MATCHED_MAX_CHARS long: written whole when it
    fits, else with long IN lists shortened, else cut. Pure: no query is run.

    `spellings` - from `_case_variant_rerun`, keyed by where each condition it widened starts
    in `sql`, the query as WRITTEN - names every spelling such a condition matched, with its
    rows (wave 3, F1). A widened condition is stated whether or not the result shows its
    column: the rows above print two forms of one value."""
    if result_is_empty(result_text):
        return ""
    shown = {str(c).lower() for c in col_names}
    stated = []
    for column, written, shortened, start in _matched_conditions(sql):
        held = (spellings or {}).get(start)
        if held:
            stated.append(_spellings_statement(written, shortened, held))
        elif str(column).lower() not in shown:
            stated.append((written, shortened))
    if not stated:
        return ""
    line = MATCHED_HEADING + " AND ".join(written for written, _ in stated)
    if len(line) > MATCHED_MAX_CHARS:
        line = MATCHED_HEADING + " AND ".join(shortened for _, shortened in stated)
    if len(line) > MATCHED_MAX_CHARS:
        line = line[:MATCHED_MAX_CHARS - 1].rstrip() + "…"
    return line


# ---------------------------------------------------------------------------
# ONE VALUE, SEVERAL SPELLINGS - wave 3, F1, 2026-10-03.
#
# Measured on the goal-function run of 2026-10-01: a short text column printed one value in
# two letter cases, each on some of its rows, and the schema block, which lists such a
# column's every value as complete, sent the writer to an exact filter on the spelling the
# question used. The result held only the rows of that one spelling, and the answer stated
# their count and area as the whole.
#
# So the filter is checked in code once the query has run. Every plain `col = 'x'` or
# `col IN ('x', ...)` ANDed at the top of the query's own WHERE (`_top_level_conditions`, the
# conditions the MATCHED line reads) on a text column of a loaded table is probed: when the
# column also holds a value equal to the literal once letter case and surrounding spaces are
# folded - `lower(trim(...))`, the very expression the re-run uses, so the probe and the
# re-run cannot disagree - the same SQL is re-run with only that predicate widened to
# `lower(trim(col)) = lower(trim('x'))` (an IN list: every literal so), and that result is
# returned with the widened SQL. The MATCHED line names every spelling the table prints, with
# its rows. A column holding no variant is never touched; a widened query that still returns
# nothing is dropped; an error drops the widening and nothing else (`_companion`). Written
# from SHAPE: no table, column or value is named. Grouping the spellings in the schema block
# instead is deferred: it would change the writer's input for every question routing such a
# table.
#
# The probe is two-stage so that a column with no variant costs no query at all: the rows
# already loaded are first read in Python with a LOOSER fold (`_loose_fold`: every white space
# removed, case folded), and only a column where that finds another spelling is asked of
# DuckDB, whose own `lower(trim(...))` - which no Python fold reproduces exactly: it trims a
# no-break space, and lower-cases some letters differently - then decides.
# ---------------------------------------------------------------------------
#: How a widened condition is stated on the MATCHED line, before the spellings it matched.
CASE_VARIANTS_NOTE = "in any letter case or spacing: the table prints "


def _comparison(condition):
    """`(qualifier or None, column, operator, [literal token, …], where the column ends in the
    SQL)` of one plain literal filter - the four shapes `_literal_filter_column` reads, the
    operator upper-cased ('=', 'LIKE', 'ILIKE' or 'IN') - or None for any other condition."""
    if not condition or not _literal_filter_column(condition):
        return None
    qualifier, j = None, 1
    if len(condition) > 3 and condition[1][1] == ".":
        qualifier, j = condition[0][2], 3
    operator = _word(condition[j]) or condition[j][1]
    return (qualifier, condition[j - 1][2], operator,
            [t for t in condition[j + 1:] if t[0] == "str"], condition[j - 1][4])


def _query_tables(tokens, depths) -> tuple:
    """`(tables, aliases)` the query's own FROM and JOINs name (depth 0), read with `sql_loop`'s
    own table reader: `aliases` maps every table name and alias, lower-cased, to its table as
    written. A FROM or JOIN on a sub-SELECT names nothing."""
    tables, aliases = [], {}
    for k, token in enumerate(tokens):
        if depths[k] == 0 and _word(token) in ("FROM", "JOIN"):
            _declare_tables(tokens, k + 1, tables, aliases)
    return tables, aliases


def _filter_table(qualifier, column, query_tables, aliases, loaded: dict):
    """The loaded table (its dict) a top-level filter's column belongs to - the table its
    qualifier names, or the one table of the query's own FROM and JOINs that has that column
    - or None when that is not exactly one loaded table."""
    if qualifier:
        named = aliases.get(str(qualifier).lower())
        candidates = [named] if named else []
    else:
        candidates = query_tables
    owners = {}
    for name in candidates:
        table = loaded.get(str(name).lower())
        if table is not None and _column_named(table, column) is not None:
            owners[str(table["table_name"]).lower()] = table
    return next(iter(owners.values())) if len(owners) == 1 else None


def _loose_fold(value) -> str:
    """A value with every white space removed and its case folded: looser than the re-run's
    `lower(trim(...))`, so a column in which it finds no other spelling of a literal holds
    none for DuckDB either - bar exotic case mappings, which are then simply not widened."""
    return re.sub(r"\s+", "", str(value)).casefold()


def _may_hold_variant(table: dict, col, literals) -> bool:
    """True when the loaded rows of `table` hold, in `col`, a value other than the literals
    themselves that `_loose_fold` makes equal to one of them - read in Python off the rows
    already fetched, so a column with no such value costs no query."""
    wanted = {_loose_fold(t[2]) for t in literals}
    own = {t[2] for t in literals}
    for row in table.get("rows") or []:
        value = _loaded_value(row.get(col), "VARCHAR")
        if value is not None and value not in own and _loose_fold(value) in wanted:
            return True
    return False


def _case_variant_rerun(con, sql: str, tables: list, col_types: dict):
    """`(widened SQL, rows, column names, spellings)` - `sql` re-run with every top-level
    equality whose text column also holds another spelling of its literal widened (see the
    comment above) - or None when there is no such column, or the widened query returns
    nothing. `spellings` maps where each widened condition starts in `sql` to
    `[(literal, [(spelling, rows), …]), …]`. No query runs for a column whose loaded rows hold
    no other spelling (`_may_hold_variant`); for one that may, one probe per literal, and the
    widened query when one varies."""
    tokens = sql_token_spans(sql)
    if not tokens or _word(tokens[0]) != "SELECT":
        return None
    query_tables, aliases = _query_tables(tokens, _paren_depths(tokens))
    loaded = {str(t["table_name"]).lower(): t for t in tables}
    edits, spellings = [], {}
    for condition in _top_level_conditions(sql):
        parts = _comparison(condition)
        if not parts or parts[2] not in ("=", "IN") or not parts[3]:
            continue
        qualifier, column, operator, literals, column_end = parts
        if any(not str(t[2]).strip() for t in literals):
            continue  # a blank literal asks for blank cells, which folding would widen
        table = _filter_table(qualifier, column, query_tables, aliases, loaded)
        if table is None:
            continue
        col = _column_named(table, column)
        if ((col_types.get(table["table_name"]) or {}).get(col) != "VARCHAR"
                or not _may_hold_variant(table, col, literals)):
            continue
        held, varies = [], False
        for token in literals:
            rows = _execute_with_timeout(con, (
                f"SELECT {_quoted_name(col)}, COUNT(*) FROM {_quoted_name(table['table_name'])} "
                f"WHERE lower(trim({_quoted_name(col)})) = lower(trim({token[1]})) "
                f"GROUP BY {_quoted_name(col)} ORDER BY MIN(rowid)"))
            found = [(str(value), int(n)) for value, n in rows]
            held.append((token[2], found))
            varies = varies or any(value != token[2] for value, _ in found)
        if not varies:
            continue
        written = sql[condition[0][3]:column_end]
        folded = [f"lower(trim({t[1]}))" for t in literals]
        edits.append((condition[0][3], condition[-1][4],
                      f"lower(trim({written})) = {folded[0]}" if operator == "=" else
                      f"lower(trim({written})) IN ({', '.join(folded)})"))
        spellings[condition[0][3]] = held
    if not edits:
        return None
    widened = sql
    for start, end, text in reversed(edits):
        widened = widened[:start] + text + widened[end:]
    rows = _execute_with_timeout(con, widened)
    if not rows:
        return None
    return widened, rows, [d[0] for d in con.description], spellings


def _spellings_statement(written: str, shortened: str, held) -> tuple:
    """`(statement, shortened statement)` of a widened condition on the MATCHED line: the
    condition as written, then every spelling the table prints for its literals - each once,
    in the order the table first holds them - with its rows; the shortened form counts them."""
    forms = {}
    for _, found in held:
        for spelling, rows in found:
            forms.setdefault(spelling, rows)
    if not forms:
        return written, shortened
    listed = _and_list(f"{_sql_literal(s)} on {_counted(n, 'row')}" for s, n in forms.items())
    total = sum(forms.values())
    return (f"{written} ({CASE_VARIANTS_NOTE}{listed})",
            f"{shortened} ({CASE_VARIANTS_NOTE}{_counted(len(forms), 'spelling')} on "
            f"{_counted(total, 'row')})")


def _and_list(parts) -> str:
    """'a', 'a and b', 'a, b and c'."""
    parts = list(parts)
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _entries(n: int) -> str:
    """'1 entry', '2 entries' - a count of the rows holding a value, in a word no router card
    scores (the LITERAL ELSEWHERE line is routed on, through the loop's re-query)."""
    return f"{n} entr{'y' if n == 1 else 'ies'}"


# ---------------------------------------------------------------------------
# A VALUE LOOKED FOR IN A COLUMN THAT NEVER HOLDS IT - wave 3, F2, 2026-10-03.
#
# Measured on the goal-function run of 2026-10-01: a board's code was compared with the PARENT
# column of a table where it only ever appears as a child - inside one arm of a set operation,
# and, in the second run, on the unmatched side of an outer join. That comparison found
# nothing, the other arm's row filled the result, the result was not empty, so no loop check
# fired, and the answer called the other arm's figure the one asked for.
#
# So, for each SELECT a set operation joins at the top of the query (`_query_branches`; the
# whole query when there is none), every literal its WHERE clauses - its sub-SELECTs' included
# - compare with `=` or `IN` is placed in the table its column belongs to by `sql_loop`'s own
# reader (`_filtered_columns`), and grouped by that table. A literal that none of the text
# columns it is compared with in a table T holds - in any letter case or spacing - though
# ANOTHER text column of T does, gets one line:
#
#     LITERAL ELSEWHERE - '<lit>' never appears in T.<col>, though it does in T.<col2>
#     (N entries)[; elsewhere in U.<c> (M entries), ...]. If the question meant the relation
#     T.<col2> holds, query T.<col2>; if it meant the relation T.<col> holds, that relation is
#     genuinely empty for '<lit>' and its empty answer stands
#
# - "elsewhere": the other loaded tables' columns that hold it, those the query already compares
# it with left out, the most entries first. BOTH READINGS, never one (review fix round 1, ruling
# W3A1-R1): the empty relation may be honest - "what did it feed then, and what now?" asked of a
# board that fed nothing then fires exactly the same way - so the line points at where the
# literal is held without calling the empty part wrong. Its fixed words score on no router card
# ("value" and "rows" are card words; "entries" are not): `sql_loop` routes each re-query on its
# own text. `sql_loop` raises the line as an issue even on a result with
# rows, and re-queries with the sentence; with no step left the line stays where it is, in the
# result the answer writer reads. Nothing is said for a literal no column of T holds (an empty
# result is EMPTY's to report), nor when every column of T that holds it is compared with it
# somewhere in the query already - the query reads those rows. The loaded rows are read in
# Python first (`_loose_fold`), so a literal its compared column holds - nearly every one -
# costs no query; DuckDB's own `lower(trim(...))` then counts the rows a line states. Written
# from SHAPE: no table, column or value is named.
# ---------------------------------------------------------------------------
#: At most this many LITERAL ELSEWHERE lines on one result.
LITERAL_ELSEWHERE_MAX = 3
#: At most this many of the other loaded tables' holding columns named on one line.
LITERAL_HOLDERS_SHOWN = 4


def _query_branches(sql: str) -> list:
    """The text of each SELECT a set operation joins at the top of `sql`, cut out verbatim -
    the whole query when it holds none - or [] when `sql` cannot be read."""
    tokens = sql_token_spans(sql)
    if not tokens:
        return []
    depths = _paren_depths(tokens)
    cuts = [k for k, token in enumerate(tokens)
            if depths[k] == 0 and _word(token) in _SET_OPERATIONS]
    branches, start = [], 0
    for cut in cuts + [len(tokens)]:
        if start < cut:
            branches.append(sql[tokens[start][3]:tokens[cut - 1][4]])
        start = cut + 1
        if start < len(tokens) and _word(tokens[start]) in ("ALL", "DISTINCT"):
            start += 1
    return branches


def _held_in(table: dict, columns, literal) -> list:
    """The `columns` of `table` whose loaded rows hold `literal`, exactly or under
    `_loose_fold` - read in Python off the rows already fetched, so it costs no query."""
    wanted = _loose_fold(literal)
    held = []
    for column in columns:
        for row in table.get("rows") or []:
            value = _loaded_value(row.get(column), "VARCHAR")
            if value is not None and (value == literal or _loose_fold(value) == wanted):
                held.append(column)
                break
    return held


def _held_counts(con, table_name: str, columns, literal) -> dict:
    """{column: the rows of the table whose `column` holds `literal` under DuckDB's own
    `lower(trim(...))`} for `columns`, in one query."""
    value = _sql_literal(literal)
    counts = ", ".join(f"COUNT(*) FILTER (WHERE lower(trim({_quoted_name(c)})) = "
                       f"lower(trim({value})))" for c in columns)
    row = _execute_with_timeout(con, f"SELECT {counts} FROM {_quoted_name(table_name)}")[0]
    return {c: int(n) for c, n in zip(columns, row)}


def _literal_elsewhere_lines(con, sql: str, tables: list, col_types: dict) -> str:
    """The LITERAL ELSEWHERE lines for `sql`, or "" - see the comment above. `col_types` is
    each loaded table's column types. Runs a query only for a literal its compared columns do
    not hold and another column of their table may; the caller drops the lines, and nothing
    else, if anything raises."""
    loaded = {str(t["table_name"]).lower(): t for t in tables if t.get("rows")}
    texts = {key: [c for c in t.get("columns") or []
                   if (col_types.get(t["table_name"]) or {}).get(c) == "VARCHAR"]
             for key, t in loaded.items()}
    cards = [{"table": t["table_name"], "columns": list(t.get("columns") or [])}
             for t in loaded.values()]
    compared = {}  # literal -> every (table, column) the query compares it with, anywhere
    for table, column, literals in _filtered_columns(sql, cards, ops=("=", "IN")):
        for literal in literals:
            compared.setdefault(literal, set()).add((str(table).lower(), str(column).lower()))
    sentences = []
    for branch in _query_branches(sql):
        groups = {}
        for table, column, literals in _filtered_columns(branch, cards, ops=("=", "IN")):
            key = str(table).lower()
            col = _column_named(loaded[key], column) if key in loaded else None
            if col is None or col not in texts[key]:
                continue
            for literal in literals:
                if str(literal).strip():
                    cols = groups.setdefault((key, literal), [])
                    if col not in cols:
                        cols.append(col)
        for (key, literal), cols in groups.items():
            table = loaded[key]
            others = [c for c in texts[key] if c not in cols]
            if _held_in(table, cols, literal) or not _held_in(table, others, literal):
                continue
            counts = _held_counts(con, table["table_name"], cols + others, literal)
            holders = [(c, counts[c]) for c in others if counts[c]]
            if (any(counts[c] for c in cols) or not holders
                    or all((key, c.lower()) in compared.get(literal, ()) for c, _ in holders)):
                continue
            elsewhere = []
            for order, (other_key, other) in enumerate(loaded.items()):
                if other_key == key:
                    continue
                candidates = _held_in(other, [c for c in texts[other_key] if (
                    other_key, c.lower()) not in compared.get(literal, ())], literal)
                if candidates:
                    for c, n in _held_counts(con, other["table_name"], candidates,
                                             literal).items():
                        if n:
                            elsewhere.append((-n, order, f"{other['table_name']}.{c}", n))
            # Worded with no word a router card scores ("value", "rows", "other", "tables" are
            # card words): the loop routes its re-query on this sentence. Both readings, and
            # neither called wrong (ruling W3A1-R1).
            name, value = table["table_name"], _sql_literal(literal)
            empty_side = " or ".join(f"{name}.{c}" for c in cols)
            held_side = " or ".join(f"{name}.{c}" for c, _ in holders)
            sentence = (f"{value} never appears in {empty_side}, though it does in "
                        + _and_list(f"{name}.{c} ({_entries(n)})" for c, n in holders))
            if elsewhere:
                elsewhere.sort()
                sentence += ("; elsewhere in "
                             + _and_list(f"{where} ({_entries(n)})"
                                         for _, _, where, n in elsewhere[:LITERAL_HOLDERS_SHOWN]))
                if len(elsewhere) > LITERAL_HOLDERS_SHOWN:
                    sentence += f", … {len(elsewhere)} in all"
            sentence += (f". If the question meant the relation {held_side} holds, query "
                         f"{held_side}; if it meant the relation {empty_side} holds, that relation "
                         f"is genuinely empty for {value} and its empty answer stands")
            if sentence not in sentences:
                sentences.append(sentence)
    return "\n".join(LITERAL_ELSEWHERE_LEAD + s for s in sentences[:LITERAL_ELSEWHERE_MAX])


# ---------------------------------------------------------------------------
# A CLASS WORD WRITTEN AS ONE KIND CODE - wave 3, G1, 2026-10-03.
#
# Measured on the goal-function runs of 2026-09-30 and 2026-10-01: a class word in the question
# was written as ONE code of a table's kind column - the plain code - and every row of the codes
# made by putting a qualifier in front of it was left out of the list, and out of the count.
# KIND_WORDS_RULE says exactly this to the writer, and in three measured runs it had not taken;
# so the result now says it, in code.
#
# For each plain `col = 'C'` / `col IN ('C', ...)` ANDed at the top of the query's own WHERE
# (`_top_level_conditions`, the conditions the MATCHED line reads) on a text column of a loaded
# table, a literal C is looked at when ALL of these hold:
#   * C is a CODE (`_is_code`): two or more characters, a letter, no lower-case letter and no
#     white space - so an ordinary word, which another word can end, never roots a family;
#   * C is held by more than one row of the table: a kind, never one thing's own name, which can
#     end another thing's name without being its kind;
#   * C is the ROOT of a family in that column: no other value of the column is a shorter ending
#     of C - a code with a qualifier in front of it is never a root;
#   * the question - read without the loop's step suffix - does not print C as a word, plurals
#     included: a question printing the code asked for that code.
# Then, for each FAMILY MEMBER X - a longer code of the same column that ends in C, in the order
# the table first holds them, and not already named by the filter - the same SQL is re-run once
# with that one condition written `col = 'X'`. Each re-run that returns something - not no rows,
# not only NULLs, blanks and zeros - adds one line saying what the filter left out and what the
# same query returns for it. The CONDITION is rewritten rather than the table shadowed, so a
# selected kind column prints each row's own kind, and the table's other reads are untouched.
# The query's own rows are never touched; an error drops the lines and nothing else
# (`_companion`). One answer rule reads the line (openai_client.OUTPUT_FORMAT_RULES). Written from
# SHAPE: no table, column, code or value is named.
#
# A HIERARCHICAL TABLE GETS COUNTS, NEVER FIGURES (review fix round 1, ruling W3A2-R1). On a table
# with a parent-reference column (`_is_parent_column`, the hierarchy note's own test) the longer
# codes are often the tiers ABOVE the plain one - a sub-main or main one - and each of their rows'
# figures already includes the rows below it. A total of a measure for the plain code, with the
# upper tiers' totals beside it, invited an answer that added them: the hierarchy counted twice.
# So on such a table an AGGREGATE's line gives only how many rows of that kind the same filter
# matches (`SELECT COUNT(*)` over the re-run's own FROM ... WHERE, read with
# `_single_table_query`; a query joining tables, whose rows are not that table's rows, gets no
# line), and says why its figures are left out. A count of the whole class still wants them: a
# sub-main one of the class is one of the class. A row LIST keeps its rows - rows, not sums - and
# a table with no parent-reference column keeps the query's own figures.
# ---------------------------------------------------------------------------
OTHER_KINDS_HEADING = "OTHER KINDS - "
#: At most this many OTHER KINDS lines on one result.
OTHER_KINDS_MAX = 4
#: At most this many of a re-run's rows are written into its line.
OTHER_KINDS_ROWS_SHOWN = 10


def _is_code(value) -> bool:
    """True when `value` reads as a code: at least two characters, a letter, no lower-case letter
    and no white space. Shape only."""
    text = str(value or "")
    return (len(text) >= 2 and any(ch.isalpha() for ch in text)
            and not any(ch.islower() or ch.isspace() for ch in text))


def _prints_word(text: str, word: str) -> bool:
    """True when `text` prints `word` as a whole word, in any letter case, plurals included."""
    return bool(re.search(r"(?<![A-Za-z0-9])" + re.escape(word) + r"(?:'?s|es)?(?![A-Za-z0-9])",
                          text or "", re.IGNORECASE))


def _holds_something(rows) -> bool:
    """True when a re-run returned something: a cell that is not NULL, blank or a zero."""
    for row in rows or []:
        for value in row:
            text = "" if value is None else str(value).strip()
            if not text:
                continue
            try:
                if float(text) == 0:
                    continue
            except ValueError:
                pass
            return True
    return False


def _shown_rows(col_names, rows) -> str:
    """A re-run's rows written into one line - at most OTHER_KINDS_ROWS_SHOWN of them, then how
    many there are in all."""
    shown = _inline_result(col_names, rows[:OTHER_KINDS_ROWS_SHOWN])
    if len(rows) > OTHER_KINDS_ROWS_SHOWN:
        shown += f", … {len(rows)} rows in all"
    return shown


def _other_kinds_lines(con, question: str, sql: str, tables: list, col_types: dict,
                       col_names) -> str:
    """The OTHER KINDS lines for `sql`, or "" - see the comment above. `col_types` is each loaded
    table's column types; `col_names` the query's own result columns, which a re-run shares (only
    one condition of its WHERE changes). Reads the loaded rows in Python, and re-runs the query
    once per family member it looks at; the caller drops the lines, and nothing else, if anything
    raises."""
    tokens = sql_token_spans(sql)
    if not tokens or _word(tokens[0]) != "SELECT":
        return ""
    query_tables, aliases = _query_tables(tokens, _paren_depths(tokens))
    loaded = {str(t["table_name"]).lower(): t for t in tables}
    text = _split_step_suffix(question)[0]
    lines = []
    for condition in _top_level_conditions(sql):
        parts = _comparison(condition)
        if not parts or parts[2] not in ("=", "IN") or not parts[3]:
            continue
        qualifier, column, _, literals, column_end = parts
        table = _filter_table(qualifier, column, query_tables, aliases, loaded)
        if table is None:
            continue
        col = _column_named(table, column)
        if (col_types.get(table["table_name"]) or {}).get(col) != "VARCHAR":
            continue
        held = []  # the column's values, in the order the table first holds them, with their rows
        rows_of = {}
        for row in table.get("rows") or []:
            value = _loaded_value(row.get(col), "VARCHAR")
            if value is None or not str(value).strip():
                continue
            if value not in rows_of:
                held.append(value)
            rows_of[value] = rows_of.get(value, 0) + 1
        named = {str(t[2]).strip().upper() for t in literals}
        written = sql[condition[0][3]:column_end]
        # Ruling W3A2-R1: an aggregate over a hierarchical table is told how many rows of each
        # other kind its filter matches, and never that kind's figures (see the comment above).
        counting = (any(_is_parent_column(c) for c in table.get("columns") or [])
                    and sql_is_aggregate(sql))
        for token in literals:
            code = str(token[2])
            root = code.strip().upper()
            if (not _is_code(code) or rows_of.get(code, 0) < 2 or _prints_word(text, code.strip())
                    or any(len(v.strip()) < len(root) and root.endswith(v.strip().upper())
                           for v in held)):
                continue
            for member in held:
                folded = member.strip().upper()
                if len(folded) <= len(root) or not folded.endswith(root) or folded in named:
                    continue
                rerun = (sql[:condition[0][3]] + f"{written} = {_sql_literal(member)}"
                         + sql[condition[-1][4]:])
                lead = (f"{OTHER_KINDS_HEADING}{_shortened_condition(sql, condition)} leaves out "
                        f"the kind {_sql_literal(member)}, a longer code ending in "
                        f"{_sql_literal(code)}; ")
                if counting:
                    shape = _single_table_query(rerun, [t["table_name"] for t in tables],
                                                nested=True)
                    if shape is None:
                        continue  # joined tables: their rows are not that table's rows
                    n = _execute_with_timeout(con, f"SELECT COUNT(*) {shape.from_where}")[0][0]
                    if not n:
                        continue
                    lines.append(
                        f"{lead}the same filter matches {_counted(int(n), 'row')} of kind "
                        f"{_sql_literal(member)}. The table is hierarchical, so that kind's figures "
                        f"are not given here: a row's figures already include the rows below it. "
                        f"When the question names the whole class rather than the code "
                        f"{_sql_literal(code)} itself, these rows count too, each under its own "
                        f"kind.")
                else:
                    rows = _execute_with_timeout(con, rerun)
                    if not _holds_something(rows):
                        continue
                    lines.append(
                        f"{lead}run for {_sql_literal(member)}, the same query returns: "
                        f"{_shown_rows(col_names, rows)}. When the question names the whole class "
                        f"rather than the code {_sql_literal(code)} itself, these rows belong in "
                        f"the answer too, each named with its kind.")
                if len(lines) >= OTHER_KINDS_MAX:
                    return "\n".join(lines)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# KINDS A WORD PATTERN LEFT OUT OF ITS SCOPE - wave 4, A3, 2026-10-03.
#
# Measured on the goal-function runs of 2026-10-03: asked how many of two kinds of thing one storey
# holds, the writer narrowed the table's kind column with a word pattern of each asked word -
# `kind ILIKE '%a%' OR kind ILIKE '%b%'` - though the schema block had listed that column's complete
# values. One kind of the asked class prints neither word, so its row was left out of the figure,
# and the query gave no count per kind. KIND_WORDS_RULE says a class word covers every kind of its
# class; no line could see this one: MATCHED leaves out a bracketed OR, WHAT THE FIGURE COUNTS
# needs '=', and OTHER KINDS needs one kind code.
#
# So, for ONE plain SELECT over one loaded table (`_single_table_query`, sub-SELECTs allowed) whose
# top-level WHERE (`_top_level_conditions`) holds, as one ANDed condition, a word pattern on a text
# column - `col ILIKE '%w%'` (or LIKE), or a bracketed OR of such on that one column
# (`_pattern_group`) - when ALL of these hold:
#   * the column's name says it holds a kind: one of its words is 'type' or 'kind' (or a plural);
#   * the question - read without the loop's step suffix - prints every pattern's word
#     (`_prints_word`, plurals included);
# the same scope is re-read once, grouped by that column, with that one condition written TRUE and
# every other filter kept, counting rows per kind and marking whether the pattern matches each kind.
# When 1 to KINDS_LEFT_OUT_MAX kinds of the scope are left out by the pattern, one line gives every
# kind with its rows, the matched ones first:
#
#     KINDS IN SCOPE - <col>, rows per kind with the same filters and no pattern on it: A 6, B 1
#     (matched); C 1 (not matched by the pattern)
#
# It only appends: the query's own rows are never touched, and an error drops the line and nothing
# else (`_companion`). One answer rule reads it (openai_client.OUTPUT_FORMAT_RULES). Written from
# SHAPE: no table, column or value is named.
#
# THE QUERY'S OWN FIGURE (fix round 1, ruling W4A1-R1). The line first counted rows whatever the
# query added up, so a SUM of a quantity column read 8 in the table and 1 on the line. Per kind it now
# re-runs the query's own aggregate (`_kind_figure`): the same expression, the pattern written TRUE,
# grouped by the kind column, every other filter kept, and labelled with that expression - 'rows' for
# COUNT(*) and for a row list. An aggregate that cannot be re-run that way (two aggregates, one
# inside an expression, a window) gets no line.
# ---------------------------------------------------------------------------
KINDS_IN_SCOPE_HEADING = "KINDS IN SCOPE - "
#: The line is written only when at least one and at most this many kinds of the scope are left
#: out by the pattern: none left out says nothing, and many make the pattern a filter on purpose.
KINDS_LEFT_OUT_MAX = 3
#: At most this many kinds are named on the line (the left-out ones always), then how many more.
KINDS_SHOWN_MAX = 12
#: How much of each kind the line shows.
KINDS_VALUE_MAX_CHARS = 60
#: The words that, in a column's name, say it holds a kind.
_KIND_NAME_WORDS = frozenset(("type", "types", "kind", "kinds"))
#: The aggregate calls whose figure a KINDS IN SCOPE line re-runs per kind (ruling W4A1-R1).
_KIND_FIGURE_CALLS = frozenset(("COUNT", "SUM", "AVG", "MIN", "MAX"))


def _kind_figure(sql: str, shape):
    """`(expression, label)` of the figure a KINDS IN SCOPE line gives per kind, or None (ruling
    W4A1-R1). A row list - no item of the SELECT list holds an aggregate call - gets its rows,
    `COUNT(*)`. An aggregate gets its own figure: its one SELECT item holding an aggregate call, when
    that item, an alias aside, is the call alone - `F(...)`, F a COUNT, SUM, AVG, MIN or MAX - as the
    query wrote it, labelled 'rows' for COUNT(*) and with the expression itself otherwise. Two such
    items, a call inside an expression, a window or a filter clause give None."""
    items, current = [], []
    for token, depth in shape.select_list:
        if depth == 0 and token[1] == ",":
            items.append(current)
            current = []
        else:
            current.append((token, depth))
    items.append(current)
    calls = [item for item in items
             if any(depth == 0 and _word(token) in _KIND_FIGURE_CALLS and k + 1 < len(item)
                    and item[k + 1][0][1] == "(" for k, (token, depth) in enumerate(item))]
    if not calls:
        return "COUNT(*)", "rows"
    if len(calls) != 1:
        return None
    tokens, depths = [t for t, _ in calls[0]], [d for _, d in calls[0]]
    if len(tokens) > 2 and _word(tokens[-2]) == "AS":
        tokens, depths = tokens[:-2], depths[:-2]
    elif len(tokens) > 1 and tokens[-1][0] in ("word", "qid") and tokens[-2][1] == ")":
        tokens, depths = tokens[:-1], depths[:-1]
    if (len(tokens) < 3 or _word(tokens[0]) not in _KIND_FIGURE_CALLS or tokens[1][1] != "("
            or tokens[-1][1] != ")" or any(d == 0 for d in depths[2:-1])):
        return None
    expression = re.sub(r"\s+", " ", sql[tokens[0][3]:tokens[-1][4]])
    if re.fullmatch(r"COUNT\s*\(\s*\*\s*\)", expression, re.IGNORECASE):
        return expression, "rows"
    return expression, expression


def _kind_figure_text(value) -> str:
    """One kind's figure as the line writes it: a whole number without '.0', any other number to two
    places, any other value shortened at white space."""
    if value is None:
        return "no value"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(int(value)) if float(value) == int(value) else f"{value:.2f}"
    return _shorten_words(value, KINDS_VALUE_MAX_CHARS)


def _pattern_group(sql: str, condition):
    """`(qualifier or None, column, [word, …], the column as written)` when one ANDed condition is a
    word pattern on one column - `col ILIKE '%w%'` (or LIKE), or a bracketed OR of such on that one
    column, each word the literal between its leading and trailing `%`, with no other wildcard -
    else None."""
    tokens = list(condition)
    if len(tokens) > 2 and tokens[0][1] == "(" and tokens[-1][1] == ")":
        depths = _paren_depths(tokens)
        if all(depths[k] > 0 for k in range(1, len(tokens) - 1)):
            tokens = tokens[1:-1]
    depths = _paren_depths(tokens)
    parts, current = [], []
    for k, token in enumerate(tokens):
        if depths[k] == 0 and _word(token) == "OR":
            parts.append(current)
            current = []
        else:
            current.append(token)
    parts.append(current)
    found, words = None, []
    for part in parts:
        compared = _comparison(part)
        if not compared or compared[2] not in ("LIKE", "ILIKE") or len(compared[3]) != 1:
            return None
        literal = str(compared[3][0][2])
        core = literal[1:-1]
        if (len(literal) < 3 or not (literal.startswith("%") and literal.endswith("%"))
                or not core.strip() or any(mark in core for mark in PATTERN_WILDCARDS)):
            return None
        key = (str(compared[0] or "").lower(), str(compared[1]).lower())
        if found is not None and found[0] != key:
            return None
        if found is None:
            found = (key, compared[0], compared[1], sql[part[0][3]:compared[4]])
        words.append(core)
    return (found[1], found[2], words, found[3]) if found else None


# KINDS IN SCOPE, SECOND DETECTOR - an explicit value list, not a word pattern - wave 5,
# 2026-10-04.
#
# Measured on the goal-function run of 2026-09-30: asked how
# many of two named kinds a storey holds, the writer correctly narrowed the kind column with an
# explicit `col IN ('a', 'b', …)` naming every kind of the asked class - no kind of the class was
# missed, unlike the word-pattern shape `_pattern_group`/A3 exists for - and still returned one
# flat COUNT/SUM with no breakdown. `_pattern_group` cannot see this shape at all: it requires
# LIKE/ILIKE, so an IN-list or a chained `col = 'a' OR col = 'b'` on the same column returns None
# from it, and KINDS IN SCOPE never fired.
#
# `_kind_value_group` reads that second shape - the same `(qualifier, column, [values], the
# column as written)` `_pattern_group` returns, so `_kinds_in_scope_line` can GROUP BY it with
# the figure-building it already has. The two shapes differ in what "left out" means: a word
# pattern can miss a kind nothing printed, so the existing path re-reads the scope with the
# pattern written TRUE to find what it missed, and only fires when 1-3 kinds were left out. An
# IN-list or an equality chain already names every kind in scope - there is nothing outside it to
# check for - so this path is unconditional: it breaks down exactly what the filter matched,
# whenever that is 2 or more distinct kinds, keeping the WHERE clause exactly as written.
KIND_VALUE_GROUP_MIN = 2


def _kind_value_group(sql: str, condition):
    """`(qualifier or None, column, [value, …], the column as written)` when one ANDed condition
    is a top-level `col IN ('a', …)` (two or more values), or a bracketed OR of `col = 'a'` on
    that one column (two or more parts) - else None. Every value plain `=`/`IN`, never
    LIKE/ILIKE: a word pattern is `_pattern_group`'s shape, not this one."""
    tokens = list(condition)
    if len(tokens) > 2 and tokens[0][1] == "(" and tokens[-1][1] == ")":
        depths = _paren_depths(tokens)
        if all(depths[k] > 0 for k in range(1, len(tokens) - 1)):
            tokens = tokens[1:-1]
    whole = _comparison(tokens)
    if whole and whole[2] == "IN" and len(whole[3]) >= KIND_VALUE_GROUP_MIN:
        return (whole[0], whole[1], [str(t[2]) for t in whole[3]], sql[tokens[0][3]:whole[4]])
    depths = _paren_depths(tokens)
    parts, current = [], []
    for k, token in enumerate(tokens):
        if depths[k] == 0 and _word(token) == "OR":
            parts.append(current)
            current = []
        else:
            current.append(token)
    parts.append(current)
    if len(parts) < KIND_VALUE_GROUP_MIN:
        return None
    found, values = None, []
    for part in parts:
        compared = _comparison(part)
        if not compared or compared[2] != "=" or len(compared[3]) != 1:
            return None
        key = (str(compared[0] or "").lower(), str(compared[1]).lower())
        if found is not None and found[0] != key:
            return None
        if found is None:
            found = (key, compared[0], compared[1], sql[part[0][3]:compared[4]])
        values.append(str(compared[3][0][2]))
    return (found[1], found[2], values, found[3]) if found else None


def _kind_type_column(table: dict, col_types: dict, column) -> str:
    """The table's own column name for `column` when it is a VARCHAR column whose name says it
    holds a kind (`_KIND_NAME_WORDS`) - or None. The same test the word-pattern path below runs
    inline, pulled out so the value-list path can share it exactly without the two paths sharing
    any other state."""
    col = _column_named(table, column)
    if (col is not None and (col_types.get(table["table_name"]) or {}).get(col) == "VARCHAR"
            and _KIND_NAME_WORDS.intersection(re.findall(r"[a-z]+", str(col).lower()))):
        return col
    return None


def _kinds_in_scope_line(con, question: str, sql: str, tables: list, col_types: dict) -> str:
    """The KINDS IN SCOPE line for `sql`, or "" - see the comment above. Re-reads the scope once per
    pattern it looks at; the caller drops the line, and nothing else, if anything raises."""
    shape = _single_table_query(sql, [t["table_name"] for t in tables], nested=True)
    if shape is None:
        return ""
    table = next((t for t in tables if str(t["table_name"]).lower() == str(shape.table).lower()),
                 None)
    figure = _kind_figure(sql, shape)
    if table is None or figure is None:
        return ""
    expression, label = figure
    text = _split_step_suffix(question)[0]
    where_end = shape.from_pos + len(shape.from_where)
    for condition in _top_level_conditions(sql):
        group = _pattern_group(sql, condition)
        if group is None:
            continue
        _, column, words, written = group
        col = _column_named(table, column)
        if (col is None or (col_types.get(table["table_name"]) or {}).get(col) != "VARCHAR"
                or not _KIND_NAME_WORDS.intersection(re.findall(r"[a-z]+", str(col).lower()))
                or not all(_prints_word(text, re.sub(r"\s+", " ", w).strip()) for w in words)):
            continue
        start, end = condition[0][3], condition[-1][4]
        scope = sql[shape.from_pos:start] + "TRUE" + sql[end:where_end]
        rows = _execute_with_timeout(con, (
            f"SELECT {written}, {expression}, MAX(CASE WHEN {sql[start:end]} THEN 1 ELSE 0 END) "
            f"{scope} GROUP BY {written} ORDER BY 3 DESC, 2 DESC NULLS LAST, MIN(rowid)"))
        kinds = [(k, v, bool(m)) for k, v, m in rows if k is not None and str(k).strip()]
        blanks = [v for k, v, _ in rows if k is None or not str(k).strip()]
        left_out = [(k, v) for k, v, m in kinds if not m]
        if not 1 <= len(left_out) <= KINDS_LEFT_OUT_MAX:
            continue
        matched = [(k, v) for k, v, m in kinds if m]
        shown_matched = matched[:max(0, KINDS_SHOWN_MAX - len(left_out))]

        def named(items):
            return ", ".join(f"{_shorten_words(k, KINDS_VALUE_MAX_CHARS)} {_kind_figure_text(v)}"
                             for k, v in items)

        line = (f"{KINDS_IN_SCOPE_HEADING}{written}, {label} per kind with the same filters and no "
                f"pattern on it: {named(shown_matched) or 'none'}")
        if len(matched) > len(shown_matched):
            line += f", … {len(matched) - len(shown_matched)} more"
        line += f" (matched); {named(left_out)} (not matched by the pattern)"
        if blanks and label == "rows":
            line += f"; {_counted(sum(int(v) for v in blanks), 'row')} with no {written}"
        elif len(blanks) == 1 and blanks[0] is not None:
            line += f"; {_kind_figure_text(blanks[0])} for the rows with no {written}"
        return line

    # Second detector: an explicit IN(...)/equality-OR-chain kind filter (wave 5). Every
    # value here is already in scope, so - unlike the word-pattern path above - this is
    # unconditional: no "left out" check, WHERE kept exactly as written.
    for condition in _top_level_conditions(sql):
        group = _kind_value_group(sql, condition)
        if group is None:
            continue
        _, column, values, written = group
        if _kind_type_column(table, col_types, column) is None or len(set(values)) < 2:
            continue
        rows = _execute_with_timeout(con, (
            f"SELECT {written}, {expression} {shape.from_where} "
            f"GROUP BY {written} ORDER BY 2 DESC NULLS LAST, MIN(rowid)"))
        kinds = [(k, v) for k, v in rows if k is not None and str(k).strip()]
        if len(kinds) < KIND_VALUE_GROUP_MIN:
            continue
        shown = kinds[:KINDS_SHOWN_MAX]
        line = (f"{KINDS_IN_SCOPE_HEADING}{written}, {label} per kind with the same filters: "
                + ", ".join(f"{_shorten_words(k, KINDS_VALUE_MAX_CHARS)} {_kind_figure_text(v)}"
                            for k, v in shown))
        if len(kinds) > len(shown):
            line += f", … {len(kinds) - len(shown)} more"
        return line
    return ""


# ---------------------------------------------------------------------------
# A PHRASE THE TABLE PRINTS IN ANOTHER WORD ORDER - wave 3, G3, 2026-10-03.
#
# Measured on the goal-function runs of 2026-09-30 and 2026-10-01: a question named a place by a
# phrase of several words and the SQL writer matched the whole phrase, `col ILIKE '%A B%'`; the
# table prints the same words in another order, with other words between them, so nothing
# matched. A second empty result ends the loop (its repeat guard), and the question fell to the
# documents, which missed part of the answer. Matched word by word, the same query returned
# exactly the rows asked about.
#
# So, when a query's result holds nothing (`_rows_hold_nothing`: no rows, or only NULLs, blanks
# or a lone zero - the loop's own notion of empty), every plain `col ILIKE '%...%'` in a WHERE
# clause of it - at any depth: under an OR, inside a sub-SELECT; read with `sql_loop`'s own
# condition reader, so the scope rules are the loop's - whose phrase has two or more WORDS
# (`_phrase_words`) is rewritten as one ILIKE per word, ANDed on the same column as written, in
# brackets; and the SQL is re-run ONCE. When the re-run returns something it becomes the result -
# the SQL line, the MATCHED line and every later line read the query that found the rows - and one
# line per rewritten phrase says how they were found. Nothing else is touched: a single word, LIKE,
# a pattern without a wildcard at both ends, a negated match (it is no plain filter) and a result
# that holds something are never re-run; a re-run that still holds nothing leaves the result
# exactly as it was, and so does an error (`_companion`). This runs inside the executor, before an
# empty result is reported, so it comes before any model re-query of the loop; and since the
# rewritten phrase is implied by the phrase as written, the re-run only ever widens what that
# condition matched. Written from SHAPE: no table, column or value is named.
#
# HOW LOOSE IT MAY BE (review fix round 1, ruling W3A2-R2). Word by word is a looser match than the
# phrase, and a phrase that missed only on its formatting - a slash with spaces round it - retried
# to rows across ten different printed places. So:
#   * a WORD is a run of letters and digits of at least WORD_BY_WORD_MIN_CHARS (two) characters:
#     a short code such as a room abbreviation counts, punctuation is no word;
#   * a phrase is rewritten only when at most WORD_BY_WORD_MAX_VALUES (three) printed values of
#     its column hold every one of its words - read over that column's own table, the table the
#     condition's qualifier or its query's FROM names (`sql_loop`'s scope rules again), so a match
#     too loose to name a place is never made: the empty result stands, and the loop's EMPTY
#     re-query lists the column's real values instead. A phrase whose table cannot be told is not
#     rewritten either;
#   * the line names those printed values, so the answer can say what the words matched.
# ---------------------------------------------------------------------------
WORD_BY_WORD_HEADING = "MATCHED WORD BY WORD - "
#: A word of a phrase - a run of letters and digits - shorter than this is left out of the words
#: matched on their own.
WORD_BY_WORD_MIN_CHARS = 2
#: At most this many WORD BY WORD lines on one result.
WORD_BY_WORD_MAX = 3
#: A phrase is matched word by word only when at most this many printed values of its column
#: hold every one of its words (ruling W3A2-R2).
WORD_BY_WORD_MAX_VALUES = 3
#: How much of each printed value the line shows.
WORD_BY_WORD_VALUE_CHARS = 80


def _phrase_words(pattern) -> list:
    """The words of an ILIKE pattern `%...%` that are matched one by one: the runs of letters and
    digits between its outer wildcards, each of at least WORD_BY_WORD_MIN_CHARS characters, once
    whatever its case, in the order written - or [] when the pattern does not open and close with
    `%`, or leaves fewer than two such words."""
    text = str(pattern or "")
    if len(text) < 2 or not (text.startswith("%") and text.endswith("%")):
        return []
    words, seen = [], set()
    for word in re.findall(r"[^\W_]+", text[1:-1]):
        if len(word) >= WORD_BY_WORD_MIN_CHARS and word.lower() not in seen:
            seen.add(word.lower())
            words.append(word)
    return words if len(words) >= 2 else []


def _phrase_conditions(sql: str, tables: list) -> list:
    """`[(start, column end, end, words, table, column), …]` - each plain `col ILIKE '%...%'` in a
    WHERE clause of `sql`, at any depth, whose phrase has two or more words to match one by one
    (`_phrase_words`): where the condition starts, where its column (as written, qualified or not)
    ends and where the condition ends in `sql`, and the loaded table (its dict) and column it
    filters. The WHERE clauses are read with `sql_loop`'s scope rules - a bracket stays in the
    WHERE unless a SELECT opens it, a clause after the WHERE ends it - and so is the table: the one
    a qualifier names, else the nearest enclosing query's FROM or JOIN table that has the column. A
    condition whose table cannot be told is left out."""
    tokens = sql_token_spans(sql)
    if not tokens:
        return []
    loaded = {str(t["table_name"]).lower(): t for t in tables}
    found, aliases = [], {}
    scopes = [{"tables": [], "where": False}]
    for i, token in enumerate(tokens):
        word = _word(token)
        if token[1] == "(":
            scopes.append({"tables": [], "where": scopes[-1]["where"]})
        elif token[1] == ")":
            if len(scopes) > 1:
                scopes.pop()
        elif word == "SELECT" or word in _CLAUSES_AFTER_WHERE or word in _SET_OPERATIONS:
            scopes[-1]["where"] = False
        elif word == "WHERE":
            scopes[-1]["where"] = True
        elif word in ("FROM", "JOIN"):
            _declare_tables(tokens, i + 1, scopes[-1]["tables"], aliases)
        elif scopes[-1]["where"] and i and _opens_condition(tokens[i - 1]):
            condition = _literal_condition(tokens, i, ops=("ILIKE",))
            if not condition:
                continue
            qualifier, column = condition[0], condition[1]
            op = i + 3 if qualifier else i + 1
            words = _phrase_words(tokens[op + 1][2])
            if not words:
                continue
            if qualifier:
                table = _filter_table(qualifier, column, [], aliases, loaded)
            else:
                table = next((t for t in (_filter_table(None, column, scope["tables"], aliases,
                                                        loaded) for scope in reversed(scopes))
                              if t is not None), None)
            if table is not None:
                found.append((tokens[i][3], tokens[op - 1][4], tokens[op + 1][4], words, table,
                              _column_named(table, column)))
    return found


def _word_by_word_rerun(con, sql: str, result, tables: list):
    """`(re-run SQL, rows, column names, lines)` - `sql` re-run with each of its phrase matches
    written word by word (see the comment above) - or None when `result` holds something, `sql`
    holds no such phrase whose words at most WORD_BY_WORD_MAX_VALUES printed values of its column
    hold, or the re-run holds nothing too. Reads each phrase's printed values once, then runs at
    most one re-run; the caller keeps the result as it was if any of that raises."""
    if not _rows_hold_nothing(result):
        return None
    kept = []
    for start, column_end, end, words, table, col in _phrase_conditions(sql, tables):
        quoted = _quoted_name(col)
        held = " AND ".join(f"{quoted} ILIKE {_sql_literal('%' + w + '%')}" for w in words)
        values = [str(v) for (v,) in _execute_with_timeout(con, (
            f"SELECT {quoted} FROM {_quoted_name(table['table_name'])} WHERE {held} "
            f"GROUP BY {quoted} ORDER BY MIN(rowid) LIMIT {WORD_BY_WORD_MAX_VALUES + 1}"))
            if v is not None]
        if values and len(values) <= WORD_BY_WORD_MAX_VALUES:
            kept.append((start, column_end, end, words, values))
    if not kept:
        return None
    rerun = sql
    for start, column_end, end, words, _ in reversed(kept):
        column = sql[start:column_end]
        rerun = (rerun[:start] + "(" + " AND ".join(f"{column} ILIKE {_sql_literal('%' + w + '%')}"
                                                     for w in words) + ")" + rerun[end:])
    rows = _execute_with_timeout(con, rerun)
    if _rows_hold_nothing(rows):
        return None
    names = [d[0] for d in con.description]
    lines = []
    for start, column_end, end, words, values in kept[:WORD_BY_WORD_MAX]:
        column = sql[start:column_end]
        shown = [_sql_literal(_clean_shape_value(v, WORD_BY_WORD_VALUE_CHARS)) for v in values]
        held_by = (f"the only value of {column} that does is {shown[0]}" if len(shown) == 1 else
                   f"the only values of {column} that do are {_and_list(shown)}")
        lines.append(f"{WORD_BY_WORD_HEADING}no row has {sql[start:end]}; the rows above were "
                     f"found with {column} holding each of its words "
                     f"({_and_list(_sql_literal(w) for w in words)}), in any order and with "
                     f"other words between, and {held_by}")
    return rerun, rows, names, "\n".join(lines)


# ---------------------------------------------------------------------------
# A WHOLE CHAIN OF A PARENT-REFERENCE TREE, WALKED IN CODE - spec-fix2, 2026-10-01.
#
# Some questions ask for more than one hop of a parent -> child tree: everything BELOW a
# node ("if X trips, what loses power?") or everything ABOVE it ("what feeds X, all the way
# back to the main supply?"). Measured on the goal-function run of 2026-09-30, the SQL
# writer only ever wrote one hop, or a fixed two-hop self-join: the answer listed fewer
# than half of the rows below the node, and a chain that stopped short of the top. Asking
# the writer for WITH RECURSIVE would collide with the prompt's ban on set operations
# (pinned by tests/test_room_and_hop_rules.py), so the walk is done HERE, in code, and
# appended to the result as ONE line - no SQL-prompt rule changes:
#
#     HIERARCHY - everything fed from X (N rows, D tiers): depth 1: A (level), ...; depth 2: ...
#     HIERARCHY - X (level) is fed from A <- B <- ... <- ROOT
#
# It fires only when BOTH hold:
#   * the question uses tree wording (`tree_direction`), word-bounded, so a name that
#     merely starts like one of the words never counts; and
#   * it prints, whole, a code from the identifier column of a LOADED table that has a FEED
#     reference column (`_is_feed_column`: the fed_from family, with `rolls_up_to` as its
#     override). A CONTAINMENT reference - `parent` / `parent_id`, what a row sits INSIDE -
#     is never walked: sitting inside something is not being supplied by it, and walking a
#     places table's parent_id listed every room of a level as losing power with it (review
#     fix round 1, T10-R5). The double-counting note below still warns over both kinds.
# The question is read WITHOUT the investigation loop's step suffix: that is the loop's own
# instruction and previous SQL, never the user's words, and the values it lists could
# change the node - so every step of an investigation carries the same line (T10-R2).
#
# The parent is the EFFECTIVE parent - a resolved `rolls_up_to` wins over the parent as
# printed, the expression the SQL prompt asks for. The column the parents point at is read
# off the DATA - the column holding the most of them, the card's identifier column breaking
# a tie - so no column name is assumed, and a table none of whose parents is one of its own
# rows holds no tree and is never walked. Level names come from the places table the card's
# declared join on `location_id` names, when that table is loaded. The walk is DuckDB
# WITH RECURSIVE over the loaded table, at most HIERARCHY_MAX_DEPTH deep, at most
# HIERARCHY_MAX_ROWS listed, cycle-safe, and it says so when a cap cut it short. It only
# appends: it never re-queries and never touches the SQL the model wrote. Written from
# SHAPE - a feed-reference column, a code - so it names no table, no column value, no
# building and no domain noun: the line counts "rows", and its depth in "tiers" - never
# "levels", the word the floor names in brackets already mean (T10-R4).
# ---------------------------------------------------------------------------
HIERARCHY_HEADING = "HIERARCHY - "
HIERARCHY_MAX_DEPTH = 10
HIERARCHY_MAX_ROWS = 60

#: Wording asking for everything BELOW a node - lose(s)/losing power or supply; "if" ...
#: trips/trip/fails/fail/is isolated/is switched off, within one sentence; downstream;
#: directly or indirectly - and everything ABOVE one - all the way back/up; back to the
#: main/source; full/whole supply path/chain; upstream. Every word is bounded on both
#: sides: 'trip' never matches a name that begins with it, and 'is isolated' never matches
#: 'isolation'. A full stop, question mark, exclamation mark or semicolon followed by a space
#: ends the "if" sentence, and so does a line break; a full stop inside a code ('1.29') does
#: not.
_SENTENCE_GOES_ON = r"(?:(?![.?!;](?:\s|$)).)*?"
_TREE_DOWN_RES = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\blos(?:e|es|ing)\s+(?:power|supply)\b",
    r"\bif\b" + _SENTENCE_GOES_ON + r"\b(?:trips?|fails?|is\s+isolated|is\s+switched\s+off)\b",
    r"\bdownstream\b",
    r"\bdirectly\s+or\s+indirectly\b",
))
_TREE_UP_RES = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\ball\s+the\s+way\s+(?:back|up)\b",
    r"\bback\s+to\s+the\s+(?:main|source)\b",
    r"\b(?:full|whole)\s+supply\s+(?:path|chain)\b",
    r"\bupstream\b",
))


def tree_direction(question: str):
    """"down" when the question asks for everything below a node, "up" when it asks for
    everything above one, None otherwise. A question wording both gets the one it prints
    first. Pure."""
    text = question or ""
    first = {}
    for way, patterns in (("down", _TREE_DOWN_RES), ("up", _TREE_UP_RES)):
        starts = [m.start() for m in (p.search(text) for p in patterns) if m]
        if starts:
            first[way] = min(starts)
    return min(first, key=first.get) if first else None


def _is_parent_column(column) -> bool:
    """A parent-reference column of either kind, by name: one containing `fed_from` (a
    feed), or `parent` / `parent_id` (containment). The double-counting note's test - a
    parent's total includes its children's down a containment tree as much as a feed tree.
    The walk follows only `_is_feed_column`."""
    name = str(column).lower()
    return "fed_from" in name or name in ("parent", "parent_id")


def _is_feed_column(column) -> bool:
    """A FEED reference, by name: a column whose name contains `fed_from` - the column
    naming what supplies a row. The only kind of parent reference the hierarchy walk
    follows (with `rolls_up_to` as its override); a containment reference (`parent`,
    `parent_id`) never is. A word merely starting with "feed" is not one: a feeder
    schedule's `feeder` column is the CHILD of its row (T10-R5)."""
    return "fed_from" in str(column).lower()


def _looks_like_code(text: str) -> bool:
    """A code rather than a word or a number: it has a letter, and a digit or a hyphen."""
    return (any(ch.isalpha() for ch in text)
            and any(ch.isdigit() or ch == "-" for ch in text))


#: What may stand on either side of a code printed whole: anything but a letter, a digit,
#: an underscore, a hyphen or a slash - and a full stop only where it ends a sentence,
#: never between two characters of a code.
_CODE_BEFORE = r"(?<![\w\-/])(?<!\w\.)"
_CODE_AFTER = r"(?![\w\-/])(?!\.\w)"


def _printed_code(text: str, codes):
    """The value in `codes` that `text` prints whole - bounded on both sides by characters
    no code is made of, in any case, runs of white space read as one - or None. Only a
    code-shaped value (`_looks_like_code`) can match, and the longest match wins, so a
    question naming `X-1-B` never resolves to `X-1`. Pure."""
    haystack = re.sub(r"\s+", " ", text or "")
    best, best_len = None, 0
    for code in codes:
        shown = re.sub(r"\s+", " ", str(code)).strip()
        if len(shown) <= best_len or not _looks_like_code(shown):
            continue
        if re.search(_CODE_BEFORE + re.escape(shown) + _CODE_AFTER, haystack, re.IGNORECASE):
            best, best_len = code, len(shown)
    return best


def _text_sql(column) -> str:
    """`column` read as trimmed text, NULL when blank - the form every walk compares."""
    return f"NULLIF(TRIM(CAST({_quoted_name(column)} AS VARCHAR)), '')"


def _sql_literal(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


#: How the walk reads one table: its name and columns, its identifier column, the SQL of
#: its effective parent, and its identifier values in table order.
_TreeShape = namedtuple("_TreeShape", "table columns id_col parent_sql codes")
#: Where a walked table's level names come from: its own location column, and the places
#: table's name, key column and level column.
_Places = namedtuple("_Places", "loc_col table key_col level_col")


def _tree_shape(con, table: dict, card):
    """The `_TreeShape` of one loaded table, or None when it holds no tree - no feed
    reference column (`_is_feed_column`), or no effective parent that is a value of one of
    its own columns. Reads the table once."""
    columns = list(table.get("columns") or [])
    feeds = [c for c in columns if _is_feed_column(c)]
    if not feeds:
        return None
    resolved = next((c for c in columns if str(c).lower() == "rolls_up_to"), None)
    parent_sql = _text_sql(feeds[0])
    if resolved is not None:
        parent_sql = f"COALESCE({_text_sql(resolved)}, {parent_sql})"
    # A parent reference of either kind is never the column the parents point at.
    others = [c for c in columns if not _is_parent_column(c) and c != resolved]
    if not others:
        return None
    rows = _execute_with_timeout(con, f"SELECT {parent_sql}, "
                                      + ", ".join(_text_sql(c) for c in others)
                                      + f" FROM {_quoted_name(table['table_name'])}")
    pointed_at = {row[0] for row in rows if row[0] is not None}
    named = str((card or {}).get("identifier_column") or "").lower()
    best = None
    for i, column in enumerate(others):
        hits = len(pointed_at & {row[i + 1] for row in rows if row[i + 1] is not None})
        rank = (hits, str(column).lower() == named)
        if hits and (best is None or rank > best[0]):
            best = (rank, i, column)
    if best is None:
        return None
    _, i, column = best
    codes = list(dict.fromkeys(row[i + 1] for row in rows if row[i + 1] is not None))
    return _TreeShape(table["table_name"], columns, column, parent_sql, codes)


def _hierarchy_target(con, text: str, sql: str, tables: list, cards: list):
    """(the `_TreeShape` to walk, the node) for `text`, or None. Every loaded table holding
    a feed tree is a candidate; the longest code printed wins, then a table the SQL read,
    then the order the tables were loaded in."""
    found = []
    for order, table in enumerate(tables):
        if not table.get("rows") or not any(_is_feed_column(c)
                                            for c in table.get("columns") or []):
            continue
        card = next((c for c in cards or [] if c.get("table") == table["table_name"]), None)
        shape = _tree_shape(con, table, card)
        node = _printed_code(text, shape.codes) if shape else None
        if node is not None:
            rank = (-len(re.sub(r"\s+", " ", str(node))),
                    not _sql_reads_table(sql, table["table_name"]), order)
            found.append((rank, shape, node))
    if not found:
        return None
    _, shape, node = min(found, key=lambda f: f[0])
    return shape, node


_DECLARED_JOIN_RE = re.compile(r"`([^`.]+)\.([^`]+)`\s*=\s*`([^`.]+)\.([^`]+)`")
#: The column doc-prep's output contract stamps every placeable row with.
_LOCATION_KEY = "location_id"


def _places_of(shape, tables: list, cards: list):
    """The `_Places` of a walked table - when its card declares a join from its
    `location_id` to a table that is loaded and has a level column - else None. Read off
    the card's declared join, never a table name. The level column is the places table's
    first column naming a level and a name, else its first naming a level."""
    card = next((c for c in cards or [] if c.get("table") == shape.table), None)
    loaded = {t["table_name"]: t for t in tables if t.get("rows")}

    def column_of(columns, name):
        return next((c for c in columns if str(c).lower() == str(name).strip().lower()), None)

    for join in (card or {}).get("declared_joins") or []:
        found = _DECLARED_JOIN_RE.search(str(join))
        if not found:
            continue
        src_table, src_col, dst_table, dst_col = (s.strip() for s in found.groups())
        if src_table != shape.table or src_col.lower() != _LOCATION_KEY or dst_table not in loaded:
            continue
        dst_columns = list(loaded[dst_table].get("columns") or [])
        loc_col, key_col = column_of(shape.columns, src_col), column_of(dst_columns, dst_col)
        levels = [c for c in dst_columns if "level" in str(c).lower()]
        level_col = next((c for c in levels if "name" in str(c).lower()),
                         levels[0] if levels else None)
        if loc_col is not None and key_col is not None and level_col is not None:
            return _Places(loc_col, dst_table, key_col, level_col)
    return None


def _walk_sources(shape, places) -> str:
    """The CTEs both walks read: each node once - its first row - with its effective parent
    and its place; and each place's level name (an empty set with no places table)."""
    loc = _text_sql(places.loc_col) if places else "NULL"
    sources = [
        f"__h_src AS (SELECT {_text_sql(shape.id_col)} AS node, {shape.parent_sql} AS parent, "
        f"{loc} AS loc, rowid AS r FROM {_quoted_name(shape.table)})",
        "__h_nodes AS (SELECT node, parent, loc FROM __h_src WHERE node IS NOT NULL "
        "QUALIFY row_number() OVER (PARTITION BY node ORDER BY r) = 1)",
    ]
    if places:
        sources.append(
            f"__h_levels AS (SELECT k, lvl FROM (SELECT {_text_sql(places.key_col)} AS k, "
            f"{_text_sql(places.level_col)} AS lvl, rowid AS r FROM {_quoted_name(places.table)}) "
            f"WHERE k IS NOT NULL QUALIFY row_number() OVER (PARTITION BY k ORDER BY r) = 1)")
    else:
        sources.append("__h_levels AS (SELECT NULL::VARCHAR AS k, NULL::VARCHAR AS lvl "
                       "WHERE FALSE)")
    return ", ".join(sources)


def _with_level(name, level) -> str:
    level = re.sub(r"\s+", " ", str(level or "")).strip()
    return f"{name} ({level})" if level else str(name)


def _counted(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _walk_down_line(con, shape, node, places) -> str:
    """The downward HIERARCHY line: every row whose effective parent chain reaches `node`,
    by depth, or "" when there is none. A row reached by two paths counts once, at its
    shallower depth; one more level than the cap is walked only to say whether it cut."""
    start = _sql_literal(node)
    cap = HIERARCHY_MAX_DEPTH
    rows = _execute_with_timeout(con, (
        f"WITH RECURSIVE {_walk_sources(shape, places)}, "
        f"__h_edges AS (SELECT DISTINCT node, parent FROM __h_src "
        f"WHERE node IS NOT NULL AND parent IS NOT NULL), "
        f"__h_walk(node, depth, path) AS (SELECT {start}, 0, [{start}] UNION ALL "
        f"SELECT e.node, w.depth + 1, list_append(w.path, e.node) FROM __h_edges e "
        f"JOIN __h_walk w ON e.parent = w.node "
        f"WHERE w.depth <= {cap} AND NOT list_contains(w.path, e.node)), "
        f"__h_found AS (SELECT node, MIN(depth) AS depth FROM __h_walk WHERE depth > 0 "
        f"GROUP BY node) "
        f"SELECT f.node, f.depth, l.lvl, "
        f"SUM(CASE WHEN f.depth <= {cap} THEN 1 ELSE 0 END) OVER () AS n, "
        f"MAX(f.depth) OVER () AS deepest "
        f"FROM __h_found f LEFT JOIN __h_nodes d ON d.node = f.node "
        f"LEFT JOIN __h_levels l ON l.k = d.loc "
        f"QUALIFY f.depth <= {cap} ORDER BY f.depth, f.node LIMIT {HIERARCHY_MAX_ROWS}"))
    if not rows:
        return ""
    total, deepest = int(rows[0][3]), int(rows[0][4])
    by_depth = {}
    for name, depth, level, _, _ in rows:
        by_depth.setdefault(int(depth), []).append(_with_level(name, level))
    listing = "; ".join(f"depth {d}: {', '.join(names)}" for d, names in sorted(by_depth.items()))
    if total > len(rows):
        listing += f" … and {total - len(rows)} more"
    if deepest > cap:
        listing += f" … and more below depth {cap}"
    return (f"{HIERARCHY_HEADING}everything fed from {node} ({_counted(total, 'row')}, "
            f"{_counted(min(deepest, cap), 'tier')}): {listing}")


def _walk_up_line(con, shape, node, places) -> str:
    """The upward HIERARCHY line: `node`'s effective parent, its parent, and so on. It ends
    at a parent with no row of its own (named as printed), at ROOT above a row that records
    no parent, at the first parent seen twice (marked as a loop), or at the depth cap (an
    ellipsis)."""
    start = _sql_literal(node)
    rows = _execute_with_timeout(con, (
        f"WITH RECURSIVE {_walk_sources(shape, places)}, "
        f"__h_up(node, depth, path) AS (SELECT {start}, 0, [{start}] UNION ALL "
        f"SELECT d.parent, u.depth + 1, list_append(u.path, d.parent) FROM __h_nodes d "
        f"JOIN __h_up u ON d.node = u.node "
        f"WHERE u.depth < {HIERARCHY_MAX_DEPTH} AND d.parent IS NOT NULL "
        f"AND NOT list_contains(u.path, d.parent)) "
        f"SELECT u.node, d.node IS NOT NULL, d.parent, l.lvl, "
        f"COALESCE(list_contains(u.path, d.parent), FALSE) "
        f"FROM __h_up u LEFT JOIN __h_nodes d ON d.node = u.node "
        f"LEFT JOIN __h_levels l ON l.k = d.loc ORDER BY u.depth"))
    if not rows:
        return ""
    parts = [_with_level(name, level) for name, _, _, level, _ in rows[1:]]
    _, has_row, parent, _, loops = rows[-1]
    if has_row and parent is None:
        parts.append("ROOT")
    elif has_row and loops:
        parts.append(f"{parent} (loop)")
    elif has_row:
        parts.append("…")
    return (f"{HIERARCHY_HEADING}{_with_level(rows[0][0], rows[0][3])} is fed from "
            f"{' <- '.join(parts)}")


def _hierarchy_line(con, question: str, sql: str, tables: list, cards: list) -> str:
    """The HIERARCHY line for `question`, or "" when it does not apply - see the comment
    above for when it does. Reads each loaded table holding a tree once, then walks one;
    the caller drops the line, and nothing else, if any of that raises."""
    text = _split_step_suffix(question)[0]
    way = tree_direction(text)
    if way is None:
        return ""
    target = _hierarchy_target(con, text, sql, tables, cards)
    if target is None:
        return ""
    shape, node = target
    walk = _walk_down_line if way == "down" else _walk_up_line
    return walk(con, shape, node, _places_of(shape, tables, cards))


# ---------------------------------------------------------------------------
# CHILDREN BY PLACE - wave 5, G13 part (b), 2026-10-04, designed in plan-wave4.md (shipped
# together with sql_loop's CURRENT_TWIN re-query, part a).
#
# Measured on the goal-function run of 2026-09-30: "which board supplies Block B and which
# supplies Block C" has no column that answers it directly - every board's OWN place column
# names where ITS OWN switchgear sits, never the place its output reaches, and that column is
# the SAME single value ('C', the Main LV room) for every top-level board in the tree. The only
# way to answer "supplies" is to count where a board's CHILDREN sit. No SQL in any recorded run
# ever computed that; `_hierarchy_line` above does not apply either, because it fires only on
# "everything below/above X" wording, never on "which of these two feeds which place".
#
# So: when the SQL result prints 2 or more of a loaded parent-reference tree's OWN node
# identifiers (read the way `_tree_shape` already does), and the question itself names 2 or
# more PRINTED values of some OTHER column of that SAME table - not its identifier, not a
# parent-reference column - as "<column name> <value>" (`_named_column_values`; the column's
# name and a value it holds, adjacent, in any case): for each such node that is one of the
# result's own, count its DIRECT children (one hop only - "supplies" is decided by what a
# board's own children are, never by what sits several boards further down) per value of that
# column. A node with no children at all is left out: there is nothing to say about it. At most
# CHILDREN_BY_PLACE_MAX nodes are shown. It only appends; an error drops it and nothing else
# (`_companion`). One answer rule reads it. Written from SHAPE: no table, column or value of the
# project is named.
#
#     CHILDREN BY PLACE - MDB-X feeds 6 in zone A, 3 in zone B; MDB-Y feeds 5 in zone B
# ---------------------------------------------------------------------------
CHILDREN_BY_PLACE_HEADING = "CHILDREN BY PLACE - "
#: At most this many matched nodes get their own clause on one line.
CHILDREN_BY_PLACE_MAX = 8
#: A categorical column must hold between this many distinct printed values (inclusive) to be a
#: candidate - the same lower/upper bound `_result_shape`'s own breakdown lines use, read here
#: off the WHOLE loaded table rather than one result.
CHILDREN_BY_PLACE_MIN_VALUES = 2
CHILDREN_BY_PLACE_MAX_VALUES = 8


def _named_column_values(text: str, table: dict, exclude) -> dict:
    """`{column: [matched value, …]}` - every column of `table` NOT in `exclude` (the tree's
    own identifier and its parent-reference columns) whose loaded rows print between
    CHILDREN_BY_PLACE_MIN_VALUES and CHILDREN_BY_PLACE_MAX_VALUES distinct, non-blank values, of
    which `text` names at least two - as that column's OWN name (its underscores/hyphens read as
    a flexible run of word-separators) immediately followed by the value, word-bounded on both
    sides, in any case ('Block B', 'Block C' for a column named `block`). In the table's own
    column order; a column with no match contributes nothing."""
    found = {}
    for column in table.get("columns") or []:
        if column in exclude:
            continue
        label = re.sub(r"[_\-]+", r"[\\s_-]+", re.escape(str(column).strip()))
        if not label:
            continue
        values = sorted({str(row.get(column)).strip() for row in table.get("rows") or []
                         if row.get(column) is not None and str(row.get(column)).strip()})
        if not (CHILDREN_BY_PLACE_MIN_VALUES <= len(values) <= CHILDREN_BY_PLACE_MAX_VALUES):
            continue
        matched = [v for v in values if re.search(
            r"(?<![A-Za-z0-9])" + label + r"\s*" + re.escape(v) + r"(?![A-Za-z0-9])",
            text, re.IGNORECASE)]
        if len(matched) >= CHILDREN_BY_PLACE_MIN_VALUES:
            found[column] = matched
    return found


def _children_by_place_line(con, question: str, sql: str, tables: list, cards: list,
                            result) -> str:
    """The CHILDREN BY PLACE line for `sql`, or "" - see the comment above for when it applies.
    One query per qualifying tree; the caller (`_companion`) drops the line, and nothing else,
    if anything raises."""
    text = _split_step_suffix(question)[0]
    printed = {str(v).strip() for row in result or [] for v in row
              if v is not None and str(v).strip()}
    if len(printed) < 2:
        return ""
    for table in tables:
        if not table.get("rows") or not any(_is_feed_column(c)
                                            for c in table.get("columns") or []):
            continue
        card = next((c for c in cards or [] if c.get("table") == table["table_name"]), None)
        shape = _tree_shape(con, table, card)
        if shape is None:
            continue
        nodes = [c for c in shape.codes if str(c).strip() in printed]
        if len(nodes) < 2:
            continue
        exclude = {shape.id_col} | {c for c in shape.columns if _is_parent_column(c)}
        named = _named_column_values(text, table, exclude)
        if not named:
            continue
        column = next(iter(named))
        rows = _execute_with_timeout(con, (
            f"SELECT {shape.parent_sql} AS parent, {_text_sql(column)} AS cat, COUNT(*) "
            f"FROM {_quoted_name(shape.table)} WHERE {shape.parent_sql} IN "
            f"({', '.join(_sql_literal(n) for n in nodes)}) GROUP BY 1, 2 ORDER BY 1, 3 DESC"))
        by_parent = {}
        for parent, cat, n in rows:
            if parent is None or cat is None:
                continue
            by_parent.setdefault(parent, []).append((str(cat), int(n)))
        parts = []
        for node in nodes:
            cats = by_parent.get(node)
            if not cats:
                continue
            parts.append(f"{node} feeds "
                         + ", ".join(f"{n} in {column} {v}" for v, n in cats))
        if not parts:
            continue
        shown = parts[:CHILDREN_BY_PLACE_MAX]
        line = CHILDREN_BY_PLACE_HEADING + "; ".join(shown)
        if len(parts) > len(shown):
            line += f"; … and {len(parts) - len(shown)} more"
        return line
    return ""


# ---------------------------------------------------------------------------
# ONE PARENT'S CHILDREN UNDER TWO RECORDS' NAMES - wave 4, G14 (a), 2026-10-03.
#
# Measured on the goal-function runs: asked what one parent feeds, the writer read its children off
# the parent's own list of ways, a second record that prints two of them under names no row of the
# tree carries - where the children's own rows, whose parent reference IS the tree, name them
# otherwise - and a spare way among them; the answer listed that record's names, the spare way
# included. Nothing links the two records, and the writer goes either way.
#
# So, for each loaded table holding a feed tree (`_tree_shape`: a feed-reference column and its
# effective parent, as the HIERARCHY walk reads it), every OTHER loaded table the SQL reads is read
# as a list of children (`_child_list_columns`): of its columns, the pair (parent, child) on whose
# rows the tree's effective parent of the child IS the parent most often - on at least
# FEED_LIST_MIN_ROWS rows; a table with no such pair lists nobody's children. When one column of the
# result prints at least two names that list gives one parent - a space or spare way never counting
# - and the two records disagree on that parent's children, under this rule:
#     the list's names for the parent (not blank, not a space or spare way) and the tree's children
#     of it (its rows whose effective parent it is), each folded - case, accents, punctuation and a
#     trailing bracketed label folded away (`_folded_name`) - each hold a name the other lacks;
# one line names the tree's own children of that parent, then the names found on one side only, as
# printed, and the spare ways the list holds:
#     FEED NAMES - <parent>: <tree> records N rows fed from it: A, B, C; <list>, listing what it
#     feeds, also prints X, Y - names no <tree> row carries - and does not print P, Q; it also lists
#     1 space or spare way.
# A pre-takeover table is compared only with a pre-takeover tree (`_current_twin`), never across.
# A name the SQL itself prints as a string literal never counts: it came into the query rather than
# out of it, as in a lookup of values for named ways (review fix round 1).
# The trees are read (`_tree_shape`, one more query for their parents) only once a table the SQL
# reads is found to list a tree's children - found in Python off the loaded rows, with the tree read
# the same way (`_loaded_tree_parents`) - so a result reading no such table costs no query at all
# (review fix round 1).
# At most FEED_NAMES_MAX lines, FEED_NAMES_SHOWN names per list. It only appends; an error drops it
# and nothing else (`_companion`). One answer rule reads it. Written from SHAPE: no table, column or
# name of the project is named.
# ---------------------------------------------------------------------------
FEED_NAMES_HEADING = "FEED NAMES - "
#: A table is read as a list of a tree's children only when its pair of columns agrees with the
#: tree's own parent on at least this many rows.
FEED_LIST_MIN_ROWS = 2
#: At most this many FEED NAMES lines on one result, and this many names in each of its lists.
FEED_NAMES_MAX = 3
FEED_NAMES_SHOWN = 12
#: A way printed as a space or a spare - an empty way, never a child.
_SPARE_WAY_RE = re.compile(r"^\s*(?:space|spare)s?\b", re.IGNORECASE)


def _folded_name(value) -> str:
    """`value` folded for comparing two records' names of one thing: accents taken down to their
    base letter, a trailing bracketed label after white space dropped, case folded, and everything
    but letters and digits removed."""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+\([^()]*\)\s*$", "", text)
    return re.sub(r"[\W_]+", "", text.casefold())


def _loaded_tree_parents(table: dict, card):
    """`{node: effective parent}` of a loaded table holding a feed tree, read in Python off its
    loaded rows the way `_tree_shape` reads it in DuckDB - the feed-reference column, a resolved
    `rolls_up_to` over it, the node column being the one holding the most of those parents (the
    card's identifier breaking a tie), every cell trimmed as DuckDB's TRIM trims (spaces and
    no-break spaces) and a blank read as nothing - or None. FEED NAMES asks it, before any query,
    whether a table the SQL reads lists the tree's children; the line itself is still built from
    `_tree_shape` (review fix round 1)."""
    columns = list(table.get("columns") or [])
    feeds = [c for c in columns if _is_feed_column(c)]
    if not feeds:
        return None
    resolved = next((c for c in columns if str(c).lower() == "rolls_up_to"), None)
    others = [c for c in columns if not _is_parent_column(c) and c != resolved]
    rows = table.get("rows") or []

    def cell(row, column):
        text = "" if row.get(column) is None else str(row.get(column)).strip("  ")
        return text or None

    parents = [(cell(row, resolved) if resolved is not None else None) or cell(row, feeds[0])
               for row in rows]
    pointed_at = {p for p in parents if p is not None}
    named = str((card or {}).get("identifier_column") or "").lower()
    best = None
    for column in others:
        hits = len(pointed_at & {cell(row, column) for row in rows})
        rank = (hits, str(column).lower() == named)
        if hits and (best is None or rank > best[0]):
            best = (rank, column)
    if best is None:
        return None
    parent_of = {}
    for row, parent in zip(rows, parents):
        node = cell(row, best[1])
        if node is not None and node not in parent_of:
            parent_of[node] = parent
    return parent_of


def _child_list_columns(table: dict, parent_of: dict):
    """`(parent column, child column)` of `table` read as a list of the children of the tree whose
    effective parents `parent_of` maps (node -> parent): the pair on whose rows the child's
    effective parent is that row's parent most often, on at least FEED_LIST_MIN_ROWS rows - or
    None. Read in Python off the loaded rows."""
    rows = table.get("rows") or []
    parents = {p for p in parent_of.values() if p is not None}
    cells = {c: [("" if r.get(c) is None else str(r.get(c)).strip()) for r in rows]
             for c in table.get("columns") or []}
    children = [c for c, vs in cells.items() if len(set(vs) & set(parent_of)) >= 2]
    holders = [c for c, vs in cells.items() if set(vs) & parents]
    best = None
    for child in children:
        for parent in holders:
            if parent == child:
                continue
            agree = sum(1 for p, c in zip(cells[parent], cells[child])
                        if c in parent_of and parent_of[c] == p)
            if agree >= FEED_LIST_MIN_ROWS and (best is None or agree > best[0]):
                best = (agree, parent, child)
    return (best[1], best[2]) if best else None


def _shown_names(names) -> str:
    shown = ", ".join(names[:FEED_NAMES_SHOWN])
    if len(names) > FEED_NAMES_SHOWN:
        shown += f", … {len(names) - FEED_NAMES_SHOWN} more"
    return shown


def _feed_names_for(parent, listed, children, parent_of, tree: str, record: str) -> str:
    """The FEED NAMES line for one parent, or "" when the two records agree under the rule above."""
    ways = [n for n in listed if not _SPARE_WAY_RE.match(n)]
    spare = len(listed) - len(ways)
    in_tree = {_folded_name(c) for c in children}
    in_list = {_folded_name(n) for n in ways}
    list_only = [n for n in ways if _folded_name(n) not in in_tree]
    tree_only = [c for c in children if _folded_name(c) not in in_list]
    if not list_only or not tree_only:
        return ""
    rows_elsewhere = [n for n in list_only if n in parent_of]
    no_row = [n for n in list_only if n not in parent_of]
    printed = []
    if no_row:
        printed.append(f"{_shown_names(no_row)} - names no {tree} row carries")
    if rows_elsewhere:
        printed.append(_and_list(f"{n} (which {tree} records as fed from {parent_of[n] or 'nothing'})"
                                 for n in rows_elsewhere[:FEED_NAMES_SHOWN]))
    line = (f"{FEED_NAMES_HEADING}{parent}: {tree} records {_counted(len(children), 'row')} fed "
            f"from it: {_shown_names(children)}; {record}, listing what it feeds, also prints "
            f"{'; '.join(printed)} - and does not print {_shown_names(tree_only)}")
    if spare:
        line += f"; it also lists {_counted(spare, 'space or spare way')}"
    return line


def _feed_names_line(con, sql: str, tables: list, cards: list, result) -> str:
    """The FEED NAMES lines for `sql`, or "" - see the comment above. A tree is read (two queries)
    only when a table the SQL reads lists its children; everything else in Python off the loaded
    rows. The caller drops the lines, and nothing else, if anything raises."""
    # A name the SQL itself was handed, as a string literal, came INTO the query, not out of it - a
    # lookup of values for named ways tells nothing of what the parent feeds (review fix round 1).
    handed = {str(t[2]).strip().casefold() for t in sql_token_spans(sql) or [] if t[0] == "str"}
    columns = {}
    for row in result or []:
        for k, value in enumerate(row):
            text = "" if value is None else str(value).strip()
            if text and text.casefold() not in handed:
                columns.setdefault(k, set()).add(text)
    if not columns:
        return ""
    read = [t for t in tables if t.get("rows") and _sql_reads_table(sql, t["table_name"])]
    lines = []
    for tree in tables:
        if not tree.get("rows") or not any(_is_feed_column(c) for c in tree.get("columns") or []):
            continue
        card = next((c for c in cards or [] if c.get("table") == tree["table_name"]), None)
        # Which tables the SQL reads list this tree's children - asked in Python off the loaded
        # rows, before any query: a result reading none costs no query (review fix round 1).
        loaded = _loaded_tree_parents(tree, card)
        if not loaded or not any(
                other is not tree
                and (_current_twin(other["table_name"]) is None)
                == (_current_twin(tree["table_name"]) is None)
                and _child_list_columns(other, loaded) is not None for other in read):
            continue
        shape = _tree_shape(con, tree, card)
        if shape is None:
            continue
        parent_of = {}
        for node, parent in _execute_with_timeout(con, (
                f"SELECT {_text_sql(shape.id_col)}, {shape.parent_sql} "
                f"FROM {_quoted_name(shape.table)} ORDER BY rowid")):
            if node is not None and node not in parent_of:
                parent_of[node] = parent
        children_of = {}
        for node, parent in parent_of.items():
            if parent is not None:
                children_of.setdefault(parent, []).append(node)
        for other in tables:
            if (other is tree or not other.get("rows")
                    or not _sql_reads_table(sql, other["table_name"])
                    or (_current_twin(other["table_name"]) is None)
                    != (_current_twin(shape.table) is None)):
                continue
            pair = _child_list_columns(other, parent_of)
            if pair is None:
                continue
            parent_col, child_col = pair
            listed, under = {}, {}
            for row in other["rows"]:
                p = "" if row.get(parent_col) is None else str(row.get(parent_col)).strip()
                c = "" if row.get(child_col) is None else str(row.get(child_col)).strip()
                if not p or not c:
                    continue
                if c not in listed.setdefault(p, []):
                    listed[p].append(c)
                if not _SPARE_WAY_RE.match(c) and p not in under.setdefault(c, []):
                    under[c].append(p)
            for k in sorted(columns):
                named = {}
                for value in columns[k]:
                    for p in under.get(value, ()):
                        named.setdefault(p, set()).add(value)
                for parent in sorted(p for p, vs in named.items() if len(vs) >= 2):
                    line = _feed_names_for(parent, listed.get(parent, []),
                                           children_of.get(parent, []), parent_of,
                                           shape.table, other["table_name"])
                    if line and line not in lines:
                        lines.append(line)
                    if len(lines) >= FEED_NAMES_MAX:
                        return "\n".join(lines)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# EVERY LINK OF THE IDS A RESULT PRINTS - wave 3, G7, 2026-10-03.
#
# Measured on the goal-function run of 2026-10-01: a question asked how many units depend on one
# place and what each group of them relies on it for. The writer joined the unit register to the
# dependency graph and kept ONE link type - the complete value list the schema block now prints
# for the link column put that type in front of it - so the units' second link to the same place,
# and the second place half of them rely on for the rest, never reached the result; the answer said
# nothing else was recorded. Every link was on record, and DEPENDENCY_GRAPH_RULE's "never one
# direction alone" had not taken.
#
# So the graph is read here, in code. When the SQL reads a loaded table shaped as a graph - columns
# subject_id, predicate and object_id, `table_router`'s own test for a graph card - and the result
# prints, in any cell, ids that table holds as the SUBJECT of a link, every link FROM those ids is
# read (one query) and grouped by (link, target): its target's display name (the first column naming
# an object and a name, when there is one), how many links the group holds, and the links' note when
# every link of the group carries the same one (a note an earlier group already gave is not
# repeated). A link type with more than GRAPH_TARGETS_PER_LINK targets - one board feeding many
# circuits, one level containing many rooms - is told as one group with its counts, never one group
# per target. The largest groups come first (ties by link, then target), at most
# GRAPH_LINKS_MAX_GROUPS of them, and the line says how many more there are. The line ends by saying
# the rows above may show only some of these links - a fact, never an instruction (review fix round
# 1): one answer rule decides when the groups are the answer (openai_client.OUTPUT_FORMAT_RULES).
# Names and notes are shortened at white space (`_shorten_words`, ruling W3A3-R1). It only appends -
# it never re-queries and never touches the rows - and an error drops it and nothing else
# (`_companion`). Written from SHAPE: the line names no table, no column and no domain noun.
#
# ONLY THE PRINTED THINGS' OWN LINKS (review of the replay, 2026-10-03). The first version also read
# the links INTO every printed id. Probed over the real graph on the query shape the graph rule
# itself prescribes - every link of one thing, both ends printed - that listed every other thing
# linking to each printed place (a whole system's units recorded in one room, every door on a
# level), and a group mixing links of several kinds showed one kind's note for all of them. A link
# is now read only from a printed id, and a group's note only when all of its links carry it.
#
# WHICH IDS SHARE WHICH TARGETS - wave 4, G11, 2026-10-03. Measured on the goal-function runs: the
# line held the right groups, yet the answer gave half the ids no second target. The groups overlap
# - each id sat in four of them - the head was inflated by place ids another column printed (a
# level's 'contains' links), and the line never said WHICH ids a target serves, so the writer had to
# intersect the groups itself. So an id whose links are ALL of a fan-out kind - a link type with more
# than GRAPH_TARGETS_PER_LINK targets, such as a level's 'contains' - is dropped from the line, unless
# the SQL names it as a literal, being then the very thing asked about (`_graph_ids`; review fix
# round 1, ruling W4A1-R2 - it first kept only the one column printing the most ids, which lost the
# asked item when its targets were printed beside it); and when the ids fall into 2 to
# GRAPH_LINK_SETS_MAX sets by their own links - each id's (link, target) pairs of the other link
# types, fan-out links staying counts in their groups - and some set holds two or more ids, one
# sentence says so (`_link_sets`): the pairs every set shares, once, then each set, largest first,
# with its size, one of its ids and its own pairs. A set with no pair of its own beyond those is never
# said to have no other link without the fan-out links it does have, by kind and count (W4A1-R2).
# ---------------------------------------------------------------------------
GRAPH_LINKS_HEADING = "GRAPH LINKS - "
#: The columns that make a table a dependency graph - `table_router._GRAPH_EDGE_COLUMNS`.
GRAPH_COLUMNS = ("subject_id", "predicate", "object_id")
#: At most this many groups are listed on the line; it says how many more there are.
GRAPH_LINKS_MAX_GROUPS = 8
#: A link type with more distinct targets than this is told as one group, with its counts.
GRAPH_TARGETS_PER_LINK = 4
#: How much of a target's name, and of a link's note, the line shows.
GRAPH_NAME_MAX_CHARS = 80
GRAPH_NOTE_MAX_CHARS = 160
#: The ids of a line are split into sets by their own links only when they fall into at least two
#: and at most this many, and some set holds two or more ids (wave 4, G11).
GRAPH_LINK_SETS_MAX = 4


def _graph_columns(table: dict):
    """`(subject, link, object, target name or None, notes or None)` - the columns of a loaded
    table shaped as a graph (every one of GRAPH_COLUMNS, whatever its case; the target's name is
    the first column whose name says object and name) - or None for any other table."""
    found = [_column_named(table, name) for name in GRAPH_COLUMNS]
    if any(column is None for column in found):
        return None
    name = next((c for c in table.get("columns") or []
                 if {"object", "name"} <= set(re.findall(r"[a-z]+", str(c).lower()))), None)
    return (*found, name, _column_named(table, "notes"))


def _graph_groups(edges) -> list:
    """`[(links, link, target or None, target name, note, targets, sources), …]` - `edges`, each
    `(link, target, name, note, source)`, grouped by link and target, largest first (see the
    comment above); a link type with more than GRAPH_TARGETS_PER_LINK targets is one group whose
    target is None and whose `targets` counts them. `sources` are the ids the group's links start
    from."""
    by_link = {}
    for link, target, name, note, source in edges:
        if link is not None and target is not None:
            by_link.setdefault(link, {}).setdefault(target, []).append((name, note, source))
    groups = []
    for link, targets in by_link.items():
        if len(targets) > GRAPH_TARGETS_PER_LINK:
            groups.append((sum(len(items) for items in targets.values()), link, None, None, None,
                           len(targets),
                           {source for items in targets.values() for _, _, source in items}))
            continue
        for target, items in targets.items():
            name = next((n for n, _, _ in items if n is not None and str(n).strip()), None)
            notes = {re.sub(r"\s+", " ", "" if note is None else str(note)).strip()
                     for _, note, _ in items}
            # A note is the group's only when every link of it carries that same one.
            note = next(iter(notes)) if len(notes) == 1 and "" not in notes else None
            groups.append((len(items), link, target, name, note, 1,
                           {source for _, _, source in items}))
    groups.sort(key=lambda g: (-g[0], str(g[1]), str(g[2] or "")))
    return groups


def _fan_out_links(edges) -> set:
    """The link types among `edges` with more than GRAPH_TARGETS_PER_LINK distinct targets - the
    ones a GRAPH LINKS line tells as one group with its counts."""
    targets = {}
    for link, target, _, _, _ in edges:
        if link is not None and target is not None:
            targets.setdefault(link, set()).add(target)
    return {link for link, ends in targets.items() if len(ends) > GRAPH_TARGETS_PER_LINK}


def _graph_ids(ids, edges, sql: str):
    """`(ids, edges)` a GRAPH LINKS line covers, of the printed graph subjects `ids` and their
    links `edges` (review fix round 1, ruling W4A1-R2): an id every link of which is of a fan-out
    kind (`_fan_out_links`) - a level's 'contains', say, printed beside the things it holds - is
    dropped, unless the SQL names it as a string literal, so the very thing asked about is never
    lost; and when that would drop every id, none is dropped."""
    fan_out = _fan_out_links(edges)
    if not fan_out:
        return ids, edges
    named = {str(t[2]).strip() for t in sql_token_spans(sql) or [] if t[0] == "str"}
    kinds = {}
    for link, target, _, _, source in edges:
        if link is not None and target is not None:
            kinds.setdefault(source, set()).add(link)
    kept = [i for i in ids if i in named or not kinds.get(i, set()) <= fan_out]
    if not kept or len(kept) == len(ids):
        return ids, edges
    keep = set(kept)
    return kept, [edge for edge in edges if edge[4] in keep]


def _link_sets(edges, ids) -> str:
    """The sentence splitting `ids` into sets by their own links, or "" (wave 4, G11) - see the
    comment above. `edges` are the line's links, each `(link, target, name, note, source)`. A set
    with no pair of its own beyond the shared ones names, by kind and count, the fan-out links its
    ids do have, rather than saying they have no other link (ruling W4A1-R2)."""
    fan_out = _fan_out_links(edges)
    own = {i: set() for i in ids}
    fanned = {i: {} for i in ids}
    for link, target, _, _, source in edges:
        if link is None or target is None or source not in own:
            continue
        if link in fan_out:
            fanned[source][link] = fanned[source].get(link, 0) + 1
        else:
            own[source].add((str(link), str(target)))
    sets = {}
    for i in ids:
        sets.setdefault(frozenset(own[i]), []).append(i)
    if not 2 <= len(sets) <= GRAPH_LINK_SETS_MAX or len(sets) == len(ids):
        return ""
    shared = frozenset.intersection(*sets)

    def pairs(links):
        return ", ".join(f"{link} {target}" for link, target in sorted(links))

    ordered = sorted(sets.items(), key=lambda s: (-len(s[1]), sorted(s[0])))
    parts = []
    for n, (links, members) in enumerate(ordered):
        size = (f"the other {len(members)}" if n == len(ordered) - 1 and n
                else f"{len(members)} of them")
        example = f" ({members[0]}{' among them' if len(members) > 1 else ''})"
        verb = "has" if len(members) == 1 else "have"
        extra = links - shared
        if extra:
            parts.append(f"{size}{example} {'also ' if shared else ''}{verb} {pairs(extra)}")
            continue
        counts = {}
        for i in members:
            for link, k in fanned[i].items():
                counts[link] = counts.get(link, 0) + k
        if not counts:
            parts.append(f"{size}{example} {verb} no other link")
            continue
        parts.append(f"{size}{example} {verb} no {'other ' if shared else ''}link of the kinds "
                     f"above; " + ", ".join(f"{link} - {k} more not grouped"
                                            for link, k in sorted(counts.items())))
    head = f"By their own links the {len(ids)} ids fall into {len(sets)} sets - "
    if shared:
        head += f"all share {pairs(shared)}; "
    return head + "; ".join(parts) + "."


def _graph_links_line(con, sql: str, tables: list, result) -> str:
    """The GRAPH LINKS line(s) for `sql`, or "" when they do not apply - see the comment above.
    One query per graph table the SQL reads whose subject ids the result prints; the caller drops
    the line, and nothing else, if one raises."""
    cells = {str(v).strip() for row in result or [] for v in row
             if v is not None and str(v).strip()}
    if not cells:
        return ""
    lines = []
    for table in tables:
        shape = _graph_columns(table) if table.get("rows") else None
        if shape is None or not _sql_reads_table(sql, table["table_name"]):
            continue
        subject, link, target, name, notes = shape
        ids = sorted({str(row.get(subject)).strip() for row in table["rows"]
                      if row.get(subject) is not None and str(row.get(subject)).strip() in cells})
        if not ids:
            continue
        edges = _execute_with_timeout(con, (
            f"SELECT {_text_sql(link)}, {_text_sql(target)}, "
            f"{_quoted_name(name) if name else 'NULL'}, {_quoted_name(notes) if notes else 'NULL'}, "
            f"{_text_sql(subject)} FROM {_quoted_name(table['table_name'])} "
            f"WHERE {_text_sql(subject)} IN ({', '.join(_sql_literal(i) for i in ids)}) "
            f"ORDER BY rowid"))
        # Ids whose links are all of a fan-out kind are dropped, the asked item never (W4A1-R2).
        ids, edges = _graph_ids(ids, edges, sql)
        groups = _graph_groups(edges)
        if not groups:
            continue
        parts, said = [], set()
        for links, kind, end, end_name, note, targets, sources in groups[:GRAPH_LINKS_MAX_GROUPS]:
            # On a line over several ids, a group whose links all start from one names it.
            source = f", from {next(iter(sources))}" if len(ids) > 1 and len(sources) == 1 else ""
            if end is None:
                parts.append(f"{kind} - {_counted(links, 'link')} to {targets} different targets"
                             f"{source}")
                continue
            part = f"{kind} {end}"
            if end_name is not None and str(end_name).strip():
                part += f" ({_shorten_words(end_name, GRAPH_NAME_MAX_CHARS)})"
            part += f" - {_counted(links, 'link')}{source}"
            if note and note not in said:
                said.add(note)
                part += f": {_shorten_words(note, GRAPH_NOTE_MAX_CHARS)}"
            parts.append(part)
        more = len(groups) - GRAPH_LINKS_MAX_GROUPS
        line = (f"{GRAPH_LINKS_HEADING}every link the dependency graph records from the "
                f"{_counted(len(ids), 'id')} the rows above print, grouped by link and target: "
                + "; ".join(parts))
        if more > 0:
            line += f"; … and {_counted(more, 'more group')}"
        # Which ids share which targets, when they fall into a few sets (wave 4, G11).
        sets = _link_sets(edges, ids)
        # A fact, never an instruction (review fix round 1): the answer rule decides when the
        # groups are the answer.
        lines.append(line + "." + (f" {sets}" if sets else "")
                     + " The rows above may show only some of these links.")
    return "\n".join(lines)


def _current_twin(name):
    """The current-state table a pre-takeover / historical table pairs with by name - the name with
    its first 'existing_' taken out, when it holds '_existing_' - or None. The one pairing rule both
    the SQL prompt's existing-table note (`execute_sql_query`) and the shared-value line read."""
    text = str(name or "")
    return text.replace("existing_", "", 1) if "_existing_" in text else None


# ---------------------------------------------------------------------------
# A SHARED VALUE, LOOKED FOR OUTSIDE A SELF-JOIN'S OWN TABLE - wave 3, G8, 2026-10-03.
#
# Measured on the goal-function run of 2026-10-01: asked whether any other unit used one unit's
# network address, the writer joined a DERIVED table - one row per unit of the register - to itself
# on the address, excluding the unit itself (`a.C = b.C AND a.K <> b.K`). That table cannot hold a
# row the register lacks, and the commissioning sheet's untagged row with the same address is not in
# the register: the self-join found no partner, and the answer said no other unit used the address.
# The commissioning table, loaded beside it, held the clash - and a note saying so.
#
# So, for ONE top-level SELECT (no set operation; no GROUP BY, HAVING, QUALIFY, WINDOW, LIMIT or
# OFFSET at its top - an ORDER BY is fine) whose FROM and JOINs name one loaded table twice, under
# two names, with `a.C = b.C` on one column C and an inequality on one column K - `a.K <> b.K`
# (or `!=`), or the partner's K unequal to a literal - outside any sub-SELECT (`_self_join`): the
# query's own FROM ... WHERE is re-read for the asked side's C and K and the partner's K (the asked
# side is the name declared first). The C values of the asked rows that found NO partner are looked
# up in every OTHER loaded table that has columns named C and K, leaving out the rows whose K is one
# of the asked rows' own (in any letter case or spacing), and each table that holds one adds a line
# naming the rows it holds - every non-blank cell, its notes cut by `_cut_note` to
# SHARED_NOTE_MAX_CHARS. A self-join whose asked rows all found a partner adds nothing; a table
# without K adds nothing either (the asked row itself could not be told apart there); more than
# SHARED_VALUES_MAX values is no "does another row share this" probe and adds nothing. The rows are
# never touched; an error drops the line and nothing else (`_companion`). One answer rule reads it
# (openai_client.OUTPUT_FORMAT_RULES). Written from SHAPE: no table, column or value is named.
#
# Fix round 1 (the review's minor 4): a row another record prints with the same value may be another
# item or the same item printed another way, so the line says the record PRINTS the same value and
# says both readings - never that the value is shared. And the asked table's pre-takeover /
# historical twin (`_current_twin`, the pairing the SQL prompt's existing-table note uses) is never
# searched: it prints the same items as they were before the takeover, not other rows of today's.
# Cell values are shortened at white space (`_shorten_words`, ruling W3A3-R1).
# ---------------------------------------------------------------------------
SHARED_VALUE_HEADING = "SHARED VALUE ELSEWHERE - "
#: More asked values than this is no "does another row share this value" probe: nothing is said.
SHARED_VALUES_MAX = 20
#: At most this many rows of one other table are written into its line.
SHARED_ROWS_SHOWN = 5
#: At most this many other tables get a line.
SHARED_TABLES_MAX = 3
#: How much of each cell, and of a row's notes, the line shows.
SHARED_VALUE_CHARS = 80
SHARED_NOTE_MAX_CHARS = 300
SHARED_NOTE_HEAD_CHARS = 150

#: How `_self_join` reads a self-join probe: the table, the asked and partner names it is joined
#: under, the shared column C and the key column K as written, the literal the partner's K is
#: compared unequal to (None for `a.K <> b.K`), and the query's FROM ... WHERE.
_SelfJoin = namedtuple("_SelfJoin", "table asked partner column key literal from_where")


def _self_join(sql: str, table_names):
    """A `_SelfJoin` when `sql` is a 'does another row share C' probe over one of `table_names`
    (see the comment above), else None. Reads the SQL with `sql_loop`'s tokenizer."""
    tokens = sql_token_spans(sql)
    if not tokens or _word(tokens[0]) != "SELECT":
        return None
    depths = _paren_depths(tokens)
    words = [_word(t) for t in tokens]
    top = [k for k in range(len(tokens)) if depths[k] == 0]
    froms = [k for k in top if words[k] == "FROM"]
    if len(froms) != 1 or any(words[k] in _SET_OPERATIONS for k in top):
        return None
    clauses = [k for k in top if k > froms[0] and words[k] in _CLAUSES_AFTER_WHERE]
    if any(words[k] != "ORDER" for k in clauses):
        return None
    # Each FROM / JOIN of the query's own: the table and the name it goes by, in declared order.
    declared = {}
    for k in top:
        if k >= froms[0] and words[k] in ("FROM", "JOIN"):
            ref = _table_ref(tokens, k + 1)
            if ref:
                declared.setdefault(str(ref[1] or ref[0]).lower(), (ref[0], len(declared)))
    # Tokens inside a sub-SELECT belong to another query.
    inner, stack = [], []
    for k, token in enumerate(tokens):
        if token[1] == "(":
            stack.append(k + 1 < len(tokens) and words[k + 1] == "SELECT")
        inner.append(any(stack))
        if token[1] == ")" and stack:
            stack.pop()

    def column_ref(j):
        if (j + 2 < len(tokens) and _is_name(tokens[j]) and tokens[j + 1][1] == "."
                and _is_name(tokens[j + 2]) and not (j and tokens[j - 1][1] == ".")):
            return str(tokens[j][2]), str(tokens[j + 2][2])
        return None

    equal, unequal = [], []
    for j in range(froms[0], len(tokens)):
        left = None if inner[j] else column_ref(j)
        if left is None or j + 3 >= len(tokens):
            continue
        op, right = tokens[j + 3][1], column_ref(j + 4)
        if op == "=" and right:
            equal.append((left, right))
        elif op in ("<>", "!="):
            if right:
                unequal.append((left, right))
            elif j + 4 < len(tokens) and tokens[j + 4][0] == "str":
                unequal.append((left, ("", tokens[j + 4][2])))
    loaded = {str(t).lower(): t for t in table_names}
    for (a, column), (b, other) in equal:
        if a.lower() == b.lower() or column.lower() != other.lower():
            continue
        first, second = declared.get(a.lower()), declared.get(b.lower())
        if not first or not second or str(first[0]).lower() != str(second[0]).lower():
            continue
        table = loaded.get(str(first[0]).lower())
        if table is None:
            continue
        asked, partner = (a, b) if first[1] < second[1] else (b, a)
        pair = {asked.lower(), partner.lower()}
        for (x, key), right in unequal:
            if x.lower() not in pair:
                continue
            literal = right[1] if not right[0] else None
            if literal is not None or (right[0].lower() in pair and right[0].lower() != x.lower()
                                       and right[1].lower() == key.lower()):
                end = tokens[clauses[0]][3] if clauses else len(sql)
                return _SelfJoin(table, asked, partner, column, key, literal,
                                 sql[tokens[froms[0]][3]:end].rstrip())
    return None


def _shared_row(col_names, row) -> str:
    """One row of another table written into the line: `(col = value, …)`, blank cells left out,
    each value cut to SHARED_VALUE_CHARS - its notes, when it has them, by `_cut_note`."""
    parts = []
    for column, value in zip(col_names, row):
        if isinstance(value, float) and value.is_integer():
            value = int(value)  # a whole number as the sheet prints it, never "20.0"
        text = re.sub(r"\s+", " ", "" if value is None else str(value)).strip()
        if not text:
            continue
        if str(column).lower() == "notes":
            text = _cut_note(text, SHARED_NOTE_MAX_CHARS, SHARED_NOTE_HEAD_CHARS)
        else:
            text = _shorten_words(text, SHARED_VALUE_CHARS)
        parts.append(f"{column} = {text}")
    return "(" + ", ".join(parts) + ")"


def _shared_value_line(con, sql: str, tables: list) -> str:
    """The SHARED VALUE ELSEWHERE line(s) for `sql`, or "" when they do not apply - see the
    comment above. One re-read of the query's FROM ... WHERE, then one query per other table
    holding both columns; the caller drops the lines, and nothing else, if any raises."""
    probe = _self_join(sql, [t["table_name"] for t in tables if t.get("rows")])
    if probe is None:
        return ""
    asked, partner = _quoted_name(probe.asked), _quoted_name(probe.partner)
    column, key = _quoted_name(probe.column), _quoted_name(probe.key)
    rows = _execute_with_timeout(con, (f"SELECT DISTINCT {asked}.{column}, {asked}.{key}, "
                                       f"{partner}.{key} {probe.from_where}"))

    def fold(value):
        return re.sub(r"\s+", " ", "" if value is None else str(value)).strip().lower()

    keys = sorted({fold(k) for _, k, _ in rows if fold(k)})
    values = list(dict.fromkeys(str(v).strip() for v, _, found in rows
                                if fold(v) and not fold(found)))
    if not values or not keys or len(values) > SHARED_VALUES_MAX:
        return ""
    if probe.literal is not None and fold(probe.literal) not in keys:
        return ""  # a K compared unequal to some other value excludes no asked row
    asked = str(probe.table).lower()
    twins = {str(_current_twin(probe.table) or "").lower()} | {
        str(t["table_name"]).lower() for t in tables
        if str(_current_twin(t["table_name"]) or "").lower() == asked}
    lines = []
    for table in tables:
        name = str(table["table_name"]).lower()
        if not table.get("rows") or name == asked or name in twins:
            continue  # the asked table, and its pre-takeover / historical twin
        held, own = _column_named(table, probe.column), _column_named(table, probe.key)
        if held is None or own is None:
            continue
        found = _execute_with_timeout(con, (
            f"SELECT * FROM {_quoted_name(table['table_name'])} "
            f"WHERE lower(trim(CAST({_quoted_name(held)} AS VARCHAR))) IN "
            f"({', '.join(_sql_literal(fold(v)) for v in values)}) "
            f"AND ({_quoted_name(own)} IS NULL OR lower(trim(CAST({_quoted_name(own)} AS VARCHAR))) "
            f"NOT IN ({', '.join(_sql_literal(k) for k in keys)})) "
            f"ORDER BY rowid LIMIT {SHARED_ROWS_SHOWN + 1}"))
        if not found:
            continue
        names = [d[0] for d in con.description]
        many = len(found) > SHARED_ROWS_SHOWN
        shown = ", ".join(_shared_row(names, row) for row in found[:SHARED_ROWS_SHOWN])
        lines.append(
            f"{SHARED_VALUE_HEADING}the query above looked for another row sharing {probe.column} "
            f"only inside {probe.table} and found none there for "
            f"{_and_list(_sql_literal(v) for v in values[:SHARED_ROWS_SHOWN])}; "
            f"{table['table_name']} prints the same {probe.column} on "
            + (f"more than {_counted(SHARED_ROWS_SHOWN, 'other row')}, the first {SHARED_ROWS_SHOWN}"
               if many else _counted(len(found), "other row"))
            + f" - another item, or the same item printed another way: {shown}")
        if len(lines) >= SHARED_TABLES_MAX:
            break
    return "\n".join(lines)


def _companion(label: str, build, *args) -> str:
    """`build(*args)`, or "" when it raises. A companion line is an aid to the answer: an
    error loses the line and never the answer it rides with."""
    try:
        return build(*args) or ""
    except Exception as e:
        logger.warning(f"{label}: not added ({type(e).__name__}: {e}); "
                       f"the result goes without it")
        return ""


# ---------------------------------------------------------------------------
# A PLACE CODE THE QUESTION PRINTS, RESOLVED TO ITS LOCATION KEY - spec-fix3 part (d),
# 2026-10-01.
#
# Measured on the goal-function run of 2026-09-30: a question named a room by a design tag
# printed nowhere but as the END of that room's location key. The writer compared the tag
# with '=' against columns that print the key or a bare number, matched nothing three steps
# running, and the answer said no asset list was on record for the room. The places table
# knows - one row per place, keyed by the location id, with a kind - so the code is resolved
# here, in code, before the writer runs. A coded token the question prints (`place_tokens`)
# that EQUALS, or WHOLE-SEGMENT-ENDS (the key ends with '-' plus the token), exactly ONE
# location id of kind 'room' adds one line to the SQL-writer prompt:
#
#     The place <token> is location_id <id> (<display_name>)
#
# A level-and-block code never resolves - it is no room. An asset tag never resolves: no key
# ends with a whole tag, and a code printed inside a longer tag is never a token of its own.
# A token matching two rooms adds nothing. A bare number is never a token at all, because a
# key can end in one and would pin the wrong room (measured on the ruler: '1', '2', '8' and a
# three-digit number each pinned one; the shape rule is load-bearing). The question is read
# WITHOUT the loop's step suffix, so every step of an investigation carries the same lines
# and a code quoted from a previous SQL is never resolved.
#
# T11a-R3: the places table is found generically - the table the cards' declared joins name
# for the location key, else a table carrying the location key and a kind column whose
# location key is its own key (non-blank and unique on every row) - and never by its name.
# When routing did not load it, it is read from the user's tables `execute_sql_query` has
# already fetched, so no second read is made. Any error drops the lines and nothing else
# (`_companion`). T11a-R1: the line only AIDS a question naming one place;
# ROOM_CONTENTS_RULE's name filter is untouched.
# ---------------------------------------------------------------------------
#: The line, as the spec words it; " (<display_name>)" follows when the places table has a
#: display column and the room's cell is not blank.
PLACE_LINE = "The place {token} is location_id {location_id}"
PLACE_CODE_MIN_CHARS = 4
#: The places table's kind for one room - the only kind a printed code resolves to.
_ROOM_KIND = "room"
#: The column naming each place's kind, on the places table.
_KIND_COLUMN = "kind"
#: One segment of a printed code - letters and digits, with an optional bracketed group as in
#: '06(B)' - and a code: segments joined by '.' or '-', never starting or ending inside a
#: longer code. A full stop that ends a sentence, or prose brackets around a code, are not
#: part of it.
_CODE_SEGMENT = r"[A-Za-z0-9]+(?:\([A-Za-z0-9]+\))?"
_PLACE_TOKEN_RE = re.compile(r"(?<![\w.\-/])" + _CODE_SEGMENT + r"(?:[.\-]" + _CODE_SEGMENT
                             + r")*(?![\w\-/(]|\.\w)")

# T11a-R8 (fix round 1): a NUMBER-ONLY token - no letter - is often a measurement, and a
# building can number half its rooms with two-decimal numbers, so "the 8.71 kW ..." named a
# room. Such a token names a place only when it is no measurement: never when a unit of
# measure follows it, with or without a space, in any case; never when '/', 'x', '×' or '-'
# joins it to another number. Generic SI and trade units, from shape - none is this building's.
# A token with a letter keeps its old behaviour. One refinement: a lower-case 'a' followed by a
# word is the English article ('is room 8.71 a lab?'), never amperes.
_UNITS = ("kvarh", "kvar", "kvah", "kwh", "mwh", "kva", "mva", "kw", "mw", "va", "var", "mah", "ah",
          "ma", "kv", "khz", "hz", "mm²", "mm2", "cm²", "cm2", "m²", "m2", "m³", "m3", "sq. m",
          "sq m", "sqm", "sq ft", "sqft", "mm", "cm", "km", "litres", "litre", "liters", "liter",
          "ltrs", "ltr", "kg", "hours", "hour", "hrs", "hr", "mins", "min", "°c", "°f", "deg c",
          "degc", "lux", "lx", "db", "rpm", "kpa", "pa", "psi", "%", "w", "v", "a", "m", "l", "h")
_UNIT_AFTER_RE = re.compile(r"[ \t]*(" + "|".join(re.escape(u) for u in sorted(
    _UNITS, key=len, reverse=True)) + r")(?![A-Za-z0-9²³])", re.IGNORECASE)
_ARTICLE_AFTER_RE = re.compile(r"[ \t]+[A-Za-z]")
_JOINED_AFTER_RE = re.compile(r"[ \t]*[/x×\-][ \t]*\d", re.IGNORECASE)
_JOINED_BEFORE_RE = re.compile(r"\d[ \t]*[/x×\-][ \t]*$", re.IGNORECASE)


def _is_measurement(text: str, start: int, end: int) -> bool:
    """True when the number-only token `text[start:end]` is a measurement (T11a-R8): a unit of
    measure follows it, or '/', 'x', '×' or '-' joins it to another number - or it is itself
    numbers joined by '-'. Pure."""
    if "-" in text[start:end]:
        return True
    after, before = text[end:], text[:start]
    unit = _UNIT_AFTER_RE.match(after)
    if unit and not (unit.group(1) == "a" and _ARTICLE_AFTER_RE.match(after, unit.end(1))):
        return True
    return bool(_JOINED_AFTER_RE.match(after) or _JOINED_BEFORE_RE.search(before))


def place_tokens(text: str) -> list:
    """Every coded token `text` prints that could name a place, in the order printed, each once
    whatever its case: at least PLACE_CODE_MIN_CHARS long, with a digit, and with a letter or a
    '.' / '-' joiner - so never a bare number - and, when it has no letter, no measurement
    (`_is_measurement`, T11a-R8). Pure."""
    text = text or ""
    found, seen = [], set()
    for match in _PLACE_TOKEN_RE.finditer(text):
        token = match.group(0)
        if (len(token) < PLACE_CODE_MIN_CHARS or not any(ch.isdigit() for ch in token)
                or not any(ch.isalpha() or ch in ".-" for ch in token)):
            continue
        if (not any(ch.isalpha() for ch in token)
                and _is_measurement(text, match.start(), match.end())):
            continue
        if token.upper() not in seen:
            seen.add(token.upper())
            found.append(token)
    return found


def resolve_place_codes(text: str, rows, key_col, kind_col, name_col=None) -> list:
    """`[(token, location id, display name or ""), …]` - for each of `text`'s `place_tokens`, in
    the order printed, the ONE location id of kind 'room' in `rows` that equals the token or
    ends with '-' plus it, whatever the case. A token matching no room, or two, gives nothing,
    and a room is named once. Pure."""
    rooms = {}
    for row in rows:
        if str(row.get(kind_col) or "").strip().lower() != _ROOM_KIND:
            continue
        key = str(row.get(key_col) or "").strip()
        if key and key.upper() not in rooms:
            name = re.sub(r"\s+", " ", str(row.get(name_col) or "")).strip() if name_col else ""
            rooms[key.upper()] = (key, name)
    resolved, named = [], set()
    for token in place_tokens(text):
        wanted = token.upper()
        hits = [k for k in rooms if k == wanted or k.endswith("-" + wanted)]
        if len(hits) == 1 and hits[0] not in named:
            named.add(hits[0])
            resolved.append((token,) + rooms[hits[0]])
    return resolved


def _column_named(table: dict, name):
    """The column of `table` spelled `name`, whatever its case, or None."""
    wanted = str(name).strip().lower()
    return next((c for c in table.get("columns") or [] if str(c).lower() == wanted), None)


def _places_table(cards: list, loaded: list, every: list):
    """`(table, key column, kind column, display column or None)` of the places table, or None
    (T11a-R3). First the table the cards' declared joins name for the location key - the one
    most of them name - when the user has it with that key column and a kind column; else a
    table carrying the location key and a kind column whose location key is its own key,
    non-blank and unique on every row, the loaded tables first. Never by its name: `loaded` are
    the tables routing loaded, `every` all of the user's, and a table is read from whichever
    holds it."""
    by_name = {}
    for table in list(loaded) + list(every):
        by_name.setdefault(str(table.get("table_name") or "").lower(), table)

    def display_of(table):
        return next((c for c in table.get("columns") or [] if "display" in str(c).lower()), None)

    votes = {}
    for card in cards or []:
        for join in card.get("declared_joins") or []:
            found = _DECLARED_JOIN_RE.search(str(join))
            if found and found.group(2).strip().lower() == _LOCATION_KEY:
                target = (found.group(3).strip(), found.group(4).strip())
                votes[target] = votes.get(target, 0) + 1
    for (name, key_name), _ in sorted(votes.items(), key=lambda kv: (-kv[1], kv[0])):
        table = by_name.get(name.lower())
        if table is None:
            continue
        key, kind = _column_named(table, key_name), _column_named(table, _KIND_COLUMN)
        if key is not None and kind is not None:
            return table, key, kind, display_of(table)
    for table in list(loaded) + list(every):
        key, kind = _column_named(table, _LOCATION_KEY), _column_named(table, _KIND_COLUMN)
        if key is None or kind is None:
            continue
        keys = [str(row.get(key) or "").strip() for row in table.get("rows") or []]
        if keys and all(keys) and len(set(keys)) == len(keys):
            return table, key, kind, display_of(table)
    return None


def _place_lines(question: str, cards: list, loaded: list, every: list) -> str:
    """The prompt's place lines for `question` - one per printed code that resolves to a
    single room (`resolve_place_codes`) - or "" when none does. The question is read without
    the loop's step suffix. The caller drops the lines, and nothing else, if this raises."""
    text = _split_step_suffix(question)[0]
    if not place_tokens(text):
        return ""
    places = _places_table(cards, loaded, every)
    if places is None:
        return ""
    table, key_col, kind_col, name_col = places
    lines = []
    for token, location_id, name in resolve_place_codes(text, table.get("rows") or [],
                                                        key_col, kind_col, name_col):
        line = PLACE_LINE.format(token=token, location_id=location_id)
        lines.append(f"{line} ({name})" if name else line)
    return "\n".join(lines)


def _fix_table_names(sql: str, real_table_names: list[str],
                      all_table_names: list[str] | None = None) -> str:
    """Fix truncated or incorrect table names in generated SQL by fuzzy matching.

    `real_table_names` are the tables actually loaded into DuckDB for this
    query — the router's (possibly narrowed) selection — so a fuzzy rewrite
    only ever lands on one of those; anything else isn't queryable anyway.

    `all_table_names` is every table that genuinely exists for this user
    (defaults to `real_table_names` when the caller has no wider set, e.g. no
    routing happened). A SQL reference that EXACTLY matches one of THOSE is
    left untouched even when the router excluded it from `real_table_names` —
    rewriting it onto the closest routed table would silently swap in a
    different table's data (the router dropping a needed table must surface
    as a loud "table not found", never as a plausible wrong number computed
    from the wrong table).
    """
    if all_table_names is None:
        all_table_names = real_table_names
    # Find all table references in SQL: FROM "xxx" or FROM xxx or JOIN "xxx" etc.
    # Also handle unquoted table names
    words = re.findall(r'(?:FROM|JOIN)\s+"?([a-zA-Z0-9_]+)"?', sql, re.IGNORECASE)
    real_set = set(real_table_names)
    all_set = set(all_table_names)

    for word in words:
        if word in real_set:
            continue  # Already correct
        if word in all_set:
            # Genuinely exists for this user, just not in the routed subset —
            # do NOT fuzzy-rewrite it onto some other routed table. Leave it
            # so DuckDB rejects it loudly; that is the safe failure mode.
            continue

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


# ---------------------------------------------------------------------------
# ROUTING-TEXT NORMALISATION — spec-fix11 (2026-09-30), evaluated in
# task-11-diagnosis.md. `openai_client.py`'s tool dispatch hands `select_tables`
# the SAME wrapped text it hands the SQL-generation prompt below:
#     f"{last_user_msg}\n(Additional context from the assistant: {question})"
# The wrapper's own fixed words carry no routing signal, and can coincidentally
# collide with a real column or vocabulary word on some unrelated card, so a
# bare question that mentions nothing of the kind can still put that card in
# the ranked selection purely because it was wrapped. Measured: stripping the
# literal phrase (never the rephrase words that follow it) reproduces the
# bare-question route on every one of the 32 recorded wrapped routes and
# restores 8 evidence tables over a 326-card simulation, at a cost of 0
# (task-11-diagnosis.md). Applied ONLY to the text handed to `select_tables` —
# the SQL-generation prompt below still receives `question` untouched,
# boilerplate and all.
# ---------------------------------------------------------------------------
_WRAPPER_BOILERPLATE = "(Additional context from the assistant: "
_WRAPPER_STRIPPED = "("


def _routing_text(question: str) -> str:
    """`question` with the wrapper's fixed boilerplate phrase reduced to a
    bare '(' — the rephrase's own words that follow it are kept, so nothing
    of the question's own content is lost, only the fixed carrier phrase. A
    question that was never wrapped (or does not carry this exact phrase)
    passes through unchanged."""
    return (question or "").replace(_WRAPPER_BOILERPLATE, _WRAPPER_STRIPPED)


# ---------------------------------------------------------------------------
# THE LOOP'S STEP SUFFIX, UNION-ROUTED — spec-fix7(b) (2026-09-30), evaluated
# in task-7-diagnosis.md. `sql_loop.step_instruction_suffix` appends
# "\n(Investigation step N: <instruction> Previous SQL: `<sql>`)" to the
# ORIGINAL question for every re-query
# (`ask = question + step_instruction_suffix(...)` in
# `sql_loop.iter_sql_investigation` — always the top-level `question`, never
# the previous `ask`, so this marker appears at most once in any text this
# module ever routes). Routing on that full text alone can drop a table step
# 1 actually needed: the instruction's own wording, and any coded value
# quoted from the previous SQL, can outscore the step-1 selection entirely
# and push it out of the ranked top-k. The fix routes on the text WITHOUT the
# suffix (exactly what step 1 itself was routed on) UNION the full text, base
# selection first, so a step-1 table is never dropped and a table the
# instruction newly points at is still added.
# ---------------------------------------------------------------------------
_STEP_SUFFIX_MARKER = "\n(Investigation step "


def _split_step_suffix(text: str):
    """(text-without-the-loop-suffix, carries_a_suffix). `text` is returned
    unchanged, and `carries_a_suffix` is False, when no loop suffix is
    present — the ordinary, non-looping case."""
    text = text or ""
    idx = text.find(_STEP_SUFFIX_MARKER)
    if idx == -1:
        return text, False
    return text[:idx], True


# ---------------------------------------------------------------------------
# A RE-QUERY'S LISTED VALUES ARE NEVER ROUTED ON — spec-fix7(c), review fix 1
# (2026-10-01). An EMPTY re-query's instruction lists the real values of the
# columns the failed WHERE filtered (`sql_loop._filtered_values_text`), and the
# full-text arm above read them like any other step text: the data's own words
# and hyphenated codes pulled in tables that merely print the same ones, and over
# the recorded re-queries the mean route grew from 12.7 tables to 16.9. The values
# come from a table the failed query ALREADY read, so routing on them can only
# add noise. They are cut from the ROUTED text only; the SQL writer is handed the
# question whole. Where the clause starts and ends is read from `sql_loop`'s own
# two constants, so the two modules cannot drift apart.
# ---------------------------------------------------------------------------
def _without_values_clause(text: str) -> str:
    """`text` with an EMPTY instruction's values clause cut out: from the space
    before `VALUES_LEAD` up to, not including, the `PREVIOUS_SQL_MARKER` after it
    (to the end of `text` should no marker follow), so what is left is byte for
    byte the step text without its values. Returned unchanged when it carries no
    clause - routing is then exactly what it was. Pure."""
    start = text.find(VALUES_LEAD)
    if start == -1:
        return text
    end = text.find(PREVIOUS_SQL_MARKER, start)
    if end == -1:
        end = len(text)
    if start and text[start - 1] == " ":
        start -= 1
    return text[:start] + text[end:]


def _routed_table_names(question: str, cards: list[dict], k: int = 3) -> list[str]:
    """The table names BOTH `route_tables` and `execute_sql_query` select for
    `question` — the one place either routing entry point calls into
    `table_router.select_tables`, so a fix to routing is a fix to both
    (spec-fix11, spec-fix7(b)).

    Normalises the wrapper boilerplate first (spec-fix11) — this alone can
    change what even a plain, un-looped question routes to, so it always
    runs. Only when what is left still carries a loop step suffix does a
    second call run: the pre-suffix text (spec-fix7(b)'s "text without the
    suffix", identical to what step 1 itself was routed on) UNION the full
    text, base selection first so a step-1 table is never dropped, then any
    table only the full text's own added words point at. Deterministic and
    ordered throughout — both calls into `select_tables` are themselves
    deterministic (see its own docstring for why), and the merge below
    preserves the base call's order before appending anything new.

    The full text is routed with an EMPTY instruction's listed values cut out
    of its step suffix (`_without_values_clause`, spec-fix7(c) review fix 1);
    a suffix with no values clause is routed exactly as before.
    """
    normalized = _routing_text(question)
    base_text, has_suffix = _split_step_suffix(normalized)
    base_selected = select_tables(base_text, cards, k=k)
    if not has_suffix:
        return base_selected
    full_selected = select_tables(
        base_text + _without_values_clause(normalized[len(base_text):]), cards, k=k)
    merged = list(base_selected)
    for name in full_selected:
        if name not in merged:
            merged.append(name)
    return merged


def route_tables(question: str, user_id: str, supabase_client,
                 live_table_names=None) -> list[dict]:
    """The CARDS the router picks for `question` - the same selection
    `execute_sql_query` makes below, returned as cards rather than as the
    `structured_data` rows it narrows.

    `sql_loop.inspect_result` needs the cards, not the tables: what it asks of a
    result is whether the identifier column a card DECLARES came back, and which
    of that card's columns are worth widening to. Deliberately the same two calls
    in the same order, with the same fallbacks - no cards, or a cards file of the
    wrong shape, yields `[]`, which the inspector documents as "the column-shaped
    issues never fire".

    `live_table_names` is the third of those fallbacks, and it is the one that
    keeps the inspector honest. `execute_sql_query` narrows `tables` by the
    selection and, when the selection matches NO live table, falls back to the
    full schema - so a card whose table is not live was never shown to the SQL
    writer. The cards drifting behind the corpus is an observed condition, not a
    hypothetical (see `_load_table_cards`: "drifted four tables behind the live
    corpus"), and in that state an unfiltered card list lets the loop demand an
    identifier column of a table nobody queried, spending a whole step on an
    instruction that cannot be obeyed. Filtering here mirrors the narrowing
    there; an empty intersection yields `[]`, mirroring the full-schema fallback,
    which the inspector reads as "no cards".

    An EMPTY or omitted `live_table_names` means the caller does not know which
    tables are live, which is not the same as none being live: it filters nothing.
    """
    cards = _load_table_cards(user_id, supabase_client)
    if not cards:
        return []
    try:
        selected = set(_routed_table_names(question, cards, k=3))
    except Exception as e:
        logger.warning(f"table_router: routing failed ({type(e).__name__}: {e}); "
                       f"the investigation runs without cards")
        return []
    routed = [c for c in cards if c.get("table") in selected]
    live = {str(t) for t in (live_table_names or []) if t}
    if live:
        routed = [c for c in routed if c.get("table") in live]
        if not routed:
            logger.warning("table_router: the routed cards name no live table "
                           "(cards out of sync with structured_data?); the "
                           "investigation runs without cards")
    return routed


def column_value_reader(user_id: str, supabase_client):
    """`read(table, column) -> [value, …] | None` over this user's tables AS
    `execute_sql_query` LOADS THEM - the `column_values` the app hands
    `sql_loop.iter_sql_investigation`, whose EMPTY re-query lists the real values
    of the columns a failed WHERE filtered (spec-fix7 part c, 2026-10-01).

    The same rows (`structured_data`, for this user and this table), the same
    column types (`_infer_column_types` over those rows) and the same cell
    conversion (`_loaded_value`), so a value the SQL writer is shown is one DuckDB
    will match. What comes back is the column's DISTINCT values as text, in the
    order the table first holds them, a DOUBLE printed the way a result table
    prints it ("35.0"); NULLs are left out and blanks kept - which of them to show
    is the loop's decision, not this reader's.

    LAZY and MEMOISED: building it reads nothing, and each table is fetched at most
    once however many of its columns are asked for - so a question whose loop never
    needs the values never pays for them. Anything it cannot answer (a table the
    user does not have, a column the table does not have, a fetch that fails) is
    None, never an exception: the values are an aid to a re-query and must never
    cost it.

    `read.columns(table)` lists the table's own columns as loaded, from the same
    fetch, or None (spec-fix3 part c, T11a-R6). The loop needs it because a column
    stamped onto the tables after the cards were written - the level code - is on no
    card: it places a filtered column no card lists, and tells whether a table has a
    level code to ask a storey through."""
    loaded = {}

    def fetched(table):
        key = str(table or "").lower()
        if key not in loaded:
            loaded[key] = _fetch_loaded_table(key, user_id, supabase_client)
        return loaded[key]

    def columns(table):
        found = fetched(table)
        return None if found is None else list(found[0])

    def read(table, column):
        found = fetched(table)
        if found is None:
            return None
        cols, rows, col_types = found
        wanted = str(column or "").lower()
        col = next((c for c in cols if str(c).lower() == wanted), None)
        if col is None:
            return None
        distinct = {}
        for row in rows:
            value = _loaded_value(row.get(col), col_types.get(col))
            if value is not None:
                distinct.setdefault(str(value), None)
        return list(distinct)

    read.columns = columns
    return read


def _fetch_loaded_table(table: str, user_id: str, supabase_client):
    """`(columns, rows, column types)` of one of this user's tables, exactly as
    `execute_sql_query` reads them from `structured_data` - or None when the user
    has no such table, it has no rows (the loader skips an empty table too), or the
    fetch fails. The result is filtered by the table's own name as well as by the
    query, so a client that ignored the filter could still never hand back the
    wrong table."""
    try:
        result = (supabase_client.table("structured_data")
                  .select("table_name, columns, rows")
                  .eq("user_id", user_id)
                  .eq("table_name", table)
                  .execute())
    except Exception as e:
        logger.warning(f"column_value_reader: could not read {table!r} "
                       f"({type(e).__name__}: {e}); the re-query goes without its values")
        return None
    for t in (result.data or []):
        if str(t.get("table_name") or "").lower() != table:
            continue
        cols, rows = t.get("columns") or [], t.get("rows") or []
        if not cols or not rows:
            return None
        return cols, rows, _infer_column_types(cols, rows)
    return None


@traceable(name="query_structured_data", run_type="tool")
def execute_sql_query(question: str, user_id: str, supabase_client) -> str:
    """Generate SQL from a natural language question and execute it against user's tabular data."""

    # 1. Fetch user's structured_data
    result = supabase_client.table("structured_data") \
        .select("table_name, columns, rows, row_count") \
        .eq("user_id", user_id) \
        .order("table_name") \
        .execute()

    tables = result.data
    if not tables:
        return "No tabular data found. Upload a CSV or XLSX file first."

    # The full, unrouted set of tables that genuinely exist for this user —
    # kept around (independent of any routing below) so `_fix_table_names`
    # can tell "doesn't exist" apart from "exists but wasn't routed to".
    all_table_names = [t["table_name"] for t in tables]
    # ...and the tables themselves, rows and all: the place-code resolver reads the
    # places table from here when routing does not load it (spec-fix3 d, T11a-R3).
    all_tables = list(tables)

    # 1b. Router: narrow `tables` to the few this question needs, plus their
    # declared join neighbours, before building the schema block or loading
    # DuckDB. SHIPPED 2026-08-29/30 (see RETRIEVAL-VERDICTS.md #3) —
    # unconditional, no flag. If no cards are loaded (missing/corrupt file),
    # or the selection matches nothing live, this falls back to the full
    # unrouted `tables` list — that fallback is a real safety net, not
    # experiment scaffolding, and stays. The whole block is defensive: a
    # syntactically-valid-but-wrong-shaped cards file (e.g. a card missing
    # its "table" key) must degrade the same way a missing file does — a
    # KeyError/TypeError escaping here would otherwise turn a bad cards file
    # into a 500 for every question instead of the documented full-schema
    # fallback.
    cards = _load_table_cards(user_id, supabase_client)
    # Each routed card's `holds` sentence, printed under its table's heading in the schema
    # block (spec-fix5). Empty when nothing was routed: the full-schema fallback stays as it was.
    routed_holds = {}
    if cards:
        try:
            selected = set(_routed_table_names(question, cards, k=3))
            filtered = [t for t in tables if t["table_name"] in selected]
        except Exception as e:
            logger.warning(f"table_router: routing failed ({type(e).__name__}: {e}); "
                            f"falling back to the full schema for this question")
            filtered = None
        if filtered:
            logger.info(f"table_router: {len(tables)} tables -> "
                        f"{[t['table_name'] for t in filtered]}")
            tables = filtered
            routed_holds = _routed_holds(cards, selected)
        elif filtered is not None:
            logger.warning("table_router: selection matched no live tables "
                            "(cards out of sync with structured_data?); "
                            "falling back to the full schema for this question")

    # 2. Build schema description for LLM: every column read off every row, cut values
    # marked, a wide table's columns all named, each routed card's holds sentence (spec-fix5,
    # `_schema_block`). The column types are the majority-vote types the DuckDB load below
    # reuses, so the schema shown to the LLM matches the actual types.
    schema_desc, table_col_types = _schema_block(tables, routed_holds)
    # A printed date column's ISO companion: its rule rides only with a table that has one.
    iso_date_line = f"\n{ISO_DATE_RULE}" if _has_iso_companion(tables) else ""

    # 2b. Disambiguate a current/pre-takeover table pair when BOTH are in
    # play. Found as a real regression while building the table router
    # (doc-prep task 8): with all 28 tables always present, the model
    # rarely confused 'hwu_panels' (current) with 'hwu_existing_panels'
    # (pre-takeover, per the manifest's own 'status' column values —
    # current/existing/backfilled) for a plain present-tense question. With
    # the router narrowing the schema block to a handful of tables, the two
    # near-identical schemas sit right next to each other with nothing else
    # to anchor against, and 6 of 33 SQL-vs-truth eval cases flipped to
    # the wrong (historical) table in one run. This note is cheap (~40
    # tokens per pair) and unconditional — it fixes a latent ambiguity in
    # the corpus itself, not something specific to the router, so it runs
    # every time, whether or not the router actually narrowed `tables`.
    existing_pairs = []
    live_names = {t["table_name"] for t in tables}
    for t in tables:
        name = t["table_name"]
        current_name = _current_twin(name)
        if current_name is not None and current_name in live_names:
            existing_pairs.append((name, current_name))
    existing_note = ""
    if existing_pairs:
        pairs_str = "; ".join(f'"{old}" is the PRE-TAKEOVER/historical counterpart of "{new}"'
                               for old, new in existing_pairs)
        existing_note = (
            f"\n- {pairs_str}. For a plain present-tense question that does NOT say "
            f"'existing', 'before', 'pre-takeover', 'original' or 'historical', use ONLY "
            f"the current table — never the historical one, even if both are listed above."
        )

    # 2c. A place code the question prints, resolved to its location key in code (spec-fix3
    # part d): one line per code that names exactly one room, placed right before the
    # question. An error drops the lines and nothing else.
    place_note = _companion("place codes", _place_lines, question, cards, tables, all_tables)
    place_block = f"\n\n{place_note}" if place_note else ""

    # 3. Use Gemini to generate DuckDB SQL
    # 3-minute request timeout — a wedged HTTP connection otherwise hangs the
    # SQL generation forever (same fix as openai_client._get_client).
    client = genai.Client(api_key=get_llm_api_key(),
                          http_options=genai_types.HttpOptions(timeout=180_000))
    model = get_llm_model()

    # Build a list of exact table names for the prompt and post-processing
    real_table_names = [t["table_name"] for t in tables]

    # ------------------------------------------------------------------
    # PROVENANCE MAP for the domain rules in the prompt below.
    #
    # Every rule from "NEVER infer a panel's block or floor" onward is a fossil
    # of a specific eval failure. Without this map the rules can only ever be
    # added, never removed — nobody can tell which one is load-bearing.
    #
    # Case numbers are 1-based positions in the project's own SQL-vs-truth
    # eval suite, which is question-bank specific to one building and is NOT
    # shipped in this repository. The map is kept because it records WHY each
    # rule exists; a rule with no entry here has no known guard.
    #
    # Rule (first words)                     Guards                Symptom if deleted
    # ---------------------------------------------------------------------------
    # NEVER infer a panel's block or floor   cases 1, 2, 6, 7, 8   db ILIKE '%-4F-%' matches
    #                                                              nothing -> silent 0 rows
    # NEVER drop a constraint                cases 1, 7, 8         floor filter alone returns
    #                                                              every load type on the floor
    # SUM the quantity column not COUNT(*)   cases 1, 7, 16        one row can be many units
    #                                                              -> undercounts FCUs/points
    # breakdown: select meaningful columns   case 6, SQL-breakdown eval
    #                                                              -> bare db/cir_no table
    # ORDER BY natural reading order         SQL-breakdown eval    -> circuits out of
    #                                                              as-printed order
    # also SELECT notes/remarks on lookups   case 24               DEWA struck out MDL 1120.40
    #                                                              -> answer misses correction
    # parent-reference NOT EXISTS pattern    cases 2, 3            SMDB-B-4F feeds the 4F DBs
    #                                                              -> double-counts the area
    # named panel value from panel schedule  cases 20, 21, 24      the "broken telephone" bug:
    #                                                              SUM(mdb_calc) gave 2283.40
    #                                                              instead of 1156.36
    #   (deliberate exception: cases 12, 13 DO read mdb_calc — they ask for one
    #    load type's row and its diversity factor, not the panel's demand)
    # keyword filters: category vs remarks   case 7 (cleaner)      OR-ing remarks double-counts
    #                                                              incidental mentions
    # TWO panels -> feeder schedule          cases 9, 10, 15       child rows have BLANK totals
    #                                                              in panels -> wrong/empty
    # ONE panel -> read fed_from             cases 5, 18           no joins needed
    # superlatives from schedule totals      case 11               summing circuits gives a
    #                                                              different, wrong total
    # area totals from panel schedule        cases 2, 3            circuits cover only a subset
    # never approximate kWh from loads       doc-QA kWh cases      tables record ratings (kW),
    #                                                              not consumption (kWh)
    # what is in a place: the place-name    ex-036, 039, 040,     a room is filtered by an id
    #   column, never a built id             041, 042-045, 052     built from floor+block -> 0
    #                                                              rows, or by the level's row
    # two-part question -> SELECT * of       ex-046 … 051, 055     two columns of a wide row ->
    #   the thing's own row                                        "the records do not contain"
    #                                                              a value printed on that row
    #   (its "UNION ALL one labelled block per part is fine" clause WITHDRAWN
    #    2026-09-28 - it is what produced the runaway below; the JOIN it offered
    #    alongside is kept, and the what-is-in-a-place rule above lost the same
    #    clause on the same day)
    # the dependency graph (subject/          no eval case yet —   a change-impact question is
    #   predicate/object)                     the change-impact    answered from the asset's own
    #                                         path, 2026-09-16     row alone, so nothing it
    #                                                              affects is ever named
    # listing entities: identifier          no eval case yet —   a list of names/tags with no
    #   column first                        spec 2026-09-23      id column cannot be acted on
    #                                         item 1, proactive
    # spec table: every parameter row        no eval case yet —   a filtered spec query returns
    #   for the matching entities             spec 2026-09-23      one parameter instead of the
    #                                         item 1, proactive     full spec list
    # two-part question: ONE query,          the owner's question  UNION ALL across tables of
    #   JOIN, never UNION/INTERSECT/EXCEPT     of 2026-09-28,       different widths -> a Binder
    #                                          traced in LangSmith  Error, an arm padded with
    #                                                               8,188 tokens of NULLs, and
    #                                                               the answer taken from one
    #                                                               stray document chunk
    # ORDER BY an identifier: sort           ey-001, and the       ORDER BY <id> puts the blank
    #   blanks/NULLs last                     owner's own          cells first -> a list of 50
    #                                         question of          rows with no identifier at
    #                                         2026-09-23           all, which is the opposite
    #                                                              of what was asked for
    # printed total rows: never summed       goal-function run    a SUM over one controller's
    #   with the items they total            of 2026-09-30        points added the sheet's
    #   (spec-fix6)                                               own printed total row to the
    #                                                             items it totals
    # kind words: a class word covers        goal-function run    'distribution boards' became
    #   every kind code; two named kinds     of 2026-09-30        one kind code, dropping the
    #   get one count each (spec-fix8)                            boards of every other kind;
    #                                                             two named kinds came back as
    #                                                             one bare sum
    #   (wave 3, 2026-10-03: the per-kind SUM and the counting bullet above SUM only a column
    #    counting the asked items themselves - a count of something fitted to each item is not
    #    their quantity, and one row per item is counted with COUNT(*) or THEN 1; measured on
    #    the goal-function run of 2026-10-01, positions were stated as the readers fitted to them)
    # floors and levels: a level code,       goal-function run    a storey filtered on printed
    #   never printed floor text or a         of 2026-09-30        floor text the table does not
    #   place name; rankings GROUP BY the     (spec-fix3)          print, or a Block letter read
    #   key, every group, no LIMIT 1                               as a basement; a ranking by
    #                                                              the printed floor split one
    #                                                              level in two and LIMIT 1 hid
    #                                                              a tie
    #   (the same day the FORMAT bullet's printed-floor example and the never-infer bullet's
    #    printed-floor filter were rewritten: both offered the printed floor column as the
    #    way to filter a storey; their own principles are unchanged. Fix round 1: the
    #    equipment-type bullet's breakdown "no WHERE" became "no TYPE filter" - a storey or
    #    place filter the question names always stays)
    # dates with an ISO companion:           goal-function run    a text MAX/sort of a printed
    #   compare and sort on <x>_iso, never    of 2026-09-30        date column holding day-first
    #   the printed <x>; only when a table    (spec-fix5)          and ISO dates picked the wrong
    #   has the pair (ISO_DATE_RULE)                               date; ISO dates sorted last
    #   (the same day the value-list bullet among the generic rules was re-worded to the schema
    #    block's labels - 'possible values (complete)', 'N distinct values, examples' - which
    #    are now true, every row being read; and a bullet beside it says a value shown ending
    #    in '…' was cut for display and is matched with ILIKE on its start, never '='. Fix
    #    round 1: a cut form an ILIKE would match to several values says '(N values)', and
    #    that bullet says what N is)
    #
    # Rules above these (exact table names, quoting, DuckDB syntax, CAST) are
    # generic SQL correctness, not domain fossils — no provenance needed.
    #
    # IMPORTANT: keep tags HERE, not inline in the prompt string. Text inside
    # the f-string is sent to the model verbatim; editing it risks the evals.
    # ------------------------------------------------------------------

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
- Match the FORMAT of the column samples — filter with a value written exactly the way the column writes it, never a paraphrase of it (e.g. if a column's values look like 'Y' and 'N', filter = 'Y' for a yes, never 'yes' or 'true'); a floor, level or storey is filtered on its level code instead (FLOORS AND LEVELS below)
- Columns marked 'possible values (complete)' list EVERY value the column holds — filter with those exact values. Columns marked 'N distinct values, examples' hold N different values and show only the first few — if the user's term (e.g. 'FCU') is not among the examples, still filter for it directly with ILIKE '%term%'; NEVER substitute a different example value for the user's term
- A value shown ending in '…' was cut for display — match it with ILIKE 'start%' (its shown start, without the '…'), never with =; a '(N values)' after it says that ILIKE matches N different values{iso_date_line}
- Tables often reference each other by shared identifier values (e.g. a board/panel name column in one table matching a name column in another) — use JOINs across tables when a question spans them{existing_note}
- NEVER infer a panel's block or floor from its NAME pattern (db ILIKE '%-4F-%' silently returns 0 rows — naming schemes vary, e.g. 'DB-04(B)-SP-01' is a 4th-floor Block B board). Resolve block/floor filters through the panel-schedule table's own block column and its level-code column: WHERE db IN (SELECT panel FROM "panels" WHERE block = 'B' AND <its level-code column> = '<the level code of that floor, as the column holds it>')
- NEVER drop a constraint from the question. If the user names an equipment/load type (e.g. 'FCU'), the WHERE clause MUST filter on it (e.g. load_type ILIKE '%FCU%') IN ADDITION TO any floor/block/area filters — a floor filter alone returns every load type on that floor, which is wrong
- When counting equipment/units and the table has a column that counts the asked items themselves (a quantity column such as 'points', 'qty' or 'count', or a column named for the asked item), SUM that column instead of COUNT(*) — one row can represent multiple units. A column counting something fitted to each item (the readers on a door) is not their quantity: when each row is one item, COUNT(*) the rows
- Counting rows by a category column that exists in the table, for a code the question PRINTS ('how many DB boards', kind = 'DB'), is a plain SELECT COUNT(*) with that WHERE - nothing below changes that
- NEVER trust a table's NAME for type membership: a table named like 'camera_counts' may still list monitors, servers and workstations alongside cameras - SUM(quantity) over it counts them all. Type membership comes only from each row's description/model values and notes
- For 'how many <equipment type>' questions over equipment tables: filter a text column with the user's type word ONLY if that word appears in that column's sample values above. Equipment tables name products ('PRO 3 IR IND DOME 2MP'), not categories, so description ILIKE '%camera%' silently matches nothing. When the type word is not in the samples, do NOT emit a filtered or whole-table SUM - instead return the per-row breakdown of the most complete quantity table (its model/description columns, its numeric quantity column, and its notes column, with no TYPE filter - a storey or place filter the question names always stays in the WHERE): the rows and their notes identify which lines are the asked type. Prefer the table whose quantity column is numeric and itemises every unit (an equipment-counts table) over a register whose qty is text like '1 Lot'
- When the same equipment appears in BOTH a models/summary table (with a qty_installed-style column) and a per-unit asset register, they describe the SAME physical units twice. Count from EXACTLY ONE table - the per-unit asset register (SUM its qty column, or COUNT its rows when qty is text like '1 Lot'). NEVER UNION, JOIN or add the two tables' counts: 5 units in the models table plus the same 5 units' register rows = 10 is wrong, the answer is 5
- When the user asks for a breakdown, list, itemization, or table of items, do NOT select only identifier columns — also SELECT every column that makes a row meaningful on its own: any room/area/location/description column, the quantity column (e.g. 'points', 'qty'), and the load/rating column if relevant. Example: for "breakdown of FCUs" select db, cir_no, room_area, points — not just db and cir_no
- For breakdowns/lists, ORDER BY the natural reading order: the board/panel column first, then the serial/row number cast numerically (e.g. ORDER BY db, TRY_CAST(sl_no AS INTEGER)) so circuits appear in as-printed order
- For single-row value lookups (not aggregates), also SELECT the notes/remarks column when the table has one — source documents sometimes contain corrections (a printed value struck out and replaced by hand). The notes record this, and the answer must be able to report the current value versus the original
- When the panel-schedule table has a 'rolls_up_to' column, it holds the RESOLVED parent for the rows that needed resolving and is EMPTY elsewhere; 'fed_from' is the value as printed (it may name non-panels like 'DEWA' or 'DEWA LV DB/TRANSFORMER'). The effective parent of a row is therefore COALESCE(NULLIF(rolls_up_to, ''), fed_from) - always use this expression, never fed_from alone, for hierarchy decisions. The whole building/campus is the single topmost row WHERE rolls_up_to = 'ROOT' - SELECT that row's tcl_kw for total connected load or mdl_kw for maximum demand (with notes); NEVER answer a whole-building total by SUMming rows. Area totals (a block, a floor) sum only rows whose effective parent is OUTSIDE the filtered set: SELECT SUM(x.tcl_kw) FROM "panels" x WHERE <area filter> AND NOT EXISTS (SELECT 1 FROM "panels" p WHERE p.panel = COALESCE(NULLIF(x.rolls_up_to, ''), x.fed_from) AND <same area filter on p>)
- If a table has a parent-reference column (e.g. 'fed_from') and NO rolls_up_to column, a parent row's totals already INCLUDE its children — summing all rows in an area double-counts. Sum ONLY rows whose parent is OUTSIDE the filtered set, using this exact pattern: SELECT SUM(x.tcl_kw) FROM "panels" x WHERE <area filter on x> AND NOT EXISTS (SELECT 1 FROM "panels" p WHERE p.panel = x.fed_from AND <same area filter on p>)
- When the user asks for a single NAMED panel/board's value (its total connected load, maximum demand / MDL / diversified load, rating), read that panel's own row from the panel-schedule table, ALWAYS written with the feeder-schedule fallback join because child boards often have BLANK totals in the panel schedule: SELECT COALESCE(x.tcl_kw, f.tcl_kw) AS tcl_kw, COALESCE(x.mdl_kw, f.mdl_kw) AS mdl_kw, x.demand_factor, x.notes FROM "panels" x LEFT JOIN "smdb_feeders" f ON f.feeder = x.panel WHERE x.panel = 'MDB-C-G2' (use the real table/column names). A named panel's DEMAND FACTOR is its own demand_factor column in the panel schedule - never the diversity column of the per-load-type calc table — NEVER answer it by SUMming a per-load-type calculation table (e.g. "mdb_calc"): those rows itemize the design calculation and their sum ignores diversity, giving an inflated wrong number. This holds regardless of how loosely the demand is phrased ('max demand', 'maximum demand load', 'demand') and even if the question mentions the calculation table: the calc table's per-load rows AND its printed TOTAL row are design-stage numbers that were superseded — for a named panel ALWAYS SELECT mdl_kw (and notes) FROM the panel-schedule table. Use the calculation table ONLY when the question is about a specific load type's row, diversity factors, or the design-calc breakdown itself. If the named panel's own row has a NULL/blank value for what was asked (child distribution boards often print no totals in the panel schedule), read it from the feeder-schedule row in the same query: SELECT COALESCE(x.tcl_kw, f.tcl_kw) AS tcl_kw, COALESCE(x.mdl_kw, f.mdl_kw) AS mdl_kw, x.notes FROM "panels" x LEFT JOIN "smdb_feeders" f ON f.feeder = x.panel WHERE x.panel = '<name>' (use the real table names)
- Keyword filters: if the term IS an equipment/load TYPE (FCU, LTG, socket, cooker, water heater), filter ONLY the category column (e.g. load_type ILIKE '%FCU%') — adding OR remarks double-counts rows whose remarks mention the type incidentally. Use the remarks/notes column (OR-ed with the category column) only for qualifier words that are not load types themselves (e.g. 'cleaner', a room or usage description)
- When the question names TWO panels in a feeder relationship ('the feeder X on/from board Y', 'the breaker rating of the feeder from Y to X'), read the FEEDER-SCHEDULE table whose rows pair a parent board column with a feeder column (e.g. SELECT tcl_kw FROM "smdb_feeders" WHERE smdb='Y' AND feeder='X'). Do NOT answer these from the panel-schedule table (SELECT tcl_kw FROM "panels" WHERE panel='X' AND fed_from='Y' is WRONG) — child-panel rows often have BLANK totals there; the value lives only in the feeder schedule
- But when only ONE panel is named and the question asks what feeds it ('which panel feeds X?'), simply read X's own parent-reference column: SELECT fed_from FROM "panels" WHERE panel = 'X' — no joins, no OR chains
- For superlative/comparison questions about panels' or boards' totals ('which board has the highest connected load'), SELECT panel, tcl_kw FROM the panel-schedule table itself with ORDER BY tcl_kw DESC NULLS LAST — NEVER compute a substitute total by summing the circuits table (not even aliased as tcl_kw); the printed schedule totals are authoritative
- The same applies to AREA totals ('total load of Block B', 'total load of the 4th floor'): they come from the panel-schedule table using the topmost-rows NOT EXISTS pattern above — never from SUM(load_w) over the circuits table, which covers only the circuit-level subset and gives a different, wrong number
- The tables record CONNECTED LOADS and ratings (W, kW, A) — NOT energy consumption, runtime, or cost. If the question asks for something the tables do not record (kWh consumed, annual energy usage, operating hours, bills), NEVER approximate it from load columns (e.g. multiplying by hours) — return a query with no rows instead (SELECT NULL WHERE FALSE) so the system can look elsewhere
{DEPENDENCY_GRAPH_RULE}
{EQUIPMENT_COUNT_RULE}
{ROOM_CONTENTS_RULE}
{TWO_HOP_RULE}
{LIST_IDENTIFIER_RULE}
{ORDER_BY_BLANKS_RULE}
{TWO_PART_RULE}
{ALL_PARAMETERS_RULE}
{PRINTED_TOTAL_ROWS_RULE}
{KIND_WORDS_RULE}
{PLACE_KEYS_RULE}{place_block}

User question: {question}"""

    from app.services.llm_usage import generate_with_usage

    response = generate_with_usage(
        client,
        model=model,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            temperature=0,
            # Thinking models (gemini-2.5+/3) spend "thought" tokens from this same
            # budget — 2048 sometimes truncated the SQL mid-string. Keep it high.
            # MEASURED 2026-09-28, and the reason it came back down: a two-part
            # question made the writer pad a UNION arm with NULLs and it spent
            # 8,188 output tokens doing it. A one-line SELECT never needs 8,192;
            # a truncation is now caught by shape (_sql_looks_runaway) instead of
            # being outrun by a bigger budget.
            max_output_tokens=2048,
        ),
        name="sql_generate",
    )

    sql = response.text.strip()
    # Strip markdown code fences if present
    if sql.startswith("```"):
        lines = sql.split("\n")
        sql = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    sql = sql.strip().rstrip(";")

    # Fix truncated/incorrect table names by fuzzy matching
    sql = _fix_table_names(sql, real_table_names, all_table_names)

    logger.info(f"Generated SQL: {sql}")

    # 3a. Refuse a runaway generation BEFORE anything is executed - and before
    # the repair call below, which would only be handed the same runaway back.
    runaway = _sql_looks_runaway(sql)
    if runaway:
        logger.error(f"Refusing runaway generated SQL ({runaway}): {sql[:300]}")
        return _runaway_failure_text(sql, runaway)

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
                con.execute(insert_sql, [_loaded_value(row.get(c), col_types[c]) for c in cols])

        # 5. Execute SQL — on failure, give the LLM one shot at repairing the
        # query with the actual error message before falling back
        try:
            result = _execute_with_timeout(con, sql)
        except _QueryTimeoutError:
            # A timeout is a resource problem, not something an LLM repair
            # prompt can fix — skip the repair round-trip and fail straight
            # to the outer handler so the request is bounded by
            # SQL_QUERY_TIMEOUT, not SQL_QUERY_TIMEOUT plus a repair attempt.
            raise
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
                config=genai_types.GenerateContentConfig(temperature=0, max_output_tokens=2048),
            )
            sql = (repair_resp.text or "").strip().rstrip(";")
            if sql.startswith("```"):
                lines = sql.split("\n")
                sql = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
            sql = _fix_table_names(sql, real_table_names, all_table_names)
            logger.info(f"Repaired SQL: {sql}")
            result = _execute_with_timeout(con, sql)
        col_names = [desc[0] for desc in con.description]

        # 5b. A filter on one spelling of a value its column also prints in another letter
        # case or spacing is re-run widened to every spelling, and that result replaces this
        # one (wave 3, F1) - before an empty result is reported, since the spelling filtered
        # on can be one the column never prints. `written_sql` is the query as the writer
        # wrote it, which the MATCHED line quotes. An error keeps the result as it is.
        written_sql, spellings = sql, {}
        widened = _companion("case variants", _case_variant_rerun, con, sql, tables,
                             table_col_types)
        if widened:
            sql, result, col_names, spellings = widened

        # 5c. A result that holds nothing, from a phrase matched with ILIKE '%...%' that the table
        # prints in another word order, is re-run once with each word of the phrase matched on its
        # own (wave 3, G3) - before an empty result is reported, so before any model re-query. When
        # that finds rows they are the result, and every line below reads the query that found
        # them. An error, or a re-run that finds nothing too, keeps the result as it is.
        wordwise = _companion("word by word", _word_by_word_rerun, con, sql, result,
                              tables)
        wordwise_lines = ""
        if wordwise:
            sql, result, col_names, wordwise_lines = wordwise
            written_sql, spellings = sql, {}

        # 5d. A row list whose top-level WHERE applies a pattern filter to a column it does not
        # show is re-run with that column appended, so each row prints what it matched (wave 3,
        # G5). The MATCHED line still reads the columns the query had before (`matched_cols`).
        # An error, or a re-run with another number of rows, keeps the result as it is; a column
        # whose every value only repeats its pattern's literal is never added (wave 4, A1).
        matched_cols = col_names
        with_matched = _companion("matched column", _matched_column_rerun, con, sql, col_names,
                                  result)
        if with_matched:
            sql, result, col_names = with_matched

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

        shape = _result_shape(col_names, result, max_rows)
        if shape:
            md += "\n" + shape + "\n"

        # Deterministic totals for quantity-like columns on multi-row results:
        # breakdown answers must end with a Total row, and the answer model
        # reliably copies a stated total but unreliably computes one itself.
        if len(result) >= 4:
            qty_like = ("points", "qty", "quantity", "nos", "load_w")
            for ci, cn in enumerate(col_names):
                if any(k in str(cn).lower() for k in qty_like):
                    vals = []
                    row_vals = []
                    for row in result:
                        try:
                            v = float(str(row[ci]).replace(",", ""))
                        except (TypeError, ValueError):
                            v = None
                        row_vals.append(v)
                        if v is not None:
                            vals.append(v)
                    if vals:
                        total = sum(vals)
                        total_str = str(int(total)) if total == int(total) else f"{total:.2f}"
                        breakdown = _totals_breakdown(col_names, result, ci, row_vals)
                        md += f"\nTOTAL {cn} (all {len(vals)} rows): {total_str}{breakdown}\n"

        md += f"\nSQL: `{sql}`"

        # What the result needs said beside it, read in code after the query ran. Each is
        # at most a couple of extra reads of `con`, and an error loses that line and never
        # the answer (`_companion`). The row notes are read FIRST, while every table is
        # still exactly the table as loaded; they are placed last, so the result ends with
        # them (spec-fix1 part a) - with the notes of a row the question names whole, and
        # each note keyed to its row (wave 3, G4 + G5).
        row_notes = _companion("notes block", _row_notes_block, con, question, sql, tables,
                               cards, result)
        # What every row above matched, when no column of theirs prints it (spec-fix10), and
        # every spelling a widened filter matched (wave 3, F1). Pure - no query.
        matched = _companion("matched", _matched_line, written_sql, matched_cols, md, spellings)
        if matched:
            md += "\n\n" + matched
        # How the rows were found when a phrase was matched word by word (wave 3, G3).
        if wordwise_lines:
            md += "\n\n" + wordwise_lines
        # A class word written as one kind code: what the same query returns for each longer code
        # of that kind column ending in it, which the filter left out (wave 3, G1).
        other_kinds = _companion("other kinds", _other_kinds_lines, con, question, sql, tables,
                                 table_col_types, col_names)
        if other_kinds:
            md += "\n\n" + other_kinds
        # A word pattern on a kind column, whose words the question prints, that left out kinds the
        # same scope holds: every kind of the scope with its rows (wave 4, A3).
        kinds_in_scope = _companion("kinds in scope", _kinds_in_scope_line, con, question, sql,
                                    tables, table_col_types)
        if kinds_in_scope:
            md += "\n\n" + kinds_in_scope
        # A value the WHERE looked for in a column of a table that never holds it, though
        # another column of that table does - which a result with rows hides; sql_loop
        # re-queries on the line (wave 3, F2).
        elsewhere = _companion("literal elsewhere", _literal_elsewhere_lines, con, sql, tables,
                               table_col_types)
        if elsewhere:
            md += "\n\n" + elsewhere
        # The sheet's own printed total rows, counted as items (spec-fix6).
        printed_totals = _companion("printed total rows", _printed_total_rows_line,
                                    con, sql, tables, cards, col_names, result)
        if printed_totals:
            md += "\n\n" + printed_totals
        # 1-3 (not all) of the rows behind a JOINED aggregate carry a note that may change
        # what they are: both figures, with and without them, picking neither (wave 5, G12).
        noted_figure = _companion("notes inside aggregate", _notes_inside_aggregate_line,
                                  con, sql, tables, col_names, result)
        if noted_figure:
            md += "\n\n" + noted_figure
        # What a one-figure aggregate counted, when its filter value is a word only some of
        # the item names it read print (wave 3, F4).
        figure = _companion("figure counts", _figure_counts_line, con, question, sql, tables,
                            table_col_types, col_names, result)
        if figure:
            md += "\n\n" + figure
        # Everything below or above a node the question names, walked in code when it asks
        # for a whole chain of a parent-reference tree (spec-fix2).
        hierarchy = _companion("hierarchy", _hierarchy_line, con, question, sql, tables, cards)
        if hierarchy:
            md += "\n\n" + hierarchy
        # A parent-reference tree's own nodes, counted by their direct children's place or
        # category, when the question names 2+ values of one (wave 5, G13 b).
        children_by_place = _companion("children by place", _children_by_place_line, con,
                                       question, sql, tables, cards, result)
        if children_by_place:
            md += "\n\n" + children_by_place
        # One parent's children that two records list under different names (wave 4, G14 a).
        feed_names = _companion("feed names", _feed_names_line, con, sql, tables, cards, result)
        if feed_names:
            md += "\n\n" + feed_names
        # Every link the dependency graph records for the ids the result prints, grouped by link
        # and target, when the SQL read the graph (wave 3, G7).
        graph_links = _companion("graph links", _graph_links_line, con, sql, tables, result)
        if graph_links:
            md += "\n\n" + graph_links
        # A self-join that found no other row sharing a value: the rows of the other loaded tables
        # that hold it, with their notes (wave 3, G8).
        shared = _companion("shared value", _shared_value_line, con, sql, tables)
        if shared:
            md += "\n\n" + shared

        # Hierarchy warning must travel WITH the result — the answer-writing
        # model never sees the SQL-generation prompt, and without this it adds
        # parent totals to child totals (double-counting). Built from the tables
        # the SQL READ, as the SOURCE lines are (spec-fix10): built from every
        # routed table, a count over a table with no parent column carried the
        # parent columns of tables merely routed beside it, and "use only the
        # topmost row(s)" rode on answers it has nothing to do with.
        parent_cols = sorted({
            c for t in tables if _sql_reads_table(sql, t["table_name"])
            for c in t["columns"] if _is_parent_column(c)
        })
        if parent_cols:
            md += (
                f"\n\nIMPORTANT (for interpreting these results): this data is hierarchical "
                f"(parent-reference column: {', '.join(parent_cols)}). A parent row's totals "
                f"already INCLUDE everything fed from it — when reporting a total for an area, "
                f"use only the topmost row(s); never add a parent's total to its children's totals."
            )
        # Which record each number came from - travels WITH the result, like the
        # hierarchy note above, because the answer-writing call never sees the cards.
        sources = _source_lines(sql, tables, cards, len(result))
        if sources:
            md += "\n\n" + sources
        if row_notes:
            md += "\n\n" + row_notes
        return md

    except Exception as e:
        logger.error(f"SQL execution error: {e}")
        return f"SQL query failed: {e}\n\nGenerated SQL: `{sql}`"
    finally:
        con.close()
