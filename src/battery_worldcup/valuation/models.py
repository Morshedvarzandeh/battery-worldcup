"""Result types for a residual valuation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..materials.bom import BillOfMaterials
from ..market.types import PriceSet
from ..money import Money
from ..packs.models import PackModel


class Pathway(str, Enum):
    """What can be done with a retired pack, best-value first in practice."""

    REUSE = "reuse"
    """Resell as a replacement traction pack."""

    PARTS_OUT = "parts_out"
    """Dismantle and sell modules and components individually."""

    SECOND_LIFE = "second_life"
    """Repurpose into a stationary storage product."""

    RECYCLING = "recycling"
    """Shred and recover the contained materials."""

    @property
    def label(self) -> str:
        """Human-readable name."""
        return {
            Pathway.REUSE: "Resale as replacement pack",
            Pathway.PARTS_OUT: "Dismantle and sell components",
            Pathway.SECOND_LIFE: "Second-life stationary storage",
            Pathway.RECYCLING: "Material recycling",
        }[self]


class LineKind(str, Enum):
    """Whether a line adds to or subtracts from the pathway's value."""

    REVENUE = "revenue"
    COST = "cost"


@dataclass(frozen=True, slots=True)
class ValueLine:
    """One item in a pathway's value build-up."""

    label: str
    amount: Money
    kind: LineKind
    detail: str = ""

    @property
    def signed_amount(self) -> Money:
        """Amount with costs negated, so lines can simply be summed."""
        return self.amount if self.kind is LineKind.REVENUE else -self.amount


@dataclass(slots=True)
class PathwayValuation:
    """What one pathway is worth, and how that number was built."""

    pathway: Pathway
    eligible: bool
    lines: list[ValueLine] = field(default_factory=list)
    confidence: float = 0.5
    assumptions: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    currency: str = "EUR"

    @property
    def label(self) -> str:
        """Human-readable pathway name."""
        return self.pathway.label

    @property
    def gross_revenue(self) -> Money:
        """Sum of all revenue lines."""
        total = Money.zero(self.currency)
        for line in self.lines:
            if line.kind is LineKind.REVENUE:
                total = total + line.amount
        return total

    @property
    def total_cost(self) -> Money:
        """Sum of all cost lines."""
        total = Money.zero(self.currency)
        for line in self.lines:
            if line.kind is LineKind.COST:
                total = total + line.amount
        return total

    @property
    def net_value(self) -> Money:
        """Revenue minus cost. Negative means the holder pays to dispose."""
        return self.gross_revenue - self.total_cost

    def value_per_kwh(self, rated_kwh: float) -> Money:
        """Net value per kWh of nameplate energy."""
        return self.net_value / rated_kwh if rated_kwh > 0 else Money.zero(self.currency)

    def revenue_lines(self) -> list[ValueLine]:
        """Revenue lines, largest first."""
        return sorted(
            (line for line in self.lines if line.kind is LineKind.REVENUE),
            key=lambda line: line.amount.amount,
            reverse=True,
        )

    def cost_lines(self) -> list[ValueLine]:
        """Cost lines, largest first."""
        return sorted(
            (line for line in self.lines if line.kind is LineKind.COST),
            key=lambda line: line.amount.amount,
            reverse=True,
        )


@dataclass(frozen=True, slots=True)
class ValuationRange:
    """A low/expected/high band from the sensitivity analysis."""

    low: Money
    expected: Money
    high: Money
    driver: str = ""

    @property
    def spread(self) -> Money:
        """Width of the band."""
        return self.high - self.low

    def describe(self) -> str:
        """Human-readable band."""
        return f"{self.low.format(0)} to {self.high.format(0)} (expected {self.expected.format(0)})"


@dataclass(frozen=True, slots=True)
class SensitivityFactor:
    """How much one input moves the headline number."""

    name: str
    low: Money
    high: Money
    swing: Money

    def describe(self) -> str:
        """Human-readable impact line."""
        return f"{self.name}: {self.low.format(0)} to {self.high.format(0)}"


@dataclass(slots=True)
class ResidualValuation:
    """The complete answer: what the pack is worth, by which route, and why."""

    battery_label: str
    rated_kwh: float
    state_of_health: float
    pathways: list[PathwayValuation]
    prices: PriceSet
    bom: BillOfMaterials
    currency: str = "EUR"
    pack_model: PackModel | None = None
    value_range: ValuationRange | None = None
    sensitivity: list[SensitivityFactor] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    provenance: list[str] = field(default_factory=list)
    generated_at: datetime | None = None

    @property
    def eligible_pathways(self) -> list[PathwayValuation]:
        """Pathways the pack actually qualifies for, most valuable first."""
        return sorted(
            (pathway for pathway in self.pathways if pathway.eligible),
            key=lambda pathway: pathway.net_value.amount,
            reverse=True,
        )

    @property
    def recommended(self) -> PathwayValuation | None:
        """The pathway that realises the most value."""
        eligible = self.eligible_pathways
        return eligible[0] if eligible else None

    @property
    def residual_value(self) -> Money:
        """Headline residual value: the best route available.

        A negative figure is a real and common outcome for LFP and sodium-ion
        packs, and means disposal costs more than the materials are worth.
        """
        best = self.recommended
        return best.net_value if best else Money.zero(self.currency)

    @property
    def value_per_kwh(self) -> Money:
        """Headline value per kWh of nameplate energy."""
        return (
            self.residual_value / self.rated_kwh
            if self.rated_kwh > 0
            else Money.zero(self.currency)
        )

    @property
    def confidence(self) -> float:
        """Confidence in the headline number, 0-1."""
        best = self.recommended
        return best.confidence if best else 0.0

    def pathway(self, pathway: Pathway) -> PathwayValuation | None:
        """Look up one pathway's valuation."""
        for candidate in self.pathways:
            if candidate.pathway is pathway:
                return candidate
        return None

    def summary(self) -> str:
        """One-paragraph plain-language summary."""
        best = self.recommended
        if best is None:
            return (
                f"{self.battery_label}: no viable pathway found. "
                + (self.warnings[0] if self.warnings else "")
            )
        return (
            f"{self.battery_label} ({self.rated_kwh:g} kWh, "
            f"{self.state_of_health * 100:.0f}% SoH): "
            f"{self.residual_value.format(0)} via {best.label.lower()} "
            f"({self.value_per_kwh.format(1)}/kWh), confidence {self.confidence:.0%}."
        )
