"""What a seller can actually ask, and how to judge a price against it.

The valuation answers "what is this pack worth end to end" -- gross recovery
minus freight, labour, refurbishment and warranty reserve. That is the right
number for the person who ends up doing the work, and it is *not* what a seller
can charge, because the buyer is the one who does that work.

So the guide price is the valuation minus the buyer's margin, and everything
else the buyer bears is already itemised in the valuation rather than assumed
here. One honest assumption instead of a made-up band.

The other case this module exists for: a pack whose valuation is negative. LFP
and sodium-ion packs, and anything fire-damaged, cost more to handle safely
than the materials are worth. In a real market those do not sell -- the holder
pays a licensed recycler to take them. Pretending otherwise would leave the
seller waiting for an offer that is never coming.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

#: What a trade buyer keeps for taking on the work, the risk and the stock.
#: Everything else they spend -- collection, dangerous-goods freight, HV
#: dismantling labour, refurbishment, warranty reserve -- is already deducted
#: inside the valuation, so this is margin alone.
BUYER_MARGIN = 0.25

#: How far either side of the guide a price is still ordinary. Wider than it
#: looks: a thin market with few comparable sales moves on who happens to need
#: a pack that week.
FAIR_BAND = 0.15

#: Below this, a listing is a disposal job rather than a sale.
DISPOSAL_THRESHOLD_EUR = 0.0


class PriceVerdict(str, Enum):
    """How an asking price sits against the independent estimate."""

    BARGAIN = "bargain"
    """Well below the guide. Should move quickly."""

    FAIR = "fair"
    """Within the normal band."""

    AMBITIOUS = "ambitious"
    """Above what a trade buyer can pay and still make it work."""

    DISPOSAL = "disposal"
    """The pack costs money to handle. The seller pays, not the buyer."""

    @property
    def label(self) -> str:
        """Short label for a chip."""
        return {
            PriceVerdict.BARGAIN: "Priced to sell",
            PriceVerdict.FAIR: "Fairly priced",
            PriceVerdict.AMBITIOUS: "Above the guide",
            PriceVerdict.DISPOSAL: "Disposal, not a sale",
        }[self]

    @property
    def tone(self) -> str:
        """``good``, ``fair`` or ``weak`` -- for styling only."""
        return {
            PriceVerdict.BARGAIN: "good",
            PriceVerdict.FAIR: "good",
            PriceVerdict.AMBITIOUS: "fair",
            PriceVerdict.DISPOSAL: "weak",
        }[self]


@dataclass(frozen=True, slots=True)
class PriceGuide:
    """What this pack should fetch, derived from its own valuation."""

    guide: float
    low: float
    high: float
    currency: str
    estimate: float
    """The full end-to-end valuation the guide is derived from."""

    is_disposal: bool = False

    def verdict(self, asking: float) -> PriceVerdict:
        """Where an asking price falls against the band."""
        if self.is_disposal:
            return PriceVerdict.DISPOSAL
        if asking < self.low:
            return PriceVerdict.BARGAIN
        if asking > self.high:
            return PriceVerdict.AMBITIOUS
        return PriceVerdict.FAIR

    def premium(self, asking: float) -> float | None:
        """How far above or below the guide, as a fraction. ``None`` if undefined."""
        if self.guide <= 0:
            return None
        return (asking - self.guide) / self.guide

    def explain(self, asking: float) -> str:
        """One sentence a seller or buyer can act on."""
        money = f"{self.currency} "
        if self.is_disposal:
            return (
                f"This pack costs about {money}{abs(self.estimate):,.0f} to handle "
                "safely, more than the materials in it are worth. Nobody will pay "
                "for it; the realistic outcome is paying a licensed recycler to "
                "take it away."
            )

        premium = self.premium(asking)
        band = f"{money}{self.low:,.0f}-{money}{self.high:,.0f}"

        if premium is None:
            return f"The guide range for this pack is {band}."
        if premium > FAIR_BAND:
            return (
                f"Asking {premium:+.0%} against a guide of {money}{self.guide:,.0f}. "
                f"A trade buyer works to {band}, because they still have to collect "
                "it, test it and carry the risk."
            )
        if premium < -FAIR_BAND:
            return (
                f"Asking {premium:+.0%} against a guide of {money}{self.guide:,.0f}. "
                "Priced to move."
            )
        return (
            f"Within the guide range of {band}, derived from this battery's own "
            "valuation."
        )


def guide_price(
    estimate: float,
    currency: str = "EUR",
    *,
    buyer_margin: float = BUYER_MARGIN,
    band: float = FAIR_BAND,
) -> PriceGuide:
    """Turn an end-to-end valuation into what a seller can realistically ask.

    Args:
        estimate: Net residual value from the valuation, in ``currency``.
        currency: Reporting currency.
        buyer_margin: Share the buyer keeps for the work and the risk.
        band: Half-width of the fair range, as a fraction of the guide.

    Returns:
        A guide price with its band, or a disposal guide when the pack is worth
        less than nothing.
    """
    if estimate <= DISPOSAL_THRESHOLD_EUR:
        return PriceGuide(
            guide=0.0,
            low=0.0,
            high=0.0,
            currency=currency,
            estimate=estimate,
            is_disposal=True,
        )

    guide = estimate * (1.0 - buyer_margin)
    return PriceGuide(
        guide=round(guide, 2),
        low=round(guide * (1.0 - band), 2),
        high=round(guide * (1.0 + band), 2),
        currency=currency,
        estimate=estimate,
    )


__all__ = [
    "BUYER_MARGIN",
    "FAIR_BAND",
    "PriceGuide",
    "PriceVerdict",
    "guide_price",
]
