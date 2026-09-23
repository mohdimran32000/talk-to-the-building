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
  regexes are English shapes only. Section 18 of the test scans this file for the names it
  must not contain.
* DETERMINISTIC — every issue is detected by code. The model is never asked "is this
  right?", because a model that wrote a bad query is not the thing to ask whether the query
  was bad.
* DEGRADES CLEANLY — `max_steps <= 1` reproduces the pre-loop behaviour exactly: one query,
  no re-query, no cross-check, no trailer line, and the same empty-result and failed-result
  fallbacks to the document index that the caller used to perform inline.

`result_is_empty` below is a deliberate DUPLICATE of `openai_client._sql_result_is_empty`.
Importing it would be circular the moment `openai_client` imports this module; the test pins
the two equal on five inputs so the copy cannot drift silently.
"""
from __future__ import annotations

import re
from collections import namedtuple

# --------------------------------------------------------------------------- types

#: `kind` is one of the constants below; `detail` is the one-line human wording the caller
#: puts on its `tool_start`/`tool_done` events; `instruction` is the deterministic sentence
#: appended to the question for the next query, or "" for an issue that is not a re-query.
Issue = namedtuple("Issue", "kind detail instruction")

#: `result_text` is what the answer writer is handed; `steps` are the per-query event
#: payloads in order; `crosscheck` is the retrieved excerpt text of the quantity
#: cross-check, or None (it is None for the terminal document fallbacks, which replace
#: `result_text` instead of riding alongside it).
Investigation = namedtuple("Investigation", "result_text steps crosscheck")

EMPTY = "EMPTY"
IDENTIFIER_MISSING = "IDENTIFIER_MISSING"
NARROW_SELECT = "NARROW_SELECT"
TRUNCATED_NO_SHAPE = "TRUNCATED_NO_SHAPE"
COUNT_CROSSCHECK = "COUNT_CROSSCHECK"

#: Priority order, spec §3. The loop acts on the first of these that carries an
#: instruction; the last two never carry one.
ISSUE_ORDER = (EMPTY, IDENTIFIER_MISSING, NARROW_SELECT, TRUNCATED_NO_SHAPE, COUNT_CROSSCHECK)

# --------------------------------------------------------------------------- constants

#: A result this narrow is a candidate for NARROW_SELECT — and a table with more
#: non-citation columns than this has something to widen to.
MAX_NARROW_COLUMNS = 3

#: Column-name shapes that name a thing, used only when a card declares no
#: `identifier_column`. Shape, not vocabulary: no actual column is named here.
IDENTIFIER_SUFFIXES = ("_number", "_id", "_tag", "_name")

#: Columns that cite a value rather than being one. A card's "non-citation columns" are
#: what a details question should get back. The exact names are the two free-text ones
#: every table in this shape of corpus carries; the rest are recognised by prefix/suffix,
#: because provenance columns are written both ways (`source_x` and `x_source`).
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


def result_sql(result_text: str) -> str:
    """The query the result came from — the last `SQL:` (or `Generated SQL:`) line."""
    found = re.findall(r"(?m)^(?:Generated )?SQL: `(.*)`\s*$", result_text or "")
    return found[-1] if found else ""


def result_truncation(result_text: str):
    """(shown, total) when the result was cut, else None."""
    m = re.search(r"\*Showing (\d+) of (\d+) rows\*", result_text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def has_result_shape(result_text: str) -> bool:
    return bool(re.search(r"(?m)^RESULT SHAPE\b", result_text or ""))


# --------------------------------------------------------------------------- card reading

def cards_in_sql(sql: str, routed_cards) -> list:
    """The cards the query actually read — those whose table name appears in the SQL as a
    whole word. Falls back to every routed card when the SQL names none of them (a
    rewritten or unparseable query must still be inspectable)."""
    cards = [c for c in (routed_cards or []) if c and c.get("table")]
    if not sql:
        return cards
    hit = [c for c in cards
           if re.search(r'(?<![A-Za-z0-9_])"?' + re.escape(c["table"]) + r'"?(?![A-Za-z0-9_])', sql)]
    return hit or cards


def card_identifier(card) -> str:
    """The column that names each entity of this table: the card's own declaration, or
    failing that the first column whose NAME has an identifying shape. Returns "" when the
    card offers neither — in which case no identifier issue can fire."""
    declared = (card or {}).get("identifier_column")
    if declared:
        return str(declared)
    for col in (card or {}).get("columns") or []:
        if str(col).lower().endswith(IDENTIFIER_SUFFIXES):
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
        (f"The question asks to list entities; SELECT the identifier column `{column}` "
         f"of `{table}` in addition to the columns you selected, same filter."),
    )


def _narrow_issue(table: str, shown: int, available: int) -> Issue:
    return Issue(
        NARROW_SELECT,
        f"Only {shown} columns of {available} — widening",
        (f"Select every non-citation column of `{table}` for the matching rows "
         f"(all parameters / all attributes), same filter."),
    )


def inspect_result(result_text: str, question: str, routed_cards) -> list:
    """Every issue the result text shows, in the spec's priority order. Pure: no I/O, no
    model call, no state. An issue whose `instruction` is "" is a finding, not a re-query."""
    issues = []
    sql = result_sql(result_text)
    cards = cards_in_sql(sql, routed_cards)
    cols = result_columns(result_text)
    lowered = {str(c).lower() for c in cols}

    if result_is_empty(result_text):
        issues.append(_empty_issue(sql))

    # A result the query never produced (a failure, or nothing at all) says nothing about
    # the columns that were selected, so the column-shaped issues below are not asked.
    if cols and not result_is_empty(result_text):
        if asks_to_list(question) or asks_for_details(question):
            for card in cards:
                ident = card_identifier(card)
                if ident and ident.lower() not in lowered:
                    issues.append(_identifier_issue(ident, card["table"]))
                    break

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

    if asks_count(question):
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


def _top_excerpts(text: str) -> str:
    """At most CROSSCHECK_EXCERPTS of whatever the caller's search returned."""
    if not text:
        return ""
    parts = text.split(EXCERPT_SEPARATOR)
    return EXCERPT_SEPARATOR.join(parts[:CROSSCHECK_EXCERPTS])


