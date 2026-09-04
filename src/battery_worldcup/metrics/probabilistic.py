"""Metrics for models that report an uncertainty.

A model that reports intervals is making two claims at once: that its point estimate is close,
and that its interval covers the truth as often as it says. Both are scored here. An
overconfident model wins on sharpness and loses on coverage, which is exactly the trade-off a
safety-relevant deployment needs to see.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import norm


def _clean(y_true, mean, std) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    y = np.asarray(y_true, dtype=float).ravel()
    m = np.asarray(mean, dtype=float).ravel()
    s = np.asarray(std, dtype=float).ravel()
    if not (y.shape == m.shape == s.shape):
        raise ValueError(f"shape mismatch: {y.shape}, {m.shape}, {s.shape}")
    keep = np.isfinite(y) & np.isfinite(m) & np.isfinite(s) & (s > 0)
    if not keep.any():
        raise ValueError("no finite predictions with a positive standard deviation")
    return y[keep], m[keep], s[keep]


def gaussian_nll(y_true, mean, std) -> float:
    """Negative log-likelihood under a Gaussian predictive distribution (lower is better)."""
    y, m, s = _clean(y_true, mean, std)
    return float(np.mean(0.5 * np.log(2 * np.pi * s**2) + (y - m) ** 2 / (2 * s**2)))


def gaussian_crps(y_true, mean, std) -> float:
    """Continuous ranked probability score for a Gaussian, in the units of ``y``."""
    y, m, s = _clean(y_true, mean, std)
    z = (y - m) / s
    return float(np.mean(s * (z * (2 * norm.cdf(z) - 1) + 2 * norm.pdf(z) - 1 / np.sqrt(np.pi))))


def coverage(y_true, mean, std, level: float = 0.9) -> float:
    """Fraction of truths inside the central interval at ``level``. Should equal ``level``."""
    y, m, s = _clean(y_true, mean, std)
    half = norm.ppf(0.5 + level / 2) * s
    return float(np.mean((y >= m - half) & (y <= m + half)))


def interval_width(mean, std, level: float = 0.9) -> float:
    """Mean width of the central interval at ``level``: the sharpness of the model."""
    s = np.asarray(std, dtype=float).ravel()
    s = s[np.isfinite(s) & (s > 0)]
    if not len(s):
        raise ValueError("no positive standard deviations")
    return float(np.mean(2 * norm.ppf(0.5 + level / 2) * s))


def probabilistic_metrics(y_true, mean, std, level: float = 0.9) -> dict[str, float]:
    return {
        "nll": gaussian_nll(y_true, mean, std),
        "crps": gaussian_crps(y_true, mean, std),
        f"coverage_{int(level * 100)}": coverage(y_true, mean, std, level),
        f"interval_width_{int(level * 100)}": interval_width(mean, std, level),
        "calibration_error": abs(coverage(y_true, mean, std, level) - level),
    }
