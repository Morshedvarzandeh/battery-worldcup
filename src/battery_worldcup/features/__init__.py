"""Health indicators computed from one cycle's signals.

Every function here takes plain arrays and returns a flat dict of features; nothing reads the
label tables (see ``docs/05-evaluation-protocol.md``, leakage check 3).
"""

from battery_worldcup.features.extract import (
    FeatureConfig,
    extract_cycle_features,
    feature_availability,
)
from battery_worldcup.features.ica import (
    Curve,
    DVASettings,
    ICASettings,
    differential_voltage,
    find_curve_peaks,
    ica_features,
    incremental_capacity,
)
from battery_worldcup.features.partial_charge import (
    DEFAULT_WINDOWS,
    cv_phase_features,
    partial_charge_features,
)
from battery_worldcup.features.relaxation import relaxation_features

__all__ = [
    "DEFAULT_WINDOWS",
    "Curve",
    "DVASettings",
    "FeatureConfig",
    "ICASettings",
    "cv_phase_features",
    "differential_voltage",
    "extract_cycle_features",
    "feature_availability",
    "find_curve_peaks",
    "ica_features",
    "incremental_capacity",
    "partial_charge_features",
    "relaxation_features",
]
