"""Health endpoint tests."""
import sys
import os
import requests

sys.path.insert(0, os.path.dirname(__file__))
import test_helpers as h


def run():
    h.reset_counters()
    h.section("Health Endpoint")

    try:
        r = requests.get(f"{h.BASE_URL}/health", timeout=5)
        h.test("Health returns 200", r.status_code == 200, f"status={r.status_code}")
        data = r.json() if r.status_code == 200 else {}
        h.test("Health returns status=ok", data.get("status") == "ok", str(data))
    except Exception as e:
        h.test("Health endpoint reachable", False, str(e))
        h.test("Health returns status=ok", False, "unreachable")

    return h.passed, h.failed


if __name__ == "__main__":
    run()
    sys.exit(h.summary())
