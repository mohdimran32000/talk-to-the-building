"""test_openai_client_claude_path.py - LLM_PROVIDER_ANSWER wires all four answer prompts in
`openai_client.stream_response` onto Claude; Gemini unchanged when unset (spec 2026-09-30,
task W9-A1).

THE FOUR PATHS, same four `test_answer_date.py`'s harness already drives (reused here almost
verbatim, because they are the only four ways to reach an answer-writing generation):
"manual filter" (pre-retrieval context injection, no tool call), "malformed call" (the
tool-choice call returns MALFORMED_FUNCTION_CALL), "tool result" (a normal tool call, the
common case), "forced analysis" (the deterministic summarization override, no tool call).

THE PROOF, run twice per path - once with every switch unset (Gemini, byte-identical to
`3816958`), once with `LLM_PROVIDER_ANSWER=anthropic` (a fake `stream_answer_text`, never the
real network): each run's OTHER provider's call site is wired to raise if touched, so a
regression that silently fell through to the wrong provider fails loudly here, not quietly in
production. The two runs' captured `system_text` must be byte-identical PER PATH - the brief's
"the SAME prompt text the Gemini path builds goes to Claude" - and the token stream's
concatenated text must equal what the (fake) model returned, proving "the caller receives the
same chunk sequence shape as from Gemini" (a sequence of `("token", ...)` events ending in
`("done", "")`, `("tool_done", ...)` included where Gemini's own path already emits one).

Section 4 proves the ANSWER_CHECK=1 interaction - UPDATED by task W9-A2 (brief requirement 5,
closing a W9-A1 review finding): with LLM_PROVIDER_ANSWER=anthropic, ANSWER_CHECK=1's buffered
`_generate_checked_answer` for the "tool result" path now ALSO answers through Claude (via
`generate_answer_text`, its non-streaming sibling), never silently staying on Gemini - a
checked retry must never mix providers. Originally (W9-A1) this path stayed on Gemini
unconditionally; see task-W9A2-report.md for the full reasoning.

No network call anywhere in this file.

Run:
    venv/Scripts/python -X utf8 tests/test_openai_client_claude_path.py
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
    for k in ("LLM_PROVIDER_ANSWER", "ANTHROPIC_MODEL_ANSWER", "ANSWER_CHECK",
              "SQL_LOOP_MAX_STEPS"):
        os.environ.pop(k, None)


_clear_env()

# --------------------------------------------------------------------------- shared fixtures
# (lifted from test_answer_date.py's capture_answer_prompts, which already drives all four
# answer paths with the minimal fakes each one needs)


class _Call:
    def __init__(self, name, args):
        self.name, self.args = name, args


class _Finish:
    name = "MALFORMED_FUNCTION_CALL"


def _tool_choice_response(function_call=None, text=None, finish=None):
    """The non-streaming tool-choice call's response shape (chat_tool_loop - out of scope,
    always Gemini, always `generate_with_usage`)."""
    part = type("_Part", (), {"function_call": function_call, "text": text})()
    content = type("_Content", (), {"parts": [part]})()
    candidate = type("_Candidate", (), {"content": content, "finish_reason": finish})()
    return type("_Response", (), {"candidates": [candidate], "usage_metadata": None})()


class _NoDocument:
    data = []


CHUNK = {"file_name": "fixture.md", "content": "a fixture excerpt", "document_id": "d-fx"}

RUNS = {
    "manual filter": ({"manual_metadata_filter": {"doc_kind": "fixture"}}, None,
                      "a neutral question"),
    "malformed call": ({}, _tool_choice_response(finish=_Finish()), "a neutral question"),
    "tool result": ({}, _tool_choice_response(
        function_call=_Call("search_documents", {"query": "fixture excerpt"})),
        "a neutral question"),
    "forced analysis": ({}, _tool_choice_response(text="no tool"),
                        "summarize the fixture handbook"),
}
PATHS = tuple(RUNS)


class _GeminiChunk:
    def __init__(self, text):
        self.text = text


class _GeminiModels:
    """The Gemini answer call for ONE path - records its system_instruction, yields a
    scripted answer. Raises if ANY call reaches it while the test is proving the Claude
    branch is what actually ran."""
    def __init__(self, key, answer_text, forbidden=False):
        self.key, self.answer_text, self.forbidden = key, answer_text, forbidden
        self.calls = []

    def generate_content_stream(self, **kw):
        if self.forbidden:
            raise AssertionError(f"[{self.key}] Gemini must not be called - "
                                 "LLM_PROVIDER_ANSWER=anthropic")
        self.calls.append(kw["config"].system_instruction)
        return [_GeminiChunk(self.answer_text)]

    def generate_content(self, **kw):
        if self.forbidden:
            raise AssertionError(f"[{self.key}] Gemini must not be called - "
                                 "LLM_PROVIDER_ANSWER=anthropic")
        self.calls.append(kw["config"].system_instruction)
        return None


class _GeminiClient:
    def __init__(self, models):
        self.models = models


def _drive_all_paths(*, answer_text, gemini_forbidden, claude_fake):
    """Runs all four answer paths through `stream_response` once each, returns
    {path: {"events": [...], "gemini_calls": [...], "claude_calls": [...]}}.

    `claude_fake` (or None) replaces `llm_provider.stream_answer_text` for the duration -
    the SAME function-level monkeypatch `stream_response`'s lazy
    `from app.services.llm_provider import ... stream_answer_text` picks up, exactly like
    `sql_tool.py`'s own lazy import of `generate_sql_text` does."""
    out = {}
    saved = (oc._get_client, oc.get_llm_model, oc.get_text_to_sql_enabled,
             oc.get_web_search_enabled, oc.get_metadata_schema, llm_usage_mod.generate_with_usage,
             oc.retrieve_chunks, oc._execute_search_documents, oc._resolve_document,
             llm_provider.stream_answer_text)
    oc.get_llm_model = lambda: "fixture-gemini-model"
    oc.get_text_to_sql_enabled = lambda: False
    oc.get_web_search_enabled = lambda: False
    oc.get_metadata_schema = lambda: settings_mod.DEFAULT_METADATA_SCHEMA
    oc.retrieve_chunks = lambda *a, **k: [dict(CHUNK)]
    oc._execute_search_documents = lambda *a, **k: [dict(CHUNK)]
    oc._resolve_document = lambda *a, **k: _NoDocument()
    if claude_fake is not None:
        llm_provider.stream_answer_text = claude_fake
    try:
        for key, (extra, first, question) in RUNS.items():
            gemini_models = _GeminiModels(key, answer_text, forbidden=gemini_forbidden)
            oc._get_client = lambda key=key, m=gemini_models: _GeminiClient(m)
            llm_usage_mod.generate_with_usage = lambda client, first=first, **kw: first
            events = list(oc.stream_response(
                messages=[{"role": "user", "content": question}],
                user_id="u-fixture", supabase_client=object(),
                has_documents=True, has_structured_data=False, **extra))
            out[key] = {"events": events, "gemini_calls": gemini_models.calls}
    finally:
        (oc._get_client, oc.get_llm_model, oc.get_text_to_sql_enabled,
         oc.get_web_search_enabled, oc.get_metadata_schema, llm_usage_mod.generate_with_usage,
         oc.retrieve_chunks, oc._execute_search_documents, oc._resolve_document,
         llm_provider.stream_answer_text) = saved
    return out


