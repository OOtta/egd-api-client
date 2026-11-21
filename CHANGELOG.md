# Changelog

## 0.1.0 – unreleased

Fixes after testing against the live EG.D API:

- `to` is exclusive and timestamps mark interval starts → the last value of every 30-day
  chunk was previously lost; `to` is now sent as `…:59.999Z` before the chunk end.
- Paging parameters removed (`pageStart=1` silently dropped the first value of a chunk);
  truncated responses are continued from the last timestamp instead.
- Period is clamped to yesterday (EG.D rejects later `to`) and to the first day the account
  has access to (HTTP 400 "nemáte oprávnění" → binary search, warning).
- Default profile follows the measurement type (`ICC1` for A/B, `DCQC` for C1).
- EG.D error messages are shown with an English hint; final substitute statuses
  (B, E, M, N) no longer trigger warnings.

- First version: `calc`, `prices`, `fetch`, `meters` commands.
- EG.D Distribuce24 OpenAPI client (unified `/rest/spotreby` and legacy C1 endpoint).
- OTE day-ahead prices (hourly CZK until 2025-09-30, 15-minute EUR from 2025-10-01) with
  ČNB conversion and local caching.
- CSV/XLSX consumption import with column, unit, timestamp-convention and DST detection.
- Multiplier, CZK/MWh surcharge, monthly fee, VAT, negative-price modes, fixed-price
  comparison, monthly/daily breakdown, CSV/JSON export.
