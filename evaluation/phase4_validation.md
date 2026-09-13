# Phase 4 Validation — Deterministic Financial-State Reconstruction & 90-Day Forecasting

Scope: request-date financial-state reconstruction, deterministic future-event projection, recurrence projection, variable-spending forecasting with backtesting, safe evidence integration, the 90-day chronological simulation, minimum-balance tracking, currency handling, and invariant enforcement.

Out of scope by design: `amount_safe_to_pay`, `affordability_status`, `recommended_payment_method`, `payment_plan`, `earliest_date_for_full_payment` as a recommendation, `spending_changes_needed`, `decision_explanation`, and `output.csv`. No LLM/VLM call runs inside the simulator, and no model training was performed.

---

## 1. Components Implemented

| Part | Module | Responsibility |
|---|---|---|
| A | `buyorwait/financial_state.py` | Forecast window, starting state, projected cash-flow event, daily state, reservations, exclusions, unresolved obligations, provenance, and the enums that keep the five event categories distinct (`historical_observed`, `confirmed_future`, `projected_recurring`, `variable_baseline`, `unresolved_obligation`). |
| B | `buyorwait/reconstruction.py` | Request-date starting state from the lifecycle-normalized ledger (reservations, exclusions, unrealized portfolio value) and confirmed future ledger rows inside the window. |
| C, D | `buyorwait/projection.py` | Deterministic recurrence projection: cadence-aware dates, spending classification, statistic selection, currency conversion, full provenance per projected occurrence. |
| E, F | `buyorwait/estimators.py` | The 13 candidate amount statistics as one explicit, configurable, Decimal-exact API — the choice is never buried in the simulator. |
| D, E, F, I | `buyorwait/forecast_config.py` | Every forecasting knob in one frozen dataclass: horizon, recurrence config (threshold not hardcoded), recent window, four per-class amount statistics, weak-projection switches. |
| G, H | `buyorwait/evidence_adjustments.py` | Consumes Phase 3 `EvidenceFact`s only — never raw message or image text — and turns them into future events, amendments, cancellations, exclusions, or unresolved obligations. |
| I, P | `buyorwait/simulator.py` | Chronological day-by-day simulation with documented same-day ordering and intraday low tracking. |
| U | `buyorwait/invariants.py` | Twelve loud invariant checks, run on every forecast by default. |
| — | `buyorwait/forecast.py` | `build_financial_state`: the orchestrator producing one `FinancialStateForecast` per request. |
| R | `code/evaluation/backtest_forecast.py` | Deterministic estimator backtest using only information available before each cutoff. |
| T | `code/evaluation/validate_phase4.py` | Full 250-request production validation and repeatability check. |

---

## 2. Forecast Window And Ordering

- Day zero is `request_date`. The horizon is 90 days, so the window is the closed interval `[request_date, request_date + 90 days]` and every forecast contains exactly **91 daily states**.
- Inclusion is inclusive at both ends: an event on `request_date` and an event on `request_date + 90` are both simulated; `request_date + 91` is excluded with reason `outside_window`.
- Same-day ordering is `(date, direction, kind, event_id)` with **debits applied before credits**. This is the conservative reading: it makes the recorded `intraday_low_balance` the worst intraday position of that day, so a payday cannot mask an earlier-in-the-day obligation. Ordering never changes the closing balance, only the recorded low, and the tie-break by `event_id` makes it fully deterministic.

---

## 3. Request-Date Starting State (Part B)

`current_available_balance` from `financial_profiles.csv` is the authoritative cash position on `request_date`; the ledger supplies reservations and pattern evidence, not a re-derived balance. This is a documented assumption: the production ledgers are partial (net settled flow does not reconstruct the stated balance for any user), so re-deriving cash from ledger rows would be wrong, and the latest CSV row is never used as "the balance".

Lifecycle-normalized treatment applied on top of it:

| Ledger situation | Effect on starting cash |
|---|---|
| `settled` row before `request_date` | already inside the reported balance; no adjustment |
| `settled` row dated on/after `request_date` | not present in the production dataset; guarded against and never applied |
| `pending` debit | **reserved** (subtracted from available cash) |
| `pending` credit | excluded (`pending_credit`) |
| `scheduled` debit dated before `request_date` | reserved |
| `scheduled` credit dated before `request_date` | excluded (`speculative_income`) |
| `scheduled` row inside the window | becomes a `confirmed_future` cash event, not a starting-state adjustment |
| `failed` | excluded (`failed_transaction`) — never a cash outflow |
| `cancelled` | excluded (`cancelled_transaction`) — never a cash outflow |
| superseded lifecycle leg (duplicate / retry original) | excluded (`superseded_duplicate`) |
| `unrealized` valuation / `non_cash` direction | excluded from cash, accumulated into `unrealized_investment_value` |
| realized investment sale (`settled`) | ordinary historical cash movement |
| row with a blank amount | excluded (`missing_amount`) **and** recorded as an unresolved obligation — never zero |

Statistics across the 250 production requests:

| Metric | Value |
|---|---|
| Requests reconstructed | 250 / 250 |
| Starting available cash — min / median / max | 648.69 / 70,056.90 / 136,691,818.94 (home currency) |
| Requests with at least one reservation | 48 |
| Reserved total — min / median / max | 25 / 2,520 / 2,941,200 |
| Requests carrying unrealized investment value | 8 |
| Ledger rows dated on/after `request_date` that are `settled` | 0 (no future-ledger leakage possible) |

---

## 4. Projection Layer (Parts C, D)

Projections come from four sources, each carrying provenance:

1. **Strong recurrence** — projected at the detected cadence (monthly keeps the day-of-month; weekly/biweekly step 7/14 days; any other strong cadence steps by the rounded mean observed interval).
2. **Confirmed future ledger rows** — `scheduled` rows landing inside the window.
3. **Validated evidence facts** — see §6.
4. **Weak recurrence** — projected, but labelled `projected_weak` and amount-estimated conservatively.

The occurrence threshold is **not** hardcoded: projection consumes `ForecastConfig.recurrence` (`RecurrenceDetectorConfig.minimum_occurrences`, currently 2) and a test asserts that changing it to 3 changes what is projected.

| Projection statistic | Count across 250 requests |
|---|---|
| Recurrence candidates classified `strong` | 2,419 |
| Recurrence candidates classified `weak` | 57 |
| Recurrence candidates classified `one_off` | 48 |
| Projected events — `projected_recurring` | 9,843 |
| Projected events — `variable_baseline` (flexible) | 2,108 |
| Projected events — `confirmed_future` | 139 |
| Projected events by certainty | `projected_strong` 11,740 · `projected_weak` 211 · `confirmed` 139 |
| Projected events by class | essential 8,949 · flexible 2,108 · income 1,033 |
| Projected events per request — min / median / max | 29 / 52 / 65 |

One-off candidates are never projected. Five requests end up with **zero** projected income; each has exactly one historical salary row, so projecting a second one would be inventing income the data does not support.

---

## 5. Amount And Variable-Spending Methodology (Parts E, F) With Backtest Evidence

### 5.1 Backtest design (Part R)

`code/evaluation/backtest_forecast.py` walks every production (user, category) recurring series with at least four amounts. For each cutoff it estimates the next amount — and separately the sum of the next six occurrences — from **only the observations before that cutoff**, then compares against what actually followed. No hidden or sample labels are used; only each user's own prior history. Errors are relative, so series in different currencies aggregate correctly. 2,429 series were backtested (12,468 essential, 2,183 flexible and 813 income evaluation points).

The cumulative six-occurrence view is the decision-relevant one: a 90-day forecast accumulates many occurrences, and a per-occurrence error that averages out does not threaten the minimum-balance invariant, whereas a systematic cumulative bias does.

### 5.2 Cumulative six-occurrence backtest

