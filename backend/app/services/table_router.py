"""table_router.py — pick the few tables a question actually needs.

WHY THIS EXISTS
`sql_tool.execute_sql_query` sends every table's schema to the model on every
question: 12,753 input tokens at 28 tables, ~455,000 projected at 1,000
(doc-prep/CLAUDE.md, 2026-08-19; measured again in
`doc-prep/12_scale_probe.py`, 2026-08-30). That grows linearly with the
corpus and is the single thing standing between this design and the owner's
~1,000-document target. `select_tables` below is what a flagged
`execute_sql_query` calls instead of describing every table: it returns a
short, ranked list of table names, and the caller sends schema only for
those.

INPUT: table cards, not a live embedding index
`select_tables` takes `cards` — the list of dicts `doc-prep/11_table_cards.py`
builds deterministically from the manifests and CSVs already on disk
(`doc-prep/eval/table_cards.json`). Every field on a card already traces to
verified text; nothing here adds anything else. Card shape (see
`11_table_cards.py` for how each field was computed):
    table, document, row_count, columns, identifier_column,
    identifier_prefixes, one_row_is, holds, value_vocabulary, joins_to,
    caveats

WHY LEXICAL, NOT EMBEDDING, SCORING
The obvious design is "embed every card once, embed the question, take the
nearest top-k." That adds a network call and non-determinism to a function
this project needs to unit-test byte-exactly (the neighbour rule guards a
known-correct 47.39 kW answer — ls-014 — and that guard has to be provable
without a live API key). `select_tables` instead scores each card against
the question with plain token overlap over three tiers that mirror exactly
what a card carries:

  1. identifier-prefix match (weight 6) — the question names a real entity
     whose ID shape is on record, e.g. 'DB-05(B)-SP-01' starts with a
     prefix hwu_panels declared from its own data ('DB').
  2. value-vocabulary match (weight 3) — the question uses a word that is a
     literal enumerated value in some column, e.g. 'FCU' is one of
     hwu_db_circuits.load_type's <=8 distinct values.
  3. subject/column-name match (weight 1) — plain word overlap against the
     table name (de-prefixed) and its column names.
  4. place signal (weight 4, +1) — the question names a PLACE in the
     building, so every card whose rows can be put in a place is worth
     looking at even when its subject words say nothing, and a card that
     declares it resolves rows as far as a single ROOM is worth a little
     more when the question names a room by its code. See
     `_PLACE_SIGNAL_RES` below for the shapes and the defect this tier
     exists for. A place named only by the NAME printed on its door is
     recognised too — off the cards, never from a list in this file; see
     `_place_names` and the tie-break in `select_tables`.
The change signal is NOT a fifth tier — it is a guard, like the neighbour
rule below. See `_CHANGE_VERB_RES` and the change-guard block in
`select_tables`.

This is a deliberate, reported simplification versus the blueprint's
"embed the cards" sketch (see task-8-report.md) — swapping in a real
embedding call later means changing the scoring function's body, not this
module's contract (`select_tables(question, cards, k) -> list[str]`).

THE NEIGHBOUR RULE IS NOT OPTIONAL
Whatever scores highest, every selected table's declared `joins_to` list is
appended too. `ls-014` needs `hwu_panels` AND `hwu_smdb_feeders` in the same
schema block — a router that finds `hwu_panels` and stops there produces a
confidently wrong per-phase answer, because child boards print blank totals
in the panel schedule and the real figure lives only in the feeder schedule.

NEITHER IS THE CHANGE GUARD, AND IT IS A GUARD RATHER THAN A TIER BECAUSE
THAT WAS MEASURED. A change-impact question ("can I replace X", "what would
this affect") is answered by the DEPENDENCY GRAPH, so every card whose
columns are a graph edge is appended for one, exactly like a join neighbour.
It was first built as a fifth SCORING tier (weight 3.5, below the
identifier-prefix tier) and that version was replayed over all 183 eval
questions offline: it could not reach k=3 for the very questions it exists
for — a question naming an asset by its tag gives three of that system's own
tables a 6 apiece — and it COST one question the table holding its answer
(whh-001, "which company supplied and installed the 30 litre water heaters?"
lost `hwu_om_waterheaters_asset_register` when the graph card displaced it).
Ranking was the wrong instrument: `sql_tool` uses this list as a SET, so
"is the graph in play" is a correctness question, not a relevance one.
Appending instead of promoting makes the guard strictly additive — it can
never cost a question a table it selects today, which is what the replay
then measured (18 changed, 0 lost).
"""
import re

