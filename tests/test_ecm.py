"""The circuit model, the OCV curve and the Kalman filter.

The filter is exercised on synthetic cells, where the open-circuit voltage model it assumes is
exactly the one that generated the data. The accuracy here is therefore a best case and says
nothing about real cells; what the tests pin down is that the estimator is unbiased and stable
when its assumptions hold, and that it fails loudly when its inputs are missing.
"""

import numpy as np
import pandas as pd
import pytest

from battery_worldcup.data.synthetic import (
    SyntheticConfig,
    make_synthetic,
    open_circuit_voltage,
    simulate_cycle,
)
from battery_worldcup.labels import build_capacity_labels
from battery_worldcup.metrics import mae
from battery_worldcup.models import (
    ECMKalmanFilter,
    ECMParameters,
    ModelData,
    OCVCurve,
    build_ocv_from_cycle,
    estimate_r0,
    simulate,
)
from battery_worldcup.tasks import build_model_data


@pytest.fixture(scope="module")
def filter_bundle():
    bundle, truth = make_synthetic(
        SyntheticConfig(n_cells=3, n_cycles=40, rpt_every=10, points_per_step=40, seed=5)
    )
    return bundle.validate(), truth


# -- OCV curve --------------------------------------------------------------------------------
def test_ocv_round_trip():
    soc = np.linspace(0, 1, 101)
    curve = OCVCurve(soc, open_circuit_voltage(soc))
    assert curve.soc_of(curve.v(soc)) == pytest.approx(soc, abs=1e-6)
    assert curve.dv_dsoc(0.5) > 0


def test_ocv_rejects_a_degenerate_curve():
    with pytest.raises(ValueError, match="at least two"):
        OCVCurve([0.5], [3.7])


def test_ocv_from_a_discharge_curve_is_reversed_correctly():
    soc = np.linspace(1, 0, 200)
    q = (1.0 - soc) * 0.8  # cumulative discharged charge
    curve = OCVCurve.from_curve(q, open_circuit_voltage(soc))
    grid = np.linspace(0, 1, 50)
    assert curve.v(grid) == pytest.approx(open_circuit_voltage(grid), abs=5e-3)


def test_averaging_charge_and_discharge_cancels_the_ohmic_offset():
    soc = np.linspace(0, 1, 200)
    true = open_circuit_voltage(soc)
    offset = 0.05  # the ohmic drop: added while charging, subtracted while discharging
    charge = (soc * 1.0, true + offset)
    # a discharge is recorded as cumulative discharged charge against a falling voltage
    discharge = ((1.0 - soc[::-1]) * 1.0, true[::-1] - offset)
    curve = OCVCurve.from_charge_and_discharge(charge, discharge)
    grid = np.linspace(0.05, 0.95, 40)
    assert curve.v(grid) == pytest.approx(open_circuit_voltage(grid), abs=5e-3)
    # each curve on its own still carries the offset it was measured with
    assert OCVCurve.from_curve(*charge).v(0.5) > open_circuit_voltage(0.5) + 0.5 * offset


def test_build_ocv_from_cycle_recovers_the_true_curve(filter_bundle):
    bundle, _ = filter_bundle
    ts = bundle.timeseries
    first = ts[(ts["cell_id"] == "SYN000") & (ts["cycle_index"] == 0)]
    r0 = estimate_r0(first["voltage_v"].to_numpy(), first["current_a"].to_numpy())
    corrected = build_ocv_from_cycle(first, r0_ohm=r0)
    grid = np.linspace(0.05, 0.95, 40)
    assert corrected.v(grid) == pytest.approx(open_circuit_voltage(grid), abs=0.02)


def test_build_ocv_needs_a_usable_step():
    empty = pd.DataFrame(
        {
            "step_index": [0, 1],
            "step_type": ["rest", "rest"],
            "capacity_ah": [0.0, 0.0],
            "voltage_v": [4.0, 4.0],
            "current_a": [0.0, 0.0],
        }
    )
    with pytest.raises(ValueError, match="no usable step"):
        build_ocv_from_cycle(empty)


