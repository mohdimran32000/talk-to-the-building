"""test_agent_mode_wiring.py — stream_response hands the question to agent mode unless
AGENT_MODE turns it off (0 / false / off / no), and then takes exactly the path it took before.
If agent mode is on but cannot start (no model key), the classic path answers and a warning
is logged.

Run:
    venv/Scripts/python -X utf8 tests/test_agent_mode_wiring.py
"""
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import agent_mode, openai_client

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


calls = []
ADAPTER = object()


def fake_stream_agent(messages, user_id, supabase_client, adapter=None):
    calls.append((messages, user_id, supabase_client, adapter))
    yield ("tool_start", '{"tool": "run_sql"}')
    yield ("token", "agent answer")
    yield ("done", "")


def fake_adapter_from_env():
    return ADAPTER


class Sentinel(Exception):
    pass


def no_client():
    raise Sentinel("the old path was taken")


class Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def ask(**kw):
    """Run stream_response; True if it took the old path (hit the Sentinel), else the events."""
    try:
        return list(openai_client.stream_response(messages=msgs, **kw))
    except Sentinel:
        return True


real = (agent_mode.stream_agent, agent_mode.adapter_from_env, openai_client._get_client)
agent_mode.stream_agent = fake_stream_agent
agent_mode.adapter_from_env = fake_adapter_from_env
openai_client._get_client = no_client
sb = object()
msgs = [{"role": "user", "content": "how many pumps?"}]
DB = dict(user_id="u1", supabase_client=sb, has_documents=True, has_structured_data=True, structured_tables=[])
try:
    print("1. the default (AGENT_MODE unset) and AGENT_MODE=1 hand the question to agent mode")
    for value in (None, "1"):
        calls.clear()
        if value is None:
            os.environ.pop("AGENT_MODE", None)
        else:
            os.environ["AGENT_MODE"] = value
        events = ask(**DB)
        check(f"AGENT_MODE={value!r}: agent mode's events are the response",
              events == [("tool_start", '{"tool": "run_sql"}'), ("token", "agent answer"), ("done", "")], events)
        check(f"AGENT_MODE={value!r}: it gets the conversation, the user, the client and the adapter",
              calls == [(msgs, "u1", sb, ADAPTER)], calls)

    print("2. switch off: the existing path, untouched")
    for value in ("0", "false", "off"):
        calls.clear()
        os.environ["AGENT_MODE"] = value
        took_old = ask(**DB)
        check(f"AGENT_MODE={value!r}: the old path runs and agent mode is never called",
              took_old is True and calls == [], (took_old, calls))

    print("3. no database: agent mode is not attempted")
    os.environ.pop("AGENT_MODE", None)
    calls.clear()
    took_old = ask()
    check("without a user and client the old path runs", took_old is True and calls == [], (took_old, calls))

    print("4. agent mode on but it cannot start: the classic path answers, with a warning")
    handler = Records()
    logging.getLogger(openai_client.__name__).addHandler(handler)
    try:
        for exc in (ValueError("No Anthropic API key configured. Set ANTHROPIC_API_KEY in backend/.env."),
                    ImportError("No module named 'anthropic'")):
            calls.clear()
            handler.messages.clear()

            def broken(exc=exc):
                raise exc
            agent_mode.adapter_from_env = broken
            took_old = ask(**DB)
            check(f"{type(exc).__name__}: the old path runs and agent mode is never called",
                  took_old is True and calls == [], (took_old, calls))
            check(f"{type(exc).__name__}: a warning names the reason and the classic pipeline",
                  any("classic pipeline" in m and str(exc) in m for m in handler.messages), handler.messages)
    finally:
        logging.getLogger(openai_client.__name__).removeHandler(handler)

    print("5. any other error is not swallowed")

    def crash():
        raise RuntimeError("boom")
    agent_mode.adapter_from_env = crash
    try:
        ask(**DB)
        raised = False
    except RuntimeError:
        raised = True
    check("a RuntimeError from the adapter reaches the caller", raised)
finally:
    agent_mode.stream_agent, agent_mode.adapter_from_env, openai_client._get_client = real
    os.environ.pop("AGENT_MODE", None)

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
