"""Pack model and component types."""

from __future__ import annotations

from dataclasses import dataclass, field

# Used-module value tracks state of health, but not linearly: a module at 70%
# SoH is worth clearly more than 70% of a fresh one because buyers price the
# remaining usable energy plus the hardware itself.
_MODULE_SOH_FLOOR = 0.35


@dataclass(frozen=True, slots=True)
class PackComponent:
    """One serviceable part of a pack."""

    key: str
    label: str
    count: int
    unit_mass_kg: float
    reusable: bool
    dominant_material: str | None = None
    unit_value_eur: float = 0.0
    dismantling_minutes_each: float = 0.0
    note: str = ""

    @property
    def total_mass_kg(self) -> float:
        """Combined mass of every unit of this component."""
        return self.unit_mass_kg * self.count

    @property
    def total_value_eur(self) -> float:
        """Combined used-market value, before any health adjustment."""
        return self.unit_value_eur * self.count if self.reusable else 0.0

    @property
    def total_dismantling_minutes(self) -> float:
        """Technician time to remove every unit of this component."""
        return self.dismantling_minutes_each * self.count

    def value_at_soh(self, soh: float) -> float:
        """Used-market value adjusted for state of health.

        Only energy-storing components care about health; a contactor box or a
        BMS is worth the same whether the cells behind it are tired or not.
        """
        if not self.reusable:
            return 0.0
        if self.key != "modules":
            return self.total_value_eur
        factor = max(_MODULE_SOH_FLOOR, min(1.0, soh))
        return self.total_value_eur * factor


@dataclass(frozen=True, slots=True)
class PackModel:
    """A known battery pack model and everything the valuation can use from it."""

    key: str
    label: str
    manufacturer: str
    chemistry: str
    rated_kwh: float
    pack_mass_kg: float
    vehicle_models: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    years: tuple[int, int] | None = None
    module_count: int | None = None
    cell_count: int | None = None
    nominal_voltage_v: float | None = None
    cell_format: str | None = None
    used_module_value_eur: float = 0.0
    oem_replacement_price_eur_per_kwh: float | None = None
    second_life_demand: str = "medium"
    confidence: str = "medium"
    notes: str = ""
    components: tuple[PackComponent, ...] = ()
    source: str = "bundled"

    @property
    def kg_per_kwh(self) -> float:
        """Pack mass per kWh of nameplate energy."""
        return self.pack_mass_kg / self.rated_kwh if self.rated_kwh else 0.0

    @property
    def reusable_components(self) -> tuple[PackComponent, ...]:
        """Components with a used-parts market."""
        return tuple(component for component in self.components if component.reusable)

    def component(self, key: str) -> PackComponent | None:
        """Look up a component by key."""
        for component in self.components:
            if component.key == key:
                return component
        return None

    @property
    def demand_factor(self) -> float:
        """Multiplier on reuse value reflecting how sought-after the model is."""
        return {"high": 1.15, "medium": 1.0, "low": 0.8}.get(
            self.second_life_demand, 1.0
        )

    @property
    def confidence_factor(self) -> float:
        """How much to trust this entry's figures, 0-1."""
        return {"high": 0.95, "medium": 0.8, "low": 0.6}.get(self.confidence, 0.8)


@dataclass(frozen=True, slots=True)
class PackMatch:
    """A catalogue hit, with why it matched."""

    model: PackModel
    score: float
    matched_on: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_confident(self) -> bool:
        """Whether the match is strong enough to enrich a passport from."""
        return self.score >= 0.75

    def describe(self) -> str:
        """One-line explanation for the provenance trail."""
        reasons = ", ".join(self.matched_on) if self.matched_on else "heuristic"
        return f"{self.model.label} (score {self.score:.2f} on {reasons})"
