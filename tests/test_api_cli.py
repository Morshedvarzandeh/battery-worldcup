"""HTTP API and command-line interface."""

from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from battery_value.api.app import app  # noqa: E402
from battery_value.cli import main  # noqa: E402


@pytest.fixture
def client():
    return TestClient(app)


class TestApi:
    def test_health(self, client):
        body = client.get("/v1/health").json()
        assert body["status"] == "ok"
        assert body["chemistries"] > 0
        assert body["pack_models"] > 0

    def test_index_serves_the_ui(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "What is your battery worth?" in response.text

    def test_scan_returns_a_passport(self, client, eu_dpp_document):
        body = client.post("/v1/scan", json={"document": eu_dpp_document}).json()
        assert body["derived"]["rated_kwh"] == 40.0
        assert body["derived"]["chemistry"] == "NMC532"
        assert body["derived"]["completeness"]["score"] > 0.8

    def test_value_returns_every_pathway(self, client, eu_dpp_document):
        response = client.post(
            "/v1/value",
            json={"document": eu_dpp_document, "offline": True, "currency": "EUR"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["residual_value"]["currency"] == "EUR"
        assert len(body["pathways"]) == 4
        assert body["recommended_pathway"]
        assert body["bill_of_materials"]["lines"]
        assert body["prices"]["quotes"]

    def test_value_in_another_currency(self, client, eu_dpp_document):
        eur = client.post(
            "/v1/value", json={"document": eu_dpp_document, "offline": True}
        ).json()
        usd = client.post(
            "/v1/value",
            json={"document": eu_dpp_document, "offline": True, "currency": "USD"},
        ).json()
        assert usd["residual_value"]["currency"] == "USD"
        assert usd["residual_value"]["amount"] > eur["residual_value"]["amount"]

    def test_manual_prices_are_used(self, client, eu_dpp_document):
        body = client.post(
            "/v1/value",
            json={
                "document": eu_dpp_document,
                "offline": True,
                "manual_prices": {"cobalt_sulphate": 50000},
            },
        ).json()
        sources = body["prices"]["sources_used"]
        assert sources.get("manual", 0) >= 1

    def test_empty_request_rejected(self, client):
        assert client.post("/v1/value", json={}).status_code == 422

    def test_unresolvable_payload_rejected(self, client):
        response = client.post("/v1/value", json={"payload": "PACK-DOES-NOT-EXIST"})
        assert response.status_code == 422
        assert "detail" in response.json()

    def test_private_host_refused_by_default(self, client):
        response = client.post(
            "/v1/value", json={"payload": "http://127.0.0.1:9/passport"}
        )
        assert response.status_code == 422

    def test_prices_endpoint(self, client):
        body = client.get("/v1/prices?offline=true").json()
        assert body["quotes"]
        for quote in body["quotes"]:
            assert quote["price"] > 0
            assert 0 < quote["contained_fraction"] <= 1
            # Contained-element price = form price per kg / contained fraction,
            # so a salt always prices above the raw form and a pure metal equals it.
            per_kg = quote["price"] / (1000 if quote["unit"] == "t" else 1)
            assert quote["price_per_kg_contained"] == pytest.approx(
                per_kg / quote["contained_fraction"], rel=1e-3
            )

    def test_salts_price_above_their_traded_form(self, client):
        """Lithium carbonate is only 18.8% lithium, so contained Li costs 5.3x more."""
        quotes = {q["form"]: q for q in client.get("/v1/prices?offline=true").json()["quotes"]}
        carbonate = quotes["lithium_carbonate"]
        assert carbonate["price_per_kg_contained"] > carbonate["price"] / 1000 * 5
        assert quotes["nickel_metal"]["contained_fraction"] == 1.0

    def test_packs_endpoint(self, client):
        body = client.get("/v1/packs").json()
        assert body["count"] >= 15
        assert all(model["components"] for model in body["models"])

    def test_packs_search(self, client):
        body = client.get("/v1/packs?search=leaf").json()
        assert body["count"] >= 1
        assert all("leaf" in json.dumps(m).lower() for m in body["models"])

    def test_single_pack(self, client):
        body = client.get("/v1/packs/nissan-leaf-ze1-40").json()
        assert body["rated_kwh"] == 40.0

    def test_unknown_pack_404(self, client):
        assert client.get("/v1/packs/not-a-pack").status_code == 404

    def test_providers_diagnostics(self, client):
        body = client.get("/v1/providers?offline=true").json()
        assert body["prices"]
        assert body["packs"]
        assert body["baseline_snapshot_date"]

    def test_openapi_schema_builds(self, client):
        assert client.get("/openapi.json").status_code == 200

    def test_value_includes_plain_language(self, client, eu_dpp_document):
        """The end-user view must not have to invent its own wording."""
        body = client.post(
            "/v1/value", json={"document": eu_dpp_document, "offline": True}
        ).json()
        assert body["plain"]["headline"]
        assert body["plain"]["confidence"]["label"]
        assert body["plain"]["why"]
        assert "NMC" not in body["plain"]["chemistry"]
        for pathway in body["pathways"]:
            assert pathway["friendly_label"]
            assert pathway["explanation"]

    def test_report_is_a_downloadable_file(self, client, eu_dpp_document):
        response = client.post(
            "/v1/report", json={"document": eu_dpp_document, "offline": True}
        )
        assert response.status_code == 200
        disposition = response.headers["content-disposition"]
        assert disposition.startswith("attachment;")
        assert ".html" in disposition
        assert response.text.startswith("<!doctype html>")

    def test_report_can_omit_the_technical_section(self, client, eu_dpp_document):
        payload = {"document": eu_dpp_document, "offline": True}
        full = client.post("/v1/report", json=payload).text
        summary = client.post("/v1/report?technical=false", json=payload).text
        assert len(summary) < len(full)

    def test_decode_returns_the_payload(self, client, qr_image_bytes):
        response = client.post(
            "/v1/decode", files={"file": ("qr.png", qr_image_bytes, "image/png")}
        )
        assert response.status_code == 200
        assert "batteryPassport" in response.json()["payload"]

    def test_decode_rejects_a_non_image(self, client):
        response = client.post(
            "/v1/decode", files={"file": ("x.png", b"not an image", "image/png")}
        )
        assert response.status_code == 422

    def test_value_from_a_photo(self, client, qr_image_bytes):
        """The main path on a phone: photograph the code, get a number."""
        response = client.post(
            "/v1/value/image?offline=true",
            files={"file": ("qr.png", qr_image_bytes, "image/png")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["residual_value"]["amount"] != 0
        assert body["plain"]["headline"]


class TestCli:
    def test_value_renders_a_report(self, tmp_path, capsys, eu_dpp_document):
        path = tmp_path / "passport.json"
        path.write_text(json.dumps(eu_dpp_document), encoding="utf-8")

        assert main(["value", "--file", str(path), "--offline"]) == 0
        output = capsys.readouterr().out
        assert "RESIDUAL VALUE" in output
        assert "PATHWAYS" in output
        assert "BILL OF MATERIALS" in output

    def test_value_json_output(self, tmp_path, capsys, eu_dpp_document):
        path = tmp_path / "passport.json"
        path.write_text(json.dumps(eu_dpp_document), encoding="utf-8")

        assert main(["value", "--file", str(path), "--offline", "--json"]) == 0
        body = json.loads(capsys.readouterr().out)
        assert body["residual_value"]["amount"] is not None
        assert len(body["pathways"]) == 4

    def test_scan_outputs_passport_json(self, tmp_path, capsys, eu_dpp_document):
        path = tmp_path / "passport.json"
        path.write_text(json.dumps(eu_dpp_document), encoding="utf-8")

        assert main(["scan", "--file", str(path)]) == 0
        body = json.loads(capsys.readouterr().out)
        assert body["derived"]["chemistry"] == "NMC532"

    def test_prices_listing(self, capsys):
        assert main(["prices", "--offline"]) == 0
        output = capsys.readouterr().out
        assert "per kg contained" in output
        assert "lithium_carbonate" in output

    def test_packs_listing(self, capsys):
        assert main(["packs"]) == 0
        assert "nissan-leaf-ze1-40" in capsys.readouterr().out

    def test_packs_search(self, capsys):
        assert main(["packs", "--search", "bmw"]) == 0
        output = capsys.readouterr().out
        assert "bmw-i3" in output
        assert "nissan" not in output

    def test_error_exits_nonzero(self, tmp_path, capsys):
        assert main(["value", "--file", str(tmp_path / "missing.json")]) == 1
        assert "error:" in capsys.readouterr().err

    def test_writes_a_report_file(self, tmp_path, capsys, eu_dpp_document):
        passport = tmp_path / "passport.json"
        passport.write_text(json.dumps(eu_dpp_document), encoding="utf-8")
        out = tmp_path / "report.html"

        assert main([
            "value", "--file", str(passport), "--offline",
            "--report", str(out), "--quiet",
        ]) == 0
        assert out.exists()
        assert out.read_text(encoding="utf-8").startswith("<!doctype html>")
        assert capsys.readouterr().out == ""

    def test_report_into_a_directory_gets_a_generated_name(
        self, tmp_path, eu_dpp_document
    ):
        passport = tmp_path / "passport.json"
        passport.write_text(json.dumps(eu_dpp_document), encoding="utf-8")

        assert main([
            "value", "--file", str(passport), "--offline",
            "--report", str(tmp_path), "--quiet",
        ]) == 0
        reports = list(tmp_path.glob("battery-value-*.html"))
        assert len(reports) == 1

    def test_qr_payload_with_inline_json(self, capsys, eu_dpp_document):
        payload = json.dumps(eu_dpp_document)
        assert main(["value", "--qr", payload, "--offline", "--json"]) == 0
        body = json.loads(capsys.readouterr().out)
        assert body["battery"]["rated_kwh"] == 40.0
