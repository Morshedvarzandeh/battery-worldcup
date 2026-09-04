import pandas as pd
import pytest

from battery_worldcup.data.schema import DatasetBundle
from battery_worldcup.data.synthetic import SyntheticConfig, make_synthetic

SMALL = SyntheticConfig(n_cells=5, n_cycles=60, rpt_every=10, points_per_step=12, seed=1)


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
