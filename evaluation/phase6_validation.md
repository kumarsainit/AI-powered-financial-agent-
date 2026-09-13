# Phase 6 Validation — Recommendation Assembly, Explanations & `output.csv`

Scope: the exact output contract, final recommendation assembly, deterministic explanation generation, payment-plan and spending-change serialization, a strict output validator, the single end-to-end production entry point, and full-dataset generation of `output.csv`.

Out of scope by design: new forecasting, affordability or optimization logic; submission packaging. No LLM or VLM is called anywhere in the pipeline, and no model is trained.

---

## 1. Output Schema

Taken verbatim from `problem_statement.md` and `dataset/output.csv`, in this exact order:

```text
request_id,amount_safe_to_pay,affordability_status,recommended_payment_method,payment_plan,earliest_date_for_full_payment,spending_changes_needed,decision_explanation
```

| Field | Type and rules as implemented |
|---|---|
| `request_id` | exactly one row per `dataset/requests.csv` row |
| `amount_safe_to_pay` | decimal, `0 <= value <= requested_amount`, at most 2 decimals, trailing zeros trimmed (`603.3`, `25256`) |
| `affordability_status` | one of `affordable_now`, `affordable_with_plan`, `affordable_later`, `not_affordable` |
| `recommended_payment_method` | one of `full_payment`, `partial_payment`, `installments`, `wait`, `not_recommended` |
| `payment_plan` | chronological `YYYY-MM-DD:amount` joined by `|`, or `none`; integral amounts print without decimals, others with exactly two (`620.40`) |
| `earliest_date_for_full_payment` | ISO `YYYY-MM-DD`, or empty when no full payment is safe within the horizon; equals `request_date` for `affordable_now` |
| `spending_changes_needed` | `none`, or up to three `stop:<event_id>` / `reduce_to:<event_id>:<amount>` entries joined by `|` |
| `decision_explanation` | one line, non-empty, ≤ 400 characters, generated only from structured facts |

Formatting rules were derived from the worked rows in `sample_requests.csv`, which use trimmed decimals in `amount_safe_to_pay` and two-decimal amounts inside `payment_plan` and `reduce_to`.

Components: `buyorwait/formatting.py` (money, dates, wording), `buyorwait/explanation.py` (templates plus a content guard), `buyorwait/output_record.py` (assembly and serialization), `buyorwait/output_writer.py` (CSV emission), `buyorwait/output_validator.py` (strict validation), `buyorwait/pipeline.py` (the single end-to-end path), `code/main.py` (entry point). Internal domain models never touch CSV formatting.

---

## 2. Production Row Count

| Metric | Value |
|---|---|
| Rows generated (excluding header) | **250** |
| Unique `request_id` values | 250 |
| Requests in `dataset/requests.csv` covered | 250 / 250 |
| Sample request ids present | **0** |
| Sample users present | **0** |
| Duplicate rows | 0 |
| Full pipeline run time | ~1.2 s (~2.4 s including the determinism rerun), single core, no GPU, no model call |

---

## 3. Validation Result

`output_validator.validate_output_file` ran all twenty required checks over the generated file: **250 rows checked, 0 errors.**

Checks: exact schema and column order; row count; unique request ids; production request ids only; no sample users; no missing required values; valid statuses; valid methods; valid ISO dates; numeric monetary values; at most two decimals everywhere; chronological plans with positive amounts; per-method plan arithmetic (`full_payment` one payment of the requested amount on `request_date`; `wait` one payment on the earliest safe date; `partial_payment` exactly two payments summing to the request, the first equal to `amount_safe_to_pay` on `request_date` and the second on or before `desired_completion_date`; installments totalling at least the requested amount); spending-change syntax, count limit and no repeated event; `0 <= amount_safe_to_pay <= requested_amount`; earliest-date consistency with the status; reconciliation of status, method, safe amount and plan against the in-memory decision result; explanation presence; no unexpected columns; no duplicate rows.

One validator rule was deliberately relaxed after inspecting the data. `not_affordable` rows may still carry an `earliest_date_for_full_payment`, because the problem statement states that this field "measures financial capacity independently of the user's payment-method preferences". Five production requests are `not_affordable` only because the user does not accept `full_payment` (so `wait` is ineligible) while the full amount is nevertheless capacity-safe on a known date; suppressing that date would discard information the contract asks for.

