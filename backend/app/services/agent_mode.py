"""Agent mode — ON by default since v1.5; AGENT_MODE=0 (or false / off / no) returns the classic
pipeline (table router, SQL verifying loop, answer rules) exactly as it was.

One model answers in one conversation. It is shown an INDEX (one line per table, one line per
document folder) and four read-only tools, and decides for itself where to look:

    describe_table(name)          columns, sample values, the table card's facts and joins
    run_sql(query)                one read-only DuckDB SELECT over every table
    search_docs(query)            the app's own hybrid search (+ reranker), top 8 excerpts
    read_document(name, offset)   a document's text, in parts

There is no table router, no SQL verifying loop and none of the answer-rule blocks: whatever the
model needs to know about the data is meant to live in the data (table cards, manifests). The
model is named by AGENT_MODEL — "anthropic:<model id>" (ANTHROPIC_API_KEY; the default is
DEFAULT_AGENT_MODEL), "gemini:<model id>" (the app's Google key) or "openrouter:<model id>"
(OPENROUTER_API_KEY; every request carries a provider rule that refuses low-precision and
data-retaining hosts). Bounded by AGENT_MAX_STEPS tool calls (default 16) and AGENT_TIME_BUDGET_S
seconds (default 180); then the model must answer with what it has. If the model cannot be built
from the configuration (no key, no SDK), the chat path falls back to the classic pipeline and logs
a warning - see openai_client.stream_response.
"""
import difflib
import inspect
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import duckdb

logger = logging.getLogger(__name__)

DEFAULT_MAX_STEPS = 16
DEFAULT_TIME_BUDGET_S = 180.0
DEFAULT_AGENT_MODEL = "anthropic:claude-sonnet-5-5"
AGENT_MODE_OFF = ("0", "false", "off", "no")
SQL_ROW_CAP = 200
CELL_CAP = 300
SQL_OUT_CAP = 20_000
DESCRIBE_CAP = 8_000
DESCRIBE_MAX_TABLES = 3
DOC_TOP_K = 8
EXCERPT_CAP = 1_500
READ_LINES = 400
HOLDS_CAP = 160

OPENROUTER_URL = "https://openrouter.ai/api/v1"
# Quality and safety are enforced per request rather than by trusting one host's name: no fp4/int4
# hosts, no host that stores or trains on prompts, zero data retention, cheapest host that passes.
OPENROUTER_PROVIDER = {"quantizations": ["fp8", "bf16", "fp16"], "data_collection": "deny",
                       "zdr": True, "sort": "price", "allow_fallbacks": True}

# Claude, $ per million tokens: (input, output, cache read, 5-minute cache write). The adapter prices
# every call itself so a measurement run's spend cap never has to guess. Only models listed here run.
ANTHROPIC_PRICES = {"claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
                    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00)}
ANTHROPIC_EFFORTS = ("low", "medium", "high", "xhigh", "max")
ANTHROPIC_MAX_TOKENS = 16_000


def agent_mode_enabled() -> bool:
    return os.environ.get("AGENT_MODE", "").strip().lower() not in AGENT_MODE_OFF


