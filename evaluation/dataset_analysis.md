# Dataset & Repository Forensic Analysis — "Buy or Wait?"

Phase 1 deliverable. Pure analysis — no solution code, no `output.csv`, no architecture. All figures below were computed directly from the files in `dataset/` (25,342-row `financial_events.csv` included) and from the three specification files, on 2026-09-13.

---

## A. Dataset Inventory

| File | Rows | Cols | Primary key | Notes |
|---|---|---|---|---|
| `requests.csv` | 250 | 8 | `request_id` | Evaluation set. Users `user_26`…`user_275` (250 distinct), fully disjoint from `sample_requests.csv` users. |
| `sample_requests.csv` | 25 | 15 (8 input + 7 output) | `request_id` | Solved examples only. Users `user_01`…`user_25` — never appear in `requests.csv`. |
| `financial_profiles.csv` | 275 | 10 | `user_id` | One row per user; 275 = 250 (requests) + 25 (samples). |
| `financial_events.csv` | 25,342 | 14 | `event_id` | By far the largest file; ~92 events/user on average (min 56, max 129). |
| `exchange_rates.csv` | 134 | 4 | (`rate_date`,`from_currency`,`to_currency`) | Only 5 directed pairs exist in the whole file. |
| `request_payment_options.csv` | 790 | 9 | `payment_option_id` | 2–4 rows per request; 275 requests covered (matches 250+25 above). |
| `messages.csv` | 215 | 7 | `message_id` | At most **one** message per user (verified: 0 users have >1). |
| `images.csv` | 16 | 4 | `image_id` | Exactly one row per blank-amount event (see D.1). |
| `dataset/media/images/*.png` | 16 | — | `image_NN.png` | 1:1 with `images.csv` rows. |
| `output.csv` | 250 | 8 | `request_id` | Blank template; its `request_id` set is byte-identical to `requests.csv`'s; every non-key cell is empty (verified). |

`code/main.py`, `code/evaluation/main.py`, `code/evaluation/usage_report.md` are all empty starter stubs.

---

## B. Relationship Map

```
financial_profiles.user_id ──┬── requests.user_id ──── request_payment_options.request_id
                              │        │                        │
                              │        ├── messages.request_id  │ (2–4 options/request)
                              │        └── images.request_id    │
                              │                                 │
                              └── financial_events.user_id      │
                                       │  ▲                     │
                                       │  └── linked_event_id ──┘ (self-referential lifecycle chain)
                                       │
                          messages.related_event_id ──┘ (only when a message describes ONE supplied event row)
                          images.related_event_id ─────┘ (always populated, always a blank-amount event)

exchange_rates: (settlement_date, event.currency → user.home_currency) — exact-date lookup
images/media: dataset/media/images/<image_id>.png
```

Join keys observed to be 100% clean: every `requests.user_id` exists in `financial_profiles`; every `images.related_event_id` exists in `financial_events` and is *always* a blank-`amount` row (16/16 both directions); every foreign-currency, non-blank event has an exact-date rate available (139/139 checked).

---

## C. Business-Rule Catalogue (from `problem_statement.md` / `README.md` / `AGENTS.md`)

**Output contract** — exactly 8 columns in order: `request_id, amount_safe_to_pay, affordability_status, recommended_payment_method, payment_plan, earliest_date_for_full_payment, spending_changes_needed, decision_explanation`. `0 ≤ amount_safe_to_pay ≤ requested_amount` always.

**`affordability_status`** (4 values) — `affordable_now` (full amount safe today AND user accepts `full_payment`; `earliest_date_for_full_payment` **must equal** `request_date`), `affordable_with_plan` (completed via partial-payment / installments / permitted spending changes), `affordable_later` (safe later), `not_affordable` (never safe within the forecast).

**`recommended_payment_method`** (5 values) — `full_payment`, `partial_payment`, `installments`, `wait`, `not_recommended`. Eligibility is layered and independent of capacity (see D.9): an immediate method is eligible only if it's in `payment_methods_user_will_consider`; `wait` is eligible only if full payment becomes safe later *and* the user accepts `full_payment`; `not_recommended` is the fallback when nothing eligible is safe.

