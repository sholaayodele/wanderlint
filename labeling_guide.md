# Wanderlint labeling guide (taxonomy v1)

For the human annotator building `data/labels/gold.jsonl`. Authority order:
`taxonomy.yaml` → CLAUDE.md §3 → this guide. If this guide and the taxonomy
disagree, the taxonomy wins and this guide is a bug.

---

## Cheat sheet

Unit = **(listing, defect type)**. One label per type, any number of spans. "Clean" is a valid answer.

| Defect | Test: label it if… | Tie-breaks / not this |
|---|---|---|
| **D1** Missing field · crit | a required field (title, short_description, description, duration, meeting_point, inclusions, cancellation_policy, category) is empty, `[]`, or **only** a placeholder (`TBD`, `tbc`, `N/A`, `n. a.`, `folgt`, `siehe oben`, `see above`, `xxx`, `-`, trailing punctuation ignored); or "lorem ipsum" appears **anywhere** | Placeholder-only field (`"TBD!!!"`) → D1, never D8 · vague but real value → not D1 · bad locale → ingestion error, not labelled |
| **D2** Inconsistency · crit | two parts of the listing (fields, list items, attributes) can't both be true | Promised + in exclusions → D2 · included but "costs extra" → D2 · claim vs false attribute → D2 · duration tag vs duration → D2 |
| **D3** Unsupported claim · crit | a **qualitative/feature** claim (skip-the-line, small group, private, accessible, "official", superlatives, guaranteed sightings) has no support | Contradicted → D2 · a thing the customer **receives** → D4 · price/promo wording → D6 · subjective fluff → nothing |
| **D4** Inclusion mismatch · crit | title, short_description or description promises something the customer **receives** (food, drinks, tickets, transport, pickup, equipment, guide) that inclusions don't mention | In exclusions → D2 · marked "optional / gegen Aufpreis" → nothing · synonyms count as listed |
| **D5** Localization · major | text in the other language, or a format from the **other** locale's convention | Allowed variants are fine (DE `14 Uhr`, EN `2pm`, `EUR 25` both) · price in title/short/description → D6 only · category/tag values are locale-free · mistranslation → out of scope |
| **D6** Policy · crit | contact details (phone, email, URL, handle) in **any** text field; price or **any** discount/promo language in title/short/description; off-platform booking | Street address ≠ contact details · fees in cancellation_policy allowed (format → D5) · discriminatory wording → out of scope |
| **D7** Category/tag · major | category/tag value **not in the vocabularies**, or clearly wrong for the content, or unsafe for its audience | Broad but true → nothing · duration tag vs duration → D2 |
| **D8** Style · minor | title ALL-CAPS (≥ 50% of counted words), > 80 chars, contains emoji; "!!!"; sentence/paragraph repeated **within one field** | Placeholder-only → D1 · cross-field copy → nothing · tone/weak copy → nothing |

**Unsure?** Write it to `unsure.jsonl` (§4), never force it. **Spans:** verbatim, shortest excerpt, `""` for empty fields (§2).

---

## 1. Workflow per listing

Note the **locale** first. Then go through these passes in order. Don't skip a
pass because you've already found a defect.

| # | Pass | Look at | Defects |
|---|---|---|---|
| 1 | Required fields | each required field has real content; "lorem ipsum" anywhere | D1 |
| 2 | Title mechanics | caps, length, emoji, "!!!" | D8 |
| 3 | Read the copy | title, short_description, description — note every claim and every promised deliverable | — |
| 4 | Cross-check | claims vs fields and `attributes`; deliverables vs inclusions/exclusions | D2, D3, D4 |
| 5 | Locale | every text field, incl. list items, duration, cancellation_policy | D5 |
| 6 | Policy | contact details in every field; price/promo wording in title/short/description; off-platform booking | D6 |
| 7 | Category & tags | in the vocabularies? fit the content? | D7 |
| 8 | Duplicates | a sentence/paragraph repeated within one field | D8 |

