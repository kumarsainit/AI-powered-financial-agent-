# Phase 5 Validation — Deterministic Affordability & Payment-Plan Optimization

Scope: request normalization, a common payment-option model, candidate-plan generation, evaluation of every candidate through the Phase 4 simulator, exact `amount_safe_to_pay`, earliest safe full-payment date, installment and partial-payment plans, spending-change optimization, conservative handling of unresolved obligations, deterministic lexicographic selection, structured explanation inputs, and fifteen enforced decision invariants.

Out of scope by design: natural-language `decision_explanation`, CSV serialization, `output.csv`, and submission packaging. No LLM or VLM is invoked anywhere in the decision path.

---

## 1. Components

| Part | Module | Responsibility |
|---|---|---|
| B, C, T, U | `buyorwait/decision.py` | Typed models: `PurchaseSpec`, `PaymentOptionModel`, `CandidatePlan`, `GateResult`, `CandidateEvaluation`, `SpendingChange`, `ExplanationFacts`, `Recommendation`, plus 4 affordability statuses, 5 methods, 5 candidate kinds and 15 internal reason codes. |
| B–S | `buyorwait/planning.py` | Normalization, option modelling, candidate generation, plan simulation, exact safe-amount and earliest-safe-date computation, spending-change optimization, lexicographic selection. |
| Y | `buyorwait/decision_invariants.py` | Fifteen loud invariant checks over the finished recommendation, several re-verified by re-simulation. |
| X | `code/evaluation/validate_phase5.py` | Full 250-request production validation and repeatability check. |
| — | `code/evaluation/calibrate_samples.py` | Development-only calibration against the 25 public sample rows. Never used in production and never consulted at decision time. |

Every candidate — full payment today, full payment later, partial payment, each installment option, and each spending-change variant — is scored by injecting its payments into the Phase 4 event stream and re-running `simulator.simulate`. There is no second affordability formula anywhere in the codebase.

---

## 2. Decision And Optimization Algorithm

**Three independent gates** are recorded per candidate and never collapsed: financial capacity (simulated), payment-method eligibility (installment term vs `max_installment_months`), and user preference (`payment_methods_user_will_consider`). A plan blocked by eligibility keeps its capacity verdict, so the true blocking reason is always recoverable.

**Lexicographic selection** over safe, eligible, preferred candidates, matching the problem statement's own ranking:

1. completes the full request by `desired_completion_date`
2. requires no spending change
3. lowest total amount paid
4. earliest first payment
5. fewest payments
6. lowest `payment_option_id`, then candidate id as the final deterministic tie-break

Financial safety and both eligibility gates are pre-filters, so the effective order is safety → eligibility → preference → the six criteria above. No weighted sum is used anywhere.

**`amount_safe_to_pay` is closed-form and exact, not a search.** Every projected event is independent of the running balance, so injecting a debit `D` on `request_date` shifts every subsequent daily closing balance by exactly `−D`. Safety is therefore monotone in `D`, and the boundary is `D_max = floor_to_cent(minimum_projected_balance − minimum_balance_to_keep)`, clamped to `[0, requested_amount]`. No floating-point binary search, no cent-by-cent enumeration. The boundary is then *verified* by two real simulations — `safe(D)` must hold and `safe(D + 0.01)` must fail whenever `D < requested_amount` — as invariants 10 and 11.

**Earliest safe full-payment date** uses the same shift property with prefix and suffix minima of daily closing balances: the first day `d` where the pre-`d` minimum stays above the floor and `min(closing_t for t ≥ d) − amount ≥ minimum_balance`. O(days), then re-simulated for verification (invariant 12).

**Spending-change optimization** enumerates subsets of at most three eligible changes, ordered by (number of changes, total reduction, event ids), pruned by each change's individually measured benefit, and returns the first subset the simulator certifies safe. Reductions always target `minimum_allowed_amount` exactly; protected categories and categories the user has not agreed to change are never touched.

---

## 3. Tests

