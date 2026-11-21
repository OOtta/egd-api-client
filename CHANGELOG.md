# Changelog

## 0.1.0 – unreleased

- First version: `calc`, `prices`, `fetch`, `meters` commands.
- EG.D Distribuce24 OpenAPI client (unified `/rest/spotreby` and legacy C1 endpoint).
- OTE day-ahead prices (hourly CZK until 2025-09-30, 15-minute EUR from 2025-10-01) with
  ČNB conversion and local caching.
- CSV/XLSX consumption import with column, unit, timestamp-convention and DST detection.
- Multiplier, CZK/MWh surcharge, monthly fee, VAT, negative-price modes, fixed-price
  comparison, monthly/daily breakdown, CSV/JSON export.
