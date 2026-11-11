"""Blind-then-reveal labeling: pure logic behind scripts/label_app.py and labeling_stats.py.

Flow per listing: a blind pass (seeds hidden) → reveal seeds → reconcile each seed
(confirm / reject / retype) → save. `labeling_log.jsonl` is the source of truth (append-only,
latest record per listing wins); gold, rejected_seeds and unsure are rebuilt from it on every
save, so re-editing a listing never duplicates rows. No API calls; nothing here reads
generated_meta.jsonl (hard-case / trap info would leak the answer).
"""

from __future__ import annotations

import json
import os
import random
import re
import statistics
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from wanderlint.datagen.checks import span_present
from wanderlint.datagen.vocab import LIST_FIELDS, TEXT_FIELDS
from wanderlint.models import DefectId, GoldLabel, Listing, RejectedSeed, Span, UnsureLabel

DEFECT_IDS: list[str] = ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"]
ORDER_SEED = 7
PHASE_CAP_S = 600.0  # 10 minutes of active time per phase
CALIBRATION_N = 10  # the first N listings labeled are calibration (kept in gold, not baseline)

GOLD = "gold.jsonl"
REJECTED = "rejected_seeds.jsonl"
UNSURE = "unsure.jsonl"
LOG = "labeling_log.jsonl"

