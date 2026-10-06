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
  identifier a list must carry comes from the routed card's `identifier_column` or from
  column-name shape alone (`*_number`, `*_id`, `*_tag`; `*_name` can be asked for but never
  counts as satisfying, see `NAMEABLE_SUFFIXES`). The question-shape regexes are English
  shapes only; the aggregate test is SQL keywords only. Section 18 of the test scans this
  file for the names it must not contain.
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
ANY consulted card's identifier or ANY identifying-shaped column is present, so a writer that
picked a different identifier from the one a card declares is not re-queried for it; the
column it asks for is the best PRINTED candidate rather than whatever key a card happens to
declare; an unparseable SQL yields NO cards rather than all of them; and a wall-clock budget,
a repeat-issue guard and per-step outcome events were added. Fix round 2 gave the budget
its own setting (`SQL_LOOP_TIMEOUT`, default 60 s) and took `_name` out of what makes a
list nameable.

That last one is a DELIBERATE gap between the two prompts, and this docstring used to deny
it, so it is spelled out: `sql_tool.LIST_IDENTIFIER_RULE` offers `_name` to the writer as one
valid identifier, and `NAMEABLE_SUFFIXES` here does not accept it. A query that returns only
a name column therefore obeys the generation-time rule and is still re-queried. That is the
intended behaviour, not an oversight - the failure this loop exists for is precisely a list
of LABELS with no number, key or tag against them, and a name is a label (see
`NAMEABLE_SUFFIXES`, and `IDENTIFIER_RANK`, which will still ASK for a name when the table
prints nothing better). The cost of the gap is one wasted re-query on a table whose only
identifying column is a name; the cost of closing it the other way is the loop going silent
on the exact answer shape it was built to catch.

FIX WAVE 1 (the Task 5 diagnosis, 2026-09-23) is the first change made from MEASUREMENT
rather than from review: the loop ran over 164 real questions against the same 164 with it
off, every re-query was replayed offline, and the arithmetic came out at +4 cards against
−3 for 44 extra queries. So three firings that paid nothing were withdrawn and one that
was never reaching its questions was widened:

* `NARROW_SELECT` keeps its detection and loses its instruction — a finding, like
  `TRUNCATED_NO_SHAPE`. Three firings, no wins, one loss.
* `IDENTIFIER_MISSING` is silent on a ONE-ROW result. Three of twelve firings, no wins.
* `EMPTY` is a finding, not a re-query, when the query read none of the routed tables:
  that writer abstained rather than mis-filtered. Five firings, no wins, one loss.
* `_COUNT_RE` gained the plurals, which is the only thing here that makes the loop do
  MORE: the cross-check is the part that paid (+3 / −1).

Each removal takes its firings with it, so the loop also gets cheaper.

FIX WAVE 2 (2026-09-28) — an empty result whose QUESTION already names the row. The generic
EMPTY instruction is advice: filter by a printed name, and prefer the table that is one row
per place and item. That advice sent a writer whose first query matched nothing away from the
one routed table that carried the answer, and the answer then said there was no record of it.
But the question had printed the entity's own coded identifier, and a routed card DECLARES
both the column such an identifier lives in (`identifier_column`) and the strings one starts
with (`identifier_prefixes`). Those two together are an ADDRESS, not advice, so when they
match the instruction becomes the address: the entity's own row, every column, nothing else.
The KIND stays `EMPTY`, so the priority order and the repeat guard are exactly as they were,
and only the `detail` (`IDENTIFIER_EMPTY`) says which of the two EMPTY instructions fired.
Replayed against the real cards before it shipped, and the replay changed the rule. Four of
the seven questions it fires on reached a table whose NAME marks it as an earlier state of the
building, because the caller hands these cards over in name order and the router's own ranking
does not survive the trip. Reordering that is a different module's decision and a different
measurement, so the rule instead OFFERS every routed card that can address the same printed
identifier, in the order it was handed them, and asks the writer to choose by subject: which
table holds what the question asks about, and which one its own name says is superseded. That
is the one part of this that a model is better placed to decide than code, and it is the only
part delegated. With exactly one match the wording is unchanged - there is nothing to choose
between.

Still generic: the prefixes and the column are read off the cards, and nothing about any
building is written here.

SPEC-FIX7 PART (c) (2026-10-01): THE GENERIC EMPTY INSTRUCTION CARRIES THE REAL VALUES. Measured
on the goal-function run of that day: where a first query filtered a text column with a value
the column does not hold, written in a vocabulary the table does not use, the generic advice
gave the writer nothing new to read - it guessed a second code, matched nothing again, and
the question fell to the documents. The values were in the loaded table all along. So the
instruction now lists them, for every column the failed WHERE compared with a plain string
literal: all of them when a column holds at most `VALUE_LIST_MAX`, otherwise the count and
the values that contain the filter's words. They come through an injected
`column_values(table, column)` (the app hands in `sql_tool.column_value_reader`), so this
module still needs no database to test, and they are read only when the re-query that carries
them can still be sent. Two hyphenated words left the advice in the same change, because each
re-query is ROUTED on its own text and the router reads the head of a hyphenated word as a
coded identifier prefix.

TASK T7 (2026-10-01): CONTACT- AND WARRANTY-SHAPED QUESTIONS GET THE COUNT CROSS-CHECK'S OWN
TREATMENT. A non-empty result answers "who do we contact, and what is their number" from
whichever table a router happened to pick, and a lifespan-plus-purchase-date column answers
"is it still under warranty" with an invented verdict — both because a non-empty result
triggers no fallback and neither shape is a count, so the one cross-check this loop already
had never ran for them. `LETTER_CROSSCHECK` is the same finding as `COUNT_CROSSCHECK` under a
different name and heading: one retrieval call, appended never substituted, raised only when
the result is non-empty and `COUNT_CROSSCHECK` did not already fire (T7-R1 — a question is
never both). `APPENDED_CROSSCHECKS` names the two kinds together so a caller tests one tuple
rather than keeping two names in step with this module.

SPEC-FIX3 PART (c) (2026-10-01): AN EMPTY RESULT THAT FILTERED A STOREY IS RE-ASKED THROUGH ITS
LEVEL CODE. Measured on the goal-function run of 2026-09-30: a floor filtered on printed floor
text came back empty, and the generic advice - filter by the printed name with ILIKE - sent the
writer back to printed text: it guessed another spelling, or WIDENED the filter to other codes
until something matched, and stated that figure. The data side now stamps a level-code column
beside every location key, so when the failed WHERE filtered a storey - by printed text on a
table that has a level code, or on the level code itself (`_storey_filters`) - the instruction
says to filter that storey on the level code (the table's own, or the places table's through
the location key), to keep it to the storey the question names, and never to widen it. The
printed storey values are not listed; the other filtered columns' values are. A place named by
its printed NAME keeps the generic advice (T11a-R1), and so does a table with no level code to
re-express through: there the instruction could not be obeyed. The kind stays `EMPTY`, and only
the `detail` (`PLACE_KEYS_EMPTY`) says which EMPTY instruction fired. The stamped level code is
on no card, deliberately, so routing cannot move (T11a-R6): a filtered column is placed by the
cards first and then by the LOADED table's columns, which the reader can list through its
optional `columns(table)`.

WAVE 3, F2 (2026-10-03): A VALUE LOOKED FOR IN A COLUMN THAT NEVER HOLDS IT, INSIDE A RESULT WITH
ROWS. Measured on the goal-function run of 2026-10-01: one arm of a set operation compared a code
with a column of a table where that code never appears, though another column of the same table
holds it; the other arm's row filled the result, so EMPTY never fired, and the answer called that
row's figure the one asked for. `sql_tool` reads this on the loaded tables and writes it as a
line starting `LITERAL_ELSEWHERE_LEAD`; `inspect_result` raises `LITERAL_ELSEWHERE` on it whatever
the rows (never on a query that did not run), and the re-query quotes the line's sentence: where
the value really is. With no step left, nothing is added or taken away - the line is already in
the result the answer writer reads. Review fix round 1 (ruling W3A1-R1): the empty part may be
the honest answer, so the line and the re-query offer both readings - query where the literal is
held if the question meant that relation, keep the empty part if it meant the empty one - and
steer to neither.

WAVE 3, G6 (2026-10-03): A RANKING OF PLACES GROUPED ON PRINTED PLACE TEXT IS RE-QUERIED THROUGH
THE LOCATION KEY. Measured on the goal-function runs of 2026-09-30 and 2026-10-01: a ranking
grouped a printed place column, so one place printed several ways was split and a word covering
a whole storey ranked first. `PLACE_RANKED_ON_TEXT` (see `_place_ranking_issue`) makes the
writer's own rule enforceable: rank by the location key, kept to the kind of place the grouped
column names, or by the level code for a storey. Every name in the instruction is read off the
query or the loaded table through the reader, so it is raised only when a re-query can be sent.

