"""Listings and offers.

The rule that makes this a market rather than a classifieds page: **a listing
can only be created from a valuation reference.** Whoever posts it has scanned
the pack, and the buyer sees the same independent assessment the seller did --
health, wear against the model, materials, prices and provenance -- rather than
a sentence in a description box.

That is the whole trust primitive. The second-hand traction battery market is
thin because a buyer cannot check what a seller claims about state of health,
and a pack that turns out to be tired is a total loss on a several-hundred-kilo
item nobody wants to ship back. A listing that carries its own audit trail is
worth more than one that does not, and the seller has an incentive to produce
one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .pricing import PriceGuide, PriceVerdict, guide_price


class ListingStatus(str, Enum):
    """Where a listing is in its life."""

    ACTIVE = "active"
    RESERVED = "reserved"
    """An offer has been accepted; the pack is spoken for but not yet gone."""

    SOLD = "sold"
    WITHDRAWN = "withdrawn"

    @property
    def label(self) -> str:
        """Human-readable status."""
        return {
            ListingStatus.ACTIVE: "Available",
            ListingStatus.RESERVED: "Reserved",
            ListingStatus.SOLD: "Sold",
            ListingStatus.WITHDRAWN: "Withdrawn",
        }[self]

    @property
    def is_open(self) -> bool:
        """Whether offers can still be made."""
        return self is ListingStatus.ACTIVE


class OfferStatus(str, Enum):
    """Where an offer stands."""

    OPEN = "open"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    WITHDRAWN = "withdrawn"


class ListingKind(str, Enum):
    """What is actually being transacted."""

    SALE = "sale"
    """The buyer pays the seller."""

    DISPOSAL = "disposal"
    """The pack costs more to handle than it is worth, so the seller pays.

    Not a failure mode -- it is the honest outcome for LFP, sodium-ion and
    anything fire-damaged, and a market that cannot express it leaves those
    holders waiting for an offer that is never coming.
    """


@dataclass(frozen=True, slots=True)
class Offer:
    """One bid on a listing."""

    reference: str
    listing_reference: str
    buyer_handle: str
    amount: float
    currency: str
    created_at: datetime
    status: OfferStatus = OfferStatus.OPEN
    message: str = ""

    @property
    def is_open(self) -> bool:
        """Whether this offer is still live."""
        return self.status is OfferStatus.OPEN


@dataclass(slots=True)
class Listing:
    """A pack offered to the market, with its valuation travelling alongside.

    The ``snapshot`` fields are copied from the valuation at listing time rather
    than looked up on read. A listing is an offer to sell *this* pack as it was
    assessed; re-deriving it later against moved metal prices would silently
    change what the seller advertised.
    """

    reference: str
    valuation_reference: str
    created_at: datetime
    updated_at: datetime
    seller_handle: str
    region: str
    asking_price: float
    currency: str

    # -- snapshot of the valuation, frozen at listing time -----------------
    battery_label: str
    rated_kwh: float
    state_of_health: float
    chemistry: str
    estimate: float
    """Net residual value the valuation gave, in ``currency``."""

    pack_model_key: str | None = None
    health_source: str = "measured"
    valuation_confidence: float = 0.0
    wear_verdict: str = "unknown"
    wear_headline: str = ""
    years_to_resale_floor: float | None = None
    condition: str = "healthy"

    kind: ListingKind = ListingKind.SALE
    status: ListingStatus = ListingStatus.ACTIVE
    title: str = ""
    description: str = ""
    collection_only: bool = True
    """Traction packs are UN3480 Class 9. Most private sales are collection."""

    sold_price: float | None = None
    sold_at: datetime | None = None
    offers: list[Offer] = field(default_factory=list)

    # -- derived -----------------------------------------------------------

    @property
    def guide(self) -> PriceGuide:
        """What this pack should fetch, from its own valuation."""
        return guide_price(self.estimate, self.currency)

    @property
    def price_verdict(self) -> PriceVerdict:
        """How the asking price sits against the guide."""
        return self.guide.verdict(self.asking_price)

    @property
    def price_note(self) -> str:
        """One sentence on the asking price, for buyer and seller alike."""
        return self.guide.explain(self.asking_price)

    @property
    def price_per_kwh(self) -> float:
        """Asking price per kWh of nameplate energy, the market's own unit."""
        return self.asking_price / self.rated_kwh if self.rated_kwh else 0.0

    @property
    def needs_dangerous_goods_freight(self) -> bool:
        """Whether moving it is more than a courier job.

        Every lithium traction pack is UN3480 Class 9. A damaged one falls
        under ADR special provision 376, which is a different carrier, a
        different price and a different set of paperwork -- so it is surfaced
        rather than left for the buyer to discover on collection day.
        """
        return self.condition in {"damaged", "thermal_event", "swollen"}

    @property
    def open_offers(self) -> list[Offer]:
        """Live offers, highest first."""
        return sorted(
            (offer for offer in self.offers if offer.is_open),
            key=lambda offer: offer.amount,
            reverse=True,
        )

    @property
    def best_offer(self) -> Offer | None:
        """The highest live offer, if any."""
        offers = self.open_offers
        return offers[0] if offers else None

    def display_title(self) -> str:
        """The seller's title, or one built from the battery itself."""
        if self.title:
            return self.title
        return f"{self.battery_label} - {self.state_of_health:.0%} health"

    def age_days(self, now: datetime | None = None) -> int:
        """Whole days this listing has been up."""
        moment = now or datetime.now(timezone.utc)
        return max((moment - self.created_at).days, 0)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API and the web UI."""
        guide = self.guide
        return {
            "reference": self.reference,
            "valuation_reference": self.valuation_reference,
            "kind": self.kind.value,
            "status": self.status.value,
            "status_label": self.status.label,
            "title": self.display_title(),
            "description": self.description,
            "seller_handle": self.seller_handle,
            "region": self.region,
            "collection_only": self.collection_only,
            "dangerous_goods": self.needs_dangerous_goods_freight,
            "created_at": self.created_at.isoformat(),
            "age_days": self.age_days(),
            "battery": {
                "label": self.battery_label,
                "rated_kwh": self.rated_kwh,
                "state_of_health": round(self.state_of_health, 4),
                "chemistry": self.chemistry,
                "pack_model_key": self.pack_model_key,
                "condition": self.condition,
                "health_source": self.health_source,
            },
            "wear": {
                "verdict": self.wear_verdict,
                "headline": self.wear_headline,
                "years_to_resale_floor": self.years_to_resale_floor,
            },
            "price": {
                "asking": round(self.asking_price, 2),
                "currency": self.currency,
                "per_kwh": round(self.price_per_kwh, 2),
                "estimate": round(self.estimate, 2),
                "guide": round(guide.guide, 2),
                "guide_low": round(guide.low, 2),
                "guide_high": round(guide.high, 2),
                "verdict": self.price_verdict.value,
                "verdict_label": self.price_verdict.label,
                "tone": self.price_verdict.tone,
                "note": self.price_note,
                "valuation_confidence": round(self.valuation_confidence, 3),
            },
            "offers": {
                "count": len(self.open_offers),
                "best": (
                    round(self.best_offer.amount, 2) if self.best_offer else None
                ),
            },
            "sold": (
                {
                    "price": round(self.sold_price, 2),
                    "at": self.sold_at.isoformat() if self.sold_at else None,
                }
                if self.status is ListingStatus.SOLD
                else None
            ),
        }


__all__ = [
    "Listing",
    "ListingKind",
    "ListingStatus",
    "Offer",
    "OfferStatus",
]
