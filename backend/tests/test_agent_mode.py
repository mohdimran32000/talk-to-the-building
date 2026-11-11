"""test_agent_mode.py — acceptance tests for AGENT_MODE (one model, an index, four read-only tools).

Everything here runs offline: the model is a scripted fake, the database is a fake Supabase
client, and the provider adapters are fed recorded response shapes. No network, no keys.

Run:
    venv/Scripts/python -X utf8 tests/test_agent_mode.py
"""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import agent_mode as am

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


# ---------------------------------------------------------------------------
# Fixtures — invented, generic data (this repository is public).
# ---------------------------------------------------------------------------
TABLES = [
    {"table_name": "acme_pumps", "columns": ["tag", "level", "kw", "notes"], "row_count": 6,
     "rows": [{"tag": f"P-0{i}", "level": "L1" if i < 4 else "L2", "kw": str(1.5 * i), "notes": ""}
              for i in range(1, 7)]},
    {"table_name": "acme_rooms", "columns": ["room_id", "name"], "row_count": 2,
     "rows": [{"room_id": "R-1", "name": "Plant room"}, {"room_id": "R-2", "name": "Office"}]},
]
BIG = {"table_name": "acme_points", "columns": ["n"], "row_count": 450,
       "rows": [{"n": str(i)} for i in range(450)]}
CARDS = [
    {"table": "acme_pumps", "row_count": 6, "holds": "One row per pump: its tag, level and rating.",
     "one_row_is": "a pump", "identifier_column": "tag", "caveats": ["kw is the nameplate rating"],
     "declared_joins": [{"to": "acme_rooms", "on": "level"}]},
]
DOCS = [
    {"id": "d1", "file_name": "pumps_manual.md", "folder_path": "/om-pumps"},
    {"id": "d2", "file_name": "pumps_manifest.md", "folder_path": "/om-pumps"},
    {"id": "d3", "file_name": "rooms.md", "folder_path": "/spine"},
]


def ctx(tables=None, cards=None, docs=None, **kw):
    return am.AgentContext(user_id="u1", supabase_client=None,
                           tables=TABLES if tables is None else tables,
                           cards=CARDS if cards is None else cards,
                           documents=DOCS if docs is None else docs, **kw)


# ---------------------------------------------------------------------------
print("1. the switch is ON unless AGENT_MODE turns it off (0 / false / off / no)")
for value, want in ((None, True), ("", True), ("1", True), (" 1 ", True), ("true", True),
                    ("0", False), (" 0 ", False), ("false", False), ("OFF", False), ("no", False)):
    if value is None:
        os.environ.pop("AGENT_MODE", None)
    else:
        os.environ["AGENT_MODE"] = value
    check(f"AGENT_MODE={value!r} -> {want}", am.agent_mode_enabled() is want)
os.environ.pop("AGENT_MODE", None)

print("1b. the measured defaults: 16 tool calls, 180 seconds, Claude Sonnet 5.5")
check("DEFAULT_MAX_STEPS is 16", am.DEFAULT_MAX_STEPS == 16, am.DEFAULT_MAX_STEPS)
check("DEFAULT_TIME_BUDGET_S is 180", am.DEFAULT_TIME_BUDGET_S == 180.0, am.DEFAULT_TIME_BUDGET_S)
check("DEFAULT_AGENT_MODEL is anthropic:claude-sonnet-5-5",
      getattr(am, "DEFAULT_AGENT_MODEL", None) == "anthropic:claude-sonnet-5-5")

# ---------------------------------------------------------------------------
print("2. the index: one line per table, documents grouped by folder")
idx = am.build_index(CARDS, TABLES + [BIG], DOCS)
pump_line = next((l for l in idx.splitlines() if "acme_pumps" in l), "")
check("a carded table names its rows, what it holds and its identifier",
      "6 rows" in pump_line and "One row per pump" in pump_line and "id: tag" in pump_line, pump_line)
check("a table with no card is still listed", any("acme_points" in l and "450 rows" in l
                                                  for l in idx.splitlines()), idx)
check("each document is listed under its folder",
      "/om-pumps: pumps_manifest.md, pumps_manual.md" in idx and "/spine: rooms.md" in idx, idx)
long_card = [{"table": "acme_rooms", "holds": "x" * 900}]
check("a long holds sentence is cut to one short line",
      max(len(l) for l in am.build_index(long_card, TABLES, []).splitlines()) < 300)

# ---------------------------------------------------------------------------
print("3. run_sql: read-only, capped, shaped, never raises")
c = ctx()
out = c.run_sql("SELECT tag, level FROM acme_pumps ORDER BY tag")
check("rows come back", "P-01" in out and "P-06" in out, out)
check("a RESULT SHAPE line states the row count", "RESULT SHAPE - rows: 6" in out, out)
c_big = ctx(tables=[BIG], cards=[])
out = c_big.run_sql("SELECT n FROM acme_points")
check("results are capped at 200 rows and say so",
      "shown 200 of 450 rows" in out and out.count("\n") < 230, out[-200:])
check("numeric columns are loaded as numbers",
      "21" in c.run_sql("SELECT SUM(kw) AS total FROM acme_pumps")
      or "31.5" in c.run_sql("SELECT SUM(kw) AS total FROM acme_pumps"),
      c.run_sql("SELECT SUM(kw) AS total FROM acme_pumps"))
for bad in ("DROP TABLE acme_pumps", "INSERT INTO acme_rooms VALUES ('R-3','x')",
            "WITH x AS (SELECT 1) DELETE FROM acme_rooms",
            "SELECT 1; DROP TABLE acme_pumps", "COPY acme_pumps TO 'out.csv'",
            "ATTACH 'other.db'", "PRAGMA database_list", "SET threads=1"):
    r = c.run_sql(bad)
    check(f"refused: {bad[:40]}", r.startswith("REFUSED"), r)
