-- Migration 005: Profiles and Global Settings
-- Adds user profiles with admin flag and a single-row global settings table.

-- ── Profiles table ──

CREATE TABLE IF NOT EXISTS public.profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    is_admin BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

-- Users can read their own profile
CREATE POLICY "Users read own profile"
    ON public.profiles FOR SELECT
    USING (auth.uid() = id);

-- Admins can read all profiles
CREATE POLICY "Admins read all profiles"
    ON public.profiles FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.profiles p
            WHERE p.id = auth.uid() AND p.is_admin = true
        )
    );

-- Users can update their own profile (non-admin fields only handled in app)
CREATE POLICY "Users update own profile"
    ON public.profiles FOR UPDATE
    USING (auth.uid() = id);

-- ── Auto-create profile on signup ──

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    INSERT INTO public.profiles (id, email)
    VALUES (NEW.id, NEW.email)
    ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END;
$$;

-- Drop trigger if it already exists, then create
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- ── Backfill existing users ──

INSERT INTO public.profiles (id, email)
SELECT id, email FROM auth.users
ON CONFLICT (id) DO NOTHING;

-- ── Global Settings table (single-row) ──

CREATE TABLE IF NOT EXISTS public.global_settings (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    llm_api_key TEXT,
    llm_model TEXT DEFAULT 'gemini-3-flash-preview',
    langsmith_api_key TEXT,
    langsmith_project TEXT,
    langsmith_tracing BOOLEAN DEFAULT true,
    updated_at TIMESTAMPTZ DEFAULT now(),
    updated_by UUID REFERENCES auth.users(id)
);

ALTER TABLE public.global_settings ENABLE ROW LEVEL SECURITY;

-- All authenticated users can read settings
CREATE POLICY "Authenticated users read settings"
    ON public.global_settings FOR SELECT
    TO authenticated
    USING (true);

-- Only admins can update settings
CREATE POLICY "Admins update settings"
    ON public.global_settings FOR UPDATE
    USING (
        EXISTS (
            SELECT 1 FROM public.profiles
            WHERE id = auth.uid() AND is_admin = true
        )
    );

-- Seed the single settings row
INSERT INTO public.global_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- ── Grant service role access ──

GRANT SELECT ON public.profiles TO service_role;
GRANT SELECT ON public.global_settings TO service_role;
GRANT UPDATE ON public.global_settings TO service_role;
