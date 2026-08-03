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

## Round trip

The path is closed, and tested:

```
battery-value JSON  →  export  →  battery-data Postgres  →  bv sync  →  battery-value JSON
```

`tests/test_battery_data.py` covers the mapping and the attribution rules
everywhere, and runs the full round trip when `BV_BATTERY_DATA_DSN` points at a
live database.
