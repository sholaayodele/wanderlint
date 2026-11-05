"""Defect injection: deterministic rule injectors and LLM edit specs + verification.

Every injection returns exact spans (labeling_guide §2). An injection is only
accepted if its spans are verbatim in the edited listing, earlier spans are
untouched, and the heuristic checks show no defect types beyond the seeded ones.
"""

from __future__ import annotations

import random
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from wanderlint.datagen.checks import CheckContext, find_issues, rstrip_punct, span_present
from wanderlint.datagen.plan import DefectSpec
from wanderlint.datagen.vocab import (
    AUDIENCE_TAGS,
    CATEGORY_MISMATCH,
    CONTACT_FIELDS,
    CONTACT_PHRASES,
    CONTACTS,
    EMOJI,
    LIST_FIELDS,
    LONG_TITLE_SUFFIXES,
    LOREM,
    OUT_OF_VOCAB_CATEGORIES,
    OUT_OF_VOCAB_TAGS,
    PLACEHOLDER_FIELDS,
    PLACEHOLDERS,
    PLACEHOLDERS_PUNCT,
    REFERENCE_PLACEHOLDER_FIELDS,
    REFERENCE_PLACEHOLDERS,
    REPEATED_PUNCT,
    REQUIRED_TEXT_FIELDS,
    TEXT_FIELDS,
    VARIANTS,
    WRONG_TAGS,
)
from wanderlint.models import Listing, Span


class CannotInject(Exception):
    """This variant can't be applied to this listing (e.g. its target field is taken)."""


@dataclass
class Injection:
    listing: Listing
    spans: list[Span]
    variant: str
    detail: dict[str, Any] = field(default_factory=dict)


# Heuristically detectable variants: the injected type must show up in find_issues.
# Content-level D7 mismatches (vocab-valid values) are LLM-detectable only.
_UNDETECTABLE_RULE_VARIANTS = {"category_mismatch", "tag_mismatch"}
_DETECTABLE_LLM_VARIANTS = {
    "time_format",
    "date_format",
    "currency_in_cancellation",
    "number_format",
    "promo",
    "price",
    "price_other_format",
}


def slug(city: str) -> str:
    ascii_ = unicodedata.normalize("NFKD", city).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", ascii_.lower())