def _tokens(events):
    return "".join(d for k, d in events if k == "token")


# --------------------------------------------------------------------------- 1. unset -> Gemini

print("1. LLM_PROVIDER_ANSWER unset: all four paths use Gemini, byte-identical shape")
_clear_env()
GEMINI_ANSWER = "the gemini answer text"
gemini_run = _drive_all_paths(answer_text=GEMINI_ANSWER, gemini_forbidden=False, claude_fake=None)

check("all four paths were driven", sorted(gemini_run) == sorted(PATHS), sorted(gemini_run))
for path in PATHS:
    run = gemini_run[path]
    check(f"{path}: the Gemini fake was called exactly once", len(run["gemini_calls"]) == 1,
          run["gemini_calls"])
    check(f"{path}: the token stream carries the Gemini answer text",
          _tokens(run["events"]) == GEMINI_ANSWER, _tokens(run["events"]))
    check(f"{path}: the event stream ends with a done event",
          run["events"][-1] == ("done", ""), run["events"][-1])

# --------------------------------------------------------------------------- 2. anthropic -> Claude

print("\n2. LLM_PROVIDER_ANSWER=anthropic: all four paths use the fake Claude client instead")
_clear_env()
os.environ["LLM_PROVIDER_ANSWER"] = "anthropic"
CLAUDE_ANSWER = "the claude answer text"
claude_calls = []


def _fake_stream_answer_text(*, system_text, contents, max_tokens=64000, client=None,
                             name="answer_stream"):
    claude_calls.append({"system_text": system_text, "name": name, "max_tokens": max_tokens})
    for piece in CLAUDE_ANSWER.split(" "):
        yield piece + " "


claude_run = _drive_all_paths(answer_text="UNUSED", gemini_forbidden=True,
                              claude_fake=_fake_stream_answer_text)

check("all four paths were driven on the Claude branch too",
      sorted(claude_run) == sorted(PATHS), sorted(claude_run))
for path in PATHS:
    run = claude_run[path]
    check(f"{path}: Gemini was never called (the forbidden fake would have raised otherwise)",
          run["gemini_calls"] == [], run["gemini_calls"])
    check(f"{path}: the token stream carries the Claude answer text, chunk by chunk",
          _tokens(run["events"]).strip() == CLAUDE_ANSWER, _tokens(run["events"]))
    tail = run["events"][-1]
    check(f"{path}: the event stream still ends with a done event (same shape as Gemini)",
          tail == ("done", ""), tail)

check(f"exactly one stream_answer_text() call per path ({len(PATHS)} total)",
      len(claude_calls) == len(PATHS), len(claude_calls))

# --------------------------------------------------------------------------- 3. same prompt text, per path

