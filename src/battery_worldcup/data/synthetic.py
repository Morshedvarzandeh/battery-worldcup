"""Synthetic cells for tests and examples.

The generator produces a small population with power-law fade, optional knees, rate-dependent
usable capacity, resistance growth and simple simulated voltage curves. It is deliberately
simple: it exists so that every module can be tested without downloading data and so that
leakage tests have a known ground truth. Nothing about it is calibrated to a real cell.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from battery_worldcup.data.schema import DatasetBundle, StepType

DATASET_KEY = "synthetic"
NOMINAL_CAPACITY_AH = 1.0
V_MAX = 4.2


@dataclass(frozen=True)
class SyntheticConfig:
    n_cells: int = 6
    n_cycles: int = 300
    rpt_every: int = 50
    seed: int = 0
    with_timeseries: bool = True
    points_per_step: int = 40
    knee_fraction: float = 0.5
    ambient_c: float = 25.0
    rate_capacity_factor: float = 0.97
    reference_c_rate: float = 0.1
    cycling_c_rate: float = 1.0


def open_circuit_voltage(soc: np.ndarray | float) -> np.ndarray:
    """A smooth, monotonic, NMC-like OCV curve spanning about 3.15 V to 4.2 V."""
    soc = np.asarray(soc, dtype=float)
    return 3.3 + 0.75 * soc + 0.15 * np.tanh(6.0 * (soc - 0.5))


def _trapezoid(y: np.ndarray, x: np.ndarray) -> float:
    if len(x) < 2:
        return 0.0
    return float(np.sum(0.5 * (y[1:] + y[:-1]) * np.diff(x)))


def _step_frame(
    *,
    dataset: str,
    cell_id: str,
    cycle_index: int,
    step_index: int,
    step_type: StepType,
    time_s: np.ndarray,
    current_a: np.ndarray,
    voltage_v: np.ndarray,
    temperature_c: np.ndarray,
    capacity_ah: np.ndarray,
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "dataset": dataset,
            "cell_id": cell_id,
            "cycle_index": cycle_index,
            "step_index": step_index,
            "step_type": step_type.value,
            "time_s": time_s,
            "current_a": current_a,
            "voltage_v": voltage_v,
            "temperature_c": temperature_c,
            "capacity_ah": capacity_ah,
        }
    )


def simulate_cycle(
    *,
    cell_id: str,
    cycle_index: int,
    capacity_ah: float,
    resistance_ohm: float,
    c_rate: float,
    ambient_c: float,
    rng: np.random.Generator,
    points: int = 40,
    dataset: str = DATASET_KEY,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Simulate CC charge, CV charge, rest and CC discharge for one cycle.

    Returns the timeseries frame and a dict with charge/discharge energy and temperatures.
    """
    i_cc = c_rate * NOMINAL_CAPACITY_AH
    heat = 25.0 * i_cc * i_cc * resistance_ohm
    frames: list[pd.DataFrame] = []
    t0 = 0.0

    # Step 0: constant-current charge from empty until the terminal voltage hits V_MAX.
    soc = np.linspace(0.0, 1.0, points)
    v = open_circuit_voltage(soc) + i_cc * resistance_ohm
    n_cc = int(np.argmax(v >= V_MAX)) if bool((v >= V_MAX).any()) else points
    n_cc = max(n_cc, 2)
    q_cc = soc[:n_cc] * capacity_ah
    t_cc = q_cc / i_cc * 3600.0
    temp_cc = ambient_c + heat * (1 - np.exp(-t_cc / 600.0)) + rng.normal(0, 0.05, n_cc)
    frames.append(
        _step_frame(
            dataset=dataset,
            cell_id=cell_id,
            cycle_index=cycle_index,
            step_index=0,
            step_type=StepType.CC_CHARGE,
            time_s=t0 + t_cc,
            current_a=np.full(n_cc, i_cc),
            voltage_v=np.minimum(v[:n_cc], V_MAX),
            temperature_c=temp_cc,
            capacity_ah=q_cc,
        )
    )
    t0 += float(t_cc[-1])
    e_charge = _trapezoid(np.minimum(v[:n_cc], V_MAX), q_cc)

    # Step 1: constant-voltage charge with exponentially decaying current until full.
    q_rem = max(capacity_ah - float(q_cc[-1]), 1e-6)
    tau = q_rem * 3600.0 / (i_cc * (1.0 - np.exp(-3.0)))
    t_cv = np.linspace(0.0, 3.0 * tau, 12)[1:]
    i_cv = i_cc * np.exp(-t_cv / tau)
    q_cv = i_cc * tau * (1.0 - np.exp(-t_cv / tau)) / 3600.0
    temp_cv = ambient_c + heat * np.exp(-t_cv / 900.0) + rng.normal(0, 0.05, len(t_cv))
    frames.append(
        _step_frame(
            dataset=dataset,
            cell_id=cell_id,
            cycle_index=cycle_index,
            step_index=1,
            step_type=StepType.CV_CHARGE,
            time_s=t0 + t_cv,
            current_a=i_cv,
            voltage_v=np.full(len(t_cv), V_MAX),
            temperature_c=temp_cv,
            capacity_ah=q_cv,
        )
    )
    t0 += float(t_cv[-1])
    e_charge += V_MAX * q_rem

    # Step 2: rest; the overpotential relaxes towards the OCV at full charge.
    t_rest = np.linspace(0.0, 600.0, 6)[1:]
    ocv_full = float(open_circuit_voltage(1.0))
    v_rest = ocv_full + (V_MAX - ocv_full) * np.exp(-t_rest / 120.0)
    frames.append(
        _step_frame(
            dataset=dataset,
            cell_id=cell_id,
            cycle_index=cycle_index,
            step_index=2,
            step_type=StepType.REST,
            time_s=t0 + t_rest,
            current_a=np.zeros(len(t_rest)),
            voltage_v=v_rest,
            temperature_c=ambient_c + rng.normal(0, 0.05, len(t_rest)),
            capacity_ah=np.zeros(len(t_rest)),
        )
    )
    t0 += float(t_rest[-1])

    # Step 3: constant-current discharge from full to empty.
    soc_d = np.linspace(1.0, 0.0, points)
    q_d = (1.0 - soc_d) * capacity_ah
    t_d = q_d / i_cc * 3600.0
    v_d = open_circuit_voltage(soc_d) - i_cc * resistance_ohm
    temp_d = ambient_c + heat * (1 - np.exp(-t_d / 600.0)) + rng.normal(0, 0.05, points)
    frames.append(
        _step_frame(
            dataset=dataset,
            cell_id=cell_id,
            cycle_index=cycle_index,
            step_index=3,
            step_type=StepType.DISCHARGE,
            time_s=t0 + t_d,
            current_a=np.full(points, -i_cc),
            voltage_v=v_d,
            temperature_c=temp_d,
            capacity_ah=q_d,
        )
    )
    e_discharge = _trapezoid(v_d, q_d)

    ts = pd.concat(frames, ignore_index=True)
    info = {
        "charge_energy_wh": e_charge,
        "discharge_energy_wh": e_discharge,
        "mean_temperature_c": float(ts["temperature_c"].mean()),
        "max_temperature_c": float(ts["temperature_c"].max()),
    }
    return ts, info


