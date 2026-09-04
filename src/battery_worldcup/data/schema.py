"""Canonical data schema.

Every dataset is converted into a :class:`DatasetBundle` holding up to four tables:

``cells``
    one row per cell (metadata).
``cycles``
    one row per cycle with per-cycle summaries and the reference-test flag.
``timeseries``
    sampled current, voltage and temperature, one row per sample (optional).
``eis``
    impedance spectra, one row per frequency point (optional).

Conventions
-----------
* ``current_a`` is positive when charging and negative when discharging.
* ``capacity_ah`` is the cumulative absolute charge passed since the start of the step.
* ``time_s`` restarts at zero at the start of every cycle.
* ``reference_capacity_ah`` is only set on cycles flagged ``is_reference_test`` and is the
  capacity measured by that reference test. SOH labels are derived from it and from nothing
  else (see :mod:`battery_worldcup.labels`).
* The nominal capacity is stored for information and is never used as an SOH denominator.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import numpy as np
import pandas as pd

SCHEMA_VERSION = "0.1"


class SchemaError(ValueError):
    """Raised when a table does not conform to the canonical schema."""


class StepType(StrEnum):
    """Kind of a step inside a cycle."""

    CC_CHARGE = "cc_charge"
    CV_CHARGE = "cv_charge"
    CHARGE = "charge"  # charge step whose CC/CV boundary is not resolved by the source
    DISCHARGE = "discharge"
    REST = "rest"
    PULSE = "pulse"
    EIS = "eis"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Column:
    name: str
    dtype: str
    nullable: bool = True
    description: str = ""


CELL_COLUMNS: tuple[Column, ...] = (
    Column("dataset", "string", False, "Registry key of the dataset"),
    Column("cell_id", "string", False, "Cell identifier, unique within the dataset"),
    Column("chemistry", "string", True, "Cathode/anode chemistry, free text"),
    Column("form_factor", "string", True, "18650, 21700, pouch, prismatic, coin"),
    Column("nominal_capacity_ah", "float64", True, "Datasheet capacity, informational only"),
    Column("nominal_voltage_v", "float64", True),
    Column("manufacturer", "string", True),
    Column("part_number", "string", True),
    Column("test_temperature_c", "float64", True, "Ambient or chamber temperature of the test"),
    Column("protocol", "string", True, "Free-text description of the aging protocol"),
    Column("licence", "string", True),
    Column("source", "string", True, "URL or DOI the data were obtained from"),
)

CYCLE_COLUMNS: tuple[Column, ...] = (
    Column("dataset", "string", False),
    Column("cell_id", "string", False),
    Column("cycle_index", "int64", False, "Zero-based cycle counter as reported by the source"),
    Column("start_time_s", "float64", True, "Seconds since the start of the cell's test"),
    Column("charge_capacity_ah", "float64", True),
    Column("discharge_capacity_ah", "float64", True),
    Column("charge_energy_wh", "float64", True),
    Column("discharge_energy_wh", "float64", True),
    Column("coulombic_efficiency", "float64", True, "discharge / charge capacity"),
    Column("mean_temperature_c", "float64", True),
    Column("max_temperature_c", "float64", True),
    Column("is_reference_test", "bool", False, "True when this cycle is a reference test"),
    Column("reference_capacity_ah", "float64", True, "Capacity measured by the reference test"),
)

TIMESERIES_COLUMNS: tuple[Column, ...] = (
    Column("dataset", "string", False),
    Column("cell_id", "string", False),
    Column("cycle_index", "int64", False),
    Column("step_index", "int64", False, "Zero-based step counter within the cycle"),
    Column("step_type", "string", False, "One of StepType"),
    Column("time_s", "float64", False, "Seconds since the start of the cycle"),
    Column("current_a", "float64", False, "Positive when charging"),
    Column("voltage_v", "float64", False),
    Column("temperature_c", "float64", True),
    Column("capacity_ah", "float64", True, "Cumulative absolute charge within the step"),
)

EIS_COLUMNS: tuple[Column, ...] = (
    Column("dataset", "string", False),
    Column("cell_id", "string", False),
    Column("cycle_index", "int64", False),
    Column("soc", "float64", True, "State of charge at which the spectrum was taken, 0..1"),
    Column("temperature_c", "float64", True),
    Column("frequency_hz", "float64", False),
    Column("z_real_ohm", "float64", False),
    Column("z_imag_ohm", "float64", False),
)

CELL_KEY = ("dataset", "cell_id")
CYCLE_KEY = ("dataset", "cell_id", "cycle_index")

_TABLES = {
    "cells": (CELL_COLUMNS, CELL_KEY),
    "cycles": (CYCLE_COLUMNS, CYCLE_KEY),
    "timeseries": (TIMESERIES_COLUMNS, None),
    "eis": (EIS_COLUMNS, None),
}


def _cast(series: pd.Series, dtype: str) -> pd.Series:
    if dtype == "string":
        return series.astype("string")
    if dtype == "float64":
        return pd.to_numeric(series, errors="raise").astype("float64")
    if dtype == "int64":
        return series.astype("int64")
    if dtype == "bool":
        return series.astype("bool")
    raise ValueError(f"unsupported dtype {dtype!r}")


def _empty(dtype: str, index: pd.Index) -> pd.Series:
    if dtype == "string":
        return pd.Series(pd.array([pd.NA] * len(index), dtype="string"), index=index)
    if dtype == "float64":
        return pd.Series(np.nan, index=index, dtype="float64")
    raise SchemaError(f"cannot create an empty non-nullable column of dtype {dtype!r}")


def coerce(df: pd.DataFrame, columns: tuple[Column, ...]) -> pd.DataFrame:
    """Return a copy with schema columns first, cast to their dtypes.

    Missing nullable columns are added as null; missing required columns raise.
    Extra columns are preserved after the schema columns.
    """
    out = df.copy()
    for col in columns:
        if col.name not in out.columns:
            if not col.nullable:
                raise SchemaError(f"missing required column {col.name!r}")
            out[col.name] = _empty(col.dtype, out.index)
        else:
            out[col.name] = _cast(out[col.name], col.dtype)
    names = [c.name for c in columns]
    extra = [c for c in out.columns if c not in names]
    return out[names + extra]


def validate_table(
    df: pd.DataFrame, columns: tuple[Column, ...], key: tuple[str, ...] | None, name: str
) -> None:
    missing = [c.name for c in columns if c.name not in df.columns]
    if missing:
        raise SchemaError(f"{name}: missing columns {missing}")
    for col in columns:
        if not col.nullable and df[col.name].isna().any():
            raise SchemaError(f"{name}: required column {col.name!r} contains nulls")
    if key is not None and df.duplicated(list(key)).any():
        raise SchemaError(f"{name}: duplicate rows for key {key}")


def _pairs(df: pd.DataFrame, cols: tuple[str, ...]) -> set[tuple]:
    return set(map(tuple, df[list(cols)].itertuples(index=False, name=None)))


@dataclass
class DatasetBundle:
    """One dataset in the canonical schema."""

    cells: pd.DataFrame
    cycles: pd.DataFrame
    timeseries: pd.DataFrame | None = None
    eis: pd.DataFrame | None = None

    # -- construction -----------------------------------------------------------------------
    def coerced(self) -> DatasetBundle:
        """Return a copy of the bundle with every table coerced to the schema dtypes."""
        return DatasetBundle(
            cells=coerce(self.cells, CELL_COLUMNS),
            cycles=coerce(self.cycles, CYCLE_COLUMNS),
            timeseries=None
            if self.timeseries is None
            else coerce(self.timeseries, TIMESERIES_COLUMNS),
            eis=None if self.eis is None else coerce(self.eis, EIS_COLUMNS),
        )

    # -- properties -------------------------------------------------------------------------
    @property
    def dataset(self) -> str:
        keys = self.cells["dataset"].unique()
        if len(keys) != 1:
            raise SchemaError(f"a bundle holds exactly one dataset, found {list(keys)}")
        return str(keys[0])

    @property
    def cell_ids(self) -> list[str]:
        return [str(c) for c in self.cells["cell_id"]]

    # -- validation -------------------------------------------------------------------------
    def validate(self) -> DatasetBundle:
        """Raise :class:`SchemaError` if any table violates the schema; return self."""
        validate_table(self.cells, CELL_COLUMNS, CELL_KEY, "cells")
        validate_table(self.cycles, CYCLE_COLUMNS, CYCLE_KEY, "cycles")
        _ = self.dataset  # exactly one dataset key

        cell_keys = _pairs(self.cells, CELL_KEY)
        unknown = _pairs(self.cycles, CELL_KEY) - cell_keys
        if unknown:
            raise SchemaError(f"cycles reference unknown cells: {sorted(unknown)[:5]}")

        ref = self.cycles.loc[
            self.cycles["is_reference_test"].astype(bool), "reference_capacity_ah"
        ]
        if ref.isna().any() or (ref <= 0).any():
            raise SchemaError("reference-test cycles must carry a positive reference_capacity_ah")

        cycle_keys = _pairs(self.cycles, CYCLE_KEY)
        if self.timeseries is not None:
            ts = self.timeseries
            validate_table(ts, TIMESERIES_COLUMNS, None, "timeseries")
            bad = set(ts["step_type"].unique()) - {s.value for s in StepType}
            if bad:
                raise SchemaError(f"timeseries: unknown step types {sorted(bad)}")
            unknown = _pairs(ts, CYCLE_KEY) - cycle_keys
            if unknown:
                raise SchemaError(f"timeseries reference unknown cycles: {sorted(unknown)[:5]}")
            diff = ts.groupby(["cell_id", "cycle_index", "step_index"], sort=False)["time_s"].diff()
            if (diff < 0).any():
                raise SchemaError("timeseries: time_s must be non-decreasing within a step")
        if self.eis is not None:
            validate_table(self.eis, EIS_COLUMNS, None, "eis")
            unknown = _pairs(self.eis, CYCLE_KEY) - cycle_keys
            if unknown:
                raise SchemaError(f"eis reference unknown cycles: {sorted(unknown)[:5]}")
        return self

    # -- summaries --------------------------------------------------------------------------
    def summary(self) -> dict:
        per_cell = self.cycles.groupby("cell_id").size()
        return {
            "dataset": self.dataset,
            "n_cells": int(len(self.cells)),
            "n_cycles": int(len(self.cycles)),
            "n_reference_cycles": int(self.cycles["is_reference_test"].sum()),
            "cycles_per_cell": {
                "min": int(per_cell.min()),
                "median": float(per_cell.median()),
                "max": int(per_cell.max()),
            },
            "n_timeseries_rows": 0 if self.timeseries is None else int(len(self.timeseries)),
            "n_eis_rows": 0 if self.eis is None else int(len(self.eis)),
        }

    # -- persistence ------------------------------------------------------------------------
    def to_parquet(self, directory: str | Path) -> Path:
        """Write the bundle as ``<directory>/{cells,cycles,timeseries,eis}.parquet``."""
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        tables = {"cells": self.cells, "cycles": self.cycles}
        if self.timeseries is not None:
            tables["timeseries"] = self.timeseries
        if self.eis is not None:
            tables["eis"] = self.eis
        for name, df in tables.items():
            df.to_parquet(d / f"{name}.parquet", index=False)
        meta = {
            "schema_version": SCHEMA_VERSION,
            "dataset": self.dataset,
            "tables": sorted(tables),
            "summary": self.summary(),
        }
        (d / "bundle.json").write_text(json.dumps(meta, indent=2))
        return d

    @classmethod
    def from_parquet(cls, directory: str | Path) -> DatasetBundle:
        d = Path(directory)
        if not (d / "bundle.json").exists():
            raise FileNotFoundError(f"{d} does not contain a bundle.json")
        meta = json.loads((d / "bundle.json").read_text())
        if meta.get("schema_version") != SCHEMA_VERSION:
            raise SchemaError(
                f"bundle schema {meta.get('schema_version')!r} != supported {SCHEMA_VERSION!r}"
            )
        tables = {name: pd.read_parquet(d / f"{name}.parquet") for name in meta["tables"]}
        return cls(
            cells=tables["cells"],
            cycles=tables["cycles"],
            timeseries=tables.get("timeseries"),
            eis=tables.get("eis"),
        ).coerced()
