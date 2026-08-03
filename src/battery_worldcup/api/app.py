"""HTTP API and the browser scan UI."""

from __future__ import annotations

import logging
from functools import lru_cache
from importlib import resources
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, Response

from .. import __version__
from ..compounds import TRADED_FORMS
from ..errors import BatteryWorldCupError, PassportError, ValuationError
from ..market.providers.baseline import baseline_snapshot_date
from ..market.resolver import build_resolver
from ..materials.chemistry import load_chemistries
from ..packs.catalogue import load_catalogue
from ..packs.providers import build_pack_resolver
from ..passport.models import BatteryPassport
from ..passport.resolver import PassportResolver
from ..serialisation import passport_to_dict, valuation_to_dict
from ..valuation.config import ValuationConfig
from ..valuation.engine import ValuationEngine
from .schemas import HealthResponse, ProvidersResponse, ScanRequest, ValueRequest

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 8 * 1024 * 1024

app = FastAPI(
    title="battery-worldcup",
    version=__version__,
    description=(
        "Scan a battery passport and get the residual value of the pack, priced "
        "against current material and system markets."
    ),
)


@lru_cache(maxsize=8)
def _engine(currency: str, offline: bool) -> ValuationEngine:
    """Cache engines by market options; provider chains are reusable."""
    config = ValuationConfig(currency=currency)
    return ValuationEngine(
        config=config,
        prices=build_resolver(currency=currency, offline=offline),
        packs=build_pack_resolver(),
    )


def _resolve_passport(request: ScanRequest) -> BatteryPassport:
    resolver = PassportResolver(allow_private_hosts=request.allow_private_hosts)
    try:
        if request.document is not None:
            return resolver.from_document(request.document)
        return resolver.from_qr(request.payload or "")
    except PassportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> HTMLResponse:
    """The scan-and-value web UI."""
    html = (
        resources.files("battery_worldcup.api.static")
        .joinpath("index.html")
        .read_text(encoding="utf-8")
    )
    return HTMLResponse(html)


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    """A tiny inline battery icon, so browsers stop logging a 404."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect x="4" y="9" width="21" height="14" rx="3" fill="none" '
        'stroke="#0b6b4f" stroke-width="3"/>'
        '<rect x="26" y="13" width="3" height="6" rx="1" fill="#0b6b4f"/>'
        '<rect x="8" y="13" width="9" height="6" fill="#0b6b4f"/></svg>'
    )
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/v1/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness plus the versions of the bundled datasets."""
    return HealthResponse(
        status="ok",
        version=__version__,
        chemistries=len(load_chemistries().specs),
        pack_models=len(load_catalogue().models),
        baseline_snapshot_date=baseline_snapshot_date().isoformat(),
    )


@app.post("/v1/scan")
def scan(request: ScanRequest) -> dict[str, Any]:
    """Read a passport without valuing it.

    Useful for showing the user what was found before committing to a number.
    """
    passport = _resolve_passport(request)
    return passport_to_dict(passport)


@app.post("/v1/value")
def value(request: ValueRequest) -> dict[str, Any]:
    """Scan a passport and return its residual value by every pathway."""
    passport = _resolve_passport(request)
    currency = request.currency.upper()

    if request.manual_prices:
        # A caller-supplied price is the best evidence there is, so it gets a
        # dedicated engine rather than the shared cached one.
        config = ValuationConfig(currency=currency)
        engine = ValuationEngine(
            config=config,
            prices=build_resolver(
                currency=currency,
                offline=request.offline,
                manual=request.manual_prices,
            ),
            packs=build_pack_resolver(),
        )
    else:
        engine = _engine(currency, request.offline)

    try:
        valuation = engine.value(passport)
    except ValuationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BatteryWorldCupError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return valuation_to_dict(valuation)


async def value_image(
    file: UploadFile = File(..., description="Image containing the passport QR code"),
    currency: str = Query(default="EUR", min_length=3, max_length=3),
    offline: bool = Query(default=False),
) -> dict[str, Any]:
    """Decode a QR code from an uploaded image, then value the pack.

    Prefer decoding in the browser and posting to ``/v1/value``: it avoids the
    upload entirely and needs no server-side decoder.
    """
    from ..passport.scan import ScanUnavailableError, decode_image_bytes

    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="image exceeds 8 MB")

    try:
        payloads = decode_image_bytes(data)
    except ScanUnavailableError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except PassportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not payloads:
        raise HTTPException(status_code=422, detail="no QR code found in the image")

    return value(
        ValueRequest(payload=payloads[0], currency=currency, offline=offline)
    )


