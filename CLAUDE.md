# CLAUDE.md — Wanderlint

> Quality checks for travel content, at catalog scale.

This file is the project charter and the working agreement for Claude Code.
Read it fully before planning or writing code. When anything here conflicts
with a request, ask before proceeding.

---

## 1. What Wanderlint is

Wanderlint is an LLM-powered quality checker for **tours & activities product
listings** (content management, merchandising, localization). It reviews a
listing, returns structured defects with evidence and suggested fixes, and
routes each listing to auto-pass, auto-flag, or human review.

It ships **with its own evaluation**: every claim about quality is backed by
precision and recall measured against a validated gold dataset.

Two goals, in priority order:
1. **Credible eval portfolio project** — the eval methodology is the headline,
   not the checker.
2. **Foundation for a real product** — code should be clean enough to grow into
   a SaaS (CLI first, API later). Don't over-engineer for this yet.

### Who it serves
| Persona | Need | Run mode |
|---|---|---|
| Content manager | Review freelancer-written listings before publishing | `freelancer-batch` |
| Supplier / partner manager | Send suppliers a concrete fix list for their feed | `supplier-feed` |
| Merchandising / catalog lead | Know catalog health per category & locale over time | `full-cycle` |

---

## 2. Scope

### In scope (v1)
- Text fields of a single listing, one listing per check.
- Locales: `en-GB`, `de-DE` (approx. 70/30 split in data).
- The 8 defect types in §3.
- Deterministic rules + one structured LLM call per listing.
- Offline eval harness: precision, recall, F1 per defect type and severity.
- CLI with three run modes and confidence-based routing.

### Out of scope (v1) — do not build unless asked
- Images, video, pricing logic, availability/inventory.
- Live supplier APIs, CMS integrations, auth, multi-tenant, web UI
  (a Streamlit labeling tool and a static HTML report are fine).
- Auto-fixing listings (planned extension, see §11).
- Locales beyond EN/DE.

---

## 3. Definition of "good": defect taxonomy

Source of truth: `taxonomy.yaml`. This table is a summary — if they differ,
`taxonomy.yaml` wins and this table must be updated.

| ID | Defect | Severity | Detection | Example |
|---|---|---|---|---|
| D1 | Missing required field | critical | rule | No meeting point or cancellation policy; field is only a placeholder ("TBD", "folgt") |
| D2 | Internal inconsistency | critical | llm | Title "3-hour tour", duration field 2h |
| D3 | Unsupported / misleading claim | critical | llm | "Skip-the-line" with no skip-the-line attribute |
| D4 | Inclusion/exclusion mismatch | critical | llm | Title or description promises lunch / pickup; inclusions don't list it |
| D5 | Localization error | major | both | Untranslated EN fragment in DE listing; wrong time/currency/number/date format |
| D6 | Policy violation | critical | both | Contact details in any text field; price or promotion language in title/short description/description |
| D7 | Category/tag mismatch | major | both | Wine tasting tagged "Kids activities"; value not in the fixed vocabularies (rule) |
| D8 | Style/readability | minor | rule | ALL-CAPS title, title > 80 chars, sentence/paragraph duplicated within one field, repeated punctuation ("!!!"), emoji in title |

**Required fields (D1):** title, short_description, description, duration,
meeting_point, inclusions, cancellation_policy, category. Empty strings,
empty lists, and whole-value placeholders count as missing: `TBD`, `tbc`,
`N/A`, `n. a.`, `folgt`, `siehe oben`, `see above`, `xxx`, `-`
(case-insensitive). `lorem ipsum` anywhere in a text field also counts
(substring match). `exclusions` and `tags` are optional. A missing or invalid
`locale` is an ingestion error, reported separately and not scored.

**Caps rule (D8):** title only; ALL-CAPS if ≥ 50% of counted words are caps.
All-caps tokens of ≤ 4 characters (VIP, BBQ, DB) and whitelisted acronyms
(`UNESCO`) are excluded from the ratio. A word is a whitespace-separated token
with ≥ 1 letter. Leading/trailing punctuation is stripped before the length check ("BBQ!!!" →
"BBQ"). Emoji = Unicode `Extended_Pictographic`, excluding © ® ™.
D8 applies only to real content.

