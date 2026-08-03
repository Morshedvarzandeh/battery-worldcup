# Why nothing trades, and what to do about it

Around 3 million EV packs will come off European roads in the next decade. Most
will be shredded, and most of them will still be worth more than scrap when they
are. Everyone blames regulation, or recyclers, or the OEMs.

None of that is the reason. **The trades are not happening because they cost too
much to arrange.**

## The bee and the farmer

The textbook version of this market says it fails: the value a careful owner
creates by keeping a pack healthy accrues to whoever gets it next, so nobody
bothers, and the state has to step in.

That is the beekeeper and the orchard. Bees pollinate the neighbouring trees and
the beekeeper is not paid for it, so — the argument runs — there will be too few
bees and the state should subsidise them. Meade used it for decades as the
cleanest example of a market failure there is.

Then somebody went and looked. Beekeepers and orchard owners had been writing
pollination contracts for a century, with prices that varied by crop and by
season, and the externality was internalised the whole time. It was never a
market failure. It was a **transaction cost** problem, and where the cost of
contracting was low the contracts existed.

The battery market is the same shape, and its transaction costs are enormous:

| Cost | What it looks like | What removes it |
|---|---|---|
| **Price discovery** | Nobody knows what a specific pack is worth | The valuation — four routes, priced, from the pack's own passport |
| **Verification** | A buyer cannot check the seller's health claim | A signed certificate that says who stated each fact |
| **Search** | Buyer and seller never meet | A market where every listing carries its assessment |
| **Timing** | Nobody knows when to sell, so nobody does | The wear curve, and the date the pack falls out of resale grade |
| **Freight** | Dangerous goods, discovered on collection day | Condition and ADR class on the listing |

Take those costs out and the allocation happens on its own. Good packs go to
vehicles, tired ones to storage, dead ones to recyclers — not because a rule
says so, but because at those prices that is where each one is worth most.

## The lemons problem is the expensive one

Of the five, verification is the one that kills the market rather than merely
taxing it.

A buyer cannot tell a good pack from a tired one before paying and arranging
freight. So every pack is priced as though it might be tired. The owners of good
packs will not sell at that price, and withdraw. What is left is the tired ones,
which confirms the discount, which pushes out the next tier of decent packs.
Akerlof's used-car market, with 300 kg of dangerous goods attached.

The cure is not a mandate, it is **credible disclosure cheap enough that a good
seller bothers**. Which is all a certificate is:

```bash
bv certify BV-7K2P-M4X9 --output certificate.json
bv verify certificate.json
# BV-7K2P-M4X9: intact, issued by …
#   The health reading is a measurement, dated and attributable.
```

Checking one costs the buyer nothing: no account, no API key, no call to us. The
public key travels with the certificate, so it verifies from a phone in a
warehouse with no signal. That matters — a check with a cost attached is a check
people skip, and a skipped check puts the discount straight back.

Note what the certificate does **not** claim. It does not say the battery is
good, and it does not say the manufacturer told the truth. It says: *this record
has not been altered, it came from this issuer, and here is who stated each
individual fact.* Every claim is marked as measured, declared, computed or
absent — because a buyer who cannot tell which is which has to price the whole
document at the level of its weakest line.

That is why a passport thick with compliance paperwork and no health reading
scores badly here, and a bare passport with a dated capacity measurement scores
well. The document is graded on what a buyer can act on, not on how full it is.

## What makes the trade actually happen

Verification lets a trade clear. It does not make anyone move. What moves people
is that **holding costs money**, and until now nobody could say how much.

A battery is a wasting asset with a knowable half-life. The fade curve is in this
package; the value follows it. So:

```bash
bv portfolio
```

```
  batteries        12
  energy           539 kWh
  value            €24,534  (45 EUR/kWh)
  losing           €36 a month  (1.8% a year)
  at the cliff     2 pack(s) holding €2,229 drop below resale grade within 2 years
  concentration    8 pack(s) hold 80% of the value

  MOVE THESE FIRST
    BV-UMUA-ERLS  BMW i3 60Ah (22 kWh)      1,060  0.8 yr to the floor
    BV-XWEP-KCLC  Renault Zoe ZE40 41 kWh   1,169  1.1 yr to the floor
```

Two numbers do the work.

**The monthly loss** turns waiting from free into expensive. A warehouse of packs
stops being storage and becomes a position.

**The cliff** is the one that gets a decision made. Value does not slide smoothly
to zero: when a pack drops below the health a buyer will fit to a vehicle, the
resale route disappears outright and the price steps down. A pack ten months
from that line is worth moving now; one six years clear of it is not urgent.
Nobody could tell those apart before, so they were treated the same and both sat
in the warehouse.

The decay is computed from each pack's own valuation rather than by revaluing
anything: the engine already re-prices every battery under a health shock, so the
local slope is sitting in the stored record. A thousand-pack portfolio is one
database read.