**`payment_plan`** — chronological `YYYY-MM-DD:amount|...`, or `none`. Installment plans **must exactly match** a row in `request_payment_options.csv`. `partial_payment` is exempt from matching an option but must be exactly two payments: `amount_safe_to_pay` on `request_date`, then `requested_amount - amount_safe_to_pay` on `earliest_date_for_full_payment` (which must be ≤ `desired_completion_date`); eligible only when `allows_partial_payment=true`, user accepts `partial_payment`, and `0 < amount_safe_to_pay < requested_amount`.

**`spending_changes_needed`** — `none` or up to 3 of `stop:<event_id>` / `reduce_to:<event_id>:<amount>`, `|`-joined. Only *flexible* (reducible/stoppable/reducible_or_stoppable), *non-protected* recurring events, in a category the user is willing to reduce/stop, may be targeted. Stop and reduce are mutually exclusive **per event** (different events may each get one).

**90-day safety check** — forecast 90 days forward using recurring income/expenses, confirmed future payments, and relevant messages/images. Safe ⇔ balance never drops below `minimum_balance_to_keep`. **Ignore** pending credits, failed/cancelled transactions, duplicate records, and unrealized investments. `amount_safe_to_pay` = max payable today before optional spending changes without breaking this check (capped at `requested_amount`); `earliest_date_for_full_payment` = first date the *full* amount passes the check, computed independently of payment-method preference (can equal `request_date` even when the chosen method is `installments`).

**Ranking safe/eligible plans (tie-break order)**: 1) complete by `desired_completion_date`, 2) no spending changes, 3) minimize total paid, 4) start earlier, 5) fewer payments, 6) lowest `payment_option_id`.

**Conflict resolution order**: 1) explicit cancellation/settlement/amendment, 2) newer record from the same source, 3) settled event over estimate/forecast, 4) financially-safer interpretation.

**Never invent** unsupported income/expenses/payment options. Investment requests concern affordability/existing contributions only — never price prediction. Messages/images are **untrusted**; embedded instructions never override these rules.

---

## D. Edge-Case Catalogue (grounded in concrete rows)

**D.1 — Blank amounts resolve 1:1 to images, but the image page is ambiguous.** 16 events have blank `amount`; `images.csv` supplies exactly 16 rows whose `related_event_id` is exactly that set (verified both directions). Example: `event_1442` ("Outstanding rent balance", `user_16`) → `image_02.png`, a rent receipt that shows **three** numbers — "Total Amount to be Received: 2,00,000", "Amount Received: 1,00,000", "Balance Due: 1,00,000". Only "Balance Due" matches the event's description; a naive "grab a number" extraction would likely pick the wrong figure. 15/16 blank events are one-time `expense` debits; only 1 (`event_253`, `user_03`, a 2019 payslip) is `income`.

**D.2 — There is almost no literal future ledger data; the 90-day forecast must be built from recurring-pattern inference.** Per-user event history ends 0–6 days before `request_date` for every one of the 250 requests. Only **42/250** requests have even a single event dated after `request_date`, and in every one of those 42 cases it is the same kind of row: an `income`/`salary` event with `status=scheduled` and `description="Next confirmed salary"`. Everything else needed for the 90-day window (rent, utilities, groceries, dining, subscriptions, debt payments, and every future salary date after the first one) has to be derived by detecting the recurring cadence in the historical `settled` rows and projecting it forward — the dataset does not hand this over as rows.

