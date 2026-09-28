"""test_table_router_identifier.py — tier 5: a row named by its own printed identifier (2026-09-28).

THE DEFECT: questions that print one row's exact identifier — a drawing number, a part
description — never reached a small table (no value vocabulary), and several drawing
registers sharing one prefix tied at 6 and list position chose. doc-prep now writes
`identifier_values` on each card (CODED values of an identifier column with <= 60 distinct
values); this tier adds `_IDENTIFIER_VALUE_WEIGHT` when the question prints one WHOLE.

FIXTURE: neutral and inline — this repository is public, so no client table, column value or
building fact appears here (same rule as tests/fixtures/router_place_cards.json). The measured
replay over the real ruler lives in doc-prep/work_spine/router-vocab-2026-09-28.md.

Run:
    venv/Scripts/python -X utf8 tests/test_table_router_identifier.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import table_router
from app.services.table_router import select_tables

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


def card(table, columns, prefixes=(), values=(), vocab=None, ident="id"):
    return {"table": table, "columns": list(columns), "identifier_column": ident,
            "identifier_prefixes": list(prefixes), "identifier_values": list(values),
            "value_vocabulary": vocab or {}, "joins_to": [], "declared_joins": []}


DRAWINGS = [card(f"bld_om_{s}_drawings", ["drawing_no", "title"], ["DWG"],
                 [f"DWG-100-{s.upper()}-{n}" for n in ("0200", "0203")], ident="drawing_no")
            for s in ("aa", "bb", "cc", "dd")]
EXTINGUISHERS = card("bld_om_ee_asset_register", ["asset_description", "model_number"],
                     values=["Foam Extinguisher, 6 Kg", "Sand Bucket, 9 L",
                             "Mat(2.0m X 3.0m)"], ident="asset_description")
DATAPOINTS = card("bld_om_ff_data_points", ["function_description"],
                  values=["Door Chime", "Hand Dryer", "A-1", "Level - 09 - Tea Room"],
                  ident="function_description")
EQUIPMENT = card("bld_equipment", ["equipment_id", "system", "description", "model"], ["EQ"],
                 vocab={"system": ["fire", "water"],
                        "description": ["Door Chime Panel", "Hand Dryer", "Extinguisher Cabinet"]},
                 ident="equipment_id")
CARDS = DRAWINGS + [EXTINGUISHERS, DATAPOINTS, EQUIPMENT]


def tier_off(q, cards=CARDS):
    saved = table_router._IDENTIFIER_VALUE_WEIGHT
    table_router._IDENTIFIER_VALUE_WEIGHT = 0.0
    try:
        return select_tables(q, cards, k=3)
    finally:
        table_router._IDENTIFIER_VALUE_WEIGHT = saved


print("1. A printed drawing number picks its own register out of four sharing its prefix")
Q1 = "What is the title of DWG-100-CC-0203?"
r = select_tables(Q1, CARDS, k=3)
check("the register printing DWG-100-CC-0203 is ranked first", r[:1] == ["bld_om_cc_drawings"], r)
r0 = tier_off(Q1)
check("MUTATION: tier off -> the four tie on the prefix and list position picks the first",
      r0[:1] == ["bld_om_aa_drawings"], r0)

print("\n2. A small register with no vocabulary is reached when the question prints its row")
Q2 = "What is the model of Foam Extinguisher, 6 Kg?"
r = select_tables(Q2, CARDS, k=3)
check("bld_om_ee_asset_register is ranked first", r[:1] == ["bld_om_ee_asset_register"], r)
r0 = tier_off(Q2)
check("MUTATION: tier off -> the word 'extinguisher' on another card wins",
      r0[:1] == ["bld_equipment"], r0)

print("\n3. The guards")
check("an UNCODED value ('Door Chime', 'Hand Dryer') never fires",
      not table_router._names_a_row("Where is the door chime and the hand dryer?", DATAPOINTS))
check("a coded value under 4 characters never fires ('A-1')",
      not table_router._names_a_row("Is A-1 working?", DATAPOINTS))
check("a coded value of exactly 4 characters fires ('QX12')",
      table_router._names_a_row(
          "Is QX12 in stock?",
          card("bld_om_gg_parts", ["part_code"], values=["QX12"], ident="part_code")))
check("a value that names a place never fires — the place tier owns places",
      not table_router._names_a_row("What is in Level - 09 - Tea Room?", DATAPOINTS))
check("bounded: a longer code that only starts with the value does not fire",
      not table_router._names_a_row("What is DWG-100-CC-02031?", DRAWINGS[2]))
check("case and spacing do not matter",
      table_router._names_a_row("model of  foam EXTINGUISHER, 6 kg", EXTINGUISHERS))
check("parentheses inside a value match as printed",
      table_router._names_a_row("What is the model of Mat(2.0m X 3.0m)?", EXTINGUISHERS))
check("a card without the field names no row",
      not table_router._names_a_row(Q1, {k: v for k, v in DRAWINGS[2].items()
                                          if k != "identifier_values"}))

print("\n4. Old cards and unrelated questions are untouched")
OLD = [{k: v for k, v in c.items() if k != "identifier_values"} for c in CARDS]
for q in (Q1, Q2, "Which registers list fire equipment?"):
    check(f"cards without identifier_values route exactly as with the tier off: {q[:40]}",
          select_tables(q, OLD, k=3) == tier_off(q), (select_tables(q, OLD, k=3), tier_off(q)))
Q4 = "Which registers list fire equipment?"
check("a question printing no identifier is unaffected by the tier",
      select_tables(Q4, CARDS, k=3) == tier_off(Q4))

print("\n5. Weight and determinism")
check("the tier weighs exactly what a prefix match weighs (6)",
      table_router._IDENTIFIER_VALUE_WEIGHT == 6.0)
check("deterministic", len({tuple(select_tables(Q1, CARDS, k=3)) for _ in range(3)}) == 1)

print("\n6. Blind cards: a code also claimable by a card that lists none does not fire")
PANELS = card("bld_panels", ["panel", "demand_kw"], ["PNL"], [], ident="panel")          # too long to list
# OLD_PANELS declares a SECOND prefix ('OLDP') that PANELS does not, so the next check is
# discriminating: an implementation that (wrongly) adds every card's prefixes to the blind set,
# not just a BLIND card's, would yield {'PNL', 'OLDP'} here, not {'PNL'}.
OLD_PANELS = card("bld_old_panels", ["panel", "demand_kw"], ["PNL", "OLDP"], ["PNL-X-G7"], ident="panel")
Q6 = "What is the maximum demand of PNL-X-G7?"
r = select_tables(Q6, [PANELS, OLD_PANELS], k=1)
check("the register that cannot list its boards is not out-ranked by one that can", r[:1] == ["bld_panels"], r)
check("_blind_prefixes names the prefix of a card listing no values", table_router._blind_prefixes([PANELS, OLD_PANELS]) == {"PNL"})
check("_names_a_row honours the blind set", not table_router._names_a_row(Q6, OLD_PANELS, {"PNL"}) and table_router._names_a_row(Q6, OLD_PANELS))
saved_blind = table_router._blind_prefixes
table_router._blind_prefixes = lambda cards: set()
try:
    r0 = select_tables(Q6, [PANELS, OLD_PANELS], k=1)
finally:
    table_router._blind_prefixes = saved_blind
check("MUTATION: with no blind set the listing card wins", r0[:1] == ["bld_old_panels"], r0)
check("a code whose head is not a word ('X100-IRS') has no head to be blind on", table_router._identifier_head("X100-IRS") is None and table_router._identifier_head("PNL-X-G7") == "PNL")

print("\n7. Shared codes: the weight is split among the cards the identifier matches")
SPECS = card("bld_specs", ["parameter", "value"], [], [], vocab={"parameter": ["Lens", "Resolution"]}, ident="value")
MODEL_TABLES = [card(f"bld_model_{s}", ["model_no", "qty"], [], ["X100-IRS"], ident="model_no") for s in ("counts", "selection", "spares")]
Q7 = "What lens does camera model X100-IRS have?"
r = select_tables(Q7, MODEL_TABLES + [SPECS], k=3)
check("the specification table stays in the ranked top three", "bld_specs" in r[:3], r)
saved_w = table_router._IDENTIFIER_VALUE_WEIGHT
table_router._IDENTIFIER_VALUE_WEIGHT = saved_w * 3   # what each of three matches would get undivided
try:
    r0 = select_tables(Q7, MODEL_TABLES + [SPECS], k=3)
finally:
    table_router._IDENTIFIER_VALUE_WEIGHT = saved_w
check("MUTATION: undivided (x3), the three model tables push it out", "bld_specs" not in r0[:3], r0)
c0 = MODEL_TABLES[0]
check("_score adds 6 / row_share", abs(table_router._score(set(), set(), c0, {}, {id(c0): (set(), set())}, names_row=True, row_share=3) - 2.0) < 1e-9)

print("\n8. Enumerated columns (A1): tier 5 also reads value_vocabulary, not just identifier_values")
# THE DEFECT this covers: a card can hold the asked-for code in a value_vocabulary column
# rather than its identifier column. _vocabulary_values(card) is its own small helper (per the
# brief) precisely so it can be switched off here and the effect proven, not just asserted.
VOCAB_ONLY = card("bld_spares", ["part_code", "note"], values=[],
                  vocab={"code": ["ZT-42"]}, ident="part_code")
check("_names_a_row fires on a coded value that lives only in value_vocabulary",
      table_router._names_a_row("Is ZT-42 still under warranty?", VOCAB_ONLY))

VOCAB_GUARD = card("bld_notes", ["note"], values=[],
                   vocab={"code": ["Spare Unit", "Level - 09 - Tea Room"]}, ident="note")
check("an UNCODED value that lives only in value_vocabulary never fires",
      not table_router._names_a_row("Is the Spare Unit available?", VOCAB_GUARD))
check("a place-shaped value that lives only in value_vocabulary never fires",
      not table_router._names_a_row("What is in Level - 09 - Tea Room?", VOCAB_GUARD))

DUP = card("bld_dup", ["code"], values=["ZT-42"], vocab={"alt": ["ZT-42"]}, ident="code")
check("de-duplicated: a value in both identifier_values and value_vocabulary yields one phrase",
      table_router._identifier_phrases(DUP).count("zt-42") == 1, table_router._identifier_phrases(DUP))

# END TO END. SPECS2 holds its code ('7734') ONLY in an enumerated column, against three cards
# that outrank an unaided SPECS2 on ordinary word overlap. '7734' is deliberately a bare number:
# _meaningful_words treats a pure-digit token as an ordinal and drops it (see _ORDINAL_RE), so it
# cannot also score on tiers 2/3 as a WORD — this fixture isolates tier 5's own contribution from
# the pre-existing word-vocabulary tier, which a hyphenated/lettered code (e.g. 'X100-IRS', used
# in section 7) would not do cleanly, since it also tokenises into a word.
#
# ARITHMETIC, worked by hand (tiers: prefix 6; vocabulary word 3/df; subject word 1/df; tier 5
# = 6 / cards matched):
#   NOISE cards (bld_noise_aa/bb/cc): each shares the word 'camera' with the question via its
#     OWN value_vocabulary (tier 2, weight 3, df=3 — only these three cards carry it anywhere,
#     in subject or vocab words) = 1.0 each. Nothing here touches _vocabulary_values (tier 5's
#     own helper), so this 1.0 is identical in both states below.
#   SPECS2 (bld_specs2): no word it carries (table name, columns, or the vocab key 'spec_value')
#     overlaps the question at all, and '7734' itself is filtered as an ordinal -> 0 from tiers
#     1-4 in BOTH states.
#   SPECS2 tier 5, vocabulary ON: '7734' is CODED (has a digit), exactly 4 chars (the minimum,
#     inclusive), not place-shaped -> a valid phrase. It is the ONLY card anywhere (identifier
#     OR vocabulary) that prints it, so row_share = 1 and it takes the undivided weight: 6 / 1 =
#     6.0. Total = 0 + 6.0 = 6.0 -> ranks 1st of 4, safely inside k=3.
#   SPECS2 tier 5, vocabulary OFF (_vocabulary_values patched to return []): no source contains
#     the code at all (identifier_values is also empty) -> tier 5 contributes 0. Total = 0.0,
#     behind all three NOISE cards (1.0 each) -> excluded from k=3 (exactly one of the four cards
#     is cut, and it is SPECS2).
SPECS2 = card("bld_specs2", ["parameter", "detail"], values=[],
             vocab={"spec_value": ["7734"]}, ident="parameter")
NOISE = [card(f"bld_noise_{s}", ["reading", "note"], values=[],
             vocab={"note": ["camera"]}, ident="reading")
         for s in ("aa", "bb", "cc")]
Q8 = "Which register names the camera holding part 7734?"
r = select_tables(Q8, [SPECS2] + NOISE, k=3)
check("the card holding its code only in value_vocabulary keeps a ranked top-three slot",
      "bld_specs2" in r[:3], r)
saved_vocab = table_router._vocabulary_values
table_router._vocabulary_values = lambda card: []
try:
    r0 = select_tables(Q8, [SPECS2] + NOISE, k=3)
finally:
    table_router._vocabulary_values = saved_vocab
check("MUTATION: vocabulary source switched off -> it loses that ranked top-three slot",
      "bld_specs2" not in r0[:3], r0)

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
