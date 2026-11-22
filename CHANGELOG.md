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
- `egd spot calc`: separate coefficients for negative spot prices
  (`--negative-multiplier`, `--negative-add-czk-mwh`; fall back to `-m` / `-a`), consistent
  with the `--negative` clamp modes.
- `egd spot calc`: two-rate fixed comparison (`--fixed-price` = VT, `--fixed-price-low` = NT)
  with NT periods entered by the user only (`--low-tariff-hours`,
  `--low-tariff-hours-weekend`, `--holidays weekday|weekend`, config `[low_tariff]`); NT
  consumption in the report. EG.D's OpenAPI provides no NT/HDO data, and nothing is
  downloaded or assumed.
- `egd spot prices --currency czk|eur` (required); EUR uses OTE's own EUR prices in the hourly
  era as well.
- `egd spot fx` gained `-o/--export` (CSV/JSON) and a summary, like `egd spot prices`.
- User-supplied distribution charges for both spot and fixed results:
  `--distribution-high`, `--distribution-low` (needs `--low-tariff-hours`),
  `--distribution-monthly-fee`, config `[distribution]`; report lines `Spot + distrib.` /
  `Fixed + distrib.`, breakdown and export columns. EG.D's OpenAPI provides no distribution
  rate, breaker or fee data and no price tables are built in.
- `--json` output is a single JSON object on stdout and nothing on stderr: progress
  messages are suppressed and all warnings go into a `warnings` array (always present).
  Errors in JSON mode print `{"error": ..., "warnings": [...]}` to stdout (exit code 2).
  JSON files written with `-o` also carry `warnings`. **Changed structure** (previously
  top-level lists): `egd meters --json` → `{"metering_points": [...]}`, `egd profiles
  --json` → `{"profiles": [...]}`, `egd data` JSON → object with `readings`,
  `egd spot prices -o *.json` → `{"currency", "unit", "prices": [...]}`, `egd spot fx -o
  *.json` → `{"rates": [...]}`. `egd spot calc` JSON gains `source`.
- `egd spot calc` shows the average spot price with the tariff applied (`Spot + tariff`:
  coefficients incl. negative-price handling, CZK/MWh excl. VAT and fees; weighted = energy cost /
  consumption, plus the simple average of the period), directly comparable with the fixed
  price; `Spot+tariff` column, `avg_spot_tariff_czk_mwh` /
  `avg_spot_tariff_time_weighted_czk_mwh` in CSV/JSON. Raw OTE averages are labelled as such.
- `egd spot calc` shows the average fixed price next to the average spot price when a
  fixed tariff is given: consumption-weighted and simple (time) average in CZK/MWh,
  excl. VAT and fees; `Fixed avg` column in
  the breakdown, `avg_fixed_czk_mwh` / `avg_fixed_time_weighted_czk_mwh` in CSV/JSON.
- Documentation in Czech (`README.md`).
- Windows: console output with Czech characters (e.g. `ČNB`) no longer fails on a
  redirected/piped cp1252 stdout – such streams are switched to UTF-8.
- `tzdata` is now an unconditional dependency, so `egd` works where the OS has no
  time-zone database (Termux/Android, minimal containers), with a clear hint if the zone
  still cannot be loaded; Python 3.14 added to CI and classifiers.
- Persistent cache of OTE prices and ČNB rates per delivery day, so repeated runs download
  nothing: only days missing from the cache are fetched (in one-month requests, never mixing
  the hourly and 15-minute regimes). Only complete past days are stored – the exact period
  count for that day incl. DST (23/24/25 hours, 92/96/100 quarter-hours); today, future and
  incomplete days are never cached. Raw OTE values are stored with their currency and
  resolution (`PT60M`/CZK before 2025-10, `PT15M`/EUR after), CZK conversion happens on every
  run. Versioned files (`ote/YYYY-MM.json`, `cnb/YYYY.json`, `"format": 2`; older files are
  migrated), atomic writes, a corrupted file is re-downloaded with a warning (in `warnings`
  with `--json`). ČNB years fetched after the year ended are never re-downloaded; the current
  year is refreshed at most once per day and only when a newer fixing is needed. Cache dir:
  `$EGD_CACHE_DIR`, `%LOCALAPPDATA%\egd-api-client` on Windows, else
  `$XDG_CACHE_HOME/egd-api-client` (`~/.cache/egd-api-client`, also on Termux).
- `--refresh-cache` (download again and update the cache) next to `--no-cache`; new
  `egd cache info` / `egd cache clear [--ote] [--cnb]` (both with `--json`). `egd spot
  calc|prices|fx` print how many days came from the cache vs. were downloaded (suppressed
  with `--json`; the JSON output has a `cache` object with the counts).
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
