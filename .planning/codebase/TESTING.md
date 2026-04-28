# Testing Patterns

**Analysis Date:** 2026-04-28

## Test Framework

**Backend:**
- **Runner:** Custom Python test suite (no pytest/unittest)
  - Config: `backend/scripts/test_all.py`
  - Command: `cd backend && venv/Scripts/python scripts/test_all.py`
  - Requires: Backend running on `localhost:8001`, `.env` file with Supabase + Gemini keys
  - Coverage: 112 tests across 12 suites

**Frontend:**
- **Runner:** Playwright (end-to-end browser testing)
  - Config: `frontend/playwright.config.ts`
  - Primary suite: `frontend/e2e/full-suite.spec.ts` (26 tests)
  - Command: `cd frontend && npx playwright test e2e/full-suite.spec.ts`
  - Requires: Backend running on `localhost:8001`, frontend on `localhost:5173` (or `5174` in config)
  - Browser: Chromium

**Assertion Library:**
- Backend: Simple assertion function `h.test(name, condition, detail)` in `test_helpers.py`
- Frontend: Playwright assertions (`expect(...)`)

**Run Commands:**
```bash
# Backend: all 112 tests
cd backend && venv/Scripts/python scripts/test_all.py

# Frontend: 26 e2e tests
cd frontend && npx playwright test e2e/full-suite.spec.ts

# Frontend: single test
cd frontend && npx playwright test e2e/full-suite.spec.ts -g "Auth.*Sign in"

# Frontend: headed mode (see browser)
cd frontend && npx playwright test --headed
```

## Test File Organization

**Location:**
- Backend: `backend/scripts/test_*.py` (12 modules, co-located in one directory)
- Frontend: `frontend/e2e/*.spec.ts` (separate from source code)

**Naming:**
- Backend: `test_<feature>.py` (e.g., `test_auth.py`, `test_messages.py`, `test_hybrid.py`)
- Frontend: `<name>.spec.ts` (e.g., `full-suite.spec.ts`, `module1.spec.ts`)

**Structure:**
```
backend/scripts/
├── test_all.py          # Main runner, orchestrates 12 suites
├── test_helpers.py      # Shared utilities (auth, SSE parsing, polling, cleanup)
├── test_health.py       # Health check endpoint
├── test_auth.py         # Auth rejection (unauthenticated requests)
├── test_threads.py      # Thread CRUD + isolation
├── test_messages.py     # Message send/receive + SSE streaming + persistence
├── test_files.py        # File upload + ingestion status
├── test_rag.py          # RAG retrieval (search_documents tool)
├── test_rls.py          # Row-Level Security isolation
├── test_settings.py     # Admin settings + RLS enforcement
├── test_metadata.py     # Metadata filtering
├── test_hybrid.py       # Hybrid search + reranking
├── test_tools.py        # Tool calling (search, query, web search)
└── test_sub_agents.py   # Sub-agent document analysis
```

## Test Structure

**Backend Pattern:**

```python
"""Brief description of what this test suite covers."""
import sys
import os
import requests

sys.path.insert(0, os.path.dirname(__file__))
import test_helpers as h

def run():
    h.reset_counters()
    token_a = h.get_auth_token()  # Get auth token for test user
    headers_a = h.auth_headers(token_a)

    # Test section
    h.section("Auth Rejection (no token)")

    # Individual tests
    r = requests.get(f"{h.BASE_URL}/api/threads", timeout=5)
    h.test("GET /api/threads rejects no token", r.status_code in (401, 403, 422), f"status={r.status_code}")

    # Cleanup tracked resources
    h.cleanup_threads(token_a)
    h.cleanup_files(token_a)

    return h.passed, h.failed

if __name__ == "__main__":
    run()
    sys.exit(h.summary())
```

**Frontend Pattern (Playwright):**

