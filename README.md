# Wanderlint

**Quality checks for travel content, at catalog scale.**

> *"The listing said lunch was included."*
>
> Every marketplace support team knows this sentence. It means a traveller trusted a listing, the listing was wrong, and the marketplace is about to pay for it — in a refund, a one-star review, and a customer who books elsewhere next time.

Wanderlint is an LLM-powered quality checker for tours & activities listings. It reads a listing the way an experienced content reviewer would, flags what's wrong with quoted evidence and a suggested fix, and decides whether the listing can go live, needs fixing, or needs a human.

But the checker isn't really the product. **The product is the confidence to trust it.** Wanderlint is built eval-first: every claim it makes about quality is backed by precision and recall measured against a human-validated gold dataset — misses included.

> **Status · Milestone 1 (ground truth) in progress.** Taxonomy v1 is locked. A 150-listing synthetic dataset with 163 planted defects is generated and audited. Blind human validation is next. Checker results will be published here at Milestones 2 and 3.

---

## Contents

1. [The opportunity](#1-the-opportunity)
2. [Who has this problem](#2-who-has-this-problem)
3. [Discovery: what has to be true](#3-discovery-what-has-to-be-true)
4. [Defining "good" before building anything](#4-defining-good-before-building-anything)
5. [Outcomes, not outputs: success metrics](#5-outcomes-not-outputs-success-metrics)
6. [The solution](#6-the-solution)
7. [Evidence over opinion: how it's evaluated](#7-evidence-over-opinion-how-its-evaluated)
8. [Building the ground truth](#8-building-the-ground-truth)
9. [Decisions and trade-offs](#9-decisions-and-trade-offs)
10. [Known limitations](#10-known-limitations)
11. [Roadmap](#11-roadmap)
12. [Repository & quickstart](#12-repository--quickstart)

---

## 1. The opportunity

A tours & activities listing is a contract written in marketing language. In a few hundred words it has to tell a traveller what they'll do, what's included, where to meet, how long it takes, and what happens if plans change. Every one of those facts can be wrong.

And content arrives from everywhere at once:

- **Suppliers** create and edit their own listings, at wildly varying quality.
- **Freelance writers** produce content in batches, under deadline.
- **Localization** multiplies every listing across languages and markets — and multiplies every error with it.

Marketplaces handle this with two tools, and neither scales:

| Today's approach | What it catches | Where it breaks |
|---|---|---|
| **Manual review** | Almost everything, when reviewers are fresh | Cost grows linearly with the catalog; quality drops with fatigue; reviewers disagree |
| **Rule-based checks** | Missing fields, phone numbers, ALL-CAPS titles | Blind to meaning — can't tell that a "3-hour tour" with a 2-hour duration field is a broken promise |

The defects that actually hurt travellers are **semantic**: a claim with nothing behind it, a promise the inclusions don't keep, two fields that contradict each other. That is exactly the gap a language model can fill — *if* it can be trusted.

**The opportunity:** replace the bulk of manual listing review with an AI reviewer that catches semantic defects at scale, routes only genuinely uncertain cases to humans, and proves its reliability with numbers a content team can audit.

---

## 2. Who has this problem

Three people inside a marketplace feel this pain differently, and each needs something different from the same engine:

| Persona | Their job to be done | What "done" looks like | Run mode |
|---|---|---|---|
| **Content manager** | Ship freelancer batches without reading every line | A per-writer report: what's wrong, where, and how to fix it | `freelancer-batch` |
| **Supplier / partner manager** | Get suppliers to fix their own feeds | A prioritised fix list per supplier, ready to send | `supplier-feed` |
| **Merchandising / catalog lead** | Know whether catalog quality is improving or decaying | Defect rates by category, locale and severity, trended over time | `full-cycle` |

Designing for all three from day one shapes the architecture: one checking engine, three ways of packaging its output. The checker never has to know who's asking.

---

## 3. Discovery: what has to be true

Before building, I wrote down what would have to be true for this product to succeed — and how I'd find out cheaply. Good discovery isn't about proving an idea right; it's about finding out fast where it's wrong.

### The four product risks

| Risk | The question | Riskiest assumption | How it's being tested |
|---|---|---|---|
| **Value** | Would a content team actually use this? | Teams will route listings on the checker's verdict if it meets their quality bar | Success metrics defined in reviewer terms (false-flag rate, % auto-routed); human review time per listing measured during labeling as the cost baseline |
| **Usability** | Can a reviewer act on its output in seconds? | Quoted evidence + a suggested fix is enough to act on without re-reading the listing | Every defect must carry a verbatim span; output formats designed per persona |
| **Feasibility** | Can an LLM find *semantic* defects reliably? | It can reach ≥ 0.90 recall on critical defects without drowning reviewers in false flags | The core of the project: a held-out test set, per-defect precision and recall |
| **Viability** | Is it cheaper than a human, at catalog scale? | ≤ €0.01 per listing at acceptable quality | Token and € cost logged on every call; compared against measured human review time |

**Feasibility is the riskiest assumption**, so it's tested first. If an LLM can't reliably separate a real misleading claim from honest marketing copy, nothing else matters — no workflow or UI rescues an untrustworthy reviewer.

### What would make me stop

A discovery plan without kill criteria is a delivery plan in disguise. Wanderlint is not worth pursuing as a product if:

- critical-defect recall plateaus well below 0.90 after error analysis and prompt iteration, **or**
- reaching the recall target pushes the clean-listing false-flag rate far above 10%, **or**
- the share of listings that can be routed without a human stays too low to remove meaningful manual work.

Any of these outcomes is still a useful result: it says *where* AI review breaks down, which is exactly what a content organisation would need to know before investing.

---

## 4. Defining "good" before building anything

The most important decision in a quality product is the definition of quality. If "good" is vague, the checker is unmeasurable, the labels are inconsistent, and every metric is noise. So the first deliverable wasn't code — it was a taxonomy.

| ID | Defect | Severity | Detected by | Example |
|---|---|---|---|---|
| D1 | Missing required field | critical | rules | No meeting point; cancellation policy reads "TBC???" |
| D2 | Internal inconsistency | critical | LLM | "Wheelchair-accessible vehicle", but the accessibility attribute is false |
| D3 | Unsupported or misleading claim | critical | LLM | "Skip-the-line entry" with no skip-the-line attribute |
| D4 | Promised item missing from inclusions | critical | LLM | Description serves "regional snacks"; inclusions don't list food |
| D5 | Localization error | major | rules + LLM | English fragment in a German listing; `2pm` in a `de-DE` listing |
| D6 | Policy violation | critical | rules + LLM | Phone number, external URL, or "only €39" in the description |
| D7 | Category or tag mismatch | major | rules + LLM | Wine tasting tagged "Kids activities" |
| D8 | Style & readability | minor | rules | ALL-CAPS title, "!!!", duplicated sentences |

Severity encodes business impact, not annoyance. A missed critical defect misleads a traveller; a missed minor defect just looks unpolished.

**The categories were the easy part. The boundaries were the work.** Is a claim that another field contradicts a D2 or a D3? Is an item that's promised and then *excluded* a D2 or a D4? Is "TBD!!!" a missing field or a style problem? Each of these was resolved explicitly — 20+ boundary decisions, every one written into the [labeling guide](labeling_guide.md) and logged with its rationale in [`failure_patterns.md`](failure_patterns.md). The taxonomy is versioned in [`taxonomy.yaml`](taxonomy.yaml) and frozen with a `taxonomy-v1` git tag, so every future result can say exactly which definition of "good" it was measured against.

Equally deliberate is what's **out of scope** for v1: images, pricing logic, live supplier integrations, and mistranslation detection (which needs source/target text pairs this dataset doesn't have). Each exclusion is a conscious bet to learn the core question faster.

---

## 5. Outcomes, not outputs: success metrics

"The model flags defects" is an output. The outcome that matters is: **fewer misleading listings reach travellers, with less human effort.** The metrics are built to measure that, and they're set *before* seeing any results.

| Scope | Target | The reasoning |
|---|---|---|
| Critical defects (D1–D4, D6) | **Recall ≥ 0.90**, precision ≥ 0.80 | A misleading listing that slips through costs far more than one extra flag |
| Major & minor (D5, D7, D8) | **Precision ≥ 0.85**, recall ≥ 0.75 | Here false alarms are the bigger cost — they burn reviewer time and trust |
| Clean listings | False-flag rate ≤ 10% | A checker that flags everything has automated nothing |
| Automation | ≥ 60% of listings routed with no human touch | The point is to remove manual work, not relocate it |
| Cost & speed | ≤ €0.01 per listing, ≤ 5 s p95 | It must beat a human reviewer on economics, at catalog scale |
| Ground truth | Self-agreement κ ≥ 0.70 per defect | If the labels aren't consistent, no metric built on them means anything |

The asymmetry is the point. A single blended accuracy score would hide exactly the trade-off a content leader has to make: *how many false alarms am I willing to accept to catch nearly every misleading listing?* Wanderlint makes that trade-off explicit, per defect type, and lets confidence thresholds be tuned against it.

---

## 6. The solution

```
listing ──► rules ──► LLM reviewer ──► merge ──► router ──► persona report
            exact,     semantic,        dedupe    pass /     content · supplier
            cheap      evidence-                  flag /     · catalog
                       required                   review
```

Four design principles, each traceable to a discovery risk:

- **Rules before models** *(viability)*. Anything a regular expression can catch exactly, it does — free and deterministic. The LLM is reserved for judgement.
- **Evidence or it didn't happen** *(usability, feasibility)*. Every LLM-flagged defect must quote a verbatim span from the listing. If the quote can't be found, the defect is discarded — and counted, as a hallucination metric.
- **One structured call per listing** *(viability)*. The model receives the rule findings so it never duplicates them, and returns a strict schema rather than prose.
- **Confidence-based routing** *(value)*. Each defect type gets thresholds for auto-flag vs. human review, chosen from measured precision/recall curves — never from intuition.

---

## 7. Evidence over opinion: how it's evaluated

An AI quality product is only as credible as its evaluation. These rules are enforced in the repository, not just intended:

1. **Strict dev / test separation.** Prompts, rules and thresholds are tuned on the dev set only. The test set is touched at milestones — and if its errors are ever inspected, the report says so.
2. **Humans own the ground truth.** Machine-generated labels are a starting point, never gold. Every label is confirmed, rejected or added by a person.
3. **Prompts are immutable once released.** A change means a new version, a logged reason, and a before/after comparison.
4. **No silent regressions.** A change ships only if its target metric improves and no other defect type drops by more than 0.03 — enforced by a CI gate.
5. **Every failure becomes a test.** Once a failure pattern is understood, regression listings lock it in permanently.
6. **Reports show the misses.** Every false positive and false negative is listed with its evidence. Headline numbers without the errors behind them are marketing, not evaluation.

---

## 8. Building the ground truth

Real supplier listings aren't publicly available, so the dataset is synthetic. That makes data generation a quality problem in its own right — a careless generator produces a dataset that flatters the checker.

### Design choices

- **150 listings**, ≈70% `en-GB` / 30% `de-DE`, spread across 12 activity categories and 37 cities.
- **40% clean, 60% defective**, with 1–3 planted defects each and at least 15 instances per defect type, so every type has enough support to measure.
- **~20% hard cases** placed deliberately on taxonomy boundaries (D2/D3, D2/D4, D3/D4, D5/D6, D1/D8) — the cases where checkers and humans both struggle.
- **Near-miss traps**: legitimate patterns that *look* like defects — a real acronym in a title, a cancellation fee in the cancellation policy, "N/A" inside a genuine sentence. They test precision honestly. Traps appear in defective listings too, so their presence never signals "clean".
- **Two-step generation**: clean listings first, defects injected separately. Every planted label is exact, and no hint of it leaks into the text.

### Five pilots before one full run

I ran five 10-listing pilots (~€0.13 each) before generating the full dataset. Each one surfaced a systematic flaw that would have silently corrupted the ground truth:

| What the pilot revealed | Why it mattered | The fix |
|---|---|---|
| An acronym trap made the model invent a UNESCO status for a wetland | A "clean" listing with a false claim poisons precision | Acronyms must make no claim the listing doesn't support |
| A planted "private visit" claim was too ambiguous to label | Ambiguous seeds waste labeling time and erode support | Claims must name the feature explicitly |
| A planted missing item overlapped an exclusion ("drinks" vs. "wine") | The label was actually a different defect type — a silent mislabel | Synonym-aware overlap check against exclusions |
| Injected promises were tacked on as ", and…" clauses | Labeling fixes labels, not text — a checker could learn to spot seams instead of defects | Deterministic trailing-clause rejection at generation time |
| In the first full run, a guard rejected "official wine tasting" for mentioning wine | Valid seeds dropped; one defect type fell below quota | One shared definition used by both generator and audit |

### The result

| | |
|---|---|
| Listings | 150 (105 `en-GB`, 45 `de-DE`) |
| Planted defects | 163 across D1–D8 (17–25 per type) |
| Hard cases | 32 (19.6% of planted defects) |
| Automated audit | 0 violations across 5 checks |
| Total generation cost | €2.21 |
| Traceability | Every listing records the prompt version, taxonomy version and git commit that produced it |

### Next: blind human validation

Planted labels become ground truth only after human review — and *how* that review is done matters. If a reviewer sees the planted labels first, they anchor on them: they confirm what's shown and miss what isn't. So labeling is **blind-then-reveal**: I record the defects I see first, then reveal the planted labels and reconcile. This produces three numbers that say a lot about the dataset and the problem:

- **Seed rejection rate** — how often the generator planted something a human disagrees with.
- **Human-added defects** — what the generator planted by accident.
- **Blind human recall** — how many planted defects a careful human catches unaided. *This is the human baseline the checker will be compared against.*

Time per listing is recorded too, because minutes of human review per listing, set against cents of checker cost, is the business case.

---

## 9. Decisions and trade-offs

A selection of calls made so far, and why:

| Decision | Alternative considered | Why this way |
|---|---|---|
| Score per *(listing, defect type)* | Span-level matching | Matches how a reviewer acts on a listing; span-level is reported separately as a stricter view |
| Recall-first for critical defects | One accuracy target for all | Business cost of a miss is asymmetric — the metric should be too |
| Synthetic data with planted defects | Scraping real listings | Exact labels, controlled difficulty, no licensing risk — at the cost of realism (see limitations) |
| Rules + one LLM call | LLM for everything | Cheaper, deterministic where possible, and easier to debug |
| Drop discriminatory-language detection from v1 | Fold it into policy violations | Too rare and too sensitive to measure meaningfully at this sample size; it deserves its own dataset |
| Blind-then-reveal labeling | Confirming pre-filled labels | Removes anchoring bias and yields a human baseline for free |

The full decision log lives in [`failure_patterns.md`](failure_patterns.md).

---

## 10. Known limitations

Stated up front, and repeated in every evaluation report:

- **Same model family.** The data generator and the checker are both Claude models. The checker may find Claude-written defects easier to spot than human-written ones, so scores may be optimistic. A small human-written sample is planned to test this directly.
- **Synthetic data.** Real supplier content is messier than any generator. The taxonomy and pipeline are built to transfer; the metrics are only claimed for this dataset.
- **Two locales.** v1 covers English and German only.
- **Small sample.** 150 listings gives directional per-defect metrics, not definitive ones. Every report states support and caveats per defect type.

---

## 11. Roadmap

**M1 — Ground truth** *(in progress)*
- [x] Charter, scope, success metrics and kill criteria
- [x] Defect taxonomy v1 with documented boundary decisions (`taxonomy-v1`)
- [x] Labeling guide with one-page cheat sheet
- [x] Synthetic data pipeline with automated quality audits — 150 listings, 163 planted defects, €2.21
- [ ] Blind-then-reveal labeling app
- [ ] Human validation of all 150 listings; self-agreement (κ)
- [ ] Frozen dev / test split

**M2 — Baseline: can an LLM do this at all?**
- [ ] Rule checks (D1, D5, D6, D7, D8)
- [ ] LLM reviewer with structured, evidence-backed output
- [ ] Eval harness and first reports (dev + baseline test)

**M3 — Iterate and decide**
- [ ] Error analysis → named failure patterns → prompt iterations
- [ ] Regression set and CI eval gate
- [ ] Three run modes with confidence-based routing
- [ ] Final test-set report against targets and kill criteria
- [ ] Public write-up of what worked, what didn't, and why

**Beyond v1 — if the evidence supports it:** a cheap-model pre-screen with escalation, auto-fix suggestions with their own evaluation, more locales, drift monitoring on the human-review queue, and an API for CMS integration.

---

## 12. Repository & quickstart

```
wanderlint/
  CLAUDE.md               # project charter and working agreement
  taxonomy.yaml           # defect definitions (versioned, tagged)
  labeling_guide.md       # how a human labels a listing
  failure_patterns.md     # every decision and failure pattern, with rationale
  config.yaml             # models, thresholds, cost caps
  prompts/                # versioned prompts, immutable once released
  src/wanderlint/         # models, rules, LLM client, checker, router, eval
  scripts/                # data generation, audit, labeling, agreement scoring
  data/                   # listings and labels
  reports/                # one record per generation or eval run
  tests/
```

Requires Python 3.11, [uv](https://github.com/astral-sh/uv), and an Anthropic API key in `.env` (`ANTHROPIC_API_KEY=...`).

```bash
uv sync --all-groups
make test && make lint

# Plan and estimate a generation run without calling the API
uv run python scripts/generate_listings.py --n 10 --dry-run
```

Checker and eval commands (`wanderlint check`, `wanderlint eval`) arrive in Milestone 2.

---

## Why I'm building this

I'm a product manager who builds AI products, and Wanderlint is how I approach one: start from the user's pain and the business cost of getting it wrong, name the riskiest assumption and test it first, define quality before writing code, set targets that reflect real trade-offs, and decide in advance what result would make me stop.

The checker is the visible part. The discovery, the definitions and the evaluation are what make it trustworthy — and trust is the product.

**Shola Ayodele** · Berlin · [LinkedIn](https://www.linkedin.com/in/shola-ay/)