# ---------------------------------------------------------------------------
# TIER 4 — THE PLACE SIGNAL
#
# THE DEFECT, measured from a LangSmith trace on 2026-09-14
# "what are the assets inside classroom 4.04?" selected
# `hwu_om_acs_asset_register`, `hwu_om_bms_asset_register` and
# `hwu_om_cctv_asset_register` — three O&M *supply lists*, none of which has a
# room column at all — purely because "assets" overlaps their subject words.
# The tables whose rows actually carry `location_id = 'RM-4.04'`
# (`hwu_equipment`, `hwu_equipment_counts`) scored zero, since nothing in the
# question overlaps the words "equipment" or "counts". The SQL found nothing,
# the app fell back to text search and stated "8 LCS devices"; the truth for
# that room is ACS 6 · fire alarm 5 · LCS 4 · central battery 5.
#
# Tiers 1-3 cannot express this. A room number is not a word the question
# shares with a card — it is a VALUE that only exists in a location column.
# So: when the question names a place, the fact that a card carries a location
# column is itself the evidence.
#
# WHERE THE SHAPES COME FROM — the building's own room numbering, on record in
# the spine tables, never invented here:
#   `4.04`, `G.21`   -> hwu_locations.room_number   (floor letter/digit . 2 digits)
#   `EX-00-055`      -> hwu_locations.legacy_number (the room master's own tags)
#   `RM-4.04`        -> hwu_locations.location_id
#   `D01-256`        -> the architects' design tags, also in `legacy_number`
#   `6F`, `L04`      -> the floor codes printed on asset registers and camera tags
#   level/floor/block/room/basement/roof/ground -> hwu_locations.kind and
#                       level_name, i.e. a place named in words rather than code
# A SECOND BUILDING IS A DIFFERENT LIST. These are lexical shapes, not data:
# nothing here reads a card, a CSV or the database, and no card content is
# changed by the app. When this router is pointed at another project the list
# is what gets re-derived from that project's own `hwu_locations` equivalent.
# ---------------------------------------------------------------------------
_ROOM_CODE_RES = [
    re.compile(r"\b[G1-6]\.\d{2}\b"),      # room_number: G.21, 4.04
    re.compile(r"\bEX-\d{2}-\d{3}\b"),     # legacy_number: EX-00-055
    re.compile(r"\bRM-\d"),                # location_id: RM-4.04
    re.compile(r"\bD0[0-6]-\d{3}\b"),      # design tag: D01-256
]
_PLACE_WORD_RES = [
    re.compile(r"\b(level|floor|block|room|basement|roof|ground)\b", re.IGNORECASE),
    re.compile(r"\b[1-6]F\b", re.IGNORECASE),    # 6F — the camera-tag floor code
    re.compile(r"\bL0[0-6]\b", re.IGNORECASE),   # L04 — the drawings' level code
]
_PLACE_SIGNAL_RES = _ROOM_CODE_RES + _PLACE_WORD_RES

# The two spine columns that make a row placeable. `wired_to_location_id` is
# the CCTV camera register's: a camera tag names the IDF room it is wired to,
# not the room it hangs in (doc-prep/CLAUDE.md, 2026-09-08).
_LOCATION_COLUMNS = frozenset({"location_id", "wired_to_location_id"})

