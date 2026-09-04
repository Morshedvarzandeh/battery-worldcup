"""Model-based adaptive estimation (family S2).

A filter needs no aging dataset: it tracks the state of charge of a single cell with an
extended Kalman filter and reads capacity out of the relation between the charge that flowed
and the state of charge that changed. That makes it the honest reference for "what can a
battery management system do on its own", and the only family in the benchmark whose
:class:`InputRequirements` set ``training_cells`` to False.

How capacity is tracked
-----------------------
Within one cycle the filter integrates current to get the charge that flowed, ``dQ``, and takes
the change in the filtered state of charge, ``dSOC``. Over a deep enough excursion these are
related by ``dQ = capacity * dSOC``, which is a scalar regression solved recursively with a
forgetting factor. Shallow cycles carry almost no capacity information and are skipped, which
is a real property of the method and not a defect of this implementation.

The state of charge is only observable where the open-circuit voltage curve has slope, so the
filter leans on rests and on the ends of a discharge. Capacity is reported relative to the
filter's own first converged estimate, so the model never needs a labelled reference.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from battery_worldcup.data.schema import StepType
from battery_worldcup.models.base import InputRequirements, ModelData, SOHModel, register
from battery_worldcup.models.ecm import MIN_SOC_SPAN, ECMParameters, OCVCurve, estimate_r0

REST_TYPES = (StepType.REST.value,)
LOW_RATE_TYPES = (
    StepType.CC_CHARGE.value,
    StepType.CHARGE.value,
    StepType.DISCHARGE.value,
)


def build_ocv_from_cycle(cycle: pd.DataFrame, r0_ohm: float = 0.0) -> OCVCurve:
    """Approximate a pseudo-OCV curve from the lowest-rate steps of one cycle.

    Even a slow step is not the open-circuit voltage: it is offset by the ohmic drop ``i * r0``.
    That offset is removed here, because a curve built from a discharge alone sits below the
    true curve, and a filter using it would drive the state of charge off the top of the curve
    every time the cell rests near full charge. When a charge and a discharge of the same rate
    cover the same capacity range, their average is used as well, which cancels whatever ohmic
    offset ``r0_ohm`` did not.
    """
    candidates = []
    for _, step in cycle.groupby("step_index", sort=True):
        if str(step["step_type"].iloc[0]) not in LOW_RATE_TYPES:
            continue
        q = step["capacity_ah"].to_numpy(dtype=float)
        current = step["current_a"].to_numpy(dtype=float)
        v = step["voltage_v"].to_numpy(dtype=float) - current * r0_ohm
        i = np.abs(current)
        if len(q) < 5 or not np.isfinite(q).all() or np.ptp(q) <= 0:
            continue
        candidates.append(
            {
                "rate": float(np.nanmedian(i)),
                "rising": float(np.mean(np.diff(v))) > 0,
                "span": float(np.ptp(q)),
                "q": q,
                "v": v,
            }
        )
    if not candidates:
        raise ValueError("no usable step to build an OCV curve from")
    rate = min(c["rate"] for c in candidates)
    at_rate = [c for c in candidates if c["rate"] <= rate * 1.5]
    widest = max(at_rate, key=lambda c: c["span"])
    # Averaging a charge against a discharge cancels the ohmic offset, but only when both cover
    # the same capacity range. A constant-current charge that stops at the voltage limit covers
    # less than the full discharge that follows it, and normalising each step to its own span
    # would stretch one curve against the other. When the spans disagree, take the widest step.
    opposite = [
        c
        for c in at_rate
        if c["rising"] != widest["rising"]
        and abs(c["span"] - widest["span"]) <= 0.05 * widest["span"]
    ]
    if opposite:
        other = opposite[0]
        up, down = (widest, other) if widest["rising"] else (other, widest)
        return OCVCurve.from_charge_and_discharge((up["q"], up["v"]), (down["q"], down["v"]))
    return OCVCurve.from_curve(widest["q"], widest["v"])


@register
class ECMKalmanFilter(SOHModel):
    """Extended Kalman filter on a Thevenin circuit, with recursive capacity estimation.

    Parameters
    ----------
    r0_ohm, r1_ohm, c1_farad:
        circuit parameters. ``r0_ohm`` is re-estimated per cell from current steps unless
        ``adapt_r0`` is False.
    sigma_v:
        measurement standard deviation in volts.
    sigma_soc, sigma_v1:
        process standard deviations per step. ``sigma_soc`` must be large enough that the
        voltage can still correct the integrated state of charge; too small a value lets the
        filter trust its own coulomb counting and drift.
    forgetting:
        memory of the capacity estimate, between 0 and 1. Small values follow the newest
        measurement; large values smooth over many cycles.
    min_soc_span:
        the smallest state-of-charge excursion a cycle must show before it updates capacity.
    """

    name = "ecm_ekf"
    family = "S2"
    requirements = InputRequirements(timeseries=True, training_cells=False)

    def __init__(
        self,
        r0_ohm: float = 0.05,
        r1_ohm: float = 0.0,
        c1_farad: float = 2000.0,
        sigma_v: float = 5e-3,
        sigma_soc: float = 1e-3,
        sigma_v1: float = 1e-4,
        forgetting: float = 0.3,
        min_soc_span: float = MIN_SOC_SPAN,
        adapt_r0: bool = True,
        clip: tuple[float, float] = (0.0, 1.2),
    ) -> None:
        super().__init__()
        self.r0_ohm = float(r0_ohm)
        self.r1_ohm = float(r1_ohm)
        self.c1_farad = float(c1_farad)
        self.sigma_v = float(sigma_v)
        self.sigma_soc = float(sigma_soc)
        self.sigma_v1 = float(sigma_v1)
        self.forgetting = float(forgetting)
        self.min_soc_span = float(min_soc_span)
        self.adapt_r0 = bool(adapt_r0)
        self.clip_low, self.clip_high = float(clip[0]), float(clip[1])
        self.traces: dict[str, pd.DataFrame] = {}

    # -- fitting is optional ----------------------------------------------------------------
    def _fit(self, data: ModelData) -> None:
        """Take a prior series resistance from the training cells, when any are visible.

        The filter runs without this; it only sharpens the initial guess.
        """
        bundle = data.bundle
        if bundle is None or bundle.timeseries is None or not len(data.labelled()):
            return
        cells = set(data.labelled()["cell_id"].astype(str))
        ts = bundle.timeseries
        first = ts[
            ts["cell_id"].astype(str).isin(cells) & (ts["cycle_index"] == ts["cycle_index"].min())
        ]
        values = []
        for _, g in first.groupby("cell_id", sort=False):
            r0 = estimate_r0(g["voltage_v"].to_numpy(), g["current_a"].to_numpy())
            if np.isfinite(r0) and r0 > 0:
                values.append(r0)
        if values:
            self.r0_ohm = float(np.median(values))

    # -- the filter -------------------------------------------------------------------------
    def run_cell(self, cell_ts: pd.DataFrame) -> pd.DataFrame:
        """Run the filter over one cell's timeseries.

        Returns one row per cycle with the tracked capacity, the series resistance and the
        state-of-charge excursion that cycle offered.
        """
        cell_ts = cell_ts.sort_values(["cycle_index", "step_index", "time_s"])
        cycles = list(cell_ts.groupby("cycle_index", sort=True))
        first = cycles[0][1]

        params = ECMParameters(
            capacity_ah=self._initial_capacity(first),
            r0_ohm=self.r0_ohm,
            r1_ohm=self.r1_ohm,
            c1_farad=self.c1_farad,
        )
        if self.adapt_r0:
            r0 = estimate_r0(first["voltage_v"].to_numpy(), first["current_a"].to_numpy())
            if np.isfinite(r0) and r0 > 0:
                params.r0_ohm = float(r0)
        ocv = build_ocv_from_cycle(first, r0_ohm=params.r0_ohm)

        # filter state: [soc, v1] with covariance p; capacity regression state: capacity, p_cap
        x = np.array([0.5, 0.0])
        p = np.diag([0.1**2, 1e-4])
        q_proc = np.diag([self.sigma_soc**2, self.sigma_v1**2])
        r_meas = self.sigma_v**2
        p_cap = 1.0
        rows = []

        for cycle_index, cycle in cycles:
            t = cycle["time_s"].to_numpy(dtype=float)
            i = cycle["current_a"].to_numpy(dtype=float)
            v = cycle["voltage_v"].to_numpy(dtype=float)
            if len(t) < 3:
                continue
            kind = cycle["step_type"].to_numpy()
            if abs(i[0]) < 1e-9:  # a cycle that opens at rest anchors the state of charge
                x[0] = float(ocv.soc_of(v[0]))
            soc = np.empty(len(t))
            v_rc = np.empty(len(t))
            soc[0], v_rc[0] = x[0], x[1]
            for k in range(1, len(t)):
                dt = t[k] - t[k - 1]
                if dt <= 0:
                    soc[k], v_rc[k] = x[0], x[1]
                    continue
                x, p = self._predict_step(x, p, i[k - 1], dt, params, q_proc)
                x, p = self._update_step(x, p, i[k], v[k], ocv, params, r_meas)
                soc[k], v_rc[k] = x[0], x[1]

            # Capacity is read off the discharge alone. Taking it over the whole cycle would
            # fold coulombic inefficiency and the constant-voltage tail into the estimate.
            span, delivered = self._discharge_excursion(t, i, soc, kind)
            raw = delivered / span if span >= self.min_soc_span else np.nan
            if np.isfinite(raw):
                params.capacity_ah, p_cap = self._capacity_update(params.capacity_ah, p_cap, raw)
            if self.adapt_r0:
                r0 = self._residual_r0(v, i, ocv.v(soc), v_rc)
                if np.isfinite(r0) and r0 > 0:
                    params.r0_ohm = 0.8 * params.r0_ohm + 0.2 * float(r0)
            rows.append(
                {
                    "cycle_index": int(cycle_index),
                    "capacity_ah": float(params.capacity_ah),
                    "capacity_raw_ah": float(raw),
                    "r0_ohm": float(params.r0_ohm),
                    "soc_span": float(span),
                }
            )
        return pd.DataFrame(rows)

    def _initial_capacity(self, first_cycle: pd.DataFrame) -> float:
        """Seed the capacity with the charge the first discharge delivered."""
        discharge = first_cycle[first_cycle["step_type"] == StepType.DISCHARGE.value]
        if len(discharge):
            q = discharge["capacity_ah"].to_numpy(dtype=float)
            if np.isfinite(q).any() and np.nanmax(q) > 0:
                return float(np.nanmax(q))
        return 1.0

    @staticmethod
    def _residual_r0(voltage, current, ocv_v, v_rc, min_current: float = 0.05) -> float:
        """Series resistance by least squares on the model residual.

        Whatever the circuit model cannot explain from the open-circuit voltage and the RC
        branch is attributed to the ohmic drop, so ``r0`` follows the resistance rise of an
        ageing cell instead of being read once from a voltage jump.
        """
        residual = np.asarray(voltage, dtype=float) - np.asarray(ocv_v, dtype=float)
        residual = residual - np.asarray(v_rc, dtype=float)
        i = np.asarray(current, dtype=float)
        use = np.abs(i) >= min_current
        if use.sum() < 3:
            return float("nan")
        denominator = float(np.sum(i[use] ** 2))
        if denominator <= 0:
            return float("nan")
        return float(np.sum(residual[use] * i[use]) / denominator)

    @staticmethod
    def _discharge_excursion(t, i, soc, kind) -> tuple[float, float]:
        """State-of-charge drop and charge delivered by the cycle's deepest discharge step."""
        mask = (kind == StepType.DISCHARGE.value) | (i < 0)
        if not mask.any():
            return 0.0, 0.0
        starts = [0] if mask[0] else []
        starts += [k for k in range(1, len(mask)) if mask[k] and not mask[k - 1]]
        best = (0.0, 0.0)
        for start in starts:
            end = start
            while end + 1 < len(mask) and mask[end + 1]:
                end += 1
            if end - start < 2:
                continue
            segment = slice(start, end + 1)
            delivered = float(
                -np.sum(0.5 * (i[segment][1:] + i[segment][:-1]) * np.diff(t[segment])) / 3600.0
            )
            drop = float(soc[start] - soc[end])
            if drop > best[0] and delivered > 0:
                best = (drop, delivered)
        return best

    @staticmethod
    def _predict_step(x, p, current, dt, params: ECMParameters, q_proc):
        tau = params.tau_s
        soc = x[0] + current * dt / (3600.0 * params.capacity_ah)
        if tau > 0:
            decay = np.exp(-dt / tau)
            v1 = x[1] * decay + params.r1_ohm * (1.0 - decay) * current
        else:
            decay, v1 = 0.0, 0.0
        f = np.array([[1.0, 0.0], [0.0, decay]])
        return np.array([soc, v1]), f @ p @ f.T + q_proc

    @staticmethod
    def _update_step(x, p, current, voltage, ocv: OCVCurve, params: ECMParameters, r_meas):
        predicted = float(ocv.v(x[0])) + x[1] + current * params.r0_ohm
        h = np.array([float(ocv.dv_dsoc(x[0])), 1.0])
        s = float(h @ p @ h.T) + r_meas
        gain = (p @ h) / s
        x = x + gain * (voltage - predicted)
        # State of charge is a fraction of the present capacity, so it cannot leave [0, 1].
        # Enforcing that matters: near a full rest the measured voltage can sit above the top of
        # a pseudo-OCV curve built from a finite-rate step, and an unconstrained filter would
        # keep pushing the state upwards, inflating the excursion the capacity estimate divides by.
        x[0] = float(np.clip(x[0], 0.0, 1.0))
        p = (np.eye(2) - np.outer(gain, h)) @ p
        return x, p

    def _capacity_update(self, capacity, p_cap, measured):
        """One recursive least-squares step towards a newly measured capacity.

        The forgetting factor sets how long the estimate remembers. Keep it small when a test
        alternates between protocols, because a slow filter averages their different deliverable
        capacities together and biases every reading.
        """
        gain = p_cap / (self.forgetting + p_cap)
        capacity = capacity + gain * (measured - capacity)
        p_cap = (p_cap - gain * p_cap) / self.forgetting
        return float(max(capacity, 1e-6)), float(min(p_cap, 1e6))

    # -- interface --------------------------------------------------------------------------
    def _predict(self, data: ModelData) -> pd.DataFrame:
        bundle = data.bundle
        if bundle is None or bundle.timeseries is None:
            raise ValueError(f"{self.name}: needs a bundle with a timeseries table")
        targets = data.targets.reset_index(drop=True)
        values = np.full(len(targets), np.nan)
        ts = bundle.timeseries
        for cell_id, rows in targets.groupby("cell_id", sort=False):
            cell_ts = ts[ts["cell_id"].astype(str) == str(cell_id)]
            if not len(cell_ts):
                continue
            trace = self.run_cell(cell_ts)
            self.traces[str(cell_id)] = trace
            if not len(trace):
                continue
            reference = float(trace["capacity_ah"].iloc[0])
            soh = trace["capacity_ah"].to_numpy() / reference
            values[rows.index.to_numpy()] = np.interp(
                rows["cycle_index"].to_numpy(dtype=float),
                trace["cycle_index"].to_numpy(dtype=float),
                soh,
            )
        fallback = float(np.nanmean(values)) if np.isfinite(values).any() else 1.0
        values = np.where(np.isfinite(values), values, fallback)
        return self._frame(targets, np.clip(values, self.clip_low, self.clip_high))

    def get_params(self) -> dict:
        return {
            "r0_ohm": self.r0_ohm,
            "r1_ohm": self.r1_ohm,
            "c1_farad": self.c1_farad,
            "sigma_v": self.sigma_v,
            "forgetting": self.forgetting,
            "min_soc_span": self.min_soc_span,
            "adapt_r0": self.adapt_r0,
        }
