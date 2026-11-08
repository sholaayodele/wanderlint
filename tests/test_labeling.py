"""Labeling logic: span validation, seed matching/reconcile, saving, timing, stats. No UI."""

import json
from pathlib import Path

import pytest

from wanderlint.labeling import (
    GOLD,
    LOG,
    PHASE_CAP_S,
    REJECTED,
    UNSURE,
    BlindLabel,
    PhaseTimer,
    ReconcileError,
    SeedOutcome,
    build_record,
    cap_seconds,
    cheat_sheet,
    compute_stats,
    defect_hints,
    field_choices,
    labeling_order,
    latest_records,
    match_seeds,
    reconcile,
    resume_index,
    save_record,
    validate_span,
)
from wanderlint.models import GoldLabel, Listing, Span, UnsureLabel

ROOT = Path(__file__).resolve().parents[1]
LID = "gen-0001"


def listing() -> Listing:
    return Listing(
        id=LID,
        locale="en-GB",
        title="Santorini airport transfer to Fira",
        short_description="Private car transfer from the airport to Fira.",
        description="Your driver waits in arrivals. The car is wheelchair accessible.",
        duration="30 minutes",
        meeting_point="",
        inclusions=["Private transfer", "Luggage help"],
        exclusions=[],
        cancellation_policy="Free cancellation up to 24 hours before.",
        category="Transfers & Transport",
        tags=["Private"],
        attributes={"wheelchair_accessible": False, "pickup": "hotel"},
    )


def sp(field: str, span: str) -> Span:
    return Span(field=field, span=span)


def seed(d: str, *spans: Span) -> GoldLabel:
    return GoldLabel(listing_id=LID, defect_id=d, spans=list(spans), label_source="seeded")


def blind(d: str, *spans: Span) -> BlindLabel:
    return BlindLabel(defect_id=d, spans=list(spans))


ACCESS = sp("description", "The car is wheelchair accessible")
ATTR = sp("attributes.wheelchair_accessible", "false")
EMPTY_MP = sp("meeting_point", "")


# --- Span validation ----------------------------------------------------------


@pytest.mark.parametrize(
    ("span", "ok"),
    [
        (ACCESS, True),
        (sp("description", "the car is wheelchair accessible"), False),  # case matters
        (sp("description", "The car is  wheelchair"), False),  # whitespace matters
        (sp("description", " The car"), False),  # leading whitespace
        (sp("title", "Fira"), True),
        (sp("title", "Fira town"), False),
        (EMPTY_MP, True),  # empty field
        (sp("description", ""), False),  # field not empty
        (sp("inclusions", "Luggage help"), True),
        (sp("inclusions", "Luggage"), False),  # list fields: exact item
        (sp("exclusions", ""), True),  # empty list
        (sp("tags", "Private"), True),
        (sp("category", "Transfers & Transport"), True),
        (sp("category", "Transfers"), False),
        (ATTR, True),
        (sp("attributes.wheelchair_accessible", "False"), False),
        (sp("attributes.pickup", "hotel"), True),
        (sp("attributes.missing", "x"), False),
        (sp("locale", "en-GB"), False),  # not a labelable field
    ],
)
def test_validate_span(span: Span, ok: bool) -> None:
    assert (validate_span(listing(), span) is None) is ok


def test_field_choices() -> None:
    lst = listing()
    assert field_choices(lst, "description") is None
    assert field_choices(lst, "inclusions") == ["Private transfer", "Luggage help"]
    assert field_choices(lst, "exclusions") == [""]
    assert field_choices(lst, "attributes.wheelchair_accessible") == ["false"]
    for f in ("inclusions", "exclusions", "category", "attributes.pickup"):
        for value in field_choices(lst, f):
            assert validate_span(lst, sp(f, value)) is None


# --- Matching & reconcile -------------------------------------------------------


def test_match_seeds_by_type() -> None:
    pairs = match_seeds(
        [blind("D2", ACCESS), blind("D1", EMPTY_MP), blind("D2", ATTR)],
        [seed("D2", ACCESS, ATTR), seed("D3", sp("title", "Fira"))],
    )
    assert list(pairs) == ["D1", "D2", "D3"]
    assert pairs["D1"] == (None, [EMPTY_MP])
    assert pairs["D2"][0].defect_id == "D2" and pairs["D2"][1] == [ACCESS, ATTR]
    assert pairs["D3"][1] is None


def test_confirm_merges_blind_spans_and_unmatched_blind_is_added() -> None:
    extra = sp("short_description", "Private car transfer")
    final, rejected = reconcile(
        LID,
        [blind("D2", ACCESS, extra), blind("D1", EMPTY_MP)],
        [seed("D2", ACCESS, ATTR)],
        [SeedOutcome(defect_id="D2", action="confirm")],
    )
    assert rejected == []
    assert [(g.defect_id, g.label_source) for g in final] == [
        ("D1", "added_by_human"),
        ("D2", "confirmed"),
    ]
    assert final[1].spans == [ACCESS, ATTR, extra]  # seed spans first, no duplicate