check("tables survive the refused statements", "P-01" in c.run_sql("SELECT tag FROM acme_pumps"))
r = c.run_sql("SELECT * FROM read_csv('C:/Windows/win.ini')")
check("a local file cannot be read", r.startswith("SQL ERROR") and "for 16-bit" not in r, r)
check("a string literal holding a keyword is not refused",
      not c.run_sql("SELECT 'drop table' AS t").startswith("REFUSED"))
r = c.run_sql("SELECT nope FROM acme_pumps")
check("a bad column returns the DuckDB error so the model can repair it",
      r.startswith("SQL ERROR") and "nope" in r, r)
c_slow = ctx(sql_timeout=1)
r = c_slow.run_sql("SELECT count(*) FROM range(100000000) a, range(100000000) b")
check("a runaway query times out and returns text", r.startswith("SQL ERROR") and "timeout" in r.lower(), r)
long_notes = [{"table_name": "acme_n", "columns": ["v"], "row_count": 1, "rows": [{"v": "y" * 5000}]}]
r = ctx(tables=long_notes, cards=[]).run_sql("SELECT v FROM acme_n")
check("a long cell is cut", len(r) < 1500, len(r))

# ---------------------------------------------------------------------------
print("4. describe_table")
d = c.describe_table("acme_pumps")
check("columns, sample values and the card's facts",
      "tag" in d and "P-01" in d and "One row per pump" in d and "kw is the nameplate rating" in d
      and "acme_rooms" in d, d)
check("case-insensitive name", "One row per pump" in c.describe_table("ACME_PUMPS"))
d = c.describe_table("acme_pump")
check("an unknown name suggests close names", d.startswith("UNKNOWN TABLE") and "acme_pumps" in d, d)

# ---------------------------------------------------------------------------
print("5. search_docs and read_document")
seen = {}


def fake_retrieve(query, user_id, supabase_client, top_k=5, **kw):
    seen.update(query=query, user_id=user_id, top_k=top_k)
    return [{"file_name": "pumps_manual.md", "content": "Pump P-01 is serviced yearly. " * 200}]


c_docs = ctx(retrieve=fake_retrieve)
r = c_docs.search_docs("service interval")
check("search passes the query, the user and top_k=8",
      seen == {"query": "service interval", "user_id": "u1", "top_k": 8}, seen)
check("each excerpt names its document and is cut", "pumps_manual.md" in r and len(r) < 3000, len(r))
check("no hits says so", "no excerpts" in ctx(retrieve=lambda *a, **k: []).search_docs("x").lower())

read_seen = {}


def fake_read(args, user_id, supabase_client):
    read_seen.update(document_id=args.document_id, offset=args.offset, user_id=user_id)
    return {"tool": "read_document", "file_name": "pumps_manual.md", "start_line": args.offset,
            "end_line": args.offset + 1, "total_lines": 900, "content": "1→hello\n2→world",
            "truncation_marker": None}


c_read = ctx(reader=fake_read)
r = c_read.read_document("pumps_manual.md", 1)
check("a document is resolved among the user's own documents by name",
      read_seen == {"document_id": "d1", "offset": 1, "user_id": "u1"}, read_seen)
check("the reply says which lines were shown and how to continue",
      "lines 1-2 of 900" in r and "offset=3" in r, r)
c_read.read_document("/om-pumps/PUMPS_MANUAL.MD", 5)
check("a folder path and any case resolve too", read_seen.get("offset") == 5 and read_seen["document_id"] == "d1")
r = c_read.read_document("pumps", 1)
check("an ambiguous name lists the candidates",
      r.startswith("UNKNOWN DOCUMENT") and "pumps_manual.md" in r and "pumps_manifest.md" in r, r)

# ---------------------------------------------------------------------------
print("6. the loop")


class FakeAdapter:
    """Replays scripted turns. Records whether tools were allowed on each step."""

    def __init__(self, turns):
        self.turns, self.allowed, self.results, self.started = list(turns), [], [], None

    def start(self, system, messages):
        self.started = (system, messages)

    def step(self, allow_tools):
        self.allowed.append(allow_tools)
        if not allow_tools:
            return am.Turn(text="final answer", tool_calls=[])
        return self.turns.pop(0) if self.turns else am.Turn(text="done", tool_calls=[])

    def add_tool_results(self, results):
        self.results.append(results)


def call(_tool, **args):
    return am.ToolCall(id=f"c{len(args)}{_tool}", name=_tool, args=args)


fa = FakeAdapter([am.Turn(text="42 pumps", tool_calls=[])])
res = am.run_agent([{"role": "user", "content": "how many?"}], ctx(), fa, max_steps=8, time_budget=90)
check("an answer with no tools is returned as is", res["answer"] == "42 pumps" and res["steps"] == [], res)
check("the system prompt carries the index", "acme_pumps" in fa.started[0])

fa = FakeAdapter([am.Turn(text="", tool_calls=[call("run_sql", query="SELECT count(*) AS n FROM acme_pumps")]),
                  am.Turn(text="6 pumps, from acme_pumps", tool_calls=[])])
events = []
res = am.run_agent([{"role": "user", "content": "how many?"}], ctx(), fa, max_steps=8, time_budget=90,
                   on_event=events.append)
check("a tool call runs and its result goes back", "6" in fa.results[0][0][2] and res["answer"].startswith("6 pumps"),
      fa.results)
check("each tool call is reported as an event", events == [("run_sql", {"query": "SELECT count(*) AS n FROM acme_pumps"})],
      events)

