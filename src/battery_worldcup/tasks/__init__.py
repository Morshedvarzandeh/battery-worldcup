"""Task definitions, split generation and model-input assembly."""

from battery_worldcup.tasks.assemble import (
    build_model_data,
    forecast_views,
    nowcast_views,
    truth_of,
)
from battery_worldcup.tasks.splits import (
    LeakageError,
    Split,
    assert_no_leakage,
    leave_one_group_out,
    load_splits,
    make_cell_folds,
    save_splits,
)

__all__ = [
    "LeakageError",
    "Split",
    "assert_no_leakage",
    "build_model_data",
    "forecast_views",
    "leave_one_group_out",
    "load_splits",
    "make_cell_folds",
    "nowcast_views",
    "save_splits",
    "truth_of",
]
