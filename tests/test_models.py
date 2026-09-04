import numpy as np
import pandas as pd
import pytest

from battery_worldcup.models import (
    MODELS,
    EmpiricalFade,
    FeatureRegressor,
    LastKnownSOH,
    LinearExtrapolation,
    MeanTrajectory,
    ModelData,
    NotFittedError,
    detect_knee,
    fit_form,
    get_model,
)
from battery_worldcup.models.base import KEYS, InputRequirements, SOHModel, register
from battery_worldcup.models.regression import ESTIMATORS


def _labels(cell_id, cycles, soh):
    return pd.DataFrame(
        {
            "dataset": "t",
            "cell_id": cell_id,
            "cycle_index": np.asarray(cycles, dtype=np.int64),
            "soh_capacity": np.asarray(soh, dtype=float),
            "soh_interpolated": False,
            "is_label": True,
            "q_ref_ah": 1.0,
        }
    )


def _data(labels, targets_cycles, cell_id="A"):
    targets = pd.DataFrame(
        {
            "dataset": "t",
            "cell_id": cell_id,
            "cycle_index": np.asarray(targets_cycles, dtype=np.int64),
        }
    )
    return ModelData(targets=targets, labels=labels)


# -- interface --------------------------------------------------------------------------------
def test_registry_names_are_unique_and_buildable():
    assert {"constant", "last_known", "linear_extrapolation", "mean_trajectory"} <= set(MODELS)
    for name, cls in MODELS.items():
        assert cls.name == name
        assert cls.family.startswith("S")
        assert isinstance(cls.requirements, InputRequirements)
    assert isinstance(get_model("last_known"), LastKnownSOH)
    with pytest.raises(KeyError, match="unknown model"):
        get_model("nope")


def test_duplicate_registration_rejected():
    with pytest.raises(ValueError, match="duplicate"):

        @register
        class Clash(SOHModel):
            name = "constant"

            def _fit(self, data):
                pass

            def _predict(self, data):
                return None


def test_predict_before_fit_raises():
    with pytest.raises(NotFittedError):
        LastKnownSOH().predict(_data(_labels("A", [0], [1.0]), [1]))


def test_prediction_frame_contract():
    labels = _labels("A", [0, 10, 20], [1.0, 0.97, 0.94])
    data = _data(labels, [30, 40])
    out = MeanTrajectory().fit(data).predict(data)
    assert list(out.columns[:4]) == [*KEYS, "soh_pred"]
    assert len(out) == 2
    assert out["soh_pred"].notna().all()


def test_wrong_row_count_is_caught():
    class Broken(SOHModel):
        name = "broken"
        family = "S0"

        def _fit(self, data):
            pass

        def _predict(self, data):
            return self._frame(data.targets.head(1), [1.0])

    data = _data(_labels("A", [0, 1], [1.0, 0.99]), [0, 1])
    with pytest.raises(ValueError, match="predictions for"):
        Broken().fit(data).predict(data)


# -- naive baselines --------------------------------------------------------------------------
def test_last_known_carries_the_final_label_forward():
    labels = _labels("A", [0, 10, 20], [1.0, 0.97, 0.94])
    out = LastKnownSOH().fit(_data(labels, [0])).predict(_data(labels, [30, 60]))
    assert out["soh_pred"].to_numpy() == pytest.approx([0.94, 0.94])


def test_last_known_falls_back_for_an_unseen_cell():
    train = _labels("A", [0, 10], [1.0, 0.9])
    model = LastKnownSOH().fit(_data(train, [0]))
    out = model.predict(_data(train, [5], cell_id="B"))  # B has no history
    assert out["soh_pred"].iloc[0] == pytest.approx(0.95)


def test_linear_extrapolation_follows_a_line():
    cycles = np.arange(0, 50, 5)
    labels = _labels("A", cycles, 1.0 - 0.001 * cycles)
    out = LinearExtrapolation(window=10).fit(_data(labels, [0])).predict(_data(labels, [100, 200]))
    assert out["soh_pred"].to_numpy() == pytest.approx([0.9, 0.8], abs=1e-6)


def test_linear_extrapolation_is_clipped():
    labels = _labels("A", [0, 10], [1.0, 0.5])
    out = LinearExtrapolation().fit(_data(labels, [0])).predict(_data(labels, [100]))
    assert out["soh_pred"].iloc[0] == 0.0


def test_mean_trajectory_averages_across_cells():
    labels = pd.concat(
        [_labels("A", [0, 10], [1.0, 0.90]), _labels("B", [0, 10], [1.0, 0.80])],
        ignore_index=True,
    )
    out = MeanTrajectory().fit(_data(labels, [0])).predict(_data(labels, [0, 5, 10], cell_id="C"))
    assert out["soh_pred"].to_numpy() == pytest.approx([1.0, 0.925, 0.85])


# -- empirical --------------------------------------------------------------------------------
def test_fit_form_recovers_a_power_law():
    n = np.arange(1, 500, 5, dtype=float)
    popt = fit_form(n, 1.0 - 0.002 * n**0.6, form="power")
    assert popt == pytest.approx([0.002, 0.6], rel=0.02)