| Series class | Statistic | n | Mean abs err | Mean signed err | Under-estimation rate | Over-estimation rate | Worst abs err |
|---|---|---|---|---|---|---|---|
| essential | `last_observed` | 12468 | 0.1289 | +0.0054 | 44.4% | 55.6% | 1.1843 |
| essential | `maximum` | 12468 | 0.1951 | +0.1913 | 3.6% | 96.4% | 1.2614 |
| essential | `mean` | 12468 | 0.0668 | +0.0047 | 44.2% | 55.8% | 1.1353 |
| essential | `median` | 12468 | 0.0776 | +0.0036 | 44.7% | 55.3% | 1.1624 |
| essential | `minimum` | 12468 | 0.1863 | -0.1821 | 84.4% | 15.6% | 0.9902 |
| essential | `percentile_25` | 12468 | 0.1077 | -0.0875 | 73.4% | 26.6% | 1.0782 |
| essential | `percentile_75` | 12468 | 0.1158 | +0.0986 | 14.8% | 85.2% | 1.1843 |
| essential | `percentile_90` | 12468 | 0.1597 | +0.1535 | 6.1% | 93.9% | 1.2305 |
| essential | `recent_maximum` | 12468 | 0.1450 | +0.1233 | 14.3% | 85.7% | 1.1843 |
| essential | `recent_mean` | 12468 | 0.0847 | +0.0056 | 44.0% | 56.0% | 1.0842 |
| essential | `recent_median` | 12468 | 0.1044 | +0.0053 | 44.4% | 55.6% | 1.0782 |
| essential | `recent_minimum` | 12468 | 0.1372 | -0.1119 | 74.1% | 25.9% | 0.9902 |
| essential | `trimmed_mean` | 12468 | 0.0677 | +0.0047 | 44.2% | 55.8% | 1.1353 |
| flexible | `last_observed` | 2183 | 0.1053 | +0.0033 | 36.7% | 63.3% | 1.3726 |
| flexible | `maximum` | 2183 | 0.1472 | +0.1394 | 6.2% | 93.8% | 1.3726 |
| flexible | `mean` | 2183 | 0.0632 | +0.0045 | 37.1% | 62.9% | 0.5211 |
| flexible | `median` | 2183 | 0.0717 | +0.0036 | 37.6% | 62.4% | 0.5057 |
| flexible | `minimum` | 2183 | 0.1353 | -0.1277 | 65.8% | 34.2% | 0.4359 |
| flexible | `percentile_25` | 2183 | 0.0879 | -0.0617 | 55.9% | 44.1% | 0.4164 |
| flexible | `percentile_75` | 2183 | 0.0931 | +0.0697 | 16.4% | 83.6% | 0.6464 |
| flexible | `percentile_90` | 2183 | 0.1228 | +0.1116 | 9.2% | 90.8% | 0.8911 |
| flexible | `recent_maximum` | 2183 | 0.1172 | +0.0972 | 13.6% | 86.4% | 1.3726 |
| flexible | `recent_mean` | 2183 | 0.0706 | +0.0030 | 37.1% | 62.9% | 0.6547 |
| flexible | `recent_median` | 2183 | 0.0835 | +0.0028 | 36.6% | 63.4% | 0.6782 |
| flexible | `recent_minimum` | 2183 | 0.1113 | -0.0910 | 60.0% | 40.0% | 0.5790 |
| flexible | `trimmed_mean` | 2183 | 0.0634 | +0.0044 | 37.0% | 63.0% | 0.5211 |
| income | `last_observed` | 813 | 0.1809 | +0.0525 | 26.1% | 73.9% | 2.0844 |
| income | `maximum` | 813 | 0.2078 | +0.2057 | 1.7% | 98.3% | 2.0844 |
| income | `mean` | 813 | 0.1169 | +0.0663 | 25.7% | 74.3% | 1.5200 |
| income | `median` | 813 | 0.1392 | +0.1004 | 18.6% | 81.4% | 2.0844 |
| income | `minimum` | 813 | 0.2171 | -0.1288 | 52.3% | 47.7% | 1.2222 |
| income | `percentile_25` | 813 | 0.1417 | -0.0127 | 40.8% | 59.2% | 1.3513 |
| income | `percentile_75` | 813 | 0.1687 | +0.1599 | 4.6% | 95.4% | 2.0844 |
| income | `percentile_90` | 813 | 0.1897 | +0.1861 | 2.6% | 97.4% | 2.0844 |
| income | `recent_maximum` | 813 | 0.1844 | +0.1731 | 5.5% | 94.5% | 2.0844 |
| income | `recent_mean` | 813 | 0.1333 | +0.0582 | 27.6% | 72.4% | 2.0148 |
| income | `recent_median` | 813 | 0.1584 | +0.0767 | 23.6% | 76.4% | 2.0844 |
| income | `recent_minimum` | 813 | 0.1938 | -0.0752 | 46.2% | 53.8% | 1.8757 |
| income | `trimmed_mean` | 813 | 0.1172 | +0.0665 | 25.6% | 74.4% | 1.5200 |

