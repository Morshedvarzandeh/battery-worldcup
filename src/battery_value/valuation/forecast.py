"""What the pack will be worth later, and what the warranty was hiding.

A leasing company does not need to know what a battery is worth today. They
committed to a number three years ago and they find out at contract end whether
they were right. That forward number is the whole business, and because nobody
could defend one, the industry has been setting battery residuals at or near
zero -- which makes leases more expensive than they need to be and hands the
upside to whoever buys the car at auction.

Two things this module says that a spot valuation cannot.

**The warranty is a put option, and it expires.** Under an 8-year/70% warranty
the holder's downside is capped: if the pack falls through the floor, the maker
replaces it. That protection is worth real money and it disappears on a known
date. The residual steps down at expiry not because anything happened to the
battery but because the guarantee stopped. Anyone pricing a post-warranty pack
against pre-warranty comparables is reading the wrong number.

**Uncertainty is priced separately from wear.** A buyer facing a pack whose
health is nobody's guess discounts it for the risk, not for the condition. That
discount is what a certificate removes, and it is the only assumption in here
that is not derived from the valuation itself -- so it is a named constant with
its reasoning attached rather than a factor buried in a formula.

The forecast re-values the pack at each horizon rather than extrapolating
today's price. Over four years a pack usually crosses the resale floor, and at
that point the best route disappears outright: a straight line through the cliff
would report a number that cannot happen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from math import erf, sqrt
from typing import TYPE_CHECKING

from ..money import Money
from ..passport.models import BatteryPassport

if TYPE_CHECKING:  # pragma: no cover
    from .engine import ValuationEngine

_DAYS_PER_YEAR = 365.2425

#: What a buyer knocks off a pack whose health nobody has measured. It prices
#: the risk of being wrong, not the battery -- an unverified pack that turns out
#: to be healthy was still worth discounting when the buyer could not tell.
#:
#: This is the one number here that is not derived from the valuation, and it is
#: the one to argue about. It is set from the gap between what certified and
#: uncertified used goods fetch in markets that have both; a battery-specific
#: figure needs the market this package is meant to create.
UNCERTAINTY_DISCOUNT = 0.30

#: The warranty most EV packs carry: eight years, to 70% of nameplate capacity.
#: Used only when the passport does not state its own.
DEFAULT_WARRANTY_YEARS = 8.0
DEFAULT_WARRANTY_FLOOR_SOH = 0.70


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    """What the pack is worth at one future date."""

    on: date
    age_years: float
    state_of_health: float
    value: Money
    low: Money
    high: Money
    under_warranty: bool

    @property
    def band(self) -> Money:
        """Width of the range. It widens with the horizon, as it should."""
        return self.high - self.low


@dataclass(slots=True)
class ResidualForecast:
    """A pack's value over time, and what the warranty is doing to it."""

    points: list[ForecastPoint] = field(default_factory=list)
    warranty_expires_on: date | None = None
    warranty_floor_soh: float = DEFAULT_WARRANTY_FLOOR_SOH
    currency: str = "EUR"

    warranty_value: Money | None = None
    """What the remaining guarantee is worth. See :func:`_warranty_option`."""

    warranty_claim_probability: float = 0.0

    @property
    def now(self) -> ForecastPoint | None:
        """Today's point."""
        return self.points[0] if self.points else None

    @property
    def at_end(self) -> ForecastPoint | None:
        """The last point on the horizon."""
        return self.points[-1] if self.points else None

    def at(self, years: float) -> ForecastPoint | None:
        """The point closest to ``years`` from now."""
        if not self.points:
            return None
        base = self.points[0].age_years
        return min(self.points, key=lambda p: abs((p.age_years - base) - years))

    @property
    def total_decline(self) -> Money:
        """Value lost across the whole horizon."""
        if not self.points:
            return Money.zero(self.currency)
        return self.points[0].value - self.points[-1].value

    def uncertainty_discount(self, point: ForecastPoint | None = None) -> Money:
        """What an unevidenced pack loses to doubt at a given point.

        This is the number a certificate is worth, and it is worth stating
        separately from the wear: it is not the battery being worse, it is the
        buyer being unable to tell.
        """
        chosen = point or self.at_end
        if chosen is None:
            return Money.zero(self.currency)
        return chosen.value * UNCERTAINTY_DISCOUNT

    def summary(self) -> str:
        """The forward number, in one line."""
        start, end = self.now, self.at_end
        if start is None or end is None:
            return "No forecast."

        horizon = end.age_years - start.age_years
        line = (
            f"Worth {start.value.format(0)} today and about "
            f"{end.value.format(0)} in {horizon:.0f} years "
            f"({end.low.format(0)} to {end.high.format(0)})."
        )
        if self.warranty_expires_on is not None:
            line += f" Warranty runs to {self.warranty_expires_on:%B %Y}"
            if self.warranty_value is not None and self.warranty_value.amount >= 1:
                line += (
                    f", and on this pack's trajectory it is worth about "
                    f"{self.warranty_value.format(0)} "
                    f"({self.warranty_claim_probability:.0%} chance of a claim)."
                )
            else:
                line += (
                    ", but this pack is not close enough to the floor for it to "
                    "be worth anything."
                )
        return line


