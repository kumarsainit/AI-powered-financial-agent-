# Phase 2 Validation — Canonical Domain & Historical Pattern Layer

Scope: ingestion/domain layer, event lifecycle normalization, historical recurrence detection, historical variable-spending representation, spending-change metadata, currency lookup, and request-level bundling. No simulator, no plan generation, no affordability decision, no `output.csv`, no LLM calls, and no model training are part of this phase.

Code lives under `code/buyorwait/`; tests under `code/tests/`.

---

## 1. Components Implemented

| Part | Module | Responsibility |
|---|---|---|
| A | `buyorwait/domain.py` | Typed, immutable domain objects and enums for every canonical concept (profile, request, event, recurring candidate, future-projected-event shape, payment option, message, image evidence, spending-change candidate, exchange-rate record, lifecycle chain/member, historical spending observation). |
| A | `buyorwait/parsing.py` | Shared, reusable low-level parsers (dates, timestamps, decimals, booleans, enums, pipe-lists) that raise a single `DataIntegrityError` type with row/field context on any invalid required value. |
| B | `buyorwait/ingestion.py` | Deterministic CSV loading, row-to-domain-object conversion, cross-file relationship construction, and the `Dataset` aggregate. Scopes the production dataset to `requests.csv`'s users only. |
| C | `buyorwait/lifecycle.py` | Connected-component resolution over `linked_event_id`, classification into 7 known lifecycle patterns (+ standalone + unclassified fallback), and a disposition (`include` / `exclude_superseded` / `exclude_pending_credit` / `exclude_noncash`) per chain member. |
| D | `buyorwait/recurrence.py` | Configurable historical-recurrence detector producing `strong` / `weak` / `one_off` candidates per (user, category), with cadence classification and full observation metadata. |
| E | `buyorwait/spending_history.py` | Per (user, category) historical spending statistics (total, average, median, maximum, recent average/maximum, frequency) plus the raw individual observations. |
| F | `buyorwait/spending_change.py` | Normalizes every non-fixed event into a `SpendingChangeCandidate` carrying its flexibility, `minimum_allowed_amount` floor, the user's reduce/stop permissions, protected-category flag, and its recurrence classification. |
| G | `buyorwait/currency.py` | `ExchangeRateTable`: exact `(date, from_currency, to_currency)` lookup and conversion, same-currency short-circuit, no interpolation, no external calls. |
| H | `buyorwait/bundle.py` | `build_request_bundle`: assembles one `RequestBundle` per request from all of the above, without deciding affordability or resolving message/image facts. |

`code/main.py` was not modified — no entry point wiring was in scope for this phase.

---

## 2. Dataset Row Counts Loaded

Production-scoped load (`ingestion.load_dataset("dataset")`), i.e. restricted to the 250 users referenced by `requests.csv`:

| File | Rows loaded (production scope) | Rows in file (all users) |
|---|---|---|
| `requests.csv` | 250 | 250 |
| `financial_profiles.csv` | 250 | 275 |
| `financial_events.csv` | 23,054 | 25,342 |
| `request_payment_options.csv` (requests covered) | 250 | 275 |
| `messages.csv` (requests with a message) | 116 | — |
| `images.csv` (requests with an image) | 11 | 16 |
| `exchange_rates.csv` | 134 (not user-scoped) | 134 |

The 25 users behind `sample_requests.csv`, and every row of every file that belongs only to them, are excluded from the production `Dataset` by construction (filtered at load time on `user_id` membership in the set derived from `requests.csv`), not merely by convention.

---

## 3. Domain-Model Coverage

All 13 canonical concepts required by Part A are represented:

1. User financial profile → `UserFinancialProfile`
2. Request → `Request`
3. Financial event → `FinancialEvent`
4. Recurring event candidate → `RecurringEventCandidate`
5. Future projected event → `FutureProjectedEvent` (shape defined; intentionally never constructed in this phase — projection is out of scope per Part D)
6. Payment option → `PaymentOption`
7. Message → `Message`
8. Image/evidence reference → `ImageEvidence` (resolves to `dataset/media/images/<image_id>.png`)
9. Spending-change candidate → `SpendingChangeCandidate`
10. Exchange-rate record → `ExchangeRateRecord`
11. Financial-event lifecycle relationship → `LifecycleChain` / `LifecycleMember`
12. Historical spending observation → `HistoricalSpendingObservation`
13. Request-specific financial-state inputs → `RequestBundle` (`bundle.py`)

