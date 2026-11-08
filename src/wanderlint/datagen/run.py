"""Orchestration: generate clean listings, inject defects, write outputs and a summary."""

from __future__ import annotations

import json
import random
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from wanderlint.cost import CostCapExceeded
from wanderlint.datagen.checks import CheckContext, find_issues, trap_present
from wanderlint.datagen.inject import (
    CannotInject,
    Injection,
    apply_llm_edit,
    inject_rule,
    inject_schema,
    llm_edit_for,
    llm_variant_detectable,
    verify_injection,
)
from wanderlint.datagen.plan import DefectSpec, ListingSpec, TrapSpec
from wanderlint.datagen.vocab import (
    ATTRIBUTE_KEYS,
    BOOL_ATTRIBUTES,
    LENGTHS,
    NA_TOKEN,
    QUALITIES,
    STR_ATTRIBUTES,
    VARIANTS,
)
from wanderlint.llm import LLMClient, LLMError
from wanderlint.models import GoldLabel, Listing, Span, Taxonomy

LANGUAGE = {"de-DE": "German", "en-GB": "British English"}


@dataclass(frozen=True)
class GenSettings:
    model: str
    prompt_version: str
    effort: str
    max_tokens: int
    max_retries: int


@dataclass(frozen=True)
class Prompts:
    listing_system: str
    inject_system: str


@dataclass
class Failure:
    listing_id: str
    stage: str
    attempt: int
    errors: list[str]
    defect_id: str | None = None


@dataclass
class ListingResult:
    spec: ListingSpec
    listing: Listing | None = None
    clean: Listing | None = None  # pre-injection listing, kept for audits
    labels: list[GoldLabel] = field(default_factory=list)
    applied: list[dict[str, Any]] = field(default_factory=list)
    failed_defects: list[dict[str, Any]] = field(default_factory=list)
    traps_present: list[TrapSpec] = field(default_factory=list)
    generation_attempts: int = 0


# --- context & prompts ------------------------------------------------------------


def check_context(taxonomy: Taxonomy) -> CheckContext:
    t = taxonomy.by_id()
    return CheckContext(
        placeholders=t["D1"].placeholder_values or [],
        placeholder_substrings=t["D1"].placeholder_substrings or [],
        caps_whitelist=t["D8"].caps_acronym_whitelist or [],
        categories=t["D7"].categories or [],
        tags=t["D7"].tags or [],
    )


def render_prompts(prompts_dir: Path, version: str, taxonomy: Taxonomy) -> Prompts:
    conv = taxonomy.by_id()["D5"].locale_conventions or {}
    lines = []
    for loc, formats in conv.items():
        parts = "; ".join(f"{k}: {', '.join(v)}" for k, v in formats.items())
        lines.append(f"   - {loc}: {parts}")
    attrs = (
        ", ".join(f"`{k}` (true/false)" for k in BOOL_ATTRIBUTES)
        + ", "
        + ", ".join(f"`{k}` (text)" for k in STR_ATTRIBUTES)
    )
    listing = (prompts_dir / f"{version.replace('_v', '_listing_v')}.md").read_text(
        encoding="utf-8"
    )
    listing = listing.replace("{{CONVENTIONS}}", "\n".join(lines)).replace("{{ATTRIBUTES}}", attrs)
    inject = (prompts_dir / f"{version.replace('_v', '_inject_v')}.md").read_text(encoding="utf-8")
    return Prompts(listing_system=listing, inject_system=inject)


