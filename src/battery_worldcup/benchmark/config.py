"""Experiment configuration.

One YAML file describes one experiment: which dataset, which task, how it is split, which
model, and which seeds. Everything the runner needs is in the file, so a result can always be
traced back to the configuration that produced it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

TASK_TYPES = ("nowcast", "forecast")


@dataclass
class DatasetConfig:
    """Where the data come from: a generated synthetic population, or a Parquet bundle."""

    key: str = "synthetic"
    bundle: str | None = None
    synthetic: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskConfig:
    type: str = "nowcast"
    origin: int | None = None
    include_interpolated: bool = False
    eol_threshold: float = 0.8

    def __post_init__(self) -> None:
        if self.type not in TASK_TYPES:
            raise ValueError(f"unknown task type {self.type!r}; known: {list(TASK_TYPES)}")
        if self.type == "forecast" and self.origin is None:
            raise ValueError("a forecast task needs an origin")

    @property
    def key(self) -> str:
        return self.type if self.origin is None else f"{self.type}@{self.origin}"


@dataclass
class SplitConfig:
    n_folds: int = 4
    seed: int = 0
    group_col: str | None = None
    order_col: str | None = "cycle_life"
    version: str = "v1"


@dataclass
class ModelConfig:
    name: str = "mean_trajectory"
    params: dict[str, Any] = field(default_factory=dict)
    seed_param: str | None = None
    """Name of the model parameter that should receive the run seed, if it has one."""


@dataclass
class ExperimentConfig:
    name: str
    dataset: DatasetConfig = field(default_factory=DatasetConfig)
    task: TaskConfig = field(default_factory=TaskConfig)
    split: SplitConfig = field(default_factory=SplitConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    seeds: list[int] = field(default_factory=lambda: [0])
    features: bool | None = None
    """Extract features? ``None`` decides from the model's declared requirements."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExperimentConfig:
        known = {"name", "dataset", "task", "split", "model", "seeds", "features"}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"unknown configuration keys: {unknown}")
        if "name" not in data:
            raise ValueError("a configuration needs a name")
        return cls(
            name=str(data["name"]),
            dataset=DatasetConfig(**data.get("dataset", {})),
            task=TaskConfig(**data.get("task", {})),
            split=SplitConfig(**data.get("split", {})),
            model=ModelConfig(**data.get("model", {})),
            seeds=[int(s) for s in data.get("seeds", [0])],
            features=data.get("features"),
        )

    @classmethod
    def load(cls, path: str | Path) -> ExperimentConfig:
        return cls.from_dict(yaml.safe_load(Path(path).read_text()) or {})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def hash(self) -> str:
        """Stable hash of the whole configuration, recorded in every result file."""
        payload = json.dumps(self.to_dict(), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]
