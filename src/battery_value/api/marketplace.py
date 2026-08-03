"""HTTP routes for the market.

Kept in its own module and mounted on the same app, because the market is a
different product from the valuation service even though it is worthless
without it. A deployment that only wants scanning does not have to serve this.
"""

from __future__ import annotations

import logging
from importlib import resources
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from ..marketplace.models import ListingKind, ListingStatus
from ..marketplace.observations import summarise, to_battery_data_sql
from ..marketplace.pricing import BUYER_MARGIN, guide_price
from ..marketplace.service import MarketError, MarketService
from ..report import build_html_report
from ..store import default_store, normalise_reference
from .schemas import (
    ListingRequest,
    OfferRequest,
    RepriceRequest,
    SaleRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/market", tags=["market"])
pages = APIRouter(include_in_schema=False)


def _service() -> MarketService:
    """The market service. Indirection so tests can point it elsewhere."""
    return MarketService()


def _fail(exc: MarketError) -> HTTPException:
    """A market rule broken by the caller is a 409, not a 500."""
    return HTTPException(status_code=409, detail=str(exc))


# -- browsing ---------------------------------------------------------------


@router.get("/listings")
def list_listings(
    status: str = Query("active", description="active, reserved, sold or withdrawn"),
    kind: str | None = Query(None, description="sale or disposal"),
    chemistry: str | None = None,
    pack_model: str | None = None,
    region: str | None = None,
    min_kwh: float | None = None,
    min_soh: float | None = Query(
        None, description="Minimum state of health as a fraction, e.g. 0.75"
    ),
    max_price: float | None = None,
    q: str | None = Query(None, description="Free text over label, title and notes"),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """Search the market."""
    try:
        wanted_status = ListingStatus(status) if status not in {"", "any"} else None
        wanted_kind = ListingKind(kind) if kind else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    listings = _service().search(
        status=wanted_status,
        kind=wanted_kind,
        chemistry=chemistry,
        pack_model_key=pack_model,
        region=region,
        minimum_kwh=min_kwh,
        minimum_soh=min_soh,
        maximum_price=max_price,
        query=q,
        limit=limit,
    )
    return {
        "count": len(listings),
        "listings": [listing.to_dict() for listing in listings],
    }


@router.get("/listings/{reference}")
def get_listing(reference: str, include_valuation: bool = True) -> dict[str, Any]:
    """One listing, with the valuation behind it.

    The valuation is the point: a buyer can read the same assessment the seller
    was given, workings and all, rather than taking a number on trust.
    """
    service = _service()
    listing = service.get(reference)
    if listing is None:
        raise HTTPException(status_code=404, detail=f"no listing {reference}")

    payload = listing.to_dict()
    payload["offers"]["all"] = [
        {
            "reference": offer.reference,
            "buyer_handle": offer.buyer_handle,
            "amount": round(offer.amount, 2),
            "currency": offer.currency,
            "status": offer.status.value,
            "message": offer.message,
            "created_at": offer.created_at.isoformat(),
        }
        for offer in listing.offers
    ]
    if include_valuation:
        payload["valuation"] = service.valuation_payload(listing)
    return payload


@pages.get("/market/report/{reference}", response_class=HTMLResponse)
def listing_report(reference: str) -> HTMLResponse:
    """The full valuation report for a listing, as a shareable page."""
    service = _service()
    listing = service.get(reference)
    if listing is None:
        raise HTTPException(status_code=404, detail=f"no listing {reference}")
    payload = service.valuation_payload(listing)
    if payload is None:
        raise HTTPException(
            status_code=404, detail="the valuation behind this listing is gone"
        )
    return HTMLResponse(build_html_report(payload))


# -- selling ----------------------------------------------------------------


@router.post("/listings", status_code=201)
def create_listing(request: ListingRequest) -> dict[str, Any]:
    """List a pack, from a valuation that already exists.

    A seller who has not scanned their pack has nothing to list. That is the
    rule the whole market rests on.
    """
    try:
        listing = _service().create_listing(
            request.valuation_reference,
            seller_handle=request.seller_handle,
            asking_price=request.asking_price,
            region=request.region,
            title=request.title,
            description=request.description,
            collection_only=request.collection_only,
        )
    except MarketError as exc:
        raise _fail(exc) from exc
    return listing.to_dict()


@router.post("/listings/{reference}/price")
def reprice(reference: str, request: RepriceRequest) -> dict[str, Any]:
    """Change the asking price. The valuation behind it does not move."""
    try:
        return _service().reprice(reference, request.asking_price).to_dict()
    except MarketError as exc:
        raise _fail(exc) from exc


@router.post("/listings/{reference}/withdraw")
def withdraw(reference: str) -> dict[str, Any]:
    """Take a listing off the market."""
    try:
        return _service().withdraw(reference).to_dict()
    except MarketError as exc:
        raise _fail(exc) from exc


@router.post("/listings/{reference}/sold")
def mark_sold(reference: str, request: SaleRequest) -> dict[str, Any]:
    """Record that the pack changed hands, and for how much."""
    try:
        return _service().mark_sold(reference, request.price).to_dict()
    except MarketError as exc:
        raise _fail(exc) from exc


# -- buying -----------------------------------------------------------------


@router.post("/listings/{reference}/offers", status_code=201)
def make_offer(reference: str, request: OfferRequest) -> dict[str, Any]:
    """Bid on a listing."""
    try:
        offer = _service().make_offer(
            reference,
            buyer_handle=request.buyer_handle,
            amount=request.amount,
            message=request.message,
        )
    except MarketError as exc:
        raise _fail(exc) from exc
    return {
        "reference": offer.reference,
        "listing_reference": offer.listing_reference,
        "amount": round(offer.amount, 2),
        "currency": offer.currency,
        "status": offer.status.value,
        "created_at": offer.created_at.isoformat(),
    }


@router.post("/offers/{reference}/accept")
def accept_offer(reference: str) -> dict[str, Any]:
    """Accept an offer, reserving the pack and declining the rest."""
    try:
        return _service().accept_offer(reference).to_dict()
    except MarketError as exc:
        raise _fail(exc) from exc


@router.post("/offers/{reference}/decline")
def decline_offer(reference: str) -> dict[str, Any]:
    """Turn an offer down."""
    try:
        offer = _service().decline_offer(reference)
    except MarketError as exc:
        raise _fail(exc) from exc
    return {"reference": offer.reference, "status": offer.status.value}


# -- what the market knows --------------------------------------------------


@router.get("/guide/{valuation_reference}")
def price_guide(valuation_reference: str) -> dict[str, Any]:
    """What a pack should fetch, before anyone lists it.

    Useful on its own: a seller can ask what their battery is worth to a buyer
    without committing to selling it.
    """
    record = default_store().get(valuation_reference)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"no valuation {normalise_reference(valuation_reference)}",
        )
    guide = guide_price(record.residual_value, record.currency)
    return {
        "valuation_reference": record.reference,
        "battery": record.battery_label,
        "estimate": round(record.residual_value, 2),
        "guide": round(guide.guide, 2),
        "low": round(guide.low, 2),
        "high": round(guide.high, 2),
        "currency": guide.currency,
        "is_disposal": guide.is_disposal,
        "buyer_margin": BUYER_MARGIN,
        "note": guide.explain(guide.guide),
    }


@router.get("/prices")
def observed_prices() -> dict[str, Any]:
    """What packs have actually sold for, by model.

    The market's own price history, and the thing that eventually replaces the
    estimated used-part values the valuation currently leans on.
    """
    sold = _service().market.sold()
    return {"models": summarise(sold), "sales": len(sold)}


@router.get("/prices/battery-data.sql")
def observed_prices_sql() -> dict[str, Any]:
    """Observed sale prices rendered as battery-data rows, ready for review."""
    return {"sql": to_battery_data_sql(_service().market.sold())}


@pages.get("/market", response_class=HTMLResponse)
def market_page() -> HTMLResponse:
    """The market's own web UI."""
    html = (
        resources.files("battery_value.api.static")
        .joinpath("market.html")
        .read_text(encoding="utf-8")
    )
    return HTMLResponse(html)


__all__ = ["pages", "router"]