WAVE 4, A2 (2026-10-03): PLACES COMPARED ON PRINTED PLACE TEXT ARE RE-QUERIED THROUGH THE LOCATION
KEY. Measured on the goal-function run of that day: "which places have one thing but not another?"
was written as `c NOT IN (SELECT c ...)` on a printed place column, so a place printed two ways was
listed as lacking what it holds, beside entries covering a whole storey. `PLACE_COMPARED_ON_TEXT`
(see `_place_comparison_issue`) is G6 generalised from rankings to `[NOT] IN` a sub-SELECT of the
same column, EXCEPT and INTERSECT: compare by the location key, kept to the kind of place the
compared column names. Same reader, same names read off the query and the loaded table.
"""
from __future__ import annotations

import logging
import os
import re
import time
from collections import namedtuple

logger = logging.getLogger(__name__)

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

FAILED_SQL = "FAILED_SQL"
EMPTY = "EMPTY"
IDENTIFIER_MISSING = "IDENTIFIER_MISSING"
NARROW_SELECT = "NARROW_SELECT"
TRUNCATED_NO_SHAPE = "TRUNCATED_NO_SHAPE"
COUNT_CROSSCHECK = "COUNT_CROSSCHECK"
#: T7 — the same one-call cross-check as COUNT_CROSSCHECK, for a contact- or
#: warranty-shaped question instead of a quantity one. See `is_letter_shaped`.
LETTER_CROSSCHECK = "LETTER_CROSSCHECK"
#: Wave 3, F2 — a WHERE compared a value with a column of a table that never holds it though
#: another column of that table does. Raised even on a NON-EMPTY result, because an empty arm
#: of a set operation, or the unmatched side of an outer join, hides inside a result that has
#: rows. See `literal_elsewhere_sentences`.
LITERAL_ELSEWHERE = "LITERAL_ELSEWHERE"
#: Wave 3, G6 — a ranking of places grouped on the place as each source printed it, on a table
#: that carries a location key and its resolution column. See `_place_ranking_issue`.
PLACE_RANKED_ON_TEXT = "PLACE_RANKED_ON_TEXT"
#: Wave 4, A2 — places compared or combined (`[NOT] IN` a sub-SELECT of the same column, EXCEPT,
#: INTERSECT) on the place as each source printed it: G6 generalised from rankings to
#: comparisons. See `_place_comparison_issue`.
PLACE_COMPARED_ON_TEXT = "PLACE_COMPARED_ON_TEXT"
#: Wave 5, G13 (a) — the result reads a pre-takeover/historical table on a plain present-tense
#: question, though its current-state twin was ALSO routed and so could be read instead. See
#: `_twin_name` / `_asks_about_existing_state`.
CURRENT_TWIN = "CURRENT_TWIN"

#: Priority order, spec §3. The loop acts on the first of these that carries an
#: instruction; the last three never carry one. Pinned by test §21 — a fixture that raises
#: two issues at once, so inverting this tuple turns a check red. `FAILED_SQL` leads it:
#: a query that did not run at all has nothing for the other checks to read.
#: `LITERAL_ELSEWHERE` (wave 3, F2) comes right after it, AHEAD of `EMPTY` (wave 6, W6-A1,
#: reordered — see below): a filter that looked in the wrong column means rows are missing,
#: which outranks a list missing its labels, and, once an EMPTY result's own text can carry
#: the line too, it is also the more specific of the two things an empty result can say —
#: it names the exact column where the value IS, which outranks both of EMPTY's own
#: instructions (the identifier address, the generic advice).
#: `CURRENT_TWIN` (wave 5, G13 a) follows it: reading the wrong ERA of table entirely is a more
#: fundamental defect than how a right-era result is grouped or compared, so it outranks both
#: `PLACE_RANKED_ON_TEXT` and `PLACE_COMPARED_ON_TEXT`, which come right after it as before.
#: `PLACE_RANKED_ON_TEXT` (wave 3, G6) is raised only on a grouped result, which the
#: column-shaped issues after it never are, and it carries the instruction the count
#: cross-check does not. `PLACE_COMPARED_ON_TEXT` (wave 4, A2), its generalisation, comes right
#: after it: a comparison that split one place by its spellings has the wrong rows, which
#: outranks a list missing its labels.
#: `EMPTY` moves to right before the column-shaped issues (wave 6, W6-A1). MEASURED on the
#: goal-function run of 2026-09-30: a then-vs-now question's first query compared a code with
#: the PARENT column of a table where it only ever appears as a CHILD, found no rows, and the
#: question's own printed identifier ALSO addressed a routed card — so the loop sent the (by
#: construction wrong, for a question needing two states) identifier address, when the data
#: already knew exactly where the value was. `CURRENT_TWIN`, `PLACE_RANKED_ON_TEXT` and
#: `PLACE_COMPARED_ON_TEXT` are all raised only on a NON-empty result by construction (each is
#: guarded by `not empty` in `inspect_result`), so moving EMPTY past them changes nothing for
#: any of the three — it matters only for the new EMPTY+LITERAL_ELSEWHERE overlap this wave
#: adds (`sql_tool` can now write the line on an empty result too; see
#: `_literal_elsewhere_lines`'s call site there).
ISSUE_ORDER = (FAILED_SQL, LITERAL_ELSEWHERE, CURRENT_TWIN, PLACE_RANKED_ON_TEXT,
               PLACE_COMPARED_ON_TEXT, EMPTY, IDENTIFIER_MISSING, NARROW_SELECT,
               TRUNCATED_NO_SHAPE,
               COUNT_CROSSCHECK, LETTER_CROSSCHECK)

#: The `detail` of an `EMPTY` issue whose instruction came from an identifier the question
#: itself printed, rather than from the generic advice. It is deliberately NOT a kind and is
#: deliberately NOT in `ISSUE_ORDER`: making it one would give the repeat guard two EMPTY
#: kinds to tell apart, and a question could then spend two steps failing to find the same
#: row. It names WHICH instruction fired, nothing more.
IDENTIFIER_EMPTY = "IDENTIFIER_EMPTY"

#: The `detail` of an `EMPTY` issue whose query filtered a storey, so the instruction asks for
#: that storey through its level code (spec-fix3 part c). A detail, like `IDENTIFIER_EMPTY`,
#: and never a kind, for the same reason: the repeat guard must see one EMPTY, not two.
PLACE_KEYS_EMPTY = "PLACE_KEYS_EMPTY"

TOOL_SQL = "query_structured_data"
TOOL_SEARCH = "search_documents"
FAILED = "FAILED"

# --------------------------------------------------------------------------- constants

#: A result this narrow is a candidate for NARROW_SELECT — and a table with more
#: non-citation columns than this has something to widen to.
MAX_NARROW_COLUMNS = 3

#: The opening of the text `sql_tool` returns when a query did not run, and how much of
#: what follows it goes into the next question. A binder error is one sentence; a refused
#: generation can carry a fragment of the query it refused, and that fragment is exactly
#: the shape the re-query must not repeat — so it is capped.
FAILURE_PREFIX = "SQL query failed"
FAILURE_ERROR_CHARS = 200

#: What makes a list nameable — a column a reader can ACT on: a printed number, a key or a
#: tag, or the identifier the card itself declares. A `_name` column is deliberately NOT
#: here, although the generation-time rule offers it as one option: a name is a LABEL, and
#: the failure this loop exists for is precisely a list of labels with no numbers against
#: them. Shape, not vocabulary: no actual column is named here.
NAMEABLE_SUFFIXES = ("_number", "_id", "_tag")

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

#: The heading the cross-check excerpts ride under. It says MENTION, and it says the
#: matches may be unrelated, because that is what they are: the top hits of an
#: unfiltered keyword search, which need not print a quantity at all. Fix round 1 of the
#: Task 4 review (I-2) replaced "Other records that state this quantity:", which promised
#: the writer something retrieval cannot deliver — and a writer that believes a heading
#: will quote an unrelated number as a rival count. The answer-side rule that goes with it
#: lives in `openai_client.OUTPUT_FORMAT_RULES`: the TABLE figure is the answer, these are
#: records to name beside it.
CROSSCHECK_HEADING = ("Cross-check: document excerpts that mention this quantity "
                      "(top matches, may be unrelated)")

#: T7 — the heading a contact- or warranty-shaped question's cross-check rides under,
#: worded with the same deliberate caution as `CROSSCHECK_HEADING`: MAY NAME, not DOES
#: NAME, because these excerpts are still the top hits of an unfiltered keyword search and
#: need not settle the question at all.
LETTER_CROSSCHECK_HEADING = ("Cross-check: document excerpts that may name this party, "
                             "contact or warranty (top matches, may be unrelated)")

#: The issue kinds whose cross-check is APPENDED to the table result rather than
#: replacing it — never reported as a fallback ("SQL failed"/"no rows"). Exported so a
#: caller (`openai_client`) tests membership in one tuple instead of keeping two names in
#: step with this module.
APPENDED_CROSSCHECKS = (COUNT_CROSSCHECK, LETTER_CROSSCHECK)

#: The terminal fallbacks, worded exactly as the caller worded them before this module
#: existed, so `max_steps=1` is byte-identical to that behaviour.
EMPTY_FALLBACK_PREFIX = ("The structured tables returned no rows for this question. "
                         "Document excerpts that may answer it instead:\n\n")
EMPTY_FALLBACK_SUFFIX = ("\n\n(If the excerpts do not contain the answer either, say the "
                         "information was not found — do not guess.)")

FIRST_STEP_DETAIL = "Querying the structured tables"

#: The caps on the values an EMPTY instruction lists (spec-fix7 part c). A filtered column
#: holding at most `VALUE_LIST_MAX` values is listed in full; a longer one gets its count and
#: at most `VALUE_LIST_MAX` of the values that contain the filter's words, those holding the
#: most of them first. A listed value is cut to `VALUE_MAX_CHARS` characters, `CUT_MARK`
#: included, and a value holding a line break is cut at the break. At most
#: `VALUE_COLUMNS_MAX` columns are listed, the first ones the WHERE filters - so whatever the
#: table, the listing stays under about 4 x 40 x 80 characters, and in practice far under.
VALUE_LIST_MAX = 40
VALUE_MAX_CHARS = 80
VALUE_COLUMNS_MAX = 4
CUT_MARK = "…"

#: Where an EMPTY instruction's values clause starts, and the marker `step_instruction_suffix`
#: writes after every instruction. Named rather than repeated because `sql_tool` cuts that
#: clause out of the text it ROUTES on (review fix 1 of spec-fix7 part c): the values are the
#: data's own words and codes, and when routed on they pulled in tables that merely print the
#: same words - the mean re-query route measured 12.7 tables without them, 16.9 with. The SQL
#: writer still reads the clause; only the router never does. The two files share these two
#: objects, so where the clause starts and ends cannot drift between them.
VALUES_LEAD = "The real values of the columns it filtered:"
PREVIOUS_SQL_MARKER = " Previous SQL:"

#: The opening of each line `sql_tool` adds to a result for a value its WHERE compared with a
#: column of a table that never holds it, though another column of that table does (wave 3,
#: F2): "LITERAL ELSEWHERE - '<lit>' never appears in <table>.<column>, though it does in
#: <table>.<column> (N entries). If the question meant the relation <that column> holds, query
#: it; if it meant the relation <the first> holds, that relation is genuinely empty ...".
#: `sql_tool` writes the line and this module reads it back, so the two share this one object
#: and where the line starts cannot drift between them.
LITERAL_ELSEWHERE_LEAD = "LITERAL ELSEWHERE - "

#: The investigation's own wall-clock budget, in seconds, from `SQL_LOOP_TIMEOUT`. It is
#: NOT `SQL_QUERY_TIMEOUT`: that one caps a single DuckDB execution (5 s) and always did,
#: whereas a loop step is dominated by the SQL-generation call, which on its own routinely
#: outlives 5 s — spending the per-execution cap here would quietly make the loop one-shot.
SQL_LOOP_TIMEOUT_ENV = "SQL_LOOP_TIMEOUT"
DEFAULT_BUDGET_SECONDS = 60.0

# --------------------------------------------------------------------------- question shapes

_LIST_RE = re.compile(r"\b(list|lists|listing)\b|\bwhat are\b|\bwhich\b|\ball the\b"
                      r"|\bshow me\b|\bgive me the\b", re.IGNORECASE)
_DETAILS_RE = re.compile(r"\bspecs?\b|\bspecification|\bdetails?\b|\battributes?\b"
                         r"|\bparameters?\b|\bbreakdown\b|\beverything about\b", re.IGNORECASE)
#: Fix wave 1: the plurals were missing, and the two cards written for the cross-check
#: were both asked in them ("what counts…", "what different totals…"), so neither ever got
#: one. NOT added, deliberately: `numbers` (this corpus asks for serials and part numbers
#: that way, and a cross-check on those buys a retrieval call and no rival quantity) and
#: `how much` (a rating or cost question, not a count).
_COUNT_RE = re.compile(r"\bhow many\b|\btotals?\b|\bnumber of\b|\bcounts?\b", re.IGNORECASE)

#: T7 — contact-shaped: the deciding fact for "who do we contact" / "what is their phone
#: number" commonly lives in a letter or a manual's contacts page, which a non-empty SQL
#: result gives no reason to ever search. Word-boundary matched, so "whole" is not "who"
#: and "phoneme" is not "phone".
_CONTACT_RE = re.compile(r"\bwho\b|\bcontacts?\b|\bphone\b|\btelephone\b|\be-?mail\b"
                         r"|\bmobile\b", re.IGNORECASE)
#: T7 — warranty-shaped: the deciding fact for "is it still under warranty" commonly lives
#: in a warranty letter or certificate, never in a lifespan or purchase-date column — see
#: `openai_client.OUTPUT_FORMAT_RULES`, which forbids treating one as the other.
_WARRANTY_RE = re.compile(r"\bwarrant(?:y|ies)\b|\bguarantee[sd]?\b|\bexpir(?:e|es|ed|y)\b"
                          r"|\bstill covered\b", re.IGNORECASE)


def asks_to_list(question: str) -> bool:
    """The question asks for a set of things, so each thing must be nameable."""
    return bool(_LIST_RE.search(question or ""))


def asks_for_details(question: str) -> bool:
    """The question asks what a thing IS, so a two-column answer is an under-answer."""
    return bool(_DETAILS_RE.search(question or ""))


def asks_count(question: str) -> bool:
    """The question asks for a quantity, which in this corpus often has rivals."""
    return bool(_COUNT_RE.search(question or ""))


def asks_contact(question: str) -> bool:
    """The question asks who to contact, or for a phone/telephone/e-mail/mobile number."""
    return bool(_CONTACT_RE.search(question or ""))


def asks_warranty(question: str) -> bool:
    """The question asks about a warranty, guarantee or expiry."""
    return bool(_WARRANTY_RE.search(question or ""))


#: Wave 5, G13 (a) — the five words that mark a question as meaning the PRE-TAKEOVER state on
#: purpose, so reading the historical table is then correct and CURRENT_TWIN stays silent. The
#: same five words `sql_tool`'s existing-table note already asks the SQL writer to look for,
#: read here too so the loop can tell a plain present-tense question apart from one that means
#: the historical state deliberately. Word-bounded, so "beforehand" is not "before".
_EXISTING_MARKER_RE = re.compile(
    r"\bexisting\b|\bbefore\b|\bpre-takeover\b|\boriginal\b|\bhistorical\b", re.IGNORECASE)


def _asks_about_existing_state(question: str) -> bool:
    """The question itself says it means the pre-takeover/historical state on purpose."""
    return bool(_EXISTING_MARKER_RE.search(question or ""))


def is_letter_shaped(question: str) -> bool:
    """Contact-shaped or warranty-shaped (T7): the deciding fact commonly lives in a letter
    or a manual page rather than a table row, so a non-empty SQL result still earns one
    document cross-check — the same treatment `asks_count` earns a quantity question.
    T7-R1: `inspect_result` checks `asks_count` first, so this is never acted on for a
    question that is also count-shaped."""
    return asks_contact(question) or asks_warranty(question)


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
    """The query did not run at all — a binder/parse error, or a generation the executor
    refused before running it. The executor's own single repair attempt has already been
    spent by the time this is true."""
    return bool(result_text) and result_text.startswith(FAILURE_PREFIX)


def result_failure_error(result_text: str) -> str:
    """What the executor said went wrong, capped at `FAILURE_ERROR_CHARS`. The query text
    that follows it is dropped: the instruction already carries the previous SQL, and a
    degenerate query quoted twice is twice the chance of it being copied."""
    text = str(result_text or "")
    if text.startswith(FAILURE_PREFIX):
        text = text[len(FAILURE_PREFIX):].lstrip(": ").lstrip()
    head = re.split(r"\n\n(?:Generated )?SQL: ", text)[0].strip()
    return head[:FAILURE_ERROR_CHARS]


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


#: What a printed identifier looks like inside a question. The token shape is a DELIBERATE
#: duplicate of `table_router`'s (letters, digits and parentheses, joined by hyphens), because
#: the prefixes matched against it are the router's own `identifier_prefixes` — a tag it
#: scored a card on must be a tag this finds. It is copied rather than imported for the same
#: reason `result_is_empty` is: reaching into another module's private tokeniser couples the
#: inspector to the router's internals, and the test pins the two to agree.
_TAG_TOKEN_RE = re.compile(r"[A-Za-z0-9()][A-Za-z0-9()\-]*")

#: A token is CODED when it carries a hyphen or a digit. Ordinary English words carry
#: neither, which is the whole discriminator: `CAM-4F-B-01` is an identifier, `units` is not.
_CODED_TOKEN_RE = re.compile(r"[-0-9]")


def question_identifiers(question: str) -> list:
    """Every coded token the question prints, in the order printed, as `(prefix, token)` —
    the token itself and the part before its first hyphen, upper-cased for matching."""
    found = []
    for tok in _TAG_TOKEN_RE.findall(question or ""):
        if _CODED_TOKEN_RE.search(tok):
            found.append((tok.split("-", 1)[0].upper(), tok))
    return found


def card_identifier_matches(question, routed_cards):
    """`[(tag, table, column), …]` — every ROUTED card that can address the SAME printed
    identifier, in the order the cards were handed over; `[]` when none can.

    ROUTED ORDER is preserved and never re-sorted. A second rule applied on top (longest
    prefix, widest table, most columns) would make the offer depend on two orderings instead
    of one, and neither of them would be visible in the instruction the writer is handed.

    ALL of them, not the first. The first card is not reliably the right one: the caller
    hands these over in name order, so a table whose name marks it as an EARLIER state of
    the same thing can sort ahead of the current one, and picking it silently would answer a
    question about today out of a superseded record. Which table holds the subject the
    question asks about is a reading judgement, not an arithmetic one — so the instruction
    lists the addresses and the writer picks, which is the only judgement this module
    delegates.

    ONE identifier, not several, and it is THE QUESTION'S. Every SELECT offered filters the
    same printed value, and that value is the first coded token in the QUESTION TEXT that any
    routed card can address — token position, never card position. The two come apart on a
    question naming two entities: the caller hands these cards over in table-name order, so a
    card that can only address the SECOND-named entity can arrive first, and keying the offer
    to card order would then answer about the entity the question mentions second (found in
    review, 2026-09-28). Card order still decides the ORDER of the offers; it does not decide
    which entity is offered.

    A first coded token that no card declares is passed over rather than fatal — the next one
    the cards can address wins.

    A card that declares prefixes but no identifier column is skipped: there is no column to
    address the row by, so there is no instruction to write.
    """
    coded = question_identifiers(question)
    if not coded:
        return []
    addressable = []
    for card in routed_cards or []:
        table = (card or {}).get("table") or ""
        column = (card or {}).get("identifier_column") or ""
        if not table or not column:
            continue
        prefixes = {str(p).strip().upper()
                    for p in ((card or {}).get("identifier_prefixes") or []) if str(p).strip()}
        if prefixes:
            addressable.append((prefixes, str(table), str(column)))
    # The question's order is the outer loop; the cards' order is the inner one. That is the
    # whole fix, and it is why these two loops are this way round and not the other.
    for prefix, tag in coded:
        offers = [(tag, table, column) for prefixes, table, column in addressable
                  if prefix in prefixes]
        if offers:
            return offers
    return []


def quote_literal(value: str) -> str:
    """A printed identifier goes into the instruction as a SQL string literal, so a single
    quote inside it is doubled — the one character that would otherwise close the literal
    early and turn a deterministic instruction into a query that cannot even be parsed."""
    return "'" + str(value).replace("'", "''") + "'"


def quote_identifier(name: str) -> str:
    """A column name goes into the instruction as SQL the writer can paste. A plain name
    needs nothing; one carrying a space or punctuation needs double quotes, because
    backticks are not identifier quoting in this dialect and the re-query would simply
    fail — landing the question in the document fallback for no reason."""
    text = str(name)
    return text if re.fullmatch(r"[A-Za-z0-9_]+", text) else f'"{text}"'


# --------------------------------------------------------------------------- filtered values

#: One SQL token. A string literal and a quoted identifier are read WHOLE, so a keyword, a
#: parenthesis or a doubled quote inside one can never be taken for syntax. `stray` is a
#: quote that no closing quote ends: a truncated or malformed query, whose WHERE is not
#: guessed at.
_SQL_TOKEN_RE = re.compile(
    r"(?P<space>\s+)"
    r"|(?P<str>'(?:[^']|'')*')"
    r"|(?P<qid>\"(?:[^\"]|\"\")*\")"
    r"|(?P<word>[A-Za-z_][A-Za-z0-9_$]*)"
    r"|(?P<num>\d+(?:\.\d*)?|\.\d+)"
    r"|(?P<stray>['\"])"
    r"|(?P<op><>|!=|<=|>=|\|\||::|.)",
    re.DOTALL)

#: Words that are SQL - never a column, a table or an alias.
_SQL_KEYWORDS = frozenset("""
    ALL AND ANTI ANY AS ASC ASOF BETWEEN BY CASE COLLATE CROSS DESC DISTINCT ELSE END ESCAPE
    EXCEPT EXISTS FALSE FROM FULL GLOB GROUP HAVING ILIKE IN INNER INTERSECT IS JOIN LATERAL
    LEFT LIKE LIMIT NATURAL NOT NULL NULLS OFFSET ON OR ORDER OUTER POSITIONAL QUALIFY RIGHT
    SELECT SEMI SIMILAR SOME THEN TRUE UNION USING WHEN WHERE WINDOW WITH
