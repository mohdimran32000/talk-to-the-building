"""RAG retrieval + memory integration tests.

Full pipeline: upload -> ingest -> query -> retrieval -> memory.
"""
import sys
import os
import requests

sys.path.insert(0, os.path.dirname(__file__))
import test_helpers as h

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
    thread_id = None
    thread2_id = None

    try:
        # Upload and ingest
        h.section("RAG - Upload & Ingest")
        r = requests.post(
            f"{h.BASE_URL}/api/files/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("capybara_facts.txt", CAPYBARA_TEXT, "text/plain")},
        )
        doc = r.json() if r.status_code == 200 else {}
        doc_id = doc.get("id")
        final_status, error_msg = h.poll_document_status(token, doc_id, "ready", max_wait=30) if doc_id else ("failed", "no doc_id")
        detail = f"status={final_status}"
        if error_msg:
            detail += f", error={error_msg}"
        h.test("Document ingested and ready", final_status == "ready", detail)

        # Chat with retrieval
        h.section("RAG - Retrieval")
        r = requests.post(f"{h.BASE_URL}/api/threads", headers=headers, json={"title": "RAG Test"})
        thread_id = r.json().get("id") if r.status_code == 200 else None

        if thread_id:
            result = h.stream_sse(token, thread_id, "What is a capybara and where does it live?")
            full_text, status, _, _ = result

            h.test("Chat streams successfully", status == 200 and bool(full_text), f"status={status}, text_len={len(full_text) if full_text else 0}")
            h.test(
                "Response references document content",
                full_text and ("capybara" in full_text.lower() or "rodent" in full_text.lower()),
                full_text[:200] if full_text else "empty",
            )

            # Verify persistence
            r = requests.get(f"{h.BASE_URL}/api/threads/{thread_id}/messages", headers=headers)
            msgs = r.json() if r.status_code == 200 else []
            user_msgs = [m for m in msgs if m["role"] == "user"]
            asst_msgs = [m for m in msgs if m["role"] == "assistant"]
            h.test("User message persisted", len(user_msgs) >= 1, f"found {len(user_msgs)}")
            h.test("Assistant message persisted", len(asst_msgs) >= 1 and bool(asst_msgs[0].get("content")), f"found {len(asst_msgs)}")

            # Memory test
            h.section("RAG - Memory")
            result2 = h.stream_sse(token, thread_id, "What did I just ask you about?")
            followup_text = result2[0] if result2 else None
            h.test(
                "Follow-up remembers context",
                followup_text and any(
                    w in followup_text.lower() for w in ["capybara", "rodent", "animal", "south america"]
                ),
                followup_text[:200] if followup_text else "empty",
            )

            # Thread isolation — conversation history is per-thread (not retrieval)
            # A new thread should not know what was discussed in the previous thread
            h.section("RAG - Thread Isolation (Chat History)")
            r = requests.post(f"{h.BASE_URL}/api/threads", headers=headers, json={"title": "Isolation Test"})
            thread2_id = r.json().get("id") if r.status_code == 200 else None
            if thread2_id:
                # Ask about something unrelated — the model should NOT reference capybara questions
                result3 = h.stream_sse(token, thread2_id, "Repeat back my previous message in this conversation exactly.")
                iso_text = result3[0] if result3 else ""
                # In a new thread there is no previous message, so the response should not contain the capybara question
                h.test(
                    "New thread has no prior conversation context",
                    not iso_text or "what is a capybara" not in iso_text.lower(),
                    iso_text[:200] if iso_text else "empty",
                )
            else:
                h.test("New thread has no prior conversation context", False, "could not create second thread")
        else:
            for name in ["Chat streams successfully", "Response references document content",
                         "User message persisted", "Assistant message persisted",
                         "Follow-up remembers context", "Different thread has no shared memory"]:
                h.test(name, False, "no thread")

        # Chat without documents
        h.section("RAG - Chat Without Documents")
        if doc_id:
            requests.delete(f"{h.BASE_URL}/api/files/{doc_id}", headers=headers)
            doc_id = None

        test_thread_r = requests.post(f"{h.BASE_URL}/api/threads", headers=headers, json={"title": "No Docs Test"})
        test_thread_id = test_thread_r.json().get("id") if test_thread_r.status_code == 200 else thread_id
        if test_thread_id:
            result4 = h.stream_sse(token, test_thread_id, "What is 2+2?")
            no_doc_text = result4[0] if result4 else ""
            h.test(
                "Chat works without documents",
                no_doc_text and "4" in no_doc_text,
                no_doc_text[:200] if no_doc_text else "empty",
            )
            if test_thread_id != thread_id and test_thread_id != thread2_id:
                requests.delete(f"{h.BASE_URL}/api/threads/{test_thread_id}", headers=headers)
        else:
            h.test("Chat works without documents", False, "no thread")

    finally:
        if doc_id:
            requests.delete(f"{h.BASE_URL}/api/files/{doc_id}", headers=headers)
        if thread_id:
            requests.delete(f"{h.BASE_URL}/api/threads/{thread_id}", headers=headers)
        if thread2_id:
            requests.delete(f"{h.BASE_URL}/api/threads/{thread2_id}", headers=headers)

    return h.passed, h.failed


if __name__ == "__main__":
    run()
    sys.exit(h.summary())
