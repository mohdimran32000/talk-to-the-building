"""E2E tests for Module 2: BYO Retrieval + Memory.

Tests:
  - Health check
  - Auth (get token)
  - GET /api/files returns empty array (no Gemini fields)
  - POST /api/files/upload with a .txt file returns status=pending instantly
  - Poll until status=ready (max 30s)
  - GET /api/files lists the document
  - Create thread, send message about mitochondria → SSE streams response
  - Follow-up "What did I just ask you about?" → response references prior question (memory)
  - DELETE /api/files/{id} → 200, status=deleted
  - Verify doc not in list after delete

Requires: backend running on localhost:8000, .env with SUPABASE_URL/SUPABASE_ANON_KEY.
"""
import os
import sys
import json
import time
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]

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
    resp = requests.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
    )
    if resp.status_code != 200:
        signup_resp = requests.post(
            f"{SUPABASE_URL}/auth/v1/signup",
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
        )
        if signup_resp.status_code not in (200, 201):
            print(f"  ERROR: Could not sign up test user: {signup_resp.text}")
            sys.exit(1)
        resp = requests.post(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
        )
    if resp.status_code != 200:
        print(f"  ERROR: Auth failed: {resp.text}")
        sys.exit(1)
    return resp.json()["access_token"]


def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def stream_sse(token, thread_id, content):
    """Send a message and collect streamed response tokens."""
    resp = requests.post(
        f"{BASE_URL}/api/threads/{thread_id}/messages",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"content": content},
        stream=True,
        timeout=60,
    )
    if resp.status_code != 200:
        return None, resp.status_code

    full_text = ""
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
            elif event.get("type") == "done":
                break
        except json.JSONDecodeError:
            pass
    return full_text, resp.status_code


# ── Tests ──

print("\n=== Module 2: BYO Retrieval + Memory E2E Tests ===\n")

# 1. Health check
print("1. Health check")
try:
    r = requests.get(f"{BASE_URL}/health", timeout=5)
    test("Health endpoint returns 200", r.status_code == 200, r.text)
except Exception as e:
    test("Health endpoint reachable", False, str(e))

# 2. Auth
print("\n2. Auth")
token = get_auth_token()
test("Got access token", bool(token))

# 3. List files (should be empty or array, no Gemini fields)
print("\n3. GET /api/files — no Gemini fields")
r = requests.get(f"{BASE_URL}/api/files", headers=auth_headers(token))
test("List files returns 200", r.status_code == 200, r.text)
files_data = r.json() if r.status_code == 200 else []
test("Returns an array", isinstance(files_data, list), str(files_data))
if files_data:
    first = files_data[0]
    test("No store_id field (old schema gone)", "store_id" not in first, str(first.keys()))
    test("No gemini_file_name field (old schema gone)", "gemini_file_name" not in first, str(first.keys()))
    test("Has updated_at field (new schema)", "updated_at" in first, str(first.keys()))

# 4. Upload a text file
print("\n4. POST /api/files/upload — text file about mitochondria")
txt_content = b"""The mitochondrion is a double membrane-bound organelle found in most eukaryotic organisms.
Mitochondria generate most of the cell's supply of adenosine triphosphate (ATP), used as a source of chemical energy.
Because of this, the mitochondrion is sometimes referred to as the powerhouse of the cell.
Mitochondria also supply cellular energy through heat and play a role in thermogenesis.
They are involved in other tasks such as signaling, cellular differentiation, and cell death.
The number of mitochondria in a cell can vary widely by organism, tissue, and cell type."""

upload_resp = requests.post(
    f"{BASE_URL}/api/files/upload",
    headers={"Authorization": f"Bearer {token}"},
    files={"file": ("mitochondria.txt", txt_content, "text/plain")},
)
test("Upload returns 200", upload_resp.status_code == 200, upload_resp.text[:300])
doc = upload_resp.json() if upload_resp.status_code == 200 else {}
doc_id = doc.get("id")
test("Upload returns status=pending", doc.get("status") == "pending", str(doc.get("status")))
test("Upload has file_name", doc.get("file_name") == "mitochondria.txt", str(doc.get("file_name")))
test("No store_id in response", "store_id" not in doc, str(doc.keys()))

