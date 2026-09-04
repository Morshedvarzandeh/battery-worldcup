"""Run one experiment configuration and write a result file.

The runner owns everything a model is not allowed to decide: which cells are in which fold,
which labels the model may see, and what is scored. A model receives a view and returns
predictions; the runner scores them against the held-out truth the view was built from.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from battery_worldcup.benchmark.config import ExperimentConfig
from battery_worldcup.benchmark.result import make_result, write_result
from battery_worldcup.data.schema import DatasetBundle
from battery_worldcup.data.synthetic import SyntheticConfig, make_synthetic
from battery_worldcup.features import extract_cycle_features
from battery_worldcup.labels import build_capacity_labels, cycle_life, rules_for
from battery_worldcup.metrics import point_metrics, probabilistic_metrics, trajectory_metrics
from battery_worldcup.models import MODELS, get_model
from battery_worldcup.tasks import (
    build_model_data,
    forecast_views,
    make_cell_folds,
    nowcast_views,
    truth_of,
)


def load_bundle(config: ExperimentConfig) -> DatasetBundle:
    dataset = config.dataset
    if dataset.bundle:
        return DatasetBundle.from_parquet(dataset.bundle).validate()
    if dataset.key == "synthetic":
        bundle, _ = make_synthetic(SyntheticConfig(**dataset.synthetic))
        return bundle.validate()
    raise ValueError(
        f"dataset {dataset.key!r} needs a 'bundle' path; convert it first with "
        f"`bwc data convert {dataset.key} --src <path>`"
    )


def needs_features(config: ExperimentConfig) -> bool:
    if config.features is not None:
        return bool(config.features)
    model_cls = MODELS.get(config.model.name)
    return bool(model_cls and model_cls.requirements.features)


def _cells_frame(bundle: DatasetBundle, labels: pd.DataFrame, threshold: float) -> pd.DataFrame:
    life = cycle_life(labels, threshold=threshold)
    cells = bundle.cells[["dataset", "cell_id"]].copy()
    cells["cell_id"] = cells["cell_id"].astype(str)
    cells = cells.merge(life, on="cell_id", how="left")
    # censored cells are ranked past the longest observed life so that stratification by
    # lifetime still spreads them across the folds instead of piling them into one
    longest = cells["cycle_life"].max()
    fallback = (longest if np.isfinite(longest) else 0.0) + cells["last_labelled_cycle"].fillna(0)
    cells["cycle_life"] = cells["cycle_life"].fillna(fallback)
    return cells


def score(predictions: pd.DataFrame, truth: pd.DataFrame, task_type: str, threshold: float) -> dict:
    merged = truth.merge(
        predictions, on=["dataset", "cell_id", "cycle_index"], how="inner", validate="one_to_one"
    )
    if not len(merged):
        raise ValueError("predictions and truth do not overlap")
    y = merged["soh_capacity"].to_numpy()
    p = merged["soh_pred"].to_numpy()
    metrics: dict[str, Any] = point_metrics(y, p)
    if "soh_std" in merged.columns and merged["soh_std"].notna().any():
        try:
            metrics.update(probabilistic_metrics(y, p, merged["soh_std"].to_numpy()))
        except ValueError:
            pass
    if task_type == "forecast":
        frame = merged.rename(columns={"soh_capacity": "y_true", "soh_pred": "y_pred"})
        metrics.update(trajectory_metrics(frame, threshold=threshold))
    return metrics


def aggregate(folds: list[dict]) -> dict[str, dict[str, float]]:
    """Mean and standard deviation of every numeric metric across folds and seeds."""
    keys: set[str] = set()
    for fold in folds:
        keys |= {k for k, v in fold["metrics"].items() if isinstance(v, int | float)}
    out = {}
    for key in sorted(keys):
        values = [
            float(f["metrics"][key])
            for f in folds
            if key in f["metrics"] and np.isfinite(float(f["metrics"][key]))
        ]
        if values:
            out[key] = {
                "mean": float(np.mean(values)),
                "std": float(np.std(values)),
                "n": len(values),
            }
    return out


def run_experiment(config: ExperimentConfig, results_dir: str | Path | None = None) -> dict:
    """Run every fold and seed of one configuration and return the result dictionary."""
    bundle = load_bundle(config)
    labels = build_capacity_labels(bundle.cycles, rules_for(bundle.dataset))
    features = extract_cycle_features(bundle) if needs_features(config) else None
    data = build_model_data(
        bundle, labels, features, include_interpolated=config.task.include_interpolated
    )
    cells = _cells_frame(bundle, labels, config.task.eol_threshold)

    folds: list[dict] = []
    fit_seconds = predict_seconds = 0.0
    model_info: dict[str, Any] = {}
    for seed in config.seeds:
        splits = make_cell_folds(
            cells,
            n_folds=config.split.n_folds,
            group_col=config.split.group_col,
            order_col=config.split.order_col,
            seed=config.split.seed + seed,
            version=config.split.version,
            dataset=bundle.dataset,
        )
        for split in splits:
            if config.task.type == "nowcast":
                train_view, eval_view = nowcast_views(data, split)
            else:
                train_view, eval_view = forecast_views(data, split, origin=config.task.origin)
            params = dict(config.model.params)
            if config.model.seed_param:
                params[config.model.seed_param] = seed
            model = get_model(config.model.name, **params)
            if not model_info:  # the description of the first fold, seed included
                model_info = model.info()

            started = time.perf_counter()
            model.fit(train_view)
            fit_seconds += time.perf_counter() - started
            started = time.perf_counter()
            predictions = model.predict(eval_view)
            predict_seconds += time.perf_counter() - started

            metrics = score(
                predictions, truth_of(eval_view), config.task.type, config.task.eol_threshold
            )
            folds.append(
                {
                    "fold": split.fold,
                    "seed": seed,
                    "n_train_cells": len(split.train),
                    "n_test_cells": len(split.test),
                    "n_targets": int(len(predictions)),
                    "metrics": metrics,
                }
            )

    runs = max(len(folds), 1)
    result = make_result(
        config=config.to_dict(),
        config_hash=config.hash(),
        model_info=model_info,
        folds=folds,
        metrics=aggregate(folds),
        cost={
            "fit_seconds_per_fold": fit_seconds / runs,
            "predict_seconds_per_fold": predict_seconds / runs,
            "folds": runs,
        },
        dataset=bundle.dataset,
        task=config.task.key,
    )
    if results_dir is not None:
        result["_path"] = str(write_result(result, results_dir))
    return result
