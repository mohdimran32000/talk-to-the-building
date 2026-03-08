"""Auth rejection tests — all /api/* endpoints must reject unauthenticated requests."""
import sys
import os
import requests

sys.path.insert(0, os.path.dirname(__file__))
import test_helpers as h

FAKE_ID = "00000000-0000-0000-0000-000000000000"


def run():
    h.reset_counters()
    h.section("Auth Rejection (no token)")

    endpoints = [
        ("POST", "/api/threads", {"json": {}}),
        ("GET", "/api/threads", {}),
        ("GET", f"/api/threads/{FAKE_ID}", {}),
        ("DELETE", f"/api/threads/{FAKE_ID}", {}),
        ("GET", f"/api/threads/{FAKE_ID}/messages", {}),
        ("POST", f"/api/threads/{FAKE_ID}/messages", {"json": {"content": "test"}}),
        ("POST", "/api/files/upload", {}),
        ("GET", "/api/files", {}),
        ("DELETE", f"/api/files/{FAKE_ID}", {}),
    ]

    for method, path, kwargs in endpoints:
        try:
            r = requests.request(method, f"{h.BASE_URL}{path}", timeout=5, **kwargs)
            h.test(
                f"{method} {path} rejects no token",
                r.status_code in (401, 403, 422),
                f"status={r.status_code}",
            )
        except Exception as e:
            h.test(f"{method} {path} rejects no token", False, str(e))

    h.section("Auth Rejection (invalid token)")
    try:
        r = requests.get(
            f"{h.BASE_URL}/api/threads",
            headers={"Authorization": "Bearer invalid-token-xxx"},
            timeout=5,
        )
        h.test("Invalid token rejected", r.status_code in (401, 403), f"status={r.status_code}")
    except Exception as e:
        h.test("Invalid token rejected", False, str(e))

    return h.passed, h.failed


if __name__ == "__main__":
    run()
    sys.exit(h.summary())
