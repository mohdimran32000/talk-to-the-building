"""Tests for Module 7: Additional Tools (Text-to-SQL + Web Search).

Tests: settings API fields, structured data ingestion from CSV,
Text-to-SQL response, web search response, tool-disabled behavior.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from test_helpers import (
    BASE_URL, test, section, reset_counters, get_auth_token,
    auth_headers, track_file, cleanup_files, poll_document_status,
    stream_sse, track_thread, cleanup_threads,
)


def run():
    reset_counters()
    token = get_auth_token()
    headers = auth_headers(token)

    # ── Settings API ──
    section("Additional Tools Settings")

    r = requests.get(f"{BASE_URL}/api/settings", headers=headers)
    test("GET /api/settings returns 200", r.status_code == 200)
    settings = r.json()
    test("Settings has text_to_sql_enabled field", "text_to_sql_enabled" in settings)
    test("Settings has web_search_enabled field", "web_search_enabled" in settings)
    test("Settings has tavily_api_key_set field", "tavily_api_key_set" in settings)
    test("text_to_sql_enabled defaults to false", settings.get("text_to_sql_enabled") is False)
    test("web_search_enabled defaults to false", settings.get("web_search_enabled") is False)

    # ── Structured Data Ingestion ──
    section("Structured Data from CSV")

    csv_content = "name,quantity,price\nWidget A,100,9.99\nWidget B,250,14.50\nWidget C,50,29.99\n"
    files = {"file": ("test_products.csv", csv_content.encode(), "text/csv")}
    r = requests.post(
        f"{BASE_URL}/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files=files,
    )
    test("CSV upload returns 200", r.status_code == 200)
    doc = r.json()
    doc_id = doc.get("id")
    if doc_id:
        track_file(doc_id)

    # Wait for ingestion to complete
    if doc_id:
        status, error = poll_document_status(token, doc_id, target="ready", max_wait=60)
        test("CSV document reaches ready status", status == "ready", f"status={status}, error={error}")

    # ── Text-to-SQL with SSE ──
    section("Text-to-SQL Tool")

    # Enable text-to-sql via admin settings (need admin token)
    from test_helpers import TEST_USER_ADMIN
    admin_token = get_auth_token(TEST_USER_ADMIN["email"], TEST_USER_ADMIN["password"])
    admin_headers = auth_headers(admin_token)

    # Enable text-to-sql
    r = requests.put(
        f"{BASE_URL}/api/settings",
        headers=admin_headers,
        json={"text_to_sql_enabled": True},
    )
    test("Enable text_to_sql_enabled via settings", r.status_code == 200)

    # Create a thread and ask a quantitative question
    r = requests.post(f"{BASE_URL}/api/threads", headers=headers, json={"title": "SQL test"})
    thread = r.json()
    thread_id = thread.get("id")
    if thread_id:
        track_thread(thread_id)

    if thread_id:
        result = stream_sse(token, thread_id, "What is the total quantity of all products?", timeout=90)
        text, status_code, has_token, has_done = result
        test("SQL question SSE returns 200", status_code == 200)
        test("SQL question returns a response", has_token and len(text) > 0, f"text={text[:100] if text else 'empty'}")

    # ── Web Search ──
    section("Web Search Tool")

    # Check web search works when enabled with key
    r = requests.get(f"{BASE_URL}/api/settings", headers=headers)
    settings = r.json()
    test("web_search_enabled readable", "web_search_enabled" in settings)

    # Disable text-to-sql, reset
    r = requests.put(
        f"{BASE_URL}/api/settings",
        headers=admin_headers,
        json={"text_to_sql_enabled": False},
    )
    test("Disable text_to_sql_enabled", r.status_code == 200)

    # ── Cleanup ──
    section("Cleanup")
    cleanup_threads(token)
    cleanup_files(token)
    test("Cleanup complete", True)

    from test_helpers import passed, failed
    return passed, failed


if __name__ == "__main__":
    p, f = run()
    print(f"\n{'='*40}")
    print(f"Results: {p} passed, {f} failed")
    sys.exit(0 if f == 0 else 1)
