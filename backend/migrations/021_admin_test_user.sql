-- Migration 021: Admin test user promotion (Phase 6 / D-02)
-- COMPANION to backend/scripts/seed_admin_user.py — that script must run FIRST to create
-- the auth.users row (Supabase Admin API; bcrypt-hashed password). This migration only
-- handles the profile-level promotion, which IS expressible in pure SQL.
--
-- IDEMPOTENT: re-running this migration is a no-op once is_admin is already true.
-- Safety net: if the seed script has NOT been run, RAISE EXCEPTION fails loudly so the
-- operator gets a clear, actionable error pointing at the right runbook step.

DO $$
DECLARE
    v_user_id UUID;
BEGIN
    -- Look up the seeded admin user in auth.users (table managed by Supabase Auth).
    SELECT id INTO v_user_id
    FROM auth.users
    WHERE email = 'admin@test.com'
    LIMIT 1;

    IF v_user_id IS NULL THEN
        RAISE EXCEPTION 'Migration 021: admin@test.com not found in auth.users. Run `cd backend && venv/Scripts/python scripts/seed_admin_user.py` FIRST, then re-apply migrations.';
    END IF;

    -- Promote to admin (idempotent — safe to re-run).
    UPDATE public.profiles
    SET is_admin = true
    WHERE id = v_user_id;

    -- Defensive: in some test environments the handle_new_user trigger may not have
    -- fired (e.g., user provisioned via Admin API before Migration 005 was applied).
    -- Upsert the profile row to guarantee the promotion lands either way.
    INSERT INTO public.profiles (id, email, is_admin)
    VALUES (v_user_id, 'admin@test.com', true)
    ON CONFLICT (id) DO UPDATE SET is_admin = true;

    RAISE NOTICE 'Migration 021: admin@test.com (id=%) promoted to is_admin=true', v_user_id;
END $$;