always = [am.Turn(text="", tool_calls=[call("describe_table", name="acme_pumps")]) for _ in range(20)]
fa = FakeAdapter(always)
res = am.run_agent([{"role": "user", "content": "q"}], ctx(), fa, max_steps=3, time_budget=90)
check("the step cap stops tool use at exactly max_steps", len(res["steps"]) == 3, len(res["steps"]))
check("then one step with tools disabled gives the answer",
      fa.allowed[-1] is False and res["answer"] == "final answer" and res["stopped"] == "step_cap", (fa.allowed, res))
check("the last allowed result tells the model to answer now", "answer now" in fa.results[-1][-1][2].lower(),
      fa.results[-1])

fa = FakeAdapter([am.Turn(text="", tool_calls=[call("describe_table", name="acme_pumps"),
                                                call("describe_table", name="acme_rooms"),
                                                call("describe_table", name="acme_pumps")])])
res = am.run_agent([{"role": "user", "content": "q"}], ctx(), fa, max_steps=2, time_budget=90)
check("parallel calls past the cap are not run",
      len(res["steps"]) == 2 and "not run" in fa.results[0][2][2].lower(), fa.results)

clock = iter([0, 0, 100, 100, 100, 100, 100])
fa = FakeAdapter(always[:])
res = am.run_agent([{"role": "user", "content": "q"}], ctx(), fa, max_steps=8, time_budget=90,
                   clock=lambda: next(clock))
check("the time budget stops tool use", res["stopped"] == "time_budget" and fa.allowed[-1] is False, (fa.allowed, res))

fa = FakeAdapter([am.Turn(text="", tool_calls=[call("delete_everything")]), am.Turn(text="ok", tool_calls=[])])
res = am.run_agent([{"role": "user", "content": "q"}], ctx(), fa, max_steps=8, time_budget=90)
check("an unknown tool returns an error result and the loop continues",
      "unknown tool" in fa.results[0][0][2].lower() and res["answer"] == "ok", fa.results)


def boom(*a, **k):
    raise RuntimeError("search down")


fa = FakeAdapter([am.Turn(text="", tool_calls=[call("search_docs", query="x")]), am.Turn(text="ok", tool_calls=[])])
res = am.run_agent([{"role": "user", "content": "q"}], ctx(retrieve=boom), fa, max_steps=8, time_budget=90)
check("a tool that raises returns its error as text", "search down" in fa.results[0][0][2], fa.results)

fa = FakeAdapter([am.Turn(text="", tool_calls=[call("run_sql")]), am.Turn(text="ok", tool_calls=[])])
am.run_agent([{"role": "user", "content": "q"}], ctx(), fa, max_steps=8, time_budget=90)
check("a missing argument returns an error, not a crash", "error" in fa.results[0][0][2].lower(), fa.results)

# ---------------------------------------------------------------------------
print("6b. one turn's tool calls run at the same time; results keep their order")
import threading  # noqa: E402
import time  # noqa: E402


def slow_retrieve(query, user_id, supabase_client, top_k):
    time.sleep(0.4)
    return [{"file_name": f"doc-{query}.md", "content": f"excerpt for {query}"}]


three = [call("search_docs", query=q) for q in ("a", "b", "c")]
for i, tc in enumerate(three):
    tc.id = f"s{i}"
fa = FakeAdapter([am.Turn(text="", tool_calls=three), am.Turn(text="ok", tool_calls=[])])
t0 = time.monotonic()
res = am.run_agent([{"role": "user", "content": "q"}], ctx(retrieve=slow_retrieve), fa, max_steps=8, time_budget=90)
took = time.monotonic() - t0
check("three 0.4 s searches in one turn take well under 1.2 s", took < 0.9, f"{took:.2f}s")
check("each result goes back against its own call, in the call order",
      [r[0] for r in fa.results[0]] == ["s0", "s1", "s2"]
      and all(f"doc-{q}.md" in r[2] for q, r in zip("abc", fa.results[0])), fa.results)
check("steps are recorded in the call order", [s["args"]["query"] for s in res["steps"]] == ["a", "b", "c"],
      res["steps"])

sqls = [call("run_sql", query=f"SELECT count(*) AS n FROM acme_pumps WHERE kw > {k}") for k in (0, 2, 4, 6)]
for i, tc in enumerate(sqls):
    tc.id = f"q{i}"
want_counts = ["6", "5", "4", "2"]  # kw = 1.5, 3, 4.5, 6, 7.5, 9
for attempt in range(3):
    fa = FakeAdapter([am.Turn(text="", tool_calls=sqls), am.Turn(text="ok", tool_calls=[])])
    am.run_agent([{"role": "user", "content": "q"}], ctx(), fa, max_steps=8, time_budget=90)
    got = [r[2].splitlines()[1].strip() for r in fa.results[0]]
    check(f"four queries at once on one fresh context all answer right (try {attempt + 1})",
          got == want_counts, fa.results[0])

c_par = ctx()
fa = FakeAdapter([am.Turn(text="", tool_calls=[call("run_sql", query="SELECT * FROM read_csv('C:/Windows/win.ini')"),
                                                call("run_sql", query="SELECT tag FROM acme_pumps")]),
                  am.Turn(text="ok", tool_calls=[])])
am.run_agent([{"role": "user", "content": "q"}], c_par, fa, max_steps=8, time_budget=90)
check("a parallel query still cannot read a local file",
      fa.results[0][0][2].startswith("SQL ERROR") and "for 16-bit" not in fa.results[0][0][2], fa.results[0][0])
check("and its neighbour still answers", "P-01" in fa.results[0][1][2], fa.results[0][1])

done = []
fa = FakeAdapter([am.Turn(text="", tool_calls=[call("run_sql", query="SELECT tag FROM acme_pumps"),
                                                call("search_docs", query="pump")]),
                  am.Turn(text="ok", tool_calls=[])])
lock = threading.Lock()


def on_done(ev):
    with lock:
        done.append(ev)


am.run_agent([{"role": "user", "content": "q"}], ctx(retrieve=slow_retrieve), fa, max_steps=8, time_budget=90,
             on_done=on_done)
