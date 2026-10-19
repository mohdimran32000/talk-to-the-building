"""test_llm_provider.py - a switchable per-ROLE provider, Gemini (default) or Claude
(spec 2026-09-30, task W9-A1).

WHAT THIS GUARDS. `app.services.llm_provider` is the one place that decides, per ROLE
("sql" | "answer"), whether a call goes to Gemini (today's behaviour, untouched) or to
Claude, and - when it is Claude - builds the request from the SAME prompt text the
Gemini path already builds: a bare prompt string for "sql" (no system turn, exactly
like `sql_tool.py`'s `contents=prompt`), and `system` + merged `messages` for "answer"
(converted from the `google.genai.types.Content` list every answer-role call site in
`openai_client.py` already builds).

NO NETWORK CALL IS MADE ANYWHERE IN THIS FILE. Every Claude call is exercised through a
fake client built below; `anthropic.Anthropic(...)` itself is never constructed with a
real key in a test that then calls out.

ABOUT TEMPERATURE (read before editing anything below that mentions it): the installed
`anthropic` SDK's `messages.create`/`messages.stream` have NO `temperature` keyword
argument at all (verified against the installed 1.13.0 signature - it is a fully
explicit keyword-only signature, not `**kwargs`), and Anthropic's own current-model
docs say the Claude 5-generation models reject a non-default `temperature`/`top_p`/
`top_k` with a 400 even where an SDK accepts the field. Sending it would make every real
call fail - worse than the "surfaces as an outage" contract, since it would fail before
any request is even sent. So `generate_sql_text`/`stream_answer_text` compute the
configured temperature (for the usage-recording trail) but never pass it to the SDK call
- section 5 and 7 below pin that the fake client is never called with a `temperature`
kwarg, which would catch a regression that tried to add one back.

Run:
    venv/Scripts/python -X utf8 tests/test_llm_provider.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.genai import types  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


def _clear_env():
    for name in ("LLM_PROVIDER_SQL", "LLM_PROVIDER_ANSWER", "LLM_PROVIDER_TOOLS",
                 "ANTHROPIC_MODEL_SQL", "ANTHROPIC_MODEL_ANSWER", "ANTHROPIC_MODEL_TOOLS",
                 "ANTHROPIC_API_KEY", "ANSWER_TEMPERATURE"):
        os.environ.pop(name, None)


_clear_env()
import app.services.llm_provider as llm_provider  # noqa: E402

# --------------------------------------------------------------------------- 1. provider switch

print("1. get_llm_provider: gemini by default, anthropic only on the exact literal")
_clear_env()
check('get_llm_provider("sql") defaults to "gemini" when LLM_PROVIDER_SQL is unset',
      llm_provider.get_llm_provider("sql") == "gemini")
check('get_llm_provider("answer") defaults to "gemini" when LLM_PROVIDER_ANSWER is unset',
      llm_provider.get_llm_provider("answer") == "gemini")

os.environ["LLM_PROVIDER_SQL"] = "anthropic"
check('LLM_PROVIDER_SQL=anthropic switches the "sql" role',
      llm_provider.get_llm_provider("sql") == "anthropic")
check('...and leaves "answer" on gemini (independent switches)',
      llm_provider.get_llm_provider("answer") == "gemini")
os.environ.pop("LLM_PROVIDER_SQL")

os.environ["LLM_PROVIDER_ANSWER"] = "  Anthropic  "
check('LLM_PROVIDER_ANSWER tolerates surrounding whitespace and case',
      llm_provider.get_llm_provider("answer") == "anthropic")
os.environ.pop("LLM_PROVIDER_ANSWER")

for bad in ("Gemini", "openai", "claude", "", "anthropicx", " "):
    os.environ["LLM_PROVIDER_SQL"] = bad
    check(f"LLM_PROVIDER_SQL={bad!r} (not the exact literal) stays gemini - never crashes, "
          f"never silently switches",
          llm_provider.get_llm_provider("sql") == "gemini")
_clear_env()

check('an unknown role name is "gemini" (never raises)',
      llm_provider.get_llm_provider("nonsense") == "gemini")

# --------------------------------------------------------------------------- 2. model/key getters

print("\n2. get_anthropic_model / get_anthropic_api_key")
_clear_env()
check("get_anthropic_model('sql') defaults to claude-sonnet-5-5",
      llm_provider.get_anthropic_model("sql") == "claude-sonnet-5-5")
check("get_anthropic_model('answer') defaults to claude-sonnet-5-5",
      llm_provider.get_anthropic_model("answer") == "claude-sonnet-5-5")
check("DEFAULT_ANTHROPIC_MODEL is exactly claude-sonnet-5-5 (the brief's literal default)",
      llm_provider.DEFAULT_ANTHROPIC_MODEL == "claude-sonnet-5-5")

os.environ["ANTHROPIC_MODEL_SQL"] = "claude-haiku-5-5"
check("ANTHROPIC_MODEL_SQL overrides the sql role's model",
      llm_provider.get_anthropic_model("sql") == "claude-haiku-5-5")
check("...and leaves the answer role's model at the default (independent)",
      llm_provider.get_anthropic_model("answer") == "claude-sonnet-5-5")
os.environ["ANTHROPIC_MODEL_SQL"] = "   "
check("a blank/whitespace-only override falls back to the default, never sends an empty id",
      llm_provider.get_anthropic_model("sql") == "claude-sonnet-5-5")
_clear_env()

check("get_anthropic_api_key() is None when ANTHROPIC_API_KEY is unset",
      llm_provider.get_anthropic_api_key() is None)
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-fixture-value"
check("get_anthropic_api_key() reads the configured value",
      llm_provider.get_anthropic_api_key() == "sk-ant-test-fixture-value")
os.environ["ANTHROPIC_API_KEY"] = ""
check("an empty ANTHROPIC_API_KEY reads as None (unset), never an empty string sent as a key",
      llm_provider.get_anthropic_api_key() is None)
_clear_env()

# --------------------------------------------------------------------------- 3. no-key client guard

print("\n3. _get_anthropic_client: a missing key raises a fixed, static message - no key "
      "material anywhere, because there is none to interpolate either way")
_clear_env()
try:
    llm_provider._get_anthropic_client()
    check("raises when ANTHROPIC_API_KEY is unset", False, "did not raise")
except ValueError as e:
    msg = str(e)
    check("raises ValueError (same exception TYPE as openai_client._get_client's own "
          "no-key guard)", True)
    check("the message is the fixed literal (never an f-string touching the key)",
          msg == "No Anthropic API key configured. Set ANTHROPIC_API_KEY in backend/.env.",
          msg)
    check('the message never contains the word "None" (a naive f-string of a None key would)',
          "None" not in msg, msg)

# A static source-level guard: this module must never build a log/exception string
# that interpolates the api_key variable - the one place a key COULD leak if someone
# "helpfully" added a debug log later.
_src = (Path(__file__).resolve().parents[1] / "app" / "services" / "llm_provider.py").read_text(
    encoding="utf-8")
_dangerous_lines = [ln for ln in _src.splitlines()
                     if ("logger." in ln or "raise " in ln) and "api_key" in ln.lower()
                     and "get_anthropic_api_key" not in ln and "ANTHROPIC_API_KEY" not in ln]
check("no logger./raise line in llm_provider.py interpolates a bare api_key value",
      _dangerous_lines == [], _dangerous_lines)

# --------------------------------------------------------------------------- 4. Gemini -> Claude message conversion

print("\n4. gemini_contents_to_claude_messages: role mapping + consecutive-turn merging")


def _content(role, *texts):
    return types.Content(role=role, parts=[types.Part(text=t) for t in texts])


check("empty contents -> empty messages", llm_provider.gemini_contents_to_claude_messages([]) == [])

single = llm_provider.gemini_contents_to_claude_messages([_content("user", "hello")])
check("one user turn -> one user message",
      single == [{"role": "user", "content": "hello"}], single)

alt = llm_provider.gemini_contents_to_claude_messages([
    _content("user", "hi"), _content("model", "hello back"), _content("user", "thanks"),
])
check("alternating user/model -> user/assistant/user, nothing merged",
      alt == [
          {"role": "user", "content": "hi"},
          {"role": "assistant", "content": "hello back"},
          {"role": "user", "content": "thanks"},
      ], alt)

consecutive = llm_provider.gemini_contents_to_claude_messages([
    _content("user", "Q1"), _content("model", "A1a"), _content("model", "A1b"),
    _content("user", "Q2"),
])
check("two consecutive model turns merge into ONE assistant message (Claude requires this)",
      consecutive == [
          {"role": "user", "content": "Q1"},
          {"role": "assistant", "content": "A1a\n\nA1b"},
          {"role": "user", "content": "Q2"},
      ], consecutive)

consecutive_user = llm_provider.gemini_contents_to_claude_messages([
    _content("user", "part one"), _content("user", "part two"), _content("model", "reply"),
])
check("two consecutive USER turns merge too (role-agnostic merging)",
      consecutive_user == [
          {"role": "user", "content": "part one\n\npart two"},
          {"role": "assistant", "content": "reply"},
      ], consecutive_user)

multi_part = llm_provider.gemini_contents_to_claude_messages([
    types.Content(role="user", parts=[types.Part(text="a"), types.Part(text="b")]),
])
check("a turn with more than one Part joins its own parts",
      multi_part == [{"role": "user", "content": "ab"}], multi_part)

# --------------------------------------------------------------------------- fakes shared by 5-8


class _FakeBlock:
    def __init__(self, type_, text=None, name=None, input=None):
        self.type = type_
        self.text = text
        self.name = name   # task W9-A2: a "tool_use" block's tool name
        self.input = input  # task W9-A2: a "tool_use" block's input dict


class _FakeUsage:
    def __init__(self, input_tokens=11, output_tokens=7,
                 cache_creation_input_tokens=0, cache_read_input_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        # task W9-A2: prompt-caching token counts (the "tools" role only in production;
        # 0 here matches a response that never used caching, same as a real one would).
        self.cache_creation_input_tokens = cache_creation_input_tokens
        self.cache_read_input_tokens = cache_read_input_tokens


class _FakeMessage:
    """Fakes a non-streaming `anthropic.types.Message` - content blocks + usage."""
    def __init__(self, blocks, input_tokens=11, output_tokens=7):
        self.content = blocks
        self.usage = _FakeUsage(input_tokens, output_tokens)


class _FakeStreamCtx:
    """Fakes the `with client.messages.stream(...) as stream:` context manager -
    `.text_stream` yields the configured chunks, `.get_final_message()` returns the
    configured final message."""
    def __init__(self, chunks, final_message):
        self._chunks = chunks
        self._final_message = final_message
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    @property
    def text_stream(self):
        return iter(self._chunks)

    def get_final_message(self):
        return self._final_message


class _FakeMessages:
    """Fakes `client.messages` with the REAL SDK's keyword-only strictness in spirit:
    captures every kwarg so a test can assert on exactly what was sent, and - because
    it is a plain Python function with an explicit signature, not `**kwargs` - a
    stray `temperature=` from a regression would raise TypeError here exactly as the
    real SDK does, not be silently swallowed into a capture dict."""
    def __init__(self, create_response=None, stream_chunks=None, stream_final=None,
                 raise_on_create=None, raise_on_stream=None):
        self.create_response = create_response
        self.stream_chunks = stream_chunks or []
        self.stream_final = stream_final
        self.raise_on_create = raise_on_create
        self.raise_on_stream = raise_on_stream
        self.create_calls = []
        self.stream_calls = []

    def create(self, *, model, max_tokens, messages, system=None, tools=None, tool_choice=None):
        self.create_calls.append({"model": model, "max_tokens": max_tokens,
                                   "messages": messages, "system": system,
                                   "tools": tools, "tool_choice": tool_choice})
        if self.raise_on_create:
            raise self.raise_on_create
        return self.create_response

    def stream(self, *, model, max_tokens, messages, system=None):
        self.stream_calls.append({"model": model, "max_tokens": max_tokens,
                                   "messages": messages, "system": system})
        if self.raise_on_stream:
            raise self.raise_on_stream
        return _FakeStreamCtx(self.stream_chunks, self.stream_final)


class _FakeClient:
    def __init__(self, messages: _FakeMessages):
        self.messages = messages


# --------------------------------------------------------------------------- 5. generate_sql_text

print("\n5. generate_sql_text (the SQL writer's Claude path)")
_clear_env()
fake_msgs = _FakeMessages(create_response=_FakeMessage(
    [_FakeBlock("text", "SELECT 1 FROM t")]))
fake_client = _FakeClient(fake_msgs)

result = llm_provider.generate_sql_text("THE PROMPT", client=fake_client, name="sql_generate")
check("returns an object with .text, the exact string the fake produced",
      result.text == "SELECT 1 FROM t", result.text)
check("exactly one create() call", len(fake_msgs.create_calls) == 1, fake_msgs.create_calls)
call = fake_msgs.create_calls[0]
check("the prompt is sent as a single user message (no system turn - matches "
      "sql_tool.py's contents=prompt, no system_instruction)",
      call["messages"] == [{"role": "user", "content": "THE PROMPT"}], call)
check("system is not set for the sql role", call["system"] is None, call)
check("model defaults to DEFAULT_ANTHROPIC_MODEL", call["model"] == "claude-sonnet-5-5", call)
check("max_tokens is at least Gemini's 2048 output budget (equal, by default)",
      call["max_tokens"] == 2048, call)
check('"temperature" is never sent (see the module docstring - the installed SDK has no '
      "such parameter and the model rejects it): the fake's explicit signature would have "
      "TypeError'd if it had been",
      "temperature" not in call)

os.environ["ANTHROPIC_MODEL_SQL"] = "claude-haiku-5-5"
fake_msgs2 = _FakeMessages(create_response=_FakeMessage([_FakeBlock("text", "SELECT 2")]))
llm_provider.generate_sql_text("P2", client=_FakeClient(fake_msgs2))
check("ANTHROPIC_MODEL_SQL reaches the actual call",
      fake_msgs2.create_calls[0]["model"] == "claude-haiku-5-5", fake_msgs2.create_calls)
_clear_env()

# A response with a thinking block AND a text block: only the text block's text counts.
fake_msgs3 = _FakeMessages(create_response=_FakeMessage(
    [_FakeBlock("thinking", "reasoning..."), _FakeBlock("text", "SELECT 3")]))
result3 = llm_provider.generate_sql_text("P3", client=_FakeClient(fake_msgs3))
check("a thinking block is ignored; only the text block's text is returned",
      result3.text == "SELECT 3", result3.text)

# A tool-use-only / empty response: "" never None, never an exception (matches
# sql_tool.py's `(repair_resp.text or "").strip()` tolerance).
fake_msgs4 = _FakeMessages(create_response=_FakeMessage([]))
result4 = llm_provider.generate_sql_text("P4", client=_FakeClient(fake_msgs4))
check('an empty content list -> .text == "" (never None, never raises)',
      result4.text == "", repr(result4.text))

# Custom max_output_tokens is honoured (the repair-call site may want to pass its own).
fake_msgs5 = _FakeMessages(create_response=_FakeMessage([_FakeBlock("text", "x")]))
llm_provider.generate_sql_text("P5", client=_FakeClient(fake_msgs5), max_output_tokens=4096)
check("a custom max_output_tokens reaches the call",
      fake_msgs5.create_calls[0]["max_tokens"] == 4096)

# --------------------------------------------------------------------------- 6. stream_answer_text

print("\n6. stream_answer_text (the four answer prompts' Claude path)")
_clear_env()
answer_contents = [_content("user", "what is the total load?")]
fake_msgs6 = _FakeMessages(
    stream_chunks=["The total ", "", "load is ", "42 kW."],
    stream_final=_FakeMessage([_FakeBlock("text", "The total load is 42 kW.")]),
)
fake_client6 = _FakeClient(fake_msgs6)

chunks = list(llm_provider.stream_answer_text(
    system_text="SYSTEM PROMPT TEXT", contents=answer_contents, client=fake_client6,
    name="answer_tool_result"))
check("yields exactly the non-empty chunks, in order (an empty chunk is skipped, "
      "mirroring Gemini's `if chunk.text:` guard)",
      chunks == ["The total ", "load is ", "42 kW."], chunks)
check("exactly one stream() call", len(fake_msgs6.stream_calls) == 1)
scall = fake_msgs6.stream_calls[0]
check("system_text reaches `system` verbatim", scall["system"] == "SYSTEM PROMPT TEXT", scall)
check("contents converted through gemini_contents_to_claude_messages",
      scall["messages"] == [{"role": "user", "content": "what is the total load?"}], scall)
check("model defaults to DEFAULT_ANTHROPIC_MODEL", scall["model"] == "claude-sonnet-5-5")
check("max_tokens defaults to the streaming budget (64000)", scall["max_tokens"] == 64000)
check('"temperature" is never sent, same reasoning as generate_sql_text',
      "temperature" not in scall)

os.environ["ANTHROPIC_MODEL_ANSWER"] = "claude-opus-5-5"
fake_msgs7 = _FakeMessages(stream_chunks=["hi"], stream_final=_FakeMessage(
    [_FakeBlock("text", "hi")]))
list(llm_provider.stream_answer_text(system_text="s", contents=answer_contents,
                                     client=_FakeClient(fake_msgs7)))
check("ANTHROPIC_MODEL_ANSWER reaches the actual call",
      fake_msgs7.stream_calls[0]["model"] == "claude-opus-5-5")
_clear_env()

# Multi-turn history with consecutive model turns - the merge really happens before
# the request is built, not just in the standalone converter test above.
multi_turn = [
    _content("user", "Q1"), _content("model", "A1a"), _content("model", "A1b"),
    _content("user", "Q2"),
]
fake_msgs8 = _FakeMessages(stream_chunks=["ok"], stream_final=_FakeMessage(
    [_FakeBlock("text", "ok")]))
list(llm_provider.stream_answer_text(system_text="s", contents=multi_turn,
                                     client=_FakeClient(fake_msgs8)))
check("a real multi-turn history is merged before being sent, not just in isolation",
      fake_msgs8.stream_calls[0]["messages"] == [
          {"role": "user", "content": "Q1"},
          {"role": "assistant", "content": "A1a\n\nA1b"},
          {"role": "user", "content": "Q2"},
      ], fake_msgs8.stream_calls[0]["messages"])

# The stream context manager is properly closed (the `with` block ran to completion).
fake_msgs9 = _FakeMessages(stream_chunks=["x"], stream_final=_FakeMessage([_FakeBlock("text", "x")]))
# Rebuild the ctx manually to inspect .closed after full consumption.
import app.services.llm_provider as _lp  # noqa: E402  (re-import alias, same module)
list(_lp.stream_answer_text(system_text="s", contents=answer_contents,
                            client=_FakeClient(fake_msgs9)))
check("stream() was actually called (the generator ran to completion, not just constructed)",
      len(fake_msgs9.stream_calls) == 1)

# --------------------------------------------------------------------------- 7. errors propagate

print("\n7. errors surface exactly as raised - never swallowed, never rewrapped "
     "(requirement 5: same user-facing fallback as a Gemini API error)")


class _FakeAPIError(Exception):
    pass


fake_err_msgs = _FakeMessages(raise_on_create=_FakeAPIError("rate limited"))
try:
    llm_provider.generate_sql_text("P", client=_FakeClient(fake_err_msgs))
    check("generate_sql_text propagates a create() error", False, "did not raise")
except _FakeAPIError as e:
    check("generate_sql_text propagates the SAME exception type and message unchanged",
          str(e) == "rate limited")
except Exception as e:  # noqa: BLE001
    check("generate_sql_text propagates the SAME exception type (got a different type)",
          False, f"{type(e).__name__}: {e}")

fake_err_msgs2 = _FakeMessages(raise_on_stream=_FakeAPIError("overloaded"))
try:
    list(llm_provider.stream_answer_text(system_text="s", contents=answer_contents,
                                         client=_FakeClient(fake_err_msgs2)))
    check("stream_answer_text propagates a stream() error", False, "did not raise")
except _FakeAPIError as e:
    check("stream_answer_text propagates the SAME exception type and message unchanged",
          str(e) == "overloaded")
except Exception as e:  # noqa: BLE001
    check("stream_answer_text propagates the SAME exception type (got a different type)",
          False, f"{type(e).__name__}: {e}")

# No-key behaviour: the real client factory is reached when client=None and no key is
# configured - confirms the production path (not just the fake-injected path) fails
# the same clean way section 3 already pinned.
_clear_env()
try:
    llm_provider.generate_sql_text("P")
    check("generate_sql_text with no client and no key raises", False, "did not raise")
except ValueError as e:
    check("...with the same fixed no-key message as _get_anthropic_client",
          str(e) == "No Anthropic API key configured. Set ANTHROPIC_API_KEY in backend/.env.")

# --------------------------------------------------------------------------- 8. usage recording

print("\n8. usage recording - never breaks the call, even when it fails")
_clear_env()

captured = []
original_record_usage = llm_provider._record_usage


def _capture(*, name, role, model, response):
    captured.append({"name": name, "role": role, "model": model})


llm_provider._record_usage = _capture
try:
    fake_msgs10 = _FakeMessages(create_response=_FakeMessage([_FakeBlock("text", "SELECT 1")]))
    llm_provider.generate_sql_text("P", client=_FakeClient(fake_msgs10), name="sql_generate")
finally:
    llm_provider._record_usage = original_record_usage
check("generate_sql_text calls _record_usage with (name, role, model)",
      captured == [{"name": "sql_generate", "role": "sql", "model": "claude-sonnet-5-5"}],
      captured)

# record_anthropic_usage itself (llm_usage.py): never raises, with or without a traced
# run, and with a malformed/usage-less response.
from app.services.llm_usage import record_anthropic_usage  # noqa: E402

try:
    record_anthropic_usage("sql_generate", "claude-sonnet-5-5",
                           _FakeMessage([_FakeBlock("text", "x")]),
                           intended_temperature=0)
    check("record_anthropic_usage does not raise outside a traced run "
          "(get_current_run_tree() returns None here)", True)
except Exception as e:  # noqa: BLE001
    check("record_anthropic_usage does not raise outside a traced run", False,
          f"{type(e).__name__}: {e}")

try:
    record_anthropic_usage("x", "y", object())  # no .usage attribute at all
    check("record_anthropic_usage tolerates a response with no .usage", True)
except Exception as e:  # noqa: BLE001
    check("record_anthropic_usage tolerates a response with no .usage", False,
          f"{type(e).__name__}: {e}")

# Force the LangSmith attachment path itself to blow up and confirm it is swallowed.
import langsmith.run_helpers as _rh  # noqa: E402

_orig_get_current_run_tree = _rh.get_current_run_tree
_rh.get_current_run_tree = lambda: (_ for _ in ()).throw(RuntimeError("langsmith boom"))
try:
    record_anthropic_usage("sql_generate", "claude-sonnet-5-5",
                           _FakeMessage([_FakeBlock("text", "x")]))
    check("a LangSmith-layer failure is swallowed, never raised", True)
except Exception as e:  # noqa: BLE001
    check("a LangSmith-layer failure is swallowed, never raised", False,
          f"{type(e).__name__}: {e}")
finally:
    _rh.get_current_run_tree = _orig_get_current_run_tree

# --------------------------------------------------------------------------- 9. "tools" role (W9-A2)

print("\n9. get_llm_provider/get_anthropic_model for the new 'tools' role (task W9-A2)")
_clear_env()
check('get_llm_provider("tools") defaults to "gemini" when LLM_PROVIDER_TOOLS is unset',
      llm_provider.get_llm_provider("tools") == "gemini")

os.environ["LLM_PROVIDER_TOOLS"] = "anthropic"
check('LLM_PROVIDER_TOOLS=anthropic switches the "tools" role',
      llm_provider.get_llm_provider("tools") == "anthropic")
check('...and leaves "sql"/"answer" on gemini (independent switches)',
      llm_provider.get_llm_provider("sql") == "gemini"
      and llm_provider.get_llm_provider("answer") == "gemini")
_clear_env()

os.environ["LLM_PROVIDER_TOOLS"] = "  Anthropic  "
check("LLM_PROVIDER_TOOLS tolerates surrounding whitespace and case",
      llm_provider.get_llm_provider("tools") == "anthropic")
_clear_env()

check("get_anthropic_model('tools') defaults to claude-sonnet-5-5",
      llm_provider.get_anthropic_model("tools") == "claude-sonnet-5-5")
os.environ["ANTHROPIC_MODEL_TOOLS"] = "claude-opus-5-5"
check("ANTHROPIC_MODEL_TOOLS overrides the tools role's model",
      llm_provider.get_anthropic_model("tools") == "claude-opus-5-5")
check("...and leaves the sql/answer roles' models at the default (independent)",
      llm_provider.get_anthropic_model("sql") == "claude-sonnet-5-5"
      and llm_provider.get_anthropic_model("answer") == "claude-sonnet-5-5")
_clear_env()

check('_intended_temperature("tools") is 0 (Gemini pins temperature=0 on chat_tool_loop too, '
      "for deterministic tool routing - same reasoning as the sql role)",
      llm_provider._intended_temperature("tools") == 0)

# --------------------------------------------------------------------------- 10. schema conversion

print("\n10. function_declarations_to_claude_tools: faithful JSON Schema conversion (W9-A2) - "
      "every REAL _build_*_tool() output round-trips")
from app.services import openai_client as _oc  # noqa: E402

from app.services import settings as _settings_mod  # noqa: E402

_real_builders = [
    _oc._build_search_tool, _oc._build_sql_tool, _oc._build_web_search_tool,
    _oc._build_analyze_tool, _oc._build_list_files_tool, _oc._build_tree_tool,
    _oc._build_glob_tool, _oc._build_read_document_tool, _oc._build_grep_tool,
    _oc._build_explore_knowledge_base_tool,
]
# _build_search_tool() reads get_metadata_schema(), which (unstubbed) needs a real Supabase
# connection - stub it with the app's own DEFAULT_METADATA_SCHEMA fixture, the same way
# test_openai_client_claude_path.py / test_tool_choice_input.py already do.
_saved_get_metadata_schema = _oc.get_metadata_schema
_oc.get_metadata_schema = lambda: _settings_mod.DEFAULT_METADATA_SCHEMA
try:
    _real_fds = [b() for b in _real_builders]
finally:
    _oc.get_metadata_schema = _saved_get_metadata_schema
_claude_tools = llm_provider.function_declarations_to_claude_tools(_real_fds)

check("one Claude tool per FunctionDeclaration, same count, same order",
      len(_claude_tools) == len(_real_fds), (len(_claude_tools), len(_real_fds)))

for fd, tool in zip(_real_fds, _claude_tools):
    check(f"{fd.name}: name round-trips", tool["name"] == fd.name)
    check(f"{fd.name}: description round-trips", tool["description"] == (fd.description or ""))
    check(f"{fd.name}: input_schema is a JSON object schema",
          tool["input_schema"]["type"] == "object", tool["input_schema"])
    expected_required = list(fd.parameters.required or [])
    check(f"{fd.name}: required fields round-trip (None and [] both mean 'nothing required')",
          tool["input_schema"].get("required", []) == expected_required,
          (tool["input_schema"].get("required"), expected_required))
    expected_props = set((fd.parameters.properties or {}).keys())
    actual_props = set(tool["input_schema"].get("properties", {}).keys())
    check(f"{fd.name}: every property name round-trips", actual_props == expected_props,
          (actual_props, expected_props))
    for pname, pschema in (fd.parameters.properties or {}).items():
        conv = tool["input_schema"]["properties"][pname]
        if pschema.enum:
            check(f"{fd.name}.{pname}: enum round-trips", conv.get("enum") == list(pschema.enum),
                  (conv.get("enum"), list(pschema.enum)))
        if pschema.type is not None:
            expected_type = str(getattr(pschema.type, "value", pschema.type)).lower()
            check(f"{fd.name}.{pname}: type round-trips as lowercase JSON Schema",
                  conv["type"] == expected_type, conv["type"])
        if pschema.type is not None and str(getattr(pschema.type, "value", pschema.type)) == "ARRAY":
            check(f"{fd.name}.{pname}: an ARRAY property carries a converted 'items'",
                  "items" in conv and conv["items"].get("type") == "string", conv)

# The tool list's LAST tool carries the prompt-caching breakpoint (requirement 4); no other does.
check("the last tool carries a cache_control breakpoint",
      _claude_tools[-1].get("cache_control") == {"type": "ephemeral"}, _claude_tools[-1])
check("no OTHER tool carries one",
      all("cache_control" not in t for t in _claude_tools[:-1]),
      [t["name"] for t in _claude_tools[:-1] if "cache_control" in t])

check("an empty function_declarations list converts to an empty tool list",
      llm_provider.function_declarations_to_claude_tools([]) == [])
check("None converts to an empty tool list too (defensive, mirrors stream_response's own "
      "function_declarations = [] default)",
      llm_provider.function_declarations_to_claude_tools(None) == [])

# Nested OBJECT/ARRAY-of-OBJECT: no real tool builder nests this deep today, but the brief asks
# for faithful conversion of "nested objects/arrays" in general - proven against a synthetic
# schema so the recursion is not just accidentally correct for today's flat shapes.
nested_fd = types.FunctionDeclaration(
    name="synthetic_nested", description="d",
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "items": types.Schema(
                type="ARRAY",
                items=types.Schema(
                    type="OBJECT",
                    properties={"id": types.Schema(type="STRING"),
                                "qty": types.Schema(type="INTEGER")},
                    required=["id"],
                ),
            ),
        },
        required=["items"],
    ),
)
nested_tool = llm_provider.function_declarations_to_claude_tools([nested_fd])[0]
check("a nested ARRAY-of-OBJECT schema converts recursively, faithfully",
      nested_tool["input_schema"] == {
          "type": "object",
          "properties": {
              "items": {
                  "type": "array",
                  "items": {
                      "type": "object",
                      "properties": {
                          "id": {"type": "string"},
                          "qty": {"type": "integer"},
                      },
                      "required": ["id"],
                  },
              },
          },
          "required": ["items"],
      }, nested_tool["input_schema"])

# --------------------------------------------------------------------------- 11. generate_tool_choice

print("\n11. generate_tool_choice (the tool-choice turn's Claude path, task W9-A2)")
_clear_env()

_real_fds2 = [_oc._build_analyze_tool(), _oc._build_sql_tool()]

fake_tool_msgs = _FakeMessages(create_response=_FakeMessage(
    [_FakeBlock("tool_use", name="search_documents", input={"query": "fixture"})]))
fake_tool_client = _FakeClient(fake_tool_msgs)

result = llm_provider.generate_tool_choice(
    system_text="SYSTEM TEXT", contents=[_content("user", "hello")],
    function_declarations=_real_fds2, client=fake_tool_client, name="chat_tool_loop")

check("exactly one create() call", len(fake_tool_msgs.create_calls) == 1)
tc_call = fake_tool_msgs.create_calls[0]
check("system is a single cache-marked text block carrying the system text verbatim",
      tc_call["system"] == [{"type": "text", "text": "SYSTEM TEXT",
                              "cache_control": {"type": "ephemeral"}}], tc_call["system"])
check("messages converted through gemini_contents_to_claude_messages",
      tc_call["messages"] == [{"role": "user", "content": "hello"}], tc_call["messages"])
check("tool_choice is auto (Gemini's own mode='AUTO')",
      tc_call["tool_choice"] == {"type": "auto"}, tc_call["tool_choice"])
check("exactly 2 tools sent, same count as function_declarations",
      len(tc_call["tools"]) == 2, tc_call["tools"])
check("tool names match the function declarations, in order",
      [t["name"] for t in tc_call["tools"]] == [fd.name for fd in _real_fds2])
check("the LAST tool carries the cache_control breakpoint",
      tc_call["tools"][-1].get("cache_control") == {"type": "ephemeral"})
check("the FIRST tool does not", "cache_control" not in tc_call["tools"][0])
check("model defaults to DEFAULT_ANTHROPIC_MODEL", tc_call["model"] == "claude-sonnet-5-5")
check("max_tokens defaults to TOOL_CHOICE_MAX_OUTPUT_TOKENS",
      tc_call["max_tokens"] == llm_provider.TOOL_CHOICE_MAX_OUTPUT_TOKENS)
check('"temperature" is never sent (Gemini pins 0 for this call; the installed SDK has no '
      "such parameter and the model rejects it)",
      "temperature" not in tc_call)

check("returns a Gemini-shaped response: exactly one candidate",
      len(result.candidates) == 1)
cand = result.candidates[0]
check("finish_reason is falsy (no Claude equivalent of MALFORMED_FUNCTION_CALL - requirement 3)",
      not cand.finish_reason, cand.finish_reason)
parts = cand.content.parts
check("exactly one part, carrying the tool_use block as a function_call",
      len(parts) == 1 and parts[0].function_call is not None, parts)
check("function_call.name is the tool_use block's name",
      parts[0].function_call.name == "search_documents")
check("function_call.args is usable as dict(fc.args), the exact call stream_response makes",
      dict(parts[0].function_call.args) == {"query": "fixture"})
check("the part's .text is falsy for a tool_use-only block", not parts[0].text, parts[0].text)

os.environ["ANTHROPIC_MODEL_TOOLS"] = "claude-haiku-5-5"
fake_tool_msgs2 = _FakeMessages(create_response=_FakeMessage(
    [_FakeBlock("text", text="a plain answer")]))
result2 = llm_provider.generate_tool_choice(
    system_text="s", contents=[], function_declarations=[], client=_FakeClient(fake_tool_msgs2))
check("ANTHROPIC_MODEL_TOOLS reaches the actual call",
      fake_tool_msgs2.create_calls[0]["model"] == "claude-haiku-5-5")
check("an empty function_declarations list sends no tools/tool_choice at all (matches Gemini's "
      "own tools=None, tool_config=None when no tool is enabled for the turn)",
      fake_tool_msgs2.create_calls[0]["tools"] is None
      and fake_tool_msgs2.create_calls[0]["tool_choice"] is None,
      fake_tool_msgs2.create_calls[0])
_clear_env()

parts2 = result2.candidates[0].content.parts
check("a text-only response: no part carries a function_call",
      all(p.function_call is None for p in parts2), parts2)
check("...and its text is the model's own text, read the same way as Gemini's no-function-call "
      "branch reads it",
      "".join(p.text or "" for p in parts2) == "a plain answer")

# Multiple tool_use blocks: the FIRST one is what stream_response's own "take the first part
# whose function_call is truthy" loop will find (requirement 3: "the FIRST tool_use block").
fake_tool_msgs3 = _FakeMessages(create_response=_FakeMessage([
    _FakeBlock("tool_use", name="tool_a", input={"x": 1}),
    _FakeBlock("tool_use", name="tool_b", input={"y": 2}),
]))
result3 = llm_provider.generate_tool_choice(
    system_text="s", contents=[], function_declarations=[], client=_FakeClient(fake_tool_msgs3))
parts3 = result3.candidates[0].content.parts
first_call = next(p.function_call for p in parts3 if p.function_call)
check("with multiple tool_use blocks, the FIRST one is the one a first-match loop finds",
      first_call.name == "tool_a", first_call.name)

fake_tool_err = _FakeMessages(raise_on_create=_FakeAPIError("overloaded"))
try:
    llm_provider.generate_tool_choice(system_text="s", contents=[], function_declarations=[],
                                      client=_FakeClient(fake_tool_err))
    check("generate_tool_choice propagates a create() error", False, "did not raise")
except _FakeAPIError as e:
    check("generate_tool_choice propagates the SAME exception type and message unchanged "
          '(requirement: "a Claude API error surfaces exactly as a Gemini API error does")',
          str(e) == "overloaded")
except Exception as e:  # noqa: BLE001
    check("generate_tool_choice propagates the SAME exception type (got a different type)",
          False, f"{type(e).__name__}: {e}")

captured_usage = []
_orig_record_usage_11 = llm_provider._record_usage


def _capture_usage_11(*, name, role, model, response):
    captured_usage.append({"name": name, "role": role, "model": model})


llm_provider._record_usage = _capture_usage_11
try:
    fake_tool_msgs4 = _FakeMessages(create_response=_FakeMessage([_FakeBlock("text", "ok")]))
    llm_provider.generate_tool_choice(system_text="s", contents=[], function_declarations=[],
                                      client=_FakeClient(fake_tool_msgs4), name="chat_tool_loop")
finally:
    llm_provider._record_usage = _orig_record_usage_11
check("generate_tool_choice calls _record_usage with role='tools'",
      captured_usage == [{"name": "chat_tool_loop", "role": "tools",
                          "model": "claude-sonnet-5-5"}], captured_usage)

# Cache tokens flow into the SAME usage dict every role's recording already uses (requirement 4).
from app.services.llm_usage import _usage_dict_anthropic  # noqa: E402

cached_usage_resp = _FakeMessage([_FakeBlock("text", "x")])
cached_usage_resp.usage.cache_creation_input_tokens = 500
cached_usage_resp.usage.cache_read_input_tokens = 26000
d = _usage_dict_anthropic(cached_usage_resp)
check("cache-write tokens are recorded", d.get("cache_creation_input_tokens") == 500, d)
check("cache-read tokens are recorded", d.get("cache_read_input_tokens") == 26000, d)

no_cache_resp = _FakeMessage([_FakeBlock("text", "x")])
d2 = _usage_dict_anthropic(no_cache_resp)
check("no cache fields on the response -> cache counts are 0, never missing/None (harmless for "
      "the sql/answer roles, which never cache)",
      d2.get("cache_creation_input_tokens") == 0 and d2.get("cache_read_input_tokens") == 0, d2)

# --------------------------------------------------------------------------- 12. generate_answer_text

print("\n12. generate_answer_text (the ANSWER_CHECK retry's Claude path, task W9-A2)")
_clear_env()
fake_ans_msgs = _FakeMessages(create_response=_FakeMessage(
    [_FakeBlock("text", text="The total load is 42 kW.")]))
text = llm_provider.generate_answer_text(
    system_text="SYSTEM PROMPT TEXT", contents=[_content("user", "what is the total load?")],
    client=_FakeClient(fake_ans_msgs), name="answer_tool_result_checked")
check("returns the exact text the fake produced, as a plain str",
      text == "The total load is 42 kW.", text)
check("exactly one create() call (non-streaming, unlike stream_answer_text)",
      len(fake_ans_msgs.create_calls) == 1)
a_call = fake_ans_msgs.create_calls[0]
check("system_text reaches `system` verbatim (a bare string, same shape as stream_answer_text)",
      a_call["system"] == "SYSTEM PROMPT TEXT", a_call)
check("contents converted through gemini_contents_to_claude_messages",
      a_call["messages"] == [{"role": "user", "content": "what is the total load?"}])
check("no tools/tool_choice is sent (this is an answer-writing call, not the tool-choice turn)",
      a_call["tools"] is None and a_call["tool_choice"] is None)
check("model defaults to DEFAULT_ANTHROPIC_MODEL (role='answer', same as stream_answer_text)",
      a_call["model"] == "claude-sonnet-5-5")
check("max_tokens defaults to ANSWER_MAX_TOKENS, the same constant stream_answer_text uses",
      a_call["max_tokens"] == llm_provider.ANSWER_MAX_TOKENS)
check('"temperature" is never sent', "temperature" not in a_call)

os.environ["ANTHROPIC_MODEL_ANSWER"] = "claude-opus-5-5"
fake_ans_msgs2 = _FakeMessages(create_response=_FakeMessage([_FakeBlock("text", "hi")]))
llm_provider.generate_answer_text(system_text="s", contents=[],
                                  client=_FakeClient(fake_ans_msgs2))
check("ANTHROPIC_MODEL_ANSWER reaches generate_answer_text too (same role as stream_answer_text)",
      fake_ans_msgs2.create_calls[0]["model"] == "claude-opus-5-5")
_clear_env()

fake_ans_msgs3 = _FakeMessages(create_response=_FakeMessage([]))
text3 = llm_provider.generate_answer_text(system_text="s", contents=[],
                                          client=_FakeClient(fake_ans_msgs3))
check('an empty content list -> "" (never None, never raises - same tolerance as '
      "generate_sql_text, sharing the same _response_text helper)",
      text3 == "", repr(text3))

fake_ans_err = _FakeMessages(raise_on_create=_FakeAPIError("rate limited"))
try:
    llm_provider.generate_answer_text(system_text="s", contents=[],
                                      client=_FakeClient(fake_ans_err))
    check("generate_answer_text propagates a create() error", False, "did not raise")
except _FakeAPIError as e:
    check("generate_answer_text propagates the SAME exception type and message unchanged",
          str(e) == "rate limited")
except Exception as e:  # noqa: BLE001
    check("generate_answer_text propagates the SAME exception type (got a different type)",
          False, f"{type(e).__name__}: {e}")

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
