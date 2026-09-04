import json

import pandas as pd

from battery_worldcup.cli import main
from battery_worldcup.data import registry
from battery_worldcup.data.loaders import LOADERS
from battery_worldcup.labels import RULES


def test_registry_is_consistent():
    keys = [e.key for e in registry.list_entries()]
    assert len(keys) == len(set(keys))
    for e in registry.list_entries():
        assert e.url and e.citation and e.chemistry
        if e.loader:
            assert e.loader in LOADERS
    for key in RULES:
        assert key in registry.REGISTRY
    assert registry.get("oxford").wave == 1


def test_cli_synth_and_labels(tmp_path, capsys):
    out = tmp_path / "syn"
    assert (
        main(
            [
                "synth",
                "--out",
                str(out),
                "--cells",
                "3",
                "--cycles",
                "20",
                "--rpt-every",
                "5",
                "--no-timeseries",
            ]
        )
        == 0
    )
    assert (out / "bundle.json").exists()
    summary = json.loads((out / "bundle.json").read_text())["summary"]
    assert summary["n_cells"] == 3
    labels_file = tmp_path / "labels.parquet"
    assert main(["labels", str(out), "--out", str(labels_file)]) == 0
    captured = capsys.readouterr().out
    assert "SYN000" in captured
    labels = pd.read_parquet(labels_file)
    assert labels["is_label"].sum() == 3 * 4


def test_cli_data_list_and_info(capsys):
    assert main(["data", "list", "--wave", "1"]) == 0
    out = capsys.readouterr().out
    assert "oxford" in out and "matr" in out and "hust" not in out
    assert main(["data", "info", "oxford"]) == 0
    assert "ora.ox.ac.uk" in capsys.readouterr().out


def test_cli_download_without_registered_files(capsys):
    assert main(["data", "download", "oxford"]) == 2
    assert "manually" in capsys.readouterr().err
