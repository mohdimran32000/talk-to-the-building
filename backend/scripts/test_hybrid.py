"""Tests for hybrid search (vector + keyword RRF) and reranking.

Covers: settings fields, hybrid retrieval, keyword matching, settings toggles, metadata filter.
"""
import sys
import os
import requests

sys.path.insert(0, os.path.dirname(__file__))
import test_helpers as h

# Document with a unique technical term for keyword search testing
HYBRID_TEST_TEXT = b"""The Zyphorian Protocol is an advanced network synchronization framework.
It uses quantum-entangled packet routing to achieve sub-nanosecond latency.
The Zyphorian Protocol was developed at the Institute of Distributed Systems in 2024.
Key features include: deterministic jitter compensation, adaptive flow control,
and multi-path redundancy with automatic failover.
The protocol operates at Layer 3.5 of the OSI model, bridging network and transport layers.
Performance benchmarks show 99.999% packet delivery rates under heavy load."""

# Admin user (admin@test.com) for settings changes
ADMIN_EMAIL = h.TEST_USER_ADMIN["email"]
ADMIN_PASSWORD = h.TEST_USER_ADMIN["password"]


def _get_admin_token():
    return h.get_auth_token(ADMIN_EMAIL, ADMIN_PASSWORD)


def _update_setting(token, **kwargs):
    """Update a global setting (requires admin)."""
    return requests.put(
        f"{h.BASE_URL}/api/settings",
        headers=h.auth_headers(token),
        json=kwargs,
    )


def run():
    h.reset_counters()
    admin_token = _get_admin_token()
    user_token = h.get_auth_token()
    headers = h.auth_headers(user_token)

    doc_id = None
    thread_id = None

    # Save original settings to restore later
    orig = requests.get(f"{h.BASE_URL}/api/settings", headers=h.auth_headers(admin_token)).json()
    orig_hybrid = orig.get("hybrid_search_enabled", True)
    orig_reranking = orig.get("reranking_enabled", False)

    try:
        # --- Settings API ---
        h.section("Hybrid - Settings API")
        r = requests.get(f"{h.BASE_URL}/api/settings", headers=h.auth_headers(admin_token))
        data = r.json() if r.status_code == 200 else {}
        h.test(
            "Settings includes hybrid_search_enabled",
            "hybrid_search_enabled" in data and isinstance(data["hybrid_search_enabled"], bool),
            f"data keys={list(data.keys())}",
        )
        h.test(
            "Settings includes reranking_enabled",
            "reranking_enabled" in data and isinstance(data["reranking_enabled"], bool),
            f"data keys={list(data.keys())}",
        )
        h.test(
            "Settings includes reranking_provider",
            "reranking_provider" in data and data["reranking_provider"] in ("gemini", "cohere"),
            f"provider={data.get('reranking_provider')}",
        )

        # --- Ensure hybrid is ON for retrieval tests ---
        _update_setting(admin_token, hybrid_search_enabled=True, reranking_enabled=False)

        # --- Upload & Ingest ---
        h.section("Hybrid - Upload & Ingest")
        r = requests.post(
            f"{h.BASE_URL}/api/files/upload",
            headers={"Authorization": f"Bearer {user_token}"},
            files={"file": ("zyphorian_protocol.txt", HYBRID_TEST_TEXT, "text/plain")},
        )
        doc = r.json() if r.status_code == 200 else {}
        doc_id = doc.get("id")
        if doc_id:
            h.track_file(doc_id)
        final_status, error_msg = h.poll_document_status(user_token, doc_id, "ready", max_wait=30) if doc_id else ("failed", "no doc_id")
        h.test("Document ingested and ready", final_status == "ready", f"status={final_status}, error={error_msg}")

        # --- Hybrid Retrieval ---
        h.section("Hybrid - Retrieval")
        r = requests.post(f"{h.BASE_URL}/api/threads", headers=headers, json={"title": "Hybrid Test"})
        thread_id = r.json().get("id") if r.status_code == 200 else None
        if thread_id:
            h.track_thread(thread_id)

        if thread_id and final_status == "ready":
            # Test keyword-exact match — "Zyphorian" is a unique term only in our document
            result = h.stream_sse(user_token, thread_id, "What is the Zyphorian Protocol?")
            if len(result) == 4:
                full_text, status, _, _ = result
            else:
                full_text, status = result[0], result[1]
            h.test(
                "Hybrid retrieval returns relevant response",
                status == 200 and full_text and "zyphorian" in full_text.lower(),
                full_text[:200] if full_text else "empty",
            )
            h.test(
                "Response includes document-specific details",
                full_text and any(w in full_text.lower() for w in ["network", "synchronization", "packet", "latency"]),
                full_text[:200] if full_text else "empty",
            )
        else:
            h.test("Hybrid retrieval returns relevant response", False, "no thread or doc not ready")
            h.test("Response includes document-specific details", False, "no thread or doc not ready")

        # --- Toggle hybrid OFF (vector-only fallback) ---
        h.section("Hybrid - Settings Toggles")
        r = _update_setting(admin_token, hybrid_search_enabled=False)
        h.test("Toggle hybrid OFF", r.status_code == 200, f"status={r.status_code}")

        if thread_id and final_status == "ready":
            result = h.stream_sse(user_token, thread_id, "Tell me about the Zyphorian Protocol's latency features.")
            if len(result) == 4:
                full_text, status, _, _ = result
            else:
                full_text, status = result[0], result[1]
            h.test(
                "Vector-only fallback still returns results",
                status == 200 and full_text and len(full_text) > 10,
                full_text[:200] if full_text else "empty",
            )

        # Restore hybrid ON
        _update_setting(admin_token, hybrid_search_enabled=True)

        # --- Toggle reranking ON ---
        r = _update_setting(admin_token, reranking_enabled=True, reranking_provider="gemini")
        h.test("Toggle reranking ON (Gemini)", r.status_code == 200, f"status={r.status_code}")

        if thread_id and final_status == "ready":
            result = h.stream_sse(user_token, thread_id, "What packet delivery rates does the Zyphorian Protocol achieve?", timeout=90)
            if len(result) == 4:
                full_text, status, _, _ = result
            else:
                full_text, status = result[0], result[1]
            h.test(
                "Reranking retrieval still returns results",
                status == 200 and full_text and len(full_text) > 10,
                full_text[:200] if full_text else "empty",
            )

        # Restore reranking OFF
        _update_setting(admin_token, reranking_enabled=False)

    finally:
        # Restore original settings
        _update_setting(admin_token, hybrid_search_enabled=orig_hybrid, reranking_enabled=orig_reranking)
        # Clean up tracked resources
        h.cleanup_files(user_token)
        h.cleanup_threads(user_token)

    return h.passed, h.failed


if __name__ == "__main__":
    h.clear_token_cache()
    run()
    sys.exit(h.summary())
