# battery-worldcup

**Scan a battery passport. Find out what the pack is actually worth.**

A battery passport tells you how healthy a pack is. It does not tell you what
that health is worth in money. This module closes that gap: it reads the
passport, identifies the pack model and its components, builds a bill of
materials, prices it against current material and system markets, and reports
the residual value by every route the holder could realistically take.

```
$ bwc value --file examples/leaf-40-passport.json

--------------------------------------------------------------------------
  Nissan Leaf ZE1 40 kWh
--------------------------------------------------------------------------
  40 kWh nameplate | 81% state of health | NMC532 | 303 kg

  RESIDUAL VALUE   €2,058
  per kWh          €51.46    confidence 86%
  range            €1,935 to €2,182 (expected €2,058)
  main driver      State of health +5 points

  PATHWAYS
     Resale as replacement pack                     €1,605  conf  95%
   * Dismantle and sell components                  €2,058  conf  86%
     Second-life stationary storage                -€1,140  conf  80%
     Material recycling                               -€63  conf  14%

  BREAKDOWN: Dismantle and sell components
    + Battery modules                                    €1,995
        24 modules x 105 EUR x 81% health x 85% sell-through
    + Battery management system and slave boards           €299
    + HV junction box, contactors, fuses and current sensor €218
    + Scrap value of non-reusable parts                    €140
    + Cooling plates, hoses and manifolds                   €80
    - HV dismantling labour                                €508
    - Collection and DG freight                            €167
    ============================================================
      NET                                                €2,058
```

## Why four numbers, not one

A retired pack does not have a single value. It has four, and the holder
realises whichever pays most:

| Route | What it means | Best for |
|---|---|---|
| **Reuse** | Sell it as a replacement traction pack | Healthy packs of models still on the road |
| **Parts-out** | Dismantle and sell modules and components | Older packs with a strong repair market |
| **Second life** | Repurpose into stationary storage | Large packs, long-life chemistries |
| **Recycling** | Shred and recover the materials | Everything else — the value floor |

Recycling is always available, which makes it the floor. For LFP and sodium-ion
that floor is routinely **negative**: disposal costs more than the materials are
worth. That is a real answer, not an error.

## Install

```bash
pip install -e '.[all]'      # everything
pip install -e .             # library and CLI only
```

Python 3.10+. Core dependencies are `pydantic` and `httpx`.

## Use it

### Command line

```bash
bwc value --file passport.json          # value a pack
bwc value --qr 'https://dpp.example.com/battery/AB123'
bwc value --image pack-label.jpg        # needs the [scan] extra
bwc value --file p.json --json          # machine-readable

bwc scan  --file passport.json          # read the passport, no valuation
bwc prices                              # every price in use, and its source
bwc packs --search bmw                  # browse the pack catalogue
bwc serve                               # API + web UI on :8000
```

### Web UI

`bwc serve`, then open <http://localhost:8000>. Scan with the camera, upload a
photo of the code, or paste a payload. The result shows the headline value, all
four routes compared, a line-by-line breakdown, what is inside the pack, and
every market price with its date and source.

Camera scanning decodes in the browser via the `BarcodeDetector` API, so no
server-side decoder is needed on the common path.

### HTTP API

```bash
curl -X POST localhost:8000/v1/value \
  -H 'Content-Type: application/json' \
  -d '{"payload": "https://dpp.example.com/battery/AB123", "currency": "EUR"}'
```

| Route | Purpose |
|---|---|
| `POST /v1/value` | Scan and value |
| `POST /v1/value/image` | Value from an uploaded QR image |
| `POST /v1/scan` | Read a passport without valuing it |
| `GET /v1/prices` | Current prices with provenance |
| `GET /v1/packs` | Pack model catalogue |
| `GET /v1/providers` | Which data layers are wired up |
| `GET /v1/health` | Liveness and dataset versions |

OpenAPI docs at `/docs`.

### Python

```python
from battery_worldcup import ValuationEngine

engine = ValuationEngine()
valuation = engine.value_scan("https://dpp.example.com/battery/AB123")

print(valuation.summary())
print(valuation.residual_value)             # €2,058
print(valuation.recommended.label)          # Dismantle and sell components

for pathway in valuation.pathways:
    print(pathway.label, pathway.net_value, pathway.eligible)
```