""".split())

#: The clauses that end a WHERE clause of the same query.
_CLAUSE_ENDS = frozenset(("GROUP", "ORDER", "HAVING", "LIMIT", "OFFSET", "QUALIFY", "WINDOW",
                          "UNION", "INTERSECT", "EXCEPT"))


def sql_token_spans(sql: str):
    """`[(kind, text, value, start, end), …]` for `sql` - `value` being a literal's or a
    quoted name's content with its doubled quotes undone, and `start`/`end` where the token
    stands in `sql` - or None when a quote opens and never closes.

    Public because `sql_tool` reads a query's shape with it too (spec-fix1, 2026-10-01): it
    cuts a query's FROM ... WHERE out of the SQL verbatim, by position, and one tokenizer
    for both modules means a keyword inside a string literal or a quoted name is never
    taken for syntax by either."""
    tokens = []
    for m in _SQL_TOKEN_RE.finditer(sql or ""):
        kind, text = m.lastgroup, m.group()
        if kind == "space":
            continue
        if kind == "stray":
            return None
        if kind == "str":
            value = text[1:-1].replace("''", "'")
        elif kind == "qid":
            value = text[1:-1].replace('""', '"')
        else:
            value = text
        tokens.append((kind, text, value, m.start(), m.end()))
    return tokens


def _sql_tokens(sql: str):
    """`[(kind, text, value), …]` for `sql` - `sql_token_spans` without the positions - or
    None when a quote opens and never closes."""
    spans = sql_token_spans(sql)
    return None if spans is None else [token[:3] for token in spans]


def _keyword(tok) -> str:
    """The upper-cased word a bare-word token spells, "" for any other token."""
    return tok[2].upper() if tok and tok[0] == "word" else ""


def _is_name(tok) -> bool:
    """A token that can name a column, a table or an alias: a quoted identifier, or a bare
    word that is not SQL."""
    return bool(tok) and (tok[0] == "qid"
                          or (tok[0] == "word" and _keyword(tok) not in _SQL_KEYWORDS))


def _table_ref(tokens, j):
    """`(table, alias or None, next index)` for a FROM or JOIN target at `tokens[j]` -
    `"t"`, `"t" AS a`, `"t" a` or `schema."t"` - or None when no table is named there (a
    subquery, say)."""
    if j >= len(tokens) or not _is_name(tokens[j]):
        return None
    table, j = tokens[j][2], j + 1
    if j + 1 < len(tokens) and tokens[j][1] == "." and _is_name(tokens[j + 1]):
        table, j = tokens[j + 1][2], j + 2
    if j < len(tokens) and _keyword(tokens[j]) == "AS":
        j += 1
    alias = None
    if j < len(tokens) and _is_name(tokens[j]):
        alias, j = tokens[j][2], j + 1
    return table, alias, j


def _declare_tables(tokens, j, tables, aliases) -> None:
    """Record what a FROM or JOIN at `tokens[j]` names - and, after a comma, the next table
    too - in the enclosing query's `tables`, and each name and alias in `aliases`."""
    for _ in range(len(tokens)):
        ref = _table_ref(tokens, j)
        if ref is None:
            return
        table, alias, j = ref
        if all(t.lower() != table.lower() for t in tables):
            tables.append(table)
        aliases[table.lower()] = table
        if alias:
            aliases[alias.lower()] = table
        if j >= len(tokens) or tokens[j][1] != ",":
            return
        j += 1


def _opens_condition(tok) -> bool:
    """A condition can begin right after WHERE, AND, OR or an opening parenthesis."""
    return bool(tok) and (tok[1] == "(" or _keyword(tok) in ("WHERE", "AND", "OR"))


def _ends_condition(tokens, k) -> bool:
    """The condition ends at `tokens[k]`: nothing follows, or AND, OR, a closing parenthesis
    or the query's next clause does. A literal that goes on (`'%' || x`, `'a' ESCAPE …`) is
    part of an expression, not a plain filter."""
    if k >= len(tokens):
        return True
    word = _keyword(tokens[k])
    return tokens[k][1] == ")" or word in ("AND", "OR") or word in _CLAUSE_ENDS


def _literal_condition(tokens, i, ops=None):
    """`(qualifier or None, column, [literal, …])` when `tokens[i:]` open one of the three
    plain literal filters - `col = 'x'`, `col ILIKE 'x'` (or LIKE), `col IN ('x', …)` - and
    the filter is the whole condition; None for anything else. `ops`, when given, keeps only
    the filters whose operator ('=', 'LIKE', 'ILIKE' or 'IN') it names (wave 3, F2)."""
    n = len(tokens)
    if not _is_name(tokens[i]):
        return None
    qualifier, column, j = None, tokens[i][2], i + 1
    if j + 1 < n and tokens[j][1] == "." and _is_name(tokens[j + 1]):
        qualifier, column, j = column, tokens[j + 1][2], j + 2
    if j >= n:
        return None
    op = _keyword(tokens[j]) or tokens[j][1]
    if ops is not None and op not in ops:
        return None
    if op in ("=", "LIKE", "ILIKE"):
        if j + 1 < n and tokens[j + 1][0] == "str" and _ends_condition(tokens, j + 2):
            return qualifier, column, [tokens[j + 1][2]]
        return None
    if op != "IN" or j + 1 >= n or tokens[j + 1][1] != "(":
        return None
    literals = []
    for k in range(j + 2, n, 2):
        if tokens[k][0] != "str":
            return None
        literals.append(tokens[k][2])
        if k + 1 < n and tokens[k + 1][1] == ",":
            continue
        if k + 1 < n and tokens[k + 1][1] == ")" and _ends_condition(tokens, k + 2):
            return qualifier, column, literals
        return None
    return None


