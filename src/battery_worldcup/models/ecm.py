"""Equivalent-circuit cell model and open-circuit-voltage curves (support for family S2).

The circuit is a Thevenin model: an open-circuit voltage source that depends on state of
charge, a series resistance ``r0``, and one RC pair ``(r1, c1)`` for the diffusion transient.
With the repository's sign convention (current positive when charging) the terminal voltage is

    v(t) = ocv(soc(t)) + v1(t) + i(t) * r0

where ``soc`` integrates current over the cell capacity and ``v1`` relaxes with the time
constant ``r1 * c1``. Setting ``r1`` to zero reduces the model to a purely ohmic cell.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

MIN_SOC_SPAN = 0.2


@dataclass(frozen=True)
class OCVCurve:
    """A monotonic open-circuit voltage curve, stored on a state-of-charge grid."""

    soc: np.ndarray
    voltage: np.ndarray

    def __post_init__(self) -> None:
        soc = np.asarray(self.soc, dtype=float)
        voltage = np.asarray(self.voltage, dtype=float)
        if len(soc) < 2 or len(soc) != len(voltage):
            raise ValueError("an OCV curve needs at least two matching points")
        order = np.argsort(soc)
        soc, voltage = soc[order], voltage[order]
        if np.any(np.diff(voltage) <= 0):
            voltage = np.maximum.accumulate(voltage + 1e-9 * np.arange(len(voltage)))
        object.__setattr__(self, "soc", soc)
        object.__setattr__(self, "voltage", voltage)

    def v(self, soc) -> np.ndarray:
        """Open-circuit voltage at a state of charge (clamped outside the grid)."""
        return np.interp(np.asarray(soc, dtype=float), self.soc, self.voltage)

    def soc_of(self, voltage) -> np.ndarray:
        """Inverse of :meth:`v`."""
        return np.interp(np.asarray(voltage, dtype=float), self.voltage, self.soc)

    def dv_dsoc(self, soc) -> np.ndarray:
        """Slope of the curve, the measurement Jacobian of the Kalman filter."""
        grad = np.gradient(self.voltage, self.soc)
        return np.interp(np.asarray(soc, dtype=float), self.soc, grad)

    @classmethod
    def from_curve(cls, capacity_ah, voltage_v, points: int = 201) -> OCVCurve:
        """Build a curve from one low-rate step, normalising capacity to a 0..1 grid."""
        q = np.asarray(capacity_ah, dtype=float).ravel()
        v = np.asarray(voltage_v, dtype=float).ravel()
        keep = np.isfinite(q) & np.isfinite(v)
        q, v = q[keep], v[keep]
        if len(q) < 3 or np.ptp(q) <= 0:
            raise ValueError("need a capacity range to build an OCV curve")
        if v[-1] < v[0]:  # a discharge: reverse so both axes increase
            q, v = q[::-1], v[::-1]
            q = q[0] - q
        soc = (q - q.min()) / np.ptp(q)
        merged = pd.DataFrame({"soc": soc, "v": v}).groupby("soc", sort=True)["v"].mean()
        grid = np.linspace(0.0, 1.0, points)
        return cls(grid, np.interp(grid, merged.index.to_numpy(), merged.to_numpy()))

    @classmethod
    def from_charge_and_discharge(
        cls, charge: tuple, discharge: tuple, points: int = 201
    ) -> OCVCurve:
        """Average a low-rate charge and discharge curve, cancelling the ohmic offset.

        Each argument is ``(capacity_ah, voltage_v)``. This is the pseudo-OCV construction used
        with C/25 characterisation data.
        """
        up = cls.from_curve(*charge, points=points)
        down = cls.from_curve(*discharge, points=points)
        return cls(up.soc, 0.5 * (up.voltage + down.v(up.soc)))


@dataclass
class ECMParameters:
    """Circuit parameters. ``capacity_ah`` is the usable capacity the filter tracks."""

    capacity_ah: float = 1.0
    r0_ohm: float = 0.05
    r1_ohm: float = 0.0
    c1_farad: float = 2000.0

    @property
    def tau_s(self) -> float:
        return float(self.r1_ohm * self.c1_farad)


def simulate(
    ocv: OCVCurve,
    params: ECMParameters,
    time_s,
    current_a,
    soc0: float = 0.0,
    v1_0: float = 0.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Run the circuit forward. Returns terminal voltage, state of charge and the RC voltage."""
    t = np.asarray(time_s, dtype=float).ravel()
    i = np.asarray(current_a, dtype=float).ravel()
    if len(t) != len(i):
        raise ValueError("time and current must have the same length")
    n = len(t)
    soc = np.empty(n)
    v1 = np.empty(n)
    soc[0], v1[0] = soc0, v1_0
    tau = params.tau_s
    for k in range(1, n):
        dt = t[k] - t[k - 1]
        soc[k] = soc[k - 1] + i[k - 1] * dt / (3600.0 * params.capacity_ah)
        if tau > 0:
            decay = np.exp(-dt / tau)
            v1[k] = v1[k - 1] * decay + params.r1_ohm * (1.0 - decay) * i[k - 1]
        else:
            v1[k] = 0.0
    return ocv.v(soc) + v1 + i * params.r0_ohm, soc, v1


def estimate_r0(voltage_v, current_a, min_step_a: float = 0.05) -> float:
    """Series resistance from the voltage jump at current steps.

    Returns NaN when the data contain no current step large enough to be informative.
    """
    v = np.asarray(voltage_v, dtype=float).ravel()
    i = np.asarray(current_a, dtype=float).ravel()
    di = np.diff(i)
    dv = np.diff(v)
    big = np.abs(di) >= min_step_a
    if not big.any():
        return float("nan")
    return float(np.median(dv[big] / di[big]))
