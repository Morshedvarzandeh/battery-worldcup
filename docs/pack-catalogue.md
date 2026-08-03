# The pack model catalogue

Knowing the pack is a Nissan Leaf ZE1 is worth far more than knowing it is
"40 kWh of NMC". The catalogue turns a model identification into:

- **Missing passport fields** — chemistry, energy, mass, module and cell counts.
- **A component breakdown** — what is physically in the pack, which parts have a
  used market, and what they are worth.
- **A model-specific replacement price** — the anchor for the reuse pathway,
  instead of a generic figure.

Without a match a pack still values, but it loses the parts-out route entirely
and falls back to a generic OEM replacement price. Matching is usually the single
biggest accuracy improvement available.

## Matching

Scored across several signals, so no single field has to be perfect:

| Signal | Weight |
|---|---|
| Catalogue key or alias appears in the passport | +0.65 |
| Named vehicle model matches | +0.55 |
| Two or more shared tokens | +0.35 |
| One distinctive shared token (`i3`, `Zoe`) | +0.20 |
| Manufacturer matches | +0.20 |
| Nameplate energy within 6% | +0.25 (+0.15 without maker match) |
| Chemistry matches | +0.10 |
| Pack mass within 10% | +0.10 |
| Energy differs by more than 25% | **−0.45** |

A match needs 0.55 to be used at all and 0.75 to be "confident" — only confident
matches enrich a passport. The negative term matters: a 200 kWh pack labelled
"Nissan Leaf" is not a Leaf, whatever the label says.

```bash
bv packs                      # list everything
bv packs --search bmw         # filter
curl localhost:8000/v1/packs/nissan-leaf-ze1-40
```

## Components

Every model has a component breakdown. Models with published teardown data carry
explicit lists; the rest are synthesised from an archetype mass split that always
balances exactly to pack mass:

| Group | Share of mass | Reusable | Typical used value |
|---|---|---|---|
| Modules | 65% | yes | model-specific, health-adjusted |
| Enclosure and crash structure | 18% | no | aluminium scrap |
| Cooling plates and manifolds | 6% | yes | €70 |
| HV busbars and harness | 4% | no | copper scrap |
| HV junction box, contactors, fuses | 4% | yes | €190 |
| BMS and slave boards | 3% | yes | €260 |

Only modules are health-adjusted — a contactor box does not care how tired the
cells are.

## Enrichment never overwrites

The passport is the authority. Catalogue values fill only fields the passport
left empty, and every substitution is recorded:

```python
from battery_value.packs import build_pack_resolver, enrich_passport

match = build_pack_resolver().find(passport)
result = enrich_passport(passport, match)

for line in result.provenance_lines():
    print(line)
# matched pack model: Nissan Leaf ZE1 40 kWh (score 1.00 on alias ..., vehicle 'Leaf ZE1')
# technical.chemistry = NMC532 (from catalogue:nissan-leaf-ze1-40)
# technical.pack_mass_kg = 303.0 (from catalogue:nissan-leaf-ze1-40)
```

A passport declaring 35 kWh keeps 35 kWh even when the catalogue says 40.

## Adding your own models

Three layers, consulted before the bundled catalogue.

### A local directory

Point at a directory of JSON files. Each holds one model object or a list.

```bash
export BV_PACK_CATALOGUE_DIR=/etc/battery-value/packs
```

```json
[
  {
    "key": "acme-hauler-500",
    "label": "Acme Hauler 500 kWh",
    "manufacturer": "Acme",
    "chemistry": "LFP",
    "rated_kwh": 500.0,
    "pack_mass_kg": 3400.0,
    "module_count": 40,
    "cell_count": 640,
    "nominal_voltage_v": 800.0,
    "vehicle_models": ["Acme Hauler 500", "Hauler eTruck"],
    "aliases": ["hauler500"],
    "used_module_value_eur": 300.0,
    "oem_replacement_price_eur_per_kwh": 210.0,
    "second_life_demand": "high",
    "confidence": "high"
  }
]
```

Required: `key`, `label`, `manufacturer`, `chemistry`, `rated_kwh`,
`pack_mass_kg`. Everything else is optional; components are synthesised when
absent. Entries missing a required field are skipped with a warning rather than
crashing the scan.

To give explicit components, add a `components` array:

```json
"components": [
  {"key": "modules", "label": "Battery modules", "count": 40,
   "unit_mass_kg": 55.0, "reusable": true, "unit_value_eur": 300.0,
   "dismantling_minutes_each": 14.0},
  {"key": "enclosure", "label": "Pack enclosure", "count": 1,
   "unit_mass_kg": 620.0, "reusable": false, "dominant_material": "Al",
   "dismantling_minutes_each": 40.0}
]
```

### A remote service

```bash
export BV_PACK_API_URL=https://fleet.internal/api/packs
```

Called with `manufacturer`, `model`, `vehicle_model`, `gtin` and `rated_kwh`
query parameters; should return a model object or `{"models": [...]}`.

### A custom layer

```python
from battery_value.packs import PackDataProvider, PackMatch, build_pack_resolver

class FleetDatabaseProvider(PackDataProvider):
    key = "fleet_db"
    label = "Internal fleet database"

    def find(self, passport) -> PackMatch | None:
        ...

resolver = build_pack_resolver(extra_providers=[FleetDatabaseProvider()])
```

A layer that raises is logged and skipped, never allowed to break a scan.

## Keeping values current

`used_module_value_eur` and `oem_replacement_price_eur_per_kwh` move with a thin
second-hand market. The bundled figures are a starting point, not a price feed.
If you operate at scale, scrape your own marketplace listings into a local
directory layer and refresh on a schedule — the same pattern as the price CSV.
