"""Seed the admin@test.com user for the Phase 6 Playwright @phase6 e2e suite.

Per D-02 (06-CONTEXT.md): provisions admin@test.com for the Phase 6 Playwright @phase6 suite.
Idempotent. Companion to migration 021 which sets is_admin=true on the resulting profile row.

Supabase Auth users CANNOT be seeded purely via SQL — auth.users.encrypted_password requires
GoTrue's bcrypt + internal columns. The Admin API is the supported path: it provisions the
auth.users row, hashes the password the way GoTrue expects, and skips the email confirmation
flow when email_confirm=True.

Separation of concerns with migration 021_admin_test_user.sql:
    - This script  -> creates auth.users row (Admin API; bcrypt-hashed password).
    - Migration 021 -> flips public.profiles.is_admin = true for the seeded user.

Usage:
    cd backend && venv/Scripts/python scripts/seed_admin_user.py

Required env vars (loaded from backend/.env via python-dotenv):
    SUPABASE_URL                  e.g. https://<project>.supabase.co
    SUPABASE_SERVICE_ROLE_KEY     service-role key (Auth Admin API REQUIRES service-role).

Optional env var:
    TEST_USER_ADMIN_PASSWORD      defaults to 'adminpassword123' to match
                                  backend/scripts/test_helpers.py:26-29 convention.

Exit codes:
    0   Clean (user created on first run, or already exists on subsequent runs).
    1   Missing env / unrecoverable network or API failure.

Idempotency: re-running this script after a successful pass is a no-op. The supabase-py
Auth Admin API raises an "email already registered" style exception when the user exists;
we catch it case-insensitively and treat it as success (the auth.users row is the desired
end state regardless of who created it).
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

# Load env from backend/.env BEFORE constructing the supabase client (the SUPABASE_URL +
# SUPABASE_SERVICE_ROLE_KEY env vars are required at create_client() time).
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_BACKEND_ROOT / ".env")


ADMIN_EMAIL = "admin@test.com"
DEFAULT_ADMIN_PASSWORD = "adminpassword123"


def _service_role_client():
    """Construct a service-role Supabase client.

    Mirrors backend/app/auth.py::get_supabase_client() — same SUPABASE_URL +
    SUPABASE_SERVICE_ROLE_KEY env-var contract. Required for auth.admin.* calls
    (the Auth Admin API rejects anon-key requests).
    """
    url = os.environ.get("SUPABASE_URL")
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not service_role_key:
        print(
            "ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in backend/.env",
            file=sys.stderr,
        )
        print(
            "  Get SUPABASE_SERVICE_ROLE_KEY from: Supabase Dashboard -> Settings -> API -> service_role secret",
            file=sys.stderr,
        )
        sys.exit(1)
    return create_client(url, service_role_key)


def _find_admin_user_id(sb) -> str | None:
    """Look up the admin@test.com user via Auth Admin API list_users().

    supabase-py exposes list_users() (no email filter on older pins); we paginate
    through the results and find the matching email. Returns None if not found.
    """
    try:
        # supabase-py auth.admin.list_users() returns a list[User] (older API) or
        # an object with .users (newer API). Handle both shapes defensively.
        result = sb.auth.admin.list_users()
        users = getattr(result, "users", None) or result
        for u in users:
            if getattr(u, "email", None) == ADMIN_EMAIL:
                return getattr(u, "id", None)
    except Exception as e:
        print(f"WARN: list_users() lookup failed: {e}", file=sys.stderr)
    return None


def main() -> int:
    password = os.environ.get("TEST_USER_ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD)
    sb = _service_role_client()

    user_id: str | None = None
    try:
        # email_confirm=True skips the confirmation-email flow — important for a
        # programmatically-seeded test user where no real inbox exists.
        # Calls supabase.auth.admin.create_user via the service-role client `sb`.
        resp = sb.auth.admin.create_user({
            "email": ADMIN_EMAIL,
            "password": password,
            "email_confirm": True,
        })
        user = getattr(resp, "user", None) or resp
        user_id = getattr(user, "id", None)
        print(f"Created admin user: {ADMIN_EMAIL}")
    except Exception as e:
        msg = str(e).lower()
        # supabase-py / GoTrue surface "User already registered" / "email address has
        # already been registered" / "already exists" depending on version.
        if "already" in msg or "exists" in msg or "registered" in msg:
            print(f"Admin user {ADMIN_EMAIL} already exists — treating as success (idempotent).")
            user_id = _find_admin_user_id(sb)
        else:
            print(f"ERROR: create_user failed: {type(e).__name__}: {e}", file=sys.stderr)
            return 1

    # Best-effort UUID lookup so the operator can sanity-check against profiles.
    if user_id is None:
        user_id = _find_admin_user_id(sb)

    if user_id:
        print(f"Admin user UUID: {user_id}")
    else:
        print("WARN: could not resolve admin user UUID via list_users() lookup", file=sys.stderr)

    print(f"OK: {ADMIN_EMAIL} is provisioned (is_admin promotion happens in migration 021)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