**Vocabularies (D7):** `taxonomy.yaml` defines a fixed list of 12 `categories`
and 30 `tags` (locale-independent values). A value outside them is D7.

**Locale conventions (D5)** — allowed formats; D5 only when the other locale's
convention is used, not for stylistic variants:
- `de-DE`: `14:00 Uhr` / `14 Uhr` / `14:00`, `25 €` / `25,00 €` / `EUR 25`, `1.234,50`, dates `24.12.2026`
- `en-GB`: `2:00 pm` / `2pm` / `14:00`, `€25` / `EUR 25`, `1,234.50`, dates `24 December 2026`

**Taxonomy rules**
- A listing can have zero or many defects. "Clean" is a valid, common outcome.
- One defect type per (listing, defect) pair for scoring; multiple spans allowed.
- Overlaps and tie-breaks are documented in `labeling_guide.md`. Example:
  a claim contradicted by another field is D2, a claim with no support at all is D3.
- Promised and listed in exclusions is D2; promised but merely absent from
  inclusions is D4.
- D3 vs D4: something the customer receives (food, drinks, tickets, transport,
  pickup, equipment) is D4; a qualitative/feature claim (skip-the-line, small
  group, private, accessible, superlatives) is D3.
- Contact details are D6 in every text field (street addresses are not contact
  details); any discount/promotion language in title/short/description is D6.
- D1 and D7 score once per listing, however many fields/tags are affected
  (each one is a separate span).
- A price in title, short description or description is D6 only — never also
  D5 for its format. Fees in `cancellation_policy` are allowed (not D6); their
  format is checked for D5.
- Listed in inclusions but described as a paid extra is D2.
- A field whose entire value is a placeholder (incl. trailing punctuation,
  e.g. "TBD!!!", "XXX") is D1 only, never D8.
- D8 duplicates count within a single field only; cross-field copying is not a
  defect in v1.
- Meaning-changing mistranslation is out of scope for v1 (see §11).
- Changing a definition is a **taxonomy change**: bump `taxonomy.yaml` version,
  note it in `failure_patterns.md`, and re-check affected gold labels.

---

## 4. Success metrics

Measured on the **held-out test set**, unit = (listing_id, defect_id) pair.

