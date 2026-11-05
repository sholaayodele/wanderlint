# Failure patterns

Living log of checker failure patterns, root causes (label | definition | model),
fixes, and taxonomy/prompt version changes.

## Known limitations (cite in every eval report)

- **Same model family for data and checker.** Synthetic listings and seeded defects are
  generated with Claude (`models.datagen`, Sonnet 5.5); the checker is also Claude. The checker
  may find Claude-written text and Claude-injected defects easier (or harder) than human-written
  ones, so scores on this data may not transfer to real catalog content. Mitigation later: a
  small set of real or human-written listings labelled with the same guide.
- **Seeded labels are not ground truth.** A "clean" generated listing can still contain an
  unintended defect; the human labeling pass (confirmed / added_by_human / RejectedSeed) is
  what makes gold.
- **Heuristic generation guard.** `datagen/checks.py` approximates the rule-detectable
  defects (emoji via code-point ranges, simple regexes). It keeps clean listings clean and
  injections single-type, but it is not the M2 rules engine.

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
- 2026-11-01 — taxonomy v1 gaps closed (before any gold labels; version stays 1):
  - G1 D3 vs D4: deliverables (food, drinks, tickets, transport, pickup, equipment, guide) → D4;
    qualitative/feature claims (skip-the-line, small group, private, accessible, superlatives) → D3.
    "Hotel pickup" removed from D3's claim list. "Best price" moved from D3 to D6 (promotion).
  - G2 Category/tag outside the vocabularies → D7, rule-detected. D7 detection: llm → both.
  - G3 D5 = the other locale's convention only. locale_conventions now lists allowed formats per
    locale (DE "14 Uhr"/"14:00 Uhr"/"14:00"; EN "2pm"/"2:00 pm"/"14:00"; "EUR 25" in both).
  - G4 Contact details → D6 in every text field incl. meeting_point, cancellation_policy.
    Street addresses are not contact details.
  - G5 Any discount/promotion language in title/short_description/description → D6.
  - G6 D8 word = whitespace token with ≥ 1 letter; emoji = Unicode Extended_Pictographic.
  - G7 locale removed from D1 required fields; invalid locale = ingestion error, not scored.
  - G8 D4 covers promises in title, short_description and description.
  - G9 Frozen gold.jsonl holds only confirmed / added_by_human. Rejected seeds are dropped
    from gold and logged in data/labels/rejected_seeds.jsonl (new RejectedSeed model).
- 2026-11-04 — D8 edge cases (before any gold labels; version stays 1):
  - © ® ™ are not emoji for D8, although they are Extended_Pictographic. They're legitimate
    in brand/attraction names ("LEGOLAND® Discovery Centre Tickets" is clean). Listed in
    D8 emoji_exclusions.
  - Acronym length check strips leading/trailing punctuation first ("BBQ!!!" → "BBQ",
    excluded from the caps ratio). Repeated punctuation remains a separate D8 trigger.
- 2026-11-04 — config: removed `llm.temperature`. The current API version rejects
  temperature/top_p/top_k with a 400 on every model (Sonnet 5.5 rejects non-default sampling
  too). Reproducibility comes from the response cache (.cache/llm, keyed on prompt version,
  model, taxonomy version and full request), not from sampling settings; the API has no seed.
- 2026-11-04 — datagen pilot finding (generation guard, not the checker): the de-DE "English
  time format" regex matched correct German "um 14:00 am Ufer" ("am" = "on the"). Fixed: a
  spaced "am" only counts when no word follows; regression test added. Carry this into the
  M2 D5 rule.
- 2026-11-05 — datagen prompts v2 (`datagen_listing_v2`, `datagen_inject_v2`), from the v1 pilot
  review (reports/datagen/pilot_v1_live_run.json):
  1. Placeholders never go in the title; "see above" / "siehe oben" only in fields below the
     description (duration, meeting_point, cancellation_policy). Pilot v1 produced the title
     "SIEHE OBEN!!!" for the D1/D8 hard case.
  2. ® trap only for categories where a real trademarked brand is plausible; listing prompt
     forbids ® on public places / natural sites (v1 produced "Schinias Wetland®").
  3. Injected spans must be the shortest excerpt (≤ 12 words; untranslated fragments ≤ 25),
     enforced in verification (v1 spanned whole sentences, e.g. D6 "…for only €39.").
  4. Inject prompt: injected claims/promises must read as the supplier's natural marketing
     copy at the listing's quality level; no stylistic seams or redundancy (v1: "Your private
     visit for your group alone"). D2/D3 claim instructions now name the concept only and
     leave phrasing to the model.
  Note: the claim instruction text lives in code (datagen/inject.py), so re-running v1 would
  not hit the v1 cache; v1 outputs are preserved only in the pilot record.
