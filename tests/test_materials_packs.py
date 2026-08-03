"""Chemistry resolution, bill of materials, and the pack catalogue."""

from __future__ import annotations

import json

import pytest

from battery_worldcup.materials import (
    UnknownChemistryError,
    build_bom,
    load_chemistries,
    load_recovery,
    resolve_chemistry,
    try_resolve_chemistry,
)
from battery_worldcup.packs import (
    JsonDirectoryProvider,
    build_pack_resolver,
    enrich_passport,
    load_catalogue,
)
from battery_worldcup.packs.providers import PackResolver


class TestChemistryResolution:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("NMC811", "NMC811"),
            ("Li-NMC 811", "NMC811"),
            ("NCM-811", "NMC811"),
            ("nmc 6-2-2", "NMC622"),
            ("LiFePO4", "LFP"),
            ("lithium iron phosphate", "LFP"),
            ("lfp", "LFP"),
            ("NCA", "NCA"),
            ("lead acid", "LEAD_ACID"),
            ("VRLA", "LEAD_ACID"),
            ("sodium-ion", "NA_ION"),
            ("NiMH", "NIMH"),
            ("LiCoO2", "LCO"),
        ],
    )
    def test_aliases(self, raw, expected):
        assert resolve_chemistry(raw).key == expected

    def test_unknown_raises(self):
        with pytest.raises(UnknownChemistryError):
            resolve_chemistry("phlogiston cells")

    def test_try_resolve_returns_none(self):
        assert try_resolve_chemistry("phlogiston") is None
        assert try_resolve_chemistry(None) is None

    def test_every_chemistry_is_internally_consistent(self):
        for spec in load_chemistries().specs.values():
            assert spec.typical_pack_kg_per_kwh > 0, spec.key
            assert spec.typical_cycle_life_to_80pct > 0, spec.key
            assert 0 <= spec.second_life_suitability <= 1, spec.key
            assert spec.material_intensity_kg_per_kwh, spec.key
            # Modelled material mass must fit inside the modelled pack mass.
            intensity = sum(spec.material_intensity_kg_per_kwh.values())
            assert intensity < spec.typical_pack_kg_per_kwh, spec.key


class TestRecoveryData:
    def test_every_process_loads(self):
        library = load_recovery()
        assert library.processes
        for process in library.processes.values():
            for element in process.elements.values():
                assert 0 <= element.recovery_rate <= 1
                assert 0 <= element.payable_fraction <= 1

    def test_value_yield_is_the_product(self):
        recovery = load_recovery().get("hydrometallurgical").recovery_for("Ni")
        assert recovery.value_yield == pytest.approx(
            recovery.recovery_rate * recovery.payable_fraction
        )

    def test_pyromet_loses_lithium(self):
        """Smelting sends lithium to slag; the dataset must reflect that."""
        assert load_recovery().get("pyrometallurgical").recovery_for("Li").recovery_rate == 0

    def test_every_traded_form_referenced_is_priceable(self):
        from battery_worldcup.compounds import TRADED_FORMS

        for process in load_recovery().processes.values():
            for element in process.elements.values():
                assert element.traded_form in TRADED_FORMS

    def test_process_family_coverage(self):
        """Every chemistry family must have at least one commercial route."""
        library = load_recovery()
        for spec in load_chemistries().specs.values():
            assert library.processes_for(spec), spec.key

    def test_damaged_pack_costs_more_to_move(self):
        logistics = load_recovery().logistics
        assert logistics.cost_eur(300, "damaged") > logistics.cost_eur(300, "healthy")

    def test_minimum_freight_charge_applies(self):
        logistics = load_recovery().logistics
        assert logistics.cost_eur(1.0, "healthy") == logistics.minimum_charge_eur


class TestBillOfMaterials:
    def test_declared_masses_win(self):
        chemistry = resolve_chemistry("NMC811")
        bom = build_bom(
            chemistry=chemistry,
            rated_kwh=75,
            pack_mass_kg=480,
            declared_masses_kg={"Co": 6.9, "Ni": 55.2},
        )
        assert bom.mass_of("Co") == 6.9
        assert bom.lines["Co"].is_declared
        assert not bom.lines["Li"].is_declared

    def test_declared_fraction(self):
        bom = build_bom(
            chemistry=resolve_chemistry("NMC811"),
            rated_kwh=75,
            pack_mass_kg=480,
            declared_masses_kg={"Ni": 55.2},
        )
        assert 0 < bom.declared_fraction < 1

    def test_mass_balance(self):
        bom = build_bom(
            chemistry=resolve_chemistry("NMC811"), rated_kwh=75, pack_mass_kg=480
        )
        assert bom.payable_mass_kg + bom.inert_mass_kg == pytest.approx(480)

    def test_estimates_mass_when_absent(self):
        bom = build_bom(chemistry=resolve_chemistry("LFP"), rated_kwh=60)
        assert bom.pack_mass_kg == pytest.approx(60 * 7.0)
        assert any("pack mass not declared" in w for w in bom.warnings)

    def test_structural_metals_scale_with_mass_active_do_not(self):
        """Cathode content follows energy; enclosure and busbars follow mass."""
        light = build_bom(
            chemistry=resolve_chemistry("NMC811"), rated_kwh=75, pack_mass_kg=400
        )
        heavy = build_bom(
            chemistry=resolve_chemistry("NMC811"), rated_kwh=75, pack_mass_kg=520
        )
        assert heavy.mass_of("Al") > light.mass_of("Al")
        assert heavy.mass_of("Ni") == pytest.approx(light.mass_of("Ni"))

    def test_extreme_mass_is_clamped(self):
        bom = build_bom(
            chemistry=resolve_chemistry("NMC811"), rated_kwh=75, pack_mass_kg=1200
        )
        assert bom.mass_scale_applied <= 1.40
        assert any("clamped" in w for w in bom.warnings)

    def test_over_declared_composition_warns(self):
        bom = build_bom(
            chemistry=resolve_chemistry("NMC811"),
            rated_kwh=75,
            pack_mass_kg=100,
            declared_masses_kg={"Ni": 90.0, "Co": 60.0},
        )
        assert bom.inert_mass_kg == 0.0
        assert any("exceed declared pack mass" in w for w in bom.warnings)

    def test_rejects_non_positive_energy(self):
        with pytest.raises(ValueError):
            build_bom(chemistry=resolve_chemistry("LFP"), rated_kwh=0)