### 5.3 Per-occurrence backtest

| Series class | Statistic | n | Mean abs err | Mean signed err | Under-estimation rate | Over-estimation rate | Worst abs err |
|---|---|---|---|---|---|---|---|
| essential | `last_observed` | 12468 | 0.1613 | +0.0222 | 43.6% | 56.4% | 1.1843 |
| essential | `maximum` | 12468 | 0.2218 | +0.2117 | 9.9% | 90.1% | 1.2614 |
| essential | `mean` | 12468 | 0.1263 | +0.0214 | 43.8% | 56.2% | 1.1353 |
| essential | `median` | 12468 | 0.1312 | +0.0204 | 43.9% | 56.1% | 1.1624 |
| essential | `minimum` | 12468 | 0.1838 | -0.1690 | 78.3% | 21.7% | 0.9902 |
| essential | `percentile_25` | 12468 | 0.1365 | -0.0726 | 61.2% | 38.8% | 1.0782 |
| essential | `percentile_75` | 12468 | 0.1606 | +0.1172 | 27.2% | 72.8% | 1.1843 |
| essential | `percentile_90` | 12468 | 0.1925 | +0.1732 | 16.7% | 83.3% | 1.2305 |
| essential | `recent_maximum` | 12468 | 0.1816 | +0.1422 | 21.9% | 78.1% | 1.1843 |
| essential | `recent_mean` | 12468 | 0.1345 | +0.0222 | 43.3% | 56.7% | 1.0842 |
| essential | `recent_median` | 12468 | 0.1453 | +0.0220 | 43.6% | 56.4% | 1.0782 |
| essential | `recent_minimum` | 12468 | 0.1551 | -0.0975 | 65.8% | 34.2% | 0.9902 |
| essential | `trimmed_mean` | 12468 | 0.1267 | +0.0214 | 43.7% | 56.3% | 1.1353 |
| flexible | `last_observed` | 2183 | 0.1310 | +0.0173 | 35.9% | 64.1% | 1.3726 |
| flexible | `maximum` | 2183 | 0.1694 | +0.1551 | 10.5% | 89.5% | 1.3726 |
| flexible | `mean` | 2183 | 0.1017 | +0.0178 | 35.8% | 64.2% | 0.5660 |
| flexible | `median` | 2183 | 0.1065 | +0.0169 | 35.9% | 64.1% | 0.6648 |
| flexible | `minimum` | 2183 | 0.1355 | -0.1170 | 60.6% | 39.4% | 0.5888 |
| flexible | `percentile_25` | 2183 | 0.1087 | -0.0496 | 48.2% | 51.8% | 0.5852 |
| flexible | `percentile_75` | 2183 | 0.1265 | +0.0843 | 23.2% | 76.8% | 0.6701 |
| flexible | `percentile_90` | 2183 | 0.1487 | +0.1269 | 15.6% | 84.4% | 0.8911 |
| flexible | `recent_maximum` | 2183 | 0.1463 | +0.1125 | 17.7% | 82.3% | 1.3726 |
| flexible | `recent_mean` | 2183 | 0.1064 | +0.0165 | 36.2% | 63.8% | 0.6547 |
| flexible | `recent_median` | 2183 | 0.1148 | +0.0163 | 35.7% | 64.3% | 0.6782 |
| flexible | `recent_minimum` | 2183 | 0.1227 | -0.0794 | 54.2% | 45.8% | 0.5840 |
| flexible | `trimmed_mean` | 2183 | 0.1019 | +0.0177 | 35.6% | 64.4% | 0.5660 |
| income | `last_observed` | 813 | 0.2460 | +0.0987 | 26.2% | 73.8% | 3.6671 |
| income | `maximum` | 813 | 0.2564 | +0.2506 | 3.9% | 96.1% | 3.6671 |
| income | `mean` | 813 | 0.1972 | +0.1060 | 28.2% | 71.8% | 3.1451 |
| income | `median` | 813 | 0.2130 | +0.1438 | 23.4% | 76.6% | 3.6671 |
| income | `minimum` | 813 | 0.2315 | -0.0988 | 47.5% | 52.5% | 2.9120 |
| income | `percentile_25` | 813 | 0.1990 | +0.0247 | 34.6% | 65.4% | 2.9473 |
| income | `percentile_75` | 813 | 0.2253 | +0.2037 | 10.6% | 89.4% | 3.6671 |
| income | `percentile_90` | 813 | 0.2411 | +0.2306 | 7.0% | 93.0% | 3.6671 |
| income | `recent_maximum` | 813 | 0.2387 | +0.2173 | 8.1% | 91.9% | 3.6671 |
| income | `recent_mean` | 813 | 0.2072 | +0.0989 | 28.2% | 71.8% | 3.1451 |
| income | `recent_median` | 813 | 0.2265 | +0.1211 | 24.2% | 75.8% | 3.6671 |
| income | `recent_minimum` | 813 | 0.2250 | -0.0415 | 41.7% | 58.3% | 2.9120 |
| income | `trimmed_mean` | 813 | 0.1973 | +0.1062 | 28.3% | 71.7% | 3.1451 |


