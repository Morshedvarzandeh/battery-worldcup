"""The valuation engine: pathways, eligibility, sensitivity and the maths."""

from __future__ import annotations

from datetime import date

import pytest

from battery_worldcup.errors import ValuationError
from battery_worldcup.market.resolver import build_resolver
from battery_worldcup.valuation import Pathway, ValuationConfig, ValuationEngine
from battery_worldcup.valuation.health import HealthSource, assess_health
from battery_worldcup.materials import resolve_chemistry

VALUATION_DATE = date(2026, 8, 1)


class TestHealthAssessment:
    def test_measured_soh_preferred(self, passports):
        passport = passports.from_document(
            {"spec": {"ratedEnergy": {"value": 40, "unit": "kWh"}},
             "status": {"stateOfHealth": 81, "cycleCount": 850}}
        )
        health = assess_health(
            passport, resolve_chemistry("NMC532"), ValuationConfig(), as_of=VALUATION_DATE
        )
        assert health.source is HealthSource.MEASURED
        assert health.soh == pytest.approx(0.81)

    def test_estimated_from_cycles(self, passports):
        """Rated cycle life is quoted to 80% SoH, so full life means 20% lost."""
        passport = passports.from_document(
            {"spec": {"ratedEnergy": {"value": 40, "unit": "kWh"},
                      "chemistry": "NMC532"},
             "status": {"cycleCount": 2000}}
        )
        health = assess_health(
            passport, resolve_chemistry("NMC532"), ValuationConfig(), as_of=VALUATION_DATE
        )
        assert health.source is HealthSource.CYCLES
        assert health.soh == pytest.approx(0.80, abs=0.01)

    def test_estimated_from_age(self, passports):
        passport = passports.from_document(
            {"spec": {"ratedEnergy": {"value": 40, "unit": "kWh"}},
             "generalInformation": {"manufacturingDate": "2016-08-01"}}
        )
        health = assess_health(
            passport, resolve_chemistry("NMC532"), ValuationConfig(), as_of=VALUATION_DATE
        )
        assert health.source is HealthSource.AGE
        assert health.soh == pytest.approx(1 - 10 * 0.023, abs=0.01)

    def test_assumed_when_nothing_known(self, passports):
        passport = passports.from_document(
            {"spec": {"ratedEnergy": {"value": 40, "unit": "kWh"}}}
        )
        health = assess_health(
            passport, resolve_chemistry("NMC532"), ValuationConfig(), as_of=VALUATION_DATE
        )
        assert health.source is HealthSource.ASSUMED
        assert health.concerns
        assert health.confidence < 0.4

    def test_confidence_ordering(self):
        assert (
            HealthSource.MEASURED.confidence
            > HealthSource.CYCLES.confidence
            > HealthSource.AGE.confidence
            > HealthSource.ASSUMED.confidence
        )

    def test_stale_measurement_reduces_confidence(self, passports):
        recent = passports.from_document(
            {"spec": {"ratedEnergy": {"value": 40, "unit": "kWh"}},
             "status": {"stateOfHealth": 81, "measurementDate": "2026-07-01"}}
        )
        old = passports.from_document(
            {"spec": {"ratedEnergy": {"value": 40, "unit": "kWh"}},
             "status": {"stateOfHealth": 81, "measurementDate": "2023-01-01"}}
        )
        config = ValuationConfig()
        chemistry = resolve_chemistry("NMC532")
        assert (
            assess_health(recent, chemistry, config, as_of=VALUATION_DATE).confidence
            > assess_health(old, chemistry, config, as_of=VALUATION_DATE).confidence
        )