def _where_literals(sql: str, ops=None):
    """`(filters, aliases)` for `sql`. Each filter is `(qualifier or None, column,
    [literal, …], scopes)`, one per plain literal condition in a WHERE clause, in the order
    written; `scopes` are the tables of the queries enclosing it, innermost first, which is
    what places an unqualified column. `aliases` maps every table name and alias the query
    declares, lower-cased, to its table. `ops` keeps only the filters of those operators
    (`_literal_condition`); None keeps all three shapes.

    One pass, tracking parentheses: a parenthesis opens a nested scope that is still in the
    WHERE clause unless a SELECT follows it (a subquery, whose own WHERE comes later), and a
    closing one returns to the enclosing query. Conditions in a SELECT list, a JOIN's ON, a
    GROUP BY or a HAVING are not WHERE filters and are never read."""
    tokens = _sql_tokens(sql)
    if not tokens:
        return [], {}
    scopes = [{"tables": [], "where": False}]
    aliases = {}
    filters = []
    for i, tok in enumerate(tokens):
        word = _keyword(tok)
        if tok[1] == "(":
            scopes.append({"tables": [], "where": scopes[-1]["where"]})
        elif tok[1] == ")":
            if len(scopes) > 1:
                scopes.pop()
        elif word == "SELECT" or word in _CLAUSE_ENDS:
            scopes[-1]["where"] = False
        elif word == "WHERE":
            scopes[-1]["where"] = True
        elif word in ("FROM", "JOIN"):
            _declare_tables(tokens, i + 1, scopes[-1]["tables"], aliases)
        elif scopes[-1]["where"] and i and _opens_condition(tokens[i - 1]):
            found = _literal_condition(tokens, i, ops)
            if found:
                filters.append(found + ([s["tables"] for s in reversed(scopes)],))
    return filters, aliases


def filtered_literals(sql: str) -> list:
    """`[(qualifier or None, column, [literal, …]), …]` - every column the WHERE clauses of
    `sql` compare with a plain string literal, in the order first filtered, each once, with
    every literal it was compared to.

    Three shapes are read, each standing as a whole condition: `col = 'x'`, `col ILIKE 'x'`
    (or LIKE) and `col IN ('x', …)`. Nothing else is: a function around the column, a
    pattern built by concatenation, a negation, a comparison with a number, `IN (SELECT …)`
    - and no part of a query holding a quote that never closes."""
    merged = {}
    for qualifier, column, literals, _scopes in _where_literals(sql)[0]:
        key = ((qualifier or "").lower(), column.lower())
        entry = merged.setdefault(key, (qualifier, column, []))
        entry[2].extend(lit for lit in literals if lit not in entry[2])
    return list(merged.values())


def _column_lister(column_values):
    """`columns(table) -> frozenset of lower-cased column names | None` - the columns of a
    table AS LOADED, when the injected reader can say (its optional `columns` attribute,
    which `sql_tool.column_value_reader` provides), else None. Each table is asked for at most
    once per lister, and a lister that raises is one that answered nothing: like the values,
    the columns are an aid to a re-query and must never cost it (spec-fix3 part c, T11a-R6)."""
    columns = getattr(column_values, "columns", None)
    if not callable(columns):
        return None
    seen = {}

    def lister(table):
        key = str(table or "").lower()
        if key not in seen:
            try:
                got = columns(table)
            except Exception as e:  # noqa: BLE001 - see the docstring
                logger.warning(f"sql_loop: listing the columns of {table} failed "
                               f"({type(e).__name__}: {e}); placing by the cards alone")
                got = None
            seen[key] = None if got is None else frozenset(str(c).lower() for c in got)
        return seen[key]

    return lister


def _filtered_columns(sql: str, routed_cards, loaded_columns=None, ops=None) -> list:
    """`[(table, column, [literal, …]), …]` - each column the WHERE of `sql` filtered with a
    literal, placed in the table it belongs to, in the order first filtered, each once.

    A qualified column belongs to the table its qualifier names, by alias or by the table's
    own name. An unqualified one belongs to the NEAREST enclosing query's table whose routed
    card lists that column - the nearest, so a subquery's filter is never charged to an outer
    table that happens to share a column name - or, when no card in that query lists it, whose
    LOADED table has it (`loaded_columns`, a `_column_lister`): a column stamped onto the
    tables after the cards were written is on no card at all (T11a-R6). A column this cannot
    place is left out: the values of the wrong table would be worse than none. `ops` keeps only
    the filters of those operators (`_literal_condition`; wave 3, F2)."""
    filters, aliases = _where_literals(sql, ops)
    card_columns = {}
    for card in routed_cards or []:
        table = str((card or {}).get("table") or "").lower()
        if table:
            card_columns[table] = {str(c).lower() for c in (card or {}).get("columns") or []}
    placed = {}
    for qualifier, column, literals, scopes in filters:
        if qualifier:
            owner = aliases.get(qualifier.lower())
            owners = [owner] if owner else []
        else:
            owners = []
            for tables in scopes:
                owners = [t for t in tables
                          if column.lower() in card_columns.get(t.lower(), ())]
                if not owners and loaded_columns is not None:
                    owners = [t for t in tables
                              if column.lower() in (loaded_columns(t) or ())]
                if owners:
                    break
        for table in owners:
            entry = placed.setdefault((table.lower(), column.lower()), (table, column, []))
            entry[2].extend(lit for lit in literals if lit not in entry[2])
    return list(placed.values())


def _shown_value(value: str):
    """`(value as a SQL literal, whether it was cut)` - cut at its first line break, and to
    `VALUE_MAX_CHARS` characters with `CUT_MARK` as the last of them."""
    text = str(value)
    head = (text.splitlines() or [""])[0]
    if head == text and len(text) <= VALUE_MAX_CHARS:
        return quote_literal(text), False
    return quote_literal(head[:VALUE_MAX_CHARS - 1] + CUT_MARK), True


def _filter_words(literals) -> list:
    """The words a filter looked for: each literal split on spaces and on the `%` wildcard,
    each word once, in the order written."""
    words = []
    for literal in literals:
        for word in re.split(r"[\s%]+", str(literal)):
            if word and word.lower() not in (w.lower() for w in words):
                words.append(word)
    return words


def _column_values_clause(table: str, column: str, values, literals):
    """One column's clause of the listing, and whether any value shown in it was cut."""
    distinct = []
    for value in values:
        text = "" if value is None else str(value)
        if text.strip() and text not in distinct:
            distinct.append(text)
    name = '"' + str(table).replace('"', '""') + '"."' + str(column).replace('"', '""') + '"'
    total = len(distinct)
    if total == 0:
        return f"{name} holds no values", False
    if total <= VALUE_LIST_MAX:
        shown = [_shown_value(v) for v in distinct]
        counted = "exactly 1 value" if total == 1 else f"exactly these {total} values"
        return (f"{name} holds {counted}: " + ", ".join(s for s, _ in shown),
                any(cut for _, cut in shown))
    held = f"{name} holds {total} values"
    words = _filter_words(literals)
    if not words:
        return held, False
    named = " or ".join(quote_literal(w) for w in words)
    lowered = [w.lower() for w in words]
    ranked = []
    for index, value in enumerate(distinct):
        hits = sum(1 for w in lowered if w in value.lower())
        if hits:
            ranked.append((-hits, index, value))
    matches = [value for _, _, value in sorted(ranked)]
    if not matches:
        return f"{held}, and none contains {named}", False
    shown = [_shown_value(v) for v in matches[:VALUE_LIST_MAX]]
    listed = ", ".join(s for s, _ in shown)
    cut = any(c for _, c in shown)
    if len(matches) == 1:
        return f"{held}, and the 1 containing {named} is: {listed}", cut
    if len(matches) <= VALUE_LIST_MAX:
        return f"{held}, and the {len(matches)} containing {named} are: {listed}", cut
    return (f"{held}, and {len(matches)} contain {named}, the first {VALUE_LIST_MAX} "
            f"being: {listed}", cut)


def _filtered_values_text(sql: str, routed_cards, column_values, skip=(),
                          loaded_columns=None) -> str:
    """The sentences an EMPTY instruction appends to list the real values of the columns
    its query filtered, or "" when there is nothing to list - no reader, no plain literal
    filter, no column that can be placed, or a reader that answers nothing.

    The reader is asked for at most `VALUE_COLUMNS_MAX` columns, in the order they were
    filtered. A reader that raises is treated as one that answered nothing: the values are
    an aid to the re-query, and must never cost it. A column no card lists is placed by the
    loaded table's columns when the reader can list them (`loaded_columns`, built from the
    reader when not handed one - T11a-R6). `skip` holds lower-cased `(table, column)` pairs
    never listed: a storey's printed text, when the instruction says to filter it on its
    level code instead (spec-fix3 part c)."""
    if column_values is None:
        return ""
    if loaded_columns is None:
        loaded_columns = _column_lister(column_values)
    clauses, any_cut = [], False
    for table, column, literals in _filtered_columns(sql, routed_cards, loaded_columns):
        if (str(table).lower(), str(column).lower()) in skip:
            continue
        if len(clauses) >= VALUE_COLUMNS_MAX:
            break
        try:
            values = column_values(table, column)
        except Exception as e:  # noqa: BLE001 - see the docstring
            logger.warning(f"sql_loop: reading the values of {table}.{column} failed "
                           f"({type(e).__name__}: {e}); the re-query goes without them")
            values = None
        if values is None:
            continue
        clause, cut = _column_values_clause(table, column, values, literals)
        clauses.append(clause)
        any_cut = any_cut or cut
    if not clauses:
        return ""
    text = (VALUES_LEAD + " " + "; ".join(clauses) + ". "
            "Filter with the listed values that mean what the question asks for, copied "
            "exactly as listed; never substitute a different value for the question's term.")
    if any_cut:
        text += (f" A listed value that ends in {CUT_MARK} is longer than shown: match it on "
                 f"its start with ILIKE.")
    return text


# --------------------------------------------------------------------------- place keys

def is_level_code_column(name) -> bool:
    """A column whose NAME says it holds a level code - its words include both "level" and
    "code" - the key a storey is filtered on (spec-fix3, 2026-10-01). The schema block lists
    such a column's codes like any column's values, read off every row (spec-fix5, T9a-R0), so
    only the loop's storey rules read it. Shape only: no column is named."""
    words = set(_name_words(name))
    return "level" in words and "code" in words


def _name_words(name) -> list:
    """The lower-case words of a column's name, in order: `floor_as_printed` -> floor, as,
    printed."""
    return re.findall(r"[a-z]+", str(name or "").lower())


#: Words that, in a column's NAME, say it holds a storey - a floor, a level, a storey, a
#: basement - written the way its source printed it (spec-fix3 part c).
_STOREY_NAME_WORDS = frozenset(("floor", "floors", "level", "levels", "storey", "storeys",
                                "story", "stories", "basement", "basements"))
#: Words that, in a column's NAME, say it holds a place written as text - a name, a display
#: name, an area, a location, a zone. Such a column holds a storey only when the literal it
#: is compared with names one.
_PLACE_NAME_WORDS = frozenset(("name", "names", "display", "place", "places", "area", "areas",
                               "location", "locations", "zone", "zones"))
#: A printed column (`*_as_printed`) holds whatever its source printed: a storey only when the
#: literal names one, like a place-name column.
_PRINTED_NAME_WORDS = frozenset(("printed",))
#: Name parts that QUALIFY a column without changing what it holds - how it was printed, what
#: kind of label it is - so `storey_block_as_printed` is a storey column (fix round 1), and a
#: measure whose name merely holds a storey word among other words is not.
_QUALIFIER_NAME_WORDS = frozenset(("as", "printed", "print", "text", "code", "codes", "from",
                                   "tag", "block", "number", "no", "label", "raw"))
