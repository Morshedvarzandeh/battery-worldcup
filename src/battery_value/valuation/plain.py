"""Plain-language wording for people who just own a battery.

The valuation engine works in state of health, payable fractions and traded
forms. Someone who has scanned the sticker on their pack wants none of that:
they want to know what it is worth, what to do with it, and how much to trust
the answer.

All the end-user copy lives here so it can be reviewed and changed as writing,
without touching valuation logic.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..materials.chemistry import ChemistrySpec
from .aging import AgingAssessment, AgingVerdict
from .models import ResidualValuation

# How each chemistry is described to someone who has never heard of NMC.
_CHEMISTRY_IN_PLAIN_WORDS: dict[str, str] = {
    "NMC811": "Nickel-rich lithium-ion",
    "NMC712": "Nickel-rich lithium-ion",
    "NMC622": "Nickel-manganese-cobalt lithium-ion",
    "NMC532": "Nickel-manganese-cobalt lithium-ion",
    "NMC111": "Nickel-manganese-cobalt lithium-ion",
    "NCA": "Nickel-cobalt-aluminium lithium-ion",
    "LFP": "Lithium iron phosphate",
    "LMFP": "Lithium manganese iron phosphate",
    "LMO": "Lithium manganese oxide",
    "LCO": "Lithium cobalt oxide",
    "LTO": "Lithium titanate",
    "NA_ION": "Sodium-ion",
    "NIMH": "Nickel metal hydride (hybrid battery)",
    "LEAD_ACID": "Lead-acid",
}

# Why each chemistry is worth what it is, in one sentence.
_CHEMISTRY_VALUE_NOTE: dict[str, str] = {
    "LFP": (
        "It contains no nickel or cobalt, so recycling it is not profitable. "
        "Its long life is what makes it valuable instead."
    ),
    "NA_ION": (
        "It contains no lithium, nickel or cobalt, so there is very little "
        "material value to recover."
    ),
    "LEAD_ACID": (
        "Lead is recycled almost completely and always has a scrap price, so "
        "these batteries are worth money even when they are worn out."
    ),
    "NIMH": (
        "It is rich in nickel, which recyclers pay for, and replacement demand "
        "for hybrid batteries is strong."
    ),
}

_HIGH_NICKEL_NOTE = (
    "It is rich in nickel and cobalt, which are the metals recyclers pay most for."
)


@dataclass(frozen=True, slots=True)
class ConfidenceBand:
    """How much to trust the number, in words rather than a percentage."""

    label: str
    explanation: str
    tone: str
    """``good``, ``fair`` or ``weak`` — for styling only."""


def confidence_band(confidence: float) -> ConfidenceBand:
    """Translate a 0-1 confidence into something a person can act on."""
    if confidence >= 0.75:
        return ConfidenceBand(
            "Good estimate",
            "We had solid information about this battery and current prices.",
            "good",
        )
    if confidence >= 0.55:
        return ConfidenceBand(
            "Reasonable estimate",
            "Most of what we needed was available. Treat it as a guide price.",
            "fair",
        )
    if confidence >= 0.35:
        return ConfidenceBand(
            "Rough estimate",
            "Some details were missing or the prices we used are not current. "
            "The real figure could differ noticeably.",
            "fair",
        )
    return ConfidenceBand(
        "Very rough estimate",
        "Important details were missing, so treat this as a ballpark only and "
        "get a quote before deciding anything.",
        "weak",
    )


def chemistry_in_plain_words(chemistry: ChemistrySpec) -> str:
    """Describe a cell chemistry without the acronym."""
    return _CHEMISTRY_IN_PLAIN_WORDS.get(chemistry.key, chemistry.label)


def chemistry_value_note(chemistry: ChemistrySpec) -> str:
    """One sentence on why this chemistry is worth what it is."""
    note = _CHEMISTRY_VALUE_NOTE.get(chemistry.key)
    if note:
        return note
    if chemistry.contains_nickel_or_cobalt():
        return _HIGH_NICKEL_NOTE
    return ""


def health_in_plain_words(soh: float) -> str:
    """Describe state of health without the term."""
    if soh >= 0.90:
        return "still close to new"
    if soh >= 0.80:
        return "in good shape for its age"
    if soh >= 0.70:
        return "noticeably worn but still useful"
    if soh >= 0.60:
        return "well worn"
    return "near the end of its working life"


def _years(value: float) -> str:
    """Say a number of years the way a person would."""
    if value < 1:
        return "less than a year"
    if value < 1.5:
        return "about a year"
    return f"about {value:.0f} years"


def aging_headline(aging: AgingAssessment) -> str:
    """One sentence on whether this battery is wearing out normally.

    Answers the question a state-of-health figure cannot answer alone: 87% is
    excellent on a nine-year-old car and disappointing on a two-year-old one.
    """
    typical = f"{aging.expected_soh:.0%}"
    mine = f"{aging.observed_soh:.0%}"
    age = _years(aging.age_years or 0)

    if aging.verdict is AgingVerdict.UNKNOWN:
        return (
            f"After {age}, a battery like this is usually around {typical} "
            "health. We could not check yours against that without a capacity "
            "reading."
        )

    if aging.verdict is AgingVerdict.AHEAD:
        return (
            f"Yours is at {mine} after {age}, where most batteries like it are "
            f"around {typical}. It has held up better than most."
        )

    if aging.verdict is AgingVerdict.BEHIND:
        return (
            f"Yours is at {mine} after {age}, where most batteries like it are "
            f"around {typical}. It has worn faster than most."
        )

    return (
        f"Yours is at {mine} after {age}, and most batteries like it are around "
        f"{typical}. That is normal wear."
    )


def aging_outlook(aging: AgingAssessment) -> str:
    """What the wear means for the value, looking forward.

    The sentence people act on. A battery two years from dropping out of the
    resale market is worth selling now, and nothing in today's health figure
    says so.
    """
    if aging.already_below_storage_floor:
        return (
            "It is now too worn for anyone to rebuild or resell, so recycling "
            "is what is left."
        )
    if aging.already_below_resale_floor:
        if aging.years_to_storage_floor is None:
            return (
                "It is past the point where someone would fit it to another "
                "vehicle, but it has plenty left for home or grid storage."
            )
        return (
            "It is past the point where someone would fit it to another "
            f"vehicle. It stays useful for home or grid storage for "
            f"{_years(aging.years_to_storage_floor)} yet."
        )
    if aging.years_to_resale_floor is None or aging.years_to_resale_floor > 10:
        # Past a decade the curve is extrapolating well beyond anything anyone
        # has measured, so a precise figure would be false precision.
        return (
            "At this rate it stays good enough to sell as a working battery "
            "for many years yet, so there is no rush."
        )
    if aging.years_to_resale_floor <= 2.0:
        return (
            f"At this rate it has {_years(aging.years_to_resale_floor)} left "
            "before buyers stop treating it as a working battery, which is when "
            "its value drops. Selling before then is worth real money."
        )
    return (
        f"At this rate it stays good enough to sell as a working battery for "
        f"{_years(aging.years_to_resale_floor)}."
    )


def aging_notes(aging: AgingAssessment) -> list[str]:
    """The handful of things that explain the wear, in plain words."""
    notes: list[str] = []

    harder = aging.uses_more_than_typical
    if harder is not None and aging.cycles_used and aging.cycles_expected:
        if harder:
            notes.append(
                f"It has been charged and discharged about "
                f"{aging.cycles_used:,} times, where a typical battery of this "
                f"age would be near {aging.cycles_expected:,.0f}. It has done "
                "more work than most."
            )
        elif aging.cycles_used < aging.cycles_expected * 0.85:
            notes.append(
                f"It has only been through about {aging.cycles_used:,} charges, "
                f"fewer than the {aging.cycles_expected:,.0f} typical at this "
                "age, so it has had an easy life."
            )

    cooling = aging.thermal_management
    if cooling == "passive":
        notes.append(
            "This pack has no cooling system, so heat is the main thing that "
            "wears it out. Fast charging in summer is what does the damage."
        )
    elif cooling == "liquid":
        notes.append(
            "This pack is liquid-cooled, which is why batteries of this model "
            "generally age slowly and hot weather matters less."
        )

    if aging.climate in {"warm", "hot"}:
        notes.append(
            "Hot climates wear batteries out faster, and that is allowed for "
            "in the figures above."
        )

    if aging.annual_fade_ahead:
        notes.append(
            f"Expect it to lose roughly {aging.annual_fade_ahead:.1f} more "
            "points of health over the next year."
        )

    notes.extend(aging.notes)
    return notes


def headline_sentence(valuation: ResidualValuation) -> str:
    """The single sentence that answers the question the user actually asked."""
    value = valuation.residual_value
    best = valuation.recommended

    if best is None:
        return (
            "We could not find a way to get value from this battery. A licensed "
            "recycler will be able to advise on safe disposal."
        )

    if value.is_negative:
        return (
            f"Handling this battery safely costs about {(-value).format(0)} more "
            f"than it is worth. That is normal for its type and condition."
        )

    return (
        f"Your battery is worth about {value.format(0)}, and the best way to get "
        f"that is to {best.pathway.friendly_label[0].lower()}{best.pathway.friendly_label[1:]}."
    )


def why_this_value(valuation: ResidualValuation) -> list[str]:
    """The two or three things that most explain the number."""
    reasons: list[str] = []
    chemistry = valuation.bom.chemistry

    reasons.append(
        f"Your battery is {chemistry_in_plain_words(chemistry).lower()} and is "
        f"{health_in_plain_words(valuation.state_of_health)} at "
        f"{valuation.state_of_health:.0%} health."
    )

    note = chemistry_value_note(chemistry)
    if note:
        reasons.append(note)

    if valuation.aging and valuation.aging.is_comparable:
        reasons.append(aging_headline(valuation.aging))

    if valuation.pack_model:
        reasons.append(
            f"We recognised it as a {valuation.pack_model.label}, so we know what "
            "is inside it and what the parts sell for."
        )
    else:
        reasons.append(
            "We could not identify the exact pack model, so this is based on "
            "typical figures for a battery of this type and size."
        )

    return reasons


def how_to_improve(valuation: ResidualValuation) -> list[str]:
    """Concrete things the user could do to get a better number.

    Ordered by how much difference each would make.
    """
    suggestions: list[str] = []

    if valuation.health_source in {"assumed", "age"}:
        suggestions.append(
            "A health check from a garage, or the vehicle's own battery report, "
            "would make the biggest difference — health drives most of the value."
        )
    elif valuation.aging and not valuation.aging.is_comparable:
        suggestions.append(
            "A measured capacity reading would let us tell you whether this "
            "battery has held up better or worse than others of the same model, "
            "which is what a buyer will want to know."
        )

    if valuation.pack_model is None:
        suggestions.append(
            "Telling us the exact vehicle or pack model would let us price the "
            "individual parts, which is often the most valuable option."
        )

    if valuation.prices.stale_forms():
        suggestions.append(
            "The metal prices behind this estimate are not up to date, so the "
            "recycling figure in particular could move."
        )

    return suggestions


def unavailable_reason(pathway_valuation) -> str:
    """The single clearest reason a route is not open, in plain words."""
    if not pathway_valuation.blockers:
        return "Not available for this battery."
    return pathway_valuation.blockers[0]