## The warranty is a put option, and it expires

This is where the money is, and it is the part nobody prices.

Under an 8-year/70% warranty the holder's downside is capped: if the pack falls
through the floor the maker replaces it. That is a put option. It is worth real
money, its value depends entirely on how close *this* pack is to the floor, and
it disappears on a known date.

```bash
bv forecast BV-7K2P-M4X9 --years 6
```

```
Worth €2,633 today and about €1,821 in 6 years (€1,617 to €2,024).
Warranty runs to February 2029, and on this pack's trajectory it is worth
about €106 (1% chance of a claim).

  date          health      value       low      high   warranty
  2026-08-03    89.0%      2,633     2,423     2,633   covered
  2028-08-02    86.6%      1,981     1,794     2,292   covered
  2029-08-02    85.5%      1,883     1,749     2,019   exposed
  2032-08-02    82.3%      1,821     1,617     2,024   exposed

warranty left    €106  (1% chance of a claim before it expires)
cost of doubt    €546 at the horizon, which is what evidence is worth
```

On a healthy pack the guarantee is worth almost nothing. On a marginal one of
the same age it can be worth **more than the battery** — a Leaf at 76% with a
year of cover left carries a warranty worth €3,400 against a pack worth €1,900.

Today those two are priced identically. That is the mistake, and it runs in both
directions: a lessor is over-reserving against the good pack and under-reserving
against the bad one, and neither position is visible.

The claim probability comes from how far real packs of that model scatter at
that age — a number the wear curve already carries. Nothing here needs a new
assumption.

## Why residuals are set at zero, and what that costs

A leasing company does not need today's price. They commit to a **forward**
number at contract signing and find out three years later whether they were
right. Because nobody could defend one, the industry sets battery residuals at
or near zero.

That is not caution, it is a transfer. A residual set to zero is priced into the
monthly payment, so the lessee pays for a battery the lessor then hands to
whoever buys the car at auction. Everyone in the chain except the auction buyer
is worse off.

The forecast **re-values** the pack at each horizon rather than extrapolating.
Over a few years most packs cross the resale floor, where the best route
disappears outright, and a straight line through that cliff reports a number
that cannot happen. It also does something a spot valuation cannot: it separates
**wear** from **doubt**. The uncertainty discount is what a buyer knocks off a
pack nobody has measured — it prices the risk of being wrong, not the battery —
and it is exactly what a certificate removes.

That makes the certificate's value quotable rather than rhetorical: on the pack
above it is €546 at the six-year mark. A lessor writing 4,000 contracts can
multiply.

## Recycling is the floor, not the goal

Recycling matters here as the **reservation price**. It is always available, so
it sets the number every other route has to beat, which is what makes the other
routes get chosen when they are worth more.

For LFP and sodium-ion that floor is negative — disposal costs more than the
materials are worth. Those packs list as *disposal jobs* rather than cheap sales,
because pretending otherwise leaves the holder waiting for an offer that is never
coming. A market that cannot say "the seller pays" is not describing this market.

And the loop closes: today the used-part values in the model are estimates, the
weakest numbers in it. Every completed sale is an observation of what an
identified pack of known health actually fetched, and those flow back into
battery-data. The market prices itself, and gets better at it the more it trades.

## Where the supply chain comes in

Residual value and supply chain are not two products. They are the same ledger
read from opposite ends.

Recycled content is the clearest case. From 2031, new packs must contain minimum
shares of recovered cobalt, lithium and nickel — and proving recovered content
means proving where the material came from, which means knowing what happened to
the pack it came out of. That is the same chain of custody that makes a residual
value credible.

So the same certificate carries both, and the compliance view says which of the
regulated fields are present, which are missing, and **whose job each one is** —
because most are the manufacturer's and cannot be fixed by whoever is holding the
pack today. Telling a garage they are non-compliant for a missing due-diligence
policy is both wrong and useless.

```
EU 2023/1542: 3 of 10 requirements are not declared and already due.
              All of them are the manufacturer's to supply.
  missing: Carbon footprint declaration (Article 7)
  missing: Supply chain due diligence policy (Articles 48-53)
  missing: Country of origin of critical materials (Annex XII / Article 49)
```

The deadlines are the useful part. An obligation that starts in 2031 reported as
overdue in 2026 is noise, and noise is what makes people ignore compliance tools.

## So: one site, three doors

- **What is it worth** — scan, price, share. The seller's door.
- **Check a certificate** (`/verify`) — the buyer's door, and the cheap one.
- **What is the pile worth, and what is waiting costing** (`bv portfolio`) — the
  fleet's door, and the one that converts.

Same record underneath all three. The market at `/market` is where they meet, but
it is downstream of the trust layer, not the other way round: a marketplace with
no verification is a classifieds page, and classifieds pages for used batteries
already exist and do not work.