class TestPackCatalogue:
    def test_catalogue_loads(self):
        catalogue = load_catalogue()
        assert len(catalogue.models) >= 15

    def test_component_masses_balance_to_pack_mass(self):
        for model in load_catalogue().models:
            total = sum(component.total_mass_kg for component in model.components)
            assert total == pytest.approx(model.pack_mass_kg, rel=1e-6), model.key

    def test_every_model_chemistry_resolves(self):
        for model in load_catalogue().models:
            assert try_resolve_chemistry(model.chemistry) is not None, model.key

    def test_match_by_vehicle_model(self, passports):
        passport = passports.from_document(
            {"battery": {"vehicleModel": "Nissan Leaf ZE1 40 kWh"}}
        )
        match = load_catalogue().match(passport)
        assert match is not None
        assert match.model.key == "nissan-leaf-ze1-40"
        assert match.is_confident

    def test_match_by_manufacturer_and_energy(self, passports):
        passport = passports.from_document(
            {
                "generalInformation": {"manufacturerName": "BMW", "batteryModel": "i3"},
                "performanceAndDurability": {
                    "ratedCapacity": {"value": 33.2, "unit": "kWh"}
                },
            }
        )
        match = load_catalogue().match(passport)
        assert match is not None
        assert match.model.key == "bmw-i3-94ah"

    def test_wrong_size_pack_is_not_matched(self, passports):
        """A 200 kWh Nissan pack is not a Leaf, whatever the name says."""
        passport = passports.from_document(
            {
                "battery": {"vehicleModel": "Nissan Leaf"},
                "spec": {"ratedEnergy": {"value": 200, "unit": "kWh"}},
            }
        )
        match = load_catalogue().match(passport)
        if match is not None:
            assert not match.is_confident

    def test_no_match_for_unknown_vehicle(self, passports):
        passport = passports.from_document(
            {"battery": {"vehicleModel": "Imaginary Motors Zephyr"}}
        )
        assert load_catalogue().match(passport) is None


class TestEnrichment:
    def test_fills_gaps(self, passports):
        passport = passports.from_document(
            {"battery": {"vehicleModel": "Nissan Leaf ZE1 40 kWh"},
             "status": {"stateOfHealth": 78}}
        )
        match = load_catalogue().match(passport)
        result = enrich_passport(passport, match)

        assert result.was_enriched
        assert passport.rated_kwh == 40.0
        assert passport.technical.chemistry.key == "NMC532"
        assert passport.technical.pack_mass_kg == 303.0
        assert result.provenance_lines()

    def test_never_overwrites_declared_values(self, passports):
        """A passport that declares 35 kWh keeps 35 kWh, not the catalogue's 40."""
        passport = passports.from_document(
            {
                "battery": {"vehicleModel": "Nissan Leaf ZE1 40 kWh"},
                "spec": {"ratedEnergy": {"value": 35, "unit": "kWh"},
                         "batteryMass": {"value": 290, "unit": "kg"}},
            }
        )
        enrich_passport(passport, load_catalogue().match(passport))
        assert passport.rated_kwh == 35.0
        assert passport.technical.pack_mass_kg == 290.0

    def test_no_match_leaves_passport_alone(self, passports):
        passport = passports.from_document({"battery": {"vehicleModel": "Unknown"}})
        result = enrich_passport(passport, None)
        assert not result.was_enriched
        assert result.pack_model is None


class TestPackProviders:
    def test_bundled_chain_available(self):
        resolver = build_pack_resolver()
        assert any("bundled" in line for line in resolver.describe_chain())

    def test_local_directory_layer(self, tmp_path, passports):
        (tmp_path / "fleet.json").write_text(
            json.dumps(
                [
                    {
                        "key": "acme-500",
                        "label": "Acme Hauler 500",
                        "manufacturer": "Acme",
                        "chemistry": "LFP",
                        "rated_kwh": 500.0,
                        "pack_mass_kg": 3400.0,
                        "module_count": 40,
                        "vehicle_models": ["Acme Hauler 500"],
                        "used_module_value_eur": 300.0,
                    }
                ]
            ),
            encoding="utf-8",
        )
        provider = JsonDirectoryProvider(tmp_path)
        assert provider.is_available()

        passport = passports.from_document(
            {"battery": {"vehicleModel": "Acme Hauler 500"}}
        )
        match = PackResolver(providers=[provider]).find(passport)
        assert match is not None
        assert match.model.key == "acme-500"
        assert match.model.components

    def test_invalid_local_model_skipped(self, tmp_path):
        (tmp_path / "bad.json").write_text(
            json.dumps([{"key": "broken"}]), encoding="utf-8"
        )
        assert not JsonDirectoryProvider(tmp_path).is_available()

    def test_absent_directory_is_not_fatal(self, tmp_path):
        assert not JsonDirectoryProvider(tmp_path / "nope").is_available()