**Stop** after pass 8. Every candidate should now be a label, an unsure entry,
or dismissed. If you're still undecided after a second read, mark it unsure.
Don't do a third read.

**Multiple defects:** label every type that applies (e.g. D1 + D4 + D8). Repeat
occurrences of the same type become extra spans on that type's label.

**Clean listings** are common and valid. Mark them *Clean*. Weak copy, wordiness
and tone are not defects in v1.

---

## 2. Marking field and span

- `spans` is a list of `{field, span}`, with at least one entry. `span` is
  copied **verbatim** (same characters, case and punctuation). Take the
  shortest excerpt that shows the defect.
- **List fields:** `field` = list name, `span` = the exact item text.
- **Attributes:** `field` = `attributes.<key>`, `span` = the value as written.
- **Empty field:** `span` = `""`. **Placeholder:** `span` = the placeholder as written.
- **D2:** at least two spans, one for each side of the contradiction.
- **D4:** span the promise only. The missing inclusion has nothing to quote.
- **D1 / D7 / D8 with several hits:** one label, one span per hit.

---

## 3. Defect reference

### D1 — Missing required field · critical · rule
**Definition.** A required field is empty, `[]`, or nothing but a placeholder,
or any text field contains "lorem ipsum".
**Rules**
- Placeholders match the whole value, case-insensitive, ignoring trailing
  punctuation (`"TBD!!!"`, `"XXX"`).
- "lorem ipsum" counts anywhere, even next to real text.
- Empty `exclusions` or `tags` is fine (they're optional).
- `locale` isn't a D1 field. A missing or invalid locale is an ingestion error,
  reported separately and not labelled.

**Not D1**
- Vague but real values ("city centre"): consider D2/D3, or no defect.
- A placeholder word inside real text: no defect.
- A placeholder-only field is never also D8.

**Examples**
- EN: `inclusions: []` → D1, `[{inclusions, ""}]`.
- DE: `meeting_point: "Siehe oben."` → D1, `[{meeting_point, "Siehe oben."}]`.

### D2 — Internal inconsistency · critical · llm
**Definition.** Two parts of the listing state facts that can't both be true.
**Rules**
- Promised and also in exclusions → D2.
- Included but described as costing extra → D2.
- A claim contradicted by an attribute → D2.
- A duration tag that contradicts `duration` → D2.
- Overlapping ranges are compatible ("2–3 hours" vs 2.5 h).

**Not D2:** no support at all → **D3** · deliverable simply missing from inclusions → **D4**.
**Examples**
- EN: short_description `"Small groups of max. 8 guests"` / description `"…groups of up to 15 people"` → D2, two spans.
- DE: short_description `"Geeignet ab 6 Jahren"` / description `"Mindestalter 12 Jahre."` → D2, two spans.

### D3 — Unsupported / misleading claim · critical · llm
**Definition.** A qualitative or feature claim about the experience that
nothing in the listing supports, or that overstates the product.
**Rules**
- Check `attributes` first. A claim backed by a true attribute is fine.
- Guaranteed outcomes outside the operator's control (weather, wildlife) are D3 unless qualified.
- Subjective marketing ("stunning") is never D3.

**Not D3**
- Contradicted → **D2**.
- Something the customer receives (pickup, lunch, tickets) → **D4**.
- "Best price" or other price/promo wording → **D6**.

**Examples**
- EN: description `"Fully wheelchair accessible route"` / attributes `{}` → D3.
- DE: title `"Kleingruppen-Tour durch die Alhambra"` / attributes `{}` → D3.

### D4 — Inclusion/exclusion mismatch · critical · llm
**Definition.** The title, short description or description promises
something the customer receives (food, drinks, tickets, transport, pickup,
equipment, guide) that `inclusions` doesn't mention.
**Rules**
- Synonyms count as listed ("tasting of 5 wines" covers "Wine").
- An item clearly marked optional or extra isn't promised.

**Not D4**
- In exclusions → **D2**.
- Included but described as a paid extra → **D2**.
- A feature claim (skip-the-line, small group) → **D3**.

**Examples**
- EN: title `"Douro Valley Wine Tour with Lunch"` / inclusions `["Guide", "Wine tasting"]` → D4, `[{title, "with Lunch"}]`.
- DE: short_description `"Inklusive Abholung vom Hotel"` / inclusions `["Reiseleitung", "Eintritt"]` → D4.

### D5 — Localization error · major · both
**Definition.** Text in the other language (a fragment or a whole field), or a
time, currency, number or date written in the **other** locale's convention.

| | de-DE allowed | en-GB allowed |
|---|---|---|
| time | 14:00 Uhr · 14 Uhr · 14:00 | 2:00 pm · 2pm · 14:00 |
| currency | 25 € · 25,00 € · EUR 25 | €25 · EUR 25 |
| number | 1.234,50 | 1,234.50 |
| date | 24.12.2026 | 24 December 2026 |

**Rules**
- D5 only when the other locale's convention appears (`€25` or `2:00 pm` in de-DE;
  `25 €`, `14:00 Uhr` or `24.12.2026` in en-GB).
