"""Trajectory and lifetime metrics.

A trajectory is scored per cell and then averaged over cells, so that a cell with a long record
does not dominate a cell with a short one. The metric that matters operationally is not the
average error along the curve but the error in *when* the cell crosses the end-of-life
threshold, because that is the number a fleet operator acts on.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

REQUIRED = ("cell_id", "cycle_index", "y_true", "y_pred")


def _check(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"trajectory frame is missing {missing}")
    out = df[list(REQUIRED)].dropna(subset=["y_true", "y_pred"])
    if not len(out):
        raise ValueError("no finite predictions to score")
    return out.sort_values(["cell_id", "cycle_index"])


def crossing_cycle(cycles, values, threshold: float) -> float:
    """First cycle at which a curve drops below ``threshold``, linearly interpolated.

    Returns NaN when the curve never crosses, which is a right-censored observation rather than
    a large number: callers must not silently treat it as one.
    """
    x = np.asarray(cycles, dtype=float)
    y = np.asarray(values, dtype=float)
    below = np.flatnonzero(y < threshold)
    if not len(below):
        return float("nan")
    k = int(below[0])
    if k == 0:
        return float(x[0])
    y0, y1 = y[k - 1], y[k]
    if y0 == y1:
        return float(x[k])
    return float(x[k - 1] + (y0 - threshold) * (x[k] - x[k - 1]) / (y0 - y1))


def trajectory_metrics(df: pd.DataFrame, threshold: float = 0.8) -> dict[str, float]:
    """Per-cell trajectory error and end-of-life timing error.

    ``eol_cycle_mae`` is averaged over the cells where both the true and the predicted curve
    cross the threshold; ``eol_censored_cells`` counts the rest, so a model cannot look accurate
    by never predicting a crossing.
    """
    data = _check(df)
    per_cell_rmse, per_cell_mae, eol_errors = [], [], []
    censored = 0
    for _, g in data.groupby("cell_id", sort=False):
        error = g["y_pred"].to_numpy() - g["y_true"].to_numpy()
        per_cell_rmse.append(float(np.sqrt(np.mean(error**2))))
        per_cell_mae.append(float(np.mean(np.abs(error))))
        true_eol = crossing_cycle(g["cycle_index"], g["y_true"], threshold)
        pred_eol = crossing_cycle(g["cycle_index"], g["y_pred"], threshold)
        if np.isfinite(true_eol) and np.isfinite(pred_eol):
            eol_errors.append(abs(pred_eol - true_eol))
        elif np.isfinite(true_eol) or np.isfinite(pred_eol):
            censored += 1
    return {
        "trajectory_rmse": float(np.mean(per_cell_rmse)),
        "trajectory_mae": float(np.mean(per_cell_mae)),
        "eol_cycle_mae": float(np.mean(eol_errors)) if eol_errors else float("nan"),
        "eol_scored_cells": len(eol_errors),
        "eol_censored_cells": censored,
        "n_cells": int(data["cell_id"].nunique()),
    }
