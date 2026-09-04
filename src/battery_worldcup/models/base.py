"""The model interface every strategy family implements.

A model sees a :class:`ModelData` bundle and must return one prediction per row of
``data.targets``. What a model is allowed to see is decided by the task and the split, never
by the model: ``data.labels`` holds exactly the labels the task makes visible (all labels of
the training cells at fit time; for a forecasting task, only the target cell's labels up to
the forecast origin at predict time). A model that reads anything else breaks the leakage
rules in ``docs/05-evaluation-protocol.md``.

Every model declares :class:`InputRequirements` so the leaderboard can be filtered by what a
deployment can actually provide (a full charge? a rest? impedance?).
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field, replace
from typing import Any

import numpy as np
import pandas as pd

KEYS: list[str] = ["dataset", "cell_id", "cycle_index"]
PREDICTION_COLUMNS: list[str] = [*KEYS, "soh_pred"]


class NotFittedError(RuntimeError):
    """Raised when predict is called before fit."""


@dataclass(frozen=True)
class InputRequirements:
    """What a model needs in order to run.

    ``training_cells`` marks models that must be fitted on other labelled cells; filter-based
    models set it to False because they adapt to a single cell with no aging dataset.
    """

    features: bool = False
    timeseries: bool = False
    history: bool = False
    full_charge: bool = False
    rest: bool = False
    temperature: bool = False
    eis: bool = False
    training_cells: bool = True

    def as_dict(self) -> dict[str, bool]:
        return asdict(self)


@dataclass
class ModelData:
    """Everything a model may read for one fit or one predict call.

    ``targets``
        the (dataset, cell_id, cycle_index) rows to predict, in output order.
    ``labels``
        the visible SOH labels, as produced by :mod:`battery_worldcup.labels`.
    ``features``
        per-cycle features, as produced by :mod:`battery_worldcup.features` (optional).
    ``cycles``
        the per-cycle table of the bundle (optional).
    ``bundle``
        the full dataset bundle, for models that read raw timeseries (optional).
    """

    targets: pd.DataFrame
    labels: pd.DataFrame | None = None
    features: pd.DataFrame | None = None
    cycles: pd.DataFrame | None = None
    bundle: Any | None = None
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        missing = [c for c in KEYS if c not in self.targets.columns]
        if missing:
            raise ValueError(f"targets is missing {missing}")

    @property
    def cells(self) -> list[str]:
        return [str(c) for c in pd.unique(self.targets["cell_id"])]

    def labelled(self) -> pd.DataFrame:
        """Visible labels with a finite SOH value, sorted by cell and cycle."""
        if self.labels is None:
            return pd.DataFrame(columns=[*KEYS, "soh_capacity"])
        out = self.labels[self.labels["soh_capacity"].notna()]
        return out.sort_values(["cell_id", "cycle_index"])

    def history_for(self, cell_id: str) -> pd.DataFrame:
        """Visible labels of one cell, sorted by cycle."""
        lab = self.labelled()
        return lab[lab["cell_id"] == cell_id]

    def subset(self, cells: list[str] | set[str]) -> ModelData:
        """A copy restricted to ``cells`` (used to build train, validation and test views)."""
        keep = set(map(str, cells))

        def _filter(df: pd.DataFrame | None) -> pd.DataFrame | None:
            if df is None:
                return None
            return df[df["cell_id"].astype(str).isin(keep)].reset_index(drop=True)

        return replace(
            self,
            targets=_filter(self.targets),
            labels=_filter(self.labels),
            features=_filter(self.features),
            cycles=_filter(self.cycles),
        )

    def with_targets(self, targets: pd.DataFrame) -> ModelData:
        return replace(self, targets=targets.reset_index(drop=True))


class SOHModel(ABC):
    """Base class for SOH estimators.

    Subclasses set :attr:`name`, :attr:`family` (a code from ``docs/02-estimation-strategies.md``)
    and :attr:`requirements`, and implement :meth:`_fit` and :meth:`_predict`.
    """

    name: str = "unnamed"
    family: str = "S0"
    requirements: InputRequirements = InputRequirements()

    def __init__(self) -> None:
        self._fitted = False

    # -- public API -------------------------------------------------------------------------
    def fit(self, data: ModelData) -> SOHModel:
        self._fit(data)
        self._fitted = True
        return self

    def predict(self, data: ModelData) -> pd.DataFrame:
        if not self._fitted:
            raise NotFittedError(f"{self.name}: call fit before predict")
        out = self._predict(data)
        if len(out) != len(data.targets):
            raise ValueError(
                f"{self.name}: returned {len(out)} predictions for {len(data.targets)} targets"
            )
        missing = [c for c in PREDICTION_COLUMNS if c not in out.columns]
        if missing:
            raise ValueError(f"{self.name}: prediction frame is missing {missing}")
        return out.reset_index(drop=True)

    def get_params(self) -> dict[str, Any]:
        """Constructor parameters, for the result files.

        Only names that appear in ``__init__`` are reported. Scraping every attribute instead
        would fold fitted state into the record, and a model's identity would then change once
        it had seen data. Override this when a parameter is not stored under its own name.
        """
        names = [n for n in inspect.signature(type(self).__init__).parameters if n != "self"]
        return {n: getattr(self, n) for n in names if hasattr(self, n)}

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        """Default value of every constructor parameter, for compact reporting."""
        out = {}
        for name, parameter in inspect.signature(cls.__init__).parameters.items():
            if name != "self" and parameter.default is not inspect.Parameter.empty:
                out[name] = parameter.default
        return out

    def info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "family": self.family,
            "params": self.get_params(),
            "requirements": self.requirements.as_dict(),
        }

    # -- to implement -----------------------------------------------------------------------
    @abstractmethod
    def _fit(self, data: ModelData) -> None: ...

    @abstractmethod
    def _predict(self, data: ModelData) -> pd.DataFrame: ...

    # -- helpers for subclasses -------------------------------------------------------------
    @staticmethod
    def _frame(targets: pd.DataFrame, values, std=None) -> pd.DataFrame:
        out = targets[KEYS].copy().reset_index(drop=True)
        out["soh_pred"] = np.asarray(values, dtype=float)
        if std is not None:
            out["soh_std"] = np.asarray(std, dtype=float)
        return out


MODELS: dict[str, type[SOHModel]] = {}


def register(cls: type[SOHModel]) -> type[SOHModel]:
    """Class decorator adding a model to the registry under its ``name``."""
    if cls.name in MODELS:
        raise ValueError(f"duplicate model name {cls.name!r}")
    MODELS[cls.name] = cls
    return cls


def get_model(name: str, **params: Any) -> SOHModel:
    try:
        cls = MODELS[name]
    except KeyError as exc:
        raise KeyError(f"unknown model {name!r}; known: {sorted(MODELS)}") from exc
    return cls(**params)
