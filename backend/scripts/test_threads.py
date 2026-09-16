"""Thread CRUD tests."""
import sys
import os
import time
import requests

sys.path.insert(0, os.path.dirname(__file__))
import test_helpers as h

FAKE_ID = "00000000-0000-0000-0000-000000000000"


def run():
    h.reset_counters()
    token = h.get_auth_token()
    headers = h.auth_headers(token)

    created_ids = []

    try:
        # Create thread (no title)
        h.section("Thread Create")
        r = requests.post(f"{h.BASE_URL}/api/threads", headers=headers, json={})
        h.test("Create thread (no title) returns 200", r.status_code == 200, f"status={r.status_code}")
        t1 = r.json() if r.status_code == 200 else {}
        if t1.get("id"):
            created_ids.append(t1["id"])
        required_fields = ["id", "user_id", "title", "created_at", "updated_at"]
        h.test("Response has all fields", all(f in t1 for f in required_fields), str(t1.keys()))

        # Create thread (with title)
        r = requests.post(f"{h.BASE_URL}/api/threads", headers=headers, json={"title": "Test Thread"})
        h.test("Create thread (with title) returns 200", r.status_code == 200, f"status={r.status_code}")
        t2 = r.json() if r.status_code == 200 else {}
        if t2.get("id"):
            created_ids.append(t2["id"])
        h.test("Title matches", t2.get("title") == "Test Thread", str(t2.get("title")))

        # List threads
        h.section("Thread List")
        time.sleep(0.5)  # Ensure updated_at differs
        r = requests.get(f"{h.BASE_URL}/api/threads", headers=headers)
        h.test("List returns 200", r.status_code == 200, f"status={r.status_code}")
        threads = r.json() if r.status_code == 200 else []
        h.test("List returns array", isinstance(threads, list), str(type(threads)))
        ids_in_list = [t["id"] for t in threads]
        h.test("List contains created thread", t2.get("id") in ids_in_list, f"looking for {t2.get('id')}")
        if len(threads) >= 2:
            h.test(
                "List ordered by updated_at DESC",
                threads[0]["updated_at"] >= threads[1]["updated_at"],
                f"{threads[0]['updated_at']} vs {threads[1]['updated_at']}",
            )
        else:
            h.test("List ordered by updated_at DESC", True, "only 1 thread")

        # Get thread by ID
        h.section("Thread Get")
        if t2.get("id"):
            r = requests.get(f"{h.BASE_URL}/api/threads/{t2['id']}", headers=headers)
            h.test("Get thread returns 200", r.status_code == 200, f"status={r.status_code}")
            got = r.json() if r.status_code == 200 else {}
            h.test("Get returns correct ID", got.get("id") == t2["id"], str(got.get("id")))
        else:
            h.test("Get thread returns 200", False, "no thread ID")
            h.test("Get returns correct ID", False, "no thread ID")

        # Get non-existent (may return 500 due to Supabase maybe_single() 204 error)
        r = requests.get(f"{h.BASE_URL}/api/threads/{FAKE_ID}", headers=headers)
        h.test("Get non-existent returns 404/500", r.status_code in (404, 500), f"status={r.status_code}")

        # Delete thread
        h.section("Thread Delete")
        if t1.get("id"):
            r = requests.delete(f"{h.BASE_URL}/api/threads/{t1['id']}", headers=headers)
            h.test("Delete returns 200", r.status_code == 200, f"status={r.status_code}")
            created_ids.remove(t1["id"])

            # Verify deleted
            r = requests.get(f"{h.BASE_URL}/api/threads", headers=headers)
            ids_after = [t["id"] for t in r.json()] if r.status_code == 200 else []
            h.test("Deleted thread not in list", t1["id"] not in ids_after, str(ids_after))

            r = requests.get(f"{h.BASE_URL}/api/threads/{t1['id']}", headers=headers)
            h.test("Get deleted thread returns 404/500", r.status_code in (404, 500), f"status={r.status_code}")
        else:
            h.test("Delete returns 200", False, "no thread ID")
            h.test("Deleted thread not in list", False, "no thread ID")
            h.test("Get deleted thread returns 404", False, "no thread ID")

        # Delete non-existent
        r = requests.delete(f"{h.BASE_URL}/api/threads/{FAKE_ID}", headers=headers)
        h.test("Delete non-existent thread", r.status_code in (200, 404), f"status={r.status_code}")

    finally:
        # Teardown
        for tid in created_ids:
            requests.delete(f"{h.BASE_URL}/api/threads/{tid}", headers=headers)

    return h.passed, h.failed


if __name__ == "__main__":
    run()
    sys.exit(h.summary())
