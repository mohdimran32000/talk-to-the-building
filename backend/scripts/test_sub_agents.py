"""Tests for Module 8: Sub-Agents (analyze_document tool).

Tests: sub-agent SSE events, tool_metadata persistence, regular search unaffected,
graceful handling of non-existent documents.
"""
import os
import sys
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from test_helpers import (
    BASE_URL, test, section, reset_counters, get_auth_token,
    auth_headers, track_file, cleanup_files, poll_document_status,
    track_thread, cleanup_threads,
)


def stream_sse_full(token, thread_id, content, timeout=90):
    """Send a message and collect ALL SSE event types (including sub-agent events)."""
    resp = requests.post(
        f"{BASE_URL}/api/threads/{thread_id}/messages",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"content": content},
        stream=True,
        timeout=timeout,
    )
    if resp.status_code != 200:
        return [], resp.status_code

    events = []
    for line in resp.iter_lines():
        if not line:
            continue
        decoded = line.decode("utf-8") if isinstance(line, bytes) else line
        if not decoded.startswith("data:"):
            continue
        json_str = decoded[5:].strip()
        if not json_str or json_str == "[DONE]":
            continue
        try:
            event = json.loads(json_str)
            events.append(event)
            if event.get("type") == "done":
                break
        except json.JSONDecodeError:
            pass

    return events, resp.status_code