# 5. Poll until ready
print("\n5. Polling until document status=ready (max 30s)")
if doc_id:
    deadline = time.time() + 30
    final_status = doc.get("status", "pending")
    while time.time() < deadline:
        r = requests.get(f"{BASE_URL}/api/files", headers=auth_headers(token))
        docs = r.json() if r.status_code == 200 else []
        match = next((d for d in docs if d["id"] == doc_id), None)
        if match:
            final_status = match["status"]
            if final_status in ("ready", "failed"):
                break
        time.sleep(2)
    test("Document reached ready status", final_status == "ready", f"status={final_status}")
else:
    test("Document ID obtained for polling", False, "No doc_id from upload")

# 6. List files includes the document
print("\n6. GET /api/files — document appears in list")
r = requests.get(f"{BASE_URL}/api/files", headers=auth_headers(token))
docs = r.json() if r.status_code == 200 else []
match = next((d for d in docs if d.get("id") == doc_id), None)
test("Document in list", match is not None, f"doc_id={doc_id}")

# 7. Create thread + chat with retrieval
print("\n7. Chat with retrieval — ask about mitochondria")
thread_resp = requests.post(
    f"{BASE_URL}/api/threads",
    headers=auth_headers(token),
    json={"title": "Module 2 E2E test"},
)
test("Create thread returns 200", thread_resp.status_code == 200, thread_resp.text)
thread_id = thread_resp.json().get("id") if thread_resp.status_code == 200 else None

if thread_id:
    response_text, status = stream_sse(token, thread_id, "What is a mitochondrion and why is it called the powerhouse of the cell?")
    test("SSE stream returns 200", status == 200, f"status={status}")
    test("Response mentions mitochondria or powerhouse", response_text and (
        "mitochondr" in response_text.lower() or "powerhouse" in response_text.lower()
    ), response_text[:200] if response_text else "empty")

    # 8. Memory test — follow-up question
    print("\n8. Memory test — follow-up references prior question")
    followup_text, followup_status = stream_sse(token, thread_id, "What did I just ask you about?")
    test("Follow-up SSE returns 200", followup_status == 200, f"status={followup_status}")
    test("Follow-up references prior topic", followup_text and (
        "mitochondr" in followup_text.lower() or "powerhouse" in followup_text.lower() or "cell" in followup_text.lower()
    ), followup_text[:200] if followup_text else "empty")
else:
    test("Thread created for chat tests", False, "No thread_id")
    test("SSE stream works", False, "No thread_id")
    test("Follow-up memory test", False, "No thread_id")

# 9. Delete document
print("\n9. DELETE /api/files/{id}")
if doc_id:
    del_resp = requests.delete(
        f"{BASE_URL}/api/files/{doc_id}",
        headers=auth_headers(token),
    )
    test("Delete returns 200", del_resp.status_code == 200, del_resp.text)
    del_data = del_resp.json() if del_resp.status_code == 200 else {}
    test("Delete returns status=deleted", del_data.get("status") == "deleted", str(del_data))

    # 10. Verify not in list
    print("\n10. Verify document no longer in list")
    r = requests.get(f"{BASE_URL}/api/files", headers=auth_headers(token))
    docs = r.json() if r.status_code == 200 else []
    still_there = any(d.get("id") == doc_id for d in docs)
    test("Document removed from list", not still_there, f"doc_id={doc_id} still present")
else:
    test("Delete document", False, "No doc_id")
    test("Document removed from list", False, "No doc_id")

# ── Summary ──
print(f"\n{'='*40}")
print(f"Results: {passed} passed, {failed} failed")
if failed == 0:
    print("All tests passed!")
else:
    print(f"{failed} test(s) failed.")
    sys.exit(1)
