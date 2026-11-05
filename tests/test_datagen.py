import json
import random
import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from wanderlint.cost import CostTracker, Price, Pricing
from wanderlint.datagen.checks import find_issues, span_present, trap_present
from wanderlint.datagen.inject import (
    apply_llm_edit,
    apply_rule_variant,
    inject_rule,
    llm_edit_for,
    verify_injection,
)
from wanderlint.datagen.plan import DefectSpec, ListingSpec, PlanConfig, TrapSpec, plan_specs
from wanderlint.datagen.run import (
    GenSettings,
    check_context,
    render_prompts,
    run_all,
    write_outputs,
)
from wanderlint.datagen.vocab import (
    ACTIVITIES,
    AUDIENCE_TAGS,
    CATEGORY_MISMATCH,
    CONTACTS,
    HARD_CASES,
    OUT_OF_VOCAB_CATEGORIES,
    OUT_OF_VOCAB_TAGS,
    TRAP_KINDS,
    WRONG_TAGS,
)
from wanderlint.llm import LLMClient
from wanderlint.models import Listing, Span, Taxonomy

ROOT = Path(__file__).resolve().parents[1]
TAXONOMY = Taxonomy.model_validate(
    yaml.safe_load((ROOT / "taxonomy.yaml").read_text(encoding="utf-8"))
)
CONFIG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
CTX = check_context(TAXONOMY)
CATEGORIES = CTX.categories
PLAN_CFG = PlanConfig.from_config(CONFIG["datagen"])


def clean_en() -> Listing:
    """A clean en-GB listing that also contains every trap kind."""
    return Listing(
        id="t-en",
        locale="en-GB",
        title="LEGOLAND® Discovery Centre Tickets with VIP Entry",
        short_description="Build, play and explore at the indoor LEGO world in Berlin.",
        description=(
            "Build, play and explore at the indoor LEGO world in Berlin. Your visit starts at "
            "2pm with a short welcome. Children can try the miniature city and the 4D cinema. "
            "Allergies: please tell us when booking (write N/A if none apply to your group)."
        ),
        duration="2 hours",
        meeting_point="Main entrance, Potsdamer Straße 4, 10785 Berlin",
        inclusions=["Entry ticket", "4D cinema show"],
        exclusions=["Food and drinks"],
        cancellation_policy="Free cancellation up to 24 hours before; later, a fee of €15 applies.",
        category="Attractions & Tickets",
        tags=["Family-friendly", "Kids activities", "Indoor"],
        attributes={"instant_confirmation": True},
    )


def clean_de() -> Listing:
    return Listing(
        id="t-de",
        locale="de-DE",
        title="Kajaktour auf der Isar mit Picknick",
        short_description="Gemütliche Kajaktour auf der Isar für Einsteiger.",
        description=(
            "Gemütliche Kajaktour auf der Isar für Einsteiger. Wir starten um 14 Uhr am Bootshaus "
            "und paddeln gemeinsam flussabwärts. Unterwegs gibt es ein kleines Picknick am Ufer. "
            "Mindestalter: n. a. – Kinder ab Schulalter sind mit Begleitung willkommen."
        ),
        duration="3 Stunden",
        meeting_point="Bootshaus, Wittelsbacherstraße 1, 80469 München",
        inclusions=["Kajak und Paddel", "Schwimmweste", "Picknick", "Guide"],
        exclusions=["Anreise"],
        cancellation_policy="Kostenlose Stornierung bis 48 Stunden vorher, danach 25,00 € Gebühr.",
        category="Water Activities",
        tags=["Kayaking", "Outdoor", "Nature"],
        attributes={"small_group": True, "max_group_size": "8"},
    )


# --- planner ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def specs150() -> list[ListingSpec]:
    return plan_specs(150, 42, PLAN_CFG, CATEGORIES)


