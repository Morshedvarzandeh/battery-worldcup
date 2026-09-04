"""Build the leaderboard from result files.

Ranking is by mean rank across the (dataset, task) groups a model appears in, never by a single
pooled number, so that one large dataset cannot decide the standings. Per-group tables are
printed in full next to it, because the per-group numbers are what a reader actually needs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from battery_worldcup.benchmark.result import load_results
from battery_worldcup.models import MODELS

PRIMARY = "mae"
SHOWN = ("mae", "rmse", "max_abs_error", "eol_cycle_mae", "coverage_90", "crps")
FLAG_LABELS = {
    "features": "features",
    "timeseries": "timeseries",
    "history": "history",
    "full_charge": "full charge",
    "rest": "rest",
    "temperature": "temperature",
    "eis": "impedance",
}


def requirement_summary(requirements: dict[str, Any]) -> str:
    """Compact description of what a model needs, for the leaderboard's rightmost column."""
    needs = [label for key, label in FLAG_LABELS.items() if requirements.get(key)]
    if not requirements.get("training_cells", True):
        needs.append("no training cells")
    return ", ".join(needs) if needs else "labels only"


#: Parameters that identify a run rather than a model, and so never appear in a model key.
RUN_PARAMS = frozenset({"random_state", "seed", "n_jobs", "clip"})


def model_key(model: dict[str, Any]) -> str:
    """Identity of a model configuration, used to follow it across datasets.

    The entry name cannot serve this purpose: it usually carries the task it was run for, so the
    same model under two tasks would look like two different competitors. Only parameters that
    differ from the model's defaults are shown, so a key stays readable as models grow options.
    """
    name = str(model.get("name", "unknown"))
    params = dict(model.get("params") or {})
    defaults = {}
    cls = MODELS.get(name)
    if cls is not None:
        defaults = cls.default_params()
    shown = {
        k: v
        for k, v in sorted(params.items())
        if k not in RUN_PARAMS and v is not None and (k not in defaults or defaults[k] != v)
    }
    if not shown:
        return name
    return f"{name}({','.join(f'{k}={v}' for k, v in shown.items())})"


def to_frame(results: list[dict]) -> pd.DataFrame:
    """One row per result file, with the aggregated metrics flattened into columns."""
    rows = []
    for r in results:
        metrics = r.get("metrics", {})
        model = r.get("model") or {}
        row: dict[str, Any] = {
            "dataset": r.get("dataset"),
            "task": r.get("task"),
            "entry": r.get("name"),
            "model": model.get("name"),
            "model_key": model_key(model),
            "family": (r.get("model") or {}).get("family"),
            "requires": requirement_summary((r.get("model") or {}).get("requirements", {})),
            "folds": (r.get("cost") or {}).get("folds"),
            "fit_s": (r.get("cost") or {}).get("fit_seconds_per_fold"),
            "commit": (r.get("git_commit") or "")[:8] or None,
        }
        for key, value in metrics.items():
            if isinstance(value, dict) and "mean" in value:
                row[key] = value["mean"]
                row[f"{key}_std"] = value.get("std")
        rows.append(row)
    return pd.DataFrame(rows)


def _fmt(value, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "-"
    return f"{value:.{digits}f}"


def _markdown_table(rows: list[list[str]], header: list[str]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def group_table(frame: pd.DataFrame, scale: float = 100.0) -> str:
    """One (dataset, task) table, sorted by the primary metric.

    SOH metrics are printed in percentage points, which is how the field reports them.
    """
    available = [m for m in SHOWN if m in frame.columns and frame[m].notna().any()]
    header = ["#", "entry", "model", "family", *available, "requires"]
    frame = frame.sort_values(PRIMARY, na_position="last").reset_index(drop=True)
    rows = []
    for i, row in frame.iterrows():
        cells = [str(i + 1), str(row["entry"]), str(row["model_key"]), str(row["family"])]
        for metric in available:
            value = row.get(metric)
            if metric in ("eol_cycle_mae",):  # already in cycles
                cells.append(_fmt(value, 1))
            elif metric in ("coverage_90",):
                cells.append(_fmt(value, 2))
            else:
                std = row.get(f"{metric}_std")
                text = _fmt(None if value is None else value * scale)
                if std is not None and np.isfinite(std):
                    text += f" ± {std * scale:.3f}"
                cells.append(text)
        cells.append(str(row["requires"]))
        rows.append(cells)
    return _markdown_table(rows, header)


def overall_table(frame: pd.DataFrame) -> str:
    """Mean rank of each model across the datasets it was run on, within each task.

    Ranks are never pooled across tasks: a nowcast error and a forecast error are different
    quantities, and averaging their ranks would rank a model against a question it never
    answered. With a single dataset this reduces to the per-group ordering, and it starts to
    carry information as soon as a second dataset lands.
    """
    frame = frame.copy()
    frame["rank"] = frame.groupby(["dataset", "task"])[PRIMARY].rank(method="min")
    summary = (
        frame.groupby(["task", "model_key"])
        .agg(
            family=("family", "first"),
            datasets=("rank", "size"),
            mean_rank=("rank", "mean"),
            best_mae=(PRIMARY, "min"),
        )
        .reset_index()
        .sort_values(["task", "mean_rank", "best_mae"])
    )
    rows = []
    for task, group in summary.groupby("task", sort=True):
        for i, (_, r) in enumerate(group.iterrows()):
            rows.append(
                [
                    str(i + 1),
                    str(task),
                    str(r["model_key"]),
                    str(r["family"]),
                    str(int(r["datasets"])),
                    _fmt(r["mean_rank"], 2),
                    _fmt(r["best_mae"] * 100.0),
                ]
            )
    header = ["#", "task", "model", "family", "datasets", "mean rank", "best MAE"]
    return _markdown_table(rows, header)


def build_leaderboard(results_dir: str | Path) -> str:
    """Render every result under ``results_dir`` as one Markdown document."""
    results = load_results(results_dir)
    if not results:
        return "# Leaderboard\n\nNo results yet. Run `bwc run <config>` to produce some.\n"
    frame = to_frame(results)
    parts = [
        "# Leaderboard",
        "",
        "Generated by `bwc leaderboard`. Every row comes from a result file under `results/`,",
        "produced by a configuration, a seed and a git commit. Errors are in SOH percentage",
        "points, lower is better; `eol_cycle_mae` is in cycles. `requires` says what the model",
        "needs, so a row can be judged against what a deployment can actually provide.",
        "",
        f"Entries: {len(frame)} across {frame.groupby(['dataset', 'task']).ngroups} groups.",
        "",
        "## Overall (mean rank across datasets, per task)",
        "",
        overall_table(frame),
        "",
    ]
    for (dataset, task), group in frame.groupby(["dataset", "task"]):
        parts += [f"## {dataset} — {task}", "", group_table(group), ""]
    parts += [
        "## Reading these numbers",
        "",
        "`coverage_90` is the share of truths that fell inside the model's own 90 per cent",
        "interval, so it should read close to 0.90. A lower number means the model is",
        "overconfident, and its `crps` and interval width should be read in that light. A model",
        "with no `coverage_90` did not report an uncertainty at all.",
        "",
        "Results on the synthetic dataset test the plumbing, not the science: the generator",
        "produces smooth cells from a known circuit and a known open-circuit voltage curve, which",
        "flatters every model and flatters the equivalent-circuit filter most of all, because the",
        "data are generated by the model it assumes. Real rankings wait for the wave-1 datasets.",
        "",
    ]
    return "\n".join(parts)
