"""Reading pack and vehicle data from battery-data.

The Postgres path needs a live database, so it is skipped unless
BV_BATTERY_DATA_DSN points at one. The mapping and attribution rules are the
part worth testing everywhere, so those run against fixtures.
"""

from __future__ import annotations

import json
import os

import httpx
import pytest

from battery_value.packs.battery_data import (
    MINIMUM_ATTRIBUTION_CONFIDENCE,
    BatteryDataHttpProvider,
    BatteryDataPostgresProvider,
    _row_to_document,
    battery_data_providers,
)

COMPLETE_ROW = {
    "product_uid": "pack/nissan/nissan-leaf-ze1-40",
    "model_number": "nissan-leaf-ze1-40",
    "manufacturer": "Nissan Motor",
    "chemistry": "NMC532",
    "rated_kwh": 40.0,
    "pack_mass_kg": 303.0,
    "module_count": 24,
    "vehicle_models": ["Leaf ZE1", "Leaf 40 kWh"],
    "attribution_confidence": 0.85,
    "attribution_basis": "teardown",
    "used_module_value_eur": 105.0,
    "oem_replacement_price_eur_per_kwh": 300.0,
}


class TestRowMapping:
    def test_complete_row(self):
        document = _row_to_document(dict(COMPLETE_ROW))
        assert document["key"] == "nissan-leaf-ze1-40"
        assert document["rated_kwh"] == 40.0
        assert document["pack_mass_kg"] == 303.0
        assert document["vehicle_models"] == ["Leaf ZE1", "Leaf 40 kWh"]
        assert document["source"] == "battery-data"

    def test_accepts_either_uid_column(self):
        """SQL aliases it 'key'; the HTTP API calls it 'product_uid'."""
        as_key = dict(COMPLETE_ROW)
        as_key["key"] = as_key.pop("product_uid")
        assert _row_to_document(as_key) == _row_to_document(dict(COMPLETE_ROW))

    @pytest.mark.parametrize("missing", ["chemistry", "rated_kwh", "pack_mass_kg"])
    def test_incomplete_rows_are_dropped(self, missing):
        """Better to say the pack is unknown than to invent a default."""
        row = dict(COMPLETE_ROW)
        row[missing] = None
        assert _row_to_document(row) is None

    def test_weakly_attributed_rows_are_dropped(self):
        """A guessed pack identity must not become a confident price."""
        for basis in ("community_reported", "inferred"):
            row = dict(COMPLETE_ROW, attribution_basis=basis)
            assert _row_to_document(row) is None, basis

    def test_low_confidence_rows_are_dropped(self):
        row = dict(COMPLETE_ROW)
        row["attribution_confidence"] = MINIMUM_ATTRIBUTION_CONFIDENCE - 0.01
        assert _row_to_document(row) is None

    def test_confidence_maps_to_catalogue_bands(self):
        assert _row_to_document(dict(COMPLETE_ROW, attribution_confidence=0.85))[
            "confidence"
        ] == "high"
        assert _row_to_document(dict(COMPLETE_ROW, attribution_confidence=0.65))[
            "confidence"
        ] == "medium"
        assert _row_to_document(dict(COMPLETE_ROW, attribution_confidence=0.55))[
            "confidence"
        ] == "low"


class TestHttpProvider:
    def _provider(self, payload, status=200):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(status, json=payload)
        )
        provider = BatteryDataHttpProvider("http://battery-data.test")
        provider.client = httpx.Client(transport=transport)
        return provider

    def test_reads_the_envelope(self, passports):
        provider = self._provider({"meta": {}, "data": [COMPLETE_ROW]})
        assert len(provider.fetch_documents()) == 1

        passport = passports.from_document(
            {"battery": {"vehicleModel": "Leaf ZE1"}, "status": {"stateOfHealth": 80}}
        )
        match = provider.find(passport)
        assert match is not None
        assert match.model.key == "nissan-leaf-ze1-40"

    def test_unavailable_without_a_url(self, monkeypatch):
        monkeypatch.delenv("BV_BATTERY_DATA_URL", raising=False)
        assert not BatteryDataHttpProvider().is_available()

    def test_server_error_degrades_to_none(self, passports):
        """A dead service must not break a scan."""
        provider = self._provider({}, status=500)
        passport = passports.from_document({"battery": {"vehicleModel": "Leaf ZE1"}})
        assert provider.find(passport) is None


class TestPostgresProvider:
    def test_unavailable_without_a_dsn(self, monkeypatch):
        monkeypatch.delenv("BV_BATTERY_DATA_DSN", raising=False)
        assert not BatteryDataPostgresProvider().is_available()

    def test_chain_is_empty_when_unconfigured(self, monkeypatch):
        """A fresh clone must not require a database."""
        monkeypatch.delenv("BV_BATTERY_DATA_DSN", raising=False)
        monkeypatch.delenv("BV_BATTERY_DATA_URL", raising=False)
        assert battery_data_providers() == []


@pytest.mark.skipif(
    not os.environ.get("BV_BATTERY_DATA_DSN"),
    reason="needs a live battery-data database",
)
class TestAgainstLiveDatabase:
    def test_reads_pack_models(self):
        documents = BatteryDataPostgresProvider().fetch_documents()
        assert documents
        for document in documents:
            assert document["rated_kwh"] > 0
            assert document["pack_mass_kg"] > 0
            assert document["chemistry"]

    def test_matches_a_passport(self, passports):
        passport = passports.from_document(
            {"battery": {"vehicleModel": "Nissan Leaf ZE1 40 kWh"},
             "status": {"stateOfHealth": 81}}
        )
        match = BatteryDataPostgresProvider().find(passport)
        assert match is not None and match.is_confident

    def test_snapshot_round_trips(self, tmp_path):
        """battery-value JSON -> battery-data -> battery-value JSON."""
        from battery_value.packs.battery_data import refresh_snapshot

        destination = tmp_path / "pack_models.json"
        count, _ = refresh_snapshot(path=destination)
        assert count > 0

        written = json.loads(destination.read_text(encoding="utf-8"))
        assert written["generated_from"].startswith("battery-data")
        assert len(written["models"]) == count
        # The file's own structure must survive a sync.
        assert "component_templates" in written
        assert "default_mass_split" in written