def test_plan_splits_and_quotas(specs150) -> None:
    assert len(specs150) == 150
    assert Counter(s.locale for s in specs150) == {"en-GB": 105, "de-DE": 45}
    assert sum(s.is_clean for s in specs150) == 60
    per_type = Counter(d.defect_id for s in specs150 for d in s.defects)
    assert all(per_type[d] >= 15 for d in ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"])
    for s in specs150:
        if not s.is_clean:
            ids = [d.defect_id for d in s.defects]
            assert 1 <= len(ids) <= 3 and len(set(ids)) == len(ids)


def test_plan_hard_cases(specs150) -> None:
    defects = [d for s in specs150 for d in s.defects]
    hard = [d for d in defects if d.hard_case]
    assert 0.15 <= len(hard) / len(defects) <= 0.25
    assert {d.hard_case for d in hard} == set(HARD_CASES)
    for d in hard:
        assert (d.defect_id, d.variant) == HARD_CASES[d.hard_case]


def test_plan_is_deterministic_and_uses_vocabularies(specs150) -> None:
    assert plan_specs(150, 42, PLAN_CFG, CATEGORIES) == specs150
    assert plan_specs(150, 43, PLAN_CFG, CATEGORIES) != specs150
    for s in specs150:
        assert s.category in CTX.categories
        assert set(s.tags) <= set(CTX.tags)


def test_pilot_plan_covers_every_type() -> None:
    specs = plan_specs(10, 42, PLAN_CFG, CATEGORIES)
    assert {d.defect_id for s in specs for d in s.defects} == {f"D{i}" for i in range(1, 9)}


def test_static_vocab_is_consistent() -> None:
    assert set(ACTIVITIES) == set(CATEGORIES)
    for acts in ACTIVITIES.values():
        for _, tags, _ in acts:
            assert set(tags) <= set(CTX.tags)
    assert not set(OUT_OF_VOCAB_TAGS) & set(CTX.tags)
    assert not set(OUT_OF_VOCAB_CATEGORIES) & set(CTX.categories)
    for cat, wrong in CATEGORY_MISMATCH.items():
        assert cat not in wrong and set(wrong) <= set(CTX.categories)
    for wrong in WRONG_TAGS.values():
        assert set(wrong) <= set(CTX.tags)


# --- (a) traps also in defective listings --------------------------------------------


def test_traps_in_clean_and_defective_listings(specs150) -> None:
    clean = [s for s in specs150 if s.is_clean]
    defective = [s for s in specs150 if not s.is_clean]
    assert all(1 <= len(s.traps) <= 2 for s in clean)
    share = sum(1 for s in defective if s.traps) / len(defective)
    assert 0.25 <= share <= 0.35
    # Trap presence must never signal "clean": every kind appears on both sides.
    for kind in TRAP_KINDS:
        assert any(t.kind == kind for s in clean for t in s.traps), kind
        assert any(t.kind == kind for s in defective for t in s.traps), kind


# --- checks ------------------------------------------------------------------------


@pytest.mark.parametrize("listing", [clean_en(), clean_de()], ids=["en", "de"])
def test_clean_fixtures_have_no_issues_and_all_traps(listing: Listing) -> None:
    assert find_issues(listing, CTX) == []
    detail = "2pm" if listing.locale == "en-GB" else "14 Uhr"
    fee = "€25" if listing.locale == "en-GB" else "25,00 €"
    for kind, d in [
        ("title_acronym_or_registered", "registered" if listing.locale == "en-GB" else "acronym"),
        ("fee_in_cancellation", fee),
        ("na_in_sentence", ""),
        ("locale_variant", detail),
        ("short_in_description", ""),
        ("street_address_meeting_point", ""),
    ]:
        if listing.locale == "de-DE" and kind == "title_acronym_or_registered":
            continue
        assert trap_present(kind, d, listing, CTX), kind


@pytest.mark.parametrize(
    ("text", "flagged"),
    [
        ("Unser Rundgang beginnt um 14:00 am Ufer der Promenade.", False),
        ("Treffpunkt um 10 Uhr am Bahnhof.", False),
        ("Start um 2:30 pm am Hafen.", True),
        ("Start um 10am.", True),
        ("Beginn: 9 am.", True),
    ],
)
def test_de_time_check_ignores_german_am(text: str, flagged: bool) -> None:
    lst = clean_de().model_copy(update={"description": text})
    assert ("D5" in {i.defect_id for i in find_issues(lst, CTX)}) is flagged


def test_d1_d8_hard_case_is_d1_only() -> None:
    lst = clean_en().model_copy(update={"meeting_point": "TBD!!!"})
    types = {i.defect_id for i in find_issues(lst, CTX)}
    assert types == {"D1"}


def test_price_in_description_other_format_is_d6_only() -> None:
    lst = clean_de().model_copy(
        update={"description": clean_de().description + " Schon ab €39 pro Person."}
    )
    assert {i.defect_id for i in find_issues(lst, CTX)} == {"D6"}


# --- (b) deterministic injectors: varied templates and positions ----------------------


def _rule_inject(listing: Listing, defect_id: str, variant: str, seed: int):
    return apply_rule_variant(
        listing, defect_id, variant, random.Random(seed), set(), city="Berlin", alcohol=False
    )


@pytest.mark.parametrize("make", [clean_en, clean_de], ids=["en", "de"])
def test_contact_formats_fields_and_positions_vary(make) -> None:
    listing = make()
    formats, fields, positions = set(), set(), set()
    for variant in ["contact_phone", "contact_email", "contact_url", "contact_handle"]:
        for seed in range(25):
            inj = _rule_inject(listing, "D6", variant, seed)
            formats.add(inj.detail["format"])
            fields.add(inj.detail["field"])
            positions.add(inj.detail["position"])
            assert all(span_present(inj.listing, s) for s in inj.spans)
            assert "D6" in {i.defect_id for i in find_issues(inj.listing, CTX)}
    assert len(formats) >= 4  # phone formats, email, URL, social handle
    assert len(fields) >= 3
    assert positions == {"start", "middle", "end"}
    assert len(CONTACTS[listing.locale]) == 4


def test_placeholders_and_d8_patterns_vary() -> None:
    values = {_rule_inject(clean_en(), "D1", "placeholder", s).spans[0].span for s in range(40)} | {
        _rule_inject(clean_de(), "D1", "placeholder", s).spans[0].span for s in range(40)
    }
    assert len(values) >= 8
    punct = {_rule_inject(clean_en(), "D8", "repeated_punct", s).detail["punct"] for s in range(40)}
    assert len(punct) >= 3
    emoji_pos = {
        _rule_inject(clean_en(), "D8", "emoji_title", s).detail["position"] for s in range(40)
    }
    assert len(emoji_pos) >= 2
    caps = {_rule_inject(clean_en(), "D8", "caps_title", s).detail["style"] for s in range(40)}
    assert caps == {"all", "all_but_last"}


@pytest.mark.parametrize(
    ("defect_id", "variant"),
    [
        ("D1", "empty"),
        ("D1", "placeholder"),
        ("D1", "empty_list"),
        ("D1", "lorem"),
        ("D1", "placeholder_punct"),
        ("D6", "contact_phone"),
        ("D6", "contact_email"),
        ("D6", "contact_url"),
        ("D6", "contact_handle"),
        ("D7", "tag_out_of_vocab"),
        ("D7", "category_out_of_vocab"),
        ("D7", "category_mismatch"),
        ("D7", "tag_mismatch"),
        ("D8", "caps_title"),
        ("D8", "repeated_punct"),
        ("D8", "emoji_title"),
        ("D8", "long_title"),
        ("D8", "dup_sentence"),
    ],
)
@pytest.mark.parametrize("make", [clean_en, clean_de], ids=["en", "de"])
def test_rule_injection_is_exact_and_only_that_type(make, defect_id, variant) -> None:
    listing = make()
    spec = DefectSpec(defect_id=defect_id, method="rule", variant=variant)
    inj, errors = inject_rule(
        listing, spec, "k", set(), [], set(), CTX, city="München", alcohol=False
    )
    assert inj is not None, errors
    assert all(span_present(inj.listing, s) for s in inj.spans)
    types = {i.defect_id for i in find_issues(inj.listing, CTX)}
    assert types <= {defect_id}


@pytest.mark.parametrize("make", [clean_en, clean_de], ids=["en", "de"])
@pytest.mark.parametrize("variant", ["placeholder", "placeholder_punct"])
def test_placeholders_never_in_title_and_references_only_below(make, variant) -> None:
    for seed in range(60):
        inj = _rule_inject(make(), "D1", variant, seed)
        (span,) = inj.spans
        assert span.field != "title"
        if span.span.rstrip("!?.").strip().lower() in {"see above", "siehe oben"}:
            assert span.field in {"duration", "meeting_point", "cancellation_policy"}


def test_registered_trap_only_in_brand_plausible_categories(specs150) -> None:
    from wanderlint.datagen.vocab import REGISTERED_OK_CATEGORIES

    traps = [
        (s.category, t.detail)
        for s in specs150
        for t in s.traps
        if t.kind == "title_acronym_or_registered"
    ]
    assert any(d == "registered" for _, d in traps)
    assert all(c in REGISTERED_OK_CATEGORIES for c, d in traps if d == "registered")


def test_llm_edit_rejects_overlong_span() -> None:
    listing = clean_en()
    spec = DefectSpec(defect_id="D6", method="llm", variant="price")
    edit = llm_edit_for(spec, listing, random.Random(0))
    sentence = "Entry ticket and an audio guide are included for only €39 per person today."
    data = {
        "text_edits": [
            {"field": "short_description", "new_value": f"{listing.short_description} {sentence}"}
        ],
        "spans": [{"field": "short_description", "span": sentence}],
    }
    _, errors = apply_llm_edit(listing, edit, data, spec.variant)
    assert any("shortest excerpt" in e for e in errors)
    data["spans"] = [{"field": "short_description", "span": "only €39"}]
    _, errors = apply_llm_edit(listing, edit, data, spec.variant)
    assert errors == []


@pytest.mark.parametrize("version", ["datagen_v1", "datagen_v2"])
def test_prompts_render(version: str) -> None:
    p = render_prompts(ROOT / "prompts", version, TAXONOMY)
    assert "{{" not in p.listing_system and p.inject_system.strip()


def test_alcohol_listing_gets_audience_tag() -> None:
    lst = clean_en().model_copy(update={"tags": ["Wine"]})
    inj = apply_rule_variant(
        lst, "D7", "tag_mismatch", random.Random(1), set(), city="x", alcohol=True
    )
    assert inj.spans[0].span in AUDIENCE_TAGS


def test_rule_injector_respects_taken_fields() -> None:
    spec = DefectSpec(defect_id="D8", method="rule", variant="caps_title")
    listing = clean_en()
    prior = [*[]]
    inj, _ = inject_rule(listing, spec, "k", {"title"}, prior, set(), CTX, city="x", alcohol=False)
    assert inj is not None and inj.variant != "caps_title"
    assert inj.listing.title == listing.title


# --- LLM edit verification ----------------------------------------------------------


def test_llm_edit_rejects_non_verbatim_span_and_hints() -> None:
    listing = clean_en()
    spec = DefectSpec(defect_id="D3", method="llm", variant="unsupported_claim")
    edit = llm_edit_for(spec, listing, random.Random(0))
    data = {
        "text_edits": [
            {
                "field": "description",
                "new_value": listing.description + " Skip-the-line entry (intentional defect).",
            }
        ],
        "spans": [{"field": "description", "span": "Skip the line"}],
    }
    inj, errors = apply_llm_edit(listing, edit, data, spec.variant)
    errors += verify_injection(listing, inj, "D3", [], set(), CTX, detectable=False)
    assert any("hints" in e for e in errors)
    assert any("not verbatim" in e for e in errors)


def test_llm_edit_hard_d2_d3_sets_contradicting_attribute() -> None:
    listing = clean_de()
    spec = DefectSpec(defect_id="D2", method="llm", variant="claim_vs_attribute", hard_case="D2/D3")
    edit = llm_edit_for(spec, listing, random.Random(3))
    (key,) = edit.set_attributes
    new_desc = listing.description + " Exklusiv für Ihre Gruppe."
    data = {
        "text_edits": [{"field": "description", "new_value": new_desc}],
        "spans": [{"field": "description", "span": "Exklusiv für Ihre Gruppe."}],
    }
    inj, errors = apply_llm_edit(listing, edit, data, spec.variant)
    errors += verify_injection(listing, inj, "D2", [], set(), CTX, detectable=False)
    assert errors == []
    assert inj.listing.attributes[key] is False
    assert any(s.field == f"attributes.{key}" and s.span == "false" for s in inj.spans)


def test_llm_edit_rejects_non_editable_field() -> None:
    listing = clean_en()
    spec = DefectSpec(defect_id="D4", method="llm", variant="food_promised")
    edit = llm_edit_for(spec, listing, random.Random(0))
    data = {"text_edits": [{"field": "cancellation_policy", "new_value": "x"}], "spans": []}
    _, errors = apply_llm_edit(listing, edit, data, spec.variant)
    assert any("non-editable" in e for e in errors)


# --- end-to-end without the API --------------------------------------------------------


class FakeGenerator:
    """Returns a clean listing satisfying the requested traps; no injection calls expected."""

    def __init__(self) -> None:
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        user = kwargs["messages"][0]["content"]
        spec = json.loads(re.search(r"SPEC\n(\{.*?\n\})", user, re.S).group(1))
        base = clean_en() if spec["locale"] == "en-GB" else clean_de()
        data = base.model_dump(exclude={"id", "locale", "category", "tags", "attributes"})
        for trap in spec["traps"]:
            if trap["kind"] == "fee_in_cancellation":
                data["cancellation_policy"] = f"Late cancellation fee: {trap['detail']}."
            if trap["kind"] == "locale_variant":
                data["meeting_point"] += f", {trap['detail']}"
            if trap["kind"] == "title_acronym_or_registered":
                data["title"] = (
                    "LEGOLAND® Discovery Centre" if trap["detail"] == "registered" else "VIP Tour"
                )
        data["attributes"] = []
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(data))],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=500),
            stop_reason="end_turn",
        )


