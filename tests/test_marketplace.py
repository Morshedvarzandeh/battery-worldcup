"""The market: listings anchored on valuations, offers, and the price guide.

The rules worth testing are the ones that stop this being a classifieds page.
A listing cannot exist without a valuation. A stale valuation cannot back one.
A pack worth less than nothing is a disposal job, not a bargain. And a damaged
pack says so before someone turns up with a van.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from battery_value.marketplace import (
    ListingKind,
    ListingStatus,
    MarketError,
    MarketService,
)
from battery_value.marketplace.observations import (
    MINIMUM_SALES_FOR_A_PRICE,
    summarise,
    to_battery_data_sql,
)
from battery_value.marketplace.pricing import PriceVerdict, guide_price
from battery_value.serialisation import valuation_to_dict


@pytest.fixture
def leaf_valuation(engine, passports, isolated_store):
    """A stored valuation of a healthy Leaf, ready to be listed."""
    passport = passports.from_document(
        {
            "battery": {
                "manufacturer": "Nissan",
                "vehicleModel": "Leaf ZE1 40 kWh",
                "manufacturingDate": "2019-04-01",
            },
            "spec": {"ratedEnergy": {"value": 40, "unit": "kWh"}, "chemistry": "NMC532"},
            "status": {"stateOfHealth": 81},
        }
    )
    payload = valuation_to_dict(engine.value(passport))
    return isolated_store.save(payload, passport=passport)


@pytest.fixture
def damaged_valuation(engine, passports, isolated_store):
    """A pack that costs money to get rid of."""
    passport = passports.from_document(
        {
            "battery": {"manufacturingDate": "2021-01-01"},
            "spec": {"ratedEnergy": {"value": 75, "unit": "kWh"}, "chemistry": "LFP"},
            "status": {"stateOfHealth": 64, "condition": "damaged"},
        }
    )
    payload = valuation_to_dict(engine.value(passport))
    return isolated_store.save(payload, passport=passport)


@pytest.fixture
def service(isolated_market, isolated_store):
    return MarketService(market=isolated_market, valuations=isolated_store)


@pytest.fixture
def api_client(isolated_market, isolated_store):
    """A client over the real app, pointed at the per-test databases."""
    from fastapi.testclient import TestClient

    from battery_value.api.app import app

    return TestClient(app)


class TestPriceGuide:
    def test_the_guide_sits_below_the_valuation(self):
        """The buyer does the collecting, testing and reselling, so they keep a cut."""
        guide = guide_price(2000.0)
        assert guide.guide < 2000.0
        assert guide.low < guide.guide < guide.high

    def test_a_price_inside_the_band_is_fair(self):
        guide = guide_price(2000.0)
        assert guide.verdict(guide.guide) is PriceVerdict.FAIR
        assert guide.verdict(guide.low * 0.5) is PriceVerdict.BARGAIN
        assert guide.verdict(guide.high * 1.5) is PriceVerdict.AMBITIOUS

    def test_a_worthless_pack_is_a_disposal_not_a_bargain(self):
        """Marking a negative valuation as cheap would be the wrong answer entirely."""
        guide = guide_price(-800.0)
        assert guide.is_disposal
        assert guide.verdict(0.0) is PriceVerdict.DISPOSAL
        assert "recycler" in guide.explain(0.0)

    def test_the_explanation_says_what_the_gap_is_for(self):
        guide = guide_price(2000.0)
        assert "collect" in guide.explain(guide.high * 2)


class TestListing:
    def test_a_listing_needs_a_valuation(self, service):
        """The rule the whole market rests on."""
        with pytest.raises(MarketError, match="Scan the battery first"):
            service.create_listing("BV-NOPE-NOPE", seller_handle="someone")

    def test_a_listing_carries_the_valuation_forward(self, service, leaf_valuation):
        listing = service.create_listing(
            leaf_valuation.reference, seller_handle="leaf-owner"
        )
        assert listing.valuation_reference == leaf_valuation.reference
        assert listing.state_of_health == pytest.approx(0.81)
        assert listing.rated_kwh == 40
        assert listing.chemistry == "NMC532"
        assert listing.estimate == pytest.approx(leaf_valuation.residual_value)

    def test_the_default_price_is_the_guide(self, service, leaf_valuation):
        listing = service.create_listing(
            leaf_valuation.reference, seller_handle="leaf-owner"
        )
        assert listing.asking_price == pytest.approx(listing.guide.guide)
        assert listing.price_verdict is PriceVerdict.FAIR

    def test_an_ambitious_price_is_called_out(self, service, leaf_valuation):
        listing = service.create_listing(
            leaf_valuation.reference,
            seller_handle="leaf-owner",
            asking_price=leaf_valuation.residual_value * 2,
        )
        assert listing.price_verdict is PriceVerdict.AMBITIOUS

    def test_a_stale_valuation_cannot_back_a_listing(
        self, service, leaf_valuation, isolated_store
    ):
        """Metal prices move weekly; a year-old number is not this pack's price."""
        old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        with isolated_store._connect() as connection:
            connection.execute(
                "UPDATE valuations SET created_at = ? WHERE reference = ?",
                (old, leaf_valuation.reference),
            )
        with pytest.raises(MarketError, match="days old"):
            service.create_listing(leaf_valuation.reference, seller_handle="x")

    def test_one_valuation_lists_once(self, service, leaf_valuation):
        service.create_listing(leaf_valuation.reference, seller_handle="a")
        with pytest.raises(MarketError, match="already listed"):
            service.create_listing(leaf_valuation.reference, seller_handle="b")

    def test_a_withdrawn_listing_frees_the_valuation(self, service, leaf_valuation):
        first = service.create_listing(leaf_valuation.reference, seller_handle="a")
        service.withdraw(first.reference)
        second = service.create_listing(leaf_valuation.reference, seller_handle="a")
        assert second.reference != first.reference

    def test_a_worthless_pack_lists_as_a_disposal(self, service, damaged_valuation):
        """Not a failure -- it is the honest outcome, and the market can say it."""
        listing = service.create_listing(
            damaged_valuation.reference, seller_handle="fleet"
        )
        assert listing.kind is ListingKind.DISPOSAL
        assert listing.asking_price == 0
        assert "recycler" in listing.price_note

    def test_a_damaged_pack_flags_its_freight(self, service, damaged_valuation):
        """ADR 376 is not something to discover on collection day."""
        listing = service.create_listing(
            damaged_valuation.reference, seller_handle="fleet"
        )
        assert listing.condition == "damaged"
        assert listing.needs_dangerous_goods_freight

    def test_a_healthy_pack_does_not_cry_wolf(self, service, leaf_valuation):
        listing = service.create_listing(leaf_valuation.reference, seller_handle="a")
        assert not listing.needs_dangerous_goods_freight

    def test_the_buyer_can_open_the_valuation(self, service, leaf_valuation):
        """The entire point: the assessment is inspectable, not asserted."""
        listing = service.create_listing(leaf_valuation.reference, seller_handle="a")
        payload = service.valuation_payload(listing)
        assert payload["battery"]["label"] == listing.battery_label
        assert payload["bill_of_materials"]["lines"]
        assert payload["prices"]["quotes"]


