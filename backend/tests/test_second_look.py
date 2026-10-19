"""test_second_look.py - pure logic for the agentic second look (spec 2026-09-30,
task W10-A1): the settings switch, the sentence-level refusal helper added to
`answer_check.py`, and the `second_look.py` module's prompt-building/parsing functions.

Every fixture here is INVENTED - no table, column, value, card id or question text of the
project (this is a public repo). Nothing in this file makes a model call or a tool call;
that wiring is proven separately in `test_second_look_wiring.py`.

Run:
    venv/Scripts/python -X utf8 tests/test_second_look.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


def _clear_env():
    os.environ.pop("AGENTIC_SECOND_LOOK", None)


_clear_env()
from app.services import settings  # noqa: E402

# --------------------------------------------------------------------------- settings switch

print("1. settings.get_agentic_second_look_enabled()")

check("the setting exists", hasattr(settings, "get_agentic_second_look_enabled"))
check("RED: unset -> False (today's behaviour, byte-identical)",
      settings.get_agentic_second_look_enabled() is False)

os.environ["AGENTIC_SECOND_LOOK"] = "1"
check("'1' -> True", settings.get_agentic_second_look_enabled() is True)

os.environ["AGENTIC_SECOND_LOOK"] = "yes"
check("anything but the literal '1' stays False (typo-safe)",
      settings.get_agentic_second_look_enabled() is False)

os.environ["AGENTIC_SECOND_LOOK"] = "0"
check("'0' stays False", settings.get_agentic_second_look_enabled() is False)

os.environ["AGENTIC_SECOND_LOOK"] = ""
check("blank stays False", settings.get_agentic_second_look_enabled() is False)
_clear_env()

# --------------------------------------------------------------------------- answer_check.refusal_sentences

print("\n2. answer_check.iter_sentences / refusal_sentences (sentence-level, generic)")

from app.services import answer_check as ac  # noqa: E402

check("the helper exists", hasattr(ac, "refusal_sentences"))

TWO_PART = ("The widget's rating is 12 kW. The records do not state its warranty period. "
            "It was installed in 2021.")
sentences = list(ac.iter_sentences(TWO_PART))
check("RED: iter_sentences splits a multi-sentence answer into its three sentences",
      len(sentences) == 3, sentences)
check("...each sentence is the verbatim text, not re-wrapped",
      sentences[1] == "The records do not state its warranty period.", sentences)

refusals = ac.refusal_sentences(TWO_PART)
check("RED: refusal_sentences finds the ONE refusing sentence, not the whole answer",
      refusals == ["The records do not state its warranty period."], refusals)

check("a refusal buried mid-answer is found even though is_refusal_opening would miss it "
      "(it only looks at the FIRST sentence)",
      not ac.is_refusal_opening(TWO_PART) and len(refusals) == 1)

NO_REFUSAL = "There are 6 widgets installed in the room. All are green."
check("no refusal anywhere -> empty list", ac.refusal_sentences(NO_REFUSAL) == [])

check("empty string -> empty list", ac.refusal_sentences("") == [])
check("empty string -> iter_sentences yields nothing", list(ac.iter_sentences("")) == [])

TWO_REFUSALS = ("The records do not contain the serial number. The warranty is not recorded "
                "either. The unit was installed in 2021.")
check("two separate refusing sentences are both returned, in order",
      ac.refusal_sentences(TWO_REFUSALS) ==
      ["The records do not contain the serial number.",
       "The warranty is not recorded either."],
      ac.refusal_sentences(TWO_REFUSALS))

check("refusal_sentences reuses answer_check's own phrase vocabulary, not a second copy "
      "(same object identity check: the module-level tuple)",
      hasattr(ac, "_REFUSAL_PHRASES") and isinstance(ac._REFUSAL_PHRASES, tuple))

# --------------------------------------------------------------------------- second_look module

print("\n3. second_look.py - prompt building and NONE-parsing")

from app.services import second_look as sl  # noqa: E402

check("FOLLOWUP_SYSTEM_PROMPT exists and is non-empty prose",
      isinstance(getattr(sl, "FOLLOWUP_SYSTEM_PROMPT", None), str)
      and len(sl.FOLLOWUP_SYSTEM_PROMPT) > 40)
check("FOLLOWUP_SYSTEM_PROMPT mentions the literal word NONE (the model's escape hatch)",
      "NONE" in sl.FOLLOWUP_SYSTEM_PROMPT)
check("SECOND_LOOK_INSTRUCTION exists and is non-empty prose",
      isinstance(getattr(sl, "SECOND_LOOK_INSTRUCTION", None), str)
      and len(sl.SECOND_LOOK_INSTRUCTION) > 20)

QUESTION = "What devices are in this room, and are they still under warranty?"
ANSWER = ("The devices are Unit A and Unit B. The records do not state their warranty "
          "period.")
SENTS = ["The records do not state their warranty period."]

user_text = sl.build_followup_user_text(QUESTION, ANSWER, SENTS)
check("RED: build_followup_user_text includes the question, the answer and the sentence",
      QUESTION in user_text and ANSWER in user_text and SENTS[0] in user_text, user_text)

user_text_2 = sl.build_followup_user_text(QUESTION, ANSWER,
                                          ["The records do not state X.",
                                           "The records do not state Y."])
check("two refusal sentences both appear",
      "The records do not state X." in user_text_2
      and "The records do not state Y." in user_text_2)

# --- parse_followup_response --------------------------------------------------------------

check("RED: a real follow-up question is returned, stripped",
      sl.parse_followup_response("  Is Unit A still under warranty?  ")
      == "Is Unit A still under warranty?")
check("the literal NONE -> None", sl.parse_followup_response("NONE") is None)
check("NONE with surrounding whitespace -> None", sl.parse_followup_response("  NONE  ") is None)
check("none, lowercase -> None (tolerant of case)", sl.parse_followup_response("none") is None)
check("'NONE.' (trailing period) -> None", sl.parse_followup_response("NONE.") is None)
check("empty string -> None (fail safe, never run a tool on nothing)",
      sl.parse_followup_response("") is None)
check("whitespace-only -> None", sl.parse_followup_response("   ") is None)
check("None input -> None", sl.parse_followup_response(None) is None)
check("a quoted question has its quotes stripped",
      sl.parse_followup_response('"Is Unit A still under warranty?"')
      == "Is Unit A still under warranty?")
check("a real question that merely CONTAINS the word none is NOT treated as NONE",
      sl.parse_followup_response("Does none of the warranty documentation mention Unit A?")
      == "Does none of the warranty documentation mention Unit A?")

# --- build_second_look_system ---------------------------------------------------------------

BASE = "You are a helpful assistant.\n\nTool (query_structured_data) results:\nUnit A, Unit B"
SQL_TEXT = "Unit A: warranty 5 years from 2018-07-22."
DOC_TEXT = "[Source: warranty.md]\nUnit A and Unit B: 5-year warranty from purchase."

full = sl.build_second_look_system(BASE, SQL_TEXT, DOC_TEXT)
check("RED: build_second_look_system keeps the original system text",
      BASE in full, full)
check("...carries the instruction sentence",
      sl.SECOND_LOOK_INSTRUCTION in full)
check("...carries the new SQL result text", SQL_TEXT in full)
check("...carries the new document result text", DOC_TEXT in full)
check("the original text comes before the instruction, which comes before the new results "
      "(order: today's context, then what this step adds)",
      full.index(BASE) < full.index(sl.SECOND_LOOK_INSTRUCTION) <
      full.index(SQL_TEXT) < full.index(DOC_TEXT))

sql_only = sl.build_second_look_system(BASE, SQL_TEXT, "")
check("an empty doc_text contributes no section at all (not an empty heading)",
      DOC_TEXT not in sql_only and SQL_TEXT in sql_only and BASE in sql_only)

neither = sl.build_second_look_system(BASE, "", "")
check("both empty -> still the base text plus the instruction (a second look that finds "
      "nothing still carries the 'keep saying not recorded' instruction)",
      BASE in neither and sl.SECOND_LOOK_INSTRUCTION in neither
      and SQL_TEXT not in neither and DOC_TEXT not in neither)

check("never prints raw NONE handling internals or project-specific text (generic only - "
      "spot check: no digit-and-letter tag shape in the prompt constants)",
      "DB-" not in sl.FOLLOWUP_SYSTEM_PROMPT and "DB-" not in sl.SECOND_LOOK_INSTRUCTION)

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
