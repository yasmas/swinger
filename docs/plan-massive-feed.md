# Plan: Massive.com (Polygon.io) Data Feed Integration

## Goal

Replace Alpaca IEX as the equity data source for paper/live bots with
Massive.com (formerly Polygon.io) to get **extended hours 5m bars**
(pre-market 4 AM - after-hours 8 PM ET).

## Why

- Alpaca IEX only provides regular-hours bars (9:30 AM - 4 PM ET)
- Alpaca SIP requires $99/mo subscription
- Massive Starter plan ($29/mo) gives real-time SIP bars including extended hours
- Extended hours data is critical for the SwingParty strategy which needs to
  detect ST flips and enter positions before the regular session opens

## Architecture

Massive plugs into the existing `ExchangeClient` interface — same pattern as
`AlpacaRestClient` and `CoinbaseRestClient`. No changes to `DataManager`,
`SwingBot`, or `SwingPartyBot` are needed.

```
exchange/
  base.py              ExchangeClient ABC (unchanged)
  massive_rest.py      NEW — MassiveRestClient
  registry.py          Add "massive" entry
  alpaca_rest.py       (unchanged, still available)
```

## API Details

**Base URL:** `https://api.massive.com` (also `https://api.polygon.io`)

**Auth:** API key via query param `?apiKey=KEY` or header `Authorization: Bearer KEY`.
Key is read from env var `MASSIVE_API_KEY` or config `api_key`.

### Endpoints Used

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `fetch_ohlcv` | `GET /v2/aggs/ticker/{symbol}/range/{mult}/{timespan}/{from}/{to}` | 5m OHLCV bars |
| `get_current_price` | `GET /v2/last/trade/{symbol}` | Latest trade price |
| `get_best_bid_ask` | `GET /v3/quotes/{symbol}?limit=1&sort=desc` | Latest NBBO quote |

### Response Mapping (bars)

| Massive field | Our field |
|---------------|-----------|
| `t` | `open_time` (already ms) |
| `o` | `open` |
| `h` | `high` |
| `l` | `low` |
| `c` | `close` |
| `v` | `volume` |
| `t + interval_ms - 1` | `close_time` (computed) |

### Pagination

The bars endpoint returns up to 50,000 results per call (default 5,000).
If more bars are needed, response contains `next_url` — follow it for the
next page. For 5m bars, 50k limit covers ~170 trading days, so pagination
is rarely needed.

## Implementation Steps

### Step 1: Create `src/exchange/massive_rest.py`

New `MassiveRestClient(ExchangeClient)` class. Mirror the structure of
`alpaca_rest.py`:

- `__init__`: Read `MASSIVE_API_KEY` from env or config. Set up
  `requests.Session` with `Authorization: Bearer {key}` header.
  Base URL: `https://api.massive.com`.
- `fetch_ohlcv(symbol, interval, start_time_ms, end_time_ms, limit)`:
  - Map interval strings: `"5m"` -> `multiplier=5, timespan="minute"`, etc.
  - Convert ms timestamps to date strings or pass as ms (API accepts both).
  - Call `/v2/aggs/ticker/{symbol}/range/{mult}/{timespan}/{from}/{to}`
    with `sort=asc`, `limit=min(limit, 50000)`, `adjusted=true`.
  - Handle pagination via `next_url` if present.
  - Map response `results[]` array to DataFrame matching our schema
    (`open_time`, `open`, `high`, `low`, `close`, `volume`, `close_time`).
- `get_current_price(symbol)`:
  - Call `/v2/last/trade/{symbol}`.
  - Return `results.p` as float.
- `get_best_bid_ask(symbol)`:
  - Call `/v3/quotes/{symbol}?limit=1&sort=desc`.
  - Return dict with `bid_price`, `bid_qty`, `ask_price`, `ask_qty`.
- Retry logic: same pattern as Alpaca (exponential backoff, 3 retries).
- Rate limiting: Starter plan has generous limits; add a small sleep if
  we hit 429 responses.