class TestOffers:
    def test_offer_and_accept(self, service, leaf_valuation):
        listing = service.create_listing(leaf_valuation.reference, seller_handle="a")
        offer = service.make_offer(
            listing.reference, buyer_handle="storage-nl", amount=1200
        )
        reserved = service.accept_offer(offer.reference)
        assert reserved.status is ListingStatus.RESERVED

    def test_accepting_declines_the_others(self, service, leaf_valuation):
        listing = service.create_listing(leaf_valuation.reference, seller_handle="a")
        low = service.make_offer(listing.reference, buyer_handle="one", amount=900)
        high = service.make_offer(listing.reference, buyer_handle="two", amount=1400)
        service.accept_offer(high.reference)
        assert service.market.get_offer(low.reference).status.value == "declined"

    def test_a_closed_listing_takes_no_offers(self, service, leaf_valuation):
        listing = service.create_listing(leaf_valuation.reference, seller_handle="a")
        service.withdraw(listing.reference)
        with pytest.raises(MarketError, match="no longer taking offers"):
            service.make_offer(listing.reference, buyer_handle="b", amount=100)

    def test_an_offer_of_nothing_is_refused(self, service, leaf_valuation):
        listing = service.create_listing(leaf_valuation.reference, seller_handle="a")
        with pytest.raises(MarketError):
            service.make_offer(listing.reference, buyer_handle="b", amount=0)

    def test_the_best_offer_is_the_highest(self, service, leaf_valuation):
        listing = service.create_listing(leaf_valuation.reference, seller_handle="a")
        service.make_offer(listing.reference, buyer_handle="one", amount=900)
        service.make_offer(listing.reference, buyer_handle="two", amount=1400)
        assert service.get(listing.reference).best_offer.amount == 1400

    def test_updating_a_listing_keeps_its_offers(self, service, leaf_valuation):
        """A replace-style write plus ON DELETE CASCADE quietly eats every bid."""
        listing = service.create_listing(leaf_valuation.reference, seller_handle="a")
        service.make_offer(listing.reference, buyer_handle="b", amount=1100)
        service.reprice(listing.reference, 1400)
        assert len(service.get(listing.reference).offers) == 1

    def test_selling_records_the_accepted_price(self, service, leaf_valuation):
        listing = service.create_listing(leaf_valuation.reference, seller_handle="a")
        offer = service.make_offer(listing.reference, buyer_handle="b", amount=1350)
        service.accept_offer(offer.reference)
        sold = service.mark_sold(listing.reference)
        assert sold.status is ListingStatus.SOLD
        assert sold.sold_price == 1350
        assert sold.sold_at is not None