class TestPathwayEligibility:
    def test_healthy_pack_qualifies_for_everything(self, engine, eu_dpp_document, passports):
        valuation = engine.value(passports.from_document(eu_dpp_document), as_of=VALUATION_DATE)
        assert valuation.pathway(Pathway.REUSE).eligible
        assert valuation.pathway(Pathway.PARTS_OUT).eligible
        assert valuation.pathway(Pathway.RECYCLING).eligible

    def test_recycling_is_always_available(self, engine, passports):
        """Even a burnt pack can be recycled; that is the value floor."""
        passport = passports.from_document(
            {
                "generalInformation": {"batteryMass": {"value": 300, "unit": "kg"}},
                "spec": {"ratedEnergy": {"value": 40, "unit": "kWh"},
                         "chemistry": "NMC532"},
                "status": {"stateOfHealth": 30, "packCondition": "thermal_event"},
            }
        )
        valuation = engine.value(passport, as_of=VALUATION_DATE)
        assert valuation.pathway(Pathway.RECYCLING).eligible
        assert not valuation.pathway(Pathway.REUSE).eligible
        assert not valuation.pathway(Pathway.PARTS_OUT).eligible
        assert not valuation.pathway(Pathway.SECOND_LIFE).eligible

    def test_blockers_explain_themselves(self, engine, passports):
        passport = passports.from_document(
            {
                "generalInformation": {"batteryMass": {"value": 300, "unit": "kg"}},
                "spec": {"ratedEnergy": {"value": 40, "unit": "kWh"},
                         "chemistry": "NMC532"},
                "status": {"stateOfHealth": 55},
            }
        )
        valuation = engine.value(passport, as_of=VALUATION_DATE)
        reuse = valuation.pathway(Pathway.REUSE)
        assert not reuse.eligible
        assert any("state of health" in blocker for blocker in reuse.blockers)

    def test_damaged_pack_costs_more_to_move(self, engine, passports):
        def recycling_cost(condition):
            passport = passports.from_document(
                {
                    "generalInformation": {"batteryMass": {"value": 300, "unit": "kg"}},
                    "spec": {"ratedEnergy": {"value": 40, "unit": "kWh"},
                             "chemistry": "NMC532"},
                    "status": {"stateOfHealth": 70, "packCondition": condition},
                }
            )
            valuation = engine.value(passport, as_of=VALUATION_DATE)
            return valuation.pathway(Pathway.RECYCLING).total_cost.amount

        assert recycling_cost("damaged") > recycling_cost("healthy")

    def test_parts_out_needs_a_known_model(self, engine, passports):
        passport = passports.from_document(
            {
                "generalInformation": {"manufacturerName": "Unknown Motors",
                                       "batteryMass": {"value": 300, "unit": "kg"}},
                "spec": {"ratedEnergy": {"value": 40, "unit": "kWh"},
                         "chemistry": "NMC532"},
                "status": {"stateOfHealth": 85},
            }
        )
        valuation = engine.value(passport, as_of=VALUATION_DATE)
        parts = valuation.pathway(Pathway.PARTS_OUT)
        assert not parts.eligible
        assert any("not identified" in blocker for blocker in parts.blockers)


