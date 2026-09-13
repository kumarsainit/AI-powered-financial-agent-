# Phase 7 Validation — Adversarial Evaluation & Hardening

## 1. Scope

Adversarial evaluation of the complete pipeline as committed at `004376d`: boundary attacks, property-based testing over randomized synthetic histories, metamorphic relations, and targeted attacks on the weak areas recorded in Phases 3.1–6. Only demonstrated generalized defects were fixed; no architectural change, no cost/performance work, no packaging, and no tuning aimed at the 25 public samples.

The implementation was verified against the code, not against the earlier reports — which is how three of the five defects below were found.

## 2. Adversarial Categories Tested

| Category | Coverage |
|---|---|
| A. Date / forecast boundaries | request date coinciding with income and with an expense; events one day before and after the request date; day 90 and day 91; recurrence at exactly one cadence and beyond it; lapsed series; deadlines at day 0 and day 90; income past the horizon |
| B. Balance safety | balance exactly at, one cent above and one cent below the minimum; payment exactly at the safe amount and one cent over; zero safe amount; temporary deficit that later recovers |
| C. Transaction lifecycle | settled, pending, failed, failed→retry, cancelled, refund pending and settled, possible duplicate, realized sale, unrealized valuation, superseded records, linked records in both orderings, and a three-member linked component |
| D. Recurrence | one and two occurrences, monthly, weekly, biweekly, irregular cadence, lapsed expense, lapsed income, missing amounts, evidence-supported recurrence |
| E. Payment-method gates | the three gates exercised independently; safe-but-ineligible, eligible-but-unsafe, safe-but-unwanted; capacity date surviving preference rejection |
| F. Partial payment | safe amount zero, full and in between; disallowed by the request; rejected by the user; completion at and after the deadline; two-payment shape and exact subtraction |
| G. Installments | one- and multi-payment options, zero and non-zero fees, differing start dates, completion before/at/after the deadline, options underfunding the request, payments past the horizon, tie-breaking by `payment_option_id` |
| H. Spending changes | none needed, single and multiple stops and reductions, protected categories, fixed expenses, floor exactly equal to the current amount, reductions at the floor, the three-change cap, unnecessary changes |
| I. Conflict / evidence | newer and older same-source facts, order independence, unrelated facts, cancellation, termination, untrusted content, unresolved facts |
| J. Output contract | schema and ordering, row count, uniqueness, sample isolation, money and date formatting, none/empty semantics, plan and change arithmetic, explanation presence and factual grounding, CSV round-tripping, line endings |

## 3. New Tests

| Suite | Tests | Purpose |
|---|---|---|
| `code/tests/test_adversarial.py` | 55 | the A–J matrix above |
| `code/tests/test_properties.py` | 270 | property-based invariants over 60 randomized synthetic financial histories |
| `code/tests/test_metamorphic.py` | 18 | metamorphic relations |
| `code/tests/test_hardening.py` | 17 | conflict resolution, extreme monetary magnitudes, CSV IO, config switches |
| `code/tests/test_explanation_consistency.py` | 8 | explanation-to-decision fact consistency across all 250 production rows |
| **Total new** | **368** | |

## 4. Property-Based And Metamorphic Testing

Sixty randomized synthetic histories (fixed seeds; the production pipeline itself remains fully deterministic) are checked against: `0 <= amount_safe_to_pay <= requested_amount` at two-decimal precision; the safe amount being safe and one cent more being unsafe; the earliest date being inside the window and actually safe; the selected plan passing all three gates and re-simulating safely; installment plans exactly matching a supplied option and respecting `max_installment_months` and the deadline; partial payments having exactly two payments summing to the request by subtraction; at most three spending changes, never on a protected or fixed category, never below `minimum_allowed_amount`, one action per event; and status/method consistency in both directions.

Metamorphic relations verified: adding an unrelated event, an unrelated message or a lapsed series changes nothing; reordering input rows or linked lifecycle records changes nothing; duplicating a superseded record creates no cash flow; more cash never lowers the safe amount; a higher minimum balance never raises it; a larger request never lowers it; removing an option or the user's willingness never yields that method; a more expensive option never becomes preferable; terminating a flexible expense never lowers capacity; and shuffling all 25,342 rows of `financial_events.csv` leaves every one of the 250 output rows byte-identical.

## 5. Failures Discovered

| # | Finding | Classification |
|---|---|---|
| 1 | `reduce_to` was **simulated at a scaled amount, not the amount published**. The projected occurrence was multiplied by `new/current` instead of being set to the declared figure, so the safety check used a different number from the one the output tells the user to adopt. Affected 8 production recommendations; the largest divergence was ~31,000 per occurrence (a plan published as "reduce to 203,300" was validated as if it were ~172,437). | 2 — implementation bug |
| 2 | The Phase 6 staleness rule used a **fixed 30-day month**, so any monthly series whose last gap was 31–33 days was wrongly treated as lapsed. It discarded 47 legitimate monthly expense series across 47 production requests. | 2 — implementation bug |
| 3 | Explanations **omitted required spending changes** whenever the plan was installments or partial payment: 9 production rows published a `stop:`/`reduce_to:` action that the sentence never mentioned, even though the plan's safety depended on it. | 1 — specification violation |
| 4 | Lifecycle components with **three or more linked records** marked every member as included, so a duplicate of an already-superseded record created extra cash flow. Not present in the current data (all components are size 1 or 2) but reachable from valid input. | 3 — robustness gap |
| 5 | An installment option whose payments **total less than the requested amount** raised a decision invariant instead of being rejected as a candidate, crashing the pipeline on otherwise valid input. | 3 — robustness gap |

Two further areas were investigated and deliberately **not** changed: the residual optimism behind the earliest-date differences (diffuse forecast magnitude, no identifiable rule error) and the tie-ordering of same-date events (no observable effect; hardened anyway, see below).

