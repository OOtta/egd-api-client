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
* `egd spot prices`, `egd spot fx` – the prices and EUR/CZK rates themselves.

The same functionality is available as a library (`egd_api_client.EgdClient`,
`egd_api_client.spot`), so more features can be added as further sub-commands.

> 🇨🇿 Česky: viz [níže](#česky).

## Contents

- [Installation](#installation)
- [Quick start](#quick-start)
- [Getting EG.D API access](#getting-egd-api-access)
- [Configuration](#configuration)
- [Commands](#commands)
- [Using it as a library](#using-it-as-a-library)
- [How the cost is calculated](#how-the-cost-is-calculated)
- [EUR/CZK conversion](#eurczk-conversion)
- [Data sources](#data-sources)
- [Input files](#input-files)
- [Limitations](#limitations)
- [Development](#development)
- [Česky](#česky)

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
the same for any supplier and are NOT included unless you add them via
--add-czk-mwh / --monthly-fee.

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

The file has an `[egd]` section (credentials, EAN, API options) and one section per feature
(`[spot]`, `[spot.fixed]`); see [`examples/config.example.toml`](examples/config.example.toml).
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
| `egd spot prices` | download / summarise / export OTE spot prices |
| `egd spot fx` | show the ČNB EUR/CZK rate applied to each delivery day |

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
| `--negative MODE` | negative price handling: `as-is` (default), `clamp-spot`, `clamp-total` |
| `--price-resolution` | `auto` (15-min prices where they exist) or `hourly` |
| `--fixed-price CZK_KWH` | compare with a fixed price per kWh (excl. VAT) |
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

# Just the prices, and the exchange rates used for them
egd spot prices --from 2025-11 --export prices-2025-11.csv
egd spot fx --from 2026-10-24 --to 2026-11-02
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
energy   = Σ E_i × price_i / 1000                    [CZK]
fees     = monthly_fee × (days in period / days in month), summed per month
total    = (energy + fees) × (1 + VAT/100)
effective price = total / Σ E_i                      [CZK/kWh]
```

* **Hourly vs. 15-minute prices.** Until 30 Sep 2025 the day-ahead market had hourly
  prices; each 15-minute consumption value uses the price of its hour. Since 1 Oct 2025 the
  15-minute price is used (or the hourly one with `--price-resolution hourly`). Hourly
  consumption data is split evenly into quarter-hours.
* **Negative prices.** `as-is` applies the formula unchanged, so a negative spot price
  reduces your bill (and a multiplier > 1 amplifies the credit). `clamp-spot` treats negative
  spot prices as 0 (you still pay the surcharge). `clamp-total` floors the final energy price
  at 0. Check your supplier's terms – they differ.
* **What is not included.** Distribution and other regulated charges (EG.D distribution
  fee, reserved capacity / circuit breaker fee, system services, POZE, electricity tax,
  OTE fee) are the same whichever supplier you choose, so they are left out. If you want an
  all-in figure, add the per-MWh components to `--add-czk-mwh` and the monthly ones to
  `--monthly-fee`.
* The report also shows your **consumption-weighted average spot price** next to the simple
  average of the period – if yours is higher, you consume more when power is expensive.
* Periods without consumption data or without a market price are excluded and reported as
  warnings (e.g. the 4 July 2025 emergency state, when OTE published no prices for 12 hours).

## EUR/CZK conversion

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
  models.py          Reading, PricePoint, LoadResult
  cli.py             `egd` entry point and core commands (meters, profiles, data)
  cli_common.py      shared CLI options, period handling, context
  config.py, cache.py, timeutil.py, http.py
  spot/              spot-tariff feature (`egd spot …`)
    cli.py           calc, prices, fx commands
    ote.py           OTE day-ahead prices (SOAP) with caching
    cnb.py           ČNB EUR/CZK rates
    calculator.py    cost computation and aggregation
    tariff.py        spot / fixed tariff definitions
    report.py        text report and CSV/JSON export
tests/               pytest suite with recorded OTE/ČNB fixtures
```

New features go into their own subpackage with a `register(subparsers, …)` function in its
`cli.py`, like `spot`.

## License

[MIT](LICENSE). Not affiliated with EG.D, OTE or ČNB.

---

## Česky

`egd-api-client` je klient a příkazová řádka pro **OpenAPI EG.D (Distribuce24)**. Umí
stáhnout čtvrthodinová data ze smartmetru – spotřebu i výrobu (přetoky) – do CSV/JSON
(`egd data`) a jako první funkci nad nimi spočítat, kolik byste zaplatili za elektřinu se
**spotovým tarifem** (`egd spot calc`). Spotové ceny si sám stáhne z **OTE** (denní trh; do
30. 9. 2025 hodinové v CZK, od 1. 10. 2025 čtvrthodinové v EUR přepočtené **kurzem ČNB
platným pro daný den dodávky** – o víkendu a ve svátek posledním vyhlášeným, stejně jako to
dělá OTE) a použije vzorec vašeho dodavatele:

```
cena = spot × koeficient + přirážka [Kč/MWh]   (+ měsíční poplatek, + DPH)
```

| Příkaz | Co dělá |
|---|---|
| `egd meters` | seznam odběrných míst (EAN) dostupných pro vaše přihlašovací údaje |
| `egd data` | export čtvrthodinové spotřeby (`--kind consumption`) nebo výroby (`--kind production`) |
| `egd spot calc` | výpočet ceny se spotovým tarifem |
| `egd spot prices` | spotové ceny OTE |
| `egd spot fx` | kurz EUR/CZK použitý pro každý den |

Příklad – dodavatel účtuje `spot × 1,15 + 400 Kč/MWh`, 99 Kč měsíčně, s DPH 21 %, porovnání
s fixní cenou 3,20 Kč/kWh:

```bash
export EGD_CLIENT_ID=...  EGD_CLIENT_SECRET=...
egd spot calc --ean 859182400xxxxxxxxx --from 2025-01 --to 2025-12 \
    -m 1.15 -a 400 --monthly-fee 99 --vat --fixed-price 3.2 --breakdown month
```

Konfigurace: `~/.config/egd-api-client/config.toml` (sekce `[egd]`, `[spot]`, `[spot.fixed]`),
proměnné prostředí `EGD_CLIENT_ID`, `EGD_CLIENT_SECRET`, `EGD_EAN`.

**Přístup k API:** na [portal.distribuce24.cz](https://portal.distribuce24.cz) →
*Správa účtů → Vzdálený přístup – OpenAPI* → *Vygenerovat client_id a client_secret*.
Funguje pro odběrná místa s měřením A, B a C1 (aktivní chytrý elektroměr). Bez API můžete
použít export z portálu: `egd spot calc --input export.xlsx ...`.

**Záporné ceny:** `--negative as-is` (výchozí, vzorec beze změny – záporná cena snižuje
účet), `clamp-spot` (záporný spot = 0), `clamp-total` (výsledná cena nikdy pod 0).

**Nezahrnuto:** regulované platby za distribuci (distribuční poplatek, jistič, systémové
služby, POZE, daň z elektřiny) – ty jsou stejné u každého dodavatele. Chcete-li celkovou
cenu, přidejte je do `--add-czk-mwh` / `--monthly-fee`.

Výsledek je odhad, ne faktura – dodavatelé se liší v kurzu, zaokrouhlování a dalších
poplatcích.
