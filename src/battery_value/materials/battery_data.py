"""Read recovery terms and cost assumptions from the battery-data database.

The pack side of battery-data is read in :mod:`battery_value.packs.battery_data`.
This is the other half: the recovery rates, refiner payables, treatment costs,
dangerous-goods tariffs and model calibration that turn a bill of materials into
money.

It matters more than it looks. Payables are negotiated and move with the market,
and a term agreed in one year silently pricing a pack in another is the failure
mode battery-data's validity windows exist to prevent. Reading them live means
the valuation tracks the terms actually in force, rather than whatever was true
when the package was last released.

Only currently-valid, non-regulatory rows are used. A regulatory minimum is a
floor on physical recovery with no claim about payment, so treating one as a
commercial term would overstate what a holder can realise.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any

from .recovery import (
    ElementRecovery,
    LogisticsModel,
    ProcessCosts,
    RecoveryLibrary,
    RecyclingProcess,
    ReuseParams,
    SecondLifeParams,
    load_recovery,
)

logger = logging.getLogger(__name__)

ENV_DSN = "BV_BATTERY_DATA_DSN"

# treatment_cost.stage -> the ProcessCosts field it feeds.
_STAGE_FIELDS = {
    "discharge_and_dismantle": "discharge_and_dismantle",
    "shredding_to_black_mass": "shredding_to_black_mass",
    "refining_gate_fee": "refining_gate_fee",
}

_PROCESS_QUERY = """
SELECT uid, route::text, name, description, maturity::text, applies_to
  FROM bd.recovery_process
"""

_YIELD_QUERY = """
SELECT p.uid AS process_uid, y.element_symbol, y.recovery_rate,
       y.payable_fraction, f.code AS traded_form
  FROM bd.recovery_yield y
  JOIN bd.recovery_process p ON p.id = y.recovery_process_id
  LEFT JOIN bd.traded_form f ON f.id = y.traded_form_id
 WHERE NOT y.is_regulatory_minimum
   AND y.valid_from <= %(today)s
   AND (y.valid_to IS NULL OR y.valid_to > %(today)s)
"""

_COST_QUERY = """
SELECT p.uid AS process_uid, c.stage::text, c.cost_per_kg
  FROM bd.treatment_cost c
  JOIN bd.recovery_process p ON p.id = c.recovery_process_id
 WHERE c.cost_per_kg IS NOT NULL
   AND c.valid_from <= %(today)s
   AND (c.valid_to IS NULL OR c.valid_to > %(today)s)
"""

_LOGISTICS_QUERY = """
SELECT condition::text, cost_per_kg, minimum_charge
  FROM bd.logistics_tariff
 WHERE valid_from <= %(today)s
   AND (valid_to IS NULL OR valid_to > %(today)s)
"""

_ASSUMPTION_QUERY = """
SELECT key, value_num
  FROM bd.valuation_assumption
 WHERE value_num IS NOT NULL
   AND valid_from <= %(today)s
   AND (valid_to IS NULL OR valid_to > %(today)s)
