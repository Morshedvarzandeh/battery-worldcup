"""Per-cycle feature extraction over a :class:`DatasetBundle`."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from battery_worldcup.data.schema import DatasetBundle, StepType
from battery_worldcup.features.ica import DVASettings, ICASettings, dva_features, ica_features
from battery_worldcup.features.partial_charge import (
    DEFAULT_WINDOWS,
    cv_phase_features,
    partial_charge_features,
)
from battery_worldcup.features.relaxation import relaxation_features

KEY_COLUMNS = ["dataset", "cell_id", "cycle_index"]


@dataclass(frozen=True)
class FeatureConfig:
    ica: ICASettings = field(default_factory=ICASettings)
    dva: DVASettings = field(default_factory=DVASettings)
    windows: tuple[tuple[float, float], ...] = DEFAULT_WINDOWS
    n_peaks: int = 3
    charge_step_types: tuple[str, ...] = (StepType.CC_CHARGE.value, StepType.CHARGE.value)
    include_discharge_ica: bool = True


def _capacity(step: pd.DataFrame) -> np.ndarray:
    """Cumulative capacity of a step, integrated from current when the column is missing."""
    q = step["capacity_ah"].to_numpy(dtype=float)
    if np.isfinite(q).all():
        return q
    t = step["time_s"].to_numpy(dtype=float)
    i = np.abs(step["current_a"].to_numpy(dtype=float))
    return np.concatenate([[0.0], np.cumsum(0.5 * (i[1:] + i[:-1]) * np.diff(t)) / 3600.0])


def _steps_in_order(cycle: pd.DataFrame) -> list[tuple[int, str, pd.DataFrame]]:
    out = []
    for step_index, step in cycle.groupby("step_index", sort=True):
        out.append((int(step_index), str(step["step_type"].iloc[0]), step))
    return out


def extract_cycle_features(
    bundle: DatasetBundle, config: FeatureConfig | None = None
) -> pd.DataFrame:
    """One row per (cell, cycle) with ICA, DVA, partial-charge, CV and relaxation features.

    The first charge step of the configured types feeds the ICA/DVA/partial-charge features;
    the first CV step feeds the CV features; the first rest step after that charge feeds the
    relaxation features; the first discharge step feeds the discharge ICA. Missing steps give
    NaN features so that availability can be reported per dataset.
    """
    cfg = config or FeatureConfig()
    if bundle.timeseries is None:
        raise ValueError("bundle has no timeseries table")
    rows = []
    for (cell_id, cycle_index), cycle in bundle.timeseries.groupby(
        ["cell_id", "cycle_index"], sort=True
    ):
        steps = _steps_in_order(cycle)
        charge = next((s for s in steps if s[1] in cfg.charge_step_types), None)
        cv = next((s for s in steps if s[1] == StepType.CV_CHARGE.value), None)
        after = charge[0] if charge else -1
        rest = next((s for s in steps if s[1] == StepType.REST.value and s[0] > after), None)
        discharge = next((s for s in steps if s[1] == StepType.DISCHARGE.value), None)

        row: dict = {"dataset": bundle.dataset, "cell_id": cell_id, "cycle_index": cycle_index}
        row["has_charge"] = charge is not None
        row["has_cv"] = cv is not None
        row["has_rest"] = rest is not None
        row["has_discharge"] = discharge is not None

        if charge is not None:
            step = charge[2]
            q = _capacity(step)
            v = step["voltage_v"].to_numpy(dtype=float)
            t = step["time_s"].to_numpy(dtype=float)
            i = step["current_a"].to_numpy(dtype=float)
            row.update(ica_features(v, q, cfg.ica, cfg.n_peaks, prefix="ica_ch"))
            row.update(dva_features(v, q, cfg.dva, cfg.n_peaks, prefix="dva_ch"))
            row.update(partial_charge_features(t, v, q, i, cfg.windows, prefix="pc"))
        else:
            row.update(ica_features([], [], cfg.ica, cfg.n_peaks, prefix="ica_ch"))
            row.update(dva_features([], [], cfg.dva, cfg.n_peaks, prefix="dva_ch"))
            row.update(partial_charge_features([], [], [], None, cfg.windows, prefix="pc"))

        if cv is not None:
            step = cv[2]
            row.update(
                cv_phase_features(
                    step["time_s"].to_numpy(dtype=float),
                    step["current_a"].to_numpy(dtype=float),
                    _capacity(step),
                )
            )
        else:
            row.update(cv_phase_features([], []))

        if rest is not None:
            step = rest[2]
            row.update(
                relaxation_features(
                    step["time_s"].to_numpy(dtype=float), step["voltage_v"].to_numpy(dtype=float)
                )
            )
        else:
            row.update(relaxation_features([], []))

        if cfg.include_discharge_ica:
            if discharge is not None:
                step = discharge[2]
                row.update(
                    ica_features(
                        step["voltage_v"].to_numpy(dtype=float),
                        _capacity(step),
                        cfg.ica,
                        cfg.n_peaks,
                        prefix="ica_dc",
                    )
                )
            else:
                row.update(ica_features([], [], cfg.ica, cfg.n_peaks, prefix="ica_dc"))
        rows.append(row)

    out = pd.DataFrame(rows)
    out["dataset"] = out["dataset"].astype("string")
    out["cell_id"] = out["cell_id"].astype("string")
    out["cycle_index"] = out["cycle_index"].astype("int64")
    return out


def feature_availability(features: pd.DataFrame) -> pd.Series:
    """Fraction of rows with a finite value, per feature column."""
    cols = [c for c in features.columns if c not in KEY_COLUMNS and not c.startswith("has_")]
    return features[cols].notna().mean().sort_values(ascending=False)
