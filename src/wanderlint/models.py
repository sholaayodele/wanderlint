"""Core pydantic schemas: listings, defects, gold labels, and the taxonomy itself."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DefectId = Literal["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"]
Severity = Literal["critical", "major", "minor"]
Detection = Literal["rule", "llm", "both"]
Locale = Literal["en-GB", "de-DE"]
DefectSource = Literal["rule", "llm"]
LabelSource = Literal["seeded", "confirmed", "added_by_human"]
Route = Literal["auto_pass", "auto_flag", "human_review"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Listings, defects, labels ---------------------------------------------


class Listing(_Strict):
    id: str
    locale: Locale
    title: str
    short_description: str
    description: str
    duration: str
    meeting_point: str
    inclusions: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    cancellation_policy: str
    category: str
    tags: list[str] = Field(default_factory=list)
    attributes: dict[str, bool | str] = Field(default_factory=dict)


class Span(_Strict):
    """A verbatim excerpt of one listing field. Empty `span` = the field is empty/absent."""

    field: str
    span: str


class Defect(_Strict):
    defect_id: DefectId
    severity: Severity
    spans: list[Span] = Field(min_length=1)
    evidence: str
    message: str
    suggested_fix: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    source: DefectSource


class GoldLabel(_Strict):
    """A labelled defect. Frozen gold.jsonl holds only confirmed / added_by_human."""

    listing_id: str
    defect_id: DefectId
    spans: list[Span] = Field(min_length=1)
    label_source: LabelSource


class UnsureLabel(_Strict):
    """A candidate defect the annotator could not decide. Stored in unsure.jsonl, never gold."""

    listing_id: str
    candidate_defect_ids: list[DefectId] = Field(min_length=1)
    reason: str = Field(min_length=1)


class RejectedSeed(_Strict):
    """A seeded defect the annotator rejected. Logged in rejected_seeds.jsonl, never gold."""

    listing_id: str
    defect_id: DefectId
    reason: str = Field(min_length=1)


class CheckResult(_Strict):
    """Output of checking one listing. Placeholder; extended in M2/M3."""

    listing_id: str
    defects: list[Defect] = Field(default_factory=list)
    route: Route | None = None
    dropped_evidence_count: int = 0
    cost_eur: float = 0.0
    latency_s: float = 0.0


class EvalReport(_Strict):
    """Eval run summary. Placeholder; fields defined in M2."""

    split: Literal["dev", "test", "regression"]
    prompt_version: str
    model: str
    taxonomy_version: int
    n_listings: int


# --- Taxonomy (validates taxonomy.yaml) ------------------------------------


class TaxonomyExample(_Strict):
    locale: Locale
    field: str
    text: str
    note: str

    @field_validator("text", "note")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must be non-empty")
        return v


class TaxonomyExamples(_Strict):
    positive: list[TaxonomyExample]
    negative: list[TaxonomyExample]


class DefectType(_Strict):
    id: DefectId
    name: str
    definition: str
    severity: Severity
    detection: Detection
    decision_rules: list[str] = Field(min_length=1)
    examples: TaxonomyExamples
    required_fields: list[str] | None = None
    placeholder_values: list[str] | None = None
    placeholder_substrings: list[str] | None = None
    caps_acronym_whitelist: list[str] | None = None
    emoji_exclusions: list[str] | None = None
    categories: list[str] | None = None
    tags: list[str] | None = None
    locale_conventions: dict[Locale, dict[str, list[str]]] | None = None


class Taxonomy(_Strict):
    version: int
    defects: list[DefectType]

    def by_id(self) -> dict[str, DefectType]:
        return {d.id: d for d in self.defects}
