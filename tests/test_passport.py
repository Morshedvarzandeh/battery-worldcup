"""Passport ingestion: carriers, adapters and the awkward real-world shapes."""

from __future__ import annotations

import json

import pytest

from battery_value.errors import PassportError, UnknownCarrierError
from battery_value.passport import (
    BatteryPassport,
    CarrierKind,
    PassportResolver,
    parse_carrier,
    parse_document,
)
from battery_value.passport.adapters import detect_adapter
from battery_value.passport.models import PackCondition


class TestCarrierParsing:
    def test_gs1_digital_link(self):
        carrier = parse_carrier("https://id.gs1.org/01/09506000134376/21/AB123")
        assert carrier.kind is CarrierKind.GS1_DIGITAL_LINK
        assert carrier.identifiers["gtin"] == "09506000134376"
        assert carrier.identifiers["serial"] == "AB123"
        assert carrier.primary_identifier == "AB123"

    def test_plain_url(self):
        carrier = parse_carrier("https://dpp.example.com/battery/xyz")
        assert carrier.kind is CarrierKind.URL
        assert carrier.is_fetchable

    def test_inline_json(self):
        carrier = parse_carrier('{"batteryId": "Z9"}')
        assert carrier.kind is CarrierKind.INLINE_JSON
        assert carrier.inline_document == {"batteryId": "Z9"}
        assert not carrier.is_fetchable

    def test_base64_data_uri(self):
        import base64

        payload = base64.b64encode(b'{"batteryId":"D1"}').decode()
        carrier = parse_carrier(f"data:application/json;base64,{payload}")
        assert carrier.kind is CarrierKind.DATA_URI
        assert carrier.inline_document["batteryId"] == "D1"

    def test_urn(self):
        carrier = parse_carrier("urn:uuid:1234-5678")
        assert carrier.kind is CarrierKind.URN
        assert carrier.identifiers["passport_id"] == "urn:uuid:1234-5678"

    def test_bare_identifier(self):
        carrier = parse_carrier("PACK-000123")
        assert carrier.kind is CarrierKind.IDENTIFIER

    def test_empty_raises(self):
        with pytest.raises(UnknownCarrierError):
            parse_carrier("   ")

    def test_query_string_identifiers(self):
        carrier = parse_carrier("https://x.example/dpp?id=ABC&other=1")
        assert carrier.identifiers["id"] == "ABC"


class TestAdapterDetection:
    def test_eu_dpp_detected(self, eu_dpp_document):
        adapter, confidence = detect_adapter(eu_dpp_document)
        assert adapter.name == "eu_dpp"
        assert confidence > 0.5

    def test_gba_detected_by_context(self):
        adapter, _ = detect_adapter(
            {"@context": "https://gbaglobal.org/passport/v1", "batteryIdentification": {}}
        )
        assert adapter.name == "gba"

    def test_native_round_trip(self, eu_dpp_document):
        original = parse_document(eu_dpp_document)
        payload = json.loads(original.model_dump_json(exclude={"raw"}))
        adapter, confidence = detect_adapter(payload)
        assert adapter.name == "native"
        assert confidence == 1.0
        restored = adapter.parse(payload)
        assert restored.rated_kwh == original.rated_kwh
        assert restored.health.soh_fraction == original.health.soh_fraction

    def test_unknown_shape_falls_back_to_generic(self):
        adapter, _ = detect_adapter({"some": {"random": "document"}})
        assert adapter.name == "generic"

    def test_empty_document_raises(self):
        with pytest.raises(PassportError):
            detect_adapter({})