def test_reject_requires_reason() -> None:
    args = (LID, [], [seed("D3", sp("title", "Fira"))])
    with pytest.raises(ReconcileError, match="reason"):
        reconcile(*args, [SeedOutcome(defect_id="D3", action="reject", reason="  ")])
    final, rejected = reconcile(
        *args, [SeedOutcome(defect_id="D3", action="reject", reason="Not a claim")]
    )
    assert final == []
    assert [(r.defect_id, r.reason) for r in rejected] == [("D3", "Not a claim")]


def test_every_seed_needs_a_decision() -> None:
    with pytest.raises(ReconcileError, match="D3"):
        reconcile(LID, [], [seed("D3", sp("title", "Fira"))], [])


def test_retype_rejects_seed_and_adds_new_type_merged_with_blind() -> None:
    final, rejected = reconcile(
        LID,
        [blind("D2", ATTR)],
        [seed("D3", ACCESS)],
        [SeedOutcome(defect_id="D3", action="retype", new_defect_id="D2", reason="attr false")],
    )
    assert [(r.defect_id, r.reason) for r in rejected] == [("D3", "retyped to D2: attr false")]
    assert [(g.defect_id, g.label_source, g.spans) for g in final] == [
        ("D2", "added_by_human", [ATTR, ACCESS])
    ]


def test_retype_into_confirmed_type_merges() -> None:
    final, _ = reconcile(
        LID,
        [],
        [seed("D2", ATTR), seed("D3", ACCESS)],
        [
            SeedOutcome(defect_id="D2", action="confirm"),
            SeedOutcome(defect_id="D3", action="retype", new_defect_id="D2"),
        ],
    )
    assert [(g.defect_id, g.label_source, g.spans) for g in final] == [
        ("D2", "confirmed", [ATTR, ACCESS])
    ]


def test_retype_needs_a_different_type() -> None:
    with pytest.raises(ReconcileError, match="different type"):
        reconcile(
            LID,
            [],
            [seed("D3", ACCESS)],
            [SeedOutcome(defect_id="D3", action="retype", new_defect_id="D3")],
        )


def test_rejecting_seed_while_keeping_blind_label_is_contradictory() -> None:
    seeds = [seed("D3", ACCESS)]
    reject = [SeedOutcome(defect_id="D3", action="reject", reason="no")]
    with pytest.raises(ReconcileError, match="drop it or confirm"):
        reconcile(LID, [blind("D3", ACCESS)], seeds, reject)
    final, rejected = reconcile(LID, [blind("D3", ACCESS)], seeds, reject, dropped_blind=["D3"])
    assert final == [] and len(rejected) == 1


def test_removed_spans_and_empty_label_error() -> None:
    seeds = [seed("D2", ACCESS, ATTR)]
    ok = [SeedOutcome(defect_id="D2", action="confirm")]
    final, _ = reconcile(LID, [], seeds, ok, removed_spans=[("D2", ACCESS)])
    assert final[0].spans == [ATTR]
    with pytest.raises(ReconcileError, match="every span"):
        reconcile(LID, [], seeds, ok, removed_spans=[("D2", ACCESS), ("D2", ATTR)])


# --- Order, timing ----------------------------------------------------------------


def test_labeling_order_is_fixed_and_not_plan_order() -> None:
    ids = [f"gen-{i:04d}" for i in range(1, 151)]
    order = labeling_order(reversed(ids))
    assert order == labeling_order(ids)  # independent of input order
    assert sorted(order) == ids and order != ids
    assert resume_index(order, order[:3]) == 3
    assert resume_index(order, [order[0], order[2]]) == 1
    assert resume_index(order, order) == len(order)


def test_phase_timer_pause_resume() -> None:
    t = PhaseTimer()
    t.start(100.0)
    t.start(105.0)  # idempotent
    assert t.elapsed(130.0) == 30.0
    t.pause(130.0)
    assert t.elapsed(500.0) == 30.0  # paused time doesn't count
    t.start(500.0)
    assert t.elapsed(510.0) == 40.0
    assert cap_seconds(599.0) == (599.0, False)
    assert cap_seconds(PHASE_CAP_S + 50) == (PHASE_CAP_S, True)


# --- Saving -----------------------------------------------------------------------


def record(**kw):
    defaults = dict(
        blind=[blind("D1", EMPTY_MP), blind("D3", ACCESS)],
        unsure=[UnsureLabel(listing_id=LID, candidate_defect_ids=["D7"], reason="tag?")],
        seeds=[seed("D2", ACCESS, ATTR), seed("D3", sp("title", "Fira"))],
        outcomes=[
            SeedOutcome(defect_id="D2", action="confirm"),
            SeedOutcome(defect_id="D3", action="reject", reason="Not a claim"),
        ],
        dropped_blind=["D3"],
        removed_spans=[],
        blind_seconds=40.0,
        reconcile_seconds=20.0,
        saved_at="2026-11-08T20:00:00+00:00",
    )
    defaults.update(kw)
    return build_record(LID, **defaults)