def split_sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def insert_sentence(text: str, sentence: str, position: str) -> str:
    """Insert a sentence at start / middle / end of a text field, between sentences."""
    if not text.strip():
        return sentence
    if position == "start":
        return f"{sentence} {text.lstrip()}"
    if position == "end":
        return f"{text.rstrip()} {sentence}"
    if "\n\n" in text:
        paras = text.split("\n\n")
        k = len(paras) // 2
        paras[k] = f"{paras[k].rstrip()} {sentence}"
        return "\n\n".join(paras)
    parts = split_sentences(text)
    if len(parts) < 2:
        return f"{text.rstrip()} {sentence}"
    parts.insert(len(parts) // 2, sentence)
    return " ".join(parts)


def _choose_field(fields: list[str], avoid: set[str], rng: random.Random) -> str:
    options = [f for f in fields if f not in avoid]
    if not options:
        raise CannotInject(f"all candidate fields taken: {fields}")
    return rng.choice(options)


# --- rule injectors ----------------------------------------------------------


def _d1(listing: Listing, variant: str, rng: random.Random, avoid: set[str]) -> Injection:
    loc = listing.locale
    if variant == "empty_list":
        if "inclusions" in avoid:
            raise CannotInject("inclusions taken")
        return Injection(
            listing.model_copy(update={"inclusions": []}),
            [Span(field="inclusions", span="")],
            variant,
        )
    if variant == "lorem":
        f = _choose_field(["description", "short_description", "meeting_point"], avoid, rng)
        text = rng.choice(LOREM)
        pos = rng.choice(["start", "middle", "end"])
        new = insert_sentence(getattr(listing, f), text, pos)
        return Injection(
            listing.model_copy(update={f: new}),
            [Span(field=f, span=text)],
            variant,
            {"field": f, "position": pos},
        )
    if variant == "empty":
        f = _choose_field(REQUIRED_TEXT_FIELDS, avoid, rng)
        value = rng.choice(["", "", " "])
        return Injection(
            listing.model_copy(update={f: value}), [Span(field=f, span="")], variant, {"field": f}
        )
    pool = PLACEHOLDERS_PUNCT[loc] if variant == "placeholder_punct" else PLACEHOLDERS[loc]
    value = rng.choice(pool)
    is_reference = rstrip_punct(value).strip().lower() in REFERENCE_PLACEHOLDERS
    f = _choose_field(
        REFERENCE_PLACEHOLDER_FIELDS if is_reference else PLACEHOLDER_FIELDS, avoid, rng
    )
    return Injection(
        listing.model_copy(update={f: value}),
        [Span(field=f, span=value)],
        variant,
        {"field": f, "value": value},
    )


def _d6(
    listing: Listing, variant: str, rng: random.Random, avoid: set[str], city: str
) -> Injection:
    loc = listing.locale
    fmt_name, template = rng.choice(CONTACTS[loc][variant])
    contact = template.format(slug=slug(city))
    phrase = rng.choice(CONTACT_PHRASES[loc][variant]).format(c=contact)
    f = _choose_field(CONTACT_FIELDS, avoid, rng)
    pos = rng.choice(["start", "middle", "end"])
    new = insert_sentence(getattr(listing, f), phrase, pos)
    return Injection(
        listing.model_copy(update={f: new}),
        [Span(field=f, span=contact)],
        variant,
        {"format": fmt_name, "field": f, "position": pos},
    )


def _d7(
    listing: Listing, variant: str, rng: random.Random, avoid: set[str], alcohol: bool
) -> Injection:
    tags = list(listing.tags)
    if variant in ("category_out_of_vocab", "category_mismatch"):
        if "category" in avoid:
            raise CannotInject("category taken")
        pool = (
            OUT_OF_VOCAB_CATEGORIES
            if variant == "category_out_of_vocab"
            else CATEGORY_MISMATCH[listing.category]
        )
        value = rng.choice(pool)
        return Injection(
            listing.model_copy(update={"category": value}),
            [Span(field="category", span=value)],
            variant,
        )
    if variant == "tag_out_of_vocab":
        value = rng.choice(OUT_OF_VOCAB_TAGS)
    else:  # tag_mismatch
        pool = AUDIENCE_TAGS if alcohol else WRONG_TAGS[listing.category]
        pool = [t for t in pool if t not in tags]
        if not pool:
            raise CannotInject("no wrong tag left")
        value = rng.choice(pool)
    if tags and rng.random() < 0.5:
        tags[rng.randrange(len(tags))] = value
    else:
        tags.insert(rng.randint(0, len(tags)), value)
    return Injection(
        listing.model_copy(update={"tags": tags}), [Span(field="tags", span=value)], variant
    )


def _d8(listing: Listing, variant: str, rng: random.Random, avoid: set[str]) -> Injection:
    loc = listing.locale
    title = listing.title
    if variant in ("caps_title", "emoji_title", "long_title") and "title" in avoid:
        raise CannotInject("title taken")
    if variant == "caps_title":
        words = title.split()
        style = rng.choice(["all", "all_but_last", "all"])
        if style == "all_but_last" and len(words) >= 4:
            new = " ".join([w.upper() for w in words[:-1]] + [words[-1]])
        else:
            new = title.upper()
        return Injection(
            listing.model_copy(update={"title": new}),
            [Span(field="title", span=new)],
            variant,
            {"style": style},
        )
    if variant == "emoji_title":
        e = rng.choice(EMOJI)
        pos = rng.choice(["start", "end", "end", "middle"])
        words = title.split()
        if pos == "start":
            new = f"{e} {title}"
        elif pos == "middle" and len(words) >= 3:
            k = len(words) // 2
            new = " ".join(words[:k] + [e] + words[k:])
        else:
            new = f"{title} {e}"
        return Injection(
            listing.model_copy(update={"title": new}),
            [Span(field="title", span=e)],
            variant,
            {"position": pos},
        )
    if variant == "long_title":
        suffixes = list(LONG_TITLE_SUFFIXES[loc])
        rng.shuffle(suffixes)
        new = title.rstrip()
        for s in suffixes:
            new += s
            if len(new) > 80:
                break
        return Injection(
            listing.model_copy(update={"title": new}),
            [Span(field="title", span=new)],
            variant,
            {"length": len(new)},
        )
    if variant == "repeated_punct":
        punct = rng.choice(REPEATED_PUNCT)
        f = _choose_field(["title", "short_description", "description"], avoid, rng)
        text = getattr(listing, f).rstrip()
        if f == "title":
            new = re.sub(r"[.!?]*$", "", text) + punct
            last = new.split()[-1]
        else:
            parts = split_sentences(text)
            if not parts:
                raise CannotInject(f"{f} is empty")
            s = rng.choice(parts)
            s_new = re.sub(r"[.!?]*$", "", s) + punct
            new = text.replace(s, s_new, 1)
            last = s_new.split()[-1]
        return Injection(
            listing.model_copy(update={f: new}),
            [Span(field=f, span=last)],
            variant,
            {"field": f, "punct": punct},
        )
    if variant == "dup_sentence":
        f = _choose_field(["description", "short_description"], avoid, rng)
        text = getattr(listing, f)
        candidates = [s for s in split_sentences(text) if len(s) >= 25 and "\n" not in s]
        if not candidates:
            raise CannotInject("no sentence to duplicate")
        s = rng.choice(candidates)
        where = rng.choice(["after", "end", "paragraph"])
        if where == "after":
            new = text.replace(s, f"{s} {s}", 1)
        elif where == "end":
            new = f"{text.rstrip()} {s}"
        else:
            new = f"{text.rstrip()}\n\n{s}"
        return Injection(
            listing.model_copy(update={f: new}),
            [Span(field=f, span=s)],
            variant,
            {"field": f, "where": where},
        )
    raise ValueError(f"unknown D8 variant {variant!r}")


def apply_rule_variant(
    listing: Listing,
    defect_id: str,
    variant: str,
    rng: random.Random,
    avoid: set[str],
    *,
    city: str,
    alcohol: bool,
) -> Injection:
    if defect_id == "D1":
        return _d1(listing, variant, rng, avoid)
    if defect_id == "D6":
        return _d6(listing, variant, rng, avoid, city)
    if defect_id == "D7":
        return _d7(listing, variant, rng, avoid, alcohol)
    if defect_id == "D8":
        return _d8(listing, variant, rng, avoid)
    raise ValueError(f"no rule injector for {defect_id}")


def verify_injection(
    before: Listing,
    inj: Injection,
    defect_id: str,
    prior_spans: list[Span],
    allowed_types: set[str],
    ctx: CheckContext,
    *,
    detectable: bool,
) -> list[str]:
    """Return a list of problems (empty = accepted)."""
    errors: list[str] = []
    if not inj.spans:
        errors.append("no spans")
    for s in inj.spans:
        if not span_present(inj.listing, s):
            errors.append(f"span not verbatim in {s.field}: {s.span!r}")
    for s in prior_spans:
        if not span_present(inj.listing, s):
            errors.append(f"earlier span destroyed in {s.field}: {s.span!r}")
    issue_types = {i.defect_id for i in find_issues(inj.listing, ctx)}
    unexpected = issue_types - allowed_types - {defect_id}
    if unexpected:
        detail = [i for i in find_issues(inj.listing, ctx) if i.defect_id in unexpected]
        errors.append(
            f"introduced other defect types: {[(i.defect_id, i.field, i.detail) for i in detail]}"
        )
    if detectable and defect_id not in issue_types:
        errors.append(f"{defect_id} not detectable after injection")
    if inj.listing == before:
        errors.append("listing unchanged")
    return errors


def inject_rule(
    listing: Listing,
    spec: DefectSpec,
    seed_key: str,
    avoid: set[str],
    prior_spans: list[Span],
    allowed_types: set[str],
    ctx: CheckContext,
    *,
    city: str,
    alcohol: bool,
) -> tuple[Injection | None, list[str]]:
    """Try the planned variant, then the type's other rule variants (hard cases: no fallback)."""
    variants = [spec.variant]
    if spec.hard_case is None:
        variants += [v for m, v in VARIANTS[spec.defect_id] if m == "rule" and v != spec.variant]
    errors: list[str] = []
    for variant in variants:
        for attempt in range(3):
            rng = random.Random(f"{seed_key}|{spec.defect_id}|{variant}|{attempt}")
            try:
                inj = apply_rule_variant(
                    listing, spec.defect_id, variant, rng, avoid, city=city, alcohol=alcohol
                )
            except CannotInject as e:
                errors.append(f"{variant}: {e}")
                break
            problems = verify_injection(
                listing,
                inj,
                spec.defect_id,
                prior_spans,
                allowed_types,
                ctx,
                detectable=variant not in _UNDETECTABLE_RULE_VARIANTS,
            )
            if not problems:
                return inj, errors
            errors += [f"{variant}#{attempt}: {p}" for p in problems]
    return None, errors


# --- LLM edit specs -------------------------------------------------------------

OTHER_LANGUAGE = {"de-DE": "English", "en-GB": "German"}
LANGUAGE = {"de-DE": "German", "en-GB": "British English"}

_CLAIMS = {
    "small_group": ("small group size (e.g. at most 8 people)", ["small_group", "max_group_size"]),
    "skip_the_line": ("skip-the-line / priority entry", ["skip_the_line"]),
    "private": ("a private tour (only the customer's own party)", ["private"]),
    "wheelchair_accessible": ("full wheelchair accessibility", ["wheelchair_accessible"]),
    "official": ("being the official tour of the attraction or city", []),
    "number_one": ("being the No. 1 / best-rated experience in the city", []),
}
_D4_ITEMS = {
    "food_promised": ["lunch", "a three-course dinner", "breakfast", "local snacks along the way"],
    "drink_promised": ["a glass of local wine", "a welcome drink", "drinks", "coffee and pastries"],
    "ticket_promised": [
        "entry tickets to the main sight",
        "admission to the museum",
        "all entrance fees",
    ],
    "transport_promised": ["return transport", "a boat transfer", "train tickets for the trip"],
    "pickup_promised": ["hotel pickup and drop-off", "pickup from your hotel"],
}
_EXCLUSION_PROMISES = ["lunch", "drinks", "hotel pickup", "entry tickets", "a souvenir photo"]
_EXAMPLES = {
    "time_format": {
        "de-DE": ["2:30 pm", "10am", "7.30 p.m."],
        "en-GB": ["14:30 Uhr", "10 Uhr", "19.30 Uhr"],
    },
    "date_format": {
        "de-DE": ["24 December 2026", "1 May 2027"],
        "en-GB": ["24.12.2026", "01.05.2027"],
    },
    "currency_in_cancellation": {"de-DE": ["€15", "€10.50"], "en-GB": ["15 €", "10,50 €"]},
    "number_format": {
        "de-DE": ["1,250 metres", "2,300.5 m"],
        "en-GB": ["1.250,5 metres", "2.300,5 m"],
    },
    "promo": {
        "de-DE": [
            "Nur heute 15 % Rabatt",
            "Sonderangebot für Frühbucher",
            "Jetzt mit Gutscheincode SOMMER buchen",
        ],
        "en-GB": [
            "20% off this week only",
            "Special offer: early-bird discount",
            "Use promo code SUMMER at checkout",
        ],
    },
    "price": {"de-DE": ["ab 39 €", "nur 49,00 €"], "en-GB": ["from €39", "only €49"]},
    "price_other_format": {"de-DE": ["only €39", "from €49"], "en-GB": ["ab 39 €", "nur 49,00 €"]},
}


@dataclass(frozen=True)
class LLMEdit:
    instruction: str
    editable: list[str]
    required_span_fields: list[str] = field(default_factory=list)
    set_attributes: dict[str, bool | str] = field(default_factory=dict)
    delete_attributes: list[str] = field(default_factory=list)
    extra_spans: list[Span] = field(default_factory=list)
    max_span_words: int = 12


def llm_edit_for(spec: DefectSpec, listing: Listing, rng: random.Random) -> LLMEdit:
    """Concrete instruction for one LLM-injected defect. Deterministic given rng."""
    loc = listing.locale
    v = spec.variant
    copy_fields = ["title", "short_description", "description"]
    if v == "duration_mismatch":
        return LLMEdit(
            instruction=(
                "State a duration in the title, short_description or description that clearly "
                f"contradicts the `duration` field (currently {listing.duration!r}). "
                "Do not change the duration field."
            ),
            editable=copy_fields,
            extra_spans=[Span(field="duration", span=listing.duration)],
        )
    if v == "included_but_paid_extra":
        if not listing.inclusions:
            raise CannotInject("no inclusions")
        item = rng.choice(listing.inclusions)
        return LLMEdit(
            instruction=(
                f"The inclusions list contains {item!r}. In the description, describe that same "
                "thing as a paid extra / available for an additional charge. "
                "Do not state an amount "
                "of money. Do not change inclusions or exclusions."
            ),
            editable=["description", "short_description"],
            extra_spans=[Span(field="inclusions", span=item)],
        )
    if v == "claim_vs_attribute":
        key = rng.choice(["small_group", "skip_the_line", "private", "wheelchair_accessible"])
        claim, _ = _CLAIMS[key]
        set_attrs: dict[str, bool | str] = {key: False}
        extra = [Span(field=f"attributes.{key}", span="false")]
        return LLMEdit(
            instruction=(
                f"Work in the feature claim: {claim}. Write it as this supplier's own marketing "
                "copy would phrase it."
            ),
            editable=copy_fields,
            set_attributes=set_attrs,
            delete_attributes=["max_group_size"] if key == "small_group" else [],
            extra_spans=extra,
        )
    if v == "promised_in_exclusions":
        item = rng.choice(_EXCLUSION_PROMISES)
        return LLMEdit(
            instruction=(
                "Promise in the title, short_description or description that the customer "
                f"receives "
                f"{item} as part of the experience, AND add the same item to `exclusions` (in the "
                "listing's language). Do not change inclusions."
            ),
            editable=[*copy_fields, "exclusions"],
            required_span_fields=["exclusions"],
        )
    if v == "unsupported_claim":
        options = list(_CLAIMS)
        if "wildlife" in listing.description.lower() or "Wildlife" in listing.tags:
            options.append("guaranteed")
        key = rng.choice(options)
        claim, related = _CLAIMS.get(key, ("guaranteed wildlife sightings", []))
        return LLMEdit(
            instruction=(
                f"Work in the feature claim: {claim}. Write it as this supplier's own marketing "
                "copy would phrase it. Nothing else in the listing may support or contradict it."
            ),
            editable=copy_fields,
            delete_attributes=related,
        )
    if v in _D4_ITEMS:
        item = rng.choice(_D4_ITEMS[v])
        return LLMEdit(
            instruction=(
                "Promise in the title, short_description or description that the customer "
                f"receives "
                f"{item} as part of the experience (stated as included, not optional or extra). It "
                "must not already be covered by inclusions. Do not change inclusions or exclusions."
            ),
            editable=copy_fields,
            delete_attributes=["hotel_pickup"] if v == "pickup_promised" else [],
        )
    if v == "untranslated_fragment":
        other = OTHER_LANGUAGE[loc]
        return LLMEdit(
            instruction=(
                f"Insert one sentence or phrase written in {other}, left untranslated as if copied "
                f"from the {other} version of this listing. "
                "Not a proper noun or established loanword."
            ),
            editable=[
                "description",
                "short_description",
                "meeting_point",
                "inclusions",
                "exclusions",
            ],
            max_span_words=25,  # the span is the untranslated fragment itself
        )
    if v in _EXAMPLES:
        ex = " or ".join(repr(e) for e in _EXAMPLES[v][loc])
        where = {
            "time_format": (["description", "meeting_point", "short_description"], "a time"),
            "date_format": (["description", "meeting_point", "cancellation_policy"], "a date"),
            "currency_in_cancellation": (["cancellation_policy"], "a cancellation fee"),
            "number_format": (
                ["description", "meeting_point"],
                "a number (distance, height, count)",
            ),
            "promo": (copy_fields, "discount or promotion wording"),
            "price": (copy_fields, "the price of the experience"),
            "price_other_format": (
                ["description", "short_description"],
                "the price of the experience",
            ),
        }[v]
        fields, what = where
        if v in ("price", "promo"):
            how = f"in a natural way, for example {ex}"
        elif v == "price_other_format":
            how = f"using the OTHER locale's currency format, for example {ex}"
        else:
            how = f"using the other locale's convention, for example {ex}, which is wrong for {loc}"
        return LLMEdit(instruction=f"Add {what} {how}.", editable=fields)
    raise ValueError(f"no LLM edit for variant {v!r}")


def inject_schema(editable: list[str]) -> dict[str, Any]:
    text_fields = [f for f in editable if f in TEXT_FIELDS]
    list_fields = [f for f in editable if f in LIST_FIELDS]
    props: dict[str, Any] = {
        "spans": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string", "enum": TEXT_FIELDS + LIST_FIELDS},
                    "span": {"type": "string"},
                },
                "required": ["field", "span"],
                "additionalProperties": False,
            },
        }
    }
    if text_fields:
        props["text_edits"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string", "enum": text_fields},
                    "new_value": {"type": "string"},
                },
                "required": ["field", "new_value"],
                "additionalProperties": False,
            },
        }
    if list_fields:
        props["list_edits"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string", "enum": list_fields},
                    "new_items": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["field", "new_items"],
                "additionalProperties": False,
            },
        }
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


