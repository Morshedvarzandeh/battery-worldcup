"""Keeping valuations on record, so they can be handed over later."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from battery_value.serialisation import valuation_to_dict
from battery_value.store import (
    ValuationStore,
    generate_reference,
    normalise_reference,
)

VALUATION_DATE = __import__("datetime").date(2026, 8, 1)


@pytest.fixture
def payload(engine, eu_dpp_document, passports):
    passport = passports.from_document(eu_dpp_document)
    return valuation_to_dict(engine.value(passport, as_of=VALUATION_DATE)), passport


@pytest.fixture
def store(tmp_path):
    return ValuationStore(tmp_path / "records.sqlite3")


class TestReferences:
    def test_shape(self):
        reference = generate_reference()
        assert reference.startswith("BV-")
        assert len(reference) == len("BV-XXXX-XXXX")

    def test_avoids_ambiguous_characters(self):
        """These get read aloud down a phone line and typed back in."""
        for _ in range(200):
            body = generate_reference().replace("BV-", "").replace("-", "")
            assert not set(body) & set("01OI")

    def test_references_are_unique(self):
        assert len({generate_reference() for _ in range(2000)}) == 2000

    @pytest.mark.parametrize(
        "typed",
        ["BV-7K2P-M4X9", "bv-7k2p-m4x9", "BV7K2PM4X9", "bv 7k2p m4x9", "7K2P-M4X9"],
    )
    def test_normalisation_accepts_however_it_was_typed(self, typed):
        assert normalise_reference(typed) == "BV-7K2P-M4X9"

    def test_malformed_reference_does_not_guess(self):
        """A wrong reference must miss, not resolve to something else."""
        assert normalise_reference("nonsense") != "BV-7K2P-M4X9"


class TestSaveAndRetrieve:
    def test_round_trip_is_byte_identical(self, store, payload):
        body, passport = payload
        record = store.save(body, passport=passport)
        assert record is not None

        restored = store.get(record.reference)
        assert restored is not None
        assert restored.payload == body

    def test_reference_is_written_into_the_payload(self, store, payload):
        body, passport = payload
        record = store.save(body, passport=passport)
        assert body["reference"] == record.reference

    def test_retrieval_does_not_recompute(self, store, payload, engine, passports,
                                          eu_dpp_document):
        """The stored answer must survive a change in market prices."""
        body, passport = payload
        record = store.save(body, passport=passport)
        original = record.residual_value

        # A later valuation at different prices must not disturb the record.
        from battery_value.market.resolver import build_resolver
        from battery_value.valuation.config import ValuationConfig
        from battery_value.valuation.engine import ValuationEngine

        shocked = ValuationEngine(
            config=ValuationConfig(currency="EUR"),
            prices=build_resolver(
                currency="EUR",
                offline=True,
                manual={"cobalt_sulphate": 90000, "nickel_sulphate": 40000},
            ),
        )
        newer = shocked.value(
            passports.from_document(eu_dpp_document), as_of=VALUATION_DATE
        )
        assert newer.residual_value.amount != original

        assert store.get(record.reference).residual_value == original

    def test_sloppy_reference_retrieves(self, store, payload):
        body, passport = payload
        record = store.save(body, passport=passport)
        typed_badly = record.reference.replace("-", "").lower()
        assert store.get(typed_badly).reference == record.reference

    def test_unknown_reference_returns_none(self, store):
        assert store.get("BV-ZZZZ-ZZZZ") is None

    def test_metadata_is_indexed(self, store, payload):
        body, passport = payload
        record = store.save(body, passport=passport)
        assert record.battery_label == "Nissan Leaf ZE1 40 kWh"
        assert record.currency == "EUR"
        assert record.pack_model_key == "nissan-leaf-ze1-40"
        assert record.recommended_pathway


class TestHistory:
    def test_recent_is_newest_first(self, store, payload):
        body, passport = payload
        first = store.save(dict(body, reference=None), passport=passport)
        second = store.save(dict(body, reference=None), passport=passport)
        references = [record.reference for record in store.recent()]
        assert set(references) == {first.reference, second.reference}

    def test_find_by_serial_number(self, store, payload):
        body, passport = payload
        passport.identity.serial_number = "PACK-000123"
        record = store.save(body, passport=passport)

        found = store.find_by_battery("PACK-000123")
        assert [r.reference for r in found] == [record.reference]

    def test_find_by_unknown_battery_is_empty(self, store, payload):
        body, passport = payload
        store.save(body, passport=passport)
        assert store.find_by_battery("NOT-A-PACK") == []

    def test_one_pack_accumulates_a_history(self, store, payload):
        """Re-valuing the same pack keeps both, because prices move."""
        body, passport = payload
        passport.identity.serial_number = "PACK-42"
        store.save(dict(body, reference=None), passport=passport)
        store.save(dict(body, reference=None), passport=passport)
        assert len(store.find_by_battery("PACK-42")) == 2

    def test_count(self, store, payload):
        body, passport = payload
        assert store.count() == 0
        store.save(body, passport=passport)
        assert store.count() == 1


class TestHousekeeping:
    def test_delete(self, store, payload):
        body, passport = payload
        record = store.save(body, passport=passport)
        assert store.delete(record.reference) is True
        assert store.get(record.reference) is None
        assert store.delete(record.reference) is False

    def test_prune_keeps_recent_records(self, store, payload):
        body, passport = payload
        record = store.save(body, passport=passport)
        assert store.prune(older_than_days=30) == 0
        assert store.get(record.reference) is not None

    def test_prune_removes_old_records(self, store, payload):
        body, passport = payload
        record = store.save(body, passport=passport)

        stale = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        with store._connect() as connection:
            connection.execute(
                "UPDATE valuations SET created_at = ? WHERE reference = ?",
                (stale, record.reference),
            )

        assert store.prune(older_than_days=365) == 1
        assert store.get(record.reference) is None

    def test_zero_retention_is_a_no_op(self, store, payload):
        """Guards against silently wiping the store."""
        body, passport = payload
        store.save(body, passport=passport)
        assert store.prune(older_than_days=0) == 0
        assert store.count() == 1


class TestDisabledStore:
    def test_nothing_is_written(self, tmp_path, payload):
        body, passport = payload
        store = ValuationStore(tmp_path / "off.sqlite3", enabled=False)

        assert store.save(body, passport=passport) is None
        assert store.get("BV-AAAA-AAAA") is None
        assert store.recent() == []
        assert store.count() == 0
        assert not (tmp_path / "off.sqlite3").exists()

    def test_env_var_disables_the_default_store(self, monkeypatch):
        from battery_value import store as store_module

        monkeypatch.setenv("BV_STORE_ENABLED", "0")
        store_module.reset_default_store()
        assert store_module.default_store().enabled is False
        store_module.reset_default_store()


class TestFailureHandling:
    def test_unwritable_path_does_not_lose_the_answer(self, tmp_path, payload):
        """A broken store must never cost the customer their valuation."""
        body, passport = payload
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("", encoding="utf-8")

        store = ValuationStore(blocker / "nested" / "records.sqlite3")
        assert store.save(body, passport=passport) is None
        assert store.get("BV-AAAA-AAAA") is None
