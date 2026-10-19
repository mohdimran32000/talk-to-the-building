"""second_look.py - pure logic for the agentic second look (spec 2026-09-30, task W10-A1).

WHY THIS EXISTS
`sql_loop.py` checks a SQL RESULT and re-queries or cross-checks on a deficiency it can
name. `answer_check.py` checks the FINAL ANSWER for a handful of named defects. Neither
catches the shape this module is for: an answer that correctly answers PART of a question
and, in the same breath, says another part is "not recorded" - when that other part was
simply never looked for (the wrong tool ran, or the right tool was asked the wrong thing).
The fix is not a smarter prompt; it is to let the question that was never asked get asked,
through the app's own tools, exactly once, bounded.

This module holds the parts of that mechanism with no model call and no tool call in them
at all - prompt text and the two small parsers either side of the ONE follow-up call. The
call itself, the two follow-up tool calls, and the event stream all live in
`openai_client.py` (`_run_agentic_second_look` and its helpers), alongside
`_generate_checked_answer`, whose machinery this reuses for every buffered generation
(see that module for why: the ANSWER_CHECK checker, if on, must still apply to whichever
generation ends up being the final one).

GENERIC ONLY: nothing below names a table, column, value, card id or question text of any
one project (this is a public repo) - every fixture in the matching test file is invented.
"""

#: What the follow-up-question-writer call is told to do (task brief requirement 2). It
#: reads the ORIGINAL question, the answer just produced, and the sentence(s) of that answer
#: which read as a refusal - and must write ONE short, focused question for a tool to try
#: again, or the literal word NONE when none of those sentences are actually about something
#: the original question asked for (a sentence restating a refusal is not itself evidence the
#: question asked for it).
FOLLOWUP_SYSTEM_PROMPT = """You write ONE focused follow-up question for a system's own search tools - never an
answer of your own, never commentary, never more than one question.

You are given the user's original question, an answer that was just produced for it, and one
or more sentences from that answer saying some part of what was asked is not recorded, not
stated, or not found. Some of those sentences may describe something the user never actually
asked about - a sentence restating a refusal is not itself evidence the original question
asked for it.

Decide: is there a part of the ORIGINAL question that those sentences say went unanswered,
and that a search tool or a data query could reasonably look for again? If so, write exactly
ONE short, focused question covering JUST that missing part - never the parts the answer
already gave, and never a restatement of the whole original question. If none of the quoted
sentences are actually about something the original question asked for, respond with exactly
the single word NONE.

Respond with nothing else: either the one follow-up question, or the single word NONE. No
preamble, no quotation marks, no explanation."""

#: Appended to the re-answer's own system prompt (task brief requirement 4): names what the
#: second look is, and the one rule its new results must be read under - evidence found is
#: used, evidence not found changes nothing about what the answer already correctly says is
#: missing.
SECOND_LOOK_INSTRUCTION = (
    "A second look searched the tools once more for the part of the question the previous "
    "answer called not recorded, not stated, or not found. Use what these new results "
    "establish; where they establish nothing, keep saying that part is not recorded - never "
    "infer it."
)


def build_followup_user_text(question: str, answer: str, sentences) -> str:
    """The follow-up-question-writer's own per-call data (task brief requirement 2): the
    original question, the answer just given, and the sentence(s) of it that read as a
    refusal - everything `FOLLOWUP_SYSTEM_PROMPT` asks it to reason about."""
    bullets = "\n".join(f"- {s}" for s in sentences)
    return (
        f"The user's original question:\n{question}\n\n"
        f"The answer just produced for it:\n{answer}\n\n"
        "Sentence(s) of that answer saying something asked is not recorded, not stated, or "
        f"not found:\n{bullets}"
    )


def parse_followup_response(text: str):
    """The follow-up-question-writer's reply, parsed: `None` for the literal NONE - tolerant
    of surrounding whitespace, quoting, trailing punctuation and case, and treating an empty
    reply the SAME way (fail safe: never run a tool over a nonsense "question") - otherwise
    the follow-up question text itself, stripped of surrounding whitespace and quotes but
    otherwise untouched (a real question's own punctuation is kept as written)."""
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    unquoted = cleaned.strip("\"'").strip()
    if not unquoted:
        return None
    if unquoted.rstrip(".").strip().upper() == "NONE":
        return None
    return unquoted


def build_second_look_system(base_system_text: str, sql_text: str, doc_text: str) -> str:
    """The re-answer's system prompt (task brief requirement 4): everything the first
    answer's own system prompt already had, PLUS one instruction naming what the second look
    is for, PLUS the second look's own new tool results - in that order, so what today's
    prompt already said comes first and what this step adds comes after it.

    `sql_text`/`doc_text` are "" when that tool was not run this turn, or ran and found
    nothing - either way it contributes no section (never an empty heading): the instruction
    alone still applies, because a second look finding nothing is itself the signal to keep
    saying the part is not recorded, not a reason to skip the re-answer."""
    sections = [base_system_text, SECOND_LOOK_INSTRUCTION]
    if sql_text:
        sections.append(f"Second look - structured-data results for the follow-up "
                         f"question:\n{sql_text}")
    if doc_text:
        sections.append(f"Second look - document search results for the follow-up "
                         f"question:\n{doc_text}")
    return "\n\n".join(sections)