SeedAction = Literal["confirm", "reject", "retype"]
BlindMode = Literal["free", "checklist"]
Answer = Literal["yes", "no", "unsure"]
DropCode = Literal["guide_rule", "misread", "other"]
DROP_REASONS: dict[str, str] = {
    "guide_rule": "guide rule says it's not this defect",
    "misread": "misread the listing",
    "other": "other",
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BlindLabel(_Strict):
    defect_id: DefectId
    spans: list[Span] = Field(min_length=1)


class SeedOutcome(_Strict):
    defect_id: DefectId  # the seed's type
    action: SeedAction
    reason: str = ""
    new_defect_id: DefectId | None = None  # retype only


class ChecklistAnswer(_Strict):
    """Blind-pass answer for one defect type. Yes needs field(s), Unsure a reason; no spans."""

    defect_id: DefectId
    answer: Answer
    fields: list[str] = Field(default_factory=list)
    reason: str = ""


class DropReason(_Strict):
    """Why a blind Yes was dropped after the reveal."""

    defect_id: DefectId
    reason: DropCode
    note: str = ""


class LogRecord(_Strict):
    listing_id: str
    # "free": legacy free-form blind labels with spans; "checklist": Yes/No/Unsure per type
    blind_mode: BlindMode = "free"
    checklist: list[ChecklistAnswer] = Field(default_factory=list)
    drop_reasons: list[DropReason] = Field(default_factory=list)
    blind_labels: list[BlindLabel] = Field(default_factory=list)
    unsure: list[UnsureLabel] = Field(default_factory=list)
    seeds: list[GoldLabel] = Field(default_factory=list)
    seed_outcomes: list[SeedOutcome] = Field(default_factory=list)
    dropped_blind: list[DefectId] = Field(default_factory=list)  # blind types removed at reveal
    final_labels: list[GoldLabel] = Field(default_factory=list)
    rejected: list[RejectedSeed] = Field(default_factory=list)
    blind_seconds: float = 0.0
    reconcile_seconds: float = 0.0
    time_capped: bool = False
    edited: bool = False
    saved_at: str


class ReconcileError(ValueError):
    """The reconcile decisions are incomplete or contradictory."""


# --- Order & resume -----------------------------------------------------------


def labeling_order(listing_ids: Iterable[str], seed: int = ORDER_SEED) -> list[str]:
    """Fixed random order (not plan order), so clean/defective and locales are mixed."""
    ids = sorted(listing_ids)
    random.Random(seed).shuffle(ids)
    return ids


def resume_index(order: list[str], done: Iterable[str]) -> int:
    """Index of the first listing without a saved record (len(order) if all are done)."""
    done_set = set(done)
    return next((i for i, lid in enumerate(order) if lid not in done_set), len(order))


# --- Fields & span validation -------------------------------------------------


def field_options(listing: Listing) -> list[str]:
    attrs = [f"attributes.{k}" for k in sorted(listing.attributes)]
    return [*TEXT_FIELDS, *LIST_FIELDS, "tags", "category", *attrs]


def field_choices(listing: Listing, field: str) -> list[str] | None:
    """Allowed span values for non-free-text fields (pick, don't type); None = free text."""
    if field in (*LIST_FIELDS, "tags"):
        items = getattr(listing, field)
        return list(items) if items else [""]
    if field == "category":
        return [listing.category]
    if field.startswith("attributes."):
        val = listing.attributes.get(field.split(".", 1)[1])
        if val is None:
            return []
        return [str(val).lower() if isinstance(val, bool) else str(val)]
    return None


def validate_span(listing: Listing, span: Span) -> str | None:
    """None if the span is verbatim in its field (labeling_guide §2), else an error message."""
    if span.field not in field_options(listing):
        return f"Unknown field {span.field!r}"
    if span.span != span.span.strip():
        return "Span has leading/trailing whitespace"
    if span_present(listing, span):
        return None
    if span.span == "":
        return f"{span.field} isn't empty; quote the text instead"
    if span.field in (*LIST_FIELDS, "tags"):
        return f"Not an exact {span.field} item"
    return f"Not found verbatim in {span.field}"


# --- Reconcile ----------------------------------------------------------------


def _merge(spans: list[Span], extra: Iterable[Span]) -> list[Span]:
    out = list(spans)
    out += [s for s in extra if s not in out]
    return out


def blind_by_type(blind: Iterable[BlindLabel]) -> dict[str, list[Span]]:
    by: dict[str, list[Span]] = {}
    for b in blind:
        by[b.defect_id] = _merge(by.get(b.defect_id, []), b.spans)
    return by


def match_seeds(
    blind: Iterable[BlindLabel], seeds: Iterable[GoldLabel]
) -> dict[str, tuple[GoldLabel | None, list[Span] | None]]:
    """Pair seeds and blind labels by defect type: type → (seed, blind spans)."""
    by_blind = blind_by_type(blind)
    by_seed = {s.defect_id: s for s in seeds}
    return {
        d: (by_seed.get(d), by_blind.get(d)) for d in DEFECT_IDS if d in by_seed or d in by_blind
    }


def reconcile(
    listing_id: str,
    blind: list[BlindLabel],
    seeds: list[GoldLabel],
    outcomes: list[SeedOutcome],
    dropped_blind: Iterable[str] = (),
    removed_spans: Iterable[tuple[str, Span]] = (),
) -> tuple[list[GoldLabel], list[RejectedSeed]]:
    """Final gold labels and rejected seeds for one listing.

    confirm → `confirmed`, seed spans + the blind spans of that type not already there;
    reject  → rejected_seeds (reason required);
    retype  → seed rejected ("retyped to Dx"), Dx `added_by_human` (merged with a blind Dx);
    blind labels without a seed → `added_by_human`. Rejecting/retyping a seed while keeping a
    blind label of the same type is contradictory: drop the blind label or confirm the seed.
    """
    dropped = set(dropped_blind)
    claimed = {d: s for d, s in blind_by_type(blind).items() if d not in dropped}
    return _reconcile_claims(listing_id, claimed, seeds, outcomes, removed_spans)


def _reconcile_claims(
    listing_id: str,
    kept_blind: dict[str, list[Span]],
    seeds: list[GoldLabel],
    outcomes: list[SeedOutcome],
    removed_spans: Iterable[tuple[str, Span]] = (),
) -> tuple[list[GoldLabel], list[RejectedSeed]]:
    """Shared core: `kept_blind` = the types the annotator still claims, with their spans."""
    by_outcome = {o.defect_id: o for o in outcomes}
    final: dict[str, tuple[str, list[Span]]] = {}
    rejected: list[RejectedSeed] = []
    retyped: list[tuple[str, list[Span]]] = []
    for seed in seeds:
        d = seed.defect_id
        o = by_outcome.get(d)
        if o is None:
            raise ReconcileError(f"Seed {d}: choose confirm, reject or change type")
        if o.action == "confirm":
            final[d] = ("confirmed", _merge(seed.spans, kept_blind.get(d, [])))
            continue
        if d in kept_blind:
            raise ReconcileError(
                f"Seed {d} is {o.action}ed but your blind {d} label is kept: drop it or confirm"
            )
        if o.action == "reject":
            if not o.reason.strip():
                raise ReconcileError(f"Seed {d}: a reason is required to reject")
            rejected.append(RejectedSeed(listing_id=listing_id, defect_id=d, reason=o.reason))
        else:
            new = o.new_defect_id
            if new is None or new == d:
                raise ReconcileError(f"Seed {d}: pick a different type to change to")
            reason = f"retyped to {new}" + (f": {o.reason.strip()}" if o.reason.strip() else "")
            rejected.append(RejectedSeed(listing_id=listing_id, defect_id=d, reason=reason))
            retyped.append((new, seed.spans))
    for d, spans in [*kept_blind.items(), *retyped]:
        if d in final:
            final[d] = (final[d][0], _merge(final[d][1], spans))
        else:
            final[d] = ("added_by_human", list(spans))
    removed = list(removed_spans)
    labels = []
    for d in DEFECT_IDS:
        if d not in final:
            continue
        src, spans = final[d]
        if not spans:
            raise ReconcileError(f"{d}: add at least one span (verbatim) or drop the label")
        spans = [s for s in spans if (d, s) not in removed]
        if not spans:
            raise ReconcileError(f"{d}: every span was removed; keep one or drop the label")
        labels.append(GoldLabel(listing_id=listing_id, defect_id=d, spans=spans, label_source=src))
    return labels, rejected


# --- Checklist blind pass -----------------------------------------------------


def _by_type(items: Iterable[ChecklistAnswer | DropReason]) -> dict:
    return {i.defect_id: i for i in items}


def checklist_problems(answers: Iterable[ChecklistAnswer]) -> list[str]:
    """What still blocks the reveal: every type answered, Yes with field(s), Unsure with reason."""
    by = _by_type(answers)
    out = []
    for d in DEFECT_IDS:
        a = by.get(d)
        if a is None:
            out.append(f"{d}: not answered")
        elif a.answer == "yes" and not a.fields:
            out.append(f"{d}: pick the field(s)")
        elif a.answer == "unsure" and not a.reason.strip():
            out.append(f"{d}: give a one-line reason")
    return out


def checklist_yes(answers: Iterable[ChecklistAnswer]) -> list[str]:
    by = _by_type(answers)
    return [d for d in DEFECT_IDS if d in by and by[d].answer == "yes"]


def reconcile_checklist(
    listing_id: str,
    answers: list[ChecklistAnswer],
    seeds: list[GoldLabel],
    outcomes: list[SeedOutcome],
    drops: list[DropReason] = (),
    human_spans: dict[str, list[Span]] | None = None,
    removed_spans: Iterable[tuple[str, Span]] = (),
) -> tuple[list[GoldLabel], list[RejectedSeed]]:
    """Reconcile a checklist blind pass with the seeds.

    A blind Yes is kept unless dropped (with a reason). A kept Yes matching a confirmed seed
    takes the seed spans (+ any spans entered); a kept Yes without one becomes `added_by_human`
    and needs ≥ 1 verbatim span entered in reconcile. Seed rules as in `reconcile`.
    """
    problems = checklist_problems(answers)
    if problems:
        raise ReconcileError("Checklist incomplete: " + "; ".join(problems))
    yes = checklist_yes(answers)
    dropped = _by_type(drops)
    for d, dr in dropped.items():
        if d not in yes:
            raise ReconcileError(f"{d}: only a blind Yes can be dropped")
        if dr.reason == "other" and not dr.note.strip():
            raise ReconcileError(f"{d}: describe the 'other' drop reason")
    spans = human_spans or {}
    claimed = {d: list(spans.get(d, [])) for d in yes if d not in dropped}
    return _reconcile_claims(listing_id, claimed, seeds, outcomes, removed_spans)


def checklist_unsure(
    listing_id: str, answers: Iterable[ChecklistAnswer], final_types: Iterable[str]
) -> list[UnsureLabel]:
    """Blind Unsure answers still open after reconcile (types that didn't end up in gold)."""
    done = set(final_types)
    return [
        UnsureLabel(listing_id=listing_id, candidate_defect_ids=[a.defect_id], reason=a.reason)
        for a in answers
        if a.answer == "unsure" and a.defect_id not in done
    ]


def blind_claims(record: LogRecord) -> set[str]:
    """The defect types the annotator claimed in the blind pass (per listing, per type)."""
    if record.blind_mode == "checklist":
        return set(checklist_yes(record.checklist))
    return set(blind_by_type(record.blind_labels))


# --- Timing -------------------------------------------------------------------


@dataclass
class PhaseTimer:
    """Active time of one phase; paused when `started` is None."""

    accumulated: float = 0.0
    started: float | None = None

    @property
    def running(self) -> bool:
        return self.started is not None

    def start(self, now: float) -> None:
        if self.started is None:
            self.started = now

    def pause(self, now: float) -> None:
        if self.started is not None:
            self.accumulated += now - self.started
            self.started = None

    def elapsed(self, now: float) -> float:
        return self.accumulated + (now - self.started if self.started is not None else 0.0)


def cap_seconds(seconds: float, cap: float = PHASE_CAP_S) -> tuple[float, bool]:
    """(seconds capped at `cap`, whether the cap was hit)."""
    return (min(seconds, cap), seconds >= cap)


# --- Records & persistence ----------------------------------------------------


def build_record(
    listing_id: str,
    *,
    blind: list[BlindLabel],
    unsure: list[UnsureLabel],
    seeds: list[GoldLabel],
    outcomes: list[SeedOutcome],
    dropped_blind: Iterable[str],
    removed_spans: Iterable[tuple[str, Span]],
    blind_seconds: float,
    reconcile_seconds: float,
    saved_at: str,
    previous: LogRecord | None = None,
    blind_mode: BlindMode = "free",
    checklist: list[ChecklistAnswer] = (),
    drop_reasons: list[DropReason] = (),
    human_spans: dict[str, list[Span]] | None = None,
) -> LogRecord:
    """A log record for one save. Re-edits keep the original blind pass and add reconcile time.

    Free mode: `blind` labels with spans, `dropped_blind` types. Checklist mode: `checklist`
    answers, `drop_reasons` (one per dropped Yes), spans for kept Yes in `human_spans`; the
    record's unsure entries are the checklist's open Unsure answers.
    """
    if previous is not None:
        blind = previous.blind_labels
        blind_mode = previous.blind_mode
        checklist = previous.checklist
        blind_seconds = previous.blind_seconds
        reconcile_seconds += previous.reconcile_seconds
    checklist = list(checklist)
    drop_reasons = list(drop_reasons) if blind_mode == "checklist" else []
    if blind_mode == "checklist":
        blind = []
        final, rejected = reconcile_checklist(
            listing_id, checklist, seeds, outcomes, drop_reasons, human_spans, removed_spans
        )
        dropped = sorted(dr.defect_id for dr in drop_reasons)
        unsure = checklist_unsure(listing_id, checklist, [g.defect_id for g in final])
    else:
        checklist = []
        dropped = sorted(set(dropped_blind) & set(blind_by_type(blind)))
        final, rejected = reconcile(listing_id, blind, seeds, outcomes, dropped, removed_spans)
    b, b_cap = cap_seconds(blind_seconds)
    r, r_cap = cap_seconds(reconcile_seconds)
    return LogRecord(
        listing_id=listing_id,
        blind_mode=blind_mode,
        checklist=checklist,
        drop_reasons=drop_reasons,
        blind_labels=blind,
        unsure=unsure,
        seeds=seeds,
        seed_outcomes=outcomes,
        dropped_blind=dropped,
        final_labels=final,
        rejected=rejected,
        blind_seconds=round(b, 1),
        reconcile_seconds=round(r, 1),
        time_capped=b_cap or r_cap or (previous is not None and previous.time_capped),
        edited=previous is not None,
        saved_at=saved_at,
    )


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def latest_records(labels_dir: Path) -> dict[str, LogRecord]:
    """Latest log record per listing (later lines win)."""
    return {r["listing_id"]: LogRecord(**r) for r in read_jsonl(labels_dir / LOG)}


def calibration_ids(labels_dir: Path, n: int = CALIBRATION_N) -> list[str]:
    """The first `n` listings ever saved, in first-save order (re-edits don't change this).

    Calibration listings stay in gold, but are excluded from the blind human baseline:
    the annotator was still learning the tool and the taxonomy on them.
    """
    seen: list[str] = []
    for row in read_jsonl(labels_dir / LOG):
        if row["listing_id"] not in seen:
            seen.append(row["listing_id"])
            if len(seen) == n:
                break
    return seen


def baseline(stats: dict) -> dict:
    """The blind human baseline part of `compute_stats` output."""
    keys = (
        "listings_labeled",
        "blind_recall",
        "blind_precision",
        "median_seconds_per_listing",
        "median_blind_seconds",
        "median_reconcile_seconds",
        "time_capped_records",
    )
    per_type = {
        d: {
            k: t[k]
            for k in (
                "confirmed_seeds",
                "blind_recall",
                "blind_labels",
                "blind_unsure",
                "blind_precision",
            )
        }
        for d, t in stats["per_type"].items()
    }
    return {**{k: stats[k] for k in keys}, "per_type": per_type}


def _write_atomic(path: Path, rows: Iterable[BaseModel]) -> None:
    text = "".join(r.model_dump_json() + "\n" for r in rows)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def rebuild_outputs(labels_dir: Path, records: dict[str, LogRecord]) -> None:
    """Rewrite gold / rejected_seeds / unsure from the latest records (sorted by listing id)."""
    recs = [records[k] for k in sorted(records)]
    gold = [g for r in recs for g in r.final_labels]
    if any(g.label_source == "seeded" for g in gold):
        raise ValueError("gold.jsonl may hold only confirmed / added_by_human labels")
    _write_atomic(labels_dir / GOLD, gold)
    _write_atomic(labels_dir / REJECTED, [x for r in recs for x in r.rejected])
    _write_atomic(labels_dir / UNSURE, [u for r in recs for u in r.unsure])


def save_record(labels_dir: Path, record: LogRecord) -> dict[str, LogRecord]:
    """Append the record to the log, then rebuild the derived files. Returns latest records."""
    labels_dir.mkdir(parents=True, exist_ok=True)
    with (labels_dir / LOG).open("a", encoding="utf-8") as fh:
        fh.write(record.model_dump_json() + "\n")
    records = latest_records(labels_dir)
    rebuild_outputs(labels_dir, records)
    return records


def load_seeds(path: Path) -> dict[str, list[GoldLabel]]:
    by: dict[str, list[GoldLabel]] = defaultdict(list)
    for row in read_jsonl(path):
        by[row["listing_id"]].append(GoldLabel(**row))
    return dict(by)


def load_listings(path: Path) -> dict[str, Listing]:
    return {d["id"]: Listing(**d) for d in read_jsonl(path)}


# --- Cheat sheet --------------------------------------------------------------


def cheat_sheet(guide_text: str) -> str:
    """The '## Cheat sheet' section of labeling_guide.md (up to the next '---')."""
    m = re.search(r"^## Cheat sheet\n(.*?)^---", guide_text, re.MULTILINE | re.DOTALL)
    if not m:
        raise ValueError("labeling_guide.md has no '## Cheat sheet' section")
    return m.group(1).strip()


def defect_hints(guide_text: str) -> dict[str, tuple[str, str]]:
    """D-id → (short name, one-line test) from the cheat-sheet table."""
    out = {}
    for line in cheat_sheet(guide_text).splitlines():
        m = re.match(r"\|\s*\*\*(D\d)\*\*\s*([^|]+)\|([^|]+)\|", line)
        if m:
            name = m.group(2).strip()
            test = re.sub(r"\*\*|`", "", m.group(3)).strip()
            out[m.group(1)] = (name, test)
    missing = set(DEFECT_IDS) - set(out)
    if missing:
        raise ValueError(f"cheat sheet has no row for {sorted(missing)}")
    return out


# --- Stats --------------------------------------------------------------------


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 3) if den else None


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def compute_stats(records: Iterable[LogRecord]) -> dict:
    """Human-baseline and dataset stats over the latest record per listing."""
    recs = list(records)
    seeds, rejected, retyped, confirmed, blind_hit = (Counter() for _ in range(5))
    human_added, blind_all, blind_kept, blind_dropped = (Counter() for _ in range(4))
    blind_unsure: Counter = Counter()
    drop_why: dict[str, Counter] = {d: Counter() for d in DEFECT_IDS}
    for r in recs:
        blind = blind_claims(r)
        for a in r.checklist:
            blind_unsure[a.defect_id] += a.answer == "unsure"
        reasons = {dr.defect_id: dr.reason for dr in r.drop_reasons}
        for d in r.dropped_blind:
            drop_why[d][reasons.get(d, "unrecorded")] += 1
        final_types = {g.defect_id for g in r.final_labels}
        for o in r.seed_outcomes:
            seeds[o.defect_id] += 1
            if o.action == "confirm":
                confirmed[o.defect_id] += 1
                blind_hit[o.defect_id] += o.defect_id in blind
            else:
                rejected[o.defect_id] += 1
                retyped[o.defect_id] += o.action == "retype"
        for g in r.final_labels:
            human_added[g.defect_id] += g.label_source == "added_by_human"
        for d in blind:
            blind_all[d] += 1
            kept = d in final_types and d not in r.dropped_blind
            blind_kept[d] += kept
            blind_dropped[d] += not kept
    per_type = {
        d: {
            "seeds": seeds[d],
            "rejected": rejected[d],
            "retyped": retyped[d],
            "rejection_rate": _rate(rejected[d], seeds[d]),
            "human_added": human_added[d],
            "confirmed_seeds": confirmed[d],
            "blind_recall": _rate(blind_hit[d], confirmed[d]),
            "blind_labels": blind_all[d],
            "blind_dropped_after_reveal": blind_dropped[d],
            "drop_reasons": {k: drop_why[d][k] for k in [*DROP_REASONS, "unrecorded"]},
            "blind_unsure": blind_unsure[d],
            "blind_precision": _rate(blind_kept[d], blind_all[d]),
        }
        for d in DEFECT_IDS
    }
    uncapped = [r for r in recs if not r.time_capped]
    return {
        "listings_labeled": len(recs),
        "checklist_listings": sum(r.blind_mode == "checklist" for r in recs),
        "seeds": sum(seeds.values()),
        "rejected": sum(rejected.values()),
        "retyped": sum(retyped.values()),
        "rejection_rate": _rate(sum(rejected.values()), sum(seeds.values())),
        "human_added": sum(human_added.values()),
        "blind_recall": _rate(sum(blind_hit.values()), sum(confirmed.values())),
        "blind_precision": _rate(sum(blind_kept.values()), sum(blind_all.values())),
        "unsure": sum(len(r.unsure) for r in recs),
        "time_capped_records": len(recs) - len(uncapped),
        "median_seconds_per_listing": _median(
            [r.blind_seconds + r.reconcile_seconds for r in uncapped]
        ),
        "median_blind_seconds": _median([r.blind_seconds for r in uncapped]),
        "median_reconcile_seconds": _median([r.reconcile_seconds for r in uncapped]),
        "per_type": per_type,
    }
