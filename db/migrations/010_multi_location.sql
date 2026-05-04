-- 010: Multi-location support
-- Allow users to track events across multiple cities/countries instead of
-- defaulting everything to London.

-- 1. Add default location arrays to user_email_preferences
ALTER TABLE user_email_preferences
    ADD COLUMN default_cities text[] NOT NULL DEFAULT '{}',
    ADD COLUMN default_countries text[] NOT NULL DEFAULT '{}';

-- 2. Drop the 'London' default on events.city — keep NOT NULL, default to 'Unknown'
ALTER TABLE events
    ALTER COLUMN city SET DEFAULT 'Unknown';

-- 3. Make user_artists.city nullable (NULL = all cities)
ALTER TABLE user_artists
    ALTER COLUMN city DROP NOT NULL,
    ALTER COLUMN city SET DEFAULT NULL;
