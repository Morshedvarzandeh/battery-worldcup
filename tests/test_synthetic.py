import numpy as np

from battery_worldcup.data.schema import StepType
from battery_worldcup.data.synthetic import SyntheticConfig, make_synthetic, open_circuit_voltage


def test_ocv_is_monotonic_and_in_range():
    soc = np.linspace(0, 1, 101)
    v = open_circuit_voltage(soc)
    assert np.all(np.diff(v) > 0)
    assert 3.3 < v[0] < 3.45 and 4.15 < v[-1] < 4.25


def test_population_shape_and_truth(small_bundle, small_truth):
    assert small_bundle.summary()["n_cycles"] == 5 * 60
    assert small_bundle.summary()["n_reference_cycles"] == 5 * 6
    first = small_truth[small_truth["cycle_index"] == 0]["soh_true"]
    last = small_truth[small_truth["cycle_index"] == 59]["soh_true"]
    assert np.allclose(first, 1.0)
    assert (last < first.to_numpy()).all()
    assert small_truth["knee_cycle"].notna().sum() > 0  # some cells have knees
    assert small_truth["knee_cycle"].isna().sum() > 0  # some do not


def test_reference_capacity_only_on_reference_cycles(small_bundle):
    cyc = small_bundle.cycles
    assert cyc.loc[cyc["is_reference_test"], "reference_capacity_ah"].notna().all()
    assert cyc.loc[~cyc["is_reference_test"], "reference_capacity_ah"].isna().all()
    # cycling capacity at 1C is below the reference capacity at C/10 on the same cell
    ref = cyc[cyc["is_reference_test"]].groupby("cell_id")["reference_capacity_ah"].first()
    one_c = cyc[~cyc["is_reference_test"]].groupby("cell_id")["discharge_capacity_ah"].first()
    assert (one_c < ref).all()


def test_sign_conventions_and_steps(small_bundle):
    ts = small_bundle.timeseries
    assert set(ts["step_type"].unique()) == {
        StepType.CC_CHARGE.value,
        StepType.CV_CHARGE.value,
        StepType.REST.value,
        StepType.DISCHARGE.value,
    }
    assert (ts.loc[ts["step_type"] == "cc_charge", "current_a"] > 0).all()
    assert (ts.loc[ts["step_type"] == "discharge", "current_a"] < 0).all()
    assert (ts.loc[ts["step_type"] == "rest", "current_a"] == 0).all()
    assert ts["voltage_v"].between(2.8, 4.2001).all()  # OCV floor minus IR drop


def test_generation_is_deterministic():
    a, _ = make_synthetic(SyntheticConfig(n_cells=2, n_cycles=5, rpt_every=5, points_per_step=8))
    b, _ = make_synthetic(SyntheticConfig(n_cells=2, n_cycles=5, rpt_every=5, points_per_step=8))
    assert a.cycles.equals(b.cycles)
    assert a.timeseries.equals(b.timeseries)


def test_without_timeseries():
    bundle, _ = make_synthetic(
        SyntheticConfig(n_cells=2, n_cycles=4, rpt_every=2, with_timeseries=False)
    )
    bundle.validate()
    assert bundle.timeseries is None
    assert bundle.cycles["charge_energy_wh"].isna().all()
