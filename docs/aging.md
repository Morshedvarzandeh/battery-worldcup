# How it is wearing

A passport reports state of health as one number. That number answers "how
worn is it" and cannot answer the question anyone actually has, which is **is
that normal?**

87% is excellent on a nine-year-old Leaf and disappointing on a two-year-old
Kona, and nothing in the reading says which. What supplies the yardstick is a
fade curve for the pack model, which is why this only became possible once
[battery-data](battery-data.md) could tell us which pack is in the car.

## What it produces

```
WEAR (ageing normally)
  Yours is at 81% after about 7 years, and most batteries like it are
  around 75%. That is normal wear.
  At this rate it stays good enough to sell as a working battery for
  about 4 years.
  measured 81.0% vs 75.3% typical (+/-5.8 points across the model),
  fading at 0.77x the usual rate
  850 cycles against 369 typical for its age
  curve: Nissan Leaf ZE1 40 kWh, temperate climate
```

Three separate claims, and they are separate on purpose:

- **Where it stands** against others of the same model at the same age.
- **How hard it has been worked**, which is about the owner, not the pack.
- **When it stops being worth anything**, which is the part that moves money.

## The curve

Fade splits into two mechanisms that behave nothing alike.

**Calendar fade** happens whether the car moves or not, as the passivation
layer on the anode thickens. That process is diffusion-limited, so it goes with
the **square root of time** — a visible drop in the first year, then a long
flattening. Anyone who has watched a new EV lose three per cent quickly and then
almost nothing for years has seen this shape. A straight line fits neither end.

**Cycle fade** is wear from use, roughly linear in throughput.

```python
from battery_value.materials.degradation import load_degradation

leaf = load_degradation().for_pack_model("nissan-leaf-ze1-40")
leaf.expected_soh(8, rated_kwh=40)          # 0.810
leaf.expected_soh(8, cycles=1200, rated_kwh=40)   # harder-worked than typical
```

### The trap: double counting

Published fade figures come from real cars, which were being driven. A model's
headline fade therefore already contains a typical amount of cycling. Adding a
full cycle term on top charges the pack twice for the same kilometres.

So `fade_at_8y` is defined **at a reference annual mileage**, and the cycle term
prices only the *difference* between what a pack has actually done and what that
reference implies. A lightly used pack gets credit; a taxi gets charged. Both
are correct, and neither is possible without storing the reference alongside the
fade.

### Cooling, not chemistry

The strongest predictor of how a fleet ages is not the cathode. It is whether
the pack is cooled.

| | cooling | health at 8 years |
|---|---|---|
| Nissan Leaf ZE0 24 kWh | none | 72% |
| Nissan Leaf ZE1 40 kWh | none | 81% |
| Renault Zoe ZE40 | air | 83% |
| Hyundai Kona 64 kWh | liquid | 89% |
| Audi e-tron 55 | liquid | 90% |

The two Leafs share a badge and nothing else; the Zoe and the Kona are both
nickel packs of similar vintage. A chemistry-level model gets all of this
wrong, which is why the profile hangs off the pack model and falls back to
chemistry only when the model is unknown.

### Heat

Calendar fade is temperature-driven, so climate is the largest difference
between two otherwise identical packs. It is optional, defaults to temperate,
and is weighted by how exposed the model actually is — a liquid-cooled pack in
Seville is not in the trouble an uncooled one is.

```bash
bv value --file passport.json --climate hot
```

## What it refuses to say

Two guards, and they matter more than the arithmetic.

**No circular verdicts.** When state of health was itself estimated from age or
from cycle count, comparing it to the curve is comparing the curve to itself. It
would come back "exactly typical" no matter what the battery is really doing —
which reads like a finding and is not one. So a verdict is only offered when
there is independent evidence about this individual pack: a **measurement**.

A cycle count still says something real — how hard the pack has been worked —
and that is reported separately, because it is a different claim.

**A cohort is not a pack.** Real examples of one model at one age differ by
several points. Without a spread, a consumer can only say "yours is below
average", which is true of half of everything. Each profile carries one standard
deviation in health points, and a pack inside it is reported as normal. Being a
point below the mean is not news.

## The forecast

The pack's own fade rate anchors the projection: a battery already ageing badly
is not forecast to suddenly behave. The ratio of observed fade to cohort fade
scales the curve forward, bounded so that an implausible reading produces a
warning rather than a confident forecast built on a typo.

Two dates come out of it, and both are configuration rather than constants:

- when it drops below what a buyer wants in a replacement pack
  (`reuse.minimum_soh`, 75%)
- when it drops below what a storage integrator will rebuild
  (`second_life.minimum_soh`, 60%)

The first is the one worth acting on. A pack two years from falling out of the
resale market is worth selling now, and today's health reading does not say so.
That is why the engine raises it as a warning rather than leaving it in a chart.

## Where the numbers live

Bundled in `materials/data/degradation.json`, and read live from battery-data
when a database is configured, exactly like the recovery terms:

```bash
export BV_BATTERY_DATA_DSN='postgresql://user@host/batterydb'
```

battery-data's `degradation_profile` table carries the same fields with
provenance, a validity window and a region. All 34 profiles — 20 pack models and
14 chemistry fallbacks — value identically from either source, and
`tests/test_battery_data.py` checks the curve itself at five ages rather than
just comparing fields, because a profile that read back with the right numbers
and computed a different answer would pass a field check and still be wrong.

## Honesty about the profiles

These are cohort central estimates calibrated against published fleet-telemetry
studies, OEM warranty floors and aggregated owner-reported capacity readings.
They describe a population and never an individual pack, each carries a
confidence, and **a measured state of health always outranks anything here**.

The one place the model is knowingly approximate is that calendar and cycle fade
are added rather than combined. They share mechanisms, so the truth is somewhere
below the sum. The error is small in the range that matters and always in the
conservative direction.