# -- circuit ----------------------------------------------------------------------------------
def test_simulate_reproduces_a_purely_ohmic_cell():
    rng = np.random.default_rng(0)
    ts, _ = simulate_cycle(
        cell_id="x",
        cycle_index=0,
        capacity_ah=1.0,
        resistance_ohm=0.05,
        c_rate=1.0,
        ambient_c=25.0,
        rng=rng,
        points=100,
    )
    discharge = ts[ts["step_type"] == "discharge"]
    soc = np.linspace(0, 1, 201)
    ocv = OCVCurve(soc, open_circuit_voltage(soc))
    params = ECMParameters(capacity_ah=1.0, r0_ohm=0.05, r1_ohm=0.0)
    v, soc_traj, _ = simulate(
        ocv,
        params,
        discharge["time_s"].to_numpy(),
        discharge["current_a"].to_numpy(),
        soc0=1.0,
    )
    assert v == pytest.approx(discharge["voltage_v"].to_numpy(), abs=5e-3)
    assert soc_traj[-1] == pytest.approx(0.0, abs=0.02)


def test_simulate_checks_input_lengths():
    ocv = OCVCurve([0.0, 1.0], [3.4, 4.2])
    with pytest.raises(ValueError, match="same length"):
        simulate(ocv, ECMParameters(), [0.0, 1.0], [1.0])


def test_estimate_r0_recovers_a_known_resistance():
    current = np.array([0.0, 0.0, -1.0, -1.0, 0.0, 0.0])
    voltage = 4.0 + current * 0.04
    assert estimate_r0(voltage, current) == pytest.approx(0.04)
    assert np.isnan(estimate_r0([4.0, 4.0], [0.0, 0.0]))


# -- the filter -------------------------------------------------------------------------------
def test_filter_tracks_capacity_without_any_training_data(filter_bundle):
    bundle, _ = filter_bundle
    labels = build_capacity_labels(bundle.cycles)
    data = build_model_data(bundle, labels)  # targets are the reference cycles
    untrained = ModelData(targets=data.targets, labels=None, bundle=bundle, cycles=bundle.cycles)
    model = ECMKalmanFilter().fit(untrained)
    pred = model.predict(untrained)
    truth = labels[labels["is_label"]].reset_index(drop=True)
    assert 100 * mae(truth["soh_capacity"], pred["soh_pred"]) < 1.5
    assert np.corrcoef(truth["soh_capacity"], pred["soh_pred"])[0, 1] > 0.99


def test_filter_declares_that_it_needs_no_training_cells():
    assert ECMKalmanFilter.requirements.training_cells is False
    assert ECMKalmanFilter.requirements.timeseries is True


def test_filter_trace_is_physical(filter_bundle):
    bundle, _ = filter_bundle
    ts = bundle.timeseries
    trace = ECMKalmanFilter().run_cell(ts[ts["cell_id"] == "SYN000"])
    assert len(trace) == 40
    assert (trace["soc_span"] <= 1.0 + 1e-9).all()  # a state of charge cannot exceed its range
    assert (trace["capacity_ah"] > 0).all()
    assert trace["capacity_ah"].iloc[-1] < trace["capacity_ah"].iloc[0]  # the cell aged
    assert trace["r0_ohm"].iloc[-1] > trace["r0_ohm"].iloc[0]  # and its resistance grew


def test_filter_needs_a_bundle():
    targets = pd.DataFrame({"dataset": ["s"], "cell_id": ["A"], "cycle_index": [0]})
    model = ECMKalmanFilter().fit(ModelData(targets=targets))
    with pytest.raises(ValueError, match="timeseries table"):
        model.predict(ModelData(targets=targets))


def test_filter_falls_back_for_a_cell_with_no_timeseries(filter_bundle):
    bundle, _ = filter_bundle
    targets = pd.DataFrame(
        {"dataset": ["synthetic"] * 2, "cell_id": ["SYN000", "GHOST"], "cycle_index": [0, 0]}
    )
    data = ModelData(targets=targets, bundle=bundle, cycles=bundle.cycles)
    out = ECMKalmanFilter().fit(data).predict(data)
    assert out["soh_pred"].notna().all()