# Deliberately between the value-vocabulary tier (3) and the identifier-prefix
# tier (6): a question that names an actual entity — 'DB-05(B)-SP-01' — must
# still put that entity's table first even when it also names a floor. The two
# weights below therefore have to sum to less than 6; `test_table_router_place`
# asserts that rather than trusting the comment.
_PLACE_WEIGHT = 4.0

# WHY A SECOND, SMALLER WEIGHT EXISTS — measured, not designed in the abstract.
# `_PLACE_WEIGHT` alone does not fix the reported defect. 27 of the 67 live
# cards are placeable, so a flat +4 leaves them all tied at 4.00 and
# the tie is broken by card order; `hwu_equipment` landed 5th and
# `hwu_equipment_counts` 6th, behind three cards that happened to score 0.12 of
# subject overlap on "assets". What separates them is on the cards already:
# `location_resolution` is a spine column whose enumerated values state the
# granularity a table's rows actually resolve to. Only `hwu_equipment`,
# `hwu_equipment_counts` and `hwu_om_acs_doors` declare `room`; the LV,
# water-heater and zip-tap registers declare `via_board`, `unresolved`, or
# nothing. So when the question names a ROOM — not merely a floor or a block —
# a card that says its rows reach room granularity is worth a little more than
# one that can only reach a level. A card that declares no resolution at all is
# NOT penalised below the base tier: absence of evidence is not evidence
# against (doc-prep/CLAUDE.md §4).
#
# AND ONLY ON A ROOM *CODE*, not on the word "room". Measured the same way, by
# replaying all 183 eval questions and asking whether each one's own recorded
# evidence table was selected: firing this refinement on the bare word "room"
# costs a question its evidence table — "which board powers the ICT rack in the
# first-floor Block B server room?" loses `hwu_db_circuits`, whose rows resolve
# `via_board` and so score 4.00 against 5.00 for room-resolving tables that
# never held the answer. The distinction is real, not a patch: '4.04' is a
# VALUE that exists nowhere but a room column, whereas "the server room" is a
# printed room NAME, and room names are printed in ordinary text columns all
# over this corpus (`room_area`, `camera_location`, the fire enclosure test
# sheets). A name is not a key.
_ROOM_GRANULARITY_WEIGHT = 1.0
_ROOM_RESOLUTION_VALUE = "room"

# ---------------------------------------------------------------------------
# THE CHANGE GUARD
#
# THE DEFECT: "can I replace camera CCTV-L6B-S-IDF-1?" is not a question about
# cameras, it is a question about that camera's DEPENDENCIES — what feeds it,
# what records it, what is backed up with it, what else sits in the same room.
# Tiers 1-3 route it to the camera tables (the tag's prefix scores 6) and stop
# there, so the schema block that reaches the SQL model holds the asset and
# none of its links, and the app answers with a generic replacement checklist
# written from general knowledge. Tier 4 does not fire either: a tag is not a
# place.
#
# The signal is the QUESTION'S VERB, and the evidence on the card is its COLUMN
# SHAPE. A table whose rows are (subject id, predicate, object id) is a
# dependency graph — one row is one link between two things — whatever it is
# called and whatever building it describes. That shape is the doc-prep spine's
# own contract (doc-prep/out/hwu-spine/hwu_spine_manifest.md: "walk up from any
# asset on `object_id`, down on `subject_id`"), and it is read off the card's
# `columns`, exactly like `_LOCATION_COLUMNS` in tier 4: no table name is
# matched anywhere in this module, so a second building with a differently
# named graph table is routed by the same rule.
#
# A SECOND BUILDING IS THE SAME LIST: unlike `_PLACE_SIGNAL_RES` (which encodes
# one building's room-numbering shapes), these are plain English change verbs
# and carry nothing project-specific.
#
# WHY THE VERB ALONE IS NOT ENOUGH — measured over the 183 eval questions, not
# reasoned about. A first draft of this list included `install\w*`, and in a
# corpus of asset registers "installed" is one of the commonest words there is:
# it fired on "how many CCTV cameras are installed in total?", "where are the
# 30 litre water heaters installed?" and 6 more plain factual questions, each of
# which would have carried the graph table and its 7 join neighbours into the
# schema block for nothing. A check that cries wolf does not ship
# (doc-prep/CLAUDE.md §12). Two fixes, both measured: `install` is gone (it is
# not a change — it is how every asset in the corpus got there), and a plain
# change VERB must also NAME something — a place, or an entity by its coded tag
# — before the guard fires. An explicit IMPACT word ("what would this affect",
# "what depends on it") is self-evidently a graph question and needs no second
# signal.
_CHANGE_VERB_RES = [
    re.compile(r"\b(replac\w*|swap\w*|substitut\w*|chang\w*|upgrad\w*|retrofit\w*"
               r"|add|adds|adding|remov\w*|decommission\w*|disconnect\w*"
               r"|relocat\w*|modif\w*|compatib\w*|interchang\w*)\b", re.IGNORECASE),
]
_IMPACT_WORD_RES = [
    re.compile(r"\b(affect\w*|impact\w*|knock-on|depend\w*|downstream|upstream)\b",
               re.IGNORECASE),
]

