"""sql_loop.py — the bounded SQL verifying loop (spec 2026-09-23 §3).

One SQL call, written once, never looked at again: that is what this module ends. The
residual eval failures it exists for are all the same shape — the writer selects too few
columns, filters on a value it built instead of one the source prints, or asks for a list
and gets back everything except the column that names each thing. None of those are reading
failures the answer model can fix; they are visible in the RESULT TEXT, by code.

So: run the query, INSPECT what came back, take the first issue that has a deterministic
re-query instruction, append it to the question, run again — at most `max_steps` times, with
`for step in range(max_steps)` and never an unbounded loop. A quantity question additionally
gets ONE retrieval cross-check so the answer can name the other records that state the same
number, which this corpus needs because several of its quantities are printed differently in
different documents.

Three binding properties, each pinned by `tests/test_sql_loop.py`:

* GENERIC — no table, column value, system or building is named anywhere below. The
  identifier a list must carry comes from the routed card's `identifier_column`, or failing
  that from column-name shape (`*_number`, `*_id`, `*_tag`, `*_name`). The question-shape
  regexes are English shapes only; the aggregate test is SQL keywords only. Section 18 of
  the test scans this file for the names it must not contain.
* DETERMINISTIC — every issue is detected by code. The model is never asked "is this
  right?", because a model that wrote a bad query is not the thing to ask whether the query
  was bad.
* DEGRADES CLEANLY — `max_steps <= 1` reproduces the pre-loop behaviour exactly: one query,
  no re-query, no cross-check, no trailer line, and the same empty-result and failed-result
  fallbacks to the document index that the caller used to perform inline.

`result_is_empty` below is a deliberate DUPLICATE of `openai_client._sql_result_is_empty`.
Importing it would be circular the moment `openai_client` imports this module; the test pins
the two equal on five inputs so the copy cannot drift silently.

FOR THE CALLER — what the old inline code did and this module now owns: when a terminal
fallback replaces the result text with document excerpts, today's branch also reassigns
`tool_name = "search_documents"`, and two later blocks read that name when deciding whether
to quote the SQL result back to the model. That reassignment is reported here as
`Investigation.source_tool` — `"query_structured_data"` normally, `"search_documents"` once
a fallback has supplied the text — so the wiring does not have to rediscover it.

Fix round 1 (review of commit `3e5f71e`) changed five things about detection, all of them
because the module had only ever been exercised against synthetic cards: an AGGREGATE result
is exempt from the column-shaped issues (a grouped breakdown has no per-entity identifier by
construction, and re-querying one destroys a correct answer); a list counts as nameable when
ANY consulted card's identifier or ANY identifying-shaped column is present, so the loop
never punishes a writer that obeyed the generation-time rule; the column it asks for is the
best PRINTED candidate rather than whatever key a card happens to declare; an unparseable
SQL yields NO cards rather than all of them; and a wall-clock budget, a repeat-issue guard
and per-step outcome events were added.
"""
from __future__ import annotations

import re
import time
from collections import namedtuple

# --------------------------------------------------------------------------- types

#: `kind` is one of the constants below; `detail` is the one-line human wording the caller
#: puts on its `tool_start`/`tool_done` events; `instruction` is the deterministic sentence
#: appended to the question for the next query, or "" for an issue that is not a re-query.
Issue = namedtuple("Issue", "kind detail instruction")

#: `result_text` is what the answer writer is handed; `steps` are the per-query `("step", …)`
#: event payloads in order (their outcomes arrive as `("step_done", …)` events);
#: `crosscheck` is the retrieved excerpt text of the quantity cross-check, or None (it is
#: None for the terminal document fallbacks, which replace `result_text` instead of riding
#: alongside it); `source_tool` is the tool the final text came from; `issues` is every
#: issue kind the investigation saw, in first-occurrence order, including the ones that are
#: findings rather than re-queries.
Investigation = namedtuple("Investigation", "result_text steps crosscheck source_tool issues")

EMPTY = "EMPTY"
IDENTIFIER_MISSING = "IDENTIFIER_MISSING"
NARROW_SELECT = "NARROW_SELECT"
TRUNCATED_NO_SHAPE = "TRUNCATED_NO_SHAPE"
COUNT_CROSSCHECK = "COUNT_CROSSCHECK"

