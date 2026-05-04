-- Add source_post_ids to events table
-- Stores platform post IDs (e.g. tweet IDs) that were analysed to produce this event
-- Used to skip already-analysed posts in future syncs
alter table events
    add column if not exists source_post_ids text[];
