"""What Regulation (EU) 2023/1542 will ask for, and whether it is there.

Not a compliance service. This is a readiness view: for a given passport, which
of the regulated fields are present, which are missing, and when each one starts
to matter. The dates are the point -- an operator planning for February 2027 has
different priorities from one who has to declare a carbon footprint now.

Every entry says *who* has to supply it, because most of these are the
manufacturer's obligation and cannot be fixed by whoever holds the pack today.
Telling a garage they are non-compliant for a missing due-diligence policy would
be both wrong and useless.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from ..passport.models import BatteryPassport

#: Article 8 minimum recycled content, from 18 August 2031, for industrial and
#: EV batteries. A second, higher tier follows in 2036.
RECYCLED_CONTENT_MINIMA_2031 = {"Co": 16.0, "Pb": 85.0, "Li": 6.0, "Ni": 6.0}
RECYCLED_CONTENT_MINIMA_2036 = {"Co": 26.0, "Pb": 85.0, "Li": 12.0, "Ni": 15.0}


class Readiness(str, Enum):
    """Where one requirement stands."""

    PRESENT = "present"
    MISSING = "missing"
    NOT_APPLICABLE = "not_applicable"

    @property
    def label(self) -> str:
        """Human-readable state."""
        return {
            Readiness.PRESENT: "Declared",
            Readiness.MISSING: "Not declared",
            Readiness.NOT_APPLICABLE: "Does not apply",
        }[self]


@dataclass(frozen=True, slots=True)
class Requirement:
    """One thing the regulation asks for."""

    key: str
    article: str
    label: str
    applies_from: date
    owner: str
    """Who has to supply it: ``manufacturer``, ``holder`` or ``recycler``."""

    state: Readiness
    detail: str = ""

    @property
    def is_due(self) -> bool:
        """Whether the obligation has already started."""
        return self.applies_from <= date.today()

    @property
    def is_a_gap(self) -> bool:
        """Missing and already due."""
        return self.state is Readiness.MISSING and self.is_due


@dataclass(frozen=True, slots=True)
class ComplianceView:
    """A passport measured against the regulation, with the dates attached."""

    requirements: tuple[Requirement, ...]
    as_of: date

    @property
    def present(self) -> tuple[Requirement, ...]:
        """Requirements the passport satisfies."""
        return tuple(r for r in self.requirements if r.state is Readiness.PRESENT)

    @property
    def gaps(self) -> tuple[Requirement, ...]:
        """Missing and already due."""
        return tuple(r for r in self.requirements if r.is_a_gap)

    @property
    def upcoming(self) -> tuple[Requirement, ...]:
        """Missing, but not yet required. Earliest deadline first."""
        return tuple(
            sorted(
                (
                    r
                    for r in self.requirements
                    if r.state is Readiness.MISSING and not r.is_due
                ),
                key=lambda r: r.applies_from,
            )
        )

    @property
    def score(self) -> float:
        """Share of applicable requirements that are declared, 0-1."""
        applicable = [
            r for r in self.requirements if r.state is not Readiness.NOT_APPLICABLE
        ]
        if not applicable:
            return 1.0
        return round(len(self.present) / len(applicable), 3)

    def summary(self) -> str:
        """One line for someone who is not going to read the table."""
        if not self.gaps:
            if self.upcoming:
                nearest = self.upcoming[0]
                return (
                    f"Nothing overdue. Next is {nearest.label.lower()}, "
                    f"from {nearest.applies_from:%B %Y}."
                )
            return "Everything the regulation asks for is declared."
        owners = {r.owner for r in self.gaps}
        who = " and ".join(sorted(owners))
        return (
            f"{len(self.gaps)} of {len(self.requirements)} requirements are not "
            f"declared and already due. All of them are the {who}'s to supply."
        )


def _state(present: bool) -> Readiness:
    return Readiness.PRESENT if present else Readiness.MISSING


def assess(passport: BatteryPassport, *, as_of: date | None = None) -> ComplianceView:
    """Measure a passport against the regulation.

    Args:
        passport: The normalised passport.
        as_of: Date to judge deadlines against, defaulting to today.
    """
    today = as_of or date.today()
    supply = passport.supply_chain
    identity = passport.identity
    health = passport.health
    composition = passport.composition

    requirements = [
        Requirement(
            key="identity",
            article="Annex XIII 1(a)",
            label="Battery identifier and manufacturer",
            applies_from=date(2027, 2, 18),
            owner="manufacturer",
            state=_state(bool(identity.battery_id or identity.passport_id)),
            detail="The unique identifier the passport is keyed on.",
        ),
        Requirement(
            key="manufacturing_date",
            article="Annex XIII 1(d)",
            label="Date of manufacture",
            applies_from=date(2027, 2, 18),
            owner="manufacturer",
            state=_state(identity.manufacturing_date is not None),
            detail="Without it, nothing can say how the battery has aged.",
        ),
        Requirement(
            key="rated_capacity",
            article="Annex XIII 2",
            label="Rated capacity",
            applies_from=date(2027, 2, 18),
            owner="manufacturer",
            state=_state(bool(passport.rated_kwh)),
        ),
        Requirement(
            key="state_of_health",
            article="Annex XIII 3",
            label="State of health",
            applies_from=date(2027, 2, 18),
            owner="holder",
            state=_state(health.soh_fraction is not None),
            detail=(
                "The single biggest driver of what the battery is worth, and the "
                "one field a holder can supply themselves."
            ),
        ),
        Requirement(
            key="cycle_count",
            article="Annex XIII 3",
            label="Number of full cycles",
            applies_from=date(2027, 2, 18),
            owner="holder",
            state=_state(bool(health.cycle_count)),
        ),
        Requirement(
            key="composition",
            article="Annex XIII 4",
            label="Critical raw material content",
            applies_from=date(2027, 2, 18),
            owner="manufacturer",
            state=_state(not composition.is_empty),
            detail="Cobalt, lithium, nickel and lead content by mass.",
        ),
        Requirement(
            key="carbon_footprint",
            article="Article 7",
            label="Carbon footprint declaration",
            applies_from=date(2025, 2, 18),
            owner="manufacturer",
            state=_state(supply.footprint_per_kwh(passport.rated_kwh) is not None),
            detail="Life-cycle CO2e per kWh, with the study behind it.",
        ),
        Requirement(
            key="due_diligence",
            article="Articles 48-53",
            label="Supply chain due diligence policy",
            applies_from=date(2025, 8, 18),
            owner="manufacturer",
            state=_state(
                bool(supply.due_diligence_policy_url or supply.due_diligence_scheme)
            ),
            detail=(
                "Covers cobalt, lithium, nickel and natural graphite, aligned "
                "with the OECD guidance and third-party audited."
            ),
        ),
        Requirement(
            key="recycled_content",
            article="Article 8",
            label="Recycled content shares",
            applies_from=date(2031, 8, 18),
            owner="manufacturer",
            state=_state(bool(composition.recycled_content_pct)),
            detail=(
                "Minimum shares of recovered cobalt, lead, lithium and nickel. "
                "Declaration is required from 2028; the minima bite in 2031."
            ),
        ),
        Requirement(
            key="material_origin",
            article="Annex XII / Article 49",
            label="Country of origin of critical materials",
            applies_from=date(2025, 8, 18),
            owner="manufacturer",
            state=_state(bool(supply.material_origin)),
        ),
    ]

    return ComplianceView(requirements=tuple(requirements), as_of=today)


def recycled_content_gap(
    passport: BatteryPassport, *, minima: dict[str, float] | None = None
) -> dict[str, float]:
    """How far each declared recycled share sits below the 2031 minimum.

    Returns element -> shortfall in percentage points, empty when a share is not
    declared at all. A missing declaration is not a shortfall of zero, and
    reporting it as one would turn silence into a pass.
    """
    thresholds = minima or RECYCLED_CONTENT_MINIMA_2031
    declared = passport.composition.recycled_content_pct
    return {
        element: round(threshold - declared[element], 1)
        for element, threshold in thresholds.items()
        if element in declared and declared[element] < threshold
    }


__all__ = [
    "RECYCLED_CONTENT_MINIMA_2031",
    "RECYCLED_CONTENT_MINIMA_2036",
    "ComplianceView",
    "Readiness",
    "Requirement",
    "assess",
    "recycled_content_gap",
]
