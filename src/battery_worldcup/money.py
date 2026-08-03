"""Money values that refuse to add themselves across currencies.

Valuations mix nickel priced in USD/t, gate fees quoted in EUR/kg and pack
prices published in USD/kWh. Silently summing those is the easiest way to
produce a confident, wrong number, so :class:`Money` carries its currency and
raises when asked to mix.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import BatteryWorldCupError

# ISO 4217 codes we carry display metadata for. Any three-letter code is
# accepted; these just get nicer formatting.
CURRENCY_SYMBOLS: dict[str, str] = {
    "EUR": "€",
    "USD": "$",
    "GBP": "£",
    "CNY": "¥",
    "JPY": "¥",
    "CHF": "CHF ",
    "SEK": "kr ",
    "NOK": "kr ",
    "PLN": "zł ",
    "KRW": "₩",
}

DEFAULT_CURRENCY = "EUR"


class CurrencyMismatchError(BatteryWorldCupError):
    """Attempted arithmetic between two different currencies."""


def normalise_currency(code: str) -> str:
    """Validate and upper-case an ISO 4217 currency code."""
    cleaned = str(code).strip().upper()
    if len(cleaned) != 3 or not cleaned.isalpha():
        raise BatteryWorldCupError(f"invalid currency code: {code!r}")
    return cleaned


@dataclass(frozen=True, slots=True, order=False)
class Money:
    """An amount in a specific currency."""

    amount: float
    currency: str = DEFAULT_CURRENCY

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", normalise_currency(self.currency))
        object.__setattr__(self, "amount", float(self.amount))

    @classmethod
    def zero(cls, currency: str = DEFAULT_CURRENCY) -> Money:
        """A zero amount in ``currency``."""
        return cls(0.0, currency)

    def _check(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"cannot combine {self.currency} and {other.currency}; "
                "convert with FxRates.convert() first"
            )

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, factor: float) -> Money:
        return Money(self.amount * float(factor), self.currency)

    __rmul__ = __mul__

    def __truediv__(self, divisor: float) -> Money:
        return Money(self.amount / float(divisor), self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __lt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._check(other)
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        self._check(other)
        return self.amount >= other.amount

    @property
    def is_negative(self) -> bool:
        """True when this represents a cost to the holder rather than a payout."""
        return self.amount < 0

    def rounded(self, places: int = 2) -> Money:
        """A copy rounded to ``places`` decimals."""
        return Money(round(self.amount, places), self.currency)

    def format(self, places: int = 2) -> str:
        """Human-readable string, e.g. ``€1,240.50``."""
        symbol = CURRENCY_SYMBOLS.get(self.currency, self.currency + " ")
        sign = "-" if self.amount < 0 else ""
        return f"{sign}{symbol}{abs(self.amount):,.{places}f}"

    def __str__(self) -> str:
        return self.format()


def money_sum(items: list[Money], currency: str = DEFAULT_CURRENCY) -> Money:
    """Sum a list of :class:`Money`, returning zero in ``currency`` when empty."""
    if not items:
        return Money.zero(currency)
    total = items[0]
    for item in items[1:]:
        total = total + item
    return total
