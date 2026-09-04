"""Model implementations, one module per strategy family.

Importing this package registers every built-in model in :data:`MODELS`; build one by name with
:func:`get_model`.
"""

from battery_worldcup.models.base import (
    MODELS,
    InputRequirements,
    ModelData,
    NotFittedError,
    SOHModel,
    get_model,
    register,
)
from battery_worldcup.models.ecm import ECMParameters, OCVCurve, estimate_r0, simulate
from battery_worldcup.models.empirical import FORMS, EmpiricalFade, Knee, detect_knee, fit_form
from battery_worldcup.models.filters import ECMKalmanFilter, build_ocv_from_cycle
from battery_worldcup.models.naive import (
    ConstantSOH,
    LastKnownSOH,
    LinearExtrapolation,
    MeanTrajectory,
)
from battery_worldcup.models.regression import ESTIMATORS, FeatureRegressor, make_regressor

__all__ = [
    "ESTIMATORS",
    "FORMS",
    "MODELS",
    "ConstantSOH",
    "ECMKalmanFilter",
    "ECMParameters",
    "EmpiricalFade",
    "FeatureRegressor",
    "InputRequirements",
    "Knee",
    "LastKnownSOH",
    "LinearExtrapolation",
    "MeanTrajectory",
    "ModelData",
    "OCVCurve",
    "NotFittedError",
    "SOHModel",
    "build_ocv_from_cycle",
    "detect_knee",
    "estimate_r0",
    "fit_form",
    "get_model",
    "make_regressor",
    "register",
    "simulate",
]
