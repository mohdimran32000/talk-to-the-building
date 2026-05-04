"""Shared test helpers for the validation suite.

Provides: auth, SSE parsing, polling, cleanup, and test runner utilities.
"""
import os
import sys
import json
import time
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8001")
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]

TEST_USER_A = {"email": "testuser@example.com", "password": "testpassword123"}
TEST_USER_B = {"email": "test@test.com", "password": "supabase123"}

# Admin fixture for two-scope RLS tests (added in Phase 1 plan 08).
# REQUIRES one-time setup: after creating this user via Supabase Auth, run
#   UPDATE public.profiles SET is_admin = true WHERE email = 'admin@test.com';
# in the Supabase SQL editor. Password may be overridden via TEST_USER_ADMIN_PASSWORD
# env var; defaults to the value below for local-dev convenience.
TEST_USER_ADMIN = {
    "email": "admin@test.com",
    "password": os.environ.get("TEST_USER_ADMIN_PASSWORD", "adminpassword123"),
}

_token_cache = {}  # Cache tokens to avoid Supabase rate limits

passed = 0
failed = 0

# Track resource IDs created during test runs for scoped cleanup
_created_thread_ids = []
_created_file_ids = []


def clear_token_cache():
    """Force re-authentication on next get_auth_token call."""
    _token_cache.clear()


def reset_counters():
    global passed, failed
    passed = 0
    failed = 0


def test(name, condition, detail=""):
    global passed, failed
    if condition:
        print(f"  PASS: {name}")
        passed += 1
    else:
        print(f"  FAIL: {name} -- {detail}")
        failed += 1


def section(title):
    print(f"\n[{title}]")


def get_auth_token(email=None, password=None):
    email = email or TEST_USER_A["email"]
    password = password or TEST_USER_A["password"]
    cache_key = email
    if cache_key in _token_cache:
        return _token_cache[cache_key]
    resp = requests.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        json={"email": email, "password": password},
    )
    if resp.status_code != 200:
        signup_resp = requests.post(
            f"{SUPABASE_URL}/auth/v1/signup",
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": email, "password": password},
        )
        if signup_resp.status_code not in (200, 201):
            print(f"  ERROR: Could not sign up {email}: {signup_resp.text}")
            sys.exit(1)
        resp = requests.post(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": email, "password": password},
        )
    if resp.status_code != 200:
        print(f"  ERROR: Auth failed for {email}: {resp.text}")
        sys.exit(1)
    token = resp.json()["access_token"]
    _token_cache[cache_key] = token
    return token


def get_admin_token() -> str:
    """Return JWT for the admin test user (TEST_USER_ADMIN).

    Wraps get_auth_token() so the admin token participates in the same _token_cache
    as TEST_USER_A/B tokens. The admin user MUST have been promoted to is_admin=true
    in public.profiles before this is called — see test_two_scope_rls.py docstring
    for the one-time UPDATE SQL.
    """
    return get_auth_token(TEST_USER_ADMIN["email"], TEST_USER_ADMIN["password"])


def get_user_supabase_client(jwt_token: str):
    """Return a supabase-py Client authenticated as the JWT's user — RLS applies.

    Critical: uses SUPABASE_ANON_KEY (NOT service-role). Service-role bypasses
    RLS and silently passes broken tests. PostgREST honors the Authorization
    header for RLS evaluation; we set it via client.postgrest.auth(jwt_token).

    Used by test_two_scope_rls.py to talk directly to Supabase (bypassing the
    FastAPI backend, which has no folders router until Phase 3). Every direct
    DB write/read in that test goes through a client returned by this helper.

    Args:
        jwt_token: A valid Supabase JWT for the user the client should impersonate.
                   Get one via get_auth_token() or get_admin_token().

    Returns:
        supabase.Client with postgrest.auth(jwt_token) called.
    """
    from supabase import create_client
    client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    client.postgrest.auth(jwt_token)
    return client


def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def stream_sse(token, thread_id, content, timeout=60):
    """Send a message and collect streamed response tokens."""
    resp = requests.post(
        f"{BASE_URL}/api/threads/{thread_id}/messages",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"content": content},
        stream=True,
        timeout=timeout,
    )
    if resp.status_code != 200:
        return None, resp.status_code

    full_text = ""
    has_token_event = False
    has_done_event = False
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
            if event.get("type") == "token":
                full_text += event.get("content", "")
                has_token_event = True
            elif event.get("type") == "done":
                has_done_event = True
                break
        except json.JSONDecodeError:
            pass
    return full_text, resp.status_code, has_token_event, has_done_event


def poll_document_status(token, doc_id, target="ready", max_wait=30):
    """Poll GET /api/files until document reaches target status or timeout."""
    deadline = time.time() + max_wait
    final_status = "pending"
    error_message = None
    while time.time() < deadline:
        r = requests.get(f"{BASE_URL}/api/files", headers=auth_headers(token))
        docs = r.json() if r.status_code == 200 else []
        match = next((d for d in docs if d["id"] == doc_id), None)
        if match:
            final_status = match["status"]
            error_message = match.get("error_message")
            if final_status in (target, "failed"):
                break
        time.sleep(2)
    return final_status, error_message


def track_thread(thread_id):
    """Register a thread ID for scoped cleanup."""
    _created_thread_ids.append(thread_id)


def track_file(file_id):
    """Register a file ID for scoped cleanup."""
    _created_file_ids.append(file_id)


def cleanup_threads(token):
    """Delete only threads created during this test run."""
    for tid in _created_thread_ids:
        try:
            requests.delete(f"{BASE_URL}/api/threads/{tid}", headers=auth_headers(token))
        except Exception:
            pass
    _created_thread_ids.clear()


def cleanup_files(token):
    """Delete only files created during this test run."""
    for fid in _created_file_ids:
        try:
            requests.delete(f"{BASE_URL}/api/files/{fid}", headers=auth_headers(token))
        except Exception:
            pass
    _created_file_ids.clear()


def summary():
    """Print results and return exit code."""
    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed == 0:
        print("All tests passed!")
    else:
        print(f"{failed} test(s) failed.")
    return 0 if failed == 0 else 1
