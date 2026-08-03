"""Price sourcing: provider chain, units, provenance, FX and caching."""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest

from battery_worldcup.market.cache import PriceCache
from battery_worldcup.market.fx import FxRates, fallback_rates, parse_ecb_xml
from battery_worldcup.market.providers.baseline import BaselineProvider, system_price
from battery_worldcup.market.providers.csv_override import CsvOverrideProvider
from battery_worldcup.market.providers.exchange import metals_api, yahoo_provider
from battery_worldcup.market.providers.http_json import JsonPathError, extract_path
from battery_worldcup.market.providers.manual import ManualProvider
from battery_worldcup.market.resolver import build_resolver
from battery_worldcup.market.types import PriceQuality, PriceQuote
from battery_worldcup.units import MassUnit

ECB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
 xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <Cube><Cube time="2026-07-31">
    <Cube currency="USD" rate="1.0850"/>
    <Cube currency="GBP" rate="0.8400"/>
  </Cube></Cube>
</gesmes:Envelope>"""


class TestPriceQuote:
    def test_per_kg_from_tonne(self):
        quote = PriceQuote(
            form="nickel_metal",
            price=15500,
            currency="USD",
            unit=MassUnit.TONNE,
            as_of=date(2026, 5, 1),
            source="test",
            quality=PriceQuality.LIVE,
        )
        assert quote.price_per_kg == pytest.approx(15.5)

    def test_contained_price_applies_the_lce_factor(self):
        """USD 12,500/t of Li2CO3 is USD 66.5/kg of contained lithium."""
        quote = PriceQuote(
            form="lithium_carbonate",
            price=12500,
            currency="USD",
            unit=MassUnit.TONNE,
            as_of=date(2026, 5, 1),
            source="test",
            quality=PriceQuality.LIVE,
        )
        assert quote.price_per_kg == pytest.approx(12.5)
        assert quote.price_per_kg_contained() == pytest.approx(12.5 / 0.18785, rel=1e-3)

    def test_pound_priced_copper(self):
        """COMEX copper quotes per pound; mixing that up inflates value 2,200x."""
        quote = PriceQuote(
            form="copper_metal",
            price=4.50,
            currency="USD",
            unit=MassUnit.POUND,
            as_of=date(2026, 5, 1),
            source="test",
            quality=PriceQuality.DELAYED,
        )
        assert quote.price_per_kg == pytest.approx(9.92, abs=0.01)

    def test_confidence_decays_with_age(self):
        today = date(2026, 8, 1)
        fresh = PriceQuote(
            "nickel_metal", 1, "EUR", MassUnit.TONNE, today, "t", PriceQuality.LIVE
        )
        old = PriceQuote(
            "nickel_metal",
            1,
            "EUR",
            MassUnit.TONNE,
            today - timedelta(days=120),
            "t",
            PriceQuality.LIVE,
        )
        assert fresh.confidence(today) > old.confidence(today)
        assert old.is_stale(45, today)
        assert not fresh.is_stale(45, today)

    def test_currency_conversion_records_provenance(self):
        quote = PriceQuote(
            "nickel_metal", 1000, "USD", MassUnit.TONNE, date(2026, 5, 1), "t",
            PriceQuality.LIVE,
        )
        converted = quote.in_currency("EUR", 0.9)
        assert converted.price == pytest.approx(900)
        assert converted.currency == "EUR"
        assert "converted from USD" in converted.source_detail


class TestBaselineProvider:
    def test_quotes_every_bundled_material(self):
        provider = BaselineProvider()
        for form in provider.supported_forms():
            quote = provider.fetch(form)
            assert quote is not None and quote.price > 0

    def test_unknown_form_returns_none(self):
        assert BaselineProvider().fetch("unobtainium") is None

    def test_quality_is_baseline(self):
        assert BaselineProvider().fetch("nickel_metal").quality is PriceQuality.BASELINE

    def test_system_prices_available(self):
        assert system_price("new_pack_price").amount > 0
        assert system_price("turnkey_bess_price").amount > 0
        with pytest.raises(KeyError):
            system_price("nonexistent")


class TestManualProvider:
    def test_unavailable_when_empty(self):
        assert not ManualProvider().is_available()

    def test_returns_registered_price(self):
        provider = ManualProvider().add("cobalt_sulphate", 9000, "EUR")
        assert provider.is_available()
        assert provider.fetch("cobalt_sulphate").price == 9000


class TestCsvProvider:
    def test_reads_valid_rows(self, tmp_path):
        path = tmp_path / "prices.csv"
        path.write_text(
            "form,price,currency,unit,as_of,source_detail\n"
            "lithium_carbonate,12400,USD,t,2026-07-30,Fastmarkets MB-LI-0029\n",
            encoding="utf-8",
        )
        provider = CsvOverrideProvider(path)
        assert provider.is_available()
        quote = provider.fetch("lithium_carbonate")
        assert quote.price == 12400
        assert quote.quality is PriceQuality.BENCHMARK
        assert "Fastmarkets" in quote.source_detail

    def test_missing_file_is_not_fatal(self, tmp_path):
        provider = CsvOverrideProvider(tmp_path / "absent.csv")
        assert not provider.is_available()
        assert provider.fetch("nickel_metal") is None

    def test_malformed_row_skipped(self, tmp_path):
        path = tmp_path / "prices.csv"
        path.write_text(
            "form,price,currency,unit,as_of\n"
            "nickel_metal,notanumber,USD,t,2026-07-30\n"
            "copper_metal,9000,USD,t,2026-07-30\n",
            encoding="utf-8",
        )
        provider = CsvOverrideProvider(path)
        assert provider.fetch("nickel_metal") is None
        assert provider.fetch("copper_metal").price == 9000

    def test_missing_columns_rejected(self, tmp_path):
        path = tmp_path / "prices.csv"
        path.write_text("form,price\nnickel_metal,1\n", encoding="utf-8")
        assert not CsvOverrideProvider(path).is_available()


class TestHttpJsonProvider:
    def test_extract_path_walks_lists(self):
        payload = {"chart": {"result": [{"meta": {"price": 4.5}}]}}
        assert extract_path(payload, "chart.result.0.meta.price") == 4.5

    def test_extract_path_reports_failure(self):
        with pytest.raises(JsonPathError):
            extract_path({"a": 1}, "a.b.c")

    def test_parses_a_mocked_yahoo_response(self, isolated_cache):
        body = {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "regularMarketPrice": 4.52,
                            "regularMarketTime": 1785000000,
                        }
                    }
                ]
            }
        }
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
        provider = yahoo_provider(cache=isolated_cache)
        provider.client = httpx.Client(transport=transport)

        quote = provider.fetch("copper_metal")
        assert quote is not None
        assert quote.price == pytest.approx(4.52)
        assert quote.unit is MassUnit.POUND
        assert quote.quality is PriceQuality.DELAYED

    def test_inverted_feed(self, isolated_cache):
        """Vendors that quote units-per-currency need inverting into a price."""
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"rates": {"LME-NI": 0.0000625}, "date": "2026-07-30"}
            )
        )
        provider = metals_api(cache=isolated_cache)
        provider.client = httpx.Client(transport=transport)

        quote = provider.fetch("nickel_metal")
        assert quote.price == pytest.approx(16000.0)
        assert quote.as_of == date(2026, 7, 30)

    def test_http_error_returns_none_not_raise(self, isolated_cache):
        transport = httpx.MockTransport(lambda request: httpx.Response(500))
        provider = yahoo_provider(cache=isolated_cache)
        provider.client = httpx.Client(transport=transport)
        assert provider.fetch_safe("copper_metal") is None

    def test_requires_credentials(self, monkeypatch, isolated_cache):
        provider = metals_api(cache=isolated_cache)
        assert not provider.is_available()
        monkeypatch.setenv("METALS_API_KEY", "secret")
        assert provider.is_available()


class TestResolverChain:
    def test_offline_chain_is_baseline_only(self, offline_resolver):
        keys = [provider.key for provider in offline_resolver.providers]
        assert keys == ["baseline"]

    def test_manual_beats_baseline(self, isolated_cache):
        resolver = build_resolver(
            currency="EUR",
            offline=True,
            manual={"cobalt_sulphate": 99999},
            cache=isolated_cache,
        )
        quote = resolver.resolve("cobalt_sulphate")
        assert quote.source == "manual"
        assert quote.price == 99999

    def test_resolve_many_reports_missing(self, offline_resolver):
        price_set = offline_resolver.resolve_many(["nickel_metal", "unobtainium"])
        assert "nickel_metal" in price_set.quotes
        assert price_set.missing == ("unobtainium",)

    def test_prices_converted_to_target_currency(self, isolated_cache):
        resolver = build_resolver(currency="EUR", offline=True, cache=isolated_cache)
        quote = resolver.resolve("nickel_metal")
        assert quote.currency == "EUR"

    def test_provenance_is_recorded(self, offline_resolver):
        price_set = offline_resolver.resolve_many(["nickel_metal", "copper_metal"])
        assert len(price_set.provenance_lines()) == 2
        assert price_set.sources_used() == {"baseline": 2}


class TestFx:
    def test_parses_ecb_xml(self):
        rates = parse_ecb_xml(ECB_XML)
        assert rates.as_of == date(2026, 7, 31)
        assert rates.rate("USD") == pytest.approx(1.085)
        assert rates.rate("EUR") == 1.0

    def test_cross_rate(self):
        rates = parse_ecb_xml(ECB_XML)
        # 1 USD -> EUR -> GBP
        assert rates.factor("USD", "GBP") == pytest.approx(0.84 / 1.085)

    def test_same_currency_is_identity(self):
        assert parse_ecb_xml(ECB_XML).factor("USD", "USD") == 1.0

    def test_fallback_rates_available(self):
        rates = fallback_rates()
        assert rates.is_fallback
        assert rates.rate("USD") > 0

    def test_unknown_currency_raises(self):
        rates = FxRates({"USD": 1.1}, date(2026, 1, 1), "test")
        with pytest.raises(Exception, match="no FX rate"):
            rates.rate("XYZ")


class TestCache:
    def test_round_trip(self, tmp_path):
        cache = PriceCache(tmp_path)
        cache.set("k", {"a": 1})
        assert cache.get("k") == {"a": 1}

    def test_expiry(self, tmp_path):
        cache = PriceCache(tmp_path)
        cache.set("k", {"a": 1})
        assert cache.get("k", ttl_seconds=0) is None

    def test_disabled_cache_stores_nothing(self, tmp_path):
        cache = PriceCache(tmp_path, enabled=False)
        cache.set("k", {"a": 1})
        assert cache.get("k") is None

    def test_corrupt_entry_is_a_miss(self, tmp_path):
        cache = PriceCache(tmp_path)
        cache.set("k", {"a": 1})
        (tmp_path / "k.json").write_text("garbage", encoding="utf-8")
        assert cache.get("k") is None