names = sorted(n for n, _ in done)
check("each finished tool is reported once, with a short detail",
      names == ["run_sql", "search_docs"] and all(isinstance(d, str) and d for _, d in done), done)
check("a query's detail says how many rows came back",
      any(n == "run_sql" and "6 rows" in d for n, d in done), done)
check("a search's detail says how many excerpts came back",
      any(n == "search_docs" and "1 excerpt" in d for n, d in done), done)
err_done = []
fa = FakeAdapter([am.Turn(text="", tool_calls=[call("run_sql", query="SELECT nope FROM acme_pumps")]),
                  am.Turn(text="ok", tool_calls=[])])
am.run_agent([{"role": "user", "content": "q"}], ctx(), fa, max_steps=8, time_budget=90, on_done=err_done.append)
check("a failed tool is reported as an error", err_done and err_done[0][1].lower().startswith("error"), err_done)

# ---------------------------------------------------------------------------
print("6c. the answer streams as it is written")


class StreamingFake(FakeAdapter):
    """Like FakeAdapter, but speaks each turn's text in pieces through on_text."""

    def step(self, allow_tools, on_text=None):
        turn = super().step(allow_tools)
        if on_text and turn.text:
            for piece in turn.text.split(" "):
                on_text(piece + " ")
            turn = am.Turn(text="".join(p + " " for p in turn.text.split(" ")), tool_calls=turn.tool_calls,
                           usage=turn.usage)
        return turn


pieces = []
fa = StreamingFake([am.Turn(text="", tool_calls=[call("run_sql", query="SELECT 1")]),
                    am.Turn(text="Six pumps in all.", tool_calls=[])])
res = am.run_agent([{"role": "user", "content": "q"}], ctx(), fa, max_steps=8, time_budget=90, on_text=pieces.append)
check("the final answer arrives in pieces", len(pieces) == 4 and "".join(pieces).strip() == "Six pumps in all.", pieces)
check("the result says the answer was streamed", res.get("streamed") is True and res["answer"].strip() == "Six pumps in all.",
      res)

pieces = []
fa = StreamingFake([am.Turn(text="Checking the register.", tool_calls=[call("run_sql", query="SELECT 1")]),
                    am.Turn(text="Six.", tool_calls=[])])
res = am.run_agent([{"role": "user", "content": "q"}], ctx(), fa, max_steps=8, time_budget=90, on_text=pieces.append)
check("text written before a tool call is kept apart from the answer",
      "".join(pieces).replace(" ", "") == "Checkingtheregister.\n\nSix.", repr("".join(pieces)))
check("the answer itself is still only the last turn's text", res["answer"].strip() == "Six.", res["answer"])

pieces = []
fa = FakeAdapter([am.Turn(text="Six.", tool_calls=[])])
res = am.run_agent([{"role": "user", "content": "q"}], ctx(), fa, max_steps=8, time_budget=90, on_text=pieces.append)
check("a model that cannot stream still answers, unstreamed",
      pieces == [] and res["answer"] == "Six." and not res.get("streamed"), (pieces, res))

# ---------------------------------------------------------------------------
print("7. the prompt is generic")
p = am.system_prompt("INDEX-HERE", max_steps=8)
check("it carries the index and the step cap", "INDEX-HERE" in p and "8" in p)
check("no unfilled placeholders", "{" not in p.replace("INDEX-HERE", ""), p)
check("four rules, no pipeline rule blocks", "RESULT SHAPE" in p and "NOTES INSIDE THE FIGURE" not in p)

# ---------------------------------------------------------------------------
print("8. the OpenRouter adapter")
posted = []