#: A literal that names a storey in words.
_STOREY_LITERAL_RE = re.compile(r"\b(?:floors?|levels?|storeys?|stor(?:y|ies)|basements?"
                                r"|ground|roofs?)\b", re.IGNORECASE)


def _storey_text(column, literals) -> bool:
    """True when filtering `column` with `literals` filters a storey by its PRINTED TEXT
    rather than by a level code. Shape only, by the WHOLE parts of the column's name:

    * a storey column - every part that is not a qualifier (`_QUALIFIER_NAME_WORDS`) is a
      floor, level, storey or basement word - with any literal (`floor = 'B%'`: a floor
      printed as a code is still printed text);
    * a place-name or printed column - including one that also carries a storey word, like a
      level's display name - only when the literal itself names a storey in words
      (`display = '%Level 2%'`), so a place named by its own printed name never counts;
    * never the level code itself, never an id, never a resolution enum (its values spell
      granularities, not places), and never a measure whose name merely holds a storey word
      among other words (fix round 1)."""
    words = _name_words(column)
    if (not words or words[-1] == "id" or "resolution" in words
            or is_level_code_column(column)):
        return False
    content = [w for w in words if w not in _QUALIFIER_NAME_WORDS]
    if content and all(w in _STOREY_NAME_WORDS for w in content):
        return True
    if not (_PLACE_NAME_WORDS.intersection(content) or _PRINTED_NAME_WORDS.intersection(words)):
        return False
    return any(_STOREY_LITERAL_RE.search(re.sub(r"[%_]", " ", str(lit))) for lit in literals)


#: The comparisons a WHERE can apply, by token: operators, and the words that compare.
_COMPARISON_OPS = frozenset(("=", "<>", "!=", "<", ">", "<=", ">="))
_COMPARISON_WORDS = frozenset(("LIKE", "ILIKE", "GLOB", "SIMILAR", "BETWEEN", "IS", "IN",
                               "EXISTS"))


def _where_comparisons(sql: str):
    """`(comparisons, carriers)` in the WHERE clauses of `sql`, read with `_where_literals`'
    scope rules: every comparison a WHERE applies (`_COMPARISON_OPS`, `_COMPARISON_WORDS`), and
    how many of them are an IN or EXISTS that only carries a sub-SELECT's own WHERE - the
    sub-SELECT's filters are counted where they stand."""
    tokens = _sql_tokens(sql)
    if not tokens:
        return 0, 0
    scopes = [False]
    comparisons = carriers = 0
    for i, tok in enumerate(tokens):
        word = _keyword(tok)
        if tok[1] == "(":
            scopes.append(scopes[-1])
        elif tok[1] == ")":
            if len(scopes) > 1:
                scopes.pop()
        elif word == "SELECT" or word in _CLAUSE_ENDS:
            scopes[-1] = False
        elif word == "WHERE":
            scopes[-1] = True
        elif scopes[-1] and (tok[1] in _COMPARISON_OPS or word in _COMPARISON_WORDS):
            comparisons += 1
            if (word in ("IN", "EXISTS") and i + 2 < len(tokens) and tokens[i + 1][1] == "("
                    and _keyword(tokens[i + 2]) == "SELECT"):
                carriers += 1
    return comparisons, carriers


def _storey_filters(sql: str, routed_cards, loaded_columns):
    """`(fires, printed, alone)` for the WHERE of an EMPTY result's `sql`: whether it filtered a
    storey that can be asked for again through a level code; the lower-cased
    `(table, column)` pairs of the storey filters written as printed text, whose values the
    instruction must not list; and whether those storey filters are ALL the query filtered on.

    A filter on a level-code column itself fires, wherever it stands. A filter on a storey's
    printed text (`_storey_text`) fires when the table it belongs to has a level-code column
    - on its routed card, or on the table as loaded (`loaded_columns`, T11a-R6) - because only
    then can the instruction be obeyed; on a table with no level code it stays today's EMPTY,
    printed values and all.

    `alone` (fix round 1): every comparison in the WHERE clauses is a storey filter - or the IN
    or EXISTS carrying one in a sub-SELECT. Only then is an empty storey the answer; with any
    other filter beside it, that filter may be what matched nothing."""
    filters, _ = _where_literals(sql)
    fires = any(is_level_code_column(column) for _, column, _, _ in filters)
    comparisons, carriers = _where_comparisons(sql)
    alone = (bool(filters)
             and all(is_level_code_column(column) or _storey_text(column, literals)
                     for _, column, literals, _ in filters)
             and comparisons == len(filters) + carriers)
    card_columns = {}
    for card in routed_cards or []:
        table = str((card or {}).get("table") or "").lower()
        if table:
            card_columns[table] = [str(c) for c in (card or {}).get("columns") or []]
    printed = set()
    for table, column, literals in _filtered_columns(sql, routed_cards, loaded_columns):
        if not _storey_text(column, literals):
            continue
        columns = list(card_columns.get(str(table).lower(), ()))
        if loaded_columns is not None:
            columns += list(loaded_columns(table) or ())
        if any(is_level_code_column(c) for c in columns):
            fires = True
            printed.add((str(table).lower(), str(column).lower()))
    return fires, printed, alone


# --------------------------------------------------------------------------- inspection

def _failed_sql_issue(error: str) -> Issue:
    """A query that did not run is the most re-queryable result there is: the engine has
    just said, in words, what was wrong with it.

    MEASURED 2026-09-28. A two-part question about one entity — what is this thing, and
    what is inside it — was written as a set operation over two tables of different widths,
    with the narrower arm padded out with NULLs until the generation cap stopped it. The
    engine refused it; the executor's one repair produced the same shape; and this loop
    then treated the failure as terminal and handed the question to the document index,
    which answered it from a single stray excerpt. Nothing about that chain was a reading
    failure. The instruction below is therefore specific about the shape that failed — one
    plain SELECT, one table, no set operations, no invented columns — and about what to do
    with the two-part question instead, which is to answer the specific part from the table
    that holds it and JOIN the entity's own row for its name and details.

    Generic, like every other instruction here: it describes SQL shapes and nothing of any
    building."""
    return Issue(
        FAILED_SQL,
        "The query failed — re-querying with one plain SELECT",
        (f"The previous SQL failed with: {error}. Write ONE plain SELECT over ONE table "
         f"that answers the main part of the question — no UNION, INTERSECT or EXCEPT, no "
         f"set operations, no invented columns; if the question has two parts about one "
         f"entity, answer the specific part (the list) from the table that holds it and "
         f"JOIN the entity's own row on the shared location key for its name and details."),
    )


def _empty_issue(sql: str, values_text: str = "", storey: bool = False,
                 alone: bool = True) -> Issue:
    """Empty, and the query read a routed table: it filtered on something the table does
    not hold in the form it was written.

    The advice is the one this issue has always given, with its two hyphenated words
    rewritten (spec-fix7 part c, 2026-10-01). Each re-query is ROUTED on its own text, and
    the router reads the head of every hyphenated word as a coded identifier prefix, so the
    old wording - "Re-read the column samples", "the one-row-per-place-and-item table" -
    asked every generic re-query for the tables whose identifiers start RE and ONE, and a
    table declaring RE took a ranked slot on most of them. Nothing in the instruction's own
    words may carry a hyphen now; the SQL it quotes and the values it lists are the data's
    own, and are what they are.

    `values_text`, when there is one, follows the advice: the real values of the columns the
    query filtered (`_filtered_values_text`). Advice cannot repair a filter written in a
    vocabulary the column does not use; the vocabulary can.

    `storey` (spec-fix3 part c): the query filtered a floor, level, storey or basement that
    can be asked for through a level code (`_storey_filters`). Then the printed-name advice
    is the wrong advice - it sends the writer back to printed text, where it guesses another
    spelling or widens the filter until something matches - so it is replaced: filter that
    storey on the level code, keep the filter, never widen it, and a storey with no rows is
    the answer. No word of it carries a hyphen, for the router's sake, like the advice, and
    its incidental words are ones no card scores ("wording", "further", "remaining", "no
    such rows"): the re-query is routed on this text, and "text", "other" and "none" each
    pulled tables into the route that the storey has nothing to do with.

    `alone` (fix round 1): only when the storey filters are ALL the query filtered on is an
    empty storey "the answer". With another filter beside it - a right level code and a wrong
    item word, say - the storey is still kept and never widened, but the writer is pointed at
    the remaining filters, whose real values follow, instead of being told it is done."""
    if storey:
        instruction = (f"The previous query `{sql}` returned no rows, and it filtered a floor, "
                       f"level, storey or basement. Filter that storey on a level code column "
                       f"(the table's own, or the places table's reached through the location "
                       f"key) with a code the column holds, never on printed floor wording or a "
                       f"place name; keep that filter to the storey the question names, and "
                       f"never widen it to further levels or codes")
        if alone:
            instruction += (": if no rows match it, that storey has no such rows, and that is "
                            "the answer.")
        else:
            instruction += (". The remaining filters may be what matched nothing: read the "
                            "column samples again for each of them.")
        if values_text:
            instruction += " " + values_text
        return Issue(EMPTY, PLACE_KEYS_EMPTY, instruction)
    instruction = (f"The previous query `{sql}` returned no rows. Read the column samples "
                   f"again; filter a place or entity by its printed NAME on the name column "
                   f"with ILIKE '%…%', never by a built id; if the question names a place, "
                   f"use the table with one row per place and item.")
    if values_text:
        instruction += " " + values_text
    return Issue(EMPTY, "No rows — re-querying by printed name", instruction)


def _empty_identifier_issue(matches) -> Issue:
    """Empty, and the question printed the identifier of a routed table — so the re-query is
    an ADDRESS rather than advice.

    MEASURED 2026-09-28. A question about one entity and its dependants was written as a
    self-join with an invented predicate, matched nothing, and the generic EMPTY instruction
    then sent the writer to a different table joined to the place index: one row, five
    columns, not one of them the columns asked about, and an answer that said no link was on
    record. The entity's own row in a routed table carried every one of them. The card
    declares the column its identifier lives in and the strings such an identifier starts
    with; the question printed one. That is enough to name the row by code, so this asks for
    exactly that row and forbids anything else — a narrower SELECT is how the first query
    lost the columns in the first place.

    When SEVERAL routed cards carry the same identifier, all of them are offered in routed
    order and the writer chooses by subject — see `card_identifier_matches` for why picking
    one here would be a guess about which STATE of the building a table records. With one
    match the wording is unchanged, because there is nothing to choose between.

    The literal is quote-doubled everywhere it appears, so there is only ever one written
    form of the tag for the writer to copy.

    Generic, like every other instruction here: the tables, the columns and the prefixes are
    all read off the routed cards."""
    tag = matches[0][0]
    literal = quote_literal(tag)
    if len(matches) == 1:
        _, table, column = matches[0]
        body = (f"which is the identifier of table \"{table}\" (column \"{column}\"). "
                f"Write exactly: SELECT * FROM \"{table}\" WHERE \"{column}\" = {literal}"
                f" — the entity's own row with every column — and nothing else.")
    else:
        named = ", ".join(f"\"{t}\" (column \"{c}\")" for _, t, c in matches)
        offered = " / ".join(f"SELECT * FROM \"{t}\" WHERE \"{c}\" = {literal}"
                             for _, t, c in matches)
        body = (f"which is the identifier of these tables: {named}. "
                f"Write exactly ONE of: {offered} — choose the table whose columns hold "
                f"what the question asks for; prefer a current-state table over one whose "
                f"name or description marks it as an earlier or superseded state. "
                f"Nothing else.")
    return Issue(
        EMPTY,
        IDENTIFIER_EMPTY,
        f"The previous query returned no rows. The question names the identifier "
        f"{literal}, {body}",
    )


