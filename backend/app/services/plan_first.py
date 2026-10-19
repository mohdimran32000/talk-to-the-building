"""plan_first.py - pure logic for the plan-first multi-part step (spec 2026-09-30,
task W11-A1).

WHY THIS EXISTS
A multi-part question ("what assets are in room X, and what maintenance does the manual
prescribe for each?") gets ONE tool call today - the tool-choice turn picks one tool, that
tool is asked one question, and the answer silently drops every part the single call never
looked for. Because the dropped part is never even NAMED as a refusal in the final answer
("not recorded"), `second_look.py`'s refusal-triggered mechanism cannot fire on it either -
there is nothing in the answer text to detect. The fix is to split the question into its
parts BEFORE any tool runs, run the app's own tools once per part, and compose one answer
that is checked against the parts it was built from.

This module holds the parts of that mechanism with no model call and no tool call in them
at all - prompt text, bounds and the small parsers either side of the plan/check calls. The
calls themselves, the per-part tool calls, and the event stream all live in
`openai_client.py` (`_run_plan_first` and its helpers), alongside `_generate_checked_answer`/
`_buffered_answer`, whose machinery the compose step reuses for every buffered generation
(see `second_look.py`'s own module docstring for why: the ANSWER_CHECK checker, if on, must
still apply to whichever generation ends up being the final one).

STRICT JSON, PARSED DEFENSIVELY (the task brief's own ambiguity resolution): either provider
may wrap its JSON in a fenced code block or surrounding prose, or occasionally return
something that does not parse at all. `_extract_json_object` tries the raw text first, then
a fenced block's own contents, then the substring between the first `{` and the last `}` -
returning the first candidate that parses, or `None` when none of them do. Every caller
treats `None` (or any other validation failure) as a fail-safe fallback, never an exception
- for the PLANNER, that means the pre-existing normal path; for CHECK, once a compose has
already succeeded, it means keeping that composed answer rather than discarding it (ruling
W11-R1: CHECK is a refinement, not a gate - see `openai_client._run_plan_first`'s own
docstring for the full contract).

GENERIC ONLY: nothing below names a table, column, value, card id or question text of any
one project (this is a public repo) - every fixture in the matching test file is invented.

TASK W13-A1 (2026-10-18) - HEDGE RETRY. A paid run found a composed answer that said two
counts "agree" while, in the SAME breath, admitting it could not actually say so: "My lookup
covered only <one level>, so I can't say whether ...". That sentence names no missing figure
in `answer_check._REFUSAL_PHRASES`'s own vocabulary ("is not recorded", "do not contain" -
see that module), so CHECK read the comparison as addressed and no retry happened. `
hedge_sentences` below adds a second, narrower vocabulary for exactly this shape - a sentence
that hedges without ever using a refusal word - and `openai_client._run_plan_first` adds
EVERY part to the retry set when either vocabulary finds one. The new list is deliberately
SEPARATE from, and never merged into, `answer_check._REFUSAL_PHRASES` (ANSWER_CHECK and the
agentic second look depend on that exact list unchanged).
"""
import json
import re

from app.services.answer_check import iter_sentences, refusal_sentences

#: Requirement 1 - "at most 6" parts are ever honoured, however many a planner names.
MAX_PARTS = 6

#: Requirement 1's three allowed values for a part's "source".
VALID_SOURCES = ("data", "documents", "both")

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _extract_json_object(raw):
    """Strict JSON, parsed defensively: tries the raw text first (the common, well-behaved
    case), then a fenced ```json ... ``` block's own contents, then the substring between the
    first '{' and the last '}' (surrounding prose either side) - returning whichever of these
    is the FIRST to parse as JSON, or `None` when none of them do. Never raises."""
    if not raw or not isinstance(raw, str) or not raw.strip():
        return None
    candidates = [raw.strip()]
    fence = _FENCE_RE.search(raw)
    if fence:
        candidates.append(fence.group(1).strip())
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(raw[start:end + 1].strip())
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            continue
    return None


# ================================================================================ 1. PLAN

#: What the planner call is told to do (task brief requirement 1): split the question into
#: its independent parts, label each with the ONE source most likely to hold it, and say so
#: as strict JSON only. A question that is genuinely one ask must come back as a single part
#: - never invented splitting just to have more than one.
PLANNER_SYSTEM_PROMPT = """You split a user's question into its independent parts, for a system that will look up
each part separately before answering.

A "part" is ONE thing the user is asking for - a list, a count, a frequency, a location, a
date, a specification, and so on. Many questions are genuinely only one part; do not invent
extra parts just to split something that is really one ask. Only split when the question
asks for more than one distinct thing.

For each part, decide which source most likely holds the answer: "data" (a structured,
tabular lookup - counts, registers, specifications), "documents" (reading manuals or other
narrative text), or "both" when either could hold it or you are unsure.

Respond with STRICT JSON and nothing else, in exactly this shape:
{"parts": [{"question": "...", "source": "data" | "documents" | "both"}, ...]}

At most 6 parts. No preamble, no markdown code fences, no explanation - JSON only."""