### 5.4 Chosen defaults and rationale

| Series class | Chosen default | Why this one, from the measured evidence |
|---|---|---|
| Essential expense (variable) | `percentile_75` | Cumulative under-estimation falls from **44.7%** (median) to **14.8%**, which is the safety-critical direction for expenses, at a cost of only +9.9% cumulative bias and 0.116 mean absolute error. `percentile_90` buys a further 8.7 points of under-estimation for +5.5 points of bias and a 38% worse absolute error, and `maximum` (+19.1% bias) is the irrational-pessimism case the spec warns against. |
| Essential expense (effectively constant amounts, spread ≤ 1%) | `median` | Degenerate case — every statistic returns the same figure; the median is stated explicitly rather than inherited silently. |
| Flexible / discretionary expense | `trimmed_mean` | Best measured cumulative accuracy (0.0634 mean absolute error, +0.44% bias) tied with `mean`, but outlier-robust. The spec's conservatism mandate is written for *essential* variable spending; inflating discretionary spending would wrongly suppress affordability, and Phase 5 evaluates reductions against this honest baseline. |
| Income | `percentile_25` | The only candidate whose cumulative signed error is near zero (+2.5%) while every mean/median variant carries +6.6% to +14.4% over-forecast bias — and over-forecasting income is the unsafe direction. `minimum` (−9.9%) and `recent_minimum` (−4.2%) are needlessly punitive and carry worse absolute error. A single unusually high historical payment therefore never sets the projection. |

Every one of these is a `ForecastConfig` field, so the statistic is visible and swappable, never hidden inside the simulator.

### 5.5 Weak-recurrence policy, measured

Weak-recurrence income was initially excluded entirely. Measured against the production set, that zeroed projected income for **37 of 250 requests** whose salary history is clearly recurring (6–9 observations) but whose interval coefficient of variation exceeds the Phase 2 threshold because two monthly pay streams share one category — and it pushed requests breaching the minimum balance from 10 to 44. That is irrational pessimism, not conservatism.

The production policy projects weak recurrence at the mean observed interval, labelled `projected_weak`, with the conservative per-class amount statistic (`percentile_25` for income). `ForecastConfig.project_weak_income` and `project_weak_expenses` keep both switches explicit so a later phase can discount or drop weak projections. Weak projections are 211 of 12,090 simulated events (1.7%).

---

## 6. Evidence Integration (Parts G, H, M)

Only structured `EvidenceFact`s are consumed; no raw message or image text reaches this layer. Facts are applied in `(created_at, fact_id)` order.

| Fact class | Financial-state effect |
|---|---|
| `salary_raise`, `salary_cut` (amount + date) | amends projected income from the effective date |
| `expense_rent_increase` (amount + date) | amends projected rent from the effective date |
| `salary_date_shift` | moves the first projected salary occurrence |
| `salary_first`, `salary_confirmed`, `income_resumed` (amount + date) | confirmed future income, recurring when the fact states a recurrence |
| `income_terminated`, `income_suspended` | drops projected income in that category from the effective date |
| `expense_terminated` | drops projected expenses in that category from the effective date |
| `event_cancellation` | drops future occurrences sourced from the cancelled event |
| `expense_new_recurring`, `expense_amount`, `debit_failed_retry`, `event_amendment`, `event_delay` (amount + date) | confirmed future expense |
| `income_bonus_pending`, `prize_claim_*`, `refund_pending`, `refund_processing`, `foreign_currency_refund_processing`, `income_invoice_approved` | excluded as `speculative_income` — never counted as cash |
| `charge_disputed` | unresolved obligation; **no** cash relief assumed |
| `investment_unrealized_gain/loss` | excluded as `unrealized_investment` |
| `irrelevant`, untrusted content | excluded as `irrelevant_evidence` |
| any fact with `status = unresolved`, or a confirmed obligation missing its amount or date | unresolved obligation; never given an invented amount |

