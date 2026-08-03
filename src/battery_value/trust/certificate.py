"""A certificate: who said what about this battery, and what has not changed.

The used battery trade is a market for lemons. A buyer cannot tell a good pack
from a tired one before handing over money and arranging dangerous-goods
freight, so every pack is priced as though it might be tired, and the owners of
good packs will not sell at that price. The market thins out until only the bad
packs are left in it, which is exactly what has happened.

The classical fix for a lemons market is not regulation, it is **credible
disclosure** -- a warranty, an inspection, a certificate. Something that lets a
good seller prove they are a good seller, cheaply enough that they bother.

This is that certificate, and its whole design rests on one distinction:

- what the **manufacturer declared** and nobody checked
- what a **measurement** recorded
- what **we computed** from those
- what was **cryptographically verified**

A document that blurs those is worth nothing, because the buyer cannot tell
which parts are load bearing. A certificate that keeps them apart is worth
paying for, because a buyer can price each claim according to what it actually
rests on.

It attests that the record has not been altered since issue and that it came
from this issuer. It does not make the manufacturer's declarations true, and it
says so in its own text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from ..passport.models import BatteryPassport
from ..store import StoredValuation
from . import compliance
from .signing import Signature, Signer, default_signer, verify

SCHEMA = "battery-value/certificate/1"

#: Wording that travels with every certificate. A certificate that overstates
#: what it proves is worse than none, because it launders a claim.
ATTESTATION = (
    "This certificate records who stated each fact about this battery and "
    "when. Its signature proves the record has not been altered since it was "
    "issued and that it came from the named issuer. It does not verify the "
    "manufacturer's own declarations, and claims marked 'declared' rest on "
    "their word alone."
)


class ClaimBasis(str, Enum):
    """What a claim actually rests on. The distinction the certificate exists for."""

    MEASURED = "measured"
    """Recorded by an instrument and dated."""

    DECLARED = "declared"
    """Stated by the manufacturer or the holder. Nobody checked it."""

    COMPUTED = "computed"
    """Derived here from the claims above, by a published method."""

    VERIFIED = "verified"
    """The source document carried a signature that was checked."""

    ABSENT = "absent"
    """Not supplied. Recorded because silence is itself worth knowing about."""

    @property
    def label(self) -> str:
        """How this is shown to someone reading the certificate."""
        return {
            ClaimBasis.MEASURED: "Measured",
            ClaimBasis.DECLARED: "Declared, unverified",
            ClaimBasis.COMPUTED: "Worked out from the above",
            ClaimBasis.VERIFIED: "Cryptographically verified",
            ClaimBasis.ABSENT: "Not supplied",
        }[self]

    @property
    def weight(self) -> float:
        """How much a buyer should let this move the price, 0-1."""
        return {
            ClaimBasis.VERIFIED: 1.0,
            ClaimBasis.MEASURED: 0.9,
            ClaimBasis.COMPUTED: 0.75,
            ClaimBasis.DECLARED: 0.5,
            ClaimBasis.ABSENT: 0.0,
        }[self]


# How much each claim moves what a buyer will pay. A carbon-footprint figure is
# a compliance obligation and a resale buyer prices it at nothing; state of
# health is most of the number. Averaging them flat would let a passport full of
# paperwork read as well evidenced while the one figure that matters is missing.
_IMPORTANCE = {
    "state_of_health": 3.0,
    "identity": 2.0,
    "manufacturing_date": 2.0,
    "rated_energy": 2.0,
    "cycle_count": 1.5,
    "wear_against_model": 1.5,
    "residual_value": 1.0,
}
_DEFAULT_IMPORTANCE = 0.5


@dataclass(frozen=True, slots=True)
class Claim:
    """One statement, with who made it."""

    key: str
    label: str
    value: Any
    basis: ClaimBasis
    source: str = ""
    unit: str = ""
    measured_at: str | None = None

    @property
    def importance(self) -> float:
        """How much this claim bears on the price."""
        return _IMPORTANCE.get(self.key, _DEFAULT_IMPORTANCE)

    def to_dict(self) -> dict[str, Any]:
        """Serialise. Part of the signed payload, so key order is fixed by sorting."""
        return {
            "key": self.key,
            "label": self.label,
            "value": self.value,
            "unit": self.unit,
            "basis": self.basis.value,
            "basis_label": self.basis.label,
            "source": self.source,
            "measured_at": self.measured_at,
        }


@dataclass(slots=True)
class Certificate:
    """A signed statement about one battery at one moment."""

    reference: str
    issued_at: datetime
    subject: dict[str, Any]
    claims: list[Claim]
    valuation: dict[str, Any]
    compliance: dict[str, Any]
    signature: Signature | None = None
    schema: str = SCHEMA
    attestation: str = ATTESTATION
    custody: list[dict[str, Any]] = field(default_factory=list)

    # -- the signed payload -------------------------------------------------

    def payload(self) -> dict[str, Any]:
        """Everything the signature covers.

        The signature itself is excluded, obviously, and so is nothing else --
        anything outside this dictionary is not protected, so there is nothing
        outside it that matters.
        """
        return {
            "schema": self.schema,
            "reference": self.reference,
            "issued_at": self.issued_at.isoformat(),
            "attestation": self.attestation,
            "subject": self.subject,
            "claims": [claim.to_dict() for claim in self.claims],
            "valuation": self.valuation,
            "compliance": self.compliance,
            "custody": self.custody,
        }

    def to_dict(self) -> dict[str, Any]:
        """The complete certificate, signature included."""
        document = self.payload()
        document["signature"] = self.signature.to_dict() if self.signature else None
        return document

    def sign(self, signer: Signer | None = None) -> Certificate:
        """Sign in place and return self."""
        self.signature = (signer or default_signer()).sign(self.payload())
        return self

    def verify(self) -> bool:
        """Whether the signature matches the content."""
        if self.signature is None:
            return False
        return verify(self.payload(), self.signature)

    # -- reading ------------------------------------------------------------

    def claim(self, key: str) -> Claim | None:
        """One claim by key."""
        return next((claim for claim in self.claims if claim.key == key), None)

    @property
    def evidence_strength(self) -> float:
        """How well evidenced this certificate is overall, 0-1.

        Weighted by how much each claim bears on the price, not a flat average:
        a certificate carrying a full set of compliance paperwork and no health
        reading is not well evidenced, whatever the field count says.

        It scores the buyer's *uncertainty*, not the battery. A measured
        certificate scores high whether the pack turns out to be good or bad,
        which is the point -- uncertainty is what was costing everyone money.
        """
        if not self.claims:
            return 0.0
        total = sum(claim.importance for claim in self.claims)
        if total <= 0:
            return 0.0
        return round(
            sum(claim.basis.weight * claim.importance for claim in self.claims)
            / total,
            3,
        )

    def strength_in_words(self) -> str:
        """The one sentence a buyer decides on, before driving anywhere.

        It leads with state of health rather than with the overall score,
        because that is the claim the money rests on. A certificate can be
        thick with compliance paperwork and still be worthless to a buyer if
        nobody measured the battery, and the reverse is also true.
        """
        health = self.claim("state_of_health")
        basis = health.basis if health else ClaimBasis.ABSENT
        strength = self.evidence_strength

        if basis in (ClaimBasis.MEASURED, ClaimBasis.VERIFIED):
            measured = (
                "The health reading is a measurement, dated and attributable"
                if basis is ClaimBasis.MEASURED
                else "The health reading came from a signed document that checked out"
            )
            rest = (
                "and the rest of the record is well evidenced too."
                if strength >= 0.7
                else "though most other figures here are the manufacturer's own."
            )
            return f"{measured}, {rest}"

        if basis is ClaimBasis.COMPUTED:
            return (
                "Nobody measured this battery. The health figure was worked out "
                "from its age and use, which is a reasonable estimate and not a "
                "reading — get it tested before money changes hands."
            )

        return (
            "This battery's health is not stated anywhere in the record. Treat "
            "every figure here as a claim until it has been tested."
        )


def _claims_from(passport: BatteryPassport, payload: dict[str, Any]) -> list[Claim]:
    """Build the claim list, marking each with what it actually rests on."""
    identity = passport.identity
    health = passport.health
    supply = passport.supply_chain
    battery = payload.get("battery", {})
    aging = payload.get("aging") or {}

    # A passport that arrived signed and checked lifts every field on it from
    # 'declared' to 'verified'. Nothing else does.
    document_basis = (
        ClaimBasis.VERIFIED if passport.source.verified else ClaimBasis.DECLARED
    )
    source = passport.source.reference or passport.source.kind

    claims: list[Claim] = [
        Claim(
            key="identity",
            label="Battery identifier",
            value=identity.battery_id or identity.passport_id or identity.serial_number,
            basis=document_basis if identity.battery_id else ClaimBasis.ABSENT,
            source=source,
        ),
        Claim(
            key="manufacturer",
            label="Manufacturer",
            value=identity.manufacturer or identity.brand,
            basis=document_basis if (identity.manufacturer or identity.brand)
            else ClaimBasis.ABSENT,
            source=source,
        ),
        Claim(
            key="manufacturing_date",
            label="Made",
            value=(
                identity.manufacturing_date.isoformat()
                if identity.manufacturing_date
                else None
            ),
            basis=document_basis if identity.manufacturing_date else ClaimBasis.ABSENT,
            source=source,
        ),
        Claim(
            key="rated_energy",
            label="Nameplate energy",
            value=passport.rated_kwh,
            unit="kWh",
            basis=document_basis if passport.rated_kwh else ClaimBasis.ABSENT,
            source=source,
        ),
    ]

    # State of health is the field the whole trade turns on, so its basis is
    # taken from how it was actually established rather than from the document.
    health_source = battery.get("health_source", "assumed")
    health_basis = {
        "measured": ClaimBasis.MEASURED,
        "cycles": ClaimBasis.COMPUTED,
        "age": ClaimBasis.COMPUTED,
        "assumed": ClaimBasis.ABSENT,
    }.get(health_source, ClaimBasis.DECLARED)
    if health_basis is ClaimBasis.MEASURED and passport.source.verified:
        health_basis = ClaimBasis.VERIFIED

    claims.append(
        Claim(
            key="state_of_health",
            label="State of health",
            value=round(float(battery.get("state_of_health") or 0.0) * 100, 1),
            unit="%",
            basis=health_basis,
            source=f"from {health_source}",
            measured_at=(
                health.measured_at.isoformat() if health.measured_at else None
            ),
        )
    )
    claims.append(
        Claim(
            key="cycle_count",
            label="Full cycles",
            value=health.cycle_count,
            basis=document_basis if health.cycle_count else ClaimBasis.ABSENT,
            source=source,
        )
    )

    if aging.get("comparable"):
        claims.append(
            Claim(
                key="wear_against_model",
                label="Wear against others of the same model",
                value=aging.get("verdict_label"),
                basis=ClaimBasis.COMPUTED,
                source="fade curve for this pack model",
            )
        )

    footprint = supply.footprint_per_kwh(passport.rated_kwh)
    claims.append(
        Claim(
            key="carbon_footprint",
            label="Carbon footprint",
            value=round(footprint, 1) if footprint else None,
            unit="kg CO2e/kWh",
            basis=document_basis if footprint else ClaimBasis.ABSENT,
            source=supply.carbon_footprint_study_url or source,
        )
    )
    claims.append(
        Claim(
            key="due_diligence",
            label="Supply chain due diligence",
            value=supply.due_diligence_scheme or supply.due_diligence_policy_url,
            basis=(
                ClaimBasis.DECLARED
                if (supply.due_diligence_scheme or supply.due_diligence_policy_url)
                else ClaimBasis.ABSENT
            ),
            source=supply.due_diligence_report_url or source,
        )
    )
    if supply.material_origin:
        claims.append(
            Claim(
                key="material_origin",
                label="Where the critical materials came from",
                value=supply.material_origin,
                basis=document_basis,
                source=source,
            )
        )
    if passport.composition.recycled_content_pct:
        claims.append(
            Claim(
                key="recycled_content",
                label="Recycled content",
                value=passport.composition.recycled_content_pct,
                unit="%",
                basis=document_basis,
                source=source,
            )
        )

    claims.append(
        Claim(
            key="residual_value",
            label="What it is worth",
            value=round(float(payload.get("residual_value", {}).get("amount", 0)), 2),
            unit=payload.get("residual_value", {}).get("currency", "EUR"),
            basis=ClaimBasis.COMPUTED,
            source="battery-value, from the claims above and published metal prices",
        )
    )
    return claims


def issue(
    record: StoredValuation,
    passport: BatteryPassport,
    *,
    signer: Signer | None = None,
    as_of: date | None = None,
    custody: list[dict[str, Any]] | None = None,
) -> Certificate:
    """Issue a signed certificate for a stored valuation.

    Args:
        record: The stored valuation. Its reference becomes the certificate's.
        passport: The passport it was produced from.
        signer: Issuing key. The process-wide one by default.
        as_of: Date to judge regulatory deadlines against.
        custody: Chain-of-custody events to embed, so they are signed too.
    """
    payload = record.payload
    view = compliance.assess(passport, as_of=as_of)

    subject = {
        "label": record.battery_label,
        "battery_id": passport.identity.battery_id,
        "serial_number": passport.identity.serial_number,
        "pack_model": record.pack_model_key,
        "chemistry": payload.get("bill_of_materials", {}).get("chemistry"),
        "rated_kwh": passport.rated_kwh,
    }

    valuation = {
        "reference": record.reference,
        "valued_at": record.created_at.isoformat(),
        "residual_value": payload.get("residual_value"),
        "value_range": payload.get("value_range"),
        "recommended_pathway": payload.get("recommended_pathway"),
        "confidence": payload.get("confidence"),
        "price_sources": payload.get("prices", {}).get("sources_used", {}),
        "oldest_price_as_of": payload.get("prices", {}).get("oldest_as_of"),
    }

    compliance_block = {
        "regulation": "EU 2023/1542",
        "assessed_as_of": view.as_of.isoformat(),
        "score": view.score,
        "summary": view.summary(),
        "requirements": [
            {
                "key": requirement.key,
                "article": requirement.article,
                "label": requirement.label,
                "state": requirement.state.value,
                "state_label": requirement.state.label,
                "applies_from": requirement.applies_from.isoformat(),
                "owner": requirement.owner,
                "detail": requirement.detail,
                "is_a_gap": requirement.is_a_gap,
            }
            for requirement in view.requirements
        ],
    }

    certificate = Certificate(
        reference=record.reference,
        issued_at=datetime.now(timezone.utc),
        subject=subject,
        claims=_claims_from(passport, payload),
        valuation=valuation,
        compliance=compliance_block,
        custody=list(custody or ()),
    )
    return certificate.sign(signer)


def from_dict(document: dict[str, Any]) -> Certificate:
    """Rebuild a certificate from its serialised form, for verification."""
    return Certificate(
        reference=str(document.get("reference", "")),
        issued_at=datetime.fromisoformat(document["issued_at"]),
        subject=document.get("subject", {}),
        claims=[
            Claim(
                key=raw["key"],
                label=raw.get("label", ""),
                value=raw.get("value"),
                basis=ClaimBasis(raw.get("basis", "declared")),
                source=raw.get("source", ""),
                unit=raw.get("unit", ""),
                measured_at=raw.get("measured_at"),
            )
            for raw in document.get("claims", [])
        ],
        valuation=document.get("valuation", {}),
        compliance=document.get("compliance", {}),
        signature=(
            Signature.from_dict(document["signature"])
            if document.get("signature")
            else None
        ),
        schema=str(document.get("schema", SCHEMA)),
        attestation=str(document.get("attestation", ATTESTATION)),
        custody=document.get("custody", []),
    )


__all__ = [
    "ATTESTATION",
    "SCHEMA",
    "Certificate",
    "Claim",
    "ClaimBasis",
    "from_dict",
    "issue",
]
