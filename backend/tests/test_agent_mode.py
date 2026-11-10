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

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
