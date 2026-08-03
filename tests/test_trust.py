"""Certificates, signatures, compliance readiness and the portfolio view.

The tests that matter are about the distinction the certificate exists to keep:
a measurement, a declaration and a computation are three different things, and a
document that lets them blur into each other cannot be priced by anyone.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from battery_value.portfolio import URGENT_HORIZON_YEARS, build as build_portfolio
from battery_value.serialisation import valuation_to_dict
from battery_value.trust import certificate as certificate_module
from battery_value.trust import compliance
from battery_value.trust.certificate import ClaimBasis
from battery_value.trust.signing import Signer, canonical_json, verify

FULL_PASSPORT = {
    "batteryPassport": {
        "generalInformation": {
            "batteryId": "AESC-LEAF40-0093122",
            "manufacturerName": "AESC",
            "vehicleModel": "Nissan Leaf ZE1 40 kWh",
            "manufacturingDate": "2019-03-14",
        },
        "carbonFootprint": {
            "carbonFootprintPerKwh": {"value": 62.4},
            "performanceClass": "C",
            "carbonFootprintStudy": "https://example.invalid/lca.pdf",
        },
        "supplyChainDueDiligence": {
            "dueDiligencePolicy": "https://example.invalid/policy",
            "recognisedScheme": "RMI RMAP",
            "thirdPartyAudited": True,
        },
        "materialComposition": {
            "batteryChemistry": "NMC532",
            "criticalRawMaterials": [
                {"substance": "Cobalt", "massKg": 7.1, "recycledContent": 16},
                {"substance": "Lithium", "massKg": 4.0, "recycledContent": 4},
            ],
            "origin": {"cobalt": "CD", "lithium": "AU"},
        },
        "performanceAndDurability": {
            "ratedCapacity": {"value": 40, "unit": "kWh"},
            "stateOfHealth": {"value": 81},
            "numberOfFullCycles": 850,
            "measurementDate": "2026-06-30",
        },
    }
}

BARE_PASSPORT = {
    "battery": {
        "manufacturer": "Nissan",
        "vehicleModel": "Leaf ZE1 40 kWh",
        "manufacturingDate": "2019-04-01",
    },
    "spec": {"ratedEnergy": {"value": 40, "unit": "kWh"}, "chemistry": "NMC532"},
}


@pytest.fixture(autouse=True)
def isolated_signer(tmp_path, monkeypatch):
    """A per-test key, so tests never touch a real issuing key."""
    from battery_value.trust import signing

    monkeypatch.delenv("BV_SIGNING_KEY", raising=False)
    monkeypatch.setenv("BV_SIGNING_KEY_PATH", str(tmp_path / "signing.key"))
    monkeypatch.setenv("BV_ISSUER", "test issuer")
    signing.reset_default_signer()
    yield
    signing.reset_default_signer()


@pytest.fixture
def certified(engine, passports, isolated_store):
    """A signed certificate for a fully populated passport."""

    def issue(document=FULL_PASSPORT):
        passport = passports.from_document(document)
        record = isolated_store.save(
            valuation_to_dict(engine.value(passport)), passport=passport
        )
        return certificate_module.issue(record, passport), passport, record

    return issue


class TestSigning:
    def test_a_signature_survives_a_round_trip(self):
        signer = Signer(issuer="test")
        payload = {"b": 2, "a": [1, {"z": None}]}
        signature = signer.sign(payload)
        assert verify(payload, signature)
        assert verify(payload, signature.to_dict())

    def test_any_change_breaks_it(self):
        signer = Signer(issuer="test")
        payload = {"soh": 0.81}
        signature = signer.sign(payload)
        assert not verify({"soh": 0.95}, signature)

    def test_canonical_form_ignores_key_order(self):
        """Two processes agreeing on content must produce identical bytes."""
        assert canonical_json({"a": 1, "b": 2}) == canonical_json({"b": 2, "a": 1})

    def test_a_signature_from_another_key_does_not_pass(self, tmp_path):
        """Naming someone else's public key does not let you sign as them."""
        from dataclasses import replace

        payload = {"x": 1}
        honest = Signer(issuer="a", key_path=tmp_path / "a.key")
        other = Signer(issuer="b", key_path=tmp_path / "b.key")
        assert honest.public_key != other.public_key

        impersonation = replace(
            other.sign(payload), public_key=honest.public_key, issuer="a"
        )
        assert verify(payload, honest.sign(payload))
        assert not verify(payload, impersonation)

    def test_a_configured_key_is_never_replaced(self, tmp_path, monkeypatch):
        """A generated key silently overwriting a configured one is unrecoverable."""
        first = Signer(key_path=tmp_path / "k")
        second = Signer(key_path=tmp_path / "k")
        assert first.public_key == second.public_key


