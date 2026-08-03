# Where the market prices come from

This is the hardest part of the problem, so it gets its own document.

A battery passport tells you the pack's health. Turning health into money needs
prices for nickel, cobalt, lithium, copper, aluminium and for whole battery
systems. Those prices are not all equally easy to get, and pretending otherwise
is how a residual-value tool ends up confidently wrong.

## The honest picture

Battery materials split into two groups with very different data availability.

**Exchange-traded metals** — nickel, copper, aluminium, lead, cobalt metal.
These settle daily on the LME and are genuinely obtainable. Official LME data is
licensed, but delayed and futures-derived proxies are freely available.

**Assessed battery chemicals** — lithium carbonate and hydroxide, nickel and
cobalt sulphate, manganese sulphate, and black mass payables. These do not trade
on an exchange. They are *assessed*: a price reporting agency surveys the market
and publishes an index. Fastmarkets, Benchmark Mineral Intelligence, SMM and
Argus all do this, all by subscription, and all with licence terms that forbid
redistributing the numbers.

There is no free, reliable, real-time feed for lithium carbonate. Any tool that
claims one is either using a stale scrape or quietly substituting a futures
contract that does not track the physical market.

So this module does three things instead of pretending:

1. Ships a **dated baseline snapshot** so it always produces an auditable number.
2. Provides a **provider chain** that upgrades to live data as you wire sources in.
3. Attaches **provenance and a decaying confidence score** to every single price,
   so a valuation built on stale data reports low confidence rather than false
   precision.

## The provider chain

Providers are tried in order; the first that answers wins.

| Order | Provider     | Quality     | Key needed | Covers |
|-------|--------------|-------------|------------|--------|
| 1 | `manual`     | `manual`    | no  | Whatever the caller passes in |
| 2 | `csv`        | `benchmark` | no  | Whatever is in your CSV |
| 3 | `metals_api` | `live`      | yes | LME base metals, cobalt, lithium |
| 4 | `yahoo`      | `delayed`   | no  | Copper, aluminium, steel (futures proxies) |
| 5 | `baseline`   | `baseline`  | no  | Everything, as a dated snapshot |

```python
from battery_worldcup.market import build_resolver

resolver = build_resolver(
    currency="EUR",
    manual={"cobalt_sulphate": 8800},       # a price you have actually been quoted
    csv_path="prices/fastmarkets-export.csv",
)
```

Inspect what is actually wired up:

```bash
bwc prices                    # table of every price and its source
curl localhost:8000/v1/providers
```

### 1. Manual — the best price there is

If a recycler has quoted you a real offtake price, that beats every index. Manual
prices sit at the front of the chain.

```python
build_resolver(manual={"nickel_sulphate": 4250, "cobalt_sulphate": 8800})
```

Over HTTP:

```json
POST /v1/value
{"payload": "...", "manual_prices": {"cobalt_sulphate": 8800}}
```

### 2. CSV — how to use subscription data legally

This is the answer for the assessed chemicals. If your organisation subscribes to
Fastmarkets, Benchmark, SMM or Argus, export the assessments and point the module
at the file. The licensed numbers stay on your infrastructure; nothing is
redistributed.

```csv
form,price,currency,unit,as_of,source_detail
lithium_carbonate,12400,USD,t,2026-07-30,Fastmarkets MB-LI-0029
lithium_hydroxide,11250,USD,t,2026-07-30,Fastmarkets MB-LI-0033
cobalt_sulphate,8350,USD,t,2026-07-30,Fastmarkets MB-CO-0004
nickel_sulphate,4180,USD,t,2026-07-30,Fastmarkets MB-NI-0246
```

```bash
export BWC_PRICE_CSV=/etc/battery-worldcup/prices.csv
bwc value --file passport.json
```

Refresh it on whatever cadence your subscription allows — a nightly cron writing
that file is usually enough, since these indices are assessed daily or weekly.

### 3. Keyed vendor APIs

`metals-api.com` covers LME base metals plus cobalt and lithium on paid tiers.
Set the key and it activates:

```bash
export METALS_API_KEY=your-key
```

Verify the units your plan returns before relying on it. Vendors change symbol
conventions between tiers, and a unit error is silent and large: reading a
copper price as USD/tonne when it is USD/pound understates copper by 2,200x.
`SymbolSpec` declares the unit per symbol precisely so this is explicit rather
than assumed.