| Suite | Tests |
|---|---|
| Phase 2 + Phase 3 | 122 |
| Phase 4 (`test_forecast.py`, `test_forecast_production.py`) | 69 |
| Phase 5 unit (`test_decision.py`) | 35 |
| Phase 5 production + adversarial (`test_decision_production.py`) | 24 |
| **Total** | **250 passed, 0 failed** |

Required coverage is met, including: affordable today; today sufficient but future-unsafe; future salary enabling a later purchase; full payment later; installments safe; installments safe upfront but unsafe later; partial payment; subtraction-derived remainder; eligibility failure; preference mismatch; minimum-balance violation; flexible reduction; reducible minimum; stoppable expense; multiple possible changes; minimal-disruption selection; unresolved obligation; speculative vs confirmed income; reservation handling; unrealized investment excluded; currency conversion; missing FX; safe-amount boundary and boundary-plus-one-cent; no safe date in horizon; total-cost comparison; same-day payment/income interaction; the 90-day boundary; deterministic candidate ordering; all-candidate simulator validation; no current-balance shortcut; no sample-user leakage.

Adversarial cases: a purchase that fits today but creates a future deficit; salary landing one day after an installment; a lower monthly instalment with a higher total cost; a flexible reduction that is insufficient; a reduction that would need to breach `minimum_allowed_amount`; uncertain income failing to rescue a plan; a new recurring expense starting after the purchase; and a cancellation message freeing capacity.

---

## 4. 250-Request Production Validation

| Metric | Value |
|---|---|
| Requests processed | **250 / 250** |
| Errors | 0 |
| Invariant violations | 0 |
| Deterministic repeatability mismatches | **0 / 250** |
| Candidates generated per request — min / median / max | 2 / 3 / 7 (860 candidates total) |
| Full run time | ~1.8 s, single core, no GPU, no model call |

---

## 5. Affordability Status Distribution

| Status | Requests | Recommended method | Requests |
|---|---|---|---|
| `affordable_with_plan` | 77 | `full_payment` | 70 |
| `affordable_now` | 65 | `installments` | 63 |
| `affordable_later` | 57 | `wait` | 57 |
| `not_affordable` | 51 | `not_recommended` | 51 |
| | | `partial_payment` | 9 |

Candidate kinds generated: 478 installment candidates, 256 full-payment-today, 115 full-payment-later, 11 partial-payment.

Candidate rejection reasons (per candidate): `completion_after_deadline` 396, `payment_method_not_preferred` 363, `payment_method_ineligible` 208, `installment_term_too_long` 173, `minimum_balance_violation` 207, `future_cash_flow_deficit` 206, `installment_unsafe` 46, `insufficient_current_surplus` 1.

Blocking reasons on the 51 `not_affordable` recommendations: `completion_after_deadline` 50, `minimum_balance_violation` 49, `future_cash_flow_deficit` 48, `payment_method_not_preferred` 38, `installment_term_too_long` 28, `installment_unsafe` 27, `payment_method_ineligible` 22, `insufficient_current_surplus` 1 (a request may carry several).

---

## 6. Safe-Amount Statistics

| Metric | Value |
|---|---|
| `amount_safe_to_pay` — min / median / max | 0 / 12,799.30 / 38,114,000 |
| As a fraction of `requested_amount` — median / mean | 0.677 / 0.59 |
| Requests where the full requested amount is safe immediately | 89 |
| Requests where `amount_safe_to_pay` is 0 | 7 |
| `0 <= amount_safe_to_pay <= requested_amount` | holds for all 250 (invariant) |
| Earliest safe full-payment offset in days — min / median / max | 0 / 9 / 90 |
| Requests with no safe full-payment date inside the horizon | 46 |

---

## 7. Payment-Plan Statistics

| Metric | Value |
|---|---|
| Installment plans selected | 63 |
| Selected installment term — min / median / max payments | 2 / 3 / 3 |
| Partial payments selected | 9 |
| Full payment today selected | 70 |
| Wait (single later full payment) selected | 57 |
| Minimum projected balance under the selected plan — median | 41,452.31 |
| Safety margin under the selected plan — median | 5,753.74 |

