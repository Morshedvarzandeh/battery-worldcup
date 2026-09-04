"""Capacity-based SOH labels.

Labels are computed only from ``reference_capacity_ah`` on cycles flagged
``is_reference_test`` (rule ``"flag"``) or, for datasets whose every cycle is a full cycle
under fixed conditions, from ``discharge_capacity_ah`` on every cycle (rule
``"every_cycle"``). This module never reads timeseries or features, so labels and model inputs
stay independent by construction (see ``docs/decisions/0001-primary-soh-definition.md``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd


class LabelError(ValueError):
    """Raised when labels cannot be built for a cell."""


@dataclass(frozen=True)
class LabelRules:
    """How to turn per-cycle capacities into SOH labels for one dataset."""

    reference: Literal["flag", "every_cycle"] = "flag"
    q_ref: Literal["first", "median_first_k"] = "first"
    k: int = 3
    interpolate: bool = True
    valid_range: tuple[float, float] = (0.3, 1.15)
    eol_threshold: float = 0.8


RULES: dict[str, LabelRules] = {
    "synthetic": LabelRules(),
    "oxford": LabelRules(interpolate=False),
    "nasa": LabelRules(reference="every_cycle", q_ref="median_first_k", k=3),
    "calce": LabelRules(reference="every_cycle", q_ref="median_first_k", k=3),
    "matr": LabelRules(reference="every_cycle", q_ref="median_first_k", k=5),
}

LABEL_COLUMNS = [
    "dataset",
    "cell_id",
    "cycle_index",
    "q_ref_ah",
    "soh_capacity",
    "soh_interpolated",
    "is_label",
]


def rules_for(dataset: str) -> LabelRules:
    """Return the label rules registered for ``dataset`` (defaults for unknown datasets)."""
    return RULES.get(dataset, LabelRules())


def _label_cell(group: pd.DataFrame, rules: LabelRules) -> pd.DataFrame:
    group = group.sort_values("cycle_index")
    cell_id = str(group["cell_id"].iloc[0])
    idx = group["cycle_index"].to_numpy(dtype=np.int64)
    if rules.reference == "flag":
        mask = group["is_reference_test"].to_numpy(dtype=bool)
        cap = group["reference_capacity_ah"].to_numpy(dtype=float)
    else:
        mask = np.ones(len(group), dtype=bool)
        cap = group["discharge_capacity_ah"].to_numpy(dtype=float)
    mask = mask & np.isfinite(cap) & (cap > 0)
    if not mask.any():
        raise LabelError(f"cell {cell_id!r}: no usable reference capacity")

    ref_caps = cap[mask]
    if rules.q_ref == "first":
        q_ref = float(ref_caps[0])
    else:
        q_ref = float(np.median(ref_caps[: rules.k]))

    soh = np.full(len(group), np.nan)
    soh[mask] = cap[mask] / q_ref
    lo, hi = rules.valid_range
    out_of_range = mask & ~((soh >= lo) & (soh <= hi))
    soh[out_of_range] = np.nan
    is_label = mask & ~out_of_range

    interpolated = np.zeros(len(group), dtype=bool)
    if rules.interpolate and int(is_label.sum()) >= 2:
        x = idx[is_label]
        y = soh[is_label]
        fill = ~is_label & (idx >= x[0]) & (idx <= x[-1])
        soh[fill] = np.interp(idx[fill], x, y)
        interpolated[fill] = True

    return pd.DataFrame(
        {
            "dataset": group["dataset"].to_numpy(),
            "cell_id": cell_id,
            "cycle_index": idx,
            "q_ref_ah": q_ref,
            "soh_capacity": soh,
            "soh_interpolated": interpolated,
            "is_label": is_label,
        }
    )


def build_capacity_labels(cycles: pd.DataFrame, rules: LabelRules | None = None) -> pd.DataFrame:
    """Build one label row per cycle.

    ``soh_capacity`` is set at reference cycles (``is_label``) and, when the rules allow it,
    linearly interpolated between them (``soh_interpolated``). Cycles before the first or after
    the last reference test are left NaN: extrapolation is a model's job, not a label's.
    """
    if rules is None:
        keys = cycles["dataset"].unique()
        if len(keys) != 1:
            raise LabelError("pass explicit rules when labelling more than one dataset")
        rules = rules_for(str(keys[0]))
    parts = [_label_cell(g, rules) for _, g in cycles.groupby("cell_id", sort=False)]
    out = pd.concat(parts, ignore_index=True)[LABEL_COLUMNS]
    out["dataset"] = out["dataset"].astype("string")
    out["cell_id"] = out["cell_id"].astype("string")
    return out


def attach_labels(cycles: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    """Left-join label columns onto a cycles table."""
    keys = ["dataset", "cell_id", "cycle_index"]
    cols = keys + ["q_ref_ah", "soh_capacity", "soh_interpolated", "is_label"]
    return cycles.merge(labels[cols], on=keys, how="left", validate="one_to_one")


def cycle_life(
    labels: pd.DataFrame, threshold: float = 0.8, include_interpolated: bool = True
) -> pd.DataFrame:
    """First cycle at which SOH drops below ``threshold`` per cell.

    Cells that never reach the threshold are right-censored (``cycle_life`` NaN,
    ``censored`` True); ``last_labelled_cycle`` tells how far the record goes.
    """
    rows = []
    for cell_id, g in labels.groupby("cell_id", sort=False):
        g = g.sort_values("cycle_index")
        use = g["is_label"].to_numpy(dtype=bool)
        if include_interpolated:
            use = use | g["soh_interpolated"].to_numpy(dtype=bool)
        s = g[use]
        last = int(s["cycle_index"].iloc[-1]) if len(s) else -1
        below = s[s["soh_capacity"] < threshold]
        if len(below):
            rows.append((cell_id, float(below["cycle_index"].iloc[0]), False, last))
        else:
            rows.append((cell_id, np.nan, True, last))
    out = pd.DataFrame(rows, columns=["cell_id", "cycle_life", "censored", "last_labelled_cycle"])
    out["cell_id"] = out["cell_id"].astype("string")
    return out
