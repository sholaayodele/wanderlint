"""Deterministic planning of listing specs: locale, content, traps, seeded defects (no API)."""

from __future__ import annotations

import random
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from wanderlint.datagen.vocab import (
    ACTIVITIES,
    CITIES,
    DEFECT_IDS,
    FEE_FORMATS,
    HARD_CASES,
    HARD_VARIANT_METHOD,
    LENGTHS,
    QUALITY_WEIGHTS,
    REGISTERED_OK_CATEGORIES,
    TIME_VARIANTS,
    TRAP_ACRONYMS,
    TRAP_KINDS,
    VARIANTS,
    Method,
)
from wanderlint.models import Locale


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TrapSpec(_Strict):
    kind: str
    detail: str = ""


class DefectSpec(_Strict):
    defect_id: str
    method: Method
    variant: str
    hard_case: str | None = None


class ListingSpec(_Strict):
    id: str
    locale: Locale
    city: str
    country: str
    category: str
    tags: list[str]
    activity: str
    alcohol: bool
    length: str
    quality: str
    traps: list[TrapSpec]
    defects: list[DefectSpec]

    @property
    def is_clean(self) -> bool:
        return not self.defects


@dataclass(frozen=True)
class PlanConfig:
    clean_fraction: float = 0.40
    de_fraction: float = 0.30
    min_per_defect_fraction: float = 0.10
    hard_case_fraction: float = 0.20
    defective_trap_fraction: float = 0.30

    @classmethod
    def from_config(cls, datagen: dict) -> PlanConfig:
        return cls(**{k: float(datagen[k]) for k in cls.__dataclass_fields__})


def _trap_detail(kind: str, locale: str, category: str, rng: random.Random) -> str:
    if kind == "title_acronym_or_registered":
        # "acronym:A|B|C" lists the claim-free acronyms allowed for this category.
        acronym = "acronym:" + "|".join(TRAP_ACRONYMS[category])
        if category not in REGISTERED_OK_CATEGORIES:
            return acronym
        return rng.choice(["registered", acronym])
    if kind == "fee_in_cancellation":
        return rng.choice(FEE_FORMATS[locale])
    if kind == "locale_variant":
        return rng.choice(TIME_VARIANTS[locale])
    return ""


def _assign_defect_types(counts: list[int], quota: int, rng: random.Random) -> list[list[str]]:
    """Distribute defect types over listings: ≥ quota per type, distinct within a listing."""
    total = sum(counts)
    pool = [d for d in DEFECT_IDS for _ in range(quota)]
    pool += rng.choices(DEFECT_IDS, k=total - len(pool))
    for _ in range(200):
        rng.shuffle(pool)
        slots: list[list[str]] = [[] for _ in counts]
        ok = True
        for d in pool:
            open_idx = [i for i, c in enumerate(counts) if len(slots[i]) < c and d not in slots[i]]
            if not open_idx:
                ok = False
                break
            most = max(counts[i] - len(slots[i]) for i in open_idx)
            best = [i for i in open_idx if counts[i] - len(slots[i]) == most]
            slots[rng.choice(best)].append(d)
        if ok:
            return [sorted(s) for s in slots]
    raise RuntimeError("could not distribute defect types; adjust fractions")


