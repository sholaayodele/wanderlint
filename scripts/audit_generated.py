"""Deterministic audit of the generated dataset (see wanderlint.datagen.audit for the checks).

Usage: uv run python scripts/audit_generated.py [--json out.json]
Exits 1 if any check has violations.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Annotated

import typer
import yaml

from wanderlint.datagen.audit import CHECKS, audit_dataset
from wanderlint.models import Listing

ROOT = Path(__file__).resolve().parents[1]


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main(
    json_out: Annotated[
        Path | None, typer.Option("--json", help="Write violations as JSON.")
    ] = None,
) -> None:
    dg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["datagen"]
    listings = {d["id"]: Listing(**d) for d in _read_jsonl(ROOT / dg["output_listings"])}
    meta = {m["listing_id"]: m for m in _read_jsonl(ROOT / dg["output_meta"])}
    labels: dict[str, list[dict]] = defaultdict(list)
    for lab in _read_jsonl(ROOT / dg["output_seeded"]):
        labels[lab["listing_id"]].append(lab)
    report, n_defective = audit_dataset(listings, meta, labels)
    typer.echo(f"Audited {n_defective} defective listings ({len(listings)} total)")
    for c in CHECKS:
        typer.echo(f"  {c:16} {len(report[c])} violations")
        for p in report[c]:
            typer.echo(f"    - {p}")
    if json_out:
        json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if any(report.values()):
        raise typer.Exit(code=1)


if __name__ == "__main__":
    typer.run(main)
