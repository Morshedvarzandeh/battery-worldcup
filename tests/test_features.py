import numpy as np
import pytest

from battery_worldcup.data.synthetic import (
    PLATEAU_SOCS,
    SyntheticConfig,
    make_synthetic,
    open_circuit_voltage,
    simulate_cycle,
)
from battery_worldcup.features import (
    ICASettings,
    cv_phase_features,
    differential_voltage,
    extract_cycle_features,
    feature_availability,
    find_curve_peaks,
    ica_features,
    incremental_capacity,
    partial_charge_features,
    relaxation_features,
)
from battery_worldcup.features.ica import dva_features


def _ocv_curve(n=400, capacity=1.0):
    soc = np.linspace(0, 1, n)
    return open_circuit_voltage(soc), soc * capacity


def test_ica_recovers_known_plateaus():
    v, q = _ocv_curve()
    curve = incremental_capacity(v, q)
    peaks = find_curve_peaks(curve, n_peaks=3)
    expected = sorted(float(open_circuit_voltage(s)) for s in PLATEAU_SOCS)
    assert len(peaks) == 2
    assert peaks["position"].to_numpy() == pytest.approx(expected, abs=0.015)
    assert (peaks["height"] > 1.0 / 0.9).all()  # above the baseline dQ/dV of the linear part


def test_ica_is_direction_independent():
    v, q = _ocv_curve()
    forward = ica_features(v, q)
    # a discharge step: voltage falls with time while cumulative capacity rises from zero
    backward = ica_features(v[::-1], q[-1] - q[::-1])
    for key in ("ica_peak0_x", "ica_peak1_x", "ica_area"):
        assert forward[key] == pytest.approx(backward[key], abs=0.01)
    assert forward["ica_area"] == pytest.approx(1.0, rel=0.02)  # integral of dQ/dV is the capacity


def test_ica_handles_noise_and_duplicates():
    rng = np.random.default_rng(0)
    v, q = _ocv_curve(n=1500)
    v_noisy = v + rng.normal(0, 0.002, len(v))
    v_noisy[100:110] = v_noisy[100]  # a stretch of repeated voltage readings
    peaks = find_curve_peaks(incremental_capacity(v_noisy, q, ICASettings(window_v=0.08)))
    expected = sorted(float(open_circuit_voltage(s)) for s in PLATEAU_SOCS)
    assert len(peaks) >= 2
    assert peaks["position"].to_numpy()[:2] == pytest.approx(expected, abs=0.03)


def test_dva_valleys_at_plateaus():
    v, q = _ocv_curve()
    curve = differential_voltage(v, q)
    feats = dva_features(v, q)
    assert curve.kind == "dva"
    assert feats["dva_peak0_x"] == pytest.approx(0.3, abs=0.02)
    assert feats["dva_peak1_x"] == pytest.approx(0.7, abs=0.02)


def test_degenerate_input_gives_nans_not_errors():
    feats = ica_features([3.5, 3.5], [0.0, 0.1])
    assert all(np.isnan(x) for x in feats.values())
    assert np.isnan(partial_charge_features([0.0], [3.5], [0.0])["pc_duration_s"])
    assert np.isnan(relaxation_features([0.0, 1.0], [4.0, 4.0])["relax_var"])
    assert np.isnan(cv_phase_features([], [])["cv_duration_s"])


def test_partial_charge_windows():
    rng = np.random.default_rng(1)
    ts, _ = simulate_cycle(
        cell_id="x",
        cycle_index=0,
        capacity_ah=1.0,
        resistance_ohm=0.05,
        c_rate=1.0,
        ambient_c=25.0,
        rng=rng,
        points=200,
    )
    cc = ts[ts["step_type"] == "cc_charge"]
    feats = partial_charge_features(
        cc["time_s"], cc["voltage_v"], cc["capacity_ah"], cc["current_a"]
    )
    assert feats["pc_mean_current_a"] == pytest.approx(1.0)
    assert feats["pc_3p6_3p9_time_s"] > 0 and feats["pc_3p9_4p1_time_s"] > 0
    # at 1 A, charge in Ah equals time in hours
    assert feats["pc_3p6_3p9_charge_ah"] == pytest.approx(
        feats["pc_3p6_3p9_time_s"] / 3600, rel=1e-6
    )
    # a window outside the data is NaN
    outside = partial_charge_features(
        cc["time_s"], cc["voltage_v"], cc["capacity_ah"], windows=((2.0, 2.5),)
    )
    assert np.isnan(outside["pc_2_2p5_time_s"])


def test_relaxation_features_on_synthetic_rest():
    rng = np.random.default_rng(2)
    ts, _ = simulate_cycle(
        cell_id="x",
        cycle_index=0,
        capacity_ah=1.0,
        resistance_ohm=0.05,
        c_rate=1.0,
        ambient_c=25.0,
        rng=rng,
    )
    rest = ts[ts["step_type"] == "rest"]
    feats = relaxation_features(rest["time_s"], rest["voltage_v"])
    assert feats["relax_delta_v"] < 0  # relaxes downwards after charge
    assert feats["relax_max"] == pytest.approx(rest["voltage_v"].max())
    assert feats["relax_var"] > 0
    assert np.isfinite(feats["relax_tau_s"])


def test_extract_features_tracks_capacity():
    cfg = SyntheticConfig(n_cells=2, n_cycles=80, rpt_every=20, points_per_step=60, seed=3)
    bundle, truth = make_synthetic(cfg)
    feats = extract_cycle_features(bundle)
    assert len(feats) == 2 * 80
    assert feats[["has_charge", "has_cv", "has_rest", "has_discharge"]].all().all()
    merged = feats.merge(truth, on=["cell_id", "cycle_index"])
    cycling = merged[merged["cycle_index"] % cfg.rpt_every != 0]  # same C-rate throughout
    for cell_id, g in cycling.groupby("cell_id"):
        corr = np.corrcoef(g["pc_3p6_3p9_charge_ah"], g["soh_true"])[0, 1]
        assert corr > 0.95, (cell_id, corr)
        corr_area = np.corrcoef(g["ica_ch_area"], g["soh_true"])[0, 1]
        assert corr_area > 0.95, (cell_id, corr_area)
    availability = feature_availability(feats)
    assert availability["pc_duration_s"] == 1.0
    assert availability["relax_var"] == 1.0
    assert 0.0 <= availability.min() <= 1.0