def plan_specs(n: int, seed: int, cfg: PlanConfig, categories: list[str]) -> list[ListingSpec]:
    if n < 1:
        raise ValueError("n must be ≥ 1")
    rng = random.Random(seed)

    n_clean = round(n * cfg.clean_fraction)
    n_def = n - n_clean
    n_de = round(n * cfg.de_fraction)
    locales: list[str] = ["de-DE"] * n_de + ["en-GB"] * (n - n_de)
    rng.shuffle(locales)
    defective = [False] * n_clean + [True] * n_def
    rng.shuffle(defective)
    cats = [categories[i % len(categories)] for i in range(n)]
    rng.shuffle(cats)

    # Defect counts per defective listing (1-3), bumped until quotas fit.
    quota = max(1, round(n * cfg.min_per_defect_fraction))
    counts = [rng.choices([1, 2, 3], weights=[0.4, 0.35, 0.25])[0] for _ in range(n_def)]
    while sum(counts) < quota * len(DEFECT_IDS):
        bumpable = [i for i, c in enumerate(counts) if c < 3]
        if not bumpable:
            raise ValueError(f"n={n} too small for {quota} defects of each type")
        counts[rng.choice(bumpable)] += 1
    types_per_listing = _assign_defect_types(counts, quota, rng)

    # Variant per defect: rotate through each type's variants for even coverage.
    decks: dict[str, list[tuple[Method, str]]] = {}
    defect_specs: list[list[DefectSpec]] = []
    for types in types_per_listing:
        specs = []
        for d in types:
            if not decks.get(d):
                decks[d] = list(VARIANTS[d])
                rng.shuffle(decks[d])
            method, variant = decks[d].pop()
            specs.append(DefectSpec(defect_id=d, method=method, variant=variant))
        defect_specs.append(specs)

    # Hard cases: ~hard_case_fraction of all seeded defects, rotating over the 5 pairs.
    all_refs = [(li, di) for li, specs in enumerate(defect_specs) for di in range(len(specs))]
    n_hard = round(len(all_refs) * cfg.hard_case_fraction)
    pairs = list(HARD_CASES)
    rng.shuffle(pairs)
    for i in range(n_hard):
        pair = pairs[i % len(pairs)]
        target, variant = HARD_CASES[pair]
        candidates = [
            (li, di)
            for li, di in all_refs
            if defect_specs[li][di].defect_id == target and defect_specs[li][di].hard_case is None
        ]
        if not candidates:
            continue
        li, di = rng.choice(candidates)
        defect_specs[li][di] = DefectSpec(
            defect_id=target, method=HARD_VARIANT_METHOD[variant], variant=variant, hard_case=pair
        )

    # Traps: every clean listing gets 1-2; ~defective_trap_fraction of defective listings get 1.
    trap_deck: list[str] = []

    def next_traps(k: int) -> list[str]:
        chosen: list[str] = []
        while len(chosen) < k:
            if all(x in chosen for x in trap_deck):
                fresh = list(TRAP_KINDS)
                rng.shuffle(fresh)
                trap_deck[:0] = fresh  # refill below the remaining cards
            for idx in range(len(trap_deck) - 1, -1, -1):
                if trap_deck[idx] not in chosen:
                    chosen.append(trap_deck.pop(idx))
                    break
        return chosen

    def_indices = [i for i, d in enumerate(defective) if d]
    trapped_defective = set(rng.sample(def_indices, round(n_def * cfg.defective_trap_fraction)))

    out: list[ListingSpec] = []
    def_iter = iter(defect_specs)
    for i in range(n):
        locale = locales[i]
        category = cats[i]
        activity, suggested, alcohol = rng.choice(ACTIVITIES[category])
        tags = sorted(rng.sample(suggested, k=rng.randint(1, len(suggested)))) if suggested else []
        city, country = rng.choice(CITIES)
        length = rng.choice(list(LENGTHS))
        quality = rng.choices(list(QUALITY_WEIGHTS), weights=list(QUALITY_WEIGHTS.values()))[0]
        if defective[i]:
            defects = next(def_iter)
            kinds = next_traps(1) if i in trapped_defective else []
        else:
            defects = []
            kinds = next_traps(rng.choice([1, 2]))
        traps = [TrapSpec(kind=k, detail=_trap_detail(k, locale, category, rng)) for k in kinds]
        out.append(
            ListingSpec(
                id=f"gen-{i + 1:04d}",
                locale=locale,  # type: ignore[arg-type]
                city=city,
                country=country,
                category=category,
                tags=tags,
                activity=activity,
                alcohol=alcohol,
                length=length,
                quality=quality,
                traps=traps,
                defects=defects,
            )
        )
    return out
