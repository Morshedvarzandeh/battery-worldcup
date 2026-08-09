"""Public reference prices: licensing, periods, and where they sit in the chain."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from battery_value.market.providers.reference import (
    ReferencePriceError,
    ReferenceProvider,
    load_dataset,
    parse_dataset,
)
from battery_value.market.resolver import build_resolver
from battery_value.market.types import PriceQuality

REPO_ROOT = Path(__file__).resolve().parents[1]

DATASET = {
    "schema_version": 1,
    "generated_at": "2026-08-09",
    "sources": {
        "worldbank-pinksheet": {
            "title": "World Bank Commodity Price Data (Pink Sheet)",
            "licence": "CC-BY-4.0",
            "url": "https://www.worldbank.org/en/research/commodity-markets",
            "retrieved": "2026-08-09",
        }
    },
    "prices": {
        "nickel_metal": {
            "price": 15800.0,
            "currency": "USD",
            "unit": "t",
            "period_start": "2026-07-01",
            "period_end": "2026-07-31",
            "basis": "monthly_average",
            "source": "worldbank-pinksheet",
            "series": "NICKEL",
        }
    },
}


def write_dataset(tmp_path: Path, document: dict) -> Path:
    path = tmp_path / "reference_prices.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def mutate(**changes) -> dict:
    """A copy of DATASET with one price field replaced."""
    document = json.loads(json.dumps(DATASET))
    document["prices"]["nickel_metal"].update(changes)
    return document


# --------------------------------------------------------------------------
# Licensing: the rule that makes the dataset safe to pass on
# --------------------------------------------------------------------------


def test_non_redistributable_licence_is_refused_by_name():
    document = json.loads(json.dumps(DATASET))
    document["sources"]["worldbank-pinksheet"]["licence"] = "Fastmarkets-subscription"
    with pytest.raises(ReferencePriceError) as excinfo:
        parse_dataset(document)
    message = str(excinfo.value)
    assert "Fastmarkets-subscription" in message
    assert "not redistributable" in message


def test_attribution_travels_with_the_data():
    dataset = parse_dataset(DATASET)
    assert dataset.attributions == (
        "World Bank Commodity Price Data (Pink Sheet) - "
        "https://www.worldbank.org/en/research/commodity-markets - CC-BY-4.0",
    )


def test_price_referencing_an_undeclared_source_is_refused():
    document = mutate(source="smm")
    with pytest.raises(ReferencePriceError, match="not declared in sources"):
        parse_dataset(document)


# --------------------------------------------------------------------------
# A period is not a day
# --------------------------------------------------------------------------


def test_quote_is_dated_to_the_close_of_its_period():
    quote = parse_dataset(DATASET).prices["nickel_metal"]
    # Not generated_at, and not the start: the number cannot claim to be
    # current before the window it averages has closed.
    assert quote.as_of == date(2026, 7, 31)
    assert quote.period_start == date(2026, 7, 1)
    assert quote.is_period_average


def test_describe_shows_the_averaging_window():
    quote = parse_dataset(DATASET).prices["nickel_metal"]
    assert "[avg 2026-07-01..2026-07-31]" in quote.describe()


def test_currency_conversion_keeps_the_period():
    # in_currency rebuilds the quote field by field, so a new field is easy to
    # drop there and hard to notice: the converted quote silently stops being
    # an average.
    quote = parse_dataset(DATASET).prices["nickel_metal"]
    converted = quote.in_currency("EUR", 0.92)
    assert converted.currency == "EUR"
    assert converted.period_start == quote.period_start
    assert converted.period_end == quote.period_end
    assert converted.is_period_average


def test_backwards_period_is_refused():
    with pytest.raises(ReferencePriceError, match="precedes period_start"):
        parse_dataset(mutate(period_start="2026-07-31", period_end="2026-07-01"))


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"basis": "vibes"}, "unknown basis"),
        ({"price": -5}, "must be positive"),
        ({"period_end": "not-a-date"}, "bad date"),
    ],
)
def test_unusable_entries_raise_rather_than_being_skipped(changes, expected):
    # A skipped entry becomes a form the resolver falls through on, and the
    # valuation still returns a number with nothing saying it got worse.
    with pytest.raises(ReferencePriceError, match=expected):
        parse_dataset(mutate(**changes))


def test_schema_version_must_match():
    document = json.loads(json.dumps(DATASET))
    document["schema_version"] = 99
    with pytest.raises(ReferencePriceError, match="unsupported schema_version"):
        parse_dataset(document)


# --------------------------------------------------------------------------
# Confidence and chain position
# --------------------------------------------------------------------------


def test_reference_outranks_the_bundled_snapshot():
    assert (
        PriceQuality.REFERENCE.base_confidence
        > PriceQuality.BASELINE.base_confidence
    )
    assert (
        PriceQuality.REFERENCE.base_confidence < PriceQuality.DELAYED.base_confidence
    )


def test_resolver_prefers_reference_to_baseline(tmp_path):
    path = write_dataset(tmp_path, DATASET)
    resolver = build_resolver(
        currency="USD", offline=True, reference_path=path, today=date(2026, 8, 9)
    )
    quote = resolver.require("nickel_metal")
    assert quote.quality is PriceQuality.REFERENCE
    assert quote.price == pytest.approx(15800.0)


def test_manual_still_beats_reference(tmp_path):
    path = write_dataset(tmp_path, DATASET)
    resolver = build_resolver(
        currency="USD",
        offline=True,
        reference_path=path,
        manual={"nickel_metal": 17000.0},
        today=date(2026, 8, 9),
    )
    assert resolver.require("nickel_metal").quality is PriceQuality.MANUAL


def test_unpriced_form_falls_through_to_baseline(tmp_path):
    path = write_dataset(tmp_path, DATASET)
    resolver = build_resolver(
        currency="USD", offline=True, reference_path=path, today=date(2026, 8, 9)
    )
    # The dataset quotes nickel only; lithium must still resolve.
    assert resolver.require("lithium_carbonate").quality is PriceQuality.BASELINE


def test_offline_valuation_beats_the_confidence_floor(tmp_path):
    # The case this provider exists for. With the snapshot alone an offline
    # valuation decays to the floor once the bundle is a few months old.
    today = date(2026, 8, 9)
    path = write_dataset(tmp_path, DATASET)
    with_reference = build_resolver(
        currency="USD", offline=True, reference_path=path, today=today
    ).require("nickel_metal")
    without = build_resolver(currency="USD", offline=True, today=today).require(
        "nickel_metal"
    )
    assert with_reference.confidence(today) > without.confidence(today)


# --------------------------------------------------------------------------
# Degrading, not exploding
# --------------------------------------------------------------------------


def test_missing_dataset_leaves_the_provider_unavailable():
    provider = ReferenceProvider(None)
    assert not provider.is_available()
    assert provider.supported_forms() == frozenset()
    assert provider.fetch("nickel_metal") is None
    assert "unavailable" in provider.describe()


def test_malformed_dataset_degrades_instead_of_failing_every_valuation(tmp_path, caplog):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    provider = ReferenceProvider(path)
    assert not provider.is_available()
    resolver = build_resolver(
        currency="USD", offline=True, reference_path=path, today=date(2026, 8, 9)
    )
    assert resolver.require("nickel_metal").quality is PriceQuality.BASELINE


def test_env_var_configures_the_provider(tmp_path, monkeypatch):
    path = write_dataset(tmp_path, DATASET)
    monkeypatch.setenv("BV_REFERENCE_PRICES", str(path))
    assert ReferenceProvider().is_available()


def test_load_dataset_round_trips(tmp_path):
    path = write_dataset(tmp_path, DATASET)
    assert load_dataset(path).forms() == frozenset({"nickel_metal"})


# --------------------------------------------------------------------------
# The import tool
# --------------------------------------------------------------------------


def run_tool(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "import_reference_prices.py"), *args],
        capture_output=True,
        text=True,
    )


def test_neutral_csv_produces_a_loadable_dataset(tmp_path):
    source = tmp_path / "extract.csv"
    source.write_text(
        "form,price,currency,unit,period_start,period_end,basis\n"
        "cobalt_sulphate,8700,USD,t,2026-07-01,2026-07-31,monthly_average\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.json"
    result = run_tool(
        "--source", "csv",
        "--input", str(source),
        "--output", str(out),
        "--source-key", "national-stats",
        "--source-title", "National statistics office",
        "--source-licence", "CC-BY-4.0",
        "--generated-at", "2026-08-09",
    )
    assert result.returncode == 0, result.stderr
    dataset = load_dataset(out)
    quote = dataset.prices["cobalt_sulphate"]
    assert quote.quality is PriceQuality.REFERENCE
    assert quote.as_of == date(2026, 7, 31)


def test_tool_refuses_a_non_redistributable_licence(tmp_path):
    source = tmp_path / "extract.csv"
    source.write_text(
        "form,price,currency,unit,period_start,period_end,basis\n"
        "cobalt_sulphate,8700,USD,t,2026-07-01,2026-07-31,monthly_average\n",
        encoding="utf-8",
    )
    result = run_tool(
        "--source", "csv",
        "--input", str(source),
        "--output", str(tmp_path / "out.json"),
        "--source-key", "fastmarkets",
        "--source-title", "Fastmarkets assessment",
        "--source-licence", "subscription",
        "--generated-at", "2026-08-09",
    )
    assert result.returncode != 0
    assert "not redistributable" in (result.stderr + result.stdout)
    assert not (tmp_path / "out.json").exists()


def test_adapter_refuses_an_unexpected_layout(tmp_path):
    # The publishers change layouts. Refusing beats reading the wrong column
    # and emitting a plausible number.
    source = tmp_path / "surprise.csv"
    source.write_text("something,else\n1,2\n", encoding="utf-8")
    result = run_tool(
        "--source", "worldbank-pinksheet",
        "--input", str(source),
        "--period", "2026-07",
        "--output", str(tmp_path / "out.json"),
    )
    assert result.returncode != 0
    assert "refused" in (result.stderr + result.stdout)


def test_worldbank_adapter_maps_a_known_layout(tmp_path):
    source = tmp_path / "pink.csv"
    source.write_text(
        "period,NICKEL,COPPER,COCOA\n"
        "2026M06,15200,9700,3000\n"
        "2026M07,15800,9800,3100\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.json"
    result = run_tool(
        "--source", "worldbank-pinksheet",
        "--input", str(source),
        "--period", "2026-07",
        "--output", str(out),
        "--generated-at", "2026-08-09",
    )
    assert result.returncode == 0, result.stderr
    dataset = load_dataset(out)
    # Cocoa is not a battery material and has no traded form; it is skipped
    # rather than mapped to something plausible.
    assert dataset.forms() == frozenset({"nickel_metal", "copper_metal"})
    assert dataset.prices["nickel_metal"].price == pytest.approx(15800.0)
    assert dataset.prices["nickel_metal"].period_end == date(2026, 7, 31)
