-- 011: Default email preferences row on signup
--
-- Auto-create a user_email_preferences row whenever a new auth.users row
-- is inserted, with default_cities defaulting to {'London'}. This way the
-- digest job and the events feed both have a non-empty location preference
-- to work with as soon as a user signs up.
--
-- Notes:
--   * The function is SECURITY DEFINER so it can write into a table that
--     has RLS enabled (the auth schema runs as a different role on signup).
--   * If auth.users.email is NULL (rare — some OAuth providers hide it),
--     we skip the insert rather than blocking the signup. The user can
--     populate email later via PUT /me/email-preferences.
--   * ON CONFLICT DO NOTHING makes the trigger idempotent.

CREATE OR REPLACE FUNCTION public.handle_new_user_email_prefs()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF NEW.email IS NOT NULL AND NEW.email <> '' THEN
        INSERT INTO public.user_email_preferences
            (user_id, email, default_cities)
        VALUES
            (NEW.id, NEW.email, ARRAY['London'])
        ON CONFLICT (user_id) DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created_email_prefs ON auth.users;

CREATE TRIGGER on_auth_user_created_email_prefs
    AFTER INSERT ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_new_user_email_prefs();

-- Backfill: give every existing auth user a prefs row (London default)
-- if they don't already have one.
INSERT INTO public.user_email_preferences (user_id, email, default_cities)
SELECT u.id, u.email, ARRAY['London']
FROM auth.users u
LEFT JOIN public.user_email_preferences p ON p.user_id = u.id
WHERE p.user_id IS NULL
  AND u.email IS NOT NULL
  AND u.email <> '';
