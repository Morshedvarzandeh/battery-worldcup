# Reading battery passports

## What a scan actually returns

EU Regulation 2023/1542 requires a QR code on the pack, but does not fix what
the code contains. In practice a scan yields one of:

| Payload | Example | Handling |
|---|---|---|
| HTTPS URL | `https://dpp.example.com/battery/AB123` | Fetched |
| GS1 Digital Link | `https://id.gs1.org/01/09506000134376/21/AB123` | Identifiers parsed, then fetched |
| Inline JSON | `{"batteryPassport": {...}}` | Parsed directly |
| Data URI | `data:application/json;base64,eyJ...` | Decoded, then parsed |
| URN or DID | `urn:uuid:6f1c2e40-...` | Passed to lookup layers |
| Bare identifier | `PACK-000123` | Passed to lookup layers |

```python
from battery_worldcup.passport import parse_carrier

carrier = parse_carrier("https://id.gs1.org/01/09506000134376/21/AB123")
carrier.kind            # CarrierKind.GS1_DIGITAL_LINK
carrier.identifiers     # {'gtin': '09506000134376', 'serial': 'AB123'}
```

GS1 Application Identifiers recognised: `00` SSCC, `01` GTIN, `10` batch,
`21` serial, `253` GDTI, `8003` GRAI, `8004` GIAI, `8006` ITIP, `8010` CPID.

## Fetching is treated as untrusted

A URL that arrived on a sticker is attacker-controllable, so the fetch path is
deliberately defensive:

- `http`/`https` only.
- Hostnames resolving to private, loopback, link-local, reserved or multicast
  addresses are refused. Override with `allow_private_hosts=True` for a trusted
  internal passport host.
- 4 MB response cap and a short timeout.
- HTML responses are searched for embedded JSON-LD rather than being scraped.

## Schema adapters

Four adapters, scored by structure; the best match wins.

| Adapter | Recognised by | Priority |
|---|---|---|
| `native` | This module's own serialised passport | 120 |
| `eu_dpp` | Annex XIII section names | 100 |
| `gba` | Global Battery Alliance sections or `@context` | 90 |
| `generic` | Anything else | −100 |

The generic adapter is the workhorse. Most passports are neither strict EU DPP
nor strict GBA — they are an OEM export with the right data under idiosyncratic
names. It flattens the whole document and matches every leaf against an alias
table, preferring exact key matches over path suffixes over substrings.

```python
from battery_worldcup.passport import PassportResolver

passport = PassportResolver().from_document(anything_json)
passport.rated_kwh
passport.technical.chemistry.key
passport.health.soh_fraction
passport.declared_masses()
```

## The awkward cases, handled

**Capacity units.** `{"ratedCapacity": {"value": 75, "unit": "kWh"}}` and
`{"nominalCapacity": {"value": 120, "unit": "Ah"}}` use nearly the same key name
but mean different things. Capacity leaves are routed by their declared unit
before anything else. Reading a kWh figure as Ah and multiplying by a 400 V pack
would report a 75 kWh pack as 30 kWh.

**State of health scale.** `0.87` and `87` both mean 87%. Reading 0.87 as
"0.87% healthy" would write off a good pack.

**Composition shapes.** Both are read:

```json
{"cobalt": {"value": 6.9, "unit": "kg"}}
[{"substance": "Cobalt", "massKg": 6.9}]
```

The second form puts the substance name in a sibling field rather than the key
path, so records are parsed as records. Percentages convert to kg using declared
pack mass. Explicit masses beat fractions for the same element.

**Dates.** ISO, `DD/MM/YYYY`, `DD.MM.YYYY`, `YYYY/MM/DD`, `YYYYMMDD`, bare
`YYYY-MM` and bare `YYYY` all parse — a year alone still bounds the pack's age.

**Derivation.** Missing SoH is derived from measured capacity ÷ nameplate, and
missing remaining capacity from SoH × nameplate.

## Completeness

Before valuing, check what is actually present:

```python
completeness = passport.completeness()
completeness.is_valuable        # False if energy, chemistry or health missing
completeness.missing_required
completeness.score              # 0-1, weighted by impact on the final number
```

Required for a valuation: nameplate energy (0.30), chemistry (0.25), state of
health (0.20). Optional but valuable: pack mass, manufacturing date, declared
composition, cycle count, manufacturer.

Missing energy or chemistry is often recoverable from the
[pack catalogue](pack-catalogue.md) — a passport carrying only
`"vehicleModel": "Nissan Leaf ZE1 40 kWh"` values fine.

## Adding a lookup layer

For carriers holding only an identifier, plug in a source that can resolve it:

```python
from battery_worldcup.passport import PassportLookup, PassportResolver

class FleetLookup(PassportLookup):
    def lookup(self, carrier):
        record = my_database.get(carrier.primary_identifier)
        return build_passport(record) if record else None

resolver = PassportResolver(lookups=[FleetLookup()])
```

## Decoding QR images

The browser UI decodes client-side with the platform `BarcodeDetector` API and
posts the payload as text, which keeps the common path dependency-free.

Server-side decoding is optional:

```bash
pip install 'battery-worldcup[scan]'
bwc value --image pack-label.jpg
```

Without the extra, `/v1/value/image` returns 501 with instructions, and the rest
of the API is unaffected.
