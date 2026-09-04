import numpy as np
import pandas as pd
import pytest

from battery_worldcup.labels import (
    LabelError,
    LabelRules,
    attach_labels,
    build_capacity_labels,
    cycle_life,
    rules_for,
)


def test_labels_match_truth_at_reference_cycles(small_bundle, small_truth):
    labels = build_capacity_labels(small_bundle.cycles)
    merged = labels.merge(small_truth, on=["cell_id", "cycle_index"])
    at_ref = merged[merged["is_label"]]
    assert len(at_ref) == 30
    assert np.abs(at_ref["soh_capacity"] - at_ref["soh_true"]).max() < 0.01
    assert not at_ref["soh_interpolated"].any()


def test_interpolation_is_flagged_and_bounded(small_bundle):
    labels = build_capacity_labels(small_bundle.cycles)
    interp = labels[labels["soh_interpolated"]]
    assert len(interp) > 0
    assert not interp["is_label"].any()
    assert interp["soh_capacity"].between(0.3, 1.15).all()
    # cycles after the last reference test are not extrapolated
    after_last = labels[labels["cycle_index"] > 50]
    assert after_last["soh_capacity"].isna().all()


def test_no_interpolation_when_disabled(small_bundle):
    labels = build_capacity_labels(small_bundle.cycles, LabelRules(interpolate=False))
    assert not labels["soh_interpolated"].any()
    assert labels["soh_capacity"].notna().sum() == labels["is_label"].sum()


def test_every_cycle_rule_uses_discharge_capacity(small_bundle):
    rules = LabelRules(reference="every_cycle", q_ref="median_first_k", k=3)
    labels = build_capacity_labels(small_bundle.cycles, rules)
    assert labels["is_label"].all()
    cyc = small_bundle.cycles.sort_values(["cell_id", "cycle_index"])
    for cell_id, g in cyc.groupby("cell_id"):
        expected = np.median(g["discharge_capacity_ah"].to_numpy()[:3])
        got = labels.loc[labels["cell_id"] == cell_id, "q_ref_ah"].iloc[0]
        assert got == pytest.approx(expected)


def test_out_of_range_reference_is_excluded(small_bundle):
    cycles = small_bundle.cycles.copy()
    ref_idx = cycles.index[cycles["is_reference_test"]]
    cycles.loc[ref_idx[1], "reference_capacity_ah"] = 5.0  # impossible value
    labels = build_capacity_labels(cycles)
    row = labels[
        (labels["cell_id"] == cycles.loc[ref_idx[1], "cell_id"])
        & (labels["cycle_index"] == cycles.loc[ref_idx[1], "cycle_index"])
    ]
    assert not bool(row["is_label"].iloc[0])
    assert bool(row["soh_interpolated"].iloc[0])  # filled from neighbours instead


def test_no_reference_raises():
    cycles = pd.DataFrame(
        {
            "dataset": ["d"] * 3,
            "cell_id": ["c"] * 3,
            "cycle_index": [0, 1, 2],
            "discharge_capacity_ah": [1.0, 0.99, 0.98],
            "is_reference_test": [False] * 3,
            "reference_capacity_ah": [np.nan] * 3,
        }
    )
    with pytest.raises(LabelError):
        build_capacity_labels(cycles, LabelRules())


def test_cycle_life_and_censoring(small_bundle, small_truth):
    labels = build_capacity_labels(small_bundle.cycles)
    life = cycle_life(labels, threshold=0.9)
    truth_life = small_truth[small_truth["soh_true"] < 0.9].groupby("cell_id")["cycle_index"].min()
    for _, row in life.iterrows():
        if row["censored"]:
            assert row["cell_id"] not in truth_life.index or truth_life[row["cell_id"]] > 50
        else:
            assert abs(row["cycle_life"] - truth_life[row["cell_id"]]) <= 10  # label spacing


def test_attach_labels(small_bundle):
    labels = build_capacity_labels(small_bundle.cycles)
    joined = attach_labels(small_bundle.cycles, labels)
    assert len(joined) == len(small_bundle.cycles)
    assert "soh_capacity" in joined.columns


def test_rules_registry():
    assert rules_for("oxford").interpolate is False
    assert rules_for("does-not-exist") == LabelRules()
