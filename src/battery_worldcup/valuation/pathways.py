"""The four things that can be done with a retired pack, each priced.

A pack does not have one residual value; it has four, and the holder realises
whichever route pays most. Each function here prices one route end to end and
reports why it is or is not available, so the answer explains itself rather
than just asserting a number.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..materials.bom import BillOfMaterials
from ..materials.chemistry import ChemistrySpec
from ..materials.recovery import RecoveryLibrary, RecyclingProcess
from ..market.fx import FxConverter
from ..market.providers.baseline import system_price
from ..market.types import PriceSet
from ..money import Money
from ..packs.models import PackModel
from ..passport.models import BatteryPassport
from .config import ValuationConfig
from .health import HealthAssessment
from .models import LineKind, Pathway, PathwayValuation, ValueLine


@dataclass(slots=True)
class PathwayContext:
    """Everything the pathway functions share."""

    passport: BatteryPassport
    health: HealthAssessment
    chemistry: ChemistrySpec
    bom: BillOfMaterials
    prices: PriceSet
    recovery: RecoveryLibrary
    config: ValuationConfig
    fx: FxConverter
    pack_model: PackModel | None = None

    @property
    def currency(self) -> str:
        """Currency every line is reported in."""
        return self.config.currency

    @property
    def rated_kwh(self) -> float:
        """Nameplate energy."""
        return self.health.rated_kwh

    @property
    def pack_mass_kg(self) -> float:
        """Pack mass in kg."""
        return self.bom.pack_mass_kg

    def money(self, amount: float) -> Money:
        """A :class:`Money` in the configured currency."""
        return Money(amount, self.currency)

    def convert(self, money: Money) -> Money:
        """Convert a :class:`Money` into the configured currency."""
        if money.currency == self.currency:
            return money
        try:
            return self.money(
                money.amount * self.fx.factor(money.currency, self.currency)
            )
        except Exception:  # noqa: BLE001 - never fail a valuation on FX
            return self.money(money.amount)

    def from_eur(self, amount: float) -> Money:
        """Convert a EUR-denominated figure from the datasets into the target currency.

        The recovery, logistics and second-life datasets are all quoted in EUR.
        Passing those numbers straight into :meth:`money` would silently relabel
        them as USD when a caller asks for USD, so they must come through here.
        """
        return self.convert(Money(amount, "EUR"))

    def system_reference(self, key: str) -> Money:
        """A whole-system reference price, converted to the target currency."""
        return self.convert(system_price(key))

    def logistics_cost(self) -> Money:
        """Dangerous-goods collection and freight for this pack."""
        return self.from_eur(
            self.recovery.logistics.cost_eur(
                self.pack_mass_kg, self.health.condition.value
            )
        )


def _ineligible(
    pathway: Pathway, currency: str, blockers: list[str]
) -> PathwayValuation:
    """A pathway the pack does not qualify for."""
    return PathwayValuation(
        pathway=pathway,
        eligible=False,
        confidence=0.0,
        blockers=blockers,
        currency=currency,
    )


# --------------------------------------------------------------------------
# Recycling
# --------------------------------------------------------------------------


def value_recycling(context: PathwayContext) -> PathwayValuation:
    """Price material recovery, choosing the best available process.

    This is the value floor: it is always available, and for LFP and sodium-ion
    it is routinely negative, meaning the holder pays a gate fee.
    """
    processes = [
        process
        for process in context.recovery.processes.values()
        if process.supports(context.chemistry)
        and (
            context.config.recycling.include_pilot_processes
            or process.maturity == "commercial"
        )
    ]

    forced = context.config.recycling.prefer_process
    if forced:
        processes = [process for process in processes if process.key == forced]

    if not processes:
        return _ineligible(
            Pathway.RECYCLING,
            context.currency,
            [
                f"no recycling process in the dataset accepts "
                f"{context.chemistry.family} chemistry"
            ],
        )

    evaluated = [_evaluate_recycling_process(context, process) for process in processes]
    return max(evaluated, key=lambda result: result.net_value.amount)


def _evaluate_recycling_process(
    context: PathwayContext, process: RecyclingProcess
) -> PathwayValuation:
    """Price one recycling route."""
    lines: list[ValueLine] = []
    assumptions = [f"process: {process.label}"]
    missing_prices: list[str] = []

    for line in context.bom.sorted_lines():
        recovery = process.recovery_for(line.element)
        if recovery.value_yield <= 0:
            continue

        quote = context.prices.get(recovery.traded_form)
        if quote is None:
            missing_prices.append(recovery.traded_form)
            continue

        contained_price = context.convert(quote.money_per_kg_contained())
        revenue = contained_price * line.mass_kg * recovery.value_yield
        if revenue.amount <= 0:
            continue

        lines.append(
            ValueLine(
                label=f"{line.element} recovery",
                amount=revenue,
                kind=LineKind.REVENUE,
                detail=(
                    f"{line.mass_kg:.1f} kg contained ({line.source}) x "
                    f"{contained_price.format(2)}/kg x "
                    f"{recovery.recovery_rate:.0%} recovered x "
                    f"{recovery.payable_fraction:.0%} payable"
                ),
            )
        )

    mass = context.pack_mass_kg
    costs = process.costs
    for label, rate in (
        ("Discharge and dismantling", costs.discharge_and_dismantle),
        ("Shredding to black mass", costs.shredding_to_black_mass),
        ("Refining gate fee", costs.refining_gate_fee),
    ):
        if rate > 0:
            lines.append(
                ValueLine(
                    label=label,
                    amount=context.from_eur(mass * rate),
                    kind=LineKind.COST,
                    detail=f"{mass:.0f} kg x {rate:.2f} EUR/kg",
                )
            )

    logistics = context.logistics_cost()
    lines.append(
        ValueLine(
            label="Collection and DG freight",
            amount=logistics,
            kind=LineKind.COST,
            detail=(
                f"{mass:.0f} kg as {context.health.condition.value} "
                "(UN3480/3481 Class 9)"
            ),
        )
    )

    if missing_prices:
        assumptions.append(
            "no price found for: " + ", ".join(sorted(set(missing_prices)))
        )

    # Recycling value depends on composition and metal prices, not on how tired
    # the cells are, so state-of-health confidence is deliberately not a factor.
    confidence = context.prices.confidence * (
        0.75 + 0.25 * context.bom.declared_fraction
    )

    return PathwayValuation(
        pathway=Pathway.RECYCLING,
        eligible=True,
        lines=lines,
        confidence=round(min(confidence, 1.0), 3),
        assumptions=assumptions,
        currency=context.currency,
    )


# --------------------------------------------------------------------------
# Reuse as a replacement pack
# --------------------------------------------------------------------------


def value_reuse(context: PathwayContext) -> PathwayValuation:
    """Price resale as a replacement traction pack.

    Usually the highest-value route for a healthy pack of a model that still
    has vehicles on the road, because it competes against the OEM's retail
    replacement price rather than against scrap metal.
    """
    config = context.config
    assumptions_config = config.reuse
    health = context.health
    blockers: list[str] = []

    if health.soh < assumptions_config.minimum_soh:
        blockers.append(
            f"state of health {health.soh:.0%} is below the "
            f"{assumptions_config.minimum_soh:.0%} floor for resale as a replacement pack"
        )
    if health.age_years and health.age_years > assumptions_config.maximum_age_years:
        blockers.append(
            f"pack is {health.age_years:.1f} years old, beyond the "
            f"{assumptions_config.maximum_age_years:.0f}-year resale window"
        )
    if not health.is_safe_for_reuse:
        blockers.append(
            f"condition '{health.condition.value}' or an open safety flag rules out live reuse"
        )
    if blockers:
        return _ineligible(Pathway.REUSE, context.currency, blockers)

    model = context.pack_model
    if model and model.oem_replacement_price_eur_per_kwh:
        reference = context.from_eur(model.oem_replacement_price_eur_per_kwh)
        reference_note = f"OEM replacement price for {model.label}"
    else:
        reference = context.from_eur(
            assumptions_config.fallback_oem_price_eur_per_kwh
        )
        reference_note = "generic OEM replacement price (pack model not identified)"

    health_factor = health.soh**assumptions_config.health_exponent
    age_factor = 1.0
    if health.age_years:
        age_factor = max(
            assumptions_config.minimum_age_factor,
            1.0 - health.age_years * assumptions_config.age_penalty_per_year,
        )
    demand_factor = model.demand_factor if model else 1.0

    gross = (
        reference
        * context.rated_kwh
        * assumptions_config.used_vs_new_discount
        * health_factor
        * age_factor
        * demand_factor
    )

    lines = [
        ValueLine(
            label="Resale as replacement pack",
            amount=gross,
            kind=LineKind.REVENUE,
            detail=(
                f"{context.rated_kwh:g} kWh x {reference.format(0)}/kWh x "
                f"{assumptions_config.used_vs_new_discount:.0%} used discount x "
                f"{health_factor:.2f} health x {age_factor:.2f} age x "
                f"{demand_factor:.2f} demand"
            ),
        )
    ]

    reuse_params = context.recovery.reuse
    refurbishment = context.from_eur(
        reuse_params.refurbishment_cost_eur(context.rated_kwh)
    )
    lines.append(
        ValueLine(
            label="Refurbishment, test and certification",
            amount=refurbishment,
            kind=LineKind.COST,
            detail=(
                f"{reuse_params.refurbishment_eur_per_kwh:.0f}/kWh + "
                f"{reuse_params.test_and_certify_eur_per_pack:.0f} per pack"
            ),
        )
    )
    lines.append(
        ValueLine(
            label="Warranty reserve",
            amount=gross * reuse_params.warranty_reserve_fraction,
            kind=LineKind.COST,
            detail=f"{reuse_params.warranty_reserve_fraction:.0%} of resale value",
        )
    )
    lines.append(
        ValueLine(
            label="Collection and DG freight",
            amount=context.logistics_cost(),
            kind=LineKind.COST,
            detail=f"{context.pack_mass_kg:.0f} kg",
        )
    )

    confidence = health.confidence * (model.confidence_factor if model else 0.55)

    assumptions = [
        reference_note,
        f"health factor = SoH^{assumptions_config.health_exponent}",
    ]
    if not model:
        assumptions.append(
            "pack model not identified, so a generic replacement price was used; "
            "identifying the model would materially tighten this estimate"
        )

    return PathwayValuation(
        pathway=Pathway.REUSE,
        eligible=True,
        lines=lines,
        confidence=round(min(confidence, 1.0), 3),
        assumptions=assumptions,
        currency=context.currency,
    )


# --------------------------------------------------------------------------
# Parts-out
# --------------------------------------------------------------------------


def value_parts_out(context: PathwayContext) -> PathwayValuation:
    """Price dismantling the pack and selling its components.

    Needs a known pack model: without a component list there is nothing to
    price. This is frequently the best route for older packs whose modules have
    a strong DIY and repair market even when the whole pack is unsellable.
    """
    model = context.pack_model
    config = context.config.parts_out
    health = context.health
    blockers: list[str] = []

    if model is None or not model.components:
        blockers.append(
            "pack model not identified, so no component breakdown is available; "
            "add the model to the catalogue or a pack-data layer to price this route"
        )
        return _ineligible(Pathway.PARTS_OUT, context.currency, blockers)

    if health.condition.value == "thermal_event":
        blockers.append("a pack that has had a thermal event cannot be parted out safely")
    if health.soh < config.minimum_soh:
        blockers.append(
            f"state of health {health.soh:.0%} is below the "
            f"{config.minimum_soh:.0%} floor for component resale"
        )
    if blockers:
        return _ineligible(Pathway.PARTS_OUT, context.currency, blockers)

    lines: list[ValueLine] = []
    demand_factor = model.demand_factor

    for component in model.components:
        if not component.reusable:
            continue
        value = component.value_at_soh(health.soh)
        if value <= 0:
            continue
        if component.key == "modules":
            value *= config.module_market_depth
            detail = (
                f"{component.count} modules x "
                f"{component.unit_value_eur:.0f} EUR x {health.soh:.0%} health x "
                f"{config.module_market_depth:.0%} sell-through"
            )
        else:
            detail = f"{component.count} x {component.unit_value_eur:.0f} EUR used"

        lines.append(
            ValueLine(
                label=component.label,
                amount=context.from_eur(value) * demand_factor,
                kind=LineKind.REVENUE,
                detail=detail,
            )
        )

    # Non-reusable parts still carry clean, sorted scrap value.
    scrap_total = 0.0
    scrap_detail: list[str] = []
    for component in model.components:
        if component.reusable or not component.dominant_material:
            continue
        form = _scrap_form_for(component.dominant_material)
        quote = context.prices.get(form)
        if quote is None:
            continue
        price = context.convert(quote.money_per_kg_contained())
        value = price.amount * component.total_mass_kg * config.scrap_payable_fraction
        scrap_total += value
        scrap_detail.append(
            f"{component.label} {component.total_mass_kg:.0f} kg {component.dominant_material}"
        )

    if scrap_total > 0:
        lines.append(
            ValueLine(
                label="Scrap value of non-reusable parts",
                amount=context.money(scrap_total),
                kind=LineKind.REVENUE,
                detail=(
                    "; ".join(scrap_detail)
                    + f" at {config.scrap_payable_fraction:.0%} payable"
                ),
            )
        )

    catalogue_labour = _labour_rate(context)
    total_minutes = sum(
        component.total_dismantling_minutes for component in model.components
    )
    total_minutes += catalogue_labour[1]
    labour_cost = context.from_eur(total_minutes / 60.0 * catalogue_labour[0])
    lines.append(
        ValueLine(
            label="HV dismantling labour",
            amount=labour_cost,
            kind=LineKind.COST,
            detail=(
                f"{total_minutes / 60:.1f} h at {catalogue_labour[0]:.0f} EUR/h "
                "by an HV-qualified technician"
            ),
        )
    )
    lines.append(
        ValueLine(
            label="Collection and DG freight",
            amount=context.logistics_cost(),
            kind=LineKind.COST,
            detail=f"{context.pack_mass_kg:.0f} kg",
        )
    )

    confidence = health.confidence * model.confidence_factor * 0.9

    return PathwayValuation(
        pathway=Pathway.PARTS_OUT,
        eligible=True,
        lines=lines,
        confidence=round(min(confidence, 1.0), 3),
        assumptions=[
            f"component breakdown for {model.label}",
            f"{config.module_market_depth:.0%} of modules assumed to find a buyer",
            "used-part values track the second-hand market and should be refreshed "
            "from live listings",
        ],
        currency=context.currency,
    )


def _scrap_form_for(element: str) -> str:
    """Traded form used to price clean scrap of an element."""
    return {
        "Al": "aluminium_metal",
        "Cu": "copper_metal",
        "Fe": "steel_scrap",
        "Pb": "lead_metal",
    }.get(element, "steel_scrap")


def _labour_rate(context: PathwayContext) -> tuple[float, float]:
    """``(rate per hour, fixed setup minutes)`` from the pack catalogue."""
    from ..packs.catalogue import load_catalogue

    catalogue = load_catalogue()
    return catalogue.labour_rate_eur_per_hour, catalogue.fixed_setup_minutes


# --------------------------------------------------------------------------
# Second life
# --------------------------------------------------------------------------


def value_second_life(context: PathwayContext) -> PathwayValuation:
    """Price repurposing into stationary storage.

    Valued on what the pack can still deliver relative to the new battery it
    would displace, rather than on a flat percentage of the new price.
    """
    config = context.config.second_life
    params = context.recovery.second_life
    health = context.health
    chemistry = context.chemistry
    blockers: list[str] = []

    minimum_soh = max(config.minimum_soh, params.minimum_viable_soh)
    if health.soh < minimum_soh:
        blockers.append(
            f"state of health {health.soh:.0%} is below the {minimum_soh:.0%} "
            "floor for repurposing"
        )
    if health.age_years and health.age_years > config.maximum_age_years:
        blockers.append(
            f"pack is {health.age_years:.1f} years old, beyond the "
            f"{config.maximum_age_years:.0f}-year repurposing window"
        )
    if health.condition.blocks_reuse:
        blockers.append(
            f"condition '{health.condition.value}' rules out repurposing"
        )
    if blockers:
        return _ineligible(Pathway.SECOND_LIFE, context.currency, blockers)

    usable_kwh = context.rated_kwh * health.soh * config.usable_dod_window

    remaining_cycles = health.remaining_cycles or 0.0
    life_ratio = min(1.0, remaining_cycles / config.new_system_cycle_life)
    if life_ratio <= 0:
        return _ineligible(
            Pathway.SECOND_LIFE,
            context.currency,
            ["no useful cycle life remains before the second-life end-of-life floor"],
        )

    reference_key = "lfp_pack_price" if chemistry.key == "LFP" else "new_pack_price"
    reference = context.system_reference(reference_key)
    demand_factor = context.pack_model.demand_factor if context.pack_model else 1.0

    gross = (
        reference
        * usable_kwh
        * life_ratio
        * chemistry.second_life_suitability
        * demand_factor
    )

    lines = [
        ValueLine(
            label="Repurposed storage capacity",
            amount=gross,
            kind=LineKind.REVENUE,
            detail=(
                f"{usable_kwh:.1f} usable kWh x {reference.format(0)}/kWh x "
                f"{life_ratio:.2f} remaining-life ratio x "
                f"{chemistry.second_life_suitability:.2f} chemistry suitability x "
                f"{demand_factor:.2f} demand"
            ),
        )
    ]

    repurposing = context.from_eur(params.repurposing_cost_eur(context.rated_kwh))
    lines.append(
        ValueLine(
            label="Repurposing: test, grade, rebuild, new BMS",
            amount=repurposing,
            kind=LineKind.COST,
            detail=(
                f"{params.testing_eur_per_kwh + params.repackaging_eur_per_kwh:.0f}/kWh "
                f"+ {params.new_bms_eur_per_pack + params.certification_eur_per_pack:.0f} per pack"
            ),
        )
    )
    lines.append(
        ValueLine(
            label="Warranty reserve",
            amount=gross * params.warranty_reserve_fraction,
            kind=LineKind.COST,
            detail=f"{params.warranty_reserve_fraction:.0%} of realised value",
        )
    )
    lines.append(
        ValueLine(
            label="Collection and DG freight",
            amount=context.logistics_cost(),
            kind=LineKind.COST,
            detail=f"{context.pack_mass_kg:.0f} kg",
        )
    )

    confidence = health.confidence * 0.8

    return PathwayValuation(
        pathway=Pathway.SECOND_LIFE,
        eligible=True,
        lines=lines,
        confidence=round(min(confidence, 1.0), 3),
        assumptions=[
            f"valued against {reference_key.replace('_', ' ')} of {reference.format(0)}/kWh",
            f"{remaining_cycles:.0f} equivalent full cycles remain to "
            f"{config.end_of_life_soh:.0%} SoH",
            f"depth-of-discharge window limited to {config.usable_dod_window:.0%}",
        ],
        currency=context.currency,
    )


ALL_PATHWAYS = (
    value_reuse,
    value_parts_out,
    value_second_life,
    value_recycling,
)