def trap_instruction(trap: TrapSpec, locale: str) -> str:
    k, d = trap.kind, trap.detail
    if k == "title_acronym_or_registered":
        if d == "registered":
            return (
                "The title contains a brand or attraction name that is genuinely trademarked, "
                "written with the ® sign, e.g. a theme park, attraction chain or branded "
                "experience (LEGOLAND®, SEA LIFE®, Madame Tussauds®, or a plausible commercial "
                "brand of that kind). Never put ® on a public place, natural site, city, street "
                "or generic museum name."
            )
        options = d.removeprefix("acronym:").split("|") if d.startswith("acronym:") else []
        return (
            "The title contains exactly one of these acronyms, in capitals, whichever fits the "
            f"activity best: {', '.join(options)}. Write it as a standalone word, not "
            "hyphenated into a compound or in brackets (e.g. 'SUP lesson' or 'per 4WD', not "
            "'SUP-Kurs', '4WD-Geländewagen' or 'GPS-guided'). It names the activity itself "
            "(equipment, vehicle, format or era), and the description must match it; if it "
            "names equipment or a vehicle the customer uses, list that in inclusions. No "
            "status, rank or perk "
            "acronyms anywhere in the title (no UNESCO, VIP, BBQ). The rest of the title is in "
            "normal case."
        )
    if k == "fee_in_cancellation":
        return (
            "The cancellation_policy states a concrete cancellation fee written in exactly this "
            f"format: '{d}' (any amount)."
        )
    if k == "na_in_sentence":
        tok = NA_TOKEN[locale]
        example = (
            "'Mindestalter: n. a. – Kinder jeden Alters sind willkommen.'"
            if locale == "de-DE"
            else "'Dietary requirements: please tell us when booking (write N/A if none).'"
        )
        return (
            f"Use the abbreviation '{tok}' naturally inside a longer sentence in one field, like "
            f"{example} Never as a whole field value."
        )
    if k == "locale_variant":
        return (
            "In the description or meeting_point, write at least one time of day in exactly this "
            f"style: '{d}' (any time)."
        )
    if k == "short_in_description":
        return "The description begins by repeating the short_description word for word."
    if k == "street_address_meeting_point":
        return (
            "The meeting_point includes a full street address with house number and postcode "
            "(plausible for the city)."
        )
    raise ValueError(k)


def generation_schema() -> dict[str, Any]:
    s = {"type": "string"}
    arr = {"type": "array", "items": {"type": "string"}}
    props = {
        "title": s,
        "short_description": s,
        "description": s,
        "duration": s,
        "meeting_point": s,
        "inclusions": arr,
        "exclusions": arr,
        "cancellation_policy": s,
        "attributes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"key": {"type": "string", "enum": ATTRIBUTE_KEYS}, "value": s},
                "required": ["key", "value"],
                "additionalProperties": False,
            },
        },
    }
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


def generation_user_prompt(spec: ListingSpec, feedback: list[str]) -> str:
    spec_block = {
        "id": spec.id,
        "locale": spec.locale,
        "language": LANGUAGE[spec.locale],
        "city": spec.city,
        "country": spec.country,
        "category": spec.category,
        "tags": spec.tags,
        "activity": spec.activity,
        "length": spec.length,
        "writing_quality": spec.quality,
        "traps": [t.model_dump() for t in spec.traps],
    }
    lines = [
        "Write one listing for this product.",
        "",
        "SPEC",
        json.dumps(spec_block, ensure_ascii=False, indent=2),
        "",
        f"- Write all fields in {LANGUAGE[spec.locale]} ({spec.locale}).",
        f"- Adapt the activity ({spec.activity}) so it is plausible for "
        f"{spec.city}, {spec.country}.",
        f"- Length: {LENGTHS[spec.length]}.",
        f"- Writing quality: {spec.quality}: {QUALITIES[spec.quality]}.",
        f"- Category (fixed): {spec.category}. Tags (fixed): {', '.join(spec.tags) or 'none'}.",
        "",
        "Special requirements:",
    ]
    lines += [f"- {trap_instruction(t, spec.locale)}" for t in spec.traps] or ["- none"]
    if feedback:
        lines += ["", "Your previous attempt was rejected for these reasons. Avoid them:"]
        lines += [f"- {e}" for e in feedback]
    return "\n".join(lines)


def parse_generated(data: dict[str, Any], spec: ListingSpec) -> tuple[Listing | None, list[str]]:
    errors: list[str] = []
    attrs: dict[str, bool | str] = {}
    for a in data.get("attributes", []):
        key, value = a["key"], a["value"].strip()
        if key in BOOL_ATTRIBUTES:
            if value.lower() not in ("true", "false"):
                errors.append(f"attribute {key} must be 'true' or 'false', got {value!r}")
                continue
            attrs[key] = value.lower() == "true"
        else:
            attrs[key] = value
    fields = {k: data.get(k) for k in generation_schema()["properties"] if k != "attributes"}
    try:
        listing = Listing(
            id=spec.id,
            locale=spec.locale,
            category=spec.category,
            tags=spec.tags,
            attributes=attrs,
            **fields,
        )
    except ValidationError as e:
        return None, errors + [f"schema: {err['loc']}: {err['msg']}" for err in e.errors()]
    if any(t.kind == "short_in_description" for t in spec.traps):
        short = listing.short_description.strip()
        if short and short not in listing.description:
            listing = listing.model_copy(update={"description": f"{short} {listing.description}"})
    return listing, errors