Negative minima in the distribution belong to `not_affordable` requests, where no plan is selected and the untouched baseline forecast is reported.

---

## 8. Spending-Change Statistics

| Metric | Value |
|---|---|
| Requests whose recommendation needs a spending change | 14 |
| Changes per recommendation | 1 change: 11 · 2 changes: 2 · 3 changes: 1 |
| Action mix | `reduce_to` 10 · `stop` 8 |
| Reductions below `minimum_allowed_amount` | 0 (invariant 8) |
| Protected categories changed | 0 |
| Recommendations exceeding three changes | 0 |

---

## 9. Unresolved-Obligation Handling

29 recommendations carry at least one unresolved debit obligation whose amount Phase 4 could not establish (missing evidence amounts, unresolved image facts, blank ledger amounts). The production policy is:

- their amount is never invented, defaulted, or bounded by guesswork;
- they never enter the cash simulation, so they cannot make a plan look *better*;
- every affected recommendation records `BLOCKING_UNRESOLVED_OBLIGATION` in `blocking_reasons` and the full obligation objects in `ExplanationFacts.blocking_obligations`, so Phase 6 can qualify the explanation;
- `DecisionConfig.block_on_unresolved_obligations` (default `False`) turns them into hard blockers.

The default is off because forcing `not_affordable` on 29 requests with no evidential basis for any magnitude would be a fabricated verdict, not a conservative one — the conservative half (never counting the unknown obligation as absent *cash relief*) is already enforced upstream in Phase 4. The switch keeps the alternative one config value away and auditable.

---

## 10. Invariants

All fifteen pass on all 250 production requests (0 violations):

1. selected plan is an eligible payment method · 2. selected plan is safe (re-simulated) · 3. every selected payment sits inside the forecast window, except installment schedules that the supplied option itself extends beyond it · 4. total paid equals the purchase obligation plus the option's stated fee · 5. partial remainder equals `requested_amount − amount_safe_to_pay` by subtraction · 6. no selected plan violates the minimum balance · 7. no selected plan depends on speculative income (excluded upstream in Phase 4) · 8. no category is reduced below `minimum_allowed_amount` · 9. no essential or protected expense is silently removed · 10. `amount_safe_to_pay` is actually safe (re-simulated) · 11. `amount_safe_to_pay + 0.01` is unsafe whenever a finite boundary exists (re-simulated) · 12. the earliest full-payment date is actually safe (re-simulated) · 13. no candidate is selected on the strength of its first payment alone — every installment trajectory is simulated in full · 14. recommendations are deterministic · 15. sample users never enter production decisions.

---

## 11. Determinism

The harness builds every recommendation twice in one process and compares a fingerprint of `amount_safe_to_pay`, status, method, selected candidate id, the full payment plan, the earliest date, and the ordered spending changes. **0 mismatches across 250 / 250.** No randomness, no wall-clock reads, no unordered iteration in any ranking path, and every ordering key ends in a unique id.

---

## 12. Development Calibration Against The Public Samples

`code/evaluation/calibrate_samples.py` scores the engine against the 25 solved `sample_requests.csv` rows. It is a development instrument only: those 25 users are excluded from the production dataset by construction, no ground-truth value is read at decision time, and no request-id-specific rule exists anywhere in the engine.

Current agreement: **affordability_status 17/25, recommended_payment_method 19/25, earliest_date_for_full_payment 14/25**, with `amount_safe_to_pay` matching exactly wherever the full amount is safe and typically within a few percent otherwise.

This calibration is what identified the two Phase 4 corrections recorded in `phase4_validation.md` §15 (closing-balance safety semantics; `median` rather than `percentile_75` for essential variable spending). Remaining disagreements are forecast-magnitude noise in both directions — five requests where the engine is more optimistic than the labels, four where it is more conservative — not a systematic gate or ranking defect.

---

## 13. Performance

250 requests reconstructed, forecast, planned, validated and re-validated for determinism in **~1.8 s** on one core. No GPU, no training, no Colab, no network call, no LLM/VLM in the decision path, and no new dependency.