def fake_post(url, headers, body):
    posted.append((url, headers, body))
    if len(posted) == 1:
        return {"provider": "SomeHost", "choices": [{"message": {
            "role": "assistant", "content": None, "reasoning_details": [{"type": "x"}],
            "tool_calls": [{"id": "t1", "type": "function",
                            "function": {"name": "run_sql", "arguments": "{\"query\": \"SELECT 1\"}"}}]}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "cost": 0.0001,
                      "prompt_tokens_details": {"cached_tokens": 40}}}
    return {"provider": "SomeHost", "choices": [{"message": {"role": "assistant", "content": "the answer"}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 5, "cost": 0.0002}}


ad = am.OpenRouterAdapter("deepseek/some-flash", api_key="k", post=fake_post)
ad.start("SYS", [{"role": "user", "content": "q"}])
t = ad.step(allow_tools=True)
url, headers, body = posted[0]
check("posts to OpenRouter's chat endpoint with the key", url.endswith("/chat/completions")
      and headers["Authorization"] == "Bearer k")
check("the safe provider rule is on every request",
      body["provider"] == {"quantizations": ["fp8", "bf16", "fp16"], "data_collection": "deny",
                           "zdr": True, "sort": "price", "allow_fallbacks": True}, body.get("provider"))
check("usage accounting is requested", body.get("usage") == {"include": True})
check("all four tools are offered", sorted(x["function"]["name"] for x in body["tools"])
      == ["describe_table", "read_document", "run_sql", "search_docs"])
check("a tool call is parsed", t.tool_calls == [am.ToolCall("t1", "run_sql", {"query": "SELECT 1"})], t)
check("its usage is recorded with the host",
      t.usage == {"input": 100, "output": 10, "cached": 40, "cost": 0.0001, "provider": "SomeHost"}, t.usage)
ad.add_tool_results([("t1", "run_sql", "n\n1")])
t2 = ad.step(allow_tools=False)
body2 = posted[1][2]
check("the assistant turn is replayed with its reasoning", body2["messages"][2].get("reasoning_details") == [{"type": "x"}],
      body2["messages"][2])
check("the result goes back as a tool message",
      body2["messages"][3] == {"role": "tool", "tool_call_id": "t1", "content": "n\n1"}, body2["messages"][3])
check("finishing sets tool_choice none", body2.get("tool_choice") == "none")
check("the final text comes back", t2.text == "the answer" and t2.tool_calls == [])
ad_bad = am.OpenRouterAdapter("m", api_key="k", post=lambda u, h, b: {"choices": [{"message": {
    "role": "assistant", "content": "", "tool_calls": [{"id": "t", "type": "function",
                                                        "function": {"name": "run_sql", "arguments": "not json"}}]}}]})
ad_bad.start("S", [{"role": "user", "content": "q"}])
t = ad_bad.step(True)
check("unparseable arguments become an empty dict, not a crash", t.tool_calls[0].args == {}, t)
try:
    a3 = am.OpenRouterAdapter("m", api_key="k", post=lambda u, h, b: {"error": {"message": "no host matches"}})
    a3.start("S", [{"role": "user", "content": "q"}])
    a3.step(True)
    check("error body raises", False)
except RuntimeError as e:
    check("error body raises", "no host matches" in str(e), str(e))

# ---------------------------------------------------------------------------
print("9. the Gemini adapter's parsing")
fc = SimpleNamespace(name="describe_table", args={"name": "acme_pumps"}, id=None)
resp = SimpleNamespace(
    candidates=[SimpleNamespace(content=SimpleNamespace(role="model", parts=[
        SimpleNamespace(text=None, function_call=fc, thought=None),
        SimpleNamespace(text="thinking", function_call=None, thought=True)]))],
    usage_metadata=SimpleNamespace(prompt_token_count=1000, candidates_token_count=50,
                                   thoughts_token_count=200, cached_content_token_count=600))
t = am.GeminiAdapter.parse(resp)
check("a function call is parsed", [(x.name, x.args) for x in t.tool_calls] == [("describe_table", {"name": "acme_pumps"})], t)
check("thought text is not the answer", t.text == "", repr(t.text))
check("usage counts thinking as output", t.usage == {"input": 1000, "output": 250, "cached": 600}, t.usage)
resp2 = SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(role="model", parts=[
    SimpleNamespace(text="Six pumps.", function_call=None, thought=None)]))], usage_metadata=None)
t = am.GeminiAdapter.parse(resp2)
check("plain text is the answer", t.text == "Six pumps." and t.tool_calls == [] and t.usage["input"] == 0, t)
check("an empty candidate list is an empty turn", am.GeminiAdapter.parse(SimpleNamespace(candidates=[], usage_metadata=None)).text == "")

# ---------------------------------------------------------------------------
print("9b. the Claude adapter")
claude_calls = []


class _FakeMessages:
    def __init__(self, replies):
        self.replies = list(replies)

    def create(self, **kw):
        claude_calls.append(kw)
        return self.replies.pop(0)


think = SimpleNamespace(type="thinking", thinking="", signature="sig")
use = SimpleNamespace(type="tool_use", id="tu1", name="run_sql", input={"query": "SELECT 1"})
r1 = SimpleNamespace(content=[think, SimpleNamespace(type="text", text="Checking."), use], stop_reason="tool_use",
                     usage=SimpleNamespace(input_tokens=200, output_tokens=100, cache_read_input_tokens=1000,
                                           cache_creation_input_tokens=4000))
r2 = SimpleNamespace(content=[SimpleNamespace(type="text", text="Six pumps.")], stop_reason="end_turn",
                     usage=SimpleNamespace(input_tokens=50, output_tokens=20, cache_read_input_tokens=5000,
                                           cache_creation_input_tokens=0))
ca = am.AnthropicAdapter("claude-sonnet-5-5", SimpleNamespace(messages=_FakeMessages([r1, r2])))
ca.start("SYS", [{"role": "user", "content": "q"}])
t = ca.step(allow_tools=True)
kw = claude_calls[0]
check("the model id is sent", kw["model"] == "claude-sonnet-5-5")
check("the system prompt is a cached block",
      kw["system"] == [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}], kw["system"])
check("the growing conversation is cached too", kw.get("cache_control") == {"type": "ephemeral"})
check("all four tools are offered with their schemas",
      sorted(x["name"] for x in kw["tools"]) == ["describe_table", "read_document", "run_sql", "search_docs"]
      and all(x["input_schema"]["type"] == "object" for x in kw["tools"]), kw["tools"])
check("tools are free to call", kw["tool_choice"] == {"type": "auto"})
check("no sampling parameter is sent (Sonnet 5.5 rejects non-default ones)",
      not ({"temperature", "top_p", "top_k"} & set(kw)), sorted(kw))
check("a tool call is parsed", t.tool_calls == [am.ToolCall("tu1", "run_sql", {"query": "SELECT 1"})], t)
check("the text beside a tool call is kept", t.text == "Checking.", repr(t.text))
# 200 + 1000 + 4000 input; $2 in, $10 out, $0.20 cache read, $2.50 cache write per million
want = (200 * 2.00 + 100 * 10.00 + 1000 * 0.20 + 4000 * 2.50) / 1e6
check("usage counts every input token and prices the cache",
      t.usage["input"] == 5200 and t.usage["output"] == 100 and t.usage["cached"] == 1000
      and abs(t.usage["cost"] - want) < 1e-12 and t.usage["provider"] == "anthropic", t.usage)
ca.add_tool_results([("tu1", "run_sql", "n\n1")])
t2 = ca.step(allow_tools=False)
msgs = claude_calls[1]["messages"]
check("the assistant turn is replayed unchanged, thinking included", msgs[1] == {"role": "assistant", "content": r1.content},
      msgs[1])
check("the results go back as tool_result blocks in one user turn",
      msgs[2] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "n\n1"}]}, msgs[2])
