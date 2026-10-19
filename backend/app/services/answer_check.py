"""answer_check.py - a deterministic checker over the FINAL ANSWER, OFF by default
(spec 2026-09-30, task W8-A1 part B).

WHY THIS EXISTS
The bounded investigation loop (`sql_loop.py`) checks the SQL RESULT - an empty result, a
list with no identifier column, a count that deserves a document cross-check - and re-queries
or cross-checks when it finds a deficiency it can name. Nothing checks the ANSWER itself after
it is written. This module is that second look: pure functions that read (question, the final
answer text, the turn's tool results, which tools ran) and return the problems found, each one
carrying plain-words retry text. It names no mechanism for RUNNING the retry - that is the
caller's job (`openai_client.py`, behind `ANSWER_CHECK=1`) - because deciding there IS a
problem and deciding WHAT TO DO about it are different concerns, and only the first one can be
unit-tested without a model call.

THE FOUR CHECKS
  REFUSED_WITHOUT_SEARCH - the answer opens by refusing, and the turn ran the SQL tool and
    NEVER a document search. The SQL tool alone cannot see the documents; a wrong table or a
    narrow query can look exactly like "not recorded" when the document search was never tried.
  REFUSED_WITH_ROWS - the answer opens by refusing while the turn's LAST sql result plainly
    holds rows: the data was there and the answer did not use it.
  UNGROUNDED_NUMBER - a number in the answer that appears in none of the question, the sql
    results (their companion lines included), the excerpts - nor as the sum or difference of
    two numbers any of those print. See `_iter_numbers` and the EXCLUSIONS below for exactly
    which numbers are even candidates.
  MIXED_SET - a `NOTES INSIDE THE FIGURE` result carries an "In all" line (two or more
    additive columns, each given WITH and WITHOUT the noted rows); the answer states one
    column's WITH figure beside another column's WITHOUT figure - a mixed, self-contradictory
    pair - instead of giving each reading as one complete set.

EXCLUSIONS FOR UNGROUNDED_NUMBER (decided and documented here, per the task brief):
  - years: a bare 4-digit number from 1900-2099 is never a candidate - it is almost always a
    year, and the document corpus prints plenty that the question/results never repeat.
  - dates: every number consumed by an ISO date (`2026-10-10`), a slash date (`07/04/2021`) or
    a "D Month YYYY"/"Month D, YYYY" date is excluded - a day-of-month number in particular is
    not a year and would otherwise slip through.
  - list ordinals: a number opening a line as a list marker (`1. `, `2) `) is formatting, not a
    figure.
  - small meta-enumeration counts: a 1-2 digit number immediately followed by a word like
    "items"/"points"/"steps" ("the following 3 points") is the model describing its OWN
    answer's shape, not citing a sourced figure.
  - tag/identifier digits: a number touching a letter with no space (`L6B`), or touching a
    hyphen with no space on either side (`TAG-07-99`, a page range `12-15`), is read as part of
    an identifier/code, never extracted as a free-standing figure to check.
  - units are handled by tokenisation, not as a drop category: the number regex never includes
    a unit suffix in the match, so "14,250 W" and "14250" compare equal once separators are
    stripped.
  These are a deliberately CONSERVATIVE, best-effort heuristic, not a parser - see the task
  report for what the gate measured and which checks that earned a place in `SHIPPED_CHECKS`.

GENERIC ONLY: nothing below names a table, column, value, card id or question text of any one
project (this is a public repo) - every fixture in the matching test file is invented.
"""
import re
from collections import namedtuple

from app.services.sql_loop import result_is_empty, result_rows
from app.services.sql_tool import NOTES_INSIDE_FIGURE_HEADING

#: One problem `find_problems` (or a single check function) found. `kind` is one of the four
#: check names below; `detail` is a short machine-readable note (logged, never shown to the
#: user); `instruction` is the plain-words sentence the retry's system prompt appends.
Problem = namedtuple("Problem", "kind detail instruction")

