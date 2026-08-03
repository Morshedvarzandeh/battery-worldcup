# battery-value

**Scan the code on a battery. Find out what it is worth.**

A battery passport tells you how healthy a pack is. It does not tell you what
that health is worth in money. This module closes that gap: it reads the
passport, identifies the pack model and its components, builds a bill of
materials, prices it against current material and system markets, and reports
what the pack is worth by every route its owner could realistically take.

Two audiences, one engine:

- **The person holding the battery** gets a phone page with a number, a
  recommendation in plain words, and a file they can send to a garage or
  recycler. No chemistry acronyms, no percentages to interpret.
- **The specialist** gets the full audit trail — line-by-line workings, the
  bill of materials, every price with its source and date — folded away behind
  one toggle, and downloadable as a report.

> The Python package is `battery_value` and the command is `bv`. The Git
> repository is still named `battery-worldcup`; renaming it is a GitHub
> settings change, not a code one.

## What the owner sees

```
Your battery is worth about €2,058, and the best way to get
that is to take it apart and sell the parts.

  Take it apart and sell the parts    €2,058   ← best option
  Sell it as a working battery        €1,605
  Recycle it for the metals inside      −€63
  Reuse it for home or grid storage  −€1,140
```

Then **Share the report** — a single self-contained HTML file with the full
workings, which opens in any browser, prints to PDF, and goes straight into the
phone's share sheet for WhatsApp, email or Files.

## Why four numbers, not one

A retired pack does not have a single value. It has four, and the owner
realises whichever pays most:

| Route | What actually happens | Best for |
|---|---|---|
| **Sell it as a working battery** | A garage fits it to another vehicle of the same model | Healthy packs of models still on the road |
| **Take it apart and sell the parts** | A specialist splits it into modules and electronics | Older packs with a strong repair market |
| **Reuse it for home or grid storage** | Rebuilt into a stationary battery | Large packs, long-life chemistries |
| **Recycle it for the metals inside** | Shredded, metals recovered | Everything else — the value floor |

Recycling is always available, which makes it the floor. For LFP and sodium-ion
that floor is routinely **negative**: disposal costs more than the materials are
worth. That is a real answer, and it is reported as one.

## Install

```bash
pip install -e '.[all]'      # everything, including photo decoding
pip install -e '.[api]'      # the web UI and HTTP API
pip install -e .             # library and CLI only
```

Python 3.10+. Core dependencies are `pydantic` and `httpx`.

## Use it

### On a phone

```bash
bv serve
```

Open <http://localhost:8000>. The primary action is **Take a photo of the
code** — `capture="environment"` opens the rear camera directly. The photo is
decoded in the browser when the platform supports it, so a phone on a weak
signal sends a few hundred bytes of text instead of a multi-megabyte image;
otherwise it falls back to the server, which retries with contrast, upscaling,
sharpening and rotation before giving up.

### Command line

```bash
bv value --file passport.json           # value a pack
bv value --qr 'https://dpp.example.com/battery/AB123'
bv value --image pack-label.jpg         # decode a photo of the code
bv value --file p.json --json           # machine-readable

bv value --file p.json --report out.html          # shareable report
bv value --file p.json --report ./ --summary-only # no technical section

bv scan  --file passport.json           # read the passport, no valuation
bv prices                               # every price in use, and its source
bv packs --search bmw                   # browse the pack catalogue
bv serve                                # API + web UI on :8000
```

### HTTP API

```bash
curl -X POST localhost:8000/v1/value \
  -H 'Content-Type: application/json' \
  -d '{"payload": "https://dpp.example.com/battery/AB123", "currency": "EUR"}'
```

| Route | Purpose |
|---|---|
| `POST /v1/value` | Scan and value. Includes a `plain` block of ready-written wording |
| `POST /v1/report` | Self-contained HTML report, as a file download |
| `POST /v1/decode` | Photo of a code → its payload text |
| `POST /v1/value/image` | Decode and value in one call |
| `POST /v1/scan` | Read a passport without valuing it |
| `GET /v1/prices` | Current prices with provenance |
| `GET /v1/packs` | Pack model catalogue |
| `GET /v1/providers` | Which data layers are wired up |
| `GET /v1/health` | Liveness, dataset versions, photo-decoding availability |

OpenAPI docs at `/docs`.

### Python

```python
from battery_value import ValuationEngine
from battery_value.report import build_html_report

engine = ValuationEngine()
valuation = engine.value_scan("https://dpp.example.com/battery/AB123")

print(valuation.summary())
print(valuation.residual_value)             # €2,058
print(valuation.recommended.label)          # Dismantle and sell components

open("report.html", "w").write(build_html_report(valuation))
```

