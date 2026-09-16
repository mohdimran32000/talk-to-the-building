"""Unified test runner -- validates ALL backend features.

Run: cd backend && venv/Scripts/python scripts/test_all.py
Requires: backend running on localhost:8001, .env with Supabase + Gemini keys.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import test_helpers as h
import test_health
import test_auth
import test_threads
import test_messages
import test_files
import test_folders         # NEW (Phase 3)
import test_exploration_tools  # NEW (Phase 4)
import test_explorer_sub_agent  # NEW (Phase 5)
import test_backfill
import test_rag
import test_rls
import test_two_scope_rls
import test_settings
import test_metadata
import test_hybrid
import test_tools
import test_sub_agents

SUITES = [
    ("Health", test_health),
    ("Auth", test_auth),
    ("Threads", test_threads),
    ("Messages", test_messages),
    ("Files", test_files),
    ("Folders", test_folders),       # NEW (Phase 3 — folders is logically a Files extension)
    ("Exploration", test_exploration_tools),  # NEW (Phase 4)
    ("Explorer", test_explorer_sub_agent),    # NEW (Phase 5 — explore_knowledge_base sub-agent)
    ("Backfill", test_backfill),
    ("RAG", test_rag),
    ("RLS", test_rls),
    ("Two-Scope RLS", test_two_scope_rls),
    ("Settings", test_settings),
    ("Metadata", test_metadata),
    ("Hybrid", test_hybrid),
    ("Tools", test_tools),
    ("Sub-Agents", test_sub_agents),
]


def main():
    h.clear_token_cache()  # Ensure fresh tokens for the run
    total_passed = 0
    total_failed = 0
    suite_results = []

    for name, module in SUITES:
        print(f"\n{'='*60}")
        print(f"  Suite: {name}")
        print(f"{'='*60}")
        try:
            p, f = module.run()
            total_passed += p
            total_failed += f
            suite_results.append((name, p, f))
        except Exception as e:
            print(f"  ERROR: Suite {name} crashed: {e}")
            total_failed += 1
            suite_results.append((name, 0, 1))

    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    for name, p, f in suite_results:
        status = "PASS" if f == 0 else "FAIL"
        print(f"  [{status}] {name}: {p} passed, {f} failed")
    print(f"\n  Total: {total_passed} passed, {total_failed} failed")
    if total_failed == 0:
        print("  All tests passed!")
    else:
        print(f"  {total_failed} test(s) failed.")
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
