"""test_openai_client_claude_tools_path.py - LLM_PROVIDER_TOOLS wires the tool-choice turn
(`chat_tool_loop`) in `openai_client.stream_response` onto Claude; Gemini unchanged when unset
(spec 2026-09-30, task W9-A2).

WHAT THIS GUARDS. The chat model picks its first tool in ONE temperature-0, non-streaming call
(`generate_with_usage(client, ..., name="chat_tool_loop")`, built from `_build_system_prompt`
and the `_build_*_tool()` function declarations). This file proves the WIRING around that call
site: with the switch unset, the call is byte-identical to before this task (Claude's own
`generate_tool_choice` is never even touched); with `LLM_PROVIDER_TOOLS=anthropic`, Gemini's
`generate_with_usage`/`_get_client` are never touched, `generate_tool_choice` receives the SAME
system text, conversation turns and function declarations the Gemini branch would have built,
and - whatever it returns - EVERYTHING AFTER THE CALL (has_function_call detection, the
(name, args) extraction, the forced_tool override, tool dispatch, the "no function call" text
branch) behaves identically whichever provider produced the response (brief requirement 3:
"everything after it runs unchanged").

The internal mechanics of `generate_tool_choice` itself (schema conversion fidelity, cache_control
marks, tool_choice=auto, usage recording) are unit-tested against a fake Anthropic client in
`test_llm_provider.py` section 11 - NOT re-proven here. Here, `llm_provider.generate_tool_choice`
is stubbed wholesale (the same technique `test_sql_tool_claude_path.py`/
`test_openai_client_claude_path.py` use for `generate_sql_text`/`stream_answer_text`), trusting
that its own contract (return a Gemini-shaped response) is upheld - proven separately.

No network call anywhere in this file.

Run:
    venv/Scripts/python -X utf8 tests/test_openai_client_claude_tools_path.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import llm_usage as llm_usage_mod  # noqa: E402
from app.services import llm_provider  # noqa: E402
from app.services import openai_client as oc  # noqa: E402
from app.services import settings as settings_mod  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


def _clear_env():
    for k in ("LLM_PROVIDER_TOOLS", "ANTHROPIC_MODEL_TOOLS"):
        os.environ.pop(k, None)


_clear_env()

# --------------------------------------------------------------------------- fixtures
# A Gemini-shaped non-streaming response (chat_tool_loop's own shape) - the SAME shape
# `generate_tool_choice`'s real adapter produces too (proven in test_llm_provider.py), so one
# fixture serves both "a real Gemini response" and "what a stubbed generate_tool_choice returns".


class _Call:
    def __init__(self, name, args):
        self.name, self.args = name, args


class _Part:
    def __init__(self, function_call=None, text=None):
        self.function_call, self.text = function_call, text


class _Content:
    def __init__(self, parts):
        self.parts = parts


class _Candidate:
    def __init__(self, content, finish_reason=None):
        self.content, self.finish_reason = content, finish_reason


class _Response:
    def __init__(self, parts, finish_reason=None):
        self.candidates = [_Candidate(_Content(parts), finish_reason)]
        self.usage_metadata = None


def _tool_call_response(name, args):
    return _Response([_Part(function_call=_Call(name, args))])


def _text_response(text):
    return _Response([_Part(text=text)])


class _GeminiChunk:
    def __init__(self, text):
        self.text = text


class _GeminiAnswerModels:
    """The SEPARATE answer-writing call (`client.models.generate_content_stream`) that runs
    AFTER the tool-choice turn - always Gemini in this file (LLM_PROVIDER_ANSWER is never set
    here); only the tool-CHOICE turn's provider is under test."""
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def generate_content_stream(self, **kw):
        self.calls += 1
        return [_GeminiChunk(self.text)]

    def generate_content(self, **kw):
        self.calls += 1
        return _text_response(self.text)


class _GeminiAnswerClient:
    def __init__(self, models):
        self.models = models


class _NoDocument:
    data = []


CHUNK = {"file_name": "fixture.md", "content": "a fixture excerpt", "document_id": "d-fx"}
DOC_TOOL_NAMES = ["analyze_document", "search_documents", "list_files", "tree", "glob",
                  "read_document", "grep", "explore_knowledge_base"]


