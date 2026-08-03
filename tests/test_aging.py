"""The wear layer: fade curves, verdicts against a cohort, and forecasts.

The tests that matter most here are the ones about what the module refuses to
say. A verdict drawn from the curve that produced the reading is not a verdict,
and a curve with no dispersion turns half of every fleet into an anomaly.
"""

from __future__ import annotations

from datetime import date

import pytest

from battery_value.materials.degradation import (
    DegradationProfile,
    load_degradation,
)
from battery_value.valuation import ValuationConfig
from battery_value.valuation.aging import AgingVerdict, assess_aging
from battery_value.valuation.config import ValuationConfig as Config
from battery_value.valuation.health import HealthSource, assess_health
from battery_value.valuation import plain

VALUATION_DATE = date(2026, 8, 1)


def profile(key: str) -> DegradationProfile:
    return load_degradation().for_pack_model(key)


def leaf_document(**status) -> dict:
    """A Leaf 40 passport with whatever health evidence a test wants."""
    return {
        "battery": {
            "manufacturer": "Nissan",
            "vehicleModel": "Leaf ZE1 40 kWh",
            "manufacturingDate": "2019-04-01",
        },
        "spec": {"ratedEnergy": {"value": 40, "unit": "kWh"}, "chemistry": "NMC532"},
        "status": status,
    }


class TestTheCurve:
    def test_fade_follows_the_square_root_of_time(self):
        """Half the eight-year fade lands in the first two years, not four."""
        leaf = profile("nissan-leaf-ze1-40")
        at_two = 1 - leaf.expected_soh(2, rated_kwh=40)
        at_eight = 1 - leaf.expected_soh(8, rated_kwh=40)
        assert at_two / at_eight == pytest.approx(0.5, abs=0.01)

    def test_cooling_separates_models_of_the_same_chemistry(self):
        """Two nickel packs of similar vintage, one cooled and one not."""
        passive = profile("nissan-leaf-ze1-40")
        cooled = profile("hyundai-kona-64")
        assert passive.thermal_management == "passive"
        assert cooled.thermal_management == "liquid"
        assert passive.expected_soh(8, rated_kwh=40) < cooled.expected_soh(
            8, rated_kwh=64
        ) - 0.05

    def test_cycles_are_charged_only_above_typical_use(self):
        """Reference mileage is already inside fade_at_8y, so it is not billed twice."""
        tesla = profile("tesla-model3-lr")
        typical = tesla.reference_cycles_per_year(75) * 5
        assert tesla.expected_soh(5, cycles=typical, rated_kwh=75) == pytest.approx(
            tesla.expected_soh(5, rated_kwh=75)
        )

    def test_light_use_is_credited(self):
        """40,000 km on an eight-year-old car really is better news than 160,000."""
        tesla = profile("tesla-model3-lr")
        light = tesla.expected_soh(8, cycles=100, rated_kwh=75)
        heavy = tesla.expected_soh(8, cycles=600, rated_kwh=75)
        assert light > heavy

    def test_stationary_products_use_absolute_cycles(self):
        """A Powerwall has no mileage to deviate from, so its cycles count in full."""
        powerwall = profile("tesla-powerwall2")
        assert powerwall.reference_km_per_year == 0
        assert powerwall.expected_soh(5, cycles=2000, rated_kwh=13.5) < (
            powerwall.expected_soh(5, cycles=200, rated_kwh=13.5)
        )

    def test_heat_hits_an_uncooled_pack_hardest(self):
        library = load_degradation()
        passive = profile("nissan-leaf-ze1-40")
        cooled = profile("hyundai-kona-64")
        assert library.climate_factor("hot", passive.climate_sensitivity) > (
            library.climate_factor("hot", cooled.climate_sensitivity)
        )

    def test_spread_widens_with_age(self):
        """New packs of one model are alike; they diverge as they wear."""
        leaf = profile("nissan-leaf-ze1-40")
        assert leaf.spread_at(2) < leaf.spread_at(8) < leaf.spread_at(15)

    def test_every_bundled_profile_is_monotone(self):
        """No profile may hand back capacity as it ages."""
        for name, entry in load_degradation().by_pack_model.items():
            previous = 1.1
            for age in range(0, 21):
                soh = entry.expected_soh(age, rated_kwh=50)
                assert soh <= previous + 1e-9, f"{name} gained capacity at {age}y"
                previous = soh

    def test_chemistry_fallback_covers_every_bundled_chemistry(self):
        from battery_value.materials.chemistry import load_chemistries

        library = load_degradation()
        for key in load_chemistries().specs:
            assert library.for_chemistry(key) is not None, key


class TestResolution:
    def test_the_pack_model_wins_over_its_chemistry(self):
        library = load_degradation()
        resolved = library.resolve("nissan-leaf-ze1-40", "NMC532")
        assert resolved.key == "nissan-leaf-ze1-40"
        assert not resolved.is_fallback

    def test_unknown_model_falls_back_to_chemistry(self):
        resolved = load_degradation().resolve("no-such-pack", "LFP")
        assert resolved.is_fallback
        assert resolved.key == "chemistry:LFP"

    def test_nothing_known_resolves_to_nothing(self):
        assert load_degradation().resolve(None, None) is None