def _normal_cdf(z: float) -> float:
    """Standard normal CDF, for the probability the pack falls through the floor."""
    return 0.5 * (1.0 + erf(z / sqrt(2.0)))


def _warranty_option(
    *,
    expected_soh_at_expiry: float,
    spread: float,
    floor: float,
    replacement_cost: Money,
) -> tuple[Money, float]:
    """What the remaining warranty is worth, and how likely a claim is.

    A capacity warranty is a put option. If the pack falls below the floor
    before expiry the maker replaces it, so the holder's downside is capped at
    a known date; after that they are naked. Its value is the chance of a claim
    times what a claim is worth.

    That chance comes from how far real packs of this model scatter at that age,
    which is a number the wear curve already carries. The result is the useful
    part: on a healthy pack the warranty is worth almost nothing, and on a
    marginal one it is worth more than the battery. Those two are priced
    identically today, which is the mistake.

    A normal approximation. The tail is not exactly Gaussian, but the input is a
    cohort standard deviation and pretending to more shape than that would be
    invented precision.
    """
    if spread <= 0 or replacement_cost.amount <= 0:
        return Money.zero(replacement_cost.currency), 0.0
    probability = _normal_cdf((floor - expected_soh_at_expiry) / spread)
    return replacement_cost * probability, round(probability, 4)


def _replacement_cost(passport: BatteryPassport, valuation) -> Money:
    """What the maker would be on the hook for, if a claim landed."""
    currency = valuation.currency
    rated = passport.rated_kwh or 0.0
    model = valuation.pack_model
    per_kwh = (
        model.oem_replacement_price_eur_per_kwh
        if model and model.oem_replacement_price_eur_per_kwh
        else 480.0
    )
    return Money(rated * per_kwh, currency)


def _warranty_expiry(passport: BatteryPassport) -> date | None:
    """When the manufacturer stops carrying the downside."""
    start = passport.identity.manufacturing_date or passport.identity.placed_on_market_date
    if start is None:
        return None
    years = passport.technical.warranty_years or DEFAULT_WARRANTY_YEARS
    return start + timedelta(days=years * _DAYS_PER_YEAR)


def _passport_at(
    passport: BatteryPassport, soh: float, cycles: int | None
) -> BatteryPassport:
    """The same battery, as it will look at a future health.

    A copy rather than a mutation: the caller's passport is theirs, and a
    forecast that quietly aged it would corrupt every later valuation.
    """
    future = passport.model_copy(deep=True)
    future.health.state_of_health_pct = round(soh * 100.0, 2)
    future.health.remaining_capacity_kwh = None
    if cycles is not None:
        future.health.cycle_count = cycles
    return future