# The edge shape. All three are required: a table with only an `object_id` is a
# foreign key, not a graph — it names one end and cannot be walked backwards.
_GRAPH_EDGE_COLUMNS = frozenset({"subject_id", "predicate", "object_id"})

_STOPWORDS = {
    "the", "a", "an", "of", "is", "are", "what", "which", "how", "many",
    "for", "on", "in", "to", "and", "or", "was", "were", "does", "do",
    "give", "me", "please", "with", "at", "by", "from", "its", "this",
    "that", "there", "has", "have", "list", "show",
    # This corpus is ONE building, so 'building' (and its synonyms) carries no
    # routing information — every table is "in the building". Measured
    # defect, 2026-09-16: "what are the various firefighting assets we have
    # in the building?" ranked `hwu_locations`/`hwu_location_aliases` first
    # (0.57 each) purely because 'building' is a literal column name on those
    # spine cards (holding B/C values), beating `hwu_equipment` (0.43, from
    # the vocabulary value 'firefighting') and the firefighting asset
    # register (0.40). The SQL step then queried only the rooms table and
    # answered with fire lobbies instead of sprinklers/extinguishers/NAF 227
    # units. A second, multi-building corpus would need this word back —
    # it is a fact about THIS deployment, not a fact about the router.
    "building", "campus", "site", "premises", "facility",
}

_TOKEN_RE = re.compile(r"[A-Za-z0-9()][A-Za-z0-9()\-]*")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text or "")


_ORDINAL_RE = re.compile(r"^\d+(st|nd|rd|th)?$")


def _norm_word(tok: str) -> str:
    w = tok.lower()
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]  # crude plural fold: 'FCUs' -> 'fcu', 'cameras' -> 'camera'
    return w


def _meaningful_words(tokens: list[str]) -> set[str]:
    """Shared word filter for BOTH the question and every card field, so a
    word is judged the same way on both sides of the match. Drops
    stopwords, single characters (too many coincidental collisions — 'B'
    for Block B matched almost every card's phase/status/block enum), and
    bare ordinals/numbers ('5th', '2', '01') — a floor number is not
    evidence a table is about floors; several unrelated tables in this
    corpus (CCTV floor levels, water-heater locations, panel floors) all
    print one, so on its own it is close to pure noise. The load-bearing
    'FCU' find (ls-008) survives this filter untouched; the false-positive
    'floor'/'5th' pull toward `hwu_om_cctv_camera_schedule` shrinks a lot
    (found by inspecting real scores while building this router)."""
    out = set()
    for t in tokens:
        if t.lower() in _STOPWORDS:
            continue
        w = _norm_word(t)
        if len(w) < 2 or _ORDINAL_RE.match(w):
            continue
        out.add(w)
    return out


def _question_words(question: str) -> set[str]:
    return _meaningful_words(_tokenize(question))


