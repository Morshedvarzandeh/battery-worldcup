import json

import pandas as pd
import pytest
import yaml

from battery_worldcup.benchmark import (
    ExperimentConfig,
    build_leaderboard,
    load_results,
    run_experiment,
    to_frame,
)
from battery_worldcup.benchmark.leaderboard import model_key, overall_table
from battery_worldcup.benchmark.result import SCHEMA, result_path
from battery_worldcup.benchmark.runner import needs_features

TINY = {
    "key": "synthetic",
    "synthetic": {"n_cells": 6, "n_cycles": 30, "rpt_every": 10, "points_per_step": 12, "seed": 3},
}


def _config(name="t", model="mean_trajectory", params=None, task=None, seeds=(0,)):
    return ExperimentConfig.from_dict(
        {
            "name": name,
            "dataset": TINY,
            "task": task or {"type": "nowcast"},
            "split": {"n_folds": 3, "seed": 0},
            "model": {"name": model, "params": params or {}},
            "seeds": list(seeds),
        }
    )


# -- configuration ----------------------------------------------------------------------------
def test_config_round_trips_through_yaml(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({"name": "x", "model": {"name": "constant"}}))
    config = ExperimentConfig.load(path)
    assert config.name == "x"
    assert config.model.name == "constant"
    assert config.task.type == "nowcast"


def test_config_rejects_unknown_keys_and_missing_name():
    with pytest.raises(ValueError, match="unknown configuration keys"):
        ExperimentConfig.from_dict({"name": "x", "modle": {}})
    with pytest.raises(ValueError, match="needs a name"):
        ExperimentConfig.from_dict({"model": {}})


def test_forecast_task_requires_an_origin():
    with pytest.raises(ValueError, match="needs an origin"):
        ExperimentConfig.from_dict({"name": "x", "task": {"type": "forecast"}})
    with pytest.raises(ValueError, match="unknown task type"):
        ExperimentConfig.from_dict({"name": "x", "task": {"type": "guess"}})


def test_config_hash_is_stable_and_sensitive():
    a = _config(name="a")
    b = _config(name="a")
    c = _config(name="a", model="constant")
    assert a.hash() == b.hash()
    assert a.hash() != c.hash()
    assert a.task.key == "nowcast"
    assert _config(task={"type": "forecast", "origin": 20}).task.key == "forecast@20"


def test_needs_features_follows_the_model_requirements():
    assert needs_features(_config(model="feature_regressor")) is True
    assert needs_features(_config(model="mean_trajectory")) is False
    forced = _config(model="mean_trajectory")
    forced.features = True
    assert needs_features(forced) is True


# -- runner -----------------------------------------------------------------------------------
def test_runner_produces_one_fold_record_per_fold_and_seed(tmp_path):
    result = run_experiment(_config(seeds=(0, 1)), results_dir=tmp_path)
    assert len(result["folds"]) == 3 * 2
    assert {f["seed"] for f in result["folds"]} == {0, 1}
    assert result["schema"] == SCHEMA
    assert result["dataset"] == "synthetic"
    assert result["metrics"]["mae"]["n"] == 6
    assert result["model"]["name"] == "mean_trajectory"
    written = result_path(tmp_path, "synthetic", "nowcast", "t")
    assert written.exists()
    assert json.loads(written.read_text())["config_hash"] == result["config_hash"]


def test_runner_dry_run_writes_nothing(tmp_path):
    result = run_experiment(_config(), results_dir=None)
    assert "_path" not in result
    assert not list(tmp_path.iterdir())


def test_forecast_run_adds_trajectory_metrics(tmp_path):
    config = _config(model="last_known", task={"type": "forecast", "origin": 10})
    result = run_experiment(config, results_dir=tmp_path)
    assert result["task"] == "forecast@10"
    assert "trajectory_rmse" in result["metrics"]
    assert "eol_cycle_mae" in result["folds"][0]["metrics"]


def test_gpr_run_reports_calibration(tmp_path):
    config = _config(model="feature_regressor", params={"estimator": "gpr"})
    result = run_experiment(config, results_dir=tmp_path)
    assert "coverage_90" in result["metrics"]
    assert "crps" in result["metrics"]


def test_seed_param_is_injected(tmp_path):
    config = _config(model="feature_regressor", params={"estimator": "random_forest"})
    config.model.seed_param = "random_state"
    result = run_experiment(config, results_dir=tmp_path)
    assert result["metrics"]["mae"]["mean"] >= 0
    # the recorded description is the first fold's, so it carries that fold's seed
    assert result["model"]["params"]["random_state"] == 0
    assert result["config"]["model"]["seed_param"] == "random_state"


def test_unconvertible_dataset_is_reported():
    config = _config()
    config.dataset.key = "oxford"
    with pytest.raises(ValueError, match="bwc data convert"):
        run_experiment(config)


# -- results and leaderboard -------------------------------------------------------------------
def test_load_results_skips_foreign_json(tmp_path):
    run_experiment(_config(name="a"), results_dir=tmp_path)
    (tmp_path / "notes.json").write_text(json.dumps({"hello": "world"}))
    (tmp_path / "broken.json").write_text("{not json")
    results = load_results(tmp_path)
    assert len(results) == 1
    assert results[0]["name"] == "a"


def test_model_key_hides_defaults_and_run_parameters():
    assert model_key({"name": "empirical_fade", "params": {"form": "power", "min_points": 4}}) == (
        "empirical_fade"
    )
    assert "form=biexponential" in model_key(
        {"name": "empirical_fade", "params": {"form": "biexponential"}}
    )
    assert model_key({"name": "feature_regressor", "params": {"random_state": 3}}) == (
        "feature_regressor"
    )
    assert model_key({"name": "not_registered", "params": {"a": 1}}) == "not_registered(a=1)"


def test_leaderboard_renders_every_group(tmp_path):
    run_experiment(_config(name="mean", model="mean_trajectory"), results_dir=tmp_path)
    run_experiment(_config(name="const", model="constant"), results_dir=tmp_path)
    run_experiment(
        _config(name="fc", model="last_known", task={"type": "forecast", "origin": 10}),
        results_dir=tmp_path,
    )
    text = build_leaderboard(tmp_path)
    assert "# Leaderboard" in text
    assert "synthetic — nowcast" in text
    assert "synthetic — forecast@10" in text
    assert "Entries: 3 across 2 groups." in text
    # the per-group table names the model, not only the entry
    nowcast = text.split("synthetic — nowcast")[1]
    assert "mean_trajectory" in nowcast and "constant" in nowcast
    # the population mean must beat the constant baseline, so it is listed first
    assert nowcast.index("mean_trajectory") < nowcast.index("constant")


def test_leaderboard_never_pools_ranks_across_tasks():
    frame = pd.DataFrame(
        {
            "dataset": ["d", "d", "d"],
            "task": ["nowcast", "nowcast", "forecast@10"],
            "entry": ["a", "b", "a-fc"],
            "model_key": ["m1", "m2", "m1"],
            "family": ["S0", "S3", "S0"],
            "mae": [0.01, 0.02, 0.05],
        }
    )
    table = overall_table(frame)
    assert table.count("nowcast") == 2
    assert table.count("forecast@10") == 1


def test_empty_results_directory(tmp_path):
    assert "No results yet" in build_leaderboard(tmp_path)


def test_to_frame_summarises_requirements(tmp_path):
    run_experiment(_config(model="ecm_ekf"), results_dir=tmp_path)
    frame = to_frame(load_results(tmp_path))
    assert frame["requires"].iloc[0] == "timeseries, no training cells"
