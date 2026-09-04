"""Assemble model inputs with the label visibility a task allows.

This is where the leakage rules become code. A nowcast view lets a model see the labels of the
training cells and nothing of the test cells; a forecast view additionally reveals the test
cell's own labels up to the forecast origin, and asks for predictions strictly after it. Models
never decide this for themselves: they read whatever :class:`ModelData` carries.
"""

from __future__ import annotations

import pandas as pd

from battery_worldcup.data.schema import DatasetBundle
from battery_worldcup.models.base import KEYS, ModelData
from battery_worldcup.tasks.splits import LeakageError, Split


def build_model_data(
    bundle: DatasetBundle,
    labels: pd.DataFrame,
    features: pd.DataFrame | None = None,
    include_interpolated: bool = False,
) -> ModelData:
    """All labelled rows of a dataset as one :class:`ModelData` to slice views from."""
    use = labels["is_label"].to_numpy(dtype=bool)
    if include_interpolated:
        use = use | labels["soh_interpolated"].to_numpy(dtype=bool)
    visible = labels[use & labels["soh_capacity"].notna()].reset_index(drop=True)
    return ModelData(
        targets=visible[KEYS].copy(),
        labels=visible,
        features=features,
        cycles=bundle.cycles,
        bundle=bundle,
        meta={"dataset": bundle.dataset, "include_interpolated": include_interpolated},
    )


def _rows_for(data: ModelData, cells: list[str]) -> pd.DataFrame:
    keep = set(map(str, cells))
    return data.labels[data.labels["cell_id"].astype(str).isin(keep)].reset_index(drop=True)


def nowcast_views(data: ModelData, split: Split, part: str = "test") -> tuple[ModelData, ModelData]:
    """(train view, evaluation view) for task T1.

    The evaluation view carries the test cells' target rows but only the training cells' labels,
    so a nowcast model cannot see any label of the cell it is scored on.
    """
    split.check()
    eval_cells = {"test": split.test, "val": split.val, "train": split.train}[part]
    train_labels = _rows_for(data, split.train)
    eval_rows = _rows_for(data, eval_cells)
    if not len(eval_rows):
        raise LeakageError(f"no labelled rows for the {part} cells")
    train_view = ModelData(
        targets=train_labels[KEYS].copy(),
        labels=train_labels,
        features=data.features,
        cycles=data.cycles,
        bundle=data.bundle,
        meta={**data.meta, "view": "train", "split_fold": split.fold},
    )
    eval_view = ModelData(
        targets=eval_rows[KEYS].copy(),
        labels=train_labels,
        features=data.features,
        cycles=data.cycles,
        bundle=data.bundle,
        meta={**data.meta, "view": part, "split_fold": split.fold, "truth": eval_rows},
    )
    return train_view, eval_view


def forecast_views(
    data: ModelData, split: Split, origin: int, part: str = "test"
) -> tuple[ModelData, ModelData]:
    """(train view, evaluation view) for tasks T2 and T4 with a forecast origin.

    The evaluation view reveals each test cell's labels up to and including ``origin`` and asks
    for the cycles after it. Cells with no label at or before the origin are dropped, because
    forecasting from nothing is a different task.
    """
    split.check()
    eval_cells = {"test": split.test, "val": split.val, "train": split.train}[part]
    train_labels = _rows_for(data, split.train)
    eval_rows = _rows_for(data, eval_cells)
    history = eval_rows[eval_rows["cycle_index"] <= origin]
    future = eval_rows[eval_rows["cycle_index"] > origin]
    usable = set(history["cell_id"].astype(str))
    future = future[future["cell_id"].astype(str).isin(usable)].reset_index(drop=True)
    if not len(future):
        raise LeakageError(f"no rows after cycle {origin} for the {part} cells")
    train_view = ModelData(
        targets=train_labels[KEYS].copy(),
        labels=train_labels,
        features=data.features,
        cycles=data.cycles,
        bundle=data.bundle,
        meta={**data.meta, "view": "train", "split_fold": split.fold, "origin": origin},
    )
    eval_view = ModelData(
        targets=future[KEYS].copy(),
        labels=pd.concat([train_labels, history], ignore_index=True),
        features=data.features,
        cycles=data.cycles,
        bundle=data.bundle,
        meta={
            **data.meta,
            "view": part,
            "split_fold": split.fold,
            "origin": origin,
            "truth": future,
        },
    )
    return train_view, eval_view


def truth_of(view: ModelData) -> pd.DataFrame:
    """The held-out labels an evaluation view was built from, for scoring."""
    truth = view.meta.get("truth")
    if truth is None:
        raise ValueError("this view carries no held-out truth; build it with a *_views helper")
    return truth
