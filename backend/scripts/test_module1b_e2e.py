"""E2E tests for Module 1b: Managed RAG (Gemini File Search) endpoints.

Tests: auth, upload, list, delete, auth rejection.
Requires: backend running on localhost:8000, SUPABASE_URL/SUPABASE_ANON_KEY env vars.
"""
import os
import sys
import json
import requests

# Load env vars from backend .env
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

BASE_URL = "http://localhost:8000"
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]

# Test user credentials (must exist in Supabase - created during Module 1 E2E)
TEST_EMAIL = "testuser@example.com"
TEST_PASSWORD = "testpassword123"

passed = 0
failed = 0


def test(name, condition, detail=""):
    global passed, failed
    if condition:
        print(f"  PASS: {name}")
        passed += 1
    else:
        print(f"  FAIL: {name} — {detail}")
        failed += 1


def get_auth_token():
    """Sign in via Supabase Auth REST API and return access token."""
    resp = requests.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        headers={
            "apikey": SUPABASE_ANON_KEY,
            "Content-Type": "application/json",
        },
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
    )
    if resp.status_code != 200:
        # Try signing up first
        signup_resp = requests.post(
            f"{SUPABASE_URL}/auth/v1/signup",
            headers={
                "apikey": SUPABASE_ANON_KEY,
                "Content-Type": "application/json",
            },
            json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
        )
        if signup_resp.status_code not in (200, 201):
            print(f"Signup failed: {signup_resp.status_code} {signup_resp.text}")
            sys.exit(1)
        # Sign in again
        resp = requests.post(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
            headers={
                "apikey": SUPABASE_ANON_KEY,
                "Content-Type": "application/json",
            },
            json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
        )
    data = resp.json()
    return data["access_token"]


# ── Test 1: Health check ──
print("\n=== Module 1b E2E Tests ===\n")
print("[Health]")
r = requests.get(f"{BASE_URL}/health")
test("Health endpoint returns 200", r.status_code == 200)
test("Health returns ok", r.json().get("status") == "ok")

# ── Test 2: Auth rejection (no token) ──
print("\n[Auth Rejection]")
r = requests.get(f"{BASE_URL}/api/files")
test("List files without token returns 401/403", r.status_code in (401, 403), f"got {r.status_code}")

r = requests.post(f"{BASE_URL}/api/files/upload")
test("Upload without token returns 401/403", r.status_code in (401, 403, 422), f"got {r.status_code}")

r = requests.delete(f"{BASE_URL}/api/files/fake-id")
test("Delete without token returns 401/403", r.status_code in (401, 403), f"got {r.status_code}")

# ── Test 3: Auth with valid token ──
print("\n[Auth + List Files]")
token = get_auth_token()
headers = {"Authorization": f"Bearer {token}"}
test("Got auth token", bool(token))

r = requests.get(f"{BASE_URL}/api/files", headers=headers)
test("List files returns 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
test("List files returns array", isinstance(r.json(), list), f"got {type(r.json())}")

# ── Test 4: Upload a test file ──
print("\n[File Upload]")
test_content = b"This is a test document for RAG file search. It contains some sample text about machine learning and AI."
files_payload = {"file": ("test_doc.txt", test_content, "text/plain")}
r = requests.post(f"{BASE_URL}/api/files/upload", headers=headers, files=files_payload)
test("Upload returns 200", r.status_code == 200, f"got {r.status_code}: {r.text[:300]}")

uploaded_file = None
if r.status_code == 200:
    uploaded_file = r.json()
    test("Upload returns file_name", uploaded_file.get("file_name") == "test_doc.txt", f"got {uploaded_file.get('file_name')}")
    test("Upload returns status ready", uploaded_file.get("status") == "ready", f"got {uploaded_file.get('status')}")
    test("Upload returns gemini_file_name", bool(uploaded_file.get("gemini_file_name")), "missing gemini_file_name")
    test("Upload returns file_size", uploaded_file.get("file_size") == len(test_content), f"got {uploaded_file.get('file_size')}")

# ── Test 5: List files shows uploaded file ──
print("\n[List After Upload]")
r = requests.get(f"{BASE_URL}/api/files", headers=headers)
test("List files returns 200", r.status_code == 200)
files_list = r.json()
test("List includes uploaded file", any(f.get("file_name") == "test_doc.txt" for f in files_list), f"got {len(files_list)} files")

# ── Test 6: Delete the uploaded file ──
if uploaded_file:
    print("\n[File Delete]")
    file_id = uploaded_file["id"]
    r = requests.delete(f"{BASE_URL}/api/files/{file_id}", headers=headers)
    test("Delete returns 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
    test("Delete returns deleted status", r.json().get("status") == "deleted", f"got {r.json()}")

    # Verify deletion
    r = requests.get(f"{BASE_URL}/api/files", headers=headers)
    files_after = r.json()
    test("File no longer in list after delete",
         not any(f.get("id") == file_id for f in files_after),
         f"file still present")

# ── Test 7: Delete non-existent file ──
print("\n[Delete Non-Existent]")
r = requests.delete(f"{BASE_URL}/api/files/00000000-0000-0000-0000-000000000000", headers=headers)
test("Delete non-existent returns 404", r.status_code == 404, f"got {r.status_code}")

# ── Summary ──
print(f"\n{'='*40}")
print(f"Results: {passed} passed, {failed} failed out of {passed + failed} tests")
print(f"{'='*40}")
sys.exit(0 if failed == 0 else 1)