def _question_prefixes(question: str) -> set[str]:
    """Upper-cased lead token of every hyphenated/coded word in the
    question, e.g. 'DB-05(B)-SP-01' -> 'DB', 'CCTV-L6B-S-IDF-1' -> 'CCTV'."""
    out = set()
    for tok in _tokenize(question):
        if "-" not in tok:
            continue
        head = tok.split("-", 1)[0].strip().upper()
        if head.isalpha() and len(head) > 1:
            out.add(head)
    return out


def _names_a_place(question: str) -> bool:
    """True when the question names somewhere in the building — a room
    number, a legacy or design tag, a location_id, a floor code, or the
    plain words 'level'/'floor'/'block'/'room'/'basement'/'roof'/'ground'.

    Matched against the RAW question, never the tokens: `_tokenize` splits on
    the '.' in '4.04', so a room number does not survive as a token. Note the
    shapes are anchored on word boundaries for the same reason — '47.39 kW'
    must not read as room 7.39, and `test_table_router_place` pins that."""
    return any(rx.search(question or "") for rx in _PLACE_SIGNAL_RES)


# ---------------------------------------------------------------------------
# TIER 4b — A PLACE NAMED BY ITS PRINTED NAME
#
# THE DEFECT, measured on the ruler 2026-09-18: "What assets are in the Energy
# Laboratory?" names a room and nothing but a room, and `_names_a_place` was
# silent — "Energy Laboratory" is neither a code shape nor one of the seven
# place WORDS above. With the tier off, ten cards tied at 0.1111 for the third
# routed slot and the tie fell to list position, so `hwu_om_acs_asset_register`
# — a supply list with no location column at all — won it and the query
# returned 0 rows. Four ruler cards (ex-038, ex-042, ex-044, ex-045) failed
# that way, and the same shape had been misread once already as a card-ordering
# bug: ordering the read made it reproducible, not right.
#
# WHY THE NAMES COME OFF THE CARDS. `_PLACE_SIGNAL_RES` above is a list of
# lexical SHAPES, which is why it can sit in this module: '4.04' is a pattern,
# and a second building re-derives the pattern. A room's printed NAME is not a
# pattern — it is that building's own room register, i.e. data — and this
# module contains no table name, no column value and no building fact anywhere.
# So the names travel here the way every other card fact does: on a card.
# `doc-prep/11_table_cards.py` writes `place_names` onto the card of the table
# whose identifier column IS the location key (its one row is one place), from
# that table's own `*_name` columns, keeping only multi-word, digit-free values.
# A card set published before that field existed simply has no names and this
# tier stays silent — the old behaviour exactly, which `test_table_router_place`
# pins.
#
# WHY A WHOLE PHRASE, NEVER A WORD. The names are matched as bounded phrases
# and are deliberately NOT folded into the scored vocabulary: that tier is
# DF-weighted and matched word by word, and 'Energy', 'Central' or 'Store' as
# routing words would be noise on questions that name no place at all. Two
# words minimum for the same reason — this register also prints 'Store',
# 'Library' and 'Studio'.
# ---------------------------------------------------------------------------
_PLACE_NAME_MIN_WORDS = 2


def _place_names(cards: list[dict]) -> set:
    """Every printed place name the CARDS carry, lower-cased.

    Read off `place_names`, exactly like `_LOCATION_COLUMNS` is read off
    `columns`: no name list lives in this module. Cards that do not carry the
    field (an older publish) contribute nothing and the tier stays silent."""
    names = set()
    for card in cards or []:
        for n in card.get("place_names") or []:
            n = str(n).strip().lower()
            if n and len(n.split()) >= _PLACE_NAME_MIN_WORDS:
                names.add(n)
    return names


def _contains_phrase(haystack_lower: str, phrase_lower: str) -> bool:
    """True when `phrase_lower` occurs in `haystack_lower` bounded on BOTH
    sides by something that is not a letter, a digit or an underscore.

    Bounded, not a bare `in`: 'Town Hall' must fire on "the Town Hall" and stay
    silent on "how many Town Halls are there?" — a plural is a different thing
    being asked about, not that room."""
    i = haystack_lower.find(phrase_lower)
    n = len(phrase_lower)
    while i != -1:
        before = haystack_lower[i - 1] if i else " "
        after = haystack_lower[i + n] if i + n < len(haystack_lower) else " "
        if not (before.isalnum() or before == "_") and \
           not (after.isalnum() or after == "_"):
            return True
        i = haystack_lower.find(phrase_lower, i + 1)
    return False


