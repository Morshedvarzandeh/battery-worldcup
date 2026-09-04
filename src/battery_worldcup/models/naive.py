"""Naive baselines.

Every leaderboard table must contain these. They cost nothing and they answer the question a
reader always has: how much of this model's accuracy comes from the model, and how much from
the fact that SOH changes slowly and cells resemble each other?
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from battery_worldcup.models.base import InputRequirements, ModelData, SOHModel, register


@register
class ConstantSOH(SOHModel):
    """Predict the mean SOH of the training labels, ignoring the input entirely."""

    name = "constant"
    family = "S0"
    requirements = InputRequirements()

    def __init__(self) -> None:
        super().__init__()
        self.value = 1.0

    def _fit(self, data: ModelData) -> None:
        lab = data.labelled()
        self.value = float(lab["soh_capacity"].mean()) if len(lab) else 1.0

    def _predict(self, data: ModelData) -> pd.DataFrame:
        return self._frame(data.targets, np.full(len(data.targets), self.value))


@register
class LastKnownSOH(SOHModel):
    """Carry the target cell's last observed label forward.

    The baseline to beat for any forecasting task: a model that cannot beat "nothing changed"
    is not measuring degradation.
    """

    name = "last_known"
    family = "S0"
    requirements = InputRequirements(history=True, training_cells=False)

    def __init__(self) -> None:
        super().__init__()
        self.fallback = 1.0

    def _fit(self, data: ModelData) -> None:
        lab = data.labelled()
        self.fallback = float(lab["soh_capacity"].mean()) if len(lab) else 1.0

    def _predict(self, data: ModelData) -> pd.DataFrame:
        values = np.empty(len(data.targets))
        for cell_id, rows in data.targets.groupby("cell_id", sort=False):
            hist = data.history_for(str(cell_id))
            value = float(hist["soh_capacity"].iloc[-1]) if len(hist) else self.fallback
            values[rows.index.to_numpy()] = value
        return self._frame(data.targets, values)


@register
class LinearExtrapolation(SOHModel):
    """Fit a straight line to the last ``window`` observed labels and extrapolate it.

    Strong before a knee and badly wrong after one, which is exactly what makes it a useful
    reference on datasets that contain knees.
    """

    name = "linear_extrapolation"
    family = "S7"
    requirements = InputRequirements(history=True, training_cells=False)

    def __init__(self, window: int = 10, clip: tuple[float, float] = (0.0, 1.2)) -> None:
        super().__init__()
        self.window = int(window)
        self.clip_low, self.clip_high = float(clip[0]), float(clip[1])
        self.fallback = 1.0

    def _fit(self, data: ModelData) -> None:
        lab = data.labelled()
        self.fallback = float(lab["soh_capacity"].mean()) if len(lab) else 1.0

    def _predict(self, data: ModelData) -> pd.DataFrame:
        values = np.empty(len(data.targets))
        targets = data.targets.reset_index(drop=True)
        for cell_id, rows in targets.groupby("cell_id", sort=False):
            hist = data.history_for(str(cell_id)).tail(self.window)
            x = hist["cycle_index"].to_numpy(dtype=float)
            y = hist["soh_capacity"].to_numpy(dtype=float)
            xt = rows["cycle_index"].to_numpy(dtype=float)
            if len(x) >= 2 and np.ptp(x) > 0:
                slope, intercept = np.polyfit(x, y, 1)
                pred = intercept + slope * xt
            elif len(x) == 1:
                pred = np.full(len(xt), y[0])
            else:
                pred = np.full(len(xt), self.fallback)
            values[rows.index.to_numpy()] = np.clip(pred, self.clip_low, self.clip_high)
        return self._frame(targets, values)


@register
class MeanTrajectory(SOHModel):
    """Predict the mean SOH of the training cells at the same cycle index.

    This is the population prior. A per-cell model that does not beat it is not using the cell.
    """

    name = "mean_trajectory"
    family = "S0"
    requirements = InputRequirements()

    def __init__(self) -> None:
        super().__init__()
        self._curve: pd.Series | None = None
        self.fallback = 1.0

    def _fit(self, data: ModelData) -> None:
        lab = data.labelled()
        if not len(lab):
            self._curve = None
            return
        self._curve = lab.groupby("cycle_index")["soh_capacity"].mean().sort_index()
        self.fallback = float(lab["soh_capacity"].mean())

    def _predict(self, data: ModelData) -> pd.DataFrame:
        xt = data.targets["cycle_index"].to_numpy(dtype=float)
        if self._curve is None or len(self._curve) == 0:
            return self._frame(data.targets, np.full(len(xt), self.fallback))
        x = self._curve.index.to_numpy(dtype=float)
        y = self._curve.to_numpy(dtype=float)
        return self._frame(data.targets, np.interp(xt, x, y))
