"""Turn valuation results into plain JSON-safe structures.

Shared by the CLI's ``--json`` mode and the HTTP API so both emit exactly the
same shape.
"""

from __future__ import annotations

from typing import Any

from .money import Money
from .valuation import plain
from .valuation.models import (
    PathwayValuation,
    ResidualValuation,
    ValueLine,
)


def money_to_dict(money: Money) -> dict[str, Any]:
    """Serialise a :class:`Money`."""
    return {
        "amount": round(money.amount, 2),
        "currency": money.currency,
        "formatted": money.format(0),
    }


def line_to_dict(line: ValueLine) -> dict[str, Any]:
    """Serialise one value line."""
    return {
        "label": line.label,
        "kind": line.kind.value,
        "amount": money_to_dict(line.amount),
        "detail": line.detail,
    }


def pathway_to_dict(
    pathway: PathwayValuation, rated_kwh: float
) -> dict[str, Any]:
    """Serialise one pathway valuation."""
    return {
        "pathway": pathway.pathway.value,
        "label": pathway.label,
        "friendly_label": pathway.pathway.friendly_label,
        "explanation": pathway.pathway.plain_explanation,
        "eligible": pathway.eligible,
        "net_value": money_to_dict(pathway.net_value),
        "value_per_kwh": money_to_dict(pathway.value_per_kwh(rated_kwh)),
        "gross_revenue": money_to_dict(pathway.gross_revenue),
        "total_cost": money_to_dict(pathway.total_cost),
        "confidence": pathway.confidence,
        "lines": [line_to_dict(line) for line in pathway.lines],
        "assumptions": pathway.assumptions,
        "blockers": pathway.blockers,
    }


def aging_to_dict(valuation: ResidualValuation) -> dict[str, Any] | None:
    """Serialise the wear assessment, prose included.

    The wording travels with the numbers so a stored record reads the same
    months later, and so a partner app does not have to reinvent how to phrase
    "your battery is wearing out faster than most".
    """
    aging = valuation.aging
    if aging is None:
        return None

    return {
        "verdict": aging.verdict.value,
        "verdict_label": aging.verdict.label,
        "tone": aging.verdict.tone,
        "headline": plain.aging_headline(aging),
        "outlook": plain.aging_outlook(aging),
        "notes": plain.aging_notes(aging),
        "comparable": aging.is_comparable,
        "age_years": aging.age_years,
        "observed_soh": aging.observed_soh,
        "expected_soh": aging.expected_soh,
        "deviation_points": aging.deviation_points,
        "spread_points": aging.spread_points,
        "fade_ratio": aging.fade_ratio,
        "annual_fade_ahead_points": aging.annual_fade_ahead,
        "years_to_resale_floor": aging.years_to_resale_floor,
        "years_to_storage_floor": aging.years_to_storage_floor,
        "already_below_resale_floor": aging.already_below_resale_floor,
        "already_below_storage_floor": aging.already_below_storage_floor,
        "resale_floor": aging.resale_floor,
        "storage_floor": aging.storage_floor,
        "cycles_used": aging.cycles_used,
        "cycles_expected": aging.cycles_expected,
        "climate": aging.climate,
        "thermal_management": aging.thermal_management,
        "confidence": aging.confidence,
        "profile": {
            "key": aging.profile_key,
            "label": aging.profile_label,
            "model_specific": aging.is_model_specific,
        },
        "trajectory": [
            {
                "age_years": point.age_years,
                "projected_soh": point.projected_soh,
                "cohort_soh": point.cohort_soh,
            }
            for point in aging.trajectory
        ],
    }


def plain_language_block(valuation: ResidualValuation) -> dict[str, Any]:
    """Everything the end-user view needs, already written as prose.

    Kept in the payload so any client -- the bundled UI, a partner app, a
    chatbot -- shows the same wording rather than inventing its own.
    """
    band = plain.confidence_band(valuation.confidence)
    chemistry = valuation.bom.chemistry
    return {
        "headline": plain.headline_sentence(valuation),
        "confidence": {
            "label": band.label,
            "explanation": band.explanation,
            "tone": band.tone,
        },
        "chemistry": plain.chemistry_in_plain_words(chemistry),
        "chemistry_note": plain.chemistry_value_note(chemistry),
        "health": plain.health_in_plain_words(valuation.state_of_health),
        "why": plain.why_this_value(valuation),
        "how_to_improve": plain.how_to_improve(valuation),
    }