---

## 4. Status Distribution

| Status | Rows | Share |
|---|---|---|
| `affordable_with_plan` | 78 | 31.2% |
| `affordable_now` | 66 | 26.4% |
| `affordable_later` | 57 | 22.8% |
| `not_affordable` | 49 | 19.6% |

---

## 5. Payment-Method Distribution

| Method | Rows |
|---|---|
| `full_payment` | 71 |
| `installments` | 65 |
| `wait` | 57 |
| `not_recommended` | 49 |
| `partial_payment` | 8 |

---

## 6. Safe-Amount Statistics

| Metric | Value |
|---|---|
| min / median / max | 0 / 12,799.30 / 38,114,000 |
| mean | 2,315,319.12 |
| Rows where `amount_safe_to_pay` equals the requested amount | 92 |
| Rows where it is 0 | 7 |
| Rows violating `0 <= value <= requested_amount` | 0 |
| Rows with more than two decimals | 0 |

---

## 7. Payment-Plan Statistics

| Payments in plan | Rows |
|---|---|
| `none` (no payment recommended) | 49 |
| 1 payment | 128 |
| 2 payments | 10 |
| 3 payments | 63 |

Installment plans selected: 65, each matching a supplied `request_payment_options.csv` option exactly (schedule, amount and count). Total financing fees carried by selected plans: 13,303,422.64 across all currencies. Partial payments: 8, each summing exactly to the requested amount by subtraction. Rows with an empty `earliest_date_for_full_payment`: 44.

---

## 8. Spending-Change Statistics

| Metric | Value |
|---|---|
| Rows needing a spending change | 13 |
| One change / two changes / three changes | 11 / 2 / 0 |
| Action mix | `reduce_to` 9 · `stop` 6 |
| Changes below `minimum_allowed_amount` | 0 |
| Protected categories changed | 0 |
| Rows exceeding three changes | 0 |
| Changes not present in the decision result | 0 |

Every serialized change is a direct translation of a `SpendingChange` produced by the decision engine; Phase 6 performs no spending optimization of its own.

---

## 9. Explanation Validation

Explanations are produced by deterministic templates, one per status, fed exclusively by the structured `ExplanationFacts` and the selected candidate. Raw message text, raw image text and event descriptions of anything other than the changed expense never reach them.

| Metric | Value |
|---|---|
| Rows with an explanation | 250 / 250 |
| Length min / median / max | 80 / 109 / 231 characters |
| Multi-line explanations | 0 |
| Explanations mentioning an internal identifier, model, prompt, algorithm or test | 0 (guarded by regex and enforced at generation time) |
| Rows qualifying an unresolved obligation | 29 |

Template shapes, all naming the actual binding constraint:

- **affordable_now** — "Pay IDR 15,656,000 today. This leaves at least IDR 24,768,300 available over the next 90 days."
- **affordable_with_plan (installments)** — "Use 3 installments of USD 268.74, starting 6 April 2026. This leaves at least USD 900 available."
- **affordable_with_plan (spending changes)** — "Stop the family streaming plan, then pay EUR 620.40 today. This leaves at least EUR 800 available."
- **affordable_with_plan (partial)** — "Pay INR 28,820 today and the remaining INR 10,840 on 15 September 2024. This completes the full request and keeps the INR 92,800 minimum protected."
- **affordable_later** — "Pay IDR 18,164,000 in full on 15 November 2024. Paying earlier would take the balance below the IDR 16,588,900 minimum."
- **not_affordable** — "Do not make this payment by 12 January 2026. None of the available options keeps the ZAR 13,100 minimum protected." A second form is used when the request and the user both permit a partial payment and something is safe today: "Do not proceed with the EUR 5,414.20 request. Although EUR 597.74 is available today, the full amount cannot be completed safely within 90 days."

When the decision carries unresolved obligations, the sentence "One upcoming commitment has no confirmed amount yet, so review this before committing." (pluralised with a count) is appended. No amount is ever invented for such an obligation, and the wording never asserts certainty the financial state does not support.

`explanation.validate_explanation` runs on every generated string and raises rather than emitting a suspect sentence.

---

## 10. Sample Calibration Results

