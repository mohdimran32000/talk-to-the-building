"""test_sql_graph_links.py - a result that prints ids of the dependency graph carries every link the
graph records for them, grouped by link and target (wave 3, G7, 2026-10-03).

Measured on the goal-function run of 2026-10-01: a question asked how many units depend on one
room, and what each group of them relies on it for. The writer joined the unit register to the
dependency graph and filtered ONE link type - the complete value list now printed for the link
column put that type in front of it - so the units' other link to the same room, and the second
room half of them rely on for the rest, never reached the result. The answer said nothing else was
recorded. Every link was on record.

So `execute_sql_query` now reads the graph itself. When the SQL reads a table shaped as a graph -
columns subject_id, predicate and object_id, the router's own test for a graph card - and the result
prints ids the graph holds as the SUBJECT of a link, every link FROM those ids is read and grouped by
(link, target), with how many links each group holds and - when every link of the group carries the
same one - the links' note, the largest groups first: one line, at most GRAPH_LINKS_MAX_GROUPS
groups. A link type with more than GRAPH_TARGETS_PER_LINK targets is told as one group with its
counts. Links INTO a printed id are never listed (review of the replay, below). The line names no
table and no column; an error drops it and nothing else; it never changes the rows.

Every fixture is invented (bld_* tables). Every check marked RED fails against sql_tool.py as it
stood before this change.

Run:
    venv/Scripts/python -X utf8 tests/test_sql_graph_links.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import openai_client, sql_loop, sql_tool  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


HEAD = "GRAPH LINKS - "
NOTE_SWITCH = "the room holding the switch this unit plugs into"
NOTE_BATTERY = "the room holding the battery behind this unit's switch"
NOTE_RECORDER = "the room the recorder is in"


def _link(ref, s_kind, s, pred, o_kind, o, name, note):
    return {"link_ref": ref, "subject_type": s_kind, "subject_id": s, "predicate": pred,
            "object_type": o_kind, "object_id": o, "object_display_name": name,
            "evidence_file": "bld_register.csv", "notes": note}


LINK_ROWS = (
    [_link(f"LK-{i:02d}", "unit", f"CM-{i}", "plugs_into", "place", "ZR-1", "Switch Room A",
           NOTE_SWITCH) for i in range(1, 6)]
    + [_link(f"LK-1{i}", "unit", f"CM-{i}", "powered_by", "place", "ZR-1", "Switch Room A",
             NOTE_BATTERY) for i in (1, 2)]
    + [_link(f"LK-2{i}", "unit", f"CM-{i}", "powered_by", "place", "ZR-2", "Switch Room B",
             NOTE_BATTERY) for i in (3, 4, 5)]
    + [_link(f"LK-3{i}", "unit", f"CM-{i}", "logged_by", "place", "ZR-9", "Recorder Room",
             NOTE_RECORDER) for i in range(1, 6)]
    + [_link(f"LK-4{i}", "place", "ZL-1", "contains", "place", f"ZR-{i}", f"Room {i}", "")
       for i in range(1, 7)]
    + [_link("LK-50", "unit", "CM-6", "plugs_into", "place", "ZR-3", "Room 3", NOTE_SWITCH)]
)
LINKS = {
    "table_name": "bld_links",
    "columns": ["link_ref", "subject_type", "subject_id", "predicate", "object_type", "object_id",
                "object_display_name", "evidence_file", "notes"],
    "rows": LINK_ROWS,
    "row_count": len(LINK_ROWS),
}
CAMS = {
    "table_name": "bld_cams",
    "columns": ["cam_tag", "cam_class"],
    "rows": [{"cam_tag": f"CM-{i}", "cam_class": "DETECT" if i % 2 else "IDENT"}
             for i in range(1, 7)],
    "row_count": 6,
}
CARDS = [
    {"table": "bld_links", "identifier_column": "link_ref",
     "holds": "one row per evidence-backed link between two things"},
    {"table": "bld_cams", "identifier_column": "cam_tag", "holds": "one row per unit"},
]


class _ExecResult:
    def __init__(self, data):
        self.data = data


class _Supabase:
    """Serves every table for `structured_data`; `.eq()` and `.order()` are accepted."""

    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        tables = self.tables if name == "structured_data" else []

        class _Q:
            def select(self, *a, **kw):
                return self

            def eq(self, *a, **kw):
                return self

            def order(self, *a, **kw):
                return self

            def execute(self):
                return _ExecResult(list(tables))

        return _Q()


class _Models:
    sql = ""

    def generate_content(self, *, model, contents, config=None):
        return type("_R", (), {"text": _Models.sql, "usage_metadata": None})()


class _Client:
    def __init__(self, *a, **kw):
        self.models = _Models()


def run(sql, question="q", tables=(LINKS, CAMS), cards=CARDS):
    """`execute_sql_query` end to end - the real loader, DuckDB and result assembly - with a fake
    SQL writer that returns `sql`. No model, no network."""
    saved = (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
             sql_tool._load_table_cards)
    sql_tool.genai.Client = _Client
    sql_tool.get_llm_api_key = lambda: "fake-key"
    sql_tool.get_llm_model = lambda: "fake-model"
    sql_tool._load_table_cards = lambda *a, **k: list(cards)
    _Models.sql = sql
    try:
        return sql_tool.execute_sql_query(question, "u-1", _Supabase(list(tables)))
    finally:
        (sql_tool.genai.Client, sql_tool.get_llm_api_key, sql_tool.get_llm_model,
         sql_tool._load_table_cards) = saved


def links_lines(text):
    return [ln for ln in text.splitlines() if ln.startswith(HEAD)]


# ---------------------------------------------------------------------------
print("1. Units joined to the graph on ONE link type: every link of theirs, by link and target (RED)")
# ---------------------------------------------------------------------------
ONE_TYPE = ("SELECT c.cam_tag, c.cam_class FROM \"bld_cams\" c JOIN \"bld_links\" l "
            "ON c.cam_tag = l.subject_id WHERE l.object_id = 'ZR-1' AND l.predicate = 'plugs_into'")
Q1 = "How many units depend on room ZR-1, and what does each group rely on it for?"
out1 = run(ONE_TYPE, Q1)
line1 = (links_lines(out1) or [""])[0]
check("the writer's own five rows are still there",
      all(f"| CM-{i} |" in out1 for i in range(1, 6)) and "| CM-6 |" not in out1, out1[:400])
check("RED: the result carries exactly one GRAPH LINKS line", len(links_lines(out1)) == 1,
      links_lines(out1) or out1[-500:])
check("RED: it counts the ids the rows above print", "from the 5 ids the rows above print" in line1,
      line1)
check("RED: two link types to the same target are two groups",
      "plugs_into ZR-1 (Switch Room A) - 5 links" in line1
      and "powered_by ZR-1 (Switch Room A) - 2 links" in line1, line1)
check("RED: the group the query's filter left out is there - the second room, with its count",
      "powered_by ZR-2 (Switch Room B) - 3 links" in line1, line1)
check("RED: every link of the ids, the third type included",
      "logged_by ZR-9 (Recorder Room) - 5 links" in line1, line1)
check("RED: each group carries its link's note", f"5 links: {NOTE_SWITCH}" in line1
      and f"5 links: {NOTE_RECORDER}" in line1 and NOTE_BATTERY in line1, line1)
check("RED: a note two groups share is given once", line1.count(NOTE_BATTERY) == 1, line1)
order1 = [line1.find(s) for s in ("logged_by ZR-9", "plugs_into ZR-1", "powered_by ZR-2",
                                  "powered_by ZR-1")]
check("RED: largest groups first; ties by link, then target",
      all(p >= 0 for p in order1) and order1 == sorted(order1), (order1, line1))
# Fix round 1 (the review's minor 3): the line carries no instruction - the answer rule decides
# when its groups are the answer - only the fact that the rows above may show part of the links.
check("RED (R1): the line gives no instruction - no 'answer per group, naming each target'",
      bool(line1) and "answer per group" not in line1.lower()
      and "naming each target" not in line1.lower(), line1)
check("it still says the rows above may show only some of these links",
      line1.endswith("The rows above may show only some of these links."), line1[-80:])
check("RED: it comes after the SQL line and before the SOURCE lines, never inside the table",
      bool(line1) and out1.index("SQL: `") < out1.index(HEAD) < out1.index("SOURCE - ")
      and not any(ln.startswith("|") and HEAD in ln for ln in out1.splitlines()), out1[-900:])
for word in ("bld_links", "bld_cams", "subject_id", "object_id", "predicate",
             "object_display_name", "link_ref", "evidence_file"):
    check(f"the line names no table or column of the project ({word})", word not in line1, line1)

# ---------------------------------------------------------------------------
print("\n2. Silent when nothing printed is a graph id, or the graph is not read")
# ---------------------------------------------------------------------------
out2a = run("SELECT c.cam_class, COUNT(*) AS n FROM \"bld_cams\" c JOIN \"bld_links\" l "
            "ON c.cam_tag = l.subject_id WHERE l.object_id = 'ZR-1' GROUP BY c.cam_class "
            "ORDER BY c.cam_class", Q1)
check("a result printing no graph id (classes and counts): no line", links_lines(out2a) == [],
      links_lines(out2a))
out2b = run("SELECT cam_tag FROM \"bld_cams\" WHERE cam_class = 'DETECT'", Q1)
check("graph ids printed by a query that never reads the graph: no line",
      links_lines(out2b) == [] and "| CM-1 |" in out2b, links_lines(out2b) or out2b[:300])
NOT_A_GRAPH = dict(LINKS, table_name="bld_pairs",
                   columns=[c for c in LINKS["columns"] if c != "predicate"],
                   rows=[{k: v for k, v in r.items() if k != "predicate"} for r in LINK_ROWS])
out2c = run("SELECT c.cam_tag FROM \"bld_cams\" c JOIN \"bld_pairs\" p ON c.cam_tag = p.subject_id "
            "WHERE p.object_id = 'ZR-1'", Q1, tables=(NOT_A_GRAPH, CAMS), cards=())
check("a table with subject and object ids but no link column is no graph: no line",
      links_lines(out2c) == [] and "| CM-1 |" in out2c, links_lines(out2c) or out2c[:300])

# ---------------------------------------------------------------------------
print("\n3. Bounded: a link type with many targets is one group; at most 8 groups (RED)")
# ---------------------------------------------------------------------------
out3 = run("SELECT subject_id, object_id, object_display_name FROM \"bld_links\" "
           "WHERE subject_id = 'ZL-1'", "What does ZL-1 contain?")
line3 = (links_lines(out3) or [""])[0]
check("RED: a link type with more targets than the cap is told as one group, with its counts",
      "from the 1 id the rows above print" in line3
      and "contains - 6 links to 6 different targets" in line3, line3)
check("no single room of the many is listed as a group of its own",
      "contains ZR-" not in line3, line3)
# Review of the replay (2026-10-03): on the query shape the graph rule itself prescribes - every
# link of one thing, both ends printed - listing the links INTO each printed place listed every
# other thing that links to that place, with one kind's note shown for another's group. Only the
# printed things' OWN links are listed.
check("RED: links INTO a printed id are not listed - only the printed things' own links",
      "plugs_into" not in line3 and "powered_by" not in line3, line3)
out3c = run("SELECT object_id, object_display_name FROM \"bld_links\" WHERE subject_id = 'CM-1'",
            "What does CM-1 rely on?")
check("RED: a result printing only targets - ids no link starts from - gets no line",
      links_lines(out3c) == [] and "| ZR-1 |" in out3c, links_lines(out3c) or out3c[:300])
MANY = dict(LINKS, rows=LINK_ROWS + [
    _link(f"LK-6{i}", "unit", "CM-1", f"kind_{i}", "place", f"ZQ-{i}", f"Spot {i}", "")
    for i in range(1, 9)], row_count=len(LINK_ROWS) + 8)
out3b = run(ONE_TYPE, Q1, tables=(MANY, CAMS))
line3b = (links_lines(out3b) or [""])[0]
check("RED: more than 8 groups: the 8 largest are listed, and the line says how many more",
      len(re.findall(r" - \d+ links?\b", line3b)) == 8 and "… and 4 more groups" in line3b
      and "logged_by ZR-9" in line3b and "powered_by ZR-1" in line3b, line3b)

out3d = run("SELECT subject_id, object_id FROM \"bld_links\" WHERE subject_id IN ('ZL-1', 'CM-6')",
            "What do ZL-1 and CM-6 link to?")
line3d = (links_lines(out3d) or [""])[0]
check("RED: on a line over several ids, a group whose links all start from one id names it",
      "contains - 6 links to 6 different targets, from ZL-1" in line3d
      and "plugs_into ZR-3 (Room 3) - 1 link: " + NOTE_SWITCH + ", from CM-6" not in line3d
      and "plugs_into ZR-3 (Room 3) - 1 link, from CM-6" in line3d, line3d)
check("a group drawn from several ids names none of them",
      ", from CM-" not in line1 and ", from " not in line1, line1)
check("and a line over one id names no source - every group is that id's",
      ", from " not in line3, line3)

# Ruling W3A3-R1 applies to the line's notes too: a long link note is shortened at white space,
# never inside a token.
NOTE_LONG = " ".join(f"word{i:02d}xy" for i in range(40))  # a cut at 159 lands inside a word
LONGNOTE = dict(LINKS, rows=[dict(r, notes=NOTE_LONG) if r["predicate"] == "logged_by" else r
                             for r in LINK_ROWS])
line1n = (links_lines(run(ONE_TYPE, Q1, tables=(LONGNOTE, CAMS))) or [""])[0]
shown1n = line1n.split("logged_by ZR-9 (Recorder Room) - 5 links: ")[-1].split(";")[0]
check("RED (W3A3-R1): a long link note is shortened at white space - every word whole, ending '…'",
      shown1n.endswith("…") and len(shown1n) <= sql_tool.GRAPH_NOTE_MAX_CHARS
      and all(tok in set(NOTE_LONG.split(" ")) for tok in shown1n[:-1].split(" ")), shown1n)

MIXED = dict(LINKS, rows=[dict(r, notes="a note for this one link only")
                          if r["link_ref"] == "LK-32" else r for r in LINK_ROWS])
line1m = (links_lines(run(ONE_TYPE, Q1, tables=(MIXED, CAMS))) or [""])[0]
check("RED: a group whose links carry different notes shows none of them",
      "logged_by ZR-9 (Recorder Room) - 5 links;" in line1m
      and "this one link only" not in line1m and NOTE_RECORDER not in line1m, line1m)
check("and a group whose links all carry one note still shows it", NOTE_SWITCH in line1m, line1m)

# ---------------------------------------------------------------------------
print("\n4. It never touches the rows, the loop reads nothing new off it, an error drops only it")
# ---------------------------------------------------------------------------
base_rows = [ln for ln in out1.splitlines() if ln.startswith("|")]
check("the rendered rows are exactly the writer's", len(base_rows) == 2 + 5, base_rows)
issues_with = [i.kind for i in sql_loop.inspect_result(out1, Q1, CARDS)]
stripped = "\n".join(ln for ln in out1.splitlines() if not ln.startswith(HEAD))
issues_without = [i.kind for i in sql_loop.inspect_result(stripped, Q1, CARDS)]
check("the loop raises the same issues with or without the line",
      issues_with == issues_without, (issues_with, issues_without))

real_exec = sql_tool._execute_with_timeout
calls = []


def _fail_graph(con, sql, timeout=sql_tool.SQL_QUERY_TIMEOUT):
    calls.append(sql)
    if len(calls) > 1 and "plugs_into" not in sql:
        raise RuntimeError("graph read failed")
    return real_exec(con, sql, timeout)


sql_tool._execute_with_timeout = _fail_graph
try:
    out4 = run(ONE_TYPE, Q1)
finally:
    sql_tool._execute_with_timeout = real_exec
check("RED: the graph read was really attempted",
      any('"subject_id"' in c and "IN (" in c for c in calls[1:]), calls)
check("an error drops the line and nothing else",
      links_lines(out4) == [] and "| CM-1 |" in out4 and "SQL query failed" not in out4,
      out4[-300:])

# ---------------------------------------------------------------------------
print("\n5. One answer rule reads the line (answer prompts only)")
# ---------------------------------------------------------------------------
rule = next((ln for ln in openai_client.OUTPUT_FORMAT_RULES.splitlines() if "GRAPH LINKS" in ln), "")
check("RED: OUTPUT_FORMAT_RULES has a GRAPH LINKS bullet", bool(rule),
      openai_client.OUTPUT_FORMAT_RULES[-300:])
check("RED: it says the line is computed in code and lists every recorded link of the ids shown",
      "computed in code" in rule and "every link" in rule, rule)
check("RED: an answer about what something relies on goes per group, naming each target",
      "per group" in rule and "naming each target" in rule, rule)
check("RED: a group the rows leave out still belongs in the answer, and nothing is called "
      "unrecorded that the line lists", "leave out" in rule and "not recorded" in rule, rule)
check("RED: the line, its heading and table or column names are never printed",
      "never print" in rule.lower(), rule)
check("the frozen tool-choice rules carry none of it (ruling W7)",
      "GRAPH LINKS" not in openai_client.TOOL_CHOICE_FORMAT_RULES)

# ---------------------------------------------------------------------------
print("\n6. The ids come from ONE column, and the line says which ids share which targets "
      "(wave 4, G11)")
# ---------------------------------------------------------------------------
# Measured on the goal-function runs of 2026-10-03: the line listed the right groups, but they
# overlapped - each id sat in four of them - its head was inflated by place ids another column
# printed ('contains - N links to N different targets'), and it never said WHICH ids a target
# serves, so the answer gave half of them no second target. Ids whose links are all of a fan-out kind
# (a level's 'contains') no longer join the line - by the rule of section 7 (fix round 1; it was the
# one column printing the most ids first) - and when the ids fall into 2-4 sets by their own links
# (link types with at most GRAPH_TARGETS_PER_LINK targets), and some set holds two or more ids, the
# line says so: the links every set shares, once, then each set's size, one of its ids and its own
# links.
SETS1 = ("By their own links the 5 ids fall into 2 sets - all share logged_by ZR-9, plugs_into "
         "ZR-1; 3 of them (CM-3 among them) also have powered_by ZR-2; the other 2 (CM-1 among "
         "them) also have powered_by ZR-1.")
check("RED: the line splits the ids into sets by their own links, largest first",
      SETS1 in line1, line1)
check("RED: the sets come after the groups, and the line still ends with its fact",
      bool(line1) and line1.find(SETS1) > line1.find("powered_by ZR-1 (Switch Room A)")
      and line1.endswith(". " + SETS1 + " The rows above may show only some of these links."),
      line1[-400:])

CAMS_AT = dict(CAMS, columns=CAMS["columns"] + ["cam_level"],
               rows=[dict(r, cam_level="ZL-1") for r in CAMS["rows"]])
out6 = run("SELECT c.cam_tag, c.cam_level FROM \"bld_cams\" c JOIN \"bld_links\" l ON c.cam_tag = "
           "l.subject_id WHERE l.object_id = 'ZR-1' AND l.predicate = 'plugs_into'", Q1,
           tables=(LINKS, CAMS_AT))
line6 = (links_lines(out6) or [""])[0]
check("RED: an id another column prints (the place the units sit in) is not counted",
      "from the 5 ids the rows above print" in line6, line6)
check("RED: and its own links do not join the line", bool(line6) and "contains" not in line6, line6)
check("RED: the units' own groups and sets are all there", SETS1 in line6
      and "logged_by ZR-9 (Recorder Room) - 5 links" in line6, line6)

out6b = run("SELECT c.cam_tag FROM \"bld_cams\" c JOIN \"bld_links\" l ON c.cam_tag = l.subject_id "
            "WHERE l.object_id = 'ZR-2'", "Which units are powered from room ZR-2?")
line6b = (links_lines(out6b) or [""])[0]
check("one set - every id has the same links: no sets sentence",
      bool(line6b) and "fall into" not in line6b, line6b)
check("a set of single ids only says nothing the groups do not (section 3's two ids)",
      bool(line3d) and "fall into" not in line3d, line3d)
# Three units get a link of their own: CM-1, CM-2 and CM-3 are one set each, CM-4 and CM-5 one set.
FANNED = dict(LINKS, rows=LINK_ROWS + [
    _link(f"LK-7{i}", "unit", f"CM-{i}", f"tie_{i}", "place", "ZQ-0", "Spot 0", "")
    for i in range(1, 4)], row_count=len(LINK_ROWS) + 3)
line6c = (links_lines(run(ONE_TYPE, Q1, tables=(FANNED, CAMS))) or [""])[0]
check("RED: four sets, one of them two ids: the sentence names all four",
      "the 5 ids fall into 4 sets" in line6c and "2 of them (CM-4 among them) also have "
      "powered_by ZR-2" in line6c and "1 of them (CM-1) also has powered_by ZR-1, tie_1 ZQ-0"
      in line6c, line6c)
line6e = (links_lines(run("SELECT c.cam_tag FROM \"bld_cams\" c JOIN \"bld_links\" l ON c.cam_tag "
                          "= l.subject_id WHERE l.predicate = 'plugs_into'", Q1,
                          tables=(FANNED, CAMS))) or [""])[0]
check("five sets (CM-6 is a fifth): no sets sentence",
      "from the 6 ids" in line6e and "fall into" not in line6e, line6e)
FAN_ONE = dict(LINKS, rows=LINK_ROWS + [
    _link(f"LK-8{i}", "unit", f"CM-{i}", "tie_all", "place", f"ZT-{i}", f"Tee {i}", "")
    for i in range(1, 6)], row_count=len(LINK_ROWS) + 5)
line6d = (links_lines(run(ONE_TYPE, Q1, tables=(FAN_ONE, CAMS))) or [""])[0]
check("RED: a link type with more than four targets stays a count and never splits the sets",
      SETS1 in line6d and "tie_all - 5 links to 5 different targets" in line6d, line6d)

rule6 = next((ln for ln in openai_client.OUTPUT_FORMAT_RULES.splitlines() if "GRAPH LINKS" in ln),
             "")
check("RED: the GRAPH LINKS rule answers per set, naming every target of each set",
      "per set" in rule6 and "every target" in rule6, rule6)

# ---------------------------------------------------------------------------
print("\n7. The asked item is never lost, and a fan-out node's links are named, not denied "
      "(fix round 1, ruling W4A1-R2)")
# ---------------------------------------------------------------------------
# The review found the one-column rule losing the asked item: every link of one main unit, both ends
# printed, put its sections - themselves graph subjects - in the column printing the most ids, so the
# line covered only the sections, and said a section whose links are all of a fan-out kind (it feeds
# many units) "has no other link". The ids are now every printed graph subject, less those whose
# links are ALL of a fan-out kind (more than GRAPH_TARGETS_PER_LINK targets) - never an id the SQL
# names as a literal, never every id - and a set with nothing of its own beyond the shared pairs names
# its fan-out links by kind and count.
def _feed(ref, s, pred, o):
    return _link(ref, "unit", s, pred, "unit", o, "", "")


FEED_ROWS = ([_feed(f"FD-0{i}", "QM-1", "feeds", f"QS-{i}") for i in (1, 2, 3)]
             + [_feed(f"FD-1{i}", "QS-1", "feeds", f"QU-{i}") for i in range(1, 7)]
             + [_feed(f"FD-2{i}", "QS-2", "feeds", f"QU-{i}") for i in range(7, 12)]
             + [_feed("FD-30", "QS-3", "feeds", "QU-12")]
             + [_link("FD-40", "unit", "QS-1", "located_in", "place", "ZR-7", "Panel Room", ""),
                _link("FD-41", "unit", "QS-2", "located_in", "place", "ZR-7", "Panel Room", "")])
FEEDS = dict(LINKS, table_name="bld_feeds", rows=FEED_ROWS, row_count=len(FEED_ROWS))
out7 = run("SELECT subject_id, predicate, object_id FROM \"bld_feeds\" WHERE subject_id = 'QM-1'",
           "What does QM-1 feed?", tables=(FEEDS,), cards=())
line7 = (links_lines(out7) or [""])[0]
check("RED (R2): the asked main unit, named by the SQL, is one of the line's ids",
      "from the 3 ids the rows above print" in line7 and "(QM-1)" in line7, line7)
check("RED (R2): a node whose links are all of the fan-out kind gets those links named, by kind and "
      "count - never 'no other link'",
      "the other 1 (QM-1) has no link of the kinds above; feeds - 3 more not grouped" in line7
      and "no other link." not in line7, line7)
check("RED (R2): the sections it feeds are split by their own links, the fan-out kind a count",
      "2 of them (QS-1 among them) have located_in ZR-7" in line7
      and "feeds - 14 links to 14 different targets" in line7, line7)
check("RED (R2): a section whose links are all of the fan-out kind, and which the SQL does not name, "
      "is dropped", "QS-3" not in line7, line7)

out7b = run("SELECT DISTINCT subject_id FROM \"bld_links\" WHERE object_id IN ('ZR-1', 'ZR-2')", Q1)
line7b = (links_lines(out7b) or [""])[0]
check("RED (R2): a level printed in the SAME column as the units is dropped too - its links are all "
      "'contains', a fan-out kind", "from the 5 ids the rows above print" in line7b
      and "contains" not in line7b and SETS1 in line7b, line7b)
out7c = run("SELECT DISTINCT subject_id FROM \"bld_links\" WHERE predicate = 'contains'",
            "Which places contain rooms?")
line7c = (links_lines(out7c) or [""])[0]
check("an id whose links are all of a fan-out kind is kept when nothing else would be left",
      "from the 1 id the rows above print" in line7c
      and "contains - 6 links to 6 different targets" in line7c, line7c)
check("section 1's line is unchanged by the rule (section 6's sentence, every id kept)",
      SETS1 in line1 and "from the 5 ids the rows above print" in line1, line1)

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
