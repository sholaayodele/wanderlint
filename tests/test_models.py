import pytest
from pydantic import ValidationError

from wanderlint.models import Defect, GoldLabel, RejectedSeed, UnsureLabel

TWO_SPANS = [
    {"field": "title", "span": "3-Hour Tapas Walking Tour"},
    {"field": "duration", "span": "2 hours"},
]


def test_gold_label_accepts_multiple_spans() -> None:
    label = GoldLabel(listing_id="L1", defect_id="D2", spans=TWO_SPANS, label_source="seeded")
    assert [s.field for s in label.spans] == ["title", "duration"]


def test_gold_label_requires_at_least_one_span() -> None:
    with pytest.raises(ValidationError):
        GoldLabel(listing_id="L1", defect_id="D2", spans=[], label_source="seeded")


def test_gold_label_allows_empty_span_for_missing_field() -> None:
    label = GoldLabel(
        listing_id="L1",
        defect_id="D1",
        spans=[{"field": "meeting_point", "span": ""}],
        label_source="confirmed",
    )
    assert label.spans[0].span == ""


def test_defect_requires_spans_and_bounded_confidence() -> None:
    base = dict(
        defect_id="D2",
        severity="critical",
        evidence="Title says 3 hours, duration says 2 hours.",
        message="Duration mismatch.",
        source="llm",
    )
    Defect(**base, spans=TWO_SPANS, confidence=0.9)
    with pytest.raises(ValidationError):
        Defect(**base, spans=[], confidence=0.9)
    with pytest.raises(ValidationError):
        Defect(**base, spans=TWO_SPANS, confidence=1.5)


def test_unsure_label_requires_candidates_and_reason() -> None:
    UnsureLabel(listing_id="L1", candidate_defect_ids=["D2", "D3"], reason="Claim vs attribute?")
    with pytest.raises(ValidationError):
        UnsureLabel(listing_id="L1", candidate_defect_ids=[], reason="x")
    with pytest.raises(ValidationError):
        UnsureLabel(listing_id="L1", candidate_defect_ids=["D2"], reason="")


def test_rejected_seed_requires_reason() -> None:
    RejectedSeed(listing_id="L1", defect_id="D3", reason="Claim is backed by attribute.")
    with pytest.raises(ValidationError):
        RejectedSeed(listing_id="L1", defect_id="D3", reason="")
