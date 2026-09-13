# Phase 8 Validation — Performance, Cost, and Production Hardening

Scope: measurement-driven performance/cost/robustness hardening of the existing, already-correct Phase 3–7 pipeline. No financial-model, forecasting-statistic, or sample-optimization changes are included. All numbers below were measured directly on this machine against the real `dataset/` (never modified) and the real `code/` tree; none are invented.

---

## 1. Baseline Measurements

| Metric | Value |
|---|---|
| Test count (pytest — the runner phases 3–7 actually use) | **653 passed** |
| Test count via `unittest discover` | 122 (many phase 3–7 test files use bare pytest-style functions/fixtures, which `unittest` cannot collect — see note below) |
| Full test-suite wall time (pytest) | 15.1–15.4s |
| Production pipeline wall time (`code/main.py`, cold) | ~1.22s average of 5 runs (1.175–1.247s range) |
| Peak resident memory (production run) | ~85.5 MB (`maximum resident set size`), ~77.8 MB peak footprint |
| `output.csv` size / rows | 52,933 bytes, 251 lines (250 data rows + header) |
| Requests processed | 250 (matches `requests.csv`) |
| Model/API calls in production | 0 (no `GOOGLE_API_KEY`/`GEMINI_API_KEY` set) |
| Token usage | 0 (no calls made) |

**Note on the 653 vs. 122 test-runner discrepancy:** this is not a Phase 8 finding requiring a fix — it is a pre-existing fact about how the test suite is written (pytest-style fixtures in `test_pipeline.py`, `test_output.py`, `test_decision.py`, etc., introduced from Phase 3 onward). `pytest` is the correct, already-in-use runner for this repository; `python3 -m unittest discover` under-collects and should not be used to judge suite health. This document uses `pytest` throughout.

No prior stage-by-stage timing instrumentation existed in the pipeline; Phase 8 measured stage costs by profiling (`cProfile`) rather than adding permanent instrumentation, since the pipeline is small enough (~1.2s total) that dedicated timing code would be unnecessary abstraction for its own sake.

---

## 2. Profiling Results

`cProfile` over a full `run_pipeline("dataset", None)` call, sorted by cumulative time (top contributors, pre-fix):

| Function | Calls | Cumulative time | Share of run |
|---|---|---|---|
| `simulator.simulate` | 2,494 | 1.066s | ~46% |
| `ingestion.load_dataset` | 1 | 0.554s | ~24% |
| `planning.generate_candidates` | 250 | 0.894s (overlaps with simulate) | — |
| `ingestion._event_from_row` | 25,342 | 0.433s | ~19% |
| `recurrence.detect_recurrence` | 250 | 0.175s | ~8% |

`simulator.simulate` and dataset ingestion together account for roughly 70% of total runtime; everything else (recurrence detection, lifecycle resolution, evidence extraction, explanation generation, output writing) is individually small.

---

## 3. Identified Bottleneck

Inside `simulator.simulate`, the day-by-day forecast loop called `sorted(by_day.get(current, ()), key=ordering_key)` **once per forecast day** (91 days per call), even though each day's event bucket only needs to be sorted once and the sort key's primary component (`event.when`) already matches the day boundary. Across the full run this produced **~227,000 `sorted()` invocations** (2,494 `simulate()` calls × ~91 days) and 249,030 calls to the `ordering_key` function, for buckets that are almost always 0–3 events long — i.e. the per-call sort overhead (function-call and comparison setup cost) dominated the actual comparison work.

This is a genuine, measured hotspot — not a theoretical one — and it is fixed by an algorithmically equivalent, not a behaviorally different, change (see below).

---

## 4. Changes Made

### 4.1 `code/buyorwait/simulator.py` — sort once instead of once per day

**Before:** events were bucketed by day first, then each day's bucket was individually sorted inside the 91-iteration day loop.

**After:** the full eligible event list (already filtered to `amount_home is not None` and `window.contains(event.when)`) is sorted **once** by the same `ordering_key`, then bucketed by day while iterating the already-sorted list. Because `ordering_key`'s first component is `event.when`, a single stable sort of the whole list produces per-day sub-sequences in exactly the same relative order as sorting each day's bucket independently would.

**Reason:** measured hotspot (Section 3); the fix removes ~227,000 redundant `sorted()` calls without changing any comparison, tie-break, or business rule.

**Proof of equivalence:** `code/tests/test_performance.py::test_simulate_matches_the_naive_per_day_sort_reference` captures real `(opening_balance, events, window, minimum_balance)` argument tuples from 40 real production requests (via a monkeypatch on `planning.simulate`), then independently re-runs both the current implementation and a self-contained reference re-implementation of the *original* per-day-sort algorithm against each captured call, asserting identical final closing balances. All 40 sampled calls match exactly.

