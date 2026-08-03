"""Turn a passport into a bill of materials: how many kg of what is in the pack."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .chemistry import ChemistrySpec

MassSource = Literal["declared", "modelled"]

# Structural metals scale with how heavy the pack is (enclosure, busbars, module
# frames). Active materials scale with how much energy it stores, because that
# is what fixes the amount of cathode and anode. Applying one blanket scale
# factor to both is a classic way to mis-state cobalt content on an unusually
# heavy or light pack.
STRUCTURAL_ELEMENTS: frozenset[str] = frozenset({"Cu", "Al", "Fe"})

# Guard rails on the mass-based correction. Beyond these the passport's mass and
# its chemistry disagree so badly that extrapolating would be guesswork.
_MIN_MASS_SCALE = 0.70
_MAX_MASS_SCALE = 1.40


@dataclass(frozen=True, slots=True)
class MaterialLine:
    """One element's contained mass in the pack."""

    element: str
    mass_kg: float
    source: MassSource
    basis: str

    @property
    def is_declared(self) -> bool:
        """True when this came from the passport rather than a model default."""
        return self.source == "declared"


@dataclass(frozen=True, slots=True)
class BillOfMaterials:
    """Contained mass of every payable element, plus the inert remainder."""

    chemistry: ChemistrySpec
    rated_kwh: float
    pack_mass_kg: float
    lines: dict[str, MaterialLine] = field(default_factory=dict)
    inert_mass_kg: float = 0.0
    warnings: tuple[str, ...] = ()
    mass_scale_applied: float = 1.0

    def mass_of(self, element: str) -> float:
        """Contained kg of ``element``, or 0.0 if absent."""
        line = self.lines.get(element)
        return line.mass_kg if line else 0.0

    @property
    def payable_mass_kg(self) -> float:
        """Total mass across all payable elements."""
        return sum(line.mass_kg for line in self.lines.values())

    @property
    def declared_fraction(self) -> float:
        """Share of payable mass that the passport actually declared.

        Drives the confidence score: a passport that declares its cobalt and
        nickel supports a much tighter valuation than one we model from a
        chemistry label alone.
        """
        total = self.payable_mass_kg
        if total <= 0:
            return 0.0
        declared = sum(
            line.mass_kg for line in self.lines.values() if line.is_declared
        )
        return declared / total

    def sorted_lines(self) -> list[MaterialLine]:
        """Lines ordered heaviest first."""
        return sorted(self.lines.values(), key=lambda line: line.mass_kg, reverse=True)


def build_bom(
    *,
    chemistry: ChemistrySpec,
    rated_kwh: float,
    pack_mass_kg: float | None = None,
    declared_masses_kg: dict[str, float] | None = None,
) -> BillOfMaterials:
    """Build the pack's bill of materials.

    Declared passport values always win. Anything the passport is silent about
    is modelled from the chemistry's default intensity, corrected for pack mass
    where the element is structural.

    Args:
        chemistry: Resolved cell chemistry.
        rated_kwh: Nameplate energy of the pack in kWh.
        pack_mass_kg: Declared pack mass. Estimated from the chemistry when absent.
        declared_masses_kg: Element -> contained kg, as declared in the passport.
    """
    if rated_kwh <= 0:
        raise ValueError("rated_kwh must be positive")

    warnings: list[str] = []
    declared = {
        element: mass
        for element, mass in (declared_masses_kg or {}).items()
        if mass is not None and mass > 0
    }

    expected_mass_kg = chemistry.typical_pack_kg_per_kwh * rated_kwh
    if pack_mass_kg is None or pack_mass_kg <= 0:
        pack_mass_kg = expected_mass_kg
        warnings.append(
            f"pack mass not declared; estimated {pack_mass_kg:.0f} kg from "
            f"{chemistry.typical_pack_kg_per_kwh:.1f} kg/kWh for {chemistry.key}"
        )

    raw_scale = pack_mass_kg / expected_mass_kg if expected_mass_kg > 0 else 1.0
    mass_scale = min(max(raw_scale, _MIN_MASS_SCALE), _MAX_MASS_SCALE)
    if abs(raw_scale - mass_scale) > 1e-9:
        warnings.append(
            f"declared pack mass is {raw_scale:.2f}x the {chemistry.key} model "
            f"expectation; structural-metal scaling clamped to {mass_scale:.2f}x"
        )

    lines: dict[str, MaterialLine] = {}
    for element, intensity in chemistry.material_intensity_kg_per_kwh.items():
        if element in declared:
            lines[element] = MaterialLine(
                element=element,
                mass_kg=declared[element],
                source="declared",
                basis="declared in battery passport",
            )
            continue

        modelled = intensity * rated_kwh
        if element in STRUCTURAL_ELEMENTS:
            modelled *= mass_scale
            basis = (
                f"{intensity:.3f} kg/kWh default for {chemistry.key}, "
                f"scaled {mass_scale:.2f}x for declared pack mass"
            )
        else:
            basis = f"{intensity:.3f} kg/kWh default for {chemistry.key}"

        lines[element] = MaterialLine(
            element=element, mass_kg=modelled, source="modelled", basis=basis
        )

    # Elements the passport declares that the chemistry model does not expect
    # (a lead counterweight, a copper-rich harness) still carry value.
    for element, mass in declared.items():
        if element not in lines:
            lines[element] = MaterialLine(
                element=element,
                mass_kg=mass,
                source="declared",
                basis="declared in battery passport, not modelled for this chemistry",
            )

    payable = sum(line.mass_kg for line in lines.values())
    inert = pack_mass_kg - payable
    if inert < 0:
        warnings.append(
            f"declared material masses ({payable:.0f} kg) exceed declared pack mass "
            f"({pack_mass_kg:.0f} kg); treating inert mass as zero"
        )
        inert = 0.0

    return BillOfMaterials(
        chemistry=chemistry,
        rated_kwh=rated_kwh,
        pack_mass_kg=pack_mass_kg,
        lines=lines,
        inert_mass_kg=inert,
        warnings=tuple(warnings),
        mass_scale_applied=mass_scale,
    )
