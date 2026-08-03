"""Recycling process parameters: what comes back out, and what gets paid for."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from ..errors import BatteryValueError
from .chemistry import ChemistrySpec, _load_json


class UnknownProcessError(BatteryValueError):
    """No recycling process matched the requested key."""


@dataclass(frozen=True, slots=True)
class ElementRecovery:
    """How much of one element survives a process, and how much of it is paid for."""

    element: str
    recovery_rate: float
    payable_fraction: float
    traded_form: str

    @property
    def value_yield(self) -> float:
        """Share of contained market value that reaches the holder's pocket.

        This is the number that matters commercially: a metal recovered at 95%
        but paid at 68% only returns 65% of its headline market value.
        """
        return self.recovery_rate * self.payable_fraction


@dataclass(frozen=True, slots=True)
class ProcessCosts:
    """Per-kg processing costs, before logistics."""

    discharge_and_dismantle: float
    shredding_to_black_mass: float
    refining_gate_fee: float

    @property
    def total_eur_per_kg(self) -> float:
        """All processing cost lines summed."""
        return (
            self.discharge_and_dismantle
            + self.shredding_to_black_mass
            + self.refining_gate_fee
        )


@dataclass(frozen=True, slots=True)
class RecyclingProcess:
    """A named recycling route with its recovery, payables and cost profile."""

    key: str
    label: str
    description: str
    applies_to_families: tuple[str, ...]
    elements: dict[str, ElementRecovery]
    costs: ProcessCosts
    maturity: str = "commercial"

    def supports(self, chemistry: ChemistrySpec) -> bool:
        """Whether this route can process the given chemistry's family."""
        return chemistry.family in self.applies_to_families

    def recovery_for(self, element: str) -> ElementRecovery:
        """Recovery terms for ``element``; unlisted elements recover nothing."""
        return self.elements.get(
            element,
            ElementRecovery(element, 0.0, 0.0, "steel_scrap"),
        )


@dataclass(frozen=True, slots=True)
class LogisticsModel:
    """Collection and dangerous-goods freight cost for end-of-life packs."""

    base_eur_per_kg: float
    condition_multiplier: dict[str, float]
    minimum_charge_eur: float
    notes: tuple[str, ...] = ()

    def cost_eur(self, mass_kg: float, condition: str = "healthy") -> float:
        """Freight cost for a pack of ``mass_kg`` in the given condition."""
        multiplier = self.condition_multiplier.get(condition, 1.0)
        return max(
            mass_kg * self.base_eur_per_kg * multiplier, self.minimum_charge_eur
        )


@dataclass(frozen=True, slots=True)
class SecondLifeParams:
    """Cost and eligibility parameters for stationary repurposing."""

    testing_eur_per_kwh: float
    repackaging_eur_per_kwh: float
    new_bms_eur_per_pack: float
    certification_eur_per_pack: float
    warranty_reserve_fraction: float
    minimum_viable_soh: float
    second_life_end_of_life_soh: float

    def repurposing_cost_eur(self, rated_kwh: float) -> float:
        """Total cost to convert a retired pack into a stationary product."""
        return (
            rated_kwh * (self.testing_eur_per_kwh + self.repackaging_eur_per_kwh)
            + self.new_bms_eur_per_pack
            + self.certification_eur_per_pack
        )


@dataclass(frozen=True, slots=True)
class ReuseParams:
    """Cost and eligibility parameters for resale as a replacement pack."""

    minimum_viable_soh: float
    maximum_age_years: float
    refurbishment_eur_per_kwh: float
    test_and_certify_eur_per_pack: float
    oem_replacement_price_discount: float
    warranty_reserve_fraction: float

    def refurbishment_cost_eur(self, rated_kwh: float) -> float:
        """Cost to make a pack saleable as a replacement unit."""
        return (
            rated_kwh * self.refurbishment_eur_per_kwh
            + self.test_and_certify_eur_per_pack
        )


