"""Tests for metadata extraction feature (Module 4)."""
import json
import os
import time
import requests
import test_helpers as h


def _collect_sse_text(resp):
    """Collect streamed SSE tokens into a single string."""
    text = ""
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
                text += event.get("content", "")
        except json.JSONDecodeError:
            pass
    return text


def run():
    h.reset_counters()
    h.section("Metadata Extraction")

    token_a = h.get_auth_token()

    # 1. GET /api/settings includes metadata_schema
    r = requests.get(f"{h.BASE_URL}/api/settings", headers=h.auth_headers(token_a))
    h.test("GET /api/settings -> 200", r.status_code == 200, f"status={r.status_code}")
    if r.status_code == 200:
        data = r.json()
        h.test(
            "Settings includes metadata_schema array",
            "metadata_schema" in data and isinstance(data["metadata_schema"], list) and len(data["metadata_schema"]) > 0,
            f"metadata_schema={data.get('metadata_schema')}",
        )
        h.test(
            "Schema has document_type field",
            any(f["name"] == "document_type" for f in data.get("metadata_schema", [])),
            f"schema={data.get('metadata_schema')}",
        )

    # 2. Upload a document and verify metadata extraction
    h.section("Metadata Extraction on Upload")
    test_content = (
        "Artificial Intelligence in Healthcare: A Comprehensive Report\n\n"
        "Published: 2025-01-15\n\n"
        "This technical report examines the application of artificial intelligence "
        "and machine learning techniques in modern healthcare systems. The study covers "
        "diagnostic imaging, drug discovery, and patient monitoring. Key organizations "
        "involved include the WHO, NIH, and Google DeepMind.\n\n"
        "The report concludes that AI-powered diagnostics can improve accuracy by 30% "
        "while reducing costs. Natural language processing enables better analysis of "
        "medical records and clinical notes.\n\n"
        "Keywords: AI, healthcare, machine learning, diagnostics, NLP"
    )

    # Upload as text file
    files_payload = {"file": ("ai_healthcare_report.txt", test_content.encode(), "text/plain")}
    r2 = requests.post(
        f"{h.BASE_URL}/api/files/upload",
        headers={"Authorization": f"Bearer {token_a}"},
        files=files_payload,
    )
    h.test("Upload document -> 200", r2.status_code == 200, f"status={r2.status_code}")
    doc_id = None
    if r2.status_code == 200:
        doc = r2.json()
        doc_id = doc["id"]
        h.track_file(doc_id)

    # 3. Poll until ready and verify metadata
    if doc_id:
        final_status, error_msg = h.poll_document_status(token_a, doc_id, target="ready", max_wait=60)
        h.test(
            "Document reached ready status",
            final_status == "ready",
            f"status={final_status}, error={error_msg}",
        )

        # Fetch the document to check metadata
        r3 = requests.get(f"{h.BASE_URL}/api/files", headers=h.auth_headers(token_a))
        if r3.status_code == 200:
            docs = r3.json()
            match = next((d for d in docs if d["id"] == doc_id), None)
            if match:
                metadata = match.get("metadata")
                h.test(
                    "Document has metadata",
                    metadata is not None and isinstance(metadata, dict),
                    f"metadata={metadata}",
                )
                if metadata:
                    h.test(
                        "Metadata has document_type",
                        "document_type" in metadata and metadata["document_type"] is not None,
                        f"metadata keys={list(metadata.keys())}",
                    )
                    h.test(
                        "Metadata has topic",
                        "topic" in metadata and metadata["topic"] is not None,
                        f"metadata={metadata}",
                    )
                    h.test(
                        "Metadata has summary",
                        "summary" in metadata and metadata["summary"] is not None,
                        f"metadata={metadata}",
                    )
                    h.test(
                        "Metadata has keywords as list",
                        "keywords" in metadata and isinstance(metadata.get("keywords"), list),
                        f"keywords={metadata.get('keywords')}",
                    )
            else:
                h.test("Document found in list", False, "Document not in GET /api/files")

    # 4. Test filtered retrieval via message endpoint
    h.section("Filtered Retrieval")
    if doc_id and final_status == "ready":
        # Create a thread for testing
        r4 = requests.post(
            f"{h.BASE_URL}/api/threads",
            headers=h.auth_headers(token_a),
            json={"title": "metadata-test"},
        )
        thread_id = None
        if r4.status_code == 200:
            thread_id = r4.json()["id"]
            h.track_thread(thread_id)

        if thread_id:
            # Send message WITH metadata filter (should work since doc matches)
            r5 = requests.post(
                f"{h.BASE_URL}/api/threads/{thread_id}/messages",
                headers=h.auth_headers(token_a),
                json={"content": "What does the report say about AI?", "metadata_filter": {"document_type": "report"}},
                stream=True,
                timeout=60,
            )
            h.test(
                "Message with metadata_filter -> 200",
                r5.status_code == 200,
                f"status={r5.status_code}",
            )

            # Send message with non-matching filter
            r6 = requests.post(
                f"{h.BASE_URL}/api/threads/{thread_id}/messages",
                headers=h.auth_headers(token_a),
                json={"content": "Tell me about AI", "metadata_filter": {"document_type": "nonexistent_type_xyz"}},
                stream=True,
                timeout=60,
            )
            h.test(
                "Message with non-matching filter -> 200 (no crash)",
                r6.status_code == 200,
                f"status={r6.status_code}",
            )

    # 5. Test auto-filter extraction (agentic query understanding)
    h.section("Auto-Filter Extraction")
    if doc_id and final_status == "ready":
        if not thread_id:
            r_t = requests.post(
                f"{h.BASE_URL}/api/threads",
                headers=h.auth_headers(token_a),
                json={"title": "autofilter-test"},
            )
            if r_t.status_code == 200:
                thread_id = r_t.json()["id"]
                h.track_thread(thread_id)

        if thread_id:
            # Send message WITHOUT manual filter — auto-filter should kick in
            r7 = requests.post(
                f"{h.BASE_URL}/api/threads/{thread_id}/messages",
                headers=h.auth_headers(token_a),
                json={"content": "What does the AI healthcare report say about diagnostics?"},
                stream=True,
                timeout=60,
            )
            h.test(
                "Auto-filter: message without manual filter -> 200",
                r7.status_code == 200,
                f"status={r7.status_code}",
            )
            # Verify response contains relevant content (retrieval worked)
            if r7.status_code == 200:
                full_text = _collect_sse_text(r7)
                h.test(
                    "Auto-filter: response mentions diagnostics or AI",
                    any(word in full_text.lower() for word in ["diagnostic", "ai", "healthcare", "accuracy"]),
                    f"response_preview={full_text[:200]}",
                )

            # Vague query should still work (auto-filter returns None)
            r8 = requests.post(
                f"{h.BASE_URL}/api/threads/{thread_id}/messages",
                headers=h.auth_headers(token_a),
                json={"content": "Hello, how are you?"},
                stream=True,
                timeout=60,
            )
            h.test(
                "Auto-filter: vague query without filter -> 200",
                r8.status_code == 200,
                f"status={r8.status_code}",
            )

    # Cleanup
    h.cleanup_files(token_a)
    h.cleanup_threads(token_a)

    return h.passed, h.failed


if __name__ == "__main__":
    import sys
    h.clear_token_cache()
    run()
    sys.exit(h.summary())