def test_fit_form_returns_none_on_degenerate_input():
    assert fit_form([1.0, 1.0, 1.0], [1.0, 0.9, 0.8], form="power") is None
    assert fit_form([1.0], [1.0], form="power") is None


def test_empirical_fade_extrapolates_its_own_cell():
    n = np.arange(0, 200, 10, dtype=float)
    labels = _labels("A", n, 1.0 - 0.001 * n**0.7)
    model = EmpiricalFade(form="power").fit(_data(labels, [0]))
    out = model.predict(_data(labels, [300, 400]))
    expected = 1.0 - 0.001 * np.array([300.0, 400.0]) ** 0.7
    assert out["soh_pred"].to_numpy() == pytest.approx(expected, abs=2e-3)


def test_empirical_fade_uses_the_population_prior_without_history():
    n = np.arange(0, 200, 10, dtype=float)
    train = _labels("A", n, 1.0 - 0.001 * n**0.7)
    model = EmpiricalFade(form="power", min_points=4).fit(_data(train, [0]))
    out = model.predict(_data(train, [100], cell_id="Z"))  # unseen cell, no history
    assert out["soh_pred"].iloc[0] == pytest.approx(1.0 - 0.001 * 100**0.7, abs=5e-3)


def test_unknown_form_rejected():
    with pytest.raises(ValueError, match="unknown form"):
        EmpiricalFade(form="quantum")


def test_knee_detection_on_synthetic_cells(small_truth):
    for cell_id, g in small_truth.groupby("cell_id"):
        knee = detect_knee(g["cycle_index"], g["soh_true"])
        true_knee = g["knee_cycle"].iloc[0]
        if np.isfinite(true_knee):
            assert knee.found, cell_id
            assert abs(knee.point - true_knee) < 0.25 * len(g), (cell_id, knee.point, true_knee)
            assert knee.slope_after < knee.slope_before
        else:
            assert not knee.found, (cell_id, knee)


def test_knee_detection_needs_enough_points():
    assert not detect_knee([0, 1, 2], [1.0, 0.99, 0.98]).found


# -- feature regressor ------------------------------------------------------------------------
def test_feature_regressor_needs_features():
    labels = _labels("A", [0, 10], [1.0, 0.9])
    with pytest.raises(ValueError, match="features table"):
        FeatureRegressor(estimator="ridge").fit(_data(labels, [0]))


def test_feature_columns_never_include_the_label():
    from battery_worldcup.features.extract import KEY_COLUMNS
    from battery_worldcup.models.regression import LABEL_COLUMNS, feature_columns

    table = pd.DataFrame(
        {
            "dataset": ["d"],
            "cell_id": ["A"],
            "cycle_index": [0],
            "soh_capacity": [0.9],
            "is_label": [True],
            "has_charge": [True],
            "ica_max": [1.0],
        }
    )
    cols = feature_columns(table)
    assert cols == ["ica_max"]
    assert not set(cols) & LABEL_COLUMNS
    assert not set(cols) & set(KEY_COLUMNS)
    with pytest.raises(ValueError, match="label columns"):
        feature_columns(table, include=["ica_max", "soh_capacity"])


def test_feature_regressor_rejects_unknown_estimator():
    with pytest.raises(ValueError, match="unknown estimator"):
        FeatureRegressor(estimator="crystal_ball")


def test_all_estimators_run_and_gpr_reports_std(nowcast_views_fixture):
    train_view, eval_view, truth = nowcast_views_fixture
    for estimator in ESTIMATORS:
        model = FeatureRegressor(estimator=estimator).fit(train_view)
        out = model.predict(eval_view)
        assert len(out) == len(truth)
        assert out["soh_pred"].notna().all()
        assert out["soh_pred"].between(0.0, 1.2).all()
        if estimator == "gpr":
            assert "soh_std" in out.columns and (out["soh_std"] > 0).all()


def test_feature_regressor_beats_the_population_prior(nowcast_views_fixture):
    from battery_worldcup.metrics import mae

    train_view, eval_view, truth = nowcast_views_fixture
    prior = MeanTrajectory().fit(train_view).predict(eval_view)
    model = FeatureRegressor(estimator="gradient_boosting").fit(train_view)
    pred = model.predict(eval_view)
    y = truth["soh_capacity"].to_numpy()
    assert mae(y, pred["soh_pred"]) < 0.02
    assert mae(y, pred["soh_pred"]) < mae(y, prior["soh_pred"])


def test_feature_importance_is_exposed(nowcast_views_fixture):
    train_view, _, _ = nowcast_views_fixture
    model = FeatureRegressor(estimator="random_forest").fit(train_view)
    importance = model.feature_importance()
    assert importance is not None and len(importance) == len(model.feature_names)
    assert importance.iloc[0] >= importance.iloc[-1]
    assert FeatureRegressor(estimator="svr").fit(train_view).feature_importance() is None
