---
phase: 6
slug: file-explorer-ui-cluster
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-10
---

# Phase 6 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | Playwright 1.x (frontend e2e) + pytest (backend) |
| **Config file** | `frontend/playwright.config.ts`, `backend/scripts/test_all.py` |
| **Quick run command** | `cd frontend && npx playwright test e2e/full-suite.spec.ts --grep '@phase6'` |
| **Full suite command** | `cd frontend && npx playwright test e2e/full-suite.spec.ts && cd ../backend && venv/Scripts/python scripts/test_all.py` |
| **Estimated runtime** | ~90s frontend (full), ~60s backend (full) |

---

## Sampling Rate

- **After every task commit:** Run quick command (Phase-6-tagged Playwright tests)
- **After every plan wave:** Run full suite command
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 90 seconds

---

## Per-Task Verification Map

> Filled by planner — each task gets a row mapping to a Playwright spec, type-check, or backend pytest.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| TBD | TBD | TBD | UI-01..UI-11, TEST-05 | Pitfall 5/11/12 | Cross-scope drag blocks; admin-only Shared writes; recursive SubAgentSection | playwright | `npx playwright test --grep '@phase6'` | ❌ W0 | ⬜ pending |

---

## Wave 0 Requirements

- [ ] `frontend/e2e/full-suite.spec.ts` — extend with `@phase6` tagged tests for: folder tree nav, drag-move (same-scope), cross-scope drag block modal, sub-agent activity card, scope visibility (admin vs regular), keyboard navigation
- [ ] Admin test account seed — `backend/supabase/seed.sql` or migration creates `admin@test.com` with `is_admin=true`
- [ ] `frontend/playwright.config.ts` or test fixture — expose `ADMIN_TEST_EMAIL` / `ADMIN_TEST_PASSWORD` (matching existing `TEST_EMAIL` / `TEST_PASSWORD` convention)
- [ ] `@dnd-kit/core@6.3.1` + `@dnd-kit/sortable@10.0.0` + `@radix-ui/react-context-menu` installed in `frontend/package.json`

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Visual polish of drop indicator (line vs highlight) | UI-04 | Subjective visual fidelity hard to assert programmatically | Drag a doc between sibling rows — confirm 2px primary-color horizontal line; drag onto a folder — confirm folder highlight ring |
| localStorage persistence across browser restarts | UI-02 | Playwright clears storage between tests | Open folders, close browser, reopen, confirm same folders open |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references (admin account, dnd-kit install)
- [ ] No watch-mode flags
- [ ] Feedback latency < 90s
- [ ] `nyquist_compliant: true` set in frontmatter (set by planner after task map fills)

**Approval:** pending