def build_planner_user_text(question: str) -> str:
    """The planner call's own per-call data (task brief requirement 1): just the question -
    everything `PLANNER_SYSTEM_PROMPT` asks it to reason about."""
    return f"The user's question:\n{question}"


def parse_planner_response(raw):
    """The planner's reply, parsed (task brief requirement 1): a list of 2-6
    {"question", "source"} dicts when the question genuinely has that many independent
    parts, or `None` - for a single-part question, zero usable parts, or malformed JSON -
    which every caller treats the SAME way: fall back to the normal path, no further
    plan-first calls this turn ("A question with one part -> the normal path, unchanged").

    Defensive, same spirit as `second_look.parse_followup_response`'s NONE-tolerance: a part
    missing its own "question" (or blank) is DROPPED, never kept as a hole; an invalid or
    missing "source" becomes "both" (the safe default - try every tool rather than guess
    wrong and skip the one that would have answered it); anything past the first MAX_PARTS
    valid parts is silently dropped (a bound, never a failure)."""
    data = _extract_json_object(raw)
    if not isinstance(data, dict):
        return None
    raw_parts = data.get("parts")
    if not isinstance(raw_parts, list):
        return None

    parts = []
    for item in raw_parts:
        if not isinstance(item, dict):
            continue
        question = item.get("question")
        if not isinstance(question, str) or not question.strip():
            continue
        source = item.get("source")
        if isinstance(source, str):
            source = source.strip().lower()
        if source not in VALID_SOURCES:
            source = "both"
        parts.append({"question": question.strip(), "source": source})
        if len(parts) >= MAX_PARTS:
            break

    if len(parts) < 2:
        return None
    return parts


# ============================================================================= 2/3. COMPOSE

#: Appended to the compose call's own system prompt (task brief requirement 3): names what
#: this is and the one rule for a part the tools did not establish - say so plainly, never
#: infer it, never borrow another part's figure.
COMPOSE_INSTRUCTION = (
    "This question was split into the parts below and the system's own tools were run for "
    "each part separately. Answer EVERY part, in the order given. For a part whose results "
    "do not establish an answer, say plainly that it is not recorded for that part - never "
    "infer it, and never use another part's result to answer a different part."
)


def _part_result_block(index: int, part: dict, result: dict) -> str:
    """One part's own labelled block for the compose system prompt - its number (1-based, for
    the model to read), its own question, and ONLY the result lines for a tool that was
    actually attempted for it. `result["sql"]`/`result["doc"]` are `None` when that tool was
    never attempted for this part (not "attempted and found nothing", which is an empty
    string that still gets its own line) - mirrors `second_look.build_second_look_system`'s
    own "no section at all, never an empty heading" rule, one level more granular."""
    lines = [f"Part {index + 1}: {part.get('question', '')}"]
    sql_text = result.get("sql")
    doc_text = result.get("doc")
    if sql_text is None and doc_text is None:
        lines.append("(No tool was available to check this part.)")
    else:
        if sql_text is not None:
            lines.append(f"Structured-data result: {sql_text or '(no matching data found)'}")
        if doc_text is not None:
            lines.append(f"Document search result: {doc_text or '(no matching passage found)'}")
    return "\n".join(lines)


def build_compose_system(base_system_text: str, parts, results) -> str:
    """The compose call's system prompt (task brief requirement 3): everything the normal
    answer prompt already says, PLUS one instruction naming what this is, PLUS every part's
    own results, labelled by part, in that order - so a part's own tool results can never be
    read as belonging to a different part, and today's existing context always comes first.

    `results` is aligned 1:1 with `parts` - each `{"sql": str|None, "doc": str|None}`, as
    `openai_client._run_plan_parts` returns them."""
    blocks = [_part_result_block(i, part, result)
              for i, (part, result) in enumerate(zip(parts, results))]
    return "\n\n".join([base_system_text, COMPOSE_INSTRUCTION, *blocks])


def build_broadened_text(part_question: str, original_question: str) -> str:
    """A part's own text, broadened by the ORIGINAL question (task brief requirement 4's
    re-run: "the part text broadened by the question's own subject (e.g. the room or asset
    the question names)") - the same move `openai_client.stream_response`'s own
    query_structured_data dispatch already makes when a router's paraphrase drops the user's
    own wording: hand the tool BOTH, the narrower part text first, so a part written without
    repeating the question's subject (e.g. "what maintenance does the manual prescribe?")
    still carries that subject somewhere in the text a router or search can match on."""
    return (f"{part_question}\n(Additional context - the original question this part came "
            f"from: {original_question})")


# ================================================================================ 4. CHECK

#: What the check call is told to do (task brief requirement 4): read the parts and the
#: composed answer, and name - by the SAME numbers the parts are listed under - any part the
#: answer did not actually address.
CHECK_SYSTEM_PROMPT = """You check whether a composed answer actually answered every part of a question that was
split into parts before being answered.

You are given the parts, numbered, and the answer composed for all of them. For each part,
decide: did the answer actually address it - even by correctly saying it is not recorded -
or did it silently skip that part, give a vague non-answer, or answer a DIFFERENT part
instead? Only the latter counts as unanswered.

Respond with STRICT JSON and nothing else, in exactly this shape:
{"unanswered": [part numbers]} - using the SAME numbers the parts are listed under below. An
empty list means every part was addressed. No preamble, no markdown code fences, no
explanation - JSON only."""


