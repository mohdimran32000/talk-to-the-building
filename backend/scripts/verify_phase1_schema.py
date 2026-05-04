"""Phase 1 schema verifier — runs structural checks against the live Supabase Postgres DB.

Invoked by plan 07 task 1-07-03 immediately after run_migrations.py applies migrations
012-016. Queries pg_extension / information_schema / pg_proc / pg_trigger / pg_policies /
pg_indexes and confirms every expected artifact exists.

Usage:
    cd backend && venv/Scripts/python scripts/verify_phase1_schema.py

Exit code:
    0  — all 18 checks pass; plan 08 (test_two_scope_rls.py) is unblocked
    1  — at least one check failed; failed checks are listed in stdout

Reusable beyond Phase 1 — any future schema regression test can run this script as a
smoke check that the Phase 1 schema is still intact.
"""
import os
import sys

import psycopg2


CHECKS = [
    ("pg_trgm extension enabled",
     "SELECT COUNT(*) FROM pg_extension WHERE extname='pg_trgm'", 1),
    ("documents.folder_path column exists",
     "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='documents' AND column_name='folder_path'", 1),
    ("documents.scope column exists",
     "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='documents' AND column_name='scope'", 1),
    ("documents.content_markdown column exists",
     "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='documents' AND column_name='content_markdown'", 1),
    ("documents.content_markdown_status column exists",
     "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='documents' AND column_name='content_markdown_status'", 1),
    ("document_chunks.scope column exists",
     "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='document_chunks' AND column_name='scope'", 1),
    ("public.folders table exists",
     "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='folders'", 1),
    ("public.is_admin() function exists",
     "SELECT COUNT(*) FROM pg_proc p JOIN pg_namespace n ON p.pronamespace=n.oid WHERE n.nspname='public' AND p.proname='is_admin'", 1),
    ("public.forbid_scope_mutation() function exists",
     "SELECT COUNT(*) FROM pg_proc p JOIN pg_namespace n ON p.pronamespace=n.oid WHERE n.nspname='public' AND p.proname='forbid_scope_mutation'", 1),
    ("forbid_scope_mutation triggers attached (>= 3)",
     "SELECT COUNT(*) FROM pg_trigger WHERE tgname LIKE '%forbid_scope_mutation%' AND NOT tgisinternal", (3,)),
    ("Two-scope policies on documents (>= 7)",
     r"SELECT COUNT(*) FROM pg_policies WHERE schemaname='public' AND tablename='documents' AND policyname LIKE 'documents\_%' ESCAPE '\'", (7,)),
    ("Two-scope policies on document_chunks (>= 5)",
     r"SELECT COUNT(*) FROM pg_policies WHERE schemaname='public' AND tablename='document_chunks' AND policyname LIKE 'document_chunks\_%' ESCAPE '\'", (5,)),
    ("Two-scope policies on folders (>= 7)",
     r"SELECT COUNT(*) FROM pg_policies WHERE schemaname='public' AND tablename='folders' AND policyname LIKE 'folders\_%' ESCAPE '\'", (7,)),
    ("Episode-1 single-axis docs policies dropped",
     "SELECT COUNT(*) FROM pg_policies WHERE schemaname='public' AND tablename='documents' AND policyname='Users can view own documents'", 0),
    ("Search index documents_content_markdown_trgm_idx exists",
     "SELECT COUNT(*) FROM pg_indexes WHERE schemaname='public' AND indexname='documents_content_markdown_trgm_idx'", 1),
    ("Search index documents_folder_path_prefix_idx exists",
     "SELECT COUNT(*) FROM pg_indexes WHERE schemaname='public' AND indexname='documents_folder_path_prefix_idx'", 1),
    ("Unique expression index folders_scope_user_path_unique exists",
     "SELECT COUNT(*) FROM pg_indexes WHERE schemaname='public' AND indexname='folders_scope_user_path_unique'", 1),
    ("Unique expression index documents_scope_user_path_filename_unique exists",
     "SELECT COUNT(*) FROM pg_indexes WHERE schemaname='public' AND indexname='documents_scope_user_path_filename_unique'", 1),
]


def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("[FATAL] DATABASE_URL not set. Export the Direct connection string from Supabase.")
        return 1

    try:
        conn = psycopg2.connect(db_url)
    except Exception as e:
        print(f"[FATAL] Could not connect to DATABASE_URL: {e}")
        return 1
    conn.autocommit = True

    print("Verifying live schema state after plans 02-06 push...")
    failures = []
    for label, sql, expected in CHECKS:
        with conn.cursor() as c:
            c.execute(sql)
            got = c.fetchone()[0]
        ok = (got == expected) if isinstance(expected, int) else (got >= expected[0])
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] {label}: got={got} expected={expected}")
        if not ok:
            failures.append(label)

    conn.close()
    print()
    if failures:
        print(f"SCHEMA VERIFY FAILED: {len(failures)} check(s) did not pass: {failures}")
        return 1
    print(f"SCHEMA VERIFY OK: all {len(CHECKS)} checks passed. Plan 08 (test_two_scope_rls.py) is unblocked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
