"""Labeling stats: progress per split, seed rejection, human-added labels, human baseline.

Usage: uv run python scripts/labeling_stats.py [--json out.json]
Reads data/labels/labeling_log.jsonl (latest record per listing) and data/splits.json. No API
calls. The blind human baseline comes from checklist listings only (blind_mode="checklist"):
all splits, and test only. Verify (dev, seeds visible) and free-mode (legacy, incl. re-checked
test listings) records are left out. A blind claim matches a gold label only if it has the same
defect type and names at least one field the label's spans touch (recall and precision).
Drop reasons: rule = guide rule says it's not this defect, misread = misread the listing,
other, ? = not recorded (free-mode records).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
import yaml

from wanderlint.labeling import (
    DEFECT_IDS,
    baseline,
    compute_stats,
    latest_records,
    split_progress,
)
from wanderlint.splits import load_split

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


def _progress_line(name: str, p: dict) -> str:
    modes = ", ".join(f"{m} {n}" for m, n in sorted(p["modes"].items())) or "none"
    line = f"{name:5} {p['labeled']:3}/{p['total']:<3} labeled ({modes})"
    if p["recheck_total"]:
        line += f" · re-checked {p['rechecked']}/{p['recheck_total']}"
    return line


def _checklist_table(title: str, b: dict) -> None:
    typer.echo(f"{title} ({b['listings_labeled']} listings)")
    typer.echo("type  conf  yes  unsure  recall  precision  dropped  drops r/m/o/?")
    for d in DEFECT_IDS:
        x = b["per_type"][d]
        why = x["drop_reasons"]
        drops = f"{why['guide_rule']}/{why['misread']}/{why['other']}/{why['unrecorded']}"
        typer.echo(
            f"{d:4}  {x['confirmed_seeds']:4}  {x['blind_labels']:3}  {x['blind_unsure']:6}  "
            f"{_pct(x['blind_recall']):>6}  {_pct(x['blind_precision']):>9}  "
            f"{x['blind_dropped_after_reveal']:7}  {drops:>13}"
        )


def main(
    json_out: Annotated[Path | None, typer.Option("--json", help="Write stats as JSON.")] = None,
) -> None:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    split_of = load_split(ROOT / cfg["paths"]["splits"])
    records = latest_records(ROOT / cfg["paths"]["labels_dir"])
    st = compute_stats(records.values())
    checklist = [r for r in records.values() if r.blind_mode == "checklist"]
    b_check = baseline(compute_stats(checklist))
    b_test = baseline(compute_stats(r for r in checklist if split_of[r.listing_id] == "test"))
    progress = split_progress(records, split_of)

    typer.echo(f"Listings labeled: {st['listings_labeled']}/{len(split_of)}")
    for name in ("test", "dev"):
        typer.echo("  " + _progress_line(name, progress[name]))
    typer.echo(
        f"Seeds reviewed: {st['seeds']} · rejected {st['rejected']} (of which retyped "
        f"{st['retyped']}) · rejection rate {_pct(st['rejection_rate']).strip()}"
    )
    typer.echo(f"Human-added labels: {st['human_added']} · unsure: {st['unsure']}")
    typer.echo("")
    typer.echo("Blind human baseline: checklist listings only (match = same type + a shared")
    typer.echo("field; recall vs confirmed seeds; precision vs final gold; time medians exclude")
    typer.echo("time-capped records)")
    typer.echo(
        f"{'':22}  {'listings':>8}  {'recall':>6}  {'precision':>9}  {'median s':>10}  "
        f"{'blind s':>7}  {'reconc. s':>9}  {'capped':>6}"
    )
    typer.echo(_baseline_row("checklist, all splits", b_check))
    typer.echo(_baseline_row("checklist, test only", b_test))
    typer.echo("")
    typer.echo("All listings, per type (seeds and human-added labels)")
    typer.echo("type  seeds  rej  retyped  rej.rate  added  conf")
    for d in DEFECT_IDS:
        t = st["per_type"][d]
        typer.echo(
            f"{d:4}  {t['seeds']:5}  {t['rejected']:3}  {t['retyped']:7}  "
            f"{_pct(t['rejection_rate']):>8}  {t['human_added']:5}  {t['confirmed_seeds']:4}"
        )
    typer.echo("")
    _checklist_table("Checklist baseline per type, all splits", b_check)
    typer.echo("")
    _checklist_table("Checklist baseline per type, test only", b_test)
    if json_out:
        out = {
            **st,
            "split_progress": progress,
            "baseline_checklist": b_check,
            "baseline_checklist_test": b_test,
        }
        json_out.write_text(json.dumps(out, indent=2), encoding="utf-8")


if __name__ == "__main__":
    typer.run(main)
