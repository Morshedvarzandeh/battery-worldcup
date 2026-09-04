import pandas as pd
import pytest

from battery_worldcup.tasks import (
    LeakageError,
    build_model_data,
    forecast_views,
    nowcast_views,
    truth_of,
)


def test_nowcast_view_hides_every_test_label(model_data, split):
    train_view, eval_view = nowcast_views(model_data, split)
    train_cells, test_cells = set(split.train), set(split.test)
    assert set(train_view.labels["cell_id"]) == train_cells
    assert set(eval_view.targets["cell_id"]) == test_cells
    # the evaluation view carries no label of any cell it is scored on
    assert set(eval_view.labels["cell_id"]) & test_cells == set()
    for cell in test_cells:
        assert len(eval_view.history_for(cell)) == 0


def test_nowcast_truth_matches_targets(model_data, split):
    _, eval_view = nowcast_views(model_data, split)
    truth = truth_of(eval_view)
    assert len(truth) == len(eval_view.targets)
    assert truth["soh_capacity"].notna().all()


def test_forecast_view_reveals_only_history_up_to_the_origin(model_data, split):
    origin = 20
    train_view, eval_view = forecast_views(model_data, split, origin=origin)
    test_cells = set(split.test)
    assert set(train_view.labels["cell_id"]) == set(split.train)
    visible_test = eval_view.labels[eval_view.labels["cell_id"].isin(test_cells)]
    assert len(visible_test) > 0
    assert visible_test["cycle_index"].max() <= origin
    assert eval_view.targets["cycle_index"].min() > origin
    for cell in test_cells:
        assert eval_view.history_for(cell)["cycle_index"].max() <= origin


def test_forecast_origin_beyond_the_data_raises(model_data, split):
    with pytest.raises(LeakageError, match="no rows after"):
        forecast_views(model_data, split, origin=10_000)


def test_views_can_target_the_validation_part(model_data, split):
    _, val_view = nowcast_views(model_data, split, part="val")
    assert set(val_view.targets["cell_id"]) == set(split.val)


def test_include_interpolated_adds_rows(small_bundle, small_labels):
    sparse = build_model_data(small_bundle, small_labels)
    dense = build_model_data(small_bundle, small_labels, include_interpolated=True)
    assert len(dense.labels) > len(sparse.labels)
    assert sparse.labels["soh_interpolated"].sum() == 0


def test_truth_requires_an_evaluation_view(model_data):
    with pytest.raises(ValueError, match="no held-out truth"):
        truth_of(model_data)


def test_subset_restricts_every_table(model_data):
    cells = model_data.cells[:2]
    sub = model_data.subset(cells)
    assert set(sub.targets["cell_id"]) == set(cells)
    assert set(sub.labels["cell_id"]) == set(cells)
    assert isinstance(sub.cycles, pd.DataFrame)
