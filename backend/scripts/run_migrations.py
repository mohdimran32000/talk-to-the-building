"""Run all SQL migrations in backend/migrations/ against a Supabase Postgres database.

Usage:
    DATABASE_URL='postgresql://...' venv/Scripts/python scripts/run_migrations.py

Get DATABASE_URL from Supabase: Project Settings -> Database -> Connection string -> URI.
Use the "Direct connection" string (port 5432), not the pooler — DDL works reliably on direct.

Each migration runs in its own transaction. On failure, the failing migration rolls back
and the script stops; later migrations are not attempted.
"""
import os
import sys
from pathlib import Path

import psycopg2

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL env var not set", file=sys.stderr)
        print("Get it from Supabase: Settings -> Database -> Connection string -> URI", file=sys.stderr)
        return 1

    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        print(f"No .sql files found in {MIGRATIONS_DIR}", file=sys.stderr)
        return 1

    print(f"Found {len(files)} migration(s) in {MIGRATIONS_DIR}")
    for f in files:
        print(f"  - {f.name}")
    print()

    conn = psycopg2.connect(db_url)
    conn.autocommit = False

    try:
        for f in files:
            sql = f.read_text(encoding="utf-8")
            if not sql.strip():
                print(f"SKIP {f.name} (empty)")
                continue

            print(f"RUN  {f.name} ... ", end="", flush=True)
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                conn.commit()
                print("OK")
            except Exception as e:
                conn.rollback()
                print(f"FAIL\n  {type(e).__name__}: {e}")
                return 2
    finally:
        conn.close()

    print(f"\nAll {len(files)} migration(s) applied successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
