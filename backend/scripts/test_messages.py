"""Messages + SSE streaming tests."""
import sys
import os
import requests

sys.path.insert(0, os.path.dirname(__file__))
import test_helpers as h


def run():
    h.reset_counters()
    token_a = h.get_auth_token()
    headers_a = h.auth_headers(token_a)

    # Create a thread for testing
    r = requests.post(f"{h.BASE_URL}/api/threads", headers=headers_a, json={"title": "Messages Test"})
    thread_id = r.json().get("id") if r.status_code == 200 else None

    try:
        h.section("Messages - Empty Thread")
        if thread_id:
            r = requests.get(f"{h.BASE_URL}/api/threads/{thread_id}/messages", headers=headers_a)
            h.test("List messages on empty thread returns 200", r.status_code == 200, f"status={r.status_code}")
            msgs = r.json() if r.status_code == 200 else None
            h.test("Empty thread has no messages", isinstance(msgs, list) and len(msgs) == 0, str(msgs))
        else:
            h.test("List messages on empty thread returns 200", False, "no thread")
            h.test("Empty thread has no messages", False, "no thread")

        h.section("Messages - Send + SSE Stream")
        if thread_id:
            result = h.stream_sse(token_a, thread_id, "What is the capital of France?")
            full_text, status, has_tokens, has_done = result

            h.test("Send message returns SSE 200", status == 200, f"status={status}")
            h.test("SSE has token events", has_tokens, "no token events")
            h.test("SSE has done event", has_done, "no done event")
            h.test("Response text non-empty", bool(full_text and full_text.strip()), f"text={repr(full_text[:50]) if full_text else 'None'}")

            # Verify persistence
            h.section("Messages - Persistence")
            r = requests.get(f"{h.BASE_URL}/api/threads/{thread_id}/messages", headers=headers_a)
            msgs = r.json() if r.status_code == 200 else []
            user_msgs = [m for m in msgs if m["role"] == "user"]
            asst_msgs = [m for m in msgs if m["role"] == "assistant"]

            h.test("User message persisted", len(user_msgs) >= 1, f"found {len(user_msgs)}")
            h.test("Assistant message persisted", len(asst_msgs) >= 1 and bool(asst_msgs[0].get("content")), f"found {len(asst_msgs)}")
            if len(msgs) >= 2:
                h.test("Messages ordered ASC (user before assistant)", msgs[0]["role"] == "user", f"first={msgs[0]['role']}")
            else:
                h.test("Messages ordered ASC (user before assistant)", False, f"only {len(msgs)} messages")
        else:
            for name in ["Send message returns SSE 200", "SSE has token events", "SSE has done event",
                         "Response text non-empty", "User message persisted", "Assistant message persisted",
                         "Messages ordered ASC (user before assistant)"]:
                h.test(name, False, "no thread")

        h.section("Messages - Cross-User Isolation")
        token_b = h.get_auth_token(h.TEST_USER_B["email"], h.TEST_USER_B["password"])
        headers_b = h.auth_headers(token_b)
        if thread_id:
            r = requests.get(f"{h.BASE_URL}/api/threads/{thread_id}/messages", headers=headers_b)
            h.test("Non-owned thread messages returns 404/500", r.status_code in (404, 500), f"status={r.status_code}")
        else:
            h.test("Non-owned thread messages returns 404", False, "no thread")

    finally:
        if thread_id:
            requests.delete(f"{h.BASE_URL}/api/threads/{thread_id}", headers=headers_a)

    return h.passed, h.failed


if __name__ == "__main__":
    run()
    sys.exit(h.summary())
