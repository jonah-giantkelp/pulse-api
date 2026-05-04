-- 009_ticket_pricing.sql
-- Add per-source pricing to event_external_ids.
-- Each platform may quote a different price for the same event.

alter table event_external_ids
    add column if not exists price_min  numeric,
    add column if not exists price_max  numeric,
    add column if not exists currency   text;

-- Backfill prices from existing raw_data where possible.
-- DICE: raw_data->'price'->>'amount' (cents) or 'amount_from'
-- Skiddle: raw_data->'ticketpricing'->>'minPrice' / 'maxPrice'
-- These run once; future events get prices at sync time.

update event_external_ids eid
set
    price_min = coalesce(
        (e.raw_data->'price'->>'amount')::numeric,
        (e.raw_data->'price'->>'amount_from')::numeric
    ) / 100.0,
    price_max = coalesce(
        (e.raw_data->'price'->>'amount')::numeric,
        (e.raw_data->'price'->>'amount_from')::numeric
    ) / 100.0,
    currency = e.raw_data->'price'->>'currency'
from events e
where eid.event_id = e.id
  and eid.source = 'dice'
  and e.source = 'dice'
  and e.raw_data->'price' is not null
  and coalesce(
      (e.raw_data->'price'->>'amount')::numeric,
      (e.raw_data->'price'->>'amount_from')::numeric
  ) is not null;

update event_external_ids eid
set
    price_min = (e.raw_data->'ticketpricing'->>'minPrice')::numeric,
    price_max = (e.raw_data->'ticketpricing'->>'maxPrice')::numeric,
    currency  = coalesce(e.raw_data->>'currency', 'GBP')
from events e
where eid.event_id = e.id
  and eid.source = 'skiddle'
  and e.source = 'skiddle'
  and e.raw_data->'ticketpricing' is not null
  and (e.raw_data->'ticketpricing'->>'minPrice')::numeric > 0;
