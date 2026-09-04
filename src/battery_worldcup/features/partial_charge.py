"""Features of a constant-current charge segment and of the constant-voltage phase.

Charging is the most repeatable part of field usage, and a fixed voltage window of the CC
charge is the input most partial-data SOH papers rely on. Features are reported per window so
that a model can be restricted to the windows a deployment actually observes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_WINDOWS: tuple[tuple[float, float], ...] = ((3.6, 3.9), (3.9, 4.1))


def _window_key(prefix: str, v1: float, v2: float) -> str:
    return f"{prefix}_{v1:g}_{v2:g}".replace(".", "p")


def _clean(*arrays) -> list[np.ndarray]:
    arrs = [np.asarray(a, dtype=float).ravel() for a in arrays]
    keep = np.ones(len(arrs[0]), dtype=bool)
    for a in arrs:
        keep &= np.isfinite(a)
    return [a[keep] for a in arrs]


def partial_charge_feature_names(
    windows: tuple[tuple[float, float], ...] = DEFAULT_WINDOWS, prefix: str = "pc"
) -> list[str]:
    names = [
        f"{prefix}_duration_s",
        f"{prefix}_capacity_ah",
        f"{prefix}_v_start",
        f"{prefix}_v_end",
        f"{prefix}_mean_current_a",
    ]
    for v1, v2 in windows:
        key = _window_key(prefix, v1, v2)
        names += [f"{key}_time_s", f"{key}_charge_ah", f"{key}_slope_v_per_ah"]
    return names


def partial_charge_features(
    time_s,
    voltage_v,
    capacity_ah,
    current_a=None,
    windows: tuple[tuple[float, float], ...] = DEFAULT_WINDOWS,
    prefix: str = "pc",
) -> dict[str, float]:
    """Time, charge and slope inside fixed voltage windows of a CC charge segment."""
    out = {name: np.nan for name in partial_charge_feature_names(windows, prefix)}
    t, v, q = _clean(time_s, voltage_v, capacity_ah)
    if len(t) < 3:
        return out
    out[f"{prefix}_duration_s"] = float(t[-1] - t[0])
    out[f"{prefix}_capacity_ah"] = float(q[-1] - q[0])
    out[f"{prefix}_v_start"] = float(v[0])
    out[f"{prefix}_v_end"] = float(v[-1])
    if current_a is not None:
        i = np.asarray(current_a, dtype=float).ravel()
        out[f"{prefix}_mean_current_a"] = float(np.nanmean(np.abs(i)))

    # t(V) and Q(V) on a strictly increasing voltage axis
    merged = pd.DataFrame({"v": v, "t": t, "q": q}).groupby("v", sort=True).mean()
    vv = merged.index.to_numpy(dtype=float)
    tt = merged["t"].to_numpy(dtype=float)
    qq = merged["q"].to_numpy(dtype=float)
    for v1, v2 in windows:
        key = _window_key(prefix, v1, v2)
        if vv[0] <= v1 and vv[-1] >= v2 and v2 > v1:
            t1, t2 = np.interp([v1, v2], vv, tt)
            q1, q2 = np.interp([v1, v2], vv, qq)
            out[f"{key}_time_s"] = float(t2 - t1)
            out[f"{key}_charge_ah"] = float(q2 - q1)
            if q2 > q1:
                out[f"{key}_slope_v_per_ah"] = float((v2 - v1) / (q2 - q1))
    return out


def cv_phase_feature_names(prefix: str = "cv") -> list[str]:
    return [
        f"{prefix}_duration_s",
        f"{prefix}_charge_ah",
        f"{prefix}_i_start_a",
        f"{prefix}_i_end_a",
        f"{prefix}_i_ratio",
        f"{prefix}_half_current_time_s",
    ]


def cv_phase_features(time_s, current_a, capacity_ah=None, prefix: str = "cv") -> dict[str, float]:
    """Duration, charge and current decay of the constant-voltage phase."""
    out = {name: np.nan for name in cv_phase_feature_names(prefix)}
    t, i = _clean(time_s, current_a)
    if len(t) < 2:
        return out
    i = np.abs(i)
    out[f"{prefix}_duration_s"] = float(t[-1] - t[0])
    if capacity_ah is not None:
        q = np.asarray(capacity_ah, dtype=float).ravel()
        q = q[np.isfinite(q)]
        if len(q):
            out[f"{prefix}_charge_ah"] = float(q[-1] - q[0])
    else:
        out[f"{prefix}_charge_ah"] = float(np.sum(0.5 * (i[1:] + i[:-1]) * np.diff(t)) / 3600.0)
    out[f"{prefix}_i_start_a"] = float(i[0])
    out[f"{prefix}_i_end_a"] = float(i[-1])
    if i[0] > 0:
        out[f"{prefix}_i_ratio"] = float(i[-1] / i[0])
        below = np.flatnonzero(i <= 0.5 * i[0])
        if len(below):
            out[f"{prefix}_half_current_time_s"] = float(t[below[0]] - t[0])
    return out
