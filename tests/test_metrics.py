import math

import numpy as np
import pytest

from battery_worldcup.metrics import mae, mape, max_abs_error, point_metrics, r2, rmse


def test_basic_values():
    y = np.array([100.0, 90.0, 80.0])
    p = np.array([101.0, 88.0, 80.0])
    assert mae(y, p) == pytest.approx(1.0)
    assert rmse(y, p) == pytest.approx(math.sqrt(5 / 3))
    assert max_abs_error(y, p) == pytest.approx(2.0)
    assert mape(y, p) == pytest.approx(100 * (0.01 + 2 / 90) / 3)
    assert r2(y, y) == pytest.approx(1.0)


def test_nan_pairs_are_dropped_and_shapes_checked():
    assert mae([1.0, np.nan, 3.0], [1.5, 2.0, np.nan]) == pytest.approx(0.5)
    with pytest.raises(ValueError):
        mae([1.0, 2.0], [1.0])
    with pytest.raises(ValueError):
        mae([np.nan], [1.0])


def test_point_metrics_keys():
    out = point_metrics([1.0, 2.0, 3.0], [1.1, 1.9, 3.2])
    assert set(out) == {"mae", "rmse", "mape", "max_abs_error", "r2", "n"}
    assert out["n"] == 3