No downstream module (lifecycle, recurrence, spending history, spending change, currency, bundle) reads a CSV row or a `dict[str, str]` — raw parsing terminates entirely inside `ingestion.py`.

---

## 4. Lifecycle Normalization Results

Computed by resolving every production user's full event history (via `LifecycleChain`s built while constructing all 250 request bundles). An earlier version of this section reported only a per-**chain** count per pattern; because 7 of the 8 patterns are 2-member chains, summing those chain counts as if they were event counts undercounts the true event total by exactly the chain-member multiplier. Corrected below with both views and an explicit event-level reconciliation (see the audit note at the end of this section).

| Pattern | Chains found | Members per chain | Events accounted for |
|---|---|---|---|
| `standalone` | 22,950 | 1 | 22,950 |
| `refund_settled` | 12 | 2 | 24 |
| `refund_pending` | 7 | 2 | 14 |
| `cancelled_authorization` | 7 | 2 | 14 |
| `failed_retry` | 7 | 2 | 14 |
| `possible_duplicate` | 6 | 2 | 12 |
| `unrealized_valuation` | 8 | 2 | 16 |
| `realized_sale` | 5 | 2 | 10 |
| `unclassified_link` | 0 | — | 0 |
| **Total** | **23,002 chains** | — | **23,054 events** |

### Exact reconciliation

| Quantity | Count |
|---|---|
| Total input events (production scope) | 23,054 |
| Events in a linked (non-standalone) chain | 104 (= 52 chains × 2 members) |
| Standalone events | 22,950 |
| Events classified into a known lifecycle pattern (refund_settled/refund_pending/cancelled_authorization/failed_retry/possible_duplicate/unrealized_valuation/realized_sale) | 104 |
| Events classified as `unclassified_link` | 0 |
| Events with no lifecycle classification at all | **0** |
| Included events (`disposition = include`) | 23,019 |
| Excluded events, total | 35 |
| — `exclude_superseded` | 20 |
| — `exclude_pending_credit` | 7 |
| — `exclude_noncash` | 8 |
| Unique event IDs seen across all chains | 23,054 (no ID appears in more than one chain) |
| Duplicate event-ID appearances across chains | 0 |

`22,950 + 24 + 14 + 14 + 14 + 12 + 16 + 10 = 23,054` (event view) and `23,019 + 20 + 7 + 8 = 23,054` (disposition view) both reconcile exactly against the 23,054 production events; both always did — only the chain-count table's column label was ambiguous. Verified programmatically by resolving lifecycle chains per user across the full production dataset (not sampled) and tracking every `event_id` into a single set: the set's size equals 23,054 with zero duplicate insertions and zero events left unvisited.

### Verification checklist

- **A — no 3+ node chains:** confirmed. Chain size distribution across the full production dataset is `{1: 22950, 2: 52}` — no chain of size 3 or more exists.
- **B — every linked event references a valid event:** confirmed. `ingestion.py`'s `_validate_relationships` already rejects a dangling `linked_event_id` at load time (see `tests/test_ingestion.py::test_unresolvable_linked_event_id_fails_clearly`); an independent re-check found 0 dangling references among the 23,054 production events.
- **C — no event is accidentally counted twice:** confirmed. Every event belongs to exactly one `LifecycleChain` (connected-component resolution visits each `event_id` once); 23,054 unique IDs observed, 0 duplicate appearances.
- **D — no event is silently dropped:** confirmed. 0 events have no lifecycle classification; every event is either `standalone` or a member of exactly one pattern chain.
- **E — a refund does not remove the original expense when both records carry real economic effect:** confirmed for `refund_settled` — both the refund and the original settled expense are `include` (12 chains × 2 = 24 events), since a settled refund on a later date is a second, real cash movement, not a correction of the first.
- **F — a cancelled authorization does not affect available balance:** confirmed — in every `cancelled_authorization` chain, the cancelled leg is `exclude_superseded` (7/7).
- **G — failed transactions do not affect available balance:** confirmed — in every `failed_retry` chain, the failed leg is `exclude_superseded` (7/7); a failed event's own `status` also excludes it independently at the general status-filtering level used elsewhere in this phase.
- **H — failed→retry chains do not double-count the failed attempt:** confirmed — only the `scheduled` retry is `include`; the `failed` leg is `exclude_superseded` in all 7 chains.
- **I — `possible_duplicate` handling does not delete the legitimate charge:** confirmed — in all 6 chains, the **target** (the original, already-`settled` charge) is `include`; the **source** (the later `pending` duplicate) is the one marked `exclude_superseded`.
- **J — unrealized investment valuation never becomes spendable cash:** confirmed — the valuation leg is `exclude_noncash` in all 8 `unrealized_valuation` chains.
- **K — realized investment sale is handled per the challenge rules:** confirmed — both the sale and the original purchase are `include` in all 5 `realized_sale` chains, i.e. both are preserved as real, distinct historical cash movements (money out at purchase, money in at sale) rather than netted or dropped; neither is treated as unrealized or as predicting future asset prices.

