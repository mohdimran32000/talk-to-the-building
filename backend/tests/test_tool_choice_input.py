"""test_tool_choice_input.py - the temperature-0 tool choice reads a FROZEN input (2026-10-02).

THE DEFECT THIS EXISTS FOR
The chat model picks its first tool in a temperature-0 call. Its input is the system prompt
`_build_system_prompt` writes plus the function declarations, and two things inside it had nothing
to do with choosing a tool, yet moved it:
  * the ANSWER-writing rules. `_build_system_prompt` appended OUTPUT_FORMAT_RULES, so every bullet
    written for reading a tool result (printed total rows, a MATCHED line, a HIERARCHY line, the
    notes block, the contact and warranty sentences: 7,524 -> 10,491 chars) changed the tool choice;
  * the table menu. It printed each table's LOADED columns, so a column the data pipeline appends
    for the SQL writer (a stamped storey key, an ISO date companion) changed the tool choice too -
    in two places, because the menu is printed in the system prompt AND in the
    query_structured_data description.
Measured on the 50-question ruler: 15 of 50 first tool calls changed, each identically in both runs
of its arm - a deterministic shift, not noise - so no later answer-rule change could be credited to
its own fix.

THE FIX (ruling W7: freeze the tool-choice input at the v1.3 text, main = 7743166)
  * TOOL_CHOICE_FORMAT_RULES is 7743166's OUTPUT_FORMAT_RULES, byte for byte (its sha256 is pinned
    below). `_build_system_prompt` appends it; the live OUTPUT_FORMAT_RULES goes only into the four
    prompts that write an answer.
  * The menu lists each table's columns as its ROUTER CARD lists them. Cards are built with the
    pipeline's own columns left off, so those never reach the tool choice; a table with no card
    keeps its loaded columns.

THE GOLDEN TEST (section 4) decides it: over invented tables and cards, the whole tool-choice request
this code sends - system prompt and every function declaration - must equal, byte for byte, what
7743166 sends for the same tables as they were loaded before the pipeline's columns existed.
`fixtures/tool_choice_request_7743166.txt` IS that request: it was written by running 7743166's own
`stream_response` through `capture_tool_choice` and `render_request` below, unchanged. Its hash is
pinned here, so the fixture cannot be regenerated from new code without this file saying so.

Every table, column and card in this file is invented. Run:
    venv/Scripts/python -X utf8 tests/test_tool_choice_input.py
"""
import ast
import difflib
import hashlib
import inspect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import llm_usage as llm_usage_mod  # noqa: E402
from app.services import openai_client as oc  # noqa: E402
from app.services import settings as settings_mod  # noqa: E402
from app.services import sql_tool  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


# ---------------------------------------------------------------------------
# Fixtures - invented tables and router cards
# ---------------------------------------------------------------------------
# The tables as they were loaded BEFORE the pipeline appended its own columns, which is exactly
# what each one's card lists.
BASE_TABLES = [
    {"table_name": "fx_book_purchases",
     "columns": ["entry_ref", "bought_on", "useful_life_years", "remarks_text"]},
    {"table_name": "fx_loose_sheet", "columns": ["sheet_key", "sheet_value"]},
    {"table_name": "fx_pump_list",
     "columns": ["pump_tag", "duty_point", "flow_rate_ls", "pump_page", "spot_key",
                 "spot_resolution"]},
    {"table_name": "fx_wide_grid", "columns": [f"grid_c{i:02d}" for i in range(1, 16)]},
]
# The same tables as loaded today: an ISO companion of a printed date column, and a key stamped
# LAST onto two tables - none of them on any card. `fx_wide_grid` is the quiet case: its 15 columns
# fill the menu's cap exactly, so its stamp showed only as a trailing ", ...".
OFF_CARD = {"fx_book_purchases": ["bought_on_iso"],
            "fx_pump_list": ["deck_key"],
            "fx_wide_grid": ["deck_key"]}