class TestValuationArithmetic:
    def test_net_is_revenue_minus_cost(self, engine, eu_dpp_document, passports):
        valuation = engine.value(passports.from_document(eu_dpp_document), as_of=VALUATION_DATE)
        for pathway in valuation.eligible_pathways:
            assert pathway.net_value.amount == pytest.approx(
                pathway.gross_revenue.amount - pathway.total_cost.amount
            )

    def test_recommended_is_the_highest_value(self, engine, eu_dpp_document, passports):
        valuation = engine.value(passports.from_document(eu_dpp_document), as_of=VALUATION_DATE)
        best = valuation.recommended
        for pathway in valuation.eligible_pathways:
            assert pathway.net_value.amount <= best.net_value.amount

    def test_value_per_kwh(self, engine, eu_dpp_document, passports):
        valuation = engine.value(passports.from_document(eu_dpp_document), as_of=VALUATION_DATE)
        assert valuation.value_per_kwh.amount == pytest.approx(
            valuation.residual_value.amount / valuation.rated_kwh
        )

    def test_recycling_revenue_matches_hand_calculation(self, engine, passports):
        """Recompute one element's recovery revenue independently."""
        passport = passports.from_document(
            {
                "generalInformation": {"batteryMass": {"value": 480, "unit": "kg"}},
                "spec": {"ratedEnergy": {"value": 75, "unit": "kWh"},
                         "chemistry": "NMC811"},
                "status": {"stateOfHealth": 70},
                "materialComposition": {
                    "criticalRawMaterials": [{"substance": "Cobalt", "massKg": 10.0}]
                },
            }
        )
        valuation = engine.value(passport, as_of=VALUATION_DATE)
        recycling = valuation.pathway(Pathway.RECYCLING)

        cobalt_line = next(
            line for line in recycling.lines if line.label.startswith("Co ")
        )
        quote = valuation.prices.get("cobalt_sulphate")
        from battery_worldcup.materials import load_recovery

        recovery = load_recovery().get("hydrometallurgical").recovery_for("Co")
        expected = 10.0 * quote.price_per_kg_contained() * recovery.value_yield
        assert cobalt_line.amount.amount == pytest.approx(expected, rel=1e-6)

    def test_lfp_recycling_is_a_net_cost(self, engine, lfp_document, passports):
        """LFP has no nickel or cobalt, so recovery does not cover treatment."""
        valuation = engine.value(passports.from_document(lfp_document), as_of=VALUATION_DATE)
        assert valuation.pathway(Pathway.RECYCLING).net_value.is_negative

    def test_higher_soh_is_worth_more(self, engine, passports):
        def value_at(soh):
            passport = passports.from_document(
                {
                    "battery": {"vehicleModel": "Nissan Leaf ZE1 40 kWh"},
                    "status": {"stateOfHealth": soh},
                }
            )
            return engine.value(passport, as_of=VALUATION_DATE).residual_value.amount

        assert value_at(90) > value_at(80) > value_at(75)

    def test_nickel_rich_beats_lfp_on_recycling(self, engine, passports):
        def recycling_value(chemistry):
            passport = passports.from_document(
                {
                    "generalInformation": {"batteryMass": {"value": 450, "unit": "kg"}},
                    "spec": {"ratedEnergy": {"value": 65, "unit": "kWh"},
                             "chemistry": chemistry},
                    "status": {"stateOfHealth": 70},
                }
            )
            valuation = engine.value(passport, as_of=VALUATION_DATE)
            return valuation.pathway(Pathway.RECYCLING).net_value.amount

        assert recycling_value("NMC811") > recycling_value("LFP")

    def test_lead_acid_recycling_is_positive(self, engine, passports):
        """Lead's closed loop is the one chemistry where scrap clearly pays."""
        passport = passports.from_document(
            {
                "generalInformation": {"batteryMass": {"value": 25, "unit": "kg"}},
                "spec": {"ratedEnergy": {"value": 0.9, "unit": "kWh"},
                         "chemistry": "lead acid"},
                "status": {"stateOfHealth": 60},
            }
        )
        valuation = engine.value(passport, as_of=VALUATION_DATE)
        recycling = valuation.pathway(Pathway.RECYCLING)
        assert recycling.gross_revenue.amount > 0


class TestCurrency:
    def test_every_line_converts_consistently(self, isolated_cache, eu_dpp_document, passports):
        def value_in(currency):
            engine = ValuationEngine(
                config=ValuationConfig(currency=currency),
                prices=build_resolver(
                    currency=currency, offline=True, cache=isolated_cache
                ),
            )
            return engine.value(
                passports.from_document(eu_dpp_document), as_of=VALUATION_DATE
            )

        eur = value_in("EUR")
        usd = value_in("USD")
        ratio = usd.residual_value.amount / eur.residual_value.amount

        # The bundled fallback rate; every line must use it, not just some.
        assert ratio == pytest.approx(1.12, rel=1e-3)
        assert usd.residual_value.currency == "USD"

    def test_money_cannot_mix_currencies_in_a_pathway(self, engine, eu_dpp_document, passports):
        valuation = engine.value(passports.from_document(eu_dpp_document), as_of=VALUATION_DATE)
        for pathway in valuation.pathways:
            for line in pathway.lines:
                assert line.amount.currency == valuation.currency


