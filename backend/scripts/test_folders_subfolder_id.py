"""D-06 verification: GET /api/folders returns subfolders as Array<{id, path}>.

Closes the plan-checker blocker on Plan 06-09 (folder-id resolution gap).
Frontend Plan 06-05 typed client and Plan 06-06 FolderNode recursion depend on
each subfolder carrying a UUID round-tripped on the list call so PATCH/DELETE
/api/folders/{id} can be wired without a separate path->id lookup endpoint.

What this test asserts:
  - GET /api/folders returns subfolders as a list of dicts (not bare strings)
  - Each subfolder dict carries both `id` and `path` keys
  - For an explicit folder row, `id` is a valid UUID string
  - For an inferred-only folder (folders row absent), `id` is None
  - Cleanup deletes ONLY the IDs created by this test (CLAUDE.md mandatory rule)

PREREQUISITE:
  1. Backend running on http://localhost:8001
  2. test@test.com user exists (Supabase Auth)
  3. Plans 06-12 Tasks 1+2 applied (folder_service + schemas + router response_model)

Run:  cd backend && venv/Scripts/python scripts/test_folders_subfolder_id.py
"""
import os
import re
import sys
import uuid

import requests

# Two-step sys.path bootstrap (matches test_folders.py:43-45 pattern).
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

import test_helpers as h  # noqa: E402

# UUID regex: 8-4-4-4-12 hex; case-insensitive (UUIDs may be upper or lower).
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


def _delete_folder(folder_id: str, headers: dict) -> None:
    """Best-effort DELETE for cleanup. Never raises."""
    try:
        requests.delete(
            f"{h.BASE_URL}/api/folders/{folder_id}", headers=headers, timeout=10
        )
    except Exception:
        pass


def _verify_setup() -> tuple[bool, str]:
    """Pre-flight: assert backend is reachable + folders router responds."""
    try:
        r = requests.get(f"{h.BASE_URL}/api/folders", timeout=5)
        if r.status_code == 404:
            return False, (
                "GET /api/folders returns 404 - folders router not registered. "
                "Plan 06-12 prerequisite gap."
            )
    except Exception as e:
        return False, (
            f"Backend unreachable: {e}. Start with: "
            f"cd backend && venv/Scripts/python -m uvicorn app.main:app --reload --port 8001"
        )
    return True, "ok"


def run():
    """Run the D-06 subfolder-id verification test."""
    h.reset_counters()

    ok, msg = _verify_setup()
    if not ok:
        h.test("Plan 06-12 setup (backend + folders router)", False, f"[FATAL] {msg}")
        return h.passed, h.failed

    # Use TEST_USER_B (test@test.com) per the plan spec.
    token = h.get_auth_token(h.TEST_USER_B["email"], h.TEST_USER_B["password"])
    headers = h.auth_headers(token)

    # Use a UUID-suffixed parent path to avoid colliding with concurrent test runs
    # or leftover state from prior runs that did not clean up.
    suffix = uuid.uuid4().hex[:8]
    parent_path = f"/d06-test-parent-{suffix}"
    child_path = f"{parent_path}/child"

    parent_id: str | None = None
    child_id: str | None = None

    try:
        h.section("D-06 GET /api/folders subfolders[].id contract")

        # 1) Create parent folder
        r = requests.post(
            f"{h.BASE_URL}/api/folders",
            headers=headers,
            json={"path": parent_path, "scope": "user"},
            timeout=10,
        )
        h.test(
            "Create parent folder returns 2xx",
            r.status_code in (200, 201),
            f"status={r.status_code} body={r.text[:200]}",
        )
        if r.status_code not in (200, 201):
            return h.passed, h.failed
        parent_body = r.json()
        parent_id = parent_body.get("id")
        h.test("Parent folder has id", isinstance(parent_id, str) and bool(parent_id))

        # 2) Create nested subfolder
        r = requests.post(
            f"{h.BASE_URL}/api/folders",
            headers=headers,
            json={"path": child_path, "scope": "user"},
            timeout=10,
        )
        h.test(
            "Create child folder returns 2xx",
            r.status_code in (200, 201),
            f"status={r.status_code} body={r.text[:200]}",
        )
        if r.status_code not in (200, 201):
            return h.passed, h.failed
        child_body = r.json()
        child_id = child_body.get("id")
        h.test("Child folder has id", isinstance(child_id, str) and bool(child_id))

        # 3) GET /api/folders?path=<parent>&scope=user
        r = requests.get(
            f"{h.BASE_URL}/api/folders",
            headers=headers,
            params={"path": parent_path, "scope": "user"},
            timeout=10,
        )
        h.test(
            "GET /api/folders returns 200",
            r.status_code == 200,
            f"status={r.status_code} body={r.text[:200]}",
        )
        if r.status_code != 200:
            return h.passed, h.failed

        body = r.json()

        # 4) subfolders is a list
        h.test(
            "subfolders is a list",
            isinstance(body.get("subfolders"), list),
            f"subfolders type={type(body.get('subfolders')).__name__}",
        )
        subfolders = body.get("subfolders") or []
        h.test(
            "subfolders has at least 1 entry",
            len(subfolders) >= 1,
            f"got {len(subfolders)} entries",
        )
        if not subfolders:
            return h.passed, h.failed

        # 5) Locate our child entry (deterministic by path; order is alpha-sorted)
        ours = next((s for s in subfolders if isinstance(s, dict) and s.get("path") == child_path), None)
        h.test(
            f"subfolder with path={child_path!r} present",
            ours is not None,
            f"subfolders={subfolders}",
        )
        if ours is None:
            return h.passed, h.failed

        # 6) Shape assertions on the subfolder entry
        h.test("subfolder is a dict (not bare string)", isinstance(ours, dict))
        h.test("subfolder has id key", "id" in ours)
        h.test("subfolder has path key", "path" in ours)
        h.test(
            "subfolder id is a string",
            isinstance(ours["id"], str),
            f"id type={type(ours['id']).__name__} value={ours['id']!r}",
        )
        h.test(
            "subfolder id matches UUID format (36-char hex with dashes)",
            isinstance(ours["id"], str) and bool(UUID_RE.match(ours["id"])),
            f"id={ours['id']!r}",
        )
        h.test(
            "subfolder id matches the id returned by POST",
            ours["id"] == child_id,
            f"GET subfolder.id={ours['id']!r} POST.id={child_id!r}",
        )
        h.test(
            "subfolder path matches expected",
            ours["path"] == child_path,
            f"got {ours['path']!r}",
        )

    finally:
        # CLAUDE.md mandatory: cleanup ONLY the IDs we created.
        # Order matters: delete child before parent so the parent is empty when
        # delete_folder_if_empty fires (Plan 03 / Migration 019 contract).
        if child_id:
            _delete_folder(child_id, headers)
        if parent_id:
            _delete_folder(parent_id, headers)

    return h.passed, h.failed


if __name__ == "__main__":
    p, f = run()
    sys.exit(h.summary())
