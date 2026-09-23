"""
Text-to-SQL tool: generates and executes SQL against user's structured data using DuckDB.
"""
import json
import logging
import os
import time
import re
import threading
from pathlib import Path

import duckdb
from google import genai
from google.genai import types as genai_types
from langsmith import traceable

from app.services.settings import get_llm_api_key, get_llm_model
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
ROOM_CONTENTS_RULE = (
    "- WHAT IS IN A PLACE ('what assets are in X', 'what is installed in <room>', 'what else is "
    "in that room'): answer from the table that already carries ONE ROW PER PLACE AND ITEM — it "
    "has BOTH a location-id column and a human-readable place-name column (a display/room-name "
    "column printing things like '<number> <Room Name> <Block>') — in preference to any "
    "per-system asset register, which carries no place column at all and can only be searched "
    "in its free text. FILTER ON THE PLACE-NAME COLUMN, with ILIKE on the place exactly as the "
    "question prints it (e.g. <place-name column> ILIKE '%<the name in the question>%'), and "
    "NEVER also equate the location-id column in the same WHERE: a level-and-block id is a "
    "DIFFERENT row from that room's own id, so the two together return nothing, and the "
    "level-and-block id on its own answers about the whole level instead of the room. Do not "
    "build a location id out of a floor and a block — you cannot know a room's id, and the "
    "printed name is what the question gave you. SELECT the place-name column, the "
    "system/category column, the item/description column and the quantity column, and ORDER BY "
    "the place-name column then the system so each place's items stay together. ALSO RETURN THE "
    "TOTALS in the same query — the total quantity for the place and the quantity per system, "
    "e.g. UNION ALL a labelled total row (SELECT '<place>', 'TOTAL', NULL, SUM(<quantity>) …) — "
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
    "prints, in the SAME query (UNION ALL one labelled block per part is fine, with a constant "
    "column naming which part each block answers). A COUNT of what something HAS comes from the "
    "table whose rows are those things (count the circuit rows in the circuits table), never "
    "from the thing's own row and never from the parent's feeder row, which holds one row per "
    "child board and not one per circuit. And when the question asks about a thing AND its "
    "neighbours, include THE THING'S OWN totals, ratings and notes as well as the neighbour "
    "list: a list of what X feeds does not contain X's own rating, and the answer will say it "
    "is not recorded."
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
        if not (2 <= k <= 8) or k == n:
            continue
        if vals and all(_is_numeric_value(v) for v in vals):
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


# Bounds on the caveats that ride with a SOURCE line (see `_source_lines`). Three
# is what the widest card in the corpus carries; 300 characters is the same cut
# `_result_shape` uses on a breakdown line.
CAVEATS_PER_TABLE = 3
CAVEAT_MAX_CHARS = 300


def _source_lines(sql: str, tables: list, cards: list) -> str:
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
    with a long caveat list cannot flood the prompt it rides in."""
    if not cards or not sql:
        return ""
    holds_by_table = {c.get("table"): (c.get("holds") or "").strip() for c in cards if c.get("table")}
    caveats_by_table = {c.get("table"): (c.get("caveats") or []) for c in cards if c.get("table")}
    lines = []
    for tbl in tables:
        name = tbl["table_name"]
        if not holds_by_table.get(name):
            continue
        if not re.search(r'(?<![A-Za-z0-9_])"?' + re.escape(name) + r'"?(?![A-Za-z0-9_])', sql):
            continue
        holds = re.sub(r"\s+", " ", holds_by_table[name])
        if len(holds) > 400:
            holds = holds[:397].rstrip() + "..."
        lines.append(f"SOURCE - {name}: {holds}")
        cavs = [re.sub(r"\s+", " ", str(c)).strip() for c in caveats_by_table.get(name) or []]
        for cav in [c for c in cavs if c][:CAVEATS_PER_TABLE]:
            if len(cav) > CAVEAT_MAX_CHARS:
                cav = cav[:CAVEAT_MAX_CHARS - 3].rstrip() + "..."
            lines.append(f"NOTE - {name}: {cav}")
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
        selected = set(select_tables(question, cards, k=3))
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
    if cards:
        try:
            selected = set(select_tables(question, cards, k=3))
            filtered = [t for t in tables if t["table_name"] in selected]
        except Exception as e:
            logger.warning(f"table_router: routing failed ({type(e).__name__}: {e}); "
                            f"falling back to the full schema for this question")
            filtered = None
        if filtered:
            logger.info(f"table_router: {len(tables)} tables -> "
                        f"{[t['table_name'] for t in filtered]}")
            tables = filtered
        elif filtered is not None:
            logger.warning("table_router: selection matched no live tables "
                            "(cards out of sync with structured_data?); "
                            "falling back to the full schema for this question")

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
        if "_existing_" in name:
            current_name = name.replace("existing_", "", 1)
            if current_name in live_names:
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
- Match the FORMAT of the column samples — e.g. if floor values look like 'GF', '4F', '6F' then the 4th floor is floor = '4F' (never '%4th%' or 'fourth')
- Columns marked 'possible values' list the complete set — filter with those exact values. Columns marked 'examples' have MANY OTHER values — if the user's term (e.g. 'FCU') is not among the examples, still filter for it directly with ILIKE '%term%'; NEVER substitute a different example value for the user's term
- Tables often reference each other by shared identifier values (e.g. a board/panel name column in one table matching a name column in another) — use JOINs across tables when a question spans them{existing_note}
- NEVER infer a panel's block or floor from its NAME pattern (db ILIKE '%-4F-%' silently returns 0 rows — naming schemes vary, e.g. 'DB-04(B)-SP-01' is a 4th-floor Block B board). Resolve block/floor filters through the panel-schedule table's own block and floor columns: WHERE db IN (SELECT panel FROM "panels" WHERE block = 'B' AND floor = '4F')
- NEVER drop a constraint from the question. If the user names an equipment/load type (e.g. 'FCU'), the WHERE clause MUST filter on it (e.g. load_type ILIKE '%FCU%') IN ADDITION TO any floor/block/area filters — a floor filter alone returns every load type on that floor, which is wrong
- When counting equipment/units and the table has a quantity column (e.g. 'points', 'qty', 'count'), SUM that column instead of COUNT(*) — one row can represent multiple units
- Counting rows by a category column that exists in the table ('how many boards', kind = 'DB') is a plain SELECT COUNT(*) with that WHERE - nothing below changes that
- NEVER trust a table's NAME for type membership: a table named like 'camera_counts' may still list monitors, servers and workstations alongside cameras - SUM(quantity) over it counts them all. Type membership comes only from each row's description/model values and notes
- For 'how many <equipment type>' questions over equipment tables: filter a text column with the user's type word ONLY if that word appears in that column's sample values above. Equipment tables name products ('PRO 3 IR IND DOME 2MP'), not categories, so description ILIKE '%camera%' silently matches nothing. When the type word is not in the samples, do NOT emit a filtered or whole-table SUM - instead return the per-row breakdown of the most complete quantity table (its model/description columns, its numeric quantity column, and its notes column, no WHERE): the rows and their notes identify which lines are the asked type. Prefer the table whose quantity column is numeric and itemises every unit (an equipment-counts table) over a register whose qty is text like '1 Lot'
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
{ALL_PARAMETERS_RULE}

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
            max_output_tokens=8192,
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
                config=genai_types.GenerateContentConfig(temperature=0, max_output_tokens=8192),
            )
            sql = (repair_resp.text or "").strip().rstrip(";")
            if sql.startswith("```"):
                lines = sql.split("\n")
                sql = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
            sql = _fix_table_names(sql, real_table_names, all_table_names)
            logger.info(f"Repaired SQL: {sql}")
            result = _execute_with_timeout(con, sql)
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
                    for row in result:
                        try:
                            vals.append(float(str(row[ci]).replace(",", "")))
                        except (TypeError, ValueError):
                            pass
                    if vals:
                        total = sum(vals)
                        total_str = str(int(total)) if total == int(total) else f"{total:.2f}"
                        md += f"\nTOTAL {cn} (all {len(vals)} rows): {total_str}\n"

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
        # Which record each number came from - travels WITH the result, like the
        # hierarchy note above, because the answer-writing call never sees the cards.
        sources = _source_lines(sql, tables, cards)
        if sources:
            md += "\n\n" + sources
        return md

    except Exception as e:
        logger.error(f"SQL execution error: {e}")
        return f"SQL query failed: {e}\n\nGenerated SQL: `{sql}`"
    finally:
        con.close()