def _empty_abstention(sql: str) -> Issue:
    """Empty, and the query read NONE of the routed tables — so the writer did not filter
    something too tightly, it declined to look anywhere. Fix wave 1: re-querying one of
    these fired five times in a 164-question measurement for no win and one loss, where a
    correct refusal came back as a stated figure. Telling an abstention to look again is
    asking it to become a claim, so this is a finding: reported, and straight to the
    documents, which is where a question the tables do not cover belongs."""
    return Issue(EMPTY, f"No rows, and the query `{sql}` read no routed table", "")


def _identifier_issue(column: str, table: str) -> Issue:
    return Issue(
        IDENTIFIER_MISSING,
        f"The list has no {column} column — re-querying",
        (f"The question asks to list entities; SELECT the identifier column "
         f"`{quote_identifier(column)}` of `{table}` in addition to the columns you "
         f"selected, same filter."),
    )


def _narrow_issue(table: str, shown: int, available: int) -> Issue:
    """A FINDING, not a re-query — its `instruction` is "" (fix wave 1).

    It fired three times in a 164-question measurement, for zero wins and one loss: on a
    result that was ALREADY the complete answer it named the subquery's table rather than
    the main FROM, and the widened re-query replaced a complete key/value answer with a
    narrower one. The loop keeps the LAST result and has no rule for keeping the better of
    two, so any re-query here is a bet that cannot be hedged. The detection stays — it
    still reaches `Investigation.issues` and the step events — and only the instruction is
    withdrawn. (It also used to reach the answer writer through the trailer; since
    spec-fix10 the trailer names no issue kind at all, see `investigation_trailer`.)"""
    return Issue(
        NARROW_SELECT,
        f"Only {shown} columns of {available} — noted, not re-queried",
        "",
    )


def literal_elsewhere_sentences(result_text: str) -> list:
    """The sentence of each LITERAL ELSEWHERE line a result carries (`sql_tool` writes them),
    in order, without its lead; [] when it carries none (wave 3, F2)."""
    return [line[len(LITERAL_ELSEWHERE_LEAD):].strip()
            for line in (result_text or "").splitlines()
            if line.startswith(LITERAL_ELSEWHERE_LEAD)]


def _literal_elsewhere_issue(sentences) -> Issue:
    """A value the query compared with a column that never holds it, though another column of
    the same table does (wave 3, F2).

    MEASURED on the goal-function run of 2026-10-01. One arm of a set operation compared a
    code with the PARENT column of a table where the code only ever appears as a child, and
    found nothing; the other arm's row filled the result, the result was not empty, so no check
    here fired - and the answer called the other arm's figure the one asked for. The second run
    hid the same miss on the unmatched side of an outer join. The data knew where the value
    was: `sql_tool` reads it on the loaded tables and writes each finding as a line, which this
    turns into the re-query, word for word. With no step left the line simply stays in the
    result, where the answer writer reads that the value was never found where it was looked
    for.

    BOTH READINGS, NEVER ONE (review fix round 1, ruling W3A1-R1). The empty part may be the
    honest answer: "what did it feed then, and what now?", asked of a thing that fed nothing
    then, is the same query and fires the same way. So the re-query does not send the writer to
    the holding column; it asks which relation the question means, and to query that one or keep
    the empty part. The line's own sentence offers the same two readings, for the answer writer
    too. When the writer keeps the empty part, the next result raises this issue again and the
    repeat guard ends the loop - one step spent, and the answer still told.

    The instruction's own words carry no hyphen and score on no router card, for the router's
    sake, like the EMPTY advice: each re-query is routed on its own text, and words such as
    'value', 'column', 'rows', 'table', 'other', 'result', 'part', 'record', 'one' and even 'so'
    and 'as' are words some card prints - measured, they pulled tables into a re-query's route.
    The sentences it quotes name tables, columns and a value, which are the data's own and are
    meant to be routed on."""
    return Issue(
        LITERAL_ELSEWHERE,
        "A literal was looked for where it never appears — re-querying",
        ("The previous query looked for a literal where it never appears: " + "; ".join(sentences)
         + ". Settle which relation the question means before writing the query again: compare "
         "the literal where that relation is held, or keep the empty answer where the question "
         "meant the empty relation; leave everything else unchanged."),
    )


# --------------------------------------------------------------------------- the current twin
#
# WAVE 5, G13 (a) (2026-10-04), designed in plan-wave4.md. MEASURED on the goal-function run of
# 2026-09-30: a present-tense question's step 1 read a pre-takeover/historical table (its own
# name carrying `_existing_`, doc-prep's tagging convention) because the question's words
# overlap it as well as its current-state twin; that query came back EMPTY, and the generic
# EMPTY re-query dropped the filter that had made it empty but never moved the writer OFF the
# historical table — so the final, non-empty result still answered from the before-state. No
# step of any recorded run ever read the current twin.
#
# `sql_tool.execute_sql_query` already warns the SQL writer, in its own generation prompt, when
# BOTH a historical table and its current twin are in play: use the current one unless the
# question says existing, before, pre-takeover, original or historical. That warning is advice
# the writer is free to ignore; this is the same gap IDENTIFIER_MISSING and the storey EMPTY
# issue already close elsewhere — a prompt rule made enforceable by a re-query, when code can
# tell the result is still wrong.
#
# So: when the result holds rows (never on an EMPTY result, which the EMPTY issue above already
# re-queries, and never on a FAILED one, which reads no table reliably), its SQL reads a table
# whose current-state twin (`_twin_name`) was ALSO routed — so the writer could read it instead
# — and the question itself never says it means the historical state on purpose
# (`_asks_about_existing_state`): one more re-query names both tables and asks for the current
# one. `_twin_name` duplicates `sql_tool._current_twin`'s own pairing rule rather than
# importing it — `sql_tool` already imports THIS module, so the reverse import would be
# circular — the same shape `result_is_empty` already uses for its own duplicate of
# `openai_client._sql_result_is_empty`; a test pins the two equal.
# ---------------------------------------------------------------------------
def _twin_name(table):
    """The current-state table name a pre-takeover/historical table's name pairs with - the
    same rule `sql_tool._current_twin` applies to the live corpus - or None. A shape read off
    the name alone: no building is named here."""
    text = str(table or "")
    return text.replace("existing_", "", 1) if "_existing_" in text else None


def _current_twin_issue(existing_table: str, current_table: str, sql: str) -> Issue:
    """The result read a pre-takeover/historical table on a plain present-tense question,
    though its current-state twin was ALSO available to read instead (see the comment above).
    Generic: both names come off the SQL and the routed cards, nothing of any building."""
    return Issue(
        CURRENT_TWIN,
        "Historical table read for a present-tense question — re-querying the current one",
        (f'The previous query `{sql}` read "{existing_table}", which is the PRE-TAKEOVER/'
         f'historical table. This question never says existing, before, pre-takeover, '
         f'original or historical, so it means the CURRENT arrangement: write a new query '
         f'that reads "{current_table}" instead of "{existing_table}".'),
    )


# --------------------------------------------------------------------------- place rankings
#
# WAVE 3, G6 (2026-10-03): A RANKING OF PLACES GROUPED ON THE PLACE AS EACH SOURCE PRINTED IT.
# Measured on the goal-function runs of 2026-09-30 and 2026-10-01: "which <place> holds the most
# ...?" was written as a ranking - GROUP BY a printed place column, ORDER BY the count DESC - over
# registers that print each place the way each source happened to spell it. One place printed
# several ways was split several ways, and a printed word covering a whole storey or an open area
# ranked first as if it were one place. The writer's prompt already says a ranking of places
# groups on the location key, keeping the entries resolved to that kind of place; the writer kept
# the rule's "never LIMIT 1" and not its key, and the loop had no handle, since a grouped result
# raises no column-shaped issue. So this makes that rule enforceable, the way IDENTIFIER_MISSING
# and the storey EMPTY do for theirs.
#
# Raised, on a result with rows, when ALL of these hold:
#   * the query is a RANKING: a GROUP BY at the top of the query, and an ORDER BY at the top
#     whose first item is sorted DESC and is no column it groups on;
#   * a column it groups on belongs to a table the query reads - at any depth, so a set
#     operation inside a sub-SELECT is seen through - that has a location key and that key's
#     resolution column (`_KEY_WORDS`, `_RESOLUTION_WORDS`, by the WORDS of a column's name),
#     read off the LOADED table through the injected reader's `columns`;
#   * no column it groups on is that key, that resolution column or a level code: grouping on
#     any of them is the rule obeyed;
#   * the grouped column's name, apart from its qualifier words (`_QUALIFIER_NAME_WORDS`: "as",
#     "printed" ...), names a storey (`_STOREY_NAME_WORDS`) and the table has a level code - or
#     names a kind of place its resolution column actually holds (read through the reader).
# The storey reading is tried first: a storey word can also be a resolution value, and a storey
# ranking keeps every entry on that storey, whatever it resolves to.
#
# Every name in the instruction - the grouped column, the key, the resolution column, the kind
# of place, the level code - is read off the query or the loaded table; nothing of any building
# is written here. Its fixed words carry no hyphen and score on no router card, for the router's
# sake: each re-query is routed on its own text. It needs the reader, which the loop hands over
# only as long as a re-query can still be sent - so with no step left, or the loop off, nothing is
# raised. A reader that answers nothing, or raises, raises nothing here.
# ---------------------------------------------------------------------------
#: The words of a table's own location key's name, and of its resolution column's name.
_KEY_WORDS = ("location", "id")
_RESOLUTION_WORDS = ("location", "resolution")


def _token_depths(tokens) -> list:
    """Each token's parenthesis depth - 0 for the query's own clauses."""
    depths, depth = [], 0
    for token in tokens:
        if token[1] == ")":
            depth = max(0, depth - 1)
        depths.append(depth)
        if token[1] == "(":
            depth += 1
    return depths


def _clause_items(tokens, depths, opener: str):
    """The items of the query's own `<opener> BY ...` clause (depth 0), each as its tokens, split
    on its top-level commas and ended by the next clause - or [] when the query has none."""
    for i, token in enumerate(tokens):
        if (depths[i] == 0 and _keyword(token) == opener and i + 1 < len(tokens)
                and _keyword(tokens[i + 1]) == "BY"):
            items, current = [], []
            for j in range(i + 2, len(tokens)):
                if depths[j] == 0 and _keyword(tokens[j]) in _CLAUSE_ENDS:
                    break
                if depths[j] == 0 and tokens[j][1] == ",":
                    items.append(current)
                    current = []
                else:
                    current.append(tokens[j])
            items.append(current)
            return [item for item in items if item]
    return []


def _plain_column(item):
    """`(qualifier or None, column)` when `item` is a bare column - `col`, `"col"` or `t.col` -
    else None (an expression, an aggregate call, a position)."""
    if len(item) == 1 and _is_name(item[0]):
        return None, item[0][2]
    if len(item) == 3 and _is_name(item[0]) and item[1][1] == "." and _is_name(item[2]):
        return item[0][2], item[2][2]
    return None


def _loaded_column_names(column_values, table):
    """The columns of `table` as loaded, as the reader spells them, or None - the reader's optional
    `columns(table)`; one that raises answered nothing.

    This and `_held_values` catch their own errors where `sql_tool` would use its `_companion`:
    `sql_tool` imports this module, so this one cannot import it back (the same reason
    `result_is_empty` above is a pinned duplicate rather than an import). The rule is the
    companion's - an aid to a re-query that fails is dropped, with a warning, and costs nothing
    else."""
    columns = getattr(column_values, "columns", None)
    if not callable(columns):
        return None
    try:
        found = columns(table)
    except Exception as e:  # noqa: BLE001 - an aid to a re-query must never cost it
        logger.warning(f"sql_loop: listing the columns of {table} failed "
                       f"({type(e).__name__}: {e}); no ranking check")
        return None
    return [str(c) for c in found] if found else None


def _held_values(column_values, table, column) -> list:
    """The values the reader gives for `table.column`, or [] - one that raises answered nothing."""
    try:
        found = column_values(table, column)
    except Exception as e:  # noqa: BLE001 - see `_loaded_column_names`
        logger.warning(f"sql_loop: reading the values of {table}.{column} failed "
                       f"({type(e).__name__}: {e}); no ranking check")
        return []
    return list(found or [])


