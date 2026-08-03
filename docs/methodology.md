# How the residual value is calculated

## The central idea

A retired battery pack does not have one value. It has four, and the holder
realises whichever route pays most:

1. **Reuse** — sell it as a replacement traction pack.
2. **Parts-out** — dismantle it and sell modules and components individually.
3. **Second life** — repurpose it into a stationary storage product.
4. **Recycling** — shred it and recover the contained materials.

The module prices all four and reports the best. It also reports the others,
with the reason each is or is not available, because "your pack is worth €2,058"
is far less useful than "it is worth €2,058 if you part it out, €1,605 if you
sell it whole, and −€63 if you scrap it".

Recycling is always available. That makes it the **value floor**, and for
chemistries with little nickel or cobalt that floor is frequently below zero:
disposal costs more than the materials are worth. A negative residual value is a
real, correct answer, not a bug.

## Pipeline

```
QR payload
   ↓  passport/qr.py          classify: URL, GS1 Digital Link, inline JSON, identifier
   ↓  passport/resolver.py    fetch (SSRF-guarded) or decode
   ↓  passport/adapters/      detect schema, normalise into BatteryPassport
   ↓  packs/                  identify pack model, fill gaps, load component list
   ↓  valuation/health.py     resolve state of health and remaining life
   ↓  materials/bom.py        build the bill of materials
   ↓  market/                 price every material, with provenance
   ↓  valuation/pathways.py   price all four routes
   ↓  valuation/engine.py     pick the best, run sensitivity
ResidualValuation
```

## State of health

Health drives everything, and it is the field most often missing. It is resolved
from the best available evidence, and which evidence was used is recorded:

| Source | How | Confidence |
|---|---|---|
| `measured` | Declared, or derived from measured capacity ÷ nameplate | 1.00 |
| `cycles` | Rated cycle life is quoted to 80% SoH, so fade = 0.20 × (cycles ÷ rated life) | 0.72 |
| `age` | Calendar fade of 2.3%/year | 0.52 |
| `assumed` | Configured default, flagged as indicative only | 0.25 |

A measurement older than 180 days loses confidence progressively — a year-old SoH
reading on a pack still in service is a guess, not a measurement.

**Remaining life** is the cycles left before the pack falls below the second-life
floor (50% SoH), derived from the implied fade per cycle.

## Bill of materials

Declared composition always wins. EU Regulation 2023/1542 Annex XIII requires
cobalt, lithium, nickel and lead content to be declared, so a compliant passport
supplies most of what matters directly. Anything undeclared is modelled from the
chemistry's default intensity in `materials/data/chemistries.json`.

One subtlety matters. Material intensities are per kWh, but not everything scales
with energy:

- **Active materials** (Li, Ni, Co, Mn, graphite) scale with **energy** — the
  amount of cathode is fixed by how much charge the pack stores.
- **Structural metals** (Cu, Al, Fe) scale with **mass** — enclosure, busbars and
  module frames follow how heavy the pack is.

Applying one blanket scale factor to both is how an unusually heavy pack ends up
with a fictitious 20% more cobalt. The correction is clamped to ±40% because
beyond that the declared mass and the chemistry disagree too badly to extrapolate.

The remainder of pack mass (separator, binder, electrolyte solvent, plastics) is
tracked as inert and carries no recovery revenue.

## Pathway 1: Recycling

For every element, revenue is:

```
mass_kg × price_per_kg_contained × recovery_rate × payable_fraction
```