def _names_a_place_by_name(question: str, place_names) -> bool:
    """True when the question prints one of the building's own place names.

    Matched against the RAW question like `_names_a_place`, so nothing depends
    on the tokeniser, and case-insensitively because the register prints both
    'Computer Laboratory' and 'COMPUTER LABORATORY'."""
    if not place_names:
        return False
    q = (question or "").lower()
    return any(_contains_phrase(q, n) for n in place_names)


def _names_a_room_by_code(question: str) -> bool:
    """True when the question names a room by its CODE — '4.04', 'RM-4.04',
    'EX-00-055', 'D01-256' — rather than by a name or by a floor/block.
    Decides whether `_ROOM_GRANULARITY_WEIGHT` applies; see the comment on
    that constant for the measurement that drew the line here."""
    return any(rx.search(question or "") for rx in _ROOM_CODE_RES)


def _asks_about_a_change(question: str) -> bool:
    """True when the question is about CHANGING something in the building —
    replacing, swapping, upgrading, adding, removing, relocating, modifying or
    matching something — or about what a change would AFFECT.

    Two ways in, and they are not the same strength of evidence:
      * an IMPACT word ('what would this affect', 'what depends on it') is on
        its own a question about links, and fires alone;
      * a plain change VERB fires only when the question also names something
        the graph could be walked from — a place, or an entity by its coded tag.
        See `_CHANGE_VERB_RES` for the 8 plain factual questions that measured
        this condition into existence.

    Matched against the RAW question like `_names_a_place`, so nothing depends
    on the tokeniser. Word-bounded on purpose: 'add' must not fire on 'address',
    and the stems are written out (`replac\\w*`) rather than fuzzily matched so
    the list can be read and argued with."""
    q = question or ""
    if any(rx.search(q) for rx in _IMPACT_WORD_RES):
        return True
    if not any(rx.search(q) for rx in _CHANGE_VERB_RES):
        return False
    return _names_a_place(q) or bool(_question_prefixes(q))


def _card_is_graph(card: dict) -> bool:
    """True when this table's rows ARE dependency links — it names both ends of
    a link and the link itself (`subject_id`, `predicate`, `object_id`).

    Read off the card's own columns, never a table name, exactly like
    `_card_is_placeable`. All three columns are required: a table carrying only
    an `object_id` is a foreign key, which names one end and cannot be walked
    backwards, and walking BOTH directions is the whole of what a change-impact
    question needs (doc-prep/out/hwu-spine/hwu_spine_manifest.md)."""
    return _GRAPH_EDGE_COLUMNS <= set(card.get("columns", []))


def _card_is_placeable(card: dict) -> bool:
    """True when this table's rows can be put in a place.

    Two ways a card can say so, both read off the card:
      * it carries a location key of its own (`location_id`, or the camera
        register's `wired_to_location_id`); or
      * one of its DECLARED JOINS resolves a column of this table to another
        table's `location_id`. This second arm is not decoration — it is what
        keeps `hwu_relationships` in play. That table holds the corpus's 253
        evidence-backed links ('feeds', 'protects', 'wiredTo'), 167 of which
        point at a location, but through a column called `object_id`, so the
        column test alone misses it. Replaying all 183 eval questions with
        only the column test, three cross-document questions — among them one
        asking which board feeds a named room's appliance — lost the table
        that held their answer, because a card scoring 3.1 on merit was
        overtaken by cards scoring 0.4 plus the flat place boost.

    `location_id` is the name doc-prep's output contract gives the spine key
    (doc-prep/CLAUDE.md §5), the same convention `_LOCATION_COLUMNS` encodes;
    no table name is matched anywhere in this module."""
    if _LOCATION_COLUMNS & set(card.get("columns", [])):
        return True
    return any(f".{col}" in join
               for join in card.get("declared_joins", [])
               for col in _LOCATION_COLUMNS)


