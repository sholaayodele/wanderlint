# Labeling guide

Companion to `taxonomy.yaml` (v1). Defines how to label listings for `gold.jsonl`.

## Unit of labeling
<!-- TODO(M1): (listing_id, defect_id), multiple spans allowed -->

## Overlaps and tie-breaks
<!-- TODO(M1): expand the decision_rules from taxonomy.yaml with worked examples -->

### D1 — placeholder substrings (`lorem ipsum`)
`placeholder_values` (TBD, folgt, …) only count when they are the whole field.
`lorem ipsum` is different: it counts as D1 wherever it appears in a text field,
case-insensitive, even alongside real text.

- **D1 (positive), de-DE, `description`:**
  `"Erkunden Sie die Altstadt mit unserem Guide. Lorem ipsum dolor sit amet."`
  The description has real text, but the leftover template filler marks it as unfinished.
- **Not D1:** `description: "Our guide will show you around — see above for times."`
  "see above" is a whole-value placeholder only; inside real text it is not D1.

## Workflow
<!-- TODO(M1): seeded → confirmed / added_by_human; re-label 30 listings for κ -->
