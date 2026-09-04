"""Command-line entry point ``bwc``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from battery_worldcup import __version__
from battery_worldcup.data import registry
from battery_worldcup.data.cache import cache_dir, download
from battery_worldcup.data.loaders import LOADERS, load_dataset
from battery_worldcup.data.schema import DatasetBundle
from battery_worldcup.data.synthetic import SyntheticConfig, make_synthetic
from battery_worldcup.labels import build_capacity_labels, cycle_life, rules_for


def _print_table(rows: list[list[str]], header: list[str]) -> None:
    widths = [max(len(str(r[i])) for r in [header, *rows]) for i in range(len(header))]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*header))
    print(fmt.format(*["-" * w for w in widths]))
    for r in rows:
        print(fmt.format(*[str(x) for x in r]))


def cmd_data_list(args: argparse.Namespace) -> int:
    rows = []
    for e in registry.list_entries(wave=args.wave):
        rows.append([e.key, e.wave, e.chemistry, e.n_cells, e.loader or "-", e.licence])
    _print_table(rows, ["key", "wave", "chemistry", "cells", "loader", "licence"])
    return 0


def cmd_data_info(args: argparse.Namespace) -> int:
    e = registry.get(args.key)
    for k, v in e.__dict__.items():
        print(f"{k:>17}: {v}")
    return 0


def cmd_data_download(args: argparse.Namespace) -> int:
    e = registry.get(args.key)
    if not e.files:
        print(
            f"{e.key}: no direct download registered. Fetch it manually from {e.url} "
            f"and convert it with: bwc data convert {e.key} --src <path>",
            file=sys.stderr,
        )
        return 2
    dest_dir = Path(args.dest) if args.dest else cache_dir() / "raw" / e.key
    for f in e.files:
        out = download(f.url, dest_dir / f.filename, sha256=f.sha256, force=args.force)
        print(out)
    return 0


def cmd_data_convert(args: argparse.Namespace) -> int:
    bundle = load_dataset(args.key, args.src)
    out = Path(args.out) if args.out else cache_dir() / "bundles" / args.key
    bundle.to_parquet(out)
    print(json.dumps(bundle.summary(), indent=2))
    print(f"written to {out}")
    return 0


def cmd_synth(args: argparse.Namespace) -> int:
    cfg = SyntheticConfig(
        n_cells=args.cells,
        n_cycles=args.cycles,
        rpt_every=args.rpt_every,
        seed=args.seed,
        with_timeseries=not args.no_timeseries,
    )
    bundle, truth = make_synthetic(cfg)
    bundle.validate()
    out = Path(args.out)
    bundle.to_parquet(out)
    truth.to_parquet(out / "truth.parquet", index=False)
    print(json.dumps(bundle.summary(), indent=2))
    print(f"written to {out}")
    return 0


def cmd_labels(args: argparse.Namespace) -> int:
    bundle = DatasetBundle.from_parquet(args.bundle).validate()
    rules = rules_for(args.rules or bundle.dataset)
    labels = build_capacity_labels(bundle.cycles, rules)
    life = cycle_life(labels, threshold=rules.eol_threshold)
    rows = []
    for cell_id, g in labels.groupby("cell_id", sort=False):
        lab = g[g["is_label"]]
        row_life = life[life["cell_id"] == cell_id].iloc[0]
        rows.append(
            [
                cell_id,
                len(lab),
                f"{lab['q_ref_ah'].iloc[0]:.4f}",
                f"{lab['soh_capacity'].min():.4f}",
                "censored" if row_life["censored"] else int(row_life["cycle_life"]),
            ]
        )
    _print_table(rows, ["cell", "labels", "q_ref_ah", "min_soh", f"life@{rules.eol_threshold:g}"])
    if args.out:
        labels.to_parquet(args.out, index=False)
        print(f"labels written to {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bwc", description="battery-worldcup command line")
    p.add_argument("--version", action="version", version=f"bwc {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    data = sub.add_parser("data", help="dataset registry, downloads and conversion")
    dsub = data.add_subparsers(dest="data_command", required=True)
    d_list = dsub.add_parser("list", help="list registered datasets")
    d_list.add_argument("--wave", type=int, default=None)
    d_list.set_defaults(func=cmd_data_list)
    d_info = dsub.add_parser("info", help="show one registry entry")
    d_info.add_argument("key")
    d_info.set_defaults(func=cmd_data_info)
    d_dl = dsub.add_parser("download", help="download registered files into the cache")
    d_dl.add_argument("key")
    d_dl.add_argument("--dest", default=None)
    d_dl.add_argument("--force", action="store_true")
    d_dl.set_defaults(func=cmd_data_download)
    d_conv = dsub.add_parser("convert", help="run a loader and write a Parquet bundle")
    d_conv.add_argument("key", choices=sorted(LOADERS))
    d_conv.add_argument("--src", required=True, help="path to the raw source file or folder")
    d_conv.add_argument("--out", default=None)
    d_conv.set_defaults(func=cmd_data_convert)

    synth = sub.add_parser("synth", help="generate a synthetic bundle")
    synth.add_argument("--out", required=True)
    synth.add_argument("--cells", type=int, default=6)
    synth.add_argument("--cycles", type=int, default=300)
    synth.add_argument("--rpt-every", type=int, default=50)
    synth.add_argument("--seed", type=int, default=0)
    synth.add_argument("--no-timeseries", action="store_true")
    synth.set_defaults(func=cmd_synth)

    labels = sub.add_parser("labels", help="build SOH labels for a Parquet bundle")
    labels.add_argument("bundle", help="bundle directory")
    labels.add_argument("--rules", default=None, help="label-rule key (default: the dataset key)")
    labels.add_argument("--out", default=None, help="write labels to this Parquet file")
    labels.set_defaults(func=cmd_labels)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