## 6. Fixes Applied

1. **`planning.apply_spending_changes`** now sets a reduced occurrence to the declared `new_amount`, converting to the home currency with the occurrence's own implied rate, so the simulated plan is exactly the published plan.
2. **`projection.elapsed_cycles` / `is_stale`** now count real calendar months for monthly cadences and cadence multiples otherwise, replacing the 30-day approximation.
3. **`explanation._affordable_with_plan`** prefixes the required spending changes to every plan shape, so installment and partial-payment recommendations state the change they depend on.
4. **`lifecycle._build_chain`** applies the existing pattern table edge by edge for components of any size; two-member behaviour is unchanged, and larger components now inherit exclusion dispositions instead of defaulting to inclusion.
5. **`RejectionReason.INSTALLMENT_TOTAL_BELOW_REQUEST`** is raised as a candidate rejection when a supplied option's payments do not cover the request, so the pipeline degrades cleanly rather than tripping an invariant.
6. **Stable ordering** — `ingestion`, `recurrence` and `spending_history` now sort by `(event_date, event_id)`, guaranteeing the row-order independence the metamorphic test asserts (12 same-user/category/date groups exist in the data).

Every fix is general; no fix references a request, user, category, or expected label.

## 7. Regression Coverage

Each finding has a dedicated test: the published-versus-simulated reduction amount; a monthly series due today (kept) versus one four months lapsed (dropped) plus all three staleness scopes; explanation wording matching the selected changes for every production row; a duplicated superseded record adding no cash flow; an underfunded installment option being rejected; and a shuffled event file producing identical output.

## 8. Production Before / After

| Metric | Before (`004376d`) | After |
|---|---|---|
| Rows | 250 | 250 |
| `affordable_with_plan` | 78 | 77 |
| `affordable_now` | 66 | 65 |
| `affordable_later` | 57 | 57 |
| `not_affordable` | 49 | 51 |
| `full_payment` / `installments` / `wait` / `partial_payment` / `not_recommended` | 71 / 65 / 57 / 8 / 49 | 70 / 63 / 57 / 9 / 51 |
| Rows using spending changes | 13 | 14 |
| Rows whose output changed | — | 39 |
| Validator errors | 0 | 0 |
| Median `amount_safe_to_pay` | 12,799.30 | 12,799.30 |

The distribution moves slightly toward caution, which is the expected direction: 47 wrongly discarded expense series are back in the forecasts, and eight reductions are now simulated at the amount actually published rather than a smaller one.

## 9. Sample Calibration Impact

| Field | Phase 6 | Phase 7 |
|---|---|---|
| `affordability_status` | 18 / 25 | 17 / 25 |
| `recommended_payment_method` | 20 / 25 | 19 / 25 |
| `payment_plan` | 19 / 25 | 17 / 25 |
| `earliest_date_for_full_payment` | 13 / 25 | 14 / 25 |
| `spending_changes_needed` | 22 / 25 | 22 / 25 |
| `amount_safe_to_pay` | 4 / 25 | 4 / 25 |

Agreement drops by three field-matches, and the reason matters: **Phase 6's apparent staleness gain was an artifact of the month-arithmetic bug.** With the arithmetic corrected the rule discards nothing at all on the production dataset, and sample agreement returns to exactly the pre-staleness baseline (93 of 150 field comparisons). The correct rule was kept and the spurious gain given up, because keeping a demonstrably wrong month calculation to score three more sample fields is fitting an error. `phase4_validation.md` §16 has been corrected accordingly.

## 10. Determinism

Two complete pipeline runs produce byte-identical `output.csv` (sha256 `3b42c57d480b477f3306962b8dccd9b0fa1eb393d0eeebc3894aa80d337d83a2`), identical records and identical recommendations. Additionally, shuffling every row of `financial_events.csv` leaves all 250 output rows unchanged. Phase 4, 5 and 6 harnesses each report zero repeatability mismatches.

## 11. Remaining Known Limitations

1. **Residual forecast optimism.** Six of the seven remaining sample status differences are cases where the engine judges the full amount safe today and the reference does not. No rule-level cause was identified; a re-sweep of the amount statistics confirmed the current defaults remain the best point of that grid, and disabling weak-income projection made agreement worse (89 versus 93). This is a magnitude question, not a logic one.
2. **Spending changes are not explored for partial-payment candidates.** The first leg is safe by construction, so a change could only enlarge it. A missed opportunity, not an incorrectness.
3. **Installment schedules extending past day 90** are validated only over the forecast window. Immaterial here (every deadline is within 86 days, so any selected plan completes inside the window), but real for other data.
4. **Eleven image-derived facts remain unresolved** because no VLM key is available; they can never make a plan look safer.
5. **Unresolved debit obligations do not block plans by default.** `DecisionConfig.block_on_unresolved_obligations` flips this; the default and its rationale are unchanged from Phase 5, and the switch is now tested end to end.

## 12. No Sample-Specific Logic

No production module contains a branch on `request_id`, `user_id`, a category name tied to a sample, or an expected output value. The sample comparison lives only in `code/evaluation/calibrate_samples.py`, which reads no ground truth at decision time and is never imported by the pipeline. Every Phase 7 fix is a general rule, and one measurable sample gain was deliberately surrendered rather than preserve an arithmetic error.

## 13. Final Test Count

**653 passed, 0 failed** (285 before this phase, 368 added).

## 14. Final Gate

`git status` shows changes only under `code/`, `evaluation/` and `output.csv`. `AGENTS.md`, `CLAUDE.md`, `problem_statement.md` and `dataset/` are untouched. Production code carries no comments. `python3 code/main.py` regenerates all 250 rows and the validator reports zero errors.