class TestFieldExtraction:
    def test_eu_dpp_fields(self, eu_dpp_document, passports):
        passport = passports.from_document(eu_dpp_document)
        assert passport.rated_kwh == 40.0
        assert passport.technical.pack_mass_kg == 303.0
        assert passport.technical.chemistry.key == "NMC532"
        assert passport.health.soh_fraction == pytest.approx(0.81)
        assert passport.health.cycle_count == 850
        assert passport.identity.manufacturing_date.year == 2019

    def test_declared_composition_from_records(self, eu_dpp_document, passports):
        """Composition given as [{substance, massKg}] must be read."""
        passport = passports.from_document(eu_dpp_document)
        declared = passport.declared_masses()
        assert declared["Co"] == pytest.approx(7.1)
        assert declared["Li"] == pytest.approx(4.0)
        assert declared["Ni"] == pytest.approx(17.4)

    def test_kwh_capacity_not_read_as_amp_hours(self, passports):
        """A kWh-tagged capacity must not be multiplied by pack voltage."""
        passport = passports.from_document(
            {
                "spec": {
                    "ratedCapacity": {"value": 75, "unit": "kWh"},
                    "nominalVoltage": 400,
                    "chemistry": "NMC811",
                }
            }
        )
        assert passport.rated_kwh == 75.0

    def test_amp_hour_capacity_uses_voltage(self, passports):
        passport = passports.from_document(
            {
                "spec": {
                    "nominalCapacity": {"value": 120, "unit": "Ah"},
                    "nominalVoltage": 400,
                    "chemistry": "LFP",
                }
            }
        )
        assert passport.rated_kwh == pytest.approx(48.0)

    def test_soh_accepts_fraction_or_percent(self, passports):
        as_fraction = passports.from_document({"status": {"stateOfHealth": 0.87}})
        as_percent = passports.from_document({"status": {"stateOfHealth": 87}})
        assert as_fraction.health.soh_fraction == pytest.approx(0.87)
        assert as_percent.health.soh_fraction == pytest.approx(0.87)

    def test_composition_as_percentages(self, passports):
        passport = passports.from_document(
            {
                "generalInformation": {"batteryMass": {"value": 400, "unit": "kg"}},
                "performanceAndDurability": {
                    "ratedCapacity": {"value": 60, "unit": "kWh"}
                },
                "materialComposition": {
                    "batteryChemistry": "NMC811",
                    "substances": [
                        {"substance": "Cobalt", "massFraction": 2.0},
                        {"substance": "Nickel", "massFraction": 14.0},
                    ],
                },
            }
        )
        declared = passport.declared_masses()
        assert declared["Co"] == pytest.approx(8.0)
        assert declared["Ni"] == pytest.approx(56.0)

    def test_remaining_capacity_derived_from_soh(self, passports):
        passport = passports.from_document(
            {"spec": {"ratedEnergy": {"value": 50, "unit": "kWh"}},
             "status": {"stateOfHealth": 80}}
        )
        assert passport.remaining_kwh == pytest.approx(40.0)

    def test_soh_derived_from_remaining_capacity(self, passports):
        passport = passports.from_document(
            {
                "spec": {"ratedEnergy": {"value": 50, "unit": "kWh"}},
                "status": {"remainingCapacity": {"value": 35, "unit": "kWh"}},
            }
        )
        assert passport.health.soh_fraction == pytest.approx(0.70)

    def test_condition_and_safety_flags(self, passports):
        passport = passports.from_document(
            {
                "status": {"packCondition": "damaged"},
                "circularity": {"safetyFlags": ["coolant leak"]},
            }
        )
        assert passport.health.condition is PackCondition.DAMAGED
        assert passport.health.has_safety_concern

    @pytest.mark.parametrize(
        "raw_date", ["2019-03-14", "14/03/2019", "14.03.2019", "2019-03", "2019"]
    )
    def test_date_formats(self, raw_date, passports):
        passport = passports.from_document(
            {"generalInformation": {"manufacturingDate": raw_date}}
        )
        assert passport.identity.manufacturing_date.year == 2019


class TestCompleteness:
    def test_complete_passport(self, eu_dpp_document, passports):
        completeness = passports.from_document(eu_dpp_document).completeness()
        assert completeness.is_valuable
        assert completeness.score == pytest.approx(1.0, abs=0.05)

    def test_sparse_passport(self, passports):
        completeness = passports.from_document({"batteryId": "X"}).completeness()
        assert not completeness.is_valuable
        assert "chemistry" in completeness.missing_required


class TestFetchSafety:
    def test_rejects_non_http_scheme(self):
        resolver = PassportResolver()
        with pytest.raises(PassportError, match="non-HTTP"):
            resolver.fetch("file:///etc/passwd")

    def test_rejects_private_address_by_default(self):
        resolver = PassportResolver()
        with pytest.raises(PassportError, match="private address"):
            resolver.fetch("http://127.0.0.1:8080/passport")

    def test_allows_private_when_opted_in(self):
        resolver = PassportResolver(allow_private_hosts=True)
        # The URL check passes; the request itself then fails, which is fine.
        with pytest.raises(PassportError, match="could not fetch"):
            resolver.fetch("http://127.0.0.1:9/passport")


class TestFileLoading:
    def test_missing_file(self, passports, tmp_path):
        with pytest.raises(PassportError, match="not found"):
            passports.from_file(tmp_path / "nope.json")

    def test_invalid_json(self, passports, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(PassportError, match="not valid JSON"):
            passports.from_file(path)

    def test_valid_file(self, passports, tmp_path, eu_dpp_document):
        path = tmp_path / "p.json"
        path.write_text(json.dumps(eu_dpp_document), encoding="utf-8")
        passport = passports.from_file(path)
        assert isinstance(passport, BatteryPassport)
        assert passport.source.kind == "file"
