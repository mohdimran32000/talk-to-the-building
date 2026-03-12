"""File upload, ingestion polling, delete, and record manager dedup tests."""
import sys
import os
import requests

sys.path.insert(0, os.path.dirname(__file__))
import test_helpers as h

FAKE_ID = "00000000-0000-0000-0000-000000000000"

CAPYBARA_TEXT = b"""The capybara (Hydrochoerus hydrochaeris) is the largest living rodent in the world.
Native to South America, capybaras are semi-aquatic mammals that inhabit savannas and dense forests.
They live near bodies of water and are excellent swimmers, able to stay submerged for up to five minutes.
Adult capybaras can weigh between 35 to 66 kilograms and measure up to 134 centimeters in length.
Capybaras are highly social animals, typically living in groups of 10 to 20 individuals.
They are herbivores, feeding mainly on grasses and aquatic plants.
Capybaras have a lifespan of 8 to 10 years in the wild."""


def run():
    h.reset_counters()
    token = h.get_auth_token()
    headers = h.auth_headers(token)

    doc_id = None
    dedup_doc_id = None

    try:
        h.section("File Upload")
        r = requests.post(
            f"{h.BASE_URL}/api/files/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("capybara_facts.txt", CAPYBARA_TEXT, "text/plain")},
        )
        h.test("Upload txt returns 200", r.status_code == 200, f"status={r.status_code}")
        doc = r.json() if r.status_code == 200 else {}
        doc_id = doc.get("id")

        h.test("Upload returns status=pending", doc.get("status") == "pending", str(doc.get("status")))
        h.test("Correct file_name", doc.get("file_name") == "capybara_facts.txt", str(doc.get("file_name")))
        h.test("Correct file_size", doc.get("file_size") == len(CAPYBARA_TEXT), f"{doc.get('file_size')} vs {len(CAPYBARA_TEXT)}")
        h.test("Correct mime_type", "text" in str(doc.get("mime_type", "")), str(doc.get("mime_type")))

        required_fields = ["id", "user_id", "file_name", "file_size", "mime_type", "status", "created_at", "updated_at"]
        h.test("All schema fields present", all(f in doc for f in required_fields), str(doc.keys()))

        # Poll until ready
        h.section("File Ingestion")
        if doc_id:
            final_status, error_msg = h.poll_document_status(token, doc_id, "ready", max_wait=30)
            detail = f"status={final_status}"
            if error_msg:
                detail += f", error={error_msg}"
            h.test("Document reaches ready status", final_status == "ready", detail)
        else:
            h.test("Document reaches ready status", False, "no doc_id")

        # List files
        h.section("File List")
        r = requests.get(f"{h.BASE_URL}/api/files", headers=headers)
        h.test("List files returns 200", r.status_code == 200, f"status={r.status_code}")
        files = r.json() if r.status_code == 200 else []
        ids_in_list = [f["id"] for f in files]
        h.test("List contains uploaded doc", doc_id in ids_in_list, f"looking for {doc_id}")

        # Delete file
        h.section("File Delete")
        if doc_id:
            r = requests.delete(f"{h.BASE_URL}/api/files/{doc_id}", headers=headers)
            h.test("Delete returns 200", r.status_code == 200, f"status={r.status_code}")
            del_data = r.json() if r.status_code == 200 else {}
            h.test("Delete returns status=deleted", del_data.get("status") == "deleted", str(del_data))

            r = requests.get(f"{h.BASE_URL}/api/files", headers=headers)
            files_after = r.json() if r.status_code == 200 else []
            ids_after = [f["id"] for f in files_after]
            h.test("Deleted file not in list", doc_id not in ids_after, str(ids_after))
            doc_id = None  # Already deleted
        else:
            h.test("Delete returns 200", False, "no doc_id")
            h.test("Delete returns status=deleted", False, "no doc_id")
            h.test("Deleted file not in list", False, "no doc_id")

        # Delete non-existent
        r = requests.delete(f"{h.BASE_URL}/api/files/{FAKE_ID}", headers=headers)
        h.test("Delete non-existent returns 404", r.status_code == 404, f"status={r.status_code}")

        # Upload with no file
        r = requests.post(
            f"{h.BASE_URL}/api/files/upload",
            headers={"Authorization": f"Bearer {token}"},
        )
        h.test("Upload with no file returns 422", r.status_code == 422, f"status={r.status_code}")

        # ── Record Manager — Deduplication ──

        h.section("Record Manager — Deduplication")

        # Upload a file
        r = requests.post(
            f"{h.BASE_URL}/api/files/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("dedup_test.txt", b"Hello world content", "text/plain")},
        )
        h.test("Dedup: first upload returns 200", r.status_code == 200, f"status={r.status_code}")
        first_doc = r.json() if r.status_code == 200 else {}
        dedup_doc_id = first_doc.get("id")
        h.test("Dedup: action is created", first_doc.get("action") == "created", str(first_doc.get("action")))

        # Wait for ingestion
        if dedup_doc_id:
            h.poll_document_status(token, dedup_doc_id, "ready", max_wait=15)

        # Upload same file again (identical content)
        r = requests.post(
            f"{h.BASE_URL}/api/files/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("dedup_test.txt", b"Hello world content", "text/plain")},
        )
        h.test("Dedup: duplicate upload returns 200", r.status_code == 200, f"status={r.status_code}")
        skip_doc = r.json() if r.status_code == 200 else {}
        h.test("Dedup: action is skipped", skip_doc.get("action") == "skipped", str(skip_doc.get("action")))
        h.test("Dedup: same document ID", skip_doc.get("id") == dedup_doc_id, f"{skip_doc.get('id')} vs {dedup_doc_id}")

        # Upload with same name but different content
        r = requests.post(
            f"{h.BASE_URL}/api/files/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("dedup_test.txt", b"Updated content here", "text/plain")},
        )
        h.test("Dedup: update upload returns 200", r.status_code == 200, f"status={r.status_code}")
        update_doc = r.json() if r.status_code == 200 else {}
        h.test("Dedup: action is updated", update_doc.get("action") == "updated", str(update_doc.get("action")))
        h.test("Dedup: same document ID on update", update_doc.get("id") == dedup_doc_id, f"{update_doc.get('id')} vs {dedup_doc_id}")

        # Wait for re-ingestion
        if dedup_doc_id:
            h.poll_document_status(token, dedup_doc_id, "ready", max_wait=15)

    finally:
        if doc_id:
            requests.delete(f"{h.BASE_URL}/api/files/{doc_id}", headers=headers)
        if dedup_doc_id:
            requests.delete(f"{h.BASE_URL}/api/files/{dedup_doc_id}", headers=headers)

    return h.passed, h.failed


if __name__ == "__main__":
    run()
    sys.exit(h.summary())
