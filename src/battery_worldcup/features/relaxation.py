"""Features of the voltage relaxation during a rest step.

Zhu et al. (2022) showed that the variance, skewness and maximum of the rest voltage after a
full charge predict capacity across chemistries; those three are reported under the names
``relax_var``, ``relax_skew`` and ``relax_max`` together with a few shape descriptors.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import kurtosis, skew


def relaxation_feature_names(prefix: str = "relax") -> list[str]:
    return [
        f"{prefix}_var",
        f"{prefix}_skew",
        f"{prefix}_kurt",
        f"{prefix}_max",
        f"{prefix}_min",
        f"{prefix}_delta_v",
        f"{prefix}_duration_s",
        f"{prefix}_tau_s",
    ]


def relaxation_features(time_s, voltage_v, prefix: str = "relax") -> dict[str, float]:
    out = {name: np.nan for name in relaxation_feature_names(prefix)}
    t = np.asarray(time_s, dtype=float).ravel()
    v = np.asarray(voltage_v, dtype=float).ravel()
    keep = np.isfinite(t) & np.isfinite(v)
    t, v = t[keep], v[keep]
    if len(v) < 3:
        return out
    out[f"{prefix}_var"] = float(np.var(v))
    out[f"{prefix}_max"] = float(np.max(v))
    out[f"{prefix}_min"] = float(np.min(v))
    out[f"{prefix}_delta_v"] = float(v[-1] - v[0])
    out[f"{prefix}_duration_s"] = float(t[-1] - t[0])
    if np.var(v) > 0:
        out[f"{prefix}_skew"] = float(skew(v))
        out[f"{prefix}_kurt"] = float(kurtosis(v))
        gap = np.abs(v - v[-1])
        if gap[0] > 1e-9:
            reached = np.flatnonzero(gap <= gap[0] / np.e)
            if len(reached):
                out[f"{prefix}_tau_s"] = float(t[reached[0]] - t[0])
    return out