def make_synthetic(config: SyntheticConfig | None = None) -> tuple[DatasetBundle, pd.DataFrame]:
    """Generate a synthetic population.

    Returns the bundle and a ground-truth frame with columns ``cell_id``, ``cycle_index``,
    ``soh_true``, ``resistance_ohm``, ``knee_cycle`` and ``cycle_life_true``.
    """
    cfg = config or SyntheticConfig()
    rng = np.random.default_rng(cfg.seed)
    cell_rows, cycle_rows, truth_rows, ts_frames = [], [], [], []
    n_knee_cells = int(round(cfg.knee_fraction * cfg.n_cells))

    for i in range(cfg.n_cells):
        cell_id = f"SYN{i:03d}"
        q0 = float(rng.normal(NOMINAL_CAPACITY_AH, 0.01))
        a = float(rng.uniform(0.10, 0.25))
        b = float(rng.uniform(0.6, 0.9))
        has_knee = i < n_knee_cells
        knee = float(rng.uniform(0.5, 0.8) * cfg.n_cycles) if has_knee else np.nan
        c = float(rng.uniform(1.0, 3.0)) / cfg.n_cycles**2 if has_knee else 0.0
        n = np.arange(cfg.n_cycles, dtype=float)
        fade = a * (n / cfg.n_cycles) ** b
        if has_knee:
            fade = fade + c * np.clip(n - knee, 0.0, None) ** 2
        q_true = q0 * (1.0 - fade)
        r0 = float(rng.uniform(0.04, 0.06))
        resistance = r0 * (1.0 + 4.0 * fade)
        soh_true = q_true / q0
        below = np.flatnonzero(soh_true < 0.8)
        cycle_life_true = int(below[0]) if len(below) else np.nan

        cell_rows.append(
            {
                "dataset": DATASET_KEY,
                "cell_id": cell_id,
                "chemistry": "synthetic NMC-like",
                "form_factor": "virtual",
                "nominal_capacity_ah": NOMINAL_CAPACITY_AH,
                "nominal_voltage_v": 3.7,
                "manufacturer": "battery-worldcup",
                "part_number": "SYN-1",
                "test_temperature_c": cfg.ambient_c,
                "protocol": (
                    f"{cfg.cycling_c_rate:g}C CC-CV charge and {cfg.cycling_c_rate:g}C discharge; "
                    f"C/{1 / cfg.reference_c_rate:g} reference test every {cfg.rpt_every} cycles"
                ),
                "licence": "MIT",
                "source": "battery_worldcup.data.synthetic",
            }
        )

        for k in range(cfg.n_cycles):
            is_rpt = k % cfg.rpt_every == 0
            q_ref = float(q_true[k] * (1.0 + rng.normal(0.0, 0.001))) if is_rpt else np.nan
            if is_rpt:
                q_cycle, c_rate = q_ref, cfg.reference_c_rate
            else:
                q_cycle = float(q_true[k] * cfg.rate_capacity_factor * (1.0 + rng.normal(0, 0.003)))
                c_rate = cfg.cycling_c_rate
            q_charge = q_cycle * float(1.0 + abs(rng.normal(0.001, 0.001)))
            info = {
                "charge_energy_wh": np.nan,
                "discharge_energy_wh": np.nan,
                "mean_temperature_c": np.nan,
                "max_temperature_c": np.nan,
            }
            if cfg.with_timeseries:
                ts, info = simulate_cycle(
                    cell_id=cell_id,
                    cycle_index=k,
                    capacity_ah=q_cycle,
                    resistance_ohm=float(resistance[k]),
                    c_rate=c_rate,
                    ambient_c=cfg.ambient_c,
                    rng=rng,
                    points=cfg.points_per_step,
                )
                ts_frames.append(ts)
            cycle_rows.append(
                {
                    "dataset": DATASET_KEY,
                    "cell_id": cell_id,
                    "cycle_index": k,
                    "start_time_s": float(k) * 3600.0 * (2.0 / cfg.cycling_c_rate + 0.2),
                    "charge_capacity_ah": q_charge,
                    "discharge_capacity_ah": q_cycle,
                    "coulombic_efficiency": q_cycle / q_charge,
                    "is_reference_test": bool(is_rpt),
                    "reference_capacity_ah": q_ref,
                    **info,
                }
            )
            truth_rows.append(
                {
                    "cell_id": cell_id,
                    "cycle_index": k,
                    "soh_true": float(soh_true[k]),
                    "resistance_ohm": float(resistance[k]),
                    "knee_cycle": knee,
                    "cycle_life_true": cycle_life_true,
                }
            )

    bundle = DatasetBundle(
        cells=pd.DataFrame(cell_rows),
        cycles=pd.DataFrame(cycle_rows),
        timeseries=pd.concat(ts_frames, ignore_index=True) if ts_frames else None,
    ).coerced()
    return bundle, pd.DataFrame(truth_rows)
