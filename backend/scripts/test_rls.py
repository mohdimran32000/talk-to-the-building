"""Row-Level Security isolation tests — two users cannot see each other's data."""
import sys
import os
import requests

sys.path.insert(0, os.path.dirname(__file__))
import test_helpers as h

CAPYBARA_TEXT = b"""The capybara is the largest living rodent. Native to South America."""


def run():
    h.reset_counters()
    token_a = h.get_auth_token(h.TEST_USER_A["email"], h.TEST_USER_A["password"])
    token_b = h.get_auth_token(h.TEST_USER_B["email"], h.TEST_USER_B["password"])
    headers_a = h.auth_headers(token_a)
    headers_b = h.auth_headers(token_b)

    a_thread_id = None
    a_doc_id = None

    try:
        # User A creates a thread and uploads a file
        r = requests.post(f"{h.BASE_URL}/api/threads", headers=headers_a, json={"title": "RLS Test A"})
        a_thread_id = r.json().get("id") if r.status_code == 200 else None

        r = requests.post(
            f"{h.BASE_URL}/api/files/upload",
            headers={"Authorization": f"Bearer {token_a}"},
            files={"file": ("rls_test.txt", CAPYBARA_TEXT, "text/plain")},
        )
        a_doc = r.json() if r.status_code == 200 else {}
        a_doc_id = a_doc.get("id")

        # Wait for ingestion
        if a_doc_id:
            h.poll_document_status(token_a, a_doc_id, "ready", max_wait=30)

        # RLS: threads
        h.section("RLS - Threads")
        r = requests.get(f"{h.BASE_URL}/api/threads", headers=headers_b)
        b_thread_ids = [t["id"] for t in r.json()] if r.status_code == 200 else []
        h.test("B cannot see A's threads", a_thread_id not in b_thread_ids, str(b_thread_ids))

        if a_thread_id:
            r = requests.get(f"{h.BASE_URL}/api/threads/{a_thread_id}", headers=headers_b)
            h.test("B cannot get A's thread", r.status_code in (404, 500), f"status={r.status_code}")

            r = requests.delete(f"{h.BASE_URL}/api/threads/{a_thread_id}", headers=headers_b)
            # Verify A's thread still exists
            r2 = requests.get(f"{h.BASE_URL}/api/threads/{a_thread_id}", headers=headers_a)
            h.test("B cannot delete A's thread", r2.status_code == 200, f"status after B's delete attempt={r2.status_code}")
        else:
            h.test("B cannot get A's thread", False, "no thread created")
            h.test("B cannot delete A's thread", False, "no thread created")

        # RLS: messages
        h.section("RLS - Messages")
        if a_thread_id:
            r = requests.get(f"{h.BASE_URL}/api/threads/{a_thread_id}/messages", headers=headers_b)
            h.test("B cannot see A's messages", r.status_code in (404, 500), f"status={r.status_code}")

            r = requests.post(
                f"{h.BASE_URL}/api/threads/{a_thread_id}/messages",
                headers=headers_b,
                json={"content": "sneaky message"},
            )
            h.test("B cannot post to A's thread", r.status_code in (404, 500), f"status={r.status_code}")
        else:
            h.test("B cannot see A's messages", False, "no thread")
            h.test("B cannot post to A's thread", False, "no thread")

        # RLS: files
        h.section("RLS - Files")
        r = requests.get(f"{h.BASE_URL}/api/files", headers=headers_b)
        b_file_ids = [f["id"] for f in r.json()] if r.status_code == 200 else []
        h.test("B cannot see A's files", a_doc_id not in b_file_ids, str(b_file_ids))

        if a_doc_id:
            r = requests.delete(f"{h.BASE_URL}/api/files/{a_doc_id}", headers=headers_b)
            h.test("B cannot delete A's file", r.status_code == 404, f"status={r.status_code}")
        else:
            h.test("B cannot delete A's file", False, "no file")

        # Verify A's data intact
        h.section("RLS - A's Data Intact")
        r_threads = requests.get(f"{h.BASE_URL}/api/threads", headers=headers_a)
        a_thread_ids = [t["id"] for t in r_threads.json()] if r_threads.status_code == 200 else []
        r_files = requests.get(f"{h.BASE_URL}/api/files", headers=headers_a)
        a_file_ids = [f["id"] for f in r_files.json()] if r_files.status_code == 200 else []
        intact = (a_thread_id in a_thread_ids if a_thread_id else True) and (a_doc_id in a_file_ids if a_doc_id else True)
        h.test("A's data intact after B's attempts", intact, f"threads={a_thread_ids}, files={a_file_ids}")

    finally:
        if a_thread_id:
            requests.delete(f"{h.BASE_URL}/api/threads/{a_thread_id}", headers=headers_a)
        if a_doc_id:
            requests.delete(f"{h.BASE_URL}/api/files/{a_doc_id}", headers=headers_a)
        # User B doesn't create any persistent data in these tests

    return h.passed, h.failed


if __name__ == "__main__":
    run()
    sys.exit(h.summary())