def _drive(*, tools_env, gemini_tool_choice_response, claude_tool_choice_response,
           gemini_forbidden=False, claude_forbidden=False, question="a neutral question",
           answer_text="the final answer text"):
    """Runs ONE `stream_response` call (has_documents=True, has_structured_data=False - the 8
    document tools, no SQL/web tool, keeping the fixture small). Returns (events, captured,
    answer_models) where `captured` holds whatever the chat_tool_loop call recorded on
    whichever branch actually ran."""
    captured = {"gemini_config": None, "gemini_kwargs": None, "claude_kwargs": None}

    def _fake_generate_with_usage(client, *, model, contents, config, name):
        captured["gemini_config"] = config
        captured["gemini_kwargs"] = {"model": model, "contents": contents, "name": name}
        if gemini_forbidden:
            raise AssertionError("Gemini's chat_tool_loop call must not run - "
                                  "LLM_PROVIDER_TOOLS=anthropic")
        return gemini_tool_choice_response

    def _fake_generate_tool_choice(*, system_text, contents, function_declarations,
                                   name="chat_tool_loop", max_output_tokens=None, client=None):
        captured["claude_kwargs"] = {"system_text": system_text, "contents": contents,
                                      "function_declarations": function_declarations,
                                      "name": name}
        if claude_forbidden:
            raise AssertionError("generate_tool_choice must not run - LLM_PROVIDER_TOOLS unset")
        return claude_tool_choice_response

    answer_models = _GeminiAnswerModels(answer_text)
    saved = (oc._get_client, oc.get_llm_model, oc.get_text_to_sql_enabled,
             oc.get_web_search_enabled, oc.get_metadata_schema,
             oc._execute_search_documents, oc._resolve_document,
             llm_usage_mod.generate_with_usage, llm_provider.generate_tool_choice)
    oc._get_client = lambda: _GeminiAnswerClient(answer_models)
    oc.get_llm_model = lambda: "fixture-gemini-model"
    oc.get_text_to_sql_enabled = lambda: False
    oc.get_web_search_enabled = lambda: False
    oc.get_metadata_schema = lambda: settings_mod.DEFAULT_METADATA_SCHEMA
    oc._execute_search_documents = lambda *a, **k: [dict(CHUNK)]
    oc._resolve_document = lambda *a, **k: _NoDocument()
    llm_usage_mod.generate_with_usage = _fake_generate_with_usage
    llm_provider.generate_tool_choice = _fake_generate_tool_choice
    if tools_env is not None:
        os.environ["LLM_PROVIDER_TOOLS"] = tools_env
    else:
        os.environ.pop("LLM_PROVIDER_TOOLS", None)
    try:
        events = list(oc.stream_response(
            messages=[{"role": "user", "content": question}],
            user_id="u-fixture", supabase_client=object(),
            has_documents=True, has_structured_data=False))
    finally:
        (oc._get_client, oc.get_llm_model, oc.get_text_to_sql_enabled,
         oc.get_web_search_enabled, oc.get_metadata_schema,
         oc._execute_search_documents, oc._resolve_document,
         llm_usage_mod.generate_with_usage, llm_provider.generate_tool_choice) = saved
        os.environ.pop("LLM_PROVIDER_TOOLS", None)
    return events, captured, answer_models


def _tokens(events):
    return "".join(d for k, d in events if k == "token")


def _tool_starts(events):
    import json
    return [json.loads(d) for k, d in events if k == "tool_start"]


# --------------------------------------------------------------------------- 1. unset: byte-identical config

print("1. LLM_PROVIDER_TOOLS unset: the tool-choice call is byte-identical to today's")
_clear_env()
events1, captured1, _ = _drive(
    tools_env=None, claude_forbidden=True,
    gemini_tool_choice_response=_text_response("a plain Gemini answer"),
    claude_tool_choice_response=None)

check("generate_tool_choice was never called (it would have raised otherwise)",
      captured1["claude_kwargs"] is None)
cfg = captured1["gemini_config"]
check("a chat_tool_loop config was built", cfg is not None)
expected_system_text = oc._build_system_prompt(True, False, False, None, None)
check("system_instruction matches the real _build_system_prompt output, unchanged",
      cfg.system_instruction == expected_system_text)
check("tool_config.function_calling_config.mode is AUTO, unchanged",
      cfg.tool_config.function_calling_config.mode == "AUTO")