def _register_upload_route() -> bool:
    """Register the image-upload route only if multipart support is installed.

    FastAPI raises at registration time when ``python-multipart`` is absent, so
    guarding here keeps the rest of the API usable in a minimal install. The
    browser UI decodes client-side anyway, and only falls back to this route.
    """
    try:
        import python_multipart  # noqa: F401
    except ImportError:  # pragma: no cover - older releases only ship `multipart`
        try:
            import multipart  # noqa: F401
        except ImportError:
            logger.info(
                "python-multipart is not installed; /v1/value/image is disabled. "
                "Install with: pip install 'battery-worldcup[api]'"
            )
            return False
    app.post("/v1/value/image")(value_image)
    return True


UPLOAD_ROUTE_ENABLED = _register_upload_route()


@app.get("/v1/prices")
def prices(
    currency: str = Query(default="EUR", min_length=3, max_length=3),
    offline: bool = Query(default=False),
) -> dict[str, Any]:
    """Current market prices for every traded form, with provenance."""
    resolver = build_resolver(currency=currency.upper(), offline=offline)
    price_set = resolver.resolve_many(sorted(TRADED_FORMS))
    return {
        "currency": price_set.currency,
        "confidence": round(price_set.confidence, 3),
        "resolved_at": price_set.resolved_at.isoformat(),
        "sources_used": price_set.sources_used(),
        "missing": list(price_set.missing),
        "stale": list(price_set.stale_forms()),
        "quotes": [
            {
                "form": form,
                "label": TRADED_FORMS[form].label,
                "price": round(quote.price, 4),
                "unit": quote.unit.value,
                "currency": quote.currency,
                "price_per_kg_contained": round(quote.price_per_kg_contained(), 4),
                "payable_element": TRADED_FORMS[form].payable_element,
                "contained_fraction": round(
                    TRADED_FORMS[form].contained_fraction(), 5
                ),
                "as_of": quote.as_of.isoformat(),
                "source": quote.source,
                "quality": quote.quality.value,
                "confidence": round(quote.confidence(price_set.resolved_at), 3),
                "detail": quote.source_detail,
            }
            for form, quote in price_set.quotes.items()
        ],
    }


def _pack_payload(search: str | None = None) -> dict[str, Any]:
    """Build the catalogue payload.

    Kept separate from the route so other routes can reuse it: calling a
    FastAPI route function directly would hand it ``Query`` objects instead of
    plain values.
    """
    models = load_catalogue().models
    if search:
        needle = search.lower()
        models = tuple(
            model
            for model in models
            if needle in model.label.lower()
            or needle in model.manufacturer.lower()
            or any(needle in vehicle.lower() for vehicle in model.vehicle_models)
        )
    return {
        "count": len(models),
        "models": [
            {
                "key": model.key,
                "label": model.label,
                "manufacturer": model.manufacturer,
                "chemistry": model.chemistry,
                "rated_kwh": model.rated_kwh,
                "pack_mass_kg": model.pack_mass_kg,
                "module_count": model.module_count,
                "cell_count": model.cell_count,
                "vehicle_models": list(model.vehicle_models),
                "second_life_demand": model.second_life_demand,
                "confidence": model.confidence,
                "components": [
                    {
                        "key": component.key,
                        "label": component.label,
                        "count": component.count,
                        "total_mass_kg": round(component.total_mass_kg, 1),
                        "reusable": component.reusable,
                        "dominant_material": component.dominant_material,
                        "total_value_eur": component.total_value_eur,
                    }
                    for component in model.components
                ],
            }
            for model in models
        ],
    }


@app.get("/v1/packs")
def packs(search: str | None = Query(default=None)) -> dict[str, Any]:
    """The pack model catalogue."""
    return _pack_payload(search)


@app.get("/v1/packs/{key}")
def pack(key: str) -> dict[str, Any]:
    """One pack model by catalogue key."""
    for entry in _pack_payload()["models"]:
        if entry["key"] == key:
            return entry
    raise HTTPException(status_code=404, detail=f"unknown pack model {key!r}")


@app.get("/v1/providers", response_model=ProvidersResponse)
def providers(
    currency: str = Query(default="EUR", min_length=3, max_length=3),
    offline: bool = Query(default=False),
) -> ProvidersResponse:
    """Which data layers are wired up right now.

    The first thing to check when a valuation's confidence looks low.
    """
    return ProvidersResponse(
        prices=build_resolver(
            currency=currency.upper(), offline=offline
        ).describe_chain(),
        packs=build_pack_resolver().describe_chain(),
        baseline_snapshot_date=baseline_snapshot_date().isoformat(),
        currency=currency.upper(),
    )
