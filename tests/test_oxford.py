"""The Oxford loader is exercised on a file with the published structure and made-up numbers."""

import numpy as np
import pytest
from scipy.io import savemat

from battery_worldcup.data.loaders import load_dataset
from battery_worldcup.data.loaders.oxford import load_oxford
from battery_worldcup.labels import build_capacity_labels, rules_for


def _step(duration_h: float, capacity_mah: float, charge: bool, n: int = 200):
    t = np.linspace(0, duration_h * 3600.0, n)  # seconds
    q = np.linspace(0, capacity_mah, n)  # mAh, cumulative
    v = 3.0 + 1.2 * (q / capacity_mah) if charge else 4.2 - 1.2 * (q / capacity_mah)
    return {"t": t, "v": v, "q": q, "T": np.full(n, 40.0)}


def _fake_cell(n_char: int, fade_per_char: float):
    cell = {}
    for i in range(n_char):
        cap = 740.0 * (1 - fade_per_char * i)
        cell[f"cyc{100 * i:04d}"] = {
            "C1ch": _step(1.0, cap * 1.01, True),
            "C1dc": _step(1.0, cap, False),
            "OCVch": _step(25.0, cap * 1.03, True),
            "OCVdc": _step(25.0, cap * 1.02, False),
        }
    return cell


@pytest.fixture
def fake_mat(tmp_path):
    path = tmp_path / "Oxford_fake.mat"
    savemat(str(path), {"Cell1": _fake_cell(4, 0.02), "Cell2": _fake_cell(3, 0.05)})
    return path


def test_loader_maps_structure(fake_mat):
    bundle = load_oxford(fake_mat, max_points_per_step=50).validate()
    assert bundle.dataset == "oxford"
    assert bundle.cell_ids == ["Cell1", "Cell2"]
    cyc = bundle.cycles
    assert cyc["is_reference_test"].all()
    assert sorted(cyc[cyc["cell_id"] == "Cell1"]["cycle_index"]) == [0, 100, 200, 300]
    # mAh converted to Ah and the 1C discharge is the label capacity
    first = cyc[(cyc["cell_id"] == "Cell1") & (cyc["cycle_index"] == 0)].iloc[0]
    assert first["reference_capacity_ah"] == pytest.approx(0.740)
    assert first["pseudo_ocv_discharge_capacity_ah"] == pytest.approx(0.740 * 1.02)
    ts = bundle.timeseries
    assert set(ts["step_type"]) == {"charge", "discharge"}
    assert (ts.loc[ts["step_type"] == "charge", "current_a"] >= 0).all()
    assert (ts.loc[ts["step_type"] == "discharge", "current_a"] <= 0).all()
    # decimation keeps the step count small and time keeps increasing across steps
    per_step = ts.groupby(["cell_id", "cycle_index", "step_index"]).size()
    assert per_step.max() <= 50
    one = ts[(ts["cell_id"] == "Cell1") & (ts["cycle_index"] == 0)]
    assert (np.diff(one["time_s"].to_numpy()) >= 0).all()


def test_labels_from_loader(fake_mat):
    bundle = load_dataset("oxford", fake_mat)
    labels = build_capacity_labels(bundle.cycles, rules_for("oxford"))
    cell2 = labels[labels["cell_id"] == "Cell2"].sort_values("cycle_index")
    assert cell2["soh_capacity"].to_numpy() == pytest.approx([1.0, 0.95, 0.90])
    assert not labels["soh_interpolated"].any()


def test_units_in_hours_and_ah_are_handled(tmp_path):
    step = _step(1.0, 740.0, False)
    step["t"] = step["t"] / 3600.0  # hours
    step["q"] = step["q"] / 1000.0  # Ah
    path = tmp_path / "units.mat"
    savemat(str(path), {"Cell1": {"cyc0000": {"C1dc": step}}})
    bundle = load_oxford(path).validate()
    assert bundle.cycles["reference_capacity_ah"].iloc[0] == pytest.approx(0.740)
    assert bundle.timeseries["time_s"].max() == pytest.approx(3600.0)