@dataclass(frozen=True, slots=True)
class RecoveryLibrary:
    """The loaded recovery dataset."""

    updated: str
    processes: dict[str, RecyclingProcess]
    logistics: LogisticsModel
    second_life: SecondLifeParams
    reuse: ReuseParams
    sources: tuple[str, ...] = ()

    def get(self, key: str) -> RecyclingProcess:
        """Fetch a process by key."""
        try:
            return self.processes[key]
        except KeyError:
            raise UnknownProcessError(
                f"unknown recycling process {key!r}; "
                f"available: {', '.join(sorted(self.processes))}"
            ) from None

    def processes_for(self, chemistry: ChemistrySpec) -> list[RecyclingProcess]:
        """Every commercially available route that can take this chemistry.

        Pilot-stage routes are excluded: quoting a residual value against a
        process nobody can currently sell into would overstate what a holder
        can actually realise today.
        """
        return [
            process
            for process in self.processes.values()
            if process.supports(chemistry) and process.maturity == "commercial"
        ]


def _element_recovery(element: str, entry: dict[str, Any]) -> ElementRecovery:
    return ElementRecovery(
        element=element,
        recovery_rate=float(entry["recovery_rate"]),
        payable_fraction=float(entry["payable_fraction"]),
        traded_form=entry["traded_form"],
    )


@lru_cache(maxsize=1)
def load_recovery() -> RecoveryLibrary:
    """Load and cache the bundled recovery dataset."""
    raw = _load_json("recovery.json")

    processes: dict[str, RecyclingProcess] = {}
    for key, entry in raw["processes"].items():
        costs = entry["cost_eur_per_kg"]
        processes[key] = RecyclingProcess(
            key=key,
            label=entry["label"],
            description=entry.get("description", ""),
            applies_to_families=tuple(entry["applies_to_families"]),
            elements={
                element: _element_recovery(element, values)
                for element, values in entry["elements"].items()
            },
            costs=ProcessCosts(
                discharge_and_dismantle=float(costs["discharge_and_dismantle"]),
                shredding_to_black_mass=float(costs["shredding_to_black_mass"]),
                refining_gate_fee=float(costs["refining_gate_fee"]),
            ),
            maturity=entry.get("maturity", "commercial"),
        )

    logistics_raw = raw["logistics"]
    second_life_raw = raw["second_life"]
    reuse_raw = raw["reuse"]

    return RecoveryLibrary(
        updated=raw["updated"],
        processes=processes,
        logistics=LogisticsModel(
            base_eur_per_kg=float(logistics_raw["base_eur_per_kg"]),
            condition_multiplier=dict(logistics_raw["condition_multiplier"]),
            minimum_charge_eur=float(logistics_raw["minimum_charge_eur"]),
            notes=tuple(logistics_raw.get("notes", ())),
        ),
        second_life=SecondLifeParams(
            testing_eur_per_kwh=float(second_life_raw["testing_eur_per_kwh"]),
            repackaging_eur_per_kwh=float(second_life_raw["repackaging_eur_per_kwh"]),
            new_bms_eur_per_pack=float(second_life_raw["new_bms_eur_per_pack"]),
            certification_eur_per_pack=float(
                second_life_raw["certification_eur_per_pack"]
            ),
            warranty_reserve_fraction=float(
                second_life_raw["warranty_reserve_fraction"]
            ),
            minimum_viable_soh=float(second_life_raw["minimum_viable_soh"]),
            second_life_end_of_life_soh=float(
                second_life_raw["second_life_end_of_life_soh"]
            ),
        ),
        reuse=ReuseParams(
            minimum_viable_soh=float(reuse_raw["minimum_viable_soh"]),
            maximum_age_years=float(reuse_raw["maximum_age_years"]),
            refurbishment_eur_per_kwh=float(reuse_raw["refurbishment_eur_per_kwh"]),
            test_and_certify_eur_per_pack=float(
                reuse_raw["test_and_certify_eur_per_pack"]
            ),
            oem_replacement_price_discount=float(
                reuse_raw["oem_replacement_price_discount"]
            ),
            warranty_reserve_fraction=float(reuse_raw["warranty_reserve_fraction"]),
        ),
        sources=tuple(raw.get("sources", ())),
    )