- A format that is in neither list (e.g. `14h`) is a stylistic variant, so not D5.
- Loanwords, brand names and proper nouns are fine ("Tour", "Ticket", "Highlights").
- Fees in `cancellation_policy` are allowed, and their format is checked here.
- Category and tag values are locale-free, so an English category on a DE listing is fine.
- Meaning-changing mistranslation is out of scope for v1.

**Not D5:** a price in title, short description or description → **D6 only**.
**Examples**
- EN: en-GB listing, `duration: "3 Stunden"` → D5.
- DE: `cancellation_policy: "Stornogebühr €10 bei Absage unter 24 h"` → D5, span `"€10"`.

### D6 — Policy violation · critical · both
**Definition.**
- Contact details (phone, email, external URL, social handle) in **any** text field.
- Prices or **any** discount or promotion language in title, short description or description.
- Requests to book or pay off-platform.

**Rules**
- Contact details count in every field, including `meeting_point` and
  `cancellation_policy`. Street addresses are not contact details.
- Promotion language counts even without an amount: "special offer",
  "nur heute", "Rabatt", "best price guaranteed".
- Fees in `cancellation_policy` are allowed. "Entry fee not included" with no amount is fine.
- Discriminatory wording is out of scope for v1 (candidate D9).

**Not D6:** a wrongly formatted cancellation fee → **D5**.
**Examples**
- EN: `cancellation_policy: "To cancel, call +44 20 7946 0000"` → D6.
- DE: description `"Nur heute: Frühbucher-Rabatt!"` → D6 (promotion, no amount needed).

### D7 — Category/tag mismatch · major · both
**Definition.** A category or tag value that isn't in the `taxonomy.yaml`
vocabularies (12 categories, 30 tags), or that clearly doesn't fit the title
and description, or that's unsafe for its audience (alcohol tagged "Kids activities").
**Rules**
- A value outside the vocabularies is always D7.
- Content mismatches must be **clear**; broad but true tags are fine.
- Compare against the content, not against the other tags.

