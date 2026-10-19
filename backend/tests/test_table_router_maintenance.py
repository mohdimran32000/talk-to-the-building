"""test_table_router_maintenance.py — the maintenance-schedule guard (task W12-A1).

THE DEFECT THIS EXISTS FOR — measured, not hypothesised
An isolated frequency question that names a device by its own coded tag ("how often
should <tag> be serviced?") gives that device's OWN tables a prefix-match bonus and
leaves the small maintenance-schedule table that actually holds the answer unranked:
measured on the real cards, it comes in around 32nd of 71. No amount of vocabulary work
fixes this, because the question never shares a word with the schedule table at all — it
names the DEVICE, not the schedule.

WHAT THE FIX IS, same two-part shape as the existing CHANGE GUARD in `table_router.py`:
  * a SHARED predicate, `asks_about_maintenance_frequency` — word-bounded wording signal
    ('how often', 'frequen*', 'maint(ain|enance)*', 'inspect*', 'service interval*'), used
    here AND by `openai_client.py`'s plan-first step;
  * a STRUCTURAL card test, `_card_is_maintenance_schedule` — a task-like column (`task` or
    `activity`) against a period (`frequency`, or a column named for the period itself),
    and NOT already placeable;
  * an APPEND GUARD in `select_tables`, after the ranked picks and after the change guard,
    never displacing either.

FIXTURE, FULLY INVENTED (public repo) — no table, column, value, card id, building or
question text of the real project appears anywhere below; every table/column name here
describes a building that does not exist.

Run:
    venv/Scripts/python -X utf8 tests/test_table_router_maintenance.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import table_router  # noqa: E402
from app.services.table_router import select_tables  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


def card(table, columns, *, placeable=False, declared_joins=None, joins_to=None,
         identifier_prefixes=(), vocab=None, ident=None):
    """A minimal invented card, shaped like the real ones but naming nothing real.
    `placeable=True` adds a `location_id` column (`_card_is_placeable`'s simplest arm)."""
    cols = list(columns)
    if placeable and "location_id" not in cols:
        cols = cols + ["location_id"]
    return {
        "table": table,
        "columns": cols,
        "identifier_column": ident or (cols[0] if cols else "id"),
        "identifier_prefixes": list(identifier_prefixes),
        "identifier_values": [],
        "value_vocabulary": vocab or {},
        "joins_to": list(joins_to or []),
        "declared_joins": list(declared_joins or []),
    }


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
FREQ_ACTIVITY_CARD = card("bld_maint_aa_schedule", ["activity", "frequency", "notes"])
TASK_PERIOD_CARD = card("bld_maint_bb_schedule", ["task", "monthly_due", "notes"])
PLACEABLE_TASK_CARD = card("bld_maint_cc_schedule", ["task", "frequency", "notes"],
                           placeable=True)
FREQUENCY_NO_TASK_CARD = card("bld_readings_dd", ["reading", "frequency", "notes"])
DEVICE_CARD = card("bld_devices_ee", ["device_tag", "model", "notes"],
                   identifier_prefixes=["DEV"])
UNRELATED_CARD = card("bld_widgets_ff", ["widget_id", "colour"], identifier_prefixes=["WDG"])
GRAPH_CARD = card("bld_relationships_gg", ["subject_id", "predicate", "object_id"])

CARDS = [DEVICE_CARD, UNRELATED_CARD, FREQ_ACTIVITY_CARD, TASK_PERIOD_CARD,
         PLACEABLE_TASK_CARD, FREQUENCY_NO_TASK_CARD]


# ===========================================================================
print("1. asks_about_maintenance_frequency — positives and negatives")
# ===========================================================================
POSITIVES = [
    "How often should the unit be serviced?",
    "What is the inspection frequency for this device?",
    "Is the equipment properly maintained?",
    "What maintenance does the manual prescribe?",
    "When was the last inspection?",
    "What is the service interval for this pump?",
    "It needs frequent attention.",   # 'frequent' is fine to match, per the brief
]
for q in POSITIVES:
    check(f"positive: {q!r}", table_router.asks_about_maintenance_frequency(q))

NEGATIVES = [
    "Please check the invoice total.",
    "Can you check my work?",
]
for q in NEGATIVES:
    check(f"negative (a plain 'check' question, deliberately NOT matched): {q!r}",
          not table_router.asks_about_maintenance_frequency(q))

check("decided and pinned: 'maintainer' DOES fire — it is the literal "
      "maint(?:ain|enance)\\w* stem used verbatim, and over-firing here is cheap (see the "
      "predicate's own docstring for why this is a deliberate choice, not an oversight)",
      table_router.asks_about_maintenance_frequency("Who is the maintainer of this plant?"))

check("empty/None text never raises and is simply not a match",
      table_router.asks_about_maintenance_frequency("") is False
      and table_router.asks_about_maintenance_frequency(None) is False)


# ===========================================================================
print("\n2. _card_is_maintenance_schedule — the structural predicate")
# ===========================================================================
check("activity + frequency -> True",
      table_router._card_is_maintenance_schedule(FREQ_ACTIVITY_CARD))
check("task + a period column (not literally named 'frequency') -> True",
      table_router._card_is_maintenance_schedule(TASK_PERIOD_CARD))
check("task + frequency but ALSO placeable -> False (tier 4 already reaches it)",
      not table_router._card_is_maintenance_schedule(PLACEABLE_TASK_CARD))
check("a frequency column with NO task-like column -> False",
      not table_router._card_is_maintenance_schedule(FREQUENCY_NO_TASK_CARD))
check("neither column at all -> False",
      not table_router._card_is_maintenance_schedule(DEVICE_CARD))
check("a bare 'activity' with no period signal at all -> False",
      not table_router._card_is_maintenance_schedule(
          card("bld_log_hh", ["activity", "notes"])))


# ===========================================================================
print("\n3. select_tables — the guard appends on a frequency question")
# ===========================================================================
Q_FREQ_DEVICE = "How often should DEV-100 be serviced?"
r = select_tables(Q_FREQ_DEVICE, CARDS, k=3)
check("the device's own table is still ranked (tag-prefix match)", "bld_devices_ee" in r, r)
check("the activity+frequency schedule was appended even though it shares no words with "
      "the question", "bld_maint_aa_schedule" in r, r)
check("the task+period-column schedule was ALSO appended", "bld_maint_bb_schedule" in r, r)
check("the placeable task+frequency card was NOT appended (tier 4's job, not this guard's)",
      "bld_maint_cc_schedule" not in r, r)
check("the frequency-only (no task-like column) card was NOT appended",
      "bld_readings_dd" not in r, r)


# ===========================================================================
print("\n4. nothing is appended on a non-frequency question")
# ===========================================================================
Q_PLAIN = "What is the model of DEV-100?"
r2 = select_tables(Q_PLAIN, CARDS, k=3)
check("neither maintenance-schedule card is appended on a question that never asks "
      "about frequency",
      "bld_maint_aa_schedule" not in r2 and "bld_maint_bb_schedule" not in r2
      and "bld_maint_cc_schedule" not in r2 and "bld_readings_dd" not in r2, r2)


# ===========================================================================
print("\n5. the guard never displaces a ranked pick")
# ===========================================================================
r3 = select_tables(Q_FREQ_DEVICE, CARDS, k=1)
check("k=1's own ranked pick is still the device table, first",
      r3[0] == "bld_devices_ee", r3)
check("the appended schedules land AFTER the k=1 ranked pick, never instead of it",
      "bld_maint_aa_schedule" in r3[1:] and "bld_maint_bb_schedule" in r3[1:], r3)


# ===========================================================================
print("\n6. deterministic order: appended in `cards` order")
# ===========================================================================
CARDS_REORDERED = [TASK_PERIOD_CARD, FREQ_ACTIVITY_CARD, DEVICE_CARD, UNRELATED_CARD]
rA = select_tables(Q_FREQ_DEVICE, CARDS, k=3)
rB = select_tables(Q_FREQ_DEVICE, CARDS_REORDERED, k=3)
idx_cards = [n for n in rA if n in ("bld_maint_aa_schedule", "bld_maint_bb_schedule")]
check("CARDS order: activity+frequency (earlier in `cards`) is appended before task+period",
      idx_cards == ["bld_maint_aa_schedule", "bld_maint_bb_schedule"], idx_cards)
idx_reordered = [n for n in rB if n in ("bld_maint_aa_schedule", "bld_maint_bb_schedule")]
check("CARDS_REORDERED order: task+period (earlier there) is appended first instead",
      idx_reordered == ["bld_maint_bb_schedule", "bld_maint_aa_schedule"], idx_reordered)
r_repeat = select_tables(Q_FREQ_DEVICE, CARDS, k=3)
check("identical (question, cards, k) -> identical result, every call", rA == r_repeat,
      (rA, r_repeat))


# ===========================================================================
print("\n7. stacks correctly after the change guard")
# ===========================================================================
Q_CHANGE_AND_FREQ = "If I replace DEV-100, how often will the new unit need servicing?"
CARDS_WITH_GRAPH = CARDS + [GRAPH_CARD]
r4 = select_tables(Q_CHANGE_AND_FREQ, CARDS_WITH_GRAPH, k=1)
check("the ranked pick is still the device table, first",
      r4[0] == "bld_devices_ee", r4)
check("the change guard's graph card, then the maintenance guard's two schedules, are "
      "appended in that order after the one ranked pick",
      r4 == ["bld_devices_ee", "bld_relationships_gg",
             "bld_maint_aa_schedule", "bld_maint_bb_schedule"], r4)


# ===========================================================================
print("\n8. MUTATION: without the guard, the schedule would not reach the ranked top 3 at "
     "all (vocabulary alone cannot find it)")
# ===========================================================================
def _guard_off(q, cards, k=3):
    saved = table_router.asks_about_maintenance_frequency
    table_router.asks_about_maintenance_frequency = lambda _q: False
    try:
        return select_tables(q, cards, k=k)
    finally:
        table_router.asks_about_maintenance_frequency = saved


r_off = _guard_off(Q_FREQ_DEVICE, CARDS, k=3)
check("MUTATION: guard off -> neither schedule reaches the ranked top 3 at all",
      "bld_maint_aa_schedule" not in r_off and "bld_maint_bb_schedule" not in r_off, r_off)
r_on = select_tables(Q_FREQ_DEVICE, CARDS, k=3)
check("control: guard restored -> both schedules are back",
      "bld_maint_aa_schedule" in r_on and "bld_maint_bb_schedule" in r_on, r_on)


print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