check("temperature is 0, unchanged (deterministic tool routing)", cfg.temperature == 0)
check("automatic_function_calling is disabled, unchanged", cfg.automatic_function_calling.disable)
check("the 8 document tools are present, in the documented order, unchanged",
      [fd.name for fd in cfg.tools[0].function_declarations] == DOC_TOOL_NAMES,
      [fd.name for fd in cfg.tools[0].function_declarations])
check("a text-only response takes the no-function-call branch (Gemini's own text, unchanged)",
      _tokens(events1) == "a plain Gemini answer", _tokens(events1))
check("event stream ends with done", events1[-1] == ("done", ""))

# --------------------------------------------------------------------------- 2. unset: dispatch baseline

print("\n2. LLM_PROVIDER_TOOLS unset, a function-call response: the dispatch BASELINE "
      "(compared against the Claude-path run in section 4)")
_clear_env()
GEMINI_FC_ARGS = {"query": "fixture excerpt"}
events2, captured2, answer_models2 = _drive(
    tools_env=None, claude_forbidden=True,
    gemini_tool_choice_response=_tool_call_response("search_documents", GEMINI_FC_ARGS),
    claude_tool_choice_response=None)
starts2 = _tool_starts(events2)
check("exactly one tool_start event, for search_documents with the model's own args",
      starts2 == [{"tool": "search_documents", "args": GEMINI_FC_ARGS}], starts2)
check("the final answer text reached the caller", _tokens(events2) == "the final answer text",
      _tokens(events2))
check("event stream ends with done", events2[-1] == ("done", ""))

# --------------------------------------------------------------------------- 3. switch on: same inputs reach Claude

print("\n3. LLM_PROVIDER_TOOLS=anthropic: generate_tool_choice receives the SAME system text, "
      "conversation turns and function declarations the Gemini branch would have built")
_clear_env()
events3, captured3, _ = _drive(
    tools_env="anthropic", gemini_forbidden=True,
    gemini_tool_choice_response=None,
    claude_tool_choice_response=_text_response("a plain Gemini answer"))

check("Gemini's chat_tool_loop call was never made (it would have raised otherwise)",
      captured3["gemini_kwargs"] is None and captured3["gemini_config"] is None)
ck = captured3["claude_kwargs"]
check("generate_tool_choice WAS called exactly once", ck is not None)
check("system_text is byte-identical to the unset run's system_instruction (requirement 2)",
      ck["system_text"] == expected_system_text, ck["system_text"] == expected_system_text)
check("name is 'chat_tool_loop', same as the Gemini call's own name",
      ck["name"] == "chat_tool_loop", ck["name"])
check("function_declarations carries the same 8 document tools, in the same order",
      [fd.name for fd in ck["function_declarations"]] == DOC_TOOL_NAMES,
      [fd.name for fd in ck["function_declarations"]])
check("contents is a non-empty list of google-genai Content turns (the same shape "
      "gemini_contents_to_claude_messages converts from)",
      len(ck["contents"]) == 1 and ck["contents"][0].role == "user")
check("a text-only Claude response takes the SAME no-function-call branch as Gemini's own "
      "equivalent text response (section 1)",
      _tokens(events3) == "a plain Gemini answer", _tokens(events3))
check("event stream ends with done", events3[-1] == ("done", ""))

# --------------------------------------------------------------------------- 4. switch on: dispatch parity

print("\n4. LLM_PROVIDER_TOOLS=anthropic, a tool_use-shaped (Gemini-adapted) response: "
      "dispatches the SAME tool with the SAME args as the equivalent Gemini function call")
_clear_env()
events4, captured4, answer_models4 = _drive(
    tools_env="anthropic", gemini_forbidden=True,
    gemini_tool_choice_response=None,
    claude_tool_choice_response=_tool_call_response("search_documents", GEMINI_FC_ARGS))
starts4 = _tool_starts(events4)
check("the SAME tool_start event as the Gemini baseline (section 2), name AND args",
      starts4 == starts2, (starts4, starts2))
check("the SAME final answer text as the Gemini baseline",
      _tokens(events4) == _tokens(events2), (_tokens(events4), _tokens(events2)))
check("event stream ends with done", events4[-1] == ("done", ""))

# --------------------------------------------------------------------------- 5. MALFORMED_FUNCTION_CALL: no Claude equivalent