class TestCertificate:
    def test_it_verifies_as_issued(self, certified):
        certificate, _, _ = certified()
        assert certificate.verify()

    def test_editing_the_health_breaks_it(self, certified):
        """The whole point: a buyer can tell the number was changed."""
        certificate, _, _ = certified()
        document = json.loads(json.dumps(certificate.to_dict()))
        for claim in document["claims"]:
            if claim["key"] == "state_of_health":
                claim["value"] = 95.0
        assert not certificate_module.from_dict(document).verify()

    def test_editing_the_price_breaks_it(self, certified):
        certificate, _, _ = certified()
        document = json.loads(json.dumps(certificate.to_dict()))
        document["valuation"]["residual_value"]["amount"] = 9999
        assert not certificate_module.from_dict(document).verify()

    def test_a_measurement_is_not_a_declaration(self, certified):
        """The distinction the certificate exists for."""
        certificate, _, _ = certified()
        assert certificate.claim("state_of_health").basis is ClaimBasis.MEASURED
        assert certificate.claim("carbon_footprint").basis is ClaimBasis.DECLARED
        assert certificate.claim("residual_value").basis is ClaimBasis.COMPUTED

    def test_an_estimated_health_is_never_shown_as_measured(self, certified):
        certificate, _, _ = certified(BARE_PASSPORT)
        assert certificate.claim("state_of_health").basis is ClaimBasis.COMPUTED
        assert "Nobody measured" in certificate.strength_in_words()

    def test_absence_is_recorded_rather_than_omitted(self, certified):
        """Silence about the carbon footprint is itself worth knowing."""
        certificate, _, _ = certified(BARE_PASSPORT)
        assert certificate.claim("carbon_footprint").basis is ClaimBasis.ABSENT

    def test_paperwork_does_not_outweigh_a_missing_measurement(self, certified):
        """A thick compliance file on an unmeasured battery is not evidence."""
        full, _, _ = certified(FULL_PASSPORT)
        bare, _, _ = certified(BARE_PASSPORT)
        assert full.evidence_strength > bare.evidence_strength
        assert "measurement" in full.strength_in_words() or "measured" in (
            full.strength_in_words()
        )

    def test_the_attestation_does_not_overclaim(self, certified):
        certificate, _, _ = certified()
        assert "does not verify the manufacturer" in certificate.attestation.lower()

    def test_it_survives_a_browser(self, certified):
        """JavaScript writes 76 where Python wrote 76.0, for the same number.

        A verifier that recomputes canonical JSON over a document a browser has
        re-serialised will call a perfectly good certificate forged. Carrying
        the signed bytes is what makes verification work off this machine at
        all, so this is the test that keeps it honest.
        """

        def as_a_browser_would(node):
            if isinstance(node, float) and node.is_integer():
                return int(node)
            if isinstance(node, dict):
                return {key: as_a_browser_would(v) for key, v in node.items()}
            if isinstance(node, list):
                return [as_a_browser_would(v) for v in node]
            return node

        certificate, _, _ = certified()
        document = as_a_browser_would(json.loads(json.dumps(certificate.to_dict())))
        assert certificate_module.from_dict(document).verify()

    def test_signing_one_thing_and_showing_another_fails(self, certified):
        """A valid signature over different content must not pass the document."""
        certificate, _, _ = certified()
        document = json.loads(json.dumps(certificate.to_dict()))
        document["subject"]["label"] = "A completely different battery"
        assert not certificate_module.from_dict(document).verify()

    def test_it_survives_json(self, certified):
        certificate, _, _ = certified()
        rebuilt = certificate_module.from_dict(
            json.loads(json.dumps(certificate.to_dict()))
        )
        assert rebuilt.verify()
        assert rebuilt.reference == certificate.reference
        assert len(rebuilt.claims) == len(certificate.claims)


class TestCompliance:
    def test_a_full_passport_has_no_gaps(self, passports):
        view = compliance.assess(passports.from_document(FULL_PASSPORT))
        assert view.gaps == ()
        assert view.score == 1.0

    def test_a_bare_passport_reports_what_is_missing(self, passports):
        view = compliance.assess(passports.from_document(BARE_PASSPORT))
        keys = {requirement.key for requirement in view.gaps}
        assert "carbon_footprint" in keys
        assert "due_diligence" in keys

    def test_a_deadline_in_the_future_is_not_a_gap(self, passports):
        """Reporting a 2031 obligation as overdue in 2026 would be noise."""
        view = compliance.assess(
            passports.from_document(BARE_PASSPORT), as_of=date(2026, 8, 1)
        )
        upcoming = {requirement.key for requirement in view.upcoming}
        assert "recycled_content" in upcoming
        assert "recycled_content" not in {r.key for r in view.gaps}

    def test_every_gap_names_whose_job_it_is(self, passports):
        """Telling a garage they are non-compliant for the OEM's paperwork is useless."""
        view = compliance.assess(passports.from_document(BARE_PASSPORT))
        for requirement in view.gaps:
            assert requirement.owner in {"manufacturer", "holder", "recycler"}
        assert "manufacturer" in view.summary()

    def test_a_missing_declaration_is_not_a_passing_score(self, passports):
        """Silence must never read as a zero shortfall."""
        passport = passports.from_document(BARE_PASSPORT)
        assert compliance.recycled_content_gap(passport) == {}

    def test_a_shortfall_is_measured_against_the_2031_minimum(self, passports):
        passport = passports.from_document(FULL_PASSPORT)
        gap = compliance.recycled_content_gap(passport)
        # Lithium is declared at 4%, against a 6% minimum.
        assert gap["Li"] == pytest.approx(2.0)
        # Cobalt is declared at 16%, exactly the minimum, so it is not a gap.
        assert "Co" not in gap


