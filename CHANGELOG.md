# Changelog

## 0.1.0 – unreleased

First version, published as **egd-api-client** (command `egd`).

- General EG.D Distribuce24 OpenAPI client (`egd_api_client.EgdClient`): OAuth client
  credentials, metering points, profiles, 15-minute readings (unified `/rest/spotreby` and
  legacy C1 endpoint) for consumption and production.
- Core commands: `egd meters`, `egd profiles`, `egd data` (consumption / production export
  to CSV or JSON).
- Spot-tariff feature as its own subpackage and command group (`egd_api_client.spot`,
  `egd spot calc`, `egd spot prices`, `egd spot fx`):
  - OTE day-ahead prices (hourly CZK until 2025-09-30, 15-minute EUR from 2025-10-01) with
    local caching;
  - EUR → CZK with the ČNB rate valid for each delivery day (fixing of that day, or the last
    one before it on weekends/holidays) – identical to OTE's own `EurRate`; `egd spot fx`
    lists the rate and fixing date per day;
  - multiplier, CZK/MWh surcharge, monthly fee, VAT, negative-price modes, fixed-price
    comparison, monthly/daily breakdown, CSV/JSON export.
- CSV/XLSX consumption import with column, unit, timestamp-convention and DST detection.
- Config file `~/.config/egd-api-client/config.toml` with `[egd]`, `[spot]`, `[spot.fixed]`
  sections; environment variables `EGD_CLIENT_ID`, `EGD_CLIENT_SECRET`, `EGD_EAN`,
  `EGD_CONFIG`, `EGD_CACHE_DIR`.

Fixes after testing against the live EG.D API:

- `to` is exclusive and timestamps mark interval starts → the last value of every 30-day
  chunk was previously lost; `to` is now sent as `…:59.999Z` before the chunk end.
- Paging parameters removed (`pageStart=1` silently dropped the first value of a chunk);
  truncated responses are continued from the last timestamp instead.
- Period is clamped to yesterday (EG.D rejects later `to`) and to the first day the account
  has access to (HTTP 400 "nemáte oprávnění" → binary search, warning).
- Default profile follows the measurement type (`ICC1`/`ISC1` for A/B, `DCQC`/`DSQC` for C1).
- EG.D error messages are shown with an English hint; final substitute statuses
  (B, E, M, N) no longer trigger warnings.