### Step 2: Register in `src/exchange/registry.py`

```python
from exchange.massive_rest import MassiveRestClient

EXCHANGE_REGISTRY = {
    "alpaca":   AlpacaRestClient,
    "binance":  BinanceRestClient,
    "coinbase": CoinbaseRestClient,
    "massive":  MassiveRestClient,    # NEW
}
```

### Step 3: Validate the API manually

Before switching any bot, run a manual smoke test to confirm the API key
works and extended hours data is returned:

```bash
PYTHONPATH=src python3 -c "
from exchange.massive_rest import MassiveRestClient
import pandas as pd

client = MassiveRestClient({})

# Test 1: fetch_ohlcv — yesterday's TSLA 5m bars including extended hours
yesterday = (pd.Timestamp.now() - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
df = client.fetch_ohlcv('TSLA', '5m',
    start_time_ms=int(pd.Timestamp(f'{yesterday}T08:00:00Z').timestamp()*1000),
    end_time_ms=int(pd.Timestamp(f'{yesterday}T23:59:00Z').timestamp()*1000),
    limit=500)
print(f'fetch_ohlcv: {len(df)} bars')
df['dt'] = pd.to_datetime(df['open_time'], unit='ms', utc=True).dt.tz_convert('US/Eastern')
print(f'  First: {df.iloc[0][\"dt\"]}')
print(f'  Last:  {df.iloc[-1][\"dt\"]}')
# Expect first bar ~4:00 AM ET, last bar ~7:55 PM ET

# Test 2: get_current_price
price = client.get_current_price('TSLA')
print(f'get_current_price: {price}')

# Test 3: get_best_bid_ask
bba = client.get_best_bid_ask('TSLA')
print(f'get_best_bid_ask: bid={bba[\"bid_price\"]} ask={bba[\"ask_price\"]}')
"
```

Only proceed to Step 4 if all three calls succeed and the bars cover
the extended hours window (4 AM - 8 PM ET).

### Step 4: Update bot configs (only after Step 3 passes)

Change the `exchange` block in bot YAML files to use Massive:

```yaml
# Before (Alpaca IEX, no extended hours):
exchange:
  type: alpaca
  feed: iex

# After (Massive, with extended hours):
exchange:
  type: massive
```

Files to update:
- `data/yasmas/paper_tsla.yaml`
- `data/yasmas/paper_swing_party.yaml`
- Any future paper/live equity bot configs

No code changes needed in `DataManager`, `SwingBot`, or `SwingPartyBot` —
they all go through the `ExchangeClient` interface.

### Step 5: Update `download_swing_party_day.py`

Add `--provider massive` option alongside existing `alpaca` and `databento`:

- Instantiate `MassiveRestClient` and call `fetch_ohlcv` the same way
  the Alpaca provider does.
- This gives backtest downloads with extended hours data (previously only
  available via Databento for historical data).

### Step 6: Verify extended hours coverage

After switching, confirm the bot receives bars outside regular hours:
- Check that 5m bars appear starting from 4 AM ET (08:00 UTC)
- Check that bars continue until 8 PM ET (00:00 UTC next day)
- Verify gap backfill works correctly with the new feed
- Run a paper trading session spanning pre-market through after-hours

## Testing

1. **Unit test:** Call `MassiveRestClient.fetch_ohlcv("TSLA", "5m", ...)` for
   a known date and verify bars span 4 AM - 8 PM ET.
2. **Integration test:** Start paper bot with `exchange.type: massive`, let it
   run through a market open, confirm bars appear in the CSV.
3. **Gap recovery test:** Kill the bot for 15 min, restart, confirm backfill
   fetches bars from Massive and fills the gap.

## Rollback

If Massive has issues, switch back to Alpaca by changing `type: massive` to
`type: alpaca` in the bot YAML. Both clients implement the same interface.

## Cost

Massive Starter plan: ~$29/month. Data cost per API call is negligible.
One bot polling every 5 min for 4 symbols = ~1,150 calls/day, well within
limits.