def valuation_to_dict(valuation: ResidualValuation) -> dict[str, Any]:
    """Serialise a full residual valuation."""
    recommended = valuation.recommended
    return {
        "plain": plain_language_block(valuation),
        "battery": {
            "label": valuation.battery_label,
            "rated_kwh": valuation.rated_kwh,
            "state_of_health": round(valuation.state_of_health, 4),
            "health_source": valuation.health_source,
            "condition": valuation.condition,
            "pack_model": (
                {
                    "key": valuation.pack_model.key,
                    "label": valuation.pack_model.label,
                    "manufacturer": valuation.pack_model.manufacturer,
                    "chemistry": valuation.pack_model.chemistry,
                    "module_count": valuation.pack_model.module_count,
                    "confidence": valuation.pack_model.confidence,
                }
                if valuation.pack_model
                else None
            ),
        },
        "aging": aging_to_dict(valuation),
        "residual_value": money_to_dict(valuation.residual_value),
        "value_per_kwh": money_to_dict(valuation.value_per_kwh),
        "recommended_pathway": recommended.pathway.value if recommended else None,
        "confidence": round(valuation.confidence, 3),
        "summary": valuation.summary(),
        "value_range": (
            {
                "low": money_to_dict(valuation.value_range.low),
                "expected": money_to_dict(valuation.value_range.expected),
                "high": money_to_dict(valuation.value_range.high),
                "driver": valuation.value_range.driver,
            }
            if valuation.value_range
            else None
        ),
        "pathways": [
            pathway_to_dict(pathway, valuation.rated_kwh)
            for pathway in valuation.pathways
        ],
        "sensitivity": [
            {
                "name": factor.name,
                "low": money_to_dict(factor.low),
                "high": money_to_dict(factor.high),
                "swing": money_to_dict(factor.swing),
            }
            for factor in valuation.sensitivity
        ],
        "bill_of_materials": {
            "chemistry": valuation.bom.chemistry.key,
            "pack_mass_kg": round(valuation.bom.pack_mass_kg, 1),
            "inert_mass_kg": round(valuation.bom.inert_mass_kg, 1),
            "declared_fraction": round(valuation.bom.declared_fraction, 3),
            "lines": [
                {
                    "element": line.element,
                    "mass_kg": round(line.mass_kg, 3),
                    "source": line.source,
                    "basis": line.basis,
                }
                for line in valuation.bom.sorted_lines()
            ],
        },
        "prices": {
            "currency": valuation.prices.currency,
            "confidence": round(valuation.prices.confidence, 3),
            "resolved_at": valuation.prices.resolved_at.isoformat(),
            "oldest_as_of": (
                valuation.prices.oldest_as_of.isoformat()
                if valuation.prices.oldest_as_of
                else None
            ),
            "sources_used": valuation.prices.sources_used(),
            "missing": list(valuation.prices.missing),
            "quotes": [
                {
                    "form": form,
                    "price": round(quote.price, 4),
                    "currency": quote.currency,
                    "unit": quote.unit.value,
                    "price_per_kg_contained": round(quote.price_per_kg_contained(), 4),
                    "as_of": quote.as_of.isoformat(),
                    "source": quote.source,
                    "quality": quote.quality.value,
                    "detail": quote.source_detail,
                }
                for form, quote in valuation.prices.quotes.items()
            ],
        },
        "warnings": valuation.warnings,
        "provenance": valuation.provenance,
        "generated_at": (
            valuation.generated_at.isoformat() if valuation.generated_at else None
        ),
    }


def passport_to_dict(passport) -> dict[str, Any]:
    """Serialise a passport without its bulky raw source document."""
    data = passport.model_dump(mode="json", exclude={"raw"})
    data["derived"] = {
        "rated_kwh": passport.rated_kwh,
        "remaining_kwh": passport.remaining_kwh,
        "chemistry": (
            passport.technical.chemistry.key if passport.technical.chemistry else None
        ),
        "age_years": passport.age_years(),
        "declared_masses_kg": passport.declared_masses(),
        "completeness": passport.completeness().model_dump(mode="json"),
    }
    return data