# --- generation & injection -------------------------------------------------------


def generate_clean(
    spec: ListingSpec,
    llm: LLMClient,
    prompts: Prompts,
    settings: GenSettings,
    ctx: CheckContext,
    failures: list[Failure],
) -> tuple[Listing | None, int]:
    feedback: list[str] = []
    for attempt in range(settings.max_retries + 1):
        try:
            result = llm.complete_json(
                purpose=f"generate:{spec.id}",
                prompt_version=settings.prompt_version,
                model=settings.model,
                system=prompts.listing_system,
                user=generation_user_prompt(spec, feedback),
                schema=generation_schema(),
                max_tokens=settings.max_tokens,
                effort=settings.effort,
                attempt=attempt,
            )
        except LLMError as e:
            failures.append(Failure(spec.id, "generate", attempt, [str(e)]))
            feedback = [str(e)]
            continue
        listing, errors = parse_generated(result.data, spec)
        if listing is not None:
            errors += [f"{i.defect_id} in {i.field}: {i.detail}" for i in find_issues(listing, ctx)]
            errors += [
                f"special requirement not met: {trap_instruction(t, spec.locale)}"
                for t in spec.traps
                if not trap_present(t.kind, t.detail, listing, ctx)
            ]
        if listing is not None and not errors:
            return listing, attempt + 1
        failures.append(Failure(spec.id, "generate", attempt, errors))
        feedback = errors
    return None, settings.max_retries + 1


def _inject_user_prompt(
    listing: Listing,
    spec: ListingSpec,
    instruction: str,
    editable: list[str],
    feedback: list[str],
    prior_spans: list[Span] | None = None,
) -> str:
    lines = [
        "LISTING",
        listing.model_dump_json(indent=2, exclude={"id"}),
        "",
        f"Locale: {listing.locale} ({LANGUAGE[listing.locale]}). Writing quality: {spec.quality}. "
        f"Length: {spec.length}.",
        "",
        f"CHANGE: {instruction}",
        "",
        f"You may edit only these fields: {', '.join(editable)}.",
    ]
    earlier = [s for s in prior_spans or [] if s.field in editable and s.span.strip()]
    if earlier:
        lines += ["", "Already changed (keep verbatim; put your change in a different sentence):"]
        lines += [f"- {s.field}: {s.span!r}" for s in earlier]
    if feedback:
        lines += ["", "Your previous attempt was rejected for these reasons. Fix them:"]
        lines += [f"- {e}" for e in feedback]
    return "\n".join(lines)


def _avoid_fields(spans: list[Span]) -> set[str]:
    return {s.field for s in spans if not s.field.startswith("attributes.")}


def fallback_variants(dspec: DefectSpec) -> list[str]:
    """Planned variant first; if it can't apply to a listing (e.g. every D4 item overlaps
    inclusions/exclusions), the type's other LLM variants. Hard cases don't fall back, except
    D4 (D3/D4 pickup): the defect is kept, the hard case is not."""
    variants = [dspec.variant]
    if dspec.hard_case is None or dspec.defect_id == "D4":
        variants += [v for m, v in VARIANTS[dspec.defect_id] if m == "llm" and v != dspec.variant]
    return variants


