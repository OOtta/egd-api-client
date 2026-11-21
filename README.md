# egd-api-client

**A Python client and command-line tool for the EG.D (E.ON Distribuce) Distribuce24 OpenAPI.**

`egd` downloads your historical 15-minute smart-meter data for metering points in the
[EG.D](https://www.egd.cz) distribution area of the Czech Republic – and does useful things
with it. The first feature built on top of the client is the **spot-tariff calculator**:

* `egd meters`, `egd data` – list your metering points, export consumption or production
  (feed-in) data to CSV / JSON;
* `egd spot calc` – what would your electricity have cost on a spot-price tariff? Downloads
  the **OTE-CR day-ahead prices** by itself (hourly before 1 Oct 2025, 15-minute since,
  converted with the official **ČNB** rate of each delivery day) and applies your supplier's
  formula – `spot × 1.15`, `spot + 400 CZK/MWh`, monthly fee, VAT – optionally per month or
  day and compared with a fixed-price tariff;
* `egd spot prices`, `egd spot fx` – the prices (in CZK or EUR) and the EUR/CZK rates;
* VT/NT fixed-price comparison in `egd spot calc`, with the low-tariff (NT) hours entered by
  you.

The same functionality is available as a library (`egd_api_client.EgdClient`,
`egd_api_client.spot`), so more features can be added as further sub-commands.

> 🇨🇿 Česky: [README.cs.md](README.cs.md)

## Contents

- [Installation](#installation)
- [Quick start](#quick-start)
- [Getting EG.D API access](#getting-egd-api-access)
- [Configuration](#configuration)
- [Commands](#commands)
- [Using it as a library](#using-it-as-a-library)
- [How the cost is calculated](#how-the-cost-is-calculated)
- [EUR/CZK conversion](#eurczk-conversion)
- [Low tariff (NT)](#low-tariff-nt)
- [Distribution fees](#distribution-fees)
- [Data sources](#data-sources)
- [Input files](#input-files)
- [Limitations](#limitations)
- [Development](#development)
- [License](#license)

## Installation

Python 3.11+ is required. The recommended way is [pipx](https://pipx.pypa.io):

```bash
pipx install "git+https://github.com/OWNER/egd-api-client"                     # API + CSV
pipx install "egd-api-client[xlsx] @ git+https://github.com/OWNER/egd-api-client"  # + XLSX input
```

From a local checkout: `pipx install ".[xlsx]"` (or `pip install -e ".[dev]"` for development).

## Quick start

With your EG.D API credentials:

```bash
export EGD_CLIENT_ID=...  EGD_CLIENT_SECRET=...
egd meters                                         # your metering points (EANs)
egd data --ean 859182400xxxxxxxxx --from 2026-10 -o october.csv
egd spot calc --ean 859182400xxxxxxxxx --from 2025-01 --to 2025-12 \
    -m 1.15 -a 400 --vat --breakdown month
```

Try the calculator without any credentials using the bundled synthetic consumption file:

```bash
egd spot calc --input examples/sample_consumption.csv \
    --multiplier 1.15 --add-czk-mwh 400 --monthly-fee 150 --vat \
    --fixed-price 3.5 --fixed-monthly-fee 150 --breakdown month
```

```text
Period:            2025-10-01 – 2025-11-30 (Europe/Prague)
Tariff:            spot × 1.15 + 400 CZK/MWh, fee 150 CZK/month, VAT 21 %
Data coverage:     5860/5860 quarter-hours (100.0 %)

Consumption:       865.107 kWh
Avg spot price:    2 860.82 CZK/MWh (weighted by your consumption)
                   2 510.70 CZK/MWh (simple average of the period)
At negative price: 3.523 kWh

Energy (spot):     3 192.20 CZK
Monthly fee(s):    300.00 CZK
VAT 21 %:          733.36 CZK
TOTAL:             4 225.56 CZK
Effective price:   4.8844 CZK/kWh (incl. VAT)

Fixed tariff:      4 026.73 CZK (3.5 CZK/kWh + 150 CZK/month)
Difference:        spot would have been MORE EXPENSIVE by 198.83 CZK

Note: distribution charges (regulated EG.D fees, POZE, system services, ...) are
the same for any supplier and are NOT included unless you enter them with
--distribution-high / --distribution-low / --distribution-monthly-fee.

Period       kWh  Spot avg    Energy     Fee     Total  CZK/kWh     Fixed    Saved
----------------------------------------------------------------------------------
2025-10  437.960  2 787.20  1 578.97  150.00  2 092.05   4.7768  2 036.26   -55.79
2025-11  427.147  2 936.31  1 613.23  150.00  2 133.51   4.9948  1 990.47  -143.04
(Spot avg in CZK/MWh, consumption-weighted; money in CZK; Energy and Fee excl. VAT; Total, Fixed and Saved incl. VAT)
```

(`examples/sample_consumption.csv` is synthetic; the prices are the real OTE prices.)

## Getting EG.D API access

The EG.D "Vzdálený přístup – OpenAPI" is free, but you have to enable it yourself:

1. Register / log in at [portal.distribuce24.cz](https://portal.distribuce24.cz)
   (or the Distribuce24 mobile app) and link your metering point (EAN).
2. Go to **Správa účtů → Vzdálený přístup – OpenAPI** and click
   **„Vygenerovat client_id a client_secret“** (validity 1, 3 or 10 years).
3. Put the values into environment variables or the config file (see below).

The API serves metering points with **measurement type A, B or C1** – C1 means an active
smart meter with 15-minute data. Type C4 (no interval metering) has no data.
EG.D updates the data once a day; please do not run the tool in a tight loop.

No API access? Export the data from the portal (*Historie spotřeb → export .xlsx*) and use
`egd spot calc --input` instead.

## Configuration

Settings are read from (later wins):

1. the config file – `~/.config/egd-api-client/config.toml` (Linux/macOS),
   `%APPDATA%\egd-api-client\config.toml` (Windows), or `--config FILE` / `EGD_CONFIG`;
2. environment variables `EGD_CLIENT_ID`, `EGD_CLIENT_SECRET`, `EGD_EAN`;
3. command-line options.

The file has an `[egd]` section (credentials, EAN, API options), `[spot]` (spot formula),
`[spot.fixed]` (fixed comparison tariff), `[low_tariff]` (NT hours) and `[distribution]`
(distribution charges); see [`examples/config.example.toml`](examples/config.example.toml).
If you store the secret in the file, `chmod 600` it – the tool warns otherwise. Secrets are
never printed or cached; the daily access token is kept in memory only.

Downloaded prices and exchange rates are cached in `~/.cache/egd-api-client` (override with
`--cache-dir` / `EGD_CACHE_DIR`, disable with `--no-cache`). Only complete days are cached.
Metering data are never cached.

## Commands

| Command | Purpose |
|---|---|
| `egd meters` | list metering points available to your API credentials |
| `egd profiles` | list EG.D profile codes |
| `egd data` | download 15-minute consumption (`--kind consumption`, default) or production (`--kind production`) to CSV or JSON |
| `egd spot calc` | compute the spot-tariff cost of your consumption (API or file) |
| `egd spot prices --currency czk\|eur` | download / summarise / export OTE spot prices (`-o .csv/.json`); `--currency` is required |
| `egd spot fx` | the ČNB EUR/CZK rate applied to each delivery day (`-o .csv/.json`, `--json`) |

Run `egd COMMAND --help` / `egd spot COMMAND --help` for all options. Common options:
`--from`, `--to` (local Czech dates, inclusive; `YYYY-MM-DD` or `YYYY-MM` = whole month),
`--ean`, `--profile`, `--json`, `--config`, `--cache-dir`, `--no-cache`.

The most important options of `egd spot calc`:

| Option | Meaning |
|---|---|
| `--ean` | metering point (uses the API); with `--input` the period defaults to the file's range |
| `-i, --input FILE` | CSV/XLSX consumption file instead of the API |
| `-m, --multiplier X` | spot price multiplier, e.g. `1.15` |
| `-a, --add-czk-mwh X` | fixed surcharge in CZK/MWh, e.g. `400` (negative values allowed) |
| `--monthly-fee CZK` | supplier's fixed monthly fee (prorated by days) |
| `--vat [PCT]` | add VAT; `--vat` alone means 21 % |
| `--negative-multiplier X` | multiplier for periods with a negative spot price (default: `-m`) |
| `--negative-add-czk-mwh X` | surcharge for periods with a negative spot price (default: `-a`) |
| `--negative MODE` | negative price handling: `as-is` (default), `clamp-spot`, `clamp-total` – see below |
| `--price-resolution` | `auto` (15-min prices where they exist) or `hourly` |
| `--fixed-price CZK_KWH` | compare with a fixed price per kWh (excl. VAT); the VT price of a two-rate product |
| `--fixed-price-low CZK_KWH` | NT price of the fixed product; requires `--low-tariff-hours` |
| `--low-tariff-hours RANGES` | your NT hours, e.g. `"22:00-06:00,13:00-15:00"` (every day) |
| `--low-tariff-hours-weekend RANGES` | different NT hours for Saturday and Sunday |
| `--holidays weekday\|sunday` | public holidays use their weekday's hours (default) or the weekend hours |
| `--distribution-high CZK_KWH` | your distribution charge per kWh in VT, or for all consumption if no NT charge is given |
| `--distribution-low CZK_KWH` | your distribution charge per kWh in NT; requires `--low-tariff-hours` |
| `--distribution-monthly-fee CZK` | your fixed monthly distribution charges (circuit breaker etc.) |
| `--fixed-monthly-fee CZK` | monthly fee of the fixed tariff |
| `-b, --breakdown` | `none`, `month` or `day` |
| `-o, --export FILE` | write the result to `.json` (full) or `.csv` (breakdown rows) |
| `--intervals FILE` | write every 15-minute interval with its price and cost to CSV |
| `--json` | print JSON to stdout instead of the text report |

More examples:

```bash
# Export a year of consumption and of feed-in (production) data
egd data --ean 8591824... --from 2025-01 --to 2025-12 -o consumption-2025.csv
egd data --ean 8591824... --kind production --from 2025-06 -o feed-in-2025-06.json

# A whole year, monthly breakdown, exported to JSON
egd spot calc --ean 8591824... --from 2025-01 --to 2025-12 -m 1.1 -a 300 -b month -o 2025.json

# Download once, then experiment offline with different tariffs
egd data --ean 8591824... --from 2025-01 --to 2025-12 -o 2025.csv
egd spot calc -i 2025.csv -m 1.15 && egd spot calc -i 2025.csv -a 500

# Supplier bills hourly prices even after the switch to 15-minute trading
egd spot calc -i export.xlsx -a 450 --price-resolution hourly

# Never get paid for consumption at negative prices
egd spot calc -i export.csv -a 400 --negative clamp-total

# Supplier: spot × 1.15 + 400, but at negative prices only the spot itself
egd spot calc -i export.csv -m 1.15 -a 400 --negative-multiplier 1 --negative-add-czk-mwh 0

# Compare with a two-rate fixed product (VT 4.20 / NT 2.90 CZK/kWh), NT 22–06 h + 13–15 h
egd spot calc -i export.csv -a 400 --fixed-price 4.2 --fixed-price-low 2.9 \
    --low-tariff-hours "22:00-06:00,13:00-15:00"

# Add your own distribution charges (example values!) to both spot and fixed totals
egd spot calc -i export.csv -m 1.15 -a 400 --vat --fixed-price 4.0 --fixed-price-low 3.0 \
    --low-tariff-hours "22:00-06:00" --distribution-high 2.0 --distribution-low 0.5 \
    --distribution-monthly-fee 300 -b month

# Just the prices (currency must be chosen), and the exchange rates used for them
egd spot prices --from 2025-11 --currency czk --export prices-2025-11.csv
egd spot prices --from 2025-11 --currency eur --export prices-2025-11-eur.json
egd spot fx --from 2026-10-24 --to 2026-11-02 -o fx.csv
```

## Using it as a library

```python
from datetime import date
from egd_api_client import EgdClient, EgdCredentials, make_session
from egd_api_client.timeutil import local_day_bounds_utc

client = EgdClient(make_session(), EgdCredentials(client_id, client_secret))
print(client.metering_points())  # [{"ean": "...", "typMereni": "B"}]
start, end = local_day_bounds_utc(date(2026, 10, 1), date(2026, 10, 31))
result = client.readings(ean, start, end, kind="consumption")  # or kind="production"
print(sum(r.kwh for r in result.readings), result.warnings)
```

`readings()` handles token refresh, 30-day chunking, the exclusive `to`, unit conversion
(kW → kWh) and clamping to the period you are authorised for. Spot-price building blocks
live in `egd_api_client.spot` (`ote.OteClient`, `cnb.CnbRates`, `calculator.compute`).

## How the cost is calculated

For every 15-minute interval *i* with consumption *E<sub>i</sub>* (kWh) and day-ahead price
*S<sub>i</sub>* (CZK/MWh):

```
price_i  = S_i × multiplier + add_czk_mwh            [CZK/MWh, excl. VAT]
           (S_i < 0: negative_multiplier / negative_add_czk_mwh, if set)
energy   = Σ E_i × price_i / 1000                    [CZK]
fees     = monthly_fee × (days in period / days in month), summed per month
total    = (energy + fees) × (1 + VAT/100)
effective price = total / Σ E_i                      [CZK/kWh]
```

* **Hourly vs. 15-minute prices.** Until 30 Sep 2025 the day-ahead market had hourly
  prices; each 15-minute consumption value uses the price of its hour. Since 1 Oct 2025 the
  15-minute price is used (or the hourly one with `--price-resolution hourly`). Hourly
  consumption data is split evenly into quarter-hours.
* **Negative prices.** Some suppliers use other coefficients when the spot price is
  negative: `--negative-multiplier` / `--negative-add-czk-mwh` (config `[spot]`
  `negative_multiplier` / `negative_add_czk_mwh`). Each one that is not set falls back to
  the normal `-m` / `-a`. The pair is chosen by the sign of the raw OTE price (strictly
  below 0), *before* any clamping. Then `--negative` decides:
  * `as-is` (default) – `S × neg_mult + neg_add`; a negative spot price reduces your bill
    (a multiplier > 1 amplifies the credit);
  * `clamp-spot` – the negative spot is set to 0 *before* the coefficients, so the price is
    `neg_add` (you still pay the surcharge for negative-price periods);
  * `clamp-total` – `S × neg_mult + neg_add`, floored at 0 (you never get paid for
    consuming).

  Check your supplier's terms – they differ.
* **Fixed VT/NT comparison.** With `--fixed-price-low`, each 15-minute interval is priced at
  the NT price for the part of it that falls into a low-tariff window and at the VT price
  (`--fixed-price`) for the rest; the NT consumption is shown in the report (`NT kWh`).
* **Distribution charges** (EG.D distribution fee, circuit breaker, system services, POZE,
  electricity tax …) are the same whichever supplier you choose, so they are left out
  unless you enter them – see [Distribution fees](#distribution-fees).
* The report also shows your **consumption-weighted average spot price** next to the simple
  average of the period – if yours is higher, you consume more when power is expensive.
* Periods without consumption data or without a market price are excluded and reported as
  warnings (e.g. the 4 July 2025 emergency state, when OTE published no prices for 12 hours).

## EUR/CZK conversion

`egd spot prices` makes you choose the currency (`--currency czk|eur` is required):

* `czk` – hourly era (until 30 Sep 2025): OTE's own CZK prices; 15-minute era: OTE's EUR
  prices × the ČNB rate of the delivery day (below);
* `eur` – OTE's own EUR prices in both eras. For the hourly era they are downloaded
  separately (`GetDamPriceE` with `InEur=true`), not converted back from CZK.

The export's `eur_czk` column shows the ČNB rate applied (empty where OTE published the
price in that currency itself).


Since 1 Oct 2025 OTE publishes day-ahead prices in EUR only, so they have to be converted.
The tool uses the **ČNB rate valid for the delivery day**:

* ČNB publishes its fixing every business day at about 14:30; it applies to that day and to
  any following weekend days and public holidays until the next fixing;
* so a delivery day that is a business day uses **its own** fixing, a Saturday, Sunday or
  holiday uses the **last fixing before it** (e.g. Tue 17 Nov 2026, a holiday → Mon 16 Nov).

This is precisely the rate OTE itself reports as `EurRate` ("Kurz ČNB pro daný den dodávky")
in its daily index (`GetDamIndexE`) and used for its own CZK prices before Oct 2025. It was
checked day by day against OTE and against ČNB's API for Jan–Sep 2025 and Jun–Nov 2026 with
no difference (a "previous business day" rule would differ on most days).
`egd spot fx` shows which rate and fixing date are used for each day. For days before
1 Oct 2025 no conversion happens – OTE's CZK prices are used directly. Prices are cached in EUR and converted on
every run, so a rate is never "frozen" in the cache. The fixing of a business day exists
only after ~14:30 that day; until then (and for tomorrow's prices, which OTE publishes
around 13:00) the last available fixing is used, which makes such figures provisional.

## Low tariff (NT)

EG.D's OpenAPI says nothing about tariffs. This was checked against the live API with a
type B metering point (Nov 2026): `/rest/om` returns only `ean` and `typMereni`, the profile
list has no VT/NT split for A/B meters, and there are no HDO, distribution-rate or
circuit-breaker endpoints. The legacy smart-meter endpoint (`/rest/c/…`) lists VT/NT
register profiles, but only for C1 smart meters (an A/B point is refused).

The NT periods are therefore **always entered by you**, and nothing is assumed or
downloaded:

* `--low-tariff-hours "22:00-06:00,13:00-15:00"` – NT hours for every day. Ranges may cross
  midnight, and boundaries need not be on quarter-hours: an interval that is partly NT is
  split proportionally.
* `--low-tariff-hours-weekend "…"` – different hours for Saturday and Sunday (optional).
* `--holidays weekday|sunday` – Czech public holidays use the hours of their weekday
  (default) or the weekend hours.
* The same can be set in the config section `[low_tariff]` (`hours`, `hours_weekend`,
  `holidays`).

`--fixed-price-low` without low-tariff hours is an error. Your NT times are listed in the
Distribuce24 portal or on egd.cz (*Časy platnosti nízkého tarifu*) for your HDO code. If they
changed during the period, calculate the periods separately. Times are local
(Europe/Prague) wall-clock times, so DST days are handled naturally.

## Distribution fees

EG.D's OpenAPI does not provide any distribution tariff data (no distribution rate such as
D02d/D25d, no circuit-breaker size, no fees), and the tool deliberately contains no
regulated price tables. You can **enter your own charges** – in CZK, excl. VAT, like the
fixed prices:

| Option | Config `[distribution]` | Meaning |
|---|---|---|
| `--distribution-high CZK_KWH` | `high_czk_kwh` | per-kWh charge in the high tariff (VT); applies to **all** consumption when no NT charge is given |
| `--distribution-low CZK_KWH` | `low_czk_kwh` | per-kWh charge in the low tariff (NT); requires `--low-tariff-hours` (error otherwise) and `--distribution-high` |
| `--distribution-monthly-fee CZK` | `monthly_fee` | fixed monthly charges (circuit breaker / reserved capacity etc.), prorated by days |

Sum up all per-kWh components of your rate yourself (distribution, system services, POZE,
electricity tax, OTE fee …), separately for VT and NT. The charges are computed once per
interval (NT share from your low-tariff hours, like the fixed NT price) and added to
**both** results, VAT on top with `--vat`:

* the report shows `Distribution`, `Spot + distrib.` and (with `--fixed-price`)
  `Fixed + distrib.`; `TOTAL`, `Fixed tariff` and `Difference` stay supplier-only (the
  distribution part is identical for both, so the difference does not change);
* the breakdown adds `Distrib.` (excl. VAT), `Total+D` and `Fixed+D` (incl. VAT);
* JSON/CSV exports add `distribution_czk`, `total_with_distribution_czk`,
  `avg_price_with_distribution_czk_kwh` and `fixed_total_with_distribution_czk`.

## Data sources

| Data | Source | Notes |
|---|---|---|
| Consumption | EG.D OpenAPI – `POST https://idm.distribuce24.cz/oauth/token` (client credentials, scope `namerena_data_openapi`), `GET https://data.distribuce24.cz/rest/spotreby?ean=…&profile=ICC1&from=…&to=…` | Profile `ICC1` = mean 15-min power in kW (÷4 → kWh); `ICQ2` = kWh; `DCQC` = C1 smart meters. Max 3000 values per call → requested in 30-day chunks (`to` is exclusive; paging parameters are avoided because `pageStart=1` drops a value). [User guide (PDF)](https://www.egd.cz/sites/default/files/2026-05/uzivatelsky_navod_openapi_abc.pdf), [older version](https://portal.distribuce24.cz/download/Uzivatelsky_navod_OpenApi.pdf) |
| Spot prices | OTE-CR public SOAP service `https://www.ote-cr.cz/pw-data/services/PublicDataService` – `GetDamPriceE` (hourly, CZK) and `GetDamPricePeriodE` (15-min, EUR) | One calendar month per request. [Manual](https://www.ote-cr.cz/cs/dokumentace/xsd-wsdl-manual-pubweb) |
| EUR/CZK | ČNB daily fixing, [`year.txt`](https://www.cnb.cz/en/financial-markets/foreign-exchange-market/central-bank-exchange-rate-fixing/central-bank-exchange-rate-fixing/year.txt?year=2025) | Rate valid for the delivery day: the fixing published that day (~14:30), or the last fixing before it on weekends/holidays. This is exactly OTE's own `EurRate` ("Kurz ČNB pro daný den dodávky"); see [EUR/CZK conversion](#eurczk-conversion). Your supplier may use a different rate. |

All timestamps are handled in UTC internally and reported in Europe/Prague time; DST days
(23 / 25 hours) are handled correctly.

### EG.D API details you may need to tweak

* `--api-variant c1-legacy` uses the separate smart-meter endpoint `/rest/c/spotreby`
  described in the 2025-12 revision of the EG.D guide (OBIS-style profile codes,
  `zdrojDat`). The current (2026-05) guide lists A, B and C1 under the unified
  `/rest/spotreby`, which is the default.
* Timestamps label the **start** of each interval (confirmed against the live API: the last
  value of a day is 23:45 local, and a request reaching local midnight of today is rejected).
  `--api-timestamps end` exists only for the legacy endpoint.
* The consumption profile is chosen from the metering point's measurement type
  (`/rest/om`): `ICC1` for A/B, `DCQC` for C1 smart meters. Override with `--profile`
  (e.g. `ICQ2`, which gives identical kWh to `ICC1`).
* EG.D only serves data up to **yesterday**, at most **3 years** back, and only for the period
  your account is authorised for. The tool trims the period accordingly and tells you from
  which date data are available.
* Values with substitute statuses that EG.D marks as final (`B`, `E`, `M`, `N`) are used
  silently; temporary ones (`G`, `V`) produce a warning.
* `--test-env` talks to EG.D's test environment (`test.distribuce24.cz`), for which you can
  generate test credentials in the portal.

## Input files

`--input` accepts:

* **XLSX** exported from the Distribuce24 portal (needs the `xlsx` extra),
* **CSV** from anywhere: `;`, `,` or tab separated, decimal comma or point, UTF-8 or
  Windows-1250, dates like `01.11.2025 00:15`, `2025-11-01 00:15`, ISO 8601 with offset,
  separate date and time columns, `24:00`,
* the tool's own CSV written by `egd data` (`start,kwh,status`).

The header row and the date/time and consumption columns are detected automatically
(headers such as *Datum*, *Čas*, *Spotřeba*, *ICC1*, *kWh*); override with
`--time-column`, `--value-column` (name or 0-based index). Units are taken from the header
(`kW` → mean power, converted to energy; `kWh`) or `--unit`. Whether timestamps mark the
start or the end of an interval is detected from the first row (`00:00` → start,
`00:15` → end) or set with `--timestamps`. Local times during the repeated hour at the end
of DST are resolved by row order.

## Limitations

* The EG.D API was verified live with a measurement type **B** metering point (Nov 2026).
  Type C1 (`DCQC` profile) follows the official profile list but has not been tried on a
  real C1 meter yet.
* The layout of the portal's XLSX export is not documented; auto-detection is heuristic.
  Use the column options if it picks the wrong column.
* Suppliers' exact formulas vary (exchange rate, rounding, treatment of negative prices,
  OTE fee, imbalance costs); results are an estimate, not a bill.
* Production / export to the grid can be downloaded with `egd data --kind production`, but
  buy-back pricing is not modelled by `egd spot calc` yet.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest --cov=egd_api_client   # all HTTP is mocked; no credentials needed
ruff check . && ruff format --check .
python examples/generate_sample.py 2025-01-01 2025-12-31 > /tmp/year.csv   # synthetic data
```

Project layout:

```
src/egd_api_client/
  client.py          EG.D OpenAPI client (token, metering points, profiles, readings)
  readings_io.py     CSV/XLSX import, canonical CSV / JSON export
  lowtariff.py       NT schedules (manual hours), Czech public holidays
  models.py          Reading, PricePoint, LoadResult
  cli.py             `egd` entry point and core commands (meters, profiles, data)
  cli_common.py      shared CLI options, period handling, context
  config.py, cache.py, timeutil.py, http.py
  spot/              spot-tariff feature (`egd spot …`)
    cli.py           calc, prices, fx commands
    ote.py           OTE day-ahead prices (SOAP) with caching
    cnb.py           ČNB EUR/CZK rates
    calculator.py    cost computation and aggregation
    tariff.py        spot / fixed / distribution tariff definitions
    report.py        text report and CSV/JSON export
tests/               pytest suite with recorded OTE/ČNB fixtures
```

New features go into their own subpackage with a `register(subparsers, …)` function in its
`cli.py`, like `spot`.

## License

[MIT](LICENSE). Not affiliated with EG.D, OTE or ČNB.
