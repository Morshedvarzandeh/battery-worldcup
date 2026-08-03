"""Creating listings, taking offers, closing sales.

Every rule that makes the market trustworthy lives here rather than in the UI,
so the CLI, the HTTP API and anything built later cannot each interpret them
differently.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timezone

from ..errors import BatteryValueError
from ..store import ValuationStore, default_store, normalise_reference
from .models import Listing, ListingKind, ListingStatus, Offer, OfferStatus
from .pricing import guide_price
from .store import MarketStore, default_market
from .store import generate_reference

logger = logging.getLogger(__name__)

#: How old a valuation may be before a listing built on it is stale. Metal
#: prices move weekly and health readings age; past this the seller is
#: advertising a number that no longer describes their pack.
MAXIMUM_VALUATION_AGE_DAYS = 90


class MarketError(BatteryValueError):
    """A listing or offer could not be created."""


class MarketService:
    """The market's rules, over a valuation store and a market store."""

    def __init__(
        self,
        market: MarketStore | None = None,
        valuations: ValuationStore | None = None,
    ) -> None:
        self.market = market or default_market()
        self.valuations = valuations or default_store()

    # -- selling -----------------------------------------------------------

    def create_listing(
        self,
        valuation_reference: str,
        *,
        seller_handle: str,
        asking_price: float | None = None,
        region: str = "",
        title: str = "",
        description: str = "",
        collection_only: bool = True,
    ) -> Listing:
        """List a pack for sale, from a valuation that already exists.

        This is the only way a listing is created. A seller who has not scanned
        their pack has nothing to list, which is the point: the buyer sees the
        same independent assessment the seller did rather than a claim in a
        description box.

        Args:
            valuation_reference: A reference from a stored valuation.
            seller_handle: How the seller is contacted. A handle, not a name.
            asking_price: What they want. Defaults to the guide price derived
                from the valuation.
            region: Where the pack is, for buyers who have to collect it.
            title: Optional headline. One is built from the battery otherwise.
            description: Free text from the seller.
            collection_only: Traction packs are dangerous goods; most private
                sales are collection.

        Raises:
            MarketError: If the valuation is unknown, too old, or already listed.
        """
        reference = normalise_reference(valuation_reference)
        record = self.valuations.get(reference)
        if record is None:
            raise MarketError(
                f"no valuation found for {reference}. Scan the battery first -- "
                "a listing carries its assessment, so there is nothing to list "
                "without one."
            )

        if record.age_days > MAXIMUM_VALUATION_AGE_DAYS:
            raise MarketError(
                f"valuation {reference} is {record.age_days} days old. Metal "
                "prices and health readings both move; re-scan the pack so the "
                "listing advertises a number that still describes it."
            )

        existing = self.market.by_valuation(reference)
        if existing is not None and existing.status.is_open:
            raise MarketError(
                f"{reference} is already listed as {existing.reference}. "
                "Withdraw that listing before creating another."
            )

        payload = record.payload
        battery = payload.get("battery", {})
        aging = payload.get("aging") or {}
        guide = guide_price(record.residual_value, record.currency)

        price = asking_price if asking_price is not None else guide.guide
        if price < 0:
            raise MarketError("an asking price cannot be negative")

        now = datetime.now(timezone.utc)
        listing = Listing(
            reference=generate_reference("LS"),
            valuation_reference=reference,
            created_at=now,
            updated_at=now,
            seller_handle=seller_handle.strip(),
            region=region.strip(),
            asking_price=round(float(price), 2),
            currency=record.currency,
            battery_label=record.battery_label,
            rated_kwh=float(battery.get("rated_kwh") or 0.0),
            state_of_health=float(battery.get("state_of_health") or 0.0),
            chemistry=str(payload.get("bill_of_materials", {}).get("chemistry", "")),
            estimate=record.residual_value,
            pack_model_key=record.pack_model_key,
            health_source=battery.get("health_source", "measured"),
            valuation_confidence=record.confidence,
            wear_verdict=aging.get("verdict", "unknown"),
            wear_headline=aging.get("headline", ""),
            years_to_resale_floor=aging.get("years_to_resale_floor"),
            condition=self._condition_of(payload),
            # A pack worth less than nothing is a disposal job, not a sale, and
            # a market that cannot say so leaves the holder waiting for an offer
            # that is never coming.
            kind=ListingKind.DISPOSAL if guide.is_disposal else ListingKind.SALE,
            title=title.strip(),
            description=description.strip(),
            collection_only=collection_only,
        )

        saved = self.market.save(listing)
        if saved is None:
            raise MarketError("could not save the listing")
        return saved

    @staticmethod
    def _condition_of(payload: dict) -> str:
        """Pack condition, so dangerous-goods freight can be flagged up front.

        A damaged pack falls under ADR special provision 376: different
        carrier, different packaging, several times the freight cost. Finding
        that out on collection day is how a deal falls apart.
        """
        return str((payload.get("battery") or {}).get("condition") or "healthy")

    def withdraw(self, listing_reference: str) -> Listing:
        """Take a listing off the market."""
        listing = self._require(listing_reference)
        listing.status = ListingStatus.WITHDRAWN
        for offer in listing.offers:
            if offer.is_open:
                self.market.save_offer(
                    replace(offer, status=OfferStatus.DECLINED)
                )
        self.market.save(listing)
        return listing

    def reprice(self, listing_reference: str, asking_price: float) -> Listing:
        """Change what a listing asks. The valuation behind it does not move."""
        if asking_price < 0:
            raise MarketError("an asking price cannot be negative")
        listing = self._require(listing_reference)
        listing.asking_price = round(float(asking_price), 2)
        self.market.save(listing)
        return listing

    # -- buying ------------------------------------------------------------

    def make_offer(
        self,
        listing_reference: str,
        *,
        buyer_handle: str,
        amount: float,
        message: str = "",
    ) -> Offer:
        """Bid on a listing.

        Raises:
            MarketError: If the listing is closed or the amount is not positive.
        """
        listing = self._require(listing_reference)
        if not listing.status.is_open:
            raise MarketError(
                f"{listing.reference} is {listing.status.label.lower()} and is no "
                "longer taking offers."
            )
        if amount <= 0:
            raise MarketError("an offer has to be for more than nothing")

        offer = Offer(
            reference=generate_reference("OF"),
            listing_reference=listing.reference,
            buyer_handle=buyer_handle.strip(),
            amount=round(float(amount), 2),
            currency=listing.currency,
            created_at=datetime.now(timezone.utc),
            message=message.strip(),
        )
        saved = self.market.save_offer(offer)
        if saved is None:
            raise MarketError("could not save the offer")
        return saved

    def accept_offer(self, offer_reference: str) -> Listing:
        """Accept an offer, reserving the pack and declining the rest."""
        offer = self.market.get_offer(offer_reference)
        if offer is None:
            raise MarketError(f"no offer found for {offer_reference}")
        if not offer.is_open:
            raise MarketError(f"{offer.reference} is no longer open")

        listing = self._require(offer.listing_reference)
        if not listing.status.is_open:
            raise MarketError(
                f"{listing.reference} is {listing.status.label.lower()}"
            )

        self.market.save_offer(
            replace(offer, status=OfferStatus.ACCEPTED)
        )
        for other in listing.offers:
            if other.reference != offer.reference and other.is_open:
                self.market.save_offer(
                    replace(other, status=OfferStatus.DECLINED)
                )

        listing.status = ListingStatus.RESERVED
        self.market.save(listing)
        return self._require(listing.reference)

    def decline_offer(self, offer_reference: str) -> Offer:
        """Turn an offer down."""
        offer = self.market.get_offer(offer_reference)
        if offer is None:
            raise MarketError(f"no offer found for {offer_reference}")
        declined = replace(offer, status=OfferStatus.DECLINED)
        self.market.save_offer(declined)
        return declined

    # -- closing -----------------------------------------------------------

    def mark_sold(
        self, listing_reference: str, price: float | None = None
    ) -> Listing:
        """Record that the pack changed hands, and for how much.

        The price matters beyond the two people involved. Used-part values in
        the datasets are estimates; a completed sale is an observation, and
        :mod:`battery_value.marketplace.observations` feeds them back into
        battery-data so the next valuation rests on what packs actually fetched
        rather than on what somebody thought they would.
        """
        listing = self._require(listing_reference)
        if listing.status is ListingStatus.SOLD:
            raise MarketError(f"{listing.reference} is already recorded as sold")

        accepted = next(
            (
                offer
                for offer in listing.offers
                if offer.status is OfferStatus.ACCEPTED
            ),
            None,
        )
        final = price if price is not None else (
            accepted.amount if accepted else listing.asking_price
        )
        if final < 0:
            raise MarketError("a sale price cannot be negative")

        listing.status = ListingStatus.SOLD
        listing.sold_price = round(float(final), 2)
        listing.sold_at = datetime.now(timezone.utc)
        self.market.save(listing)
        return listing

    # -- reading -----------------------------------------------------------

    def get(self, listing_reference: str) -> Listing | None:
        """One listing, or ``None``."""
        return self.market.get(listing_reference)

    def search(self, **filters) -> list[Listing]:
        """Listings matching the given filters."""
        return self.market.search(**filters)

    def valuation_payload(self, listing: Listing) -> dict | None:
        """The full valuation behind a listing, for the buyer to inspect.

        This is the point of the whole design: a buyer can open the same report
        the seller was given, workings and all, rather than taking a number on
        trust.
        """
        record = self.valuations.get(listing.valuation_reference)
        return record.payload if record else None

    def _require(self, listing_reference: str) -> Listing:
        listing = self.market.get(listing_reference)
        if listing is None:
            raise MarketError(f"no listing found for {listing_reference}")
        return listing


__all__ = ["MAXIMUM_VALUATION_AGE_DAYS", "MarketError", "MarketService"]
