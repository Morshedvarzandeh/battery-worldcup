# Reading from battery-data

[battery-data](https://github.com/Morshedvarzandeh/battery-data) is a
provenance-first Postgres database of battery specifications. It already models
the thing this module needs most and models it better than a bundled JSON file
could: which pack is fielded in which vehicle, what that pack is assembled
from, and — crucially — **on what evidence**.

So battery-value reads it rather than keeping a rival copy.

## The split

| | battery-data | battery-value |
|---|---|---|
| Which pack is in which car | ✅ owns it | reads it |
| Pack assembly, chemistry, mass, energy | ✅ owns it | reads it |
| How fast each pack model wears out | ✅ owns it | reads it |
| Recovery rates and refiner payables | ✅ owns it | reads it |
| Used-part and OEM replacement prices | ✅ owns it | reads it |
| **Live metal prices** | deliberately absent | ✅ owns it |
| **Turning all of it into a number** | — | ✅ owns it |

Metal prices stay out of battery-data on purpose. They are a daily series, the
ones that matter are licensed assessments, and their terms forbid
redistribution — storing them would make that repository undistributable. See
[market-data.md](market-data.md).

## Three layers

Configured independently, tried in order, exactly like the price providers:

```bash
# 1. Postgres. Richest: sees the attribution columns directly.
export BV_BATTERY_DATA_DSN='postgresql://user@host/batterydb'
pip install 'battery-value[batterydata]'

# 2. HTTP. No database coupling.
export BV_BATTERY_DATA_URL='http://localhost:8080'

# 3. The bundled snapshot. Always there, needs nothing.
```

```bash
bv packs                 # what the chain resolves to
curl localhost:8000/v1/providers
```

The bundled catalogue is a **generated cache**, not a second source of truth.
Refresh it and commit the result, so a fresh clone still works offline:

```bash
BV_BATTERY_DATA_DSN=... bv sync
```

`bv sync` refuses to write an empty catalogue over a working one, so a
misconfigured database cannot silently blank the fallback.

## Attribution is not decoration

battery-data deliberately keeps weakly evidenced claims rather than discarding
them — forum consensus, inference from form factor — and marks them with a
`basis` and a `confidence`. Its own schema comment says an unsourced "everyone
knows the Model Y uses 2170s" is what the table exists to keep out.

battery-value honours that. A pack whose vehicle link is `community_reported`
or `inferred`, or carries a confidence below `MINIMUM_ATTRIBUTION_CONFIDENCE`
(0.5), is **not used**. Valuing a pack against a guessed identity would launder
the guess into a number with a currency symbol on it, and the output would look
exactly as confident either way.

A dropped row means the pack is reported as unidentified, which costs the
parts-out route and widens the estimate. That is the correct outcome.

## What the export writes back

`tools/export_to_battery_data.py` regenerates
`seed/002_packs_and_valuation.sql` in battery-data:

- pack models as `product` (kind=`pack`) + `product_revision`
- modules as their own products, linked by `product_assembly`
- vehicles as `application`, linked by `product_application` with
  `basis='teardown'` and the catalogue's own confidence carried through
- nameplate energy and pack mass as `observation` rows
- recovery processes, yields, payables, treatment costs, DG freight tariffs,
  used-part values, replacement prices and model assumptions

Two details worth knowing, both of which their schema enforced rather than
suggested:

**Recovery and payable stay separate columns.** `recovery_yield` holds both,
and `v_recovery_economics` exposes the product. A metal recovered at 95% and
paid at 68% returns 65% of headline value, and a schema with one column keeps
whichever number the source happened to quote.

**Nameplate energy declares what it does not know.** battery-data rejects an
`energy` observation with no rate and temperature, because nameplate energy
without them is not comparable. The catalogue's figures are OEM ratings whose
conditions are unstated, so the export records exactly that via
`condition_set.unstated` rather than inventing a plausible 25 °C.

## Recovery terms are read live too

Packs are not the only thing sourced from battery-data. When a DSN is
configured, the engine also reads the recovery rates, refiner payables,
treatment costs, dangerous-goods tariffs and model calibration from it, taking
only rows valid **today** and never a regulatory minimum — a floor on physical
recovery makes no claim about payment.

```python
from battery_value.materials.battery_data import recovery_library

terms = recovery_library()      # live when configured, bundled otherwise
terms.get("hydrometallurgical").recovery_for("Ni").value_yield   # 0.646
```

This matters because payables are negotiated and move. A term agreed in one
year silently pricing a pack in another is the failure mode battery-data's
validity windows exist to prevent, and reading live is what makes them count.

Falling back to the bundled dataset is logged rather than silent. The
difference between live and bundled payables is real money.

## And so are the fade curves

`degradation_profile` holds how fast each pack model wears out — the eight-year
fade, the mileage that figure assumes, the cooling design, and the spread across
real packs of that model. It is what lets the valuation say whether a battery is
ageing normally rather than just how worn it is.

```python
from battery_value.materials.battery_data import degradation_library

curves = degradation_library()
curves.for_pack_model("nissan-leaf-ze1-40").expected_soh(8, rated_kwh=40)  # 0.810
```

Two columns there are load-bearing and easy to drop. `spread_points_at_8y` is
what turns "below average" — true of half of everything — into a statement worth
making. `reference_km_per_year` records how much cycling `fade_at_8y` already
contains, without which a consumer adds a full cycle term on top and bills the
same kilometres twice. See [aging.md](aging.md).

## Round trip

The path is closed, and the equality is a test rather than a claim:

```
battery-value JSON  →  export  →  battery-data Postgres  →  bv sync  →  battery-value JSON
```

**All twenty pack models value identically from either source, across all four
pathways, and all thirty-four fade curves return the same health at every age.** Getting there caught four things the export was quietly dropping —
the human label, every component except modules, the demand tier, and aliases —
each of which moved a number. Two more surfaced on the read side: battery-data
stores the legal entity (`Nissan Motor`) where a passport names the brand
(`Nissan`), and a pack now assembles four kinds of child, so the module join
needed constraining or `module_count` came from whichever child the planner
picked first.

None of those would have been visible without comparing the two sources
directly, which is why `tests/test_battery_data.py` does it for every model
rather than for one. It also checks the recovery terms match. Both run when
`BV_BATTERY_DATA_DSN` points at a live database and skip cleanly otherwise.