**Not D7:** a duration tag that contradicts `duration` → **D2**.
**Examples**
- EN: tags `["Walking tour", "Foodie"]` → D7, `[{tags, "Foodie"}]` ("Foodie" isn't in the vocabulary).
- DE: title `"Schnorchelausflug zur Blauen Lagune"` / tags `["Snorkelling", "Indoor"]` → D7, `[{tags, "Indoor"}]`.

### D8 — Style/readability · minor · rule
**Definition.** Mechanical problems in real (non-placeholder) content:
- an ALL-CAPS title
- a title over 80 characters (after trimming)
- emoji in the title
- repeated punctuation ("!!!")
- a sentence or paragraph duplicated within one field

**Rules**
- The caps ratio applies to the title only.
  - A **word** is a whitespace-separated token with at least one letter.
  - A caps word has all its letters uppercase.
  - All-caps tokens of 4 characters or fewer, and `UNESCO`, aren't counted.
  - It's D8 at 50% or more. If no counted words remain, it's not D8.
- **Emoji** means any Unicode `Extended_Pictographic` character. That includes
  © ® ™, so "LEGOLAND®" in a title is D8.

**Not D8**
- A placeholder-only field → **D1**.
- Text copied between fields: no defect.
- Tone or weak copy: no defect.

**Examples**
- EN: title `"Full-Day Cotswolds Villages Tour from London with Afternoon Tea, Lunch and Guided Walks"` (87 chars) → D8.
- DE: title `"Bootsfahrt auf dem Bodensee 🚤"` → D8.

---

## 4. When to mark "unsure"

Write to `data/labels/unsure.jsonl` instead of forcing a label when:
- the taxonomy doesn't cover the case;
- deciding needs knowledge from outside the listing (is this tour really "official"?);
- two types fit and the cheat sheet doesn't settle it;
- you'd likely pick differently on another day.

`{"listing_id": "L042", "candidate_defect_ids": ["D3", "D7"], "reason": "…"}`

Label the rest of the listing in gold as usual. Only the uncertain candidate
stays out. Resolve every unsure entry before the dev/test split is frozen: it
becomes a gold label or is dropped. If resolving it needs a new rule, that's a
taxonomy change; log it in `failure_patterns.md`. If many listings end up
unsure for the same reason, stop and fix the taxonomy before you label more.

---

## 5. Tie-break table

| Situation | Label |
|---|---|
| Claim contradicted by another field or a false attribute | D2 |
| Feature/qualitative claim with no support | D3 |
| Claim backed by a true attribute | — |
| Deliverable promised, absent from inclusions | D4 |
| Deliverable promised, listed in exclusions | D2 |
| Item in inclusions, described as costing extra | D2 |
| Item marked "optional / gegen Aufpreis", not in inclusions | — |
| Hotel pickup promised, not in inclusions, no attribute | D4 (deliverable) |
| "Skip-the-line" with no attribute | D3 (feature) |
| Price in title / short_description / description, any format | D6 only |
| "Best price guaranteed", "Rabatt", "special offer" | D6 |
| Fee in cancellation_policy, other locale's format | D5 |
| Fee in cancellation_policy, allowed format | — |
| Phone number or email in meeting_point / cancellation_policy | D6 |
| Field is only a placeholder, even with "!!!" or caps | D1 only |
| Real title in caps or with "!!!" | D8 |
| Duration tag contradicts the duration field | D2 |
| Category/tag value not in the vocabularies | D7 |

---

## 6. label_source

| Value | Meaning |
|---|---|
| `seeded` | Planted during data generation (`seeded.jsonl`). Pre-filled in the tool. **Not** ground truth until you review it, and never in the frozen gold. |
| `confirmed` | A seeded label you reviewed and agree with. You may correct its spans. |
| `added_by_human` | A defect you found that wasn't seeded. |

- The **frozen `gold.jsonl`** contains only `confirmed` and `added_by_human`.
- **Rejecting a seed:** leave it out of gold and log it in
  `data/labels/rejected_seeds.jsonl` with a reason:
  `{"listing_id": "L042", "defect_id": "D3", "reason": "Backed by attribute small_group=true"}`.
- **Re-typing a seed** (seeded D3, really D2): log the D3 as rejected and add
  the D2 as `added_by_human`.
- Generated listings often contain defects nobody planted. Don't assume the
  seeds are complete.
- **Self-agreement (κ):** relabel 30 listings a few days later without looking
  at your first labels. Target κ ≥ 0.70 per type.

---

## 7. Open points

No gaps that block labeling. Points to confirm:

- **® and ™ in titles count as D8.** They're `Extended_Pictographic`, so a
  trademarked name like "LEGOLAND®" in a title is D8.
- **"≤ 4 characters" for acronyms:** it isn't stated whether punctuation
  counts. "BBQ!!!" is 6 characters with punctuation and 3 without.
