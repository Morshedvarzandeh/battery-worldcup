"""Chemical formula parsing, so contained-metal maths is derived, not guessed.

Battery materials are almost never traded as the pure metal. Lithium trades as
carbonate or hydroxide, nickel and cobalt as sulphate hydrates. A price of
"USD 14,000 per tonne of lithium carbonate" is *not* a price for a tonne of
lithium -- it is a price for 187.9 kg of lithium plus a lot of carbonate.

Every one of those bridging factors is computed here from the formula and
standard atomic weights, rather than hard-coded, so the numbers are auditable
and cannot silently drift.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from .errors import UnitError

# IUPAC standard atomic weights (2021), abridged to 5 significant figures.
ATOMIC_WEIGHTS: dict[str, float] = {
    "H": 1.0080,
    "Li": 6.9400,
    "B": 10.810,
    "C": 12.011,
    "N": 14.007,
    "O": 15.999,
    "F": 18.998,
    "Na": 22.990,
    "Mg": 24.305,
    "Al": 26.982,
    "Si": 28.085,
    "P": 30.974,
    "S": 32.060,
    "Cl": 35.450,
    "K": 39.098,
    "Ca": 40.078,
    "Ti": 47.867,
    "V": 50.942,
    "Cr": 51.996,
    "Mn": 54.938,
    "Fe": 55.845,
    "Co": 58.933,
    "Ni": 58.693,
    "Cu": 63.546,
    "Zn": 65.380,
    "Zr": 91.224,
    "Nb": 92.906,
    "Sn": 118.71,
    "Sb": 121.76,
    "Ba": 137.33,
    "W": 183.84,
    "Pb": 207.20,
}

# Unambiguous hydrate separators. A plain "." is deliberately excluded here:
# in cathode formulas it is a decimal point ("LiNi0.8Mn0.1Co0.1O2"), and only
# _HYDRATE_DOT below rewrites it when it genuinely introduces water.
_HYDRATE_SEPARATORS = "·*•∙"

# A "." counts as a hydrate separator only when water of crystallisation
# follows it, e.g. "CoSO4.7H2O". "Ni0.8Mn..." is left as a decimal.
_HYDRATE_DOT = re.compile(r"\.(?=\d*H2O(?![a-z0-9]))")

_TOKEN = re.compile(r"([A-Z][a-z]?)(\d*\.?\d*)|(\()|(\)(\d*\.?\d*))")


class FormulaError(UnitError):
    """The supplied string is not a chemical formula this parser understands."""


@lru_cache(maxsize=256)
def parse_formula(formula: str) -> dict[str, float]:
    """Return the element -> atom-count map for a chemical formula.

    Handles nested parentheses, fractional stoichiometry (as in NMC cathodes)
    and hydrate notation:

    >>> parse_formula("Li2CO3") == {"Li": 2.0, "C": 1.0, "O": 3.0}
    True
    >>> parse_formula("CoSO4.7H2O")["H"]
    14.0
    """
    if not formula or not formula.strip():
        raise FormulaError("empty formula")

    normalised = _HYDRATE_DOT.sub("·", formula.strip())
    for separator in _HYDRATE_SEPARATORS:
        normalised = normalised.replace(separator, "·")

    counts: dict[str, float] = {}
    for segment in normalised.split("·"):
        segment = segment.strip()
        if not segment:
            continue
        # A hydrate segment may carry a leading multiplier, e.g. "7H2O".
        multiplier, remainder = _split_leading_multiplier(segment)
        for element, count in _parse_segment(remainder).items():
            counts[element] = counts.get(element, 0.0) + count * multiplier

    if not counts:
        raise FormulaError(f"no elements found in formula: {formula!r}")
    return counts


def _split_leading_multiplier(segment: str) -> tuple[float, str]:
    match = re.match(r"^(\d+\.?\d*)(?=[A-Z(])", segment)
    if match:
        return float(match.group(1)), segment[match.end() :]
    return 1.0, segment


def _parse_segment(segment: str) -> dict[str, float]:
    """Parse one hydrate-free segment, honouring nested parentheses."""
    stack: list[dict[str, float]] = [{}]
    position = 0

    while position < len(segment):
        match = _TOKEN.match(segment, position)
        if match is None:
            raise FormulaError(
                f"cannot parse formula at position {position}: {segment!r}"
            )
        position = match.end()
        element, count_text, open_paren, close_paren, group_count = match.groups()

        if open_paren:
            stack.append({})
        elif close_paren:
            if len(stack) == 1:
                raise FormulaError(f"unbalanced parentheses in {segment!r}")
            group = stack.pop()
            factor = float(group_count) if group_count else 1.0
            for symbol, amount in group.items():
                stack[-1][symbol] = stack[-1].get(symbol, 0.0) + amount * factor
        else:
            if element not in ATOMIC_WEIGHTS:
                raise FormulaError(f"unknown element {element!r} in {segment!r}")
            amount = float(count_text) if count_text else 1.0
            stack[-1][element] = stack[-1].get(element, 0.0) + amount

    if len(stack) != 1:
        raise FormulaError(f"unbalanced parentheses in {segment!r}")
    return stack[0]


@lru_cache(maxsize=256)
def molar_mass(formula: str) -> float:
    """Molar mass of a formula in g/mol."""
    return sum(
        ATOMIC_WEIGHTS[element] * count for element, count in parse_formula(formula).items()
    )


@lru_cache(maxsize=256)
def mass_fraction(formula: str, element: str) -> float:
    """Mass fraction (0-1) of ``element`` within ``formula``.

    >>> round(mass_fraction("Li2CO3", "Li"), 5)
    0.18788
    >>> round(mass_fraction("CoSO4.7H2O", "Co"), 5)
    0.20966
    """
    counts = parse_formula(formula)
    if element not in counts:
        raise FormulaError(f"{element!r} does not occur in {formula!r}")
    if element not in ATOMIC_WEIGHTS:
        raise FormulaError(f"unknown element {element!r}")
    return (ATOMIC_WEIGHTS[element] * counts[element]) / molar_mass(formula)


@dataclass(frozen=True, slots=True)
class TradedForm:
    """A physical form a material is actually bought and sold in.

    ``payable_element`` is the element whose contained mass carries the value.
    Price feeds quote per tonne of *this form*; the valuation engine needs per
    kg of contained element, and :meth:`contained_fraction` is the bridge.
    """

    key: str
    label: str
    formula: str | None
    payable_element: str
    note: str = ""

    def contained_fraction(self) -> float:
        """kg of payable element per kg of the traded form."""
        if self.formula is None:
            return 1.0  # Already the pure metal (LME nickel, copper, aluminium).
        return mass_fraction(self.formula, self.payable_element)

    def form_per_element(self) -> float:
        """kg of traded form equivalent to 1 kg of contained element.

        For lithium carbonate this is the industry's "LCE factor" of 5.323.
        """
        return 1.0 / self.contained_fraction()


# The forms our price providers actually quote. Keyed by the identifier used
# throughout the market layer.
TRADED_FORMS: dict[str, TradedForm] = {
    form.key: form
    for form in (
        TradedForm("nickel_metal", "Nickel (LME cash, 99.8%)", None, "Ni"),
        TradedForm(
            "nickel_sulphate",
            "Nickel sulphate hexahydrate",
            "NiSO4.6H2O",
            "Ni",
            note="Battery-grade precursor; trades at a premium/discount to LME.",
        ),
        TradedForm("cobalt_metal", "Cobalt (standard grade)", None, "Co"),
        TradedForm(
            "cobalt_sulphate",
            "Cobalt sulphate heptahydrate",
            "CoSO4.7H2O",
            "Co",
        ),
        TradedForm(
            "lithium_carbonate",
            "Lithium carbonate (battery grade, 99.5%)",
            "Li2CO3",
            "Li",
            note="The LCE basis: 1 kg Li = 5.323 kg Li2CO3.",
        ),
        TradedForm(
            "lithium_hydroxide",
            "Lithium hydroxide monohydrate (battery grade)",
            "LiOH.H2O",
            "Li",
        ),
        TradedForm("copper_metal", "Copper (LME cash, grade A)", None, "Cu"),
        TradedForm("aluminium_metal", "Aluminium (LME cash, high grade)", None, "Al"),
        TradedForm(
            "manganese_sulphate",
            "Manganese sulphate monohydrate",
            "MnSO4.H2O",
            "Mn",
        ),
        TradedForm("lead_metal", "Lead (LME cash)", None, "Pb"),
        TradedForm("steel_scrap", "Steel scrap (HMS 1&2)", None, "Fe"),
        TradedForm(
            "graphite_flake",
            "Natural graphite (flake, 94-95% C)",
            None,
            "C",
            note="Recovered anode graphite rarely meets battery spec; see recovery data.",
        ),
    )
}


def get_traded_form(key: str) -> TradedForm:
    """Look up a traded form by key."""
    try:
        return TRADED_FORMS[key]
    except KeyError:
        raise FormulaError(f"unknown traded form: {key!r}") from None