## Getting real market prices

**This is the part that needs your attention before quoting anyone a number.**

The module ships a dated baseline snapshot so it works offline on a fresh clone,
with no API keys. That snapshot is deliberately not current data: every price
carries a decaying confidence score, and a valuation resting on it reports low
confidence and warns you.

The awkward truth is that the prices which matter most — lithium carbonate,
cobalt and nickel sulphate, black mass payables — are assessed by subscription
agencies (Fastmarkets, Benchmark, SMM, Argus) and have no free live feed. So the
price layer is a chain you upgrade as you wire sources in:

```
manual  →  csv  →  keyed vendor API  →  free exchange proxy  →  bundled snapshot
```

The practical setup for most users is a CSV export from whatever subscription
you already have, which keeps licensed data on your own infrastructure:

```bash
export BWC_PRICE_CSV=/etc/battery-worldcup/prices.csv
bwc prices                # confirm what is actually being used
```

**[docs/market-data.md](docs/market-data.md) covers every source, what it costs,
what it covers, and how to add your own.**

## Getting the pack right

Identifying the pack model is the single biggest accuracy improvement available.
It supplies the component breakdown that the parts-out route needs, a
model-specific replacement price for the reuse route, and fills whatever the
passport left out.

Twenty common European EV and hybrid packs ship in the catalogue. Add your own
without forking:

```bash
export BWC_PACK_CATALOGUE_DIR=/etc/battery-worldcup/packs   # directory of JSON
export BWC_PACK_API_URL=https://fleet.internal/api/packs    # or a service
```

**[docs/pack-catalogue.md](docs/pack-catalogue.md)** has the schema and the
custom-layer API.

## Documentation

- **[Methodology](docs/methodology.md)** — how each of the four values is
  calculated, what drives confidence, and the known limitations.
- **[Market data](docs/market-data.md)** — every price source, the contained-metal
  maths, recovery rates and refiner payables.
- **[Pack catalogue](docs/pack-catalogue.md)** — matching, components, extending.
- **[Passport formats](docs/passport-formats.md)** — carriers, schema adapters,
  and the awkward real-world shapes that are handled.

## How it fits together

```
QR payload  →  classify  →  fetch/decode  →  normalise to BatteryPassport
                                                      ↓
                                      identify pack model, fill gaps
                                                      ↓
                          resolve health  →  build bill of materials
                                                      ↓
                                    price everything, with provenance
                                                      ↓
                    price all four routes  →  pick best  →  sensitivity
```

| Package | Responsibility |
|---|---|
| `passport/` | Scan, fetch, and normalise any passport schema |
| `packs/` | Identify the pack model and its components |
| `materials/` | Chemistry, bill of materials, recovery rates and payables |
| `market/` | Price providers, FX, caching, provenance |
| `valuation/` | Health, the four pathways, sensitivity |
| `api/`, `cli.py` | HTTP service with web UI, and the `bwc` command |

## Design commitments

- **Every number is auditable.** Each price carries its source, date and quality;
  each material line says whether it was declared or modelled; each pathway lists
  its assumptions. Nothing is asserted without provenance.
- **Contained-metal maths is derived, not hard-coded.** Lithium carbonate is
  18.79% lithium because `compounds.py` parses `Li₂CO₃` against IUPAC atomic
  weights, not because someone typed 5.323.
- **Payables are modelled separately from recovery.** Refiners pay 60–70% of
  contained nickel value, not 100%. Conflating the two is the largest source of
  over-valuation in naive models.
- **Declared beats modelled, always.** Catalogue data fills gaps; it never
  overwrites what a passport states.
- **It works offline.** A fresh clone with no keys produces a real, auditable
  number — clearly labelled as low confidence.
- **Failures degrade, they do not crash.** A dead provider, an unreadable CSV or
  an unreachable FX feed is logged and skipped.

## Development

```bash
pip install -e '.[all]'
python -m pytest              # 217 tests, no network required
```

Tests run fully offline against an isolated cache: a test that silently reached
the network would be flaky and would stop exercising the fallback paths that
matter most.

## Licence

MIT — see [LICENSE](LICENSE).