`code/evaluation/calibrate_samples.py` runs the full pipeline over the 25 solved `sample_requests.csv` rows and compares all six machine-checkable fields. It is a development instrument: those users are excluded from the production dataset by construction, no ground-truth value is read at decision time, and the engine contains no `request_id` or `user_id` branch anywhere.

| Field | Exact agreement |
|---|---|
| `affordability_status` | **18 / 25** |
| `recommended_payment_method` | **20 / 25** |
| `payment_plan` | **19 / 25** |
| `spending_changes_needed` | **22 / 25** |
| `earliest_date_for_full_payment` | 13 / 25 |
| `amount_safe_to_pay` (exact string) | 4 / 25 |

Where the decision agrees, the generated explanation is essentially the reference sentence — e.g. reference "Pay ZAR 25,256 today. This leaves at least ZAR 18,000 available over the next 90 days." versus generated, identical.

The seven remaining status mismatches are all forecast-magnitude, not rule, differences: six are cases where the engine judges the full amount safe today and the reference does not, one is the reverse. A full re-sweep of the amount statistics (5 essential × 4 income × 2 flexible combinations, 40 configurations) confirmed the current defaults are already the best-scoring point of that grid, so no further tuning was applied — continuing to tune against 25 rows would be fitting noise.

---

## 11. Generalized Corrections Made In This Phase

1. **Lapsed recurring expenses are no longer resurrected** (Phase 4 `projection.py`). A series whose last observation precedes `request_date` by more than one full cadence is not projected. Scope and threshold were selected by measurement; full rationale and the rejected alternative are recorded in `phase4_validation.md` §16. Regression tests: lapsed series, current series, and all three scope settings.
2. **`not_affordable` may legitimately carry an earliest safe date** (validator rule), per the problem statement's explicit note that the field measures capacity independently of payment-method preference. Covered by the production validator run and by unit tests.
3. **`SpendingChange` now carries the source event's description**, so explanations can name the expense the user recognises ("the family streaming plan") instead of a category slug. Pure data plumbing; no decision behaviour changed.

No `if request_id ==` or `if user_id ==` branch exists in the codebase, and no reference output value is embedded in production code.

---

## 12. Deterministic Repeatability

The pipeline was run twice over identical inputs in one process and compared at three levels:

| Comparison | Result |
|---|---|
| `output.csv` bytes | **identical** (sha256 `45de9ebf3ed7dd7bca1bf4a11f6c1307324790b4f8661b55c5fd3a37c9b9f5ca`) |
| Assembled output records (all 8 fields × 250 rows) | identical |
| Recommendations, payment plans, safe amounts, statuses, explanations | identical |

**Zero differences.** There is no randomness, no wall-clock read, no unordered iteration in any ordering path, and no model call anywhere in the pipeline.

---

## 13. Unresolved Cases

| Case | Count | Handling |
|---|---|---|
| Requests carrying an unresolved future obligation | 29 | never given an invented amount, never simulated as cash, surfaced in the explanation as an unconfirmed upcoming commitment |
| Image-evidence facts still unresolved (no VLM key) | 11 | remain unresolved obligations; they cannot make any plan look safer |
| Requests with no safe full-payment date in the 90-day horizon | 44 | `earliest_date_for_full_payment` left empty, as the contract requires |
| Requests where the requested amount could not be converted to the account currency | 0 | path implemented and unit-tested; never triggered in production |

---

## 14. Tests

**285 passed, 0 failed** — 122 (Phases 2–3), 72 (Phase 4, including the three new staleness regressions), 59 (Phase 5), 24 (Phase 6 output/explanation/validator), 8 (end-to-end pipeline).

Phase 6 coverage: every schema field; column ordering; row count; production-user isolation; payment-plan serialization; partial-payment arithmetic; installment serialization; spending-change serialization; each affordability status; earliest safe date; safe amount; unresolved-obligation explanation; explanation determinism; explanation factual guards; malformed output rejection; duplicate-request rejection; invalid status, method and date rejection; monetary precision; output-to-decision reconciliation; and the full end-to-end pipeline including a byte-identical rerun and a sample-leak guard.

---

## 15. Output Path

`output.csv` in the repository root, as required by `README.md`. Regenerate with:

```bash
python3 code/main.py
```

The entry point runs ingestion → evidence → forecast → decision → assembly → CSV, then validates the file it just wrote and fails loudly if anything is off. `--dataset` and `--output` override the defaults; `--skip-validation` is available for debugging only.
