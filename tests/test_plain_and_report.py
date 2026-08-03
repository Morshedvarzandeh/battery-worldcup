"""The end-user surface: plain wording, and the report people send on."""

from __future__ import annotations

import re
from datetime import date

import pytest

from battery_value.report import build_html_report, report_filename
from battery_value.valuation import plain
from battery_value.valuation.models import Pathway

VALUATION_DATE = date(2026, 8, 1)

# Terms an ordinary battery owner should never have to read in the plain view.
JARGON = [
    "state of health",
    "soh",
    "payable",
    "pathway",
    "traded form",
    "kg/kwh",
    "hydrometallurg",
    "black mass",
    "nmc",
    "lfp",
    "bom",
]


@pytest.fixture
def valuation(engine, eu_dpp_document, passports):
    return engine.value(passports.from_document(eu_dpp_document), as_of=VALUATION_DATE)


@pytest.fixture
def lfp_valuation(engine, lfp_document, passports):
    return engine.value(passports.from_document(lfp_document), as_of=VALUATION_DATE)


class TestFriendlyPathwayNames:
    def test_every_pathway_has_plain_wording(self):
        for pathway in Pathway:
            assert pathway.friendly_label
            assert pathway.plain_explanation.endswith(".")
            # The friendly name must not simply echo the industry term.
            assert pathway.friendly_label != pathway.label

    def test_friendly_names_avoid_jargon(self):
        for pathway in Pathway:
            text = (pathway.friendly_label + " " + pathway.plain_explanation).lower()
            for term in ("pathway", "residual", "state of health", "payable"):
                assert term not in text, (pathway, term)


class TestConfidenceBand:
    @pytest.mark.parametrize(
        ("confidence", "expected"),
        [(0.95, "Good estimate"), (0.6, "Reasonable estimate"),
         (0.4, "Rough estimate"), (0.1, "Very rough estimate")],
    )
    def test_bands(self, confidence, expected):
        assert plain.confidence_band(confidence).label == expected

    def test_tone_is_styleable(self):
        assert plain.confidence_band(0.95).tone == "good"
        assert plain.confidence_band(0.1).tone == "weak"

    def test_band_boundaries_are_continuous(self):
        """Every confidence from 0 to 1 must land in exactly one band."""
        for step in range(0, 101):
            band = plain.confidence_band(step / 100)
            assert band.label and band.explanation


class TestPlainDescriptions:
    def test_chemistry_avoids_the_acronym(self, valuation):
        described = plain.chemistry_in_plain_words(valuation.bom.chemistry)
        assert "NMC" not in described
        assert "lithium" in described.lower()

    def test_every_chemistry_has_plain_words(self):
        from battery_value.materials import load_chemistries

        for spec in load_chemistries().specs.values():
            described = plain.chemistry_in_plain_words(spec)
            assert described and described != spec.key

    @pytest.mark.parametrize(
        ("soh", "fragment"),
        [(0.95, "close to new"), (0.85, "good shape"), (0.75, "worn"),
         (0.62, "well worn"), (0.4, "end of its working life")],
    )
    def test_health_in_words(self, soh, fragment):
        assert fragment in plain.health_in_plain_words(soh)

    def test_lfp_note_explains_the_low_recycling_value(self):
        from battery_value.materials import resolve_chemistry

        note = plain.chemistry_value_note(resolve_chemistry("LFP"))
        assert "nickel" in note.lower() and "cobalt" in note.lower()


