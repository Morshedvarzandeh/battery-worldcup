"""Feature-based regressors (family S3).

A health-indicator table goes in, an SOH estimate comes out. The pipeline imputes missing
features (a feature can be missing because a dataset lacks the step it needs), scales them and
fits a scikit-learn estimator. Preprocessing statistics are computed on the training fold only,
which is leakage check 4 in ``docs/05-evaluation-protocol.md``.

Estimators that return a standard deviation (Gaussian process regression) fill a ``soh_std``
column, so the probabilistic metrics of phase 7 can score them without a wrapper.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, ConstantKernel, WhiteKernel
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNet, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVR

from battery_worldcup.models.base import KEYS, InputRequirements, ModelData, SOHModel, register

NON_FEATURE_PREFIXES = ("has_",)
#: Label columns. A feature table must never contain these, and a caller that passes an
#: explicit ``include`` list must not name them either: regressing SOH on SOH scores
#: perfectly and measures nothing.
LABEL_COLUMNS = frozenset(
    {"soh_capacity", "soh_resistance", "soh_interpolated", "is_label", "q_ref_ah", "soh_true"}
)


def _estimator(kind: str, **params: Any):
    if kind == "ridge":
        return Ridge(alpha=params.pop("alpha", 1.0), **params)
    if kind == "elastic_net":
        return ElasticNet(
            alpha=params.pop("alpha", 0.01),
            l1_ratio=params.pop("l1_ratio", 0.5),
            max_iter=params.pop("max_iter", 20000),
            **params,
        )
    if kind == "gpr":
        # The kernel bounds are scikit-learn's defaults on purpose. Widening them to silence the
        # "bound reached" warning let the optimiser find degenerate length scales on some folds,
        # which tripled this model's error and its spread across folds.
        kernel = ConstantKernel(1.0) * RBF(length_scale=np.sqrt(params.pop("n_features", 10.0)))
        kernel = kernel + WhiteKernel(noise_level=1e-3)
        return GaussianProcessRegressor(
            kernel=kernel, normalize_y=True, alpha=params.pop("alpha", 1e-6), **params
        )
    if kind == "svr":
        return SVR(C=params.pop("C", 10.0), epsilon=params.pop("epsilon", 0.005), **params)
    if kind == "random_forest":
        return RandomForestRegressor(
            n_estimators=params.pop("n_estimators", 300),
            random_state=params.pop("random_state", 0),
            n_jobs=params.pop("n_jobs", -1),
            **params,
        )
    if kind == "gradient_boosting":
        return GradientBoostingRegressor(
            n_estimators=params.pop("n_estimators", 300),
            learning_rate=params.pop("learning_rate", 0.05),
            random_state=params.pop("random_state", 0),
            **params,
        )
    raise ValueError(f"unknown estimator {kind!r}")


ESTIMATORS: tuple[str, ...] = (
    "ridge",
    "elastic_net",
    "gpr",
    "svr",
    "random_forest",
    "gradient_boosting",
)


def feature_columns(features: pd.DataFrame, include: list[str] | None = None) -> list[str]:
    """Numeric feature columns, excluding keys, availability flags and label columns."""
    if include is not None:
        leaked = sorted(set(include) & LABEL_COLUMNS)
        if leaked:
            raise ValueError(f"label columns cannot be used as features: {leaked}")
        return list(include)
    cols = []
    for c in features.columns:
        if c in KEYS or c in LABEL_COLUMNS or c.startswith(NON_FEATURE_PREFIXES):
            continue
        if pd.api.types.is_numeric_dtype(features[c]):
            cols.append(c)
    return cols


@register
class FeatureRegressor(SOHModel):
    """Impute, scale and regress SOH from per-cycle features."""

    name = "feature_regressor"
    family = "S3"
    requirements = InputRequirements(features=True, timeseries=True)

    def __init__(
        self,
        estimator: str = "gradient_boosting",
        include: list[str] | None = None,
        drop_all_nan: bool = True,
        clip: tuple[float, float] = (0.0, 1.2),
        **estimator_params: Any,
    ) -> None:
        super().__init__()
        if estimator not in ESTIMATORS:
            raise ValueError(f"unknown estimator {estimator!r}; known: {list(ESTIMATORS)}")
        self.estimator = estimator
        self.include = include
        self.drop_all_nan = bool(drop_all_nan)
        self.clip_low, self.clip_high = float(clip[0]), float(clip[1])
        self.estimator_params = dict(estimator_params)
        self._columns: list[str] = []
        self._pipeline: Pipeline | None = None
        self.fallback = 1.0

    # -- internals --------------------------------------------------------------------------
    def _matrix(self, features: pd.DataFrame) -> np.ndarray:
        missing = [c for c in self._columns if c not in features.columns]
        if missing:
            raise ValueError(f"{self.name}: features are missing {missing[:5]}")
        return features[self._columns].to_numpy(dtype=float)

    def _join(self, data: ModelData, rows: pd.DataFrame) -> pd.DataFrame:
        if data.features is None:
            raise ValueError(f"{self.name}: needs a features table")
        return rows.merge(data.features, on=KEYS, how="left", validate="one_to_one")

    # -- interface --------------------------------------------------------------------------
    def _fit(self, data: ModelData) -> None:
        lab = data.labelled()
        if not len(lab):
            raise ValueError(f"{self.name}: no labelled rows to fit on")
        table = self._join(data, lab[[*KEYS, "soh_capacity"]])
        # the column list comes from the feature table alone: the joined frame also carries the
        # label, and a feature list derived from it would leak the target into the model
        self._columns = feature_columns(data.features, self.include)
        if self.drop_all_nan:
            usable = table[self._columns].notna().any(axis=0)
            self._columns = [c for c, ok in zip(self._columns, usable, strict=True) if ok]
        if not self._columns:
            raise ValueError(f"{self.name}: no usable feature columns")
        y = table["soh_capacity"].to_numpy(dtype=float)
        params = dict(self.estimator_params)
        if self.estimator == "gpr":
            params.setdefault("n_features", float(len(self._columns)))
        self._pipeline = Pipeline(
            [
                ("impute", SimpleImputer(strategy="median")),
                ("scale", StandardScaler()),
                ("model", _estimator(self.estimator, **params)),
            ]
        )
        self._pipeline.fit(self._matrix(table), y)
        self.fallback = float(np.mean(y))

    def _predict(self, data: ModelData) -> pd.DataFrame:
        targets = data.targets.reset_index(drop=True)
        table = self._join(data, targets[KEYS])
        x = self._matrix(table)
        std = None
        if self.estimator == "gpr":
            steps = self._pipeline[:-1].transform(x)
            values, std = self._pipeline[-1].predict(steps, return_std=True)
        else:
            values = self._pipeline.predict(x)
        values = np.clip(values, self.clip_low, self.clip_high)
        return self._frame(targets, values, std)

    def get_params(self) -> dict:
        return {
            "estimator": self.estimator,
            "include": self.include,
            "drop_all_nan": self.drop_all_nan,
            "clip": [self.clip_low, self.clip_high],
            **self.estimator_params,
        }

    @property
    def feature_names(self) -> list[str]:
        return list(self._columns)

    def feature_importance(self) -> pd.Series | None:
        """Tree importances or absolute linear coefficients, when the estimator exposes them."""
        if self._pipeline is None:
            return None
        model = self._pipeline[-1]
        if hasattr(model, "feature_importances_"):
            values = np.asarray(model.feature_importances_, dtype=float)
        elif hasattr(model, "coef_"):
            values = np.abs(np.asarray(model.coef_, dtype=float).ravel())
        else:
            return None
        return pd.Series(values, index=self._columns).sort_values(ascending=False)


def make_regressor(estimator: str, **params: Any) -> FeatureRegressor:
    """Convenience factory: ``make_regressor("gpr")``."""
    return FeatureRegressor(estimator=estimator, **params)