Zero chains fell into the `unclassified_link` fallback on the production dataset — every linked pair observed matches one of the 7 patterns identified in Phase 1's forensic analysis (Section D.3). The `possible_duplicate` pattern (6 cases) is the one that actively overrides a status-only rule — each of these is a `pending` debit that would otherwise be reserved as a real future obligation, but is excluded here because it is linked to an already-`settled` original charge.

---

## 5. Recurrence Candidates Detected

Detector configuration used (see `RecurrenceDetectorConfig` in `buyorwait/recurrence.py`) — **explicitly configurable, not hardcoded into the detection logic**:

- `minimum_occurrences = 2` (the Section I / Q2 evidence-backed default: a single historical occurrence is never treated as recurring, and 2+ occurrences at a consistent interval is the safest supported minimum)
- `weekly_days = (5, 9)`, `biweekly_days = (12, 16)`, `monthly_days = (27, 33)`
- `max_coefficient_of_variation = 0.35` (interval regularity threshold separating `strong` from `weak`)

Results across all 250 production users' (user, category) groups:

| Strength | Count |
|---|---|
| `strong` | 2,419 |
| `weak` | 57 |
| `one_off` | 48 |

| Inferred cadence | Count |
|---|---|
| `monthly` | 1,651 |
| `weekly` | 294 |
| `biweekly` | 205 |
| `other` | 269 |
| `none` (one-off or irregular) | 105 |

Every candidate retains: `user_id`, `category`, `event_type`, `source_event_ids`, `occurrence_count`, `observed_dates`, `observed_intervals_days`, `amounts`, `currency`, `inferred_cadence`, `strength`, and a human-readable `reason` string. No future transaction is invented or projected — `detect_recurrence` only describes what the history shows.

---

## 6. Historical Spending Statistics Available

`compute_spending_observations` produced **2,261** `HistoricalSpendingObservation` records (one per (user, category) with at least one `settled` debit). Each retains, per Part E's requirement: `historical_total`, `historical_average`, `historical_median`, `historical_maximum`, `recent_average`/`recent_maximum` (last 3 observations by default, configurable via `SpendingHistoryConfig.recent_window`), `occurrence_count`, `spending_frequency_days`, `observed_date_range`, and the full tuple of individual `(date, amount)` observations — not aggregates only.

**No forecasting statistic has been selected.** All five candidate statistics (total, average, median, maximum, recent) are computed and exposed side by side for every category; nothing in this phase or its tests treats any one of them as "the" forecast value.

Scope decision made in this phase (not a forecasting decision): an observation counts as "historical spending" only when `direction=debit` and `status=settled`. Pending debits, credits, and non-settled events are excluded from these statistics — this only defines what counts as a confirmed past data point, not how a future value should be estimated from it.

---

## 7. Records That Could Not Be Normalized

None on the production dataset. Every one of the 52 linked-event pairs found among the 250 production users' histories matched one of the 7 known lifecycle patterns (0 `unclassified_link` chains). The `UNCLASSIFIED_LINK` fallback path exists and is exercised by `tests/test_lifecycle.py::test_unclassified_link_falls_back_to_include` against a synthetic combination, so behavior on an unforeseen hidden-data pattern is defined (conservative include-both, flagged pattern) rather than an unhandled crash — but no real record needed it.

Similarly, zero rows across `financial_events.csv`, `requests.csv`, `financial_profiles.csv`, `messages.csv`, `images.csv`, and `exchange_rates.csv` (production scope) failed to parse; the one previously-undocumented nullable field discovered while building this phase — `settlement_date` is blank on all 10 `unrealized` `investment_valuation` rows (they never settle) — was made nullable in the domain model rather than treated as an error.

---

## 8. Test Results

Run with:

```bash
python3 -m unittest discover -s code/tests -t code
```

**78 tests, 78 passed, 0 failed, 0 errored.**