# ---------------------------------------------------------------------------
# The index and the prompt
# ---------------------------------------------------------------------------
def _one_line(text, cap: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= cap else text[:cap - 1].rstrip() + "…"


def build_index(cards, tables, documents) -> str:
    """One line per table (name · rows · what it holds · identifier) and one per document folder."""
    by_table = {c.get("table"): c for c in cards or []}
    lines = ["TABLES (name · rows · what it holds · identifier column):"]
    for t in sorted(tables, key=lambda t: t["table_name"]):
        name = t["table_name"]
        card = by_table.get(name, {})
        rows = t.get("row_count")
        if rows is None:
            rows = len(t.get("rows") or [])
        line = f"- {name} · {rows} rows"
        holds = _one_line(card.get("holds") or card.get("one_row_is") or "", HOLDS_CAP)
        if holds:
            line += f" · {holds}"
        if card.get("identifier_column"):
            line += f" · id: {card['identifier_column']}"
        lines.append(line)
    folders = {}
    for d in documents or []:
        folders.setdefault(d.get("folder_path") or "/", []).append(d["file_name"])
    if folders:
        lines.append("")
        lines.append("DOCUMENTS (folder: files):")
        for folder in sorted(folders):
            lines.append(f"{folder}: {', '.join(sorted(folders[folder]))}")
    return "\n".join(lines)


_PROMPT = """You answer questions about one building from its records: data tables and documents.
{today}

The INDEX below lists every table (rows, what it holds, its identifier column) and every document by folder.
Tools: describe_table (columns, sample values, notes, joins), run_sql (one read-only DuckDB SELECT over any tables), search_docs (the best-matching document excerpts), read_document (a document's text by name, in parts).
You may make at most {max_steps} tool calls; then you must answer.

Rules:
1. Start from the index to choose where to look; describe a table before you query it.
2. Use run_sql for every count, total and list - never count from excerpts. A RESULT SHAPE line states how many rows exist and how many you were shown; never describe rows you were not shown.
3. Name the table or document each figure comes from, in plain words. When records disagree, give each figure with its source.
4. Say something is not recorded only after checking both the tables and the documents.

Answer in plain markdown, never HTML. Copy identifiers exactly as printed.

INDEX
{index}"""


def system_prompt(index: str, max_steps: int) -> str:
    from datetime import date
    return _PROMPT.format(today=f"Today's date is {date.today().isoformat()}.",
                          max_steps=max_steps, index=index)


TOOLS = [
    {"name": "describe_table",
     "description": "Columns with types and sample values, what the table holds, its notes and joins. "
                    f"Up to {DESCRIBE_MAX_TABLES} table names separated by commas.",
     "parameters": {"type": "object", "properties": {
         "name": {"type": "string", "description": "table name(s) from the index"}}, "required": ["name"]}},
    {"name": "run_sql",
     "description": f"Run ONE read-only DuckDB SELECT (or WITH ... SELECT) over any tables. Returns at most "
                    f"{SQL_ROW_CAP} rows plus a RESULT SHAPE line. Quote names that contain spaces.",
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string", "description": "the SQL"}}, "required": ["query"]}},
    {"name": "search_docs",
     "description": f"Search every document; returns the {DOC_TOP_K} best-matching excerpts, each with its document.",
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string", "description": "what to look for, in words"}}, "required": ["query"]}},
    {"name": "read_document",
     "description": f"Read a document from the index by file name (or folder/file name), {READ_LINES} lines "
                    "at a time from line `offset`.",
     "parameters": {"type": "object", "properties": {
         "name": {"type": "string", "description": "file name from the index"},
         "offset": {"type": "integer", "description": "1-based line to start at (default 1)"}},
         "required": ["name"]}},
]


# ---------------------------------------------------------------------------
# The tools
# ---------------------------------------------------------------------------
_LITERALS = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\n]*|/\*.*?\*/", re.S)
_FORBIDDEN = re.compile(r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|COPY|ATTACH|DETACH|INSTALL|LOAD|"
                        r"PRAGMA|SET|RESET|CALL|EXPORT|IMPORT|TRUNCATE|VACUUM|CHECKPOINT)\b", re.I)
_STARTS = re.compile(r"^\s*(\(\s*)*(SELECT|WITH|FROM|VALUES)\b", re.I)


def _cell(value) -> str:
    if value is None:
        return ""
    text = str(value).replace("\n", " ")
    return text if len(text) <= CELL_CAP else text[:CELL_CAP - 1] + "…"


class AgentContext:
    """The data one question can see — this user's tables, cards and documents — and the tools."""

    def __init__(self, user_id, supabase_client, tables, cards, documents,
                 retrieve: Optional[Callable] = None, reader: Optional[Callable] = None,
                 sql_timeout: Optional[float] = None):
        self.user_id, self.sb = user_id, supabase_client
        self.tables, self.cards, self.documents = tables or [], cards or [], documents or []
        self._retrieve, self._reader = retrieve, reader
        self.sql_timeout = sql_timeout
        self._con = None

    @classmethod
    def from_database(cls, user_id, supabase_client):
        from app.services.sql_tool import _load_table_cards
        tables = (supabase_client.table("structured_data")
                  .select("table_name, columns, rows, row_count").eq("user_id", user_id)
                  .order("table_name").execute().data) or []
        documents = (supabase_client.table("documents").select("id, file_name, folder_path")
                     .eq("user_id", user_id).execute().data) or []
        return cls(user_id, supabase_client, tables, _load_table_cards(user_id, supabase_client), documents)

    # -- dispatch -----------------------------------------------------------
    def call_tool(self, name: str, args: dict) -> str:
        method = {t["name"]: getattr(self, t["name"]) for t in TOOLS}.get(name)
        if method is None:
            return f"ERROR - unknown tool {name!r}. Tools: {', '.join(t['name'] for t in TOOLS)}."
        accepted = inspect.signature(method).parameters
        try:
            return method(**{k: v for k, v in (args or {}).items() if k in accepted})
        except Exception as e:  # noqa: BLE001 - a tool failure is the model's to read, never a crash
            return f"ERROR - {name} failed: {type(e).__name__}: {e}"

    # -- SQL ----------------------------------------------------------------
    def _connection(self):
        if self._con is None:
            from app.services.sql_tool import _infer_column_types, _loaded_value
            con = duckdb.connect(":memory:")
            for t in self.tables:
                cols, rows = t["columns"], t.get("rows") or []
                if not rows:
                    continue
                types_ = _infer_column_types(cols, rows)
                con.execute(f'CREATE TABLE "{t["table_name"]}" ('
                            + ", ".join(f'"{c}" {types_[c]}' for c in cols) + ")")
                con.executemany(f'INSERT INTO "{t["table_name"]}" VALUES ({", ".join("?" * len(cols))})',
                                [[_loaded_value(r.get(c), types_[c]) for c in cols] for r in rows])
            # No file, network or extension access from model-written SQL, and it cannot be undone.
            con.execute("SET enable_external_access = false")
            con.execute("SET lock_configuration = true")
            self._con = con
        return self._con

    def run_sql(self, query: str) -> str:
        from app.services.sql_tool import SQL_QUERY_TIMEOUT, _QueryTimeoutError, _execute_with_timeout, _result_shape
        sql = str(query).strip().rstrip(";").strip()
        bare = _LITERALS.sub(" ", sql)
        refused = ("REFUSED - run_sql runs ONE read-only SELECT (or WITH ... SELECT) statement; "
                   "nothing that writes, loads, attaches or changes settings.")
        if ";" in bare or not _STARTS.match(bare) or _FORBIDDEN.search(bare):
            return refused
        con = self._connection()
        try:
            result = _execute_with_timeout(con, sql, timeout=self.sql_timeout or SQL_QUERY_TIMEOUT)
            col_names = [d[0] for d in (con.description or [])]
        except _QueryTimeoutError as e:
            return f"SQL ERROR - {e} (timeout). Narrow the query."
        except Exception as e:  # noqa: BLE001 - the error text is what the model repairs from
            return f"SQL ERROR - {type(e).__name__}: {e}"
        n, shown = len(result), min(len(result), SQL_ROW_CAP)
        lines = [" | ".join(col_names)]
        lines += [" | ".join(_cell(v) for v in row) for row in result[:shown]]
        lines.append(f"(shown {shown} of {n} rows)" if n > shown else f"({n} rows)")
        shape = _result_shape(col_names, result, shown)
        if shape:
            lines.append(shape)
        out = "\n".join(lines)
        return out if len(out) <= SQL_OUT_CAP else out[:SQL_OUT_CAP] + "\n…(output cut; narrow the query)"

    # -- tables -------------------------------------------------------------
    def _find_table(self, name: str):
        want = name.strip().strip('"').lower()
        return next((t for t in self.tables if t["table_name"].lower() == want), None)

    def describe_table(self, name: str) -> str:
        names = [n for n in str(name).split(",") if n.strip()][:DESCRIBE_MAX_TABLES]
        return "\n\n".join(self._describe_one(n.strip()) for n in names) or "ERROR - no table name given."

    def _describe_one(self, name: str) -> str:
        from app.services.sql_tool import _infer_column_types, _table_schema_body
        t = self._find_table(name)
        if t is None:
            all_names = [x["table_name"] for x in self.tables]
            close = [n for n in all_names if name.lower() in n.lower()]
            close += [n for n in difflib.get_close_matches(name, all_names, n=5, cutoff=0.5) if n not in close]
            return f"UNKNOWN TABLE {name!r}. Close names: {', '.join(close[:8]) or 'none - see the index'}."
        card = next((c for c in self.cards if c.get("table") == t["table_name"]), {})
        rows = t.get("rows") or []
        out = [f"TABLE {t['table_name']} - {t.get('row_count', len(rows))} rows"]
        for label, key in (("Holds", "holds"), ("One row is", "one_row_is"),
                           ("Identifier column", "identifier_column")):
            if card.get(key):
                out.append(f"{label}: {card[key]}")
        for caveat in card.get("caveats") or []:
            out.append(f"Note: {caveat}")
        for join in card.get("declared_joins") or []:
            out.append(f"Join: {json.dumps(join, ensure_ascii=False)}")
        out.append(_table_schema_body(t["columns"], rows, _infer_column_types(t["columns"], rows)).rstrip())
        text = "\n".join(out)
        return text if len(text) <= DESCRIBE_CAP else text[:DESCRIBE_CAP] + "\n…(cut)"

    # -- documents ----------------------------------------------------------
    def search_docs(self, query: str) -> str:
        retrieve = self._retrieve
        if retrieve is None:
            from app.services.openai_client import retrieve_chunks as retrieve
        hits = retrieve(query=str(query), user_id=self.user_id, supabase_client=self.sb, top_k=DOC_TOP_K)
        if not hits:
            return "No excerpts found for that query."
        return "\n\n".join(f"[{i}] {h.get('file_name')}\n{_one_line(h.get('content'), EXCERPT_CAP)}"
                           for i, h in enumerate(hits, 1))

    def _resolve_document(self, name: str):
        q = str(name).strip()
        folder, _, fname = q.rpartition("/")
        exact = [d for d in self.documents if d["file_name"].lower() == fname.lower()
                 and (not folder or (d.get("folder_path") or "/").lower() == folder.lower())]
        if len(exact) == 1:
            return exact[0], []
        partial = exact or [d for d in self.documents if fname.lower() in d["file_name"].lower()]
        if len(partial) == 1:
            return partial[0], []
        if not partial:
            names = [d["file_name"] for d in self.documents]
            partial = [d for d in self.documents
                       if d["file_name"] in difflib.get_close_matches(fname, names, n=5, cutoff=0.4)]
        return None, partial

    def read_document(self, name: str, offset: int = 1) -> str:
        doc, candidates = self._resolve_document(name)
        if doc is None:
            listed = ", ".join(f"{d.get('folder_path') or '/'}/{d['file_name']}" for d in candidates[:10])
            return f"UNKNOWN DOCUMENT {name!r}. Candidates: {listed or 'none - see the index'}."
        from app.services.exploration_tools.schemas import ReadDocumentArgs
        reader = self._reader
        if reader is None:
            from app.services.exploration_tools.read_document import read_document as reader
        offset = max(1, int(offset or 1))
        res = reader(ReadDocumentArgs(document_id=doc["id"], offset=offset, limit=READ_LINES),
                     self.user_id, self.sb)
        if res.get("error"):
            return f"READ ERROR - {res.get('message')}"
        if res.get("status") == "pending_reindex":
            return f"{doc['file_name']} has no readable text yet."
        start, end, total = res.get("start_line"), res.get("end_line"), res.get("total_lines")
        text = f"{res.get('file_name')} - lines {start}-{end} of {total}\n{res.get('content') or ''}"
        if end is not None and total is not None and end < total:
            text += f"\n(More: call read_document again with offset={end + 1}.)"
        return text


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------
@dataclass
class ToolCall:
    id: Optional[str]
    name: str
    args: dict


@dataclass
class Turn:
    text: str
    tool_calls: list = field(default_factory=list)
    usage: Optional[dict] = None


_USAGE = []
_LAST_RUN = {}


def reset_usage():
    _USAGE.clear()


def usage_log() -> list:
    """Every model call's usage since the last reset - read by a measurement run's spend cap."""
    return list(_USAGE)


def last_run() -> dict:
    return dict(_LAST_RUN)


_LAST_CALL_NOTE = "\n\n[That was your last tool call. Answer now from what you have.]"
_NOT_RUN = "NOT RUN - the tool budget is used up. Answer now from what you have."
_NO_ANSWER = "I could not finish an answer within the tool budget."


def run_agent(messages, ctx: AgentContext, adapter, max_steps: int = DEFAULT_MAX_STEPS,
              time_budget: float = DEFAULT_TIME_BUDGET_S, clock=time.monotonic, on_event=None) -> dict:
    adapter.start(system_prompt(build_index(ctx.cards, ctx.tables, ctx.documents), max_steps), messages)
    steps, stopped, t0 = [], None, clock()
    while True:
        allow = len(steps) < max_steps and clock() - t0 < time_budget
        if not allow and stopped is None:
            stopped = "step_cap" if len(steps) >= max_steps else "time_budget"
        turn = adapter.step(allow_tools=allow)
        if turn.usage:
            _USAGE.append(turn.usage)
        if not allow or not turn.tool_calls:
            answer = turn.text if (turn.text or "").strip() else _NO_ANSWER
            break
        results = []
        for tc in turn.tool_calls:
            if len(steps) >= max_steps:
                results.append((tc.id, tc.name, _NOT_RUN))
                continue
            if on_event:
                on_event((tc.name, tc.args))
            text = ctx.call_tool(tc.name, tc.args)
            steps.append({"tool": tc.name, "args": tc.args, "chars": len(text)})
            if len(steps) == max_steps:
                text += _LAST_CALL_NOTE
            results.append((tc.id, tc.name, text))
        adapter.add_tool_results(results)
    return {"answer": answer, "steps": steps, "stopped": stopped, "seconds": round(clock() - t0, 1)}


def stream_agent(messages, user_id, supabase_client, adapter=None):
    """stream_response's agent-mode path: the same event names the chat route and the eval read."""
    ctx = AgentContext.from_database(user_id, supabase_client)
    events = []
    result = run_agent(messages, ctx, adapter or adapter_from_env(),
                       max_steps=int(os.environ.get("AGENT_MAX_STEPS", DEFAULT_MAX_STEPS)),
                       time_budget=float(os.environ.get("AGENT_TIME_BUDGET_S", DEFAULT_TIME_BUDGET_S)),
                       on_event=events.append)
    _LAST_RUN.clear()
    _LAST_RUN.update({k: result[k] for k in ("steps", "stopped", "seconds")})
    logger.info("agent mode: %d tool calls, stopped=%s, %.1fs", len(result["steps"]), result["stopped"],
                result["seconds"])
    for name, args in events:
        yield ("tool_start", json.dumps({"tool": name, "args": args}, ensure_ascii=False))
    yield ("token", result["answer"])
    yield ("done", "")


# ---------------------------------------------------------------------------
# Model adapters
# ---------------------------------------------------------------------------
def _http_post(url, headers, body):
    import httpx
    r = httpx.post(url, headers=headers, json=body, timeout=180)
    try:
        return r.json()
    except ValueError:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}") from None


class OpenRouterAdapter:
    """OpenAI-compatible chat completions on OpenRouter, with the provider rule on every request."""

    def __init__(self, model, api_key, post=None, base_url=OPENROUTER_URL, provider=None):
        self.model, self.api_key, self.base_url = model, api_key, base_url
        self.post = post or _http_post
        self.provider = provider or dict(OPENROUTER_PROVIDER)
        self.messages = []

    def start(self, system, messages):
        self.messages = [{"role": "system", "content": system}] + [
            {"role": "user" if m["role"] == "user" else "assistant", "content": m["content"]} for m in messages]

    def step(self, allow_tools):
        body = {"model": self.model, "messages": self.messages,
                "tools": [{"type": "function", "function": t} for t in TOOLS],
                "tool_choice": "auto" if allow_tools else "none",
                "provider": self.provider, "usage": {"include": True}}
        resp = self.post(f"{self.base_url}/chat/completions",
                         {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, body)
        if resp.get("error") or not resp.get("choices"):
            err = resp.get("error") or {}
            raise RuntimeError(f"OpenRouter error: {err.get('message') if isinstance(err, dict) else err}")
        msg = resp["choices"][0].get("message") or {}
        replay = {"role": "assistant", "content": msg.get("content") or ""}
        for key in ("tool_calls", "reasoning_details"):
            if msg.get(key):
                replay[key] = msg[key]
        self.messages.append(replay)
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            calls.append(ToolCall(tc.get("id"), fn.get("name"), args if isinstance(args, dict) else {}))
        u = resp.get("usage") or {}
        usage = {"input": u.get("prompt_tokens", 0), "output": u.get("completion_tokens", 0),
                 "cached": (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
                 "cost": u.get("cost"), "provider": resp.get("provider")}
        return Turn(text=msg.get("content") or "", tool_calls=calls, usage=usage)

    def add_tool_results(self, results):
        for call_id, _name, text in results:
            self.messages.append({"role": "tool", "tool_call_id": call_id, "content": text})


class GeminiAdapter:
    """google-genai with automatic function calling OFF, so the loop above owns every step."""

    def __init__(self, model, client, service_tier=None):
        self.model, self.client, self.service_tier = model, client, service_tier
        self.system, self.contents = "", []

    def start(self, system, messages):
        from google.genai import types
        self.system = system
        self.contents = [types.Content(role="user" if m["role"] == "user" else "model",
                                       parts=[types.Part(text=m["content"])]) for m in messages]

    def step(self, allow_tools):
        from google.genai import types
        from app.services.llm_config import apply_llm_seed
        config = apply_llm_seed(types.GenerateContentConfig(
            system_instruction=self.system,
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t["name"], description=t["description"],
                                          parameters_json_schema=t["parameters"]) for t in TOOLS])],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
                mode="AUTO" if allow_tools else "NONE")),
            **({"service_tier": self.service_tier} if self.service_tier else {})))
        resp = self.client.models.generate_content(model=self.model, contents=self.contents, config=config)
        if resp.candidates and resp.candidates[0].content:
            self.contents.append(resp.candidates[0].content)  # keeps thought signatures intact
        return self.parse(resp)

    @staticmethod
    def parse(resp) -> Turn:
        um = getattr(resp, "usage_metadata", None)
        usage = {"input": getattr(um, "prompt_token_count", 0) or 0,
                 "output": (getattr(um, "candidates_token_count", 0) or 0) + (getattr(um, "thoughts_token_count", 0) or 0),
                 "cached": getattr(um, "cached_content_token_count", 0) or 0}
        cands = getattr(resp, "candidates", None) or []
        parts = (getattr(cands[0].content, "parts", None) or []) if cands and cands[0].content else []
        text = "".join(p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", None))
        calls = [ToolCall(getattr(p.function_call, "id", None), p.function_call.name, dict(p.function_call.args or {}))
                 for p in parts if getattr(p, "function_call", None)]
        return Turn(text=text, tool_calls=calls, usage=usage)

    def add_tool_results(self, results):
        from google.genai import types
        self.contents.append(types.Content(role="user", parts=[
            types.Part(function_response=types.FunctionResponse(id=call_id, name=name, response={"result": text}))
            for call_id, name, text in results]))


class AnthropicAdapter:
    """Claude's Messages API. The system prompt and the growing conversation are cached; each assistant
    turn is replayed exactly as returned, so thinking blocks stay valid; tool results go back together
    in one user turn."""

    def __init__(self, model, client, max_tokens=ANTHROPIC_MAX_TOKENS, effort=None):
        if model not in ANTHROPIC_PRICES:
            raise ValueError(f"no known price for Claude model {model!r}: add it to ANTHROPIC_PRICES first")
        if effort is not None and effort not in ANTHROPIC_EFFORTS:
            raise ValueError(f"unknown effort {effort!r}: use one of {', '.join(ANTHROPIC_EFFORTS)}")
        self.model, self.client, self.max_tokens, self.effort = model, client, max_tokens, effort
        self.prices = ANTHROPIC_PRICES[model]
        self.system, self.messages = [], []

    def start(self, system, messages):
        self.system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        self.messages = [{"role": "user" if m["role"] == "user" else "assistant", "content": m["content"]}
                         for m in messages]

    def step(self, allow_tools):
        extra = {"output_config": {"effort": self.effort}} if self.effort else {}
        resp = self.client.messages.create(
            model=self.model, max_tokens=self.max_tokens, system=self.system, messages=self.messages,
            tools=[{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                   for t in TOOLS],
            tool_choice={"type": "auto" if allow_tools else "none"},
            cache_control={"type": "ephemeral"}, **extra)
        content = list(getattr(resp, "content", None) or [])
        self.messages.append({"role": "assistant", "content": content})
        text = "".join(getattr(b, "text", "") or "" for b in content if getattr(b, "type", None) == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input or {}))
                 for b in content if getattr(b, "type", None) == "tool_use"]
        u = resp.usage
        fresh = getattr(u, "input_tokens", 0) or 0
        read = getattr(u, "cache_read_input_tokens", 0) or 0
        write = getattr(u, "cache_creation_input_tokens", 0) or 0
        out = getattr(u, "output_tokens", 0) or 0
        p_in, p_out, p_read, p_write = self.prices
        cost = (fresh * p_in + out * p_out + read * p_read + write * p_write) / 1e6
        usage = {"input": fresh + read + write, "output": out, "cached": read, "cost": cost, "provider": "anthropic"}
        if getattr(resp, "stop_reason", None) == "refusal":
            logger.warning("agent mode: Claude stopped with a refusal")
        return Turn(text=text, tool_calls=calls, usage=usage)

    def add_tool_results(self, results):
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call_id, "content": text} for call_id, _name, text in results]})