def inject_llm(
    listing: Listing,
    spec: ListingSpec,
    dspec: DefectSpec,
    prior_spans: list[Span],
    allowed: set[str],
    llm: LLMClient,
    prompts: Prompts,
    settings: GenSettings,
    ctx: CheckContext,
    failures: list[Failure],
    seed: int,
) -> Injection | None:
    variants = fallback_variants(dspec)
    edit = None
    for variant in variants:
        rng = random.Random(f"{seed}|{spec.id}|{dspec.defect_id}|{variant}")
        try:
            edit = llm_edit_for(dspec.model_copy(update={"variant": variant}), listing, rng)
            break
        except CannotInject as e:
            failures.append(Failure(spec.id, "inject", 0, [f"{variant}: {e}"], dspec.defect_id))
    if edit is None:
        return None
    feedback: list[str] = []
    for attempt in range(settings.max_retries + 1):
        try:
            result = llm.complete_json(
                purpose=f"inject:{spec.id}:{dspec.defect_id}",
                prompt_version=settings.prompt_version,
                model=settings.model,
                system=prompts.inject_system,
                user=_inject_user_prompt(
                    listing, spec, edit.instruction, edit.editable, feedback, prior_spans
                ),
                schema=inject_schema(edit.editable),
                max_tokens=settings.max_tokens,
                effort=settings.effort,
                attempt=attempt,
            )
        except LLMError as e:
            failures.append(Failure(spec.id, "inject", attempt, [str(e)], dspec.defect_id))
            feedback = [str(e)]
            continue
        inj, errors = apply_llm_edit(listing, edit, result.data, variant)
        errors += verify_injection(
            listing,
            inj,
            dspec.defect_id,
            prior_spans,
            allowed,
            ctx,
            detectable=llm_variant_detectable(variant),
        )
        if not errors:
            inj.detail["instruction"] = edit.instruction
            return inj
        failures.append(Failure(spec.id, "inject", attempt, errors, dspec.defect_id))
        feedback = errors
    return None


def inject_defects(
    clean: Listing,
    spec: ListingSpec,
    llm: LLMClient,
    prompts: Prompts,
    settings: GenSettings,
    ctx: CheckContext,
    failures: list[Failure],
    seed: int,
) -> ListingResult:
    res = ListingResult(spec=spec, clean=clean)
    listing = clean
    spans_so_far: list[Span] = []
    allowed: set[str] = set()
    # LLM edits first (they rewrite prose), then deterministic edits on remaining fields.
    ordered = sorted(spec.defects, key=lambda d: (d.method != "llm", d.defect_id))
    for dspec in ordered:
        if dspec.method == "llm":
            inj = inject_llm(
                listing,
                spec,
                dspec,
                spans_so_far,
                allowed,
                llm,
                prompts,
                settings,
                ctx,
                failures,
                seed,
            )
        else:
            inj, errs = inject_rule(
                listing,
                dspec,
                f"{seed}|{spec.id}",
                _avoid_fields(spans_so_far),
                spans_so_far,
                allowed,
                ctx,
                city=spec.city,
                alcohol=spec.alcohol,
            )
            if inj is None:
                failures.append(Failure(spec.id, "inject", 0, errs, dspec.defect_id))
        if inj is None:
            res.failed_defects.append(dspec.model_dump())
            continue
        listing = inj.listing
        spans_so_far += inj.spans
        allowed.add(dspec.defect_id)
        res.labels.append(
            GoldLabel(
                listing_id=spec.id,
                defect_id=dspec.defect_id,
                spans=inj.spans,
                label_source="seeded",
            )  # type: ignore[arg-type]
        )
        res.applied.append(
            {
                "defect_id": dspec.defect_id,
                "method": dspec.method,
                "planned_variant": dspec.variant,
                "variant": inj.variant,
                "hard_case": dspec.hard_case if inj.variant == dspec.variant else None,
                "hard_case_planned": dspec.hard_case,
                "detail": inj.detail,
            }
        )
    res.listing = listing
    return res


def run_all(
    specs: list[ListingSpec],
    llm: LLMClient,
    prompts: Prompts,
    settings: GenSettings,
    ctx: CheckContext,
    seed: int,
    progress: Any = None,
) -> tuple[list[ListingResult], list[Failure], str | None]:
    """Returns (results, failures, stop_reason). Stops cleanly at the cost cap."""
    results: list[ListingResult] = []
    failures: list[Failure] = []
    for i, spec in enumerate(specs):
        try:
            clean, attempts = generate_clean(spec, llm, prompts, settings, ctx, failures)
            if clean is None:
                results.append(ListingResult(spec=spec, generation_attempts=attempts))
                continue
            if spec.defects:
                res = inject_defects(clean, spec, llm, prompts, settings, ctx, failures, seed)
            else:
                res = ListingResult(spec=spec, listing=clean)
            res.generation_attempts = attempts
            assert res.listing is not None
            res.traps_present = [
                t for t in spec.traps if trap_present(t.kind, t.detail, res.listing, ctx)
            ]
            results.append(res)
        except CostCapExceeded as e:
            return results, failures, f"cost cap reached before {spec.id}: {e}"
        if progress:
            progress(i + 1, len(specs), spec.id, llm.tracker.total_eur)
    return results, failures, None