### 4. Free exchange proxies

Yahoo Finance's chart endpoint gives unauthenticated access to COMEX futures.
It covers copper, aluminium and steel — real, but a minority of a lithium-ion
pack's material value, and futures are a proxy for physical metal rather than
the same thing. Everything from here is tagged `delayed`.

### 5. The bundled baseline

`market/data/baseline_prices.json` is a dated snapshot with a documented
reference for every line. It guarantees the module works offline, with no keys,
on a fresh clone. It is explicitly **not** current data: quotes carry
`quality=baseline`, and confidence decays about 0.4 points per 100 days, so a
valuation resting on it reports low confidence and the engine emits a warning.

Update it by editing the JSON and moving `snapshot_date` forward.

## Adding your own source

Any JSON HTTP endpoint can become a provider without writing a class:

```python
from battery_worldcup.market import HttpJsonProvider, SymbolSpec, build_resolver
from battery_worldcup.market.types import PriceQuality
from battery_worldcup.units import MassUnit

internal = HttpJsonProvider(
    provider_key="internal_desk",
    provider_label="Trading desk marks",
    provider_quality=PriceQuality.BENCHMARK,
    url_template="https://prices.internal/api/v2/{symbol}?key=${DESK_API_KEY}",
    symbols={
        "lithium_carbonate": SymbolSpec("LI2CO3-BG", MassUnit.TONNE, "USD"),
        "cobalt_sulphate": SymbolSpec("COSO4-BG", MassUnit.TONNE, "USD"),
    },
    price_path="data.price.mid",
    date_path="data.asOf",
    required_env=("DESK_API_KEY",),
)

resolver = build_resolver(extra_providers=[internal])
```

`${VAR}` placeholders read from the environment, so keys never appear in code.
For anything more unusual, subclass `PriceProvider` and implement `fetch`.

## Currency

Conversion is anchored on the **ECB daily reference rates** — free, no key, no
quota, no licence restriction, which makes it the right default. If the feed is
unreachable the bundled fallback rates take over and are marked as such.

All dataset costs (logistics, processing, refurbishment, labour) are quoted in
EUR and converted through `PathwayContext.from_eur`, so asking for USD converts
every line rather than relabelling it.

## Units and contained metal

The single most common modelling error in this domain is confusing the price of
a *compound* with the price of the *metal in it*.

Lithium carbonate at USD 12,500/t is **not** lithium at USD 12,500/t. Li₂CO₃ is
18.79% lithium by mass, so contained lithium costs USD 66,500/t — the industry's
"LCE factor" of 5.323.

Every such factor is computed from the chemical formula and IUPAC atomic weights
in `compounds.py`, never hard-coded:

| Traded form | Formula | Contained | Fraction |
|---|---|---|---|
| Lithium carbonate | Li₂CO₃ | Li | 18.79% |
| Lithium hydroxide monohydrate | LiOH·H₂O | Li | 16.54% |
| Cobalt sulphate heptahydrate | CoSO₄·7H₂O | Co | 20.97% |
| Nickel sulphate hexahydrate | NiSO₄·6H₂O | Ni | 22.33% |
| Manganese sulphate monohydrate | MnSO₄·H₂O | Mn | 32.51% |

## Recovery and payables

Contained metal value is still not what the holder receives. Two haircuts apply:

- **Recovery rate** — the share that physically survives the process.
- **Payable fraction** — the share of the recovered metal's value the refiner
  actually pays. Black mass contracts typically pay 60–70% for nickel and cobalt,
  and much less for lithium.

Effective revenue is `price × recovery_rate × payable_fraction`. For nickel via
hydrometallurgy that is 0.95 × 0.68 = **65% of headline market value**. Ignoring
payables is the largest single source of over-valuation in naive models, which is
why the two factors are stored and reported separately.

## Checking your setup

```bash
bwc prices                      # what is wired up, and how old each price is
bwc value --file p.json         # warns when prices exceed 45 days old
```

In the API response, `prices.confidence` and `prices.sources_used` tell you the
same thing. If you see `{"baseline": 12}`, you are running entirely on the
snapshot — fine for development, not for quoting a customer.
