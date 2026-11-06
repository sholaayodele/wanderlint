"""Deterministic audit of a generated dataset (datagen v5 guards, re-checked after the fact).

Checks every seeded label against the final listing and its pre-injection version:
  claim_product      group claims (private / small group) only on guided products;
                     claims add no receivable (guide, food, drinks, transport, pickup)
  d4_overlap         D4 promised spans don't match inclusions/exclusions by synonym group
  same_sentence      no two seeded spans of one text field in the same sentence
  trailing_clause    no seeded span in a tail appended to an unchanged original sentence
  span_length        claim/promise/price spans ≤ 8 words, format tokens ≤ 3, all verbatim

Usage: uv run python scripts/audit_generated.py [--json out.json]
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Annotated

import typer
import yaml

from wanderlint.datagen.checks import receivable_counts, receivable_groups, span_present
from wanderlint.datagen.inject import (
    _CLAIM_TERMS,
    _TOKEN_EXAMPLES,
    claim_keys,
    same_sentence_problems,
    trailing_clause_problems,
)
from wanderlint.datagen.vocab import GROUP_CLAIMS, TEXT_FIELDS
from wanderlint.models import Listing, Span

ROOT = Path(__file__).resolve().parents[1]
CHECKS = ["claim_product", "d4_overlap", "same_sentence", "trailing_clause", "span_length"]
CLAIM_VARIANTS = {"unsupported_claim", "claim_vs_attribute"}
D4_VARIANTS = {"food_promised", "drink_promised", "ticket_promised", "transport_promised",
               "pickup_promised"}  # fmt: skip


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _strip(text: str, terms: list[str]) -> str:
    low = text
    for t in terms:
        low = low.replace(t, " ").replace(t.lower(), " ").replace(t.capitalize(), " ")
    return low


def audit_listing(final: Listing, pre: Listing | None, seeded: list[dict], labels: list[dict]):
    out: dict[str, list[str]] = defaultdict(list)
    by_type = {lab["defect_id"]: [Span(**s) for s in lab["spans"]] for lab in labels}
    all_terms = [t for loc in _CLAIM_TERMS.values() for ts in loc.values() for t in ts]
    group_terms = [t for k in GROUP_CLAIMS for t in _CLAIM_TERMS[k][final.locale]]
    for a in seeded:
        spans = by_type.get(a["defect_id"], [])
        text_spans = [s for s in spans if s.field in TEXT_FIELDS and s.span.strip()]
        v = a["variant"]
        if v in CLAIM_VARIANTS and pre is not None:
            if not set(GROUP_CLAIMS) & set(claim_keys(pre, list(GROUP_CLAIMS))):
                hits = [s.span for s in text_spans
                        if any(t.lower() in s.span.lower() for t in group_terms)]  # fmt: skip
                out["claim_product"] += [f"{a['defect_id']}: group claim on unguided product: {h!r}"
                                         for h in hits]  # fmt: skip
            for f in {s.field for s in text_spans}:
                old = receivable_counts(_strip(getattr(pre, f), all_terms))
                new = receivable_counts(_strip(getattr(final, f), all_terms))
                added = [g for g in new if g != "tickets" and new[g] > old[g]]
                if added:
                    out["claim_product"].append(f"{a['defect_id']}: {f} adds {added}")
        if v in D4_VARIANTS:
            listed = set().union(
                *(receivable_groups(i) for i in final.inclusions + final.exclusions)
            )
            for s in text_spans:
                clash = receivable_groups(s.span) & listed
                if clash:
                    out["d4_overlap"].append(f"{s.span!r} overlaps listed {sorted(clash)}")
        limit = 3 if v in _TOKEN_EXAMPLES else 25 if v == "untranslated_fragment" else 8
        for s in spans:
            if not span_present(final, s):
                out["span_length"].append(f"not verbatim: {s.field}: {s.span!r}")
            if a["method"] == "llm" and s in text_spans and len(s.span.split()) > limit:
                out["span_length"].append(f"{v}: {len(s.span.split())} > {limit} words: {s.span!r}")
    seeded_types = [a["defect_id"] for a in seeded]
    for i, d in enumerate(seeded_types):
        earlier = [s for e in seeded_types[:i] for s in by_type.get(e, [])]
        out["same_sentence"] += same_sentence_problems(final, by_type.get(d, []), earlier)
        if pre is not None:
            out["trailing_clause"] += trailing_clause_problems(pre, final, by_type.get(d, []))
    return out


def main(
    json_out: Annotated[
        Path | None, typer.Option("--json", help="Write violations as JSON.")
    ] = None,
) -> None:
    dg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["datagen"]
    listings = {d["id"]: Listing(**d) for d in _read_jsonl(ROOT / dg["output_listings"])}
    meta = {m["listing_id"]: m for m in _read_jsonl(ROOT / dg["output_meta"])}
    labels: dict[str, list[dict]] = defaultdict(list)
    for lab in _read_jsonl(ROOT / dg["output_seeded"]):
        labels[lab["listing_id"]].append(lab)
    report: dict[str, list[str]] = {c: [] for c in CHECKS}
    n_defective = 0
    for lid, final in listings.items():
        m = meta[lid]
        if not m["seeded"]:
            continue
        n_defective += 1
        pre = Listing(**m["pre_injection"]) if m.get("pre_injection") else None
        if pre is None:
            report["trailing_clause"].append(f"{lid}: no pre_injection listing to compare")
        for check, problems in audit_listing(final, pre, m["seeded"], labels[lid]).items():
            report[check] += [f"{lid}: {p}" for p in problems]
    print(f"Audited {n_defective} defective listings ({len(listings)} total)")
    for c in CHECKS:
        print(f"  {c:16} {len(report[c])} violations")
        for p in report[c]:
            print(f"    - {p}")
    if json_out:
        json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    typer.run(main)
