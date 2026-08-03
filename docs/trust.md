# Certificates, and who said what

A buyer looking at a seller's battery report has no way to tell whether it
describes the pack in front of them, whether the health figure was edited on the
way, or whether the document was invented. So they assume the worst and discount
accordingly, and the owners of good packs stop selling. See
[economics.md](economics.md) for why that is a transaction-cost problem rather
than a market failure.

A certificate is the cheapest available fix.

```bash
bv certify BV-7K2P-M4X9 --output certificate.json
bv verify certificate.json
```

```
BV-7K2P-M4X9: intact, issued by battery-value demo issuer
  The health reading is a measurement, dated and attributable, though most
  other figures here are the manufacturer's own.
```

## What it proves, and what it does not

A signature proves the record is **tamper-evident and attributable**: this
document came from this issuer, unchanged. That is a smaller claim than it
sounds, and it is the one that matters — it moves the buyer's question from *is
this person lying* to *do I trust the issuer*, and there are far fewer issuers
than sellers.

It does not make the manufacturer honest. Every certificate carries that in its
own text rather than in a footnote somebody has to find.

## Four bases, kept apart

This is the whole design. Every claim says what it rests on:

| Basis | Means | Weight |
|---|---|---|
| **verified** | The source document carried a signature that was checked | 1.00 |
| **measured** | Recorded by an instrument, and dated | 0.90 |
| **computed** | Derived here, by a published method, from the claims above | 0.75 |
| **declared** | Stated by the manufacturer or holder. Nobody checked | 0.50 |
| **absent** | Not supplied. Recorded, because silence is worth knowing about | 0.00 |

```
WHO SAID WHAT
  Battery identifier                      AESC-LEAF40-0093122   Declared, unverified
  State of health                         81.0                  Measured
  Full cycles                             850                   Declared, unverified
  Wear against others of the same model   Ageing normally       Worked out from the above
  Carbon footprint                        62.4                  Declared, unverified
  Supply chain due diligence              RMI RMAP              Declared, unverified
  What it is worth                        2058.49               Worked out from the above
```

A document that blurs these is worth nothing, because a buyer cannot tell which
parts are load-bearing and has to price the whole thing at the level of its
weakest line. Keeping them apart is what makes the certificate worth issuing.

**The claims are weighted by how much they move the price**, not counted flat. A
certificate thick with compliance paperwork and no health reading is not well
evidenced, whatever the field count says — and a bare passport with a dated
capacity measurement is. The headline sentence leads with state of health for
the same reason: it is the claim the money rests on.

## Verification is free, and offline

```bash
curl localhost:8000/v1/trust/public-key
```

The public key travels inside every certificate, so checking one needs no
account, no API key and no call to this service. A buyer standing in a warehouse
with no signal can verify a file on their phone. That is deliberate: a check with
a cost attached is a check people skip, and a skipped check puts the whole
discount straight back.

`/verify` is the public page. A QR sticker can link directly to
`/verify#BV-7K2P-M4X9`.

| Route | Purpose |
|---|---|
| `GET /v1/certificates/{ref}` | Issue a signed certificate for a stored valuation |
| `POST /v1/certificates/verify` | Check one. Public, no auth |
| `GET /v1/trust/public-key` | The issuing key |
| `GET /verify` | The page a buyer uses |

## Keys

```bash
export BV_SIGNING_KEY='…'          # the private seed, base64url
export BV_SIGNING_KEY_PATH=/etc/battery-value/signing.key
export BV_ISSUER='Acme Battery Assessment BV'
```

Ed25519. Small keys, small signatures, no parameters to get wrong.

Without configuration a key is generated on first use and written under the data
directory, and it says so loudly in the logs — those certificates verify against
that deployment and nowhere else, which is fine for a pilot and useless for
anything federated. A configured key is **never** silently replaced by a
generated one.

## Compliance readiness

The same certificate carries what Regulation (EU) 2023/1542 will ask for, when
each item falls due, and **whose job it is**:

```
EU 2023/1542: 3 of 10 requirements are not declared and already due.
              All of them are the manufacturer's to supply.
  missing: Carbon footprint declaration (Article 7, manufacturer's to supply)
  missing: Supply chain due diligence policy (Articles 48-53, manufacturer's)
  missing: Country of origin of critical materials (Annex XII / Article 49)
```

Three design calls worth stating:

**Deadlines are respected.** An Article 8 recycled-content obligation that bites
in 2031 is reported as upcoming in 2026, not as a failure. Tools that cry wolf
about future obligations get ignored, and then the real gaps get ignored too.

**Ownership is stated.** Most of these are the manufacturer's and cannot be fixed
by whoever holds the pack today. Telling a garage they are non-compliant for a
missing due-diligence policy is both wrong and useless.

**Silence is not a pass.** `recycled_content_gap()` returns nothing for an
element whose recycled share was never declared — a missing declaration is not a
shortfall of zero, and reporting it as one would turn silence into compliance.

## Supply chain

`BatterySupplyChain` holds what the passport says about origin: carbon footprint
per kWh and the study behind it, the due-diligence policy and scheme, whether it
was third-party audited, where each critical material was extracted, and the
manufacturing site.

All of it is **relayed, never asserted**. A footprint figure is a claim by
whoever wrote the passport, and treating it as more than that would be the same
mistake as treating a seller's word about state of health as a measurement.

Residual value and supply chain are the same ledger read from two ends: proving
recycled content means proving where material came from, which means knowing
what happened to the pack it came out of — the same chain of custody that makes
a residual value credible.

## What is not here

- **No revocation.** A certificate cannot yet be withdrawn once issued.
- **No issuer registry.** Nothing says which public keys are trustworthy; that
  is a governance question, not a code one.
- **No chain of custody.** The `custody` field exists and is signed, but nothing
  writes to it yet. Transfers, dismantling and recycling events belong there,
  and that is what would make recycled content provable end to end.
