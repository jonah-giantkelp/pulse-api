-- Add MusicBrainz ID to artists table
alter table artists add column if not exists musicbrainz_id text unique;

-- Allow 'musicbrainz' as a resolved_by value in artist_resolutions
alter table artist_resolutions drop constraint if exists artist_resolutions_resolved_by_check;
alter table artist_resolutions
    add constraint artist_resolutions_resolved_by_check
    check (resolved_by in ('ai', 'user', 'musicbrainz'));
