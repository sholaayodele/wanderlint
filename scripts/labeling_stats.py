"""Labeling stats: seed rejection, human-added labels and the blind human baseline.

Usage: uv run python scripts/labeling_stats.py [--json out.json]
Reads data/labels/labeling_log.jsonl (latest record per listing). No API calls.
The blind human baseline is reported with and without the calibration listings (the first
CALIBRATION_N listings labeled); their labels stay in gold either way.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
import yaml

from wanderlint.labeling import (
    CALIBRATION_N,
    DEFECT_IDS,
    baseline,
    calibration_ids,
    compute_stats,
    latest_records,
)

ROOT = Path(__file__).resolve().parents[1]


def _pct(x: float | None) -> str:
    return "  –  " if x is None else f"{x:5.1%}"


def _num(x: float | None) -> str:
    return "–" if x is None else f"{x}"


def _baseline_row(name: str, b: dict) -> str:
    return (
        f"{name:22}  {b['listings_labeled']:8}  {_pct(b['blind_recall']):>6}  "
        f"{_pct(b['blind_precision']):>9}  {_num(b['median_seconds_per_listing']):>10}  "
        f"{_num(b['median_blind_seconds']):>7}  {_num(b['median_reconcile_seconds']):>9}  "
        f"{b['time_capped_records']:6}"
    )


def main(
    json_out: Annotated[Path | None, typer.Option("--json", help="Write stats as JSON.")] = None,
) -> None:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    labels_dir = ROOT / cfg["paths"]["labels_dir"]
    total = sum(1 for line in (ROOT / cfg["datagen"]["output_listings"]).open() if line.strip())
    records = latest_records(labels_dir)
    calib = calibration_ids(labels_dir)
    st = compute_stats(records.values())
    b_all = baseline(st)
    b_excl = baseline(compute_stats(r for lid, r in records.items() if lid not in calib))

    typer.echo(f"Listings labeled: {st['listings_labeled']}/{total}")
    typer.echo(
        f"Seeds reviewed: {st['seeds']} · rejected {st['rejected']} (of which retyped "
        f"{st['retyped']}) · rejection rate {_pct(st['rejection_rate']).strip()}"
    )
    typer.echo(f"Human-added labels: {st['human_added']} · unsure: {st['unsure']}")
    typer.echo(
        f"Calibration listings (first {CALIBRATION_N} labeled; kept in gold, "
        f"excluded from baseline below): {len(calib)}"
    )
    typer.echo("")
    typer.echo("Blind human baseline (recall vs confirmed seeds; precision = blind labels kept in")
    typer.echo("gold; time medians exclude time-capped records)")
    typer.echo(
        f"{'':22}  {'listings':>8}  {'recall':>6}  {'precision':>9}  {'median s':>10}  "
        f"{'blind s':>7}  {'reconc. s':>9}  {'capped':>6}"
    )
    typer.echo(_baseline_row("all listings", b_all))
    typer.echo(_baseline_row("excluding calibration", b_excl))
    typer.echo("")
    typer.echo(
        "type  seeds  rej  retyped  rej.rate  added  conf  blind.rec  blind  dropped  blind.prec"
        "  | excl. calibration: rec   prec"
    )
    for d in DEFECT_IDS:
        t = st["per_type"][d]
        x = b_excl["per_type"][d]
        typer.echo(
            f"{d:4}  {t['seeds']:5}  {t['rejected']:3}  {t['retyped']:7}  "
            f"{_pct(t['rejection_rate']):>8}  {t['human_added']:5}  {t['confirmed_seeds']:4}  "
            f"{_pct(t['blind_recall']):>9}  {t['blind_labels']:5}  "
            f"{t['blind_dropped_after_reveal']:7}  {_pct(t['blind_precision']):>10}"
            f"  |                    {_pct(x['blind_recall']):>5}  {_pct(x['blind_precision']):>5}"
        )
    if json_out:
        out = {
            **st,
            "calibration_listing_ids": calib,
            "baseline_all": b_all,
            "baseline_excluding_calibration": b_excl,
        }
        json_out.write_text(json.dumps(out, indent=2), encoding="utf-8")


if __name__ == "__main__":
    typer.run(main)