check("finishing sets tool_choice none", claude_calls[1]["tool_choice"] == {"type": "none"})
check("the final text comes back", t2.text == "Six pumps." and t2.tool_calls == [])
check("a model without a known price refuses rather than guess",
      _raises(lambda: am.AnthropicAdapter("claude-unknown-9", SimpleNamespace(messages=None))))
check("no effort is sent unless one is asked for (the model's default applies)",
      "output_config" not in claude_calls[0] and "output_config" not in claude_calls[1], sorted(claude_calls[0]))
claude_calls.clear()
oa = am.AnthropicAdapter("claude-opus-5-5", SimpleNamespace(messages=_FakeMessages([r1])), effort="low")
oa.start("SYS", [{"role": "user", "content": "q"}])
ot = oa.step(allow_tools=True)
check("an asked-for effort is sent as output_config", claude_calls[0].get("output_config") == {"effort": "low"},
      claude_calls[0].get("output_config"))
check("thinking is left to the model's default (Opus 5.5 rejects disabling it)", "thinking" not in claude_calls[0])
# Opus 5.5: $4 in, $20 out, $0.20 cache read, $5.00 cache write (1.25x) per million
want_o = (200 * 4.00 + 100 * 20.00 + 1000 * 0.20 + 4000 * 5.00) / 1e6
check("Opus is priced at its own rates", abs(ot.usage["cost"] - want_o) < 1e-12, ot.usage)
check("an unknown effort level refuses rather than send it",
      _raises(lambda: am.AnthropicAdapter("claude-opus-5-5", SimpleNamespace(messages=None), effort="extreme")))

# ---------------------------------------------------------------------------
print("9c. the Claude adapter streams when asked")


class _Block(SimpleNamespace):
    """A streamed content block: like the SDK's, it carries an SDK-only parsed_output field."""

    def model_dump(self, mode="python", exclude_none=False):
        d = dict(vars(self))
        d["parsed_output"] = None
        return {k: v for k, v in d.items() if not (exclude_none and v is None)}


class _FakeStream:
    def __init__(self, events, final):
        self.events, self.final = events, final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self.events)

    def get_final_message(self):
        return self.final


class _StreamingMessages(_FakeMessages):
    def __init__(self, replies, streams):
        super().__init__(replies)
        self.streams, self.stream_kw = list(streams), []

    def stream(self, **kw):
        self.stream_kw.append(kw)
        return self.streams.pop(0)


s_think = _Block(type="thinking", thinking="", signature="sig2")
s_text = _Block(type="text", text="Six pumps.", citations=None)
final = SimpleNamespace(content=[s_think, s_text], stop_reason="end_turn",
                        usage=SimpleNamespace(input_tokens=50, output_tokens=20, cache_read_input_tokens=5000,
                                              cache_creation_input_tokens=0))
events = [SimpleNamespace(type="message_start"), SimpleNamespace(type="text", text="Six "),
          SimpleNamespace(type="content_block_delta"), SimpleNamespace(type="text", text="pumps.")]
claude_calls.clear()
sm = _StreamingMessages([], [_FakeStream(events, final)])
sa = am.AnthropicAdapter("claude-sonnet-5-5", SimpleNamespace(messages=sm))
sa.start("SYS", [{"role": "user", "content": "q"}])
got = []
st = sa.step(allow_tools=True, on_text=got.append)
check("with on_text the stream is used, not create", len(sm.stream_kw) == 1 and claude_calls == [], (sm.stream_kw, claude_calls))
skw = sm.stream_kw[0]
check("the streamed request is the same request (model, cached system, tools, cache, tool choice)",
      skw["model"] == "claude-sonnet-5-5" and skw["system"][0]["cache_control"] == {"type": "ephemeral"}
      and len(skw["tools"]) == 4 and skw.get("cache_control") == {"type": "ephemeral"}
      and skw["tool_choice"] == {"type": "auto"}, sorted(skw))
check("text deltas reach on_text as they come", got == ["Six ", "pumps."], got)
check("the turn's text is the final message's text", st.text == "Six pumps." and st.tool_calls == [], st)
check("the streamed turn is priced like any other", st.usage["output"] == 20 and st.usage["cached"] == 5000
      and st.usage["cost"] > 0, st.usage)
replayed = sa.messages[-1]
check("the streamed turn is replayed as plain blocks, thinking signature kept, no SDK-only fields",
      replayed["role"] == "assistant"
      and replayed["content"] == [{"type": "thinking", "thinking": "", "signature": "sig2"},
                                  {"type": "text", "text": "Six pumps."}], replayed)

# ---------------------------------------------------------------------------
print("10. adapter choice from AGENT_MODEL")
os.environ["AGENT_MODEL"] = "openrouter:deepseek/some-flash"
os.environ["OPENROUTER_API_KEY"] = "k"
a = am.adapter_from_env()
check("openrouter:<id>", isinstance(a, am.OpenRouterAdapter) and a.model == "deepseek/some-flash")
os.environ["AGENT_MODEL"] = "gemini:gemini-x-flash"
a = am.adapter_from_env(gemini_client=object())
check("gemini:<id>", isinstance(a, am.GeminiAdapter) and a.model == "gemini-x-flash")
os.environ["AGENT_MODEL"] = "anthropic:claude-sonnet-5-5"
a = am.adapter_from_env(anthropic_client=SimpleNamespace(messages=None))
check("anthropic:<id>", isinstance(a, am.AnthropicAdapter) and a.model == "claude-sonnet-5-5")
check("no effort unless AGENT_ANTHROPIC_EFFORT is set", a.effort is None)
os.environ["AGENT_ANTHROPIC_EFFORT"] = "medium"
a = am.adapter_from_env(anthropic_client=SimpleNamespace(messages=None))
check("AGENT_ANTHROPIC_EFFORT reaches the adapter", a.effort == "medium")
os.environ.pop("AGENT_ANTHROPIC_EFFORT", None)
for unset in (None, "  "):
    if unset is None:
        os.environ.pop("AGENT_MODEL", None)
    else:
        os.environ["AGENT_MODEL"] = unset
    try:
        a = am.adapter_from_env(anthropic_client=SimpleNamespace(messages=None))
    except Exception as e:  # noqa: BLE001 - reported as the failure
        a = e
    check(f"AGENT_MODEL={unset!r} -> Claude Sonnet 5.5",
          isinstance(a, am.AnthropicAdapter) and a.model == "claude-sonnet-5-5", a)