print("\n5. Claude has no MALFORMED_FUNCTION_CALL - a text-only Claude response is read as the "
      "model's own text, never routed through Gemini's malformed-call fallback")
_clear_env()
# A response whose finish_reason, if it were truthy, WOULD trigger Gemini's malformed-call
# branch - generate_tool_choice's real adapter always sets finish_reason=None (proven in
# test_llm_provider.py); this proves stream_response correctly takes no special action when
# finish_reason is falsy, whichever provider is active.
events5, captured5, _ = _drive(
    tools_env="anthropic", gemini_forbidden=True,
    gemini_tool_choice_response=None,
    claude_tool_choice_response=_text_response("not a malformed-fallback answer"))
check("the answer is the model's own text (section 3's branch), not the malformed-fallback "
      "system prompt's answer",
      _tokens(events5) == "not a malformed-fallback answer", _tokens(events5))
check("event stream ends with done (no extra fallback round-trip)", events5[-1] == ("done", ""))

# --------------------------------------------------------------------------- 6. forced_tool override on a Claude pick

print("\n6. forced_tool override still applies on top of a Claude tool pick (summarization "
      "intent overrides whatever tool Claude chose, same as it would for Gemini)")
_clear_env()
events6, captured6, _ = _drive(
    tools_env="anthropic", gemini_forbidden=True,
    gemini_tool_choice_response=None,
    claude_tool_choice_response=_tool_call_response("search_documents", {"query": "x"}),
    question="please summarize the fixture handbook for me")
starts6 = _tool_starts(events6)
check("Claude picked search_documents, but the summarization override redirects to "
      "analyze_document (same override Gemini's own pick would get)",
      len(starts6) == 1 and starts6[0]["tool"] == "analyze_document", starts6)

# --------------------------------------------------------------------------- 7. errors propagate uncaught

print("\n7. a generate_tool_choice error surfaces exactly as a Gemini chat_tool_loop error "
      "does on this call (requirement 3) - neither is caught here")
_clear_env()


class _FakeAPIError(Exception):
    pass


def _raise_gemini(client, **kw):
    raise _FakeAPIError("gemini outage")


def _raise_claude(**kw):
    raise _FakeAPIError("claude outage")


for label, tools_env, gen_with_usage, gen_tool_choice in (
    ("Gemini (unset)", None, _raise_gemini, None),
    ("Claude (anthropic)", "anthropic", None, _raise_claude),
):
    saved = (oc._get_client, oc.get_llm_model, oc.get_text_to_sql_enabled,
             oc.get_web_search_enabled, oc.get_metadata_schema,
             llm_usage_mod.generate_with_usage, llm_provider.generate_tool_choice)
    oc._get_client = lambda: _GeminiAnswerClient(_GeminiAnswerModels("unused"))
    oc.get_llm_model = lambda: "fixture-gemini-model"
    oc.get_text_to_sql_enabled = lambda: False
    oc.get_web_search_enabled = lambda: False
    oc.get_metadata_schema = lambda: settings_mod.DEFAULT_METADATA_SCHEMA
    if gen_with_usage is not None:
        llm_usage_mod.generate_with_usage = gen_with_usage
    if gen_tool_choice is not None:
        llm_provider.generate_tool_choice = gen_tool_choice
    if tools_env is not None:
        os.environ["LLM_PROVIDER_TOOLS"] = tools_env
    else:
        os.environ.pop("LLM_PROVIDER_TOOLS", None)
    try:
        try:
            list(oc.stream_response(
                messages=[{"role": "user", "content": "a neutral question"}],
                user_id="u-fixture", supabase_client=object(),
                has_documents=True, has_structured_data=False))
            check(f"{label}: an API error propagates out of stream_response", False,
                  "did not raise")
        except _FakeAPIError as e:
            check(f"{label}: the SAME exception type and message propagate, uncaught",
                  str(e) == f"{'gemini' if 'Gemini' in label else 'claude'} outage")
        except Exception as e:  # noqa: BLE001
            check(f"{label}: the SAME exception type propagates (got a different type)",
                  False, f"{type(e).__name__}: {e}")
    finally:
        (oc._get_client, oc.get_llm_model, oc.get_text_to_sql_enabled,
         oc.get_web_search_enabled, oc.get_metadata_schema,
         llm_usage_mod.generate_with_usage, llm_provider.generate_tool_choice) = saved
        _clear_env()

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
