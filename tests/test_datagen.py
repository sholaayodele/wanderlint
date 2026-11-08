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
    _D4_ITEMS,
    apply_llm_edit,
    apply_rule_variant,
    claim_keys,
    d4_items,
    inject_rule,
    llm_edit_for,
    same_sentence_problems,
    trailing_clause_problems,
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


@pytest.mark.parametrize(
    "version", ["datagen_v1", "datagen_v2", "datagen_v3", "datagen_v4", "datagen_v5"]
)
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
    claim = f"Als {edit.required_span_terms[0]} buchbar."
    new_desc = f"{listing.description} {claim}"
    data = {
        "text_edits": [{"field": "description", "new_value": new_desc}],
        "spans": [{"field": "description", "span": claim}],
    }
    inj, errors = apply_llm_edit(listing, edit, data, spec.variant)
    errors += verify_injection(listing, inj, "D2", [], set(), CTX, detectable=False)
    assert errors == []
    assert inj.listing.attributes[key] is False
    assert any(s.field == f"attributes.{key}" and s.span == "false" for s in inj.spans)


def test_llm_edit_rejects_non_editable_field() -> None:
    listing = clean_en().model_copy(update={"exclusions": []})
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
                    "LEGOLAND® Discovery Centre"
                    if trap["detail"] == "registered"
                    else f"{trap['detail'].removeprefix('acronym:').split('|')[0]} Tour"
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


# --- datagen v3 ---------------------------------------------------------------------


def test_acronym_trap_lists_claim_free_acronyms(specs150) -> None:
    from wanderlint.datagen.vocab import CLAIM_ACRONYMS, TRAP_ACRONYMS

    assert set(TRAP_ACRONYMS) == set(TAXONOMY.by_id()["D7"].categories)
    details = [
        (s.category, t.detail)
        for s in specs150
        for t in s.traps
        if t.kind == "title_acronym_or_registered" and t.detail != "registered"
    ]
    assert details
    for category, detail in details:
        options = detail.removeprefix("acronym:").split("|")
        assert options == TRAP_ACRONYMS[category]
        assert not set(options) & CLAIM_ACRONYMS
        assert all(len(a) <= 4 for a in options)  # never counted by the caps rule


@pytest.mark.parametrize(
    ("title", "present"),
    [
        ("SUP Yoga on the Alster", True),
        ("Stand-up paddling on the Alster", False),
        ("UNESCO Wetlands SUP Tour", False),  # a claim acronym spoils the trap
        ("VIP 4WD Safari", False),
        ("GPS Treasure Hunt", False),  # not in this category's list
    ],
)
def test_acronym_trap_requires_listed_acronym_and_no_claim(title: str, present: bool) -> None:
    listing = clean_en().model_copy(update={"title": title})
    detail = "acronym:4WD|SUP|MTB"
    assert trap_present("title_acronym_or_registered", detail, listing, CTX) is present


@pytest.mark.parametrize("variant", ["unsupported_claim", "claim_vs_attribute"])
@pytest.mark.parametrize("make", [clean_en, clean_de], ids=["en", "de"])
def test_claims_must_name_the_feature_explicitly(make, variant: str) -> None:
    listing = make()
    spec = DefectSpec(defect_id="D3", method="llm", variant=variant)
    for seed in range(6):
        edit = llm_edit_for(spec, listing, random.Random(seed))
        assert edit.required_span_terms and edit.required_span_terms[0] in edit.instruction
        vague = "A personal visit" if listing.locale == "en-GB" else "Ein persönlicher Besuch"
        explicit = edit.required_span_terms[0].capitalize() + " included"
        for claim, ok in [(vague, False), (explicit, True)]:
            data = {
                "text_edits": [
                    {"field": "description", "new_value": f"{listing.description} {claim}."}
                ],
                "spans": [{"field": "description", "span": claim}],
            }
            _, errors = apply_llm_edit(listing, edit, data, variant)
            assert any("explicitly" in e for e in errors) is not ok, (seed, claim)


def test_claim_spans_capped_at_eight_words() -> None:
    listing = clean_en().model_copy(update={"exclusions": []})
    spec = DefectSpec(defect_id="D4", method="llm", variant="drink_promised")
    edit = llm_edit_for(spec, listing, random.Random(0))
    for span, ok in [
        ("a hot drink and a glass of wine", True),  # 8 words
        ("warm up with a hot drink and a glass of wine", False),  # 10 words
    ]:
        data = {
            "text_edits": [
                {"field": "description", "new_value": f"{listing.description} You {span}."}
            ],
            "spans": [{"field": "description", "span": span}],
        }
        _, errors = apply_llm_edit(listing, edit, data, spec.variant)
        assert any("shortest excerpt" in e for e in errors) is not ok


