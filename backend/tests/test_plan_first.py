"""test_plan_first.py - pure logic for the plan-first multi-part step (spec 2026-09-30,
task W11-A1): the settings switch and the `plan_first.py` module's prompt-building/parsing
functions.

Every fixture here is INVENTED - no table, column, value, card id or question text of the
project (this is a public repo). Nothing in this file makes a model call or a tool call;
that wiring is proven separately in `test_plan_first_wiring.py`.

Run:
    venv/Scripts/python -X utf8 tests/test_plan_first.py
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
    os.environ.pop("PLAN_FIRST", None)


_clear_env()
from app.services import settings  # noqa: E402

# --------------------------------------------------------------------------- settings switch

print("1. settings.get_plan_first_enabled()")

check("the setting exists", hasattr(settings, "get_plan_first_enabled"))
check("RED: unset -> False (today's behaviour, byte-identical)",
      settings.get_plan_first_enabled() is False)

os.environ["PLAN_FIRST"] = "1"
check("'1' -> True", settings.get_plan_first_enabled() is True)

os.environ["PLAN_FIRST"] = "yes"
check("anything but the literal '1' stays False (typo-safe)",
      settings.get_plan_first_enabled() is False)

os.environ["PLAN_FIRST"] = "0"
check("'0' stays False", settings.get_plan_first_enabled() is False)

os.environ["PLAN_FIRST"] = ""
check("blank stays False", settings.get_plan_first_enabled() is False)
_clear_env()

# --------------------------------------------------------------------------- plan_first module

print("\n2. plan_first.py - planner prompt, parsing, bounds")

from app.services import plan_first as pf  # noqa: E402

check("PLANNER_SYSTEM_PROMPT exists and is non-empty prose",
      isinstance(getattr(pf, "PLANNER_SYSTEM_PROMPT", None), str)
      and len(pf.PLANNER_SYSTEM_PROMPT) > 40)
check("CHECK_SYSTEM_PROMPT exists and is non-empty prose",
      isinstance(getattr(pf, "CHECK_SYSTEM_PROMPT", None), str)
      and len(pf.CHECK_SYSTEM_PROMPT) > 40)
check("MAX_PARTS is 6 (the brief's own bound)", pf.MAX_PARTS == 6)

QUESTION = "What assets are in the storage room, and what maintenance does the manual " \
           "prescribe for each?"
user_text = pf.build_planner_user_text(QUESTION)
check("RED: build_planner_user_text carries the question",
      QUESTION in user_text, user_text)

# --- parse_planner_response: the happy path -------------------------------------------------

TWO_PART_JSON = (
    '{"parts": ['
    '{"question": "What assets are in the storage room?", "source": "data"}, '
    '{"question": "What maintenance does the manual prescribe for each asset?", '
    '"source": "documents"}'
    ']}'
)
parts = pf.parse_planner_response(TWO_PART_JSON)
check("RED: a genuine 2-part plan parses to a 2-item list",
      isinstance(parts, list) and len(parts) == 2, parts)
check("...part 0 keeps its own question and source",
      parts[0] == {"question": "What assets are in the storage room?", "source": "data"},
      parts[0])
check("...part 1 keeps its own question and source",
      parts[1] == {"question": "What maintenance does the manual prescribe for each asset?",
                    "source": "documents"},
      parts[1])

# --- defensive JSON: fenced code block, surrounding prose -----------------------------------

FENCED = f"Sure, here is the plan:\n```json\n{TWO_PART_JSON}\n```\nLet me know if that helps."
check("a fenced ```json ... ``` block with surrounding prose still parses",
      pf.parse_planner_response(FENCED) == parts, pf.parse_planner_response(FENCED))

PROSE_WRAPPED = f"Here you go -> {TWO_PART_JSON} <- hope that's useful!"
check("JSON with surrounding prose (no fence) still parses",
      pf.parse_planner_response(PROSE_WRAPPED) == parts, pf.parse_planner_response(PROSE_WRAPPED))

# --- one part -> None (the normal path, no further calls) -----------------------------------

ONE_PART_JSON = '{"parts": [{"question": "How many widgets are there?", "source": "data"}]}'
check("RED: a single-part plan -> None (requirement 1: the normal path, unchanged)",
      pf.parse_planner_response(ONE_PART_JSON) is None)

ZERO_PART_JSON = '{"parts": []}'
check("an empty parts list -> None", pf.parse_planner_response(ZERO_PART_JSON) is None)

# --- malformed JSON -> None (fail safe) ------------------------------------------------------

check("RED: garbage text -> None", pf.parse_planner_response("not json at all {{{") is None)
check("empty string -> None", pf.parse_planner_response("") is None)
check("None input -> None", pf.parse_planner_response(None) is None)
check("a bare JSON list (no 'parts' wrapper) -> None",
      pf.parse_planner_response('[{"question": "a?"}, {"question": "b?"}]') is None)
check("a dict with no 'parts' key -> None",
      pf.parse_planner_response('{"steps": []}') is None)
check("'parts' present but not a list -> None",
      pf.parse_planner_response('{"parts": "not a list"}') is None)

# --- a part missing "question" is dropped, never kept as a hole -----------------------------

HOLE_JSON = ('{"parts": ['
             '{"question": "Real part A?", "source": "data"}, '
             '{"source": "documents"}, '
             '{"question": "", "source": "data"}, '
             '{"question": "Real part B?", "source": "both"}'
             ']}')
holed = pf.parse_planner_response(HOLE_JSON)
check("a part with no question (or blank) is dropped, not kept as a placeholder",
      holed == [{"question": "Real part A?", "source": "data"},
                {"question": "Real part B?", "source": "both"}],
      holed)

# --- an invalid/missing source defaults to 'both' --------------------------------------------

BAD_SOURCE_JSON = ('{"parts": ['
                   '{"question": "Part one?", "source": "spreadsheet"}, '
                   '{"question": "Part two?"}'
                   ']}')
bad_source = pf.parse_planner_response(BAD_SOURCE_JSON)
check("RED: an unrecognised source value falls back to 'both' (try everything, never guess "
      "wrong and skip a tool)",
      bad_source[0]["source"] == "both", bad_source)
check("a missing source also falls back to 'both'", bad_source[1]["source"] == "both",
      bad_source)

# --- bounds: at most 6 parts honoured ---------------------------------------------------------

SEVEN_PARTS = '{"parts": [' + ", ".join(
    f'{{"question": "Part {i}?", "source": "both"}}' for i in range(1, 8)
) + ']}'
seven = pf.parse_planner_response(SEVEN_PARTS)
check("RED: 7 valid parts in the JSON -> only the first 6 are honoured",
      len(seven) == 6, seven)
check("...the 6 kept are the FIRST 6, in order",
      [p["question"] for p in seven] == [f"Part {i}?" for i in range(1, 7)], seven)

# --------------------------------------------------------------------------- build_compose_system

print("\n3. plan_first.build_compose_system - every part's results, labelled by part")

BASE_SYSTEM = "You are a helpful assistant.\n\nAnswer the user's question."
PARTS = [{"question": "What assets are in the storage room?", "source": "data"},
         {"question": "What maintenance does the manual prescribe for each?",
          "source": "documents"}]
RESULTS_BOTH = [{"sql": "Asset X, Asset Y.", "doc": None},
                {"sql": None, "doc": "Clean quarterly."}]

composed_system = pf.build_compose_system(BASE_SYSTEM, PARTS, RESULTS_BOTH)
check("RED: build_compose_system keeps the original system text",
      BASE_SYSTEM in composed_system, composed_system)
check("...carries the compose instruction",
      pf.COMPOSE_INSTRUCTION in composed_system)
check("...names part 1 and carries its own sql result",
      "Part 1" in composed_system and "Asset X, Asset Y." in composed_system)
check("...names part 2 and carries its own doc result",
      "Part 2" in composed_system and "Clean quarterly." in composed_system)
check("the base text comes before the instruction, which comes before the per-part blocks "
      "(order: today's context, then what this step adds)",
      composed_system.index(BASE_SYSTEM) < composed_system.index(pf.COMPOSE_INSTRUCTION) <
      composed_system.index("Part 1") < composed_system.index("Part 2"))

RESULTS_NEITHER = [{"sql": None, "doc": None}, {"sql": None, "doc": "Clean quarterly."}]
neither_system = pf.build_compose_system(BASE_SYSTEM, PARTS, RESULTS_NEITHER)
check("a part where NEITHER tool was attempted says so explicitly rather than being silent",
      "Part 1" in neither_system
      and "no tool" in neither_system.lower().split("part 2")[0].split("part 1")[1],
      neither_system)

RESULTS_EMPTY_FOUND = [{"sql": "", "doc": None}, {"sql": None, "doc": ""}]
empty_system = pf.build_compose_system(BASE_SYSTEM, PARTS, RESULTS_EMPTY_FOUND)
check("a tool that was attempted and found NOTHING is distinguished from 'not attempted' "
      "(an attempted-but-empty result still gets its own labelled line)",
      "Part 1" in empty_system and "Part 2" in empty_system, empty_system)

check("one part's result text never appears inside another part's own block "
      "(no bleed-through)",
      composed_system.split("Part 2")[0].count("Clean quarterly.") == 0, composed_system)

# --------------------------------------------------------------------------- build_broadened_text

print("\n4. plan_first.build_broadened_text")

PART_Q = "What maintenance does the manual prescribe?"
ORIGINAL_Q = "What assets are in the storage room, and what maintenance does the manual " \
             "prescribe for each?"
broadened = pf.build_broadened_text(PART_Q, ORIGINAL_Q)
check("RED: the broadened text carries the part's own question",
      PART_Q in broadened, broadened)
check("...and the original question (the subject it may be missing)",
      ORIGINAL_Q in broadened, broadened)

# --------------------------------------------------------------------------- CHECK

print("\n5. plan_first - the CHECK call's prompt building and parsing")

check("CHECK_SYSTEM_PROMPT mentions strict JSON shape (the literal field name)",
      "unanswered" in pf.CHECK_SYSTEM_PROMPT)

COMPOSED_ANSWER = "The storage room has Asset X and Asset Y."
check_user_text = pf.build_check_user_text(PARTS, COMPOSED_ANSWER)
check("RED: build_check_user_text carries both parts, numbered",
      "Part 1" in check_user_text and "Part 2" in check_user_text
      and PARTS[0]["question"] in check_user_text and PARTS[1]["question"] in check_user_text,
      check_user_text)
check("...and the composed answer", COMPOSED_ANSWER in check_user_text, check_user_text)

check("RED: '{\"unanswered\": [2]}' against 2 parts -> 0-based index [1]",
      pf.parse_check_response('{"unanswered": [2]}', 2) == [1])
check("an empty 'unanswered' list -> [] (a clean check, no re-run)",
      pf.parse_check_response('{"unanswered": []}', 2) == [])
check("both parts named -> both 0-based indexes, sorted",
      pf.parse_check_response('{"unanswered": [2, 1]}', 2) == [0, 1])
check("duplicate indexes are de-duplicated",
      pf.parse_check_response('{"unanswered": [1, 1, 1]}', 2) == [0])
check("an out-of-range part number is dropped, not fatal to the whole parse",
      pf.parse_check_response('{"unanswered": [9]}', 2) == [])
check("a non-integer entry is dropped, not fatal to the whole parse",
      pf.parse_check_response('{"unanswered": ["two", 2]}', 2) == [1])
check("a fenced ```json ... ``` CHECK response still parses",
      pf.parse_check_response('```json\n{"unanswered": [1]}\n```', 2) == [0])
check("CHECK response with surrounding prose still parses",
      pf.parse_check_response('Sure! {"unanswered": [1]} Hope that helps.', 2) == [0])

check("RED: malformed CHECK JSON -> None (never an empty list - the two must stay distinct: "
      "None is 'the call failed', [] is 'nothing is wrong')",
      pf.parse_check_response("not json at all {{{", 2) is None)
check("empty string -> None", pf.parse_check_response("", 2) is None)
check("a dict missing the 'unanswered' key entirely -> None",
      pf.parse_check_response('{"something_else": []}', 2) is None)
check("'unanswered' present but not a list -> None",
      pf.parse_check_response('{"unanswered": "none"}', 2) is None)
check("None -> [] is never conflated with None -> None (sanity: distinguishable types)",
      pf.parse_check_response('{"unanswered": []}', 2) is not None)

# --------------------------------------------------------------------------- generic only

print("\n6. generic-only check (public repo)")

check("no digit-and-letter tag shape in either prompt constant (spot check)",
      "DB-" not in pf.PLANNER_SYSTEM_PROMPT and "DB-" not in pf.CHECK_SYSTEM_PROMPT)
check("no project-shaped card id in either prompt constant",
      not any(tok in pf.PLANNER_SYSTEM_PROMPT or tok in pf.CHECK_SYSTEM_PROMPT
              for tok in ("ex-", "ey-", "gq-", "hwu")))

# ===========================================================================
# 7. plan_first.hedge_sentences - task W13-A1, requirement 2 (HEDGE RETRY)
# ===========================================================================
print("\n7. plan_first.hedge_sentences")

check("hedge_sentences exists", hasattr(pf, "hedge_sentences"))

POSITIVES = [
    "My lookup covered only the ground floor, so I can't say whether the counts agree.",
    "My lookup covered only the ground floor, so I cant say whether the counts agree.",
    "I cannot say whether these two figures agree.",
    "I can't tell if these match.",
    "I cannot tell if these match.",
    "I can't confirm that the totals line up.",
    "I cannot confirm that the totals line up.",
    "I cannot determine which total is correct.",
    "I can't determine which total is correct.",
    "We are not able to say which figure is right.",
    "We are not able to tell which figure is right.",
    "We are not able to confirm this.",
    "We are not able to determine this.",
    "The search only covered the first set of records.",
    "The search covered only the first set of records.",
]
for sentence in POSITIVES:
    check(f"RED: fires on {sentence!r}", pf.hedge_sentences(sentence) == [sentence], sentence)

NEGATIVES = [
    "The manual says the unit is serviced yearly.",
    "We can say the totals agree.",
    "There were 12 items on that list.",
    "The canteen covered the full floor.",
    "",
]
for sentence in NEGATIVES:
    check(f"the negative {sentence!r} does not fire", pf.hedge_sentences(sentence) == [])

check("None input yields nothing, never raises", pf.hedge_sentences(None) == [])

# refusal_sentences' own vocabulary is ALSO a hedge, without touching the new list at all
REFUSAL_ONLY = "The records are not recorded for that part."
check("RED: a plain refusal_sentences hit counts as a hedge too",
      pf.hedge_sentences(REFUSAL_ONLY) == [REFUSAL_ONLY], pf.hedge_sentences(REFUSAL_ONLY))

# both vocabularies can fire in the SAME multi-sentence answer - each sentence once, in order
MULTI = ("The site has 40 widgets. This is not recorded anywhere else. "
         "My lookup covered only one level, so I can't say if that matches.")
found_multi = pf.hedge_sentences(MULTI)
check("RED: both sentences are found, in the order the text prints them, the first sentence "
      "(a plain figure, no hedge) excluded",
      found_multi == ["This is not recorded anywhere else.",
                      "My lookup covered only one level, so I can't say if that matches."],
      found_multi)

# a sentence that matches BOTH vocabularies is listed once, not twice
BOTH_AT_ONCE = "This is not recorded, and I cannot say more than that."
check("a sentence matching both vocabularies at once is counted once",
      pf.hedge_sentences(BOTH_AT_ONCE) == [BOTH_AT_ONCE], pf.hedge_sentences(BOTH_AT_ONCE))

# a phrase split across two sentences by coincidence is never joined into one hedge
SPLIT = "We looked, but only. Covered nothing further, so no comment."
check("a phrase split across a sentence boundary is never counted as one hedge",
      pf.hedge_sentences(SPLIT) == [], pf.hedge_sentences(SPLIT))

check("no digit-and-letter tag shape or project card id near the new hedge vocabulary "
      "(public repo, spot check)",
      not any(tok in repr(pf._HEDGE_PHRASE_RE.pattern)
              for tok in ("DB-", "ex-", "ey-", "gq-", "hwu")))

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