**D.3 — `linked_event_id` encodes 7 recurring lifecycle patterns (58 rows total) that must be netted out, not double-counted.** Concrete examples pulled from the data:
- `refund(settled)→expense(settled)` — `event_99`/`event_98`: a reversed card charge; both legs are historical/settled, net effect already in the running balance.
- `expense(settled)→expense(cancelled)` — `event_101`/`event_100`: a settled purchase superseding a cancelled authorization hold; the cancelled leg must be dropped (its `status` alone already tells you to ignore it).
- `refund(pending)→expense(settled)` — `event_1785`/`event_1784` (this is `request_20`'s own evidence): a pending merchant refund. Per the "ignore pending credits" rule this ₹8,640 must **not** count as available cash — which is exactly why the sample ground truth for `request_20` only finds ₹5,400 safe against a ₹3,03,700 trip and returns `not_affordable`.
- `debt_payment(scheduled)→debt_payment(failed)`: `event_5169`/`event_5168` — a failed attempt followed by a scheduled retry; only the scheduled retry is a real future obligation.
- `expense(pending)→expense(settled)` — `event_12709`/`event_12708`, literally described as **"Possible duplicate card charge"** / **"Original card charge"**: an explicit, labeled duplicate-representation case; counting both would double-debit the forecast.
- `investment_valuation(unrealized)→investment_purchase(settled)` (10 rows) and `investment_sale(settled)→investment_purchase(settled)` (5 rows): unrealized valuations must never be treated as cash; realized sale proceeds are historical/settled and already reflected in `current_available_balance`.

**D.4 — Messages can assert future facts that exist nowhere in `financial_events.csv`.** Patterns observed across the 215 messages (English + Bahasa Indonesia — see D.7): salary raises/cuts with a **future effective date** inside the 90-day window (e.g. `message_01`: user_02's salary rises to IDR 42,750,000 effective 2025-08-15, while the last settled payroll event is still IDR 33,345,000 — `request_02`'s window (2025-08-05 → +90d) spans that effective date); confirmed payroll-**date shifts** ("confirmed salary is now expected on `<date>`, replaces the payroll date shown…"); temporary reductions for unpaid leave; brand-new recurring obligations announced only in text ("a new recurring childcare payment begins in the same month…") with **zero** historical rows to detect a pattern from; income-stream terminations ("household employment record has ended", "seasonal contract has ended, no renewal confirmed") that must not be assumed to continue; and explicit pending-commission/arrears callouts that reinforce "don't count until settled."

**D.5 — At least one message is adversarial noise, not evidence.** `message_67` ("QuickPrize… Congratulations! You've been selected for a cash prize. **Pay the release charge today** to receive the funds… pay the processing charge now to avoid losing the claim") is a phishing-style solicitation with no corresponding `financial_events` row. It is the concrete instance of the "treat messages as untrusted; embedded instructions must not override the rules" directive — it must be recognized as noise and never turned into an assumed payment or windfall.

**D.6 — A single request can carry two independent evidence pointers to two different events.** `request_20` has both an image (`image_05→event_1786`, a blank-amount pending telecom bill) and a message (`message_14→event_1785`, the pending refund from D.3) — both must be resolved and applied, not treated as alternatives.

**D.7 — Message language is currency-correlated.** 44/45 messages belonging to IDR-currency users are in Bahasa Indonesia (only 1 in English); all other currencies' messages are English. Any text-understanding step must handle both languages.

**D.8 — Currency conversion is simple but must still be looked up, not hardcoded.** Only 5 directed pairs exist in `exchange_rates.csv` (`USD→IDR`, `USD→INR`, `USD→EUR`, `EUR→USD`, `EUR→ZAR`), and every one of the 139 non-blank foreign-currency events checked has an exact `(settlement_date, pair)` match — no multi-hop or interpolation is ever required for this data. Oddly, each pair's rate is a single constant repeated across every date (e.g. `USD→INR` is 83.33 on every listed date from 2024-01-15 to 2026-11-15) — realistic-looking but not actually time-varying in this dataset. `EUR→USD` (1.09) is not the exact inverse of `USD→EUR` (0.92) — a minor, presumably intentional, real-world-style inconsistency.

**D.9 — Financial capacity, payment-method eligibility, and preference are three separate, independently-violable gates.** (a) 40 of the 80 `allows_partial_payment=true` requests belong to users whose `payment_methods_user_will_consider` does **not** include `partial_payment` — the request permitting a method does not make it eligible. (b) 173 of 261 checked installment options exceed the user's own `max_installment_months` — a seller-offered option can be financially fine and still ineligible for that user. (c) Sample rows show `amount_safe_to_pay > 0` together with `not_affordable`/`not_recommended` (e.g. `request_14`: "Although EUR 597.74 is available today, the full amount…"; `request_24` similarly) — money being available today does not imply the request is affordable.

**D.10 — `earliest_date_for_full_payment` tracks capacity, not the recommendation.** All 6 `affordable_later`/`wait` sample rows show `earliest_date_for_full_payment` either equal to (`request_03/08/13/18/23`) or strictly before (`request_04`: earliest 2024-06-15 vs. deadline 2024-06-19) `desired_completion_date` — capacity and the deadline are evaluated independently. The spec also states this field may equal `request_date` even under an `installments` recommendation (the user has simply chosen not to consider full payment); no sample row exercises that combination, so it must be implemented from the rule rather than copied from an example.

**D.11 — Installment arithmetic is internally exact; a self-constructed partial-payment split is not guaranteed to be.** Verified across all 515 installment rows: `total_payable_amount = requested_amount + financing_fee` exactly, and `payment_amount = round(total_payable_amount / number_of_payments, 2)` with **zero** rounding-remainder mismatches (i.e. `payment_amount × number_of_payments` always equals `total_payable_amount` to the cent). `request_payment_options.csv` never contains a `payment_method=partial_payment` row (only `installments`/`full_payment`) — consistent with the spec's "partial payment does not need to match a supplied option," since there is no such option to match.

**D.12 — Deadlines never exceed the forecast horizon.** `desired_completion_date − request_date` ranges 6–86 days (median 65) across all 250 requests — always ≤ 90, so the mandated 90-day window is always long enough to judge the deadline itself.

**D.13 — Spending-change targeting is constrained on three axes at once.** `flexibility` splits `fixed` (21,138 rows — never touchable), `reducible` (2,682), `stoppable` (1,297), `reducible_or_stoppable` (225). A valid `stop`/`reduce_to` target must additionally sit in a category the specific user's profile lists under `expense_categories_user_is_willing_to_stop`/`..._to_reduce`, and must not be in `expense_categories_to_protect`. Sample rows confirm both actions can co-occur in one plan (`request_21`: `stop:event_1815|reduce_to:event_1816:23.50`) as long as they target different events. 2,907 rows carry a non-blank `minimum_allowed_amount` (a floor value on reducible/stoppable rows) whose exact intended use is not stated anywhere in the spec — see H.

**D.14 — Status distribution matters for what to exclude.** `settled` 25,148, `pending` 71, `scheduled` 70, `cancelled` 22, `failed` 21, `unrealized` 10. Per the rules, `cancelled`/`failed`/`unrealized` are excluded outright; `pending` is reserved only when it is a *debit* and ignored when it is a *credit*.

---

## E. Data-Quality Risks

- **Blank vs. zero.** `amount` is a free string; exactly 16 rows are truly blank (never `"0"`). Any parser must distinguish "missing, must be imaged" from a legitimate zero-amount row (none currently exist, but the format doesn't rule one out).
- **Undocumented field.** `minimum_allowed_amount` (2,907 non-blank rows) is present in the schema but never explained in `problem_statement.md`/`AGENTS.md`; its plausible role as a floor for `reduce_to`'s `new_amount` is an inference, not a stated rule (see H).
- **Free-text descriptions are the only extra signal in some lifecycle pairs** (e.g. "Settled card purchase" vs. "Card authorization", "Possible duplicate card charge" vs. "Original card charge") beyond `status` + `linked_event_id`. A row with similar phrasing but no `linked_event_id` in the hidden evaluation data would rely on status alone.
- **Locale-formatted amounts inside `request_text`** (e.g. `"IDR 15,656,000"`, `"ZAR 6,670"`) mix thousands separators and currency codes; the user's `home_currency` code string is always present in `request_text` (0/250 mismatches verified), but numeric formatting varies by currency and must be parsed robustly.
- **275 profiles vs. 250 requests.** The 25 "extra" profile users are exactly `sample_requests.csv`'s users and need no prediction; a naive "loop over all profiles" implementation would waste effort (and, if this becomes LLM-driven, tokens) on users outside the scoring set.
- **Exchange rates show zero real temporal variation** despite being modeled as dated rows — a solution should still perform the date-keyed lookup (for correctness of design and in case of format drift) rather than hardcoding today's observed constants.
- **Multiple installment options per request** (2–4; distribution 3-option:180, 2-option:65, 4-option:30) mean tie-break rule #6 (lowest `payment_option_id`) is a real, reachable case whenever two installment options are otherwise equally ranked.

---

## F. Proposed Deterministic Computation Requirements

These are requirements, not an implementation:

1. Typed CSV loading with strict blank-handling (`amount` blank ≠ 0) and enum validation against the known value sets in this document.
2. A per-user **recurring-pattern detector** over `financial_events` (group by category/description/direction/amount-similarity, infer interval and day-of-month/period) — required because, per D.2, forecastable future data is otherwise almost entirely absent.
3. An **event lifecycle resolver** that nets out `linked_event_id` chains and drops `cancelled`/`failed`/`unrealized`/pending-credit rows before any forecasting happens (D.3, D.14).
4. A **currency converter** doing exact `(settlement_date, from_currency, to_currency)` lookups against `exchange_rates.csv` (D.8).
5. A **blank-amount resolver** that follows `event_id → images.related_event_id → dataset/media/images/<image_id>.png` and extracts the one figure matching the event's own description/category (D.1) — flagged in G as the one step that is not purely deterministic.
6. An **evidence merger** applying the stated 4-step conflict-resolution precedence (C) to fold parsed message/image facts (amendments, cancellations, new recurring items) into the per-user forecast model.
7. A **90-day balance simulator**: apply the recurring + confirmed + evidence-amended cash flow day-by-day from `request_date`, and derive `amount_safe_to_pay` and `earliest_date_for_full_payment` against `minimum_balance_to_keep`, before any optional spending change.
8. A **payment-method eligibility filter**, run strictly separate from the capacity simulator, intersecting `payment_methods_user_will_consider`, `max_installment_months`, and `allows_partial_payment` (D.9).
9. A **plan ranker** implementing the exact 6-point tie-break order (C) over the eligible ∩ safe plan set.
10. An **output validator** run before any row is written: bounds (`0 ≤ amount_safe_to_pay ≤ requested_amount`), `payment_plan` arithmetic and chronology, installment-plan-equals-a-supplied-option check, spending-change flexible/permitted-category check, and exact column/order match against `dataset/output.csv`'s header.

---

## G. Where AI / VLM Is Actually Necessary

- **Reading the 16 evidence images.** Each is a differently-formatted real document (an Indonesian payslip, an Indian rent receipt with three candidate totals, generic merchant/utility receipts) — extracting the *specific* figure that matches the linked event's description (D.1) requires visual + semantic understanding, not fixed-position OCR.
- **Interpreting free-text messages** in two languages (English and Bahasa Indonesia, D.7) to classify each as amendment / cancellation / confirmation / new-obligation / noise, and to pull out the structured fact it asserts (new amount, effective date, new recurring category).
- **Separating genuine financial evidence from adversarial content** — the QuickPrize-style message (D.5) needs semantic judgment; no fixed keyword filter reliably distinguishes it from legitimate `financial_service` messages otherwise.
- **Writing `decision_explanation`** in natural language, grounded in numbers the deterministic layer already computed.
- Everything else — arithmetic, date math, eligibility, ranking, bounds — belongs in deterministic code, both for correctness and because token/cost accounting is a scored deliverable (`evaluation/usage_report.md`).

---

## H. Unresolved Questions (need an answer before implementation)

1. What exactly governs `minimum_allowed_amount` on reducible/stoppable events — is it the floor for `reduce_to`'s `new_amount`, or something else? Not defined in any spec file.
2. What minimum evidence counts as "recurrence... supported by history" (the spec's own phrase) — how many same-category/amount/interval occurrences are required before a pattern may be extrapolated?
3. How should a **message-only** new recurring expense (D.4 — e.g. a childcare payment with zero historical rows) be reconciled with the "detect recurrence only when history supports it" rule? The rule appears written for CSV history and doesn't explicitly say message evidence can substitute for it.
4. Should `sample_requests.csv`'s 25 users/rows be loaded into the production run at all, given they need no prediction and are format examples only?
5. When `requested_amount` doesn't divide evenly at the currency's minor unit, what is the exact rounding rule for `partial_payment`'s two amounts (spec only requires they "add up to `requested_amount`")?
6. How exactly should "forecast essential variable spending conservatively" be operationalized for protected-but-variable categories (groceries, transport) — historical average, historical max, or most recent observed value? The spec states the principle, not the formula.