#: Priority order, spec §3. The loop acts on the first of these that carries an
#: instruction; the last two never carry one. Pinned by test §21 — a fixture that raises
#: two issues at once, so inverting this tuple turns a check red.
ISSUE_ORDER = (EMPTY, IDENTIFIER_MISSING, NARROW_SELECT, TRUNCATED_NO_SHAPE, COUNT_CROSSCHECK)

TOOL_SQL = "query_structured_data"
TOOL_SEARCH = "search_documents"
FAILED = "FAILED"

# --------------------------------------------------------------------------- constants

#: A result this narrow is a candidate for NARROW_SELECT — and a table with more
#: non-citation columns than this has something to widen to.
MAX_NARROW_COLUMNS = 3

#: What makes a list nameable. The generation-time rule tells the writer to select "the
#: column the schema marks as the identifier or whose name ends in _number/_id/_tag/_name",
#: so a result carrying any of those has complied and must not be re-queried for it. Shape,
#: not vocabulary: no actual column is named here.
NAMEABLE_SUFFIXES = ("_number", "_id", "_tag", "_name")

#: When the issue DOES fire, which column to ask for, best first: a printed number, then a
#: printed tag, then a printed name — and only then the key the card declares, which in this
#: corpus is often an internal id printed on nothing.
IDENTIFIER_RANK = ("_number", "_tag", "_name")

#: Columns that cite a value rather than being one. A card's "non-citation columns" are
#: what a details question should get back. The two exact names are the free-text ones every
#: table in this shape of corpus carries; the rest are recognised by prefix/suffix, because
#: provenance columns are written both ways (`source_x` and `x_source`).
CITATION_EXACT = ("notes", "remarks")
CITATION_PREFIXES = ("source_",)
CITATION_SUFFIXES = ("_source", "_resolution")

#: How many retrieved excerpts the quantity cross-check keeps (spec §3: top 3), and the
#: separator the caller's search wrapper puts between them.
CROSSCHECK_EXCERPTS = 3
EXCERPT_SEPARATOR = "\n\n---\n\n"
CROSSCHECK_HEADING = "Other records that state this quantity:"

#: The terminal fallbacks, worded exactly as the caller worded them before this module
#: existed, so `max_steps=1` is byte-identical to that behaviour.
EMPTY_FALLBACK_PREFIX = ("The structured tables returned no rows for this question. "
                         "Document excerpts that may answer it instead:\n\n")
EMPTY_FALLBACK_SUFFIX = ("\n\n(If the excerpts do not contain the answer either, say the "
                         "information was not found — do not guess.)")

FIRST_STEP_DETAIL = "Querying the structured tables"

#: Used only if `sql_tool` cannot be imported (it pulls in the model SDK); the real budget
#: is `SQL_QUERY_TIMEOUT`, read below.
DEFAULT_BUDGET_SECONDS = 5.0

# --------------------------------------------------------------------------- question shapes

_LIST_RE = re.compile(r"\b(list|lists|listing)\b|\bwhat are\b|\bwhich\b|\ball the\b"
                      r"|\bshow me\b|\bgive me the\b", re.IGNORECASE)
_DETAILS_RE = re.compile(r"\bspecs?\b|\bspecification|\bdetails?\b|\battributes?\b"
                         r"|\bparameters?\b|\bbreakdown\b|\beverything about\b", re.IGNORECASE)
_COUNT_RE = re.compile(r"\bhow many\b|\btotal\b|\bnumber of\b|\bcount\b", re.IGNORECASE)


def asks_to_list(question: str) -> bool:
    """The question asks for a set of things, so each thing must be nameable."""
    return bool(_LIST_RE.search(question or ""))


def asks_for_details(question: str) -> bool:
    """The question asks what a thing IS, so a two-column answer is an under-answer."""
    return bool(_DETAILS_RE.search(question or ""))


def asks_count(question: str) -> bool:
    """The question asks for a quantity, which in this corpus often has rivals."""
    return bool(_COUNT_RE.search(question or ""))


# --------------------------------------------------------------------------- result parsing

