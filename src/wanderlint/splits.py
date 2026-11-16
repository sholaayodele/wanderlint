"""Frozen dev/test split: iterative (multi-label) stratification, deterministic for a seed.

Each listing's stratification labels are its locale plus its seeded defect types ("clean" if
none), so a listing with D2+D5+D7 counts toward all three types (Sechidis et al., 2011:
repeatedly take the rarest remaining label and hand its listings to the split that still wants
it most). No API calls; nothing here reads generated_meta.jsonl.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path

from wanderlint.models import GoldLabel, Listing

SPLIT_SEED = 11
SPLITS = ("dev", "test")


def strat_labels(
    listings: Iterable[Listing], seeds: Mapping[str, Iterable[GoldLabel]]
) -> dict[str, set[str]]:
    """listing id → {locale} ∪ seeded defect types (or {"clean"})."""
    out = {}
    for x in listings:
        types = {g.defect_id for g in seeds.get(x.id, [])}
        out[x.id] = {x.locale} | (types or {"clean"})
    return out


def stratified_split(
    labels: Mapping[str, set[str]], sizes: Mapping[str, int], seed: int = SPLIT_SEED
) -> dict[str, str]:
    """listing id → split name, with exactly `sizes[split]` listings per split."""
    if sum(sizes.values()) != len(labels):
        raise ValueError(f"split sizes {dict(sizes)} don't add up to {len(labels)} listings")
    rng = random.Random(seed)
    order = sorted(labels)
    rng.shuffle(order)
    total = Counter(lab for ls in labels.values() for lab in ls)
    n = len(labels)
    want = {s: Counter({k: v * sizes[s] / n for k, v in total.items()}) for s in sizes}
    need = dict(sizes)
    remaining = set(order)
    out: dict[str, str] = {}
    while remaining:
        left = Counter(lab for i in remaining for lab in labels[i])
        rarest = min(left, key=lambda k: (left[k], k))
        for i in [i for i in order if i in remaining and rarest in labels[i]]:
            # most wanted for this label, then most room left, then a seeded coin flip
            ranked = sorted(sizes, key=lambda s: (-want[s][rarest], -need[s], rng.random()))
            s = next(s for s in ranked if need[s] > 0)
            out[i] = s
            remaining.discard(i)
            need[s] -= 1
            for lab in labels[i]:
                want[s][lab] -= 1
    return out


def balance(labels: Mapping[str, set[str]], split_of: Mapping[str, str]) -> dict[str, dict]:
    """Per stratification label: count per split."""
    keys = sorted({lab for ls in labels.values() for lab in ls}, key=_label_order)
    return {
        k: {s: sum(1 for i, ls in labels.items() if k in ls and split_of[i] == s) for s in SPLITS}
        for k in keys
    }


def _label_order(k: str) -> tuple[int, str]:
    return (0 if "-" in k else 1 if k == "clean" else 2, k)


def write_split(
    listings: Mapping[str, Listing],
    split_of: Mapping[str, str],
    listings_dir: Path,
    splits_path: Path,
    meta: dict,
) -> None:
    """dev.jsonl / test.jsonl (listings sorted by id) and splits.json. Refuses to overwrite."""
    if splits_path.exists():
        raise FileExistsError(f"{splits_path} exists: the split is frozen")
    for s in SPLITS:
        ids = sorted(i for i, x in split_of.items() if x == s)
        (listings_dir / f"{s}.jsonl").write_text(
            "".join(listings[i].model_dump_json() + "\n" for i in ids), encoding="utf-8"
        )
    body = {**meta, **{s: sorted(i for i, x in split_of.items() if x == s) for s in SPLITS}}
    splits_path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def load_split(splits_path: Path) -> dict[str, str]:
    """listing id → split name, from splits.json."""
    body = json.loads(splits_path.read_text(encoding="utf-8"))
    return {i: s for s in SPLITS for i in body[s]}