"""


def _process_key(uid: str) -> str:
    """'process/hydrometallurgical' -> 'hydrometallurgical'."""
    return str(uid).rsplit("/", 1)[-1].replace("-", "_")


def _build_logistics(rows: list[dict[str, Any]], fallback: LogisticsModel) -> LogisticsModel:
    """Rebuild the multiplier model from absolute per-condition tariffs.

    battery-data stores what each condition actually costs, which is the
    honest shape. The valuation works in a base rate plus multipliers, so the
    healthy tariff becomes the base and the rest divide into it.
    """
    if not rows:
        return fallback

    by_condition = {row["condition"]: float(row["cost_per_kg"]) for row in rows}
    base = by_condition.get("healthy") or min(by_condition.values())
    if base <= 0:
        return fallback

    return LogisticsModel(
        base_eur_per_kg=base,
        condition_multiplier={
            condition: round(cost / base, 4)
            for condition, cost in by_condition.items()
        },
        minimum_charge_eur=float(
            rows[0].get("minimum_charge") or fallback.minimum_charge_eur
        ),
        notes=("sourced from battery-data logistics_tariff",),
    )


def _build_second_life(
    assumptions: dict[str, float], fallback: SecondLifeParams
) -> SecondLifeParams:
    """Second-life parameters, falling back per field rather than wholesale."""
    def value(key: str, default: float) -> float:
        return assumptions.get(f"second_life.{key}", default)

    return SecondLifeParams(
        testing_eur_per_kwh=value("testing_eur_per_kwh", fallback.testing_eur_per_kwh),
        repackaging_eur_per_kwh=value(
            "repackaging_eur_per_kwh", fallback.repackaging_eur_per_kwh
        ),
        new_bms_eur_per_pack=value(
            "new_bms_eur_per_pack", fallback.new_bms_eur_per_pack
        ),
        certification_eur_per_pack=value(
            "certification_eur_per_pack", fallback.certification_eur_per_pack
        ),
        warranty_reserve_fraction=value(
            "warranty_reserve_fraction", fallback.warranty_reserve_fraction
        ),
        minimum_viable_soh=value("minimum_viable_soh", fallback.minimum_viable_soh),
        second_life_end_of_life_soh=value(
            "end_of_life_soh", fallback.second_life_end_of_life_soh
        ),
    )


def _build_reuse(assumptions: dict[str, float], fallback: ReuseParams) -> ReuseParams:
    """Reuse parameters, falling back per field."""
    def value(key: str, default: float) -> float:
        return assumptions.get(f"reuse.{key}", default)

    return ReuseParams(
        minimum_viable_soh=value("minimum_viable_soh", fallback.minimum_viable_soh),
        maximum_age_years=value("maximum_age_years", fallback.maximum_age_years),
        refurbishment_eur_per_kwh=value(
            "refurbishment_eur_per_kwh", fallback.refurbishment_eur_per_kwh
        ),
        test_and_certify_eur_per_pack=value(
            "test_and_certify_eur_per_pack", fallback.test_and_certify_eur_per_pack
        ),
        oem_replacement_price_discount=value(
            "oem_replacement_price_discount", fallback.oem_replacement_price_discount
        ),
        warranty_reserve_fraction=value(
            "warranty_reserve_fraction", fallback.warranty_reserve_fraction
        ),
    )


def build_recovery_library(
    process_rows: list[dict[str, Any]],
    yield_rows: list[dict[str, Any]],
    cost_rows: list[dict[str, Any]],
    logistics_rows: list[dict[str, Any]],
    assumption_rows: list[dict[str, Any]],
    *,
    fallback: RecoveryLibrary | None = None,
) -> RecoveryLibrary:
    """Assemble a :class:`RecoveryLibrary` from battery-data rows.

    Anything the database does not carry falls back to the bundled dataset
    field by field, so a partially populated database degrades to a mixture
    rather than to nothing.
    """
    fallback = fallback or load_recovery()

    yields_by_process: dict[str, dict[str, ElementRecovery]] = {}
    for row in yield_rows:
        key = _process_key(row["process_uid"])
        payable = row.get("payable_fraction")
        yields_by_process.setdefault(key, {})[row["element_symbol"]] = ElementRecovery(
            element=row["element_symbol"],
            recovery_rate=float(row["recovery_rate"]),
            payable_fraction=float(payable) if payable is not None else 0.0,
            traded_form=row.get("traded_form") or "steel_scrap",
        )

    costs_by_process: dict[str, dict[str, float]] = {}
    for row in cost_rows:
        field = _STAGE_FIELDS.get(row["stage"])
        if field is None:
            continue
        key = _process_key(row["process_uid"])
        costs_by_process.setdefault(key, {})[field] = float(row["cost_per_kg"])

    processes: dict[str, RecyclingProcess] = {}
    for row in process_rows:
        key = _process_key(row["uid"])
        elements = yields_by_process.get(key)
        if not elements:
            # A process with no current yields cannot price anything, and
            # offering it as an option would quietly return zero revenue.
            logger.debug("battery-data: %s has no current yields; skipped", key)
            continue

        costs = costs_by_process.get(key, {})
        processes[key] = RecyclingProcess(
            key=key,
            label=row.get("name") or key,
            description=row.get("description") or "",
            applies_to_families=tuple(row.get("applies_to") or ()),
            elements=elements,
            costs=ProcessCosts(
                discharge_and_dismantle=costs.get("discharge_and_dismantle", 0.0),
                shredding_to_black_mass=costs.get("shredding_to_black_mass", 0.0),
                refining_gate_fee=costs.get("refining_gate_fee", 0.0),
            ),
            maturity=row.get("maturity") or "commercial",
        )

    if not processes:
        raise ValueError("battery-data returned no usable recovery processes")

    assumptions = {
        row["key"]: float(row["value_num"]) for row in assumption_rows
    }

    return RecoveryLibrary(
        updated=date.today().isoformat(),
        processes=processes,
        logistics=_build_logistics(logistics_rows, fallback.logistics),
        second_life=_build_second_life(assumptions, fallback.second_life),
        reuse=_build_reuse(assumptions, fallback.reuse),
        sources=("battery-data",),
    )


def load_recovery_from_battery_data(dsn: str | None = None) -> RecoveryLibrary:
    """Query a battery-data database for the current recovery terms.

    Raises:
        RuntimeError: If no DSN is configured or psycopg is missing, so a
            caller that asked for live terms is told it did not get them
            rather than silently receiving the bundled ones.
    """
    resolved = dsn or os.environ.get(ENV_DSN)
    if not resolved:
        raise RuntimeError(f"no battery-data database configured; set {ENV_DSN}")

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError(
            "psycopg is required to read battery-data; "
            "pip install 'battery-value[batterydata]'"
        ) from exc

    params = {"today": date.today()}
    with psycopg.connect(resolved, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(_PROCESS_QUERY)
            processes = [dict(row) for row in cursor.fetchall()]
            cursor.execute(_YIELD_QUERY, params)
            yields = [dict(row) for row in cursor.fetchall()]
            cursor.execute(_COST_QUERY, params)
            costs = [dict(row) for row in cursor.fetchall()]
            cursor.execute(_LOGISTICS_QUERY, params)
            logistics = [dict(row) for row in cursor.fetchall()]
            cursor.execute(_ASSUMPTION_QUERY, params)
            assumptions = [dict(row) for row in cursor.fetchall()]

    return build_recovery_library(processes, yields, costs, logistics, assumptions)


def recovery_library(dsn: str | None = None) -> RecoveryLibrary:
    """The best available recovery terms.

    Live from battery-data when it is configured and reachable, otherwise the
    bundled dataset. Falling back is logged rather than silent, because the
    difference between live and bundled payables is real money.
    """
    if not (dsn or os.environ.get(ENV_DSN)):
        return load_recovery()
    try:
        return load_recovery_from_battery_data(dsn)
    except Exception as exc:  # noqa: BLE001 - never fail a valuation on this
        logger.warning(
            "could not read recovery terms from battery-data (%s); "
            "using the bundled dataset",
            exc,
        )
        return load_recovery()