REFUSED_WITHOUT_SEARCH = "REFUSED_WITHOUT_SEARCH"
REFUSED_WITH_ROWS = "REFUSED_WITH_ROWS"
UNGROUNDED_NUMBER = "UNGROUNDED_NUMBER"
MIXED_SET = "MIXED_SET"

ALL_CHECKS = (REFUSED_WITHOUT_SEARCH, REFUSED_WITH_ROWS, UNGROUNDED_NUMBER, MIXED_SET)

#: Checks that measured ZERO false alarms on PASS answers in the recorded goal-function runs
#: (task W8-A1's gate, 919 scored items across 15 trace files - see `task-W8A1-report.md` for
#: the full table). A check left OUT of this set is still built and fully tested above; it
#: ships OFF because it misfired on at least one re-scored PASS answer:
#:   REFUSED_WITHOUT_SEARCH  22 catches / 5  false alarms (2 distinct questions, repeated
#:                           across runs) - every one a CORRECT refusal of a field the data
#:                           genuinely never records; this check cannot tell that apart from
#:                           a wrong one without the retry's own search actually running.
#:   REFUSED_WITH_ROWS       24 catches / 8  false alarms (4 distinct questions) - same shape:
#:                           the SQL rows that exist are about a different attribute than the
#:                           one asked for, and refusing was the right call.
#:   UNGROUNDED_NUMBER       101 catches / 195 false alarms - far too noisy; 85.6% of the false
#:                           alarms (167 of 195) and 81% of the catches (82 of 101) sit in the
#:                           5 of 15 trace files recorded before excerpt TEXT was captured at
#:                           all, so most of its signal here is an artifact of that gap, not of
#:                           the check - but even restricted to the 10 complete files it still
#:                           has 28 false alarms, so OFF is the right call regardless.
#:   MIXED_SET               0 catches / 0 false alarms - its precondition (a NOTES INSIDE THE
#:                           FIGURE block with an "In all" line) occurs exactly twice in the
#:                           whole dataset, both already-correct PASS answers it correctly
#:                           leaves alone. ON, measured clean, with no FAIL case yet to prove
#:                           it catches anything - say so, don't overclaim it.
#: `find_problems` reads this when the caller passes no explicit `enabled` set, so turning
#: `ANSWER_CHECK=1` on runs exactly this one check, never the other three, until a later
#: measurement (the brief's own "next paid run") adds one.
SHIPPED_CHECKS = frozenset({MIXED_SET})


# --------------------------------------------------------------------------- is this a refusal?

_REFUSAL_PHRASES = (
    "do not contain", "does not contain",
    "do not record", "does not record",
    "do not state", "does not state",
    "do not specify", "does not specify",
    "do not mention", "does not mention",
    "do not list", "does not list",
    "do not show", "does not show",
    "is not recorded", "are not recorded",
    "is not stated", "are not stated",
    "is not specified", "are not specified",
    "is not available", "are not available",
    "no record of", "not contain the answer",
    "cannot find", "unable to find", "could not find",
)

_SENTENCE_END_RE = re.compile(r"[.!?](?:\s|$)")


def _first_sentence(text: str) -> str:
    text = (text or "").strip()
    m = _SENTENCE_END_RE.search(text)
    return text[:m.end()] if m else text


def is_refusal_opening(answer: str) -> bool:
    """True when the answer's OWN OPENING sentence says the records/documents do not
    contain or record what was asked - a refusal with no figure of its own, as opposed to a
    two-part answer that already gave a figure before refusing the rest (that shape is
    deliberately NOT this: a figure already in hand makes the retry this module asks for
    pointless, even wasteful, for the part already answered)."""
    return any(p in _first_sentence(answer).lower() for p in _REFUSAL_PHRASES)


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def iter_sentences(text: str):
    """Yield every sentence of `text`, split on a sentence-ending punctuation mark followed
    by whitespace - good enough for a prose answer (never run on SQL or a markdown table). A
    text with no such boundary is one sentence; an empty/blank text yields nothing."""
    text = (text or "").strip()
    if not text:
        return
    for raw in _SENTENCE_SPLIT_RE.split(text):
        sentence = raw.strip()
        if sentence:
            yield sentence