### 4.2 `code/buyorwait/output_writer.py` — atomic, permission-preserving writes

**Before:** `write_output_csv` opened the target path directly in `"w"` mode and wrote rows as they were produced. A crash, exception, or interruption partway through iterating the `records` generator would leave a truncated/corrupted `output.csv` at the one authoritative path, and a partially-written file could be mistaken for a complete one.

**After:** rows are written to a hidden temporary file in the same directory (guaranteeing the same filesystem, which `os.replace` requires for atomicity), flushed and `fsync`ed, then atomically renamed onto the target path with `os.replace`. On any exception during writing, the temporary file is removed and the exception re-raised (not swallowed) — the previous `output.csv`, if any, is left completely untouched. The temp file's permissions are explicitly set to `0o644` so that `output.csv`'s permissions match what direct-write `open(path, "w")` would have produced (an initial version of this fix left `output.csv` at `0o600`, `tempfile.mkstemp`'s default — caught and corrected before commit, see `test_output_writer_replaces_atomically`).

**Reason:** demonstrated production-safety gap identified in Section 9/10 of the Phase 8 checklist (a real, not hypothetical, partial-write risk), not a performance change.

**Verification:** `test_output_writer_leaves_no_partial_or_temp_file_on_failure` constructs a records generator that raises mid-iteration and asserts the pre-existing target file is byte-unchanged and no `.tmp` file remains; `test_output_writer_replaces_atomically` confirms the happy path and file mode.

No other production code was changed.

---

## 5. Before/After Runtime

Clean, profiler-free wall-clock timing, 5 runs each, before vs. after the `simulator.py` fix (identical dataset, identical machine, same session):

| | Before | After |
|---|---|---|
| Average | 1.2188s | 1.1817s |
| Minimum | 1.1753s | 1.1432s |
| Runs (s) | 1.175, 1.217, 1.247, 1.222, 1.233 | 1.222, 1.205, 1.169, 1.169, 1.143 |

**~3% average wall-clock improvement.** This is modest by design: the pipeline was already fast (~1.2s for 250 requests, ~4.9ms/request), and per Section 7 of the Phase 8 instructions ("if the current architecture is already comfortably efficient, do not redesign it"), no further restructuring was attempted once the one measured hotspot was addressed. The `output_writer.py` change is a safety fix, not a performance one, and adds a negligible, unmeasurable amount of overhead (one extra `fsync` and `os.replace` per run).

---

## 6. Memory

| | Before | After |
|---|---|---|
| Maximum resident set size | 85,491,712 bytes (~81.5 MiB) | 86,097,920 bytes (~82.1 MiB) |
| Peak memory footprint | 77,791,856 bytes (~74.2 MiB) | 78,381,656 bytes (~74.7 MiB) |

No meaningful change (within normal run-to-run noise for a process this size).

**Scaling check (item 7):** a synthetic in-memory benchmark fed increasing event counts (500 → 8,000, roughly 60× the largest real per-user history of ~129 events) through `resolve_lifecycle`, `detect_recurrence`, and `compute_spending_observations` independently. All three scaled linearly (doubling input size roughly doubled runtime: e.g. `resolve_lifecycle` went 0.0006s → 0.0102s across a 16× size increase, consistent with O(n) to O(n log n), not O(n²)). No redesign was warranted or performed.

---

## 7. Model/API Call Result

- **0 model calls** in the production run (no `GOOGLE_API_KEY`/`GEMINI_API_KEY` in the environment), confirmed by `UsageTracker.total_calls() == 0` after a full 250-request run — enforced going forward by the new test `test_no_model_calls_occur_without_an_api_key`.
- Evidence extraction is deterministic-first: `extract_message_facts` never calls a model (pure text parsing); `extract_image_fact` is called at most once per image, and every request has at most one image, so no duplicate-call risk exists structurally.
- `extract_image_fact` already consults an on-disk cache (`cache_get`/`cache_put` in `usage.py`, keyed by a content hash) before any model call, and records a `ModelUsageRecord` (including `error_message`) on every path — missing SDK, missing key, or a raised exception — so a failure degrades to unresolved evidence and remains observable rather than crashing or being silently swallowed. No credential value is ever printed, logged, or included in a `ModelUsageRecord`.
- No changes were made to this layer — it already satisfied every Phase 8 requirement on inspection.

## 8. Token Usage and Cost

Not applicable for this run: 0 calls were made, so total input/output/cost are all 0. The accounting fields (`ModelUsageRecord.input_tokens`, `output_tokens`, `total_tokens`, `estimated_cost_usd`) are already wired to the real Gemini SDK response's `usage_metadata` when a call does occur (see `image_extractor.py`), and are `None` (not fabricated zeros) when no call was attempted.

---

## 9. Simulation Counts

Measured directly by wrapping `planning.simulate` during a full production run:

| Metric | Value |
|---|---|
| Total `simulate()` calls | 2,494 |
| Requests | 250 |
| Average calls per request | 9.976 |
| Maximum calls for one request | 23 |
| Minimum calls for one request | 5 |

This is a small, bounded candidate count — not combinatorial, not requiring reduction. No candidate was removed, no verification simulation was skipped, and the canonical simulator was not replaced or duplicated with a second formula; the only internal change to `simulate()` was the sort-once refactor (Section 4.1), proven equivalent per-call against a reference re-implementation.

---

## 10. Determinism Results

| Check | Result |
|---|---|
| Two full production runs, byte-for-byte `output.csv` | **Identical** |
| `financial_events.csv` row order shuffled (`random.Random(1337).shuffle`), full pipeline re-run | **Identical recommendations for all 250 requests** |
| Stable ordering | `sorted_request_ids` explicitly sorts by numeric suffix; `simulator.simulate`'s new sort-once path uses the same total-order `ordering_key` as before; no code depends on dict/set iteration order for output content |
| Concurrency | None introduced — Section 3 of the Phase 8 brief was satisfied by *not* adding parallelism, since no measured bottleneck justified the added determinism risk |

Both checks are now permanent regression tests (`test_repeated_full_runs_are_byte_identical`, `test_shuffled_financial_events_produce_identical_recommendations`) rather than one-off manual verifications.

---

## 11. Production-Output Comparison

`output.csv` is **byte-identical** before and after every change made in this phase (verified after the `simulator.py` fix and again after the `output_writer.py` fix, via `cmp`/`diff`). No production row changed. Per Section 12/13 of the Phase 8 brief, this was the required outcome — identical output at lower/equal cost — and any output change would have been treated as a correctness regression rather than accepted.

---

## 12. Test Results

| | Before Phase 8 | After Phase 8 |
|---|---|---|
| Test count (pytest) | 653 | **661** |
| New tests added | — | 8 (`code/tests/test_performance.py`) |
| Result | 653 passed | 661 passed, 0 failed |
| Suite wall time | ~15.3s | ~23.9s (the new suite includes two full extra pipeline runs plus the shuffle test's third full run; each individual new test is fast in isolation) |

New tests: repeated-run byte identity, clean rerun over an existing/stale `output.csv`, shuffled-input recommendation identity, zero-model-calls-without-a-key, bounded simulation counts, the `simulate()` sort-once equivalence proof, and two `output_writer.py` atomicity tests (failure leaves no partial/temp file; happy path writes correctly with correct permissions).

---

## 13. Recommendation-Distribution Comparison

Unchanged — expected, since `output.csv` is byte-identical before and after every Phase 8 change (Section 11). No recommendation, status, plan, or explanation for any of the 250 production rows was altered.

---

## 14. Remaining Performance Risks

- **`ingestion._event_from_row` / `load_dataset` (~0.55s, ~24% of total runtime)** is the single largest remaining cost and was deliberately left untouched: it is one-time, per-run parsing work whose cost is dominated by the strict, fail-clearly validation Phase 2 established as a correctness requirement (every field individually type/enum/date-checked, with row-level error context). Speeding this up would mean weakening that validation or restructuring the parser purely for speed with no measured production pain point (250 requests / 23,054 events load in ~0.55s) — out of scope per "do not optimize unless measurement demonstrates a meaningful cost," since 0.55s is not a meaningful cost at this data size.
- **`planning.generate_candidates` / `evaluate_candidate`** remain a moderate share of runtime (candidate generation + simulation together are the largest single component). The measured candidate counts (Section 9) are small and bounded, so this is noted as a place future data-scale growth should be re-profiled, not a current risk.
- **`image_extractor.py`'s exception handling around the external SDK call** is intentionally broad (`except Exception`) because network/SDK failures are heterogeneous; it remains fully observable (every failure is recorded with a truncated error message via `UsageTracker`) and was reviewed rather than narrowed, since narrowing it further would not change behavior and risks missing a real SDK failure mode. A theoretical residual risk (not observed, not demonstrated) is that an SDK's own exception text could echo back part of a malformed request rather than the key itself — the key is never interpolated into any log or error path in this codebase.
- No O(n²) or worse behavior was found anywhere in the pipeline at up to 60× the real per-user data scale (Section 6); this should be re-verified if the dataset's per-user event count grows by orders of magnitude rather than assumed to hold indefinitely.

---

## 15. Explicit Statement

**No sample-specific optimization, forecast-statistic change, or business-rule adjustment was introduced in Phase 8.** Every change in this phase is a measured performance or production-safety fix (Sections 3–4) with proven behavioral equivalence (Section 4.1) or an explicitly reviewed-and-left-alone finding (Sections 7, 14). `output.csv` is byte-identical to the pre-Phase-8 version for all 250 production requests.