os.environ["AGENT_MODEL"] = "nonsense"
try:
    am.adapter_from_env()
    check("an unknown provider raises", False)
except ValueError:
    check("an unknown provider raises", True)
for k in ("AGENT_MODEL", "OPENROUTER_API_KEY"):
    os.environ.pop(k, None)

# ---------------------------------------------------------------------------
print("11. usage is logged for the spend cap")
am.reset_usage()
fa = FakeAdapter([am.Turn(text="a", tool_calls=[], usage={"input": 5, "output": 1, "cached": 0})])
am.run_agent([{"role": "user", "content": "q"}], ctx(), fa, max_steps=8, time_budget=90)
check("each model call's usage lands in the log", am.usage_log() == [{"input": 5, "output": 1, "cached": 0}],
      am.usage_log())

# ---------------------------------------------------------------------------
print("12. the chat stream: steps and answer go out while the model works")
real_from_db = am.AgentContext.from_database
am.AgentContext.from_database = classmethod(lambda cls, user_id, sb: ctx())
try:
    seen_start = threading.Event()

    class WaitsForTheScreen(StreamingFake):
        """Holds the second model call until the consumer has seen the first tool start."""

        def step(self, allow_tools, on_text=None):
            if self.allowed:
                self.saw_it = seen_start.wait(timeout=3)
            return super().step(allow_tools, on_text)

    wa = WaitsForTheScreen([am.Turn(text="", tool_calls=[call("run_sql", query="SELECT tag FROM acme_pumps")]),
                            am.Turn(text="Six pumps.", tool_calls=[])])
    out = []
    for ev in am.stream_agent([{"role": "user", "content": "q"}], "u1", None, adapter=wa):
        out.append(ev)
        if ev[0] == "tool_start":
            seen_start.set()
    check("a tool start reaches the screen before the model has finished", getattr(wa, "saw_it", False), out)
    kinds = [k for k, _ in out]
    check("the order is start, done, answer pieces, done",
          kinds[:2] == ["tool_start", "tool_done"] and kinds[-1] == "done"
          and all(k == "token" for k in kinds[2:-1]) and len(kinds) > 4, kinds)
    check("the streamed pieces make the answer, sent once",
          "".join(d for k, d in out if k == "token").strip() == "Six pumps.", out)
    check("tool_start keeps its shape", json.loads(out[0][1]) == {"tool": "run_sql", "args": {"query": "SELECT tag FROM acme_pumps"}},
          out[0])
    check("tool_done names the tool and says what came back",
          json.loads(out[1][1])["tool"] == "run_sql" and "6 rows" in json.loads(out[1][1])["detail"], out[1])
    check("the run is still recorded for the eval", am.last_run().get("steps") and "seconds" in am.last_run(),
          am.last_run())

    plain = FakeAdapter([am.Turn(text="Two rooms.", tool_calls=[])])
    out = list(am.stream_agent([{"role": "user", "content": "q"}], "u1", None, adapter=plain))
    check("a model that cannot stream sends its answer as one token at the end",
          out == [("token", "Two rooms."), ("done", "")], out)

    class Broken(FakeAdapter):
        def step(self, allow_tools):
            raise RuntimeError("API down")

    try:
        list(am.stream_agent([{"role": "user", "content": "q"}], "u1", None, adapter=Broken([])))
        check("a model error still reaches the caller", False, "no exception")
    except RuntimeError as e:
        check("a model error still reaches the caller", "API down" in str(e), e)
finally:
    am.AgentContext.from_database = real_from_db

# ---------------------------------------------------------------------------
print("13. loading: rows go in by the chunk, and the database is kept until the data changes")
import duckdb  # noqa: E402
from app.services.sql_tool import _infer_column_types, _loaded_value  # noqa: E402

ODD = {"table_name": "acme_odd", "columns": ["code", "qty", "note", "naïve"], "row_count": 6,
       "rows": [{"code": "0552", "qty": "1,200", "note": None, "naïve": "é"},
                {"code": "17", "qty": "N/A", "note": "", "naïve": None},
                {"code": None, "qty": "3.5", "note": "x,y", "naïve": "ok"},
                {"code": "9", "qty": None, "note": "'quoted'", "naïve": ""},
                {"code": "10", "qty": "4", "note": "line\nbreak", "naïve": "z"},
                {"code": "11", "qty": "5", "note": "z", "naïve": "z"}]}
WIDE = {"table_name": "acme_wide", "columns": [f"c{i}" for i in range(60)], "row_count": 700,
        "rows": [{f"c{i}": (str(r * i) if i % 3 else f"t{r}-{i}") for i in range(60)} for r in range(700)]}


def dump(con, name):
    cols = con.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ? "
                       "ORDER BY ordinal_position", [name]).fetchall()
    return cols, con.execute(f'SELECT * FROM "{name}"').fetchall()


