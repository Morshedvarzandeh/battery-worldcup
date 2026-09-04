"""Loader for the Oxford Battery Degradation Dataset 1.

Source structure (from the dataset description): one MATLAB file with variables ``Cell1`` to
``Cell8``. Each cell is a struct with fields ``cyc0000``, ``cyc0100``, ... (one per
characterisation, every 100 cycles). Each characterisation holds four steps, ``C1ch`` (1C
charge), ``C1dc`` (1C discharge), ``OCVch`` (C/25 charge) and ``OCVdc`` (C/25 discharge), each
with arrays ``t`` (time), ``v`` (voltage, V), ``q`` (charge, mAh) and ``T`` (temperature, degC).

Mapping to the canonical schema:

* every characterisation becomes one cycle flagged ``is_reference_test``; its
  ``reference_capacity_ah`` is the 1C discharge capacity, which is the capacity reported in the
  source publication. The C/25 discharge capacity is kept in the extra column
  ``pseudo_ocv_discharge_capacity_ah``;
* the source has no current column, so ``current_a`` is the time derivative of ``q`` with the
  sign of the step; charge steps are typed ``charge`` because the CC/CV boundary is not
  resolved by the source;
* steps are decimated to at most ``max_points_per_step`` samples (endpoints kept) because the
  raw sampling would produce on the order of a hundred million rows.

Unit handling is heuristic and documented: ``q`` is treated as mAh when its range exceeds 20,
``t`` as hours when its maximum is below 200. This implementation follows the published
structure; validation against the real file is tracked in the dataset card.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.io import loadmat

from battery_worldcup.data.schema import DatasetBundle, StepType

DATASET_KEY = "oxford"
SOURCE_URL = "https://ora.ox.ac.uk/objects/uuid:03ba4b01-cfed-46d3-9b1a-7d4a7bdf6fac"

# (field name in the source, step type, current sign)
STEPS: tuple[tuple[str, StepType, float], ...] = (
    ("C1ch", StepType.CHARGE, +1.0),
    ("C1dc", StepType.DISCHARGE, -1.0),
    ("OCVch", StepType.CHARGE, +1.0),
    ("OCVdc", StepType.DISCHARGE, -1.0),
)


def _as_1d(value) -> np.ndarray:
    return np.atleast_1d(np.asarray(value, dtype=float)).ravel()


def _decimate(n: int, max_points: int) -> np.ndarray:
    if n <= max_points:
        return np.arange(n)
    return np.unique(np.linspace(0, n - 1, max_points).round().astype(int))


def _fields(obj) -> list[str]:
    names = getattr(obj, "_fieldnames", None)
    if names is None:
        raise ValueError("expected a MATLAB struct; load the file with struct_as_record=False")
    return list(names)


def _step_arrays(step) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    t = _as_1d(step.t)
    v = _as_1d(step.v)
    q = _as_1d(step.q)
    temp = _as_1d(step.T) if "T" in _fields(step) else np.full_like(t, np.nan)
    if float(np.nanmax(t)) < 200.0:  # hours -> seconds
        t = t * 3600.0
    q = np.abs(q - q[0])
    if float(np.nanmax(q)) > 20.0:  # mAh -> Ah
        q = q / 1000.0
    return t, v, q, temp


def _cell_sort_key(name: str) -> int:
    digits = "".join(ch for ch in name if ch.isdigit())
    return int(digits) if digits else 0


def load_oxford(path: str | Path, max_points_per_step: int = 2000) -> DatasetBundle:
    mat = loadmat(str(path), squeeze_me=True, struct_as_record=False)
    cell_names = sorted((k for k in mat if k.startswith("Cell")), key=_cell_sort_key)
    if not cell_names:
        raise ValueError(f"{path}: no 'CellN' variables found")

    cell_rows, cycle_rows, ts_frames = [], [], []
    for name in cell_names:
        cell = mat[name]
        cell_rows.append(
            {
                "dataset": DATASET_KEY,
                "cell_id": name,
                "chemistry": "Kokam pouch, LCO/NCO-type cathode, graphite anode",
                "form_factor": "pouch",
                "nominal_capacity_ah": 0.74,
                "nominal_voltage_v": 3.7,
                "manufacturer": "Kokam",
                "part_number": "SLPB533459H4",
                "test_temperature_c": 40.0,
                "protocol": (
                    "1C CC-CV charge and Urban Artemis drive-cycle discharge at 40 degC; "
                    "characterisation every 100 cycles: 1C charge/discharge and C/25 "
                    "pseudo-OCV charge/discharge"
                ),
                "licence": "see source",
                "source": SOURCE_URL,
            }
        )
        cyc_names = sorted(
            (f for f in _fields(cell) if f.startswith("cyc")), key=lambda s: int(s[3:])
        )
        for cyc_name in cyc_names:
            cyc = getattr(cell, cyc_name)
            cycle_index = int(cyc_name[3:])
            available = _fields(cyc)
            caps: dict[str, float] = {}
            energies: dict[str, float] = {}
            temps: list[np.ndarray] = []
            t_offset = 0.0
            for step_index, (field, step_type, sign) in enumerate(STEPS):
                if field not in available:
                    continue
                t, v, q, temp = _step_arrays(getattr(cyc, field))
                caps[field] = float(q[-1])
                energies[field] = (
                    float(np.sum(0.5 * (v[1:] + v[:-1]) * np.diff(q))) if len(q) > 1 else 0.0
                )
                temps.append(temp)
                current = sign * np.gradient(q, t) * 3600.0 if len(q) > 1 else np.zeros_like(q)
                keep = _decimate(len(t), max_points_per_step)
                ts_frames.append(
                    pd.DataFrame(
                        {
                            "dataset": DATASET_KEY,
                            "cell_id": name,
                            "cycle_index": cycle_index,
                            "step_index": step_index,
                            "step_type": step_type.value,
                            "time_s": t_offset + t[keep] - t[0],
                            "current_a": current[keep],
                            "voltage_v": v[keep],
                            "temperature_c": temp[keep],
                            "capacity_ah": q[keep],
                        }
                    )
                )
                t_offset += float(t[-1] - t[0])
            if "C1dc" not in caps:
                continue  # a characterisation without the 1C discharge carries no label
            all_temp = np.concatenate(temps) if temps else np.array([np.nan])
            cycle_rows.append(
                {
                    "dataset": DATASET_KEY,
                    "cell_id": name,
                    "cycle_index": cycle_index,
                    "start_time_s": np.nan,
                    "charge_capacity_ah": caps.get("C1ch", np.nan),
                    "discharge_capacity_ah": caps["C1dc"],
                    "charge_energy_wh": energies.get("C1ch", np.nan),
                    "discharge_energy_wh": energies.get("C1dc", np.nan),
                    "coulombic_efficiency": caps["C1dc"] / caps["C1ch"]
                    if "C1ch" in caps
                    else np.nan,
                    "mean_temperature_c": float(np.nanmean(all_temp)),
                    "max_temperature_c": float(np.nanmax(all_temp)),
                    "is_reference_test": True,
                    "reference_capacity_ah": caps["C1dc"],
                    "pseudo_ocv_discharge_capacity_ah": caps.get("OCVdc", np.nan),
                    "pseudo_ocv_charge_capacity_ah": caps.get("OCVch", np.nan),
                }
            )

    if not cycle_rows:
        raise ValueError(f"{path}: no characterisation cycles with a 1C discharge found")
    return DatasetBundle(
        cells=pd.DataFrame(cell_rows),
        cycles=pd.DataFrame(cycle_rows),
        timeseries=pd.concat(ts_frames, ignore_index=True) if ts_frames else None,
    ).coerced()