def _place_shapes(tokens, column_values):
    """`(shapes, aliases)` for a query's tokens: `shapes` maps each table the query reads - at any
    depth - that carries a location key and that key's resolution column, read off the LOADED table
    through the reader, to `(lower-cased columns, key, resolution column, level code or None)`;
    `aliases` maps every table name and alias the query declares, lower-cased, to its table."""
    tables, aliases = [], {}
    for i, token in enumerate(tokens):
        if _keyword(token) in ("FROM", "JOIN"):
            _declare_tables(tokens, i + 1, tables, aliases)
    shapes = {}
    for table in tables:
        columns = _loaded_column_names(column_values, table)
        if not columns:
            continue
        key = next((c for c in columns if tuple(_name_words(c)) == _KEY_WORDS), None)
        resolution = next((c for c in columns if tuple(_name_words(c)) == _RESOLUTION_WORDS), None)
        if key and resolution:
            shapes[table] = ({c.lower() for c in columns}, key, resolution,
                             next((c for c in columns if is_level_code_column(c)), None))
    return shapes, aliases


def _place_ranking_issue(sql: str, column_values):
    """PLACE_RANKED_ON_TEXT for `sql`, or None - see the comment above."""
    tokens = sql_token_spans(sql) if column_values is not None else None
    if not tokens:
        return None
    depths = _token_depths(tokens)
    groups = [_plain_column(item) for item in _clause_items(tokens, depths, "GROUP")]
    orders = _clause_items(tokens, depths, "ORDER")
    if not groups or not orders:
        return None
    first = [_keyword(token) for token in orders[0]]
    if "DESC" not in first:
        return None
    head = _plain_column(orders[0][:first.index("DESC")])
    grouped = [g for g in groups if g]
    if head and any(g[1].lower() == head[1].lower() for g in grouped):
        return None  # ordered by what it groups on: a listing, not a ranking
    shapes, aliases = _place_shapes(tokens, column_values)
    if not shapes:
        return None
    for _, key, resolution, _ in shapes.values():
        if any(g[1].lower() in (key.lower(), resolution.lower()) or is_level_code_column(g[1])
               for g in grouped):
            return None  # grouped on the key, its resolution or a level code: the rule obeyed
    for qualifier, column in grouped:
        named = aliases.get(str(qualifier or "").lower())
        holders = ([named] if named in shapes else []) or [
            t for t in shapes if column.lower() in shapes[t][0]]
        if not holders:
            continue
        _, key, resolution, level = shapes[holders[0]]
        words = [w for w in _name_words(column) if w not in _QUALIFIER_NAME_WORDS]
        if not words:
            continue
        if any(w in _STOREY_NAME_WORDS for w in words):
            if level:
                return Issue(
                    PLACE_RANKED_ON_TEXT, "A ranking grouped printed text — re-querying on the key",
                    (f"The previous query ranked by {quote_identifier(column)}, whose spelling "
                     f"varies: the same storey is spelled several ways there. Rank by "
                     f"{quote_identifier(level)} instead, and return them all, largest first."))
            continue
        kinds = [v for v in _held_values(column_values, holders[0], resolution)
                 if str(v).strip().lower() in words]
        if kinds:
            return Issue(
                PLACE_RANKED_ON_TEXT, "A ranking grouped printed text — re-querying on the key",
                (f"The previous query ranked by {quote_identifier(column)}, whose spelling varies: "
                 f"the same spot is spelled several ways there, and some entries cover a whole "
                 f"storey instead. Rank by {quote_identifier(key)} instead, keeping just the "
                 f"entries whose {quote_identifier(resolution)} = {quote_literal(kinds[0])}, take "
                 f"each spot's wording from the listing keyed by {quote_identifier(key)}, and "
                 f"return them all, largest first."))
    return None


# --------------------------------------------------------------------------- place comparisons
#
# WAVE 4, A2 (2026-10-03): PLACES COMPARED OR COMBINED ON THE PLACE AS EACH SOURCE PRINTED IT.
# Measured on the goal-function run of that day: "which <places> have one thing but not another?"
# was written as a set comparison on a printed place column - `c NOT IN (SELECT c FROM ... WHERE
# <the other thing>)` - over a register that prints one place several ways. Places holding both
# things under two spellings came back as holding only one, beside entries that cover a whole
# storey or an open area; the result was not empty, so no check here fired, and G6 above reads
# rankings only. So G6's rule is generalised from rankings to comparisons.
#
# Raised, on a result with rows and whatever its shape, when ALL of these hold:
#   * the query compares or combines places: a column compared `[NOT] IN` a sub-SELECT of a column
#     of the same name (`c [NOT] IN (SELECT [DISTINCT] c FROM ...)`), or the two arms of an EXCEPT
#     or INTERSECT, each selecting one plain column (`_place_comparisons`);
#   * each compared column's table - the one its qualifier names, else the first table of its own
#     query's FROM that has it - carries a location key and that key's resolution column, read off
#     the LOADED table through the reader (`_place_shapes`, G6's own reading);
#   * neither compared column is the key, the resolution column or a level code: comparing on any
#     of them is the rule obeyed;
#   * the compared column's name, apart from its qualifier words (`_QUALIFIER_NAME_WORDS`), names a
#     kind of place its resolution column actually holds - and no storey word, since a storey is
#     compared through its level code, which is no kind of place.
# Every name in the instruction - the compared column, the key, the resolution column, the kind -
# is read off the query or the loaded table; nothing of any building is written here. Its fixed
# words carry no hyphen and score on no router card, as G6's do. Like G6 it needs the reader, so
# with no step left, or the loop off, nothing is raised; a reader that answers nothing, or raises,
# raises nothing here. An EMPTY result raises EMPTY and not this.
# ---------------------------------------------------------------------------
#: The set operations whose arms are compared row against row - and every set operation, each of
#: which ends the arm before it.
_COMPARING_SET_OPERATIONS = ("EXCEPT", "INTERSECT")
_SET_OPERATION_WORDS = ("UNION",) + _COMPARING_SET_OPERATIONS


def _column_at(tokens, i):
    """`(qualifier or None, column, next index)` when a plain column - `col`, `"col"` or `t.col` -
    starts at `tokens[i]` (never the second half of a qualified one), else None."""
    n = len(tokens)
    if i >= n or not _is_name(tokens[i]) or (i and tokens[i - 1][1] == "."):
        return None
    if i + 2 < n and tokens[i + 1][1] == "." and _is_name(tokens[i + 2]):
        return tokens[i][2], tokens[i + 2][2], i + 3
    return None, tokens[i][2], i + 1


def _enclosing_select(tokens, depths, i):
    """The index of the SELECT opening the query `tokens[i]` stands in - walking back through any
    bracket that holds no SELECT of its own - or None."""
    current = depths[i]
    for k in range(i - 1, -1, -1):
        current = min(current, depths[k])
        if depths[k] == current and _keyword(tokens[k]) == "SELECT":
            return k
    return None


def _scope_tables(tokens, depths, select_at) -> list:
    """The tables the FROM and JOINs of the query opened by the SELECT at `select_at` name - its
    own clauses, at that SELECT's depth, up to the end of that query or of its arm."""
    if select_at is None:
        return []
    tables, aliases = [], {}
    depth = depths[select_at]
    for k in range(select_at + 1, len(tokens)):
        word = _keyword(tokens[k])
        if depths[k] < depth or (depths[k] == depth and word in _SET_OPERATION_WORDS):
            break
        if depths[k] == depth and word in ("FROM", "JOIN"):
            _declare_tables(tokens, k + 1, tables, aliases)
    return tables


def _arm_column(tokens, depths, select_at):
    """`(qualifier or None, column, its query's tables)` when the query opened by the SELECT at
    `select_at` selects exactly one plain column, else None."""
    if select_at is None:
        return None
    k = select_at + 1
    if k < len(tokens) and _keyword(tokens[k]) == "DISTINCT":
        k += 1
    found = _column_at(tokens, k)
    if found is None or found[2] >= len(tokens) or _keyword(tokens[found[2]]) != "FROM":
        return None
    return found[0], found[1], _scope_tables(tokens, depths, select_at)


def _place_comparisons(tokens, depths) -> list:
    """`[(compared, other), …]` - each pair of columns the query compares as sets, each side
    `(qualifier or None, column, its own query's tables)`: `c [NOT] IN (SELECT [DISTINCT] c FROM
    …)`, the two columns of one name, compared first; then the one plain column each arm of an
    EXCEPT or INTERSECT selects."""
    found, n = [], len(tokens)
    for i in range(n):
        left = _column_at(tokens, i)
        if left is None:
            continue
        j = left[2] + (1 if left[2] < n and _keyword(tokens[left[2]]) == "NOT" else 0)
        if not (j + 2 < n and _keyword(tokens[j]) == "IN" and tokens[j + 1][1] == "("
                and _keyword(tokens[j + 2]) == "SELECT"):
            continue
        right = _arm_column(tokens, depths, j + 2)
        if right is None or right[1].lower() != left[1].lower():
            continue
        found.append(((left[0], left[1],
                       _scope_tables(tokens, depths, _enclosing_select(tokens, depths, i))),
                      right))
    for s in range(n):
        if _keyword(tokens[s]) not in _COMPARING_SET_OPERATIONS:
            continue
        k = next((m for m in range(s + 1, n)
                  if not (_keyword(tokens[m]) in ("ALL", "DISTINCT") or tokens[m][1] == "(")), n)
        first = _arm_column(tokens, depths, _enclosing_select(tokens, depths, s))
        second = _arm_column(tokens, depths, k if k < n and _keyword(tokens[k]) == "SELECT"
                             else None)
        if first and second:
            found.append((first, second))
    return found


def _place_holder(side, shapes, aliases):
    """The table of `shapes` a compared column belongs to - the one its qualifier names, else the
    first table of its own query's FROM that has it - or None."""
    qualifier, column, scope = side
    by_lower = {t.lower(): t for t in shapes}
    if qualifier:
        named = by_lower.get(str(aliases.get(str(qualifier).lower()) or "").lower())
        return named if named and column.lower() in shapes[named][0] else None
    return next((by_lower[t.lower()] for t in scope
                 if t.lower() in by_lower and column.lower() in shapes[by_lower[t.lower()]][0]),
                None)


def _place_comparison_issue(sql: str, column_values):
    """PLACE_COMPARED_ON_TEXT for `sql`, or None - see the comment above."""
    tokens = sql_token_spans(sql) if column_values is not None else None
    if not tokens:
        return None
    depths = _token_depths(tokens)
    pairs = _place_comparisons(tokens, depths)
    if not pairs:
        return None
    shapes, aliases = _place_shapes(tokens, column_values)
    if not shapes:
        return None
    for compared, other in pairs:
        holders = [_place_holder(side, shapes, aliases) for side in (compared, other)]
        if None in holders:
            continue
        if any(side[1].lower() in (shapes[h][1].lower(), shapes[h][2].lower())
               or is_level_code_column(side[1]) for side, h in zip((compared, other), holders)):
            continue  # compared on the key, its resolution or a level code: the rule obeyed
        _, key, resolution, _ = shapes[holders[0]]
        words = [w for w in _name_words(compared[1]) if w not in _QUALIFIER_NAME_WORDS]
        if not words or any(w in _STOREY_NAME_WORDS for w in words):
            continue
        kinds = [v for v in _held_values(column_values, holders[0], resolution)
                 if str(v).strip().lower() in words]
        if kinds:
            return Issue(
                PLACE_COMPARED_ON_TEXT, "A comparison on printed text — re-querying on the key",
                (f"The previous query compared spots by {quote_identifier(compared[1])}, whose "
                 f"spelling varies: the same spot is spelled several ways there, and some entries "
                 f"cover a whole storey instead. Compare by {quote_identifier(key)} instead, "
                 f"keeping just the entries whose {quote_identifier(resolution)} = "
                 f"{quote_literal(kinds[0])}, and take each spot's wording from the listing keyed "
                 f"by {quote_identifier(key)}."))
    return None


