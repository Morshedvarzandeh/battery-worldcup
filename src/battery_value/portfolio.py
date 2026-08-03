"""What a stack of batteries is worth, and what waiting costs.

Nobody with a thousand retired packs cares about state of health. They care
about three numbers: what the pile is worth, how fast that is falling, and which
ones to move first. Those numbers have never existed, so batteries sit in a
warehouse depreciating quietly while everyone waits for a better price that is
not coming.

The decay rate is the whole argument. A battery is a **wasting asset with a
knowable half-life**: it loses capacity on a curve this package can already
draw, and value follows. Put a number on the monthly loss and holding stops
being free, which is what makes the trade happen. No mandate required.

The cliff matters more than the slope. Value does not slide smoothly to zero --
when a pack drops below the health a buyer will fit to a vehicle, the reuse
route disappears outright and the price steps down. Packs approaching that line
are worth moving now, and packs well clear of it are not urgent. That single
distinction is worth more to a fleet manager than every other figure here.

Everything is derived from what is already in the stored valuations, so a
portfolio costs one database read rather than a thousand revaluations.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from .money import Money
from .store import StoredValuation

#: Packs crossing the resale floor within this many years are the ones to move.
URGENT_HORIZON_YEARS = 2.0

#: The sensitivity factor whose swing gives the local value-per-health-point.
_HEALTH_FACTOR_PREFIX = "State of health"

#: Width of that shock, in health points. Matches ValuationConfig's default of
#: 0.05 either way, so the swing spans ten points.
_HEALTH_SHOCK_POINTS = 10.0


@dataclass(frozen=True, slots=True)
class Holding:
    """One battery in a portfolio, with what it is doing to the balance sheet."""

    reference: str
    label: str
    pack_model_key: str | None
    chemistry: str
    rated_kwh: float
    state_of_health: float
    value: float
    currency: str
    confidence: float
    valued_on: date
    annual_loss: float
    """Value expected to evaporate over the next year, in ``currency``."""

    years_to_resale_floor: float | None
    already_below_resale_floor: bool
    wear_verdict: str

    @property
    def monthly_loss(self) -> float:
        """What holding this costs per month."""
        return self.annual_loss / 12.0

    @property
    def is_urgent(self) -> bool:
        """Whether it is about to fall out of the resale market.

        Not a gradient: below the floor the highest-value route disappears
        outright, so this is a cliff and the packs near it are the ones where
        waiting is genuinely expensive.
        """
        return (
            self.years_to_resale_floor is not None
            and self.years_to_resale_floor <= URGENT_HORIZON_YEARS
        )

    @property
    def loss_rate(self) -> float:
        """Annual loss as a share of current value, for ranking unlike packs."""
        return self.annual_loss / self.value if self.value > 0 else 0.0


@dataclass(frozen=True, slots=True)
class Group:
    """A slice of the portfolio -- one model, one chemistry, one site."""

    key: str
    label: str
    count: int
    value: float
    energy_kwh: float
    annual_loss: float
    currency: str

    @property
    def value_per_kwh(self) -> float:
        """What this slice is worth per kWh of nameplate energy."""
        return self.value / self.energy_kwh if self.energy_kwh else 0.0


@dataclass(slots=True)
class Portfolio:
    """A stack of batteries, priced and dated."""

    holdings: list[Holding] = field(default_factory=list)
    currency: str = "EUR"

    # -- the three numbers --------------------------------------------------

    @property
    def value(self) -> Money:
        """What the whole stack is worth today."""
        return Money(sum(holding.value for holding in self.holdings), self.currency)

    @property
    def annual_loss(self) -> Money:
        """What it will be worth less in a year, if nothing is done."""
        return Money(
            sum(holding.annual_loss for holding in self.holdings), self.currency
        )

    @property
    def monthly_loss(self) -> Money:
        """The same, per month. The number that ends the conversation."""
        return self.annual_loss / 12.0

    @property
    def urgent(self) -> list[Holding]:
        """Packs about to fall out of the resale market, worst first."""
        return sorted(
            (holding for holding in self.holdings if holding.is_urgent),
            key=lambda holding: holding.years_to_resale_floor or 0.0,
        )

    @property
    def value_at_risk(self) -> Money:
        """Value sitting in packs that are about to hit the cliff."""
        return Money(sum(holding.value for holding in self.urgent), self.currency)

    @property
    def stranded(self) -> list[Holding]:
        """Packs already past the resale floor. The cliff is behind them."""
        return [
            holding for holding in self.holdings if holding.already_below_resale_floor
        ]

    @property
    def liabilities(self) -> list[Holding]:
        """Packs that cost money to get rid of.

        A negative holding is not a rounding error, it is a provision. LFP and
        damaged packs cost more to handle than their materials are worth, and a
        portfolio that nets them off against good stock hides a real obligation.
        """
        return [holding for holding in self.holdings if holding.value < 0]

    @property
    def energy_kwh(self) -> float:
        """Total nameplate energy held."""
        return sum(holding.rated_kwh for holding in self.holdings)

    @property
    def value_per_kwh(self) -> float:
        """Portfolio value per kWh, the number that compares to a purchase price."""
        return self.value.amount / self.energy_kwh if self.energy_kwh else 0.0

    @property
    def loss_rate(self) -> float:
        """Annual loss as a share of the portfolio, i.e. the depreciation rate."""
        total = self.value.amount
        return self.annual_loss.amount / total if total > 0 else 0.0

    # -- slices -------------------------------------------------------------

    def by(self, attribute: str) -> list[Group]:
        """Group the holdings, largest value first."""
        buckets: dict[str, list[Holding]] = defaultdict(list)
        for holding in self.holdings:
            key = getattr(holding, attribute) or "unknown"
            buckets[str(key)].append(holding)

        groups = [
            Group(
                key=key,
                label=members[0].label if attribute == "pack_model_key" else key,
                count=len(members),
                value=sum(member.value for member in members),
                energy_kwh=sum(member.rated_kwh for member in members),
                annual_loss=sum(member.annual_loss for member in members),
                currency=self.currency,
            )
            for key, members in buckets.items()
        ]
        return sorted(groups, key=lambda group: group.value, reverse=True)

    def concentration(self, share: float = 0.8) -> int:
        """How few packs hold ``share`` of the value.

        A fleet where 8% of the packs carry 80% of the value should be selling
        those eight per cent attentively and clearing the rest by the pallet.
        """
        total = self.value.amount
        if total <= 0:
            return 0
        running = 0.0
        for index, holding in enumerate(
            sorted(self.holdings, key=lambda h: h.value, reverse=True), start=1
        ):
            running += holding.value
            if running >= total * share:
                return index
        return len(self.holdings)

    def summary(self) -> str:
        """The whole thing in one paragraph, for someone who reads one paragraph."""
        if not self.holdings:
            return "Nothing held."

        parts = [
            f"{len(self.holdings)} batteries, {self.energy_kwh:,.0f} kWh, "
            f"worth {self.value.format(0)} "
            f"({self.value_per_kwh:,.0f} {self.currency}/kWh)."
        ]
        if self.annual_loss.amount > 0:
            parts.append(
                f"Falling {self.monthly_loss.format(0)} a month "
                f"({self.loss_rate:.0%} a year)."
            )
        urgent = self.urgent
        if urgent:
            parts.append(
                f"{len(urgent)} of them, holding {self.value_at_risk.format(0)}, "
                f"drop below resale grade within {URGENT_HORIZON_YEARS:.0f} years."
            )
        if self.liabilities:
            parts.append(
                f"{len(self.liabilities)} cost money to dispose of rather than "
                "being worth anything."
            )
        return " ".join(parts)


def _value_per_health_point(payload: dict) -> float:
    """How much one point of state of health is worth on this pack, from its own
    sensitivity analysis.

    The engine already re-values every pack under a health shock, so the local
    slope is sitting in the stored record. Using it means a portfolio needs no
    revaluation at all, and it is exact at today's health rather than assuming
    value moves proportionally -- which it does not, because the reuse route
    prices health convexly and recycling does not price it at all.
    """
    for factor in payload.get("sensitivity", []):
        if not str(factor.get("name", "")).startswith(_HEALTH_FACTOR_PREFIX):
            continue
        swing = abs(float(factor.get("swing", {}).get("amount", 0.0)))
        return swing / _HEALTH_SHOCK_POINTS
    return 0.0


def holding_from(record: StoredValuation) -> Holding:
    """Turn one stored valuation into a portfolio line."""
    payload = record.payload
    battery = payload.get("battery", {})
    aging = payload.get("aging") or {}

    # Local slope times the health the pack is about to lose. First order, and
    # good over a year; it does not try to see round the cliff, which is what
    # `is_urgent` is for.
    per_point = _value_per_health_point(payload)
    fade_ahead = float(aging.get("annual_fade_ahead_points") or 0.0)

    return Holding(
        reference=record.reference,
        label=record.battery_label,
        pack_model_key=record.pack_model_key,
        chemistry=str(payload.get("bill_of_materials", {}).get("chemistry", "unknown")),
        rated_kwh=float(battery.get("rated_kwh") or 0.0),
        state_of_health=float(battery.get("state_of_health") or 0.0),
        value=record.residual_value,
        currency=record.currency,
        confidence=record.confidence,
        valued_on=record.created_at.date(),
        annual_loss=round(per_point * fade_ahead, 2),
        years_to_resale_floor=aging.get("years_to_resale_floor"),
        already_below_resale_floor=bool(aging.get("already_below_resale_floor")),
        wear_verdict=str(aging.get("verdict", "unknown")),
    )


def build(records: list[StoredValuation], *, currency: str = "EUR") -> Portfolio:
    """Assemble a portfolio from stored valuations.

    Records in another currency are skipped rather than converted: silently
    adding dollars to euros would produce a total that looks right and is not.
    """
    holdings = [
        holding_from(record) for record in records if record.currency == currency
    ]
    return Portfolio(holdings=holdings, currency=currency)


def to_dict(portfolio: Portfolio) -> dict:
    """Serialise a portfolio for the API and the web view."""
    return {
        "currency": portfolio.currency,
        "count": len(portfolio.holdings),
        "energy_kwh": round(portfolio.energy_kwh, 1),
        "value": round(portfolio.value.amount, 2),
        "value_formatted": portfolio.value.format(0),
        "value_per_kwh": round(portfolio.value_per_kwh, 2),
        "annual_loss": round(portfolio.annual_loss.amount, 2),
        "monthly_loss": round(portfolio.monthly_loss.amount, 2),
        "monthly_loss_formatted": portfolio.monthly_loss.format(0),
        "loss_rate": round(portfolio.loss_rate, 4),
        "value_at_risk": round(portfolio.value_at_risk.amount, 2),
        "urgent_count": len(portfolio.urgent),
        "stranded_count": len(portfolio.stranded),
        "liability_count": len(portfolio.liabilities),
        "concentration_80pct": portfolio.concentration(0.8),
        "summary": portfolio.summary(),
        "by_model": [
            {
                "key": group.key,
                "label": group.label,
                "count": group.count,
                "value": round(group.value, 2),
                "energy_kwh": round(group.energy_kwh, 1),
                "value_per_kwh": round(group.value_per_kwh, 2),
                "annual_loss": round(group.annual_loss, 2),
            }
            for group in portfolio.by("pack_model_key")
        ],
        "by_chemistry": [
            {
                "key": group.key,
                "count": group.count,
                "value": round(group.value, 2),
                "annual_loss": round(group.annual_loss, 2),
            }
            for group in portfolio.by("chemistry")
        ],
        "urgent": [
            {
                "reference": holding.reference,
                "label": holding.label,
                "value": round(holding.value, 2),
                "state_of_health": round(holding.state_of_health, 4),
                "years_to_resale_floor": holding.years_to_resale_floor,
                "annual_loss": round(holding.annual_loss, 2),
            }
            for holding in portfolio.urgent[:50]
        ],
    }


__all__ = [
    "URGENT_HORIZON_YEARS",
    "Holding",
    "Portfolio",
    "build",
    "holding_from",
    "to_dict",
]