class TestPortfolio:
    def _record(self, engine, passports, isolated_store, soh, made="2019-04-01"):
        document = json.loads(json.dumps(BARE_PASSPORT))
        document["battery"]["manufacturingDate"] = made
        document["status"] = {"stateOfHealth": soh}
        passport = passports.from_document(document)
        return isolated_store.save(
            valuation_to_dict(engine.value(passport)), passport=passport
        )

    def test_it_totals_what_is_held(self, engine, passports, isolated_store):
        records = [
            self._record(engine, passports, isolated_store, soh)
            for soh in (88, 81, 76)
        ]
        book = build_portfolio(records)
        assert len(book.holdings) == 3
        assert book.value.amount == pytest.approx(
            sum(record.residual_value for record in records)
        )
        assert book.energy_kwh == pytest.approx(120)

    def test_holding_costs_money(self, engine, passports, isolated_store):
        """The number that makes waiting stop being free."""
        book = build_portfolio(
            [self._record(engine, passports, isolated_store, 81)]
        )
        assert book.annual_loss.amount > 0
        assert book.monthly_loss.amount == pytest.approx(
            book.annual_loss.amount / 12
        )
        assert 0 < book.loss_rate < 1

    def test_packs_near_the_cliff_are_singled_out(
        self, engine, passports, isolated_store
    ):
        """Below the resale floor the best route vanishes; that is a step, not a slope."""
        near = self._record(engine, passports, isolated_store, 77)
        far = self._record(engine, passports, isolated_store, 93, made="2023-01-01")
        book = build_portfolio([near, far])
        urgent = {holding.reference for holding in book.urgent}
        assert near.reference in urgent
        assert far.reference not in urgent
        assert all(
            holding.years_to_resale_floor <= URGENT_HORIZON_YEARS
            for holding in book.urgent
        )

    def test_another_currency_is_skipped_not_converted(
        self, engine, passports, isolated_store
    ):
        """Adding dollars to euros gives a total that looks right and is not."""
        record = self._record(engine, passports, isolated_store, 81)
        book = build_portfolio([record], currency="USD")
        assert book.holdings == []

    def test_the_summary_leads_with_money(self, engine, passports, isolated_store):
        book = build_portfolio(
            [self._record(engine, passports, isolated_store, 81)]
        )
        summary = book.summary()
        assert "worth" in summary and "month" in summary

    def test_concentration_finds_the_few_that_matter(
        self, engine, passports, isolated_store
    ):
        records = [
            self._record(engine, passports, isolated_store, soh)
            for soh in (95, 60, 60, 60, 60)
        ]
        book = build_portfolio(records)
        assert 1 <= book.concentration(0.8) <= len(records)


class TestHttp:
    @pytest.fixture
    def client(self, isolated_store, isolated_market):
        from fastapi.testclient import TestClient

        from battery_value.api.app import app

        return TestClient(app)

    @pytest.fixture
    def stored(self, engine, passports, isolated_store):
        passport = passports.from_document(FULL_PASSPORT)
        return isolated_store.save(
            valuation_to_dict(engine.value(passport)), passport=passport
        )

    def test_the_public_key_is_public(self, client):
        body = client.get("/v1/trust/public-key").json()
        assert body["algorithm"] == "Ed25519"
        assert body["public_key"]

    def test_certificate_then_verify(self, client, stored):
        certificate = client.get(f"/v1/certificates/{stored.reference}").json()
        assert certificate["signature"]["algorithm"] == "Ed25519"

        checked = client.post("/v1/certificates/verify", json=certificate).json()
        assert checked["valid"] is True
        assert "does not verify what the manufacturer" in checked["verdict"].lower()

    def test_a_tampered_certificate_is_refused(self, client, stored):
        certificate = client.get(f"/v1/certificates/{stored.reference}").json()
        certificate["subject"]["label"] = "Something else entirely"
        checked = client.post("/v1/certificates/verify", json=certificate).json()
        assert checked["valid"] is False

    def test_rubbish_is_a_422_not_a_500(self, client):
        response = client.post("/v1/certificates/verify", json={"nope": True})
        assert response.status_code == 422

    def test_the_verify_page_serves(self, client):
        response = client.get("/verify")
        assert response.status_code == 200
        assert "Check a battery certificate" in response.text

    def test_the_portfolio_endpoint_answers(self, client, stored):
        body = client.get("/v1/portfolio").json()
        assert body["count"] == 1
        assert body["value"] == pytest.approx(stored.residual_value)
        assert body["summary"]

    def test_a_stored_record_keeps_its_passport(self, stored):
        """A certificate reissued months later must say the same things."""
        assert stored.payload["passport"]["identity"]["battery_id"]