class TestObservations:
    """Completed sales are what eventually replace the estimated part values."""

    def _sell(self, service, engine, passports, isolated_store, price, soh):
        passport = passports.from_document(
            {
                "battery": {
                    "manufacturer": "Nissan",
                    "vehicleModel": "Leaf ZE1 40 kWh",
                    "manufacturingDate": "2019-04-01",
                },
                "spec": {
                    "ratedEnergy": {"value": 40, "unit": "kWh"},
                    "chemistry": "NMC532",
                },
                "status": {"stateOfHealth": soh},
            }
        )
        record = isolated_store.save(
            valuation_to_dict(engine.value(passport)), passport=passport
        )
        listing = service.create_listing(record.reference, seller_handle="seller")
        return service.mark_sold(listing.reference, price)

    def test_one_sale_is_not_a_price(
        self, service, engine, passports, isolated_store
    ):
        """A single transaction may have been a favour or a mistake."""
        self._sell(service, engine, passports, isolated_store, 1400, 81)
        assert summarise(service.market.sold()) == {}

    def test_enough_sales_make_a_median(
        self, service, engine, passports, isolated_store
    ):
        for price, soh in ((1200, 78), (1400, 81), (1600, 85)):
            self._sell(service, engine, passports, isolated_store, price, soh)

        summary = summarise(service.market.sold())
        assert len(summary) == 1
        entry = summary["nissan-leaf-ze1-40"]
        assert entry["sample_size"] == MINIMUM_SALES_FOR_A_PRICE
        assert entry["median_price_per_kwh"] == pytest.approx(1400 / 40)
        assert entry["min_soh"] == pytest.approx(0.78)
        assert entry["max_soh"] == pytest.approx(0.85)

    def test_the_median_ignores_an_outlier(
        self, service, engine, passports, isolated_store
    ):
        """A thin market produces desperate sellers. The published figure holds."""
        for price in (1350, 1400, 1450, 1500, 60):
            self._sell(service, engine, passports, isolated_store, price, 81)
        entry = summarise(service.market.sold())["nissan-leaf-ze1-40"]
        assert entry["median_price_per_kwh"] == pytest.approx(1400 / 40)

    def test_sql_states_the_health_range(
        self, service, engine, passports, isolated_store
    ):
        """A price per kWh means nothing without knowing how worn the packs were."""
        for price, soh in ((1200, 78), (1400, 81), (1600, 85)):
            self._sell(service, engine, passports, isolated_store, price, soh)
        sql = to_battery_data_sql(service.market.sold())
        assert "INSERT INTO replacement_price" in sql
        assert "state of health 78%-85%" in sql
        assert "median of 3 completed private sales" in sql

    def test_sql_says_so_when_there_is_nothing_to_say(self, service):
        assert "anecdote" in to_battery_data_sql([])


class TestHttp:
    def test_the_market_page_serves(self, api_client):
        response = api_client.get("/market")
        assert response.status_code == 200
        assert "Battery market" in response.text

    def test_list_and_browse(self, api_client, leaf_valuation):
        created = api_client.post(
            "/v1/market/listings",
            json={
                "valuation_reference": leaf_valuation.reference,
                "seller_handle": "leaf-owner",
                "region": "NL",
            },
        )
        assert created.status_code == 201
        reference = created.json()["reference"]

        listings = api_client.get("/v1/market/listings").json()
        assert listings["count"] == 1

        detail = api_client.get(f"/v1/market/listings/{reference}").json()
        assert detail["valuation"]["battery"]["label"] == leaf_valuation.battery_label
        assert detail["price"]["verdict"] == "fair"

    def test_listing_an_unknown_valuation_is_a_conflict(self, api_client):
        response = api_client.post(
            "/v1/market/listings",
            json={"valuation_reference": "BV-NOPE-NOPE", "seller_handle": "someone"},
        )
        assert response.status_code == 409
        assert "Scan the battery first" in response.json()["detail"]

    def test_the_report_behind_a_listing_renders(self, api_client, leaf_valuation):
        created = api_client.post(
            "/v1/market/listings",
            json={
                "valuation_reference": leaf_valuation.reference,
                "seller_handle": "leaf-owner",
            },
        )
        reference = created.json()["reference"]
        report = api_client.get(f"/market/report/{reference}")
        assert report.status_code == 200
        assert "How it is wearing" in report.text

    def test_the_guide_works_without_listing_anything(
        self, api_client, leaf_valuation
    ):
        """A seller can ask what it is worth without committing to selling it."""
        guide = api_client.get(f"/v1/market/guide/{leaf_valuation.reference}").json()
        assert guide["guide"] < guide["estimate"]
        assert guide["low"] < guide["guide"] < guide["high"]

    def test_offers_over_http(self, api_client, leaf_valuation):
        created = api_client.post(
            "/v1/market/listings",
            json={
                "valuation_reference": leaf_valuation.reference,
                "seller_handle": "leaf-owner",
            },
        ).json()
        offer = api_client.post(
            f"/v1/market/listings/{created['reference']}/offers",
            json={"buyer_handle": "storage-nl", "amount": 1200},
        )
        assert offer.status_code == 201
        accepted = api_client.post(f"/v1/market/offers/{offer.json()['reference']}/accept")
        assert accepted.json()["status"] == "reserved"