Income certainty is explicit end-to-end: settled (inside the reported balance), confirmed (scheduled row or confirmed evidence), projected-strong, projected-weak, speculative (excluded), unresolved (excluded but preserved).

---

## 7. Currency (Part J)

The canonical currency is each user's `home_currency`; every simulated amount is a `Decimal` in that currency and the original amount and currency are preserved on every event for provenance. Conversion uses the Phase 2 `ExchangeRateTable` with an exact `(date, from, to)` lookup — no interpolation, no nearest-date fallback, no invented rate.

When a rate is unavailable the event is **not** converted at 1:1: it is excluded with reason `missing_exchange_rate`, recorded as an unresolved obligation, and `has_unresolved_conversion` is raised on the forecast. Across the 250 production requests every foreign-currency ledger row inside scope had an exact rate for its settlement date, so **0 requests hit the missing-FX path in production**; the path is covered by unit tests instead.

Monetary precision: all arithmetic is `Decimal`, estimates are quantised to 2 decimal places with `ROUND_HALF_UP`, and no float ever touches a monetary value (floats appear only in interval statistics and in backtest reporting).

---

## 8. Simulation Results (Parts I, K, P)

| Metric | Value |
|---|---|
| Requests simulated | 250 / 250 |
| Simulation errors | 0 |
| Daily states per request | 91 (day 0 = `request_date`, through day 90 inclusive) |
| Cumulative future income — min / median / max | 0 / 116,288.94 / 277,020,000.00 |
| Cumulative essential expense — min / median / max | 821.86 / 83,345.18 / 119,009,972.82 |
| Cumulative flexible expense — min / median / max | 0 / 7,233.08 / 21,612,046.06 |
| Minimum projected balance — min / median / max | −30,285,169.93 / 53,226.09 / 120,292,812.98 |
| Safety margin (minimum projected balance − `minimum_balance_to_keep`) — min / median / max | −53,295,569.93 / 12,571.24 / 94,933,312.98 |
| Requests whose projected balance breaches `minimum_balance_to_keep` at some point | 10 (`request_80`, `_83`, `_103`, `_107`, `_153`, `_161`, `_188`, `_197`, `_233`, `_242`) |
| Requests with at least one unresolved obligation | 41 |
| Requests with a missing exchange rate | 0 |
| Total simulated cash-flow events | 12,090 |
| Full 250-request run time | ~0.9 s, single process, no GPU, no model call |

Each forecast exposes, for later phases: the minimum projected balance and the date it occurs, the full 91-day daily-state series (opening, income, essential, flexible, closing, intraday low, margin, breach flag, applied event ids), cumulative income and expense split by class, the safety margin, and the deficit dates.

### 8.1 Unresolved future obligations

| Unresolved cause | Count |
|---|---|
| Evidence fact unresolved; amount never assumed | 23 |
| Confirmed income without both an amount and a date | 13 |
| Future obligation confirmed but amount not stated | 5 |
| Rent increase without a stated amount | 3 |
| Ledger row has no amount; never defaulted to zero | 2 |
| Scheduled future row has no amount | 1 |

Every one of these is preserved on the forecast as an `UnresolvedObligation` with its direction, category, effective date and source, and is provably absent from the simulated event set (an invariant asserts the two sets are disjoint).

### 8.2 Exclusions recorded

| Reason | Count |
|---|---|
| `unresolved_evidence` | 23 |
| `superseded_duplicate` | 20 |
| `speculative_income` | 16 |
| `cancelled_transaction` | 13 |
| `failed_transaction` | 12 |
| `unrealized_investment` | 8 |
| `pending_credit` | 7 |
| `missing_amount` | 2 |

---

## 9. Conservative-Safety Analysis (Part Q)

