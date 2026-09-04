"""Point-estimate metrics. Inputs are array-likes in the same units (the benchmark uses SOH
in percentage points); NaN pairs are dropped."""

from __future__ import annotations

import numpy as np


def _clean(y_true, y_pred) -> tuple[np.ndarray, np.ndarray]:
    a = np.asarray(y_true, dtype=float).ravel()
    b = np.asarray(y_pred, dtype=float).ravel()
    if a.shape != b.shape:
        raise ValueError(f"shape mismatch: {a.shape} vs {b.shape}")
    keep = np.isfinite(a) & np.isfinite(b)
    if not keep.any():
        raise ValueError("no finite pairs to score")
    return a[keep], b[keep]


def mae(y_true, y_pred) -> float:
    a, b = _clean(y_true, y_pred)
    return float(np.mean(np.abs(a - b)))


def rmse(y_true, y_pred) -> float:
    a, b = _clean(y_true, y_pred)
    return float(np.sqrt(np.mean((a - b) ** 2)))


def mape(y_true, y_pred) -> float:
    """Mean absolute percentage error; pairs with a zero true value are dropped."""
    a, b = _clean(y_true, y_pred)
    nz = a != 0
    if not nz.any():
        raise ValueError("all true values are zero")
    return float(100.0 * np.mean(np.abs((a[nz] - b[nz]) / a[nz])))


def max_abs_error(y_true, y_pred) -> float:
    a, b = _clean(y_true, y_pred)
    return float(np.max(np.abs(a - b)))


def r2(y_true, y_pred) -> float:
    a, b = _clean(y_true, y_pred)
    ss_res = float(np.sum((a - b) ** 2))
    ss_tot = float(np.sum((a - a.mean()) ** 2))
    if ss_tot == 0.0:
        return float("nan")
    return 1.0 - ss_res / ss_tot


def point_metrics(y_true, y_pred) -> dict[str, float]:
    return {
        "mae": mae(y_true, y_pred),
        "rmse": rmse(y_true, y_pred),
        "mape": mape(y_true, y_pred),
        "max_abs_error": max_abs_error(y_true, y_pred),
        "r2": r2(y_true, y_pred),
        "n": int(_clean(y_true, y_pred)[0].size),
    }
