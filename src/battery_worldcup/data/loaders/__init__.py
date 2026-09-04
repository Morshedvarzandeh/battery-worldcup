"""Dataset loaders: source files in, :class:`DatasetBundle` out."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from battery_worldcup.data.loaders.oxford import load_oxford
from battery_worldcup.data.schema import DatasetBundle
from battery_worldcup.data.synthetic import make_synthetic

Loader = Callable[[Path], DatasetBundle]


def _load_synthetic(_: Path) -> DatasetBundle:
    bundle, _truth = make_synthetic()
    return bundle


LOADERS: dict[str, Loader] = {
    "synthetic": _load_synthetic,
    "oxford": load_oxford,
}


def load_dataset(key: str, path: str | Path) -> DatasetBundle:
    """Run the loader registered for ``key`` on ``path`` and validate the result."""
    try:
        loader = LOADERS[key]
    except KeyError as exc:
        raise KeyError(f"no loader for {key!r}; available: {sorted(LOADERS)}") from exc
    return loader(Path(path)).validate()


__all__ = ["LOADERS", "Loader", "load_dataset", "load_oxford"]
