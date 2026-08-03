"""battery-worldcup: scan a battery passport, learn what the pack is worth.

A battery passport tells you how healthy a pack is. It does not tell you what
that health is worth. This package closes that gap: it reads the passport,
identifies the pack model and its components, builds a bill of materials,
prices it against current market data, and reports the residual value by each
route the holder could realistically take.

Typical use::

    from battery_worldcup import ValuationEngine

    engine = ValuationEngine()
    valuation = engine.value_scan("https://dpp.example.com/battery/AB123")
    print(valuation.summary())
"""

from .errors import (
    BatteryWorldCupError,
    MarketDataError,
    NoPriceAvailableError,
    PassportError,
    ValuationError,
)
from .money import Money
from .packs import PackCatalogue, PackModel, build_pack_resolver, load_catalogue
from .passport import BatteryPassport, PassportResolver, parse_carrier
from .valuation import (
    Pathway,
    PathwayValuation,
    ResidualValuation,
    ValuationConfig,
    ValuationEngine,
    value_passport,
)

__version__ = "0.1.0"

__all__ = [
    "BatteryPassport",
    "BatteryWorldCupError",
    "MarketDataError",
    "Money",
    "NoPriceAvailableError",
    "PackCatalogue",
    "PackModel",
    "PassportError",
    "PassportResolver",
    "Pathway",
    "PathwayValuation",
    "ResidualValuation",
    "ValuationConfig",
    "ValuationEngine",
    "ValuationError",
    "__version__",
    "build_pack_resolver",
    "load_catalogue",
    "parse_carrier",
    "value_passport",
]