_HINT_RE = re.compile(
    r"\b(?:D[1-8]|defects?|seeded|inject\w*|intentional\w*|placeholder|untranslated|mistake)\b",
    re.IGNORECASE,
)


def apply_llm_edit(
    listing: Listing, edit: LLMEdit, data: dict[str, Any], variant: str
) -> tuple[Injection, list[str]]:
    """Apply model output; return the injection plus structural problems."""
    errors: list[str] = []
    update: dict[str, Any] = {}
    for e in data.get("text_edits", []):
        f = e["field"]
        if f not in edit.editable:
            errors.append(f"edited non-editable field {f}")
            continue
        old, new = getattr(listing, f), e["new_value"]
        if len(new) > len(old) * 1.6 + 250 or (len(old) > 60 and len(new) < len(old) * 0.6):
            errors.append(f"{f}: length changed too much ({len(old)} -> {len(new)} chars)")
        if len(_HINT_RE.findall(new)) > len(_HINT_RE.findall(old)):
            errors.append(f"{f}: new text hints at the defect")
        update[f] = new
    for e in data.get("list_edits", []):
        f = e["field"]
        if f not in edit.editable:
            errors.append(f"edited non-editable field {f}")
            continue
        old_items, new_items = getattr(listing, f), e["new_items"]
        changed = len(set(new_items) - set(old_items)) + len(set(old_items) - set(new_items))
        if changed > 3:
            errors.append(f"{f}: too many items changed ({changed})")
        if any(len(_HINT_RE.findall(i)) for i in new_items if i not in old_items):
            errors.append(f"{f}: new item hints at the defect")
        update[f] = new_items
    if not update:
        errors.append("no edits returned")

    attrs = dict(listing.attributes)
    for k in edit.delete_attributes:
        attrs.pop(k, None)
    attrs.update(edit.set_attributes)
    update["attributes"] = attrs
    new_listing = listing.model_copy(update=update)

    spans = [Span(field=s["field"], span=s["span"]) for s in data.get("spans", [])]
    spans = [s for s in spans if s.span.strip()]
    for s in spans:
        if s.field not in LIST_FIELDS and len(s.span.split()) > edit.max_span_words:
            errors.append(
                f"span in {s.field} is {len(s.span.split())} words; give the shortest excerpt "
                f"(at most {edit.max_span_words} words) that shows the problem"
            )
    edited = {f for f in update if f != "attributes"}
    if not any(s.field in edited for s in spans):
        errors.append("no span in an edited field")
    for f in edit.required_span_fields:
        if not any(s.field == f for s in spans):
            errors.append(f"missing span for {f}")
        if f in LIST_FIELDS and f not in edited:
            errors.append(f"{f} must be edited")
    # Drop model spans that duplicate our programmatic ones, then add those.
    extra_keys = {(s.field, s.span) for s in edit.extra_spans}
    spans = [s for s in spans if (s.field, s.span) not in extra_keys] + list(edit.extra_spans)
    return Injection(new_listing, spans, variant, {"edited_fields": sorted(edited)}), errors


def llm_variant_detectable(variant: str) -> bool:
    return variant in _DETECTABLE_LLM_VARIANTS
