"""Incremental capacity (dQ/dV) and differential voltage (dV/dQ) analysis.

Both curves are derivatives of a noisy, unevenly sampled Q(V) relation, so the method matters
as much as the data. This implementation

1. takes the samples of one constant-current step, flips discharge steps so that voltage and
   capacity both increase, sorts by voltage and merges duplicate voltages,
2. interpolates capacity on a uniform voltage grid (default 5 mV),
3. differentiates with a Savitzky-Golay filter (default 50 mV window, polynomial order 2),
4. locates peaks with :func:`scipy.signal.find_peaks`.

Smoothing changes peak heights, so features should always be compared under the same
settings. Differential voltage analysis does the same on a uniform capacity grid.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.signal import find_peaks, savgol_filter


@dataclass(frozen=True)
class ICASettings:
    voltage_step_v: float = 0.005
    window_v: float = 0.05
    polyorder: int = 2
    v_min: float | None = None
    v_max: float | None = None
    min_points: int = 10


@dataclass(frozen=True)
class DVASettings:
    capacity_step_frac: float = 0.005
    window_frac: float = 0.05
    polyorder: int = 2
    min_points: int = 10


@dataclass
class Curve:
    """A derivative curve: ``x`` is voltage (ICA) or capacity (DVA)."""

    x: np.ndarray
    y: np.ndarray
    kind: str

    def as_frame(self) -> pd.DataFrame:
        name = {"ica": "dq_dv_ah_per_v", "dva": "dv_dq_v_per_ah"}[self.kind]
        xname = {"ica": "voltage_v", "dva": "capacity_ah"}[self.kind]
        return pd.DataFrame({xname: self.x, name: self.y})


def _monotone_qv(voltage, capacity) -> tuple[np.ndarray, np.ndarray]:
    """Return (v, q) with v strictly increasing and q non-decreasing."""
    v = np.asarray(voltage, dtype=float).ravel()
    q = np.asarray(capacity, dtype=float).ravel()
    keep = np.isfinite(v) & np.isfinite(q)
    v, q = v[keep], q[keep]
    if len(v) < 3:
        raise ValueError("need at least 3 finite samples")
    if v[-1] < v[0]:  # discharge: reverse so voltage increases, capacity counted from the end
        v = v[::-1]
        q = q[::-1]
        q = q[0] - q
    else:
        q = q - q[0]
    merged = pd.DataFrame({"v": v, "q": q}).groupby("v", sort=True)["q"].mean()
    v = merged.index.to_numpy(dtype=float)
    q = np.maximum.accumulate(merged.to_numpy(dtype=float))
    if len(v) < 3 or v[-1] - v[0] <= 0:
        raise ValueError("voltage does not span a range")
    return v, q


def _odd_window(points: float, polyorder: int, n: int) -> int:
    w = int(round(points))
    w = max(w, polyorder + 2)
    if w % 2 == 0:
        w += 1
    if w > n:
        w = n if n % 2 == 1 else n - 1
    if w <= polyorder:
        raise ValueError("too few points for the requested smoothing window")
    return w


def incremental_capacity(voltage, capacity, settings: ICASettings | None = None) -> Curve:
    """dQ/dV against voltage on a uniform voltage grid."""
    s = settings or ICASettings()
    v, q = _monotone_qv(voltage, capacity)
    lo = v[0] if s.v_min is None else max(v[0], s.v_min)
    hi = v[-1] if s.v_max is None else min(v[-1], s.v_max)
    n = int(np.floor((hi - lo) / s.voltage_step_v)) + 1
    if n < s.min_points:
        raise ValueError(f"only {n} grid points in [{lo:.3f}, {hi:.3f}] V")
    grid = lo + np.arange(n) * s.voltage_step_v
    q_grid = np.interp(grid, v, q)
    window = _odd_window(s.window_v / s.voltage_step_v, s.polyorder, n)
    dqdv = savgol_filter(q_grid, window, s.polyorder, deriv=1, delta=s.voltage_step_v)
    return Curve(grid, dqdv, "ica")


def differential_voltage(voltage, capacity, settings: DVASettings | None = None) -> Curve:
    """dV/dQ against capacity on a uniform capacity grid."""
    s = settings or DVASettings()
    v, q = _monotone_qv(voltage, capacity)
    merged = pd.DataFrame({"q": q, "v": v}).groupby("q", sort=True)["v"].mean()
    q = merged.index.to_numpy(dtype=float)
    v = merged.to_numpy(dtype=float)
    total = q[-1] - q[0]
    if len(q) < 3 or total <= 0:
        raise ValueError("capacity does not span a range")
    step = total * s.capacity_step_frac
    n = int(np.floor(total / step)) + 1
    if n < s.min_points:
        raise ValueError("too few grid points for DVA")
    grid = q[0] + np.arange(n) * step
    v_grid = np.interp(grid, q, v)
    window = _odd_window(s.window_frac / s.capacity_step_frac, s.polyorder, n)
    dvdq = savgol_filter(v_grid, window, s.polyorder, deriv=1, delta=step)
    return Curve(grid, dvdq, "dva")


def find_curve_peaks(
    curve: Curve, prominence: float | None = None, n_peaks: int = 3
) -> pd.DataFrame:
    """The ``n_peaks`` most prominent peaks of a curve, ordered by position.

    Columns: ``position``, ``height``, ``prominence``, ``width`` (in units of ``x``).
    """
    y = curve.y
    span = float(np.nanmax(y) - np.nanmin(y)) if len(y) else 0.0
    prom = prominence if prominence is not None else 0.05 * span
    if len(y) < 3 or span <= 0:
        return pd.DataFrame(columns=["position", "height", "prominence", "width"])
    idx, props = find_peaks(y, prominence=prom, width=0)
    dx = float(curve.x[1] - curve.x[0]) if len(curve.x) > 1 else 1.0
    peaks = pd.DataFrame(
        {
            "position": curve.x[idx],
            "height": y[idx],
            "prominence": props["prominences"],
            "width": props["widths"] * dx,
        }
    )
    peaks = peaks.sort_values("prominence", ascending=False).head(n_peaks)
    return peaks.sort_values("position").reset_index(drop=True)


def _peak_names(n_peaks: int) -> list[str]:
    names = ["max", "x_at_max", "area"]
    for i in range(n_peaks):
        names += [f"peak{i}_x", f"peak{i}_height", f"peak{i}_prominence", f"peak{i}_width"]
    return names


def curve_features(curve: Curve | None, n_peaks: int = 3, prefix: str = "ica") -> dict[str, float]:
    out = {f"{prefix}_{name}": np.nan for name in _peak_names(n_peaks)}
    if curve is None or len(curve.y) < 3:
        return out
    y = curve.y
    out[f"{prefix}_max"] = float(np.nanmax(y))
    out[f"{prefix}_x_at_max"] = float(curve.x[int(np.nanargmax(y))])
    out[f"{prefix}_area"] = float(np.sum(0.5 * (y[1:] + y[:-1]) * np.diff(curve.x)))
    peaks = find_curve_peaks(curve, n_peaks=n_peaks)
    for i in range(min(n_peaks, len(peaks))):
        row = peaks.iloc[i]
        out[f"{prefix}_peak{i}_x"] = float(row["position"])
        out[f"{prefix}_peak{i}_height"] = float(row["height"])
        out[f"{prefix}_peak{i}_prominence"] = float(row["prominence"])
        out[f"{prefix}_peak{i}_width"] = float(row["width"])
    return out


def ica_features(
    voltage, capacity, settings: ICASettings | None = None, n_peaks: int = 3, prefix: str = "ica"
) -> dict[str, float]:
    """ICA peak features for one step; all NaN when the curve cannot be computed."""
    try:
        curve = incremental_capacity(voltage, capacity, settings)
    except ValueError:
        curve = None
    return curve_features(curve, n_peaks=n_peaks, prefix=prefix)


def dva_features(
    voltage, capacity, settings: DVASettings | None = None, n_peaks: int = 3, prefix: str = "dva"
) -> dict[str, float]:
    """DVA features for one step. Valleys of dV/dQ are reported as peaks of -dV/dQ."""
    try:
        curve = differential_voltage(voltage, capacity, settings)
        curve = Curve(curve.x, -curve.y, "dva")
    except ValueError:
        curve = None
    return curve_features(curve, n_peaks=n_peaks, prefix=prefix)