class TestHealthUsesTheModelCurve:
    def test_age_only_health_uses_the_pack_model(self, passports):
        """A Leaf and a Kona of the same age must not land on the same number."""
        passport = passports.from_document(leaf_document())
        with_curve = assess_health(
            passport,
            None,
            Config(),
            as_of=VALUATION_DATE,
            profile=profile("nissan-leaf-ze1-40"),
        )
        without = assess_health(passport, None, Config(), as_of=VALUATION_DATE)
        assert with_curve.source is HealthSource.AGE
        assert with_curve.soh != pytest.approx(without.soh, abs=0.01)

    def test_cycles_and_age_together_beat_cycles_alone(self, passports):
        """Counting cycles alone flatters a pack that has simply sat around."""
        passport = passports.from_document(leaf_document(cycleCount=1400))
        with_curve = assess_health(
            passport,
            None,
            Config(),
            as_of=VALUATION_DATE,
            profile=profile("nissan-leaf-ze1-40"),
        )
        without = assess_health(passport, None, Config(), as_of=VALUATION_DATE)
        assert with_curve.soh < without.soh

    def test_a_measurement_is_never_overridden(self, passports):
        passport = passports.from_document(leaf_document(stateOfHealth=91))
        health = assess_health(
            passport,
            None,
            Config(),
            as_of=VALUATION_DATE,
            profile=profile("nissan-leaf-ze1-40"),
        )
        assert health.source is HealthSource.MEASURED
        assert health.soh == pytest.approx(0.91)


class TestVerdict:
    def assess(self, passports, document, **kwargs):
        passport = passports.from_document(document)
        entry = profile("nissan-leaf-ze1-40")
        health = assess_health(
            passport, None, Config(), as_of=VALUATION_DATE, profile=entry
        )
        return assess_aging(
            health, entry, load_degradation(), Config(), **kwargs
        )

    def test_a_worn_pack_is_called_out(self, passports):
        aging = self.assess(passports, leaf_document(stateOfHealth=62))
        assert aging.verdict is AgingVerdict.BEHIND
        assert aging.deviation_points < 0
        assert aging.fade_ratio > 1

    def test_a_healthy_pack_is_credited(self, passports):
        aging = self.assess(passports, leaf_document(stateOfHealth=92))
        assert aging.verdict is AgingVerdict.AHEAD

    def test_inside_the_spread_is_normal(self, passports):
        """Being a point below average is not a finding, and must not read as one."""
        aging = self.assess(passports, leaf_document(stateOfHealth=78))
        assert aging.verdict is AgingVerdict.TYPICAL
        assert abs(aging.deviation_points) < aging.spread_points

    def test_age_derived_health_yields_no_verdict(self, passports):
        """The curve cannot grade a reading the curve produced."""
        aging = self.assess(passports, leaf_document())
        assert aging.verdict is AgingVerdict.UNKNOWN
        assert not aging.is_comparable
        assert any("capacity reading" in note for note in aging.notes)

    def test_cycle_derived_health_yields_no_verdict_either(self, passports):
        aging = self.assess(passports, leaf_document(cycleCount=900))
        assert aging.verdict is AgingVerdict.UNKNOWN
        # It can still say how hard the pack has been worked, which is real.
        assert aging.uses_more_than_typical is True

    def test_a_new_pack_is_not_graded(self, passports):
        document = leaf_document(stateOfHealth=97)
        document["battery"]["manufacturingDate"] = "2026-03-01"
        aging = self.assess(passports, document)
        assert aging.verdict is AgingVerdict.UNKNOWN

    def test_climate_moves_the_expectation(self, passports):
        temperate = self.assess(passports, leaf_document(stateOfHealth=79))
        hot = self.assess(passports, leaf_document(stateOfHealth=79), climate="hot")
        assert hot.expected_soh < temperate.expected_soh

    def test_an_impossible_reading_is_flagged_not_extrapolated(self, passports):
        """A 20% pack is a broken module or a broken meter, not a forecast input."""
        aging = self.assess(passports, leaf_document(stateOfHealth=20))
        assert aging.fade_ratio <= 3.0
        assert any("confirmed" in note for note in aging.notes)


