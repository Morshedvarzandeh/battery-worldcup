"""Empirical aging models and knee detection (family S7).

These models are fitted to a single cell's observed SOH history and extrapolated. Their
parameters are learned per cell at predict time; :meth:`fit` on the training cells only
establishes a population prior used when a target cell has too little history to fit.

The functional forms are the ones that dominate the literature:

``power``
    ``SOH(n) = 1 - a * n**b``. ``b`` near 0.5 is the square-root-of-time behaviour of
    SEI-limited aging; ``b`` near 1 is linear fade.
``biexponential``
    ``SOH(n) = a*exp(b*n) + c*exp(d*n)``, the form used in most RUL papers on the NASA data.
``linear``
    ``SOH(n) = 1 - a*n``, kept as the simplest member of the family.

None of them can express a knee, which is why :func:`detect_knee` is provided separately: the
benchmark reports error before and after the knee rather than pretending one model covers both.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

from battery_worldcup.models.base import InputRequirements, ModelData, SOHModel, register


# -- functional forms -----------------------------------------------------------------------
def power_law(n, a, b):
    return 1.0 - a * np.power(np.maximum(n, 0.0), b)


def linear_fade(n, a):
    return 1.0 - a * n


def biexponential(n, a, b, c, d):
    return a * np.exp(b * n) + c * np.exp(d * n)


FORMS: dict[str, dict] = {
    "power": {
        "func": power_law,
        "p0": (1e-3, 0.8),
        "bounds": ((0.0, 0.05), (10.0, 3.0)),
    },
    "linear": {
        "func": linear_fade,
        "p0": (1e-4,),
        "bounds": ((0.0,), (1.0,)),
    },
    "biexponential": {
        "func": biexponential,
        "p0": (1.0, -1e-5, -1e-3, -1e-3),
        "bounds": ((0.0, -1.0, -1.0, -1.0), (2.0, 0.0, 1.0, 0.0)),
    },
}


def fit_form(
    cycles: np.ndarray, soh: np.ndarray, form: str = "power", maxfev: int = 20000
) -> np.ndarray | None:
    """Least-squares fit of one functional form; ``None`` when the fit does not converge."""
    spec = FORMS[form]
    x = np.asarray(cycles, dtype=float)
    y = np.asarray(soh, dtype=float)
    keep = np.isfinite(x) & np.isfinite(y)
    x, y = x[keep], y[keep]
    if len(x) < len(spec["p0"]) + 1 or np.ptp(x) <= 0:
        return None
    try:
        popt, _ = curve_fit(spec["func"], x, y, p0=spec["p0"], bounds=spec["bounds"], maxfev=maxfev)
    except (RuntimeError, ValueError):
        return None
    return np.asarray(popt, dtype=float)


# -- knee detection -------------------------------------------------------------------------
@dataclass(frozen=True)
class Knee:
    """Result of a knee fit. ``onset`` is where the second segment starts to bend."""

    point: float
    onset: float
    slope_before: float
    slope_after: float
    rmse: float
    found: bool


def _bacon_watts(n, a0, a1, a2, x1, gamma):
    return a0 + a1 * (n - x1) + a2 * (n - x1) * np.tanh((n - x1) / gamma)


def detect_knee(cycles, soh, min_points: int = 8, slope_ratio: float = 1.5) -> Knee:
    """Locate a knee with the Bacon-Watts model.

    The knee is reported as found when the fit converges inside the observed range and the
    post-knee fade is at least ``slope_ratio`` times steeper than the pre-knee fade. ``onset``
    is the classical ``x1 - gamma`` estimate of where the transition begins.
    """
    x = np.asarray(cycles, dtype=float)
    y = np.asarray(soh, dtype=float)
    keep = np.isfinite(x) & np.isfinite(y)
    x, y = x[keep], y[keep]
    empty = Knee(np.nan, np.nan, np.nan, np.nan, np.nan, False)
    if len(x) < min_points or np.ptp(x) <= 0:
        return empty
    span = float(np.ptp(x))
    slope0 = float(np.polyfit(x, y, 1)[0])
    p0 = (float(y[0]), slope0, 0.0, float(x[0] + 0.7 * span), 0.05 * span)
    bounds = (
        (-np.inf, -np.inf, -np.inf, float(x[0] + 0.1 * span), 1e-6),
        (np.inf, np.inf, np.inf, float(x[0] + 0.95 * span), span),
    )
    try:
        popt, _ = curve_fit(_bacon_watts, x, y, p0=p0, bounds=bounds, maxfev=20000)
    except (RuntimeError, ValueError):
        return empty
    a0, a1, a2, x1, gamma = (float(v) for v in popt)
    rmse = float(np.sqrt(np.mean((y - _bacon_watts(x, *popt)) ** 2)))
    before, after = a1 - a2, a1 + a2
    steeper = after < 0 and abs(after) >= slope_ratio * max(abs(before), 1e-12)
    return Knee(x1, x1 - gamma, before, after, rmse, bool(steeper))


# -- the model ------------------------------------------------------------------------------
@register
class EmpiricalFade(SOHModel):
    """Fit an empirical fade law to each target cell's history and extrapolate it."""

    name = "empirical_fade"
    family = "S7"
    requirements = InputRequirements(history=True, training_cells=False)

    def __init__(
        self, form: str = "power", min_points: int = 4, clip: tuple[float, float] = (0.0, 1.2)
    ) -> None:
        super().__init__()
        if form not in FORMS:
            raise ValueError(f"unknown form {form!r}; known: {sorted(FORMS)}")
        self.form = form
        self.min_points = int(min_points)
        self.clip_low, self.clip_high = float(clip[0]), float(clip[1])
        self._prior: np.ndarray | None = None
        self.fallback = 1.0

    def _fit(self, data: ModelData) -> None:
        lab = data.labelled()
        self.fallback = float(lab["soh_capacity"].mean()) if len(lab) else 1.0
        fits = []
        for _, g in lab.groupby("cell_id", sort=False):
            popt = fit_form(g["cycle_index"].to_numpy(), g["soh_capacity"].to_numpy(), self.form)
            if popt is not None:
                fits.append(popt)
        self._prior = np.median(np.vstack(fits), axis=0) if fits else None

    def _predict(self, data: ModelData) -> pd.DataFrame:
        func = FORMS[self.form]["func"]
        targets = data.targets.reset_index(drop=True)
        values = np.empty(len(targets))
        for cell_id, rows in targets.groupby("cell_id", sort=False):
            hist = data.history_for(str(cell_id))
            xt = rows["cycle_index"].to_numpy(dtype=float)
            popt = None
            if len(hist) >= self.min_points:
                popt = fit_form(
                    hist["cycle_index"].to_numpy(), hist["soh_capacity"].to_numpy(), self.form
                )
            if popt is None:
                popt = self._prior
            if popt is None:
                pred = np.full(len(xt), self.fallback)
            else:
                pred = func(xt, *popt)
            values[rows.index.to_numpy()] = np.clip(pred, self.clip_low, self.clip_high)
        return self._frame(targets, values)

    def get_params(self) -> dict:
        return {
            "form": self.form,
            "min_points": self.min_points,
            "clip": [self.clip_low, self.clip_high],
        }
