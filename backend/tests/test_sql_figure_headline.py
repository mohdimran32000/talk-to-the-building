"""test_sql_figure_headline.py - the FIGURE COUNTS bullet forbids the asked word on a combined
total it contradicts (wave 5, 2026-10-04).

THE DEFECT (diagnosed on the goal-function run of 2026-09-30; plan-wave5.md section 2.8). The
SQL/tool layer is fully correct and deterministic: a WHAT THE FIGURE COUNTS line already splits
a combined total into the items named the asked word and the other items the same filter caught.
The answer-writer's drafting sometimes opens with the COMBINED total using the asked-for noun
phrase (e.g. "9 pump units") though its own very next sentence correctly says only 4 of them are
pumps - a self-contradicting headline. One of four recorded runs already produced the safe
phrasing ("9 pump-related units") unprompted.

THE FIX, in OUTPUT_FORMAT_RULES only: when the line shows the aggregate spans more than one named
category, the headline figure must be the line's own asked-word sub-figure, named with the asked
word; the combined total, if mentioned at all, must use a neutral noun instead - codifying the
safe phrasing the writer already produced once.

Run:
    venv/Scripts/python -X utf8 tests/test_sql_figure_headline.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import openai_client as oc  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


# ===========================================================================
print("1. OUTPUT_FORMAT_RULES forbids the asked word on the combined total")
# ===========================================================================
rules = oc.OUTPUT_FORMAT_RULES
bullet = next((ln for ln in rules.splitlines()
              if ln.startswith("- A result may carry a WHAT THE FIGURE COUNTS line")), "")
check("RED: the bullet exists", bool(bullet), rules[:400])
check("RED: it still says the part named the asked word is the answer when that is what was "
      "asked for (unchanged)",
      "that part is the answer, ahead of the MATCHED rule" in bullet, bullet)
check("RED: the headline figure must be the line's OWN asked-word sub-figure, named with the "
      "asked word", "headline figure" in bullet and "asked-word sub-figure" in bullet, bullet)
check("RED: never the combined total, which the split contradicts as that word's count",
      "the split itself contradicts" in bullet or "the split contradicts" in bullet, bullet)
check("RED: the combined total, if mentioned, takes a NEUTRAL noun instead of the asked word",
      "neutral noun" in bullet, bullet)

# ===========================================================================
print("\n2. The frozen tool-choice prompt carries none of the new wording (ruling W7)")
# ===========================================================================
frozen = oc.TOOL_CHOICE_FORMAT_RULES
frozen_bullet = next((ln for ln in frozen.splitlines()
                      if ln.startswith("- A result may carry a WHAT THE FIGURE COUNTS line")),
                     "")
check("the frozen copy has no FIGURE COUNTS bullet at all - it predates F4 (v1.3)",
      frozen_bullet == "", frozen_bullet)
check("RED: ...so it carries none of the new wording either way",
      "neutral noun" not in frozen and "asked-word sub-figure" not in frozen)

# ===========================================================================
print("\n3. Named mutations")
# ===========================================================================
saved = oc.OUTPUT_FORMAT_RULES
try:
    oc.OUTPUT_FORMAT_RULES = rules.replace("neutral noun", "any noun")
    mutated = next((ln for ln in oc.OUTPUT_FORMAT_RULES.splitlines()
                    if ln.startswith("- A result may carry a WHAT THE FIGURE COUNTS line")), "")
    check("M1: the neutral-noun requirement is gone", "neutral noun" not in mutated)
finally:
    oc.OUTPUT_FORMAT_RULES = saved
check("mutation restored",
      "neutral noun" in next((ln for ln in oc.OUTPUT_FORMAT_RULES.splitlines()
                              if ln.startswith("- A result may carry a WHAT THE FIGURE COUNTS "
                                               "line")), ""))

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
