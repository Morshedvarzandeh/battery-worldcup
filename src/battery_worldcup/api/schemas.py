"""Request and response schemas for the HTTP API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class ScanRequest(BaseModel):
    """A scanned carrier payload, or a passport document supplied directly."""

    payload: str | None = Field(
        default=None,
        description="Raw QR payload: a URL, a GS1 Digital Link, inline JSON or an identifier.",
    )
    document: dict[str, Any] | None = Field(
        default=None,
        description="A passport document, when the caller already has one.",
    )
    allow_private_hosts: bool = Field(
        default=False,
        description=(
            "Permit fetching passports from private or loopback addresses. "
            "Leave off unless the passport host is a trusted internal service."
        ),
    )

    @model_validator(mode="after")
    def _require_one_source(self) -> ScanRequest:
        if not self.payload and not self.document:
            raise ValueError("supply either 'payload' or 'document'")
        return self


class ValueRequest(ScanRequest):
    """A scan plus the market options to value it under."""

    currency: str = Field(default="EUR", min_length=3, max_length=3)
    offline: bool = Field(
        default=False,
        description="Skip network price providers and use the bundled snapshot.",
    )
    manual_prices: dict[str, float] | None = Field(
        default=None,
        description=(
            "Prices the caller has actually been quoted, per tonne of the traded "
            "form, in the requested currency. These take priority over every "
            "other provider."
        ),
    )


class ProviderInfo(BaseModel):
    """One entry in a provider chain."""

    key: str
    label: str
    available: bool
    detail: str


class ProvidersResponse(BaseModel):
    """Diagnostics for both provider chains."""

    prices: list[str]
    packs: list[str]
    baseline_snapshot_date: str
    currency: str


class HealthResponse(BaseModel):
    """Liveness and dataset versions."""

    status: str
    version: str
    chemistries: int
    pack_models: int
    baseline_snapshot_date: str
