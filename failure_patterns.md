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