class TestSensitivity:
    def test_range_brackets_the_expected_value(self, engine, eu_dpp_document, passports):
        valuation = engine.value(passports.from_document(eu_dpp_document), as_of=VALUATION_DATE)
        band = valuation.value_range
        assert band.low.amount <= band.expected.amount <= band.high.amount

    def test_factors_are_ranked_by_impact(self, engine, eu_dpp_document, passports):
        valuation = engine.value(passports.from_document(eu_dpp_document), as_of=VALUATION_DATE)
        swings = [abs(factor.swing.amount) for factor in valuation.sensitivity]
        assert swings == sorted(swings, reverse=True)

    def test_price_shock_moves_a_recycling_bound_pack(self, engine, passports):
        """For a pack whose best route is recycling, metal prices dominate."""
        passport = passports.from_document(
            {
                "generalInformation": {"batteryMass": {"value": 480, "unit": "kg"}},
                "spec": {"ratedEnergy": {"value": 75, "unit": "kWh"},
                         "chemistry": "NMC811"},
                "status": {"stateOfHealth": 45},
            }
        )
        valuation = engine.value(passport, as_of=VALUATION_DATE)
        assert valuation.recommended.pathway is Pathway.RECYCLING
        price_factor = next(
            factor for factor in valuation.sensitivity if "prices" in factor.name
        )
        assert price_factor.high.amount > price_factor.low.amount


class TestEngineErrors:
    def test_missing_energy_raises_with_guidance(self, engine, passports):
        passport = passports.from_document({"spec": {"chemistry": "NMC811"}})
        with pytest.raises(ValuationError, match="nameplate energy"):
            engine.value(passport, as_of=VALUATION_DATE)

    def test_missing_chemistry_raises_with_guidance(self, engine, passports):
        passport = passports.from_document(
            {"spec": {"ratedEnergy": {"value": 40, "unit": "kWh"}}}
        )
        with pytest.raises(ValuationError, match="chemistry"):
            engine.value(passport, as_of=VALUATION_DATE)

    def test_catalogue_supplies_both_missing_fields(self, engine, passports):
        """A vehicle name alone is enough, because the catalogue fills the rest."""
        passport = passports.from_document(
            {"battery": {"vehicleModel": "Nissan Leaf ZE1 40 kWh"},
             "status": {"stateOfHealth": 80}}
        )
        valuation = engine.value(passport, as_of=VALUATION_DATE)
        assert valuation.rated_kwh == 40.0
        assert valuation.pack_model is not None


class TestReporting:
    def test_summary_is_human_readable(self, engine, eu_dpp_document, passports):
        valuation = engine.value(passports.from_document(eu_dpp_document), as_of=VALUATION_DATE)
        summary = valuation.summary()
        assert "kWh" in summary and "SoH" in summary and "confidence" in summary

    def test_provenance_records_every_price(self, engine, eu_dpp_document, passports):
        valuation = engine.value(passports.from_document(eu_dpp_document), as_of=VALUATION_DATE)
        assert valuation.provenance
        assert len(valuation.prices.provenance_lines()) == len(valuation.prices.quotes)

    def test_stale_baseline_prices_warn(self, engine, eu_dpp_document, passports):
        valuation = engine.value(passports.from_document(eu_dpp_document), as_of=VALUATION_DATE)
        assert any("live provider" in warning for warning in valuation.warnings)

    def test_declared_composition_raises_confidence(self, engine, passports):
        def confidence(with_composition):
            document = {
                "generalInformation": {"batteryMass": {"value": 480, "unit": "kg"}},
                "spec": {"ratedEnergy": {"value": 75, "unit": "kWh"},
                         "chemistry": "NMC811"},
                "status": {"stateOfHealth": 40},
            }
            if with_composition:
                document["materialComposition"] = {
                    "criticalRawMaterials": [
                        {"substance": "Cobalt", "massKg": 6.9},
                        {"substance": "Nickel", "massKg": 55.0},
                        {"substance": "Lithium", "massKg": 7.4},
                    ]
                }
            valuation = engine.value(
                passports.from_document(document), as_of=VALUATION_DATE
            )
            return valuation.pathway(Pathway.RECYCLING).confidence

        assert confidence(True) > confidence(False)
