"""The result file format.

A result is a JSON file produced by the runner from a configuration, a seed and a git commit.
Nothing else may enter ``results/``: a number that cannot be regenerated is not a result. The
leaderboard is built entirely from these files.
"""

from __future__ import annotations

import json
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = "bwc-result/1"


def git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def environment() -> dict[str, Any]:
    versions: dict[str, str] = {"python": platform.python_version()}
    for module in ("numpy", "pandas", "scipy", "sklearn", "pyarrow"):
        try:
            versions[module] = __import__(module).__version__
        except Exception:  # noqa: BLE001 - a missing optional dependency is not an error here
            continue
    return {"versions": versions, "platform": platform.platform()}


def make_result(
    config: dict[str, Any],
    config_hash: str,
    model_info: dict[str, Any],
    folds: list[dict[str, Any]],
    metrics: dict[str, Any],
    cost: dict[str, float],
    dataset: str,
    task: str,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "name": config.get("name"),
        "dataset": dataset,
        "task": task,
        "model": model_info,
        "metrics": metrics,
        "folds": folds,
        "cost": cost,
        "config": config,
        "config_hash": config_hash,
        "git_commit": git_commit(),
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "environment": environment(),
    }


def result_path(root: str | Path, dataset: str, task: str, name: str) -> Path:
    safe_task = task.replace("@", "-at-")
    return Path(root) / dataset / safe_task / f"{name}.json"


def write_result(result: dict[str, Any], root: str | Path) -> Path:
    path = result_path(root, result["dataset"], result["task"], result["name"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, default=str))
    return path


def load_results(root: str | Path) -> list[dict[str, Any]]:
    """Every result file under ``root``, skipping anything that is not this schema."""
    out = []
    for path in sorted(Path(root).rglob("*.json")):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("schema") == SCHEMA:
            data["_path"] = str(path)
            out.append(data)
    return out
