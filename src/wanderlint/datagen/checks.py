"""Heuristic defect checks and trap detectors for synthetic data.

These are a *sanity guard* for generation, not the M2 rules engine: they catch
the obvious rule-detectable defects (D1, D5 formats, D6 contact/price/promo,
D7 vocab, D8) so that (a) clean listings come out clean and (b) an injection
introduces only the defect it was asked for. They are intentionally simple; the
human labeling pass is the real check. The emoji test approximates Unicode
Extended_Pictographic with code-point ranges (no `regex` dependency yet).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from wanderlint.datagen.vocab import (
    FEE_FORMATS,
    NA_TOKEN,
    PRICE_FIELDS,
    REQUIRED_TEXT_FIELDS,
    TEXT_FIELDS,
)
from wanderlint.models import Listing, Span


@dataclass(frozen=True)
class Issue:
    defect_id: str
    field: str
    detail: str


# --- helpers ----------------------------------------------------------------


def _strip_punct(s: str) -> str:
    start, end = 0, len(s)
    while start < end and unicodedata.category(s[start]).startswith("P"):
        start += 1
    while end > start and unicodedata.category(s[end - 1]).startswith("P"):
        end -= 1
    return s[start:end]


def rstrip_punct(s: str) -> str:
    end = len(s)
    while end > 0 and unicodedata.category(s[end - 1]).startswith("P"):
        end -= 1
    return s[:end]


def is_placeholder(value: str, placeholders: list[str]) -> bool:
    """Whole-value placeholder match, case-insensitive, trailing punctuation ignored."""
    v = value.strip()
    if not v:
        return False
    norm = rstrip_punct(v).strip().lower()
    if norm == "":  # value is only punctuation, e.g. "-" or "!!!"
        return True
    return norm in {rstrip_punct(p).strip().lower() for p in placeholders}


def caps_ratio(title: str, whitelist: list[str]) -> tuple[int, int]:
    """(caps words, counted words) per taxonomy D8 rules."""
    caps = counted = 0
    wl = set(whitelist)
    for token in title.split():
        if not any(c.isalpha() for c in token):
            continue
        core = _strip_punct(token)
        letters = [c for c in core if c.isalpha()]
        is_caps = bool(letters) and all(c.isupper() for c in letters)
        if is_caps and (len(core) <= 4 or core in wl):
            continue  # acronym: excluded from numerator and denominator
        counted += 1
        caps += is_caps
    return caps, counted


_EMOJI_RANGES = [
    (0x1F000, 0x1FAFF),
    (0x2600, 0x27BF),
    (0x2300, 0x23FF),
    (0x2B00, 0x2BFF),
    (0xFE0F, 0xFE0F),
]
_EMOJI_EXCLUDED = {"©", "®", "™"}


def emoji_chars(text: str) -> list[str]:
    return [
        c
        for c in text
        if c not in _EMOJI_EXCLUDED and any(lo <= ord(c) <= hi for lo, hi in _EMOJI_RANGES)
    ]


REPEATED_PUNCT_RE = re.compile(r"[!?]{3,}")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL_RE = re.compile(
    r"(?:https?://|www\.)\S+|\b[\w-]+\.(?:com|de|co\.uk|at|ch|net|org|eu|info|travel)\b",
    re.IGNORECASE,
)
HANDLE_RE = re.compile(r"(?<![\w.@])@[A-Za-z0-9_.]{3,}")
PHONE_CANDIDATE_RE = re.compile(r"\+?\(?\d[\d\s()./-]{5,}\d")
DATE_DOTTED_RE = re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b")
CURRENCY_RE = re.compile(r"[€£$]\s?\d|\d\s?(?:€|EUR\b|Euro\b|euros?\b)|\bEUR\s?\d", re.IGNORECASE)
PROMO_RE = re.compile(
    r"\d+\s?%\s?(?:off|rabatt)|\b(?:discount\w*|special offer|limited[- ]time offer|best price"
    r"|on sale|deal of the day|book now and save|early[- ]bird|nur heute|rabatt\w*"
    r"|sonderangebot\w*|gutschein\w*|promo(?:code)?|coupon|sparpreis|jetzt sparen)\b",
    re.IGNORECASE,
)
_MONTHS_EN = "January|February|March|April|May|June|July|August|September|October|November|December"
TIME_EN_RE = re.compile(
    r"\b\d{1,2}(?::\d{2})?\s?(?:pm\b|p\.m\.|a\.m\.)"
    r"|\b\d{1,2}(?::\d{2})?am\b"
    r"|\b\d{1,2}(?::\d{2})?\s+am\b(?!\s+\w)",
    re.IGNORECASE,
)
WRONG_FORMAT: dict[str, list[tuple[str, re.Pattern[str], bool]]] = {
    # (name, pattern, applies to price fields too)
    "de-DE": [
        # German "am" ("on the") often follows a time ("um 14:00 am Ufer"), so a spaced
        # "am" only counts when no word follows it.
        ("time_en", TIME_EN_RE, True),
        ("currency_en", re.compile(r"€\s?\d"), False),
        ("date_en", re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)? (?:{_MONTHS_EN}) \d{{4}}\b"), True),
        ("number_en", re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b"), True),
    ],
    "en-GB": [
        ("time_de", re.compile(r"\b\d{1,2}(?::\d{2})?\s?Uhr\b"), True),
        ("currency_de", re.compile(r"\d(?:[\d.,]*\d)?\s?€"), False),
        ("date_de", DATE_DOTTED_RE, True),
        ("number_de", re.compile(r"\b\d{1,3}(?:\.\d{3})+,\d+\b"), True),
    ],
}


def phones(text: str) -> list[str]:
    out = []
    for m in PHONE_CANDIDATE_RE.finditer(text):
        cand = m.group(0).strip()
        if DATE_DOTTED_RE.fullmatch(cand):
            continue
        if sum(c.isdigit() for c in cand) >= 8:
            out.append(cand)
    return out


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]


def duplicated_sentences(text: str) -> list[str]:
    seen: dict[str, int] = {}
    for s in _sentences(text):
        key = re.sub(r"\s+", " ", s.lower())
        if len(key) >= 20:
            seen[key] = seen.get(key, 0) + 1
    return [k for k, n in seen.items() if n > 1]


def field_values(listing: Listing) -> list[tuple[str, str]]:
    """(field, text) for every text field and every list item."""
    out = [(f, getattr(listing, f)) for f in TEXT_FIELDS]
    for f in ("inclusions", "exclusions"):
        out += [(f, item) for item in getattr(listing, f)]
    return out


# --- issues -----------------------------------------------------------------


@dataclass(frozen=True)
class CheckContext:
    placeholders: list[str]
    placeholder_substrings: list[str]
    caps_whitelist: list[str]
    categories: list[str]
    tags: list[str]


def find_issues(listing: Listing, ctx: CheckContext) -> list[Issue]:
    issues: list[Issue] = []
    placeholder_fields: set[str] = set()

    # D1
    for f in REQUIRED_TEXT_FIELDS:
        v = getattr(listing, f)
        if not v.strip():
            issues.append(Issue("D1", f, "empty"))
            placeholder_fields.add(f)
        elif is_placeholder(v, ctx.placeholders):
            issues.append(Issue("D1", f, f"placeholder {v!r}"))
            placeholder_fields.add(f)
    if not listing.category.strip():
        issues.append(Issue("D1", "category", "empty"))
    if not [i for i in listing.inclusions if i.strip()]:
        issues.append(Issue("D1", "inclusions", "empty list"))
    for f, v in field_values(listing):
        for sub in ctx.placeholder_substrings:
            if sub.lower() in v.lower():
                issues.append(Issue("D1", f, f"contains {sub!r}"))

    # D8
    title = listing.title
    if "title" not in placeholder_fields:
        if len(title.strip()) > 80:
            issues.append(Issue("D8", "title", f"{len(title.strip())} chars"))
        caps, counted = caps_ratio(title, ctx.caps_whitelist)
        if counted and caps / counted >= 0.5:
            issues.append(Issue("D8", "title", f"caps {caps}/{counted}"))
        if emoji_chars(title):
            issues.append(Issue("D8", "title", "emoji"))
    for f, v in field_values(listing):
        if f in placeholder_fields:
            continue
        if REPEATED_PUNCT_RE.search(v):
            issues.append(Issue("D8", f, "repeated punctuation"))
        if f not in ("inclusions", "exclusions") and duplicated_sentences(v):
            issues.append(Issue("D8", f, "duplicated sentence"))

    # D6
    for f, v in field_values(listing):
        if EMAIL_RE.search(v) or URL_RE.search(v) or HANDLE_RE.search(v) or phones(v):
            issues.append(Issue("D6", f, "contact details"))
    for f in PRICE_FIELDS:
        v = getattr(listing, f)
        if CURRENCY_RE.search(v):
            issues.append(Issue("D6", f, "price"))
        if PROMO_RE.search(v):
            issues.append(Issue("D6", f, "promotion"))

    # D5 (format conventions only; untranslated text is not checked here)
    for f, v in field_values(listing):
        for name, pat, in_price_fields in WRONG_FORMAT[listing.locale]:
            if f in PRICE_FIELDS and not in_price_fields:
                continue  # a price there is D6 only
            if pat.search(v):
                issues.append(Issue("D5", f, name))

    # D7 (vocabulary only)
    if listing.category.strip() and listing.category not in ctx.categories:
        issues.append(Issue("D7", "category", f"not in vocabulary: {listing.category!r}"))
    for t in listing.tags:
        if t not in ctx.tags:
            issues.append(Issue("D7", "tags", f"not in vocabulary: {t!r}"))

    return issues


# --- traps ------------------------------------------------------------------

_TIME_VARIANT_RE: dict[str, re.Pattern[str]] = {
    "14 Uhr": re.compile(r"\b\d{1,2} Uhr\b"),
    "14:00": re.compile(r"\b\d{1,2}:\d{2}\b(?!\s?(?:Uhr|am|pm))", re.I),
    "2pm": re.compile(r"\b\d{1,2}(?::\d{2})?(?:am|pm)\b", re.I),
}
_FEE_RE: dict[str, re.Pattern[str]] = {
    "25 €": re.compile(r"\b\d+ €"),
    "25,00 €": re.compile(r"\b\d+,\d{2} €"),
    "EUR 25": re.compile(r"\bEUR \d"),
    "€25": re.compile(r"€\d"),
}


def trap_present(kind: str, detail: str, listing: Listing, ctx: CheckContext) -> bool:
    loc = listing.locale
    if kind == "title_acronym_or_registered":
        if detail == "registered":
            return "®" in listing.title
        for token in listing.title.split():
            core = _strip_punct(token)
            letters = [c for c in core if c.isalpha()]
            if (
                letters
                and all(c.isupper() for c in letters)
                and (len(core) <= 4 or core in ctx.caps_whitelist)
            ):
                return True
        return False
    if kind == "fee_in_cancellation":
        pat = _FEE_RE.get(detail)
        return bool(pat and pat.search(listing.cancellation_policy))
    if kind == "na_in_sentence":
        token = NA_TOKEN[loc]
        return any(
            token in v
            and not is_placeholder(v, ctx.placeholders)
            and len(v.strip()) > len(token) + 10
            for _, v in field_values(listing)
        )
    if kind == "locale_variant":
        pat = _TIME_VARIANT_RE[detail]
        return bool(pat.search(listing.description) or pat.search(listing.meeting_point))
    if kind == "short_in_description":
        return bool(listing.short_description.strip()) and (
            listing.short_description.strip() in listing.description
        )
    if kind == "street_address_meeting_point":
        mp = listing.meeting_point
        return any(c.isdigit() for c in mp) and len(mp) > 15
    raise ValueError(f"unknown trap kind {kind!r}")


def fee_formats(locale: str) -> list[str]:
    return FEE_FORMATS[locale]


def span_present(listing: Listing, span: Span) -> bool:
    """Verbatim check per labeling_guide §2 (lists: exact item; attributes: value as written)."""
    f = span.field
    if f.startswith("attributes."):
        key = f.split(".", 1)[1]
        if key not in listing.attributes:
            return False
        val = listing.attributes[key]
        written = str(val).lower() if isinstance(val, bool) else str(val)
        return written == span.span
    if f in ("inclusions", "exclusions", "tags"):
        items = getattr(listing, f)
        return (span.span == "" and not items) or span.span in items
    if f == "category":
        return listing.category == span.span
    if f in TEXT_FIELDS:
        value = getattr(listing, f)
        return value.strip() == "" if span.span == "" else span.span in value
    return False