def _card_reaches_room(card: dict) -> bool:
    """True when the card's OWN `location_resolution` vocabulary says its rows
    resolve as far as a single room. Read off the card, never hard-coded: no
    table name appears anywhere in this module."""
    values = card.get("value_vocabulary", {}).get("location_resolution", [])
    return any(str(v).strip().lower() == _ROOM_RESOLUTION_VALUE for v in values)


def _card_subject_words(card: dict) -> set[str]:
    doc_prefix_tokens = {"hwu", "om"}
    name_tokens = [t for t in card["table"].split("_") if t not in doc_prefix_tokens]
    tokens = list(name_tokens)
    for col in card.get("columns", []):
        tokens += col.split("_")
    return _meaningful_words(tokens)


def _card_vocab_words(card: dict) -> set[str]:
    tokens = []
    for values in card.get("value_vocabulary", {}).values():
        for v in values:
            tokens += _tokenize(v)
    return _meaningful_words(tokens)


def _document_frequency(cards: list[dict], card_words: dict) -> dict:
    """How many cards a word appears in (subject words + vocab words
    combined) — the denominator of a simple TF-IDF-style down-weighting.

    Why this exists (found against the REAL cards, not hypothesised): a
    question about the load schedule ('FCUs on the 5th floor of Block B',
    ls-008) was outscored by CCTV's `camera_schedule` table, because that
    table's own `camera_location` enum genuinely contains the values '5TH
    FLOOR' and '4TH FLOOR' — a real, verified enum, not a false-positive
    like the one `11_table_cards.py`'s `_value_vocabulary` guard fixed. The
    words 'floor' and '5th' are simply generic enough to appear in two
    unrelated documents' real data. Down-weighting a word by how many
    tables it shows up in (so 'fcu' — one table only — outweighs 'floor' —
    several) fixes this without hand-listing generic words, the same way
    real search engines discount common terms.

    `card_words` is `select_tables`'s once-per-call {id(card): (subject,
    vocab)} map — every card's word sets are read from it rather than
    recomputed here, since every card in `cards` needs them again in
    `_score` right after this.
    """
    df = {}
    for card in cards:
        subject, vocab = card_words[id(card)]
        for w in subject | vocab:
            df[w] = df.get(w, 0) + 1
    return df


def _score(question_words: set[str], question_prefixes: set[str], card: dict,
           df: dict, card_words: dict,
           names_place: bool = False, names_room: bool = False) -> float:
    score = 0.0
    prefixes = set(card.get("identifier_prefixes", []))
    score += 6 * len(question_prefixes & prefixes)
    subject, vocab = card_words[id(card)]
    for w in question_words & vocab:
        score += 3 / df.get(w, 1)
    for w in question_words & subject:
        score += 1 / df.get(w, 1)
    # Tier 4. Flat, not divided by document frequency: `location_id` is
    # deliberately on 26 of the 67 cards — it is the spine, and the whole point
    # is that a placed row is reachable. Down-weighting it by how common it is
    # would undo the tier it implements.
    #
    # The change guard is deliberately NOT a term here — see the module
    # docstring for the replay that decided that.
    if names_place and _card_is_placeable(card):
        score += _PLACE_WEIGHT
        if names_room and _card_reaches_room(card):
            score += _ROOM_GRANULARITY_WEIGHT
    return score