def result_is_empty(result_text: str) -> bool:
    """True when a structured-query result carries no data: either the explicit
    no-results message, or a markdown table whose data cells are all NULL (rendered as
    empty) — e.g. SUM() over zero matching rows. A 0 value is NOT empty; zero can be a
    correct answer.

    DUPLICATE of `openai_client._sql_result_is_empty` (circular import otherwise); the
    test pins the two equal on five inputs.
    """
    if not result_text:
        return False
    if result_text.startswith("Query returned no results"):
        return True
    table_lines = [l.strip() for l in result_text.splitlines() if l.strip().startswith("|")]
    if len(table_lines) < 3:
        return False
    cells = [c.strip() for r in table_lines[2:] for c in r.strip("|").split("|")]
    if bool(cells) and all(c == "" for c in cells):
        return True
    return len(cells) == 1 and cells[0] in ("0", "0.0")


def result_is_failure(result_text: str) -> bool:
    """The query did not run at all. Not an issue to re-query — the executor already had
    its own repair attempt — so it goes straight to the document fallback."""
    return bool(result_text) and result_text.startswith("SQL query failed")


def result_columns(result_text: str) -> list:
    """The column names of the rendered table: the first line that starts with `|`."""
    for line in (result_text or "").splitlines():
        s = line.strip()
        if s.startswith("|"):
            return [c.strip() for c in s.strip("|").split("|")]
    return []


def result_rows(result_text: str) -> int:
    """How many data rows the rendered table shows (header and separator excluded)."""
    table_lines = [l.strip() for l in (result_text or "").splitlines() if l.strip().startswith("|")]
    return max(0, len(table_lines) - 2)