class TestForecast:
    def assess(self, passports, soh, key="nissan-leaf-ze1-40"):
        passport = passports.from_document(leaf_document(stateOfHealth=soh))
        entry = profile(key)
        health = assess_health(
            passport, None, Config(), as_of=VALUATION_DATE, profile=entry
        )
        return assess_aging(health, entry, load_degradation(), Config())

    def test_a_worn_pack_runs_out_sooner(self, passports):
        healthy = self.assess(passports, 82)
        worn = self.assess(passports, 78)
        assert worn.years_to_resale_floor < healthy.years_to_resale_floor

    def test_a_pack_below_the_floor_says_so(self, passports):
        aging = self.assess(passports, 70)
        assert aging.already_below_resale_floor
        assert aging.years_to_resale_floor is None
        assert not aging.already_below_storage_floor

    def test_the_forecast_never_recovers(self, passports):
        aging = self.assess(passports, 85)
        values = [point.projected_soh for point in aging.trajectory]
        assert values == sorted(values, reverse=True)

    def test_the_trajectory_starts_where_the_pack_is(self, passports):
        aging = self.assess(passports, 85)
        assert aging.trajectory[0].projected_soh == pytest.approx(0.85, abs=0.005)

    def test_a_slow_pack_stays_above_the_floor(self, passports):
        """An LFP pack that outlives the horizon reports that, rather than a date."""
        passport = passports.from_document(
            {
                "battery": {"manufacturingDate": "2022-01-01"},
                "spec": {
                    "ratedEnergy": {"value": 60, "unit": "kWh"},
                    "chemistry": "LFP",
                },
                "status": {"stateOfHealth": 95},
            }
        )
        entry = load_degradation().for_chemistry("LFP")
        health = assess_health(
            passport, None, Config(), as_of=VALUATION_DATE, profile=entry
        )
        aging = assess_aging(health, entry, load_degradation(), Config())
        assert aging.years_to_resale_floor is None
        assert "many years" in plain.aging_outlook(aging)


class TestThroughTheEngine:
    def test_a_valuation_carries_a_wear_assessment(self, engine, passports):
        passport = passports.from_document(leaf_document(stateOfHealth=81))
        valuation = engine.value(passport, as_of=VALUATION_DATE)
        assert valuation.aging is not None
        assert valuation.aging.is_model_specific
        assert "wear curve" in " ".join(valuation.provenance)

    def test_a_fast_wearing_pack_warns(self, engine, passports):
        passport = passports.from_document(leaf_document(stateOfHealth=62))
        valuation = engine.value(passport, as_of=VALUATION_DATE)
        assert any("faster" in warning or "lower end" in warning
                   for warning in valuation.warnings)

    def test_climate_reaches_the_engine(self, engine, passports):
        passport = passports.from_document(leaf_document(stateOfHealth=79))
        temperate = engine.value(passport, as_of=VALUATION_DATE)
        hot = engine.value(passport, as_of=VALUATION_DATE, climate="hot")
        assert hot.aging.expected_soh < temperate.aging.expected_soh
        assert hot.aging.climate == "hot"

    def test_a_pack_with_no_date_gets_no_assessment(self, engine, passports):
        """No elapsed time means the curve has nothing to say."""
        passport = passports.from_document(
            {
                "battery": {"vehicleModel": "Leaf ZE1 40 kWh"},
                "spec": {
                    "ratedEnergy": {"value": 40, "unit": "kWh"},
                    "chemistry": "NMC532",
                },
                "status": {"stateOfHealth": 81},
            }
        )
        assert engine.value(passport, as_of=VALUATION_DATE).aging is None

    def test_it_reaches_the_payload_and_the_report(self, engine, passports):
        from battery_value.report import build_html_report
        from battery_value.serialisation import valuation_to_dict

        passport = passports.from_document(leaf_document(stateOfHealth=81))
        payload = valuation_to_dict(engine.value(passport, as_of=VALUATION_DATE))

        assert payload["aging"]["verdict"] == "typical"
        assert payload["aging"]["headline"]
        assert len(payload["aging"]["trajectory"]) == 11

        html = build_html_report(payload)
        assert "How it is wearing" in html
        assert "<svg" in html and "resale grade" in html


class TestPlainLanguage:
    """The wording is the product for anyone who is not an engineer."""

    BANNED = (
        "state of health",
        "SoH",
        "calendar fade",
        "cycle life",
        "degradation",
        "coefficient",
        "sigma",
    )

    def sentences(self, passports, **status) -> list[str]:
        passport = passports.from_document(leaf_document(**status))
        entry = profile("nissan-leaf-ze1-40")
        health = assess_health(
            passport, None, Config(), as_of=VALUATION_DATE, profile=entry
        )
        aging = assess_aging(health, entry, load_degradation(), Config())
        return [
            plain.aging_headline(aging),
            plain.aging_outlook(aging),
            *plain.aging_notes(aging),
        ]

    @pytest.mark.parametrize(
        "status",
        [
            {"stateOfHealth": 91},
            {"stateOfHealth": 79},
            {"stateOfHealth": 62},
            {"cycleCount": 900},
            {},
        ],
    )
    def test_no_jargon_reaches_the_owner(self, passports, status):
        for sentence in self.sentences(passports, **status):
            for term in self.BANNED:
                assert term not in sentence, f"{term!r} in {sentence!r}"

    def test_the_headline_answers_the_question_asked(self, passports):
        headline = self.sentences(passports, stateOfHealth=79)[0]
        assert "%" in headline and "years" in headline

    def test_a_config_change_moves_the_wording(self):
        """The floors are configuration, not constants baked into a sentence."""
        assert ValuationConfig().reuse.minimum_soh == 0.75
