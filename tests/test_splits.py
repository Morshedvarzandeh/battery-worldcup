import json

import numpy as np
import pandas as pd
import pytest

from battery_worldcup.tasks import (
    LeakageError,
    Split,
    assert_no_leakage,
    leave_one_group_out,
    load_splits,
    make_cell_folds,
    save_splits,
)


def _cells(n=20, groups=("A", "B", "C", "D")):
    rng = np.random.default_rng(0)
    return pd.DataFrame(
        {
            "dataset": "demo",
            "cell_id": [f"c{i:02d}" for i in range(n)],
            "protocol": [groups[i % len(groups)] for i in range(n)],
            "cycle_life": rng.uniform(300, 1500, n),
        }
    )


def test_folds_are_disjoint_and_cover_every_cell_once():
    cells = _cells()
    splits = make_cell_folds(cells, n_folds=5, group_col="protocol", order_col="cycle_life")
    assert len(splits) == 5
    tested = [c for s in splits for c in s.test]
    assert sorted(tested) == sorted(cells["cell_id"])
    for s in splits:
        assert set(s.train) | set(s.val) | set(s.test) == set(cells["cell_id"])
        s.check()


def test_folds_are_balanced_across_groups():
    cells = _cells(n=40)
    splits = make_cell_folds(cells, n_folds=4, group_col="protocol")
    for s in splits:
        counts = cells[cells["cell_id"].isin(s.test)]["protocol"].value_counts()
        assert set(counts.index) == {"A", "B", "C", "D"}
        assert counts.max() - counts.min() <= 1


def test_ordering_spreads_lifetimes():
    cells = _cells(n=40)
    splits = make_cell_folds(cells, n_folds=4, order_col="cycle_life")
    means = [cells[cells["cell_id"].isin(s.test)]["cycle_life"].mean() for s in splits]
    assert max(means) - min(means) < 250  # far below the 1200 range of the data


def test_determinism_and_seed_effect():
    cells = _cells()
    a = make_cell_folds(cells, seed=1)
    b = make_cell_folds(cells, seed=1)
    c = make_cell_folds(cells, seed=2)
    assert [s.test for s in a] == [s.test for s in b]
    assert [s.test for s in a] != [s.test for s in c]


def test_leakage_is_detected():
    s = Split("d", "v1", 0, 3, train=["a", "b"], val=["c"], test=["a"])
    with pytest.raises(LeakageError, match="both train and test"):
        s.check()
    with pytest.raises(LeakageError, match="empty"):
        Split("d", "v1", 0, 3, train=["a"], val=[], test=["b"]).check()


def test_json_round_trip(tmp_path):
    splits = make_cell_folds(_cells(), n_folds=3)
    path = save_splits(splits, tmp_path / "demo" / "v1.json")
    back = load_splits(path)
    assert [s.to_dict() for s in back] == [s.to_dict() for s in splits]


def test_load_rejects_leaky_file(tmp_path):
    bad = Split("d", "v1", 0, 3, train=["a"], val=["b"], test=["a"])
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([bad.to_dict()]))
    with pytest.raises(LeakageError):
        load_splits(path)


def test_leave_one_group_out():
    cells = _cells(n=16)
    splits = leave_one_group_out(cells, "protocol", val_fraction=0.25)
    assert len(splits) == 4
    for s in splits:
        held = s.extra["held_out_group"]
        assert set(cells[cells["cell_id"].isin(s.test)]["protocol"]) == {held}
        assert held not in set(cells[cells["cell_id"].isin(s.train + s.val)]["protocol"])
    assert_no_leakage(splits)


def test_too_few_folds_rejected():
    with pytest.raises(ValueError):
        make_cell_folds(_cells(), n_folds=2)