for t in (ODD, WIDE, TABLES[0]):
    types_ = _infer_column_types(t["columns"], t["rows"])
    vals = [[_loaded_value(r.get(c), types_[c]) for c in t["columns"]] for r in t["rows"]]
    ref = duckdb.connect(":memory:")
    ref.execute(f'CREATE TABLE "{t["table_name"]}" (' + ", ".join(f'"{c}" {types_[c]}' for c in t["columns"]) + ")")
    ref.executemany(f'INSERT INTO "{t["table_name"]}" VALUES ({", ".join("?" * len(t["columns"]))})', vals)
    new = ctx(tables=[t], cards=[])._connection()
    check(f"{t['table_name']}: the chunked load holds exactly what the row-by-row load held",
          dump(new, t["table_name"]) == dump(ref, t["table_name"]))


class _FakeQuery:
    def __init__(self, sb, name):
        self.sb, self.name, self.cols, self.user = sb, name, "*", None

    def select(self, cols):
        self.cols = cols
        return self

    def eq(self, col, value):
        if col == "user_id":
            self.user = value
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        self.sb.calls.append((self.name, self.cols))
        data = [r for r in self.sb.data.get(self.name, []) if self.user is None or r.get("user_id", self.user) == self.user]
        want = None if self.cols.strip() == "*" else [c.strip() for c in self.cols.split(",")]
        return SimpleNamespace(data=[dict(r) if want is None else {k: r.get(k) for k in want} for r in data])

    def __getattr__(self, _name):  # any other builder step (limit, in_, ...) is a no-op here
        return lambda *a, **k: self


class _FakeSB:
    def __init__(self, data):
        self.data, self.calls = data, []

    def table(self, name):
        return _FakeQuery(self, name)


def sd_rows(tables, user="u1", stamp="2026-11-11T00:00:00"):
    return [dict(t, id=f"id-{t['table_name']}", user_id=user, created_at=stamp) for t in tables]


def rows_reads(sb):
    return sum(1 for name, cols in sb.calls if name == "structured_data" and "rows" in cols)


am._DATA_CACHE.clear()
sb = _FakeSB({"structured_data": sd_rows(TABLES), "documents": [dict(d, user_id="u1") for d in DOCS]})
c1 = am.AgentContext.from_database("u1", sb)
check("the first question reads the rows", rows_reads(sb) == 1 and len(c1.tables) == 2, sb.calls)
first = c1.run_sql("SELECT count(*) AS n FROM acme_pumps")
c2 = am.AgentContext.from_database("u1", sb)
check("a second question on unchanged data does not read the rows again", rows_reads(sb) == 1, sb.calls)
check("and reuses the database already built", c2._connection() is c1._connection())
check("and gets the same answers", c2.run_sql("SELECT count(*) AS n FROM acme_pumps") == first)

sb.data["structured_data"] = sd_rows(TABLES + [ODD])
c3 = am.AgentContext.from_database("u1", sb)
check("new data is read again", rows_reads(sb) == 2 and len(c3.tables) == 3, sb.calls)
check("and gets a fresh database that holds it",
      c3._connection() is not c1._connection() and "6" in c3.run_sql("SELECT count(*) AS n FROM acme_odd"))

sb.data["structured_data"] = sd_rows([dict(TABLES[0], row_count=5, rows=TABLES[0]["rows"][:5]), TABLES[1], ODD])
c4 = am.AgentContext.from_database("u1", sb)
check("a changed row count is new data too", rows_reads(sb) == 3
      and "5" in c4.run_sql("SELECT count(*) AS n FROM acme_pumps").splitlines()[1], c4.run_sql("SELECT count(*) AS n FROM acme_pumps"))

other = _FakeSB({"structured_data": sd_rows([TABLES[1]], user="u2"), "documents": []})
c5 = am.AgentContext.from_database("u2", other)
check("another user never sees this user's database",
      [t["table_name"] for t in c5.tables] == ["acme_rooms"] and c5._connection() is not c4._connection()
      and c5.run_sql("SELECT count(*) FROM acme_pumps").startswith("SQL ERROR"), c5.tables)

saved_age = am.DATA_CACHE_MAX_AGE_S
am.DATA_CACHE_MAX_AGE_S = 0
am.AgentContext.from_database("u1", sb)
check("a kept database older than the limit is read again", rows_reads(sb) == 4, sb.calls)
am.DATA_CACHE_MAX_AGE_S = saved_age

am._DATA_CACHE.clear()
sb = _FakeSB({"structured_data": sd_rows(TABLES), "documents": []})
shared = [am.AgentContext.from_database("u1", sb) for _ in range(2)]
outs = []
threads = [threading.Thread(target=lambda c=c, k=k: outs.append((k, c.run_sql(f"SELECT count(*) AS n FROM acme_pumps WHERE kw > {k}"))))
           for c in shared for k in (0, 2, 4, 6)]
for th in threads:
    th.start()
for th in threads:
    th.join()
got = sorted((k, o.splitlines()[1].strip()) for k, o in outs)
check("two questions querying one kept database at once both answer right",
      got == sorted([(k, n) for k, n in zip((0, 2, 4, 6), want_counts)] * 2) and rows_reads(sb) == 1, got)
check("a kept database still cannot read a local file",
      shared[1].run_sql("SELECT * FROM read_csv('C:/Windows/win.ini')").startswith("SQL ERROR"))

print("13b. the database starts loading as soon as the question arrives")
am._DATA_CACHE.clear()
probe = ctx()
real_from_db = am.AgentContext.from_database
am.AgentContext.from_database = classmethod(lambda cls, user_id, sb: probe)
try:
    class LooksFirst(FakeAdapter):
        def step(self, allow_tools):
            deadline = time.monotonic() + 3
            while probe._con is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.built_before_any_tool = probe._con is not None
            return super().step(allow_tools)

    lf = LooksFirst([am.Turn(text="Two rooms.", tool_calls=[])])
    list(am.stream_agent([{"role": "user", "content": "q"}], "u1", None, adapter=lf))
    check("the database is loaded without waiting for the model's first query", lf.built_before_any_tool)
finally:
    am.AgentContext.from_database = real_from_db

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
