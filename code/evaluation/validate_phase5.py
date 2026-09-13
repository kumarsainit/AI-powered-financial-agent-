#!/usr/bin/env python3
"""Phase 5 production validation: affordability decisions and payment-plan optimization."""
from __future__ import annotations

import os
import statistics
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from buyorwait.bundle import build_request_bundle
from buyorwait.decision import CandidateKind
from buyorwait.decision_invariants import check_recommendation_invariants
from buyorwait.evidence_pipeline import build_evidence_bundle
from buyorwait.forecast import build_financial_state
from buyorwait.ingestion import load_dataset, load_sample_request_user_ids
from buyorwait.planning import build_recommendation, normalize_request
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
        "mean": round(statistics.mean(float(v) for v in ordered), 2),
    }


def main() -> int:
    dataset = load_dataset(DATASET_DIR)
    sample_users = load_sample_request_user_ids(DATASET_DIR)
    tracker = UsageTracker()

    statuses: Counter[str] = Counter()
    methods: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    rejections: Counter[str] = Counter()
    blocking: Counter[str] = Counter()
    change_actions: Counter[str] = Counter()

    errors: list[tuple[str, str]] = []
    safe_amounts: list[Decimal] = []
    safe_fraction: list[float] = []
    margins: list[Decimal] = []
    minimums: list[Decimal] = []
    candidate_counts: list[int] = []
    earliest_offsets: list[int] = []
    no_earliest = 0
    changes_used = 0
    change_counts: Counter[int] = Counter()
    installment_terms: list[int] = []
    partial_used = 0
    unresolved_flagged = 0
    fingerprints: dict[str, tuple] = {}

    request_ids = sorted(dataset.requests, key=lambda r: int(r.split("_")[1]))
    for request_id in request_ids:
        try:
            bundle = build_request_bundle(dataset, request_id)
            assert bundle.profile.user_id not in sample_users
            forecast = build_financial_state(bundle, build_evidence_bundle(bundle, tracker))
            spec = normalize_request(bundle, forecast)
            recommendation = build_recommendation(bundle, forecast, spec=spec)
            check_recommendation_invariants(recommendation, forecast, spec)
        except Exception as exc:  # noqa: BLE001
            errors.append((request_id, repr(exc)))
            continue

        statuses[recommendation.affordability_status.value] += 1
        methods[recommendation.recommended_payment_method.value] += 1
        candidate_counts.append(len(recommendation.candidates))
        safe_amounts.append(recommendation.amount_safe_to_pay)
        if spec.requested_amount_home:
            safe_fraction.append(float(recommendation.amount_safe_to_pay / spec.requested_amount_home))
        margins.append(recommendation.safety_margin)
        minimums.append(recommendation.minimum_projected_balance)

        if recommendation.earliest_date_for_full_payment is None:
            no_earliest += 1
        else:
            earliest_offsets.append(
                (recommendation.earliest_date_for_full_payment - spec.request_date).days
            )

        if recommendation.spending_changes:
            changes_used += 1
        change_counts[len(recommendation.spending_changes)] += 1
        for change in recommendation.spending_changes:
            change_actions[change.action.value] += 1

        for evaluation in recommendation.candidates:
            kinds[evaluation.candidate.kind.value] += 1
            for reason in evaluation.rejection_reasons:
                rejections[reason.value] += 1
        for reason in recommendation.blocking_reasons:
            blocking[reason.value] += 1
        if recommendation.explanation_facts.blocking_obligations:
            unresolved_flagged += 1

        selected = next(
            (
                e
                for e in recommendation.candidates
                if e.candidate.candidate_id == recommendation.selected_candidate_id
            ),
            None,
        )
        if selected is not None and selected.candidate.kind is CandidateKind.INSTALLMENTS:
            installment_terms.append(len(selected.candidate.payments))
        if selected is not None and selected.candidate.kind is CandidateKind.PARTIAL_PAYMENT:
            partial_used += 1

        fingerprints[request_id] = (
            recommendation.amount_safe_to_pay,
            recommendation.affordability_status.value,
            recommendation.recommended_payment_method.value,
            recommendation.selected_candidate_id,
            recommendation.payment_plan,
            recommendation.earliest_date_for_full_payment,
            tuple((c.action.value, c.event_id, c.new_amount) for c in recommendation.spending_changes),
        )

    mismatches = []
    for request_id in request_ids:
        if request_id not in fingerprints:
            continue
        bundle = build_request_bundle(dataset, request_id)
        forecast = build_financial_state(bundle, build_evidence_bundle(bundle, tracker))
        spec = normalize_request(bundle, forecast)
        recommendation = build_recommendation(bundle, forecast, spec=spec)
        repeat = (
            recommendation.amount_safe_to_pay,
            recommendation.affordability_status.value,
            recommendation.recommended_payment_method.value,
            recommendation.selected_candidate_id,
            recommendation.payment_plan,
            recommendation.earliest_date_for_full_payment,
            tuple((c.action.value, c.event_id, c.new_amount) for c in recommendation.spending_changes),
        )
        if repeat != fingerprints[request_id]:
            mismatches.append(request_id)

    print(f"requests processed: {len(fingerprints)} / {len(request_ids)}")
    print(f"errors: {len(errors)} {errors[:5]}")
    print(f"invariant violations: {len([e for e in errors if 'Invariant' in e[1]])}")
    print(f"affordability status: {dict(statuses)}")
    print(f"recommended method: {dict(methods)}")
    print(f"candidate kinds generated: {dict(kinds)}")
    print(f"candidates per request: {summarize(candidate_counts)}")
    print(f"candidate rejection reasons: {dict(rejections)}")
    print(f"recommendation blocking reasons: {dict(blocking)}")
    print(f"amount_safe_to_pay: {summarize(safe_amounts)}")
    print(f"amount_safe_to_pay as a fraction of the requested amount: {summarize(safe_fraction)}")
    print(f"requests where the full amount is safe immediately: {sum(1 for f in safe_fraction if f >= 1.0)}")
    print(f"requests with amount_safe_to_pay == 0: {sum(1 for a in safe_amounts if a == 0)}")
    print(f"earliest safe full-payment offset in days: {summarize(earliest_offsets)}")
    print(f"requests with no safe full-payment date in the horizon: {no_earliest}")
    print(f"requests using spending changes: {changes_used} (counts {dict(change_counts)})")
    print(f"spending change actions: {dict(change_actions)}")
    print(f"selected installment terms: {summarize(installment_terms)}")
    print(f"partial payments selected: {partial_used}")
    print(f"requests flagged with unresolved obligations: {unresolved_flagged}")
    print(f"minimum projected balance under the selected plan: {summarize(minimums)}")
    print(f"safety margin under the selected plan: {summarize(margins)}")
    print(f"deterministic repeatability mismatches: {len(mismatches)} {mismatches[:5]}")
    return 1 if errors or mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