def build(
    passport: BatteryPassport,
    engine: ValuationEngine,
    *,
    years: float = 5.0,
    step_years: float = 1.0,
    as_of: date | None = None,
    climate: str = "temperate",
) -> ResidualForecast:
    """Value a pack at intervals across a horizon.

    Args:
        passport: The battery, as it is today.
        engine: A configured valuation engine.
        years: How far ahead to look.
        step_years: Interval between points.
        as_of: Today, for testing.
        climate: Where the pack lives, which drives the fade curve.

    Returns:
        A forecast with a band at every point, widening with the horizon.

    The pack is **re-valued** at each step rather than extrapolated. Over a few
    years most packs cross the resale floor, where the best route disappears
    outright; a straight line through that cliff reports a number that cannot
    happen.
    """
    today = as_of or date.today()
    expiry = _warranty_expiry(passport)
    currency = engine.config.currency

    # One valuation today, to get the wear curve this pack is actually on.
    base = engine.value(passport, as_of=today, climate=climate)
    aging = base.aging
    trajectory = {round(point.age_years, 1): point for point in (aging.trajectory if aging else ())}
    spread = ((aging.spread_points or 0.0) / 100.0) if aging else 0.0
    age_now = (aging.age_years if aging and aging.age_years else 0.0)

    cycles_per_year = None
    if passport.health.cycle_count and age_now > 0:
        cycles_per_year = passport.health.cycle_count / age_now

    points: list[ForecastPoint] = []
    step = 0.0
    while step <= years + 1e-9:
        when = today + timedelta(days=step * _DAYS_PER_YEAR)
        age = age_now + step

        if step == 0:
            soh = base.state_of_health
        else:
            projected = trajectory.get(round(age, 1))
            soh = projected.projected_soh if projected else max(
                0.0, base.state_of_health - 0.02 * step
            )

        cycles = (
            int(cycles_per_year * age) if cycles_per_year is not None else None
        )

        def value_at(health: float) -> Money:
            if health >= base.state_of_health - 1e-9 and step == 0:
                return base.residual_value
            future = engine.value(
                _passport_at(passport, health, cycles), as_of=when, climate=climate
            )
            return future.residual_value

        # The band comes from how far real packs of this model scatter at this
        # age, which widens as the cohort diverges. Anything narrower would be
        # false precision at four years out.
        widening = spread * (1.0 + step * 0.35)
        low_soh = max(0.0, soh - widening)
        high_soh = min(1.0, soh + widening)

        points.append(
            ForecastPoint(
                on=when,
                age_years=round(age, 2),
                state_of_health=round(soh, 4),
                value=value_at(soh),
                low=value_at(low_soh),
                high=value_at(high_soh),
                under_warranty=expiry is not None and when < expiry,
            )
        )
        step += step_years

    # What the remaining guarantee is worth, if there is any left.
    warranty_value, claim_probability = Money.zero(currency), 0.0
    if expiry is not None and expiry > today:
        years_left = (expiry - today).days / _DAYS_PER_YEAR
        at_expiry = next(
            (
                point
                for point in points
                if point.age_years >= age_now + years_left
            ),
            points[-1],
        )
        warranty_value, claim_probability = _warranty_option(
            expected_soh_at_expiry=at_expiry.state_of_health,
            # The spread widens over the years to expiry, the same way the
            # forecast band does.
            spread=spread * (1.0 + years_left * 0.35) if spread else 0.0,
            floor=DEFAULT_WARRANTY_FLOOR_SOH,
            replacement_cost=_replacement_cost(passport, base),
        )

    return ResidualForecast(
        points=points,
        warranty_expires_on=expiry,
        warranty_floor_soh=DEFAULT_WARRANTY_FLOOR_SOH,
        currency=currency,
        warranty_value=warranty_value,
        warranty_claim_probability=claim_probability,
    )


def to_dict(forecast: ResidualForecast) -> dict:
    """Serialise for the API and the fleet view."""
    return {
        "currency": forecast.currency,
        "summary": forecast.summary(),
        "warranty_expires_on": (
            forecast.warranty_expires_on.isoformat()
            if forecast.warranty_expires_on
            else None
        ),
        "warranty_floor_soh": forecast.warranty_floor_soh,
        "uncertainty_discount_fraction": UNCERTAINTY_DISCOUNT,
        "uncertainty_discount": round(forecast.uncertainty_discount().amount, 2),
        "total_decline": round(forecast.total_decline.amount, 2),
        "warranty_value": (
            round(forecast.warranty_value.amount, 2)
            if forecast.warranty_value
            else 0.0
        ),
        "warranty_claim_probability": forecast.warranty_claim_probability,
        "points": [
            {
                "on": point.on.isoformat(),
                "age_years": point.age_years,
                "state_of_health": point.state_of_health,
                "value": round(point.value.amount, 2),
                "low": round(point.low.amount, 2),
                "high": round(point.high.amount, 2),
                "under_warranty": point.under_warranty,
            }
            for point in forecast.points
        ],
    }


__all__ = [
    "DEFAULT_WARRANTY_FLOOR_SOH",
    "DEFAULT_WARRANTY_YEARS",
    "UNCERTAINTY_DISCOUNT",
    "ForecastPoint",
    "ResidualForecast",
    "build",
    "to_dict",
]
