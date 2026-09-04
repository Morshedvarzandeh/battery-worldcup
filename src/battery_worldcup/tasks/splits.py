"""Cell-level splits (see ``docs/decisions/0002-split-policy.md``).

A cell belongs to exactly one of train, validation and test. Folds are balanced across an
optional grouping column (protocol, batch, temperature) and along an optional ordering column
(cycle life) so that every fold spans the range of conditions and lifetimes.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd


class LeakageError(ValueError):
    """Raised when a split would put one cell into more than one partition."""


@dataclass
class Split:
    dataset: str
    version: str
    fold: int
    n_folds: int
    train: list[str]
    val: list[str]
    test: list[str]
    seed: int = 0
    notes: str = ""
    extra: dict = field(default_factory=dict)

    def check(self) -> Split:
        parts = {"train": set(self.train), "val": set(self.val), "test": set(self.test)}
        for name, cells in parts.items():
            if not cells:
                raise LeakageError(f"{self.dataset} fold {self.fold}: {name} partition is empty")
        names = list(parts)
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                overlap = parts[a] & parts[b]
                if overlap:
                    raise LeakageError(
                        f"{self.dataset} fold {self.fold}: cells in both {a} and {b}: "
                        f"{sorted(overlap)[:5]}"
                    )
        return self

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Split:
        return cls(**data)


def assert_no_leakage(splits: Split | Iterable[Split]) -> None:
    items = [splits] if isinstance(splits, Split) else list(splits)
    for s in items:
        s.check()


def _fold_assignment(
    cells: pd.DataFrame,
    n_folds: int,
    group_col: str | None,
    order_col: str | None,
    rng: np.random.Generator,
) -> pd.Series:
    """Assign a fold id to every cell, balanced per group and along the ordering column."""
    df = cells[["cell_id"]].copy()
    df["_group"] = cells[group_col].astype(str).to_numpy() if group_col else "all"
    df["_order"] = cells[order_col].to_numpy(dtype=float) if order_col else 0.0
    df["_rand"] = rng.random(len(df))
    df = df.sort_values(["_group", "_order", "_rand"], kind="stable")
    fold = np.empty(len(df), dtype=np.int64)
    positions = {cid: i for i, cid in enumerate(df["cell_id"])}
    for _, g in df.groupby("_group", sort=False):
        offset = int(rng.integers(n_folds))
        for j, cid in enumerate(g["cell_id"]):
            fold[positions[cid]] = (j + offset) % n_folds
    return pd.Series(fold, index=df["cell_id"].to_numpy())


def make_cell_folds(
    cells: pd.DataFrame,
    n_folds: int = 5,
    group_col: str | None = None,
    order_col: str | None = None,
    seed: int = 0,
    version: str = "v1",
    dataset: str | None = None,
) -> list[Split]:
    """K-fold cell-level splits. Fold ``i`` tests on fold ``i`` and validates on fold ``i+1``.

    ``cells`` needs a ``cell_id`` column; ``group_col`` and ``order_col`` are optional columns
    used for stratification (for example protocol and cycle life).
    """
    if n_folds < 3:
        raise ValueError("n_folds must be at least 3 so that train, val and test are disjoint")
    if cells["cell_id"].duplicated().any():
        raise ValueError("cells contains duplicate cell_id values")
    if len(cells) < n_folds:
        raise ValueError(f"{len(cells)} cells cannot be split into {n_folds} folds")
    dataset = dataset or str(cells["dataset"].iloc[0]) if "dataset" in cells else "unknown"
    rng = np.random.default_rng(seed)
    fold_of = _fold_assignment(cells, n_folds, group_col, order_col, rng)
    splits = []
    for i in range(n_folds):
        test = sorted(str(c) for c in fold_of.index[fold_of == i])
        val = sorted(str(c) for c in fold_of.index[fold_of == (i + 1) % n_folds])
        train = sorted(str(c) for c in fold_of.index[~fold_of.isin([i, (i + 1) % n_folds])])
        splits.append(
            Split(
                dataset=dataset,
                version=version,
                fold=i,
                n_folds=n_folds,
                train=train,
                val=val,
                test=test,
                seed=seed,
                notes=f"k-fold, group_col={group_col}, order_col={order_col}",
            ).check()
        )
    return splits


def leave_one_group_out(
    cells: pd.DataFrame,
    group_col: str,
    val_fraction: float = 0.2,
    seed: int = 0,
    version: str = "v1",
    dataset: str | None = None,
) -> list[Split]:
    """One split per group: the group is the test set, the rest is split into train and val."""
    if cells["cell_id"].duplicated().any():
        raise ValueError("cells contains duplicate cell_id values")
    dataset = dataset or str(cells["dataset"].iloc[0]) if "dataset" in cells else "unknown"
    rng = np.random.default_rng(seed)
    groups = sorted(cells[group_col].astype(str).unique())
    splits = []
    for i, g in enumerate(groups):
        in_group = cells[group_col].astype(str) == g
        test = sorted(str(c) for c in cells.loc[in_group, "cell_id"])
        rest = np.array(sorted(str(c) for c in cells.loc[~in_group, "cell_id"]))
        rng.shuffle(rest)
        n_val = max(1, int(round(val_fraction * len(rest)))) if len(rest) > 1 else 0
        val = sorted(rest[:n_val].tolist())
        train = sorted(rest[n_val:].tolist())
        splits.append(
            Split(
                dataset=dataset,
                version=version,
                fold=i,
                n_folds=len(groups),
                train=train,
                val=val,
                test=test,
                seed=seed,
                notes=f"leave-one-group-out on {group_col}",
                extra={"held_out_group": g},
            ).check()
        )
    return splits


def save_splits(splits: Iterable[Split], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([s.to_dict() for s in splits], indent=2))
    return path


def load_splits(path: str | Path) -> list[Split]:
    data = json.loads(Path(path).read_text())
    splits = [Split.from_dict(d) for d in data]
    assert_no_leakage(splits)
    return splits
