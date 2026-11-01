# Wanderlint labeling guide (taxonomy v1)

For the human annotator building `data/labels/gold.jsonl`. Authority order:
`taxonomy.yaml` → CLAUDE.md §3 → this guide. If this guide and the taxonomy
disagree, the taxonomy wins and this guide is a bug.

You label **(listing_id, defect_id)** pairs: for each listing, decide which of
D1–D8 are present. One label per defect type per listing; one label may carry
several spans.

---

## 1. Workflow per listing

Note the **locale** first (`en-GB` or `de-DE`). Then go through these passes in order.
Each pass is short; don't skip one because you already found a defect.

| # | Pass | Look at | Defects |
|---|---|---|---|
| 1 | Required fields | every required field is filled with real content; "lorem ipsum" anywhere | D1 |
| 2 | Title mechanics | caps, length > 80, emoji, "!!!" | D8 |
| 3 | Read the copy | title, short_description, description — note every factual claim and every promised item | — |
| 4 | Cross-check | each claim vs duration, other fields, `attributes`; each promised item vs inclusions/exclusions | D2, D3, D4 |
| 5 | Locale | every text field (incl. list items, duration, cancellation_policy) | D5 |
| 6 | Policy | contact details, URLs, off-platform booking; prices in title/short/description | D6 |
| 7 | Category & tags | vs the listing content, using the vocabularies in `taxonomy.yaml` | D7 |
| 8 | Duplicates | a sentence/paragraph repeated **within** one field | D8 |

**When to stop:** after pass 8. Every candidate is now a label, an unsure entry
(§4), or dismissed. If you are still undecided after a second read, mark it
unsure. Don't do a third read.

**Multiple defects:** label each defect type that applies. A listing can carry
D1 + D4 + D8 at once. Within a type, add every occurrence as another span on the
same label.

**Clean listings:** "no defects" is a valid and common outcome. Record it
explicitly (the labeling tool has a *Clean* button); don't invent a minor issue
to justify the review time. Weak copy, wordiness and tone are **not** defects in v1.

---

## 2. Marking field and span

- `spans` is a list of `{field, span}`, at least one per label.
- `span` is copied **verbatim** from the listing: same characters, case and
  punctuation. Take the shortest excerpt that shows the defect (a phrase or a
  sentence, not the whole description).
- **List fields** (`inclusions`, `exclusions`, `tags`): `field` = the list name,
  `span` = the exact item text.
- **Attributes:** `field` = `attributes.<key>`, `span` = the value as written
  (e.g. `attributes.skip_the_line`, `false`).
- **Empty or absent field:** `span` = `""`. **Placeholder:** `span` = the
  placeholder as written (`"TBD!!!"`).
- **D2:** at least two spans, one for each side of the contradiction.
- **D4:** span the promise only. The missing inclusion has nothing to quote.
- **D1 / D7 with several affected fields or tags:** still one label, one span each.

---

## 3. Defect reference

### D1 — Missing required field · critical · rule

**Definition.** A required field is empty, missing, or nothing but a placeholder,
or any text field contains "lorem ipsum".
Required: title, short_description, description, duration, meeting_point,
inclusions, cancellation_policy, category, locale.

**Borderline rules**
- Placeholders count only as the **whole** field value, case-insensitive,
  ignoring trailing punctuation: `TBD`, `tbc`, `N/A`, `n. a.`, `folgt`,
  `siehe oben`, `see above`, `xxx`, `-`. So `"TBD!!!"` and `"XXX"` are D1.
- "lorem ipsum" counts **anywhere** in any text field, even next to real text.
- An empty `inclusions` list is D1. Empty `exclusions` or `tags` is never D1 (optional).

**Not D1**
- A present but vague or wrong value, e.g. meeting point "city centre". That's D2 or D3, or no defect at all.
- A placeholder word inside real text ("…see above for times"). That's no defect.
- A placeholder-only title like "TBD!!!" is D1 **only**, never D8 as well.

**Examples**
- EN: `inclusions: []` → D1, spans `[{inclusions, ""}]`.
- DE: `meeting_point: "Siehe oben."` → D1, spans `[{meeting_point, "Siehe oben."}]`.

