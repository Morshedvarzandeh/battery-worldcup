import numpy as np
import pandas as pd
import pytest

from battery_worldcup.data.schema import (
    CELL_COLUMNS,
    CYCLE_COLUMNS,
    DatasetBundle,
    SchemaError,
    coerce,
)


def test_validate_passes_on_synthetic(small_bundle):
    assert small_bundle.validate() is small_bundle
    assert small_bundle.dataset == "synthetic"
    assert small_bundle.summary()["n_cells"] == 5


def test_coerce_adds_nullable_columns_and_keeps_extras():
    df = pd.DataFrame({"dataset": ["x"], "cell_id": ["c1"], "extra": [1]})
    out = coerce(df, CELL_COLUMNS)
    assert list(out.columns)[: len(CELL_COLUMNS)] == [c.name for c in CELL_COLUMNS]
    assert out["extra"].iloc[0] == 1
    assert out["nominal_capacity_ah"].isna().all()
    assert str(out["chemistry"].dtype) == "string"


def test_coerce_rejects_missing_required_column():
    with pytest.raises(SchemaError, match="cell_id"):
        coerce(pd.DataFrame({"dataset": ["x"]}), CELL_COLUMNS)


def test_duplicate_cycle_key_is_rejected(small_bundle):
    cycles = pd.concat([small_bundle.cycles, small_bundle.cycles.iloc[:1]], ignore_index=True)
    with pytest.raises(SchemaError, match="duplicate"):
        DatasetBundle(cells=small_bundle.cells, cycles=cycles).validate()


def test_unknown_cell_in_cycles_is_rejected(small_bundle):
    cycles = small_bundle.cycles.copy()
    cycles.loc[cycles.index[0], "cell_id"] = "GHOST"
    with pytest.raises(SchemaError, match="unknown cells"):
        DatasetBundle(cells=small_bundle.cells, cycles=cycles).validate()


def test_reference_cycle_needs_capacity(small_bundle):
    cycles = small_bundle.cycles.copy()
    first_ref = cycles.index[cycles["is_reference_test"]][0]
    cycles.loc[first_ref, "reference_capacity_ah"] = np.nan
    with pytest.raises(SchemaError, match="reference"):
        DatasetBundle(cells=small_bundle.cells, cycles=cycles).validate()


def test_time_must_not_go_backwards(small_bundle):
    ts = small_bundle.timeseries.copy()
    ts.loc[ts.index[1], "time_s"] = -1.0
    with pytest.raises(SchemaError, match="time_s"):
        DatasetBundle(
            cells=small_bundle.cells, cycles=small_bundle.cycles, timeseries=ts
        ).validate()


def test_unknown_step_type_is_rejected(small_bundle):
    ts = small_bundle.timeseries.copy()
    ts.loc[ts.index[0], "step_type"] = "teleport"
    with pytest.raises(SchemaError, match="step types"):
        DatasetBundle(
            cells=small_bundle.cells, cycles=small_bundle.cycles, timeseries=ts
        ).validate()


def test_parquet_round_trip(tmp_path, small_bundle):
    out = small_bundle.to_parquet(tmp_path / "bundle")
    assert (out / "bundle.json").exists()
    back = DatasetBundle.from_parquet(out).validate()
    assert back.summary() == small_bundle.summary()
    pd.testing.assert_frame_equal(
        back.cycles.reset_index(drop=True), coerce(small_bundle.cycles, CYCLE_COLUMNS)
    )
    assert len(back.timeseries) == len(small_bundle.timeseries)
