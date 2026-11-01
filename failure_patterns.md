# Failure patterns

Living log of checker failure patterns, root causes (label | definition | model),
fixes, and taxonomy/prompt version changes.

## Changelog

- 2026-11-01 — taxonomy v1 created (D1–D8).
- 2026-11-01 — taxonomy v1 decisions (made before any gold labels existed, so no version bump):
  - Promised + listed in exclusions → D2; promised + absent from inclusions → D4.
  - D1 and D7 score once per listing; each affected field/tag is a separate span.
  - Price in free text → D6 only, never also D5.
  - D8 extra triggers kept (repeated punctuation, emoji in title). Caps-ratio rule is
    title-only and ignores all-caps tokens ≤ 4 chars plus whitelisted acronyms (UNESCO).
  - Discriminatory wording removed from D6; parked as candidate D9 (CLAUDE.md §11).
  - D1 placeholder list: TBD, tbc, N/A, n. a., folgt, siehe oben, see above, lorem ipsum, xxx, "-".
- 2026-11-01 — taxonomy v1 decision: "lorem ipsum" moved from placeholder_values (whole-field
  match) to new placeholder_substrings (case-insensitive match anywhere in a text field).
  Other placeholders stay whole-field. Example in labeling_guide.md (taxonomy keeps 2+1 examples).
- 2026-11-01 — taxonomy v1 refinements (before any gold labels; version stays 1):
  1. Schema: GoldLabel and Defect use `spans: list[{field, span}]` (≥ 1) instead of a single
     field/span. New UnsureLabel → data/labels/unsure.jsonl, never part of gold.
  2. D1 vs D8: a field whose entire value is a placeholder (incl. trailing punctuation,
     "TBD!!!", "XXX") is D1 only; D8 applies only to real content.
  3. D4 narrowed to "description promises an item inclusions don't mention". Included but
     described as paid extra is D2.
  4. D6 prices count only in title, short_description, description. Fees in
     cancellation_policy are allowed; D5 formatting applies there.
  5. D7: fixed vocabularies added (12 categories, 30 tags, locale-independent). D7 = clear
     mismatch with content. Duration tags that contradict the duration field are D2.
  6. D8 duplicates: within one field only. Cross-field copying is not a defect (CLAUDE.md §14).
  7. D5: meaning-changing mistranslation removed from v1 (CLAUDE.md §11, needs source/target
     pairs). D5 = untranslated fragments, wrong-language text, locale format errors.
