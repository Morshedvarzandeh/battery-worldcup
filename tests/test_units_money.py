"""Unit conversion and currency safety."""

from __future__ import annotations

import pytest

from battery_worldcup.errors import UnitError
from battery_worldcup.money import CurrencyMismatchError, Money, money_sum
from battery_worldcup.units import (
    MassUnit,
    convert_energy,
    convert_mass,
    parse_mass_unit,
    to_kg,
    to_kwh,
)


class TestMass:
    @pytest.mark.parametrize(
        ("value", "unit", "expected_kg"),
        [
            (1, "t", 1000.0),
            (1, "kg", 1.0),
            (1000, "g", 1.0),
            (1, "lb", 0.45359237),
            (1, "ton_us", 907.18474),
        ],
    )
    def test_to_kg(self, value, unit, expected_kg):
        assert to_kg(value, unit) == pytest.approx(expected_kg)

    def test_round_trip(self):
        assert convert_mass(convert_mass(5.0, "t", "lb"), "lb", "t") == pytest.approx(5.0)

    @pytest.mark.parametrize("alias", ["T", "tonne", "TONNES", "metric_ton", "MT"])
    def test_aliases(self, alias):
        assert parse_mass_unit(alias) is MassUnit.TONNE

    def test_unknown_unit_raises(self):
        with pytest.raises(UnitError, match="unrecognised mass unit"):
            to_kg(1, "furlongs")


class TestEnergy:
    def test_wh_to_kwh(self):
        assert to_kwh(1500, "Wh") == pytest.approx(1.5)

    def test_mwh_to_kwh(self):
        assert to_kwh(2, "MWh") == pytest.approx(2000.0)

    def test_round_trip(self):
        assert convert_energy(convert_energy(75, "kWh", "Wh"), "Wh", "kWh") == 75


class TestMoney:
    def test_addition(self):
        assert (Money(10, "EUR") + Money(5, "EUR")).amount == 15

    def test_mixing_currencies_raises(self):
        with pytest.raises(CurrencyMismatchError):
            Money(10, "EUR") + Money(5, "USD")

    def test_comparison_requires_same_currency(self):
        with pytest.raises(CurrencyMismatchError):
            _ = Money(10, "EUR") < Money(5, "USD")

    def test_scaling(self):
        assert (Money(10, "EUR") * 2.5).amount == 25
        assert (3 * Money(10, "EUR")).amount == 30

    def test_negative_formatting(self):
        """The minus sign precedes the symbol, and thousands are grouped."""
        assert Money(-1234.56, "EUR").format(0) == "-€1,235"
        assert Money(-1234.56, "EUR").format(2) == "-€1,234.56"

    def test_is_negative(self):
        assert Money(-1, "EUR").is_negative
        assert not Money(1, "EUR").is_negative

    def test_currency_normalised(self):
        assert Money(1, "eur").currency == "EUR"

    def test_sum_of_empty_list(self):
        assert money_sum([], "USD") == Money(0, "USD")

    def test_sum(self):
        total = money_sum([Money(1), Money(2), Money(3)])
        assert total.amount == 6