| File | Tests | Focus |
|---|---|---|
| `test_parsing.py` | 13 | Date/timestamp/decimal/boolean/enum/pipe-list parsing, required-vs-optional handling |
| `test_ingestion.py` | 15 | Real-dataset loading, joins, production-scope filtering, malformed-row failures (invalid amount, missing id, invalid enum, unresolvable link), blank-amount handling |
| `test_lifecycle.py` | 8 | All 7 named patterns, standalone, unclassified fallback, dangling link, double-count prevention |
| `test_recurrence.py` | 11 | One-off, exactly-two-occurrences, 3+ monthly, weekly, inconsistent intervals, "repeated but non-recurring-looking", configurable threshold, status filtering, per-category independence |
| `test_spending_change.py` | 7 | Fixed exclusion, reducible/stoppable/reducible_or_stoppable, `minimum_allowed_amount` floor, protected flag, pending exclusion, cross-user guard |
| `test_currency.py` | 6 | Same-currency shortcut, exact-date conversion, missing-rate failure, date-specific lookup |
| `test_bundle.py` | 9 | Production-only request isolation, sample-user exclusion, unknown-request handling, cross-user event integrity, no double-counted lifecycle chain, `minimum_allowed_amount` floor respected, shared exchange-rate table |
| `test_spending_history.py` | 9 | Average/median/maximum, recent-window configurability, individual-observation retention, credit/pending/blank-amount exclusion |

No test hardcodes a production answer or a specific `request_id`'s expected output; assertions check general rules (bounds, disjointness, structural invariants) rather than known numeric results.

---

## 9. Assumptions Made In This Phase

1. **Recurrence minimum-occurrence threshold = 2** — configurable, not hardcoded as a permanent business rule (per Section I / Q2, marked UNRESOLVED — SAFE ASSUMPTION REQUIRED; this phase treats it as a default, not a fact).
2. **Interval-regularity cutoff (coefficient of variation ≤ 0.35) separates `strong` from `weak` recurrence** — not sourced from the spec; chosen as a reasonable, documented, configurable default.
3. **Cadence bands** (weekly 5–9 days, biweekly 12–16, monthly 27–33) — derived from the interval statistics observed in Phase 1's forensic analysis, not stated in the spec.
4. **"Historical spending" (Part E) is scoped to `settled` debit events only** — a data-scope decision, independent of which statistic a later phase picks as the forecast.
5. **`recent` window defaults to the last 3 observations** — configurable; not a forecasting decision.
6. **2-decimal-place monetary rounding** is used in `ExchangeRateTable.convert` — matches the dataset-wide convention documented in Section I / Q5, flagged there as an inferred convention rather than an explicit rule.
7. **`settlement_date` is nullable** on `FinancialEvent` — discovered empirically (blank on all 10 `unrealized` `investment_valuation` rows) while implementing this phase; not previously documented in Phase 1.

---

## 10. Remaining Unresolved Decisions For Later Phases

- **Final variable-spending forecasting statistic: intentionally not selected in Phase 2.** All of total/average/median/maximum/recent-average/recent-maximum are computed and available on `HistoricalSpendingObservation`; no claim is made here that any one of them is correct. This remains the highest hidden-test-risk open question carried over from Phase 1 Section I (Q6).
- Whether a `weak` recurrence candidate should ever be projected forward, and with what confidence discount, is undecided — this phase only classifies, it does not project.
- `FutureProjectedEvent` is defined but never populated; the 90-day forward projection logic itself is out of scope for Phase 2.
- How `SpendingChangeCandidate.recurrence_classification` (including `weak`/`one_off` cases) should gate eligibility for an actual `stop`/`reduce_to` recommendation is undecided — this phase normalizes and attaches the classification but does not filter or decide on it.
- Message/image evidence (amendments, confirmations, new recurring facts with no historical support, e.g. the childcare-payment pattern from Phase 1 Section I / Q3) is not yet parsed into structured facts or merged into the recurrence/spending-history layers — that is explicitly the "evidence phase," not this one.
- The 90-day balance simulator, candidate-plan generation, plan ranking, affordability-status determination, and `output.csv` generation are all untouched, per this phase's strict scope.

---

Phase 2 is complete per the stated completion condition: canonical domain objects exist, dataset relationships are represented, lifecycle normalization works, recurrence detection works, historical spending observations are preserved, spending-change metadata is normalized, currency lookup is implemented, request-level bundles exist, all 78 tests pass, and this document exists.