def run():
    reset_counters()
    token = get_auth_token()
    headers = auth_headers(token)

    # ── Setup: Upload a test document ──
    section("Sub-Agent Setup")

    test_content = (
        "This is the quarterly business report for Q4 2025.\n\n"
        "Key findings:\n"
        "1. Revenue grew 15% year-over-year to $2.3M\n"
        "2. Customer retention rate improved to 94%\n"
        "3. New product launches exceeded targets by 20%\n"
        "4. Operating costs decreased by 8% due to automation\n\n"
        "Recommendations:\n"
        "- Continue investing in automation\n"
        "- Expand into European markets in Q1 2026\n"
        "- Increase R&D spending by 10%\n"
    )
    files = {"file": ("subagent_test_report.txt", test_content.encode(), "text/plain")}
    r = requests.post(
        f"{BASE_URL}/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files=files,
    )
    test("Upload test document returns 200", r.status_code == 200)
    doc = r.json()
    doc_id = doc.get("id")
    doc_name = doc.get("file_name", "subagent_test_report.txt")
    if doc_id:
        track_file(doc_id)

    # Wait for ingestion
    if doc_id:
        status, error = poll_document_status(token, doc_id, target="ready", max_wait=60)
        test("Test document reaches ready status", status == "ready", f"status={status}, error={error}")

    # ── Test 1: analyze_document SSE events ──
    section("Sub-Agent SSE Events")

    r = requests.post(f"{BASE_URL}/api/threads", headers=headers, json={"title": "Sub-agent test"})
    thread = r.json()
    thread_id = thread.get("id")
    if thread_id:
        track_thread(thread_id)

    if thread_id and doc_id:
        events, status_code = stream_sse_full(
            token, thread_id,
            f"Summarize the document {doc_name}",
            timeout=120,
        )
        test("Sub-agent SSE returns 200", status_code == 200)

        event_types = [e.get("type") for e in events]
        # Plan 06-04 collapsed legacy sub_agent_* events into one 'sub_agent' envelope
        # with a discriminating 'event' field. Assert on the new shape.
        sub_envelopes = [e for e in events if e.get("type") == "sub_agent"]
        sub_event_names = [e.get("event") for e in sub_envelopes]
        test("SSE has sub_agent event=start", "start" in sub_event_names,
             f"sub_event_names={sub_event_names}")
        test("SSE has sub_agent event=token(s)", "token" in sub_event_names,
             f"sub_event_names={sub_event_names}")
        test("SSE has sub_agent event=done", "done" in sub_event_names,
             f"sub_event_names={sub_event_names}")
        test("SSE has done event", "done" in event_types,
             f"event_types={event_types}")

        # The start envelope should carry document_name in its payload (analyze_document case).
        start_envelopes = [e for e in sub_envelopes if e.get("event") == "start"]
        if start_envelopes:
            payload = start_envelopes[0].get("payload") or {}
            test("sub_agent start payload has document_name", "document_name" in payload,
                 f"start_envelope={start_envelopes[0]}")

        # Check main agent tokens exist (context-injection response)
        token_events = [e for e in events if e.get("type") == "token"]
        main_text = "".join(e.get("content", "") for e in token_events)
        test("Main agent returns synthesized response", len(main_text) > 0,
             f"main_text_len={len(main_text)}")

    # ── Test 2: tool_metadata persisted ──
    section("Tool Metadata Persistence")

    if thread_id:
        r = requests.get(f"{BASE_URL}/api/threads/{thread_id}/messages", headers=headers)
        test("GET messages returns 200", r.status_code == 200)
        msgs = r.json()
        assistant_msgs = [m for m in msgs if m.get("role") == "assistant"]
        test("Assistant message exists", len(assistant_msgs) > 0)

        if assistant_msgs:
            last_assistant = assistant_msgs[-1]
            tm = last_assistant.get("tool_metadata")
            test("tool_metadata is populated", tm is not None, f"tool_metadata={tm}")
            if tm:
                tools_used = tm.get("tools_used", [])
                test("tools_used is non-empty", len(tools_used) > 0)
                if tools_used:
                    test("tool is analyze_document", tools_used[0].get("tool") == "analyze_document",
                         f"tool={tools_used[0].get('tool')}")
                    test("document_name is set", bool(tools_used[0].get("document_name")),
                         f"doc_name={tools_used[0].get('document_name')}")
                    test("sub_agent_result is set", bool(tools_used[0].get("sub_agent_result")),
                         f"result={tools_used[0].get('sub_agent_result', '')[:50]}")

    # ── Test 3: Regular search unaffected ──
    section("Regular Search Regression")

    r = requests.post(f"{BASE_URL}/api/threads", headers=headers, json={"title": "Regular search test"})
    thread2 = r.json()
    thread2_id = thread2.get("id")
    if thread2_id:
        track_thread(thread2_id)

    if thread2_id:
        events, status_code = stream_sse_full(
            token, thread2_id,
            "What did the report say about revenue?",
            timeout=90,
        )
        test("Regular search SSE returns 200", status_code == 200)

        event_types = [e.get("type") for e in events]
        # Regular search uses search_documents (not analyze_document), so no sub_agent
        # envelope with event=start for an Explorer-style sub-agent should fire.
        has_explorer_start = any(
            e.get("type") == "sub_agent" and e.get("event") == "start"
            and (e.get("agent_name") in ("analyze_document", "explore_knowledge_base"))
            for e in events
        )
        test("Regular search has NO Explorer sub_agent start event", not has_explorer_start,
             f"event_types={event_types}")
        test("Regular search has done event", "done" in event_types)

    # ── Test 4: Document not found graceful ──
    section("Document Not Found Graceful")

    r = requests.post(f"{BASE_URL}/api/threads", headers=headers, json={"title": "Not found test"})
    thread3 = r.json()
    thread3_id = thread3.get("id")
    if thread3_id:
        track_thread(thread3_id)

    if thread3_id:
        events, status_code = stream_sse_full(
            token, thread3_id,
            "Analyze the document nonexistent_file_xyz.pdf",
            timeout=90,
        )
        test("Not-found query SSE returns 200", status_code == 200)

        event_types = [e.get("type") for e in events]
        test("Not-found query completes with done event", "done" in event_types,
             f"event_types={event_types}")

        # Should not have errored out
        error_events = [e for e in events if e.get("type") == "error"]
        test("No error events for not-found doc", len(error_events) == 0,
             f"errors={error_events}")

    # ── Cleanup ──
    section("Cleanup")
    cleanup_threads(token)
    cleanup_files(token)
    test("Cleanup complete", True)

    from test_helpers import passed, failed
    return passed, failed


if __name__ == "__main__":
    p, f = run()
    print(f"\n{'='*40}")
    print(f"Results: {p} passed, {f} failed")
    sys.exit(0 if f == 0 else 1)
