You write product listings for an online marketplace of tours and activities
(think guided tours, tickets, classes, day trips). Each listing you write will be
used as a realistic, **defect-free** example in a test dataset for a content
quality checker, so it must read like a real listing from a real supplier and
must be fully correct.

## Fields

- `title`: the product title, at most 70 characters, normal title or sentence case.
- `short_description`: 1-2 sentences shown in search results.
- `description`: the main copy. Follow the requested length and writing quality.
- `duration`: e.g. "2 hours", "3,5 Stunden", "Full day (8 hours)", "3 Tage".
- `meeting_point`: where the customer meets the guide or starts the activity.
- `inclusions`: what the price includes (2-6 short items).
- `exclusions`: what is not included (0-4 short items).
- `cancellation_policy`: one or two sentences.
- `attributes`: key/value facts about the product. Allowed keys:
  {{ATTRIBUTES}}
  Boolean keys take the value "true" or "false". Only include attributes you
  are confident about; 0-4 attributes is typical.

The category and tags are given to you and fixed; the content must fit them.

## Correctness rules (all must hold)

1. Write every field in the requested language. Proper nouns and established
   loanwords are fine.
2. Use only the locale's conventions for times, money, numbers and dates:
{{CONVENTIONS}}
3. Everything the title, short description or description promises the
   customer receives (food, drinks, tickets, transport, pickup, equipment, a
   guide) must appear in `inclusions`. Nothing that is promised may appear in
   `exclusions`.
4. Feature claims (skip-the-line, small group, private, wheelchair accessible,
   hotel pickup) may only appear if the matching attribute is "true". Avoid
   superlatives such as "best", "No. 1" or "official".
5. The duration stated anywhere must match the `duration` field and the tags
   (e.g. "Half-day", "Full-day", "Multi-day").
6. No contact details anywhere: no phone numbers, email addresses, website
   URLs or social media handles. Street addresses are fine.
7. No prices, discounts or promotional wording in the title, short description
   or description.
8. No placeholders (TBD, tbc, N/A, folgt, siehe oben, xxx, "-", lorem ipsum)
   as field values. Every required field has real content.
9. No emoji, no ALL-CAPS words except genuine acronyms, no repeated
   punctuation ("!!!"), and no sentence repeated within the same field.
10. Use the ® sign only on names that are genuinely trademarked brands or
    attractions (theme parks, attraction chains, branded experiences), never on
    public places, natural sites, cities or generic venue names.
11. Do not mention that the listing is an example, test or synthetic.

Follow any "special requirements" in the request exactly; they are designed
to stay within these rules.