### D2 — Internal inconsistency · critical · llm

**Definition.** Two parts of the same listing state facts that cannot both be
true. Both sides are in the listing: fields, list items or attributes.

**Borderline rules**
- Promised in the description **and** listed in exclusions → D2.
- Listed in inclusions **but** described as costing extra → D2.
- A claim contradicted by an attribute (claim "skip-the-line", `skip_the_line: false`) → D2.
- A duration tag (Half-day, Full-day, Multi-day) that contradicts `duration` → D2.
- Overlapping ranges are compatible: "2–3 hours" vs 2.5 h, "half-day" vs 4 h.

**Not D2**
- A claim with no support anywhere is **D3**.
- A promised item that is simply absent from inclusions is **D4**.

**Examples**
- EN: short_description `"Small groups of max. 8 guests"` / description `"…groups of up to 15 people"` → D2, two spans.
- DE: short_description `"Geeignet ab 6 Jahren"` / description `"Mindestalter 12 Jahre."` → D2, two spans.

### D3 — Unsupported / misleading claim · critical · llm

**Definition.** A factual or benefit claim that nothing in the listing backs up,
or that overstates the product. Typical claims: skip-the-line, private, small
group, hotel pickup, wheelchair accessible, guaranteed sighting, "official", "best price".

**Borderline rules**
- Check `attributes` first. A claim backed by a true attribute is fine.
- Guarantees of outcomes outside the operator's control (weather, wildlife) are
  D3 unless qualified ("sightings likely but not guaranteed" is fine).
- Subjective marketing ("stunning", "unforgettable") is never D3.

**Not D3**
- A claim that another field or attribute **contradicts** is **D2**.
- A promised item missing from inclusions is **D4**. Hotel pickup can look like both D3 and D4; see §5 and gap G1.

**Examples**
- EN: description `"Fully wheelchair accessible route"` / attributes `{}` → D3, spans `[{description, "Fully wheelchair accessible route"}]`.
- DE: title `"Kleingruppen-Tour durch die Alhambra"` / attributes `{}` → D3, span on the title.

### D4 — Inclusion/exclusion mismatch · critical · llm

**Definition.** The description or short description promises an item (food,
drinks, tickets, transport, equipment, guide) that `inclusions` doesn't mention.

**Borderline rules**
- Synonyms count as listed: "tasting of 5 wines" covers "Wine".
- An item clearly marked optional or extra ("optional", "gegen Aufpreis") isn't promised, so it's not D4.

**Not D4**
- Promised **and** in exclusions → **D2**.
- Included but described as a paid extra → **D2**.

**Examples**
- EN: short_description `"Includes a glass of prosecco on arrival"` / inclusions `["Guide", "Boat ride"]` → D4, span the short_description phrase.
- DE: description `"Leihfahrrad und Helm inklusive"` / inclusions `["Leihfahrrad", "Reiseleitung"]` → D4, span `"Leihfahrrad und Helm inklusive"` (the helmet is missing).

### D5 — Localization error · major · both

**Definition.** Text that doesn't fit the listing's locale. Either it's in the
other language (a fragment or a whole field), or a time, currency, number or
date is in the wrong format.

| | de-DE | en-GB |
|---|---|---|
| time | 14:00 Uhr | 2:00 pm or 14:00 |
| currency | 25 € / 25,00 € | €25 |
| number | 1.234,50 | 1,234.50 |
| date | 24.12.2026 | 24 December 2026 |

**Borderline rules**
- Proper nouns, brand names and established loanwords ("Tour", "Ticket",
  "Highlights", "Street-Food" in DE) are fine.
- Fees in `cancellation_policy` are allowed, and their **format** is checked here.
- Category and tag values come from fixed, locale-independent lists. An English
  category on a DE listing is **not** D5.
- Meaning-changing mistranslation is **out of scope** for v1. Don't label it.

**Not D5**
- A price in title, short_description or description is **D6 only**. Don't also label D5 for its format.

**Examples**
- EN: en-GB listing, `duration: "3 Stunden"` → D5, spans `[{duration, "3 Stunden"}]`.
- DE: de-DE listing, `cancellation_policy: "Stornogebühr €10 bei Absage unter 24 h"` → D5 (should be `10 €`), span `"€10"`.