```typescript
import { test, expect } from '@playwright/test'

const TEST_EMAIL = 'test@test.com'
const TEST_PASSWORD = 'supabase123'

async function signIn(page: import('@playwright/test').Page) {
  await page.goto('/login')
  await page.getByLabel('Email').fill(TEST_EMAIL)
  await page.getByLabel('Password').fill(TEST_PASSWORD)
  await page.getByRole('button', { name: 'Sign In' }).click()
  await expect(page.getByRole('button', { name: '+ New Chat' })).toBeVisible({ timeout: 15000 })
}

test.describe('Auth', () => {
  test.describe.configure({ mode: 'serial' })

  test('Protected route redirects to /login', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveURL(/\/login/)
  })

  test('Valid credentials redirect to chat', async ({ page }) => {
    await signIn(page)
    await expect(page).toHaveURL('/')
  })
})
```

**Patterns:**
- Backend: Module-level `run()` function called by `test_all.py`
  - Section markers: `h.section(title)` for grouping
  - Per-test assertion: `h.test(name, condition, detail)`
  - Results tracked globally: `h.passed`, `h.failed`
- Frontend: Test groups via `test.describe()`, serial mode for stateful tests
  - Helpers defined at top: `signIn()`, `uploadFile()`, etc.
  - Assertions use Playwright's fluent API

## Mocking

**Framework:**
- Backend: Minimal mocking; prefer real Supabase + Gemini for integration tests
- Frontend: No mocking detected; tests hit real backend

**Patterns:**
- **Backend:** No mock objects; `test_helpers.py` provides real auth flow
  - Auth tokens obtained from real Supabase: `supabase.auth.getUser(token)`
  - Supabase queries real tables (with `.eq("user_id", user_id)` RLS filters)
  - Gemini API called for real during message streaming tests
- **Frontend:** No Vitest/Jest setup; Playwright drives real browser against real backend

**What to Mock:**
- Nothing in current test suite (all integration/e2e)
- If adding unit tests: Would mock external API calls, but not currently done

**What NOT to Mock:**
- Auth (test with real Supabase tokens)
- Database queries (test with real RLS filters)
- LLM calls (test with real Gemini during e2e)

## Fixtures and Factories

**Test Data:**
- Backend test users (hardcoded in `test_helpers.py`):
  ```python
  TEST_USER_A = {"email": "testuser@example.com", "password": "testpassword123"}
  TEST_USER_B = {"email": "test@test.com", "password": "supabase123"}
  ```
- Created during tests: Threads, messages, files, documents
- Resource tracking: Helper functions register IDs for cleanup

**Location:**
- `backend/scripts/test_helpers.py` — Auth, SSE parsing, polling, cleanup utilities
- Frontend helpers: Inline in `full-suite.spec.ts` (e.g., `signIn()` helper)

**Test File Upload (Frontend Example):**
```typescript
// Upload a file, track ID for cleanup
const [fileBuffer] = await page.evaluate(() => {
  const blob = new Blob(['CSV data...'], { type: 'text/csv' })
  return [new File([blob], 'test.csv')]
})
await page.locator('input[type="file"]').setInputFiles({ name: 'test.csv', mimeType: 'text/csv', buffer: fileBuffer })
await page.getByRole('button', { name: 'Upload' }).click()
```

## Coverage

**Requirements:** Not enforced (no coverage threshold configured)

**View Coverage:**
- Backend: No built-in coverage (tests run sequentially, report pass/fail counts)
- Frontend: No coverage tool configured

**What IS Tested:**
- Backend: 112 tests across health, auth, threads, messages, files, RAG, RLS, settings, metadata, hybrid search, tools, sub-agents
- Frontend: 26 tests covering auth flow, threads, messages, documents, theme toggle, console errors

## Test Types

**Unit Tests:**
- Not used; project relies on integration/e2e tests
- Backend: Each test module tests one feature end-to-end (auth, threads, messages)
- Frontend: Playwright tests are effectively integration tests (browser + backend)

**Integration Tests:**
- Backend: `test_rag.py`, `test_messages.py`, `test_hybrid.py` test API endpoints + Supabase + LLM
- Frontend: `full-suite.spec.ts` tests UI + auth + backend API

**E2E Tests:**
- Frontend: `full-suite.spec.ts` with 26 tests
  - Covers: Auth (sign up/in/out), threads (create, list, select), messages (send, SSE stream), documents (upload, delete), settings (admin only), theme toggle
  - Browser: Chromium
  - Isolation: Serial mode for auth tests to avoid race conditions

## Common Patterns