STAMPED_TABLES = [{"table_name": t["table_name"],
                   "columns": t["columns"] + OFF_CARD.get(t["table_name"], [])}
                  for t in BASE_TABLES]
# Every loaded table but one has a card, and one card names a table that is not loaded.
CARDS = ([{"table": t["table_name"], "columns": list(t["columns"])}
          for t in BASE_TABLES if t["table_name"] != "fx_loose_sheet"]
         + [{"table": "fx_ghost_table", "columns": ["ghost_col"]}])

# The four markers of the answer bullets waves 1-2 added, none of which 7743166 carried.
WAVE_MARKERS = ("PRINTED TOTAL ROWS", "MATCHED", "HIERARCHY", "NOTES ON THE ROWS")
# sha256 of 7743166's OUTPUT_FORMAT_RULES (7,524 chars), read with `git show`.
FROZEN_RULES_SHA256 = "5a9dac889675814df1765c3ba08419ac80788e187d6dcac409bd576076d784b0"
GOLDEN_PATH = Path(__file__).resolve().parent / "fixtures" / "tool_choice_request_7743166.txt"
GOLDEN_SHA256 = "c17667f820fff9f8ac66ff622fbbefd5205762ea0f56c646ab5cbcb629293b8c"


def sha256(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The harness. It is written against the module objects it is handed, so the very same code
# rebuilt 7743166's request for the golden fixture.
# ---------------------------------------------------------------------------
def capture_tool_choice(oc, llm_usage, settings_mod, sql_tool, tables, cards, *,
                        has_documents=True, web=False, sql_on=True, cards_raise=None,
                        card_reads=None):
    """Drive `oc.stream_response` with every external edge faked and return the config of its
    temperature-0 tool-choice call (None if it never made one). The model answers in plain text,
    so the stream ends right after the choice.

    The router cards are served the way the app stores them - one `table_cards` row per card - and
    the local cards file is pointed at a name that does not exist, so a run sees exactly `cards`.
    `card_reads` (a list) records each `table_cards` read; `cards_raise` makes that read raise."""
    captured = {}

    class _Res:
        def __init__(self, data):
            self.data = data

    class _Query:
        def __init__(self, name):
            self.name = name

        def select(self, *a, **k):
            return self

        def eq(self, *a, **k):
            return self

        def order(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        def execute(self):
            if self.name != "table_cards":
                return _Res([])
            if card_reads is not None:
                card_reads.append(self.name)
            if cards_raise is not None:
                raise cards_raise
            return _Res([{"table_name": c["table"], "card": c} for c in cards])

    class _Supabase:
        def table(self, name):
            return _Query(name)

    class _Part:
        function_call = None
        text = "a plain answer"

    class _Content:
        parts = [_Part()]

    class _Candidate:
        content = _Content()
        finish_reason = None

    class _Response:
        candidates = [_Candidate()]
        usage_metadata = None

    def fake_generate(client, **kw):
        captured.setdefault("config", kw.get("config"))
        return _Response()

    saved = (oc._get_client, oc.get_llm_model, oc.get_text_to_sql_enabled,
             oc.get_web_search_enabled, oc.get_metadata_schema, llm_usage.generate_with_usage,
             sql_tool._TABLE_CARDS_PATH)
    oc._get_client = lambda: object()
    oc.get_llm_model = lambda: "fixture-model"
    oc.get_text_to_sql_enabled = lambda: sql_on
    oc.get_web_search_enabled = lambda: web
    oc.get_metadata_schema = lambda: settings_mod.DEFAULT_METADATA_SCHEMA
    llm_usage.generate_with_usage = fake_generate
    sql_tool._TABLE_CARDS_PATH = sql_tool._TABLE_CARDS_PATH.with_name("no-such-cards-file.json")
    sql_tool._reset_table_cards_cache()
    try:
        list(oc.stream_response(
            messages=[{"role": "user", "content": "a neutral question"}],
            user_id="u-fixture", supabase_client=_Supabase(),
            has_documents=has_documents, has_structured_data=True,
            structured_tables=[dict(t) for t in tables]))
    finally:
        (oc._get_client, oc.get_llm_model, oc.get_text_to_sql_enabled,
         oc.get_web_search_enabled, oc.get_metadata_schema, llm_usage.generate_with_usage,
         sql_tool._TABLE_CARDS_PATH) = saved
        sql_tool._reset_table_cards_cache()
    return captured.get("config")


def render_request(cfg):
    """The tool-choice request as text: the system instruction, every function declaration as
    sorted JSON (None fields left out), then the call's own settings."""
    if cfg is None:
        return ""
    parts = ["=== system_instruction ===", cfg.system_instruction or ""]
    for d in (cfg.tools[0].function_declarations if cfg.tools else []):
        parts += [f"=== declaration: {d.name} ===",
                  json.dumps(d.model_dump(mode="json", exclude_none=True),
                             ensure_ascii=False, indent=1, sort_keys=True)]
    call = {
        "temperature": cfg.temperature,
        "tool_config": (cfg.tool_config.model_dump(mode="json", exclude_none=True)
                        if cfg.tool_config else None),
        "automatic_function_calling": (
            cfg.automatic_function_calling.model_dump(mode="json", exclude_none=True)
            if cfg.automatic_function_calling else None),
    }
    parts += ["=== call ===", json.dumps(call, ensure_ascii=False, sort_keys=True)]
    return "\n".join(parts) + "\n"


def readable_diff(expected, actual, limit=40):
    """A unified diff a person can read: the menu is one long line, so it is split at its
    '; ' table boundaries first, and every line is clipped."""
    lines = list(difflib.unified_diff(
        expected.replace("; ", ";\n").splitlines(), actual.replace("; ", ";\n").splitlines(),
        "7743166 (golden)", "this code", lineterm="", n=0))
    shown = [ln if len(ln) <= 220 else ln[:220] + " ..." for ln in lines[:limit]]
    more = [f"... and {len(lines) - limit} more diff lines"] if len(lines) > limit else []
    return "\n      " + "\n      ".join(shown + more)


def menus(cfg):
    """The table menu as the model reads it, twice: from the system prompt's
    query_structured_data line, and from that tool's own description."""
    if cfg is None:
        return "", ""
    system = cfg.system_instruction or ""
    line = next((ln for ln in system.splitlines() if ln.startswith("- query_structured_data:")),
                "")
    decl = next((d for d in (cfg.tools[0].function_declarations if cfg.tools else [])
                 if d.name == "query_structured_data"), None)
    description = decl.description if decl is not None else ""
    pick = lambda s: s.split(" Available tables: ", 1)[1] if " Available tables: " in s else ""
    return pick(line), pick(description)


def entries(menu):
    """{table name: its column text} for one printed menu."""
    out = {}
    for item in menu.split("; ") if menu else []:
        name, _, rest = item.partition("(")
        out[name] = rest[:-1] if rest.endswith(")") else rest
    return out


def capture_answer_prompts(oc, llm_usage, settings_mod):
    """{path: the system instruction of the call that WRITES the answer}, for each of
    stream_response's four answer paths, every external edge faked."""
    prompts = {}

    class _Chunk:
        def __init__(self, text):
            self.text = text

    class _Models:
        def __init__(self, key):
            self.key = key

        def generate_content_stream(self, **kw):
            prompts.setdefault(self.key, kw["config"].system_instruction)
            return [_Chunk("an answer")]

        def generate_content(self, **kw):
            prompts.setdefault(self.key, kw["config"].system_instruction)
            return None

    class _Client:
        def __init__(self, key):
            self.models = _Models(key)

    class _Call:
        def __init__(self, name, args):
            self.name, self.args = name, args

    class _Finish:
        name = "MALFORMED_FUNCTION_CALL"

    def response(function_call=None, text=None, finish=None):
        part = type("_Part", (), {"function_call": function_call, "text": text})()
        content = type("_Content", (), {"parts": [part]})()
        candidate = type("_Candidate", (), {"content": content, "finish_reason": finish})()
        return type("_Response", (), {"candidates": [candidate], "usage_metadata": None})()

    class _NoDocument:
        data = []

    chunk = {"file_name": "fixture.md", "content": "a fixture excerpt", "document_id": "d-fx"}
    runs = {
        "manual filter": ({"manual_metadata_filter": {"doc_kind": "fixture"}}, None,
                          "a neutral question"),
        "malformed call": ({}, response(finish=_Finish()), "a neutral question"),
        "tool result": ({}, response(function_call=_Call("search_documents",
                                                         {"query": "fixture excerpt"})),
                        "a neutral question"),
        "forced analysis": ({}, response(text="no tool"), "summarize the fixture handbook"),
    }
    saved = (oc._get_client, oc.get_llm_model, oc.get_text_to_sql_enabled,
             oc.get_web_search_enabled, oc.get_metadata_schema, llm_usage.generate_with_usage,
             oc.retrieve_chunks, oc._execute_search_documents, oc._resolve_document)
    oc.get_llm_model = lambda: "fixture-model"
    oc.get_text_to_sql_enabled = lambda: False
    oc.get_web_search_enabled = lambda: False
    oc.get_metadata_schema = lambda: settings_mod.DEFAULT_METADATA_SCHEMA
    oc.retrieve_chunks = lambda *a, **k: [dict(chunk)]
    oc._execute_search_documents = lambda *a, **k: [dict(chunk)]
    oc._resolve_document = lambda *a, **k: _NoDocument()
    try:
        for key, (extra, first, question) in runs.items():
            oc._get_client = lambda key=key: _Client(key)
            llm_usage.generate_with_usage = lambda client, first=first, **kw: first
            list(oc.stream_response(
                messages=[{"role": "user", "content": question}],
                user_id="u-fixture", supabase_client=object(),
                has_documents=True, has_structured_data=False, **extra))
    finally:
        (oc._get_client, oc.get_llm_model, oc.get_text_to_sql_enabled,
         oc.get_web_search_enabled, oc.get_metadata_schema, llm_usage.generate_with_usage,
         oc.retrieve_chunks, oc._execute_search_documents, oc._resolve_document) = saved
    return prompts


FROZEN = getattr(oc, "TOOL_CHOICE_FORMAT_RULES", None)


# ===========================================================================
print("1. The tool choice carries the FROZEN rules, never the answer bullets")
# ===========================================================================
check("RED: TOOL_CHOICE_FORMAT_RULES exists", isinstance(FROZEN, str))
check("RED: it is 7743166's OUTPUT_FORMAT_RULES, byte for byte (7,524 chars, sha256 pinned)",
      sha256(FROZEN) == FROZEN_RULES_SHA256, f"{len(FROZEN or '')} chars, {sha256(FROZEN)}")
check("the live OUTPUT_FORMAT_RULES still carries every wave-1/2 marker (so the absence checks "
      "below cannot pass by the markers simply being gone)",
      all(m in oc.OUTPUT_FORMAT_RULES for m in WAVE_MARKERS),
      [m for m in WAVE_MARKERS if m not in oc.OUTPUT_FORMAT_RULES])
check("RED: ...and the frozen copy carries none of them",
      FROZEN is not None and not any(m in FROZEN for m in WAVE_MARKERS),
      [m for m in WAVE_MARKERS if FROZEN and m in FROZEN])

cfg_main = capture_tool_choice(oc, llm_usage_mod, settings_mod, sql_tool, STAMPED_TABLES, CARDS)
system_main = cfg_main.system_instruction if cfg_main is not None else ""
check("the harness captured the tool-choice call, at temperature 0",
      cfg_main is not None and cfg_main.temperature == 0, cfg_main)
for marker in WAVE_MARKERS:
    check(f"RED: the tool-choice system prompt carries no {marker!r}", marker not in system_main)
check("RED: the tool-choice system prompt carries the frozen rules verbatim",
      FROZEN is not None and FROZEN in system_main)
check("RED: the tool-choice system prompt does not carry the live rules",
      oc.OUTPUT_FORMAT_RULES not in system_main)
# Every shape of the prompt, not only the one the app sends today.
variants = {}
for hd in (True, False):
    for web in (True, False):
        variants[(hd, web)] = oc._build_system_prompt(hd, True, web, STAMPED_TABLES)
variants["tools off"] = oc._build_system_prompt(True, False, False)
check("RED: no shape of the tool-choice prompt carries a wave-1/2 marker (documents on/off, "
      "web on/off, tables off)",
      not any(m in p for p in variants.values() for m in WAVE_MARKERS),
      [k for k, p in variants.items() if any(m in p for m in WAVE_MARKERS)])
check("a session with no tools at all is untouched",
      oc._build_system_prompt(False, False, False) == oc.SYSTEM_PROMPT_NO_DOCS)


def names_read(func):
    """Every name the function's CODE reads (comments and prose cannot satisfy this)."""
    return [n.id for n in ast.walk(ast.parse(inspect.getsource(func)))
            if isinstance(n, ast.Name)]


build_names = names_read(oc._build_system_prompt)
check("RED: _build_system_prompt reads the frozen copy and never the live rules",
      "TOOL_CHOICE_FORMAT_RULES" in build_names and "OUTPUT_FORMAT_RULES" not in build_names,
      [n for n in build_names if n.endswith("FORMAT_RULES")])


# ===========================================================================
print("\n2. The answer prompts carry the LIVE rules - every one of the four")
# ===========================================================================
# Green before this change by construction (the four answer prompts always carried the live
# rules); this section is the guard that the freeze never leaks into a prompt that WRITES an
# answer. Mutation M4 below proves it turns red when one does.
prompts = capture_answer_prompts(oc, llm_usage_mod, settings_mod)
PATHS = ("manual filter", "malformed call", "tool result", "forced analysis")
check("all four answer paths were driven", sorted(prompts) == sorted(PATHS), sorted(prompts))
# One wave-1/2 bullet pinned per path, plus the wave-2 HIERARCHY bullet in every path.
PINNED = {"manual filter": "A result may carry a PRINTED TOTAL ROWS line",
          "malformed call": "A result may carry a MATCHED line",
          "tool result": '"NOTES ON THE ROWS BEHIND THIS RESULT" block',
          "forced analysis": "For a who/contact question"}
for path in PATHS:
    p = prompts.get(path, "")
    check(f"{path}: the answer prompt carries the live OUTPUT_FORMAT_RULES whole",
          oc.OUTPUT_FORMAT_RULES in p)
    check(f"{path}: ...including its pinned bullet and the HIERARCHY bullet",
          PINNED[path] in p and "A result may carry a HIERARCHY line" in p)
    check(f"{path}: ...and never the frozen tool-choice copy",
          FROZEN is None or FROZEN not in p)
stream_names = names_read(oc.stream_response)
check("stream_response reads the live rules at exactly its four answer sites",
      stream_names.count("OUTPUT_FORMAT_RULES") == 4, stream_names.count("OUTPUT_FORMAT_RULES"))
check("stream_response never reads the frozen copy (it belongs to the tool choice only)",
      "TOOL_CHOICE_FORMAT_RULES" not in stream_names)


# ===========================================================================
print("\n3. The table menu lists each table's columns as its router card lists them")
# ===========================================================================
reads = []
cfg_cards = capture_tool_choice(oc, llm_usage_mod, settings_mod, sql_tool, STAMPED_TABLES, CARDS,
                                card_reads=reads)
in_system, in_tool = menus(cfg_cards)
check("fixture sanity: both menus were found", bool(in_system) and bool(in_tool),
      (in_system[:80], in_tool[:80]))
for table, cols in OFF_CARD.items():
    for col in cols:
        check(f"RED: {table}.{col} (on no card) is in neither menu - system prompt and "
              f"query_structured_data description",
              col not in in_system and col not in in_tool)
for card in CARDS[:-1]:
    want = ", ".join(card["columns"][:15]) + (", ..." if len(card["columns"]) > 15 else "")
    check(f"RED: {card['table']} is listed with its card's columns, in the card's order",
          entries(in_system).get(card["table"]) == want
          and entries(in_tool).get(card["table"]) == want,
          (entries(in_system).get(card["table"]), want))
check("RED: the quiet case - 15 card columns fill the cap exactly, so no trailing ', ...'",
      entries(in_system).get("fx_wide_grid", "").count(", ") == 14
      and not entries(in_system).get("fx_wide_grid", "").endswith("..."),
      entries(in_system).get("fx_wide_grid"))
check("a loaded table with no card keeps its loaded columns",
      entries(in_system).get("fx_loose_sheet") == "sheet_key, sheet_value"
      and entries(in_tool).get("fx_loose_sheet") == "sheet_key, sheet_value",
      entries(in_system).get("fx_loose_sheet"))
check("a card for a table that is not loaded lists nothing - the menu is the loaded tables",
      "fx_ghost_table" not in in_system and "ghost_col" not in in_tool)
check("the menu keeps the loaded tables' own order",
      list(entries(in_system)) == [t["table_name"] for t in STAMPED_TABLES],
      list(entries(in_system)))
check("both menus are the same text", in_system == in_tool)
check("RED: the cards are read once per question, through the router's own loader",
      reads == ["table_cards"], reads)

# The two builders themselves, given the cards directly.
try:
    built_system = oc._build_system_prompt(True, True, False, STAMPED_TABLES, table_cards=CARDS)
    built_tool = oc._build_sql_tool(STAMPED_TABLES, table_cards=CARDS).description
    build_error = None
except TypeError as e:
    built_system, built_tool, build_error = "", "", e
check("RED: _build_system_prompt and _build_sql_tool take the router cards",
      build_error is None, repr(build_error))
check("RED: ...and leave every off-card column out of their menus",
      build_error is None and not any(c in built_system or c in built_tool
                                      for cols in OFF_CARD.values() for c in cols))
check("without cards, the builders list the loaded columns exactly as before",
      "deck_key" in oc._build_system_prompt(True, True, False, STAMPED_TABLES)
      and "deck_key" in oc._build_sql_tool(STAMPED_TABLES).description)

# Degrades, never raises - the router's own chain: database -> file -> none.
down_reads = []
cfg_down = capture_tool_choice(oc, llm_usage_mod, settings_mod, sql_tool, STAMPED_TABLES, CARDS,
                               cards_raise=RuntimeError("database unreachable"),
                               card_reads=down_reads)
down_system, down_tool = menus(cfg_down)
check("cards unreadable: the tool choice still happens, its menu listing the loaded columns",
      cfg_down is not None and "deck_key" in down_system and "deck_key" in down_tool)
no_sql_reads = []
cfg_no_sql = capture_tool_choice(oc, llm_usage_mod, settings_mod, sql_tool, STAMPED_TABLES, CARDS,
                                 sql_on=False, card_reads=no_sql_reads)
check("SQL tool off: no menu is printed and no card is read",
      cfg_no_sql is not None and no_sql_reads == []
      and "query_structured_data" not in cfg_no_sql.system_instruction, no_sql_reads)
no_table_reads = []
capture_tool_choice(oc, llm_usage_mod, settings_mod, sql_tool, [], CARDS,
                    card_reads=no_table_reads)
check("no tables loaded: no card is read", no_table_reads == [], no_table_reads)


# ===========================================================================
print("\n4. GOLDEN - the whole tool-choice request equals 7743166's, byte for byte")
# ===========================================================================
golden = GOLDEN_PATH.read_text(encoding="utf-8") if GOLDEN_PATH.exists() else ""
check("the golden fixture is 7743166's request, as pinned", sha256(golden) == GOLDEN_SHA256,
      f"{GOLDEN_PATH.name}: {sha256(golden)}")
actual = render_request(cfg_cards)
check("RED: GOLDEN - system prompt + all nine function declarations + call settings, over "
      "today's loaded tables and their cards, equal 7743166's over the tables it loaded",
      sha256(actual) == GOLDEN_SHA256, readable_diff(golden, actual))
actual_base = render_request(capture_tool_choice(oc, llm_usage_mod, settings_mod, sql_tool,
                                                 BASE_TABLES, CARDS))
check("RED: GOLDEN - ...and the same bytes when the loaded tables carry nothing off their cards",
      sha256(actual_base) == GOLDEN_SHA256, readable_diff(golden, actual_base))
check("the golden holds nine declarations, query_structured_data among them",
      golden.count("=== declaration: ") == 9
      and "=== declaration: query_structured_data ===" in golden)


# ===========================================================================
print("\n5. Named mutations - each must turn its own check red")
# ===========================================================================
def _main_request():
    return capture_tool_choice(oc, llm_usage_mod, settings_mod, sql_tool, STAMPED_TABLES, CARDS)


# M1 - the live rules back in the tool choice (the defect itself).
if FROZEN is not None:
    oc.TOOL_CHOICE_FORMAT_RULES = oc.OUTPUT_FORMAT_RULES
    try:
        m1 = _main_request()
        check("M1 live rules in the tool choice: a marker is back AND the golden breaks",
              any(m in m1.system_instruction for m in WAVE_MARKERS)
              and sha256(render_request(m1)) != GOLDEN_SHA256)
    finally:
        oc.TOOL_CHOICE_FORMAT_RULES = FROZEN
else:
    check("M1 needs TOOL_CHOICE_FORMAT_RULES", False)

# M2 - stream_response stops handing the cards to the menu.
if hasattr(oc, "_tool_choice_cards"):
    saved_m2 = oc._tool_choice_cards
    oc._tool_choice_cards = lambda *a, **k: []
    try:
        m2_system, m2_tool = menus(_main_request())
        check("M2 no cards reach the menu: the off-card column is back in BOTH menus",
              "deck_key" in m2_system and "deck_key" in m2_tool)
    finally:
        oc._tool_choice_cards = saved_m2
else:
    check("M2 needs _tool_choice_cards", False)

# M3 - the menu formatter ignores the cards it is given.
if hasattr(oc, "_card_columns"):
    saved_m3 = oc._card_columns
    oc._card_columns = lambda *a, **k: {}
    try:
        m3_system, m3_tool = menus(_main_request())
        check("M3 the formatter ignores the cards: the off-card columns are back",
              "bought_on_iso" in m3_system and "bought_on_iso" in m3_tool)
    finally:
        oc._card_columns = saved_m3
else:
    check("M3 needs _card_columns", False)

# M4 - the freeze leaks into the prompts that write answers.
if FROZEN is not None:
    live = oc.OUTPUT_FORMAT_RULES
    oc.OUTPUT_FORMAT_RULES = FROZEN
    try:
        m4 = capture_answer_prompts(oc, llm_usage_mod, settings_mod)
        check("M4 the frozen copy in the answer prompts: every path loses its HIERARCHY bullet",
              all("A result may carry a HIERARCHY line" not in m4.get(p, "") for p in PATHS))
    finally:
        oc.OUTPUT_FORMAT_RULES = live
else:
    check("M4 needs TOOL_CHOICE_FORMAT_RULES", False)

# M5 - one character of the frozen copy edited.
if FROZEN is not None:
    oc.TOOL_CHOICE_FORMAT_RULES = FROZEN.replace("(strict)", "(strict!)", 1)
    try:
        check("M5 one character edited: the pinned hash and the golden both break",
              sha256(oc.TOOL_CHOICE_FORMAT_RULES) != FROZEN_RULES_SHA256
              and sha256(render_request(_main_request())) != GOLDEN_SHA256)
    finally:
        oc.TOOL_CHOICE_FORMAT_RULES = FROZEN
else:
    check("M5 needs TOOL_CHOICE_FORMAT_RULES", False)

check("all mutations restored: the golden holds again",
      sha256(render_request(_main_request())) == GOLDEN_SHA256)

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
