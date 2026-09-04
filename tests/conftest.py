import pandas as pd
import pytest

from battery_worldcup.data.schema import DatasetBundle
from battery_worldcup.data.synthetic import SyntheticConfig, make_synthetic
from battery_worldcup.features import extract_cycle_features
from battery_worldcup.labels import build_capacity_labels
from battery_worldcup.tasks import build_model_data, make_cell_folds, nowcast_views, truth_of

SMALL = SyntheticConfig(n_cells=5, n_cycles=60, rpt_every=10, points_per_step=12, seed=1)
POP = SyntheticConfig(n_cells=8, n_cycles=60, rpt_every=10, points_per_step=40, seed=7)


@pytest.fixture(scope="session")
def small() -> tuple[DatasetBundle, pd.DataFrame]:
    bundle, truth = make_synthetic(SMALL)
    return bundle.validate(), truth


@pytest.fixture(scope="session")
def small_bundle(small) -> DatasetBundle:
    return small[0]


@pytest.fixture(scope="session")
def small_truth(small) -> pd.DataFrame:
    return small[1]


@pytest.fixture(scope="session")
def small_labels(small_bundle) -> pd.DataFrame:
    return build_capacity_labels(small_bundle.cycles)


@pytest.fixture(scope="session")
def population() -> DatasetBundle:
    """A slightly larger population with enough resolution for feature-based models."""
    bundle, _ = make_synthetic(POP)
    return bundle.validate()


@pytest.fixture(scope="session")
def model_data(population):
    labels = build_capacity_labels(population.cycles)
    features = extract_cycle_features(population)
    return build_model_data(population, labels, features, include_interpolated=True)


@pytest.fixture(scope="session")
def split(model_data):
    cells = pd.DataFrame({"dataset": "synthetic", "cell_id": model_data.cells})
    return make_cell_folds(cells, n_folds=4, seed=0)[0]


@pytest.fixture(scope="session")
def nowcast_views_fixture(model_data, split):
    train_view, eval_view = nowcast_views(model_data, split)
    return train_view, eval_view, truth_of(eval_view)