**Async Testing (Backend):**
```python
# SSE streaming: collect tokens from event stream
full_text, status, has_token_event, has_done_event = h.stream_sse(
    token_a, thread_id, "What is the capital of France?"
)
h.test("Response text non-empty", bool(full_text and full_text.strip()), ...)
```

**Polling Pattern (Backend):**
```python
# Wait for ingestion to complete
final_status, error_message = h.poll_document_status(token_a, doc_id, target="ready", max_wait=30)
h.test("Document ready", final_status == "ready", f"status={final_status}, error={error_message}")
```

**Async Testing (Frontend):**
```typescript
// Send message, wait for response to stream in
await page.getByPlaceholder('Type a message...').fill('Hello')
await page.getByRole('button', { name: 'Send' }).click()
await expect(page.locator('.assistant-message')).toContainText('response', { timeout: 30000 })
```

**Error Testing (Backend):**
```python
# Test error responses
r = requests.post(f"{h.BASE_URL}/api/threads", headers=headers_b, json={})
h.test("Create thread as other user fails", r.status_code != 200, f"status={r.status_code}")
```

**Isolation Testing (Frontend):**
```typescript
test('Sign out redirects to login', async ({ page }) => {
  await signIn(page)
  await page.getByRole('button', { name: 'Sign Out' }).click()
  await expect(page).toHaveURL(/\/login/, { timeout: 10000 })
})
```

## Resource Cleanup

**CRITICAL RULE:** Tests must NEVER delete all user data. Only clean up resources created during test runs.

**Backend Cleanup Pattern:**
```python
def track_thread(thread_id):
    """Register a thread ID for scoped cleanup."""
    _created_thread_ids.append(thread_id)

def cleanup_threads(token):
    """Delete only threads created during this test run."""
    for tid in _created_thread_ids:
        try:
            requests.delete(f"{BASE_URL}/api/threads/{tid}", headers=auth_headers(token))
        except Exception:
            pass
    _created_thread_ids.clear()
```

**Usage in Tests:**
```python
try:
    # Create resources
    r = requests.post(f"{h.BASE_URL}/api/threads", headers=headers_a, json={"title": "Test"})
    thread_id = r.json().get("id")
    h.track_thread(thread_id)  # Register for cleanup

    # Run test
    # ...
finally:
    h.cleanup_threads(token_a)  # Clean up only tracked threads
    h.cleanup_files(token_a)    # Clean up only tracked files
```

**Constraints:**
- Never use blanket `DELETE FROM` or `TRUNCATE` statements
- Never run migrations with `DROP TABLE` on tables holding user data
- Track IDs in module-level lists: `_created_thread_ids`, `_created_file_ids`
- Call cleanup functions in `finally` blocks or after test section

## Test Execution Flow

**Backend Full Suite:**
1. `test_all.py` imports 12 test modules
2. Iterates over `SUITES` list, calling `module.run()` on each
3. Each module returns `(passed, failed)` tuple
4. Aggregates results, prints summary
5. Exits with code 0 (all pass) or 1 (any fail)

**Frontend Full Suite:**
1. Playwright discovers `full-suite.spec.ts`
2. Runs test groups serially (auth tests first, then threads, etc.)
3. Per-test timeout: 30000ms
4. Assertion timeout: 10000ms
5. Screenshots on failure, trace on first retry
6. Reports pass/fail/skip

## Adding New Tests

**Backend:**
1. Create `backend/scripts/test_<feature>.py`
2. Implement `run()` function that returns `(passed, failed)`
3. Use `h.test()`, `h.section()`, tracking functions (`h.track_thread()`, etc.)
4. Register in `test_all.py` SUITES list
5. Run: `cd backend && venv/Scripts/python scripts/test_all.py`

**Frontend:**
1. Add test groups to `frontend/e2e/full-suite.spec.ts`
2. Use `test.describe()` for grouping, `test()` for individual tests
3. Use Playwright locators: `page.getByLabel()`, `page.getByRole()`, `page.locator()`
4. Add assertions: `expect(...).toBeVisible()`, `expect(...).toHaveURL()`
5. Run: `cd frontend && npx playwright test e2e/full-suite.spec.ts`

---

*Testing analysis: 2026-04-28*
