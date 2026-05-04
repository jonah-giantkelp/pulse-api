# Ticket Price Audit — Live `raw_data` Analysis

Audit of actual price data found in Supabase `events.raw_data` across all
sources, based on live database queries (2026-04-14).

---

## Summary

| Source | Has price? | Field path in `raw_data` | Format | Currency |
|---|---|---|---|---|
| **DICE** | Yes | `price.amount` / `price.amount_from` | Cents (int) | `price.currency` (USD/EUR/GBP) |
| **Skiddle** | Yes | `ticketpricing.minPrice` / `.maxPrice` | Pounds (float) | `currency` (GBP/EUR) |
| **Ticketmaster** | No | `priceRanges` **not present** in any sample | — | — |
| **Bandsintown** | Partial | `isFree` (bool), `hasTickets` (bool) | Boolean only | — |
| **RA** | No | — | — | — |
| **Concerts Tracker** | No data | No events in DB | — | — |
| **Website** | No data | No events in DB | — | — |

---

## Detailed Findings

### 1. DICE — confirmed, reliable pricing

Every DICE event has a top-level `price` object:

```json
{
  "price": {
    "amount": 2364,
    "currency": "EUR",
    "sales_tax": 0,
    "amount_from": null
  }
}
```

**Rules:**
- `amount` = fixed price in **cents/pence** (2364 = €23.64). Can be `null`.
- `amount_from` = "from" price in cents when there are tiers (e.g. 1500 = $15.00). Can be `null`.
- Use `amount` when present, fall back to `amount_from`.
- Divide by 100 to get the human-readable price.
- `currency` is ISO 4217 (USD, EUR, GBP).
- `sales_tax` is included in `amount` — no need to add it.

**Live samples:**

| Event | amount | amount_from | currency |
|---|---|---|---|
| DJ Seinfeld (Miami) | null | 1500 | USD |
| DJ SEINFELD (EU) | 2364 | null | EUR |
| Steel City | 6000 | null | EUR |
| DJ Assault + DJ Paypal | 4136 | null | USD |
| Festival Plein Air 2026 | 5876 | null | EUR |

**Extraction logic:**
```python
price = raw_data.get("price", {})
amount_cents = price.get("amount") or price.get("amount_from")
price_value = amount_cents / 100 if amount_cents else None
currency = price.get("currency")  # "GBP", "EUR", "USD"
```

---

### 2. Skiddle — confirmed, reliable pricing

Skiddle has two price fields. `ticketpricing` is the reliable one:

```json
{
  "ticketpricing": {
    "minPrice": 43.5,
    "maxPrice": 43.5
  },
  "entryprice": "",
  "currency": "GBP"
}
```

**Rules:**
- `ticketpricing.minPrice` / `.maxPrice` are in **whole currency units** (43.5 = £43.50).
- `entryprice` is often empty string `""` or `"0"` — **do not rely on it**.
- `ticketpricing` can be `null` — treat as no price available.
- Values of `0` likely mean "free" or "not set" — filter with caution.
- `currency` is at the event root level.

**Live samples:**

| Event | minPrice | maxPrice | currency | entryprice |
|---|---|---|---|---|
| Caribou | 43.5 | 43.5 | GBP | "" |
| Blue Labs Beats | 0 | 0 | GBP | "" |
| Big Thief | 41.2 | 41.2 | GBP | "" |
| Scissor Sisters | null | null | EUR | "0" |
| Mamma Mia! | 0 | 0 | GBP | "" |

**Extraction logic:**
```python
tp = raw_data.get("ticketpricing") or {}
min_price = tp.get("minPrice")
max_price = tp.get("maxPrice")
currency = raw_data.get("currency", "GBP")
# Filter out 0 values (likely means "not set")
if min_price == 0 and max_price == 0:
    min_price = max_price = None
```

---

### 3. Ticketmaster — no pricing in stored data

Despite the API documentation listing `priceRanges`, **none of the 10
Ticketmaster events sampled contain this field**. The stored `raw_data` keys
are: `_embedded`, `_links`, `accessibility`, `ageRestrictions`,
`classifications`, `dates`, `id`, `images`, `info`, `name`, `promoter`,
`sales`, `seatmap`, `ticketing`, `url`.

The `ticketing` object contains only `safeTix` and `allInclusivePricing`
(a boolean), not actual prices.

**Why?** Ticketmaster's Discovery API only includes `priceRanges` for
certain markets/events. UK events frequently omit it. We'd need to either:
- Request with `includePriceRanges=true` (not a real param — they just don't provide it)
- Hit a different endpoint (e.g. event detail by ID)
- Accept Ticketmaster won't give us prices

**Conclusion:** Not extractable from current data.

---

### 4. Bandsintown — free/paid flag only

No price amounts, but two useful booleans:

```json
{
  "isFree": false,
  "hasTickets": true
}
```

All 5 sampled events: `isFree: false`, `hasTickets: true`.

Useful as metadata but not for showing a price.

---

### 5. Resident Advisor — no pricing

Confirmed from live data. The GraphQL response keys are:
`artists`, `contentUrl`, `date`, `flyerFront`, `id`, `startTime`, `title`, `venue`.

No price-related fields at all.

---

### 6. Concerts Tracker / Website — no events in DB

No events currently stored from these sources, so nothing to audit.

---

## Recommended Schema Addition

```sql
ALTER TABLE events
  ADD COLUMN price_min  NUMERIC,       -- in whole currency units (e.g. 25.00)
  ADD COLUMN price_max  NUMERIC,       -- null if single-price (= price_min)
  ADD COLUMN currency   TEXT;           -- ISO 4217: GBP, EUR, USD
```

**For the frontend:**
- If `price_min` and `price_max` are equal → show "£25"
- If different → show "£25–55"
- If `price_min` is null → show "Price TBC" or hide
- Use `currency` for the symbol (GBP → £, EUR → €, USD → $)
- Bandsintown's `isFree` could set `price_min = 0` with a "Free" label

## Recommended `EventResult` Change

```python
@dataclass
class EventResult:
    # ... existing fields ...
    price_min: float | None = None   # NEW — whole units (25.0 = £25)
    price_max: float | None = None   # NEW
    currency: str | None = None      # NEW — ISO 4217
```

## Extraction by Source (for `_event_dict`)

```python
def _extract_price(source: str, raw: dict) -> tuple[float|None, float|None, str|None]:
    """Return (price_min, price_max, currency) from raw_data."""
    if source == "dice":
        p = raw.get("price", {})
        cents = p.get("amount") or p.get("amount_from")
        if cents:
            val = cents / 100
            return (val, val, p.get("currency"))
        return (None, None, p.get("currency"))

    if source == "skiddle":
        tp = raw.get("ticketpricing") or {}
        lo, hi = tp.get("minPrice"), tp.get("maxPrice")
        cur = raw.get("currency", "GBP")
        if lo == 0 and hi == 0:
            return (None, None, cur)
        return (lo or None, hi or None, cur)

    if source == "bandsintown":
        if raw.get("isFree"):
            return (0, 0, "GBP")
        return (None, None, None)

    # ticketmaster, ra, concerts_tracker, website — no price data
    return (None, None, None)
```