def test_run_all_writes_listings_labels_and_meta_with_traps(tmp_path) -> None:
    specs = [
        ListingSpec(
            id="gen-0001", locale="en-GB", city="Berlin", country="Germany",
            category="Attractions & Tickets", tags=["Indoor"], activity="a", alcohol=False,
            length="short", quality="plain",
            traps=[TrapSpec(kind="fee_in_cancellation", detail="EUR 25")], defects=[],
        ),
        ListingSpec(
            id="gen-0002", locale="de-DE", city="München", country="Germany",
            category="Water Activities", tags=["Kayaking"], activity="a", alcohol=False,
            length="short", quality="average",
            traps=[TrapSpec(kind="locale_variant", detail="14:00")],
            defects=[
                DefectSpec(defect_id="D6", method="rule", variant="contact_email"),
                DefectSpec(defect_id="D8", method="rule", variant="emoji_title"),
            ],
        ),
    ]  # fmt: skip
    fake = FakeGenerator()
    pricing = Pricing(0.9, {"m": Price(2.0, 10.0, 0.2, 2.5)})
    llm = LLMClient(
        client=SimpleNamespace(messages=fake),
        cache_dir=tmp_path / "cache",
        tracker=CostTracker(pricing=pricing, cap_eur=1.0),
        taxonomy_version=1,
    )
    prompts = render_prompts(ROOT / "prompts", "datagen_v1", TAXONOMY)
    assert "{{" not in prompts.listing_system
    settings = GenSettings(
        model="m", prompt_version="v", effort="low", max_tokens=100, max_retries=1
    )
    results, failures, stop = run_all(specs, llm, prompts, settings, CTX, seed=42)
    assert stop is None and fake.calls == 2, failures
    paths = {k: tmp_path / f"{k}.jsonl" for k in ["listings", "seeded", "meta", "failures"]}
    write_outputs(results, failures, paths)

    listings = [Listing.model_validate_json(x) for x in paths["listings"].read_text().splitlines()]
    labels = [json.loads(x) for x in paths["seeded"].read_text().splitlines()]
    meta = {m["listing_id"]: m for m in map(json.loads, paths["meta"].read_text().splitlines())}
    assert len(listings) == 2
    assert {(lab["listing_id"], lab["defect_id"]) for lab in labels} == {
        ("gen-0002", "D6"),
        ("gen-0002", "D8"),
    }
    assert all(lab["label_source"] == "seeded" for lab in labels)
    by_id = {x.id: x for x in listings}
    for lab in labels:
        for s in lab["spans"]:
            assert span_present(by_id[lab["listing_id"]], Span(**s))
    assert meta["gen-0001"]["traps"] == [{"kind": "fee_in_cancellation", "detail": "EUR 25"}]
    assert meta["gen-0002"]["traps"] == [{"kind": "locale_variant", "detail": "14:00"}]
    assert {a["defect_id"] for a in meta["gen-0002"]["seeded"]} == {"D6", "D8"}
