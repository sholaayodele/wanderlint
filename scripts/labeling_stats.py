"""Labeling stats: seed rejection, human-added labels and the blind human baseline.

Usage: uv run python scripts/labeling_stats.py [--json out.json]
Reads data/labels/labeling_log.jsonl (latest record per listing). No API calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
import yaml

from wanderlint.labeling import DEFECT_IDS, compute_stats, latest_records

ROOT = Path(__file__).resolve().parents[1]


def _pct(x: float | None) -> str:
    return "  –  " if x is None else f"{x:5.1%}"


def _num(x: float | None) -> str:
    return "–" if x is None else f"{x}"


def main(
    json_out: Annotated[Path | None, typer.Option("--json", help="Write stats as JSON.")] = None,
) -> None:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    total = sum(1 for line in (ROOT / cfg["datagen"]["output_listings"]).open() if line.strip())
    st = compute_stats(latest_records(ROOT / cfg["paths"]["labels_dir"]).values())
    typer.echo(f"Listings labeled: {st['listings_labeled']}/{total}")
    typer.echo(
        f"Seeds reviewed: {st['seeds']} · rejected {st['rejected']} (of which retyped "
        f"{st['retyped']}) · rejection rate {_pct(st['rejection_rate']).strip()}"
    )
    typer.echo(f"Human-added labels: {st['human_added']} · unsure: {st['unsure']}")
    typer.echo(
        f"Blind human baseline: recall {_pct(st['blind_recall']).strip()} "
        f"(vs confirmed seeds) · precision {_pct(st['blind_precision']).strip()} "
        "(blind labels kept in gold)"
    )
    typer.echo(
        f"Median seconds per listing: {_num(st['median_seconds_per_listing'])} "
        f"(blind {_num(st['median_blind_seconds'])}, reconcile {_num(st['median_reconcile_seconds'])}); "
        f"excludes {st['time_capped_records']} time-capped record(s)"
    )
    typer.echo("")
    typer.echo(
        "type  seeds  rej  retyped  rej.rate  added  conf  blind.rec  blind  dropped  blind.prec"
    )
    for d in DEFECT_IDS:
        t = st["per_type"][d]
        typer.echo(
            f"{d:4}  {t['seeds']:5}  {t['rejected']:3}  {t['retyped']:7}  "
            f"{_pct(t['rejection_rate']):>8}  {t['human_added']:5}  {t['confirmed_seeds']:4}  "
            f"{_pct(t['blind_recall']):>9}  {t['blind_labels']:5}  "
            f"{t['blind_dropped_after_reveal']:7}  {_pct(t['blind_precision']):>10}"
        )
    if json_out:
        json_out.write_text(json.dumps(st, indent=2), encoding="utf-8")


if __name__ == "__main__":
    typer.run(main)
