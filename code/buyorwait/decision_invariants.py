from __future__ import annotations

from decimal import Decimal

from .decision import (
    AffordabilityStatus,
    CandidateKind,
    ChangeAction,
    PurchaseSpec,
    Recommendation,
    RecommendedMethod,
)
from .financial_state import FinancialStateForecast
from .planning import CENT, simulate_plan


class DecisionInvariantViolation(AssertionError):
    pass


def check_recommendation_invariants(
    recommendation: Recommendation,
    forecast: FinancialStateForecast,
    spec: PurchaseSpec,
) -> None:
    request_id = recommendation.request_id
    selected = next(
        (
            evaluation
            for evaluation in recommendation.candidates
            if evaluation.candidate.candidate_id == recommendation.selected_candidate_id
        ),
        None,
    )

    if recommendation.recommended_payment_method is RecommendedMethod.NOT_RECOMMENDED:
        if recommendation.payment_plan:
            raise DecisionInvariantViolation(f"{request_id}: not_recommended must carry no payment plan")
    elif selected is None:
        raise DecisionInvariantViolation(f"{request_id}: a recommended plan must reference a candidate")

    if selected is not None:
        candidate = selected.candidate
        if not selected.gates.method_eligibility:
            raise DecisionInvariantViolation(f"{request_id}: selected plan is not an eligible payment method")
        if not selected.gates.user_preference:
            raise DecisionInvariantViolation(f"{request_id}: selected plan is not an accepted payment method")
        if selected.breaches_minimum:
            raise DecisionInvariantViolation(f"{request_id}: selected plan breaches the minimum balance")
        if selected.rejection_reasons:
            raise DecisionInvariantViolation(f"{request_id}: selected plan carries rejection reasons")

        outcome = simulate_plan(forecast, spec, candidate.payments, candidate.spending_changes)
        if outcome.breaches_minimum:
            raise DecisionInvariantViolation(f"{request_id}: re-simulated selected plan breaches the minimum balance")
        if outcome.minimum_projected_balance != selected.minimum_projected_balance:
            raise DecisionInvariantViolation(f"{request_id}: selected plan is not reproducible")

        if candidate.kind is CandidateKind.PARTIAL_PAYMENT:
            if len(candidate.payments) != 2:
                raise DecisionInvariantViolation(f"{request_id}: partial payment must have exactly two payments")
            first, second = candidate.payments
            if spec.requested_amount_home is None or first[1] + second[1] != spec.requested_amount_home:
                raise DecisionInvariantViolation(f"{request_id}: partial payments must sum to the requested amount")
            if second[1] != spec.requested_amount_home - first[1]:
                raise DecisionInvariantViolation(f"{request_id}: remaining payment must be derived by subtraction")

        if candidate.kind is CandidateKind.INSTALLMENTS:
            expected_total = candidate.total_paid
            if expected_total < (spec.requested_amount_home or Decimal("0")):
                raise DecisionInvariantViolation(f"{request_id}: installment total is below the requested amount")
            if candidate.completion_date is None or candidate.completion_date > spec.desired_completion_date:
                raise DecisionInvariantViolation(f"{request_id}: installment plan completes after the deadline")

        for change in candidate.spending_changes:
            if change.action is ChangeAction.REDUCE_TO:
                if change.minimum_allowed_amount is None or change.new_amount < change.minimum_allowed_amount:
                    raise DecisionInvariantViolation(
                        f"{request_id}: reduction below the allowed minimum for {change.event_id}"
                    )
            if change.new_amount > change.current_amount:
                raise DecisionInvariantViolation(f"{request_id}: a spending change may not increase spending")
        if len(candidate.spending_changes) > 3:
            raise DecisionInvariantViolation(f"{request_id}: more than three spending changes selected")
        change_ids = [c.event_id for c in candidate.spending_changes]
        if len(change_ids) != len(set(change_ids)):
            raise DecisionInvariantViolation(f"{request_id}: the same event is changed twice")

    amount = recommendation.amount_safe_to_pay
    if amount < 0:
        raise DecisionInvariantViolation(f"{request_id}: amount_safe_to_pay is negative")
    if spec.requested_amount_home is not None and amount > spec.requested_amount_home:
        raise DecisionInvariantViolation(f"{request_id}: amount_safe_to_pay exceeds the requested amount")

    if amount > 0:
        safe = simulate_plan(forecast, spec, ((spec.request_date, amount),))
        if safe.breaches_minimum:
            raise DecisionInvariantViolation(f"{request_id}: amount_safe_to_pay is not actually safe")
    if spec.requested_amount_home is not None and amount < spec.requested_amount_home:
        over = simulate_plan(forecast, spec, ((spec.request_date, amount + CENT),))
        if not over.breaches_minimum:
            raise DecisionInvariantViolation(
                f"{request_id}: amount_safe_to_pay plus one cent is still safe, so the boundary is wrong"
            )

    earliest = recommendation.earliest_date_for_full_payment
    if earliest is not None and spec.requested_amount_home is not None:
        if not forecast.window.contains(earliest):
            raise DecisionInvariantViolation(f"{request_id}: earliest full-payment date is outside the window")
        outcome = simulate_plan(forecast, spec, ((earliest, spec.requested_amount_home),))
        if outcome.breaches_minimum:
            raise DecisionInvariantViolation(f"{request_id}: earliest full-payment date is not actually safe")

    if recommendation.affordability_status is AffordabilityStatus.AFFORDABLE_NOW:
        if earliest != spec.request_date:
            raise DecisionInvariantViolation(
                f"{request_id}: affordable_now requires earliest_date_for_full_payment == request_date"
            )