# --- datagen v4 ---------------------------------------------------------------------


def test_group_claims_only_for_guided_products() -> None:
    keys = ["small_group", "skip_the_line", "private", "wheelchair_accessible"]
    ticket = clean_en()  # Attractions & Tickets, no guide
    assert claim_keys(ticket, keys) == ["skip_the_line", "wheelchair_accessible"]
    guided = clean_de()  # Water Activities with "Guide" in inclusions
    assert claim_keys(guided, keys) == keys
    no_guide = guided.model_copy(update={"inclusions": ["Kajak und Paddel", "Schwimmweste"]})
    assert "private" not in claim_keys(no_guide, keys)
    audio = no_guide.model_copy(update={"inclusions": ["Audio guide", "Exhibition guide"]})
    assert "private" not in claim_keys(audio, keys)
    museum = guided.model_copy(update={"category": "Museums & Exhibitions"})
    assert "private" not in claim_keys(museum, keys)
    for seed in range(20):
        for variant in ("unsupported_claim", "claim_vs_attribute"):
            spec = DefectSpec(defect_id="D3", method="llm", variant=variant)
            edit = llm_edit_for(spec, ticket, random.Random(seed))
            assert not {"private tour", "small group"} & set(edit.required_span_terms)
            assert all("guided" not in t for t in edit.required_span_terms)


def test_claim_must_not_add_a_receivable() -> None:
    listing = clean_en()
    spec = DefectSpec(defect_id="D3", method="llm", variant="unsupported_claim")
    edit = llm_edit_for(spec, listing, random.Random(1))
    term = edit.required_span_terms[0]
    for added, ok in [
        (f"Enjoy {term} access.", True),
        (f"Enjoy {term} access with a guided walk.", False),
        (f"Enjoy {term} access and a welcome drink.", False),
    ]:
        data = {
            "text_edits": [{"field": "description", "new_value": f"{listing.description} {added}"}],
            "spans": [{"field": "description", "span": f"{term} access"}],
        }
        _, errors = apply_llm_edit(listing, edit, data, spec.variant)
        assert any("customer receives" in e for e in errors) is not ok, added


@pytest.mark.parametrize(
    ("exclusions", "item", "allowed"),
    [
        (["Lunch and drinks"], "a glass of local wine", False),  # pilot v3 gen-0002
        (["Getränke"], "a welcome drink", False),
        (["Food and snacks"], "lunch", False),
        (["Hotel transfers"], "hotel pickup and drop-off", False),
        (["Gratuities"], "a glass of local wine", True),
        # pilot v5 gen-0002: a ferry ticket is transport, not admission
        (["Return high-speed ferry ticket from Tokyo"], "entry tickets to the main sight", True),
        (["Return high-speed ferry ticket from Tokyo"], "return transport", False),
    ],
)
def test_d4_item_must_not_overlap_listed_items(exclusions, item, allowed) -> None:
    listing = clean_de().model_copy(update={"exclusions": exclusions, "inclusions": ["Kajak"]})
    assert (item in d4_items(listing, [item])) is allowed


def test_d4_falls_back_when_every_item_overlaps() -> None:
    listing = clean_en()  # exclusions: "Food and drinks"
    for v in ("food_promised", "drink_promised"):
        assert d4_items(listing, _D4_ITEMS[v]) == []
    assert d4_items(listing, _D4_ITEMS["transport_promised"])
    # A span that overlaps a listed item is rejected even if the model chose it itself.
    listing = listing.model_copy(update={"exclusions": ["Lunch and drinks"]})
    spec = DefectSpec(defect_id="D4", method="llm", variant="transport_promised")
    edit = llm_edit_for(spec, listing, random.Random(0))
    data = {
        "text_edits": [
            {"field": "description", "new_value": f"{listing.description} Wine is on us."}
        ],
        "spans": [{"field": "description", "span": "Wine is on us"}],
    }
    _, errors = apply_llm_edit(listing, edit, data, spec.variant)
    assert any("overlaps the listed item" in e for e in errors)


def test_two_edits_in_one_field_need_different_sentences() -> None:
    text = (
        "Warm up with a glass of local wine, and you receive a souvenir photo. "
        "Snorkel over the reef.\n\nYou receive a souvenir photo of the day."
    )
    listing = clean_en().model_copy(update={"description": text})
    prior = [Span(field="description", span="a glass of local wine")]
    same = [Span(field="description", span="you receive a souvenir photo")]
    other = [Span(field="description", span="You receive a souvenir photo of the day")]
    assert same_sentence_problems(listing, same, prior)
    assert same_sentence_problems(listing, other, prior) == []
    # Different fields never clash.
    assert same_sentence_problems(listing, [Span(field="title", span="LEGOLAND®")], prior) == []


