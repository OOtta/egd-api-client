# egd-spot-calc

**What would my electricity have cost on a spot-price tariff?**

`egd-spot` is a command-line tool for households and businesses in the
[EG.D (E.ON Distribuce)](https://www.egd.cz) distribution area of the Czech Republic. It

1. downloads your historical 15-minute smart-meter data from the **EG.D Distribuce24 OpenAPI**
   (or reads an exported CSV/XLSX file),
2. downloads the **OTE-CR day-ahead market prices** for the same period by itself
   (hourly prices before 1 Oct 2025, 15-minute prices since), converting EUR → CZK with the
   official **ČNB** exchange rate where needed,
3. applies your supplier's spot-tariff formula – e.g. `spot × 1.15`, `spot + 400 CZK/MWh`,
   a monthly fee, VAT – and prints what you would have paid, optionally per month or per day
   and compared with a fixed-price tariff.

> 🇨🇿 Česky: viz [níže](#česky).

## Contents

- [Installation](#installation)
- [Quick start](#quick-start)
- [Getting EG.D API access](#getting-egd-api-access)
- [Configuration](#configuration)
- [Commands](#commands)
- [How the cost is calculated](#how-the-cost-is-calculated)
- [Data sources](#data-sources)
- [Input files](#input-files)
- [Limitations](#limitations)
- [Development](#development)
- [Česky](#česky)

## Installation

Python 3.11+ is required. The recommended way is [pipx](https://pipx.pypa.io):

```bash
pipx install "git+https://github.com/OWNER/egd-spot-calc"          # CSV input + API
pipx install "egd-spot-calc[xlsx] @ git+https://github.com/OWNER/egd-spot-calc"  # + XLSX input
```

From a local checkout: `pipx install ".[xlsx]"` (or `pip install -e ".[dev]"` for development).

## Quick start

Try it without any credentials using the bundled synthetic consumption file:

```bash
egd-spot calc --input examples/sample_consumption.csv \
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

With your EG.D API credentials:

```bash
export EGD_CLIENT_ID=...  EGD_CLIENT_SECRET=...
egd-spot meters                                   # list your metering points (EANs)
egd-spot calc --ean 859182400xxxxxxxxx --from 2025-01 --to 2025-12 \
    --add-czk-mwh 350 --vat --breakdown month
```

## Getting EG.D API access

The EG.D "Vzdálený přístup – OpenAPI" is free, but you have to enable it yourself:

1. Register / log in at [portal.distribuce24.cz](https://portal.distribuce24.cz)
   (or the Distribuce24 mobile app) and link your metering point (EAN).
2. Go to **Správa účtů → Vzdálený přístup – OpenAPI** and click
   **„Vygenerovat client_id a client_secret“** (validity 1, 3 or 10 years).
3. Put the values into environment variables or the config file (see below).

The API serves metering points with **measurement type A, B or C1** – C1 means an active
smart meter with 15-minute data. Type C4 (no interval metering) has no data to compute with.
EG.D updates the data once a day; please do not run the tool in a tight loop.

No API access? Export the data from the portal (*Historie spotřeb → export .xlsx*) and use
`--input` instead.

## Configuration

Settings are read from (later wins):

1. the config file – `~/.config/egd-spot/config.toml` (Linux/macOS),
   `%APPDATA%\egd-spot\config.toml` (Windows), or `--config FILE` / `EGD_SPOT_CONFIG`;
2. environment variables `EGD_CLIENT_ID`, `EGD_CLIENT_SECRET`, `EGD_EAN`;
3. command-line options.

See [`examples/config.example.toml`](examples/config.example.toml). If you store the secret
in the file, `chmod 600` it – the tool warns otherwise. Secrets are never printed or cached;
the daily access token is kept in memory only.

Downloaded prices and exchange rates are cached in `~/.cache/egd-spot` (override with
`--cache-dir` / `EGD_SPOT_CACHE_DIR`, disable with `--no-cache`). Only complete days are
cached, so re-running a calculation is instant and offline-friendly.

## Commands

| Command | Purpose |
|---|---|
| `egd-spot calc` | compute the spot-tariff cost of your consumption (API or file) |
| `egd-spot prices` | download / summarise / export OTE spot prices |
| `egd-spot fetch` | download EG.D consumption into a reusable CSV |
| `egd-spot meters` | list metering points available to your API credentials |

Run `egd-spot COMMAND --help` for all options. The most important ones of `calc`:

| Option | Meaning |
|---|---|
| `--from`, `--to` | period in local Czech dates, inclusive; `YYYY-MM-DD` or `YYYY-MM` (whole month). With `--input` they default to the file's range |
| `--ean` | metering point (uses the API) |
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
# A whole year, monthly breakdown, exported to JSON
egd-spot calc --ean 8591824... --from 2025-01 --to 2025-12 -m 1.1 -a 300 -b month -o 2025.json

# Supplier bills hourly prices even after the switch to 15-minute trading
egd-spot calc -i export.xlsx -a 450 --price-resolution hourly

# Never get paid for consumption at negative prices
egd-spot calc -i export.csv -a 400 --negative clamp-total

# Save API data once, then experiment offline with different tariffs
egd-spot fetch --ean 8591824... --from 2025-01 --to 2025-12 -o 2025.csv
egd-spot calc -i 2025.csv -m 1.15 && egd-spot calc -i 2025.csv -a 500

# Just the prices
egd-spot prices --from 2025-11 --export prices-2025-11.csv
```

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

## Data sources

| Data | Source | Notes |
|---|---|---|
| Consumption | EG.D OpenAPI – `POST https://idm.distribuce24.cz/oauth/token` (client credentials, scope `namerena_data_openapi`), `GET https://data.distribuce24.cz/rest/spotreby?ean=…&profile=ICC1&from=…&to=…` | Profile `ICC1` = mean 15-min power in kW (÷4 → kWh); `ICQ2` = kWh; `DCQC` = C1 smart meters. Max 3000 values per call → requested in 30-day chunks (`to` is exclusive; paging parameters are avoided because `pageStart=1` drops a value). [User guide (PDF)](https://www.egd.cz/sites/default/files/2026-05/uzivatelsky_navod_openapi_abc.pdf), [older version](https://portal.distribuce24.cz/download/Uzivatelsky_navod_OpenApi.pdf) |
| Spot prices | OTE-CR public SOAP service `https://www.ote-cr.cz/pw-data/services/PublicDataService` – `GetDamPriceE` (hourly, CZK) and `GetDamPricePeriodE` (15-min, EUR) | One calendar month per request. [Manual](https://www.ote-cr.cz/cs/dokumentace/xsd-wsdl-manual-pubweb) |
| EUR/CZK | ČNB daily fixing, [`year.txt`](https://www.cnb.cz/en/financial-markets/foreign-exchange-market/central-bank-exchange-rate-fixing/central-bank-exchange-rate-fixing/year.txt?year=2025) | Rate of the delivery day (last fixing before it on weekends/holidays) – this reproduces OTE's own CZK prices from before Oct 2025. Your supplier may use a different rate. |

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
* the tool's own CSV written by `egd-spot fetch` (`start,kwh,status`).

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
* Export to the grid (profile `ISC1`) can be downloaded with `--profile ISC1`, but buy-back
  pricing is not modelled.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest --cov=egd_spot      # all HTTP is mocked; no credentials needed
ruff check . && ruff format --check .
python examples/generate_sample.py 2025-01-01 2025-12-31 > /tmp/year.csv   # synthetic data
```

Project layout:

```
src/egd_spot/
  cli.py             argument parsing and commands
  egd.py             EG.D OpenAPI client (token, readings, metering points)
  ote.py             OTE day-ahead prices (SOAP) with caching
  cnb.py             ČNB EUR/CZK rates
  consumption_io.py  CSV/XLSX import, canonical CSV export
  calculator.py      cost computation and aggregation
  tariff.py          spot / fixed tariff definitions
  report.py          text report and CSV/JSON export
  config.py, cache.py, timeutil.py, models.py, http.py
tests/               pytest suite with recorded OTE/ČNB fixtures
```

## License

[MIT](LICENSE). Not affiliated with EG.D, OTE or ČNB.

---

## Česky

`egd-spot` spočítá, kolik byste zaplatili za elektřinu se **spotovým tarifem**. Stáhne vaše
čtvrthodinová data ze smartmetru přes **OpenAPI EG.D (Distribuce24)** – nebo načte export
z portálu (XLSX/CSV) – sám si stáhne **spotové ceny OTE** (denní trh; do 30. 9. 2025
hodinové v CZK, od 1. 10. 2025 čtvrthodinové v EUR přepočtené kurzem ČNB) a použije vzorec
vašeho dodavatele:

```
cena = spot × koeficient + přirážka [Kč/MWh]   (+ měsíční poplatek, + DPH)
```

Příklad – dodavatel účtuje `spot × 1,15 + 400 Kč/MWh`, 99 Kč měsíčně, s DPH 21 %, porovnání
s fixní cenou 3,20 Kč/kWh:

```bash
export EGD_CLIENT_ID=...  EGD_CLIENT_SECRET=...
egd-spot calc --ean 859182400xxxxxxxxx --from 2025-01 --to 2025-12 \
    -m 1.15 -a 400 --monthly-fee 99 --vat --fixed-price 3.2 --breakdown month
```

**Přístup k API:** na [portal.distribuce24.cz](https://portal.distribuce24.cz) →
*Správa účtů → Vzdálený přístup – OpenAPI* → *Vygenerovat client_id a client_secret*.
Funguje pro odběrná místa s měřením A, B a C1 (aktivní chytrý elektroměr). Bez API můžete
použít export z portálu: `egd-spot calc --input export.xlsx ...`.

**Záporné ceny:** `--negative as-is` (výchozí, vzorec beze změny – záporná cena snižuje
účet), `clamp-spot` (záporný spot = 0), `clamp-total` (výsledná cena nikdy pod 0).

**Nezahrnuto:** regulované platby za distribuci (distribuční poplatek, jistič, systémové
služby, POZE, daň z elektřiny) – ty jsou stejné u každého dodavatele. Chcete-li celkovou
cenu, přidejte je do `--add-czk-mwh` / `--monthly-fee`.

Výsledek je odhad, ne faktura – dodavatelé se liší v kurzu, zaokrouhlování a dalších
poplatcích.
