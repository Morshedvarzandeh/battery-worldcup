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
from battery_worldcup.models.empirical import FORMS, EmpiricalFade, Knee, detect_knee, fit_form
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
    "EmpiricalFade",
    "FeatureRegressor",
    "InputRequirements",
    "Knee",
    "LastKnownSOH",
    "LinearExtrapolation",
    "MeanTrajectory",
    "ModelData",
    "NotFittedError",
    "SOHModel",
    "detect_knee",
    "fit_form",
    "get_model",
    "make_regressor",
    "register",
]
