"""Frozen dev/test split: stratification, determinism, freeze."""

import json
from pathlib import Path

import pytest
import yaml

from wanderlint.labeling import load_listings, load_seeds
from wanderlint.splits import (
    SPLIT_SEED,
    balance,
    load_split,
    strat_labels,
    stratified_split,
    write_split,
)

ROOT = Path(__file__).resolve().parents[1]
CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def _real():
    listings = load_listings(ROOT / CFG["datagen"]["output_listings"])
    seeds = load_seeds(ROOT / CFG["datagen"]["output_seeded"])
    return listings, strat_labels(listings.values(), seeds)


def test_split_sizes_determinism_and_balance() -> None:
    _, labels = _real()
    sizes = {"test": 50, "dev": 100}
    a = stratified_split(labels, sizes, SPLIT_SEED)
    assert a == stratified_split(labels, sizes, SPLIT_SEED)  # deterministic
    assert set(a) == set(labels)  # every listing in exactly one split
    assert sum(s == "test" for s in a.values()) == 50
    # defect types and locales within ±1 of a third in test; "clean" takes the slack (±2)
    for k, c in balance(labels, a).items():
        assert abs(c["test"] - (c["dev"] + c["test"]) / 3) <= (2 if k == "clean" else 1), k


def test_split_sizes_must_add_up() -> None:
    with pytest.raises(ValueError, match="add up"):
        stratified_split({"a": {"en-GB"}}, {"test": 1, "dev": 1})


def test_committed_split_is_the_seeded_one() -> None:
    """data/splits.json is what seed 11 produces, and dev/test.jsonl match it."""
    listings, labels = _real()
    frozen = load_split(ROOT / CFG["paths"]["splits"])
    sizes = {"test": CFG["split"]["test_size"], "dev": len(labels) - CFG["split"]["test_size"]}
    assert frozen == stratified_split(labels, sizes, CFG["split"]["seed"])
    for s in ("dev", "test"):
        ids = [json.loads(x)["id"] for x in (ROOT / CFG["paths"][s]).read_text().splitlines()]
        assert sorted(ids) == sorted(i for i, x in frozen.items() if x == s)


def test_write_split_refuses_to_overwrite(tmp_path: Path) -> None:
    listings, labels = _real()
    split_of = stratified_split(labels, {"test": 50, "dev": 100})
    path = tmp_path / "splits.json"
    write_split(listings, split_of, tmp_path, path, {"seed": 11})
    assert load_split(path) == split_of
    assert len((tmp_path / "test.jsonl").read_text().splitlines()) == 50
    with pytest.raises(FileExistsError, match="frozen"):
        write_split(listings, split_of, tmp_path, path, {"seed": 11})