### D6 — Policy violation · critical · both

**Definition.** Phone numbers, email addresses, external URLs or social handles.
Requests to book or pay off-platform. Prices or discounts in title,
short_description or description.

**Borderline rules**
- Prices and discounts count **only** in title, short_description and description.
- Fees in `cancellation_policy` are allowed ("50 % fee within 24 h", "10 € Stornogebühr").
- A street address in `meeting_point` is fine. A phone number there is D6.
- "Entry fee not included" with no amount is fine.
- Discriminatory wording is **out of scope** for v1 (candidate D9). Don't label it.

**Not D6**
- A wrongly formatted fee in `cancellation_policy` is **D5**.
- "Best price guaranteed" with no amount is an unsupported claim, so it's **D3**.

**Examples**
- EN: description `"Message us on WhatsApp to book direct and save"` → D6 (off-platform booking).
- DE: title `"Berlin Stadtrundfahrt – nur 19 €"` → D6, span `"nur 19 €"`.

### D7 — Category/tag mismatch · major · llm

**Definition.** The category or a tag clearly doesn't fit what the listing is.
Judge it against the `categories` (12) and `tags` (30) lists in `taxonomy.yaml`
and against the title and description. Also D7: a value that is unsafe or
misleading for its audience (an alcohol product tagged "Kids activities").

**Borderline rules**
- Only label **clear** mismatches. Broad but true tags are fine ("Outdoor" on a walking tour).
- Compare category and tags with the content, not with each other.

**Not D7**
- A duration tag that contradicts `duration` is **D2**.

**Examples**
- EN: title `"Pasta-Making Class in Trastevere"` / category `"Water Activities"` → D7, spans `[{category, "Water Activities"}]`.
- DE: title `"Schnorchelausflug zur Blauen Lagune"` / tags `["Snorkelling", "Indoor"]` → D7, spans `[{tags, "Indoor"}]`.

### D8 — Style/readability · minor · rule

**Definition.** Mechanical style problems in real (non-placeholder) content:
- an ALL-CAPS title
- a title longer than 80 characters (after trimming)
- emoji in the title
- repeated punctuation ("!!!")
- a sentence or paragraph duplicated **within one field**

**Borderline rules**
- The caps rule applies to the **title only**. ALL-CAPS means at least 50% of
  counted words are in caps. All-caps tokens of 4 characters or fewer (VIP, BBQ,
  NYC, DB) and `UNESCO` aren't counted at all. If no counted words remain, it's not D8.
- Several D8 problems in one listing still make one label, with one span each.

**Not D8**
- A placeholder-only field ("TBD!!!") is **D1** only.
- Text copied **between** fields (short_description repeated in description) is not a defect in v1.
- Tone, wordiness or weak copy are not defects.

**Examples**
- EN: title `"Full-Day Cotswolds Villages Tour from London with Afternoon Tea, Lunch and Guided Walks"` (87 chars) → D8, span the title.
- DE: title `"Bootsfahrt auf dem Bodensee 🚤"` → D8 (emoji in title), span the title.

---

## 4. When to mark "unsure"

Use `data/labels/unsure.jsonl` instead of forcing a label when:
- the taxonomy doesn't cover the case (see §6 gaps);
- deciding needs knowledge from outside the listing (is this tour really "official"?);
- two types fit and the tie-break table doesn't settle it;
- you'd likely pick a different answer on a second day.

Record one line per uncertain candidate:
`{"listing_id": "L042", "candidate_defect_ids": ["D3", "D4"], "reason": "Hotel pickup promised, not in inclusions, no attribute"}`

The rest of the listing is labelled in gold as usual. Only the uncertain
candidate stays out of gold. Every unsure entry must be resolved before the
dev/test split is frozen: it becomes a gold label or is dropped. If resolving
it needs a new rule, that's a taxonomy change; log it in `failure_patterns.md`.
If many listings end up unsure for the same reason, stop and fix the taxonomy
before you label more.

---

## 5. Tie-break table