# --- datagen v5 ---------------------------------------------------------------------

_G2_BEFORE = (
    "Life jackets are provided, so the trip suits beginners as well as confident swimmers. "
    "Expect damselfish and wrasse.\n\n"
    "The trip finishes back at the harbour with time for questions."
)


@pytest.mark.parametrize(
    ("after", "span", "rejected"),
    [
        # pilot v4 gen-0002, both lines: a promise bolted onto an unchanged sentence.
        (
            _G2_BEFORE.replace(
                "confident swimmers.", "confident swimmers, and all entrance fees are included."
            ),
            "all entrance fees are included",
            True,
        ),
        (
            _G2_BEFORE.replace(
                "for questions.",
                "for questions, and each guest takes home a complimentary souvenir photo.",
            ),
            "a complimentary souvenir photo",
            True,
        ),
        # Same promise as its own sentence, or a rewritten sentence on that topic: fine.
        (
            _G2_BEFORE.replace(
                "for questions.",
                "for questions. Each guest takes home a complimentary souvenir photo.",
            ),
            "a complimentary souvenir photo",
            False,
        ),
        (
            _G2_BEFORE.replace(
                "The trip finishes back at the harbour with time for questions.",
                "Back at the harbour, each guest receives a complimentary souvenir photo.",
            ),
            "a complimentary souvenir photo",
            False,
        ),
    ],
)
def test_trailing_clause_seam_is_rejected(after: str, span: str, rejected: bool) -> None:
    before = clean_en().model_copy(update={"description": _G2_BEFORE})
    new = before.model_copy(update={"description": after})
    problems = trailing_clause_problems(before, new, [Span(field="description", span=span)])
    assert bool(problems) is rejected
    if rejected:
        assert "own sentence" in problems[0]


def test_trailing_clause_check_runs_in_verification() -> None:
    before = clean_en().model_copy(update={"description": _G2_BEFORE})
    after = _G2_BEFORE.replace(
        "confident swimmers.", "confident swimmers, and all entrance fees are included."
    )
    from wanderlint.datagen.inject import Injection

    inj = Injection(
        before.model_copy(update={"description": after}),
        [Span(field="description", span="all entrance fees are included")],
        "ticket_promised",
    )
    errors = verify_injection(before, inj, "D4", [], set(), CTX, detectable=False)
    assert any("appended to an existing sentence" in e for e in errors)


@pytest.mark.parametrize(
    "variant", ["time_format", "date_format", "number_format", "price", "price_other_format"]
)
def test_format_variants_span_the_token_only(variant: str) -> None:
    listing = clean_en()
    spec = DefectSpec(defect_id="D5", method="llm", variant=variant)
    edit = llm_edit_for(spec, listing, random.Random(0))
    assert edit.max_span_words == 3 and "itself" in edit.instruction
    promo = llm_edit_for(
        DefectSpec(defect_id="D6", method="llm", variant="promo"), listing, random.Random(0)
    )
    assert promo.max_span_words == 8


# --- datagen v5 fixes: shared receivables, acronym trap, D4 hard-case fallback, provenance ---

from wanderlint.datagen.audit import audit_listing  # noqa: E402
from wanderlint.datagen.checks import added_receivables  # noqa: E402
from wanderlint.datagen.run import fallback_variants, meta_row  # noqa: E402


def _wine_tasting() -> Listing:
    return clean_de().model_copy(
        update={
            "title": "Weinverkostung in Budapest",
            "description": "Probieren Sie ungarische Weine in einem historischen Keller.",
            "inclusions": ["Verkostung von fünf Weinen", "Käseteller", "Guide"],
            "category": "Food & Drink",
        }
    )


def test_claim_mentioning_an_included_item_is_not_a_new_receivable() -> None:
    # pilot full run gen-0008: "die offizielle Weinverkostung" on a wine tasting was rejected.
    listing = _wine_tasting()
    after = f"{listing.description} Dies ist die offizielle Weinverkostung der Stadt."
    terms = ["offizielle", "offiziell"]
    assert (
        added_receivables(
            listing.description,
            after,
            inclusions=listing.inclusions,
            strip=terms,
            claim_key="official",
        )
        == []
    )
    # Without wine in inclusions the same sentence would promise drinks.
    assert added_receivables(listing.description, after, strip=terms, claim_key="official") == [
        "drinks"
    ]
    # The generator's guard uses the same function.
    spec = DefectSpec(defect_id="D3", method="llm", variant="unsupported_claim")
    edit = next(
        e
        for seed in range(50)
        if (e := llm_edit_for(spec, listing, random.Random(seed))).claim_key == "official"
    )
    data = {
        "text_edits": [{"field": "description", "new_value": after}],
        "spans": [{"field": "description", "span": "die offizielle Weinverkostung"}],
    }
    _, errors = apply_llm_edit(listing, edit, data, spec.variant)
    assert not any("customer receives" in e for e in errors)