def refusal_sentences(text: str) -> list:
    """Every sentence of `text` - not just the OPENING one `is_refusal_opening` looks at -
    whose own words match the refusal-phrase vocabulary above. Generic, and reused rather
    than duplicated: the agentic second look (task W10-A1) needs every sentence of a (possibly
    multi-part) answer that reads as a refusal, not just whether the answer opens with one, so
    it calls this instead of carrying a second copy of `_REFUSAL_PHRASES`."""
    return [s for s in iter_sentences(text) if any(p in s.lower() for p in _REFUSAL_PHRASES)]


# --------------------------------------------------------------------------- check 1 & 2

def check_refused_without_search(answer: str, tools_ran) -> "Problem | None":
    """REFUSED_WITHOUT_SEARCH - SQL ran, search never did, and the answer opens refusing."""
    tools = set(tools_ran or [])
    if "query_structured_data" not in tools or "search_documents" in tools:
        return None
    if not is_refusal_opening(answer):
        return None
    return Problem(
        REFUSED_WITHOUT_SEARCH,
        "SQL-only turn, answer opens with a refusal, no document search ran",
        "The previous answer said this was not recorded, but the turn never searched the "
        "documents. Matching document excerpts are included below - answer from them "
        "together with the data above, or say exactly which specific part is genuinely not "
        "recorded anywhere.",
    )


def check_refused_with_rows(answer: str, sql_results) -> "Problem | None":
    """REFUSED_WITH_ROWS - the turn's LAST sql result holds rows, the answer opens refusing."""
    results = [r for r in (sql_results or []) if r]
    if not results:
        return None
    last = results[-1]
    if result_is_empty(last):
        return None
    n = result_rows(last)
    if n <= 0:
        return None
    if not is_refusal_opening(answer):
        return None
    return Problem(
        REFUSED_WITH_ROWS,
        f"the turn's last SQL result holds {n} row(s), the answer opens with a refusal",
        f"The result above holds {n} row{'' if n == 1 else 's'}; answer from them, or say "
        f"exactly which part is not recorded.",
    )


# --------------------------------------------------------------------------- number extraction

_NUMBER_TOKEN_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

_MONTH_RE = (r"January|February|March|April|May|June|July|August|September|October|"
             r"November|December")
_DATE_PATTERNS = (
    re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b"),                      # 2026-10-10
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),                    # 07/04/2021
    re.compile(rf"\b\d{{1,2}}\s+(?:{_MONTH_RE})\s+\d{{4}}\b", re.IGNORECASE),   # 12 April 2021
    re.compile(rf"\b(?:{_MONTH_RE})\s+\d{{1,2}},?\s+\d{{4}}\b", re.IGNORECASE),  # April 12, 2021
)
_META_COUNT_RE = re.compile(
    r"\b\d{1,2}\b(?=\s+(?:following|items?|points?|parts?|steps?|ways?|options?|bullets?|"
    r"sentences?|readings?)\b)", re.IGNORECASE)
_ORDINAL_LINE_RE = re.compile(r"^[ \t]*[*\-•]?[ \t]*\d{1,3}[.)](?=\s)")


def _iter_lines_with_offset(text: str):
    offset = 0
    for line in text.splitlines(keepends=True):
        yield offset, line
        offset += len(line)


def _excluded_spans(text: str):
    """Character ranges of `text` whose numbers are never UNGROUNDED_NUMBER candidates:
    dates, small meta-enumeration counts, leading list ordinals. (Years and tag digits are
    excluded per-match in `_iter_numbers`/`_is_year`, not as spans, since they need no
    surrounding context.)"""
    spans = [m.span() for pat in _DATE_PATTERNS for m in pat.finditer(text)]
    spans += [m.span() for m in _META_COUNT_RE.finditer(text)]
    for offset, line in _iter_lines_with_offset(text):
        m = _ORDINAL_LINE_RE.match(line)
        if m:
            spans.append((offset + m.start(), offset + m.end()))
    return spans