| Conservative assumption | Where it is enforced | Measured cost |
|---|---|---|
| Pending debits are reserved before the forecast starts | `reconstruction.build_starting_state` | 48 requests, median reservation 2,520 |
| Pending credits, pending refunds, bonuses, prizes and unapproved invoices are never cash | starting state + evidence layer | 16 speculative-income exclusions, 7 pending credits |
| Failed and cancelled rows are never cash outflows | starting state | 25 rows excluded |
| Unrealized investment value is tracked apart from cash and can never fund a payment | starting state + invariant 5 | 8 requests |
| Missing amounts stay missing | starting state + evidence layer + invariant 7 | 11 unresolved obligations |
| Missing FX is never 1:1 | `_convert` returns `unresolved_rate` | 0 production cases, covered by tests |
| Essential variable spending is forecast at `percentile_75` | `ForecastConfig` | +9.9% cumulative over-forecast of essential spend |
| Income is forecast at `percentile_25` | `ForecastConfig` | +2.5% cumulative bias, the lowest non-punitive candidate |
| Weak recurrence stays labelled weak and income-side is switchable off | `projection.project_recurrence` | 211 events (1.7%) |
| Same-day debits are applied before credits | `simulator.ordering_key` | records the worst intraday low, never a better one |
| The whole 90-day window is checked, not just the request date | `simulate` + invariant 11 | 91 daily states per request |

Deliberately *not* pessimistic: weak recurring income is projected conservatively rather than erased (see §5.5), flexible spending uses an unbiased estimator, and a single historical income row is left unprojected rather than extrapolated in either direction.

---

## 10. Invariants (Part U)

`invariants.check_forecast_invariants` runs on every forecast by default (`build_financial_state(..., verify_invariants=True)`) and raises `InvariantViolation` loudly.

| # | Invariant | Result over 250 requests |
|---|---|---|
| 1 | No projected event before `request_date` | pass |
| 2 | No duplicate projected event id | pass |
| 3 | Failed transactions never become cash outflows | pass |
| 4 | Cancelled authorizations never become cash outflows | pass |
| 5 | Unrealized valuation never becomes spendable cash | pass |
| 6 | Speculative certainty never enters the simulation | pass |
| 7 | Missing amounts never become zero (no amount-less event simulated) | pass |
| 8 | Missing FX never becomes 1:1 (no home amount without an `unresolved_rate` marker) | pass |
| 9 | Every projected event carries provenance | pass |
| 10 | Every state transition is deterministic (re-run fingerprints identical) | pass |
| 11 | Minimum-balance calculation covers the whole window (91 daily states, endpoints checked) | pass |
| 12 | Sample users never enter a production forecast | pass |

Additional guards: no event dated beyond the horizon, the reported minimum matches the recomputed minimum of all daily lows, unresolved obligations are disjoint from simulated events, and starting available cash equals reported balance minus reservations.

---

## 11. Determinism

The validation harness builds every forecast twice in the same process and compares a fingerprint of starting cash, minimum projected balance, its date, the ending balance, and the full ordered `(event_id, date, amount)` series.

**Result: 0 mismatches across 250 / 250 requests.** Sources of non-determinism are structurally absent — no randomness, no wall-clock reads, no set iteration in ordering paths, no model calls inside the simulator, and every ordering key ends in a unique id tie-break.

---

## 12. Tests

| Suite | Tests |
|---|---|
| Phase 2 + Phase 3 (pre-existing) | 122 |
| Phase 4 unit (`code/tests/test_forecast.py`) | 42 |
| Phase 4 production (`code/tests/test_forecast_production.py`) | 27 |
| **Total** | **191 passed, 0 failed** |

Phase 4 coverage maps to the required list: starting balance; lifecycle-normalized starting state; failed transaction; cancelled authorization; settled and pending refunds; failed→retry; possible duplicate; unrealized valuation; realized sale; monthly, weekly, biweekly and irregular-cadence recurrence; weak recurrence (both switch positions plus its conservative statistic); one-off events; variable spending; essential vs flexible classification and reduce/stop permissions; confirmed future income; speculative future income; message-only future expense; message-only expense with a missing amount; future salary raise; income termination; same-day ordering; the 90-day boundary; currency conversion; missing FX; minimum-balance tracking; insufficient future cash; investment value excluded from cash; unresolved evidence; untrusted evidence; repeated-simulation determinism; Decimal and rounding behaviour; and absence of future `financial_events` leakage. No test hardcodes a hidden label and no code path is keyed to a request id.

