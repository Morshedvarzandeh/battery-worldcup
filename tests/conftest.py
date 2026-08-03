"""Shared fixtures.

Every test runs offline against an isolated cache. A test that silently reached
the network would be slow, flaky and would quietly stop testing the fallback
paths that matter most.
"""

from __future__ import annotations

import pytest

from battery_worldcup.market.cache import PriceCache
from battery_worldcup.market.resolver import build_resolver
from battery_worldcup.passport.resolver import PassportResolver
from battery_worldcup.valuation.config import ValuationConfig
from battery_worldcup.valuation.engine import ValuationEngine


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Point the price cache at a per-test directory."""
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("BWC_CACHE_DIR", str(cache_dir))
    # Clear any resolver-level env leakage between tests.
    monkeypatch.delenv("BWC_PRICE_CSV", raising=False)
    monkeypatch.delenv("BWC_PACK_CATALOGUE_DIR", raising=False)
    monkeypatch.delenv("BWC_PACK_API_URL", raising=False)
    monkeypatch.delenv("METALS_API_KEY", raising=False)
    return PriceCache(cache_dir)


@pytest.fixture
def offline_resolver(isolated_cache):
    """A price resolver that only uses the bundled snapshot."""
    return build_resolver(currency="EUR", offline=True, cache=isolated_cache)


@pytest.fixture
def engine(offline_resolver):
    """A valuation engine wired for offline use."""
    return ValuationEngine(
        config=ValuationConfig(currency="EUR"), prices=offline_resolver
    )


@pytest.fixture
def passports():
    """A passport resolver that never touches the network."""
    return PassportResolver()


@pytest.fixture
def eu_dpp_document():
    """A representative EU digital battery passport."""
    return {
        "batteryPassport": {
            "generalInformation": {
                "batteryPassportId": "urn:uuid:6f1c2e40-2b7a-4f1e-9d3a-11c0a4d5e881",
                "manufacturerName": "Automotive Energy Supply Corporation",
                "vehicleModel": "Nissan Leaf ZE1 40 kWh",
                "manufacturingDate": "2019-03-14",
                "batteryCategory": "EV battery",
                "batteryMass": {"value": 303, "unit": "kg"},
            },
            "performanceAndDurability": {
                "ratedCapacity": {"value": 40, "unit": "kWh"},
                "nominalVoltage": {"value": 350, "unit": "V"},
                "stateOfHealth": {"value": 81, "unit": "%"},
                "numberOfFullCycles": 850,
                "measurementDate": "2026-06-30",
            },
            "materialComposition": {
                "batteryChemistry": "Li-NMC 532",
                "criticalRawMaterials": [
                    {"substance": "Cobalt", "massKg": 7.1},
                    {"substance": "Lithium", "massKg": 4.0},
                    {"substance": "Nickel", "massKg": 17.4},
                ],
            },
            "circularity": {"safetyFlags": []},
        }
    }


@pytest.fixture
def lfp_document():
    """An LFP pack, where recycling is expected to be a net cost."""
    return {
        "generalInformation": {
            "manufacturerName": "BYD",
            "batteryModel": "Blade",
            "vehicleModel": "BYD Atto 3",
            "manufacturingDate": "2022-05-01",
            "batteryMass": {"value": 440, "unit": "kg"},
        },
        "performanceAndDurability": {
            "ratedCapacity": {"value": 60.5, "unit": "kWh"},
            "stateOfHealth": {"value": 88, "unit": "%"},
        },
        "materialComposition": {"batteryChemistry": "LFP"},
        "circularity": {},
    }