def adapter_from_env(gemini_client=None, anthropic_client=None):
    spec = os.environ.get("AGENT_MODEL", "").strip() or DEFAULT_AGENT_MODEL
    provider, _, model = spec.partition(":")
    if provider == "openrouter" and model:
        key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not key:
            raise ValueError("AGENT_MODEL names openrouter but OPENROUTER_API_KEY is not set")
        override = os.environ.get("AGENT_OPENROUTER_PROVIDER", "").strip()
        return OpenRouterAdapter(model, key, provider=json.loads(override) if override else None)
    if provider == "gemini" and model:
        if gemini_client is None:
            from google import genai
            from app.services.settings import get_llm_api_key
            gemini_client = genai.Client(api_key=get_llm_api_key())
        return GeminiAdapter(model, gemini_client, service_tier=os.environ.get("AGENT_GEMINI_TIER") or None)
    if provider == "anthropic" and model:
        if anthropic_client is None:
            from app.services.llm_provider import _get_anthropic_client
            anthropic_client = _get_anthropic_client()
        return AnthropicAdapter(model, anthropic_client,
                                effort=os.environ.get("AGENT_ANTHROPIC_EFFORT", "").strip() or None)
    raise ValueError(f"AGENT_MODEL must be 'gemini:<id>', 'openrouter:<id>' or 'anthropic:<id>', got {spec!r}")