# --- output -----------------------------------------------------------------------


def write_jsonl(path: Path, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(r + "\n" for r in rows), encoding="utf-8")


def meta_row(res: ListingResult, provenance: dict[str, Any] | None = None) -> dict[str, Any]:
    s = res.spec
    return {
        "listing_id": s.id,
        "generated": res.listing is not None,
        "locale": s.locale,
        "city": s.city,
        "country": s.country,
        "planned_category": s.category,
        "planned_tags": s.tags,
        "activity": s.activity,
        "length": s.length,
        "quality": s.quality,
        "planned_clean": s.is_clean,
        "traps_planned": [t.model_dump() for t in s.traps],
        "traps": [t.model_dump() for t in res.traps_present],
        "seeded": res.applied,
        "failed_defects": res.failed_defects,
        "generation_attempts": res.generation_attempts,
        "pre_injection": res.clean.model_dump() if res.clean and res.labels else None,
        **(provenance or {}),
    }


def write_outputs(
    results: list[ListingResult],
    failures: list[Failure],
    paths: dict[str, Path],
    provenance: dict[str, Any] | None = None,
) -> None:
    ok = [r for r in results if r.listing is not None]
    write_jsonl(paths["listings"], [r.listing.model_dump_json() for r in ok])  # type: ignore[union-attr]
    write_jsonl(paths["seeded"], [lab.model_dump_json() for r in ok for lab in r.labels])
    write_jsonl(
        paths["meta"], [json.dumps(meta_row(r, provenance), ensure_ascii=False) for r in results]
    )
    write_jsonl(paths["failures"], [json.dumps(asdict(f), ensure_ascii=False) for f in failures])


def summarize(
    results: list[ListingResult], failures: list[Failure], llm: LLMClient
) -> dict[str, Any]:
    ok = [r for r in results if r.listing is not None]
    labels = [lab for r in ok for lab in r.labels]
    hard = Counter(a["hard_case"] for r in ok for a in r.applied if a["hard_case"])
    calls = llm.tracker.calls
    live = [c for c in calls if not c.cached]
    by_purpose: dict[str, list[Any]] = {"generate": [], "inject": []}
    for c in live:
        by_purpose[c.purpose.split(":")[0]].append(c)

    def avg(xs: list[float]) -> float:
        return round(sum(xs) / len(xs), 1) if xs else 0.0

    return {
        "listings_planned": len(results),
        "listings_generated": len(ok),
        "clean_listings": sum(1 for r in ok if not r.labels),
        "per_locale": dict(Counter(r.listing.locale for r in ok)),  # type: ignore[union-attr]
        "per_category": dict(Counter(r.spec.category for r in ok)),
        "per_defect": dict(sorted(Counter(lab.defect_id for lab in labels).items())),
        "seeded_total": len(labels),
        "hard_cases": sum(hard.values()),
        "hard_cases_by_pair": dict(hard),
        "traps_clean": dict(Counter(t.kind for r in ok if not r.labels for t in r.traps_present)),
        "traps_defective": dict(Counter(t.kind for r in ok if r.labels for t in r.traps_present)),
        "defective_with_traps": sum(1 for r in ok if r.labels and r.traps_present),
        "failed_defects": sum(len(r.failed_defects) for r in results),
        "failure_events": len(failures),
        "api_calls": len(live),
        "cached_calls": len(calls) - len(live),
        "cost_eur_total": round(llm.tracker.total_eur, 4),
        "cost_eur_per_listing": round(llm.tracker.total_eur / max(1, len(ok)), 4),
        "tokens_per_call": {
            purpose: {
                "calls": len(cs),
                "avg_input": avg([c.usage.input_tokens for c in cs]),
                "avg_cache_read": avg([c.usage.cache_read_input_tokens for c in cs]),
                "avg_cache_write": avg([c.usage.cache_creation_input_tokens for c in cs]),
                "avg_output": avg([c.usage.output_tokens for c in cs]),
                "avg_cost_eur": round(sum(c.cost_eur for c in cs) / len(cs), 5) if cs else 0.0,
                "avg_latency_s": avg([c.latency_s for c in cs]),
            }
            for purpose, cs in by_purpose.items()
        },
    }
