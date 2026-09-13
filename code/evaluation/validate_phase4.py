#!/usr/bin/env python3
"""Phase 4 production validation: financial-state reconstruction and 90-day forecasting."""
from __future__ import annotations

import os
import statistics
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from buyorwait.bundle import build_request_bundle

from buyorwait.evidence_pipeline import build_evidence_bundle
from buyorwait.financial_state import ExclusionReason
from buyorwait.forecast import build_financial_state
from buyorwait.forecast_config import ForecastConfig
from buyorwait.ingestion import load_dataset, load_sample_request_user_ids
from buyorwait.invariants import check_forecast_invariants
from buyorwait.usage import UsageTracker

DATASET_DIR = Path(__file__).parent.parent.parent / "dataset"


def summarize(values):
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "min": ordered[0],
        "median": ordered[len(ordered) // 2],
        "max": ordered[-1],
        "mean": statistics.mean(float(v) for v in ordered),
    }


def main() -> int:
    config = ForecastConfig()
    dataset = load_dataset(DATASET_DIR)
    sample_users = load_sample_request_user_ids(DATASET_DIR)
    tracker = UsageTracker()

    errors: list[tuple[str, str]] = []
    kind_counts: Counter[str] = Counter()
    certainty_counts: Counter[str] = Counter()
    class_counts: Counter[str] = Counter()
    strength_counts: Counter[str] = Counter()
    exclusion_counts: Counter[str] = Counter()
    unresolved_notes: Counter[str] = Counter()

    event_counts: list[int] = []
    start_balances: list[Decimal] = []
    reserved_totals: list[Decimal] = []
    reserved_requests = 0
    unrealized_requests = 0
    income_totals: list[Decimal] = []
    essential_totals: list[Decimal] = []
    flexible_totals: list[Decimal] = []
    minimum_balances: list[Decimal] = []
    margins: list[Decimal] = []
    deficit_requests: list[str] = []
    unresolved_requests: list[str] = []
    missing_fx_requests: list[str] = []
    fingerprints: dict[str, tuple] = {}

    request_ids = sorted(dataset.requests, key=lambda r: int(r.split("_")[1]))
    for request_id in request_ids:
        try:
            bundle = build_request_bundle(dataset, request_id, recurrence_config=config.recurrence)
            assert bundle.profile.user_id not in sample_users
            evidence = build_evidence_bundle(bundle, tracker)
            forecast = build_financial_state(bundle, evidence, config)
            check_forecast_invariants(forecast)
        except Exception as exc:  # noqa: BLE001
            errors.append((request_id, repr(exc)))
            continue

        for candidate in bundle.recurrence_candidates:
            strength_counts[candidate.strength.value] += 1

        event_counts.append(len(forecast.events))
        start_balances.append(forecast.starting_state.available_cash)
        reserved_totals.append(forecast.starting_state.reserved_total)
        if forecast.starting_state.reserved_total:
            reserved_requests += 1
        if forecast.starting_state.unrealized_investment_value:
            unrealized_requests += 1
        income_totals.append(forecast.cumulative_future_income)
        essential_totals.append(forecast.cumulative_future_essential_expense)
        flexible_totals.append(forecast.cumulative_future_flexible_expense)
        minimum_balances.append(forecast.minimum_projected_balance)
        margins.append(forecast.safety_margin)

        if forecast.breaches_minimum:
            deficit_requests.append(request_id)
        if forecast.unresolved_obligations:
            unresolved_requests.append(request_id)
        for obligation in forecast.unresolved_obligations:
            unresolved_notes[obligation.note] += 1
        if any(item.reason is ExclusionReason.MISSING_EXCHANGE_RATE for item in forecast.excluded):
            missing_fx_requests.append(request_id)

        for event in forecast.events:
            kind_counts[event.kind.value] += 1
            certainty_counts[event.certainty.value] += 1
            class_counts[event.spending_class.value] += 1
        for item in forecast.excluded:
            exclusion_counts[item.reason.value] += 1

        fingerprints[request_id] = (
            forecast.starting_state.available_cash,
            forecast.minimum_projected_balance,
            forecast.minimum_projected_balance_date,
            forecast.ending_balance,
            tuple((e.event_id, e.when, e.amount_home) for e in forecast.events),
        )

    repeat_mismatches = []
    for request_id in request_ids:
        if request_id not in fingerprints:
            continue
        bundle = build_request_bundle(dataset, request_id, recurrence_config=config.recurrence)
        evidence = build_evidence_bundle(bundle, tracker)
        forecast = build_financial_state(bundle, evidence, config)
        repeat = (
            forecast.starting_state.available_cash,
            forecast.minimum_projected_balance,
            forecast.minimum_projected_balance_date,
            forecast.ending_balance,
            tuple((e.event_id, e.when, e.amount_home) for e in forecast.events),
        )
        if repeat != fingerprints[request_id]:
            repeat_mismatches.append(request_id)

    print(f"requests processed: {len(fingerprints)} / {len(request_ids)}")
    print(f"errors: {len(errors)} {errors[:5]}")
    print(f"horizon days: {config.horizon_days}")
    print(f"projected events per request: {summarize(event_counts)}")
    print(f"starting available cash: {summarize(start_balances)}")
    print(f"requests with reservations: {reserved_requests}")
    print(f"reserved totals: {summarize([r for r in reserved_totals if r])}")
    print(f"requests with unrealized investment value: {unrealized_requests}")
    print(f"cumulative future income: {summarize(income_totals)}")
    print(f"cumulative essential expense: {summarize(essential_totals)}")
    print(f"cumulative flexible expense: {summarize(flexible_totals)}")
    print(f"minimum projected balance: {summarize(minimum_balances)}")
    print(f"safety margin: {summarize(margins)}")
    print(f"requests breaching minimum balance: {len(deficit_requests)} {deficit_requests[:10]}")
    print(f"requests with unresolved obligations: {len(unresolved_requests)} {unresolved_requests[:10]}")
    print(f"unresolved obligation notes: {dict(unresolved_notes)}")
    print(f"requests with missing FX: {len(missing_fx_requests)} {missing_fx_requests[:10]}")
    print(f"event kinds: {dict(kind_counts)}")
    print(f"event certainty: {dict(certainty_counts)}")
    print(f"event spending class: {dict(class_counts)}")
    print(f"recurrence strengths (per request bundle): {dict(strength_counts)}")
    print(f"exclusion reasons: {dict(exclusion_counts)}")
    print(f"deterministic repeatability mismatches: {len(repeat_mismatches)} {repeat_mismatches[:5]}")
    return 1 if errors or repeat_mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