def _is_year(token: str) -> bool:
    return len(token) == 4 and token.isdigit() and 1900 <= int(token) <= 2099


def _iter_numbers(text: str):
    """Yield (value, start, end) for each FREE-STANDING number in `text` - one that does not
    touch a letter or a hyphen with no intervening space on either side, so an identifier or a
    coded tag (`WIDGET#42-Q7`, `L6B`, a `12-15` page range) never contributes a bare number.
    No exclusions beyond that here - dates/ordinals/meta-counts are a separate pass
    (`_excluded_spans`) applied only to the ANSWER side; the grounding POOL stays permissive."""
    text = text or ""
    for m in _NUMBER_TOKEN_RE.finditer(text):
        start, end = m.start(), m.end()
        before = text[start - 1] if start > 0 else ""
        after = text[end] if end < len(text) else ""
        if before.isalpha() or after.isalpha() or before == "-" or after == "-":
            continue
        try:
            value = float(m.group().replace(",", ""))
        except ValueError:
            continue
        yield value, start, end


def _numbers_for_checking(answer: str):
    """The answer's own candidate numbers, after every exclusion (years, dates, ordinals,
    meta-counts, tag digits)."""
    answer = answer or ""
    excluded = _excluded_spans(answer)
    out = []
    for value, start, end in _iter_numbers(answer):
        if any(s < end and start < e for s, e in excluded):
            continue
        if _is_year(answer[start:end]):
            continue
        out.append(value)
    return out


def _pool_numbers(texts):
    """Every free-standing number across `texts` (question / sql results / excerpts) - the
    GROUNDING pool. Deliberately permissive: a year or an ordinal printed in the SOURCE still
    grounds the answer stating that same number (exclusions only decide what the answer's OWN
    numbers must be checked against, not what counts as evidence)."""
    out = []
    for t in texts:
        for value, _, _ in _iter_numbers(t or ""):
            out.append(value)
    return out


#: Pairwise sum/difference is O(n^2) on the pool's DISTINCT values; one excerpt or a wide SQL
#: table can print hundreds of numbers, so this caps the expansion rather than let one question
#: go quadratic on thousands. Direct membership (no sum/difference) still runs regardless.
_MAX_POOL_FOR_PAIRS = 300
_TOLERANCE = 0.01


def _fmt(value: float) -> str:
    return str(int(value)) if value == int(value) else f"{value:g}"


def check_ungrounded_number(question: str, answer: str, sql_results, excerpts) -> list:
    """UNGROUNDED_NUMBER - every candidate number in the answer that grounds in none of the
    question, the sql results, the excerpts, nor as the sum or difference of two numbers any
    of those print. Returns a list - an answer can state more than one such number."""
    pool = _pool_numbers([question] + list(sql_results or []) + list(excerpts or []))
    distinct = sorted({round(v, 2) for v in pool})
    extended = set(distinct)
    if len(distinct) <= _MAX_POOL_FOR_PAIRS:
        for i, a in enumerate(distinct):
            for b in distinct[i:]:
                extended.add(round(a + b, 2))
                extended.add(round(abs(a - b), 2))

    problems = []
    seen = set()
    for value in _numbers_for_checking(answer):
        key = round(value, 2)
        if key in seen:
            continue
        if any(abs(key - g) <= _TOLERANCE for g in extended):
            continue
        seen.add(key)
        shown = _fmt(value)
        problems.append(Problem(
            UNGROUNDED_NUMBER,
            f"{shown} appears in the answer but not in the question, the data shown, the "
            f"documents, or as the sum/difference of two numbers they show",
            f"The previous answer stated {shown}, which does not appear in the question, the "
            f"data shown above, or the documents, and is not the sum or difference of two "
            f"numbers they show. Recompute it strictly from what is shown above, or say it is "
            f"not recorded.",
        ))
    return problems


