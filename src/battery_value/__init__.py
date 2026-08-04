"""battery-value: scan a battery passport, learn what the pack is worth.

A battery passport tells you how healthy a pack is. It does not tell you what
that health is worth. This package closes that gap: it reads the passport,
identifies the pack model and its components, builds a bill of materials,
prices it against current market data, and reports the residual value by each
route the holder could realistically take.

Typical use::

    from battery_value import ValuationEngine

    engine = ValuationEngine()
    valuation = engine.value_scan("https://dpp.example.com/battery/AB123")
    print(valuation.summary())

Copyright (C) 2026 Morshed Varzandeh.

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU Affero General Public License as published by the Free
Software Foundation, either version 3 of the License, or (at your option) any
later version. It is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License in
``LICENSE`` for details.
"""

from .errors import (
    BatteryValueError,
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
    "BatteryValueError",
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
