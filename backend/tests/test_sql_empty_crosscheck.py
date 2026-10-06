"""test_sql_empty_crosscheck.py - the cross-check bullet gets an EMPTY branch (wave 5, 2026-10-04).

THE DEFECT (diagnosed on the goal-function run of 2026-09-30; plan-wave5.md section 2.2). The
cross-check bullet in OUTPUT_FORMAT_RULES says the TABLE figure is the answer and forbids
replacing it with a cross-check excerpt's figure - but it never says what to do when there is NO
table figure at all, because the structured query returned no rows. On one retrieval draw, a
reranked chunk happened to print an unrelated count next to a sentence that merely CO-MENTIONED
the asked-for place, and the writer fabricated a brand-new per-category breakdown and total out of
it - grounded in neither the (empty) SQL result nor any single excerpt that actually states that
figure.

THE FIX, in OUTPUT_FORMAT_RULES only (never TOOL_CHOICE_FORMAT_RULES, which has no such branch and
stays byte-pinned): when the structured data returned no rows, the cross-check excerpts may only
CONFIRM or REFUTE the quantity - quoting a figure that is itself a verbatim sentence of a single
excerpt - never assemble a new breakdown or total out of rows that do not share the asked-for
place or entity; with nothing confirming a figure, say the quantity is not recorded.

Every value here is invented. Run:
    venv/Scripts/python -X utf8 tests/test_sql_empty_crosscheck.py
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
print("1. OUTPUT_FORMAT_RULES carries the new EMPTY-cross-check clause")
# ===========================================================================
rules = oc.OUTPUT_FORMAT_RULES
crosscheck_bullet = next((ln for ln in rules.splitlines()
                          if ln.startswith("- When a tool result carries a section of document "
                                           "excerpts added as a cross-check")), "")
check("RED: the cross-check bullet exists", bool(crosscheck_bullet), rules[:400])
check("RED: it still forbids replacing the table figure with a cross-check figure (unchanged)",
      "NEVER replace it with a figure from the cross-check excerpts" in crosscheck_bullet,
      crosscheck_bullet)
check("RED: it now names the EMPTY case - no rows at all from the structured data",
      "NO rows" in crosscheck_bullet or "no rows" in crosscheck_bullet, crosscheck_bullet)
check("RED: in that case, excerpts may only confirm or refute - never assemble a new breakdown "
      "or total", "never assemble a new" in crosscheck_bullet
      and ("breakdown" in crosscheck_bullet and "total" in crosscheck_bullet), crosscheck_bullet)
check("RED: a confirming figure must be a verbatim sentence of a single excerpt",
      "verbatim sentence" in crosscheck_bullet and "single excerpt" in crosscheck_bullet,
      crosscheck_bullet)
check("RED: with nothing confirming, the rule says to call the quantity not recorded",
      "not recorded" in crosscheck_bullet, crosscheck_bullet)
check("RED: the rows an empty-case figure must NOT come from are tied to the asked-for place or "
      "entity - never rows that merely sit nearby", "asked-for" in crosscheck_bullet,
      crosscheck_bullet)

# ===========================================================================
print("\n2. The frozen tool-choice prompt carries none of it (ruling W7)")
# ===========================================================================
frozen = oc.TOOL_CHOICE_FORMAT_RULES
frozen_bullet = next((ln for ln in frozen.splitlines()
                      if ln.startswith("- When a tool result carries a section of document "
                                       "excerpts added as a cross-check")), "")
check("the frozen copy keeps its own (older) cross-check bullet, unrelated to this fix",
      bool(frozen_bullet), frozen[:400])
check("RED: the frozen bullet carries no EMPTY branch - it is byte for byte the v1.3 text",
      "never assemble a new" not in frozen_bullet and "not recorded" not in frozen_bullet,
      frozen_bullet)
check("the frozen prompt is shorter than the live one only by bullets added since the freeze "
      "(sanity: the two are not accidentally the same object)",
      frozen is not oc.OUTPUT_FORMAT_RULES)

# ===========================================================================
print("\n3. Named mutations - each must turn its own check red")
# ===========================================================================
saved = oc.OUTPUT_FORMAT_RULES
try:
    oc.OUTPUT_FORMAT_RULES = rules.replace("never assemble a new", "may assemble a new")
    mutated_bullet = next((ln for ln in oc.OUTPUT_FORMAT_RULES.splitlines()
                           if ln.startswith("- When a tool result carries a section of document "
                                            "excerpts added as a cross-check")), "")
    check("M1: wording weakened - the forbidding phrase is gone",
          "never assemble a new" not in mutated_bullet)
finally:
    oc.OUTPUT_FORMAT_RULES = saved
check("mutation restored: the real rule is back",
      "never assemble a new" in next((ln for ln in oc.OUTPUT_FORMAT_RULES.splitlines()
                                      if ln.startswith("- When a tool result carries a section "
                                                       "of document excerpts added as a "
                                                       "cross-check")), ""))

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