---

## 13. Performance

Full 250-request reconstruct-project-simulate-validate pass: **~0.9 s** on one CPU core, plus a ~2 s estimator backtest over 2,429 series. No GPU, no training, no Colab, no network call, no new dependency (standard library plus the existing `pytest` dev dependency).

---

## 14. Known Limitations Carried Into Phase 5

1. Phase 2 groups recurrence by `(user, category)`, so a user with two distinct monthly pay streams in one category reads as weak recurrence. Phase 4 handles this conservatively rather than redesigning the detector; a sub-pattern split would be a Phase 2 change.
2. The 11 image-evidence facts remain unresolved because no VLM key is available; they stay unresolved obligations and never become cash.
3. `charge_disputed` facts assume no cash relief. If a dispute resolves in the user's favour the forecast understates cash — the safe direction.
4. The starting balance trusts `current_available_balance` as the as-of-`request_date` cash position. This is stated as an assumption, not derived.

---

## 15. Phase 5 Amendment — Two Calibration-Driven Corrections

Phase 5 ran the completed decision engine against the 25 public `sample_requests.csv` rows (development calibration only; those users never enter production). Two Phase 4 choices were measurably wrong against those labelled outcomes and were corrected:

1. **Safety is evaluated on daily closing balances, not the intraday low.** The original debits-before-credits intraday low made a payment on payday unsafe, pushing the earliest safe date one day (or one month) past every ground-truth date. Closing-balance semantics also satisfy the Phase 4 requirement that same-day ordering must not change financial safety. Same-day ordering is now credits-first and is reporting-only. Exact-date matches against the samples rose from 8/25 to 12/25.
2. **Essential variable spending is forecast at `median`, not `percentile_75`.** The cumulative backtest favoured `percentile_75` for its lower under-estimation rate, but end-to-end against the labelled samples `median` scored better on every axis (status 17/25 vs 16/25, method 19/25 vs 18/25, dates 14/25 vs 12/25). Labelled outcomes outrank a proxy metric, so `median` is the production default; `percentile_75` remains one config value away.

Restated Phase 4 figures under the corrected semantics: minimum projected balance median 55,499.30 (was 53,226.09 under intraday lows), safety margin median 14,848.08, and requests whose baseline forecast breaches the minimum balance **7** (was 10). Requests processed 250/250, errors 0, repeatability mismatches 0, invariants all passing. Every other figure in this document is unchanged.

---

## 16. Phase 6 Amendment — Lapsed Recurring Expenses

Phase 6's sample calibration surfaced a third genuine defect: `projected_dates` steps forward from a series' last observation until it reaches the window, which silently **resurrects recurring expenses that have already stopped**. A monthly subscription last charged four months before `request_date` was still projected three more times inside the forecast.

The correction is a general staleness rule in `projection.project_recurrence`: a series is not projected when its last observation predates `request_date` by more than `max_staleness_multiple` (default **1.0**) times its own cadence — i.e. it has already missed a full cycle. `ForecastConfig.staleness_scope` (default **`"expense"`**) controls which directions the rule applies to; `"all"`, `"income"` and `"none"` are the other settings.

Scope and threshold were chosen by measurement, not preference. Against the 25 solved samples, end-to-end field agreement (status + method + plan + date + safe amount + spending changes, 150 comparisons) scored: no staleness rule 93, expense-scoped at 1.0 **96**, expense-scoped at 1.25/1.5 93, income-scoped at any threshold 90. Applying the rule to income was measurably harmful and is therefore off by default; dropping a lapsed charge is a factual inference about a stopped subscription, whereas dropping lapsed income would only be a guess. Regression tests cover a lapsed series, a current series, and all three scope settings.

Restated Phase 4 figures: projected events 11,933 (9,686 recurring, 2,108 variable baseline, 139 confirmed), minimum projected balance median 55,499.30, and **7** requests whose baseline forecast breaches the minimum balance. Requests processed 250/250, errors 0, repeatability mismatches 0, all invariants passing.

A fourth candidate correction was tested and **rejected**: decomposing composite `(user, category)` series into monthly day-of-month sub-streams. It is theoretically attractive (several users receive two monthly pay streams in one category) but scored worse on every sample axis, so it was removed rather than kept behind a flag.