def build_check_user_text(parts, composed_answer: str) -> str:
    """The check call's own per-call data (task brief requirement 4): every part, numbered
    the same way `CHECK_SYSTEM_PROMPT` tells it to answer with, and the answer composed for
    all of them."""
    numbered = "\n".join(f"Part {i + 1}: {part.get('question', '')}"
                          for i, part in enumerate(parts))
    return f"The parts:\n{numbered}\n\nThe composed answer:\n{composed_answer}"


def parse_check_response(raw, part_count: int):
    """The check call's reply, parsed (task brief requirement 4): a sorted, de-duplicated
    list of 0-based part indexes the answer did not address (converted down from the 1-based
    numbers the prompt used), or `None` when the JSON does not parse into the expected shape
    at all. `None` and `[]` are never conflated: `None` means the call itself could not be
    read, `[]` means it was read and found nothing wrong - a real distinction even though
    `openai_client._run_plan_first` ships the already-composed answer either way (ruling
    W11-R1: CHECK is a refinement, not a gate, so a CHECK that cannot be read is treated the
    same as one that read clean, never as a reason to discard a good answer). The distinction
    still matters because a NON-EMPTY list is the one case that differs from both: it names
    parts to re-run before composing again.

    An out-of-range or non-integer entry is dropped rather than failing the whole parse - a
    model naming "part 9" of a 4-part question is still a mostly sensible reply, and
    `openai_client._run_plan_first` only ever re-runs the parts this function says are
    genuinely in range."""
    data = _extract_json_object(raw)
    if not isinstance(data, dict) or "unanswered" not in data:
        return None
    raw_list = data.get("unanswered")
    if not isinstance(raw_list, list):
        return None

    indexes = set()
    for item in raw_list:
        if isinstance(item, bool) or not isinstance(item, int):
            continue
        idx = item - 1
        if 0 <= idx < part_count:
            indexes.add(idx)
    return sorted(indexes)


# =========================================================================== 5. HEDGE RETRY

#: Task W13-A1, requirement 2. A word-bounded vocabulary, kept SEPARATE from (never merged
#: into) `answer_check._REFUSAL_PHRASES` - that list is matched as a bare substring and is
#: depended on unchanged by ANSWER_CHECK and the agentic second look; this one is matched
#: word-bounded, inside one sentence at a time (`hedge_sentences` below), and exists only to
#: widen plan-first's own retry set. An apostrophe is optional and either style (`'`/`'`)
#: is read the same way, so "can't"/"cant"/"can't" all match alike. Generic: no table,
#: column, value or project fact is named by any of these phrases.
_APOS = "['’]?"
_HEDGE_PHRASE_RE = re.compile(
    rf"\bcan{_APOS}t\s+say\b|\bcannot\s+say\b|"
    rf"\bcan{_APOS}t\s+tell\b|\bcannot\s+tell\b|"
    rf"\bcan{_APOS}t\s+confirm\b|\bcannot\s+confirm\b|"
    rf"\bcan{_APOS}t\s+determine\b|\bcannot\s+determine\b|"
    r"\bnot\s+able\s+to\s+(?:say|tell|confirm|determine)\b|"
    r"\bonly\s+covered\b|\bcovered\s+only\b",
    re.IGNORECASE,
)


def hedge_sentences(composed_answer: str) -> list:
    """Every sentence of `composed_answer` that reads as a HEDGE (task W13-A1, requirement 2):
    every sentence `answer_check.refusal_sentences` already finds, PLUS any further sentence
    matching `_HEDGE_PHRASE_RE` above, each sentence once even when both vocabularies would
    have matched it. Returned in TWO PASSES, not text order throughout (task W13-A3 correction
    - this docstring previously claimed "in the order the text prints them", which is false
    whenever a hedge-only sentence precedes a refusal-vocabulary one): every
    `refusal_sentences` match first, in its own order, then any remaining
    `_HEDGE_PHRASE_RE`-only sentence, in the order the text prints it - so a hedge-only
    sentence that precedes a refusal-vocabulary sentence in the text comes back AFTER it here.
    The sole caller (`openai_client._run_plan_first`) only tests truthiness, so this ordering
    has no behavioural effect today: a non-empty result means it adds EVERY part to its retry
    set - not just the part the hedge happens to be about, which the sentence alone cannot say.

    Sentence by sentence, exactly like `refusal_sentences` itself (`answer_check.
    iter_sentences`), so a phrase that happens to straddle two unrelated sentences is never
    counted; an empty/blank answer yields nothing."""
    found = list(refusal_sentences(composed_answer))
    seen = set(found)
    for sentence in iter_sentences(composed_answer):
        if sentence not in seen and _HEDGE_PHRASE_RE.search(sentence):
            found.append(sentence)
            seen.add(sentence)
    return found