The plain-language wording lives in `battery_value.valuation.plain`, so any
client shows the same phrasing rather than inventing its own.

## Getting real market prices

**This is the part that needs your attention before quoting anyone a number.**

The module ships a dated baseline snapshot so it works offline on a fresh
clone, with no API keys. That snapshot is deliberately not current data: every
price carries a decaying confidence score, and a valuation resting on it
reports "Rough estimate" rather than false precision.

The awkward truth is that the prices which matter most — lithium carbonate,
cobalt and nickel sulphate, black mass payables — are assessed by subscription
agencies (Fastmarkets, Benchmark, SMM, Argus) and have no free live feed. So
the price layer is a chain you upgrade as you wire sources in:

```
manual  →  csv  →  keyed vendor API  →  free exchange proxy  →  bundled snapshot
```

The practical setup for most users is a CSV export from whatever subscription
you already have, which keeps licensed data on your own infrastructure:

```bash
export BV_PRICE_CSV=/etc/battery-value/prices.csv
bv prices                 # confirm what is actually being used
```

**[docs/market-data.md](docs/market-data.md) covers every source, what it
costs, what it covers, and how to add your own.**

## Getting the pack right

Identifying the pack model is the single biggest accuracy improvement
available. It supplies the component breakdown the parts-out route needs, a
model-specific replacement price for resale, and fills whatever the passport
left out.

Twenty common European EV and hybrid packs ship in the catalogue. Add your own
without forking:

```bash
export BV_PACK_CATALOGUE_DIR=/etc/battery-value/packs   # directory of JSON
export BV_PACK_API_URL=https://fleet.internal/api/packs # or a service
```

**[docs/pack-catalogue.md](docs/pack-catalogue.md)** has the schema and the
custom-layer API.

## Documentation

- **[The owner's view](docs/end-user.md)** — the plain-language rules, the
  photo-decoding path, and the shareable report.
- **[Methodology](docs/methodology.md)** — how each of the four values is
  calculated, what drives confidence, and the known limitations.
- **[Market data](docs/market-data.md)** — every price source, the
  contained-metal maths, recovery rates and refiner payables.
- **[Pack catalogue](docs/pack-catalogue.md)** — matching, components, extending.
- **[Passport formats](docs/passport-formats.md)** — carriers, schema adapters,
  and the awkward real-world shapes that are handled.

## How it fits together

```
photo of a code  →  decode  →  classify  →  fetch  →  normalise to BatteryPassport
                                                              ↓
                                              identify pack model, fill gaps
                                                              ↓
                                  resolve health  →  build bill of materials
                                                              ↓
                                            price everything, with provenance
                                                              ↓
                            price all four routes  →  pick best  →  sensitivity
                                                              ↓
                                    plain answer  +  shareable report
```

| Package | Responsibility |
|---|---|
| `passport/` | Decode photos, fetch, and normalise any passport schema |
| `packs/` | Identify the pack model and its components |
| `materials/` | Chemistry, bill of materials, recovery rates and payables |
| `market/` | Price providers, FX, caching, provenance |
| `valuation/` | Health, the four routes, sensitivity, plain-language wording |
| `report.py` | The self-contained file an owner sends on |
| `api/`, `cli.py` | HTTP service with phone UI, and the `bv` command |

## Design commitments

- **Plain by default, complete on demand.** The owner sees words and one
  number; the specialist expands one toggle or downloads the report. Neither
  audience is served a compromise.
- **Every number is auditable.** Each price carries its source, date and
  quality; each material line says whether it was declared or modelled; each
  route lists its assumptions.
- **Contained-metal maths is derived, not hard-coded.** Lithium carbonate is
  18.79% lithium because `compounds.py` parses `Li₂CO₃` against IUPAC atomic
  weights, not because someone typed 5.323.
- **Payables are modelled separately from recovery.** Refiners pay 60–70% of
  contained nickel value, not 100%. Conflating the two is the largest source of
  over-valuation in naive models.
- **Declared beats modelled, always.** Catalogue data fills gaps; it never
  overwrites what a passport states.
- **It works offline.** A fresh clone with no keys produces a real, auditable
  number — clearly labelled as a rough estimate.
- **Failures degrade, they do not crash.** A dead provider, an unreadable CSV
  or an unreachable FX feed is logged and skipped.

## Development

```bash
pip install -e '.[all]'
python -m pytest              # 282 tests, no network required
```

Tests run fully offline against an isolated cache. The QR tests degrade a clean
render the ways a real phone photo goes wrong — rotated, soft focus, poor
light, heavy JPEG, sensor noise — and check it still reads.

## Licence

MIT — see [LICENSE](LICENSE).