| Scope | Target |
|---|---|
| Critical defects (D1–D4, D6) | Recall ≥ 0.90 **and** precision ≥ 0.80 (each type) |
| Major & minor (D5, D7, D8) | Precision ≥ 0.85, recall ≥ 0.75 |
| Clean listings | False-flag rate ≤ 10% |
| Cost | ≤ €0.01 per listing (avg) |
| Latency | ≤ 5 s per listing (p95, excluding batch queueing) |
| Routing | ≥ 60% of listings auto-routed (no human review) at the targets above |
| Label quality | Self-agreement (Cohen's κ) ≥ 0.70 per defect on 30 re-labelled listings |

Rationale: missing a critical defect (misleading travelers) costs more than a
false flag, so critical defects favor recall; lower-severity defects favor
precision so reviewers aren't flooded.

---

## 5. Timeline & milestones (~3 weeks, evenings)

| Week | Milestone | Done when |
|---|---|---|
| 1 | M1 — Ground truth | `taxonomy.yaml`, `labeling_guide.md`, 150 listings, `gold.jsonl` human-validated, κ computed, dev/test split frozen |
| 2 | M2 — Baseline | rules + LLM checker run end-to-end on dev; first eval report; baseline test-set report |
| 3 | M3 — Iterate & ship | ≥ 2 prompt iterations logged; regression set + eval gate in CI; 3 run modes + routing; final test-set report; README write-up |

---

## 6. Architecture

```
listing(s) ──► rules.py ──► llm_checker.py ──► merge & dedupe ──► router ──► reports
               (D1,D5,D6,D8)  (D2–D7, given        (Defect list)   (pass /    (per mode)
                              rule results)                         flag /
                                                                    review)
                                   │
                         eval.py ◄─┘  (predictions vs gold → metrics + error analysis)
```

- **Rules first**: cheap, exact, deterministic. Never send a rule-detectable
  defect to the LLM as a primary check.
- **One LLM call per listing** with structured output (tool use / JSON schema),
  rule results passed in so the model doesn't duplicate them.
- **Evidence required**: every LLM defect must quote a span from the listing.
  Defects whose evidence isn't found verbatim in the listing are dropped
  (log the count — it's a hallucination metric).
- **Routing** by per-defect confidence thresholds from `config.yaml`, chosen
  from the eval report's threshold tables, never guessed.

---

## 7. Repository layout

```
wanderlint/
  CLAUDE.md
  README.md
  pyproject.toml
  config.yaml                 # model, thresholds, cost caps, paths
  taxonomy.yaml               # defect definitions (versioned)
  labeling_guide.md
  failure_patterns.md         # living log of failure patterns & fixes
  prompts/
    checker_v1.md             # never edit a released version; add v2, v3…
    datagen_listing_v1.md     # synthetic listing generation (clean)
    datagen_inject_v1.md      # synthetic defect injection
  data/
    listings/                 # dev.jsonl, test.jsonl, raw/ (generated.jsonl, generated_meta.jsonl)
    listings/regression/      # per-failure-pattern listings
    labels/                   # seeded, gold, relabel_kappa, unsure, rejected_seeds (.jsonl)
  src/wanderlint/
    models.py                 # pydantic: Listing, Span, Defect, GoldLabel, UnsureLabel, Taxonomy, …
    rules.py
    llm_checker.py
    router.py
    pipeline.py               # CLI entry: run modes
    eval.py
    cost.py                   # token + € accounting, hard cost cap
    llm.py                    # cached, cost-tracked structured-output API calls
    datagen/                  # synthetic data: vocab, plan (seeded specs), checks, inject, run, audit
  scripts/
    generate_listings.py
    audit_generated.py        # re-checks seeded data (claims, D4 overlap, seams, spans)
    label_app.py              # Streamlit labeling tool
    kappa.py
  reports/                    # {date}_{prompt_version}_{split}.md (+ .json)
  tests/
```

---

## 8. Tech stack & conventions

- Python 3.11, `uv` for env/deps, `pydantic` v2, `anthropic` SDK, `pyyaml`,
  `pandas`, `typer` (CLI), `streamlit` (labeling only), `pytest`, `ruff`.
- **Models** (configurable in `config.yaml`): default checker `claude-sonnet-5-5`;
  cheap pre-screen experiments `claude-haiku-5-5`; data generation may use
  either. Never hard-code model names outside config.
- API key from `ANTHROPIC_API_KEY` env var. Never commit secrets; `.env` is gitignored.
- Data files are JSONL, one object per line, UTF-8.
- Type hints everywhere; small pure functions; no global state.
- Cache LLM responses keyed on `(prompt_version, model, taxonomy_version, listing_hash)`
  so re-running eval doesn't re-spend money.
- Log tokens and € cost per call; every report includes total and per-listing cost.
- Seeds for anything random (data generation, splits, sampling).

### Core schemas (keep in `models.py`)
```python
class Listing:   id, locale, title, short_description, description, duration,
                 meeting_point, inclusions: list[str], exclusions: list[str],
                 cancellation_policy, category, tags: list[str],
                 attributes: dict[str, bool | str]

class Span:      field, span          # verbatim excerpt; "" = field empty/absent

class Defect:    defect_id ("D1".."D8"), severity, spans: list[Span] (≥ 1),
                 evidence, message, suggested_fix | None, confidence (0–1),
                 source ("rule" | "llm")

class GoldLabel: listing_id, defect_id, spans: list[Span] (≥ 1),
                 label_source ("seeded" | "confirmed" | "added_by_human")

                 # frozen gold.jsonl: only "confirmed" | "added_by_human"

class UnsureLabel: listing_id, candidate_defect_ids (≥ 1), reason
                   # data/labels/unsure.jsonl — never part of gold

class RejectedSeed: listing_id, defect_id, reason
                    # data/labels/rejected_seeds.jsonl — seeded labels dropped from gold
```

---

## 9. Evaluation rules (non-negotiable)

1. **Unit of scoring:** (listing_id, defect_id). TP if gold has that pair.
   Field/span-level matching is an optional stricter metric, reported separately.
2. **Dev vs test:** iterate only on `dev`. Run `test` only at milestones
   (baseline M2, final M3). Never tune prompts, thresholds, or rules by looking
   at test-set errors. If test results are viewed in detail, say so in the report.
3. **Gold labels are human-owned.** Claude must never edit `gold.jsonl`
   directly. Propose label changes as a diff in a separate file
   (`data/labels/proposed_changes.jsonl`) with reasons; the human approves.
4. **Prompts are versioned.** Never edit a released `checker_vN.md`. Create
   `vN+1` and record the change in `failure_patterns.md`.
5. **Accept a change only if** the targeted metric improves and no other
   per-defect metric regresses by more than 0.03 on dev + regression sets.
6. **Every failure pattern gets regression coverage:** ≥ 5 labelled listings
   in `data/listings/regression/`.
7. **Every report contains:** summary table vs targets (pass/fail), per-defect
   P/R/F1/support, micro + macro averages, clean-listing false-flag rate,
   precision/recall vs confidence-threshold table, dropped-evidence count,
   cost and latency, and every FP/FN with excerpt, model evidence, and gold label.
8. Report numbers honestly, including misses. Small-sample caveats go in the report.

---

## 10. Commands

```bash
uv sync --all-groups                                 # install (incl. dev + streamlit label group)
make test                                            # = uv run pytest
make lint                                            # ruff check + ruff format --check
make format                                          # ruff format

uv run python scripts/generate_listings.py --n 10 --seed 42 --dry-run   # plan + estimate, no API
uv run python scripts/generate_listings.py --n 150 --seed 42 --yes      # > 20 listings needs --yes
uv run python scripts/audit_generated.py                                 # deterministic audit of generated data
uv run streamlit run scripts/label_app.py
uv run python scripts/kappa.py

uv run wanderlint check --mode freelancer-batch --input data/listings/raw/batch_01/
uv run wanderlint check --mode supplier-feed   --input feeds/supplier_x.csv
uv run wanderlint check --mode full-cycle      --input data/listings/dev.jsonl

uv run wanderlint eval --split dev  --prompt v1
uv run wanderlint eval --split test --prompt v1      # milestones only
make eval-gate                                       # dev + regression, fails on regression
```
(Update this section when commands change.)

---

## 11. Planned extensions (not v1 — don't build unprompted)

- Haiku pre-screen → Sonnet escalation; measure cost vs recall.
- Auto-fix mode with its own eval (defect removed, no new defects, no fact drift).
- LLM-as-judge for suggested fixes, validated against human ratings first.
- New locales (FR first), per-locale metrics.
- Candidate defect type D9 — discriminatory wording (removed from D6 in v1):
  needs dedicated dataset; too rare and sensitive for v1 sample size.
- Meaning-changing mistranslation (removed from D5 in v1): needs source/target
  text pairs.
- Drift monitor on full-cycle runs; mine the human-review queue for new patterns.
- HTML dashboard over `reports/`.
- API + CMS integration if Wanderlint becomes a product.

---

## 12. How Claude should work in this repo

- **Plan first** for anything touching more than one file; show the plan and
  wait for approval.
- Work milestone by milestone (§5). Don't jump ahead to orchestration before
  ground truth and baseline eval exist.
- Write tests with each module; rules must have tests built from
  `taxonomy.yaml` examples.
- Before any run that calls the API on more than 20 listings, state the
  estimated cost and ask.
- Keep changes small and reviewable; summarize what changed and why.
- When an eval reveals errors, **cluster them into failure patterns and
  diagnose root cause (label | definition | model) before changing anything.**
- If a request would break an eval rule in §9, say so and propose an alternative.
- Don't add dependencies without saying why.
- Keep this file current: update §3, §7, §10 when they change.

---

## 13. Glossary

- **Gold set** — human-validated labels; the ground truth.
- **Seeded defect** — a defect deliberately planted during data generation
  (not ground truth until confirmed).
- **Failure pattern** — a recurring, named type of checker error with a root cause.
- **Regression set** — listings that lock in fixes for past failure patterns.
- **Routing** — auto-pass / auto-flag / human-review decision per listing.

## 14. Open decisions

- Span-level scoring: report only, or also gate on it?
- Single LLM call vs one call per defect group: revisit if D5 recall lags.
- Threshold for "clean" when only minor (D8) defects are present.
- Cross-field duplicated text (e.g. short description copied into description):
  defect or not? Not a defect in v1.
