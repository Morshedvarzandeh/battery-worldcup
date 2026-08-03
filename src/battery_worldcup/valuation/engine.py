"""The valuation entry point: scan in, residual value out."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone

from ..errors import ValuationError
from ..materials.bom import build_bom
from ..materials.chemistry import ChemistrySpec, try_resolve_chemistry
from ..materials.recovery import load_recovery
from ..market.resolver import PriceResolver, build_resolver
from ..money import Money
from ..packs.enrichment import EnrichmentResult, enrich_passport
from ..packs.providers import PackResolver, build_pack_resolver
from ..passport.models import BatteryPassport
from ..passport.resolver import PassportResolver
from .config import ValuationConfig
from .health import assess_health
from .models import (
    Pathway,
    PathwayValuation,
    ResidualValuation,
    SensitivityFactor,
    ValuationRange,
)
from .pathways import ALL_PATHWAYS, PathwayContext

logger = logging.getLogger(__name__)

# Traded forms every valuation needs quoted, beyond those the chemistry implies.
_ALWAYS_PRICE = ("aluminium_metal", "copper_metal", "steel_scrap")


@dataclass
class ValuationEngine:
    """Values a battery pack from its passport.

    Args:
        config: Valuation assumptions.
        prices: Price resolver. Built with defaults when omitted.
        packs: Pack-data resolver. Built with defaults when omitted.
        passports: Passport resolver, used by :meth:`value_scan`.
    """

    config: ValuationConfig = field(default_factory=ValuationConfig)
    prices: PriceResolver | None = None
    packs: PackResolver | None = None
    passports: PassportResolver | None = None

    def __post_init__(self) -> None:
        if self.prices is None:
            self.prices = build_resolver(currency=self.config.currency)
        if self.packs is None:
            self.packs = build_pack_resolver()
        if self.passports is None:
            self.passports = PassportResolver()

    def value_scan(self, payload: str, *, as_of: date | None = None) -> ResidualValuation:
        """Value a pack straight from a scanned QR payload."""
        passport = self.passports.from_qr(payload)
        return self.value(passport, as_of=as_of)

    def value(
        self, passport: BatteryPassport, *, as_of: date | None = None
    ) -> ResidualValuation:
        """Value a pack from its passport.

        Raises:
            ValuationError: If the passport lacks the fields needed even after
                enrichment from the pack catalogue.
        """
        today = as_of or date.today()
        warnings: list[str] = []
        provenance: list[str] = []

        enrichment = self._enrich(passport, provenance)
        pack_model = enrichment.pack_model

        chemistry = self._resolve_chemistry(passport, pack_model, warnings)
        rated_kwh = passport.rated_kwh
        if not rated_kwh or rated_kwh <= 0:
            raise ValuationError(
                "cannot value this pack: nameplate energy is unknown and could not "
                "be recovered from the pack catalogue. Supply rated_capacity_kwh, or "
                "an Ah rating plus nominal voltage."
            )

        health = assess_health(passport, chemistry, self.config, as_of=today)
        warnings.extend(health.concerns)

        bom = build_bom(
            chemistry=chemistry,
            rated_kwh=rated_kwh,
            pack_mass_kg=passport.technical.pack_mass_kg
            or (pack_model.pack_mass_kg if pack_model else None),
            declared_masses_kg=passport.declared_masses(),
        )
        warnings.extend(bom.warnings)

        price_set = self._resolve_prices(bom, pack_model)
        provenance.extend(price_set.provenance_lines())
        if price_set.missing:
            warnings.append(
                "no market price available for: " + ", ".join(price_set.missing)
            )
        stale = price_set.stale_forms()
        if stale:
            warnings.append(
                f"{len(stale)} price(s) are more than 45 days old; wire up a live "
                "provider before quoting this commercially (see docs/market-data.md)"
            )

        context = PathwayContext(
            passport=passport,
            health=health,
            chemistry=chemistry,
            bom=bom,
            prices=price_set,
            recovery=load_recovery(),
            config=self.config,
            fx=self.prices.fx,
            pack_model=pack_model,
        )

        pathways = [evaluate(context) for evaluate in ALL_PATHWAYS]

        valuation = ResidualValuation(
            battery_label=self._label(passport, pack_model),
            rated_kwh=rated_kwh,
            state_of_health=health.soh,
            pathways=pathways,
            prices=price_set,
            bom=bom,
            currency=self.config.currency,
            pack_model=pack_model,
            warnings=warnings,
            provenance=provenance,
            generated_at=datetime.now(timezone.utc),
        )

        valuation.sensitivity = self._sensitivity(context, valuation)
        valuation.value_range = self._value_range(valuation)

        if valuation.confidence < self.config.minimum_confidence_to_quote:
            valuation.warnings.insert(
                0,
                f"confidence is {valuation.confidence:.0%}, below the "
                f"{self.config.minimum_confidence_to_quote:.0%} threshold: treat this "
                "as indicative only",
            )

        return valuation

    # -- internals ---------------------------------------------------------

    def _enrich(
        self, passport: BatteryPassport, provenance: list[str]
    ) -> EnrichmentResult:
        match = self.packs.find(passport) if self.packs else None
        enrichment = enrich_passport(passport, match)
        provenance.extend(enrichment.provenance_lines())
        return enrichment

    @staticmethod
    def _resolve_chemistry(
        passport: BatteryPassport, pack_model, warnings: list[str]
    ) -> ChemistrySpec:
        chemistry = passport.technical.chemistry
        if chemistry is not None:
            return chemistry

        if pack_model:
            resolved = try_resolve_chemistry(pack_model.chemistry)
            if resolved:
                warnings.append(
                    f"chemistry not declared; using {resolved.key} from the "
                    f"catalogue entry for {pack_model.label}"
                )
                return resolved

        raise ValuationError(
            "cannot value this pack: the cell chemistry is unknown and the pack "
            "model was not recognised. Declare the chemistry in the passport, or "
            "add the model to a pack-data layer."
        )

    def _resolve_prices(self, bom, pack_model):
        recovery = load_recovery()
        forms: set[str] = set(_ALWAYS_PRICE)
        for process in recovery.processes.values():
            if not process.supports(bom.chemistry):
                continue
            for element in bom.lines:
                entry = process.elements.get(element)
                if entry and entry.recovery_rate > 0:
                    forms.add(entry.traded_form)
        if pack_model:
            for component in pack_model.components:
                if component.dominant_material:
                    forms.add(_scrap_form(component.dominant_material))
        return self.prices.resolve_many(sorted(forms))

    @staticmethod
    def _label(passport: BatteryPassport, pack_model) -> str:
        if pack_model:
            return pack_model.label
        return passport.identity.display_name

    def _sensitivity(
        self, context: PathwayContext, valuation: ResidualValuation
    ) -> list[SensitivityFactor]:
        """Re-run the valuation under shocked inputs to size the uncertainty."""
        factors: list[SensitivityFactor] = []
        baseline = valuation.residual_value

        shock = self.config.sensitivity_price_shock
        low_prices = _rerun_with_price_shock(context, 1.0 - shock)
        high_prices = _rerun_with_price_shock(context, 1.0 + shock)
        factors.append(
            SensitivityFactor(
                name=f"Material prices {shock:+.0%}",
                low=low_prices,
                high=high_prices,
                swing=high_prices - low_prices,
            )
        )

        soh_shock = self.config.sensitivity_soh_shock
        low_health = _rerun_with_soh(context, -soh_shock)
        high_health = _rerun_with_soh(context, soh_shock)
        factors.append(
            SensitivityFactor(
                name=f"State of health {soh_shock * 100:+.0f} points",
                low=low_health,
                high=high_health,
                swing=high_health - low_health,
            )
        )

        factors.sort(key=lambda factor: abs(factor.swing.amount), reverse=True)
        logger.debug("sensitivity baseline %s", baseline)
        return factors

    def _value_range(self, valuation: ResidualValuation) -> ValuationRange:
        """Combine the sensitivity factors into one low/high band."""
        expected = valuation.residual_value
        if not valuation.sensitivity:
            return ValuationRange(low=expected, expected=expected, high=expected)

        lows = [factor.low.amount for factor in valuation.sensitivity]
        highs = [factor.high.amount for factor in valuation.sensitivity]
        dominant = valuation.sensitivity[0]

        return ValuationRange(
            low=Money(min(lows), self.config.currency),
            expected=expected,
            high=Money(max(highs), self.config.currency),
            driver=dominant.name,
        )


def _scrap_form(element: str) -> str:
    return {
        "Al": "aluminium_metal",
        "Cu": "copper_metal",
        "Fe": "steel_scrap",
        "Pb": "lead_metal",
    }.get(element, "steel_scrap")


def _best_net_value(context: PathwayContext) -> Money:
    """Highest net value across eligible pathways under the given context."""
    values = [
        result.net_value
        for result in (evaluate(context) for evaluate in ALL_PATHWAYS)
        if result.eligible
    ]
    return max(values, key=lambda money: money.amount) if values else Money.zero(
        context.currency
    )


def _rerun_with_price_shock(context: PathwayContext, factor: float) -> Money:
    """Re-value with every material price scaled by ``factor``."""
    shocked_quotes = {
        form: replace(quote, price=quote.price * factor)
        for form, quote in context.prices.quotes.items()
    }
    shocked = replace(context.prices, quotes=shocked_quotes)
    return _best_net_value(replace(context, prices=shocked))


def _rerun_with_soh(context: PathwayContext, delta: float) -> Money:
    """Re-value with state of health moved by ``delta`` fraction points."""
    health = context.health
    shocked_soh = min(max(health.soh + delta, 0.0), 1.0)

    shocked_health = replace(
        health,
        soh=shocked_soh,
        remaining_kwh=health.rated_kwh * shocked_soh,
        remaining_cycles=(
            max(0.0, health.remaining_cycles + delta / (0.20 / health.cycle_life))
            if health.remaining_cycles is not None and health.cycle_life
            else health.remaining_cycles
        ),
    )
    return _best_net_value(replace(context, health=shocked_health))


def value_passport(
    passport: BatteryPassport,
    *,
    config: ValuationConfig | None = None,
    offline: bool = False,
) -> ResidualValuation:
    """Convenience wrapper: value one passport with default wiring."""
    settings = config or ValuationConfig()
    engine = ValuationEngine(
        config=settings,
        prices=build_resolver(currency=settings.currency, offline=offline),
    )
    return engine.value(passport)


__all__ = [
    "Pathway",
    "PathwayValuation",
    "ResidualValuation",
    "ValuationEngine",
    "value_passport",
]