class TestHeadline:
    def test_positive_value_names_the_best_route(self, valuation):
        headline = plain.headline_sentence(valuation)
        assert "worth about" in headline
        assert valuation.residual_value.format(0) in headline

    def test_negative_value_is_framed_as_a_cost(self, engine, passports):
        """A pack that costs money to dispose of must not read as income."""
        passport = passports.from_document(
            {
                "generalInformation": {"batteryMass": {"value": 480, "unit": "kg"}},
                "spec": {"ratedEnergy": {"value": 75, "unit": "kWh"},
                         "chemistry": "NMC811"},
                "status": {"stateOfHealth": 40, "packCondition": "damaged"},
            }
        )
        result = engine.value(passport, as_of=VALUATION_DATE)
        assert result.residual_value.is_negative

        headline = plain.headline_sentence(result)
        assert "costs about" in headline
        assert "worth about" not in headline
        assert "-" not in headline  # no bare minus sign for a reader to decode

    def test_why_explains_without_jargon(self, valuation):
        joined = " ".join(plain.why_this_value(valuation)).lower()
        for term in ("payable", "pathway", "traded form", "kg/kwh"):
            assert term not in joined

    def test_unknown_model_is_admitted(self, engine, passports):
        passport = passports.from_document(
            {
                "generalInformation": {"manufacturerName": "Unknown Motors",
                                       "batteryMass": {"value": 300, "unit": "kg"}},
                "spec": {"ratedEnergy": {"value": 40, "unit": "kWh"},
                         "chemistry": "NMC532"},
                "status": {"stateOfHealth": 85},
            }
        )
        result = engine.value(passport, as_of=VALUATION_DATE)
        assert any("could not identify" in r for r in plain.why_this_value(result))

    def test_improvements_are_actionable(self, engine, passports):
        """A pack with no health data should be told to get a health check."""
        passport = passports.from_document(
            {
                "generalInformation": {"batteryMass": {"value": 300, "unit": "kg"}},
                "spec": {"ratedEnergy": {"value": 40, "unit": "kWh"},
                         "chemistry": "NMC532"},
            }
        )
        result = engine.value(passport, as_of=VALUATION_DATE)
        assert result.health_source == "assumed"
        assert any("health check" in tip for tip in plain.how_to_improve(result))


class TestReport:
    def test_is_a_standalone_document(self, valuation):
        document = build_html_report(valuation)
        assert document.startswith("<!doctype html>")
        assert "</html>" in document

    def test_has_no_external_references(self, valuation):
        """It must survive being emailed, opened offline, and printed."""
        document = build_html_report(valuation)
        assert not re.search(r'src\s*=\s*["\']https?://', document)
        assert not re.search(r'<link[^>]+href\s*=\s*["\']https?://', document)
        assert "<script" not in document.lower()

    def test_leads_with_the_plain_answer(self, valuation):
        document = build_html_report(valuation)
        assert valuation.residual_value.format(0) in document
        assert plain.headline_sentence(valuation) in document

    def test_carries_the_audit_trail(self, valuation):
        document = build_html_report(valuation)
        assert "Materials in the pack" in document
        assert "Market prices used" in document
        assert "lithium_carbonate" in document

    def test_summary_only_drops_the_technical_section(self, valuation):
        summary = build_html_report(valuation, include_technical=False)
        full = build_html_report(valuation, include_technical=True)
        assert "Market prices used" not in summary
        assert len(summary) < len(full)
        # The answer itself must survive.
        assert valuation.residual_value.format(0) in summary

    def test_lists_unavailable_routes_with_reasons(self, engine, passports):
        passport = passports.from_document(
            {
                "generalInformation": {"batteryMass": {"value": 300, "unit": "kg"}},
                "spec": {"ratedEnergy": {"value": 40, "unit": "kWh"},
                         "chemistry": "NMC532"},
                "status": {"stateOfHealth": 30, "packCondition": "thermal_event"},
            }
        )
        document = build_html_report(engine.value(passport, as_of=VALUATION_DATE))
        assert "not possible" in document

    def test_escapes_passport_supplied_text(self, engine, passports):
        """Passport fields are untrusted; they must not become markup."""
        passport = passports.from_document(
            {
                "generalInformation": {
                    "manufacturerName": "<script>alert('x')</script>",
                    "batteryMass": {"value": 300, "unit": "kg"},
                },
                "spec": {"ratedEnergy": {"value": 40, "unit": "kWh"},
                         "chemistry": "NMC532"},
                "status": {"stateOfHealth": 85},
            }
        )
        document = build_html_report(engine.value(passport, as_of=VALUATION_DATE))
        assert "<script>alert" not in document
        assert "&lt;script&gt;" in document

    def test_filename_is_safe_and_descriptive(self, valuation):
        name = report_filename(valuation)
        assert name.endswith(".html")
        assert re.fullmatch(r"[a-z0-9.\-]+", name), name
        assert "leaf" in name

    def test_lfp_report_explains_the_negative_recycling_value(self, lfp_valuation):
        document = build_html_report(lfp_valuation)
        assert "nickel" in document.lower()
