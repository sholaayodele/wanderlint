"""Freeze the dev/test split (stratified by seeded defect types + locale). Run once.

Usage: uv run python scripts/make_split.py [--dry-run]   (= make split)
Writes data/listings/dev.jsonl, data/listings/test.jsonl and data/splits.json; refuses to
overwrite an existing splits.json. Prints the balance per split, how many labeled listings land
in each, and the free-mode test listings that need a re-check. No API calls.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Annotated

import typer
import yaml

from wanderlint.labeling import latest_records, load_listings, load_seeds, needs_recheck
from wanderlint.splits import SPLITS, balance, strat_labels, stratified_split, write_split

ROOT = Path(__file__).resolve().parents[1]


def main(
    dry_run: Annotated[bool, typer.Option(help="Print the balance, write nothing.")] = False,
) -> None:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    if not dry_run and (ROOT / cfg["paths"]["splits"]).exists():
        typer.echo(f"{cfg['paths']['splits']} exists: the split is frozen. Nothing written.")
        raise typer.Exit(1)
    listings = load_listings(ROOT / cfg["datagen"]["output_listings"])
    seeds = load_seeds(ROOT / cfg["datagen"]["output_seeded"])
    seed, n_test = cfg["split"]["seed"], cfg["split"]["test_size"]
    sizes = {"test": n_test, "dev": len(listings) - n_test}
    labels = strat_labels(listings.values(), seeds)
    split_of = stratified_split(labels, sizes, seed)

    bal = balance(labels, split_of)
    typer.echo(f"Split (seed {seed}): " + ", ".join(f"{s} {sizes[s]}" for s in SPLITS))
    typer.echo(f"{'':8}{'dev':>5}{'test':>6}{'test %':>8}")
    for k, c in bal.items():
        typer.echo(f"{k:8}{c['dev']:5}{c['test']:6}{c['test'] / (c['dev'] + c['test']):8.0%}")
    records = latest_records(ROOT / cfg["paths"]["labels_dir"])
    done = Counter((split_of[i], r.blind_mode) for i, r in records.items())
    for s in SPLITS:
        modes = ", ".join(f"{m} {n}" for (sp, m), n in sorted(done.items()) if sp == s)
        typer.echo(f"labeled in {s}: {sum(n for (sp, _), n in done.items() if sp == s)} ({modes})")
    recheck = needs_recheck(records, split_of)
    typer.echo(f"free-mode test listings to re-check ({len(recheck)}): {', '.join(recheck)}")
    if dry_run:
        typer.echo("Dry run: nothing written.")
        return
    meta = {
        "seed": seed,
        "method": "iterative stratification over {locale} + seeded defect types ('clean' if none)",
        "sizes": sizes,
        "balance": bal,
    }
    write_split(
        listings, split_of, ROOT / cfg["paths"]["listings_dir"], ROOT / cfg["paths"]["splits"], meta
    )
    typer.echo(f"Wrote {cfg['paths']['dev']}, {cfg['paths']['test']}, {cfg['paths']['splits']}")


if __name__ == "__main__":
    typer.run(main)
