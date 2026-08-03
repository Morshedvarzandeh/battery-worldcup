"""Chemical maths. These numbers are checkable against a periodic table."""

from __future__ import annotations

import pytest

from battery_worldcup.compounds import (
    TRADED_FORMS,
    FormulaError,
    get_traded_form,
    mass_fraction,
    molar_mass,
    parse_formula,
)


class TestParseFormula:
    def test_simple(self):
        assert parse_formula("Li2CO3") == {"Li": 2.0, "C": 1.0, "O": 3.0}

    def test_nested_parentheses(self):
        assert parse_formula("Ca(OH)2") == {"Ca": 1.0, "O": 2.0, "H": 2.0}

    def test_hydrate_with_middle_dot(self):
        counts = parse_formula("CoSO4·7H2O")
        assert counts["Co"] == 1.0
        assert counts["H"] == 14.0
        assert counts["O"] == pytest.approx(11.0)

    def test_hydrate_with_plain_dot(self):
        assert parse_formula("CoSO4.7H2O") == parse_formula("CoSO4·7H2O")

    @pytest.mark.parametrize(
        ("formula", "expected"),
        [
            ("LiNi0.8Mn0.1Co0.1O2", {"Ni": 0.8, "Mn": 0.1, "Co": 0.1}),
            ("LiNi0.6Mn0.2Co0.2O2", {"Ni": 0.6, "Mn": 0.2, "Co": 0.2}),
            ("LiNi0.8Co0.15Al0.05O2", {"Ni": 0.8, "Co": 0.15, "Al": 0.05}),
        ],
    )
    def test_fractional_stoichiometry(self, formula, expected):
        """A decimal point in a cathode formula is not a hydrate separator."""
        counts = parse_formula(formula)
        for element, amount in expected.items():
            assert counts[element] == pytest.approx(amount)

    def test_rejects_unknown_element(self):
        with pytest.raises(FormulaError, match="unknown element"):
            parse_formula("Xx2O3")

    def test_rejects_empty(self):
        with pytest.raises(FormulaError):
            parse_formula("")

    def test_rejects_unbalanced_parentheses(self):
        with pytest.raises(FormulaError, match="unbalanced"):
            parse_formula("Ca(OH2")


class TestMassFraction:
    @pytest.mark.parametrize(
        ("formula", "element", "expected"),
        [
            ("Li2CO3", "Li", 0.18785),
            ("LiOH.H2O", "Li", 0.16539),
            ("CoSO4.7H2O", "Co", 0.20966),
            ("NiSO4.6H2O", "Ni", 0.22330),
            ("MnSO4.H2O", "Mn", 0.32506),
        ],
    )
    def test_known_battery_salts(self, formula, element, expected):
        assert mass_fraction(formula, element) == pytest.approx(expected, abs=1e-4)

    def test_fractions_sum_to_one(self):
        counts = parse_formula("CoSO4.7H2O")
        total = sum(mass_fraction("CoSO4.7H2O", element) for element in counts)
        assert total == pytest.approx(1.0, abs=1e-9)

    def test_missing_element_raises(self):
        with pytest.raises(FormulaError, match="does not occur"):
            mass_fraction("Li2CO3", "Ni")

    def test_molar_mass(self):
        assert molar_mass("Li2CO3") == pytest.approx(73.89, abs=0.01)


class TestTradedForms:
    def test_lce_factor(self):
        """The industry's LCE conversion: 1 kg Li = 5.323 kg Li2CO3."""
        form = get_traded_form("lithium_carbonate")
        assert form.form_per_element() == pytest.approx(5.323, abs=0.002)

    def test_pure_metal_has_unit_fraction(self):
        assert get_traded_form("nickel_metal").contained_fraction() == 1.0

    def test_every_form_resolves(self):
        for key, form in TRADED_FORMS.items():
            assert 0 < form.contained_fraction() <= 1.0, key

    def test_unknown_form_raises(self):
        with pytest.raises(FormulaError, match="unknown traded form"):
            get_traded_form("unobtainium")