# --------------------------------------------------------------------------- check 4: MIXED_SET

_IN_ALL_LINE_RE = re.compile(r"^In all: (.+)$", re.MULTILINE)
_IN_ALL_PART_RE = re.compile(
    r"([^\d;]+?)\s+([\d,]+(?:\.\d+)?)\s*\(([\d,]+(?:\.\d+)?)\s+without the noted rows\)")


def _in_all_pairs(result_text: str):
    """[(column, with_value, without_value), ...] from a result's own "In all: ..." line
    (`sql_tool._in_all_line`'s format), or [] when the result carries no NOTES INSIDE THE
    FIGURE block, or that block has no "In all" line at all (a single-group aggregate never
    gets one - see `sql_tool._in_all_line`)."""
    result_text = result_text or ""
    if NOTES_INSIDE_FIGURE_HEADING not in result_text:
        return []
    m = _IN_ALL_LINE_RE.search(result_text)
    if not m:
        return []
    out = []
    for col, w, wo in _IN_ALL_PART_RE.findall(m.group(1)):
        try:
            out.append((col.strip(), float(w.replace(",", "")), float(wo.replace(",", ""))))
        except ValueError:
            continue
    return out


def _first_value_position(text: str, value: float):
    best = None
    for v, start, _ in _iter_numbers(text or ""):
        if abs(v - value) <= _TOLERANCE and (best is None or start < best):
            best = start
    return best


_MIXED_SET_INSTRUCTION = (
    "The previous answer mixed one reading's figure with the other reading's figure. Give "
    "each reading as one complete set: state the value WITH the noted rows together, and the "
    "value WITHOUT them together, never pairing a figure from one reading with a figure from "
    "the other."
)


def check_mixed_set(answer: str, sql_results) -> "Problem | None":
    """MIXED_SET - a result's "In all" line gives 2+ additive columns both WITH and WITHOUT
    the noted rows; the answer's FIRST mention of each column commits it to one reading or the
    other, and those commitments disagree across columns."""
    for result_text in (sql_results or []):
        pairs = _in_all_pairs(result_text)
        distinct = [(c, w, wo) for c, w, wo in pairs if abs(w - wo) > _TOLERANCE]
        if len(distinct) < 2:
            continue
        committed = []
        for _col, w, wo in distinct:
            pos_w = _first_value_position(answer, w)
            pos_wo = _first_value_position(answer, wo)
            if pos_w is None and pos_wo is None:
                continue
            if pos_wo is None or (pos_w is not None and pos_w < pos_wo):
                committed.append("with")
            else:
                committed.append("without")
        if len(set(committed)) > 1:
            return Problem(
                MIXED_SET,
                "the answer pairs one reading's figure with the other reading's figure",
                _MIXED_SET_INSTRUCTION,
            )
    return None


# --------------------------------------------------------------------------- entry point

def find_problems(question, answer, sql_results, excerpts, tools_ran, enabled=None) -> list:
    """Run the enabled checks (default `SHIPPED_CHECKS`) and return every `Problem` found.
    Order matches `ALL_CHECKS`. Pure - no I/O, no model call; the caller decides what to do
    with a non-empty result."""
    enabled = SHIPPED_CHECKS if enabled is None else frozenset(enabled)
    problems = []
    if REFUSED_WITHOUT_SEARCH in enabled:
        p = check_refused_without_search(answer, tools_ran)
        if p:
            problems.append(p)
    if REFUSED_WITH_ROWS in enabled:
        p = check_refused_with_rows(answer, sql_results)
        if p:
            problems.append(p)
    if UNGROUNDED_NUMBER in enabled:
        problems.extend(check_ungrounded_number(question, answer, sql_results, excerpts))
    if MIXED_SET in enabled:
        p = check_mixed_set(answer, sql_results)
        if p:
            problems.append(p)
    return problems
