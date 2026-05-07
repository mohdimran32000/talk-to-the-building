---
status: partial
phase: 03-folder-service-routers-dedup-extension
source: [03-VERIFICATION.md, 03-REVIEW.md]
started: 2026-05-07T11:30:00Z
updated: 2026-05-07T11:30:00Z
---

## Current Test

[awaiting human testing]

## Tests

### 1. Restart uvicorn and run the focused folder suite
expected: Output ends with `Results: N passed, 0 failed` where N >= 35 (36 if `DATABASE_URL` is exported; 35 if the FOLDER-03 transactional-rollback test SKIPs).
result: [pending]

Steps:
- Stop the running uvicorn on `localhost:8001` (it predates Plans 04+05 — `/openapi.json` shows 10 routes; HEAD source mounts 23).
- Start a fresh shell: `cd backend && venv/Scripts/python -m uvicorn app.main:app --reload --port 8001`
- Verify routes: `curl http://localhost:8001/openapi.json | jq '.paths | keys | length'` → expect 23.
- Run the focused suite: `cd backend && venv/Scripts/python scripts/test_folders.py`
- Expected last line: `Results: N passed, 0 failed`

### 2. (Optional, recommended) Export DATABASE_URL before running the suite
expected: FOLDER-03 transactional-rollback section runs `test_rename_folder_prefix_fails_midway` PL/pgSQL fixture and asserts post-failure `documents.folder_path` is UNCHANGED.
result: [pending]

Steps:
- Source the Direct connection string from Supabase Dashboard → Project Settings → Database → Connection string → URI → Direct connection (port 5432).
- `$env:DATABASE_URL = "postgresql://..."`
- Re-run the focused suite from Test 1.

### 3. (Optional) Cross-suite regression sweep
expected: All 15 suites either pass or fail with previously-known Phase-1 carry-forward FAILs only (admin-assumption regression in Threads/Messages/Hybrid/Tools/Sub-Agents per STATE.md L118).
result: [pending]

Steps:
- After the focused suite is green: `cd backend && venv/Scripts/python scripts/test_all.py`
- This is operator-gated per CLAUDE.md: "Do NOT run the full test suite automatically."

## Summary

total: 3
passed: 0
issues: 0
pending: 3
skipped: 0
blocked: 0

## Gaps

(none from verification — all 56/57 must-haves verified at the source level; remaining item is runtime confirmation only)
