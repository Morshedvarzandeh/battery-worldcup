"""Certificates, verification and the portfolio view, over HTTP.

The verification endpoint is deliberately public and deliberately dull: it takes
a certificate, checks the signature and says yes or no. No account, no key, no
rate-limited API contract. A check that costs the buyer something is a check
they will skip, and a skipped check is the whole problem again.
"""

from __future__ import annotations

import logging
from importlib import resources
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import HTMLResponse

from .. import portfolio as portfolio_module
from ..passport.resolver import PassportResolver
from ..store import default_store, normalise_reference
from ..trust import certificate as certificate_module
from ..trust.signing import SigningUnavailable, default_signer, signing_available

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["trust"])
pages = APIRouter(include_in_schema=False)


def _passport_for(payload: dict[str, Any]):
    """Rebuild the passport a stored valuation was produced from.

    The store keeps the valuation, not the source document, so the passport is
    reconstructed from the payload's own record of it. Anything it cannot
    recover shows up in the certificate as an absent claim, which is the honest
    outcome rather than a silent one.
    """
    passport_document = payload.get("passport")
    resolver = PassportResolver()
    if passport_document:
        return resolver.from_document(passport_document)

    battery = payload.get("battery", {})
    return resolver.from_document(
        {
            "batteryId": payload.get("battery_id"),
            "manufacturer": (battery.get("pack_model") or {}).get("manufacturer"),
            "model": battery.get("label"),
            "ratedCapacity": {"value": battery.get("rated_kwh"), "unit": "kWh"},
            "stateOfHealth": battery.get("state_of_health"),
            "chemistry": payload.get("bill_of_materials", {}).get("chemistry"),
        }
    )


@router.get("/trust/public-key")
def public_key() -> dict[str, Any]:
    """The issuing public key. Everything needed to verify a certificate offline."""
    if not signing_available():
        raise HTTPException(
            status_code=503,
            detail="signing is not available; pip install 'battery-value[trust]'",
        )
    signer = default_signer()
    return {
        "algorithm": "Ed25519",
        "public_key": signer.public_key,
        "issuer": signer.issuer,
        "note": (
            "Verification needs this key and nothing else. A certificate can be "
            "checked offline, from a phone, without asking this service anything."
        ),
    }


@router.get("/certificates/{reference}")
def get_certificate(reference: str) -> dict[str, Any]:
    """Issue a signed certificate for a stored valuation."""
    record = default_store().get(reference)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"no valuation {normalise_reference(reference)}",
        )
    try:
        certificate = certificate_module.issue(record, _passport_for(record.payload))
    except SigningUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    document = certificate.to_dict()
    document["evidence_strength"] = certificate.evidence_strength
    document["evidence_note"] = certificate.strength_in_words()
    return document


@router.post("/certificates/verify")
def verify_certificate(document: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Check a certificate someone has been handed.

    Answers one question and refuses to imply a second: the record is intact and
    came from the named issuer, or it is not. Whether that issuer is worth
    trusting is the reader's call, and this endpoint does not pretend to make it.
    """
    try:
        certificate = certificate_module.from_dict(document)
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422, detail=f"not a certificate: {exc}"
        ) from exc

    valid = certificate.verify()
    return {
        "valid": valid,
        "reference": certificate.reference,
        "issued_at": certificate.issued_at.isoformat(),
        "issuer": certificate.signature.issuer if certificate.signature else None,
        "public_key": (
            certificate.signature.public_key if certificate.signature else None
        ),
        "subject": certificate.subject,
        "evidence_strength": certificate.evidence_strength,
        "evidence_note": certificate.strength_in_words(),
        "verdict": (
            "This record is intact and was issued by the key shown. It does not "
            "verify what the manufacturer declared."
            if valid
            else "This record does not match its signature. It has been altered "
            "since it was issued, or it was never signed by the key it names."
        ),
    }


@router.get("/portfolio")
def get_portfolio(
    currency: str = Query("EUR", min_length=3, max_length=3),
    limit: int = Query(500, ge=1, le=2000),
) -> dict[str, Any]:
    """What everything on record is worth, and what waiting costs.

    The three numbers a holder of a thousand packs actually acts on: the value,
    the monthly decay, and which ones are about to fall out of the resale market.
    """
    records = default_store().recent(limit=limit)
    return portfolio_module.to_dict(
        portfolio_module.build(records, currency=currency.upper())
    )


@pages.get("/verify", response_class=HTMLResponse)
def verify_page() -> HTMLResponse:
    """The public certificate checker."""
    html = (
        resources.files("battery_value.api.static")
        .joinpath("verify.html")
        .read_text(encoding="utf-8")
    )
    return HTMLResponse(html)


__all__ = ["pages", "router"]
