"""Ingest the 8 O&M manuals for the doc-QA audit (DOC_QA_PLAYBOOK.md).

Uploads via the backend's own /api/files/upload endpoint as the admin eval
user (the structured_data owner, so routing traps can hit SQL and docs for
the same account). Record-manager dedup makes re-runs idempotent. Skips the
two Load Schedule .md files — that data already lives in the SQL tool.

Usage: cd backend && venv/Scripts/python scripts/ingest_om_manuals.py
Requires backend running on :8001.
"""
import os
import sys

import requests
from dotenv import load_dotenv
from supabase import create_client

sys.path.insert(0, os.path.dirname(__file__))
from test_helpers import BASE_URL, TEST_USER_ADMIN, auth_headers, get_auth_token, poll_document_status

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

MANUALS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "Ref Screenshots", "Final O&M's")

MANUALS = [
    "ACS.md",
    "CCTV HWUD MAIN CAMPUS.md",
    "HWUD Main Campus BMS O&M.md",
    "HWUD Main Campus UPS O&M.md",
    "Hydro Ziptaps.md",
    "LV SWITCHGEAR & DISTRIBUTION BOARDS 201F20007-KMEP-OMM-EL-001 Final Submission 2.md",
    "Sanitary Accessories.md",
    "Water Heaters.md",
]


def main():
    token = get_auth_token(TEST_USER_ADMIN["email"], TEST_USER_ADMIN["password"])
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

    existing = {d["file_name"]: d for d in
                requests.get(f"{BASE_URL}/api/files", headers=auth_headers(token)).json()}

    failures = []
    for name in MANUALS:
        path = os.path.join(MANUALS_DIR, name)
        if not os.path.exists(path):
            failures.append(f"{name}: source file missing at {path}")
            continue

        if existing.get(name, {}).get("status") == "ready":
            print(f"[SKIP] {name} — already ready (dedup)")
            doc_id = existing[name]["id"]
        else:
            with open(path, "rb") as f:
                resp = requests.post(
                    f"{BASE_URL}/api/files/upload",
                    headers={"Authorization": f"Bearer {token}"},
                    files={"file": (name, f, "text/markdown")},
                )
            if resp.status_code != 200:
                failures.append(f"{name}: upload failed {resp.status_code} {resp.text[:200]}")
                continue
            doc_id = resp.json()["id"]
            status, err = poll_document_status(token, doc_id, target="ready", max_wait=180)
            if status != "ready":
                failures.append(f"{name}: status={status} error={err}")
                continue
            print(f"[OK]   {name} — ingested, id={doc_id[:8]}")

        # Verify chunks exist (service-role read, count only)
        n = sb.table("document_chunks").select("id", count="exact").eq("document_id", doc_id).execute().count
        print(f"       chunks={n}")
        if not n:
            failures.append(f"{name}: 0 chunks after ingestion")

    print()
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        sys.exit(1)
    print(f"All {len(MANUALS)} manuals ingested with chunks > 0.")


if __name__ == "__main__":
    main()