def test_skip_the_line_may_mention_entry_but_not_other_receivables() -> None:
    listing = clean_en().model_copy(update={"inclusions": ["Audio guide"]})
    before = listing.description
    entry = f"{before} Enjoy skip-the-line entry to the main hall."
    lunch = f"{before} Enjoy skip-the-line entry and lunch in the café."
    terms = ["skip-the-line", "skip the line"]
    assert added_receivables(before, entry, strip=terms, claim_key="skip_the_line") == []
    assert added_receivables(before, lunch, strip=terms, claim_key="skip_the_line") == ["food"]
    # The tickets exemption belongs to skip-the-line only.
    assert added_receivables(before, entry, strip=["official"], claim_key="official") == ["tickets"]


def test_audit_ignores_receivables_from_other_seeds() -> None:
    # full run gen-0075: a D2 claim and a D4 food promise in the same description.
    pre = clean_de().model_copy(update={"inclusions": ["Kajak und Paddel", "Guide"]})
    claim, promise = "Als Privattour buchbar.", "Ein Mittagessen am Ufer ist inbegriffen."
    final = pre.model_copy(
        update={
            "description": f"{pre.description} {claim} {promise}",
            "attributes": {**pre.attributes, "private": False},
        }
    )
    seeded = [
        {"defect_id": "D2", "variant": "claim_vs_attribute", "method": "llm",
         "detail": {"claim_key": "private"}},
        {"defect_id": "D4", "variant": "food_promised", "method": "llm", "detail": {}},
    ]  # fmt: skip
    labels = [
        {"defect_id": "D2", "spans": [{"field": "description", "span": "Als Privattour buchbar"},
                                      {"field": "attributes.private", "span": "false"}]},
        {"defect_id": "D4", "spans": [{"field": "description", "span": "Ein Mittagessen am Ufer"}]},
    ]  # fmt: skip
    assert audit_listing(final, pre, seeded, labels)["claim_product"] == []
    # If the claim itself carried the food, the audit still catches it.
    labels[1]["spans"] = [{"field": "description", "span": "ist inbegriffen"}]
    assert audit_listing(final, pre, seeded, labels)["claim_product"]


@pytest.mark.parametrize(
    ("title", "present"),
    [
        ("Wildlife spotting by 4WD near Athens", True),
        ("Wildtierbeobachtung im Geländewagen (4WD) bei Budapest", True),
        ("Wildtierbeobachtung im 4WD-Geländewagen bei Budapest", False),
        ("Beginner climbing with GPS-guided route", False),
    ],
)
def test_acronym_trap_needs_a_standalone_token(title: str, present: bool) -> None:
    listing = clean_en().model_copy(update={"title": title})
    assert trap_present("title_acronym_or_registered", "acronym:4WD|GPS", listing, CTX) is present


def test_acronym_trap_instruction_asks_for_standalone_word() -> None:
    from wanderlint.datagen.run import trap_instruction

    text = trap_instruction(
        TrapSpec(kind="title_acronym_or_registered", detail="acronym:SUP"), "de-DE"
    )
    assert "standalone word" in text and "SUP-Kurs" in text


def test_d4_hard_case_falls_back_other_hard_cases_do_not() -> None:
    hard_d4 = DefectSpec(defect_id="D4", method="llm", variant="pickup_promised", hard_case="D3/D4")
    assert fallback_variants(hard_d4)[0] == "pickup_promised"
    assert len(fallback_variants(hard_d4)) > 1
    hard_d2 = DefectSpec(
        defect_id="D2", method="llm", variant="claim_vs_attribute", hard_case="D2/D3"
    )
    assert fallback_variants(hard_d2) == ["claim_vs_attribute"]


def test_meta_row_records_provenance() -> None:
    from wanderlint.datagen.run import ListingResult

    spec = plan_specs(10, 42, PlanConfig(), TAXONOMY.by_id()["D7"].categories)[0]
    prov = {"git_commit": "abc123", "git_dirty": False, "prompt_version": "datagen_v5",
            "taxonomy_version": 1}  # fmt: skip
    row = meta_row(ListingResult(spec=spec, listing=clean_en()), prov)
    assert {k: row[k] for k in prov} == prov