print("\n3. the SAME prompt text per path reaches Claude as reached Gemini (requirement 3)")
by_path_claude = {}
for i, path in enumerate(PATHS):
    by_path_claude[path] = claude_calls[i]["system_text"]

for path in PATHS:
    gemini_text = gemini_run[path]["gemini_calls"][0]
    claude_text = by_path_claude.get(path)
    check(f"{path}: system_text sent to Claude is byte-identical to the one sent to Gemini",
          claude_text == gemini_text,
          f"gemini={gemini_text!r}\nclaude={claude_text!r}" if claude_text != gemini_text else "")

# tool_choice_input's own frozen-prompt golden test guards TOOL_CHOICE_FORMAT_RULES
# separately; this file only needs to confirm the live rules (which the date line and the
# answer system_text both carry) were not quietly duplicated or dropped by either branch.
for path in PATHS:
    check(f"{path}: the live OUTPUT_FORMAT_RULES text is present in what Claude was sent",
          oc.OUTPUT_FORMAT_RULES in by_path_claude[path])

_clear_env()

# --------------------------------------------------------------------------- 4. ANSWER_CHECK now follows the switch too (W9-A2)
#
# UPDATED by task W9-A2 (brief requirement 5, closing the W9-A1 review's "Minor, but worth
# naming" finding): `_generate_checked_answer` now reads `_answer_on_claude` too, so
# ANSWER_CHECK=1 + LLM_PROVIDER_ANSWER=anthropic sends the 'tool result' path's checked
# generation to Claude as well - through `generate_answer_text` (the NON-streaming sibling of
# `stream_answer_text`; `_generate_checked_answer` is buffered, never streamed). Gemini's
# `generate_content` is now wired to RAISE if touched at all - proving this path is no longer
# the Gemini-only exception W9-A1 shipped. The isolated-unit-level proof of the retry mechanics
# itself (primary + retry, both via Claude, never mixing providers) lives in
# `test_answer_check_wiring.py`; this is the end-to-end wiring confirmation through
# `stream_response`'s own tool-result path.

print("\n4. ANSWER_CHECK=1 + LLM_PROVIDER_ANSWER=anthropic: the 'tool result' path's checked "
     "generation now goes to Claude too (task W9-A2 - was Gemini-only under W9-A1)")
_clear_env()
os.environ["LLM_PROVIDER_ANSWER"] = "anthropic"
os.environ["ANSWER_CHECK"] = "1"
os.environ["SQL_LOOP_MAX_STEPS"] = "1"

ac_claude_calls = []


def _ac_claude_fake(*, system_text, contents, max_tokens=64000, client=None, name="answer_stream"):
    ac_claude_calls.append(name)
    return "the checked claude answer"


class _ACModels:
    def generate_content_stream(self, **kw):
        raise AssertionError("ANSWER_CHECK=1 must not use the streaming call")

    def generate_content(self, *, model, contents, config=None):
        raise AssertionError("Gemini must not be called - LLM_PROVIDER_ANSWER=anthropic now "
                              "reaches the checked-answer path too (task W9-A2)")


class _ACClient:
    def __init__(self):
        self.models = _ACModels()


saved = (oc._get_client, oc.get_llm_model, oc.get_text_to_sql_enabled,
         oc.get_web_search_enabled, llm_usage_mod.generate_with_usage,
         oc._execute_search_documents, llm_provider.stream_answer_text,
         llm_provider.generate_answer_text)
oc._get_client = lambda: _ACClient()
oc.get_llm_model = lambda: "fixture-model"
oc.get_text_to_sql_enabled = lambda: False
oc.get_web_search_enabled = lambda: False
oc._execute_search_documents = lambda *a, **k: []
# stream_answer_text (the streaming answer prompts) must also stay untouched on this path -
# ANSWER_CHECK=1 short-circuits to the buffered _generate_checked_answer before any of those
# four sites would run.


def _stream_answer_text_forbidden(**kw):
    raise AssertionError("stream_answer_text must not be used on the ANSWER_CHECK=1 path "
                          "(it is buffered, never streamed)")


llm_provider.stream_answer_text = _stream_answer_text_forbidden
llm_provider.generate_answer_text = _ac_claude_fake
llm_usage_mod.generate_with_usage = lambda client, **kw: _tool_choice_response(
    function_call=_Call("search_documents", {"query": "fixture excerpt"}))
try:
    ac_events = list(oc.stream_response(
        messages=[{"role": "user", "content": "a neutral question"}],
        user_id="u-fixture", supabase_client=object(),
        has_documents=True, has_structured_data=False))
finally:
    (oc._get_client, oc.get_llm_model, oc.get_text_to_sql_enabled,
     oc.get_web_search_enabled, llm_usage_mod.generate_with_usage,
     oc._execute_search_documents, llm_provider.stream_answer_text,
     llm_provider.generate_answer_text) = saved
    _clear_env()

check("the checked-answer path's CLAUDE text reached the caller (Gemini would have raised)",
      _tokens(ac_events) == "the checked claude answer", _tokens(ac_events))
check("generate_answer_text (Claude) was called at least once for this path",
      len(ac_claude_calls) >= 1, ac_claude_calls)

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