def inspect_result(result_text: str, question: str, routed_cards, column_values=None) -> list:
    """Every issue the result text shows, in the spec's priority order. No model call and
    no state. Its one outside read is the injected `column_values(table, column)` - the
    distinct values of a column as the loaded table holds them, or None - which is asked
    only for the columns an EMPTY result's WHERE filtered, and only when that EMPTY gets
    the generic instruction; without it (the default) this is pure. An issue whose
    `instruction` is "" is a finding, not a re-query."""
    issues = []
    sql = result_sql(result_text)
    cards = cards_in_sql(sql, routed_cards)
    cols = result_columns(result_text)
    aggregate = sql_is_aggregate(sql)
    empty = result_is_empty(result_text)

    # A query that did not run has no header, no rows and no shape line, so none of the
    # checks below can read anything — this is the only issue such a result can raise.
    if result_is_failure(result_text):
        issues.append(_failed_sql_issue(result_failure_error(result_text)))

    if empty:
        # The address first, and it is tried against the ROUTED cards rather than the ones
        # the SQL read: the query that matched nothing is precisely the one that looked in
        # the wrong place, so what it read is no guide to where the row is. It also overrides
        # the ABSTENTION above, deliberately. That rule is about a vague instruction — telling
        # a writer that declined to look anywhere to "look again" asks an abstention to become
        # a claim. An address asks nothing of the kind: it names a row, and the row either
        # exists or it does not.
        addressed = card_identifier_matches(question, routed_cards)
        if addressed:
            issues.append(_empty_identifier_issue(addressed))
        elif cards:
            # A storey filtered by printed text, or on its level code, is asked for again
            # through the level code, and its printed values are never listed (spec-fix3 c).
            # The loaded tables' columns are listed once, for both questions (T11a-R6).
            loaded = _column_lister(column_values)
            storey, printed, alone = _storey_filters(sql, routed_cards, loaded)
            issues.append(_empty_issue(
                sql, _filtered_values_text(sql, routed_cards, column_values, skip=printed,
                                           loaded_columns=loaded), storey=storey, alone=alone))
        else:
            issues.append(_empty_abstention(sql))

    # Wave 3, F2: a value compared with a column of a table that never holds it, though another
    # column of that table does - raised whatever the rows, since an empty arm of a set
    # operation or the unmatched side of an outer join hides inside a result that has rows.
    # Never for a query that did not run: such a result carries no line to read.
    if not result_is_failure(result_text):
        sentences = literal_elsewhere_sentences(result_text)
        if sentences:
            issues.append(_literal_elsewhere_issue(sentences))

    # Wave 5, G13 (a): the result reads a pre-takeover/historical table though its current-state
    # twin was ALSO routed, on a question that never says it means the historical state on
    # purpose - see the comment above `_current_twin_issue`. Never on an EMPTY result (the EMPTY
    # issue above is tried first) or a FAILED one (no table is reliably "read" by a query that
    # never ran).
    if not empty and not result_is_failure(result_text) and not _asks_about_existing_state(question):
        routed_names = {c.get("table") for c in (routed_cards or []) if c.get("table")}
        for card in cards:
            twin = _twin_name(card.get("table"))
            if twin and twin in routed_names:
                issues.append(_current_twin_issue(card["table"], twin, sql))
                break

    # Wave 3, G6: a ranking of places grouped on printed place text, re-queried through the
    # location key. Only on a grouped result with rows, and only with the reader, which reads the
    # loaded table's columns and its resolution values - so never on the last step.
    if aggregate and not empty and not result_is_failure(result_text):
        ranked = _place_ranking_issue(sql, column_values)
        if ranked:
            issues.append(ranked)

    # Wave 4, A2: places compared or combined on printed place text - `[NOT] IN` a sub-SELECT of
    # the same column, EXCEPT or INTERSECT - re-queried through the location key. On a result with
    # rows, whatever its shape, and only with the reader - so never on the last step.
    if not empty and not result_is_failure(result_text):
        compared = _place_comparison_issue(sql, column_values)
        if compared:
            issues.append(compared)

    # An aggregate result is exempt: it has no per-entity identifier and no wider row to
    # widen to. An empty result is NOT exempt — its header still says which columns were
    # selected, and the priority order decides which issue the loop acts on.
    if cols and not aggregate:
        # A ONE-ROW result is the answer, not a list that lost its labels: there is
        # nothing to tell apart, so no identifier is needed to act on it. Fix wave 1 —
        # three of twelve firings in the measurement were on one-row results, none of the
        # loop's wins was, and one of the three sent a correct single-fact answer off to
        # a different table and lost it.
        if (asks_to_list(question) or asks_for_details(question)) and result_total_rows(result_text) != 1:
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

    # A quantity question whose tables answered gets exactly one cross-check, whatever shape
    # the answer came back in: a single cell, a grouped count, or — the commonest shape here,
    # and the one this corpus's disputed quantities all take — a plain row list whose total
    # is stated by the RESULT SHAPE or truncation line rather than computed by the query.
    # Fix round 3: an earlier narrowing to the first two shapes silenced the third, which is
    # the one the cross-check exists for. Silence is for a question that asks no count, and
    # for an empty result, which goes to the document fallback instead.
    if asks_count(question) and not empty and cols:
        issues.append(Issue(
            COUNT_CROSSCHECK,
            "Quantity question — cross-checking the documents",
            "",
        ))
    # T7 — a contact- or warranty-shaped question whose tables answered gets the SAME
    # one-call treatment, for the same reason: "who do we contact" and "is it still under
    # warranty" commonly turn on a letter or a manual page a table-only answer never
    # reaches. `elif`, not a second `if` — T7-R1 keeps a question that is BOTH count-shaped
    # and letter-shaped on the count cross-check alone (today's heading, byte-identical),
    # so this never fires once COUNT_CROSSCHECK already has. `not empty and cols` is the
    # same guard COUNT_CROSSCHECK uses, for the same reason: it is also what keeps a FAILED
    # result (no header, so no `cols`) out — T7-R4.
    elif is_letter_shaped(question) and not empty and cols:
        issues.append(Issue(
            LETTER_CROSSCHECK,
            "Contact/warranty question — cross-checking the documents",
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
    """What gets appended to the question for query number `step`. `PREVIOUS_SQL_MARKER` is
    where a values clause in the instruction ends, for the router's cut in `sql_tool`."""
    return (f"\n(Investigation step {step}: {requery_instruction(issue)}"
            f"{PREVIOUS_SQL_MARKER} `{previous_sql}`)")


def investigation_trailer(step_count: int) -> str:
    """The one line that tells the answer writer an investigation happened. Never quoted
    back to the user — the caller's never-print rule names it.

    It counts the steps and names no issue kind (spec-fix10, 2026-10-01). It used to end
    "; issues: <kinds>", and a writer handed a result holding every value its question
    asked for, under "issues: IDENTIFIER_MISSING", answered that the records had no such
    entry: an issue name printed under a complete answer read as a verdict on it. The kinds
    stay where they are bookkeeping - `Investigation.issues` and each step's events."""
    return f"INVESTIGATION - steps: {step_count}"


def investigation_budget_seconds() -> float:
    """The wall-clock budget for the whole investigation, in seconds: the `SQL_LOOP_TIMEOUT`
    environment setting, default 60. Anything unreadable — or a value so small it would
    disable the loop outright — falls back to the default, because a mistyped setting must
    slow the loop down, never silently switch it off. Read at call time, so a deployment can
    change it without a restart of this module's import."""
    raw = os.environ.get(SQL_LOOP_TIMEOUT_ENV)
    if raw is None or not str(raw).strip():
        return DEFAULT_BUDGET_SECONDS
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_BUDGET_SECONDS
    return value if value > 0 else DEFAULT_BUDGET_SECONDS


def _top_excerpts(text: str) -> str:
    """At most CROSSCHECK_EXCERPTS of whatever the caller's search returned."""
    if not text:
        return ""
    parts = text.split(EXCERPT_SEPARATOR)
    return EXCERPT_SEPARATOR.join(parts[:CROSSCHECK_EXCERPTS])


# --------------------------------------------------------------------------- the loop

def iter_sql_investigation(question, user_id, sb, *, execute, search=None, routed_cards=None,
                           max_steps=3, user_question=None, clock=None, column_values=None):
    """Run the bounded investigation, yielding an event before and after every external call.

    Events:
      ("step",       {"step": k, "issue": kind or None, "detail": str, "sql": str})
                     — before query k; `sql` is the query being reacted to ("" for k=1).
      ("step_done",  {"step": k, "rows": int, "empty": bool, "issues_found": [kind, …]})
                     — after query k, so the caller can close its tool event with an
                       outcome rather than only an intention.
      ("crosscheck", {"kind": …, "detail": str, "query": str})
                     — before the single retrieval call, whether that call is an appended
                       cross-check (quantity or, T7, contact/warranty — `APPENDED_CROSSCHECKS`)
                       or the terminal fallback for an empty/failed result.
      ("final",      Investigation)

    `execute(question, user_id, sb) -> str` and `search(query) -> str` are injected so the
    loop is testable without a database, a model or a network. `user_question` is the
    user's own wording, which is what the document search gets (the tool's paraphrase
    dilutes keyword ranking); it defaults to `question`. `clock` is injected by the test.
    `column_values(table, column) -> [value, …] | None` is injected the same way: the
    real values an EMPTY re-query lists for the columns its failed WHERE filtered. It is
    asked only when that re-query can still be sent, and never with the loop off.
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
        # The filtered columns' values cost a read of their table, and the only thing that
        # carries them is a generic EMPTY re-query - so the reader is handed over only when
        # such a re-query can still be SENT: not on the last step, and not after a step an
        # EMPTY issue drove, because a second EMPTY in a row is exactly what the repeat
        # guard below stops.
        reader = column_values if (k < max_steps and last_kind != EMPTY) else None
        issues = (inspect_result(result_text, question, cards, column_values=reader)
                  if looping else [])
        for issue in issues:
            note(issue.kind)
        yield ("step_done", {
            "step": k,
            "rows": result_total_rows(result_text),
            "empty": result_is_empty(result_text),
            "issues_found": [i.kind for i in issues],
        })

        # A failure used to break here, which is what sent a re-queryable binder error
        # straight to the document index. It is now an ISSUE with an instruction
        # (FAILED_SQL), so the ordinary machinery below decides: re-query for as long as
        # there are steps left, and fall back to the documents only once the LAST step has
        # failed — which is exactly what the terminal block after this loop does.
        if k >= max_steps:
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

    # The appended cross-check: one retrieval call, appended rather than substituted, and
    # only when the tables actually answered (otherwise the fallback above already asked).
    # T7 widens this from COUNT_CROSSCHECK alone to `APPENDED_CROSSCHECKS` (COUNT_CROSSCHECK
    # or LETTER_CROSSCHECK); `inspect_result` raises at most one of the two for a given
    # question (T7-R1), so there is never a question needing both headings.
    elif looping and search is not None and any(i.kind in APPENDED_CROSSCHECKS for i in issues):
        kind = next(i.kind for i in issues if i.kind in APPENDED_CROSSCHECKS)
        heading = CROSSCHECK_HEADING if kind == COUNT_CROSSCHECK else LETTER_CROSSCHECK_HEADING
        detail = ("Cross-checking the quantity against the documents" if kind == COUNT_CROSSCHECK
                 else "Cross-checking the documents for this party or warranty")
        yield ("crosscheck", {
            "kind": kind,
            "detail": detail,
            "query": search_query,
        })
        crosscheck = _top_excerpts(search(search_query))
        if crosscheck:
            result_text = result_text + "\n\n" + heading + "\n\n" + crosscheck

    if looping:
        result_text = result_text + "\n\n" + investigation_trailer(len(steps))

    yield ("final", Investigation(result_text, steps, crosscheck, source_tool, trail))


def run_sql_investigation(question, user_id, sb, *, execute, search=None, routed_cards=None,
                          max_steps=3, user_question=None, clock=None,
                          column_values=None) -> Investigation:
    """`iter_sql_investigation` drained: the same work, for a caller with no use for the
    per-step events."""
    final = None
    for kind, payload in iter_sql_investigation(question, user_id, sb, execute=execute,
                                                search=search, routed_cards=routed_cards,
                                                max_steps=max_steps,
                                                user_question=user_question, clock=clock,
                                                column_values=column_values):
        if kind == "final":
            final = payload
    return final