def _rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_save_writes_gold_rejected_unsure_and_log(tmp_path: Path) -> None:
    save_record(tmp_path, record())
    gold = _rows(tmp_path / GOLD)
    assert [(g["defect_id"], g["label_source"]) for g in gold] == [
        ("D1", "added_by_human"),
        ("D2", "confirmed"),
    ]
    assert all(GoldLabel(**g) for g in gold)
    assert _rows(tmp_path / REJECTED) == [
        {"listing_id": LID, "defect_id": "D3", "reason": "Not a claim"}
    ]
    assert _rows(tmp_path / UNSURE)[0]["candidate_defect_ids"] == ["D7"]
    (log,) = _rows(tmp_path / LOG)
    assert log["blind_seconds"] == 40.0 and log["reconcile_seconds"] == 20.0
    assert log["dropped_blind"] == ["D3"] and not log["time_capped"] and not log["edited"]
    assert {o["defect_id"]: o["action"] for o in log["seed_outcomes"]} == {
        "D2": "confirm",
        "D3": "reject",
    }


def test_resave_keeps_latest_version_without_duplicates(tmp_path: Path) -> None:
    first = record()
    save_record(tmp_path, first)
    # a re-edit passes the previous record: blind pass and blind time are kept
    second = record(
        blind=[],
        outcomes=[
            SeedOutcome(defect_id="D2", action="reject", reason="attr is a typo"),
            SeedOutcome(defect_id="D3", action="reject", reason="Not a claim"),
        ],
        blind_seconds=999.0,
        reconcile_seconds=15.0,
        previous=first,
    )
    records = save_record(tmp_path, second)
    assert len(_rows(tmp_path / LOG)) == 2  # append-only
    assert list(records) == [LID]
    latest = latest_records(tmp_path)[LID]
    assert latest.edited and latest.blind_labels == first.blind_labels
    assert latest.blind_seconds == 40.0 and latest.reconcile_seconds == 35.0
    gold = _rows(tmp_path / GOLD)
    assert [g["defect_id"] for g in gold] == ["D1"]  # D2 now rejected, nothing duplicated
    assert sorted(r["defect_id"] for r in _rows(tmp_path / REJECTED)) == ["D2", "D3"]
    assert len(_rows(tmp_path / UNSURE)) == 1


def test_time_cap_marks_record(tmp_path: Path) -> None:
    rec = record(blind_seconds=PHASE_CAP_S + 120)
    assert rec.time_capped and rec.blind_seconds == PHASE_CAP_S
    again = record(blind=[], previous=rec, reconcile_seconds=5.0)
    assert again.time_capped  # a capped blind pass stays capped after re-edit


def test_gold_never_holds_seeded_rows(tmp_path: Path) -> None:
    save_record(tmp_path, record())
    assert {g["label_source"] for g in _rows(tmp_path / GOLD)} <= {"confirmed", "added_by_human"}


# --- Stats ------------------------------------------------------------------------


def test_compute_stats() -> None:
    a = record()  # D2 confirmed (not found blind), D3 rejected, blind D1 added, blind D3 dropped
    b = build_record(
        "gen-0002",
        blind=[blind("D5", sp("description", "x"))],
        unsure=[],
        seeds=[
            GoldLabel(
                listing_id="gen-0002",
                defect_id="D5",
                spans=[sp("description", "x")],
                label_source="seeded",
            )
        ],
        outcomes=[SeedOutcome(defect_id="D5", action="confirm")],
        dropped_blind=[],
        removed_spans=[],
        blind_seconds=PHASE_CAP_S + 1,
        reconcile_seconds=10.0,
        saved_at="t",
    )
    st = compute_stats([a, b])
    assert st["seeds"] == 3 and st["rejected"] == 1 and st["rejection_rate"] == 0.333
    assert st["blind_recall"] == 0.5  # D5 found blind, D2 not
    assert st["blind_precision"] == 0.667  # D1, D5 kept; D3 dropped
    assert st["per_type"]["D3"]["blind_dropped_after_reveal"] == 1
    assert st["per_type"]["D3"]["blind_precision"] == 0.0
    assert st["per_type"]["D1"]["human_added"] == 1
    assert st["per_type"]["D2"]["blind_recall"] == 0.0
    assert st["unsure"] == 1
    assert st["time_capped_records"] == 1
    assert st["median_seconds_per_listing"] == 60.0  # capped record excluded


# --- Cheat sheet & leakage --------------------------------------------------------


def test_cheat_sheet_and_hints_from_guide() -> None:
    guide = (ROOT / "labeling_guide.md").read_text(encoding="utf-8")
    sheet = cheat_sheet(guide)
    assert sheet.startswith("Unit =") and "**D8**" in sheet
    hints = defect_hints(guide)
    assert sorted(hints) == [f"D{i}" for i in range(1, 9)]
    assert hints["D3"][0].startswith("Unsupported claim")


def test_labeling_code_never_reads_meta() -> None:
    for path in ("scripts/label_app.py", "src/wanderlint/labeling.py"):
        src = (ROOT / path).read_text(encoding="utf-8")
        assert "output_meta" not in src and 'generated_meta.jsonl"' not in src
        assert "hard_case" not in src and "traps" not in src