# --------------------------------------------------------------------------- the loop

def iter_sql_investigation(question, user_id, sb, *, execute, search=None,
                           routed_cards=None, max_steps=3):
    """Run the bounded investigation, yielding an event before every external call.

    Events:
      ("step",       {"step": k, "issue": kind or None, "detail": str, "sql": str})
                     — before query k; `sql` is the query being reacted to ("" for k=1).
      ("crosscheck", {"kind": …, "detail": str, "query": str})
                     — before the single retrieval call, whether that call is the quantity
                       cross-check or the terminal fallback for an empty/failed result.
      ("final",      Investigation)

    `execute(question, user_id, sb) -> str` and `search(query) -> str` are injected so the
    loop is testable without a database, a model or a network.
    """
    cards = list(routed_cards or [])
    try:
        max_steps = int(max_steps)
    except (TypeError, ValueError):
        max_steps = 1
    max_steps = max(1, max_steps)
    looping = max_steps > 1

    steps = []
    trail = []
    issues = []
    result_text = ""
    previous_sql = ""
    pending = None
    ask = question

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

        if k >= max_steps or result_is_failure(result_text):
            break
        pending = first_requery_issue(issues)
        if pending is None:
            break
        trail.append(pending.kind)
        ask = question + step_instruction_suffix(k + 1, pending, previous_sql)

    crosscheck = None

    # Terminal fallbacks — the caller used to do these inline, and they are what
    # `max_steps=1` must reproduce byte for byte.
    if search is not None and (result_is_empty(result_text) or result_is_failure(result_text)):
        kind = EMPTY if result_is_empty(result_text) else "FAILED"
        yield ("crosscheck", {
            "kind": kind,
            "detail": ("No rows — checking the documents" if kind == EMPTY
                       else "The query failed — checking the documents"),
            "query": question,
        })
        found = search(question)
        if found:
            result_text = (EMPTY_FALLBACK_PREFIX + found + EMPTY_FALLBACK_SUFFIX
                           if kind == EMPTY else found)
        if looping:
            trail.append(kind)

    # The quantity cross-check: one retrieval call, appended rather than substituted, and
    # only when the tables actually answered (otherwise the fallback above already asked).
    elif looping and search is not None and any(i.kind == COUNT_CROSSCHECK for i in issues):
        yield ("crosscheck", {
            "kind": COUNT_CROSSCHECK,
            "detail": "Cross-checking the quantity against the documents",
            "query": question,
        })
        crosscheck = _top_excerpts(search(question))
        if crosscheck:
            result_text = result_text + "\n\n" + CROSSCHECK_HEADING + "\n\n" + crosscheck
            trail.append(COUNT_CROSSCHECK)

    if looping:
        result_text = result_text + "\n\n" + investigation_trailer(len(steps), trail)

    yield ("final", Investigation(result_text, steps, crosscheck))


def run_sql_investigation(question, user_id, sb, *, execute, search=None,
                          routed_cards=None, max_steps=3) -> Investigation:
    """`iter_sql_investigation` drained: the same work, for a caller with no use for the
    per-step events."""
    final = None
    for kind, payload in iter_sql_investigation(question, user_id, sb, execute=execute,
                                                search=search, routed_cards=routed_cards,
                                                max_steps=max_steps):
        if kind == "final":
            final = payload
    return final