Both haircuts matter and are stored separately (see
[market-data.md](market-data.md#recovery-and-payables)). Nickel via hydrometallurgy
recovers at 95% and is paid at 68%, so it returns 65% of headline value.

Costs: discharge and dismantling, shredding to black mass, refining gate fee, and
dangerous-goods freight. End-of-life lithium batteries move as UN3480/3481 Class 9;
damaged or defective packs fall under ADR special provision 376, which multiplies
freight cost several-fold. That multiplier is applied from the pack's condition.

The engine evaluates every commercially available process for the chemistry
(hydrometallurgical, pyrometallurgical, lead smelting, NiMH smelting) and takes
the best. Pilot-stage routes like direct recycling are excluded by default: quoting
a value against a process nobody can currently sell into overstates what a holder
can realise today.

Recycling confidence depends on price quality and how much composition was
declared — deliberately **not** on state of health, because a tired pack contains
exactly as much nickel as a fresh one.

## Pathway 2: Reuse as a replacement pack

Usually the highest-value route for a healthy pack of a model still on the road,
because it competes against the OEM's retail replacement price rather than scrap.

```
gross = rated_kWh
      × oem_replacement_price_per_kWh     (model-specific from the catalogue)
      × used_vs_new_discount              (0.45)
      × soh^1.5                           (value falls faster than health)
      × age_factor                        (1 − 0.05/year, floored at 0.40)
      × demand_factor                     (0.8–1.15 by model demand)
```

Less refurbishment, testing, certification, a warranty reserve and freight.

Gated on SoH ≥ 75%, age ≤ 12 years, and no safety flag or blocking condition.
The health exponent of 1.5 is convex: buyers discount a tired pack more than
proportionally, because remaining life shortens faster than capacity falls.

## Pathway 3: Parts-out

Needs an identified pack model, because without a component list there is nothing
to price. Frequently the best route for older packs whose modules have a strong
repair and DIY market even when the whole pack is unsellable.

```
gross = Σ reusable components × used value × soh factor × sell-through (0.85)
      + Σ non-reusable components × material price × scrap payable (0.60)
costs = (fixed setup + Σ dismantling minutes) × HV technician rate
      + freight
```

Only modules are health-adjusted: a contactor box or BMS is worth the same
whether the cells behind it are tired or not. Module value is floored at 35% of
fresh, because a buyer is also purchasing the hardware, not only the energy.

## Pathway 4: Second life

Valued on what the pack can still deliver relative to the new battery it would
displace, rather than a flat percentage of new price:

```
usable_kWh = rated × soh × dod_window (0.90)
life_ratio = remaining_cycles ÷ new_system_cycle_life (6000), capped at 1.0
gross      = usable_kWh × new_pack_price_per_kWh × life_ratio
           × chemistry_suitability × demand_factor
```

Less repurposing (test, grade, rebuild, new BMS, certification), a warranty
reserve and freight.

The anchor is the **pack** price, not turnkey system price, because a repurposed
pack replaces cells and pack hardware — not the inverter, installation and
balance of plant.

Small packs often come out negative here: repurposing has a large fixed cost
(new BMS, certification) that a 40 kWh pack cannot amortise. That is a real
finding, and it is why second life is dominated by large packs and by LFP, whose
long cycle life gives a high `life_ratio`.

## Confidence

Each pathway reports its own confidence, and the headline confidence is that of
the recommended pathway. Inputs:

- **Price quality** — live > benchmark > manual > delayed > baseline, decaying
  about 0.4 points per 100 days of age.
- **Health evidence** — measured > cycles > age > assumed.
- **Composition** — declared beats modelled.
- **Pack model** — a confident catalogue match beats a generic fallback.

Below 35% the result is flagged as indicative only. A fresh clone with no
providers configured lands there deliberately: it is the signal to wire in live
prices, not a defect.

## Sensitivity

Two shocks are re-run through the whole pipeline, including pathway re-selection:

- **Material prices ±25%** — dominates recycling-bound packs.
- **State of health ±5 points** — dominates reuse and parts-out packs.

Which one dominates is itself informative: it tells you whether to spend effort
on better price data or on better health measurement.

For how these numbers are presented to a non-specialist, see
[end-user.md](end-user.md).

## Known limitations

- **The pack catalogue is small.** Twenty models covering common European EVs.
  Unmatched packs still value, but lose the parts-out route and use a generic
  replacement price. See [pack-catalogue.md](pack-catalogue.md) to extend it.
- **Used-part values are estimates.** They move with a thin second-hand market
  and should be refreshed from live listings.
- **No regional variation.** Costs are European road-freight and labour rates.
- **No time value.** Values are spot, not discounted over a holding period.
- **Sensitivity is one-at-a-time**, not a correlated Monte Carlo. Nickel and
  cobalt move together in reality, so the true band is wider than reported.
- **Recycling gate fees are modelled, not quoted.** Real contracts vary widely
  with volume, chemistry mix and contamination.