def result_truncation(result_text: str):
    """(shown, total) when the result was cut, else None."""
    m = re.search(r"\*Showing (\d+) of (\d+) rows\*", result_text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def result_total_rows(result_text: str) -> int:
    """How many rows the query actually matched — the truncation line's total when the
    result was cut, otherwise what is rendered."""
    cut = result_truncation(result_text)
    return cut[1] if cut else result_rows(result_text)


def has_result_shape(result_text: str) -> bool:
    return bool(re.search(r"(?m)^RESULT SHAPE\b", result_text or ""))


#: A backticked SQL line, possibly written across several lines. The generation prompt asks
#: for a single line, twice — but "uncommon" is not "impossible", and a mis-parse used to
#: leave the loop naming a table the query never read.
_SQL_LINE_RE = re.compile(r"^(?:Generated )?SQL: `(.*?)`\s*$", re.MULTILINE | re.DOTALL)


def result_sql(result_text: str) -> str:
    """The query the result came from — the last `SQL:` (or `Generated SQL:`) line."""
    found = _SQL_LINE_RE.findall(result_text or "")
    return found[-1] if found else ""


#: SQL keywords only — GROUP BY anywhere, or an aggregate call in the SELECT list. An
#: aggregate result has no per-entity identifier BY CONSTRUCTION, so demanding one (or
#: demanding every column of the underlying table) turns a correct breakdown into the whole
#: table with the counts lost.
AGGREGATE_RE = re.compile(r"\bGROUP\s+BY\b|\b(?:count|sum|avg|min|max)\s*\(", re.IGNORECASE)
_SELECT_LIST_RE = re.compile(r"\bSELECT\b(.*?)\bFROM\b", re.IGNORECASE | re.DOTALL)


def sql_is_aggregate(sql: str) -> bool:
    """True when the query groups, or aggregates in its SELECT list. An aggregate inside a
    WHERE-clause subquery does not count: the rows such a query returns are still
    entities, and they still need their identifier."""
    if not sql:
        return False
    head = _SELECT_LIST_RE.search(sql)
    head_end = head.end(1) if head else 0
    for m in AGGREGATE_RE.finditer(sql):
        if m.group(0).upper().startswith("GROUP"):
            return True
        if head and m.start() < head_end:
            return True
    return False


def result_is_single_cell(result_text: str) -> bool:
    """One column, one row — the shape a quantity answer comes back in."""
    return len(result_columns(result_text)) == 1 and result_rows(result_text) == 1


# --------------------------------------------------------------------------- card reading

def cards_in_sql(sql: str, routed_cards) -> list:
    """The cards the query actually read — those whose table name appears in the SQL as a
    whole word. NEVER falls back to every routed card: when the SQL names none of them,
    attribution is precisely what is missing, and naming a table the query never read is
    the one thing this must not do. The column-shaped issues then degrade to silence."""
    cards = [c for c in (routed_cards or []) if c and c.get("table")]
    if not sql:
        return []
    return [c for c in cards
            if re.search(r'(?<![A-Za-z0-9_])"?' + re.escape(c["table"]) + r'"?(?![A-Za-z0-9_])', sql)]


def card_identifier(card) -> str:
    """The column that names each entity of this table: the card's own declaration, or
    failing that the first column whose NAME has an identifying shape. Returns "" when the
    card offers neither — in which case no identifier issue can fire."""
    declared = (card or {}).get("identifier_column")
    if declared:
        return str(declared)
    for col in (card or {}).get("columns") or []:
        if str(col).lower().endswith(NAMEABLE_SUFFIXES):
            return str(col)
    return ""


def _is_citation_column(col: str) -> bool:
    c = str(col).lower()
    return (c in CITATION_EXACT
            or c.startswith(CITATION_PREFIXES)
            or c.endswith(CITATION_SUFFIXES))


def non_citation_columns(card) -> list:
    """A card's columns minus the ones that cite rather than describe."""
    return [c for c in ((card or {}).get("columns") or []) if not _is_citation_column(c)]


def result_is_nameable(result_cols, cards) -> bool:
    """Can a reader act on this list? Yes when a consulted card's declared identifier is in
    the result, or when any result column has an identifying name shape — which is exactly
    what the generation-time rule asks the writer for, so obeying that rule can never
    trigger a re-query here."""
    lowered = {str(c).lower() for c in result_cols}
    if any(c.endswith(NAMEABLE_SUFFIXES) for c in lowered):
        return True
    for card in cards or []:
        declared = str((card or {}).get("identifier_column") or "").lower()
        if declared and declared in lowered:
            return True
    return False


def best_identifier(cards, result_cols):
    """(column, table) — the most PRINTED identifying column the consulted cards offer that
    is not already in the result, or None. A number outranks a tag, a tag a name, and all
    three outrank the key a card declares, which is often internal."""
    lowered = {str(c).lower() for c in result_cols}
    best = None
    for order, card in enumerate(cards or []):
        table = (card or {}).get("table") or ""
        candidates = []
        for col in (card or {}).get("columns") or []:
            low = str(col).lower()
            if low in lowered:
                continue
            for rank, suffix in enumerate(IDENTIFIER_RANK):
                if low.endswith(suffix):
                    candidates.append((rank, str(col)))
                    break
        declared = (card or {}).get("identifier_column")
        if declared and str(declared).lower() not in lowered:
            candidates.append((len(IDENTIFIER_RANK), str(declared)))
        if not candidates:
            continue
        rank, col = min(candidates, key=lambda c: c[0])
        if best is None or (rank, order) < best[0]:
            best = ((rank, order), col, table)
    return (best[1], best[2]) if best else None


def quote_identifier(name: str) -> str:
    """A column name goes into the instruction as SQL the writer can paste. A plain name
    needs nothing; one carrying a space or punctuation needs double quotes, because
    backticks are not identifier quoting in this dialect and the re-query would simply
    fail — landing the question in the document fallback for no reason."""
    text = str(name)
    return text if re.fullmatch(r"[A-Za-z0-9_]+", text) else f'"{text}"'


# --------------------------------------------------------------------------- inspection

def _empty_issue(sql: str) -> Issue:
    return Issue(
        EMPTY,
        "No rows — re-querying by printed name",
        (f"The previous query `{sql}` returned no rows. Re-read the column samples; "
         f"filter a place or entity by its printed NAME on the name column with "
         f"ILIKE '%…%', never by a built id; if the question names a place, use the "
         f"one-row-per-place-and-item table."),
    )


def _identifier_issue(column: str, table: str) -> Issue:
    return Issue(
        IDENTIFIER_MISSING,
        f"The list has no {column} column — re-querying",
        (f"The question asks to list entities; SELECT the identifier column "
         f"`{quote_identifier(column)}` of `{table}` in addition to the columns you "
         f"selected, same filter."),
    )


def _narrow_issue(table: str, shown: int, available: int) -> Issue:
    return Issue(
        NARROW_SELECT,
        f"Only {shown} columns of {available} — widening",
        (f"Select every non-citation column of `{table}` (at most {available} columns) "
         f"for the matching rows only (all parameters / all attributes), same filter."),
    )


def inspect_result(result_text: str, question: str, routed_cards) -> list:
    """Every issue the result text shows, in the spec's priority order. Pure: no I/O, no
    model call, no state. An issue whose `instruction` is "" is a finding, not a re-query."""
    issues = []
    sql = result_sql(result_text)
    cards = cards_in_sql(sql, routed_cards)
    cols = result_columns(result_text)
    aggregate = sql_is_aggregate(sql)
    empty = result_is_empty(result_text)

    if empty:
        issues.append(_empty_issue(sql))

    # An aggregate result is exempt: it has no per-entity identifier and no wider row to
    # widen to. An empty result is NOT exempt — its header still says which columns were
    # selected, and the priority order decides which issue the loop acts on.
    if cols and not aggregate:
        if asks_to_list(question) or asks_for_details(question):
            if not result_is_nameable(cols, cards):
                best = best_identifier(cards, cols)
                if best:
                    issues.append(_identifier_issue(best[0], best[1]))

        if len(cols) <= MAX_NARROW_COLUMNS and asks_for_details(question):
            for card in cards:
                available = non_citation_columns(card)
                if len(available) > MAX_NARROW_COLUMNS:
                    issues.append(_narrow_issue(card["table"], len(cols), len(available)))
                    break

    cut = result_truncation(result_text)
    if cut and not has_result_shape(result_text):
        issues.append(Issue(
            TRUNCATED_NO_SHAPE,
            f"Truncated ({cut[0]} of {cut[1]} rows) with no shape line",
            "",
        ))

    # Only when the tables answered WITH a quantity: a question containing "total" whose
    # SQL returned a list of rows states no quantity to cross-check, and heading three
    # excerpts "other records that state this quantity" above such a result would be a lie.
    if (asks_count(question) and not empty
            and (aggregate or result_is_single_cell(result_text))):
        issues.append(Issue(
            COUNT_CROSSCHECK,
            "Quantity question — cross-checking the documents",
            "",
        ))

    issues.sort(key=lambda i: ISSUE_ORDER.index(i.kind))
    return issues


def requery_instruction(issue) -> str:
    """The deterministic sentence appended to the question for the next query ("" when the
    issue is a finding rather than something to try again)."""
    return issue.instruction if issue else ""


def first_requery_issue(issues):
    """The highest-priority issue that can actually be acted on, or None."""
    for issue in issues or []:
        if issue.instruction:
            return issue
    return None


def step_instruction_suffix(step: int, issue, previous_sql: str) -> str:
    """What gets appended to the question for query number `step`."""
    return (f"\n(Investigation step {step}: {requery_instruction(issue)} "
            f"Previous SQL: `{previous_sql}`)")


def investigation_trailer(step_count: int, kinds) -> str:
    """The one line that tells the answer writer an investigation happened. Never quoted
    back to the user — the caller's never-print rule names it."""
    return (f"INVESTIGATION - steps: {step_count}; issues: "
            + (", ".join(kinds) if kinds else "none"))


def investigation_budget_seconds() -> float:
    """The wall-clock budget for the whole investigation (spec §4: "wall-clock guard
    reuses SQL_QUERY_TIMEOUT"). Read the way `sql_tool` reads it, and lazily, so that
    importing this module does not drag in the model SDK."""
    try:
        from app.services.sql_tool import SQL_QUERY_TIMEOUT
        return float(SQL_QUERY_TIMEOUT)
    except Exception:
        return DEFAULT_BUDGET_SECONDS


def _top_excerpts(text: str) -> str:
    """At most CROSSCHECK_EXCERPTS of whatever the caller's search returned."""
    if not text:
        return ""
    parts = text.split(EXCERPT_SEPARATOR)
    return EXCERPT_SEPARATOR.join(parts[:CROSSCHECK_EXCERPTS])


# --------------------------------------------------------------------------- the loop

def iter_sql_investigation(question, user_id, sb, *, execute, search=None, routed_cards=None,
                           max_steps=3, user_question=None, clock=None):
    """Run the bounded investigation, yielding an event before and after every external call.

    Events:
      ("step",       {"step": k, "issue": kind or None, "detail": str, "sql": str})
                     — before query k; `sql` is the query being reacted to ("" for k=1).
      ("step_done",  {"step": k, "rows": int, "empty": bool, "issues_found": [kind, …]})
                     — after query k, so the caller can close its tool event with an
                       outcome rather than only an intention.
      ("crosscheck", {"kind": …, "detail": str, "query": str})
                     — before the single retrieval call, whether that call is the quantity
                       cross-check or the terminal fallback for an empty/failed result.
      ("final",      Investigation)

    `execute(question, user_id, sb) -> str` and `search(query) -> str` are injected so the
    loop is testable without a database, a model or a network. `user_question` is the
    user's own wording, which is what the document search gets (the tool's paraphrase
    dilutes keyword ranking); it defaults to `question`. `clock` is injected by the test.
    """
    cards = list(routed_cards or [])
    try:
        max_steps = int(max_steps)
    except (TypeError, ValueError):
        max_steps = 1
    max_steps = max(1, max_steps)
    looping = max_steps > 1
    clock = clock or time.monotonic
    started = clock()
    budget = investigation_budget_seconds()
    search_query = question if user_question is None else user_question

    steps = []
    trail = []
    issues = []
    result_text = ""
    previous_sql = ""
    pending = None
    last_kind = None
    ask = question

    def note(kind):
        if kind and kind not in trail:
            trail.append(kind)

    for step in range(max_steps):
        k = step + 1
        event = {
            "step": k,
            "issue": pending.kind if pending else None,
            "detail": pending.detail if pending else FIRST_STEP_DETAIL,
            "sql": previous_sql,
        }
        steps.append(event)
        yield ("step", event)

        result_text = execute(ask, user_id, sb)
        previous_sql = result_sql(result_text)
        issues = inspect_result(result_text, question, cards) if looping else []
        for issue in issues:
            note(issue.kind)
        yield ("step_done", {
            "step": k,
            "rows": result_total_rows(result_text),
            "empty": result_is_empty(result_text),
            "issues_found": [i.kind for i in issues],
        })

        if k >= max_steps or result_is_failure(result_text):
            break
        if clock() - started > budget:
            break
        nxt = first_requery_issue(issues)
        if nxt is None:
            break
        # The same issue twice running means the instruction did not work; a third
        # identical instruction is a step spent to no purpose.
        if nxt.kind == last_kind:
            break
        pending, last_kind = nxt, nxt.kind
        ask = question + step_instruction_suffix(k + 1, nxt, previous_sql)

    crosscheck = None
    source_tool = TOOL_SQL

    # Terminal fallbacks — the caller used to do these inline, and they are what
    # `max_steps=1` must reproduce byte for byte.
    if search is not None and (result_is_empty(result_text) or result_is_failure(result_text)):
        kind = EMPTY if result_is_empty(result_text) else FAILED
        yield ("crosscheck", {
            "kind": kind,
            "detail": ("No rows — checking the documents" if kind == EMPTY
                       else "The query failed — checking the documents"),
            "query": search_query,
        })
        found = search(search_query)
        if found:
            result_text = (EMPTY_FALLBACK_PREFIX + found + EMPTY_FALLBACK_SUFFIX
                           if kind == EMPTY else found)
            source_tool = TOOL_SEARCH
        if looping:
            note(kind)

    # The quantity cross-check: one retrieval call, appended rather than substituted, and
    # only when the tables actually answered (otherwise the fallback above already asked).
    elif looping and search is not None and any(i.kind == COUNT_CROSSCHECK for i in issues):
        yield ("crosscheck", {
            "kind": COUNT_CROSSCHECK,
            "detail": "Cross-checking the quantity against the documents",
            "query": search_query,
        })
        crosscheck = _top_excerpts(search(search_query))
        if crosscheck:
            result_text = result_text + "\n\n" + CROSSCHECK_HEADING + "\n\n" + crosscheck

    if looping:
        result_text = result_text + "\n\n" + investigation_trailer(len(steps), trail)

    yield ("final", Investigation(result_text, steps, crosscheck, source_tool, trail))


def run_sql_investigation(question, user_id, sb, *, execute, search=None, routed_cards=None,
                          max_steps=3, user_question=None, clock=None) -> Investigation:
    """`iter_sql_investigation` drained: the same work, for a caller with no use for the
    per-step events."""
    final = None
    for kind, payload in iter_sql_investigation(question, user_id, sb, execute=execute,
                                                search=search, routed_cards=routed_cards,
                                                max_steps=max_steps,
                                                user_question=user_question, clock=clock):
        if kind == "final":
            final = payload
    return final
