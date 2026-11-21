# egd-api-client

**Klient a příkazová řádka pro OpenAPI EG.D (E.ON Distribuce) – Distribuce24.**

`egd` stahuje vaše historická čtvrthodinová data z elektroměru pro odběrná místa
v distribučním území [EG.D](https://www.egd.cz) a dělá s nimi užitečné věci. První
funkcí postavenou nad klientem je **kalkulačka spotového tarifu**:

* `egd meters`, `egd data` – seznam vašich odběrných míst, export spotřeby nebo výroby
  (přetoků do sítě) do CSV / JSON;
* `egd spot calc` – kolik byste zaplatili se spotovým tarifem? Sama si stáhne **ceny
  denního trhu OTE** (do 30. 9. 2025 hodinové, od 1. 10. 2025 čtvrthodinové, přepočtené
  oficiálním **kurzem ČNB** platným pro každý den dodávky) a použije vzorec vašeho
  dodavatele – `spot × 1,15`, `spot + 400 Kč/MWh`, měsíční poplatek, DPH –, volitelně
  s rozpisem po měsících nebo dnech a s porovnáním s fixní cenou (i dvoutarifní VT/NT);
* `egd spot prices`, `egd spot fx` – samotné ceny (v CZK nebo EUR) a použité kurzy EUR/CZK;
* vlastní distribuční poplatky (VT/NT a měsíční) připočtené ke spotovému i fixnímu výsledku.

Stejné funkce jsou k dispozici jako knihovna (`egd_api_client.EgdClient`,
`egd_api_client.spot`), takže lze snadno přidávat další podpříkazy.

## Obsah

- [Instalace](#instalace)
- [Rychlý start](#rychlý-start)
- [Získání přístupu k API EG.D](#získání-přístupu-k-api-egd)
- [Konfigurace](#konfigurace)
- [Příkazy](#příkazy)
- [Použití jako knihovny](#použití-jako-knihovny)
- [Jak se počítá cena](#jak-se-počítá-cena)
- [Přepočet EUR/CZK](#přepočet-eurczk)
- [Nízký tarif (NT)](#nízký-tarif-nt)
- [Distribuční poplatky](#distribuční-poplatky)
- [Zdroje dat](#zdroje-dat)
- [Vstupní soubory](#vstupní-soubory)
- [Omezení](#omezení)
- [Řešení problémů](#řešení-problémů)
- [Vývoj](#vývoj)
- [Licence](#licence)

## Instalace

Potřebujete Python 3.11 nebo novější. Doporučený způsob je [pipx](https://pipx.pypa.io):

```bash
pipx install "git+https://github.com/OOtta/egd-api-client"                        # API + CSV
pipx install "egd-api-client[xlsx] @ git+https://github.com/OOtta/egd-api-client"  # + vstup XLSX
```

Z lokální kopie: `pipx install ".[xlsx]"` (pro vývoj `pip install -e ".[dev]"`).

## Rychlý start

S přihlašovacími údaji k API EG.D:

```bash
export EGD_CLIENT_ID=...  EGD_CLIENT_SECRET=...
egd meters                                         # vaše odběrná místa (EAN)
egd data --ean 859182400xxxxxxxxx --from 2026-10 -o rijen.csv
egd spot calc --ean 859182400xxxxxxxxx --from 2025-01 --to 2025-12 \
    -m 1.15 -a 400 --vat --breakdown month
```

Kalkulačku si můžete vyzkoušet i bez přihlašovacích údajů s přiloženým syntetickým souborem
spotřeby:

```bash
egd spot calc --input examples/sample_consumption.csv \
    --multiplier 1.15 --add-czk-mwh 400 --monthly-fee 150 --vat \
    --fixed-price 3.5 --fixed-monthly-fee 150 --breakdown month
```

Výstup programu je v angličtině:

```text
Period:            2025-10-01 – 2025-11-30 (Europe/Prague)
Tariff:            spot × 1.15 + 400 CZK/MWh, fee 150 CZK/month, VAT 21 %
Data coverage:     5860/5860 quarter-hours (100.0 %)

Consumption:       865.107 kWh
Avg spot price:    2 860.82 CZK/MWh (raw OTE, weighted by your consumption)
                   2 510.70 CZK/MWh (raw OTE, simple average of the period)
Spot + tariff:     3 689.94 CZK/MWh (weighted by your consumption)
                   3 287.30 CZK/MWh (simple average of the period)
                   (spot × 1.15 + 400 CZK/MWh; excl. VAT and fees)
Avg fixed price:   3 500.00 CZK/MWh (weighted by your consumption)
                   3 500.00 CZK/MWh (simple average of the period)
                   (3.5 CZK/kWh + 150 CZK/month; excl. VAT and fees)
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

Period       kWh  Spot avg  Spot+tariff  Fixed avg    Energy     Fee     Total  CZK/kWh     Fixed    Saved
----------------------------------------------------------------------------------------------------------
2025-10  437.960  2 787.20     3 605.27   3 500.00  1 578.97  150.00  2 092.05   4.7768  2 036.26   -55.79
2025-11  427.147  2 936.31     3 776.75   3 500.00  1 613.23  150.00  2 133.51   4.9948  1 990.47  -143.04
(Spot avg, Spot+tariff and Fixed avg in CZK/MWh excl. VAT, consumption-weighted; money in CZK; Energy and Fee excl. VAT; Total, Fixed and Saved incl. VAT)
```

(`examples/sample_consumption.csv` je syntetický, ceny jsou skutečné ceny OTE.)

## Získání přístupu k API EG.D

„Vzdálený přístup – OpenAPI“ od EG.D je zdarma, ale musíte si ho sami zapnout:

1. Zaregistrujte se / přihlaste na [portal.distribuce24.cz](https://portal.distribuce24.cz)
   (nebo v mobilní aplikaci Distribuce24) a přidejte své odběrné místo (EAN).
2. V části **Správa účtů → Vzdálený přístup – OpenAPI** klikněte na
   **„Vygenerovat client_id a client_secret“** (platnost 1, 3 nebo 10 let).
3. Hodnoty uložte do proměnných prostředí nebo do konfiguračního souboru (viz níže).

API obsluhuje odběrná místa s **typem měření A, B nebo C1** – C1 znamená aktivní chytrý
elektroměr se čtvrthodinovými daty. Typ C4 (bez průběhového měření) žádná data nemá.
EG.D aktualizuje data jednou denně; nespouštějte prosím nástroj ve smyčce.

Nemáte přístup k API? Exportujte data z portálu (*Historie spotřeb → export .xlsx*)
a použijte `egd spot calc --input`.

## Konfigurace

Nastavení se čte z (pozdější vyhrává):

1. konfiguračního souboru – `~/.config/egd-api-client/config.toml` (Linux/macOS),
   `%APPDATA%\egd-api-client\config.toml` (Windows), případně `--config SOUBOR` /
   `EGD_CONFIG`;
2. proměnných prostředí `EGD_CLIENT_ID`, `EGD_CLIENT_SECRET`, `EGD_EAN`;
3. voleb příkazové řádky.

Soubor má sekce `[egd]` (přihlašovací údaje, EAN, volby API), `[spot]` (vzorec spotového
tarifu), `[spot.fixed]` (fixní tarif pro porovnání), `[low_tariff]` (časy NT)
a `[distribution]` (distribuční poplatky); viz
[`examples/config.example.toml`](examples/config.example.toml). Pokud v souboru uložíte
tajný klíč, nastavte mu `chmod 600` – jinak vás nástroj upozorní. Tajné údaje se nikdy
nevypisují ani neukládají do mezipaměti; denní přístupový token je jen v paměti.

Stažené ceny a kurzy se ukládají do `~/.cache/egd-api-client` (změna přes `--cache-dir` /
`EGD_CACHE_DIR`, vypnutí `--no-cache`). Ukládají se jen kompletní dny. Naměřená data se
neukládají nikdy.

## Příkazy

| Příkaz | Co dělá |
|---|---|
| `egd meters` | seznam odběrných míst dostupných pro vaše přihlašovací údaje |
| `egd profiles` | seznam kódů profilů EG.D (`--all` včetně plynu) |
| `egd data` | stažení čtvrthodinové spotřeby (`--kind consumption`, výchozí) nebo výroby (`--kind production`) do CSV nebo JSON (`-o`) |
| `egd spot calc` | výpočet ceny vaší spotřeby se spotovým tarifem (z API nebo ze souboru) |
| `egd spot prices --currency czk\|eur` | stažení / souhrn / export spotových cen OTE (`-o .csv/.json`); `--currency` je povinná |
| `egd spot fx` | kurz ČNB EUR/CZK použitý pro každý den dodávky (`-o .csv/.json`, `--json`) |

Všechny volby vypíše `egd PŘÍKAZ --help` / `egd spot PŘÍKAZ --help`. Společné volby:
`--from`, `--to` (místní data, včetně; `RRRR-MM-DD` nebo `RRRR-MM` = celý měsíc), `--ean`,
`--profile`, `--json`, `--config`, `--cache-dir`, `--no-cache`, `--test-env`.

### Výstup JSON (`--json`)

S `--json` vypíše každý příkaz na standardní výstup **jen jeden dokument JSON** – vždy
objekt – a na chybový výstup nepíše nic. Průběžné hlášky („Downloading …“, „Low-tariff
periods …“, „Wrote …“) se vynechají a všechna varování (zkrácení období na včerejšek,
mezery v datech, chybějící ceny OTE, upozornění z konfigurace …) jsou v poli
**`warnings`** (seznam řetězců, vždy přítomný, případně prázdný). Výstup lze tedy rovnou
předat např. `python -m json.tool` nebo `jq`.

| Příkaz | Struktura JSON |
|---|---|
| `egd meters` | `{"metering_points": [...], "warnings": [...]}` |
| `egd profiles` | `{"profiles": [...], "warnings": [...]}` |
| `egd data` | `{"period", "kind", "unit", "values", "total_kwh", "readings": [{"start", "kwh", "status"}], "warnings"}`; s `-o` se místo `readings` vypíše `"output": soubor` |
| `egd spot calc` | `{"source", "period", "tariff", "fixed_tariff", "low_tariff_schedule", "distribution", "summary", "breakdown", "warnings"}` |
| `egd spot prices` | souhrn (`from`, `to`, `currency`, `unit`, `points`, `avg`, `min`, `max`, `negative_points`) + `warnings` |
| `egd spot fx` | souhrn + `days_detail` + `warnings` |

Soubory JSON zapsané přes `-o` / `--export` jsou také čistý JSON s polem `warnings`
(`egd data` → stejný objekt jako výše, `egd spot calc` → stejný jako `--json`,
`egd spot prices` → `{"currency", "unit", "prices": [...], "warnings"}`,
`egd spot fx` → `{"rates": [...], "warnings"}`). Bez `--json` se varování dál vypisují
na chybový výstup (`warning: …`).

**Chyby:** příkaz skončí s návratovým kódem 2 a v režimu `--json` vypíše na standardní
výstup `{"error": "…", "warnings": [...]}` (chybový výstup zůstane prázdný). Chyby
samotných voleb příkazové řádky (neznámá volba, chybějící `--currency` …) hlásí argparse
jako obvykle textem na chybový výstup.

Ukázka (zkráceno):

```json
{
  "source": {"type": "file", "path": "examples/sample_consumption.csv", "readings": 5860},
  "period": {"from": "2025-09-30", "to": "2025-10-31"},
  "low_tariff_schedule": "NT 00:00–06:00, 22:00–24:00",
  "summary": {"kwh": 437.96, "...": "..."},
  "warnings": [
    "Consumption data covers 2980/3076 quarter-hours (24 h missing) – totals only include the data available."
  ]
}
```

Nejdůležitější volby `egd spot calc`:

| Volba | Význam |
|---|---|
| `--ean` | odběrné místo (použije API); s `--input` je výchozím obdobím rozsah souboru |
| `-i, --input SOUBOR` | soubor CSV/XLSX se spotřebou místo API |
| `-m, --multiplier X` | násobek spotové ceny, např. `1.15` |
| `-a, --add-czk-mwh X` | pevná přirážka v Kč/MWh, např. `400` (lze i záporně) |
| `--monthly-fee KČ` | pevný měsíční poplatek dodavatele (poměrně podle dnů) |
| `--vat [PROCENTA]` | připočíst DPH; samotné `--vat` znamená 21 % |
| `--negative-multiplier X` | násobek pro období se záporným spotem (výchozí: `-m`) |
| `--negative-add-czk-mwh X` | přirážka pro období se záporným spotem (výchozí: `-a`) |
| `--negative REŽIM` | zacházení se zápornými cenami: `as-is` (výchozí), `clamp-spot`, `clamp-total` – viz níže |
| `--price-resolution` | `auto` (čtvrthodinové ceny, kde existují) nebo `hourly` |
| `--fixed-price KČ_KWH` | porovnání s fixní cenou za kWh (bez DPH); u dvoutarifního produktu cena VT |
| `--fixed-price-low KČ_KWH` | cena NT fixního produktu; vyžaduje `--low-tariff-hours` |
| `--fixed-monthly-fee KČ` | měsíční poplatek fixního tarifu |
| `--low-tariff-hours ROZSAHY` | vaše časy NT, např. `"22:00-06:00,13:00-15:00"` (každý den) |
| `--low-tariff-hours-weekend ROZSAHY` | jiné časy NT pro sobotu a neděli |
| `--holidays weekday\|weekend` | svátky podle svého dne v týdnu (výchozí) nebo podle víkendových časů |
| `--distribution-high KČ_KWH` | váš distribuční poplatek za kWh ve VT, bez NT ceny pro veškerou spotřebu |
| `--distribution-low KČ_KWH` | váš distribuční poplatek za kWh v NT; vyžaduje `--low-tariff-hours` |
| `--distribution-monthly-fee KČ` | vaše pevné měsíční distribuční platby (jistič apod.) |
| `-b, --breakdown` | `none`, `month` nebo `day` |
| `-o, --export SOUBOR` | zápis výsledku do `.json` (celý) nebo `.csv` (řádky rozpisu) |
| `--intervals SOUBOR` | zápis každého čtvrthodinového intervalu s cenou a nákladem do CSV |
| `--json` | místo textového výpisu vypíše JSON |

Pro vstupní soubor dále: `--time-column`, `--value-column`, `--unit auto|kwh|kw`,
`--timestamps auto|start|end`, `--interval-minutes`, `--sheet` (viz
[Vstupní soubory](#vstupní-soubory)).

Další příklady:

```bash
# Export roční spotřeby a výroby (přetoků)
egd data --ean 8591824... --from 2025-01 --to 2025-12 -o spotreba-2025.csv
egd data --ean 8591824... --kind production --from 2025-06 -o pretoky-2025-06.json

# Celý rok, rozpis po měsících, export do JSON
egd spot calc --ean 8591824... --from 2025-01 --to 2025-12 -m 1.1 -a 300 -b month -o 2025.json

# Jednou stáhnout, pak zkoušet různé tarify offline
egd data --ean 8591824... --from 2025-01 --to 2025-12 -o 2025.csv
egd spot calc -i 2025.csv -m 1.15 && egd spot calc -i 2025.csv -a 500

# Dodavatel účtuje hodinové ceny i po přechodu na čtvrthodinový trh
egd spot calc -i export.xlsx -a 450 --price-resolution hourly

# Nikdy nedostat zaplaceno za spotřebu při záporných cenách
egd spot calc -i export.csv -a 400 --negative clamp-total

# Dodavatel: spot × 1,15 + 400, ale při záporných cenách jen samotný spot
egd spot calc -i export.csv -m 1.15 -a 400 --negative-multiplier 1 --negative-add-czk-mwh 0

# Porovnání s dvoutarifním fixním produktem (VT 4,20 / NT 2,90 Kč/kWh), NT 22–6 h a 13–15 h
egd spot calc -i export.csv -a 400 --fixed-price 4.2 --fixed-price-low 2.9 \
    --low-tariff-hours "22:00-06:00,13:00-15:00"

# Vlastní distribuční poplatky (ukázkové hodnoty!) ke spotovému i fixnímu výsledku
egd spot calc -i export.csv -m 1.15 -a 400 --vat --fixed-price 4.0 --fixed-price-low 3.0 \
    --low-tariff-hours "22:00-06:00" --distribution-high 2.0 --distribution-low 0.5 \
    --distribution-monthly-fee 300 -b month

# Jen ceny (měnu je nutné zvolit) a kurzy, kterými byly přepočteny
egd spot prices --from 2025-11 --currency czk --export ceny-2025-11.csv
egd spot prices --from 2025-11 --currency eur --export ceny-2025-11-eur.json
egd spot fx --from 2026-10-24 --to 2026-11-02 -o kurzy.csv
```

## Použití jako knihovny

```python
from datetime import date
from egd_api_client import EgdClient, EgdCredentials, make_session
from egd_api_client.timeutil import local_day_bounds_utc

client = EgdClient(make_session(), EgdCredentials(client_id, client_secret))
print(client.metering_points())  # [{"ean": "...", "typMereni": "B"}]
start, end = local_day_bounds_utc(date(2026, 10, 1), date(2026, 10, 31))
result = client.readings(ean, start, end, kind="consumption")  # nebo kind="production"
print(sum(r.kwh for r in result.readings), result.warnings)
```

`readings()` se postará o obnovu tokenu, dělení na 30denní úseky, exkluzivní `to`, převod
jednotek (kW → kWh) a omezení na období, ke kterému máte oprávnění. Stavební bloky pro
spotové ceny jsou v `egd_api_client.spot` (`ote.OteClient`, `cnb.CnbRates`,
`calculator.compute`).

## Jak se počítá cena

Pro každý čtvrthodinový interval *i* se spotřebou *E<sub>i</sub>* (kWh) a cenou denního trhu
*S<sub>i</sub>* (Kč/MWh):

```
cena_i   = S_i × násobek + přirážka                  [Kč/MWh, bez DPH]
           (S_i < 0: násobek / přirážka pro záporné ceny, jsou-li zadány)
energie  = Σ E_i × cena_i / 1000                      [Kč]
poplatky = měsíční poplatek × (dny v období / dny v měsíci), sečteno po měsících
celkem   = (energie + poplatky) × (1 + DPH/100)
efektivní cena = celkem / Σ E_i                       [Kč/kWh]
```

* **Hodinové vs. čtvrthodinové ceny.** Do 30. 9. 2025 měl denní trh hodinové ceny; každá
  čtvrthodinová hodnota spotřeby dostane cenu své hodiny. Od 1. 10. 2025 se používá
  čtvrthodinová cena (nebo hodinová s `--price-resolution hourly`). Hodinová data spotřeby
  se rovnoměrně rozdělí na čtvrthodiny.
* **Záporné ceny.** Někteří dodavatelé při záporném spotu používají jiné koeficienty:
  `--negative-multiplier` / `--negative-add-czk-mwh` (v konfiguraci `[spot]`
  `negative_multiplier` / `negative_add_czk_mwh`). Co není zadáno, převezme se z běžného
  `-m` / `-a`. Která dvojice platí, rozhoduje znaménko původní ceny OTE (ostře pod 0),
  ještě *před* jakýmkoli ořezáním. Potom `--negative` určí:
  * `as-is` (výchozí) – `S × záp_násobek + záp_přirážka`; záporná cena snižuje účet
    (násobek > 1 dobropis zvětšuje);
  * `clamp-spot` – záporný spot se *před* použitím koeficientů nahradí nulou, cena je tedy
    `záp_přirážka` (přirážku za období se zápornou cenou platíte i tak);
  * `clamp-total` – `S × záp_násobek + záp_přirážka`, ořezáno zdola na 0 (za spotřebu
    nikdy nedostanete zaplaceno).

  Ověřte si podmínky svého dodavatele – liší se.
* **Porovnání s fixní cenou VT/NT.** S `--fixed-price-low` se každý čtvrthodinový interval
  ocení cenou NT za tu část, která spadá do okna nízkého tarifu, a cenou VT
  (`--fixed-price`) za zbytek; spotřeba v NT se zobrazí ve výpisu (`NT kWh`).
* **Distribuční poplatky** (distribuční cena EG.D, jistič, systémové služby, POZE, daň
  z elektřiny …) jsou stejné u každého dodavatele, proto se nezapočítávají, pokud je
  nezadáte – viz [Distribuční poplatky](#distribuční-poplatky).
* Výpis ukazuje i vaši **spotřebou váženou průměrnou spotovou cenu** vedle prostého průměru
  období – je-li vaše vyšší, spotřebováváte více, když je elektřina drahá.
* Řádky `Avg spot price` jsou **surová cena OTE** (před koeficienty). Řádky
  **`Spot + tariff`** ukazují cenu energie, kterou byste skutečně platili – se započtenou
  přirážkou a násobkem včetně koeficientů a režimu pro záporné ceny, bez DPH a bez měsíčních
  poplatků, v CZK/MWh. Vážený průměr = `Energy (spot)` / spotřeba,
  prostý průměr = průměr tarifované ceny přes všechny intervaly období. Tyto řádky jsou
  přímo srovnatelné s `Avg fixed price`. V rozpisu je sloupec `Spot+tariff`, v CSV/JSON pole
  `avg_spot_tariff_czk_mwh` a v souhrnu JSON `avg_spot_tariff_time_weighted_czk_mwh`.
* Je-li zadaná fixní cena, výpis ukáže stejně i **průměrnou fixní cenu** – váženou vaší
  spotřebou a prostý průměr období (v CZK/MWh, bez DPH a bez měsíčních poplatků
  jako spotové průměry). S jedinou cenou jsou oba průměry
  rovny `--fixed-price`; s `--fixed-price-low` se liší – prostý průměr váží ceny VT/NT
  délkou oken (hodinami), vážený vaší spotřebou v NT a VT. V měsíčním/denním rozpisu je
  sloupec `Fixed avg` (vážený), v CSV/JSON pole `avg_fixed_czk_mwh` a v souhrnu JSON
  `avg_fixed_time_weighted_czk_mwh`.
* Období bez dat spotřeby nebo bez tržní ceny se vynechají a nahlásí jako varování (např.
  stav nouze 4. 7. 2025, kdy OTE 12 hodin nezveřejnil ceny).

## Přepočet EUR/CZK

`egd spot prices` vyžaduje volbu měny (`--currency czk|eur` je povinná):

* `czk` – hodinová éra (do 30. 9. 2025): vlastní ceny OTE v CZK; čtvrthodinová éra: ceny OTE
  v EUR × kurz ČNB pro den dodávky (viz níže);
* `eur` – vlastní ceny OTE v EUR v obou érách. Pro hodinovou éru se stahují zvlášť
  (`GetDamPriceE` s `InEur=true`), nepřepočítávají se zpět z CZK.

Sloupec `eur_czk` v exportu ukazuje použitý kurz ČNB (prázdný tam, kde OTE zveřejnil cenu
přímo v dané měně).

Od 1. 10. 2025 zveřejňuje OTE ceny denního trhu jen v EUR, takže je nutné je přepočítat.
Nástroj používá **kurz ČNB platný pro den dodávky**:

* ČNB vyhlašuje kurzy každý pracovní den kolem 14:30; kurz platí pro tento den a pro
  následující víkend a svátky až do dalšího vyhlášení;
* pracovní den dodávky tedy používá **svůj vlastní** kurz, sobota, neděle nebo svátek
  **poslední kurz vyhlášený před ním** (např. út 17. 11. 2026, svátek → po 16. 11.).

Přesně tento kurz uvádí sám OTE jako `EurRate` („Kurz ČNB pro daný den dodávky“) ve svém
denním indexu (`GetDamIndexE`) a používal ho pro své ceny v CZK před říjnem 2025. Ověřeno
den po dni proti OTE a proti API ČNB za leden–září 2025 a červen–listopad 2026 bez jediného
rozdílu (pravidlo „předchozí pracovní den“ by se lišilo ve většině dní). `egd spot fx`
ukazuje, který kurz a z kterého dne se pro každý den použil. Pro dny před 1. 10. 2025 se nic
nepřepočítává – použijí se přímo ceny OTE v CZK. Ceny se ukládají do mezipaměti v EUR
a přepočítávají se při každém spuštění, takže kurz v mezipaměti nikdy „nezamrzne“. Kurz pro
pracovní den existuje až po ~14:30 daného dne; do té doby (a pro zítřejší ceny, které OTE
zveřejňuje kolem 13:00) se použije poslední dostupný kurz, takové údaje jsou tedy předběžné.

## Nízký tarif (NT)

OpenAPI EG.D o tarifech nic neříká. Ověřeno proti živému API s odběrným místem typu B
(listopad 2026): `/rest/om` vrací jen `ean` a `typMereni`, seznam profilů nemá pro měření
A/B rozdělení VT/NT a neexistují koncové body pro HDO, distribuční sazbu ani jistič. Starší
koncový bod pro chytré elektroměry (`/rest/c/…`) sice uvádí registrové profily VT/NT, ale
jen pro chytré elektroměry C1 (odběrné místo A/B odmítne).

Časy NT proto **vždy zadáváte sami**, nic se nepředpokládá ani nestahuje:

* `--low-tariff-hours "22:00-06:00,13:00-15:00"` – časy NT pro každý den. Rozsahy mohou
  přecházet přes půlnoc a hranice nemusí ležet na čtvrthodinách: interval, který je v NT
  jen zčásti, se rozdělí poměrně.
* `--low-tariff-hours-weekend "…"` – jiné časy pro sobotu a neděli (nepovinné).
* `--holidays weekday|weekend` – české státní svátky se řídí časy svého dne v týdnu
  (výchozí) nebo víkendovými časy.
* Totéž lze nastavit v konfigurační sekci `[low_tariff]` (`hours`, `hours_weekend`,
  `holidays`).

`--fixed-price-low` bez zadaných časů NT skončí chybou. Své časy NT najdete v portálu
Distribuce24 nebo na egd.cz (*Časy platnosti nízkého tarifu*) podle svého kódu HDO. Pokud se
v průběhu období změnily, spočítejte období zvlášť. Časy jsou místní (Europe/Prague), takže
dny přechodu na letní/zimní čas se zpracují přirozeně.

## Distribuční poplatky

OpenAPI EG.D neposkytuje žádné údaje o distribučním tarifu (žádnou distribuční sazbu jako
D02d/D25d, velikost jističe ani poplatky) a nástroj záměrně neobsahuje žádné regulované
ceníky. **Své poplatky můžete zadat sami** – v Kč bez DPH, stejně jako fixní ceny:

| Volba | Konfigurace `[distribution]` | Význam |
|---|---|---|
| `--distribution-high KČ_KWH` | `high_czk_kwh` | poplatek za kWh ve vysokém tarifu (VT); bez NT poplatku platí pro **veškerou** spotřebu |
| `--distribution-low KČ_KWH` | `low_czk_kwh` | poplatek za kWh v nízkém tarifu (NT); vyžaduje `--low-tariff-hours` (jinak chyba) a `--distribution-high` |
| `--distribution-monthly-fee KČ` | `monthly_fee` | pevné měsíční platby (jistič / rezervovaná kapacita apod.), poměrně podle dnů |

Všechny složky ceny za kWh své sazby (distribuce, systémové služby, POZE, daň z elektřiny,
poplatek OTE …) si sečtěte sami, zvlášť pro VT a NT. Poplatky se spočítají jednou pro každý
interval (podíl NT podle vašich časů NT, stejně jako u fixní ceny NT) a připočtou se
k **oběma** výsledkům, s `--vat` včetně DPH:

* výpis ukazuje `Distribution`, `Spot + distrib.` a (s `--fixed-price`) `Fixed + distrib.`;
  `TOTAL`, `Fixed tariff` a `Difference` zůstávají jen za dodavatele (distribuční část je pro
  oba stejná, rozdíl se tedy nemění);
* rozpis přidá sloupce `Distrib.` (bez DPH), `Total+D` a `Fixed+D` (s DPH);
* exporty JSON/CSV přidají `distribution_czk`, `total_with_distribution_czk`,
  `avg_price_with_distribution_czk_kwh` a `fixed_total_with_distribution_czk`.

## Zdroje dat

| Data | Zdroj | Poznámky |
|---|---|---|
| Spotřeba | OpenAPI EG.D – `POST https://idm.distribuce24.cz/oauth/token` (client credentials, scope `namerena_data_openapi`), `GET https://data.distribuce24.cz/rest/spotreby?ean=…&profile=ICC1&from=…&to=…` | Profil `ICC1` = průměrný čtvrthodinový výkon v kW (÷4 → kWh); `ICQ2` = kWh; `DCQC` = chytré elektroměry C1. Nejvýše 3000 hodnot na dotaz → stahuje se po 30denních úsecích (`to` je exkluzivní; stránkování se nepoužívá, protože `pageStart=1` jednu hodnotu vynechá). [Uživatelský návod (PDF)](https://www.egd.cz/sites/default/files/2026-05/uzivatelsky_navod_openapi_abc.pdf), [starší verze](https://portal.distribuce24.cz/download/Uzivatelsky_navod_OpenApi.pdf) |
| Spotové ceny | Veřejná SOAP služba OTE-CR `https://www.ote-cr.cz/pw-data/services/PublicDataService` – `GetDamPriceE` (hodinové, CZK/EUR) a `GetDamPricePeriodE` (čtvrthodinové, EUR) | Jeden kalendářní měsíc na dotaz. [Manuál](https://www.ote-cr.cz/cs/dokumentace/xsd-wsdl-manual-pubweb) |
| EUR/CZK | Denní kurzy ČNB, [`year.txt`](https://www.cnb.cz/en/financial-markets/foreign-exchange-market/central-bank-exchange-rate-fixing/central-bank-exchange-rate-fixing/year.txt?year=2025) | Kurz platný pro den dodávky: kurz vyhlášený ten den (~14:30), o víkendu a ve svátek poslední vyhlášený před ním. Přesně odpovídá `EurRate` OTE; viz [Přepočet EUR/CZK](#přepočet-eurczk). Váš dodavatel může používat jiný kurz. |

Všechny časy se interně zpracovávají v UTC a vypisují v čase Europe/Prague; dny přechodu
na letní/zimní čas (23 / 25 hodin) jsou ošetřeny.

### Podrobnosti API EG.D, které můžete potřebovat upravit

* `--api-variant c1-legacy` použije samostatný koncový bod pro chytré elektroměry
  `/rest/c/spotreby` popsaný ve verzi návodu EG.D z prosince 2025 (kódy profilů ve stylu
  OBIS, `zdrojDat`). Aktuální návod (květen 2026) uvádí A, B i C1 pod společným
  `/rest/spotreby`, který je výchozí.
* Časová značka označuje **začátek** intervalu (ověřeno proti živému API: poslední hodnota
  dne je 23:45 místního času a dotaz sahající do dnešní místní půlnoci je odmítnut).
  `--api-timestamps end` existuje jen pro starší koncový bod.
* Profil spotřeby se volí podle typu měření odběrného místa (`/rest/om`): `ICC1` pro A/B,
  `DCQC` pro chytré elektroměry C1; pro výrobu `ISC1` / `DSQC`. Změnit lze přes `--profile`
  (např. `ICQ2`, který dává stejné kWh jako `ICC1`).
* EG.D poskytuje data jen **do včerejška**, nejvýše **3 roky** zpět a jen za období, ke
  kterému má váš účet oprávnění. Nástroj období podle toho zkrátí a oznámí, od kterého dne
  jsou data k dispozici.
* Hodnoty s náhradními statusy, které EG.D označuje jako konečné (`B`, `E`, `M`, `N`), se
  použijí bez upozornění; dočasné (`G`, `V`) vyvolají varování.
* `--test-env` komunikuje s testovacím prostředím EG.D (`test.distribuce24.cz`), pro které si
  v portálu můžete vygenerovat testovací údaje.

## Vstupní soubory

`--input` přijímá:

* **XLSX** exportovaný z portálu Distribuce24 (vyžaduje doplněk `xlsx`),
* **CSV** odkudkoli: oddělovač `;`, `,` nebo tabulátor, desetinná čárka i tečka, UTF-8 nebo
  Windows-1250, data jako `01.11.2025 00:15`, `2025-11-01 00:15`, ISO 8601 s posunem,
  samostatné sloupce data a času, `24:00`,
* vlastní CSV nástroje zapsané příkazem `egd data` (`start,kwh,status`).

Řádek záhlaví a sloupce s datem/časem a spotřebou se rozpoznají automaticky (záhlaví jako
*Datum*, *Čas*, *Spotřeba*, *ICC1*, *kWh*); změnit lze přes `--time-column`, `--value-column`
(název nebo index od 0). Jednotky se berou ze záhlaví (`kW` → průměrný výkon, převede se na
energii; `kWh`) nebo z `--unit`. Zda časová značka označuje začátek, nebo konec intervalu, se
pozná z prvního řádku (`00:00` → začátek, `00:15` → konec) nebo se nastaví `--timestamps`.
Místní časy v opakované hodině na konci letního času se rozliší podle pořadí řádků.

## Omezení

* API EG.D bylo ověřeno naživo s odběrným místem typu měření **B** (listopad 2026). Typ C1
  (profil `DCQC`) odpovídá oficiálnímu seznamu profilů, ale na skutečném elektroměru C1
  zatím vyzkoušen nebyl.
* Formát exportu XLSX z portálu není zdokumentovaný; automatické rozpoznání je heuristické.
  Pokud vybere špatný sloupec, použijte volby pro sloupce.
* Přesné vzorce dodavatelů se liší (kurz, zaokrouhlování, zacházení se zápornými cenami,
  poplatek OTE, náklady na odchylku); výsledky jsou odhad, ne faktura.
* Výrobu / přetoky do sítě lze stáhnout přes `egd data --kind production`, ale výkupní ceny
  `egd spot calc` zatím nemodeluje.
* Časy NT a distribuční poplatky API neposkytuje – zadáváte je ručně.

## Řešení problémů

**`ZoneInfoNotFoundError: 'No time zone found with key Europe/Prague'`** (Termux/Android,
Windows, minimalistické kontejnery) – systém nemá databázi časových pásem. Nové verze
instalují balíček `tzdata` automaticky. Starší instalaci opravíte jedním z příkazů:

```bash
pipx install --force "git+https://github.com/OOtta/egd-api-client"   # přeinstalace
pipx inject egd-api-client tzdata                                     # jen doplnit tzdata
```

## Vývoj

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest --cov=egd_api_client   # veškeré HTTP je simulované; přihlašovací údaje nejsou potřeba
ruff check . && ruff format --check .
python examples/generate_sample.py 2025-01-01 2025-12-31 > /tmp/rok.csv   # syntetická data
```

Struktura projektu:

```
src/egd_api_client/
  client.py          klient OpenAPI EG.D (token, odběrná místa, profily, naměřená data)
  readings_io.py     import CSV/XLSX, export do kanonického CSV / JSON
  lowtariff.py       časy NT (ruční zadání), české státní svátky
  models.py          Reading, PricePoint, LoadResult
  cli.py             vstupní bod `egd` a základní příkazy (meters, profiles, data)
  cli_common.py      společné volby CLI, práce s obdobím, kontext
  config.py, cache.py, timeutil.py, http.py
  spot/              funkce spotového tarifu (`egd spot …`)
    cli.py           příkazy calc, prices, fx
    ote.py           ceny denního trhu OTE (SOAP) s mezipamětí
    cnb.py           kurzy ČNB EUR/CZK
    calculator.py    výpočet a agregace nákladů
    tariff.py        definice spotového, fixního a distribučního tarifu
    report.py        textový výpis a export CSV/JSON
tests/               testy pytest se zaznamenanými daty OTE/ČNB
```

Nové funkce patří do vlastního podbalíčku s funkcí `register(subparsers, …)` v jeho `cli.py`,
stejně jako `spot`.

## Licence

[MIT](LICENSE). Projekt není spojen s EG.D, OTE ani ČNB.