| Situation | Label | Why |
|---|---|---|
| Claim contradicted by another field or an attribute | D2 | Both sides are in the listing |
| Claim with no support anywhere | D3 | Nothing contradicts it, nothing backs it |
| Claim backed by a true attribute | — | Supported |
| Promised item listed in exclusions | D2 | Direct contradiction |
| Promised item absent from inclusions | D4 | Missing, not contradicted |
| Item in inclusions, but description says it costs extra | D2 | Direct contradiction |
| Item marked "optional / gegen Aufpreis", not in inclusions | — | Not promised |
| Price in title / short_description / description, any format | D6 only | D6 absorbs the D5 format issue |
| Fee in cancellation_policy, wrong locale format | D5 | Fee allowed; format still checked |
| Fee in cancellation_policy, correct format | — | Allowed |
| Field is only a placeholder, even with "!!!" or caps | D1 only | D8 applies to real content only |
| Real title in caps or with "!!!" | D8 | |
| Duration tag contradicts the duration field | D2 | Not D7 |
| Promised item that is also an unsupported claim (e.g. hotel pickup) | unsure | Gap G1 |

---

## 6. label_source

| Value | Meaning |
|---|---|
| `seeded` | Planted deliberately during data generation (`seeded.jsonl`). Shown pre-filled in the tool. **Not** ground truth until you review it. |
| `confirmed` | A seeded label you reviewed and agree with. You may correct its spans. |
| `added_by_human` | A defect you found that wasn't seeded. |

- **Rejecting a seed:** leave it out of gold. `seeded.jsonl` keeps the record,
  so the precision of the seeding can still be measured.
- **Re-typing a seed** (e.g. seeded D3, but it's really D2): drop the D3 and add
  the D2 as `added_by_human`.
- Generated listings often contain defects nobody planted. Don't assume the
  seeds are complete.
- **Self-agreement (κ):** relabel 30 listings a few days later without looking
  at your first labels. Target κ ≥ 0.70 per defect type.

---

## 7. Gaps and contradictions still open in taxonomy.yaml

Until each gap is resolved, mark affected cases unsure (§4).

- **G1 — D3 vs D4 overlap.** "Hotel pickup" (and "guide") appear in D3's list
  of claims and also as D4 items. When it's promised, missing from inclusions
  and has no attribute, both definitions fit, and no rule picks one.
- **G2 — Values outside the vocabulary.** D7 is judged against the fixed
  `categories`/`tags` lists, but nothing says what to do with a value that
  isn't in either list: D7, a different defect, or a data error.
- **G3 — Format strictness in D5.** The conventions give one standard form per
  locale. It isn't stated whether close variants are errors: de-DE `14 Uhr` or
  `14:00` without "Uhr"; en-GB `2pm`; `EUR 25` in either locale.
- **G4 — Where contact details count in D6.** Prices are limited to three
  fields. For phone numbers, emails and URLs, only `meeting_point` is mentioned.
  It isn't stated whether a phone number in `cancellation_policy` or an
  inclusion item is D6.
- **G5 — Discount with no amount.** "Rabatt", "special offer" or "20 % off" in
  a description: the D6 definition says "prices or discounts", but it isn't
  clear whether a discount without a number counts, or where it stops being
  "best price" (D3).
- **G6 — Counting rules for the D8 caps ratio.** It isn't defined what counts
  as a "word": digits, hyphenated tokens like "3-HOUR", "&". It also isn't
  defined what counts as an "emoji": symbols like ★ ✓ ®.
- **G7 — D1 for `locale` can't happen.** `Listing.locale` only accepts
  `en-GB`/`de-DE`, so a listing with a missing or invalid locale fails schema
  validation before any rule runs. There's no defined way to label it as D1.
- **G8 — Promises in the title.** D4 covers promises in the description and
  short description only. It isn't stated whether "Wine Tasting with Lunch" in
  the title, with no lunch in inclusions, is D4.
- **G9 — `seeded` in gold.** `GoldLabel.label_source` allows `seeded`, but the
  gold set is supposed to be human-validated. This guide assumes the frozen
  `gold.jsonl` holds only `confirmed` and `added_by_human`. Please confirm.
