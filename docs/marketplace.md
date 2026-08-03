# The market

The second-hand traction battery market is thin, and the reason is trust rather
than demand.

A buyer cannot verify what a seller claims about state of health. A pack that
turns out to be tired is a total loss on a 300 kg item nobody wants to ship
back, and it is dangerous goods in both directions. So buyers discount
everything heavily to cover the risk, good packs cannot fetch what they are
worth, and their owners scrap them instead — which is exactly the outcome the
EU battery regulation exists to prevent.

Everything else in this repository was built to produce one thing: a defensible,
provenance-backed number for a *specific* battery. That is the missing
ingredient.

## The rule

**A listing can only be created from a valuation reference.**

```bash
bv value --file passport.json          # → BV-7K2P-M4X9
bv market sell BV-7K2P-M4X9 --seller nl-ev-garage --region "NL, Rotterdam"
```

There is no field for state of health, energy or chemistry on a listing, and
that is deliberate: they come from the scan, not from the seller. The buyer
opens the same report the seller was given — health, wear against others of the
same model, the bill of materials, the prices used and where each came from.

A seller who has not scanned their pack has nothing to list. That is the whole
trust primitive, and two useful things fall out of it.

## 1. "Is this a fair price" stops being an opinion

The valuation answers *what is this pack worth end to end* — gross recovery
minus freight, labour, refurbishment and warranty reserve. That is the right
number for whoever does the work, and it is **not** what a seller can charge,
because the buyer is the one who does that work.

So the guide is the valuation minus the buyer's margin:

```
guide = valuation × (1 − BUYER_MARGIN)        # 25%
fair  = guide ± 15%
```

Everything else the buyer bears is already itemised inside the valuation, so
that is one honest assumption rather than a band pulled from nowhere. A listing
shows where its asking price falls against that band, and says why the gap
exists:

> Asking +38% against a guide of €1,543. A trade buyer works to €1,312–€1,775,
> because they still have to collect it, test it and carry the risk.

## 2. Sales become observations

The used-part values in the datasets are the weakest numbers in the whole model
— benchmarked estimates carrying `evidence='estimated'` and a confidence around
0.6 — and they move the parts-out and reuse pathways more than anything else.

A completed sale is not an estimate. It is an observation of what one identified
pack, of known health and known age, actually fetched on a known date.

```
scan ─→ valuation ─→ listing ─→ sale ─→ observation ─→ battery-data
          ▲                                                  │
          └──────────────────────────────────────────────────┘
```

```bash
bv market prices              # median per kWh, by model
bv market prices --sql        # as battery-data rows, ready for review
```

Three sales before a model gets a published figure — one transaction is an
anecdote, it may have been a favour or a fire sale. The median is used rather
than the mean, because a thin market produces outliers and one desperate seller
should not move the published number. The health range each figure covers
travels with it: a price per kWh means nothing without knowing how worn the
packs behind it were.

Nothing writes to battery-data directly. The SQL is reviewed, then loaded — a
price nobody looked at is exactly the kind of claim that repository exists to
keep out.

## A pack worth less than nothing is not a bargain

LFP and sodium-ion packs, and anything fire-damaged, cost more to handle safely
than their materials are worth. In a real market those do not sell: the holder
pays a licensed recycler to take them away.

So a negative valuation produces a **disposal listing** rather than a cheap one.
Marking it as a bargain would be the wrong answer entirely, and a market that
cannot express "the seller pays" leaves those holders waiting for an offer that
is never coming.

## Freight is not an afterthought

Every lithium traction pack is UN3480 Class 9. A damaged one falls under ADR
special provision 376 — a different carrier, different packaging and materially
higher cost. The valuation already knows the pack's condition, so the listing
says so up front rather than leaving it for collection day, which is where deals
fall apart.

## Running it

```bash
python examples/demo_market.py     # scans, lists, bids and sells, end to end
bv serve                           # http://127.0.0.1:8000/market
```

| | |
|---|---|
| `GET /market` | the web UI |
| `GET /v1/market/listings` | search, with filters |
| `GET /v1/market/listings/{ref}` | one listing **and its valuation** |
| `GET /market/report/{ref}` | the full report behind a listing |
| `POST /v1/market/listings` | list a pack, from a valuation reference |
| `POST /v1/market/listings/{ref}/offers` | bid |
| `POST /v1/market/offers/{ref}/accept` | accept, reserving the pack |
| `GET /v1/market/guide/{valuation_ref}` | what it should fetch, without listing it |
| `GET /v1/market/prices` | what packs actually sold for |

Listings live in their own SQLite file (`BV_MARKET_PATH`), separate from the
valuation store on purpose: a deployment that must not retain customer
valuations can still run a market, and a market that closes leaves the
valuation records intact.

## What this is not

- **No payments.** Offers and acceptance are recorded; money is not moved.
- **No identity.** Sellers and buyers are handles. Nothing here verifies that
  the person listing a pack owns it.
- **No escrow, no dispute process, no shipping integration.** All three are
  real requirements for a market that handles money, and none of them are here.

The prototype answers the question that had to be answered first — *can a
listing carry enough evidence that a buyer will pay what a good pack is worth* —
and deliberately stops there.