def select_tables(question: str, cards: list[dict], k: int = 3) -> list[str]:
    """Return up to k table names ranked by relevance to `question`, most
    relevant first, plus every declared join-neighbour of a selected table
    (neighbours are appended after the ranked top-k and are not themselves
    ranked or capped by k — the neighbour rule is a correctness guard, not
    a relevance signal, and must not be squeezed out by it).

    THE CHANGE GUARD, same shape and for the same reason. When the question
    asks about CHANGING something, every dependency-graph card is appended too
    — after the ranked picks, never displacing one. See the module docstring
    for why this is a guard and not a scoring tier, and `_asks_about_a_change`
    for when it fires.

    Deterministic: identical (question, cards, k) always returns the same
    list in the same order. Ties in score are broken by a stated rule first —
    on a question that names a room BY ITS PRINTED NAME, a card whose rows
    reach room granularity wins the tie — and by each card's position in
    `cards` after that (stable
    sort), so the result depends only on the inputs, never on dict/set
    iteration order.
    """
    if not cards:
        return []

    qwords = _question_words(question)
    qprefixes = _question_prefixes(question)
    # Tier 4 fires on a lexical shape OR on a place name the cards carry; the
    # ROOM refinement stays on codes only (`_ROOM_GRANULARITY_WEIGHT`'s comment
    # records the question that measured that line, and it still holds — a
    # printed room name is also printed in ordinary text columns).
    place_names = _place_names(cards)
    names_by_name = _names_a_place_by_name(question, place_names)
    names_place = _names_a_place(question) or names_by_name
    names_room = names_place and _names_a_room_by_code(question)
    # THE TIE-BREAK, and why it is a tie-break rather than a weight. Adding
    # `_ROOM_GRANULARITY_WEIGHT` on a name was measured over all 263 eval
    # questions and COST xd-002 the table its answer is in: "which board powers
    # the ICT rack in the first-floor Block B server room?" scores
    # `hwu_db_circuits` 4.61 against 4.58 for the room-resolving cards, and +1
    # inverts that. A tie-break cannot: it only ever orders cards that already
    # scored the SAME, so it can never demote a card that scored higher. It
    # replaces "whatever position the card happens to hold in the list" — an
    # arbitrary fact about a database read — with a stated rule: when the
    # question names a room BY NAME, a card whose own `location_resolution`
    # says its rows reach a room comes first. Measured: 8 of 263 selections
    # change, 4 evidence tables gained, 0 lost.
    #
    # BY NAME ONLY, and that is measured too: a room named by its CODE is
    # already separated by `_ROOM_GRANULARITY_WEIGHT`, and extending the
    # tie-break to codes changes nothing at all over the 263 (the two variants
    # returned identical selections) while making that weight untestable - the
    # M2 mutation in `test_table_router_place` exists to prove the +1 is
    # load-bearing, and a tie-break that also covers the code case would
    # silently take over its job.
    prefer_room = names_by_name
    asks_change = _asks_about_a_change(question)
    # Each card's subject/vocab word sets are pure functions of the card,
    # but both _document_frequency and _score need them for every card —
    # computed once per card here (keyed by identity, scoped to this call
    # only) rather than twice, which matters at the corpus sizes this router
    # exists for (doc-prep/12_scale_probe.py projects 1,000 tables).
    card_words = {id(card): (_card_subject_words(card), _card_vocab_words(card))
                  for card in cards}
    df = _document_frequency(cards, card_words)

    scored = [
        (_score(qwords, qprefixes, card, df, card_words, names_place, names_room),
         0 if (prefer_room and _card_reaches_room(card)) else 1,
         i, card["table"])
        for i, card in enumerate(cards)
    ]
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))

    by_name = {c["table"]: c for c in cards}
    top = [name for score, _, _, name in scored[:k] if score > 0] or \
          [name for _, _, _, name in scored[:k]]

    # The change guard (see the docstring). Appended in `cards` order, so the
    # result stays deterministic, and before the neighbour pass so that a graph
    # table brings the asset tables its own declared joins name — which is how
    # "what would this affect" reaches the thing affected.
    if asks_change:
        for card in cards:
            if _card_is_graph(card) and card["table"] not in top:
                top.append(card["table"])

    result = list(top)
    for name in top:
        for nb in by_name.get(name, {}).get("joins_to", []):
            if nb not in result and nb in by_name:
                result.append(nb)
    return result
